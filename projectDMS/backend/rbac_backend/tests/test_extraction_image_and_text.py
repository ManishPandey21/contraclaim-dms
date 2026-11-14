"""Images and text files get their own extractors, not the PDF path."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from rbac_backend.services.extraction.image_extractor import (
    extract_image,
    extract_text_file,
)
from rbac_backend.services.extraction.image_ocr_runner import TesseractImageOcrRunner
from rbac_backend.services.extraction.models import (
    Completeness,
    PageClass,
    PageSource,
    PageStatus,
)
from rbac_backend.services.extraction.page_store import ImageOcrRunner, NullPageStore


class _StubImageOcrRunner:
    def __init__(self, text: str = "SCANNED SITE INSTRUCTION") -> None:
        self.text = text
        self.calls: list[Path] = []

    async def run(self, source: Path, language: str) -> str:
        self.calls.append(source)
        return self.text


class _FailingImageOcrRunner:
    async def run(self, source: Path, language: str) -> str:
        raise RuntimeError("tesseract exploded")


def _png(path: Path, size: tuple[int, int] = (800, 600)) -> Path:
    Image.new("RGB", size, color=(200, 200, 200)).save(path)
    return path


def test_standalone_image_runner_has_the_non_pdf_protocol() -> None:
    assert isinstance(TesseractImageOcrRunner(), ImageOcrRunner)


def test_image_and_pdf_runners_have_incompatible_signatures() -> None:
    """The two runners are not interchangeable, and this is how you can tell.

    `isinstance` cannot express it: runtime_checkable Protocols compare method
    *names* only, so an image runner structurally satisfies OcrRunner just by
    having a `run`. The real distinction is the signature - the PDF runner
    takes page numbers and returns a per-page mapping; the image runner takes
    neither and returns a single string.
    """
    import inspect

    from rbac_backend.services.extraction.ocrmypdf_runner import OcrMyPdfRunner

    image_params = set(inspect.signature(TesseractImageOcrRunner.run).parameters)
    pdf_params = set(inspect.signature(OcrMyPdfRunner.run).parameters)

    assert "page_numbers" in pdf_params
    assert "page_numbers" not in image_params
    assert image_params != pdf_params


async def test_image_yields_one_page_of_ocr_text(tmp_path: Path) -> None:
    source = _png(tmp_path / "photo.png")

    result = await extract_image(
        source,
        store=NullPageStore(),
        image_ocr_runner=_StubImageOcrRunner(),
        language="eng",
    )

    assert len(result.pages) == 1
    assert result.pages[0].number == 1
    assert result.pages[0].text == "SCANNED SITE INSTRUCTION"
    assert result.pages[0].source is PageSource.OCR
    assert result.pages[0].status is PageStatus.OCR_COMPLETED
    assert result.completeness is Completeness.COMPLETE


async def test_image_dimensions_are_recorded(tmp_path: Path) -> None:
    source = _png(tmp_path / "photo.png", size=(1024, 768))

    result = await extract_image(
        source,
        store=NullPageStore(),
        image_ocr_runner=_StubImageOcrRunner(),
        language="eng",
    )
    classification = result.pages[0].classification

    assert classification.width == 1024
    assert classification.height == 768
    assert classification.page_class is PageClass.SCANNED_IMAGE


async def test_image_is_passed_to_ocr_unchanged(tmp_path: Path) -> None:
    source = _png(tmp_path / "photo.png")
    before = source.read_bytes()
    runner = _StubImageOcrRunner()

    await extract_image(
        source, store=NullPageStore(), image_ocr_runner=runner, language="eng"
    )

    assert runner.calls == [source]
    # Never wrapped into a PDF: the file we hold is the file the user uploaded.
    assert source.read_bytes() == before
    assert source.suffix == ".png"


async def test_empty_ocr_on_an_image_is_marked_not_pretended(tmp_path: Path) -> None:
    source = _png(tmp_path / "blank.png")

    result = await extract_image(
        source,
        store=NullPageStore(),
        image_ocr_runner=_StubImageOcrRunner(text=""),
        language="eng",
    )

    assert result.pages[0].status is PageStatus.OCR_EMPTY
    assert result.completeness is Completeness.PARTIAL


async def test_failed_image_ocr_is_partial_and_records_the_error(
    tmp_path: Path,
) -> None:
    source = _png(tmp_path / "photo.png")
    store = NullPageStore()

    result = await extract_image(
        source,
        store=store,
        image_ocr_runner=_FailingImageOcrRunner(),
        language="eng",
    )

    assert result.pages[0].status is PageStatus.OCR_FAILED
    assert result.pages[0].error
    assert result.ocr_failed_pages == [1]
    assert result.completeness is Completeness.PARTIAL
    assert store.batches[0]["status"] == "failed"


async def test_image_pages_are_recorded_through_the_store(tmp_path: Path) -> None:
    source = _png(tmp_path / "photo.png")
    store = NullPageStore()

    await extract_image(
        source,
        store=store,
        image_ocr_runner=_StubImageOcrRunner(),
        language="eng",
    )

    assert [page.number for page in store.recorded_pages] == [1]


async def test_text_file_is_read_without_ocr(tmp_path: Path) -> None:
    source = tmp_path / "note.txt"
    source.write_text("Ref: CC/2026/001\nSite instruction issued.", encoding="utf-8")

    result = await extract_text_file(source, store=NullPageStore())

    assert result.pages[0].source is PageSource.TEXT_LAYER
    assert result.pages[0].status is PageStatus.TEXT_LAYER
    assert "Site instruction issued." in result.combined_text
    assert result.ocr_pages_total == 0


async def test_empty_text_file_is_complete_but_marked_empty(tmp_path: Path) -> None:
    source = tmp_path / "empty.txt"
    source.write_text("", encoding="utf-8")

    result = await extract_text_file(source, store=NullPageStore())

    assert result.pages[0].source is PageSource.EMPTY
    assert result.completeness is Completeness.COMPLETE


async def test_text_file_with_invalid_bytes_does_not_raise(tmp_path: Path) -> None:
    source = tmp_path / "latin.txt"
    source.write_bytes(b"Chainage 12\xff\xfe m")

    result = await extract_text_file(source, store=NullPageStore())

    assert "Chainage 12" in result.combined_text
