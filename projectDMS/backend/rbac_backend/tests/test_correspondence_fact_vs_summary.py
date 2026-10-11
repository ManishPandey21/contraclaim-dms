"""The LLM summary is context; it never outranks the letter's own text as fact.

Every consumer below labels what it hands on as factual evidence - a
``SourceEvidence(allowed_use="fact")``, an arbitration ledger snippet, a
chronology ``source_span``, an evidence-graph span, a drafting source. Each
used to read ``summary or body``, so a document carrying both was cited by its
LLM summary. The summary stays available - as summary/context - but the fact
slot takes the source body whenever the document has one.

Markers are distinct so an assertion can only pass on the right text.
"""

from __future__ import annotations

import ast
import asyncio
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from bson import ObjectId

SOURCE_BODY = "SOURCE BODY: the Engineer failed to hand over Pier P-114 under Clause 8.4 by 12-08-2024"
LLM_SUMMARY = "LLM SUMMARY: delay notice about Clause 99.9"

BACKEND = Path(__file__).resolve().parents[1]


def _row(**fields: Any) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "_id": "doc-1",
        "organization_id": "org-A",
        "project_id": "proj-A",
        "processing_status": "metadata_extracted",
        "subject": "Delay notice",
        "letterNo": "EMP/118",
        "ocrText": SOURCE_BODY,
        "summary": LLM_SUMMARY,
    }
    row.update(fields)
    return row


def _model(**fields: Any):
    from rbac_backend.models.document import Document

    values: Dict[str, Any] = {
        "_id": "doc-1",
        "organization_id": "org-A",
        "project_id": "proj-A",
        "filename": "x.pdf",
        "filetype": "application/pdf",
        "filesize": 1,
        "uploadType": "incoming",
        "date": "2024-10-14T00:00:00",
        "subject": "Delay notice",
        "status": "processed",
        "createdBy": "user-1",
        "ocrText": SOURCE_BODY,
        "summary": LLM_SUMMARY,
    }
    values.update(fields)
    return Document(**values)


class _Cursor:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self._rows = list(rows)

    def limit(self, _n: int) -> "_Cursor":
        return self

    def __aiter__(self):
        async def gen():
            for row in self._rows:
                yield row

        return gen()


class _Find:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self._rows = rows

    def find(self, *_a: Any, **_k: Any) -> _Cursor:
        return _Cursor(self._rows)

    async def find_one(self, *_a: Any, **_k: Any):
        return None


# --- the accessor contract ------------------------------------------------------


def test_fact_text_is_the_body_and_never_the_summary() -> None:
    """The accessor never returns the summary: a caller falls back to it
    itself, after its own source fields, so it cannot outrank any of them."""
    from rbac_backend.services.publication_policy import authoritative_fact_text

    assert authoritative_fact_text(_row()) == SOURCE_BODY
    assert authoritative_fact_text(_row(ocrText=None)) == ""
    assert authoritative_fact_text(_row(processing_status="human_review_required")) == ""
    # A document_vectors chunk carries the letter's summary at top level
    # beside its own source `text` (review HIGH).
    chunk = {"document_id": "d", "summary": LLM_SUMMARY, "text": "CHUNK SOURCE TEXT"}
    assert authoritative_fact_text(chunk) == ""


def test_drafting_falls_back_to_the_summary_only_without_a_body() -> None:
    from rbac_backend.services.letter_drafting.context import DraftContextBuilder

    class _Docs:
        async def get_documents_by_ids(self, ids):
            return [_model(ocrText=None)]

        async def get_comments(self, doc_id):
            return []

    builder = DraftContextBuilder.__new__(DraftContextBuilder)
    builder.document_service = _Docs()
    _ids, sources, _c = asyncio.run(
        builder._document_sources(SimpleNamespace(), SimpleNamespace(document_ids=["doc-1"]), "org-A", "proj-A", [])
    )
    assert LLM_SUMMARY in sources[0].text  # nothing better exists


# --- drafting -------------------------------------------------------------------


def test_v2_draft_context_document_fact_is_the_body() -> None:
    from rbac_backend.services.letter_drafting.context import DraftContextBuilder

    class _Docs:
        async def get_documents_by_ids(self, ids):
            return [_model()]

        async def get_comments(self, doc_id):
            return []

    builder = DraftContextBuilder.__new__(DraftContextBuilder)
    builder.document_service = _Docs()
    _ids, sources, _c = asyncio.run(
        builder._document_sources(SimpleNamespace(), SimpleNamespace(document_ids=["doc-1"]), "org-A", "proj-A", [])
    )
    fact = sources[0]
    assert fact.allowed_use == "fact"
    assert SOURCE_BODY in fact.text and LLM_SUMMARY not in fact.text
    assert fact.metadata.get("summary") == LLM_SUMMARY  # still available, as summary


def test_exact_reference_fact_is_the_body() -> None:
    from rbac_backend.models.letter_drafting import ExactReferenceSearchRequest
    from rbac_backend.services.letter_drafting.service import DraftRunService

    service = DraftRunService.__new__(DraftRunService)
    service.db = SimpleNamespace(documents=_Find([_row()]), letters=_Find([]))
    service._assert_scope_allowed = lambda *a, **k: None
    request = ExactReferenceSearchRequest(reference="EMP/118", organization_id="org-A", project_id="proj-A")
    response = asyncio.run(service.exact_reference_search(request, SimpleNamespace()))
    fact = next(s for s in response.sources if s.source_type == "context_document")
    assert SOURCE_BODY in fact.text and LLM_SUMMARY not in fact.text


def test_incoming_letter_facts_are_read_from_the_body() -> None:
    from rbac_backend.services.letter_drafting.incoming_analyzer import IncomingLetterAnalyzer

    analyzer = IncomingLetterAnalyzer.__new__(IncomingLetterAnalyzer)
    text = asyncio.run(
        analyzer._incoming_text(SimpleNamespace(content=None), SimpleNamespace(incoming_document_id=None), None, _model())
    )
    assert SOURCE_BODY in text and LLM_SUMMARY not in text


@pytest.mark.asyncio
async def test_langgraph_context_document_source_is_the_body(monkeypatch) -> None:
    from test_letterdraft_pipeline_source_authority_red import (
        SHARED_CODE,
        _document,
        _letter,
        _run_pipeline,
    )

    letter_id, doc_id = ObjectId(), ObjectId()
    run = await _run_pipeline(
        monkeypatch,
        letters=[_letter(letter_id)],
        documents=[_document(doc_id, letter_no=SHARED_CODE, summary=LLM_SUMMARY, subject="Delay notice", ocrText=SOURCE_BODY)],
        letter_id=letter_id,
    )
    sources = [s for s in run.letter_row.get("draft_sources") or [] if s.get("source_type") == "context_document"]
    assert sources, "the context document did not reach the draft sources"
    assert all(SOURCE_BODY[:120] in (s.get("snippet") or "") for s in sources)
    assert all(LLM_SUMMARY not in (s.get("snippet") or "") for s in sources)


# --- arbitration ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_arbitration_selected_document_ledger_snippet_is_the_body(monkeypatch) -> None:
    from test_arbitration_selected_reference_authority_red import _eligible_world, _select, _selection

    world, _ = _eligible_world()
    world.documents.append({**_row(_id="doc-letter"), "filename": "EOT notice", "processing_status": "completed"})
    outcome = await _select(monkeypatch, world=world, references=[_selection("doc-letter", source_type="document")])
    assert not outcome.rejected
    snippets = " ".join(str(row.get("snippet")) for row in outcome.rows)
    assert SOURCE_BODY[:120] in snippets and LLM_SUMMARY not in snippets


def test_arbitration_document_suggestion_snippet_is_the_body() -> None:
    from rbac_backend.services.arbitration_drafting.service import ArbitrationDraftingService

    service = ArbitrationDraftingService.__new__(ArbitrationDraftingService)
    service.db = SimpleNamespace(documents=_Find([_row(_id=ObjectId())]))
    out = asyncio.run(service._search_documents({"organization_id": "org-A", "project_id": "proj-A"}, "Delay", 5))
    assert out and SOURCE_BODY[:120] in out[0].snippet and LLM_SUMMARY not in out[0].snippet


# --- chronology / evidence ------------------------------------------------------


@pytest.mark.asyncio
async def test_chronology_source_span_is_the_body() -> None:
    from rbac_backend.services.chronology import ChronologyService

    service = ChronologyService.__new__(ChronologyService)
    captured: Dict[str, Any] = {}

    async def authority(_id):
        return True

    async def create_extraction(model, current_user=None):
        return {"_id": "x-1"}

    async def create_event(event, current_user):
        captured["event"] = event
        return {}

    service._has_current_document_authority = authority
    service.graph = SimpleNamespace(create_ai_extraction=create_extraction)
    service._insert_event = create_event
    service.db = SimpleNamespace(matter_chronology_events=_Find([]))
    await service._extract_document_event({"_id": "c-1", "organization_id": "org-A", "project_id": "proj-A"}, _row(), None)
    event = captured["event"]
    spans = " ".join(span.text if hasattr(span, "text") else span["text"] for span in event.source_spans)
    assert SOURCE_BODY[:80] in spans and LLM_SUMMARY not in spans
    assert LLM_SUMMARY not in (event.description or "")


def test_evidence_graph_source_spans_come_from_the_body() -> None:
    from rbac_backend.models.document_metadata import ParsedDocumentMetadata
    from rbac_backend.services.evidence_graph_service import EvidenceGraphService

    service = EvidenceGraphService.__new__(EvidenceGraphService)
    parsed = service._parsed_metadata(_row(), ParsedDocumentMetadata(summary=LLM_SUMMARY), "incoming")
    span_texts = {span["text"] for span in parsed["source_spans"]}
    assert "Clause 8.4" in span_texts and "Clause 99.9" not in span_texts
    assert parsed["summary"] == LLM_SUMMARY  # summary kept, as summary


# --- the class guard ------------------------------------------------------------

#: Accessors whose result must never outrank the body in a fact slot.
_SUMMARY_FIRST = re.compile(
    r"(?:authoritative_summary|consumable_summary)\([^()]*\)\s*or\s*(?:authoritative_text|consumable_text)\("
)


def test_no_production_path_prefers_the_summary_over_the_body() -> None:
    """The defect was one shape repeated at every fact slot. Any new
    ``summary(...) or text(...)`` fails here with file:line."""
    offenders = []
    for path in BACKEND.rglob("*.py"):
        if "tests" in path.parts or "manual" in path.parts:
            continue
        source = path.read_text(encoding="utf-8-sig")
        ast.parse(source)  # the guard reads real code, not a broken file
        flat = re.sub(r"\s+", " ", source)
        if _SUMMARY_FIRST.search(flat):
            offenders.append(str(path.relative_to(BACKEND)))
    assert not offenders, f"summary-first fact selection remains in: {offenders}"


@pytest.mark.asyncio
async def test_arbitration_clause_chunk_snippet_is_its_text_not_the_summary(monkeypatch) -> None:
    """HIGH (final review): a selected clause resolves to a document_vectors
    chunk whose top-level `summary` is the letter's LLM summary; the ledger
    snippet took it ahead of the chunk's own source `text`."""
    from test_arbitration_selected_reference_authority_red import (
        AUTHORISED_MARKER,
        _eligible_world,
        _select,
        _selection,
    )

    world, source_id = _eligible_world()
    world.vectors[-1]["summary"] = LLM_SUMMARY
    outcome = await _select(monkeypatch, world=world, references=[_selection(source_id)])
    assert not outcome.rejected
    snippets = " ".join(str(row.get("snippet")) for row in outcome.rows)
    assert AUTHORISED_MARKER in snippets and LLM_SUMMARY not in snippets


@pytest.mark.asyncio
async def test_chronology_fact_fields_come_from_the_body() -> None:
    """MEDIUM (final review): date, clauses and letter number were parsed from
    text that put the summary first."""
    from rbac_backend.services.chronology import ChronologyService

    service = ChronologyService.__new__(ChronologyService)
    captured: Dict[str, Any] = {}

    async def authority(_id):
        return True

    async def create_extraction(model, current_user=None):
        return {"_id": "x-1"}

    async def create_event(event, current_user):
        captured["event"] = event
        return {}

    service._has_current_document_authority = authority
    service.graph = SimpleNamespace(create_ai_extraction=create_extraction)
    service._insert_event = create_event
    service.db = SimpleNamespace(matter_chronology_events=_Find([]))
    row = _row(summary="LLM SUMMARY: refers to NTP dated 01-02-2023 under Clause 99.9")
    await service._extract_document_event({"_id": "c-1"}, row, None)
    event = captured["event"]
    assert event.date_text == "12-08-2024"
    assert "99.9" not in " ".join(event.contract_clauses)


def test_evidence_graph_link_refs_come_from_the_body() -> None:
    from rbac_backend.models.document_metadata import ParsedDocumentMetadata
    from rbac_backend.services.evidence_graph_service import EvidenceGraphService

    service = EvidenceGraphService.__new__(EvidenceGraphService)
    parsed = service._parsed_metadata(
        _row(), ParsedDocumentMetadata(summary="LLM SUMMARY cites IPC-77 and DWG-X99"), "incoming"
    )
    assert "IPC-77" not in parsed["payment_refs"] and "DWG-X99" not in parsed["drawing_refs"]


def test_responses_failed_is_a_provider_failure_not_a_truncation() -> None:
    from rbac_backend.utils.exceptions import DocumentProcessingError, ModelOutputIncompleteError
    from test_correspondence_source_authority import _openai_service, _responses_client

    with pytest.raises(DocumentProcessingError) as caught:
        asyncio.run(_openai_service(_responses_client("failed", None, "partial")).process_document("f"))
    assert not isinstance(caught.value, ModelOutputIncompleteError)


def test_deferred_embedding_with_nothing_to_index_is_visible() -> None:
    from rbac_backend.services.database_service import DatabaseService

    statuses: List[Dict[str, Any]] = []
    service = DatabaseService.__new__(DatabaseService)

    class _Docs:
        async def find_one(self, *_a, **_k):
            return _row(
                _id=ObjectId(),
                ocrText="1) Date: x\n2) Letter No.: y\n5) Subject: z",
                ocr_text_kind="extraction_report",
            )

    async def get_database():
        return SimpleNamespace(documents=_Docs())

    async def update_status(db, document_id, **kwargs):
        statuses.append(kwargs)

    service.get_database = get_database
    service._update_vector_sync_status = update_status
    assert asyncio.run(service.create_embeddings_for_document("65f000000000000000000001")) == 0
    assert statuses and statuses[0]["status"] == "empty"


def _find_key(value: Any, key: str) -> List[Any]:
    found: List[Any] = []
    if isinstance(value, dict):
        for k, v in value.items():
            if k == key:
                found.append(v)
            found.extend(_find_key(v, key))
    elif isinstance(value, list):
        for item in value:
            found.extend(_find_key(item, key))
    return found


@pytest.mark.asyncio
async def test_langgraph_contractual_references_come_from_the_body(monkeypatch) -> None:
    """MEDIUM (final review): contractual references were cut from the LLM
    summary whenever it mentioned "clause"."""
    from test_letterdraft_pipeline_source_authority_red import (
        SHARED_CODE,
        _document,
        _letter,
        _run_pipeline,
    )

    letter_id, doc_id = ObjectId(), ObjectId()
    run = await _run_pipeline(
        monkeypatch,
        letters=[_letter(letter_id)],
        documents=[_document(doc_id, letter_no=SHARED_CODE, summary=LLM_SUMMARY, subject="Delay notice", ocrText=SOURCE_BODY)],
        letter_id=letter_id,
    )
    references = " ".join(str(v) for v in _find_key(run.response.model_dump(), "contractual_references"))
    assert references, "no contractual references were produced"
    assert "Clause 99.9" not in references
    assert "Clause 8.4" in references
