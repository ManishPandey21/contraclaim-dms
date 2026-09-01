"""Cross-role concurrency on one legacy migration candidate.

A legacy flat array proves only "this Document was associated with this target".
It does not prove that every role two operators might choose is simultaneously
correct. The canonical relationship identity includes `relationship_role`, so
two operators adjudicating the SAME legacy candidate differently can each write
a distinct row and each believe they migrated it.

The migration decision is therefore its own identity — module + legacy field +
target + document — independent of relationship-row uniqueness. Exactly one
adjudication may become the migration result.

Ordinary (non-migration) multi-role relationships are unaffected: this is about
migration decision ownership, not canonical relationship semantics.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from rbac_backend.core.permissions import Permissions
from rbac_backend.routers import legacy_relationship_backfill as backfill_router
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


def _operator(user_id: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id,
        organization_id="org-1",
        permissions={
            Permissions.DMS_ADMIN,
            Permissions.IPC_VIEW,
            Permissions.IPC_EDIT,
            Permissions.CLAIM_VIEW,
            Permissions.CLAIM_EDIT,
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


async def _seed_document(database: Any) -> None:
    await database.documents.insert_one(
        {
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
    )
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
            "sha256": "e" * 64,
        }
    )


async def _seed_ipc(database: Any) -> None:
    await database.ipc_bills.insert_one(
        {
            "_id": "ipc-1",
            "ipc_number": "IPC-07",
            "organization_id": "org-1",
            "project_id": "project-1",
            "linked_document_ids": ["doc-1"],
        }
    )
    await _seed_document(database)


async def _seed_claim(database: Any) -> None:
    await database.claims.insert_one(
        {
            "_id": "claim-1",
            "claim_ref": "CLM-001",
            "organization_id": "org-1",
            "project_id": "project-1",
            "linked_document_ids": ["doc-1"],
        }
    )
    await _seed_document(database)


async def _apply(database, monkeypatch, *, module, target_id, role, user="op-a", **over):
    async def _step_up(*_args, **_kwargs):
        return None

    monkeypatch.setattr(backfill_router, "require_step_up", _step_up)

    kwargs = {
        "request": _request(),
        "module": module,
        "selections": [
            {
                "target_id": target_id,
                "document_id": "doc-1",
                "relationship_role": role,
            }
        ],
        "org_id": "org-1",
        "project_id": "project-1",
        "dry_run": False,
        "db": database,
        "current_user": _operator(user),
        "policy": _OperatorPolicy(),
    }
    kwargs.update(over)
    return await backfill_router.apply_legacy_relationship_backfill(**kwargs)


async def _active_links(database) -> list:
    return [row async for row in database.entity_document_links.find({"removed_at": None})]


async def _creation_audits(database) -> int:
    return await database.audit_events.count_documents(
        {"action": "legacy_relationship.backfilled"}
    )


@pytest.mark.asyncio
async def test_real_mongo_concurrent_different_roles_yield_one_migration(
    monkeypatch,
) -> None:
    """THE RACE: one legacy candidate, two operators, two different roles."""
    client, database = await _new_database()
    try:
        await _seed_ipc(database)

        results = await asyncio.gather(
            _apply(
                database, monkeypatch, module="ipc", target_id="ipc-1",
                role="invoice", user="op-a",
            ),
            _apply(
                database, monkeypatch, module="ipc", target_id="ipc-1",
                role="certified_ipc", user="op-b",
            ),
            return_exceptions=True,
        )

        assert [row for row in results if isinstance(row, Exception)] == []

        links = await _active_links(database)
        assert len(links) == 1, [row["relationship_role"] for row in links]
        assert await _creation_audits(database) == 1

        # Exactly one adjudication wins; the loser never gets a success-shaped
        # answer for a relationship it did not write. Whether it is told
        # `in_progress` (winner not yet confirmed) or `role_conflict` (winner
        # confirmed) depends only on interleaving, so pinning one of them here
        # would be pinning machine speed. The non-terminal case is proven
        # deterministically in test_ipc_legacy_relationship_backfill.py against
        # a claim whose lease is still live.
        statuses = [row["results"][0]["status"] for row in results]
        assert statuses.count("backfilled") == 1, statuses
        loser = [status for status in statuses if status != "backfilled"]
        assert loser == ["in_progress"] or loser == ["role_conflict"], statuses

        # Retrying after the winner confirms resolves terminally, naming the
        # role that actually won.
        won_role = links[0]["relationship_role"]
        losing_role = "certified_ipc" if won_role == "invoice" else "invoice"
        retry = await _apply(
            database, monkeypatch, module="ipc", target_id="ipc-1",
            role=losing_role, user="op-b",
        )
        assert retry["results"][0]["status"] == "role_conflict"
        assert retry["results"][0]["existing_role"] == won_role
        assert len(await _active_links(database)) == 1
        assert await _creation_audits(database) == 1
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_concurrent_same_role_still_reports_already_canonical(
    monkeypatch,
) -> None:
    """Existing same-role fencing must not regress."""
    client, database = await _new_database()
    try:
        await _seed_ipc(database)

        results = await asyncio.gather(
            _apply(
                database, monkeypatch, module="ipc", target_id="ipc-1",
                role="invoice", user="op-a",
            ),
            _apply(
                database, monkeypatch, module="ipc", target_id="ipc-1",
                role="invoice", user="op-b",
            ),
            return_exceptions=True,
        )

        assert [row for row in results if isinstance(row, Exception)] == []
        assert len(await _active_links(database)) == 1
        assert await _creation_audits(database) == 1
        statuses = [row["results"][0]["status"] for row in results]
        assert statuses.count("backfilled") == 1, statuses
        # Same-role: the loser is either still waiting on the winner, or is
        # correctly told the relationship already exists — never that it created
        # one. Timing decides which; both are truthful.
        loser = [status for status in statuses if status != "backfilled"]
        assert loser == ["in_progress"] or loser == ["already_canonical"], statuses

        # Once the winner has confirmed, a retry is terminal and idempotent.
        retry = await _apply(
            database, monkeypatch, module="ipc", target_id="ipc-1",
            role="invoice", user="op-b",
        )
        assert retry["results"][0]["status"] == "already_canonical"
        assert len(await _active_links(database)) == 1
        assert await _creation_audits(database) == 1
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_sequential_second_role_is_refused(monkeypatch) -> None:
    """Non-concurrent case: a later operator must not silently mint a second
    migration-derived role for the same legacy candidate."""
    client, database = await _new_database()
    try:
        await _seed_ipc(database)

        first = await _apply(
            database, monkeypatch, module="ipc", target_id="ipc-1", role="invoice"
        )
        second = await _apply(
            database,
            monkeypatch,
            module="ipc",
            target_id="ipc-1",
            role="supporting_document",
            user="op-b",
        )

        assert first["results"][0]["status"] == "backfilled"
        assert second["results"][0]["status"] == "role_conflict"
        assert second["results"][0]["existing_role"] == "invoice"
        links = await _active_links(database)
        assert len(links) == 1
        assert links[0]["relationship_role"] == "invoice"
        assert await _creation_audits(database) == 1
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_ordinary_multi_role_relationships_still_work(
    monkeypatch,
) -> None:
    """The fix is migration-scoped: ordinary relationship writes may still hold
    the same Document under two roles."""
    from rbac_backend.models.document_relationship import DocumentRelationshipInput
    from rbac_backend.services.document_relationship_service import (
        DocumentRelationshipService,
    )

    client, database = await _new_database()
    try:
        await _seed_ipc(database)
        service = DocumentRelationshipService(database, policy=_OperatorPolicy())
        actor = _operator("op-a")

        await service.link_batch(
            actor, "ipc_bill", "ipc-1",
            [DocumentRelationshipInput(document_id="doc-1", relationship_role="invoice")],
            source="user",
        )
        await service.link_batch(
            actor, "ipc_bill", "ipc-1",
            [
                DocumentRelationshipInput(
                    document_id="doc-1", relationship_role="certified_ipc"
                )
            ],
            source="user",
        )

        links = await _active_links(database)
        assert len(links) == 2
        assert sorted(row["relationship_role"] for row in links) == [
            "certified_ipc",
            "invoice",
        ]
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_role_switch_cannot_evade_a_human_removal(
    monkeypatch,
) -> None:
    """previously_removed must still win when the retry uses a different role."""
    client, database = await _new_database()
    try:
        await _seed_ipc(database)
        await _apply(
            database, monkeypatch, module="ipc", target_id="ipc-1", role="invoice"
        )
        link = (await _active_links(database))[0]
        await database.entity_document_links.update_one(
            {"_id": link["_id"]},
            {"$set": {"removed_at": "2026-08-23T00:00:00Z", "removal_reason": "Wrong bill"}},
        )

        result = await _apply(
            database,
            monkeypatch,
            module="ipc",
            target_id="ipc-1",
            role="certified_ipc",
            user="op-b",
        )

        assert result["results"][0]["status"] == "previously_removed"
        assert await database.entity_document_links.count_documents(
            {"removed_at": None}
        ) == 0
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_dry_run_never_claims_ownership(monkeypatch) -> None:
    """Two dry-runs under different roles leave no durable claim, and a later
    real run is not blocked."""
    client, database = await _new_database()
    try:
        await _seed_ipc(database)

        await _apply(
            database, monkeypatch, module="ipc", target_id="ipc-1",
            role="invoice", dry_run=True,
        )
        await _apply(
            database, monkeypatch, module="ipc", target_id="ipc-1",
            role="certified_ipc", user="op-b", dry_run=True,
        )

        assert await database.entity_document_links.count_documents({}) == 0
        assert await database.audit_events.count_documents({}) == 0

        real = await _apply(
            database, monkeypatch, module="ipc", target_id="ipc-1", role="invoice"
        )
        assert real["results"][0]["status"] == "backfilled"
        assert len(await _active_links(database)) == 1
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_claim_candidate_is_equally_protected(monkeypatch) -> None:
    """The reconciliation is common, not IPC-specific: Claim resolves
    `supporting_document` from its classifier, and an operator override to a
    different role must not mint a second migration relationship."""
    client, database = await _new_database()
    try:
        await _seed_claim(database)

        first = await _apply(
            database, monkeypatch, module="claim", target_id="claim-1", role=None
        )
        second = await _apply(
            database,
            monkeypatch,
            module="claim",
            target_id="claim-1",
            role="notice",
            user="op-b",
        )

        assert first["results"][0]["status"] == "backfilled"
        assert first["results"][0]["relationship_role"] == "supporting_document"
        assert second["results"][0]["status"] == "role_conflict"
        assert len(await _active_links(database)) == 1
        assert await _creation_audits(database) == 1
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_a_failed_adjudication_does_not_poison_the_candidate(
    monkeypatch,
) -> None:
    """Ownership is released when the write fails, so the candidate stays
    retryable rather than becoming permanently locked."""
    client, database = await _new_database()
    try:
        await _seed_ipc(database)

        rejected = await _apply(
            database, monkeypatch, module="ipc", target_id="ipc-1", role="policy"
        )
        assert rejected["results"][0]["status"] == "rejected"
        assert await database.entity_document_links.count_documents({}) == 0

        retried = await _apply(
            database, monkeypatch, module="ipc", target_id="ipc-1", role="invoice"
        )

        assert retried["results"][0]["status"] == "backfilled"
        assert len(await _active_links(database)) == 1
        assert await _creation_audits(database) == 1
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_an_abandoned_lease_is_reclaimed(monkeypatch) -> None:
    """The self-heal path: a crashed adjudication leaves a claim behind, and
    the ONLY thing preventing a permanently stuck candidate is lease expiry.

    Simulates the crash the try/finally cannot cover — the process dying — by
    leaving a `processing` claim whose lease has passed.
    """
    from datetime import datetime, timedelta, timezone

    from rbac_backend.services.legacy_relationship_backfill import (
        BACKFILL_CLAIMS_COLLECTION,
    )

    client, database = await _new_database()
    try:
        await _seed_ipc(database)
        claim_id = (
            "legacy-backfill:ipc:linked_document_ids:ipc_bill:ipc-1:doc-1"
        )
        await database[BACKFILL_CLAIMS_COLLECTION].insert_one(
            {
                "_id": claim_id,
                "relationship_role": "certified_ipc",
                "migration_run_id": "crashed-run",
                "owner_token": "crashed-token",
                "status": "processing",
                "claimed_at": datetime.now(timezone.utc) - timedelta(hours=1),
                "lease_expires_at": datetime.now(timezone.utc) - timedelta(minutes=1),
            }
        )

        # A live lease would block; an expired one must be reclaimable.
        result = await _apply(
            database, monkeypatch, module="ipc", target_id="ipc-1", role="invoice"
        )

        assert result["results"][0]["status"] == "backfilled"
        links = await _active_links(database)
        assert len(links) == 1
        assert links[0]["relationship_role"] == "invoice"
        assert await _creation_audits(database) == 1

        claim = await database[BACKFILL_CLAIMS_COLLECTION].find_one({"_id": claim_id})
        assert claim["status"] == "complete"
        assert claim["owner_token"] != "crashed-token"
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_a_live_lease_is_not_stealable(monkeypatch) -> None:
    """The converse: an unexpired lease held by another run must NOT be taken."""
    from datetime import datetime, timedelta, timezone

    from rbac_backend.services.legacy_relationship_backfill import (
        BACKFILL_CLAIMS_COLLECTION,
    )

    client, database = await _new_database()
    try:
        await _seed_ipc(database)
        claim_id = (
            "legacy-backfill:ipc:linked_document_ids:ipc_bill:ipc-1:doc-1"
        )
        await database[BACKFILL_CLAIMS_COLLECTION].insert_one(
            {
                "_id": claim_id,
                "relationship_role": "certified_ipc",
                "migration_run_id": "live-run",
                "owner_token": "live-token",
                "status": "processing",
                "claimed_at": datetime.now(timezone.utc),
                "lease_expires_at": datetime.now(timezone.utc) + timedelta(minutes=15),
            }
        )

        result = await _apply(
            database, monkeypatch, module="ipc", target_id="ipc-1", role="invoice"
        )

        assert result["results"][0]["status"] == "in_progress"
        assert await database.entity_document_links.count_documents({}) == 0
        claim = await database[BACKFILL_CLAIMS_COLLECTION].find_one({"_id": claim_id})
        assert claim["owner_token"] == "live-token"
    finally:
        await client.drop_database(database.name)
        client.close()
