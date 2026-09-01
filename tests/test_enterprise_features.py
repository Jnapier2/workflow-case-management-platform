"""Enterprise-style workflow/case capabilities added after the B006 foundation."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import re

import pytest
from sqlalchemy import select

from app.models import BusinessRuleSet, CaseRecord, IntegrationExecution, RequesterUpdate, User, WorkflowDefinition
from app.services.audit import build_case_audit_export
from app.services.calendar_sla import add_service_hours
from app.services.common import loads, utcnow
from app.services.outbox import process_outbox_batch
from app.services.relations import create_relation
from app.services.rules import save_rule_set
from app.services.workflow_engine import TransitionBlocked, create_case, decide_approval, transition_case
from app.services.workflow_versions import migration_preview, publish_draft, save_draft


def _session(client):
    return client.app.state.SessionLocal()


def _vendor_payload(*, spend: int = 90000, personal: bool = False) -> dict:
    return {
        "vendor_name": "Enterprise Feature Vendor",
        "service_category": "Software",
        "annual_spend": spend,
        "handles_personal_data": personal,
        "country": "United States",
        "business_owner": "Operations",
        "requested_start_date": (date.today() + timedelta(days=30)).isoformat(),
        "risk_notes": "Automated enterprise feature test.",
    }


def _complete_vendor_to_contracting(db, case, admin):
    transition_case(db, case, admin, "validation")
    transition_case(db, case, admin, "due_diligence")
    transition_case(db, case, admin, "approvals")
    for approval in list(case.approvals):
        if approval.status == "Pending":
            decide_approval(db, approval, admin, "Approved", "Automated test approval")
    transition_case(db, case, admin, "contracting")


def test_business_calendar_skips_weekend_and_holiday():
    config = {
        "business_calendar": {
            "timezone": "America/Chicago",
            "business_days": [0, 1, 2, 3, 4],
            "start": "08:00",
            "end": "17:00",
            "holidays": ["2026-09-07"],
        }
    }
    # Friday Sep 4, 2026 16:00 CDT; one hour Friday + one hour Tuesday after Labor Day.
    start = datetime(2026, 9, 4, 21, 0, tzinfo=timezone.utc)
    due = add_service_hours(config, 2, start)
    assert due == datetime(2026, 9, 8, 14, 0, tzinfo=timezone.utc)


def test_seeded_rules_are_idempotent_and_apply_to_intake(client):
    with _session(client) as db:
        item = db.scalar(select(BusinessRuleSet).where(BusinessRuleSet.key == "vendor_risk"))
        assert item
        original_version = item.version
        rules = loads(item.rules_json, [])
        same = save_rule_set(db, key=item.key, name=item.name, description=item.description, rules=rules)
        assert same.version == original_version

        workflow = db.scalar(select(WorkflowDefinition).where(WorkflowDefinition.key == "vendor_onboarding"))
        admin = db.scalar(select(User).where(User.role == "Administrator"))
        case = create_case(db, workflow, admin, title="High-value sensitive vendor", description="", requester_name="Jordan Example", requester_email="jordan@example.com", priority="Medium", field_values=_vendor_payload(spend=300000, personal=True))
        db.flush()
        assert case.priority == "High"
        assert {"data_sensitive", "high_value"}.issubset(set(loads(case.tags_json, [])))
        snapshot = loads(case.workflow_snapshot_json, {})
        approval_keys = {a["key"] for s in snapshot["stages"] for a in s.get("approvals", [])}
        assert "executive_finance_review" in approval_keys


def test_parent_child_gate_blocks_parent_until_child_is_complete(client):
    with _session(client) as db:
        workflow = db.scalar(select(WorkflowDefinition).where(WorkflowDefinition.key == "vendor_onboarding"))
        admin = db.scalar(select(User).where(User.role == "Administrator"))
        parent = create_case(db, workflow, admin, title="Parent vendor", description="", requester_name="P", requester_email="p@example.com", priority="Medium", field_values=_vendor_payload())
        child = create_case(db, workflow, admin, title="Child review", description="", requester_name="P", requester_email="p@example.com", priority="Medium", field_values=_vendor_payload())
        _complete_vendor_to_contracting(db, parent, admin)
        create_relation(db, parent, child, "parent_child", admin, "Security sub-review")
        with pytest.raises(TransitionBlocked, match="child"):
            transition_case(db, parent, admin, "completed")
        child.status = "completed"; child.completed_at = utcnow(); db.flush()
        transition_case(db, parent, admin, "completed")
        assert parent.completed_at is not None


def test_transition_connector_is_durable_and_audited(client):
    with _session(client) as db:
        workflow = db.scalar(select(WorkflowDefinition).where(WorkflowDefinition.key == "vendor_onboarding"))
        admin = db.scalar(select(User).where(User.role == "Administrator"))
        case = create_case(db, workflow, admin, title="Connector vendor", description="", requester_name="C", requester_email="c@example.com", priority="Medium", field_values=_vendor_payload())
        _complete_vendor_to_contracting(db, case, admin)
        transition_case(db, case, admin, "completed")
        db.commit()
        ref = case.reference
    result = process_outbox_batch(client.app.state.SessionLocal, limit=100)
    assert result["failed"] == 0
    with _session(client) as db:
        case = db.scalar(select(CaseRecord).where(CaseRecord.reference == ref))
        executions = list(db.scalars(select(IntegrationExecution).where(IntegrationExecution.case_id == case.id)))
        assert executions and executions[-1].status == "Completed"
        export = build_case_audit_export(db, case)
        assert export["integration_executions"][-1]["status"] == "Completed"
        assert export["audit_chain_verification"]["valid"] is True


def test_workflow_draft_publish_preserves_existing_case_snapshot(client):
    with _session(client) as db:
        workflow = db.scalar(select(WorkflowDefinition).where(WorkflowDefinition.key == "vendor_onboarding"))
        admin = db.scalar(select(User).where(User.role == "Administrator"))
        case = create_case(db, workflow, admin, title="Versioned vendor", description="", requester_name="V", requester_email="v@example.com", priority="Medium", field_values=_vendor_payload())
        captured = case.workflow_version
        config = loads(workflow.config_json, {})
        draft_config = deepcopy(config)
        draft_config["description"] = "Published by enterprise feature test."
        draft = save_draft(db, workflow, draft_config, admin, "Exercise version governance")
        publish_draft(db, workflow, draft, admin)
        db.flush()
        assert workflow.version == captured + 1
        assert case.workflow_version == captured
        preview = migration_preview(case, loads(workflow.config_json, {}))
        assert preview["eligible"] is True


def test_portal_create_update_and_case_audit_capture_requester_exchange(client):
    page = client.get("/portal?workflow=vendor_onboarding")
    assert page.status_code == 200
    token_match = re.search(r'name="csrf_token" value="([^"]+)"', page.text)
    assert token_match
    token = token_match.group(1)
    data = {
        "csrf_token": token,
        "workflow_key": "vendor_onboarding",
        "title": "Portal vendor request",
        "requester_name": "Portal User",
        "requester_email": "portal.user@example.com",
        "description": "Submitted through self-service.",
        "field__vendor_name": "Portal Vendor",
        "field__service_category": "Software",
        "field__annual_spend": "12000",
        "field__country": "United States",
        "field__business_owner": "Operations",
        "field__requested_start_date": (date.today() + timedelta(days=14)).isoformat(),
        "field__risk_notes": "None",
    }
    response = client.post("/portal/requests", data=data, follow_redirects=False)
    assert response.status_code == 303
    location = response.headers["location"]
    ref = location.rsplit("/", 1)[-1]
    detail = client.get(location)
    assert detail.status_code == 200 and ref in detail.text
    token2 = re.search(r'name="csrf_token" value="([^"]+)"', detail.text).group(1)
    response = client.post(f"/portal/cases/{ref}/updates", data={"csrf_token": token2, "body": "Additional requester evidence context."}, follow_redirects=False)
    assert response.status_code == 303
    with _session(client) as db:
        case = db.scalar(select(CaseRecord).where(CaseRecord.reference == ref))
        assert db.scalar(select(RequesterUpdate).where(RequesterUpdate.case_id == case.id)) is not None
        export = build_case_audit_export(db, case)
        assert export["requester_updates"][0]["body"].startswith("Additional requester")


def test_process_intelligence_case_assist_and_capability_apis(client):
    cap = client.get("/api/v1/capabilities")
    assert cap.status_code == 200
    value = cap.json()
    assert value["workflow_studio"] is True and value["requester_portal"] is True
    insights = client.get("/api/v1/process-intelligence")
    assert insights.status_code == 200 and insights.json()["case_count"] >= 9
    with _session(client) as db:
        case = db.scalar(select(CaseRecord).where(CaseRecord.completed_at.is_(None)).order_by(CaseRecord.id))
        ref = case.reference
    assist = client.get(f"/api/v1/cases/{ref}/assist")
    assert assist.status_code == 200
    assert assist.json()["applied"] is False
    assert "authorized user action" in assist.json()["safety"]


def test_skill_aware_assignment_and_canary_deployment(client):
    with _session(client) as db:
        workflow = db.scalar(select(WorkflowDefinition).where(WorkflowDefinition.key == "vendor_onboarding"))
        admin = db.scalar(select(User).where(User.role == "Administrator"))
        risk = db.scalar(select(User).where(User.role == "Risk Reviewer"))
        assert "vendor_risk" in loads(risk.skills_json, [])

        # Publish a new revision at 0% rollout so new requests deterministically retain the baseline.
        baseline = workflow.version
        config = loads(workflow.config_json, {})
        config["deployment"] = {"rollout_percent": 0, "baseline_version": baseline}
        draft = save_draft(db, workflow, config, admin, "Canary deployment test")
        publish_draft(db, workflow, draft, admin)
        assert workflow.version == baseline + 1
        case = create_case(db, workflow, admin, title="Canary baseline case", description="", requester_name="Canary", requester_email="canary@example.com", priority="Medium", field_values=_vendor_payload())
        assert case.workflow_version == baseline
        transition_case(db, case, admin, "validation")
        transition_case(db, case, admin, "due_diligence")
        assert case.assignee_id == risk.id


def test_external_case_assist_is_explicit_redacted_and_non_executable(client, monkeypatch):
    from app.services import assist_remote

    with _session(client) as db:
        case = db.scalar(select(CaseRecord).where(CaseRecord.completed_at.is_(None)).order_by(CaseRecord.id))
        payload = assist_remote.build_redacted_assist_payload(db, case)
        serialized = str(payload).lower()
        assert case.requester_email.lower() not in serialized
        assert case.requester_name.lower() not in serialized
        assert case.title.lower() not in serialized
        assert "description" not in payload
        assert "comments" not in payload
        assert "attachments" not in payload

        monkeypatch.setenv("WORKFLOW_ASSIST_HTTPS_ENDPOINT", "https://advisor.example.com/case")
        monkeypatch.delenv("WORKFLOW_ASSIST_SECRET_ENV", raising=False)
        monkeypatch.setattr(assist_remote, "validate_https_endpoint", lambda url, **_: url)

        class FakeResponse:
            status_code = 200
            content = b'{"summary":["Stage risk is elevated."],"suggestions":[{"kind":"risk","confidence":0.92,"title":"Review SLA risk","rationale":"The case is approaching its stage deadline.","action":{"type":"set_priority","value":"Critical"}}]}'
            def raise_for_status(self):
                return None

        class FakeClient:
            def __init__(self, *args, **kwargs):
                self.payload = None
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def post(self, endpoint, json=None, headers=None):
                assert endpoint.startswith("https://")
                assert case.requester_email not in str(json)
                assert case.requester_name not in str(json)
                return FakeResponse()

        monkeypatch.setattr(assist_remote.httpx, "Client", FakeClient)
        result = assist_remote.request_remote_advisory(db, case)
        assert result["provider"] == "external_guarded_advisory"
        assert result["applied"] is False
        assert result["suggestions"][0]["action"] == {"type": "none"}


def test_oidc_mode_keeps_health_public_and_requires_session_for_case_apis(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from app.config import Settings
    from app.main import create_app

    monkeypatch.setenv("WORKFLOW_AUTH_MODE", "oidc")
    monkeypatch.setenv("WORKFLOW_OIDC_ISSUER", "https://identity.example.com")
    monkeypatch.setenv("WORKFLOW_OIDC_CLIENT_ID", "portfolio-client")
    settings = Settings(
        root=tmp_path,
        database_path=tmp_path / "data" / "oidc_test.db",
        testing=True,
        seed_demo=True,
    )
    application = create_app(settings)
    with TestClient(application) as oidc_client:
        assert oidc_client.get("/api/v1/health").status_code == 200
        assert oidc_client.get("/api/v1/ready").status_code == 200
        assert oidc_client.get("/api/v1/capabilities").status_code == 401
        assert oidc_client.get("/api/v1/process-intelligence").status_code == 401
        with application.state.SessionLocal() as db:
            case = db.scalar(select(CaseRecord).order_by(CaseRecord.id))
            ref = case.reference
        assert oidc_client.get(f"/api/v1/cases/{ref}/assist").status_code == 401
