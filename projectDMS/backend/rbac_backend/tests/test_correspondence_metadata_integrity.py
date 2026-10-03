"""Phase A: correspondence metadata integrity (DI-B3, DI-M11, DI-H6, DI-H5, DI-L7, DI-N6).

The defect family: one bad value, or one degraded extraction, silently
destroyed good data.

* DI-B3 - ``_parse_list_block`` ran the reference parser on every list field.
  A keyword such as "letter no. XYZ/12 dated 01.08.2024" became a dict inside
  ``List[str]``; the validation error hit a catch-all that returned an empty
  ``ParsedDocumentMetadata()``. Date, letter number, parties, subject,
  summary and references all vanished, the document stayed
  ``metadata_extracted``, and ``sync_bidirectional(references=[],
  clear_existing=True)`` then deleted the document's existing reference links.
* DI-M11 - a degraded extraction looked identical to a clean one.
* DI-H6 - the OCR fallback stored the filename as letter number and subject.
* DI-H5 - reprocessing overwrote human corrections.
* DI-L7 - prompt items 18 and 21 were both labelled "Key Words".
* DI-N6 - AI reply advice (``key_reply_points``) was presented to the drafter
  as what the incoming letter says, stamped with unrelated evidence ids, and
  copied into evidence-chunk payloads.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import inspect
import textwrap
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from bson import ObjectId

from rbac_backend.models.document import Document, DocumentUpdate
from rbac_backend.models.document_metadata import (
    METADATA_EXTRACTION_PROMPT_VERSION,
    ParsedDocumentMetadata,
    build_parsed_metadata,
)
from rbac_backend.models.letter_drafting import DraftRunCreateRequest, IncomingLetterAnalysis
from rbac_backend.services import text_processing_service as tps_module
from rbac_backend.services.database_service import DatabaseService
from rbac_backend.services.document_processor import DocumentProcessor
from rbac_backend.services.document_service import DocumentService
from rbac_backend.services.letter_drafting.planning import PlanningSheetBuilder
from rbac_backend.services.letter_drafting.user_direction import UserDirectionAgent
from rbac_backend.services.metadata_integrity import (
    QUALITY_COMPLETE,
    QUALITY_COMPLETE_WITH_WARNINGS,
    QUALITY_PARTIAL_EXTRACTION,
    SUMMARY_METADATA_FIELDS,
    assess_metadata_quality,
    effective_human_edited_fields,
    protect_human_edited_fields,
)
from rbac_backend.services.text_processing_service import TextProcessingService
from rbac_backend.tests.test_document_references import FakeDatabase, _make_document_dict

TRIGGER = "letter no. XYZ/12 dated 01.08.2024"

CLEAN_REPORT = """1) Date: 05-08-2024
2) Letter No.: ABC/CORR/2024/118
3) From (Company): Alpha Constructions Ltd
4) To (Company): Metro Rail Corporation
5) Subject: Delay in handover of Station Box land
6) References:
- LTR-200 dated 12.07.2024
7) Asset Type: Station
14) Issue Nature: Land Handover
18) Key Words: land handover, delay, EOT
21) Additional Key Words: station box, access
22) Summary: Contractor notifies continued delay in land handover.
23) Contractual Clauses: GCC 8.4
24) Key Reply Points - Points to be Addressed While Responding:
- Confirm handover date
25) Full Content: The full letter text.
26) extracted_tags: Delay
27) extracted_subTags: Land Acquisition
"""

#: What the clean report must parse to, exactly (the "still works" case).
CLEAN_EXPECTED: Dict[str, Any] = {
    "date": "05-08-2024",
    "subject": "Delay in handover of Station Box land",
    "letter_no": "ABC/CORR/2024/118",
    "from_company": "Alpha Constructions Ltd",
    "to_company": "Metro Rail Corporation",
    "references": [
        {
            "letterNo": "LTR-200",
            "letter_no": "LTR-200",
            "date": "12-07-2024",
            "raw": "LTR-200 dated 12.07.2024",
        }
    ],
    "asset_type": "Station",
    "location": None,
    "specific_area": None,
    "chainage_from": None,
    "chainage_to": None,
    "work_type": None,
    "issue_nature": "Land Handover",
    "claim_category": None,
    "alleged_responsibility": None,
    "priority": None,
    "summary": "- Contractor notifies continued delay in land handover.",
    "keywords": ["land handover", "delay", "EOT"],
    "linked_event_suggested": None,
    "reference_chain": None,
    "additional_keywords": ["station box", "access"],
    "contractual_clauses": ["GCC 8.4"],
    "key_reply_points": ["Confirm handover date"],
    "full_content": "The full letter text.",
    "tags": ["Delay"],
    "subTags": ["Land Acquisition"],
}

HEADER_FIELDS = ("date", "letter_no", "from_company", "to_company", "subject", "summary")


def _parser() -> TextProcessingService:
    return TextProcessingService(SimpleNamespace(chunk_size=1000, chunk_overlap=100))


def _report_with(block_line: str, value: str) -> str:
    """Replace the content of one numbered block with a multi-line value."""
    lines = CLEAN_REPORT.splitlines()
    out: List[str] = []
    skipping = False
    for line in lines:
        if line.startswith(block_line):
            out.append(f"{block_line}")
            out.append(f"- {value}")
            out.append("- delay")
            skipping = True
            continue
        if skipping and line.startswith("- "):
            continue
        skipping = False
        out.append(line)
    return "\n".join(out) + "\n"


# --- A / H: the trigger, and the clean report unchanged -----------------------


def test_clean_report_parses_exactly_as_before() -> None:
    parsed = _parser().parse_extraction_report(CLEAN_REPORT)
    assert parsed.model_dump(by_alias=True) == CLEAN_EXPECTED
    assert parsed.field_failures == {}


def test_trigger_phrase_in_keywords_does_not_wipe_unrelated_fields() -> None:
    parsed = _parser().parse_extraction_report(_report_with("18) Key Words:", TRIGGER))
    for field in HEADER_FIELDS:
        assert getattr(parsed, field) == CLEAN_EXPECTED[field], field
    assert parsed.references == CLEAN_EXPECTED["references"]
    # The keyword is kept as the string it is, not parsed into a reference.
    assert parsed.keywords == [TRIGGER, "delay"]
    assert parsed.field_failures == {}


#: Every List[str] field and the numbered block that feeds it.
LIST_FIELD_BLOCKS = [
    ("keywords", "18) Key Words:"),
    ("additional_keywords", "21) Additional Key Words:"),
    ("contractual_clauses", "23) Contractual Clauses:"),
    ("key_reply_points", "24) Key Reply Points - Points to be Addressed While Responding:"),
    ("tags", "26) extracted_tags:"),
    ("sub_tags", "27) extracted_subTags:"),
]


@pytest.mark.parametrize("field,block", LIST_FIELD_BLOCKS)
def test_reference_shaped_item_in_any_string_list_keeps_every_other_field(
    field: str, block: str
) -> None:
    parsed = _parser().parse_extraction_report(_report_with(block, TRIGGER))
    for name in HEADER_FIELDS:
        assert getattr(parsed, name) == CLEAN_EXPECTED[name], name
    assert parsed.references == CLEAN_EXPECTED["references"]
    value = getattr(parsed, field)
    assert all(isinstance(item, str) for item in value), value
    assert parsed.field_failures == {}


def test_references_field_still_parses_structured_references() -> None:
    parsed = _parser().parse_extraction_report(_report_with("6) References:", TRIGGER))
    assert parsed.references[0] == {
        "letterNo": "XYZ/12",
        "letter_no": "XYZ/12",
        "date": "01-08-2024",
        "raw": TRIGGER,
    }


# --- B: a failing reference parser is contained to references ----------------


def test_malformed_reference_is_contained_to_the_references_field(monkeypatch) -> None:
    def _explode(value: str) -> Optional[Dict[str, str]]:
        raise ValueError("malformed reference")

    monkeypatch.setattr(tps_module, "parse_legacy_reference_text", _explode)
    parsed = _parser().parse_extraction_report(CLEAN_REPORT)

    for field in HEADER_FIELDS:
        assert getattr(parsed, field) == CLEAN_EXPECTED[field], field
    assert parsed.keywords == CLEAN_EXPECTED["keywords"]
    assert parsed.references == []
    assert set(parsed.field_failures) == {"references"}


def test_one_invalid_field_is_dropped_and_recorded_not_the_whole_object() -> None:
    parsed = build_parsed_metadata(
        {
            "subject": "Kept",
            "letter_no": "L-1",
            "keywords": [{"not": "a string"}],
        }
    )
    assert parsed.subject == "Kept"
    assert parsed.letter_no == "L-1"
    assert parsed.keywords == []
    assert set(parsed.field_failures) == {"keywords"}
    # The failure record never lands in the stored metadata snapshot.
    assert "field_failures" not in parsed.model_dump(by_alias=True)


def test_parser_has_no_whole_object_empty_fallback() -> None:
    """Static guard: no handler in the parser may return an empty model."""
    source = textwrap.dedent(inspect.getsource(TextProcessingService.parse_extraction_report))
    tree = ast.parse(source)
    offenders = []
    for handler in ast.walk(tree):
        if not isinstance(handler, ast.ExceptHandler):
            continue
        for node in ast.walk(handler):
            if (
                isinstance(node, ast.Return)
                and isinstance(node.value, ast.Call)
                and getattr(node.value.func, "id", None) == "ParsedDocumentMetadata"
            ):
                offenders.append(node.lineno)
    assert offenders == [], f"whole-object fallback at lines {offenders}"


# --- DI-M11: extraction quality ------------------------------------------------


def test_quality_distinguishes_clean_warning_and_degraded_results() -> None:
    clean = _parser().parse_extraction_report(CLEAN_REPORT)
    assert assess_metadata_quality(clean, metadata_source="pydantic_ai")["status"] == QUALITY_COMPLETE

    no_to = clean.model_copy(update={"to_company": None})
    warn = assess_metadata_quality(no_to, metadata_source="pydantic_ai")
    assert warn["status"] == QUALITY_COMPLETE_WITH_WARNINGS
    assert warn["references_authoritative"] is True

    failed_refs = build_parsed_metadata(
        {"subject": "S", "letter_no": "L"}, field_failures={"references": "x"}
    )
    degraded = assess_metadata_quality(failed_refs, metadata_source="pydantic_ai")
    assert degraded["status"] == QUALITY_PARTIAL_EXTRACTION
    assert degraded["references_authoritative"] is False

    empty = assess_metadata_quality(ParsedDocumentMetadata(), metadata_source="legacy_regex")
    assert empty["status"] == QUALITY_PARTIAL_EXTRACTION
    assert empty["references_authoritative"] is False

    fallback = assess_metadata_quality(clean, metadata_source="ocr_fallback_regex")
    assert fallback["degraded"] is True
    assert fallback["references_authoritative"] is False


# --- C / D / E / I: the caller (process_document_async) ------------------------


class _Processor:
    def __init__(self, metadata: Any, *, metadata_source: str = "openai_text_legacy_regex") -> None:
        self.metadata = metadata
        self.metadata_source = metadata_source

    async def process_document(self, **_kwargs: Any) -> SimpleNamespace:
        return SimpleNamespace(
            success=True,
            metadata=self.metadata,
            processing_time=0.1,
            chunks_created=1,
            processed_path="/processed/letter.pdf",
            metadata_source=self.metadata_source,
            partial_failures={},
            publishable=True,
        )


def _service(monkeypatch, docs: List[Dict[str, Any]]) -> DocumentService:
    service = DocumentService(FakeDatabase(docs))

    async def _noop(**_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(service.graph_ingestion, "ingest_document", _noop)
    monkeypatch.setattr(service.graph_ingestion, "sync_document_to_falkor", lambda **_k: None)
    return service


async def _reprocess(
    monkeypatch,
    service: DocumentService,
    doc_id: ObjectId,
    metadata: Any,
    tmp_path: Path,
    *,
    upload_type: str = "incoming",
    metadata_source: str = "openai_text_legacy_regex",
) -> Dict[str, Any]:
    monkeypatch.setattr(
        "rbac_backend.services.document_service.create_document_processor",
        lambda: _Processor(metadata, metadata_source=metadata_source),
    )
    source = tmp_path / "letter.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    await service.process_document_async(
        str(doc_id),
        file_path=str(source),
        organization_id="org-1",
        project_id="proj-1",
        upload_type=upload_type,
    )
    db = await service._get_db()
    return await db.documents.find_one({"_id": doc_id})


def _link_targets(stored: Dict[str, Any]) -> List[str]:
    return [
        str(ref.get("documentId") if isinstance(ref, dict) else getattr(ref, "documentId", ""))
        for ref in stored.get("references") or []
    ]


def _linked_fixture(monkeypatch, tmp_path: Path, **source_overrides: Any):
    doc_id, target_id = ObjectId(), ObjectId()
    service = _service(
        monkeypatch,
        [
            _make_document_dict(_id=doc_id, summary=None, keywords=None, **source_overrides),
            _make_document_dict(_id=target_id, letterNo="LTR-200", letterNoNormalized="ltr-200"),
        ],
    )
    return service, doc_id, target_id


@pytest.mark.asyncio
@pytest.mark.parametrize("upload_type", ["incoming", "outgoing"])
async def test_degraded_reference_extraction_keeps_existing_links(
    monkeypatch, tmp_path, upload_type
) -> None:
    service, doc_id, target_id = _linked_fixture(monkeypatch, tmp_path)
    parser = _parser()

    first = await _reprocess(
        monkeypatch, service, doc_id, parser.parse_extraction_report(CLEAN_REPORT), tmp_path,
        upload_type=upload_type,
    )
    assert _link_targets(first) == [str(target_id)]
    stored_reference = first["reference"]

    def _explode(value: str) -> Optional[Dict[str, str]]:
        raise ValueError("malformed reference")

    monkeypatch.setattr(tps_module, "parse_legacy_reference_text", _explode)
    degraded = parser.parse_extraction_report(CLEAN_REPORT)
    assert "references" in degraded.field_failures

    second = await _reprocess(
        monkeypatch, service, doc_id, degraded, tmp_path, upload_type=upload_type
    )
    assert _link_targets(second) == [str(target_id)], "degraded extraction removed links"
    assert second["reference"] == stored_reference
    # The unrelated fields were still written.
    assert second["subject"] == CLEAN_EXPECTED["subject"]
    assert second["letterNo"] == CLEAN_EXPECTED["letter_no"]
    # And the degradation is visible, not a plain success.
    quality = second["metadata_quality"]
    assert quality["status"] == QUALITY_PARTIAL_EXTRACTION
    assert quality["references_authoritative"] is False
    assert "references" in quality["field_failures"]
    assert second["processing_metadata"]["partial_failures"]["metadata_parse"]["status"] == (
        QUALITY_PARTIAL_EXTRACTION
    )
    assert Document(**second).metadata_quality["status"] == QUALITY_PARTIAL_EXTRACTION


@pytest.mark.asyncio
async def test_trigger_reprocess_keeps_links_and_fields(monkeypatch, tmp_path) -> None:
    service, doc_id, target_id = _linked_fixture(monkeypatch, tmp_path)
    parser = _parser()
    await _reprocess(monkeypatch, service, doc_id, parser.parse_extraction_report(CLEAN_REPORT), tmp_path)

    second = await _reprocess(
        monkeypatch,
        service,
        doc_id,
        parser.parse_extraction_report(_report_with("18) Key Words:", TRIGGER)),
        tmp_path,
    )
    assert _link_targets(second) == [str(target_id)]
    assert second["letterNo"] == CLEAN_EXPECTED["letter_no"]
    assert second["keywords"] == [TRIGGER, "delay"]
    assert second["metadata_quality"]["status"] == QUALITY_COMPLETE


@pytest.mark.asyncio
async def test_ocr_fallback_with_no_references_keeps_existing_links(monkeypatch, tmp_path) -> None:
    service, doc_id, target_id = _linked_fixture(monkeypatch, tmp_path)
    parser = _parser()
    await _reprocess(monkeypatch, service, doc_id, parser.parse_extraction_report(CLEAN_REPORT), tmp_path)

    fallback = parser.parse_extraction_report(CLEAN_REPORT.replace("- LTR-200 dated 12.07.2024\n", "null\n"))
    assert fallback.references == []
    second = await _reprocess(
        monkeypatch, service, doc_id, fallback, tmp_path, metadata_source="ocr_fallback_regex"
    )
    assert _link_targets(second) == [str(target_id)]
    assert second["metadata_quality"]["degraded"] is True


@pytest.mark.asyncio
async def test_authoritative_zero_references_clears_parser_links(monkeypatch, tmp_path) -> None:
    service, doc_id, target_id = _linked_fixture(monkeypatch, tmp_path)
    parser = _parser()
    await _reprocess(monkeypatch, service, doc_id, parser.parse_extraction_report(CLEAN_REPORT), tmp_path)

    zero = parser.parse_extraction_report(CLEAN_REPORT.replace("- LTR-200 dated 12.07.2024\n", "null\n"))
    second = await _reprocess(monkeypatch, service, doc_id, zero, tmp_path)
    assert second["metadata_quality"]["references_authoritative"] is True
    assert _link_targets(second) == []
    assert second["reference"] == []


@pytest.mark.asyncio
async def test_valid_replacement_references_update_links(monkeypatch, tmp_path) -> None:
    doc_id, old_target, new_target = ObjectId(), ObjectId(), ObjectId()
    service = _service(
        monkeypatch,
        [
            _make_document_dict(_id=doc_id, summary=None, keywords=None),
            _make_document_dict(_id=old_target, letterNo="LTR-200", letterNoNormalized="ltr-200"),
            _make_document_dict(_id=new_target, letterNo="LTR-300", letterNoNormalized="ltr-300"),
        ],
    )
    parser = _parser()
    await _reprocess(monkeypatch, service, doc_id, parser.parse_extraction_report(CLEAN_REPORT), tmp_path)
    replacement = parser.parse_extraction_report(CLEAN_REPORT.replace("LTR-200", "LTR-300"))
    second = await _reprocess(monkeypatch, service, doc_id, replacement, tmp_path)
    assert _link_targets(second) == [str(new_target)]


@pytest.mark.asyncio
async def test_manual_links_survive_an_authoritative_parser_clear(monkeypatch, tmp_path) -> None:
    service, doc_id, target_id = _linked_fixture(monkeypatch, tmp_path)
    await service.reference_sync_service.sync_bidirectional(
        document_id=str(doc_id),
        references=[{"documentId": str(target_id), "letterNo": "LTR-200"}],
        source="manual",
        default_link_type="direct",
    )
    zero = _parser().parse_extraction_report(CLEAN_REPORT.replace("- LTR-200 dated 12.07.2024\n", "null\n"))
    stored = await _reprocess(monkeypatch, service, doc_id, zero, tmp_path)
    assert _link_targets(stored) == [str(target_id)]


@pytest.mark.asyncio
async def test_human_edited_subject_and_letter_number_survive_reprocess(
    monkeypatch, tmp_path
) -> None:
    doc_id = ObjectId()
    service = _service(
        monkeypatch,
        [
            _make_document_dict(
                _id=doc_id,
                subject="Human subject",
                letterNo="HUMAN/1",
                letterNoNormalized="human/1",
                human_edited_fields=["subject", "letterNo"],
            )
        ],
    )
    stored = await _reprocess(
        monkeypatch, service, doc_id, _parser().parse_extraction_report(CLEAN_REPORT), tmp_path
    )
    assert stored["subject"] == "Human subject"
    assert stored["letterNo"] == "HUMAN/1"
    assert stored["letterNoNormalized"] == "human/1"
    # Unedited fields still refresh from extraction.
    assert stored["from"] == CLEAN_EXPECTED["from_company"]
    # The snapshot does not contradict the protected field.
    assert "subject" not in stored["metadata"] or stored["metadata"]["subject"] != CLEAN_EXPECTED["subject"]


@pytest.mark.asyncio
async def test_legacy_summary_override_flag_protects_summary_fields(monkeypatch, tmp_path) -> None:
    doc_id = ObjectId()
    service = _service(
        monkeypatch,
        [
            _make_document_dict(
                _id=doc_id,
                keywords=["human keyword"],
                asset_type="Tunnel",
                manual_summary_metadata_override=True,
            )
        ],
    )
    stored = await _reprocess(
        monkeypatch, service, doc_id, _parser().parse_extraction_report(CLEAN_REPORT), tmp_path
    )
    assert stored["keywords"] == ["human keyword"]
    assert stored["asset_type"] == "Tunnel"
    assert stored["subject"] == CLEAN_EXPECTED["subject"]


def test_processor_side_writer_also_keeps_human_edits() -> None:
    doc_id = ObjectId()
    stored = _make_document_dict(
        _id=doc_id, subject="Human subject", human_edited_fields=["subject"]
    )
    fake_db = FakeDatabase([stored])
    service = DatabaseService(SimpleNamespace())
    parsed = _parser().parse_extraction_report(CLEAN_REPORT)
    doc = asyncio.run(
        service._upsert_document_metadata(fake_db, str(doc_id), "/tmp/x.pdf", parsed, "ocr text")
    )
    persisted = asyncio.run(fake_db.documents.find_one({"_id": doc_id}))
    assert persisted["subject"] == "Human subject"
    assert doc["subject"] == "Human subject"
    assert persisted["letterNo"] == CLEAN_EXPECTED["letter_no"]


def test_protection_helper_contract() -> None:
    updates = {
        "subject": "AI",
        "letterNo": "AI/1",
        "letterNoNormalized": "ai/1",
        "from": "AI Co",
        "metadata": {"subject": "AI", "letter_no": "AI/1", "from_company": "AI Co"},
    }
    stored = {
        "human_edited_fields": ["subject", "letterNo"],
        "metadata": {"subject": "Human"},
    }
    kept = protect_human_edited_fields(updates, stored)
    assert sorted(kept) == ["letterNo", "subject"]
    assert "subject" not in updates and "letterNo" not in updates
    assert "letterNoNormalized" not in updates
    assert updates["from"] == "AI Co"
    assert updates["metadata"] == {"subject": "Human", "from_company": "AI Co"}
    assert effective_human_edited_fields({"manual_summary_metadata_override": True})
    assert effective_human_edited_fields({}) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "existing,expected",
    [
        ({}, ["asset_type", "keywords"]),
        # Keeps earlier markers.
        ({"human_edited_fields": ["subject"]}, ["asset_type", "keywords", "subject"]),
        # A document edited before per-field markers keeps its whole
        # summary-field protection rather than shrinking to this edit.
        ({"manual_summary_metadata_override": True}, None),
    ],
)
async def test_summary_metadata_edit_records_per_field_markers(existing, expected) -> None:
    from rbac_backend.tests.test_document_update_regression import (
        RecordingCollection,
        _document_payload,
    )

    doc_id = ObjectId()
    collection = RecordingCollection([_document_payload(_id=doc_id, **existing)])
    service = DocumentService(SimpleNamespace(documents=collection))
    await service.update_summary_metadata(
        str(doc_id),
        {"asset_type": "Tunnel", "metadata.asset_type": "Tunnel", "keywords": ["k"]},
        updated_by="user-1",
    )
    recorded = collection.last_update_payload["$set"]["human_edited_fields"]
    if expected is None:
        assert set(recorded) == set(SUMMARY_METADATA_FIELDS)
    else:
        assert recorded == expected


@pytest.mark.asyncio
async def test_put_edit_records_only_changed_correspondence_fields(monkeypatch) -> None:
    from rbac_backend.routers.documents import controller_update_document

    from rbac_backend.tests.test_document_update_regression import (
        RecordingCollection,
        _document_payload,
    )

    class _Collection(RecordingCollection):
        async def find_one(self, filter: Dict[str, Any], *_args: Any, **_kwargs: Any):  # type: ignore[override]
            return await super().find_one(filter)

    doc_id = ObjectId()
    from datetime import datetime, timezone

    stored_date = datetime(2024, 8, 5, 0, 0)
    collection = _Collection(
        [
            _document_payload(
                _id=doc_id, subject="Old subject", letterNo="LTR-001", date=stored_date
            )
        ]
    )
    service = DocumentService(SimpleNamespace(documents=collection))

    async def _allow(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def _enrich(document: Any) -> Any:
        return document

    monkeypatch.setattr(service, "enrich_document", _enrich)
    controller = SimpleNamespace(
        document_service=service,
        policy_service=SimpleNamespace(authorize_document=_allow),
        audit_service=SimpleNamespace(emit=_allow),
    )
    await controller_update_document(
        controller,
        str(doc_id),
        # letterNo and date are resubmitted unchanged (the date tz-aware, as a
        # form sends it); only the subject is a real edit.
        DocumentUpdate(
            subject="Corrected subject",
            letterNo="LTR-001",
            date=datetime(2024, 8, 5, 0, 0, tzinfo=timezone.utc),
        ),
        SimpleNamespace(id="user-1"),
    )
    written = collection.last_update_payload["$set"]
    assert written["subject"] == "Corrected subject"
    assert written["human_edited_fields"] == ["subject"]


# --- F: the OCR fallback invents nothing --------------------------------------


def test_ocr_fallback_does_not_turn_the_filename_into_source_metadata() -> None:
    processor = DocumentProcessor.__new__(DocumentProcessor)
    ocr_text = (
        "We refer to our letter no. XYZ/12 dated 01.08.2024 regarding the station box.\n"
        "The handover remains pending."
    )
    report = processor._build_ocr_fallback_report(
        ocr_text, filename="0123456789abcdef01234567_ABC-CORR-118.pdf"
    )
    parsed = _parser().parse_extraction_report(report)
    assert parsed.letter_no is None
    assert parsed.subject is None
    assert parsed.date is None, "a reference's 'dated' is not this letter's date"
    assert "ABC-CORR-118" not in report.split("25) Full Content:")[0]


def test_ocr_fallback_still_reads_labelled_header_lines() -> None:
    processor = DocumentProcessor.__new__(DocumentProcessor)
    ocr_text = (
        "Ref: ABC/CORR/2024/118\n"
        "Subject: Delay in handover of Station Box land\n"
        "Date: 05.08.2024\n"
        "We refer to our letter no. XYZ/12 dated 01.08.2024."
    )
    parsed = _parser().parse_extraction_report(
        processor._build_ocr_fallback_report(ocr_text, filename="scan.pdf")
    )
    assert parsed.letter_no == "ABC/CORR/2024/118"
    assert parsed.date == "05-08-2024"
    assert parsed.subject == "Delay in handover of Station Box land"


# --- J: both pipelines share the parser --------------------------------------


class _RecordingDatabaseService:
    def __init__(self) -> None:
        self.saved: List[Dict[str, Any]] = []
        self.partial_failures: Dict[str, Any] = {}

    async def save_document_data(self, **kwargs: Any) -> int:
        self.saved.append(kwargs)
        return 1


class _FileService:
    async def save_summary(self, *args: Any) -> None:
        return None


def _real_parser_processor(reply: Any) -> DocumentProcessor:
    class _OpenAI:
        async def process_text(self, text: str, *, filename: str) -> str:
            if isinstance(reply, Exception):
                raise reply
            return reply

        async def cleanup_file(self, file_id: str) -> None:
            return None

    processor = DocumentProcessor.__new__(DocumentProcessor)
    processor.openai_service = _OpenAI()
    processor.text_service = _parser()
    processor.file_service = _FileService()
    processor.database_service = _RecordingDatabaseService()
    processor.pydantic_ai_service = SimpleNamespace(is_enabled=False, extract_metadata=None)
    processor.config = SimpleNamespace(use_pydantic_ai=False, openai_api_key=None)
    return processor


@pytest.mark.parametrize("pipeline", ["legacy_v0", "unified_v1"])
def test_both_pipelines_keep_fields_on_the_trigger(tmp_path: Path, pipeline: str) -> None:
    processor = _real_parser_processor(_report_with("18) Key Words:", TRIGGER))
    source = tmp_path / "letter.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    extraction = (
        None
        if pipeline == "legacy_v0"
        else SimpleNamespace(pages=[], withheld_pages=[], completeness=SimpleNamespace(value="complete"))
    )
    result = asyncio.run(
        processor._extract_and_persist(
            input_path=source,
            original_path=str(source),
            processed_path=source,
            raw_ocr_text="OCR text of the letter",
            extraction=extraction,
            pages_human_review=[],
            path_structure="org/proj",
            upload_type="incoming",
            document_id="doc-1",
            skip_embeddings=False,
            start_time=0.0,
        )
    )
    assert result.success is True
    for field in HEADER_FIELDS:
        assert getattr(result.metadata, field) == CLEAN_EXPECTED[field], field
    assert result.metadata.references == CLEAN_EXPECTED["references"]


def test_ai_failure_fallback_is_reported_degraded(tmp_path: Path) -> None:
    processor = _real_parser_processor(RuntimeError("llm down"))
    source = tmp_path / "ABC-CORR-118.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    result = asyncio.run(
        processor._extract_and_persist(
            input_path=source,
            original_path=str(source),
            processed_path=source,
            raw_ocr_text="Some OCR text without a header block.",
            extraction=None,
            pages_human_review=[],
            path_structure="org/proj",
            upload_type="incoming",
            document_id="doc-1",
            skip_embeddings=False,
            start_time=0.0,
        )
    )
    assert result.metadata_source == "ocr_fallback_regex"
    assert result.metadata.letter_no is None
    assert result.metadata.subject is None
    quality = assess_metadata_quality(
        result.metadata,
        metadata_source=result.metadata_source,
        partial_failures=result.partial_failures,
    )
    assert quality["degraded"] is True
    assert quality["references_authoritative"] is False


# --- G: DI-N6, reply analysis is not source fact ------------------------------


def _analysis() -> IncomingLetterAnalysis:
    return IncomingLetterAnalysis(
        main_request="Grant EOT of 60 days",
        key_reply_points=["Reserve rights on prolongation cost"],
    )


def _fact_source() -> SimpleNamespace:
    return SimpleNamespace(
        source_id="fact-1",
        allowed_use="fact",
        source_type="context_document",
        clause_number=None,
    )


def test_ai_reply_points_are_advisory_rows_without_borrowed_evidence() -> None:
    rows = PlanningSheetBuilder()._reply_matrix(
        DraftRunCreateRequest(points="Address programme impact"), [_fact_source()], _analysis()
    )
    by_point = {row.incoming_point: row for row in rows}

    advisory = by_point["Reserve rights on prolongation cost"]
    assert advisory.point_origin == "ai_reply_consideration"
    assert advisory.source_ids == []
    assert advisory.status == "needs_confirmation"

    incoming = by_point["Grant EOT of 60 days"]
    assert incoming.point_origin == "incoming_letter"
    assert incoming.source_ids == ["fact-1"]
    assert by_point["Address programme impact"].point_origin == "user_direction"


def test_plan_text_never_frames_ai_advice_as_the_letters_words() -> None:
    builder = PlanningSheetBuilder()
    rows = builder._reply_matrix(DraftRunCreateRequest(), [_fact_source()], _analysis())
    sheet = SimpleNamespace(
        sender_role="contractor",
        tone="formal",
        risk_level="low",
        issue_type="eot",
        response_deadline=None,
        factual_basis=[],
        contractual_basis=[],
        cited_clause_evaluations=[],
        recommended_position=None,
        required_action=None,
        trigger_event=None,
    )
    plan = builder._plan_text(sheet, rows)
    assert "Incoming point: Reserve rights on prolongation cost" not in plan
    assert (
        "AI-derived reply consideration (advisory; not stated in the incoming letter): "
        "Reserve rights on prolongation cost"
    ) in plan
    assert "Incoming point: Grant EOT of 60 days" in plan


def test_scope_question_does_not_call_ai_advice_the_letters_content() -> None:
    analysis = IncomingLetterAnalysis(key_reply_points=["A", "B"])
    questions = UserDirectionAgent.build_questions(
        analysis, DraftRunCreateRequest(desired_position="Reject"), []
    )
    scope = next(q for q in questions if q.category == "scope")
    assert "incoming letter has" not in scope.question
    assert "advisory" in scope.question


def test_reply_advice_is_not_copied_into_evidence_chunk_payloads() -> None:
    source = textwrap.dedent(inspect.getsource(DatabaseService._create_and_store_embeddings))
    tree = ast.parse(source)
    payload_fields = {
        element.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Tuple)
        for element in node.elts
        if isinstance(element, ast.Constant) and isinstance(element.value, str)
    }
    assert "summary" in payload_fields  # positive control: found the tuple
    assert "key_reply_points" not in payload_fields


# --- DI-L7: prompt labels -----------------------------------------------------


def test_prompt_items_have_distinct_labels_and_a_bumped_version() -> None:
    from rbac_backend.services.openai_service import OpenAIService

    prompt = OpenAIService._expanded_extraction_prompt(SimpleNamespace())  # type: ignore[arg-type]
    labels = [
        line.split(":", 1)[0].split(")", 1)[1].strip()
        for line in prompt.splitlines()
        if line[:3].rstrip(")").isdigit() and ")" in line[:4]
    ]
    assert len(labels) == len(set(labels)), labels
    assert "Additional Key Words" in labels
    assert METADATA_EXTRACTION_PROMPT_VERSION == "existing_document_metadata.v3"


# --- Sibling fixes ------------------------------------------------------------


@pytest.mark.asyncio
async def test_degraded_run_merges_the_stored_metadata_snapshot(monkeypatch, tmp_path) -> None:
    service, doc_id, _target = _linked_fixture(monkeypatch, tmp_path)
    parser = _parser()
    first = await _reprocess(
        monkeypatch, service, doc_id, parser.parse_extraction_report(CLEAN_REPORT), tmp_path
    )
    assert first["metadata"]["references"] == CLEAN_EXPECTED["references"]

    def _explode(value: str) -> Optional[Dict[str, str]]:
        raise ValueError("malformed reference")

    monkeypatch.setattr(tps_module, "parse_legacy_reference_text", _explode)
    second = await _reprocess(
        monkeypatch, service, doc_id, parser.parse_extraction_report(CLEAN_REPORT), tmp_path
    )
    # The failed field keeps its previous record; the rest refreshes.
    assert second["metadata"]["references"] == CLEAN_EXPECTED["references"]
    assert second["metadata"]["subject"] == CLEAN_EXPECTED["subject"]


@pytest.mark.asyncio
async def test_clean_run_still_replaces_the_snapshot(monkeypatch, tmp_path) -> None:
    doc_id = ObjectId()
    service = _service(
        monkeypatch,
        [_make_document_dict(_id=doc_id, metadata={"stale_field": "old", "subject": "old"})],
    )
    stored = await _reprocess(
        monkeypatch, service, doc_id, _parser().parse_extraction_report(CLEAN_REPORT), tmp_path
    )
    assert "stale_field" not in stored["metadata"]


def test_processor_writer_receives_the_quality_marker(tmp_path: Path) -> None:
    """Bulk upload persists only through the processor's writer."""
    processor = _real_parser_processor(RuntimeError("llm down"))
    source = tmp_path / "letter.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    asyncio.run(
        processor._extract_and_persist(
            input_path=source,
            original_path=str(source),
            processed_path=source,
            raw_ocr_text="Some OCR text without a header block.",
            extraction=None,
            pages_human_review=[],
            path_structure="org/proj",
            upload_type="incoming",
            document_id="doc-1",
            skip_embeddings=False,
            start_time=0.0,
        )
    )
    quality = processor.database_service.saved[-1]["metadata_quality"]
    assert quality["status"] == QUALITY_PARTIAL_EXTRACTION
    assert "ai_extraction_unavailable_deterministic_fallback" in quality["warnings"]


def test_database_writer_persists_quality_and_merges_degraded_snapshot() -> None:
    doc_id = ObjectId()
    fake_db = FakeDatabase(
        [_make_document_dict(_id=doc_id, metadata={"references": [{"letterNo": "OLD"}]})]
    )
    parsed = build_parsed_metadata(
        {"subject": "S", "letter_no": "L"}, field_failures={"references": "x"}
    )
    quality = assess_metadata_quality(parsed, metadata_source="openai_text_legacy_regex")
    asyncio.run(
        DatabaseService(SimpleNamespace())._upsert_document_metadata(
            fake_db, str(doc_id), "/tmp/x.pdf", parsed, "ocr", metadata_quality=quality
        )
    )
    persisted = asyncio.run(fake_db.documents.find_one({"_id": doc_id}))
    assert persisted["metadata_quality"]["status"] == QUALITY_PARTIAL_EXTRACTION
    assert persisted["metadata"]["references"] == [{"letterNo": "OLD"}]
    assert persisted["subject"] == "S"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "quality,expected_clear",
    [
        ({"references_authoritative": False, "degraded": True}, False),
        ({"references_authoritative": True, "degraded": False}, True),
        (None, True),  # processed before the marker existed: unchanged behaviour
    ],
)
async def test_manual_resync_only_clears_after_an_authoritative_extraction(
    quality, expected_clear
) -> None:
    from rbac_backend.routers.documents import controller_sync_references

    document = Document(
        **_make_document_dict(_id=ObjectId(), reference=[], metadata_quality=quality)
    )
    calls: List[Dict[str, Any]] = []

    async def _sync(**kwargs: Any) -> Dict[str, Any]:
        calls.append(kwargs)
        return {"resolved": 0}

    async def _get(_document_id: str) -> Document:
        return document

    async def _noop(*_args: Any, **_kwargs: Any) -> None:
        return None

    controller = SimpleNamespace(
        document_service=SimpleNamespace(
            get_document_by_id=_get,
            reference_sync_service=SimpleNamespace(sync_bidirectional=_sync),
            sync_reference_graph=_noop,
        ),
        policy_service=SimpleNamespace(authorize_document=_noop),
    )
    await controller_sync_references(controller, document.id, SimpleNamespace(id="u"))
    assert calls[0]["clear_existing"] is expected_clear


# --- Review round 1: findings H1-H4, M5-M8, L9, L12, L14, L15 -----------------

BODY_WITH_NUMBERED_LINES = CLEAN_REPORT.replace(
    "25) Full Content: The full letter text.\n",
    "25) Full Content: The full letter text.\n"
    "2) Letter No. of Engineer: ENG/9 dated 01.07.2024\n"
    "6) References: Our letter ZZZ/1 dated 02.02.2024\n"
    "Yours faithfully\n",
)


def test_numbered_lines_inside_full_content_do_not_replace_header_items() -> None:
    parsed = _parser().parse_extraction_report(BODY_WITH_NUMBERED_LINES)
    assert parsed.letter_no == CLEAN_EXPECTED["letter_no"]
    assert parsed.references == CLEAN_EXPECTED["references"]
    assert "ENG/9" in parsed.full_content and "Yours faithfully" in parsed.full_content
    assert parsed.tags == CLEAN_EXPECTED["tags"]  # item 26 after the body still parses


def test_label_fallbacks_do_not_read_the_letter_body() -> None:
    report = (
        CLEAN_REPORT.replace("2) Letter No.: ABC/CORR/2024/118", "2) Letter No.: null")
        .replace("5) Subject: Delay in handover of Station Box land", "5) Subject: null")
        .replace(
            "25) Full Content: The full letter text.",
            "25) Full Content:\nLetter No.: OTHER/77\nSubject: Some other letter",
        )
    )
    parsed = _parser().parse_extraction_report(report)
    assert parsed.letter_no is None
    assert parsed.subject is None


def test_reference_chain_item_is_not_read_as_a_reference() -> None:
    report = CLEAN_REPORT.replace("6) References:\n- LTR-200 dated 12.07.2024\n", "") + (
        "20) Reference Chain: reply to previous letter\n"
    )
    parsed = _parser().parse_extraction_report(report)
    assert parsed.references == []
    assert "references" in parsed.field_failures  # absent item: unknown, not zero


@pytest.mark.asyncio
async def test_missing_references_item_keeps_existing_links(monkeypatch, tmp_path) -> None:
    service, doc_id, target_id = _linked_fixture(monkeypatch, tmp_path)
    parser = _parser()
    await _reprocess(monkeypatch, service, doc_id, parser.parse_extraction_report(CLEAN_REPORT), tmp_path)
    no_item = parser.parse_extraction_report(
        CLEAN_REPORT.replace("6) References:\n- LTR-200 dated 12.07.2024\n", "")
    )
    stored = await _reprocess(monkeypatch, service, doc_id, no_item, tmp_path)
    assert _link_targets(stored) == [str(target_id)]
    assert stored["metadata_quality"]["references_authoritative"] is False


@pytest.mark.parametrize(
    "line,expected",
    [
        ("References:", None),
        ("Refund of retention money is overdue as per clause 14", None),
        ("Ref: XYZ/12 dated 01.08.2024", None),
        ("Ref: ABC/CORR/2024/118", "ABC/CORR/2024/118"),
        ("Our Ref. No. ABC/118", "ABC/118"),
        ("Letter No.: ABC/1 dated 05.08.2024", "ABC/1"),
        ("Letter No: see enclosure", None),
        ("We refer to our letter no. XYZ/12 dated 01.08.2024", None),
    ],
)
def test_fallback_letter_number_reads_only_this_letters_label(line, expected) -> None:
    processor = DocumentProcessor.__new__(DocumentProcessor)
    assert processor._fallback_letter_number([line]) == expected


@pytest.mark.asyncio
async def test_degraded_source_fills_gaps_but_never_replaces_stored_values(
    monkeypatch, tmp_path
) -> None:
    doc_id = ObjectId()
    service = _service(
        monkeypatch,
        [
            _make_document_dict(
                _id=doc_id,
                letterNo="ABC/CORR/2024/118",
                letterNoNormalized="abc/corr/2024/118",
                subject="Clean subject",
                summary=None,
            )
        ],
    )
    guessed = build_parsed_metadata(
        {"letter_no": "ERENCES", "subject": "Guessed subject", "summary": "- OCR line"}
    )
    stored = await _reprocess(
        monkeypatch, service, doc_id, guessed, tmp_path, metadata_source="ocr_fallback_regex"
    )
    assert stored["letterNo"] == "ABC/CORR/2024/118"
    assert stored["letterNoNormalized"] == "abc/corr/2024/118"
    assert stored["subject"] == "Clean subject"
    assert stored["summary"] == "- OCR line"  # empty before, so filled


def test_processor_writer_never_stores_a_non_authoritative_reference_list() -> None:
    doc_id = ObjectId()
    fake_db = FakeDatabase([_make_document_dict(_id=doc_id, reference=[{"letterNo": "KEEP"}])])
    parsed = build_parsed_metadata(
        {"subject": "S", "letter_no": "L-9", "references": ["PARTIAL/1 dated 01.01.2024"]}
    )
    quality = assess_metadata_quality(
        parsed,
        metadata_source="openai_text_legacy_regex",
        partial_failures={"ai_extraction": {"stage": "ocr_text_extraction"}},
    )
    assert quality["references_authoritative"] is False
    asyncio.run(
        DatabaseService(SimpleNamespace())._upsert_document_metadata(
            fake_db, str(doc_id), "/tmp/x.pdf", parsed, "ocr", metadata_quality=quality
        )
    )
    persisted = asyncio.run(fake_db.documents.find_one({"_id": doc_id}))
    assert persisted["reference"] == [{"letterNo": "KEEP"}]


def test_processor_writer_keeps_the_normalized_number_in_step() -> None:
    doc_id = ObjectId()
    fake_db = FakeDatabase(
        [_make_document_dict(_id=doc_id, letterNo="OLD/1", letterNoNormalized="old/1")]
    )
    parsed = _parser().parse_extraction_report(CLEAN_REPORT)
    asyncio.run(
        DatabaseService(SimpleNamespace())._upsert_document_metadata(
            fake_db, str(doc_id), "/tmp/x.pdf", parsed, "ocr"
        )
    )
    persisted = asyncio.run(fake_db.documents.find_one({"_id": doc_id}))
    assert persisted["letterNo"] == CLEAN_EXPECTED["letter_no"]
    assert persisted["letterNoNormalized"] != "old/1"


def test_pydantic_ai_success_after_ocr_text_failure_is_authoritative() -> None:
    parsed = _parser().parse_extraction_report(CLEAN_REPORT)
    quality = assess_metadata_quality(
        parsed,
        metadata_source="pydantic_ai",
        partial_failures={"ai_extraction": {"stage": "ocr_text_extraction"}},
    )
    assert quality["references_authoritative"] is True
    withheld = assess_metadata_quality(
        parsed,
        metadata_source="pydantic_ai",
        partial_failures={"ai_extraction": {"stage": "withheld_text_layer"}},
    )
    assert withheld["references_authoritative"] is False


@pytest.mark.asyncio
async def test_graph_publication_uses_the_human_value(monkeypatch, tmp_path) -> None:
    doc_id = ObjectId()
    service = _service(
        monkeypatch,
        [
            _make_document_dict(
                _id=doc_id,
                letterNo="ABC/1",
                letterNoNormalized="abc/1",
                human_edited_fields=["letterNo"],
            )
        ],
    )
    published: List[Any] = []
    falkor: List[Any] = []
    original_publish = service._publish_graph_and_evidence_from_current
    original_falkor = service._sync_current_document_to_falkor

    async def _publish(**kwargs: Any) -> Any:
        published.append(kwargs["metadata"])
        return await original_publish(**kwargs)

    async def _falkor(document_id: str, **kwargs: Any) -> Any:
        falkor.append(kwargs.get("metadata"))
        return await original_falkor(document_id, **kwargs)

    monkeypatch.setattr(service, "_publish_graph_and_evidence_from_current", _publish)
    monkeypatch.setattr(service, "_sync_current_document_to_falkor", _falkor)
    ai = _parser().parse_extraction_report(CLEAN_REPORT.replace("ABC/CORR/2024/118", "ABC/l"))
    await _reprocess(monkeypatch, service, doc_id, ai, tmp_path)

    assert published and all(m.letter_no == "ABC/1" for m in published)
    assert falkor and all(m.letter_no == "ABC/1" for m in falkor)
    # Everything the person did not edit is still the extraction's.
    assert published[0].subject == CLEAN_EXPECTED["subject"]


@pytest.mark.asyncio
async def test_an_edit_made_while_processing_runs_is_kept(monkeypatch, tmp_path) -> None:
    doc_id = ObjectId()
    service = _service(monkeypatch, [_make_document_dict(_id=doc_id, subject="Upload subject")])
    parsed = _parser().parse_extraction_report(CLEAN_REPORT)

    class _SlowProcessor(_Processor):
        async def process_document(self, **kwargs: Any) -> SimpleNamespace:
            db = await service._get_db()
            # A person corrects the subject while OCR/LLM work is in flight.
            await db.documents.update_one(
                {"_id": doc_id},
                {"$set": {"subject": "Human subject", "human_edited_fields": ["subject"]}},
            )
            return await super().process_document(**kwargs)

    monkeypatch.setattr(
        "rbac_backend.services.document_service.create_document_processor",
        lambda: _SlowProcessor(parsed),
    )
    source = tmp_path / "letter.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    await service.process_document_async(
        str(doc_id), file_path=str(source), organization_id="org-1",
        project_id="proj-1", upload_type="incoming",
    )
    db = await service._get_db()
    stored = await db.documents.find_one({"_id": doc_id})
    assert stored["subject"] == "Human subject"


def test_snapshot_carries_the_human_value_for_put_edited_fields() -> None:
    updates = {"subject": "AI", "metadata": {"subject": "AI", "letter_no": "AI/1"}}
    stored = {
        "subject": "Human",
        "human_edited_fields": ["subject"],
        "metadata": {"subject": "Old AI"},
    }
    protect_human_edited_fields(updates, stored)
    assert updates["metadata"]["subject"] == "Human"


def test_clause_applicability_ignores_ai_reply_advice() -> None:
    from rbac_backend.services.letter_drafting import clause_checker

    tree = ast.parse(inspect.getsource(clause_checker))
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            getattr(target, "id", None) == "issue_terms" for target in node.targets
        ):
            found = True
            assert "key_reply_points" not in ast.unparse(node.value)
    assert found, "issue_terms assignment not found"


def test_user_stated_point_is_not_demoted_by_a_matching_ai_suggestion() -> None:
    analysis = IncomingLetterAnalysis(key_reply_points=["Address programme impact"])
    rows = PlanningSheetBuilder()._reply_matrix(
        DraftRunCreateRequest(points="Address programme impact"), [_fact_source()], analysis
    )
    assert len(rows) == 1
    assert rows[0].point_origin == "user_direction"
    assert rows[0].source_ids == ["fact-1"]


@pytest.mark.parametrize(
    "quality,warned", [("partial_extraction", True), ("complete", False), (None, False)]
)
def test_partial_incoming_metadata_is_flagged_to_the_drafter(quality, warned) -> None:
    analysis = IncomingLetterAnalysis(source_metadata_quality=quality)
    context = SimpleNamespace(threshold_inputs={}, current_materials=[])
    _sheet, _rows, summary, _plan = PlanningSheetBuilder().build(
        SimpleNamespace(), DraftRunCreateRequest(), "contractor", context, [], analysis  # type: ignore[arg-type]
    )
    assert any("partially extracted" in warning for warning in summary.warnings) is warned


# --- Review round 2: N1-N4, N6, N7 --------------------------------------------


def test_numbered_sub_items_inside_references_do_not_swallow_later_items() -> None:
    report = CLEAN_REPORT.replace(
        "6) References:\n- LTR-200 dated 12.07.2024\n",
        "6) References:\n"
        "1) Our letter AAA/1 dated: 01.01.2024\n"
        "7) Our letter No.: GGG/7 dated 03.03.2024\n",
    )
    parsed = _parser().parse_extraction_report(report)
    assert parsed.asset_type == "Station"
    assert parsed.letter_no == CLEAN_EXPECTED["letter_no"]
    assert parsed.date == CLEAN_EXPECTED["date"]


def test_items_out_of_order_are_all_read() -> None:
    lines = CLEAN_REPORT.splitlines()
    summary = next(line for line in lines if line.startswith("22) Summary"))
    reordered = [line for line in lines if line != summary]
    reordered.insert(reordered.index("18) Key Words: land handover, delay, EOT"), summary)
    parsed = _parser().parse_extraction_report("\n".join(reordered) + "\n")
    assert parsed.summary == CLEAN_EXPECTED["summary"]
    assert parsed.keywords == CLEAN_EXPECTED["keywords"]
    assert parsed.additional_keywords == CLEAN_EXPECTED["additional_keywords"]


def test_a_letter_with_many_numbered_paragraphs_keeps_its_tags_and_text() -> None:
    report = CLEAN_REPORT.replace(
        "25) Full Content: The full letter text.\n",
        "25) Full Content: The full letter text.\n"
        "26) Payment claim: the contractor claims payment.\n"
        "27) Further: we reserve our rights.\n",
    )
    parsed = _parser().parse_extraction_report(report)
    assert parsed.tags == CLEAN_EXPECTED["tags"]
    assert parsed.sub_tags == CLEAN_EXPECTED["subTags"]
    assert "we reserve our rights" in parsed.full_content


@pytest.mark.parametrize(
    "line",
    [
        "Reference to your letter no. 12",
        "Ref: Your letter No. XYZ/12",
        "Reference 2 above is relevant",
        "Ref: GCC Clause 8.4",
    ],
)
def test_fallback_letter_number_rejects_prose(line) -> None:
    processor = DocumentProcessor.__new__(DocumentProcessor)
    assert processor._fallback_letter_number([line]) is None


@pytest.mark.parametrize(
    "references_item",
    [
        "6) References:\n",  # present, empty
        "6) References: []\n",  # present, empty list form
    ],
)
def test_unreadable_references_item_is_not_authoritative_zero(references_item) -> None:
    report = CLEAN_REPORT.replace("6) References:\n- LTR-200 dated 12.07.2024\n", references_item)
    parsed = _parser().parse_extraction_report(report)
    assert parsed.references == []
    assert "references" in parsed.field_failures
    # And the empty item does not swallow the next item as a reference.
    assert parsed.asset_type == "Station"


def test_explicit_null_references_item_is_authoritative_zero() -> None:
    report = CLEAN_REPORT.replace("6) References:\n- LTR-200 dated 12.07.2024\n", "6) References: null\n")
    parsed = _parser().parse_extraction_report(report)
    assert parsed.references == []
    assert parsed.field_failures == {}


def test_dash_layout_unreadable_references_and_body_are_contained() -> None:
    report = (
        "- Date: 05-08-2024\n"
        "- Letter No.: null\n"
        "- References: Our letter X/9 dated 01.01.2024\n"
        "- Full content: Letter No.: OTHER/77\n"
    )
    parsed = _parser().parse_extraction_report(report)
    # Either the reference is read, or the item is recorded as unreadable;
    # never an authoritative empty list.
    assert parsed.references or "references" in parsed.field_failures
    assert parsed.letter_no is None  # not read from the body


def test_degraded_snapshot_agrees_with_the_kept_value() -> None:
    from rbac_backend.services.metadata_integrity import keep_stored_values_on_degraded_source

    updates = {"letterNo": "GUESS", "metadata": {"letter_no": "GUESS", "summary": "- s"}}
    stored = {"letterNo": "ABC/1"}
    keep_stored_values_on_degraded_source(
        updates, stored, {"source_degraded": True}
    )
    assert "letterNo" not in updates
    assert updates["metadata"]["letter_no"] == "ABC/1"


def test_a_clean_read_of_a_headerless_document_is_not_a_degraded_source() -> None:
    parsed = build_parsed_metadata({"summary": "- minutes of meeting", "keywords": ["minutes"]})
    quality = assess_metadata_quality(parsed, metadata_source="pydantic_ai")
    assert quality["source_degraded"] is False
    assert quality["references_authoritative"] is False  # nothing read: links kept


def test_pydantic_ai_on_a_discarded_whole_file_reply_is_not_authoritative() -> None:
    parsed = _parser().parse_extraction_report(CLEAN_REPORT)
    quality = assess_metadata_quality(
        parsed,
        metadata_source="pydantic_ai",
        partial_failures={"ai_extraction": {"stage": "whole_file_extraction"}},
    )
    assert quality["references_authoritative"] is False


# --- Review round 3: R1 (unrecognised labels), R2 (Markdown labels) ----------


@pytest.mark.parametrize(
    "old,new,lost_field,guarded_field",
    [
        (
            "3) From (Company): Alpha Constructions Ltd",
            "3) Sender (Company): Alpha",
            "from_company",
            "letter_no",
        ),
        (
            "2) Letter No.: ABC/CORR/2024/118",
            "2) Letter Ref. No.: ABC/DEF/123",
            "letter_no",
            "date",
        ),
        (
            "5) Subject: Delay in handover of Station Box land",
            "5) Sub: Delay",
            "subject",
            "to_company",
        ),
    ],
)
def test_an_unrecognised_item_label_never_pollutes_the_previous_field(
    old, new, lost_field, guarded_field
) -> None:
    parsed = _parser().parse_extraction_report(CLEAN_REPORT.replace(old, new))
    # The previous field keeps exactly its own value.
    assert getattr(parsed, guarded_field) == CLEAN_EXPECTED[guarded_field]
    # The unrecognised item is unknown, and says so.
    assert getattr(parsed, lost_field) is None
    assert lost_field in parsed.field_failures
    # Everything after it still parses.
    assert parsed.asset_type == "Station"


def test_an_unrecognised_references_label_is_contained() -> None:
    report = CLEAN_REPORT.replace(
        "6) References:\n- LTR-200 dated 12.07.2024\n",
        "6) Ref:\n- Our letter X/9 dated 01.01.2024\n",
    )
    parsed = _parser().parse_extraction_report(report)
    assert parsed.subject == CLEAN_EXPECTED["subject"]
    assert parsed.references == []
    assert "references" in parsed.field_failures


def test_markdown_bold_labels_parse_cleanly() -> None:
    report = "\n".join(
        (
            f"{line.split(')', 1)[0]}) **{line.split(')', 1)[1].strip().split(':', 1)[0]}:**"
            f"{line.split(':', 1)[1]}"
        )
        if line[:1].isdigit() and ":" in line
        else line
        for line in CLEAN_REPORT.splitlines()
    ) + "\n"
    assert "1) **Date:** 05-08-2024" in report
    parsed = _parser().parse_extraction_report(report)
    for field in HEADER_FIELDS:
        assert getattr(parsed, field) == CLEAN_EXPECTED[field], field
    assert parsed.asset_type == "Station"
    assert parsed.references == CLEAN_EXPECTED["references"]
    assert parsed.tags == CLEAN_EXPECTED["tags"]
    assert parsed.field_failures == {}


# --- Review round 4: L1 (sub-list numbers inside a list item) ------------------


def test_a_numbered_sub_list_inside_a_list_item_is_kept_whole() -> None:
    report = CLEAN_REPORT.replace(
        "24) Key Reply Points - Points to be Addressed While Responding:\n- Confirm handover date\n",
        "24) Key Reply Points - Points to be Addressed While Responding:\n"
        "- Confirm handover date\n"
        "10) Reserve rights: under clause 8.4\n"
        "- Request the revised programme\n",
    )
    parsed = _parser().parse_extraction_report(report)
    assert "Request the revised programme" in parsed.key_reply_points
    assert any("Reserve rights" in point for point in parsed.key_reply_points)
    assert parsed.full_content == CLEAN_EXPECTED["full_content"]
    assert parsed.field_failures == {}


# --- PR #36 typing correction: the paths mypy flagged ---------------------------
#
# mypy reported seven Optional findings in `_parse_numbered_blocks` and
# `protect_human_edited_fields`. No finding was reachable: the parser's number and
# label are None only when the line did not match, and every use sat behind a
# `match is not None` test mypy could not connect to them; the snapshot was
# type-tested on one lookup and used from another. The correction moved those
# uses inside the match and reads each snapshot once. These pin the inputs that
# would have exercised a None if one had been reachable.


def test_unnumbered_and_unmatched_lines_never_open_or_set_aside_an_item() -> None:
    unrecognised: Dict[int, str] = {}
    blocks = _parser()._parse_numbered_blocks(
        "\n".join(
            [
                "Preamble without a number",
                "7 ) spaced number: not an item",
                "no) label: not an item",
                "1) Date: 01-08-2024",
                "a continuation line",
                "99) Unknown label: x",
                "3) Sender (Company): set aside",
                "glued nowhere",
            ]
        ),
        unrecognised,
    )
    assert list(blocks) == [1]
    # 99 is no schema item, so it is content of the open item, not a new one.
    assert blocks[1]["content"] == "01-08-2024\na continuation line\n99) Unknown label: x"
    assert unrecognised == {3: "Sender (Company)"}


def test_parser_tolerates_an_empty_or_missing_report() -> None:
    assert _parser()._parse_numbered_blocks("") == {}
    assert _parser()._parse_numbered_blocks(None) == {}  # type: ignore[arg-type]


@pytest.mark.parametrize("stored_snapshot", [None, [], "text", 0])
@pytest.mark.parametrize("new_snapshot", [None, [], "text", {"subject": "AI"}])
def test_protection_tolerates_a_non_mapping_snapshot(stored_snapshot: Any, new_snapshot: Any) -> None:
    # The helper mutates the snapshot in place; a parametrize value is shared
    # across cases, so each case gets its own copy.
    new_snapshot = copy.deepcopy(new_snapshot)
    updates: Dict[str, Any] = {"subject": "AI", "metadata": new_snapshot}
    stored = {"human_edited_fields": ["subject"], "metadata": stored_snapshot, "subject": ""}
    kept = protect_human_edited_fields(updates, stored)
    assert kept == ["subject"]
    assert "subject" not in updates
    if isinstance(new_snapshot, dict):
        # No human value and no stored snapshot value: the AI value is removed
        # rather than published over the person's (empty) field.
        assert updates["metadata"] == {}
    else:
        assert updates["metadata"] == new_snapshot
