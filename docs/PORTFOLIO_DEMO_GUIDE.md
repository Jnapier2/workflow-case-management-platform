# Portfolio Demo Guide

## 10–15 minute walkthrough

### 1. Start with the management problem

Explain that the platform converts loosely managed requests into a governed case lifecycle: validated intake, routing, service levels, collaboration, approvals, evidence, integrations, audit history, and process improvement.

### 2. Dashboard and Work Queues

Show backlog/SLA/bottleneck metrics, then open **Work Queues**. Demonstrate saved/shared filters, workload/capacity, skill-aware assignment, delegation, and a bulk action. Emphasize that bulk work still uses the same domain rules and writes individual case audit events.

### 3. Visual Workflow Studio

Open Vendor Onboarding in Studio:

- point out visual stages/transitions;
- show that Studio edits the same JSON source the engine executes;
- show business-calendar/SLA metadata and required skills;
- save/validate a draft;
- show revision diff and deployment/canary settings.

Explain immutable case snapshots: publishing a new workflow does not rewrite cases already in flight.

### 4. Rules Studio

Open the vendor risk rules and the Rule Playground. Use a high-spend/data-sensitive sample to show matched conditions and the resulting priority/tags/conditional approval—without writing case data. This demonstrates business policy separated from application code.

### 5. Requester Portal

Submit a new request through the requester-facing portal. Show knowledge guidance, milestones, requester updates, and evidence. Explain that the local lookup is intentionally demo-grade and the deployment has an optional OIDC boundary for real identity integration.

### 6. Case-management depth

Open a case and demonstrate:

- parent/child or related case link;
- duplicate suggestion;
- child-completion transition gate;
- business-calendar stage deadline;
- pause/resume SLA with reason;
- conditional approvals;
- streamed evidence with SHA-256;
- requester and internal collaboration;
- integration execution/audit evidence.

### 7. Guarded Case Assist

Show the local Case Assist summary/suggestions. Explain that a suggestion does nothing until an authorized user explicitly accepts/rejects it. If an external advisor is configured, show that only redacted operational metadata is sent and external responses cannot contain executable actions.

### 8. Process Intelligence

Open **Process Intelligence** and discuss variants, stage dwell percentiles, cycle time, rework loops, approval turnaround, SLA causes, manual touches, and improvement opportunities. Position this as continuous process improvement derived from the audit data already created during case work.

### 9. Auditability and recovery

Show the case's verified hash-chain indicator and JSON audit package. Then open **Operations** for schema/search/worker/outbox/connectors/storage/Recovery Doctor/request-timing health.

Explain that Recovery Doctor creates a checked backup and restores/validates an isolated copy rather than overwriting live data.

### 10. Close with engineering credibility

Briefly show:

- one Windows launcher;
- manifest/hash release gate;
- automated tests and soak/fault harness;
- project-local runtime layout;
- REST/OpenAPI capability discovery;
- SQLite default + optional PostgreSQL/OIDC adapter boundaries.

## Suggested portfolio summary

> Built a configurable enterprise-style workflow and case-management platform with visual process/rule design, skill/capacity routing, business-calendar SLAs, related cases, approvals, requester self-service, durable integrations, process intelligence, guarded decision assistance, version governance, and verifiable audit/recovery controls across multiple business-process templates.

## Suggested resume bullet

> Developed a configurable workflow/case platform with visual process and rule design, SLA/approval governance, related-case orchestration, self-service intake, durable integrations, process analytics, guarded decision assistance, REST APIs, and hash-linked audit history, backed by migration/recovery and fault-testing controls.

## Accurate boundaries to state if asked

The release is portfolio-grade and source/archive qualified. Do not claim production OIDC certification, live SMTP, malware scanning, public-cloud hardening, multi-tenancy, production PostgreSQL recovery, or externally notarized audit evidence unless those are separately implemented/verified.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
