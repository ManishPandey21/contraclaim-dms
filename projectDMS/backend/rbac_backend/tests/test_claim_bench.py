"""Claim Bench: heads tested on five elements, with records, skill and person kept apart.

What these tests hold the Bench to:

- an unpublished skill is never used, and its absence is visible on the step;
- without a skill no proposal is ever "supported";
- a skill's proposal cannot contradict a recorded fact;
- a proposal is not a decision, and the board locks only on people's decisions;
- the party the project acts for is never assumed.
"""

from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace
from typing import Any, List, Optional

import pytest
from fastapi import HTTPException

from rbac_backend.models.claim import ELEMENT_KEYS, ClaimBench, ClaimHead, NoticeAssessment
from rbac_backend.models.claim_skill_outputs import ClaimHeadTestOutput, ElementFinding
from rbac_backend.routers.claims import _read_model, mark_bench_tests_stale
from rbac_backend.services.claim_bench import (
    CLAIM_HEAD_TEST_PROMPT_KEY,
    BoardVersionRequest,
    ClaimBenchService,
    ClauseCheck,
    ElementDecisionRequest,
    HeadCreateRequest,
    HeadPatchRequest,
    HeadTestRequest,
    apply_fact_guards,
    claim_bench_rollout,
    compute_gate,
    initial_head,
    record_checks,
)
from rbac_backend.services.letter_drafting.prompts import PromptRegistry, has_default_template
from rbac_backend.services.skills.executor import SkillExecutor
from rbac_backend.services.skills.registry import SkillRegistry
from rbac_backend.services.skills.steps import SkillStepRunner
from rbac_backend.services.skills.system_skills import CLAIMS_SKILL_KEY, system_skill_version
from rbac_backend.tests.bench_fakes import FakeDB

ORG, PROJECT, CLAIM_ID, USER = "org-A", "proj-A", "claim-1", "user-1"
ON = SimpleNamespace(CLAIM_BENCH_ROLLOUT_MODE="primary", CLAIM_BENCH_CANARY_TENANT_IDS="")


class StubLLM:
    def __init__(self, reply: Optional[dict] = None, down: bool = False) -> None:
        self.reply = reply
        self.down = down
        self.prompts: List[str] = []

    async def __call__(self, prompt: str, max_tokens: int) -> str:
        self.prompts.append(prompt)
        if self.down:
            raise RuntimeError("provider unavailable")
        return json.dumps(self.reply or {})


def _all_supported(**over: Any) -> dict:
    finding = {"state": "supported", "reason": "The record establishes this.", "source_ids": []}
    return {
        **{key: dict(finding) for key in ELEMENT_KEYS},
        "classification": "compensable",
        "clause_refs": ["8.4", "99.9"],
        "residual_risks": ["Programme logic not checked."],
        "missing_information": [],
        **over,
    }


def _setup(
    *,
    llm: Optional[StubLLM] = None,
    published: bool = False,
    capacity: Optional[str] = "contractor",
    claim: Optional[dict] = None,
    clauses: bool = True,
):
    db = FakeDB()
    db.projects.docs.append({"_id": PROJECT, "organization_id": ORG, "name": "NH-48", "contractual_capacity": capacity})
    db.claims.docs.append(
        {
            "_id": CLAIM_ID,
            "title": "Delayed handover of Site",
            "type": "eot",
            "status": "submitted",
            "description": "Right of way not handed over.",
            "event_date": datetime(2026, 3, 2),
            "notice_date": datetime(2026, 4, 11),
            "amount_claimed": 184500000.0,
            "eot_days_claimed": 146,
            "contract_clauses": ["Sub-Clause 8.4", "20.1"],
            "currency": "INR",
            "organization_id": ORG,
            "project_id": PROJECT,
            "bench": None,
            **(claim or {}),
        }
    )
    if clauses:
        db.contract_clauses.docs.append(
            {
                "org_id": ORG, "project_id": PROJECT, "is_current": True, "is_authorised_for_ai": True,
                "clause_no": "8.4", "clause_title": "Extension of Time for Completion", "document_type": "GCC",
                "cleaned_text": "The Contractor shall be entitled to an extension of the Time for Completion.",
            }
        )
    if published:
        draft = system_skill_version(CLAIMS_SKILL_KEY)
        db.skill_versions.docs.append(
            {**draft.model_dump(by_alias=True, exclude={"id"}), "status": "published"}
        )
    llm = llm or StubLLM(down=True)
    registry = SkillRegistry(db)
    runner = SkillStepRunner(db, registry, SkillExecutor(PromptRegistry(db), llm=llm))
    return db, ClaimBenchService(db, registry=registry, runner=runner, config=ON), llm


async def _claim(db: FakeDB) -> dict:
    return await db.claims.find_one({"_id": CLAIM_ID})


async def _opened(service: ClaimBenchService, db: FakeDB):
    return await service.open(await _claim(db), USER)


async def _tested(service: ClaimBenchService, db: FakeDB, head_id: str = "H1", force: bool = False):
    bench = await service.get(await _claim(db))
    return await service.test_head(
        await _claim(db), head_id, HeadTestRequest(board_version=bench.bench.board_version, force=force), USER
    )


async def _decide_all(service: ClaimBenchService, db: FakeDB, state: str = "weak"):
    response = await service.get(await _claim(db))
    for head in response.bench.heads:
        for key in ELEMENT_KEYS:
            response = await service.decide_element(
                await _claim(db), head.head_id, key,
                ElementDecisionRequest(board_version=response.bench.board_version, state=state), USER,
            )
        response = await service.patch_head(
            await _claim(db), head.head_id,
            HeadPatchRequest(board_version=response.bench.board_version, classification="excusable"), USER,
        )
    return response


def _refused(exc: pytest.ExceptionInfo, status: int, *fragments: str) -> None:
    assert exc.value.status_code == status
    for fragment in fragments:
        assert fragment in str(exc.value.detail)


# --------------------------------------------------------------------------- #
# Opening
# --------------------------------------------------------------------------- #
async def test_open_carries_one_head_over_from_the_register_and_is_idempotent() -> None:
    db, service, _ = _setup()
    first = await _opened(service, db)
    assert first.exists and first.bench.board_version == 0 and first.bench.stage == "heads"
    (head,) = first.bench.heads
    assert head.head_id == "H1" and head.title == "Delayed handover of Site"
    assert head.kind == "time_and_cost" and head.amount_claimed == 184500000.0 and head.eot_days_claimed == 146
    assert head.clause_refs == ["8.4", "20.1"]  # "Sub-Clause" stripped, order kept
    assert all(head.elements[key].state is None and head.elements[key].proposed_state is None for key in ELEMENT_KEYS)
    again = await _opened(service, db)
    assert again.bench.board_version == 0 and len(again.bench.heads) == 1


async def test_a_claim_without_a_board_reads_as_not_yet_opened() -> None:
    db, service, _ = _setup()
    response = await service.get(await _claim(db))
    assert response.exists is False and response.bench.heads == []
    assert response.gate.can_lock is False and "Add at least one head of claim" in response.gate.blockers


@pytest.mark.parametrize(
    "fields, kind",
    [
        ({"amount_claimed": None, "eot_days_claimed": 30, "type": "eot"}, "time"),
        ({"amount_claimed": 5.0, "eot_days_claimed": None, "type": "variation"}, "cost"),
        ({"amount_claimed": 5.0, "eot_days_claimed": None, "type": "eot"}, "time_and_cost"),
        ({"amount_claimed": None, "eot_days_claimed": None, "type": "other"}, "time_and_cost"),
    ],
)
def test_initial_head_kind_follows_what_the_register_holds(fields: dict, kind: str) -> None:
    assert initial_head({"title": "T", **fields}).kind == kind


# --------------------------------------------------------------------------- #
# The skill is not used until it is published
# --------------------------------------------------------------------------- #
async def test_an_unpublished_skill_is_not_used_and_the_step_says_so() -> None:
    llm = StubLLM(reply=_all_supported())
    db, service, _ = _setup(llm=llm, published=False)
    await _opened(service, db)
    response = await _tested(service, db)
    assert llm.prompts == []  # no model call at all
    assert response.skill.available is False and response.skill.reason == "Awaiting legal approval"
    (step,) = response.steps
    assert step.degraded and step.skill_version == "" and "No published version" in (step.error or "")
    head = response.bench.heads[0]
    assert {head.elements[key].proposed_by for key in ELEMENT_KEYS} == {"record_check"}
    assert "supported" not in {head.elements[key].proposed_state for key in ELEMENT_KEYS}
    assert head.proposed_classification == "undetermined"
    assert all(head.elements[key].state is None for key in ELEMENT_KEYS)  # nothing decided for the user


async def test_the_test_is_refused_until_the_projects_party_is_stated() -> None:
    db, service, llm = _setup(capacity=None, published=True, llm=StubLLM(reply=_all_supported()))
    await _opened(service, db)
    with pytest.raises(HTTPException) as exc:
        await _tested(service, db)
    _refused(exc, 409, "State which party this project acts for")
    assert llm.prompts == [] and await db.skill_steps.count_documents({}) == 0
    assert (await service.get(await _claim(db))).perspective is None


async def test_a_published_skill_runs_from_the_projects_position_and_is_held_to_the_records() -> None:
    llm = StubLLM(reply=_all_supported())
    # No notice served, and clause 20.1 is not in the index.
    db, service, _ = _setup(llm=llm, published=True, capacity="engineer", claim={"notice_date": None})
    await _opened(service, db)
    response = await _tested(service, db)
    (prompt,) = llm.prompts
    assert "acting for the engineer" in prompt
    assert "Notice served on: not recorded" in prompt and "Clause 20.1: NOT found in the clause index" in prompt
    assert "entitled to an extension of the Time for Completion" in prompt  # indexed wording of 8.4 only
    (step,) = response.steps
    assert not step.degraded and step.skill_version == "1.0.0" and step.role == "engineer"
    head = response.bench.heads[0]
    assert head.elements["event"].proposed_state == "supported" and head.elements["event"].proposed_by == "skill"
    # The model said "supported"; the record says no notice was served.
    assert head.elements["notice"].proposed_state == "missing"
    assert "No notice is recorded" in head.elements["notice"].proposed_reason
    # A cited clause could not be found: supported is brought down, with both reasons.
    basis = head.elements["contractual_basis"]
    assert basis.proposed_state == "weak" and "Not found in this project's clause index: 20.1" in basis.proposed_reason
    assert head.proposed_classification == "compensable" and response.bench.perspective == "engineer"
    assert head.residual_risks == ["Programme logic not checked."]


async def test_a_model_outage_falls_back_to_the_record_checks_and_is_marked() -> None:
    db, service, llm = _setup(llm=StubLLM(down=True), published=True)
    await _opened(service, db)
    response = await _tested(service, db)
    assert len(llm.prompts) == 1
    (step,) = response.steps
    assert step.degraded and "provider unavailable" in (step.error or "")
    assert response.bench.heads[0].elements["causation"].proposed_by == "record_check"


async def test_an_unchanged_head_reuses_its_step_and_force_runs_it_again() -> None:
    llm = StubLLM(reply=_all_supported())
    db, service, _ = _setup(llm=llm, published=True)
    await _opened(service, db)
    await _tested(service, db)
    await _tested(service, db)
    assert len(llm.prompts) == 1 and await db.skill_steps.count_documents({}) == 1
    await _tested(service, db, force=True)
    assert len(llm.prompts) == 2 and await db.skill_steps.count_documents({}) == 1


def test_the_claim_prompt_has_its_own_template() -> None:
    assert has_default_template(CLAIM_HEAD_TEST_PROMPT_KEY)


# --------------------------------------------------------------------------- #
# Record checks
# --------------------------------------------------------------------------- #
def _notice(**over: Any) -> NoticeAssessment:
    return NoticeAssessment(
        **{
            "basis": "event", "basis_date": datetime(2026, 3, 2), "period_days": 28,
            "period_source": "assumed_default", "due_date": datetime(2026, 3, 30),
            "served_on": datetime(2026, 4, 11), "served_late_by_days": 12, "state": "served_late", **over,
        }
    )


def _head(**over: Any) -> ClaimHead:
    return ClaimHead(
        **{"head_id": "H1", "title": "Site handover", "event_summary": "Site handed over late.",
           "clause_refs": ["8.4"], "amount_claimed": 10.0, "eot_days_claimed": 5, **over}
    )


CLAIM = {"event_date": datetime(2026, 3, 2), "type": "eot"}
FOUND = [ClauseCheck(clause_no="8.4", found=True)]


def test_record_checks_never_propose_supported() -> None:
    for notice in (None, _notice(), _notice(state="served_in_time", served_late_by_days=None),
                   _notice(period_source="contract_verified", period_verified=True, clause_no="20.1")):
        output = record_checks(CLAIM, _head(), notice, FOUND, True)
        assert "supported" not in {getattr(output, key).state for key in ELEMENT_KEYS}
        assert output.classification == "undetermined"


def test_a_late_notice_against_an_unverified_period_is_weak_not_fatal() -> None:
    output = record_checks(CLAIM, _head(), _notice(), FOUND, True)
    assert output.notice.state == "weak"
    assert "12 day(s) after a 28-day period that has not been verified against the contract" in output.notice.reason
    assert "Lateness alone does not dispose of the head" in output.notice.reason
    assert "The notice period, confirmed from the contract's own clause" in output.missing_information


def test_a_verified_period_is_named_by_its_clause() -> None:
    verified = _notice(period_source="contract_verified", period_verified=True, clause_no="20.1")
    output = record_checks(CLAIM, _head(), verified, FOUND, True)
    assert "the 28-day period in clause 20.1" in output.notice.reason
    assert output.missing_information == []


@pytest.mark.parametrize(
    "head, indexed, checks, state, fragment",
    [
        (_head(clause_refs=[]), True, [], "missing", "No contract clause is cited"),
        (_head(), False, [], "weak", "not clause-indexed"),
        (_head(clause_refs=["8.4", "20.1"]), True, [*FOUND, ClauseCheck(clause_no="20.1", found=False)], "weak", "20.1"),
        (_head(), True, FOUND, "weak", "Every cited clause exists"),
    ],
)
def test_contractual_basis_follows_the_clause_index(head, indexed, checks, state, fragment) -> None:
    basis = record_checks(CLAIM, head, _notice(), checks, indexed).contractual_basis
    assert basis.state == state and fragment in basis.reason


@pytest.mark.parametrize(
    "head, state, fragment",
    [
        (_head(kind="time", eot_days_claimed=None), "missing", "days claimed"),
        (_head(kind="cost", amount_claimed=None), "missing", "amount claimed"),
        (_head(kind="time_and_cost", amount_claimed=None), "weak", "amount claimed"),
        (_head(kind="time_and_cost", amount_claimed=None, eot_days_claimed=None), "missing", "days claimed and amount claimed"),
        (_head(kind="cost", eot_days_claimed=None), "weak", "A figure is recorded"),
    ],
)
def test_quantum_asks_only_for_what_the_head_claims(head, state, fragment) -> None:
    quantum = record_checks(CLAIM, head, _notice(), FOUND, True).quantum
    assert quantum.state == state and fragment in quantum.reason


def test_event_and_notice_are_missing_when_nothing_is_recorded() -> None:
    output = record_checks({"type": "eot"}, _head(event_summary=None), None, FOUND, True)
    assert output.event.state == "missing" and output.notice.state == "missing"
    assert record_checks(CLAIM, _head(), _notice(served_on=None, state="overdue"), FOUND, True).notice.state == "missing"


def test_guards_leave_a_proposal_alone_when_the_records_do_not_contradict_it() -> None:
    verified = _notice(period_source="contract_verified", period_verified=True, clause_no="20.1",
                       state="served_in_time", served_late_by_days=None)
    floor = record_checks(CLAIM, _head(), verified, FOUND, True)
    proposed = ClaimHeadTestOutput(**_all_supported(clause_refs=["8.4", "99.9"]))
    guarded = apply_fact_guards(proposed, floor)
    assert {getattr(guarded, key).state for key in ELEMENT_KEYS} == {"supported"}
    assert guarded.clause_refs == ["8.4"]  # a clause the head never cited is dropped


def test_guards_keep_not_applicable_and_never_raise_a_state() -> None:
    floor = record_checks(CLAIM, _head(kind="time", eot_days_claimed=None), None, FOUND, True)
    proposed = ClaimHeadTestOutput(
        **_all_supported(quantum={"state": "not_applicable", "reason": "Time only."},
                         event={"state": "missing", "reason": "No record."})
    )
    guarded = apply_fact_guards(proposed, floor)
    assert guarded.quantum.state == "not_applicable" and guarded.event.state == "missing"
    assert guarded.notice.state == "missing"
    assert isinstance(guarded.causation, ElementFinding) and guarded.causation.state == "supported"


# --------------------------------------------------------------------------- #
# Decisions, gate, lock
# --------------------------------------------------------------------------- #
async def test_a_proposal_is_not_a_decision_and_the_board_locks_only_on_decisions() -> None:
    db, service, _ = _setup(llm=StubLLM(reply=_all_supported()), published=True)
    await _opened(service, db)
    tested = await _tested(service, db)
    assert tested.gate.undecided_elements == 5 and tested.gate.unclassified_heads == 1 and not tested.gate.can_lock
    with pytest.raises(HTTPException) as exc:
        await service.lock(await _claim(db), BoardVersionRequest(board_version=tested.bench.board_version), USER)
    _refused(exc, 409, "5 elements still need your call", "1 head not yet classified")

    decided = await _decide_all(service, db)
    assert decided.gate.blockers == [] and decided.gate.can_lock
    element = decided.bench.heads[0].elements["notice"]
    assert element.state == "weak" and element.decided_by == USER and element.decided_at is not None
    locked = await service.lock(await _claim(db), BoardVersionRequest(board_version=decided.bench.board_version), USER)
    assert locked.bench.stage == "locked" and locked.bench.locked_by == USER and locked.bench.perspective == "contractor"
    assert locked.gate.can_lock is False


async def test_a_person_may_decide_against_the_proposal_and_record_why() -> None:
    db, service, _ = _setup()
    opened = await _opened(service, db)
    response = await service.decide_element(
        await _claim(db), "H1", "notice",
        ElementDecisionRequest(board_version=opened.bench.board_version, state="supported", note="Email of 5 March is a notice."),
        USER,
    )
    element = response.bench.heads[0].elements["notice"]
    assert element.state == "supported" and element.note == "Email of 5 March is a notice." and element.proposed_state is None
    cleared = await service.decide_element(
        await _claim(db), "H1", "notice", ElementDecisionRequest(board_version=response.bench.board_version, clear=True), USER
    )
    assert cleared.bench.heads[0].elements["notice"].state is None


async def test_editing_a_tested_head_marks_the_test_stale_and_reopens_a_locked_board() -> None:
    db, service, _ = _setup(llm=StubLLM(reply=_all_supported()), published=True)
    await _opened(service, db)
    await _tested(service, db)
    decided = await _decide_all(service, db)
    locked = await service.lock(await _claim(db), BoardVersionRequest(board_version=decided.bench.board_version), USER)
    edited = await service.patch_head(
        await _claim(db), "H1", HeadPatchRequest(board_version=locked.bench.board_version, clause_refs=["8.4"]), USER
    )
    head = edited.bench.heads[0]
    assert edited.bench.stage == "heads" and head.test_stale and edited.gate.stale_tests == 1
    assert head.elements["event"].state == "weak"  # the person's decisions are kept
    assert edited.steps[0].stale


async def test_a_stale_view_is_refused_rather_than_overwriting() -> None:
    db, service, _ = _setup()
    opened = await _opened(service, db)
    await service.add_head(
        await _claim(db), HeadCreateRequest(board_version=opened.bench.board_version, title="Prolongation", kind="cost"), USER
    )
    with pytest.raises(HTTPException) as exc:
        await service.add_head(
            await _claim(db), HeadCreateRequest(board_version=opened.bench.board_version, title="Again", kind="cost"), USER
        )
    _refused(exc, 409, "The Bench changed since you loaded it")
    assert len((await service.get(await _claim(db))).bench.heads) == 2


async def test_frozen_evidence_makes_the_board_read_only() -> None:
    db, service, _ = _setup()
    opened = await _opened(service, db)
    await db.claims.update_one({"_id": CLAIM_ID}, {"$set": {"evidence_frozen_at": datetime(2026, 11, 1)}})
    response = await service.get(await _claim(db))
    assert response.read_only and "frozen" in (response.read_only_reason or "")
    with pytest.raises(HTTPException) as exc:
        await service.decide_element(
            await _claim(db), "H1", "event", ElementDecisionRequest(board_version=opened.bench.board_version, state="weak"), USER
        )
    _refused(exc, 409, "frozen")


async def test_the_registers_amount_follows_the_heads_and_days_are_not_summed() -> None:
    db, service, _ = _setup()
    opened = await _opened(service, db)
    added = await service.add_head(
        await _claim(db),
        HeadCreateRequest(board_version=opened.bench.board_version, title="Prolongation", kind="cost",
                          amount_claimed=1000.0, eot_days_claimed=30),
        USER,
    )
    stored = await _claim(db)
    assert stored["amount_claimed"] == 184501000.0
    assert stored["eot_days_claimed"] == 146  # overlapping heads must not add up
    removed = await service.remove_head(await _claim(db), "H2", BoardVersionRequest(board_version=added.bench.board_version), USER)
    assert [head.head_id for head in removed.bench.heads] == ["H1"]
    assert (await _claim(db))["amount_claimed"] == 184500000.0


async def test_invalid_inputs_are_refused() -> None:
    db, service, _ = _setup()
    opened = await _opened(service, db)
    version = opened.bench.board_version
    for call, status in [
        (service.decide_element(await _claim(db), "H1", "liability", ElementDecisionRequest(board_version=version, state="weak"), USER), 404),
        (service.decide_element(await _claim(db), "H1", "event", ElementDecisionRequest(board_version=version, state="proven"), USER), 422),
        (service.decide_element(await _claim(db), "H9", "event", ElementDecisionRequest(board_version=version, state="weak"), USER), 404),
        (service.patch_head(await _claim(db), "H1", HeadPatchRequest(board_version=version, classification="winning"), USER), 422),
        (service.add_head(await _claim(db), HeadCreateRequest(board_version=version, title="X", kind="money"), USER), 422),
    ]:
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == status


def test_gate_counts_per_element_and_per_head() -> None:
    bench = ClaimBench(heads=[_head(), _head(head_id="H2")])
    gate = compute_gate(bench)
    assert gate.undecided_elements == 10 and gate.unclassified_heads == 2 and not gate.can_lock


# --------------------------------------------------------------------------- #
# The register and the board
# --------------------------------------------------------------------------- #
class _Windows:
    async def for_claim(self, claim: dict) -> dict:
        return {}

    async def requirement_for(self, claim: dict):
        return None


async def test_the_register_sees_an_outline_of_the_board_not_the_board() -> None:
    db, service, _ = _setup()
    await _opened(service, db)
    read = await _read_model(await _claim(db), [], _Windows())
    assert read.head_count == 1 and read.bench_stage == "heads"
    assert read.bench is None
    untouched = await _read_model({"_id": "c2", "title": "T", "status": "draft"}, [], _Windows())
    assert untouched.head_count == 0 and untouched.bench_stage is None


async def test_changing_a_date_on_the_claim_marks_tested_heads_stale_and_reopens_the_board() -> None:
    db, service, _ = _setup(llm=StubLLM(reply=_all_supported()), published=True)
    await _opened(service, db)
    await _tested(service, db)
    decided = await _decide_all(service, db)
    await service.lock(await _claim(db), BoardVersionRequest(board_version=decided.bench.board_version), USER)
    refreshed = await mark_bench_tests_stale(db, await _claim(db))
    assert refreshed is not None
    bench = ClaimBench(**refreshed["bench"])
    assert bench.stage == "heads" and bench.heads[0].test_stale and bench.board_version == decided.bench.board_version + 2
    assert bench.heads[0].elements["event"].state == "weak"
    assert (await db.skill_steps.find_one({}))["stale"] is True
    assert await mark_bench_tests_stale(db, {"_id": "none", "bench": None}) is None


# --------------------------------------------------------------------------- #
# Rollout
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "mode, tenants, tenant, enabled",
    [
        ("off", "", ORG, False),
        ("primary", "", ORG, True),
        ("canary", "org-A, org-C", ORG, True),
        ("canary", "org-C", ORG, False),
        ("canary", "org-A", None, False),
        ("everything", "", ORG, False),
    ],
)
def test_rollout_is_decided_by_the_server(mode, tenants, tenant, enabled) -> None:
    config = SimpleNamespace(CLAIM_BENCH_ROLLOUT_MODE=mode, CLAIM_BENCH_CANARY_TENANT_IDS=tenants)
    assert claim_bench_rollout(tenant, config).enabled is enabled


async def test_a_bench_that_is_off_answers_not_found() -> None:
    db, _, _ = _setup()
    service = ClaimBenchService(db, config=SimpleNamespace(CLAIM_BENCH_ROLLOUT_MODE="off"))
    with pytest.raises(HTTPException) as exc:
        service.ensure_enabled(ORG)
    _refused(exc, 404, "not enabled")
