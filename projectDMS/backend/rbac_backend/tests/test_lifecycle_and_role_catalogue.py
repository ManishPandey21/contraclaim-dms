"""Counts must summarise the list they describe, and every tier must be grantable.

ISSUE-03: the Dashboard aggregated over ``documents`` with no lifecycle
predicate while the Library excluded soft-deleted and duplicate-held rows, so
the card reported 59 documents against a list of 50. The exclusion list already
existed twice -- in ``document_service`` and in ``projects`` -- which is exactly
how a third surface came to omit it. One definition now, plus a guard against a
fourth copy.

ISSUE-04: ``superuser`` is a global tenancy tier in ``core.effective_scope`` and
is handled explicitly in ``security`` and ``policy_service``, but it had no entry
in the role catalogue. Permission resolution therefore returned nothing and the
account was denied everywhere -- it could sign in and do nothing at all. The
tier-coverage test below fails for *any* tier declared in effective_scope that
the catalogue cannot grant, so the next tier added cannot repeat it.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from rbac_backend.core import effective_scope
from rbac_backend.core.document_lifecycle import (
    HIDDEN_LIFECYCLE_STATES,
    apply_hidden_lifecycle,
    hidden_lifecycle_filter,
    hidden_lifecycle_states,
)
from rbac_backend.initial_data.default_roles import DEFAULT_ROLES

BACKEND_ROOT = pathlib.Path(effective_scope.__file__).resolve().parent.parent


# --- ISSUE-03: one definition of "hidden from listings" --------------------


def test_hidden_states_cover_soft_delete_and_duplicate_holds():
    assert "deleted" in HIDDEN_LIFECYCLE_STATES
    assert "duplicate_review" in HIDDEN_LIFECYCLE_STATES
    assert "duplicate" in HIDDEN_LIFECYCLE_STATES


def test_filter_is_a_list_because_mongo_operators_reject_tuples():
    value = hidden_lifecycle_filter()["$nin"]
    assert isinstance(value, list)
    assert isinstance(hidden_lifecycle_states(), list)


def test_apply_adds_the_predicate_to_a_bare_query():
    query: dict = {}
    apply_hidden_lifecycle(query)
    assert query["lifecycle_state"] == {"$nin": list(HIDDEN_LIFECYCLE_STATES)}


def test_apply_preserves_a_deliberate_lifecycle_filter():
    """An audit or restore view must keep its ability to ask for deleted rows."""
    query = {"lifecycle_state": "deleted"}
    apply_hidden_lifecycle(query)
    assert query["lifecycle_state"] == "deleted"


def test_apply_does_not_disturb_the_tenant_predicate():
    query = {"organization_id": {"$in": ["org-a"]}, "project_id": {"$in": ["p1"]}}
    apply_hidden_lifecycle(query)
    assert query["organization_id"] == {"$in": ["org-a"]}
    assert query["project_id"] == {"$in": ["p1"]}


def test_dashboard_applies_the_shared_exclusion():
    """The count endpoint must route through the same helper as the list."""
    src = (BACKEND_ROOT / "routers" / "dashboard.py").read_text(encoding="utf-8")
    assert "apply_hidden_lifecycle" in src, (
        "dashboard aggregates documents; without the lifecycle exclusion its "
        "totals describe a wider set than the Library it summarises"
    )


def test_no_module_respells_the_exclusion_list():
    """Guard the single definition -- the duplication is what caused ISSUE-03."""
    literal = re.compile(r'"deleted"\s*,\s*"duplicate_review"\s*,\s*"duplicate"')
    offenders = []
    for path in BACKEND_ROOT.rglob("*.py"):
        if path.name == "document_lifecycle.py" or "tests" in path.parts:
            continue
        if literal.search(path.read_text(encoding="utf-8")):
            offenders.append(str(path.relative_to(BACKEND_ROOT)))
    assert not offenders, (
        "these modules re-spell the hidden-lifecycle list instead of importing it "
        "from core.document_lifecycle: " + ", ".join(offenders)
    )


# --- ISSUE-04: every tenancy tier must be grantable ------------------------


def _catalogue_ids() -> set[str]:
    return {str(role.get("_id")) for role in DEFAULT_ROLES}


def test_superuser_exists_in_the_catalogue():
    assert "superuser" in _catalogue_ids(), (
        "superuser is a global tier in effective_scope; without a role document "
        "it resolves to zero permissions and is denied on every endpoint"
    )


def test_superuser_can_actually_use_the_product():
    role = next(r for r in DEFAULT_ROLES if r["_id"] == "superuser")
    perms = set(role["permissions"])
    for required in ("dms.document.view", "dms.dashboard.view", "projects:read"):
        assert required in perms, f"superuser cannot {required}"


def test_superuser_has_the_same_capabilities_as_super_admin():
    """Capability and tenancy are independent axes for this role.

    Super User does everything Super Admin can do, but only inside the
    organisations assigned to the account -- that bound is enforced by
    EffectiveScope, not by withholding permissions.
    """
    su = next(r for r in DEFAULT_ROLES if r["_id"] == "superuser")
    sa = next(r for r in DEFAULT_ROLES if r["_id"] == "superadmin")
    assert set(su["permissions"]) == set(sa["permissions"])


def test_superuser_capabilities_do_not_include_a_wildcard():
    """A concrete list, never ``*``.

    ``permission_service`` short-circuits to ``["*"]`` on the *role name*
    superadmin. Super User must not acquire that blanket grant through its
    permission list, or the tenancy bound would be the only thing left holding.
    """
    su = next(r for r in DEFAULT_ROLES if r["_id"] == "superuser")
    assert "*" not in su["permissions"]


def test_superuser_remains_tenancy_bounded_not_platform_wide():
    """The capability change must not turn it into a second superadmin."""
    from rbac_backend.core.effective_scope import EffectiveScope, UNBOUNDED

    class _Actor:
        id = "u1"
        roles = ["superuser"]
        organization_id = None
        project_id = None
        organizations = ["org-a"]
        projects = []

    scope = EffectiveScope.resolve(_Actor())
    assert scope.authorized_org_ids is not UNBOUNDED, (
        "a Super User is bounded by its assigned organisations; only superadmin "
        "is unbounded"
    )
    assert scope.authorized_org_ids == frozenset({"org-a"})


@pytest.mark.parametrize(
    "tier_role",
    sorted(
        effective_scope.GLOBAL_ROLES
        | effective_scope.ORG_ROLES
        | effective_scope.PROJECT_ROLES
    ),
)
def test_every_declared_tenancy_tier_has_a_role_document(tier_role):
    """A tier the scope engine understands but the catalogue cannot grant is a
    role that authenticates and then does nothing -- the ISSUE-04 shape."""
    assert tier_role in _catalogue_ids(), (
        f"{tier_role} is a tenancy tier in effective_scope but has no entry in "
        "DEFAULT_ROLES, so accounts holding it resolve to zero permissions"
    )


def test_unresolved_role_is_logged_rather_than_dropped_silently():
    """The deny is correct; the silence was the defect.

    Asserted with a handler attached straight to the module's logger rather than
    via ``caplog``: this suite runs under a custom async bridge, and a capture
    that quietly collects nothing would let this test pass while the warning was
    gone.
    """
    import logging

    from rbac_backend.services import permission_service

    records: list[logging.LogRecord] = []

    class _Collector(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    handler = _Collector(level=logging.WARNING)
    logger = permission_service.logger
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        permission_service._warn_unresolved_role("no-such-role", where="unit-test")
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)

    assert records, "an unresolved role must not be dropped in silence"
    message = records[0].getMessage()
    assert "no-such-role" in message
    assert "default_roles" in message
    assert records[0].levelno == logging.WARNING
