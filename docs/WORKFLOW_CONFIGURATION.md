# Workflow Configuration and Studio

## One executable model

Workflow Studio is a visual/editor surface over the same JSON configuration executed by `workflow_engine.py`. There is no separate visual-only workflow representation.

A workflow defines:

- `key`, `name`, `description`, `version`;
- intake fields and validation;
- `initial_stage`;
- stages, roles, skill requirements, SLA hours, approvals;
- transitions with roles/gates/connector actions;
- terminal outcomes;
- overall SLA/business calendar;
- escalation policy;
- rule-set references;
- optional deployment/canary settings.

Server-side validation rejects invalid graph/state structures before publication/import.

## Business calendar example

```json
{
  "business_calendar": {
    "timezone": "America/Chicago",
    "business_days": [0, 1, 2, 3, 4],
    "start": "08:00",
    "end": "17:00",
    "holidays": ["2026-09-07"]
  },
  "sla_warning_hours": 4
}
```

Due dates are calculated in business time. Case SLA pause/resume is a separate auditable runtime action and does not rewrite the workflow definition.

## Stage example

```json
{
  "key": "due_diligence",
  "label": "Due Diligence",
  "roles": ["Risk Reviewer", "Administrator"],
  "required_skills": ["vendor_risk"],
  "sla_hours": 16,
  "approvals": []
}
```

Assignment considers the configured role, required skills, current open-case load, user capacity, and active delegation.

## Transition gates/connectors

```json
{
  "to": "completed",
  "label": "Complete",
  "roles": ["Case Manager", "Administrator"],
  "requires_approvals": true,
  "requires_children_complete": true,
  "connector_actions": ["portfolio_audit_log"]
}
```

A transition never bypasses its normal authorization or gates merely because it came from a bulk action or assist suggestion.

## Declarative rules

Rules are reusable policy objects outside the workflow graph. Supported case-intake actions include priority/tag/routing and conditional approval injection. Conditions are structured comparisons; arbitrary executable expressions are prohibited.

Use Rules Studio's Playground to evaluate sample context and inspect which conditions/actions would match without changing a case.

## Draft/publish/revisions

- save draft: validates and stores a new draft revision;
- publish: makes a validated revision current;
- diff: highlights structural/configuration change categories;
- case snapshot: existing cases stay on their captured version;
- migration preview: checks whether an existing case can move to the current definition;
- controlled migration: explicit Administrator action, captured in audit history.

## Deterministic canary deployment

A published workflow may include:

```json
{
  "deployment": {
    "rollout_percent": 25,
    "baseline_version": 2
  }
}
```

New requests are deterministically bucketed. About 25% select the candidate revision and the remainder use the declared baseline. A created case stores the selected version/snapshot permanently, so later rollout changes cannot move it silently.

## Example workflows

The release ships three configurable demonstrations:

- `vendor_onboarding`;
- `employee_access_request`;
- `customer_complaint`.

They exercise different rules, skills, approvals, SLA patterns, and portal intake fields while using one runtime engine.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
