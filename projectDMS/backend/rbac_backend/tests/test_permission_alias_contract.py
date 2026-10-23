"""Legacy permission aliases translate a name; they never create authority.

`LEGACY_PERMISSION_ALIASES` declares, per canonical permission, the legacy names
that used to gate the same capability. Both directions are load-bearing, and the
source says so:

* canonical holder -> legacy route gate. The default roles store canonical
  `dms.*` names while routes still depend on legacy names
  (`require_permission("projects:update")` on `PUT /api/projects/{id}`), and the
  Gate 4 row-12 control relies on it.
* legacy holder -> canonical check. The table was introduced (1fe9fc5) "so legacy
  role strings still satisfy new router checks", and production roles still store
  `projects:update`, `organizations:read`, `system:admin`.

What the table does not say is that two canonical permissions sharing a legacy
name are the same permission. Routes gate on distinct canonicals
(`authorize("dms.task.manage")` vs `authorize("dms.project.manage")`); making them
equivalent would make those gates identical. Resolution used to be transitive,
through a reverse table in `PermissionService` and a second expansion in
`PolicyService.has_permission`, so a role holding only `dms.task.manage` passed
`dms.project.manage` (F-A9A-2) and a role holding only `billing.plan.manage` passed
`dms.admin` and, through it, every `dms.*` check (R-A9B).

The contract pinned here: a held name satisfies a checked name iff they are the
same name (label spellings such as `projects:edit` included), or one declares the
other as its alias. One hop, in either direction, never through a shared alias.
"""

from __future__ import annotations

import asyncio
from itertools import permutations
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Set

import pytest

from rbac_backend.core.permissions import LEGACY_LABEL_ALIASES, LEGACY_PERMISSION_ALIASES
from rbac_backend.services import permission_service as permission_module
from rbac_backend.services.permission_service import PermissionService
from rbac_backend.services.policy_service import PolicyService
from rbac_backend.utils.audit_logger import AuditLogger

BACKEND_ROOT = Path(__file__).resolve().parents[1]
#: `users:create` / `users:read` are decided by role NAME in the resolver, not by
#: aliases; a probe role with no name cannot exercise them.
ROLE_NAME_DECIDED = {"users:create", "users:read"}


def _declarers() -> Dict[str, List[str]]:
    reverse: Dict[str, List[str]] = {}
    for canonical, aliases in LEGACY_PERMISSION_ALIASES.items():
        for alias in aliases:
            reverse.setdefault(alias, []).append(canonical)
    return reverse


SHARED_LEGACY_ALIASES = {alias: sorted(names) for alias, names in _declarers().items() if len(names) > 1}
MULTI_ALIAS_CANONICALS = {c: sorted(a) for c, a in LEGACY_PERMISSION_ALIASES.items() if len(a) > 1}


def _policy_supers(permission: str) -> Set[str]:
    """The admin permissions `PolicyService.has_permission` deliberately accepts for a family."""
    if permission.startswith("drafting."):
        return {"drafting.admin"}
    if permission.startswith("dms."):
        return {"dms.admin"}
    if permission.startswith(("billing.", "subscription.")):
        return {"billing.plan.manage", "subscription.entitlement.manage"}
    return set()


class _Collection:
    def __init__(self, docs):
        self.docs = docs

    async def find_one(self, query, *_args, **_kwargs):
        for doc in self.docs:
            if all(str(doc.get(key)) == str(value) for key, value in query.items()):
                return dict(doc)
        return None

    def find(self, *_args, **_kwargs):
        class _Cursor:
            async def to_list(self, length=None):
                return []

        return _Cursor()


@pytest.fixture
def hold(monkeypatch):
    state = {}

    async def _get_db(self):
        return state["db"]

    async def _no_redis():
        return None

    async def _log_noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr(PermissionService, "_get_db", _get_db)
    monkeypatch.setattr(permission_module, "get_runtime_state", lambda: SimpleNamespace(get_redis=_no_redis))
    monkeypatch.setattr(AuditLogger, "log_permission_check", _log_noop)

    def _hold(*held: str) -> SimpleNamespace:
        state["db"] = SimpleNamespace(
            users=_Collection([{"_id": "u1", "roles": ["r1"]}]),
            roles=_Collection([{"_id": "r1", "permissions": list(held)}]),
            permissions=_Collection([]),
        )
        service = PermissionService()
        policy = PolicyService(
            permission_service=service,
            scope_service=SimpleNamespace(is_superadmin=lambda _user: False),
            entitlement_service=object(),
            audit_service=object(),
            usage_metering_service=object(),
        )
        principal = SimpleNamespace(id="u1", roles=[])
        return SimpleNamespace(
            satisfies=lambda name: asyncio.run(service.user_has_permission("u1", name, log=False)),
            policy_satisfies=lambda name: asyncio.run(policy.has_permission(principal, name)),
            effective=lambda: set(asyncio.run(service.get_effective_permission_names("u1"))),
        )

    return _hold


# --------------------------------------------------------------------------- #
# The reported case
# --------------------------------------------------------------------------- #


def test_task_management_alone_does_not_satisfy_project_management(hold) -> None:
    holder = hold("dms.task.manage")
    assert not holder.satisfies("dms.project.manage")
    assert not holder.policy_satisfies("dms.project.manage")
    assert "dms.project.manage" not in holder.effective()


def test_task_management_still_passes_the_legacy_route_gate_it_declares(hold) -> None:
    """The dependency on `projects:update` is coarse by design; the canonical gate is the precise one."""
    assert hold("dms.task.manage").satisfies("projects:update")


def test_project_management_satisfies_project_management_and_its_legacy_route_gates(hold) -> None:
    holder = hold("dms.project.manage")
    assert holder.satisfies("dms.project.manage") and holder.policy_satisfies("dms.project.manage")
    for legacy in ("projects:create", "projects:update", "projects:delete", "projects:assign"):
        assert holder.satisfies(legacy), legacy


def test_a_stored_legacy_permission_satisfies_each_canonical_that_declares_it(hold) -> None:
    """Legacy compatibility: a role storing the legacy name keeps what that name used to gate."""
    holder = hold("projects:update")
    for canonical in ("dms.project.manage", "dms.task.manage", "dms.claim.manage"):
        assert holder.satisfies(canonical), canonical
        assert holder.policy_satisfies(canonical), canonical


def test_a_label_spelling_is_the_same_name_as_its_target(hold) -> None:
    holder = hold("projects:edit")
    assert holder.satisfies("projects:update")
    assert holder.satisfies("dms.project.manage"), "the label lost the legacy compatibility of its target"
    assert hold("orgs:view").satisfies("organizations:read")
    assert hold("organizations:read").satisfies("orgs:view")


def test_legacy_siblings_do_not_satisfy_each_other(hold) -> None:
    holder = hold("projects:create")
    assert not holder.satisfies("projects:delete")
    assert not holder.policy_satisfies("projects:delete")
    assert "projects:delete" not in holder.effective()


def test_billing_and_subscription_admins_do_not_become_dms_admins(hold) -> None:
    for held in ("billing.plan.manage", "subscription.entitlement.manage", "subscription.upgrade"):
        holder = hold(held)
        for checked in ("dms.admin", "dms.project.manage", "dms.document.delete", "system:admin"):
            assert not holder.policy_satisfies(checked), f"{held} satisfied {checked}"
        assert not holder.satisfies("system:admin"), f"{held} reached system administration (F-A9B-2)"


def test_a_stored_system_admin_is_not_an_alias_of_dms_administration(hold) -> None:
    """R-A9D (F-A9B-2): `system:admin` is no canonical's legacy name, in either direction.
    Pinned in full by `test_system_admin_authority_contract.py`."""
    holder = hold("system:admin")
    assert holder.satisfies("system:admin")
    assert not holder.satisfies("dms.admin")
    assert not holder.policy_satisfies("dms.document.delete")


# --------------------------------------------------------------------------- #
# Matrices over every ambiguous alias group
# --------------------------------------------------------------------------- #


def test_the_ambiguous_groups_are_the_ones_this_contract_was_written_against() -> None:
    """A new shared alias must be looked at, not silently absorbed by the matrix."""
    assert set(SHARED_LEGACY_ALIASES) == {
        "projects:update",
        "projects:read",
        "reports:view",
        "organizations:read",
    }
    assert set(MULTI_ALIAS_CANONICALS) == {"dms.user.manage", "dms.project.manage"}


@pytest.mark.parametrize("alias", sorted(SHARED_LEGACY_ALIASES))
def test_canonicals_sharing_a_legacy_alias_do_not_satisfy_each_other(hold, alias: str) -> None:
    violations = []
    for held, checked in permutations(SHARED_LEGACY_ALIASES[alias], 2):
        holder = hold(held)
        if holder.satisfies(checked):
            violations.append(f"user_has_permission: {held} -> {checked}")
        if holder.policy_satisfies(checked) != (held in _policy_supers(checked)):
            violations.append(f"PolicyService.has_permission: {held} -> {checked}")
        if checked in holder.effective():
            violations.append(f"effective names: {held} -> {checked}")
    assert violations == [], f"authority fans out through {alias}:\n" + "\n".join(violations)


@pytest.mark.parametrize("canonical", sorted(MULTI_ALIAS_CANONICALS))
def test_legacy_names_sharing_a_canonical_do_not_satisfy_each_other(hold, canonical: str) -> None:
    violations = []
    for held, checked in permutations(MULTI_ALIAS_CANONICALS[canonical], 2):
        if checked in ROLE_NAME_DECIDED:
            continue
        holder = hold(held)
        if holder.satisfies(checked) or holder.policy_satisfies(checked) or checked in holder.effective():
            violations.append(f"{held} -> {checked}")
    assert violations == [], f"authority fans out through {canonical}:\n" + "\n".join(violations)


def test_every_declared_alias_still_resolves_one_hop_in_both_directions(hold) -> None:
    violations = []
    for canonical, aliases in LEGACY_PERMISSION_ALIASES.items():
        for alias in aliases:
            if alias not in ROLE_NAME_DECIDED and not hold(canonical).satisfies(alias):
                violations.append(f"canonical holder {canonical} refused legacy gate {alias}")
            if not hold(alias).satisfies(canonical):
                violations.append(f"legacy holder {alias} refused canonical check {canonical}")
    assert violations == [], "\n".join(violations)


def test_effective_names_are_exactly_what_the_resolver_grants(hold) -> None:
    """`/auth/me` must not advertise a permission the gate refuses, or hide one it grants."""
    labels = {label for spellings in LEGACY_LABEL_ALIASES.values() for label in spellings}
    universe = set(LEGACY_PERMISSION_ALIASES) | set(_declarers()) | set(LEGACY_LABEL_ALIASES) | labels
    violations = []
    for held in sorted(universe):
        holder = hold(held)
        granted = {name for name in universe if name not in ROLE_NAME_DECIDED and holder.satisfies(name)}
        advertised = holder.effective() - ROLE_NAME_DECIDED
        if granted | {held} != advertised | {held}:
            violations.append(f"{held}: granted-only={sorted(granted - advertised)} advertised-only={sorted(advertised - granted - {held})}")
    assert violations == [], "\n".join(violations)


# --------------------------------------------------------------------------- #
# R-A9B: the same fan-out, measured through the real authorize chain
# --------------------------------------------------------------------------- #


def _authorize(monkeypatch, role: Dict, principal_roles: List[str], permission: str, organization_id=None) -> str:
    from fastapi import HTTPException

    from rbac_backend.services.entitlement_service import EntitlementService
    from rbac_backend.services.scope_service import ScopeService

    class _Memberships:
        def find(self, *_args, **_kwargs):
            class _Cursor:
                async def to_list(self, length=None):
                    return []

            return _Cursor()

    db = SimpleNamespace(
        users=_Collection([{"_id": "u1", "roles": [role["_id"]], "organization_id": "org1"}]),
        roles=_Collection([role]),
        permissions=_Collection([]),
        organization_memberships=_Memberships(),
        project_memberships=_Memberships(),
    )

    async def _noop(*_args, **_kwargs):
        return None

    async def _get_db(self):
        return db

    # The `hold` fixture wires PermissionService to its own probe database; this
    # chain must read THIS role, or a refusal would be vacuous.
    monkeypatch.setattr(PermissionService, "_get_db", _get_db)
    policy = PolicyService(
        db,
        permission_service=PermissionService(),
        scope_service=ScopeService(db),
        entitlement_service=EntitlementService(db),
        audit_service=SimpleNamespace(emit=_noop),
        usage_metering_service=SimpleNamespace(check_and_record=_noop),
    )
    principal = SimpleNamespace(
        id="u1", roles=principal_roles, organization_id="org1", organizations=["org1"], projects=[], account_type="client_user"
    )
    try:
        asyncio.run(policy.authorize(principal, permission, organization_id=organization_id, resource_type="probe"))
        return "allow"
    except HTTPException as exc:
        return f"{exc.status_code} {exc.detail}"


def _default_role(key: str) -> Dict:
    from rbac_backend.initial_data.default_roles import DEFAULT_ROLES

    return next(dict(role) for role in DEFAULT_ROLES if role["_id"] == key)


@pytest.mark.parametrize(
    "permission,organization_id",
    [
        ("billing.plan.manage", None),  # POST/PUT /api/plans: the platform-wide plan catalogue
        ("subscription.entitlement.manage", None),
        ("subscription.entitlement.manage", "org1"),  # PUT /api/subscriptions/{id} in its own org
        ("subscription.upgrade", "org1"),
    ],
)
def test_the_default_org_admin_is_not_a_billing_or_subscription_admin(monkeypatch, hold, permission, organization_id) -> None:
    """Before R-A9B `dms.admin` reached these through `system:admin`, and the billing
    branch of `authorize` allows `billing.plan.manage` with no tenant scope at all."""
    hold()  # installs the Redis / audit stubs
    decision = _authorize(monkeypatch, _default_role("orgadmin"), ["orgadmin"], permission, organization_id)
    assert decision == "403 Not authorized: missing_permission", decision


def test_the_billing_admin_control_still_manages_plans(monkeypatch, hold) -> None:
    """Positive control: the same chain reads the role it is given."""
    hold()
    decision = _authorize(
        monkeypatch, _default_role("contraclaim_billing_admin"), ["contraclaim_billing_admin"], "billing.plan.manage"
    )
    assert decision == "allow", decision


def test_a_held_dms_admin_does_not_pass_the_system_admin_route_gate(hold) -> None:
    """F-A9B-2, closed in R-A9D: `/api/admin/legal-words` depends on
    `require_permission("system:admin")`, and organisation administration does not reach it."""
    assert not hold("dms.admin").satisfies("system:admin")


# --------------------------------------------------------------------------- #
# Guard: one resolution function
# --------------------------------------------------------------------------- #


def test_alias_tables_are_only_read_by_the_resolution_function() -> None:
    """A second expansion elsewhere is how the transitive fan-out came back through PolicyService."""
    offenders = []
    for path in BACKEND_ROOT.rglob("*.py"):
        if "tests" in path.parts or path.relative_to(BACKEND_ROOT).as_posix() == "core/permissions.py":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for token in ("LEGACY_PERMISSION_ALIASES", "ALIAS_TO_CANONICAL", "_permission_aliases"):
            if token in text:
                offenders.append(f"{path.relative_to(BACKEND_ROOT).as_posix()}: {token}")
    assert offenders == [], "alias tables read outside equivalent_permissions:\n" + "\n".join(offenders)


def test_only_the_resolver_expands_a_name_before_checking_it() -> None:
    """Calling `equivalent_permissions` and then `user_has_permission` on each result is
    the second hop; the R-A9B review noted the table-name guard above cannot see it."""
    allowed = {"core/permissions.py", "services/permission_service.py"}
    offenders = [
        path.relative_to(BACKEND_ROOT).as_posix()
        for path in BACKEND_ROOT.rglob("*.py")
        if "tests" not in path.parts
        and path.relative_to(BACKEND_ROOT).as_posix() not in allowed
        and "equivalent_permissions(" in path.read_text(encoding="utf-8", errors="ignore")
    ]
    assert offenders == [], f"alias expansion outside the resolver: {offenders}"
