"""F2: a source's identity is not its fingerprint.

`_merge_source_ledgers` carries parent-version sources forward so citation
tokens in unregenerated sections keep resolving. Its dedupe key was

    (source_type, source_id, source_hash)

and `source_hash` is computed over the whole ledger row - including `snippet`,
`verification_status` and `authority_denied`. So the moment a document is sent
to human review, the freshly-built DENIED row hashes differently from the
stored AUTHORISED one, misses the dedupe, and is APPENDED. The stale row
survives beside it, and `generator._facts()` reads `row["snippet"]` off both.

Identity and revision are different things. `(source_type, source_id)` plus the
matrix row that produced it is the stable logical identity; the hash, snippet,
quality flags and authority state are mutable attributes OF that identity.

Supersession rather than removal: the parent row keeps its `source_key`, so
`[S3]` in an unregenerated section still resolves, but its content and
authority state are replaced by the current build. Dropping the row instead
would dangle every citation to it.
"""

from __future__ import annotations

from typing import Any, Dict, List

from rbac_backend.services.arbitration_drafting.service import (
    ArbitrationDraftingService,
)

CLEAN_SNIPPET = "the Employer admitted liability on 2024-03-01"
DENIED_LABEL = "Delay notice"

_merge = ArbitrationDraftingService._merge_source_ledgers


def _row(**extra: Any) -> Dict[str, Any]:
    row = {
        "source_key": "S1",
        "source_type": "document",
        "source_id": "doc-1",
        "label": DENIED_LABEL,
        "snippet": CLEAN_SNIPPET,
        "verification_status": "verified",
        "quality_flags": [],
        "evidence_strength": "strong",
        "source_hash": "hash-clean",
        "metadata": {"matrix_row_id": "idx-1"},
    }
    row.update(extra)
    return row


def _denied() -> Dict[str, Any]:
    """What the current build produces once the document is blocked."""
    return _row(
        source_key="S1",
        snippet=DENIED_LABEL,
        authority_denied=True,
        verification_status="authority_denied",
        quality_flags=["authority_denied"],
        evidence_strength="needs_review",
        source_hash="hash-denied",
    )


def _snippets(rows: List[Dict[str, Any]]) -> List[str]:
    return [str(row.get("snippet") or "") for row in rows]


# --- the defect ----------------------------------------------------------------


def test_a_blocked_rebuild_supersedes_the_stale_authorised_row() -> None:
    merged = _merge([_row()], [_denied()])

    assert len(merged) == 1, (
        "the denied row was appended beside the stale authorised one because "
        "source_hash is part of the identity key"
    )


def test_the_stale_snippet_is_gone_after_the_source_is_blocked() -> None:
    merged = _merge([_row()], [_denied()])

    assert CLEAN_SNIPPET not in " ".join(_snippets(merged))


def test_the_current_authority_state_wins() -> None:
    merged = _merge([_row()], [_denied()])

    assert merged[0].get("authority_denied") is True
    assert merged[0].get("evidence_strength") != "strong"
    assert merged[0].get("verification_status") == "authority_denied"


def test_the_citation_key_is_preserved_so_unregenerated_sections_still_resolve() -> None:
    """Supersede, do not delete: [S1] elsewhere in the pleading must survive."""
    merged = _merge([_row(source_key="S3")], [_denied()])

    assert merged[0]["source_key"] == "S3"


# --- revision vs authority -----------------------------------------------------


def test_an_unchanged_source_is_not_duplicated() -> None:
    merged = _merge([_row()], [_row()])

    assert len(merged) == 1
    assert merged[0]["snippet"] == CLEAN_SNIPPET


def test_a_content_revision_updates_in_place_rather_than_appending() -> None:
    revised = _row(snippet="the Employer admitted liability on 2024-03-02", source_hash="hash-2")
    merged = _merge([_row()], [revised])

    assert len(merged) == 1
    assert merged[0]["snippet"] == "the Employer admitted liability on 2024-03-02"


def test_an_operational_failure_keeps_the_source_usable() -> None:
    """Model B. `failed` is not an authority change, so the row stays strong."""
    still_clean = _row(source_hash="hash-after-retry")
    merged = _merge([_row()], [still_clean])

    assert len(merged) == 1
    assert merged[0]["snippet"] == CLEAN_SNIPPET
    assert merged[0]["evidence_strength"] == "strong"


# --- distinct sources must stay distinct ---------------------------------------


def test_a_different_source_id_is_a_different_source() -> None:
    other = _row(source_id="doc-2", source_key="S9", metadata={"matrix_row_id": "idx-2"})
    merged = _merge([_row()], [other])

    assert len(merged) == 2


def test_a_different_source_type_is_a_different_source() -> None:
    other = _row(source_type="clause", metadata={"matrix_row_id": "cm-1"})
    merged = _merge([_row()], [other])

    assert len(merged) == 2


def test_two_matrix_rows_over_one_document_are_not_collapsed() -> None:
    """`source_id` alone is not identity: two index rows can cite one document."""
    second = _row(source_key="S2", metadata={"matrix_row_id": "idx-2"})
    merged = _merge([_row(), second], [])

    assert len(merged) == 2


def test_a_parent_source_absent_from_the_current_build_is_carried_forward() -> None:
    """The whole reason the merge exists."""
    merged = _merge([_row(source_key="S4")], [])

    assert len(merged) == 1
    assert merged[0]["source_key"] == "S4"


def test_a_brand_new_source_is_appended_with_a_fresh_key() -> None:
    fresh = _row(source_id="doc-9", source_key="S1", metadata={"matrix_row_id": "idx-9"})
    merged = _merge([_row(source_key="S1")], [fresh])

    assert len(merged) == 2
    assert {row["source_key"] for row in merged} == {"S1", "S2"}
