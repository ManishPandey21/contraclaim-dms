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
from rbac_backend.services.authentication_service import (
    AuthenticationService,
    SessionRevocationUnavailableError,
)
from rbac_backend.services.user_service import UserService, UserServiceError
from rbac_backend.utils.error_handler import UserError
import rbac_backend.services.authentication_service as authentication_service
import rbac_backend.services.permission_service as permission_service
import rbac_backend.services.runtime_state as runtime_state
import rbac_backend.utils.audit_logger as audit_logger
import jwt as _jwt
from rbac_backend.core.config import settings as _settings
from rbac_backend.core.security import create_access_token, principal_from_access_token
from rbac_backend.services import runtime_state as _runtime_state
from rbac_backend.services.credential_fields import CREDENTIAL_VERSION_CLAIM

# Disposable test-only values; none is or was a real credential. Kept low-entropy
# so the repository secret scan does not mistake them for keys.
OLD_PASSWORD = "Old-Passw0rd-A"
NEW_PASSWORD = "New-Passw0rd-B"
LEGACY_PASSWORD = "Legacy-Pass-11"
WRONG_PASSWORD_C = "Wrong-Pass-11"
WRONG_PASSWORD = "Wrong-Pass-22"
THIRD_PASSWORD = "Third-Pass-33"
FRESH_PASSWORD = "Fresh-Pass-44"
BODY_PASSWORD = "Body-Pass-55"
PLAIN_PASSWORD = "Plain-Pass-66"
PROBE_PASSWORD = "Probe-Pass-77"
WEAK_PROBE_PASSWORD = "probe-pass-weak"
STORED_PASSWORD = "Stored-Pass-88"
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
            elif isinstance(value, dict) and "$in" in value:
                if doc.get(key) not in value["$in"]:  # None matches a missing field, as in Mongo
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

    async def log_user_updated(self, actor_id, target_id, changed_fields, **details):
        self.updates.append((actor_id, target_id, list(changed_fields), details))


class _Sessions:
    """Session-store double. ``fail_on`` holds the 1-based revoke calls that fail."""

    def __init__(self, fail_on=()):
        self.invalidated = []
        self.revoked = []
        self.fail_on = set(fail_on)
        self._calls = 0

    async def invalidate_user_sessions(self, user_id):
        self.invalidated.append(str(user_id))

    async def revoke_all_sessions(self, user_id):
        self._calls += 1
        if self._calls in self.fail_on:
            raise SessionRevocationUnavailableError("simulated session store outage")
        self.revoked.append(str(user_id))


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


def _controller(db, sessions=None):
    audit, sessions = _Audit(), sessions or _Sessions()
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


async def _admin_reset(db, user_id, password=NEW_PASSWORD, sessions=None):
    controller, audit, sessions = _controller(db, sessions)
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
        # One session-store cleanup after the write.
        assert sessions.revoked == [user_id]
        # Audit names the credential change without carrying any secret value.
        (_, target, fields, details), = audit.updates
        assert target == user_id
        assert "hashed_password" in fields
        assert details == {"session_cleanup": "complete"}
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
        bad = _login(client, WRONG_PASSWORD_C)
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
                passwordHash=get_password_hash(LEGACY_PASSWORD),
            )
        )
        assert await _login_reader_accepts(db, OLD_PASSWORD) is True
        # A conflicting legacy copy must not become a second accepted password.
        assert await _login_reader_accepts(db, LEGACY_PASSWORD) is False

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
        ("missing field, whole body is the input", {"password": BODY_PASSWORD, "email": None}),
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


def test_create_with_a_weak_password_is_refused_without_echoing_it():
    """UserCreatePayload has no policy validator; the controller refuses with 400."""
    import asyncio

    from rbac_backend.routers.users import UserCreatePayload

    async def run():
        db = _FakeDb()
        controller, _, _ = _controller(db)
        payload = UserCreatePayload(**{**_NEW_USER, "password": WEAK_PROBE_PASSWORD})
        actor = SimpleNamespace(id=str(ObjectId()), roles=["superadmin"])
        with pytest.raises(UserError) as exc:
            await controller.create_user(payload, actor)
        assert exc.value.http_status == 400
        assert WEAK_PROBE_PASSWORD not in str(exc.value)
        assert db.users.docs == []

    asyncio.run(run())


def test_create_validation_failure_message_never_echoes_the_password():
    import asyncio

    from pydantic import ValidationError

    secret = PLAIN_PASSWORD

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

    secret = FRESH_PASSWORD

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

    third, wrong = THIRD_PASSWORD, WRONG_PASSWORD

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
         {**_NEW_USER, "password": {"nested": PROBE_PASSWORD}}, 422),
        ("login, whole body is the input", "post", "/api/login", {"password": PROBE_PASSWORD}, 422),
        ("admin reset, wrong type", "put", "/api/users/6ad3d0000000000000000001",
         {"password": {"nested": PROBE_PASSWORD}}, 422),
        ("self-service change, whole body is the input", "post",
         "/api/profiles/change-password", {"new_password": PROBE_PASSWORD}, 422),
    ],
)
def test_password_bearing_routes_never_echo_the_password(label, method, url, body, expected):
    from rbac_backend.main import app as production_app

    secret = body["password"] if isinstance(body.get("password"), str) else PROBE_PASSWORD

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


def _record_session_invalidation(monkeypatch, fail_on=()):
    calls, attempts = [], []

    async def record(self, user_id):
        attempts.append(str(user_id))
        if len(attempts) in set(fail_on):
            raise SessionRevocationUnavailableError("simulated session store outage")
        calls.append(str(user_id))

    monkeypatch.setattr(AuthenticationService, "revoke_all_sessions", record)
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
                passwordHash=get_password_hash(LEGACY_PASSWORD),
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
    assert calls == [user_id]  # one session-store cleanup after the write


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
                ChangePassword(current_password=WRONG_PASSWORD, new_password=NEW_PASSWORD),
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

    secret, hashed = STORED_PASSWORD, get_password_hash(STORED_PASSWORD)

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


# --- G: credential generation (credential_version / cred_v) --------------------
#
# A credential change is ONE atomic Mongo write: new hash, no legacy/plaintext
# copy, credential_version + 1. Every token names the generation it was minted
# under (cred_v); principal_from_access_token rejects a mismatch before, and
# independently of, Redis. Session-store cleanup afterwards is defence in depth.



def _token(email, user_id, cred_v="absent", session_id=None):
    data = {"sub": email, "user_id": str(user_id), "roles": []}
    if cred_v != "absent":
        data[CREDENTIAL_VERSION_CLAIM] = cred_v
    if session_id:
        data["session_id"] = session_id
    return create_access_token(data=data)


def _no_runtime_redis(monkeypatch):
    """Request-time checks with no runtime Redis configured (credential check only)."""
    monkeypatch.setattr(
        _runtime_state.RuntimeStateService, "redis_url", property(lambda self: None)
    )

    async def none(self):
        return None

    monkeypatch.setattr(_runtime_state.RuntimeStateService, "get_redis", none)


def _principal(db, token):
    return asyncio_run(principal_from_access_token(token, db))


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)


def _legacy_user(db, **fields):
    uid = ObjectId()
    db.users.docs.append(
        _doc(_id=uid, roles=[], hashed_password=get_password_hash(OLD_PASSWORD), **fields)
    )
    return uid


# 1, 3, 4 -------------------------------------------------------------------


def test_legacy_token_without_claim_is_accepted_while_the_user_is_generation_0(monkeypatch):
    _no_runtime_redis(monkeypatch)
    db = _FakeDb()
    uid = _legacy_user(db)  # predates the field: credential_version absent
    assert _principal(db, _token(EMAIL, uid)) is not None


def test_old_and_missing_claim_tokens_are_rejected_once_the_generation_moves(monkeypatch):
    _no_runtime_redis(monkeypatch)
    db = _FakeDb()
    uid = _legacy_user(db)
    legacy = _token(EMAIL, uid)  # no cred_v -> generation 0
    explicit_zero = _token(EMAIL, uid, cred_v=0)
    asyncio_run(_admin_reset(db, str(uid)))
    assert db.users.docs[0]["credential_version"] == 1  # 2: 0 -> 1
    assert _principal(db, legacy) is None  # 4: missing claim, user > 0
    assert _principal(db, explicit_zero) is None  # 3: old generation
    assert _principal(db, _token(EMAIL, uid, cred_v=1)) is not None  # positive control


@pytest.mark.parametrize("claim", ["1", 1.0, True, None, [1]])
def test_malformed_claim_is_rejected(monkeypatch, claim):
    _no_runtime_redis(monkeypatch)
    db = _FakeDb()
    uid = _legacy_user(db, credential_version=1)
    assert _principal(db, _token(EMAIL, uid, cred_v=claim)) is None


def test_malformed_stored_generation_fails_closed(monkeypatch):
    _no_runtime_redis(monkeypatch)
    db = _FakeDb()
    uid = _legacy_user(db, credential_version="1")
    assert _principal(db, _token(EMAIL, uid, cred_v=1)) is None


def test_new_users_start_at_generation_1():
    import asyncio

    async def run():
        db = _FakeDb()
        user_id = await _created_user(db)
        return (await db.users.find_one({"_id": ObjectId(user_id)}))["credential_version"]

    assert asyncio.run(run()) == 1


def test_no_request_model_can_set_the_generation():
    from rbac_backend.models.user import UserCreate, UserUpdate
    from rbac_backend.routers.users import UserCreatePayload
    from rbac_backend.schemas.profile import ChangePassword, ProfileUpdate

    for model in (UserCreate, UserUpdate, UserCreatePayload, ProfileUpdate, ChangePassword):
        assert "credential_version" not in model.model_fields, model.__name__


def test_generation_is_not_serialised_with_the_principal(monkeypatch):
    _no_runtime_redis(monkeypatch)
    db = _FakeDb()
    uid = _legacy_user(db, credential_version=3)
    principal = _principal(db, _token(EMAIL, uid, cred_v=3))
    assert principal.credential_version == 3
    assert "credential_version" not in principal.model_dump()
    assert "credential_version" not in principal.model_dump_json()


def test_a_dict_update_cannot_move_the_generation():
    import asyncio

    async def run():
        db = _FakeDb()
        user_id = await _created_user(db)
        await UserService(db).update_user(user_id, {"first_name": "X", "credential_version": 99})
        return (await db.users.find_one({"_id": ObjectId(user_id)}))["credential_version"]

    assert asyncio.run(run()) == 1


# 5: real login after reset carries the new generation ------------------------


def test_http_login_after_reset_mints_the_new_generation(monkeypatch):
    import asyncio

    db = _FakeDb()
    user_id = asyncio.run(_created_user(db))  # generation 1
    asyncio.run(_admin_reset(db, user_id))  # generation 2
    with _login_client(monkeypatch, db) as client:
        response = _login(client, NEW_PASSWORD)
    assert response.status_code == 200
    claims = _jwt.decode(
        response.json()["access_token"], _settings.SECRET_KEY, algorithms=[_settings.ALGORITHM]
    )
    assert claims[CREDENTIAL_VERSION_CLAIM] == 2


# 2, 11: the write is atomic ----------------------------------------------------


def test_reset_advances_the_generation_in_the_same_write_as_the_hash():
    import asyncio

    async def run():
        db = _FakeDb()
        user_id = await _created_user(db)
        writes = []
        real = db.users.update_one

        async def spy(query, update, *a, **k):
            writes.append(update)
            return await real(query, update, *a, **k)

        db.users.update_one = spy
        await _admin_reset(db, user_id)
        (write,) = [w for w in writes if "$inc" in w or PASSWORD_HASH_KEY in (w.get("$set") or {})]
        assert PASSWORD_HASH_KEY in write["$set"]
        assert write["$inc"] == {"credential_version": 1}
        assert set(write["$unset"]) == {"passwordHash", "password"}

    PASSWORD_HASH_KEY = "hashed_password"
    asyncio.run(run())


def test_failed_credential_write_changes_neither_hash_nor_generation():
    import asyncio

    async def run():
        db = _FakeDb()
        user_id = await _created_user(db)
        before = deepcopy(await db.users.find_one({"_id": ObjectId(user_id)}))
        db.users.fail_updates = True
        sessions = _Sessions()
        with pytest.raises(HTTPException):
            await _admin_reset(db, user_id, sessions=sessions)
        after = await db.users.find_one({"_id": ObjectId(user_id)})
        assert after["credential_version"] == before["credential_version"]
        assert after["hashed_password"] == before["hashed_password"]
        assert sessions.revoked == []  # nothing changed, so nothing was cleaned up

    asyncio.run(run())


# 10: concurrent changes ----------------------------------------------------------


def test_concurrent_resets_advance_monotonically_and_last_commit_wins():
    import asyncio

    async def run():
        db = _FakeDb()
        user_id = await _created_user(db)  # generation 1
        await asyncio.gather(
            _admin_reset(db, user_id, password=NEW_PASSWORD),
            _admin_reset(db, user_id, password=THIRD_PASSWORD),
        )
        doc = await db.users.find_one({"_id": ObjectId(user_id)})
        assert doc["credential_version"] == 3  # each committed change counted once
        accepts_new = verify_password(NEW_PASSWORD, doc["hashed_password"])
        accepts_third = verify_password(THIRD_PASSWORD, doc["hashed_password"])
        assert accepts_new != accepts_third  # exactly one committed change is live
        assert verify_password(OLD_PASSWORD, doc["hashed_password"]) is False

    asyncio.run(run())


# 7, 9: post-write session cleanup failure ------------------------------------


def test_admin_reset_succeeds_and_records_a_failed_session_cleanup(caplog):
    import asyncio
    import logging

    async def run():
        db = _FakeDb()
        user_id = await _created_user(db)
        _, audit, sessions = await _admin_reset(db, user_id, sessions=_Sessions(fail_on={1}))
        state = await _state(db, user_id)
        return audit, state

    with caplog.at_level(logging.ERROR):
        audit, state = asyncio.run(run())
    assert state == {"old": False, "new": True, "fields": ["hashed_password"]}
    (_, _, fields, details), = audit.updates
    assert "hashed_password" in fields
    assert details == {"session_cleanup": "failed"}
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "cleanup failed" in text
    assert NEW_PASSWORD not in text and OLD_PASSWORD not in text


def test_admin_reset_records_a_complete_session_cleanup():
    import asyncio

    async def run():
        db = _FakeDb()
        user_id = await _created_user(db)
        _, audit, sessions = await _admin_reset(db, user_id)
        (_, _, _, details), = audit.updates
        return user_id, sessions, details

    user_id, sessions, details = asyncio.run(run())
    assert sessions.revoked == [user_id]
    assert details == {"session_cleanup": "complete"}


def test_self_service_change_succeeds_when_session_cleanup_fails(monkeypatch, caplog):
    import asyncio
    import logging

    calls = _record_session_invalidation(monkeypatch, fail_on={1})

    async def run():
        db = _FakeDb()
        user_id = await _created_user(db)
        result = await _change(db, user_id, OLD_PASSWORD, NEW_PASSWORD)
        assert result == {"message": "Password changed successfully"}
        doc = await db.users.find_one({"_id": ObjectId(user_id)})
        assert doc["credential_version"] == 2
        return await _state(db, user_id)

    with caplog.at_level(logging.ERROR):
        state = asyncio.run(run())
    assert state == {"old": False, "new": True, "fields": ["hashed_password"]}
    assert calls == []  # the one cleanup attempt failed
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "cleanup failed" in text
    assert NEW_PASSWORD not in text and OLD_PASSWORD not in text


def test_self_service_wrong_current_password_changes_nothing(monkeypatch):
    import asyncio

    calls = _record_session_invalidation(monkeypatch)

    async def run():
        db = _FakeDb()
        user_id = await _created_user(db)
        with pytest.raises(HTTPException) as exc:
            await _change(db, user_id, WRONG_PASSWORD, NEW_PASSWORD)
        assert exc.value.status_code == 400
        doc = await db.users.find_one({"_id": ObjectId(user_id)})
        assert doc["credential_version"] == 1
        return await _state(db, user_id)

    assert asyncio.run(run())["old"] is True
    assert calls == []


def test_self_service_change_makes_this_sessions_token_stale(monkeypatch):
    """9: the self-service path advances the generation like the admin reset."""
    import asyncio

    _record_session_invalidation(monkeypatch)
    _no_runtime_redis(monkeypatch)
    db = _FakeDb()
    uid = _legacy_user(db)  # generation 0 (absent)
    token = _token(EMAIL, uid)
    assert _principal(db, token) is not None
    asyncio.run(_change(db, str(uid), OLD_PASSWORD, NEW_PASSWORD))
    assert db.users.docs[0]["credential_version"] == 1
    assert _principal(db, token) is None
    assert _principal(db, _token(EMAIL, uid, cred_v=1)) is not None


# 8: the credential check does not weaken the Redis fail-closed policy ----------


def test_request_auth_still_fails_closed_when_the_configured_redis_is_down(monkeypatch):
    monkeypatch.setattr(_settings, "AUTH_SESSION_FAIL_CLOSED", True)
    monkeypatch.setattr(
        _runtime_state.RuntimeStateService, "redis_url", property(lambda self: "redis://configured")
    )

    async def unreachable(self):
        return None

    monkeypatch.setattr(_runtime_state.RuntimeStateService, "get_redis", unreachable)
    db = _FakeDb()
    uid = _legacy_user(db, credential_version=2)
    with pytest.raises(HTTPException) as exc:  # current token: Redis policy applies (503)
        _principal(db, _token(EMAIL, uid, cred_v=2, session_id="s-1"))
    assert exc.value.status_code == 503
    # A stale token is refused before Redis is consulted: no outage can revive it.
    assert _principal(db, _token(EMAIL, uid, cred_v=1, session_id="s-1")) is None


# 12, 13: step-up tokens ------------------------------------------------------------


def test_step_up_token_from_before_a_password_change_is_rejected(monkeypatch):
    import asyncio

    from rbac_backend.services.step_up_service import StepUpService

    class _NoAudit:
        async def emit(self, **kwargs):
            return None

    _no_runtime_redis(monkeypatch)

    async def run():
        db = _FakeDb()
        user_id = await _created_user(db)  # generation 1
        principal = SimpleNamespace(id=user_id, email=EMAIL)
        service = StepUpService(db)
        service.audit_service = _NoAudit()
        before = await service.issue_after_password(
            current_user=principal, password=OLD_PASSWORD, action="users.delete"
        )
        await _admin_reset(db, user_id)  # generation 2
        after = await service.issue_after_password(
            current_user=principal, password=NEW_PASSWORD, action="users.delete"
        )
        return service, user_id, before, after

    service, user_id, before, after = asyncio.run(run())
    with pytest.raises(HTTPException) as exc:
        service.verify_token(token=before, user_id=user_id, action="users.delete", credential_version=2)
    assert exc.value.status_code == 403
    service.verify_token(token=after, user_id=user_id, action="users.delete", credential_version=2)


def test_require_step_up_compares_against_the_principals_generation():
    import asyncio

    from rbac_backend.services.step_up_service import StepUpService, require_step_up

    token = StepUpService().create_token(user_id="u-1", action="*", credential_version=1)
    request = SimpleNamespace(headers={"x-step-up-token": token})
    asyncio.run(require_step_up(request, SimpleNamespace(id="u-1", credential_version=1), action="x"))
    with pytest.raises(HTTPException):
        asyncio.run(require_step_up(request, SimpleNamespace(id="u-1", credential_version=2), action="x"))


def test_legacy_step_up_token_only_valid_at_generation_0():
    from rbac_backend.services.step_up_service import StepUpService

    service = StepUpService()
    legacy = service.create_token(user_id="u-1", action="*")  # minted as generation 0
    service.verify_token(token=legacy, user_id="u-1", action="a", credential_version=0)
    with pytest.raises(HTTPException):
        service.verify_token(token=legacy, user_id="u-1", action="a", credential_version=1)


# 14: refresh cannot mint from a stale principal --------------------------------


def test_refresh_refuses_a_principal_whose_generation_has_moved():
    import asyncio

    from rbac_backend.routers.auth import AuthController
    from rbac_backend.utils.error_handler import AuthenticationError

    class _RefreshAudit:
        def __init__(self):
            self.failures = []

        async def log_token_refresh_failed(self, user_id, reason):
            self.failures.append(reason)

    class _Alive:
        async def is_session_active(self, session_id):
            return True

        async def extend_session(self, *a, **k):
            return None

    async def run():
        db = _FakeDb()
        user_id = await _created_user(db)  # generation 1
        audit = _RefreshAudit()
        controller = AuthController(_Alive(), UserService(db), None, _NoLimit(), audit)
        principal = SimpleNamespace(id=user_id, credential_version=1)
        token = _token(EMAIL, user_id, cred_v=1, session_id="s-1")
        fresh = await controller.refresh_token(principal, token)  # positive control
        claims = _jwt.decode(fresh.access_token, _settings.SECRET_KEY, algorithms=[_settings.ALGORITHM])
        assert claims[CREDENTIAL_VERSION_CLAIM] == 1
        await _admin_reset(db, user_id)  # generation 2 while the principal is in flight
        with pytest.raises(AuthenticationError):
            await controller.refresh_token(principal, token)
        return audit.failures

    assert asyncio.run(run()) == ["credential_changed"]


async def _state(db, user_id):
    doc = await db.users.find_one({"_id": ObjectId(user_id)})
    return {
        "old": await _login_reader_accepts(db, OLD_PASSWORD),
        "new": await _login_reader_accepts(db, NEW_PASSWORD),
        "fields": sorted(k for k in doc if "password" in k.lower()),
    }


def _change(db, user_id, current, new):
    from rbac_backend.routers.profiles import change_my_password
    from rbac_backend.schemas.profile import ChangePassword

    return change_my_password(
        ChangePassword(current_password=current, new_password=new),
        current_user=SimpleNamespace(id=user_id, roles=["user"]),
        db=db,
    )


# --- the real fence against the real session store contract -----------------


class _FakeRedis:
    def __init__(self, fail=False):
        self.store, self.sets, self.fail = {}, {}, fail

    async def set(self, key, value, *args, **kwargs):
        if self.fail:
            raise ConnectionError("simulated")
        self.store[key] = value

    async def smembers(self, key):
        return set(self.sets.get(key, set()))

    async def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)
            self.sets.pop(key, None)


def _service_with(monkeypatch, redis, redis_url="redis://configured"):
    """A service with its own runtime-state double; the process-global one is untouched."""
    service = AuthenticationService()

    async def get_redis():
        return redis

    service._runtime_state = SimpleNamespace(get_redis=get_redis, redis_url=redis_url)
    return service


def test_fence_sets_the_existing_revocation_floor_and_deletes_sessions(monkeypatch):
    import asyncio
    import time

    redis = _FakeRedis()
    uid = str(ObjectId())
    redis.store["auth:session:tok-1"] = "{}"
    redis.sets[f"auth:user_sessions:{uid}"] = {b"tok-1"}
    service = _service_with(monkeypatch, redis)
    before = int(time.time())
    asyncio.run(service.revoke_all_sessions(uid))
    floor = int(redis.store[f"user_jwt_min_iat:{uid}"])
    # Same key and unit core.security.principal_from_access_token compares iat to.
    assert before <= floor <= int(time.time())
    assert "auth:session:tok-1" not in redis.store
    assert f"auth:user_sessions:{uid}" not in redis.sets


@pytest.mark.parametrize("case", ["configured_but_unreachable", "store_write_fails"])
def test_fence_never_reports_success_it_cannot_prove(monkeypatch, case):
    import asyncio

    redis = None if case == "configured_but_unreachable" else _FakeRedis(fail=True)
    service = _service_with(monkeypatch, redis)
    with pytest.raises(SessionRevocationUnavailableError):
        asyncio.run(service.revoke_all_sessions(str(ObjectId())))


def test_legacy_invalidate_user_sessions_is_silent_when_redis_is_unreachable(monkeypatch):
    """Why the fence exists: the old call reported success while ending nothing."""
    import asyncio

    service = _service_with(monkeypatch, None)
    asyncio.run(service.invalidate_user_sessions(str(ObjectId())))  # no error, nothing ended


# --- H: final gates (2026-10-19) ----------------------------------------------


@pytest.mark.parametrize("claim", [-1, -5])
def test_negative_claim_is_rejected(monkeypatch, claim):
    _no_runtime_redis(monkeypatch)
    db = _FakeDb()
    uid = _legacy_user(db)  # generation 0
    assert _principal(db, _token(EMAIL, uid, cred_v=claim)) is None


def test_negative_stored_generation_fails_closed(monkeypatch):
    _no_runtime_redis(monkeypatch)
    db = _FakeDb()
    uid = _legacy_user(db, credential_version=-1)
    assert _principal(db, _token(EMAIL, uid, cred_v=-1)) is None
    assert _principal(db, _token(EMAIL, uid)) is None


# Self-service compare-and-set ------------------------------------------------------


def test_self_service_change_never_overwrites_a_concurrent_change(monkeypatch):
    """A and B both verify generation N; B commits N+1; A's write must conflict."""
    import asyncio

    _record_session_invalidation(monkeypatch)

    async def run():
        db = _FakeDb()
        user_id = await _created_user(db)  # generation 1, OLD_PASSWORD
        real_update = db.users.update_one
        fired = {"b": False}

        async def interleave(query, update, *a, **k):
            # A has read and verified generation 1. Before A writes, B (which
            # verified the same generation) commits its own change.
            if not fired["b"] and "$inc" in update:
                fired["b"] = True
                await _change(db, user_id, OLD_PASSWORD, THIRD_PASSWORD)
            return await real_update(query, update, *a, **k)

        db.users.update_one = interleave
        with pytest.raises(HTTPException) as exc:
            await _change(db, user_id, OLD_PASSWORD, NEW_PASSWORD)  # A
        db.users.update_one = real_update
        assert exc.value.status_code == 409
        assert NEW_PASSWORD not in str(exc.value.detail)
        doc = await db.users.find_one({"_id": ObjectId(user_id)})
        assert doc["credential_version"] == 2  # only B's change committed
        assert verify_password(THIRD_PASSWORD, doc["hashed_password"])  # B's survives
        assert not verify_password(NEW_PASSWORD, doc["hashed_password"])  # A did not overwrite

    asyncio.run(run())


def test_self_service_compare_and_set_guards_pre_field_users(monkeypatch):
    """Generation 0 is the absent field; the guard must still match it."""
    import asyncio

    _record_session_invalidation(monkeypatch)

    async def run():
        db = _FakeDb()
        uid = _legacy_user(db)  # no credential_version field
        await _change(db, str(uid), OLD_PASSWORD, NEW_PASSWORD)
        doc = await db.users.find_one({"_id": uid})
        assert doc["credential_version"] == 1
        assert verify_password(NEW_PASSWORD, doc["hashed_password"])

    asyncio.run(run())


# Real HTTP 422 through an isolated app (no global main.app state) --------------


def _isolated_app():
    """The three password-bearing routers, wired exactly as main.py wires them."""
    from rbac_backend.core.errors import (
        register_domain_error_handler,
        register_request_validation_redaction,
    )
    from rbac_backend.routers import auth as auth_router
    from rbac_backend.routers import profiles as profiles_router
    from rbac_backend.routers import users as users_router

    app = FastAPI()
    register_domain_error_handler(app)
    register_request_validation_redaction(app)
    app.include_router(auth_router.router, prefix="/api", tags=["auth"])
    app.include_router(users_router.router, prefix="/api", tags=["users"])
    app.include_router(profiles_router.router, prefix="/api", tags=["profiles"])

    async def fake_user():
        return SimpleNamespace(
            id=str(ObjectId()), roles=["superadmin"], organization_id=None, projects=[],
            email="a@b.co", permissions=["*"], disabled=False, credential_version=1,
        )

    app.dependency_overrides[security.get_current_user] = fake_user
    return app


@pytest.mark.parametrize(
    "label, method, url, body, secret",
    [
        ("create user", "post", "/api/users",
         {**_NEW_USER, "password": {"nested": PROBE_PASSWORD}}, PROBE_PASSWORD),
        ("create user, whole body", "post", "/api/users",
         {"password": PROBE_PASSWORD, "email": "a@b.co"}, PROBE_PASSWORD),
        ("login", "post", "/api/login", {"password": PROBE_PASSWORD}, PROBE_PASSWORD),
        ("login, wrong type", "post", "/api/login",
         {"email": "a@b.co", "password": {"x": PROBE_PASSWORD}}, PROBE_PASSWORD),
        ("admin reset", "put", "/api/users/6ad3d0000000000000000001",
         {"password": {"nested": PROBE_PASSWORD}}, PROBE_PASSWORD),
        ("self-service change", "post", "/api/profiles/change-password",
         {"new_password": PROBE_PASSWORD}, PROBE_PASSWORD),
        ("self-service change, wrong type", "post", "/api/profiles/change-password",
         {"current_password": PROBE_PASSWORD, "new_password": [PROBE_PASSWORD]}, PROBE_PASSWORD),
    ],
)
def test_isolated_http_422_never_carries_the_submitted_values(label, method, url, body, secret):
    import json

    with TestClient(_isolated_app(), base_url="https://web.contraclaim.com") as client:
        response = getattr(client, method)(url, json=body)
    assert response.status_code == 422, (label, response.status_code)
    text = response.text
    assert secret not in text, label
    for fragment in (secret[:6], secret[-6:], secret[3:9]):  # no credential fragment either
        assert fragment not in text, (label, fragment)
    assert json.dumps(body) not in text  # never the raw body
    detail = response.json()["detail"]
    assert isinstance(detail, list) and detail  # FastAPI's default shape, with content
    for error in detail:
        assert "input" not in error, label
        assert set(error) <= {"type", "loc", "msg", "ctx", "url"}, (label, set(error))
        assert secret not in json.dumps(error.get("ctx", {}))


def test_the_production_app_registers_the_same_redaction():
    from fastapi.exceptions import RequestValidationError

    from rbac_backend.core.errors import redacted_request_validation_handler
    from rbac_backend.main import app as production_app

    assert production_app.exception_handlers.get(RequestValidationError) is (
        redacted_request_validation_handler
    )


# Post-write Redis failure: every pre-change credential is still dead --------------


def test_redis_cleanup_failure_after_the_write_leaves_no_old_credential_alive(monkeypatch):
    import asyncio

    from rbac_backend.services.step_up_service import StepUpService

    class _NoAudit:
        async def emit(self, **kwargs):
            return None

    _no_runtime_redis(monkeypatch)  # request-time checks: the credential generation alone

    async def run():
        db = _FakeDb()
        uid = _legacy_user(db, credential_version=4)
        user_id = str(uid)
        old_access = _token(EMAIL, uid, cred_v=4)
        step_up = StepUpService(db)
        step_up.audit_service = _NoAudit()
        principal = SimpleNamespace(id=user_id, email=EMAIL)
        old_step_up = await step_up.issue_after_password(
            current_user=principal, password=OLD_PASSWORD, action="users.delete"
        )
        # A login that verified the old password, finishing after the change
        # (the real login flow is driven in test_password_change_session_race).
        gap_access = _token(EMAIL, uid, cred_v=4, session_id="gap")
        # The change commits; the session-store cleanup after it fails.
        await _admin_reset(db, user_id, sessions=_Sessions(fail_on={1}))
        assert (await db.users.find_one({"_id": uid}))["credential_version"] == 5
        return db, uid, user_id, old_access, gap_access, step_up, old_step_up

    db, uid, user_id, old_access, gap_access, step_up, old_step_up = asyncio.run(run())
    assert _principal(db, old_access) is None
    assert _principal(db, gap_access) is None
    with pytest.raises(HTTPException):
        step_up.verify_token(
            token=old_step_up, user_id=user_id, action="users.delete", credential_version=5
        )
    fresh = _principal(db, _token(EMAIL, uid, cred_v=5))
    assert fresh is not None and fresh.credential_version == 5
