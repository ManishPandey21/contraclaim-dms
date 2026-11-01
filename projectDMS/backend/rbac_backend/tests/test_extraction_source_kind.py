"""Route an upload to the extractor that can actually read it."""

from __future__ import annotations

import pytest

from rbac_backend.services.extraction.models import SourceKind
from rbac_backend.services.extraction.source_kind import ARCHIVE_MIMES, route


@pytest.mark.parametrize(
    ("mime", "expected"),
    [
        ("application/pdf", SourceKind.PDF),
        ("image/png", SourceKind.IMAGE),
        ("image/jpeg", SourceKind.IMAGE),
        ("text/plain", SourceKind.TEXT),
        ("application/zip", SourceKind.ARCHIVE),
        ("application/vnd.rar", SourceKind.ARCHIVE),
        ("application/octet-stream", SourceKind.UNSUPPORTED),
        ("application/msword", SourceKind.UNSUPPORTED),
    ],
)
def test_mime_routing(mime: str, expected: SourceKind) -> None:
    assert route(mime) is expected


def test_missing_mime_is_unsupported() -> None:
    assert route(None) is SourceKind.UNSUPPORTED
    assert route("") is SourceKind.UNSUPPORTED


def test_mime_wins_over_a_misleading_extension() -> None:
    # A .pdf-named file that sniffed as PNG is an image, not a PDF. Trusting
    # the name here is how a mislabelled upload reaches the wrong extractor.
    assert route("image/png", "invoice.pdf") is SourceKind.IMAGE


def test_archive_mimes_cover_zip_and_rar() -> None:
    assert ARCHIVE_MIMES == frozenset({"application/zip", "application/vnd.rar"})


def test_case_and_parameters_are_tolerated() -> None:
    assert route("APPLICATION/PDF") is SourceKind.PDF
    assert route("text/plain; charset=utf-8") is SourceKind.TEXT


def test_surrounding_whitespace_is_tolerated() -> None:
    assert route("  application/pdf  ") is SourceKind.PDF


def test_docx_is_unsupported_by_the_general_path() -> None:
    # DOCX is a contract-path format; the general path has no extractor for it
    # and must say so rather than route it to the PDF reader.
    assert (
        route(
            "application/vnd.openxmlformats-officedocument"
            ".wordprocessingml.document"
        )
        is SourceKind.UNSUPPORTED
    )


def test_router_class_and_module_alias_agree() -> None:
    from rbac_backend.services.extraction.source_kind import SourceKindRouter

    assert SourceKindRouter.route("application/pdf") is route("application/pdf")
