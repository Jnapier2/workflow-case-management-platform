"""Restart-safe persistent job/outbox processing.

The outbox makes notification delivery and SLA scans durable across normal restarts and
unexpected termination. The current portfolio build delivers notifications in-app; future
email/webhook adapters can consume the same durable job contract without changing case writes.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

from datetime import timedelta
import re
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import Notification, OutboxJob
from app.services.common import as_utc, dumps, loads, utcnow
from app.services.coordination import database_write_lock


STALE_JOB_SECONDS = 120
DEFAULT_BATCH_SIZE = 50


def _safe_error(exc: Exception) -> str:
    text = str(exc).replace("\r", " ").replace("\n", " ")[:300]
    text = re.sub(r"(?i)(postgres(?:ql)?(?:\+\w+)?://[^:/@\s]+:)[^@\s]+@", r"\1***@", text)
    return f"{type(exc).__name__}: {text}"[:500]


def enqueue_job(
    db: Session,
    kind: str,
    payload: dict[str, Any] | None = None,
    *,
    dedupe_key: str | None = None,
    max_attempts: int = 5,
    priority: int = 50,
) -> OutboxJob:
    if dedupe_key:
        existing = db.scalar(select(OutboxJob).where(OutboxJob.dedupe_key == dedupe_key))
        if existing is not None:
            return existing
    job = OutboxJob(
        kind=kind[:80],
        payload_json=dumps(payload or {}),
        dedupe_key=dedupe_key[:180] if dedupe_key else None,
        status="Pending",
        priority=max(0, min(int(priority), 1000)),
        max_attempts=max(1, int(max_attempts)),
        available_at=utcnow(),
    )
    db.add(job)
    db.flush()
    return job


def enqueue_notification_delivery(db: Session, notification: Notification) -> OutboxJob:
    return enqueue_job(
        db,
        "notification_delivery",
        {"notification_id": notification.id},
        dedupe_key=f"notification:{notification.id}",
        priority=50,
    )


def enqueue_sla_scan(db: Session, interval_seconds: float, *, force: bool = False) -> OutboxJob:
    now = utcnow()
    bucket_seconds = max(5, int(interval_seconds))
    bucket = int(now.timestamp()) if force else int(now.timestamp() // bucket_seconds)
    return enqueue_job(
        db,
        "sla_scan",
        {"requested_at": now.isoformat(), "bucket_seconds": bucket_seconds},
        dedupe_key=f"sla_scan:{bucket}",
        max_attempts=8,
        priority=10,
    )


def _due_job_statement(backend: str):  # type: ignore[no-untyped-def]
    now = utcnow()
    stale_before = now - timedelta(seconds=STALE_JOB_SECONDS)
    statement = (
        select(OutboxJob)
        .where(
            or_(
                and_(OutboxJob.status == "Pending", OutboxJob.available_at <= now),
                and_(
                    OutboxJob.status == "Processing",
                    OutboxJob.locked_at.is_not(None),
                    OutboxJob.locked_at < stale_before,
                ),
            )
        )
        .order_by(OutboxJob.priority.asc(), OutboxJob.available_at.asc(), OutboxJob.id.asc())
        .limit(1)
    )
    if backend == "postgresql":
        statement = statement.with_for_update(skip_locked=True)
    return statement


def _next_due_job(db: Session) -> OutboxJob | None:
    bind = db.get_bind()
    backend = "postgresql" if bind.dialect.name.lower().startswith("postgres") else bind.dialect.name.lower()
    return db.scalar(_due_job_statement(backend))


def _dispatch(db: Session, job: OutboxJob) -> int:
    payload = loads(job.payload_json, {})
    if job.kind == "notification_delivery":
        notification_id = int(payload.get("notification_id", 0) or 0)
        notification = db.get(Notification, notification_id)
        if notification is not None:
            notification.delivery_status = "Delivered"
            notification.delivered_at = utcnow()
        return 0
    if job.kind == "sla_scan":
        from app.services.workflow_engine import process_sla_escalations

        return int(process_sla_escalations(db))
    if job.kind == "connector_execute":
        from app.services.connectors import execute_connector_job

        return int(execute_connector_job(db, payload))
    raise RuntimeError(f"Unsupported durable job kind: {job.kind}")


def process_outbox_batch(
    factory: sessionmaker[Session],
    *,
    limit: int = DEFAULT_BATCH_SIZE,
) -> dict[str, int]:
    """Process a bounded batch and recover stale in-progress jobs after interruption."""
    processed = 0
    failed = 0
    changes = 0
    with database_write_lock():
        for _ in range(max(1, min(int(limit), 500))):
            with factory() as db:
                job = _next_due_job(db)
                if job is None:
                    break
                now = utcnow()
                job.status = "Processing"
                job.locked_at = now
                job.attempts += 1
                job.updated_at = now
                db.commit()
                job_id = job.id

            with factory() as db:
                job = db.get(OutboxJob, job_id)
                if job is None:
                    continue
                try:
                    changes += _dispatch(db, job)
                    job.status = "Completed"
                    job.completed_at = utcnow()
                    job.locked_at = None
                    job.last_error = ""
                    job.updated_at = utcnow()
                    db.commit()
                    processed += 1
                except Exception as exc:
                    db.rollback()
                    job = db.get(OutboxJob, job_id)
                    if job is None:
                        failed += 1
                        continue
                    job.last_error = _safe_error(exc)
                    job.locked_at = None
                    job.updated_at = utcnow()
                    if job.attempts >= job.max_attempts:
                        job.status = "Failed"
                    else:
                        job.status = "Pending"
                        delay = min(60, 2 ** min(job.attempts, 6))
                        job.available_at = utcnow() + timedelta(seconds=delay)
                    db.commit()
                    failed += 1
    return {"processed": processed, "failed": failed, "business_changes": changes}
