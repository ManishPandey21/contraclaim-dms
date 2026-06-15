"""RBAC role x scope matrix (Week 2.2).

Exhaustively asserts the scope rules encoded by the pure functions in
``core/security.py``:

- ``authorize_scope`` — raises 403 on an out-of-scope (org, project) request
- ``build_scope_query`` — produces a Mongo filter that can never return
  out-of-scope rows

These are the deterministic source of truth for tenant scoping; every list and
mutation endpoint relies on them.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from rbac_backend.core.security import authorize_scope, build_scope_query


def _user(org=None, projects=(), roles=("orguser",), organizations=None):
    return SimpleNamespace(
        id="user-1",
        roles=list(roles),
        organization_id=org,
        organizations=list(organizations) if organizations is not None else ([org] if org else []),
        projects=list(projects),
        account_type="client_user",
    )


# (roles, user_org, user_projects, req_org, req_project, expected_allow)
AUTHORIZE_SCOPE_MATRIX = [
    # superadmin — global
    (["superadmin"], None, [], "org-Z", "proj-Z", True),
    (["superadmin"], None, [], None, None, True),
    # orgadmin — bound to its org, all projects within it
    (["orgadmin"], "org-A", [], "org-A", "proj-A", True),
    (["orgadmin"], "org-A", [], "org-A", None, True),
    (["orgadmin"], "org-A", [], "org-B", "proj-A", False),
    (["orgadmin"], "org-A", [], "org-B", None, False),
    # orguser — bound to its org
    (["orguser"], "org-A", [], "org-A", "proj-A", True),
    (["orguser"], "org-A", [], "org-B", None, False),
    # orguser with explicit project assignment is restricted to it
    (["orguser"], "org-A", ["proj-A"], "org-A", "proj-B", False),
    (["orguser"], "org-A", ["proj-A"], "org-A", "proj-A", True),
    # projectadmin — project required and must be assigned
    (["projectadmin"], "org-A", ["proj-A"], "org-A", "proj-A", True),
    (["projectadmin"], "org-A", ["proj-A"], "org-A", "proj-B", False),
    (["projectadmin"], "org-A", ["proj-A"], "org-A", None, False),
    (["projectadmin"], "org-A", ["proj-A"], "org-B", "proj-A", False),
    # projectuser — project required and must be assigned
    (["projectuser"], "org-A", ["proj-A"], "org-A", "proj-A", True),
    (["projectuser"], "org-A", ["proj-A"], "org-A", "proj-B", False),
    (["projectuser"], "org-A", ["proj-A"], "org-A", None, False),
    # external expert — assigned project, cross-org allowed
    (["contract_expert"], None, ["proj-A"], None, "proj-A", True),
    (["contract_expert"], None, ["proj-A"], None, "proj-B", False),
    (["contract_expert"], None, ["proj-A"], None, None, False),
    # unknown role — default deny
    (["randomrole"], "org-A", ["proj-A"], "org-A", "proj-A", False),
    (["orguser"], None, [], "org-A", None, False),  # org-scoped user with no org
]


@pytest.mark.parametrize(
    "roles,user_org,user_projects,req_org,req_project,allowed", AUTHORIZE_SCOPE_MATRIX
)
def test_authorize_scope_matrix(roles, user_org, user_projects, req_org, req_project, allowed):
    user = _user(org=user_org, projects=user_projects, roles=roles)
    if allowed:
        authorize_scope(user, organization_id=req_org, project_id=req_project)
    else:
        with pytest.raises(HTTPException) as exc:
            authorize_scope(user, organization_id=req_org, project_id=req_project)
        assert exc.value.status_code == 403


def test_authorize_scope_superuser_with_no_orgs_is_denied():
    """A multi-org 'superuser' with no granted organizations must be denied
    (deny-by-default), consistent with build_scope_query's _deny_all behaviour.
    """
    user = _user(org=None, projects=[], roles=["superuser"], organizations=[])
    with pytest.raises(HTTPException) as exc:
        authorize_scope(user, organization_id="org-Z", project_id="proj-Z")
    assert exc.value.status_code == 403


def test_authorize_scope_superuser_restricted_to_granted_orgs():
    user = _user(org=None, projects=["proj-A"], roles=["superuser"], organizations=["org-A"])
    authorize_scope(user, organization_id="org-A", project_id="proj-A")  # allowed
    with pytest.raises(HTTPException):
        authorize_scope(user, organization_id="org-B", project_id="proj-A")  # cross-org denied


# build_scope_query parity for the same roles

def test_build_scope_query_superuser_no_orgs_denies_all():
    user = _user(org=None, projects=[], roles=["superuser"], organizations=[])
    assert build_scope_query(user) == {"_id": {"$in": []}}


def test_build_scope_query_expert_restricts_to_projects():
    user = _user(org=None, projects=["proj-A"], roles=["contract_expert"])
    q = build_scope_query(user)
    assert "project_id" in q
    assert "proj-A" in q["project_id"]["$in"]
