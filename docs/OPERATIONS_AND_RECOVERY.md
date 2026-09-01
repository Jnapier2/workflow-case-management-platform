# Operations and Recovery

## Operations page

`/operations` and `/api/v1/operations` expose bounded support information without case contents or credentials:

- backend/redacted database target/schema/search mode;
- SQLite database/WAL/SHM size when applicable;
- durable worker state/last run/failure counts;
- outbox Pending/Processing/Failed/Completed counts and oldest-pending age;
- integration execution Queued/Processing/Completed/Failed counts and latest bounded failure;
- queued notification count;
- evidence metadata count/total bytes;
- managed backup count/latest backup;
- current/fresh Recovery Doctor result;
- request timing/slow-request counters.

Stale or prior-build health receipts can be shown as provenance but do not set present-tense `Attention` state.

## Recovery Doctor

`LAUNCH_WORKFLOW_PLATFORM.bat doctor` is low-risk and non-destructive. For SQLite it:

1. creates a consistent project-local backup;
2. verifies backup SHA-256;
3. restores an isolated temporary copy;
4. checks structure/foreign keys/logical counts;
5. verifies evidence relationships/checksums;
6. records a version/build-stamped receipt;
7. removes only the temporary restore copy.

The live database is never replaced by Doctor.

PostgreSQL Doctor reports bounded health with a limitation because server-native backup/restore belongs to the actual deployment environment.

## Durable work

Temporary job failure returns work to `Pending` with bounded delay. Exhausted jobs become `Failed` and remain visible. Stale `Processing` leases become reclaimable. SLA scans have higher priority than routine notification/integration work so deadline handling cannot be starved by a normal backlog.

PostgreSQL consumers use `FOR UPDATE SKIP LOCKED`; SQLite supports the bounded one-server local coordinator.

## Integration operations

Connector executions record correlation/idempotency/status/HTTP/latency/bounded error information. They never store connector secret values. A failed connector job is visible in both Operations and the associated case audit trail.

## Evidence integrity

Evidence metadata records the storage backend/key and expected SHA-256. Downloads verify the stored bytes before serving. An integrity mismatch fails rather than silently serving altered content.

## Export20

Export20 remains bounded to at most 20 redacted/read-only support items. It excludes the database, evidence bytes, session secret, and other credential material. A project-local temporary ZIP is integrity/count tested before atomic finalization.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
