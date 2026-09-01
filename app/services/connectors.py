"""Durable connector execution with safe local defaults and SSRF-aware webhook delivery."""
from __future__ import annotations

import os
import time
from typing import Any
from uuid import uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CaseRecord, IntegrationConnector, IntegrationExecution, User
from app.services.audit import append_activity
from app.services.common import dumps, loads, utcnow
from app.services.outbox import enqueue_job
from app.services.network_safety import validate_https_endpoint



def validate_connector(connector: IntegrationConnector) -> None:
    if connector.kind not in {"log", "webhook", "email"}:
        raise ValueError("Connector kind must be log, webhook, or email.")
    if connector.kind == "webhook":
        validate_https_endpoint(
            connector.endpoint_url,
            allow_private_network=bool(connector.allow_private_network),
            label="Webhook connector endpoint",
        )
    if connector.secret_env and not connector.secret_env.replace("_", "A").isalnum():
        raise ValueError("secret_env must be an environment-variable name.")


def enqueue_transition_connectors(db: Session, case: CaseRecord, transition: dict[str, Any], actor: User) -> int:
    actions = transition.get("connector_actions", [])
    if not isinstance(actions, list):
        return 0
    count = 0
    for key in actions:
        if not isinstance(key, str):
            continue
        connector = db.scalar(select(IntegrationConnector).where(IntegrationConnector.key == key, IntegrationConnector.enabled.is_(True)))
        if connector is None:
            continue
        execution = IntegrationExecution(
            connector_id=connector.id,
            case_id=case.id,
            correlation_id=uuid4().hex,
            idempotency_key=f"transition:{case.id}:{case.status}:{connector.id}:{case.updated_at.isoformat()}",
            status="Queued",
        )
        db.add(execution)
        db.flush()
        enqueue_job(db, "connector_execute", {"execution_id": execution.id}, dedupe_key=f"connector_execution:{execution.id}", max_attempts=5, priority=40)
        append_activity(db, case, actor, "integration_queued", f"Integration queued: {connector.name}", {"connector_key": connector.key, "execution_id": execution.id, "correlation_id": execution.correlation_id})
        count += 1
    return count


def _redact_summary(text: str) -> str:
    return text.replace("\r", " ").replace("\n", " ")[:500]


def execute_connector_job(db: Session, payload: dict[str, Any]) -> int:
    execution_id = int(payload.get("execution_id", 0) or 0)
    execution = db.get(IntegrationExecution, execution_id)
    if execution is None:
        raise RuntimeError("Connector execution no longer exists.")
    connector = db.get(IntegrationConnector, execution.connector_id)
    if connector is None:
        raise RuntimeError("Connector definition no longer exists.")
    validate_connector(connector)
    if not connector.enabled:
        execution.status = "Skipped"
        execution.completed_at = utcnow()
        execution.response_summary = "Connector disabled before execution."
        return 0
    case = db.get(CaseRecord, execution.case_id) if execution.case_id else None
    body = {
        "correlation_id": execution.correlation_id,
        "case_reference": case.reference if case else None,
        "case_status": case.status if case else None,
        "priority": case.priority if case else None,
        "event": "workflow_transition",
    }
    started = time.perf_counter()
    try:
        if connector.kind == "log":
            status = 200
            summary = "Local audit/log connector completed."
        elif connector.kind == "email":
            # Portfolio-safe adapter: creates a durable execution receipt without sending externally.
            # SMTP delivery can be implemented behind this contract without changing workflow writes.
            status = 202
            summary = "Email adapter accepted the durable notification payload; external SMTP delivery is not configured."
        else:
            headers = {"Content-Type": "application/json", "X-Correlation-ID": execution.correlation_id, "Idempotency-Key": execution.idempotency_key}
            if connector.secret_env:
                secret = os.getenv(connector.secret_env, "")
                if not secret:
                    raise RuntimeError(f"Connector secret environment variable {connector.secret_env} is not configured.")
                headers["Authorization"] = f"Bearer {secret}"
            with httpx.Client(timeout=15.0, follow_redirects=False) as client:
                response = client.request(connector.method.upper(), connector.endpoint_url, json=body, headers=headers)
            status = int(response.status_code)
            summary = f"Webhook returned HTTP {status}."
            if status < 200 or status >= 300:
                raise RuntimeError(summary)
        execution.status = "Completed"
        execution.http_status = status
        execution.response_summary = _redact_summary(summary)
        execution.error_summary = ""
        execution.completed_at = utcnow()
        execution.latency_ms = round((time.perf_counter() - started) * 1000)
        if case:
            append_activity(db, case, None, "integration_completed", f"Integration completed: {connector.name}", {"connector_key": connector.key, "execution_id": execution.id, "correlation_id": execution.correlation_id, "http_status": status, "latency_ms": execution.latency_ms})
        return 1
    except Exception as exc:
        execution.status = "Failed"
        execution.error_summary = _redact_summary(f"{type(exc).__name__}: {exc}")
        execution.completed_at = utcnow()
        execution.latency_ms = round((time.perf_counter() - started) * 1000)
        if case:
            append_activity(db, case, None, "integration_failed", f"Integration failed: {connector.name}", {"connector_key": connector.key, "execution_id": execution.id, "correlation_id": execution.correlation_id, "error": execution.error_summary})
        raise
