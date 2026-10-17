"""Stored rows are republished as evidence only if they are the letter.

Two repair paths re-embed ``chunks`` rows an earlier writer stored -
storage-sync's single and bulk repair (``_resync_document_vectors``, the
``chunks`` branch) and ``VectorReconciler.reconcile_document``. Rows chunked
before source authority (20ac7c8) came from ``full_text`` -> ``ocrText`` ->
``text``, and for a document with no source text ``ocrText`` held the whole
extraction report, Key Reply Points included. Both paths re-upserted those rows
with no eligibility check: the report, advice and all, went back into the
evidence namespace.

Every path that republishes stored rows now asks the one rule,
``refuse_unpublishable_stored_rows``. Storage-sync's chunk repair also takes
the point's organisation and project from the document, never from the row.

Run against the in-memory Qdrant client, and a disposable real Qdrant when
``CORRESPONDENCE_QDRANT_TEST_URL`` is set.
"""

from __future__ import annotations

import uuid
from typing import Any, Dict, List

import pytest
from bson import ObjectId
from fastapi import HTTPException

from rbac_backend.retrieval.point_ids import generic_chunk_point_id
from rbac_backend.services.source_text import OCR_TEXT_KIND_REPORT, OCR_TEXT_KIND_SOURCE
from rbac_backend.tests.correspondence_vector_harness import (
    QDRANT_BACKENDS,
    Database,
    DeterministicEmbeddingClient,
    QdrantHarness,
    correspondence_document,
    run,
)

ORG, PROJECT = str(ObjectId()), str(ObjectId())
FOREIGN_ORG, FOREIGN_PROJECT = str(ObjectId()), str(ObjectId())

ADVICE = "ADVISORY-REPLY-POINT"
REPORT = (
    "1) Date: 01-08-2026\n"
    "2) Letter No: RPT/001\n"
    "5) Subject: Delay to viaduct pier foundations\n"
    "22) Summary: Notice of delay.\n"
    f"24) Key Reply Points: 1) Deny liability: {ADVICE}\n"
)
#: A reply cut off before its advice item: still the report, not the letter.
TRUNCATED_REPORT = (
    "1) Date: 01-08-2026\n"
    "2) Letter No: RPT/002\n"
    "5) Subject: Delay to viaduct pier foundations\n"
    "22) Summary: Notice of delay.\n"
)
LETTER = (
    "Dear Sir, we give notice of delay to the viaduct pier foundations caused "
    "by late access to the railway corridor. "
) * 3


@pytest.fixture(params=QDRANT_BACKENDS)
def harness(request, monkeypatch):
    built = QdrantHarness(request.param, monkeypatch)
    yield built
    built.drop()


def _document(**overrides: Any) -> Dict[str, Any]:
    return correspondence_document(
        organization_id=ORG,
        project_id=PROJECT,
        letter_no="L/1",
        subject="Delay",
        processing_status="completed",
        **overrides,
    )


def _chunk_rows(db: Database, document_id: str, texts: List[str], **row: Any) -> List[str]:
    ids = []
    for index, text in enumerate(texts):
        chunk_id = str(uuid.uuid4())
        ids.append(chunk_id)
        run(
            db.chunks.insert_one(
                {
                    "chunk_id": chunk_id,
                    "document_id": document_id,
                    "org_id": row.get("org_id", ORG),
                    "project_id": row.get("project_id", PROJECT),
                    "chunk_index": index,
                    "text_original": text,
                }
            )
        )
    return ids


def _points(harness: QdrantHarness, document_id: str) -> List[Any]:
    return [p for p in harness.scroll() if (p.payload or {}).get("document_id") == document_id]


REPORT_DOCUMENTS = {
    # ocrText is the report, labelled so; the old pipeline chunked it.
    "labelled_report_with_advice": dict(ocrText=REPORT, ocr_text_kind=OCR_TEXT_KIND_REPORT),
    # Unlabelled (pre-provenance) report text carrying the advice.
    "unlabelled_report_with_advice": dict(ocrText=REPORT),
    # A reply cut off before its advice item: the report's shape alone.
    "truncated_report": dict(ocrText=TRUNCATED_REPORT, ocr_text_kind=OCR_TEXT_KIND_REPORT),
}
REPORT_ROWS = {
    "labelled_report_with_advice": [REPORT],
    "unlabelled_report_with_advice": ["1) Date: 01-08-2026\n2) Letter No: RPT/001\n24) Key Reply", f" Points: {ADVICE}"],
    "truncated_report": [TRUNCATED_REPORT],
}


# --- storage-sync chunk repair ---------------------------------------------------


@pytest.mark.parametrize("case", sorted(REPORT_DOCUMENTS))
def test_storage_sync_does_not_republish_report_chunks(harness: QdrantHarness, case: str) -> None:
    from rbac_backend.routers import storage_sync

    document = _document(**REPORT_DOCUMENTS[case])
    document_id = str(document["_id"])
    db = Database([document])
    _chunk_rows(db, document_id, REPORT_ROWS[case])

    with pytest.raises(HTTPException) as refused:
        run(
            storage_sync._resync_document_vectors(
                document_id, db, harness.config, DeterministicEmbeddingClient(), harness.reader
            )
        )

    assert refused.value.status_code == 409
    assert _points(harness, document_id) == []


def test_storage_sync_still_repairs_letter_chunks_under_the_documents_authority(
    harness: QdrantHarness,
) -> None:
    from rbac_backend.routers import storage_sync

    document = _document(ocrText=LETTER, ocr_text_kind=OCR_TEXT_KIND_SOURCE)
    document_id = str(document["_id"])
    db = Database([document])
    # A row stamped with another tenant's scope: authority is the document's.
    ids = _chunk_rows(db, document_id, [LETTER], org_id=FOREIGN_ORG, project_id=FOREIGN_PROJECT)

    result = run(
        storage_sync._resync_document_vectors(
            document_id, db, harness.config, DeterministicEmbeddingClient(), harness.reader
        )
    )

    points = _points(harness, document_id)
    assert [p.payload["chunk_id"] for p in points] == ids, result
    assert points[0].payload["org_id"] == ORG
    assert points[0].payload["project_id"] == PROJECT
    assert points[0].payload["text"] == LETTER


# --- the vector reconciler ---------------------------------------------------------


def _reconciler(db: Database, harness: QdrantHarness):
    from rbac_backend.retrieval.reconcile import VectorReconciler

    return VectorReconciler(db, DeterministicEmbeddingClient(), harness.reader)


@pytest.mark.parametrize("case", sorted(REPORT_DOCUMENTS))
def test_the_reconciler_does_not_republish_report_chunks(harness: QdrantHarness, case: str) -> None:
    document = _document(**REPORT_DOCUMENTS[case])
    document_id = str(document["_id"])
    db = Database([document])
    _chunk_rows(db, document_id, REPORT_ROWS[case])

    result = run(_reconciler(db, harness).reconcile_document(document_id, ORG, PROJECT))

    assert _points(harness, document_id) == []
    assert result["repaired"] == 0
    assert result.get("skipped_unpublishable") == 1, result


def test_the_reconciler_still_repairs_letter_chunks_under_the_documents_authority(
    harness: QdrantHarness,
) -> None:
    document = _document(ocrText=LETTER, ocr_text_kind=OCR_TEXT_KIND_SOURCE)
    document_id = str(document["_id"])
    db = Database([document])
    ids = _chunk_rows(db, document_id, [LETTER])

    result = run(_reconciler(db, harness).reconcile_document(document_id, ORG, PROJECT))

    points = _points(harness, document_id)
    assert [p.payload["chunk_id"] for p in points] == ids
    assert result["repaired"] == 1
    assert (points[0].payload["org_id"], points[0].payload["project_id"]) == (ORG, PROJECT)


def test_the_reconciler_still_leaves_contract_points_alone(harness: QdrantHarness) -> None:
    document = _document(uploadType="contract", ocrText=LETTER, ocr_text_kind=OCR_TEXT_KIND_SOURCE)
    document_id = str(document["_id"])
    db = Database([document])
    _chunk_rows(db, document_id, [LETTER])

    result = run(_reconciler(db, harness).reconcile_document(document_id, ORG, PROJECT))

    assert result.get("skipped_contract") == 1
    assert _points(harness, document_id) == []


# --- the rule itself ----------------------------------------------------------------


def test_the_rule_trusts_rows_the_canonical_writer_wrote() -> None:
    from rbac_backend.retrieval.correspondence_payload import refuse_unpublishable_stored_rows

    # The document's stored text is the report; rows rebuilt by the canonical
    # writer were refused at write time if they were, so they are trusted.
    refuse_unpublishable_stored_rows(
        _document(ocrText=REPORT), ["25) Full Content: notice of delay."], canonically_written=True
    )


def test_a_letter_that_numbers_its_particulars_is_not_refused_when_source_is_recorded() -> None:
    from rbac_backend.retrieval.correspondence_payload import refuse_unpublishable_stored_rows

    numbered = "1) Date: 01-08-2026\n2) Letter No: L/9\n5) Subject: Piers\nDear Sir, notice."
    refuse_unpublishable_stored_rows(
        _document(ocrText=numbered, ocr_text_kind=OCR_TEXT_KIND_SOURCE), [numbered]
    )


# --- review follow-ups ---------------------------------------------------------


def test_the_reconciler_still_removes_orphan_points_when_rows_are_refused(
    harness: QdrantHarness,
) -> None:
    """Refusing to re-embed must not also stop the safe direction: a point no
    stored row backs is still removed."""
    from rbac_backend.tests.correspondence_vector_harness import embed_text

    document = _document(**REPORT_DOCUMENTS["labelled_report_with_advice"])
    document_id = str(document["_id"])
    db = Database([document])
    _chunk_rows(db, document_id, REPORT_ROWS["labelled_report_with_advice"])
    orphan = str(uuid.uuid4())
    run(
        harness.reader.upsert(
            [embed_text(REPORT)],
            [{"chunk_id": orphan, "document_id": document_id, "org_id": ORG,
              "project_id": PROJECT, "text": REPORT}],
            point_id_for=generic_chunk_point_id,
        )
    )

    result = run(_reconciler(db, harness).reconcile_document(document_id, ORG, PROJECT))

    assert result.get("skipped_unpublishable") == 1, result
    assert result["removed"] == 1 and _points(harness, document_id) == []


def test_the_reconciler_refuses_a_scope_that_is_not_the_documents(harness: QdrantHarness) -> None:
    document = _document(ocrText=LETTER, ocr_text_kind=OCR_TEXT_KIND_SOURCE)
    document_id = str(document["_id"])
    db = Database([document])
    # Rows stamped with another tenant's scope, reconciled under that scope.
    _chunk_rows(db, document_id, [LETTER], org_id=FOREIGN_ORG, project_id=FOREIGN_PROJECT)

    result = run(
        _reconciler(db, harness).reconcile_document(document_id, FOREIGN_ORG, FOREIGN_PROJECT)
    )

    assert result.get("skipped_scope_mismatch") == 1, result
    assert _points(harness, document_id) == []


def test_a_refused_document_is_recorded_and_stops_taking_bulk_slots(harness: QdrantHarness, monkeypatch) -> None:
    from types import SimpleNamespace

    from rbac_backend.core.security import CurrentUser
    from rbac_backend.routers import storage_sync

    report = _document(**REPORT_DOCUMENTS["labelled_report_with_advice"])
    letter = _document(ocrText=LETTER, ocr_text_kind=OCR_TEXT_KIND_SOURCE)
    # The report document is the newest, so it heads the bulk window.
    report["updatedAt"], letter["updatedAt"] = 2, 1
    db = Database([report, letter])
    _chunk_rows(db, str(report["_id"]), REPORT_ROWS["labelled_report_with_advice"])
    _chunk_rows(db, str(letter["_id"]), [LETTER])

    async def _database():
        return db

    async def _step_up(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def _distinct(field: str):
        return []

    db["contract_documents"].distinct = _distinct  # type: ignore[attr-defined]
    monkeypatch.setattr(storage_sync, "get_database", _database)
    monkeypatch.setattr(storage_sync, "require_step_up", _step_up)
    monkeypatch.setattr(storage_sync, "DocumentProcessingConfig", lambda: harness.config)
    monkeypatch.setattr(storage_sync, "VectorClient", lambda _config: harness.reader)
    monkeypatch.setattr(storage_sync, "EmbeddingClient", lambda _config: DeterministicEmbeddingClient())
    superadmin = CurrentUser(id="sa", username="sa", email="sa@example.com", roles=["superadmin"])

    def _bulk():
        return run(
            storage_sync.resync_bulk_vectors(
                request=SimpleNamespace(), org_id=ORG, project_id=None, limit=1,
                include_synced=True, current_user=superadmin,
            )
        )

    first = _bulk()
    assert [e["document_id"] for e in first["errors"]] == [str(report["_id"])], first
    status = run(db.vector_sync_status.find_one({"document_id": str(report["_id"])}))
    assert status["sync_status"] == "reprocess_required"
    # The next run does not spend its only slot on it again.
    second = _bulk()
    assert [e["document_id"] for e in second["processed"]] == [str(letter["_id"])], second


def test_storage_sync_refuses_to_publish_an_unscoped_document(harness: QdrantHarness) -> None:
    from rbac_backend.routers import storage_sync

    document = _document(ocrText=LETTER, ocr_text_kind=OCR_TEXT_KIND_SOURCE)
    document["organization_id"] = None
    document_id = str(document["_id"])
    db = Database([document])
    _chunk_rows(db, document_id, [LETTER])

    with pytest.raises(HTTPException) as refused:
        run(
            storage_sync._resync_document_vectors(
                document_id, db, harness.config, DeterministicEmbeddingClient(), harness.reader
            )
        )

    assert refused.value.status_code == 409
    assert _points(harness, document_id) == []


def test_a_reprocessed_document_is_repaired_from_its_canonical_rows(harness: QdrantHarness, monkeypatch) -> None:
    """Reprocessing writes canonical document_vectors rows but leaves an older
    pipeline's chunks behind. Repair uses the canonical rows - replacing the
    stale chunk points - instead of refusing on chunks nothing reads, so
    "reprocess the document" really ends the refusal."""
    from rbac_backend.retrieval.correspondence_payload import (
        CORRESPONDENCE_PAYLOAD_SCHEMA_VERSION,
        PAYLOAD_SCHEMA_VERSION_FIELD,
        assert_canonical_correspondence_payload,
    )
    from rbac_backend.routers import storage_sync

    document = _document(ocrText=LETTER, ocr_text_kind=OCR_TEXT_KIND_SOURCE)
    document_id = str(document["_id"])
    db = Database([document])
    _chunk_rows(db, document_id, REPORT_ROWS["labelled_report_with_advice"])
    run(
        db.document_vectors.insert_one(
            {
                "document_id": document_id,
                "chunk_index": 0,
                "text": LETTER,
                PAYLOAD_SCHEMA_VERSION_FIELD: CORRESPONDENCE_PAYLOAD_SCHEMA_VERSION,
            }
        )
    )
    monkeypatch.setattr(storage_sync, "LangChainVectorService", lambda _config: harness.writer)

    async def _count(_config, doc_id):
        return harness.client.count(
            collection_name=harness.config.qdrant_collection,
            count_filter=storage_sync._qdrant_document_filter(harness.writer._qdrant_models, doc_id),
            exact=True,
        ).count

    monkeypatch.setattr(storage_sync, "_fetch_qdrant_document_count", _count)

    result = run(
        storage_sync._resync_document_vectors(
            document_id, db, harness.config, DeterministicEmbeddingClient(), harness.reader
        )
    )

    assert result["status"] == "synced", result
    points = _points(harness, document_id)
    assert points
    for point in points:
        assert_canonical_correspondence_payload(point.payload)
        assert ADVICE not in (point.payload.get("text") or "")
    status = run(db.vector_sync_status.find_one({"document_id": document_id}))
    assert status["sync_status"] == "synced"


def test_refused_documents_cannot_crowd_the_bulk_window(harness: QdrantHarness, monkeypatch) -> None:
    """Excluded in the query, not skipped after the window was cut: four
    refused documents newer than the only repairable one, limit 1 (window 3)."""
    from types import SimpleNamespace

    from rbac_backend.core.security import CurrentUser
    from rbac_backend.routers import storage_sync

    refused = [_document(**REPORT_DOCUMENTS["labelled_report_with_advice"]) for _ in range(4)]
    letter = _document(ocrText=LETTER, ocr_text_kind=OCR_TEXT_KIND_SOURCE)
    for index, row in enumerate(refused):
        row["updatedAt"] = 10 + index
    letter["updatedAt"] = 1
    db = Database([*refused, letter])
    for row in refused:
        _chunk_rows(db, str(row["_id"]), REPORT_ROWS["labelled_report_with_advice"])
        run(
            db.vector_sync_status.insert_one(
                {"document_id": str(row["_id"]), "sync_status": "reprocess_required"}
            )
        )
    _chunk_rows(db, str(letter["_id"]), [LETTER])

    async def _database():
        return db

    async def _step_up(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def _distinct(field: str):
        return []

    db["contract_documents"].distinct = _distinct  # type: ignore[attr-defined]
    monkeypatch.setattr(storage_sync, "get_database", _database)
    monkeypatch.setattr(storage_sync, "require_step_up", _step_up)
    monkeypatch.setattr(storage_sync, "DocumentProcessingConfig", lambda: harness.config)
    monkeypatch.setattr(storage_sync, "VectorClient", lambda _config: harness.reader)
    monkeypatch.setattr(storage_sync, "EmbeddingClient", lambda _config: DeterministicEmbeddingClient())
    # The query decides the window: the harness ignores sort and limit, so
    # apply both the way Mongo does.
    find = db.documents.find

    def _windowed(filter, projection=None, *args, **kwargs):
        cursor = find(filter, *args, **kwargs)
        cursor._documents.sort(key=lambda row: row.get("updatedAt") or 0, reverse=True)  # type: ignore[attr-defined]
        return cursor

    db.documents.find = _windowed  # type: ignore[method-assign]
    superadmin = CurrentUser(id="sa", username="sa", email="sa@example.com", roles=["superadmin"])

    result = run(
        storage_sync.resync_bulk_vectors(
            request=SimpleNamespace(), org_id=ORG, project_id=None, limit=1,
            include_synced=True, current_user=superadmin,
        )
    )

    assert [entry["document_id"] for entry in result["processed"]] == [str(letter["_id"])], result


def test_the_reconciler_refuses_a_document_whose_scope_is_not_an_id(harness: QdrantHarness) -> None:
    document = _document(ocrText=LETTER, ocr_text_kind=OCR_TEXT_KIND_SOURCE)
    document["organization_id"] = {"$ne": None}
    document_id = str(document["_id"])
    db = Database([document])
    _chunk_rows(db, document_id, [LETTER])

    result = run(_reconciler(db, harness).reconcile_document(document_id, ORG, PROJECT))

    assert result.get("skipped_scope_mismatch") == 1, result
    assert _points(harness, document_id) == []


def test_after_reprocessing_superseded_chunks_are_retired_and_reconcile_settles(
    harness: QdrantHarness, monkeypatch
) -> None:
    """The stale chunk points (report text) are removed by id, the refused rows
    go, and the next reconcile finds the document in sync instead of
    rebuilding it on every run."""
    from types import SimpleNamespace

    from rbac_backend.core.security import CurrentUser
    from rbac_backend.retrieval.correspondence_payload import (
        CORRESPONDENCE_PAYLOAD_SCHEMA_VERSION,
        PAYLOAD_SCHEMA_VERSION_FIELD,
    )
    from rbac_backend.routers import storage_sync
    from rbac_backend.tests.correspondence_vector_harness import embed_text

    document = _document(ocrText=LETTER, ocr_text_kind=OCR_TEXT_KIND_SOURCE)
    document_id = str(document["_id"])
    db = Database([document])
    stale_ids = _chunk_rows(db, document_id, REPORT_ROWS["labelled_report_with_advice"])
    # What the older pipeline published from those rows.
    run(
        harness.reader.upsert(
            [embed_text(REPORT)],
            [{"chunk_id": stale_ids[0], "document_id": document_id, "org_id": ORG,
              "project_id": PROJECT, "text": REPORT}],
            point_id_for=generic_chunk_point_id,
        )
    )
    run(
        db.document_vectors.insert_one(
            {
                "document_id": document_id,
                "chunk_index": 0,
                "text": LETTER,
                PAYLOAD_SCHEMA_VERSION_FIELD: CORRESPONDENCE_PAYLOAD_SCHEMA_VERSION,
            }
        )
    )

    async def _count(_config, doc_id):
        return harness.client.count(
            collection_name=harness.config.qdrant_collection,
            count_filter=storage_sync._qdrant_document_filter(harness.writer._qdrant_models, doc_id),
            exact=True,
        ).count

    async def _database():
        return db

    async def _step_up(*_args: Any, **_kwargs: Any) -> None:
        return None

    async def _distinct(field: str):
        return []

    db["contract_documents"].distinct = _distinct  # type: ignore[attr-defined]
    monkeypatch.setattr(storage_sync, "LangChainVectorService", lambda _config: harness.writer)
    monkeypatch.setattr(storage_sync, "_fetch_qdrant_document_count", _count)
    monkeypatch.setattr(storage_sync, "get_database", _database)
    monkeypatch.setattr(storage_sync, "require_step_up", _step_up)
    monkeypatch.setattr(storage_sync, "DocumentProcessingConfig", lambda: harness.config)
    monkeypatch.setattr(storage_sync, "VectorClient", lambda _config: harness.reader)
    monkeypatch.setattr(storage_sync, "EmbeddingClient", lambda _config: DeterministicEmbeddingClient())

    repaired = run(
        storage_sync._resync_document_vectors(
            document_id, db, harness.config, DeterministicEmbeddingClient(), harness.reader
        )
    )

    assert repaired["status"] == "synced", repaired
    assert not {str(p.id) for p in _points(harness, document_id)} & set(stale_ids)
    assert all(ADVICE not in (p.payload.get("text") or "") for p in _points(harness, document_id))
    assert run(db.chunks.count_documents({"document_id": document_id})) == 0

    superadmin = CurrentUser(id="sa", username="sa", email="sa@example.com", roles=["superadmin"])
    settled = run(
        storage_sync.reconcile_vectors(
            request=SimpleNamespace(), org_id=ORG, project_id=PROJECT, limit=10,
            dry_run=False, current_user=superadmin,
        )
    )
    assert [d["status"] for d in settled["details"] if d["document_id"] == document_id] == [
        "in_sync"
    ], settled


def _superseded_fixture(harness: QdrantHarness, monkeypatch):
    """A reprocessed document: canonical rows, plus an older pipeline's refused
    chunk rows with production chunk ids, whose point carries another
    tenant's scope (the stamped-row case) and a uuid5 point id."""
    from rbac_backend.ingestion.chunk_ids import deterministic_chunk_id
    from rbac_backend.retrieval.correspondence_payload import (
        CORRESPONDENCE_PAYLOAD_SCHEMA_VERSION,
        PAYLOAD_SCHEMA_VERSION_FIELD,
    )
    from rbac_backend.routers import storage_sync
    from rbac_backend.tests.correspondence_vector_harness import embed_text

    document = _document(ocrText=LETTER, ocr_text_kind=OCR_TEXT_KIND_SOURCE)
    document_id = str(document["_id"])
    db = Database([document])
    chunk_id = deterministic_chunk_id(document_id, 0, None, REPORT)
    run(
        db.chunks.insert_one(
            {"chunk_id": chunk_id, "document_id": document_id, "org_id": FOREIGN_ORG,
             "project_id": FOREIGN_PROJECT, "chunk_index": 0, "text_original": REPORT}
        )
    )
    models = harness.writer._qdrant_models
    harness.client.upsert(
        collection_name=harness.config.qdrant_collection,
        points=[
            models.PointStruct(
                id=str(uuid.uuid5(uuid.NAMESPACE_URL, chunk_id)),
                vector=embed_text(REPORT),
                payload={"chunk_id": chunk_id, "document_id": document_id,
                         "org_id": FOREIGN_ORG, "project_id": FOREIGN_PROJECT, "text": REPORT},
            )
        ],
        wait=True,
    )
    run(
        db.document_vectors.insert_one(
            {"document_id": document_id, "chunk_index": 0, "text": LETTER,
             PAYLOAD_SCHEMA_VERSION_FIELD: CORRESPONDENCE_PAYLOAD_SCHEMA_VERSION}
        )
    )

    async def _count(_config, doc_id):
        return harness.client.count(
            collection_name=harness.config.qdrant_collection,
            count_filter=storage_sync._qdrant_document_filter(models, doc_id),
            exact=True,
        ).count

    monkeypatch.setattr(storage_sync, "LangChainVectorService", lambda _config: harness.writer)
    monkeypatch.setattr(storage_sync, "_fetch_qdrant_document_count", _count)
    return db, document_id, chunk_id


def test_superseded_points_with_production_ids_and_any_scope_are_removed(
    harness: QdrantHarness, monkeypatch
) -> None:
    from rbac_backend.routers import storage_sync

    db, document_id, chunk_id = _superseded_fixture(harness, monkeypatch)

    result = run(
        storage_sync._resync_document_vectors(
            document_id, db, harness.config, DeterministicEmbeddingClient(), harness.reader
        )
    )

    assert result["status"] == "synced", result
    assert [p for p in harness.scroll() if (p.payload or {}).get("chunk_id") == chunk_id] == []
    assert run(db.chunks.count_documents({"chunk_id": chunk_id})) == 0


def test_superseded_rows_stay_when_their_points_cannot_be_confirmed_gone(
    harness: QdrantHarness, monkeypatch
) -> None:
    from rbac_backend.routers import storage_sync

    db, document_id, chunk_id = _superseded_fixture(harness, monkeypatch)

    async def _unavailable(*_args, **_kwargs):
        raise ConnectionError("vector store unavailable")

    monkeypatch.setattr(harness.reader, "delete_document_chunks", _unavailable)

    result = run(
        storage_sync._resync_document_vectors(
            document_id, db, harness.config, DeterministicEmbeddingClient(), harness.reader
        )
    )

    # Never reported synced, and the rows - the record of those points - kept.
    assert result["status"] == "mismatch", result
    assert run(db.chunks.count_documents({"chunk_id": chunk_id})) == 1


def test_a_canonical_point_sharing_a_superseded_chunk_id_survives(
    harness: QdrantHarness, monkeypatch
) -> None:
    """Pipeline and canonical chunk ids seed from the same parts, so a
    superseded chunk can carry the id of the canonical chunk replace_document
    just wrote. Only the superseded (non-canonical) point may go."""
    from rbac_backend.retrieval.correspondence_payload import (
        CORRESPONDENCE_PAYLOAD_SCHEMA_VERSION,
        PAYLOAD_SCHEMA_VERSION_FIELD,
        build_correspondence_chunks,
    )
    from rbac_backend.routers import storage_sync
    from rbac_backend.tests.correspondence_vector_harness import embed_text

    document = _document(ocrText=LETTER, ocr_text_kind=OCR_TEXT_KIND_SOURCE)
    document_id = str(document["_id"])
    db = Database([document])
    [canonical] = build_correspondence_chunks(
        document, [LETTER], embedding_model=harness.config.openai_embedding_model
    )
    shared_id = canonical["payload"]["chunk_id"]
    run(
        db.chunks.insert_one(
            {"chunk_id": shared_id, "document_id": document_id, "org_id": ORG,
             "project_id": PROJECT, "chunk_index": 0, "text_original": REPORT}
        )
    )
    models = harness.writer._qdrant_models
    harness.client.upsert(
        collection_name=harness.config.qdrant_collection,
        points=[
            models.PointStruct(
                id=str(uuid.uuid4()),
                vector=embed_text(REPORT),
                payload={"chunk_id": shared_id, "document_id": document_id,
                         "org_id": ORG, "project_id": PROJECT, "text": REPORT},
            )
        ],
        wait=True,
    )
    run(
        db.document_vectors.insert_one(
            {"document_id": document_id, "chunk_index": 0, "text": LETTER,
             PAYLOAD_SCHEMA_VERSION_FIELD: CORRESPONDENCE_PAYLOAD_SCHEMA_VERSION}
        )
    )

    async def _count(_config, doc_id):
        return harness.client.count(
            collection_name=harness.config.qdrant_collection,
            count_filter=storage_sync._qdrant_document_filter(models, doc_id),
            exact=True,
        ).count

    monkeypatch.setattr(storage_sync, "LangChainVectorService", lambda _config: harness.writer)
    monkeypatch.setattr(storage_sync, "_fetch_qdrant_document_count", _count)

    result = run(
        storage_sync._resync_document_vectors(
            document_id, db, harness.config, DeterministicEmbeddingClient(), harness.reader
        )
    )

    assert result["status"] == "synced", result
    survivors = [p for p in harness.scroll() if (p.payload or {}).get("chunk_id") == shared_id]
    assert [p.payload.get(PAYLOAD_SCHEMA_VERSION_FIELD) for p in survivors] == [
        CORRESPONDENCE_PAYLOAD_SCHEMA_VERSION
    ], survivors
