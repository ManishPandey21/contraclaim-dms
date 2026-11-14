"""Pure Letter Bench logic: text cleaning, spans, deadlines, staleness, gates, HTTP surface."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from rbac_backend.core.security import get_current_user
from rbac_backend.models.letter_bench import (
    Assertion,
    BenchAccepted,
    BenchAvailability,
    BenchOpenResponse,
    BoardQuestion,
    ChainLink,
    ClauseCheck,
    PositionBoard,
    ReplyParagraph,
    SkillStep,
)
from rbac_backend.models.letter_drafting import ProbingQuestion
from rbac_backend.routers import letter_bench as bench_router
from rbac_backend.services.letter_drafting import bench_logic as logic
from rbac_backend.services.letter_drafting.incoming_analyzer import (
    clean_incoming_text,
    extract_assertions,
    extract_clause_refs,
    locate_quote,
)
from rbac_backend.services.letter_drafting.validator import DraftValidator


# --------------------------------------------------------------------------- #
# Text
# --------------------------------------------------------------------------- #
def test_cleaning_drops_letterhead_noise_and_keeps_the_letter() -> None:
    raw = "ACME LTD\r\nTel: 011-22334455\r\nwww.acme.example\r\n=====~~~~=====\r\nPage 1 of 3\r\n\r\n\r\n\r\nDear Sir,\r\nWe claim  payment.\r\n12"
    cleaned = clean_incoming_text(raw)
    assert cleaned == "ACME LTD\n\nDear Sir,\nWe claim payment."
    assert clean_incoming_text(None) == "" and clean_incoming_text("   ") == ""


def test_clause_refs_cover_prefixed_and_lettered_forms() -> None:
    text = "arbitration under GCC 17.5(b), read with Sub-Clause 20.2 and clause 13; see Clause 20.2 again"
    assert extract_clause_refs(text) == ["17.5(b)", "20.2", "13"]
    assert extract_clause_refs("no references here") == []


def test_assertions_are_spans_of_the_body_only() -> None:
    text = (
        "Ref. No. X/1\n\nSubject: Notice\n\nDear Sir,\n\n"
        "We refer to your letter dated 01-01-2026.\n\n"
        "The weather was pleasant during the visit to the site office.\n\n"
        "We hereby invoke arbitration under GCC 17.5(b). You have failed to make payment of Rs. 10,000 due on 01-02-2026.\n\n"
        "Yours faithfully,\nWe demand everything."
    )
    found = extract_assertions(text)
    assert [item["quoted_text"] for item in found] == [
        "We hereby invoke arbitration under GCC 17.5(b).",
        "You have failed to make payment of Rs. 10,000 due on 01-02-2026.",
    ]
    assert found[0]["clause_refs"] == ["17.5(b)"]
    for item in found:
        assert text[item["start"] : item["end"]] == item["quoted_text"]
    assert extract_assertions("") == []


def test_locate_quote_tolerates_whitespace_but_never_invents() -> None:
    text = "The Site was\nhanded over   46 days late."
    start, end = locate_quote(text, "the site was handed over 46 days late")
    assert text[start:end].startswith("The Site was")
    assert locate_quote(text, "The Employer admits liability") is None
    assert locate_quote(text, "short") is None


# --------------------------------------------------------------------------- #
# Deadline
# --------------------------------------------------------------------------- #
def test_deadline_resolution_is_day_first_and_counts_days() -> None:
    today = date(2026, 11, 14)
    info = logic.resolve_deadline("within 14 days", "02-11-2026", today=today, risk="high")
    assert (info.due_date, info.days_remaining, info.risk) == ("2026-11-16", 2, "high")
    assert logic.resolve_deadline("by 05/12/2026", None, today=today).due_date == "2026-12-05"
    unknown = logic.resolve_deadline("within 28 days", None, today=today)
    assert unknown.due_date is None and "letter date not identified" in unknown.basis
    assert logic.resolve_deadline(None, "02-11-2026").source == "none"
    assert logic.parse_date("31-02-2026") is None
    manual = logic.manual_deadline("2026-11-20", None, today=today)
    assert (manual.days_remaining, manual.risk, manual.source) == (6, "high", "manual")
    assert logic.refresh_deadline(manual, today=date(2026, 11, 22)).days_remaining == -2


# --------------------------------------------------------------------------- #
# Staleness
# --------------------------------------------------------------------------- #
def _step(step_id: str, targets: list[str], schema: str, parents: list[str] | None = None) -> SkillStep:
    return SkillStep(
        step_id=step_id, letter_id="L", skill_key="k", skill_version="1.0.0", skill_hash="h",
        target_assertion_ids=targets, input_hash=step_id, output_schema_key=schema,
        status="completed", parent_step_ids=parents or [],
    )


STEPS = [
    _step("analysis", [], "letter_analysis.v1"),
    _step("eval-A1", ["A1"], "variation_evaluation.v1", ["analysis"]),
    _step("para-A1", ["A1"], "reply_paragraph.v1", ["eval-A1"]),
    _step("para-A2", ["A2"], "reply_paragraph.v1"),
]


def test_a_verdict_change_stales_only_that_assertions_paragraph() -> None:
    assert logic.steps_stale_after_position_change(STEPS, "A1", include_evaluations=False) == {"para-A1"}
    assert logic.steps_stale_after_position_change(STEPS, "A2", include_evaluations=False) == {"para-A2"}
    assert logic.steps_stale_after_position_change(STEPS, "A9", include_evaluations=False) == set()


def test_a_routing_change_stales_the_evaluation_and_what_follows_it() -> None:
    assert logic.steps_stale_after_position_change(STEPS, "A1", include_evaluations=True) == {"eval-A1", "para-A1"}
    assert logic.descendants(STEPS, ["analysis"]) == {"eval-A1", "para-A1"}
    assert logic.descendants(STEPS, ["para-A2"]) == set()


def test_stale_steps_are_reflected_on_paragraphs_and_chain_links() -> None:
    board = PositionBoard(
        letter_id="L", organization_id="o",
        chain=[
            ChainLink(link_id="l1", skill_key="k", label="Variations", kind="evaluation", target_assertion_ids=["A1"], status="done"),
            ChainLink(link_id="l2", skill_key="k", label="Other", kind="evaluation", target_assertion_ids=["A2"], status="done"),
        ],
        reply_paragraphs=[
            ReplyParagraph(paragraph_id="P-A1", assertion_id="A1", step_id="para-A1", text="x"),
            ReplyParagraph(paragraph_id="P-A2", assertion_id="A2", step_id="para-A2", text="y"),
        ],
    )
    logic.apply_stale_to_board(board, {"eval-A1", "para-A1"}, STEPS)
    assert [p.stale for p in board.reply_paragraphs] == [True, False]
    assert [link.status for link in board.chain] == ["stale", "done"]


# --------------------------------------------------------------------------- #
# Gate
# --------------------------------------------------------------------------- #
def _decided(verdict: str | None = "none", state: str = "decided") -> Assertion:
    return Assertion(assertion_id="A1", order=1, quoted_text="We claim payment.", verdict=verdict, state=state)


def test_gate_counts_blockers_in_plain_language() -> None:
    question = BoardQuestion(question=ProbingQuestion(question_id="deadline", question="Confirm?", category="deadline"))
    board = PositionBoard(letter_id="L", organization_id="o", stage="positions", assertions=[_decided(None, "open")], questions=[question])
    gate = logic.compute_gate(board, [])
    assert (gate.undecided, gate.unanswered_questions, gate.can_lock, gate.can_compose) == (1, 1, False, False)
    assert gate.blockers == ["1 position still need your call", "1 question need an answer"]
    board.assertions = [_decided()]
    board.questions[0].answer = "Yes"
    assert logic.compute_gate(board, []).can_lock is True
    running = _step("r", ["A1"], "variation_evaluation.v1").model_copy(update={"status": "running"})
    assert logic.compute_gate(board, [running]).blockers == ["A skill is still running"]
    board.chain = [ChainLink(link_id="l", skill_key="k", label="V", kind="evaluation", status="stale")]
    assert "out of date" in logic.compute_gate(board, []).blockers[0]


def test_compose_needs_current_locked_positions() -> None:
    board = PositionBoard(
        letter_id="L", organization_id="o", stage="locked", locked_strategy_version=1,
        assertions=[_decided(state="locked")],
    )
    gate = logic.compute_gate(board, [])
    assert gate.can_compose is True and gate.can_lock is False
    board.composing = True
    assert logic.compute_gate(board, []).can_compose is False
    assert logic.compute_gate(PositionBoard(letter_id="L", organization_id="o"), []).can_lock is False


# --------------------------------------------------------------------------- #
# Reply matrix, framing, fallbacks
# --------------------------------------------------------------------------- #
def test_reply_matrix_status_follows_clause_checks_and_verdicts() -> None:
    board = PositionBoard(
        letter_id="L", organization_id="o", role="employer",
        assertions=[
            Assertion(assertion_id="A1", order=1, quoted_text="Point one", verdict="none", verdict_source="user",
                      user_direction="Out of time.", clause_refs=["20.2"],
                      clause_checks=[ClauseCheck(clause_number="20.2", state="found")]),
            Assertion(assertion_id="A2", order=2, quoted_text="Point two", verdict="need_information"),
            Assertion(assertion_id="A3", order=3, quoted_text="Point three", verdict="partial", clause_refs=["9.9"],
                      clause_checks=[ClauseCheck(clause_number="9.9", state="not_found")]),
        ],
    )
    rows = logic.build_reply_matrix(board)
    assert [row.status for row in rows] == ["supported", "needs_confirmation", "needs_confirmation"]
    assert rows[0].proposed_reply == "No entitlement. Out of time."
    assert rows[2].risk_note == "Clause 9.9: not found in this contract"
    plan = logic.build_plan_text(board, rows)
    assert "A3: Point three" in plan and "Position: Partial entitlement." in plan
    sheet = logic.build_planning_sheet(board, None)
    assert sheet.sender_role == "employer" and sheet.user_confirmed and sheet.rights_reservation_required


@pytest.mark.parametrize("role,phrase", [
    ("contractor", "the Contractor expressly reserves"),
    ("employer", "the Employer expressly reserves"),
    ("engineer", "rights of the Parties under the Contract are reserved"),
])
def test_reservation_wording_follows_the_drafting_role(role: str, phrase: str) -> None:
    board = PositionBoard(letter_id="L", organization_id="o", role=role, assertions=[_decided()])
    framing = logic.framing_paragraphs(board, [])
    assert phrase in framing["reservations"].text
    assert set(framing) == {"header", "opening", "request", "reservations", "closing"}


def test_user_edited_framing_is_kept_and_letter_is_numbered_by_assertion_order() -> None:
    board = PositionBoard(
        letter_id="L", organization_id="o", role="employer",
        assertions=[_decided(), Assertion(assertion_id="A2", order=2, quoted_text="Second", verdict="partial")],
    )
    edited = ReplyParagraph(paragraph_id="P-opening", part="opening", text="Our own opening.", edited_by_user=True)
    framing = logic.framing_paragraphs(board, [edited])
    assert framing["opening"].text == "Our own opening."
    board.reply_paragraphs = logic.ordered_paragraphs(
        board,
        [ReplyParagraph(paragraph_id="P-A2", assertion_id="A2", text="Second answer."),
         ReplyParagraph(paragraph_id="P-A1", assertion_id="A1", text="First answer."), *framing.values()],
    )
    letter = logic.assemble_letter(board)
    assert letter.index("1. First answer.") < letter.index("2. Second answer.") < letter.index("expressly reserves")
    assert letter.index("Our own opening.") < letter.index("1. First answer.")


@pytest.mark.parametrize("verdict", ["entitlement", "partial", "none", "need_information"])
def test_fallback_paragraph_never_reads_as_a_clause_citation(verdict: str) -> None:
    assertion = Assertion(
        assertion_id="A1", order=1, verdict=verdict, needs=["BOQ rates"],
        quoted_text="We hereby give notice under Sub-Clause 20.2 and GCC Clause 17.5(b) that the Site was late.",
    )
    output = logic.fallback_paragraph(assertion, "employer")
    assert "[CONFIRM:" in output.text and "the provision cited" in output.text
    assert DraftValidator.CLAUSE_PATTERN.search(output.text) is None
    assert "template wording" in output.internal_note.lower()


def test_fallback_variation_stops_at_insufficient_information() -> None:
    output = logic.fallback_variation(Assertion(assertion_id="A1", order=1, quoted_text="Extra work", clause_refs=["13.1"]))
    assert output.classification == "insufficient_information" and output.valuation_limb == "not_determined"
    assert output.suggested_verdict == "need_information" and len(output.missing_information) >= 3
    assert output.contractual_basis == ["Sender relies on clause 13.1"]


def test_deterministic_proposal_never_concedes_or_rejects() -> None:
    cited = Assertion(assertion_id="A1", order=1, quoted_text="x" * 40, clause_refs=["20.2"],
                      clause_checks=[ClauseCheck(clause_number="20.2", state="found")])
    logic.propose_verdict(cited)
    assert cited.proposed_verdict is None and "Decide manually" in cited.proposed_reason
    bare = Assertion(assertion_id="A2", order=2, quoted_text="x" * 40)
    logic.propose_verdict(bare)
    assert bare.proposed_verdict == "need_information"


# --------------------------------------------------------------------------- #
# HTTP surface
# --------------------------------------------------------------------------- #
class _FakeService:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.error: HTTPException | None = None

    def availability(self, tenant_id):
        return BenchAvailability(enabled=tenant_id == "org1", rollout_mode="canary")

    async def open(self, payload, current_user):
        self.calls.append(("open", payload.incoming_document_id))
        if self.error:
            raise self.error
        return BenchOpenResponse(letter_id="L1", created=True, board_version=0)

    async def begin_dissect(self, letter_id, current_user):
        self.calls.append(("begin_dissect", letter_id))
        return BenchAccepted(letter_id=letter_id, operation="dissect", poll_url=f"/api/letters/{letter_id}/bench")

    async def begin_first_dissect(self, letter_id, current_user):
        self.calls.append(("begin_first_dissect", letter_id))
        return BenchAccepted(letter_id=letter_id, operation="dissect", poll_url=f"/api/letters/{letter_id}/bench")

    async def list_skills(self, letter_id, current_user):
        self.calls.append(("list_skills", letter_id))
        return []

    async def run_dissect(self, letter_id, current_user):
        self.calls.append(("run_dissect", letter_id))

    async def begin_compose(self, letter_id, payload, current_user):
        self.calls.append(("begin_compose", payload.board_version))
        if self.error:
            raise self.error
        return BenchAccepted(letter_id=letter_id, operation="compose", poll_url=f"/api/letters/{letter_id}/bench")

    async def run_compose(self, letter_id, payload, current_user):
        self.calls.append(("run_compose", letter_id))


def _client(monkeypatch) -> tuple[TestClient, _FakeService]:
    service = _FakeService()
    app = FastAPI()
    app.include_router(bench_router.router, prefix="/api")
    app.dependency_overrides[bench_router.get_bench_service] = lambda: service
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="u1", organization_id="org1", roles=[])

    async def _no_limit(*args, **kwargs):
        return None

    monkeypatch.setattr(bench_router.bench_run_limiter, "check_user_limit", _no_limit)
    monkeypatch.setattr(bench_router.bench_edit_limiter, "check_user_limit", _no_limit)
    return TestClient(app), service


def test_open_route_dissects_in_the_background(monkeypatch) -> None:
    client, service = _client(monkeypatch)
    response = client.post("/api/letters/bench/open", json={"incoming_document_id": "doc1"})
    assert response.status_code == 200 and response.json()["letter_id"] == "L1"
    # The first analysis rides on the open's own authorisation.
    assert service.calls == [("open", "doc1"), ("begin_first_dissect", "L1"), ("run_dissect", "L1")]
    assert client.post("/api/letters/bench/open", json={}).status_code == 422


def test_long_operations_answer_202_with_a_poll_url(monkeypatch) -> None:
    client, service = _client(monkeypatch)
    response = client.post("/api/letters/L1/bench/compose", json={"board_version": 7})
    assert response.status_code == 202
    assert response.json() == {
        "letter_id": "L1", "operation": "compose", "step_id": None, "poll_url": "/api/letters/L1/bench",
    }
    assert ("run_compose", "L1") in service.calls
    assert client.post("/api/letters/L1/bench/dissect").status_code == 202


def test_refusals_keep_their_status_and_do_not_start_work(monkeypatch) -> None:
    client, service = _client(monkeypatch)
    service.error = HTTPException(status_code=409, detail="Lock positions before composing the reply")
    response = client.post("/api/letters/L1/bench/compose", json={"board_version": 7})
    assert response.status_code == 409 and "Lock positions" in response.json()["detail"]
    assert ("run_compose", "L1") not in service.calls
    service.error = HTTPException(status_code=403, detail="Not authorized for this project")
    assert client.post("/api/letters/bench/open", json={"incoming_document_id": "d"}).status_code == 403
    assert ("begin_first_dissect", "L1") not in service.calls


def test_availability_route_is_not_shadowed_and_reveals_only_the_flag(monkeypatch) -> None:
    client, _ = _client(monkeypatch)
    assert client.get("/api/letters/bench/availability").json() == {"enabled": True, "rollout_mode": "canary"}
    assert client.get("/api/letters/bench/availability?organization_id=org2").json()["enabled"] is False
    assert client.get("/api/letters/L1/bench/skills").json() == []
    assert client.patch("/api/letters/L1/bench/assertions/A1", json={"verdict": "maybe", "board_version": 1}).status_code == 422
    assert client.patch("/api/letters/L1/bench/assertions/A1", json={"verdict": "none"}).status_code == 422
