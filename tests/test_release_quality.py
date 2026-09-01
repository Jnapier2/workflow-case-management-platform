"""Static release-quality and v2.17.13 consolidation assertions.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

import ast
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_LAUNCHER = "LAUNCH_WORKFLOW_PLATFORM.bat"
RETIRED_LAUNCHERS = {
    "DOCTOR_WORKFLOW_PLATFORM.bat",
    "EXPORT_DIAGNOSTICS.bat",
    "MIGRATE_DATABASE.bat",
    "POSTGRES_PREFLIGHT.bat",
    "RUN_SOAK_FAULT_TESTS.bat",
    "RUN_TESTS.bat",
    "scripts/SELECT_PYTHON.bat",
}


def _pins(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        assert "==" in line, f"Dependency is not exactly pinned: {line}"
        name, version = line.split("==", 1)
        distribution_name = name.strip().split("[", 1)[0]
        values[distribution_name.lower().replace("_", "-")] = version.strip()
    return values


def test_direct_dependencies_are_covered_by_full_lock():
    direct = _pins(ROOT / "requirements.txt")
    locked = _pins(ROOT / "requirements.lock.txt")
    assert direct
    assert set(direct).issubset(locked)
    for name, version in direct.items():
        assert locked[name] == version
    assert len(locked) >= 25


def test_exactly_one_active_windows_launcher_case_insensitively():
    batch_files = sorted(
        path.relative_to(ROOT).as_posix()
        for path in ROOT.rglob("*")
        if path.is_file() and path.suffix.lower() in {".bat", ".cmd"}
    )
    assert batch_files == [CANONICAL_LAUNCHER]
    assert len({value.casefold() for value in batch_files}) == 1
    assert not any((ROOT / retired).exists() for retired in RETIRED_LAUNCHERS)


def test_canonical_launcher_is_root_derived_and_routes_once():
    text = (ROOT / CANONICAL_LAUNCHER).read_text(encoding="utf-8")
    assert 'set "ROOT=%~dp0"' in text
    assert "scripts\\workflow_platform.py" in text
    assert "scripts\\SELECT_PYTHON.bat" not in text
    assert text.count('"%GATEWAY_PYTHON%" "%ROOT%scripts\\workflow_platform.py" %*') == 1
    assert 'if not exist "%ROOT%scripts\\workflow_platform.py" goto :IncompletePackage' in text
    assert 'set "PYTHONNOUSERSITE=1"' in text
    assert 'set "PYTHONUTF8=1"' in text


def test_action_registry_has_one_backend_per_capability():
    script = ROOT / "scripts" / "workflow_platform.py"
    spec = importlib.util.spec_from_file_location("workflow_platform", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    actions = list(module.ACTION_REGISTRY)
    ids = [action.id for action in actions]
    assert ids == [
        "start",
        "doctor",
        "export",
        "tests",
        "soak",
        "postgres-preflight",
        "migrate",
    ]
    assert len(ids) == len(set(ids))
    backends = [action.backend for action in actions if action.backend != "pytest"]
    assert len(backends) == len(set(backends))
    for backend in backends:
        assert (ROOT / backend).is_file(), backend


def test_current_runbooks_reference_only_canonical_bat():
    paths = [ROOT / "README.md", *sorted((ROOT / "docs").glob("*.md"))]
    for path in paths:
        text = path.read_text(encoding="utf-8")
        for retired in RETIRED_LAUNCHERS:
            assert retired not in text, f"stale active-runbook reference in {path.name}: {retired}"


def test_release_metadata_records_parameter_source_and_launcher_contract():
    metadata = json.loads((ROOT / "PACKAGE_METADATA.json").read_text(encoding="utf-8"))
    assert metadata["source_parameter_version"] == "2.17.13"
    assert metadata["source_parameter_sha256"] == (
        "63BDA0B5F61BA44F18F55C5B75512085ED3A2FE67C575E3406A5877ECD5F4566"
    )
    assert metadata["primary_entrypoint"] == CANONICAL_LAUNCHER
    assert metadata["approved_windows_launchers"] == [CANONICAL_LAUNCHER]
    assert metadata["approved_launcher_aliases"] == []
    assert set(metadata["retired_windows_launchers"]) == RETIRED_LAUNCHERS
    assert metadata["action_registry_source"] == "scripts/workflow_platform.py"
    assert metadata["active_action_ids"] == [
        "start",
        "doctor",
        "export",
        "tests",
        "soak",
        "postgres-preflight",
        "migrate",
        "verify",
    ]


def test_direct_script_entrypoints_resolve_project_imports_from_other_cwd(tmp_path):
    for relative in (
        "scripts/workflow_platform.py",
        "scripts/run_server.py",
        "scripts/export_diagnostics.py",
        "scripts/capture_launcher_failure.py",
    ):
        script = ROOT / relative
        command = (
            "import runpy; "
            f"runpy.run_path({str(script)!r}, run_name='entrypoint_import_check')"
        )
        result = subprocess.run(
            [sys.executable, "-c", command],
            cwd=tmp_path,
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, f"{relative}: {result.stdout}{result.stderr}"


def test_clean_release_zip_inventory_excludes_mutable_runtime_residue():
    script = ROOT / "scripts" / "build_release_zip.py"
    spec = importlib.util.spec_from_file_location("build_release_zip", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    members = {path.relative_to(ROOT).as_posix() for path in module.release_member_paths()}
    assert "state/.gitkeep" in members
    assert "logs/.gitkeep" in members
    assert "MANIFEST.json" in members
    assert "state/last_integrity_report.json" not in members
    assert "state/session_secret.txt" not in members
    assert "logs/application.log" not in members
    assert not any("__pycache__" in member for member in members)
    assert not any(member.endswith(".pyc") for member in members)


def test_windows_batch_files_use_crlf_without_bom():
    batch_files = [ROOT / CANONICAL_LAUNCHER]
    for path in batch_files:
        data = path.read_bytes()
        assert not data.startswith(b"\xef\xbb\xbf"), path.name
        assert b"\r\n" in data, path.name
        assert data.replace(b"\r\n", b"").find(b"\n") == -1, path.name


def test_application_runtime_has_no_exact_duplicate_function_bodies():
    groups: dict[str, list[str]] = {}
    for path in sorted((ROOT / "app").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = ast.dump(ast.Module(body=node.body, type_ignores=[]), include_attributes=False)
            if len(body) < 120:
                continue
            digest = sha256(body.encode("utf-8")).hexdigest()
            groups.setdefault(digest, []).append(f"{path.relative_to(ROOT)}:{node.name}")
    duplicates = [members for members in groups.values() if len(members) > 1]
    assert duplicates == []


def test_export_retention_cleanup_is_not_duplicated():
    tree = ast.parse((ROOT / "app" / "diagnostics.py").read_text(encoding="utf-8"))
    target = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_create_export"
    )
    calls = [
        node
        for node in ast.walk(target)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_apply_retention"
    ]
    assert len(calls) == 1
