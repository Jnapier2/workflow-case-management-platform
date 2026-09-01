"""Management metrics and bottleneck analysis.

Dashboard history metrics are aggregated in SQL so completed-case growth does not require
loading the entire case table into Python on every request.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from statistics import mean
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.models import Approval, CaseRecord, User, WorkflowDefinition
from app.services.common import as_utc, utcnow
from app.services.workflow_engine import case_workflow_config, stage_label


def build_dashboard(db: Session) -> dict[str, Any]:
    now = utcnow()
    workflows = {item.id: item for item in db.scalars(select(WorkflowDefinition))}
    users = {item.id: item for item in db.scalars(select(User))}

    open_cases = list(
        db.scalars(
            select(CaseRecord)
            .where(CaseRecord.completed_at.is_(None))
            .options(selectinload(CaseRecord.workflow), selectinload(CaseRecord.assignee))
            .order_by(CaseRecord.updated_at.desc())
        )
    )
    overdue = [
        item
        for item in open_cases
        if (as_utc(item.stage_due_at) and as_utc(item.stage_due_at) < now)
        or (as_utc(item.overall_due_at) and as_utc(item.overall_due_at) < now)
    ]
    due_soon = [
        item
        for item in open_cases
        if as_utc(item.stage_due_at)
        and now <= as_utc(item.stage_due_at) <= now + timedelta(hours=48)
    ]

    pending_approvals = db.scalar(
        select(func.count(Approval.id)).where(Approval.status == "Pending")
    ) or 0
    completed_30d = db.scalar(
        select(func.count(CaseRecord.id)).where(CaseRecord.completed_at >= now - timedelta(days=30))
    ) or 0
    measured_closed = db.scalar(
        select(func.count(CaseRecord.id)).where(CaseRecord.closed_on_time.is_not(None))
    ) or 0
    on_time_closed = db.scalar(
        select(func.count(CaseRecord.id)).where(CaseRecord.closed_on_time.is_(True))
    ) or 0

    workflow_open_rows = db.execute(
        select(WorkflowDefinition.name, func.count(CaseRecord.id))
        .join(CaseRecord, CaseRecord.workflow_id == WorkflowDefinition.id)
        .where(CaseRecord.completed_at.is_(None))
        .group_by(WorkflowDefinition.id, WorkflowDefinition.name)
        .order_by(func.count(CaseRecord.id).desc(), WorkflowDefinition.name.asc())
    ).all()

    stage_groups: dict[tuple[int, str, str], list[CaseRecord]] = defaultdict(list)
    for item in open_cases:
        stage_groups[(item.workflow_id, item.status, item.workflow_snapshot_json)].append(item)
    bottlenecks: list[dict[str, Any]] = []
    for (workflow_id, status, _snapshot), group in stage_groups.items():
        ages = [max(0.0, (now - as_utc(item.stage_started_at)).total_seconds() / 3600) for item in group]
        config = case_workflow_config(group[0])
        stage = next((x for x in config.get("stages", []) if x.get("key") == status), {})
        target = float(stage.get("sla_hours", 0) or 0)
        average_age = mean(ages) if ages else 0.0
        overdue_count = sum(
            1 for item in group if as_utc(item.stage_due_at) and as_utc(item.stage_due_at) < now
        )
        risk_score = average_age / target if target > 0 else average_age / 24
        workflow_name = workflows[workflow_id].name if workflow_id in workflows else f"Workflow {workflow_id}"
        bottlenecks.append(
            {
                "workflow": workflow_name,
                "stage": stage_label(config, status),
                "count": len(group),
                "average_age_hours": round(average_age, 1),
                "target_hours": target,
                "overdue_count": overdue_count,
                "risk_score": round(risk_score + overdue_count, 2),
            }
        )
    bottlenecks.sort(key=lambda item: (item["risk_score"], item["count"]), reverse=True)

    overdue_ids = {item.id for item in overdue}
    workload: list[dict[str, Any]] = []
    for user in users.values():
        assigned = [item for item in open_cases if item.assignee_id == user.id]
        if not assigned:
            continue
        workload.append(
            {
                "name": user.name,
                "role": user.role,
                "open": len(assigned),
                "overdue": sum(1 for item in assigned if item.id in overdue_ids),
                "critical": sum(1 for item in assigned if item.priority == "Critical"),
            }
        )
    workload.sort(key=lambda item: (item["overdue"], item["open"]), reverse=True)

    cycle_rows = db.execute(
        select(
            CaseRecord.workflow_id,
            func.count(CaseRecord.id),
            func.avg((func.julianday(CaseRecord.completed_at) - func.julianday(CaseRecord.created_at)) * 24.0),
        )
        .where(CaseRecord.completed_at.is_not(None))
        .group_by(CaseRecord.workflow_id)
    ).all()
    workflow_cycle = [
        {
            "workflow": workflows[workflow_id].name if workflow_id in workflows else f"Workflow {workflow_id}",
            "closed_cases": int(closed_count or 0),
            "average_cycle_hours": round(float(average_hours or 0.0), 1),
        }
        for workflow_id, closed_count, average_hours in cycle_rows
    ]
    workflow_cycle.sort(key=lambda item: item["average_cycle_hours"], reverse=True)

    recent_cases = list(
        db.scalars(
            select(CaseRecord)
            .options(selectinload(CaseRecord.workflow), selectinload(CaseRecord.assignee))
            .order_by(CaseRecord.updated_at.desc())
            .limit(8)
        )
    )

    return {
        "metrics": {
            "open_cases": len(open_cases),
            "overdue_cases": len(overdue),
            "due_soon": len(due_soon),
            "pending_approvals": int(pending_approvals),
            "completed_30d": int(completed_30d),
            "sla_compliance_pct": round((on_time_closed / measured_closed * 100), 1)
            if measured_closed
            else None,
        },
        "workflow_open": [
            {"workflow": name, "count": int(count)} for name, count in workflow_open_rows
        ],
        "bottlenecks": bottlenecks[:8],
        "workload": workload[:8],
        "cycle_times": workflow_cycle,
        "recent_cases": recent_cases,
        "overdue_references": {item.reference for item in overdue},
    }
