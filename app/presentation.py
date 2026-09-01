"""Presentation helpers shared by HTML and API responses.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.models import CaseRecord
from app.services.common import as_utc, iso, loads, utcnow
from app.services.workflow_engine import case_workflow_config, stage_label


def case_to_dict(case: CaseRecord, *, include_fields: bool = True) -> dict[str, Any]:
    config = case_workflow_config(case)
    now = utcnow()
    stage_due = as_utc(case.stage_due_at)
    overall_due = as_utc(case.overall_due_at)
    value: dict[str, Any] = {
        "reference": case.reference,
        "workflow_key": case.workflow.key,
        "workflow_name": case.workflow.name,
        "workflow_version": case.workflow_version,
        "published_workflow_version": case.workflow.version,
        "title": case.title,
        "description": case.description,
        "requester_name": case.requester_name,
        "requester_email": case.requester_email,
        "status": case.status,
        "status_label": stage_label(config, case.status),
        "priority": case.priority,
        "assignee": (
            {"id": case.assignee.id, "name": case.assignee.name, "role": case.assignee.role}
            if case.assignee
            else None
        ),
        "overall_due_at": iso(case.overall_due_at),
        "stage_due_at": iso(case.stage_due_at),
        "stage_started_at": iso(case.stage_started_at),
        "created_at": iso(case.created_at),
        "updated_at": iso(case.updated_at),
        "completed_at": iso(case.completed_at),
        "closed_on_time": case.closed_on_time,
        "escalation_level": case.escalation_level,
        "tags": loads(case.tags_json, []),
        "sla_paused_at": iso(case.sla_paused_at),
        "sla_pause_reason": case.sla_pause_reason,
        "sla_warning_at": iso(case.sla_warning_at),
        "stage_overdue": bool(stage_due and case.completed_at is None and case.sla_paused_at is None and stage_due < now),
        "overall_overdue": bool(overall_due and case.completed_at is None and case.sla_paused_at is None and overall_due < now),
    }
    if include_fields:
        value["fields"] = loads(case.field_values_json, {})
    return value


def format_datetime(value: datetime | None) -> str:
    normalized = as_utc(value)
    if not normalized:
        return "—"
    return normalized.strftime("%b %d, %Y %I:%M %p UTC")


def relative_deadline(value: datetime | None, *, completed: bool = False) -> str:
    normalized = as_utc(value)
    if not normalized:
        return "No deadline"
    delta = normalized - utcnow()
    seconds = int(delta.total_seconds())
    if completed:
        return format_datetime(normalized)
    overdue = seconds < 0
    seconds = abs(seconds)
    days, remainder = divmod(seconds, 86400)
    hours = remainder // 3600
    if days:
        text = f"{days}d {hours}h"
    else:
        minutes = (remainder % 3600) // 60
        text = f"{hours}h {minutes}m"
    return f"Overdue by {text}" if overdue else f"Due in {text}"


def filesize(value: int) -> str:
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{value} B"
