"""Grounded letter summary (Release 1.2): the stored shape.

Two products live here, both derived views of one canonical evidence revision:

* ``short_summary`` - one or two sentences for cards and lists;
* ``detailed_summary`` - sectioned items, each citing the facts it was built
  from, each fact carrying the canonical page and span it was read from.

Neither is evidence. The canonical text (``canonical_evidence_service``)
stays the factual authority; a summary is valid only for the exact
``canonical_revision`` + ``canonical_sha256`` named in ``generated_from`` and is
stale the moment the head moves. See
``docs/architecture/grounded_letter_summary_release_1_2.md``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1"

#: Pinned. Changing the presentation prompt or the deterministic renderer's
#: wording rules requires bumping this; tests pin the value.
GROUNDED_SUMMARY_PROMPT_VERSION = "grounded_letter_summary.v1"

VerificationStatus = Literal[
    "verified",
    "source_stated",
    "human_approved",
    "uncertain",
    "not_checkable",
]

ItemRole = Literal[
    "source_fact",
    "party_position",
    "request",
    "instruction",
    "reservation",
    "claim",
    "reference",
    "uncertainty",
]

#: ``documents.summary_state.status`` values.
SUMMARY_NOT_GENERATED = "not_generated"
SUMMARY_GENERATING = "generating"
SUMMARY_CURRENT = "current"
SUMMARY_STALE = "stale"
SUMMARY_DEGRADED = "degraded"
SUMMARY_BLOCKED_FOR_REVIEW = "blocked_for_review"
SUMMARY_WITHHELD = "withheld"
SUMMARY_UNAVAILABLE = "unavailable"
SUMMARY_FAILED = "failed"

SUMMARY_STATUSES = frozenset(
    {
        SUMMARY_NOT_GENERATED,
        SUMMARY_GENERATING,
        SUMMARY_CURRENT,
        SUMMARY_STALE,
        SUMMARY_DEGRADED,
        SUMMARY_BLOCKED_FOR_REVIEW,
        SUMMARY_WITHHELD,
        SUMMARY_UNAVAILABLE,
        SUMMARY_FAILED,
    }
)

#: The statuses under which a stored summary may be shown as describing the
#: current evidence.
DISPLAYABLE_STATUSES = frozenset({SUMMARY_CURRENT, SUMMARY_DEGRADED})

#: Section keys, in display order.
DETAILED_SECTIONS: tuple[str, ...] = (
    "background",
    "material_events",
    "issues_positions",
    "contractual_basis",
    "requests_relief",
    "claims_reservations",
    "dates_periods",
    "amounts_quantities",
    "references",
    "uncertainties",
)

SECTION_TITLES: Dict[str, str] = {
    "background": "Background",
    "material_events": "Material Events / Sequence",
    "issues_positions": "Issues / Allegations / Positions Stated",
    "contractual_basis": "Contractual Basis Cited",
    "requests_relief": "Requests / Instructions / Relief Sought",
    "claims_reservations": "Claims / Reservations / Entitlements Asserted",
    "dates_periods": "Material Dates / Periods",
    "amounts_quantities": "Amounts / Quantities",
    "references": "Referenced Correspondence / Documents",
    "uncertainties": "Uncertainty / Human Review / Qualification",
}


class SourceSpan(BaseModel):
    """Character offsets into the canonical text of ``generated_from``."""

    page_number: int
    start_char: int
    end_char: int


class GroundedFact(BaseModel):
    id: str
    type: str
    label: str
    value: Any = None
    unit: Optional[str] = None
    normalized_value: Any = None
    page_numbers: List[int] = Field(default_factory=list)
    source_spans: List[SourceSpan] = Field(default_factory=list)
    evidence_checks: List[Dict[str, Any]] = Field(default_factory=list)
    verification_status: VerificationStatus
    #: Where the value came from: ``canonical_text`` (read from the page
    #: text), ``structured_metadata`` (an extracted field, located or not),
    #: ``human_edit``, ``reference_sync`` or ``page_quality``.
    origin: str
    #: Who states it, for statements made in the letter.
    party: Optional[str] = None
    role: Optional[ItemRole] = None
    #: The sentence that states it, verbatim, at most 400 characters. Never
    #: whole page text.
    quote: Optional[str] = None
    #: For references: the DMS document the reference resolved to, only when
    #: ReferenceSync actually linked one.
    resolved_document_id: Optional[str] = None


class GroundedSummaryItem(BaseModel):
    text: str
    role: ItemRole = "source_fact"
    party: Optional[str] = None
    fact_ids: List[str] = Field(default_factory=list)
    page_numbers: List[int] = Field(default_factory=list)
    verification_status: VerificationStatus
    source_spans: List[SourceSpan] = Field(default_factory=list)


class GeneratedFrom(BaseModel):
    canonical_revision: int
    canonical_sha256: str
    extraction_run_id: Optional[str] = None
    #: Present when this summary was built after a human review resolved
    #: the machine verdict for this exact revision.
    review_resolution: Optional[Dict[str, Any]] = None


class GeneratorInfo(BaseModel):
    mode: Literal["deterministic", "llm_assisted"]
    model: Optional[str] = None
    prompt_version: str = GROUNDED_SUMMARY_PROMPT_VERSION
    generated_at: datetime
    degraded: bool = False
    #: Why model output was not used, when it was asked for and rejected.
    llm_rejection: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None


class DetailedSummary(BaseModel):
    schema_version: str = SCHEMA_VERSION
    executive_overview: Optional[str] = None
    background: List[GroundedSummaryItem] = Field(default_factory=list)
    material_events: List[GroundedSummaryItem] = Field(default_factory=list)
    issues_positions: List[GroundedSummaryItem] = Field(default_factory=list)
    contractual_basis: List[GroundedSummaryItem] = Field(default_factory=list)
    requests_relief: List[GroundedSummaryItem] = Field(default_factory=list)
    claims_reservations: List[GroundedSummaryItem] = Field(default_factory=list)
    dates_periods: List[GroundedSummaryItem] = Field(default_factory=list)
    amounts_quantities: List[GroundedSummaryItem] = Field(default_factory=list)
    references: List[GroundedSummaryItem] = Field(default_factory=list)
    uncertainties: List[GroundedSummaryItem] = Field(default_factory=list)
    facts: List[GroundedFact] = Field(default_factory=list)
    markdown: str = ""
    generated_from: GeneratedFrom
    generator: GeneratorInfo

    model_config = ConfigDict(extra="forbid")


__all__ = [
    "DETAILED_SECTIONS",
    "DISPLAYABLE_STATUSES",
    "DetailedSummary",
    "GROUNDED_SUMMARY_PROMPT_VERSION",
    "GeneratedFrom",
    "GeneratorInfo",
    "GroundedFact",
    "GroundedSummaryItem",
    "SCHEMA_VERSION",
    "SECTION_TITLES",
    "SUMMARY_BLOCKED_FOR_REVIEW",
    "SUMMARY_CURRENT",
    "SUMMARY_DEGRADED",
    "SUMMARY_FAILED",
    "SUMMARY_GENERATING",
    "SUMMARY_NOT_GENERATED",
    "SUMMARY_STALE",
    "SUMMARY_STATUSES",
    "SUMMARY_UNAVAILABLE",
    "SUMMARY_WITHHELD",
    "SourceSpan",
]
