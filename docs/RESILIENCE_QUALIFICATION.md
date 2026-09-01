# Resilience Qualification — 0.3.0 Evidence Retained by 0.5.2

## 120-case discovery/retest

An isolated 120-case / 8-worker run originally exposed SLA-job starvation behind routine notification backlog. After priority-aware queue processing was added, the same qualification passed all checks:

- concurrent intake: 120/120 created and 120 unique references;
- intake p50: 81.62 ms;
- p95: 160.97 ms;
- p99: 169.97 ms;
- max: 172.43 ms;
- 25/25 representative transitions passed;
- SQLite FTS5 search passed;
- forced external SQLite writer contention completed successfully;
- durable SLA first pass changed the overdue case once; immediate second pass changed zero;
- stale job recovery passed;
- Recovery Doctor passed;
- stale server lock recovery passed;
- backup-target fault injection failed safely.

## 500-case qualification

A heavier isolated 500-case / 12-worker pass also completed with overall `PASS`:

- concurrent intake: **500/500 successful and 500 unique references**;
- intake median: **212.87 ms**;
- p95: **331.27 ms**;
- p99: **376.12 ms**;
- max: **475.71 ms**;
- 25/25 representative transitions passed;
- SQLite FTS5 search passed;
- forced SQLite writer contention request completed successfully at about 342 ms while a raw write lock was deliberately held for ~250 ms;
- durable SLA first pass: 1 business change; immediate second pass: 0;
- stale job recovery passed with zero failures;
- Recovery Doctor passed with zero attachment-evidence problems;
- stale server lock recovery passed;
- backup-target fault injection failed safely.

## Interpretation

These results verify bounded local behavior under synthetic concurrency and targeted fault conditions in the provided Linux/Python environment. They are not a service-level guarantee and do not establish Windows, Norton, live PostgreSQL, or production capacity.

The earlier 0.2.0 20,000-history comparative microbenchmark remains in `docs/PERFORMANCE_QUALIFICATION.md` because it measures a different dimension: growth of dashboard/history and reference-allocation paths.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
