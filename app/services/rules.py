"""Declarative business rules and decision tables for intake/routing policy."""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BusinessRuleSet
from app.services.common import dumps, loads

OPERATORS = {"eq", "neq", "truthy", "falsy", "gte", "gt", "lte", "lt", "contains", "in"}
ACTIONS = {"set_priority", "add_tag", "route_role", "require_approval"}


def _value(context: dict[str, Any], field: str) -> Any:
    if field.startswith("case."):
        return context.get(field[5:])
    return context.get("fields", {}).get(field)


def condition_matches(condition: dict[str, Any], context: dict[str, Any]) -> bool:
    field = str(condition.get("field", ""))
    op = str(condition.get("operator", "eq"))
    if op not in OPERATORS or not field:
        return False
    actual = _value(context, field)
    expected = condition.get("value")
    try:
        if op == "eq": return actual == expected
        if op == "neq": return actual != expected
        if op == "truthy": return bool(actual)
        if op == "falsy": return not bool(actual)
        if op == "gte": return float(actual) >= float(expected)
        if op == "gt": return float(actual) > float(expected)
        if op == "lte": return float(actual) <= float(expected)
        if op == "lt": return float(actual) < float(expected)
        if op == "contains": return str(expected).casefold() in str(actual).casefold()
        if op == "in": return actual in (expected if isinstance(expected, list) else [])
    except (TypeError, ValueError):
        return False
    return False


def validate_rules(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("Rules must be a JSON array.")
    normalized: list[dict[str, Any]] = []
    for index, rule in enumerate(value, start=1):
        if not isinstance(rule, dict):
            raise ValueError(f"Rule {index} must be an object.")
        name = str(rule.get("name", f"Rule {index}")).strip()[:160]
        conditions = rule.get("all", [])
        actions = rule.get("actions", [])
        if not isinstance(conditions, list) or not isinstance(actions, list) or not actions:
            raise ValueError(f"Rule {name} requires condition and action arrays.")
        for condition in conditions:
            if not isinstance(condition, dict) or str(condition.get("operator", "eq")) not in OPERATORS:
                raise ValueError(f"Rule {name} contains an unsupported condition.")
        for action in actions:
            if not isinstance(action, dict) or str(action.get("type", "")) not in ACTIONS:
                raise ValueError(f"Rule {name} contains an unsupported action.")
        normalized.append({"name": name, "all": conditions, "actions": actions, "stop": bool(rule.get("stop", False))})
    return normalized


def save_rule_set(db: Session, *, key: str, name: str, description: str, rules: Any) -> BusinessRuleSet:
    key = key.strip().lower().replace("-", "_")
    if not key or not key.replace("_", "a").isalnum():
        raise ValueError("Rule-set key must use letters, numbers, and underscores.")
    normalized = validate_rules(rules)
    item = db.scalar(select(BusinessRuleSet).where(BusinessRuleSet.key == key))
    normalized_name = name.strip()[:160] or key
    normalized_description = description.strip()[:2000]
    normalized_rules = dumps(normalized)
    if item is None:
        item = BusinessRuleSet(key=key, name=normalized_name, description=normalized_description, rules_json=normalized_rules)
        db.add(item)
    else:
        changed = (
            item.name != normalized_name
            or item.description != normalized_description
            or item.rules_json != normalized_rules
            or not item.active
        )
        item.name = normalized_name
        item.description = normalized_description
        item.rules_json = normalized_rules
        item.active = True
        if changed:
            item.version += 1
    db.flush()
    return item


def evaluate_rule_sets(db: Session, config: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    snapshot = deepcopy(config)
    result: dict[str, Any] = {"priority": context.get("priority"), "tags": [], "route_role": None, "matched": []}
    keys = [str(x) for x in config.get("rule_sets", []) if isinstance(x, str)]
    if not keys:
        return {**result, "config": snapshot}
    rule_sets = list(db.scalars(select(BusinessRuleSet).where(BusinessRuleSet.key.in_(keys), BusinessRuleSet.active.is_(True))))
    by_key = {item.key: item for item in rule_sets}
    for key in keys:
        item = by_key.get(key)
        if item is None:
            continue
        for rule in loads(item.rules_json, []):
            conditions = rule.get("all", [])
            if not all(condition_matches(cond, context) for cond in conditions):
                continue
            result["matched"].append({"rule_set": key, "rule": rule.get("name", "Rule")})
            for action in rule.get("actions", []):
                kind = action.get("type")
                if kind == "set_priority" and action.get("value") in {"Low", "Medium", "High", "Critical"}:
                    result["priority"] = action["value"]
                elif kind == "add_tag" and str(action.get("value", "")).strip():
                    tag = str(action["value"]).strip()[:60]
                    if tag not in result["tags"]:
                        result["tags"].append(tag)
                elif kind == "route_role" and str(action.get("role", "")).strip():
                    result["route_role"] = str(action["role"]).strip()[:80]
                elif kind == "require_approval":
                    stage_key = str(action.get("stage", "")).strip()
                    stage = next((x for x in snapshot.get("stages", []) if x.get("key") == stage_key), None)
                    if stage is not None:
                        approvals = stage.setdefault("approvals", [])
                        approval = {
                            "key": str(action.get("key", "rule_approval"))[:80],
                            "name": str(action.get("name", "Rule-required approval"))[:180],
                            "assigned_role": str(action.get("role", "Administrator"))[:80],
                        }
                        if not any(x.get("key") == approval["key"] for x in approvals):
                            approvals.append(approval)
            if rule.get("stop"):
                break
    result["config"] = snapshot
    return result


def preview_rules(rules: Any, context: dict[str, Any]) -> dict[str, Any]:
    """Explain which declarative rules would fire without changing any case data."""
    normalized = validate_rules(rules)
    matched: list[dict[str, Any]] = []
    for rule in normalized:
        checks = []
        for condition in rule.get("all", []):
            ok = condition_matches(condition, context)
            checks.append({"condition": condition, "matched": ok, "actual": _value(context, str(condition.get("field", "")))})
        if all(item["matched"] for item in checks):
            matched.append({"name": rule["name"], "conditions": checks, "actions": rule.get("actions", []), "stop": rule.get("stop", False)})
            if rule.get("stop"):
                break
    return {"matched": matched, "match_count": len(matched), "rules_evaluated": len(normalized), "applied": False}
