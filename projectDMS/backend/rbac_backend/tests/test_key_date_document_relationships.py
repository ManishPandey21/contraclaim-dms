from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from rbac_backend.core.database import get_db
from rbac_backend.core.permissions import Permissions
from rbac_backend.core.security import get_current_user
from rbac_backend.routers.document_relationships import (
    get_document_relationship_service,
    router as relationship_router,
)
from rbac_backend.routers.key_dates import get_policy, router as key_date_router
from rbac_backend.models.key_date import (
    AchievementRecord,
    EOTApplicationCreate,
    EOTDeterminationItemInput,
    EOTDeterminationUpdate,
    EOTSubmissionItemInput,
    EOTSubmissionUpdate,
)
from rbac_backend.services.document_relationship_service import DocumentRelationshipService
from rbac_backend.services.key_date_service import KeyDateError, KeyDateService
from rbac_backend.services.key_date_revision_service import KeyDateRevisionService
from rbac_backend.tests.selection_fixtures import pin_selection
from rbac_backend.tests.test_claim_document_relationships import _Collection, _Database, _user


class _AllowPolicy:
    async def authorize(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    async def authorize_document(self, *_args: Any, **_kwargs: Any) -> None:
        return None


class _KeyDateCollection(_Collection):
    async def insert_many(self, documents: list[dict[str, Any]], *args: Any, **kwargs: Any):
        inserted_ids = []
        for document in documents:
            result = await self.insert_one(document, *args, **kwargs)
            inserted_ids.append(result.inserted_id)
        return type("InsertManyResult", (), {"inserted_ids": inserted_ids})()

    async def delete_many(self, query: dict[str, Any], *args: Any, **kwargs: Any):
        matches = [row for row in self.documents if all(row.get(k) == v for k, v in query.items())]
        for row in matches:
            self.documents.remove(row)
        return type("DeleteManyResult", (), {"deleted_count": len(matches)})()

    async def replace_one(
        self, query: dict[str, Any], document: dict[str, Any], *args: Any, **kwargs: Any
    ):
        await self.delete_one(query)
        return await self.insert_one(document, *args, **kwargs)


class _KeyDateDatabase(_Database):
    def __init__(self) -> None:
        super().__init__()
        self.key_date_milestones = _Collection(
            "key_date_milestones",
            [
                {
                    "_id": "kd-1",
                    "milestone_ref": "KD-01",
                    "title": "Complete foundations",
                    "contractual_week_number": 4,
                    "original_planned_key_date": datetime(2026, 8, 1),
                    "current_approved_key_date": datetime(2026, 8, 1),
                    "organization_id": "org-1",
                    "project_id": "project-1",
                    "contract_id": "primary",
                    "linked_document_ids": [],
                    "linked_letter_ids": [],
                    "current_revision": 0,
                    "created_at": datetime(2026, 1, 1),
                }
            ],
        )
        self.key_date_achievements = _KeyDateCollection("key_date_achievements")
        self.key_date_eot_applications = _Collection("key_date_eot_applications")
        self.key_date_extension_history = _Collection("key_date_extension_history")
        self.key_date_notifications = _Collection("key_date_notifications")
        self.key_date_baselines = _KeyDateCollection(
            "key_date_baselines",
            [
                {
                    "_id": "baseline-1",
                    "organization_id": "org-1",
                    "project_id": "project-1",
                    "contract_id": "primary",
                    "status": "frozen",
                    "next_revision_number": 0,
                    "items": [
                        {
                            "key_date_id": "kd-1",
                            "milestone_ref": "KD-01",
                            "original_planned_key_date": datetime(2026, 8, 1),
                        }
                    ],
                }
            ],
        )
        self.key_date_eot_submissions = _KeyDateCollection("key_date_eot_submissions")
        self.key_date_eot_submission_items = _KeyDateCollection("key_date_eot_submission_items")
        self.key_date_eot_determinations = _KeyDateCollection("key_date_eot_determinations")
        self.key_date_eot_determination_items = _KeyDateCollection("key_date_eot_determination_items")
        self.projects = _Collection("projects")
        self.contract_master = _Collection("contract_master")
        self.letters = _Collection("letters")


class _RollbackSession:
    def __init__(self, db: _KeyDateDatabase) -> None:
        self.db = db

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args: Any):
        return None

    async def with_transaction(self, callback: Any):
        snapshots = {
            name: deepcopy(value.documents)
            for name, value in vars(self.db).items()
            if hasattr(value, "documents")
        }
        try:
            return await callback(self)
        except Exception:
            for name, documents in snapshots.items():
                getattr(self.db, name).documents = documents
            raise


class _RollbackClient:
    def __init__(self, db: _KeyDateDatabase) -> None:
        self.db = db

    async def start_session(self) -> _RollbackSession:
        return _RollbackSession(self.db)


class _FailingAuditCollection(_Collection):
    async def insert_one(self, *_args: Any, **_kwargs: Any):
        raise RuntimeError("audit write failed")


class _FailingItemCollection(_KeyDateCollection):
    async def insert_many(self, *_args: Any, **_kwargs: Any):
        raise RuntimeError("item insert failed")


class _FailOnceDomainAuditCollection(_Collection):
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        super().__init__("audit_events", documents)
        self.failed = False

    async def insert_one(self, document: dict[str, Any], *args: Any, **kwargs: Any):
        if (
            document.get("action") == "keydate.eot_determination.frozen"
            and not self.failed
        ):
            self.failed = True
            raise RuntimeError("domain audit failed once")
        return await super().insert_one(document, *args, **kwargs)


class _BarrierRevisionService(KeyDateRevisionService):
    def __init__(self, db: Any) -> None:
        super().__init__(db)
        self.audit_reads = 0
        self.audit_read_barrier = asyncio.Event()

    async def _domain_audit_exists(
        self, action: str, resource_id: str, *, session: Any = None
    ) -> bool:
        self.audit_reads += 1
        if self.audit_reads >= 2:
            self.audit_read_barrier.set()
        try:
            await asyncio.wait_for(self.audit_read_barrier.wait(), timeout=0.2)
        except asyncio.TimeoutError:
            pass
        return False


def _app(db: _KeyDateDatabase) -> FastAPI:
    policy = _AllowPolicy()
    app = FastAPI()
    app.include_router(key_date_router, prefix="/api")
    app.include_router(relationship_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_policy] = lambda: policy
    app.dependency_overrides[get_document_relationship_service] = lambda: DocumentRelationshipService(
        db, policy=policy
    )
    # The selection the browser sends for the fixture key date: its own project.
    pin_selection(app, db, "org-1", "project-1")
    return app


def _relationship_app(db: _KeyDateDatabase, policy: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(relationship_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = _user
    app.dependency_overrides[get_document_relationship_service] = lambda: DocumentRelationshipService(
        db, policy=policy
    )
    # The selection the browser sends for the fixture key date: its own project.
    pin_selection(app, db, "org-1", "project-1")
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("document_changes", "expected_status"),
    [
        ({"processing_status": "failed"}, 201),
        ({"processing_status": "human_review_required"}, 409),
        ({"duplicate_status": "duplicate"}, 409),
        ({"lifecycle_state": "duplicate"}, 409),
        ({"lifecycle_state": "deleted"}, 409),
        ({"project_id": None}, 409),
        ({"project_id": "project-2"}, 403),
        ({"organization_id": "org-2"}, 403),
    ],
)
async def test_key_date_event_public_seam_enforces_canonical_document_authority(
    document_changes: dict[str, Any], expected_status: int
) -> None:
    db = _KeyDateDatabase()
    db.documents.documents[0].update(document_changes)
    await db.key_date_achievements.insert_one(
        {
            "_id": "kd-1:ach",
            "milestone_id": "kd-1",
            "organization_id": "org-1",
            "project_id": "project-1",
            "actual_achievement_date": datetime(2026, 8, 3),
        }
    )
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/entities/key_date_achievement/kd-1:ach/document-links:batch",
            json={
                "organization_id": "forged-org",
                "project_id": "forged-project",
                "links": [
                    {
                        "document_id": "doc-1",
                        "relationship_role": "completion_certificate",
                    }
                ],
            },
        )

    assert response.status_code == expected_status, response.text
    if expected_status == 201:
        assert db.entity_document_links.documents[0]["organization_id"] == "org-1"
        assert db.entity_document_links.documents[0]["project_id"] == "project-1"
    else:
        assert db.entity_document_links.documents == []


@pytest.mark.asyncio
async def test_key_date_event_public_seam_denies_missing_document() -> None:
    db = _KeyDateDatabase()
    db.documents.documents.clear()
    await db.key_date_achievements.insert_one(
        {
            "_id": "kd-1:ach",
            "milestone_id": "kd-1",
            "organization_id": "org-1",
            "project_id": "project-1",
        }
    )
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/entities/key_date_achievement/kd-1:ach/document-links:batch",
            json={"links": [{"document_id": "missing", "relationship_role": "supporting_document"}]},
        )

    assert response.status_code == 404, response.text
    assert db.entity_document_links.documents == []


class _PermissionPolicy(_AllowPolicy):
    def __init__(self, denied: str) -> None:
        self.denied = denied

    async def authorize_document(
        self, _actor: Any, permission: str, _resource: Any, **_kwargs: Any
    ) -> None:
        if permission == self.denied:
            raise HTTPException(status_code=403, detail="denied")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "denied_permission",
    [Permissions.KEYDATE_ACHIEVEMENT, Permissions.DOCUMENT_VIEW],
)
async def test_key_date_event_requires_target_and_document_permissions(
    denied_permission: str,
) -> None:
    db = _KeyDateDatabase()
    await db.key_date_achievements.insert_one(
        {
            "_id": "kd-1:ach",
            "milestone_id": "kd-1",
            "organization_id": "org-1",
            "project_id": "project-1",
        }
    )
    policy = _PermissionPolicy(denied_permission)
    transport = httpx.ASGITransport(app=_relationship_app(db, policy))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/entities/key_date_achievement/kd-1:ach/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "supporting_document"}]},
        )

    assert response.status_code == 403, response.text
    assert db.entity_document_links.documents == []


@pytest.mark.asyncio
async def test_forged_achievement_scope_fails_closed_against_canonical_parent() -> None:
    db = _KeyDateDatabase()
    await db.key_date_achievements.insert_one(
        {
            "_id": "kd-1:ach",
            "milestone_id": "kd-1",
            "organization_id": "org-1",
            "project_id": "project-foreign",
        }
    )
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/entities/key_date_achievement/kd-1:ach/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "supporting_document"}]},
        )

    assert response.status_code == 404, response.text
    assert db.entity_document_links.documents == []


@pytest.mark.asyncio
async def test_multi_supporter_and_multi_event_relationships_remain_distinct() -> None:
    db = _KeyDateDatabase()
    db.documents.documents.append(
        {
            **db.documents.documents[0],
            "_id": "doc-2",
            "filename": "Inspection.pdf",
            "current_version_id": "version-2",
        }
    )
    db.document_versions.documents.append(
        {
            "_id": "version-2",
            "document_id": "doc-2",
            "version_number": 1,
            "is_current": True,
            "file_object_id": "file-v2",
        }
    )
    await db.key_date_achievements.insert_one(
        {
            "_id": "kd-1:ach",
            "milestone_id": "kd-1",
            "organization_id": "org-1",
            "project_id": "project-1",
        }
    )
    await db.key_date_eot_submissions.insert_one(
        {
            "_id": "submission-distinct",
            "organization_id": "org-1",
            "project_id": "project-1",
            "contract_id": "primary",
            "revision_number": 1,
            "revision_label": "EOT-1",
            "status": "submitted",
            "locked_at": None,
        }
    )
    await db.key_date_eot_submission_items.insert_one(
        {
            "_id": "submission-distinct:item",
            "eot_submission_id": "submission-distinct",
            "key_date_id": "kd-1",
            "milestone_ref": "KD-01",
        }
    )
    await db.key_date_eot_determinations.insert_one(
        {
            "_id": "determination-distinct",
            "organization_id": "org-1",
            "project_id": "project-1",
            "contract_id": "primary",
            "origin": "contractor_submission",
            "eot_submission_ids": ["submission-distinct"],
            "determination_reference": "ENG/DET-DISTINCT",
            "status": "under_review",
            "frozen_at": None,
        }
    )
    await db.key_date_eot_determination_items.insert_one(
        {
            "_id": "determination-distinct:item",
            "determination_id": "determination-distinct",
            "key_date_id": "kd-1",
            "milestone_ref": "KD-01",
            "determination_result": "pending",
        }
    )
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        achievement = await client.post(
            "/api/entities/key_date_achievement/kd-1:ach/document-links:batch",
            json={"links": [
                {"document_id": "doc-1", "relationship_role": "completion_certificate"},
                {"document_id": "doc-2", "relationship_role": "inspection_record"},
            ]},
        )
        submission = await client.post(
            "/api/entities/eot_submission/submission-distinct/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "eot_submission"}]},
        )
        determination = await client.post(
            "/api/entities/eot_determination/determination-distinct/document-links:batch",
            json={"links": [
                {"document_id": "doc-1", "relationship_role": "engineer_determination"}
            ]},
        )
        achievement_doc_1 = next(
            row for row in achievement.json()["links"] if row["document_id"] == "doc-1"
        )
        removed = await client.post(
            f"/api/document-links/{achievement_doc_1['_id']}:remove",
            json={"reason": "superseded certificate", "expected_revision": 1},
        )
        remaining_achievement = await client.get(
            "/api/entities/key_date_achievement/kd-1:ach/document-links"
        )

    assert achievement.status_code == 201, achievement.text
    assert submission.status_code == 201, submission.text
    assert determination.status_code == 201, determination.text
    assert removed.status_code == 200, removed.text
    assert [
        row["document_id"] for row in remaining_achievement.json()["links"]
    ] == ["doc-2"]
    active = [row for row in db.entity_document_links.documents if not row.get("removed_at")]
    assert {(row["target_type"], row["target_id"], row["document_id"]) for row in active} == {
        ("key_date_achievement", "kd-1:ach", "doc-2"),
        ("eot_submission", "submission-distinct", "doc-1"),
        ("eot_determination", "determination-distinct", "doc-1"),
    }


@pytest.mark.asyncio
async def test_achievement_parent_event_and_audit_roll_back_together() -> None:
    db = _KeyDateDatabase()
    db.client = _RollbackClient(db)
    db.audit_events = _FailingAuditCollection("audit_events")
    before = deepcopy(db.key_date_milestones.documents[0])

    with pytest.raises(RuntimeError, match="audit write failed"):
        await KeyDateService(db).record_achievement(
            before,
            AchievementRecord(actual_achievement_date=datetime(2026, 8, 3)),
            _user(),
        )

    assert db.key_date_milestones.documents[0] == before
    assert db.key_date_achievements.documents == []
    assert db.audit_events.documents == []


@pytest.mark.asyncio
async def test_parent_delete_cannot_orphan_achievement_event_evidence() -> None:
    db = _KeyDateDatabase()
    db.key_date_baselines.documents.clear()
    await db.key_date_achievements.insert_one(
        {
            "_id": "kd-1:ach",
            "milestone_id": "kd-1",
            "organization_id": "org-1",
            "project_id": "project-1",
            "actual_achievement_date": datetime(2026, 8, 3),
        }
    )
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        linked = await client.post(
            "/api/entities/key_date_achievement/kd-1:ach/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "completion_certificate"}]},
        )
        deleted = await client.delete("/api/key-dates/kd-1")

    assert linked.status_code == 201, linked.text
    assert deleted.status_code == 409, deleted.text
    assert len(db.key_date_milestones.documents) == 1
    assert len(db.key_date_achievements.documents) == 1
    assert len(db.entity_document_links.documents) == 1


@pytest.mark.asyncio
async def test_parent_delete_preserves_redacted_legacy_evidence_for_manual_review() -> None:
    db = _KeyDateDatabase()
    db.key_date_baselines.documents.clear()
    db.key_date_milestones.documents[0]["linked_document_ids"] = ["doc-1"]
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        loaded = await client.get("/api/key-dates/kd-1")
        deleted = await client.delete("/api/key-dates/kd-1")

    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["linked_document_ids"] == []
    assert deleted.status_code == 409, deleted.text
    assert db.key_date_milestones.documents[0]["linked_document_ids"] == ["doc-1"]


@pytest.mark.asyncio
async def test_submission_freeze_requires_specialized_lock_permission() -> None:
    db = _KeyDateDatabase()
    await db.key_date_eot_submissions.insert_one(
        {
            "_id": "submission-permission",
            "organization_id": "org-1",
            "project_id": "project-1",
            "contract_id": "primary",
            "revision_number": 1,
            "revision_label": "EOT-1",
            "status": "submitted",
            "contractor_submission_date": datetime(2026, 8, 10),
            "contractor_letter_reference": "CON/EOT-1",
            "locked_at": None,
        }
    )
    await db.key_date_eot_submission_items.insert_one(
        {
            "_id": "submission-permission:item",
            "eot_submission_id": "submission-permission",
            "key_date_id": "kd-1",
            "milestone_ref": "KD-01",
        }
    )
    policy = _PermissionPolicy(Permissions.KEYDATE_EOT_LOCK_SUBMISSION)
    transport = httpx.ASGITransport(app=_relationship_app(db, policy))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/entities/eot_submission/submission-permission/document-links:freeze",
            json={"reason": "submit"},
        )

    assert response.status_code == 403, response.text
    assert db.key_date_eot_submissions.documents[0]["locked_at"] is None


@pytest.mark.asyncio
async def test_achievement_event_owns_document_relationship_forward_and_reverse() -> None:
    db = _KeyDateDatabase()
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        achieved = await client.post(
            "/api/key-dates/kd-1/achievement",
            json={
                "actual_achievement_date": "2026-08-03T00:00:00Z",
                "client_notification_required": True,
                "client_notification_ref": "CON/KD-01/ACH",
                "linked_document_ids": [],
            },
        )
        linked = await client.post(
            "/api/entities/key_date_achievement/kd-1:ach/document-links:batch",
            json={
                "links": [
                    {
                        "document_id": "doc-1",
                        "relationship_role": "contractor_notification",
                    }
                ]
            },
        )
        forward = await client.get(
            "/api/entities/key_date_achievement/kd-1:ach/document-links"
        )
        reverse = await client.get("/api/documents/doc-1/entity-links")

    assert achieved.status_code == 200, achieved.text
    assert linked.status_code == 201, linked.text
    assert forward.status_code == 200, forward.text
    assert reverse.status_code == 200, reverse.text
    peer = forward.json()["links"][0]
    reverse_peer = reverse.json()["links"][0]
    assert peer["target_type"] == "key_date_achievement"
    assert peer["target_id"] == "kd-1:ach"
    assert peer["parent_type"] == "key_date"
    assert peer["parent_id"] == "kd-1"
    assert peer["relationship_role"] == "contractor_notification"
    assert reverse_peer["_id"] == peer["_id"]
    assert reverse_peer["target_label"] == "KD-01 · Achievement"


@pytest.mark.asyncio
async def test_achievement_rejects_nonempty_legacy_document_intent_before_mutation() -> None:
    db = _KeyDateDatabase()
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/key-dates/kd-1/achievement",
            json={
                "actual_achievement_date": "2026-08-03T00:00:00Z",
                "linked_document_ids": ["doc-1"],
            },
        )

    assert response.status_code == 409, response.text
    assert db.key_date_achievements.documents == []
    assert "actual_achievement_date" not in db.key_date_milestones.documents[0]


@pytest.mark.asyncio
async def test_key_date_parent_create_and_update_legacy_document_writes_require_manual_review() -> None:
    db = _KeyDateDatabase()
    db.key_date_baselines.documents.clear()
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/key-dates",
            json={
                "title": "Ambiguous parent evidence",
                "project_id": "project-1",
                "organization_id": "org-1",
                "contractual_week_number": 5,
                "project_start_date": "2026-07-01T00:00:00Z",
                "linked_document_ids": ["doc-1"],
            },
        )
        updated = await client.put(
            "/api/key-dates/kd-1",
            json={"linked_document_ids": ["doc-1"]},
        )

    assert created.status_code == 409, created.text
    assert updated.status_code == 409, updated.text
    assert len(db.key_date_milestones.documents) == 1
    assert db.key_date_milestones.documents[0]["linked_document_ids"] == []


@pytest.mark.asyncio
async def test_eot_submission_owns_evidence_without_collapsing_to_key_date() -> None:
    db = _KeyDateDatabase()
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/key-dates/eot-submissions",
            json={
                "organization_id": "org-1",
                "project_id": "project-1",
                "contract_id": "primary",
                "contractor_submission_date": "2026-08-10T00:00:00Z",
                "contractor_letter_reference": "CON/EOT-1",
                "status": "submitted",
                "linked_document_ids": [],
                "items": [
                    {
                        "milestone_ref": "KD-01",
                        "eot_submitted_date": "2026-09-01T00:00:00Z",
                        "claimed_extension_days": 31,
                    }
                ],
            },
        )
        assert created.status_code == 201, created.text
        submission_id = created.json()["_id"]
        linked = await client.post(
            f"/api/entities/eot_submission/{submission_id}/document-links:batch",
            json={
                "links": [
                    {
                        "document_id": "doc-1",
                        "relationship_role": "eot_submission",
                    }
                ]
            },
        )
        bad_role = await client.post(
            f"/api/entities/eot_submission/{submission_id}/document-links:batch",
            json={
                "links": [
                    {
                        "document_id": "doc-1",
                        "relationship_role": "engineer_determination",
                    }
                ]
            },
        )
        reverse = await client.get("/api/documents/doc-1/entity-links")

    assert linked.status_code == 201, linked.text
    assert bad_role.status_code == 422, bad_role.text
    peer = reverse.json()["links"][0]
    assert peer["target_type"] == "eot_submission"
    assert peer["target_id"] == submission_id
    assert peer["parent_type"] == "project"
    assert peer["parent_id"] == "project-1"
    assert peer["target_label"] == "EOT-1 · Contractor Submission"


@pytest.mark.asyncio
async def test_eot_submission_rejects_legacy_array_create_and_replacement_intent() -> None:
    db = _KeyDateDatabase()
    transport = httpx.ASGITransport(app=_app(db))
    base_payload = {
        "organization_id": "org-1",
        "project_id": "project-1",
        "contract_id": "primary",
        "status": "draft",
        "items": [],
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        rejected_create = await client.post(
            "/api/key-dates/eot-submissions",
            json={**base_payload, "linked_document_ids": ["doc-1"]},
        )
        created = await client.post(
            "/api/key-dates/eot-submissions",
            json={**base_payload, "linked_document_ids": []},
        )
        assert created.status_code == 201, created.text
        rejected_update = await client.put(
            f"/api/key-dates/eot-submissions/{created.json()['_id']}",
            json={"linked_document_ids": ["doc-1"]},
        )

    assert rejected_create.status_code == 409, rejected_create.text
    assert rejected_update.status_code == 409, rejected_update.text
    assert "manual review" in rejected_create.text.lower()
    assert "manual review" in rejected_update.text.lower()
    assert len(db.key_date_eot_submissions.documents) == 1
    assert "linked_document_ids" not in db.key_date_eot_submissions.documents[0]


@pytest.mark.asyncio
async def test_submission_lock_freezes_and_version_pins_relationships() -> None:
    db = _KeyDateDatabase()
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/key-dates/eot-submissions",
            json={
                "organization_id": "org-1",
                "project_id": "project-1",
                "contract_id": "primary",
                "contractor_submission_date": "2026-08-10T00:00:00Z",
                "contractor_letter_reference": "CON/EOT-LOCK",
                "status": "submitted",
                "items": [
                    {
                        "milestone_ref": "KD-01",
                        "eot_submitted_date": "2026-09-01T00:00:00Z",
                        "claimed_extension_days": 31,
                    }
                ],
            },
        )
        submission_id = created.json()["_id"]
        linked = await client.post(
            f"/api/entities/eot_submission/{submission_id}/document-links:batch",
            json={
                "links": [
                    {
                        "document_id": "doc-1",
                        "relationship_role": "eot_submission",
                    }
                ]
            },
        )
        link = linked.json()["links"][0]
        generic_freeze = await client.post(
            f"/api/entities/eot_submission/{submission_id}/document-links:freeze",
            json={"reason": "must use submission lifecycle"},
        )
        locked = await client.post(
            f"/api/key-dates/eot-submissions/{submission_id}/lock"
        )
        locked_again = await client.post(
            f"/api/key-dates/eot-submissions/{submission_id}/lock"
        )
        db.documents.documents[0]["current_version_id"] = "version-2"
        db.document_versions.documents.append(
            {
                "_id": "version-2",
                "document_id": "doc-1",
                "version_number": 2,
                "is_current": True,
                "file_object_id": "file-v2",
            }
        )
        historical = await client.get(
            f"/api/entities/eot_submission/{submission_id}/document-links"
        )
        late_link = await client.post(
            f"/api/entities/eot_submission/{submission_id}/document-links:batch",
            json={
                "links": [
                    {
                        "document_id": "doc-1",
                        "relationship_role": "supporting_document",
                    }
                ]
            },
        )
        unlink = await client.post(
            f"/api/document-links/{link['_id']}:remove",
            json={"reason": "late rewrite", "expected_revision": 2},
        )

    assert created.status_code == 201, created.text
    assert linked.status_code == 201, linked.text
    assert generic_freeze.status_code == 409, generic_freeze.text
    assert locked.status_code == 200, locked.text
    assert locked_again.status_code == 200, locked_again.text
    assert historical.status_code == 200, historical.text
    assert historical.json()["links"][0]["document"]["resolved_version_id"] == "version-1"
    assert historical.json()["links"][0]["document"]["file_object_id"] == "file-v1"
    assert locked.json()["status"] == "locked"
    stored = next(row for row in db.entity_document_links.documents if row["_id"] == link["_id"])
    assert stored["document_version_id"] == "version-1"
    assert stored["frozen_at"] is not None
    assert stored["_revision"] == 2
    assert late_link.status_code == 409, late_link.text
    assert unlink.status_code == 409, unlink.text
    assert any(
        row.get("action") == "document_relationships.frozen"
        for row in db.audit_events.documents
    )
    assert sum(
        row.get("action") == "keydate.eot_submission.locked"
        for row in db.audit_events.documents
    ) == 1


@pytest.mark.asyncio
async def test_eot_determination_owns_distinct_evidence_and_role_vocabulary() -> None:
    db = _KeyDateDatabase()
    await db.key_date_eot_submissions.insert_one(
        {
            "_id": "submission-1",
            "organization_id": "org-1",
            "project_id": "project-1",
            "contract_id": "primary",
            "revision_number": 1,
            "revision_label": "EOT-1",
            "status": "locked",
            "locked_at": datetime(2026, 8, 11),
            "contractor_submission_date": datetime(2026, 8, 10),
            "contractor_letter_reference": "CON/EOT-1",
            "created_at": datetime(2026, 8, 10),
            "items_count": 1,
        }
    )
    await db.key_date_eot_submission_items.insert_one(
        {
            "_id": "submission-item-1",
            "eot_submission_id": "submission-1",
            "key_date_id": "kd-1",
            "milestone_ref": "KD-01",
            "eot_submitted_date": datetime(2026, 9, 1),
            "claimed_extension_days": 31,
        }
    )
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/key-dates/eot-determinations",
            json={
                "organization_id": "org-1",
                "project_id": "project-1",
                "contract_id": "primary",
                "eot_submission_ids": ["submission-1"],
                "determination_reference": "ENG/DET-1",
                "determination_date": "2026-08-20T00:00:00Z",
                "status": "rejected",
                "linked_document_ids": [],
                "items": [
                    {
                        "milestone_ref": "KD-01",
                        "determination_result": "rejected",
                        "source_submission_id": "submission-1",
                    }
                ],
            },
        )
        assert created.status_code == 201, created.text
        determination_id = created.json()["_id"]
        linked = await client.post(
            f"/api/entities/eot_determination/{determination_id}/document-links:batch",
            json={
                "links": [
                    {
                        "document_id": "doc-1",
                        "relationship_role": "engineer_determination",
                    }
                ]
            },
        )
        incoherent = await client.post(
            f"/api/entities/eot_determination/{determination_id}/document-links:batch",
            json={
                "links": [
                    {
                        "document_id": "doc-1",
                        "relationship_role": "contractor_notification",
                    }
                ]
            },
        )
        reverse = await client.get("/api/documents/doc-1/entity-links")

    assert linked.status_code == 201, linked.text
    assert incoherent.status_code == 422, incoherent.text
    peer = reverse.json()["links"][0]
    assert peer["target_type"] == "eot_determination"
    assert peer["target_id"] == determination_id
    assert peer["parent_type"] == "project"
    assert peer["parent_id"] == "project-1"
    assert peer["target_label"] == "ENG/DET-1 · Determination"


@pytest.mark.asyncio
async def test_eot_determination_rejects_legacy_array_create_and_replacement_intent() -> None:
    db = _KeyDateDatabase()
    base_payload = {
        "organization_id": "org-1",
        "project_id": "project-1",
        "contract_id": "primary",
        "origin": "employer_initiated",
        "remarks": "Employer determination",
        "status": "under_review",
        "items": [],
    }
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        rejected_create = await client.post(
            "/api/key-dates/eot-determinations",
            json={**base_payload, "linked_document_ids": ["doc-1"]},
        )
        created = await client.post(
            "/api/key-dates/eot-determinations",
            json={**base_payload, "linked_document_ids": []},
        )
        assert created.status_code == 201, created.text
        rejected_update = await client.put(
            f"/api/key-dates/eot-determinations/{created.json()['_id']}",
            json={"linked_document_ids": ["doc-1"]},
        )

    assert rejected_create.status_code == 409, rejected_create.text
    assert rejected_update.status_code == 409, rejected_update.text
    assert "manual review" in rejected_create.text.lower()
    assert "manual review" in rejected_update.text.lower()
    assert len(db.key_date_eot_determinations.documents) == 1
    assert "linked_document_ids" not in db.key_date_eot_determinations.documents[0]


@pytest.mark.asyncio
async def test_determination_freeze_version_pins_its_own_relationships() -> None:
    db = _KeyDateDatabase()
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/key-dates/eot-determinations",
            json={
                "organization_id": "org-1",
                "project_id": "project-1",
                "contract_id": "primary",
                "origin": "employer_initiated",
                "determination_reference": "ENG/DET-FREEZE",
                "determination_date": "2026-08-20T00:00:00Z",
                "remarks": "Employer determination",
                "status": "rejected",
                "items": [
                    {
                        "milestone_ref": "KD-01",
                        "determination_result": "rejected",
                    }
                ],
            },
        )
        determination_id = created.json()["_id"]
        linked = await client.post(
            f"/api/entities/eot_determination/{determination_id}/document-links:batch",
            json={
                "links": [
                    {
                        "document_id": "doc-1",
                        "relationship_role": "engineer_determination",
                    }
                ]
            },
        )
        generic_freeze = await client.post(
            f"/api/entities/eot_determination/{determination_id}/document-links:freeze",
            json={"reason": "must use determination lifecycle"},
        )
        frozen = await client.post(
            f"/api/key-dates/eot-determinations/{determination_id}/freeze"
        )
        frozen_again = await client.post(
            f"/api/key-dates/eot-determinations/{determination_id}/freeze"
        )
        late_link = await client.post(
            f"/api/entities/eot_determination/{determination_id}/document-links:batch",
            json={
                "links": [
                    {
                        "document_id": "doc-1",
                        "relationship_role": "supporting_document",
                    }
                ]
            },
        )

    assert created.status_code == 201, created.text
    assert linked.status_code == 201, linked.text
    assert generic_freeze.status_code == 409, generic_freeze.text
    assert frozen.status_code == 200, frozen.text
    assert frozen_again.status_code == 200, frozen_again.text
    assert frozen.json()["frozen_at"] is not None
    stored = next(
        row
        for row in db.entity_document_links.documents
        if row["target_id"] == determination_id
    )
    assert stored["document_version_id"] == "version-1"
    assert stored["frozen_at"] is not None
    assert stored["_revision"] == 2
    assert late_link.status_code == 409, late_link.text
    assert any(
        row.get("action") == "document_relationships.frozen"
        and row.get("resource_id") == determination_id
        for row in db.audit_events.documents
    )
    assert sum(
        row.get("action") == "keydate.eot_determination.frozen"
        for row in db.audit_events.documents
    ) == 1


@pytest.mark.asyncio
async def test_determination_freeze_retry_converges_after_domain_audit_failure() -> None:
    db = _KeyDateDatabase()
    transport = httpx.ASGITransport(app=_app(db), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/key-dates/eot-determinations",
            json={
                "organization_id": "org-1",
                "project_id": "project-1",
                "contract_id": "primary",
                "origin": "employer_initiated",
                "determination_reference": "ENG/DET-RETRY",
                "determination_date": "2026-08-20T00:00:00Z",
                "approval_grant_reference": "ENG/GRANT-RETRY",
                "remarks": "Employer determination",
                "status": "granted",
                "items": [
                    {
                        "milestone_ref": "KD-01",
                        "determination_result": "granted",
                        "eot_granted_date": "2026-09-15T00:00:00Z",
                    }
                ],
            },
        )
        determination_id = created.json()["_id"]
        db.audit_events = _FailOnceDomainAuditCollection(db.audit_events.documents)
        first = await client.post(
            f"/api/key-dates/eot-determinations/{determination_id}/freeze"
        )
        retried = await client.post(
            f"/api/key-dates/eot-determinations/{determination_id}/freeze"
        )

    assert first.status_code == 500, first.text
    assert retried.status_code == 200, retried.text
    assert retried.json()["frozen_at"] is not None
    assert db.key_date_milestones.documents[0]["current_approved_key_date"] == datetime(
        2026, 9, 15, tzinfo=db.key_date_milestones.documents[0]["current_approved_key_date"].tzinfo
    )
    assert sum(
        row.get("action") == "keydate.eot_determination.frozen"
        for row in db.audit_events.documents
    ) == 1


@pytest.mark.asyncio
async def test_record_achievement_preserves_legacy_discovery_and_relationship_revision() -> None:
    db = _KeyDateDatabase()
    db.key_date_achievements.documents.append(
        {
            "_id": "kd-1:ach",
            "milestone_id": "kd-1",
            "organization_id": "org-1",
            "project_id": "project-1",
            "linked_document_ids": ["doc-legacy"],
            "document_relationship_revision": 7,
            "created_at": datetime(2026, 7, 1),
        }
    )

    await KeyDateService(db).record_achievement(
        db.key_date_milestones.documents[0],
        AchievementRecord(actual_achievement_date=datetime(2026, 8, 3)),
        _user(),
    )

    stored = db.key_date_achievements.documents[0]
    assert stored["linked_document_ids"] == ["doc-legacy"]
    assert stored["document_relationship_revision"] == 7
    assert stored["created_at"] == datetime(2026, 7, 1)


@pytest.mark.asyncio
async def test_incomplete_eot_lifecycle_freezes_fail_closed_as_conflicts() -> None:
    db = _KeyDateDatabase()
    await db.key_date_eot_submissions.insert_one(
        {
            "_id": "submission-incomplete",
            "organization_id": "org-1",
            "project_id": "project-1",
            "contract_id": "primary",
            "revision_number": 1,
            "revision_label": "EOT-1",
            "status": "submitted",
            "contractor_submission_date": datetime(2026, 8, 10),
            "contractor_letter_reference": "CON/EOT-1",
            "locked_at": None,
        }
    )
    await db.key_date_eot_determinations.insert_one(
        {
            "_id": "determination-incomplete",
            "organization_id": "org-1",
            "project_id": "project-1",
            "contract_id": "primary",
            "origin": "employer_initiated",
            "determination_reference": "ENG/DET-1",
            "determination_date": datetime(2026, 8, 20),
            "status": "under_review",
            "frozen_at": None,
        }
    )

    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        submission = await client.post(
            "/api/key-dates/eot-submissions/submission-incomplete/lock"
        )
        determination = await client.post(
            "/api/key-dates/eot-determinations/determination-incomplete/freeze"
        )

    assert submission.status_code == 409, submission.text
    assert determination.status_code == 409, determination.text


@pytest.mark.asyncio
async def test_submission_item_replacement_rolls_back_parent_and_children_together() -> None:
    db = _KeyDateDatabase()
    db.client = _RollbackClient(db)
    submission = {
        "_id": "submission-rollback",
        "organization_id": "org-1",
        "project_id": "project-1",
        "contract_id": "primary",
        "revision_number": 1,
        "revision_label": "EOT-1",
        "status": "draft",
        "remarks": "original",
        "locked_at": None,
    }
    await db.key_date_eot_submissions.insert_one(submission)
    db.key_date_eot_submission_items = _FailingItemCollection(
        "key_date_eot_submission_items",
        [
            {
                "_id": "old-item",
                "eot_submission_id": "submission-rollback",
                "key_date_id": "kd-1",
                "milestone_ref": "KD-01",
                "eot_submitted_date": datetime(2026, 9, 1),
            }
        ],
    )

    with pytest.raises(RuntimeError, match="item insert failed"):
        await KeyDateRevisionService(db).update_submission(
            submission,
            EOTSubmissionUpdate(
                remarks="changed",
                items=[
                    EOTSubmissionItemInput(
                        milestone_ref="KD-01",
                        eot_submitted_date=datetime(2026, 9, 2),
                    )
                ],
            ),
            _user(),
        )

    stored = await db.key_date_eot_submissions.find_one({"_id": "submission-rollback"})
    assert stored["remarks"] == "original"
    assert [row["_id"] for row in db.key_date_eot_submission_items.documents] == ["old-item"]


@pytest.mark.asyncio
async def test_determination_item_replacement_rolls_back_parent_and_children_together() -> None:
    db = _KeyDateDatabase()
    db.client = _RollbackClient(db)
    determination = {
        "_id": "determination-rollback",
        "organization_id": "org-1",
        "project_id": "project-1",
        "contract_id": "primary",
        "origin": "employer_initiated",
        "eot_submission_ids": [],
        "status": "under_review",
        "remarks": "original",
        "frozen_at": None,
    }
    await db.key_date_eot_determinations.insert_one(determination)
    db.key_date_eot_determination_items = _FailingItemCollection(
        "key_date_eot_determination_items",
        [
            {
                "_id": "old-determination-item",
                "determination_id": "determination-rollback",
                "key_date_id": "kd-1",
                "milestone_ref": "KD-01",
                "determination_result": "rejected",
            }
        ],
    )

    with pytest.raises(RuntimeError, match="item insert failed"):
        await KeyDateRevisionService(db).update_determination(
            determination,
            EOTDeterminationUpdate(
                remarks="changed",
                items=[
                    EOTDeterminationItemInput(
                        milestone_ref="KD-01",
                        determination_result="rejected",
                    )
                ],
            ),
            _user(),
        )

    stored = await db.key_date_eot_determinations.find_one(
        {"_id": "determination-rollback"}
    )
    assert stored["remarks"] == "original"
    assert [row["_id"] for row in db.key_date_eot_determination_items.documents] == [
        "old-determination-item"
    ]


@pytest.mark.asyncio
async def test_key_date_delete_and_audit_roll_back_together() -> None:
    db = _KeyDateDatabase()
    db.key_date_baselines.documents.clear()
    db.audit_events = _FailingAuditCollection("audit_events")
    db.client = _RollbackClient(db)

    with pytest.raises(RuntimeError, match="audit write failed"):
        await KeyDateService(db).delete(
            db.key_date_milestones.documents[0], _user()
        )

    assert len(db.key_date_milestones.documents) == 1
    assert db.key_date_milestones.documents[0].get("deleting_at") is None


@pytest.mark.asyncio
async def test_legacy_eot_reads_include_inherited_scope_but_omit_explicit_mismatches() -> None:
    db = _KeyDateDatabase()
    common = {
        "milestone_id": "kd-1",
        "project_id": "project-1",
        "organization_id": "org-1",
        "application_date": datetime(2026, 7, 1),
        "requested_extension_days": 5,
        "status": "submitted",
        "created_at": datetime(2026, 7, 1),
    }
    await db.key_date_eot_applications.insert_one(
        {"_id": "legacy-local", **common, "linked_document_ids": ["doc-1"]}
    )
    inherited = {"_id": "legacy-inherited", **common}
    inherited.pop("organization_id")
    inherited.pop("project_id")
    await db.key_date_eot_applications.insert_one(inherited)
    await db.key_date_eot_applications.insert_one(
        {"_id": "legacy-foreign", **common, "project_id": "project-foreign"}
    )
    history_common = {
        "milestone_id": "kd-1",
        "revision_number": 1,
        "original_key_date": datetime(2026, 8, 1),
        "status": "approved",
        "created_at": datetime(2026, 7, 2),
    }
    await db.key_date_extension_history.insert_one(
        {
            "_id": "history-inherited",
            **history_common,
        }
    )
    await db.key_date_extension_history.insert_one(
        {
            "_id": "history-foreign",
            **history_common,
            "organization_id": "org-foreign",
            "project_id": "project-1",
        }
    )
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        eots = await client.get("/api/key-dates/kd-1/eots")
        history = await client.get("/api/key-dates/kd-1/history")

    assert eots.status_code == 200, eots.text
    assert history.status_code == 200, history.text
    assert [row["_id"] for row in eots.json()] == [
        "legacy-local",
        "legacy-inherited",
    ]
    assert all(row["linked_document_ids"] == [] for row in eots.json())
    assert [row["_id"] for row in history.json()] == ["history-inherited"]


@pytest.mark.asyncio
async def test_native_eot_reads_do_not_treat_stored_legacy_arrays_as_authority() -> None:
    db = _KeyDateDatabase()
    await db.key_date_eot_submissions.insert_one(
        {
            "_id": "legacy-submission-array",
            "organization_id": "org-1",
            "project_id": "project-1",
            "contract_id": "primary",
            "revision_number": 1,
            "revision_label": "EOT-1",
            "status": "draft",
            "linked_document_ids": ["doc-1"],
            "created_at": datetime(2026, 8, 10),
        }
    )
    await db.key_date_eot_determinations.insert_one(
        {
            "_id": "legacy-determination-array",
            "organization_id": "org-1",
            "project_id": "project-1",
            "contract_id": "primary",
            "origin": "employer_initiated",
            "eot_submission_ids": [],
            "status": "under_review",
            "linked_document_ids": ["doc-1"],
            "created_at": datetime(2026, 8, 11),
        }
    )
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        workflow = await client.get(
            "/api/key-dates/workflow", params={"project_id": "project-1"}
        )

    assert workflow.status_code == 200, workflow.text
    assert workflow.json()["submissions"][0]["linked_document_ids"] == []
    assert workflow.json()["determinations"][0]["linked_document_ids"] == []
    assert db.key_date_eot_submissions.documents[0]["linked_document_ids"] == ["doc-1"]
    assert db.key_date_eot_determinations.documents[0]["linked_document_ids"] == ["doc-1"]


@pytest.mark.asyncio
async def test_legacy_eot_nonempty_document_writes_are_manual_review_only() -> None:
    db = _KeyDateDatabase()
    await db.key_date_eot_applications.insert_one(
        {
            "_id": "legacy-eot-1",
            "milestone_id": "kd-1",
            "organization_id": "org-1",
            "project_id": "project-1",
            "requested_extension_days": 5,
            "status": "submitted",
            "created_at": datetime(2026, 7, 1),
        }
    )
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        submitted = await client.post(
            "/api/key-dates/kd-1/eot",
            json={
                "requested_extension_days": 5,
                "eot_letter_reference": "CON/LEGACY-EOT",
                "linked_document_ids": ["doc-1"],
            },
        )
        reviewed = await client.post(
            "/api/key-dates/kd-1/eot/legacy-eot-1/review",
            json={
                "decision": "under_review",
                "linked_document_ids": ["doc-1"],
            },
        )

    assert submitted.status_code == 409, submitted.text
    assert reviewed.status_code == 409, reviewed.text
    assert len(db.key_date_eot_applications.documents) == 1
    assert "linked_document_ids" not in db.key_date_eot_applications.documents[0]


@pytest.mark.asyncio
async def test_frozen_new_baseline_blocks_all_legacy_eot_mutation_even_without_documents() -> None:
    db = _KeyDateDatabase()
    await db.key_date_eot_applications.insert_one(
        {
            "_id": "legacy-eot-frozen",
            "milestone_id": "kd-1",
            "organization_id": "org-1",
            "project_id": "project-1",
            "contract_id": "primary",
            "requested_extension_days": 5,
            "status": "submitted",
            "created_at": datetime(2026, 7, 1),
        }
    )
    transport = httpx.ASGITransport(app=_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        submitted = await client.post(
            "/api/key-dates/kd-1/eot",
            json={
                "requested_extension_days": 5,
                "eot_letter_reference": "CON/LEGACY-LATE",
                "linked_document_ids": [],
            },
        )
        reviewed = await client.post(
            "/api/key-dates/kd-1/eot/legacy-eot-frozen/review",
            json={"decision": "under_review", "linked_document_ids": []},
        )

    assert submitted.status_code == 400, submitted.text
    assert reviewed.status_code == 400, reviewed.text
    assert db.key_date_eot_applications.documents[0]["status"] == "submitted"


@pytest.mark.asyncio
async def test_concurrent_submission_lock_retries_emit_one_domain_audit() -> None:
    db = _KeyDateDatabase()
    submission = {
        "_id": "submission-audit-race",
        "organization_id": "org-1",
        "project_id": "project-1",
        "contract_id": "primary",
        "revision_number": 1,
        "revision_label": "EOT-1",
        "status": "locked",
        "contractor_submission_date": datetime(2026, 8, 10),
        "contractor_letter_reference": "CON/EOT-1",
        "locked_at": datetime(2026, 8, 11),
    }
    await db.key_date_eot_submissions.insert_one(submission)
    service = _BarrierRevisionService(db)

    await asyncio.gather(
        service.emit_submission_relationship_lock(submission, _user()),
        service.emit_submission_relationship_lock(submission, _user()),
    )

    matching = [
        row
        for row in db.audit_events.documents
        if row.get("action") == "keydate.eot_submission.locked"
        and row.get("resource_id") == "submission-audit-race"
    ]
    assert len(matching) == 1


@pytest.mark.asyncio
async def test_failed_transactionless_delete_releases_deletion_claim() -> None:
    db = _KeyDateDatabase()
    db.key_date_baselines.documents.clear()
    milestone = db.key_date_milestones.documents[0]
    await db.key_date_achievements.insert_one(
        {
            "_id": "kd-1:ach",
            "milestone_id": "kd-1",
            "organization_id": "org-1",
            "project_id": "project-1",
            "actual_achievement_date": datetime(2026, 8, 2),
        }
    )

    with pytest.raises(KeyDateError, match="cannot be deleted"):
        await KeyDateService(db).delete(milestone, _user())

    stored = await db.key_date_milestones.find_one({"_id": "kd-1"})
    assert stored is not None
    assert stored.get("deleting_at") is None
    assert stored.get("deleting_by") is None


@pytest.mark.asyncio
async def test_legacy_draft_eot_rejects_parent_already_claimed_for_deletion() -> None:
    db = _KeyDateDatabase()
    db.key_date_baselines.documents.clear()
    milestone = db.key_date_milestones.documents[0]
    milestone["deleting_at"] = datetime(2026, 8, 22)
    milestone["deleting_by"] = "deleter"

    with pytest.raises(KeyDateError, match="deletion"):
        await KeyDateService(db).submit_eot(
            milestone,
            EOTApplicationCreate(requested_extension_days=5, submit=False),
            _user(),
        )

    assert db.key_date_eot_applications.documents == []
