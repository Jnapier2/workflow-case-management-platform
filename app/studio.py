"""Business-user Workflow Studio, Rules Studio, connector registry, and knowledge administration."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import BusinessRuleSet, IntegrationConnector, KnowledgeArticle, WorkflowDefinition
from app.services.access import can
from app.services.common import loads
from app.services.connectors import validate_connector
from app.services.coordination import database_write_lock
from app.services.rules import preview_rules, save_rule_set
from app.services.workflow_engine import WorkflowValidationError, validate_workflow_config, workflow_config
from app.services.workflow_versions import config_diff, ensure_published_revision, latest_draft, list_revisions, publish_draft, save_draft
from app.web import _current_actor, _flash, _render, _verify_csrf

router = APIRouter()


def _admin(request: Request, db: Session):
    actor = _current_actor(request, db)
    if not can(actor, "*"):
        raise HTTPException(status_code=403, detail="Workflow Studio requires the Administrator role.")
    return actor


@router.get("/studio", name="studio_home")
def studio_home(request: Request, db: Session = Depends(get_db)):
    _admin(request, db)
    workflows = list(db.scalars(select(WorkflowDefinition).order_by(WorkflowDefinition.name)))
    rules = list(db.scalars(select(BusinessRuleSet).order_by(BusinessRuleSet.name)))
    connectors = list(db.scalars(select(IntegrationConnector).order_by(IntegrationConnector.name)))
    articles = list(db.scalars(select(KnowledgeArticle).order_by(KnowledgeArticle.title)))
    return _render(request, db, "studio.html", workflows=workflows, rules=rules, connectors=connectors, articles=articles)


@router.get("/studio/workflows/{workflow_key}", name="studio_workflow")
def studio_workflow(workflow_key: str, request: Request, db: Session = Depends(get_db)):
    _admin(request, db)
    workflow = db.scalar(select(WorkflowDefinition).where(WorkflowDefinition.key == workflow_key))
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    with database_write_lock():
        ensure_published_revision(db, workflow)
        db.commit()
    draft = latest_draft(db, workflow_key)
    revisions = list_revisions(db, workflow_key)
    current = workflow_config(workflow)
    draft_config = loads(draft.config_json, {}) if draft else current
    return _render(
        request,
        db,
        "studio_workflow.html",
        workflow=workflow,
        config=current,
        draft=draft,
        draft_config=draft_config,
        config_text=json.dumps(draft_config, indent=2, ensure_ascii=False),
        revisions=revisions,
        diff=config_diff(current, draft_config) if draft else None,
    )


@router.post("/studio/workflows/{workflow_key}/draft", name="studio_workflow_save_draft")
async def studio_workflow_save_draft(workflow_key: str, request: Request, db: Session = Depends(get_db)):
    actor = _admin(request, db)
    form = await request.form()
    _verify_csrf(request, str(form.get("csrf_token", "")))
    workflow = db.scalar(select(WorkflowDefinition).where(WorkflowDefinition.key == workflow_key))
    if not workflow:
        raise HTTPException(status_code=404, detail="Workflow not found.")
    try:
        value = json.loads(str(form.get("config_text", "{}")))
        with database_write_lock():
            draft = save_draft(db, workflow, value, actor, str(form.get("change_note", "")))
            db.commit()
        _flash(request, f"Draft v{draft.version} saved and linted.")
    except (json.JSONDecodeError, WorkflowValidationError, ValueError) as exc:
        db.rollback()
        _flash(request, f"Draft was not saved: {exc}", "danger")
    return RedirectResponse(f"/studio/workflows/{workflow_key}", status_code=303)


@router.post("/studio/workflows/{workflow_key}/publish", name="studio_workflow_publish")
async def studio_workflow_publish(workflow_key: str, request: Request, db: Session = Depends(get_db)):
    actor = _admin(request, db)
    form = await request.form()
    _verify_csrf(request, str(form.get("csrf_token", "")))
    workflow = db.scalar(select(WorkflowDefinition).where(WorkflowDefinition.key == workflow_key))
    draft = latest_draft(db, workflow_key)
    if not workflow or not draft:
        _flash(request, "No draft is available to publish.", "danger")
        return RedirectResponse(f"/studio/workflows/{workflow_key}", status_code=303)
    try:
        with database_write_lock():
            publish_draft(db, workflow, draft, actor)
            db.commit()
        _flash(request, f"Workflow {workflow.name} v{workflow.version} published. Existing cases remain on their captured snapshots.")
    except ValueError as exc:
        db.rollback()
        _flash(request, str(exc), "danger")
    return RedirectResponse(f"/studio/workflows/{workflow_key}", status_code=303)


@router.get("/studio/rules", name="studio_rules")
def studio_rules(request: Request, db: Session = Depends(get_db)):
    _admin(request, db)
    rules = list(db.scalars(select(BusinessRuleSet).order_by(BusinessRuleSet.name)))
    return _render(request, db, "studio_rules.html", rules=rules, loads=loads)


@router.post("/studio/rules/save", name="studio_rules_save")
async def studio_rules_save(request: Request, db: Session = Depends(get_db)):
    _admin(request, db)
    form = await request.form()
    _verify_csrf(request, str(form.get("csrf_token", "")))
    try:
        rules = json.loads(str(form.get("rules_text", "[]")))
        with database_write_lock():
            item = save_rule_set(db, key=str(form.get("key", "")), name=str(form.get("name", "")), description=str(form.get("description", "")), rules=rules)
            db.commit()
        _flash(request, f"Rule set {item.name} v{item.version} saved.")
    except (json.JSONDecodeError, ValueError) as exc:
        db.rollback()
        _flash(request, f"Rule set not saved: {exc}", "danger")
    return RedirectResponse("/studio/rules", status_code=303)


@router.post("/studio/rules/preview", name="studio_rules_preview")
async def studio_rules_preview(request: Request, db: Session = Depends(get_db)):
    _admin(request, db)
    form = await request.form()
    _verify_csrf(request, str(form.get("csrf_token", "")))
    rules = list(db.scalars(select(BusinessRuleSet).order_by(BusinessRuleSet.name)))
    try:
        context = json.loads(str(form.get("context_text", "{}")))
        rule_value = json.loads(str(form.get("rules_text", "[]")))
        if not isinstance(context, dict):
            raise ValueError("Preview context must be a JSON object.")
        result = preview_rules(rule_value, context)
        return _render(request, db, "studio_rules.html", rules=rules, loads=loads, preview=result, preview_context=json.dumps(context, indent=2), preview_rules_text=json.dumps(rule_value, indent=2))
    except (json.JSONDecodeError, ValueError) as exc:
        _flash(request, f"Rule preview failed: {exc}", "danger")
        return RedirectResponse("/studio/rules", status_code=303)


@router.get("/studio/connectors", name="studio_connectors")
def studio_connectors(request: Request, db: Session = Depends(get_db)):
    _admin(request, db)
    connectors = list(db.scalars(select(IntegrationConnector).order_by(IntegrationConnector.name)))
    return _render(request, db, "studio_connectors.html", connectors=connectors)


@router.post("/studio/connectors/save", name="studio_connectors_save")
async def studio_connectors_save(request: Request, db: Session = Depends(get_db)):
    _admin(request, db)
    form = await request.form()
    _verify_csrf(request, str(form.get("csrf_token", "")))
    key = str(form.get("key", "")).strip().lower().replace("-", "_")
    try:
        config = json.loads(str(form.get("config_text", "{}")))
        item = db.scalar(select(IntegrationConnector).where(IntegrationConnector.key == key))
        if item is None:
            item = IntegrationConnector(key=key, name=str(form.get("name", "")).strip()[:160] or key)
            db.add(item)
        item.name = str(form.get("name", item.name)).strip()[:160] or item.name
        item.kind = str(form.get("kind", "log")).strip().lower()
        item.endpoint_url = str(form.get("endpoint_url", "")).strip()[:600]
        item.method = str(form.get("method", "POST")).strip().upper()[:10]
        item.secret_env = str(form.get("secret_env", "")).strip()[:120]
        item.enabled = str(form.get("enabled", "")) == "on"
        item.allow_private_network = str(form.get("allow_private_network", "")) == "on"
        item.config_json = json.dumps(config, separators=(",", ":"), ensure_ascii=False)
        validate_connector(item)
        with database_write_lock():
            db.commit()
        _flash(request, f"Connector {item.name} saved.")
    except (json.JSONDecodeError, ValueError) as exc:
        db.rollback()
        _flash(request, f"Connector not saved: {exc}", "danger")
    return RedirectResponse("/studio/connectors", status_code=303)


@router.post("/studio/knowledge/save", name="studio_knowledge_save")
async def studio_knowledge_save(request: Request, db: Session = Depends(get_db)):
    _admin(request, db)
    form = await request.form()
    _verify_csrf(request, str(form.get("csrf_token", "")))
    slug = str(form.get("slug", "")).strip().lower().replace(" ", "-")[:120]
    if not slug:
        _flash(request, "Knowledge article slug is required.", "danger")
        return RedirectResponse("/studio", status_code=303)
    item = db.scalar(select(KnowledgeArticle).where(KnowledgeArticle.slug == slug))
    if item is None:
        item = KnowledgeArticle(slug=slug, title=str(form.get("title", slug)).strip()[:200] or slug)
        db.add(item)
    item.title = str(form.get("title", item.title)).strip()[:200] or item.title
    item.summary = str(form.get("summary", "")).strip()[:500]
    item.body = str(form.get("body", "")).strip()[:10000]
    item.workflow_key = str(form.get("workflow_key", "")).strip()[:80]
    item.published = str(form.get("published", "")) == "on"
    with database_write_lock():
        db.commit()
    _flash(request, f"Knowledge article {item.title} saved.")
    return RedirectResponse("/studio", status_code=303)
