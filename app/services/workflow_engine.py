"""Configurable workflow rules, routing, approvals, SLA escalation, and notifications.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

from datetime import date, timedelta
import json
import re
from typing import Any
from uuid import uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.models import Approval, CaseRecord, Notification, User, WorkflowDefinition
from app.services.audit import append_activity
from app.services.calendar_sla import due_for_stage, due_for_workflow, warning_window_hours
from app.services.common import as_utc, dumps, loads, utcnow
from app.services.outbox import enqueue_notification_delivery
from app.services.search import apply_case_search


FIELD_TYPES = {"text", "textarea", "number", "select", "checkbox", "date", "email"}
PRIORITIES = {"Low", "Medium", "High", "Critical"}
KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,79}$")


class WorkflowError(ValueError):
    """Base domain exception."""


class WorkflowValidationError(WorkflowError):
    """Raised when a workflow or case payload is invalid."""


class PermissionDenied(WorkflowError):
    """Raised when the selected demo actor lacks a configured role."""


class TransitionBlocked(WorkflowError):
    """Raised when a transition is not allowed or prerequisites are incomplete."""


def workflow_config(workflow: WorkflowDefinition) -> dict[str, Any]:
    value = loads(workflow.config_json, {})
    if not isinstance(value, dict):
        raise WorkflowValidationError("Stored workflow configuration is invalid.")
    return value


def case_workflow_config(case: CaseRecord) -> dict[str, Any]:
    """Return the immutable definition snapshot captured when the case was created."""
    value = loads(case.workflow_snapshot_json, {})
    if isinstance(value, dict) and value.get("key"):
        return value
    return workflow_config(case.workflow)


def validate_workflow_config(config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise WorkflowValidationError("Workflow configuration must be a JSON object.")

    required_text = ("key", "name", "description", "reference_prefix", "initial_stage")
    for key in required_text:
        if not isinstance(config.get(key), str) or not config[key].strip():
            raise WorkflowValidationError(f"Workflow field '{key}' is required.")

    if not KEY_PATTERN.match(config["key"]):
        raise WorkflowValidationError("Workflow key must use lowercase letters, numbers, and underscores.")
    prefix = config["reference_prefix"].strip().upper()
    if not re.fullmatch(r"[A-Z]{2,6}", prefix):
        raise WorkflowValidationError("reference_prefix must contain 2–6 uppercase letters.")
    config["reference_prefix"] = prefix

    version = config.get("version", 1)
    if not isinstance(version, int) or version < 1:
        raise WorkflowValidationError("Workflow version must be a positive integer.")

    total_sla = config.get("total_sla_hours", 0)
    if not isinstance(total_sla, (int, float)) or total_sla < 0:
        raise WorkflowValidationError("total_sla_hours must be zero or greater.")

    rule_sets = config.get("rule_sets", [])
    if not isinstance(rule_sets, list) or not all(isinstance(x, str) and KEY_PATTERN.match(x) for x in rule_sets):
        raise WorkflowValidationError("rule_sets must contain valid rule-set keys.")
    deployment = config.get("deployment", {})
    if deployment is not None and not isinstance(deployment, dict):
        raise WorkflowValidationError("deployment must be an object when configured.")
    if isinstance(deployment, dict):
        rollout = deployment.get("rollout_percent", 100)
        if not isinstance(rollout, int) or not 0 <= rollout <= 100:
            raise WorkflowValidationError("deployment.rollout_percent must be an integer from 0 to 100.")
        baseline = deployment.get("baseline_version")
        if baseline is not None and (not isinstance(baseline, int) or baseline < 1):
            raise WorkflowValidationError("deployment.baseline_version must be a positive integer.")

    calendar = config.get("business_calendar")
    if calendar is not None:
        if not isinstance(calendar, dict):
            raise WorkflowValidationError("business_calendar must be an object.")
        days = calendar.get("business_days", [0, 1, 2, 3, 4])
        if not isinstance(days, list) or not days or not all(isinstance(x, int) and 0 <= x <= 6 for x in days):
            raise WorkflowValidationError("business_calendar.business_days must contain weekday integers 0–6.")
        if not isinstance(calendar.get("timezone", "UTC"), str):
            raise WorkflowValidationError("business_calendar.timezone must be text.")
        holidays = calendar.get("holidays", [])
        if not isinstance(holidays, list) or not all(isinstance(x, str) for x in holidays):
            raise WorkflowValidationError("business_calendar.holidays must be ISO date strings.")

    fields = config.get("fields")
    if not isinstance(fields, list):
        raise WorkflowValidationError("Workflow fields must be a list.")
    field_keys: set[str] = set()
    for field in fields:
        if not isinstance(field, dict):
            raise WorkflowValidationError("Every field definition must be an object.")
        key = field.get("key")
        if not isinstance(key, str) or not KEY_PATTERN.match(key):
            raise WorkflowValidationError("Each intake field needs a valid lowercase key.")
        if key in field_keys:
            raise WorkflowValidationError(f"Duplicate intake field key: {key}")
        field_keys.add(key)
        if field.get("type") not in FIELD_TYPES:
            raise WorkflowValidationError(f"Unsupported field type for {key}: {field.get('type')}")
        if not isinstance(field.get("label"), str) or not field["label"].strip():
            raise WorkflowValidationError(f"Field {key} requires a label.")
        if not isinstance(field.get("required", False), bool):
            raise WorkflowValidationError(f"Field {key} required must be true or false.")
        max_length = field.get("max_length")
        if max_length is not None and (not isinstance(max_length, int) or max_length < 1 or max_length > 20000):
            raise WorkflowValidationError(f"Field {key} has an invalid max_length.")
        if field["type"] == "select":
            options = field.get("options")
            if not isinstance(options, list) or not options or not all(isinstance(x, str) and x for x in options):
                raise WorkflowValidationError(f"Select field {key} requires non-empty string options.")
            if len(set(options)) != len(options):
                raise WorkflowValidationError(f"Select field {key} contains duplicate options.")

    stages = config.get("stages")
    if not isinstance(stages, list) or len(stages) < 2:
        raise WorkflowValidationError("A workflow requires at least two stages.")
    stage_keys: set[str] = set()
    terminal_count = 0
    for stage in stages:
        if not isinstance(stage, dict):
            raise WorkflowValidationError("Every stage definition must be an object.")
        key = stage.get("key")
        if not isinstance(key, str) or not KEY_PATTERN.match(key):
            raise WorkflowValidationError("Each stage needs a valid lowercase key.")
        if key in stage_keys:
            raise WorkflowValidationError(f"Duplicate stage key: {key}")
        stage_keys.add(key)
        if not isinstance(stage.get("label"), str) or not stage["label"].strip():
            raise WorkflowValidationError(f"Stage {key} requires a label.")
        if stage.get("terminal"):
            terminal_count += 1
        else:
            role = stage.get("default_role")
            if not isinstance(role, str) or not role.strip():
                raise WorkflowValidationError(f"Non-terminal stage {key} requires default_role.")
            required_skills = stage.get("required_skills", [])
            if not isinstance(required_skills, list) or not all(isinstance(x, str) and x.strip() for x in required_skills):
                raise WorkflowValidationError(f"Stage {key} required_skills must be a list of strings.")
            sla = stage.get("sla_hours", 0)
            if not isinstance(sla, (int, float)) or sla < 0:
                raise WorkflowValidationError(f"Stage {key} has an invalid sla_hours value.")
        approvals = stage.get("approvals", [])
        if not isinstance(approvals, list):
            raise WorkflowValidationError(f"Stage {key} approvals must be a list.")
        approval_keys: set[str] = set()
        for approval in approvals:
            if not isinstance(approval, dict):
                raise WorkflowValidationError(f"Stage {key} contains an invalid approval.")
            approval_key = approval.get("key")
            if not isinstance(approval_key, str) or not KEY_PATTERN.match(approval_key):
                raise WorkflowValidationError(f"Stage {key} approval needs a valid key.")
            if approval_key in approval_keys:
                raise WorkflowValidationError(f"Duplicate approval key in {key}: {approval_key}")
            approval_keys.add(approval_key)
            if not approval.get("name") or not approval.get("assigned_role"):
                raise WorkflowValidationError(f"Approval {approval_key} requires name and assigned_role.")
            condition = approval.get("condition")
            if condition is not None:
                if not isinstance(condition, dict) or condition.get("field") not in field_keys:
                    raise WorkflowValidationError(f"Approval {approval_key} has an invalid condition field.")
                if condition.get("operator") not in {"truthy", "eq", "gte", "lte", "contains"}:
                    raise WorkflowValidationError(f"Approval {approval_key} has an unsupported condition operator.")

    if config["initial_stage"] not in stage_keys:
        raise WorkflowValidationError("initial_stage must match a configured stage.")
    if terminal_count < 1:
        raise WorkflowValidationError("A workflow requires at least one terminal stage.")

    transitions = config.get("transitions")
    if not isinstance(transitions, list) or not transitions:
        raise WorkflowValidationError("A workflow requires at least one transition.")
    seen_transitions: set[tuple[str, str]] = set()
    for transition in transitions:
        if not isinstance(transition, dict):
            raise WorkflowValidationError("Every transition must be an object.")
        source, target = transition.get("from"), transition.get("to")
        if source not in stage_keys or target not in stage_keys:
            raise WorkflowValidationError(f"Transition {source!r} -> {target!r} references an unknown stage.")
        if (source, target) in seen_transitions:
            raise WorkflowValidationError(f"Duplicate transition: {source} -> {target}")
        seen_transitions.add((source, target))
        roles = transition.get("roles")
        if not isinstance(roles, list) or not roles or not all(isinstance(x, str) and x for x in roles):
            raise WorkflowValidationError(f"Transition {source} -> {target} requires one or more roles.")
        if not isinstance(transition.get("label"), str) or not transition["label"].strip():
            raise WorkflowValidationError(f"Transition {source} -> {target} requires a label.")
        if not isinstance(transition.get("requires_approvals", False), bool) or not isinstance(transition.get("requires_children_complete", False), bool):
            raise WorkflowValidationError(f"Transition {source} -> {target} gating flags must be true or false.")
        connector_actions = transition.get("connector_actions", [])
        if not isinstance(connector_actions, list) or not all(isinstance(x, str) and KEY_PATTERN.match(x) for x in connector_actions):
            raise WorkflowValidationError(f"Transition {source} -> {target} connector_actions must contain valid connector keys.")
        source_stage = next(item for item in stages if item["key"] == source)
        if source_stage.get("terminal"):
            raise WorkflowValidationError(f"Terminal stage {source} cannot have outgoing transitions.")

    stage_by_key = {item["key"]: item for item in stages}
    if stage_by_key[config["initial_stage"]].get("terminal"):
        raise WorkflowValidationError("initial_stage cannot be terminal.")
    outgoing: dict[str, set[str]] = {key: set() for key in stage_keys}
    reverse: dict[str, set[str]] = {key: set() for key in stage_keys}
    for transition in transitions:
        outgoing[transition["from"]].add(transition["to"])
        reverse[transition["to"]].add(transition["from"])
    for key, stage in stage_by_key.items():
        if not stage.get("terminal") and not outgoing[key]:
            raise WorkflowValidationError(f"Non-terminal stage {key} requires an outgoing transition.")

    reachable = {config["initial_stage"]}
    pending = [config["initial_stage"]]
    while pending:
        source = pending.pop()
        for target in outgoing[source]:
            if target not in reachable:
                reachable.add(target)
                pending.append(target)
    unreachable = sorted(stage_keys - reachable)
    if unreachable:
        raise WorkflowValidationError(f"Unreachable stage(s): {', '.join(unreachable)}.")

    can_finish = {key for key, stage in stage_by_key.items() if stage.get("terminal")}
    pending = list(can_finish)
    while pending:
        target = pending.pop()
        for source in reverse[target]:
            if source not in can_finish:
                can_finish.add(source)
                pending.append(source)
    dead_ends = sorted(stage_keys - can_finish)
    if dead_ends:
        raise WorkflowValidationError(f"Stage(s) cannot reach a terminal outcome: {', '.join(dead_ends)}.")

    return config


def import_workflow_definition(db: Session, config: dict[str, Any]) -> WorkflowDefinition:
    config = validate_workflow_config(config)
    normalized = dumps(config)
    existing = db.scalar(select(WorkflowDefinition).where(WorkflowDefinition.key == config["key"]))
    for other in db.scalars(select(WorkflowDefinition).where(WorkflowDefinition.key != config["key"])):
        other_config = workflow_config(other)
        if other_config.get("reference_prefix") == config["reference_prefix"]:
            raise WorkflowValidationError(
                f"Reference prefix {config['reference_prefix']} is already used by workflow {other.key}."
            )
    if existing:
        from app.services.workflow_versions import ensure_published_revision
        prior_revision = ensure_published_revision(db, existing)
        if config["version"] < existing.version:
            raise WorkflowValidationError(
                f"Workflow {config['key']} is already at version {existing.version}; an older version cannot replace it."
            )
        if config["version"] == existing.version:
            if normalized == existing.config_json:
                return existing
            raise WorkflowValidationError(
                f"Workflow {config['key']} is already at version {existing.version}; increment version before changing it."
            )
        prior_revision.status = "Archived"
        existing.name = config["name"].strip()
        existing.description = config["description"].strip()
        existing.version = int(config["version"])
        existing.active = True
        existing.config_json = normalized
        db.flush()
        ensure_published_revision(db, existing)
        return existing
    workflow = WorkflowDefinition(
        key=config["key"],
        name=config["name"].strip(),
        description=config["description"].strip(),
        version=int(config["version"]),
        active=True,
        config_json=normalized,
    )
    db.add(workflow)
    db.flush()
    from app.services.workflow_versions import ensure_published_revision
    ensure_published_revision(db, workflow)
    return workflow


def stage_definition(config: dict[str, Any], stage_key: str) -> dict[str, Any]:
    for stage in config.get("stages", []):
        if stage.get("key") == stage_key:
            return stage
    raise WorkflowValidationError(f"Unknown stage: {stage_key}")


def stage_label(config: dict[str, Any], stage_key: str) -> str:
    try:
        return str(stage_definition(config, stage_key).get("label", stage_key))
    except WorkflowValidationError:
        return stage_key.replace("_", " ").title()


def is_terminal_stage(config: dict[str, Any], stage_key: str) -> bool:
    return bool(stage_definition(config, stage_key).get("terminal"))


def allowed_transitions(config: dict[str, Any], stage_key: str) -> list[dict[str, Any]]:
    return [item for item in config.get("transitions", []) if item.get("from") == stage_key]


def _normalize_email(value: str) -> str:
    value = value.strip().lower()
    if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise WorkflowValidationError("Enter a valid email address.")
    return value


def validate_case_fields(config: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    errors: list[str] = []
    for field in config.get("fields", []):
        key = field["key"]
        field_type = field["type"]
        raw = values.get(key)
        if field_type == "checkbox":
            value = raw is True or str(raw).strip().lower() in {"1", "true", "yes", "on"}
        else:
            value = raw.strip() if isinstance(raw, str) else raw

        missing = value is None or value == ""
        if missing and field.get("required"):
            errors.append(f"{field['label']} is required.")
            continue
        if missing:
            clean[key] = False if field_type == "checkbox" else None
            continue

        try:
            if field_type == "number":
                number = float(value)
                minimum = field.get("minimum")
                maximum = field.get("maximum")
                if minimum is not None and number < float(minimum):
                    raise ValueError(f"must be at least {minimum}")
                if maximum is not None and number > float(maximum):
                    raise ValueError(f"must be no more than {maximum}")
                clean[key] = int(number) if number.is_integer() else number
            elif field_type == "select":
                if value not in field.get("options", []):
                    raise ValueError("contains an unsupported option")
                clean[key] = value
            elif field_type == "email":
                clean[key] = _normalize_email(str(value))
            elif field_type == "date":
                clean[key] = date.fromisoformat(str(value)).isoformat()
            else:
                text = str(value)
                max_length = int(field.get("max_length", 4000))
                if len(text) > max_length:
                    raise ValueError(f"must be {max_length} characters or fewer")
                clean[key] = text
        except (TypeError, ValueError) as exc:
            errors.append(f"{field['label']} {exc}.")
    if errors:
        raise WorkflowValidationError(" ".join(errors))
    return clean


def _condition_matches(condition: dict[str, Any] | None, values: dict[str, Any]) -> bool:
    if not condition:
        return True
    actual = values.get(condition.get("field"))
    operator = condition.get("operator")
    expected = condition.get("value")
    try:
        if operator == "truthy":
            return bool(actual)
        if operator == "eq":
            return actual == expected
        if operator == "gte":
            return float(actual) >= float(expected)
        if operator == "lte":
            return float(actual) <= float(expected)
        if operator == "contains":
            return str(expected).lower() in str(actual).lower()
    except (TypeError, ValueError):
        return False
    return False


def _stage_due(stage: dict[str, Any], now=None, config: dict[str, Any] | None = None):  # type: ignore[no-untyped-def]
    now = now or utcnow()
    return due_for_stage(config or {}, stage, now)


def _overall_due(config: dict[str, Any], now=None):  # type: ignore[no-untyped-def]
    now = now or utcnow()
    return due_for_workflow(config, now)


def select_assignee(db: Session, role: str | None, required_skills: list[str] | None = None) -> User | None:
    if not role:
        return None
    from app.services.queues import active_delegate

    users = list(db.scalars(select(User).where(User.active.is_(True), User.role == role).order_by(User.id.asc())))
    required = {str(x).strip().casefold() for x in (required_skills or []) if str(x).strip()}
    if required:
        users = [u for u in users if required.issubset({str(x).casefold() for x in loads(u.skills_json, [])})]
    if not users:
        return None
    open_counts = {
        user_id: count
        for user_id, count in db.execute(
            select(CaseRecord.assignee_id, func.count(CaseRecord.id))
            .where(CaseRecord.completed_at.is_(None), CaseRecord.assignee_id.in_([u.id for u in users]))
            .group_by(CaseRecord.assignee_id)
        )
    }
    # Prefer users below configured capacity; fall back to the least-loaded eligible user.
    below_capacity = [u for u in users if open_counts.get(u.id, 0) < max(1, int(u.capacity or 10))]
    selected = min(below_capacity or users, key=lambda item: (open_counts.get(item.id, 0) / max(1, int(item.capacity or 10)), open_counts.get(item.id, 0), item.id))
    delegate = active_delegate(db, selected)
    return delegate if delegate and delegate.active else selected


def notify(db: Session, user: User | None, case: CaseRecord | None, kind: str, message: str) -> None:
    if user is None:
        return
    notification = Notification(
        user_id=user.id,
        case_id=case.id if case else None,
        kind=kind,
        message=message[:400],
        delivery_status="Queued",
    )
    db.add(notification)
    db.flush()
    enqueue_notification_delivery(db, notification)


def _reference_from_case_id(prefix: str, case_id: int) -> str:
    """Return a stable O(1) reference derived from SQLite's unique primary key.

    This avoids scanning every prior case and eliminates the duplicate-reference race that
    can occur when two requests calculate the same next per-prefix number concurrently.
    Existing references remain unchanged.
    """
    return f"{prefix}-{case_id:04d}"


def create_stage_approvals(db: Session, case: CaseRecord, actor: User | None = None) -> list[Approval]:
    config = case_workflow_config(case)
    stage = stage_definition(config, case.status)
    values = loads(case.field_values_json, {})
    created: list[Approval] = []
    for definition in stage.get("approvals", []):
        if not _condition_matches(definition.get("condition"), values):
            continue
        exists = db.scalar(
            select(Approval).where(
                Approval.case_id == case.id,
                Approval.stage_key == case.status,
                Approval.approval_key == definition["key"],
            )
        )
        if exists:
            continue
        approval = Approval(
            case_id=case.id,
            stage_key=case.status,
            approval_key=definition["key"],
            name=definition["name"],
            assigned_role=definition["assigned_role"],
        )
        db.add(approval)
        db.flush()
        created.append(approval)
        for reviewer in db.scalars(
            select(User).where(User.active.is_(True), User.role == definition["assigned_role"])
        ):
            notify(db, reviewer, case, "approval_requested", f"{case.reference}: {approval.name} is ready for decision.")
        append_activity(
            db,
            case,
            actor,
            "approval_requested",
            f"Approval requested: {approval.name}",
            {"approval_key": approval.approval_key, "assigned_role": approval.assigned_role},
        )
    return created


def create_case(
    db: Session,
    workflow: WorkflowDefinition,
    actor: User | None,
    *,
    title: str,
    description: str,
    requester_name: str,
    requester_email: str,
    priority: str,
    field_values: dict[str, Any],
) -> CaseRecord:
    current_config = workflow_config(workflow)
    validate_workflow_config(current_config)
    title = title.strip()
    requester_name = requester_name.strip()
    if not title or len(title) > 240:
        raise WorkflowValidationError("Case title is required and must be 240 characters or fewer.")
    if not requester_name or len(requester_name) > 160:
        raise WorkflowValidationError("Requester name is required and must be 160 characters or fewer.")
    requester_email = _normalize_email(requester_email)
    description = description.strip()
    if len(description) > 5000:
        raise WorkflowValidationError("Description must be 5,000 characters or fewer.")
    if priority not in PRIORITIES:
        raise WorkflowValidationError("Select a valid priority.")
    from app.services.workflow_versions import select_deployed_config
    config, deployment = select_deployed_config(db, workflow, requester_email)
    validate_workflow_config(config)
    clean_fields = validate_case_fields(config, field_values)

    from app.services.rules import evaluate_rule_sets

    rule_result = evaluate_rule_sets(
        db,
        config,
        {
            "fields": clean_fields,
            "priority": priority,
            "title": title,
            "requester_email": requester_email,
            "requester_name": requester_name,
        },
    )
    snapshot = rule_result["config"]
    priority = str(rule_result.get("priority") or priority)
    now = utcnow()
    initial_stage = stage_definition(snapshot, snapshot["initial_stage"])
    route_role = rule_result.get("route_role") or initial_stage.get("default_role")
    assignee = select_assignee(db, route_role, initial_stage.get("required_skills", []))
    case = CaseRecord(
        reference=f"TMP-{uuid4().hex[:20]}",
        workflow=workflow,
        workflow_version=int(snapshot.get("version", workflow.version) or workflow.version),
        title=title,
        description=description,
        requester_name=requester_name,
        requester_email=requester_email,
        status=snapshot["initial_stage"],
        priority=priority,
        assignee=assignee,
        field_values_json=dumps(clean_fields),
        workflow_snapshot_json=dumps(snapshot),
        tags_json=dumps(rule_result.get("tags", [])),
        overall_due_at=_overall_due(snapshot, now),
        stage_due_at=_stage_due(initial_stage, now, snapshot),
        stage_started_at=now,
        created_at=now,
        updated_at=now,
    )
    db.add(case)
    db.flush()
    case.reference = _reference_from_case_id(snapshot["reference_prefix"], case.id)
    db.flush()
    append_activity(
        db,
        case,
        actor,
        "case_created",
        f"Case created in {initial_stage['label']}",
        {
            "workflow_key": workflow.key,
            "workflow_version": case.workflow_version,
            "priority": priority,
            "assignee": assignee.name if assignee else None,
            "rule_matches": rule_result.get("matched", []),
            "tags": rule_result.get("tags", []),
            "deployment": deployment,
        },
    )
    if assignee:
        notify(db, assignee, case, "assignment", f"{case.reference} was assigned to you.")
    create_stage_approvals(db, case, actor)
    return case

def _find_transition(config: dict[str, Any], source: str, target: str) -> dict[str, Any] | None:
    return next(
        (item for item in config.get("transitions", []) if item.get("from") == source and item.get("to") == target),
        None,
    )


def _actor_allowed(actor: User, roles: list[str]) -> bool:
    return actor.role == "Administrator" or actor.role in roles


def transition_case(db: Session, case: CaseRecord, actor: User, target_stage: str, note: str = "") -> CaseRecord:
    if case.completed_at is not None:
        raise TransitionBlocked("This case is already in a terminal state.")
    config = case_workflow_config(case)
    transition = _find_transition(config, case.status, target_stage)
    if not transition:
        raise TransitionBlocked("That transition is not configured from the current stage.")
    if not _actor_allowed(actor, transition.get("roles", [])):
        raise PermissionDenied(
            f"This transition requires one of these roles: {', '.join(transition.get('roles', []))}."
        )
    if transition.get("requires_approvals"):
        approvals = list(db.scalars(select(Approval).where(Approval.case_id == case.id, Approval.stage_key == case.status)))
        rejected = [item for item in approvals if item.status == "Rejected"]
        pending = [item for item in approvals if item.status != "Approved"]
        if rejected:
            raise TransitionBlocked("A required approval was rejected. Use a configured rejection or rework transition.")
        if pending:
            raise TransitionBlocked("All required approvals must be approved before this transition.")
    if transition.get("requires_children_complete"):
        from app.services.relations import incomplete_children

        children = incomplete_children(db, case)
        if children:
            raise TransitionBlocked(
                "Complete dependent child cases first: " + ", ".join(item.reference for item in children[:8])
            )

    source_stage = case.status
    target = stage_definition(config, target_stage)
    old_assignee = case.assignee
    now = utcnow()
    case.status = target_stage
    case.stage_started_at = now
    case.updated_at = now
    case.escalation_level = 0
    case.escalated_at = None
    case.sla_warning_at = None
    case.sla_paused_at = None
    case.sla_pause_reason = ""
    if target.get("terminal"):
        case.stage_due_at = None
        case.completed_at = now
        due = as_utc(case.overall_due_at)
        case.closed_on_time = due is None or now <= due
        case.assignee = None
    else:
        case.stage_due_at = _stage_due(target, now, config)
        case.assignee = select_assignee(db, target.get("default_role"), target.get("required_skills", []))
    db.flush()

    append_activity(
        db,
        case,
        actor,
        "status_changed",
        f"Status changed from {stage_label(config, source_stage)} to {target['label']}",
        {
            "from": source_stage,
            "to": target_stage,
            "transition_label": transition.get("label"),
            "note": note.strip()[:2000],
            "previous_assignee": old_assignee.name if old_assignee else None,
            "new_assignee": case.assignee.name if case.assignee else None,
        },
    )
    if case.assignee and (not old_assignee or case.assignee.id != old_assignee.id):
        notify(db, case.assignee, case, "assignment", f"{case.reference} moved to {target['label']} and was assigned to you.")
    create_stage_approvals(db, case, actor)
    from app.services.connectors import enqueue_transition_connectors

    enqueue_transition_connectors(db, case, transition, actor)
    return case

def decide_approval(db: Session, approval: Approval, actor: User, decision: str, note: str = "") -> Approval:
    if approval.status != "Pending":
        raise TransitionBlocked("This approval already has a decision.")
    if actor.role != "Administrator" and actor.role != approval.assigned_role:
        raise PermissionDenied(f"This decision requires the {approval.assigned_role} role.")
    if decision not in {"Approved", "Rejected"}:
        raise WorkflowValidationError("Decision must be Approved or Rejected.")
    approval.status = decision
    approval.decided_at = utcnow()
    approval.decided_by_id = actor.id
    approval.decision_note = note.strip()[:2000]
    db.flush()
    append_activity(
        db,
        approval.case,
        actor,
        "approval_decided",
        f"{approval.name}: {decision}",
        {
            "approval_key": approval.approval_key,
            "decision": decision,
            "note": approval.decision_note,
        },
    )
    if approval.case.assignee:
        notify(
            db,
            approval.case.assignee,
            approval.case,
            "approval_decided",
            f"{approval.case.reference}: {approval.name} was {decision.lower()}.",
        )
    return approval


def assign_case(db: Session, case: CaseRecord, actor: User, assignee: User | None) -> None:
    from app.services.access import can
    if not can(actor, "case.assign"):
        raise PermissionDenied("The selected role cannot reassign cases.")
    previous = case.assignee
    case.assignee = assignee
    case.updated_at = utcnow()
    db.flush()
    append_activity(
        db,
        case,
        actor,
        "assignment_changed",
        f"Assignment changed to {assignee.name if assignee else 'Unassigned'}",
        {
            "previous_assignee": previous.name if previous else None,
            "new_assignee": assignee.name if assignee else None,
        },
    )
    if assignee:
        notify(db, assignee, case, "assignment", f"{case.reference} was assigned to you by {actor.name}.")


def change_priority(db: Session, case: CaseRecord, actor: User, priority: str) -> None:
    from app.services.access import can
    if not can(actor, "case.priority"):
        raise PermissionDenied("The selected role cannot change priority.")
    if priority not in PRIORITIES:
        raise WorkflowValidationError("Select a valid priority.")
    previous = case.priority
    case.priority = priority
    case.updated_at = utcnow()
    db.flush()
    append_activity(
        db,
        case,
        actor,
        "priority_changed",
        f"Priority changed from {previous} to {priority}",
        {"from": previous, "to": priority},
    )



def change_tags(db: Session, case: CaseRecord, actor: User, tags: list[str]) -> None:
    from app.services.access import can
    if not can(actor, "case.priority"):
        raise PermissionDenied("The selected role cannot change case tags.")
    clean: list[str] = []
    for item in tags:
        tag = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(item).strip()).strip("_")[:60]
        if tag and tag not in clean:
            clean.append(tag)
    previous = loads(case.tags_json, [])
    case.tags_json = dumps(clean[:20])
    case.updated_at = utcnow()
    db.flush()
    append_activity(db, case, actor, "tags_changed", "Case tags updated", {"from": previous, "to": clean[:20]})


def pause_sla(db: Session, case: CaseRecord, actor: User, reason: str) -> None:
    from app.services.access import can
    if not can(actor, "sla.manage"):
        raise PermissionDenied("The selected role cannot pause service-level timers.")
    if case.completed_at is not None:
        raise TransitionBlocked("Completed cases cannot pause an SLA.")
    if case.sla_paused_at is not None:
        raise TransitionBlocked("This case SLA is already paused.")
    reason = reason.strip()[:200]
    if not reason:
        raise WorkflowValidationError("A pause reason is required.")
    case.sla_paused_at = utcnow()
    case.sla_pause_reason = reason
    case.updated_at = utcnow()
    db.flush()
    append_activity(db, case, actor, "sla_paused", "Service-level timers paused", {"reason": reason})


def resume_sla(db: Session, case: CaseRecord, actor: User) -> None:
    from app.services.access import can
    if not can(actor, "sla.manage"):
        raise PermissionDenied("The selected role cannot resume service-level timers.")
    paused = as_utc(case.sla_paused_at)
    if paused is None:
        raise TransitionBlocked("This case SLA is not paused.")
    now = utcnow()
    delta = now - paused
    if case.stage_due_at is not None:
        case.stage_due_at = (as_utc(case.stage_due_at) or case.stage_due_at) + delta
    if case.overall_due_at is not None:
        case.overall_due_at = (as_utc(case.overall_due_at) or case.overall_due_at) + delta
    reason = case.sla_pause_reason
    case.sla_paused_at = None
    case.sla_pause_reason = ""
    case.updated_at = now
    db.flush()
    append_activity(db, case, actor, "sla_resumed", "Service-level timers resumed", {"paused_seconds": int(delta.total_seconds()), "previous_reason": reason})

def process_sla_escalations(db: Session) -> int:
    """Record due-soon warnings and overdue escalation levels without touching paused cases."""
    now = utcnow()
    cases = list(
        db.scalars(
            select(CaseRecord)
            .where(
                CaseRecord.completed_at.is_(None),
                CaseRecord.sla_paused_at.is_(None),
                or_(CaseRecord.stage_due_at.is_not(None), CaseRecord.overall_due_at.is_not(None)),
            )
            .order_by(CaseRecord.id.asc())
        )
    )
    if not cases:
        return 0

    changes = 0
    administrators = list(db.scalars(select(User).where(User.active.is_(True), User.role == "Administrator")))
    for case in cases:
        config = case_workflow_config(case)
        stage = stage_definition(config, case.status)
        stage_due = as_utc(case.stage_due_at)
        overall_due = as_utc(case.overall_due_at)
        warning_hours = warning_window_hours(config, stage)
        nearest = min([item for item in (stage_due, overall_due) if item is not None], default=None)
        if nearest and nearest >= now and case.sla_warning_at is None and nearest <= now + timedelta(hours=warning_hours):
            case.sla_warning_at = now
            case.updated_at = now
            append_activity(db, case, None, "sla_warning", "Service-level deadline is approaching", {"warning_hours": warning_hours, "due_at": nearest.isoformat()})
            recipients = administrators[:]
            if case.assignee and all(user.id != case.assignee.id for user in recipients):
                recipients.append(case.assignee)
            for recipient in recipients:
                notify(db, recipient, case, "sla_warning", f"{case.reference} is approaching a service-level deadline.")
            changes += 1

        new_level = case.escalation_level
        reason = None
        if overall_due and overall_due < now and case.escalation_level < 2:
            new_level = 2
            reason = "overall_sla_overdue"
        elif stage_due and stage_due < now and case.escalation_level < 1:
            new_level = 1
            reason = "stage_sla_overdue"
        if not reason:
            continue

        case.escalation_level = new_level
        case.escalated_at = now
        case.updated_at = now
        escalation_cfg = config.get("escalation", {}) if isinstance(config.get("escalation"), dict) else {}
        level_cfg = escalation_cfg.get(str(new_level), {}) if isinstance(escalation_cfg.get(str(new_level)), dict) else {}
        reassign_role = str(level_cfg.get("reassign_role", "")).strip()
        previous_assignee = case.assignee
        if reassign_role:
            replacement = select_assignee(db, reassign_role)
            if replacement is not None:
                case.assignee = replacement
        append_activity(
            db,
            case,
            None,
            "sla_escalated",
            "Service-level deadline escalated",
            {
                "level": new_level,
                "reason": reason,
                "reassign_role": reassign_role or None,
                "previous_assignee": previous_assignee.name if previous_assignee else None,
                "new_assignee": case.assignee.name if case.assignee else None,
            },
        )
        recipients = administrators[:]
        notify_roles = [str(x) for x in level_cfg.get("notify_roles", []) if isinstance(x, str)]
        if notify_roles:
            recipients.extend(list(db.scalars(select(User).where(User.active.is_(True), User.role.in_(notify_roles)))))
        if case.assignee and all(user.id != case.assignee.id for user in recipients):
            recipients.append(case.assignee)
        unique: dict[int, User] = {user.id: user for user in recipients}
        for recipient in unique.values():
            notify(db, recipient, case, "sla_escalation", f"{case.reference} has an overdue service-level deadline.")
        changes += 1
    return changes

def case_search_query(
    db: Session,
    *,
    workflow_id: int | None = None,
    status: str | None = None,
    priority: str | None = None,
    assignee_id: int | None = None,
    search: str | None = None,
):  # type: ignore[no-untyped-def]
    query = select(CaseRecord).order_by(CaseRecord.updated_at.desc())
    if workflow_id:
        query = query.where(CaseRecord.workflow_id == workflow_id)
    if status:
        query = query.where(CaseRecord.status == status)
    if priority:
        query = query.where(CaseRecord.priority == priority)
    if assignee_id:
        query = query.where(CaseRecord.assignee_id == assignee_id)
    return apply_case_search(query, db, search)
