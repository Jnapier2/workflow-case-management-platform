"""Deterministic demonstration data for the portfolio experience.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Approval, BusinessRuleSet, CaseRecord, Comment, IntegrationConnector, KnowledgeArticle, SavedQueue, User, WorkflowDefinition
from app.services.audit import append_activity
from app.services.common import dumps, utcnow
from app.services.rules import save_rule_set
from app.services.workflow_engine import (
    create_case,
    decide_approval,
    import_workflow_definition,
    transition_case,
)


DEMO_USERS = [
    ("Devon Carter", "devon.carter@example.test", "Administrator"),
    ("Priya Shah", "priya.shah@example.test", "Case Manager"),
    ("Marcus Chen", "marcus.chen@example.test", "Procurement Analyst"),
    ("Elena Ruiz", "elena.ruiz@example.test", "Risk Reviewer"),
    ("Amina Brooks", "amina.brooks@example.test", "Finance Approver"),
    ("Jordan Lee", "jordan.lee@example.test", "Access Coordinator"),
    ("Taylor Morgan", "taylor.morgan@example.test", "Manager"),
    ("Noah Williams", "noah.williams@example.test", "Security Reviewer"),
    ("Sofia Patel", "sofia.patel@example.test", "IT Provisioner"),
    ("Maya Thompson", "maya.thompson@example.test", "Customer Care Analyst"),
    ("Owen Kim", "owen.kim@example.test", "Complaint Investigator"),
    ("Grace Johnson", "grace.johnson@example.test", "Customer Care Manager"),
]


def _load_examples(root: Path) -> list[dict]:
    values: list[dict] = []
    for path in sorted((root / "examples").glob("*.json")):
        values.append(json.loads(path.read_text(encoding="utf-8")))
    return values


def _user_by_role(db: Session, role: str) -> User:
    user = db.scalar(select(User).where(User.role == role).order_by(User.id.asc()))
    if not user:
        raise RuntimeError(f"Demo user role missing: {role}")
    return user


def _workflow(db: Session, key: str) -> WorkflowDefinition:
    value = db.scalar(select(WorkflowDefinition).where(WorkflowDefinition.key == key))
    if not value:
        raise RuntimeError(f"Demo workflow missing: {key}")
    return value


def _approve_pending(db: Session, case: CaseRecord, admin: User) -> None:
    for approval in db.scalars(
        select(Approval).where(Approval.case_id == case.id, Approval.status == "Pending")
    ):
        decide_approval(db, approval, admin, "Approved", "Approved for the portfolio demonstration.")


def seed_database(db: Session, root: Path) -> None:
    if not db.scalar(select(func.count(User.id))):
        for name, email, role in DEMO_USERS:
            db.add(User(name=name, email=email, role=role, active=True))
        db.flush()

    demo_skills = {
        "Case Manager": ["case_management", "escalation"],
        "Procurement Analyst": ["vendor_onboarding", "procurement"],
        "Risk Reviewer": ["vendor_risk", "privacy_review"],
        "Finance Approver": ["financial_review"],
        "Access Coordinator": ["access_governance"],
        "Security Reviewer": ["security_review"],
        "IT Provisioner": ["access_provisioning"],
        "Customer Care Analyst": ["complaint_triage"],
        "Complaint Investigator": ["complaint_investigation"],
        "Customer Care Manager": ["complaint_management", "escalation"],
    }
    for user in db.scalars(select(User).where(User.active.is_(True))):
        if (not user.skills_json or user.skills_json == "[]") and user.role in demo_skills:
            user.skills_json = dumps(demo_skills[user.role])

    for config in _load_examples(root):
        existing = db.scalar(select(WorkflowDefinition).where(WorkflowDefinition.key == config.get("key")))
        if existing is None or int(config.get("version", 1)) > existing.version:
            import_workflow_definition(db, config)
    db.flush()

    # Reusable business policy is seeded separately from workflow orchestration.
    save_rule_set(
        db, key="vendor_risk", name="Vendor risk and value routing",
        description="Classifies sensitive/high-value vendor requests without embedding policy in Python.",
        rules=[
            {"name":"Sensitive data","all":[{"field":"handles_personal_data","operator":"truthy"}],"actions":[{"type":"set_priority","value":"High"},{"type":"add_tag","value":"data_sensitive"}]},
            {"name":"High value vendor","all":[{"field":"annual_spend","operator":"gte","value":250000}],"actions":[{"type":"set_priority","value":"High"},{"type":"add_tag","value":"high_value"},{"type":"require_approval","stage":"approvals","key":"executive_finance_review","name":"Executive finance review","role":"Finance Approver"}]},
        ],
    )
    save_rule_set(
        db, key="access_risk", name="Access risk classification",
        description="Classifies elevated employee access requests.",
        rules=[
            {"name":"Contributor access","all":[{"field":"access_level","operator":"eq","value":"Contribute"}],"actions":[{"type":"set_priority","value":"High"},{"type":"add_tag","value":"elevated_access"}]},
        ],
    )
    save_rule_set(
        db, key="complaint_priority", name="Complaint severity classification",
        description="Raises severe/privacy complaints for faster handling.",
        rules=[
            {"name":"Critical impact","all":[{"field":"customer_impact","operator":"eq","value":"Critical"}],"actions":[{"type":"set_priority","value":"Critical"},{"type":"add_tag","value":"critical_impact"}]},
            {"name":"Privacy complaint","all":[{"field":"category","operator":"eq","value":"Privacy"}],"actions":[{"type":"set_priority","value":"Critical"},{"type":"add_tag","value":"privacy"}]},
        ],
    )

    connector = db.scalar(select(IntegrationConnector).where(IntegrationConnector.key == "portfolio_audit_log"))
    if connector is None:
        db.add(IntegrationConnector(key="portfolio_audit_log", name="Portfolio audit connector", kind="log", enabled=True, config_json="{}"))

    articles = [
        ("vendor-onboarding-guide", "Vendor onboarding guide", "What information to gather before submitting a vendor request.", "Provide the vendor legal name, service category, expected annual spend, business owner, requested start date, and any known data-handling or operational risks.", "vendor_onboarding"),
        ("access-request-guide", "Employee access request guide", "How to request governed system access.", "Use the minimum access level required for the work, include a clear business justification, and specify an end date for temporary access.", "employee_access_request"),
        ("complaint-intake-guide", "Customer complaint intake guide", "What helps an investigation start quickly.", "Record the incident date, impact, channel, category, contact information, and a concise factual description. Attach evidence when available.", "customer_complaint"),
    ]
    for slug, title, summary, body, workflow_key in articles:
        if db.scalar(select(KnowledgeArticle).where(KnowledgeArticle.slug == slug)) is None:
            db.add(KnowledgeArticle(slug=slug, title=title, summary=summary, body=body, workflow_key=workflow_key, published=True))

    db.flush()
    admin = _user_by_role(db, "Administrator")
    if not db.scalar(select(func.count(SavedQueue.id))):
        db.add_all([
            SavedQueue(user_id=admin.id, name="SLA at risk", filters_json=dumps({"due":"at_risk"}), shared=True),
            SavedQueue(user_id=admin.id, name="Critical open cases", filters_json=dumps({"priority":"Critical"}), shared=True),
            SavedQueue(user_id=admin.id, name="Unassigned work", filters_json=dumps({"assignee":"unassigned"}), shared=True),
        ])
    db.flush()

    if db.scalar(select(func.count(CaseRecord.id))):
        db.commit()
        return

    admin = _user_by_role(db, "Administrator")
    now = utcnow()
    today = date.today()

    vendor = _workflow(db, "vendor_onboarding")
    access = _workflow(db, "employee_access_request")
    complaint = _workflow(db, "customer_complaint")

    northstar = create_case(
        db,
        vendor,
        admin,
        title="Onboard Northstar Analytics",
        description="Analytics services for regional operations reporting.",
        requester_name="Morgan Ellis",
        requester_email="morgan.ellis@example.test",
        priority="Medium",
        field_values={
            "vendor_name": "Northstar Analytics",
            "service_category": "Professional Services",
            "annual_spend": 32000,
            "handles_personal_data": False,
            "country": "United States",
            "business_owner": "Regional Operations",
            "requested_start_date": str(today + timedelta(days=21)),
            "risk_notes": "Standard professional-services engagement.",
        },
    )
    transition_case(db, northstar, admin, "validation", "Initial intake completed.")
    northstar.stage_started_at = now - timedelta(hours=9)
    northstar.stage_due_at = now + timedelta(hours=15)

    summit = create_case(
        db,
        vendor,
        admin,
        title="Onboard Summit Logistics",
        description="Third-party fulfillment and reverse-logistics provider.",
        requester_name="Avery Robinson",
        requester_email="avery.robinson@example.test",
        priority="High",
        field_values={
            "vendor_name": "Summit Logistics",
            "service_category": "Logistics",
            "annual_spend": 146000,
            "handles_personal_data": True,
            "country": "Canada",
            "business_owner": "Supply Chain",
            "requested_start_date": str(today + timedelta(days=10)),
            "risk_notes": "Will receive customer return labels and contact information.",
        },
    )
    transition_case(db, summit, admin, "validation", "Intake complete.")
    transition_case(db, summit, admin, "due_diligence", "Privacy and logistics risk review required.")
    summit.stage_started_at = now - timedelta(hours=86)
    summit.stage_due_at = now - timedelta(hours=14)
    summit.overall_due_at = now + timedelta(hours=26)

    blue_peak = create_case(
        db,
        vendor,
        admin,
        title="Onboard Blue Peak Creative",
        description="Creative production partner for digital campaigns.",
        requester_name="Riley Carter",
        requester_email="riley.carter@example.test",
        priority="High",
        field_values={
            "vendor_name": "Blue Peak Creative",
            "service_category": "Marketing",
            "annual_spend": 85000,
            "handles_personal_data": True,
            "country": "United States",
            "business_owner": "Brand Operations",
            "requested_start_date": str(today + timedelta(days=18)),
            "risk_notes": "Agency will access campaign audience files in a controlled workspace.",
        },
    )
    transition_case(db, blue_peak, admin, "validation", "Required fields present.")
    transition_case(db, blue_peak, admin, "due_diligence", "Risk review completed.")
    transition_case(db, blue_peak, admin, "approvals", "Conditional approvals requested.")
    blue_peak.stage_started_at = now - timedelta(hours=30)
    blue_peak.stage_due_at = now + timedelta(hours=18)

    lakefront = create_case(
        db,
        vendor,
        admin,
        title="Onboard Lakefront Facilities",
        description="Preventive maintenance services for office locations.",
        requester_name="Casey Nguyen",
        requester_email="casey.nguyen@example.test",
        priority="Low",
        field_values={
            "vendor_name": "Lakefront Facilities",
            "service_category": "Facilities",
            "annual_spend": 18000,
            "handles_personal_data": False,
            "country": "United States",
            "business_owner": "Facilities",
            "requested_start_date": str(today - timedelta(days=5)),
            "risk_notes": "No system or personal-data access.",
        },
    )
    transition_case(db, lakefront, admin, "validation")
    transition_case(db, lakefront, admin, "due_diligence")
    transition_case(db, lakefront, admin, "approvals")
    _approve_pending(db, lakefront, admin)
    transition_case(db, lakefront, admin, "contracting")
    transition_case(db, lakefront, admin, "completed", "Agreement executed and evidence recorded.")
    lakefront.created_at = now - timedelta(days=8)
    lakefront.completed_at = now - timedelta(days=2)
    lakefront.closed_on_time = True

    payroll = create_case(
        db,
        access,
        admin,
        title="Payroll reporting access",
        description="Read-only reporting access for monthly reconciliation.",
        requester_name="Jamie Davis",
        requester_email="jamie.davis@example.test",
        priority="Medium",
        field_values={
            "employee_name": "Jamie Davis",
            "employee_email": "jamie.davis@example.test",
            "system_name": "Payroll Reporting Workspace",
            "access_level": "Read",
            "manager_name": "Taylor Morgan",
            "justification": "Monthly payroll-to-ledger reconciliation.",
            "end_date": str(today + timedelta(days=180)),
        },
    )
    transition_case(db, payroll, admin, "manager_approval", "Manager review requested.")
    payroll.stage_started_at = now - timedelta(hours=4)

    finance_dw = create_case(
        db,
        access,
        admin,
        title="Finance data warehouse access",
        description="Contributor access for an approved reporting project.",
        requester_name="Alex Garcia",
        requester_email="alex.garcia@example.test",
        priority="Critical",
        field_values={
            "employee_name": "Alex Garcia",
            "employee_email": "alex.garcia@example.test",
            "system_name": "Finance Data Warehouse",
            "access_level": "Contribute",
            "manager_name": "Taylor Morgan",
            "justification": "Build governed finance reporting transformations.",
            "end_date": str(today + timedelta(days=90)),
        },
    )
    transition_case(db, finance_dw, admin, "manager_approval")
    _approve_pending(db, finance_dw, admin)
    transition_case(db, finance_dw, admin, "security_review")
    finance_dw.stage_started_at = now - timedelta(hours=36)
    finance_dw.stage_due_at = now - timedelta(hours=12)
    finance_dw.overall_due_at = now + timedelta(hours=20)

    billing = create_case(
        db,
        complaint,
        admin,
        title="Duplicate billing complaint",
        description="Customer reports two charges for one service period.",
        requester_name="Sam Wilson",
        requester_email="sam.wilson@example.test",
        priority="High",
        field_values={
            "customer_name": "Sam Wilson",
            "contact_email": "sam.wilson@example.test",
            "channel": "Web form",
            "category": "Billing",
            "customer_impact": "High",
            "incident_date": str(today - timedelta(days=3)),
            "complaint_details": "Two identical charges appeared after a plan change.",
        },
    )
    transition_case(db, billing, admin, "triage")
    transition_case(db, billing, admin, "investigation")
    billing.stage_started_at = now - timedelta(hours=20)
    billing.stage_due_at = now + timedelta(hours=28)

    outage = create_case(
        db,
        complaint,
        admin,
        title="Service outage complaint",
        description="Customer requested explanation and service credit after an outage.",
        requester_name="Drew Allen",
        requester_email="drew.allen@example.test",
        priority="Medium",
        field_values={
            "customer_name": "Drew Allen",
            "contact_email": "drew.allen@example.test",
            "channel": "Phone",
            "category": "Service quality",
            "customer_impact": "Moderate",
            "incident_date": str(today - timedelta(days=12)),
            "complaint_details": "Service was unavailable for approximately three hours.",
        },
    )
    transition_case(db, outage, admin, "triage")
    transition_case(db, outage, admin, "investigation")
    transition_case(db, outage, admin, "resolution")
    _approve_pending(db, outage, admin)
    transition_case(db, outage, admin, "closed", "Response issued and credit confirmed.")
    outage.created_at = now - timedelta(days=11)
    outage.completed_at = now - timedelta(days=7)
    outage.closed_on_time = True

    privacy = create_case(
        db,
        complaint,
        admin,
        title="Privacy handling complaint",
        description="Customer alleges that an account document was sent to the wrong address.",
        requester_name="Robin Harris",
        requester_email="robin.harris@example.test",
        priority="Critical",
        field_values={
            "customer_name": "Robin Harris",
            "contact_email": "robin.harris@example.test",
            "channel": "Regulator",
            "category": "Privacy",
            "customer_impact": "Critical",
            "incident_date": str(today - timedelta(days=5)),
            "complaint_details": "A document may have been delivered to an outdated postal address.",
        },
    )
    transition_case(db, privacy, admin, "triage")
    transition_case(db, privacy, admin, "investigation")
    transition_case(db, privacy, admin, "resolution")
    privacy.stage_started_at = now - timedelta(hours=18)
    privacy.stage_due_at = now + timedelta(hours=6)

    comment = Comment(
        case_id=summit.id,
        actor_id=_user_by_role(db, "Risk Reviewer").id,
        body="Requested confirmation of the vendor's retention schedule and subcontractor list.",
    )
    db.add(comment)
    db.flush()
    append_activity(
        db,
        summit,
        _user_by_role(db, "Risk Reviewer"),
        "comment_added",
        "A comment was added",
        {"comment_id": comment.id},
    )

    db.commit()
