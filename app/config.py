"""Project-local configuration and database-backend selection.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import os
import secrets
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]


@dataclass(slots=True)
class Settings:
    root: Path = ROOT
    host: str = "127.0.0.1"
    port: int = 8010
    demo_mode: bool = True
    seed_demo: bool = True
    testing: bool = False
    database_path: Path | None = None
    database_url: str | None = None
    max_upload_bytes: int = 10 * 1024 * 1024
    sla_poll_seconds: float = 60.0
    job_poll_seconds: float = 5.0
    slow_request_ms: float = 1000.0
    postgres_pool_size: int = 5
    postgres_max_overflow: int = 10
    postgres_pool_recycle_seconds: int = 1800

    @property
    def data_dir(self) -> Path:
        return self.root / "data"

    @property
    def uploads_dir(self) -> Path:
        return self.root / "uploads"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    @property
    def state_dir(self) -> Path:
        return self.root / "state"

    @property
    def diagnostics_dir(self) -> Path:
        return self.root / "diagnostics"

    @property
    def backups_dir(self) -> Path:
        return self.root / "backups"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    @property
    def db_path(self) -> Path:
        if self.database_path is not None:
            return self.database_path
        override = os.getenv("WORKFLOW_DB_PATH")
        return Path(override).expanduser().resolve() if override else self.data_dir / "workflow_cases.db"

    @property
    def resolved_database_url(self) -> str:
        """Return the configured SQLAlchemy URL without logging or persisting secrets."""
        configured = self.database_url or os.getenv("WORKFLOW_DATABASE_URL")
        if configured:
            return configured.strip()
        return f"sqlite:///{self.db_path.as_posix()}"

    @property
    def database_backend(self) -> str:
        scheme = urlsplit(self.resolved_database_url).scheme.lower()
        if scheme.startswith("sqlite"):
            return "sqlite"
        if scheme.startswith("postgresql") or scheme.startswith("postgres"):
            return "postgresql"
        return scheme or "unknown"

    def ensure_runtime_dirs(self) -> None:
        for path in (
            self.data_dir,
            self.uploads_dir,
            self.logs_dir,
            self.state_dir,
            self.diagnostics_dir / "exports",
            self.diagnostics_dir / "crash_capsules",
            self.diagnostics_dir / "temp",
            self.backups_dir,
            self.reports_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
        if self.database_backend == "sqlite":
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def session_secret(self) -> str:
        """Return a project-local signed-session secret without exposing it."""
        self.ensure_runtime_dirs()
        secret_file = self.state_dir / "session_secret.txt"
        if secret_file.exists():
            value = secret_file.read_text(encoding="utf-8").strip()
            if len(value) >= 32:
                return value
        value = secrets.token_urlsafe(48)
        temp = secret_file.with_suffix(".tmp")
        temp.write_text(value, encoding="utf-8")
        os.replace(temp, secret_file)
        return value
