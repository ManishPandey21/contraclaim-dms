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


# --------------------------------------------------------------------------- #
# Owner review 2026-11-17: contract identity is the Contract Master entry -
# project and key together - and evidence whose contract is not confirmed is
# reported, never refused or rewritten.
# --------------------------------------------------------------------------- #
async def _case(env: Any) -> str:
    created = await env.call(
        "POST", "org_admin", "/api/arbitration/cases",
        json={"organization_id": ORG_A, "project_id": PROJ_A1, "title": "ABC v. Authority", "party_perspective": "claimant"},
    )
    assert created.status_code in (200, 201), created.text[:300]
    return str(created.json().get("id") or created.json().get("_id"))


def test_the_same_contract_key_in_another_project_is_another_contract(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await apply_m05(env.db, False)
            await _open(env, "primary")
            await env.db.contract_master.insert_one(
                {"_id": "cm-primary", "organization_id": ORG_A, "project_id": PROJ_A1, "contract_id": "primary",
                 "contract_name": "Main works"}
            )
            await env.db.delay_events.insert_one(_hindrance("HP", contract_id="primary"))
            assert (await _link(env, "hindrance", "HP")).status_code == 200
            case_id = await _case(env)
            preview = await env.call("POST", "org_admin", PREVIEW, json={"claim_ids": [CLAIM], "case_id": case_id})
            assert not any("contract" in item for item in preview.json()["blockers"]), preview.json()["blockers"]

            # The hindrance now belongs to another project's main contract: same key, different contract.
            await env.db.delay_events.update_one({"_id": "HP"}, {"$set": {"project_id": "proj-a2"}})
            preview = await env.call("POST", "org_admin", PREVIEW, json={"claim_ids": [CLAIM], "case_id": case_id})
            assert preview.status_code == 200, preview.text[:300]
            assert preview.json()["can_escalate"] is False
            assert any("different contract" in item for item in preview.json()["blockers"]), preview.json()["blockers"]

    _run(scenario())


def test_evidence_not_confirmed_against_the_contract_master_waits_for_a_person(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await apply_m05(env.db, False)
            await _open(env, "primary")
            await env.db.contract_master.delete_many({"project_id": PROJ_A1})
            await env.db.delay_events.insert_many([_hindrance("HP", contract_id="primary"), _hindrance("HN")])
            for target in ("HP", "HN"):
                assert (await _link(env, "hindrance", target)).status_code == 200
            linked = await env.call(
                "POST", "org_admin", DOC_LINKS,
                json={"links": [{"document_id": DOC_IN_ID, "relationship_role": "supporting_document"}]},
            )
            assert linked.status_code in (200, 201), linked.text[:300]
            case_id = await _case(env)

            preview = (await env.call("POST", "org_admin", PREVIEW, json={"claim_ids": [CLAIM], "case_id": case_id})).json()
            # OD-1: not a mismatch, but escalation waits until a person confirms each record.
            assert not any("different contract" in item for item in preview["blockers"]), preview["blockers"]
            assert preview["can_escalate"] is False
            assert any("must confirm each one belongs to the claim's contract" in item for item in preview["blockers"])
            assert {(item["kind"], item["record_id"], item["state"]) for item in preview["unconfirmed_evidence"]} == {
                ("hindrance", "HP", "unverified"), ("hindrance", "HN", "unidentified"), ("document", DOC_IN_ID, "unidentified"),
            }
            notes = preview["evidence_notes"]
            assert len(notes) == 2, notes
            unverified = next(note for note in notes if "holds no such contract" in note)
            unidentified = next(note for note in notes if "does not say which contract" in note)
            assert unidentified.count(",") >= 1 and "Document" in unidentified  # the hindrance HN and the letter
            assert "Document" not in unverified
            view = (await env.call("GET", "org_admin", f"/api/claims/{CLAIM}/submission")).json()
            assert [note.split(": ", 1)[-1] for note in notes] == view["evidence_notes"]

            # Once the project's Contract Master holds its main contract, HP is confirmed.
            await env.db.contract_master.insert_one(
                {"_id": "cm-primary", "organization_id": ORG_A, "project_id": PROJ_A1, "contract_id": "primary",
                 "contract_name": "Main works"}
            )
            preview = (await env.call("POST", "org_admin", PREVIEW, json={"claim_ids": [CLAIM], "case_id": case_id})).json()
            assert len(preview["evidence_notes"]) == 1 and "does not say which contract" in preview["evidence_notes"][0]
            assert len(preview["unconfirmed_evidence"]) == 2

            # Nothing was rewritten.
            assert (await env.db.delay_events.find_one({"_id": "HN"})).get("contract_id") is None
            assert (await env.db.documents.find_one({"_id": DOC_IN})).get("contract_id") is None

    _run(scenario())


def test_a_person_confirms_unidentified_evidence_audited_and_nothing_is_rewritten(monkeypatch) -> None:
    """OD-1 option B: confirm, withdraw, RBAC, a mismatch cannot be confirmed, and escalation follows."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await apply_m05(env.db, False)
            await _open(env, "primary")
            await env.db.contract_master.insert_one(
                {"_id": "cm-primary", "organization_id": ORG_A, "project_id": PROJ_A1, "contract_id": "primary"}
            )
            await env.db.delay_events.insert_many([_hindrance("HN"), _hindrance("HX", contract_id="primary")])
            for target in ("HN", "HX"):
                assert (await _link(env, "hindrance", target)).status_code == 200
            case_id = await _case(env)
            EVIDENCE = f"/api/claims/{CLAIM}/evidence-contract"

            review = (await env.call("GET", "project_admin", EVIDENCE)).json()
            assert [item["record_id"] for item in review["unconfirmed"]] == ["HN"] and review["blocking"]

            # Reading is not confirming: a viewer cannot confirm (the claim's edit permission is needed).
            await env.db.users.update_one({"_id": "user-project_admin"}, {"$set": {"roles": ["viewer"]}})
            denied = await env.call("POST", "project_admin", f"{EVIDENCE}/confirmations",
                                    json={"record_kind": "hindrance", "record_id": "HN", "reason": "Site diary of the main works."})
            assert denied.status_code == 403, denied.text[:300]

            short = await env.call("POST", "org_admin", f"{EVIDENCE}/confirmations",
                                   json={"record_kind": "hindrance", "record_id": "HN", "reason": "yes"})
            assert short.status_code == 422 and _code(short) == "evidence_confirmation_reason_required"
            # A verified record, and a record of another contract, cannot be "confirmed".
            await env.db.delay_events.update_one({"_id": "HX"}, {"$set": {"contract_id": "pkg-depot"}})
            mismatch = await env.call("POST", "org_admin", f"{EVIDENCE}/confirmations",
                                      json={"record_kind": "hindrance", "record_id": "HX", "reason": "It is ours, honestly."})
            assert mismatch.status_code == 409 and _code(mismatch) == "evidence_confirmation_not_applicable"
            await env.db.delay_events.update_one({"_id": "HX"}, {"$set": {"contract_id": "primary"}})

            confirmed = await env.call("POST", "org_admin", f"{EVIDENCE}/confirmations",
                                       json={"record_kind": "hindrance", "record_id": "HN",
                                             "reason": "The hindrance is at the main works site, chainage 12+400."})
            assert confirmed.status_code == 200, confirmed.text[:300]
            body = confirmed.json()
            assert body["unconfirmed"] == [] and body["blocking"] is None
            (item,) = body["confirmed"]
            assert item["record_id"] == "HN" and item["confirmed_by"] == "user-org_admin" and item["state"] == "unidentified"
            again = await env.call("POST", "org_admin", f"{EVIDENCE}/confirmations",
                                   json={"record_kind": "hindrance", "record_id": "HN", "reason": "Confirming once more."})
            assert again.status_code == 409 and _code(again) == "evidence_already_confirmed"

            audit = await env.db.audit_events.find_one({"action": "claim.evidence_contract_confirmed"})
            assert audit and audit["actor_id"] == "user-org_admin" and audit["after"]["record_id"] == "HN"
            assert (await env.db.delay_events.find_one({"_id": "HN"})).get("contract_id") is None  # not rewritten

            preview = (await env.call("POST", "org_admin", PREVIEW, json={"claim_ids": [CLAIM], "case_id": case_id})).json()
            assert not any("confirm" in item for item in preview["blockers"]), preview["blockers"]

            # Withdrawn: escalation waits again, and the run itself refuses.
            withdrawn = await env.call("POST", "org_admin", f"{EVIDENCE}/confirmations/{item['confirmation_id']}/withdraw",
                                       json={"reason": "Chainage was wrong; this is the depot site."})
            assert withdrawn.status_code == 200 and withdrawn.json()["blocking"], withdrawn.text[:300]
            assert (await env.db.claim_evidence_contract_confirmations.find_one({"_id": item["confirmation_id"]}))["status"] == "withdrawn"
            run = await env.call("POST", "org_admin", RUN,
                                 json={"claim_ids": [CLAIM], "case_id": case_id, "acknowledged_weak": [CLAIM]})
            assert run.status_code == 409 and _code(run) == "claim_evidence_contract_unconfirmed", run.text[:300]
            assert not (await env.db.claims.find_one({"_id": CLAIM})).get("arbitration_case_id")

    _run(scenario())


def test_the_formal_submission_waits_for_confirmation(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _open(env, "primary")
            linked = await env.call(
                "POST", "org_admin", DOC_LINKS,
                json={"links": [{"document_id": DOC_IN_ID, "relationship_role": "supporting_document"}]},
            )
            assert linked.status_code in (200, 201), linked.text[:300]
            view = (await env.call("GET", "org_admin", f"/api/claims/{CLAIM}/submission")).json()
            assert [item["record_id"] for item in view["unconfirmed_evidence"]] == [DOC_IN_ID]
            # Not composed yet, so export already waits; the confirmation rule is checked once composed.
            await env.db.claims.update_one({"_id": CLAIM}, {"$set": {"bench.stage": "locked"}})
            await env.db.claim_submissions.insert_one({"_id": "s1", "claim_id": CLAIM, "sections": [], "evidence": []})
            view = (await env.call("GET", "org_admin", f"/api/claims/{CLAIM}/submission")).json()
            assert view["can_export"] is False and "must confirm each one" in view["cannot_export_reason"]
            exported = await env.call("GET", "org_admin", f"/api/claims/{CLAIM}/submission/export?format=docx")
            assert exported.status_code == 409, exported.text[:300]
            await env.call("POST", "org_admin", f"/api/claims/{CLAIM}/evidence-contract/confirmations",
                           json={"record_kind": "document", "record_id": DOC_IN_ID, "reason": "Letter is on the main works file."})
            view = (await env.call("GET", "org_admin", f"/api/claims/{CLAIM}/submission")).json()
            assert view["unconfirmed_evidence"] == [] and "must confirm" not in (view["cannot_export_reason"] or "")

    _run(scenario())
