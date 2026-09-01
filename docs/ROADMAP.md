# Roadmap

## Completed through 0.5.2

### Reliability foundation

- fail-closed release identity and one active Windows launcher;
- SQLite WAL/busy timeout/indexing and PostgreSQL-ready persistence;
- checked pre-migration backup and versioned schema ledger;
- Recovery Doctor with isolated restore verification;
- durable outbox with priority, dedupe, retries, stale-job recovery, and PostgreSQL `SKIP LOCKED` leasing;
- FTS5/indexed fallback and PostgreSQL full-text path;
- operational health, bounded logs, Critical diagnostics, Export20, soak/fault qualification;
- diagnostic evidence identity/freshness classification.

### Enterprise case-management layer

- visual workflow Studio over the executable workflow model;
- declarative Rules Studio and read-only rule explanation/playground;
- workflow draft/publish/diff/revision history, controlled migration, deterministic canary rollout;
- parent/child/related/duplicate/blocking cases;
- business-calendar SLA, warning/escalation policy, auditable pause/resume;
- saved/shared queues, bulk actions, delegation, workload capacity and skills;
- durable connector framework with HTTPS safety/idempotency;
- requester portal, requester updates, evidence, knowledge guidance;
- process intelligence and improvement-opportunity heuristics;
- guarded local Case Assist and optional redacted external advisory adapter;
- optional OIDC auth-code/PKCE, stable issuer+subject identity, role mapping, API session enforcement;
- streamed evidence-store abstraction with SHA-256 verification.

## Highest-value future production evolution

- physical-Windows/Norton/SmartScreen qualification of the exact release;
- live disposable PostgreSQL migration/rollback, backup/restore, and multi-worker qualification;
- private object-storage evidence adapter with server-side encryption, malware-scanning hook, retention policy, and signed retrieval;
- live SMTP/Teams/Slack-style notification adapters behind the durable connector contract;
- provider-specific OIDC logout/session-expiry/refresh behavior and independent security review;
- tenant/organizational-unit isolation if a real multi-customer SaaS requirement appears;
- records-retention/legal-hold/disposition policies when a concrete governance regime is defined;
- long-duration soak with thousands of cases, evidence I/O, forced process interruptions, disk pressure, and repeated restarts;
- signed/provenance-attested releases and CI security checks.

## Deliberately deferred

The current product does not need a large RPA ecosystem, hundreds of vendor-specific connectors, contact-center omnichannel, unsupervised autonomous agents, or multi-tenant billing. Those would add substantial complexity without strengthening the core case-management and audit capabilities today.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
