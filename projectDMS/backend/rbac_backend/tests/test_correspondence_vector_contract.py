"""DI-B1: an uploaded correspondence must be findable by tenant-scoped retrieval.

The integration tests drive the real writer (queued-job worker ->
DocumentProcessor -> DatabaseService -> LangChainVectorService) into a real
Qdrant and read back through the real ``RetrievalService.search`` - the path
``/api/v1/retrieval/*``, the drafting agent and the LangGraph drafting pipeline
use. ``correspondence_vector_harness`` states what is faked (only system
boundaries). Each integration test runs against qdrant-client local mode, and
also against a disposable Qdrant server when ``CORRESPONDENCE_QDRANT_TEST_URL``
is set.

Before the fix the writer stored ``{page_content, metadata: {organization_id,
...}}`` and every scoped search answered 0 while an unfiltered search found the
point; ``test_the_pre_fix_payload_shape_is_invisible_to_scoped_search`` keeps
that mechanism pinned, because it is why legacy points need a reindex.
"""

from __future__ import annotations

import inspect
import uuid
from pathlib import Path
from typing import Any, Dict, List

import pytest
from bson import ObjectId

from rbac_backend.retrieval.correspondence_payload import (
    ADVISORY_FIELDS,
    CORRESPONDENCE_PAYLOAD_SCHEMA_VERSION,
    DESCRIPTIVE_FIELDS,
    PAYLOAD_SCHEMA_VERSION_FIELD,
    CorrespondencePayloadError,
    assert_canonical_correspondence_payload,
    build_correspondence_chunks,
    canonical_scope_id,
    correspondence_point_id,
)
from rbac_backend.retrieval.models import SearchStrategy
from rbac_backend.tests.correspondence_vector_harness import (
    ADVISORY_TEXT,
    QDRANT_BACKENDS,
    Database,
    QdrantHarness,
    correspondence_document,
    embed_text,
    retrieval_service,
    run,
    scoped_search,
    upload_and_process,
    user_for,
)

ORG_A, PROJECT_A, PROJECT_A2 = str(ObjectId()), str(ObjectId()), str(ObjectId())
ORG_B, PROJECT_B = str(ObjectId()), str(ObjectId())

BODY = (
    "Notice of delay to the viaduct pier foundations caused by late access to "
    "the railway corridor. The contractor requests an extension of time. "
) * 4
QUERY = "viaduct pier foundations delay railway corridor"


@pytest.fixture(params=QDRANT_BACKENDS)
def harness(request, monkeypatch):
    built = QdrantHarness(request.param, monkeypatch)
    yield built
    built.drop()


def _letter(org: Any, project: Any, letter_no: str, **overrides: Any) -> Dict[str, Any]:
    return correspondence_document(
        organization_id=org,
        project_id=project,
        letter_no=letter_no,
        subject="Delay to viaduct pier foundations",
        **overrides,
    )


def _ids(response) -> List[str]:
    return [result.document_id for result in response.results]


def _points_for(harness: QdrantHarness, document_id: str) -> List[Any]:
    return [
        point
        for point in harness.scroll()
        if (point.payload or {}).get("document_id") == document_id
    ]


# --- B / I / K / M / N / P: upload -> extract -> write -> scoped search --------


@pytest.mark.parametrize("pipeline", ["legacy_v0", "unified_v1"])
def test_uploaded_letter_is_found_by_tenant_scoped_search(
    harness: QdrantHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    pipeline: str,
) -> None:
    db = Database()
    row = _letter(ORG_A, PROJECT_A, "ABC/2026/001")
    processor = run(
        upload_and_process(monkeypatch, tmp_path, db, harness, row, body=BODY, pipeline=pipeline)
    )
    document_id = str(row["_id"])

    # The worker really wrote through the one writer and recorded it synced.
    assert processor.database_service.partial_failures == {}
    status = run(db.vector_sync_status.find_one({"document_id": document_id}))
    assert status["sync_status"] == "synced", status

    points = _points_for(harness, document_id)
    assert points, "the writer published nothing"
    payload = points[0].payload
    assert payload[PAYLOAD_SCHEMA_VERSION_FIELD] == CORRESPONDENCE_PAYLOAD_SCHEMA_VERSION
    assert payload["org_id"] == payload["organization_id"] == ORG_A
    assert payload["project_id"] == PROJECT_A
    assert payload["doc_type"] == "letter"
    assert payload["uploadType"] == payload["source_type"] == "incoming"
    assert "metadata" not in payload and "page_content" not in payload
    # P: the parser returned reply advice; it is stored on the document but is
    # not evidence, so no point carries it.
    stored = run(db.documents.find_one({"_id": row["_id"]}))
    assert stored["key_reply_points"] == [ADVISORY_TEXT]
    for point in points:
        assert not ADVISORY_FIELDS.intersection(point.payload)
        assert "ADVISORY-REPLY-POINT" not in repr(point.payload)

    response = run(scoped_search(db, harness, QUERY, org_id=ORG_A, project_id=PROJECT_A))
    assert response.backend_used.value == "qdrant"
    assert document_id in _ids(response)
    hit = next(r for r in response.results if r.document_id == document_id)
    assert "viaduct pier foundations" in hit.snippet
    assert hit.payload["letterNo"] == "ABC/2026/001"


# --- C / F / D: tenant and project isolation, caller cannot override ---------


def _seed_three(harness, monkeypatch, tmp_path):
    db = Database()
    rows = {
        "a": _letter(ORG_A, PROJECT_A, "A/001"),
        "a2": _letter(ORG_A, PROJECT_A2, "A2/001"),
        "b": _letter(ORG_B, PROJECT_B, "B/001"),
    }
    for key, row in rows.items():
        run(
            upload_and_process(
                monkeypatch, tmp_path / key, db, harness, row, body=BODY
            )
        )
    return db, {key: str(row["_id"]) for key, row in rows.items()}


def test_org_a_cannot_retrieve_org_b_and_projects_stay_apart(
    harness: QdrantHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for sub in ("a", "a2", "b"):
        (tmp_path / sub).mkdir()
    db, ids = _seed_three(harness, monkeypatch, tmp_path)

    # Positive control: all three are in the collection and globally searchable.
    unfiltered = run(harness.reader.search(embed_text(QUERY), {}, limit=10, allow_global=True))
    assert {hit["payload"]["document_id"] for hit in unfiltered} == set(ids.values())

    as_a = _ids(run(scoped_search(db, harness, QUERY, org_id=ORG_A, project_id=PROJECT_A)))
    as_a2 = _ids(run(scoped_search(db, harness, QUERY, org_id=ORG_A, project_id=PROJECT_A2)))
    as_b = _ids(run(scoped_search(db, harness, QUERY, org_id=ORG_B, project_id=PROJECT_B)))
    assert as_a == [ids["a"]]
    assert as_a2 == [ids["a2"]]
    assert as_b == [ids["b"]]
    # Org A cannot reach Org B's project even by naming it.
    assert _ids(run(scoped_search(db, harness, QUERY, org_id=ORG_A, project_id=PROJECT_B))) == []


@pytest.mark.parametrize(
    "hostile",
    [
        {"org_id": ORG_B},
        {"organization_id": ORG_B},
        {"project_id": PROJECT_B},
        {"org_id": ORG_B, "project_id": PROJECT_B},
        {"metadata.org_id": ORG_B},
        {"ORG-ID": ORG_B},
        {"tenant_id": ORG_B},
        {"workspace_id": ORG_B},
        {"project_id": [PROJECT_A, PROJECT_B]},
        {"project_id": None},
    ],
)
def test_caller_metadata_cannot_override_authority(
    harness: QdrantHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    hostile: Dict[str, Any],
) -> None:
    for sub in ("a", "a2", "b"):
        (tmp_path / sub).mkdir()
    db, ids = _seed_three(harness, monkeypatch, tmp_path)
    found = _ids(
        run(
            scoped_search(
                db, harness, QUERY, org_id=ORG_A, project_id=PROJECT_A, metadata=hostile
            )
        )
    )
    # Still works (not a blanket denial), and nothing outside the authority.
    assert found == [ids["a"]]


# --- E: organisation-level correspondence -----------------------------------


def test_org_level_letter_has_null_project_and_is_not_served_to_a_project_search(
    harness: QdrantHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = Database()
    row = _letter(ORG_A, "", "ORG/001")
    run(upload_and_process(monkeypatch, tmp_path, db, harness, row, body=BODY))
    [point] = _points_for(harness, str(row["_id"]))
    assert point.payload["project_id"] is None
    assert point.payload["org_id"] == ORG_A
    # Same answer as build_scope_query: a project selection matches by equality.
    assert _ids(run(scoped_search(db, harness, QUERY, org_id=ORG_A, project_id=PROJECT_A))) == []
    # The empty-string legacy sentinel matches nothing canonical.
    assert _ids(run(scoped_search(db, harness, QUERY, org_id=ORG_A, project_id=""))) == []


# --- G: ObjectId / string normalisation ---------------------------------------


def test_objectid_typed_document_row_is_found_by_a_string_scoped_search(
    harness: QdrantHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = Database()
    row = _letter(ObjectId(ORG_A), ObjectId(PROJECT_A), "OID/001")
    run(upload_and_process(monkeypatch, tmp_path, db, harness, row, body=BODY))
    [point] = _points_for(harness, str(row["_id"]))
    assert point.payload["org_id"] == ORG_A and point.payload["project_id"] == PROJECT_A
    assert _ids(run(scoped_search(db, harness, QUERY, org_id=ORG_A, project_id=PROJECT_A))) == [
        str(row["_id"])
    ]


# --- A / H / J: legacy points, and canonical beside them ----------------------


def _legacy_point(org: str, project: str, document_id: str) -> Dict[str, Any]:
    """Exactly what the pre-fix writer (LangChain add_texts) stored."""
    chunk_id = f"{document_id}-legacy0000000000000000"
    return {
        "id": correspondence_point_id(document_id, chunk_id),
        "payload": {
            "page_content": BODY,
            "metadata": {
                "document_id": document_id,
                "organization_id": org,
                "project_id": project,
                "uploadType": "incoming",
                "chunk_id": chunk_id,
                "chunk_index": 0,
                "source": "document_processing",
                "key_reply_points": [ADVISORY_TEXT],
            },
        },
    }


def _upsert_raw(harness: QdrantHarness, points: List[Dict[str, Any]]) -> None:
    models = harness.writer._qdrant_models
    harness.client.upsert(
        collection_name=harness.config.qdrant_collection,
        points=[
            models.PointStruct(id=p["id"], vector=embed_text(BODY), payload=p["payload"])
            for p in points
        ],
        wait=True,
    )


def test_the_pre_fix_payload_shape_is_invisible_to_scoped_search(
    harness: QdrantHarness,
) -> None:
    """DI-B1's mechanism, pinned: why legacy points must be reindexed.

    Legacy read compatibility is deliberately NOT implemented: these points
    were never reachable by a scoped reader, and they carry reply advice
    (DI-N6) that a compatibility read would surface as evidence.
    """
    db = Database()
    legacy = _legacy_point(ORG_A, PROJECT_A, str(ObjectId()))
    _upsert_raw(harness, [legacy])
    unfiltered = run(harness.reader.search(embed_text(QUERY), {}, limit=5, allow_global=True))
    assert [hit["id"] for hit in unfiltered] == [legacy["id"]]
    assert _ids(run(scoped_search(db, harness, QUERY, org_id=ORG_A, project_id=PROJECT_A))) == []


def test_legacy_and_canonical_points_coexist_without_leakage(
    harness: QdrantHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = Database()
    _upsert_raw(
        harness,
        [
            _legacy_point(ORG_A, PROJECT_A, str(ObjectId())),
            _legacy_point(ORG_B, PROJECT_B, str(ObjectId())),
        ],
    )
    row = _letter(ORG_A, PROJECT_A, "NEW/001")
    run(upload_and_process(monkeypatch, tmp_path, db, harness, row, body=BODY))
    response = run(scoped_search(db, harness, QUERY, org_id=ORG_A, project_id=PROJECT_A))
    assert _ids(response) == [str(row["_id"])]
    assert all("ADVISORY-REPLY-POINT" not in repr(r.payload) for r in response.results)
    assert _ids(run(scoped_search(db, harness, QUERY, org_id=ORG_B, project_id=PROJECT_B))) == []


# --- Q: reindex overwrites in place --------------------------------------------


def test_reindex_is_idempotent_and_replaces_a_legacy_point_in_place(
    harness: QdrantHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    db = Database()
    row = _letter(ORG_A, PROJECT_A, "RE/001")
    document_id = str(row["_id"])
    # The document's own pre-fix point, as production holds it today.
    _upsert_raw(harness, [_legacy_point(ORG_A, PROJECT_A, document_id)])
    run(upload_and_process(monkeypatch, tmp_path, db, harness, row, body=BODY))
    first = sorted(str(p.id) for p in harness.scroll())

    from rbac_backend.tests.correspondence_vector_harness import database_service

    writer = database_service(db, harness)
    run(writer.create_embeddings_for_document(document_id))
    run(writer.create_embeddings_for_document(document_id))
    second = sorted(str(p.id) for p in harness.scroll())

    assert first == second
    points = harness.scroll()
    assert all(
        p.payload.get(PAYLOAD_SCHEMA_VERSION_FIELD) == CORRESPONDENCE_PAYLOAD_SCHEMA_VERSION
        for p in points
    ), "the legacy point for this document survived the reindex"
    assert len(points) == len(_points_for(harness, document_id))


# --- L: the bulk path -----------------------------------------------------------


def test_bulk_rows_are_created_by_the_single_upload_path() -> None:
    """Bulk shares the writer: each row goes through ``create_document``.

    ``create_document`` queues the same processing job the single upload does
    (when the row's ``ocr_enabled`` is true, the template default), and that job
    runs ``process_document_async`` - the seam every test above drives.
    """
    from rbac_backend.routers.documents import DocumentController

    single_file = inspect.getsource(DocumentController._process_single_file)
    create = inspect.getsource(DocumentController.create_document)
    assert "self.create_document(" in single_file
    assert "queue_document_processing(" in create


# --- O: drafting retrieval ------------------------------------------------------


def test_drafting_agent_cites_the_uploaded_letter(
    harness: QdrantHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from rbac_backend.agents.models import AgentRequest
    from rbac_backend.agents.service import DraftingAgentService

    db = Database()
    row = _letter(ORG_A, PROJECT_A, "DRAFT/001")
    run(upload_and_process(monkeypatch, tmp_path, db, harness, row, body=BODY))
    (tmp_path / "b").mkdir()
    foreign = _letter(ORG_B, PROJECT_B, "DRAFT/B01")
    run(upload_and_process(monkeypatch, tmp_path / "b", db, harness, foreign, body=BODY))

    class _Llm:
        async def generate(self, prompt: str, **_kwargs: Any) -> str:
            return "draft"

    class _Observability:
        async def log_run(self, **_kwargs: Any) -> None:
            return None

    agent = DraftingAgentService(db, retrieval_service(db, harness), _Llm(), _Observability())
    response = run(
        agent.run(
            AgentRequest(
                org_id=ORG_A,
                project_id=PROJECT_A,
                incoming_text="Please respond on the viaduct pier delay",
                user_goal=QUERY,
            ),
            user_for(ORG_A, PROJECT_A),
        )
    )
    cited = [citation.document_id for citation in response.citations]
    assert cited == [str(row["_id"])]


def test_langgraph_drafting_letter_search_finds_the_uploaded_letter(
    harness: QdrantHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The LangGraph pipeline's correspondence query, issued as it issues it."""
    from rbac_backend.ai_workflows.langgraph import letter_pipeline

    source = inspect.getsource(letter_pipeline)
    # The request below mirrors the pipeline; fail if the pipeline drifts.
    assert 'metadata={"doc_type": "letter"}' in source
    assert "strategy=SearchStrategy.RAG_FUSION" in source

    db = Database()
    row = _letter(ORG_A, PROJECT_A, "LG/001")
    run(upload_and_process(monkeypatch, tmp_path, db, harness, row, body=BODY))
    from rbac_backend.retrieval.models import SearchFilters, SearchRequest

    request = SearchRequest(
        query=QUERY,
        strategy=SearchStrategy.RAG_FUSION,
        limit=6,
        filters=SearchFilters(
            org_id=ORG_A, project_id=PROJECT_A, metadata={"doc_type": "letter"}
        ),
        use_enriched_text=True,
    )
    response = run(
        retrieval_service(db, harness).search(request, user_for(ORG_A, PROJECT_A), log_run=False)
    )
    assert _ids(response) == [str(row["_id"])]
    assert response.results[0].snippet


# --- /documents vector search (DI-N2) ----------------------------------------


def test_document_vector_search_is_tenant_bounded_and_requires_an_org(
    harness: QdrantHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from rbac_backend.retrieval.vector_client import VectorScopeError

    for sub in ("a", "a2", "b"):
        (tmp_path / sub).mkdir()
    _db, ids = _seed_three(harness, monkeypatch, tmp_path)
    service = harness.writer

    as_a = run(service.similarity_search(QUERY, org_ids=[ORG_A], top_k=10))
    assert {r["metadata"]["document_id"] for r in as_a} == {ids["a"], ids["a2"]}
    assert all(r["text"] for r in as_a)
    only_a = run(
        service.similarity_search(QUERY, org_ids=[ORG_A], project_ids=[PROJECT_A], top_k=10)
    )
    assert [r["metadata"]["document_id"] for r in only_a] == [ids["a"]]
    with pytest.raises(VectorScopeError):
        run(service.similarity_search(QUERY, org_ids=[], top_k=10))


# --- unit: the canonical builder -----------------------------------------------


def _doc(**overrides: Any) -> Dict[str, Any]:
    base = {
        "_id": ObjectId(),
        "organization_id": ORG_A,
        "project_id": PROJECT_A,
        "uploadType": "outgoing",
        "letterNo": "U/001",
        "subject": "s",
        "summary": "sum",
        "key_reply_points": [ADVISORY_TEXT],
        "points_to_address": ["x"],
        "suggested_response_considerations": ["y"],
    }
    base.update(overrides)
    return base


def test_builder_refuses_to_publish_an_unscoped_point() -> None:
    for missing in (None, "", "   "):
        with pytest.raises(CorrespondencePayloadError):
            build_correspondence_chunks(_doc(organization_id=missing), ["t"], embedding_model="m")
    for bogus in ({"$ne": None}, ["a", "b"], True):
        with pytest.raises(CorrespondencePayloadError):
            build_correspondence_chunks(_doc(organization_id=bogus), ["t"], embedding_model="m")


def test_builder_output_is_canonical_deterministic_and_advice_free() -> None:
    doc = _doc(
        organization_id=ObjectId(ORG_A),
        project_id=ObjectId(PROJECT_A),
        # descriptive fields may not smuggle authority in
        reference_chain="R-1",
        extracted_tags=["org_id"],
    )
    first = build_correspondence_chunks(doc, ["alpha", "beta"], embedding_model="m")
    again = build_correspondence_chunks(doc, ["alpha", "beta"], embedding_model="m")
    assert first == again
    for chunk in first:
        payload = chunk["payload"]
        assert_canonical_correspondence_payload(payload)
        assert payload["org_id"] == ORG_A and payload["project_id"] == PROJECT_A
        assert payload["doc_type"] == "letter"
        assert chunk["point_id"] == payload["qdrant_point_id"]
        uuid.UUID(chunk["point_id"])
        assert not ADVISORY_FIELDS.intersection(payload)
        assert not ADVISORY_FIELDS.intersection(chunk["metadata"])
        # The Mongo `document_vectors` row contract is unchanged.
        assert chunk["metadata"]["organization_id"] == ORG_A
    assert canonical_scope_id(ObjectId(ORG_A), "x") == canonical_scope_id(f" {ORG_A} ", "x")
    assert not ADVISORY_FIELDS.intersection(DESCRIPTIVE_FIELDS)
    assert "summary" in DESCRIPTIVE_FIELDS


def test_writer_refuses_a_non_canonical_point_before_touching_qdrant() -> None:
    from rbac_backend.services.langchain_vector_service import LangChainVectorService

    service = LangChainVectorService.__new__(LangChainVectorService)
    service._enabled = True
    service._vector_store = object()

    async def _forbidden(*_a: Any, **_k: Any) -> bool:
        raise AssertionError("nothing may be deleted for a refused write")

    service.delete_document = _forbidden
    legacy_shape = {"text": "t", "metadata": {"document_id": "d"}}
    with pytest.raises(CorrespondencePayloadError):
        run(service.replace_document([legacy_shape]))
    unscoped = build_correspondence_chunks(_doc(), ["t"], embedding_model="m")[0]
    unscoped["payload"]["org_id"] = ""
    with pytest.raises(CorrespondencePayloadError):
        run(service.replace_document([unscoped]))
    advised = build_correspondence_chunks(_doc(), ["t"], embedding_model="m")[0]
    advised["payload"]["key_reply_points"] = [ADVISORY_TEXT]
    with pytest.raises(CorrespondencePayloadError):
        run(service.replace_document([advised]))
    mixed = build_correspondence_chunks(_doc(), ["t"], embedding_model="m") + build_correspondence_chunks(
        _doc(organization_id=ORG_B), ["t"], embedding_model="m"
    )
    with pytest.raises(CorrespondencePayloadError):
        run(service.replace_document(mixed))


def test_database_writer_marks_an_org_less_document_failed_and_publishes_nothing(
    harness: QdrantHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from rbac_backend.tests.correspondence_vector_harness import (
        database_service,
        letter_metadata,
    )

    row = _letter(None, PROJECT_A, "NOORG/001")
    db = Database([row])
    writer = database_service(db, harness)
    created = run(
        writer.save_document_data(
            document_id=str(row["_id"]),
            file_path="NOORG-001.pdf",
            parsed_metadata=letter_metadata(letter_no="NOORG/001", subject="s", body=BODY),
            full_text=BODY,
            embedding_text=BODY,
        )
    )
    assert created == 0
    assert harness.scroll() == []
    # Visible, not silent: the sync record and the partial failure both say so.
    status = run(db.vector_sync_status.find_one({"document_id": str(row["_id"])}))
    assert status["sync_status"] == "error"
    assert "organisation" in status["details"]
    assert "organisation" in writer.partial_failures["embeddings"]["message"]


# --- the storage-sync legacy repair writer ------------------------------------


def test_storage_sync_legacy_repair_writes_canonical_points_from_document_authority(
    harness: QdrantHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rbac_backend.routers import storage_sync

    row = _letter(ORG_A, PROJECT_A, "REPAIR/001", processing_status="completed")
    document_id = str(row["_id"])
    db = Database([row])
    # Stale Mongo vector rows that name another tenant: authority must come
    # from the canonical document, never from the stored rows.
    for index, text in enumerate([BODY, BODY + " second"]):
        run(
            db.document_vectors.insert_one(
                {
                    "document_id": document_id,
                    "organization_id": ORG_B,
                    "project_id": PROJECT_B,
                    "chunk_index": index,
                    "text": text,
                }
            )
        )
    monkeypatch.setattr(storage_sync, "LangChainVectorService", lambda _config: harness.writer)

    async def _count(_config, doc_id):
        return len(_points_for(harness, doc_id))

    monkeypatch.setattr(storage_sync, "_fetch_qdrant_document_count", _count)
    result = run(storage_sync._resync_document_vectors(document_id, db, harness.config))

    assert result["status"] == "synced", result
    points = _points_for(harness, document_id)
    assert len(points) == 2
    for point in points:
        assert_canonical_correspondence_payload(point.payload)
        assert point.payload["org_id"] == ORG_A
        assert point.payload["project_id"] == PROJECT_A
    # Chunk-level hits: both chunks of the one document.
    assert _ids(
        run(scoped_search(Database([row]), harness, QUERY, org_id=ORG_A, project_id=PROJECT_A))
    ) == [document_id, document_id]
    assert _ids(
        run(scoped_search(Database([row]), harness, QUERY, org_id=ORG_B, project_id=PROJECT_B))
    ) == []


# --- the /documents vector-search route (DI-N2) --------------------------------


def _controller(db: Database, harness: QdrantHarness, monkeypatch: pytest.MonkeyPatch):
    from types import SimpleNamespace

    from rbac_backend.routers import documents
    from rbac_backend.services.authorization_service import AuthorizationService
    from rbac_backend.services.document_service import DocumentService

    class _Policy:
        async def authorize(self, *_a: Any, **_k: Any) -> None:
            return None

        async def authorize_document(self, *_a: Any, **_k: Any) -> None:
            return None

    monkeypatch.setattr(documents, "DocumentProcessingConfig", lambda: harness.config)
    monkeypatch.setattr(documents, "LangChainVectorService", lambda _config: harness.writer)
    controller = documents.DocumentController(
        document_service=DocumentService(db),
        file_service=SimpleNamespace(),
        export_service=SimpleNamespace(),
        auth_service=AuthorizationService(),
        bulk_upload_service=SimpleNamespace(),
    )
    controller.policy_service = _Policy()  # type: ignore[assignment]
    return controller


def test_document_vector_search_route_is_bounded_by_the_callers_scope(
    harness: QdrantHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from rbac_backend.core.security import CurrentUser
    from rbac_backend.utils.error_handler import AuthorizationError, DocumentError

    for sub in ("a", "a2", "b"):
        (tmp_path / sub).mkdir()
    db, ids = _seed_three(harness, monkeypatch, tmp_path)
    controller = _controller(db, harness, monkeypatch)
    no_filters = {"organization_id": None, "project_id": None, "uploadType": None}

    def _found(user, filters=no_filters):
        response = run(
            controller.vector_search_documents(
                query=QUERY, limit=10, filters=dict(filters), current_user=user
            )
        )
        return sorted({r["document_id"] for r in response["results"]})

    org_admin = CurrentUser(
        id="u1", username="oa", email="oa@example.com", roles=["orgadmin"], organization_id=ORG_A
    )
    project_user = user_for(ORG_A, PROJECT_A)
    assert _found(org_admin) == sorted([ids["a"], ids["a2"]])
    assert _found(project_user) == [ids["a"]]
    # Naming a foreign organisation is refused, not widened.
    with pytest.raises(AuthorizationError):
        _found(org_admin, {**no_filters, "organization_id": ORG_B})
    # A global role with no organisation selected gets a 400, never all tenants.
    superadmin = CurrentUser(id="u2", username="sa", email="sa@example.com", roles=["superadmin"])
    with pytest.raises(DocumentError) as refused:
        _found(superadmin)
    assert refused.value.http_status == 400
    assert _found(superadmin, {**no_filters, "organization_id": ORG_B}) == [ids["b"]]


def test_a_legacy_only_document_is_reported_out_of_sync_until_reindexed(
    harness: QdrantHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rbac_backend.routers import storage_sync

    row = _letter(ORG_A, PROJECT_A, "LEG/001", processing_status="completed")
    document_id = str(row["_id"])
    db = Database([row])
    run(
        db.document_vectors.insert_one(
            {"document_id": document_id, "chunk_index": 0, "text": BODY}
        )
    )
    _upsert_raw(harness, [_legacy_point(ORG_A, PROJECT_A, document_id)])

    def _count() -> int:
        return harness.client.count(
            collection_name=harness.config.qdrant_collection,
            count_filter=storage_sync._qdrant_document_filter(
                harness.writer._qdrant_models, document_id
            ),
            exact=True,
        ).count

    # The legacy point exists but is not counted: 0 canonical vs 1 Mongo row.
    assert len(harness.scroll()) == 1
    assert _count() == 0

    monkeypatch.setattr(storage_sync, "LangChainVectorService", lambda _config: harness.writer)

    async def _fetch(_config, doc_id):
        return _count()

    monkeypatch.setattr(storage_sync, "_fetch_qdrant_document_count", _fetch)
    result = run(storage_sync._resync_document_vectors(document_id, db, harness.config))
    assert result["status"] == "synced"
    assert _count() == 1
    [point] = harness.scroll()  # replaced in place, not duplicated
    assert_canonical_correspondence_payload(point.payload)


# --- backfill census (read-only) -----------------------------------------------


def test_census_classifies_every_writer_contract_and_writes_nothing(
    harness: QdrantHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import importlib.util

    from rbac_backend.retrieval.correspondence_payload import (
        POINT_CANONICAL,
        POINT_LEGACY_ENVELOPE,
        POINT_NATIVE_FLAT,
        POINT_UNSCOPED,
        classify_vector_payload,
    )

    script = Path(__file__).resolve().parents[3] / "scripts" / "correspondence_vector_census.py"
    spec = importlib.util.spec_from_file_location("correspondence_vector_census", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]

    db = Database()
    run(upload_and_process(monkeypatch, tmp_path, db, harness, _letter(ORG_A, PROJECT_A, "C/1"), body=BODY))
    legacy_doc = str(ObjectId())
    _upsert_raw(
        harness,
        [
            _legacy_point(ORG_A, PROJECT_A, legacy_doc),
            {"id": str(uuid.uuid4()), "payload": {"org_id": ORG_B, "project_id": PROJECT_B, "document_id": "k", "text": "contract chunk"}},
            {"id": str(uuid.uuid4()), "payload": {"page_content": "x", "metadata": {"document_id": "u", "organization_id": ""}}},
        ],
    )
    before = sorted((str(p.id), repr(p.payload)) for p in harness.scroll())
    report = module.census(harness.client, harness.config.qdrant_collection, batch_size=2)
    after = sorted((str(p.id), repr(p.payload)) for p in harness.scroll())

    assert before == after
    assert report["totals"] == {
        POINT_CANONICAL: 1,
        POINT_LEGACY_ENVELOPE: 1,
        POINT_NATIVE_FLAT: 1,
        POINT_UNSCOPED: 1,
    }
    assert report["_legacy_document_ids"][ORG_A] == [legacy_doc]
    assert report["legacy_points_with_reply_advice"] == 1
    assert classify_vector_payload({}) == POINT_UNSCOPED


# --- reply advice arriving as chunk TEXT (review finding, DI-N6 class) ---------

REPORT_WITH_ADVICE = (
    "1) Date: 01-08-2026\n"
    "2) Letter No: RPT/001\n"
    "5) Subject: Delay to viaduct pier foundations\n"
    "22) Summary: Notice of delay to the viaduct pier foundations.\n"
    "24) Key Reply Points - Points to be Addressed While Responding: "
    "ADVISORY-REPLY-POINT reserve rights on prolongation cost\n"
    "1) Deny liability: ADVISORY-REPLY-POINT the delay is the contractor's risk\n"
    "2) Ask for particulars: ADVISORY-REPLY-POINT within 28 days\n"
    "25) Full Content: " + BODY + "\n"
)


def test_report_derived_text_is_refused_visibly_and_nothing_is_published(
    harness: QdrantHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """No OCR text and no parsed full content: the text would be the LLM report.

    Driven on legacy_v0, where an empty OCR result reaches the whole-PDF LLM
    fallback. The refusal lives in ``DatabaseService._create_and_store_embeddings``,
    which both pipelines reach through the shared ``_save_results``. The report
    is not the letter, and cleaning it proved leaky (closure review M1a/M1b), so
    it is refused: no point, sync ``error``, a recorded partial failure.
    """
    from rbac_backend.models.document_metadata import ParsedDocumentMetadata

    db = Database()
    row = _letter(ORG_A, PROJECT_A, "RPT/001")
    processor = run(
        upload_and_process(
            monkeypatch,
            tmp_path,
            db,
            harness,
            row,
            body=BODY,
            pipeline="legacy_v0",
            report=REPORT_WITH_ADVICE,
            ocr_text="",
            metadata=ParsedDocumentMetadata(
                letter_no="RPT/001",
                subject="Delay to viaduct pier foundations",
                key_reply_points=[ADVISORY_TEXT],
            ),
        )
    )
    document_id = str(row["_id"])
    assert _points_for(harness, document_id) == []
    status = run(db.vector_sync_status.find_one({"document_id": document_id}))
    assert status["sync_status"] == "error"
    assert "extraction report" in status["details"]
    assert "ADVISORY-REPLY-POINT" not in status["details"]  # names the doc, never the text
    failure = processor.database_service.partial_failures["embeddings"]["message"]
    assert "extraction report" in failure and "ADVISORY-REPLY-POINT" not in failure
    assert run(scoped_search(db, harness, QUERY, org_id=ORG_A, project_id=PROJECT_A)).results == []


def test_the_same_letter_with_real_text_is_still_indexed(
    harness: QdrantHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Positive control for the refusal: OCR text present, nothing refused."""
    db = Database()
    row = _letter(ORG_A, PROJECT_A, "RPT/002")
    run(
        upload_and_process(
            monkeypatch, tmp_path, db, harness, row, body=BODY, report=REPORT_WITH_ADVICE
        )
    )
    points = _points_for(harness, str(row["_id"]))
    assert points
    assert "ADVISORY-REPLY-POINT" not in " ".join(str(p.payload) for p in points)


def test_builder_refuses_chunk_text_that_still_carries_reply_advice() -> None:
    with pytest.raises(CorrespondencePayloadError):
        build_correspondence_chunks(_doc(), ["24) Key Reply Points: deny"], embedding_model="m")
    ok = build_correspondence_chunks(_doc(), ["a letter"], embedding_model="m")[0]
    ok["payload"]["text"] = "10) **Key Reply Points**: deny"
    with pytest.raises(CorrespondencePayloadError):
        assert_canonical_correspondence_payload(ok["payload"])


def test_storage_sync_repair_refuses_rows_chunked_from_the_report(
    harness: QdrantHarness, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import HTTPException

    from rbac_backend.routers import storage_sync

    row = _letter(ORG_A, PROJECT_A, "RPTROWS/001", processing_status="completed")
    db = Database([row])
    for index, text in enumerate(["22) Summary: s\n24) Key Reply Points: deny", "1) Deny: x"]):
        run(
            db.document_vectors.insert_one(
                {"document_id": str(row["_id"]), "chunk_index": index, "text": text}
            )
        )
    monkeypatch.setattr(storage_sync, "LangChainVectorService", lambda _config: harness.writer)
    with pytest.raises(HTTPException) as refused:
        run(storage_sync._resync_document_vectors(str(row["_id"]), db, harness.config))
    assert refused.value.status_code == 409
    assert harness.scroll() == []


def test_document_vector_search_route_drops_withheld_and_unresolved_hits(
    harness: QdrantHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A stale point outlives a failed purge; the route must not serve it."""
    for sub in ("a", "a2", "b"):
        (tmp_path / sub).mkdir()
    db, ids = _seed_three(harness, monkeypatch, tmp_path)
    controller = _controller(db, harness, monkeypatch)
    org_admin = __import__("rbac_backend.core.security", fromlist=["CurrentUser"]).CurrentUser(
        id="u1", username="oa", email="oa@example.com", roles=["orgadmin"], organization_id=ORG_A
    )

    def _served():
        response = run(
            controller.vector_search_documents(
                query=QUERY,
                limit=10,
                filters={"organization_id": None, "project_id": None, "uploadType": None},
                current_user=org_admin,
            )
        )
        return response["results"]

    assert sorted({r["document_id"] for r in _served()}) == sorted([ids["a"], ids["a2"]])
    # Withheld after indexing (e.g. reprocess ended in human review) ...
    run(
        db.documents.update_one(
            {"_id": ObjectId(ids["a"])}, {"$set": {"processing_status": "human_review_required"}}
        )
    )
    # ... and a point whose document row is gone.
    run(db.documents.delete_one({"_id": ObjectId(ids["a2"])}))
    served = _served()
    assert served == []


# --- native chunks-collection reconcilers must not see correspondence points ---


def test_native_reconcilers_never_treat_correspondence_points_as_stale(
    harness: QdrantHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`list_chunk_ids` feeds deletes against `db.chunks`; letters have no such rows."""
    from rbac_backend.retrieval.reconcile import VectorReconciler
    from rbac_backend.tests.correspondence_vector_harness import DeterministicEmbeddingClient

    db = Database()
    row = _letter(ORG_A, PROJECT_A, "REC/001", processing_status="completed")
    run(upload_and_process(monkeypatch, tmp_path, db, harness, row, body=BODY))
    document_id = str(row["_id"])
    before = _points_for(harness, document_id)
    assert before

    scope = {"org_id": ORG_A, "project_id": PROJECT_A, "document_id": document_id}
    assert run(harness.reader.list_chunk_ids(scope)) == []

    deleted: List[Any] = []
    original_delete = harness.reader.delete

    async def _recording_delete(chunk_ids, namespace=None):
        deleted.extend(chunk_ids)
        return await original_delete(chunk_ids, namespace=namespace)

    monkeypatch.setattr(harness.reader, "delete", _recording_delete)
    result = run(
        VectorReconciler(db, DeterministicEmbeddingClient(), harness.reader).reconcile_document(
            document_id, ORG_A, PROJECT_A
        )
    )
    assert result["missing_in_mongo"] == 0 and result["removed"] == 0
    assert deleted == []
    assert len(_points_for(harness, document_id)) == len(before)


def test_vector_client_uses_the_builders_schema_marker() -> None:
    from rbac_backend.retrieval import correspondence_payload, vector_client

    assert vector_client.PAYLOAD_SCHEMA_VERSION_FIELD is correspondence_payload.PAYLOAD_SCHEMA_VERSION_FIELD
    assert (
        vector_client.CORRESPONDENCE_PAYLOAD_SCHEMA_VERSION
        is correspondence_payload.CORRESPONDENCE_PAYLOAD_SCHEMA_VERSION
    )
