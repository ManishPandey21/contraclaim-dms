"""Upload-to-retrieval integration proof (improvement #8):

upload (seeded OCR pages) -> ClauseChunkingAgent.process_document ->
contract_clauses populated -> clause vectors searchable in the
"contract_clauses" namespace -> RetrievalService serves contract requests from
structured clauses FIRST (document_vectors only as fallback) -> the QA/drafting
citation path (SoC/SoD/claim workflows) cites those clause records.

Uses the real VectorClient in offline mode (its in-memory index mirrors the
Qdrant namespace semantics) and a deterministic fake embedding client.
"""

from __future__ import annotations

import pytest

from rbac_backend.config.document_processing_config import DocumentProcessingConfig
from rbac_backend.retrieval.models import (
    SearchBackend,
    SearchFilters,
    SearchRequest,
)
from rbac_backend.retrieval.service import RetrievalService
from rbac_backend.retrieval.vector_client import VectorClient
from rbac_backend.services.contract_clause import ClauseChunkingAgent
from rbac_backend.services.contract_clause.embedding_service import ClauseEmbeddingService


# --------------------------------------------------------------------------- #
# Fakes
# --------------------------------------------------------------------------- #
class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def limit(self, _n):
        return self

    def sort(self, *_a, **_k):
        return self

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class FakeCollection:
    def __init__(self, name=""):
        self.name = name
        self.docs = {}
        self.inserted = []

    async def create_index(self, *a, **k):
        return None

    def find(self, filt):
        out = []
        for doc in self.docs.values():
            ok = True
            for key, expected in (filt or {}).items():
                if expected is None:
                    continue
                if isinstance(expected, dict):
                    continue  # ignore operators in this double
                if doc.get(key) != expected:
                    ok = False
                    break
            if ok:
                out.append(dict(doc))
        return _Cursor(out)

    async def find_one(self, filt):
        if "clause_uid" in filt:
            d = self.docs.get(filt["clause_uid"])
            return dict(d) if d else None
        if "_id" in filt:
            wanted = filt["_id"]
            wanted_set = wanted.get("$in") if isinstance(wanted, dict) else [wanted]
            for doc in self.docs.values():
                if doc.get("_id") in [str(w) for w in wanted_set] or doc.get("_id") in wanted_set:
                    return dict(doc)
            return None
        for doc in self.docs.values():
            return dict(doc)
        return None

    async def update_one(self, filt, update, upsert=False):
        uid = filt.get("clause_uid") or filt.get("_id")
        if uid in self.docs:
            self.docs[uid].update(update.get("$set", {}))
        elif upsert:
            self.docs[uid] = {
                "clause_uid": uid,
                **update.get("$setOnInsert", {}),
                **update.get("$set", {}),
            }

    async def insert_one(self, doc):
        self.inserted.append(doc)

        class _R:
            inserted_id = "x"

        return _R()


class FakeDB:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection(name))

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]


class FakeEmbeddingClient:
    """Deterministic keyword-hash embeddings so cosine ranks the right clause."""

    _AXES = ["extension", "time", "variation", "payment", "termination", "boq"]

    async def embed(self, texts, model=None):
        vectors = []
        for text in texts:
            lowered = (text or "").lower()
            vec = [1.0 if axis in lowered else 0.0 for axis in self._AXES]
            vec.append(1.0)  # bias so no vector is all-zero
            vectors.append(vec)
        return vectors


class _Obs:
    async def log_run(self, **kwargs):
        return None


class _Gen:
    async def generate(self, *a, **k):
        return "n/a"


OCR_PAGE_TEXT = (
    "8. GENERAL PROVISIONS\n"
    "8.4 Extension of Time for Completion\n"
    "The Contractor shall be entitled to an extension of time if completion is delayed.\n"
    "13.3 Variation Procedure\n"
    "The Engineer may initiate a variation by instruction and the payment shall be adjusted.\n"
)


def _seed_upload(db: FakeDB):
    db["documents"].docs["doc-1"] = {
        "_id": "doc-1",
        "organization_id": "org-A",
        "project_id": "proj-A",
        "contract_id": "ct-1",
        "filename": "Vol-2-GCC.pdf",
        "contract_categories": ["GCC"],
        "uploadType": "contract",
        "status": "completed",
    }
    db["contract_ocr_pages"].docs["p1"] = {
        "_id": "p1",
        "document_id": "doc-1",
        "page_number": 1,
        "cleaned_text": OCR_PAGE_TEXT,
        "raw_text": OCR_PAGE_TEXT,
        "status": "text_layer",
    }


def _offline_vector_client() -> VectorClient:
    config = DocumentProcessingConfig()
    config.qdrant_url = None  # force offline: in-memory namespace-aware index
    return VectorClient(config)


async def _index_document(db: FakeDB, vector_client: VectorClient) -> None:
    embedder = FakeEmbeddingClient()
    agent = ClauseChunkingAgent(
        db=db,
        embedding_service=ClauseEmbeddingService(db, embedder, vector_client),
    )
    summary = await agent.process_document(current_user=None, document_id="doc-1")
    assert summary.clauses_detected >= 2
    assert summary.records_written.get("embedded", 0) >= 2


def _service(db: FakeDB, vector_client: VectorClient) -> RetrievalService:
    return RetrievalService(
        db=db,
        embedding_client=FakeEmbeddingClient(),
        vector_client=vector_client,
        llm_generator=_Gen(),
        observability=_Obs(),
    )


def _contract_request(query: str, backend: SearchBackend) -> SearchRequest:
    # Mirrors what the contract-qa router sends (uploadType/document_type pins).
    return SearchRequest(
        query=query,
        limit=5,
        backend=backend,
        filters=SearchFilters(
            org_id="org-A",
            project_id="proj-A",
            document_id="doc-1",
            metadata={"uploadType": "contract", "document_type": "contract"},
        ),
    )


# --------------------------------------------------------------------------- #
# Upload -> contract_clauses -> clause vectors searchable
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_upload_populates_contract_clauses_and_clause_vectors():
    db = FakeDB()
    _seed_upload(db)
    vc = _offline_vector_client()
    await _index_document(db, vc)

    # Structured records saved with scope + hierarchy + pages.
    clauses = {d.get("clause_no"): d for d in db["contract_clauses"].docs.values()}
    assert "8.4" in clauses and "13.3" in clauses
    assert clauses["8.4"]["org_id"] == "org-A"
    assert clauses["8.4"]["page_start"] == 1

    # Clause vectors live in the contract_clauses namespace with req-17 payload.
    q = (await FakeEmbeddingClient().embed(["extension of time"]))[0]
    hits = await vc.search(q, filters={"org_id": "org-A"}, limit=3, namespace="contract_clauses")
    assert hits, "clause vectors must be searchable in the contract_clauses namespace"
    top = hits[0]["payload"]
    assert top.get("clause_no") == "8.4"
    assert top.get("document_id") == "doc-1"
    assert top.get("is_current") is True


# --------------------------------------------------------------------------- #
# Retrieval prefers structured clauses; document_vectors is fallback only
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_vector_retrieval_serves_contract_requests_from_clauses_first():
    db = FakeDB()
    _seed_upload(db)
    vc = _offline_vector_client()
    await _index_document(db, vc)

    # Decoy token-chunk in the LEGACY namespace: if retrieval used the old
    # path first, this would come back instead of the clause record.
    legacy_vec = (await FakeEmbeddingClient().embed(["extension of time legacy chunk"]))[0]
    await vc.upsert(
        [legacy_vec],
        [{
            "chunk_id": "legacy-1",
            "org_id": "org-A",
            "project_id": "proj-A",
            "document_id": "doc-1",
            "text": "legacy blind token chunk",
            "metadata": {"uploadType": "contract", "document_type": "contract"},
        }],
    )

    service = _service(db, vc)
    response = await service.search(
        _contract_request("extension of time", SearchBackend.QDRANT), None, log_run=False
    )
    assert response.results, "clause-first retrieval returned nothing"
    top = response.results[0]
    assert top.payload.get("clause_number") == "8.4"
    assert top.chunk_id != "legacy-1"
    assert all(r.chunk_id != "legacy-1" for r in response.results)


@pytest.mark.asyncio
async def test_vector_retrieval_falls_back_to_legacy_chunks_without_clauses():
    db = FakeDB()
    _seed_upload(db)
    vc = _offline_vector_client()  # no clause indexing run

    legacy_vec = (await FakeEmbeddingClient().embed(["extension of time"]))[0]
    await vc.upsert(
        [legacy_vec],
        [{
            "chunk_id": "legacy-1",
            "org_id": "org-A",
            "project_id": "proj-A",
            "document_id": "doc-1",
            "text": "legacy chunk about extension of time",
            # Real legacy chunks carry these inside metadata (vector_client only
            # merges extra payload fields from chunk["metadata"]).
            "metadata": {"uploadType": "contract", "document_type": "contract"},
        }],
    )

    service = _service(db, vc)
    response = await service.search(
        _contract_request("extension of time", SearchBackend.QDRANT), None, log_run=False
    )
    assert response.results
    assert response.results[0].chunk_id == "legacy-1"


@pytest.mark.asyncio
async def test_mongo_backend_prefers_clause_records_over_document_vectors():
    db = FakeDB()
    _seed_upload(db)
    vc = _offline_vector_client()
    await _index_document(db, vc)

    # Decoy document_vectors row (legacy Mongo source).
    db["document_vectors"].docs["dv-1"] = {
        "_id": "dv-1",
        "uploadType": "contract",
        "organization_id": "org-A",
        "project_id": "proj-A",
        "document_id": "doc-1",
        "chunk_id": "dv-1",
        "text": "extension of time legacy document_vectors row",
    }

    service = _service(db, vc)
    response = await service.search(
        _contract_request("extension of time", SearchBackend.MONGO), None, log_run=False
    )
    assert response.results
    top = response.results[0]
    assert top.payload.get("clause_number") == "8.4"
    assert all(r.chunk_id != "dv-1" for r in response.results)


# --------------------------------------------------------------------------- #
# Drafting / claim / SoC / SoD citation path cites clause records
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_citation_path_cites_structured_clauses():
    db = FakeDB()
    _seed_upload(db)
    vc = _offline_vector_client()
    await _index_document(db, vc)

    service = _service(db, vc)
    response = await service.search(
        _contract_request("extension of time", SearchBackend.QDRANT), None, log_run=False
    )

    citation_map = service._build_citation_map(response.results)
    assert citation_map, "citation map must not be empty"
    ids = " ".join(str(entry.get("id")) for entry in citation_map.values())
    assert "8.4" in ids  # QA/drafting cite the clause identity, not a blind chunk

    citations = service._to_citations(response.results, {})
    assert citations
    assert citations[0].clause_number == "8.4"
    assert citations[0].page == 1
    assert citations[0].document_id == "doc-1"
