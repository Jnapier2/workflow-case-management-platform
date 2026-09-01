"""Verified SQLite backup/restore qualification for recovery readiness.

The recovery Doctor never overwrites the live database. It creates a consistent backup,
checks it, restores a temporary copy, compares logical table summaries, verifies attachment
references and hashes, writes a receipt, then removes only its own temporary restore copy.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sqlite3
from typing import Any
from uuid import uuid4

from app.config import Settings
from app.database import create_database_engine, database_backend
from app.database_maintenance import SCHEMA_VERSION, current_schema_version, verify_database_health
from app.runtime_utils import atomic_write_json, iso_now, sha256_file, utcnow
from app.version import APP_VERSION, BUILD_ID


_DOCTOR_BACKUP_PREFIX = "workflow_cases_verified_backup_"
_DOCTOR_BACKUP_KEEP = 5
_COMPARE_TABLES = (
    "workflow_definitions",
    "users",
    "cases",
    "approvals",
    "comments",
    "attachments",
    "activities",
    "notifications",
    "outbox_jobs",
)


def _table_summary(connection: sqlite3.Connection) -> dict[str, dict[str, int]]:
    existing = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    summary: dict[str, dict[str, int]] = {}
    for table in _COMPARE_TABLES:
        if table not in existing:
            summary[table] = {"count": 0, "max_id": 0}
            continue
        count, max_id = connection.execute(f"SELECT COUNT(*), COALESCE(MAX(id),0) FROM {table}").fetchone()
        summary[table] = {"count": int(count), "max_id": int(max_id)}
    return summary


def _attachment_evidence_check(connection: sqlite3.Connection, uploads_dir: Path) -> dict[str, Any]:
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='attachments' LIMIT 1"
    ).fetchone():
        return {"checked": 0, "missing": [], "size_mismatch": [], "sha256_mismatch": []}
    rows = connection.execute(
        "SELECT a.id,c.reference,a.stored_name,a.size_bytes,a.sha256 "
        "FROM attachments a JOIN cases c ON c.id=a.case_id ORDER BY a.id"
    ).fetchall()
    missing: list[int] = []
    size_mismatch: list[int] = []
    sha_mismatch: list[int] = []
    root = uploads_dir.resolve()
    for attachment_id, reference, stored_name, expected_size, expected_sha in rows:
        path = (uploads_dir / str(reference) / str(stored_name)).resolve()
        if root not in path.parents or not path.is_file():
            missing.append(int(attachment_id))
            continue
        if path.stat().st_size != int(expected_size):
            size_mismatch.append(int(attachment_id))
            continue
        if sha256_file(path).lower() != str(expected_sha).lower():
            sha_mismatch.append(int(attachment_id))
    return {
        "checked": len(rows),
        "missing": missing[:50],
        "size_mismatch": size_mismatch[:50],
        "sha256_mismatch": sha_mismatch[:50],
        "problem_count": len(missing) + len(size_mismatch) + len(sha_mismatch),
    }


def _sqlite_schema_probe(connection: sqlite3.Connection) -> dict[str, Any]:
    """Read independent SQLite schema markers without changing the database."""
    user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
    tables = {
        str(row[0])
        for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    ledger_version: int | None = None
    if "platform_schema_state" in tables:
        row = connection.execute(
            "SELECT value FROM platform_schema_state WHERE key='schema_version'"
        ).fetchone()
        if row is not None:
            try:
                ledger_version = int(row[0])
            except (TypeError, ValueError):
                ledger_version = None

    notification_columns: set[str] = set()
    outbox_columns: set[str] = set()
    if "notifications" in tables:
        notification_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(notifications)").fetchall()}
    if "outbox_jobs" in tables:
        outbox_columns = {str(row[1]) for row in connection.execute("PRAGMA table_info(outbox_jobs)").fetchall()}

    current_structure = (
        "cases" in tables
        and "notifications" in tables
        and "outbox_jobs" in tables
        and {"delivery_status", "delivered_at"}.issubset(notification_columns)
        and "priority" in outbox_columns
    )
    return {
        "sqlite_user_version": user_version,
        "schema_ledger_version": ledger_version,
        "current_structure_detected": current_structure,
    }


def _schema_compatibility(probe: dict[str, Any]) -> tuple[str, list[str], list[str]]:
    """Classify schema metadata separately from backup/restore recoverability."""
    user_version = int(probe.get("sqlite_user_version") or 0)
    ledger_raw = probe.get("schema_ledger_version")
    ledger_version = int(ledger_raw) if isinstance(ledger_raw, int) else None
    current_structure = bool(probe.get("current_structure_detected"))
    advisories: list[str] = []
    fatal: list[str] = []

    observed = [value for value in (user_version, ledger_version) if value is not None]
    if any(value > SCHEMA_VERSION for value in observed):
        fatal.append(
            f"database schema marker is newer than supported: user_version={user_version} "
            f"ledger={ledger_version} expected_at_most={SCHEMA_VERSION}"
        )
        return "future_schema", advisories, fatal

    if user_version == SCHEMA_VERSION and ledger_version in (None, SCHEMA_VERSION):
        return "current", advisories, fatal

    if ledger_version == SCHEMA_VERSION and user_version in (0, SCHEMA_VERSION):
        if user_version == 0:
            advisories.append(
                "SQLite user_version marker is 0 while the platform schema ledger is current; "
                "normal application startup will resynchronize the marker."
            )
            return "marker_drift", advisories, fatal
        return "current", advisories, fatal

    if current_structure and user_version == 0 and ledger_version is None:
        advisories.append(
            "Current schema structures are present but SQLite schema markers are unsynchronized; "
            "normal application startup will apply the idempotent schema ledger/marker synchronization."
        )
        return "marker_unsynchronized", advisories, fatal

    effective = max(observed) if observed else 0
    if effective < SCHEMA_VERSION:
        advisories.append(
            f"recoverable database schema {effective} is older than application schema {SCHEMA_VERSION}; "
            "normal SQLite startup will create a checked pre-migration backup before upgrading it."
        )
        return "migration_required", advisories, fatal

    if user_version != ledger_version and ledger_version is not None:
        advisories.append(
            f"SQLite schema markers disagree (user_version={user_version}, ledger={ledger_version}); "
            "normal application startup will re-evaluate and synchronize supported markers."
        )
        return "marker_mismatch", advisories, fatal

    return "current", advisories, fatal


def _retain_managed_backups(backups_dir: Path) -> None:
    values = sorted(
        [path for path in backups_dir.glob(f"{_DOCTOR_BACKUP_PREFIX}*.db") if path.is_file()],
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    for old in values[_DOCTOR_BACKUP_KEEP:]:
        old.unlink(missing_ok=True)
        old.with_suffix(old.suffix + ".sha256.txt").unlink(missing_ok=True)


def run_recovery_doctor(settings: Settings) -> dict[str, Any]:
    settings.ensure_runtime_dirs()
    started = iso_now()
    identity = {"application_version": APP_VERSION, "build_id": BUILD_ID}
    if settings.database_backend != "sqlite":
        engine = create_database_engine(settings)
        try:
            health = verify_database_health(engine, settings.state_dir)
            receipt = {
                **identity,
                "checked_at": started,
                "result": "PASS_WITH_LIMITATION",
                "backend": database_backend(engine),
                "schema_version": current_schema_version(engine),
                "database_health": health.get("result"),
                "backup_restore": "not_run",
                "reason": "PostgreSQL backup/restore must use server-native or managed-service backup tooling.",
                "live_database_modified": False,
            }
        finally:
            engine.dispose()
        atomic_write_json(settings.state_dir / "recovery_doctor.json", receipt)
        return receipt

    database = settings.db_path
    problems: list[str] = []
    if not database.is_file() or database.stat().st_size == 0:
        receipt = {
            **identity,
            "checked_at": started,
            "result": "FAIL",
            "backend": "sqlite",
            "problems": ["The project-local database does not exist or is empty."],
            "live_database_modified": False,
        }
        atomic_write_json(settings.state_dir / "recovery_doctor.json", receipt)
        return receipt

    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = settings.diagnostics_dir / "temp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    stamp = utcnow().strftime("%Y%m%d_%H%M%S_%f_UTC")
    backup = settings.backups_dir / f"{_DOCTOR_BACKUP_PREFIX}{stamp}.db"
    backup_temp = settings.backups_dir / f".{backup.name}.{uuid4().hex}.tmp"
    restore_copy = temp_dir / f"recovery_restore_{uuid4().hex}.db"

    source = sqlite3.connect(str(database), timeout=15.0)
    target = sqlite3.connect(str(backup_temp), timeout=15.0)
    try:
        source_check = source.execute("PRAGMA quick_check").fetchall()
        if source_check != [("ok",)]:
            problems.append(f"live quick_check failed: {source_check[:3]}")
        source.backup(target)
        target.commit()
    finally:
        target.close()
        source.close()

    os.replace(backup_temp, backup)
    backup_hash = sha256_file(backup)
    backup.with_suffix(backup.suffix + ".sha256.txt").write_text(
        f"{backup_hash}  {backup.name}\n", encoding="utf-8"
    )
    _retain_managed_backups(settings.backups_dir)

    shutil.copy2(backup, restore_copy)
    restore_hash = sha256_file(restore_copy)
    if restore_hash != backup_hash:
        problems.append("temporary restore copy SHA-256 does not match backup")

    backup_connection = sqlite3.connect(str(backup), timeout=15.0)
    restore_connection = sqlite3.connect(str(restore_copy), timeout=15.0)
    try:
        backup_integrity = backup_connection.execute("PRAGMA integrity_check").fetchall()
        restore_integrity = restore_connection.execute("PRAGMA integrity_check").fetchall()
        backup_foreign = backup_connection.execute("PRAGMA foreign_key_check").fetchall()
        restore_foreign = restore_connection.execute("PRAGMA foreign_key_check").fetchall()
        backup_probe = _sqlite_schema_probe(backup_connection)
        restore_probe = _sqlite_schema_probe(restore_connection)
        backup_version = int(backup_probe["sqlite_user_version"])
        restore_version = int(restore_probe["sqlite_user_version"])
        backup_summary = _table_summary(backup_connection)
        restore_summary = _table_summary(restore_connection)
        evidence = _attachment_evidence_check(backup_connection, settings.uploads_dir)
    finally:
        backup_connection.close()
        restore_connection.close()
        restore_copy.unlink(missing_ok=True)

    if backup_integrity != [("ok",)]:
        problems.append(f"backup integrity_check failed: {backup_integrity[:3]}")
    if restore_integrity != [("ok",)]:
        problems.append(f"restore integrity_check failed: {restore_integrity[:3]}")
    if backup_foreign or restore_foreign:
        problems.append("backup or restore foreign_key_check found relationship problems")
    schema_compatibility, schema_advisories, schema_fatal = _schema_compatibility(backup_probe)
    problems.extend(schema_fatal)
    if backup_probe != restore_probe:
        problems.append(
            "schema metadata differs between backup and temporary restore "
            f"(backup={backup_probe}, restore={restore_probe})"
        )
    if backup_summary != restore_summary:
        problems.append("logical table summaries differ between backup and temporary restore")
    if evidence.get("problem_count", 0):
        problems.append(f"attachment evidence verification found {evidence['problem_count']} problem(s)")

    receipt = {
        **identity,
        "checked_at": started,
        "completed_at": iso_now(),
        "result": (
            "FAIL" if problems else ("PASS_WITH_ADVISORY" if schema_advisories else "PASS")
        ),
        "backend": "sqlite",
        "backup_path": str(backup.relative_to(settings.root)),
        "backup_sha256": backup_hash,
        "restore_copy_sha256": restore_hash,
        "restore_copy_removed": not restore_copy.exists(),
        "schema_version": backup_version,
        "expected_schema_version": SCHEMA_VERSION,
        "schema_compatibility": schema_compatibility,
        "schema_markers": backup_probe,
        "advisories": schema_advisories,
        "backup_integrity_check": [str(row[0]) for row in backup_integrity[:5]],
        "restore_integrity_check": [str(row[0]) for row in restore_integrity[:5]],
        "backup_foreign_key_problem_count": len(backup_foreign),
        "restore_foreign_key_problem_count": len(restore_foreign),
        "logical_summary": backup_summary,
        "attachment_evidence": evidence,
        "problems": problems,
        "live_database_modified": False,
    }
    report = settings.reports_dir / f"RECOVERY_DOCTOR_{stamp}.json"
    atomic_write_json(report, receipt)
    atomic_write_json(settings.state_dir / "recovery_doctor.json", {**receipt, "report_path": str(report.relative_to(settings.root))})
    return receipt
