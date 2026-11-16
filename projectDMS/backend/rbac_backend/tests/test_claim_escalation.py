"""Escalating claims into an arbitration case, and the evidence controls on a pleading draft.

Held to:

- what is carried is what the claim record establishes, by rule, and every row
  starts as needs-review like any other row in the case;
- an unverified notice period, a cost line without proof and privileged
  material are never carried;
- an overlap with no decided treatment arrives as an open issue with no figure;
- escalating twice adds nothing twice;
- a citation is verified, unverified or unresolved by one computation, and an
  unverified or unresolved one blocks approval;
- a pleaded head missing provision, event, causal link, relief, notice or
  support for its figure blocks approval of a Statement of Claim;
- a draft with no case must be asked for as a scratch draft.
"""

from __future__ import annotations

import copy
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from rbac_backend.models.arbitration_drafting import ArbitrationDraftCreate
from rbac_backend.models.claim_delay import QuantumRecord
from rbac_backend.routers import arbitration_drafting as arbitration_router
from rbac_backend.routers import claim_escalation as escalation_router
from rbac_backend.core.permissions import Permissions
from rbac_backend.services.arbitration_drafting.agents.deterministic import DeterministicArbitrationAgent
from rbac_backend.services.arbitration_drafting.citation_states import (
    citation_blockers,
    classify_citations,
    pleaded_head_gaps,
)
from rbac_backend.services.arbitration_drafting.validator import ArbitrationDraftValidator
from rbac_backend.services.claim_bench import UnlockRequest
from rbac_backend.services.claim_escalation import ClaimEscalation, EscalationRequest, NewCase, status_after
from rbac_backend.services.claim_privileged import ClaimPrivileged, StrategyNoteRequest
from rbac_backend.services.claim_quantum import ClaimQuantum, QuantumLineRequest
from rbac_backend.tests.bench_fakes import FakeDB
from rbac_backend.utils.datetime import now_utc
from rbac_backend.tests.test_claim_bench import CLAIM_ID, ORG, PROJECT, USER, _setup
from rbac_backend.tests.test_claim_delay_quantum import _hindrance
from rbac_backend.tests.test_claim_findings import _decide_all, _documents, _version
from rbac_backend.tests.test_claim_findings_router import Policy, Selection
from rbac_backend.services.claim_delay_lab import ClaimDelayLab, EventDecisionRequest, LinkCreateRequest

ACTOR = SimpleNamespace(id=USER, organization_id=ORG)
DOCS = {CLAIM_ID: {"doc-1", "doc-2"}}
NEW = EscalationRequest(
    claim_ids=[CLAIM_ID], new_case=NewCase(title="ABC Infra v. Authority"), idempotency_key="escalate-test-0001"
)


class _Freezer:
    """The document-relationship freeze, as the claim adapter applies it. The real service
    (transactions, document versions) is exercised against MongoDB in
    integration/test_claim_escalation_safety_mongo.py (4B-36)."""

    def __init__(self, db) -> None:
        self.db = db
        self.preflights: list = []

    async def freeze_preflight(self, actor, target_type, target_id) -> None:
        self.preflights.append(target_id)

    async def freeze(self, actor, target_type, target_id, *, reason, allow_already_frozen=False):
        await self.db.claims.update_one(
            {"_id": target_id, "evidence_frozen_at": None},
            {"$set": {"evidence_frozen_at": now_utc(), "evidence_frozen_by": actor.id, "evidence_freeze_reason": reason}},
        )
        return []


def _escalation(db, service) -> ClaimEscalation:
    return ClaimEscalation(db, service, _Freezer(db))


async def _claim(db: FakeDB) -> dict:
    return await db.claims.find_one({"_id": CLAIM_ID})


async def _worked_claim():
    """A locked board with one cost line that has proof, one that has not, and an undecided overlap."""
    db, service, _ = _setup(capacity="contractor")
    _documents(db)
    db.delay_events.docs.extend(
        [_hindrance("E1", "2026-03-01", "2026-03-31"), _hindrance("C1", "2026-03-20", "2026-04-10", "contractor")]
    )
    await service.open(await _claim(db), USER)
    quantum = ClaimQuantum(db, service)
    for description, amount, records in (
        ("Idle plant", 500000, [QuantumRecord(document_id="doc-1")]),
        ("Unvouched labour", 4500000, []),
    ):
        await quantum.add_line(
            await _claim(db), "H1",
            QuantumLineRequest(board_version=await _version(db), category="direct", description=description, amount=amount, records=records),
            DOCS[CLAIM_ID], USER,
        )
    await _decide_all(service, db)
    return db, service


async def _with_overlap(db, service) -> None:
    lab = ClaimDelayLab(db, service)
    for target, owner in (("E1", "employer"), ("C1", "contractor")):
        record = await lab.load_target("hindrance", target)
        await lab.add_link(
            await _claim(db), "H1",
            LinkCreateRequest(board_version=await _version(db), target_type="hindrance", target_id=target), record, USER,
        )
        await lab.decide_event(
            await _claim(db), "H1", target,
            EventDecisionRequest(board_version=await _version(db), risk_owner=owner, criticality="critical"), USER,
        )


def _rows(db: FakeDB, collection: str) -> list:
    return db[collection].docs


# --------------------------------------------------------------------------- #
# Escalation
# --------------------------------------------------------------------------- #
async def test_the_preview_says_what_is_carried_what_is_weak_and_what_is_left_behind_and_changes_nothing() -> None:
    db, service = await _worked_claim()
    await _with_overlap(db, service)
    before = copy.deepcopy(await _claim(db))
    preview = await _escalation(db, service).preview([await _claim(db)], DOCS)
    (carry,) = preview.claims
    assert preview.can_escalate and preview.blockers == []
    assert carry.rows == {
        "claim-matrix": 1, "quantum-annexures": 1, "issue-matrix": 1, "notice-compliance": 1,
        "chronology-matrix": 4, "document-index": 2,
    }
    assert list(preview.totals) == ["claim-matrix", "notice-compliance", "quantum-annexures", "chronology-matrix", "document-index", "issue-matrix"]
    assert any("not locked" in item for item in carry.weak)
    assert any("overlap" in item and "no decided treatment" in item for item in carry.weak)
    assert any("1 cost line" in item and "INR 45,00,000.00" in item for item in carry.not_carried)
    assert any("28-day notice period" in item and "not been verified" in item for item in carry.not_carried)
    assert carry.status_after == "disputed"
    assert await _claim(db) == before and _rows(db, "arbitration_cases") == []


async def test_escalation_creates_the_case_seeds_needs_review_rows_and_freezes_the_claim() -> None:
    db, service = await _worked_claim()
    await _with_overlap(db, service)
    result = await _escalation(db, service).escalate([await _claim(db)], DOCS, NEW, ACTOR)
    assert result.created_case and result.rows_already_present == 0
    assert result.rows_created["claim-matrix"] == 1 and result.rows_created["document-index"] == 2
    (case,) = _rows(db, "arbitration_cases")
    assert case["claim_ids"] == [CLAIM_ID] and case["project_id"] == PROJECT and case["title"] == "ABC Infra v. Authority"
    (head,) = _rows(db, "arbitration_claim_matrix")
    # Server-controlled review state: a carried row is not an approved row.
    assert head["approval_status"] == "needs_review" and head["verification_status"] == "needs_review"
    assert head["origin"] == {"claim_id": CLAIM_ID, "head_id": "H1"} and head["source_claim_id"] == CLAIM_ID
    assert head["case_id"] == case["_id"] and head["clause_ids"] == ["8.4", "20.1"]
    # Only the line with proof is in the head's figure and in the annexures.
    assert head["amount"] == 500000 and head["evidence_ids"] == ["doc-1"] and head["notice_ids"] == []
    (annexure,) = _rows(db, "arbitration_quantum_annexures")
    assert annexure["description"] == "Idle plant" and annexure["amount"] == 500000
    assert "Unvouched labour" not in repr(_rows(db, "arbitration_quantum_annexures"))
    # The assumed period is not in the notice row; only the dates are.
    (notice,) = _rows(db, "arbitration_notice_compliance")
    assert notice["period_status"] == "not verified against the contract"
    assert "notice_period_days" not in notice and "compliant" not in notice
    assert notice["notice_date"] == datetime(2026, 4, 11)
    # An undecided overlap is an open issue and carries no number of days.
    (issue,) = _rows(db, "arbitration_issue_matrix")
    assert issue["open"] is True and issue["issue_type"] == "concurrent_delay" and issue["status"] == "needs_review"
    assert "days" not in issue and "12" not in issue["issue"]
    exhibits = sorted(row["exhibit_id"] for row in _rows(db, "arbitration_document_index"))
    assert exhibits == ["C-1", "C-2"]
    claim = await _claim(db)
    assert claim["arbitration_case_id"] == case["_id"] and claim["status"] == "disputed"
    assert claim["evidence_frozen_at"] is not None and claim["evidence_freeze_reason"] == "Escalated to arbitration"
    # Frozen evidence makes the board read-only from here on.
    with pytest.raises(HTTPException) as frozen:
        await service.unlock(
            claim, UnlockRequest(board_version=claim["bench"]["board_version"], reason="Reopen to revise a head"), USER
        )
    assert frozen.value.status_code == 409 and "frozen" in frozen.value.detail


async def test_a_verified_notice_period_is_carried_with_its_clause() -> None:
    db, service = await _worked_claim()
    db.claim_notice_requirements.docs.append(
        {
            "_id": "nr1", "organization_id": ORG, "project_id": PROJECT, "claim_type": "eot", "status": "current",
            "period_days": 28, "trigger": "event", "clause_no": "20.1", "quote": "within 28 days",
            "verification": "verified", "verified_by": "user-9", "verified_at": datetime(2026, 5, 1),
            "created_by": USER, "created_at": datetime(2026, 5, 1),
        }
    )
    await _escalation(db, service).escalate([await _claim(db)], DOCS, NEW, ACTOR)
    (notice,) = _rows(db, "arbitration_notice_compliance")
    assert notice["period_status"] == "verified against the contract"
    assert notice["notice_period_days"] == 28 and notice["notice_clause"] == "20.1" and notice["compliant"] is False


async def test_escalating_again_adds_nothing_twice_and_extends_an_existing_case() -> None:
    db, service = await _worked_claim()
    escalation = _escalation(db, service)
    first = await escalation.escalate([await _claim(db)], DOCS, NEW, ACTOR)
    (case,) = _rows(db, "arbitration_cases")
    counts = {name: len(_rows(db, name)) for name in ("arbitration_claim_matrix", "arbitration_document_index", "arbitration_chronology_matrix")}
    again = await escalation.escalate(
        [await _claim(db)], DOCS, EscalationRequest(claim_ids=[CLAIM_ID], case_id=case["_id"]), ACTOR, case
    )
    assert not again.created_case and again.rows_created == {} and again.rows_already_present == sum(first.rows_created.values())
    assert counts == {name: len(_rows(db, name)) for name in counts}
    assert (await db.arbitration_cases.find_one({"_id": case["_id"]}))["claim_ids"] == [CLAIM_ID]


async def test_a_claim_with_no_board_carries_one_head_from_its_register_entry_and_says_so() -> None:
    db, service, _ = _setup(capacity="contractor")
    result = await _escalation(db, service).escalate([await _claim(db)], {}, NEW, ACTOR)
    (carry,) = result.claims
    assert not carry.has_board and any("not been worked head by head" in item for item in carry.weak)
    (head,) = _rows(db, "arbitration_claim_matrix")
    assert head["claim_head"] == "Delayed handover of Site" and head["eot_days"] == 146
    assert (await _claim(db)).get("bench") is None


async def test_claims_that_cannot_be_escalated_are_refused_with_the_reason() -> None:
    db, service, _ = _setup(capacity="contractor", claim={"status": "closed"})
    with pytest.raises(HTTPException) as closed:
        await _escalation(db, service).escalate([await _claim(db)], {}, NEW, ACTOR)
    assert closed.value.status_code == 409 and "is closed" in closed.value.detail
    db2, service2, _ = _setup(capacity="contractor", claim={"arbitration_case_id": "case-other"})
    preview = await _escalation(db2, service2).preview([await _claim(db2)], {})
    assert not preview.can_escalate and "already in another arbitration case" in preview.blockers[0]
    other = {**(await _claim(db2)), "_id": "claim-2", "project_id": "proj-B", "arbitration_case_id": None}
    mixed = ClaimEscalation._blockers([{**(await _claim(db2)), "arbitration_case_id": None}, other], None)
    assert mixed == ["Claims escalated together must belong to one project."]
    assert status_after("submitted") == "disputed" and status_after("draft") == "draft" and status_after("disputed") == "disputed"
    with pytest.raises(HTTPException) as no_target:
        db3, service3, _ = _setup(capacity="contractor")
        await _escalation(db3, service3).escalate([await _claim(db3)], {}, EscalationRequest(claim_ids=[CLAIM_ID]), ACTOR)
    assert no_target.value.status_code == 422


async def test_privileged_material_never_reaches_a_case() -> None:
    db, service = await _worked_claim()
    await ClaimPrivileged(db, service).save_note(
        await _claim(db), StrategyNoteRequest(version=0, weaknesses=["ZEBRA-WEAKNESS"], notes="ZEBRA-FLOOR"), USER
    )
    await _escalation(db, service).escalate([await _claim(db)], DOCS, NEW, ACTOR)
    for name, collection in db._collections.items():
        if name.startswith("arbitration_"):
            assert "ZEBRA" not in repr(collection.docs), name


async def test_a_case_built_by_escalation_reads_only_its_own_claims() -> None:
    db = FakeDB()
    db.claims.docs.extend(
        [
            {"_id": "c1", "organization_id": ORG, "project_id": PROJECT, "title": "Escalated"},
            {"_id": "c2", "organization_id": ORG, "project_id": PROJECT, "title": "Same project, not escalated"},
        ]
    )
    base = {"_id": "case-1", "organization_id": ORG, "project_id": PROJECT}
    def _agent(case: dict) -> DeterministicArbitrationAgent:
        return DeterministicArbitrationAgent(db=db, case=case, draft_id=None, options={}, current_user=ACTOR)

    named = _agent({**base, "claim_ids": ["c1"]})
    assert [row["_id"] for row in await named._find_source_rows("claims")] == ["c1"]
    # A case with no named claims keeps the earlier project-wide behaviour.
    unnamed = _agent(base)
    assert sorted(row["_id"] for row in await unnamed._find_source_rows("claims")) == ["c1", "c2"]


async def test_escalation_routes_ask_for_claim_management_and_the_arbitration_permission(monkeypatch) -> None:
    db, service, _ = _setup(capacity="contractor")

    async def _no_documents(claim, db, current_user):
        return set()

    monkeypatch.setattr(escalation_router, "_claim_documents", _no_documents)
    monkeypatch.setattr(escalation_router, "_claim_notice_documents", _no_documents)

    def _args(policy: Policy) -> dict:
        return {"db": db, "bench": service, "current_user": ACTOR, "policy": policy, "selection": Selection()}

    viewer = Policy(Permissions.CLAIM_VIEW, Permissions.CLAIM_EDIT, Permissions.ARBITRATION_CREATE)
    with pytest.raises(HTTPException) as no_manage:
        await escalation_router.preview_claim_escalation(NEW, **_args(viewer))
    assert no_manage.value.status_code == 403 and Permissions.CLAIM_MANAGE in no_manage.value.detail
    manager = Policy(Permissions.CLAIM_MANAGE)
    with pytest.raises(HTTPException) as no_arbitration:
        await escalation_router.run_claim_escalation(NEW, **_args(manager))
    assert no_arbitration.value.status_code == 403 and Permissions.ARBITRATION_CREATE in no_arbitration.value.detail
    assert _rows(db, "arbitration_cases") == []
    with pytest.raises(HTTPException) as both:
        await escalation_router.run_claim_escalation(
            EscalationRequest(claim_ids=[CLAIM_ID], case_id="x", new_case=NewCase(title="T")),
            **_args(Policy(Permissions.CLAIM_MANAGE, Permissions.ARBITRATION_CREATE)),
        )
    assert both.value.status_code == 422


# --------------------------------------------------------------------------- #
# Evidence controls on a pleading draft
# --------------------------------------------------------------------------- #
LEDGER = [
    {"source_key": "S1", "label": "Letter of Acceptance", "verification_status": "verified"},
    {"source_key": "S2", "label": "Site diary", "verification_status": "needs_review"},
    {"source_key": "S3", "label": "Typed note", "verification_status": "verified", "is_user_supplied": True},
    {"source_key": "S4", "label": "Unused", "verification_status": "verified"},
]
TEXT = "The Works were awarded [S1: LoA]. Handover was late [S2: diary] [S3: note]. See [S1: LoA] and [S9: memo]."


def test_every_citation_is_verified_unverified_or_unresolved_by_one_computation() -> None:
    result = classify_citations(TEXT, LEDGER)
    assert [(item["source_key"], item["state"], item["uses"]) for item in result["items"]] == [
        ("S1", "verified", 2), ("S2", "unverified", 1), ("S3", "unverified", 1), ("S9", "unresolved", 1),
    ]
    assert result["counts"] == {"verified": 1, "unverified": 2, "unresolved": 1, "cited": 4, "ledger": 4}
    assert citation_blockers(result) == [
        "Draft cites sources that are not in the source ledger: S9.",
        "Draft cites sources that are not yet verified: S2, S3.",
    ]
    assert classify_citations("No citations here.", LEDGER)["counts"]["cited"] == 0


def test_the_validator_reports_the_same_states_and_blocks_on_them() -> None:
    context = {"source_ledger": LEDGER, "draft": {"case_id": "case-1", "draft_type": "statement_of_defence"}}
    report = ArbitrationDraftValidator().validation_report(context, TEXT)
    assert report["citations"] == classify_citations(TEXT, LEDGER)
    assert "Draft cites sources that are not yet verified: S2, S3." in report["approval_blockers"]
    assert any("S9" in blocker and "not present in the source ledger" in blocker for blocker in report["approval_blockers"])
    clean = ArbitrationDraftValidator().validation_report(
        {"source_ledger": LEDGER[:1], "draft": {"case_id": "case-1", "draft_type": "statement_of_defence"}},
        "[Evidence required]",
    )
    assert clean["citations"]["counts"]["cited"] == 0 and clean["pleaded_head_gaps"] == []


def _claim_row(**metadata: Any) -> dict:
    complete = {
        "clause_ids": ["8.4"], "facts": "Site handed over late.", "causation": "Earthwork front could not start.",
        "amount_or_days": "146 days", "notice_ids": ["n1"], "evidence_ids": ["d1"],
    }
    return {"citation": "CLM-014/H1", "metadata": {**complete, **metadata}}


def test_a_pleaded_head_must_carry_provision_event_causal_link_relief_notice_and_support() -> None:
    assert pleaded_head_gaps({"claims": [_claim_row()]}) == []
    (gap,) = pleaded_head_gaps({"claims": [_claim_row(clause_ids=[], causation=" ", notice_ids=[], evidence_ids=[])]})
    assert gap["claim"] == "CLM-014/H1"
    assert gap["missing"] == [
        "the contractual provision relied on",
        "the causal link from the event to the loss or delay",
        "a notice record",
        "records supporting the amount or days pleaded",
    ]
    (no_relief,) = pleaded_head_gaps({"claims": [_claim_row(amount_or_days=None, evidence_ids=[])]})
    assert no_relief["missing"] == ["quantified relief"]


def test_incomplete_heads_block_a_statement_of_claim_and_only_that() -> None:
    matrix = {"claims": [_claim_row(notice_ids=[])]}
    for draft_type, blocked in (("statement_of_claim", True), ("statement_of_defence", False)):
        report = ArbitrationDraftValidator().validation_report(
            {"source_ledger": LEDGER[:1], "matrix_context": matrix, "draft": {"case_id": "c", "draft_type": draft_type}},
            "[Evidence required]",
        )
        assert ("Claim CLM-014/H1 does not carry: a notice record." in report["approval_blockers"]) is blocked
        assert bool(report["pleaded_head_gaps"]) is blocked


async def test_a_draft_with_no_case_must_be_asked_for_as_a_scratch_draft() -> None:
    payload = ArbitrationDraftCreate(
        project_id=PROJECT, organization_id=ORG, draft_type="statement_of_claim", party_role="claimant", title="SoC"
    )
    with pytest.raises(HTTPException) as exc:
        await arbitration_router.create_arbitration_draft(
            payload, db=FakeDB(), current_user=ACTOR, policy=Policy(Permissions.ARBITRATION_CREATE)
        )
    assert exc.value.status_code == 422 and "scratch draft" in exc.value.detail
    assert ArbitrationDraftCreate(**{**payload.model_dump(), "scratch": True}).scratch is True


async def test_the_migration_marks_caseless_drafts_as_scratch_and_deletes_nothing() -> None:
    from rbac_backend.migrations import v20261119_0001_claim_escalation_and_scratch_drafts as migration
    from rbac_backend.migrations.catalog import MIGRATIONS

    assert migration.VERSION in [item.version for item in MIGRATIONS]
    db = FakeDB()
    db.arbitration_drafts.docs.extend(
        [
            {"_id": "d1", "title": "No case", "status": "draft"},
            {"_id": "d2", "title": "Empty case", "case_id": "", "status": "under_review"},
            {"_id": "d3", "title": "In a case", "case_id": "case-1", "status": "approved"},
        ]
    )
    before = copy.deepcopy(db.arbitration_drafts.docs)
    dry = await migration.upgrade(db, True)
    assert dry.status == "dry_run" and db.arbitration_drafts.docs == before
    assert dry.operations[-1]["matched"] == 2 and not getattr(dry, "warnings", [])
    applied = await migration.upgrade(db, False)
    assert applied.status == "applied"
    marked = {doc["_id"]: doc.get("is_scratch") for doc in db.arbitration_drafts.docs}
    assert marked == {"d1": True, "d2": True, "d3": None}
    assert [doc["status"] for doc in db.arbitration_drafts.docs] == ["draft", "under_review", "approved"]
    assert (await migration.upgrade(db, False)).operations[-1]["matched"] == 0


# --------------------------------------------------------------------------- #
# Pleadings on skills
# --------------------------------------------------------------------------- #
class _Model:
    available = True

    def __init__(self) -> None:
        self.prompts: list = []

    async def generate(self, prompt: str, **_: Any) -> str:
        self.prompts.append(prompt)
        return '{"facts": "The Site was handed over late [S1: LoA]."}'


def _draft_context() -> dict:
    return {
        "draft": {"_id": "d1", "draft_type": "statement_of_claim", "title": "SoC", "party_role": "claimant", "project_id": PROJECT},
        "source_ledger": [{"source_key": "S1", "citation": "LoA", "label": "Letter of Acceptance", "verification_status": "verified"}],
        "matrix_context": {}, "claim_heads": [], "paragraph_responses": [], "missing_evidence": [],
    }


async def test_an_unpublished_pleadings_skill_is_never_compiled_and_a_published_one_is_recorded() -> None:
    from rbac_backend.services.arbitration_drafting.service import ArbitrationDraftingService, stable_generation_input_hash
    from rbac_backend.services.skills.system_skills import PLEADINGS_SKILL_KEY, system_skill_version

    db = FakeDB()
    db.projects.docs.append({"_id": PROJECT, "organization_id": ORG, "contractual_capacity": "contractor"})
    draft = {"_id": "d1", "organization_id": ORG, "project_id": PROJECT}
    stored = system_skill_version(PLEADINGS_SKILL_KEY).model_dump(by_alias=True, exclude={"id"})
    db.skill_versions.docs.append({**stored, "status": "draft"})
    service = ArbitrationDraftingService(db)
    assert await service._pleading_skill(draft) is None
    db.skill_versions.docs[0]["status"] = "published"
    skill = await service._pleading_skill(draft)
    assert skill["skill_key"] == PLEADINGS_SKILL_KEY and skill["role"] == "contractor" and skill["block"]
    assert skill["content_hash"] == stored["content_hash"]
    # The Engineer is not a party to the pleading: the skill does not support that role, so it is not used.
    db.projects.docs[0]["contractual_capacity"] = "engineer"
    assert await service._pleading_skill(draft) is None
    # The skill is part of what a draft was generated from, and only when one is in use.
    context = _draft_context()
    without = stable_generation_input_hash(context)
    assert stable_generation_input_hash({**context, "pleading_skill": skill}) != without
    assert stable_generation_input_hash({**context, "pleading_skill": None}) == without


async def test_the_drafting_prompt_is_unchanged_without_a_skill_and_carries_the_method_with_one() -> None:
    from rbac_backend.services.arbitration_drafting.llm_generator import LLM_DRAFT_PROMPT_VERSION, LLMDraftGenerator

    plain_model = _Model()
    plain = await LLMDraftGenerator(generator=plain_model, model="m").generate(_draft_context())
    (plain_prompt,) = plain_model.prompts
    assert plain_prompt.startswith("You are an arbitration counsel drafting a construction pleading.")
    assert "+skill" not in str(plain.get("ai_prompt_version"))
    assert "skill" not in plain["structured_output"]

    skilled_model = _Model()
    skill = {"skill_key": "indian-infra-arbitration-pleadings", "version": "1.0.0", "content_hash": "ab" * 32, "role": "contractor", "block": "<skill>METHOD TEXT</skill>"}
    skilled = await LLMDraftGenerator(generator=skilled_model, model="m").generate({**_draft_context(), "pleading_skill": skill})
    (prompt,) = skilled_model.prompts
    # Method first, then the unchanged task with its hard rules.
    assert prompt.startswith("<skill>METHOD TEXT</skill>") and plain_prompt.strip() in prompt
    assert "Do NOT introduce any new amount, date, party name, clause number, or citation token." in prompt
    if skilled["structured_output"].get("generation_mode") == "llm":
        assert skilled["ai_prompt_version"] == f"{LLM_DRAFT_PROMPT_VERSION}+skill.abababababab"
        assert skilled["structured_output"]["skill"] == {key: skill[key] for key in ("skill_key", "version", "content_hash", "role")}
    assert LLMDraftGenerator.prompt_version_for(None) == LLM_DRAFT_PROMPT_VERSION


# --------------------------------------------------------------------------- #
# 4B-33: the legal-review gate counts pleaded-head blockers
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "blocker",
    [
        "Claim CLM-014/H1 does not carry: the contractual provision relied on; the event.",
        "A Statement of Claim needs at least one approved claim head in this case's claim matrix, and none is approved.",
        "2 claim heads in this case are still awaiting approval. Approve or remove them before this Statement of Claim can be approved.",
    ],
)
async def test_the_legal_review_gate_blocks_on_pleaded_head_gaps(blocker: str) -> None:
    """Before 4B-33 the gate matched keywords; these messages name none of them, so it passed them."""
    from rbac_backend.services.arbitration_drafting.workflow_validation import ArbitrationValidationOrchestrator

    version = {
        "draft_id": "d1", "version": 1, "full_markdown": "# STATEMENT OF CLAIM\n\n1. Text. [Evidence required]",
        "source_ledger": [], "structured_output": {"approval_blockers": [blocker]},
        "validation_status": "blocked",
    }
    report = await ArbitrationValidationOrchestrator().evaluate(version, branches=["legal_structure"])
    assert report["status"] == "blocked"
    assert [issue["message"] for issue in report["blockers"]] == [blocker]


async def test_the_legal_review_gate_still_ignores_unrelated_warnings() -> None:
    from rbac_backend.services.arbitration_drafting.workflow_validation import ArbitrationValidationOrchestrator

    version = {
        "draft_id": "d1", "version": 1, "full_markdown": "# STATEMENT OF CLAIM\n\n1. Text.",
        "source_ledger": [], "structured_output": {"approval_blockers": [], "validation_warnings": ["Duplicate claim heads."]},
    }
    report = await ArbitrationValidationOrchestrator().evaluate(version, branches=["legal_structure"])
    assert report["blockers"] == []


# --------------------------------------------------------------------------- #
# 4B-34: the claim's notice letters become each head's notice record
# --------------------------------------------------------------------------- #
async def test_the_preview_says_when_no_notice_letter_is_linked_and_carries_one_that_is() -> None:
    db, service = await _worked_claim()
    escalation = _escalation(db, service)
    without = await escalation.preview([await _claim(db)], DOCS)
    assert any("No notice letter is linked to the claim" in item for item in without.claims[0].weak)

    with_notice = await escalation.preview([await _claim(db)], DOCS, None, {CLAIM_ID: {"doc-1"}})
    assert not any("No notice letter is linked" in item for item in with_notice.claims[0].weak)


async def test_a_notice_letter_the_caller_cannot_see_is_not_carried() -> None:
    db, service = await _worked_claim()
    await _escalation(db, service).escalate([await _claim(db)], DOCS, NEW, ACTOR, None, {CLAIM_ID: {"doc-1", "doc-secret"}})
    (row,) = _rows(db, "arbitration_claim_matrix")
    assert row["notice_ids"] == ["doc-1"]
