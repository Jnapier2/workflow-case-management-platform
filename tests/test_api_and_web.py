"""API and browser workflow tests.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

from datetime import date, timedelta
import re

from sqlalchemy import select

from app.models import Attachment, CaseRecord


def _payload() -> dict:
    return {
        "workflow_key": "vendor_onboarding",
        "title": "Onboard Atlas Data",
        "description": "Portfolio API creation test.",
        "requester_name": "Avery Example",
        "requester_email": "avery@example.test",
        "priority": "High",
        "fields": {
            "vendor_name": "Atlas Data",
            "service_category": "Software",
            "annual_spend": 72000,
            "handles_personal_data": True,
            "country": "United States",
            "business_owner": "Data Operations",
            "requested_start_date": (date.today() + timedelta(days=20)).isoformat(),
            "risk_notes": "Controlled analytics access.",
        },
    }


def _csrf(client, path: str) -> str:
    response = client.get(path)
    assert response.status_code == 200
    match = re.search(r'name="csrf_token" value="([^"]+)"', response.text)
    assert match
    return match.group(1)


def test_api_create_transition_and_audit(client):
    created = client.post("/api/v1/cases", json=_payload())
    assert created.status_code == 201, created.text
    case = created.json()
    assert case["reference"].startswith("VEN-")
    assert case["status"] == "submitted"
    assert case["assignee"]["role"] == "Case Manager"

    moved = client.post(
        f"/api/v1/cases/{case['reference']}/transitions",
        json={"target_stage": "validation", "note": "API-controlled transition"},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["status"] == "validation"

    audit = client.get(f"/api/v1/cases/{case['reference']}/audit")
    assert audit.status_code == 200
    payload = audit.json()
    assert payload["case"]["reference"] == case["reference"]
    assert payload["workflow_definition_snapshot"]["key"] == "vendor_onboarding"
    assert payload["audit_chain_verification"]["valid"] is True

    verify = client.get(f"/api/v1/cases/{case['reference']}/audit/verify")
    assert verify.status_code == 200
    assert verify.json()["valid"] is True


def test_web_validation_status_and_attachment(client):
    token = _csrf(client, "/cases/new?workflow=vendor_onboarding")
    invalid = client.post(
        "/cases",
        data={
            "csrf_token": token,
            "workflow_key": "vendor_onboarding",
            "title": "",
            "requester_name": "Avery Example",
            "requester_email": "not-an-email",
            "priority": "Medium",
        },
    )
    assert invalid.status_code == 400
    assert "Correct the following" in invalid.text

    created = client.post("/api/v1/cases", json=_payload()).json()
    reference = created["reference"]
    token = _csrf(client, f"/cases/{reference}")
    attached = client.post(
        f"/cases/{reference}/attachments",
        data={"csrf_token": token},
        files={"evidence": ("review.txt", b"controlled evidence\n", "text/plain")},
        follow_redirects=False,
    )
    assert attached.status_code == 303

    with client.app.state.SessionLocal() as db:
        case = db.scalar(select(CaseRecord).where(CaseRecord.reference == reference))
        attachment = db.scalar(select(Attachment).where(Attachment.case_id == case.id))
        assert attachment
        assert attachment.original_name == "review.txt"
        assert attachment.size_bytes == len(b"controlled evidence\n")
        download_id = attachment.id

    downloaded = client.get(f"/attachments/{download_id}")
    assert downloaded.status_code == 200
    assert downloaded.content == b"controlled evidence\n"
    assert "attachment" in downloaded.headers["content-disposition"].lower()


def test_notification_can_be_marked_read(client):
    from app.models import Notification, User

    with client.app.state.SessionLocal() as db:
        notification = db.scalar(
            select(Notification).where(Notification.is_read.is_(False)).order_by(Notification.id.asc())
        )
        assert notification
        actor = db.get(User, notification.user_id)
        assert actor
        notification_id = notification.id
        actor_id = actor.id

    token = _csrf(client, "/")
    switched = client.post(
        "/session/actor",
        data={"csrf_token": token, "actor_id": actor_id, "next": "/notifications"},
        follow_redirects=False,
    )
    assert switched.status_code == 303

    token = _csrf(client, "/notifications")
    marked = client.post(
        f"/notifications/{notification_id}/read",
        data={"csrf_token": token, "next": "/notifications"},
        follow_redirects=False,
    )
    assert marked.status_code == 303
    with client.app.state.SessionLocal() as db:
        updated = db.get(Notification, notification_id)
        assert updated and updated.is_read is True
