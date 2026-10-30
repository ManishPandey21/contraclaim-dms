"""Page rasterization for the vision fallback.

Backed by pypdfium2 (Apache-licensed, already installed via pdfplumber) rather
than PyMuPDF, which the companion design rejected on AGPL grounds.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from rbac_backend.services.extraction.rasterizer import (
    DEFAULT_DPI,
    PageRasterizer,
    RasterizationError,
)
from rbac_backend.tests.fixtures.pdf_builders import build_mixed_pdf


def test_default_dpi_is_150() -> None:
    assert DEFAULT_DPI == 150


def test_renders_a_page_to_png_bytes(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")

    data = PageRasterizer().render_page(source, 3)
    image = Image.open(io.BytesIO(data))

    assert image.format == "PNG"
    assert image.width > 1000
    assert image.height > 1000


def test_landscape_page_renders_wider_than_tall(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")

    image = Image.open(io.BytesIO(PageRasterizer().render_page(source, 5)))

    assert image.width > image.height


def test_crop_returns_only_the_requested_region(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    rasterizer = PageRasterizer()

    full = Image.open(io.BytesIO(rasterizer.render_page(source, 3)))
    cropped = Image.open(
        io.BytesIO(rasterizer.crop_region(source, 3, (0.0, 0.0, 200.0, 200.0)))
    )

    assert cropped.width < full.width
    assert cropped.height < full.height


def test_lower_dpi_produces_a_smaller_image(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")

    small = Image.open(io.BytesIO(PageRasterizer(dpi=72).render_page(source, 3)))
    large = Image.open(io.BytesIO(PageRasterizer(dpi=150).render_page(source, 3)))

    assert small.width < large.width


def test_out_of_range_page_raises(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")

    with pytest.raises(RasterizationError):
        PageRasterizer().render_page(source, 99)


def test_zero_area_bbox_raises(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")

    with pytest.raises(RasterizationError):
        PageRasterizer().crop_region(source, 3, (10.0, 10.0, 10.0, 10.0))


def test_missing_file_raises_rather_than_returning_empty_bytes(
    tmp_path: Path,
) -> None:
    with pytest.raises(RasterizationError):
        PageRasterizer().render_page(tmp_path / "absent.pdf", 1)


def test_standalone_image_renders_as_logical_page_one(tmp_path: Path) -> None:
    source = tmp_path / "scan.png"
    Image.new("RGB", (320, 240), "white").save(source)

    rendered = Image.open(io.BytesIO(PageRasterizer().render_page(source, 1)))

    assert rendered.size == (320, 240)
    with pytest.raises(RasterizationError):
        PageRasterizer().render_page(source, 2)


def test_standalone_image_can_be_cropped(tmp_path: Path) -> None:
    source = tmp_path / "scan.png"
    Image.new("RGB", (320, 240), "white").save(source)

    cropped = Image.open(
        io.BytesIO(PageRasterizer().crop_region(source, 1, (0.0, 0.0, 100.0, 80.0)))
    )

    assert cropped.size == (100, 80)


def test_crop_is_clamped_to_the_page_rather_than_erroring(tmp_path: Path) -> None:
    # A bbox running past the page edge is a rounding artefact, not a bug.
    source = build_mixed_pdf(tmp_path / "mixed.pdf")

    data = PageRasterizer().crop_region(source, 3, (500.0, 700.0, 5000.0, 5000.0))

    assert Image.open(io.BytesIO(data)).width > 0
