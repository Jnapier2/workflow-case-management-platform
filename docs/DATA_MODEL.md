# Data Model

## Schema version

The current application schema is **5**.

1. v1 — stability/query indexes.
2. v2 — notification delivery fields and persistent outbox.
3. v3 — full-text/indexed search structures.
4. v4 — durable-job priority and priority-aware due index.
5. v5 — enterprise case-management layer: workflow revisions, business rule sets, case relationships, requester updates, queues, delegation, connector definitions/executions, knowledge, capacity/skills, stable OIDC identity metadata, captured workflow version/tags/SLA pause metadata, and evidence-store metadata.

SQLite mirrors the version through `PRAGMA user_version`; all supported backends also maintain `platform_schema_state`. Future/newer schemas fail closed. Existing SQLite data is backed up and checked before an actual upgrade.

## Primary entities

### User

Local actor/account record with role, active state, capacity, skills, and optional stable OIDC `issuer + subject` binding. Email remains profile/contact data rather than the permanent external identity key.

### WorkflowDefinition

Current published workflow definition, version, display metadata, and executable configuration JSON.

### WorkflowRevision

Draft/published revision history keyed by workflow key/version. Revisions support comparison, controlled publication, case-migration preview, and deployment/canary governance without changing historical case snapshots.

### BusinessRuleSet

Reusable declarative policy rules kept separate from orchestration. Rules can classify, prioritize, route, or inject conditional approvals into an individual case's captured snapshot.

### CaseRecord

Canonical case state: reference, workflow/revision snapshot, requester identity, intake values, tags, status/priority, assignee, SLA fields, escalation, completion state, and timestamps.

### CaseRelation

Directed relationships among cases: `parent_child`, `related`, `duplicate`, and `blocks`. Parent/child relations can enforce completion gates and support roll-up display.

### Approval

Stage-level approval requirement, assigned role, decision state, decision actor/note, and timing. Conditional rule-generated approvals use the same entity and workflow gate logic.

### Comment / RequesterUpdate

Internal collaboration and requester-originated updates are separate records so the audit/export/UI can preserve communication context and channel.

### Attachment

Evidence metadata: generated stored name/key, original name, content type, size, SHA-256, actor, storage backend, integrity state, scan state, and upload time. File bytes are owned by the configured evidence-store adapter.

### Activity

Append-only business/audit event linked by previous/current SHA-256 values. It is the authoritative case event stream for traceability and process-intelligence derivation.

### Notification / OutboxJob

Notifications represent user-visible messages; `OutboxJob` is restart-safe asynchronous work with dedupe key, priority, attempts, lease state, availability, completion, and bounded error details.

### SavedQueue / Delegation

Work-management configuration for saved filters/shared queues and time-bounded delegation/out-of-office coverage.

### IntegrationConnector / IntegrationExecution

Connector definition stores safe non-secret configuration plus a reference to an optional secret environment variable. Execution records correlation/idempotency/status/timing/result metadata and are surfaced through Operations and audit export.

### KnowledgeArticle

Simple reusable requester guidance used by the self-service portal.

## Snapshot rule

Every case stores the workflow configuration that governed it at creation or an explicitly authorized compatible migration. Publishing a later workflow revision therefore cannot silently reinterpret historical stage labels, approvals, SLA policy, transition gates, or integration actions.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
