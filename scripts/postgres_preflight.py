"""Read-only PostgreSQL connectivity and schema preflight.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.database import create_database_engine, database_backend, safe_database_target
from app.database_maintenance import SCHEMA_VERSION, current_schema_version
from app.services.search import detect_search_mode


def main() -> int:
    settings = Settings()
    if settings.database_backend != "postgresql":
        print("WORKFLOW_DATABASE_URL is not configured for PostgreSQL. No network connection was attempted.")
        return 2
    engine = create_database_engine(settings)
    try:
        with engine.connect() as connection:
            responsive = connection.exec_driver_sql("SELECT 1").scalar_one() == 1
        current = current_schema_version(engine)
        print(f"Backend: {database_backend(engine)}")
        print(f"Target: {safe_database_target(engine)}")
        print(f"Responsive: {responsive}")
        print(f"Schema: {current} / expected {SCHEMA_VERSION}")
        print(f"Search: {detect_search_mode(engine)}")
        return 0 if responsive and current == SCHEMA_VERSION else 3
    finally:
        engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
