"""Letter drafting and arbitration evidence consumers.

A-39, consumer half - contract evidence is gated by the positive eligible set,
so the two authority helpers cannot diverge in effect.

The divergence is real and deliberate: the per-row resolver arbitration calls
fails closed on a document it cannot resolve, while the shared batch filter
deliberately does not. That is only safe because the positive eligible set has
already excluded unresolvable candidates - a conditional safety, which is exactly
the kind that stops holding quietly. These tests pin the condition rather than
trusting it.
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
from rbac_backend.services.contract_drafting_evidence import (
    ConsumerModeRequired,
    build_drafting_evidence,
)

ORG = "org-draft"
PROJECT = "project-draft"
CONTRACT = "contract-draft"


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


# --------------------------------------------------------------------------- #
# only resolved evidence enters
# --------------------------------------------------------------------------- #


def test_both_consumers_accept_only_applicable_instruments():
    with pytest.raises(TypeError):
        build_drafting_evidence(
            ({"document_id": "doc-1"},),  # type: ignore[arg-type]
            query_mode=CurrentState(),
            content_by_document={},
        )


def test_a_consumer_cannot_reconstruct_authority_from_identifiers():
    """There is no scope parameter to go looking with."""
    import inspect

    parameters = set(inspect.signature(build_drafting_evidence).parameters)
    for identifier in ("project_id", "contract_id", "organization_id", "scope", "resolver"):
        assert identifier not in parameters


def test_a_consumer_must_state_its_mode():
    with pytest.raises(ConsumerModeRequired):
        build_drafting_evidence(
            (_instrument("doc-1"),), query_mode=None, content_by_document={}
        )


def test_a_consumer_cannot_draft_from_browse_results():
    with pytest.raises(ConsumerModeRequired) as excinfo:
        build_drafting_evidence(
            (_instrument("doc-1"),), query_mode=Browse(), content_by_document={}
        )
    assert "catalogue" in str(excinfo.value)


def test_historical_mode_carries_its_date_into_the_evidence():
    evidence = build_drafting_evidence(
        (_instrument("doc-1"),),
        query_mode=Historical(event_date=date(2021, 3, 1)),
        content_by_document={"doc-1": "clause text"},
    )
    assert evidence.query_mode_name == "historical"
    assert evidence.event_date == "2021-03-01"


# --------------------------------------------------------------------------- #
# A-39 : the arbitration path cannot bypass the positive set
# --------------------------------------------------------------------------- #


def test_the_arbitration_path_cannot_bypass_the_positive_eligible_set():
    """A-39. An unresolvable document never arrives, because it is not an instrument."""
    evidence = build_drafting_evidence(
        (_instrument("doc-eligible"),),
        query_mode=CurrentState(),
        content_by_document={"doc-eligible": "text", "doc-orphan": "text"},
    )
    documents = {item.instrument.document_id for item in evidence.items}
    assert documents == {"doc-eligible"}


def test_content_for_a_document_outside_the_eligible_set_is_never_emitted():
    """Extra content in the map cannot smuggle a document in."""
    evidence = build_drafting_evidence(
        (_instrument("doc-a"),),
        query_mode=CurrentState(),
        content_by_document={"doc-a": "a", "doc-b": "b", "doc-c": "c"},
    )
    assert len(evidence.items) == 1
    assert evidence.items[0].text == "a"


def test_the_two_authority_helpers_cannot_diverge_in_effect_here():
    """Whatever the batch filter would have admitted, only instruments arrive."""
    evidence = build_drafting_evidence(
        (_instrument("doc-a"),),
        query_mode=CurrentState(),
        content_by_document={"doc-a": "a"},
        # An id the batch filter would fail *open* on. It is not an instrument,
        # so it has no route into the evidence at all.
        blocked_document_ids=["doc-unresolvable"],
    )
    assert {item.instrument.document_id for item in evidence.items} == {"doc-a"}
    assert evidence.usable_items


# --------------------------------------------------------------------------- #
# blocked but applicable
# --------------------------------------------------------------------------- #


def test_a_blocked_but_applicable_instrument_is_shown_with_content_withheld():
    evidence = build_drafting_evidence(
        (_instrument("doc-blocked"),),
        query_mode=CurrentState(),
        content_by_document={"doc-blocked": "secret clause text"},
        blocked_document_ids=["doc-blocked"],
    )

    assert len(evidence.items) == 1
    item = evidence.items[0]
    assert item.content_available is False
    assert item.text is None
    assert item.withheld_reason


def test_a_blocked_instrument_still_reports_its_applicability_basis():
    """Dropping it would tell a drafter the contract is silent on the point."""
    evidence = build_drafting_evidence(
        (_instrument("doc-blocked", applicability_event_id="event-42"),),
        query_mode=CurrentState(),
        content_by_document={"doc-blocked": "secret"},
        blocked_document_ids=["doc-blocked"],
    )
    assert evidence.items[0].applicability_basis == "event-42"


def test_blocked_content_bytes_never_appear_anywhere_in_the_evidence():
    evidence = build_drafting_evidence(
        (_instrument("doc-blocked"),),
        query_mode=CurrentState(),
        content_by_document={"doc-blocked": "SECRET-CLAUSE-BODY"},
        blocked_document_ids=["doc-blocked"],
    )
    assert "SECRET-CLAUSE-BODY" not in repr(evidence)


def test_blocked_and_usable_instruments_are_separately_addressable():
    evidence = build_drafting_evidence(
        (_instrument("doc-ok"), _instrument("doc-blocked")),
        query_mode=CurrentState(),
        content_by_document={"doc-ok": "visible", "doc-blocked": "hidden"},
        blocked_document_ids=["doc-blocked"],
    )
    assert {item.instrument.document_id for item in evidence.usable_items} == {"doc-ok"}
    assert {item.instrument.document_id for item in evidence.withheld_items} == {"doc-blocked"}


# --------------------------------------------------------------------------- #
# degraded state propagates
# --------------------------------------------------------------------------- #


def test_degraded_state_propagates_into_what_the_consumer_persists():
    evidence = build_drafting_evidence(
        (_instrument("doc-a"),),
        query_mode=CurrentState(),
        content_by_document={"doc-a": "text"},
        degraded_sources=["vector"],
    )
    assert evidence.is_degraded
    assert evidence.degraded_sources == ("vector",)


def test_a_complete_run_is_not_marked_degraded():
    evidence = build_drafting_evidence(
        (_instrument("doc-a"),),
        query_mode=CurrentState(),
        content_by_document={"doc-a": "text"},
    )
    assert evidence.is_degraded is False


def test_evidence_carries_document_version_identifiers_for_provenance():
    """The consumer writes provenance; it must have the version to record."""
    evidence = build_drafting_evidence(
        (_instrument("doc-a"),),
        query_mode=CurrentState(),
        content_by_document={"doc-a": "text"},
    )
    assert evidence.items[0].instrument.document_version_id == "doc-a-v1"
    assert evidence.items[0].instrument.classification_revision == 1
