"""The notice period a project's contract sets for a type of claim.

A requirement is always tied to a clause of this project's contract, with the
wording it was read from. It starts ``extracted_unverified`` and becomes
``verified`` only when a second person confirms the period against that wording
(D-08, plan 4B-14): the recorder cannot verify their own entry.

``origin`` is the server's finding, never the client's: ``clause_index`` when the
clause resolved in this project's clause index and the quote is in its text;
``entered_from_contract`` otherwise. ``entered`` is the value written before 4B-14,
kept readable.
Records are never edited in place: a change supersedes the current record and
writes a new one, so what a past assessment relied on stays readable.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..utils.datetime import now_utc
from .claim import ClaimType

NoticeTrigger = Literal["awareness", "event"]
NoticeVerification = Literal["extracted_unverified", "verified"]
NoticeRequirementStatus = Literal["current", "superseded"]
NoticeRequirementOrigin = Literal["clause_index", "entered_from_contract", "entered"]
CLAIM_TYPES = tuple(item.value for item in ClaimType)


def _claim_type(value: Any) -> str:
    text = str(getattr(value, "value", value) or "").strip()
    if text not in CLAIM_TYPES:
        raise ValueError(f"claim_type must be one of: {', '.join(CLAIM_TYPES)}")
    return text


class NoticeRequirement(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    organization_id: str
    project_id: str
    claim_type: str
    period_days: int = Field(..., ge=1, le=365)
    trigger: NoticeTrigger = "awareness"
    clause_no: str = Field(..., min_length=1, max_length=40)
    clause_title: Optional[str] = Field(default=None, max_length=300)
    # The contract wording the period was read from, as it appears in the clause.
    quote: str = Field(..., min_length=10, max_length=2000)
    document_id: Optional[str] = None
    document_type: Optional[str] = None
    clause_uid: Optional[str] = None
    # clause_index: picked from a candidate found in the project's clause index.
    # entered: typed by a person from the contract in front of them.
    origin: NoticeRequirementOrigin = "entered"
    verification: NoticeVerification = "extracted_unverified"
    status: NoticeRequirementStatus = "current"
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=now_utc)
    verified_by: Optional[str] = None
    verified_at: Optional[datetime] = None
    superseded_at: Optional[datetime] = None
    superseded_by_id: Optional[str] = None

    @field_validator("id", mode="before")
    @classmethod
    def _stringify_id(cls, value: Any) -> str:
        return str(value) if value is not None else str(uuid.uuid4())


class NoticeRequirementCreate(BaseModel):
    project_id: str = Field(..., min_length=1)
    organization_id: Optional[str] = None
    claim_type: str = Field(..., min_length=1, max_length=40)
    period_days: int = Field(..., ge=1, le=365)
    trigger: NoticeTrigger = "awareness"
    clause_no: str = Field(..., min_length=1, max_length=40)
    clause_title: Optional[str] = Field(default=None, max_length=300)
    quote: str = Field(..., min_length=10, max_length=2000)
    document_id: Optional[str] = None
    document_type: Optional[str] = None
    clause_uid: Optional[str] = None
    # Ignored: the server decides the origin (D-08). Accepted so older clients still post.
    origin: Optional[str] = None

    @field_validator("claim_type", mode="before")
    @classmethod
    def _known_claim_type(cls, value: Any) -> str:
        return _claim_type(value)


class NoticePeriodCandidate(BaseModel):
    """A notice period found in the wording of an indexed contract clause. A proposal only."""

    period_days: int
    trigger: NoticeTrigger
    clause_no: str
    clause_title: Optional[str] = None
    quote: str
    document_id: Optional[str] = None
    document_type: Optional[str] = None
    clause_uid: Optional[str] = None
    # True for a document that modifies the general conditions (particular or
    # special conditions, an amendment): it takes precedence where both speak.
    modifies_general_conditions: bool = False
    cited_by_claim: bool = False


class NoticeCandidatesResponse(BaseModel):
    project_id: str
    claim_type: str
    clause_index_available: bool
    candidates: List[NoticePeriodCandidate] = Field(default_factory=list)
    current: Optional[NoticeRequirement] = None


__all__ = [
    "NoticeCandidatesResponse",
    "NoticePeriodCandidate",
    "NoticeRequirement",
    "NoticeRequirementCreate",
    "NoticeTrigger",
    "NoticeVerification",
]
