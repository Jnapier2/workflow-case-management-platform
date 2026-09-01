# Architecture

## Core principle: one authority per capability

The platform avoids parallel implementations. The visual Studio edits the same workflow JSON executed by the engine; requester intake creates the same case entity used by the operations workspace; connector work uses the durable outbox; process intelligence derives from the case/audit data already recorded; Case Assist can only propose actions that normal authorized workflow services would perform.

## Runtime layers

### Presentation/API

- FastAPI/Jinja operations UI;
- requester portal;
- Workflow/Rules/Connector Studio;
- REST/OpenAPI endpoints;
- optional OIDC session boundary;
- liveness/readiness/Operations endpoints.

### Domain services

- workflow engine and validation;
- workflow revision/deployment governance;
- declarative business rules;
- case relationships/dependencies;
- business-calendar SLA/escalation;
- queue/delegation/skill/capacity routing;
- approvals/comments/requester updates;
- connectors/outbox;
- process intelligence;
- guarded Case Assist;
- audit-chain/export;
- search/evidence storage.

### Persistence

SQLite is the zero-configuration default. PostgreSQL is optional through the same SQLAlchemy model layer, with explicit migration/preflight behavior. Schema version 5 adds the enterprise case-management entities around the existing case/audit foundation rather than replacing historical tables.

### Reliability/support

- fail-closed release gate;
- one active BAT/CMD launcher and one Python action registry;
- checked pre-migration backup;
- Recovery Doctor;
- durable worker/outbox;
- bounded logs and operational metrics;
- Export20/crash capsule;
- isolated soak/fault harness.

## Request lifecycle

1. An internal user or requester portal selects a workflow.
2. The workflow deployment selector chooses the published/canary revision deterministically.
3. Intake validation runs against that selected definition.
4. Declarative rules classify/route and may inject conditional approvals into the case's captured snapshot.
5. The case is assigned using role, skill, capacity, and active delegation information.
6. Business-calendar due dates are calculated.
7. Every material case action appends an audit-chain event.
8. Valid transitions enforce authorization, approvals, child-case prerequisites, and configured connector actions.
9. Connectors execute asynchronously through the durable outbox.
10. Requester collaboration and evidence become part of the same case/audit package.
11. Process intelligence derives path/dwell/rework metrics from recorded events.

## Workflow version governance

`WorkflowDefinition` is the current published definition; `WorkflowRevision` holds draft/published history. Existing cases retain `workflow_version` and `workflow_snapshot_json`.

A published workflow may specify a rollout percentage and baseline version. The case selector uses a stable hash of workflow/requester context to select the candidate or baseline revision. This makes rollout deterministic while ensuring already-created cases never switch revision because a later percentage changes.

Case migration is explicit, authorized, previewed for compatibility, and audited.

## Integration boundary

Integration definitions store only non-secret configuration and an environment-variable *name* for optional secret material. Transition events create `IntegrationExecution` rows and durable jobs. HTTPS targets are validated and private/loopback resolution is blocked by default.

The email connector intentionally stops at the durable adapter boundary in the local portfolio build; it does not claim SMTP delivery.

## Identity boundary

Demo mode is default. Optional OIDC mode uses authorization-code + PKCE, state, HTTPS discovery/token/userinfo, stable provider issuer+subject identity, supported role mapping, and session enforcement across UI/API protected surfaces. Userinfo is treated as provider-authenticated profile data obtained with the token returned from the configured token endpoint; the project does not claim independent OIDC conformance certification.

## Case Assist boundary

Local assist is deterministic. The optional external advisor is explicit-only and receives a redacted operational payload. External response content is bounded and normalized; executable actions are discarded. Applying any local assist action still uses ordinary role checks/domain services and creates audit evidence.

## Evidence boundary

The local evidence adapter streams bounded uploads into project-local storage using generated safe keys, calculates SHA-256 during storage, and verifies content before download. Relational metadata carries backend/key/integrity/scan state, allowing a future private object-store implementation without a second evidence model.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
