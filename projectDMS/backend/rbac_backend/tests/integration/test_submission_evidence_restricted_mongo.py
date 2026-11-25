"""4B-27 (D-25, AQ-5): one evidence set for every viewer; restricted items redacted; export refused.
Real HTTP, real MongoDB replica set, real RBAC.

The plan's proof: two users with different document access see the same staleness
state; the narrower one sees "Restricted item" and cannot export. Also: nothing
identifying a restricted item - title, reference, date, type or id - appears anywhere in
the narrower user's raw JSON; the refusal is a structured, audited error naming no item;
a document changed after composing makes the evidence stale for both users alike.

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set); run in CI's real-Mongo step.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from rbac_backend.tests.integration.test_claim_privileged_rbac_mongo import _add_actor
from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import CLAIM, _env
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import DOC_IN, DOC_IN_ID, DOC_OUT_ID, PROJ_A1

pytestmark = pytest.mark.integration

SUBMISSION = f"/api/claims/{CLAIM}/submission"
DOC_LINKS = f"/api/entities/claim/{CLAIM}/document-links:batch"
# Everything that identifies the two linked documents (fixtures in test_variation_relationships_cl2_mongo).
IDENTIFYING = (
    DOC_IN_ID, DOC_OUT_ID, "Engineer instruction on added works", "Contractor variation quotation",
    "ENG/VO/014", "CON/VO/022", "2026-09-01", "incoming", "Outgoing", ".pdf",
)
NARROW = ["dms.claim.view", "dms.claim.edit"]  # the claim, but not its documents


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _ok(response: Any) -> dict:
    assert response.status_code == 200, (response.status_code, response.text[:400])
    return response.json()


async def _composed(env: Any) -> None:
    await env.db.projects.update_one({"_id": PROJ_A1}, {"$set": {"contractual_capacity": "contractor"}})
    await _add_actor(env.db, "narrow", "role-narrow", NARROW)
    await _ok(await env.call("POST", "org_admin", f"/api/claims/{CLAIM}/bench/open"))
    linked = await env.call(
        "POST", "org_admin", DOC_LINKS,
        json={"links": [{"document_id": DOC_IN_ID, "relationship_role": "supporting_document"},
                        {"document_id": DOC_OUT_ID, "relationship_role": "supporting_document"}]},
    )
    assert linked.status_code in (200, 201), linked.text[:300]
    await env.db.claims.update_one({"_id": CLAIM}, {"$set": {"bench.stage": "locked"}})
    await _ok(await env.call("POST", "org_admin", f"{SUBMISSION}/compose", json={"sections": []}))


def test_every_viewer_sees_the_same_state_and_the_narrower_one_sees_restricted_items(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _composed(env)
            full = await _ok(await env.call("GET", "org_admin", SUBMISSION))
            narrow_response = await env.call("GET", "narrow", SUBMISSION)
            narrow = await _ok(narrow_response)

            # The same submission, the same staleness, whoever looks.
            assert [item["stale"] for item in full["sections"]] == [item["stale"] for item in narrow["sections"]]
            assert full["stale_sections"] == narrow["stale_sections"] == 0
            assert len(full["evidence"]) == len(narrow["evidence"]) == 2
            assert full["restricted_items"] == 0 and narrow["restricted_items"] == 2

            assert {item["title"] for item in narrow["evidence"]} == {"Restricted item"}
            assert all(item["document_id"] == "" and item["reference"] is None and item["date"] is None
                       for item in narrow["evidence"])
            raw = narrow_response.text
            for value in IDENTIFYING:
                assert value not in raw, value
            # The full-access viewer sees them.
            assert "Engineer instruction on added works" in json.dumps(full)

            assert narrow["can_export"] is False and "2 items" in narrow["cannot_export_reason"]
            refused = await env.call("GET", "narrow", f"{SUBMISSION}/export?format=docx")
            assert refused.status_code == 403, refused.text[:300]
            detail = refused.json()["detail"]
            assert detail["code"] == "evidence_restricted" and detail["restricted_items"] == 2
            for value in IDENTIFYING:
                assert value not in refused.text, value
            audit = await env.db.audit_events.find_one({"action": "claim.submission_export_refused"})
            assert audit and audit["actor_id"] == "user-narrow" and audit["after"]["restricted_items"] == 2
            assert not any(value in json.dumps(audit, default=str) for value in IDENTIFYING[2:6])

            exported = await env.call("GET", "org_admin", f"{SUBMISSION}/export?format=docx")
            assert exported.status_code == 200, exported.text[:300]

    _run(scenario())


def test_a_document_changed_after_composing_makes_the_evidence_stale_for_everyone(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _composed(env)
            stored = await env.db.claim_submissions.find_one({"claim_id": CLAIM})
            assert set(stored["evidence_snapshot"]) == {DOC_IN_ID, DOC_OUT_ID}

            # A tag is not a revision.
            await env.db.documents.update_one({"_id": DOC_IN}, {"$set": {"tags": ["reviewed"]}})
            assert (await _ok(await env.call("GET", "org_admin", SUBMISSION)))["evidence_changed"] == 0

            # A new subject is.
            await env.db.documents.update_one({"_id": DOC_IN}, {"$set": {"subject": "Engineer instruction (revised)"}})
            full = await _ok(await env.call("GET", "org_admin", SUBMISSION))
            narrow_response = await env.call("GET", "narrow", SUBMISSION)
            narrow = await _ok(narrow_response)
            assert full["evidence_changed"] == narrow["evidence_changed"] == 1
            evidence_stale = lambda view: next(item["stale"] for item in view["sections"] if item["section"]["key"] == "chronology_and_evidence")  # noqa: E731
            assert evidence_stale(full) is True and evidence_stale(narrow) is True
            assert full["stale_sections"] == narrow["stale_sections"] >= 1
            assert "revised" not in narrow_response.text
            assert full["can_export"] is False  # stale: re-compose first

            await _ok(await env.call("POST", "org_admin", f"{SUBMISSION}/compose", json={"sections": []}))
            assert (await _ok(await env.call("GET", "org_admin", SUBMISSION)))["evidence_changed"] == 0

    _run(scenario())
