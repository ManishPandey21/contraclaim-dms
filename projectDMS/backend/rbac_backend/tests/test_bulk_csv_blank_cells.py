"""A blank bulk-CSV cell is blank, not the literal text ``"nan"``.

pandas reads an empty cell as ``NaN``. Two things turned that into user data:

* ``BulkUploadService.clean_dataframe`` ran ``astype(str)`` over every object
  column, so each blank cell became the three-character string ``"nan"`` before
  anything downstream could see that it was missing.
* ``DocumentController._validate_csv_row`` stringified cells without asking
  whether they were null, and chose between aliases with ``or`` - but
  ``bool(float("nan"))`` is ``True``, so a ``NaN`` won every ``or`` chain and was
  then stringified to ``"nan"``. That path is reached with raw ``NaN`` by
  ``_parse_and_validate_csv``, which reads the CSV itself and never cleans it.

The result was a persisted subject, sender, recipient, folder path, tag list or
status of ``"nan"``, and a blank *required* filename or letter number that
passed its "is required" check because ``"nan"`` is not empty.

Every test drives the real reader and the real validator. The CSV always has a
populated value next to each blank one in the same column, so pandas infers the
mixed object columns a real spreadsheet export produces rather than an
all-empty float column.
"""

from __future__ import annotations

import asyncio
import io
import math
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, List

import numpy as np
import pandas as pd
import pytest
from fastapi import UploadFile

from rbac_backend.routers.documents import DocumentController
from rbac_backend.services.bulk_upload_service import BulkUploadService

HEADER = (
    "filename,upload_type,letter_no,date,subject,{from_header},to,tags,sub_tags,"
    "path_structure,path_structure1,status,ocr_enabled,compression_enabled\n"
)

ROWS = (
    # Every optional column populated, with padding and a quoted comma list.
    'a.pdf,incoming,L-1,2024-01-05,  Subject ABC  ,Employer,Contractor,'
    '"Employer, Contractor","Sub A, Sub B",Root/Folder,Root/Sub,final,true,true\n'
    # Every optional column blank.
    "b.pdf,outgoing,L-2,2024-01-06,,,,,,,,,,\n"
    # Blank and populated optional columns interleaved in one row.
    "c.pdf,incoming,L-3,2024-01-07,Only subject,,Contractor,,Sub C,,Root/Only,,false,\n"
)

BLANK_ROW = 1  # zero-based index of b.pdf

NULL_TEXT = {"nan", "NaN", "<NA>", "None", "NaT"}


def _csv(from_header: str = "from") -> bytes:
    return (HEADER.format(from_header=from_header) + ROWS).encode("utf-8")


def _controller() -> DocumentController:
    # The CSV methods under test touch only `bulk_upload_service`.
    unused: Any = SimpleNamespace()
    return DocumentController(
        document_service=unused,
        file_service=unused,
        export_service=unused,
        auth_service=unused,
        bulk_upload_service=BulkUploadService(),
    )


def _upload(body: bytes) -> UploadFile:
    return UploadFile(file=io.BytesIO(body), filename="metadata.csv")


def _bulk_route_rows(body: bytes) -> List[Dict[str, Any]]:
    """The sequence `bulk_upload_documents` runs, step for step."""
    controller = _controller()
    df = controller.bulk_upload_service.read_csv_from_upload_file(_upload(body))
    df = controller.bulk_upload_service.normalize_csv_dataframe(df)
    rows = []
    for idx, row in df.iterrows():
        row_dict = row.to_dict()
        try:
            rows.append(asyncio.run(controller._validate_csv_row(row_dict, idx + 1)))
        except Exception as exc:  # the route records the refusal on the row
            rows.append(controller._csv_refused_row(row_dict, str(exc), idx + 1))
    return rows


def _parse_path_rows(body: bytes) -> List[Dict[str, Any]]:
    return asyncio.run(_controller()._parse_and_validate_csv(body))


PATHS = pytest.mark.parametrize(
    "read_rows", [_bulk_route_rows, _parse_path_rows], ids=["bulk_route", "parse_and_validate"]
)


def _assert_no_null_text(value: Any, where: str) -> None:
    if isinstance(value, list):
        for item in value:
            _assert_no_null_text(item, where)
        return
    assert not (isinstance(value, str) and value.strip() in NULL_TEXT), (
        f"{where}: blank CSV cell surfaced as {value!r}"
    )
    assert not (isinstance(value, float) and math.isnan(value)), f"{where}: raw NaN leaked"


# --------------------------------------------------------------------------- #
# A. The reader keeps a blank cell null
# --------------------------------------------------------------------------- #


def test_reader_keeps_blank_cells_null_in_mixed_object_columns() -> None:
    df = BulkUploadService().read_csv_from_upload_file(_upload(_csv()))

    for column in ("subject", "from_", "to", "tags", "sub_tags", "path_structure", "status"):
        assert df[column].dtype == object, column
        assert pd.isna(df.loc[BLANK_ROW, column]), (
            f"{column}: blank cell read as {df.loc[BLANK_ROW, column]!r}"
        )
        assert not df[column].isin(NULL_TEXT).any(), column


def test_reader_still_cleans_populated_cells_exactly_as_before() -> None:
    body = (
        "filename,upload_type,letter_no,date,subject\n"
        "a.pdf,incoming,L-1,2024-01-05,\"  A B� \x96 C \x97 D  \"\n"
        "b.pdf,incoming,L-2,2024-01-06,\n"
    ).encode("utf-8")

    df = BulkUploadService().read_csv_from_upload_file(_upload(body))

    assert df.loc[0, "subject"] == "A B - C -- D"
    assert pd.isna(df.loc[1, "subject"])


def test_reader_does_not_stringify_numeric_columns() -> None:
    body = (
        "filename,upload_type,letter_no,date,amount\n"
        "a.pdf,incoming,L-1,2024-01-05,10\n"
        "b.pdf,incoming,L-2,2024-01-06,\n"
    ).encode("utf-8")

    df = BulkUploadService().read_csv_from_upload_file(_upload(body))

    assert df["amount"].dtype.kind == "f"
    assert df.loc[0, "amount"] == 10
    assert pd.isna(df.loc[1, "amount"])


def test_normalize_is_idempotent_on_reader_output() -> None:
    service = BulkUploadService()
    df = service.read_csv_from_upload_file(_upload(_csv()))
    columns = list(df.columns)

    assert list(service.normalize_csv_dataframe(df).columns) == columns
    assert "from_" in columns and "from" not in columns


# --------------------------------------------------------------------------- #
# B-H, L, N, O. Blank optional fields, through both CSV entry points
# --------------------------------------------------------------------------- #


@PATHS
@pytest.mark.parametrize("from_header", ["from", "from_"])
def test_blank_optional_fields_are_blank_not_nan(read_rows, from_header) -> None:
    rows = read_rows(_csv(from_header))
    blank = rows[BLANK_ROW]

    assert "_validation_error" not in blank, blank.get("_validation_error")
    assert blank["subject"] == ""
    assert blank["from_"] is None
    assert blank["to"] is None
    assert blank["tags"] == []
    assert blank["sub_tags"] == []
    assert blank["path_structure"] is None
    assert blank["path_structure1"] is None
    assert blank["status"] == "draft"


@PATHS
def test_mixed_rows_keep_blank_and_populated_values_apart(read_rows) -> None:
    rows = read_rows(_csv())
    mixed = rows[2]

    assert mixed["subject"] == "Only subject"
    assert mixed["from_"] is None
    assert mixed["to"] == "Contractor"
    assert mixed["tags"] == []
    assert mixed["sub_tags"] == ["Sub C"]
    assert mixed["path_structure"] is None
    assert mixed["path_structure1"] == "Root/Only"
    assert mixed["status"] == "draft"


@PATHS
def test_no_row_carries_null_text_in_any_field(read_rows) -> None:
    for row in read_rows(_csv()):
        for key, value in row.items():
            if key.startswith("_"):
                continue
            _assert_no_null_text(value, f"row {row['_row_number']} {key}")


# --------------------------------------------------------------------------- #
# I. Populated values are unchanged
# --------------------------------------------------------------------------- #


@PATHS
def test_populated_values_are_unchanged(read_rows) -> None:
    full = read_rows(_csv())[0]

    assert full == {
        "filename": "a.pdf",
        "upload_type": "incoming",
        "letter_no": "L-1",
        "date": full["date"],
        "subject": "Subject ABC",
        "from_": "Employer",
        "to": "Contractor",
        "tags": ["Employer", "Contractor"],
        "sub_tags": ["Sub A", "Sub B"],
        "status": "final",
        "ocr_enabled": True,
        "compression_enabled": True,
        "path_structure": "Root/Folder",
        "path_structure1": "Root/Sub",
        "_row_number": 1,
    }
    assert isinstance(full["date"], datetime)
    assert (full["date"].year, full["date"].month, full["date"].day) == (2024, 1, 5)


def test_special_characters_and_quoted_commas_survive() -> None:
    body = (
        "filename,upload_type,letter_no,date,subject,to,path_structure,ocr_enabled\n"
        'a.pdf,incoming,L/1-A,2024-01-05,"Re: ""Delay"" & costs, 5%",O\'Neil & Co. (#2),A/B C/D,true\n'
        "b.pdf,incoming,L-2,2024-01-06,,,,true\n"
    ).encode("utf-8")

    for rows in (_bulk_route_rows(body), _parse_path_rows(body)):
        assert rows[0]["subject"] == 'Re: "Delay" & costs, 5%'
        assert rows[0]["to"] == "O'Neil & Co. (#2)"
        assert rows[0]["letter_no"] == "L/1-A"
        assert rows[0]["path_structure"] == "A/B C/D"


# --------------------------------------------------------------------------- #
# The validator on its own: every null-like value an optional field can hold
# --------------------------------------------------------------------------- #


NULLS = [None, float("nan"), np.nan, pd.NA, ""]


@pytest.mark.parametrize("null", NULLS, ids=["None", "float_nan", "np_nan", "pd_NA", "empty"])
def test_validator_treats_every_null_like_optional_value_as_blank(null) -> None:
    row = {
        "filename": "a.pdf",
        "upload_type": "incoming",
        "letter_no": "L-1",
        "date": "2024-01-05",
        "subject": null,
        "from_": null,
        "from": null,
        "to": null,
        "tags": null,
        "sub_tags": null,
        "subTags": null,
        "path_structure": null,
        "pathStructure": null,
        "path_structure1": null,
        "pathStructure1": null,
    }

    result = asyncio.run(_controller()._validate_csv_row(row, 1))

    assert result["subject"] == ""
    assert result["from_"] is None
    assert result["to"] is None
    assert result["tags"] == []
    assert result["sub_tags"] == []
    assert result["path_structure"] is None
    assert result["path_structure1"] is None


@pytest.mark.parametrize("null", [None, float("nan"), pd.NA], ids=["None", "nan", "pd_NA"])
def test_validator_blank_status_falls_back_to_the_absent_default(null) -> None:
    row = {
        "filename": "a.pdf",
        "upload_type": "incoming",
        "letter_no": "L-1",
        "date": "2024-01-05",
        "status": null,
    }

    assert asyncio.run(_controller()._validate_csv_row(row, 1))["status"] == "draft"


def test_validator_null_primary_alias_falls_through_to_the_secondary() -> None:
    row = {
        "filename": "a.pdf",
        "upload_type": float("nan"),
        "uploadType": "outgoing",
        "letter_no": float("nan"),
        "letterNo": "L-9",
        "date": "2024-01-05",
        "from_": float("nan"),
        "from": "Employer",
        "sub_tags": float("nan"),
        "subTags": "X, Y",
        "path_structure": float("nan"),
        "pathStructure": "Root/Alias",
    }

    result = asyncio.run(_controller()._validate_csv_row(row, 1))

    assert result["upload_type"] == "outgoing"
    assert result["letter_no"] == "L-9"
    assert result["from_"] == "Employer"
    assert result["sub_tags"] == ["X", "Y"]
    assert result["path_structure"] == "Root/Alias"


# --------------------------------------------------------------------------- #
# P. Required fields: a blank required cell is refused, not accepted as "nan"
# --------------------------------------------------------------------------- #


@PATHS
@pytest.mark.parametrize(
    ("blank_column", "message"),
    [
        ("filename", "filename is required"),
        ("upload_type", "upload_type must be 'incoming' or 'outgoing'"),
        ("letter_no", "letter_no is required"),
        ("date", "date is required"),
    ],
)
def test_blank_required_cell_is_refused(read_rows, blank_column, message) -> None:
    columns = ["filename", "upload_type", "letter_no", "date", "ocr_enabled"]
    good = {"filename": "a.pdf", "upload_type": "incoming", "letter_no": "L-1",
            "date": "2024-01-05", "ocr_enabled": "true"}
    second = {**good, "filename": "b.pdf", "letter_no": "L-2"}
    second[blank_column] = ""
    body = (
        ",".join(columns) + "\n"
        + ",".join(good[c] for c in columns) + "\n"
        + ",".join(second[c] for c in columns) + "\n"
    ).encode("utf-8")

    rows = read_rows(body)

    assert "_validation_error" not in rows[0]
    assert message in rows[1]["_validation_error"]


@PATHS
def test_invalid_populated_required_values_are_still_refused(read_rows) -> None:
    body = (
        "filename,upload_type,letter_no,date,ocr_enabled\n"
        "a.pdf,sideways,L-1,2024-01-05,true\n"
        "b.pdf,incoming,L-2,not-a-date,true\n"
    ).encode("utf-8")

    rows = read_rows(body)

    assert "upload_type must be" in rows[0]["_validation_error"]
    # `parse_date_safely` raises its own message before the router's
    # "invalid date format" branch is reached; that is the existing contract.
    assert "Unsupported date format: 'not-a-date'" in rows[1]["_validation_error"]


# --------------------------------------------------------------------------- #
# Q. Malformed CSV behaviour is unchanged
# --------------------------------------------------------------------------- #


def test_parse_path_missing_columns_is_still_a_400() -> None:
    from rbac_backend.utils.error_handler import BaseDomainError

    with pytest.raises(BaseDomainError) as excinfo:
        _parse_path_rows(b"filename,subject\na.pdf,\n")
    assert "Missing required columns" in str(excinfo.value)
    assert excinfo.value.http_status == 400


def test_parse_path_empty_csv_is_still_a_400() -> None:
    from rbac_backend.utils.error_handler import BaseDomainError

    with pytest.raises(BaseDomainError) as excinfo:
        _parse_path_rows(b"")
    assert excinfo.value.http_status == 400


def test_reader_header_only_csv_is_still_a_processing_failure() -> None:
    with pytest.raises(ValueError, match="CSV processing failed"):
        BulkUploadService().read_csv_from_upload_file(_upload(b"filename,subject\n"))


def test_semicolon_delimited_csv_still_parses_with_blanks() -> None:
    body = (
        "filename;upload_type;letter_no;date;subject;tags;ocr_enabled\n"
        "a.pdf;incoming;L-1;2024-01-05;S;T1, T2;true\n"
        "b.pdf;incoming;L-2;2024-01-06;;;true\n"
    ).encode("utf-8")

    for rows in (_bulk_route_rows(body), _parse_path_rows(body)):
        assert rows[0]["subject"] == "S" and rows[0]["tags"] == ["T1", "T2"]
        assert rows[1]["subject"] == "" and rows[1]["tags"] == []


# --------------------------------------------------------------------------- #
# Boolean columns: the combined release semantics
# --------------------------------------------------------------------------- #


#: (ocr_enabled, compression_enabled) per row. This ticket pinned the base's
#: values - blank read as False, and the parse path turned row 3's explicit
#: "false" into True - and left the flags to `fix/bulk-ocr-enabled-parsing`.
#: Combined with that fix, the owner decision (2026-10-02) is: a blank flag
#: takes its documented default (ocr_enabled ON, compression_enabled OFF), an
#: explicit value is honoured, and both paths agree.
BOOLEANS_COMBINED = [(True, True), (True, False), (False, False)]


@PATHS
def test_boolean_columns_follow_the_combined_contract(read_rows) -> None:
    rows = read_rows(_csv())

    assert [(r["ocr_enabled"], r["compression_enabled"]) for r in rows] == BOOLEANS_COMBINED


def test_reader_leaves_populated_boolean_cells_as_the_strings_they_were() -> None:
    """A mixed flag column keeps ``"False"`` as text, never the bool ``False``.

    ``_validate_csv_row`` selects ``ocr_enabled`` with an ``or`` chain whose
    last operand is ``'true'``; a real ``False`` would fall through it and turn
    into ``True``. The old reader stringified every cell, which is what kept a
    ``false`` in a column that also has blanks from flipping. Preserving null
    must not remove that.
    """
    df = BulkUploadService().read_csv_from_upload_file(_upload(_csv()))

    assert list(df["ocr_enabled"].iloc[[0, 2]]) == ["True", "False"]
    assert pd.isna(df.loc[BLANK_ROW, "ocr_enabled"])


# --------------------------------------------------------------------------- #
# The parser, documented: literal null tokens are indistinguishable from blank
# --------------------------------------------------------------------------- #


def test_literal_null_tokens_are_read_as_missing_by_the_parser() -> None:
    """pandas' default ``na_values`` include ``nan``, ``None``, ``NA`` and ``N/A``.

    A cell containing one of those words is therefore missing before any
    project code runs, and the reader cannot tell it from an empty cell. This
    pins that existing parser behaviour; keeping such words as text would mean
    changing ``read_csv``'s NA configuration for every bulk column, which is a
    separate decision.
    """
    body = (
        "filename,upload_type,letter_no,date,subject,ocr_enabled\n"
        "a.pdf,incoming,L-1,2024-01-05,real subject,true\n"
        "b.pdf,incoming,L-2,2024-01-06,nan,true\n"
        "c.pdf,incoming,L-3,2024-01-07,None,true\n"
    ).encode("utf-8")

    for rows in (_bulk_route_rows(body), _parse_path_rows(body)):
        assert [r["subject"] for r in rows] == ["real subject", "", ""]


# --------------------------------------------------------------------------- #
# A refused row reaches the background job with a usable filename
# --------------------------------------------------------------------------- #


class _RecordingJobs:
    """The three job-state calls `_process_bulk_upload` makes, recorded."""

    def __init__(self) -> None:
        self.completed: List[Any] = []
        self.failed: List[str] = []

    async def update_progress(self, *args: Any) -> None:
        return None

    async def complete_job(self, job_id, status, successful, failed, results) -> None:
        self.completed.append((status, successful, failed, results))

    async def fail_job(self, job_id: str, error_message: str) -> None:
        self.failed.append(error_message)


@pytest.mark.parametrize(
    "body",
    [
        # One blank filename among populated ones: an object column.
        b"filename,upload_type,letter_no,date,ocr_enabled\n"
        b",incoming,L-1,2024-01-05,true\n"
        b"b.pdf,incoming,L-2,2024-01-06,true\n",
        # Every filename blank: pandas reads a float64 column of NaN.
        b"filename,upload_type,letter_no,date,ocr_enabled\n"
        b",incoming,L-1,2024-01-05,true\n"
        b",incoming,L-2,2024-01-06,true\n",
    ],
    ids=["one_blank_filename", "all_blank_filenames"],
)
def test_refused_blank_filename_row_fails_alone_not_the_whole_job(body) -> None:
    """Review finding: the refused row kept its raw NaN ``filename``.

    ``DocumentProcessingResult.filename`` is ``str``, so building the per-row
    failure raised, the per-row handler raised again building its own, and the
    exception reached ``fail_job``: the whole job failed and no later row was
    processed. A refused row must fail on its own, with its refusal as the error.
    """
    controller = _controller()
    jobs = _RecordingJobs()
    rows = _bulk_route_rows(body)
    controller.bulk_upload_service = jobs  # type: ignore[assignment]

    asyncio.run(controller._process_bulk_upload("job-1", rows, [], "org", "proj", None))  # type: ignore[arg-type]

    assert jobs.failed == []
    assert len(jobs.completed) == 1
    status, successful, failed, results = jobs.completed[0]
    assert status == "completed_with_errors"
    assert failed == len(rows) and successful == 0
    first = results[0]
    assert first.filename == ""
    assert first.success is False
    assert "filename is required" in first.error
    assert first.row_number == 1


def test_single_file_result_never_carries_a_null_filename() -> None:
    result = asyncio.run(
        _controller()._process_single_file(
            {"filename": float("nan"), "_validation_error": "Row 3: filename is required", "_row_number": 3},
            {},
            "org",
            "proj",
            None,  # type: ignore[arg-type]
        )
    )

    assert result.filename == ""
    assert result.success is False
    assert result.error == "Row 3: filename is required"
    assert result.row_number == 3
