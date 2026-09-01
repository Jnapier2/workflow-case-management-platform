"""Explicit schema migration entrypoint, primarily for opt-in PostgreSQL backends.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.database import create_database_engine, database_backend, safe_database_target
from app.database_maintenance import (
    SCHEMA_VERSION,
    apply_schema_migrations,
    backup_before_migration,
    current_schema_version,
    inspect_database,
    verify_database_health,
)
from app.models import Base
from app.runtime_lock import InstanceAlreadyRunning, ProjectInstanceLock
from app.version import BUILD_ID
from app.services.coordination import configure_write_serialization


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Apply the configured schema migration.")
    args = parser.parse_args()
    if not args.apply:
        print("No schema changes were made. Supply --apply only after the protected launcher confirmation.")
        return 2

    settings = Settings()
    settings.ensure_runtime_dirs()
    lock = ProjectInstanceLock(settings.state_dir / "server_instance.lock", build_id=f"{BUILD_ID}-MIGRATION")
    try:
        lock.acquire()
    except InstanceAlreadyRunning as exc:
        print(f"Migration refused: {exc}")
        print("Stop the running application before applying database schema changes.")
        return 3

    engine = None
    try:
        if settings.database_backend == "sqlite":
            legacy = inspect_database(settings.db_path)
            backup_before_migration(
                settings.db_path,
                settings.backups_dir,
                int(legacy.get("user_version", 0)),
                SCHEMA_VERSION,
            )

        engine = create_database_engine(settings)
        backend = database_backend(engine)
        configure_write_serialization(backend == "sqlite")
        before = current_schema_version(engine)
        Base.metadata.create_all(engine)
        after = apply_schema_migrations(engine)
        health = verify_database_health(engine, settings.state_dir)
        print(f"Backend: {backend}")
        print(f"Target: {safe_database_target(engine)}")
        print(f"Schema: {before} -> {after}")
        print(f"Health: {health['result']}")
        return 0 if after == SCHEMA_VERSION and health["result"] == "PASS" else 2
    finally:
        if engine is not None:
            engine.dispose()
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
