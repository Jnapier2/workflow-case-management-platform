# Soak and Fault Testing

## Purpose

`LAUNCH_WORKFLOW_PLATFORM.bat soak` exercises high-value resilience paths against a disposable project-local test runtime. It does not target the configured live case database.

## Current checks

- concurrent case creation and reference uniqueness;
- bounded intake latency percentiles;
- representative case transitions;
- full-text/indexed search;
- external SQLite `BEGIN IMMEDIATE` writer contention;
- durable SLA escalation and immediate idempotency;
- stale `Processing` outbox lease recovery;
- Recovery Doctor backup/restore qualification;
- stale server-instance lock recovery;
- backup-target fault injection with safe failure.

The harness writes a timestamped JSON report under `reports/` and a compact latest receipt under `state/soak_fault_latest.json`, then removes the disposable runtime unless `--keep` is explicitly supplied to the Python script.

## Important finding in 0.3.0 development

The initial 120-case run found that an SLA scan could wait behind more than one worker batch of queued notification-delivery jobs because all jobs had equal priority. The application was changed to priority-aware dispatch, SLA priority 10 versus routine delivery priority 50, and a dedicated automated regression now recreates a >100-job backlog and requires the SLA change on the first worker pass.

This is an example of why the soak/fault harness is part of the portfolio: it found a reliability issue that the ordinary functional suite did not expose.

## Qualification boundary

These tests are local synthetic evidence, not a production throughput guarantee. They do not reproduce Windows antivirus effects, network/database server latency, real attachment volumes, multi-day uptime, low-disk conditions, hardware failure, or production PostgreSQL infrastructure.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
