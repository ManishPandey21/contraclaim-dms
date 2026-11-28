"""The skill registry API is dark while LETTER_BENCH_ROLLOUT_MODE is off.

System skills apply to every organisation, and has_permission treats
drafting.admin as covering every drafting.* permission. A role that holds
drafting.admin could therefore import, publish or pin skills as soon as the
release is deployed, while the Bench itself was still switched off. With the
mode off every /skills route answers 404, as every Bench route does.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rbac_backend.core.security import get_current_user
from rbac_backend.routers import skills as skills_router


class _Policy:
    async def authorize(self, *args, **kwargs):
        return None

    async def has_permission(self, *args, **kwargs):
        return True


class _Registry:
    async def list_effective(self, *args, **kwargs):
        return []

    async def admin_list(self, *args, **kwargs):
        return {"skills": [], "versions": [], "bindings": []}


def _client(monkeypatch, mode: str) -> TestClient:
    monkeypatch.setattr(skills_router.settings, "LETTER_BENCH_ROLLOUT_MODE", mode, raising=False)
    app = FastAPI()
    app.include_router(skills_router.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id="u1", roles=["limited_user"], organization_id="org1", projects=[]
    )
    app.dependency_overrides[skills_router.get_policy_service] = lambda: _Policy()
    app.dependency_overrides[skills_router.get_skill_registry] = lambda: _Registry()
    return TestClient(app)


ROUTES = [
    ("get", "/api/skills?organization_id=org1"),
    ("get", "/api/skills/admin"),
    ("get", "/api/skills/indian-infra-variations/versions/1.0.0"),
    ("post", "/api/skills/import"),
    ("post", "/api/skills/indian-infra-variations/versions/9.9.9/publish"),
    ("post", "/api/skills/indian-infra-variations/versions/9.9.9/deprecate"),
    ("put", "/api/skills/bindings"),
]


@pytest.mark.parametrize("method,path", ROUTES)
def test_every_skills_route_answers_404_while_the_bench_is_off(monkeypatch, method, path) -> None:
    client = _client(monkeypatch, "off")
    response = getattr(client, method)(path)
    assert response.status_code == 404, (path, response.status_code, response.text)


@pytest.mark.parametrize("mode", ["canary", "primary"])
def test_the_registry_answers_once_the_bench_is_on(monkeypatch, mode) -> None:
    client = _client(monkeypatch, mode)
    assert client.get("/api/skills?organization_id=org1").status_code == 200
