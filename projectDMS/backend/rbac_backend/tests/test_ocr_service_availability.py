import importlib.util
import sys
import types

from rbac_backend.services.ocr_service import OCRService
from rbac_backend.tests.fixtures.child_python import run_python_child


def test_ocr_availability_requires_external_binaries(monkeypatch):
    fake_ocrmypdf = types.ModuleType("ocrmypdf")
    fake_ocrmypdf.__version__ = "test"
    monkeypatch.setitem(sys.modules, "ocrmypdf", fake_ocrmypdf)

    def fake_which(binary: str) -> str | None:
        if binary in {"qpdf", "tesseract"}:
            return None
        return f"/usr/bin/{binary}"

    monkeypatch.setattr("rbac_backend.services.ocr_service.shutil.which", fake_which)

    service = OCRService.__new__(OCRService)
    assert service._check_ocr_availability() is False


def test_ocr_availability_accepts_complete_runtime(monkeypatch):
    fake_ocrmypdf = types.ModuleType("ocrmypdf")
    fake_ocrmypdf.__version__ = "test"
    monkeypatch.setitem(sys.modules, "ocrmypdf", fake_ocrmypdf)
    monkeypatch.setattr(
        "rbac_backend.services.ocr_service.shutil.which",
        lambda binary: f"/usr/bin/{binary}",
    )

    service = OCRService.__new__(OCRService)
    assert service._check_ocr_availability() is True


def test_ocr_availability_reports_a_missing_package(monkeypatch):
    monkeypatch.delitem(sys.modules, "ocrmypdf", raising=False)
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *args: None if name == "ocrmypdf" else real_find_spec(name, *args),
    )
    monkeypatch.setattr(
        "rbac_backend.services.ocr_service.shutil.which",
        lambda binary: f"/usr/bin/{binary}",
    )

    service = OCRService.__new__(OCRService)
    assert service._check_ocr_availability() is False


# Runs in a fresh interpreter: whether this pytest process has already imported
# OCRmyPDF depends on which tests ran first, so an in-process assertion about
# sys.modules would itself be order-dependent.
_AVAILABILITY_CHILD = r"""
import json, sys
from pdfminer.pdffont import PDFSimpleFont
from pdfminer.psparser import PSBaseParser
from rbac_backend.services.ocr_service import OCRService

service = OCRService.__new__(OCRService)
available = service._check_ocr_availability()
print(json.dumps({
    "available": available,
    "ocrmypdf_imported": "ocrmypdf" in sys.modules,
    "simple_font_init_module": PDFSimpleFont.__init__.__module__,
    "bufsiz": PSBaseParser.BUFSIZ,
}))
"""


def test_availability_check_does_not_import_ocrmypdf(tmp_path):
    """Asking whether OCRmyPDF is installed must not install its pdfminer patch.

    Importing ``ocrmypdf`` rewrites ``PDFSimpleFont.__init__`` and
    ``PSBaseParser.BUFSIZ`` for the whole process. The check runs on every
    ``OCRService`` construction - every document-controller request - so an
    import here patched every backend worker that served one.
    """
    state = run_python_child(_AVAILABILITY_CHILD, cwd=tmp_path)

    assert state["ocrmypdf_imported"] is False
    assert state["simple_font_init_module"] == "pdfminer.pdffont"
    assert state["bufsiz"] == 4096
