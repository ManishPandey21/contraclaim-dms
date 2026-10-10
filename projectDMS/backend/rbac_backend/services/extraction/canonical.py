"""The one authoritative assembly of a document's complete canonical text.

Chunks and embeddings are retrieval aids; they never replace the document. The
canonical document is assembled here, deterministically, from page evidence
alone - no vector store, no embedding provider, no LLM - and every consumer
that needs a document's full text (persistence, the evidence read path, a
future Matter Context Pack or chronology extractor) gets the same bytes.

Format, pinned by tests and by the persisted checksum:

* pages in ascending PDF page number, each exactly once;
* each page contributes its published (canonical) ``text`` verbatim - nothing
  stripped, nothing truncated;
* pages are joined by ``PAGE_SEPARATOR``. This is byte-identical to the
  ``combined_text`` the pipeline has always written to ``documents.ocrText``,
  so adopting this builder changes no stored text, chunk or embedding;
* a page with no usable text (failed, deferred, withheld ``(cid:N)``) still
  occupies a zero-length span, so it is represented in the page map rather
  than silently disappearing.

Content availability and reliability are separate on purpose: a page that
needs human review still contributes its usable text, and its review state is
carried beside it in the page map, never by dropping the text.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ...models.processing_state import _UNRESOLVED_PAGE_STATUSES
from .models import ExtractedPage

PAGE_SEPARATOR = "\n\n"

#: Bumped only when the assembly format above changes. Persisted with every
#: manifest so a reader can tell which rule produced a stored checksum.
CANONICAL_FORMAT_VERSION = "1"

BUILD_COMPLETE = "complete"
BUILD_PARTIAL = "partial"


class DuplicatePageError(ValueError):
    """Two page records claim the same page number.

    Never resolved by picking one: a retry that left two contradictory records
    for a page is exactly the corruption canonical evidence must surface.
    """

    def __init__(self, page_numbers: Sequence[int]) -> None:
        self.page_numbers = sorted(set(page_numbers))
        super().__init__(f"duplicate page records for page(s) {self.page_numbers}")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PageSpan:
    """Where one source page sits in the canonical text, and how reliable it is.

    ``start``/``end`` are character offsets into the canonical text
    (``text[start:end]`` is the page's canonical text). Carries no page text,
    so the manifest that holds these can be logged and stored freely.
    """

    page_number: int
    start: int
    end: int
    content_sha256: str
    status: str
    source: str
    page_class: str
    quality_verdict: Optional[str]
    needs_review: bool
    unresolved: bool
    text_withheld: bool
    #: True when the page row keeps an original extraction that differs from
    #: the canonical text (a deterministic repair, or withheld text).
    has_original_text: bool
    repair_count: int
    table_count: int

    @property
    def char_count(self) -> int:
        return self.end - self.start

    def to_record(self) -> Dict[str, Any]:
        return {
            "page_number": self.page_number,
            "start": self.start,
            "end": self.end,
            "content_sha256": self.content_sha256,
            "status": self.status,
            "source": self.source,
            "page_class": self.page_class,
            "quality_verdict": self.quality_verdict,
            "needs_review": self.needs_review,
            "unresolved": self.unresolved,
            "text_withheld": self.text_withheld,
            "has_original_text": self.has_original_text,
            "repair_count": self.repair_count,
            "table_count": self.table_count,
        }


@dataclass(frozen=True)
class CanonicalDocument:
    text: str
    page_map: List[PageSpan] = field(default_factory=list)

    @property
    def page_numbers(self) -> List[int]:
        return [span.page_number for span in self.page_map]

    @property
    def page_count(self) -> int:
        return len(self.page_map)

    @property
    def char_count(self) -> int:
        return len(self.text)

    @property
    def sha256(self) -> str:
        return sha256_text(self.text)

    @property
    def review_pages(self) -> List[int]:
        return [span.page_number for span in self.page_map if span.needs_review]

    @property
    def unresolved_pages(self) -> List[int]:
        return [span.page_number for span in self.page_map if span.unresolved]

    @property
    def withheld_pages(self) -> List[int]:
        return [span.page_number for span in self.page_map if span.text_withheld]

    @property
    def build_status(self) -> str:
        """Content completeness only. Review is reliability, reported apart."""
        if self.unresolved_pages or self.withheld_pages:
            return BUILD_PARTIAL
        return BUILD_COMPLETE

    def page_text(self, page_number: int) -> str:
        for span in self.page_map:
            if span.page_number == page_number:
                return self.text[span.start : span.end]
        raise KeyError(page_number)

    def page_at(self, offset: int) -> Optional[int]:
        """The source page that canonical character ``offset`` came from."""
        for span in self.page_map:
            if span.start <= offset < span.end:
                return span.page_number
        return None

    def manifest(self) -> Dict[str, Any]:
        """Integrity metadata for persistence and logs. Contains no page text."""
        return {
            "canonical_format_version": CANONICAL_FORMAT_VERSION,
            "build_status": self.build_status,
            "page_count": self.page_count,
            "page_numbers": self.page_numbers,
            "canonical_char_count": self.char_count,
            "canonical_sha256": self.sha256,
            "review_pages": self.review_pages,
            "unresolved_pages": self.unresolved_pages,
            "withheld_pages": self.withheld_pages,
            "page_map": [span.to_record() for span in self.page_map],
        }


def _span(page: ExtractedPage, start: int, end: int, text: str) -> PageSpan:
    original = page.raw_text
    return PageSpan(
        page_number=int(page.number),
        start=start,
        end=end,
        content_sha256=sha256_text(text),
        status=page.status.value,
        source=page.source.value,
        page_class=page.classification.page_class.value,
        quality_verdict=page.quality_verdict,
        needs_review=bool(page.needs_review),
        unresolved=page.status in _UNRESOLVED_PAGE_STATUSES,
        text_withheld=bool(page.text_withheld),
        has_original_text=original is not None and original != text,
        repair_count=len(page.applied_repairs or []),
        table_count=len(page.tables or []),
    )


def assemble_canonical_document(pages: Iterable[ExtractedPage]) -> CanonicalDocument:
    """Assemble the complete canonical text and its page map. Pure and total.

    Deterministic: the same page evidence always yields byte-identical text
    and an identical page map, whatever order the pages arrive in.
    """
    ordered = sorted(pages, key=lambda page: int(page.number))
    numbers = [int(page.number) for page in ordered]
    duplicates = [n for i, n in enumerate(numbers) if i and numbers[i - 1] == n]
    if duplicates:
        raise DuplicatePageError(duplicates)

    parts: List[str] = []
    spans: List[PageSpan] = []
    offset = 0
    for index, page in enumerate(ordered):
        if index:
            parts.append(PAGE_SEPARATOR)
            offset += len(PAGE_SEPARATOR)
        text = page.text or ""
        parts.append(text)
        spans.append(_span(page, offset, offset + len(text), text))
        offset += len(text)
    return CanonicalDocument(text="".join(parts), page_map=spans)
