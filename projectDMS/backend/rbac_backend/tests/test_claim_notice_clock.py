"""Notice Clock: periods read from the contract, verified by a person, and
signals that a claim exists before anyone has raised one."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any, Optional

import pytest
from fastapi import HTTPException

from rbac_backend.models.claim import ALLOWED_STATUS_TRANSITIONS, ClaimStatus
from rbac_backend.models.claim_notice import NoticeRequirementCreate
from rbac_backend.services.claim_notice_requirements import NoticeRequirementService, find_notice_periods
from rbac_backend.services.claim_signals import ClaimSignalService
from rbac_backend.services.claims_notice import assess_notice
from rbac_backend.services.skills.system_skills import CLAIMS_SKILL_KEY, EOT_SKILL_KEY, LETTERS_SKILL_KEY
from rbac_backend.tests.bench_fakes import FakeDB

ORG, PROJECT = "org-A", "proj-A"
NOW = datetime(2026, 11, 15)
USER = SimpleNamespace(id="user-1", organization_id=ORG, roles=["projectadmin"])

GCC_20_1 = (
    "If the Contractor considers himself to be entitled to any extension of the Time for Completion "
    "and/or any additional payment, the Contractor shall give notice to the Engineer, describing the "
    "event or circumstance giving rise to the claim. The notice shall be given as soon as practicable, "
    "and not later than 28 days after the Contractor became aware, or should have become aware, of the "
    "event or circumstance. Within 42 days after the Contractor became aware of the event, the "
    "Contractor shall send to the Engineer a fully detailed claim."
)
SCC_20_1 = (
    "Sub-Clause 20.1 is amended: the notice of a claim for extension of time shall be given "
    "within fourteen (14) days of the occurrence of the event."
)


def _clause(clause_no: str, text: str, document_type: str = "GCC", **over: Any) -> dict:
    return {
        "org_id": ORG, "project_id": PROJECT, "is_current": True, "is_authorised_for_ai": True,
        "clause_no": clause_no, "clause_title": "Contractor's Claims", "document_type": document_type,
        "document_id": f"doc-{document_type}", "clause_uid": f"{document_type}-{clause_no}", "text": text, **over,
    }


# --------------------------------------------------------------------------- #
# Reading periods from clause wording
# --------------------------------------------------------------------------- #
def test_finds_the_notice_period_and_what_it_runs_from() -> None:
    (candidate,) = find_notice_periods([_clause("20.1", GCC_20_1)])
    assert candidate.period_days == 28 and candidate.trigger == "awareness" and candidate.clause_no == "20.1"
    assert candidate.quote.startswith("The notice shall be given as soon as practicable")
    assert "28 days" in candidate.quote
    # The 42-day sentence is about the detailed claim, not about notice.
    assert all(item.period_days != 42 for item in find_notice_periods([_clause("20.1", GCC_20_1)]))


def test_a_modifying_document_ranks_ahead_of_the_general_conditions() -> None:
    found = find_notice_periods([_clause("20.1", GCC_20_1), _clause("20.1", SCC_20_1, "SCC")])
    assert [(item.period_days, item.document_type) for item in found] == [(14, "SCC"), (28, "GCC")]
    assert found[0].modifies_general_conditions and found[0].trigger == "event"
    assert not found[1].modifies_general_conditions


def test_a_clause_the_claim_cites_ranks_first() -> None:
    other = _clause("13.3", "The Contractor shall give notice of a Variation claim within 7 days of the event.")
    found = find_notice_periods([other, _clause("20.1", GCC_20_1)], cited_clause_numbers=["Sub-Clause 20.1"])
    assert found[0].clause_no == "20.1" and found[0].cited_by_claim
    assert found[1].clause_no == "13.3" and not found[1].cited_by_claim


@pytest.mark.parametrize(
    "text",
    [
        "The Employer shall pay the amount certified within 56 days after the Engineer receives the Statement.",
        "The Contractor shall give notice to the Engineer of the date on which any Plant will be delivered.",
        "Notice of a claim shall be given promptly.",
        "",
    ],
)
def test_wording_that_states_no_notice_period_for_a_claim_yields_nothing(text: str) -> None:
    assert find_notice_periods([_clause("14.7", text)]) == []


def test_out_of_range_numbers_and_clause_numbers_are_not_read_as_periods() -> None:
    text = "Notice of the claim under Sub-Clause 20.1 shall be given within 0 days or 400 days of the event."
    assert find_notice_periods([_clause("20.1", text)]) == []


def test_duplicate_wording_is_reported_once() -> None:
    assert len(find_notice_periods([_clause("20.1", GCC_20_1), _clause("20.1", GCC_20_1)])) == 1


# --------------------------------------------------------------------------- #
# Recording and verifying
# --------------------------------------------------------------------------- #
def _payload(**over: Any) -> NoticeRequirementCreate:
    return NoticeRequirementCreate(
        **{
            "project_id": PROJECT, "claim_type": "eot", "period_days": 28, "trigger": "awareness",
            "clause_no": "Sub-Clause 20.1",
            "quote": "The notice shall be given not later than 28 days after the Contractor became aware.",
            **over,
        }
    )


async def test_a_recorded_period_starts_unverified_and_becomes_verified_by_a_person() -> None:
    service = NoticeRequirementService(FakeDB())
    record = await service.set(ORG, _payload(), "user-1")
    assert record.verification == "extracted_unverified" and record.status == "current"
    assert record.clause_no == "20.1" and record.created_by == "user-1"
    verified = await service.verify(record, "user-2")
    assert verified.verification == "verified" and verified.verified_by == "user-2" and verified.verified_at
    assert (await service.current(ORG, PROJECT, "eot")).id == record.id


async def test_the_quote_must_state_the_period() -> None:
    service = NoticeRequirementService(FakeDB())
    with pytest.raises(HTTPException) as exc:
        await service.set(ORG, _payload(period_days=21), "user-1")
    assert exc.value.status_code == 422 and "does not state 21 days" in str(exc.value.detail)
    # "128 days" does not state 28 days.
    with pytest.raises(HTTPException):
        await service.set(ORG, _payload(quote="The notice shall be given not later than 128 days after the event."), "user-1")


async def test_a_new_period_supersedes_the_old_one_and_keeps_it_as_history() -> None:
    service = NoticeRequirementService(FakeDB())
    first = await service.verify(await service.set(ORG, _payload(), "user-1"), "user-2")
    second = await service.set(
        ORG, _payload(period_days=14, trigger="event", quote="Notice shall be given within 14 days of the event."), "user-1"
    )
    current = await service.current(ORG, PROJECT, "eot")
    assert current.id == second.id and current.verification == "extracted_unverified"
    history = await service.history(ORG, PROJECT, "eot")
    old = next(item for item in history if item.id == first.id)
    assert old.status == "superseded" and old.superseded_by_id == second.id and old.verification == "verified"
    with pytest.raises(HTTPException) as exc:
        await service.verify(old, "user-2")
    assert exc.value.status_code == 409
    # Another claim type on the project is untouched.
    assert await service.current(ORG, PROJECT, "variation") is None


async def test_candidates_come_from_the_projects_own_index_only() -> None:
    db = FakeDB()
    db.contract_clauses.docs.extend(
        [_clause("20.1", GCC_20_1), _clause("20.1", SCC_20_1, "SCC", project_id="proj-B")]
    )
    indexed, found = await NoticeRequirementService(db).candidates(ORG, PROJECT)
    assert indexed and [item.period_days for item in found] == [28]
    indexed, found = await NoticeRequirementService(db).candidates(ORG, "proj-none")
    assert indexed is False and found == []


# --------------------------------------------------------------------------- #
# The assessment uses the recorded period
# --------------------------------------------------------------------------- #
def _requirement(**over: Any) -> SimpleNamespace:
    return SimpleNamespace(
        **{"id": "req-1", "period_days": 28, "trigger": "awareness", "clause_no": "20.1",
           "verification": "extracted_unverified", **over}
    )


CLAIM = {"type": "eot", "status": "submitted", "event_date": datetime(2026, 3, 2), "notice_date": datetime(2026, 4, 11)}


def test_an_unverified_contract_period_is_used_but_not_reported_as_verified() -> None:
    result = assess_notice(CLAIM, now=NOW, requirement=_requirement(), organisation_windows={"eot": 14})
    assert result.period_source == "contract_unverified" and result.period_verified is False
    assert result.period_days == 28 and result.clause_no == "20.1" and result.requirement_id == "req-1"
    assert result.state == "served_late" and result.served_late_by_days == 12


def test_only_a_verified_period_is_reported_as_the_contracts() -> None:
    result = assess_notice(CLAIM, now=NOW, requirement=_requirement(verification="verified"))
    assert result.period_source == "contract_verified" and result.period_verified is True


def test_a_period_the_contract_counts_from_the_event_ignores_a_later_awareness_date() -> None:
    claim = {**CLAIM, "awareness_date": datetime(2026, 3, 20)}
    from_event = assess_notice(claim, now=NOW, requirement=_requirement(trigger="event"))
    assert from_event.basis == "event" and from_event.basis_date == datetime(2026, 3, 2)
    assert from_event.state == "served_late"
    from_awareness = assess_notice(claim, now=NOW, requirement=_requirement())
    assert from_awareness.basis == "awareness" and from_awareness.state == "served_in_time"


def test_without_a_recorded_period_nothing_claims_to_come_from_the_contract() -> None:
    result = assess_notice(CLAIM, now=NOW)
    assert result.period_source == "assumed_default" and result.clause_no is None and result.requirement_id is None


# --------------------------------------------------------------------------- #
# Signals
# --------------------------------------------------------------------------- #
def _hindrance(_id: str, **over: Any) -> dict:
    return {
        "_id": _id, "organization_id": ORG, "project_id": PROJECT, "hindrance_ref": f"HR-{_id}",
        "title": f"Utility shifting at {_id}", "start_date": datetime(2026, 11, 1),
        "responsibility": "employer", "critical_path_impact": True, "archived_at": None, **over,
    }


def _board(letter_id: str, *skills: str, **over: Any) -> dict:
    return {
        "letter_id": letter_id, "organization_id": ORG, "project_id": PROJECT,
        "incoming_meta": {"letter_no": "CTR/2026/118", "letter_date": "2026-11-02", "subject": "Notice of delay"},
        "chain": [{"skill_key": key} for key in skills], **over,
    }


SCOPE = {"organization_id": ORG, "project_id": PROJECT}


async def _signals(db: FakeDB, hindrances: Optional[dict] = SCOPE, boards: Optional[dict] = SCOPE):
    return await ClaimSignalService(db).list(
        organization_id=ORG, hindrance_query=hindrances, board_query=boards, now=NOW
    )


async def test_a_critical_employer_hindrance_is_a_signal_with_its_clock_already_running() -> None:
    db = FakeDB()
    db.delay_events.docs.extend(
        [
            _hindrance("7"),
            _hindrance("8", responsibility="contractor"),
            _hindrance("9", critical_path_impact=False),
            _hindrance("10", archived_at=datetime(2026, 11, 2)),
            _hindrance("11", project_id="proj-B"),
            _hindrance("12", responsibility="concurrent", start_date=datetime(2026, 10, 1)),
        ]
    )
    signals = await _signals(db)
    assert [item.source_id for item in signals] == ["12", "7"]  # most urgent first
    overdue, open_ = signals
    assert overdue.notice_assessment.state == "overdue" and "concurrent" in overdue.reason
    assert open_.label == "Hindrance HR-7" and open_.claim_type.value == "eot"
    assert open_.notice_assessment.state == "open" and open_.notice_assessment.days_remaining == 14
    assert open_.notice_assessment.period_source == "assumed_default"


async def test_a_letter_routed_to_the_claims_or_eot_skill_is_a_signal() -> None:
    db = FakeDB()
    db.letter_position_boards.docs.extend(
        [
            _board("L1", "indian-infra-letter-analysis", EOT_SKILL_KEY, LETTERS_SKILL_KEY),
            _board("L2", "indian-infra-letter-analysis", CLAIMS_SKILL_KEY),
            _board("L3", "indian-infra-letter-analysis", LETTERS_SKILL_KEY),
        ]
    )
    signals = {item.source_id: item for item in await _signals(db)}
    assert set(signals) == {"L1", "L2"}
    assert signals["L1"].claim_type.value == "eot" and "delay" in signals["L1"].reason
    assert signals["L2"].claim_type.value == "other" and signals["L1"].label == "Letter CTR/2026/118"
    assert signals["L1"].event_date == datetime(2026, 11, 2)


async def test_a_source_the_caller_may_not_read_contributes_nothing() -> None:
    db = FakeDB()
    db.delay_events.docs.append(_hindrance("7"))
    db.letter_position_boards.docs.append(_board("L1", EOT_SKILL_KEY))
    assert [item.source_type for item in await _signals(db, hindrances=None)] == ["letter_bench"]
    assert [item.source_type for item in await _signals(db, boards=None)] == ["hindrance"]
    assert await _signals(db, hindrances=None, boards=None) == []


async def test_accepting_a_signal_raises_one_potential_claim_and_the_signal_goes_away() -> None:
    db = FakeDB()
    db.delay_events.docs.append(_hindrance("7"))
    service = ClaimSignalService(db)
    source = await service.load_source("hindrance", "7")
    claim, created = await service.raise_potential("hindrance", source, USER)
    assert created and claim["status"] == "potential" and claim["type"] == "eot"
    assert claim["origin"] == {"type": "hindrance", "id": "7", "label": "Hindrance HR-7"}
    assert claim["event_date"] == datetime(2026, 11, 1) and claim["project_id"] == PROJECT
    assert claim["created_by"] == "user-1" and "Raised from Hindrance HR-7" in claim["description"]
    again, created_again = await service.raise_potential("hindrance", source, USER)
    assert not created_again and again["_id"] == claim["_id"]
    assert await db.claims.count_documents({}) == 1
    assert await _signals(db) == []
    # The hindrance itself was not written to.
    assert await db.delay_events.find_one({"_id": "7"}) == _hindrance("7")


async def test_a_record_that_indicates_no_claim_cannot_be_raised() -> None:
    db = FakeDB()
    db.letter_position_boards.docs.append(_board("L3", LETTERS_SKILL_KEY))
    service = ClaimSignalService(db)
    with pytest.raises(HTTPException) as exc:
        await service.raise_potential("letter_bench", await service.load_source("letter_bench", "L3"), USER)
    assert exc.value.status_code == 422
    assert await service.load_source("hindrance", "missing") is None


def test_a_potential_claim_is_taken_up_or_dropped() -> None:
    assert ALLOWED_STATUS_TRANSITIONS[ClaimStatus.POTENTIAL] == {
        ClaimStatus.DRAFT, ClaimStatus.NOTIFIED, ClaimStatus.CLOSED
    }
    assert all(ClaimStatus.POTENTIAL not in targets for targets in ALLOWED_STATUS_TRANSITIONS.values())


# --------------------------------------------------------------------------- #
# Migration
# --------------------------------------------------------------------------- #
async def test_index_migration_declares_what_makes_the_writes_safe() -> None:
    from rbac_backend.migrations.v20261116_0001_claim_bench_indexes import INDEXES, upgrade

    by_name = {index["name"]: index for index in INDEXES}
    assert by_name["skill_step_idempotency_unique"]["unique"] and by_name["skill_step_id_unique"]["unique"]
    current = by_name["notice_requirement_current_unique"]
    assert current["unique"] and current["partialFilterExpression"] == {"status": "current"}
    origin = by_name["claim_origin_unique"]
    assert origin["unique"] and origin["partialFilterExpression"] == {"origin.id": {"$type": "string"}}
    dry = await upgrade(FakeDB(), True)
    assert dry.status == "dry_run" and len(dry.operations) == len(INDEXES) and len(dry.notices) == 1
    assert (await upgrade(FakeDB(), False)).status == "applied"
