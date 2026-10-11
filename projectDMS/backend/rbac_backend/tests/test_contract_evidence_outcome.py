"""Degraded, empty and hard-fail semantics across all sources.

A-31 / A-32 - a source failure cannot yield a complete-looking legal answer.

These need mutation proof for a specific reason: a silent empty looks *identical*
to a real empty. Every assertion here would pass against an implementation that
collapses both to ``[]`` and reports nothing, so the mutations make the
degradation silent, and each must turn its own RED.
"""

from __future__ import annotations

import pytest

from rbac_backend.services.contract_evidence_outcome import (
    EvidenceHardFailure,
    EvidenceStatus,
    HardFailReason,
    SourceStatus,
    classify_evidence_outcome,
)


def _classify(**overrides):
    payload = {
        "candidates": [],
        "source_status": {
            "lexical": SourceStatus.SUCCESS.value,
            "vector": SourceStatus.SUCCESS.value,
            "graph": SourceStatus.SUCCESS.value,
        },
        "eligible_document_ids": ["doc-1"],
    }
    payload.update(overrides)
    return classify_evidence_outcome(**payload)


# --------------------------------------------------------------------------- #
# A-31 / A-32 : a failure is never a complete answer
# --------------------------------------------------------------------------- #


def test_a_source_outage_with_no_results_is_degraded_not_empty():
    """The case that used to read as 'nothing applies'."""
    outcome = _classify(
        source_status={
            "lexical": SourceStatus.SUCCESS.value,
            "vector": SourceStatus.DEGRADED.value,
            "graph": SourceStatus.SUCCESS.value,
        }
    )
    assert outcome.status is EvidenceStatus.DEGRADED
    assert outcome.is_trustworthy_empty is False
    assert outcome.degraded_sources == ("vector",)


def test_a_source_outage_with_results_is_still_degraded():
    outcome = _classify(
        candidates=["clause-1"],
        source_status={
            "lexical": SourceStatus.SUCCESS.value,
            "vector": SourceStatus.DEGRADED.value,
            "graph": SourceStatus.SUCCESS.value,
        },
    )
    assert outcome.status is EvidenceStatus.DEGRADED
    assert outcome.candidates == ("clause-1",)


def test_the_failed_sources_are_named():
    outcome = _classify(
        source_status={
            "lexical": SourceStatus.DEGRADED.value,
            "vector": SourceStatus.DEGRADED.value,
            "graph": SourceStatus.SUCCESS.value,
        }
    )
    assert outcome.degraded_sources == ("lexical", "vector")


def test_a_degraded_answer_is_never_reported_as_a_trustworthy_empty():
    outcome = _classify(
        source_status={
            "lexical": SourceStatus.DEGRADED.value,
            "vector": SourceStatus.SUCCESS.value,
            "graph": SourceStatus.SUCCESS.value,
        }
    )
    assert outcome.is_trustworthy_empty is False


def test_a_consumer_that_needs_completeness_can_refuse_a_degraded_answer():
    outcome = _classify(
        source_status={
            "lexical": SourceStatus.DEGRADED.value,
            "vector": SourceStatus.SUCCESS.value,
            "graph": SourceStatus.SUCCESS.value,
        }
    )
    with pytest.raises(EvidenceHardFailure):
        outcome.require_complete()


# --------------------------------------------------------------------------- #
# valid empty
# --------------------------------------------------------------------------- #


def test_zero_applicable_instruments_is_a_valid_empty_result():
    outcome = _classify(candidates=[], eligible_document_ids=[])
    assert outcome.status is EvidenceStatus.VALID_EMPTY
    assert outcome.is_trustworthy_empty is True
    assert outcome.degraded_sources == ()


def test_a_valid_empty_answer_is_distinguishable_from_a_degraded_one():
    empty = _classify(candidates=[], eligible_document_ids=[])
    degraded = _classify(
        candidates=[],
        source_status={
            "lexical": SourceStatus.DEGRADED.value,
            "vector": SourceStatus.SUCCESS.value,
            "graph": SourceStatus.SUCCESS.value,
        },
    )
    assert empty.status is not degraded.status
    assert empty.is_trustworthy_empty and not degraded.is_trustworthy_empty


def test_a_complete_answer_with_results_is_complete():
    outcome = _classify(candidates=["clause-1", "clause-2"])
    assert outcome.status is EvidenceStatus.COMPLETE
    assert len(outcome.candidates) == 2
    outcome.require_complete()


# --------------------------------------------------------------------------- #
# hard failures
# --------------------------------------------------------------------------- #


def test_unresolved_authority_hard_fails():
    with pytest.raises(EvidenceHardFailure) as excinfo:
        _classify(authority_resolved=False)
    assert excinfo.value.reason is HardFailReason.AUTHORITY_UNRESOLVED


def test_a_missing_eligible_set_hard_fails():
    with pytest.raises(EvidenceHardFailure) as excinfo:
        _classify(eligible_document_ids=None)
    assert excinfo.value.reason is HardFailReason.AUTHORITY_UNRESOLVED


def test_ambiguous_applicability_hard_fails():
    with pytest.raises(EvidenceHardFailure) as excinfo:
        _classify(ambiguous_applicability=True)
    assert excinfo.value.reason is HardFailReason.AMBIGUOUS_APPLICABILITY


def test_a_historical_question_without_a_date_hard_fails():
    with pytest.raises(EvidenceHardFailure) as excinfo:
        _classify(missing_event_date=True)
    assert excinfo.value.reason is HardFailReason.MISSING_EVENT_DATE


def test_a_stale_projection_hard_fails_and_never_degrades():
    with pytest.raises(EvidenceHardFailure) as excinfo:
        _classify(stale_projection=True)
    assert excinfo.value.reason is HardFailReason.STALE_PROJECTION


def test_a_stale_projection_hard_fails_even_when_candidates_exist():
    """Falling back to the prior generation is the forbidden move."""
    with pytest.raises(EvidenceHardFailure):
        _classify(candidates=["stale-clause"], stale_projection=True)


def test_authority_failure_beats_degradation():
    """A partial answer is only meaningful if what survives is legitimate."""
    with pytest.raises(EvidenceHardFailure) as excinfo:
        _classify(
            authority_resolved=False,
            source_status={"lexical": SourceStatus.DEGRADED.value},
        )
    assert excinfo.value.reason is HardFailReason.AUTHORITY_UNRESOLVED


def test_every_hard_fail_reason_is_distinguishable():
    reasons = set()
    for kwargs in (
        {"authority_resolved": False},
        {"missing_event_date": True},
        {"ambiguous_applicability": True},
        {"stale_projection": True},
    ):
        with pytest.raises(EvidenceHardFailure) as excinfo:
            _classify(**kwargs)
        reasons.add(excinfo.value.reason)
    assert len(reasons) == 4


# --------------------------------------------------------------------------- #
# one interpretation, not three
# --------------------------------------------------------------------------- #


def test_all_three_sources_share_one_interpretation():
    outcomes = {
        source: _classify(
            source_status={
                name: (
                    SourceStatus.DEGRADED.value if name == source else SourceStatus.SUCCESS.value
                )
                for name in ("lexical", "vector", "graph")
            }
        ).status
        for source in ("lexical", "vector", "graph")
    }
    assert set(outcomes.values()) == {EvidenceStatus.DEGRADED}


def test_no_source_keeps_a_silent_empty_fallback_on_the_evidence_path():
    """The stop condition, asserted against the three capability modules."""
    import ast
    import inspect

    from rbac_backend.services import (
        contract_graph_containment,
        contract_lexical_containment,
        contract_vector_containment,
    )

    for module in (
        contract_lexical_containment,
        contract_vector_containment,
        contract_graph_containment,
    ):
        tree = ast.parse(inspect.getsource(module))
        for handler in [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]:
            returns_bare_empty = [
                node
                for node in ast.walk(handler)
                if isinstance(node, ast.Return)
                and isinstance(node.value, ast.List)
                and not node.value.elts
            ]
            assert not returns_bare_empty, (
                f"{module.__name__} swallows an exception into a bare empty list"
            )
