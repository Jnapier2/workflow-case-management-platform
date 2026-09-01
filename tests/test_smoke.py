"""Application smoke and route coverage tests.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations


from app.version import APP_VERSION


def test_health_dashboard_and_seed(client):
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["version"] == APP_VERSION

    workflows = client.get("/api/v1/workflows")
    assert workflows.status_code == 200
    assert {item["key"] for item in workflows.json()} == {
        "vendor_onboarding",
        "employee_access_request",
        "customer_complaint",
    }

    cases = client.get("/api/v1/cases")
    assert cases.status_code == 200
    assert len(cases.json()) == 9

    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "Management dashboard" in dashboard.text
    assert "Bottleneck analysis" in dashboard.text
    assert "SLA compliance" in dashboard.text

    stylesheet = client.get("/static/styles.css")
    assert stylesheet.status_code == 200
    assert "--teal-700" in stylesheet.text


def test_openapi_and_error_boundary(client):
    schema = client.get("/api/openapi.json")
    assert schema.status_code == 200
    assert "/api/v1/cases" in schema.json()["paths"]

    missing = client.get("/cases/DOES-NOT-EXIST")
    assert missing.status_code == 404
    assert "The request could not be completed" in missing.text


def test_major_browser_routes_render(client):
    cases = client.get("/api/v1/cases").json()
    references = [item["reference"] for item in cases[:3]]
    routes = [
        "/",
        "/cases",
        "/cases?priority=High",
        "/cases/new",
        "/workflows",
        "/notifications",
        "/api/docs",
        "/api/redoc",
        "/api/openapi.json",
    ]
    for workflow_key in ("vendor_onboarding", "employee_access_request", "customer_complaint"):
        routes.extend(
            [
                f"/cases/new?workflow={workflow_key}",
                f"/workflows/{workflow_key}",
                f"/api/v1/workflows/{workflow_key}",
            ]
        )
    for filename in ("vendor_onboarding.json", "employee_access_request.json", "customer_complaint.json"):
        routes.append(f"/workflows/examples/{filename}")
    for reference in references:
        routes.extend(
            [
                f"/cases/{reference}",
                f"/cases/{reference}/audit.json",
                f"/cases/{reference}/audit.csv",
                f"/api/v1/cases/{reference}",
                f"/api/v1/cases/{reference}/audit",
                f"/api/v1/cases/{reference}/audit/verify",
            ]
        )

    failures = []
    for route in routes:
        response = client.get(route)
        if response.status_code != 200:
            failures.append((route, response.status_code, response.text[:200]))
    assert not failures
