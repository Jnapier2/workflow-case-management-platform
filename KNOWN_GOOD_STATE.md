# Current Qualification / Rollback State

## Forward repair candidate

- Version: **0.5.2**
- Build: **WCM-B009**
- Status: source/archive/recovery/soak-qualified localhost port-selection / startup-exit correctness repair candidate
- Parameters: Gateway shared defaults **v2.17.13**
- Database schema: **5** (unchanged)
- Immediate source predecessor: **v0.5.1 / WCM-B008**

B009 changes only startup orchestration. The visual workflow/rules studio, case relationships, business-calendar SLA, queues/bulk/delegation, connectors, requester portal, process intelligence, workflow governance, guarded Case Assist, OIDC boundary, evidence storage, and audit semantics remain B008/B007-compatible.

## Revalidation finding

B008 fixed silent launcher/export evidence loss, but the local server still preferred a single fixed port and converted any Uvicorn `SystemExit` into success. B009 safely selects another bounded localhost port in normal demo/local mode when 8010 is occupied and never converts a nonzero Uvicorn startup exit into PASS.

## New uploaded evidence boundary

The supplied 2026-08-31 23:36 UTC field-export sidecar records SHA-256 `7765750BB792764F55C9D3EC5EE9F34D624C4F5A72D876F7AA2EED71C7F7E5DA`. Its raw ZIP payload was not available for content-level review in this pass, so it is retained as checksum evidence only and is not used to promote B008.

## Qualification completed

B009 exact-archive qualification passes 120/120 managed-file integrity, 68/68 tests, deterministic ZIP identity, controlled occupied-port fallback/readiness, primary HTTP surfaces, Recovery Doctor, 120-case/8-worker soak/fault, and B008 schema-5 compatibility. These are build-environment qualifications, not physical-Windows/Norton confirmation.

## Windows-working rollback

**v0.3.1 / WCM-B004** remains the strongest physical-Windows confirmed rollback baseline.

## Promotion gate

Promote B009 only after the exact final ZIP completes on physical Windows with Norton/SmartScreen enabled:

1. Start local platform and reach readiness, including a safe fallback if 8010 is intentionally occupied.
2. Recovery Doctor.
3. Manual Export20 with visible path/SHA-256.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
