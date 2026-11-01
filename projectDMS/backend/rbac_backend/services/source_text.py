"""Which stored text is the correspondence body, and whether it is the source.

A document can carry three bodies:

* ``ocrText`` - the native/OCR text the extractor read from the file. When
  there was no such text, the legacy writer stores the whole LLM extraction
  report here instead (``text_for_db = raw_ocr_text or extracted_content``).
* ``full_text`` - historically the LLM's report Item 25 ("Full Content"), a
  retyped copy of the letter. From this change on it is the source text
  whenever complete source text existed, and Item 25 only as a fallback.
* ``body`` - a human/application-supplied body on non-extracted records.

The LLM saw nothing but the source text when it wrote Item 25 on the OCR-text
path, so Item 25 can never be more complete than that source - only less
faithful. The rule is therefore: the source outranks any LLM rendering of it,
and the extraction report is never a letter body.

Provenance is written on every new extraction (``ocr_text_kind``,
``full_text_source``, ``source_text_status``). Rows written before that carry
no marker, so their ``ocrText`` is classified by its shape - the report
carries its schema-numbered item labels and the reply-advice item; a letter
does not. A letter misclassified as the report only falls back to the
pre-change order (``full_text`` first), never further.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Mapping, Optional

#: ``documents.ocr_text_kind`` values.
OCR_TEXT_KIND_SOURCE = "source_text"
OCR_TEXT_KIND_REPORT = "extraction_report"

#: ``documents.full_text_source`` values.
FULL_TEXT_SOURCE_SOURCE = "source_text"
FULL_TEXT_SOURCE_LLM = "llm_full_content"

#: ``documents.source_text_status`` values.
SOURCE_TEXT_COMPLETE = "complete"
SOURCE_TEXT_UNVERIFIED = "unverified"
SOURCE_TEXT_ABSENT = "absent"

#: A numbered item line, markdown emphasis tolerated: ``25) Full Content:``,
#: ``**1) Date:**``, ``- 2) __Letter No.__:``. Group 1 = number, 2 = label.
_NUMBERED_LINE = re.compile(
    r"^[\s*_-]*(\d{1,2})\)\s*(?:\*\*|__)?\s*([^:\n]{1,80}?)\s*(?:\*\*|__)?\s*:",
    re.MULTILINE,
)
_REPORT_MIN_SCHEMA_ITEMS = 3


def is_extraction_report_text(text: Any) -> bool:
    """Is ``text`` the LLM extraction report rather than a letter?"""
    value = str(text or "")
    if not value.strip():
        return False
    from ..retrieval.correspondence_payload import has_advisory_report_item

    if has_advisory_report_item(value):
        return True
    # The report's own schema: an item counts only when its number carries
    # the label the report layout puts there (the parser's table - current
    # and older layouts). A letter's numbered list ("1) Location: ...",
    # "2) Chainage From: ...", or a reference list repeating "Letter No.")
    # does not line up with that numbering.
    from .text_processing_service import _ITEM_LABELS

    matched = set()
    for number_text, label in _NUMBERED_LINE.findall(value):
        number = int(number_text)
        pattern = _ITEM_LABELS.get(number)
        if pattern and re.search(pattern, label.strip("*_ ").strip(), re.IGNORECASE):
            matched.add(number)
    return len(matched) >= _REPORT_MIN_SCHEMA_ITEMS


def source_text_is_complete(raw_text: Optional[str], extraction: Any) -> bool:
    """Did extraction produce complete source text for the whole document?

    Only the unified pipeline can answer. Its quality gate re-derives
    ``completeness`` from every page after repairs and fallbacks, so COMPLETE
    means no page is failed, deferred, unrenderable or awaiting review; a
    withheld (unusable) text layer is refused as well.

    The legacy pipeline (``extraction is None``) makes one document-level text
    layer decision and can silently miss scanned pages of a mixed PDF, so its
    completeness is unknown - never assumed.
    """
    if not raw_text or not raw_text.strip() or extraction is None:
        return False
    if not list(getattr(extraction, "pages", None) or []):
        return False
    completeness = getattr(extraction, "completeness", None)
    if getattr(completeness, "value", completeness) != "complete":
        return False
    for unresolved in (
        "withheld_pages",
        "ocr_failed_pages",
        "ocr_deferred_pages",
        "unrenderable_pages",
    ):
        if getattr(extraction, unresolved, None):
            return False
    return True


def source_ocr_text(document: Optional[Mapping[str, Any]]) -> str:
    """``ocrText`` when it is the document's source text, else ``""``."""
    if not document:
        return ""
    value = document.get("ocrText")
    if not value:
        return ""
    kind = document.get("ocr_text_kind")
    if kind == OCR_TEXT_KIND_SOURCE:
        return str(value)
    if kind == OCR_TEXT_KIND_REPORT:
        return ""
    return "" if is_extraction_report_text(value) else str(value)


def _full_text(document: Mapping[str, Any]) -> str:
    # Never classified by shape: `full_text` has only ever held a letter body
    # (Item 25 or source text), and dropping it on a heuristic could leave a
    # row worse off than the old order - served its summary instead.
    value = document.get("full_text")
    return str(value) if value else ""


def select_body_text(
    document: Optional[Mapping[str, Any]], *, include_summary: bool = True
) -> str:
    """The document body, by source authority. No consumability check.

    ``body`` > source ``ocrText`` > ``full_text`` > ``summary``.

    The extraction report is never returned: it is LLM output that carries
    reply advice, not the letter.
    """
    if not document:
        return ""
    body = document.get("body")
    if body:
        return str(body)
    text = source_ocr_text(document) or _full_text(document)
    if text:
        return text
    if include_summary:
        summary = document.get("summary")
        if summary:
            return str(summary)
    return ""


def full_text_updates(metadata: Any) -> Dict[str, Any]:
    """The ``documents.full_text`` write for one extraction, with provenance.

    Both writers (``DatabaseService`` inside the processor and
    ``DocumentService.process_document_async``) use this, so neither can put
    the LLM's Item 25 back over source text the other wrote.

    Empty when the run produced no body: a degraded run never erases the
    stored one (the same rule the metadata snapshot follows).
    """
    body = getattr(metadata, "body_text", None)
    if body:
        return {
            "full_text": body,
            "full_text_source": getattr(metadata, "body_text_source", None)
            or FULL_TEXT_SOURCE_SOURCE,
        }
    content = getattr(metadata, "full_content", None)
    if content:
        return {"full_text": content, "full_text_source": FULL_TEXT_SOURCE_LLM}
    return {}


__all__ = [
    "FULL_TEXT_SOURCE_LLM",
    "FULL_TEXT_SOURCE_SOURCE",
    "OCR_TEXT_KIND_REPORT",
    "OCR_TEXT_KIND_SOURCE",
    "SOURCE_TEXT_ABSENT",
    "SOURCE_TEXT_COMPLETE",
    "SOURCE_TEXT_UNVERIFIED",
    "full_text_updates",
    "is_extraction_report_text",
    "select_body_text",
    "source_ocr_text",
    "source_text_is_complete",
]
