"""Backend-aware case search with indexed full-text paths and safe fallback behavior.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

import logging
import re
import weakref

from sqlalchemy import func, or_, select, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import Session

from app.database import database_backend
from app.models import CaseRecord


logger = logging.getLogger(__name__)
_MODE_CACHE: "weakref.WeakKeyDictionary[Engine, str]" = weakref.WeakKeyDictionary()


def _fallback_indexes(connection: Connection) -> None:
    for statement in (
        "CREATE INDEX IF NOT EXISTS ix_cases_title ON cases(title)",
        "CREATE INDEX IF NOT EXISTS ix_cases_requester_name ON cases(requester_name)",
        "CREATE INDEX IF NOT EXISTS ix_cases_requester_email ON cases(requester_email)",
    ):
        connection.exec_driver_sql(statement)


def install_search_index(connection: Connection, backend: str) -> str:
    """Install a scalable search path. Failure falls back to ordinary indexed fields."""
    _fallback_indexes(connection)
    if backend == "sqlite":
        try:
            connection.exec_driver_sql(
                "CREATE VIRTUAL TABLE IF NOT EXISTS cases_fts USING fts5("
                "reference, title, requester_name, requester_email, description, "
                "content='cases', content_rowid='id', tokenize='unicode61')"
            )
            connection.exec_driver_sql(
                "CREATE TRIGGER IF NOT EXISTS cases_fts_ai AFTER INSERT ON cases BEGIN "
                "INSERT INTO cases_fts(rowid,reference,title,requester_name,requester_email,description) "
                "VALUES (new.id,new.reference,new.title,new.requester_name,new.requester_email,new.description); END"
            )
            connection.exec_driver_sql(
                "CREATE TRIGGER IF NOT EXISTS cases_fts_ad AFTER DELETE ON cases BEGIN "
                "INSERT INTO cases_fts(cases_fts,rowid,reference,title,requester_name,requester_email,description) "
                "VALUES('delete',old.id,old.reference,old.title,old.requester_name,old.requester_email,old.description); END"
            )
            connection.exec_driver_sql(
                "CREATE TRIGGER IF NOT EXISTS cases_fts_au AFTER UPDATE ON cases BEGIN "
                "INSERT INTO cases_fts(cases_fts,rowid,reference,title,requester_name,requester_email,description) "
                "VALUES('delete',old.id,old.reference,old.title,old.requester_name,old.requester_email,old.description); "
                "INSERT INTO cases_fts(rowid,reference,title,requester_name,requester_email,description) "
                "VALUES (new.id,new.reference,new.title,new.requester_name,new.requester_email,new.description); END"
            )
            connection.exec_driver_sql("INSERT INTO cases_fts(cases_fts) VALUES('rebuild')")
            return "sqlite_fts5"
        except DatabaseError as exc:
            logger.warning("fts5_unavailable fallback=indexed_like type=%s", type(exc).__name__)
            return "indexed_like"
    if backend == "postgresql":
        connection.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_cases_search_fts ON cases USING GIN "
            "(to_tsvector('simple', coalesce(reference,'') || ' ' || coalesce(title,'') || ' ' || "
            "coalesce(requester_name,'') || ' ' || coalesce(requester_email,'') || ' ' || coalesce(description,'')))"
        )
        return "postgresql_fts"
    return "indexed_like"


def clear_search_mode_cache(engine: Engine | None = None) -> None:
    if engine is None:
        _MODE_CACHE.clear()
    else:
        _MODE_CACHE.pop(engine, None)


def detect_search_mode(engine: Engine) -> str:
    cached = _MODE_CACHE.get(engine)
    if cached:
        return cached
    backend = database_backend(engine)
    mode = "indexed_like"
    try:
        with engine.connect() as connection:
            if backend == "sqlite":
                found = connection.exec_driver_sql(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cases_fts' LIMIT 1"
                ).first()
                mode = "sqlite_fts5" if found else "indexed_like"
            elif backend == "postgresql":
                found = connection.exec_driver_sql(
                    "SELECT 1 FROM pg_indexes WHERE indexname='ix_cases_search_fts' LIMIT 1"
                ).first()
                mode = "postgresql_fts" if found else "indexed_like"
    except Exception:
        mode = "indexed_like"
    _MODE_CACHE[engine] = mode
    return mode


def _fts5_query(term: str) -> str:
    # Convert untrusted input into literal prefix tokens; never pass user FTS operators through.
    tokens = re.findall(r"[\w@.-]+", term, flags=re.UNICODE)[:12]
    values = []
    for token in tokens:
        clean = token.replace('"', '')[:64]
        if clean:
            values.append(f'"{clean}"*')
    return " AND ".join(values)


def apply_case_search(query, db: Session, search: str | None):  # type: ignore[no-untyped-def]
    if not search:
        return query
    term = search.strip()[:200]
    if not term:
        return query
    if re.fullmatch(r"[A-Za-z]{2,8}-\d+", term):
        return query.where(CaseRecord.reference == term.upper())

    engine = db.get_bind()
    mode = detect_search_mode(engine)
    if mode == "sqlite_fts5":
        fts = _fts5_query(term)
        if fts:
            matching_ids = (
                select(text("rowid"))
                .select_from(text("cases_fts"))
                .where(text("cases_fts MATCH :case_fts_query"))
            )
            return query.where(CaseRecord.id.in_(matching_ids)).params(case_fts_query=fts)
    elif mode == "postgresql_fts":
        document = func.concat_ws(
            " ",
            CaseRecord.reference,
            CaseRecord.title,
            CaseRecord.requester_name,
            CaseRecord.requester_email,
            CaseRecord.description,
        )
        return query.where(
            func.to_tsvector("simple", document).op("@@")(func.plainto_tsquery("simple", term))
        )

    pattern = f"%{term}%"
    return query.where(
        or_(
            CaseRecord.reference.ilike(pattern),
            CaseRecord.title.ilike(pattern),
            CaseRecord.requester_name.ilike(pattern),
            CaseRecord.requester_email.ilike(pattern),
        )
    )
