"""Dependency-free launcher phase journal and failure evidence.

This module must remain standard-library only so Windows startup failures can be recorded before
project dependencies or application imports are available.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import re
import sys
from typing import Any
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "state"
LOG_DIR = ROOT / "logs"
STATUS_PATH = STATE_DIR / "launcher_status.json"
LAST_FAILURE_PATH = STATE_DIR / "last_launcher_failure.json"
LOG_PATH = LOG_DIR / "launcher.log"
MAX_LOG_BYTES = 256 * 1024

_SECRET_PATTERN = re.compile(
    r"(?i)\b(password|passwd|token|secret|api[_-]?key|authorization|cookie)\b\s*[:=]\s*([^\s,;]+)"
)
_USER_PATH = re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\\s]+")


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _identity() -> tuple[str, str]:
    version = "unknown"
    build_id = "unknown"
    try:
        lines = (ROOT / "VERSION.txt").read_text(encoding="utf-8", errors="replace").splitlines()
        if lines:
            version = lines[0].strip() or version
        for line in lines[1:]:
            if line.lower().startswith("build:"):
                build_id = line.split(":", 1)[1].strip() or build_id
                break
    except OSError:
        pass
    return version, build_id


def _redact(text: str) -> str:
    text = _SECRET_PATTERN.sub(lambda m: f"{m.group(1)}=<redacted>", text)
    return _USER_PATH.sub("<redacted-user-path>", text)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temp.write_text(json.dumps(value, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    os.replace(temp, path)


def _append_log(line: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    try:
        if LOG_PATH.is_file() and LOG_PATH.stat().st_size > MAX_LOG_BYTES:
            rotated = LOG_PATH.with_suffix(".log.1")
            rotated.unlink(missing_ok=True)
            LOG_PATH.replace(rotated)
    except OSError:
        pass
    try:
        with LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(line.rstrip() + "\n")
    except OSError:
        pass


def write_launcher_status(
    *,
    action: str,
    phase: str,
    result: str,
    exit_code: int | None = None,
    detail: str | None = None,
    runtime_python: str | None = None,
) -> dict[str, Any]:
    """Persist the most recent launcher phase without importing application dependencies."""
    version, build_id = _identity()
    payload: dict[str, Any] = {
        "application": "Workflow & Case Management Platform",
        "version": version,
        "build_id": build_id,
        "updated_at": _iso(),
        "result": result,
        "action": action,
        "phase": phase,
        "exit_code": exit_code,
        "detail": _redact(detail or ""),
        "base_python": _redact(str(Path(sys.executable).resolve())),
        "runtime_python": _redact(runtime_python or ""),
        "python_version": platform.python_version(),
        "operating_system": platform.system(),
        "os_release": platform.release(),
        "process_id": os.getpid(),
        "root_relative": ".",
    }
    try:
        _atomic_json(STATUS_PATH, payload)
        if result == "FAIL":
            _atomic_json(LAST_FAILURE_PATH, payload)
    except OSError:
        pass
    _append_log(
        f"{payload['updated_at']} action={action} phase={phase} result={result} "
        f"exit_code={exit_code if exit_code is not None else '-'} detail={payload['detail'] or '-'}"
    )
    return payload
