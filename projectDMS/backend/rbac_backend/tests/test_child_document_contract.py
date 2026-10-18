"""Phase 8 PR A: the child-document contract that needs no database.

The behaviour (identity, RBAC, lifecycle, purge) is exercised against real
Mongo in ``integration/test_child_documents_mongo.py``. These checks run in
the default suite.
"""

from __future__ import annotations

from datetime import datetime

from rbac_backend.models.document import Document, Enclosure


def test_relationship_types_are_exactly_the_agreed_three() -> None:
    from rbac_backend.services.child_document_service import CHILD_RELATIONSHIP_TYPES

    assert CHILD_RELATIONSHIP_TYPES == frozenset(
        {"enclosure", "annexure", "attachment"}
    )


def test_enclosure_model_carries_the_child_link() -> None:
    entry = {
        "id": "e1",
        "filename": "a.pdf",
        "uploadedBy": "u",
        "uploadedAt": datetime(2026, 9, 1),
        "child_document_id": "c1",
    }
    assert Enclosure(**entry).model_dump(by_alias=True)["child_document_id"] == "c1"
    assert Enclosure(**{**entry, "child_document_id": None}).child_document_id is None


def test_remove_enclosure_never_rewrites_the_array_through_the_model() -> None:
    """The model omits storage fields (``storage_key``, ``file_object_id``,
    ``storage_locations``) on purpose: they are not for the client. So the
    array must be edited in place with ``$pull``. Rewriting it from model
    dumps strips those fields from every surviving entry."""
    import inspect

    from rbac_backend.services.document_service import DocumentService

    source = inspect.getsource(DocumentService.remove_enclosure)
    assert "$pull" in source
    assert "model_dump" not in source


def test_the_generic_update_schema_cannot_carry_enclosures() -> None:
    """Enclosures are owned by the dedicated enclosure operations. The generic
    metadata update schema ignores the field, as it ignores every other
    unknown key."""
    from rbac_backend.models.document import DocumentUpdate

    update = DocumentUpdate(**{"subject": "s", "enclosures": [{"id": "x"}]})
    assert "enclosures" not in DocumentUpdate.model_fields
    assert "enclosures" not in update.model_dump(exclude_unset=True)


def test_document_model_declares_the_child_fields() -> None:
    """``Document`` has no ``extra="allow"``: an undeclared field is dropped by
    every ``Document(**row)`` consumer, which would make a child read as a
    top-level document."""
    for field in (
        "parent_document_id",
        "relationship_type",
        "source_page_range",
        "parent_provenance",
        "original_filename",
        "lifecycle_cascade_parent_id",
    ):
        assert field in Document.model_fields, field


def test_audit_metadata_never_carries_storage_details() -> None:
    from rbac_backend.services.child_document_service import child_audit_metadata

    child = {
        "parent_document_id": "p1",
        "relationship_type": "annexure",
        "file_object_id": "fo-1",
        "source_page_range": {"start": 1, "end": 2},
        "parent_provenance": {"enclosure_entry_id": "e1"},
        "storage_key": "/org/proj/enclosures/x.pdf",
        "filepath_local": "/data/x.pdf",
        "storage_locations": [{"path": "/data/x.pdf"}],
        "original_filename": "Secret annexure.pdf",
        "filename": "Secret annexure.pdf",
    }
    meta = child_audit_metadata(child)
    assert meta == {
        "parent_document_id": "p1",
        "relationship_type": "annexure",
        "file_object_id": "fo-1",
        "enclosure_entry_id": "e1",
        "source_page_range": {"start": 1, "end": 2},
    }
