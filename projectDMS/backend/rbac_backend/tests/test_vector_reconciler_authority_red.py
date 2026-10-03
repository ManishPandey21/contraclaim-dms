"""RED: vector reconciliation must not resurrect denied document vectors.

The public admin repair route drives the real ``VectorReconciler``. Its
structured result is useful, but containment is certified from the persistent
Qdrant boundary state after the request completes.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Iterable, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from rbac_backend.retrieval.reconcile import VectorReconciler
from rbac_backend.routers import retrieval_engine


DOCUMENT_ID = "vector-reconciler-authority-doc"
CHUNK_ID = "vector-reconciler-authority-chunk"
ORG_ID = "org-A"
PROJECT_ID = "proj-A"
BLOCKED_MARKER = "VECTOR_RECONCILER_BLOCKED_MARKER_20260819"


class _Cursor:
    def __init__(self, rows: Iterable[Dict[str, Any]]) -> None:
        self._rows = [dict(row) for row in rows]

    def __aiter__(self):
        async def _iterate():
            for row in self._rows:
                yield row

        return _iterate()


class _Documents:
    def __init__(self, document: Optional[Dict[str, Any]]) -> None:
        self._document = dict(document) if document is not None else None

    async def find_one(self, _query, _projection=None):
        return dict(self._document) if self._document is not None else None


class _Chunks:
    def find(self, query) -> _Cursor:
        if query != {
            "document_id": DOCUMENT_ID,
            "org_id": ORG_ID,
            "project_id": PROJECT_ID,
        }:
            return _Cursor([])
        return _Cursor(
            [
                {
                    "chunk_id": CHUNK_ID,
                    "document_id": DOCUMENT_ID,
                    "org_id": ORG_ID,
                    "project_id": PROJECT_ID,
                    "page_start": 1,
                    "text_original": BLOCKED_MARKER,
                }
            ]
        )


class _Database:
    def __init__(self, document: Optional[Dict[str, Any]]) -> None:
        self.documents = _Documents(document)
        self.chunks = _Chunks()


class _EmbeddingBoundary:
    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts):
        self.calls += 1
        return [[1.0, 0.0] for _ in texts]


class _QdrantBoundary:
    """Persistent external-boundary fake with initially missing vectors."""

    def __init__(self) -> None:
        self.points: Dict[str, Dict[str, Any]] = {}

    async def list_chunk_ids(self, filters, namespace=None):
        return sorted(
            chunk_id
            for chunk_id, point in self.points.items()
            if point.get("org_id") == filters.get("org_id")
            and point.get("project_id") == filters.get("project_id")
            and point.get("document_id") == filters.get("document_id")
        )

    async def upsert(self, _vectors, chunks, namespace=None, point_id_for=None):
        for chunk in chunks:
            self.points[str(chunk["chunk_id"])] = dict(chunk)
        return len(chunks)

    async def delete(self, chunk_ids, namespace=None, point_id_for=None):
        removed = 0
        for chunk_id in chunk_ids:
            if self.points.pop(str(chunk_id), None) is not None:
                removed += 1
        return removed


def _document(
    processing_status: str = "completed",
    *,
    duplicate_status: str = "unique",
    lifecycle_state: str = "active",
) -> Dict[str, Any]:
    return {
        "_id": DOCUMENT_ID,
        "organization_id": ORG_ID,
        "project_id": PROJECT_ID,
        "processing_status": processing_status,
        "duplicate_status": duplicate_status,
        "lifecycle_state": lifecycle_state,
    }


@pytest.mark.parametrize(
    ("document", "expected_qdrant_chunks"),
    [
        pytest.param(_document(), 1, id="clean-repairs-missing-vector"),
        pytest.param(
            _document("failed"),
            1,
            id="operational-failure-keeps-model-b",
        ),
        pytest.param(
            _document("human_review_required"),
            0,
            id="human-review-required-is-contained",
        ),
        pytest.param(
            _document(duplicate_status="duplicate"),
            0,
            id="duplicate-quarantine-is-contained",
        ),
        pytest.param(
            _document(lifecycle_state="duplicate"),
            0,
            id="duplicate-lifecycle-is-contained",
        ),
        pytest.param(
            _document(lifecycle_state="deleted"),
            0,
            id="deleted-lifecycle-is-contained",
        ),
        pytest.param(None, 0, id="missing-canonical-source-fails-closed"),
    ],
)
def test_public_vector_reconciliation_obeys_canonical_publication_authority(
    document: Optional[Dict[str, Any]],
    expected_qdrant_chunks: int,
) -> None:
    embedding = _EmbeddingBoundary()
    qdrant = _QdrantBoundary()
    reconciler = VectorReconciler(
        db=_Database(document),
        embedding_client=embedding,
        vector_client=qdrant,
    )
    app = FastAPI()
    app.include_router(retrieval_engine.router, prefix="/api")
    app.dependency_overrides[retrieval_engine.get_reconciler] = lambda: reconciler
    app.dependency_overrides[retrieval_engine.get_current_user] = lambda: type(
        "Admin", (), {"roles": ["superadmin"]}
    )()

    response = TestClient(app).post(
        "/api/v1/admin/vector/reconcile",
        params={
            "document_id": DOCUMENT_ID,
            "org_id": ORG_ID,
            "project_id": PROJECT_ID,
        },
    )

    assert response.status_code == 200
    assert len(qdrant.points) == expected_qdrant_chunks
    marker_present = any(
        BLOCKED_MARKER in str(point.get("text") or "")
        for point in qdrant.points.values()
    )
    assert marker_present is (expected_qdrant_chunks == 1)
    assert response.json()["repaired"] == expected_qdrant_chunks
    assert embedding.calls == expected_qdrant_chunks


class _Instruments:
    def __init__(self, governed: bool) -> None:
        self._governed = governed

    async def find_one(self, _query, _projection=None):
        return {"_id": "instrument-1"} if self._governed else None


@pytest.mark.parametrize(
    ("document", "governed"),
    [
        pytest.param({**_document(), "uploadType": "contract"}, False, id="contract-upload"),
        pytest.param(_document(), True, id="contract-master-instrument"),
    ],
)
def test_reconciliation_never_rewrites_a_contract_projection(
    document: Dict[str, Any], governed: bool
) -> None:
    """Contract vectors have no ``chunks`` rows: reconciling them would delete
    every point of a CURRENT Contract Master projection as "missing in Mongo"."""
    embedding = _EmbeddingBoundary()
    qdrant = _QdrantBoundary()
    db = _Database(document)
    db.contract_documents = _Instruments(governed)  # type: ignore[attr-defined]
    result = asyncio.run(
        VectorReconciler(db=db, embedding_client=embedding, vector_client=qdrant).reconcile_document(
            DOCUMENT_ID, ORG_ID, PROJECT_ID
        )
    )
    assert result["skipped_contract"] == 1
    assert (result["removed"], result["repaired"]) == (0, 0)
    assert qdrant.points == {} and embedding.calls == 0
