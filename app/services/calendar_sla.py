"""Business-calendar service-level calculations with deterministic local rules."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Any

from app.services.common import as_utc

UTC = timezone.utc


def _parse_hhmm(value: str, default: time) -> time:
    try:
        hour, minute = [int(x) for x in value.split(":", 1)]
        return time(hour, minute)
    except Exception:
        return default


def calendar_config(config: dict[str, Any]) -> dict[str, Any] | None:
    raw = config.get("business_calendar")
    if not isinstance(raw, dict):
        return None
    try:
        zone = ZoneInfo(str(raw.get("timezone", "UTC")))
    except ZoneInfoNotFoundError:
        zone = ZoneInfo("UTC")
    days = raw.get("business_days", [0, 1, 2, 3, 4])
    business_days = {int(x) for x in days if isinstance(x, int) and 0 <= int(x) <= 6}
    if not business_days:
        business_days = {0, 1, 2, 3, 4}
    start = _parse_hhmm(str(raw.get("start", "08:00")), time(8, 0))
    end = _parse_hhmm(str(raw.get("end", "17:00")), time(17, 0))
    if (end.hour, end.minute) <= (start.hour, start.minute):
        end = time(17, 0)
    holidays: set[date] = set()
    for item in raw.get("holidays", []):
        try:
            holidays.add(date.fromisoformat(str(item)))
        except ValueError:
            continue
    return {"zone": zone, "business_days": business_days, "start": start, "end": end, "holidays": holidays}


def _is_open_day(day: date, cal: dict[str, Any]) -> bool:
    return day.weekday() in cal["business_days"] and day not in cal["holidays"]


def _next_open(local_dt: datetime, cal: dict[str, Any]) -> datetime:
    zone = cal["zone"]
    day = local_dt.date()
    for offset in range(370):
        candidate_day = day + timedelta(days=offset)
        if not _is_open_day(candidate_day, cal):
            continue
        start_dt = datetime.combine(candidate_day, cal["start"], tzinfo=zone)
        end_dt = datetime.combine(candidate_day, cal["end"], tzinfo=zone)
        if offset == 0:
            if local_dt < start_dt:
                return start_dt
            if local_dt < end_dt:
                return local_dt
        else:
            return start_dt
    return local_dt


def add_service_hours(config: dict[str, Any], hours: float, start_at: datetime) -> datetime | None:
    if hours <= 0:
        return None
    cal = calendar_config(config)
    start_utc = as_utc(start_at) or start_at.replace(tzinfo=UTC)
    if cal is None:
        return start_utc + timedelta(hours=float(hours))
    local = _next_open(start_utc.astimezone(cal["zone"]), cal)
    remaining = timedelta(hours=float(hours))
    while remaining.total_seconds() > 0:
        if not _is_open_day(local.date(), cal):
            local = _next_open(local + timedelta(days=1), cal)
            continue
        end_dt = datetime.combine(local.date(), cal["end"], tzinfo=cal["zone"])
        available = end_dt - local
        if remaining <= available:
            return (local + remaining).astimezone(UTC)
        remaining -= max(available, timedelta())
        local = _next_open(end_dt + timedelta(seconds=1), cal)
    return local.astimezone(UTC)


def due_for_stage(workflow_config: dict[str, Any], stage: dict[str, Any], start_at: datetime) -> datetime | None:
    return add_service_hours(workflow_config, float(stage.get("sla_hours", 0) or 0), start_at)


def due_for_workflow(workflow_config: dict[str, Any], start_at: datetime) -> datetime | None:
    return add_service_hours(workflow_config, float(workflow_config.get("total_sla_hours", 0) or 0), start_at)


def warning_window_hours(config: dict[str, Any], stage: dict[str, Any]) -> float:
    value = stage.get("warning_hours", config.get("sla_warning_hours", 4))
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 4.0
