"""Bounded project-local crash capsules and Export20 diagnostics.

The collector is read-only with respect to application behavior. It performs no network,
Drive, Norton, repair, dependency installation, project rescan, or managed-file rehash.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import tempfile
import time
import traceback
from typing import Any
from uuid import uuid4
import zipfile

from app.evidence_status import classify_cached_evidence
from app.runtime_lock import pid_alive
from app.runtime_utils import utcnow
from app.version import APP_VERSION, BUILD_ID, DISPLAY_NAME, PACKAGE_ID


ROOT = Path(__file__).resolve().parents[1]
DIAGNOSTICS = ROOT / "diagnostics"
STATE_DIR = ROOT / "state"
LOGS_DIR = ROOT / "logs"
EXPORTS_DIR = DIAGNOSTICS / "exports"
CAPSULES_DIR = DIAGNOSTICS / "crash_capsules"
TEMP_DIR = DIAGNOSTICS / "temp"
STATE_FILE = STATE_DIR / "diagnostic_export_state.json"
LOCK_FILE = STATE_DIR / "diagnostic_export.lock"
COOLDOWN_SECONDS = 600
MAX_EXPORT_ENTRIES = 20
MAX_EXPORT_BYTES = 10 * 1024 * 1024
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_SECONDS = 5.0
RETENTION_COUNT = 10
RETENTION_DAYS = 30
RETENTION_BYTES = 100 * 1024 * 1024

_export_active = False

_SECRET_PATTERN = re.compile(
    r"(?i)\b(password|passwd|token|secret|api[_-]?key|authorization|cookie)\b\s*[:=]\s*([^\s,;]+)"
)
_EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_IPV4_PATTERN = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")
_WINDOWS_USER_PATTERN = re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\\s]+")


def _stamp(value: datetime | None = None) -> str:
    return (value or utcnow()).strftime("%Y%m%d_%H%M%S_UTC")


def _iso(value: datetime | None = None) -> str:
    return (value or utcnow()).isoformat().replace("+00:00", "Z")


def _ensure_dirs() -> None:
    for path in (EXPORTS_DIR, CAPSULES_DIR, TEMP_DIR, STATE_DIR, LOGS_DIR):
        path.mkdir(parents=True, exist_ok=True)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temp.write_text(text, encoding="utf-8")
    os.replace(temp, path)


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    _atomic_write_text(path, json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n")


def _read_json(path: Path, default: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else (default or {})
    except (OSError, ValueError, TypeError):
        return default or {}


def _redact(text: str) -> str:
    text = _SECRET_PATTERN.sub(lambda match: f"{match.group(1)}=<redacted>", text)
    text = _EMAIL_PATTERN.sub("<redacted-email>", text)
    text = _WINDOWS_USER_PATTERN.sub("<redacted-user-path>", text)
    text = _IPV4_PATTERN.sub("<redacted-ip>", text)
    return text


def _exception_summary(exc: BaseException | None) -> tuple[str | None, str | None, str | None]:
    if exc is None:
        return None, None, None
    exc_type = type(exc).__name__
    message = _redact(str(exc))[:1500]
    trace = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return exc_type, message, _redact(trace)[-12000:]


def _fingerprint(trigger: str, exc: BaseException | None, context: dict[str, Any] | None) -> str:
    exc_type, message, _ = _exception_summary(exc)
    stable_context = {
        key: value
        for key, value in (context or {}).items()
        if key in {"phase", "component", "exit_code", "integrity_result", "reason"}
    }
    material = json.dumps(
        {"trigger": trigger, "exception_type": exc_type, "message": message, "context": stable_context},
        sort_keys=True,
        default=str,
    )
    return sha256(material.encode("utf-8")).hexdigest()


def _acquire_lock() -> bool:
    _ensure_dirs()
    try:
        fd = os.open(LOCK_FILE, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"pid={os.getpid()}\ncreated={_iso()}\n")
        return True
    except FileExistsError:
        try:
            text = LOCK_FILE.read_text(encoding="utf-8", errors="replace")
            match = re.search(r"(?m)^pid=(\d+)$", text)
            owner_pid = int(match.group(1)) if match else 0
            age = time.time() - LOCK_FILE.stat().st_mtime
            if (owner_pid and not pid_alive(owner_pid)) or (not owner_pid and age > 600):
                LOCK_FILE.unlink(missing_ok=True)
                return _acquire_lock()
        except OSError:
            pass
        return False


def _release_lock() -> None:
    try:
        LOCK_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _runtime_status() -> dict[str, Any]:
    return {
        "application": DISPLAY_NAME,
        "package_id": PACKAGE_ID,
        "version": APP_VERSION,
        "build_id": BUILD_ID,
        "captured_at": _iso(),
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "operating_system": platform.system(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
        "process_id": os.getpid(),
        "root_relative": ".",
        "network_calls_performed": False,
        "repair_or_behavior_mutation_performed": False,
    }


def write_status_cache(status: dict[str, Any], state_dir: Path | None = None) -> None:
    """Persist a small precomputed status summary for future diagnostics.

    Custom/test runtimes pass their own project-local state directory so status evidence cannot
    leak across roots or make a support export describe a different database instance.
    """
    target_state_dir = state_dir or STATE_DIR
    target_state_dir.mkdir(parents=True, exist_ok=True)
    safe = {
        "application": DISPLAY_NAME,
        "version": APP_VERSION,
        "build_id": BUILD_ID,
        "updated_at": _iso(),
        **{
            key: status[key]
            for key in status
            if key in {
                "startup",
                "database",
                "workflow_count",
                "case_count",
                "schema_version",
                "sla_scheduler",
                "durable_worker",
                "database_backend",
                "search_mode",
                "outbox_startup_processed",
                "run_id",
                "last_progress",
            }
        },
    }
    _atomic_write_json(target_state_dir / "app_status.json", safe)


def _tail_logs() -> str:
    sections: list[str] = []
    for path in (
        LOGS_DIR / "launcher.log",
        LOGS_DIR / "launcher_bootstrap_failure.txt",
        LOGS_DIR / "application.log",
        LOGS_DIR / "bootstrap.log",
    ):
        if not path.exists() or not path.is_file():
            continue
        try:
            size = path.stat().st_size
            with path.open("rb") as handle:
                if size > 256 * 1024:
                    handle.seek(-256 * 1024, os.SEEK_END)
                data = handle.read(256 * 1024)
            text = data.decode("utf-8", errors="replace")
            lines = text.splitlines()[-400:]
            sections.append(f"===== {path.name} =====\n" + "\n".join(lines))
        except OSError as exc:
            sections.append(f"===== {path.name} unavailable: {type(exc).__name__} =====")
    return _redact("\n\n".join(sections))[:512 * 1024]


def _capsule_payload(
    *,
    trigger: str,
    severity: str,
    fingerprint: str,
    exc: BaseException | None,
    context: dict[str, Any] | None,
    suppression_count: int,
) -> dict[str, Any]:
    exc_type, message, trace = _exception_summary(exc)
    return {
        "capsule_schema": "gateway-critical-capsule-1",
        "application": DISPLAY_NAME,
        "package_id": PACKAGE_ID,
        "version": APP_VERSION,
        "build_id": BUILD_ID,
        "captured_at": _iso(),
        "trigger": trigger,
        "severity": severity,
        "fingerprint": fingerprint,
        "exception_type": exc_type,
        "exception_message": message,
        "traceback_tail": trace,
        "context": context or {},
        "last_progress": (context or {}).get("last_progress"),
        "suppression_count": suppression_count,
        "export_result": "not_attempted",
        "export_path": None,
        "export_sha256": None,
        "export_elapsed_seconds": None,
        "export_failure_reason": None,
    }


def _copy_redacted_text(source: Path, target: Path) -> bool:
    try:
        if source.stat().st_size > MAX_SOURCE_BYTES:
            return False
        text = source.read_text(encoding="utf-8", errors="replace")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(_redact(text), encoding="utf-8")
        return True
    except OSError:
        return False


def _stage_export(capsule: Path | None, summary: dict[str, Any], started: float) -> tuple[Path, list[str]]:
    _ensure_dirs()
    stage = Path(tempfile.mkdtemp(prefix="export20_", dir=TEMP_DIR))
    entries: list[str] = []

    def add_generated(name: str, content: str) -> None:
        if len(entries) >= MAX_EXPORT_ENTRIES:
            return
        path = stage / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_redact(content), encoding="utf-8")
        entries.append(name)

    cached_evidence = (
        "state/app_status.json",
        "state/last_integrity_report.json",
        "state/database_health.json",
        "state/recovery_doctor.json",
        "state/soak_fault_latest.json",
        "state/launcher_status.json",
        "state/last_launcher_failure.json",
    )
    evidence_index: dict[str, dict[str, Any]] = {}
    current_cached: list[str] = []
    for relative in cached_evidence:
        source = ROOT / relative
        receipt = _read_json(source) if source.is_file() else None
        classification = classify_cached_evidence(receipt)
        evidence_index[relative] = classification
        if classification.get("current"):
            current_cached.append(relative)

    summary_payload = {**summary, "cached_evidence": evidence_index}
    add_generated(
        "diagnostic_summary.json",
        json.dumps(summary_payload, indent=2, sort_keys=True, ensure_ascii=False, default=str),
    )
    add_generated("runtime_status.json", json.dumps(_runtime_status(), indent=2, sort_keys=True))
    log_tail = _tail_logs()
    if log_tail:
        add_generated("recent_log_tail.txt", log_tail)

    if capsule and capsule.exists() and len(entries) < MAX_EXPORT_ENTRIES:
        destination = stage / "critical_crash_capsule.json"
        if _copy_redacted_text(capsule, destination):
            entries.append(destination.name)

    # Current runtime/launcher evidence outranks reference documentation under the 20-item cap.
    # This guarantees a Start failure remains visible even when Doctor/health/soak receipts are all current.
    allowlist = [
        *current_cached,
        "VERSION.txt",
        "PACKAGE_METADATA.json",
        "MANIFEST.json",
        "README.md",
        "CHANGELOG.md",
        "KNOWN_GOOD_STATE.md",
        "BUILD_REPORT.md",
        "SBOM.json",
        "THIRD_PARTY_NOTICES.md",
        "docs/RECOVERY_AND_TRANSFER.md",
        "docs/SECURITY.md",
        "docs/ARCHITECTURE.md",
    ]
    for relative in allowlist:
        if time.monotonic() - started > MAX_TOTAL_SECONDS:
            break
        if len(entries) >= MAX_EXPORT_ENTRIES:
            break
        source = ROOT / relative
        if not source.exists() or not source.is_file():
            continue
        destination = stage / relative
        if _copy_redacted_text(source, destination):
            entries.append(relative)

    total = sum((stage / item).stat().st_size for item in entries if (stage / item).exists())
    if total > MAX_EXPORT_BYTES:
        raise RuntimeError(f"staged export exceeds {MAX_EXPORT_BYTES} bytes")
    return stage, entries

def _zip_stage(stage: Path, entries: list[str], label: str) -> tuple[Path, str]:
    stamp = _stamp()
    final = EXPORTS_DIR / f"workflow_case_management_{label}_EXPORT20_{stamp}.zip"
    temp_zip = TEMP_DIR / f".{final.name}.{uuid4().hex}.tmp"
    with zipfile.ZipFile(temp_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for relative in sorted(entries):
            source = stage / relative
            if source.exists() and source.is_file():
                archive.write(source, arcname=relative.replace("\\", "/"))
    with zipfile.ZipFile(temp_zip, "r") as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f"ZIP integrity test failed at {bad}")
        if len(archive.infolist()) > MAX_EXPORT_ENTRIES:
            raise RuntimeError("Export20 entry limit exceeded")
    os.replace(temp_zip, final)
    digest = sha256(final.read_bytes()).hexdigest().upper()
    _atomic_write_text(final.with_suffix(final.suffix + ".sha256.txt"), f"{digest}  {final.name}\n")
    return final, digest


def _apply_retention() -> None:
    try:
        now = utcnow()
        files = sorted(
            [path for path in EXPORTS_DIR.glob("workflow_case_management_*_EXPORT20_*.zip") if path.is_file()],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        total = 0
        for index, path in enumerate(files):
            stat = path.stat()
            age_days = (now - datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)).days
            total += stat.st_size
            remove = index >= RETENTION_COUNT or age_days > RETENTION_DAYS or total > RETENTION_BYTES
            if remove:
                path.unlink(missing_ok=True)
                path.with_suffix(path.suffix + ".sha256.txt").unlink(missing_ok=True)
    except OSError:
        pass


def _create_export(capsule: Path | None, summary: dict[str, Any], label: str) -> tuple[Path, str, float]:
    started = time.monotonic()
    stage: Path | None = None
    try:
        stage, entries = _stage_export(capsule, summary, started)
        final, digest = _zip_stage(stage, entries, label)
        _apply_retention()
        return final, digest, round(time.monotonic() - started, 3)
    finally:
        if stage:
            shutil.rmtree(stage, ignore_errors=True)


def capture_critical(
    trigger: str,
    exc: BaseException | None = None,
    *,
    context: dict[str, Any] | None = None,
    attempt_full_export: bool = True,
) -> dict[str, Any]:
    """Atomically capture a Critical capsule, then attempt one isolated Export20."""
    global _export_active
    _ensure_dirs()
    fingerprint = _fingerprint(trigger, exc, context)
    state = _read_json(STATE_FILE)
    now = utcnow()
    last_at_raw = state.get("last_at")
    last_at = None
    if isinstance(last_at_raw, str):
        try:
            last_at = datetime.fromisoformat(last_at_raw.replace("Z", "+00:00"))
        except ValueError:
            last_at = None
    if state.get("fingerprint") == fingerprint and last_at and (now - last_at).total_seconds() < COOLDOWN_SECONDS:
        state["suppression_count"] = int(state.get("suppression_count", 0)) + 1
        state["last_suppressed_at"] = _iso(now)
        _atomic_write_json(STATE_FILE, state)
        return {
            "result": "suppressed",
            "fingerprint": fingerprint,
            "suppression_count": state["suppression_count"],
        }

    if _export_active:
        return {"result": "suppressed_recursive", "fingerprint": fingerprint}
    if not _acquire_lock():
        state.update(
            {
                "fingerprint": fingerprint,
                "last_at": _iso(now),
                "suppression_count": int(state.get("suppression_count", 0)) + 1,
                "last_result": "exporter_lock_busy",
            }
        )
        _atomic_write_json(STATE_FILE, state)
        return {"result": "exporter_lock_busy", "fingerprint": fingerprint}

    _export_active = True
    capsule_path = CAPSULES_DIR / f"critical_capsule_{_stamp(now)}_{fingerprint[:10]}.json"
    payload = _capsule_payload(
        trigger=trigger,
        severity="Critical",
        fingerprint=fingerprint,
        exc=exc,
        context=context,
        suppression_count=0,
    )
    try:
        _atomic_write_json(capsule_path, payload)
        if attempt_full_export:
            try:
                summary = {
                    "trigger": trigger,
                    "severity": "Critical",
                    "fingerprint": fingerprint,
                    "version": APP_VERSION,
                    "build_id": BUILD_ID,
                    "suppression_count": 0,
                    "capsule": capsule_path.name,
                    "collector_policy": "read-only, redacted, bounded, project-local",
                }
                final, digest, elapsed = _create_export(capsule_path, summary, "CRITICAL")
                payload.update(
                    {
                        "export_result": "success",
                        "export_path": str(final.relative_to(ROOT)).replace("\\", "/"),
                        "export_sha256": digest,
                        "export_elapsed_seconds": elapsed,
                    }
                )
            except Exception as export_error:  # exporter failure must not recurse
                payload.update(
                    {
                        "export_result": "failed_capsule_preserved",
                        "export_failure_reason": _redact(f"{type(export_error).__name__}: {export_error}")[:1500],
                    }
                )
        _atomic_write_json(capsule_path, payload)
        state = {
            "fingerprint": fingerprint,
            "last_at": _iso(now),
            "suppression_count": 0,
            "last_result": payload["export_result"],
            "last_capsule": str(capsule_path.relative_to(ROOT)).replace("\\", "/"),
            "last_export": payload.get("export_path"),
            "last_export_sha256": payload.get("export_sha256"),
        }
        _atomic_write_json(STATE_FILE, state)
        return payload
    finally:
        _export_active = False
        _release_lock()


def create_manual_export() -> dict[str, Any]:
    """Create a read-only Export20 without classifying normal operation as an error."""
    global _export_active
    _ensure_dirs()
    if _export_active:
        return {"result": "exporter_busy"}
    if not _acquire_lock():
        return {"result": "exporter_lock_busy"}
    _export_active = True
    try:
        summary = {
            "trigger": "manual_support_export",
            "severity": "Informational",
            "version": APP_VERSION,
            "build_id": BUILD_ID,
            "captured_at": _iso(),
            "collector_policy": "read-only, redacted, bounded, project-local",
        }
        final, digest, elapsed = _create_export(None, summary, "MANUAL")
        return {
            "result": "success",
            "path": str(final.relative_to(ROOT)).replace("\\", "/"),
            "sha256": digest,
            "elapsed_seconds": elapsed,
        }
    finally:
        _export_active = False
        _release_lock()
