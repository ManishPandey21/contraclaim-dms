"""The golden page-routing contract for fixture #1.

This test does not exercise production code. It pins the expectations every
later phase is measured against, and fails if the fixture drifts away from
them.
"""

from __future__ import annotations

import json
from pathlib import Path

import pdfplumber

from rbac_backend.tests.fixtures.pdf_builders import build_mixed_pdf

GOLDEN = Path(__file__).parent / "fixtures" / "golden_page_routing.json"


def test_golden_file_exists_and_is_well_formed() -> None:
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))

    assert data["pages_total"] == 9
    assert data["min_text_chars_per_page"] == 40
    assert data["pages_expected_ocr"] == [1, 2]
    assert data["pages_expected_native"] == [3, 4, 5, 6, 7, 8, 9]
    assert data["expected_batches"] == [[1, 2]]


def test_fixture_matches_golden_expectations(tmp_path: Path) -> None:
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    threshold = data["min_text_chars_per_page"]
    target = build_mixed_pdf(tmp_path / "mixed.pdf")

    with pdfplumber.open(target) as pdf:
        thin = [
            index + 1
            for index, page in enumerate(pdf.pages)
            if len((page.extract_text() or "").strip()) < threshold
        ]
        thick = [
            index + 1
            for index, page in enumerate(pdf.pages)
            if len((page.extract_text() or "").strip()) >= threshold
        ]

    assert thin == data["pages_expected_ocr"]
    assert thick == data["pages_expected_native"]
