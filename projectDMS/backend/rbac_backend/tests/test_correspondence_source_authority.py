"""Correspondence source authority and fail-visible extraction truncation.

Three defects, one theme - LLM text posing as the letter:

1. **Precedence.** ``authoritative_text`` returned ``body -> full_text ->
   ocrText``. ``full_text`` held report Item 25, the LLM's retyped copy of the
   very OCR text it was given, so every drafting, planning, arbitration,
   evidence and vector consumer received the paraphrase ahead of the source.
2. **Redundant Item 25.** When complete native/OCR text existed the model was
   still asked to retype the whole letter, spending most of the 4096-token
   output budget and silently truncating the items after it.
3. **Silent truncation.** No extraction path checked the provider's stop
   signal, so a reply cut off at the output cap was parsed and persisted as a
   complete extraction - including a partial Item 25 as the letter body.

Markers are distinct strings so an assertion can only pass on the right text.
No test calls a model or the network.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest

from rbac_backend.models.document_metadata import (
    METADATA_EXTRACTION_PROMPT_VERSION,
    ParsedDocumentMetadata,
)
from rbac_backend.services.publication_policy import authoritative_text, consumable_text

OCR = "AUTHORITATIVE OCR TEXT of letter EMP/118 dated 14-10-2024"
LLM = "LLM ALTERED TEXT of letter EMP/118 dated 15-10-2024"
SUMMARY = "- 14-10-2024: Employer notified delay"

#: The report written to ``ocrText`` when no source text existed (legacy
#: behaviour, still produced for no-text PDFs).
REPORT = "\n".join(
    [
        "1) Date: 14-10-2024",
        "2) Letter No.: EMP/118",
        "3) From (Company): Employer",
        "4) To (Company): Contractor",
        "5) Subject: Delay notice",
        "6) References: null",
        f"22) Summary: {SUMMARY}",
        "24) Key Reply Points - Points to be Addressed While Responding: - Confirm dates",
        f"25) Full Content: {LLM}",
    ]
)


def _doc(**fields: Any) -> Dict[str, Any]:
    base: Dict[str, Any] = {"_id": "doc-1", "processing_status": "metadata_extracted"}
    base.update(fields)
    return base


# ============================================================================
# A. Source precedence at the policy accessor
# ============================================================================


def test_a_source_ocr_text_outranks_llm_full_text() -> None:
    """RED before the fix: returned the LLM's Item 25."""
    assert authoritative_text(_doc(ocrText=OCR, full_text=LLM)) == OCR


def test_a_stale_llm_full_text_on_a_legacy_row_cannot_outrank_ocr() -> None:
    """Existing rows carry no provenance marker; read order alone must fix them."""
    row = _doc(ocrText=OCR, full_text=LLM, summary=SUMMARY)
    assert authoritative_text(row) == OCR
    assert "full_text_source" not in row and "ocr_text_kind" not in row


def test_a_no_source_row_keeps_item_25_as_its_body() -> None:
    """The fallback: ocrText is the report, so Item 25 is the only body."""
    assert authoritative_text(_doc(ocrText=REPORT, full_text=LLM)) == LLM


def test_a_the_extraction_report_is_never_served_as_the_letter() -> None:
    """RED before the fix: a no-source row without Item 25 served the report,
    reply advice included, as the letter body."""
    text = authoritative_text(_doc(ocrText=REPORT, summary=SUMMARY))
    assert text == SUMMARY
    assert "Key Reply Points" not in text


def test_a_provenance_markers_decide_when_present() -> None:
    labelled_report = _doc(ocrText="Plain words", ocr_text_kind="extraction_report", full_text=LLM)
    assert authoritative_text(labelled_report) == LLM
    labelled_source = _doc(ocrText=OCR, ocr_text_kind="source_text", full_text=LLM)
    assert authoritative_text(labelled_source) == OCR


def test_a_body_still_ranks_first_and_blocked_documents_stay_withheld() -> None:
    assert authoritative_text(_doc(body="BODY", ocrText=OCR, full_text=LLM)) == "BODY"
    blocked = _doc(ocrText=OCR, full_text=LLM, processing_status="human_review_required")
    assert authoritative_text(blocked) == ""


def test_a_a_letter_with_ordinary_header_lines_is_still_source_text() -> None:
    letter = "Date: 14-10-2024\nSubject: Delay\nFrom: Employer\nTo: Contractor\n1. We refer to clause 8.4."
    assert authoritative_text(_doc(ocrText=letter, full_text=LLM)) == letter


def _document_model(**fields: Any):
    from rbac_backend.models.document import Document

    values: Dict[str, Any] = {
        "_id": "d1",
        "organization_id": "org",
        "project_id": "proj",
        "filename": "x.pdf",
        "filetype": "application/pdf",
        "filesize": 1,
        "uploadType": "incoming",
        "date": "2024-10-14T00:00:00",
        "subject": "Delay",
        "status": "processed",
        "createdBy": "user-1",
    }
    values.update(fields)
    return Document(**values)


def test_a_model_objects_carry_provenance_to_the_policy() -> None:
    doc = _document_model(ocrText="Plain words", ocr_text_kind="extraction_report", full_text=LLM)
    assert consumable_text(doc) == LLM
    doc.ocrText, doc.ocr_text_kind = OCR, "source_text"
    assert consumable_text(doc) == OCR


# ============================================================================
# Q/R. Downstream consumers receive the source text
# ============================================================================


class _One:
    def __init__(self, doc: Optional[Dict[str, Any]]) -> None:
        self._doc = doc

    async def find_one(self, *_a: Any, **_k: Any):
        return self._doc


def test_q_retrieval_drafting_agent_receives_source_text() -> None:
    from rbac_backend.agents.service import DraftingAgentService

    service = DraftingAgentService.__new__(DraftingAgentService)
    service.db = SimpleNamespace(letters=_One(None), documents=_One(_doc(ocrText=OCR, full_text=LLM)))
    assert asyncio.run(service._load_letter_text("doc-1")) == OCR


def test_q_deep_planning_receives_source_text() -> None:
    from bson import ObjectId

    import rbac_backend.routers.deep_planning as dp

    oid = ObjectId()
    db = SimpleNamespace(documents=_One(_doc(_id=oid, filename="l.pdf", ocrText=OCR, full_text=LLM)))
    text = asyncio.run(dp.extract_document_content(db, [str(oid)]))
    assert OCR in text and LLM not in text
    assert "OCR Text:" not in text  # the body may be Item 25; it is not labelled OCR


def test_q_letter_drafting_context_receives_source_text() -> None:
    from rbac_backend.services.letter_drafting.context import DraftContextBuilder

    doc = _document_model(ocrText=OCR, full_text=LLM)

    class _Docs:
        async def get_documents_by_ids(self, ids: List[str]):
            return [doc]

        async def get_comments(self, doc_id: str):
            return []

    builder = DraftContextBuilder.__new__(DraftContextBuilder)
    builder.document_service = _Docs()
    request = SimpleNamespace(document_ids=["d1"])
    _ids, sources, _comments = asyncio.run(
        builder._document_sources(SimpleNamespace(), request, "org", "proj", [])
    )
    assert sources and OCR in sources[0].text and LLM not in sources[0].text


@pytest.mark.asyncio
async def test_r_arbitration_selected_document_receives_source_text(monkeypatch) -> None:
    from test_arbitration_selected_reference_authority_red import (
        _eligible_world,
        _select,
        _selection,
    )

    world, _ = _eligible_world()
    world.documents.append(
        {
            "_id": "doc-letter",
            "organization_id": "org-A",
            "project_id": "proj-A",
            "processing_status": "completed",
            "filename": "EOT notice",
            "ocrText": OCR,
            "full_text": LLM,
        }
    )
    outcome = await _select(
        monkeypatch, world=world, references=[_selection("doc-letter", source_type="document")]
    )
    assert not outcome.rejected
    rendered = outcome.text()
    assert OCR in rendered and LLM not in rendered


def test_r_evidence_graph_derives_from_source_text() -> None:
    from rbac_backend.services.evidence_graph_service import _document_body

    metadata = ParsedDocumentMetadata(full_content=LLM)
    assert _document_body(_doc(ocrText=OCR, full_text=LLM), metadata) == OCR
    # No-source row: Item 25 remains the fallback.
    assert _document_body(_doc(ocrText=REPORT), metadata) == LLM


def test_r_duplicate_fingerprint_uses_source_text() -> None:
    from rbac_backend.services.duplicate_detection_service import DuplicateDetectionService

    service = DuplicateDetectionService.__new__(DuplicateDetectionService)
    same_source_different_paraphrase = service._compare_documents(
        _doc(ocrText=OCR, full_text=LLM),
        _doc(_id="doc-2", ocrText=OCR, full_text=LLM + " reworded"),
    )
    assert same_source_different_paraphrase["text_fingerprint_match"] is True


def test_r_strategy_context_uses_source_text() -> None:
    from rbac_backend.services.strategy_context_service import StrategyContextService

    entry = StrategyContextService._format_document_entry(
        SimpleNamespace(letterNo="EMP/118", subject="Delay", ocrText=OCR, full_text=LLM)
    )
    assert OCR[:40] in entry and "LLM ALTERED" not in entry


# ============================================================================
# P/RAG. Vector writers embed source text
# ============================================================================


def test_p_deferred_embedding_uses_source_text() -> None:
    from rbac_backend.services.database_service import DatabaseService

    seen: List[str] = []
    service = DatabaseService.__new__(DatabaseService)

    async def get_database():
        return SimpleNamespace(documents=_One(_doc(ocrText=OCR, full_text=LLM)))

    async def create(db, doc, text):
        seen.append(text)
        return 1

    service.get_database = get_database
    service._create_and_store_embeddings = create
    asyncio.run(service.create_embeddings_for_document("65f000000000000000000001"))
    assert seen == [OCR]


def test_p_ingestion_reindex_uses_source_text_and_never_the_report() -> None:
    from rbac_backend.ingestion.pipeline import IngestionPipeline

    pipeline = IngestionPipeline.__new__(IngestionPipeline)
    assert pipeline._extract_text(_doc(ocrText=OCR, full_text=LLM)) == OCR
    assert pipeline._extract_text(_doc(ocrText=REPORT)) == ""


# ============================================================================
# B/D/E/M. Item 25 is requested only when no complete source text exists
# ============================================================================


def _report_prompt(**kwargs: Any) -> str:
    from rbac_backend.services.openai_service import OpenAIService

    return OpenAIService._expanded_extraction_prompt(SimpleNamespace(), **kwargs)


def test_b_metadata_only_prompt_omits_item_25_and_keeps_every_other_item() -> None:
    import re

    full = _report_prompt()
    metadata_only = _report_prompt(include_full_content=False)
    assert "25) Full Content" in full
    assert "Full Content" not in metadata_only
    numbered = lambda p: re.findall(r"^(\d+)\) ", p, flags=re.M)  # noqa: E731
    assert numbered(metadata_only) == [n for n in numbered(full) if n != "25"]
    for number in ("22", "23", "24", "26", "27"):
        line = next(row for row in full.splitlines() if row.startswith(f"{number}) "))
        assert line in metadata_only.splitlines()


def test_b_prompt_version_is_unchanged_v4() -> None:
    assert METADATA_EXTRACTION_PROMPT_VERSION == "existing_document_metadata.v4"


def test_d_a_report_without_item_25_parses_cleanly() -> None:
    from rbac_backend.services.text_processing_service import TextProcessingService

    report = "\n".join(line for line in REPORT.splitlines() if not line.startswith("25)"))
    report += "\n26) extracted_tags: null\n27) extracted_subTags: null"
    parsed = TextProcessingService(SimpleNamespace(chunk_size=1000, chunk_overlap=100)).parse_extraction_report(report)
    assert parsed.field_failures == {}
    assert parsed.full_content is None
    assert parsed.summary == SUMMARY
    assert parsed.key_reply_points == ["Confirm dates"]


def test_the_pydantic_agent_is_never_asked_to_retype_the_letter() -> None:
    from rbac_backend.services.pydantic_ai_service import PydanticAIService

    prompt = PydanticAIService._build_expanded_prompt(SimpleNamespace(), "LETTER", {})
    assert "Full Content" not in prompt
    for number in ("22)", "24)", "26)", "27)"):
        assert number in prompt


def test_pydantic_never_stores_its_trimmed_input_as_full_content() -> None:
    from rbac_backend.services.pydantic_ai_service import PydanticAIService

    data = SimpleNamespace(summary_points=["x"], full_content=None)
    parsed = PydanticAIService._to_parsed_metadata(SimpleNamespace(), data, fallback_text="TRIMMED INPUT")
    assert parsed.full_content is None


# --- the processor, end to end on fakes ----------------------------------------

FULL_REPORT = REPORT + "\n26) extracted_tags: null\n27) extracted_subTags: null"
METADATA_REPORT = "\n".join(row for row in FULL_REPORT.splitlines() if not row.startswith("25)"))


class _OpenAI:
    def __init__(self, *, text_reply: Any = METADATA_REPORT, file_reply: Any = FULL_REPORT) -> None:
        self.text_reply = text_reply
        self.file_reply = file_reply
        self.text_calls: List[Dict[str, Any]] = []
        self.uploads = 0

    async def process_text(self, text: str, *, filename: Optional[str] = None, include_full_content: bool = True):
        self.text_calls.append({"text": text, "include_full_content": include_full_content})
        if isinstance(self.text_reply, Exception):
            raise self.text_reply
        return self.text_reply

    async def upload_file(self, path: str) -> str:
        self.uploads += 1
        return "file-1"

    async def process_document(self, file_id: str) -> str:
        if isinstance(self.file_reply, Exception):
            raise self.file_reply
        return self.file_reply

    async def cleanup_file(self, file_id: str) -> None:
        return None


async def _run(processor, tmp_path: Path, *, unified: bool):
    from rbac_backend.services.pipeline_routing import LEGACY_PIPELINE, UNIFIED_PIPELINE

    pdf = tmp_path / "EMP-118.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")
    return await processor.process_document(
        str(pdf),
        "org/project",
        "incoming",
        "65f000000000000000000001",
        pipeline_version=UNIFIED_PIPELINE if unified else LEGACY_PIPELINE,
    )


@pytest.fixture
def build(monkeypatch):
    """A processor on fakes whose persistence is the real DatabaseService
    ``_upsert_document_metadata`` against an in-memory collection."""
    from test_document_processor import make_processor

    from rbac_backend.services.database_service import DatabaseService

    def factory(ocr_text: Optional[str], openai: _OpenAI):
        processor = make_processor(ocr_text or "", openai)
        if ocr_text is None:
            async def no_text(path):
                return path, None

            processor.ocr_service.process_pdf = no_text
        written: Dict[str, Any] = {}
        embedded: List[str] = []
        db_service = DatabaseService.__new__(DatabaseService)
        db_service.config = SimpleNamespace(chunk_size=1000, chunk_overlap=100)
        db_service.partial_failures = {}

        class _Documents:
            async def find_one(self, *_a, **_k):
                return None

            async def insert_one(self, doc):
                written.update(doc)
                return SimpleNamespace(inserted_id="65f000000000000000000001")

        async def save_document_data(**kwargs):
            await DatabaseService._upsert_document_metadata(
                db_service,
                SimpleNamespace(documents=_Documents()),
                kwargs["document_id"],
                kwargs["file_path"],
                kwargs["parsed_metadata"],
                kwargs["full_text"],
                metadata_quality=kwargs.get("metadata_quality"),
                source_provenance=kwargs.get("source_provenance"),
            )
            embedded.append(kwargs["embedding_text"])
            return 1

        async def save_summary(*_a, **_k):
            return None

        processor.database_service.save_document_data = save_document_data
        processor.file_service = SimpleNamespace(save_summary=save_summary)
        processor.written = written
        processor.embedded = embedded
        return processor

    return factory


@pytest.mark.asyncio
async def test_b_c_d_complete_source_omits_item_25_and_stores_source_as_full_text(build, tmp_path) -> None:
    openai = _OpenAI()
    processor = build(OCR, openai)
    result = await _run(processor, tmp_path, unified=True)

    assert result.success
    assert openai.text_calls[0]["include_full_content"] is False
    assert openai.uploads == 0
    doc = processor.written
    assert doc["full_text"] == OCR and doc["full_text_source"] == "source_text"
    assert doc["ocrText"] == OCR and doc["ocr_text_kind"] == "source_text"
    assert doc["source_text_status"] == "complete"
    assert "full_content" not in doc.get("metadata", {})
    assert doc["metadata_quality"]["status"] == "complete"
    assert processor.embedded == [OCR]
    # N/O: summary chronology and advisory reply points unchanged.
    assert doc["summary"] == SUMMARY
    assert doc["key_reply_points"] == ["Confirm dates"]


@pytest.mark.asyncio
async def test_b_a_parsed_full_content_is_ignored_when_it_was_not_requested(build, tmp_path) -> None:
    """A model that volunteers Item 25 anyway cannot replace the source."""
    processor = build(OCR, _OpenAI(text_reply=FULL_REPORT))
    await _run(processor, tmp_path, unified=True)
    assert processor.written["full_text"] == OCR


@pytest.mark.asyncio
async def test_legacy_source_of_unknown_completeness_still_requests_item_25(build, tmp_path) -> None:
    openai = _OpenAI(text_reply=FULL_REPORT)
    processor = build(OCR, openai)
    await _run(processor, tmp_path, unified=False)

    assert openai.text_calls[0]["include_full_content"] is True
    doc = processor.written
    assert doc["full_text"] == LLM and doc["full_text_source"] == "llm_full_content"
    assert doc["ocr_text_kind"] == "source_text" and doc["source_text_status"] == "unverified"
    # ...and the source still outranks it for every reader.
    assert authoritative_text({**doc, "processing_status": "metadata_extracted"}) == OCR
    assert processor.embedded == [OCR]


@pytest.mark.asyncio
async def test_e_no_source_keeps_item_25_as_the_fallback_body(build, tmp_path) -> None:
    openai = _OpenAI()
    processor = build(None, openai)
    await _run(processor, tmp_path, unified=False)

    assert openai.uploads == 1 and openai.text_calls == []
    doc = processor.written
    assert doc["full_text"] == LLM and doc["full_text_source"] == "llm_full_content"
    assert doc["ocr_text_kind"] == "extraction_report" and doc["source_text_status"] == "absent"
    assert authoritative_text({**doc, "processing_status": "metadata_extracted"}) == LLM
    assert processor.embedded == [LLM]


# ============================================================================
# F/G. Provider truncation signals
# ============================================================================


def _openai_service(client: Any):
    from rbac_backend.services.openai_service import OpenAIService

    service = OpenAIService.__new__(OpenAIService)
    service.config = SimpleNamespace(max_output_tokens=4096, openai_model="gpt-4o")
    service._client = client
    return service


def _chat_client(finish_reason: str, content: str):
    async def create(**_kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(finish_reason=finish_reason, message=SimpleNamespace(content=content))]
        )

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def _responses_client(status: str, reason: Optional[str], content: str):
    async def create(**_kwargs):
        details = SimpleNamespace(reason=reason) if reason else None
        return SimpleNamespace(status=status, incomplete_details=details, output_text=content)

    return SimpleNamespace(responses=SimpleNamespace(create=create))


SECRET = "CONFIDENTIAL LETTER BODY 9f3a"


def test_g_chat_length_is_a_visible_truncation_without_document_text() -> None:
    from rbac_backend.utils.exceptions import ModelOutputIncompleteError

    service = _openai_service(_chat_client("length", f"1) Date: x\n25) Full Content: {SECRET}"))
    with pytest.raises(ModelOutputIncompleteError) as caught:
        asyncio.run(service.process_text(SECRET, filename="x.pdf"))
    err = caught.value
    assert err.truncated and err.reason == "max_output_tokens" and err.max_output_tokens == 4096
    assert err.stage == "ocr_text_extraction" and err.provider == "openai.chat_completions"
    for surface in (str(err), repr(err), repr(err.failure_record())):
        assert SECRET not in surface
    assert SECRET in (err.partial_output or "")  # kept for salvage only


def test_g_chat_content_filter_is_incomplete_too() -> None:
    from rbac_backend.utils.exceptions import ModelOutputIncompleteError

    service = _openai_service(_chat_client("content_filter", "1) Date: x"))
    with pytest.raises(ModelOutputIncompleteError) as caught:
        asyncio.run(service.process_text("text", filename="x.pdf"))
    assert caught.value.reason == "content_filter" and not caught.value.truncated


def test_m_chat_stop_is_unchanged() -> None:
    service = _openai_service(_chat_client("stop", METADATA_REPORT))
    assert asyncio.run(service.process_text("text", filename="x.pdf")) == METADATA_REPORT


def test_f_responses_incomplete_max_output_tokens_is_a_visible_truncation() -> None:
    from rbac_backend.utils.exceptions import ModelOutputIncompleteError

    service = _openai_service(_responses_client("incomplete", "max_output_tokens", f"25) Full Content: {SECRET}"))
    with pytest.raises(ModelOutputIncompleteError) as caught:
        asyncio.run(service.process_document("file-1"))
    err = caught.value
    assert err.truncated and err.stage == "whole_file_extraction" and err.provider == "openai.responses"
    assert SECRET not in str(err) and SECRET not in repr(err.failure_record())


@pytest.mark.parametrize("status,reason", [("incomplete", "content_filter")])
def test_f_other_responses_outcomes_fail_visibly(status: str, reason: Optional[str]) -> None:
    from rbac_backend.utils.exceptions import ModelOutputIncompleteError

    service = _openai_service(_responses_client(status, reason, "partial"))
    with pytest.raises(ModelOutputIncompleteError) as caught:
        asyncio.run(service.process_document("file-1"))
    assert not caught.value.truncated


def test_m_responses_completed_is_unchanged() -> None:
    service = _openai_service(_responses_client("completed", None, FULL_REPORT))
    assert asyncio.run(service.process_document("file-1")) == FULL_REPORT


# ============================================================================
# H. PydanticAI - its own finish_reason, through the real library
# ============================================================================


def _pydantic_service(model_function):
    pytest.importorskip("pydantic_ai")
    from pydantic_ai.models.function import FunctionModel

    from rbac_backend.services.pydantic_ai_service import PydanticAIService

    config = SimpleNamespace(
        use_pydantic_ai=True,
        openai_api_key="ci-openai-key",
        pydantic_ai_model="gpt-4o",
        openai_model="gpt-4o",
        max_output_tokens=4096,
        openai_timeout=30,
    )
    service = PydanticAIService(config)
    assert service.is_enabled
    service._agent._model = FunctionModel(model_function)  # the agent's own model slot
    return service


def test_h_pydantic_truncated_tool_call_is_a_visible_truncation() -> None:
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    from rbac_backend.services.pydantic_ai_service import PydanticAIMetadataError
    from rbac_backend.utils.exceptions import ModelOutputIncompleteError

    def cut_off(messages, info):
        tool = info.output_tools[0].name
        return ModelResponse(
            parts=[ToolCallPart(tool_name=tool, args='{"subject": "Del')],
            finish_reason="length",
        )

    service = _pydantic_service(cut_off)
    with pytest.raises(PydanticAIMetadataError) as caught:
        asyncio.run(service.extract_metadata(SECRET))
    err = caught.value
    assert isinstance(err, ModelOutputIncompleteError) and err.truncated
    assert err.provider == "pydantic_ai" and SECRET not in str(err)


def test_h_pydantic_length_after_valid_output_is_still_truncation() -> None:
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    from rbac_backend.utils.exceptions import ModelOutputIncompleteError

    def cut_after_json(messages, info):
        tool = info.output_tools[0].name
        return ModelResponse(
            parts=[ToolCallPart(tool_name=tool, args={"subject": "Delay"})],
            finish_reason="length",
        )

    service = _pydantic_service(cut_after_json)
    with pytest.raises(ModelOutputIncompleteError):
        asyncio.run(service.extract_metadata("letter text"))


def test_m_pydantic_normal_stop_is_unchanged() -> None:
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    def complete(messages, info):
        tool = info.output_tools[0].name
        return ModelResponse(
            parts=[ToolCallPart(tool_name=tool, args={"subject": "Delay", "summary_points": ["a"]})],
            finish_reason="stop",
        )

    service = _pydantic_service(complete)
    result = asyncio.run(service.extract_metadata("letter text"))
    assert result.metadata.subject == "Delay" and result.metadata.full_content is None


# ============================================================================
# I/J/K/L. A truncated reply is never persisted as complete
# ============================================================================


def _truncated(stage: str, partial: str):
    from rbac_backend.utils.exceptions import ModelOutputIncompleteError

    return ModelOutputIncompleteError(
        provider="test",
        stage=stage,
        reason="max_output_tokens",
        max_output_tokens=4096,
        partial_output=partial,
    )


CUT_IN_ITEM_25 = FULL_REPORT.split("\n26)")[0][:-10]  # stops mid Full Content
CUT_IN_ITEM_26 = FULL_REPORT.split("\n27)")[0][:-3]  # Item 25 finished, 26 cut


@pytest.mark.asyncio
async def test_i_k_l_no_source_truncated_item_25_is_not_stored(build, tmp_path) -> None:
    processor = build(None, _OpenAI(file_reply=_truncated("whole_file_extraction", CUT_IN_ITEM_25)))
    result = await _run(processor, tmp_path, unified=False)

    doc = processor.written
    assert "full_text" not in doc, "a cut-off Item 25 was stored as the letter body"
    assert "full_content" not in doc.get("metadata", {})
    assert result.partial_failures["ai_extraction"]["truncated"] is True
    assert doc["metadata_quality"]["status"] == "partial_extraction"
    assert "model_output_incomplete" in doc["metadata_quality"]["warnings"]
    # Items the reply finished are kept; the only text left for vectors is
    # the report, which the payload guard refuses visibly (Phase B contract).
    assert doc["summary"] == SUMMARY and doc["letterNo"] == "EMP/118"
    from rbac_backend.retrieval.correspondence_payload import (
        CorrespondencePayloadError,
        refuse_report_derived_text,
    )

    with pytest.raises(CorrespondencePayloadError):
        refuse_report_derived_text("doc-1", processor.embedded[0])
    # The partial report in ocrText is labelled, so no reader serves it.
    assert doc["ocr_text_kind"] == "extraction_report"
    assert authoritative_text({**doc, "processing_status": "metadata_extracted"}) == SUMMARY


@pytest.mark.asyncio
async def test_i_a_finished_item_25_survives_a_cut_in_a_later_item(build, tmp_path) -> None:
    processor = build(None, _OpenAI(file_reply=_truncated("whole_file_extraction", CUT_IN_ITEM_26)))
    result = await _run(processor, tmp_path, unified=False)
    assert processor.written["full_text"] == LLM
    assert result.partial_failures["ai_extraction"]["truncated"] is True
    assert processor.written["metadata_quality"]["status"] == "partial_extraction"


@pytest.mark.asyncio
async def test_j_k_l_complete_source_survives_a_truncated_metadata_reply(build, tmp_path) -> None:
    cut = METADATA_REPORT.split("\n24)")[0] + "\n24) Key Reply Points - Points to be Addressed While Responding: - Con"
    processor = build(OCR, _OpenAI(text_reply=_truncated("ocr_text_extraction", cut)))
    result = await _run(processor, tmp_path, unified=True)

    doc = processor.written
    assert doc["full_text"] == OCR and doc["ocrText"] == OCR
    record = result.partial_failures["ai_extraction"]
    assert record["truncated"] is True and record["stage"] == "ocr_text_extraction"
    assert set(record) >= {"provider", "reason", "max_output_tokens"}
    assert doc["metadata_quality"]["status"] == "partial_extraction"
    # The cut item (Key Reply Points) is dropped, not stored half-written.
    assert "key_reply_points" not in doc
    assert doc["summary"] == SUMMARY


@pytest.mark.asyncio
async def test_legacy_truncation_inside_item_25_keeps_source_and_metadata(build, tmp_path) -> None:
    processor = build(OCR, _OpenAI(text_reply=_truncated("ocr_text_extraction", CUT_IN_ITEM_25)))
    result = await _run(processor, tmp_path, unified=False)
    doc = processor.written
    assert "full_text" not in doc
    assert doc["ocrText"] == OCR and doc["summary"] == SUMMARY
    assert doc["key_reply_points"] == ["Confirm dates"]
    assert result.metadata_source == "openai_text_legacy_regex"
    assert doc["metadata_quality"]["status"] == "partial_extraction"


def test_l_an_incomplete_record_on_any_path_is_never_complete() -> None:
    from rbac_backend.services.metadata_integrity import assess_metadata_quality

    metadata = ParsedDocumentMetadata(
        date="14-10-2024", letter_no="A/1", from_company="E", to_company="C", subject="S"
    )
    clean = assess_metadata_quality(metadata, metadata_source="openai_text_legacy_regex")
    assert clean["status"] == "complete"
    agent_cut = assess_metadata_quality(
        metadata,
        metadata_source="openai_text_legacy_regex",
        partial_failures={"pydantic_ai": _truncated("pydantic_ai_extraction", "").failure_record()},
    )
    assert agent_cut["status"] == "complete_with_warnings"
    assert "model_output_incomplete" in agent_cut["warnings"]


def test_trim_keeps_only_items_the_reply_finished() -> None:
    from rbac_backend.services.text_processing_service import TextProcessingService

    parser = TextProcessingService(SimpleNamespace(chunk_size=1000, chunk_overlap=100))
    trimmed, kept = parser.trim_incomplete_report(CUT_IN_ITEM_25)
    assert 25 not in kept and 24 in kept and "Full Content" not in trimmed
    trimmed, kept = parser.trim_incomplete_report(CUT_IN_ITEM_26)
    assert 25 in kept and 26 not in kept
    # A numbered paragraph inside the letter is not mistaken for an item.
    body = "25) Full Content: Dear Sir,\n1) Date: of the meeting: 12-08-2024\n2) The works"
    trimmed, kept = parser.trim_incomplete_report("5) Subject: X\n" + body)
    assert kept == [5]


def test_p_a_report_cut_off_before_its_advice_item_is_still_refused_for_vectors() -> None:
    """Phase B refused the report by its reply-advice heading. A reply cut off
    before Item 24 has no such heading and was indexed as the letter."""
    from rbac_backend.retrieval.correspondence_payload import (
        CorrespondencePayloadError,
        refuse_report_derived_text,
    )

    cut_before_advice = FULL_REPORT.split("\n22)")[0]
    with pytest.raises(CorrespondencePayloadError):
        refuse_report_derived_text("doc-1", cut_before_advice)
    # A letter listing its references by number is not the report.
    letter = "Dear Sir,\n1) Letter No.: A/1 dated 01-08-2024\n2) Letter No.: A/2\n3) Letter No.: A/3\nRegards"
    refuse_report_derived_text("doc-1", letter)


# ============================================================================
# Independent-review regressions
# ============================================================================


def test_review_a_letter_with_its_own_numbered_fields_is_not_the_report() -> None:
    """MEDIUM (review): a hindrance letter listing "1) Location / 2) Chainage
    From / 3) Chainage To" was refused for vectors as if it were the report."""
    from rbac_backend.retrieval.correspondence_payload import refuse_report_derived_text
    from rbac_backend.services.source_text import is_extraction_report_text

    letter = (
        "Sub: Hindrance at Bridge 4\n1) Location: Bridge 4\n2) Chainage From: 12+000\n"
        "3) Chainage To: 12+400\n4) Priority: urgent attention"
    )
    assert not is_extraction_report_text(letter)
    refuse_report_derived_text("doc-1", letter)
    assert authoritative_text(_doc(ocrText=letter, full_text=LLM)) == letter


def test_review_a_bold_legacy_report_without_advice_is_still_the_report() -> None:
    """LOW-MEDIUM (review): ``**1) Date:**`` reports escaped detection."""
    from rbac_backend.services.source_text import is_extraction_report_text

    bold = "**1) Date:** 14-10-2024\n**2) Letter No.:** EMP/118\n**5) Subject:** Delay\n**11) Full content:** x"
    assert is_extraction_report_text(bold)
    assert authoritative_text(_doc(ocrText=bold, full_text=LLM)) == LLM


def test_review_a_misclassified_letter_is_never_worse_than_the_old_order() -> None:
    """MEDIUM (review): a letter whose own lines match the report schema, on a
    row with no markers, fell through to the LLM summary. It must fall back to
    the pre-change order - ``full_text`` - never further."""
    letter = "1) Date: 14-10-2024\n2) Letter No.: EMP/118\n3) From: Employer\nDear Sir, ..."
    retyped = "1) Date: 14-10-2024\n2) Letter No.: EMP/118\n3) From: Employer\nDear Sir ..."
    row = _doc(ocrText=letter, full_text=retyped, summary=SUMMARY)
    assert authoritative_text(row) == retyped


@pytest.mark.asyncio
async def test_review_publication_payload_reads_the_body_from_the_row_as_written(monkeypatch) -> None:
    """MEDIUM (review): the evidence payload was a Document dumped before
    processing (ocrText None) overlaid on the fresh row, so evidence spans came
    from Item 25 although the source text was already stored."""
    import rbac_backend.services.publication_policy as policy
    from rbac_backend.services.document_service import DocumentService
    from rbac_backend.services.evidence_graph_service import _document_body

    fresh = _doc(ocrText=OCR, ocr_text_kind="source_text", full_text=LLM, full_text_source="llm_full_content")

    async def canonical(_db, _id):
        return dict(fresh)

    monkeypatch.setattr(policy, "resolve_canonical_document", canonical)
    service = DocumentService.__new__(DocumentService)

    async def get_db():
        return object()

    service._get_db = get_db
    stale_dump = {"ocrText": None, "ocr_text_kind": None, "full_text": LLM, "summary": SUMMARY}
    payload = await service._current_publication_payload("doc-1", stale_dump)
    assert payload["ocrText"] == OCR and payload["ocr_text_kind"] == "source_text"
    assert _document_body(payload, ParsedDocumentMetadata(full_content=LLM)) == OCR


@pytest.mark.asyncio
async def test_review_deterministic_fallback_body_is_labelled_source_under_pydantic(build, tmp_path) -> None:
    """LOW (review): OCR fallback + PydanticAI success stored raw OCR labelled
    as the LLM's Item 25."""
    from rbac_backend.services.pydantic_ai_service import MetadataAgentResult

    processor = build(OCR, _OpenAI(text_reply=RuntimeError("provider down")))

    async def extract_metadata(document_text, context=None):
        return MetadataAgentResult(
            metadata=ParsedDocumentMetadata(subject="Delay", letter_no="EMP/118"),
            raw_result={},
            debug={},
        )

    processor.pydantic_ai_service = SimpleNamespace(is_enabled=True, extract_metadata=extract_metadata)
    await _run(processor, tmp_path, unified=False)
    doc = processor.written
    assert doc["full_text"] == OCR and doc["full_text_source"] == "source_text"


def test_review_chronology_spans_never_read_the_extraction_report() -> None:
    """HIGH pre-existing (review): chronology read raw ``ocrText``, which for a
    no-source document is the report with its Key Reply Points."""
    from rbac_backend.services.chronology import ChronologyService

    service = ChronologyService.__new__(ChronologyService)
    no_source = service._document_text(_doc(subject="Delay", ocrText=REPORT, full_text=LLM))
    assert "Key Reply Points" not in no_source and LLM in no_source
    with_source = service._document_text(_doc(subject="Delay", ocrText=OCR, full_text=LLM))
    assert OCR in with_source and LLM not in with_source


def test_review_recorded_source_text_is_not_refused_by_report_shape() -> None:
    """LOW (re-review): a letter quoting another letter's particulars in the
    report's own numbering ("1) Date / 2) Letter No. / 3) From") is refused by
    shape - unless the writer recorded it as the extracted source."""
    from rbac_backend.retrieval.correspondence_payload import (
        CorrespondencePayloadError,
        refuse_report_derived_text,
    )

    letter = "Dear Sir,\n1) Date: 01-08-2024\n2) Letter No.: A/1\n3) From: Engineer\nRegards"
    with pytest.raises(CorrespondencePayloadError):
        refuse_report_derived_text("doc-1", letter)
    refuse_report_derived_text("doc-1", letter, recorded_source=True)
    # The advice heading is refused whatever the label says.
    with pytest.raises(CorrespondencePayloadError):
        refuse_report_derived_text("doc-1", REPORT, recorded_source=True)


# ============================================================================
# F/G on the exact installed SDK types (not duck-typed fakes)
# ============================================================================


def _sdk_response(status: str, reason: Optional[str], text: str):
    from openai.types.responses import Response, ResponseOutputMessage, ResponseOutputText
    from openai.types.responses.response import IncompleteDetails

    message = ResponseOutputMessage(
        id="msg-1",
        type="message",
        role="assistant",
        status="incomplete" if status == "incomplete" else "completed",
        content=[ResponseOutputText(type="output_text", text=text, annotations=[])],
    )
    return Response.model_construct(
        id="resp-1",
        object="response",
        status=status,
        incomplete_details=IncompleteDetails(reason=reason) if reason else None,
        output=[message],
    )


def _sdk_chat(finish_reason: str, text: str):
    from openai.types.chat import ChatCompletion

    return ChatCompletion.model_validate(
        {
            "id": "chat-1",
            "object": "chat.completion",
            "created": 0,
            "model": "gpt-4o",
            "choices": [
                {"index": 0, "finish_reason": finish_reason, "message": {"role": "assistant", "content": text}}
            ],
        }
    )


def _client_returning(obj, *, chat: bool):
    async def create(**_kwargs):
        return obj

    if chat:
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return SimpleNamespace(responses=SimpleNamespace(create=create))


def test_sdk_responses_incomplete_max_output_tokens_is_detected() -> None:
    from rbac_backend.utils.exceptions import ModelOutputIncompleteError

    response = _sdk_response("incomplete", "max_output_tokens", f"25) Full Content: {SECRET}")
    assert response.output_text.endswith(SECRET)  # the real SDK property the service reads
    with pytest.raises(ModelOutputIncompleteError) as caught:
        asyncio.run(_openai_service(_client_returning(response, chat=False)).process_document("f"))
    assert caught.value.truncated and SECRET not in str(caught.value)
    complete = _sdk_response("completed", None, FULL_REPORT)
    assert asyncio.run(_openai_service(_client_returning(complete, chat=False)).process_document("f")) == FULL_REPORT


def test_sdk_chat_length_is_detected_and_stop_is_unchanged() -> None:
    from rbac_backend.utils.exceptions import ModelOutputIncompleteError

    with pytest.raises(ModelOutputIncompleteError) as caught:
        asyncio.run(
            _openai_service(_client_returning(_sdk_chat("length", SECRET), chat=True)).process_text("t", filename="x")
        )
    assert caught.value.truncated and SECRET not in str(caught.value)
    ok = _openai_service(_client_returning(_sdk_chat("stop", METADATA_REPORT), chat=True))
    assert asyncio.run(ok.process_text("t", filename="x")) == METADATA_REPORT



def test_h_pydantic_limit_before_any_output_is_a_visible_truncation() -> None:
    """LOW (final review): pydantic-ai raises a plain UnexpectedModelBehavior
    when the cap is hit before any output; that too is a truncation."""
    from pydantic_ai.messages import ModelResponse

    from rbac_backend.utils.exceptions import ModelOutputIncompleteError

    def nothing(messages, info):
        return ModelResponse(parts=[], finish_reason="length")

    service = _pydantic_service(nothing)
    with pytest.raises(ModelOutputIncompleteError) as caught:
        asyncio.run(service.extract_metadata("letter text"))
    assert caught.value.truncated
