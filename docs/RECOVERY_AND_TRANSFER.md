# Recovery and Transfer

## Recovery hierarchy

1. Preserve the exact versioned release ZIP and checksum.
2. For a populated SQLite environment, preserve `data/`, `uploads/`, and required `backups/` together while the application is stopped.
3. Before every supported SQLite schema migration, the application creates a consistent checked database backup and SHA-256 sidecar under `backups/`.
4. Use `LAUNCH_WORKFLOW_PLATFORM.bat doctor` when you want an explicit recoverability qualification receipt.

## Recovery Doctor

Doctor is intentionally non-destructive to the live SQLite data store. It:

- runs health checks on the live database;
- creates a consistent SQLite backup;
- verifies and records the backup SHA-256;
- creates an isolated temporary restore copy under project-local diagnostics temp;
- verifies restore SHA-256 equality;
- runs full `PRAGMA integrity_check` and foreign-key checks on the restore copy;
- compares logical counts/max IDs for core business and outbox tables;
- verifies attachment records against project-local evidence file path, size, and SHA-256;
- writes a versioned report under `reports/` and the latest compact receipt under `state/recovery_doctor.json`;
- removes only the temporary restore copy.

The live database is never replaced by Doctor. A failed Doctor report therefore signals a recovery/evidence problem without attempting automatic repair.

PostgreSQL Doctor returns a bounded health result with a clear limitation: server-native backup/restore must be exercised with the deployment's PostgreSQL tooling and backup service. This source release does not claim a PostgreSQL restore drill it did not perform.

## Schema upgrades

SQLite normal launch can perform supported migrations only after a pre-migration backup succeeds. A database schema newer than the application is rejected.

PostgreSQL normal launch performs no schema mutation. Use `LAUNCH_WORKFLOW_PLATFORM.bat postgres-preflight` and then the protected `LAUNCH_WORKFLOW_PLATFORM.bat migrate` when a schema change is intentional. The migration command uses the same local server-instance lock, so it refuses to run while the application for that extracted root is active.

## Rollback

The Windows-proven rollback authority is 0.3.1 / WCM-B004 until the exact B008 artifact is field-confirmed on Windows. Never point older code at a database schema it reports as newer than supported. Restore the matching pre-migration database plus the corresponding `uploads/` evidence set.

## Resetting the demo

After stopping the application and preserving anything required, remove only known generated project data if you intentionally want a clean deterministic demo. Do not delete unknown or user-created files silently.

## Transfer

Google Drive or a repository may store finalized versioned ZIPs/checksums for handoff/archive. Do not run the active application from a synchronized cloud folder. Extract to a normal local path, stop the application before copying populated data, and verify the release checksum after transfer.

## Critical diagnostics

The Windows action router first persists a dependency-free `state/launcher_status.json` phase receipt and bounded `logs/launcher.log`. Critical startup/runtime failures then produce a minimal crash capsule when that richer path is available, followed by one isolated bounded Export20 when process/storage/shutdown budget permits. The exporter excludes database content and evidence attachments and does not perform repair, network/API calls, Drive calls, security-tool changes, project rescans, or behavior mutation.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.

## Cached evidence freshness

Recovery/health/soak receipts produced by the current release carry application version/build identity. Export20 copies a full cached receipt only when its identity matches the current release and its timestamp is within the 24-hour support-freshness window. Older, prior-build, or legacy-unattributed receipts are summarized in `diagnostic_summary.json` and remain historical evidence; they do not create a present-tense Operations failure. Run the canonical `doctor` action before Export20 when a fresh recovery-readiness receipt is required.


## Early Windows launcher evidence

If Start fails before FastAPI/database initialization, the next Export20 can still include `state/launcher_status.json`, `state/last_launcher_failure.json`, plus bounded launcher/bootstrap log tails. The launcher records release verification, dependency bootstrap, backend execution, completion, cancellation, and router-exception phases without importing application dependencies. The single BAT also writes `logs/launcher_bootstrap_failure.txt` when the extracted package is incomplete or no supported Python is found.
