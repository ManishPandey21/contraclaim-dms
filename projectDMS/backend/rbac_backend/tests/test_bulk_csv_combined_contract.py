"""The bulk-upload CSV contract once both CSV fixes are combined.

``fix/bulk-ocr-enabled-parsing`` (02fcd54) and ``fix/bulk-csv-blank-cells``
(fd2d7be) were built independently and both rewrote ``_validate_csv_row``. The
release candidate resolves them as a semantic union; this module pins what
that union must mean, through both CSV entry points (the bulk route's
read -> normalise -> validate sequence, and ``_parse_and_validate_csv``) and the
validator on its own.

Owner decision 2026-10-02: a blank ``ocr_enabled`` takes the default, ON; an
explicit false is OFF and an explicit true is ON. ``compression_enabled``
follows its product default, OFF. Blank text fields stay null-safe.
"""

from __future__ import annotations

import asyncio
import math
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import pytest

from rbac_backend.tests.test_bulk_csv_blank_cells import (
    _bulk_route_rows,
    _controller,
    _parse_path_rows,
)

PATHS = pytest.mark.parametrize(
    "read_rows", [_bulk_route_rows, _parse_path_rows], ids=["bulk_route", "parse_and_validate"]
)

HEADER = (
    "filename,upload_type,letter_no,date,subject,from,to,tags,sub_tags,"
    "path_structure,status,ocr_enabled,compression_enabled\n"
)


def _row(filename: str, letter_no: str, ocr: str, compression: str, /, **blank: str) -> str:
    cells = {
        "filename": filename,
        "upload_type": "incoming",
        "letter_no": letter_no,
        "date": "2024-01-05",
        "subject": "Subject",
        "from": "Employer",
        "to": "Contractor",
        "tags": "Delay",
        "sub_tags": "EOT",
        "path_structure": "Root/Folder",
        "status": "final",
        "ocr_enabled": ocr,
        "compression_enabled": compression,
    }
    cells.update(blank)
    return ",".join(cells[column] for column in HEADER.strip().split(",")) + "\n"


def _body(*rows: str) -> bytes:
    return (HEADER + "".join(rows)).encode("utf-8")


def _assert_no_null_text(value: Any) -> None:
    if isinstance(value, list):
        for item in value:
            _assert_no_null_text(item)
        return
    null_text = {"nan", "<na>", "none", "nat"}
    assert not (isinstance(value, str) and value.strip().lower() in null_text), value
    assert not (isinstance(value, float) and math.isnan(value)), value
    assert value is not pd.NA


# --- the boolean matrix ---------------------------------------------------------


@PATHS
@pytest.mark.parametrize(
    ("ocr_cell", "expected"),
    [("", True), ("false", False), ("true", True)],
    ids=["blank_default_on", "explicit_false", "explicit_true"],
)
def test_ocr_enabled_matrix(read_rows, ocr_cell: str, expected: bool) -> None:
    # A second row with the other explicit value keeps the column mixed, so the
    # reader cannot hand the validator a uniform bool column by accident.
    other = "true" if ocr_cell == "false" else "false"
    rows = read_rows(_body(_row("a.pdf", "L-1", ocr_cell, ""), _row("b.pdf", "L-2", other, "")))

    assert "_validation_error" not in rows[0], rows[0]
    assert rows[0]["ocr_enabled"] is expected


@PATHS
@pytest.mark.parametrize(
    ("compression_cell", "expected"),
    [("", False), ("false", False), ("true", True)],
    ids=["blank_default_off", "explicit_false", "explicit_true"],
)
def test_compression_enabled_matrix(read_rows, compression_cell: str, expected: bool) -> None:
    other = "true" if compression_cell == "false" else "false"
    rows = read_rows(
        _body(_row("a.pdf", "L-1", "", compression_cell), _row("b.pdf", "L-2", "", other))
    )

    assert "_validation_error" not in rows[0], rows[0]
    assert rows[0]["compression_enabled"] is expected


# --- blank text fields ----------------------------------------------------------


@PATHS
def test_blank_optional_text_fields_are_null_safe(read_rows) -> None:
    blank_row = _row(
        "a.pdf",
        "L-1",
        "",
        "",
        subject="",
        to="",
        tags="",
        sub_tags="",
        path_structure="",
        status="",
        **{"from": ""},
    )
    rows = read_rows(_body(blank_row, _row("b.pdf", "L-2", "false", "true")))
    row = rows[0]

    assert "_validation_error" not in row, row
    assert row["subject"] == ""
    assert row["from_"] is None
    assert row["to"] is None
    assert row["tags"] == []
    assert row["sub_tags"] == []
    assert row["path_structure"] is None
    assert row["status"] == "draft"
    assert (row["ocr_enabled"], row["compression_enabled"]) == (True, False)
    for value in row.values():
        _assert_no_null_text(value)
    # The populated row beside it is untouched by its neighbour's blanks.
    assert (rows[1]["ocr_enabled"], rows[1]["compression_enabled"]) == (False, True)
    assert rows[1]["tags"] == ["Delay"] and rows[1]["sub_tags"] == ["EOT"]


@PATHS
@pytest.mark.parametrize(
    ("blank", "message"),
    [({"filename": ""}, "filename is required"), ({"letter_no": ""}, "letter_no is required")],
    ids=["filename", "letter_no"],
)
def test_blank_required_identity_is_a_validation_failure(
    read_rows, blank: Dict[str, str], message: str
) -> None:
    rows: List[Dict[str, Any]] = read_rows(
        _body(_row("a.pdf", "L-1", "true", ""), _row("b.pdf", "L-2", "", "", **blank))
    )

    assert "_validation_error" not in rows[0]
    assert message in rows[1]["_validation_error"]
    assert rows[1]["filename"] != "nan"


# --- the validator on raw pandas nulls --------------------------------------------


@pytest.mark.parametrize(
    "null", [None, float("nan"), np.nan, pd.NA], ids=["None", "float_nan", "np_nan", "pd_NA"]
)
def test_validator_takes_null_cells_without_crashing_or_inventing_text(null) -> None:
    row = {
        "filename": "a.pdf",
        "upload_type": "incoming",
        "letter_no": "L-1",
        "date": "2024-01-05",
        "subject": null,
        "from_": null,
        "to": null,
        "tags": null,
        "sub_tags": null,
        "path_structure": null,
        "status": null,
        "ocr_enabled": null,
        "compression_enabled": null,
    }

    result = asyncio.run(_controller()._validate_csv_row(row, 1))

    assert result["subject"] == ""
    assert result["from_"] is None and result["to"] is None
    assert result["tags"] == [] and result["sub_tags"] == []
    assert result["path_structure"] is None
    assert result["status"] == "draft"
    assert (result["ocr_enabled"], result["compression_enabled"]) == (True, False)
    for value in result.values():
        _assert_no_null_text(value)
