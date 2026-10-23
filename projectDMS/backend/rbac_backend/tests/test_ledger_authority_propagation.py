"""A safety flag that does not survive row construction is not a control.

`_rehydrate_direct_reference` computes `authority_denied` (`context.py:449`) and
returns it on the hydrated reference. `_ledger_row` (`:482`) then rebuilds the
ledger row from a FIXED KEY SET that does not include it, and hard-codes
`verification_status` from `metadata` - which this path always sets to
"verified" (`:474`).

So `_annotate_source_quality`'s `if row.get("authority_denied")` (`:1620`) can
never be true for a real reference. A blocked document reached the drafting
prompt reported as strong, verified and flag-free, with its snippet silently
collapsed to the label - and `missing_snippet` cannot fire either, because the
label is non-empty. That is exactly the failure the comment above that check
claims to have fixed.

It stayed green because `test_arbitration_source_quality.py` hand-builds a
ledger dict containing `authority_denied` instead of going through
`_ledger_row`. These tests go through the real construction path, because the
defect IS the construction boundary.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from rbac_backend.services.arbitration_drafting.context import (
    VERIFIED_SOURCE_STATUSES,
    ArbitrationContextBuilder,
)

BODY = "THE BLOCKED DOCUMENT BODY"
DRAFT = {"organization_id": "org-1", "project_id": "project-1", "case_id": "case-1"}


class _Collection:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    async def find_one(self, query: Dict[str, Any], *a: Any, **k: Any):
        for row in self.rows:
            if _matches(row, query):
                return row
        return None


def _matches(row: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for key, expected in query.items():
        actual = row.get(key)
        if isinstance(expected, dict) and "$in" in expected:
            if not any(str(actual) == str(c) for c in expected["$in"]):
                return False
            continue
        if str(actual) != str(expected):
            return False
    return True


class _DB:
    def __init__(self, **collections: List[Dict[str, Any]]) -> None:
        self._collections = {n: _Collection(r) for n, r in collections.items()}

    def __getitem__(self, name: str) -> _Collection:
        return self._collections.setdefault(name, _Collection([]))

    def __getattr__(self, name: str) -> _Collection:
        return self[name]


def _document(status: str) -> Dict[str, Any]:
    return {
        "_id": "doc-1",
        "organization_id": "org-1",
        "project_id": "project-1",
        "subject": "Delay notice",
        "ocrText": BODY,
        "processing_status": status,
    }


def _ledger_entry(status: str) -> Dict[str, Any]:
    """Hydrate -> build the ledger row -> annotate, exactly as production does."""
    builder = ArbitrationContextBuilder(_DB(documents=[_document(status)]))
    reference = asyncio.run(
        builder._rehydrate_direct_reference(
            DRAFT, {"source_type": "document", "source_id": "doc-1"}
        )
    )
    assert reference is not None, "the reference should still hydrate"
    row = builder._ledger_row(reference, 1)
    asyncio.run(builder._annotate_source_quality([row], []))
    return row


# --- the premise, established through the real hydrator ------------------------


def test_the_hydrator_does_compute_the_denial() -> None:
    builder = ArbitrationContextBuilder(_DB(documents=[_document("human_review_required")]))
    reference = asyncio.run(
        builder._rehydrate_direct_reference(
            DRAFT, {"source_type": "document", "source_id": "doc-1"}
        )
    )

    assert reference["authority_denied"] is True
    assert BODY not in str(reference["snippet"])


# --- the construction boundary -------------------------------------------------


def test_the_denial_survives_ledger_row_construction() -> None:
    row = _ledger_entry("human_review_required")

    assert row.get("authority_denied") is True, (
        "_ledger_row rebuilt the row from a fixed key set and dropped the "
        "denial, so every downstream check on it was dead code"
    )


def test_a_denied_source_is_flagged() -> None:
    assert "authority_denied" in _ledger_entry("human_review_required")["quality_flags"]


def test_a_denied_source_cannot_score_strong() -> None:
    assert _ledger_entry("human_review_required")["evidence_strength"] != "strong"


def test_a_denied_source_is_not_reported_verified() -> None:
    """A blocked document must not be presented to drafting as verified."""
    row = _ledger_entry("human_review_required")

    assert row["verification_status"] not in VERIFIED_SOURCE_STATUSES


def test_a_denied_source_carries_no_document_body() -> None:
    assert BODY not in str(_ledger_entry("human_review_required")["snippet"])


# --- no false denial -----------------------------------------------------------


def test_a_clean_source_is_not_denied() -> None:
    row = _ledger_entry("metadata_extracted")

    assert not row.get("authority_denied")
    assert "authority_denied" not in row["quality_flags"]
    assert row["verification_status"] in VERIFIED_SOURCE_STATUSES
    assert BODY in str(row["snippet"])


def test_an_operational_failure_is_not_denied() -> None:
    """Model B: a worker crash must not downgrade evidence."""
    row = _ledger_entry("failed")

    assert not row.get("authority_denied")
    assert row["evidence_strength"] == "strong"


def test_a_quarantined_source_is_denied() -> None:
    builder = ArbitrationContextBuilder(
        _DB(
            documents=[
                {**_document("metadata_extracted"), "duplicate_status": "duplicate"}
            ]
        )
    )
    reference = asyncio.run(
        builder._rehydrate_direct_reference(
            DRAFT, {"source_type": "document", "source_id": "doc-1"}
        )
    )
    row = builder._ledger_row(reference, 1)
    asyncio.run(builder._annotate_source_quality([row], []))

    assert row.get("authority_denied") is True
    assert row["evidence_strength"] != "strong"


# --- the general defect, not just this one field -------------------------------


def test_ledger_row_preserves_every_safety_field_the_hydrator_computes() -> None:
    """The construction boundary is the bug; `authority_denied` was one victim.

    Any field the hydrator computes for SAFETY has to survive the copy. Listing
    them here means adding a new one without wiring it through fails the build
    rather than becoming silent dead code.
    """
    from rbac_backend.services.arbitration_drafting.context import (
        LEDGER_SAFETY_FIELDS,
    )

    builder = ArbitrationContextBuilder(_DB(documents=[_document("human_review_required")]))
    reference = asyncio.run(
        builder._rehydrate_direct_reference(
            DRAFT, {"source_type": "document", "source_id": "doc-1"}
        )
    )
    row = builder._ledger_row(reference, 1)

    for field in LEDGER_SAFETY_FIELDS:
        assert field in row, f"{field} is computed upstream but dropped by _ledger_row"
