"""Contract Master v1 domain types — the categorical values and the evidence
result boundary.

Owned by implementation ticket 02. Two release gates live here:

``A-06``
    A ``BrowseResult`` — or a bare dict shaped like one — cannot be passed where
    an ``ApplicableInstrument`` is required. Browse answers *"what exists in the
    catalogue?"*; evidence answers *"what governs this contract?"*. Conflating
    them is how a catalogue entry becomes legal evidence.

``A-21a``
    ``Historical`` is unconstructable without an ``event_date``. This is the
    TYPE-LEVEL half only. The runtime half — a Q&A request that omits the mode
    or the date being refused — is ``A-21b`` and belongs to ticket 22. A test
    here must never pass merely because some later service validates input.

The tests assert construction and acceptance semantics, not that a validator
helper ran. A test that only proved "the checking function was called" would
pass while the check itself did nothing.
"""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from rbac_backend.models.contract_document import (
    ApplicabilityLifecycleKind,
    ApplicableInstrument,
    Browse,
    BrowseResult,
    ContractDocumentType,
    ContractScopeLevel,
    CurrentState,
    Historical,
    LegalEffectKind,
    LegalEffectScope,
    ProjectionStatus,
    PromotionState,
    ScopeClassification,
    TypeClassification,
    require_applicable_instruments,
)

# --------------------------------------------------------------------------- #
# Criterion 1 — the categorical values are categorical
# --------------------------------------------------------------------------- #


def test_contract_scope_level_is_an_explicit_two_member_discriminator() -> None:
    assert {member.value for member in ContractScopeLevel} == {"organization", "project"}


def test_contract_document_type_has_seventeen_members() -> None:
    assert len(list(ContractDocumentType)) == 17


def test_contract_document_type_has_no_unknown_member() -> None:
    """UNKNOWN is not an instrument. An unresolved type is a CLASSIFICATION
    state, tracked separately by TypeClassification."""
    names = {member.name for member in ContractDocumentType}
    values = {member.value for member in ContractDocumentType}
    assert "UNKNOWN" not in names
    assert "unknown" not in values


def test_other_contractual_document_is_an_affirmative_member_not_a_fallback() -> None:
    """It exists as a real classification an operator can choose, and nothing in
    the enum marks it as a default."""
    assert ContractDocumentType.OTHER_CONTRACTUAL_DOCUMENT.value == "other_contractual_document"
    with pytest.raises(ValueError):
        ContractDocumentType("")
    with pytest.raises(ValueError):
        ContractDocumentType("unknown")


def test_instrument_type_and_classification_state_are_different_vocabularies() -> None:
    """TYPE_RESOLVED is a state of knowing; general_conditions is a thing known.

    Overlapping these is the axis collision the programme already carries in the
    legacy clause field, and the target model must not repeat it.
    """
    type_values = {member.value for member in ContractDocumentType}
    state_values = {member.value for member in TypeClassification}
    assert not (type_values & state_values)


def test_instrument_type_never_overlaps_the_clause_subject_taxonomy() -> None:
    """CategoryKey answers 'what subject does this clause concern?'.

    ContractDocumentType answers 'what legal instrument is this?'. A value that
    satisfied both would let a subject masquerade as an instrument.
    """
    from rbac_backend.services.contract_categorizer import CategoryKey

    instrument_values = {member.value for member in ContractDocumentType}
    subject_values = {member.value for member in CategoryKey}
    assert not (instrument_values & subject_values)


def test_lifecycle_effect_and_scope_enums_are_closed() -> None:
    assert {m.value for m in ApplicabilityLifecycleKind} == {"APPLIED", "WITHDRAWN", "SUPERSEDED"}
    assert {m.value for m in LegalEffectKind} == {"OVERRIDE", "SUPERSEDE"}
    assert {m.value for m in LegalEffectScope} == {"WHOLE_DOCUMENT", "CLAUSE_REFERENCES"}
    assert {m.value for m in ScopeClassification} == {
        "PROJECT_SCOPE_RESOLVED",
        "ORG_SCOPE_CONFIRMED",
        "AMBIGUOUS",
        "INVALID",
    }


def test_projection_status_has_the_four_derived_states() -> None:
    assert {m.value for m in ProjectionStatus} == {"PENDING", "IN_PROGRESS", "CURRENT", "FAILED"}


def test_promotion_state_has_no_evidence_capable_member() -> None:
    """EVIDENCE_CAPABLE is a query-time conjunction of applicability, document
    authority, projection currency and actor authorisation.

    Storing it as a promotion state would freeze a judgement whose inputs change
    independently of the instrument — a second authority source.
    """
    names = {member.name for member in PromotionState}
    assert names == {"RECONCILIATION", "AUTHORITATIVE", "QUARANTINED"}
    assert "EVIDENCE_CAPABLE" not in names


def test_no_enum_in_this_module_encodes_evidence_readiness() -> None:
    """Guards the whole module, not just PromotionState, against readiness
    leaking back in as a stored value."""
    from rbac_backend.models import contract_document as module

    for name in dir(module):
        attribute = getattr(module, name)
        if isinstance(attribute, type) and issubclass(attribute, __import__("enum").Enum):
            member_names = {member.name for member in attribute}
            offenders = {n for n in member_names if "EVIDENCE" in n or "READY" in n}
            assert not offenders, f"{name} encodes evidence readiness: {offenders}"


# --------------------------------------------------------------------------- #
# Criterion 2 / gate A-21a — Historical is unconstructable without a date
# --------------------------------------------------------------------------- #


def test_a21a_historical_cannot_be_constructed_without_an_event_date() -> None:
    """TYPE-LEVEL gate. Not satisfied by a later runtime refusal (that is A-21b,
    ticket 22): the invalid state must be impossible to build at all."""
    with pytest.raises(TypeError):
        Historical()  # type: ignore[call-arg]


def test_a21a_historical_carries_its_date_and_is_frozen() -> None:
    mode = Historical(event_date=date(2026, 3, 1))
    assert mode.event_date == date(2026, 3, 1)
    with pytest.raises(dataclasses.FrozenInstanceError):
        mode.event_date = date(2026, 4, 1)  # type: ignore[misc]


def test_a21a_historical_rejects_a_none_event_date() -> None:
    """A nullable date would reintroduce 'no date' through the back door."""
    with pytest.raises((TypeError, ValueError)):
        Historical(event_date=None)  # type: ignore[arg-type]


def test_query_mode_is_a_closed_sum_of_exactly_three_shapes() -> None:
    from rbac_backend.models.contract_document import ApplicabilityQueryMode

    import typing

    members = set(typing.get_args(ApplicabilityQueryMode))
    assert members == {Browse, CurrentState, Historical}


def test_browse_and_current_state_carry_no_date_at_all() -> None:
    """There is no implicit 'today' hiding on the non-historical modes."""
    for mode_type in (Browse, CurrentState):
        field_names = {f.name for f in dataclasses.fields(mode_type)}
        assert not field_names, f"{mode_type.__name__} carries state: {field_names}"


# --------------------------------------------------------------------------- #
# Criterion 3 / gate A-06 — browse cannot enter evidence
# --------------------------------------------------------------------------- #


def _instrument() -> ApplicableInstrument:
    return ApplicableInstrument(
        contract_document_id="cd-1",
        document_id="doc-1",
        document_version_id="ver-1",
        organization_id="org-1",
        project_id="proj-1",
        contract_id="primary",
        contract_document_type=ContractDocumentType.GENERAL_CONDITIONS,
        classification_revision=1,
        applicability_event_id="evt-1",
    )


def _browse_result() -> BrowseResult:
    return BrowseResult(
        contract_document_id="cd-1",
        organization_id="org-1",
        contract_document_type=ContractDocumentType.GENERAL_CONDITIONS,
        scope_level=ContractScopeLevel.ORGANIZATION,
        applicability_count=0,
    )


def test_browse_result_and_applicable_instrument_are_unrelated_types() -> None:
    """Not one shape with a flag: neither may be substituted for the other."""
    assert not issubclass(BrowseResult, ApplicableInstrument)
    assert not issubclass(ApplicableInstrument, BrowseResult)


def test_a06_a_browse_result_is_refused_where_an_instrument_is_required() -> None:
    with pytest.raises(TypeError) as excinfo:
        require_applicable_instruments([_browse_result()])  # type: ignore[list-item]
    assert "BrowseResult" in str(excinfo.value)


def test_a06_a_bare_dict_is_refused_even_when_shaped_correctly() -> None:
    """The mutation target. A duck-typed mapping carrying every right key must
    still be refused, or the boundary is decorative."""
    lookalike = {
        "contract_document_id": "cd-1",
        "document_id": "doc-1",
        "document_version_id": "ver-1",
        "organization_id": "org-1",
        "project_id": "proj-1",
        "contract_id": "primary",
        "contract_document_type": ContractDocumentType.GENERAL_CONDITIONS,
        "classification_revision": 1,
        "applicability_event_id": "evt-1",
    }
    with pytest.raises(TypeError):
        require_applicable_instruments([lookalike])  # type: ignore[list-item]


def test_a06_a_single_bad_element_refuses_the_whole_batch() -> None:
    """Partial acceptance would let one browse row ride in beside real evidence."""
    with pytest.raises(TypeError):
        require_applicable_instruments([_instrument(), _browse_result()])  # type: ignore[list-item]


def test_a06_genuine_instruments_are_accepted_and_returned_unchanged() -> None:
    instrument = _instrument()
    accepted = require_applicable_instruments([instrument])
    assert accepted == (instrument,)


def test_applicable_instrument_requires_the_identifiers_provenance_needs() -> None:
    """The consumer writes provenance but must never reconstruct these."""
    with pytest.raises((TypeError, ValueError)):
        ApplicableInstrument(  # type: ignore[call-arg]
            contract_document_id="cd-1",
            document_id="doc-1",
            organization_id="org-1",
            project_id="proj-1",
            contract_id="primary",
            contract_document_type=ContractDocumentType.GENERAL_CONDITIONS,
            classification_revision=1,
            applicability_event_id="evt-1",
        )
