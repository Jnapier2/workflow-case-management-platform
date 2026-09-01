"""Case relationship, child-rollup, and duplicate-suggestion services."""
from __future__ import annotations

from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models import CaseRecord, CaseRelation, User
from app.services.audit import append_activity

RELATION_TYPES = {"parent_child", "related", "duplicate", "blocks"}


def create_relation(db: Session, source: CaseRecord, target: CaseRecord, relation_type: str, actor: User, note: str = "") -> CaseRelation:
    if source.id == target.id:
        raise ValueError("A case cannot be related to itself.")
    if relation_type not in RELATION_TYPES:
        raise ValueError("Select a supported case relationship.")
    existing = db.scalar(select(CaseRelation).where(CaseRelation.source_case_id == source.id, CaseRelation.target_case_id == target.id, CaseRelation.relation_type == relation_type))
    if existing:
        return existing
    relation = CaseRelation(source_case_id=source.id, target_case_id=target.id, relation_type=relation_type, note=note.strip()[:500], created_by_id=actor.id)
    db.add(relation)
    db.flush()
    append_activity(db, source, actor, "case_related", f"Related to {target.reference} ({relation_type})", {"target": target.reference, "relation_type": relation_type, "note": relation.note})
    append_activity(db, target, actor, "case_related", f"Related from {source.reference} ({relation_type})", {"source": source.reference, "relation_type": relation_type, "note": relation.note})
    return relation


def relation_summary(db: Session, case: CaseRecord) -> dict[str, Any]:
    outgoing = list(db.scalars(select(CaseRelation).where(CaseRelation.source_case_id == case.id).order_by(CaseRelation.created_at)))
    incoming = list(db.scalars(select(CaseRelation).where(CaseRelation.target_case_id == case.id).order_by(CaseRelation.created_at)))
    child_ids = [r.target_case_id for r in outgoing if r.relation_type == "parent_child"]
    children = list(db.scalars(select(CaseRecord).where(CaseRecord.id.in_(child_ids)).order_by(CaseRecord.reference))) if child_ids else []
    open_children = [c for c in children if c.completed_at is None]
    return {
        "outgoing": outgoing,
        "incoming": incoming,
        "children": children,
        "child_count": len(children),
        "open_child_count": len(open_children),
        "child_progress_percent": 100 if not children else round((len(children)-len(open_children))*100/len(children)),
    }


def incomplete_children(db: Session, case: CaseRecord) -> list[CaseRecord]:
    ids = list(db.scalars(select(CaseRelation.target_case_id).where(CaseRelation.source_case_id == case.id, CaseRelation.relation_type == "parent_child")))
    return list(db.scalars(select(CaseRecord).where(CaseRecord.id.in_(ids), CaseRecord.completed_at.is_(None)))) if ids else []


def duplicate_suggestions(db: Session, case: CaseRecord | None = None, *, workflow_id: int | None = None, requester_email: str = "", title: str = "", limit: int = 5) -> list[dict[str, Any]]:
    workflow_id = case.workflow_id if case else workflow_id
    requester_email = (case.requester_email if case else requester_email).strip().casefold()
    title = (case.title if case else title).strip()
    if not workflow_id:
        return []
    query = select(CaseRecord).where(CaseRecord.workflow_id == workflow_id)
    if case:
        query = query.where(CaseRecord.id != case.id)
    candidates = list(db.scalars(query.order_by(CaseRecord.updated_at.desc()).limit(250)))
    scored: list[dict[str, Any]] = []
    for other in candidates:
        email_match = bool(requester_email and other.requester_email.casefold() == requester_email)
        title_score = SequenceMatcher(None, title.casefold(), other.title.casefold()).ratio() if title else 0.0
        score = (0.55 if email_match else 0.0) + 0.45 * title_score
        if score >= 0.55:
            scored.append({"case": other, "score": round(score, 3), "email_match": email_match, "title_similarity": round(title_score, 3)})
    scored.sort(key=lambda item: (-item["score"], -item["case"].id))
    return scored[:limit]
