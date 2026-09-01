"""Operational-health metrics for supportability and performance oversight.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import threading
import time
from typing import Any

from fastapi import Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import database_backend, safe_database_target
from app.evidence_status import classify_cached_evidence
from app.models import Attachment, IntegrationExecution, Notification, OutboxJob
from app.services.common import as_utc, iso, utcnow


class RequestMetrics:
    def __init__(self) -> None:
        self.started_monotonic = time.monotonic()
        self._lock = threading.Lock()
        self.requests = 0
        self.slow_requests = 0
        self.total_ms = 0.0
        self.max_ms = 0.0
        self.last_slow_path = ""
        self.last_slow_ms = 0.0

    def record(self, path: str, elapsed_ms: float, slow_threshold_ms: float) -> None:
        with self._lock:
            self.requests += 1
            self.total_ms += elapsed_ms
            self.max_ms = max(self.max_ms, elapsed_ms)
            if elapsed_ms >= slow_threshold_ms:
                self.slow_requests += 1
                self.last_slow_path = path[:200]
                self.last_slow_ms = elapsed_ms

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            requests = self.requests
            return {
                "uptime_seconds": round(max(0.0, time.monotonic() - self.started_monotonic), 1),
                "requests": requests,
                "slow_requests": self.slow_requests,
                "average_ms": round(self.total_ms / requests, 1) if requests else 0.0,
                "max_ms": round(self.max_ms, 1),
                "last_slow_path": self.last_slow_path or None,
                "last_slow_ms": round(self.last_slow_ms, 1) if self.last_slow_path else None,
            }


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size if path.is_file() else 0
    except OSError:
        return 0


def _latest_managed_backup(backups_dir: Path) -> dict[str, Any]:
    try:
        values = sorted(
            [path for path in backups_dir.glob("workflow_cases_*.db") if path.is_file()],
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        values = []
    if not values:
        return {"count": 0, "latest": None, "latest_bytes": 0}
    latest = values[0]
    return {"count": len(values), "latest": latest.name, "latest_bytes": _file_size(latest)}


def _doctor_receipt(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def build_operational_health(request: Request, db: Session) -> dict[str, Any]:
    settings = request.app.state.settings
    engine = request.app.state.engine
    backend = database_backend(engine)
    now = utcnow()
    counts = {
        status: int(
            db.scalar(select(func.count(OutboxJob.id)).where(OutboxJob.status == status)) or 0
        )
        for status in ("Pending", "Processing", "Failed", "Completed")
    }
    oldest_pending = db.scalar(
        select(func.min(OutboxJob.available_at)).where(OutboxJob.status == "Pending")
    )
    queued_notifications = int(
        db.scalar(select(func.count(Notification.id)).where(Notification.delivery_status == "Queued")) or 0
    )
    attachment_count, attachment_bytes = db.execute(
        select(func.count(Attachment.id), func.coalesce(func.sum(Attachment.size_bytes), 0))
    ).one()
    integration_counts = {
        status: int(db.scalar(select(func.count(IntegrationExecution.id)).where(IntegrationExecution.status == status)) or 0)
        for status in ("Queued", "Processing", "Completed", "Failed")
    }
    latest_integration_failure = db.scalar(
        select(IntegrationExecution).where(IntegrationExecution.status == "Failed").order_by(IntegrationExecution.created_at.desc()).limit(1)
    )

    tasks = getattr(request.app.state, "runtime_tasks", None)
    request_metrics: RequestMetrics = request.app.state.request_metrics
    backup = _latest_managed_backup(settings.backups_dir)
    doctor = _doctor_receipt(settings.state_dir / "recovery_doctor.json")
    doctor_evidence = classify_cached_evidence(doctor, now=now)

    database_storage: dict[str, Any] = {"database_bytes": None, "wal_bytes": None, "shm_bytes": None}
    if backend == "sqlite":
        database_storage = {
            "database_bytes": _file_size(settings.db_path),
            "wal_bytes": _file_size(Path(str(settings.db_path) + "-wal")),
            "shm_bytes": _file_size(Path(str(settings.db_path) + "-shm")),
        }

    issues: list[str] = []
    if counts["Failed"]:
        issues.append(f"{counts['Failed']} durable job(s) failed")
    if tasks is not None and not tasks.alive and not settings.testing:
        issues.append("durable worker is stopped")
    if queued_notifications > 25:
        issues.append(f"notification delivery backlog is {queued_notifications}")
    if doctor_evidence.get("current") and doctor and doctor.get("result") == "FAIL":
        issues.append("current recovery Doctor failed")
    if integration_counts["Failed"]:
        issues.append(f"{integration_counts['Failed']} integration execution(s) failed")

    return {
        "status": "Attention" if issues else "Healthy",
        "issues": issues,
        "database": {
            "backend": backend,
            "target": safe_database_target(engine),
            "schema_version": int(getattr(request.app.state, "schema_version", 0)),
            "health": getattr(request.app.state, "database_health", {}).get("result", "Unknown"),
            "search_mode": getattr(request.app.state, "search_mode", "unknown"),
            **database_storage,
        },
        "worker": {
            "state": "active" if tasks and tasks.alive else ("disabled" if tasks is None else "stopped"),
            "last_run": iso(tasks.last_run_utc) if tasks and tasks.last_run_utc else None,
            "failure_count": int(tasks.failure_count) if tasks else 0,
            "jobs_processed": int(tasks.jobs_processed) if tasks else 0,
            "last_business_changes": int(tasks.last_change_count) if tasks else 0,
        },
        "outbox": {
            **counts,
            "oldest_pending_at": iso(as_utc(oldest_pending)) if oldest_pending else None,
            "oldest_pending_age_seconds": round((now - as_utc(oldest_pending)).total_seconds(), 1)
            if oldest_pending
            else None,
            "queued_notifications": queued_notifications,
        },
        "integrations": {
            **integration_counts,
            "latest_failure": {
                "correlation_id": latest_integration_failure.correlation_id,
                "error_summary": latest_integration_failure.error_summary,
                "created_at": iso(latest_integration_failure.created_at),
            } if latest_integration_failure else None,
        },
        "storage": {
            "attachment_count": int(attachment_count or 0),
            "attachment_bytes": int(attachment_bytes or 0),
            "managed_backups": backup,
        },
        "recovery": {
            "latest_doctor_result": doctor.get("result") if doctor else None,
            "latest_doctor_checked_at": doctor.get("checked_at") if doctor else None,
            "latest_doctor_report": doctor.get("report_path") if doctor else None,
            "latest_doctor_current": bool(doctor_evidence.get("current")),
            "latest_doctor_evidence_status": doctor_evidence.get("status"),
            "latest_doctor_evidence_reason": doctor_evidence.get("reason"),
            "latest_doctor_source_version": doctor_evidence.get("source_version"),
            "latest_doctor_source_build_id": doctor_evidence.get("source_build_id"),
        },
        "requests": request_metrics.snapshot(),
        "generated_at": iso(now),
    }
