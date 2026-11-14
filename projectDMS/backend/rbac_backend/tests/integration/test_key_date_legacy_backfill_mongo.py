"""Real Mongo verification for Key Date / Milestone / EOT legacy backfill.

Opt-in: shares the disposable replica set named by INSURANCE_TRACER_MONGODB_URI.

Charter under test: NEVER COLLAPSE CONTRACTUAL EVENT EVIDENCE TO THE WRONG KEY
DATE / EOT EVENT. Key Date is the one register whose legacy evidence sits on the
event rows themselves, so exact ownership is physically provable and the module
is WRITE_BACKFILL — but only for those three sources. The milestone parent array
and the deprecated EOT application shape prove nothing about which event owns
the evidence and are registered inventory-only.

Every assertion below reads final physical Mongo state, never a return value
alone.
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

SCOPE = {"organization_id": "org-1", "project_id": "project-1"}


class _OperatorPolicy:
    """Permission-faithful stub.

    `authorize_document` is the seam that carries each adapter's own
    manage/view/freeze permission, so stubbing it to a no-op — as the older
    module integration files do — silently removes target-side authorization
    from every assertion. It enforces here.
    """

    async def authorize_document(
        self, actor: Any, permission: str, document: Any, **kwargs: Any
    ) -> None:
        await self.authorize(actor, permission)

    async def authorize(self, actor: Any, permission: str, **kwargs: Any) -> None:
        if permission not in getattr(actor, "permissions", set()):
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail="Denied")


def _operator(user_id: str = "kd-operator", *permissions: str) -> SimpleNamespace:
    granted = set(permissions) or {
        Permissions.DMS_ADMIN,
        Permissions.KEYDATE_VIEW,
        Permissions.KEYDATE_EDIT,
        Permissions.KEYDATE_ACHIEVEMENT,
        Permissions.KEYDATE_EOT_SUBMIT,
        Permissions.KEYDATE_EOT_DETERMINE,
        Permissions.DOCUMENT_VIEW,
    }
    return SimpleNamespace(id=user_id, organization_id="org-1", permissions=granted)


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


async def _seed_document(database: Any, document_id: str, **overrides: Any) -> None:
    row = {
        "_id": document_id,
        "filename": f"{document_id}.pdf",
        "lifecycle_state": "active",
        "processing_status": "completed",
        "duplicate_status": "unique",
        "current_version_id": f"{document_id}:v1",
        "file_object_id": f"{document_id}:f1",
        **SCOPE,
    }
    row.update(overrides)
    await database.documents.insert_one(row)
    await database.document_versions.insert_one(
        {
            "_id": f"{document_id}:v1",
            "document_id": document_id,
            "file_object_id": f"{document_id}:f1",
            "version_number": 1,
            "is_current": True,
        }
    )
    await database.file_objects.insert_one(
        {"_id": f"{document_id}:f1", "sha256": "a" * 64, **SCOPE}
    )


async def _seed(database: Any, **document_overrides: Any) -> None:
    """Milestone M1: achievement A, submissions S1/S2, determinations D1/D2.

    Each event owns its OWN document, so any collapse is directly visible in
    final state. The milestone parent array and the deprecated application row
    carry their own documents too, so their inventory-only posture is testable.
    """
    await database.key_date_milestones.insert_one(
        {
            "_id": "M1",
            "title": "Sectional completion",
            **SCOPE,
            "linked_document_ids": ["doc-parent"],
        }
    )
    await database.key_date_achievements.insert_one(
        {"_id": "A", "milestone_id": "M1", **SCOPE, "linked_document_ids": ["doc-A"]}
    )
    await database.key_date_eot_submissions.insert_many(
        [
            {
                "_id": "S1",
                "key_date_id": "M1",
                "contract_id": "primary",
                **SCOPE,
                "revision_label": "R1",
                "linked_document_ids": ["doc-S1"],
            },
            {
                "_id": "S2",
                "key_date_id": "M1",
                "contract_id": "primary",
                **SCOPE,
                "revision_label": "R2",
                "linked_document_ids": ["doc-S2"],
            },
        ]
    )
    await database.key_date_eot_determinations.insert_many(
        [
            {
                "_id": "D1",
                "key_date_id": "M1",
                "contract_id": "primary",
                **SCOPE,
                "linked_document_ids": ["doc-D1"],
            },
            {
                "_id": "D2",
                "key_date_id": "M1",
                "contract_id": "primary",
                **SCOPE,
                "linked_document_ids": ["doc-D2"],
            },
        ]
    )
    await database.key_date_eot_applications.insert_one(
        {
            "_id": "L1",
            "milestone_id": "M1",
            **SCOPE,
            "linked_document_ids": ["doc-legacy"],
        }
    )
    await database.key_date_baselines.insert_one(
        {"_id": "B1", "contract_id": "primary", "status": "frozen", **SCOPE}
    )
    for document_id in (
        "doc-parent",
        "doc-A",
        "doc-S1",
        "doc-S2",
        "doc-D1",
        "doc-D2",
        "doc-legacy",
    ):
        await _seed_document(database, document_id, **document_overrides)


async def _apply(database, monkeypatch, *, module="key_date", **overrides):
    async def _step_up(*_args, **_kwargs):
        return None

    monkeypatch.setattr(backfill_router, "require_step_up", _step_up)
    kwargs = {
        "request": _request(),
        "module": module,
        "selections": [
            {
                "target_id": "S1",
                "document_id": "doc-S1",
                "relationship_role": "eot_submission",
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


async def _inventory(database, monkeypatch, *, module="key_date", **overrides):
    async def _step_up(*_args, **_kwargs):
        return None

    monkeypatch.setattr(backfill_router, "require_step_up", _step_up)
    kwargs = {
        "request": _request(),
        "module": module,
        "org_id": "org-1",
        "project_id": "project-1",
        "db": database,
        "current_user": _operator(),
        "policy": _OperatorPolicy(),
    }
    kwargs.update(overrides)
    return await backfill_router.inventory_legacy_relationship_backfill(**kwargs)


async def _links(database) -> list:
    return [
        row async for row in database.entity_document_links.find({"removed_at": None})
    ]


async def _audits(database) -> list:
    return [
        row
        async for row in database.audit_events.find(
            {"action": "legacy_relationship.backfilled"}
        )
    ]


# -- exact event ownership ---------------------------------------------------


@pytest.mark.asyncio
async def test_real_mongo_every_event_document_lands_on_its_own_event(
    monkeypatch,
) -> None:
    """The charter, in physical state: five documents, five events, zero
    collapse onto the milestone parent or onto a sibling event."""
    client, database = await _new_database()
    try:
        await _seed(database)

        for target_id, document_id, role in (
            ("A", "doc-A", "completion_certificate"),
            ("S1", "doc-S1", "eot_submission"),
            ("S2", "doc-S2", "eot_submission"),
            ("D1", "doc-D1", "eot_determination"),
            ("D2", "doc-D2", "eot_determination"),
        ):
            result = await _apply(
                database,
                monkeypatch,
                selections=[
                    {
                        "target_id": target_id,
                        "document_id": document_id,
                        "relationship_role": role,
                    }
                ],
            )
            assert result["results"][0]["status"] == "backfilled", (target_id, result)

        owned = sorted(
            (row["target_type"], row["target_id"], row["document_id"])
            for row in await _links(database)
        )
        assert owned == [
            ("eot_determination", "D1", "doc-D1"),
            ("eot_determination", "D2", "doc-D2"),
            ("eot_submission", "S1", "doc-S1"),
            ("eot_submission", "S2", "doc-S2"),
            ("key_date_achievement", "A", "doc-A"),
        ]
        assert len(await _audits(database)) == 5
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_legacy_arrays_are_left_intact(monkeypatch) -> None:
    """Backfill adds canonical relationships; it never rewrites the legacy row,
    so a bad migration stays reversible from the evidence that produced it."""
    client, database = await _new_database()
    try:
        await _seed(database)

        await _apply(database, monkeypatch)

        submission = await database.key_date_eot_submissions.find_one({"_id": "S1"})
        milestone = await database.key_date_milestones.find_one({"_id": "M1"})
        assert submission["linked_document_ids"] == ["doc-S1"]
        assert milestone["linked_document_ids"] == ["doc-parent"]
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_one_document_on_two_events_stays_two_relationships(
    monkeypatch,
) -> None:
    """A letter can be evidence for a submission AND for the determination that
    answers it. Two contractual events, two relationships — never merged."""
    client, database = await _new_database()
    try:
        await _seed(database)
        await database.key_date_eot_submissions.update_one(
            {"_id": "S1"}, {"$set": {"linked_document_ids": ["doc-shared"]}}
        )
        await database.key_date_eot_determinations.update_one(
            {"_id": "D1"}, {"$set": {"linked_document_ids": ["doc-shared"]}}
        )
        await _seed_document(database, "doc-shared")

        await _apply(
            database,
            monkeypatch,
            selections=[
                {
                    "target_id": "S1",
                    "document_id": "doc-shared",
                    "relationship_role": "eot_submission",
                }
            ],
        )
        await _apply(
            database,
            monkeypatch,
            selections=[
                {
                    "target_id": "D1",
                    "document_id": "doc-shared",
                    "relationship_role": "eot_determination",
                }
            ],
        )

        owned = sorted(
            (row["target_type"], row["target_id"]) for row in await _links(database)
        )
        assert owned == [("eot_determination", "D1"), ("eot_submission", "S1")]
    finally:
        await client.drop_database(database.name)
        client.close()


# -- inventory-only sources --------------------------------------------------


@pytest.mark.asyncio
async def test_real_mongo_parent_and_deprecated_sources_are_never_writable(
    monkeypatch,
) -> None:
    from fastapi import HTTPException

    client, database = await _new_database()
    try:
        await _seed(database)

        for target_id, document_id in (("M1", "doc-parent"), ("L1", "doc-legacy")):
            with pytest.raises(HTTPException) as excinfo:
                await _apply(
                    database,
                    monkeypatch,
                    module="key_date_legacy",
                    selections=[
                        {
                            "target_id": target_id,
                            "document_id": document_id,
                            "relationship_role": "supporting_document",
                        }
                    ],
                )
            assert excinfo.value.status_code == 409

        assert await _links(database) == []
        assert await database.audit_events.count_documents({}) == 0
        assert await database[BACKFILL_CLAIMS_COLLECTION].count_documents({}) == 0
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_ambiguous_sources_stay_visible_for_reconciliation(
    monkeypatch,
) -> None:
    """Not writable is not the same as hidden: the operator must still see the
    evidence, marked with the finding no role can resolve."""
    client, database = await _new_database()
    try:
        await _seed(database)

        report = await _inventory(database, monkeypatch, module="key_date_legacy")

        by_kind = {row["source_kind"]: row for row in report["candidates"]}
        assert set(by_kind) == {
            "legacy_key_date_parent_array",
            "legacy_eot_application_array",
        }
        for row in by_kind.values():
            assert "ambiguous_event" in row["findings"]
        assert await _links(database) == []
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_writable_module_cannot_see_the_ambiguous_sources(
    monkeypatch,
) -> None:
    client, database = await _new_database()
    try:
        await _seed(database)

        report = await _inventory(database, monkeypatch)

        kinds = {row["source_kind"] for row in report["candidates"]}
        assert kinds == {
            "legacy_key_date_achievement_array",
            "legacy_eot_submission_array",
            "legacy_eot_determination_array",
        }
        for row in report["candidates"]:
            assert "ambiguous_event" not in row["findings"]
            assert row["target_id"]
    finally:
        await client.drop_database(database.name)
        client.close()


# -- authority before role ---------------------------------------------------


@pytest.mark.asyncio
async def test_real_mongo_a_blocked_document_never_migrates(monkeypatch) -> None:
    client, database = await _new_database()
    try:
        await _seed(database, processing_status="human_review_required")

        result = await _apply(database, monkeypatch)

        assert result["results"][0]["status"] == "requires_manual_review"
        assert "blocked_document" in result["results"][0]["findings"]
        assert await _links(database) == []
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_an_operationally_failed_document_still_migrates(
    monkeypatch,
) -> None:
    """Model B: `failed` is an operational processing outcome, not an authority
    denial — the document is still the contractual evidence it always was."""
    client, database = await _new_database()
    try:
        await _seed(database, processing_status="failed")

        result = await _apply(database, monkeypatch)

        assert result["results"][0]["status"] == "backfilled"
        assert len(await _links(database)) == 1
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_a_cross_project_document_never_migrates(monkeypatch) -> None:
    client, database = await _new_database()
    try:
        await _seed(database, project_id="project-2")

        result = await _apply(database, monkeypatch)

        assert result["results"][0]["status"] in {
            "requires_manual_review",
            "out_of_scope",
        }
        assert await _links(database) == []
    finally:
        await client.drop_database(database.name)
        client.close()


# -- role adjudication -------------------------------------------------------


@pytest.mark.asyncio
async def test_real_mongo_a_determination_role_is_illegal_on_a_submission(
    monkeypatch,
) -> None:
    client, database = await _new_database()
    try:
        await _seed(database)

        result = await _apply(
            database,
            monkeypatch,
            selections=[
                {
                    "target_id": "S1",
                    "document_id": "doc-S1",
                    "relationship_role": "eot_determination",
                }
            ],
        )

        assert result["results"][0]["status"] == "rejected"
        assert await _links(database) == []
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_submission_rights_do_not_authorize_a_determination(
    monkeypatch,
) -> None:
    """Contractual asymmetry survives the migration path: recording a contractor
    submission is not authority to record the engineer determination."""
    client, database = await _new_database()
    try:
        await _seed(database)
        operator = _operator(
            "kd-submitter",
            Permissions.DMS_ADMIN,
            Permissions.KEYDATE_VIEW,
            Permissions.KEYDATE_EOT_SUBMIT,
            Permissions.DOCUMENT_VIEW,
        )

        allowed = await _apply(database, monkeypatch, current_user=operator)
        refused = await _apply(
            database,
            monkeypatch,
            current_user=operator,
            selections=[
                {
                    "target_id": "D1",
                    "document_id": "doc-D1",
                    "relationship_role": "eot_determination",
                }
            ],
        )

        assert allowed["results"][0]["status"] == "backfilled"
        assert refused["results"][0]["status"] == "rejected"
        assert [row["target_id"] for row in await _links(database)] == ["S1"]
    finally:
        await client.drop_database(database.name)
        client.close()


# -- freeze / version pinning ------------------------------------------------


@pytest.mark.asyncio
async def test_real_mongo_a_locked_submission_is_not_mutated(monkeypatch) -> None:
    client, database = await _new_database()
    try:
        await _seed(database)
        await database.key_date_eot_submissions.update_one(
            {"_id": "S1"}, {"$set": {"locked_at": "2026-08-01T00:00:00Z"}}
        )

        frozen = await _apply(database, monkeypatch)
        sibling = await _apply(
            database,
            monkeypatch,
            selections=[
                {
                    "target_id": "S2",
                    "document_id": "doc-S2",
                    "relationship_role": "eot_submission",
                }
            ],
        )

        assert frozen["results"][0]["status"] == "frozen_target"
        assert sibling["results"][0]["status"] == "backfilled"
        assert [row["target_id"] for row in await _links(database)] == ["S2"]
    finally:
        await client.drop_database(database.name)
        client.close()


# -- idempotency / removal / dry run -----------------------------------------


@pytest.mark.asyncio
async def test_real_mongo_a_rerun_creates_nothing_further(monkeypatch) -> None:
    client, database = await _new_database()
    try:
        await _seed(database)

        first = await _apply(database, monkeypatch)
        second = await _apply(database, monkeypatch)

        assert first["results"][0]["status"] == "backfilled"
        assert second["results"][0]["status"] == "already_canonical"
        assert len(await _links(database)) == 1
        assert len(await _audits(database)) == 1
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_a_removed_relationship_is_not_resurrected(
    monkeypatch,
) -> None:
    """An operator who removed evidence from the wrong event decided something;
    a later backfill run must not silently undo that decision."""
    client, database = await _new_database()
    try:
        await _seed(database)
        await _apply(database, monkeypatch)
        await database.entity_document_links.update_one(
            {"target_id": "S1", "document_id": "doc-S1"},
            {
                "$set": {
                    "removed_at": "2026-08-24T00:00:00Z",
                    "removal_reason": "Wrong submission",
                }
            },
        )

        result = await _apply(database, monkeypatch)

        assert result["results"][0]["status"] == "previously_removed"
        assert await _links(database) == []
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_dry_run_writes_nothing_at_all(monkeypatch) -> None:
    client, database = await _new_database()
    try:
        await _seed(database)

        result = await _apply(database, monkeypatch, dry_run=True)

        assert result["results"][0]["status"] == "eligible"
        assert await database.entity_document_links.count_documents({}) == 0
        assert await database.audit_events.count_documents({}) == 0
        assert await database[BACKFILL_CLAIMS_COLLECTION].count_documents({}) == 0
    finally:
        await client.drop_database(database.name)
        client.close()


# -- concurrency -------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_mongo_concurrent_roles_on_one_event_yield_one_migration(
    monkeypatch,
) -> None:
    """Two operators adjudicating the same EOT submission differently: exactly
    one migration, and the loser is told it is in flight rather than being given
    a success-shaped answer for a link that may never exist."""
    client, database = await _new_database()
    try:
        await _seed(database)

        results = await asyncio.gather(
            _apply(
                database,
                monkeypatch,
                current_user=_operator("op-a"),
                selections=[
                    {
                        "target_id": "S1",
                        "document_id": "doc-S1",
                        "relationship_role": "eot_submission",
                    }
                ],
            ),
            _apply(
                database,
                monkeypatch,
                current_user=_operator("op-b"),
                selections=[
                    {
                        "target_id": "S1",
                        "document_id": "doc-S1",
                        "relationship_role": "eot_supporting_document",
                    }
                ],
            ),
            return_exceptions=True,
        )

        assert [row for row in results if isinstance(row, Exception)] == []
        statuses = [row["results"][0]["status"] for row in results]
        # The contract, not the timing. Exactly one adjudication wins. The loser
        # is told either that the outcome is not yet known (`in_progress`) or
        # that it is known and refused (`role_conflict`) -- which of the two it
        # sees depends only on whether the winner had confirmed its claim yet.
        # What it must NEVER get is a success-shaped answer for a relationship
        # it did not write.
        assert statuses.count("backfilled") == 1, statuses
        loser = [status for status in statuses if status != "backfilled"]
        assert loser == ["in_progress"] or loser == ["role_conflict"], statuses
        assert len(await _links(database)) == 1
        assert len(await _audits(database)) == 1
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_cold_start_concurrent_first_use_is_deterministic(
    monkeypatch,
) -> None:
    """Cold start must be deterministic, not merely survivable.

    The backfill commits the relationship and its audit in ONE transaction, so
    `audit_events` is a transaction participant. Mongo does not allow two
    transactions to create the same namespace implicitly at the same time, so a
    database where that collection has never been written can lose a migration
    to a namespace race rather than to anything about the data.

    The repository already owns this: `core.database.ensure_indexes` creates the
    `audit_events` indexes for a real deployment. The test harness must give the
    same guarantee before any concurrent transactional work begins.
    """
    client, database = await _new_database()
    try:
        await _seed(database)
        # No manual pre-touching: the harness initializer alone must have made
        # every transaction participant exist.
        names = set(await database.list_collection_names())
        assert "audit_events" in names
        assert "entity_document_links" in names

        results = await asyncio.gather(
            _apply(
                database,
                monkeypatch,
                current_user=_operator("op-a"),
                selections=[
                    {
                        "target_id": "S1",
                        "document_id": "doc-S1",
                        "relationship_role": "eot_submission",
                    }
                ],
            ),
            _apply(
                database,
                monkeypatch,
                current_user=_operator("op-b"),
                selections=[
                    {
                        "target_id": "S2",
                        "document_id": "doc-S2",
                        "relationship_role": "eot_submission",
                    }
                ],
            ),
            return_exceptions=True,
        )

        assert [row for row in results if isinstance(row, Exception)] == []
        assert all(row["results"][0]["status"] == "backfilled" for row in results)
        assert sorted(row["target_id"] for row in await _links(database)) == ["S1", "S2"]
        assert len(await _audits(database)) == 2
        stuck = [
            row
            async for row in database[BACKFILL_CLAIMS_COLLECTION].find(
                {"status": "processing"}
            )
        ]
        assert stuck == []
    finally:
        await client.drop_database(database.name)
        client.close()


# -- audit -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_real_mongo_the_audit_names_the_exact_event(monkeypatch) -> None:
    """An audit that named the milestone would make a wrong-event migration
    indistinguishable from a right one after the fact."""
    client, database = await _new_database()
    try:
        await _seed(database)

        await _apply(
            database,
            monkeypatch,
            selections=[
                {
                    "target_id": "D2",
                    "document_id": "doc-D2",
                    "relationship_role": "eot_determination",
                }
            ],
        )

        audits = await _audits(database)
        assert len(audits) == 1
        assert audits[0]["resource_type"] == "eot_determination"
        assert audits[0]["resource_id"] == "D2"
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_other_writable_modules_are_unaffected(monkeypatch) -> None:
    """Regression guard: registering Key Date must not disturb a module that was
    already accepted."""
    import tempfile
    from pathlib import Path

    client, database = await _new_database()
    try:
        await database.ipc_bills.insert_one(
            {
                "_id": "ipc-1",
                "ipc_number": "IPC-07",
                **SCOPE,
                "linked_document_ids": ["doc-1"],
            }
        )
        await _seed_document(database, "doc-1")

        async def _step_up(*_args, **_kwargs):
            return None

        monkeypatch.setattr(backfill_router, "require_step_up", _step_up)
        monkeypatch.setattr(
            backfill_router, "_legacy_insurance_root", lambda: Path(tempfile.mkdtemp())
        )

        result = await backfill_router.apply_legacy_relationship_backfill(
            request=_request(),
            module="ipc",
            selections=[
                {
                    "target_id": "ipc-1",
                    "document_id": "doc-1",
                    "relationship_role": "invoice",
                }
            ],
            org_id="org-1",
            project_id="project-1",
            dry_run=False,
            db=database,
            current_user=_operator(
                "ipc-op",
                Permissions.DMS_ADMIN,
                Permissions.IPC_VIEW,
                Permissions.IPC_EDIT,
                Permissions.DOCUMENT_VIEW,
            ),
            policy=_OperatorPolicy(),
        )

        assert result["results"][0]["status"] == "backfilled"
        assert len(await _links(database)) == 1
    finally:
        await client.drop_database(database.name)
        client.close()
