"""Bulk CSV boolean columns must mean what the cell says.

``_validate_csv_row`` used ``row.get('ocr_enabled') or ... or 'true'``. pandas
reads ``false`` as ``False`` and ``0`` as ``0`` - both falsy - so the ``or``
chain replaced them with ``'true'``: a bulk row could never turn OCR off, and
every bulk row was queued for extraction. A blank cell arrives as ``NaN``,
which is truthy, so it skipped the ``'true'`` default and silently became
``False``. These tests drive the real CSV reader and column normaliser, so the
cell values have the dtypes pandas really produces.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from fastapi import UploadFile

from rbac_backend.routers.documents import DocumentController
from rbac_backend.services.bulk_upload_service import BulkUploadService


def _controller() -> DocumentController:
    return DocumentController(
        document_service=SimpleNamespace(),
        file_service=SimpleNamespace(),
        export_service=SimpleNamespace(),
        auth_service=SimpleNamespace(),
        bulk_upload_service=BulkUploadService(),
    )


def _rows(csv_text: str) -> List[Dict[str, Any]]:
    service = BulkUploadService()
    df = service.read_csv_from_upload_file(
        UploadFile(filename="rows.csv", file=BytesIO(csv_text.encode("utf-8")))
    )
    df = service.normalize_csv_dataframe(df)
    return [row.to_dict() for _idx, row in df.iterrows()]


def _validated(csv_text: str) -> List[Dict[str, Any]]:
    controller = _controller()
    return [
        asyncio.run(controller._validate_csv_row(row, index + 1))
        for index, row in enumerate(_rows(csv_text))
    ]


def _csv(column: str, values: List[str], extra: str = "") -> str:
    header = f"filename,upload_type,letter_no,date,{column}{extra}"
    lines = [
        f"f{i}.pdf,incoming,L/{i},2026-08-01,{value}" + ("," if extra else "")
        for i, value in enumerate(values)
    ]
    return "\n".join([header, *lines]) + "\n"


@pytest.mark.parametrize("column", ["ocr_enabled", "ocrEnabled", "OCR"])
@pytest.mark.parametrize(
    "value,expected",
    [
        ("false", False), ("False", False), ("FALSE", False), ("no", False),
        ("n", False), ("off", False), ("f", False), ("0", False),
        ("true", True), ("True", True), ("yes", True), ("y", True),
        ("on", True), ("t", True), ("1", True),
    ],
)
def test_an_explicit_ocr_value_is_honoured(column: str, value: str, expected: bool) -> None:
    # A column of one value: pandas gives bool for true/false, int64 for 0/1.
    [row] = _validated(_csv(column, [value]))
    assert row["ocr_enabled"] is expected


def test_mixed_and_blank_ocr_cells_in_one_file() -> None:
    rows = _validated(_csv("ocr_enabled", ["false", "true", "", "0", "1"]))
    # Blank means "not specified": the same default as a single upload (on).
    assert [row["ocr_enabled"] for row in rows] == [False, True, True, False, True]


def test_numeric_column_with_a_blank_cell() -> None:
    # pandas makes this column float64 with NaN: 1.0, NaN, 0.0.
    rows = _validated(_csv("ocr_enabled", ["1", "", "0"]))
    assert [row["ocr_enabled"] for row in rows] == [True, True, False]


def test_an_unrecognised_ocr_value_is_a_row_error_not_a_guess() -> None:
    with pytest.raises(ValueError, match="ocr_enabled"):
        _validated(_csv("ocr_enabled", ["maybe"]))
    with pytest.raises(ValueError, match="ocr_enabled"):
        _validated(_csv("ocr_enabled", ["2"]))


@pytest.mark.parametrize(
    "value,expected",
    [("true", True), ("1", True), ("false", False), ("0", False), ("", False)],
)
def test_compression_flag_is_honoured_and_defaults_off(value: str, expected: bool) -> None:
    [row] = _validated(_csv("ocr_enabled", ["true"], extra=",compression_enabled").replace(
        "2026-08-01,true,", f"2026-08-01,true,{value}"
    ))
    assert row["compression_enabled"] is expected


def test_a_false_row_reaches_create_document_as_false() -> None:
    """The flag create_document uses to decide whether to queue extraction."""
    controller = _controller()
    captured: Dict[str, Any] = {}

    async def fake_create_document(**kwargs: Any) -> SimpleNamespace:
        captured.update(kwargs)
        return SimpleNamespace(id="doc-1")

    controller.create_document = fake_create_document  # type: ignore[method-assign]
    [row] = _validated(_csv("ocr_enabled", ["false"]))
    result = asyncio.run(
        controller._process_single_file(
            {**row, "date": datetime.now(timezone.utc)},
            {"f0.pdf": UploadFile(filename="f0.pdf", file=BytesIO(b"%PDF-1.4"))},
            "org-1",
            "proj-1",
            SimpleNamespace(id="uploader"),
        )
    )
    assert result.success is True
    assert captured["ocr_enabled"] is False
