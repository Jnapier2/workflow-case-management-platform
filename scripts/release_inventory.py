"""Authoritative managed-file inventory and release identity helpers.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "MANIFEST.json"
PACKAGE_METADATA_PATH = ROOT / "PACKAGE_METADATA.json"
VERSION_PATH = ROOT / "VERSION.txt"
VERSION_MODULE_PATH = ROOT / "app" / "version.py"

EXCLUDED_TOP_LEVEL = {
    ".git",
    ".github",
    ".idea",
    ".pytest_cache",
    ".venv",
    ".vscode",
    "backups",
    "data",
    "diagnostics",
    "logs",
    "reports",
    "state",
    "uploads",
}
EXCLUDED_NAMES = {
    ".DS_Store",
    "MANIFEST.json",
    "Thumbs.db",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".tmp"}


class ReleaseInventoryError(RuntimeError):
    pass


def iso_now() -> str:
    """UTC ISO timestamp for standard-library release-gate/build tooling."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def normalized_relative(path: Path) -> str:
    relative = path.relative_to(ROOT).as_posix()
    if relative.startswith("/") or relative.startswith("../") or "/../" in f"/{relative}/":
        raise ReleaseInventoryError(f"Unsafe managed path: {relative}")
    if "\\" in relative or "\x00" in relative:
        raise ReleaseInventoryError(f"Invalid managed path: {relative}")
    return relative


def is_managed_path(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    if not relative.parts:
        return False
    if relative.parts[0] in EXCLUDED_TOP_LEVEL:
        return False
    if any(part == "__pycache__" for part in relative.parts):
        return False
    if path.name in EXCLUDED_NAMES or path.suffix.lower() in EXCLUDED_SUFFIXES:
        return False
    return path.is_file()


def managed_paths() -> list[Path]:
    values: list[Path] = []
    seen_casefold: dict[str, str] = {}
    for path in ROOT.rglob("*"):
        if not is_managed_path(path):
            continue
        if path.is_symlink():
            raise ReleaseInventoryError(f"Managed symlink is not permitted: {normalized_relative(path)}")
        relative = normalized_relative(path)
        folded = relative.casefold()
        if folded in seen_casefold:
            raise ReleaseInventoryError(
                f"Case-insensitive path collision: {seen_casefold[folded]} and {relative}"
            )
        seen_casefold[folded] = relative
        values.append(path)
    return sorted(values, key=lambda item: normalized_relative(item).casefold())


def parse_version_txt() -> tuple[str, str]:
    lines = [line.strip() for line in VERSION_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
    if len(lines) < 2 or not lines[1].lower().startswith("build:"):
        raise ReleaseInventoryError("VERSION.txt must contain version and Build: lines.")
    return lines[0], lines[1].split(":", 1)[1].strip()


def parse_version_module() -> dict[str, str]:
    text = VERSION_MODULE_PATH.read_text(encoding="utf-8")
    names = ("APP_VERSION", "BUILD_ID", "DISPLAY_NAME", "PACKAGE_ID")
    values: dict[str, str] = {}
    for name in names:
        match = re.search(rf'^\s*{name}\s*=\s*["\']([^"\']+)["\']\s*$', text, re.MULTILINE)
        if not match:
            raise ReleaseInventoryError(f"Could not read {name} from app/version.py.")
        values[name] = match.group(1)
    return values


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ReleaseInventoryError(f"Expected JSON object: {path.name}")
    return value


def atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")
    temp.replace(path)


def build_entries(paths: Iterable[Path]) -> list[dict[str, Any]]:
    return [
        {
            "path": normalized_relative(path),
            "size": path.stat().st_size,
            "sha256": sha256_file(path),
        }
        for path in paths
    ]
