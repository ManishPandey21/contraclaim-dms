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
    assert [str(p.id) for p in points] == ids, result
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
    assert [str(p.id) for p in points] == ids
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
