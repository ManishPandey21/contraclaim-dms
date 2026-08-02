"""Progressive narrowing of the Organisation / Project data scope.

Contract:

* Organisation User -- no project selected means consolidated data across every
  project in their organisation; selecting a project narrows to it.
* Super Admin -- nothing selected means consolidated data across all
  organisations; selecting an organisation narrows to that organisation;
  additionally selecting a project narrows to that project.
* Super User -- the same, bounded by their assigned organisations.
* Project User -- always bound to their assignment; there is no consolidated
  view to widen into.

A selection must always *narrow*. Any combination that would widen the result
beyond the caller's entitlement is a defect.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rbac_backend.core.security import build_scope_query


DENY_ALL = {"_id": {"$in": []}}


def _user(roles, org=None, orgs=(), projects=()):
    return SimpleNamespace(
        roles=list(roles), organization_id=org, organizations=list(orgs), projects=list(projects)
    )


def org_user():
    return _user(["orguser"], org="org-A", orgs=["org-A"])


def org_admin():
    return _user(["orgadmin"], org="org-A", orgs=["org-A"])


def superadmin():
    return _user(["superadmin"])


def superuser():
    return _user(["superuser"], org="org-A", orgs=["org-A", "org-B"])


def project_user():
    return _user(["projectuser"], org="org-A", orgs=["org-A"], projects=["proj-A1"])


def _orgs_in(query):
    """Normalise the organisation predicate to a set for comparison."""
    value = query.get("organization_id")
    if value is None:
        return None
    if isinstance(value, dict):
        return {str(v) for v in value.get("$in", [])}
    return {str(value)}


# --- Organisation User / Admin --------------------------------------------


@pytest.mark.parametrize("user", [org_user(), org_admin()], ids=["orguser", "orgadmin"])
def test_org_role_consolidates_across_projects_when_none_selected(user):
    query = build_scope_query(user)
    assert _orgs_in(query) == {"org-A"}
    assert "project_id" not in query, "no project selected means all projects in the organisation"


@pytest.mark.parametrize("user", [org_user(), org_admin()], ids=["orguser", "orgadmin"])
def test_org_role_narrows_to_selected_project(user):
    query = build_scope_query(user, project_id="proj-A1")
    assert _orgs_in(query) == {"org-A"}
    assert "proj-A1" in str(query["project_id"])


def test_org_role_cannot_widen_to_another_organisation():
    assert build_scope_query(org_user(), organization_id="org-B") == DENY_ALL


# --- Super Admin -----------------------------------------------------------


def test_superadmin_consolidates_across_everything_when_nothing_selected():
    assert build_scope_query(superadmin()) == {}


def test_superadmin_narrows_to_selected_organisation():
    query = build_scope_query(superadmin(), organization_id="org-A")
    assert _orgs_in(query) == {"org-A"}
    assert "project_id" not in query


def test_superadmin_narrows_to_organisation_and_project():
    query = build_scope_query(superadmin(), organization_id="org-A", project_id="proj-A1")
    assert _orgs_in(query) == {"org-A"}
    assert "proj-A1" in str(query["project_id"])


# --- Super User ------------------------------------------------------------


def test_superuser_consolidates_across_assigned_organisations():
    assert _orgs_in(build_scope_query(superuser())) == {"org-A", "org-B"}


def test_superuser_narrows_to_the_selected_organisation():
    """Regression: the selection was validated but never applied."""
    query = build_scope_query(superuser(), organization_id="org-B")
    assert _orgs_in(query) == {"org-B"}, "selecting one organisation must exclude the others"


def test_superuser_narrows_to_organisation_and_project():
    query = build_scope_query(superuser(), organization_id="org-B", project_id="proj-B1")
    assert _orgs_in(query) == {"org-B"}
    assert "proj-B1" in str(query["project_id"])


def test_superuser_cannot_select_an_unassigned_organisation():
    assert build_scope_query(superuser(), organization_id="org-Z") == DENY_ALL


# --- Project User ----------------------------------------------------------


def test_project_user_has_no_consolidated_view():
    query = build_scope_query(project_user())
    assert _orgs_in(query) == {"org-A"}
    assert "proj-A1" in str(query["project_id"])


def test_project_user_cannot_reach_an_unassigned_project():
    assert build_scope_query(project_user(), project_id="proj-A2") == DENY_ALL


# --- clearing a selection widens only back to entitlement -----------------


def test_clearing_the_project_returns_to_organisation_scope():
    narrowed = build_scope_query(org_user(), project_id="proj-A1")
    cleared = build_scope_query(org_user())
    assert "project_id" in narrowed
    assert "project_id" not in cleared
    assert _orgs_in(cleared) == {"org-A"}


def test_clearing_the_organisation_returns_a_superuser_to_assigned_organisations():
    narrowed = build_scope_query(superuser(), organization_id="org-B")
    cleared = build_scope_query(superuser())
    assert _orgs_in(narrowed) == {"org-B"}
    assert _orgs_in(cleared) == {"org-A", "org-B"}
