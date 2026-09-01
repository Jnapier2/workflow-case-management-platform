"""Requester self-service portal with intake, status lookup, updates, evidence, and knowledge."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Attachment, CaseRecord, KnowledgeArticle, RequesterUpdate, User, WorkflowDefinition
from app.services.audit import append_activity
from app.services.coordination import database_write_lock
from app.services.evidence_store import save_upload
from app.services.workflow_engine import WorkflowError, case_workflow_config, create_case, workflow_config
from app.web import _csrf_token, _flash, _verify_csrf, templates
from app.version import APP_VERSION, BUILD_ID, DISPLAY_NAME

router = APIRouter()


def _portal_context(request: Request, **extra):
    base = {"request": request, "app_name": DISPLAY_NAME, "app_version": APP_VERSION, "build_id": BUILD_ID, "csrf_token": _csrf_token(request), "flash": request.session.pop("flash", None)}
    base.update(extra)
    return base


def _authorized(request: Request, reference: str) -> bool:
    values = request.session.get("portal_authorized_cases", [])
    return isinstance(values, list) and reference.upper() in values


def _authorize(request: Request, reference: str) -> None:
    values = request.session.get("portal_authorized_cases", [])
    values = values if isinstance(values, list) else []
    ref = reference.upper()
    if ref not in values:
        values.append(ref)
    request.session["portal_authorized_cases"] = values[-20:]


def _portal_actor(db: Session) -> User:
    email = "requester.portal@local.invalid"
    actor = db.scalar(select(User).where(User.email == email))
    if actor is None:
        actor = User(name="Requester Portal", email=email, role="External Requester", active=True, capacity=1)
        db.add(actor); db.flush()
    return actor


@router.get("/portal", name="requester_portal")
def requester_portal(request: Request, workflow: str | None = None, db: Session = Depends(get_db)):
    workflows = list(db.scalars(select(WorkflowDefinition).where(WorkflowDefinition.active.is_(True)).order_by(WorkflowDefinition.name)))
    selected = next((x for x in workflows if x.key == workflow), workflows[0] if workflows else None)
    articles = list(db.scalars(select(KnowledgeArticle).where(KnowledgeArticle.published.is_(True)).order_by(KnowledgeArticle.title)))
    return templates.TemplateResponse(request=request, name="portal.html", context=_portal_context(request, workflows=workflows, selected_workflow=selected, workflow_config=workflow_config(selected) if selected else None, articles=articles))


@router.post("/portal/requests", name="requester_portal_create")
async def requester_portal_create(request: Request, db: Session = Depends(get_db)):
    form = await request.form(); _verify_csrf(request, str(form.get("csrf_token", "")))
    workflow = db.scalar(select(WorkflowDefinition).where(WorkflowDefinition.key == str(form.get("workflow_key", "")), WorkflowDefinition.active.is_(True)))
    if not workflow: raise HTTPException(status_code=404, detail="Workflow not found.")
    fields = {str(k)[7:]: v for k, v in form.multi_items() if str(k).startswith("field__")}
    try:
        with database_write_lock():
            case = create_case(db, workflow, None, title=str(form.get("title", "")), description=str(form.get("description", "")), requester_name=str(form.get("requester_name", "")), requester_email=str(form.get("requester_email", "")), priority="Medium", field_values=fields)
            append_activity(db, case, None, "requester_portal_submitted", "Request submitted through requester portal", {})
            db.commit()
        _authorize(request, case.reference)
        _flash(request, f"Request {case.reference} submitted.")
        return RedirectResponse(f"/portal/cases/{case.reference}", status_code=303)
    except WorkflowError as exc:
        db.rollback(); _flash(request, str(exc), "danger")
        return RedirectResponse(f"/portal?workflow={workflow.key}", status_code=303)


@router.post("/portal/lookup", name="requester_portal_lookup")
async def requester_portal_lookup(request: Request, db: Session = Depends(get_db)):
    form = await request.form(); _verify_csrf(request, str(form.get("csrf_token", "")))
    ref = str(form.get("reference", "")).strip().upper(); email = str(form.get("email", "")).strip().lower()
    case = db.scalar(select(CaseRecord).where(CaseRecord.reference == ref))
    if not case or case.requester_email.lower() != email:
        _flash(request, "Request reference and email did not match.", "danger"); return RedirectResponse("/portal", status_code=303)
    _authorize(request, ref)
    return RedirectResponse(f"/portal/cases/{ref}", status_code=303)


@router.get("/portal/cases/{reference}", name="requester_portal_case")
def requester_portal_case(reference: str, request: Request, db: Session = Depends(get_db)):
    ref = reference.upper()
    if not _authorized(request, ref): raise HTTPException(status_code=403, detail="Use the portal lookup form to access this request.")
    case = db.scalar(select(CaseRecord).where(CaseRecord.reference == ref))
    if not case: raise HTTPException(status_code=404, detail="Request not found.")
    config = case_workflow_config(case)
    updates = list(db.scalars(select(RequesterUpdate).where(RequesterUpdate.case_id == case.id).order_by(RequesterUpdate.created_at.desc()).limit(50)))
    articles = list(db.scalars(select(KnowledgeArticle).where(KnowledgeArticle.published.is_(True), (KnowledgeArticle.workflow_key == case.workflow.key) | (KnowledgeArticle.workflow_key == "")).order_by(KnowledgeArticle.title)))
    stages = config.get("stages", [])
    current_index = next((i for i, stage in enumerate(stages) if stage.get("key") == case.status), 0)
    return templates.TemplateResponse(request=request, name="portal_case.html", context=_portal_context(request, case=case, config=config, stages=stages, current_index=current_index, updates=updates, articles=articles))


@router.post("/portal/cases/{reference}/updates", name="requester_portal_update")
async def requester_portal_update(reference: str, request: Request, db: Session = Depends(get_db)):
    ref=reference.upper()
    if not _authorized(request, ref): raise HTTPException(status_code=403, detail="Portal access is not authorized.")
    form=await request.form(); _verify_csrf(request, str(form.get("csrf_token", "")))
    case=db.scalar(select(CaseRecord).where(CaseRecord.reference==ref)); body=str(form.get("body", "")).strip()
    if not case or not body: _flash(request, "Enter an update before submitting.", "danger"); return RedirectResponse(f"/portal/cases/{ref}", status_code=303)
    with database_write_lock():
        item=RequesterUpdate(case_id=case.id, requester_name=case.requester_name, body=body[:5000]); db.add(item); db.flush()
        append_activity(db, case, None, "requester_update", "Requester provided additional information", {"requester_update_id":item.id})
        db.commit()
    _flash(request, "Update added to the request.")
    return RedirectResponse(f"/portal/cases/{ref}", status_code=303)


@router.post("/portal/cases/{reference}/evidence", name="requester_portal_evidence")
async def requester_portal_evidence(reference: str, request: Request, evidence: UploadFile = File(...), db: Session = Depends(get_db)):
    ref=reference.upper()
    if not _authorized(request, ref): raise HTTPException(status_code=403, detail="Portal access is not authorized.")
    case=db.scalar(select(CaseRecord).where(CaseRecord.reference==ref))
    if not case: raise HTTPException(status_code=404, detail="Request not found.")
    original=Path(evidence.filename or "evidence.bin").name[:255]; destination=None
    try:
        destination, receipt, stored = await save_upload(evidence, request.app.state.settings.uploads_dir, case.reference, original, request.app.state.settings.max_upload_bytes)
        with database_write_lock():
            actor=_portal_actor(db)
            item=Attachment(case_id=case.id,actor_id=actor.id,original_name=original,stored_name=stored,storage_backend=receipt.backend,storage_key=receipt.storage_key,integrity_status=receipt.integrity_status,scan_status=receipt.scan_status,content_type=(evidence.content_type or "application/octet-stream")[:160],size_bytes=receipt.size_bytes,sha256=receipt.sha256)
            db.add(item); db.flush(); append_activity(db,case,None,"requester_evidence_attached",f"Requester evidence attached: {original}",{"attachment_id":item.id,"sha256":item.sha256,"storage_backend":receipt.backend,"scan_status":receipt.scan_status}); db.commit()
        _flash(request,"Evidence attached.")
    except ValueError as exc:
        db.rollback();
        if destination is not None: destination.unlink(missing_ok=True)
        _flash(request,str(exc),"danger")
    finally:
        await evidence.close()
    return RedirectResponse(f"/portal/cases/{ref}", status_code=303)

