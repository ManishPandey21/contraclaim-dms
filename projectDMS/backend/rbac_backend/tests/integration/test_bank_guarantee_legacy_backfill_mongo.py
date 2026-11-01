"""Real Mongo checks for Bank Guarantee legacy evidence — INVENTORY ONLY.

Opt-in: shares the disposable replica set named by INSURANCE_TRACER_MONGODB_URI.

BG is registered INVENTORY_ONLY. These tests prove, in final physical state,
that realistic parent evidence is discoverable but never writable: no
relationship, no migration claim, no success audit. The classifier's exact-event
resolver is exercised only against explicitly NONSTANDARD/HISTORICAL rows.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from rbac_backend.core.permissions import Permissions
from rbac_backend.routers import legacy_relationship_backfill as backfill_router
from rbac_backend.services.bank_guarantee_document_link_migration import (
    classify_legacy_bank_guarantee_links,
)
from rbac_backend.services.legacy_relationship_backfill import (
    BACKFILL_CLAIMS_COLLECTION,
)
from rbac_backend.tests.integration.test_insurance_document_relationships_mongo import (
    _new_database,
)


class _OperatorPolicy:
    async def authorize_document(
        self, actor: Any, permission: str, document: Any, **kwargs: Any
    ) -> None:
        # `authorize_document` is the seam that carries each adapter's own
        # manage/view/freeze permission. Stubbing it to a no-op would leave
        # every authority claim in this suite unproven.
        await self.authorize(actor, permission)

    async def authorize(self, actor: Any, permission: str, **kwargs: Any) -> None:
        if permission not in getattr(actor, "permissions", set()):
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail="Denied")


def _operator() -> SimpleNamespace:
    return SimpleNamespace(
        id="bg-operator",
        organization_id="org-1",
        permissions={
            Permissions.DMS_ADMIN,
            Permissions.BG_VIEW,
            Permissions.BG_EDIT,
            Permissions.BG_EXTEND,
            Permissions.BG_RELEASE,
            Permissions.INSURANCE_EDIT,
            Permissions.DOCUMENT_VIEW,
        },
    )


def _request():
    from starlette.requests import Request

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/legacy-relationship-backfill/apply",
            "headers": [],
            "query_string": b"",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "scheme": "http",
        }
    )


async def _seed_document(database: Any, **overrides: Any) -> None:
    document = {
        "_id": "doc-1",
        "organization_id": "org-1",
        "project_id": "project-1",
        "filename": "Evidence.pdf",
        "lifecycle_state": "active",
        "processing_status": "completed",
        "duplicate_status": "unique",
        "current_version_id": "version-1",
        "file_object_id": "file-1",
    }
    document.update(overrides)
    await database.documents.insert_one(document)
    await database.document_versions.insert_one(
        {"_id": "version-1", "document_id": "doc-1", "file_object_id": "file-1",
         "version_number": 1, "is_current": True}
    )
    await database.file_objects.insert_one(
        {"_id": "file-1", "organization_id": "org-1", "project_id": "project-1",
         "sha256": "d" * 64}
    )


async def _seed_realistic(database: Any, *, history_row=None, **doc_overrides) -> None:
    """Parent-level evidence; history without a document field (model-accurate)."""
    await database.bank_guarantees.insert_one(
        {"_id": "bg-1", "bg_number": "BG-001", "organization_id": "org-1",
         "project_id": "project-1", "bg_status": "active",
         "linked_document_ids": ["doc-1"]}
    )
    await database.bank_guarantee_events.insert_one(
        {"_id": "E2", "bank_guarantee_id": "bg-1", "organization_id": "org-1",
         "project_id": "project-1", "event_type": "extension", "sequence": 2,
         "revision_number": 1}
    )
    await database.bg_extension_history.insert_one(
        history_row
        if history_row is not None
        else {"_id": "hist-1", "bg_id": "bg-1", "revision_number": 1,
              "extension_date": "2026-05-01T00:00:00Z",
              "new_expiry_date": "2027-05-01T00:00:00Z"}
    )
    await _seed_document(database, **doc_overrides)


async def _inventory(database, monkeypatch, **overrides):
    async def _step_up(*_args, **_kwargs):
        return None

    monkeypatch.setattr(backfill_router, "require_step_up", _step_up)
    kwargs = {
        "request": _request(),
        "module": "bank_guarantee",
        "org_id": "org-1",
        "project_id": "project-1",
        "db": database,
        "current_user": _operator(),
        "policy": _OperatorPolicy(),
    }
    kwargs.update(overrides)
    return await backfill_router.inventory_legacy_relationship_backfill(**kwargs)


async def _apply(database, monkeypatch, **overrides):
    async def _step_up(*_args, **_kwargs):
        return None

    monkeypatch.setattr(backfill_router, "require_step_up", _step_up)
    kwargs = {
        "request": _request(),
        "module": "bank_guarantee",
        "selections": [
            {"target_id": "E2", "document_id": "doc-1", "relationship_role": "extension"}
        ],
        "org_id": "org-1",
        "project_id": "project-1",
        "dry_run": False,
        "db": database,
        "current_user": _operator(),
        "policy": _OperatorPolicy(),
    }
    kwargs.update(overrides)
    return await backfill_router.apply_legacy_relationship_backfill(**kwargs)


@pytest.mark.asyncio
async def test_real_mongo_realistic_parent_evidence_is_inventoried_not_written(
    monkeypatch,
) -> None:
    client, database = await _new_database()
    try:
        await _seed_realistic(database)

        report = await _inventory(database, monkeypatch)

        parent = [
            row for row in report["candidates"]
            if row.get("source_kind") == "legacy_bg_parent_array"
        ]
        assert len(parent) == 1
        assert parent[0]["target_id"] is None
        assert "ambiguous_event" in parent[0]["findings"]
        # Read-only: nothing written by inventory.
        assert await database.entity_document_links.count_documents({}) == 0
        assert await database[BACKFILL_CLAIMS_COLLECTION].count_documents({}) == 0
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_apply_is_refused_and_writes_nothing(monkeypatch) -> None:
    from fastapi import HTTPException

    client, database = await _new_database()
    try:
        await _seed_realistic(database)

        with pytest.raises(HTTPException) as excinfo:
            await _apply(database, monkeypatch)

        assert excinfo.value.status_code == 409
        assert await database.entity_document_links.count_documents({}) == 0
        assert await database.audit_events.count_documents({}) == 0
        assert await database[BACKFILL_CLAIMS_COLLECTION].count_documents({}) == 0
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_inventory_resolves_document_authority(monkeypatch) -> None:
    client, database = await _new_database()
    try:
        await _seed_realistic(database, processing_status="human_review_required")

        report = await _inventory(database, monkeypatch)

        parent = [
            row for row in report["candidates"]
            if row.get("source_kind") == "legacy_bg_parent_array"
        ]
        assert parent and "blocked_document" in parent[0]["findings"]
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_malformed_revision_is_safe(monkeypatch) -> None:
    """One malformed nonstandard row must not resolve an event or raise."""
    client, database = await _new_database()
    try:
        await _seed_realistic(
            database,
            history_row={"_id": "hist-1", "bg_id": "bg-1", "revision_number": "R-1",
                         "extension_date": "2026-05-01T00:00:00Z",
                         "linked_document_ids": ["doc-1"]},
        )

        report = await classify_legacy_bank_guarantee_links(database)

        ext = [
            row for row in report["candidates"]
            if row.get("source_kind") == "legacy_bg_extension_history"
        ]
        assert ext and ext[0]["target_id"] is None
        assert "ambiguous_event" in ext[0]["findings"]
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_nonstandard_exact_revision_resolves_deterministically(
    monkeypatch,
) -> None:
    """NONSTANDARD/HISTORICAL row with a document id + unique revision resolves
    the exact event in the classifier — reconciliation capability only."""
    client, database = await _new_database()
    try:
        await _seed_realistic(
            database,
            history_row={"_id": "hist-1", "bg_id": "bg-1", "revision_number": 1,
                         "extension_date": "2026-05-01T00:00:00Z",
                         "new_expiry_date": "2027-05-01T00:00:00Z",
                         "linked_document_ids": ["doc-1"]},
        )

        report = await classify_legacy_bank_guarantee_links(database)

        ext = [
            row for row in report["candidates"]
            if row.get("source_kind") == "legacy_bg_extension_history"
        ]
        assert ext and ext[0]["target_id"] == "E2"
        assert ext[0]["event_resolution_source"] == "extension_revision_number"
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_insurance_remains_writable(monkeypatch) -> None:
    """Regression guard: making BG inventory-only must not downgrade a genuine
    write-backfill module."""
    import tempfile
    from pathlib import Path

    client, database = await _new_database()
    try:
        await database.insurance_policies.insert_one(
            {"_id": "insurance-1", "policy_number": "POL-1",
             "organization_id": "org-1", "project_id": "project-1",
             "linked_document_ids": ["doc-1"]}
        )
        await _seed_document(database)

        async def _step_up(*_args, **_kwargs):
            return None

        monkeypatch.setattr(backfill_router, "require_step_up", _step_up)
        monkeypatch.setattr(
            backfill_router, "_legacy_insurance_root",
            lambda: Path(tempfile.mkdtemp()),
        )

        result = await backfill_router.apply_legacy_relationship_backfill(
            request=_request(),
            module="insurance",
            selections=[
                {"target_id": "insurance-1", "document_id": "doc-1",
                 "relationship_role": "supporting_document"}
            ],
            org_id="org-1",
            project_id="project-1",
            dry_run=False,
            db=database,
            current_user=_operator(),
            policy=_OperatorPolicy(),
        )

        assert result["results"][0]["status"] == "backfilled"
        assert await database.entity_document_links.count_documents(
            {"removed_at": None}
        ) == 1
    finally:
        await client.drop_database(database.name)
        client.close()


# -- inventory-only: capability refusal precedes authorization ---------------


@pytest.mark.asyncio
async def test_real_mongo_bg_apply_is_refused_before_any_authorization(
    monkeypatch,
) -> None:
    """BG is INVENTORY_ONLY, so target authorization is never reached.

    Pinned explicitly rather than pretending BG has a reachable target-auth
    path: an operator holding NO bank guarantee permission at all still gets the
    capability refusal (409), not a 403. If BG were ever promoted to writable,
    this test would start seeing an authorization outcome instead and fail --
    which is exactly the signal wanted.
    """
    from fastapi import HTTPException

    client, database = await _new_database()
    try:
        await _seed_realistic(database)
        stranger = SimpleNamespace(
            id="no-bg-rights",
            organization_id="org-1",
            permissions={Permissions.DMS_ADMIN, Permissions.DOCUMENT_VIEW},
        )

        with pytest.raises(HTTPException) as excinfo:
            await _apply(database, monkeypatch, current_user=stranger)

        assert excinfo.value.status_code == 409
        assert "inventory-only" in excinfo.value.detail
        assert await database.entity_document_links.count_documents({}) == 0
        assert await database.audit_events.count_documents({}) == 0
        assert await database[BACKFILL_CLAIMS_COLLECTION].count_documents({}) == 0
    finally:
        await client.drop_database(database.name)
        client.close()
