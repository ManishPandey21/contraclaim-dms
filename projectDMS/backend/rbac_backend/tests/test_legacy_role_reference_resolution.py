"""A stored legacy role spelling carries its canonical role's tier AND its permissions, or neither.

Production stores `organization-admin` (x2) and `project-admin` (x1) in `users.roles`;
the role documents are keyed `orgadmin` and `projectadmin`. The R-A9B production-copy
drill (2026-09-15, `05c`) measured the split this file pins shut:

* the principal's normaliser (`core.security._normalize_roles_list`) turns
  `organization-admin` into `orgadmin`, so the account has org-admin TIER - scope,
  assignment checks, `build_scope_query`;
* `PermissionService` looked the raw string up as `roles._id`, found nothing, and loaded
  none of the `orgadmin` document's permissions.

Owner contract (2026-09-15): `organization-admin` and `orgadmin` are the same role. A
legacy spelling resolves ONE hop to exactly one active canonical document and receives
that document's permissions. Unresolved, ambiguous or inactive -> nothing: no permission,
no tier. System-role aliases (`super-admin`) never load a document through the alias.
Role aliases and permission aliases are separate; resolving one never widens the other.

`project-admin` -> `projectadmin` is derived, not inferred from the name: every role alias
table since the initial commit (`73f1e88`: `core/security.py`, `services/permission_service.py`,
`services/rbac_service.py`, `client/src/hooks/useRBAC.ts`) maps it there and nowhere else,
`projectadmin` is a seeded role id in that same commit, and `3ad4971` (codex branches only)
fixed this defect for exactly these two spellings against the same three production accounts.

Fixtures are production-shaped: the `orgadmin` document carries no lifecycle, scope or
system metadata, as measured.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from bson import ObjectId

from rbac_backend.core import security as security_module
from rbac_backend.services import permission_service as permission_module
from rbac_backend.services.permission_service import PermissionService
from rbac_backend.services.policy_service import PolicyService
from rbac_backend.utils.audit_logger import AuditLogger

USER_ID = "legacy-alias-holder"
REPO_ROOT = Path(__file__).resolve().parents[3]

_spec = importlib.util.spec_from_file_location("system_role_audit", REPO_ROOT / "scripts" / "system_role_audit.py")
audit = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("system_role_audit", audit)
_spec.loader.exec_module(audit)

ORGADMIN_PERMISSIONS = ["dms.dashboard.view", "dms.project.manage", "organizations:read", "orgadmin.sentinel"]
PROJECTADMIN_PERMISSIONS = ["dms.document.share", "projects:read", "projectadmin.sentinel"]


def _orgadmin(**extra: Any) -> Dict[str, Any]:
    """Measured shape: `_id` orgadmin, name "Organization-Admin", no metadata fields."""
    return {"_id": "orgadmin", "name": "Organization-Admin", "permissions": list(ORGADMIN_PERMISSIONS), **extra}


def _projectadmin(**extra: Any) -> Dict[str, Any]:
    return {"_id": "projectadmin", "name": "Project Admin", "permissions": list(PROJECTADMIN_PERMISSIONS), **extra}


def _superadmin(**extra: Any) -> Dict[str, Any]:
    return {"_id": "superadmin", "name": "Super Admin", "permissions": ["*"], **extra}


# --------------------------------------------------------------------------- #
# In-memory store (supports the operators the resolvers use)
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

    async def find_one(self, query, *_args, **_kwargs):
        for doc in self.docs:
            if _matches(doc, query):
                return dict(doc)
        return None

    def find(self, query=None, *_args, **_kwargs):
        return _Cursor([doc for doc in self.docs if _matches(doc, query or {})])

    async def update_one(self, query, update):
        for doc in self.docs:
            if _matches(doc, query):
                doc.update(update.get("$set", {}))
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)


class _Redis:
    def __init__(self):
        self.store: Dict[str, Any] = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, ex=None):
        self.store[key] = value

    async def delete(self, key):
        self.store.pop(key, None)


class _Holder:
    """One user holding `references`, evaluated by every resolver that turns them into authority."""

    def __init__(self, monkeypatch, references: List[Any], roles: List[Dict[str, Any]]):
        catalogue = {name for role in roles for name in role.get("permissions", []) if name != "*"}
        self.db = SimpleNamespace(
            users=_Collection(
                [{"_id": USER_ID, "email": "holder@example.com", "username": "holder", "roles": list(references)}]
            ),
            roles=_Collection(roles),
            permissions=_Collection([{"_id": ObjectId(), "name": name, "is_active": True} for name in catalogue]),
        )

        async def _get_db(_self):
            return self.db

        async def _no_redis():
            return None

        async def _log_noop(*_args, **_kwargs):
            return None

        monkeypatch.setattr(PermissionService, "_get_db", _get_db)
        monkeypatch.setattr(permission_module, "get_runtime_state", lambda: SimpleNamespace(get_redis=_no_redis))
        monkeypatch.setattr(AuditLogger, "log_permission_check", _log_noop)

    def principal_roles(self) -> List[str]:
        return asyncio.run(security_module.resolve_stored_principal(self.db, USER_ID)).roles

    def has(self, permission: str) -> bool:
        return asyncio.run(PermissionService().user_has_permission(USER_ID, permission, log=False))

    def listed(self) -> set:
        return {p.name for p in asyncio.run(PermissionService().get_user_permissions(USER_ID))}

    def effective(self) -> set:
        return set(asyncio.run(PermissionService().get_effective_permission_names(USER_ID)))

    def policy(self, permission: str) -> bool:
        policy = PolicyService(
            permission_service=PermissionService(),
            scope_service=SimpleNamespace(is_superadmin=lambda _user: False),
            entitlement_service=object(),
            audit_service=object(),
            usage_metering_service=object(),
        )
        return asyncio.run(policy.has_permission(SimpleNamespace(id=USER_ID, roles=[]), permission))

    def grants_nothing(self, tier: str, permissions: List[str]) -> List[str]:
        """Every channel through which the reference could still carry authority."""
        leaks = []
        if tier in self.principal_roles():
            leaks.append(f"principal tier {tier}")
        for name in permissions + ["users:read", "users:create"]:
            if self.has(name):
                leaks.append(f"user_has_permission {name}")
            if name in self.effective():
                leaks.append(f"effective {name}")
        if self.listed():
            leaks.append(f"listed {sorted(self.listed())}")
        return leaks


# --------------------------------------------------------------------------- #
# 1-2. The legacy spellings receive their canonical role's permissions
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "reference,tier,document,permissions",
    [
        ("organization-admin", "orgadmin", _orgadmin, ORGADMIN_PERMISSIONS),
        ("project-admin", "projectadmin", _projectadmin, PROJECTADMIN_PERMISSIONS),
    ],
)
def test_a_legacy_spelling_receives_the_canonical_documents_permissions(
    monkeypatch, reference, tier, document, permissions
) -> None:
    holder = _Holder(monkeypatch, [reference], [document(), _superadmin()])

    assert tier in holder.principal_roles(), "positive control: the principal already reads the alias as this tier"
    missing = [name for name in permissions if not holder.has(name)]
    assert missing == [], f"{reference} has {tier} tier but lacks the {tier} document's permissions: {missing}"
    assert set(permissions) <= holder.effective()
    assert set(permissions) <= holder.listed()
    assert holder.policy(permissions[0])


# --------------------------------------------------------------------------- #
# 3-4. Canonical references are unchanged
# --------------------------------------------------------------------------- #


def test_the_canonical_role_id_still_grants(monkeypatch) -> None:
    holder = _Holder(monkeypatch, ["orgadmin"], [_orgadmin(), _superadmin()])
    assert holder.principal_roles() == ["orgadmin"]
    assert all(holder.has(name) for name in ORGADMIN_PERMISSIONS)
    assert set(ORGADMIN_PERMISSIONS) <= holder.effective()


@pytest.mark.parametrize("as_object_id", [False, True])
def test_a_custom_role_referenced_by_its_object_id_still_grants(monkeypatch, as_object_id: bool) -> None:
    role_id = ObjectId()
    role = {"_id": role_id, "name": "Site Reviewer", "permissions": ["reviewer.sentinel"], "is_active": True}
    holder = _Holder(monkeypatch, [role_id if as_object_id else str(role_id)], [role, _orgadmin()])
    assert holder.has("reviewer.sentinel")
    assert "reviewer.sentinel" in holder.effective()


# --------------------------------------------------------------------------- #
# 6-9. Fail closed: inactive, deleted, ambiguous, unknown
# --------------------------------------------------------------------------- #


def test_an_alias_to_a_deactivated_canonical_role_grants_nothing(monkeypatch) -> None:
    active = _Holder(monkeypatch, ["organization-admin"], [_orgadmin(is_active=True)])
    assert "orgadmin" in active.principal_roles(), "positive control: the active canonical role is carried"

    holder = _Holder(monkeypatch, ["organization-admin"], [_orgadmin(is_active=False)])
    assert holder.grants_nothing("orgadmin", ORGADMIN_PERMISSIONS) == []


def test_an_alias_whose_canonical_document_was_deleted_grants_nothing(monkeypatch) -> None:
    holder = _Holder(monkeypatch, ["organization-admin"], [_superadmin()])
    assert holder.grants_nothing("orgadmin", ORGADMIN_PERMISSIONS) == []


def test_an_alias_that_also_names_another_role_is_ambiguous_and_grants_nothing(monkeypatch) -> None:
    lookalike = {"_id": ObjectId(), "name": "organization-admin", "permissions": ["lookalike.sentinel"], "is_active": True}
    holder = _Holder(monkeypatch, ["organization-admin"], [_orgadmin(), lookalike])
    assert holder.grants_nothing("orgadmin", ORGADMIN_PERMISSIONS + ["lookalike.sentinel"]) == []


def test_an_unknown_spelling_grants_nothing(monkeypatch) -> None:
    holder = _Holder(monkeypatch, ["organization-admins"], [_orgadmin(), _superadmin()])
    assert holder.grants_nothing("orgadmin", ORGADMIN_PERMISSIONS) == []


@pytest.mark.parametrize("stored", ["DocController", "Reporter", "ContractMgr_Org"])
def test_a_case_variant_of_a_role_id_carries_that_roles_tier_and_permissions(monkeypatch, stored: str) -> None:
    """The principal has always read `DocController` as `doccontroller`, and name-based gates read
    that key (drafting assignment, expert scope, subscription view). Dropping it would break them;
    resolving it keeps tier and permissions equal. The target's own display name is no lookalike."""
    key = stored.lower()
    role = {"_id": key, "name": stored, "permissions": [f"{key}.sentinel"]}
    holder = _Holder(monkeypatch, [stored], [role, _orgadmin()])
    assert key in holder.principal_roles()
    assert holder.has(f"{key}.sentinel")


def test_a_case_variant_of_super_admin_keeps_name_authority_but_never_loads_the_document(monkeypatch) -> None:
    document = _superadmin(permissions=["superadmin.document.sentinel"])
    holder = _Holder(monkeypatch, ["SuperAdmin"], [document])
    assert "superadmin" in holder.principal_roles(), "ADR 0001: unchanged name-based authority"
    assert "superadmin.document.sentinel" not in holder.listed()


def test_a_table_listed_case_variant_resolves_like_its_canonical_id(monkeypatch) -> None:
    holder = _Holder(monkeypatch, ["OrgAdmin"], [_orgadmin()])
    assert "orgadmin" in holder.principal_roles()
    assert all(holder.has(name) for name in ORGADMIN_PERMISSIONS)


@pytest.mark.parametrize("spelling", ["organisation-admin", "project administrator"])
def test_a_spelling_the_principal_does_not_resolve_grants_nothing_by_name(monkeypatch, spelling: str) -> None:
    """Only `permission_service`'s display-name table knows these; the reference must not borrow it."""
    holder = _Holder(monkeypatch, [spelling], [_orgadmin(), _projectadmin()])
    assert not ({"orgadmin", "projectadmin"} & set(holder.principal_roles()))
    assert not holder.has("users:create") and not holder.has("users:read")
    assert not ({"users:create", "users:read"} & holder.effective())


# --------------------------------------------------------------------------- #
# 10. System-role aliases
# --------------------------------------------------------------------------- #


def test_a_system_role_alias_never_loads_the_system_document_through_the_alias(monkeypatch) -> None:
    """ADR 0001 keeps `super-admin` name-based on the principal; the audit fails it and
    assignment refuses it. What the alias must not do is borrow the document: a
    sentinel only the document holds stays out of every non-bypass resolver."""
    document = _superadmin(permissions=["superadmin.document.sentinel"])
    holder = _Holder(monkeypatch, ["super-admin"], [document])
    assert "superadmin.document.sentinel" not in holder.listed()


def test_an_alias_to_a_deactivated_super_admin_document_is_dropped_from_the_principal(monkeypatch) -> None:
    active = _Holder(monkeypatch, ["super-admin"], [_superadmin()])
    assert "superadmin" in active.principal_roles(), "positive control: ADR 0001 name-based authority"

    holder = _Holder(monkeypatch, ["super-admin"], [_superadmin(is_active=False)])
    assert "superadmin" not in holder.principal_roles(), "deactivating superadmin did not revoke its alias"
    assert holder.effective() != {"*"}


# --------------------------------------------------------------------------- #
# 11. Role aliases do not widen permission aliases
# --------------------------------------------------------------------------- #


def test_an_alias_holder_with_task_management_does_not_gain_project_management(monkeypatch) -> None:
    role = _orgadmin(permissions=["dms.task.manage"])
    holder = _Holder(monkeypatch, ["organization-admin"], [role])
    assert holder.has("dms.task.manage"), "positive control: the canonical document's permission is granted"
    assert holder.has("projects:update"), "the one-hop legacy route gate `dms.task.manage` declares"
    assert not holder.has("dms.project.manage")
    assert not holder.policy("dms.project.manage")
    assert "dms.project.manage" not in holder.effective()


def test_mutating_the_canonical_role_revokes_a_legacy_holders_cached_grant(monkeypatch) -> None:
    """The holder receives `orgadmin`'s permissions through the spelling, so deleting (or editing)
    `orgadmin` must drop that holder's cached grant too, not leave it for the cache TTL."""
    from rbac_backend.services import runtime_state as runtime_state_module
    from rbac_backend.services.role_service import RoleService

    holder = _Holder(monkeypatch, ["organization-admin"], [_orgadmin(), _superadmin()])
    redis = _Redis()

    async def _get_redis():
        return redis

    async def _role_db(_self):
        return holder.db

    runtime = SimpleNamespace(get_redis=_get_redis)
    monkeypatch.setattr(permission_module, "get_runtime_state", lambda: runtime)
    monkeypatch.setattr(runtime_state_module, "get_runtime_state", lambda: runtime)
    monkeypatch.setattr(RoleService, "_get_db", _role_db)

    assert holder.has("orgadmin.sentinel"), "positive control: the grant is computed and cached"
    cache_key = permission_module.permission_cache_key(USER_ID)
    assert cache_key in redis.store

    superadmin = SimpleNamespace(id="sa", roles=["superadmin"], organization_id=None, projects=[])
    assert asyncio.run(RoleService().delete_role("orgadmin", superadmin))

    assert cache_key not in redis.store, "the legacy holder's cached grant outlived the role's deletion"
    assert f"user_jwt_min_iat:{USER_ID}" in redis.store, "the legacy holder was not asked to re-authenticate"
    assert not holder.has("orgadmin.sentinel")


def test_holder_counts_include_legacy_spellings(monkeypatch) -> None:
    from rbac_backend.services.role_service import RoleService

    holder = _Holder(monkeypatch, ["organization-admin"], [_orgadmin()])
    holder.db.users.docs.append({"_id": "canonical-holder", "roles": ["orgadmin"], "is_active": True})
    holder.db.users.docs.append({"_id": "lookalike-case", "roles": ["Reporter"], "is_active": True})
    holder.db.users.docs[0]["is_active"] = True

    async def _role_db(_self):
        return holder.db

    monkeypatch.setattr(RoleService, "_get_db", _role_db)
    assert asyncio.run(RoleService().count_users_with_role("orgadmin")) == 2


def test_grants_cached_before_legacy_spellings_resolved_are_never_read() -> None:
    """A `v2` entry for an alias holder carries the alias's role names without its document."""
    key = permission_module.permission_cache_key("u1")
    assert key not in {"user_perms:u1", "user_perms:v2:u1"}, f"permission cache key {key} is a pre-fix key"


# --------------------------------------------------------------------------- #
# 12. The audit classifies a reference exactly as authorization treats it
# --------------------------------------------------------------------------- #


def _lookalike() -> Dict[str, Any]:
    return {"_id": ObjectId(), "name": "organization-admin", "permissions": ["lookalike.sentinel"], "is_active": True}


PARITY_CASES = {
    "canonical": (["orgadmin"], lambda: [_orgadmin()]),
    "legacy alias": (["organization-admin"], lambda: [_orgadmin()]),
    "legacy project alias": (["project-admin"], lambda: [_projectadmin()]),
    "alias to deactivated": (["organization-admin"], lambda: [_orgadmin(is_active=False)]),
    "alias to deleted": (["organization-admin"], lambda: []),
    "ambiguous alias": (["organization-admin"], lambda: [_orgadmin(), _lookalike()]),
    "unknown": (["organization-admins"], lambda: [_orgadmin()]),
    "deactivated canonical": (["orgadmin"], lambda: [_orgadmin(is_active=False)]),
}


def _audit_disposition(reference: str, roles: List[Dict[str, Any]]) -> str:
    from rbac_backend.core.role_reference import resolve_role_reference

    users = [(["superadmin"], False), (["superadmin"], False), ([reference], False)]
    # As the gate runs it inside the candidate image: embedded copy plus the image's own module.
    report = audit.evaluate(roles + [_superadmin()], users, 2, resolve_role_reference)
    if any(reference in line for line in report.failures):
        return "fail"
    if any(reference in line for line in report.warnings):
        return "warn"
    return "pass"


@pytest.mark.parametrize("case", sorted(PARITY_CASES))
def test_audit_disposition_equals_runtime_authority(monkeypatch, case: str) -> None:
    (reference,), roles = PARITY_CASES[case][0], PARITY_CASES[case][1]()
    tier = security_module._normalize_roles_list([reference])[0]
    sentinels = [name for role in roles for name in role.get("permissions", [])]
    holder = _Holder(monkeypatch, [reference], roles + [_superadmin()])

    runtime_grants = bool(sentinels) and all(holder.has(name) for name in sentinels if name.endswith(".sentinel"))
    if case == "ambiguous alias":
        runtime_grants = any(holder.has(name) for name in sentinels)
    # An unknown spelling normalises to itself; carrying that string is not a tier.
    tier_carried = tier in set(security_module.ROLE_ALIASES.values()) and tier in holder.principal_roles()
    disposition = _audit_disposition(reference, roles)

    assert tier_carried == runtime_grants, f"{case}: principal tier {tier_carried} but permissions {runtime_grants}"
    assert (disposition in {"pass", "warn"}) == runtime_grants, (
        f"{case}: audit says {disposition} but authorization {'grants' if runtime_grants else 'grants nothing'}"
    )
    assert (disposition == "warn") == (runtime_grants and reference != tier), (
        f"{case}: only a legacy spelling that authorization really resolves may warn (audit: {disposition})"
    )
