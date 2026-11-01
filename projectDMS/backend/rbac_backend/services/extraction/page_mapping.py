"""Map source PDF page numbers to their index in an OCR batch's output.

Background: OCRmyPDF invoked with ``--pages A-B`` may either retain every input
page in its output or trim the output to the requested range. The previous
implementation guessed between the two with ``output_count >= max(requested)``,
which coincides with the correct answer for some batches and silently
mis-attributes page text for others - storing page 8's text under page 1.

Measured against the production container on 2026-08-14
(``docs/architecture/phase0_extraction_measurements_2026-08-14.md``):
``ocrmypdf --pages 1-2`` on a 9-page input emits a **9-page** output. The
full-length branch is therefore the live one; the trimmed branch is defensive
cover for a different tool or a future version.

This module accepts only those two shapes and raises on anything else, because
a wrong mapping does not fail - it is invisible until someone cites the wrong
page in a claim.
"""

from __future__ import annotations

from typing import Dict, Sequence


class PageMappingError(Exception):
    """Raised when an OCR output's page count matches no expected shape."""


def map_source_pages_to_output(
    requested_pages: Sequence[int],
    output_page_count: int,
    input_page_count: int,
) -> Dict[int, int]:
    """Return ``{source_page_number: zero_based_output_index}``.

    Two output shapes are accepted:

    * **full-length** - the output has as many pages as the input, so a source
      page keeps its own position: ``index = page_number - 1``. This is what
      OCRmyPDF does, as measured.
    * **trimmed** - the output has exactly as many pages as were requested, so
      source pages map to their ordinal position within the batch.

    Any other shape raises :class:`PageMappingError` rather than falling back
    to a guess.
    """
    ordered = sorted({int(page) for page in requested_pages if int(page) > 0})
    if not ordered:
        return {}

    if ordered[-1] > input_page_count:
        raise PageMappingError(
            f"Requested page {ordered[-1]} exceeds input page count {input_page_count}"
        )

    if output_page_count == input_page_count:
        return {page: page - 1 for page in ordered}

    if output_page_count == len(ordered):
        return {page: index for index, page in enumerate(ordered)}

    raise PageMappingError(
        f"OCR output has {output_page_count} pages, which matches neither the "
        f"input page count ({input_page_count}) nor the requested batch size "
        f"({len(ordered)}). Refusing to guess the page mapping for "
        f"pages {ordered}."
    )
