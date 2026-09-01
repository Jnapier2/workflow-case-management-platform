"""Resilience, recovery, outbox, operations, and indexed-search regression tests.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
import json
import sqlite3
import subprocess
import sys

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.backup_restore import run_recovery_doctor
from app.config import Settings
from app.database import create_sqlite_engine
from app.database_maintenance import SCHEMA_VERSION, apply_schema_migrations
from app.main import create_app
from app.models import Base, CaseRecord, Notification, OutboxJob
from app.services.outbox import process_outbox_batch


def _payload(title: str = "Zephyr Compliance Beacon") -> dict:
    return {
        "workflow_key": "vendor_onboarding",
        "title": title,
        "description": "Resilience regression request.",
        "requester_name": "Foundation Tester",
        "requester_email": "foundation@example.com",
        "priority": "Medium",
        "fields": {
            "vendor_name": title,
            "service_category": "Software",
            "annual_spend": 7200,
            "handles_personal_data": False,
            "country": "United States",
            "business_owner": "Operations",
            "requested_start_date": (date.today() + timedelta(days=14)).isoformat(),
            "risk_notes": "Automated regression test.",
        },
    }


def test_notification_delivery_uses_persistent_outbox(client):
    response = client.post("/api/v1/cases", json=_payload("Durable Notification Vendor"))
    assert response.status_code == 201, response.text
    reference = response.json()["reference"]

    with client.app.state.SessionLocal() as db:
        case = db.scalar(select(CaseRecord).where(CaseRecord.reference == reference))
        assert case is not None
        notification = db.scalar(
            select(Notification)
            .where(Notification.case_id == case.id)
            .order_by(Notification.id.desc())
        )
        assert notification is not None
        assert notification.delivery_status == "Queued"
        job = db.scalar(
            select(OutboxJob).where(OutboxJob.dedupe_key == f"notification:{notification.id}")
        )
        assert job is not None and job.status == "Pending"
        notification_id = notification.id
        job_id = job.id

    result = process_outbox_batch(client.app.state.SessionLocal, limit=20)
    assert result["failed"] == 0
    assert result["processed"] >= 1
    with client.app.state.SessionLocal() as db:
        notification = db.get(Notification, notification_id)
        job = db.get(OutboxJob, job_id)
        assert notification is not None and notification.delivery_status == "Delivered"
        assert notification.delivered_at is not None
        assert job is not None and job.status == "Completed"
    assert reference.startswith("VEN-")


def test_indexed_search_and_operational_health(client):
    created = client.post("/api/v1/cases", json=_payload())
    assert created.status_code == 201

    search = client.get("/api/v1/cases", params={"q": "Zephyr Compliance", "limit": 10})
    assert search.status_code == 200, search.text
    assert any(item["title"] == "Zephyr Compliance Beacon" for item in search.json())

    operations = client.get("/api/v1/operations")
    assert operations.status_code == 200
    value = operations.json()
    assert value["database"]["schema_version"] == SCHEMA_VERSION
    assert value["database"]["search_mode"] in {"sqlite_fts5", "indexed_like"}
    assert value["outbox"]["Pending"] >= 1
    assert "requests" in value

    page = client.get("/operations")
    assert page.status_code == 200
    assert "Operational health" in page.text
    assert "durable work" in page.text


def test_recovery_doctor_creates_verified_backup_and_restore_receipt(tmp_path: Path):
    settings = Settings(
        root=tmp_path,
        database_path=tmp_path / "data" / "doctor.db",
        testing=True,
        seed_demo=True,
    )
    app = create_app(settings)
    with TestClient(app) as client:
        assert client.get("/api/v1/ready").status_code == 200

    receipt = run_recovery_doctor(settings)
    assert receipt["result"] == "PASS", receipt
    assert receipt["application_version"]
    assert receipt["build_id"]
    assert receipt["schema_version"] == SCHEMA_VERSION
    assert receipt["restore_copy_removed"] is True
    assert receipt["backup_sha256"] == receipt["restore_copy_sha256"]
    assert receipt["logical_summary"]["cases"]["count"] == 9
    assert receipt["attachment_evidence"]["problem_count"] == 0
    backup = tmp_path / receipt["backup_path"]
    assert backup.is_file()
    assert backup.with_suffix(backup.suffix + ".sha256.txt").is_file()
    assert (tmp_path / "state" / "recovery_doctor.json").is_file()


def test_recovery_doctor_treats_unsynchronized_schema_zero_as_recoverable_advisory(tmp_path: Path):
    """Regression for the Windows WCM-B003 Doctor failure captured on 2026-08-28."""
    database = tmp_path / "data" / "doctor_unsynchronized.db"
    database.parent.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(database)
    try:
        # Reproduce a structurally current SQLite database whose legacy user_version marker
        # has not yet been synchronized by normal application startup.
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA user_version = 0")
            connection.exec_driver_sql("DROP TABLE IF EXISTS platform_schema_state")
    finally:
        engine.dispose()

    settings = Settings(root=tmp_path, database_path=database, testing=True, seed_demo=False)
    receipt = run_recovery_doctor(settings)

    assert receipt["result"] == "PASS_WITH_ADVISORY", receipt
    assert receipt["schema_compatibility"] == "marker_unsynchronized"
    assert receipt["schema_markers"]["sqlite_user_version"] == 0
    assert receipt["schema_markers"]["current_structure_detected"] is True
    assert receipt["live_database_modified"] is False
    assert receipt["problems"] == []
    assert receipt["advisories"]
    with sqlite3.connect(str(database)) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='platform_schema_state'"
        ).fetchone()


def test_schema_one_database_upgrades_to_outbox_and_search(tmp_path: Path):
    database = tmp_path / "legacy_v1.db"
    with sqlite3.connect(str(database)) as connection:
        connection.executescript(
            """
            CREATE TABLE cases (
                id INTEGER PRIMARY KEY,
                reference VARCHAR(32) NOT NULL,
                workflow_id INTEGER NOT NULL,
                title VARCHAR(240) NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                requester_name VARCHAR(160) NOT NULL,
                requester_email VARCHAR(200) NOT NULL,
                status VARCHAR(80) NOT NULL,
                priority VARCHAR(20) NOT NULL,
                assignee_id INTEGER,
                completed_at TIMESTAMP,
                stage_due_at TIMESTAMP,
                overall_due_at TIMESTAMP,
                created_at TIMESTAMP NOT NULL,
                updated_at TIMESTAMP NOT NULL
            );
            CREATE TABLE approvals (id INTEGER PRIMARY KEY, case_id INTEGER NOT NULL, status VARCHAR(20) NOT NULL);
            CREATE TABLE notifications (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL,
                case_id INTEGER,
                kind VARCHAR(60) NOT NULL,
                message VARCHAR(400) NOT NULL,
                is_read BOOLEAN NOT NULL DEFAULT 0,
                created_at TIMESTAMP NOT NULL
            );
            PRAGMA user_version = 1;
            """
        )

    engine = create_sqlite_engine(database)
    try:
        assert apply_schema_migrations(engine) == SCHEMA_VERSION
        with engine.connect() as connection:
            columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(notifications)").all()}
            tables = {row[0] for row in connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table'").all()}
            version = connection.exec_driver_sql("PRAGMA user_version").scalar_one()
            ledger = connection.exec_driver_sql(
                "SELECT value FROM platform_schema_state WHERE key='schema_version'"
            ).scalar_one()
        assert {"delivery_status", "delivered_at"}.issubset(columns)
        assert "outbox_jobs" in tables
        assert int(version) == SCHEMA_VERSION
        assert int(ledger) == SCHEMA_VERSION
    finally:
        engine.dispose()


def test_postgresql_configuration_is_explicit_and_migration_script_is_noop_without_apply(tmp_path: Path):
    settings = Settings(
        root=tmp_path,
        database_url="postgresql+psycopg://portfolio_user:secret@example.invalid:5432/workflow",
    )
    assert settings.database_backend == "postgresql"
    assert settings.resolved_database_url.startswith("postgresql+psycopg://")

    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [sys.executable, str(root / "scripts" / "migrate_database.py")],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 2
    assert "No schema changes were made" in result.stdout


def test_sla_priority_is_not_starved_by_notification_backlog(client):
    from datetime import timedelta

    from app.services.common import utcnow
    from app.services.runtime_tasks import RuntimeTasks

    response = client.post("/api/v1/cases", json=_payload("Priority SLA Vendor"))
    assert response.status_code == 201
    reference = response.json()["reference"]

    with client.app.state.SessionLocal() as db:
        case = db.scalar(select(CaseRecord).where(CaseRecord.reference == reference))
        assert case is not None
        case.stage_due_at = utcnow() - timedelta(hours=2)
        case.overall_due_at = utcnow() + timedelta(days=1)
        # Add enough routine jobs to exceed one worker batch. The SLA job must still run first.
        for index in range(150):
            db.add(
                OutboxJob(
                    kind="notification_delivery",
                    payload_json='{"notification_id": 0}',
                    dedupe_key=f"priority-backlog:{index}",
                    status="Pending",
                    priority=50,
                    max_attempts=2,
                    available_at=utcnow(),
                )
            )
        db.commit()

    worker = RuntimeTasks(client.app.state.SessionLocal, sla_interval_seconds=60, job_poll_seconds=60)
    worker._run_once()
    assert worker.last_change_count == 1

    with client.app.state.SessionLocal() as db:
        case = db.scalar(select(CaseRecord).where(CaseRecord.reference == reference))
        assert case is not None
        assert case.escalation_level == 1
        pending_routine = db.scalar(
            select(OutboxJob).where(
                OutboxJob.dedupe_key.like("priority-backlog:%"),
                OutboxJob.status == "Pending",
            ).limit(1)
        )
        assert pending_routine is not None


def test_postgresql_outbox_uses_skip_locked_leasing():
    from sqlalchemy.dialects import postgresql

    from app.services.outbox import _due_job_statement

    compiled = str(_due_job_statement("postgresql").compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE SKIP LOCKED" in compiled.upper()


def test_operations_does_not_treat_stale_prior_doctor_failure_as_current_health_failure(client):
    state = client.app.state.settings.state_dir
    state.mkdir(parents=True, exist_ok=True)
    (state / "recovery_doctor.json").write_text(
        json.dumps(
            {
                "checked_at": "2026-08-28T17:52:09Z",
                "result": "FAIL",
                "problems": ["historical field failure"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    response = client.get("/api/v1/operations")
    assert response.status_code == 200
    value = response.json()
    assert value["recovery"]["latest_doctor_result"] == "FAIL"
    assert value["recovery"]["latest_doctor_current"] is False
    assert value["recovery"]["latest_doctor_evidence_status"] == "legacy_unattributed"
    assert "current recovery Doctor failed" not in value["issues"]

    page = client.get("/operations")
    assert page.status_code == 200
    assert "FAIL · stale" in page.text


def test_operations_surfaces_current_build_doctor_failure(client):
    from app.runtime_utils import iso_now
    from app.version import APP_VERSION, BUILD_ID

    state = client.app.state.settings.state_dir
    state.mkdir(parents=True, exist_ok=True)
    (state / "recovery_doctor.json").write_text(
        json.dumps(
            {
                "application_version": APP_VERSION,
                "build_id": BUILD_ID,
                "checked_at": iso_now(),
                "result": "FAIL",
                "problems": ["current synthetic failure"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    response = client.get("/api/v1/operations")
    assert response.status_code == 200
    value = response.json()
    assert value["status"] == "Attention"
    assert value["recovery"]["latest_doctor_current"] is True
    assert value["recovery"]["latest_doctor_evidence_status"] == "current"
    assert "current recovery Doctor failed" in value["issues"]
