"""Document processing states that cannot report unfinished work as done.

The house failure mode this exists to prevent: a document marked `completed`
while pages were silently skipped. Every state below is derived from what the
extraction engine actually produced, and `COMPLETED` is reachable only when
nothing was deferred, failed, or unrenderable.

These values are persisted to `documents.processing_status`. The four that
already exist in production records - queued, processing, completed, failed -
are preserved verbatim; changing any value is a data migration.
"""

from __future__ import annotations

from enum import Enum
from typing import FrozenSet

from ..services.extraction.models import Completeness, PageExtractionResult


class ProcessingState(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    #: Stored deliberately without extraction (archives). Not a failure, and
    #: explicitly not `completed` - nothing was extracted to complete.
    STORED_ONLY = "stored_only"
    #: Some pages extracted, others deferred or failed, retries still available.
    PARTIALLY_PROCESSED = "partially_processed"
    #: Automated recovery is exhausted or impossible; a person must look.
    HUMAN_REVIEW_REQUIRED = "human_review_required"
    FAILED = "failed"


#: States from which no further automated work will happen.
TERMINAL_STATES: FrozenSet[ProcessingState] = frozenset(
    {
        ProcessingState.COMPLETED,
        ProcessingState.STORED_ONLY,
        ProcessingState.HUMAN_REVIEW_REQUIRED,
        ProcessingState.FAILED,
    }
)

#: States that may be reported to a user as "this document is fully extracted".
#: Deliberately narrow: only COMPLETED qualifies.
SUCCESS_STATES: FrozenSet[ProcessingState] = frozenset({ProcessingState.COMPLETED})

#: States the job loop should pick up again.
RESUMABLE_STATES: FrozenSet[ProcessingState] = frozenset(
    {ProcessingState.PARTIALLY_PROCESSED}
)


def derive_processing_state(
    result: PageExtractionResult, *, attempts_exhausted: bool
) -> ProcessingState:
    """Derive the document state from what extraction actually produced.

    Ordering matters:

    1. An unrenderable page needs a person regardless of retries left - another
       attempt cannot make an unparseable page parse.
    2. Anything else unresolved is resumable while attempts remain, and needs a
       person once they are gone.
    3. `COMPLETED` requires COMPLETE completeness *and* empty problem lists, so
       a bug in either one cannot produce a false success on its own.
    """
    if result.unrenderable_pages:
        return ProcessingState.HUMAN_REVIEW_REQUIRED

    unresolved = bool(
        result.ocr_failed_pages
        or result.ocr_deferred_pages
        or result.completeness is Completeness.PARTIAL
    )
    if unresolved:
        return (
            ProcessingState.HUMAN_REVIEW_REQUIRED
            if attempts_exhausted
            else ProcessingState.PARTIALLY_PROCESSED
        )

    return ProcessingState.COMPLETED
