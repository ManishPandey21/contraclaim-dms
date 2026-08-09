"""Effective Data Scope = User Entitlement ∩ Validated Navbar Selection.

Entitlement is the maximum boundary; the navbar selection is the working
context inside it. A selection may only ever narrow.

Every case here failed before the EffectiveScope migration. The Document
Library carried its own tenant rules that exempted superadmin outright and never
read the validated selection, so selecting Organisation A still returned every
organisation's documents -- and Search carried the same defect independently.
The point of these tests is that the two can no longer disagree, because there
is only one implementation left to disagree with.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from rbac_backend.core.effective_scope import UNBOUNDED, EffectiveScope
from rbac_backend.core.security import build_scope_query
from rbac_backend.services.authorization_service import AuthorizationService
from rbac_backend.utils.error_handler import AuthorizationError


def actor(roles, org=None, project=None, orgs=(), projects=()):
    """A CurrentUser as get_current_user builds it *after* validating X-Org-Id.

    ``organization_id``/``project_id`` therefore hold the validated navbar
    selection, not raw client input.
    """
    return SimpleNamespace(
        id="u1",
        roles=list(roles),
        organization_id=org,
        project_id=project,
        organizations=list(orgs),
        projects=list(projects),
        account_type="client_user",
        disabled=False,
    )


def orgs_of(query):
    """Normalise the organisation predicate to a set of ids."""
    value = query.get("organization_id")
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(v) for v in value.get("$in", [])}
    return {str(value)}


def projects_of(query):
    value = query.get("project_id")
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(v) for v in value.get("$in", [])}
    return {str(value)}


def document_query(user, filters=None):
    """Drive the async builder from a sync test.

    ``asyncio.run`` rather than ``get_event_loop().run_until_complete``:
    conftest gives every coroutine test its own loop and closes it, so a shared
    loop here passes standalone and fails in the suite.
    """
    return asyncio.run(AuthorizationService().build_document_query(user, filters or {}))


# --- the superadmin bypass, which is the defect class ----------------------


def test_superadmin_selecting_an_organisation_is_not_platform_wide():
    """`if superadmin: don't scope` is the bug, not the feature.

    Platform-wide *entitlement* still narrows to the selected working context.
    """
    scope = EffectiveScope.resolve(actor(["superadmin"], org="org-A"))
    assert scope.is_platform_wide is False
    assert scope.effective_org_ids() == {"org-A"}
    assert orgs_of(scope.mongo_filter()) == {"org-A"}


def test_superadmin_with_nothing_selected_is_platform_wide():
    scope = EffectiveScope.resolve(actor(["superadmin"]))
    assert scope.is_platform_wide is True
    assert scope.effective_org_ids() is UNBOUNDED
    assert scope.mongo_filter() == {}


# --- the four leaks demonstrated before the migration ----------------------


@pytest.mark.parametrize(
    "label,user,expected_orgs,expected_projects",
    [
        ("superadmin selects Org-A", actor(["superadmin"], org="org-A"), {"org-A"}, None),
        (
            "superadmin selects Org-A + Proj-A1",
            actor(["superadmin"], org="org-A", project="proj-A1"),
            {"org-A"},
            {"proj-A1"},
        ),
        (
            "orgadmin selects Proj-A1",
            actor(["orgadmin"], org="org-A", project="proj-A1", orgs=["org-A"]),
            {"org-A"},
            {"proj-A1"},
        ),
        (
            "superuser of {A,B} selects B",
            actor(["superuser"], org="org-B", orgs=["org-A", "org-B"]),
            {"org-B"},
            None,
        ),
    ],
)
def test_document_library_honours_the_navbar_selection(label, user, expected_orgs, expected_projects):
    """Each row returned a wider set than this before the migration."""
    query = document_query(user)
    assert orgs_of(query) == expected_orgs, f"{label}: organisation scope wrong"
    assert projects_of(query) == expected_projects, f"{label}: project scope wrong"


@pytest.mark.parametrize(
    "user",
    [
        actor(["superadmin"], org="org-A"),
        actor(["superadmin"], org="org-A", project="proj-A1"),
        actor(["orgadmin"], org="org-A", project="proj-A1", orgs=["org-A"]),
        actor(["superuser"], org="org-B", orgs=["org-A", "org-B"]),
        actor(["projectuser"], org="org-A", projects=["proj-A1", "proj-A2"]),
    ],
)
def test_library_and_central_builder_select_the_same_records(user):
    """Consistency requirement: the same context must mean the same rows.

    Predicate *shape* may differ (a scalar and a one-element ``$in`` select
    identically); the selected id sets must not.
    """
    assert orgs_of(document_query(user)) == orgs_of(build_scope_query(user))
    assert projects_of(document_query(user)) == projects_of(build_scope_query(user))


# --- client input may narrow, never expand ---------------------------------


def test_a_requested_organisation_outside_entitlement_is_refused():
    """Fails closed with an error rather than an empty page: an empty list is
    indistinguishable from "this organisation has no documents"."""
    user = actor(["orgadmin"], org="org-A", orgs=["org-A"])
    with pytest.raises(AuthorizationError):
        document_query(user, {"organization_id": "org-B"})


def test_a_requested_project_outside_entitlement_is_refused():
    user = actor(["projectuser"], org="org-A", projects=["proj-A1"])
    with pytest.raises(AuthorizationError):
        document_query(user, {"project_id": "proj-A9"})


def test_requested_organisation_outside_entitlement_denies_rather_than_widening():
    """A superuser assigned {A,B} asking for C gets nothing -- not a silent
    fallback to {A,B}, which is how "narrowing" turns into "widening".

    Passed as an explicit request rather than as the actor's selection on
    purpose: for a global role the selection on the actor has already been
    validated against entitlement by TenantContextResolver, so an invalid one
    cannot reach this layer. That upstream half is covered by
    test_tenant_context.py; this is the query-side half.
    """
    user = actor(["superuser"], orgs=["org-A", "org-B"])
    scope = EffectiveScope.resolve(user, organization_id="org-C")
    assert scope.is_denied is True
    assert scope.mongo_filter() == {"_id": {"$in": []}}
    assert build_scope_query(user, organization_id="org-C") == {"_id": {"$in": []}}


def test_project_tier_cannot_reach_an_unassigned_project():
    scope = EffectiveScope.resolve(
        actor(["projectuser"], org="org-A", project="proj-B1", projects=["proj-A1"])
    )
    assert scope.is_denied is True


def test_an_account_with_no_reach_is_denied_not_unbounded():
    """Deny by default: absence of entitlement must never read as 'no filter'."""
    for user in (
        actor(["orgadmin"]),
        actor(["projectuser"], org="org-A"),
        actor(["reporter"]),
    ):
        scope = EffectiveScope.resolve(user)
        assert scope.is_denied is True
        assert scope.mongo_filter() == {"_id": {"$in": []}}


# --- entitlement questions are separable from the selection ----------------


def test_permits_distinguishes_not_yours_from_not_selected():
    scope = EffectiveScope.resolve(actor(["superuser"], org="org-A", orgs=["org-A", "org-B"]))
    assert scope.permits_organization("org-B") is True   # entitled, just not selected
    assert scope.permits_organization("org-Z") is False  # outside entitlement
    assert scope.effective_org_ids() == {"org-A"}        # selection still narrows


# --- tags: a visibility dimension on top of the tenant predicate -----------


def tag_query(user, filters=None):
    return asyncio.run(AuthorizationService().build_tag_query(user, filters or {}))


def _visibility(query, level):
    """The condition guarding one visibility level, or None if absent."""
    for condition in query.get("$or", []) or []:
        if condition.get("visibility") == level:
            return condition
    return None


def test_superadmin_tags_narrow_to_the_selected_organisation():
    """The superadmin early-return meant tags ignored the selection entirely."""
    query = tag_query(actor(["superadmin"], org="org-A"))

    org_tags = _visibility(query, "organization")
    assert orgs_of(org_tags) == {"org-A"}, "organisation tags must be bounded by the selection"

    project_tags = _visibility(query, "project")
    assert orgs_of(project_tags) == {"org-A"}, "project tags must be bounded by the selection"


def test_global_tags_stay_visible_in_every_scope():
    """Global tags belong to no tenant, so narrowing must not hide them."""
    for user in (
        actor(["superadmin"]),
        actor(["superadmin"], org="org-A"),
        actor(["orgadmin"], org="org-A", orgs=["org-A"]),
        actor(["projectuser"], org="org-A", projects=["proj-A1"]),
    ):
        assert _visibility(tag_query(user), "global") == {"visibility": "global"}


def test_superadmin_tags_unrestricted_when_nothing_is_selected():
    query = tag_query(actor(["superadmin"]))
    assert _visibility(query, "organization") == {"visibility": "organization"}
    assert _visibility(query, "project") == {"visibility": "project"}


def test_project_tier_tags_bounded_by_assignment():
    query = tag_query(actor(["projectuser"], org="org-A", projects=["proj-A1", "proj-A2"]))
    project_tags = _visibility(query, "project")
    assert {str(v) for v in project_tags["project_id"]["$in"]} == {"proj-A1", "proj-A2"}
    assert orgs_of(_visibility(query, "organization")) == {"org-A"}


def test_tag_request_outside_entitlement_is_refused():
    user = actor(["orgadmin"], org="org-A", orgs=["org-A"])
    with pytest.raises(AuthorizationError):
        tag_query(user, {"organization_id": "org-B"})


def test_experts_are_bounded_by_projects_across_organisations():
    """Experts are allocated across tenants by design, so an organisation
    selection does not apply to them -- but their projects still bound them."""
    scope = EffectiveScope.resolve(
        actor(["contraclaim_expert_drafter"], org="org-A", projects=["proj-A1", "proj-B1"])
    )
    assert scope.is_denied is False
    assert scope.effective_org_ids() is UNBOUNDED
    assert scope.effective_project_ids() == {"proj-A1", "proj-B1"}
