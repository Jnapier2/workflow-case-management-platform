"""Cached support-evidence identity and freshness classification.

Cached receipts are useful only when their provenance is clear.  A prior-build or aged
Doctor/health/soak receipt must never make the current runtime look unhealthy.  This module is
the single authority used by Operations and Export20 when deciding whether cached evidence is
current enough to affect present-tense health.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.version import APP_VERSION, BUILD_ID


DEFAULT_MAX_AGE = timedelta(hours=24)
_TIMESTAMP_KEYS = ("completed_at", "checked_at", "updated_at", "generated_at", "captured_at")
_VERSION_KEYS = ("application_version", "version")
_BUILD_KEYS = ("build_id", "application_build_id")


def _first_text(value: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        raw = value.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return None


def _parse_timestamp(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
        parsed = datetime.fromisoformat(candidate)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def classify_cached_evidence(
    receipt: dict[str, Any] | None,
    *,
    now: datetime | None = None,
    max_age: timedelta = DEFAULT_MAX_AGE,
    current_version: str = APP_VERSION,
    current_build_id: str = BUILD_ID,
) -> dict[str, Any]:
    """Return a compact, non-mutating current/stale classification for a cached receipt."""
    observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    if not isinstance(receipt, dict) or not receipt:
        return {
            "status": "missing",
            "current": False,
            "reason": "no cached evidence is available",
            "source_result": None,
            "source_timestamp": None,
            "source_version": None,
            "source_build_id": None,
            "age_seconds": None,
        }

    source_version = _first_text(receipt, _VERSION_KEYS)
    source_build_id = _first_text(receipt, _BUILD_KEYS)
    source_timestamp_text = _first_text(receipt, _TIMESTAMP_KEYS)
    source_timestamp = _parse_timestamp(source_timestamp_text)
    source_result = receipt.get("result") if isinstance(receipt.get("result"), str) else None

    base = {
        "source_result": source_result,
        "source_timestamp": source_timestamp_text,
        "source_version": source_version,
        "source_build_id": source_build_id,
        "age_seconds": None,
    }

    if not source_version or not source_build_id:
        return {
            **base,
            "status": "legacy_unattributed",
            "current": False,
            "reason": "cached receipt does not identify the application version/build that produced it",
        }
    if source_version != current_version or source_build_id != current_build_id:
        return {
            **base,
            "status": "prior_build",
            "current": False,
            "reason": (
                f"cached receipt belongs to {source_version} / {source_build_id}; "
                f"current runtime is {current_version} / {current_build_id}"
            ),
        }
    if source_timestamp is None:
        return {
            **base,
            "status": "undated",
            "current": False,
            "reason": "cached receipt does not contain a parseable UTC timestamp",
        }

    age = observed_at - source_timestamp
    age_seconds = round(age.total_seconds(), 1)
    base["age_seconds"] = age_seconds
    if age < -timedelta(minutes=5):
        return {
            **base,
            "status": "future_timestamp",
            "current": False,
            "reason": "cached receipt timestamp is materially ahead of the current clock",
        }
    if age > max_age:
        return {
            **base,
            "status": "stale",
            "current": False,
            "reason": f"cached receipt is older than {int(max_age.total_seconds() // 3600)} hours",
        }
    return {
        **base,
        "status": "current",
        "current": True,
        "reason": "cached receipt matches the current build and freshness window",
    }
