"""Contract Q&A evidence contract.

A-21b - the runtime half: a request without a mode, or Historical without a
        date, is refused. (The type-level half A-21a belongs to ticket 02.)
A-24  - canonical override beats higher retrieval relevance.
A-25  - unresolved precedence surfaces as unresolved; neither is asserted.

The change under test is to the evidence contract itself, not to a label: what a
question must state, and what decides which answer governs.
"""

from __future__ import annotations

from datetime import date

import pytest

from rbac_backend.models.contract_document import (
    ApplicableInstrument,
    Browse,
    ContractDocumentType,
    CurrentState,
    Historical,
)
from rbac_backend.services.contract_qa_evidence import (
    ContractQARequest,
    QueryModeRequired,
    resolve_precedence,
)

ORG = "org-qa"
PROJECT = "project-qa"
CONTRACT = "contract-qa"


def _instrument(document_id: str, **overrides) -> ApplicableInstrument:
    payload = {
        "contract_document_id": f"cd-{document_id}",
        "document_id": document_id,
        "document_version_id": f"{document_id}-v1",
        "organization_id": ORG,
        "project_id": PROJECT,
        "contract_id": CONTRACT,
        "contract_document_type": ContractDocumentType.GENERAL_CONDITIONS,
        "classification_revision": 1,
        "applicability_event_id": "event-1",
    }
    payload.update(overrides)
    return ApplicableInstrument(**payload)


def _request(**overrides) -> ContractQARequest:
    payload = {
        "organization_id": ORG,
        "project_id": PROJECT,
        "contract_id": CONTRACT,
        "question": "What notice is required for a variation?",
        "query_mode": CurrentState(),
    }
    payload.update(overrides)
    return ContractQARequest(**payload)


# --------------------------------------------------------------------------- #
# A-21b : the request contract
# --------------------------------------------------------------------------- #


def test_a_request_without_a_mode_is_refused():
    """A-21b."""
    with pytest.raises(QueryModeRequired) as excinfo:
        _request(query_mode=None)
    assert "no implicit current-state default" in str(excinfo.value)


def test_historical_without_a_date_is_refused():
    """A-21b. The date cannot default to today."""
    with pytest.raises(TypeError):
        Historical()  # type: ignore[call-arg]


def test_historical_with_a_non_date_is_refused_at_construction():
    """Ticket 02 already makes this unconstructable, which is stronger.

    The runtime guard here is belt-and-braces for a mode built some other way;
    the type refuses the string before Q&A ever sees it.
    """
    with pytest.raises(TypeError) as excinfo:
        Historical(event_date="2021-03-01")
    assert "must be a date" in str(excinfo.value)


def test_browse_cannot_produce_evidence():
    with pytest.raises(QueryModeRequired) as excinfo:
        _request(query_mode=Browse())
    assert "catalogue mode" in str(excinfo.value)


def test_a_question_without_a_contract_is_refused():
    with pytest.raises(QueryModeRequired):
        _request(contract_id="")


def test_a_question_without_a_project_is_refused():
    with pytest.raises(QueryModeRequired):
        _request(project_id="")


def test_a_well_formed_current_state_question_is_accepted():
    request = _request()
    assert isinstance(request.query_mode, CurrentState)


def test_a_well_formed_historical_question_is_accepted():
    request = _request(query_mode=Historical(event_date=date(2021, 3, 1)))
    assert request.query_mode.event_date == date(2021, 3, 1)


def test_the_ingestion_status_gate_is_gone_from_the_evidence_contract():
    """DEBT-11: processing state answered the wrong question entirely."""
    import inspect

    from rbac_backend.services import contract_qa_evidence

    source = inspect.getsource(contract_qa_evidence.ContractQARequest)
    for legacy in ("processing_status", "ingestion_status", "all_completed"):
        assert legacy not in source


# --------------------------------------------------------------------------- #
# A-24 : canonical override beats relevance
# --------------------------------------------------------------------------- #


def test_a_canonical_override_beats_higher_retrieval_relevance():
    """A-24."""
    gcc = _instrument("doc-gcc")
    scc = _instrument("doc-scc", contract_document_type=ContractDocumentType.PARTICULAR_CONDITIONS)

    outcome = resolve_precedence(
        (gcc, scc),
        legal_effects=[
            {
                "kind": "OVERRIDE",
                "source_contract_document_id": scc.contract_document_id,
                "target_contract_document_id": gcc.contract_document_id,
            }
        ],
        # The GCC clause is the better semantic match. It still loses.
        retrieval_ranking=[gcc.contract_document_id, scc.contract_document_id],
    )

    assert outcome.governing is scc
    assert outcome.superseded == (gcc,)
    assert outcome.is_resolved


def test_reversing_the_retrieval_ranking_changes_nothing():
    gcc = _instrument("doc-gcc")
    scc = _instrument("doc-scc")
    effects = [
        {
            "kind": "OVERRIDE",
            "source_contract_document_id": scc.contract_document_id,
            "target_contract_document_id": gcc.contract_document_id,
        }
    ]

    first = resolve_precedence((gcc, scc), legal_effects=effects, retrieval_ranking=["cd-doc-gcc"])
    second = resolve_precedence((gcc, scc), legal_effects=effects, retrieval_ranking=["cd-doc-scc"])
    assert first.governing is second.governing is scc


def test_retrieval_ranking_is_not_consulted_for_the_decision():
    import ast
    import inspect

    from rbac_backend.services import contract_qa_evidence

    source = inspect.getsource(contract_qa_evidence.resolve_precedence)
    tree = ast.parse(source.lstrip())
    # The parameter exists so a caller cannot think passing it would help; it
    # must never be read.
    reads = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id == "retrieval_ranking"
    ]
    assert len(reads) == 0


def test_supersede_also_settles_precedence():
    original = _instrument("doc-original")
    amendment = _instrument("doc-amendment", contract_document_type=ContractDocumentType.AMENDMENT)

    outcome = resolve_precedence(
        (original, amendment),
        legal_effects=[
            {
                "kind": "SUPERSEDE",
                "source_contract_document_id": amendment.contract_document_id,
                "target_contract_document_id": original.contract_document_id,
            }
        ],
    )
    assert outcome.governing is amendment


def test_instrument_type_alone_never_establishes_precedence():
    """A PCC does not beat a GCC by being a PCC."""
    gcc = _instrument("doc-gcc")
    scc = _instrument("doc-scc", contract_document_type=ContractDocumentType.PARTICULAR_CONDITIONS)

    outcome = resolve_precedence((gcc, scc), legal_effects=[])
    assert outcome.governing is None
    assert set(outcome.unresolved) == {gcc, scc}


# --------------------------------------------------------------------------- #
# A-25 : unresolved stays unresolved
# --------------------------------------------------------------------------- #


def test_unresolved_precedence_surfaces_as_unresolved():
    """A-25."""
    first = _instrument("doc-a")
    second = _instrument("doc-b")

    outcome = resolve_precedence((first, second), legal_effects=[])

    assert outcome.governing is None
    assert outcome.is_resolved is False
    assert set(outcome.unresolved) == {first, second}


def test_neither_side_is_asserted_when_precedence_is_unresolved():
    first = _instrument("doc-a")
    second = _instrument("doc-b")

    outcome = resolve_precedence((first, second), legal_effects=[])
    assert outcome.governing is not first
    assert outcome.governing is not second


def test_an_irrelevant_effect_does_not_resolve_precedence():
    first = _instrument("doc-a")
    second = _instrument("doc-b")

    outcome = resolve_precedence(
        (first, second),
        legal_effects=[
            {
                "kind": "REFERENCES",
                "source_contract_document_id": first.contract_document_id,
                "target_contract_document_id": second.contract_document_id,
            }
        ],
    )
    assert outcome.governing is None


def test_a_single_applicable_instrument_governs_without_an_effect():
    only = _instrument("doc-only")
    outcome = resolve_precedence((only,), legal_effects=[])
    assert outcome.governing is only
    assert outcome.is_resolved


def test_zero_applicable_instruments_is_an_explicit_empty_answer():
    outcome = resolve_precedence((), legal_effects=[])
    assert outcome.governing is None
    assert outcome.unresolved == ()
    assert outcome.superseded == ()


def test_only_applicable_instruments_are_accepted():
    with pytest.raises(TypeError):
        resolve_precedence(({"document_id": "doc-a"},), legal_effects=[])  # type: ignore[arg-type]
