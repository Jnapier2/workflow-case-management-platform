"""Runtime stability and bounded-performance regression tests.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
import os
from pathlib import Path

import pytest
from sqlalchemy import select

from app.database_maintenance import SCHEMA_VERSION
from app.models import Activity, CaseRecord
from app.runtime_lock import InstanceAlreadyRunning, ProjectInstanceLock
from app.services.runtime_tasks import RuntimeTasks


def _payload(index: int = 1) -> dict:
    return {
        "workflow_key": "vendor_onboarding",
        "title": f"Stable intake {index}",
        "description": "Runtime stability regression case.",
        "requester_name": "Stability Tester",
        "requester_email": f"stable{index}@example.com",
        "priority": "Medium",
        "fields": {
            "vendor_name": f"Stable Vendor {index}",
            "service_category": "Software",
            "annual_spend": 2000 + index,
            "handles_personal_data": False,
            "country": "United States",
            "business_owner": "Operations",
            "requested_start_date": (date.today() + timedelta(days=14)).isoformat(),
            "risk_notes": "Regression test.",
        },
    }


def test_readiness_timing_and_api_pagination(client):
    ready = client.get("/api/v1/ready")
    assert ready.status_code == 200, ready.text
    body = ready.json()
    assert body["status"] == "ready"
    assert body["database"] == "ready"
    assert body["schema_version"] == SCHEMA_VERSION
    assert float(ready.headers["X-Process-Time-Ms"]) >= 0

    page = client.get("/api/v1/cases?limit=3&offset=2")
    assert page.status_code == 200
    assert len(page.json()) == 3
    assert int(page.headers["X-Total-Count"]) >= 9
    assert page.headers["X-Limit"] == "3"
    assert page.headers["X-Offset"] == "2"


def test_case_reference_is_derived_from_unique_database_identity(client):
    created = []
    for index in range(1, 5):
        response = client.post("/api/v1/cases", json=_payload(index))
        assert response.status_code == 201, response.text
        created.append(response.json()["reference"])

    assert len(set(created)) == len(created)
    with client.app.state.SessionLocal() as db:
        rows = list(db.scalars(select(CaseRecord).where(CaseRecord.reference.in_(created))))
        assert len(rows) == len(created)
        for case in rows:
            assert case.reference == f"VEN-{case.id:04d}"




def test_concurrent_intake_preserves_unique_references(client):
    def submit(index: int) -> tuple[int, str]:
        response = client.post("/api/v1/cases", json=_payload(100 + index))
        reference = response.json().get("reference", "") if response.headers.get("content-type", "").startswith("application/json") else ""
        return response.status_code, reference

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(submit, range(16)))

    assert all(status == 201 for status, _ in results), results
    references = [reference for _, reference in results]
    assert len(references) == 16
    assert len(set(references)) == 16

def test_sla_scheduler_run_is_independent_and_idempotent(client):
    with client.app.state.SessionLocal() as db:
        case = db.scalar(
            select(CaseRecord)
            .where(CaseRecord.completed_at.is_(None))
            .order_by(CaseRecord.id.asc())
        )
        assert case is not None
        case.stage_due_at = case.created_at - timedelta(minutes=1)
        case.overall_due_at = case.created_at + timedelta(days=10)
        case.escalation_level = 0
        reference = case.reference
        case_id = case.id
        db.commit()

    tasks = RuntimeTasks(client.app.state.SessionLocal, 5.0)
    tasks._run_once()
    assert tasks.failure_count == 0
    assert tasks.last_change_count == 1

    tasks._run_once()
    assert tasks.failure_count == 0
    assert tasks.last_change_count == 0

    with client.app.state.SessionLocal() as db:
        case = db.get(CaseRecord, case_id)
        assert case is not None and case.reference == reference
        assert case.escalation_level == 1
        events = list(
            db.scalars(
                select(Activity).where(
                    Activity.case_id == case_id,
                    Activity.event_type == "sla_escalated",
                )
            )
        )
        assert len(events) == 1


def test_same_pc_instance_lock_rejects_live_duplicate_and_recovers(tmp_path: Path):
    path = tmp_path / "state" / "server_instance.lock"
    first = ProjectInstanceLock(path, build_id="TEST-A")
    first.acquire()
    try:
        second = ProjectInstanceLock(path, build_id="TEST-B")
        with pytest.raises(InstanceAlreadyRunning):
            second.acquire()
        assert str(os.getpid()) in path.read_text(encoding="utf-8")
    finally:
        first.release()

    third = ProjectInstanceLock(path, build_id="TEST-C")
    third.acquire()
    assert path.is_file()
    third.release()
    assert not path.exists()


def _load_run_server_module():
    import importlib.util
    script = Path(__file__).resolve().parents[1] / "scripts" / "run_server.py"
    spec = importlib.util.spec_from_file_location("run_server_runtime_stability_test", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_run_server_selects_next_free_local_port_without_touching_holder():
    import socket

    module = _load_run_server_module()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as holder:
        holder.bind(("127.0.0.1", 0))
        holder.listen(1)
        occupied = holder.getsockname()[1]
        selected = module._select_port("127.0.0.1", occupied, attempts=3)
        assert selected != occupied
        assert occupied < selected <= occupied + 2
        # The original listener is still ours and remains active.
        assert holder.getsockname()[1] == occupied


def test_run_server_preserves_nonzero_system_exit_code():
    module = _load_run_server_module()
    assert module._system_exit_code(SystemExit(0)) == 0
    assert module._system_exit_code(SystemExit(None)) == 0
    assert module._system_exit_code(SystemExit(7)) == 7
    assert module._system_exit_code(SystemExit("bad-bind")) == 1


def test_run_server_workflow_port_override_validation(monkeypatch):
    module = _load_run_server_module()
    monkeypatch.delenv("WORKFLOW_PORT", raising=False)
    assert module._preferred_port(8010) == 8010
    monkeypatch.setenv("WORKFLOW_PORT", "8025")
    assert module._preferred_port(8010) == 8025
    monkeypatch.setenv("WORKFLOW_PORT", "70000")
    with pytest.raises(RuntimeError):
        module._preferred_port(8010)
    monkeypatch.setenv("WORKFLOW_PORT", "not-a-port")
    with pytest.raises(RuntimeError):
        module._preferred_port(8010)
