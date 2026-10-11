"""Creating an Insurance policy from an existing canonical Document (HIGH 2).

Spec §12.3 requires "link an existing policy without upload". Creation must
therefore accept EITHER new uploaded evidence OR a reference to a Document that
already exists, and the second path must mint no new bytes and no new Document.
"""

from __future__ import annotations

import httpx
import pytest

from rbac_backend.core.security import CurrentUser, get_current_user
from rbac_backend.tests.test_insurance_document_relationships import (
    _insurance_app,
    _insurance_db,
)


def _scoped_user() -> CurrentUser:
    """A tenant user with an active organisation selection.

    The shared harness user is a superadmin with no active selection, which is a
    legitimate state but cannot scope a relationship target.
    """
    return CurrentUser(
        id="user-1",
        username="architect",
        email="architect@example.com",
        roles=["superadmin"],
        organization_id="org-1",
        organizations=["org-1"],
        projects=["project-1"],
        disabled=False,
    )


def _app(db):
    app = _insurance_app(db)
    app.dependency_overrides[get_current_user] = _scoped_user
    return app


def _payload(**overrides) -> dict:
    payload = {
        "project_id": "project-1",
        "insurance_type": "Marine Cargo Insurance",
        "policy_number": "POL-NEW-1",
        "date_of_issue": "2026-08-01T00:00:00Z",
        "date_of_expiry": "2027-08-01T00:00:00Z",
        "existing_document_links": [
            {"document_id": "doc-1", "relationship_role": "policy"}
        ],
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_insurance_can_be_created_from_an_existing_document_without_upload() -> None:
    db = _insurance_db()
    document_ids_before = sorted(str(row["_id"]) for row in db.documents.documents)
    transport = httpx.ASGITransport(app=_app(db))

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post("/api/insurance", json=_payload())
        assert created.status_code == 201, created.text
        insurance_id = created.json()["_id"]

        forward = await client.get(
            f"/api/entities/insurance/{insurance_id}/document-links"
        )
        reverse = await client.get("/api/documents/doc-1/entity-links")

    # No re-upload: no new Document, no new version, no new bytes.
    assert sorted(str(row["_id"]) for row in db.documents.documents) == document_ids_before
    assert not hasattr(db, "file_objects") or db.file_objects.documents == []

    # The relationship is canonical, on the new entity, with the right role.
    links = forward.json()["links"]
    assert len(links) == 1
    assert links[0]["document_id"] == "doc-1"
    assert links[0]["target_type"] == "insurance"
    assert links[0]["target_id"] == insurance_id
    assert links[0]["relationship_role"] == "policy"

    # Forward/reverse parity.
    assert [row["target_id"] for row in reverse.json()["links"]] == [insurance_id]


async def _create(db, **overrides):
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/api/insurance", json=_payload(**overrides))


def _with_document(db, **fields):
    """Replace doc-1's authority-relevant fields."""
    db.documents.documents[0].update(fields)
    return db


def _policy_rows(db) -> list:
    return [
        row
        for row in db.insurance_policies.documents
        if row.get("policy_number") == "POL-NEW-1"
    ]


@pytest.mark.asyncio
async def test_upload_new_path_still_requires_no_existing_links() -> None:
    """Path A is untouched: creating without existing links still succeeds."""
    db = _insurance_db()

    response = await _create(db, existing_document_links=[])

    assert response.status_code == 201, response.text
    assert len(_policy_rows(db)) == 1


@pytest.mark.asyncio
async def test_an_operationally_failed_document_remains_usable() -> None:
    """Model B: a worker crash is operational, not an adverse judgement."""
    db = _with_document(_insurance_db(), processing_status="failed")

    response = await _create(db)

    assert response.status_code == 201, response.text
    assert len(_policy_rows(db)) == 1


@pytest.mark.parametrize(
    "fields",
    [
        pytest.param({"processing_status": "human_review_required"}, id="human_review"),
        pytest.param({"duplicate_status": "duplicate"}, id="duplicate"),
        pytest.param({"lifecycle_state": "deleted"}, id="deleted"),
        pytest.param({"lifecycle_state": "duplicate"}, id="quarantined"),
        pytest.param({"organization_id": "org-2"}, id="cross_organisation"),
        pytest.param({"project_id": "project-2"}, id="cross_project"),
        pytest.param({"project_id": None}, id="project_null"),
    ],
)
@pytest.mark.asyncio
async def test_an_unusable_existing_document_is_refused_and_the_policy_is_rolled_back(
    fields: dict,
) -> None:
    db = _with_document(_insurance_db(), **fields)

    response = await _create(db)

    assert response.status_code >= 400, response.text
    # Compensation: no half-created authoritative policy survives.
    assert _policy_rows(db) == []
    active_links = [
        row for row in db.entity_document_links.documents if row.get("removed_at") is None
    ]
    assert active_links == []


@pytest.mark.asyncio
async def test_an_unknown_document_is_refused_and_the_policy_is_rolled_back() -> None:
    db = _insurance_db()

    response = await _create(
        db,
        existing_document_links=[
            {"document_id": "does-not-exist", "relationship_role": "policy"}
        ],
    )

    assert response.status_code >= 400, response.text
    assert _policy_rows(db) == []


@pytest.mark.asyncio
async def test_a_role_outside_the_insurance_vocabulary_is_refused() -> None:
    db = _insurance_db()

    response = await _create(
        db,
        existing_document_links=[
            {"document_id": "doc-1", "relationship_role": "renewal"}
        ],
    )

    assert response.status_code == 422, response.text
    assert _policy_rows(db) == []


@pytest.mark.asyncio
async def test_the_rollback_is_audited() -> None:
    db = _with_document(_insurance_db(), lifecycle_state="deleted")

    await _create(db)

    rollbacks = [
        row
        for row in db.audit_events.documents
        if row.get("action") == "insurance.create_rolled_back"
    ]
    assert len(rollbacks) == 1
    assert rollbacks[0]["after"]["reason"] == "existing_document_link_failed"


@pytest.mark.asyncio
async def test_a_retried_create_does_not_duplicate_authoritative_state() -> None:
    """The duplicate-policy guard is what stops a retry, before any second link."""
    db = _insurance_db()

    first = await _create(db)
    second = await _create(db)

    assert first.status_code == 201, first.text
    assert second.status_code == 409, second.text
    assert len(_policy_rows(db)) == 1
    active_links = [
        row for row in db.entity_document_links.documents if row.get("removed_at") is None
    ]
    assert len(active_links) == 1


@pytest.mark.asyncio
async def test_an_infrastructure_failure_also_rolls_the_policy_back(monkeypatch) -> None:
    """A driver/transaction fault is not a domain error, but it must not leave
    a committed policy without the evidence the caller required."""
    from rbac_backend.services.document_relationship_service import (
        DocumentRelationshipService,
    )

    async def _boom(*_args, **_kwargs):
        raise RuntimeError("transaction aborted")

    monkeypatch.setattr(DocumentRelationshipService, "link_batch", _boom)
    db = _insurance_db()

    with pytest.raises(RuntimeError):
        await _create(db)

    assert _policy_rows(db) == []


@pytest.mark.asyncio
async def test_legacy_linked_document_ids_cannot_regain_authority() -> None:
    db = _insurance_db()

    response = await _create(db, linked_document_ids=["doc-1"])

    assert response.status_code == 409, response.text
    assert _policy_rows(db) == []
