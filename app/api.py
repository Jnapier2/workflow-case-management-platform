"""REST interface for workflow and case operations.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.database_maintenance import SCHEMA_VERSION
from app.models import Approval, BusinessRuleSet, CaseRecord, IntegrationConnector, User, WorkflowDefinition, WorkflowRevision
from app.presentation import case_to_dict
from app.schemas import ApprovalDecisionRequest, CaseCreateRequest, TransitionRequest
from app.services.audit import build_case_audit_export, verify_case_audit_chain
from app.services.coordination import database_write_lock
from app.services.dashboard import build_dashboard
from app.services.identity import oidc_enabled
from app.services.operations import build_operational_health
from app.services.workflow_engine import (
    PermissionDenied,
    TransitionBlocked,
    WorkflowError,
    case_search_query,
    create_case,
    decide_approval,
    transition_case,
    workflow_config,
)
from app.version import APP_VERSION, BUILD_ID, DISPLAY_NAME


router = APIRouter(prefix="/api/v1", tags=["Workflow Case Management"])


def _actor(db: Session, actor_id: int | None, request: Request | None = None) -> User:
    if oidc_enabled():
        if request is None:
            raise HTTPException(status_code=401, detail="OIDC session is required.")
        session_actor_id = request.session.get("oidc_user_id")
        actor = db.get(User, session_actor_id) if isinstance(session_actor_id, int) else None
        if actor is None or not actor.active:
            raise HTTPException(status_code=401, detail="Sign in through /auth/login before using the API.")
        return actor
    actor = db.get(User, actor_id) if actor_id else None
    if actor is None:
        actor = db.scalar(select(User).where(User.role == "Administrator", User.active.is_(True)).order_by(User.id))
    if actor is None or not actor.active:
        raise HTTPException(status_code=400, detail="A valid active demo actor is required.")
    return actor



def _require_api_session(request: Request, db: Session) -> None:
    if not oidc_enabled():
        return
    actor_id = request.session.get("oidc_user_id")
    actor = db.get(User, actor_id) if isinstance(actor_id, int) else None
    if actor is None or not actor.active:
        raise HTTPException(status_code=401, detail="Sign in through /auth/login before using protected API resources.")

def _case(db: Session, reference: str) -> CaseRecord:
    case = db.scalar(
        select(CaseRecord)
        .where(CaseRecord.reference == reference.upper())
        .options(selectinload(CaseRecord.workflow), selectinload(CaseRecord.assignee))
    )
    if not case:
        raise HTTPException(status_code=404, detail="Case not found.")
    return case


def _raise_domain(exc: WorkflowError) -> None:
    if isinstance(exc, PermissionDenied):
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if isinstance(exc, TransitionBlocked):
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/health")
def health() -> dict[str, Any]:
    """Cheap process liveness endpoint."""
    return {
        "status": "ok",
        "application": DISPLAY_NAME,
        "version": APP_VERSION,
        "build_id": BUILD_ID,
    }


@router.get("/ready")
def ready(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Readiness endpoint that verifies the request-scoped database path is responsive."""
    database_ok = db.scalar(select(1)) == 1
    tasks = getattr(request.app.state, "runtime_tasks", None)
    scheduler = "active" if tasks and tasks.alive else ("disabled" if tasks is None else "stopped")
    if not database_ok:
        raise HTTPException(status_code=503, detail="Database readiness check failed.")
    return {
        "status": "ready",
        "application": DISPLAY_NAME,
        "version": APP_VERSION,
        "build_id": BUILD_ID,
        "database": "ready",
        "database_backend": getattr(request.app.state, "database_backend", "unknown"),
        "schema_version": int(getattr(request.app.state, "schema_version", SCHEMA_VERSION)),
        "search_mode": getattr(request.app.state, "search_mode", "unknown"),
        "durable_worker": scheduler,
    }


@router.get("/workflows")
def workflows(request: Request, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    _require_api_session(request, db)
    values = list(
        db.scalars(
            select(WorkflowDefinition)
            .where(WorkflowDefinition.active.is_(True))
            .order_by(WorkflowDefinition.name)
        )
    )
    return [
        {
            "key": item.key,
            "name": item.name,
            "description": item.description,
            "version": item.version,
            "active": item.active,
        }
        for item in values
    ]




@router.get("/capabilities")
def capabilities(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Portfolio capability discovery without exposing connector secrets or mutable runtime paths."""
    _require_api_session(request, db)
    return {
        "workflow_studio": True,
        "business_rules": int(db.scalar(select(func.count(BusinessRuleSet.id))) or 0),
        "workflow_version_governance": True,
        "case_relationships": ["parent_child", "related", "duplicate", "blocks"],
        "business_calendar_sla": True,
        "saved_queues_bulk_work_delegation": True,
        "durable_connectors": int(db.scalar(select(func.count(IntegrationConnector.id))) or 0),
        "requester_portal": True,
        "process_intelligence": True,
        "case_assist": {
            "local": "deterministic_guarded_advisory",
            "external_adapter": "explicit_redacted_https_advisory_optional",
            "automatic_case_mutation": False,
        },
        "optional_oidc": "authorization_code_pkce_stable_subject_role_mapping",
        "evidence_integrity": "streamed_sha256_verified_storage_adapter",
        "workflow_deployment": "draft_publish_diff_migrate_deterministic_canary",
    }


@router.get("/workflows/{workflow_key}/revisions")
def workflow_revisions_endpoint(workflow_key: str, request: Request, db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    _require_api_session(request, db)
    values = list(db.scalars(select(WorkflowRevision).where(WorkflowRevision.workflow_key == workflow_key).order_by(WorkflowRevision.version.desc())))
    return [{"version": item.version, "status": item.status, "change_note": item.change_note, "created_at": item.created_at.isoformat(), "published_at": item.published_at.isoformat() if item.published_at else None} for item in values]


@router.get("/workflows/{workflow_key}")
def workflow_detail(workflow_key: str, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    _require_api_session(request, db)
    item = db.scalar(select(WorkflowDefinition).where(WorkflowDefinition.key == workflow_key))
    if not item:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    return workflow_config(item)


@router.get("/cases")
def cases(
    response: Response,
    request: Request,
    workflow_id: int | None = None,
    status: str | None = None,
    priority: str | None = None,
    assignee_id: int | None = None,
    q: str | None = None,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    _require_api_session(request, db)
    base = case_search_query(
        db,
        workflow_id=workflow_id,
        status=status,
        priority=priority,
        assignee_id=assignee_id,
        search=q,
    )
    total = db.scalar(select(func.count()).select_from(base.order_by(None).subquery())) or 0
    response.headers["X-Total-Count"] = str(total)
    response.headers["X-Limit"] = str(limit)
    response.headers["X-Offset"] = str(offset)
    query = (
        base.options(selectinload(CaseRecord.workflow), selectinload(CaseRecord.assignee))
        .limit(limit)
        .offset(offset)
    )
    return [case_to_dict(item, include_fields=False) for item in db.scalars(query)]


@router.post("/cases", status_code=201)
def create_case_endpoint(payload: CaseCreateRequest, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    workflow = db.scalar(
        select(WorkflowDefinition).where(
            WorkflowDefinition.key == payload.workflow_key,
            WorkflowDefinition.active.is_(True),
        )
    )
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    actor = _actor(db, payload.actor_id, request)
    with database_write_lock():
        try:
            case = create_case(
                db,
                workflow,
                actor,
                title=payload.title,
                description=payload.description,
                requester_name=payload.requester_name,
                requester_email=payload.requester_email,
                priority=payload.priority,
                field_values=payload.fields,
            )
            db.commit()
            db.refresh(case)
            return case_to_dict(case)
        except WorkflowError as exc:
            db.rollback()
            _raise_domain(exc)


@router.get("/cases/{reference}")
def case_detail_endpoint(reference: str, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    _require_api_session(request, db)
    return case_to_dict(_case(db, reference))


@router.post("/cases/{reference}/transitions")
def transition_endpoint(reference: str, payload: TransitionRequest, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    case = _case(db, reference)
    actor = _actor(db, payload.actor_id, request)
    with database_write_lock():
        try:
            transition_case(db, case, actor, payload.target_stage, payload.note)
            db.commit()
            return case_to_dict(case)
        except WorkflowError as exc:
            db.rollback()
            _raise_domain(exc)


@router.post("/approvals/{approval_id}/decision")
def approval_endpoint(
    approval_id: int,
    payload: ApprovalDecisionRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    approval = db.scalar(
        select(Approval).where(Approval.id == approval_id).options(selectinload(Approval.case))
    )
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found.")
    actor = _actor(db, payload.actor_id, request)
    with database_write_lock():
        try:
            decide_approval(db, approval, actor, payload.decision, payload.note)
            db.commit()
            return {
                "id": approval.id,
                "case_reference": approval.case.reference,
                "name": approval.name,
                "assigned_role": approval.assigned_role,
                "status": approval.status,
            }
        except WorkflowError as exc:
            db.rollback()
            _raise_domain(exc)


@router.get("/cases/{reference}/audit")
def case_audit(reference: str, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    _require_api_session(request, db)
    return build_case_audit_export(db, _case(db, reference))


@router.get("/cases/{reference}/audit/verify")
def audit_verify(reference: str, request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    _require_api_session(request, db)
    case = _case(db, reference)
    return {"reference": case.reference, **verify_case_audit_chain(db, case.id)}


@router.get("/dashboard")
def dashboard_endpoint(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    _require_api_session(request, db)
    value = build_dashboard(db)
    return {
        "metrics": value["metrics"],
        "workflow_open": value["workflow_open"],
        "bottlenecks": value["bottlenecks"],
        "workload": value["workload"],
        "cycle_times": value["cycle_times"],
    }


@router.get("/operations")
def operations_endpoint(request: Request, db: Session = Depends(get_db)) -> dict[str, Any]:
    """Operational health, durable-job backlog, storage, recovery, and request timing."""
    _require_api_session(request, db)
    return build_operational_health(request, db)
