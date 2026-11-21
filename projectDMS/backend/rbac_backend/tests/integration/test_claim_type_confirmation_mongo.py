"""Claim-type confirmation (owner decision 2026-11-18). Real HTTP, real RBAC, real MongoDB.

A claim starts from three questions with a provisional type ("other"). Until a
person chooses the type, the notice period recorded for it is not the contract's
period for this claim. A change of type withdraws the notice decisions made under
the old type (kept in history), marks the heads' tests stale and records the
change with the notice assessment as it stood. Starting a claim is never blocked
by an unconfirmed type.

Opt-in: ``CLAIM_BENCH_MONGODB_URI``; run in CI's real-Mongo step.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

import pytest

from rbac_backend.tests.integration.test_claim_contract_identity_mongo import MAIN, _contracts
from rbac_backend.tests.integration.test_claim_privileged_rbac_mongo import _add_actor
from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import CLAIM, _env
from rbac_backend.tests.integration.test_notice_verification_mongo import SET, _payload, _rid, _seed
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, PROJ_A1, PROJ_B1

pytestmark = pytest.mark.integration

START = "/api/claims/start"
HAPPENED = "Access to the depot site was handed over eighteen days after the programme date."


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _verified_requirement(env: Any, claim_type: str) -> str:
    created = await env.call("POST", "org_admin", SET, json=_payload(claim_type=claim_type))
    assert created.status_code == 201, created.text[:300]
    requirement_id = _rid(created)
    verified = await env.call("POST", "project_admin", f"{SET}/{requirement_id}/verify")
    assert verified.status_code == 200, verified.text[:300]
    return requirement_id


async def _start(env: Any) -> Dict[str, Any]:
    started = await env.call("POST", "org_admin", START, json={
        "project_id": PROJ_A1, "organization_id": ORG_A, "what_happened": HAPPENED, "aware_on": "2026-03-20",
    })
    assert started.status_code == 201, started.text[:300]
    return started.json()["claim"]


async def _claim(env: Any, claim_id: str, persona: str = "org_admin") -> Dict[str, Any]:
    response = await env.call("GET", persona, f"/api/claims/{claim_id}")
    assert response.status_code == 200, response.text[:300]
    return response.json()


async def _confirm(env: Any, claim_id: str, claim_type: str, persona: str = "org_admin", **extra: Any) -> Any:
    return await env.call("POST", persona, f"/api/claims/{claim_id}/type", json={"claim_type": claim_type, **extra})


def test_a_started_claim_is_provisional_until_its_type_is_confirmed(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            await _contracts(env, MAIN)
            await _verified_requirement(env, "other")
            started = await _start(env)
            assert started["type"] == "other" and started["type_confirmed"] is False
            notice = (await _claim(env, started["_id"]))["notice_assessment"]
            # A verified requirement exists for "other", but nobody chose "other" for this claim.
            assert notice["period_source"] == "contract_unverified" and notice["period_verified"] is False
            assert notice["claim_type_confirmed"] is False

            confirmed = await _confirm(env, started["_id"], "other", reason="It is not a time or money claim.")
            assert confirmed.status_code == 200, confirmed.text[:300]
            body = confirmed.json()
            assert body["type_confirmed"] is True
            assert body["notice_assessment"]["period_source"] == "contract_verified"
            assert body["notice_assessment"]["period_verified"] is True
            [entry] = body["type_history"]
            assert (entry["from"], entry["to"], entry["changed"], entry["via"]) == ("other", "other", False, "confirm")
            assert entry["confirmed_by"] == "user-org_admin" and entry["reason"] == "It is not a time or money claim."
            assert entry["notice_before"]["period_source"] == "contract_unverified"
            audit = await env.db.audit_events.find_one({"action": "claim.type_confirmed", "resource_id": started["_id"]})
            assert audit and audit["actor_id"] == "user-org_admin"

    _run(scenario())


def test_choosing_another_type_uses_that_types_verified_period(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            await _contracts(env, MAIN)
            requirement_id = await _verified_requirement(env, "eot")
            started = await _start(env)
            assert (await _claim(env, started["_id"]))["notice_assessment"]["period_source"] != "contract_verified"
            changed = await _confirm(env, started["_id"], "eot")
            assert changed.status_code == 200, changed.text[:300]
            notice = changed.json()["notice_assessment"]
            assert notice["period_source"] == "contract_verified" and notice["requirement_id"] == requirement_id
            assert changed.json()["type_history"][-1]["changed"] is True
            assert (await env.db.audit_events.find_one({"action": "claim.type_changed", "resource_id": started["_id"]}))

    _run(scenario())


@pytest.mark.parametrize(
    "fields, confirmed",
    [({"type": "eot"}, True), ({"type": "other"}, False), ({}, False)],
)
def test_the_full_form_confirms_only_a_type_a_person_chose(monkeypatch, fields: Dict[str, Any], confirmed: bool) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _contracts(env, MAIN)
            created = await env.call("POST", "org_admin", "/api/claims", json={
                "title": "Late access", "project_id": PROJ_A1, "organization_id": ORG_A, "status": "draft", **fields,
            })
            assert created.status_code in (200, 201), created.text[:300]
            assert created.json()["type_confirmed"] is confirmed

    _run(scenario())


def test_a_type_change_withdraws_the_notice_decisions_made_under_the_old_type(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            await _contracts(env, MAIN)
            started = await _start(env)
            claim_id = started["_id"]
            board = (await env.call("GET", "org_admin", f"/api/claims/{claim_id}/bench")).json()
            decided = await env.call("PUT", "org_admin", f"/api/claims/{claim_id}/bench/heads/H1/elements/notice", json={
                "board_version": board["bench"]["board_version"], "state": "supported", "note": "Notice served in time.",
            })
            assert decided.status_code == 200, decided.text[:300]
            # The edit form sends the same type back: nothing is confirmed or withdrawn.
            same = await env.call("PUT", "org_admin", f"/api/claims/{claim_id}", json={"type": "other", "title": "Late access"})
            assert same.status_code == 200, same.text[:300]
            assert same.json()["type_confirmed"] is False and same.json()["type_history"] == []

            changed = await env.call("PUT", "org_admin", f"/api/claims/{claim_id}", json={"type": "variation"})
            assert changed.status_code == 200, changed.text[:300]
            body = changed.json()
            assert body["type"] == "variation" and body["type_confirmed"] is True
            assert [(e["from"], e["to"], e["via"]) for e in body["type_history"]] == [("other", "variation", "edit")]
            stored = await env.db.claims.find_one({"_id": claim_id})
            notice = stored["bench"]["heads"][0]["elements"]["notice"]
            assert notice["state"] is None and notice["decided_by"] is None
            [withdrawn] = notice["history"]
            assert withdrawn["state"] == "supported" and withdrawn["note"] == "Notice served in time."
            assert withdrawn["decided_by"] == "user-org_admin" and "from other to variation" in withdrawn["reason"]
            assert stored["bench"]["stage"] == "heads"

    _run(scenario())


def test_confirming_a_type_is_refused_where_editing_the_claim_is(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _add_actor(env.db, "claim_viewer", "role-claim-viewer", ["dms.claim.view"])
            viewer = await _confirm(env, CLAIM, "eot", persona="claim_viewer")
            assert viewer.status_code == 403, viewer.text[:300]
            foreign = await env.call("POST", "foreign_org_admin", f"/api/claims/{CLAIM}/type",
                                     json={"claim_type": "eot"}, project=PROJ_B1)
            assert foreign.status_code in (403, 404), foreign.text[:300]
            anonymous = await env.client.post(f"/api/claims/{CLAIM}/type", json={"claim_type": "eot"},
                                              headers={"X-Proj-Id": PROJ_A1})
            assert anonymous.status_code == 401
            for body in ({"claim_type": "made_up"}, {"claim_type": "eot", "type_confirmed": True}):
                assert (await env.call("POST", "org_admin", f"/api/claims/{CLAIM}/type", json=body)).status_code == 422
            await env.db.claims.update_one({"_id": CLAIM}, {"$set": {"evidence_frozen_at": "2026-11-18"}})
            frozen = await _confirm(env, CLAIM, "eot")
            assert frozen.status_code == 409, frozen.text[:300]
            assert (await env.db.claims.find_one({"_id": CLAIM})).get("type_history") in (None, [])

    _run(scenario())


def test_a_claim_stored_before_confirmation_existed_reads_as_confirmed(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            stored = await env.db.claims.find_one({"_id": CLAIM})
            assert "type_confirmed" not in stored
            body = await _claim(env, CLAIM)
            assert body["type_confirmed"] is None
            if body.get("notice_assessment"):
                assert body["notice_assessment"]["claim_type_confirmed"] is True

    _run(scenario())
