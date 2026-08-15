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

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, FrozenSet, List, Mapping, Optional

from ..services.extraction.models import (
    Completeness,
    PageExtractionResult,
    PageStatus,
)


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


#: Page statuses that mean the page still owes work.
_UNRESOLVED_PAGE_STATUSES: FrozenSet[PageStatus] = frozenset(
    {
        PageStatus.OCR_FAILED,
        PageStatus.OCR_DEFERRED,
        PageStatus.OCR_DISABLED,
        PageStatus.OCR_PENDING,
        PageStatus.OCR_EMPTY,
        PageStatus.UNRENDERABLE,
    }
)

#: Only pages actually submitted to OCR consume a retry allowance. A DEFERRED
#: page never ran, so charging it an attempt would burn the budget for work
#: that has not been tried even once.
#:
#: OCR_EMPTY *does* consume one: the OCR ran and produced nothing, and if it
#: were remaining-but-free the job would resume forever without ever exhausting
#: its attempts and reaching human review.
_ATTEMPT_CONSUMING_STATUSES: FrozenSet[PageStatus] = frozenset(
    {PageStatus.OCR_FAILED, PageStatus.OCR_EMPTY}
)


@dataclass(frozen=True)
class ProcessingAttemptOutcome:
    """What one extraction attempt produced, as a durable checkpoint.

    Persisted onto the job so a resumed attempt knows exactly which pages it
    still owes, rather than re-deriving them from text or re-running pages that
    already succeeded.
    """

    state: ProcessingState
    expected_page_numbers: List[int] = field(default_factory=list)
    resolved_page_numbers: List[int] = field(default_factory=list)
    remaining_page_numbers: List[int] = field(default_factory=list)
    page_attempts: Dict[str, int] = field(default_factory=dict)
    attempts_exhausted: bool = False

    def to_checkpoint(self) -> Dict[str, Any]:
        """Render the fields the durable job record stores."""
        return {
            "processing_state": self.state.value,
            "expected_page_numbers": list(self.expected_page_numbers),
            "resolved_page_numbers": list(self.resolved_page_numbers),
            "remaining_page_numbers": list(self.remaining_page_numbers),
            "page_attempts": dict(self.page_attempts),
        }


def build_attempt_outcome(
    result: Optional[PageExtractionResult],
    *,
    prior_page_attempts: Mapping[str, int],
    attempts_exhausted: bool,
) -> ProcessingAttemptOutcome:
    """Derive the durable checkpoint for one extraction attempt.

    A missing result is a failure, never a completion - the absence of
    evidence must not read as evidence of success.
    """
    if result is None:
        return ProcessingAttemptOutcome(state=ProcessingState.FAILED)

    expected = sorted(page.number for page in result.pages)
    remaining = sorted(
        page.number
        for page in result.pages
        if page.status in _UNRESOLVED_PAGE_STATUSES
    )
    resolved = [number for number in expected if number not in set(remaining)]

    page_attempts = {str(key): int(value) for key, value in prior_page_attempts.items()}
    for page in result.pages:
        if page.status in _ATTEMPT_CONSUMING_STATUSES:
            key = str(page.number)
            page_attempts[key] = page_attempts.get(key, 0) + 1

    return ProcessingAttemptOutcome(
        state=derive_processing_state(result, attempts_exhausted=attempts_exhausted),
        expected_page_numbers=expected,
        resolved_page_numbers=resolved,
        remaining_page_numbers=remaining,
        page_attempts=page_attempts,
        attempts_exhausted=attempts_exhausted,
    )
