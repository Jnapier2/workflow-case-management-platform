"""Guarded advisory Case Assist: deterministic local suggestions with explicit evidence."""
from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Approval, Attachment, CaseRecord
from app.services.common import as_utc, loads, utcnow
from app.services.relations import duplicate_suggestions
from app.services.workflow_engine import allowed_transitions, case_workflow_config, stage_definition


def build_case_assist(db: Session, case: CaseRecord) -> dict[str, Any]:
    config = case_workflow_config(case)
    fields = loads(case.field_values_json, {})
    stage = stage_definition(config, case.status)
    approvals = list(db.scalars(select(Approval).where(Approval.case_id == case.id, Approval.stage_key == case.status)))
    attachments = list(db.scalars(select(Attachment).where(Attachment.case_id == case.id)))
    duplicates = duplicate_suggestions(db, case, limit=3)
    suggestions: list[dict[str, Any]] = []
    now = utcnow()
    due = as_utc(case.stage_due_at)
    if due and case.completed_at is None and case.sla_paused_at is None:
        remaining = (due - now).total_seconds() / 3600
        if remaining <= 4:
            suggestions.append({"id": "sla-risk", "kind": "sla", "confidence": 0.96, "title": "Prioritize this case", "rationale": f"The current stage deadline is {round(remaining,1)} hours away.", "action": {"type": "set_priority", "value": "High" if case.priority in {"Low", "Medium"} else case.priority}})
    if not attachments and case.status not in {str(config.get("initial_stage")), "completed", "rejected", "closed"}:
        suggestions.append({"id": "evidence-gap", "kind": "evidence", "confidence": 0.78, "title": "Check for missing evidence", "rationale": "No evidence attachment is currently associated with this in-progress case.", "action": {"type": "none"}})
    if bool(fields.get("handles_personal_data")) and case.priority in {"Low", "Medium"}:
        suggestions.append({"id": "data-risk-priority", "kind": "risk", "confidence": 0.88, "title": "Consider High priority", "rationale": "The intake indicates personal/confidential data handling.", "action": {"type": "set_priority", "value": "High"}})
    try:
        spend = float(fields.get("annual_spend", 0) or 0)
    except (TypeError, ValueError):
        spend = 0
    if spend >= 250000 and "high_value" not in loads(case.tags_json, []):
        suggestions.append({"id": "high-value-tag", "kind": "classification", "confidence": 0.91, "title": "Tag as high value", "rationale": f"Estimated annual spend is {spend:,.0f}.", "action": {"type": "add_tag", "value": "high_value"}})
    if duplicates:
        top = duplicates[0]
        suggestions.append({"id": "possible-duplicate", "kind": "duplicate", "confidence": min(0.99, float(top["score"])), "title": f"Review possible duplicate {top['case'].reference}", "rationale": f"Requester/title similarity score {top['score']:.2f}.", "action": {"type": "link_duplicate", "reference": top["case"].reference}})
    pending = [a for a in approvals if a.status == "Pending"]
    transitions = allowed_transitions(config, case.status)
    if pending:
        next_action = f"Resolve {len(pending)} pending approval(s) before advancing."
    elif transitions:
        next_action = f"Configured next action: {transitions[0].get('label') or transitions[0].get('to')}."
    else:
        next_action = "No forward transition is currently configured."
    summary = [
        f"{case.reference} is in {stage.get('label', case.status)} at {case.priority} priority.",
        f"Assigned to {case.assignee.name if case.assignee else 'no owner'}; {len(attachments)} evidence file(s), {len(pending)} pending approval(s).",
        next_action,
    ]
    return {
        "provider": "local_guarded_advisory",
        "generated_at": now.isoformat(),
        "applied": False,
        "summary": summary,
        "next_action": next_action,
        "suggestions": suggestions,
        "safety": "Suggestions are advisory and require an authorized user action before any case change.",
    }
