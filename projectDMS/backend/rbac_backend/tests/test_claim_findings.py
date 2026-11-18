"""Two Chairs and the Submission Pack.

What these tests hold Phase 3 to:

- a determination is official and a simulation is not, and they are different
  records in different collections;
- only an Engineer or Employer workspace authors a determination, only with
  authority, and never approved by the person who made its findings;
- an approved or recorded determination does not change;
- nothing here writes a contractual fact: the claim and its board are the same
  after as before;
- privileged material stays behind its wall: one module names its collection,
  and an export never carries it;
- a submission is composed from a locked board, says nothing the record does
  not, and never states an unverified notice period;
- skill text that goes beyond the record is discarded.
"""

from __future__ import annotations

import copy
import io
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import pytest
from fastapi import HTTPException

from rbac_backend.core.permissions import (
    CLIENT_DMS_PERMISSIONS,
    EXPLICIT_GRANT_ONLY_PERMISSIONS,
    SUPERADMIN_EXCLUDED_PERMISSIONS,
    Permissions,
)
from rbac_backend.initial_data.default_roles import DEFAULT_ROLES
from rbac_backend.models.claim import ELEMENT_KEYS, ClaimHead, HeadElement, NoticeAssessment
from rbac_backend.models.claim_delay import QuantumRecord
from rbac_backend.models.claim_findings import (
    INTERNAL_ANALYSIS_LABEL,
    INTERNAL_NOTE_LABEL,
    SECTION_ORDER,
    InformationRequest,
)
from rbac_backend.services.claim_bench import (
    BoardVersionRequest,
    ClaimBenchService,
    ElementDecisionRequest,
    HeadPatchRequest,
    UnlockRequest,
)
from rbac_backend.services.claim_determinations import (
    DETERMINATIONS,
    ClaimDeterminations,
    DeterminationCreateRequest,
    DeterminationSubmitRequest,
    DeterminationVersionRequest,
    HeadFindingRequest,
    hold_to_record,
    record_proposal,
)
from rbac_backend.models.claim_skill_outputs import ClaimDeterminationOutput
from rbac_backend.services.claim_privileged import (
    PRIVILEGED,
    ClaimPrivileged,
    SimulationRequest,
    StrategyNoteRequest,
)
from rbac_backend.services.claim_quantum import ClaimQuantum, QuantumLineRequest
from rbac_backend.services.claim_submission import (
    DRAFT_MARK,
    RESERVATION_WORDING,
    ClaimSubmissions,
    ComposeRequest,
    SubmissionRecord,
    compose_record_sections,
    evidence_schedule,
    format_amount,
    numbers_in,
    validate_section_text,
)
from rbac_backend.tests.bench_fakes import FakeDB
from rbac_backend.tests.test_claim_bench import CLAIM_ID, ORG, PROJECT, USER, StubLLM, _setup

REVIEWER = "user-2"
DOCS = {"doc-1", "doc-2"}


async def _claim(db: FakeDB) -> dict:
    return await db.claims.find_one({"_id": CLAIM_ID})


async def _version(db: FakeDB) -> int:
    return (await _claim(db))["bench"]["board_version"]


async def _decide_all(service: ClaimBenchService, db: FakeDB, state: str = "supported") -> None:
    for key in ELEMENT_KEYS:
        await service.decide_element(
            await _claim(db), "H1", key, ElementDecisionRequest(board_version=await _version(db), state=state), USER
        )
    await service.patch_head(
        await _claim(db), "H1", HeadPatchRequest(board_version=await _version(db), classification="compensable"), USER
    )


async def _locked(service: ClaimBenchService, db: FakeDB) -> None:
    await service.open(await _claim(db), USER)
    await _decide_all(service, db)
    await service.lock(await _claim(db), BoardVersionRequest(board_version=await _version(db)), USER)


def _documents(db: FakeDB) -> None:
    db.documents.docs.extend(
        [
            {"_id": "doc-1", "subject": "Plant log March 2026", "date": "2026-03-31", "letter_no": "PL/03"},
            {"_id": "doc-2", "subject": "Handover request", "date": "2026-03-05", "letter_no": "HO/11"},
        ]
    )


def _refused(exc: pytest.ExceptionInfo, status: int, *fragments: str) -> None:
    assert exc.value.status_code == status
    for fragment in fragments:
        assert fragment in str(exc.value.detail)


async def _draft(db: FakeDB, service: ClaimBenchService, user: str = USER):
    determinations = ClaimDeterminations(db, service, approver_rule=_only_reviewer)
    view = await determinations.create(
        await _claim(db), DeterminationCreateRequest(origin="authored"), user, holds_authority=True
    )
    return determinations, view


async def _found(determinations: ClaimDeterminations, db: FakeDB, view, user: str = USER, **over: Any):
    request = {"result": "partial", "days": 90, "findings": "Employer delay established for part of the period.", **over}
    return await determinations.set_finding(
        await _claim(db), view.determination.id, "H1",
        HeadFindingRequest(version=view.determination.version, **request), user,
    )


# --------------------------------------------------------------------------- #
# Permissions
# --------------------------------------------------------------------------- #
def test_determination_authority_and_privileged_access_are_granted_by_name_only() -> None:
    for permission in (Permissions.CLAIM_DETERMINE, Permissions.CLAIM_STRATEGY_READ):
        assert permission in CLIENT_DMS_PERMISSIONS and permission in EXPLICIT_GRANT_ONLY_PERMISSIONS
        holders = {role["_id"] for role in DEFAULT_ROLES if permission in (role.get("permissions") or [])}
        # 4B-10 (plan D-12): in no default role; an administrator grants them by name.
        assert holders == set()
        assert permission in SUPERADMIN_EXCLUDED_PERMISSIONS


# --------------------------------------------------------------------------- #
# Determinations
# --------------------------------------------------------------------------- #
async def test_a_contractor_workspace_cannot_author_a_determination_even_with_authority() -> None:
    db, service, _ = _setup(capacity="contractor")
    await service.open(await _claim(db), USER)
    with pytest.raises(HTTPException) as exc:
        await _draft(db, service)
    _refused(exc, 409, "acts as Contractor")
    listing = await ClaimDeterminations(db, service).list(await _claim(db), holds_authority=True)
    assert not listing.can_author and "acts as Contractor" in listing.cannot_author_reason
    assert await db[DETERMINATIONS].count_documents({}) == 0


async def test_an_engineer_workspace_needs_authority_and_a_stated_capacity() -> None:
    db, service, _ = _setup(capacity="engineer")
    await service.open(await _claim(db), USER)
    with pytest.raises(HTTPException) as exc:
        await ClaimDeterminations(db, service).create(
            await _claim(db), DeterminationCreateRequest(origin="authored"), USER, holds_authority=False
        )
    _refused(exc, 403, "determination authority")
    db2, service2, _ = _setup(capacity=None)
    await service2.open(await _claim(db2), USER)
    with pytest.raises(HTTPException) as unstated:
        await _draft(db2, service2)
    _refused(unstated, 409, "which party")


async def test_without_a_published_skill_the_proposal_is_the_record_and_never_an_entitlement() -> None:
    llm = StubLLM(reply={"result": "full", "findings": "Made out."})
    db, service, _ = _setup(capacity="engineer", llm=llm, published=False)
    await service.open(await _claim(db), USER)
    determinations, view = await _draft(db, service)
    view = await determinations.propose(
        await _claim(db), view.determination.id, DeterminationVersionRequest(version=view.determination.version), USER
    )
    (finding,) = view.determination.heads
    assert llm.prompts == []
    assert finding.proposal.proposed_by == "record_check" and finding.proposal.result == "insufficient_information"
    assert finding.proposal.information_required  # the open elements are listed
    # A proposal is not a finding.
    assert finding.result is None and finding.days is None and finding.amount is None
    assert view.blockers and "No finding yet" in view.blockers[0]


async def test_a_skill_cannot_propose_full_over_an_element_the_board_does_not_hold_as_supported() -> None:
    llm = StubLLM(reply={"result": "full", "findings": "Made out.", "clause_refs": ["8.4", "99.9"]})
    db, service, _ = _setup(capacity="engineer", llm=llm, published=True)
    await service.open(await _claim(db), USER)
    determinations, view = await _draft(db, service)
    view = await determinations.propose(
        await _claim(db), view.determination.id, DeterminationVersionRequest(version=view.determination.version), USER
    )
    proposal = view.determination.heads[0].proposal
    assert proposal.proposed_by == "skill" and proposal.result == "partial"
    assert "Record check" in proposal.findings
    (prompt,) = llm.prompts
    assert "position of the engineer" in prompt and "Never state a number of days or an amount" in prompt


def test_hold_to_record_lets_full_stand_only_when_every_element_is_supported() -> None:
    head = ClaimHead(head_id="H1", title="Handover", clause_refs=["8.4"])
    for key in ELEMENT_KEYS:
        head.elements[key] = HeadElement(state="supported")
    kept = hold_to_record(ClaimDeterminationOutput(result="full", findings="Made out.", clause_refs=["8.4", "1.1"]), head)
    assert kept.result == "full" and kept.clause_refs == ["8.4"]
    assert record_proposal(head).result == "insufficient_information"


async def test_a_finding_is_validated_against_the_head() -> None:
    db, service, _ = _setup(capacity="engineer")
    await service.open(await _claim(db), USER)
    determinations, view = await _draft(db, service)
    with pytest.raises(HTTPException) as figure_on_none:
        await _found(determinations, db, view, result="none", days=10)
    _refused(figure_on_none, 422, "cannot carry days")
    with pytest.raises(HTTPException) as no_information:
        await _found(determinations, db, view, result="insufficient_information", days=None)
    _refused(no_information, 422, "what information is required")
    view = await _found(
        determinations, db, view, result="insufficient_information", days=None,
        information_required=[InformationRequest(item="As-built programme", consequence="The head cannot be determined without it.")],
    )
    assert view.determination.heads[0].information_required[0].item == "As-built programme"


async def _only_reviewer(user_id: str) -> bool:
    """The reviewer check the router supplies (PickerService.is_eligible); here, REVIEWER alone."""
    return user_id == REVIEWER


async def test_approval_is_by_a_second_person_and_then_the_determination_does_not_change() -> None:
    db, service, _ = _setup(capacity="engineer")
    await service.open(await _claim(db), USER)
    before = copy.deepcopy(await _claim(db))
    determinations, view = await _draft(db, service)
    det_id = view.determination.id
    with pytest.raises(HTTPException) as unfinished:
        await determinations.submit(
            await _claim(db), det_id, DeterminationSubmitRequest(version=view.determination.version, reviewer_id=REVIEWER), USER, reviewer_eligible=_only_reviewer)
    _refused(unfinished, 409, "No finding yet")
    view = await _found(determinations, db, view)
    with pytest.raises(HTTPException) as self_review:
        await determinations.submit(
            await _claim(db), det_id, DeterminationSubmitRequest(version=view.determination.version, reviewer_id=USER), USER, reviewer_eligible=_only_reviewer)
    _refused(self_review, 422, "someone other")
    view = await determinations.submit(
        await _claim(db), det_id, DeterminationSubmitRequest(version=view.determination.version, reviewer_id=REVIEWER), USER, reviewer_eligible=_only_reviewer)
    assert view.determination.status == "in_review" and not view.editable
    with pytest.raises(HTTPException) as in_review:
        await _found(determinations, db, view)
    _refused(in_review, 409, "with its reviewer")
    with pytest.raises(HTTPException) as own:
        await determinations.approve(await _claim(db), det_id, DeterminationVersionRequest(version=view.determination.version), USER)
    _refused(own, 403, "made its findings")
    view = await determinations.approve(
        await _claim(db), det_id, DeterminationVersionRequest(version=view.determination.version), REVIEWER
    )
    assert view.determination.status == "approved" and view.determination.decided_by == REVIEWER
    approved = copy.deepcopy(await db[DETERMINATIONS].find_one({"_id": det_id}))
    with pytest.raises(HTTPException) as immutable:
        await _found(determinations, db, view, result="full")
    _refused(immutable, 409, "cannot be changed", "new revision")
    with pytest.raises(HTTPException) as again:
        await determinations.approve(await _claim(db), det_id, DeterminationVersionRequest(version=view.determination.version), REVIEWER)
    _refused(again, 409, "in review")
    # A revision is a new record; the approved one is exactly as it was.
    revision = await determinations.revise(await _claim(db), det_id, USER, holds_authority=True)
    assert revision.determination.revision == 2 and revision.determination.supersedes_id == det_id
    # D-13 (4B-26): a revision keeps the reasons but clears every result, to be decided again.
    assert revision.determination.status == "draft" and revision.determination.heads[0].result is None
    assert revision.determination.heads[0].decided_by is None
    assert await db[DETERMINATIONS].find_one({"_id": det_id}) == approved
    # No contractual fact was written at any point.
    assert await _claim(db) == before


async def test_a_returned_determination_needs_a_reason_and_can_be_reworked() -> None:
    db, service, _ = _setup(capacity="employer")
    await service.open(await _claim(db), USER)
    determinations, view = await _draft(db, service)
    view = await _found(determinations, db, view)
    view = await determinations.submit(
        await _claim(db), view.determination.id,
        DeterminationSubmitRequest(version=view.determination.version, reviewer_id=REVIEWER), USER, reviewer_eligible=_only_reviewer)
    with pytest.raises(HTTPException) as silent:
        await determinations.send_back(
            await _claim(db), view.determination.id, DeterminationVersionRequest(version=view.determination.version), REVIEWER
        )
    _refused(silent, 422, "what needs to change")
    view = await determinations.send_back(
        await _claim(db), view.determination.id,
        DeterminationVersionRequest(version=view.determination.version, comment="Days not explained."), REVIEWER,
    )
    assert view.determination.status == "returned" and view.editable
    assert view.determination.issued_by == "employer"
    assert [event.action for event in view.determination.history] == ["created", "submitted", "returned"]


async def test_only_one_determination_is_prepared_at_a_time() -> None:
    db, service, _ = _setup(capacity="engineer")
    await service.open(await _claim(db), USER)
    await _draft(db, service)
    with pytest.raises(HTTPException) as second:
        await _draft(db, service)
    _refused(second, 409, "already being prepared")


async def test_a_received_determination_is_tied_to_its_document_and_is_not_an_authored_one() -> None:
    db, service, _ = _setup(capacity="contractor")
    await service.open(await _claim(db), USER)
    determinations = ClaimDeterminations(db, service, approver_rule=_only_reviewer)

    async def _receive(**over: Any):
        request = {"origin": "received", "issued_by": "engineer", "source_document_id": "doc-1", "reference": "ENG/DET/7", **over}
        return await determinations.create(
            await _claim(db), DeterminationCreateRequest(**request), USER, holds_authority=False, allowed_documents=DOCS
        )

    with pytest.raises(HTTPException) as unlinked:
        await _receive(source_document_id="doc-9")
    _refused(unlinked, 422, "tied to the document")
    with pytest.raises(HTTPException) as own_side:
        await _receive(issued_by="contractor")
    _refused(own_side, 422, "engineer or employer")
    view = await _receive()
    assert view.determination.origin == "received" and view.determination.source_document_id == "doc-1"
    with pytest.raises(HTTPException) as submit:
        await determinations.submit(
            await _claim(db), view.determination.id,
            DeterminationSubmitRequest(version=view.determination.version, reviewer_id=REVIEWER), USER, reviewer_eligible=_only_reviewer)
    _refused(submit, 409, "recorded, not submitted")
    with pytest.raises(HTTPException) as propose:
        await determinations.propose(
            await _claim(db), view.determination.id, DeterminationVersionRequest(version=view.determination.version), USER
        )
    _refused(propose, 409, "transcribed")
    view = await _found(determinations, db, view, result="none", days=None, findings="Rejected: notice said to be late.")
    view = await determinations.record_received(
        await _claim(db), view.determination.id, DeterminationVersionRequest(version=view.determination.version), USER
    )
    assert view.determination.status == "recorded" and not view.editable
    with pytest.raises(HTTPException) as immutable:
        await _found(determinations, db, view, result="full", days=10)
    _refused(immutable, 409, "cannot be changed")


async def test_an_engineer_workspace_does_not_record_its_own_determination_as_received() -> None:
    db, service, _ = _setup(capacity="engineer")
    await service.open(await _claim(db), USER)
    with pytest.raises(HTTPException) as exc:
        await ClaimDeterminations(db, service).create(
            await _claim(db),
            DeterminationCreateRequest(origin="received", issued_by="engineer", source_document_id="doc-1"),
            USER, holds_authority=False, allowed_documents=DOCS,
        )
    _refused(exc, 409, "authored here")


# --------------------------------------------------------------------------- #
# Privileged: simulations and the strategy note
# --------------------------------------------------------------------------- #
async def test_a_simulation_is_labelled_internal_and_writes_nothing_but_itself() -> None:
    llm = StubLLM(reply={"result": "full", "findings": "The Engineer would accept this.", "residual_risks": ["Notice timing"]})
    db, service, _ = _setup(capacity="contractor", llm=llm, published=True)
    await service.open(await _claim(db), USER)
    before = copy.deepcopy(await _claim(db))
    privileged = ClaimPrivileged(db, service)
    simulation = await privileged.simulate(await _claim(db), SimulationRequest(simulated_party="engineer"), USER)
    assert simulation.label == INTERNAL_ANALYSIS_LABEL and simulation.privileged
    assert simulation.simulated_party == "engineer" and simulation.prepared_for == "contractor"
    (head,) = simulation.heads
    # Held to the record like any proposal: the board does not hold the elements as supported.
    assert head.produced_by == "skill" and head.likely_result == "partial"
    assert "internal simulation" in llm.prompts[0] and "not a determination" in llm.prompts[0]
    # It wrote to its own collection and to nothing else.
    assert await _claim(db) == before
    assert await db[DETERMINATIONS].count_documents({}) == 0
    assert await db.skill_steps.count_documents({}) == 0
    assert await db[PRIVILEGED].count_documents({"kind": "simulation"}) == 1
    listing = await privileged.simulations(await _claim(db))
    assert listing.label == INTERNAL_ANALYSIS_LABEL and listing.can_simulate == ["employer", "engineer"]
    assert listing.items[0].label == INTERNAL_ANALYSIS_LABEL


async def test_a_simulation_without_a_skill_predicts_nothing_and_says_so() -> None:
    llm = StubLLM(reply={"result": "full"})
    db, service, _ = _setup(capacity="contractor", llm=llm, published=False)
    await service.open(await _claim(db), USER)
    simulation = await ClaimPrivileged(db, service).simulate(await _claim(db), SimulationRequest(simulated_party="employer"), USER)
    assert llm.prompts == [] and simulation.degraded and "Skill not used" in simulation.degraded_reason
    (head,) = simulation.heads
    assert head.produced_by == "record_check" and head.likely_result == "insufficient_information"
    assert "No response is predicted" in head.reasoning and head.likely_challenges


async def test_a_party_does_not_simulate_itself_and_capacity_is_never_assumed() -> None:
    db, service, _ = _setup(capacity="engineer")
    await service.open(await _claim(db), USER)
    with pytest.raises(HTTPException) as own:
        await ClaimPrivileged(db, service).simulate(await _claim(db), SimulationRequest(simulated_party="engineer"), USER)
    _refused(own, 409, "not simulated")
    db2, service2, _ = _setup(capacity=None)
    await service2.open(await _claim(db2), USER)
    with pytest.raises(HTTPException) as unstated:
        await ClaimPrivileged(db2, service2).simulate(await _claim(db2), SimulationRequest(simulated_party="engineer"), USER)
    _refused(unstated, 409, "which party")


async def test_the_strategy_note_is_drafted_from_the_board_edited_by_a_person_and_versioned() -> None:
    db, service, _ = _setup(capacity="contractor")
    await service.open(await _claim(db), USER)
    await service.decide_element(
        await _claim(db), "H1", "event", ElementDecisionRequest(board_version=await _version(db), state="supported"), USER
    )
    await service.decide_element(
        await _claim(db), "H1", "notice", ElementDecisionRequest(board_version=await _version(db), state="weak", note="Served late"), USER
    )
    before = copy.deepcopy(await _claim(db))
    privileged = ClaimPrivileged(db, service)
    response = await privileged.generate_note(await _claim(db), USER)
    note = response.note
    assert response.label == INTERNAL_NOTE_LABEL and note.label == INTERNAL_NOTE_LABEL and note.privileged
    assert note.produced_by == "record_check" and note.degraded
    assert any("H1 event: decided supported" in item for item in note.strengths)
    assert any("H1 notice: decided weak (Served late)" in item for item in note.weaknesses)
    saved = await privileged.save_note(
        await _claim(db),
        StrategyNoteRequest(version=note.version, strengths=note.strengths, weaknesses=["  "], notes="Settle above 60%."),
        USER,
    )
    assert saved.note.produced_by == "person" and saved.note.weaknesses == [] and saved.note.version == 1
    with pytest.raises(HTTPException) as stale:
        await privileged.save_note(await _claim(db), StrategyNoteRequest(version=0, notes="Lost update"), USER)
    _refused(stale, 409, "changed since")
    # Re-drafting replaces the lists and keeps what the person wrote.
    again = await privileged.generate_note(await _claim(db), USER)
    assert again.note.notes == "Settle above 60%." and again.note.version == 2
    assert await db[PRIVILEGED].count_documents({"kind": "strategy_note"}) == 1
    assert await _claim(db) == before


def test_only_the_privileged_module_names_the_privileged_collection() -> None:
    """The wall: no exporter, bundle, ledger or seeding code can read what it cannot name."""
    root = Path(__file__).resolve().parents[1]
    owners = {"services/claim_privileged.py", "migrations/v20261118_0001_claim_findings_indexes.py"}
    importers = {"routers/claim_findings.py"}
    yes_no_only = "services/project_perspective.py"
    for path in root.rglob("*.py"):
        relative = path.relative_to(root).as_posix()
        if relative.startswith("tests/") or "__pycache__" in relative:
            continue
        source = path.read_text(encoding="utf-8")
        if "claim_privileged" in source and relative not in owners:
            if relative == yes_no_only:
                # 4B-15: the perspective lock asks one yes/no question ("does a
                # simulation exist for these projects?") and may name nothing else.
                mentions = [line.strip() for line in source.splitlines() if "claim_privileged" in line]
                assert mentions == ["from .claim_privileged import any_simulation_for_projects"], (
                    f"{relative} may import only the yes/no check from the privileged module, found: {mentions}"
                )
                continue
            assert relative in importers, f"{relative} reaches into privileged claim material"
        for model in ("ClaimSimulation", "StrategyNote"):
            if re.search(rf"\b{model}\b", source):
                assert relative in owners | importers | {"models/claim_findings.py"}, f"{relative} uses {model}"


# --------------------------------------------------------------------------- #
# Submission: record composition (pure)
# --------------------------------------------------------------------------- #
def _head(**over: Any) -> ClaimHead:
    return ClaimHead(
        **{"head_id": "H1", "title": "Delayed handover", "kind": "time_and_cost", "clause_refs": ["8.4", "20.1"],
           "amount_claimed": 4500000.0, "eot_days_claimed": 146, **over}
    )


def _notice(source: str, **over: Any) -> NoticeAssessment:
    return NoticeAssessment(
        **{"basis": "event", "basis_date": datetime(2026, 3, 2), "period_days": 28, "period_source": source,
           "period_verified": source == "contract_verified", "clause_no": "20.1", "due_date": datetime(2026, 3, 30),
           "served_on": datetime(2026, 4, 11), "served_late_by_days": 12, "state": "served_late", **over}
    )


def _record(**over: Any) -> SubmissionRecord:
    return SubmissionRecord(
        **{"claim_ref": "CLM-7", "title": "Delayed handover of Site", "heads": [_head()],
           "event_date": datetime(2026, 3, 2), "notice": _notice("assumed_default"),
           "clauses": {"8.4": {"found": True, "title": "Extension of Time", "document_type": "GCC",
                               "wording": "The Contractor shall be   entitled to an extension."}}, **over}
    )


def _text(section) -> str:
    return "\n".join(paragraph.text for paragraph in section.paragraphs)


def test_the_six_sections_come_out_in_the_skills_order() -> None:
    sections = compose_record_sections(_record())
    assert list(sections) == SECTION_ORDER
    assert all(section.produced_by == "record_composition" and section.input_hash for section in sections.values())


def test_an_unverified_notice_period_never_reaches_the_submission() -> None:
    for source in ("assumed_default", "organisation_rule", "contract_unverified"):
        basis = compose_record_sections(_record(notice=_notice(source)))["contractual_basis"]
        text = _text(basis)
        assert "Notice of the claim was given on 11 April 2026." in text
        assert "28" not in text and "within" not in text and "after that period" not in text
        assert any("not been verified" in note for note in basis.internal_notes)


def test_a_verified_notice_period_is_stated_as_the_record_shows_it_late_or_not() -> None:
    late = _text(compose_record_sections(_record(notice=_notice("contract_verified")))["contractual_basis"])
    assert "Sub-Clause 20.1 requires notice within 28 days of the event; notice was given 12 days after that period." in late
    in_time = _text(
        compose_record_sections(
            _record(notice=_notice("contract_verified", served_on=datetime(2026, 3, 20), served_late_by_days=None, state="served_in_time"))
        )["contractual_basis"]
    )
    assert "notice was given within that period." in in_time


def test_clause_wording_is_quoted_from_the_index_and_a_clause_not_found_is_not_quoted() -> None:
    basis = compose_record_sections(_record())["contractual_basis"]
    text = _text(basis)
    assert "Sub-Clause 8.4 (Extension of Time), which provides: “The Contractor shall be entitled to an extension.”" in text
    assert "Head H1 relies on Sub-Clause 20.1." in text
    assert any("20.1 is not in the project's clause index" in note for note in basis.internal_notes)
    clause_sources = [source for paragraph in basis.paragraphs for source in paragraph.sources if source.kind == "clause"]
    assert [source.ref for source in clause_sources] == ["8.4"]


def test_figures_with_no_assessment_behind_them_are_flagged_and_reservation_is_always_made() -> None:
    sections = compose_record_sections(_record())
    summary = sections["executive_summary"]
    assert "an extension of time of 146 days and payment of INR 45,00,000.00" in _text(summary)
    assert any("no delay assessment" in note for note in summary.internal_notes)
    assert any("no build-up" in note for note in summary.internal_notes)
    relief = sections["relief_and_reservation"]
    assert relief.paragraphs[-1].text == RESERVATION_WORDING
    assert relief.paragraphs[-1].sources[0].kind == "standard_wording"


def test_the_evidence_schedule_is_in_date_order_with_undated_documents_last() -> None:
    schedule = evidence_schedule(
        [
            {"_id": "c", "subject": "Undated memo"},
            {"_id": "a", "subject": "Plant log", "date": "2026-03-31", "letter_no": "PL/03"},
            {"_id": "b", "subject": "Handover request", "date": datetime(2026, 3, 5)},
        ]
    )
    assert [(item.number, item.document_id) for item in schedule] == [("E-1", "b"), ("E-2", "a"), ("E-3", "c")]
    assert schedule[1].reference == "PL/03" and schedule[2].date is None


def test_amounts_use_indian_grouping_and_numbers_are_compared_without_it() -> None:
    assert format_amount(184500000) == "INR 18,45,00,000.00"
    assert format_amount(999.5) == "INR 999.50" and format_amount(100000) == "INR 1,00,000.00"
    assert numbers_in("INR 18,45,00,000.00 over 146 days, clause 8.4.") == {"184500000", "146", "8.4"}


def test_skill_text_is_refused_when_it_goes_beyond_the_record() -> None:
    record = ["Under head H1 the Contractor claims an extension of time of 146 days.", RESERVATION_WORDING]
    assert validate_section_text("relief_and_reservation", ["The Contractor seeks 146 days. All rights are reserved."], record, {"8.4"}) == []
    invented = validate_section_text("relief_and_reservation", ["The Contractor seeks 150 days. Rights reserved."], record, {"8.4"})
    assert invented and invented[0].startswith("no_invented_amounts") and "150" in invented[0]
    clause = validate_section_text("contractual_basis", ["Under Sub-Clause 13.7 the Contractor is entitled."], ["Head H1 relies on Sub-Clause 8.4."], {"8.4"})
    assert any(reason.startswith("exact_sub_clause_cited") and "13.7" in reason for reason in clause)
    dropped = validate_section_text("relief_and_reservation", ["The Contractor seeks 146 days."], record, {"8.4"})
    assert dropped == ["reservation_of_rights: the reservation of rights is missing or negated"]
    assert validate_section_text("quantum", ["  "], record, set()) == ["no text was returned"]


# --------------------------------------------------------------------------- #
# Submission: service
# --------------------------------------------------------------------------- #
async def test_a_submission_is_composed_only_from_a_locked_board_in_a_contractor_workspace() -> None:
    db, service, _ = _setup(capacity="contractor")
    await service.open(await _claim(db), USER)
    submissions = ClaimSubmissions(db, service)
    with pytest.raises(HTTPException) as unlocked:
        await submissions.compose(await _claim(db), ComposeRequest(), DOCS, USER)
    _refused(unlocked, 409, "Lock the board first")
    view = await submissions.view(await _claim(db), DOCS)
    assert not view.exists and not view.can_compose and not view.can_export
    db2, service2, _ = _setup(capacity="engineer")
    await _locked(service2, db2)
    with pytest.raises(HTTPException) as engineer:
        await ClaimSubmissions(db2, service2).compose(await _claim(db2), ComposeRequest(), DOCS, USER)
    _refused(engineer, 409, "Contractor's document")


async def test_without_a_skill_the_record_composition_is_the_submission_and_is_marked() -> None:
    llm = StubLLM(reply={"paragraphs": ["Invented prose."]})
    db, service, _ = _setup(capacity="contractor", llm=llm, published=False)
    _documents(db)
    await _locked(service, db)
    before = copy.deepcopy(await _claim(db))
    view = await ClaimSubmissions(db, service).compose(await _claim(db), ComposeRequest(), DOCS, USER)
    assert llm.prompts == []
    assert [item.section.key for item in view.sections] == SECTION_ORDER
    assert all(item.section.degraded and "Skill not used" in item.section.degraded_reason for item in view.sections)
    assert all(item.section.produced_by == "record_composition" and not item.stale for item in view.sections)
    assert [item.number for item in view.evidence] == ["E-1", "E-2"] and view.evidence[0].document_id == "doc-2"
    assert view.can_export and not view.approved_for_issue
    assert await _claim(db) == before


async def test_skill_text_within_the_record_is_used_and_text_beyond_it_is_discarded() -> None:
    llm = StubLLM(reply={"paragraphs": ["The Contractor claims 999 days."]})
    db, service, _ = _setup(capacity="contractor", llm=llm, published=True)
    await _locked(service, db)
    submissions = ClaimSubmissions(db, service)
    view = await submissions.compose(await _claim(db), ComposeRequest(), set(), USER)
    for item in view.sections:
        assert item.section.produced_by == "record_composition" and item.section.degraded
        assert "Skill text not used" in item.section.degraded_reason
        assert "999" not in "\n".join(paragraph.text for paragraph in item.section.paragraphs)
    # A faithful re-expression is accepted, and carries the section's sources.
    llm.reply = {"paragraphs": ["The Contractor reserves all of its rights in respect of these matters."]}
    view = await submissions.compose(await _claim(db), ComposeRequest(sections=["relief_and_reservation"]), set(), USER)
    relief = view.sections[-1].section
    assert relief.produced_by == "skill" and not relief.degraded
    assert relief.paragraphs[0].text.startswith("The Contractor reserves") and relief.paragraphs[0].sources


async def test_a_cost_line_without_proof_is_left_out_of_the_submission() -> None:
    db, service, _ = _setup(capacity="contractor", claim={"amount_claimed": None, "eot_days_claimed": None})
    await service.open(await _claim(db), USER)
    quantum = ClaimQuantum(db, service)

    async def _line(description: str, amount: float, records: list) -> None:
        await quantum.add_line(
            await _claim(db), "H1",
            QuantumLineRequest(board_version=await _version(db), category="direct", description=description, amount=amount, records=records),
            DOCS, USER,
        )

    await _line("Idle plant", 500000, [QuantumRecord(document_id="doc-1")])
    await _line("Unvouched labour", 4500000, [])
    await _decide_all(service, db)
    await service.lock(await _claim(db), BoardVersionRequest(board_version=await _version(db)), USER)
    _documents(db)
    view = await ClaimSubmissions(db, service).compose(await _claim(db), ComposeRequest(), DOCS, USER)
    quantum_section = next(item.section for item in view.sections if item.section.key == "quantum")
    text = "\n".join(paragraph.text for paragraph in quantum_section.paragraphs)
    assert "Idle plant: INR 5,00,000.00." in text and "Unvouched labour" not in text
    assert "The amount claimed under head H1 is INR 5,00,000.00." in text
    assert any("Unvouched labour" in note and "left out" in note for note in quantum_section.internal_notes)
    proof = [source for paragraph in quantum_section.paragraphs for source in paragraph.sources if source.kind == "document"]
    assert proof and proof[0].label.startswith("E-")
    summary = next(item.section for item in view.sections if item.section.key == "executive_summary")
    assert "INR 45,00,000.00" in " ".join(summary.internal_notes)
    assert "45,00,000" not in "\n".join(paragraph.text for paragraph in summary.paragraphs)


async def test_only_sections_whose_record_changed_go_stale_and_only_they_are_recomposed() -> None:
    db, service, _ = _setup(capacity="contractor")
    await _locked(service, db)
    submissions = ClaimSubmissions(db, service)
    first = await submissions.compose(await _claim(db), ComposeRequest(), set(), USER)
    stamps = {item.section.key: item.section.composed_at for item in first.sections}
    # The head's days change: the board is reopened with a reason (D-22), edited, and locked again.
    await service.unlock(await _claim(db), UnlockRequest(board_version=await _version(db), reason="The days claimed were revised"), USER)
    await service.patch_head(await _claim(db), "H1", HeadPatchRequest(board_version=await _version(db), eot_days_claimed=120), USER)
    unlocked = await submissions.view(await _claim(db), set())
    assert not unlocked.can_compose and not unlocked.can_export
    await service.lock(await _claim(db), BoardVersionRequest(board_version=await _version(db)), USER)
    view = await submissions.view(await _claim(db), set())
    stale = {item.section.key for item in view.sections if item.stale}
    assert stale == {"executive_summary", "relief_and_reservation"}
    assert not view.can_export and "no longer match the record" in view.cannot_export_reason
    with pytest.raises(HTTPException) as refused:
        await submissions.export(await _claim(db), "docx", set())
    _refused(refused, 409, "Re-compose")
    again = await submissions.compose(await _claim(db), ComposeRequest(), set(), USER)
    assert again.stale_sections == 0 and again.can_export
    for item in again.sections:
        assert (item.section.composed_at == stamps[item.section.key]) == (item.section.key not in stale)
    assert "120 days" in "\n".join(p.text for p in again.sections[0].section.paragraphs)
    with pytest.raises(HTTPException) as unknown:
        await submissions.compose(await _claim(db), ComposeRequest(sections=["appendix"]), set(), USER)
    _refused(unknown, 422, "Unknown section")


def _docx_text(content: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(content)) as archive:
        return re.sub(r"<[^>]+>", " ", archive.read("word/document.xml").decode("utf-8"))


async def test_an_export_is_a_draft_until_the_claim_is_approved_and_never_carries_privileged_material() -> None:
    db, service, _ = _setup(capacity="contractor")
    _documents(db)
    await _locked(service, db)
    privileged = ClaimPrivileged(db, service)
    note = (await privileged.generate_note(await _claim(db), USER)).note
    await privileged.save_note(
        await _claim(db), StrategyNoteRequest(version=note.version, weaknesses=["ZEBRA-WEAKNESS notice was late"], notes="ZEBRA-FLOOR settle at 60%"), USER
    )
    await privileged.simulate(await _claim(db), SimulationRequest(simulated_party="engineer"), USER)
    submissions = ClaimSubmissions(db, service)
    await submissions.compose(await _claim(db), ComposeRequest(), DOCS, USER)
    content, media_type, filename = await submissions.export(await _claim(db), "docx", DOCS)
    text = _docx_text(content)
    assert media_type.endswith("wordprocessingml.document") and filename.endswith(".docx")
    assert DRAFT_MARK in text and "Evidence schedule" in text and "Handover request" in text
    assert "1. Executive summary" in text and "6. Relief sought and reservation of rights" in text
    for forbidden in ("ZEBRA", "Internal", "simulation", "No response is predicted", "left out", "not been verified"):
        assert forbidden not in text
    db.approvals.docs.append({"_id": "a1", "resource_type": "claim", "resource_id": CLAIM_ID, "state": "approved"})
    approved, _, _ = await submissions.export(await _claim(db), "docx", DOCS)
    assert DRAFT_MARK not in _docx_text(approved)
    pdf, pdf_type, pdf_name = await submissions.export(await _claim(db), "pdf", DOCS)
    assert pdf.startswith(b"%PDF") and pdf_type == "application/pdf" and pdf_name.endswith(".pdf")
    with pytest.raises(HTTPException) as fmt:
        await submissions.export(await _claim(db), "pptx", DOCS)
    _refused(fmt, 422, "docx or pdf")


async def test_nothing_in_the_stored_submission_or_the_claim_holds_privileged_text() -> None:
    db, service, _ = _setup(capacity="contractor")
    await _locked(service, db)
    privileged = ClaimPrivileged(db, service)
    await privileged.save_note(await _claim(db), StrategyNoteRequest(version=0, notes="ZEBRA-FLOOR"), USER)
    await ClaimSubmissions(db, service).compose(await _claim(db), ComposeRequest(), set(), USER)
    for name, collection in db._collections.items() if hasattr(db, "_collections") else []:
        if name != PRIVILEGED:
            assert "ZEBRA" not in repr(collection.docs), name
    assert "ZEBRA" not in repr(await _claim(db))
    assert "ZEBRA" not in repr(await db.claim_submissions.find_one({"claim_id": CLAIM_ID}))


def test_the_findings_migration_is_in_the_catalogue_and_changes_no_role() -> None:
    from rbac_backend.migrations import v20261118_0001_claim_findings_indexes as migration
    from rbac_backend.migrations.catalog import MIGRATIONS

    assert migration.VERSION == "20261118_0001" and migration.VERSION in {item.version for item in MIGRATIONS}
    assert {index["collection"] for index in migration.INDEXES} == {"claim_determinations", "claim_privileged", "claim_submissions"}
    assert not any("roles" in index["collection"] for index in migration.INDEXES)


def _unused(_: Optional[str] = None) -> None:  # pragma: no cover - keeps optional imports honest
    return None


def test_the_yes_no_simulation_check_returns_a_boolean_and_nothing_else() -> None:
    """4B-15: what the perspective lock is allowed to learn from privileged material."""
    import inspect

    from rbac_backend.services import claim_privileged

    source = inspect.getsource(claim_privileged.any_simulation_for_projects)
    assert "-> bool" in source
    assert source.count("return ") == 2 and "return False" in source and "return row is not None" in source
    # It reads the key only: no text of any simulation leaves the module.
    assert '{"_id": 1}' in source
