"""Bounded project-local logging.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def configure_logging(logs_dir: Path) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    if any(getattr(handler, "_workflow_handler", False) for handler in root.handlers):
        return
    formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(name)s %(message)s", "%Y-%m-%dT%H:%M:%S")
    formatter.converter = __import__("time").gmtime
    file_handler = RotatingFileHandler(
        logs_dir / "application.log",
        maxBytes=2 * 1024 * 1024,
        backupCount=4,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler._workflow_handler = True  # type: ignore[attr-defined]
    root.addHandler(file_handler)
