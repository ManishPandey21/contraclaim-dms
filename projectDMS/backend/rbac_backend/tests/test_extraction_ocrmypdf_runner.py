"""OCRmyPDF batch runner with explicit page mapping."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from rbac_backend.services.extraction.ocrmypdf_runner import (
    OcrMyPdfRunner,
    OcrRunnerError,
)
from rbac_backend.services.extraction.page_store import OcrRunner
from rbac_backend.tests.fixtures.pdf_builders import build_mixed_pdf, build_text_pdf


def test_runner_satisfies_the_protocol(tmp_path: Path) -> None:
    assert isinstance(OcrMyPdfRunner(work_dir=tmp_path), OcrRunner)


def test_command_requests_only_the_batch_range(tmp_path: Path) -> None:
    runner = OcrMyPdfRunner(work_dir=tmp_path)

    command = runner._build_command(
        source=Path("in.pdf"),
        output=Path("out.pdf"),
        sidecar=Path("out.txt"),
        page_numbers=[3, 4, 5],
        language="eng",
    )

    assert "--pages" in command
    assert command[command.index("--pages") + 1] == "3-5"
    assert "--sidecar" in command


def test_command_formats_a_single_page_without_a_range(tmp_path: Path) -> None:
    runner = OcrMyPdfRunner(work_dir=tmp_path)

    command = runner._build_command(
        source=Path("in.pdf"),
        output=Path("out.pdf"),
        sidecar=Path("out.txt"),
        page_numbers=[7],
        language="eng",
    )

    assert command[command.index("--pages") + 1] == "7"


def test_command_carries_the_requested_language(tmp_path: Path) -> None:
    runner = OcrMyPdfRunner(work_dir=tmp_path)

    command = runner._build_command(
        source=Path("in.pdf"),
        output=Path("out.pdf"),
        sidecar=Path("out.txt"),
        page_numbers=[1],
        language="deu",
    )

    assert command[command.index("--language") + 1] == "deu"


def test_command_forces_ocr_on_pages_that_already_carry_text(tmp_path: Path) -> None:
    """Every page the engine sends has a text layer it judged unusable, or none.

    Measured against OCRmyPDF 16.10.4 with tesseract 5.5.0: without
    ``--force-ocr`` a batch containing any page that already has text exits 6
    (``PriorOcrFoundError: page already has text!``) - the whole batch, so a
    scanned page sharing a batch with a thin or ``(cid:N)`` page failed with it.
    ``--skip-text`` and ``--redo-ocr`` leave that text in place, which for a
    page of placeholders means the placeholders come back.
    """
    runner = OcrMyPdfRunner(work_dir=tmp_path)

    command = runner._build_command(
        source=Path("in.pdf"),
        output=Path("out.pdf"),
        sidecar=Path("out.txt"),
        page_numbers=[1, 2],
        language="eng",
    )

    assert "--force-ocr" in command
    assert "--skip-text" not in command
    assert "--redo-ocr" not in command


def test_input_page_count_is_read_from_the_source(tmp_path: Path) -> None:
    source = build_mixed_pdf(tmp_path / "mixed.pdf")
    runner = OcrMyPdfRunner(work_dir=tmp_path)

    assert runner._input_page_count(source) == 9


def test_empty_batch_returns_nothing_without_running_ocr(tmp_path: Path) -> None:
    runner = OcrMyPdfRunner(work_dir=tmp_path)

    async def _call() -> dict[int, str]:
        return await runner.run(tmp_path / "missing.pdf", [], "eng")

    assert asyncio.run(_call()) == {}


def test_full_length_output_reads_absolute_pages(tmp_path: Path) -> None:
    # A 9-page "output" stands in for OCRmyPDF retaining every input page,
    # which is what production measurement showed it does.
    output = build_mixed_pdf(tmp_path / "out.pdf")
    runner = OcrMyPdfRunner(work_dir=tmp_path)

    mapped = runner._extract_mapped_pages(
        output_path=output, page_numbers=[3, 4], input_page_count=9
    )

    assert set(mapped) == {3, 4}
    assert "Claim summary page 3" in mapped[3]
    assert "Claim summary page 4" in mapped[4]


def test_trimmed_output_reads_positional_pages(tmp_path: Path) -> None:
    output = build_text_pdf(tmp_path / "out.pdf", pages=2, text="Batch page")
    runner = OcrMyPdfRunner(work_dir=tmp_path)

    mapped = runner._extract_mapped_pages(
        output_path=output, page_numbers=[8, 9], input_page_count=9
    )

    assert set(mapped) == {8, 9}
    assert "Page 1 of 2" in mapped[8]
    assert "Page 2 of 2" in mapped[9]


def test_unexpected_output_shape_raises_rather_than_guessing(tmp_path: Path) -> None:
    output = build_text_pdf(tmp_path / "out.pdf", pages=5)
    runner = OcrMyPdfRunner(work_dir=tmp_path)

    with pytest.raises(OcrRunnerError):
        runner._extract_mapped_pages(
            output_path=output, page_numbers=[1, 2], input_page_count=9
        )


def test_work_dir_is_created_on_demand(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "ocr"
    OcrMyPdfRunner(work_dir=target)

    # Construction must not require the directory to pre-exist; the runner
    # creates it when it actually writes a batch.
    assert not target.exists() or target.is_dir()
