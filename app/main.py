"""Application factory and lifecycle.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import os
import sys
import threading
import time
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from starlette.middleware.sessions import SessionMiddleware

from app.api import router as api_router
from app.auth_routes import router as auth_router
from app.portal import router as portal_router
from app.studio import router as studio_router
from app.work_management import router as work_management_router
from app.config import ROOT, Settings
from app.database import create_database_engine, create_session_factory, database_backend
from app.database_maintenance import (
    SCHEMA_VERSION,
    DatabaseHealthError,
    apply_schema_migrations,
    backup_before_migration,
    current_schema_version,
    inspect_database,
    optimize_database,
    verify_database_health,
)
from app.diagnostics import capture_critical, write_status_cache
from app.logging_setup import configure_logging
from app.models import Base, CaseRecord, WorkflowDefinition
from app.services.coordination import configure_write_serialization, database_write_lock
from app.services.identity import oidc_cookie_secure
from app.services.operations import RequestMetrics
from app.services.outbox import enqueue_sla_scan, process_outbox_batch
from app.services.runtime_tasks import RuntimeTasks
from app.services.search import detect_search_mode
from app.services.seed import seed_database
from app.version import APP_VERSION, BUILD_ID, DISPLAY_NAME
from app.web import router as web_router, templates


logger = logging.getLogger(__name__)
_fatal_hooks_installed = False


def _install_fatal_hooks() -> None:
    global _fatal_hooks_installed
    if _fatal_hooks_installed:
        return

    original_hook = sys.excepthook

    def fatal_hook(exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        if isinstance(exc, KeyboardInterrupt):
            original_hook(exc_type, exc, tb)
            return
        try:
            capture_critical(
                "uncaught_fatal_exception",
                exc,
                context={"component": "main_thread", "last_progress": "process-level exception"},
            )
        finally:
            original_hook(exc_type, exc, tb)

    sys.excepthook = fatal_hook

    if hasattr(threading, "excepthook"):
        original_thread_hook = threading.excepthook

        def thread_hook(args) -> None:  # type: ignore[no-untyped-def]
            try:
                capture_critical(
                    "uncaught_fatal_exception",
                    args.exc_value,
                    context={"component": "thread", "last_progress": getattr(args.thread, "name", "unknown")},
                )
            finally:
                original_thread_hook(args)

        threading.excepthook = thread_hook

    _fatal_hooks_installed = True


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    settings.ensure_runtime_dirs()
    configure_logging(settings.logs_dir)
    _install_fatal_hooks()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine = None
        runtime_tasks: RuntimeTasks | None = None
        startup_complete = False
        run_id = uuid4().hex[:12]
        app.state.run_id = run_id
        try:
            backend = settings.database_backend
            if backend == "sqlite":
                legacy = inspect_database(settings.db_path)
                if int(legacy.get("user_version", 0)) > SCHEMA_VERSION:
                    raise RuntimeError(
                        f"Database schema {legacy['user_version']} is newer than supported schema {SCHEMA_VERSION}."
                    )
                backup_path = backup_before_migration(
                    settings.db_path,
                    settings.backups_dir,
                    int(legacy.get("user_version", 0)),
                    SCHEMA_VERSION,
                )
                if backup_path:
                    logger.info("database_pre_migration_backup path=%s", backup_path.relative_to(settings.root))

            engine = create_database_engine(settings)
            active_backend = database_backend(engine)
            configure_write_serialization(active_backend == "sqlite")
            session_factory = create_session_factory(engine)
            app.state.engine = engine
            app.state.SessionLocal = session_factory
            app.state.settings = settings
            app.state.database_backend = active_backend

            if active_backend == "sqlite":
                Base.metadata.create_all(engine)
                schema_version = apply_schema_migrations(engine)
            else:
                # PostgreSQL schema changes are an explicit bulk-write action. The normal
                # launcher verifies and uses only a schema already prepared by the protected migrate action.
                schema_version = current_schema_version(engine)
                if schema_version != SCHEMA_VERSION:
                    raise DatabaseHealthError(
                        f"PostgreSQL schema is {schema_version}; expected {SCHEMA_VERSION}. "
                        "Run LAUNCH_WORKFLOW_PLATFORM.bat migrate intentionally before starting this backend."
                    )

            database_health = verify_database_health(engine, settings.state_dir)
            search_mode = detect_search_mode(engine)
            app.state.schema_version = schema_version
            app.state.database_health = database_health
            app.state.search_mode = search_mode

            with database_write_lock():
                with session_factory() as db:
                    should_seed = settings.seed_demo and (
                        active_backend == "sqlite"
                        or os.getenv("WORKFLOW_POSTGRES_SEED_DEMO", "").strip() == "1"
                    )
                    if should_seed:
                        seed_database(db, ROOT)
                    enqueue_sla_scan(db, settings.sla_poll_seconds, force=True)
                    db.commit()
                    workflow_count = db.scalar(select(func.count(WorkflowDefinition.id))) or 0
                    case_count = db.scalar(select(func.count(CaseRecord.id))) or 0

            startup_jobs = process_outbox_batch(session_factory, limit=250)
            if startup_jobs["failed"]:
                logger.warning("startup_outbox_failures count=%s", startup_jobs["failed"])

            if not settings.testing and settings.job_poll_seconds > 0:
                runtime_tasks = RuntimeTasks(
                    session_factory,
                    settings.sla_poll_seconds,
                    settings.job_poll_seconds,
                )
                runtime_tasks.start()
            app.state.runtime_tasks = runtime_tasks

            write_status_cache(
                {
                    "startup": "ready",
                    "database": f"{active_backend}_ready",
                    "workflow_count": workflow_count,
                    "case_count": case_count,
                    "schema_version": schema_version,
                    "sla_scheduler": "durable_worker_active" if runtime_tasks else "disabled",
                    "search_mode": search_mode,
                    "outbox_startup_processed": startup_jobs["processed"],
                    "run_id": run_id,
                    "last_progress": "application startup complete",
                },
                settings.state_dir,
            )
            logger.info(
                "application_start run_id=%s version=%s build=%s backend=%s schema=%s search=%s workflows=%s cases=%s worker=%s",
                run_id,
                APP_VERSION,
                BUILD_ID,
                active_backend,
                schema_version,
                search_mode,
                workflow_count,
                case_count,
                bool(runtime_tasks),
            )
            startup_complete = True
            yield
            logger.info("application_shutdown run_id=%s normal=true", run_id)
        except Exception as exc:
            trigger = "runtime_abort" if startup_complete else "startup_abort"
            logger.exception("%s run_id=%s", trigger, run_id)
            capture_critical(
                trigger,
                exc,
                context={
                    "phase": "lifespan_runtime" if startup_complete else "lifespan_startup",
                    "last_progress": (
                        "application was running" if startup_complete else "database migration, verification, or seed initialization"
                    ),
                },
            )
            raise
        finally:
            if runtime_tasks is not None:
                runtime_tasks.stop()
            if engine is not None:
                if startup_complete:
                    optimize_database(engine)
                engine.dispose()

    app = FastAPI(
        title=DISPLAY_NAME,
        version=APP_VERSION,
        description=(
            "Configurable business workflow and case management with visual workflow/rule governance, routing, approvals, "
            "business-calendar service levels, requester self-service, integrations, process intelligence, and tamper-evident audit evidence."
        ),
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.request_metrics = RequestMetrics()
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.session_secret(),
        same_site="lax",
        https_only=oidc_cookie_secure(),
        max_age=8 * 60 * 60,
    )
    app.mount("/static", StaticFiles(directory=str(ROOT / "app" / "static")), name="static")
    app.include_router(api_router)
    app.include_router(auth_router)
    app.include_router(portal_router)
    app.include_router(studio_router)
    app.include_router(work_management_router)
    app.include_router(web_router)

    @app.middleware("http")
    async def request_timing(request: Request, call_next):  # type: ignore[no-untyped-def]
        started = time.monotonic()
        response = await call_next(request)
        elapsed_ms = (time.monotonic() - started) * 1000.0
        response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.1f}"
        request.app.state.request_metrics.record(request.url.path, elapsed_ms, settings.slow_request_ms)
        if elapsed_ms >= settings.slow_request_ms:
            logger.warning("slow_request method=%s path=%s elapsed_ms=%.1f", request.method, request.url.path, elapsed_ms)
        return response

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        if request.url.path.startswith("/api/"):
            return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={
                "request": request,
                "app_name": DISPLAY_NAME,
                "app_version": APP_VERSION,
                "build_id": BUILD_ID,
                "status_code": exc.status_code,
                "message": str(exc.detail),
                "actor": None,
                "demo_users": [],
                "csrf_token": "",
                "unread_notifications": 0,
                "flash": None,
            },
            status_code=exc.status_code,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        if request.url.path.startswith("/api/"):
            return JSONResponse(status_code=422, content={"detail": exc.errors()})
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={
                "request": request,
                "app_name": DISPLAY_NAME,
                "app_version": APP_VERSION,
                "build_id": BUILD_ID,
                "status_code": 422,
                "message": "The submitted request could not be validated.",
                "actor": None,
                "demo_users": [],
                "csrf_token": "",
                "unread_notifications": 0,
                "flash": None,
            },
            status_code=422,
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception("request_error path=%s", request.url.path)
        if request.url.path.startswith("/api/"):
            return JSONResponse(status_code=500, content={"detail": "Internal server error."})
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={
                "request": request,
                "app_name": DISPLAY_NAME,
                "app_version": APP_VERSION,
                "build_id": BUILD_ID,
                "status_code": 500,
                "message": "The request could not be completed. The application remains available.",
                "actor": None,
                "demo_users": [],
                "csrf_token": "",
                "unread_notifications": 0,
                "flash": None,
            },
            status_code=500,
        )

    return app


app = create_app()
