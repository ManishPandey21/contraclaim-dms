"""superadmin means platform-wide *entitlement*, not "skip scoping".

``core/effective_scope`` states the rule these four endpoints violated: a
superadmin who has selected an organisation is working in that organisation, and
a query that ignores the selection is a defect. Four sites read the role and
skipped scope entirely -- two observability reads, a vector-reconcile write, and
a monetization read -- plus user creation, which lands an account in a tenant and
is the one bypass with lasting side effects.

The distinction each test below turns on: with **no** selection active the scope
genuinely is platform-wide and unscoped behaviour is correct. It is only wrong
when a selection exists and is ignored.
"""

from __future__ import annotations

import pytest

from rbac_backend.core.security import CurrentUser
from rbac_backend.routers.rbac_monetization import _subscription_scope_filters
from rbac_backend.routers.retrieval_engine import _selection_defaults

ORG_A = "org-a"
ORG_B = "org-b"
PROJ_A1 = "proj-a1"


def _superadmin(selected_org=None, selected_project=None):
    """A superadmin's organization_id *is* the navbar selection, not a home org."""
    return CurrentUser(
        id="admin-1",
        username="admin",
        email="admin@example.com",
        roles=["superadmin"],
        organization_id=selected_org,
        project_id=selected_project,
        organizations=[],
        projects=[],
    )


# --- observability reads ---------------------------------------------------


def test_unscoped_stays_unscoped_when_nothing_is_selected():
    """All Organisations is a real working context; do not break it."""
    assert _selection_defaults(_superadmin(), None, None) == (None, None)


def test_an_active_selection_narrows_the_read():
    org, project = _selection_defaults(_superadmin(ORG_A, PROJ_A1), None, None)
    assert org == ORG_A
    assert project == PROJ_A1


def test_an_explicit_argument_still_wins_over_the_selection():
    """Explicit scope is validated elsewhere; this helper only fills the gap."""
    org, project = _selection_defaults(_superadmin(ORG_A), ORG_B, None)
    assert org == ORG_B


def test_organisation_selection_alone_is_honoured():
    org, project = _selection_defaults(_superadmin(ORG_A), None, None)
    assert org == ORG_A
    assert project is None


# --- monetization read -----------------------------------------------------


@pytest.mark.asyncio
async def test_billing_is_unfiltered_only_with_no_selection():
    orgs, projects = await _subscription_scope_filters(_superadmin())
    assert orgs is None and projects is None


@pytest.mark.asyncio
async def test_billing_follows_the_selected_organisation():
    orgs, projects = await _subscription_scope_filters(_superadmin(ORG_A))
    assert orgs == [ORG_A]
    assert projects is None


@pytest.mark.asyncio
async def test_billing_follows_the_selected_project():
    orgs, projects = await _subscription_scope_filters(_superadmin(ORG_A, PROJ_A1))
    assert orgs == [ORG_A]
    assert projects == [PROJ_A1]


# --- write paths -----------------------------------------------------------


def test_vector_reconcile_consults_the_working_scope():
    """Guard: the write must not reach across tenants on role alone."""
    import pathlib

    from rbac_backend.routers import retrieval_engine

    src = pathlib.Path(retrieval_engine.__file__).read_text(encoding="utf-8")
    assert "Superadmin required for reconciliation" in src
    assert "permits_within_selection" in src, (
        "reconciliation rewrites vector state; being superadmin authorises the "
        "operation, not an arbitrary tenant"
    )


def test_user_creation_consults_the_working_scope():
    import pathlib

    from rbac_backend.routers import users

    src = pathlib.Path(users.__file__).read_text(encoding="utf-8")
    assert "Superadmin can create users in any organization" not in src, (
        "creating an account in an unselected organisation is the one superadmin "
        "bypass with lasting side effects"
    )
    assert "permits_within_selection" in src


def test_no_endpoint_skips_scope_on_the_role_alone():
    """Every remaining superadmin branch must consult the scope, not just return.

    Reads the four modules the audit named and requires each to mention
    EffectiveScope, so a future 'if superadmin: pass' cannot be added without at
    least confronting the rule.
    """
    import pathlib

    from rbac_backend.routers import (
        rbac_monetization,
        retrieval_engine,
        users,
    )

    for module in (retrieval_engine, rbac_monetization, users):
        src = pathlib.Path(module.__file__).read_text(encoding="utf-8")
        assert "EffectiveScope" in src, (
            f"{module.__name__} branches on superadmin but never consults the "
            "effective scope"
        )
