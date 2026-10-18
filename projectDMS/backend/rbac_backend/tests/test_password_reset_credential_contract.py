"""Admin password reset and self-service change must change the credential login reads.

Defect reproduced here: user documents carry two password-hash fields.
``UserService.create_user`` writes both ``passwordHash`` and ``hashed_password``;
the admin reset (``PUT /api/users/{id}`` with ``password``) and the self-service
``change_password`` wrote only ``passwordHash``. Every login reader resolves
``hashed_password`` first, so after a reset login kept verifying the *old*
hash while the API reported success.

Canonical credential field: ``hashed_password``. ``passwordHash`` is a legacy
read-only fallback for documents that never had the canonical field.

No plaintext password or full hash is printed by these tests.
"""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest
from bson import ObjectId
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from rbac_backend.core import security
from rbac_backend.core.config import settings
from rbac_backend.core.security import get_password_hash, verify_password
from rbac_backend.models.user import UserCreate, UserUpdate
from rbac_backend.routers import auth
from rbac_backend.routers.users import UserController
from rbac_backend.services.authentication_service import AuthenticationService
from rbac_backend.services.user_service import UserService, UserServiceError
from rbac_backend.utils.error_handler import UserError
import rbac_backend.services.authentication_service as authentication_service
import rbac_backend.services.permission_service as permission_service
import rbac_backend.services.runtime_state as runtime_state
import rbac_backend.utils.audit_logger as audit_logger

OLD_PASSWORD = "Old-Passw0rd-A"
NEW_PASSWORD = "New-Passw0rd-B"
EMAIL = "reset-target@example.com"


class _Collection:
    def __init__(self, docs=None):
        self.docs = [deepcopy(doc) for doc in (docs or [])]
        self.fail_updates = False
        self.vanish_on_update = False

    async def find_one(self, query, *args, **kwargs):
        for doc in self.docs:
            if self._matches(doc, query):
                return deepcopy(doc)
        return None

    async def update_one(self, query, update, *args, **kwargs):
        if self.fail_updates:
            raise RuntimeError("simulated write failure")
        if self.vanish_on_update:
            self.docs = [d for d in self.docs if not self._matches(d, query)]
        for doc in self.docs:
            if not self._matches(doc, query):
                continue
            before = deepcopy(doc)
            for key, value in (update.get("$set") or {}).items():
                doc[key] = value
            for key in (update.get("$unset") or {}):
                doc.pop(key, None)
            for key, value in (update.get("$inc") or {}).items():
                doc[key] = doc.get(key, 0) + value
            return SimpleNamespace(matched_count=1, modified_count=int(before != doc))
        return SimpleNamespace(matched_count=0, modified_count=0)

    async def insert_one(self, doc, *args, **kwargs):
        doc = deepcopy(doc)
        doc.setdefault("_id", ObjectId())
        self.docs.append(doc)
        return SimpleNamespace(inserted_id=doc["_id"])

    async def count_documents(self, query, *args, **kwargs):
        return sum(1 for d in self.docs if self._matches(d, query))

    @staticmethod
    def _matches(doc, query):
        for key, value in query.items():
            if isinstance(value, dict) and "$ne" in value:
                if doc.get(key) == value["$ne"]:
                    return False
            elif doc.get(key) != value:
                return False
        return True


class _FakeDb:
    def __init__(self):
        self.users = _Collection()
        self.organizations = _Collection()
        self.roles = _Collection()
        self.audit_logs = _Collection()


class _AllowAll:
    async def require_permission(self, *args, **kwargs):
        return None

    async def check_user_access(self, *args, **kwargs):
        return None


class _NoLimit:
    async def check_user_limit(self, *args, **kwargs):
        return None


class _Audit:
    def __init__(self):
        self.updates = []

    async def log_user_updated(self, actor_id, target_id, changed_fields):
        self.updates.append((actor_id, target_id, list(changed_fields)))


class _Sessions:
    def __init__(self):
        self.invalidated = []

    async def invalidate_user_sessions(self, user_id):
        self.invalidated.append(str(user_id))


def _stored_hash(doc):
    """Exactly how every login reader resolves the stored credential."""
    return doc.get("hashed_password") or doc.get("passwordHash")


def _fingerprint(value):
    import hashlib

    return hashlib.sha256((value or "").encode()).hexdigest()[:12]


async def _created_user(db, *, email=EMAIL, password=OLD_PASSWORD, disabled=False):
    """Create the user through the real create path (writes the real field shape)."""
    service = UserService(db)
    # The admin create route hands UserService.create_user a UserCreate model.
    payload = UserCreate(
        email=email,
        username=email.split("@")[0],
        first_name="Reset",
        last_name="Target",
        roles=["user"],
        organization_id=str(ObjectId()),
        password=password,
    )
    created = await service.create_user(payload, payload.password)
    if disabled:
        await db.users.update_one({"_id": ObjectId(str(created.id))}, {"$set": {"disabled": True}})
    return str(created.id)


def _controller(db):
    audit, sessions = _Audit(), _Sessions()
    controller = UserController(
        user_service=UserService(db),
        auth_service=_AllowAll(),
        authentication_service=sessions,
        rate_limiter=_NoLimit(),
        audit_logger=audit,
        notification_service=None,
    )

    async def _response(user, **kwargs):
        if user is None:
            raise AssertionError("success response built for a user that was not updated")
        return {"id": user.id}

    controller._build_user_response = _response
    return controller, audit, sessions


async def _admin_reset(db, user_id, password=NEW_PASSWORD):
    controller, audit, sessions = _controller(db)
    actor = SimpleNamespace(id=str(ObjectId()), roles=["superadmin"])
    result = await controller.update_user(user_id, UserUpdate(password=password), actor)
    return result, audit, sessions


async def _login_reader_accepts(db, password, email=EMAIL):
    """The reader behind POST /api/login (AuthController.login_user)."""
    service = AuthenticationService()
    service._db = db
    return await service.authenticate_user_secure(email, password) is not None


def test_admin_reset_changes_the_credential_login_verifies():
    import asyncio

    async def run():
        db = _FakeDb()
        user_id = await _created_user(db)
        assert await _login_reader_accepts(db, OLD_PASSWORD)
        before = _stored_hash(await db.users.find_one({"_id": ObjectId(user_id)}))

        await _admin_reset(db, user_id)

        doc = await db.users.find_one({"_id": ObjectId(user_id)})
        after = _stored_hash(doc)
        assert _fingerprint(before) != _fingerprint(after), "stored hash did not change"
        assert verify_password(NEW_PASSWORD, after) is True
        assert verify_password(OLD_PASSWORD, after) is False
        assert await _login_reader_accepts(db, NEW_PASSWORD) is True
        assert await _login_reader_accepts(db, OLD_PASSWORD) is False

    asyncio.run(run())


def test_reset_leaves_one_credential_field_no_divergent_copy():
    import asyncio

    async def run():
        db = _FakeDb()
        user_id = await _created_user(db)
        await _admin_reset(db, user_id)
        doc = await db.users.find_one({"_id": ObjectId(user_id)})
        assert "hashed_password" in doc
        assert "passwordHash" not in doc, "a second, divergent credential field survives"

    asyncio.run(run())


def test_create_writes_only_the_canonical_field():
    import asyncio

    async def run():
        db = _FakeDb()
        user_id = await _created_user(db)
        doc = await db.users.find_one({"_id": ObjectId(user_id)})
        assert verify_password(OLD_PASSWORD, doc["hashed_password"])
        assert "passwordHash" not in doc

    asyncio.run(run())


def test_create_never_persists_the_plaintext_password():
    import asyncio

    async def run():
        db = _FakeDb()
        user_id = await _created_user(db)
        doc = await db.users.find_one({"_id": ObjectId(user_id)})
        assert "password" not in doc, "plaintext password persisted on the user document"
        assert OLD_PASSWORD not in repr(doc)

    asyncio.run(run())


def test_reset_repairs_a_document_already_carrying_divergent_fields():
    """Users reset before the fix hold a stale hashed_password and a newer passwordHash."""
    import asyncio

    async def run():
        db = _FakeDb()
        uid = ObjectId()
        db.users.docs.append(
            {
                "_id": uid,
                "email": EMAIL,
                "username": "reset-target",
                "roles": ["user"],
                "projects": [],
                "disabled": False,
                "hashed_password": get_password_hash(OLD_PASSWORD),
                "passwordHash": get_password_hash("Lost-Reset-0ne"),
            }
        )
        await _admin_reset(db, str(uid))
        assert await _login_reader_accepts(db, NEW_PASSWORD) is True
        doc = await db.users.find_one({"_id": uid})
        assert "passwordHash" not in doc

    asyncio.run(run())


def test_password_reset_invalidates_existing_sessions():
    import asyncio

    async def run():
        db = _FakeDb()
        user_id = await _created_user(db)
        _, audit, sessions = await _admin_reset(db, user_id)
        assert sessions.invalidated == [user_id]
        # Audit names the credential change without carrying any secret value.
        (_, target, fields), = audit.updates
        assert target == user_id
        assert "hashed_password" in fields
        assert NEW_PASSWORD not in repr(audit.updates)

    asyncio.run(run())


def test_database_write_failure_is_not_success():
    import asyncio

    async def run():
        db = _FakeDb()
        user_id = await _created_user(db)
        db.users.fail_updates = True
        with pytest.raises(HTTPException) as exc:
            await _admin_reset(db, user_id)
        assert exc.value.status_code == 500
        assert NEW_PASSWORD not in str(exc.value.detail)

    asyncio.run(run())


def test_unmatched_update_is_not_success():
    import asyncio

    async def run():
        db = _FakeDb()
        user_id = await _created_user(db)
        db.users.vanish_on_update = True  # deleted between lookup and write
        with pytest.raises(UserError) as exc:
            await _admin_reset(db, user_id)
        assert exc.value.http_status == 404

    asyncio.run(run())


def test_policy_violation_is_not_success():
    import asyncio

    async def run():
        db = _FakeDb()
        user_id = await _created_user(db)
        before = _stored_hash(await db.users.find_one({"_id": ObjectId(user_id)}))
        with pytest.raises(UserError) as exc:
            await _admin_reset(db, user_id, password="short")
        assert exc.value.http_status == 400
        after = _stored_hash(await db.users.find_one({"_id": ObjectId(user_id)}))
        assert before == after

    asyncio.run(run())


@pytest.mark.parametrize("padded", [" " + NEW_PASSWORD, NEW_PASSWORD + " ", NEW_PASSWORD + "\t"])
def test_reset_refuses_a_password_login_would_strip(padded):
    """POST /api/login strips the submitted password before verifying it.

    A stored secret with leading/trailing whitespace can therefore never
    authenticate, so the reset must refuse it instead of reporting success.
    """
    import asyncio

    async def run():
        db = _FakeDb()
        user_id = await _created_user(db)
        before = _stored_hash(await db.users.find_one({"_id": ObjectId(user_id)}))
        with pytest.raises(UserError) as exc:
            await _admin_reset(db, user_id, password=padded)
        assert exc.value.http_status == 400
        after = _stored_hash(await db.users.find_one({"_id": ObjectId(user_id)}))
        assert before == after
        assert await _login_reader_accepts(db, OLD_PASSWORD) is True

    asyncio.run(run())


def test_self_service_change_password_changes_the_credential_login_verifies():
    import asyncio

    async def run():
        db = _FakeDb()
        user_id = await _created_user(db)
        assert await UserService(db).change_password(user_id, OLD_PASSWORD, NEW_PASSWORD)
        assert await _login_reader_accepts(db, NEW_PASSWORD) is True
        assert await _login_reader_accepts(db, OLD_PASSWORD) is False

    asyncio.run(run())


def test_self_service_change_password_works_for_canonical_only_documents():
    """Seeded and SSO-era documents carry only hashed_password."""
    import asyncio

    async def run():
        db = _FakeDb()
        uid = ObjectId()
        db.users.docs.append(
            {
                "_id": uid,
                "email": EMAIL,
                "username": "reset-target",
                "roles": ["user"],
                "projects": [],
                "disabled": False,
                "hashed_password": get_password_hash(OLD_PASSWORD),
            }
        )
        assert await UserService(db).change_password(str(uid), OLD_PASSWORD, NEW_PASSWORD)
        assert await _login_reader_accepts(db, NEW_PASSWORD) is True

    asyncio.run(run())


# --- normal HTTP login (POST /api/login) ------------------------------------


def _login_client(monkeypatch, db):
    monkeypatch.setattr(settings, "AUTH_SESSION_FAIL_CLOSED", False)

    async def fake_get_db():
        yield db

    async def fake_get_database():
        return db

    monkeypatch.setattr(authentication_service, "get_database", fake_get_database)
    monkeypatch.setattr(audit_logger, "get_database", fake_get_database)
    monkeypatch.setattr(permission_service, "get_database", fake_get_database)

    # In-process session/lockout doubles: the process-global RuntimeStateService
    # would otherwise cache a Redis client bound to an earlier test's loop.
    async def no_redis(self):
        return None

    monkeypatch.setattr(runtime_state.RuntimeStateService, "get_redis", no_redis)
    app = FastAPI()
    app.include_router(auth.router, prefix="/api")
    app.dependency_overrides[auth.get_db] = fake_get_db
    app.dependency_overrides[security.get_db] = fake_get_db
    return TestClient(app, base_url="https://web.contraclaim.com")


def _login(client, password, email=EMAIL):
    return client.post("/api/login", json={"email": email, "password": password})


@pytest.mark.parametrize("email", ["reset-a@example.com", "reset-b@example.com"])
def test_http_login_after_admin_reset(monkeypatch, email):
    import asyncio

    db = _FakeDb()
    user_id = asyncio.run(_created_user(db, email=email))
    # One portal loop for every request: login-path state is loop-bound.
    with _login_client(monkeypatch, db) as client:
        assert _login(client, OLD_PASSWORD, email).status_code == 200

        asyncio.run(_admin_reset(db, user_id))

        client.cookies.clear()  # unauthenticated: no carried session cookie
        assert _login(client, OLD_PASSWORD, email).status_code == 401
        assert _login(client, NEW_PASSWORD, email).status_code == 200


def test_http_login_disabled_account_is_distinguishable_from_bad_password(monkeypatch):
    import asyncio

    db = _FakeDb()
    user_id = asyncio.run(_created_user(db, disabled=True))
    asyncio.run(_admin_reset(db, user_id))
    with _login_client(monkeypatch, db) as client:
        bad = _login(client, "Wrong-Passw0rd-C")
        disabled = _login(client, NEW_PASSWORD)
    assert bad.status_code == 401
    assert disabled.status_code in (401, 403)
    assert disabled.json() != bad.json(), "disabled account reads as a bad password"


# --- E: exposure, compatibility, policy and tenancy ---------------------------


def _doc(**fields):
    base = {
        "_id": ObjectId(),
        "email": EMAIL,
        "username": "reset-target",
        "first_name": "Reset",
        "last_name": "Target",
        "roles": ["user"],
        "projects": [],
        "disabled": False,
    }
    base.update(fields)
    return base


def test_reset_failure_does_not_echo_internal_exception_text():
    import asyncio

    async def run():
        db = _FakeDb()
        user_id = await _created_user(db)
        db.users.fail_updates = True
        with pytest.raises(HTTPException) as exc:
            await _admin_reset(db, user_id)
        assert exc.value.detail == "User update failed"

    asyncio.run(run())


def test_canonical_field_wins_and_legacy_is_never_also_accepted():
    """Compatibility rule: hashed_password wins; passwordHash only when it is absent."""
    import asyncio

    async def run():
        db = _FakeDb()
        db.users.docs.append(
            _doc(
                hashed_password=get_password_hash(OLD_PASSWORD),
                passwordHash=get_password_hash("Legacy-Passw0rd-D"),
            )
        )
        assert await _login_reader_accepts(db, OLD_PASSWORD) is True
        # A conflicting legacy copy must not become a second accepted password.
        assert await _login_reader_accepts(db, "Legacy-Passw0rd-D") is False

    asyncio.run(run())


def test_legacy_only_document_still_authenticates():
    import asyncio

    async def run():
        db = _FakeDb()
        db.users.docs.append(_doc(passwordHash=get_password_hash(OLD_PASSWORD)))
        assert await _login_reader_accepts(db, OLD_PASSWORD) is True

    asyncio.run(run())


def test_self_service_change_enforces_the_admin_reset_policy(monkeypatch):
    import asyncio

    _record_session_invalidation(monkeypatch)

    from rbac_backend.routers.profiles import change_my_password
    from rbac_backend.schemas.profile import ChangePassword

    async def run():
        db = _FakeDb()
        user_id = await _created_user(db)
        before = _stored_hash(await db.users.find_one({"_id": ObjectId(user_id)}))
        actor = SimpleNamespace(id=user_id, roles=["user"])
        for weak in ("short", "alllowercase1", " " + NEW_PASSWORD):
            with pytest.raises(HTTPException) as exc:
                await change_my_password(
                    ChangePassword(current_password=OLD_PASSWORD, new_password=weak),
                    current_user=actor,
                    db=db,
                )
            assert exc.value.status_code == 400
            assert weak not in str(exc.value.detail)
        after = _stored_hash(await db.users.find_one({"_id": ObjectId(user_id)}))
        assert before == after

        ok = await change_my_password(
            ChangePassword(current_password=OLD_PASSWORD, new_password=NEW_PASSWORD),
            current_user=actor,
            db=db,
        )
        assert NEW_PASSWORD not in repr(ok)
        assert await _login_reader_accepts(db, NEW_PASSWORD) is True

    asyncio.run(run())


@pytest.mark.parametrize(
    "label, body",
    [
        ("too short", {"password": "Ab1sh"}),
        ("fails a validator", {"password": "weakpassword"}),
        ("too long", {"password": "A1" + "x" * 200}),
        ("missing field, whole body is the input", {"password": "Body-Passw0rd-H", "email": None}),
    ],
)
def test_validation_error_responses_never_echo_the_password(label, body):
    from fastapi.exceptions import RequestValidationError

    from rbac_backend.core.errors import (
        redacted_request_validation_handler,
        register_domain_error_handler,
        register_request_validation_redaction,
    )
    from rbac_backend.main import app as production_app

    # The production app must carry the redacting handler, not FastAPI's default.
    assert (
        production_app.exception_handlers.get(RequestValidationError)
        is redacted_request_validation_handler
    )

    app = FastAPI()
    register_domain_error_handler(app)  # exactly the registration main.py does
    register_request_validation_redaction(app)

    @app.post("/users")
    async def create(payload: UserCreate):  # pragma: no cover - never reached
        return {}

    payload = {"username": "abc", "email": "a@b.co", "first_name": "a", "last_name": "b"}
    payload.update(body)
    payload = {k: v for k, v in payload.items() if v is not None}
    response = TestClient(app).post("/users", json=payload)
    assert response.status_code == 422, label
    assert body["password"] not in response.text, label
    assert isinstance(response.json()["detail"], list)  # FastAPI's default shape kept
    assert all("msg" in e and "loc" in e for e in response.json()["detail"])


async def _passthrough(data):
    return data


async def _nothing(*args, **kwargs):
    return None


def _create_controller(db):
    controller, audit, sessions = _controller(db)
    controller._validate_user_input = _passthrough
    controller._resolve_assigned_roles = _passthrough
    controller._validate_user_context = _nothing
    return controller, audit


def _create_payload(secret):
    return UserCreate(
        email=EMAIL, username="reset-target", first_name="R", last_name="T",
        roles=["user"], organization_id=str(ObjectId()), password=secret,
    )


def test_create_validation_failure_message_never_echoes_the_password():
    import asyncio

    from pydantic import ValidationError

    secret = "Plain-Secret-9"

    async def run():
        db = _FakeDb()
        controller, _ = _create_controller(db)

        async def explode(data, password):
            # A model-level failure whose input dict carries the plaintext.
            raise ValidationError.from_exception_data(
                "User",
                [{"type": "missing", "loc": ("first_name",), "input": {"password": password}}],
            )

        controller.user_service.check_user_exists = _nothing
        controller.user_service.create_user = explode
        actor = SimpleNamespace(id=str(ObjectId()), roles=["superadmin"])
        with pytest.raises(UserError) as exc:
            await controller.create_user(_create_payload(secret), actor)
        assert exc.value.http_status == 400
        assert secret not in str(exc.value)

    asyncio.run(run())


def test_create_response_and_audit_carry_no_credential():
    import asyncio

    secret = "Fresh-Passw0rd-E"

    async def run():
        db = _FakeDb()
        controller, audit = _create_controller(db)
        del controller._build_user_response  # use the real response builder
        created = []

        async def log_user_created(*args, **kwargs):
            created.append((args, kwargs))

        audit.log_user_created = log_user_created
        actor = SimpleNamespace(id=str(ObjectId()), roles=["superadmin"])
        response = await controller.create_user(_create_payload(secret), actor)

        doc = db.users.docs[0]
        assert "password" not in doc
        for blob in (response.model_dump_json(), repr(created)):
            assert secret not in blob
            assert doc["hashed_password"] not in blob

    asyncio.run(run())


def test_credential_flows_never_log_password_or_hash(caplog):
    import asyncio
    import logging

    third, wrong = "Third-Passw0rd-F", "Wrong-Passw0rd-G"

    async def run():
        db = _FakeDb()
        user_id = await _created_user(db)
        await _admin_reset(db, user_id)
        await UserService(db).change_password(user_id, NEW_PASSWORD, third)
        await _login_reader_accepts(db, third)
        await _login_reader_accepts(db, wrong)
        return _stored_hash(await db.users.find_one({"_id": ObjectId(user_id)}))

    with caplog.at_level(logging.DEBUG):
        final_hash = asyncio.run(run())
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert caplog.records, "control: these flows are expected to log"
    for secret in (OLD_PASSWORD, NEW_PASSWORD, third, wrong, final_hash):
        assert secret not in text


@pytest.mark.parametrize(
    "actor_roles, same_org, actor_projects, allowed",
    [
        (["orgadmin"], True, [], True),  # positive control: same organisation
        (["orgadmin"], False, [], False),  # foreign organisation
        (["projectadmin"], True, ["p-other"], False),  # same org, no project overlap
        (["projectadmin"], False, ["p-1"], False),  # foreign organisation
        (["user"], True, [], False),  # ordinary user
    ],
)
def test_cross_scope_reset_is_denied_and_writes_nothing(
    actor_roles, same_org, actor_projects, allowed
):
    import asyncio

    from rbac_backend.services.authorization_service import AuthorizationService
    from rbac_backend.utils.error_handler import AuthorizationError

    async def run():
        db = _FakeDb()
        target_org = str(ObjectId())
        uid = ObjectId()
        db.users.docs.append(
            _doc(
                _id=uid,
                organization_id=target_org,
                projects=["p-1"],
                hashed_password=get_password_hash(OLD_PASSWORD),
            )
        )
        controller, _, _ = _controller(db)

        # Real per-user scope check; only the permission-name gate is opened.
        class _RealScope(AuthorizationService):
            async def require_permission(self, *args, **kwargs):
                return None

        controller.auth_service = _RealScope()
        actor = SimpleNamespace(
            id=str(ObjectId()),
            roles=actor_roles,
            organization_id=target_org if same_org else str(ObjectId()),
            organizations=[],
            projects=actor_projects,
        )
        before = _stored_hash(await db.users.find_one({"_id": uid}))
        if allowed:
            await controller.update_user(str(uid), UserUpdate(password=NEW_PASSWORD), actor)
            assert await _login_reader_accepts(db, NEW_PASSWORD) is True
        else:
            with pytest.raises(AuthorizationError):
                await controller.update_user(str(uid), UserUpdate(password=NEW_PASSWORD), actor)
            assert _stored_hash(await db.users.find_one({"_id": uid})) == before

    asyncio.run(run())


# --- F: owner checklist additions (2026-10-18) ---------------------------------


_NEW_USER = {"username": "abc", "email": "a@b.co", "first_name": "a", "last_name": "b"}


@pytest.mark.parametrize(
    "label, method, url, body, expected",
    [
        ("create, wrong type", "post", "/api/users",
         {**_NEW_USER, "password": {"nested": "Probe-Secret-77"}}, 422),
        # UserCreatePayload has no policy validator: the controller refuses with 400.
        ("create, weak password", "post", "/api/users",
         {**_NEW_USER, "password": "Probe-secret-weak"}, 400),
        ("login, whole body is the input", "post", "/api/login", {"password": "Probe-Secret-77"}, 422),
        ("admin reset, wrong type", "put", "/api/users/6ad3d0000000000000000001",
         {"password": {"nested": "Probe-Secret-77"}}, 422),
        ("self-service change, whole body is the input", "post",
         "/api/profiles/change-password", {"new_password": "Probe-Secret-77"}, 422),
    ],
)
def test_password_bearing_routes_never_echo_the_password(label, method, url, body, expected):
    from rbac_backend.main import app as production_app

    secret = body["password"] if isinstance(body.get("password"), str) else "Probe-Secret-77"

    async def fake_user():
        return SimpleNamespace(
            id=str(ObjectId()), roles=["superadmin"], organization_id=None,
            projects=[], email="a@b.co", permissions=["*"], disabled=False,
        )

    production_app.dependency_overrides[security.get_current_user] = fake_user
    try:
        client = TestClient(
            production_app, base_url="https://web.contraclaim.com", raise_server_exceptions=False
        )
        response = getattr(client, method)(url, json=body)
    finally:
        production_app.dependency_overrides.pop(security.get_current_user, None)
    assert response.status_code == expected, (label, response.status_code)
    assert secret not in response.text, label
    if expected == 422:
        assert all("msg" in e and "input" not in e for e in response.json()["detail"]), label


def _record_session_invalidation(monkeypatch):
    calls = []

    async def record(self, user_id):
        calls.append(str(user_id))

    monkeypatch.setattr(AuthenticationService, "invalidate_user_sessions", record)
    return calls


def test_self_service_change_removes_legacy_field_and_ends_sessions(monkeypatch):
    import asyncio

    from rbac_backend.routers.profiles import change_my_password
    from rbac_backend.schemas.profile import ChangePassword

    calls = _record_session_invalidation(monkeypatch)

    async def run():
        db = _FakeDb()
        uid = ObjectId()
        db.users.docs.append(
            _doc(
                _id=uid,
                hashed_password=get_password_hash(OLD_PASSWORD),
                passwordHash=get_password_hash("Legacy-Passw0rd-D"),
            )
        )
        actor = SimpleNamespace(id=str(uid), roles=["user"])
        await change_my_password(
            ChangePassword(current_password=OLD_PASSWORD, new_password=NEW_PASSWORD),
            current_user=actor,
            db=db,
        )
        doc = await db.users.find_one({"_id": uid})
        assert "passwordHash" not in doc
        assert verify_password(NEW_PASSWORD, doc["hashed_password"])
        assert await _login_reader_accepts(db, NEW_PASSWORD) is True
        assert await _login_reader_accepts(db, OLD_PASSWORD) is False
        return str(uid)

    user_id = asyncio.run(run())
    assert calls == [user_id]


def test_failed_self_service_change_ends_no_sessions(monkeypatch):
    import asyncio

    from rbac_backend.routers.profiles import change_my_password
    from rbac_backend.schemas.profile import ChangePassword

    calls = _record_session_invalidation(monkeypatch)

    async def run():
        db = _FakeDb()
        user_id = await _created_user(db)
        actor = SimpleNamespace(id=user_id, roles=["user"])
        with pytest.raises(HTTPException) as exc:
            await change_my_password(
                ChangePassword(current_password="Wrong-Passw0rd-G", new_password=NEW_PASSWORD),
                current_user=actor,
                db=db,
            )
        assert exc.value.status_code == 400
        assert await _login_reader_accepts(db, OLD_PASSWORD) is True

    asyncio.run(run())
    assert calls == []


def test_unparseable_user_record_is_logged_without_its_contents(caplog):
    import asyncio
    import logging

    secret, hashed = "Stored-Plain-1", get_password_hash("Stored-Plain-1")

    async def run():
        db = _FakeDb()
        # Model-invalid record (no first/last name) still carrying credentials.
        db.users.docs.append(
            {"_id": ObjectId(), "email": EMAIL, "username": "x", "roles": [],
             "password": secret, "hashed_password": hashed}
        )
        service = UserService(db)
        db.users.find = lambda *a, **k: _Cursor(db.users.docs)
        await service.get_users(0, 10)
        with pytest.raises(UserServiceError) as exc:
            await service.get_user(str(db.users.docs[0]["_id"]))
        assert secret not in str(exc.value) and hashed[-20:] not in str(exc.value)

    with caplog.at_level(logging.DEBUG):
        asyncio.run(run())
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "validation error" in text  # control: the failure is still reported
    assert secret not in text
    assert hashed not in text
    assert hashed[:20] not in text and hashed[-20:] not in text  # no truncated fragment


class _Cursor:
    def __init__(self, docs):
        self._docs = [deepcopy(d) for d in docs]

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration
