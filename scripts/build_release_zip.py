"""Build a deterministic clean source ZIP from managed files and approved empty runtime placeholders.

The builder intentionally excludes mutable runtime state, logs, databases, evidence, caches,
compiled Python, diagnostics, backups, and local secrets. It packages only release-managed files,
MANIFEST.json, and the explicit .gitkeep placeholders needed to preserve project-local folders.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from release_inventory import MANIFEST_PATH, managed_paths, normalized_relative


APPROVED_PLACEHOLDERS = (
    "backups/.gitkeep",
    "data/.gitkeep",
    "diagnostics/crash_capsules/.gitkeep",
    "diagnostics/exports/.gitkeep",
    "diagnostics/temp/.gitkeep",
    "logs/.gitkeep",
    "reports/.gitkeep",
    "state/.gitkeep",
    "uploads/.gitkeep",
)
FIXED_ZIP_TIME = (2026, 1, 1, 0, 0, 0)


def release_member_paths() -> list[Path]:
    values = list(managed_paths())
    values.append(MANIFEST_PATH)
    for relative in APPROVED_PLACEHOLDERS:
        path = ROOT / relative
        if not path.is_file():
            raise RuntimeError(f"Required release placeholder is missing: {relative}")
        values.append(path)
    unique: dict[str, Path] = {}
    for path in values:
        relative = path.relative_to(ROOT).as_posix()
        folded = relative.casefold()
        if folded in unique:
            raise RuntimeError(f"Duplicate/case-colliding release path: {relative}")
        if path.is_symlink():
            raise RuntimeError(f"Release symlink is not permitted: {relative}")
        unique[folded] = path
    return sorted(unique.values(), key=lambda item: item.relative_to(ROOT).as_posix().casefold())


def build(output: Path, folder_name: str) -> Path:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(f".{output.name}.tmp")
    temp.unlink(missing_ok=True)
    prefix = folder_name.strip().strip("/\\")
    if not prefix or "/" in prefix or "\\" in prefix or prefix in {".", ".."}:
        raise RuntimeError("folder_name must be one safe top-level folder name")

    with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in release_member_paths():
            relative = path.relative_to(ROOT).as_posix()
            arcname = f"{prefix}/{relative}"
            info = zipfile.ZipInfo(arcname, date_time=FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)

    with zipfile.ZipFile(temp, "r") as archive:
        if archive.testzip() is not None:
            raise RuntimeError("Release ZIP integrity test failed")
        names = archive.namelist()
        if len(names) != len(set(name.casefold() for name in names)):
            raise RuntimeError("Release ZIP contains duplicate/case-colliding paths")
        if not all(name.startswith(prefix + "/") for name in names):
            raise RuntimeError("Release ZIP contains an unexpected top-level path")

    temp.replace(output)
    print(output)
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--folder-name", required=True)
    args = parser.parse_args()
    try:
        build(args.output, args.folder_name)
        return 0
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
