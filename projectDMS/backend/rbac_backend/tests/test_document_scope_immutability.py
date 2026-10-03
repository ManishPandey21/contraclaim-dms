"""A document's organisation and project are fixed at creation.

Canonical correspondence vectors are replaced per (document, org, project); the
writer's delete is scoped to the document's CURRENT authority. That is safe
only while a document can never move - a moved document would leave its old
points searchable in the scope it left.

A read-only sweep (2026-09-28) of all 58 write sites on the ``documents``
collection found no path that changes ``organization_id``/``project_id``: the
only live update is ``PUT /api/documents/{id}``, whose body model
``DocumentUpdate`` has neither field and ignores unknown keys; there is no
move/transfer endpoint; project and organisation deletion never rewrite
documents. Nothing pinned that. These tests do.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from bson import ObjectId

from rbac_backend.models.document import DocumentUpdate
from rbac_backend.tests.correspondence_vector_harness import (
    QDRANT_BACKENDS,
    Database,
    QdrantHarness,
    correspondence_document,
    database_service,
    run,
    scoped_search,
    upload_and_process,
    user_for,
)

ORG_A, PROJECT_A = str(ObjectId()), str(ObjectId())
ORG_B, PROJECT_B = str(ObjectId()), str(ObjectId())
BODY = (
    "Notice of delay to the viaduct pier foundations caused by late access to "
    "the railway corridor. The contractor requests an extension of time. "
) * 4
QUERY = "viaduct pier foundations delay railway corridor"
SCOPE_KEYS = ("organization_id", "project_id", "organizationId", "projectId")


def test_the_update_model_cannot_carry_a_scope_change() -> None:
    fields = set(DocumentUpdate.model_fields)
    aliases = {f.alias for f in DocumentUpdate.model_fields.values() if f.alias}
    assert not (fields | aliases) & set(SCOPE_KEYS)
    assert DocumentUpdate.model_config.get("extra") in (None, "ignore")
    hostile = DocumentUpdate.model_validate(
        {"subject": "s", **{key: ORG_B for key in SCOPE_KEYS}}
    )
    assert set(hostile.model_dump(exclude_unset=True)) == {"subject"}


@pytest.fixture(params=QDRANT_BACKENDS)
def harness(request, monkeypatch):
    built = QdrantHarness(request.param, monkeypatch)
    yield built
    built.drop()


@pytest.mark.parametrize("stored_as_objectid", [False, True], ids=["str-ids", "objectid-ids"])
def test_put_with_scope_keys_keeps_the_scope_and_reindex_leaves_no_stale_point(
    harness: QdrantHarness,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    stored_as_objectid: bool,
) -> None:
    from rbac_backend.routers.documents import DocumentController, controller_update_document
    from rbac_backend.services.document_service import DocumentService

    db = Database()
    org: Any = ObjectId(ORG_A) if stored_as_objectid else ORG_A
    project: Any = ObjectId(PROJECT_A) if stored_as_objectid else PROJECT_A
    row = correspondence_document(
        organization_id=org, project_id=project, letter_no="IMM/1", subject="Delay"
    )
    run(upload_and_process(monkeypatch, tmp_path, db, harness, row, body=BODY))
    document_id = str(row["_id"])
    before = sorted((str(p.id), p.payload["org_id"], p.payload["project_id"]) for p in harness.scroll())
    assert before and {(o, p) for _i, o, p in before} == {(ORG_A, PROJECT_A)}

    class _Policy:
        async def authorize_document(self, *_a: Any, **_k: Any) -> None:
            return None

    class _Audit:
        async def emit(self, **_k: Any) -> None:
            return None

    controller = DocumentController(
        document_service=DocumentService(db),
        file_service=SimpleNamespace(),
        export_service=SimpleNamespace(),
        auth_service=SimpleNamespace(),
        bulk_upload_service=SimpleNamespace(),
    )
    controller.policy_service = _Policy()  # type: ignore[assignment]
    controller.audit_service = _Audit()  # type: ignore[assignment]
    hostile = DocumentUpdate.model_validate(
        {
            "subject": "Edited subject",
            "organization_id": ORG_B,
            "project_id": PROJECT_B,
            "organizationId": ORG_B,
            "projectId": PROJECT_B,
        }
    )
    run(controller_update_document(controller, document_id, hostile, user_for(ORG_A, PROJECT_A)))

    stored = run(db.documents.find_one({"_id": row["_id"]}))
    assert stored["subject"] == "Edited subject"  # positive control: the PUT wrote
    assert str(stored["organization_id"]) == ORG_A
    assert str(stored["project_id"]) == PROJECT_A
    assert "organizationId" not in stored and "projectId" not in stored

    # Reindex after the edit: same points, same scope - nothing left behind.
    run(database_service(db, harness).create_embeddings_for_document(document_id))
    after = sorted((str(p.id), p.payload["org_id"], p.payload["project_id"]) for p in harness.scroll())
    assert after == before
    hits = run(scoped_search(db, harness, QUERY, org_id=ORG_A, project_id=PROJECT_A))
    assert document_id in [r.document_id for r in hits.results]
    assert run(scoped_search(db, harness, QUERY, org_id=ORG_B, project_id=PROJECT_B)).results == []
