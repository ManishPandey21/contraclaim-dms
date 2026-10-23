"""Phase 8 PR B P0: a child document is never a correspondence letter.

The Letter graph merges nodes on the normalised letter code alone, so the
guard lives inside ``GraphIngestionService`` itself: every caller - the
pipeline, extraction-review publication, reference refresh, the delete
cascade - goes through it. The end-to-end proof over real Mongo and the
production pipeline is ``integration/test_enclosure_child_processing_mongo.py``
(``test_p0_*``).
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from rbac_backend.graph.graph_ingestion_service import GraphIngestionService
from rbac_backend.models.document_metadata import ParsedDocumentMetadata
from rbac_backend.services.child_document_service import (
    is_child_document,
    without_letter_identity,
)


class _Falkor:
    enabled = True

    def __init__(self) -> None:
        self.upserts: List[Dict[str, Any]] = []
        self.deletes: List[str] = []

    def upsert_letter_with_refs(self, letter: Dict[str, Any], references: Any, **kwargs: Any) -> None:
        self.upserts.append(letter)

    def delete_letter(self, code: str) -> bool:
        self.deletes.append(code)
        return True


class _Adapter:
    def __init__(self) -> None:
        self.config = type("Config", (), {"enabled": True})()
        self.nodes: List[Any] = []

    def upsert_node(self, node: Any) -> Dict[str, Any]:
        self.nodes.append(node)
        return {}

    def upsert_edge(self, edge: Any) -> Dict[str, Any]:
        return {}


def _service() -> tuple:
    falkor, adapter = _Falkor(), _Adapter()
    return GraphIngestionService(adapter=adapter, falkor=falkor), falkor, adapter


def _document(**extra: Any) -> Dict[str, Any]:
    return {
        "organization_id": "org-a",
        "project_id": "proj-a1",
        "uploadType": "incoming",
        "letterNo": "CC/PLAN/0001",
        "processing_status": "completed",
        "lifecycle_state": "active",
        **extra,
    }


CHILD = {"parent_document_id": "0" * 24, "relationship_type": "enclosure"}


async def test_ingesting_a_child_writes_no_letter_node_anywhere() -> None:
    service, falkor, adapter = _service()
    metadata = ParsedDocumentMetadata(letter_no="CC/PLAN/0001")
    await service.ingest_document("child-1", _document(**CHILD), metadata, "fallback")
    assert falkor.upserts == [] and adapter.nodes == []


async def test_ingesting_a_top_level_letter_still_writes_its_node() -> None:
    """The contrast: the guard is child-specific."""
    service, falkor, adapter = _service()
    metadata = ParsedDocumentMetadata(letter_no="CC/PLAN/0001")
    await service.ingest_document("letter-1", _document(), metadata, "fallback")
    assert [u["normCode"] for u in falkor.upserts] == ["cc-plan-0001"]
    assert ["Letter"] in [n.labels for n in adapter.nodes]


def test_a_child_is_never_synced_to_falkor_even_without_a_letter_number() -> None:
    """With no letter number the payload falls back to the document id; a child
    must not get a Letter node under that key either."""
    service, falkor, _ = _service()
    service.sync_document_to_falkor("child-1", _document(letterNo=None, **CHILD), None, "incoming")
    assert falkor.upserts == []


def test_deleting_a_child_never_deletes_the_letter_its_stale_code_names() -> None:
    service, falkor, _ = _service()
    assert service.remove_document_from_falkor("child-1", _document(**CHILD), raise_on_error=True) is False
    assert falkor.deletes == []
    assert service.remove_document_from_falkor("letter-1", _document(), raise_on_error=True) is True
    assert falkor.deletes == ["CC/PLAN/0001"]


@pytest.mark.parametrize(
    "row,expected",
    [
        ({"parent_document_id": "abc"}, True),
        ({"parent_document_id": None}, False),
        ({"parent_document_id": ""}, False),
        ({}, False),
        (None, False),
    ],
)
def test_is_child_document(row: Any, expected: bool) -> None:
    assert is_child_document(row) is expected


def test_without_letter_identity_strips_only_the_letter_number() -> None:
    metadata = ParsedDocumentMetadata(letter_no="CC/PLAN/0001", subject="Annexure A")
    stripped = without_letter_identity(metadata)
    assert stripped.letter_no is None and stripped.subject == "Annexure A"
    assert metadata.letter_no == "CC/PLAN/0001"  # the caller's object is untouched
    assert without_letter_identity({"letterNo": "X", "letter_no": "X", "subject": "s"}) == {"subject": "s"}
    assert without_letter_identity(None) is None


def test_without_letter_identity_fails_closed_for_a_plain_object() -> None:
    class Plain:
        letter_no = "CC/PLAN/0001"

    assert without_letter_identity(Plain()).letter_no is None
