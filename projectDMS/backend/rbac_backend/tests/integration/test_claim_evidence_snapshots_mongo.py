"""Delay-record evidence snapshots (owner decision 2026-11-18). Real MongoDB, real HTTP and RBAC.

At escalation the documents behind a head's delay records (hindrances, EOT
submissions and determinations) are snapshotted for that claim and case: id,
version, content hash, source record, head, contract identity, provenance, when
and by whom. Shared records and documents are never locked or rewritten. When a
source moves on the snapshot goes stale (captured fields untouched) and blocks new
pleading approval in the case until someone who manages the claim re-pins or
withdraws it. Concurrent captures and reviews resolve to one result.

The end-to-end path (escalation captures; a revision blocks the final approval; a
re-pin unblocks it) is in ``test_claim_journey_http_mongo.py``.

Opt-in: ``CLAIM_BENCH_MONGODB_URI``; run in CI's real-Mongo step.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Dict

import pytest
from bson import ObjectId

from rbac_backend.tests.integration.test_claim_privileged_rbac_mongo import _add_actor
from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import CLAIM, _env
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, PROJ_A1, PROJ_B1, _document

pytestmark = pytest.mark.integration

CASE = "case-snap-1"
HINDRANCE = "hin-snap-1"
DIARY = ObjectId()
DIARY_ID = str(DIARY)
SNAPSHOTS = f"/api/claims/{CLAIM}/evidence-snapshots"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _seed(env: Any) -> Dict[str, Any]:
    db = env.db
    await db.claims.update_one({"_id": CLAIM}, {"$set": {"contract_id": "primary"}})
    await db.delay_events.insert_one({"_id": HINDRANCE, "organization_id": ORG_A, "project_id": PROJ_A1,
                                      "title": "Right of way not handed over", "linked_document_ids": []})
    await db.claim_head_links.insert_one({
        "_id": "hl-snap-1", "organization_id": ORG_A, "project_id": PROJ_A1, "claim_id": CLAIM, "head_id": "H1",
        "target_type": "hindrance", "target_id": HINDRANCE, "target_revision": "r1", "target_label": "HIN-0001",
        "status": "active", "created_at": datetime(2026, 3, 1),
    })
    await db.documents.insert_one({**_document(DIARY, organization_id=ORG_A, project_id=PROJ_A1, upload_type="incoming",
                                               subject="Site diary", letter_no="SD/03", sender="Site engineer"),
                                   "current_version_id": "v1", "sha256": "a" * 64, "contract_id": "primary"})
    await db.document_versions.insert_one({"_id": "v1", "document_id": DIARY_ID, "sha256": "a" * 64, "file_object_id": "f1"})
    await db.entity_document_links.insert_one({
        "_id": "edl-snap-1", "organization_id": ORG_A, "project_id": PROJ_A1, "target_type": "delay_event",
        "target_id": HINDRANCE, "document_id": DIARY_ID, "relationship_role": "supporting_document",
        "removed_at": None, "created_by": "user-org_admin", "created_at": datetime(2026, 3, 2),
    })
    return await db.claims.find_one({"_id": CLAIM})


async def _capture(env: Any, claim: Dict[str, Any]) -> list:
    from rbac_backend.services import claim_evidence_snapshots as snapshots

    return await snapshots.capture(env.db, claim, CASE, "user-org_admin")


async def _revise(env: Any) -> None:
    await env.db.document_versions.insert_one({"_id": "v2", "document_id": DIARY_ID, "sha256": "b" * 64, "file_object_id": "f2"})
    await env.db.documents.update_one({"_id": DIARY}, {"$set": {"current_version_id": "v2", "sha256": "b" * 64}})


def test_escalation_records_what_the_claim_relied_on_and_concurrent_captures_agree(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            claim = await _seed(env)
            first, second = await asyncio.gather(_capture(env, claim), _capture(env, claim))
            assert len(first) == len(second) == 1
            rows = await env.db.claim_evidence_snapshots.find({"claim_id": CLAIM}).to_list(10)
            assert len(rows) == 1, rows
            [row] = rows
            assert (row["document_id"], row["document_version_id"], row["content_hash"]) == (DIARY_ID, "v1", "a" * 64)
            assert row["source_record"] == {"type": "hindrance", "id": HINDRANCE} and row["head_id"] == "H1"
            assert (row["contract_id"], row["document_contract_id"]) == ("primary", "primary")
            assert row["provenance"]["link_id"] == "edl-snap-1" and row["provenance"]["linked_by"] == "user-org_admin"
            assert row["captured_by"] == "user-org_admin" and row["status"] == "current" and row["case_id"] == CASE
            # Nothing shared was locked or rewritten.
            assert "frozen_at" not in (await env.db.entity_document_links.find_one({"_id": "edl-snap-1"}))
            assert (await env.db.documents.find_one({"_id": DIARY}))["current_version_id"] == "v1"

    _run(scenario())


@pytest.mark.parametrize(
    "change, reason",
    [
        ("revise", "new version"),
        ("unlink", "link to the delay record has been removed"),
        ("unhead", "no longer relies on this delay record"),
        ("quarantine", "withdrawn from use"),
    ],
)
def test_a_source_that_moved_on_marks_the_snapshot_stale_and_keeps_what_was_captured(monkeypatch, change: str, reason: str) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services import claim_evidence_snapshots as snapshots

            [captured] = await _capture(env, await _seed(env))
            await snapshots.assert_current_for_case(env.db, CASE)  # current: approval allowed
            if change == "revise":
                await _revise(env)
            elif change == "unlink":
                await env.db.entity_document_links.update_one({"_id": "edl-snap-1"}, {"$set": {"removed_at": datetime.utcnow()}})
            elif change == "unhead":
                await env.db.claim_head_links.update_one({"_id": "hl-snap-1"}, {"$set": {"status": "removed"}})
            else:
                await env.db.documents.update_one({"_id": DIARY}, {"$set": {"lifecycle_state": "deleted"}})
            with pytest.raises(Exception) as refused:
                await snapshots.assert_current_for_case(env.db, CASE)
            assert getattr(refused.value, "status_code", None) == 409
            assert refused.value.detail["code"] == "delay_evidence_stale"
            stored = await env.db.claim_evidence_snapshots.find_one({"_id": captured["_id"]})
            assert stored["status"] == "stale" and reason in stored["stale_reason"]
            assert (stored["document_version_id"], stored["content_hash"]) == ("v1", "a" * 64), "captured fields are never rewritten"
            assert await env.db.audit_events.find_one({"action": "claim.evidence_snapshot.stale",
                                                       "metadata.snapshot_id": captured["_id"]})

    _run(scenario())


def test_a_manager_re_pins_a_revised_document_once_and_a_concurrent_review_is_refused(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services import claim_evidence_snapshots as snapshots

            [captured] = await _capture(env, await _seed(env))
            await _revise(env)
            listed = await env.call("GET", "org_admin", SNAPSHOTS)
            assert listed.status_code == 200 and listed.json()["stale"] == 1, listed.text[:300]
            review = f"{SNAPSHOTS}/{captured['_id']}/review"
            body = {"action": "repin", "reason": "The revised diary was reviewed; it still supports H1."}
            results = await asyncio.gather(env.call("POST", "org_admin", review, json=body),
                                           env.call("POST", "project_admin", review, json=body))
            assert sorted(r.status_code for r in results) == [200, 409], [r.text[:200] for r in results]
            old = await env.db.claim_evidence_snapshots.find_one({"_id": captured["_id"]})
            new = await env.db.claim_evidence_snapshots.find_one({"_id": old["superseded_by"]})
            assert old["status"] == "superseded" and old["review_reason"].startswith("The revised diary")
            assert (new["status"], new["document_version_id"], new["content_hash"], new["repinned_from"]) == (
                "current", "v2", "b" * 64, captured["_id"])
            await snapshots.assert_current_for_case(env.db, CASE)
            assert await env.db.audit_events.find_one({"action": "claim.evidence_snapshot.repinned"})

    _run(scenario())


def test_a_document_that_no_longer_supports_the_record_is_withdrawn_not_re_pinned(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services import claim_evidence_snapshots as snapshots

            [captured] = await _capture(env, await _seed(env))
            await env.db.entity_document_links.update_one({"_id": "edl-snap-1"}, {"$set": {"removed_at": datetime.utcnow()}})
            await snapshots.refresh(env.db, case_id=CASE)
            review = f"{SNAPSHOTS}/{captured['_id']}/review"
            repin = await env.call("POST", "org_admin", review, json={"action": "repin", "reason": "Try to re-pin it."})
            assert repin.status_code == 409 and repin.json()["detail"]["code"] == "evidence_snapshot_unavailable"
            withdraw = await env.call("POST", "org_admin", review, json={"action": "withdraw", "reason": "The diary was unlinked."})
            assert withdraw.status_code == 200 and withdraw.json()["status"] == "withdrawn"
            await snapshots.assert_current_for_case(env.db, CASE)

    _run(scenario())


def test_reading_and_reviewing_snapshots_follow_claim_and_document_access(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services import claim_evidence_snapshots as snapshots

            [captured] = await _capture(env, await _seed(env))
            await _revise(env)
            review = f"{SNAPSHOTS}/{captured['_id']}/review"
            body = {"action": "repin", "reason": "Reviewed by someone without the right."}
            await _add_actor(env.db, "claim_viewer", "role-claim-viewer", ["dms.claim.view"])
            assert (await env.call("GET", "claim_viewer", SNAPSHOTS)).status_code == 200
            assert (await env.call("POST", "claim_viewer", review, json=body)).status_code == 403
            foreign = await env.call("GET", "foreign_org_admin", SNAPSHOTS, project=PROJ_B1)
            assert foreign.status_code in (403, 404), foreign.text[:200]
            assert (await env.client.get(SNAPSHOTS, headers={"X-Proj-Id": PROJ_A1})).status_code == 401
            for bad in ({"action": "delete", "reason": "Not an action."}, {"action": "repin", "reason": "x"},
                        {"action": "repin", "reason": "Fine reason.", "status": "current"}):
                assert (await env.call("POST", "org_admin", review, json=bad)).status_code == 422

            async def nothing_readable(*args: Any, **kwargs: Any) -> set:
                return set()

            # A reader who may not read the document sees a restricted entry: no id, version, hash or source.
            monkeypatch.setattr(snapshots, "readable_documents", nothing_readable)
            restricted = (await env.call("GET", "org_admin", SNAPSHOTS)).json()["snapshots"][0]
            assert restricted["restricted"] is True and restricted["document_id"] is None
            assert not any(key in restricted for key in ("document_version_id", "content_hash", "source_record"))
            assert DIARY_ID not in str(restricted)
            refused = await env.call("POST", "org_admin", review, json=body)
            assert refused.status_code == 403 and refused.json()["detail"]["code"] == "evidence_restricted"
            assert (await env.db.claim_evidence_snapshots.find_one({"_id": captured["_id"]}))["status"] == "stale"

    _run(scenario())


def test_an_undone_escalation_withdraws_its_snapshots_and_keeps_them(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services import claim_evidence_snapshots as snapshots

            [captured] = await _capture(env, await _seed(env))
            await _revise(env)
            assert await snapshots.withdraw_for_case(env.db, CLAIM, CASE, "Escalation undone: test", "user-org_admin") == 1
            stored = await env.db.claim_evidence_snapshots.find_one({"_id": captured["_id"]})
            assert stored["status"] == "withdrawn" and stored["document_version_id"] == "v1"
            await snapshots.assert_current_for_case(env.db, CASE)

    _run(scenario())


def test_capturing_again_never_rewrites_or_revives_a_snapshot(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.services import claim_evidence_snapshots as snapshots

            claim = await _seed(env)
            [captured] = await _capture(env, claim)
            first = await env.db.claim_evidence_snapshots.find_one({"_id": captured["_id"]})
            # The same version's content changes: the snapshot goes stale under the same id.
            await env.db.document_versions.update_one({"_id": "v1"}, {"$set": {"sha256": "c" * 64}})
            await env.db.documents.update_one({"_id": DIARY}, {"$set": {"sha256": "c" * 64}})
            await snapshots.refresh(env.db, case_id=CASE)
            await _capture(env, claim)  # an escalation run again (idempotent)
            stored = await env.db.claim_evidence_snapshots.find_one({"_id": captured["_id"]})
            assert stored["status"] == "stale" and "content has changed" in stored["stale_reason"]
            assert stored["content_hash"] == "a" * 64 and stored["captured_at"] == first["captured_at"]

    _run(scenario())
