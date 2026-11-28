"""Administering skills needs the explicit skill permission, not drafting.admin.

PolicyService.has_permission lets drafting.admin satisfy every drafting.*
check. Production roles reporter, settings_manager and limited_user hold
drafting.admin, so through that implication their holders could import,
publish, deprecate or pin system skills, which every organisation uses.
The admin routes now require the role to hold drafting.skill.publish /
drafting.skill.bind itself (or Super Admin).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rbac_backend.core.security import get_current_user
from rbac_backend.routers import skills as skills_router

ROLE_PERMISSIONS = {
    # Production shape: drafting.admin, no drafting.skill.*.
    "limited_user": {"drafting.admin", "drafting.request.create"},
    "reporter": {"drafting.admin"},
    "settings_manager": {"drafting.admin"},
    "skill_publisher": {"drafting.skill.publish"},
    "skill_binder": {"drafting.skill.bind"},
}


class _Permissions:
    def __init__(self, role: str) -> None:
        self.role = role

    async def user_has_permission(self, user_id, permission, log=True):
        held = ROLE_PERMISSIONS.get(self.role, set())
        return "*" in held or permission in held


class _Policy:
    def __init__(self, role: str) -> None:
        self.permission_service = _Permissions(role)
        self.scope_service = SimpleNamespace(is_superadmin=lambda user: role == "superadmin")

    async def authorize(self, *args, **kwargs):
        # The existing policy lets drafting.admin through; the explicit check
        # must refuse before this is reached.
        return None


class _Registry:
    async def admin_list(self, *a, **k):
        return {"skills": [], "versions": [], "bindings": []}

    async def publish(self, *a, **k):
        raise RuntimeError("reached publish")

    async def deprecate(self, *a, **k):
        raise RuntimeError("reached deprecate")

    async def set_binding(self, *a, **k):
        raise RuntimeError("reached bind")


def _client(monkeypatch, role: str) -> TestClient:
    monkeypatch.setattr(skills_router.settings, "LETTER_BENCH_ROLLOUT_MODE", "primary", raising=False)
    app = FastAPI()
    app.include_router(skills_router.router, prefix="/api")
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id="u1", roles=[role], organization_id="org1", projects=[]
    )
    app.dependency_overrides[skills_router.get_policy_service] = lambda: _Policy(role)
    app.dependency_overrides[skills_router.get_skill_registry] = lambda: _Registry()
    return TestClient(app, raise_server_exceptions=False)


PUBLISH_ROUTES = [
    ("get", "/api/skills/admin", None),
    ("post", "/api/skills/import", "multipart"),
    ("post", "/api/skills/indian-infra-variations/versions/9.9.9/publish",
     {"eval_report": {"suite": "golden", "score": 1, "threshold": 0.8, "case_count": 5}}),
    ("post", "/api/skills/indian-infra-variations/versions/9.9.9/deprecate", None),
]
BIND = ("put", "/api/skills/bindings", {"organization_id": "org1", "skill_key": "indian-infra-variations"})


def _call(client, method, path, body):
    if body == "multipart":
        return client.post(path, files={"file": ("s.zip", b"PK", "application/zip")},
                           data={"version": "9.9.9", "kind": "drafting", "manifest": "{}"})
    return getattr(client, method)(path, json=body) if body is not None else getattr(client, method)(path)


@pytest.mark.parametrize("role", ["limited_user", "reporter", "settings_manager"])
@pytest.mark.parametrize("method,path,body", [*PUBLISH_ROUTES, BIND])
def test_drafting_admin_alone_cannot_administer_skills(monkeypatch, role, method, path, body) -> None:
    response = _call(_client(monkeypatch, role), method, path, body)
    assert response.status_code == 403, (role, path, response.status_code, response.text)


@pytest.mark.parametrize("method,path,body", PUBLISH_ROUTES)
def test_explicit_publish_permission_passes_the_gate(monkeypatch, method, path, body) -> None:
    response = _call(_client(monkeypatch, "skill_publisher"), method, path, body)
    assert response.status_code != 403, (path, response.text)


def test_explicit_publish_permission_does_not_grant_binding(monkeypatch) -> None:
    method, path, body = BIND
    assert _call(_client(monkeypatch, "skill_publisher"), method, path, body).status_code == 403
    assert _call(_client(monkeypatch, "skill_binder"), method, path, body).status_code != 403


@pytest.mark.parametrize("method,path,body", [*PUBLISH_ROUTES, BIND])
def test_super_admin_passes_the_gate(monkeypatch, method, path, body) -> None:
    response = _call(_client(monkeypatch, "superadmin"), method, path, body)
    assert response.status_code != 403, (path, response.text)
