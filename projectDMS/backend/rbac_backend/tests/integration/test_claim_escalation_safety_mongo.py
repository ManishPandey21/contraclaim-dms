"""4B-36 (D-18 except undo, C-10, M-05): escalation is idempotent, guarded and refuses
closed cases. Real HTTP, MongoDB, RBAC and the real document-relationship freeze.

Before 4B-36:
- rows were carried with ``find_one`` then insert and no unique index, so concurrent
  runs into one case inserted duplicate rows;
- two "new case" runs created two cases (one an orphan);
- the claim update was unconditional, outside the status lifecycle, with no per-claim
  audit; a filed or archived case was not refused;
- the preview ignored the chosen case;
- the freeze was a direct ``$set`` that bypassed DocumentRelationshipService.freeze.

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set); run in CI's
real-Mongo step.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import Any

import pytest

from rbac_backend.migrations.v20261120_0001_arbitration_matrix_origin_unique import upgrade as apply_m05
from rbac_backend.services.arbitration_drafting.matrix_registry import MATRIX_COLLECTIONS
from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import CLAIM, _env
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import DOC_IN_ID, ORG_A, PROJ_A1
from rbac_backend.utils.rate_limiter import RateLimiter

pytestmark = pytest.mark.integration

RUN = "/api/claims/escalation/run"
PREVIEW = "/api/claims/escalation/preview"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _code(response: Any) -> str:
    detail = response.json().get("detail")
    return detail.get("code", "") if isinstance(detail, dict) else ""


async def _prepared(env: Any, *, link: bool = True) -> None:
    # The escalation limiter (10 a minute per user) keeps its counts in process; each
    # scenario starts with its own window, as test_rbac_policy_hardening does.
    RateLimiter._shared_user_requests.clear()
    RateLimiter._shared_buckets.clear()
    await apply_m05(env.db, False)
    assert (await env.call("POST", "org_admin", f"/api/claims/{CLAIM}/bench/open")).status_code == 200
    if link:
        linked = await env.call(
            "POST",
            "org_admin",
            f"/api/entities/claim/{CLAIM}/document-links:batch",
            json={"links": [{"document_id": DOC_IN_ID, "relationship_role": "notice"}]},
        )
        assert linked.status_code in (200, 201), linked.text[:300]


async def _case(env: Any, title: str = "ABC Infra v. Authority") -> str:
    created = await env.call(
        "POST",
        "org_admin",
        "/api/arbitration/cases",
        json={"organization_id": ORG_A, "project_id": PROJ_A1, "title": title, "party_perspective": "claimant"},
    )
    assert created.status_code in (200, 201), created.text[:300]
    body = created.json()
    return str(body.get("id") or body.get("_id"))


async def _rows(env: Any, case_id: str) -> list:
    rows: list = []
    for collection in sorted(set(MATRIX_COLLECTIONS.values())):
        rows.extend(await env.db[collection].find({"case_id": case_id, "origin_key": {"$type": "string"}}).to_list(None))
    return rows


async def _claim(env: Any) -> dict:
    return await env.db.claims.find_one({"_id": CLAIM})


def test_five_concurrent_runs_into_one_case_carry_each_row_once(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _prepared(env)
            case_id = await _case(env)
            results = await asyncio.gather(
                *(env.call("POST", "org_admin", RUN, json={"claim_ids": [CLAIM], "case_id": case_id}) for _ in range(5))
            )
            assert all(item.status_code == 201 for item in results), [item.text[:200] for item in results]
            rows = await _rows(env, case_id)
            keys = Counter(row["origin_key"] for row in rows)
            assert rows and max(keys.values()) == 1, keys.most_common(3)
            created = sum(sum(item.json()["rows_created"].values()) for item in results)
            assert created == len(rows)

            claim = await _claim(env)
            assert claim["arbitration_case_id"] == case_id and claim["status"] == "disputed"
            assert claim["escalation"]["prior_status"] == "submitted"
            assert claim["escalation"]["was_frozen"] is False
            assert len(claim["escalation"]["seeded_rows"]) == len(rows)
            assert claim["evidence_frozen_at"] is not None
            # The freeze went through the relationship service: the link is pinned to a version.
            link = await env.db.entity_document_links.find_one({"target_id": CLAIM, "removed_at": None})
            assert link["frozen_at"] is not None and link["document_version_id"]
            moves = await env.db.audit_events.count_documents({"action": "claim.escalated", "resource_id": CLAIM})
            assert moves == 1
            case = await env.db.arbitration_cases.find_one({"_id": case_id})
            assert case["claim_ids"] == [CLAIM]

            # A re-run is a no-op.
            again = await env.call("POST", "org_admin", RUN, json={"claim_ids": [CLAIM], "case_id": case_id})
            assert again.status_code == 201, again.text[:300]
            assert again.json()["rows_created"] == {} and again.json()["rows_already_present"] == len(rows)
            assert len(await _rows(env, case_id)) == len(rows)
            assert await env.db.audit_events.count_documents({"action": "claim.escalated", "resource_id": CLAIM}) == 1

    _run(scenario())


def test_five_new_case_runs_with_one_key_make_one_case(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _prepared(env)
            missing = await env.call(
                "POST", "org_admin", RUN, json={"claim_ids": [CLAIM], "new_case": {"title": "No key"}}
            )
            assert missing.status_code == 422, missing.text[:300]
            assert await env.db.arbitration_cases.count_documents({}) == 0
            body = {"claim_ids": [CLAIM], "new_case": {"title": "ABC Infra v. Authority"}, "idempotency_key": "esc-test-one-key"}
            results = await asyncio.gather(*(env.call("POST", "org_admin", RUN, json=body) for _ in range(5)))
            assert all(item.status_code == 201 for item in results), [item.text[:200] for item in results]
            case_ids = {item.json()["case_id"] for item in results}
            assert len(case_ids) == 1
            assert sum(item.json()["created_case"] for item in results) == 1
            live = await env.db.arbitration_cases.count_documents({"project_id": PROJ_A1, "deleted_at": None})
            assert live == 1
            rows = await _rows(env, next(iter(case_ids)))
            assert max(Counter(row["origin_key"] for row in rows).values()) == 1

    _run(scenario())


def test_two_new_case_runs_with_different_keys_leave_one_live_case(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _prepared(env)
            results = await asyncio.gather(
                *(
                    env.call(
                        "POST",
                        "org_admin",
                        RUN,
                        json={"claim_ids": [CLAIM], "new_case": {"title": f"Case {n}"}, "idempotency_key": f"esc-key-{n:04d}"},
                    )
                    for n in range(2)
                )
            )
            codes = sorted(item.status_code for item in results)
            # Whichever reaches the claim second is refused: by the reservation if both passed
            # the blockers, or by the blocker if the first had already finished.
            assert codes == [201, 409], [item.text[:200] for item in results]
            winner = next(item for item in results if item.status_code == 201).json()["case_id"]
            loser = next(item for item in results if item.status_code == 409)
            assert _code(loser) == "escalation_claim_taken" or "another arbitration case" in loser.text
            claim = await _claim(env)
            assert claim["arbitration_case_id"] == winner
            live = await env.db.arbitration_cases.find({"project_id": PROJ_A1, "deleted_at": None}).to_list(None)
            # The loser's case, if one was made, is soft-deleted; no live case is left without the claim.
            assert [str(case["_id"]) for case in live] == [winner]
            for case in await env.db.arbitration_cases.find({"deleted_at": {"$ne": None}}).to_list(None):
                assert not await _rows(env, str(case["_id"]))

    _run(scenario())


@pytest.mark.parametrize("closed", ["filed", "archived"])
def test_a_filed_or_archived_case_takes_no_claims(monkeypatch, closed: str) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _prepared(env)
            case_id = await _case(env)
            await env.db.arbitration_cases.update_one({"_id": case_id}, {"$set": {"status": closed}})
            preview = await env.call("POST", "org_admin", PREVIEW, json={"claim_ids": [CLAIM], "case_id": case_id})
            assert preview.status_code == 200 and preview.json()["can_escalate"] is False
            assert any(closed in blocker for blocker in preview.json()["blockers"])
            refused = await env.call("POST", "org_admin", RUN, json={"claim_ids": [CLAIM], "case_id": case_id})
            assert refused.status_code == 409, refused.text[:300]
            claim = await _claim(env)
            assert claim.get("arbitration_case_id") is None and claim["status"] == "submitted"
            assert claim.get("evidence_frozen_at") is None
            assert await _rows(env, case_id) == []

    _run(scenario())


def test_the_preview_is_for_the_chosen_case(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _prepared(env)
            first, other = await _case(env, "First"), await _case(env, "Other")
            assert (await env.call("POST", "org_admin", RUN, json={"claim_ids": [CLAIM], "case_id": first})).status_code == 201
            same = await env.call("POST", "org_admin", PREVIEW, json={"claim_ids": [CLAIM], "case_id": first})
            assert same.json()["can_escalate"] is True, same.json()["blockers"]
            elsewhere = await env.call("POST", "org_admin", PREVIEW, json={"claim_ids": [CLAIM], "case_id": other})
            assert elsewhere.json()["can_escalate"] is False
            assert any("another arbitration case" in blocker for blocker in elsewhere.json()["blockers"])

    _run(scenario())


def test_a_claim_that_cannot_be_frozen_is_not_escalated_and_nothing_is_written(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _prepared(env)
            # The linked document loses its immutable file: the freeze would refuse it.
            changed = await env.db.document_versions.update_many(
                {"document_id": DOC_IN_ID}, {"$unset": {"file_object_id": ""}}
            )
            assert changed.modified_count >= 1
            body = {"claim_ids": [CLAIM], "new_case": {"title": "T"}, "idempotency_key": "esc-unfreezable"}
            refused = await env.call("POST", "org_admin", RUN, json=body)
            assert refused.status_code == 409, refused.text[:300]
            claim = await _claim(env)
            assert claim.get("arbitration_case_id") is None and claim["status"] == "submitted"
            assert claim.get("evidence_frozen_at") is None and claim.get("escalation") is None
            assert await env.db.arbitration_cases.count_documents({"deleted_at": None}) == 0
            assert await env.db.claim_escalation_keys.count_documents({}) == 0

    _run(scenario())


def test_m05_reports_existing_duplicates_and_leaves_that_collection_unindexed(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            from rbac_backend.migrations.v20261120_0001_arbitration_matrix_origin_unique import INDEX_NAME, INDEXES

            duplicate = {"case_id": "case-x", "origin_key": "claim:c1:head:H1"}
            await env.db.arbitration_claim_matrix.insert_many([{"_id": "r1", **duplicate}, {"_id": "r2", **duplicate}])
            dry = await apply_m05(env.db, True)
            assert dry.warnings and "arbitration_claim_matrix" in dry.warnings[0]
            applied = await apply_m05(env.db, False)
            assert applied.warnings == dry.warnings
            names = {index["name"] for index in await env.db.arbitration_claim_matrix.list_indexes().to_list(None)}
            assert INDEX_NAME not in names
            assert await env.db.arbitration_claim_matrix.count_documents({}) == 2  # nothing deleted
            others = [index["collection"] for index in INDEXES if index["collection"] != "arbitration_claim_matrix"]
            for collection in others:
                names = {index["name"] for index in await env.db[collection].list_indexes().to_list(None)}
                assert INDEX_NAME in names, collection
            # Once resolved, a re-run creates it.
            await env.db.arbitration_claim_matrix.delete_one({"_id": "r2"})
            clean = await apply_m05(env.db, False)
            assert not clean.warnings
            names = {index["name"] for index in await env.db.arbitration_claim_matrix.list_indexes().to_list(None)}
            assert INDEX_NAME in names

    _run(scenario())
