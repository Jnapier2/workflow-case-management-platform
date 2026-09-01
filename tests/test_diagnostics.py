"""Bounded diagnostic export tests.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

import json
from pathlib import Path
import zipfile

import app.diagnostics as diagnostics


def _redirect_diagnostics(monkeypatch, root: Path) -> None:
    diagnostics_root = root / "diagnostics"
    monkeypatch.setattr(diagnostics, "ROOT", root)
    monkeypatch.setattr(diagnostics, "DIAGNOSTICS", diagnostics_root)
    monkeypatch.setattr(diagnostics, "STATE_DIR", root / "state")
    monkeypatch.setattr(diagnostics, "LOGS_DIR", root / "logs")
    monkeypatch.setattr(diagnostics, "EXPORTS_DIR", diagnostics_root / "exports")
    monkeypatch.setattr(diagnostics, "CAPSULES_DIR", diagnostics_root / "crash_capsules")
    monkeypatch.setattr(diagnostics, "TEMP_DIR", diagnostics_root / "temp")
    monkeypatch.setattr(diagnostics, "STATE_FILE", root / "state" / "diagnostic_export_state.json")
    monkeypatch.setattr(diagnostics, "LOCK_FILE", root / "state" / "diagnostic_export.lock")
    monkeypatch.setattr(diagnostics, "_export_active", False)
    for relative in [
        "VERSION.txt",
        "PACKAGE_METADATA.json",
        "README.md",
        "CHANGELOG.md",
        "KNOWN_GOOD_STATE.md",
        "SBOM.json",
        "THIRD_PARTY_NOTICES.md",
    ]:
        source = Path(__file__).resolve().parents[1] / relative
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(source.read_bytes())


def test_manual_export_is_bounded_and_excludes_case_data(tmp_path, monkeypatch):
    _redirect_diagnostics(monkeypatch, tmp_path)
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "workflow_cases.db").write_bytes(b"case data must not export")
    (tmp_path / "uploads").mkdir()
    (tmp_path / "uploads" / "evidence.txt").write_text("evidence must not export", encoding="utf-8")

    result = diagnostics.create_manual_export()
    assert result["result"] == "success"
    export = tmp_path / result["path"]
    assert export.is_file()
    with zipfile.ZipFile(export) as archive:
        names = archive.namelist()
        assert len(names) <= 20
        assert archive.testzip() is None
        assert not any(name.startswith("data/") for name in names)
        assert not any(name.startswith("uploads/") for name in names)
        runtime = json.loads(archive.read("runtime_status.json"))
        assert runtime["network_calls_performed"] is False
        assert runtime["repair_or_behavior_mutation_performed"] is False


def test_critical_capsule_then_export_and_suppression(tmp_path, monkeypatch):
    _redirect_diagnostics(monkeypatch, tmp_path)
    error = RuntimeError("password=secret user@example.com 192.168.1.20")
    first = diagnostics.capture_critical(
        "uncaught_fatal_exception",
        error,
        context={"component": "test", "last_progress": "diagnostic test"},
    )
    assert first["export_result"] == "success"
    capsule = next((tmp_path / "diagnostics" / "crash_capsules").glob("critical_capsule_*.json"))
    text = capsule.read_text(encoding="utf-8")
    assert "secret" not in text
    assert "user@example.com" not in text
    assert "192.168.1.20" not in text

    second = diagnostics.capture_critical(
        "uncaught_fatal_exception",
        error,
        context={"component": "test", "last_progress": "diagnostic test"},
    )
    assert second["result"] == "suppressed"
    assert second["suppression_count"] == 1


def test_manual_export_recovers_only_dead_pid_lock(tmp_path, monkeypatch):
    _redirect_diagnostics(monkeypatch, tmp_path)
    diagnostics.STATE_DIR.mkdir(parents=True, exist_ok=True)
    diagnostics.LOCK_FILE.write_text("pid=99999999\ncreated=2026-01-01T00:00:00Z\n", encoding="utf-8")
    monkeypatch.setattr(diagnostics, "pid_alive", lambda pid: False)

    result = diagnostics.create_manual_export()
    assert result["result"] == "success"
    assert not diagnostics.LOCK_FILE.exists()


def test_manual_export_preserves_live_pid_lock(tmp_path, monkeypatch):
    _redirect_diagnostics(monkeypatch, tmp_path)
    diagnostics.STATE_DIR.mkdir(parents=True, exist_ok=True)
    diagnostics.LOCK_FILE.write_text("pid=12345\ncreated=2026-01-01T00:00:00Z\n", encoding="utf-8")
    monkeypatch.setattr(diagnostics, "pid_alive", lambda pid: True)

    result = diagnostics.create_manual_export()
    assert result["result"] == "exporter_lock_busy"
    assert diagnostics.LOCK_FILE.exists()


def test_status_cache_honors_explicit_runtime_state_directory(tmp_path):
    target = tmp_path / "isolated_state"
    diagnostics.write_status_cache(
        {"startup": "ready", "schema_version": 4, "case_count": 9},
        state_dir=target,
    )
    receipt = json.loads((target / "app_status.json").read_text(encoding="utf-8"))
    assert receipt["startup"] == "ready"
    assert receipt["schema_version"] == 4
    assert receipt["case_count"] == 9



def test_manual_export_excludes_legacy_unattributed_doctor_but_reports_provenance(tmp_path, monkeypatch):
    _redirect_diagnostics(monkeypatch, tmp_path)
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "recovery_doctor.json").write_text(
        json.dumps(
            {
                "checked_at": "2026-08-28T17:52:09Z",
                "result": "FAIL",
                "problems": ["old marker-only failure"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = diagnostics.create_manual_export()
    assert result["result"] == "success"
    with zipfile.ZipFile(tmp_path / result["path"]) as archive:
        names = archive.namelist()
        assert "state/recovery_doctor.json" not in names
        summary = json.loads(archive.read("diagnostic_summary.json"))
        evidence = summary["cached_evidence"]["state/recovery_doctor.json"]
        assert evidence["status"] == "legacy_unattributed"
        assert evidence["current"] is False
        assert evidence["source_result"] == "FAIL"


def test_manual_export_includes_current_build_doctor_receipt(tmp_path, monkeypatch):
    from app.version import APP_VERSION, BUILD_ID

    _redirect_diagnostics(monkeypatch, tmp_path)
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "recovery_doctor.json").write_text(
        json.dumps(
            {
                "application_version": APP_VERSION,
                "build_id": BUILD_ID,
                "checked_at": diagnostics._iso(),
                "result": "PASS",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = diagnostics.create_manual_export()
    assert result["result"] == "success"
    with zipfile.ZipFile(tmp_path / result["path"]) as archive:
        assert "state/recovery_doctor.json" in archive.namelist()
        summary = json.loads(archive.read("diagnostic_summary.json"))
        evidence = summary["cached_evidence"]["state/recovery_doctor.json"]
        assert evidence["status"] == "current"
        assert evidence["current"] is True
        assert evidence["source_result"] == "PASS"
