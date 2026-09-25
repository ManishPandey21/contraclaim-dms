"""A service-level harness for page-retry regressions.

Drives the real chain - ``DocumentService.process_document_job`` ->
``DocumentProcessor.process_document`` -> ``OCRService.process_pdf_pagewise``
-> ``PageExtractionEngine`` -> ``DocumentPageStore`` -> the persistence and
embedding seam - over an in-memory Mongo double. Only OCR itself, OpenAI, the
file system summary write and the graph/reference publication are stubbed, so
a defect in how retries are merged, stored or indexed shows up here rather
than being papered over by a mocked extraction result.

Deliberately not a unit harness: the defect this exists for lives between the
engine's merge, the page store's upsert and the checkpoint, and every
component-level test passed while the chain lost text.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence

from bson import ObjectId
from pymongo import ReplaceOne

from rbac_backend.config.document_processing_config import DocumentProcessingConfig
from rbac_backend.services import document_service as document_service_module
from rbac_backend.services.document_processor import DocumentProcessor
from rbac_backend.services.document_service import DocumentService
from rbac_backend.services.extraction.quality.gate import ExtractionQualityGate
from rbac_backend.services.extraction.source_kind import SourceKindRouter
from rbac_backend.services.ocr_service import OCRService
from rbac_backend.services.pipeline_routing import UNIFIED_PIPELINE
from rbac_backend.services.text_processing_service import TextProcessingService
from rbac_backend.tests.fixtures.pdf_builders import build_page_text_map_pdf
from rbac_backend.tests.test_document_processing_jobs import (
    insert_document,
    make_document,
)


# --- In-memory Mongo double ---------------------------------------------


def _field(document: Dict[str, Any], key: str) -> Any:
    """Mongo's dotted-path read: ``a.b`` is ``document["a"]["b"]``."""
    value: Any = document
    for part in key.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _matches(document: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for key, expected in (query or {}).items():
        if key == "$or":
            if not any(_matches(document, branch) for branch in expected):
                return False
            continue
        if key == "$and":
            if not all(_matches(document, branch) for branch in expected):
                return False
            continue
        actual = _field(document, key)
        if isinstance(expected, dict):
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            if "$exists" in expected and (key in document) != expected["$exists"]:
                return False
            if "$lte" in expected and (actual is None or actual > expected["$lte"]):
                return False
            continue
        if actual != expected:
            return False
    return True


class _Cursor:
    def __init__(self, documents: List[Dict[str, Any]]) -> None:
        self._documents = documents

    def sort(self, key: Any, direction: int = 1) -> "_Cursor":
        if isinstance(key, list):
            key, direction = key[0]
        self._documents.sort(key=lambda item: item.get(key), reverse=direction < 0)
        return self

    def limit(self, count: int) -> "_Cursor":
        self._documents = self._documents[:count]
        return self

    async def to_list(self, length: Optional[int] = None) -> List[Dict[str, Any]]:
        return [dict(document) for document in self._documents[:length]]

    def __aiter__(self) -> "_Cursor":
        self._iterator = iter(list(self._documents))
        return self

    async def __anext__(self) -> Dict[str, Any]:
        try:
            return dict(next(self._iterator))
        except StopIteration as stop:  # pragma: no cover - iterator protocol
            raise StopAsyncIteration from stop


class FakeCollection:
    def __init__(self) -> None:
        self.docs: Dict[Any, Dict[str, Any]] = {}

    async def insert_one(self, document: Dict[str, Any]) -> SimpleNamespace:
        stored = dict(document)
        inserted_id = stored.get("_id") or ObjectId()
        stored["_id"] = inserted_id
        self.docs[inserted_id] = stored
        return SimpleNamespace(inserted_id=inserted_id)

    async def find_one(
        self,
        query: Dict[str, Any],
        projection: Any = None,
        sort: Any = None,
        session: Any = None,
    ) -> Optional[Dict[str, Any]]:
        matches = [doc for doc in self.docs.values() if _matches(doc, query)]
        if sort:
            key, direction = sort[0]
            matches.sort(
                key=lambda item: item.get(key) or datetime.min, reverse=direction < 0
            )
        return dict(matches[0]) if matches else None

    def find(
        self, query: Dict[str, Any], projection: Any = None, session: Any = None
    ) -> _Cursor:
        return _Cursor([doc for doc in self.docs.values() if _matches(doc, query)])

    async def count_documents(self, query: Dict[str, Any], **kwargs: Any) -> int:
        query = dict(query or {})
        page_filter = query.pop("page_number", None)
        matches = [doc for doc in self.docs.values() if _matches(doc, query)]
        if isinstance(page_filter, dict) and "$in" in page_filter:
            allowed = set(page_filter["$in"])
            matches = [doc for doc in matches if doc.get("page_number") in allowed]
        elif page_filter is not None:
            matches = [doc for doc in matches if doc.get("page_number") == page_filter]
        return len(matches)

    async def update_one(
        self,
        query: Dict[str, Any],
        update: Dict[str, Any],
        upsert: bool = False,
        session: Any = None,
    ) -> SimpleNamespace:
        for key, document in self.docs.items():
            if _matches(document, query):
                for field, value in update.get("$set", {}).items():
                    document[field] = value
                for field, value in update.get("$inc", {}).items():
                    document[field] = document.get(field, 0) + value
                self.docs[key] = document
                return SimpleNamespace(matched_count=1, modified_count=1)
        if upsert:
            document = {
                key: value
                for key, value in (query or {}).items()
                if not isinstance(value, dict)
            }
            document.update(update.get("$set", {}))
            await self.insert_one(document)
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    async def find_one_and_update(
        self,
        query: Dict[str, Any],
        update: Dict[str, Any],
        sort: Any = None,
        return_document: Any = None,
    ) -> Optional[Dict[str, Any]]:
        found = await self.find_one(query, sort=sort)
        if not found:
            return None
        await self.update_one({"_id": found["_id"]}, update)
        return await self.find_one({"_id": found["_id"]})

    async def delete_many(self, query: Dict[str, Any]) -> SimpleNamespace:
        removed = [key for key, doc in self.docs.items() if _matches(doc, query)]
        for key in removed:
            del self.docs[key]
        return SimpleNamespace(deleted_count=len(removed))

    async def bulk_write(self, operations: Sequence[Any]) -> SimpleNamespace:
        for operation in operations:
            query = getattr(operation, "_filter")
            replacement = dict(getattr(operation, "_doc"))
            existing = [
                key for key, doc in self.docs.items() if _matches(doc, query)
            ]
            if existing:
                key = existing[0]
                replacement["_id"] = key
                self.docs[key] = replacement
            elif getattr(operation, "_upsert", False):
                await self.insert_one(replacement)
        return SimpleNamespace(acknowledged=True)


class FakeDb:
    """Attribute and item access both resolve to the same collections."""

    def __init__(self) -> None:
        self.collections: Dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection())

    def __getattr__(self, name: str) -> FakeCollection:
        if name.startswith("_"):
            raise AttributeError(name)
        return self.__getitem__(name)


# --- Stubbed edges -------------------------------------------------------


class ScriptedOcrRunner:
    """OCR that answers from a per-attempt script and records what it was asked.

    A page mapped to an exception raises for that batch (OCR_FAILED); a page
    absent from the attempt's map returns no text (OCR_EMPTY); anything else
    returns the scripted text.
    """

    def __init__(self, script: Dict[int, Dict[int, Any]]) -> None:
        self.script = script
        self.attempt = 0
        self.requested: List[List[int]] = []
        self.requested_by_attempt: Dict[int, List[List[int]]] = {}

    def start_attempt(self, attempt: int) -> None:
        self.attempt = attempt
        self.requested_by_attempt.setdefault(attempt, [])

    async def run(
        self, source: Path, page_numbers: Sequence[int], language: str
    ) -> Dict[int, str]:
        pages = list(page_numbers)
        self.requested.append(pages)
        self.requested_by_attempt.setdefault(self.attempt, []).append(pages)
        answers = self.script.get(self.attempt, {})
        result: Dict[int, str] = {}
        for page in pages:
            value = answers.get(page)
            if isinstance(value, BaseException):
                raise value
            if value is not None:
                result[page] = value
        return result


class _PagewiseOcr:
    """The real OCRService with availability and the runner pinned."""

    def __init__(self, *, runner: Any, min_text_chars: int, batch_size: int) -> None:
        config = DocumentProcessingConfig()
        config.ocr_enabled = True
        config.contract_ocr_min_text_chars_per_page = min_text_chars
        config.contract_ocr_batch_size = batch_size
        self.service = OCRService.__new__(OCRService)
        self.service.config = config
        self.service._ocr_available = True
        self.runner = runner

    async def process_pdf_pagewise(self, path: Path, **kwargs: Any) -> Any:
        kwargs["ocr_runner"] = self.runner
        return await self.service.process_pdf_pagewise(path, **kwargs)


class FakeDatabaseService:
    """Captures exactly what reaches the persistence/embedding seam."""

    def __init__(self, db: FakeDb) -> None:
        self.db = db
        self.saved: List[Dict[str, Any]] = []
        self.deferred_embeddings: List[str] = []
        self.partial_failures: Dict[str, Any] = {}

    async def get_database(self) -> FakeDb:
        return self.db

    async def save_document_data(self, **kwargs: Any) -> int:
        self.saved.append(dict(kwargs))
        return 1

    async def create_embeddings_for_document(self, document_id: str) -> None:
        self.deferred_embeddings.append(document_id)

    async def close_connection(self) -> None:
        return None


class _OpenAI:
    """No network: metadata comes from the processor's own OCR-text report."""

    def __init__(self, processor_ref: Dict[str, Any]) -> None:
        self.processor_ref = processor_ref
        self.texts: List[str] = []
        self.uploads: List[str] = []

    async def process_text(self, text: str, *, filename: str) -> str:
        self.texts.append(text)
        processor: DocumentProcessor = self.processor_ref["processor"]
        return processor._build_ocr_fallback_report(text, filename=filename)

    async def upload_file(self, path: str) -> str:
        self.uploads.append(path)
        return "file-1"

    async def process_document(self, file_id: str) -> str:
        return "REPORT<uploaded>"

    async def cleanup_file(self, file_id: str) -> None:
        return None


class CountingLadder:
    """A fallback ladder that only records that it was asked to spend."""

    def __init__(self) -> None:
        self.calls: List[int] = []

    async def resolve(self, source: Path, page: Any, verdict: Any, *, document_id: str):
        self.calls.append(page.number)
        from rbac_backend.services.extraction.fallback.models import (
            FallbackOutcome,
            FallbackResolution,
        )

        return FallbackResolution(outcome=FallbackOutcome.UNRESOLVED, page=page)


# --- The harness ---------------------------------------------------------


class RetryHarness:
    def __init__(
        self,
        *,
        db: FakeDb,
        service: DocumentService,
        job_id: str,
        document_id: ObjectId,
        runner: ScriptedOcrRunner,
        source: Path,
        min_text_chars: int,
        batch_size: int,
        fallback_ladder: Any = None,
        escalate_pages: Optional[set] = None,
    ) -> None:
        self.db = db
        self.service = service
        self.job_id = job_id
        self.document_id = document_id
        self.runner = runner
        self.source = source
        self.min_text_chars = min_text_chars
        self.batch_size = batch_size
        self.fallback_ladder = fallback_ladder
        self.escalate_pages = set(escalate_pages or ())
        self.database_services: List[FakeDatabaseService] = []
        self.attempt = 0
        self.gate_assessed: List[List[int]] = []

    @classmethod
    async def create(
        cls,
        tmp_path: Path,
        monkeypatch: Any,
        *,
        page_lines: List[Optional[List[str]]],
        script: Dict[int, Dict[int, Any]],
        min_text_chars: int = 10,
        batch_size: int = 1,
        fallback_ladder: Any = None,
        escalate_pages: Optional[set] = None,
    ) -> "RetryHarness":
        source = build_page_text_map_pdf(
            tmp_path / "letter.pdf", page_lines=page_lines
        )
        db = FakeDb()
        document_id = ObjectId()
        document = make_document(document_id)
        await insert_document(db, document, document_id)

        service = DocumentService(db)
        job_id = await service.queue_document_processing(document, str(source))
        # The unified pipeline is the one that owns page-level retries; pin it
        # on the job rather than on global configuration.
        await db.document_processing_jobs.update_one(
            {"_id": job_id}, {"$set": {"pipeline_version": UNIFIED_PIPELINE}}
        )

        harness = cls(
            db=db,
            service=service,
            job_id=job_id,
            document_id=document_id,
            runner=ScriptedOcrRunner(script),
            source=source,
            min_text_chars=min_text_chars,
            batch_size=batch_size,
            fallback_ladder=fallback_ladder,
            escalate_pages=escalate_pages,
        )
        harness._install(monkeypatch)
        return harness

    def _install(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(
            document_service_module, "create_document_processor", self._processor
        )

        async def _publish(*args: Any, **kwargs: Any) -> bool:
            return False

        monkeypatch.setattr(
            self.service, "_publish_graph_and_evidence_from_current", _publish
        )

    def _processor(self, config: Any = None) -> DocumentProcessor:
        database_service = FakeDatabaseService(self.db)
        self.database_services.append(database_service)

        processor = DocumentProcessor.__new__(DocumentProcessor)
        processor_ref: Dict[str, Any] = {}
        processor.config = DocumentProcessingConfig()
        processor.config.contract_ocr_min_text_chars_per_page = self.min_text_chars
        processor.config.contract_ocr_batch_size = self.batch_size
        processor.ocr_service = _PagewiseOcr(
            runner=self.runner,
            min_text_chars=self.min_text_chars,
            batch_size=self.batch_size,
        )
        processor.openai_service = _OpenAI(processor_ref)
        processor.text_service = TextProcessingService(processor.config)
        processor.database_service = database_service
        processor.file_service = SimpleNamespace(save_summary=_async_none)
        processor.pydantic_ai_service = SimpleNamespace(is_enabled=False)
        processor.quality_gate = _RecordingGate(self.gate_assessed, self.escalate_pages)
        processor.fallback_ladder = self.fallback_ladder
        processor.fallback_max_pages_per_document = 5 if self.fallback_ladder else 0
        processor.source_kind_router = SourceKindRouter
        processor.image_ocr_runner = object()
        processor._image_extractor = None
        processor._text_extractor = None
        ladder = self.fallback_ladder
        processor.resolve_fallback_ladder = lambda kind, db=None: ladder
        processor_ref["processor"] = processor
        return processor

    async def run_attempt(self) -> bool:
        self.attempt += 1
        self.runner.start_attempt(self.attempt)
        self.gate_assessed.append([])
        return await self.service.process_document_job(self.job_id)

    # --- evidence readers ------------------------------------------------

    async def page_rows(self) -> List[Dict[str, Any]]:
        rows = await self.db["document_ocr_pages"].find({}).to_list(None)
        return sorted(rows, key=lambda row: row["page_number"])

    async def page_state(self) -> Dict[int, Dict[str, Any]]:
        return {
            row["page_number"]: {
                "text": row.get("raw_text"),
                "source": row.get("source"),
                "status": row.get("status"),
                "extraction_run_id": row.get("extraction_run_id"),
                "quality_verdict": row.get("quality_verdict"),
                "needs_review": row.get("needs_review"),
                "applied_repairs": row.get("applied_repairs"),
                "batch_id": row.get("batch_id"),
                "error": row.get("error"),
            }
            for row in await self.page_rows()
        }

    async def job(self) -> Dict[str, Any]:
        return await self.db.document_processing_jobs.find_one({"_id": self.job_id})

    async def document(self) -> Dict[str, Any]:
        return await self.db.documents.find_one({"_id": self.document_id})

    def saves(self) -> List[Dict[str, Any]]:
        return [
            call
            for service in self.database_services
            for call in service.saved
        ]

    def last_save(self) -> Dict[str, Any]:
        saves = self.saves()
        assert saves, "persistence was never reached"
        return saves[-1]

    def full_text(self) -> str:
        return str(self.last_save().get("full_text") or "")

    def embedding_text(self) -> str:
        return str(self.last_save().get("embedding_text") or "")


class _RecordingGate(ExtractionQualityGate):
    """The real gate; records which page numbers it was asked to assess.

    `escalate` forces a FAIL for the named pages, which is how a test reaches
    the review path without having to hand-build a document the deterministic
    checks happen to reject.
    """

    def __init__(self, sink: List[List[int]], escalate: Optional[set] = None) -> None:
        super().__init__()
        self._sink = sink
        self._escalate = set(escalate or ())

    def assess(self, page: Any, *, tables: Any = None) -> Any:
        if self._sink:
            self._sink[-1].append(page.number)
        verdict = super().assess(page, tables=tables)
        if page.number in self._escalate:
            from rbac_backend.services.extraction.quality.models import (
                CheckResult,
                QualityVerdict,
                Verdict,
            )

            return QualityVerdict(
                verdict=Verdict.FAIL,
                checks=[
                    CheckResult(
                        name="row_identity",
                        verdict=Verdict.FAIL,
                        detail="forced by the harness",
                    )
                ],
                reasons=["forced by the harness"],
            )
        return verdict


async def _async_none(*args: Any, **kwargs: Any) -> None:
    return None
