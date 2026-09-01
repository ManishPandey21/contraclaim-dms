"""Permanent premise contracts for Bank Guarantee legacy evidence.

These pin — as executable regression, not comments — the mechanically proven
facts that make BG a reconciliation-only module: repository-defined BG legacy
evidence is parent-level and carries no exact event provenance, so it cannot be
auto-mapped to a `bank_guarantee_event` without inventing ownership.

Charter: never manufacture contractual event provenance.
"""

from __future__ import annotations

import inspect

from rbac_backend.models import bank_guarantee as bg_models


def test_a_parent_carries_the_legacy_document_array() -> None:
    """`bank_guarantees.linked_document_ids` is the real legacy field, and it
    lives on the PARENT guarantee, not on any event."""
    fields = bg_models.BankGuaranteeBase.model_fields
    assert "linked_document_ids" in fields


def test_b_the_extension_history_model_has_no_document_field() -> None:
    """`BGExtensionHistory` records the extension, not its evidence: it declares
    no `linked_document_ids`, so extension-history rows produced by current
    source can never carry canonical Document ids."""
    fields = bg_models.BGExtensionHistory.model_fields
    assert "linked_document_ids" not in fields
    # It DOES carry the only real correlator, revision_number, used solely for
    # nonstandard/imported rows that happen to also carry a document id.
    assert "revision_number" in fields


def test_c_release_evidence_is_a_textual_reference_not_a_document_id() -> None:
    """Release evidence is a letter reference string, never a canonical
    Document id, so it must not become a Document relationship."""
    release_fields = bg_models.BGReleaseRequest.model_fields
    assert "linked_document_ids" not in release_fields
    # A textual reference field exists on the extension request instead.
    assert "extension_letter_reference" in bg_models.BGExtendRequest.model_fields


def test_d_the_bg_event_model_has_no_legacy_document_array() -> None:
    """Events are first-class but legacy evidence was never attached to them —
    the event model has no `linked_document_ids` to have held it."""
    assert "linked_document_ids" not in bg_models.BankGuaranteeEvent.model_fields
    # revision_number is written on extension events (the exact correlator).
    assert "revision_number" in bg_models.BankGuaranteeEvent.model_fields


def test_e_the_legacy_extension_writer_targets_the_parent_not_the_event() -> None:
    """Guards against a future edit that would start writing evidence onto the
    event or the history row: the current extend() path must not attach
    `linked_document_ids` to `bank_guarantee_events` or `bg_extension_history`.

    Read the source of the service rather than trusting a comment.
    """
    from rbac_backend.services import bank_guarantee_service

    source = inspect.getsource(bank_guarantee_service)
    # No writer path may put a document array onto an event or history record.
    assert "bank_guarantee_events" in source  # sanity: events are written
    for forbidden in (
        'event["linked_document_ids"]',
        'history["linked_document_ids"]',
        'event.linked_document_ids',
    ):
        assert forbidden not in source, (
            f"legacy BG evidence must not be attached to events: {forbidden}"
        )
