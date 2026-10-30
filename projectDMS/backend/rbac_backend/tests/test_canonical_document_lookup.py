"""G35/G36: identifier typing, and the orphaned pipeline.

**G35.** `documents._id` is ObjectId-keyed - `document_service` pops any supplied
`_id` before `insert_one`, so Mongo always generates one - while callers hold
the string form from a URL or payload. Five sites queried `{"_id": <raw
string>}`, which matches nothing. Every one failed CLOSED: empty drafting
context, `ValueError` from the reindex path, "outside case scope" for
arbitration document selection, `missing_or_out_of_scope` manifests. Not an
authority escalation, but a real availability defect - and on the drafting path
it made the G27 guard vacuous, because the document was never loaded for the
guard to inspect.

`document_id_candidates` returns both forms rather than guessing one. The
earlier single-shot `ObjectId(str(id))` relied on real and non-real ids being
syntactically distinguishable: a plain string that happened to be valid 24-hex
would convert to an ObjectId matching a DIFFERENT document, silently.

**G36.** `rbac_backend/documents.py` is a 1863-line parallel document pipeline
that never writes `processing_status`. If it were reachable, every document it
created would read as legacy-consumable forever and the whole publication
barrier would be inert for them. It is not merely unimported - it is
*unimportable*, which is a stronger guarantee and is what this pins.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import pytest
from bson import ObjectId

from rbac_backend.services.publication_policy import (
    document_id_candidates,
    resolve_canonical_document,
)


# --- G35: identifier candidates ------------------------------------------------


def test_a_string_id_yields_both_forms() -> None:
    raw = "507f1f77bcf86cd799439011"
    candidates = document_id_candidates(raw)

    assert raw in candidates
    assert ObjectId(raw) in candidates


def test_an_objectid_is_accepted_directly() -> None:
    oid = ObjectId()
    candidates = document_id_candidates(oid)

    assert oid in candidates


def test_a_malformed_id_yields_only_itself() -> None:
    """Not every identifier is an ObjectId; a business key must still work."""
    candidates = document_id_candidates("not-an-objectid")

    assert candidates == ["not-an-objectid"]
    assert all(not isinstance(c, ObjectId) for c in candidates)


def test_an_empty_id_is_not_expanded() -> None:
    assert document_id_candidates("") == [""]


# --- G35: the canonical lookup -------------------------------------------------


class _Documents:
    def __init__(self, docs: List[Dict[str, Any]]) -> None:
        self._docs = docs
        self.queries: List[Any] = []

    async def find_one(self, query: Dict[str, Any], *a: Any, **k: Any):
        key = query.get("_id")
        self.queries.append(key)
        for doc in self._docs:
            if doc.get("_id") == key:
                return doc
        return None


class _DB:
    def __init__(self, docs: List[Dict[str, Any]]) -> None:
        self.documents = _Documents(docs)


def _resolve(docs: List[Dict[str, Any]], identifier: Any):
    db = _DB(docs)
    return asyncio.run(resolve_canonical_document(db, identifier)), db


def test_a_string_resolves_an_objectid_keyed_document() -> None:
    """The defect: this returned None for every real document."""
    oid = ObjectId()
    found, _ = _resolve([{"_id": oid, "subject": "S"}], str(oid))

    assert found is not None
    assert found["subject"] == "S"


def test_an_objectid_resolves_directly() -> None:
    oid = ObjectId()
    found, _ = _resolve([{"_id": oid}], oid)

    assert found is not None


def test_a_string_keyed_document_still_resolves() -> None:
    """Legitimate non-ObjectId keys must keep working."""
    found, _ = _resolve([{"_id": "business-key-1"}], "business-key-1")

    assert found is not None


def test_a_missing_document_returns_none() -> None:
    found, _ = _resolve([{"_id": ObjectId()}], str(ObjectId()))

    assert found is None


def test_a_missing_identifier_returns_none_without_querying() -> None:
    found, db = _resolve([{"_id": ObjectId()}], None)

    assert found is None
    assert db.documents.queries == []


def test_a_lookup_exception_propagates_for_the_caller_to_fail_closed() -> None:
    """The resolver must not swallow it - authority decides how to fail."""

    class _Broken:
        async def find_one(self, *a: Any, **k: Any):
            raise RuntimeError("mongo down")

    class _BrokenDB:
        documents = _Broken()

    with pytest.raises(RuntimeError):
        asyncio.run(resolve_canonical_document(_BrokenDB(), "abc"))


# --- G36: the orphaned pipeline ------------------------------------------------


def test_the_mounted_documents_router_is_the_rbac_one() -> None:
    from rbac_backend.routers import documents as mounted

    assert "routers" in mounted.__file__.replace("\\", "/")


def test_the_orphaned_document_pipeline_cannot_be_imported() -> None:
    """G36, pinned structurally rather than by convention.

    `rbac_backend/documents.py` never writes `processing_status`, so if it were
    reachable every document it created would read as legacy-consumable forever
    and the publication barrier would be inert for them.

    It is unimportable: its module-level `from ..models.document import ...`
    reaches beyond the top-level package. Reconnecting it therefore fails
    loudly at import rather than silently disabling the barrier. If this test
    ever fails, that guarantee is gone and the file needs the full authority
    lifecycle wired in before it can be used.
    """
    import importlib

    with pytest.raises(ImportError):
        importlib.import_module("rbac_backend.documents")


def test_the_orphaned_pipeline_still_writes_no_processing_status() -> None:
    """Belt and braces: if it ever becomes importable, this says why that matters."""
    from pathlib import Path

    orphan = Path(__file__).resolve().parents[1] / "documents.py"
    if not orphan.is_file():
        pytest.skip("orphaned pipeline already removed")

    assert "processing_status" not in orphan.read_text(encoding="utf-8"), (
        "the orphaned pipeline now writes processing_status - if it is being "
        "revived, wire it through the publication policy deliberately"
    )
