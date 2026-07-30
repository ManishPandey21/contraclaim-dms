"""Tenant scoping of the email-group list query.

``build_email_group_query`` validated a supplied organisation against the
caller's allow-list, but both of its constraint branches were guarded by
``if allowed_orgs``. An empty allow-list therefore skipped them entirely and
returned an unconstrained query -- every tenant's email groups -- rather than
denying. Roles such as reporter and doccontroller carry no organisation
invariant on the ``User`` model, so that state is reachable.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rbac_backend.services.authorization_service import (
    AuthorizationError,
    AuthorizationService,
)


DENY_ALL = {"_id": {"$in": []}}


def _service() -> AuthorizationService:
    return AuthorizationService()


def superadmin():
    return SimpleNamespace(id="u1", roles=["superadmin"], organization_id=None, organizations=[], projects=[])


def org_user(org="org-A"):
    return SimpleNamespace(id="u2", roles=["orguser"], organization_id=org, organizations=[org], projects=[])


def orgless_role():
    """A role with no organisation invariant -- the fail-open case."""
    return SimpleNamespace(id="u3", roles=["reporter"], organization_id=None, organizations=[], projects=[])


@pytest.mark.asyncio
async def test_user_without_any_organisation_sees_nothing():
    query = await _service().build_email_group_query(orgless_role(), {})
    assert query == DENY_ALL


@pytest.mark.asyncio
async def test_user_without_organisation_cannot_name_one():
    query = await _service().build_email_group_query(orgless_role(), {"organization_id": "org-A"})
    assert query == DENY_ALL


@pytest.mark.asyncio
async def test_org_user_is_constrained_without_filters():
    query = await _service().build_email_group_query(org_user(), {})
    assert query["organization_id"] == {"$in": ["org-A"]}


@pytest.mark.asyncio
async def test_org_user_may_narrow_to_own_organisation():
    query = await _service().build_email_group_query(org_user(), {"organization_id": "org-A"})
    assert query["organization_id"] == "org-A"


@pytest.mark.asyncio
async def test_org_user_denied_another_organisation():
    with pytest.raises(AuthorizationError):
        await _service().build_email_group_query(org_user(), {"organization_id": "org-B"})


@pytest.mark.asyncio
async def test_superadmin_remains_unrestricted():
    query = await _service().build_email_group_query(superadmin(), {})
    assert query == {}


@pytest.mark.asyncio
async def test_non_tenant_filters_survive_scoping():
    query = await _service().build_email_group_query(org_user(), {"name": "Site team"})
    assert query["name"] == "Site team"
    assert query["organization_id"] == {"$in": ["org-A"]}


# --- build_party_query shares the same fail-open shape --------------------


@pytest.mark.asyncio
async def test_party_query_denies_user_without_any_organisation():
    """An orgless non-superadmin previously received {} -- every tenant."""
    query = await _service().build_party_query(orgless_role(), {})
    assert query == DENY_ALL


@pytest.mark.asyncio
async def test_party_query_scopes_org_user():
    query = await _service().build_party_query(org_user(), {})
    assert query != DENY_ALL
    assert "org-A" in str(query)


@pytest.mark.asyncio
async def test_party_query_superadmin_unrestricted():
    query = await _service().build_party_query(superadmin(), {})
    assert query == {}
