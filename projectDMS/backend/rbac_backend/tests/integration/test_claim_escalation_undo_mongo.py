"""4B-37 (D-18, AQ-6, C-10): undoing an escalation, over real HTTP, MongoDB, RBAC and the
real document-relationship freeze.

Undo is allowed until the case's first pleading version. It restores the claim's prior
status, lifts the freeze the escalation made (and only that one), archives the rows it
seeded with the reason, and discards a case it created that nothing else uses. It needs
claim-manage on the claim, arbitration-edit on the case and a reason, and is audited.

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set); run in CI's
real-Mongo step.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import pytest

from rbac_backend.tests.integration.test_claim_escalation_safety_mongo import RUN, _case, _claim, _prepared, _rows
from rbac_backend.tests.integration.test_claim_privileged_rbac_mongo import _add_actor
from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import CLAIM, _env
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, PROJ_A1

pytestmark = pytest.mark.integration

UNDO = "/api/claims/escalation/undo"
REASON = "Escalated by mistake; the claim is still being negotiated."


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _code(response: Any) -> str:
    detail = response.json().get("detail")
    return detail.get("code", "") if isinstance(detail, dict) else ""


async def _escalate_new(env: Any, key: str = "esc-undo-0001") -> str:
    run = await env.call(
        "POST", "org_admin", RUN, json={"claim_ids": [CLAIM], "acknowledged_weak": [CLAIM], "new_case": {"title": "ABC v. Authority"}, "idempotency_key": key}
    )
    assert run.status_code == 201, run.text[:300]
    return run.json()["case_id"]


def test_undo_before_any_pleading_version_reverses_the_escalation_exactly(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _prepared(env)
            link_before = await env.db.entity_document_links.find_one({"target_id": CLAIM, "removed_at": None})
            case_id = await _escalate_new(env)
            seeded = await _rows(env, case_id)
            assert seeded and (await _claim(env))["evidence_frozen_at"] is not None

            undone = await env.call("POST", "org_admin", UNDO, json={"claim_id": CLAIM, "case_id": case_id, "reason": REASON})
            assert undone.status_code == 200, undone.text[:300]
            body = undone.json()
            assert body["rows_archived"] == len(seeded) and body["freeze_lifted"] and body["case_discarded"]

            claim = await _claim(env)
            assert claim["status"] == "submitted" and claim.get("arbitration_case_id") is None
            assert claim.get("evidence_frozen_at") is None and claim.get("escalation") is None
            assert claim["escalation_history"][-1]["undo_reason"] == REASON
            link = await env.db.entity_document_links.find_one({"_id": link_before["_id"]})
            assert link["frozen_at"] is None
            assert link.get("document_version_id") == link_before.get("document_version_id")
            assert await _rows(env, case_id) == []  # archived rows carry no origin any more
            for row in seeded:
                stored = await env.db.arbitration_claim_matrix.find_one({"_id": row["_id"]}) or await _find_any(env, row["_id"])
                assert stored["deleted_at"] is not None and stored["delete_reason"].endswith(REASON)
                assert stored["archived_origin_key"] == row["origin_key"]
            assert (await env.db.arbitration_cases.find_one({"_id": case_id}))["deleted_at"] is not None
            actions = {
                event["action"]
                for event in await env.db.audit_events.find(
                    {"action": {"$in": ["claim.escalation_undone", "arbitration_case.claim_escalation_undone",
                                        "document_relationships.unfrozen"]}}
                ).to_list(None)
            }
            assert actions == {"claim.escalation_undone", "arbitration_case.claim_escalation_undone",
                               "document_relationships.unfrozen"}

            # The claim can be escalated again, and its rows are carried afresh.
            again = await _escalate_new(env, key="esc-undo-0002")
            assert again != case_id and len(await _rows(env, again)) == len(seeded)

    _run(scenario())


async def _find_any(env: Any, row_id: Any) -> dict:
    from rbac_backend.services.arbitration_drafting.matrix_registry import MATRIX_COLLECTIONS

    for name in set(MATRIX_COLLECTIONS.values()):
        row = await env.db[name].find_one({"_id": row_id})
        if row:
            return row
    return {}


def test_undo_is_refused_once_the_case_has_a_pleading_version(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _prepared(env)
            case_id = await _escalate_new(env)
            # A scratch draft's version does not count.
            await env.db.arbitration_drafts.insert_one({"_id": "scratch-1", "case_id": None, "is_scratch": True})
            await env.db.arbitration_draft_versions.insert_one({"_id": "sv1", "draft_id": "scratch-1", "version": 1})
            # A version of a draft in the case does.
            await env.db.arbitration_drafts.insert_one({"_id": "draft-1", "case_id": case_id, "organization_id": ORG_A, "project_id": PROJ_A1})
            await env.db.arbitration_draft_versions.insert_one({"_id": "v1", "draft_id": "draft-1", "version": 1})
            refused = await env.call("POST", "org_admin", UNDO, json={"claim_id": CLAIM, "case_id": case_id, "reason": REASON})
            assert refused.status_code == 409 and _code(refused) == "escalation_undo_after_pleading", refused.text[:300]
            claim = await _claim(env)
            assert claim["arbitration_case_id"] == case_id and claim["escalation"]["case_id"] == case_id
            assert claim["evidence_frozen_at"] is not None and await _rows(env, case_id)

            # Without the case draft's version, the scratch version alone does not block it.
            await env.db.arbitration_draft_versions.delete_one({"_id": "v1"})
            allowed = await env.call("POST", "org_admin", UNDO, json={"claim_id": CLAIM, "case_id": case_id, "reason": REASON})
            assert allowed.status_code == 200, allowed.text[:300]
            # The case has a draft, so it is kept.
            assert allowed.json()["case_discarded"] is False

    _run(scenario())


def test_undo_needs_claim_manage_arbitration_edit_and_a_reason(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _prepared(env)
            await _add_actor(env.db, "claims_only", "claims_only", ["dms.claim.view", "dms.claim.edit", "dms.claim.manage"])
            await _add_actor(env.db, "editor_only", "editor_only",
                             ["dms.claim.view", "dms.claim.edit", "dms.arbitration.view", "dms.arbitration.edit"])
            # Sees the case and manages the claim, but may not edit the case.
            await _add_actor(env.db, "case_viewer", "case_viewer",
                             ["dms.claim.view", "dms.claim.edit", "dms.claim.manage", "dms.arbitration.view"])
            case_id = await _escalate_new(env)
            body = {"claim_id": CLAIM, "case_id": case_id, "reason": REASON}
            for persona in ("claims_only", "editor_only", "foreign_org_admin"):
                refused = await env.call("POST", persona, UNDO, json=body)
                assert refused.status_code in (403, 404), (persona, refused.status_code, refused.text[:200])
            viewer = await env.call("POST", "case_viewer", UNDO, json=body)
            assert viewer.status_code == 403, viewer.text[:300]
            for reason in ("", "too short", "  . . . . . . . "):
                refused = await env.call("POST", "org_admin", UNDO, json={**body, "reason": reason})
                assert refused.status_code == 422, (reason, refused.text[:200])
            assert (await _claim(env))["arbitration_case_id"] == case_id

    _run(scenario())


def test_undo_keeps_an_earlier_freeze_and_an_existing_case(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _prepared(env, link=False)
            earlier = datetime(2026, 10, 1)
            await env.db.claims.update_one(
                {"_id": CLAIM},
                {"$set": {"evidence_frozen_at": earlier, "evidence_frozen_by": "someone", "evidence_freeze_reason": "Lifecycle"}},
            )
            case_id = await _case(env)
            run = await env.call("POST", "org_admin", RUN, json={"claim_ids": [CLAIM], "acknowledged_weak": [CLAIM], "case_id": case_id})
            assert run.status_code == 201, run.text[:300]
            undone = await env.call("POST", "org_admin", UNDO, json={"claim_id": CLAIM, "case_id": case_id, "reason": REASON})
            assert undone.status_code == 200, undone.text[:300]
            assert undone.json()["freeze_lifted"] is False and undone.json()["case_discarded"] is False
            claim = await _claim(env)
            assert claim["evidence_frozen_at"] == earlier and claim["evidence_freeze_reason"] == "Lifecycle"
            assert (await env.db.arbitration_cases.find_one({"_id": case_id}))["deleted_at"] is None

    _run(scenario())


def test_undo_is_refused_when_the_claim_has_moved_on_or_was_not_escalated_there(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _prepared(env)
            other = await _case(env, "Other")
            case_id = await _escalate_new(env)
            elsewhere = await env.call("POST", "org_admin", UNDO, json={"claim_id": CLAIM, "case_id": other, "reason": REASON})
            assert elsewhere.status_code == 409 and _code(elsewhere) == "escalation_not_found", elsewhere.text[:300]
            await env.db.claims.update_one({"_id": CLAIM}, {"$set": {"status": "agreed"}})
            moved = await env.call("POST", "org_admin", UNDO, json={"claim_id": CLAIM, "case_id": case_id, "reason": REASON})
            assert moved.status_code == 409 and _code(moved) == "escalation_claim_moved", moved.text[:300]
            claim = await _claim(env)
            assert claim["arbitration_case_id"] == case_id and claim["evidence_frozen_at"] is not None
            assert await _rows(env, case_id)

    _run(scenario())


def test_lifting_a_freeze_lifts_only_the_one_named(monkeypatch) -> None:
    """lift_freeze reverses one freeze, by its stamp; a claim frozen at another time stays frozen."""

    async def scenario() -> None:
        from types import SimpleNamespace

        from rbac_backend.services.document_relationship_service import (
            DocumentRelationshipError,
            DocumentRelationshipService,
        )

        async with _env(monkeypatch) as env:
            await _prepared(env)
            actor = SimpleNamespace(id="user-superadmin", roles=["superadmin"], organization_id=ORG_A, organizations=[ORG_A], projects=[PROJ_A1])
            service = DocumentRelationshipService(env.db)
            await service.freeze(actor, "claim", CLAIM, reason="Lifecycle freeze")
            stamp = (await _claim(env))["evidence_frozen_at"]
            with pytest.raises(DocumentRelationshipError):
                await service.lift_freeze(
                    actor, "claim", CLAIM, frozen_at=datetime(2026, 1, 1), versions_before={}, reason="Not this one"
                )
            claim = await _claim(env)
            assert claim["evidence_frozen_at"] == stamp
            link = await env.db.entity_document_links.find_one({"target_id": CLAIM, "removed_at": None})
            assert link["frozen_at"] == stamp
            await service.lift_freeze(actor, "claim", CLAIM, frozen_at=stamp, versions_before={}, reason="This one")
            assert (await _claim(env))["evidence_frozen_at"] is None

    _run(scenario())
