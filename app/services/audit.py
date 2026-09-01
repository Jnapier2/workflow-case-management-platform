"""Append-only application activity history and tamper-evident verification.

The hash chain is evidence of unexpected edits inside this application boundary; it is not
an external digital signature and does not prevent a database administrator from replacing
both data and hashes.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

from hashlib import sha256
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Activity, Approval, Attachment, CaseRecord, CaseRelation, Comment, IntegrationExecution, RequesterUpdate, User
from app.services.common import dumps, iso, loads, utcnow


def _activity_payload(
    *,
    case_id: int,
    actor_id: int | None,
    event_type: str,
    summary: str,
    details: dict[str, Any],
    created_at_iso: str,
) -> dict[str, Any]:
    return {
        "case_id": case_id,
        "actor_id": actor_id,
        "event_type": event_type,
        "summary": summary,
        "details": details,
        "created_at": created_at_iso,
    }


def _hash_entry(previous_hash: str, payload: dict[str, Any]) -> str:
    material = f"{previous_hash}|{dumps(payload)}".encode("utf-8")
    return sha256(material).hexdigest()


def append_activity(
    db: Session,
    case: CaseRecord,
    actor: User | None,
    event_type: str,
    summary: str,
    details: dict[str, Any] | None = None,
) -> Activity:
    details = details or {}
    previous = db.scalar(
        select(Activity)
        .where(Activity.case_id == case.id)
        .order_by(Activity.id.desc())
        .limit(1)
    )
    previous_hash = previous.entry_hash if previous else ""
    created_at = utcnow()
    payload = _activity_payload(
        case_id=case.id,
        actor_id=actor.id if actor else None,
        event_type=event_type,
        summary=summary,
        details=details,
        created_at_iso=iso(created_at) or "",
    )
    entry = Activity(
        case_id=case.id,
        actor_id=actor.id if actor else None,
        event_type=event_type,
        summary=summary,
        details_json=dumps(details),
        created_at=created_at,
        previous_hash=previous_hash,
        entry_hash=_hash_entry(previous_hash, payload),
    )
    db.add(entry)
    db.flush()
    return entry


def verify_case_audit_chain(db: Session, case_id: int) -> dict[str, Any]:
    entries = list(
        db.scalars(
            select(Activity)
            .where(Activity.case_id == case_id)
            .order_by(Activity.id.asc())
        )
    )
    previous_hash = ""
    problems: list[dict[str, Any]] = []
    for entry in entries:
        details = loads(entry.details_json, {})
        payload = _activity_payload(
            case_id=entry.case_id,
            actor_id=entry.actor_id,
            event_type=entry.event_type,
            summary=entry.summary,
            details=details,
            created_at_iso=iso(entry.created_at) or "",
        )
        expected = _hash_entry(previous_hash, payload)
        if entry.previous_hash != previous_hash:
            problems.append(
                {
                    "activity_id": entry.id,
                    "issue": "previous_hash_mismatch",
                    "expected": previous_hash,
                    "actual": entry.previous_hash,
                }
            )
        if entry.entry_hash != expected:
            problems.append(
                {
                    "activity_id": entry.id,
                    "issue": "entry_hash_mismatch",
                    "expected": expected,
                    "actual": entry.entry_hash,
                }
            )
        previous_hash = entry.entry_hash
    return {
        "valid": not problems,
        "entry_count": len(entries),
        "latest_hash": previous_hash,
        "problems": problems,
    }


def build_case_audit_export(db: Session, case: CaseRecord) -> dict[str, Any]:
    """Build a complete review package without changing the append-only activity chain."""
    workflow_config = loads(case.workflow_snapshot_json, {}) or loads(case.workflow.config_json, {})
    field_values = loads(case.field_values_json, {})
    approvals = list(db.scalars(select(Approval).where(Approval.case_id == case.id).order_by(Approval.id.asc())))
    comments = list(db.scalars(select(Comment).where(Comment.case_id == case.id).order_by(Comment.id.asc())))
    attachments = list(db.scalars(select(Attachment).where(Attachment.case_id == case.id).order_by(Attachment.id.asc())))
    requester_updates = list(db.scalars(select(RequesterUpdate).where(RequesterUpdate.case_id == case.id).order_by(RequesterUpdate.id.asc())))
    activities = list(db.scalars(select(Activity).where(Activity.case_id == case.id).order_by(Activity.id.asc())))
    outgoing_relations = list(db.scalars(select(CaseRelation).where(CaseRelation.source_case_id == case.id).order_by(CaseRelation.id.asc())))
    incoming_relations = list(db.scalars(select(CaseRelation).where(CaseRelation.target_case_id == case.id).order_by(CaseRelation.id.asc())))
    integrations = list(db.scalars(select(IntegrationExecution).where(IntegrationExecution.case_id == case.id).order_by(IntegrationExecution.id.asc())))

    actor_ids = {item.actor_id for item in comments if item.actor_id}
    actor_ids.update(item.actor_id for item in attachments if item.actor_id)
    actor_ids.update(item.actor_id for item in activities if item.actor_id)
    actor_ids.update(item.decided_by_id for item in approvals if item.decided_by_id)
    actor_ids.update(item.created_by_id for item in outgoing_relations + incoming_relations if item.created_by_id)
    users = {user.id: user for user in db.scalars(select(User).where(User.id.in_(actor_ids or {-1})))}

    related_ids = {item.target_case_id for item in outgoing_relations} | {item.source_case_id for item in incoming_relations}
    related_cases = {item.id: item.reference for item in db.scalars(select(CaseRecord).where(CaseRecord.id.in_(related_ids or {-1})))}

    verification = verify_case_audit_chain(db, case.id)
    return {
        "export_type": "case_audit",
        "generated_at": iso(utcnow()),
        "case": {
            "reference": case.reference,
            "workflow_key": workflow_config.get("key", case.workflow.key),
            "workflow_name": case.workflow.name,
            "workflow_version": case.workflow_version,
            "published_workflow_version": case.workflow.version,
            "workflow_definition_sha256": sha256(dumps(workflow_config).encode("utf-8")).hexdigest(),
            "title": case.title,
            "description": case.description,
            "requester_name": case.requester_name,
            "requester_email": case.requester_email,
            "status": case.status,
            "priority": case.priority,
            "tags": loads(case.tags_json, []),
            "assignee": case.assignee.name if case.assignee else None,
            "assignee_role": case.assignee.role if case.assignee else None,
            "field_values": field_values,
            "overall_due_at": iso(case.overall_due_at),
            "stage_due_at": iso(case.stage_due_at),
            "sla_paused_at": iso(case.sla_paused_at),
            "sla_pause_reason": case.sla_pause_reason,
            "sla_warning_at": iso(case.sla_warning_at),
            "created_at": iso(case.created_at),
            "completed_at": iso(case.completed_at),
            "closed_on_time": case.closed_on_time,
            "escalation_level": case.escalation_level,
        },
        "workflow_definition_snapshot": workflow_config,
        "approvals": [
            {
                "stage": item.stage_key,
                "key": item.approval_key,
                "name": item.name,
                "assigned_role": item.assigned_role,
                "status": item.status,
                "requested_at": iso(item.requested_at),
                "decided_at": iso(item.decided_at),
                "decided_by": users[item.decided_by_id].name if item.decided_by_id in users else None,
                "decision_note": item.decision_note,
            }
            for item in approvals
        ],
        "comments": [
            {
                "actor": users[item.actor_id].name if item.actor_id in users else "Unknown",
                "role": users[item.actor_id].role if item.actor_id in users else "Unknown",
                "body": item.body,
                "created_at": iso(item.created_at),
            }
            for item in comments
        ],
        "requester_updates": [
            {"requester_name": item.requester_name, "body": item.body, "created_at": iso(item.created_at)}
            for item in requester_updates
        ],
        "attachments": [
            {
                "original_name": item.original_name,
                "content_type": item.content_type,
                "size_bytes": item.size_bytes,
                "sha256": item.sha256,
                "storage_backend": item.storage_backend,
                "storage_key": item.storage_key,
                "integrity_status": item.integrity_status,
                "scan_status": item.scan_status,
                "uploaded_by": users[item.actor_id].name if item.actor_id in users else "Unknown",
                "uploaded_at": iso(item.uploaded_at),
            }
            for item in attachments
        ],
        "relationships": [
            {
                "direction": "outgoing",
                "relation_type": item.relation_type,
                "case_reference": related_cases.get(item.target_case_id),
                "note": item.note,
                "created_by": users[item.created_by_id].name if item.created_by_id in users else "System",
                "created_at": iso(item.created_at),
            }
            for item in outgoing_relations
        ] + [
            {
                "direction": "incoming",
                "relation_type": item.relation_type,
                "case_reference": related_cases.get(item.source_case_id),
                "note": item.note,
                "created_by": users[item.created_by_id].name if item.created_by_id in users else "System",
                "created_at": iso(item.created_at),
            }
            for item in incoming_relations
        ],
        "integration_executions": [
            {
                "connector_key": item.connector.key if item.connector else None,
                "connector_name": item.connector.name if item.connector else None,
                "correlation_id": item.correlation_id,
                "idempotency_key": item.idempotency_key,
                "status": item.status,
                "http_status": item.http_status,
                "latency_ms": item.latency_ms,
                "response_summary": item.response_summary,
                "error_summary": item.error_summary,
                "created_at": iso(item.created_at),
                "completed_at": iso(item.completed_at),
            }
            for item in integrations
        ],
        "activity": [
            {
                "id": item.id,
                "event_type": item.event_type,
                "summary": item.summary,
                "details": loads(item.details_json, {}),
                "actor": users[item.actor_id].name if item.actor_id in users else "System",
                "actor_role": users[item.actor_id].role if item.actor_id in users else None,
                "created_at": iso(item.created_at),
                "previous_hash": item.previous_hash,
                "entry_hash": item.entry_hash,
            }
            for item in activities
        ],
        "audit_chain_verification": verification,
    }
