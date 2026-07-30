"""Tenant scoping of the letter list query.

``build_letter_query`` used to copy the client's organization_id/project_id
into the Mongo filter verbatim. That had two consequences:

* omitting both produced a query with no tenant predicate, so the listing
  returned letters from every organisation; and
* supplying another organisation's id returned that organisation's letters.

These tests pin the contract: the client's ids are a narrowing request, and the
caller's real scope decides what is returned.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rbac_backend.services.authorization_service import AuthorizationService


DENY_ALL = {"_id": {"$in": []}}


def _service() -> AuthorizationService:
    return AuthorizationService()


def superadmin():
    return SimpleNamespace(id="u1", roles=["superadmin"], organization_id=None, organizations=[], projects=[])


def org_user(org="org-A"):
    return SimpleNamespace(id="u2", roles=["orguser"], organization_id=org, organizations=[org], projects=[])


def project_user(org="org-A", projects=("proj-A1",)):
    return SimpleNamespace(
        id="u3", roles=["projectuser"], organization_id=org, organizations=[org], projects=list(projects)
    )


@pytest.mark.asyncio
async def test_unfiltered_request_is_still_tenant_bound():
    """The regression: no filters must not mean no tenant predicate."""
    query = await _service().build_letter_query(org_user(), {})
    assert query.get("organization_id") == "org-A"


@pytest.mark.asyncio
async def test_org_user_cannot_request_another_organisation():
    query = await _service().build_letter_query(org_user(), {"organization_id": "org-B"})
    assert query == DENY_ALL


@pytest.mark.asyncio
async def test_org_user_may_narrow_within_own_organisation():
    query = await _service().build_letter_query(org_user(), {"organization_id": "org-A"})
    assert query.get("organization_id") == "org-A"
    assert query != DENY_ALL


@pytest.mark.asyncio
async def test_project_user_is_bound_to_assigned_projects_without_filters():
    query = await _service().build_letter_query(project_user(), {})
    assert query.get("organization_id") == "org-A"
    assert "proj-A1" in str(query.get("project_id"))


@pytest.mark.asyncio
async def test_project_user_cannot_request_unassigned_project():
    query = await _service().build_letter_query(
        project_user(projects=("proj-A1",)), {"project_id": "proj-A2"}
    )
    assert query == DENY_ALL


@pytest.mark.asyncio
async def test_project_user_cannot_request_another_organisation():
    query = await _service().build_letter_query(project_user(), {"organization_id": "org-B"})
    assert query == DENY_ALL


@pytest.mark.asyncio
async def test_superadmin_may_target_any_organisation():
    query = await _service().build_letter_query(superadmin(), {"organization_id": "org-B"})
    assert query.get("organization_id") == "org-B"


@pytest.mark.asyncio
async def test_status_filter_is_preserved_alongside_scope():
    query = await _service().build_letter_query(org_user(), {"status": "draft"})
    assert query.get("status") == "draft"
    assert query.get("organization_id") == "org-A"


@pytest.mark.asyncio
async def test_contract_drafter_is_additionally_limited_to_assignments():
    drafter = SimpleNamespace(
        id="u9",
        roles=["contraclaim_expert_drafter"],
        organization_id="org-A",
        organizations=["org-A"],
        projects=["proj-A1"],
    )
    query = await _service().build_letter_query(drafter, {})
    assert query.get("assigned_to") == "u9"
    # Scope must still apply on top of the assignment narrowing.
    assert query != {"assigned_to": "u9"}
