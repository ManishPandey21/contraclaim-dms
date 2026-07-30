"""Tenant scoping of the task list/board query.

Two defects are pinned here:

1. ``routers/tasks.py`` carried its own ``build_scope_query`` that handled five
   roles and default-denied everything else, so Super Users and the expert
   drafting roles were silently shown no tasks.
2. ``_apply_task_filters`` assigned the client's organization_id/project_id
   over the scope predicate that had just been computed, so a narrowing filter
   could widen the query instead. The route's ``policy.authorize`` call stopped
   that being exploitable, but the query builder must not depend on a separate
   check to be safe.
"""

from __future__ import annotations

from types import SimpleNamespace

from rbac_backend.routers.tasks import _apply_task_filters, build_scope_query


DENY_ALL = {"_id": {"$in": []}}


def superadmin():
    return SimpleNamespace(id="u1", roles=["superadmin"], organization_id=None, organizations=[], projects=[])


def superuser(orgs=("org-A", "org-B")):
    return SimpleNamespace(
        id="u2", roles=["superuser"], organization_id=orgs[0], organizations=list(orgs), projects=[]
    )


def org_user(org="org-A"):
    return SimpleNamespace(id="u3", roles=["orguser"], organization_id=org, organizations=[org], projects=[])


def project_user(org="org-A", projects=("proj-A1",)):
    return SimpleNamespace(
        id="u4", roles=["projectuser"], organization_id=org, organizations=[org], projects=list(projects)
    )


def expert():
    return SimpleNamespace(
        id="u5",
        roles=["contraclaim_expert_drafter"],
        organization_id="org-A",
        organizations=["org-A"],
        projects=["proj-A1"],
    )


# --- roles the previous local copy silently denied ------------------------


def test_superuser_is_scoped_not_denied():
    query = build_scope_query(superuser())
    assert query != DENY_ALL
    assert set(query["organization_id"]["$in"]) >= {"org-A", "org-B"}


def test_expert_role_is_scoped_not_denied():
    query = build_scope_query(expert())
    assert query != DENY_ALL


def test_unknown_role_still_denies():
    unknown = SimpleNamespace(id="u9", roles=["reporter"], organization_id=None, organizations=[], projects=[])
    assert build_scope_query(unknown) == DENY_ALL


# --- client ids narrow, never widen ---------------------------------------


def test_org_user_default_scope_is_own_organisation():
    assert build_scope_query(org_user())["organization_id"] == "org-A"


def test_org_user_cannot_widen_to_another_organisation():
    assert build_scope_query(org_user(), organization_id="org-B") == DENY_ALL


def test_project_user_cannot_widen_to_unassigned_project():
    assert build_scope_query(project_user(), project_id="proj-A2") == DENY_ALL


def test_superadmin_may_target_any_organisation():
    assert build_scope_query(superadmin(), organization_id="org-B")["organization_id"] == "org-B"


# --- the filter helper must not touch tenant fields -----------------------


def test_filters_do_not_overwrite_the_tenant_predicate():
    scoped = build_scope_query(org_user())
    result = _apply_task_filters(dict(scoped), status_filter="open", assigned_to="u3")
    assert result["organization_id"] == "org-A"
    assert result["status"] == "open"
    assert result["assigned_to"] == "u3"


def test_filter_helper_rejects_tenant_keyword_arguments():
    """Passing org/project here must be a TypeError, not a silent overwrite."""
    scoped = build_scope_query(org_user())
    try:
        _apply_task_filters(dict(scoped), organization_id="org-B")
    except TypeError:
        return
    raise AssertionError("_apply_task_filters must not accept organization_id")


def test_deny_all_survives_additional_filters():
    denied = build_scope_query(org_user(), organization_id="org-B")
    result = _apply_task_filters(dict(denied), status_filter="open")
    assert result["_id"] == {"$in": []}
