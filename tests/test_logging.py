"""Logging timestamp regression tests.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
import time

from app.logging_setup import configure_logging


def test_application_log_formatter_emits_true_utc(tmp_path: Path):
    root = logging.getLogger()
    prior = list(root.handlers)
    for handler in prior:
        if getattr(handler, "_workflow_handler", False):
            root.removeHandler(handler)
            handler.close()
    configure_logging(tmp_path / "logs")
    handler = next(item for item in root.handlers if getattr(item, "_workflow_handler", False))
    try:
        formatter = handler.formatter
        assert formatter is not None
        assert formatter.converter is time.gmtime
        record = logging.LogRecord("utc-test", logging.INFO, __file__, 1, "ok", (), None)
        record.created = 0.0
        assert formatter.formatTime(record, formatter.datefmt) == "1970-01-01T00:00:00"
        assert datetime.fromtimestamp(record.created, tz=timezone.utc).year == 1970
    finally:
        root.removeHandler(handler)
        handler.close()
