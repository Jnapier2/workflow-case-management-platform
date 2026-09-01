"""Create and validate the project-local Python environment.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
from typing import Any
import venv

from release_inventory import sha256_file


ROOT = Path(__file__).resolve().parents[1]
VENV = ROOT / ".venv"
LOCK = ROOT / "requirements.lock.txt"
FALLBACK_REQUIREMENTS = ROOT / "requirements.txt"
STAMP = VENV / ".gateway_environment.json"
LOG = ROOT / "logs" / "bootstrap.log"
MAX_LOG_BYTES = 256 * 1024


def _log(message: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    print(message)
    if LOG.is_file() and LOG.stat().st_size > MAX_LOG_BYTES:
        backup = LOG.with_suffix(".log.1")
        backup.unlink(missing_ok=True)
        LOG.replace(backup)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(message + "\n")


def _venv_python() -> Path:
    return VENV / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def _requirements_path() -> Path:
    return LOCK if LOCK.is_file() else FALLBACK_REQUIREMENTS


def _read_pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-")):
            continue
        markerless = line.split(";", 1)[0].strip()
        if "==" not in markerless:
            raise RuntimeError(f"Dependency line is not exactly pinned: {line}")
        name, version = markerless.split("==", 1)
        distribution_name = name.strip().split("[", 1)[0]
        normalized = distribution_name.lower().replace("_", "-")
        if not normalized or not version.strip():
            raise RuntimeError(f"Invalid dependency pin: {line}")
        if normalized in pins and pins[normalized] != version.strip():
            raise RuntimeError(f"Conflicting dependency pins for {normalized}.")
        pins[normalized] = version.strip()
    if not pins:
        raise RuntimeError(f"No exact dependency pins were found in {path.name}.")
    return pins


def _installed_matches(python: Path, pins: dict[str, str]) -> bool:
    child = """
import importlib.metadata as metadata
import json
import sys

pins = json.loads(sys.argv[1])
bad = []
for name, expected in sorted(pins.items()):
    try:
        actual = metadata.version(name)
    except metadata.PackageNotFoundError:
        actual = None
    if actual != expected:
        bad.append({"package": name, "expected": expected, "actual": actual})
print(json.dumps(bad, sort_keys=True))
raise SystemExit(1 if bad else 0)
"""
    result = subprocess.run(
        [str(python), "-c", child, json.dumps(pins, sort_keys=True)],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        details = result.stdout.strip() or result.stderr.strip() or f"exit code {result.returncode}"
        _log(f"Environment package check requires repair: {details}")
        return False
    return True


def _create_environment() -> Path:
    if sys.version_info < (3, 10) or sys.version_info >= (3, 15):
        raise RuntimeError(
            f"Python 3.10 through 3.14 is required; found {sys.version_info.major}.{sys.version_info.minor}."
        )
    python = _venv_python()
    if not python.is_file():
        _log(f"Creating project-local virtual environment with {sys.executable}")
        venv.EnvBuilder(with_pip=True, clear=False, symlinks=False).create(VENV)
    if not python.is_file():
        raise RuntimeError("The project-local virtual environment did not create a Python executable.")
    return python


def _pip_check(python: Path) -> None:
    result = subprocess.run(
        [str(python), "-m", "pip", "check"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        details = result.stdout.strip() or result.stderr.strip() or f"exit code {result.returncode}"
        raise RuntimeError(f"Installed dependency compatibility check failed: {details}")


def ensure() -> Path:
    requirements = _requirements_path()
    if not requirements.is_file():
        raise RuntimeError("No requirements file is available.")
    python = _create_environment()
    digest = sha256_file(requirements)
    pins = _read_pins(requirements)
    stamp: dict[str, Any] = {}
    try:
        stamp = json.loads(STAMP.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        pass
    current = (
        stamp.get("requirements_sha256") == digest
        and stamp.get("python_major_minor") == f"{sys.version_info.major}.{sys.version_info.minor}"
        and _installed_matches(python, pins)
    )
    if not current:
        _log(f"Installing exact binary dependencies from {requirements.name}")
        command = [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--only-binary=:all:",
            "--requirement",
            str(requirements),
        ]
        result = subprocess.run(command, cwd=ROOT, check=False)
        if result.returncode != 0:
            raise RuntimeError(f"Dependency installation failed with exit code {result.returncode}.")
        if not _installed_matches(python, pins):
            raise RuntimeError("Installed dependency versions do not match the lock file.")
        _pip_check(python)
        STAMP.write_text(
            json.dumps(
                {
                    "requirements_file": requirements.name,
                    "requirements_sha256": digest,
                    "python_major_minor": f"{sys.version_info.major}.{sys.version_info.minor}",
                    "python_executable": str(python),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    else:
        _pip_check(python)
        _log("Project-local dependency environment is current.")
    print(python)
    return python


def main() -> int:
    try:
        ensure()
        return 0
    except Exception as exc:
        _log(f"ERROR: {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
