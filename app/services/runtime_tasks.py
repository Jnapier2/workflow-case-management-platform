"""Low-overhead persistent background work for the local application runtime.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

from datetime import datetime
import logging
import threading

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.services.common import utcnow
from app.services.coordination import database_write_lock
from app.services.outbox import enqueue_sla_scan, process_outbox_batch


logger = logging.getLogger(__name__)


class RuntimeTasks:
    """Poll the persistent outbox and enqueue restart-safe SLA scans."""

    def __init__(
        self,
        factory: sessionmaker[Session],
        sla_interval_seconds: float,
        job_poll_seconds: float = 5.0,
    ) -> None:
        self.factory = factory
        self.sla_interval_seconds = max(5.0, float(sla_interval_seconds))
        self.job_poll_seconds = max(1.0, float(job_poll_seconds))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_run_utc: datetime | None = None
        self.last_change_count = 0
        self.jobs_processed = 0
        self.failure_count = 0

    @property
    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def start(self) -> None:
        if self.alive:
            return
        self._thread = threading.Thread(target=self._run, name="workflow-durable-worker", daemon=True)
        self._thread.start()
        logger.info(
            "durable_worker_start sla_interval_seconds=%s job_poll_seconds=%s",
            self.sla_interval_seconds,
            self.job_poll_seconds,
        )

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)
        logger.info(
            "durable_worker_stop alive=%s failures=%s jobs_processed=%s",
            self.alive,
            self.failure_count,
            self.jobs_processed,
        )

    def _run_once(self) -> None:
        try:
            with database_write_lock():
                with self.factory() as db:
                    try:
                        enqueue_sla_scan(db, self.sla_interval_seconds)
                        db.commit()
                    except IntegrityError:
                        # Multiple PostgreSQL workers can race on the same SLA bucket.
                        # The unique dedupe key makes one winner authoritative.
                        db.rollback()
            result = process_outbox_batch(self.factory, limit=100)
            self.last_change_count = int(result["business_changes"])
            self.jobs_processed += int(result["processed"])
            self.failure_count = 0 if result["failed"] == 0 else self.failure_count + int(result["failed"])
            if result["processed"] or result["business_changes"]:
                logger.info(
                    "durable_worker_batch processed=%s failed=%s business_changes=%s",
                    result["processed"],
                    result["failed"],
                    result["business_changes"],
                )
        except Exception as exc:
            self.failure_count += 1
            logger.warning(
                "durable_worker_failure count=%s type=%s message=%s",
                self.failure_count,
                type(exc).__name__,
                exc,
            )
        finally:
            self.last_run_utc = utcnow()

    def _run(self) -> None:
        self._run_once()
        while not self._stop.wait(self.job_poll_seconds):
            self._run_once()
