"""D4-B: an authorization change never leaves stale authority silently trusted.

`PermissionService.user_has_permission` serves grants from `user_perms:v3:{id}` for
up to an hour. Every authority-changing mutation - role delete, role permission
update, user role update - therefore has to make that entry untrustworthy. Before
R-A9D the invalidation ran AFTER the database write and swallowed every error:

* `RoleService._invalidate_role_caches` returned silently when Redis was
  unreachable, and logged-and-continued when the holder lookup or a Redis write
  failed;
* `UserService.update_user` did the same for a user's own role change.

So a transient failure left the revoked grant in the cache, still served, for up
to the TTL, while the API reported success.

The contract pinned here (fail closed):

* the authority change is announced BEFORE the write - a pending marker per
  affected user. If that cannot be done (store unreachable, holder lookup failed,
  marker write failed) the mutation is refused with nothing changed;
* while a user's change is pending, no cached grant is served and none is written;
* after the write the usual invalidation runs. If it fails, the marker stays and
  outlives any entry that could predate the change, so the only cost of the
  failure is cache bypass, never stale authority;
* a deployment with no runtime Redis has no permission cache and is unaffected.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Set

import pytest
from bson import ObjectId

from rbac_backend.services import permission_service as permission_module
from rbac_backend.services import runtime_state as runtime_state_module
from rbac_backend.services.permission_service import PermissionService
from rbac_backend.services.role_service import RoleService, RoleServiceError
from rbac_backend.services.user_service import UserService
from rbac_backend.utils.audit_logger import AuditLogger

ROLE_ID = ObjectId()
VIEWER_ROLE_ID = ObjectId()
USER_OID = ObjectId()
USER_ID = str(USER_OID)
SUPERADMIN = SimpleNamespace(id="sa", roles=["superadmin"], organization_id=None, projects=[])
REVOKED = "dms.document.delete"


# --------------------------------------------------------------------------- #
# In-memory database and Redis with failure injection
# --------------------------------------------------------------------------- #


def _values(value: Any) -> List[Any]:
    return value if isinstance(value, list) else [value]


def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for key, condition in (query or {}).items():
        if key == "$or":
            if not any(_matches(doc, branch) for branch in condition):
                return False
            continue
        value = doc.get(key)
        if isinstance(condition, dict):
            if "$in" in condition:
                wanted = {str(item) for item in condition["$in"]}
                if not any(str(item) in wanted for item in _values(value)):
                    return False
            if "$ne" in condition and value == condition["$ne"]:
                return False
        elif not any(str(item) == str(condition) for item in _values(value)):
            return False
    return True


class _Cursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs

    async def to_list(self, length=None):
        return [dict(doc) for doc in self._docs]


class _Collection:
    def __init__(self, docs: List[Dict[str, Any]]):
        self.docs = docs
        self.fail_find = False

    async def find_one(self, query, *_args, **_kwargs):
        for doc in self.docs:
            if _matches(doc, query):
                return dict(doc)
        return None

    def find(self, query=None, *_args, **_kwargs):
        if self.fail_find:
            raise RuntimeError("collection unreachable")
        return _Cursor([doc for doc in self.docs if _matches(doc, query or {})])

    async def update_one(self, query, update):
        for doc in self.docs:
            if _matches(doc, query):
                doc.update(update.get("$set", {}))
                for field, value in (update.get("$pull") or {}).items():
                    doc[field] = [item for item in doc.get(field, []) if item != value]
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)


class _Redis:
    """`fail` holds `op` or `op:key-prefix` rules; a matching call raises."""

    def __init__(self):
        self.store: Dict[str, Any] = {}
        self.fail: Set[str] = set()

    def _check(self, op: str, key: str) -> None:
        for rule in self.fail:
            name, _, prefix = rule.partition(":")
            if name == op and str(key).startswith(prefix):
                raise ConnectionError(f"redis {op} failed")

    async def get(self, key):
        self._check("get", key)
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self._check("set", key)
        self.store[key] = value

    async def delete(self, key):
        self._check("delete", key)
        self.store.pop(key, None)


def _role(role_id, permissions: List[str], name: str) -> Dict[str, Any]:
    return {
        "_id": role_id,
        "name": name,
        "scope": "organization",
        "organization_id": "org1",
        "permissions": list(permissions),
        "is_system": False,
        "is_active": True,
    }


class _Harness:
    def __init__(self, monkeypatch, *, configured: bool = True):
        self.redis: Optional[_Redis] = _Redis() if configured else None
        self.reachable = True
        self.db = SimpleNamespace(
            roles=_Collection(
                [
                    _role(ROLE_ID, [REVOKED, "dms.document.view"], "Site Deleter"),
                    _role(VIEWER_ROLE_ID, ["dms.document.view"], "Site Viewer"),
                ]
            ),
            users=_Collection(
                [{"_id": USER_OID, "email": "held@example.com", "roles": [str(ROLE_ID)], "organization_id": "org1"}]
            ),
            permissions=_Collection([{"_id": ObjectId(), "name": name, "is_active": True} for name in (REVOKED, "dms.document.view")]),
        )

        async def _get_db(_self):
            return self.db

        async def _get_redis():
            return self.redis if self.reachable else None

        async def _log_noop(*_args, **_kwargs):
            return None

        runtime = SimpleNamespace(redis_url="redis://runtime" if configured else None, get_redis=_get_redis)
        monkeypatch.setattr(RoleService, "_get_db", _get_db)
        monkeypatch.setattr(PermissionService, "_get_db", _get_db)
        monkeypatch.setattr(permission_module, "get_runtime_state", lambda: runtime)
        monkeypatch.setattr(runtime_state_module, "get_runtime_state", lambda: runtime)
        monkeypatch.setattr(AuditLogger, "log_permission_check", _log_noop)

    def has(self, permission: str = REVOKED) -> bool:
        return asyncio.run(PermissionService().user_has_permission(USER_ID, permission, log=False))

    @property
    def cache_key(self) -> str:
        return permission_module.permission_cache_key(USER_ID)

    def role(self) -> Dict[str, Any]:
        return next(doc for doc in self.db.roles.docs if doc["_id"] == ROLE_ID)

    def user_roles(self) -> List[str]:
        return list(self.db.users.docs[0]["roles"])


# Each mutation revokes REVOKED from the user, and reports whether it was applied.
def _delete_role(h: _Harness) -> None:
    asyncio.run(RoleService().delete_role(str(ROLE_ID), SUPERADMIN))


def _replace_role_permissions(h: _Harness) -> None:
    asyncio.run(RoleService().update_role_permissions(str(ROLE_ID), ["dms.document.view"], SUPERADMIN))


def _remove_permission_from_role(h: _Harness) -> None:
    asyncio.run(RoleService().remove_permission_from_role(str(ROLE_ID), REVOKED, SUPERADMIN))


def _change_user_roles(h: _Harness) -> None:
    asyncio.run(UserService(h.db).update_user(USER_ID, {"roles": [str(VIEWER_ROLE_ID)]}))


def _applied(h: _Harness, mutation) -> bool:
    if mutation is _delete_role:
        return h.role().get("is_active") is False
    if mutation is _change_user_roles:
        return h.user_roles() == [str(VIEWER_ROLE_ID)]
    return REVOKED not in h.role()["permissions"]


MUTATIONS = [_delete_role, _replace_role_permissions, _remove_permission_from_role, _change_user_roles]
ROLE_MUTATIONS = [_delete_role, _replace_role_permissions, _remove_permission_from_role]


def _attempt(h: _Harness, mutation) -> Optional[BaseException]:
    try:
        mutation(h)
    except Exception as exc:  # noqa: BLE001 - the refusal is what some cases assert
        return exc
    return None


def _assert_refused_as_outage(mutation, refusal: BaseException) -> None:
    """A refusal is a 503 at the route: the role router maps `RoleServiceError.status_code`,
    and the domain-error handler maps the user path's `http_status`. Never a wrapped 500."""
    if mutation is _change_user_roles:
        assert isinstance(refusal, permission_module.AuthorityChangeUnavailableError), repr(refusal)
        assert refusal.http_status == 503
    else:
        assert isinstance(refusal, RoleServiceError) and refusal.status_code == 503, repr(refusal)


def _cached_grant(h: _Harness) -> None:
    assert h.has(), "positive control: the role grants before the change"
    assert h.cache_key in h.redis.store, "precondition: the grant is now served from the cache"
    assert REVOKED in json.loads(h.redis.store[h.cache_key])["raw_permissions"]


# --------------------------------------------------------------------------- #
# The reproduction: invalidation fails after the write
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda m: m.__name__)
def test_a_failed_invalidation_after_the_write_never_serves_the_revoked_grant(monkeypatch, mutation) -> None:
    h = _Harness(monkeypatch)
    _cached_grant(h)
    # The session marker and the cache delete both fail: the old code swallowed
    # that and the hour-long entry kept answering.
    h.redis.fail = {"set:user_jwt_min_iat", "delete"}

    _attempt(h, mutation)

    assert _applied(h, mutation), "precondition: the change reached the database"
    h.redis.fail = set()
    assert not h.has(), f"{mutation.__name__}: revoked authority was served from a stale cache entry"


@pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda m: m.__name__)
def test_a_store_that_cannot_be_reached_refuses_the_change(monkeypatch, mutation) -> None:
    """Redis is configured but unreachable: nothing can be invalidated, so nothing is changed."""
    h = _Harness(monkeypatch)
    _cached_grant(h)
    h.reachable = False

    refusal = _attempt(h, mutation)

    assert refusal is not None, f"{mutation.__name__} reported success without being able to invalidate"
    assert not _applied(h, mutation), f"{mutation.__name__} changed authority it could not invalidate"
    _assert_refused_as_outage(mutation, refusal)
    h.reachable = True
    assert h.has(), "the refused change still had an effect"


@pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda m: m.__name__)
def test_a_store_write_failure_before_the_change_refuses_it(monkeypatch, mutation) -> None:
    h = _Harness(monkeypatch)
    _cached_grant(h)
    h.redis.fail = {"set", "delete"}

    refusal = _attempt(h, mutation)

    assert refusal is not None, f"{mutation.__name__} reported success without being able to invalidate"
    assert not _applied(h, mutation)
    _assert_refused_as_outage(mutation, refusal)
    h.redis.fail = set()
    assert h.has()


@pytest.mark.parametrize("mutation", ROLE_MUTATIONS, ids=lambda m: m.__name__)
def test_a_holder_lookup_failure_refuses_the_role_change(monkeypatch, mutation) -> None:
    """The holders are the users whose cache must be invalidated; unknown holders, no change."""
    h = _Harness(monkeypatch)
    _cached_grant(h)
    h.db.users.fail_find = True

    refusal = _attempt(h, mutation)

    assert isinstance(refusal, RoleServiceError) and refusal.status_code == 503, refusal
    assert not _applied(h, mutation)
    h.db.users.fail_find = False
    assert h.has()


# --------------------------------------------------------------------------- #
# The pending marker itself
# --------------------------------------------------------------------------- #


def test_a_pending_change_is_never_served_from_the_cache(monkeypatch) -> None:
    """The window between the announcement and the write, and a crash inside it."""
    h = _Harness(monkeypatch)
    _cached_grant(h)

    asyncio.run(permission_module.begin_authority_change([USER_ID]))

    h.db.roles.docs[0]["permissions"] = ["dms.document.view"]
    assert not h.has(), "a cached grant was served while the user's authority change was pending"


def test_no_cache_entry_is_written_while_a_change_is_pending(monkeypatch) -> None:
    h = _Harness(monkeypatch)
    asyncio.run(permission_module.begin_authority_change([USER_ID]))

    assert h.has(), "positive control: the uncached check still grants"
    assert h.cache_key not in h.redis.store, "a check cached a grant computed during a pending change"


def test_the_pending_marker_outlives_every_cache_entry(monkeypatch) -> None:
    assert permission_module.AUTHORITY_CHANGE_PENDING_TTL_SECONDS > permission_module.PERMISSION_CACHE_TTL_SECONDS


def test_a_completed_change_clears_the_marker_and_the_cache_serves_again(monkeypatch) -> None:
    """Fail-closed must not become cache-off: a successful change leaves no marker behind."""
    h = _Harness(monkeypatch)
    _cached_grant(h)

    _delete_role(h)

    assert not any("pending" in key for key in h.redis.store), h.redis.store
    assert not h.has()
    assert h.cache_key in h.redis.store, "the recomputed decision was not cached again"
    assert f"user_jwt_min_iat:{USER_ID}" in h.redis.store, "holders were not asked to re-authenticate"


@pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda m: m.__name__)
def test_without_a_runtime_store_there_is_no_cache_and_the_change_applies(monkeypatch, mutation) -> None:
    h = _Harness(monkeypatch, configured=False)
    assert h.has()

    assert _attempt(h, mutation) is None
    assert _applied(h, mutation)
    assert not h.has()
