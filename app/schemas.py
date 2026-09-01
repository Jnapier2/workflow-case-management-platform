"""API request models.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class CaseCreateRequest(BaseModel):
    actor_id: int | None = None
    workflow_key: str
    title: str = Field(min_length=1, max_length=240)
    description: str = Field(default="", max_length=5000)
    requester_name: str = Field(min_length=1, max_length=160)
    requester_email: str = Field(min_length=3, max_length=200)
    priority: Literal["Low", "Medium", "High", "Critical"] = "Medium"
    fields: dict[str, Any] = Field(default_factory=dict)


class TransitionRequest(BaseModel):
    actor_id: int | None = None
    target_stage: str
    note: str = Field(default="", max_length=2000)


class ApprovalDecisionRequest(BaseModel):
    actor_id: int | None = None
    decision: Literal["Approved", "Rejected"]
    note: str = Field(default="", max_length=2000)
