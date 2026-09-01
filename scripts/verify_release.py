"""Fail-closed release identity and managed-file verification.

The verifier uses only the Python standard library so it can run before dependency
installation and before any authenticated or consequential operation.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any
from uuid import uuid4
import zipfile

from release_inventory import (
    MANIFEST_PATH,
    PACKAGE_METADATA_PATH,
    ROOT,
    VERSION_PATH,
    atomic_write_json,
    iso_now,
    load_json,
    managed_paths,
    normalized_relative,
    parse_version_module,
    parse_version_txt,
    sha256_file,
)


def _safe_manifest_paths(manifest: dict[str, Any], errors: list[str]) -> dict[str, dict[str, Any]]:
    entries = manifest.get("files")
    if not isinstance(entries, list):
        errors.append("MANIFEST.json files must be a list.")
        return {}
    expected: dict[str, dict[str, Any]] = {}
    folded: dict[str, str] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            errors.append(f"Manifest entry {index} is not an object.")
            continue
        relative = entry.get("path")
        if not isinstance(relative, str) or not relative or relative.startswith(("/", "../")):
            errors.append(f"Manifest entry {index} has an unsafe path.")
            continue
        if "\\" in relative or "/../" in f"/{relative}/" or "\x00" in relative:
            errors.append(f"Manifest entry {index} has an invalid path: {relative!r}")
            continue
        key = relative.casefold()
        if key in folded:
            errors.append(f"Manifest has a case-insensitive path collision: {folded[key]} and {relative}")
            continue
        folded[key] = relative
        expected[relative] = entry
    return expected


def verify() -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    checked = 0
    try:
        manifest = load_json(MANIFEST_PATH)
        metadata = load_json(PACKAGE_METADATA_PATH)
        version, build_id = parse_version_txt()
        module = parse_version_module()
    except Exception as exc:
        return {
            "result": "FAIL",
            "checked_at": iso_now(),
            "checked_files": 0,
            "errors": [f"Release metadata could not be read: {type(exc).__name__}: {exc}"],
            "warnings": [],
        }

    identities = {
        "VERSION.txt version": version,
        "PACKAGE_METADATA version": metadata.get("version"),
        "MANIFEST version": manifest.get("version"),
        "running APP_VERSION": module.get("APP_VERSION"),
    }
    if len(set(identities.values())) != 1:
        errors.append("Version disagreement: " + json.dumps(identities, sort_keys=True))
    builds = {
        "VERSION.txt build": build_id,
        "PACKAGE_METADATA build": metadata.get("build_id"),
        "MANIFEST build": manifest.get("build_id"),
        "running BUILD_ID": module.get("BUILD_ID"),
    }
    if len(set(builds.values())) != 1:
        errors.append("Build disagreement: " + json.dumps(builds, sort_keys=True))
    package_ids = {
        "PACKAGE_METADATA package_id": metadata.get("package_id"),
        "MANIFEST package_id": manifest.get("package_id"),
        "running PACKAGE_ID": module.get("PACKAGE_ID"),
    }
    if len(set(package_ids.values())) != 1:
        errors.append("Package identity disagreement: " + json.dumps(package_ids, sort_keys=True))

    expected = _safe_manifest_paths(manifest, errors)
    declared_count = manifest.get("managed_file_count")
    metadata_count = metadata.get("managed_file_count")
    if declared_count != len(expected):
        errors.append(f"Manifest count is {declared_count!r}; parsed entry count is {len(expected)}.")
    if metadata_count != len(expected):
        errors.append(f"Package metadata count is {metadata_count!r}; expected {len(expected)}.")

    try:
        actual_paths = managed_paths()
    except Exception as exc:
        errors.append(f"Managed-file inventory failed: {type(exc).__name__}: {exc}")
        actual_paths = []
    actual = {normalized_relative(path): path for path in actual_paths}
    for relative in sorted(set(actual) - set(expected)):
        errors.append(f"Unexpected managed file: {relative}")
    for relative in sorted(set(expected) - set(actual)):
        errors.append(f"Missing managed file: {relative}")

    for relative in sorted(set(expected) & set(actual)):
        entry = expected[relative]
        path = actual[relative]
        try:
            size = path.stat().st_size
            digest = sha256_file(path)
            checked += 1
        except OSError as exc:
            errors.append(f"Could not read {relative}: {exc}")
            continue
        if entry.get("size") != size:
            errors.append(f"Size mismatch: {relative}")
        if str(entry.get("sha256", "")).upper() != digest:
            errors.append(f"SHA-256 mismatch: {relative}")

    entrypoint = metadata.get("primary_entrypoint")
    if not isinstance(entrypoint, str) or not (ROOT / entrypoint).is_file():
        errors.append("Primary entrypoint is missing or invalid.")

    # v2.17.13 active-launcher contract: one approved BAT/CMD in the active package,
    # no returned retired launcher, and no case-insensitive launcher collision.
    batch_paths = sorted(
        relative
        for relative in actual
        if Path(relative).suffix.lower() in {".bat", ".cmd"}
    )
    approved = metadata.get("approved_windows_launchers")
    if not isinstance(approved, list) or not all(isinstance(item, str) for item in approved):
        errors.append("approved_windows_launchers must be a list of root-relative strings.")
        approved = []
    batch_folded = [item.casefold() for item in batch_paths]
    approved_folded = [item.casefold() for item in approved]
    if len(batch_folded) != len(set(batch_folded)):
        errors.append("Active package contains a case-insensitive BAT/CMD collision.")
    if sorted(batch_folded) != sorted(approved_folded):
        errors.append(
            "Active BAT/CMD set does not match approved_windows_launchers: "
            + json.dumps({"active": batch_paths, "approved": approved}, sort_keys=True)
        )
    if isinstance(entrypoint, str) and entrypoint.casefold() not in approved_folded:
        errors.append("Primary entrypoint is not in approved_windows_launchers.")

    retired = metadata.get("retired_windows_launchers", [])
    if not isinstance(retired, list):
        errors.append("retired_windows_launchers must be a list.")
        retired = []
    retired_folded = {str(item).casefold() for item in retired}
    returned = sorted(item for item in batch_paths if item.casefold() in retired_folded)
    if returned:
        errors.append("Retired BAT/CMD returned to active package: " + ", ".join(returned))

    registry_source = metadata.get("action_registry_source")
    if not isinstance(registry_source, str) or not (ROOT / registry_source).is_file():
        errors.append("Canonical action registry source is missing or invalid.")

    return {
        "result": "PASS" if not errors else "FAIL",
        "checked_at": iso_now(),
        "version": version,
        "build_id": build_id,
        "package_id": module.get("PACKAGE_ID"),
        "checked_files": checked,
        "expected_files": len(expected),
        "errors": errors,
        "warnings": warnings,
    }


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def _integrity_failure_export(report: dict[str, Any]) -> dict[str, Any]:
    """Best-effort bounded capsule + Export20 for a failed release gate."""
    diagnostics = ROOT / "diagnostics"
    capsules = diagnostics / "crash_capsules"
    exports = diagnostics / "exports"
    temp_root = diagnostics / "temp"
    state = ROOT / "state"
    for folder in (capsules, exports, temp_root, state):
        folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_UTC")
    fingerprint = sha256(json.dumps(report, sort_keys=True).encode("utf-8")).hexdigest()
    capsule = capsules / f"critical_capsule_{stamp}_{fingerprint[:10]}.json"
    capsule_payload = {
        "capsule_schema": "gateway-critical-capsule-1",
        "captured_at": iso_now(),
        "trigger": "runtime_identity_failure",
        "severity": "Critical",
        "fingerprint": fingerprint,
        "integrity_result": report.get("result"),
        "errors": report.get("errors", [])[:50],
        "collector_policy": "read-only, bounded, project-local; no network, repair, install, or behavior mutation",
        "export_result": "not_attempted",
    }
    atomic_write_json(capsule, capsule_payload)

    stage = Path(tempfile.mkdtemp(prefix="integrity_export20_", dir=temp_root))
    try:
        allowlist = [
            ("critical_crash_capsule.json", capsule),
            ("last_integrity_report.json", state / "last_integrity_report.json"),
            ("VERSION.txt", VERSION_PATH),
            ("PACKAGE_METADATA.json", PACKAGE_METADATA_PATH),
            ("MANIFEST.json", MANIFEST_PATH),
            ("README.md", ROOT / "README.md"),
            ("CHANGELOG.md", ROOT / "CHANGELOG.md"),
            ("KNOWN_GOOD_STATE.md", ROOT / "KNOWN_GOOD_STATE.md"),
            ("BUILD_REPORT.md", ROOT / "BUILD_REPORT.md"),
            ("SBOM.json", ROOT / "SBOM.json"),
            ("THIRD_PARTY_NOTICES.md", ROOT / "THIRD_PARTY_NOTICES.md"),
        ]
        entries: list[str] = []
        for name, source in allowlist:
            if len(entries) >= 20 or not source.is_file() or source.stat().st_size > 2 * 1024 * 1024:
                continue
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            entries.append(name)
        final = exports / f"workflow_case_management_CRITICAL_EXPORT20_{stamp}.zip"
        temp_zip = temp_root / f".{final.name}.{uuid4().hex}.tmp"
        with zipfile.ZipFile(temp_zip, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name in sorted(entries):
                archive.write(stage / name, arcname=name)
        with zipfile.ZipFile(temp_zip, "r") as archive:
            bad = archive.testzip()
            if bad or len(archive.infolist()) > 20:
                raise RuntimeError(f"Export ZIP validation failed: {bad or 'entry_limit'}")
        os.replace(temp_zip, final)
        digest = sha256_file(final)
        _atomic_text(final.with_suffix(final.suffix + ".sha256.txt"), f"{digest}  {final.name}\n")
        capsule_payload.update(
            {
                "export_result": "success",
                "export_path": final.relative_to(ROOT).as_posix(),
                "export_sha256": digest,
            }
        )
    except Exception as exc:
        capsule_payload.update(
            {
                "export_result": "failed_capsule_preserved",
                "export_failure_reason": f"{type(exc).__name__}: {exc}"[:1500],
            }
        )
    finally:
        shutil.rmtree(stage, ignore_errors=True)
        atomic_write_json(capsule, capsule_payload)
    return capsule_payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--diagnostic-on-failure", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    report = verify()
    report_path = ROOT / "state" / "last_integrity_report.json"
    atomic_write_json(report_path, report)
    if not args.quiet:
        print(
            f"Release verification: {report['result']} | "
            f"{report.get('checked_files', 0)}/{report.get('expected_files', 0)} files | "
            f"version {report.get('version', 'unknown')} | build {report.get('build_id', 'unknown')}"
        )
        for error in report.get("errors", []):
            print(f"  ERROR: {error}")
        for warning in report.get("warnings", []):
            print(f"  WARNING: {warning}")
    if report["result"] != "PASS":
        if args.diagnostic_on_failure:
            result = _integrity_failure_export(report)
            if not args.quiet:
                print(f"Integrity diagnostic: {result.get('export_result')}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
