"""Shared automated-test fixtures.

Copyright © 2026 Gateway Information Group LLC. All rights reserved.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


@pytest.fixture()
def client(tmp_path: Path):
    settings = Settings(
        root=tmp_path,
        database_path=tmp_path / "data" / "test_workflow_cases.db",
        testing=True,
        seed_demo=True,
    )
    application = create_app(settings)
    with TestClient(application) as test_client:
        yield test_client
