# Security and Trust Boundaries

## Release/runtime integrity

The canonical launcher verifies the declared version/build, package metadata, manifest, and every managed-file SHA-256 before dependency/authenticated preflight or server launch. The active package allows one BAT/CMD only. Runtime identity failure fails closed and preserves bounded diagnostics.

## Local binding

The portfolio runtime prefers `127.0.0.1:8010`. In demo/local mode, if that port is already held, the launcher selects the next free localhost port through 8019 without terminating or altering the existing listener. `WORKFLOW_PORT` can choose an explicit port. OIDC mode fails closed rather than changing a registered redirect port automatically. Do not expose the default demo build directly to an untrusted network.

## Identity modes

### Demo mode

Demo mode is intentionally convenient for portfolio walkthroughs. Actor switching demonstrates role/routing behavior; it is **not production authentication**.

### Optional OIDC mode

Set `WORKFLOW_AUTH_MODE=oidc` plus:

- `WORKFLOW_OIDC_ISSUER` — HTTPS issuer;
- `WORKFLOW_OIDC_CLIENT_ID`;
- optional `WORKFLOW_OIDC_CLIENT_SECRET`;
- `WORKFLOW_OIDC_REDIRECT_URI`;
- optional `WORKFLOW_OIDC_DEFAULT_ROLE`;
- optional `WORKFLOW_OIDC_ROLE_MAP_JSON` mapping provider group/role claim strings to supported platform roles.

Controls:

- authorization-code flow;
- PKCE S256;
- state validation;
- HTTPS discovery/token/userinfo endpoints;
- discovery issuer equality check;
- verified-email rejection when provider explicitly reports `email_verified=false`;
- required `sub` claim;
- stable identity binding to issuer + subject;
- role mappings fail closed unless the result is a supported platform role;
- protected API endpoints require the authenticated session and ignore caller-supplied demo actor IDs;
- session cookies use HTTPS-only behavior when the configured redirect URI is HTTPS.

This is a production-shaped portfolio integration boundary, not an external identity-provider certification or independent OIDC conformance claim. Provider-specific logout, token refresh/session expiry, MFA policy, conditional access, and deployment proxy/TLS behavior must be validated in the actual environment.

## CSRF/session controls

Browser mutation forms use CSRF tokens. Session state is signed with a project-local secret created at runtime and excluded from release packages/Export20. OIDC API identity comes from session state rather than payload-supplied actor fields.

## Business rules

Rule definitions are declarative data. Arbitrary Python, `eval()`, and executable expressions are not supported. Rule actions go through a small allowlist and normal workflow data structures.

## Connector safety

- webhook endpoints must use HTTPS;
- URL embedded credentials are rejected;
- private/loopback/link-local/reserved/multicast targets are blocked unless an administrator explicitly enables private-network delivery;
- redirects are disabled;
- connector secrets are loaded only from named environment variables;
- secrets are not stored in workflow JSON, database connector definitions, or diagnostic output;
- durable execution uses idempotency/correlation metadata and bounded error summaries.

DNS/IP validation reduces SSRF risk but does not replace network egress controls in a real deployment.

## External Case Assist

External Case Assist is disabled unless an HTTPS endpoint is explicitly configured. It is never invoked automatically by workflow transitions or the background worker.

The external payload excludes:

- requester name/email;
- case title/description/intake free text;
- comments/requester update text;
- attachment names/bytes/evidence content;
- connector secrets.

It contains bounded operational state only (workflow/stage/version, priority/tags, SLA state, and aggregate counts). Responses are limited to 64 KiB, normalized into text/confidence suggestions, and any returned action is replaced with `{type: none}`. External output therefore cannot autonomously mutate a case.

Configuration:

- `WORKFLOW_ASSIST_HTTPS_ENDPOINT`;
- optional `WORKFLOW_ASSIST_SECRET_ENV` naming an environment variable;
- optional `WORKFLOW_ASSIST_ALLOW_PRIVATE_NETWORK=true` only for an explicitly trusted local/private service.

## Evidence uploads

- maximum upload size is bounded;
- files are streamed, not buffered wholesale in memory;
- storage keys are generated rather than trusting filenames;
- path resolution is contained to the evidence root;
- SHA-256 is recorded and verified before download;
- attachment metadata records integrity and scan status.

The local build does **not** include a malware scanner. Do not use it for untrusted production evidence until a scan/quarantine/storage policy is implemented.

## Audit chain

Activity hashes make unexpected application-level history edits detectable, but they are not an external timestamp authority, digital signature, WORM store, or cryptographic notarization service.

## Protected operations

Database migration remains an explicit protected write and uses one terse `Action? [Y/N]` prompt. Recovery Doctor, verification, diagnostics, and isolated soak work are low-risk/read-only/reversible and do not require routine prompts.

## Secrets and diagnostics

Secrets, databases, evidence contents, and session keys are excluded from clean source releases and Export20. Database URLs are redacted in operational output. Support evidence is version/build/freshness-aware so stale receipts cannot masquerade as current health.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
