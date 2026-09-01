# Build Report — v0.5.2 / WCM-B009

## Purpose

Windows startup correctness maintenance release over v0.5.1 / WCM-B008. Schema 5 and all enterprise case-management behavior remain unchanged.

## New field evidence boundary

The newly supplied sidecar records `workflow_case_management_MANUAL_EXPORT20_20260831_233606_UTC.zip` at SHA-256 `7765750BB792764F55C9D3EC5EE9F34D624C4F5A72D876F7AA2EED71C7F7E5DA`. The attachment index can verify that sidecar, but the raw ZIP payload was not available to the repair runtime for content-level replay. This build therefore does not invent any claim about the new archive's internal receipts.

## Defect found during exact B008 revalidation

`run_server.py` had two related startup risks:

1. localhost port 8010 was fixed; an unrelated process holding it could prevent startup;
2. every `SystemExit`, including a nonzero Uvicorn startup/bind exit, was converted to return code 0 and could be reported by the launcher as success.

## Repairs

- Added bounded localhost port probing. Demo/local mode prefers 8010 and can use the next free port through 8019 without stopping or modifying the existing listener.
- Added an explicit `WORKFLOW_PORT` override with range validation.
- OIDC mode does not silently change a registered redirect port: an occupied selected port fails closed with clear guidance.
- Preserved nonzero Uvicorn `SystemExit` codes; only explicit zero/normal signal unwind is treated as successful shutdown.
- Console prints the selected local URL.
- Preserved the B008 launcher journal, early-failure capture, persistent last-launcher-failure receipt, interactive Export20 UX, one-active-launcher contract, schema 5, and enterprise capabilities.

## Regression coverage

Added tests for non-destructive occupied-port fallback, `WORKFLOW_PORT` validation, and nonzero `SystemExit` propagation. Existing B008 launcher/export/recovery/resilience tests remain active.

## Exact-archive qualification

- 120/120 managed files PASS after manifest freeze.
- 68/68 automated tests PASS from a fresh extraction of the exact release ZIP.
- Deterministic source ZIP rebuild PASS; 130 safe entries; exactly one BAT/CMD; zero non-empty duplicate-content groups.
- Controlled occupied-port replay PASS: a harmless listener held the preferred port, B009 selected the next bounded localhost port, reached readiness, left the holder untouched, and removed its instance lock on clean shutdown.
- Primary health, dashboard, Operations, Queues, Process Intelligence, Studio, Requester Portal, and OpenAPI surfaces returned HTTP 200.
- Recovery Doctor PASS with matching backup/restore SHA-256 and `live_database_modified=false`.
- Isolated 120-case / 8-worker soak/fault PASS with 120/120 unique cases, SLA idempotency, FTS5, SQLite contention recovery, stale-job recovery, transitions, and backup-fault containment.
- Exact B008/schema-5 compatibility PASS: all 9 seeded case references were preserved and no migration was required.
- Healthy B009 Support Export20 finalized at 20 items with current app/database/integrity/launcher/Doctor/soak receipts and no database, evidence payload, or session secret.

## Qualification boundary

Physical-Windows execution of the exact B009 BAT with Norton/SmartScreen enabled remains the promotion gate. v0.3.1 / WCM-B004 remains the field-proven Windows rollback until B009 is confirmed.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
