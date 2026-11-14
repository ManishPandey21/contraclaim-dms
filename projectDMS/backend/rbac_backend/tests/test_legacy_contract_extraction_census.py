"""The pre-deploy legacy-contract census must find what it hunts for.

A census that reads zero on real data proves nothing unless it reads non-zero
when an affected contract is planted. These build a small mongodump archive in
memory in the real archive layout and assert the script's verdict both ways.
"""

from __future__ import annotations

import gzip
import importlib.util
import io
import struct
from pathlib import Path
from typing import Any, Dict, List, Tuple

import bson

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "legacy_contract_extraction_census.py"
_spec = importlib.util.spec_from_file_location("_legacy_census", SCRIPT)
assert _spec is not None and _spec.loader is not None
census_module: Any = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(census_module)

TERMINATOR = struct.pack("<I", 0xFFFFFFFF)
CID_TEXT = " ".join(f"(cid:{n})" for n in range(40))


def _archive(collections: List[Tuple[str, List[Dict[str, Any]]]]) -> io.BytesIO:
    raw = struct.pack("<I", census_module.ARCHIVE_MAGIC)
    raw += bson.encode({"concurrent_collections": 1, "version": "0.1"})
    for name, _ in collections:
        raw += bson.encode({"db": "contraclaim", "collection": name, "metadata": "{}"})
    raw += TERMINATOR
    for name, documents in collections:
        header = bson.encode({"db": "contraclaim", "collection": name, "EOF": False, "CRC": 0})
        raw += header
        for document in documents:
            raw += bson.encode(document)
        raw += TERMINATOR
    return io.BytesIO(gzip.compress(raw))


def _run(collections: List[Tuple[str, List[Dict[str, Any]]]]) -> Dict[str, Any]:
    return census_module.census(gzip.GzipFile(fileobj=_archive(collections)))


def _contract(document_id: str, processing_status: Any = None) -> Dict[str, Any]:
    return {
        "_id": document_id,
        "uploadType": "contract",
        "status": "completed",
        "processing_status": processing_status,
    }


def _page(document_id: str, number: int, status: str = "text_layer", text: str = "clause text") -> Dict[str, Any]:
    return {
        "document_id": document_id,
        "page_number": number,
        "status": status,
        "raw_text": text,
        "cleaned_text": text,
    }


def test_clean_contracts_pass() -> None:
    result = _run(
        [
            ("documents", [_contract("c1"), {"_id": "letter", "uploadType": "incoming"}]),
            ("contract_ocr_pages", [_page("c1", 1), _page("c1", 2, "ocr_completed")]),
            ("document_vectors", [{"document_id": "c1", "uploadType": "contract", "text": "ok"}]),
        ]
    )
    assert result["contract_documents"] == 1
    assert result["contract_page_rows"] == 2
    assert result["affected_contracts"] == []


def test_each_legacy_loss_shape_is_found_and_a_held_contract_is_not() -> None:
    result = _run(
        [
            (
                "documents",
                [
                    _contract("unresolved"),
                    _contract("cid_row"),
                    _contract("cid_vector"),
                    _contract("already_held", "human_review_required"),
                    _contract("clean"),
                ],
            ),
            (
                "contract_ocr_pages",
                [
                    _page("unresolved", 1),
                    _page("unresolved", 2, "ocr_failed", ""),
                    _page("cid_row", 1, "text_layer", CID_TEXT),
                    _page("cid_vector", 1),
                    _page("already_held", 1, "ocr_pending", ""),
                    _page("clean", 1),
                ],
            ),
            (
                "document_vectors",
                [{"document_id": "cid_vector", "uploadType": "contract", "text": "a (cid:3) b"}],
            ),
        ]
    )
    assert result["affected_contracts"] == ["cid_row", "cid_vector", "unresolved"]
    assert result["unresolved_page_rows"] == 2
    assert result["unusable_cid_page_rows"] == 1


def test_a_stream_that_is_not_an_archive_is_refused() -> None:
    import pytest

    with pytest.raises(ValueError):
        census_module.census(gzip.GzipFile(fileobj=io.BytesIO(gzip.compress(b"not an archive"))))
