"""Versioned schema migration, database health, backup, and bounded maintenance helpers.

SQLite remains the zero-configuration portfolio default. PostgreSQL uses the same explicit
schema version ledger but requires an intentional migration action before the application
will change a PostgreSQL schema.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

import os
import logging
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from app.database import SQLITE_BUSY_TIMEOUT_MS, database_backend
from app.models import OutboxJob
from app.runtime_utils import atomic_write_json, iso_now, sha256_file, utcnow
from app.services.search import clear_search_mode_cache, detect_search_mode, install_search_index
from app.version import APP_VERSION, BUILD_ID


logger = logging.getLogger(__name__)
SCHEMA_VERSION = 5
_BACKUP_PREFIX = "workflow_cases_pre_migration_"
_BACKUP_KEEP = 5
_SCHEMA_TABLE = "platform_schema_state"


class DatabaseHealthError(RuntimeError):
    """Raised when database health or schema compatibility is unsafe."""


def inspect_database(path: Path) -> dict[str, Any]:
    """Read compact legacy SQLite schema information without changing the database."""
    if not path.is_file() or path.stat().st_size == 0:
        return {"exists": False, "user_version": 0, "has_cases_table": False}
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5.0)
    try:
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        has_cases = bool(
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cases' LIMIT 1"
            ).fetchone()
        )
        return {"exists": True, "user_version": user_version, "has_cases_table": has_cases}
    finally:
        connection.close()


def backup_before_migration(database_path: Path, backups_dir: Path, from_version: int, to_version: int) -> Path | None:
    """Create a consistent, checked SQLite backup before changing an existing schema."""
    info = inspect_database(database_path)
    if not info["exists"] or not info["has_cases_table"] or from_version >= to_version:
        return None

    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = utcnow().strftime("%Y%m%d_%H%M%S_%f_UTC")
    final = backups_dir / f"{_BACKUP_PREFIX}v{from_version}_to_v{to_version}_{stamp}.db"
    temp = backups_dir / f".{final.name}.{uuid4().hex}.tmp"

    source = sqlite3.connect(str(database_path), timeout=15.0)
    target = sqlite3.connect(str(temp), timeout=15.0)
    try:
        source.backup(target)
        target.commit()
        check = target.execute("PRAGMA quick_check").fetchall()
        if check != [("ok",)]:
            raise DatabaseHealthError(f"Migration backup quick_check failed: {check[:3]}")
    finally:
        target.close()
        source.close()

    os.replace(temp, final)
    digest = sha256_file(final)
    sidecar = final.with_suffix(final.suffix + ".sha256.txt")
    sidecar.write_text(f"{digest}  {final.name}\n", encoding="utf-8")

    managed = sorted(
        [item for item in backups_dir.glob(f"{_BACKUP_PREFIX}*.db") if item.is_file()],
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for old in managed[_BACKUP_KEEP:]:
        old.unlink(missing_ok=True)
        old.with_suffix(old.suffix + ".sha256.txt").unlink(missing_ok=True)
    return final


def _schema_state_exists(engine: Engine) -> bool:
    try:
        return inspect(engine).has_table(_SCHEMA_TABLE)
    except Exception:
        return False


def current_schema_version(engine: Engine) -> int:
    backend = database_backend(engine)
    if backend == "sqlite":
        with engine.connect() as connection:
            return int(connection.exec_driver_sql("PRAGMA user_version").scalar_one())
    if not _schema_state_exists(engine):
        return 0
    with engine.connect() as connection:
        row = connection.exec_driver_sql(
            f"SELECT value FROM {_SCHEMA_TABLE} WHERE key='schema_version'"
        ).first()
    return int(row[0]) if row else 0


def _record_schema_version(connection, version: int, backend: str) -> None:  # type: ignore[no-untyped-def]
    connection.exec_driver_sql(
        f"CREATE TABLE IF NOT EXISTS {_SCHEMA_TABLE} (key VARCHAR(80) PRIMARY KEY, value VARCHAR(200) NOT NULL)"
    )
    connection.exec_driver_sql(
        f"INSERT INTO {_SCHEMA_TABLE} (key,value) VALUES ('schema_version', :version) "
        "ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
        {"version": str(version)},
    )
    if backend == "sqlite":
        connection.exec_driver_sql(f"PRAGMA user_version = {int(version)}")


def _column_names(engine: Engine, table: str) -> set[str]:
    return {str(item["name"]) for item in inspect(engine).get_columns(table)}


def apply_schema_migrations(engine: Engine) -> int:
    """Apply idempotent schema upgrades and return the resulting schema version."""
    backend = database_backend(engine)
    current = current_schema_version(engine)
    if current > SCHEMA_VERSION:
        raise DatabaseHealthError(
            f"Database schema version {current} is newer than this application supports ({SCHEMA_VERSION})."
        )

    with engine.begin() as connection:
        # v1: stability/query indexes introduced in v0.2.0.
        if current < 1:
            for statement in (
                "CREATE INDEX IF NOT EXISTS ix_cases_completed_stage_due ON cases(completed_at, stage_due_at)",
                "CREATE INDEX IF NOT EXISTS ix_cases_completed_overall_due ON cases(completed_at, overall_due_at)",
                "CREATE INDEX IF NOT EXISTS ix_cases_assignee_completed ON cases(assignee_id, completed_at)",
                "CREATE INDEX IF NOT EXISTS ix_cases_updated_at ON cases(updated_at)",
                "CREATE INDEX IF NOT EXISTS ix_cases_completed_created ON cases(completed_at, created_at)",
                "CREATE INDEX IF NOT EXISTS ix_approvals_status_case ON approvals(status, case_id)",
                "CREATE INDEX IF NOT EXISTS ix_notifications_user_read_created ON notifications(user_id, is_read, created_at)",
            ):
                connection.exec_driver_sql(statement)
            _record_schema_version(connection, 1, backend)
            current = 1

    # Inspector calls use independent connections; commit each migration boundary first.
    if current < 2:
        columns = _column_names(engine, "notifications")
        with engine.begin() as connection:
            if "delivery_status" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE notifications ADD COLUMN delivery_status VARCHAR(20) NOT NULL DEFAULT 'Queued'"
                )
            if "delivered_at" not in columns:
                connection.exec_driver_sql("ALTER TABLE notifications ADD COLUMN delivered_at TIMESTAMP NULL")
            OutboxJob.__table__.create(bind=connection, checkfirst=True)
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_notifications_delivery_status_created "
                "ON notifications(delivery_status, created_at)"
            )
            _record_schema_version(connection, 2, backend)
            current = 2

    if current < 3:
        with engine.begin() as connection:
            install_search_index(connection, backend)
            _record_schema_version(connection, 3, backend)
            current = 3
        clear_search_mode_cache(engine)

    if current < 4:
        columns = _column_names(engine, "outbox_jobs")
        with engine.begin() as connection:
            if "priority" not in columns:
                connection.exec_driver_sql(
                    "ALTER TABLE outbox_jobs ADD COLUMN priority INTEGER NOT NULL DEFAULT 50"
                )
            connection.exec_driver_sql(
                "CREATE INDEX IF NOT EXISTS ix_outbox_status_priority_available "
                "ON outbox_jobs(status, priority, available_at)"
            )
            _record_schema_version(connection, 4, backend)
            current = 4

    if current < 5:
        inspector = inspect(engine)
        has_users = inspector.has_table("users")
        has_cases = inspector.has_table("cases")
        has_attachments = inspector.has_table("attachments")
        has_workflows = inspector.has_table("workflow_definitions")
        user_columns = _column_names(engine, "users") if has_users else set()
        case_columns = _column_names(engine, "cases") if has_cases else set()
        attachment_columns = _column_names(engine, "attachments") if has_attachments else set()
        with engine.begin() as connection:
            if has_users and "capacity" not in user_columns:
                connection.exec_driver_sql("ALTER TABLE users ADD COLUMN capacity INTEGER NOT NULL DEFAULT 10")
            if has_users and "skills_json" not in user_columns:
                connection.exec_driver_sql("ALTER TABLE users ADD COLUMN skills_json TEXT NOT NULL DEFAULT '[]'")
            if has_users and "oidc_issuer" not in user_columns:
                connection.exec_driver_sql("ALTER TABLE users ADD COLUMN oidc_issuer VARCHAR(500) NULL")
            if has_users and "oidc_subject" not in user_columns:
                connection.exec_driver_sql("ALTER TABLE users ADD COLUMN oidc_subject VARCHAR(300) NULL")
            if has_users:
                connection.exec_driver_sql(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_user_oidc_identity ON users(oidc_issuer, oidc_subject)"
                )
            if has_cases and "workflow_version" not in case_columns:
                connection.exec_driver_sql("ALTER TABLE cases ADD COLUMN workflow_version INTEGER NOT NULL DEFAULT 1")
            if has_cases and "tags_json" not in case_columns:
                connection.exec_driver_sql("ALTER TABLE cases ADD COLUMN tags_json TEXT NOT NULL DEFAULT '[]'")
            if has_cases and "sla_paused_at" not in case_columns:
                connection.exec_driver_sql("ALTER TABLE cases ADD COLUMN sla_paused_at TIMESTAMP NULL")
            if has_cases and "sla_pause_reason" not in case_columns:
                connection.exec_driver_sql("ALTER TABLE cases ADD COLUMN sla_pause_reason VARCHAR(200) NOT NULL DEFAULT ''")
            if has_cases and "sla_warning_at" not in case_columns:
                connection.exec_driver_sql("ALTER TABLE cases ADD COLUMN sla_warning_at TIMESTAMP NULL")
            if has_attachments and "storage_backend" not in attachment_columns:
                connection.exec_driver_sql("ALTER TABLE attachments ADD COLUMN storage_backend VARCHAR(40) NOT NULL DEFAULT 'local'")
            if has_attachments and "storage_key" not in attachment_columns:
                connection.exec_driver_sql("ALTER TABLE attachments ADD COLUMN storage_key VARCHAR(500) NOT NULL DEFAULT ''")
            if has_attachments and "integrity_status" not in attachment_columns:
                connection.exec_driver_sql("ALTER TABLE attachments ADD COLUMN integrity_status VARCHAR(30) NOT NULL DEFAULT 'Verified'")
            if has_attachments and "scan_status" not in attachment_columns:
                connection.exec_driver_sql("ALTER TABLE attachments ADD COLUMN scan_status VARCHAR(30) NOT NULL DEFAULT 'NotConfigured'")
            if has_cases and has_workflows:
                connection.exec_driver_sql(
                    "UPDATE cases SET workflow_version = COALESCE((SELECT version FROM workflow_definitions WHERE workflow_definitions.id = cases.workflow_id), 1) "
                    "WHERE workflow_version = 1"
                )
            if has_attachments and has_cases:
                connection.exec_driver_sql(
                    "UPDATE attachments SET storage_key = (SELECT reference FROM cases WHERE cases.id = attachments.case_id) || '/' || stored_name "
                    "WHERE storage_key = ''"
                )
            _record_schema_version(connection, 5, backend)
            current = 5

    # Keep the cross-backend ledger synchronized even for legacy SQLite databases.
    with engine.begin() as connection:
        _record_schema_version(connection, current, backend)
    return current


def verify_database_health(engine: Engine, state_dir: Path) -> dict[str, Any]:
    """Run bounded backend-aware structural checks and persist a compact cached receipt."""
    backend = database_backend(engine)
    problems: list[str] = []
    receipt: dict[str, Any] = {
        "application_version": APP_VERSION,
        "build_id": BUILD_ID,
        "checked_at": iso_now(),
        "backend": backend,
        "expected_schema_version": SCHEMA_VERSION,
    }

    if backend == "sqlite":
        with engine.connect() as connection:
            quick = [str(row[0]) for row in connection.exec_driver_sql("PRAGMA quick_check").all()]
            foreign = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchmany(20)
            schema_version = int(connection.exec_driver_sql("PRAGMA user_version").scalar_one())
            journal_mode = str(connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()).lower()
            busy_timeout_ms = int(connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one())
        if quick != ["ok"]:
            problems.append(f"quick_check={quick[:5]}")
        if foreign:
            problems.append(f"foreign_key_check returned {len(foreign)} problem row(s)")
        if journal_mode != "wal":
            problems.append(f"journal_mode={journal_mode}, expected=wal")
        if busy_timeout_ms < SQLITE_BUSY_TIMEOUT_MS:
            problems.append(f"busy_timeout_ms={busy_timeout_ms}, expected_at_least={SQLITE_BUSY_TIMEOUT_MS}")
        receipt.update(
            {
                "quick_check": quick[:5],
                "foreign_key_problem_count": len(foreign),
                "journal_mode": journal_mode,
                "busy_timeout_ms": busy_timeout_ms,
            }
        )
    else:
        with engine.connect() as connection:
            responsive = connection.exec_driver_sql("SELECT 1").scalar_one() == 1
        schema_version = current_schema_version(engine)
        if not responsive:
            problems.append("database SELECT 1 readiness check failed")
        receipt.update({"responsive": bool(responsive), "pool_pre_ping": True})

    if schema_version != SCHEMA_VERSION:
        problems.append(f"schema_version={schema_version}, expected={SCHEMA_VERSION}")
    receipt.update(
        {
            "result": "PASS" if not problems else "FAIL",
            "schema_version": schema_version,
            "search_mode": detect_search_mode(engine),
            "problems": problems,
        }
    )
    atomic_write_json(state_dir / "database_health.json", receipt)
    if problems:
        raise DatabaseHealthError("Database health verification failed: " + "; ".join(problems))
    return receipt


def optimize_database(engine: Engine) -> None:
    """Run bounded low-risk maintenance during normal SQLite shutdown."""
    if database_backend(engine) != "sqlite":
        return
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA optimize")
            connection.exec_driver_sql("PRAGMA wal_checkpoint(PASSIVE)")
    except Exception as exc:
        logger.warning("database_shutdown_maintenance_failed type=%s message=%s", type(exc).__name__, exc)
