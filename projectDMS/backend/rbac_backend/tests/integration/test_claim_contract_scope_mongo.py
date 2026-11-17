"""4B-23 (D-23, AQ-1, C-05): no record of another contract supports a claim.
Real HTTP, real MongoDB replica set, real RBAC.

The plan's proof: a cross-contract link is refused with 422. Checked here at
every place a record reaches a claim - head links (hindrance, EOT submission,
EOT determination), re-pinning a link, document and letter links, quantum
records, cost evidence and escalation - and for a claim with no contract
identified, which may use only records that carry none.

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set); run in CI's
real-Mongo step.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import pytest
from bson import ObjectId

from rbac_backend.migrations.v20261120_0001_arbitration_matrix_origin_unique import upgrade as apply_m05
from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import CLAIM, _env
from rbac_backend.tests.integration.test_claim_signals_mongo import _hindrance
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import DOC_IN, DOC_IN_ID, DOC_OUT, DOC_OUT_ID, ORG_A, PROJ_A1
from rbac_backend.utils.rate_limiter import RateLimiter

pytestmark = pytest.mark.integration

BENCH = f"/api/claims/{CLAIM}/bench"
LINKS = f"{BENCH}/heads/H1/delay/links"
LINES = f"{BENCH}/heads/H1/quantum/lines"
DOC_LINKS = f"/api/entities/claim/{CLAIM}/document-links:batch"
PREVIEW = "/api/claims/escalation/preview"
RUN = "/api/claims/escalation/run"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _code(response: Any) -> str:
    detail = response.json().get("detail")
    return detail.get("code", "") if isinstance(detail, dict) else ""


async def _version(env: Any) -> int:
    return int((await env.db.claims.find_one({"_id": CLAIM}))["bench"]["board_version"])


async def _open(env: Any, contract: Any) -> None:
    RateLimiter._shared_user_requests.clear()
    RateLimiter._shared_buckets.clear()
    await env.db.claims.update_one({"_id": CLAIM}, {"$set": {"contract_id": contract}})
    opened = await env.call("POST", "org_admin", f"{BENCH}/open")
    assert opened.status_code == 200, opened.text[:300]


def _submission(_id: str, contract: str) -> dict:
    return {
        "_id": _id, "organization_id": ORG_A, "project_id": PROJ_A1, "contract_id": contract,
        "eot_reference": f"EOT-{_id}", "revision_label": "R0", "status": "submitted",
        "contractor_submission_date": datetime(2026, 9, 15),
    }


async def _link(env: Any, target_type: str, target_id: str) -> Any:
    return await env.call(
        "POST", "org_admin", LINKS,
        json={"board_version": await _version(env), "target_type": target_type, "target_id": target_id},
    )


async def _active_links(env: Any) -> int:
    return await env.db.claim_head_links.count_documents({"claim_id": CLAIM, "status": "active"})


def test_a_head_link_to_another_contracts_record_is_refused(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _open(env, "primary")
            await env.db.delay_events.insert_many(
                [_hindrance("HX", contract_id="pkg-depot"), _hindrance("HP", contract_id="primary"), _hindrance("HN")]
            )
            await env.db.key_date_eot_submissions.insert_many([_submission("SX", "pkg-depot"), _submission("SP", "primary")])

            for target_type, target_id in (("hindrance", "HX"), ("eot_submission", "SX")):
                refused = await _link(env, target_type, target_id)
                assert refused.status_code == 422, (target_id, refused.status_code, refused.text[:300])
                assert _code(refused) == "claim_contract_mismatch"
                assert "different contract" in refused.json()["detail"]["user_message"]
            assert await _active_links(env) == 0

            # The claim's own contract, and a record that names no contract, are linked as before.
            for target_type, target_id in (("hindrance", "HP"), ("hindrance", "HN"), ("eot_submission", "SP")):
                linked = await _link(env, target_type, target_id)
                assert linked.status_code == 200, (target_id, linked.text[:300])
            assert await _active_links(env) == 3

    _run(scenario())


def test_a_claim_with_no_contract_uses_only_records_that_carry_none(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _open(env, None)
            await env.db.delay_events.insert_many([_hindrance("HN"), _hindrance("HP", contract_id="primary")])
            await env.db.key_date_eot_submissions.insert_one(_submission("SP", "primary"))

            for target_type, target_id in (("eot_submission", "SP"), ("hindrance", "HP")):
                refused = await _link(env, target_type, target_id)
                assert refused.status_code == 422 and _code(refused) == "claim_contract_mismatch", refused.text[:300]
                assert "Set the claim's contract first" in refused.json()["detail"]["user_message"]
            assert (await _link(env, "hindrance", "HN")).status_code == 200

            # Once the claim is given its contract (4B-18), that contract's records link.
            await env.db.contract_master.insert_one(
                {"_id": "cm-primary", "organization_id": ORG_A, "project_id": PROJ_A1, "contract_id": "primary",
                 "contract_name": "Main works"}
            )
            given = await env.call("PUT", "org_admin", f"/api/claims/{CLAIM}", json={"contract_id": "primary"})
            assert given.status_code == 200, given.text[:300]
            assert (await _link(env, "eot_submission", "SP")).status_code == 200

    _run(scenario())


def test_re_pinning_a_link_reads_the_record_as_it_is_now(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _open(env, "primary")
            await env.db.delay_events.insert_one(_hindrance("HP", contract_id="primary"))
            linked = await _link(env, "hindrance", "HP")
            assert linked.status_code == 200, linked.text[:300]
            link_id = linked.json()["links"][0]["link_id"] if "links" in linked.json() else (
                (await env.db.claim_head_links.find_one({"claim_id": CLAIM}))["_id"]
            )
            # The hindrance is moved to another contract after it was linked.
            await env.db.delay_events.update_one({"_id": "HP"}, {"$set": {"contract_id": "pkg-depot"}})
            refreshed = await env.call(
                "POST", "org_admin", f"{LINKS}/{link_id}/remove",
                json={"board_version": await _version(env), "refresh": True},
            )
            assert refreshed.status_code == 422 and _code(refreshed) == "claim_contract_mismatch", refreshed.text[:300]
            # The old link is untouched: nothing is removed or rewritten by the refusal.
            assert await _active_links(env) == 1

    _run(scenario())


def test_a_document_or_letter_of_another_contract_is_not_linked_to_the_claim(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _open(env, "primary")
            await env.db.documents.update_one({"_id": DOC_IN}, {"$set": {"contract_id": "pkg-depot"}})
            refused = await env.call(
                "POST", "org_admin", DOC_LINKS,
                json={"links": [{"document_id": DOC_IN_ID, "relationship_role": "supporting_document"}]},
            )
            assert refused.status_code == 422, (refused.status_code, refused.text[:300])
            assert "different contract" in refused.text
            assert await env.db.entity_document_links.count_documents({"target_type": "claim", "target_id": CLAIM, "removed_at": None}) == 0

            # A document that names no contract, or the claim's own, is linked as before.
            await env.db.documents.update_one({"_id": DOC_OUT}, {"$set": {"contract_id": "primary"}})
            linked = await env.call(
                "POST", "org_admin", DOC_LINKS,
                json={"links": [{"document_id": DOC_OUT_ID, "relationship_role": "supporting_document"}]},
            )
            assert linked.status_code in (200, 201), linked.text[:300]
            assert await env.db.entity_document_links.count_documents(
                {"target_type": "claim", "target_id": CLAIM, "removed_at": None}
            ) == 1

    _run(scenario())


def test_quantum_and_escalation_refuse_evidence_that_became_another_contracts(monkeypatch) -> None:
    """A link made before the rule, or a record moved later, is caught where the evidence is relied on."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await apply_m05(env.db, False)
            await _open(env, "primary")
            linked = await env.call(
                "POST", "org_admin", DOC_LINKS,
                json={"links": [{"document_id": DOC_IN_ID, "relationship_role": "supporting_document"}]},
            )
            assert linked.status_code in (200, 201), linked.text[:300]
            await env.db.documents.update_one({"_id": DOC_IN}, {"$set": {"contract_id": "pkg-depot"}})

            line = {"category": "plant", "description": "Idle excavator", "amount": 125000.0,
                    "records": [{"document_id": DOC_IN_ID, "role": "support"}]}
            refused = await env.call("POST", "org_admin", LINES, json={"board_version": await _version(env), **line})
            assert refused.status_code == 422 and _code(refused) == "claim_contract_mismatch", refused.text[:300]
            head = (await env.db.claims.find_one({"_id": CLAIM}))["bench"]["heads"][0]
            assert head.get("quantum_lines") in (None, [])

            created = await env.call(
                "POST", "org_admin", "/api/arbitration/cases",
                json={"organization_id": ORG_A, "project_id": PROJ_A1, "title": "ABC v. Authority", "party_perspective": "claimant"},
            )
            assert created.status_code in (200, 201), created.text[:300]
            case_id = str(created.json().get("id") or created.json().get("_id"))
            preview = await env.call("POST", "org_admin", PREVIEW, json={"claim_ids": [CLAIM], "case_id": case_id})
            assert preview.status_code == 200, preview.text[:300]
            assert preview.json()["can_escalate"] is False
            assert any("different contract" in item for item in preview.json()["blockers"])
            run = await env.call(
                "POST", "org_admin", RUN, json={"claim_ids": [CLAIM], "case_id": case_id, "acknowledged_weak": [CLAIM]}
            )
            assert run.status_code == 422 and _code(run) == "claim_contract_mismatch", run.text[:300]
            claim = await env.db.claims.find_one({"_id": CLAIM})
            assert not claim.get("arbitration_case_id") and not claim.get("evidence_frozen_at")

    _run(scenario())


def test_unrelated_object_ids_are_never_treated_as_a_contract(monkeypatch) -> None:
    """A claim whose evidence carries no contract escalates exactly as before."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await apply_m05(env.db, False)
            await _open(env, "primary")
            await env.db.delay_events.insert_one(_hindrance(str(ObjectId())))
            created = await env.call(
                "POST", "org_admin", "/api/arbitration/cases",
                json={"organization_id": ORG_A, "project_id": PROJ_A1, "title": "ABC v. Authority", "party_perspective": "claimant"},
            )
            case_id = str(created.json().get("id") or created.json().get("_id"))
            preview = await env.call("POST", "org_admin", PREVIEW, json={"claim_ids": [CLAIM], "case_id": case_id})
            assert preview.status_code == 200, preview.text[:300]
            assert not any("contract" in item for item in preview.json()["blockers"]), preview.json()["blockers"]

    _run(scenario())


def test_a_submission_is_not_composed_from_another_contracts_evidence(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _open(env, "primary")
            linked = await env.call(
                "POST", "org_admin", DOC_LINKS,
                json={"links": [{"document_id": DOC_IN_ID, "relationship_role": "supporting_document"}]},
            )
            assert linked.status_code in (200, 201), linked.text[:300]
            await env.db.documents.update_one({"_id": DOC_IN}, {"$set": {"contract_id": "pkg-depot"}})
            # Reach the composer's own checks: a locked board on a Contractor-side project.
            await env.db.claims.update_one({"_id": CLAIM}, {"$set": {"bench.stage": "locked"}})
            composed = await env.call("POST", "org_admin", f"/api/claims/{CLAIM}/submission/compose", json={"sections": []})
            assert composed.status_code == 422 and _code(composed) == "claim_contract_mismatch", composed.text[:300]
            assert await env.db.claim_submissions.count_documents({"claim_id": CLAIM}) == 0

    _run(scenario())


def test_cost_evidence_of_another_contract_is_refused(monkeypatch) -> None:
    async def scenario() -> None:
        from rbac_backend.tests.integration.test_delay_cost_basis_mongo import BASE, _prepared

        async with _env(monkeypatch) as env:
            await env.db.claims.update_one({"_id": CLAIM}, {"$set": {"contract_id": "primary"}})
            overlap_id = await _prepared(env)  # records a basis citing DOC_IN, which names no contract
            await env.db.documents.update_one({"_id": DOC_IN}, {"$set": {"contract_id": "pkg-depot"}})
            refused = await env.call(
                "PUT", "org_admin", f"{BASE}/overlaps/{overlap_id}/cost-basis",
                json={"board_version": await _version(env), "contractual_basis": "Sub-Clause 8.4 with Sub-Clause 8.5 cost.",
                      "causation": "Plant stood idle only for want of access.", "evidence_document_ids": [DOC_IN_ID]},
            )
            assert refused.status_code == 422 and _code(refused) == "claim_contract_mismatch", refused.text[:300]

    _run(scenario())
