# Workflow & Case Management Platform

A configurable case-management platform that turns business requests into controlled, measurable, collaborative, and auditable processes. One workflow engine supports vendor onboarding, employee access requests, customer complaints, and additional configurations without creating separate applications for each use case.

Version **0.5.2 / WCM-B009** is the Windows startup port/exit-correctness maintenance release over the B007 enterprise product layer: visual workflow and rules configuration, related/child cases, business-calendar SLAs, saved work queues, bulk actions, delegation and skill/capacity-aware routing, durable integration connectors, a requester portal and knowledge base, process intelligence, controlled workflow-version deployment, guarded Case Assist, optional OIDC identity, and a storage-adapter boundary for evidence.

## Run it on Windows

1. Extract the release ZIP into a normal writable local folder.
2. Double-click `LAUNCH_WORKFLOW_PLATFORM.bat`.
3. Press Enter for **Start local platform**, or choose another maintenance action.
4. The launcher verifies version/build agreement, the managed-file manifest, every managed SHA-256, and the one-active-launcher contract before dependency preparation or application startup.
5. On a machine without the exact project-local environment, the first launch requires internet access to create `.venv` and install `requirements.lock.txt`.
6. The runtime prefers `127.0.0.1:8010`. If an unrelated process already owns that port in demo/local mode, it selects the next free port through 8019 without stopping the holder and prints the selected local URL.
7. Set `WORKFLOW_PORT` to choose an explicit local port. OIDC mode fails closed instead of silently changing a registered redirect port.
8. The browser opens only after the selected `/api/v1/ready` endpoint reports ready.
9. Interactive maintenance actions return to the same menu instead of immediately closing the console. Export20 prints its final path and SHA-256 before returning.

Python 3.10–3.14 is supported. The launcher derives the project root from its own location, not the current working directory, Desktop, or Downloads. Runtime data remains project-local. The Windows launcher sets `PYTHONNOUSERSITE=1` and `PYTHONUTF8=1` for deterministic startup behavior.

If startup fails before the web application is ready, inspect `state/launcher_status.json`, `state/last_launcher_failure.json`, `logs/launcher.log`, and `logs/bootstrap.log`. A subsequent Export20 includes current launcher-phase evidence and bounded launcher/bootstrap/application log tails when available.

## Windows port and startup-exit repair in 0.5.2

B009 retains the B008 launcher evidence/Export20 repair and closes two additional startup correctness gaps. The server now performs a bounded, non-destructive localhost port probe: normal demo/local mode prefers 8010 but can use 8011–8019 when another application owns the preferred port. It never terminates or modifies that holder. `WORKFLOW_PORT` can select a preferred port explicitly. In OIDC mode, an occupied configured port fails closed so a registered redirect URI is not silently changed.

The server launcher also preserves nonzero Uvicorn `SystemExit` values. A bind/startup failure can no longer be converted into exit code 0 and reported as a successful Start. Normal Ctrl+C/SIGTERM cleanup still exits cleanly and removes the project-local instance lock.


## Product walkthrough

The included sample workflows support a concise walkthrough:

1. **Dashboard** — backlog, SLA, approvals, workload, cycle time, bottlenecks.
2. **Workflow Studio** — inspect the visual stages, edit the same underlying workflow model, validate a draft, compare revisions, publish, and show controlled/canary deployment settings.
3. **Rules Studio** — show declarative decision rules and use the Rule Playground to explain why a rule matches without writing case data.
4. **Requester Portal** — submit a request, review milestones, add a requester update, and view knowledge guidance.
5. **Work Queues** — saved/shared queues, workload/capacity, delegation, and transactional bulk work.
6. **Case detail** — relationships, duplicate hints, SLA pause/resume, approvals, evidence, requester exchanges, connector execution, Case Assist, and the append-only activity chain.
7. **Process Intelligence** — workflow variants, dwell times, rework, approval turnaround, cycle-time percentiles, automation/manual touches, SLA causes, and improvement opportunities.
8. **Operations** — schema/search/worker health, outbox backlog, connector outcomes, storage, Recovery Doctor status, and request timing.
9. **API Docs** — capability discovery, workflow revisions, case APIs, process intelligence, health/readiness, and audit verification.

See `docs/PORTFOLIO_DEMO_GUIDE.md` for a concise recruiter-facing script.

## Enterprise-style capabilities

### Visual Workflow Studio

The Studio edits the same validated workflow definition executed by the engine—there is no second UI-only workflow model. It provides:

- visual stage cards and transitions;
- drag/reorder support;
- human approval, gate, connector, and transition metadata;
- server-side graph validation and linting;
- JSON import/edit for transparent source-of-truth access;
- draft, publish, revision history, diff, rollback-style revision selection, controlled case migration, and deterministic canary rollout.

Each case captures the selected workflow version and immutable configuration snapshot at creation. Publishing a later revision cannot silently reinterpret historical work.

### Decision & Business Rules Studio

Business rules are stored separately from orchestration. The engine supports declarative conditions/actions rather than arbitrary Python or `eval()`:

- set priority;
- add tags;
- route to a role;
- inject a conditional approval into the individual case snapshot.

The Rule Playground evaluates sample context read-only and explains matched conditions/actions before a rule is used.

### Case relationships

Cases can be linked as:

- parent → child;
- related;
- duplicate;
- blocking/dependent.

Parent workflow transitions can require child completion. Duplicate suggestions use bounded similarity signals and remain suggestions until an authorized user links cases.

### Business-calendar SLA engine

Workflow definitions can specify timezone, working days/hours, holidays, warning thresholds, and escalation policy. Stage/overall deadlines use business time rather than raw wall-clock addition. Authorized users can pause/resume SLA clocks with an auditable reason, such as waiting on requester evidence.

Durable SLA jobs retain the priority/starvation protections introduced in the reliability foundation.

### Work queues, bulk work, delegation, skills, and capacity

The Work Queues surface supports:

- saved personal/shared filters;
- My Work/unassigned/SLA-risk style queue definitions;
- capacity-aware workload visibility;
- temporary delegation/out-of-office coverage;
- skill-aware routing;
- transactional bulk assignment, priority, tags, SLA pause/resume, and valid stage transition.

Bulk actions are all-or-nothing and still generate normal per-case audit events.

### Durable integrations

Transition connector actions use the existing persistent outbox rather than synchronous workflow-side effects. Connector types include:

- project-local audit/log adapter;
- HTTPS webhook adapter with correlation/idempotency metadata and private-network blocking by default;
- portfolio-safe email adapter contract that records durable acceptance but does not claim live SMTP delivery.

Secret values are referenced by environment-variable name, not stored in workflow definitions or diagnostic output. Connector execution appears in Operations and case audit exports.

### Requester portal & knowledge

The self-service portal is separated from the operations workspace. It supports:

- workflow-guided request submission;
- request lookup/status and milestones;
- requester updates;
- evidence upload;
- knowledge guidance.

The default local portfolio lookup is deliberately labeled demo-grade. Production-style deployment should enable the optional OIDC identity boundary.

### Process intelligence

The platform derives operational/process intelligence from its existing event history rather than maintaining a second analytics truth store. Current analysis includes:

- workflow variants/path frequency;
- stage dwell p50/p90/p95;
- case cycle-time percentiles;
- rework/return loops;
- approval turnaround;
- SLA escalation causes;
- manual touches and automation rate;
- bottleneck/improvement opportunity heuristics.

### Guarded Case Assist

Local Case Assist is deterministic and evidence-grounded. It can summarize current state and suggest SLA priority, evidence review, tagging, or duplicate review. Suggestions require an authorized user decision.

An optional external HTTPS advisory adapter is available only when explicitly configured and explicitly invoked by a user. It sends bounded operational metadata only—no requester name/email, intake free text, comments, filenames, or evidence content. Returned external suggestions are normalized to **non-executable advisory output**; an external provider cannot cause an automatic priority change, approval, spend, provisioning, deletion, or publication.

### Optional OIDC identity

Default demo mode remains zero-configuration and localhost-friendly. Optional OIDC mode adds:

- authorization-code flow with PKCE and state validation;
- HTTPS issuer discovery/token/userinfo endpoints;
- stable local identity binding by OIDC issuer + subject rather than email alone;
- group/role mapping restricted to supported platform roles;
- API session enforcement that ignores caller-supplied demo actor IDs in OIDC mode;
- secure session cookies when the configured redirect URI is HTTPS.

This is an optional portfolio integration boundary, not a claim of external identity-provider certification. See `docs/SECURITY.md`.

### Evidence storage boundary

The default evidence store remains private project-local storage for a zero-dependency demo. Uploads are streamed in bounded chunks, stored with safe generated keys, hashed with SHA-256, and verified before download. Storage backend/key/integrity/scan metadata is kept in the relational record so a private object-storage implementation can replace the local adapter later without changing workflow or audit semantics.

## Reliability foundation retained

The enterprise layer preserves the prior engineering controls:

- exactly one BAT/CMD launcher;
- fail-closed pre-runtime release identity verification;
- immutable case workflow snapshots;
- append-only SHA-256-linked activity history;
- JSON/CSV audit packages and chain verification;
- SQLite WAL, 15-second busy timeout, bounded writes, indexes, and FTS5 with safe fallback;
- optional PostgreSQL/Psycopg persistence, pooled connections, explicit migrations, GIN search, and `SKIP LOCKED` worker leasing;
- restart-safe durable outbox with priorities, idempotency, bounded retries, and stale-lease recovery;
- checked pre-migration SQLite backup;
- Recovery Doctor with isolated restore qualification;
- isolated soak/fault harness;
- same-PC duplicate-instance protection;
- UTC logging, request timing, bounded logs, and Critical-error Export20 behavior;
- current-build/freshness-aware diagnostic evidence so stale receipts cannot masquerade as present health.

## Canonical launcher actions

`LAUNCH_WORKFLOW_PLATFORM.bat` is the only active BAT/CMD:

- `start` — verify, bootstrap as needed, launch local platform;
- `doctor` — checked recovery qualification without replacing live data;
- `export` — bounded redacted Export20;
- `tests` — automated test suite;
- `soak` — isolated soak/fault qualification;
- `postgres-preflight` — read-only PostgreSQL configuration/preflight;
- `migrate` — protected schema write path; uses one terse `Action? [Y/N]` prompt;
- `verify` — release identity/integrity verification.

## Runtime locations

- `data/` — default SQLite database;
- `uploads/` — evidence bytes;
- `backups/` — managed SQLite migration/Doctor backups;
- `logs/` — bounded runtime/bootstrap logs;
- `state/` — local secret and compact health/status receipts;
- `diagnostics/` — crash capsules, staging, Export20;
- `reports/` — Recovery Doctor and soak/fault reports.

No runtime data is intentionally written to Desktop, Downloads, another project, or Google Drive. Drive is a handoff/archive destination, not runtime storage.

## Honest deployment boundaries

This release is a portfolio/recruiter-facing application and a source/archive-qualified candidate—not a certification of production readiness. Specifically:

- demo identity is not production authentication;
- optional OIDC requires a real compatible provider and deployment testing;
- the email connector is a durable adapter contract, not live SMTP delivery;
- no external AI endpoint is configured by default;
- local evidence storage is not production object storage or malware scanning;
- PostgreSQL code paths require live-server qualification before production claims;
- multi-tenancy, legal hold/records disposition, regulatory certification, public-internet hardening, and independently supervised distributed workers are not claimed;
- Norton/SmartScreen and exact physical-Windows qualification must be performed on the delivered artifact.

## Documentation

- `docs/ARCHITECTURE.md`
- `docs/DATA_MODEL.md`
- `docs/WORKFLOW_CONFIGURATION.md`
- `docs/SECURITY.md`
- `docs/OPERATIONS_AND_RECOVERY.md`
- `docs/PORTFOLIO_DEMO_GUIDE.md`
- `docs/ENTERPRISE_FEATURE_MATRIX.md`
- `docs/POSTGRESQL_DEPLOYMENT.md`
- `docs/SOAK_AND_FAULT_TESTING.md`
- `docs/ROADMAP.md`

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
