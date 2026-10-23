"""Class A, closed in one place instead of five.

`authority_denied` was written by exactly one of the ledger builders. The
others resolve authority to gate their snippet, then discard the decision, so
`_annotate_source_quality` - which downgrades a denied row - was dead for them
and a blocked document scored `strong`/`verified`.

Rather than add the flag to five dict literals (one more builder and it drifts
again), `_annotate_source_quality` re-resolves authority from each row's own
`source_type`/`source_id` through the shared resolver. A builder cannot emit a
document-governed row that escapes the check, because the check no longer
depends on the builder.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from rbac_backend.services.arbitration_drafting.context import (
    ArbitrationContextBuilder,
    VERIFIED_SOURCE_STATUSES,
)


class _Cursor:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = list(rows)

    async def to_list(self, length: Optional[int] = None) -> List[Dict[str, Any]]:
        return list(self.rows)


class _Collection:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    async def find_one(self, query: Dict[str, Any], *a: Any, **k: Any):
        expected = query.get("_id")
        for row in self.rows:
            if isinstance(expected, dict) and "$in" in expected:
                if any(str(row.get("_id")) == str(c) for c in expected["$in"]):
                    return row
            elif str(row.get("_id")) == str(expected):
                return row
        return None


class _DB:
    def __init__(self, **collections: List[Dict[str, Any]]) -> None:
        self._c = {n: _Collection(r) for n, r in collections.items()}

    def __getitem__(self, name: str) -> _Collection:
        return self._c.setdefault(name, _Collection([]))

    def __getattr__(self, name: str) -> _Collection:
        return self[name]


def _annotated(row: Dict[str, Any], **collections: List[Dict[str, Any]]) -> Dict[str, Any]:
    builder = ArbitrationContextBuilder(_DB(**collections))
    rows = [row]
    asyncio.run(builder._annotate_source_quality(rows, []))
    return rows[0]


def _doc_row(**extra: Any) -> Dict[str, Any]:
    """A document-index-shaped ledger row that looks fully clean on its face."""
    row = {
        "source_key": "S1",
        "source_type": "document",
        "source_id": "doc-1",
        "citation": "C-1",
        "snippet": "Some non-empty label text",
        "verification_status": "approved",
        "quality_flags": [],
        "exhibit_id": "C-1",
        "source_origin": "case_document_index",
    }
    row.update(extra)
    return row


CLEAN = {"_id": "doc-1", "processing_status": "metadata_extracted"}
BLOCKED = {"_id": "doc-1", "processing_status": "human_review_required"}
FAILED = {"_id": "doc-1", "processing_status": "failed"}
DUPLICATE = {"_id": "doc-1", "processing_status": "metadata_extracted", "duplicate_status": "duplicate"}


def test_a_clean_document_row_scores_normally() -> None:
    row = _annotated(_doc_row(), documents=[CLEAN])

    assert "authority_denied" not in row["quality_flags"]
    assert row["verification_status"] in VERIFIED_SOURCE_STATUSES


def test_a_blocked_document_row_is_flagged_by_the_central_check() -> None:
    row = _annotated(_doc_row(), documents=[BLOCKED])

    assert "authority_denied" in row["quality_flags"], (
        "the ledger row for a blocked document scored clean because the builder "
        "did not set the flag and the annotator did not re-resolve authority"
    )
    assert row["evidence_strength"] != "strong"


def test_a_quarantined_document_row_is_flagged() -> None:
    row = _annotated(_doc_row(), documents=[DUPLICATE])

    assert "authority_denied" in row["quality_flags"]


def test_an_operational_failure_row_stays_strong() -> None:
    """Model B: a worker crash is not an authority verdict."""
    row = _annotated(_doc_row(), documents=[FAILED])

    assert "authority_denied" not in row["quality_flags"]
    assert row["evidence_strength"] == "strong"


def test_a_blocked_clause_row_resolves_through_its_parent() -> None:
    clause_row = _doc_row(
        source_type="clause",
        source_id="clause-9",
        source_origin="case_clause_matrix",
    )
    row = _annotated(
        clause_row,
        contract_clauses=[{"_id": "clause-9", "document_id": "doc-1"}],
        documents=[BLOCKED],
    )

    assert "authority_denied" in row["quality_flags"]


def test_a_manual_fact_is_not_authority_denied() -> None:
    """An independent application source has no document to block."""
    row = _annotated(
        _doc_row(source_type="manual_fact", source_id="mf-1", source_origin="manual"),
        documents=[BLOCKED],
    )

    assert "authority_denied" not in row["quality_flags"]


def test_a_claim_row_is_not_authority_denied() -> None:
    row = _annotated(
        _doc_row(source_type="claim", source_id="claim-1", source_origin="case_claim_matrix"),
        documents=[BLOCKED],
    )

    assert "authority_denied" not in row["quality_flags"]


def test_an_explicit_builder_denial_is_still_honoured() -> None:
    """A row the builder already denied must stay denied even if resolution can't run."""
    row = _annotated(_doc_row(authority_denied=True), documents=[])

    assert "authority_denied" in row["quality_flags"]
    assert row["evidence_strength"] != "strong"
