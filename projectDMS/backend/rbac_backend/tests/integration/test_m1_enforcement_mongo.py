"""M1 remediation (security owner S-4 and owner direction, 2026-11-25): ``BUILT_IN_ROLE_ASSIGNMENTS=on``.

A tenant built-in role counts only in the user's primary organisation and where an explicit
``role_assignments`` row puts it. The pinned exposures M1-E1 to M1-E5
(``test_m1_cross_org_role_combinations_mongo.py``, which keeps asserting today's behaviour with
the flag off) are refused here with the flag on; the primary organisation keeps its meaning;
an explicit assignment restores a secondary reach; a cached grant never crosses a mode change.

Real HTTP through the production app on real MongoDB. Opt-in: ``CLAIM_BENCH_MONGODB_URI``.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

import pytest

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_m1_cross_org_role_combinations_mongo import MONETIZATION, _headers, _person
from rbac_backend.tests.integration.test_security_remediation_mongo import _role
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, ORG_B, PROJ_A1, PROJ_A2, PROJ_B1

pytestmark = pytest.mark.integration


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _mode(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    from rbac_backend.core.config import settings

    monkeypatch.setattr(settings, "BUILT_IN_ROLE_ASSIGNMENTS", mode, raising=False)


async def _get(env: Any, persona: str, path: str, **headers: str) -> int:
    response = await env.client.get(path, headers=_headers(persona, **headers))
    env.client.cookies.clear()
    return response.status_code


async def _effective(env: Any, persona: str) -> Dict[str, Any]:
    response = await env.client.get(f"{MONETIZATION}/plan-settings/effective-services", headers=_headers(persona))
    env.client.cookies.clear()
    assert response.status_code == 200, response.text[:200]
    return response.json()["effective"]


def _billing_state_shown(effective: Dict[str, Any], kind: str, key: str) -> bool:
    return "billing_status" in ((effective.get(kind) or {}).get(key) or {})


async def _assign(env: Any, user_id: str, role_key: str, *, organization_id: str = None, project_id: str = None) -> None:
    await env.db.role_assignments.insert_one(
        {"user_id": user_id, "role_key": role_key, "organization_id": organization_id, "project_id": project_id,
         "status": "active", "granted_by": "test", "granted_at": "2026-11-25"}
    )


def test_m1_e1_e2_e4_secondary_organisation_reach_is_refused_when_on(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _person(env, "m1on_e1", ["orgadmin"], primary=ORG_A, listed=[ORG_A, ORG_B])
            await _person(env, "m1on_e2", ["orgadmin"], primary=ORG_A, membership_rows=[ORG_B])
            await _person(env, "m1on_e4", ["contraclaim_billing_admin"], primary=ORG_A, listed=[ORG_A, ORG_B])
            _mode(monkeypatch, "on")
            for persona in ("m1on_e1", "m1on_e2", "m1on_e4"):
                assert await _get(env, persona, f"{MONETIZATION}/billing/records/{ORG_B}") == 403, persona
                assert await _get(env, persona, f"{MONETIZATION}/billing/review-queue/{ORG_B}") == 403, persona
                effective = await _effective(env, persona)
                assert not _billing_state_shown(effective, "organizations", ORG_B), persona
            for persona in ("m1on_e1", "m1on_e2"):
                assert await _get(env, persona, "/api/claims", **{"X-Org-Id": ORG_B, "X-Proj-Id": PROJ_B1}) == 403, persona

    _run(scenario())


def test_m1_e3_a_role_of_one_organisation_never_makes_the_user_organisation_wide_in_another(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _person(env, "m1on_e3", ["projectadmin", "orguser"], primary=ORG_B, listed=[ORG_A, ORG_B], projects=[PROJ_A1])
            _mode(monkeypatch, "on")
            assert await _get(env, "m1on_e3", "/api/claims", **{"X-Proj-Id": PROJ_A2}) == 403
            assert await _get(env, "m1on_e3", f"{MONETIZATION}/billing/records/{ORG_A}") == 403
            assert not _billing_state_shown(await _effective(env, "m1on_e3"), "projects", PROJ_A2)

    _run(scenario())


def test_m1_e5_a_built_in_role_of_a_does_not_follow_a_custom_role_into_b(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.roles.insert_one(_role("m1on-b-viewer", ["dms.document.view"], organization_id=ORG_B))
            await _person(env, "m1on_e5", ["orguser", "m1on-b-viewer"], primary=ORG_A, listed=[ORG_A, ORG_B])
            _mode(monkeypatch, "on")
            assert await _get(env, "m1on_e5", f"{MONETIZATION}/billing/records/{ORG_B}") == 403
            assert await _get(env, "m1on_e5", "/api/claims", **{"X-Org-Id": ORG_B, "X-Proj-Id": PROJ_B1}) == 403

    _run(scenario())


def test_the_primary_organisation_keeps_its_meaning_when_on(monkeypatch) -> None:
    """The same requests in the user's own organisation answer exactly as with the flag off."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _person(env, "m1on_dual", ["orgadmin"], primary=ORG_A, listed=[ORG_A, ORG_B])
            await _person(env, "m1on_single", ["orgadmin"], primary=ORG_A)
            await _person(env, "m1on_billing", ["contraclaim_billing_admin"], primary=ORG_A)
            await _person(env, "m1on_projadmin", ["projectadmin"], primary=ORG_A, projects=[PROJ_A1])
            requests = [
                ("m1on_dual", f"{MONETIZATION}/billing/records/{ORG_A}", {}),
                ("m1on_dual", "/api/claims", {"X-Org-Id": ORG_A, "X-Proj-Id": PROJ_A1}),
                ("m1on_single", f"{MONETIZATION}/billing/records/{ORG_A}", {}),
                ("m1on_single", "/api/claims", {"X-Proj-Id": PROJ_A2}),
                ("m1on_billing", f"{MONETIZATION}/billing/records/{ORG_A}", {}),
                ("m1on_projadmin", "/api/claims", {"X-Proj-Id": PROJ_A1}),
                ("m1on_projadmin", "/api/claims", {"X-Proj-Id": PROJ_A2}),
            ]
            _mode(monkeypatch, "off")
            off = [await _get(env, persona, path, **headers) for persona, path, headers in requests]
            off_effective = {p: await _effective(env, p) for p in ("m1on_dual", "m1on_single", "m1on_projadmin")}
            _mode(monkeypatch, "on")
            on = [await _get(env, persona, path, **headers) for persona, path, headers in requests]
            on_effective = {p: await _effective(env, p) for p in ("m1on_dual", "m1on_single", "m1on_projadmin")}
            assert on == off, list(zip(requests, off, on))
            assert off[:6] == [200, 200, 200, 200, 200, 200] and off[6] == 403  # sanity: the rows above mean something
            for persona in ("m1on_single", "m1on_projadmin"):
                assert on_effective[persona] == off_effective[persona], persona
            assert on_effective["m1on_dual"]["organizations"][ORG_A] == off_effective["m1on_dual"]["organizations"][ORG_A]

    _run(scenario())


def test_an_explicit_assignment_restores_exactly_the_reach_it_names(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            org_row = await _person(env, "m1on_org_row", ["orgadmin"], primary=ORG_A, listed=[ORG_A, ORG_B])
            await _assign(env, org_row, "orgadmin", organization_id=ORG_B)
            proj_row = await _person(env, "m1on_proj_row", ["projectadmin"], primary=ORG_A, listed=[ORG_A, ORG_B], projects=[PROJ_A1, PROJ_B1])
            await _assign(env, proj_row, "projectadmin", project_id=PROJ_B1)
            # A project row never makes an organisation-tier key count elsewhere.
            wrong = await _person(env, "m1on_wrong_row", ["orguser"], primary=ORG_A, listed=[ORG_A, ORG_B])
            await _assign(env, wrong, "orguser", project_id=PROJ_B1)
            # An inactive row counts for nothing.
            revoked = await _person(env, "m1on_revoked", ["orgadmin"], primary=ORG_A, listed=[ORG_A, ORG_B])
            await env.db.role_assignments.insert_one({"user_id": revoked, "role_key": "orgadmin", "organization_id": ORG_B, "status": "revoked"})
            _mode(monkeypatch, "on")
            assert await _get(env, "m1on_org_row", f"{MONETIZATION}/billing/records/{ORG_B}") == 200
            assert await _get(env, "m1on_org_row", "/api/claims", **{"X-Org-Id": ORG_B, "X-Proj-Id": PROJ_B1}) == 200
            assert await _get(env, "m1on_proj_row", "/api/claims", **{"X-Proj-Id": PROJ_B1}) == 200
            assert await _get(env, "m1on_proj_row", f"{MONETIZATION}/billing/records/{ORG_B}") == 403
            assert await _get(env, "m1on_wrong_row", f"{MONETIZATION}/billing/records/{ORG_B}") == 403
            assert await _get(env, "m1on_wrong_row", "/api/claims", **{"X-Org-Id": ORG_B, "X-Proj-Id": PROJ_B1}) == 403
            assert await _get(env, "m1on_revoked", f"{MONETIZATION}/billing/records/{ORG_B}") == 403

    _run(scenario())


def test_a_tenant_built_in_role_with_no_primary_organisation_grants_nothing_when_on(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services.permission_service import PermissionService

            user_id = await _person(env, "m1on_orphan", ["orgadmin"], primary=None, listed=[ORG_A])
            _mode(monkeypatch, "on")
            assert await _get(env, "m1on_orphan", f"{MONETIZATION}/billing/records/{ORG_A}") == 403
            assert not await PermissionService().user_has_permission(user_id, "users:read", log=False)

    _run(scenario())


def test_super_admin_and_single_organisation_scope_checks_are_unchanged_when_on(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services.scope_service import ScopeService

            await _person(env, "m1on_super", ["superadmin"], primary=None)
            _mode(monkeypatch, "on")
            assert await _get(env, "m1on_super", f"{MONETIZATION}/billing/records/{ORG_B}") == 200

            class _User:
                def __init__(self, user_id: str, roles: list, primary: str, orgs: list) -> None:
                    self.id, self.roles, self.organization_id, self.organizations, self.projects = user_id, roles, primary, orgs, []

            scope = ScopeService(env.db)
            dual = _User("m1on-scope-dual", ["orgadmin"], ORG_A, [ORG_A, ORG_B])
            assert await scope.has_organization_wide_scope(dual, organization_id=ORG_A)
            assert not await scope.has_organization_wide_scope(dual, organization_id=ORG_B)
            assert await scope.is_client_scope_allowed(dual, organization_id=ORG_A, project_id=PROJ_A2)
            assert not await scope.is_client_scope_allowed(dual, organization_id=ORG_B, project_id=PROJ_B1)
            # Membership itself is unchanged: B is still a member organisation.
            assert await scope.is_client_scope_allowed(dual, organization_id=ORG_B)
            _mode(monkeypatch, "off")
            assert await scope.has_organization_wide_scope(dual, organization_id=ORG_B)  # today's M1 reach, flag off

    _run(scenario())


def test_a_cached_grant_never_crosses_a_mode_change(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services.permission_service import PermissionService

            user_id = await _person(env, "m1on_cache", ["orgadmin"], primary=ORG_A, listed=[ORG_A, ORG_B])
            service = PermissionService()
            _mode(monkeypatch, "off")
            assert await service.user_has_permission(user_id, "organizations:read", organization_id=ORG_B, log=False)
            _mode(monkeypatch, "on")
            assert not await service.user_has_permission(user_id, "organizations:read", organization_id=ORG_B, log=False)
            assert await service.user_has_permission(user_id, "organizations:read", organization_id=ORG_A, log=False)
            _mode(monkeypatch, "off")
            assert await service.user_has_permission(user_id, "organizations:read", organization_id=ORG_B, log=False)

    _run(scenario())


def test_on_writes_no_role_user_membership_or_assignment(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _person(env, "m1on_quiet", ["orgadmin"], primary=ORG_A, listed=[ORG_A, ORG_B])
            names = ("users", "roles", "organization_memberships", "project_memberships", "role_assignments")
            before = {name: await env.db[name].find({}).sort("_id", 1).to_list(length=None) for name in names}
            _mode(monkeypatch, "on")
            await _get(env, "m1on_quiet", f"{MONETIZATION}/billing/records/{ORG_B}")
            await _get(env, "m1on_quiet", "/api/claims", **{"X-Org-Id": ORG_A, "X-Proj-Id": PROJ_A1})
            await _effective(env, "m1on_quiet")
            after = {name: await env.db[name].find({}).sort("_id", 1).to_list(length=None) for name in names}
            assert after == before

    _run(scenario())


class _Principal:
    def __init__(self, user_id: str, roles: list, primary: Any, orgs: list, projects: list = None) -> None:
        self.id, self.roles, self.organization_id, self.organizations = user_id, roles, primary, orgs
        self.projects = projects or []


def test_tier_checks_outside_the_policy_gate_are_bounded_to_the_assignment_when_on(monkeypatch) -> None:
    """AuthorizationService tiers, role visibility and search: the other §3 sites."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.routers.search import _organisation_wide_orgs
            from rbac_backend.services.authorization_service import AuthorizationError, AuthorizationService
            from rbac_backend.services.role_service import RoleService
            from rbac_backend.services.scope_service import ScopeService

            dual = _Principal("m1on-tier-dual", ["orgadmin"], ORG_A, [ORG_A, ORG_B])
            custom_only = _Principal("m1on-tier-custom", ["m1on-custom-b"], ORG_A, [ORG_A, ORG_B])
            authz = AuthorizationService(env.db)
            b_role = {"_id": "m1on-b-role", "name": "B role", "scope": "organization", "organization_id": ORG_B}

            _mode(monkeypatch, "off")
            await authz.check_organization_access(dual, ORG_B, "admin")  # today's M1 reach
            assert await RoleService().can_view_role(dual, b_role)
            assert ORG_B in await _organisation_wide_orgs(ScopeService(env.db), dual, {ORG_A, ORG_B})

            _mode(monkeypatch, "on")
            with pytest.raises(AuthorizationError):
                await authz.check_organization_access(dual, ORG_B, "admin")
            await authz.check_organization_access(dual, ORG_A, "admin")
            assert not await RoleService().can_view_role(dual, b_role)
            assert await _organisation_wide_orgs(ScopeService(env.db), dual, {ORG_A, ORG_B}) == {ORG_A}
            # A user with custom roles only is not reasoned about by tier: membership unchanged.
            assert authz._collect_user_org_ids(custom_only) == {ORG_A, ORG_B}

    _run(scenario())


def test_a_bare_reviewer_key_answers_arbitration_gates_only_where_assigned_when_on(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services.reviewer_assignments import arbitration_reviewer_roles

            reviewer = _Principal("m1on-reviewer", ["legal_reviewer"], ORG_A, [ORG_A, ORG_B])
            _mode(monkeypatch, "off")
            assert "legal_reviewer" in await arbitration_reviewer_roles(env.db, reviewer, organization_id=ORG_B, project_id=PROJ_B1)
            _mode(monkeypatch, "on")
            assert "legal_reviewer" in await arbitration_reviewer_roles(env.db, reviewer, organization_id=ORG_A, project_id=PROJ_A1)
            assert "legal_reviewer" not in await arbitration_reviewer_roles(env.db, reviewer, organization_id=ORG_B, project_id=PROJ_B1)
            assert "legal_reviewer" not in await arbitration_reviewer_roles(env.db, reviewer, organization_id=None, project_id=PROJ_B1)
            await _assign(env, "m1on-reviewer", "legal_reviewer", organization_id=ORG_B)
            assert "legal_reviewer" in await arbitration_reviewer_roles(env.db, reviewer, organization_id=ORG_B, project_id=PROJ_B1)

    _run(scenario())


def test_checkout_scope_and_the_perspective_default_need_the_role_assigned_there_when_on(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from fastapi import HTTPException

            from rbac_backend.routers.rbac_monetization import CheckoutRequest, _authorize_checkout_scope
            from rbac_backend.services.policy_service import PolicyService
            from rbac_backend.services.project_perspective import ProjectPerspectiveService

            user_id = await _person(env, "m1on_checkout", ["orgadmin"], primary=ORG_A, listed=[ORG_A, ORG_B])
            admin = _Principal(user_id, ["orgadmin"], ORG_A, [ORG_A, ORG_B])
            policy = PolicyService()
            for_b = CheckoutRequest(organization_id=ORG_B, plan_code="dms_enterprise")
            for_a = CheckoutRequest(organization_id=ORG_A, plan_code="dms_enterprise")
            perspective = ProjectPerspectiveService(env.db)

            _mode(monkeypatch, "off")
            await _authorize_checkout_scope(for_b, admin, policy)  # today's M1 reach
            assert await perspective._is_organisation_admin(admin, ORG_B)

            _mode(monkeypatch, "on")
            with pytest.raises(HTTPException) as refused:
                await _authorize_checkout_scope(for_b, admin, policy)
            assert refused.value.status_code == 403
            await _authorize_checkout_scope(for_a, admin, policy)
            assert not await perspective._is_organisation_admin(admin, ORG_B)
            assert await perspective._is_organisation_admin(admin, ORG_A)

    _run(scenario())
