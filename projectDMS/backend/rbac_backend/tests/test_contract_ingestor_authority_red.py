"""G31 ContractIngestor publication-authority regressions.

The public worker seam is ``ContractIngestor.ingest_file``.  Contract parsing,
clause extraction, payload construction, Mongo persistence, and the real
``ContractGraphService`` remain in the path.  Mongo, embedding, Qdrant, and
FalkorDB are persistent system-boundary fakes so assertions inspect final store
state rather than collaborator call counts.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from rbac_backend.services.contract_graph_service import ContractGraphService
from rbac_backend.services.contracts_ingest import (
    ClauseExtractor,
    ContractIngestor,
    DatabaseService,
    IngestionConfig,
    ParsedDocument,
    ParsedPage,
)


DOCUMENT_ID = "contract-ingestor-authority-document"
QDRANT_MARKER = "CONTRACT_INGESTOR_QDRANT_BLOCKED_20260819"
FALKOR_MARKER = "CONTRACT_INGESTOR_FALKOR_BLOCKED_20260819"
MONGO_MARKER = "CONTRACT_INGESTOR_MONGO_BLOCKED_20260819"
SOURCE_TEXT = (
    "1.1 General Obligations\n"
    f"The Contractor shall preserve {QDRANT_MARKER}, {FALKOR_MARKER}, and "
    f"{MONGO_MARKER} while coordinating viaduct interfaces and approvals. "
    "The Engineer shall review the submitted records before construction.\n"
)

STATIC_AUTHORITY_MATRIX = [
    ("clean", {}, True),
    ("operational_failed", {"processing_status": "failed"}, True),
    ("human_review", {"processing_status": "human_review_required"}, False),
    ("duplicate_status", {"duplicate_status": "duplicate"}, False),
    ("duplicate_lifecycle", {"lifecycle_state": "duplicate"}, False),
    ("deleted", {"lifecycle_state": "deleted"}, False),
    ("missing_canonical", None, False),
]

TOCTOU_AUTHORITY_MATRIX = [
    ("human_review", {"processing_status": "human_review_required"}),
    ("duplicate", {"duplicate_status": "duplicate"}),
    ("deleted", {"lifecycle_state": "deleted"}),
    ("missing_canonical", None),
]


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, expected in query.items():
        actual = document.get(key)
        if isinstance(expected, dict) and "$in" in expected:
            if actual not in expected["$in"]:
                return False
        elif actual != expected:
            return False
    return True


class _Collection:
    def __init__(self, documents=()) -> None:
        self.documents = [deepcopy(document) for document in documents]

    async def create_index(self, *_args, **_kwargs):
        return None

    async def find_one(self, query, *_args, **_kwargs):
        for document in self.documents:
            if _matches(document, query):
                return deepcopy(document)
        return None

    async def update_one(self, query, update, upsert=False):
        for document in self.documents:
            if _matches(document, query):
                document.update(deepcopy(update.get("$set", {})))
                return SimpleNamespace(matched_count=1, modified_count=1, upserted_id=None)
        if upsert:
            inserted = {
                **deepcopy(query),
                **deepcopy(update.get("$setOnInsert", {})),
                **deepcopy(update.get("$set", {})),
            }
            self.documents.append(inserted)
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id="upserted")
        return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=None)

    async def delete_many(self, query):
        retained = [document for document in self.documents if not _matches(document, query)]
        deleted = len(self.documents) - len(retained)
        self.documents = retained
        return SimpleNamespace(deleted_count=deleted)

    async def insert_many(self, documents):
        self.documents.extend(deepcopy(list(documents)))
        return SimpleNamespace(inserted_ids=list(range(len(documents))))


class _Database:
    def __init__(self, documents=()) -> None:
        self.documents = _Collection(documents)
        self.document_vectors = _Collection()
        self.contract_ingest_jobs = _Collection()
        self.contract_ocr_batches = _Collection()
        self.contract_ocr_pages = _Collection()
        self.organizations = _Collection([{"_id": "org-authority", "name": "Authority Org"}])
        self.projects = _Collection([{"_id": "project-authority", "name": "Authority Project"}])


class _ParserBoundary:
    async def extract_text(self, file_path: Path) -> ParsedDocument:
        return ParsedDocument(
            text=SOURCE_TEXT,
            pages=[ParsedPage(number=1, text=SOURCE_TEXT, start=0, end=len(SOURCE_TEXT))],
            file_path=str(file_path),
        )


class _MarkerBoundary:
    async def extract_markdown(self, _file_path: Path, _upload_id: str):
        return None


class _CategorizerBoundary:
    async def categorize(self, _text: str, _org_name: str | None, _project_name: str | None):
        return ["GCC"]


class _EmbeddingBoundary:
    def __init__(
        self,
        transition: Callable[[], None] | None = None,
        *,
        fail_once: bool = False,
    ) -> None:
        self.transition = transition
        self.fail_once = fail_once

    async def embed(self, texts, model=None):
        if self.transition is not None:
            transition, self.transition = self.transition, None
            transition()
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("deterministic contract embedding boundary failure")
        return [[float(len(text)), 1.0] for text in texts]


class _PersistentQdrant:
    enabled = True

    def __init__(self, after_write: Callable[[], None] | None = None) -> None:
        self.points: dict[str, dict[str, Any]] = {}
        self.after_write = after_write

    async def upsert(self, vectors, chunks, namespace=None) -> int:
        for vector, chunk in zip(vectors, chunks):
            self.points[str(chunk["chunk_id"])] = {
                "vector": deepcopy(vector),
                "payload": deepcopy(chunk),
                "namespace": namespace,
            }
        if self.after_write is not None:
            after_write, self.after_write = self.after_write, None
            after_write()
        return len(chunks)

    def document_points(self) -> list[dict[str, Any]]:
        return [
            point
            for point in self.points.values()
            if str(point["payload"].get("document_id")) == DOCUMENT_ID
        ]

    def marker_present(self) -> bool:
        return any(
            QDRANT_MARKER in str(point["payload"].get("text") or "")
            for point in self.document_points()
        )


class _PersistentFalkor:
    enabled = True

    def __init__(self) -> None:
        self.statements: list[tuple[str, dict[str, Any]]] = []

    def _execute(self, cypher: str, params=None, **_kwargs):
        self.statements.append((cypher, deepcopy(params or {})))

    def marker_present(self) -> bool:
        return any(
            FALKOR_MARKER in str(params.get("text_content") or "")
            for _cypher, params in self.statements
        )

    def has_contract_publication(self) -> bool:
        has_document = any("MERGE (d:Document" in cypher for cypher, _params in self.statements)
        has_clause = any("MERGE (c:Clause" in cypher for cypher, _params in self.statements)
        return has_document and has_clause


def _canonical_document(**overrides) -> dict[str, Any]:
    document = {
        "_id": DOCUMENT_ID,
        "organization_id": "org-authority",
        "project_id": "project-authority",
        "uploadType": "contract",
        "processing_status": "completed",
        "duplicate_status": "unique",
        "lifecycle_state": "active",
    }
    document.update(overrides)
    return document


def _change_authority(db: _Database, new_authority: dict[str, Any] | None) -> None:
    if new_authority is None:
        db.documents.documents.clear()
    else:
        db.documents.documents[0].update(deepcopy(new_authority))


def _mongo_marker_present(db: _Database) -> bool:
    return any(
        str(record.get("document_id")) == DOCUMENT_ID
        and MONGO_MARKER in str(record.get("text") or "")
        for record in db.document_vectors.documents
    )


def _make_ingestor(
    db: _Database,
    *,
    embedding_transition: Callable[[], None] | None = None,
    qdrant_after_write: Callable[[], None] | None = None,
    embedding_fail_once: bool = False,
) -> tuple[ContractIngestor, _PersistentQdrant, _PersistentFalkor]:
    qdrant = _PersistentQdrant(after_write=qdrant_after_write)
    falkor = _PersistentFalkor()
    ingestor = ContractIngestor.__new__(ContractIngestor)
    ingestor.config = IngestionConfig(MIN_CHUNK_LENGTH=1, EMBEDDING_BATCH_SIZE=16)
    ingestor.processing_config = SimpleNamespace(
        contract_text_cleaning_enabled=False,
        contract_ai_chunking_enabled=False,
        qdrant_enabled=True,
    )
    ingestor.db_service = DatabaseService(db)
    ingestor.parser = _ParserBoundary()
    ingestor.text_preprocessor = None
    ingestor.clause_extractor = ClauseExtractor()
    ingestor.vector_service = None
    ingestor.marker_service = _MarkerBoundary()
    ingestor.clause_worker = SimpleNamespace(enabled=False)
    ingestor.embedding_client = _EmbeddingBoundary(
        transition=embedding_transition,
        fail_once=embedding_fail_once,
    )
    ingestor.vector_client = qdrant
    ingestor.categorizer = _CategorizerBoundary()
    ingestor.contract_graph = ContractGraphService(falkor=falkor)
    ingestor._indexes_ready = False
    return ingestor, qdrant, falkor


async def _ingest(ingestor: ContractIngestor, source_path: Path) -> dict[str, Any]:
    return await ingestor.ingest_file(
        organization_id="org-authority",
        project_id="project-authority",
        file_path=str(source_path),
        filename="authority-contract.txt",
        tags=["authority"],
        upload_id="contract-ingestor-authority-upload",
        document_id=DOCUMENT_ID,
    )


@pytest.fixture
def contract_source(tmp_path: Path) -> Path:
    source_path = tmp_path / "authority-contract.txt"
    source_path.write_text(SOURCE_TEXT, encoding="utf-8")
    return source_path


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "authority", "should_publish"),
    STATIC_AUTHORITY_MATRIX,
    ids=[case for case, _authority, _expected in STATIC_AUTHORITY_MATRIX],
)
async def test_ingest_file_publishes_only_static_authoritative_contracts(
    case: str,
    authority: dict[str, Any] | None,
    should_publish: bool,
    contract_source: Path,
):
    db = _Database([] if authority is None else [_canonical_document(**authority)])
    ingestor, qdrant, falkor = _make_ingestor(db)

    await _ingest(ingestor, contract_source)

    assert qdrant.marker_present() is should_publish, case
    assert (len(qdrant.document_points()) > 0) is should_publish, case
    assert falkor.marker_present() is should_publish, case
    assert falkor.has_contract_publication() is should_publish, case
    assert _mongo_marker_present(db) is should_publish, case


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "new_authority"),
    TOCTOU_AUTHORITY_MATRIX,
    ids=[case for case, _authority in TOCTOU_AUTHORITY_MATRIX],
)
async def test_ingest_file_rechecks_authority_after_embedding_before_publication(
    case: str,
    new_authority: dict[str, Any] | None,
    contract_source: Path,
):
    db = _Database([_canonical_document()])
    ingestor, qdrant, falkor = _make_ingestor(
        db,
        embedding_transition=lambda: _change_authority(db, new_authority),
    )

    await _ingest(ingestor, contract_source)

    assert qdrant.marker_present() is False, case
    assert len(qdrant.document_points()) == 0, case
    assert falkor.marker_present() is False, case
    assert falkor.has_contract_publication() is False, case
    assert _mongo_marker_present(db) is False, case


@pytest.mark.asyncio
async def test_later_stores_recheck_authority_if_it_changes_after_qdrant(
    contract_source: Path,
):
    db = _Database([_canonical_document()])
    ingestor, qdrant, falkor = _make_ingestor(
        db,
        qdrant_after_write=lambda: _change_authority(
            db, {"processing_status": "human_review_required"}
        ),
    )

    await _ingest(ingestor, contract_source)

    assert qdrant.marker_present() is True
    assert falkor.marker_present() is False
    assert falkor.has_contract_publication() is False
    assert _mongo_marker_present(db) is False


@pytest.mark.asyncio
async def test_same_contract_reentry_does_not_republish_after_authority_is_denied(
    contract_source: Path,
):
    db = _Database([_canonical_document()])
    ingestor, qdrant, falkor = _make_ingestor(db, embedding_fail_once=True)

    first_result = await _ingest(ingestor, contract_source)
    _change_authority(db, {"duplicate_status": "duplicate"})
    second_result = await _ingest(ingestor, contract_source)

    assert first_result["ok"] is False
    assert second_result["ok"] is False
    assert qdrant.marker_present() is False
    assert len(qdrant.document_points()) == 0
    assert falkor.marker_present() is False
    assert falkor.has_contract_publication() is False
    assert _mongo_marker_present(db) is False
