"""Release 1.1.1 extraction review: pure helpers (no database).

The real-Mongo behaviour is pinned in
``tests/integration/test_extraction_review_mongo.py``; these cover the two rules
that suite can only exercise indirectly.
"""

from __future__ import annotations

from datetime import datetime

from rbac_backend.services.extraction_review_service import (
    REVIEW_NOT_REQUIRED,
    REVIEW_PUBLICATION_PENDING,
    REVIEW_REQUIRED,
    REVIEW_RESOLVED,
    ExtractionReviewService,
    blocking_pages,
)
from rbac_backend.services.metadata_integrity import metadata_from_document


def test_blocking_pages_is_the_union_of_the_verdict_and_the_head() -> None:
    document = {
        "processing_error": {"pages": [5], "quality_review_pages": [5], "unresolved_pages": [2]},
    }
    head = {"review_pages": [3, 5], "unresolved_pages": []}
    # A page one writer missed still blocks.
    assert blocking_pages(document, head) == [2, 3, 5]


def test_blocking_pages_survive_the_resolution_moving_the_verdict() -> None:
    document = {
        "processing_error": {"message": "publication pending"},
        "extraction_review": {"machine_verdict": {"pages": [5]}},
    }
    assert blocking_pages(document, {}) == [5]


def test_review_state_is_bound_to_the_head_revision() -> None:
    head = {"canonical_revision": 2, "canonical_sha256": "b"}
    resolved_rev1 = {"status": "resolved", "canonical_revision": 1, "canonical_sha256": "a"}
    resolved_rev2 = {"status": "resolved", "canonical_revision": 2, "canonical_sha256": "b"}
    state = ExtractionReviewService.review_state
    assert state({"processing_status": "human_review_required"}, head) == REVIEW_REQUIRED
    assert state({"processing_status": "partially_processed", "extraction_review": resolved_rev2}, head) == (
        REVIEW_PUBLICATION_PENDING
    )
    assert state({"processing_status": "completed", "extraction_review": resolved_rev2}, head) == REVIEW_RESOLVED
    # An approval of revision 1 says nothing about revision 2.
    assert state({"processing_status": "completed", "extraction_review": resolved_rev1}, head) == (
        REVIEW_NOT_REQUIRED
    )
    assert state({"processing_status": "partially_processed", "extraction_review": resolved_rev1}, head) == (
        REVIEW_NOT_REQUIRED
    )


def test_metadata_is_rebuilt_from_the_stored_publication_values() -> None:
    metadata = metadata_from_document(
        {
            "letterNo": "GLM/4299",
            "subject": "Guide wall rework",
            "from": "Gulermak-SAM JV",
            "date": datetime(2022, 12, 24),
            "contractual_clauses": ["8.4.1", "17.1"],
            "reference": ["TYP/123"],
            "extracted_tags": ["not-a-controlled-tag"],
        }
    )
    assert metadata.letter_no == "GLM/4299"
    assert metadata.from_company == "Gulermak-SAM JV"
    assert metadata.date == "24-12-2022"
    assert metadata.contractual_clauses == ["8.4.1", "17.1"]
    assert metadata.references
    assert metadata.tags == []  # the controlled vocabulary still applies


def test_metadata_absent_when_nothing_is_stored() -> None:
    assert metadata_from_document({}) is None
    assert metadata_from_document({"subject": ""}) is None
