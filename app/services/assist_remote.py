"""Explicit, redacted external Case Assist adapter.

The adapter is disabled unless configured. It sends only bounded operational metadata,
never requester identity, free-text intake, comments, attachment names/bytes, or evidence
content. Returned suggestions are advisory-only and cannot contain executable case actions.
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import Approval, Attachment, CaseRecord, CaseRelation
from app.services.common import as_utc, loads, utcnow
from app.services.network_safety import validate_https_endpoint
from app.services.workflow_engine import case_workflow_config, stage_definition

MAX_RESPONSE_BYTES = 64 * 1024
MAX_SUMMARY_ITEMS = 5
MAX_SUGGESTIONS = 5


def remote_assist_settings() -> dict[str, Any]:
    return {
        "endpoint": os.getenv("WORKFLOW_ASSIST_HTTPS_ENDPOINT", "").strip(),
        "secret_env": os.getenv("WORKFLOW_ASSIST_SECRET_ENV", "").strip(),
        "allow_private_network": os.getenv("WORKFLOW_ASSIST_ALLOW_PRIVATE_NETWORK", "false").strip().lower() in {"1", "true", "yes", "on"},
    }


def remote_assist_available() -> bool:
    return bool(remote_assist_settings()["endpoint"])


def build_redacted_assist_payload(db: Session, case: CaseRecord) -> dict[str, Any]:
    """Create a bounded operational-only payload suitable for an explicitly configured advisor."""
    config = case_workflow_config(case)
    stage = stage_definition(config, case.status)
    now = utcnow()
    due = as_utc(case.stage_due_at)
    remaining_hours = None if due is None else round((due - now).total_seconds() / 3600, 1)
    pending_approvals = int(db.scalar(select(func.count(Approval.id)).where(Approval.case_id == case.id, Approval.status == "Pending")) or 0)
    evidence_count = int(db.scalar(select(func.count(Attachment.id)).where(Attachment.case_id == case.id)) or 0)
    relation_count = int(db.scalar(select(func.count(CaseRelation.id)).where(or_(CaseRelation.source_case_id == case.id, CaseRelation.target_case_id == case.id))) or 0)
    tags = [str(item)[:60] for item in loads(case.tags_json, []) if isinstance(item, str)][:20]
    return {
        "schema": "workflow_case_assist_operational_v1",
        "generated_at": now.isoformat(),
        "workflow_key": str(config.get("key") or (case.workflow.key if case.workflow else ""))[:100],
        "captured_workflow_version": int(case.workflow_version or 0),
        "stage_key": case.status[:80],
        "stage_label": str(stage.get("label", case.status))[:120],
        "priority": case.priority[:20],
        "tags": tags,
        "sla": {
            "paused": case.sla_paused_at is not None,
            "stage_remaining_hours": remaining_hours,
            "escalation_level": int(case.escalation_level or 0),
        },
        "counts": {
            "pending_approvals": pending_approvals,
            "evidence_files": evidence_count,
            "case_relationships": relation_count,
        },
        "privacy": "Operational metadata only. No requester identity, intake free text, comments, attachment names, or evidence content included.",
    }


def _clean_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").replace("\x00", "").split())[:limit]


def _normalize_response(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RuntimeError("External Case Assist response must be a JSON object.")
    summary_raw = value.get("summary", [])
    suggestions_raw = value.get("suggestions", [])
    summary = []
    if isinstance(summary_raw, list):
        summary = [_clean_text(item, 300) for item in summary_raw[:MAX_SUMMARY_ITEMS] if _clean_text(item, 300)]
    suggestions: list[dict[str, Any]] = []
    if isinstance(suggestions_raw, list):
        for index, item in enumerate(suggestions_raw[:MAX_SUGGESTIONS], start=1):
            if not isinstance(item, dict):
                continue
            title = _clean_text(item.get("title"), 160)
            rationale = _clean_text(item.get("rationale"), 500)
            if not title or not rationale:
                continue
            try:
                confidence = max(0.0, min(1.0, float(item.get("confidence", 0.5))))
            except (TypeError, ValueError):
                confidence = 0.5
            suggestions.append({
                "id": f"external-{index}",
                "kind": _clean_text(item.get("kind") or "advisory", 40),
                "confidence": confidence,
                "title": title,
                "rationale": rationale,
                # Critical boundary: an external advisor can never return an executable action.
                "action": {"type": "none"},
            })
    if not summary and not suggestions:
        raise RuntimeError("External Case Assist response did not contain bounded advisory content.")
    return {
        "provider": "external_guarded_advisory",
        "generated_at": utcnow().isoformat(),
        "applied": False,
        "summary": summary,
        "suggestions": suggestions,
        "safety": "External suggestions are redacted advisory output only and cannot autonomously change a case.",
    }


def request_remote_advisory(db: Session, case: CaseRecord) -> dict[str, Any]:
    """Make one explicit HTTPS advisory request. Never called automatically by workflow execution."""
    settings = remote_assist_settings()
    if not settings["endpoint"]:
        raise RuntimeError("External Case Assist is not configured.")
    endpoint = validate_https_endpoint(
        str(settings["endpoint"]),
        allow_private_network=bool(settings["allow_private_network"]),
        label="External Case Assist endpoint",
    )
    headers = {"Content-Type": "application/json", "User-Agent": "WorkflowCaseManagement/CaseAssist"}
    secret_env = str(settings["secret_env"])
    if secret_env:
        if not secret_env.replace("_", "A").isalnum():
            raise RuntimeError("WORKFLOW_ASSIST_SECRET_ENV must name an environment variable.")
        secret = os.getenv(secret_env, "")
        if not secret:
            raise RuntimeError(f"External Case Assist secret environment variable {secret_env} is not configured.")
        headers["Authorization"] = f"Bearer {secret}"
    payload = build_redacted_assist_payload(db, case)
    with httpx.Client(timeout=20.0, follow_redirects=False) as client:
        response = client.post(endpoint, json=payload, headers=headers)
    response.raise_for_status()
    content = response.content
    if len(content) > MAX_RESPONSE_BYTES:
        raise RuntimeError("External Case Assist response exceeded the 64 KiB safety limit.")
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError("External Case Assist response was not valid UTF-8 JSON.") from exc
    return _normalize_response(value)
