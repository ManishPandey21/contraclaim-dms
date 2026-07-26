from __future__ import annotations

from copy import deepcopy

from bson import ObjectId
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rbac_backend.core import security
from rbac_backend.core.config import settings
from rbac_backend.core.security import CurrentUser, get_password_hash
from rbac_backend.routers import auth
import rbac_backend.services.authentication_service as authentication_service
import rbac_backend.services.permission_service as permission_service
import rbac_backend.utils.audit_logger as audit_logger


class _Collection:
    def __init__(self, docs=None):
        self.docs = [deepcopy(doc) for doc in (docs or [])]

    async def find_one(self, query):
        for doc in self.docs:
            if self._matches(doc, query):
                return deepcopy(doc)
        return None

    async def update_one(self, query, update):
        for doc in self.docs:
            if not self._matches(doc, query):
                continue
            for key, value in (update.get("$set") or {}).items():
                doc[key] = value
            for key, value in (update.get("$inc") or {}).items():
                doc[key] = doc.get(key, 0) + value
            return None
        return None

    async def insert_one(self, doc):
        self.docs.append(deepcopy(doc))
        return None

    @staticmethod
    def _matches(doc, query):
        for key, value in query.items():
            if doc.get(key) != value:
                return False
        return True


class _FakeDb:
    def __init__(self):
        self.superadmin_id = ObjectId()
        self.users = _Collection(
            [
                {
                    "_id": self.superadmin_id,
                    "username": "superadmin",
                    "email": "superadmin@example.com",
                    "hashed_password": get_password_hash("password"),
                    "roles": ["superadmin"],
                    "projects": [],
                    "disabled": False,
                }
            ]
        )
        self.organizations = _Collection()
        self.roles = _Collection()
        self.audit_logs = _Collection()


def _make_client(monkeypatch):
    # This unit intentionally uses the in-process session double. Production
    # Redis fail-closed behavior is covered in test_session_fail_closed.py.
    monkeypatch.setattr(settings, "AUTH_SESSION_FAIL_CLOSED", False)
    db = _FakeDb()

    async def fake_get_db():
        yield db

    async def fake_get_database():
        return db

    monkeypatch.setattr(authentication_service, "get_database", fake_get_database)
    monkeypatch.setattr(audit_logger, "get_database", fake_get_database)
    monkeypatch.setattr(permission_service, "get_database", fake_get_database)

    app = FastAPI()
    app.include_router(auth.router, prefix="/api")
    app.dependency_overrides[auth.get_db] = fake_get_db
    app.dependency_overrides[security.get_db] = fake_get_db
    # Production correctly marks authentication cookies Secure.  Use an HTTPS
    # origin so the client exercises the deployed cookie policy instead of
    # silently discarding the session cookie on an artificial HTTP origin.
    return TestClient(app, base_url="https://web.contraclaim.com")


def test_superadmin_login_sets_cookie_and_me_resolves_session(monkeypatch):
    client = _make_client(monkeypatch)

    login_response = client.post(
        "/api/login",
        json={"email": "superadmin@example.com", "password": "password"},
    )

    assert login_response.status_code == 200
    assert login_response.json()["user"]["roles"] == ["superadmin"]
    assert settings.AUTH_COOKIE_NAME in client.cookies

    me_response = client.get("/api/me")

    assert me_response.status_code == 200
    assert me_response.json()["email"] == "superadmin@example.com"
    assert me_response.json()["roles"] == ["superadmin"]
    assert me_response.json()["permissions"] == ["*"]


def test_superadmin_me_uses_valid_cookie_when_legacy_bearer_header_is_stale(monkeypatch):
    client = _make_client(monkeypatch)

    login_response = client.post(
        "/api/login",
        json={"email": "superadmin@example.com", "password": "password"},
    )
    assert login_response.status_code == 200

    me_response = client.get(
        "/api/me",
        headers={"Authorization": "Bearer stale-token-from-old-build"},
    )

    assert me_response.status_code == 200
    assert me_response.json()["email"] == "superadmin@example.com"
    assert me_response.json()["roles"] == ["superadmin"]


def test_me_controller_returns_effective_permissions():
    class ExplodingRateLimiter:
        async def check_user_limit(self, _user_id):
            raise AssertionError("/me should not rate-limit session verification")

    class UserService:
        async def get_user_by_id(self, _user_id):
            return current_user

    class PermissionService:
        async def get_effective_permission_names(self, _user_id):
            return ["dms.document.view", "dms.document.upload"]

    controller = auth.AuthController(
        auth_service=object(),
        user_service=UserService(),
        authorization_service=object(),
        rate_limiter=ExplodingRateLimiter(),
        audit_logger=object(),
    )
    controller.permission_service = PermissionService()

    current_user = CurrentUser(
        id="68cffb41b50384feb94ff90f",
        username="superadmin",
        email="superadmin@example.com",
        roles=["superadmin"],
        organization_id=None,
        organizations=[],
        projects=[],
        disabled=False,
    )

    import anyio

    resolved = anyio.run(controller.get_current_user_info, current_user)

    assert resolved["email"] == "superadmin@example.com"
    assert resolved["roles"] == ["superadmin"]
    assert resolved["permissions"] == ["dms.document.view", "dms.document.upload"]
