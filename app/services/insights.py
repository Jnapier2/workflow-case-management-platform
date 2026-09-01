"""Process-intelligence analytics derived from immutable case/activity evidence."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from statistics import median
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Activity, Approval, CaseRecord
from app.services.common import as_utc, loads, utcnow
from app.services.workflow_engine import case_workflow_config, stage_label


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    if len(values) == 1:
        return round(values[0], 2)
    pos = (len(values) - 1) * percentile
    lo = int(pos)
    hi = min(lo + 1, len(values) - 1)
    weight = pos - lo
    return round(values[lo] * (1 - weight) + values[hi] * weight, 2)


def build_process_intelligence(db: Session, *, workflow_id: int | None = None, case_limit: int = 2500) -> dict[str, Any]:
    query = select(CaseRecord).order_by(CaseRecord.created_at.desc()).limit(max(1, min(case_limit, 10000)))
    if workflow_id:
        query = query.where(CaseRecord.workflow_id == workflow_id)
    cases = list(db.scalars(query))
    if not cases:
        return {"case_count": 0, "variants": [], "stage_dwell": [], "opportunities": [], "cycle_hours": {"p50": None, "p90": None, "p95": None}}
    case_ids = [c.id for c in cases]
    activities = list(db.scalars(select(Activity).where(Activity.case_id.in_(case_ids)).order_by(Activity.case_id, Activity.id)))
    approvals = list(db.scalars(select(Approval).where(Approval.case_id.in_(case_ids))))
    by_case: dict[int, list[Activity]] = defaultdict(list)
    for item in activities:
        by_case[item.case_id].append(item)

    stage_dwell: dict[str, list[float]] = defaultdict(list)
    variants: Counter[str] = Counter()
    rework_cases = 0
    cycle_hours: list[float] = []
    manual_events = 0
    system_events = 0
    escalation_reasons: Counter[str] = Counter()

    for case in cases:
        events = by_case.get(case.id, [])
        config = case_workflow_config(case)
        path: list[str] = []
        initial = str(config.get("initial_stage", ""))
        if initial:
            path.append(initial)
        current_stage = initial
        stage_started = as_utc(case.created_at)
        seen: Counter[str] = Counter(path)
        for event in events:
            if event.actor_id is None:
                system_events += 1
            else:
                manual_events += 1
            if event.event_type == "sla_escalated":
                reason = str(loads(event.details_json, {}).get("reason", "unknown"))
                escalation_reasons[reason] += 1
            if event.event_type != "status_changed":
                continue
            details = loads(event.details_json, {})
            source = str(details.get("from", current_stage or ""))
            target = str(details.get("to", ""))
            event_time = as_utc(event.created_at)
            if source and stage_started and event_time:
                stage_dwell[stage_label(config, source)].append(max(0.0, (event_time - stage_started).total_seconds() / 3600))
            if target:
                path.append(target)
                seen[target] += 1
                current_stage = target
                stage_started = event_time
        if case.completed_at and case.created_at:
            cycle_hours.append(max(0.0, ((as_utc(case.completed_at) or case.completed_at) - (as_utc(case.created_at) or case.created_at)).total_seconds() / 3600))
        elif current_stage and stage_started:
            stage_dwell[stage_label(config, current_stage)].append(max(0.0, (utcnow() - stage_started).total_seconds() / 3600))
        if any(v > 1 for v in seen.values()):
            rework_cases += 1
        variants[" → ".join(path) if path else case.status] += 1

    approval_hours: list[float] = []
    for approval in approvals:
        if approval.decided_at and approval.requested_at:
            approval_hours.append(max(0.0, ((as_utc(approval.decided_at) or approval.decided_at) - (as_utc(approval.requested_at) or approval.requested_at)).total_seconds() / 3600))

    stage_rows = []
    for label, values in stage_dwell.items():
        stage_rows.append({"stage": label, "observations": len(values), "p50_hours": _percentile(values, 0.50), "p90_hours": _percentile(values, 0.90), "p95_hours": _percentile(values, 0.95), "average_hours": round(sum(values) / len(values), 2)})
    stage_rows.sort(key=lambda row: row["p90_hours"] or 0, reverse=True)
    variant_rows = [{"path": path, "count": count, "percent": round(count * 100 / len(cases), 1)} for path, count in variants.most_common(8)]
    total_events = manual_events + system_events
    opportunities: list[dict[str, Any]] = []
    if stage_rows:
        top = stage_rows[0]
        opportunities.append({"kind": "bottleneck", "title": f"Review {top['stage']} capacity", "detail": f"This stage has the highest p90 observed dwell time at {top['p90_hours']} hours across {top['observations']} observations."})
    if rework_cases:
        opportunities.append({"kind": "rework", "title": "Reduce repeat-stage loops", "detail": f"{rework_cases} of {len(cases)} analyzed cases revisited at least one stage. Review validation rules and required evidence at intake."})
    if escalation_reasons:
        reason, count = escalation_reasons.most_common(1)[0]
        opportunities.append({"kind": "sla", "title": "Target the leading SLA exception", "detail": f"{reason.replace('_', ' ')} is the most frequent escalation reason ({count} recorded events)."})
    return {
        "case_count": len(cases),
        "completed_count": sum(1 for c in cases if c.completed_at),
        "rework_cases": rework_cases,
        "rework_rate_percent": round(rework_cases * 100 / len(cases), 1),
        "variants": variant_rows,
        "stage_dwell": stage_rows,
        "cycle_hours": {"p50": _percentile(cycle_hours, 0.50), "p90": _percentile(cycle_hours, 0.90), "p95": _percentile(cycle_hours, 0.95)},
        "approval_hours": {"p50": _percentile(approval_hours, 0.50), "p90": _percentile(approval_hours, 0.90)},
        "manual_touches": manual_events,
        "system_events": system_events,
        "automation_rate_percent": round(system_events * 100 / total_events, 1) if total_events else 0.0,
        "escalation_reasons": [{"reason": key, "count": value} for key, value in escalation_reasons.most_common(8)],
        "opportunities": opportunities,
    }
