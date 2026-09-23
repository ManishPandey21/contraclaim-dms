"""Domain models for page-level extraction.

Every enum value here is persisted to Mongo. Changing a value is a data
migration, not a rename. The five contract page statuses
(text_layer, ocr_completed, ocr_empty, ocr_failed, ocr_disabled) are carried
over verbatim so the contract path's existing records stay valid.

Quality fields on ExtractedPage are plain strings and dicts rather than the
Phase 6 verdict types, so Phase 1 does not import Phase 6 and no cycle forms.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class SourceKind(str, Enum):
    PDF = "pdf"
    IMAGE = "image"
    TEXT = "text"
    ARCHIVE = "archive"
    UNSUPPORTED = "unsupported"


class PageSource(str, Enum):
    """Where a page's final text came from."""

    TEXT_LAYER = "text_layer"
    OCR = "ocr"
    RECONSTRUCTED = "reconstructed"
    EMPTY = "empty"


class PageStatus(str, Enum):
    TEXT_LAYER = "text_layer"
    OCR_COMPLETED = "ocr_completed"
    OCR_EMPTY = "ocr_empty"
    OCR_FAILED = "ocr_failed"
    OCR_DISABLED = "ocr_disabled"
    OCR_PENDING = "ocr_pending"
    OCR_DEFERRED = "ocr_deferred"
    UNRENDERABLE = "unrenderable"


class PageClass(str, Enum):
    TEXT_NATIVE = "text_native"
    SCANNED_IMAGE = "scanned_image"
    MIXED_CONTENT = "mixed_content"
    TABLE_HEAVY = "table_heavy"
    BLANK = "blank"
    UNRENDERABLE = "unrenderable"


class Completeness(str, Enum):
    COMPLETE = "complete"
    PARTIAL = "partial"


@dataclass(frozen=True)
class PageClassification:
    page_class: PageClass
    char_count: int
    image_count: int
    image_coverage: float
    table_count: int
    width: float
    height: float
    rotation: int

    @property
    def is_landscape(self) -> bool:
        return self.width > self.height


@dataclass
class ExtractedPage:
    number: int
    text: str
    source: PageSource
    status: PageStatus
    classification: PageClassification
    tables: List[List[List[str]]] = field(default_factory=list)
    batch_id: Optional[str] = None
    error: Optional[str] = None
    quality_verdict: Optional[str] = None
    quality_checks: List[Dict[str, Any]] = field(default_factory=list)
    needs_review: bool = False
    #: What extraction actually read, when that differs from the published
    #: `text`: set the first time a deterministic repair rewrites `text`, or by
    #: the engine when the text was unusable ``(cid:N)`` placeholders and was
    #: withheld (`text` is then empty - see text_quality.withhold_unusable).
    #: `text` is the published representation, `raw_text` is evidence and is
    #: never indexed. Never overwritten once set.
    raw_text: Optional[str] = None
    #: Applied repairs, as provenance records. Distinct from the gate's
    #: proposals: these are the ones that were re-verified and adopted.
    applied_repairs: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        return len((self.text or "").strip())


@dataclass(frozen=True)
class PageExtractionPolicy:
    ocr_enabled: bool
    min_text_chars_per_page: int
    batch_size: int
    max_ocr_pages_per_attempt: int
    ocr_language: str


@dataclass
class PageExtractionResult:
    pages: List[ExtractedPage]
    combined_text: str
    ocr_pages_total: int
    ocr_failed_pages: List[int] = field(default_factory=list)
    ocr_deferred_pages: List[int] = field(default_factory=list)
    unrenderable_pages: List[int] = field(default_factory=list)
    #: Pages whose text was unusable (cid:N) placeholders and was withheld
    #: from `text`/`combined_text` (kept as `raw_text` evidence). Consumers use
    #: it to avoid re-reading the same unusable text layer by another route.
    withheld_pages: List[int] = field(default_factory=list)
    completeness: Completeness = Completeness.COMPLETE
    engine_version: str = "1"
