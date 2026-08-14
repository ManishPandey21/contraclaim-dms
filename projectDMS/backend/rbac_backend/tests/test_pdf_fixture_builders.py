"""Deterministic PDF fixtures used by the extraction test-suite."""

from __future__ import annotations

from pathlib import Path

import pdfplumber

from rbac_backend.tests.fixtures.pdf_builders import (
    build_mixed_pdf,
    build_scanned_only_pdf,
    build_text_pdf,
)


def test_mixed_pdf_has_nine_pages_with_two_textless(tmp_path: Path) -> None:
    target = build_mixed_pdf(tmp_path / "mixed.pdf")

    with pdfplumber.open(target) as pdf:
        assert len(pdf.pages) == 9
        char_counts = [len((page.extract_text() or "").strip()) for page in pdf.pages]

    assert char_counts[0] == 0
    assert char_counts[1] == 0
    assert all(count > 40 for count in char_counts[2:])


def test_mixed_pdf_page_five_is_landscape(tmp_path: Path) -> None:
    target = build_mixed_pdf(tmp_path / "mixed.pdf")

    with pdfplumber.open(target) as pdf:
        page = pdf.pages[4]
        assert page.width > page.height


def test_scanned_only_pdf_has_no_text_layer(tmp_path: Path) -> None:
    target = build_scanned_only_pdf(tmp_path / "scan.pdf", pages=3)

    with pdfplumber.open(target) as pdf:
        assert len(pdf.pages) == 3
        assert all(not (page.extract_text() or "").strip() for page in pdf.pages)


def test_text_pdf_is_reproducible(tmp_path: Path) -> None:
    first = build_text_pdf(tmp_path / "a.pdf", pages=2, text="Letter No. ABC/123")
    second = build_text_pdf(tmp_path / "b.pdf", pages=2, text="Letter No. ABC/123")

    assert first.read_bytes() == second.read_bytes()
