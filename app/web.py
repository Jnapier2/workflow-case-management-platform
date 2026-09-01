"""Server-rendered portfolio interface.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
import re
import secrets
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.config import ROOT
from app.database import get_db
from app.models import Activity, Approval, Attachment, CaseRecord, Comment, Notification, User, WorkflowDefinition
from app.presentation import case_to_dict, filesize, format_datetime, relative_deadline
from app.services.audit import append_activity, build_case_audit_export, verify_case_audit_chain
from app.services.coordination import database_write_lock
from app.services.common import dumps, loads, utcnow
from app.services.dashboard import build_dashboard
from app.services.operations import build_operational_health
from app.services.identity import oidc_enabled
from app.services.evidence_store import LocalEvidenceStore, save_upload
from app.services.workflow_engine import (
    PRIORITIES,
    PermissionDenied,
    TransitionBlocked,
    WorkflowError,
    WorkflowValidationError,
    allowed_transitions,
    assign_case,
    case_search_query,
    case_workflow_config,
    change_priority,
    create_case,
    decide_approval,
    import_workflow_definition,
    stage_definition,
    stage_label,
    transition_case,
    workflow_config,
)
from app.version import APP_VERSION, BUILD_ID, DISPLAY_NAME


router = APIRouter()
CASE_PAGE_SIZE = 50
templates = Jinja2Templates(directory=str(ROOT / "app" / "templates"))
templates.env.filters["datetime"] = format_datetime
templates.env.filters["deadline"] = relative_deadline
templates.env.filters["filesize"] = filesize
templates.env.filters["prettyjson"] = lambda value: json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)


def _csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not isinstance(token, str) or len(token) < 32:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def _verify_csrf(request: Request, supplied: str) -> None:
    expected = request.session.get("csrf_token")
    if not isinstance(expected, str) or not secrets.compare_digest(expected, supplied or ""):
        raise HTTPException(status_code=400, detail="The form session expired. Refresh the page and try again.")


def _flash(request: Request, message: str, kind: str = "success") -> None:
    request.session["flash"] = {"message": message[:500], "kind": kind}


def _safe_next(value: str | None, default: str = "/") -> str:
    if value and value.startswith("/") and not value.startswith("//"):
        return value
    return default


def _current_actor(request: Request, db: Session) -> User:
    if oidc_enabled():
        actor_id = request.session.get("oidc_user_id")
        actor = db.get(User, actor_id) if isinstance(actor_id, int) else None
        if actor is None or not actor.active:
            raise HTTPException(status_code=401, detail="Sign in through /auth/login to access the operations workspace.")
        return actor
    actor_id = request.session.get("actor_id")
    actor = db.get(User, actor_id) if isinstance(actor_id, int) else None
    if actor is None or not actor.active:
        actor = db.scalar(select(User).where(User.active.is_(True), User.role == "Administrator").order_by(User.id.asc()))
        actor = actor or db.scalar(select(User).where(User.active.is_(True)).order_by(User.id.asc()))
        if actor is None:
            raise HTTPException(status_code=503, detail="No active demo users are available.")
        request.session["actor_id"] = actor.id
    return actor


def _case_or_404(db: Session, reference: str) -> CaseRecord:
    case = db.scalar(
        select(CaseRecord)
        .where(CaseRecord.reference == reference.upper())
        .options(
            selectinload(CaseRecord.workflow),
            selectinload(CaseRecord.assignee),
            selectinload(CaseRecord.approvals).selectinload(Approval.decided_by),
            selectinload(CaseRecord.comments).selectinload(Comment.actor),
            selectinload(CaseRecord.attachments).selectinload(Attachment.actor),
            selectinload(CaseRecord.activities).selectinload(Activity.actor),
        )
    )
    if not case:
        raise HTTPException(status_code=404, detail="Case not found.")
    return case


def _render(
    request: Request,
    db: Session,
    template_name: str,
    *,
    status_code: int = 200,
    **context: Any,
):  # type: ignore[no-untyped-def]
    actor = _current_actor(request, db)
    users = list(db.scalars(select(User).where(User.active.is_(True)).order_by(User.role, User.name)))
    unread = db.scalar(
        select(func.count(Notification.id)).where(Notification.user_id == actor.id, Notification.is_read.is_(False))
    ) or 0
    base = {
        "request": request,
        "app_name": DISPLAY_NAME,
        "app_version": APP_VERSION,
        "build_id": BUILD_ID,
        "actor": actor,
        "demo_users": users,
        "csrf_token": _csrf_token(request),
        "unread_notifications": unread,
        "flash": request.session.pop("flash", None),
        "priorities": sorted(PRIORITIES, key=lambda item: ["Low", "Medium", "High", "Critical"].index(item)),
        "now": utcnow(),
        "oidc_enabled": oidc_enabled(),
    }
    base.update(context)
    return templates.TemplateResponse(request=request, name=template_name, context=base, status_code=status_code)


@router.get("/", name="dashboard")
def dashboard(request: Request, db: Session = Depends(get_db)):
    data = build_dashboard(db)
    return _render(request, db, "dashboard.html", dashboard=data)


@router.get("/operations", name="operations")
def operations(request: Request, db: Session = Depends(get_db)):
    data = build_operational_health(request, db)
    return _render(request, db, "operations.html", operations=data)


@router.post("/session/actor", name="switch_actor")
async def switch_actor(request: Request, db: Session = Depends(get_db)):
    if oidc_enabled():
        raise HTTPException(status_code=403, detail="Demo identity switching is disabled in OIDC mode.")
    form = await request.form()
    _verify_csrf(request, str(form.get("csrf_token", "")))
    try:
        actor_id = int(str(form.get("actor_id", "0")))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid demo actor.")
    actor = db.get(User, actor_id)
    if not actor or not actor.active:
        raise HTTPException(status_code=404, detail="Demo actor not found.")
    request.session["actor_id"] = actor.id
    _flash(request, f"Acting as {actor.name} — {actor.role}.", "info")
    return RedirectResponse(_safe_next(str(form.get("next", "/"))), status_code=303)


@router.get("/cases", name="case_list")
def case_list(
    request: Request,
    workflow: int | None = None,
    status: str | None = None,
    priority: str | None = None,
    assignee: int | None = None,
    q: str | None = None,
    page: int = 1,
    db: Session = Depends(get_db),
):
    base_query = case_search_query(
        db,
        workflow_id=workflow,
        status=status,
        priority=priority,
        assignee_id=assignee,
        search=q,
    )
    total = db.scalar(select(func.count()).select_from(base_query.order_by(None).subquery())) or 0
    total_pages = max(1, (total + CASE_PAGE_SIZE - 1) // CASE_PAGE_SIZE)
    page = max(1, min(page, total_pages))
    query = (
        base_query.options(selectinload(CaseRecord.workflow), selectinload(CaseRecord.assignee))
        .limit(CASE_PAGE_SIZE)
        .offset((page - 1) * CASE_PAGE_SIZE)
    )
    cases = list(db.scalars(query))
    workflows = list(db.scalars(select(WorkflowDefinition).where(WorkflowDefinition.active.is_(True)).order_by(WorkflowDefinition.name)))
    status_options: dict[str, str] = {}
    for definition in workflows:
        config = workflow_config(definition)
        for stage in config.get("stages", []):
            status_options.setdefault(stage["key"], stage["label"])

    filters = {"workflow": workflow, "status": status, "priority": priority, "assignee": assignee, "q": q or ""}
    url_values = {key: value for key, value in filters.items() if value not in {None, ""}}

    def page_url(target: int) -> str:
        values = {**url_values, "page": target}
        return "/cases?" + urlencode(values)

    return _render(
        request,
        db,
        "cases.html",
        cases=cases,
        workflows=workflows,
        status_options=sorted(status_options.items(), key=lambda item: item[1]),
        filters=filters,
        pagination={
            "page": page,
            "page_size": CASE_PAGE_SIZE,
            "total": total,
            "total_pages": total_pages,
            "start": 0 if total == 0 else (page - 1) * CASE_PAGE_SIZE + 1,
            "end": min(page * CASE_PAGE_SIZE, total),
            "previous_url": page_url(page - 1) if page > 1 else None,
            "next_url": page_url(page + 1) if page < total_pages else None,
        },
        case_to_dict=case_to_dict,
    )


@router.get("/cases/new", name="case_new")
def case_new(request: Request, workflow: str | None = None, db: Session = Depends(get_db)):
    workflows = list(db.scalars(select(WorkflowDefinition).where(WorkflowDefinition.active.is_(True)).order_by(WorkflowDefinition.name)))
    selected = None
    if workflow:
        selected = next((item for item in workflows if item.key == workflow), None)
    selected = selected or (workflows[0] if workflows else None)
    config = workflow_config(selected) if selected else None
    return _render(
        request,
        db,
        "case_new.html",
        workflows=workflows,
        selected_workflow=selected,
        workflow_config=config,
        form_values={},
        errors=[],
    )


@router.post("/cases", name="case_create")
async def case_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    _verify_csrf(request, str(form.get("csrf_token", "")))
    actor = _current_actor(request, db)
    workflow_key = str(form.get("workflow_key", ""))
    workflow = db.scalar(select(WorkflowDefinition).where(WorkflowDefinition.key == workflow_key, WorkflowDefinition.active.is_(True)))
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    field_values = {
        str(key)[7:]: value
        for key, value in form.multi_items()
        if str(key).startswith("field__")
    }
    values = {str(key): str(value) for key, value in form.items()}
    try:
        with database_write_lock():
            case = create_case(
                db,
                workflow,
                actor,
                title=str(form.get("title", "")),
                description=str(form.get("description", "")),
                requester_name=str(form.get("requester_name", "")),
                requester_email=str(form.get("requester_email", "")),
                priority=str(form.get("priority", "Medium")),
                field_values=field_values,
            )
            db.commit()
        _flash(request, f"{case.reference} was created and routed.")
        return RedirectResponse(f"/cases/{case.reference}", status_code=303)
    except WorkflowError as exc:
        db.rollback()
        workflows = list(db.scalars(select(WorkflowDefinition).where(WorkflowDefinition.active.is_(True)).order_by(WorkflowDefinition.name)))
        return _render(
            request,
            db,
            "case_new.html",
            workflows=workflows,
            selected_workflow=workflow,
            workflow_config=workflow_config(workflow),
            form_values=values,
            errors=[str(exc)],
            status_code=400,
        )


@router.get("/cases/{reference}", name="case_detail")
def case_detail(reference: str, request: Request, db: Session = Depends(get_db)):
    case = _case_or_404(db, reference)
    config = case_workflow_config(case)
    stage = stage_definition(config, case.status)
    actor = _current_actor(request, db)
    transitions = allowed_transitions(config, case.status)
    stage_keys = [item["key"] for item in config.get("stages", [])]
    current_stage_index = stage_keys.index(case.status) if case.status in stage_keys else 0
    audit_check = verify_case_audit_chain(db, case.id)
    from app.services.assist import build_case_assist
    from app.services.assist_remote import remote_assist_available
    from app.services.relations import duplicate_suggestions, relation_summary
    from app.services.workflow_versions import migration_preview
    relations = relation_summary(db, case)
    duplicates = duplicate_suggestions(db, case)
    assist = build_case_assist(db, case)
    migration = migration_preview(case, workflow_config(case.workflow)) if case.workflow else {"eligible": False, "reasons": ["Workflow unavailable"]}
    return _render(
        request,
        db,
        "case_detail.html",
        case=case,
        case_view=case_to_dict(case),
        config=config,
        current_stage=stage,
        current_stage_index=current_stage_index,
        transitions=transitions,
        actor_can_transition=lambda transition: actor.role == "Administrator" or actor.role in transition.get("roles", []),
        audit_check=audit_check,
        field_values=loads(case.field_values_json, {}),
        stage_label=lambda value: stage_label(config, value),
        relations=relations,
        duplicate_suggestions=duplicates,
        assist=assist,
        external_assist=request.session.pop(f"external_assist:{case.reference}", None),
        remote_assist_available=remote_assist_available(),
        workflow_migration=migration,
        requester_updates=case.requester_updates,
    )


@router.post("/cases/{reference}/transition", name="case_transition")
async def case_transition(reference: str, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    _verify_csrf(request, str(form.get("csrf_token", "")))
    case = _case_or_404(db, reference)
    actor = _current_actor(request, db)
    try:
        with database_write_lock():
            transition_case(db, case, actor, str(form.get("target_stage", "")), str(form.get("note", "")))
            db.commit()
        _flash(request, f"{case.reference} moved to {stage_label(case_workflow_config(case), case.status)}.")
    except WorkflowError as exc:
        db.rollback()
        _flash(request, str(exc), "danger")
    return RedirectResponse(f"/cases/{reference}", status_code=303)


@router.post("/cases/{reference}/assign", name="case_assign")
async def case_assign(reference: str, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    _verify_csrf(request, str(form.get("csrf_token", "")))
    case = _case_or_404(db, reference)
    actor = _current_actor(request, db)
    assignee_raw = str(form.get("assignee_id", ""))
    assignee = db.get(User, int(assignee_raw)) if assignee_raw.isdigit() else None
    try:
        with database_write_lock():
            assign_case(db, case, actor, assignee)
            db.commit()
        _flash(request, "Assignment updated.")
    except WorkflowError as exc:
        db.rollback()
        _flash(request, str(exc), "danger")
    return RedirectResponse(f"/cases/{reference}", status_code=303)


@router.post("/cases/{reference}/priority", name="case_priority")
async def case_priority(reference: str, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    _verify_csrf(request, str(form.get("csrf_token", "")))
    case = _case_or_404(db, reference)
    actor = _current_actor(request, db)
    try:
        with database_write_lock():
            change_priority(db, case, actor, str(form.get("priority", "")))
            db.commit()
        _flash(request, "Priority updated.")
    except WorkflowError as exc:
        db.rollback()
        _flash(request, str(exc), "danger")
    return RedirectResponse(f"/cases/{reference}", status_code=303)


@router.post("/cases/{reference}/comments", name="comment_add")
async def comment_add(reference: str, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    _verify_csrf(request, str(form.get("csrf_token", "")))
    case = _case_or_404(db, reference)
    actor = _current_actor(request, db)
    body = str(form.get("body", "")).strip()
    if not body or len(body) > 4000:
        _flash(request, "Comment must contain 1–4,000 characters.", "danger")
        return RedirectResponse(f"/cases/{reference}#comments", status_code=303)
    with database_write_lock():
        comment = Comment(case_id=case.id, actor_id=actor.id, body=body)
        db.add(comment)
        db.flush()
        append_activity(db, case, actor, "comment_added", "A comment was added", {"comment_id": comment.id})
        db.commit()
    _flash(request, "Comment added.")
    return RedirectResponse(f"/cases/{reference}#comments", status_code=303)


@router.post("/cases/{reference}/attachments", name="attachment_add")
async def attachment_add(
    reference: str,
    request: Request,
    csrf_token: str = Form(...),
    evidence: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    _verify_csrf(request, csrf_token)
    case = _case_or_404(db, reference)
    actor = _current_actor(request, db)
    original_name = Path(evidence.filename or "evidence.bin").name[:255]
    destination = None
    try:
        destination, receipt, stored_name = await save_upload(
            evidence, request.app.state.settings.uploads_dir, case.reference, original_name, request.app.state.settings.max_upload_bytes
        )
        with database_write_lock():
            attachment = Attachment(
                case_id=case.id, actor_id=actor.id, original_name=original_name, stored_name=stored_name,
                storage_backend=receipt.backend, storage_key=receipt.storage_key, integrity_status=receipt.integrity_status, scan_status=receipt.scan_status,
                content_type=(evidence.content_type or "application/octet-stream")[:160], size_bytes=receipt.size_bytes, sha256=receipt.sha256,
            )
            db.add(attachment); db.flush()
            append_activity(db, case, actor, "evidence_attached", f"Evidence attached: {original_name}", {"attachment_id": attachment.id, "size_bytes": receipt.size_bytes, "sha256": receipt.sha256, "storage_backend": receipt.backend, "scan_status": receipt.scan_status})
            db.commit()
    except ValueError as exc:
        db.rollback()
        if destination is not None: destination.unlink(missing_ok=True)
        _flash(request, str(exc), "danger")
        return RedirectResponse(f"/cases/{reference}#evidence", status_code=303)
    except Exception:
        db.rollback()
        if destination is not None: destination.unlink(missing_ok=True)
        raise
    finally:
        await evidence.close()
    _flash(request, "Evidence attached.")
    return RedirectResponse(f"/cases/{reference}#evidence", status_code=303)


@router.get("/attachments/{attachment_id}", name="attachment_download")
def attachment_download(attachment_id: int, request: Request, db: Session = Depends(get_db)):
    attachment = db.get(Attachment, attachment_id)
    if not attachment:
        raise HTTPException(status_code=404, detail="Attachment not found.")
    case = db.get(CaseRecord, attachment.case_id)
    if not case:
        raise HTTPException(status_code=404, detail="Case not found.")
    store = LocalEvidenceStore(request.app.state.settings.uploads_dir)
    storage_key = attachment.storage_key or f"{case.reference}/{attachment.stored_name}"
    try:
        path = store.resolve(storage_key)
    except ValueError:
        raise HTTPException(status_code=404, detail="Attachment storage key is invalid.")
    if not path.is_file() or not store.verify(storage_key, attachment.sha256):
        raise HTTPException(status_code=409, detail="Attachment integrity verification failed or the file is unavailable.")
    return FileResponse(path, media_type="application/octet-stream", filename=attachment.original_name)


@router.post("/approvals/{approval_id}/decision", name="approval_decision")
async def approval_decision(approval_id: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    _verify_csrf(request, str(form.get("csrf_token", "")))
    approval = db.scalar(select(Approval).where(Approval.id == approval_id).options(selectinload(Approval.case)))
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found.")
    actor = _current_actor(request, db)
    reference = approval.case.reference
    try:
        with database_write_lock():
            decide_approval(db, approval, actor, str(form.get("decision", "")), str(form.get("note", "")))
            db.commit()
        _flash(request, f"{approval.name} recorded as {approval.status}.")
    except WorkflowError as exc:
        db.rollback()
        _flash(request, str(exc), "danger")
    return RedirectResponse(f"/cases/{reference}#approvals", status_code=303)


@router.get("/cases/{reference}/audit.json", name="audit_json")
def audit_json(reference: str, db: Session = Depends(get_db)):
    case = _case_or_404(db, reference)
    payload = build_case_audit_export(db, case)
    return JSONResponse(
        payload,
        headers={"Content-Disposition": f'attachment; filename="{case.reference}_audit.json"'},
    )


@router.get("/cases/{reference}/audit.csv", name="audit_csv")
def audit_csv(reference: str, db: Session = Depends(get_db)):
    case = _case_or_404(db, reference)
    payload = build_case_audit_export(db, case)
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["activity_id", "created_at", "actor", "actor_role", "event_type", "summary", "details_json", "entry_hash"])
    for item in payload["activity"]:
        writer.writerow(
            [
                item["id"],
                item["created_at"],
                item["actor"],
                item["actor_role"],
                item["event_type"],
                item["summary"],
                dumps(item["details"]),
                item["entry_hash"],
            ]
        )
    return Response(
        output.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{case.reference}_activity.csv"'},
    )


@router.get("/workflows", name="workflow_list")
def workflow_list(request: Request, db: Session = Depends(get_db)):
    workflows = list(db.scalars(select(WorkflowDefinition).order_by(WorkflowDefinition.name)))
    return _render(request, db, "workflows.html", workflows=workflows)


@router.get("/workflows/{workflow_key}", name="workflow_detail")
def workflow_detail(workflow_key: str, request: Request, db: Session = Depends(get_db)):
    workflow = db.scalar(select(WorkflowDefinition).where(WorkflowDefinition.key == workflow_key))
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    config = workflow_config(workflow)
    return _render(request, db, "workflow_detail.html", workflow=workflow, config=config)


@router.post("/workflows/import", name="workflow_import")
async def workflow_import(
    request: Request,
    csrf_token: str = Form(...),
    config_text: str = Form(""),
    config_file: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    _verify_csrf(request, csrf_token)
    actor = _current_actor(request, db)
    if actor.role != "Administrator":
        _flash(request, "Workflow import requires the Administrator role.", "danger")
        return RedirectResponse("/workflows", status_code=303)
    raw = config_text.strip()
    if config_file and config_file.filename:
        data = await config_file.read(200_001)
        if len(data) > 200_000:
            _flash(request, "Workflow definition exceeds the 200 KB limit.", "danger")
            return RedirectResponse("/workflows", status_code=303)
        raw = data.decode("utf-8", errors="strict")
    try:
        config = json.loads(raw)
        with database_write_lock():
            workflow = import_workflow_definition(db, config)
            db.commit()
        _flash(request, f"Workflow {workflow.name} version {workflow.version} imported.")
        return RedirectResponse(f"/workflows/{workflow.key}", status_code=303)
    except (UnicodeDecodeError, json.JSONDecodeError, WorkflowValidationError) as exc:
        db.rollback()
        _flash(request, f"Workflow import failed: {exc}", "danger")
        return RedirectResponse("/workflows", status_code=303)


@router.get("/workflows/examples/{filename}", name="workflow_example")
def workflow_example(filename: str):
    allowed = {"vendor_onboarding.json", "employee_access_request.json", "customer_complaint.json"}
    if filename not in allowed:
        raise HTTPException(status_code=404, detail="Example not found.")
    path = ROOT / "examples" / filename
    return FileResponse(path, media_type="application/json", filename=filename)


@router.get("/notifications", name="notification_list")
def notification_list(request: Request, db: Session = Depends(get_db)):
    actor = _current_actor(request, db)
    notifications = list(
        db.scalars(
            select(Notification)
            .where(Notification.user_id == actor.id)
            .options(selectinload(Notification.case))
            .order_by(Notification.created_at.desc())
            .limit(100)
        )
    )
    return _render(request, db, "notifications.html", notifications=notifications)


@router.post("/notifications/{notification_id}/read", name="notification_read")
async def notification_read(notification_id: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    _verify_csrf(request, str(form.get("csrf_token", "")))
    actor = _current_actor(request, db)
    notification = db.scalar(
        select(Notification).where(Notification.id == notification_id, Notification.user_id == actor.id)
    )
    if notification:
        with database_write_lock():
            notification.is_read = True
            db.commit()
    return RedirectResponse(_safe_next(str(form.get("next", "/notifications")), "/notifications"), status_code=303)


@router.post("/notifications/read-all", name="notifications_read_all")
async def notifications_read_all(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    _verify_csrf(request, str(form.get("csrf_token", "")))
    actor = _current_actor(request, db)
    with database_write_lock():
        for notification in db.scalars(
            select(Notification).where(Notification.user_id == actor.id, Notification.is_read.is_(False))
        ):
            notification.is_read = True
        db.commit()
    _flash(request, "Notifications marked as read.")
    return RedirectResponse("/notifications", status_code=303)
