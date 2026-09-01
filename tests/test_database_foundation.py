"""Database stability and migration regression tests.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from app.database import SQLITE_BUSY_TIMEOUT_MS, create_sqlite_engine
from app.database_maintenance import (
    SCHEMA_VERSION,
    DatabaseHealthError,
    apply_schema_migrations,
    backup_before_migration,
    inspect_database,
    verify_database_health,
)
from app.models import Base


def test_legacy_database_is_backed_up_migrated_and_health_checked(tmp_path: Path):
    database = tmp_path / "data" / "workflow_cases.db"
    database.parent.mkdir(parents=True)
    engine = create_sqlite_engine(database)
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA user_version = 0")
    finally:
        engine.dispose()

    before = inspect_database(database)
    assert before == {"exists": True, "user_version": 0, "has_cases_table": True}

    backup = backup_before_migration(database, tmp_path / "backups", 0, SCHEMA_VERSION)
    assert backup is not None and backup.is_file()
    sidecar = backup.with_suffix(backup.suffix + ".sha256.txt")
    assert sidecar.is_file()
    with sqlite3.connect(str(backup)) as connection:
        assert connection.execute("PRAGMA quick_check").fetchall() == [("ok",)]
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0

    engine = create_sqlite_engine(database)
    try:
        assert apply_schema_migrations(engine) == SCHEMA_VERSION
        receipt = verify_database_health(engine, tmp_path / "state")
        assert receipt["result"] == "PASS"
        assert receipt["schema_version"] == SCHEMA_VERSION
        assert receipt["journal_mode"] == "wal"
        assert receipt["busy_timeout_ms"] == SQLITE_BUSY_TIMEOUT_MS

        with engine.connect() as connection:
            indexes = {
                str(row[0])
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).all()
            }
        assert {
            "ix_cases_completed_stage_due",
            "ix_cases_completed_overall_due",
            "ix_cases_assignee_completed",
            "ix_cases_updated_at",
            "ix_cases_completed_created",
            "ix_approvals_status_case",
            "ix_notifications_user_read_created",
        }.issubset(indexes)
    finally:
        engine.dispose()


def test_newer_database_schema_fails_closed(tmp_path: Path):
    database = tmp_path / "future.db"
    engine = create_sqlite_engine(database)
    try:
        Base.metadata.create_all(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
        with pytest.raises(DatabaseHealthError):
            apply_schema_migrations(engine)
    finally:
        engine.dispose()


def test_schema_four_adds_enterprise_user_identity_columns_without_rewriting_users(tmp_path: Path):
    database = tmp_path / "legacy_v4_users.db"
    with sqlite3.connect(str(database)) as connection:
        connection.executescript(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                name VARCHAR(120) NOT NULL,
                email VARCHAR(200) NOT NULL UNIQUE,
                role VARCHAR(80) NOT NULL,
                active BOOLEAN NOT NULL DEFAULT 1,
                created_at TIMESTAMP NOT NULL
            );
            INSERT INTO users(id,name,email,role,active,created_at)
            VALUES (1,'Existing User','existing@example.test','Case Manager',1,CURRENT_TIMESTAMP);
            PRAGMA user_version = 4;
            """
        )
    engine = create_sqlite_engine(database)
    try:
        assert apply_schema_migrations(engine) == SCHEMA_VERSION
        with engine.connect() as connection:
            columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(users)").all()}
            existing = connection.exec_driver_sql("SELECT name,email,role,capacity,skills_json,oidc_issuer,oidc_subject FROM users WHERE id=1").one()
            indexes = {row[1] for row in connection.exec_driver_sql("PRAGMA index_list(users)").all()}
        assert {"capacity", "skills_json", "oidc_issuer", "oidc_subject"}.issubset(columns)
        assert tuple(existing[:5]) == ("Existing User", "existing@example.test", "Case Manager", 10, "[]")
        assert existing[5] is None and existing[6] is None
        assert "uq_user_oidc_identity" in indexes
    finally:
        engine.dispose()
