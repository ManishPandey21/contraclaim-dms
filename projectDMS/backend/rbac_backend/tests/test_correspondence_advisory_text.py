"""AI reply advice and interpretation must not travel as correspondence evidence.

Closure review M1/M2: the report's "Key Reply Points" item arrives in several
Markdown shapes, a stored row boundary can split it, and the interpretive
fields (``alleged_responsibility``, ``linked_event_suggested``) are the
model's reading of a letter rather than a description of it.
"""

from __future__ import annotations

import re

import pytest
from bson import ObjectId

from rbac_backend.retrieval.correspondence_payload import (
    ADVISORY_FIELDS,
    CORRESPONDENCE_PAYLOAD_SCHEMA_VERSION,
    DESCRIPTIVE_FIELDS,
    INTERPRETIVE_FIELDS,
    PAYLOAD_SCHEMA_VERSION_FIELD,
    CorrespondencePayloadError,
    _REPORT_ITEM,
    assert_canonical_correspondence_payload,
    build_correspondence_chunks,
    has_advisory_report_item,
    refuse_report_derived_text,
    rows_need_reprocess,
)

ADVICE = "ADVISORY-REPLY-POINT"
HEADER = (
    "1) Date: 01-08-2026\n"
    "2) Letter No: RPT/001\n"
    "5) Subject: Delay to viaduct pier foundations\n"
    "22) Summary: Notice of delay.\n"
)
FULL = "25) Full Content: The contractor gives notice of delay to the viaduct piers.\n"
SUBLIST = f"1) Deny liability: {ADVICE} one\n2) Ask for particulars: {ADVICE} two\n"


@pytest.mark.parametrize(
    "heading",
    [
        "24) Key Reply Points - Points to be Addressed While Responding: ",
        "**24) Key Reply Points**: ",
        "24. Key Reply Points: ",
        "### 24) Key Reply Points\n",
        "- **24. Key Reply Points:** ",
        "__24) Key Reply Points__ \u2014 ",
        "24) Key Reply Points (Points to be Addressed While Responding): ",
        "(24) Key Reply Points: ",
        "24 - Key Reply Points: ",
        "24) Key Reply Point: ",
        "24) Points to be Addressed While Responding: ",
        "24) Key Reply Points / Points to be Addressed While Responding: ",
        "24) Key Reply Points & Points to be Addressed: ",
        "| 24) Key Reply Points | ",
        "24\\) Key Reply Points: ",
        "Points to be Addressed: ",
        "Key Reply Points: ",
        "**Key Reply Points**\n",
        "### Key Reply Points\n",
    ],
)
def test_every_report_heading_shape_is_detected_and_refused(heading: str) -> None:
    report = HEADER + heading + f"{ADVICE} reserve rights\n" + SUBLIST + FULL
    assert has_advisory_report_item(report)
    with pytest.raises(CorrespondencePayloadError) as refused:
        refuse_report_derived_text("doc-1", report)
    assert ADVICE not in str(refused.value)


def test_an_ordinary_letter_mentioning_reply_points_is_not_refused() -> None:
    letters = [
        "Dear Sir,\nKey reply points were discussed at the meeting.\nRegards",
        "Dear Sir,\nKey reply points:\n1) Deny liability: we disagree\nRegards",
        "Please address the key reply points raised in our letter of 3 May.",
        "To, The Engineer\nSubject: Delay\nDate: 1 May\nKey reply points were noted.",
        "Minutes of meeting\n5. Points to be addressed:\n- site access\n- drawings",
        "Minutes\n5) Points to be addressed - site access\n6. Action by: Engineer",
        "Agenda\n5. Points to be addressed\n1) Access",
    ]
    for letter in letters:
        assert not has_advisory_report_item(letter)
        refuse_report_derived_text("doc-1", letter)  # does not raise


def test_numbered_advice_is_refused_by_the_builder_in_any_shape() -> None:
    doc = {"_id": ObjectId(), "organization_id": "org", "project_id": "p"}
    for chunk in ("**24) Key Reply Points**: deny", "24. Key Reply Points: deny", "- 10) Key Reply Points:"):
        with pytest.raises(CorrespondencePayloadError):
            build_correspondence_chunks(doc, [chunk], embedding_model="m")


def test_report_labels_cover_every_other_parser_item_label() -> None:
    from rbac_backend.services.text_processing_service import _ITEM_LABELS

    samples = {
        1: "Date", 2: "Letter No", 3: "From", 4: "To", 5: "Subject", 6: "References",
        7: "Summary", 8: "Key Words", 9: "Contractual Clauses", 11: "Full content",
        12: "Chainage To", 13: "Work Type", 14: "Issue Nature", 15: "Claim Category",
        16: "Alleged Responsibility", 17: "Priority", 18: "Key Words",
        19: "Linked Event Suggested", 20: "Reference Chain", 21: "Additional Key Words",
        22: "Summary", 23: "Contractual Clauses", 25: "Full Content", 26: "tags",
        27: "subTags",
    }
    for number, pattern in _ITEM_LABELS.items():
        if re.search(pattern, "Key Reply Points", flags=re.I):
            continue  # the advice item itself
        label = samples[number]
        assert re.search(pattern, label, flags=re.I), (number, label)
        assert _REPORT_ITEM.match(f"{number}) {label}: x"), (number, label)
        assert _REPORT_ITEM.match(f"**{label}:** x"), (number, label)


def test_repair_refuses_legacy_rows_whose_advice_a_row_boundary_split() -> None:
    document = {"full_text": HEADER + "24) Key Reply Points: deny\n" + SUBLIST + FULL}
    rows = ["22) Summary: Notice of delay.\n24) Key Reply", f" Points: deny\n{SUBLIST}", FULL]
    assert not any(has_advisory_report_item(row) for row in rows)
    assert rows_need_reprocess(document, rows)
    clean = {"full_text": "Dear Sir, notice of delay to the viaduct piers."}
    assert not rows_need_reprocess(clean, ["Dear Sir, notice of delay to the viaduct piers."])


def test_rows_written_by_the_canonical_writer_carry_the_marker() -> None:
    doc = {
        "_id": ObjectId(),
        "organization_id": "org",
        "project_id": "p",
        "summary": "s",
        "alleged_responsibility": "Employer",
        "linked_event_suggested": "EOT-3",
    }
    [chunk] = build_correspondence_chunks(doc, ["a letter"], embedding_model="m")
    assert chunk["metadata"][PAYLOAD_SCHEMA_VERSION_FIELD] == CORRESPONDENCE_PAYLOAD_SCHEMA_VERSION
    # Interpretation stays on the Mongo row, never in the vector payload.
    for field in INTERPRETIVE_FIELDS:
        assert field in chunk["metadata"]
        assert field not in chunk["payload"]
    assert chunk["payload"]["summary"] == "s"
    assert not set(INTERPRETIVE_FIELDS) & set(DESCRIPTIVE_FIELDS)
    assert not set(INTERPRETIVE_FIELDS) & ADVISORY_FIELDS
    leaked = dict(chunk["payload"], alleged_responsibility="Employer")
    with pytest.raises(CorrespondencePayloadError):
        assert_canonical_correspondence_payload(leaked)


def test_storage_sync_trusts_canonical_rows_and_refuses_split_legacy_rows(monkeypatch) -> None:
    from fastapi import HTTPException

    from rbac_backend.routers import storage_sync
    from rbac_backend.tests.correspondence_vector_harness import (
        Database,
        correspondence_document,
        run,
    )

    class _Writer:
        enabled = True

        def __init__(self) -> None:
            self.calls = 0

        async def replace_document(self, points):
            self.calls += 1
            return len(points)

    async def _count(_config, _doc_id):
        return 1

    writer = _Writer()
    monkeypatch.setattr(storage_sync, "LangChainVectorService", lambda _c: writer)
    monkeypatch.setattr(storage_sync, "_fetch_qdrant_document_count", _count)
    from rbac_backend.config.document_processing_config import DocumentProcessingConfig

    config = DocumentProcessingConfig()
    config.qdrant_url = None  # offline VectorClient; the writer is the fake above

    report_doc = correspondence_document(
        organization_id="org", project_id="p", letter_no="L/1", subject="s",
        processing_status="completed",
        full_text=HEADER + "24) Key Reply Points: deny\n" + SUBLIST + FULL,
    )
    db = Database([report_doc])
    for index, text in enumerate(["22) Summary: s\n24) Key Reply", " Points: deny"]):
        run(db.document_vectors.insert_one({"document_id": str(report_doc["_id"]), "chunk_index": index, "text": text}))
    with pytest.raises(HTTPException) as refused:
        run(storage_sync._resync_document_vectors(str(report_doc["_id"]), db, config))
    assert refused.value.status_code == 409 and writer.calls == 0

    # The same document's rows once rebuilt by the canonical writer are trusted.
    db2 = Database([report_doc])
    run(
        db2.document_vectors.insert_one(
            {
                "document_id": str(report_doc["_id"]),
                "chunk_index": 0,
                "text": "25) Full Content: The contractor gives notice of delay.",
                PAYLOAD_SCHEMA_VERSION_FIELD: CORRESPONDENCE_PAYLOAD_SCHEMA_VERSION,
            }
        )
    )
    result = run(storage_sync._resync_document_vectors(str(report_doc["_id"]), db2, config))
    assert result["status"] == "synced" and writer.calls == 1
