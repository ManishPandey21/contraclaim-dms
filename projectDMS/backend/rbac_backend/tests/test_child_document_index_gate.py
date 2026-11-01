"""Phase 8 PR B: the mandatory live-index gate in front of child creation.

``ensure_indexes`` is a fail-open background task, so a startup log proves
nothing about the indexes that hold child identity. Child creation reads the
live ``index_information()`` on every call and fails closed - 503, nothing
written - unless ``uq_documents_child_identity`` is unique with its keys and
partial filter and ``ix_documents_children_by_parent`` exists.

The same gate is proven against a real replica set, including a dropped and a
non-unique index, in ``integration/test_enclosure_child_processing_mongo.py``.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest
from fastapi import status

from rbac_backend.services.child_document_service import (
    CHILD_DOCUMENT_INDEXES,
    ChildDocumentIndexGateError,
    ChildDocumentService,
    child_index_problems,
    require_child_document_indexes,
)


def _live_indexes() -> Dict[str, Dict[str, Any]]:
    """What ``index_information()`` reports for correctly built indexes."""
    info: Dict[str, Dict[str, Any]] = {"_id_": {"key": [("_id", 1)], "v": 2}}
    for spec in CHILD_DOCUMENT_INDEXES:
        entry: Dict[str, Any] = {
            "key": [(field, float(direction)) for field, direction in spec["keys"]],
            "v": 2,
            "partialFilterExpression": dict(spec["partialFilterExpression"]),
        }
        if spec.get("unique"):
            entry["unique"] = True
        info[spec["name"]] = entry
    return info


class _Documents:
    def __init__(self, info: Any) -> None:
        self.info = info
        self.writes: List[str] = []
        self.reads: List[Any] = []

    async def index_information(self) -> Dict[str, Any]:
        if isinstance(self.info, BaseException):
            raise self.info
        return self.info

    async def find_one(self, *args: Any, **kwargs: Any) -> Any:
        self.reads.append(args)
        return None

    async def insert_one(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        self.writes.append("insert_one")
        raise AssertionError("child creation must not write past a failed gate")


class _Db:
    def __init__(self, info: Any) -> None:
        self.documents = _Documents(info)


async def _gate(info: Any) -> None:
    await require_child_document_indexes(_Db(info))


# --- the four required cases ----------------------------------------------


async def test_missing_identity_unique_index_denies_child_creation() -> None:
    info = _live_indexes()
    del info["uq_documents_child_identity"]
    with pytest.raises(ChildDocumentIndexGateError) as refused:
        await _gate(info)
    assert refused.value.http_status == status.HTTP_503_SERVICE_UNAVAILABLE
    assert "uq_documents_child_identity is missing" in refused.value.problems


async def test_identity_index_present_but_not_unique_denies() -> None:
    info = _live_indexes()
    info["uq_documents_child_identity"].pop("unique")
    with pytest.raises(ChildDocumentIndexGateError) as refused:
        await _gate(info)
    assert refused.value.problems == ["uq_documents_child_identity is not unique"]

    info["uq_documents_child_identity"]["unique"] = False
    with pytest.raises(ChildDocumentIndexGateError):
        await _gate(info)


async def test_missing_parent_lookup_index_denies() -> None:
    info = _live_indexes()
    del info["ix_documents_children_by_parent"]
    with pytest.raises(ChildDocumentIndexGateError) as refused:
        await _gate(info)
    assert refused.value.problems == ["ix_documents_children_by_parent is missing"]


async def test_correct_indexes_allow_child_creation() -> None:
    await _gate(_live_indexes())
    assert child_index_problems(_live_indexes()) == []


# --- the same name is not the same index -----------------------------------


async def test_a_same_named_index_with_other_keys_denies() -> None:
    info = _live_indexes()
    info["uq_documents_child_identity"]["key"] = [("parent_document_id", 1), ("sha256", 1)]
    with pytest.raises(ChildDocumentIndexGateError) as refused:
        await _gate(info)
    assert "has keys" in refused.value.problems[0]


async def test_a_unique_index_without_the_partial_filter_denies() -> None:
    info = _live_indexes()
    info["uq_documents_child_identity"].pop("partialFilterExpression")
    with pytest.raises(ChildDocumentIndexGateError) as refused:
        await _gate(info)
    assert refused.value.problems == ["uq_documents_child_identity has the wrong partial filter"]


async def test_unreadable_index_information_denies() -> None:
    with pytest.raises(ChildDocumentIndexGateError) as refused:
        await _gate(RuntimeError("not authorized on documents to execute listIndexes"))
    assert refused.value.problems == ["index information unavailable"]


async def test_create_child_refuses_before_reading_the_parent() -> None:
    """The gate is the first thing ``create_child`` does once the request is valid."""
    info = _live_indexes()
    del info["uq_documents_child_identity"]
    db = _Db(info)
    service = ChildDocumentService(db=db, policy=object())
    with pytest.raises(ChildDocumentIndexGateError):
        await service.create_child(
            object(),
            parent_document_id="0" * 24,
            relationship_type="enclosure",
            file_object_id="fo-1",
        )
    assert db.documents.reads == [] and db.documents.writes == []


async def test_the_gate_is_read_on_every_call_never_cached() -> None:
    """A verdict cached at the first call would outlive an index dropped later."""
    db = _Db(_live_indexes())
    await require_child_document_indexes(db)
    del db.documents.info["uq_documents_child_identity"]
    with pytest.raises(ChildDocumentIndexGateError):
        await require_child_document_indexes(db)
