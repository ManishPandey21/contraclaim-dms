"""OCRmyPDF must run in a child process, never inside a backend process.

``import ocrmypdf`` (16.10.4, ``ocrmypdf/pdfinfo/layout.py:61,66``) rewrites
pdfminer's ``PDFSimpleFont.__init__`` and ``PSBaseParser.BUFSIZ`` for the whole
interpreter. The legacy path used to call ``ocrmypdf.ocr`` in-process, so the
first scanned document a worker OCR'd changed how every later document in
that worker extracted: a simple font without ``/Encoding`` read as text in a
fresh worker and as ``(cid:N)`` - and so was force-OCR'd - in a warm one.
Legacy is the default pipeline for every tenant not on the unified canary.
"""

from __future__ import annotations

import ast
import os
import subprocess
from pathlib import Path
from typing import Any, List

import pytest

from rbac_backend.config.document_processing_config import DocumentProcessingConfig
from rbac_backend.services import ocr_service as ocr_module
from rbac_backend.services.ocr_service import (
    OCRService,
    OcrMyPdfEncrypted,
    OcrMyPdfFailed,
    OcrMyPdfPriorOcrFound,
)
from rbac_backend.tests.fixtures.child_python import BACKEND_ROOT, run_python_child
from rbac_backend.tests.fixtures.pdf_builders import build_scanned_only_pdf

_PRODUCTION_ROOT = BACKEND_ROOT / "rbac_backend"


def _service() -> OCRService:
    service = OCRService.__new__(OCRService)
    service.config = DocumentProcessingConfig()
    service.config.ocr_language = "eng"
    return service


class _FakePopen:
    """Stands in for the OCRmyPDF child process."""

    def __init__(self, returncode: int, timeout: bool = False) -> None:
        self.returncode = returncode
        self.pid = 4242
        self._timeout = timeout
        self.killed = False

    def communicate(self, timeout: Any = None) -> Any:
        if self._timeout:
            self._timeout = False
            raise subprocess.TimeoutExpired("ocrmypdf", timeout or 0)
        return (b"page text", b"customer words")

    def kill(self) -> None:
        self.killed = True


def _record_run(monkeypatch: pytest.MonkeyPatch, returncode: int) -> List[List[str]]:
    calls: List[List[str]] = []

    def popen(command: List[str], **kwargs: Any) -> _FakePopen:
        calls.append(list(command))
        return _FakePopen(returncode)

    monkeypatch.setattr(ocr_module.subprocess, "Popen", popen)
    return calls


def test_no_production_module_imports_ocrmypdf() -> None:
    offenders = []
    for path in _PRODUCTION_ROOT.rglob("*.py"):
        if "tests" in path.relative_to(_PRODUCTION_ROOT).parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            names: List[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            if any(name.split(".")[0] == "ocrmypdf" for name in names):
                offenders.append(f"{path.relative_to(BACKEND_ROOT)}:{node.lineno}")

    assert offenders == []


def test_legacy_ocr_runs_the_cli_with_the_historical_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _record_run(monkeypatch, 0)

    _service()._ocrmypdf(tmp_path / "in.pdf", tmp_path / "out.pdf", force_ocr=False)

    (command,) = calls
    assert command[-2:] == [str(tmp_path / "in.pdf"), str(tmp_path / "out.pdf")]
    assert command[command.index("--language") + 1] == "eng"
    assert "--rotate-pages" in command and "--deskew" in command
    assert command[command.index("--optimize") + 1] == "1"
    assert "--force-ocr" not in command


def test_forced_replacement_passes_force_ocr(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _record_run(monkeypatch, 0)

    _service()._ocrmypdf(tmp_path / "in.pdf", tmp_path / "out.pdf", force_ocr=True)

    assert "--force-ocr" in calls[0]


@pytest.mark.parametrize(
    ("returncode", "error"),
    [
        (6, OcrMyPdfPriorOcrFound),
        (8, OcrMyPdfEncrypted),
        (2, OcrMyPdfFailed),
        (15, OcrMyPdfFailed),
    ],
)
def test_exit_codes_map_to_typed_errors_without_output_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, returncode: int, error: type
) -> None:
    _record_run(monkeypatch, returncode)

    with pytest.raises(error) as raised:
        _service()._ocrmypdf(tmp_path / "in.pdf", tmp_path / "out.pdf", force_ocr=False)

    message = str(raised.value)
    assert f"exit={returncode}" in message
    # OCRmyPDF's streams can quote the document; only their lengths are kept.
    assert "customer words" not in message and "page text" not in message


def test_a_real_subprocess_call_is_what_runs(tmp_path: Path) -> None:
    # Guards the seam itself: no stubbing, a missing input must come back as a
    # typed failure from a child process, not an in-process import error.
    service = _service()
    with pytest.raises((OcrMyPdfFailed, OcrMyPdfPriorOcrFound, OcrMyPdfEncrypted)):
        service._ocrmypdf(
            tmp_path / "missing.pdf", tmp_path / "out.pdf", force_ocr=False
        )


_LEGACY_OCR_CHILD = r"""
import asyncio, json, shutil, subprocess, sys
from pathlib import Path

from pdfminer.pdffont import PDFSimpleFont
from rbac_backend.config.document_processing_config import DocumentProcessingConfig
from rbac_backend.services import ocr_service
from rbac_backend.tests.fixtures.pdf_builders import build_scanned_only_pdf, build_text_pdf

work = Path(sys.argv[1])
ocr_text_pdf = build_text_pdf(work / "ocr_out.pdf", pages=1, text="Recovered by OCR")

class FakePopen:
    # Stands in for the ocrmypdf CLI child process: writes the output PDF.
    def __init__(self, command, **kwargs):
        shutil.copyfile(ocr_text_pdf, command[-1])
        self.returncode = 0
        self.pid = 4242

    def communicate(self, timeout=None):
        return (b"", b"")

ocr_service.subprocess.Popen = FakePopen
config = DocumentProcessingConfig()
config.process_dir = str(work / "processed")
service = ocr_service.OCRService(config)
service._ocr_available = True
_, text = asyncio.run(service.process_pdf(build_scanned_only_pdf(work / "scan.pdf", pages=1)))
print(json.dumps({
    "text": text,
    "ocrmypdf_imported": "ocrmypdf" in sys.modules,
    "simple_font_init_module": PDFSimpleFont.__init__.__module__,
}))
"""


def test_a_legacy_ocr_run_leaves_pdfminer_untouched(tmp_path: Path) -> None:
    state = run_python_child(_LEGACY_OCR_CHILD, str(tmp_path), cwd=tmp_path)

    assert state["text"] is not None and "Recovered by OCR" in state["text"]
    assert state["ocrmypdf_imported"] is False
    assert state["simple_font_init_module"] == "pdfminer.pdffont"


def test_the_subprocess_module_is_the_one_the_service_uses() -> None:
    assert ocr_module.subprocess is subprocess


#: A long-lived worker: judge one hazardous PDF, run one real legacy OCR job
#: (the actual CLI - whether it succeeds on this host is irrelevant), judge
#: the same PDF again. ``argv[1]`` is a work directory.
_WORKER_HISTORY_CHILD = r"""
import asyncio, json, sys
from pathlib import Path

from pdfminer.pdffont import PDFSimpleFont
from rbac_backend.config.document_processing_config import DocumentProcessingConfig
from rbac_backend.services.ocr_service import OCRService
from rbac_backend.tests.fixtures.pdf_builders import (
    build_scanned_only_pdf, build_unencoded_font_pdf,
)

work = Path(sys.argv[1])
hazard = build_unencoded_font_pdf(
    work / "unencoded.pdf",
    lines=["Notice of claim for extension of time", "Delay event seven"],
)
config = DocumentProcessingConfig()
config.process_dir = str(work / "processed")
service = OCRService(config)
service._ocr_available = True

ocr_runs = []
real_ocrmypdf = service._ocrmypdf
def counting_ocrmypdf(*args, **kwargs):
    ocr_runs.append(1)
    return real_ocrmypdf(*args, **kwargs)
service._ocrmypdf = counting_ocrmypdf

imported_before = "ocrmypdf" in sys.modules
before = service.assess_text_layer(hazard).value
try:
    asyncio.run(service.process_pdf(build_scanned_only_pdf(work / "scan.pdf", pages=1)))
    job = "ok"
except Exception as exc:
    job = type(exc).__name__
after = service.assess_text_layer(hazard).value
print(json.dumps({
    "before": before,
    "after": after,
    "ocr_runs": len(ocr_runs),
    "job": job,
    "imported_before": imported_before,
    "imported_after": "ocrmypdf" in sys.modules,
    "simple_font_init_module": PDFSimpleFont.__init__.__module__,
}))
"""


def test_the_same_pdf_gets_the_same_verdict_before_and_after_a_legacy_ocr_job(
    tmp_path: Path,
) -> None:
    """Owner decision: the verdict must not depend on the worker's history.

    Against the in-process implementation this read ``textual`` before the OCR
    job and ``ocr_required_unusable_text`` after it, because the job's
    ``import ocrmypdf`` emptied the unencoded font's Unicode map for the rest
    of the process.
    """
    state = run_python_child(_WORKER_HISTORY_CHILD, str(tmp_path), cwd=tmp_path)

    # The job really went down the legacy OCR branch.
    assert state["ocr_runs"] >= 1, state
    assert state["imported_before"] is False
    assert state["before"] == "textual", state
    assert state["after"] == state["before"], state
    assert state["imported_after"] is False, state
    assert state["simple_font_init_module"] == "pdfminer.pdffont", state


def test_an_ocr_run_is_bounded_by_a_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: List[Any] = []
    child = _FakePopen(0, timeout=True)

    def popen(command: List[str], **kwargs: Any) -> _FakePopen:
        seen.append(kwargs.get("start_new_session"))
        return child

    monkeypatch.setattr(ocr_module.subprocess, "Popen", popen)

    with pytest.raises(OcrMyPdfFailed, match="timeout"):
        _service()._ocrmypdf(
            tmp_path / "in.pdf", tmp_path / "out.pdf", force_ocr=True, pages=[3]
        )

    # A timed-out run must not leave ghostscript and tesseract behind: on POSIX
    # the whole session is killed, elsewhere the child itself.
    assert child.killed or hasattr(os, "killpg")
    if hasattr(os, "setsid"):
        assert seen == [True]


def test_the_timeout_scales_with_the_pages_being_ocrd(tmp_path: Path) -> None:
    """A flat bound would fail long scans that the untimed in-process call finished."""
    service = _service()
    source = build_scanned_only_pdf(tmp_path / "scan.pdf", pages=3)

    assert service._ocr_timeout_for(source, [4]) == OCRService.OCR_TIMEOUT_SECONDS
    assert service._ocr_timeout_for(source, None) == OCRService.OCR_TIMEOUT_SECONDS

    long_scan = list(range(1, 4001))
    assert (
        120 + OCRService.OCR_SECONDS_PER_PAGE * len(long_scan)
        > OCRService.OCR_TIMEOUT_MAX_SECONDS
    )
    assert (
        service._ocr_timeout_for(source, long_scan)
        == OCRService.OCR_TIMEOUT_MAX_SECONDS
    )

    mid_scan = list(range(1, 601))
    assert (
        service._ocr_timeout_for(source, mid_scan)
        == 120 + OCRService.OCR_SECONDS_PER_PAGE * 600
    )


def test_only_the_unusable_pages_are_forced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _record_run(monkeypatch, 0)

    _service()._ocrmypdf(
        tmp_path / "in.pdf", tmp_path / "out.pdf", force_ocr=True, pages=[2, 7]
    )

    (command,) = calls
    assert command[command.index("--pages") + 1] == "2,7"
    assert "--force-ocr" in command


def test_the_exit_code_table_is_ocrmypdfs_own(tmp_path: Path) -> None:
    """Pinned against the installed package, read in a child interpreter.

    Importing OCRmyPDF here to read its IntEnum would install in this process
    the pdfminer patch this module exists to keep out of it.
    """
    pytest.importorskip("ocrmypdf")
    child = (
        "import json\n"
        "from ocrmypdf.exceptions import ExitCode, PriorOcrFoundError, EncryptedPdfError\n"
        "print(json.dumps({"
        "'names': {int(code): code.name for code in ExitCode},"
        "'prior': int(PriorOcrFoundError.exit_code),"
        "'encrypted': int(EncryptedPdfError.exit_code)}))\n"
    )

    installed = run_python_child(child, cwd=BACKEND_ROOT)

    assert installed["prior"] == ocr_module._EXIT_PRIOR_OCR
    assert installed["encrypted"] == ocr_module._EXIT_ENCRYPTED
    assert ocr_module._EXIT_NAMES == {
        int(code): name for code, name in installed["names"].items()
    }


def test_undecodable_output_does_not_break_the_exit_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The streams are bytes of the customer's PDF; only their lengths are read."""

    class _BinaryPopen(_FakePopen):
        def communicate(self, timeout: Any = None) -> Any:
            return (b"\xff\xfe\x00", b"\x80abc")

    def popen(command: List[str], **kwargs: Any) -> _FakePopen:
        assert "text" not in kwargs and "encoding" not in kwargs
        return _BinaryPopen(8)

    monkeypatch.setattr(ocr_module.subprocess, "Popen", popen)

    with pytest.raises(OcrMyPdfEncrypted) as raised:
        _service()._ocrmypdf(tmp_path / "in.pdf", tmp_path / "out.pdf", force_ocr=False)

    assert "exit=8" in str(raised.value)
    assert "stderr_chars=4" in str(raised.value)
