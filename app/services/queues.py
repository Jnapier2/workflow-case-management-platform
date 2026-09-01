"""Saved work queues, delegation windows, and capacity-aware assignment helpers."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import CaseRecord, Delegation, SavedQueue, User
from app.services.common import as_utc, dumps, loads, utcnow


def save_queue(db: Session, user: User, name: str, filters: dict[str, Any], *, shared: bool = False) -> SavedQueue:
    name = name.strip()[:120]
    if not name:
        raise ValueError("Queue name is required.")
    item = SavedQueue(user_id=user.id, name=name, filters_json=dumps(filters), shared=bool(shared))
    db.add(item)
    db.flush()
    return item


def available_queues(db: Session, user: User) -> list[SavedQueue]:
    return list(db.scalars(select(SavedQueue).where((SavedQueue.user_id == user.id) | (SavedQueue.shared.is_(True))).order_by(SavedQueue.shared.desc(), SavedQueue.name)))


def active_delegate(db: Session, user: User, now: datetime | None = None) -> User | None:
    now = as_utc(now or utcnow())
    item = db.scalar(select(Delegation).where(Delegation.from_user_id == user.id, Delegation.active.is_(True), Delegation.starts_at <= now, Delegation.ends_at >= now).order_by(Delegation.starts_at.desc()))
    return db.get(User, item.to_user_id) if item else None


def create_delegation(db: Session, from_user: User, to_user: User, starts_at: datetime, ends_at: datetime, reason: str = "") -> Delegation:
    if from_user.id == to_user.id:
        raise ValueError("A user cannot delegate work to themselves.")
    if ends_at <= starts_at:
        raise ValueError("Delegation end must be after its start.")
    item = Delegation(from_user_id=from_user.id, to_user_id=to_user.id, starts_at=starts_at, ends_at=ends_at, reason=reason.strip()[:240], active=True)
    db.add(item)
    db.flush()
    return item


def workload(db: Session, user: User) -> dict[str, int]:
    open_count = int(db.scalar(select(func.count(CaseRecord.id)).where(CaseRecord.assignee_id == user.id, CaseRecord.completed_at.is_(None))) or 0)
    capacity = max(1, int(user.capacity or 10))
    return {"open": open_count, "capacity": capacity, "available": max(0, capacity - open_count), "utilization_percent": min(999, round(open_count * 100 / capacity))}


def queue_filters(item: SavedQueue) -> dict[str, Any]:
    value = loads(item.filters_json, {})
    return value if isinstance(value, dict) else {}
