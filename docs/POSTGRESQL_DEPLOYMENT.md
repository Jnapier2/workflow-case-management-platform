# PostgreSQL Deployment Path

The PostgreSQL path exists to demonstrate a production-shaped persistence boundary while keeping SQLite as the zero-friction portfolio default.

## Configuration

Supply a runtime environment variable rather than editing source files:

```text
WORKFLOW_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/DATABASE
```

Do not store real credentials in this repository, diagnostic packages, screenshots, or documentation.

## Preparation sequence

1. Provision a PostgreSQL database and take whatever server-native snapshot/backup your deployment requires before schema changes.
2. Set `WORKFLOW_DATABASE_URL` for the current terminal/process.
3. Run `LAUNCH_WORKFLOW_PLATFORM.bat postgres-preflight`.
4. If it reports schema preparation is required, stop the application for this extracted root and run `LAUNCH_WORKFLOW_PLATFORM.bat migrate`.
5. The migration command presents one protected `Action? [Y/N]` prompt, holds the local server-instance lock, creates/updates the schema, installs the search index, records schema version 5, then runs health verification.
6. Run `LAUNCH_WORKFLOW_PLATFORM.bat postgres-preflight` again; it should report responsive and schema 5.
7. Launch normally.

Normal PostgreSQL launch refuses to create or migrate an unprepared schema. This keeps bulk schema writes explicit.

## Pool and worker behavior

Default SQLAlchemy settings are bounded: pool size 5, max overflow 10, pre-ping enabled, recycle 1800 seconds. They can be changed in a deployment-specific `Settings` construction; the portfolio Windows launcher intentionally keeps the source defaults simple.

Outbox consumers use row-level `FOR UPDATE SKIP LOCKED` leasing. Concurrent SLA bucket enqueue attempts are protected by the unique dedupe key and handled as one authoritative bucket. This is compatible with a future separately supervised worker without changing business transactions or case records.

## Demo data

PostgreSQL does not seed demo data by default. For a disposable portfolio PostgreSQL database only, set:

```text
WORKFLOW_POSTGRES_SEED_DEMO=1
```

Do not use that setting as a production initialization mechanism.

## Search

Migration installs a GIN expression index over a simple-text `to_tsvector` document containing case reference, title, requester name/email, and description. Queries use `plainto_tsquery` semantics rather than accepting raw full-text query syntax.

## Recovery boundary

`LAUNCH_WORKFLOW_PLATFORM.bat doctor` can report PostgreSQL connectivity/schema health but does not call `pg_dump`, cloud snapshot APIs, or restore commands. A production PostgreSQL deployment must qualify backup, point-in-time recovery, and restore using its actual database infrastructure.

## Qualification status

The current 0.5.2 source build retains and compiles the PostgreSQL `SKIP LOCKED` leasing statement and pins Psycopg 3.3.4 / psycopg-binary 3.3.4. No live PostgreSQL server was available in the build environment, so live server migration, concurrency, and restore are explicitly unverified until performed on a real PostgreSQL instance.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
