"""Workflow draft/publish/version governance and controlled case-snapshot migration."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CaseRecord, User, WorkflowDefinition, WorkflowRevision
from app.services.audit import append_activity
from app.services.common import dumps, loads, utcnow


def _engine_validate(config: dict[str, Any]) -> dict[str, Any]:
    from app.services.workflow_engine import validate_workflow_config
    return validate_workflow_config(config)


def ensure_published_revision(db: Session, workflow: WorkflowDefinition) -> WorkflowRevision:
    revision = db.scalar(select(WorkflowRevision).where(WorkflowRevision.workflow_key == workflow.key, WorkflowRevision.version == workflow.version))
    if revision is None:
        revision = WorkflowRevision(
            workflow_key=workflow.key,
            version=workflow.version,
            status="Published",
            config_json=workflow.config_json,
            change_note="Imported active workflow baseline",
            published_at=workflow.updated_at or workflow.created_at,
        )
        db.add(revision)
        db.flush()
    return revision


def list_revisions(db: Session, workflow_key: str) -> list[WorkflowRevision]:
    return list(db.scalars(select(WorkflowRevision).where(WorkflowRevision.workflow_key == workflow_key).order_by(WorkflowRevision.version.desc())))


def latest_draft(db: Session, workflow_key: str) -> WorkflowRevision | None:
    return db.scalar(select(WorkflowRevision).where(WorkflowRevision.workflow_key == workflow_key, WorkflowRevision.status == "Draft").order_by(WorkflowRevision.version.desc()))


def save_draft(db: Session, workflow: WorkflowDefinition, config: dict[str, Any], actor: User, change_note: str = "") -> WorkflowRevision:
    normalized = _engine_validate(deepcopy(config))
    ensure_published_revision(db, workflow)
    desired = max(workflow.version + 1, int(normalized.get("version", workflow.version + 1)))
    draft = db.scalar(select(WorkflowRevision).where(WorkflowRevision.workflow_key == workflow.key, WorkflowRevision.status == "Draft"))
    collision = db.scalar(select(WorkflowRevision).where(WorkflowRevision.workflow_key == workflow.key, WorkflowRevision.version == desired))
    if collision is not None and (draft is None or collision.id != draft.id):
        desired = int(db.scalar(select(func.max(WorkflowRevision.version)).where(WorkflowRevision.workflow_key == workflow.key)) or workflow.version) + 1
    normalized["version"] = desired
    if draft is None:
        draft = WorkflowRevision(workflow_key=workflow.key, version=desired, status="Draft", config_json=dumps(normalized), created_by_id=actor.id, change_note=change_note.strip()[:500])
        db.add(draft)
    else:
        draft.version = desired
        draft.config_json = dumps(normalized)
        draft.created_by_id = actor.id
        draft.change_note = change_note.strip()[:500]
        draft.created_at = utcnow()
    db.flush()
    return draft


def publish_draft(db: Session, workflow: WorkflowDefinition, draft: WorkflowRevision, actor: User) -> WorkflowDefinition:
    if draft.workflow_key != workflow.key or draft.status != "Draft":
        raise ValueError("Select a current draft revision.")
    config = _engine_validate(loads(draft.config_json, {}))
    next_version = max(workflow.version + 1, int(draft.version))
    config["version"] = next_version
    collision = db.scalar(select(WorkflowRevision).where(WorkflowRevision.workflow_key == workflow.key, WorkflowRevision.version == next_version, WorkflowRevision.id != draft.id))
    if collision is not None:
        next_version = int(db.scalar(select(func.max(WorkflowRevision.version)).where(WorkflowRevision.workflow_key == workflow.key)) or workflow.version) + 1
        config["version"] = next_version
    workflow.name = config["name"]
    workflow.description = config.get("description", "")
    workflow.version = next_version
    workflow.config_json = dumps(config)
    workflow.active = True
    workflow.updated_at = utcnow()
    for old in db.scalars(select(WorkflowRevision).where(WorkflowRevision.workflow_key == workflow.key, WorkflowRevision.status == "Published")):
        old.status = "Archived"
    draft.version = next_version
    draft.config_json = dumps(config)
    draft.status = "Published"
    draft.published_at = utcnow()
    draft.created_by_id = actor.id
    db.flush()
    return workflow


def config_diff(old_config: dict[str, Any], new_config: dict[str, Any]) -> dict[str, Any]:
    old_stages = {x.get("key"): x for x in old_config.get("stages", []) if isinstance(x, dict)}
    new_stages = {x.get("key"): x for x in new_config.get("stages", []) if isinstance(x, dict)}
    old_fields = {x.get("key"): x for x in old_config.get("fields", []) if isinstance(x, dict)}
    new_fields = {x.get("key"): x for x in new_config.get("fields", []) if isinstance(x, dict)}
    changed_stages = []
    for key in sorted(set(old_stages) & set(new_stages)):
        if old_stages[key] != new_stages[key]:
            changed_stages.append(key)
    changed_fields = []
    for key in sorted(set(old_fields) & set(new_fields)):
        if old_fields[key] != new_fields[key]:
            changed_fields.append(key)
    return {
        "from_version": old_config.get("version"),
        "to_version": new_config.get("version"),
        "added_stages": sorted(set(new_stages) - set(old_stages)),
        "removed_stages": sorted(set(old_stages) - set(new_stages)),
        "changed_stages": changed_stages,
        "added_fields": sorted(set(new_fields) - set(old_fields)),
        "removed_fields": sorted(set(old_fields) - set(new_fields)),
        "changed_fields": changed_fields,
        "calendar_changed": old_config.get("business_calendar") != new_config.get("business_calendar"),
        "rule_sets_changed": old_config.get("rule_sets", []) != new_config.get("rule_sets", []),
        "deployment_changed": old_config.get("deployment", {}) != new_config.get("deployment", {}),
    }


def migration_preview(case: CaseRecord, target_config: dict[str, Any]) -> dict[str, Any]:
    from app.services.workflow_engine import validate_case_fields
    reasons: list[str] = []
    stage_keys = {str(x.get("key")) for x in target_config.get("stages", []) if isinstance(x, dict)}
    if case.status not in stage_keys:
        reasons.append(f"Current stage '{case.status}' does not exist in target workflow.")
    try:
        validate_case_fields(target_config, loads(case.field_values_json, {}))
    except Exception as exc:
        reasons.append(str(exc))
    return {"eligible": not reasons, "reasons": reasons, "target_version": target_config.get("version")}


def migrate_case_snapshot(db: Session, case: CaseRecord, workflow: WorkflowDefinition, actor: User) -> dict[str, Any]:
    target = loads(workflow.config_json, {})
    preview = migration_preview(case, target)
    if not preview["eligible"]:
        raise ValueError("; ".join(preview["reasons"]))
    old = loads(case.workflow_snapshot_json, {})
    old_version = int(case.workflow_version or old.get("version", 1) or 1)
    case.workflow_snapshot_json = dumps(target)
    case.workflow_version = workflow.version
    case.updated_at = utcnow()
    db.flush()
    append_activity(db, case, actor, "workflow_snapshot_migrated", f"Workflow snapshot migrated v{old_version} → v{workflow.version}", {"from_version": old_version, "to_version": workflow.version, "diff": config_diff(old, target)})
    return {"from_version": old_version, "to_version": workflow.version, "diff": config_diff(old, target)}


def select_deployed_config(db: Session, workflow: WorkflowDefinition, deployment_key: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """Select the active or baseline published revision for deterministic canary rollout.

    The deployment key should be stable for a requester (for example normalized email). The
    selected configuration is still copied into the case, so later rollout changes do not
    rewrite running work.
    """
    from hashlib import sha256

    current = loads(workflow.config_json, {})
    policy = current.get("deployment", {}) if isinstance(current.get("deployment"), dict) else {}
    try:
        rollout = max(0, min(100, int(policy.get("rollout_percent", 100))))
    except (TypeError, ValueError):
        rollout = 100
    if rollout >= 100:
        return current, {"mode": "all_current", "selected_version": workflow.version, "rollout_percent": 100}

    baseline_version = policy.get("baseline_version")
    baseline = None
    if baseline_version is not None:
        try:
            baseline = db.scalar(select(WorkflowRevision).where(WorkflowRevision.workflow_key == workflow.key, WorkflowRevision.version == int(baseline_version)))
        except (TypeError, ValueError):
            baseline = None
    if baseline is None:
        baseline = db.scalar(select(WorkflowRevision).where(WorkflowRevision.workflow_key == workflow.key, WorkflowRevision.version < workflow.version).order_by(WorkflowRevision.version.desc()).limit(1))
    if baseline is None:
        return current, {"mode": "current_no_baseline", "selected_version": workflow.version, "rollout_percent": rollout}

    key = f"{workflow.key}|{deployment_key.strip().casefold()}".encode("utf-8")
    bucket = int.from_bytes(sha256(key).digest()[:4], "big") % 100
    if bucket < rollout:
        return current, {"mode": "canary_current", "selected_version": workflow.version, "baseline_version": baseline.version, "rollout_percent": rollout, "bucket": bucket}
    baseline_config = loads(baseline.config_json, {})
    return baseline_config, {"mode": "canary_baseline", "selected_version": baseline.version, "baseline_version": baseline.version, "rollout_percent": rollout, "bucket": bucket}
