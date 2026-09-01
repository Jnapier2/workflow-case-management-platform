"""Canonical Windows action router for the Workflow & Case Management Platform.

This is the single orchestration source behind LAUNCH_WORKFLOW_PLATFORM.bat. It performs
release verification before dependency bootstrap or application imports, records every startup
phase with standard-library-only evidence, then routes each action to its one canonical backend.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys
from typing import NamedTuple


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from launcher_state import write_launcher_status


class Action(NamedTuple):
    id: str
    label: str
    backend: str
    bootstrap: bool
    risk: str
    advanced: bool = False


ACTION_REGISTRY: tuple[Action, ...] = (
    Action("start", "Start local platform", "scripts/run_server.py", True, "low"),
    Action("doctor", "Recovery Doctor", "scripts/doctor.py", True, "low"),
    Action("export", "Create redacted Support Export20", "scripts/export_diagnostics.py", False, "low"),
    Action("tests", "Run automated tests", "pytest", True, "low"),
    Action("soak", "Run isolated soak/fault qualification", "scripts/soak_fault_test.py", True, "low", True),
    Action("postgres-preflight", "PostgreSQL read-only preflight", "scripts/postgres_preflight.py", True, "low", True),
    Action("migrate", "Apply configured database migration", "scripts/migrate_database.py", True, "protected", True),
)

_ACTIONS = {action.id: action for action in ACTION_REGISTRY}


def _run(command: list[str], *, cwd: Path = ROOT) -> int:
    try:
        return subprocess.run(command, cwd=cwd, check=False).returncode
    except OSError as exc:
        print(f"ERROR: Could not start child process: {type(exc).__name__}: {exc}")
        return 127


def _verify(python: Path, *, diagnostic_on_failure: bool = True) -> int:
    command = [str(python), str(SCRIPTS / "verify_release.py")]
    if diagnostic_on_failure:
        command.append("--diagnostic-on-failure")
    return _run(command)


def _venv_python() -> Path:
    return ROOT / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _capture_launcher_failure(python: Path, trigger: str, exit_code: int) -> None:
    try:
        _run(
            [
                str(python),
                str(SCRIPTS / "capture_launcher_failure.py"),
                trigger,
                "--exit-code",
                str(exit_code),
            ]
        )
    except Exception:
        # Diagnostic capture must never hide the original launcher result.
        pass


def _bootstrap(base_python: Path, action_id: str) -> tuple[int, Path | None]:
    write_launcher_status(action=action_id, phase="dependency_bootstrap", result="RUNNING")
    rc = _run([str(base_python), str(SCRIPTS / "bootstrap.py")])
    python = _venv_python()
    if rc != 0 or not python.is_file():
        code = rc or 2
        write_launcher_status(
            action=action_id,
            phase="dependency_bootstrap",
            result="FAIL",
            exit_code=code,
            detail="Project-local dependency environment could not be prepared. See logs/bootstrap.log.",
        )
        _capture_launcher_failure(base_python, "startup_dependency_failure", code)
        return code, None
    write_launcher_status(
        action=action_id,
        phase="dependency_bootstrap",
        result="PASS",
        exit_code=0,
        runtime_python=str(python),
    )
    return 0, python


def _menu() -> str | None:
    print("=" * 60)
    print("Workflow & Case Management Platform")
    print("=" * 60)
    print("1  Start local platform")
    print("2  Recovery Doctor")
    print("3  Create redacted Support Export20")
    print("4  Run automated tests")
    print("5  Advanced maintenance")
    print("Q  Quit")
    choice = input("Choose [1]: ").strip().lower() or "1"
    direct = {"1": "start", "2": "doctor", "3": "export", "4": "tests"}
    if choice in direct:
        return direct[choice]
    if choice in {"q", "quit"}:
        return None
    if choice != "5":
        print("Unknown selection.")
        return "menu"

    print()
    print("Advanced maintenance")
    print("1  Isolated soak/fault qualification")
    print("2  PostgreSQL read-only preflight")
    print("3  Apply configured database migration")
    print("B  Back")
    advanced = input("Choose: ").strip().lower()
    mapping = {"1": "soak", "2": "postgres-preflight", "3": "migrate"}
    return mapping.get(advanced, "menu")


def _confirm_protected(action: Action) -> bool:
    if action.risk != "protected":
        return True
    return input("Action? [Y/N] ").strip().lower() == "y"


def _execute(action: Action, base_python: Path) -> int:
    write_launcher_status(action=action.id, phase="release_verification", result="RUNNING")
    verify_rc = _verify(base_python, diagnostic_on_failure=True)
    if verify_rc != 0:
        write_launcher_status(
            action=action.id,
            phase="release_verification",
            result="FAIL",
            exit_code=verify_rc,
            detail="Release verification failed before application startup.",
        )
        print("ERROR: Release verification failed. No application action was started.")
        return verify_rc
    write_launcher_status(action=action.id, phase="release_verification", result="PASS", exit_code=0)

    runtime_python = base_python
    if action.bootstrap:
        bootstrap_rc, prepared = _bootstrap(base_python, action.id)
        if bootstrap_rc != 0 or prepared is None:
            print("ERROR: Project-local dependency environment could not be prepared.")
            return bootstrap_rc or 2
        runtime_python = prepared

    if not _confirm_protected(action):
        write_launcher_status(action=action.id, phase="protected_confirmation", result="CANCELLED", exit_code=0)
        print("No changes were made.")
        return 0

    write_launcher_status(
        action=action.id,
        phase="backend_execution",
        result="RUNNING",
        runtime_python=str(runtime_python),
    )
    if action.backend == "pytest":
        rc = _run([str(runtime_python), "-m", "pytest", "-q"])
    else:
        backend = ROOT / action.backend
        command = [str(runtime_python), str(backend)]
        if action.id == "migrate":
            command.append("--apply")
        elif action.id == "soak":
            command.extend(["--cases", "120", "--workers", "8"])
        rc = _run(command)

    if action.id == "start" and rc == 3:
        write_launcher_status(
            action=action.id,
            phase="backend_execution",
            result="PASS",
            exit_code=0,
            detail="Existing local instance detected; no duplicate server started.",
            runtime_python=str(runtime_python),
        )
        print("An existing local instance is already running. No second server was started.")
        return 0

    if rc != 0:
        write_launcher_status(
            action=action.id,
            phase="backend_execution",
            result="FAIL",
            exit_code=rc,
            detail=f"{action.label} exited before successful completion.",
            runtime_python=str(runtime_python),
        )
        if action.id == "start":
            _capture_launcher_failure(runtime_python, "terminal_critical_exit", rc)
        return rc

    write_launcher_status(
        action=action.id,
        phase="completed",
        result="PASS",
        exit_code=0,
        detail=f"{action.label} completed successfully.",
        runtime_python=str(runtime_python),
    )
    return 0


def _interactive_loop(base_python: Path) -> int:
    """Keep a double-clicked console usable after maintenance actions or failures."""
    while True:
        try:
            action_id = _menu()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if action_id is None:
            return 0
        if action_id == "menu":
            print()
            continue
        action = _ACTIONS[action_id]
        print()
        print(f"Action: {action.label}")
        rc = _execute(action, base_python)
        print()
        if rc == 0:
            print(f"RESULT: {action.label} completed successfully.")
        else:
            print(f"RESULT: {action.label} failed with exit code {rc}.")
            print("Evidence was retained under project-local logs, state, and diagnostics when possible.")
        try:
            response = input("Press Enter to return to the menu, or Q to quit: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return rc
        print()
        if response in {"q", "quit"}:
            return rc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Canonical action router for the Workflow & Case Management Platform."
    )
    parser.add_argument(
        "action",
        nargs="?",
        choices=["menu", "verify", *sorted(_ACTIONS)],
        help="Action to run. Omit to open the compact interactive menu.",
    )
    args = parser.parse_args(argv)
    os.chdir(ROOT)
    base_python = Path(sys.executable).resolve()

    action_id = args.action
    if action_id in (None, "menu"):
        return _interactive_loop(base_python)
    if action_id == "verify":
        write_launcher_status(action="verify", phase="release_verification", result="RUNNING")
        rc = _verify(base_python, diagnostic_on_failure=True)
        write_launcher_status(
            action="verify",
            phase="completed" if rc == 0 else "release_verification",
            result="PASS" if rc == 0 else "FAIL",
            exit_code=rc,
        )
        return rc

    action = _ACTIONS[action_id]
    print(f"Action: {action.label}")
    return _execute(action, base_python)


def _guarded_main() -> int:
    try:
        return main()
    except Exception as exc:
        code = 2
        detail = f"Unhandled launcher error: {type(exc).__name__}: {exc}"
        write_launcher_status(action="launcher", phase="router_exception", result="FAIL", exit_code=code, detail=detail)
        print(f"ERROR: {detail}")
        print("A launcher failure receipt was retained under state/launcher_status.json.")
        try:
            _capture_launcher_failure(Path(sys.executable).resolve(), "terminal_critical_exit", code)
        except Exception:
            pass
        return code


if __name__ == "__main__":
    raise SystemExit(_guarded_main())
