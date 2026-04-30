"""Smoke checks for user endpoints that require seeded auth/database state."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from rbac_backend.main import app


pytestmark = pytest.mark.skip(
    reason=(
        "Requires seeded auth/database state and stable fixture data. "
        "Run as an environment-backed API smoke test instead of CI unit coverage."
    )
)

client = TestClient(app)


def test_create_user() -> None:
    response = client.post(
        "/api/users",
        json={
            "username": "testuser",
            "email": "testuser@example.com",
            "first_name": "Test",
            "last_name": "User",
            "password": "password123",
            "roles": ["orgadmin"],
            "organization_id": "org_id_example",
        },
    )
    assert response.status_code == 200
    assert "id" in response.json()


def test_read_users() -> None:
    response = client.get("/api/users")
    assert response.status_code == 200
    assert isinstance(response.json().get("users"), list)


def test_read_user() -> None:
    response = client.get("/api/users/testuser_id")
    assert response.status_code == 200
    assert "email" in response.json()
