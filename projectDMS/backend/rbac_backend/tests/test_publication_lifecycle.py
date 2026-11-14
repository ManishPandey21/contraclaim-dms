"""G33: the policy must not hide valid documents.

The publication policy was built as an ALLOW-LIST over `processing_status`:
consumable meant `completed` or `stored_only`, and anything else - including
anything unrecognised - was denied.

Two things make that wrong, both established from production code:

1. `metadata_extracted` is the terminal SUCCESS state of a normally processed
   document. `_checkpoint_extraction_attempt` writes `completed`
   (`document_service.py:1133`), then the caller sets `metadata_extracted`
   (`:1687`) and applies it last (`:1777`). `completed` never survives on the
   normal path. So the allow-list excluded the state every healthy document
   actually ends in.

2. The real vocabulary is wider than `ProcessingState`: `retrying`, `skipped`
   and `metadata_extracted` are written as bare strings and appear in no enum.

So the policy denied the main path. That is the opposite failure from G18 -
safe content incorrectly withheld rather than unsafe content admitted - and the
system has to satisfy both properties at once.

The corrected model, following the last-known-good lifecycle decision:

    content is authoritative UNLESS something has judged it adverse

An adverse judgement is a terminal verdict against the latest run -
`human_review_required` or `failed`. Everything else is either verified or
not-yet-judged, and under last-known-good a run in flight does not retract the
previous publication. A reprocess starting is not evidence the old content is
bad; only an outcome is.

This inverts the policy from an allow-list on success to a deny-list on adverse
judgement, which is why an unclassified state no longer silently hides a
document. Vocabulary drift is caught by a test instead - see
`test_no_unclassified_state_is_written_by_production_code`.
"""

from __future__ import annotations

import pytest

from rbac_backend.services.publication_policy import (
    ADVERSE_STATES,
    authoritative_summary,
    authoritative_text,
    is_consumable,
)

TEXT = "AUTHORITATIVE CLAIM TEXT"


def _doc(status=None, **extra):
    document = {"_id": "doc-1", "ocrText": TEXT}
    if status is not None:
        document["processing_status"] = status
    document.update(extra)
    return document


# --- Availability: the states a healthy document actually rests in ------------


def test_metadata_extracted_is_consumable() -> None:
    """The terminal success state of the normal path.

    If this is denied, every successfully processed document disappears from
    drafting, planning, arbitration, chronology and retrieval.
    """
    assert is_consumable(_doc("metadata_extracted")) is True
    assert authoritative_text(_doc("metadata_extracted")) == TEXT


def test_completed_is_consumable() -> None:
    assert is_consumable(_doc("completed")) is True


def test_stored_only_is_consumable() -> None:
    """An archive stored without extraction has no text to leak."""
    assert is_consumable(_doc("stored_only")) is True


def test_skipped_is_consumable() -> None:
    """The duplicate path. Nothing judged this content adverse.

    If no text was written, `authoritative_text` returns empty anyway - so
    treating it as consumable leaks nothing and avoids hiding a real document.
    """
    assert is_consumable(_doc("skipped")) is True


# --- Availability under last-known-good: in-flight must not retract -----------


@pytest.mark.parametrize(
    "status", ["queued", "processing", "retrying", "partially_processed"]
)
def test_an_in_flight_run_does_not_retract_the_previous_publication(
    status: str,
) -> None:
    """Model B. A reprocess starting is not a verdict on the old content.

    Under the alternative, every routine retry - `retrying` is written on two
    paths with max_attempts=3 - would make a good document vanish from drafting
    mid-window, and a worker crash would strand it there indefinitely.
    """
    assert is_consumable(_doc(status)) is True
    assert authoritative_text(_doc(status)) == TEXT


# --- Safety: adverse judgements still deny ------------------------------------


@pytest.mark.parametrize("status", ["human_review_required"])
def test_an_adverse_judgement_denies_consumption(status: str) -> None:
    assert is_consumable(_doc(status)) is False
    assert authoritative_text(_doc(status)) == ""
    assert authoritative_summary(_doc(status, summary="S")) == ""


def test_the_adverse_set_is_only_the_quality_verdict() -> None:
    """`failed` is operational and was removed: both its production writers are
    pipeline breakage, not a judgement about the document."""
    assert ADVERSE_STATES == frozenset({"human_review_required"})


# --- Historical rows ----------------------------------------------------------


def test_a_historical_document_without_the_field_is_consumable() -> None:
    """`processing_status` postdates most of the corpus."""
    assert is_consumable(_doc(None)) is True


def test_a_historical_document_reprocessed_and_blocked_is_denied() -> None:
    """Entering the modern pipeline gives a legacy row modern semantics.

    This is the case that matters: for most of the corpus a reprocess is the
    FIRST time the quality gate has ever seen the document, so a blocked
    outcome means "we finally looked and it is corrupt", not "one bad run".
    """
    legacy = _doc(None)
    assert is_consumable(legacy) is True

    legacy["processing_status"] = "human_review_required"
    assert is_consumable(legacy) is False


def test_a_historical_document_reprocessed_and_passed_stays_consumable() -> None:
    legacy = _doc(None)
    legacy["processing_status"] = "metadata_extracted"

    assert is_consumable(legacy) is True


# --- Vocabulary drift ---------------------------------------------------------


def test_an_unclassified_state_does_not_silently_hide_a_document() -> None:
    """The G33 mechanism itself.

    Denying an unrecognised state at runtime is what hid the main path. The
    guard against drift is the test below, which fails when production starts
    writing a value nobody classified - a build failure, not a silent outage.
    """
    assert is_consumable(_doc("some_future_state")) is True


def test_no_unclassified_state_is_written_by_production_code() -> None:
    """Fail the build when a new processing_status value appears.

    This is the safety net that lets the runtime rule stay permissive. If
    someone adds a state, they have to decide here whether it is adverse.
    """
    import re
    from pathlib import Path

    from rbac_backend.services.publication_policy import KNOWN_STATES

    backend = Path(__file__).resolve().parents[1]
    written = set()
    pattern = re.compile(r"""processing_status["']?\]?\s*[:=]\s*["']([a-z_]+)["']""")
    for path in backend.rglob("*.py"):
        if "tests" in path.parts:
            continue
        for match in pattern.finditer(path.read_text(encoding="utf-8")):
            written.add(match.group(1))

    unclassified = written - KNOWN_STATES
    assert not unclassified, (
        f"processing_status values written by production code but not "
        f"classified in publication_policy: {sorted(unclassified)}. "
        f"Decide explicitly whether each is an adverse judgement."
    )


# --- Safety holes found by independent review ---------------------------------


def test_a_confirmed_duplicate_is_not_consumable() -> None:
    """Quarantine is a second axis the policy was blind to.

    Duplicate detection sets duplicate_status/lifecycle_state and never touches
    processing_status, which by then already reads `metadata_extracted`. So a
    document the system had explicitly quarantined was declared fully
    authoritative by the gate every consumer trusts.
    """
    assert is_consumable(_doc("metadata_extracted", duplicate_status="duplicate")) is False
    assert is_consumable(_doc("metadata_extracted", lifecycle_state="duplicate")) is False
    assert authoritative_text(_doc("metadata_extracted", lifecycle_state="duplicate")) == ""


def test_a_soft_deleted_document_is_not_consumable() -> None:
    assert is_consumable(_doc("metadata_extracted", lifecycle_state="deleted")) is False


def test_a_pending_duplicate_is_still_consumable() -> None:
    """Only a CONFIRMED duplicate is quarantined; pending is undecided."""
    assert is_consumable(_doc("metadata_extracted", duplicate_status="pending")) is True
