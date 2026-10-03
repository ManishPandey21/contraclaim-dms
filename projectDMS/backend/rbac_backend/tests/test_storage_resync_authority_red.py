"""RED: single-document repair must not republish denied source material.

The public storage-repair route is a re-entry writer: an operator invokes it
after Mongo and Qdrant have drifted.  Its response is not proof of containment;
the observable contract is the final Qdrant state after the request completes.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, Iterable, Optional

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from rbac_backend.routers import storage_sync


DOCUMENT_ID = "507f1f77bcf86cd799439011"
CHUNK_ID = "chunk-authority-red"


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
    def find(self, _query) -> _Cursor:
        return _Cursor(
            [
                {
                    "chunk_id": CHUNK_ID,
                    "document_id": DOCUMENT_ID,
                    "org_id": "org-A",
                    "project_id": "proj-A",
                    "text_original": "authority-controlled extraction",
                }
            ]
        )


class _SyncStatus:
    def __init__(self) -> None:
        self.last_update: Optional[Dict[str, Any]] = None

    async def update_one(self, _query, update, **_kwargs) -> None:
        self.last_update = dict(update.get("$set") or {})


class _DB:
    def __init__(self, document: Optional[Dict[str, Any]]) -> None:
        self.documents = _Documents(document)
        self.chunks = _Chunks()
        self.vector_sync_status = _SyncStatus()

    def __getitem__(self, name: str) -> Any:
        # The repair asks whether a Contract Master instrument governs the
        # document (it would then delegate to reprojection); none does here.
        assert name == "contract_documents", name
        return _NoInstruments()


class _NoInstruments:
    async def find_one(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def find(self, *_args: Any, **_kwargs: Any) -> "_NoInstruments":
        return self

    async def to_list(self, length: Any = None) -> list:
        return []


class _EmbeddingBoundary:
    async def embed(self, texts):
        return [[1.0, 0.0] for _ in texts]


class _QdrantBoundary:
    """Persistent external-boundary fake: points survive until explicitly changed."""

    def __init__(self) -> None:
        self.points: Dict[str, Dict[str, Any]] = {}

    async def upsert(self, _vectors, chunks, namespace=None):
        for chunk in chunks:
            self.points[str(chunk["chunk_id"])] = dict(chunk)
        return len(chunks)

    async def list_chunk_ids(self, _filters, namespace=None):
        return sorted(self.points)


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/storage-sync/resync-doc",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )


def _document(
    processing_status: str = "metadata_extracted",
    *,
    duplicate_status: str = "unique",
    lifecycle_state: str = "active",
) -> Dict[str, Any]:
    return {
        "_id": DOCUMENT_ID,
        "organization_id": "org-A",
        "project_id": "proj-A",
        "processing_status": processing_status,
        "duplicate_status": duplicate_status,
        "lifecycle_state": lifecycle_state,
    }


@pytest.mark.parametrize(
    ("document", "expected_qdrant_chunks", "expected_status"),
    [
        pytest.param(_document(), 1, "synced", id="clean-publishes"),
        pytest.param(
            _document("failed"),
            1,
            "synced",
            id="operational-failure-keeps-model-b",
        ),
        pytest.param(
            _document("human_review_required"),
            0,
            "skipped",
            id="adverse-is-contained",
        ),
        pytest.param(
            _document(duplicate_status="duplicate"),
            0,
            "skipped",
            id="quarantined-duplicate-is-contained",
        ),
        pytest.param(
            _document(lifecycle_state="deleted"),
            0,
            "skipped",
            id="deleted-source-is-contained",
        ),
    ],
)
def test_single_document_repair_obeys_current_canonical_authority(
    monkeypatch,
    document: Dict[str, Any],
    expected_qdrant_chunks: int,
    expected_status: str,
) -> None:
    db = _DB(document)
    qdrant = _QdrantBoundary()

    async def _database():
        return db

    async def _step_up(*_args, **_kwargs):
        return None

    monkeypatch.setattr(storage_sync, "get_database", _database)
    monkeypatch.setattr(storage_sync, "require_step_up", _step_up)
    monkeypatch.setattr(storage_sync, "DocumentProcessingConfig", lambda: object())
    monkeypatch.setattr(storage_sync, "EmbeddingClient", lambda _config: _EmbeddingBoundary())
    monkeypatch.setattr(storage_sync, "VectorClient", lambda _config: qdrant)

    result = asyncio.run(
        storage_sync.resync_document_vectors(
            DOCUMENT_ID,
            _request(),
            SimpleNamespace(id="admin-1", roles=["superadmin"]),
        )
    )

    assert result["document_id"] == DOCUMENT_ID
    assert result["status"] == expected_status
    assert result["qdrant_chunks"] == expected_qdrant_chunks
    assert len(qdrant.points) == expected_qdrant_chunks
    if expected_status == "skipped":
        assert result["reason"] == "document_not_consumable"


def test_single_document_repair_rejects_missing_canonical_source(monkeypatch) -> None:
    db = _DB(None)
    qdrant = _QdrantBoundary()

    async def _database():
        return db

    async def _step_up(*_args, **_kwargs):
        return None

    monkeypatch.setattr(storage_sync, "get_database", _database)
    monkeypatch.setattr(storage_sync, "require_step_up", _step_up)
    monkeypatch.setattr(storage_sync, "DocumentProcessingConfig", lambda: object())
    monkeypatch.setattr(storage_sync, "EmbeddingClient", lambda _config: _EmbeddingBoundary())
    monkeypatch.setattr(storage_sync, "VectorClient", lambda _config: qdrant)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(
            storage_sync.resync_document_vectors(
                DOCUMENT_ID,
                _request(),
                SimpleNamespace(id="admin-1", roles=["superadmin"]),
            )
        )

    assert exc_info.value.status_code == 404
    assert qdrant.points == {}
