"""Relational data model for workflow definitions, cases, collaboration, and audit evidence.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app.runtime_utils import utcnow


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("oidc_issuer", "oidc_subject", name="uq_user_oidc_identity"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    role: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    capacity: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    skills_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    oidc_issuer: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    oidc_subject: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    assigned_cases: Mapped[list["CaseRecord"]] = relationship(back_populates="assignee", foreign_keys="CaseRecord.assignee_id")


class WorkflowDefinition(Base):
    __tablename__ = "workflow_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    cases: Mapped[list["CaseRecord"]] = relationship(back_populates="workflow")


class WorkflowRevision(Base):
    """Draft/published workflow versions without rewriting active case snapshots."""

    __tablename__ = "workflow_revisions"
    __table_args__ = (
        UniqueConstraint("workflow_key", "version", name="uq_workflow_revision_version"),
        Index("ix_workflow_revision_key_status", "workflow_key", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    workflow_key: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="Draft", index=True)
    config_json: Mapped[str] = mapped_column(Text, nullable=False)
    change_note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    published_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    created_by: Mapped[Optional[User]] = relationship(foreign_keys=[created_by_id])


class BusinessRuleSet(Base):
    __tablename__ = "business_rule_sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    rules_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class CaseRecord(Base):
    __tablename__ = "cases"
    __table_args__ = (
        Index("ix_cases_status_due", "status", "stage_due_at"),
        Index("ix_cases_workflow_status", "workflow_id", "status"),
        Index("ix_cases_completed_stage_due", "completed_at", "stage_due_at"),
        Index("ix_cases_completed_overall_due", "completed_at", "overall_due_at"),
        Index("ix_cases_assignee_completed", "assignee_id", "completed_at"),
        Index("ix_cases_updated_at", "updated_at"),
        Index("ix_cases_completed_created", "completed_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    reference: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    workflow_id: Mapped[int] = mapped_column(ForeignKey("workflow_definitions.id"), nullable=False)
    workflow_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    requester_name: Mapped[str] = mapped_column(String(160), nullable=False)
    requester_email: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    priority: Mapped[str] = mapped_column(String(20), nullable=False, default="Medium", index=True)
    assignee_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    field_values_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    workflow_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    tags_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    overall_due_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    stage_due_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    stage_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    sla_paused_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    sla_pause_reason: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    sla_warning_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    escalation_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    escalated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_on_time: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)

    workflow: Mapped[WorkflowDefinition] = relationship(back_populates="cases")
    assignee: Mapped[Optional[User]] = relationship(back_populates="assigned_cases", foreign_keys=[assignee_id])
    approvals: Mapped[list["Approval"]] = relationship(back_populates="case", cascade="all, delete-orphan")
    comments: Mapped[list["Comment"]] = relationship(back_populates="case", cascade="all, delete-orphan")
    attachments: Mapped[list["Attachment"]] = relationship(back_populates="case", cascade="all, delete-orphan")
    activities: Mapped[list["Activity"]] = relationship(back_populates="case", cascade="all, delete-orphan")
    notifications: Mapped[list["Notification"]] = relationship(back_populates="case", cascade="all, delete-orphan")
    requester_updates: Mapped[list["RequesterUpdate"]] = relationship(back_populates="case", cascade="all, delete-orphan")


class CaseRelation(Base):
    __tablename__ = "case_relations"
    __table_args__ = (
        UniqueConstraint("source_case_id", "target_case_id", "relation_type", name="uq_case_relation"),
        Index("ix_case_relation_source", "source_case_id", "relation_type"),
        Index("ix_case_relation_target", "target_case_id", "relation_type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), nullable=False)
    target_case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), nullable=False)
    relation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    note: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    source_case: Mapped[CaseRecord] = relationship(foreign_keys=[source_case_id])
    target_case: Mapped[CaseRecord] = relationship(foreign_keys=[target_case_id])
    created_by: Mapped[Optional[User]] = relationship(foreign_keys=[created_by_id])


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        UniqueConstraint("case_id", "stage_key", "approval_key", name="uq_case_stage_approval"),
        Index("ix_approvals_status_case", "status", "case_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), nullable=False, index=True)
    stage_key: Mapped[str] = mapped_column(String(80), nullable=False)
    approval_key: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(180), nullable=False)
    assigned_role: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="Pending", index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    decided_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    decided_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    decision_note: Mapped[str] = mapped_column(Text, nullable=False, default="")

    case: Mapped[CaseRecord] = relationship(back_populates="approvals")
    decided_by: Mapped[Optional[User]] = relationship(foreign_keys=[decided_by_id])


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), nullable=False, index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    case: Mapped[CaseRecord] = relationship(back_populates="comments")
    actor: Mapped[User] = relationship(foreign_keys=[actor_id])


class RequesterUpdate(Base):
    __tablename__ = "requester_updates"
    __table_args__ = (Index("ix_requester_update_case_created", "case_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), nullable=False, index=True)
    requester_name: Mapped[str] = mapped_column(String(160), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    case: Mapped[CaseRecord] = relationship(back_populates="requester_updates")


class Attachment(Base):
    __tablename__ = "attachments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), nullable=False, index=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    original_name: Mapped[str] = mapped_column(String(255), nullable=False)
    stored_name: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_backend: Mapped[str] = mapped_column(String(40), nullable=False, default="local")
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    integrity_status: Mapped[str] = mapped_column(String(30), nullable=False, default="Verified")
    scan_status: Mapped[str] = mapped_column(String(30), nullable=False, default="NotConfigured")
    content_type: Mapped[str] = mapped_column(String(160), nullable=False, default="application/octet-stream")
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    case: Mapped[CaseRecord] = relationship(back_populates="attachments")
    actor: Mapped[User] = relationship(foreign_keys=[actor_id])


class Activity(Base):
    __tablename__ = "activities"
    __table_args__ = (Index("ix_activity_case_created", "case_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), nullable=False, index=True)
    actor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("users.id"), nullable=True)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    summary: Mapped[str] = mapped_column(String(400), nullable=False)
    details_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    entry_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    case: Mapped[CaseRecord] = relationship(back_populates="activities")
    actor: Mapped[Optional[User]] = relationship(foreign_keys=[actor_id])


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notification_user_read", "user_id", "is_read"),
        Index("ix_notifications_user_read_created", "user_id", "is_read", "created_at"),
        Index("ix_notifications_delivery_status_created", "delivery_status", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    case_id: Mapped[Optional[int]] = mapped_column(ForeignKey("cases.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(60), nullable=False)
    message: Mapped[str] = mapped_column(String(400), nullable=False)
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    delivery_status: Mapped[str] = mapped_column(String(20), nullable=False, default="Queued")
    delivered_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    user: Mapped[User] = relationship(foreign_keys=[user_id])
    case: Mapped[Optional[CaseRecord]] = relationship(back_populates="notifications")


class SavedQueue(Base):
    __tablename__ = "saved_queues"
    __table_args__ = (Index("ix_saved_queue_user_name", "user_id", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    filters_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    shared: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)

    user: Mapped[User] = relationship(foreign_keys=[user_id])


class Delegation(Base):
    __tablename__ = "delegations"
    __table_args__ = (Index("ix_delegation_from_window", "from_user_id", "starts_at", "ends_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    from_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    to_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(240), nullable=False, default="")
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

    from_user: Mapped[User] = relationship(foreign_keys=[from_user_id])
    to_user: Mapped[User] = relationship(foreign_keys=[to_user_id])


class IntegrationConnector(Base):
    __tablename__ = "integration_connectors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(80), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    kind: Mapped[str] = mapped_column(String(40), nullable=False, default="log")
    endpoint_url: Mapped[str] = mapped_column(String(600), nullable=False, default="")
    method: Mapped[str] = mapped_column(String(10), nullable=False, default="POST")
    secret_env: Mapped[str] = mapped_column(String(120), nullable=False, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    allow_private_network: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    config_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class IntegrationExecution(Base):
    __tablename__ = "integration_executions"
    __table_args__ = (
        Index("ix_integration_exec_connector_created", "connector_id", "created_at"),
        Index("ix_integration_exec_status_created", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    connector_id: Mapped[int] = mapped_column(ForeignKey("integration_connectors.id"), nullable=False)
    case_id: Mapped[Optional[int]] = mapped_column(ForeignKey("cases.id"), nullable=True)
    correlation_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="Queued")
    http_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    response_summary: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    error_summary: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    connector: Mapped[IntegrationConnector] = relationship(foreign_keys=[connector_id])
    case: Mapped[Optional[CaseRecord]] = relationship(foreign_keys=[case_id])


class KnowledgeArticle(Base):
    __tablename__ = "knowledge_articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(120), nullable=False, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    summary: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    workflow_key: Mapped[str] = mapped_column(String(80), nullable=False, default="")
    published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)


class OutboxJob(Base):
    """Durable restart-safe work item for notifications, SLA scans, and integrations."""

    __tablename__ = "outbox_jobs"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_outbox_dedupe_key"),
        Index("ix_outbox_status_available", "status", "available_at"),
        Index("ix_outbox_status_priority_available", "status", "priority", "available_at"),
        Index("ix_outbox_status_locked", "status", "locked_at"),
        Index("ix_outbox_created_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(80), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    dedupe_key: Mapped[Optional[str]] = mapped_column(String(180), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="Pending")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    locked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str] = mapped_column(String(500), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow)
