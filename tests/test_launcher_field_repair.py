"""Windows launcher/export field-repair regressions.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import zipfile

import app.diagnostics as diagnostics


ROOT = Path(__file__).resolve().parents[1]


def _load_launcher_state():
    script = ROOT / "scripts" / "launcher_state.py"
    spec = importlib.util.spec_from_file_location("launcher_state_repair_test", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_launcher_state_is_stdlib_only_and_writes_phase_receipt(tmp_path, monkeypatch):
    module = _load_launcher_state()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(module, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(module, "STATUS_PATH", tmp_path / "state" / "launcher_status.json")
    monkeypatch.setattr(module, "LAST_FAILURE_PATH", tmp_path / "state" / "last_launcher_failure.json")
    monkeypatch.setattr(module, "LOG_PATH", tmp_path / "logs" / "launcher.log")
    (tmp_path / "VERSION.txt").write_text("0.5.2\nBuild: WCM-B009\n", encoding="utf-8")

    receipt = module.write_launcher_status(
        action="start",
        phase="dependency_bootstrap",
        result="FAIL",
        exit_code=2,
        detail=r"password=bad C:\Users\Example\project",
    )
    assert receipt["version"] == "0.5.2"
    assert receipt["build_id"] == "WCM-B009"
    assert receipt["result"] == "FAIL"
    persisted = json.loads(module.STATUS_PATH.read_text(encoding="utf-8"))
    assert persisted["exit_code"] == 2
    assert "bad" not in persisted["detail"]
    assert "Example" not in persisted["detail"]
    assert module.LOG_PATH.is_file()
    assert json.loads(module.LAST_FAILURE_PATH.read_text(encoding="utf-8"))["phase"] == "dependency_bootstrap"


def test_router_child_process_oserror_returns_bounded_failure(monkeypatch):
    script = ROOT / "scripts" / "workflow_platform.py"
    spec = importlib.util.spec_from_file_location("workflow_platform_repair_test", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    def explode(*_args, **_kwargs):
        raise OSError("simulated child launch failure")

    monkeypatch.setattr(module.subprocess, "run", explode)
    assert module._run(["missing-child"]) == 127


def test_interactive_router_keeps_menu_after_maintenance_action(monkeypatch):
    script = ROOT / "scripts" / "workflow_platform.py"
    spec = importlib.util.spec_from_file_location("workflow_platform_interactive_test", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    selections = iter(["export", None])
    prompts = iter([""])
    called: list[str] = []
    monkeypatch.setattr(module, "_menu", lambda: next(selections))
    monkeypatch.setattr(module, "_execute", lambda action, _python: called.append(action.id) or 0)
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(prompts))
    assert module._interactive_loop(Path(sys.executable)) == 0
    assert called == ["export"]


def test_export_cli_reports_path_and_checksum(tmp_path, monkeypatch, capsys):
    script = ROOT / "scripts" / "export_diagnostics.py"
    spec = importlib.util.spec_from_file_location("export_diagnostics_repair_test", script)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(
        module,
        "create_manual_export",
        lambda: {"result": "success", "path": "diagnostics/exports/demo.zip", "sha256": "ABC123", "elapsed_seconds": 0.1},
    )
    assert module.main() == 0
    output = capsys.readouterr().out
    assert "Support Export20 created successfully." in output
    assert "Path: diagnostics/exports/demo.zip" in output
    assert "SHA-256: ABC123" in output


def test_manual_export_includes_current_launcher_status_and_launcher_log(tmp_path, monkeypatch):
    diagnostics_root = tmp_path / "diagnostics"
    monkeypatch.setattr(diagnostics, "ROOT", tmp_path)
    monkeypatch.setattr(diagnostics, "DIAGNOSTICS", diagnostics_root)
    monkeypatch.setattr(diagnostics, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(diagnostics, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(diagnostics, "EXPORTS_DIR", diagnostics_root / "exports")
    monkeypatch.setattr(diagnostics, "CAPSULES_DIR", diagnostics_root / "crash_capsules")
    monkeypatch.setattr(diagnostics, "TEMP_DIR", diagnostics_root / "temp")
    monkeypatch.setattr(diagnostics, "STATE_FILE", tmp_path / "state" / "diagnostic_export_state.json")
    monkeypatch.setattr(diagnostics, "LOCK_FILE", tmp_path / "state" / "diagnostic_export.lock")
    monkeypatch.setattr(diagnostics, "_export_active", False)

    for relative in ["VERSION.txt", "PACKAGE_METADATA.json", "README.md", "CHANGELOG.md", "KNOWN_GOOD_STATE.md", "SBOM.json", "THIRD_PARTY_NOTICES.md"]:
        source = ROOT / relative
        destination = tmp_path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())

    from app.version import APP_VERSION, BUILD_ID
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "logs").mkdir(parents=True, exist_ok=True)
    failure = {
        "version": APP_VERSION,
        "build_id": BUILD_ID,
        "updated_at": diagnostics._iso(),
        "result": "FAIL",
        "action": "start",
        "phase": "dependency_bootstrap",
        "exit_code": 2,
    }
    current = {
        "version": APP_VERSION,
        "build_id": BUILD_ID,
        "updated_at": diagnostics._iso(),
        "result": "PASS",
        "action": "export",
        "phase": "completed",
        "exit_code": 0,
    }
    (tmp_path / "state" / "launcher_status.json").write_text(json.dumps(current) + "\n", encoding="utf-8")
    (tmp_path / "state" / "last_launcher_failure.json").write_text(json.dumps(failure) + "\n", encoding="utf-8")
    (tmp_path / "logs" / "launcher.log").write_text("launcher phase evidence\n", encoding="utf-8")

    result = diagnostics.create_manual_export()
    assert result["result"] == "success"
    with zipfile.ZipFile(tmp_path / result["path"]) as archive:
        names = archive.namelist()
        assert "state/launcher_status.json" in names
        assert "state/last_launcher_failure.json" in names
        assert "recent_log_tail.txt" in names
        assert "launcher phase evidence" in archive.read("recent_log_tail.txt").decode("utf-8")
        summary = json.loads(archive.read("diagnostic_summary.json"))
        launcher = summary["cached_evidence"]["state/launcher_status.json"]
        failure = summary["cached_evidence"]["state/last_launcher_failure.json"]
        assert launcher["current"] is True
        assert launcher["source_result"] == "PASS"
        assert failure["current"] is True
        assert failure["source_result"] == "FAIL"


def test_export20_prioritizes_launcher_evidence_when_all_receipts_are_current(tmp_path, monkeypatch):
    diagnostics_root = tmp_path / "diagnostics"
    monkeypatch.setattr(diagnostics, "ROOT", tmp_path)
    monkeypatch.setattr(diagnostics, "DIAGNOSTICS", diagnostics_root)
    monkeypatch.setattr(diagnostics, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(diagnostics, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(diagnostics, "EXPORTS_DIR", diagnostics_root / "exports")
    monkeypatch.setattr(diagnostics, "CAPSULES_DIR", diagnostics_root / "crash_capsules")
    monkeypatch.setattr(diagnostics, "TEMP_DIR", diagnostics_root / "temp")
    monkeypatch.setattr(diagnostics, "STATE_FILE", tmp_path / "state" / "diagnostic_export_state.json")
    monkeypatch.setattr(diagnostics, "LOCK_FILE", tmp_path / "state" / "diagnostic_export.lock")
    monkeypatch.setattr(diagnostics, "_export_active", False)

    for relative in [
        "VERSION.txt", "PACKAGE_METADATA.json", "MANIFEST.json", "README.md", "CHANGELOG.md",
        "KNOWN_GOOD_STATE.md", "BUILD_REPORT.md", "SBOM.json", "THIRD_PARTY_NOTICES.md",
        "docs/ARCHITECTURE.md", "docs/SECURITY.md", "docs/RECOVERY_AND_TRANSFER.md",
    ]:
        source = ROOT / relative
        if source.is_file():
            destination = tmp_path / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(source.read_bytes())

    from app.version import APP_VERSION, BUILD_ID
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    state.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    now = diagnostics._iso()
    files = {
        "app_status.json": {"version": APP_VERSION, "build_id": BUILD_ID, "updated_at": now, "result": "PASS"},
        "last_integrity_report.json": {"version": APP_VERSION, "build_id": BUILD_ID, "checked_at": now, "result": "PASS"},
        "database_health.json": {"version": APP_VERSION, "build_id": BUILD_ID, "checked_at": now, "result": "PASS"},
        "recovery_doctor.json": {"application_version": APP_VERSION, "build_id": BUILD_ID, "checked_at": now, "result": "PASS"},
        "soak_fault_latest.json": {"application_version": APP_VERSION, "build_id": BUILD_ID, "completed_at": now, "result": "PASS"},
        "launcher_status.json": {"version": APP_VERSION, "build_id": BUILD_ID, "updated_at": now, "result": "PASS", "action": "export"},
        "last_launcher_failure.json": {"version": APP_VERSION, "build_id": BUILD_ID, "updated_at": now, "result": "FAIL", "action": "start"},
    }
    for name, payload in files.items():
        (state / name).write_text(json.dumps(payload) + "\n", encoding="utf-8")
    (logs / "launcher.log").write_text("failure then export evidence\n", encoding="utf-8")

    result = diagnostics.create_manual_export()
    assert result["result"] == "success"
    with zipfile.ZipFile(tmp_path / result["path"]) as archive:
        names = archive.namelist()
        assert len(names) <= 20
        assert "state/launcher_status.json" in names
        assert "state/last_launcher_failure.json" in names
        assert "state/recovery_doctor.json" in names
        assert "state/soak_fault_latest.json" in names
