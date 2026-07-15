from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class LegalWordStatus(str, Enum):
    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    REJECTED = "rejected"
    INACTIVE = "inactive"


class LegalWordSource(str, Enum):
    SYSTEM = "system"
    ADMIN_CREATED = "admin_created"
    USER_REQUESTED = "user_requested"
    AI_SUGGESTED = "ai_suggested"


class LegalWordSuggestionEligibility(str, Enum):
    NEW_WORD = "new_word"
    BLOCKED_RECENTLY_PUBLISHED = "blocked_recently_published"
    ELIGIBLE_FOR_REPUBLISH = "eligible_for_republish"


class LegalWordPublicationType(str, Enum):
    NEW = "new"
    REPEATED = "repeated"
    PREVIOUSLY_PUBLISHED = "previously_published"
    UNPUBLISHED = "unpublished"
    ELIGIBLE_FOR_REPUBLICATION = "eligible_for_republication"


class LegalWordBase(BaseModel):
    word: str = Field(..., min_length=1, max_length=100)
    category: Optional[str] = Field(default=None, max_length=100)
    meaning: Optional[str] = Field(default=None, max_length=2000)
    synonyms: List[str] = Field(default_factory=list, max_length=25)
    example_sentence: Optional[str] = Field(default=None, max_length=3000)

    @field_validator("word")
    @classmethod
    def _strip_word(cls, value: str) -> str:
        value = " ".join(value.strip().split())
        if not value:
            raise ValueError("word must not be empty")
        return value

    @field_validator("category", "meaning", "example_sentence")
    @classmethod
    def _strip_optional_text(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return value
        value = " ".join(value.strip().split())
        return value or None

    @field_validator("synonyms")
    @classmethod
    def _normalize_synonyms(cls, values: List[str]) -> List[str]:
        seen: set[str] = set()
        normalized: List[str] = []
        for value in values or []:
            text = " ".join(str(value).strip().split())
            if not text:
                continue
            key = text.casefold()
            if key in seen:
                continue
            seen.add(key)
            normalized.append(text)
        return normalized


class LegalWord(LegalWordBase):
    model_config = ConfigDict(
        populate_by_name=True,
        json_encoders={
            date: lambda value: value.isoformat(),
            datetime: lambda value: value.isoformat(),
        },
    )

    id: Optional[str] = Field(alias="_id", default=None)
    normalized_word: str
    source: LegalWordSource = LegalWordSource.SYSTEM
    requested_by_user_id: Optional[str] = None
    requested_at: Optional[datetime] = None
    approved_by_admin_id: Optional[str] = None
    approved_at: Optional[datetime] = None
    first_suggested_date: Optional[date] = None
    last_published_date: Optional[date] = None
    scheduled_date: Optional[date] = None
    published_date: Optional[date] = None
    published_count: int = 0
    publication_history: List[date] = Field(default_factory=list)
    is_eligible_for_republish: bool = True
    status: LegalWordStatus = LegalWordStatus.PENDING_REVIEW
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    updated_by: Optional[str] = None


class LegalWordCreate(LegalWordBase):
    meaning: str = Field(..., min_length=1, max_length=2000)
    example_sentence: str = Field(..., min_length=1, max_length=3000)
    source: LegalWordSource = LegalWordSource.ADMIN_CREATED
    status: LegalWordStatus = LegalWordStatus.PENDING_REVIEW
    first_suggested_date: Optional[date] = None
    last_published_date: Optional[date] = None
    scheduled_date: Optional[date] = None
    published_date: Optional[date] = None
    published_count: int = 0
    publication_history: List[date] = Field(default_factory=list)
    is_eligible_for_republish: bool = True

    @model_validator(mode="after")
    def _require_complete_word(self) -> "LegalWordCreate":
        if not self.meaning or not self.example_sentence:
            raise ValueError("meaning and example_sentence are required")
        return self


class LegalWordUpdate(BaseModel):
    word: Optional[str] = Field(default=None, min_length=1, max_length=100)
    category: Optional[str] = Field(default=None, max_length=100)
    meaning: Optional[str] = Field(default=None, max_length=2000)
    synonyms: Optional[List[str]] = Field(default=None, max_length=25)
    example_sentence: Optional[str] = Field(default=None, max_length=3000)
    source: Optional[LegalWordSource] = None
    status: Optional[LegalWordStatus] = None
    first_suggested_date: Optional[date] = None
    last_published_date: Optional[date] = None
    scheduled_date: Optional[date] = None
    published_date: Optional[date] = None
    published_count: Optional[int] = Field(default=None, ge=0)
    publication_history: Optional[List[date]] = None
    is_eligible_for_republish: Optional[bool] = None

    @field_validator("word")
    @classmethod
    def _strip_optional_word(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return LegalWordBase._strip_word(value)

    _strip_optional_text = field_validator("category", "meaning", "example_sentence")(
        LegalWordBase._strip_optional_text.__func__
    )
    _normalize_synonyms = field_validator("synonyms")(
        LegalWordBase._normalize_synonyms.__func__
    )


class LegalWordListResponse(BaseModel):
    words: List[LegalWord]
    total: int
    page: int
    limit: int
    has_next: bool = False
    has_prev: bool = False

    @model_validator(mode="after")
    def _compute_pagination(self) -> "LegalWordListResponse":
        self.has_next = (self.page * self.limit) < self.total
        self.has_prev = self.page > 1
        return self


class LegalWordPublishedItem(LegalWord):
    display_publication_date: date
    publication_type: LegalWordPublicationType
    publication_badge: Optional[str] = None


class LegalWordPublishedListResponse(BaseModel):
    words: List[LegalWordPublishedItem]
    total: int
    page: int
    limit: int
    has_next: bool = False
    has_prev: bool = False

    @model_validator(mode="after")
    def _compute_pagination(self) -> "LegalWordPublishedListResponse":
        self.has_next = (self.page * self.limit) < self.total
        self.has_prev = self.page > 1
        return self


class LegalWordSearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=100)

    @field_validator("query")
    @classmethod
    def _strip_query(cls, value: str) -> str:
        value = " ".join(value.strip().split())
        if not value:
            raise ValueError("query must not be empty")
        return value


class LegalWordSearchResponse(BaseModel):
    found: bool
    requested: bool = False
    message: Optional[str] = None
    word: Optional[LegalWord] = None


class LegalWordScheduleRequest(BaseModel):
    scheduled_date: date


class LegalWordPublishRequest(BaseModel):
    published_date: Optional[date] = None


class LegalWordDailySet(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        json_encoders={
            date: lambda value: value.isoformat(),
            datetime: lambda value: value.isoformat(),
        },
    )

    id: Optional[str] = Field(alias="_id", default=None)
    published_date: date
    word_ids: List[str] = Field(default_factory=list, min_length=0, max_length=6)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    updated_by: Optional[str] = None


class TodayLegalWordsResponse(BaseModel):
    published_date: date
    words: List[LegalWord]
    count: int
    message: Optional[str] = None


class LegalWordAISuggestion(BaseModel):
    word: str
    meaning: str
    synonyms: List[str] = Field(default_factory=list)
    example_sentence: str
    existing_word_id: Optional[str] = None
    word_record: Optional[LegalWord] = None
    previous_publication_status: str
    last_published_date: Optional[date] = None
    eligibility_status: LegalWordSuggestionEligibility
    eligibility_label: str
    is_eligible_for_republish: bool
    blocked_reason: Optional[str] = None


class LegalWordAISuggestionResponse(BaseModel):
    suggestions: List[LegalWordAISuggestion]
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    count: int = 0

    @model_validator(mode="after")
    def _compute_count(self) -> "LegalWordAISuggestionResponse":
        self.count = len(self.suggestions)
        return self


__all__ = [
    "LegalWord",
    "LegalWordAISuggestion",
    "LegalWordAISuggestionResponse",
    "LegalWordCreate",
    "LegalWordDailySet",
    "LegalWordListResponse",
    "LegalWordPublishRequest",
    "LegalWordPublicationType",
    "LegalWordPublishedItem",
    "LegalWordPublishedListResponse",
    "LegalWordScheduleRequest",
    "LegalWordSearchRequest",
    "LegalWordSearchResponse",
    "LegalWordSource",
    "LegalWordSuggestionEligibility",
    "LegalWordStatus",
    "LegalWordUpdate",
    "TodayLegalWordsResponse",
]
