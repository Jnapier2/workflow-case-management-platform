"""Isolated soak and fault-injection qualification harness.

This script never targets the configured live database. It creates a disposable project-local
runtime under diagnostics/temp, exercises concurrency and recovery scenarios, writes a report,
and removes its test runtime unless --keep is supplied.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import json
from pathlib import Path
import shutil
import sqlite3
import statistics
import sys
import tempfile
import threading
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.backup_restore import run_recovery_doctor
from app.config import Settings
from app.database_maintenance import SCHEMA_VERSION, backup_before_migration
from app.main import create_app
from app.models import CaseRecord, Notification, OutboxJob, User
from app.runtime_lock import ProjectInstanceLock
from app.services.common import dumps, utcnow
from app.services.outbox import process_outbox_batch
from app.services.runtime_tasks import RuntimeTasks
from app.version import APP_VERSION, BUILD_ID


def _payload(index: int) -> dict:
    return {
        "workflow_key": "vendor_onboarding",
        "title": f"Soak Vendor {index}",
        "description": "Isolated resilience qualification request.",
        "requester_name": f"Soak Requester {index}",
        "requester_email": f"soak{index}@example.test",
        "priority": "Medium" if index % 5 else "High",
        "fields": {
            "vendor_name": f"Soak Vendor {index}",
            "service_category": "Software",
            "annual_spend": 5000 + index,
            "handles_personal_data": bool(index % 2),
            "country": "United States",
            "business_owner": "Operations",
            "requested_start_date": (date.today() + timedelta(days=14)).isoformat(),
            "risk_notes": "Synthetic qualification data only.",
        },
    }


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = max(0, min(len(ordered) - 1, int(round((len(ordered) - 1) * percentile))))
    return round(ordered[position], 2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=int, default=120)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    case_count = max(20, min(args.cases, 5000))
    workers = max(2, min(args.workers, 32))

    temp_parent = ROOT / "diagnostics" / "temp"
    temp_parent.mkdir(parents=True, exist_ok=True)
    runtime_root = Path(tempfile.mkdtemp(prefix="soak_fault_", dir=temp_parent))
    settings = Settings(
        root=runtime_root,
        database_path=runtime_root / "data" / "soak.db",
        testing=True,
        seed_demo=True,
        sla_poll_seconds=60,
        job_poll_seconds=0,
    )
    report: dict = {
        "schema": "workflow-soak-fault-qualification-1",
        "application_version": APP_VERSION,
        "build_id": BUILD_ID,
        "cases_requested": case_count,
        "workers": workers,
        "isolated_runtime": True,
        "live_database_touched": False,
        "checks": {},
    }
    latencies: list[float] = []
    references: list[str] = []

    try:
        app = create_app(settings)
        with TestClient(app) as client:
            def create_one(index: int) -> tuple[int, str, float]:
                started = time.perf_counter()
                response = client.post("/api/v1/cases", json=_payload(index))
                elapsed = (time.perf_counter() - started) * 1000.0
                reference = response.json().get("reference", "") if response.status_code == 201 else ""
                return response.status_code, reference, elapsed

            with ThreadPoolExecutor(max_workers=workers) as pool:
                results = list(pool.map(create_one, range(1, case_count + 1)))
            statuses = [status for status, _, _ in results]
            references = [reference for status, reference, _ in results if status == 201]
            latencies = [elapsed for _, _, elapsed in results]
            report["checks"]["concurrent_intake"] = {
                "result": "PASS" if all(status == 201 for status in statuses) and len(set(references)) == case_count else "FAIL",
                "created": len(references),
                "unique_references": len(set(references)),
                "p50_ms": _percentile(latencies, 0.50),
                "p95_ms": _percentile(latencies, 0.95),
                "p99_ms": _percentile(latencies, 0.99),
                "max_ms": round(max(latencies), 2) if latencies else 0.0,
            }

            transition_failures = 0
            for reference in references[: min(25, len(references))]:
                response = client.post(
                    f"/api/v1/cases/{reference}/transitions",
                    json={"target_stage": "validation", "note": "Soak transition."},
                )
                if response.status_code != 200:
                    transition_failures += 1
            report["checks"]["transitions"] = {
                "result": "PASS" if transition_failures == 0 else "FAIL",
                "attempted": min(25, len(references)),
                "failures": transition_failures,
            }

            search = client.get("/api/v1/cases", params={"q": f"Soak Vendor {max(1, case_count // 2)}", "limit": 10})
            report["checks"]["full_text_search"] = {
                "result": "PASS" if search.status_code == 200 and len(search.json()) >= 1 else "FAIL",
                "status_code": search.status_code,
                "matches": len(search.json()) if search.status_code == 200 else 0,
                "search_mode": getattr(app.state, "search_mode", "unknown"),
            }

            # External writer contention: application waits and succeeds rather than surfacing SQLITE_BUSY.
            raw = sqlite3.connect(str(settings.db_path), timeout=5.0, check_same_thread=False)
            raw.execute("BEGIN IMMEDIATE")
            holder: dict[str, object] = {}

            def blocked_create() -> None:
                holder["result"] = create_one(case_count + 1000)

            thread = threading.Thread(target=blocked_create, daemon=True)
            thread.start()
            time.sleep(0.25)
            raw.commit()
            raw.close()
            thread.join(timeout=10.0)
            contention_result = holder.get("result")
            contention_pass = bool(
                isinstance(contention_result, tuple)
                and contention_result[0] == 201
                and not thread.is_alive()
            )
            report["checks"]["sqlite_external_contention"] = {
                "result": "PASS" if contention_pass else "FAIL",
                "request_result": contention_result,
            }

            # Force one overdue case, then ensure the durable SLA job is idempotent within its bucket.
            with app.state.SessionLocal() as db:
                case = db.scalar(select(CaseRecord).where(CaseRecord.completed_at.is_(None)).order_by(CaseRecord.id.desc()))
                case.stage_due_at = utcnow() - timedelta(minutes=2)
                case.overall_due_at = utcnow() + timedelta(days=1)
                case.escalation_level = 0
                case_id = case.id
                db.commit()
            tasks = RuntimeTasks(app.state.SessionLocal, 60, 1)
            tasks._run_once()
            first_changes = tasks.last_change_count
            tasks._run_once()
            second_changes = tasks.last_change_count
            with app.state.SessionLocal() as db:
                escalated = db.get(CaseRecord, case_id)
                level = escalated.escalation_level if escalated else -1
            report["checks"]["durable_sla_idempotency"] = {
                "result": "PASS" if first_changes >= 1 and second_changes == 0 and level >= 1 else "FAIL",
                "first_changes": first_changes,
                "second_changes": second_changes,
                "escalation_level": level,
            }

            # Simulate an interrupted worker by leaving a stale Processing job behind.
            with app.state.SessionLocal() as db:
                user = db.scalar(select(User).order_by(User.id.asc()))
                notification = Notification(
                    user_id=user.id,
                    kind="fault_test",
                    message="Stale durable job recovery check.",
                    delivery_status="Queued",
                )
                db.add(notification)
                db.flush()
                job = OutboxJob(
                    kind="notification_delivery",
                    payload_json=dumps({"notification_id": notification.id}),
                    dedupe_key=f"fault-stale:{uuid4().hex}",
                    status="Processing",
                    attempts=1,
                    max_attempts=5,
                    available_at=utcnow() - timedelta(minutes=10),
                    locked_at=utcnow() - timedelta(minutes=10),
                )
                db.add(job)
                db.commit()
                notification_id = notification.id
                job_id = job.id
            recovered = process_outbox_batch(app.state.SessionLocal, limit=20)
            with app.state.SessionLocal() as db:
                notification = db.get(Notification, notification_id)
                job = db.get(OutboxJob, job_id)
                recovered_ok = bool(
                    notification
                    and notification.delivery_status == "Delivered"
                    and job
                    and job.status == "Completed"
                )
            report["checks"]["stale_job_recovery"] = {
                "result": "PASS" if recovered_ok else "FAIL",
                "processed": recovered["processed"],
                "failed": recovered["failed"],
            }

        # Server is stopped before recovery qualification.
        doctor = run_recovery_doctor(settings)
        report["checks"]["backup_restore_doctor"] = {
            "result": "PASS" if doctor.get("result") == "PASS" else "FAIL",
            "doctor_result": doctor.get("result"),
            "attachment_problems": doctor.get("attachment_evidence", {}).get("problem_count", 0),
        }

        lock_path = settings.state_dir / "server_instance.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(json.dumps({"pid": 999_999_999, "build_id": "STALE"}) + "\n", encoding="utf-8")
        lock = ProjectInstanceLock(lock_path, build_id="SOAK")
        lock.acquire()
        lock.release()
        report["checks"]["stale_server_lock_recovery"] = {
            "result": "PASS" if not lock_path.exists() else "FAIL"
        }

        # Backup-target fault injection must fail without changing the source database.
        bad_target = runtime_root / "not_a_directory"
        bad_target.write_text("fault injection\n", encoding="utf-8")
        fault_detected = False
        try:
            backup_before_migration(settings.db_path, bad_target, SCHEMA_VERSION - 1, SCHEMA_VERSION)
        except OSError:
            fault_detected = True
        report["checks"]["backup_target_fault"] = {
            "result": "PASS" if fault_detected else "FAIL",
            "safe_failure_detected": fault_detected,
        }

        report["result"] = (
            "PASS" if all(item.get("result") == "PASS" for item in report["checks"].values()) else "FAIL"
        )
        report["schema_version"] = SCHEMA_VERSION
        report["intake_latency_ms"] = {
            "median": round(statistics.median(latencies), 2) if latencies else 0.0,
            "p95": _percentile(latencies, 0.95),
            "p99": _percentile(latencies, 0.99),
        }
        report["completed_at"] = utcnow().isoformat().replace("+00:00", "Z")
    finally:
        reports = ROOT / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S_UTC", time.gmtime())
        output = reports / f"SOAK_FAULT_QUALIFICATION_{stamp}.json"
        output.write_text(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
        latest = ROOT / "state" / "soak_fault_latest.json"
        latest.parent.mkdir(parents=True, exist_ok=True)
        latest.write_text(json.dumps({**report, "report_path": str(output.relative_to(ROOT))}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if not args.keep:
            shutil.rmtree(runtime_root, ignore_errors=True)

    print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
    return 0 if report.get("result") == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
