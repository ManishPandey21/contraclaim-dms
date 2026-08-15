"""Structured metadata extracted from OCR/LLM pipelines."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator


ReferenceValue = Union[str, Dict[str, Any]]

NULLISH_VALUES = {"", "null", "'null'", '"null"', "not found", "none", "n/a", "na", "not applicable", "-", "--"}

EXTRACTED_TAG_OPTIONS: tuple[str, ...] = (
    "Design & Drawings",
    "Safety",
    "Quality",
    "Variations",
    "Contractual",
    "Payment",
    "Schedule",
    "Hindrances",
    "EOT",
    "Delay",
    "Counterclaim",
    "Responsibility",
    "Notice Compliance",
    "Status",
    "Priority",
)

EXTRACTED_SUBTAG_OPTIONS: tuple[str, ...] = (
    "Unforeseen Delay",
    "Employer's Delay",
    "Contractor's Delay",
    "GFC",
    "CRD",
    "Design Approvals",
    "As-Built Drawings",
    "Shop Drawings",
    "Safety Training",
    "Inspections",
    "Safety Incidents",
    "Penalty",
    "Vendor/Source Approval",
    "Material Testing",
    "Quality Assurance",
    "Quality",
    "NCN",
    "Additional Work",
    "Extra Items",
    "Quantity Variations",
    "Release of Performance Bank Guarantee",
    "Taking Over Certificate",
    "Price Variation/Escalation",
    "Excise Duty",
    "Advance",
    "Retention",
    "GST Reimbursement",
    "Resources Planning",
    "Key Dates",
    "Schedule Updates",
    "Baseline Schedule",
    "DWP",
    "Hindrances",
    "Force Majeure",
    "Local Restrictions",
    "Adverse Weather",
    "Site Access",
    "Restrictions",
    "Utility Relocation",
    "Tree Cutting",
    "Land Acquisition",
    "EOT Programme",
)


def enforce_controlled_vocabulary(values: Any, options: tuple[str, ...]) -> list[str]:
    """Keep only values from the controlled vocabulary, restoring canonical casing.

    Matching is case-insensitive and whitespace-tolerant; anything the model
    (or an injected document) invents outside the allowlist is dropped.
    """
    canonical = {option.lower(): option for option in options}
    kept: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        match = canonical.get(str(value).strip().lower())
        if match and match not in seen:
            kept.append(match)
            seen.add(match)
    return kept


def _clean_scalar(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if text.lower() in NULLISH_VALUES:
        return None
    return text


def _clean_list(values: Any) -> List[Any]:
    if values in (None, "", [], {}):
        return []
    raw_items = values if isinstance(values, list) else [values]
    cleaned: List[Any] = []
    for item in raw_items:
        if isinstance(item, dict):
            row = {key: value for key, value in item.items() if _clean_scalar(value) is not None}
            if row:
                cleaned.append(row)
            continue
        value = _clean_scalar(item)
        if value is not None:
            cleaned.append(value)
    return cleaned


class ParsedDocumentMetadata(BaseModel):
    date: Optional[str] = None
    subject: Optional[str] = None
    letter_no: Optional[str] = None
    from_company: Optional[str] = None
    to_company: Optional[str] = None
    references: List[ReferenceValue] = Field(default_factory=list)
    asset_type: Optional[str] = None
    location: Optional[str] = None
    specific_area: Optional[str] = None
    chainage_from: Optional[str] = None
    chainage_to: Optional[str] = None
    work_type: Optional[str] = None
    issue_nature: Optional[str] = None
    claim_category: Optional[str] = None
    alleged_responsibility: Optional[str] = None
    priority: Optional[str] = None
    summary: Optional[str] = None
    keywords: List[str] = Field(default_factory=list)
    linked_event_suggested: Optional[str] = None
    reference_chain: Optional[str] = None
    additional_keywords: List[str] = Field(default_factory=list)
    contractual_clauses: List[str] = Field(default_factory=list)
    key_reply_points: List[str] = Field(default_factory=list)
    full_content: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    sub_tags: List[str] = Field(default_factory=list, alias="subTags")

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="before")
    @classmethod
    def _normalize_extracted_aliases(cls, values: Any) -> Any:
        if not isinstance(values, dict):
            return values
        normalized = dict(values)
        if "tags" not in normalized and "extracted_tags" in normalized:
            normalized["tags"] = normalized.get("extracted_tags")
        if (
            "subTags" not in normalized
            and "sub_tags" not in normalized
        ):
            if "extracted_subTags" in normalized:
                normalized["subTags"] = normalized.get("extracted_subTags")
            elif "extracted_sub_tags" in normalized:
                normalized["subTags"] = normalized.get("extracted_sub_tags")
        return normalized

    @model_validator(mode="after")
    def _normalize_nullish_values(self):
        scalar_fields = (
            "date",
            "subject",
            "letter_no",
            "from_company",
            "to_company",
            "asset_type",
            "location",
            "specific_area",
            "chainage_from",
            "chainage_to",
            "work_type",
            "issue_nature",
            "claim_category",
            "alleged_responsibility",
            "priority",
            "summary",
            "linked_event_suggested",
            "reference_chain",
            "full_content",
        )
        for field in scalar_fields:
            setattr(self, field, _clean_scalar(getattr(self, field, None)))

        for field in (
            "references",
            "keywords",
            "additional_keywords",
            "contractual_clauses",
            "key_reply_points",
            "tags",
            "sub_tags",
        ):
            setattr(self, field, _clean_list(getattr(self, field, None)))

        # Controlled vocabularies are enforced server-side, not just requested
        # in the prompt: extraction output is derived from untrusted document
        # text, so a document that steers the model ("tag this as X") can at
        # most pick from the approved list — never invent a tag that then
        # drives search filters and register views.
        self.tags = enforce_controlled_vocabulary(self.tags, EXTRACTED_TAG_OPTIONS)
        self.sub_tags = enforce_controlled_vocabulary(self.sub_tags, EXTRACTED_SUBTAG_OPTIONS)
        return self


class ProcessingResult(BaseModel):
    """Result of document processing"""
    success: bool
    document_id: Optional[str] = None
    processed_path: Optional[str] = None
    metadata: Optional[ParsedDocumentMetadata] = None
    chunks_created: int = 0
    error: Optional[str] = None
    processing_time: float = 0.0
    metadata_source: str = "legacy_regex"
    metadata_debug: Optional[Dict[str, Any]] = None
    partial_failures: Dict[str, Any] = Field(default_factory=dict)
    #: Typed page-extraction result. Carried so the durable job layer can
    #: derive the document state and requeue deferred pages without
    #: re-deriving them from text. Not a Pydantic model - allow arbitrary types.
    extraction_result: Optional[Any] = None
    extraction_completeness: Optional[str] = None
    #: Which extractor handled this upload (pdf/image/text/archive).
    source_kind: Optional[str] = None
    #: Set when the processor reached an explicit terminal state of its own,
    #: such as an archive stored without extraction.
    processing_state: Optional[str] = None
    #: Pages the quality gate and fallback ladder could not resolve. A
    #: non-empty list means this document cannot be reported as completed.
    pages_human_review: List[int] = Field(default_factory=list)

    model_config = ConfigDict(arbitrary_types_allowed=True)


__all__ = [
    "ParsedDocumentMetadata",
    "ProcessingResult",
    "ReferenceValue",
    "EXTRACTED_TAG_OPTIONS",
    "EXTRACTED_SUBTAG_OPTIONS",
    "enforce_controlled_vocabulary",
    "EXTRACTED_METADATA_FIELDS",
    "parsed_metadata_snapshot",
    "extracted_metadata_updates",
]


EXTRACTED_METADATA_FIELDS: tuple[str, ...] = (
    "asset_type",
    "location",
    "specific_area",
    "chainage_from",
    "chainage_to",
    "work_type",
    "issue_nature",
    "claim_category",
    "alleged_responsibility",
    "priority",
    "linked_event_suggested",
    "reference_chain",
    "additional_keywords",
    "tags",
    "sub_tags",
)


def parsed_metadata_snapshot(metadata: Any) -> Dict[str, Any]:
    """Return a Mongo-safe snapshot of all extracted metadata fields."""
    if not metadata:
        return {}
    if hasattr(metadata, "model_dump"):
        return metadata.model_dump(by_alias=True, exclude_none=True)  # type: ignore[attr-defined]
    if isinstance(metadata, dict):
        return {key: value for key, value in metadata.items() if value not in (None, "", [], {})}
    return {}


def extracted_metadata_updates(metadata: Any) -> Dict[str, Any]:
    """Top-level document fields derived from extended AI metadata.

    User-managed document ``tags`` and ``subTags`` are not overwritten. Extracted
    classification suggestions are stored under explicit extracted_* fields.
    """
    snapshot = parsed_metadata_snapshot(metadata)
    updates: Dict[str, Any] = {}
    if snapshot:
        updates["metadata"] = snapshot

    for field in EXTRACTED_METADATA_FIELDS:
        if isinstance(metadata, dict):
            if field == "tags":
                value = metadata.get("extracted_tags") or metadata.get("tags")
            elif field == "sub_tags":
                value = (
                    metadata.get("extracted_subTags")
                    or metadata.get("extracted_sub_tags")
                    or metadata.get("subTags")
                    or metadata.get("sub_tags")
                )
            else:
                value = metadata.get(field)
        else:
            value = getattr(metadata, field, None) if metadata is not None else None
        if value in (None, "", [], {}):
            continue
        if field == "tags":
            updates["extracted_tags"] = value
        elif field == "sub_tags":
            updates["extracted_subTags"] = value
        else:
            updates[field] = value
    return updates
