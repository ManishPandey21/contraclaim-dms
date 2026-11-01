"""Assemble the minimum material that can answer the question.

Region-preferred: a crop when the failure is localised to one table or block,
the full page only when the failure is page-wide. The model never receives the
whole document, neighbouring pages, or unrelated metadata.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Tuple

from ..models import ExtractedPage
from ..rasterizer import PageRasterizer
from ..quality.models import QualityVerdict
from .models import Evidence

logger = logging.getLogger(__name__)


def assemble_evidence(
    source: Path,
    page: ExtractedPage,
    verdict: QualityVerdict,
    rasterizer: PageRasterizer,
    *,
    region: Optional[Tuple[float, float, float, float]] = None,
) -> Evidence:
    """Build the evidence packet for one page or region."""
    if region is not None:
        image = rasterizer.crop_region(source, page.number, region)
    else:
        image = rasterizer.render_page(source, page.number)

    return Evidence(
        page_number=page.number,
        image_png=image,
        is_region=region is not None,
        bbox=region,
        native_text=page.text or "",
        ocr_text=page.text if page.source.value == "ocr" else "",
        tables=[[list(row) for row in table] for table in (page.tables or [])],
        trigger_reasons=list(verdict.reasons),
        dpi=rasterizer.dpi,
    )
