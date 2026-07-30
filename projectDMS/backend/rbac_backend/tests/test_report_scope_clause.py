"""Tenant scoping of report data.

``ReportService._scope_clause`` had two defects:

1. It combined the organisation and project constraints with ``$or``. A
   project-scoped user therefore matched every row in their organisation via
   the organisation clause alone, so reports ignored project restriction --
   contradicting "a Project User cannot access another Project".
2. It returned ``None`` (no filter at all) when the caller had neither an
   organisation nor a project, exposing every tenant's report data.
"""

from __future__ import annotations

from types import SimpleNamespace

from rbac_backend.services.report_service import ReportService


DENY_ALL = {"_id": {"$in": []}}


def _clause(user):
    return ReportService._scope_clause(user, "organization_id", "project_id")


def superadmin():
    return SimpleNamespace(id="u1", roles=["superadmin"], organization_id=None, organizations=[], projects=[])


def org_user(org="org-A", projects=()):
    return SimpleNamespace(
        id="u2", roles=["orguser"], organization_id=org, organizations=[org], projects=list(projects)
    )


def project_user(org="org-A", projects=("proj-A1",)):
    return SimpleNamespace(
        id="u3", roles=["projectuser"], organization_id=org, organizations=[org], projects=list(projects)
    )


def superuser(orgs=("org-A", "org-B")):
    return SimpleNamespace(
        id="u4", roles=["superuser"], organization_id=orgs[0], organizations=list(orgs), projects=[]
    )


def orgless_role():
    return SimpleNamespace(id="u5", roles=["reporter"], organization_id=None, organizations=[], projects=[])


# --- the OR-widening defect ------------------------------------------------


def test_project_user_is_constrained_by_organisation_and_project():
    clause = _clause(project_user())
    assert "$and" in clause, "organisation and project must both apply"
    assert {"organization_id": "org-A"} in clause["$and"]
    assert {"project_id": "proj-A1"} in clause["$and"]


def test_project_user_clause_is_not_satisfiable_by_organisation_alone():
    """The regression: an org-only match must not be enough."""
    clause = _clause(project_user())
    assert "$or" not in clause


def test_project_user_with_several_assignments_uses_in():
    clause = _clause(project_user(projects=("proj-A1", "proj-A2")))
    assert {"project_id": {"$in": ["proj-A1", "proj-A2"]}} in clause["$and"]


# --- the fail-open defect --------------------------------------------------


def test_caller_without_any_scope_sees_nothing():
    assert _clause(orgless_role()) == DENY_ALL


# --- unchanged behaviour ---------------------------------------------------


def test_superadmin_is_unrestricted():
    assert _clause(superadmin()) is None


def test_org_user_is_scoped_to_its_organisation_only():
    assert _clause(org_user()) == {"organization_id": "org-A"}


def test_org_user_is_not_narrowed_by_incidental_project_links():
    """An organisation role still sees the whole organisation."""
    assert _clause(org_user(projects=("proj-A1",))) == {"organization_id": "org-A"}


def test_superuser_is_scoped_to_assigned_organisations():
    assert _clause(superuser()) == {"organization_id": {"$in": ["org-A", "org-B"]}}
