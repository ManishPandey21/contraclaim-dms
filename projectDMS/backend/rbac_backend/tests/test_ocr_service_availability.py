import sys
import types

from rbac_backend.services.ocr_service import OCRService


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
