"""Regression tests for cascading cleanup when a letter/document is deleted.

Contract pinned by this file:

- Deleting a document removes inbound reference entries and backlinks from
  every other document, clears the deleted document's own link arrays, purges
  its reference-sync queue entries, deletes its FalkorDB node (with all
  edges), and removes its vector records.
- Unrelated links on other documents survive untouched.
- The cascade is idempotent — a second run is a clean no-op.
- A FalkorDB outage is recorded in the summary's ``errors`` but does not
  abort the Mongo cleanup (fail-visible, not fail-total).
- A normCode still owned by another live document skips graph-node deletion
  and says so explicitly ("skipped_shared_code").
- ``FalkorGraphService.delete_letter`` issues a DETACH DELETE keyed by the
  normalized code, treats disabled config as a no-op, and propagates
  failures as FalkorGraphError.
- ``delete_document`` keeps its truthiness contract and still raises
  DocumentConflictError on revision conflicts (it must not be rewrapped).
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional

import pytest
from bson import ObjectId

from rbac_backend.services.document_service import (
    DocumentConflictError,
    DocumentDeletionResult,
    DocumentService,
)
from rbac_backend.services.falkor_graph_service import (
    FalkorGraphConfig,
    FalkorGraphError,
    FalkorGraphService,
)
from rbac_backend.services.letter_service import LetterService


# --------------------------------------------------------------------------- #
# Narrow Mongo fakes: implement exactly the query/update shapes the cascade    #
# issues, and fail loudly (no silent match) on anything else.                  #
# --------------------------------------------------------------------------- #


def _norm_id(value: Any) -> str:
    return str(value)


def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for key, cond in query.items():
        if key == "$or":
            if not any(_matches(doc, sub) for sub in cond):
                return False
            continue
        if "." in key:
            # Dotted array-field match, e.g. "references.documentId": <id>
            field, sub = key.split(".", 1)
            entries = doc.get(field) or []
            if not any(
                isinstance(entry, dict) and _norm_id(entry.get(sub)) == _norm_id(cond)
                for entry in entries
            ):
                return False
            continue
        value = doc.get(key)
        if isinstance(cond, dict):
            if "$ne" in cond:
                if _norm_id(value) == _norm_id(cond["$ne"]):
                    return False
                continue
            raise AssertionError(f"FakeCollection: unsupported operator in {key}: {cond}")
        if key == "_id":
            if _norm_id(value) != _norm_id(cond):
                return False
        elif value != cond:
            return False
    return True


def _apply_update(doc: Dict[str, Any], update: Dict[str, Any]) -> None:
    for key, value in update.get("$set", {}).items():
        doc[key] = value
    for key, value in update.get("$inc", {}).items():
        doc[key] = int(doc.get(key) or 0) + value
    for field, criteria in update.get("$pull", {}).items():
        entries = doc.get(field) or []
        doc[field] = [
            entry
            for entry in entries
            if not (
                isinstance(entry, dict)
                and all(_norm_id(entry.get(k)) == _norm_id(v) for k, v in criteria.items())
            )
        ]


class FakeCollection:
    def __init__(self, docs: Optional[Iterable[Dict[str, Any]]] = None) -> None:
        self.docs: List[Dict[str, Any]] = [dict(d) for d in (docs or [])]

    async def find_one(self, query: Dict[str, Any], projection: Any = None):
        for doc in self.docs:
            if _matches(doc, query):
                return doc
        return None

    async def update_one(self, query: Dict[str, Any], update: Dict[str, Any], **_kw):
        for doc in self.docs:
            if _matches(doc, query):
                _apply_update(doc, update)
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    async def update_many(self, query: Dict[str, Any], update: Dict[str, Any], **_kw):
        modified = 0
        for doc in self.docs:
            if _matches(doc, query):
                _apply_update(doc, update)
                modified += 1
        return SimpleNamespace(matched_count=modified, modified_count=modified)

    async def delete_one(self, query: Dict[str, Any]):
        for index, doc in enumerate(self.docs):
            if _matches(doc, query):
                del self.docs[index]
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    async def delete_many(self, query: Dict[str, Any]):
        keep = [doc for doc in self.docs if not _matches(doc, query)]
        removed = len(self.docs) - len(keep)
        self.docs = keep
        return SimpleNamespace(deleted_count=removed)

    def get(self, doc_id: Any) -> Dict[str, Any]:
        for doc in self.docs:
            if _norm_id(doc.get("_id")) == _norm_id(doc_id):
                return doc
        raise AssertionError(f"document {doc_id} not found in fake collection")


class StubGraphIngestion:
    """Records Falkor removal calls; optionally simulates an outage."""

    def __init__(self, *, result: Any = True, error: Optional[Exception] = None) -> None:
        self.calls: List[str] = []
        self.result = result
        self.error = error

    def remove_document_from_falkor(self, document_id, document, *, raise_on_error=False):
        self.calls.append(str(document_id))
        if self.error is not None:
            raise self.error
        return self.result


class StubFalkorService:
    def __init__(self) -> None:
        self.deleted: List[str] = []

    def delete_letter(self, code: str) -> bool:
        self.deleted.append(code)
        return True


def _build_world():
    """One deleted letter, one citing doc, one cited doc, one bystander."""
    target_oid = ObjectId()
    citing_oid = ObjectId()
    cited_oid = ObjectId()
    bystander_oid = ObjectId()
    deleted_id = str(target_oid)

    documents = FakeCollection(
        [
            {
                "_id": target_oid,
                "letterNo": "LET-100",
                "letterNoNormalized": "let-100",
                "organization_id": "org-1",
                "project_id": "proj-1",
                "lifecycle_state": "active",
                "_revision": 3,
                "references": [{"documentId": str(cited_oid), "source": "parser"}],
                "referencedBy": [{"documentId": str(citing_oid), "source": "parser"}],
            },
            {
                "_id": citing_oid,
                "letterNo": "LET-200",
                "letterNoNormalized": "let-200",
                "lifecycle_state": "active",
                "references": [
                    {"documentId": deleted_id, "source": "parser"},
                    {"documentId": str(bystander_oid), "source": "manual"},
                ],
                "referencedBy": [],
            },
            {
                "_id": cited_oid,
                "letterNo": "LET-300",
                "letterNoNormalized": "let-300",
                "lifecycle_state": "active",
                "references": [],
                "referencedBy": [
                    {"documentId": deleted_id, "source": "parser"},
                    {"documentId": str(bystander_oid), "source": "manual"},
                ],
            },
            {
                "_id": bystander_oid,
                "letterNo": "LET-400",
                "letterNoNormalized": "let-400",
                "lifecycle_state": "active",
                "references": [{"documentId": str(citing_oid), "source": "manual"}],
                "referencedBy": [],
            },
        ]
    )
    queue = FakeCollection(
        [
            {"_id": ObjectId(), "document_id": deleted_id, "reference_key": "letter:let-999", "status": "pending"},
            {"_id": ObjectId(), "document_id": str(citing_oid), "reference_key": f"document:{deleted_id}", "status": "pending"},
            {"_id": ObjectId(), "document_id": str(citing_oid), "reference_key": "letter:let-777", "status": "pending"},
        ]
    )
    vectors = FakeCollection(
        [
            {"_id": ObjectId(), "document_id": deleted_id, "vector_ref": "v1"},
            {"_id": ObjectId(), "document_id": deleted_id, "vector_ref": "v2"},
            {"_id": ObjectId(), "document_id": str(citing_oid), "vector_ref": "v3"},
        ]
    )
    db = SimpleNamespace(
        documents=documents,
        reference_sync_queue=queue,
        document_vectors=vectors,
        letters=FakeCollection(),
    )
    return db, {
        "target": target_oid,
        "citing": citing_oid,
        "cited": cited_oid,
        "bystander": bystander_oid,
        "deleted_id": deleted_id,
    }


def _make_service(db, *, ingestion: Optional[StubGraphIngestion] = None) -> DocumentService:
    service = DocumentService(db=db)
    service.graph_ingestion = ingestion if ingestion is not None else StubGraphIngestion()

    async def _fake_qdrant(raw_doc, doc_id):
        service._qdrant_calls = getattr(service, "_qdrant_calls", [])
        service._qdrant_calls.append(doc_id)
        return True

    service._delete_qdrant_vectors = _fake_qdrant
    return service


# --------------------------------------------------------------------------- #
# Cascade contract                                                             #
# --------------------------------------------------------------------------- #


async def test_cascade_removes_all_link_records_and_spares_unrelated_ones():
    db, ids = _build_world()
    ingestion = StubGraphIngestion()
    service = _make_service(db, ingestion=ingestion)

    summary = await service.cascade_delete_cleanup(ids["deleted_id"])

    citing = db.documents.get(ids["citing"])
    cited = db.documents.get(ids["cited"])
    bystander = db.documents.get(ids["bystander"])
    target = db.documents.get(ids["target"])

    # Inbound references and backlinks to the deleted letter are gone...
    assert [r["documentId"] for r in citing["references"]] == [str(ids["bystander"])]
    assert [r["documentId"] for r in cited["referencedBy"]] == [str(ids["bystander"])]
    # ...unrelated links survive ("still works" case)...
    assert bystander["references"] == [{"documentId": str(ids["citing"]), "source": "manual"}]
    # ...and the deleted document holds no relationship records either.
    assert target["references"] == []
    assert target["referencedBy"] == []

    # Queue: only the unrelated pending entry survives.
    assert [q["reference_key"] for q in db.reference_sync_queue.docs] == ["letter:let-777"]

    # Vectors: only the other document's chunk survives.
    assert [v["vector_ref"] for v in db.document_vectors.docs] == ["v3"]

    # Graph: node deleted exactly once, keyed by the deleted document.
    assert ingestion.calls == [ids["deleted_id"]]

    assert summary["inbound_references_removed"] == 1
    assert summary["backlinks_removed"] == 1
    assert summary["own_links_cleared"] is True
    assert summary["sync_queue_removed"] == 2
    assert summary["falkor_node_deleted"] is True
    assert summary["vector_chunks_removed"] == 2
    assert summary["qdrant_vectors_deleted"] is True
    assert summary["errors"] == []


async def test_cascade_is_idempotent():
    db, ids = _build_world()
    service = _make_service(db)

    await service.cascade_delete_cleanup(ids["deleted_id"])
    second = await service.cascade_delete_cleanup(ids["deleted_id"])

    assert second["inbound_references_removed"] == 0
    assert second["backlinks_removed"] == 0
    assert second["own_links_cleared"] is False
    assert second["sync_queue_removed"] == 0
    assert second["vector_chunks_removed"] == 0
    assert second["errors"] == []


async def test_falkor_outage_is_recorded_but_mongo_cleanup_completes():
    db, ids = _build_world()
    ingestion = StubGraphIngestion(error=FalkorGraphError("FalkorDB down"))
    service = _make_service(db, ingestion=ingestion)

    summary = await service.cascade_delete_cleanup(ids["deleted_id"])

    # The outage is loud, not silent...
    assert summary["falkor_node_deleted"] is False
    assert any("falkor_graph" in err for err in summary["errors"])
    # ...and the Mongo cleanup still happened.
    citing = db.documents.get(ids["citing"])
    assert [r["documentId"] for r in citing["references"]] == [str(ids["bystander"])]
    assert [q["reference_key"] for q in db.reference_sync_queue.docs] == ["letter:let-777"]


async def test_shared_norm_code_skips_graph_node_deletion():
    db, ids = _build_world()
    # Another live document owns the same normalized letter code.
    db.documents.docs.append(
        {
            "_id": ObjectId(),
            "letterNo": "LET/100",
            "letterNoNormalized": "let-100",
            "lifecycle_state": "active",
            "references": [],
            "referencedBy": [],
        }
    )
    ingestion = StubGraphIngestion()
    service = _make_service(db, ingestion=ingestion)

    summary = await service.cascade_delete_cleanup(ids["deleted_id"])

    assert summary["falkor_node_deleted"] == "skipped_shared_code"
    assert ingestion.calls == []
    assert summary["errors"] == []


# --------------------------------------------------------------------------- #
# delete_document contract                                                     #
# --------------------------------------------------------------------------- #


async def test_delete_document_soft_deletes_then_cascades():
    db, ids = _build_world()
    ingestion = StubGraphIngestion()
    service = _make_service(db, ingestion=ingestion)

    result = await service.delete_document(ids["deleted_id"])

    assert isinstance(result, DocumentDeletionResult)
    assert bool(result) is True
    assert db.documents.get(ids["target"])["lifecycle_state"] == "deleted"
    assert result.cascade["inbound_references_removed"] == 1
    assert ingestion.calls == [ids["deleted_id"]]


async def test_delete_document_missing_is_falsy_with_no_cascade():
    db, _ids = _build_world()
    ingestion = StubGraphIngestion()
    service = _make_service(db, ingestion=ingestion)

    result = await service.delete_document(str(ObjectId()))

    assert bool(result) is False
    assert result.cascade == {}
    assert ingestion.calls == []


async def test_delete_document_revision_conflict_raises_conflict_error():
    db, ids = _build_world()
    service = _make_service(db)

    # Stored revision is 3; a stale client presents 2.
    with pytest.raises(DocumentConflictError):
        await service.delete_document(ids["deleted_id"], expected_revision=2)

    # And the document is not deleted.
    assert db.documents.get(ids["target"])["lifecycle_state"] == "active"


# --------------------------------------------------------------------------- #
# FalkorGraphService.delete_letter contract                                    #
# --------------------------------------------------------------------------- #


def _falkor(enabled: bool = True) -> FalkorGraphService:
    config = FalkorGraphConfig(
        host="localhost",
        port=6379,
        graph_name="test-graph",
        password=None,
        enabled=enabled,
        cleanup=True,
    )
    return FalkorGraphService(config)


def test_delete_letter_disabled_is_noop():
    service = _falkor(enabled=False)
    calls: List[Any] = []
    service._execute = lambda *args, **kwargs: calls.append(args)

    assert service.delete_letter("LET-100") is False
    assert calls == []


def test_delete_letter_issues_detach_delete_with_normalized_code():
    service = _falkor()
    captured: Dict[str, Any] = {}

    def fake_execute(cypher, params=None, **_kwargs):
        captured["cypher"] = cypher
        captured["params"] = params

    service._execute = fake_execute

    assert service.delete_letter("LET/100 A") is True
    assert "DETACH DELETE" in captured["cypher"]
    assert captured["params"] == {"normCode": "let-100-a"}


def test_delete_letter_propagates_failures():
    service = _falkor()

    def failing_execute(*_args, **_kwargs):
        raise FalkorGraphError("connection refused")

    service._execute = failing_execute

    with pytest.raises(FalkorGraphError):
        service.delete_letter("LET-100")


def test_delete_letter_blank_code_is_noop():
    service = _falkor()
    calls: List[Any] = []
    service._execute = lambda *args, **kwargs: calls.append(args)

    assert service.delete_letter("") is False
    assert calls == []


# --------------------------------------------------------------------------- #
# LetterService.delete_letter graph cleanup                                    #
# --------------------------------------------------------------------------- #


async def test_letter_service_delete_cleans_graph_when_sole_owner(monkeypatch):
    letter_oid = ObjectId()
    db = SimpleNamespace(
        letters=FakeCollection([{"_id": letter_oid, "letter_no": "LET-500"}]),
        documents=FakeCollection(),
    )
    stub = StubFalkorService()
    monkeypatch.setattr(
        "rbac_backend.services.falkor_graph_service.FalkorGraphService",
        lambda *args, **kwargs: stub,
    )
    service = LetterService(db)

    assert await service.delete_letter(str(letter_oid)) is True
    assert db.letters.docs == []
    assert stub.deleted == ["LET-500"]


async def test_letter_service_delete_skips_graph_when_code_shared(monkeypatch):
    letter_oid = ObjectId()
    db = SimpleNamespace(
        letters=FakeCollection([{"_id": letter_oid, "letter_no": "LET-500"}]),
        documents=FakeCollection(
            [
                {
                    "_id": ObjectId(),
                    "letterNoNormalized": "let-500",
                    "lifecycle_state": "active",
                }
            ]
        ),
    )
    stub = StubFalkorService()
    monkeypatch.setattr(
        "rbac_backend.services.falkor_graph_service.FalkorGraphService",
        lambda *args, **kwargs: stub,
    )
    service = LetterService(db)

    assert await service.delete_letter(str(letter_oid)) is True
    assert stub.deleted == []


# --------------------------------------------------------------------------- #
# LangChainVectorService.delete_document disabled path                         #
# --------------------------------------------------------------------------- #


async def test_langchain_delete_document_disabled_is_noop():
    from rbac_backend.services.langchain_vector_service import LangChainVectorService

    config = SimpleNamespace(qdrant_enabled=False, qdrant_auth_configuration_error=None)
    service = LangChainVectorService(config)

    assert await service.delete_document("64b0f0c2a1b2c3d4e5f60718") is False
