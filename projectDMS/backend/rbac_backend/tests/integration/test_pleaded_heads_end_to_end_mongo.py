"""4B-33 (B2): escalation -> matrix -> source ledger -> pleading validator, end to end.

The audit found that every Statement of Claim pleading an approved claim head
was blocked: escalation wrote the head's provision, event, causal link and
relief onto the matrix row, but the source-ledger builder never copied them
into the metadata the pleaded-head check reads. The unit tests passed because
they built that metadata by hand. And a case with no approved heads at all
produced no gaps, so it passed trivially.

Here nothing is hand-built:

1. a real claim board is escalated by ClaimEscalation (as the escalation tests do);
2. the stored records are loaded into a real MongoDB replica set;
3. the head is approved through the case workspace's own review path, by a
   legal reviewer and a different quantum reviewer;
4. a Statement of Claim version is stored through the real drafting service
   (create_manual_version), which builds the real source ledger and runs the
   real validator. Generation adds a case-readiness gate in front of the same
   two steps; that gate is not under test here.

Opt-in: ``CLAIM_ESCALATION_MONGODB_URI`` (a disposable replica set). Run in
CI's real-Mongo step.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any, AsyncIterator, Dict, List

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from rbac_backend.models.arbitration_drafting import (
    ArbitrationDraftCreate,
    ArbitrationMatrixReviewRequest,
    ArbitrationMatrixRowCreate,
)
from rbac_backend.services.arbitration_drafting.case_workspace import ArbitrationCaseWorkspaceService
from rbac_backend.services.arbitration_drafting.citation_states import NO_APPROVED_HEADS, is_pleaded_head_blocker
from rbac_backend.services.arbitration_drafting.service import ArbitrationDraftingService
from rbac_backend.tests.test_claim_bench import ORG, PROJECT
from rbac_backend.tests.test_claim_escalation import ACTOR, DOCS, NEW, _claim, _escalation, _worked_claim

pytestmark = pytest.mark.integration

MONGODB_URI_ENV = "CLAIM_ESCALATION_MONGODB_URI"

LEGAL = SimpleNamespace(id="legal-reviewer", organization_id=ORG, roles=["legal_reviewer"])
QUANTUM = SimpleNamespace(id="quantum-reviewer", organization_id=ORG, roles=["quantum_reviewer"])
DRAFTER = SimpleNamespace(id="drafter", organization_id=ORG, roles=["orgadmin"])

PLEADING_TEXT = "# STATEMENT OF CLAIM\n\n1. The Claimant claims as set out below. [Evidence required]\n"

ELEMENT_GAPS = ("the contractual provision relied on", "the event", "the causal link from the event to the loss or delay")


def _uri() -> str:
    uri = os.getenv(MONGODB_URI_ENV)
    if not uri:
        pytest.skip(f"set {MONGODB_URI_ENV} to a disposable MongoDB replica set")
    return uri


@asynccontextmanager
async def _db() -> AsyncIterator[Any]:
    client = AsyncIOMotorClient(_uri(), serverSelectionTimeoutMS=5_000)
    hello = await client.admin.command("hello")
    if not hello.get("setName") or not hello.get("isWritablePrimary"):
        client.close()
        pytest.fail(f"{MONGODB_URI_ENV} is not a writable replica set")
    name = f"pleaded_heads_{uuid.uuid4().hex[:12]}"
    try:
        yield client[name]
    finally:
        await client.drop_database(name)
        client.close()


async def _escalated_case(db: Any, notices: Dict[str, Any] | None = None, causation: str | None = None) -> str:
    """Escalate a worked claim with the real service, then load every record into MongoDB.

    ``causation``: decide the head's causation element "supported" with this note first,
    as a person would on the Claim Bench, so the causal link is carried.
    """
    fake, service = await _worked_claim()
    if causation:
        from rbac_backend.services.claim_bench import ElementDecisionRequest

        claim = await _claim(fake)
        version = (await service.get(claim)).bench.board_version
        await service.decide_element(
            claim, "H1", "causation", ElementDecisionRequest(board_version=version, state="supported", note=causation), "user-1"
        )
    result = await _escalation(fake, service).escalate([await _claim(fake)], DOCS, NEW, ACTOR, None, notices)
    for name, collection in fake._collections.items():
        if collection.docs:
            await db[name].insert_many([dict(doc) for doc in collection.docs])
    return str(result.case_id)


async def _approve_claim_row(db: Any, case_id: str, row_id: str) -> None:
    workspace = ArbitrationCaseWorkspaceService(db)
    for role, actor in (("legal", LEGAL), ("quantum", QUANTUM)):
        await workspace.review_matrix_row(
            case_id, "claim-matrix", row_id, ArbitrationMatrixReviewRequest(action="approve", reviewer_role=role), actor
        )


async def _statement_of_claim(db: Any, case_id: str) -> Dict[str, Any]:
    service = ArbitrationDraftingService(db)
    draft = await service.create_draft(
        ArbitrationDraftCreate(
            project_id=PROJECT, organization_id=ORG, draft_type="statement_of_claim",
            party_role="claimant", title="Statement of Claim", case_id=case_id,
        ),
        DRAFTER,
    )
    draft_id = str(draft.get("_id") or draft.get("id"))
    # A version written by hand goes through the same real context builder and
    # validator as a generated one, without generation's case-readiness gate,
    # which is not what this suite tests.
    await service.create_manual_version(draft_id, PLEADING_TEXT, DRAFTER)
    version = await db.arbitration_draft_versions.find_one({"draft_id": draft_id}, sort=[("version", -1)])
    assert version is not None, "no version stored"
    return version


def _present(value: Any) -> bool:
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return bool(str(value or "").strip())


def _expected_missing(row: Dict[str, Any]) -> List[str]:
    """What the pleaded-head rule should report for this stored row, worked out independently."""
    missing = [
        label
        for field, label in (("clause_ids", ELEMENT_GAPS[0]), ("facts", ELEMENT_GAPS[1]), ("causation", ELEMENT_GAPS[2]))
        if not _present(row.get(field))
    ]
    pleads_figure = _present(row.get("amount_or_days"))
    if not pleads_figure and not _present(row.get("relief")):
        missing.append("quantified relief")
    if not _present(row.get("notice_ids")):
        missing.append("a notice record")
    if pleads_figure and not _present(row.get("evidence_ids")):
        missing.append("records supporting the amount or days pleaded")
    return missing


def _blockers(version: Dict[str, Any]) -> List[str]:
    structured = version.get("structured_output") or {}
    return [str(item) for item in structured.get("approval_blockers") or []]


def _head_gaps(version: Dict[str, Any]) -> List[Dict[str, Any]]:
    return list((version.get("structured_output") or {}).get("pleaded_head_gaps") or [])


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_an_escalated_and_approved_head_reaches_the_validator_with_its_elements() -> None:
    async def scenario() -> None:
        async with _db() as db:
            case_id = await _escalated_case(db)
            (row,) = await db.arbitration_claim_matrix.find({"case_id": case_id}).to_list(length=None)
            # Escalation writes the provision and the event; the causal link only
            # when the head's causation element was decided "supported".
            assert row["clause_ids"] and row["facts"], "escalation wrote the provision and the event"
            await _approve_claim_row(db, case_id, row["_id"])

            version = await _statement_of_claim(db, case_id)
            gaps = _head_gaps(version)
            assert len(gaps) == 1, gaps
            # The validator reports exactly what the stored row lacks: no more (B2) and no less.
            assert gaps[0]["missing"] == _expected_missing(row), (gaps[0]["missing"], _expected_missing(row))
            for element in ("the contractual provision relied on", "the event"):
                assert element not in gaps[0]["missing"], element
            # Still reported, honestly: no letter is linked to this claim with the role "notice".
            assert "a notice record" in gaps[0]["missing"]
            assert NO_APPROVED_HEADS not in _blockers(version)
            assert not any("awaiting approval" in item for item in _blockers(version))

    _run(scenario())


def test_an_escalated_claim_with_a_linked_notice_reaches_a_statement_of_claim_with_no_head_gaps() -> None:
    """4B-34 proof: the claim's notice letter (role "notice") becomes the head's notice record."""

    async def scenario() -> None:
        async with _db() as db:
            claim_id = next(iter(DOCS))
            # doc-2 is linked to the claim as its notice; doc-9 is a notice the caller cannot see.
            case_id = await _escalated_case(
                db, {claim_id: {"doc-2", "doc-9"}},
                causation="Handover of the Site was late, so the works started late on the critical path.",
            )
            (row,) = await db.arbitration_claim_matrix.find({"case_id": case_id}).to_list(length=None)
            assert row["notice_ids"] == ["doc-2"], row["notice_ids"]
            notice_row = await db.arbitration_notice_compliance.find_one({"case_id": case_id})
            assert notice_row is not None and notice_row.get("notice_document_ids") == ["doc-2"], notice_row
            assert _expected_missing(row) == [], _expected_missing(row)
            await _approve_claim_row(db, case_id, row["_id"])

            version = await _statement_of_claim(db, case_id)
            assert _head_gaps(version) == [], _head_gaps(version)
            assert not any(is_pleaded_head_blocker(item) for item in _blockers(version)), _blockers(version)

    _run(scenario())


def test_a_head_entered_by_hand_with_its_elements_has_no_element_gaps() -> None:
    async def scenario() -> None:
        async with _db() as db:
            case_id = await _escalated_case(db)
            await db.arbitration_claim_matrix.delete_many({"case_id": case_id})
            workspace = ArbitrationCaseWorkspaceService(db)
            row = await workspace.create_matrix_row(
                case_id, "claim-matrix",
                ArbitrationMatrixRowCreate(
                    claim_no="C-2", claim_head="Idling of plant", amount_or_days="INR 5,00,000",
                    facts="Plant stood idle 12 to 30 March.", causation="Access to the work front was withheld.",
                    relief="Payment of INR 5,00,000", clause_ids=["8.4"], notice_ids=["notice-1"],
                    evidence_ids=["doc-1"],
                ),
                DRAFTER,
            )
            await _approve_claim_row(db, case_id, row["_id"])
            version = await _statement_of_claim(db, case_id)
            assert _head_gaps(version) == [], _head_gaps(version)
            assert not any(is_pleaded_head_blocker(item) for item in _blockers(version)), _blockers(version)

    _run(scenario())


def test_a_head_entered_by_hand_without_a_provision_is_reported_as_missing_it() -> None:
    async def scenario() -> None:
        async with _db() as db:
            case_id = await _escalated_case(db)
            await db.arbitration_claim_matrix.delete_many({"case_id": case_id})
            workspace = ArbitrationCaseWorkspaceService(db)
            row = await workspace.create_matrix_row(
                case_id, "claim-matrix",
                ArbitrationMatrixRowCreate(
                    claim_no="C-3", claim_head="Idling", amount_or_days="INR 5,00,000",
                    facts="Plant stood idle.", causation="Access withheld.", relief="Payment",
                    notice_ids=["notice-1"], evidence_ids=["doc-1"],
                ),
                DRAFTER,
            )
            await _approve_claim_row(db, case_id, row["_id"])
            (gap,) = _head_gaps(await _statement_of_claim(db, case_id))
            assert gap["missing"] == ["the contractual provision relied on"], gap

    _run(scenario())


def test_a_case_with_no_approved_head_cannot_carry_a_statement_of_claim() -> None:
    """Before: zero approved heads produced zero gaps, and the check passed trivially."""

    async def scenario() -> None:
        async with _db() as db:
            case_id = await _escalated_case(db)  # its head is still awaiting review
            version = await _statement_of_claim(db, case_id)
            blockers = _blockers(version)
            assert NO_APPROVED_HEADS in blockers, blockers
            assert any("1 claim head in this case is still awaiting approval" in item for item in blockers), blockers

            await db.arbitration_claim_matrix.delete_many({"case_id": case_id})
            empty = _blockers(await _statement_of_claim(db, case_id))
            assert NO_APPROVED_HEADS in empty
            assert not any("awaiting approval" in item for item in empty)

    _run(scenario())


def test_a_statement_of_defence_is_not_held_to_the_claim_head_rules() -> None:
    async def scenario() -> None:
        async with _db() as db:
            case_id = await _escalated_case(db)
            service = ArbitrationDraftingService(db)
            draft = await service.create_draft(
                ArbitrationDraftCreate(
                    project_id=PROJECT, organization_id=ORG, draft_type="statement_of_defence",
                    party_role="respondent", title="Statement of Defence", case_id=case_id,
                ),
                DRAFTER,
            )
            draft_id = str(draft.get("_id") or draft.get("id"))
            await service.create_manual_version(draft_id, PLEADING_TEXT, DRAFTER)
            version = await db.arbitration_draft_versions.find_one({"draft_id": draft_id}, sort=[("version", -1)])
            assert not any(is_pleaded_head_blocker(item) for item in _blockers(version))

    _run(scenario())


def test_a_case_created_by_the_workspace_can_be_found_listed_updated_and_soft_deleted() -> None:
    """Found by this suite, reproduced on 34d664a: create_case stores deleted_at: null, and the
    case queries asked for {"$exists": False}, so on a real MongoDB every such case answered 404."""

    async def scenario() -> None:
        from rbac_backend.models.arbitration_drafting import ArbitrationCaseCreate, ArbitrationCaseUpdate

        async with _db() as db:
            workspace = ArbitrationCaseWorkspaceService(db)
            case = await workspace.create_case(
                ArbitrationCaseCreate(project_id=PROJECT, organization_id=ORG, title="ABC Infra v. Authority"), DRAFTER
            )
            stored = await db.arbitration_cases.find_one({"_id": case["_id"]})
            assert "deleted_at" in stored and stored["deleted_at"] is None  # the shape that broke the lookup
            assert (await workspace.get_case(str(case["_id"])))["_id"] == case["_id"]
            listed = await workspace.list_cases({"organization_id": ORG}, {"project_id": PROJECT})
            assert [item["_id"] for item in listed] == [case["_id"]]
            updated = await workspace.update_case(str(case["_id"]), ArbitrationCaseUpdate(title="Renamed"), DRAFTER)
            assert updated["title"] == "Renamed"

            await workspace.soft_delete_case(str(case["_id"]), DRAFTER)
            with pytest.raises(Exception) as gone:
                await workspace.get_case(str(case["_id"]))
            assert getattr(gone.value, "status_code", None) == 404
            assert await workspace.list_cases({"organization_id": ORG}, {"project_id": PROJECT}) == []

    _run(scenario())
