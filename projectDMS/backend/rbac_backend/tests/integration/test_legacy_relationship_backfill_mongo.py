"""Real Mongo checks for the unified legacy relationship backfill.

Opt-in only, and shares the Insurance tracer's disposable replica set: set
INSURANCE_TRACER_MONGODB_URI to a test-only replica set. Every test uses and
drops a random database.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
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
        Permissions.INSURANCE_EDIT,
        Permissions.DOCUMENT_VIEW,
    }
    return SimpleNamespace(
        id="backfill-operator", organization_id="org-1", permissions=granted
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
    await database.insurance_policies.insert_one(
        {
            "_id": "insurance-1",
            "policy_number": "POL-17",
            "insurance_type": "Marine Cargo Insurance",
            "organization_id": "org-1",
            "project_id": "project-1",
            "linked_document_ids": ["doc-1"],
        }
    )
    document = {
        "_id": "doc-1",
        "organization_id": "org-1",
        "project_id": "project-1",
        "filename": "Legacy evidence.pdf",
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
            "sha256": "a" * 64,
        }
    )


async def _apply(database, tmp_path: Path, monkeypatch, **overrides):
    async def _step_up(*_args, **_kwargs):
        return None

    monkeypatch.setattr(backfill_router, "require_step_up", _step_up)
    monkeypatch.setattr(backfill_router, "_legacy_insurance_root", lambda: tmp_path)

    kwargs = {
        "request": _request(),
        "module": "insurance",
        "selections": [
            {
                "target_id": "insurance-1",
                "document_id": "doc-1",
                "relationship_role": "supporting_document",
            }
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


async def _active_links(database) -> list:
    return [row async for row in database.entity_document_links.find({"removed_at": None})]


@pytest.mark.asyncio
async def test_real_mongo_backfill_creates_exactly_one_canonical_relationship(
    tmp_path: Path, monkeypatch
) -> None:
    client, database = await _new_database()
    try:
        await _seed(database)

        result = await _apply(database, tmp_path, monkeypatch)

        assert result["results"][0]["status"] == "backfilled"
        links = await _active_links(database)
        assert len(links) == 1
        assert links[0]["target_type"] == "insurance"
        assert links[0]["document_id"] == "doc-1"
        assert links[0]["source"] == "migration"
        assert (
            await database.audit_events.count_documents(
                {"action": "legacy_relationship.backfilled"}
            )
            == 1
        )
        # Legacy array survives: this programme backfills, it does not retire.
        policy = await database.insurance_policies.find_one({"_id": "insurance-1"})
        assert policy["linked_document_ids"] == ["doc-1"]
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_backfill_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    client, database = await _new_database()
    try:
        await _seed(database)

        first = await _apply(database, tmp_path, monkeypatch)
        second = await _apply(database, tmp_path, monkeypatch)

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
async def test_real_mongo_concurrent_backfill_does_not_duplicate_authority(
    tmp_path: Path, monkeypatch
) -> None:
    client, database = await _new_database()
    try:
        await _seed(database)

        results = await asyncio.gather(
            _apply(database, tmp_path, monkeypatch),
            _apply(database, tmp_path, monkeypatch),
            return_exceptions=True,
        )

        # Whatever the interleaving: one relationship, and exactly one audit —
        # the loser must not record a write it did not perform.
        assert [row for row in results if isinstance(row, Exception)] == []
        links = await _active_links(database)
        assert len(links) == 1
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

        retry = await _apply(database, tmp_path, monkeypatch)
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
async def test_real_mongo_dry_run_mutates_nothing(tmp_path: Path, monkeypatch) -> None:
    client, database = await _new_database()
    try:
        await _seed(database)

        result = await _apply(database, tmp_path, monkeypatch, dry_run=True)

        assert result["results"][0]["status"] == "eligible"
        assert await database.entity_document_links.count_documents({}) == 0
        assert await database.audit_events.count_documents({}) == 0
        assert await database.documents.count_documents({"_id": "doc-1"}) == 1
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_blocked_document_is_never_backfilled(
    tmp_path: Path, monkeypatch
) -> None:
    client, database = await _new_database()
    try:
        await _seed(database, processing_status="human_review_required")

        result = await _apply(database, tmp_path, monkeypatch)

        assert result["results"][0]["status"] == "requires_manual_review"
        assert await database.entity_document_links.count_documents({}) == 0
        assert (
            await database.audit_events.count_documents(
                {"action": "legacy_relationship.backfilled"}
            )
            == 0
        )
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_cross_scope_candidate_is_rejected(
    tmp_path: Path, monkeypatch
) -> None:
    client, database = await _new_database()
    try:
        await _seed(database)
        # Operator scoped to a different project than the target.
        result = await _apply(
            database, tmp_path, monkeypatch, project_id="project-2"
        )

        assert result["results"][0]["status"] == "out_of_scope"
        assert await database.entity_document_links.count_documents({}) == 0
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_ambiguous_event_candidate_stays_manual_review(
    tmp_path: Path, monkeypatch
) -> None:
    """Pins the event-bearing protection before BG / Key Date adapters land."""
    import dataclasses

    client, database = await _new_database()
    try:
        await _seed(database)
        real = backfill_router._MODULES["insurance"].classify

        async def _ambiguous(db_arg, **kwargs):
            report = await real(db_arg, **kwargs)
            for row in report["candidates"]:
                if row.get("source_kind") == "legacy_linked_document_id":
                    row["findings"] = list(row["findings"]) + ["ambiguous_event"]
            return report

        monkeypatch.setitem(
            backfill_router._MODULES,
            "insurance",
            dataclasses.replace(
                backfill_router._MODULES["insurance"], classify=_ambiguous
            ),
        )

        result = await _apply(database, tmp_path, monkeypatch)

        assert result["results"][0]["status"] == "requires_manual_review"
        assert result["results"][0]["blocking_findings"] == ["ambiguous_event"]
        assert await database.entity_document_links.count_documents({}) == 0
    finally:
        await client.drop_database(database.name)
        client.close()


# -- real target authorization ----------------------------------------------
#
# Driven through the production seam: DocumentRelationshipService._target
# resolves the insurance adapter and calls PolicyService.authorize_document with
# the adapter's own INSURANCE_EDIT manage permission.


async def _claims_taken(database) -> int:
    return await database[BACKFILL_CLAIMS_COLLECTION].count_documents({})


@pytest.mark.asyncio
async def test_real_mongo_insurance_backfill_requires_the_insurance_manage_permission(
    tmp_path: Path, monkeypatch
) -> None:
    client, database = await _new_database()
    try:
        await _seed(database)
        reader = _operator(
            Permissions.DMS_ADMIN,
            Permissions.INSURANCE_VIEW,
            Permissions.DOCUMENT_VIEW,
        )

        result = await _apply(database, tmp_path, monkeypatch, current_user=reader)

        assert result["results"][0]["status"] == "rejected"
        assert await _active_links(database) == []
        assert await database.audit_events.count_documents({}) == 0
        assert await _claims_taken(database) == 0
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_dms_admin_alone_cannot_backfill_insurance(
    tmp_path: Path, monkeypatch
) -> None:
    client, database = await _new_database()
    try:
        await _seed(database)
        admin = _operator(Permissions.DMS_ADMIN, Permissions.DOCUMENT_VIEW)

        result = await _apply(database, tmp_path, monkeypatch, current_user=admin)

        assert result["results"][0]["status"] == "rejected"
        assert await _active_links(database) == []
        assert await database.audit_events.count_documents({}) == 0
        assert await _claims_taken(database) == 0
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_insurance_has_no_freeze_semantics(
    tmp_path: Path, monkeypatch
) -> None:
    """Pinned as a fact about the CURRENT adapter, not an invented rule.

    InsuranceEntityAdapter builds its EntityContext with `frozen=False`
    unconditionally, so a freeze-looking field on the policy row means nothing
    here. Asserting a refusal would be inventing lifecycle semantics Insurance
    does not have.
    """
    client, database = await _new_database()
    try:
        await _seed(database)
        await database.insurance_policies.update_one(
            {"_id": "insurance-1"},
            {"$set": {"evidence_frozen_at": "2026-08-01T00:00:00Z"}},
        )

        result = await _apply(database, tmp_path, monkeypatch)

        assert result["results"][0]["status"] == "backfilled"
        assert len(await _active_links(database)) == 1
    finally:
        await client.drop_database(database.name)
        client.close()
