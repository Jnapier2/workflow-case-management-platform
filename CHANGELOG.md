# Changelog

## 0.5.2 — Windows Port Fallback & Startup Exit Correctness

- Added bounded localhost port fallback for demo/local startup: prefer 8010, then use the next free port through 8019 without terminating or altering the existing listener.
- Added validated `WORKFLOW_PORT` override support.
- OIDC mode fails closed on an occupied selected/registered port instead of silently changing redirect behavior.
- Fixed `run_server.py` so nonzero Uvicorn `SystemExit` values propagate as failures; only zero/normal shutdown unwind is converted to success.
- Added visible selected-local-URL output.
- Added regression coverage for occupied-port preservation/fallback, explicit port validation, and nonzero startup-exit propagation.
- Preserved schema 5, one active launcher, B008 launcher evidence/Export20 behavior, and all enterprise features.
- Recorded the newly supplied 23:36 UTC field-export checksum `7765750BB792764F55C9D3EC5EE9F34D624C4F5A72D876F7AA2EED71C7F7E5DA`; content-level promotion is intentionally not claimed without raw ZIP replay.

## 0.5.1 — Windows Launcher & Export Field Repair

- Repaired the B007 physical-Windows launcher evidence gap without changing schema 5 or enterprise business behavior.
- Added dependency-free launcher phase journaling to `state/launcher_status.json` and `logs/launcher.log`.
- Bounded child-process start failures and top-level router exceptions instead of allowing the canonical action router to terminate without durable phase evidence.
- Kept interactive maintenance actions visible and returned them to the one canonical menu, preventing a successful Export20 from looking like a console crash.
- Added explicit Export20 success path/SHA-256/elapsed output.
- Added launcher/bootstrap/application log tails plus current launcher status to bounded Export20 evidence.
- Added incomplete-package and missing-Python project-local bootstrap failure receipts in the single BAT launcher.
- Enabled `PYTHONUTF8=1` and `PYTHONNOUSERSITE=1` in the Windows launcher.
- Added field-repair regression coverage while preserving the v2.17.13 one-active-launcher contract.

## 0.5.0 — Enterprise Case Management & Process Intelligence

- Added Visual Workflow Studio over the same executable workflow model, including stage/transition helpers, validation, draft/publish/revision diff, controlled migration, and deterministic canary deployment.
- Added declarative Business Rules Studio with safe condition/action allowlists and a read-only Rule Playground; rules can classify, prioritize, route, tag, and inject conditional approvals into an individual case snapshot.
- Added parent/child, related, duplicate, and blocking case relationships, child-completion gates, roll-up progress, and duplicate suggestions.
- Added business-calendar SLA calculation with timezone, business days/hours, holidays, warning windows, escalation policy, and auditable pause/resume.
- Added saved/shared work queues, all-or-nothing bulk actions, delegation/out-of-office windows, workload capacity, required skills, and skill-aware routing.
- Added durable connector definitions/executions through the existing outbox: local log, HTTPS webhook, and portfolio-safe email adapter; secrets remain environment references.
- Added a requester-facing portal for guided intake, request status/milestones, requester updates, evidence, and knowledge guidance.
- Added process intelligence for variants, stage dwell/cycle percentiles, rework, approval turnaround, escalation causes, touches/automation, and improvement opportunities.
- Added guarded Case Assist: deterministic local advisory plus an optional explicit redacted HTTPS advisor whose returned actions are always stripped to non-executable `none`.
- Added optional OIDC authorization-code + PKCE integration with HTTPS discovery/token/userinfo, stable issuer+subject identity binding, supported-role mapping, and protected API session enforcement.
- Added streamed evidence-store abstraction with generated storage keys, SHA-256 integrity metadata, and verify-before-download behavior.
- Advanced database schema from 4 to 5 using the existing checked pre-migration backup path. Existing case/workflow/audit structures are preserved; enterprise tables/columns are additive.
- Consolidated outbound HTTPS safety checks so webhook and external-advisory adapters use one private-network/URL validation authority.
- Extended case audit export to include captured/published workflow versions, relationships, requester updates, evidence storage/integrity metadata, integration executions, and rules-driven state while retaining the append-only hash chain.
- Preserved the v2.17.13 one-active-launcher contract; `LAUNCH_WORKFLOW_PLATFORM.bat` remains the only BAT/CMD.

## 0.4.1 — Windows Field Evidence Coherence & Save-State Repair

- Reviewed the user-generated Windows 11 v0.3.1 / WCM-B004 manual Export20 dated 2026-08-31; its supplied SHA-256 matched `4AC29C40D4F7094355C75944C2D6157CCE2CB9EB8A62FB5F5457EE5CE067833D`.
- Promoted v0.3.1 / WCM-B004 to the Windows-working rollback/save-state baseline for normal startup: CPython 3.13.15, schema 4, WAL, FTS5, durable worker active, 3 workflows / 9 cases, and 96/96 release-integrity PASS.
- Fixed stale cached support evidence: the Aug. 31 Export20 carried the older Aug. 28 Recovery Doctor `FAIL`, which could make a healthy current run look unhealthy and could keep Operations in false `Attention`.
- Added one shared cached-evidence classifier for version/build identity and a 24-hour freshness window. Prior-build, legacy-unattributed, future-dated, or aged receipts cannot affect current health.
- Export20 now copies full cached status/health/Doctor/soak receipts only when they are current; stale evidence is summarized with provenance/reason in `diagnostic_summary.json`.
- Recovery Doctor, database-health, and soak/fault receipts now stamp the application version/build that produced them.
- Operations now labels stale Doctor evidence explicitly and only raises a Doctor failure signal for a current-build/current-window receipt.
- Added regression coverage for the exact stale legacy Doctor failure pattern, current Doctor inclusion, stale Operations suppression, and current Operations failure signaling.
- Preserved the v2.17.13 one-active-launcher contract and SQLite schema version 4; no data migration is introduced.

## 0.4.0 — v2.17.13 Active-Launcher & Source Consolidation

- Aligned the release metadata and engineering contract with Gateway shared defaults v2.17.13, exact source parameter SHA-256 `63BDA0B5F61BA44F18F55C5B75512085ED3A2FE67C575E3406A5877ECD5F4566`.
- Fully indexed the active package and mapped BAT/CMD references before retirement. No orphaned application modules or duplicated business-domain implementations were found.
- Consolidated eight BAT/CMD files down to one stable canonical launcher, `LAUNCH_WORKFLOW_PLATFORM.bat`. The former Doctor, diagnostics, migration, PostgreSQL preflight, soak/fault, test, and Python-selector BAT filenames are preserved only in versioned history/metadata, not as active aliases.
- Added `scripts/workflow_platform.py` as the one canonical action registry/router for start, Doctor, Export20, tests, soak/fault, PostgreSQL preflight, protected migration, and release verification.
- Added a release-gate self-test that rejects unapproved BAT/CMD files, case-insensitive launcher collisions, a primary entrypoint outside the approved set, a missing action registry, or the return of a retired launcher.
- Consolidated repeated runtime UTC, file-hash, and atomic-JSON helpers into `app/runtime_utils.py`; the pre-release integrity verifier remains intentionally standard-library/self-contained as a protected trust boundary.
- Removed a duplicated diagnostic-retention call so Export20 retention cleanup runs once per completed export.
- Added regression coverage for the one-active-launcher contract, action/backend uniqueness, stale runbook references, current parameter identity, runtime duplicate-function detection, and single retention cleanup.
- Preserved all workflow/case capabilities, SQLite schema version 4, Recovery Doctor behavior, durable outbox, search, PostgreSQL opt-in path, and v0.3.1 Doctor repair behavior. No data migration is introduced by 0.4.0.

## 0.3.1 — Windows Doctor & Diagnostic Evidence Repair

- Fixed the Windows field failure where Recovery Doctor created and verified a healthy SQLite backup/restore but returned `FAIL` solely because `PRAGMA user_version` was `0` while the application expected schema 4.
- Recovery Doctor now separates recoverability from schema readiness. A healthy database with an older or unsynchronized schema marker returns `PASS_WITH_ADVISORY`; unsupported future schema markers still fail closed.
- Added an exact regression for the observed `user_version=0` / structurally current database condition and verified Doctor does not rewrite the live schema marker or schema ledger.
- Fixed diagnostic status isolation so test/custom runtime settings write `app_status.json` only to their own active `state/` directory rather than contaminating the real project status cache.
- Added a deterministic clean release ZIP builder that packages managed source plus approved empty runtime placeholders only, excluding runtime state, logs, databases, evidence, caches, compiled Python, and local secrets.
- Added release regression coverage proving mutable runtime residue such as `state/last_integrity_report.json`, `state/session_secret.txt`, `logs/application.log`, `__pycache__`, and `.pyc` files cannot enter the source archive.
- Improved Doctor console/BAT output so `PASS_WITH_ADVISORY` is visible and actionable without being misreported as a data-recovery failure.
- Database schema remains version 4; no live case data migration is introduced by this maintenance release.

## 0.3.0 — Resilience, Recovery & Scale Foundation

- Added a Recovery Doctor that creates a consistent SQLite backup, verifies SHA-256, restores an isolated temporary copy, runs full integrity/foreign-key checks, compares logical table summaries, verifies attachment evidence relationships/checksums, records a versioned receipt, and never overwrites the live database.
- Added an isolated soak/fault qualification harness covering concurrent case intake, reference uniqueness, workflow transitions, indexed search, external SQLite write contention, durable SLA idempotency, stale-job recovery, stale server-lock recovery, Recovery Doctor, and backup-target safe failure.
- Added persistent `outbox_jobs` for restart-safe notifications and SLA scans with dedupe keys, availability times, bounded retries, stale lease recovery, failure state, and priority.
- Fixed a starvation defect found by the soak harness: SLA work now has higher queue priority than routine notification delivery, with regression coverage proving an SLA scan executes inside the first worker batch even behind more than 100 routine jobs.
- Added PostgreSQL outbox row leasing with `FOR UPDATE SKIP LOCKED` and tolerant concurrent SLA bucket deduplication.
- Added optional PostgreSQL/Psycopg persistence with bounded SQLAlchemy pooling, credential-redacted diagnostics, explicit preflight, protected schema migration, fail-closed normal startup against unprepared schemas, and opt-in demo seeding.
- Added database schema versions 2–4 for notification delivery state, durable outbox, indexed search, and job priority. SQLite upgrades retain checked pre-migration backup behavior.
- Added SQLite FTS5 case search with synchronization triggers and literal-token query construction; added indexed fallback and PostgreSQL GIN full-text search.
- Added an Operations page/API with database/backend health, schema/search mode, SQLite storage/WAL size, durable-worker health, outbox backlog, notification backlog, attachment storage, backup/Doctor status, and request timing.
- Added `DOCTOR_WORKFLOW_PLATFORM.bat`, `RUN_SOAK_FAULT_TESTS.bat`, `POSTGRES_PREFLIGHT.bat`, and protected `MIGRATE_DATABASE.bat` entrypoints, all using the canonical root-derived Python selector.
- Added PostgreSQL schema-change protection against a concurrently running local application instance.
- Added exact Psycopg 3.3.4 / psycopg-binary 3.3.4 dependency pins, SBOM entries, and third-party notices.
- Expanded automated regression coverage for recovery, outbox delivery, search, operations health, schema 1→4 migration, PostgreSQL configuration, queue starvation, and PostgreSQL `SKIP LOCKED` leasing.

## 0.2.0 — Stability & Performance Foundation

- Corrected application log timestamps to emit true UTC before appending the `Z` suffix, fixing a five-hour incident-correlation mismatch observed in the Windows manual diagnostic export.
- Added SQLite schema versioning, fail-closed future-schema detection, online pre-migration backups with SHA-256 sidecars, bounded backup retention, startup `quick_check` / foreign-key verification, and shutdown `PRAGMA optimize` / passive WAL checkpointing.
- Enabled SQLite WAL mode, a 15-second busy timeout, bounded memory cache, normal synchronous mode, and a single-process write coordinator.
- Replaced reference generation that scanned prior case references with stable primary-key-derived references.
- Moved SLA escalation work out of dashboard reads into a low-overhead background cadence and added due-only queries.
- Added performance indexes, paginated queues, SQL completed-history aggregation, streamed attachments, readiness, request timing, single-instance protection, and graceful shutdown hardening.
- Expanded stability regression coverage and qualified a 0.1→0.2 upgrade path.

## 0.1.0 — Foundation MVP

- Added configurable workflows, dynamic intake, routing, deadlines, approvals, evidence, comments, notifications, dashboards, immutable snapshots, audit exports, REST/OpenAPI, Windows launcher, integrity verification, exact dependency lock, diagnostics, SBOM/notices, documentation, and deterministic demonstration data.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
