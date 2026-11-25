"""4B-22 (D-16, AQ-4): who may make an overlap's days carry cost, over real HTTP, MongoDB and RBAC.

An overlap decided as not true concurrency carries time. Its days carry cost only once
a cost basis (contractual basis, causation, cost evidence linked to the claim) is
recorded and then reviewed by a different person who holds the claim's determination
authority by name. Org admin, project admin and Super Admin hold no such authority
(4B-10), so they cannot review; a named holder can. The quantum lock follows.

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set); run in CI's real-Mongo step.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from rbac_backend.tests.integration.test_claim_privileged_rbac_mongo import CUSTOM_ROLES, _add_actor, _superadmin_headers
from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import CLAIM, _env
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import DOC_IN_ID, ORG_A, PROJ_A1
from rbac_backend.tests.test_claim_delay_quantum import _hindrance

pytestmark = pytest.mark.integration

BASE = f"/api/claims/{CLAIM}/bench/heads/H1/delay"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _version(env: Any) -> int:
    return int((await env.db.claims.find_one({"_id": CLAIM}))["bench"]["board_version"])


async def _ok(response: Any) -> dict:
    assert response.status_code == 200, (response.status_code, response.text[:400])
    return response.json()


async def _prepared(env: Any) -> str:
    """A head with E1 and C1 critical and overlapping, the overlap decided not true concurrency,
    and a cost basis recorded by the org admin citing a letter linked to the claim."""
    for role_id, permissions in CUSTOM_ROLES.items():
        await _add_actor(env.db, role_id, role_id, permissions)
    await env.db.delay_events.insert_many(
        [_hindrance("E1", "2026-03-01", "2026-03-31", organization_id=ORG_A, project_id=PROJ_A1),
         _hindrance("C1", "2026-03-20", "2026-04-10", "contractor", organization_id=ORG_A, project_id=PROJ_A1)]
    )
    await _ok(await env.call("POST", "org_admin", f"/api/claims/{CLAIM}/bench/open"))
    linked = await env.call(
        "POST", "org_admin", f"/api/entities/claim/{CLAIM}/document-links:batch",
        json={"links": [{"document_id": DOC_IN_ID, "relationship_role": "supporting_document"}]},
    )
    assert linked.status_code in (200, 201), linked.text[:300]
    for event_id, owner in (("E1", "employer"), ("C1", "contractor")):
        await _ok(await env.call("POST", "org_admin", f"{BASE}/links",
                                 json={"board_version": await _version(env), "target_type": "hindrance", "target_id": event_id}))
        await _ok(await env.call("PUT", "org_admin", f"{BASE}/events/{event_id}",
                                 json={"board_version": await _version(env), "risk_owner": owner, "criticality": "critical"}))
    view = await _ok(await env.call("GET", "org_admin", BASE))
    (overlap,) = view["overlaps"]
    overlap_id = overlap["overlap_id"]
    await _ok(await env.call("PUT", "org_admin", f"{BASE}/overlaps/{overlap_id}",
                             json={"board_version": await _version(env), "treatment": "not_true_concurrency",
                                   "basis": "The Contractor delay was not on the critical path in fact."}))
    recorded = await _ok(await env.call(
        "PUT", "org_admin", f"{BASE}/overlaps/{overlap_id}/cost-basis",
        json={"board_version": await _version(env), "contractual_basis": "Sub-Clause 8.4 with Sub-Clause 8.5 cost.",
              "causation": "Plant stood idle only for want of access.", "evidence_document_ids": [DOC_IN_ID]},
    ))
    assert not recorded["overlaps"][0]["cost_established"]
    return overlap_id


def test_only_a_named_determination_authority_other_than_the_recorder_can_review(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.projects.update_one({"_id": PROJ_A1}, {"$set": {"contractual_capacity": "contractor"}})
            overlap_id = await _prepared(env)
            review = f"{BASE}/overlaps/{overlap_id}/cost-basis/review"
            for persona in ("org_admin", "project_admin", "dms_admin_only", "wildcard_role"):
                response = await env.call("POST", persona, review, json={"board_version": await _version(env)})
                assert response.status_code == 403, (persona, response.status_code, response.text[:200])
            super_admin = await env.client.post(review, json={"board_version": await _version(env)},
                                                headers=_superadmin_headers(env))
            assert super_admin.status_code == 403, super_admin.text[:200]
            stored = (await env.db.claims.find_one({"_id": CLAIM}))["bench"]["heads"][0]["delay"]["findings"][overlap_id]
            assert stored["cost_basis"]["reviewed_by"] is None

            reviewed = await _ok(await env.call("POST", "claims_counsel", review, json={"board_version": await _version(env)}))
            assert reviewed["overlaps"][0]["cost_established"]
            assert sum(period["days"] for period in reviewed["compensable_periods"]) == 31

    _run(scenario())


def test_the_recorder_cannot_review_even_with_the_authority(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.projects.update_one({"_id": PROJ_A1}, {"$set": {"contractual_capacity": "contractor"}})
            overlap_id = await _prepared(env)
            # The named holder (who can also read the letter) records a fresh basis, then tries to review it.
            await env.db.roles.update_one({"_id": "claims_counsel"}, {"$addToSet": {"permissions": "dms.document.view"}})
            await _ok(await env.call(
                "PUT", "claims_counsel", f"{BASE}/overlaps/{overlap_id}/cost-basis",
                json={"board_version": await _version(env), "contractual_basis": "Sub-Clause 8.4 with Sub-Clause 8.5 cost.",
                      "causation": "Plant stood idle only for want of access.", "evidence_document_ids": [DOC_IN_ID]},
            ))
            response = await env.call("POST", "claims_counsel", f"{BASE}/overlaps/{overlap_id}/cost-basis/review",
                                      json={"board_version": await _version(env)})
            assert response.status_code == 403, response.text[:200]

    _run(scenario())
