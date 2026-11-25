"""C-25: arbitration cases are found again, over real HTTP, MongoDB and RBAC.

``ArbitrationCase.deleted_at`` is stored as null. Before the fix (and on
production's 34d664a) the case queries asked for ``{"deleted_at": {"$exists":
False}}``, so a case made by ``POST /api/arbitration/cases`` answered 404 to its
own creator and was missing from every list. The in-memory test double ignores
``$exists``, so no unit test could see it.

Owner decision 4 (2026-11-16) asks for the corrected behaviour to be verified
against a real MongoDB for create, list, get, update, soft-delete, tenant
isolation and case-driven drafting. Each test below goes through the
production ``app`` with no dependency override (``_env`` asserts that).

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set); run in CI's
real-Mongo step.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict

import pytest

from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env, _never_leaks
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, PROJ_A1, PROJ_A2

pytestmark = pytest.mark.integration

CASES = "/api/arbitration/cases"
DRAFTS = "/api/arbitration/drafts"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _create(env: Any, persona: str = "org_admin", project: str = PROJ_A1, title: str = "ABC Infra v. Authority") -> Dict[str, Any]:
    response = await env.call(
        "POST", persona, CASES, project=project,
        json={"organization_id": ORG_A, "project_id": project, "title": title},
    )
    assert response.status_code == 201, response.text[:300]
    return response.json()


def _ids(response: Any) -> list:
    assert response.status_code == 200, response.text[:300]
    return [item.get("_id") or item.get("id") for item in response.json()]


def test_a_created_case_is_found_listed_and_updated_by_its_creator(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            case = await _create(env)
            case_id = case.get("_id") or case.get("id")
            stored = await env.db.arbitration_cases.find_one({"_id": case_id})
            # The stored shape that the pre-fix query could not match.
            assert "deleted_at" in stored and stored["deleted_at"] is None

            got = await env.call("GET", "org_admin", f"{CASES}/{case_id}")
            assert got.status_code == 200, got.text[:300]
            assert case_id in _ids(await env.call("GET", "org_admin", f"{CASES}?project_id={PROJ_A1}"))
            assert case_id in _ids(await env.call("GET", "org_admin", CASES))

            patched = await env.call("PATCH", "org_admin", f"{CASES}/{case_id}", json={"title": "Renamed"})
            assert patched.status_code == 200, patched.text[:300]
            assert patched.json()["title"] == "Renamed"
            assert (await env.db.arbitration_cases.find_one({"_id": case_id}))["title"] == "Renamed"

    _run(scenario())


def test_a_soft_deleted_case_stays_hidden_and_cannot_be_updated(monkeypatch) -> None:
    async def scenario() -> None:
        from rbac_backend.services.arbitration_drafting.case_workspace import ArbitrationCaseWorkspaceService

        async with _env(monkeypatch) as env:
            case_id = (await _create(env))["_id"]
            await ArbitrationCaseWorkspaceService(env.db).soft_delete_case(case_id, {"_id": "user-org_admin"})
            assert (await env.db.arbitration_cases.find_one({"_id": case_id}))["deleted_at"] is not None

            assert (await env.call("GET", "org_admin", f"{CASES}/{case_id}")).status_code == 404
            assert case_id not in _ids(await env.call("GET", "org_admin", CASES))
            patched = await env.call("PATCH", "org_admin", f"{CASES}/{case_id}", json={"title": "Back"})
            assert patched.status_code == 404
            assert (await env.db.arbitration_cases.find_one({"_id": case_id}))["title"] != "Back"

    _run(scenario())


def test_cases_stored_in_every_deleted_at_shape_are_read_correctly(monkeypatch) -> None:
    """Rows written before the model set deleted_at (field absent) stay visible;
    stored null is visible; a set value is deleted."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            base = {"organization_id": ORG_A, "project_id": PROJ_A1, "status": "intake",
                    "party_perspective": "claimant", "created_at": datetime(2026, 10, 1), "updated_at": datetime(2026, 10, 1)}
            await env.db.arbitration_cases.insert_many(
                [
                    {"_id": "case-legacy", "title": "Legacy", **base},
                    {"_id": "case-null", "title": "Null", "deleted_at": None, **base},
                    {"_id": "case-gone", "title": "Gone", "deleted_at": datetime(2026, 10, 2), **base},
                ]
            )
            listed = set(_ids(await env.call("GET", "org_admin", CASES)))
            assert {"case-legacy", "case-null"} <= listed and "case-gone" not in listed
            assert (await env.call("GET", "org_admin", f"{CASES}/case-legacy")).status_code == 200
            assert (await env.call("GET", "org_admin", f"{CASES}/case-null")).status_code == 200
            assert (await env.call("GET", "org_admin", f"{CASES}/case-gone")).status_code == 404

    _run(scenario())


def test_another_tenant_or_project_never_sees_the_case(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            case_id = (await _create(env))["_id"]

            # Another organisation: refused, absent from its lists, cannot rename it.
            _never_leaks(await env.call("GET", "foreign_org_admin", f"{CASES}/{case_id}"))
            foreign_list = await env.call("GET", "foreign_org_admin", CASES)
            if foreign_list.status_code == 200:
                assert case_id not in _ids(foreign_list)
            else:
                _never_leaks(foreign_list)
            _never_leaks(await env.call("PATCH", "foreign_org_admin", f"{CASES}/{case_id}", json={"title": "X"}))

            # Another project in the same organisation.
            _never_leaks(await env.call("GET", "other_project_admin", f"{CASES}/{case_id}", project=PROJ_A2))
            other_list = await env.call("GET", "other_project_admin", CASES, project=PROJ_A2)
            if other_list.status_code == 200:
                assert case_id not in _ids(other_list)
            else:
                _never_leaks(other_list)

            assert (await env.db.arbitration_cases.find_one({"_id": case_id}))["title"] == "ABC Infra v. Authority"

    _run(scenario())


def test_a_draft_is_created_and_prepared_from_a_case_and_not_from_a_deleted_one(monkeypatch) -> None:
    async def scenario() -> None:
        from rbac_backend.services.arbitration_drafting.case_workspace import ArbitrationCaseWorkspaceService

        async with _env(monkeypatch) as env:
            case_id = (await _create(env))["_id"]
            created = await env.call(
                "POST", "org_admin", DRAFTS,
                json={"case_id": case_id, "organization_id": ORG_A, "project_id": PROJ_A1,
                      "draft_type": "statement_of_claim", "party_role": "claimant", "title": "SoC"},
            )
            assert created.status_code == 201, created.text[:300]
            draft_id = created.json().get("_id") or created.json().get("id")

            prepared = await env.call("POST", "org_admin", f"{DRAFTS}/{draft_id}/prepare-from-case")
            assert prepared.status_code == 200, prepared.text[:300]
            assert prepared.json()["case_id"] == case_id

            # Another tenant can neither read nor prepare it.
            _never_leaks(await env.call("POST", "foreign_org_admin", f"{DRAFTS}/{draft_id}/prepare-from-case"))

            await ArbitrationCaseWorkspaceService(env.db).soft_delete_case(case_id, {"_id": "user-org_admin"})
            after = await env.call("POST", "org_admin", f"{DRAFTS}/{draft_id}/prepare-from-case")
            assert after.status_code == 404, after.text[:300]

    _run(scenario())


def test_the_pages_unfiltered_lists_answer_tenant_users(monkeypatch) -> None:
    """C-26: the case and drafts pages list with no query parameters. The subscription check
    was given no organisation and refused every tenant user (403 "No active subscription");
    only Super Admin, who skips that check, saw a list. Same code on 34d664a."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            case_id = (await _create(env))["_id"]
            created = await env.call(
                "POST", "org_admin", DRAFTS,
                json={"case_id": case_id, "organization_id": ORG_A, "project_id": PROJ_A1,
                      "draft_type": "statement_of_claim", "party_role": "claimant", "title": "SoC"},
            )
            assert created.status_code == 201, created.text[:300]
            draft_id = created.json().get("_id") or created.json().get("id")

            for persona in ("org_admin", "project_admin"):
                assert case_id in _ids(await env.call("GET", persona, CASES)), persona
                assert case_id in _ids(await env.call("GET", persona, f"{CASES}?project_id={PROJ_A1}")), persona
                assert draft_id in _ids(await env.call("GET", persona, DRAFTS)), persona

            # The fallback is the user's own organisation: it widens nothing.
            assert (await env.call("GET", "project_user", CASES)).status_code == 403
            for path in (CASES, DRAFTS):
                foreign = await env.call("GET", "foreign_org_admin", path)
                if foreign.status_code == 200:
                    assert case_id not in _ids(foreign) and draft_id not in _ids(foreign)
                else:
                    _never_leaks(foreign)
            other = await env.call("GET", "other_project_admin", CASES, project=PROJ_A2)
            if other.status_code == 200:
                assert case_id not in _ids(other)
            else:
                _never_leaks(other)

    _run(scenario())
