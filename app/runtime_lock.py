"""Project-local single-instance lock for the supported one-worker desktop runtime.

The lock is advisory and same-computer only. It never coordinates or blocks another PC.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import sys
from typing import Any
from uuid import uuid4


class InstanceAlreadyRunning(RuntimeError):
    pass


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if sys.platform == "win32":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        # Access denied means the process exists but is not queryable by this process.
        return int(kernel32.GetLastError()) == 5
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_lock(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


class ProjectInstanceLock:
    def __init__(self, path: Path, *, build_id: str) -> None:
        self.path = path
        self.build_id = build_id
        self.acquired = False

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps({"pid": os.getpid(), "build_id": self.build_id}, sort_keys=True) + "\n"
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                self.acquired = True
                return
            except FileExistsError:
                existing = _read_lock(self.path)
                owner = int(existing.get("pid") or 0)
                if owner and pid_alive(owner):
                    raise InstanceAlreadyRunning(
                        f"Another local instance is already running (PID {owner}). Close it before launching a second copy."
                    )
                # This is the application's own known lock file and its owner is no longer alive.
                stale = self.path.with_name(f".{self.path.name}.{uuid4().hex}.stale")
                try:
                    os.replace(self.path, stale)
                    stale.unlink(missing_ok=True)
                except OSError:
                    pass
        raise InstanceAlreadyRunning("The local instance lock could not be acquired safely.")

    def release(self) -> None:
        if not self.acquired:
            return
        existing = _read_lock(self.path)
        if int(existing.get("pid") or 0) == os.getpid():
            self.path.unlink(missing_ok=True)
        self.acquired = False

    def __enter__(self) -> "ProjectInstanceLock":
        self.acquire()
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:  # type: ignore[no-untyped-def]
        self.release()
