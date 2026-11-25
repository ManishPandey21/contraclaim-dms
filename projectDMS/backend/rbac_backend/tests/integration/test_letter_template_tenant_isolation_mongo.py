"""Letter templates: tenant isolation through the real API (owner direction 2026-11-25, item 2).

Found while auditing M1: ``LetterTemplateService._resolve_scope`` takes the organisation (and
project) of a new template from the request body for an Organization Admin or a project-tier
user, and the create route checked only the ``letter_templates:create`` permission - never the
target organisation. Read, update and delete already run ``check_letter_template_access``.

Each case runs through the production app on real MongoDB. Opt-in: ``CLAIM_BENCH_MONGODB_URI``.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

import pytest

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_m1_cross_org_role_combinations_mongo import _headers, _person
from rbac_backend.tests.integration.test_security_remediation_mongo import _role
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, ORG_B, PROJ_A1, PROJ_B1

pytestmark = pytest.mark.integration

TEMPLATES = "/api/letter-templates"
#: No built-in role carries the template permissions; a tenant grants them with a custom role of
#: its own organisation (bounded to that organisation by SEC-1).
TEMPLATE_PERMISSIONS = ["letter_templates:read", "letter_templates:create", "letter_templates:update", "letter_templates:delete"]


async def _template_role(env: Any, role_id: str, organization_id: str) -> str:
    await env.db.roles.insert_one(_role(role_id, TEMPLATE_PERMISSIONS, organization_id=organization_id))
    return role_id


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _body(code: str, **scope: Any) -> Dict[str, Any]:
    return {"name": f"Template {code}", "code": code, "category": "Contract Management", **scope}


async def _call(env: Any, method: str, persona: str, path: str, json: Any = None) -> Any:
    response = await env.client.request(method, path, headers=_headers(persona), json=json)
    env.client.cookies.clear()
    return response


async def _seed_template(env: Any, code: str, organization_id: str, project_id: Any = None) -> str:
    """B's template, created through the API by a Super Admin."""
    scope = {"organization_id": organization_id, "visibility": "project" if project_id else "organization"}
    if project_id:
        scope["project_id"] = project_id
    response = await _call(env, "POST", "lt_super", TEMPLATES, _body(code, **scope))
    assert response.status_code in (200, 201), response.text[:300]
    return str(response.json().get("_id") or response.json().get("id"))


def test_an_org_admin_of_a_cannot_create_read_change_or_delete_templates_of_b(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _person(env, "lt_super", ["superadmin"], primary=None)
            await _person(env, "lt_admin_a", ["orgadmin", await _template_role(env, "lt-a-templates", ORG_A)], primary=ORG_A)
            seeded = {"lt-b-org": await _seed_template(env, "lt-b-org", ORG_B),
                      "lt-b-proj": await _seed_template(env, "lt-b-proj", ORG_B, PROJ_B1)}

            # Control: the same administrator can create in its own organisation.
            own = await _call(env, "POST", "lt_admin_a", TEMPLATES, _body("LT-A-OWN", organization_id=ORG_A))
            assert own.status_code in (200, 201), own.text[:300]
            assert own.json().get("organization_id") == ORG_A

            for code, scope in (
                ("LT-B-ORG", {"organization_id": ORG_B}),
                ("LT-B-VIS", {"organization_id": ORG_B, "visibility": "organization"}),
                ("LT-B-PROJ", {"organization_id": ORG_B, "project_id": PROJ_B1}),
                ("LT-A-B1", {"organization_id": ORG_A, "project_id": PROJ_B1}),  # B's project under A's name
            ):
                response = await _call(env, "POST", "lt_admin_a", TEMPLATES, _body(code, **scope))
                assert response.status_code == 403, (code, response.status_code, response.text[:300])
                assert await env.db.letter_templates.count_documents({"code": code}) == 0, code

            for code, template_id in seeded.items():
                # Control: the template exists and a Super Admin reads it, so the 403 below is a refusal.
                assert (await _call(env, "GET", "lt_super", f"{TEMPLATES}/{template_id}")).status_code == 200
                assert (await _call(env, "GET", "lt_admin_a", f"{TEMPLATES}/{template_id}")).status_code == 403
                update = await _call(env, "PUT", "lt_admin_a", f"{TEMPLATES}/{template_id}", {"name": "changed"})
                assert update.status_code == 403
                assert (await _call(env, "DELETE", "lt_admin_a", f"{TEMPLATES}/{template_id}")).status_code == 403
                stored = await env.db.letter_templates.find_one({"code": code})
                assert stored is not None and stored["name"] == f"Template {code}" and stored.get("is_active") is not False

            listed = await _call(env, "GET", "lt_admin_a", TEMPLATES)
            assert listed.status_code == 200
            assert not [t for t in listed.json()["templates"] if t.get("organization_id") == ORG_B]
            filtered = await _call(env, "GET", "lt_admin_a", f"{TEMPLATES}?organization_id={ORG_B}")
            assert filtered.status_code == 403

    _run(scenario())


def test_a_project_admin_cannot_create_templates_outside_its_projects_or_under_another_organisation(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _person(env, "lt_padmin_a1", ["projectadmin", await _template_role(env, "lt-a-templates-p", ORG_A)],
                          primary=ORG_A, projects=[PROJ_A1])
            own = await _call(env, "POST", "lt_padmin_a1", TEMPLATES, _body("LT-P-OWN", project_id=PROJ_A1))
            assert own.status_code in (200, 201), own.text[:300]
            for code, scope in (
                ("LT-P-B1", {"project_id": PROJ_B1}),
                ("LT-P-A1-AS-B", {"organization_id": ORG_B, "project_id": PROJ_A1}),
                ("LT-P-ORG-B", {"organization_id": ORG_B}),
            ):
                response = await _call(env, "POST", "lt_padmin_a1", TEMPLATES, _body(code, **scope))
                assert response.status_code in (403, 422), (code, response.status_code, response.text[:300])
                assert await env.db.letter_templates.count_documents({"code": code}) == 0, code

    _run(scenario())
