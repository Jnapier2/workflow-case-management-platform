# Performance Qualification

## Purpose

This document records a bounded synthetic comparison used to validate the 0.2.0 stability/performance foundation. It is engineering evidence for the portfolio build, not a production capacity promise or service-level agreement.

## Environment

- Same provided Linux build environment for both versions
- CPython 3.13.x compatible runtime
- Same installed SQLAlchemy stack and same host resources
- Temporary local SQLite database for each run
- 20,000 synthetic completed vendor-onboarding cases plus the standard demonstration records
- Seven warmed dashboard measurements per version
- One new vendor-onboarding intake measured after the historical rows were present

No network, browser rendering, Windows filesystem, antivirus, or production concurrency was included in this microbenchmark.

## Results

| Measure | 0.1.0 / WCM-B001 | 0.2.0 / WCM-B002 | Observed change |
|---|---:|---:|---:|
| Dashboard build, median | 507.6 ms | 88.3 ms | ~5.8× faster |
| Dashboard build, min | 390.5 ms | 83.0 ms | — |
| Dashboard build, max | 533.1 ms | 96.3 ms | — |
| One new intake with 20k history | 23.9 ms | 3.7 ms | ~6.4× faster |

The 0.2.0 dashboard samples were approximately 85.0, 83.0, 88.3, 92.7, 86.4, 96.3, and 92.9 ms. The 0.1.0 samples were approximately 500.3, 494.1, 533.1, 521.3, 519.1, 390.5, and 507.6 ms.

## Why the path improved

0.1.0 loaded the full case history into Python for dashboard metrics and scanned prior matching references when assigning the next case reference. 0.2.0 moves completed-history counts and cycle-time calculations into SQL, keeps queue retrieval paginated, adds supporting indexes, and derives each new human reference from the unique database case ID. These changes keep those common paths from growing linearly with completed-history volume in the same way.

## Remaining scaling boundary

Snapshot-aware bottleneck and workload calculations still inspect the active/open case set in Python because historical workflow snapshots may carry different stage labels and service-level targets. This is intentional for correctness in the current portfolio build. If active-case volume becomes large enough to matter, the next optimization should be measured first and should preserve snapshot semantics, likely by introducing normalized snapshot/stage reporting dimensions rather than caching blindly.

## 0.3.0 resilience qualification retained in 0.5.2

Version 0.3.0 retains the 0.2.0 optimized dashboard/reference paths and adds a different qualification dimension: concurrent intake and targeted fault handling. An isolated 500-case / 12-worker pass created 500/500 unique cases with median 212.87 ms, p95 331.27 ms, p99 376.12 ms, and max 475.71 ms while also passing forced SQLite writer contention, durable SLA idempotency, stale-job recovery, full-text search, Recovery Doctor, stale-lock recovery, and backup-target safe-failure checks. See `RESILIENCE_QUALIFICATION.md`.

These concurrency numbers are not directly comparable to the single-operation 20,000-history measurements above.

## Qualification rule

Treat these figures as comparative local evidence only. Windows performance, cold-start behavior, disk contention, antivirus effects, real attachment I/O, and production load remain unmeasured for the 0.2.0 comparison; the 0.3.0 resilience pass measures separate local concurrency/fault behavior.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
