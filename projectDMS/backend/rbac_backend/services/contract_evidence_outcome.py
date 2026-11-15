"""Degraded, empty and hard-fail semantics - one interpretation, not three.

A contract evidence answer is explicitly **complete**, explicitly **degraded**,
or explicitly **empty**. It is never silently partial, because the three read
identically at the call site — an empty list — and a consumer that cannot tell
them apart will present an outage as "nothing applies", which in a legal context
is a statement it has no basis for.

The classification, in the order it is decided:

* **Hard fail** — authority could not be resolved, applicability is ambiguous,
  ``Historical`` arrived without a date, or a projection is stale. None of these
  has a safe partial answer. Stale in particular never degrades to the prior
  generation: falling back to superseded content because current content is
  missing is how a correction gets silently un-applied.
* **Valid empty** — zero applicable instruments. A complete, trustworthy answer
  that must never be widened into a broader search.
* **Degraded** — one or more sources were unavailable, *and* the remaining
  candidates are canonically authorised. Marked with the failed sources, so a
  consumer can say what it could not see.
* **Complete** — everything answered.

Degradation is deliberately not available when authority itself is in doubt: a
partial answer is only meaningful if what survives is known to be legitimate.

All three sources share this one interpretation. Previously each degraded to an
empty list on its own terms, which is why the same outage could look like three
different answers depending on which source failed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

__all__ = [
    "EvidenceHardFailure",
    "EvidenceOutcome",
    "EvidenceStatus",
    "HardFailReason",
    "SourceStatus",
    "classify_evidence_outcome",
]


class EvidenceStatus(str, Enum):
    COMPLETE = "complete"
    DEGRADED = "degraded"
    VALID_EMPTY = "valid_empty"


class SourceStatus(str, Enum):
    SUCCESS = "success"
    DEGRADED = "degraded"


class HardFailReason(str, Enum):
    """Conditions with no safe partial answer."""

    AUTHORITY_UNRESOLVED = "authority_unresolved"
    AMBIGUOUS_APPLICABILITY = "ambiguous_applicability"
    MISSING_EVENT_DATE = "missing_event_date"
    STALE_PROJECTION = "stale_projection"


class EvidenceHardFailure(Exception):
    """The request cannot be answered at all.

    Carries its reason so a consumer can distinguish "we could not establish
    authority" from "the projection is stale" - different fixes, and a caller
    that conflated them would retry the one that cannot be retried.
    """

    def __init__(self, reason: HardFailReason, detail: str) -> None:
        super().__init__(f"{reason.value}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class EvidenceOutcome:
    """The answer, and what kind of answer it is."""

    status: EvidenceStatus
    candidates: Tuple[Any, ...] = ()
    degraded_sources: Tuple[str, ...] = ()

    @property
    def is_trustworthy_empty(self) -> bool:
        """True only for a complete empty answer, never for a degraded one."""
        return self.status is EvidenceStatus.VALID_EMPTY

    def require_complete(self) -> None:
        """For callers that may not act on a partial answer."""
        if self.status is EvidenceStatus.DEGRADED:
            raise EvidenceHardFailure(
                HardFailReason.AUTHORITY_UNRESOLVED,
                "this consumer requires a complete answer; sources unavailable: "
                + ", ".join(self.degraded_sources),
            )


def classify_evidence_outcome(
    *,
    candidates: Sequence[Any],
    source_status: Mapping[str, str],
    eligible_document_ids: Optional[Sequence[str]],
    authority_resolved: bool = True,
    ambiguous_applicability: bool = False,
    stale_projection: bool = False,
    missing_event_date: bool = False,
) -> EvidenceOutcome:
    """Turn raw source results into one explicit answer.

    Hard failures are checked first and unconditionally: a degraded answer built
    on unresolved authority would look like partial evidence when it is actually
    no evidence at all.
    """
    if not authority_resolved or eligible_document_ids is None:
        raise EvidenceHardFailure(
            HardFailReason.AUTHORITY_UNRESOLVED,
            "canonical eligibility was not resolved; there is no partial answer "
            "to give and no broader search to fall back to",
        )
    if missing_event_date:
        raise EvidenceHardFailure(
            HardFailReason.MISSING_EVENT_DATE,
            "a historical question needs a proven date; defaulting to today would "
            "answer a different question",
        )
    if ambiguous_applicability:
        raise EvidenceHardFailure(
            HardFailReason.AMBIGUOUS_APPLICABILITY,
            "applicability is ambiguous or conflicting; asserting either reading "
            "would manufacture a legal position",
        )
    if stale_projection:
        raise EvidenceHardFailure(
            HardFailReason.STALE_PROJECTION,
            "the projection is superseded; falling back to the prior generation "
            "would silently un-apply a correction",
        )

    degraded = tuple(
        sorted(
            name
            for name, state in source_status.items()
            if state == SourceStatus.DEGRADED.value
        )
    )
    results = tuple(candidates)

    if degraded:
        # Marked, never silent - including when the surviving sources returned
        # nothing, which is precisely the case that would otherwise read as
        # "nothing applies".
        logger.warning("contract evidence degraded; sources unavailable: %s", ", ".join(degraded))
        return EvidenceOutcome(
            status=EvidenceStatus.DEGRADED, candidates=results, degraded_sources=degraded
        )

    if not results:
        return EvidenceOutcome(status=EvidenceStatus.VALID_EMPTY)

    return EvidenceOutcome(status=EvidenceStatus.COMPLETE, candidates=results)
