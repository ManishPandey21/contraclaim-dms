"""G24: EXTRACTION_FALLBACK_ENABLED must actually control execution.

The flag existed in config, `.env.example`, compose, the status script and the
runbook - and had **zero** production readers. Nothing constructed a ladder, so
the ladder was inert because the code did not exist, not because the flag was
false. The documented rollback lever did nothing.

The committed specification (plan line 8829) requires:

    ladder = None
    if settings.EXTRACTION_FALLBACK_ENABLED and source_kind in {PDF, IMAGE}:
        ladder = ExtractionFallbackLadder(...)

These tests drive the real `DocumentProcessor` construction path. Asserting on
`settings.EXTRACTION_FALLBACK_ENABLED` would prove only that a variable exists,
which was already true while the defect was live.
"""

from __future__ import annotations

import pytest

from rbac_backend.services.document_processor import DocumentProcessor
from rbac_backend.services.extraction.models import SourceKind


def _resolve(processor: DocumentProcessor, kind, *, enabled: bool):
    """Ask the processor what ladder it would use for this source kind."""
    return processor.resolve_fallback_ladder(kind, enabled=enabled, db=None)


def test_the_processor_exposes_a_ladder_decision() -> None:
    """Without a production decision point the flag cannot control anything."""
    assert hasattr(DocumentProcessor, "resolve_fallback_ladder"), (
        "no production code path decides whether to build a fallback ladder, "
        "so EXTRACTION_FALLBACK_ENABLED controls nothing"
    )


def test_flag_off_builds_no_ladder() -> None:
    processor = DocumentProcessor.__new__(DocumentProcessor)

    assert _resolve(processor, SourceKind.PDF, enabled=False) is None


@pytest.mark.parametrize("kind", [SourceKind.PDF, SourceKind.IMAGE])
def test_flag_on_with_an_eligible_source_builds_a_ladder(kind) -> None:
    processor = DocumentProcessor.__new__(DocumentProcessor)
    processor.quality_gate = object()

    assert _resolve(processor, kind, enabled=True) is not None, (
        f"{kind} is an eligible source but no ladder was built"
    )


@pytest.mark.parametrize("kind", [SourceKind.TEXT, SourceKind.ARCHIVE])
def test_flag_on_with_an_ineligible_source_builds_no_ladder(kind) -> None:
    """The spec restricts the ladder to PDF and IMAGE."""
    processor = DocumentProcessor.__new__(DocumentProcessor)
    processor.quality_gate = object()

    assert _resolve(processor, kind, enabled=True) is None


def test_an_unknown_source_kind_builds_no_ladder() -> None:
    """Fail closed: an unrecognised kind never opts into paid model calls."""
    processor = DocumentProcessor.__new__(DocumentProcessor)
    processor.quality_gate = object()

    assert _resolve(processor, None, enabled=True) is None


def test_the_flag_changes_behaviour() -> None:
    """The whole point: off and on must not be the same execution."""
    processor = DocumentProcessor.__new__(DocumentProcessor)
    processor.quality_gate = object()

    off = _resolve(processor, SourceKind.PDF, enabled=False)
    on = _resolve(processor, SourceKind.PDF, enabled=True)

    assert off is None and on is not None, (
        "flag off and flag on produced identical behaviour, so the documented "
        "rollback control does not exist"
    )


def test_the_default_is_off() -> None:
    from rbac_backend.core.config import Settings

    assert Settings.model_fields["EXTRACTION_FALLBACK_ENABLED"].default is False
