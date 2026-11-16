"""4B-38 (C-20): attaching a pleading draft to a case validates the case. Real HTTP,
MongoDB and RBAC, through the production app.

Before 4B-38 ``update_draft`` set ``is_scratch = not case_id`` without loading the
case, and the router checked arbitration-edit on the draft only. A draft attached to
a missing case, a deleted one or another project's lost its scratch mark and was
held to no case gate.

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set); run in CI's
real-Mongo step.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

import pytest

from rbac_backend.tests.integration.test_arbitration_case_lookup_mongo import CASES, DRAFTS, _create
from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, ORG_B, PROJ_A1, PROJ_A2, PROJ_B1

pytestmark = pytest.mark.integration


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _id(body: Dict[str, Any]) -> str:
    return str(body.get("_id") or body.get("id"))


def _code(response: Any) -> str:
    detail = response.json().get("detail")
    return detail.get("code", "") if isinstance(detail, dict) else ""


async def _scratch(env: Any) -> str:
    created = await env.call(
        "POST",
        "org_admin",
        DRAFTS,
        json={"organization_id": ORG_A, "project_id": PROJ_A1, "draft_type": "statement_of_claim",
              "party_role": "claimant", "title": "Working draft", "scratch": True},
    )
    assert created.status_code == 201, created.text[:300]
    return _id(created.json())


async def _draft(env: Any, draft_id: str) -> Dict[str, Any]:
    return await env.db.arbitration_drafts.find_one({"_id": draft_id})


def test_attaching_to_a_missing_deleted_or_foreign_case_is_refused_and_the_scratch_mark_kept(monkeypatch) -> None:
    async def scenario() -> None:
        from rbac_backend.services.arbitration_drafting.case_workspace import ArbitrationCaseWorkspaceService

        async with _env(monkeypatch) as env:
            draft_id = await _scratch(env)
            other_project = _id(await _create(env, project=PROJ_A2, title="Other project"))
            deleted = _id(await _create(env, title="Deleted"))
            await ArbitrationCaseWorkspaceService(env.db).soft_delete_case(deleted, {"_id": "user-org_admin"})
            foreign = await env.call(
                "POST", "foreign_org_admin", CASES, project=PROJ_B1,
                json={"organization_id": ORG_B, "project_id": PROJ_B1, "title": "Foreign"},
            )
            assert foreign.status_code == 201, foreign.text[:300]

            for case_id, expected in (
                ("no-such-case", 404),
                (deleted, 404),
                (_id(foreign.json()), 404),  # another organisation's case is not even visible
                (other_project, 409),
            ):
                refused = await env.call("PATCH", "org_admin", f"{DRAFTS}/{draft_id}", json={"case_id": case_id})
                assert refused.status_code == expected, (case_id, refused.status_code, refused.text[:300])
                if expected == 409:
                    assert _code(refused) == "draft_case_scope_mismatch"
                stored = await _draft(env, draft_id)
                assert stored["is_scratch"] is True and not stored.get("case_id"), (case_id, stored.get("case_id"))

            # A case of the draft's own project is accepted, and the draft stops being scratch.
            own = _id(await _create(env, title="ABC Infra v. Authority"))
            attached = await env.call("PATCH", "org_admin", f"{DRAFTS}/{draft_id}", json={"case_id": own})
            assert attached.status_code == 200, attached.text[:300]
            stored = await _draft(env, draft_id)
            assert stored["case_id"] == own and stored["is_scratch"] is False

    _run(scenario())


def test_a_draft_cannot_be_created_in_a_missing_or_foreign_case(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            other_project = _id(await _create(env, project=PROJ_A2, title="Other project"))
            for case_id, expected in (("no-such-case", 404), (other_project, 409)):
                refused = await env.call(
                    "POST",
                    "org_admin",
                    DRAFTS,
                    json={"case_id": case_id, "organization_id": ORG_A, "project_id": PROJ_A1,
                          "draft_type": "statement_of_claim", "party_role": "claimant", "title": "SoC"},
                )
                assert refused.status_code == expected, (case_id, refused.text[:300])
            assert await env.db.arbitration_drafts.count_documents({}) == 0

    _run(scenario())


def test_a_project_admin_cannot_attach_to_a_case_outside_their_project(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            draft_id = await _scratch(env)
            other_project = _id(await _create(env, project=PROJ_A2, title="Other project"))
            refused = await env.call("PATCH", "project_admin", f"{DRAFTS}/{draft_id}", json={"case_id": other_project})
            assert refused.status_code in (403, 404), refused.text[:300]
            assert (await _draft(env, draft_id))["is_scratch"] is True

    _run(scenario())
