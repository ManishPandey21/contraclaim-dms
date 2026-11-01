"""A login that races a password change must not outlive it.

The race: login verifies the OLD hash, the password change commits, then the
login finishes and mints its token. Nothing in Redis can catch that token if the
session-store cleanup after the write fails. The credential generation does: the
token names the generation its password belonged to (read from the same
document the password was verified against), and the change advanced it in the
same atomic write as the hash. Drives the real reset, the real login session
creation and the real per-request check (core.security.principal_from_access_token)
against an in-memory Redis whose writes fail after the change.
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace

from bson import ObjectId

from rbac_backend.core.security import create_access_token, get_password_hash, principal_from_access_token
from rbac_backend.services.credential_fields import CREDENTIAL_VERSION_CLAIM
from rbac_backend.models.user import UserUpdate
from rbac_backend.routers.users import UserController
from rbac_backend.services import runtime_state
from rbac_backend.services.authentication_service import AuthenticationService
from rbac_backend.services.user_service import UserService

OLD_PASSWORD = "Old-Passw0rd-A"
NEW_PASSWORD = "New-Passw0rd-B"
EMAIL = "race-target@example.com"


class _Redis:
    def __init__(self):
        self.kv, self.sets, self.fail_writes = {}, {}, False

    def _guard(self):
        if self.fail_writes:
            raise ConnectionError("simulated outage")

    async def set(self, key, value, *a, **k):
        self._guard()
        self.kv[key] = str(value)

    async def setex(self, key, ttl, value):
        self._guard()
        self.kv[key] = value

    async def get(self, key):
        return self.kv.get(key)

    async def exists(self, key):
        return int(key in self.kv)

    async def sadd(self, key, member):
        self._guard()
        self.sets.setdefault(key, set()).add(member)

    async def expire(self, *a, **k):
        return True

    async def smembers(self, key):
        return set(self.sets.get(key, set()))

    async def delete(self, *keys):
        self._guard()
        for key in keys:
            self.kv.pop(key, None)
            self.sets.pop(key, None)


class _Users:
    def __init__(self, docs):
        self.docs = docs

    async def find_one(self, query, *a, **k):
        for doc in self.docs:
            if all(doc.get(key) == value for key, value in query.items()):
                return deepcopy(doc)
        return None

    async def update_one(self, query, update, *a, **k):
        for doc in self.docs:
            if all(doc.get(key) == value for key, value in query.items()):
                doc.update(update.get("$set") or {})
                for key in update.get("$unset") or {}:
                    doc.pop(key, None)
                for key, step in (update.get("$inc") or {}).items():
                    doc[key] = doc.get(key, 0) + step
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)


class _Allow:
    async def require_permission(self, *a, **k):
        return None

    async def check_user_access(self, *a, **k):
        return None

    async def check_user_limit(self, *a, **k):
        return None

    async def log_user_updated(self, *a, **k):
        return None


async def _finish_login(auth: AuthenticationService, user) -> str:
    """What AuthController.login_user does after the password check succeeded."""
    session_id = await auth.create_user_session(user.id, "203.0.113.9", "test", timedelta(minutes=60))
    return create_access_token(
        data={
            "sub": EMAIL, "user_id": user.id, "roles": [], "session_id": session_id,
            CREDENTIAL_VERSION_CLAIM: user.credential_version,
        },
        expires_delta=timedelta(minutes=60),
    )


def test_a_login_during_the_change_does_not_outlive_it(monkeypatch):
    redis = _Redis()

    async def get_redis(self):
        return redis

    monkeypatch.setattr(runtime_state.RuntimeStateService, "get_redis", get_redis)
    monkeypatch.setattr(
        runtime_state.RuntimeStateService, "redis_url", property(lambda self: "redis://configured")
    )

    uid = ObjectId()
    db = SimpleNamespace(
        users=_Users([{
            "_id": uid, "email": EMAIL, "username": "race-target", "first_name": "R",
            "last_name": "T", "roles": [], "projects": [], "disabled": False,
            "hashed_password": get_password_hash(OLD_PASSWORD),
        }]),
        roles=None,
    )
    auth = AuthenticationService()
    auth._db = db  # the login reader (authenticate_user_secure) reads this database
    service = UserService(db)
    controller = UserController(service, _Allow(), auth, _Allow(), _Allow(), None)

    async def response(user, **kwargs):
        return {"id": user.id}

    controller._build_user_response = response

    async def run():
        before_change = await _finish_login(auth, await auth.authenticate_user_secure(EMAIL, OLD_PASSWORD))
        real_write = service.update_user
        tokens = {}

        async def write_with_a_login_in_the_gap(user_id, update):
            # A login verifies the OLD password just before the change commits...
            racing_login = await auth.authenticate_user_secure(EMAIL, OLD_PASSWORD)
            assert racing_login is not None
            result = await real_write(user_id, update)
            # ...and finishes (session + token) after it. Then the session store
            # fails, so the post-write cleanup cannot help.
            tokens["gap"] = await _finish_login(auth, racing_login)
            redis.fail_writes = True
            return result

        service.update_user = write_with_a_login_in_the_gap
        await controller.update_user(
            str(uid), UserUpdate(password=NEW_PASSWORD), SimpleNamespace(id="actor", roles=["superadmin"])
        )
        redis.fail_writes = False
        pre = await principal_from_access_token(before_change, db)
        gap = await principal_from_access_token(tokens["gap"], db)
        fresh = await _finish_login(auth, await auth.authenticate_user_secure(EMAIL, NEW_PASSWORD))
        return pre, gap, await principal_from_access_token(fresh, db), db.users.docs[0]

    pre, gap, fresh, doc = asyncio.run(run())
    assert doc["credential_version"] == 1  # 0 (absent) -> 1, in the same write as the hash
    assert pre is None, "a session from before the change still authenticates"
    assert gap is None, "a login that raced the change still authenticates"
    assert fresh is not None and fresh.credential_version == 1  # positive control
