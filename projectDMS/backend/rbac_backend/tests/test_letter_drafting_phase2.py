"""Phase 2 of the multi-agent letter-drafting workflow:

Clause Checking Agent — cited clauses verified against structured clause
records (existence, wording snippet, conservative applicability, SCC/addendum
modification alerts, no negative legal conclusions), and the
User Direction Agent — deterministic probing questions + answer merging.
"""

from __future__ import annotations

import pytest

from rbac_backend.models.letter_drafting import (
    CitedClauseEvaluation,
    DraftRunCreateRequest,
    IncomingLetterAnalysis,
    UserDirectionAnswer,
)
from rbac_backend.services.letter_drafting.clause_checker import (
    ClauseCheckingAgent,
    normalize_cited_clause,
)
from rbac_backend.services.letter_drafting.user_direction import (
    MAX_QUESTIONS,
    UserDirectionAgent,
)


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def limit(self, _n):
        return self

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class FakeCollection:
    def __init__(self, docs=None):
        self.docs = list(docs or [])

    def find(self, query):
        out = [
            dict(d) for d in self.docs
            if all(d.get(k) == v for k, v in query.items())
        ]
        return _Cursor(out)


class FakeDB:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())


def _analysis(**overrides) -> IncomingLetterAnalysis:
    base = dict(
        subject="Extension of time for sewer diversion",
        main_request="Grant extension of time under Clause 8.4",
        key_reply_points=["Confirm delay notice validity under Clause 8.4"],
        clauses_cited=["GCC 8.4", "9.9"],
        cited_clause_evaluations=[
            CitedClauseEvaluation(clause_number="GCC 8.4"),
            CitedClauseEvaluation(clause_number="9.9"),
        ],
    )
    base.update(overrides)
    return IncomingLetterAnalysis(**base)


def _clause_db(extra_docs=None) -> FakeDB:
    db = FakeDB()
    scope = {
        "org_id": "org-A", "project_id": "proj-A",
        "is_current": True, "is_authorised_for_ai": True,
    }
    db["contract_clauses"].docs = [
        {
            **scope,
            "clause_uid": "uid-84",
            "clause_no": "8.4",
            "clause_title": "Extension of Time for Completion",
            "cleaned_text": (
                "The Contractor shall be entitled to an extension of time if "
                "completion is delayed by a Variation or other listed cause, "
                "subject to notice of the delay event."
            ),
            "document_type": "GCC",
        },
        *(extra_docs or []),
    ]
    return db


# --------------------------------------------------------------------------- #
# Clause number normalization
# --------------------------------------------------------------------------- #
def test_normalize_cited_clause_strips_prefixes():
    assert normalize_cited_clause("GCC 8.4") == "8.4"
    assert normalize_cited_clause("Sub-Clause 8.4(a)") == "8.4(a)"
    assert normalize_cited_clause("SCC Clause 9.2") == "9.2"
    assert normalize_cited_clause("8.4") == "8.4"
    assert normalize_cited_clause(None) is None


# --------------------------------------------------------------------------- #
# Clause Checking Agent
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_existing_clause_verified_with_wording_and_applicability():
    agent = ClauseCheckingAgent(_clause_db())
    enriched = await agent.enrich(_analysis(), "org-A", "proj-A")

    by_no = {ev.clause_number: ev for ev in enriched.cited_clause_evaluations}
    found = by_no["GCC 8.4"]
    assert found.exists_in_contract is True
    assert found.clause_title == "Extension of Time for Completion"
    assert found.quoted_text and "extension of time" in found.quoted_text.lower()
    # Clear keyword overlap with the issue -> conservative True.
    assert found.applicable_to_issue is True


@pytest.mark.asyncio
async def test_missing_clause_flagged_for_review_never_negative_applicability():
    agent = ClauseCheckingAgent(_clause_db())
    enriched = await agent.enrich(_analysis(), "org-A", "proj-A")

    missing = {ev.clause_number: ev for ev in enriched.cited_clause_evaluations}["9.9"]
    assert missing.exists_in_contract is False
    assert missing.legal_or_commercial_review_required is True
    assert "not found" in (missing.drafter_comment or "")
    # The agent never asserts a negative legal conclusion.
    assert missing.applicable_to_issue is None
    assert missing.effect_on_sender_position in (None, "unknown")


@pytest.mark.asyncio
async def test_unindexed_project_leaves_everything_none():
    agent = ClauseCheckingAgent(FakeDB())  # no clause records at all
    enriched = await agent.enrich(_analysis(), "org-A", "proj-A")
    for ev in enriched.cited_clause_evaluations:
        assert ev.exists_in_contract is None
        assert "not clause-indexed" in (ev.drafter_comment or "")


@pytest.mark.asyncio
async def test_scc_modification_alert():
    scc_doc = {
        "org_id": "org-A", "project_id": "proj-A",
        "is_current": True, "is_authorised_for_ai": True,
        "clause_uid": "uid-84-scc", "clause_no": "8.4",
        "clause_title": "Extension of Time (as amended)",
        "cleaned_text": "Clause 8.4 of GCC is deleted and replaced as follows...",
        "document_type": "SCC",
    }
    agent = ClauseCheckingAgent(_clause_db([scc_doc]))
    enriched = await agent.enrich(_analysis(), "org-A", "proj-A")

    found = {ev.clause_number: ev for ev in enriched.cited_clause_evaluations}["GCC 8.4"]
    assert found.exists_in_contract is True
    assert "SCC" in (found.drafter_comment or "")
    assert found.legal_or_commercial_review_required is True
    # Quote still comes from the base (GCC) wording.
    assert "entitled to an extension" in (found.quoted_text or "")


# --------------------------------------------------------------------------- #
# User Direction Agent: probing questions
# --------------------------------------------------------------------------- #
def test_probing_questions_cover_position_deadline_amount_and_unfound_clause():
    analysis = _analysis(
        response_deadline="within 14 days",
        amount_claimed="INR 45,00,000",
        cited_clause_evaluations=[
            CitedClauseEvaluation(clause_number="9.9", exists_in_contract=False),
        ],
        key_reply_points=["Point A", "Point B"],
    )
    request = DraftRunCreateRequest()  # no desired position provided

    questions = UserDirectionAgent.build_questions(analysis, request, ["main_factual_basis"])
    categories = [q.category for q in questions]
    assert "position" in categories
    assert "deadline" in categories
    assert "clause" in categories
    assert "amount" in categories
    assert "missing_input" in categories
    assert len(questions) <= MAX_QUESTIONS


def test_no_position_question_when_position_given():
    questions = UserDirectionAgent.build_questions(
        _analysis(), DraftRunCreateRequest(desired_position="Reject the claim"), []
    )
    assert all(q.category != "position" for q in questions)


def test_no_questions_without_analysis():
    assert UserDirectionAgent.build_questions(None, DraftRunCreateRequest(), []) == []


# --------------------------------------------------------------------------- #
# User Direction Agent: answer merging
# --------------------------------------------------------------------------- #
def test_format_directions_renders_answers_and_free_text():
    block = UserDirectionAgent.format_directions(
        [
            UserDirectionAnswer(question_id="position", answer="Reject; reserve rights on cost"),
            UserDirectionAnswer(answer="Cite programme records"),
        ],
        directions="Keep the tone firm but courteous.",
    )
    assert block.startswith("User direction / line of action:")
    assert "[position] Reject; reserve rights on cost" in block
    assert "- Cite programme records" in block
    assert "Keep the tone firm but courteous." in block


def test_format_directions_empty():
    assert UserDirectionAgent.format_directions([], None) == ""
