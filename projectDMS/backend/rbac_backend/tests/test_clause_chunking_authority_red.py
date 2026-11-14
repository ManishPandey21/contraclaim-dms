"""G31 authority barrier at the public contract-clause indexing route.

The test keeps the endpoint, ClauseChunkingAgent, extractor, Mongo clause
persistence, ClauseEmbeddingService, and ClauseGraphService real.  Only the
external stores are represented by persistent in-memory boundaries so the
assertions can inspect their final state rather than call counts.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from rbac_backend.core.database import get_db
from rbac_backend.core.security import get_current_user
from rbac_backend.routers import contract_clauses
from rbac_backend.services.contract_clause.agent import ClauseChunkingAgent
from rbac_backend.services.contract_clause.embedding_service import ClauseEmbeddingService
from rbac_backend.services.contract_clause.graph_service import ClauseGraphService
from rbac_backend.tests.selection_fixtures import pin_selection


QDRANT_MARKER = "CLAUSE_BLOCKED_QDRANT_MARKER_20260819"
FALKOR_MARKER = "CLAUSE_BLOCKED_FALKOR_MARKER_20260819"
SOURCE_TEXT = (
    f"8.4 {FALKOR_MARKER}\n"
    f"The Contractor {QDRANT_MARKER} shall comply with the Engineer's instruction.\n"
    "13.3 Variation Procedure\n"
    "The Engineer may initiate a variation by instruction.\n"
)


class _Cursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents

    def __aiter__(self):
        self._iterator = iter(self._documents)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _Collection:
    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}
        self.inserted: list[dict[str, Any]] = []

    async def create_index(self, *_args, **_kwargs):
        return None

    def find(self, query: dict[str, Any]):
        matches = []
        for document in self.documents.values():
            if all(document.get(key) == value for key, value in query.items()):
                matches.append(deepcopy(document))
        return _Cursor(matches)

    async def find_one(self, query: dict[str, Any]):
        if "clause_uid" in query:
            document = self.documents.get(str(query["clause_uid"]))
            return deepcopy(document) if document is not None else None
        if "_id" in query:
            wanted = query["_id"]
            for document in self.documents.values():
                if document.get("_id") == wanted or str(document.get("_id")) == str(wanted):
                    return deepcopy(document)
            return None
        for document in self.documents.values():
            return deepcopy(document)
        return None

    async def update_one(self, query, update, upsert=False):
        key = str(query.get("clause_uid") or query.get("_id"))
        existing = self.documents.get(key)
        if existing is not None:
            existing.update(deepcopy(update.get("$set", {})))
        elif upsert:
            self.documents[key] = {
                **deepcopy(update.get("$setOnInsert", {})),
                **deepcopy(update.get("$set", {})),
            }

        class _Result:
            upserted_id = None

        return _Result()

    async def insert_one(self, document):
        self.inserted.append(deepcopy(document))

        class _Result:
            inserted_id = "run-1"

        return _Result()


class _Database:
    def __init__(self) -> None:
        self.collections: dict[str, _Collection] = {}

    def __getitem__(self, name: str) -> _Collection:
        return self.collections.setdefault(name, _Collection())

    def __getattr__(self, name: str) -> _Collection:
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]


class _AllowPolicy:
    async def authorize(self, *_args, **_kwargs):
        return None


class _EmbeddingClient:
    async def embed(self, texts: list[str]):
        return [[float(len(text)), 1.0] for text in texts]


class _PersistentQdrant:
    def __init__(self) -> None:
        self.namespaces: dict[str, dict[str, dict[str, Any]]] = {}

    async def upsert(self, vectors, chunks, namespace="default"):
        points = self.namespaces.setdefault(namespace, {})
        for vector, chunk in zip(vectors, chunks):
            points[str(chunk["chunk_id"])] = {
                "vector": list(vector),
                "payload": deepcopy(chunk),
            }

    def marker_present(self, marker: str) -> bool:
        return any(
            marker in str(point["payload"].get("text") or "")
            for point in self.namespaces.get("contract_clauses", {}).values()
        )


class _PersistentFalkor:
    """Minimal physical graph state behind the real ClauseGraphService."""

    def __init__(self) -> None:
        self.nodes: dict[tuple[str, str], dict[str, Any]] = {}
        self.edges: set[tuple[str, str, str]] = set()

    async def _execute(self, cypher: str, params: dict[str, Any]):
        if cypher.startswith("MERGE (n:"):
            label = cypher.split("MERGE (n:", 1)[1].split(" ", 1)[0]
            self.nodes[(label, str(params["key"]))] = deepcopy(params["props"])
            return
        if "MERGE (a)-[:" in cypher:
            relationship = cypher.split("MERGE (a)-[:", 1)[1].split("]", 1)[0]
            self.edges.add((str(params["from"]), relationship, str(params["to"])))

    def marker_present(self, marker: str) -> bool:
        return any(
            marker in str(properties.get("title") or properties.get("text") or "")
            for (label, _key), properties in self.nodes.items()
            if label == "Clause"
        )

    def has_clause_publication(self) -> bool:
        has_clause = any(label == "Clause" for label, _key in self.nodes)
        has_relation = any(rel == "HAS_CLAUSE" for _source, rel, _target in self.edges)
        return has_clause and has_relation


class _ContractService:
    def __init__(self, route_document: dict[str, Any]) -> None:
        self.route_document = route_document

    async def get_contract_document(self, _document_id, _current_user):
        return deepcopy(self.route_document)


class _User:
    id = "authority-test-user"


def _canonical_document(document_id: str, **overrides) -> dict[str, Any]:
    document = {
        "_id": document_id,
        "organization_id": "org-authority",
        "project_id": "project-authority",
        "contract_id": "contract-authority",
        "filename": "authority-matrix-gcc.pdf",
        "contract_categories": ["GCC"],
        "processing_status": "completed",
        "duplicate_status": "unique",
        "lifecycle_state": "active",
    }
    document.update(overrides)
    return document


async def _post_index_route(
    monkeypatch: pytest.MonkeyPatch,
    canonical: dict[str, Any] | None,
) -> tuple[httpx.Response, _PersistentQdrant, _PersistentFalkor]:
    document_id = str((canonical or {}).get("_id") or "missing-canonical-document")
    route_document = canonical or _canonical_document(document_id)
    db = _Database()
    if canonical is not None:
        db["documents"].documents[document_id] = deepcopy(canonical)
    db["contract_ocr_pages"].documents["page-1"] = {
        "_id": "page-1",
        "document_id": document_id,
        "page_number": 1,
        "cleaned_text": SOURCE_TEXT,
        "raw_text": SOURCE_TEXT,
        "status": "text_layer",
    }

    qdrant = _PersistentQdrant()
    falkor = _PersistentFalkor()
    agent = ClauseChunkingAgent(
        db=db,
        policy_service=_AllowPolicy(),
        embedding_service=ClauseEmbeddingService(db, _EmbeddingClient(), qdrant),
        graph_service=ClauseGraphService(falkor=falkor),
    )
    monkeypatch.setattr(contract_clauses, "ClauseChunkingAgent", lambda **_kwargs: agent)

    app = FastAPI()
    app.include_router(contract_clauses.router)
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[contract_clauses.get_contract_service] = lambda: _ContractService(
        route_document
    )
    app.dependency_overrides[get_current_user] = lambda: _User()
    # CL-4A holds the contract to the navbar selection; this suite is about publication
    # authority, so it sends the selection the browser would: the contract's own project.
    pin_selection(app, db, "org-authority", "project-authority")

    contract_clauses._BACKGROUND_TASKS.clear()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://authority.test") as client:
        response = await client.post(f"/contracts/{document_id}/clauses/index")
        tasks = list(contract_clauses._BACKGROUND_TASKS)
        assert len(tasks) == 1, "the accepted route must launch its real indexing background path"
        await tasks[0]
    contract_clauses._BACKGROUND_TASKS.clear()
    return response, qdrant, falkor


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "canonical", "should_publish"),
    [
        ("clean", _canonical_document("clause-authority-clean"), True),
        (
            "operational_failed",
            _canonical_document("clause-authority-failed", processing_status="failed"),
            True,
        ),
        (
            "human_review",
            _canonical_document(
                "clause-authority-review", processing_status="human_review_required"
            ),
            False,
        ),
        (
            "duplicate_status",
            _canonical_document("clause-authority-duplicate-status", duplicate_status="duplicate"),
            False,
        ),
        (
            "duplicate_lifecycle",
            _canonical_document("clause-authority-duplicate-life", lifecycle_state="duplicate"),
            False,
        ),
        (
            "deleted_lifecycle",
            _canonical_document("clause-authority-deleted", lifecycle_state="deleted"),
            False,
        ),
        ("missing_canonical", None, False),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
async def test_clause_index_route_obeys_canonical_publication_authority(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    canonical: dict[str, Any] | None,
    should_publish: bool,
):
    response, qdrant, falkor = await _post_index_route(monkeypatch, canonical)

    assert response.status_code == 202, case
    assert response.json()["status"] == "processing", case

    physical_store_state = (
        qdrant.marker_present(QDRANT_MARKER),
        falkor.marker_present(FALKOR_MARKER),
        falkor.has_clause_publication(),
    )
    assert physical_store_state == (should_publish, should_publish, should_publish), case
