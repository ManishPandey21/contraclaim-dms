"""Real Mongo transaction/concurrency checks for the Key Date/EOT tracer.

Opt-in only: requires an explicit test-only replica-set URI and creates a
random disposable database that is dropped after each test.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from rbac_backend.migrations.v20260820_0001_entity_document_links import upgrade
from rbac_backend.models.document_relationship import DocumentRelationshipInput
from rbac_backend.models.key_date import (
    AchievementRecord,
    EOTApplicationCreate,
    EOTDeterminationUpdate,
    EOTSubmissionUpdate,
)
from rbac_backend.services.document_relationship_service import (
    DocumentRelationshipError,
    DocumentRelationshipService,
)
from rbac_backend.services.key_date_service import KeyDateService
from rbac_backend.services.key_date_revision_service import KeyDateRevisionService
from rbac_backend.tests.integration.test_claim_document_relationships_mongo import (
    InjectedFailure,
    _AllowPolicy,
    _FailingCollection,
    _FaultDatabase,
)


MONGODB_URI_ENV = "KEY_DATE_TRACER_MONGODB_URI"


def _actor() -> Any:
    return SimpleNamespace(id="key-date-tracer-verifier", organization_id="org-1")


async def _new_database() -> tuple[Any, Any]:
    uri = os.getenv(MONGODB_URI_ENV)
    if not uri:
        pytest.skip(f"set {MONGODB_URI_ENV} to a test-only MongoDB replica set")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5_000)
    hello = await client.admin.command("hello")
    if not hello.get("setName") or not hello.get("isWritablePrimary"):
        client.close()
        pytest.fail(f"{MONGODB_URI_ENV} is not a writable replica set")
    database = client[f"key_date_tracer_verification_{uuid.uuid4().hex}"]
    await upgrade(database, dry_run=False)
    return client, database


async def _seed(database: Any) -> None:
    now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    await database.key_date_milestones.insert_one(
        {
            "_id": "kd-1",
            "milestone_ref": "KD-01",
            "title": "Complete foundations",
            "organization_id": "org-1",
            "project_id": "project-1",
            "contract_id": "primary",
            "original_planned_key_date": now,
            "current_approved_key_date": now,
            "current_revision": 0,
        }
    )
    await database.key_date_baselines.insert_one(
        {
            "_id": "baseline-1",
            "organization_id": "org-1",
            "project_id": "project-1",
            "contract_id": "primary",
            "status": "frozen",
            "items": [{"key_date_id": "kd-1", "milestone_ref": "KD-01"}],
        }
    )
    await database.key_date_achievements.insert_one(
        {
            "_id": "kd-1:ach",
            "milestone_id": "kd-1",
            "organization_id": "org-1",
            "project_id": "project-1",
            "actual_achievement_date": now,
        }
    )
    await database.key_date_eot_submissions.insert_one(
        {
            "_id": "submission-1",
            "organization_id": "org-1",
            "project_id": "project-1",
            "contract_id": "primary",
            "revision_number": 1,
            "revision_label": "EOT-1",
            "status": "submitted",
            "contractor_submission_date": now,
            "contractor_letter_reference": "CON/EOT-1",
            "locked_at": None,
        }
    )
    await database.key_date_eot_submission_items.insert_one(
        {
            "_id": "submission-item-1",
            "eot_submission_id": "submission-1",
            "key_date_id": "kd-1",
            "milestone_ref": "KD-01",
            "eot_submitted_date": now,
        }
    )
    await database.key_date_eot_determinations.insert_one(
        {
            "_id": "determination-1",
            "organization_id": "org-1",
            "project_id": "project-1",
            "contract_id": "primary",
            "origin": "contractor_submission",
            "eot_submission_ids": ["submission-1"],
            "determination_reference": "ENG/DET-1",
            "determination_date": now,
            "status": "rejected",
            "frozen_at": None,
        }
    )
    await database.key_date_eot_determination_items.insert_one(
        {
            "_id": "determination-item-1",
            "determination_id": "determination-1",
            "key_date_id": "kd-1",
            "milestone_ref": "KD-01",
            "determination_result": "rejected",
        }
    )
    for document_id in ("doc-1", "doc-2"):
        await database.documents.insert_one(
            {
                "_id": document_id,
                "filename": f"{document_id}.pdf",
                "organization_id": "org-1",
                "project_id": "project-1",
                "processing_status": "metadata_extracted",
                "lifecycle_state": "active",
                "current_version_id": f"{document_id}:v1",
            }
        )
        await database.document_versions.insert_one(
            {
                "_id": f"{document_id}:v1",
                "document_id": document_id,
                "version_number": 1,
                "is_current": True,
                "file_object_id": f"{document_id}:file",
            }
        )


@pytest.mark.asyncio
async def test_real_mongo_concurrent_identical_achievement_links_are_idempotent() -> None:
    client, database = await _new_database()
    try:
        await _seed(database)
        service = DocumentRelationshipService(database, policy=_AllowPolicy())
        request = [
            DocumentRelationshipInput(
                document_id="doc-1", relationship_role="completion_certificate"
            )
        ]
        first, second = await asyncio.gather(
            service.link_batch(
                _actor(), "key_date_achievement", "kd-1:ach", request,
                idempotency_key="key-date-real-link",
            ),
            service.link_batch(
                _actor(), "key_date_achievement", "kd-1:ach", request,
                idempotency_key="key-date-real-link",
            ),
        )

        assert first[0].id == second[0].id
        assert await database.entity_document_links.count_documents(
            {"target_type": "key_date_achievement", "removed_at": None}
        ) == 1
        assert await database.audit_events.count_documents(
            {"action": "document_relationship.linked"}
        ) == 1
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_achievement_parent_event_and_audit_roll_back_together() -> None:
    client, database = await _new_database()
    try:
        await _seed(database)
        milestone = await database.key_date_milestones.find_one({"_id": "kd-1"})
        original_event = await database.key_date_achievements.find_one({"_id": "kd-1:ach"})
        fault_database = _FaultDatabase(
            database,
            audit_events=_FailingCollection(
                database.audit_events,
                fail_insert=lambda row: row.get("action") == "keydate.achievement.recorded",
            ),
        )

        with pytest.raises(InjectedFailure, match="injected insert failure"):
            await KeyDateService(fault_database).record_achievement(
                milestone,
                AchievementRecord(
                    actual_achievement_date=datetime(2026, 8, 5, tzinfo=timezone.utc),
                    client_notification_ref="CON/KD-01/ACH",
                ),
                _actor(),
            )

        stored_parent = await database.key_date_milestones.find_one({"_id": "kd-1"})
        stored_event = await database.key_date_achievements.find_one({"_id": "kd-1:ach"})
        assert stored_parent.get("actual_achievement_date") is None
        assert stored_event == original_event
        assert await database.audit_events.count_documents(
            {"action": "keydate.achievement.recorded"}
        ) == 0
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_concurrent_achievement_replacements_keep_one_stable_aligned_event() -> None:
    client, database = await _new_database()
    try:
        await _seed(database)
        milestone = await database.key_date_milestones.find_one({"_id": "kd-1"})
        service = KeyDateService(database)
        await asyncio.gather(
            service.record_achievement(
                milestone,
                AchievementRecord(actual_achievement_date=datetime(2026, 8, 3, tzinfo=timezone.utc)),
                _actor(),
            ),
            service.record_achievement(
                milestone,
                AchievementRecord(actual_achievement_date=datetime(2026, 8, 4, tzinfo=timezone.utc)),
                _actor(),
            ),
        )

        parent = await database.key_date_milestones.find_one({"_id": "kd-1"})
        event = await database.key_date_achievements.find_one({"_id": "kd-1:ach"})
        assert event["milestone_id"] == "kd-1"
        assert event["actual_achievement_date"] == parent["actual_achievement_date"]
        assert await database.key_date_achievements.count_documents({"milestone_id": "kd-1"}) == 1
        assert await database.audit_events.count_documents(
            {"action": "keydate.achievement.recorded"}
        ) == 2
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_type", "target_id", "role", "frozen_query"),
    [
        ("eot_submission", "submission-1", "eot_submission", {"status": "locked"}),
        ("eot_determination", "determination-1", "engineer_determination", {"frozen_at": {"$ne": None}}),
    ],
)
async def test_real_mongo_link_freeze_race_has_no_partial_evidence_freeze(
    target_type: str, target_id: str, role: str, frozen_query: dict[str, Any]
) -> None:
    client, database = await _new_database()
    try:
        await _seed(database)
        service = DocumentRelationshipService(database, policy=_AllowPolicy())
        link_result, freeze_result = await asyncio.gather(
            service.link_batch(
                _actor(), target_type, target_id,
                [DocumentRelationshipInput(document_id="doc-1", relationship_role=role)],
                idempotency_key=f"{target_id}-race",
            ),
            service.freeze(
                _actor(), target_type, target_id, reason="real race",
                lifecycle_orchestrated=True,
            ),
            return_exceptions=True,
        )

        assert not isinstance(freeze_result, Exception), freeze_result
        collection = (
            database.key_date_eot_submissions
            if target_type == "eot_submission"
            else database.key_date_eot_determinations
        )
        assert await collection.count_documents({"_id": target_id, **frozen_query}) == 1
        active = [
            row async for row in database.entity_document_links.find(
                {"target_type": target_type, "target_id": target_id, "removed_at": None}
            )
        ]
        assert len(active) in {0, 1}
        assert all(row.get("frozen_at") and row.get("document_version_id") for row in active)
        if isinstance(link_result, Exception):
            assert isinstance(link_result, DocumentRelationshipError)
            assert link_result.status_code == 409
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_missing_version_rolls_back_submission_freeze() -> None:
    client, database = await _new_database()
    try:
        await _seed(database)
        service = DocumentRelationshipService(database, policy=_AllowPolicy())
        await service.link_batch(
            _actor(), "eot_submission", "submission-1",
            [DocumentRelationshipInput(document_id="doc-1", relationship_role="eot_submission")],
        )
        await database.document_versions.delete_many({"document_id": "doc-1"})

        with pytest.raises(DocumentRelationshipError, match="(?i)current.*version"):
            await service.freeze(
                _actor(), "eot_submission", "submission-1", reason="must roll back",
                lifecycle_orchestrated=True,
            )

        submission = await database.key_date_eot_submissions.find_one({"_id": "submission-1"})
        relationship = await database.entity_document_links.find_one(
            {"target_type": "eot_submission", "target_id": "submission-1"}
        )
        assert submission["locked_at"] is None
        assert submission["status"] == "submitted"
        assert relationship.get("frozen_at") is None
        assert relationship.get("document_version_id") is None
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_type", "target_id"),
    [
        ("eot_submission", "submission-1"),
        ("eot_determination", "determination-1"),
    ],
)
async def test_real_mongo_item_update_and_freeze_race_keeps_valid_snapshot(
    target_type: str, target_id: str
) -> None:
    client, database = await _new_database()
    try:
        await _seed(database)
        relationship_service = DocumentRelationshipService(database, policy=_AllowPolicy())
        revision_service = KeyDateRevisionService(database)
        if target_type == "eot_submission":
            parent = await database.key_date_eot_submissions.find_one({"_id": target_id})
            update = revision_service.update_submission(
                parent, EOTSubmissionUpdate(items=[]), _actor()
            )
            child_collection = database.key_date_eot_submission_items
            child_query = {"eot_submission_id": target_id}
            frozen = lambda row: bool(row.get("locked_at"))
        else:
            parent = await database.key_date_eot_determinations.find_one({"_id": target_id})
            update = revision_service.update_determination(
                parent, EOTDeterminationUpdate(items=[]), _actor()
            )
            child_collection = database.key_date_eot_determination_items
            child_query = {"determination_id": target_id}
            frozen = lambda row: bool(row.get("frozen_at"))

        update_result, freeze_result = await asyncio.gather(
            update,
            relationship_service.freeze(
                _actor(), target_type, target_id, reason="update race",
                lifecycle_orchestrated=True,
            ),
            return_exceptions=True,
        )

        collection = (
            database.key_date_eot_submissions
            if target_type == "eot_submission"
            else database.key_date_eot_determinations
        )
        stored = await collection.find_one({"_id": target_id})
        child_count = await child_collection.count_documents(child_query)
        assert not (frozen(stored) and child_count == 0), (
            update_result,
            freeze_result,
            stored,
        )
        if frozen(stored):
            assert child_count == 1
            assert isinstance(update_result, Exception)
        else:
            assert child_count == 0
            assert isinstance(freeze_result, Exception)
    finally:
        await client.drop_database(database.name)
        client.close()


class _PausedFindOneCollection:
    def __init__(self, collection: Any, checked: asyncio.Event, release: asyncio.Event) -> None:
        self.collection = collection
        self.checked = checked
        self.release = release

    def __getattr__(self, name: str) -> Any:
        return getattr(self.collection, name)

    async def find_one(self, *args: Any, **kwargs: Any) -> Any:
        result = await self.collection.find_one(*args, **kwargs)
        self.checked.set()
        await self.release.wait()
        return result


class _DeletionRaceDatabase:
    def __init__(self, database: Any, checked: asyncio.Event, release: asyncio.Event) -> None:
        self._database = database
        self.client = database.client
        self.key_date_eot_determination_items = _PausedFindOneCollection(
            database.key_date_eot_determination_items, checked, release
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._database, name)


@pytest.mark.asyncio
async def test_real_mongo_delete_and_achievement_race_cannot_orphan_event() -> None:
    client, database = await _new_database()
    try:
        await _seed(database)
        await database.key_date_baselines.delete_many({})
        await database.key_date_achievements.delete_many({})
        await database.key_date_eot_submission_items.delete_many({})
        await database.key_date_eot_determination_items.delete_many({})
        milestone = await database.key_date_milestones.find_one({"_id": "kd-1"})
        checked = asyncio.Event()
        release = asyncio.Event()
        deletion_db = _DeletionRaceDatabase(database, checked, release)

        deletion = asyncio.create_task(
            KeyDateService(deletion_db).delete(milestone, _actor())
        )
        await asyncio.wait_for(checked.wait(), timeout=5)
        achievement = asyncio.create_task(
            KeyDateService(database).record_achievement(
                milestone,
                AchievementRecord(
                    actual_achievement_date=datetime(2026, 8, 5, tzinfo=timezone.utc)
                ),
                _actor(),
            )
        )
        await asyncio.sleep(0.1)
        release.set()
        delete_result, achievement_result = await asyncio.gather(
            deletion, achievement, return_exceptions=True
        )

        parent = await database.key_date_milestones.find_one({"_id": "kd-1"})
        event = await database.key_date_achievements.find_one({"_id": "kd-1:ach"})
        assert not (parent is None and event is not None), (
            delete_result,
            achievement_result,
        )
        if parent is None:
            assert isinstance(achievement_result, Exception)
            assert event is None
        else:
            assert isinstance(delete_result, Exception)
            assert event is not None
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_concurrent_lifecycle_retries_emit_one_domain_audit() -> None:
    client, database = await _new_database()
    try:
        await _seed(database)
        locked_at = datetime(2026, 8, 11, tzinfo=timezone.utc)
        await database.key_date_eot_submissions.update_one(
            {"_id": "submission-1"},
            {"$set": {"status": "locked", "locked_at": locked_at}},
        )
        submission = await database.key_date_eot_submissions.find_one(
            {"_id": "submission-1"}
        )
        service = KeyDateRevisionService(database)

        await asyncio.gather(
            service.emit_submission_relationship_lock(submission, _actor()),
            service.emit_submission_relationship_lock(submission, _actor()),
        )

        assert await database.audit_events.count_documents(
            {
                "action": "keydate.eot_submission.locked",
                "resource_id": "submission-1",
                "result": "success",
            }
        ) == 1
        stored = await database.key_date_eot_submissions.find_one(
            {"_id": "submission-1"}
        )
        assert stored.get("relationship_lock_domain_audit_at") is not None
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_delete_and_legacy_draft_eot_race_cannot_orphan_child() -> None:
    client, database = await _new_database()
    try:
        await _seed(database)
        await database.key_date_baselines.delete_many({})
        await database.key_date_achievements.delete_many({})
        await database.key_date_eot_submission_items.delete_many({})
        await database.key_date_eot_determination_items.delete_many({})
        milestone = await database.key_date_milestones.find_one({"_id": "kd-1"})
        checked = asyncio.Event()
        release = asyncio.Event()
        deletion_db = _DeletionRaceDatabase(database, checked, release)

        deletion = asyncio.create_task(
            KeyDateService(deletion_db).delete(milestone, _actor())
        )
        await asyncio.wait_for(checked.wait(), timeout=5)
        draft = asyncio.create_task(
            KeyDateService(database).submit_eot(
                milestone,
                EOTApplicationCreate(requested_extension_days=5, submit=False),
                _actor(),
            )
        )
        await asyncio.sleep(0.1)
        release.set()
        delete_result, draft_result = await asyncio.gather(
            deletion, draft, return_exceptions=True
        )

        parent = await database.key_date_milestones.find_one({"_id": "kd-1"})
        legacy_eot = await database.key_date_eot_applications.find_one(
            {"milestone_id": "kd-1"}
        )
        assert not (parent is None and legacy_eot is not None), (
            delete_result,
            draft_result,
        )
        if parent is None:
            assert isinstance(draft_result, Exception)
            assert legacy_eot is None
        else:
            assert isinstance(delete_result, Exception)
            assert legacy_eot is not None
    finally:
        await client.drop_database(database.name)
        client.close()
