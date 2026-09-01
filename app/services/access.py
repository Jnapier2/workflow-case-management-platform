"""Central role-based access policy for case and administration capabilities."""
from __future__ import annotations

from app.models import User


ROLE_PERMISSIONS: dict[str, set[str]] = {
    "Administrator": {"*"},
    "Case Manager": {"case.view", "case.create", "case.assign", "case.priority", "case.comment", "case.evidence", "case.relate", "case.bulk", "sla.manage", "queue.manage", "assist.use"},
    "Procurement Analyst": {"case.view", "case.comment", "case.evidence", "approval.decide", "assist.use"},
    "Risk Reviewer": {"case.view", "case.comment", "case.evidence", "approval.decide", "assist.use"},
    "Finance Approver": {"case.view", "case.comment", "approval.decide", "assist.use"},
    "Access Coordinator": {"case.view", "case.create", "case.assign", "case.priority", "case.comment", "case.evidence", "case.relate", "case.bulk", "sla.manage", "queue.manage", "assist.use"},
    "Application Owner": {"case.view", "case.comment", "approval.decide", "assist.use"},
    "Customer Care Manager": {"case.view", "case.create", "case.assign", "case.priority", "case.comment", "case.evidence", "case.relate", "case.bulk", "sla.manage", "queue.manage", "assist.use"},
    "Quality Reviewer": {"case.view", "case.comment", "case.evidence", "approval.decide", "assist.use"},
    "Manager": {"case.view", "case.create", "case.assign", "case.priority", "case.comment", "case.evidence", "case.relate", "case.bulk", "sla.manage", "queue.manage", "assist.use"},
    "Security Reviewer": {"case.view", "case.comment", "case.evidence", "approval.decide", "assist.use"},
    "IT Provisioner": {"case.view", "case.comment", "case.evidence", "assist.use"},
    "Customer Care Analyst": {"case.view", "case.comment", "case.evidence", "assist.use"},
    "Complaint Investigator": {"case.view", "case.comment", "case.evidence", "approval.decide", "assist.use"},
    "External Requester": set(),
}


def can(actor: User | None, permission: str) -> bool:
    if actor is None or not actor.active:
        return False
    values = ROLE_PERMISSIONS.get(actor.role, set())
    return "*" in values or permission in values


def permissions_for(actor: User | None) -> set[str]:
    if actor is None:
        return set()
    return set(ROLE_PERMISSIONS.get(actor.role, set()))
