"""Notifications narrow with the navbar without hiding organisation-wide notices.

Recipient membership decides what was addressed to a user. It says nothing about
whether a notice belongs to the organisation and project they are currently
working in, so the listing used to show everything addressed to them regardless
of context.

The naive fix -- filtering on the selected project id -- is wrong, and wrong in
the dangerous direction: an organisation-wide notice carries no project, so it
would vanish exactly when a project is selected. Subscription expiry and access
changes are the messages users least afford to miss.

Ownership decides instead, at three levels:

* platform  -- no organisation, no project: belongs to no tenant
* organisation -- an organisation, no project: applies to all of it
* project   -- both: belongs to that project alone

Asserted at record level against a seeded set, because a predicate that looks
correct can still select the wrong rows.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rbac_backend.core.effective_scope import EffectiveScope
from rbac_backend.tests.test_scope_acceptance_matrix import matches

# --- seeded notifications, one per ownership level per tenant --------------

NOTIFICATIONS = [
    {"_id": "platform", "organization_id": None, "project_id": None},
    {"_id": "platform-absent"},  # same level, fields omitted rather than null
    {"_id": "org-A-wide", "organization_id": "org-A", "project_id": None},
    {"_id": "org-A-A1", "organization_id": "org-A", "project_id": "proj-A1"},
    {"_id": "org-A-A2", "organization_id": "org-A", "project_id": "proj-A2"},
    {"_id": "org-B-wide", "organization_id": "org-B", "project_id": None},
    {"_id": "org-B-B1", "organization_id": "org-B", "project_id": "proj-B1"},
]

PLATFORM = {"platform", "platform-absent"}
ORG_A_ALL = {"org-A-wide", "org-A-A1", "org-A-A2"}
ORG_B_ALL = {"org-B-wide", "org-B-B1"}


def visible(scope: EffectiveScope):
    query = scope.ownership_filter()
    return {n["_id"] for n in NOTIFICATIONS if matches(n, query)}


def actor(roles, org=None, project=None, orgs=(), projects=()):
    return SimpleNamespace(
        id="u1", roles=list(roles), organization_id=org, project_id=project,
        organizations=list(orgs), projects=list(projects),
    )


# --- the three worked examples from the specification ----------------------


def test_organisation_a_project_a1_keeps_organisation_wide_notices():
    """Org A / Project A1 sees org-A-wide *and* project-A1 -- not A2, not B."""
    scope = EffectiveScope.resolve(actor(["superadmin"], org="org-A", project="proj-A1"))
    seen = visible(scope)

    assert "org-A-wide" in seen, "an organisation-wide notice must survive a project selection"
    assert "org-A-A1" in seen
    assert "org-A-A2" not in seen, "another project's notice must not appear"
    assert seen & ORG_B_ALL == set(), "another organisation must never appear"


def test_organisation_a_all_projects_shows_everything_under_a():
    scope = EffectiveScope.resolve(actor(["orgadmin"], org="org-A", orgs=["org-A"]))
    seen = visible(scope)

    assert ORG_A_ALL <= seen
    assert seen & ORG_B_ALL == set()


def test_all_organisations_all_projects_shows_everything_permitted():
    scope = EffectiveScope.resolve(actor(["superadmin"]))
    assert visible(scope) == {n["_id"] for n in NOTIFICATIONS}


# --- the level that must never be filtered away ----------------------------


def test_platform_notices_survive_every_narrowing():
    """They belong to no tenant, so no tenant scope may exclude them."""
    for user in (
        actor(["superadmin"]),
        actor(["superadmin"], org="org-A"),
        actor(["superadmin"], org="org-A", project="proj-A1"),
        actor(["orgadmin"], org="org-A", orgs=["org-A"]),
        actor(["projectuser"], org="org-A", projects=["proj-A1"]),
    ):
        assert PLATFORM <= visible(EffectiveScope.resolve(user)), (
            "a platform notice was hidden by a tenant filter"
        )


def test_the_naive_project_equality_filter_would_have_failed_this():
    """Guards the specific mistake this design exists to avoid.

    A plain ``project_id == selected`` predicate drops every record whose
    project is null, which is the whole organisation-level tier.
    """
    scope = EffectiveScope.resolve(actor(["superadmin"], org="org-A", project="proj-A1"))
    naive = {n["_id"] for n in NOTIFICATIONS if n.get("project_id") == "proj-A1"}
    ownership = visible(scope)

    assert "org-A-wide" not in naive
    assert "org-A-wide" in ownership
    assert naive < ownership


# --- tenant isolation ------------------------------------------------------


@pytest.mark.parametrize(
    "label,user,forbidden",
    [
        ("orgadmin A", actor(["orgadmin"], org="org-A", orgs=["org-A"]), ORG_B_ALL),
        ("orgadmin B", actor(["orgadmin"], org="org-B", orgs=["org-B"]), ORG_A_ALL),
        (
            "projectuser A/A1",
            actor(["projectuser"], org="org-A", projects=["proj-A1"]),
            {"org-A-A2"} | ORG_B_ALL,
        ),
        (
            "superuser {A,B} working in B",
            actor(["superuser"], org="org-B", orgs=["org-A", "org-B"]),
            ORG_A_ALL,
        ),
    ],
)
def test_no_notification_crosses_the_effective_boundary(label, user, forbidden):
    seen = visible(EffectiveScope.resolve(user))
    assert seen & forbidden == set(), f"{label}: leaked {sorted(seen & forbidden)}"


def test_a_project_user_sees_their_organisations_wide_notices():
    """Bounded to one project, but still part of the organisation.

    Denying organisation-level notices to project-tier users would silently cut
    them out of tenant-wide announcements.
    """
    scope = EffectiveScope.resolve(actor(["projectuser"], org="org-A", projects=["proj-A1"]))
    seen = visible(scope)

    assert "org-A-wide" in seen
    assert "org-A-A1" in seen
    assert "org-A-A2" not in seen


def test_a_caller_with_no_reach_sees_nothing():
    scope = EffectiveScope.resolve(actor(["orgadmin"]))
    assert visible(scope) == set()
