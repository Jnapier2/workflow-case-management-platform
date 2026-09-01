"""Operational workspaces: queues, bulk actions, delegation, relations, SLA controls, insights, and Case Assist."""
from __future__ import annotations

from datetime import datetime, timezone
import json

import httpx
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import CaseRecord, Delegation, IntegrationExecution, SavedQueue, User, WorkflowDefinition
from app.services.access import can
from app.services.assist import build_case_assist
from app.services.assist_remote import request_remote_advisory
from app.services.audit import append_activity
from app.services.common import dumps, loads, utcnow
from app.services.coordination import database_write_lock
from app.services.insights import build_process_intelligence
from app.services.queues import available_queues, create_delegation, queue_filters, save_queue, workload
from app.services.relations import RELATION_TYPES, create_relation
from app.services.workflow_engine import WorkflowError, assign_case, change_priority, change_tags, pause_sla, resume_sla, transition_case
from app.services.workflow_versions import migrate_case_snapshot, migration_preview
from app.web import _case_or_404, _current_actor, _flash, _render, _verify_csrf

router = APIRouter()


def _parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@router.get("/queues", name="queues")
def queues(request: Request, queue_id: int | None = None, db: Session = Depends(get_db)):
    actor = _current_actor(request, db)
    items = available_queues(db, actor)
    selected = next((item for item in items if item.id == queue_id), items[0] if items else None)
    filters = queue_filters(selected) if selected else {}
    query = select(CaseRecord).where(CaseRecord.completed_at.is_(None)).order_by(CaseRecord.updated_at.desc()).limit(250)
    if filters.get("priority"):
        query = query.where(CaseRecord.priority == filters["priority"])
    if filters.get("assignee") == "unassigned":
        query = query.where(CaseRecord.assignee_id.is_(None))
    if filters.get("assignee") == "me":
        query = query.where(CaseRecord.assignee_id == actor.id)
    cases = list(db.scalars(query))
    if filters.get("due") == "at_risk":
        threshold = utcnow().timestamp() + 4 * 3600
        cases = [c for c in cases if c.sla_paused_at is None and ((c.stage_due_at and c.stage_due_at.timestamp() <= threshold) or (c.overall_due_at and c.overall_due_at.timestamp() <= threshold))]
    users = list(db.scalars(select(User).where(User.active.is_(True)).order_by(User.role, User.name)))
    delegations = list(db.scalars(select(Delegation).where(Delegation.active.is_(True)).order_by(Delegation.ends_at)))
    return _render(request, db, "queues.html", queues=items, selected_queue=selected, queue_cases=cases, filters=filters, users=users, workloads={u.id: workload(db, u) for u in users}, delegations=delegations)


@router.post("/queues/save", name="queue_save")
async def queue_save(request: Request, db: Session = Depends(get_db)):
    actor = _current_actor(request, db)
    if not can(actor, "queue.manage"):
        raise HTTPException(status_code=403, detail="The selected role cannot create queues.")
    form = await request.form(); _verify_csrf(request, str(form.get("csrf_token", "")))
    filters = {key: str(form.get(key, "")) for key in ("priority", "assignee", "due") if str(form.get(key, ""))}
    try:
        with database_write_lock():
            item = save_queue(db, actor, str(form.get("name", "")), filters, shared=str(form.get("shared", "")) == "on")
            db.commit()
        _flash(request, f"Queue {item.name} saved.")
    except ValueError as exc:
        db.rollback(); _flash(request, str(exc), "danger")
    return RedirectResponse("/queues", status_code=303)


@router.post("/delegations", name="delegation_create")
async def delegation_create(request: Request, db: Session = Depends(get_db)):
    actor = _current_actor(request, db)
    if not can(actor, "queue.manage") and actor.role != "Administrator":
        raise HTTPException(status_code=403, detail="The selected role cannot manage delegation.")
    form = await request.form(); _verify_csrf(request, str(form.get("csrf_token", "")))
    try:
        from_user = db.get(User, int(str(form.get("from_user_id", actor.id))))
        to_user = db.get(User, int(str(form.get("to_user_id", "0"))))
        if not from_user or not to_user: raise ValueError("Select valid users.")
        if actor.role != "Administrator" and from_user.id != actor.id: raise ValueError("Only Administrators can delegate another user's work.")
        with database_write_lock():
            create_delegation(db, from_user, to_user, _parse_iso(str(form.get("starts_at", ""))), _parse_iso(str(form.get("ends_at", ""))), str(form.get("reason", "")))
            db.commit()
        _flash(request, "Delegation window saved.")
    except (ValueError, TypeError) as exc:
        db.rollback(); _flash(request, f"Delegation not saved: {exc}", "danger")
    return RedirectResponse("/queues", status_code=303)


@router.post("/cases/bulk", name="cases_bulk")
async def cases_bulk(request: Request, db: Session = Depends(get_db)):
    actor = _current_actor(request, db)
    if not can(actor, "case.bulk"):
        raise HTTPException(status_code=403, detail="The selected role cannot perform bulk case actions.")
    form = await request.form(); _verify_csrf(request, str(form.get("csrf_token", "")))
    references = [str(x).upper() for x in form.getlist("references")][:100]
    action = str(form.get("action", ""))
    if not references:
        _flash(request, "Select at least one case.", "danger"); return RedirectResponse("/queues", status_code=303)
    try:
        with database_write_lock():
            cases = list(db.scalars(select(CaseRecord).where(CaseRecord.reference.in_(references))))
            if len(cases) != len(set(references)): raise ValueError("One or more selected cases no longer exist.")
            for case in cases:
                if action == "priority": change_priority(db, case, actor, str(form.get("value", "Medium")))
                elif action == "assign": assign_case(db, case, actor, db.get(User, int(str(form.get("value", "0")))) if str(form.get("value", "0")) != "0" else None)
                elif action == "tag": change_tags(db, case, actor, list(dict.fromkeys(loads(case.tags_json, []) + [str(form.get("value", ""))])))
                elif action == "pause": pause_sla(db, case, actor, str(form.get("value", "Bulk work hold")))
                elif action == "resume": resume_sla(db, case, actor)
                elif action == "transition": transition_case(db, case, actor, str(form.get("value", "")), "Bulk transition")
                else: raise ValueError("Select a supported bulk action.")
            db.commit()
        _flash(request, f"Bulk action completed for {len(references)} case(s).")
    except (WorkflowError, ValueError) as exc:
        db.rollback(); _flash(request, f"Bulk action was rolled back: {exc}", "danger")
    return RedirectResponse("/queues", status_code=303)


@router.post("/cases/{reference}/relations", name="case_relation_create")
async def case_relation_create(reference: str, request: Request, db: Session = Depends(get_db)):
    actor = _current_actor(request, db)
    if not can(actor, "case.relate"):
        raise HTTPException(status_code=403, detail="The selected role cannot relate cases.")
    form = await request.form(); _verify_csrf(request, str(form.get("csrf_token", "")))
    case = _case_or_404(db, reference)
    target = db.scalar(select(CaseRecord).where(CaseRecord.reference == str(form.get("target_reference", "")).strip().upper()))
    try:
        if not target: raise ValueError("Target case was not found.")
        with database_write_lock():
            create_relation(db, case, target, str(form.get("relation_type", "related")), actor, str(form.get("note", "")))
            db.commit()
        _flash(request, f"Relationship to {target.reference} saved.")
    except ValueError as exc:
        db.rollback(); _flash(request, str(exc), "danger")
    return RedirectResponse(f"/cases/{reference}#relationships", status_code=303)


@router.post("/cases/{reference}/sla", name="case_sla_action")
async def case_sla_action(reference: str, request: Request, db: Session = Depends(get_db)):
    actor = _current_actor(request, db); form = await request.form(); _verify_csrf(request, str(form.get("csrf_token", "")))
    case = _case_or_404(db, reference)
    try:
        with database_write_lock():
            if str(form.get("action")) == "pause": pause_sla(db, case, actor, str(form.get("reason", "")))
            else: resume_sla(db, case, actor)
            db.commit()
        _flash(request, "SLA state updated.")
    except WorkflowError as exc:
        db.rollback(); _flash(request, str(exc), "danger")
    return RedirectResponse(f"/cases/{reference}", status_code=303)


@router.post("/cases/{reference}/migrate-workflow", name="case_workflow_migrate")
async def case_workflow_migrate(reference: str, request: Request, db: Session = Depends(get_db)):
    actor = _current_actor(request, db)
    if actor.role != "Administrator": raise HTTPException(status_code=403, detail="Workflow migration requires Administrator.")
    form = await request.form(); _verify_csrf(request, str(form.get("csrf_token", "")))
    case = _case_or_404(db, reference); workflow = db.get(WorkflowDefinition, case.workflow_id)
    try:
        if not workflow: raise ValueError("Workflow definition unavailable.")
        with database_write_lock(): migrate_case_snapshot(db, case, workflow, actor); db.commit()
        _flash(request, f"{case.reference} migrated to workflow v{workflow.version}.")
    except ValueError as exc:
        db.rollback(); _flash(request, f"Migration refused: {exc}", "danger")
    return RedirectResponse(f"/cases/{reference}", status_code=303)


@router.get("/insights", name="process_insights")
def process_insights(request: Request, workflow_id: int | None = None, db: Session = Depends(get_db)):
    _current_actor(request, db)
    workflows = list(db.scalars(select(WorkflowDefinition).order_by(WorkflowDefinition.name)))
    data = build_process_intelligence(db, workflow_id=workflow_id)
    return _render(request, db, "insights.html", insights=data, workflows=workflows, selected_workflow_id=workflow_id)


@router.get("/api/v1/process-intelligence")
def process_insights_api(request: Request, workflow_id: int | None = None, db: Session = Depends(get_db)):
    _current_actor(request, db)
    return build_process_intelligence(db, workflow_id=workflow_id)


@router.get("/api/v1/cases/{reference}/assist")
def case_assist_api(reference: str, request: Request, db: Session = Depends(get_db)):
    _current_actor(request, db)
    case = db.scalar(select(CaseRecord).where(CaseRecord.reference == reference.upper()))
    if not case: raise HTTPException(status_code=404, detail="Case not found.")
    return build_case_assist(db, case)


@router.post("/cases/{reference}/assist/external", name="case_assist_external")
async def case_assist_external(reference: str, request: Request, db: Session = Depends(get_db)):
    """Explicitly request a redacted external advisory; returned content can never mutate the case."""
    actor = _current_actor(request, db)
    if not can(actor, "assist.use"):
        raise HTTPException(status_code=403, detail="The selected role cannot use Case Assist.")
    form = await request.form()
    _verify_csrf(request, str(form.get("csrf_token", "")))
    case = _case_or_404(db, reference)
    try:
        result = request_remote_advisory(db, case)
        # Keep one bounded result only long enough to render the next page.
        request.session[f"external_assist:{case.reference}"] = result
        with database_write_lock():
            append_activity(
                db,
                case,
                actor,
                "external_assist_requested",
                "External Case Assist advisory requested",
                {
                    "provider": result.get("provider"),
                    "summary_items": len(result.get("summary", [])),
                    "suggestion_count": len(result.get("suggestions", [])),
                    "applied": False,
                    "data_scope": "redacted_operational_metadata_only",
                },
            )
            db.commit()
        _flash(request, "External Case Assist returned a redacted advisory. No action was applied.")
    except (RuntimeError, ValueError, httpx.HTTPError) as exc:
        db.rollback()
        _flash(request, f"External Case Assist unavailable: {exc}", "danger")
    return RedirectResponse(f"/cases/{reference}#assist", status_code=303)


@router.post("/cases/{reference}/assist", name="case_assist_action")
async def case_assist_action(reference: str, request: Request, db: Session = Depends(get_db)):
    actor = _current_actor(request, db)
    if not can(actor, "assist.use"): raise HTTPException(status_code=403, detail="The selected role cannot use Case Assist.")
    form = await request.form(); _verify_csrf(request, str(form.get("csrf_token", "")))
    case = _case_or_404(db, reference)
    suggestion_id = str(form.get("suggestion_id", "")); decision = str(form.get("decision", "reject"))
    assist = build_case_assist(db, case); suggestion = next((x for x in assist["suggestions"] if x["id"] == suggestion_id), None)
    if not suggestion:
        _flash(request, "Suggestion is no longer current.", "danger"); return RedirectResponse(f"/cases/{reference}#assist", status_code=303)
    try:
        with database_write_lock():
            if decision == "accept":
                action = suggestion.get("action", {})
                if action.get("type") == "set_priority": change_priority(db, case, actor, str(action.get("value")))
                elif action.get("type") == "add_tag": change_tags(db, case, actor, list(dict.fromkeys(loads(case.tags_json, []) + [str(action.get("value"))])))
                elif action.get("type") == "link_duplicate":
                    target = db.scalar(select(CaseRecord).where(CaseRecord.reference == str(action.get("reference"))))
                    if target: create_relation(db, case, target, "duplicate", actor, "Accepted Case Assist duplicate suggestion")
            append_activity(db, case, actor, "assist_feedback", f"Case Assist suggestion {decision}ed", {"suggestion_id": suggestion_id, "decision": decision, "confidence": suggestion.get("confidence"), "provider": assist.get("provider")})
            db.commit()
        _flash(request, f"Case Assist suggestion {decision}ed.")
    except (WorkflowError, ValueError) as exc:
        db.rollback(); _flash(request, str(exc), "danger")
    return RedirectResponse(f"/cases/{reference}#assist", status_code=303)
