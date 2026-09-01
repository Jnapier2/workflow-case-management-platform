# Enterprise Feature Matrix

Version 0.5.2 / WCM-B009 maps the platform's portfolio capabilities to the operational problem each capability demonstrates.

| Capability | Implemented behavior | Portfolio value | Boundary |
|---|---|---|---|
| Visual Workflow Studio | Visual stages/transitions over the same executable JSON model; validation; draft/publish/diff/revisions | Low-code process design and governed change | Not a BPMN implementation |
| Business Rules Studio | Declarative conditions; priority/tags/routing/conditional approvals; read-only playground | Separates business policy from code | No arbitrary code/eval |
| Version governance | Immutable case snapshots, revisions, compatible case migration, deterministic canary rollout | Change governance and safe rollout | No automatic migration of running cases |
| Relationships | Parent/child, related, duplicate, blockers, child-completion gate | True case management vs simple ticketing | Duplicate detection remains advisory |
| Business-calendar SLA | Timezone, business days/hours, holidays, warnings, pause/resume, escalation policy | Operational SLA realism | Calendar changes do not rewrite captured case history |
| Work queues | Saved/shared queues, capacity, skills, delegation, bulk actions | Agent/operations workspace | Bulk transitions still enforce workflow gates |
| Durable integrations | Log, HTTPS webhook, email-adapter contract through persistent outbox | Integration architecture and idempotency | Live SMTP not claimed |
| Requester Portal | Intake, status, milestones, updates, evidence, knowledge | Self-service/requester experience | Local lookup is demo-grade unless OIDC is enabled |
| Process intelligence | Variants, dwell/cycle percentiles, rework, approvals, touches, escalation reasons | Continuous process improvement | Heuristic opportunities, not causal proof |
| Case Assist | Local deterministic advisory plus optional redacted external HTTPS advisor | Guarded AI/decision support story | No autonomous protected actions |
| Optional OIDC | Auth code + PKCE, issuer/subject identity, supported role mapping, API session enforcement | Enterprise identity boundary | Requires real IdP/deployment qualification |
| Evidence store | Streamed bounded local adapter, generated keys, SHA-256 verification | Evidence integrity and storage abstraction | Local storage; no malware scanner/object store by default |
| Audit trail | Append-only hash-linked activity plus comprehensive audit export | Governance, traceability, investigation | Tamper-evident application chain, not external notarization |
| Recovery/diagnostics | Pre-migration backup, Recovery Doctor, Export20, soak/fault, operational health | Reliability/production-minded engineering | Does not replace enterprise DR/offsite backups |

## Architectural rule

Each capability has one execution authority. The Studio edits the same workflow model the engine executes; connector actions use the same durable outbox; process intelligence derives from existing case/audit data; Case Assist cannot bypass workflow or authorization; the requester portal creates the same `CaseRecord` used by operations.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
