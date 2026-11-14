#!/usr/bin/env python3
"""Pre-deploy gate: legacy contracts that are missing page content.

Before the withheld-contract-ingest fix, contract ingest ignored the extraction
engine's unresolved and withheld pages. A contract could therefore be
``completed`` and evidence-ready with a page's content missing. The fix holds
new ingests; it does not revisit contracts ingested earlier. The owner accepted
that on one condition: immediately before a production cutover, this census
runs against a fresh ISOLATED copy of production and must find zero affected
contracts.

It reads a ``mongodump --archive --gzip`` stream on stdin and parses it in
memory. Nothing is restored, nothing is written, and no live database is
contacted. It prints counts; with ``--list-ids`` it also prints the ids of
affected contracts (ids only, never page text).

A contract Document (``uploadType == "contract"``) is AFFECTED when it is not
already held (``processing_status != "human_review_required"``) and any of its
``contract_ocr_pages`` rows either has an unresolved status or holds unusable
``(cid:N)`` text in ``raw_text``/``cleaned_text``, or any of its contract
``document_vectors`` chunks contains a ``(cid:N)`` placeholder.

Usage (the archive never leaves the backup host unencrypted on disk here):

    ssh <host> "cat /var/backups/contractdms/mongo/<archive>.archive.gz" \\
        | python scripts/legacy_contract_extraction_census.py [--list-ids]

Exit status: 0 when zero contracts are affected, 3 when any are (STOP the
cutover), 2 when the stream is not a mongodump archive.
"""

from __future__ import annotations

import argparse
import gzip
import re
import struct
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, BinaryIO, Dict, Optional, Tuple

import bson

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from rbac_backend.services.extraction.text_quality import (  # noqa: E402
    assess_native_text_quality,
)

ARCHIVE_MAGIC = 0x8199E26D
TERMINATOR = 0xFFFFFFFF
#: The statuses the extraction run treats as still owing work
#: (models.processing_state._UNRESOLVED_PAGE_STATUSES).
UNRESOLVED = {
    "ocr_failed",
    "ocr_empty",
    "ocr_disabled",
    "ocr_pending",
    "ocr_deferred",
    "unrenderable",
}
CID = re.compile(r"\(cid:\d+\)")


def _read_exact(stream: BinaryIO, size: int) -> Optional[bytes]:
    buffer = b""
    while len(buffer) < size:
        chunk = stream.read(size - len(buffer))
        if not chunk:
            return None
        buffer += chunk
    return buffer


def _read_document(stream: BinaryIO) -> Tuple[Optional[str], Optional[bytes]]:
    head = _read_exact(stream, 4)
    if head is None:
        return None, None
    (size,) = struct.unpack("<I", head)
    if size == TERMINATOR:
        return "terminator", None
    body = _read_exact(stream, size - 4)
    if body is None:
        raise ValueError("archive truncated inside a document")
    return "document", head + body


def census(stream: BinaryIO) -> Dict[str, Any]:
    magic = _read_exact(stream, 4)
    if magic is None or struct.unpack("<I", magic)[0] != ARCHIVE_MAGIC:
        raise ValueError("stdin is not a mongodump archive")

    # Prelude: the archive header, then collection metadata, then a terminator.
    _read_document(stream)
    while True:
        kind, _ = _read_document(stream)
        if kind != "document":
            break

    contracts: Dict[str, Tuple[str, str]] = {}
    pages: Dict[str, Counter] = defaultdict(Counter)
    vector_cid: set = set()
    seen: Counter = Counter()
    namespace: Optional[str] = None

    while True:
        kind, raw = _read_document(stream)
        if kind is None:
            break
        if kind == "terminator":
            namespace = None
            continue
        doc = bson.decode(raw)
        if namespace is None and {"db", "collection", "EOF"} <= set(doc):
            namespace = str(doc["collection"])
            continue
        seen[namespace] += 1
        if namespace == "documents" and doc.get("uploadType") == "contract":
            contracts[str(doc["_id"])] = (
                str(doc.get("status")),
                str(doc.get("processing_status")),
            )
        elif namespace == "contract_ocr_pages":
            entry = pages[str(doc.get("document_id"))]
            entry["rows"] += 1
            if str(doc.get("status")) in UNRESOLVED:
                entry["unresolved"] += 1
            if any(
                (doc.get(field) or "")
                and assess_native_text_quality(doc.get(field) or "").unusable
                for field in ("raw_text", "cleaned_text")
            ):
                entry["unusable"] += 1
        elif namespace == "document_vectors" and doc.get("uploadType") == "contract":
            if CID.search(str(doc.get("text") or "")):
                vector_cid.add(str(doc.get("document_id")))

    affected = sorted(
        document_id
        for document_id, (_, processing_status) in contracts.items()
        if processing_status != "human_review_required"
        and (
            pages[document_id]["unresolved"]
            or pages[document_id]["unusable"]
            or document_id in vector_cid
        )
    )
    return {
        "collections_seen": {
            name: seen[name]
            for name in ("documents", "contract_ocr_pages", "document_vectors")
        },
        "contract_documents": len(contracts),
        "contract_status": dict(Counter(status for status, _ in contracts.values())),
        "contract_processing_status": dict(
            Counter(processing for _, processing in contracts.values())
        ),
        "contract_page_rows": sum(pages[d]["rows"] for d in contracts),
        "unresolved_page_rows": sum(pages[d]["unresolved"] for d in contracts),
        "unusable_cid_page_rows": sum(pages[d]["unusable"] for d in contracts),
        "contracts_with_cid_vectors": len(vector_cid & set(contracts)),
        "affected_contracts": affected,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list-ids", action="store_true", help="print affected contract ids")
    args = parser.parse_args()
    try:
        result = census(gzip.GzipFile(fileobj=sys.stdin.buffer))
    except ValueError as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        return 2
    affected = result.pop("affected_contracts")
    for key, value in result.items():
        print(f"{key} = {value}")
    print(f"affected_legacy_contracts = {len(affected)}")
    if args.list_ids:
        for document_id in affected:
            print(f"affected_contract_id = {document_id}")
    if affected:
        print("VERDICT = STOP CUTOVER: classify these contracts and approve a repair plan")
        return 3
    print("VERDICT = PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
