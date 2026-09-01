"""Real Mongo checks for the Claim legacy relationship backfill.

Opt-in: shares the disposable replica set named by INSURANCE_TRACER_MONGODB_URI.
Every test uses and drops a random database.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from rbac_backend.core.permissions import Permissions
from rbac_backend.routers import legacy_relationship_backfill as backfill_router
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


def _operator(*permissions: str) -> SimpleNamespace:
    granted = set(permissions) or {
        Permissions.DMS_ADMIN,
        # CLAIM_VIEW is the claim adapter's own view permission; a forward /
        # reverse relationship listing goes through `manage=False` and needs
        # it. The previous no-op `authorize_document` hid that requirement.
        Permissions.CLAIM_VIEW,
        Permissions.CLAIM_EDIT,
        Permissions.DOCUMENT_VIEW,
    }
    return SimpleNamespace(
        id="claim-operator", organization_id="org-1", permissions=granted
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


async def _seed(database: Any, **document_overrides: Any) -> None:
    await database.claims.insert_one(
        {
            "_id": "claim-1",
            "claim_ref": "CLM-001",
            "title": "Delay claim",
            "organization_id": "org-1",
            "project_id": "project-1",
            "status": "draft",
            "linked_document_ids": ["doc-1"],
        }
    )
    document = {
        "_id": "doc-1",
        "organization_id": "org-1",
        "project_id": "project-1",
        "filename": "Notice.pdf",
        "lifecycle_state": "active",
        "processing_status": "completed",
        "duplicate_status": "unique",
        "current_version_id": "version-1",
        "file_object_id": "file-1",
    }
    document.update(document_overrides)
    await database.documents.insert_one(document)
    await database.document_versions.insert_one(
        {
            "_id": "version-1",
            "document_id": "doc-1",
            "file_object_id": "file-1",
            "version_number": 1,
            "is_current": True,
        }
    )
    await database.file_objects.insert_one(
        {
            "_id": "file-1",
            "organization_id": "org-1",
            "project_id": "project-1",
            "sha256": "b" * 64,
        }
    )


async def _apply(database, monkeypatch, **overrides):
    async def _step_up(*_args, **_kwargs):
        return None

    monkeypatch.setattr(backfill_router, "require_step_up", _step_up)

    kwargs = {
        "request": _request(),
        "module": "claim",
        "selections": [{"target_id": "claim-1", "document_id": "doc-1"}],
        "org_id": "org-1",
        "project_id": "project-1",
        "dry_run": False,
        "db": database,
        "current_user": _operator(),
        "policy": _OperatorPolicy(),
    }
    kwargs.update(overrides)
    return await backfill_router.apply_legacy_relationship_backfill(**kwargs)


async def _active_links(database) -> list:
    return [row async for row in database.entity_document_links.find({"removed_at": None})]


@pytest.mark.asyncio
async def test_real_mongo_claim_backfill_uses_the_accepted_role(monkeypatch) -> None:
    client, database = await _new_database()
    try:
        await _seed(database)

        result = await _apply(database, monkeypatch)

        assert result["results"][0]["status"] == "backfilled"
        links = await _active_links(database)
        assert len(links) == 1
        assert links[0]["target_type"] == "claim"
        assert links[0]["relationship_role"] == "supporting_document"
        assert links[0]["source"] == "migration"
        assert (
            await database.audit_events.count_documents(
                {"action": "legacy_relationship.backfilled"}
            )
            == 1
        )
        claim = await database.claims.find_one({"_id": "claim-1"})
        assert claim["linked_document_ids"] == ["doc-1"]
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_claim_dry_run_mutates_nothing(monkeypatch) -> None:
    client, database = await _new_database()
    try:
        await _seed(database)

        result = await _apply(database, monkeypatch, dry_run=True)

        assert result["results"][0]["status"] == "eligible"
        assert await database.entity_document_links.count_documents({}) == 0
        assert await database.audit_events.count_documents({}) == 0
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_claim_backfill_is_idempotent(monkeypatch) -> None:
    client, database = await _new_database()
    try:
        await _seed(database)

        first = await _apply(database, monkeypatch)
        second = await _apply(database, monkeypatch)

        assert first["results"][0]["status"] == "backfilled"
        assert second["results"][0]["status"] == "already_canonical"
        assert len(await _active_links(database)) == 1
        assert (
            await database.audit_events.count_documents(
                {"action": "legacy_relationship.backfilled"}
            )
            == 1
        )
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_concurrent_claim_backfill_does_not_duplicate(
    monkeypatch,
) -> None:
    """Owning test for the migration_run_id fencing: the loser must not report
    a write it did not perform."""
    client, database = await _new_database()
    try:
        await _seed(database)

        results = await asyncio.gather(
            _apply(database, monkeypatch),
            _apply(database, monkeypatch),
            return_exceptions=True,
        )

        assert [row for row in results if isinstance(row, Exception)] == []
        assert len(await _active_links(database)) == 1
        assert (
            await database.audit_events.count_documents(
                {"action": "legacy_relationship.backfilled"}
            )
            == 1
        )
        # The loser reports the outcome is still unknown rather than claiming a
        # terminal success for a write it did not perform; a retry after the
        # winner confirms is terminal and idempotent.
        # Exactly one adjudication wins; the loser is never told it wrote
        # something it did not. Whether it sees `in_progress` (winner not yet
        # confirmed) or `already_canonical` (winner confirmed) depends only on
        # interleaving, so pinning one of them would be pinning machine speed.
        # The non-terminal case is proven deterministically against a live
        # lease in test_ipc_legacy_relationship_backfill.py.
        statuses = [row["results"][0]["status"] for row in results]
        assert statuses.count("backfilled") == 1, statuses
        loser = [status for status in statuses if status != "backfilled"]
        assert loser == ["in_progress"] or loser == ["already_canonical"], statuses

        retry = await _apply(database, monkeypatch)
        assert retry["results"][0]["status"] == "already_canonical"
        assert len(await _active_links(database)) == 1
        assert (
            await database.audit_events.count_documents(
                {"action": "legacy_relationship.backfilled"}
            )
            == 1
        )
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_blocked_document_is_never_backfilled(monkeypatch) -> None:
    client, database = await _new_database()
    try:
        await _seed(database, processing_status="human_review_required")

        result = await _apply(database, monkeypatch)

        assert result["results"][0]["status"] == "requires_manual_review"
        assert await database.entity_document_links.count_documents({}) == 0
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_cross_project_document_is_never_backfilled(
    monkeypatch,
) -> None:
    client, database = await _new_database()
    try:
        await _seed(database, project_id="project-2")

        result = await _apply(database, monkeypatch)

        assert result["results"][0]["status"] == "requires_manual_review"
        assert "cross_project" in result["results"][0]["findings"]
        assert await database.entity_document_links.count_documents({}) == 0
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_a_frozen_claim_is_never_mutated(monkeypatch) -> None:
    client, database = await _new_database()
    try:
        await _seed(database)
        await database.claims.update_one(
            {"_id": "claim-1"},
            {"$set": {"evidence_frozen_at": "2026-08-01T00:00:00Z"}},
        )

        result = await _apply(database, monkeypatch)

        assert result["results"][0]["status"] == "frozen_target"
        assert await database.entity_document_links.count_documents({}) == 0
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_a_removed_relationship_is_not_resurrected(
    monkeypatch,
) -> None:
    client, database = await _new_database()
    try:
        await _seed(database)
        await _apply(database, monkeypatch)
        link = (await _active_links(database))[0]
        await database.entity_document_links.update_one(
            {"_id": link["_id"]},
            {
                "$set": {
                    "removed_at": "2026-08-23T00:00:00Z",
                    "removal_reason": "Wrong claim",
                }
            },
        )

        result = await _apply(database, monkeypatch)

        assert result["results"][0]["status"] == "previously_removed"
        assert await database.entity_document_links.count_documents(
            {"removed_at": None}
        ) == 0
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_forward_and_reverse_parity(monkeypatch) -> None:
    from rbac_backend.services.document_relationship_service import (
        DocumentRelationshipService,
    )

    client, database = await _new_database()
    try:
        await _seed(database)
        await _apply(database, monkeypatch)

        service = DocumentRelationshipService(database, policy=_OperatorPolicy())
        actor = _operator()
        forward = await service.list_for_target(actor, "claim", "claim-1")
        reverse = await service.list_for_document(actor, "doc-1")

        assert len(forward) == 1
        assert len(reverse) == 1
        assert forward[0].id == reverse[0].id
        assert forward[0].target_id == "claim-1"
        assert forward[0].document_id == "doc-1"
        assert forward[0].relationship_role == "supporting_document"
        assert forward[0].source == "migration"
    finally:
        await client.drop_database(database.name)
        client.close()


# -- real target authorization ----------------------------------------------
#
# These drive the production seam: DocumentRelationshipService._target resolves
# the claim adapter and calls PolicyService.authorize_document with the
# adapter's own CLAIM_EDIT manage permission. No no-op policy double.


async def _claims_taken(database) -> int:
    return await database[BACKFILL_CLAIMS_COLLECTION].count_documents({})


@pytest.mark.asyncio
async def test_real_mongo_claim_backfill_requires_the_claim_manage_permission(
    monkeypatch,
) -> None:
    """Reading a claim is not authority to attach evidence to it."""
    client, database = await _new_database()
    try:
        await _seed(database)
        reader = _operator(
            Permissions.DMS_ADMIN,
            Permissions.CLAIM_VIEW,
            Permissions.DOCUMENT_VIEW,
        )

        result = await _apply(database, monkeypatch, current_user=reader)

        assert result["results"][0]["status"] == "rejected"
        assert await _active_links(database) == []
        assert await database.audit_events.count_documents({}) == 0
        # No durable claim is left holding the candidate hostage.
        assert await _claims_taken(database) == 0
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_dms_admin_alone_cannot_backfill_a_claim(monkeypatch) -> None:
    """The operator gate on the route is DMS_ADMIN, and it is deliberately NOT
    the whole story: the target still demands its own module permission."""
    client, database = await _new_database()
    try:
        await _seed(database)
        admin = _operator(Permissions.DMS_ADMIN, Permissions.DOCUMENT_VIEW)

        result = await _apply(database, monkeypatch, current_user=admin)

        assert result["results"][0]["status"] == "rejected"
        assert await _active_links(database) == []
        assert await database.audit_events.count_documents({}) == 0
        assert await _claims_taken(database) == 0
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_a_frozen_claim_is_not_backfilled(monkeypatch) -> None:
    """The claim adapter reports frozen from `evidence_frozen_at`; a frozen
    evidence register must not gain rows through the migration path."""
    client, database = await _new_database()
    try:
        await _seed(database)
        await database.claims.update_one(
            {"_id": "claim-1"},
            {"$set": {"evidence_frozen_at": "2026-08-01T00:00:00Z"}},
        )

        result = await _apply(database, monkeypatch)

        assert result["results"][0]["status"] == "frozen_target"
        assert await _active_links(database) == []
        assert await database.audit_events.count_documents({}) == 0
    finally:
        await client.drop_database(database.name)
        client.close()
