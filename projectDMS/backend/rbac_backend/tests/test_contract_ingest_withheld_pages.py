"""A contract page the extraction engine withheld must not vanish from a completed contract.

The engine (PR #25) withholds a page whose native text is unusable ``(cid:N)``
placeholders, and reports it in ``withheld_pages``/``completeness``. The general
document path turns that verdict into a processing state; the contract path
read only ``result.pages``. A mixed contract - a readable page, a withheld page,
another readable page - therefore extracted to the two readable pages, published
their clauses, reported ``completed`` and stayed evidence-ready in Contract
Master with a page's content silently gone.

These tests drive the service path an upload or an OCR retry takes -
``ContractService.process_ingest_job`` -> ``ContractIngestor.ingest_file`` ->
``PageExtractionEngine`` -> ``ContractPageStore`` -> clause payloads, Qdrant,
``document_vectors`` - over an in-memory Mongo double, with a real PDF. Only
OCR, embeddings, the vector store, the graph and clause-index side effects are
stubbed. Contract Master readiness is read through the router's own decorator
and the real scope resolver.

    A  a withheld page cannot produce an evidence-ready completed contract
    B  good + withheld + good does not silently complete
    C  ``(cid:N)`` text reaches no published text, vector or evidence result
    D  a later retry that resolves the page completes the contract cumulatively
    E  pages an earlier attempt resolved are preserved across the retry,
       including when a stale retry list names them
    F  a fully readable contract still completes normally
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Sequence, cast

import pikepdf
import pytest
from bson import ObjectId

from rbac_backend.config.document_processing_config import DocumentProcessingConfig
from rbac_backend.routers import contract_master_api
from rbac_backend.services.contract_graph_service import ContractGraphService
from rbac_backend.services.contract_scope_resolver import CONTRACT_DOCUMENTS_COLLECTION
from rbac_backend.services.contract_service import ContractService
from rbac_backend.services.contracts_ingest import (
    ClauseExtractor,
    ContractIngestor,
    ContractTextPreprocessor,
    DatabaseService,
    IngestionConfig,
)
from rbac_backend.services.extraction import ocrmypdf_runner as ocrmypdf_runner_module
from rbac_backend.services.publication_policy import resolve_document_authority
from rbac_backend.utils.error_handler import ContractError
from rbac_backend.tests.fixtures.pdf_builders import (
    build_composite_font_pdf,
    build_image_page_pdf,
    build_page_text_map_pdf,
)
from rbac_backend.tests.retry_harness import FakeCollection, FakeDb
from rbac_backend.tests.test_cl4a_release_extraction_cross_regression import (
    ORG,
    PROJECT,
    _evidence,
    _Policy,
    _seed_contract_master,
)
from rbac_backend.tests.test_contract_ingestor_authority_red import (
    _EmbeddingBoundary,
    _PersistentFalkor,
    _PersistentQdrant,
)

CID = re.compile(r"\(cid:\d+\)")

PAGE_ONE = [
    "1.1 General Obligations",
    "The Contractor shall execute the Works in accordance with the Contract",
    "and shall provide all labour, materials and plant PAGEONEMARKER.",
]
PAGE_TWO_CID = [
    "2.1 Payment Terms",
    "The Employer shall pay the Contractor within twenty eight days of the",
    "certificate issued by the Engineer for each interim application.",
]
PAGE_THREE = [
    "3.1 Time for Completion",
    "The Contractor shall complete the whole of the Works within the Time",
    "for Completion stated in the Contract Data PAGETHREEMARKER.",
]
OCR_TWO = (
    "2.1 Payment Terms\nThe Employer shall pay the Contractor within twenty "
    "eight days of the certificate PAGETWOOCRMARKER."
)
OCR_THREE = (
    "3.1 Time for Completion\nThe Contractor shall complete the Works within "
    "the Time for Completion PAGETHREEOCRMARKER."
)


# --- fixtures -------------------------------------------------------------


def _splice(path: Path, parts: Sequence[Path]) -> Path:
    """One PDF whose pages are the single pages of `parts`, in order."""
    out = pikepdf.new()
    sources = [pikepdf.open(part) for part in parts]
    try:
        for source in sources:
            out.pages.extend(source.pages)
        out.save(path, deterministic_id=True)
    finally:
        for source in sources:
            source.close()
    return path


def _contract_pdf(
    tmp_path: Path, *, cid_page: bool, scanned_third: bool = False, image_third: bool = False
) -> Path:
    one = build_page_text_map_pdf(tmp_path / "p1.pdf", page_lines=[PAGE_ONE])
    if cid_page:
        two = build_composite_font_pdf(tmp_path / "p2.pdf", composite_lines=PAGE_TWO_CID)
    else:
        two = build_page_text_map_pdf(tmp_path / "p2.pdf", page_lines=[PAGE_TWO_CID])
    if image_third:
        three = build_image_page_pdf(tmp_path / "p3.pdf")
    else:
        three = build_page_text_map_pdf(
            tmp_path / "p3.pdf", page_lines=[None if scanned_third else PAGE_THREE]
        )
    return _splice(tmp_path / "contract.pdf", [one, two, three])


class _ScriptedOcr:
    """OcrMyPdfRunner stand-in answering from a per-attempt script.

    A page mapped to an exception fails its batch; a page absent from the
    current attempt's answers comes back empty.
    """

    script: Dict[int, Dict[int, Any]] = {}
    attempt = 1
    requested: List[List[int]] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass

    async def run(self, source: Path, page_numbers: Sequence[int], language: str) -> Dict[int, str]:
        pages = list(page_numbers)
        type(self).requested.append(pages)
        answers = type(self).script.get(type(self).attempt, {})
        result: Dict[int, str] = {}
        for page in pages:
            value = answers.get(page)
            if isinstance(value, BaseException):
                raise value
            if value is not None:
                result[page] = value
        return result


async def _noop(**_: Any) -> None:
    return None


class _Collection(FakeCollection):
    async def insert_many(self, documents: Sequence[Dict[str, Any]]) -> SimpleNamespace:
        ids = [(await self.insert_one(document)).inserted_id for document in documents]
        return SimpleNamespace(inserted_ids=ids)


class _Db(FakeDb):
    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, _Collection())


class _Config(DocumentProcessingConfig):
    # A read-only property on the real config; Qdrant dual-write is on here so
    # the zero-vector guard and the vector publication both stay in the path.
    qdrant_enabled = True  # type: ignore[assignment]


class _Harness:
    def __init__(self, tmp_path: Path, monkeypatch: Any, *, ocr_enabled: bool) -> None:
        from rbac_backend.services import contracts_ingest as contracts_ingest_module

        monkeypatch.setattr(contracts_ingest_module, "BASE_UPLOAD_PATH", tmp_path)
        monkeypatch.setattr(ocrmypdf_runner_module, "OcrMyPdfRunner", _ScriptedOcr)
        _ScriptedOcr.requested = []
        _ScriptedOcr.attempt = 1

        self.db = _Db()
        self.document_id = ObjectId()
        self.upload_id = "upload-withheld"
        self.qdrant = _PersistentQdrant()
        self.falkor = _PersistentFalkor()
        self.clause_index_runs: List[str] = []
        self.source = tmp_path / "contract.pdf"

        config = _Config()
        config.ocr_enabled = ocr_enabled
        config.contract_text_cleaning_enabled = True
        config.contract_ai_chunking_enabled = False
        # One page per OCR batch, so one page's OCR failure cannot fail its
        # neighbour and each page's outcome is its own.
        config.contract_ocr_batch_size = 1

        # Test doubles stand in for the boundary clients, so the object is Any.
        ingestor: Any = ContractIngestor.__new__(ContractIngestor)
        ingestor.config = IngestionConfig(MIN_CHUNK_LENGTH=1, EMBEDDING_BATCH_SIZE=16)
        ingestor.processing_config = config
        ingestor.db_service = DatabaseService(self.db)
        ingestor.text_preprocessor = ContractTextPreprocessor()
        ingestor.clause_extractor = ClauseExtractor()
        ingestor.vector_service = None
        ingestor.marker_service = SimpleNamespace(extract_markdown=self._no_marker)
        ingestor.clause_worker = SimpleNamespace(enabled=False)
        ingestor.embedding_client = _EmbeddingBoundary()
        ingestor.vector_client = self.qdrant
        ingestor.categorizer = SimpleNamespace(categorize=self._categorize)
        ingestor.contract_graph = ContractGraphService(falkor=cast(Any, self.falkor))
        ingestor.usage_metering_service = SimpleNamespace(check_and_record=_noop)
        ingestor._indexes_ready = True

        service: Any = ContractService.__new__(ContractService)
        service._db = self.db
        service._jobs = self.db.contract_ingest_jobs
        service._documents = self.db.documents
        service._vectors = self.db.document_vectors
        service._ingestor = ingestor
        service._run_clause_indexing_safe = self._record_clause_indexing
        # The job deletes a processing file that sits under the temp dir, which
        # pytest's tmp_path does; the retry needs the same source again.
        service._cleanup_tmp_file = lambda _path: None
        self.service = service

    @staticmethod
    async def _no_marker(*_: Any) -> None:
        return None

    @staticmethod
    async def _categorize(*_: Any) -> List[str]:
        return ["GCC"]

    async def _record_clause_indexing(self, document_id: str) -> None:
        self.clause_index_runs.append(document_id)

    @classmethod
    async def create(
        cls,
        tmp_path: Path,
        monkeypatch: Any,
        *,
        cid_page: bool = True,
        scanned_third: bool = False,
        image_third: bool = False,
        ocr_enabled: bool = True,
        script: Optional[Dict[int, Dict[int, Any]]] = None,
    ) -> "_Harness":
        harness = cls(tmp_path, monkeypatch, ocr_enabled=ocr_enabled)
        _ScriptedOcr.script = script or {}
        harness.source = _contract_pdf(
            tmp_path, cid_page=cid_page, scanned_third=scanned_third, image_third=image_third
        )
        await harness.db.documents.insert_one(
            {
                "_id": harness.document_id,
                "organization_id": ORG,
                "project_id": PROJECT,
                "uploadType": "contract",
                "filename": "contract.pdf",
                "status": "queued",
                "contract_upload_id": harness.upload_id,
                "duplicate_status": "unique",
                "lifecycle_state": "active",
            }
        )
        await _seed_contract_master(harness.db, harness.document_id)
        return harness

    async def ingest(self, *, retry_ocr_pages: Optional[List[int]] = None, attempt: int = 1) -> None:
        _ScriptedOcr.attempt = attempt
        payload: Dict[str, Any] = {
            "upload_id": self.upload_id,
            "document_id": str(self.document_id),
            "organization_id": ORG,
            "project_id": PROJECT,
            "filename": "contract.pdf",
            "tags": [],
            "processing_path": str(self.source),
        }
        if retry_ocr_pages:
            payload["retry_ocr_pages"] = retry_ocr_pages
        await self.service.process_ingest_job(payload)

    async def job(self) -> Dict[str, Any]:
        return await self.db.contract_ingest_jobs.find_one({"upload_id": self.upload_id}) or {}

    async def document(self) -> Dict[str, Any]:
        return await self.db.documents.find_one({"_id": self.document_id}) or {}

    async def page_rows(self) -> Dict[int, Dict[str, Any]]:
        rows = await self.db.contract_ocr_pages.find({"document_id": str(self.document_id)}).to_list()
        return {int(row["page_number"]): row for row in rows}

    def published_texts(self) -> List[str]:
        """Every text the contract published: vectors, Mongo chunks, graph."""
        texts = [str(point["payload"].get("text") or "") for point in self.qdrant.points.values()]
        texts += [str(row.get("text") or "") for row in self.db.document_vectors.docs.values()]
        texts += [str(params.get("text_content") or "") for _cypher, params in self.falkor.statements]
        return texts

    async def published_page_texts(self) -> List[str]:
        """What the clause agent and the pages view read from each row."""
        rows = await self.page_rows()
        return [
            str(row.get(field) or "")
            for row in rows.values()
            for field in ("raw_text", "cleaned_text")
        ]

    async def reprojected(self) -> None:
        """The completed ingest withdrew the projection; stand in for its rebuild.

        A completed ingest rewrites the document's rows, so it moves the
        instrument back to PENDING and the contract-worker rebuilds it
        (``test_contract_reprojection_runtime_mongo`` drives that real path on a
        replica set). Here the rebuild is stood in for by the CURRENT stamp alone.
        """
        record = await self.db[CONTRACT_DOCUMENTS_COLLECTION].find_one({"_id": "cd-1"})
        assert record is not None
        assert record["projection_status"] == "PENDING", "the ingest must withdraw the projection"
        assert await self.evidence_ready() is False
        await self.db[CONTRACT_DOCUMENTS_COLLECTION].update_one(
            {"_id": "cd-1"}, {"$set": {"projection_status": "CURRENT", "projection_revision": 1}}
        )

    async def evidence_ready(self) -> bool:
        record = await self.db[CONTRACT_DOCUMENTS_COLLECTION].find_one({"_id": "cd-1"})
        assert record is not None
        decorated = await contract_master_api._decorate(self.db, record)
        return bool(decorated["evidence_ready"])

    async def evidence_document_ids(self) -> frozenset:
        resolved = await _evidence(self.db, _Policy(self.db), ORG, PROJECT)
        return resolved.eligible_document_ids


def _assert_no_cid(texts: Sequence[str]) -> None:
    leaked = [text for text in texts if CID.search(text)]
    assert not leaked, f"(cid:N) text reached publication: {leaked[:2]}"


# --- A, B, C: a withheld page cannot complete the contract ------------------

UNRESOLVED_CASES = [
    # OCR switched off: the withheld page is ocr_disabled.
    ("ocr_disabled", False, {}),
    # OCR ran and read nothing: the withheld page is ocr_empty.
    ("ocr_empty", True, {1: {}}),
    # OCR failed: the withheld page is ocr_failed.
    ("ocr_failed", True, {1: {2: RuntimeError("OCRmyPDF batch failed (exit=2)")}}),
]


@pytest.mark.parametrize(
    ("case", "ocr_enabled", "script"),
    UNRESOLVED_CASES,
    ids=[case for case, _ocr, _script in UNRESOLVED_CASES],
)
async def test_withheld_contract_page_blocks_completion_and_evidence(
    case: str, ocr_enabled: bool, script: Dict[int, Dict[int, Any]], tmp_path: Path, monkeypatch: Any
) -> None:
    harness = await _Harness.create(tmp_path, monkeypatch, ocr_enabled=ocr_enabled, script=script)

    await harness.ingest()

    pages = await harness.page_rows()
    # The engine did withhold page 2, and pages 1 and 3 are readable.
    assert pages[2]["status"] == case
    assert pages[2]["text_withheld"] is True
    assert (pages[2]["raw_text"], pages[2]["cleaned_text"]) == ("", "")
    assert "PAGEONEMARKER" in pages[1]["raw_text"]
    assert "PAGETHREEMARKER" in pages[3]["raw_text"]

    # B: the contract is not reported complete - to the job, the Document, or
    # the contract register.
    job = await harness.job()
    document = await harness.document()
    assert job["status"] == "human_review_required", job
    assert job.get("unresolved_pages") == [2]
    assert job.get("withheld_pages") == [2]
    assert document["status"] == "human_review_required"
    assert document["processing_status"] == "human_review_required"
    assert document["processing_error"]["pages"] == [2]
    assert document["processing_error"]["source"] == "contract_extraction"

    # A: the one publication predicate denies it, so Contract Master does too.
    authority = await resolve_document_authority(harness.db, str(harness.document_id))
    assert authority.consumable is False
    assert await harness.evidence_ready() is False
    assert await harness.evidence_document_ids() == frozenset()

    # A partial contract publishes nothing: no clause index run, no vectors,
    # no Mongo chunks, no graph clauses built from two thirds of the text.
    assert harness.clause_index_runs == []
    assert harness.published_texts() == []

    # C: whatever was written, no placeholder is in it.
    _assert_no_cid(await harness.published_page_texts())

    # The page is where an operator retry finds it.
    assert await harness.service.get_failed_ocr_pages(str(harness.document_id)) == [2]


# --- F: a fully readable contract still completes ---------------------------


async def test_fully_readable_contract_completes_and_is_evidence(
    tmp_path: Path, monkeypatch: Any
) -> None:
    harness = await _Harness.create(tmp_path, monkeypatch, cid_page=False)

    await harness.ingest()

    job = await harness.job()
    document = await harness.document()
    assert job["status"] == "completed", job
    assert document["status"] == "completed"
    assert document["processing_status"] == "completed"
    assert harness.clause_index_runs == [str(harness.document_id)]
    published = " ".join(harness.published_texts())
    assert "PAGEONEMARKER" in published and "PAGETHREEMARKER" in published
    await harness.reprojected()
    assert await harness.evidence_ready() is True
    assert await harness.evidence_document_ids() == frozenset({str(harness.document_id)})
    assert _ScriptedOcr.requested == []


# --- D, E: retry completes the contract and keeps resolved pages ------------


@pytest.mark.parametrize("stale_retry_list", [False, True], ids=["retry", "stale_checkpoint"])
async def test_retry_resolves_withheld_page_and_preserves_resolved_ocr(
    stale_retry_list: bool, tmp_path: Path, monkeypatch: Any
) -> None:
    """Attempt 1: page 1 native, page 2 CID + OCR failure, page 3 scanned + OCR'd.

    Attempt 2's script has no answer for page 3, so had the retry re-OCR'd the
    page attempt 1 resolved, its text would come back empty.
    """
    harness = await _Harness.create(
        tmp_path,
        monkeypatch,
        scanned_third=True,
        script={
            1: {2: RuntimeError("OCRmyPDF batch failed (exit=2)"), 3: OCR_THREE},
            2: {2: OCR_TWO},
        },
    )

    await harness.ingest()
    assert (await harness.job())["status"] == "human_review_required"
    first = await harness.page_rows()
    assert (first[3]["status"], first[3]["raw_text"]) == ("ocr_completed", OCR_THREE)
    assert first[2]["status"] == "ocr_failed"

    retry = await harness.service.get_failed_ocr_pages(str(harness.document_id))
    assert retry == [2]
    if stale_retry_list:
        # A stale list naming a page the run already resolved must not reopen it.
        retry = [2, 3]
    _ScriptedOcr.requested = []
    await harness.ingest(retry_ocr_pages=retry, attempt=2)

    # E: only the unresolved page went back to OCR; resolved rows are untouched.
    assert _ScriptedOcr.requested == [[2]]
    final = await harness.page_rows()
    assert (final[3]["status"], final[3]["raw_text"]) == ("ocr_completed", OCR_THREE)
    assert (final[1]["status"], final[1]["raw_text"]) == (first[1]["status"], first[1]["raw_text"])
    assert final[2]["status"] == "ocr_completed"
    assert final[2].get("text_withheld") is False

    # D: the contract completes, and what it published is the whole run.
    job = await harness.job()
    document = await harness.document()
    assert job["status"] == "completed", job
    assert document["processing_status"] == "completed"
    assert document["status"] == "completed"
    assert harness.clause_index_runs == [str(harness.document_id)]
    published = " ".join(harness.published_texts())
    for marker in ("PAGEONEMARKER", "PAGETWOOCRMARKER", "PAGETHREEOCRMARKER"):
        assert marker in published, marker
    full_text = "\n".join(
        str(row.get("cleaned_text") or "") for _n, row in sorted(final.items())
    )
    assert (
        full_text.index("PAGEONEMARKER")
        < full_text.index("PAGETWOOCRMARKER")
        < full_text.index("PAGETHREEOCRMARKER")
    )
    _assert_no_cid(harness.published_texts())
    _assert_no_cid(await harness.published_page_texts())
    await harness.reprojected()
    assert await harness.evidence_ready() is True
    assert await harness.evidence_document_ids() == frozenset({str(harness.document_id)})


# --- The hold is this contract's own, and only its own ---------------------


@pytest.mark.parametrize(
    "foreign",
    [
        # A review verdict someone other than contract extraction wrote.
        {"processing_error": {"source": "quality_gate", "pages": [1]}},
        # Contract extraction's hold, but the document is also quarantined.
        {"duplicate_status": "duplicate"},
    ],
    ids=["other_review_source", "held_and_duplicate"],
)
async def test_an_adverse_verdict_extraction_did_not_place_is_never_lifted(
    foreign: Dict[str, Any], tmp_path: Path, monkeypatch: Any
) -> None:
    harness = await _Harness.create(tmp_path, monkeypatch, cid_page=False)
    await harness.db.documents.update_one(
        {"_id": harness.document_id},
        {
            "$set": {
                "processing_status": "human_review_required",
                "processing_error": {"source": "contract_extraction", "pages": [2]},
                **foreign,
            }
        },
    )

    with pytest.raises(ContractError, match="not authoritative"):
        await harness.ingest()

    document = await harness.document()
    assert document["processing_status"] == "human_review_required"
    assert harness.published_texts() == []
    assert harness.clause_index_runs == []


async def test_legacy_and_malformed_contract_rows_load_or_fail_closed() -> None:
    from rbac_backend.services.extraction.page_store import InconsistentExtractionRunError
    from rbac_backend.services.extraction_adapters.contract_page_store import (
        ResumableContractPageStore,
        from_contract_page_record,
    )

    # A row written before text_withheld/source/page_class existed.
    legacy = from_contract_page_record(
        {"page_number": 3, "status": "ocr_completed", "raw_text": OCR_THREE, "batch_id": "b"}
    )
    assert (legacy.status.value, legacy.source.value, legacy.text) == (
        "ocr_completed",
        "ocr",
        OCR_THREE,
    )
    assert legacy.text_withheld is False

    db = _Db()
    await db.contract_ocr_pages.insert_one(
        {"document_id": "d", "page_number": 1, "status": "text_layer", "raw_text": "x"}
    )
    await db.contract_ocr_pages.insert_one(
        {"document_id": "d", "page_number": 2, "status": "not-a-status", "raw_text": ""}
    )
    store = ResumableContractPageStore(
        db_service=DatabaseService(db),
        document_id="d",
        upload_id="u",
        organization_id=ORG,
        project_id=PROJECT,
    )
    with pytest.raises(InconsistentExtractionRunError) as refused:
        await store.load_run_pages()
    assert refused.value.missing_page_numbers == [2]


async def test_retry_page_list_skips_unrenderable_pages(tmp_path: Path, monkeypatch: Any) -> None:
    harness = await _Harness.create(tmp_path, monkeypatch, cid_page=False)
    for number, status, withheld in [
        (1, "text_layer", False),
        (2, "ocr_pending", True),
        (3, "unrenderable", False),
        (4, "ocr_deferred", False),
        (5, "text_layer", True),
    ]:
        await harness.db.contract_ocr_pages.insert_one(
            {
                "document_id": str(harness.document_id),
                "page_number": number,
                "status": status,
                "text_withheld": withheld,
            }
        )
    assert await harness.service.get_failed_ocr_pages(str(harness.document_id)) == [2, 4, 5]


# --- Blank pages are empty, scans are not ----------------------------------


UNREADABLE_OCR = " ".join(f"(cid:{n})" for n in range(80))


@pytest.mark.parametrize(
    ("image_third", "ocr_answer", "expected"),
    [
        (False, None, "completed"),
        (True, None, "human_review_required"),
        # OCR found something on the "blank" page (e.g. text drawn as vector
        # outlines) but could not read it: content exists, so the page is held.
        (False, UNREADABLE_OCR, "human_review_required"),
    ],
    ids=["blank_page_settles", "scanned_page_held", "blank_page_unreadable_ocr_held"],
)
async def test_empty_ocr_settles_a_blank_page_but_not_a_scan(
    image_third: bool,
    ocr_answer: Optional[str],
    expected: str,
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    """OCR yields no usable text on page 3 in every case; what differs is why.

    A blank page (no text layer, no image) that OCR confirms empty has no
    content to lose. A raster page that reads empty does, and so does a blank
    page whose OCR returned text nobody can read.
    """
    harness = await _Harness.create(
        tmp_path,
        monkeypatch,
        cid_page=False,
        scanned_third=not image_third,
        image_third=image_third,
        script={1: {} if ocr_answer is None else {3: ocr_answer}},
    )

    await harness.ingest()

    pages = await harness.page_rows()
    assert pages[3]["status"] == "ocr_empty"
    assert pages[3]["page_class"] == ("scanned_image" if image_third else "blank")
    assert (await harness.job())["status"] == expected
    assert (await harness.document())["processing_status"] == expected
    retry = await harness.service.get_failed_ocr_pages(str(harness.document_id))
    assert retry == ([] if expected == "completed" else [3])
    if expected != "completed":
        assert await harness.evidence_ready() is False
        _assert_no_cid(await harness.published_page_texts())
        assert harness.published_texts() == []


# --- The hold outlives a failed publication ---------------------------------


class _ZeroWriteQdrant(_PersistentQdrant):
    """A vector store that accepts the call and writes nothing (the 401 shape)."""

    async def upsert(self, vectors: Any, chunks: Any, namespace: Any = None) -> int:
        return 0


async def test_a_retry_that_fails_to_publish_leaves_the_contract_held(
    tmp_path: Path, monkeypatch: Any
) -> None:
    harness = await _Harness.create(
        tmp_path,
        monkeypatch,
        script={1: {2: RuntimeError("OCRmyPDF batch failed (exit=2)")}, 2: {2: OCR_TWO}},
    )
    await harness.ingest()
    assert (await harness.document())["processing_status"] == "human_review_required"

    # Every page now resolves, but the vector store writes nothing.
    harness.service._ingestor.vector_client = _ZeroWriteQdrant()
    with pytest.raises(ContractError, match="Vector indexing wrote 0"):
        await harness.ingest(retry_ocr_pages=[2], attempt=2)

    document = await harness.document()
    assert document["processing_status"] == "human_review_required"
    assert document["processing_error"]["source"] == "contract_extraction"
    assert (await resolve_document_authority(harness.db, str(harness.document_id))).consumable is False
    assert await harness.evidence_ready() is False
    assert harness.clause_index_runs == []


# --- An explicit retry is honoured or refused, never silently ignored -------


async def test_ocr_retry_refuses_pages_with_no_unresolved_work(
    tmp_path: Path, monkeypatch: Any
) -> None:
    harness = await _Harness.create(
        tmp_path, monkeypatch, script={1: {2: RuntimeError("OCRmyPDF batch failed (exit=2)")}}
    )
    await harness.ingest()
    document_id = str(harness.document_id)

    assert await harness.service.resolve_ocr_retry_pages(document_id, []) == [2]
    assert await harness.service.resolve_ocr_retry_pages(document_id, [2]) == [2]
    for requested in ([1], [2, 3], [99]):
        with pytest.raises(ContractError) as refused:
            await harness.service.resolve_ocr_retry_pages(document_id, requested)
        assert refused.value.http_status == 422


async def test_explicit_retry_without_a_recorded_run_extracts_every_page(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """No page rows to continue: listing page 2 must not rebuild page 3 empty."""
    harness = await _Harness.create(
        tmp_path, monkeypatch, cid_page=False, scanned_third=True, script={1: {3: OCR_THREE}}
    )

    await harness.ingest(retry_ocr_pages=[2])

    pages = await harness.page_rows()
    assert (pages[3]["status"], pages[3]["raw_text"]) == ("ocr_completed", OCR_THREE)
    assert (await harness.job())["status"] == "completed"


# --- No other writer can clear or be relabelled as the hold -----------------


async def test_the_hold_never_relabels_another_writers_review_verdict(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from rbac_backend.services.contracts_ingest import ContractExtractionVerdict
    from rbac_backend.models.processing_state import ProcessingState

    harness = await _Harness.create(tmp_path, monkeypatch)
    foreign = {"source": "quality_gate", "pages": [7]}
    await harness.db.documents.update_one(
        {"_id": harness.document_id},
        {"$set": {"processing_status": "human_review_required", "processing_error": foreign}},
    )

    await harness.service._ingestor._hold_for_review(
        ContractExtractionVerdict(
            state=ProcessingState.HUMAN_REVIEW_REQUIRED,
            unresolved_pages=[2],
            withheld_pages=[2],
            run_page_records=[],
        ),
        upload_id=harness.upload_id,
        document_id=str(harness.document_id),
        file_path=harness.source,
        filename="contract.pdf",
        organization_id=ORG,
        project_id=PROJECT,
        tags=[],
        file_size=None,
    )

    document = await harness.document()
    assert document["processing_status"] == "human_review_required"
    assert document["processing_error"] == foreign


async def test_general_reprocessing_cannot_clear_a_contract_hold(
    tmp_path: Path, monkeypatch: Any
) -> None:
    from rbac_backend.services.document_service import DocumentService

    harness = await _Harness.create(
        tmp_path, monkeypatch, script={1: {2: RuntimeError("OCRmyPDF batch failed (exit=2)")}}
    )
    await harness.ingest()
    before = await harness.document()
    assert before["processing_status"] == "human_review_required"

    service: Any = DocumentService.__new__(DocumentService)

    async def _get_db() -> Any:
        return harness.db

    service._get_db = _get_db
    processed = await service.process_document_async(
        str(harness.document_id), str(harness.source), upload_type="contract"
    )

    assert processed is False
    assert await harness.document() == before


async def test_reprocess_route_refuses_a_held_contract_before_touching_it(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Both /documents/{id}/process routes go through DocumentController.process_document."""
    from rbac_backend.routers.documents import DocumentController
    from rbac_backend.utils.error_handler import DocumentError

    harness = await _Harness.create(
        tmp_path, monkeypatch, script={1: {2: RuntimeError("OCRmyPDF batch failed (exit=2)")}}
    )
    await harness.ingest()
    stored = await harness.document()
    started: List[str] = []

    async def _get_document(_document_id: str) -> Any:
        return SimpleNamespace(**stored)

    async def _process(**kwargs: Any) -> bool:
        started.append(kwargs["document_id"])
        return True

    controller: Any = DocumentController.__new__(DocumentController)
    controller.document_service = SimpleNamespace(
        get_document=_get_document, process_document_async=_process
    )

    async def _materialize(_document: Any) -> str:
        started.append("materialized")
        return str(harness.source)

    controller._materialize_for_processing = _materialize

    with pytest.raises(DocumentError) as refused:
        await controller.process_document(str(harness.document_id), None, skip_authorization=True)

    assert refused.value.http_status == 409
    assert started == []
    assert await harness.document() == stored
