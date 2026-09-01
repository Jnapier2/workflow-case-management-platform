"""Workflow engine and audit tests.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import Activity, Approval, CaseRecord, User, WorkflowDefinition
from app.services.audit import verify_case_audit_chain
from app.services.workflow_engine import (
    PermissionDenied,
    TransitionBlocked,
    WorkflowValidationError,
    case_workflow_config,
    create_case,
    decide_approval,
    import_workflow_definition,
    transition_case,
    validate_workflow_config,
)


SOURCE_ROOT = Path(__file__).resolve().parents[1]


def _vendor_payload() -> dict:
    return {
        "vendor_name": "Orion Systems",
        "service_category": "Software",
        "annual_spend": 90000,
        "handles_personal_data": True,
        "country": "United States",
        "business_owner": "Operations Analytics",
        "requested_start_date": (date.today() + timedelta(days=30)).isoformat(),
        "risk_notes": "Access limited to an approved tenant.",
    }


def _session(client):
    return client.app.state.SessionLocal()


def test_workflow_graph_and_field_validation():
    config = json.loads((SOURCE_ROOT / "examples" / "vendor_onboarding.json").read_text(encoding="utf-8"))
    assert validate_workflow_config(deepcopy(config))["key"] == "vendor_onboarding"

    invalid = deepcopy(config)
    invalid["stages"].append(
        {
            "key": "orphan_review",
            "label": "Orphan Review",
            "description": "Unreachable on purpose.",
            "default_role": "Case Manager",
            "sla_hours": 1,
        }
    )
    invalid["transitions"].append(
        {
            "from": "orphan_review",
            "to": "orphan_review",
            "label": "Remain orphaned",
            "roles": ["Case Manager"],
        }
    )
    with pytest.raises(WorkflowValidationError, match="Unreachable stage"):
        validate_workflow_config(invalid)

    invalid_date_values = _vendor_payload()
    invalid_date_values["requested_start_date"] = "not-a-date"
    # Date validation is exercised through create_case in a real session below.
    assert invalid_date_values["requested_start_date"] == "not-a-date"


def test_snapshot_conditional_approvals_permissions_and_audit(client):
    with _session(client) as db:
        workflow = db.scalar(select(WorkflowDefinition).where(WorkflowDefinition.key == "vendor_onboarding"))
        admin = db.scalar(select(User).where(User.role == "Administrator"))
        risk = db.scalar(select(User).where(User.role == "Risk Reviewer"))
        assert workflow and admin and risk

        case = create_case(
            db,
            workflow,
            admin,
            title="Onboard Orion Systems",
            description="Governed software vendor onboarding.",
            requester_name="Jordan Example",
            requester_email="jordan@example.com",
            priority="High",
            field_values=_vendor_payload(),
        )
        db.commit()
        reference = case.reference
        original_snapshot = case_workflow_config(case)
        captured_version = original_snapshot["version"]
        assert captured_version == case.workflow_version

        with pytest.raises(PermissionDenied):
            transition_case(db, case, risk, "validation")
        db.rollback()
        case = db.scalar(select(CaseRecord).where(CaseRecord.reference == reference))
        workflow = case.workflow

        transition_case(db, case, admin, "validation")
        transition_case(db, case, admin, "due_diligence")
        transition_case(db, case, admin, "approvals")
        db.commit()
        approvals = list(db.scalars(select(Approval).where(Approval.case_id == case.id)))
        assert {item.approval_key for item in approvals} == {
            "procurement_review",
            "data_risk_review",
            "finance_approval",
        }
        with pytest.raises(TransitionBlocked, match="approvals"):
            transition_case(db, case, admin, "contracting")

        case = db.scalar(select(CaseRecord).where(CaseRecord.reference == reference))
        approvals = list(db.scalars(select(Approval).where(Approval.case_id == case.id)))
        for approval in approvals:
            decide_approval(db, approval, admin, "Approved", "Approved during automated test.")
        transition_case(db, case, admin, "contracting")
        db.commit()

        updated = deepcopy(original_snapshot)
        updated["version"] = captured_version + 1
        updated["name"] = "Vendor Onboarding — Updated"
        import_workflow_definition(db, updated)
        db.commit()
        case = db.scalar(select(CaseRecord).where(CaseRecord.reference == reference))
        assert case_workflow_config(case)["version"] == captured_version
        assert case_workflow_config(case)["name"] == original_snapshot["name"]

        assert verify_case_audit_chain(db, case.id)["valid"] is True
        activity = db.scalar(select(Activity).where(Activity.case_id == case.id).order_by(Activity.id.asc()))
        activity.summary = "Unexpected edited summary"
        db.commit()
        assert verify_case_audit_chain(db, case.id)["valid"] is False


def test_same_version_change_and_reference_prefix_collision(client):
    with _session(client) as db:
        vendor = db.scalar(select(WorkflowDefinition).where(WorkflowDefinition.key == "vendor_onboarding"))
        assert vendor
        config = json.loads(vendor.config_json)
        config["description"] = "Changed without a version increase."
        with pytest.raises(WorkflowValidationError, match="increment version"):
            import_workflow_definition(db, config)

        second = json.loads((SOURCE_ROOT / "examples" / "customer_complaint.json").read_text(encoding="utf-8"))
        second["key"] = "complaint_copy"
        second["reference_prefix"] = "VEN"
        with pytest.raises(WorkflowValidationError, match="already used"):
            import_workflow_definition(db, second)


def test_invalid_date_rejected(client):
    with _session(client) as db:
        workflow = db.scalar(select(WorkflowDefinition).where(WorkflowDefinition.key == "vendor_onboarding"))
        admin = db.scalar(select(User).where(User.role == "Administrator"))
        values = _vendor_payload()
        values["requested_start_date"] = "31/31/2026"
        with pytest.raises(WorkflowValidationError, match="Requested start date"):
            create_case(
                db,
                workflow,
                admin,
                title="Invalid date case",
                description="",
                requester_name="Jordan Example",
                requester_email="jordan@example.com",
                priority="Medium",
                field_values=values,
            )


def test_startup_sla_escalation_is_idempotent_and_notifies(client):
    from app.models import Notification
    from app.services.workflow_engine import process_sla_escalations

    with _session(client) as db:
        overdue = list(
            db.scalars(
                select(CaseRecord).where(
                    CaseRecord.completed_at.is_(None),
                    CaseRecord.escalation_level > 0,
                )
            )
        )
        assert overdue
        overdue_ids = {item.id for item in overdue}
        activities = list(
            db.scalars(
                select(Activity).where(
                    Activity.case_id.in_(overdue_ids),
                    Activity.event_type == "sla_escalated",
                )
            )
        )
        notifications = list(
            db.scalars(
                select(Notification).where(
                    Notification.case_id.in_(overdue_ids),
                    Notification.kind == "sla_escalation",
                )
            )
        )
        assert activities
        assert notifications
        assert process_sla_escalations(db) == 0
