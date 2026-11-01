"""A carried repaired page is not a withheld page.

Where the two changes meet. PR #25 withholds a page's unusable ``(cid:N)``
text, keeps it whole in ``raw_text``, and reports the page in
``withheld_pages`` so no consumer reads that text layer by another route -
``DocumentProcessor`` skips the whole-file OpenAI upload for such a document.
PR #28 makes an extraction run cumulative, so an attempt's result also carries
pages resolved by earlier attempts, restored from the store.

``raw_text`` has two writers: the engine when it withholds unusable text, and
the quality gate when a deterministic repair rewrites a page's text. Deriving
``withheld_pages`` from ``raw_text is not None`` therefore reports a carried
*repaired* page - whose published text is perfectly good - as withheld, and the
document loses its whole-file extraction for a reason that does not exist.

The withhold decision is explicit (`text_withheld`), so the two cannot be
confused, and it survives the store round-trip that a carried page comes back
through.
"""

from __future__ import annotations

from typing import Any, Dict, List

from rbac_backend.services.extraction.models import (
    ExtractedPage,
    PageClassification,
    PageClass,
    PageSource,
    PageStatus,
)
from rbac_backend.services.extraction_adapters.document_page_store import (
    from_document_page_record,
    to_document_page_record,
)


def _classification() -> PageClassification:
    return PageClassification(
        page_class=PageClass.TEXT_NATIVE,
        char_count=10,
        image_count=0,
        image_coverage=0.0,
        table_count=0,
        width=595.0,
        height=842.0,
        rotation=0,
    )


def _page(number: int, **overrides: Any) -> ExtractedPage:
    fields: Dict[str, Any] = dict(
        number=number,
        text="Readable body text",
        source=PageSource.TEXT_LAYER,
        status=PageStatus.TEXT_LAYER,
        classification=_classification(),
    )
    fields.update(overrides)
    return ExtractedPage(**fields)


def _round_trip(page: ExtractedPage) -> ExtractedPage:
    record = to_document_page_record(
        page,
        document_id="doc-1",
        organization_id="org-1",
        project_id=None,
        extraction_run_id="run-1",
    )
    return from_document_page_record(record)


def test_a_repaired_page_is_not_withheld() -> None:
    """The gate sets raw_text on a repair; the page's text is still published."""
    repaired = _page(3, text="Repaired body text", raw_text="Original body text")

    assert repaired.text_withheld is False
    assert _round_trip(repaired).text_withheld is False


def test_a_withheld_page_says_so_through_the_store() -> None:
    """A carried page comes back from the store, so the flag must survive it."""
    withheld = _page(
        4,
        text="",
        source=PageSource.EMPTY,
        raw_text="(cid:78)(cid:111)(cid:116)(cid:105)(cid:99)(cid:101)",
        text_withheld=True,
    )

    restored = _round_trip(withheld)

    assert restored.text_withheld is True
    assert restored.raw_text is not None and "(cid:" in restored.raw_text
    assert restored.text == ""


def test_the_result_reports_only_genuinely_withheld_pages() -> None:
    """withheld_pages drives a publication decision, so it must not over-report."""
    from rbac_backend.services.extraction.engine import PageExtractionEngine

    pages: List[ExtractedPage] = [
        _page(1),
        _page(2, text="Repaired body text", raw_text="Original body text"),
        _page(
            3,
            text="",
            source=PageSource.EMPTY,
            raw_text="(cid:78)(cid:111)",
            text_withheld=True,
        ),
    ]

    assert PageExtractionEngine._withheld_page_numbers(pages) == [3]
