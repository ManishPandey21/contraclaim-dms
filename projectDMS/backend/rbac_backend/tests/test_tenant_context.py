"""Authorization matrix for the Active Organisation / Active Project context.

Covers each role tier against each access combination, plus the negative
cross-organisation and cross-project cases that prove a manipulated id cannot
bypass the guard.

Fixture world:
    org-A  -> proj-A1 (active), proj-A2 (active), proj-A3 (deactivated)
    org-B  -> proj-B1 (active)
    org-C  -> disabled organisation, proj-C1 (active)
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from rbac_backend.core.tenant_context import (
    CONTEXT_FORBIDDEN,
    SELECTION_REQUIRED,
    TenantContextError,
    TenantContextResolver,
    role_tier,
)


ORGANIZATIONS = {
    "org-A": {"_id": "org-A", "name": "Org A", "is_active": True},
    "org-B": {"_id": "org-B", "name": "Org B"},  # no flag -> treated as active
    "org-C": {"_id": "org-C", "name": "Org C", "is_active": False},
}

PROJECTS = {
    "proj-A1": {"_id": "proj-A1", "organization_id": "org-A", "is_active": True},
    "proj-A2": {"_id": "proj-A2", "organization_id": "org-A"},  # no flag -> active
    "proj-A3": {"_id": "proj-A3", "organization_id": "org-A", "is_active": False},
    "proj-B1": {"_id": "proj-B1", "organization_id": "org-B", "is_active": True},
    "proj-C1": {"_id": "proj-C1", "organization_id": "org-C", "is_active": True},
}


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    async def to_list(self, length=None):
        return list(self._docs)


class _MembershipColl:
    def __init__(self, docs=None):
        self._docs = docs or []

    def find(self, *_a, **_k):
        return _Cursor(self._docs)


class _RecordColl:
    """Honours the {_id: {$in: [...]}, is_active: {$ne: False}} shape."""

    def __init__(self, docs):
        self._docs = docs

    async def find_one(self, query):
        raw = (query or {}).get("_id")
        candidates = raw.get("$in", []) if isinstance(raw, dict) else [raw]
        wants_active = "is_active" in (query or {})
        for candidate in candidates:
            doc = self._docs.get(str(candidate))
            if not doc:
                continue
            if wants_active and doc.get("is_active") is False:
                return None
            return dict(doc)
        return None


class _DB:
    def __init__(self, org_memberships=None, project_memberships=None):
        self.organizations = _RecordColl(ORGANIZATIONS)
        self.projects = _RecordColl(PROJECTS)
        self.organization_memberships = _MembershipColl(org_memberships)
        self.project_memberships = _MembershipColl(project_memberships)


def _resolver(**kwargs) -> TenantContextResolver:
    return TenantContextResolver(_DB(**kwargs))


def superadmin():
    return SimpleNamespace(id="u-sa", roles=["superadmin"], organization_id=None, organizations=[], projects=[])


def superuser(orgs=("org-A", "org-B")):
    return SimpleNamespace(
        id="u-su", roles=["superuser"], organization_id=orgs[0], organizations=list(orgs), projects=[]
    )


def org_user(org="org-A", role="orguser"):
    return SimpleNamespace(id="u-org", roles=[role], organization_id=org, organizations=[org], projects=[])


def project_user(org="org-A", projects=("proj-A1",), role="projectuser"):
    return SimpleNamespace(
        id="u-proj", roles=[role], organization_id=org, organizations=[org], projects=list(projects)
    )


# --- role tier classification ---------------------------------------------


@pytest.mark.parametrize(
    "user,expected",
    [
        (superadmin(), "global"),
        (superuser(), "global"),
        (org_user(role="orgadmin"), "org"),
        (org_user(role="orguser"), "org"),
        (project_user(role="projectadmin"), "project"),
        (project_user(role="projectuser"), "project"),
        (SimpleNamespace(id="x", roles=["reporter"], organization_id=None, organizations=[], projects=[]), "restricted"),
    ],
)
def test_role_tier_classification(user, expected):
    assert role_tier(user) == expected


# --- global roles ----------------------------------------------------------


@pytest.mark.asyncio
async def test_superadmin_requires_explicit_organisation_selection():
    with pytest.raises(TenantContextError) as exc:
        await _resolver().resolve(superadmin(), organization_id=None, project_id=None, require_project=True)
    assert exc.value.status_code == 400
    assert exc.value.code == SELECTION_REQUIRED


@pytest.mark.asyncio
async def test_superadmin_requires_project_after_organisation():
    with pytest.raises(TenantContextError) as exc:
        await _resolver().resolve(superadmin(), organization_id="org-A", project_id=None, require_project=True)
    assert exc.value.code == SELECTION_REQUIRED


@pytest.mark.asyncio
async def test_superadmin_resolves_any_active_pair():
    ctx = await _resolver().resolve(
        superadmin(), organization_id="org-B", project_id="proj-B1", require_project=True
    )
    assert (ctx.organization_id, ctx.project_id) == ("org-B", "proj-B1")
    assert ctx.can_switch_organization and ctx.can_switch_project


@pytest.mark.asyncio
async def test_superuser_limited_to_assigned_organisations():
    with pytest.raises(TenantContextError) as exc:
        await _resolver().resolve(
            superuser(orgs=("org-A",)), organization_id="org-B", project_id="proj-B1", require_project=True
        )
    assert exc.value.status_code == 403
    assert exc.value.code == CONTEXT_FORBIDDEN


@pytest.mark.asyncio
async def test_superuser_resolves_within_assigned_organisation():
    ctx = await _resolver().resolve(
        superuser(), organization_id="org-B", project_id="proj-B1", require_project=True
    )
    assert ctx.organization_id == "org-B"


# --- organisation roles ----------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["orgadmin", "orguser"])
async def test_org_role_is_pinned_to_own_organisation(role):
    ctx = await _resolver().resolve(
        org_user(role=role), organization_id=None, project_id="proj-A1", require_project=True
    )
    assert ctx.organization_id == "org-A"
    assert ctx.can_switch_organization is False
    assert ctx.can_switch_project is True


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["orgadmin", "orguser"])
async def test_org_role_cannot_request_another_organisation(role):
    with pytest.raises(TenantContextError) as exc:
        await _resolver().resolve(
            org_user(role=role), organization_id="org-B", project_id="proj-B1", require_project=True
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_org_role_cannot_reach_project_in_another_organisation():
    """The manipulated-id case: own org declared, foreign project supplied."""
    with pytest.raises(TenantContextError) as exc:
        await _resolver().resolve(
            org_user(), organization_id="org-A", project_id="proj-B1", require_project=True
        )
    assert exc.value.status_code == 403
    assert "does not belong" in exc.value.detail["message"]


@pytest.mark.asyncio
async def test_org_role_must_select_project_when_several_exist():
    with pytest.raises(TenantContextError) as exc:
        await _resolver().resolve(org_user(), organization_id=None, project_id=None, require_project=True)
    assert exc.value.code == SELECTION_REQUIRED


# --- project roles ---------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["projectadmin", "projectuser"])
async def test_project_role_is_pinned_to_assignment(role):
    ctx = await _resolver().resolve(
        project_user(role=role), organization_id=None, project_id=None, require_project=True
    )
    assert (ctx.organization_id, ctx.project_id) == ("org-A", "proj-A1")
    assert ctx.can_switch_organization is False
    assert ctx.can_switch_project is False


@pytest.mark.asyncio
async def test_project_user_cannot_select_sibling_project_in_same_organisation():
    with pytest.raises(TenantContextError) as exc:
        await _resolver().resolve(
            project_user(projects=("proj-A1",)),
            organization_id="org-A",
            project_id="proj-A2",
            require_project=True,
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_project_user_cannot_cross_organisation():
    with pytest.raises(TenantContextError) as exc:
        await _resolver().resolve(
            project_user(), organization_id="org-B", project_id="proj-B1", require_project=True
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_project_user_without_assignment_is_denied():
    with pytest.raises(TenantContextError) as exc:
        await _resolver().resolve(
            project_user(projects=()), organization_id=None, project_id=None, require_project=True
        )
    assert exc.value.status_code == 403


# --- active-state enforcement ---------------------------------------------


@pytest.mark.asyncio
async def test_disabled_organisation_is_rejected():
    with pytest.raises(TenantContextError) as exc:
        await _resolver().resolve(
            superadmin(), organization_id="org-C", project_id="proj-C1", require_project=True
        )
    assert exc.value.status_code == 403
    assert "disabled" in exc.value.detail["message"]


@pytest.mark.asyncio
async def test_deactivated_project_is_rejected():
    with pytest.raises(TenantContextError) as exc:
        await _resolver().resolve(
            superadmin(), organization_id="org-A", project_id="proj-A3", require_project=True
        )
    assert exc.value.status_code == 403
    assert "deactivated" in exc.value.detail["message"]


@pytest.mark.asyncio
async def test_unknown_project_id_is_rejected():
    with pytest.raises(TenantContextError) as exc:
        await _resolver().resolve(
            superadmin(), organization_id="org-A", project_id="does-not-exist", require_project=True
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_missing_is_active_flag_is_treated_as_active():
    """Pre-migration rows must keep working."""
    ctx = await _resolver().resolve(
        superadmin(), organization_id="org-A", project_id="proj-A2", require_project=True
    )
    assert ctx.project_id == "proj-A2"


# --- membership-driven access ---------------------------------------------


@pytest.mark.asyncio
async def test_organisation_membership_grants_access():
    resolver = _resolver(org_memberships=[{"organization_id": "org-B", "status": "active"}])
    user = SimpleNamespace(id="u-m", roles=["superuser"], organization_id=None, organizations=[], projects=[])
    ctx = await resolver.resolve(user, organization_id="org-B", project_id="proj-B1", require_project=True)
    assert ctx.organization_id == "org-B"


@pytest.mark.asyncio
async def test_project_membership_grants_access_to_project_role():
    resolver = _resolver(project_memberships=[{"project_id": "proj-A2", "status": "active"}])
    user = project_user(projects=())
    ctx = await resolver.resolve(user, organization_id="org-A", project_id="proj-A2", require_project=True)
    assert ctx.project_id == "proj-A2"


# --- optional-project mode -------------------------------------------------


@pytest.mark.asyncio
async def test_require_project_false_allows_organisation_only_context():
    ctx = await _resolver().resolve(
        org_user(), organization_id=None, project_id=None, require_project=False
    )
    assert ctx.organization_id == "org-A"
    assert ctx.project_id is None
    assert ctx.is_complete is False


@pytest.mark.asyncio
async def test_audit_fields_expose_validated_ids():
    ctx = await _resolver().resolve(
        superadmin(), organization_id="org-A", project_id="proj-A1", require_project=True
    )
    assert ctx.audit_fields() == {"organization_id": "org-A", "project_id": "proj-A1"}
