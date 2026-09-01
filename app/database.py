"""Database engine and request-scoped session setup.

SQLite remains the zero-configuration default. PostgreSQL is an explicit opt-in backend
using Psycopg 3 and bounded SQLAlchemy connection pooling.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from fastapi import Request
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings


SQLITE_BUSY_TIMEOUT_MS = 15_000
SQLITE_CACHE_KIB = 32 * 1024


def create_sqlite_engine(database_path: Path) -> Engine:
    engine = create_engine(
        f"sqlite:///{database_path.as_posix()}",
        connect_args={
            "check_same_thread": False,
            "timeout": SQLITE_BUSY_TIMEOUT_MS / 1000,
        },
        pool_pre_ping=True,
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute("PRAGMA wal_autocheckpoint=1000")
            cursor.execute("PRAGMA temp_store=MEMORY")
            cursor.execute(f"PRAGMA cache_size=-{SQLITE_CACHE_KIB}")
        finally:
            cursor.close()

    return engine


def create_database_engine(settings: Settings) -> Engine:
    backend = settings.database_backend
    if backend == "sqlite":
        return create_sqlite_engine(settings.db_path)
    if backend == "postgresql":
        url = settings.resolved_database_url
        # Keep credentials out of logs/state. SQLAlchemy receives the URL directly.
        return create_engine(
            url,
            pool_pre_ping=True,
            pool_size=max(1, int(settings.postgres_pool_size)),
            max_overflow=max(0, int(settings.postgres_max_overflow)),
            pool_recycle=max(60, int(settings.postgres_pool_recycle_seconds)),
        )
    raise RuntimeError(
        f"Unsupported database backend {backend!r}. Use project-local SQLite or a postgresql+psycopg URL."
    )


def database_backend(engine: Engine) -> str:
    name = engine.dialect.name.lower()
    return "postgresql" if name.startswith("postgres") else name


def safe_database_target(engine: Engine) -> str:
    """Return a credential-redacted backend target for diagnostics/status pages."""
    try:
        return engine.url.render_as_string(hide_password=True)
    except Exception:
        return database_backend(engine)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db(request: Request) -> Generator[Session, None, None]:
    factory: sessionmaker[Session] = request.app.state.SessionLocal
    db = factory()
    try:
        yield db
    finally:
        db.close()
