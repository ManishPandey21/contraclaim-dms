"""An authority-denied source must not report as verified/strong.

Review D found the mechanism: when authority denies a reference, its snippet
falls back to the label. `_annotate_source_quality` only flagged
`missing_snippet` when the snippet was EMPTY - and a label is not empty - so the
row kept `verification_status` and was scored `strong` while carrying no
supporting content at all. Empty citations reaching a pleading, undetected.

Three things are distinct and were being conflated:

    the source exists
        != authority accepted it
            != actual supporting content is available
"""

from __future__ import annotations

from rbac_backend.services.arbitration_drafting.context import ArbitrationContextBuilder


def _annotate(rows):
    builder = ArbitrationContextBuilder.__new__(ArbitrationContextBuilder)
    warnings = []
    builder._annotate_source_quality(rows, warnings)
    return rows, warnings


def test_an_authority_denied_source_is_not_strong() -> None:
    rows, _ = _annotate(
        [
            {
                "citation": "C-001",
                "snippet": "C-001",  # collapsed to the label
                "authority_denied": True,
            }
        ]
    )

    assert "authority_denied" in rows[0]["quality_flags"]
    assert rows[0]["evidence_strength"] != "strong"


def test_a_denied_source_with_a_non_empty_label_still_flags() -> None:
    """The exact hazard: non-empty snippet hid the denial."""
    rows, _ = _annotate(
        [{"citation": "X", "snippet": "Some Exhibit Title", "authority_denied": True}]
    )

    assert rows[0]["evidence_strength"] == "needs_review"


def test_a_healthy_source_is_still_strong() -> None:
    """The fix must not downgrade legitimate evidence."""
    rows, _ = _annotate(
        [
            {
                "citation": "C-002",
                "snippet": "Full extracted clause text supporting the claim.",
                "authority_denied": False,
            }
        ]
    )

    assert rows[0]["quality_flags"] == []
    assert rows[0]["evidence_strength"] == "strong"


def test_a_source_without_the_marker_is_unaffected() -> None:
    """Rows built by other paths must not be spuriously downgraded."""
    rows, _ = _annotate([{"citation": "C-003", "snippet": "Real supporting text."}])

    assert rows[0]["evidence_strength"] == "strong"
