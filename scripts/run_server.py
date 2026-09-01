"""Stable root-derived local server entrypoint.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

import os
from pathlib import Path
import signal
import socket
import sys
import threading
import time
import urllib.request
import webbrowser


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import uvicorn

from app.config import Settings
from app.runtime_lock import InstanceAlreadyRunning, ProjectInstanceLock
from app.version import BUILD_ID




def _port_available(host: str, port: int) -> bool:
    """Return True when a localhost TCP port can be bound without touching the current holder."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            # Use a strict bind probe. Do not opt into address reuse: on Windows,
            # SO_REUSEADDR can allow multiple processes to bind the same endpoint.
            sock.bind((host, port))
        return True
    except OSError:
        return False


def _preferred_port(default: int) -> int:
    raw = os.getenv("WORKFLOW_PORT", "").strip()
    if not raw:
        return default
    try:
        port = int(raw)
    except ValueError as exc:
        raise RuntimeError("WORKFLOW_PORT must be an integer from 1 through 65535.") from exc
    if not 1 <= port <= 65535:
        raise RuntimeError("WORKFLOW_PORT must be an integer from 1 through 65535.")
    return port


def _select_port(host: str, preferred: int, *, attempts: int = 10) -> int:
    """Select the preferred local port or the next bounded free port without terminating holders."""
    upper = min(65535, preferred + max(1, attempts) - 1)
    for port in range(preferred, upper + 1):
        if _port_available(host, port):
            return port
    raise RuntimeError(f"No free local port was found in the bounded range {preferred}-{upper}.")


def _system_exit_code(exc: SystemExit) -> int:
    """Preserve non-zero Uvicorn exits while treating an explicit zero signal unwind as normal."""
    code = exc.code
    if code is None:
        return 0
    if isinstance(code, int):
        return code
    try:
        return int(str(code))
    except (TypeError, ValueError):
        return 1


def _browser_enabled() -> bool:
    value = os.getenv("WORKFLOW_OPEN_BROWSER", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _open_browser_when_ready(url: str, health_url: str) -> None:
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(health_url, timeout=0.75) as response:
                if response.status == 200:
                    webbrowser.open(url, new=2)
                    return
        except Exception:
            time.sleep(0.25)




def _signal_exit(_signum, _frame) -> None:  # type: ignore[no-untyped-def]
    """Let Uvicorn finish graceful shutdown, then unwind through our cleanup finally block."""
    raise SystemExit(0)


def _install_cleanup_signal_handlers() -> dict[int, object]:
    originals: dict[int, object] = {}
    for name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue
        try:
            originals[int(sig)] = signal.getsignal(sig)
            signal.signal(sig, _signal_exit)
        except (OSError, ValueError):
            # Signal registration may be unavailable outside the main thread/platform.
            originals.pop(int(sig), None)
    return originals


def _restore_signal_handlers(originals: dict[int, object]) -> None:
    for number, handler in originals.items():
        try:
            signal.signal(number, handler)
        except (OSError, ValueError):
            pass


def main() -> int:
    os.chdir(ROOT)
    settings = Settings()
    lock = ProjectInstanceLock(settings.state_dir / "server_instance.lock", build_id=BUILD_ID)
    try:
        lock.acquire()
    except InstanceAlreadyRunning as exc:
        print(f"INFO: {exc}")
        print("The existing local server was left unchanged.")
        return 3

    original_signals = _install_cleanup_signal_handlers()
    try:
        settings.port = _preferred_port(settings.port)
        preferred_port = settings.port
        selected_port = _select_port(settings.host, preferred_port)
        if selected_port != preferred_port:
            if os.getenv("WORKFLOW_AUTH_MODE", "demo").strip().lower() == "oidc":
                raise RuntimeError(
                    f"OIDC mode requires a stable registered redirect port, but local port {preferred_port} is in use. "
                    "Set matching WORKFLOW_PORT and WORKFLOW_OIDC_REDIRECT_URI values instead of using automatic fallback."
                )
            print(
                f"INFO: Preferred local port {preferred_port} is already in use; "
                f"using {selected_port} instead. The existing listener was left unchanged."
            )
            settings.port = selected_port
        base_url = f"http://{settings.host}:{settings.port}"
        print(f"INFO: Local URL: {base_url}")
        if _browser_enabled():
            threading.Thread(
                target=_open_browser_when_ready,
                args=(base_url, f"{base_url}/api/v1/ready"),
                name="workflow-browser-opener",
                daemon=True,
            ).start()
        try:
            uvicorn.run(
                "app.main:app",
                host=settings.host,
                port=settings.port,
                reload=False,
                access_log=True,
                log_level="info",
                workers=1,
                timeout_keep_alive=5,
            )
        except KeyboardInterrupt:
            return 0
        except SystemExit as exc:
            # Our cleanup signal handler raises SystemExit(0). Uvicorn may also raise
            # SystemExit(nonzero) for startup/bind failures; never misreport those as success.
            return _system_exit_code(exc)
        return 0
    finally:
        _restore_signal_handlers(original_signals)
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
