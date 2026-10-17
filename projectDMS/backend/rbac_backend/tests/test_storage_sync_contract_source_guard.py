"""Storage-sync correspondence repair must never rewrite a contract's vectors.

The legacy repair path (``_resync_document_vectors`` for a document with no
``chunks`` rows) counts only canonical ``correspondence.v1`` points. A contract
document's points are clause points written by contract ingest - never in that
shape - so the count reads 0 and the document looked "missing". The repair then
rebuilt it with the correspondence builder: ``replace_document`` deleted every
point of the document (the clause points included), wrote correspondence
chunks without clause or page provenance, and marked it ``synced``.

Documents named by a Contract Master instrument were already excluded (PR #34).
A contract upload with no instrument yet - not promoted - was not. Its only
writers are contract ingest and the contract reindex, as for an instrument's
document, so storage-sync leaves it alone too: excluded from the reconcile and
bulk candidate windows, delegated by the single-document repair.

Run against the in-memory Qdrant client, and a disposable real Qdrant when
``CORRESPONDENCE_QDRANT_TEST_URL`` is set.
"""

from __future__ import annotations

import uuid
from copy import deepcopy
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from bson import ObjectId

from rbac_backend.core.security import CurrentUser
from rbac_backend.retrieval.correspondence_payload import (
    CORRESPONDENCE_PAYLOAD_SCHEMA_VERSION,
    PAYLOAD_SCHEMA_VERSION_FIELD,
    assert_canonical_correspondence_payload,
)
from rbac_backend.services.contract_document_store import CONTRACT_DOCUMENTS_COLLECTION
from rbac_backend.tests.correspondence_vector_harness import (
    QDRANT_BACKENDS,
    Database,
    QdrantHarness,
    correspondence_document,
    embed_text,
    run,
)

ORG, PROJECT = str(ObjectId()), str(ObjectId())

CLAUSES = [
    ("8.4", "Extension of Time for Completion", [41, 42]),
    ("20.1", "Contractor's Claims", [77]),
    ("14.7", "Payment", [63, 64]),
]


@pytest.fixture(params=QDRANT_BACKENDS)
def harness(request, monkeypatch):
    built = QdrantHarness(request.param, monkeypatch)
    yield built
    built.drop()


def _contract_document(**overrides: Any) -> Dict[str, Any]:
    document = {
        "_id": ObjectId(),
        "organization_id": ORG,
        "project_id": PROJECT,
        "uploadType": "contract",
        "document_type": "contract",
        "filename": "epc-contract.pdf",
        "processing_status": "completed",
        "updatedAt": None,
    }
    document.update(overrides)
    return document


def _seed_contract(db: Database, harness: QdrantHarness, document: Dict[str, Any]) -> str:
    """Contract ingest's footprint: clause rows in Mongo, clause points in Qdrant."""
    document_id = str(document["_id"])
    models = harness.writer._qdrant_models
    points = []
    for index, (number, title, pages) in enumerate(CLAUSES):
        chunk_id = f"{document_id}-clause-{number}"
        text = f"Clause {number} {title}. The Contractor shall be entitled subject to notice."
        row = {
            "document_id": document_id,
            "contract_id": document_id,
            "organization_id": ORG,
            "project_id": PROJECT,
            "uploadType": "contract",
            "document_type": "contract",
            "chunk_id": chunk_id,
            "chunk_index": index,
            "clause_number": number,
            "clause_title": title,
            "page_numbers": pages,
            "page_start": pages[0],
            "page_end": pages[-1],
            "text": text,
        }
        run(db.document_vectors.insert_one(row))
        points.append(
            models.PointStruct(
                id=_uuid_for(chunk_id),
                vector=embed_text(text),
                payload={key: value for key, value in row.items() if key != "_id"},
            )
        )
    harness.client.upsert(
        collection_name=harness.config.qdrant_collection, points=points, wait=True
    )
    return document_id


def _uuid_for(chunk_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id))


def _points(harness: QdrantHarness, document_id: str) -> List[Dict[str, Any]]:
    """Every point of the document, id + payload + vector, in a stable order."""
    points, _ = harness.client.scroll(
        collection_name=harness.config.qdrant_collection,
        limit=1000,
        with_payload=True,
        with_vectors=True,
    )
    found = [
        {"id": str(point.id), "payload": dict(point.payload or {}), "vector": point.vector}
        for point in points
        if (point.payload or {}).get("document_id") == document_id
    ]
    return sorted(found, key=lambda point: point["id"])


def _rows(db: Database, document_id: str) -> List[Dict[str, Any]]:
    rows = [
        deepcopy(row)
        for row in db.document_vectors._docs.values()
        if row.get("document_id") == document_id
    ]
    for row in rows:
        row.pop("_id", None)
    return sorted(rows, key=lambda row: row["chunk_id"])


def _canonical_count(harness: QdrantHarness, document_id: str) -> int:
    from rbac_backend.routers import storage_sync

    return harness.client.count(
        collection_name=harness.config.qdrant_collection,
        count_filter=storage_sync._qdrant_document_filter(
            harness.writer._qdrant_models, document_id
        ),
        exact=True,
    ).count


def _wire(monkeypatch: pytest.MonkeyPatch, harness: QdrantHarness, db: Database) -> None:
    from rbac_backend.routers import storage_sync

    async def _database():
        return db

    async def _step_up(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def _count(_config, document_id):
        return _canonical_count(harness, document_id)

    async def _distinct(field: str):
        rows = db[CONTRACT_DOCUMENTS_COLLECTION]._docs.values()
        return sorted({row.get(field) for row in rows if row.get(field)})

    db[CONTRACT_DOCUMENTS_COLLECTION].distinct = _distinct  # type: ignore[attr-defined]

    # The harness ignores projections; Mongo does not. Apply them, so a guard
    # that reads a field the route forgot to project fails here too.
    documents = db.documents
    unprojected_find = documents.find

    def _projected_find(filter: Dict[str, Any], projection: Any = None, *args: Any, **kwargs: Any):
        cursor = unprojected_find(filter, *args, **kwargs)
        if projection:
            kept = {key for key, include in projection.items() if include}
            cursor._documents = [  # type: ignore[attr-defined]
                {key: value for key, value in row.items() if key in kept or key == "_id"}
                for row in cursor._documents  # type: ignore[attr-defined]
            ]
        return cursor

    documents.find = _projected_find  # type: ignore[method-assign]
    monkeypatch.setattr(storage_sync, "get_database", _database)
    monkeypatch.setattr(storage_sync, "require_step_up", _step_up)
    monkeypatch.setattr(storage_sync, "DocumentProcessingConfig", lambda: harness.config)
    monkeypatch.setattr(storage_sync, "VectorClient", lambda _config: harness.reader)
    monkeypatch.setattr(storage_sync, "LangChainVectorService", lambda _config: harness.writer)
    monkeypatch.setattr(storage_sync, "_fetch_qdrant_document_count", _count)


SUPERADMIN = CurrentUser(id="sa", username="sa", email="sa@example.com", roles=["superadmin"])


def _letter_with_legacy_rows(db: Database) -> str:
    """A correspondence letter whose only vectors are Mongo rows: repairable."""
    letter = correspondence_document(
        organization_id=ORG,
        project_id=PROJECT,
        letter_no="CORR/REPAIR/001",
        subject="Delay to viaduct pier foundations",
        processing_status="completed",
    )
    run(db.documents.insert_one(letter))
    letter_id = str(letter["_id"])
    run(
        db.document_vectors.insert_one(
            {
                "document_id": letter_id,
                "chunk_index": 0,
                "text": "Notice of delay to the viaduct pier foundations. " * 6,
            }
        )
    )
    return letter_id


@pytest.mark.parametrize("upload_type", ["contract", "Contract"])
def test_reconcile_leaves_an_unpromoted_contract_untouched_and_still_repairs_correspondence(
    harness: QdrantHarness, monkeypatch: pytest.MonkeyPatch, upload_type: str
) -> None:
    from rbac_backend.routers import storage_sync

    contract = _contract_document(uploadType=upload_type)
    db = Database([contract])
    contract_id = _seed_contract(db, harness, contract)
    letter_id = _letter_with_legacy_rows(db)
    _wire(monkeypatch, harness, db)
    points_before, rows_before = _points(harness, contract_id), _rows(db, contract_id)
    assert len(points_before) == len(CLAUSES)

    result = run(
        storage_sync.reconcile_vectors(
            request=SimpleNamespace(),
            org_id=ORG,
            project_id=PROJECT,
            limit=50,
            dry_run=False,
            current_user=SUPERADMIN,
        )
    )

    # The contract: byte-equivalent points (ids, payloads, vectors), clause ids
    # and page provenance intact, nothing in correspondence shape, not "synced".
    assert _points(harness, contract_id) == points_before
    assert _rows(db, contract_id) == rows_before
    assert _canonical_count(harness, contract_id) == 0
    assert all(
        point["payload"].get(PAYLOAD_SCHEMA_VERSION_FIELD) != CORRESPONDENCE_PAYLOAD_SCHEMA_VERSION
        for point in _points(harness, contract_id)
    )
    assert {p["payload"]["chunk_id"] for p in _points(harness, contract_id)} == {
        f"{contract_id}-clause-{number}" for number, _title, _pages in CLAUSES
    }
    contract_status = run(db.vector_sync_status.find_one({"document_id": contract_id}))
    assert contract_status is None or contract_status.get("sync_status") != "synced"
    assert contract_id not in {
        entry["document_id"] for entry in result["details"] if entry.get("status") == "repaired"
    }
    assert result["failed"] == 0, result

    # Ordinary correspondence repair is unaffected.
    assert {"document_id": letter_id, "status": "repaired"}.items() <= next(
        entry for entry in result["details"] if entry["document_id"] == letter_id
    ).items()
    letter_points = _points(harness, letter_id)
    assert letter_points
    for point in letter_points:
        assert_canonical_correspondence_payload(point["payload"])
    assert run(db.vector_sync_status.find_one({"document_id": letter_id}))["sync_status"] == "synced"


@pytest.mark.parametrize("upload_type", ["contract", "Contract"])
def test_reconcile_dry_run_does_not_offer_to_repair_a_contract(
    harness: QdrantHarness, monkeypatch: pytest.MonkeyPatch, upload_type: str
) -> None:
    from rbac_backend.routers import storage_sync

    contract = _contract_document(uploadType=upload_type)
    db = Database([contract])
    contract_id = _seed_contract(db, harness, contract)
    _wire(monkeypatch, harness, db)

    result = run(
        storage_sync.reconcile_vectors(
            request=SimpleNamespace(),
            org_id=ORG,
            project_id=PROJECT,
            limit=50,
            dry_run=True,
            current_user=SUPERADMIN,
        )
    )

    assert contract_id not in {
        entry["document_id"] for entry in result["details"] if entry.get("status") == "would_repair"
    }


@pytest.mark.parametrize("upload_type", ["contract", "Contract"])
def test_bulk_resync_leaves_an_unpromoted_contract_untouched(
    harness: QdrantHarness, monkeypatch: pytest.MonkeyPatch, upload_type: str
) -> None:
    from rbac_backend.routers import storage_sync

    contract = _contract_document(uploadType=upload_type)
    db = Database([contract])
    contract_id = _seed_contract(db, harness, contract)
    letter_id = _letter_with_legacy_rows(db)
    _wire(monkeypatch, harness, db)
    points_before, rows_before = _points(harness, contract_id), _rows(db, contract_id)

    result = run(
        storage_sync.resync_bulk_vectors(
            request=SimpleNamespace(),
            org_id=ORG,
            project_id=PROJECT,
            limit=25,
            include_synced=True,
            current_user=SUPERADMIN,
        )
    )

    assert _points(harness, contract_id) == points_before
    assert _rows(db, contract_id) == rows_before
    assert [entry["document_id"] for entry in result["processed"]] == [letter_id], result


@pytest.mark.parametrize("upload_type", ["contract", "Contract"])
def test_single_document_repair_delegates_an_unpromoted_contract(
    harness: QdrantHarness, monkeypatch: pytest.MonkeyPatch, upload_type: str
) -> None:
    from rbac_backend.routers import storage_sync

    contract = _contract_document(uploadType=upload_type)
    db = Database([contract])
    contract_id = _seed_contract(db, harness, contract)
    _wire(monkeypatch, harness, db)
    points_before, rows_before = _points(harness, contract_id), _rows(db, contract_id)
    # A row a correspondence pass wrote earlier: its counts must not survive.
    run(
        db.vector_sync_status.insert_one(
            {
                "document_id": contract_id,
                "sync_status": "synced",
                "mongo_chunks": 3,
                "qdrant_chunks": 3,
            }
        )
    )

    result = run(storage_sync._resync_document_vectors(contract_id, db, harness.config))

    assert result["status"] == "delegated", result
    assert _points(harness, contract_id) == points_before
    assert _rows(db, contract_id) == rows_before
    status = run(db.vector_sync_status.find_one({"document_id": contract_id}))
    assert status["sync_status"] == "delegated"
    assert "mongo_chunks" not in status and "qdrant_chunks" not in status
    assert len([r for r in db.vector_sync_status._docs.values() if r["document_id"] == contract_id]) == 1


def test_reconcile_still_excludes_an_instrument_governed_contract(
    harness: QdrantHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The PR #34 exclusion, kept: an instrument's document is never selected."""
    from rbac_backend.routers import storage_sync

    contract = _contract_document()
    db = Database([contract])
    contract_id = _seed_contract(db, harness, contract)
    run(
        db[CONTRACT_DOCUMENTS_COLLECTION].insert_one(
            {"_id": ObjectId(), "document_id": contract_id, "classification_revision": 1}
        )
    )
    _wire(monkeypatch, harness, db)
    points_before = _points(harness, contract_id)

    result = run(
        storage_sync.reconcile_vectors(
            request=SimpleNamespace(),
            org_id=ORG,
            project_id=PROJECT,
            limit=50,
            dry_run=False,
            current_user=SUPERADMIN,
        )
    )

    assert _points(harness, contract_id) == points_before
    assert contract_id not in {entry["document_id"] for entry in result["details"]}


@pytest.mark.parametrize("upload_type", ["contract", "Contract"])
def test_reconcile_vectors_script_refuses_to_rebuild_a_contract(
    harness: QdrantHarness, monkeypatch: pytest.MonkeyPatch, upload_type: str
) -> None:
    """``scripts/reconcile_vectors.py --repair``: the same writer, the same rule."""
    import importlib.util
    from pathlib import Path

    script = Path(__file__).resolve().parents[3] / "scripts" / "reconcile_vectors.py"
    spec = importlib.util.spec_from_file_location("reconcile_vectors_contract_guard", script)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    contract = _contract_document(uploadType=upload_type)
    db = Database([contract])
    contract_id = _seed_contract(db, harness, contract)
    points_before, rows_before = _points(harness, contract_id), _rows(db, contract_id)

    async def _resolve(_db, document_id):
        return contract if str(document_id) == contract_id else None

    monkeypatch.setattr(module, "resolve_canonical_document", _resolve)

    repaired = run(module._repair_document(db, harness.writer, contract_id))

    assert repaired is None
    assert _points(harness, contract_id) == points_before
    assert _rows(db, contract_id) == rows_before
