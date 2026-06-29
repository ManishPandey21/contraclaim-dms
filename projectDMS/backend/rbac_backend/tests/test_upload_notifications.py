from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from bson import ObjectId
from fastapi import UploadFile

import backend.rbac_backend.services.email_service as email_service_module
from backend.rbac_backend.models.document import Document, DocumentProcessingResult
from backend.rbac_backend.models.notification import (
    Notification,
    NotificationCategory,
    NotificationContext,
    NotificationType,
)
from backend.rbac_backend.routers.documents import DocumentController
from backend.rbac_backend.services.document_service import DocumentService
from backend.rbac_backend.services.email_service import EmailService
from backend.rbac_backend.services.rbac_service import RBACService
from backend.rbac_backend.utils.notification_service import NotificationService


class FakeCursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = list(docs)

    def sort(self, key: str, direction: int):
        reverse = direction < 0
        self._docs = sorted(self._docs, key=lambda doc: doc.get(key), reverse=reverse)
        return self

    def skip(self, amount: int):
        self._docs = self._docs[max(amount, 0) :]
        return self

    def limit(self, amount: int):
        self._docs = self._docs[: max(amount, 0)]
        return self

    async def to_list(self, length=None):
        return list(self._docs)


class FakeInsertResult:
    def __init__(self, inserted_id):
        self.inserted_id = inserted_id


class FakeUpdateResult:
    def __init__(self, matched_count: int, modified_count: int):
        self.matched_count = matched_count
        self.modified_count = modified_count


def _match_value(value: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        if "$ne" in expected:
            disallowed = expected["$ne"]
            if isinstance(value, list):
                if disallowed in value:
                    return False
            elif value == disallowed:
                return False
        if "$gte" in expected and (value is None or value < expected["$gte"]):
            return False
        if "$in" in expected:
            options = expected["$in"]
            if isinstance(value, list):
                return bool(set(value).intersection(set(options)))
            return value in options
        if "$regex" in expected:
            import re

            flags = re.IGNORECASE if "i" in str(expected.get("$options", "")) else 0
            return re.search(str(expected["$regex"]), str(value or ""), flags) is not None
        return True

    if isinstance(value, list):
        return expected in value
    return value == expected


def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    def _get_value(record: Dict[str, Any], dotted_key: str) -> Any:
        value: Any = record
        for part in dotted_key.split("."):
            if not isinstance(value, dict):
                return None
            value = value.get(part)
        return value

    for key, expected in query.items():
        if key == "$or":
            if not any(_matches(doc, clause) for clause in expected):
                return False
            continue

        if not _match_value(_get_value(doc, key), expected):
            return False

    return True


class FakeCollection:
    def __init__(self, docs: Optional[List[Dict[str, Any]]] = None):
        self.docs = [deepcopy(doc) for doc in (docs or [])]

    async def find_one(self, query: Dict[str, Any], *args, **kwargs):
        for doc in self.docs:
            if _matches(doc, query):
                return deepcopy(doc)
        return None

    def find(self, query: Dict[str, Any], projection: Optional[Dict[str, int]] = None):
        items: List[Dict[str, Any]] = []
        for doc in self.docs:
            if not _matches(doc, query):
                continue
            if projection:
                items.append({key: doc.get(key) for key in projection})
            else:
                items.append(deepcopy(doc))
        return FakeCursor(items)

    async def insert_one(self, doc: Dict[str, Any]):
        stored = deepcopy(doc)
        stored.setdefault("_id", ObjectId())
        self.docs.append(stored)
        return FakeInsertResult(stored["_id"])

    async def update_one(self, query: Dict[str, Any], update: Dict[str, Any], *args, **kwargs):
        def _set_nested(record: Dict[str, Any], key: str, value: Any) -> None:
            target = record
            parts = key.split(".")
            for part in parts[:-1]:
                target = target.setdefault(part, {})
            target[parts[-1]] = value

        for doc in self.docs:
            if not _matches(doc, query):
                continue
            modified = 0
            for field, value in update.get("$addToSet", {}).items():
                doc.setdefault(field, [])
                if value not in doc[field]:
                    doc[field].append(value)
                    modified = 1
            for field, value in update.get("$set", {}).items():
                _set_nested(doc, field, value)
                modified = 1
            for field, value in update.get("$push", {}).items():
                doc.setdefault(field, [])
                doc[field].append(value)
                modified = 1
            return FakeUpdateResult(1, modified)
        if kwargs.get("upsert"):
            stored = deepcopy(query)
            for field, value in update.get("$setOnInsert", {}).items():
                stored[field] = value
            for field, value in update.get("$set", {}).items():
                stored[field] = value
            stored.setdefault("_id", ObjectId())
            self.docs.append(stored)
            return FakeUpdateResult(1, 1)
        return FakeUpdateResult(0, 0)

    async def update_many(self, query: Dict[str, Any], update: Dict[str, Any]):
        matched = 0
        modified = 0
        for doc in self.docs:
            if not _matches(doc, query):
                continue
            matched += 1
            for field, value in update.get("$addToSet", {}).items():
                doc.setdefault(field, [])
                if value not in doc[field]:
                    doc[field].append(value)
                    modified += 1
        return FakeUpdateResult(matched, modified)

    async def count_documents(self, query: Dict[str, Any]):
        return sum(1 for doc in self.docs if _matches(doc, query))

    async def create_index(self, *args, **kwargs):
        return None


class FakeDatabase:
    def __init__(
        self,
        *,
        users: Optional[List[Dict[str, Any]]] = None,
        documents: Optional[List[Dict[str, Any]]] = None,
        projects: Optional[List[Dict[str, Any]]] = None,
        notifications: Optional[List[Dict[str, Any]]] = None,
        notification_preferences: Optional[List[Dict[str, Any]]] = None,
        project_notification_subscriptions: Optional[List[Dict[str, Any]]] = None,
        notification_templates: Optional[List[Dict[str, Any]]] = None,
        notification_delivery_logs: Optional[List[Dict[str, Any]]] = None,
        notification_action_logs: Optional[List[Dict[str, Any]]] = None,
        letters: Optional[List[Dict[str, Any]]] = None,
    ):
        self.users = FakeCollection(users)
        self.documents = FakeCollection(documents)
        self.projects = FakeCollection(projects)
        self.notifications = FakeCollection(notifications)
        self.notification_preferences = FakeCollection(notification_preferences)
        self.project_notification_subscriptions = FakeCollection(project_notification_subscriptions)
        self.notification_templates = FakeCollection(notification_templates)
        self.notification_delivery_logs = FakeCollection(notification_delivery_logs)
        self.notification_action_logs = FakeCollection(notification_action_logs)
        self.letters = FakeCollection(letters)

    def __getitem__(self, item: str):
        return getattr(self, item)


class RecordingNotificationService:
    def __init__(self):
        self.calls: List[Dict[str, Any]] = []

    async def emit(self, event_type, resource_id, resource_type, **kwargs):
        self.calls.append(
            {
                "event_type": event_type,
                "resource_id": resource_id,
                "resource_type": resource_type,
                **kwargs,
            }
        )
        return SimpleNamespace(id="notification-1")


class StubManager:
    def __init__(self):
        self.messages: List[Dict[str, Any]] = []

    async def broadcast(self, recipients, payload):
        self.messages.append({"recipients": list(recipients), "payload": payload})


class StubBulkUploadService:
    def __init__(self):
        self.progress_updates: List[Dict[str, Any]] = []
        self.completed: Optional[Dict[str, Any]] = None
        self.failed: Optional[Dict[str, Any]] = None

    async def update_progress(self, job_id, processed, successful, failed, results):
        self.progress_updates.append(
            {
                "job_id": job_id,
                "processed": processed,
                "successful": successful,
                "failed": failed,
                "results": list(results),
            }
        )

    async def complete_job(self, job_id, status, successful, failed, results):
        self.completed = {
            "job_id": job_id,
            "status": status,
            "successful": successful,
            "failed": failed,
            "results": list(results),
        }

    async def fail_job(self, job_id, error):
        self.failed = {"job_id": job_id, "error": error}


def _make_controller(notification_service=None) -> tuple[DocumentController, StubBulkUploadService]:
    bulk_upload_service = StubBulkUploadService()
    document_service = SimpleNamespace(notification_service=notification_service)
    controller = DocumentController(
        document_service=document_service,
        file_service=SimpleNamespace(),
        export_service=SimpleNamespace(),
        auth_service=SimpleNamespace(),
        bulk_upload_service=bulk_upload_service,
    )
    return controller, bulk_upload_service


@pytest.mark.asyncio
async def test_rbac_upload_recipients_include_scoped_roles_and_aliases():
    db = FakeDatabase(
        users=[
            {"_id": "uploader", "roles": ["orguser"], "organization_id": "org-1", "projects": ["proj-1"]},
            {"_id": "org-admin", "roles": ["orgadmin"], "organization_id": "org-1"},
            {"_id": "org-user", "roles": ["orguser"], "organization_id": "org-1"},
            {"_id": "proj-admin", "roles": ["projadmin"], "projects": ["proj-1"]},
            {"_id": "proj-user", "roles": ["projuser"], "projects": ["proj-1"]},
            {"_id": "disabled-user", "roles": ["projectuser"], "projects": ["proj-1"], "disabled": True},
            {"_id": "outside-org", "roles": ["orguser"], "organization_id": "org-2"},
            {"_id": "outside-project", "roles": ["projectuser"], "projects": ["proj-2"]},
            {"_id": "super-admin", "roles": ["superadmin"], "organization_id": "org-1"},
        ],
        documents=[
            {
                "_id": "doc-1",
                "organization_id": "org-1",
                "project_id": "proj-1",
                "createdBy": "uploader",
            }
        ],
    )
    service = RBACService(db)

    recipients = await service.get_notifiable_users(
        NotificationType.NEW_UPLOAD,
        "doc-1",
        "document",
        NotificationContext.PROJECT,
        exclude_users=["uploader"],
    )

    assert set(recipients) == {"org-admin", "org-user", "proj-admin", "proj-user"}


@pytest.mark.asyncio
async def test_rbac_bulk_upload_project_scope_uses_project_collection():
    db = FakeDatabase(
        users=[
            {"_id": "org-admin", "roles": ["orgadmin"], "organization_id": "org-1"},
            {"_id": "proj-user", "roles": ["projectuser"], "projects": ["proj-1"]},
        ],
        projects=[
            {
                "_id": "proj-1",
                "organization_id": "org-1",
            }
        ],
    )
    service = RBACService(db)

    recipients = await service.get_notifiable_users(
        NotificationType.BULK_UPLOAD_COMPLETED,
        "proj-1",
        "project",
        NotificationContext.PROJECT,
    )

    assert set(recipients) == {"org-admin", "proj-user"}


@pytest.mark.asyncio
async def test_notification_service_mark_as_read_is_idempotent():
    notification_id = "507f1f77bcf86cd799439011"
    db = FakeDatabase(
        notifications=[
            {
                "_id": ObjectId(notification_id),
                "type": NotificationType.NEW_UPLOAD.value,
                "category": NotificationCategory.UPLOADS.value,
                "resource_id": "doc-1",
                "resource_type": "document",
                "recipients": ["user-1"],
                "read_by": ["user-1"],
                "created_at": datetime.now(timezone.utc),
                "data": {},
            }
        ]
    )
    service = NotificationService(db, manager=StubManager())

    assert await service.mark_as_read(notification_id, "user-1") is True


@pytest.mark.asyncio
async def test_notification_service_unread_since_filters_by_time_and_read_status():
    now = datetime.now(timezone.utc)
    db = FakeDatabase(
        notifications=[
            {
                "_id": str(ObjectId()),
                "type": NotificationType.NEW_UPLOAD.value,
                "category": NotificationCategory.UPLOADS.value,
                "resource_id": "doc-fresh",
                "resource_type": "document",
                "recipients": ["user-1"],
                "read_by": [],
                "created_at": now - timedelta(minutes=5),
                "data": {"title": "Fresh"},
            },
            {
                "_id": str(ObjectId()),
                "type": NotificationType.NEW_UPLOAD.value,
                "category": NotificationCategory.UPLOADS.value,
                "resource_id": "doc-read",
                "resource_type": "document",
                "recipients": ["user-1"],
                "read_by": ["user-1"],
                "created_at": now - timedelta(minutes=1),
                "data": {"title": "Read"},
            },
            {
                "_id": str(ObjectId()),
                "type": NotificationType.NEW_UPLOAD.value,
                "category": NotificationCategory.UPLOADS.value,
                "resource_id": "doc-old",
                "resource_type": "document",
                "recipients": ["user-1"],
                "read_by": [],
                "created_at": now - timedelta(days=2),
                "data": {"title": "Old"},
            },
        ]
    )
    service = NotificationService(db, manager=StubManager())

    notifications = await service.unread_since(
        "user-1",
        category=NotificationCategory.UPLOADS,
        since=now - timedelta(hours=1),
    )

    assert [item.resource_id for item in notifications] == ["doc-fresh"]


@pytest.mark.asyncio
async def test_notification_service_emit_persists_phase1_metadata_and_dedupes():
    db = FakeDatabase(
        users=[
            {"_id": "uploader", "roles": ["orguser"], "organization_id": "org-1", "projects": ["proj-1"]},
            {"_id": "recipient", "roles": ["projectuser"], "projects": ["proj-1"]},
        ],
        documents=[
            {
                "_id": "doc-1",
                "organization_id": "org-1",
                "project_id": "proj-1",
                "createdBy": "uploader",
            }
        ],
    )
    manager = StubManager()
    service = NotificationService(db, manager=manager)

    first = await service.emit(
        NotificationType.NEW_UPLOAD,
        "doc-1",
        "document",
        context=NotificationContext.PROJECT,
        actor_id="uploader",
        data={"title": "New document uploaded", "message": "A file was uploaded"},
        actions=[{"key": "view_document", "label": "View Document", "href": "/documentviewer/doc-1"}],
        dedupe_key="document:new_upload:doc-1",
    )
    second = await service.emit(
        NotificationType.NEW_UPLOAD,
        "doc-1",
        "document",
        context=NotificationContext.PROJECT,
        actor_id="uploader",
        data={"title": "Duplicate"},
        dedupe_key="document:new_upload:doc-1",
    )

    assert first.id == second.id
    assert len(db.notifications.docs) == 1
    stored_doc = dict(db.notifications.docs[0])
    stored_doc["_id"] = str(stored_doc["_id"])
    stored = Notification(**stored_doc)
    assert stored.recipients == ["recipient"]
    assert stored.title == "New document uploaded"
    assert stored.message == "A file was uploaded"
    assert stored.resource_link == "/documentviewer/doc-1"
    assert stored.dedupe_key == "document:new_upload:doc-1"
    assert stored.actions[0].key == "view_document"
    assert manager.messages[0]["recipients"] == ["recipient"]


@pytest.mark.asyncio
async def test_notification_service_list_and_mark_all_support_phase1_filters():
    now = datetime.now(timezone.utc)
    db = FakeDatabase(
        notifications=[
            {
                "_id": str(ObjectId()),
                "type": NotificationType.NEW_UPLOAD.value,
                "category": NotificationCategory.UPLOADS.value,
                "resource_id": "doc-1",
                "resource_type": "document",
                "project_id": "proj-1",
                "recipients": ["user-1"],
                "read_by": [],
                "created_at": now,
                "title": "Contract uploaded",
                "data": {"subject": "Contract package"},
            },
            {
                "_id": str(ObjectId()),
                "type": NotificationType.COMMENT_ADDED.value,
                "category": NotificationCategory.COMMENTS.value,
                "resource_id": "doc-2",
                "resource_type": "document",
                "project_id": "proj-2",
                "recipients": ["user-1"],
                "read_by": [],
                "created_at": now,
                "title": "Comment added",
                "data": {},
            },
        ]
    )
    service = NotificationService(db, manager=StubManager())

    listed = await service.list_notifications(
        "user-1",
        event_type=NotificationType.NEW_UPLOAD.value,
        project_id="proj-1",
        search="contract",
    )
    assert listed.total == 1
    assert listed.notifications[0].resource_id == "doc-1"

    updated = await service.mark_all_as_read(
        "user-1",
        event_type=NotificationType.NEW_UPLOAD.value,
        project_id="proj-1",
    )
    assert updated == 1
    assert db.notifications.docs[0]["read_by"] == ["user-1"]
    assert db.notifications.docs[1]["read_by"] == []


@pytest.mark.asyncio
async def test_notification_preferences_disable_event_delivery():
    db = FakeDatabase(
        users=[
            {"_id": "uploader", "roles": ["orguser"], "organization_id": "org-1", "projects": ["proj-1"]},
            {"_id": "recipient", "roles": ["projectuser"], "projects": ["proj-1"]},
        ],
        documents=[
            {
                "_id": "doc-1",
                "organization_id": "org-1",
                "project_id": "proj-1",
                "createdBy": "uploader",
            }
        ],
        notification_preferences=[
            {
                "user_id": "recipient",
                "organization_id": "org-1",
                "default_channels": ["in_app", "websocket", "email"],
                "event_settings": {NotificationType.NEW_UPLOAD.value: {"enabled": False}},
                "quiet_hours": {},
                "digest_enabled": True,
                "browser_notifications_enabled": False,
                "email_notifications_enabled": True,
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        ],
    )
    service = NotificationService(db, manager=StubManager())

    notification = await service.emit(
        NotificationType.NEW_UPLOAD,
        "doc-1",
        "document",
        context=NotificationContext.PROJECT,
        actor_id="uploader",
        data={"title": "New document uploaded"},
    )

    assert notification.recipients == []
    assert db.notifications.docs == []


@pytest.mark.asyncio
async def test_project_notification_subscription_can_unsubscribe_user():
    db = FakeDatabase(
        users=[
            {"_id": "uploader", "roles": ["orguser"], "organization_id": "org-1", "projects": ["proj-1"]},
            {"_id": "recipient", "roles": ["projectuser"], "projects": ["proj-1"]},
        ],
        documents=[
            {
                "_id": "doc-1",
                "organization_id": "org-1",
                "project_id": "proj-1",
                "createdBy": "uploader",
            }
        ],
        project_notification_subscriptions=[
            {
                "project_id": "proj-1",
                "organization_id": "org-1",
                "user_id": "recipient",
                "subscribed": False,
                "event_settings": {},
                "created_at": datetime.now(timezone.utc),
                "updated_at": datetime.now(timezone.utc),
            }
        ],
    )
    service = NotificationService(db, manager=StubManager())

    notification = await service.emit(
        NotificationType.NEW_UPLOAD,
        "doc-1",
        "document",
        context=NotificationContext.PROJECT,
        actor_id="uploader",
        data={"title": "New document uploaded"},
    )

    assert notification.recipients == []
    assert db.notifications.docs == []


@pytest.mark.asyncio
async def test_email_delivery_log_records_smtp_disabled_skip():
    db = FakeDatabase(
        users=[
            {
                "_id": "user-1",
                "email": "user@example.com",
                "preferences": {"emailNotifications": True},
            }
        ]
    )
    service = EmailService(db)
    service.smtp_host = None
    service.smtp_user = None
    service.smtp_password = None

    notification = Notification(
        type=NotificationType.NEW_UPLOAD,
        category=NotificationCategory.UPLOADS,
        resource_id="doc-1",
        resource_type="document",
        context=NotificationContext.PROJECT,
        recipients=["user-1"],
        title="New document uploaded",
        message="A document was uploaded",
        resource_link="/documentviewer/doc-1",
        data={},
    )

    assert await service.send_immediate_notification("user-1", notification) is False
    assert len(db.notification_delivery_logs.docs) == 1
    log = db.notification_delivery_logs.docs[0]
    assert log["status"] == "skipped"
    assert log["error_code"] == "smtp_disabled"


@pytest.mark.asyncio
async def test_notification_action_approve_rechecks_letter_and_marks_action_complete():
    letter_id = ObjectId()
    notification_id = ObjectId()
    db = FakeDatabase(
        notifications=[
            {
                "_id": notification_id,
                "type": NotificationType.APPROVAL_ASSIGNED.value,
                "category": NotificationCategory.APPROVALS.value,
                "resource_id": str(letter_id),
                "resource_type": "letter",
                "organization_id": "org-1",
                "project_id": "proj-1",
                "recipients": ["approver"],
                "read_by": [],
                "created_at": datetime.now(timezone.utc),
                "title": "Approval assigned",
                "message": "Letter is waiting for approval",
                "actions": [
                    {"key": "approve", "label": "Approve", "method": "post"},
                    {"key": "reject", "label": "Reject", "method": "post"},
                ],
                "action_state": {},
                "resource_link": f"/letters/{letter_id}/approval",
                "data": {"title": "Approval assigned"},
            }
        ],
        letters=[
            {
                "_id": letter_id,
                "title": "Letter for approval",
                "recipient": "Owner",
                "subject": "Subject",
                "content": "",
                "status": "Approval",
                "created_by": "submitter",
                "assigned_to": "approver",
                "organization_id": "org-1",
                "project_id": "proj-1",
                "createdAt": datetime.now(timezone.utc),
                "updatedAt": datetime.now(timezone.utc),
            }
        ],
    )
    service = NotificationService(db, manager=StubManager())

    result = await service.execute_action(
        str(notification_id),
        "approver",
        "approve",
        current_user=SimpleNamespace(
            id="approver",
            roles=["superadmin"],
            organization_id=None,
            organizations=[],
            projects=[],
        ),
    )

    assert result["status"] == "completed"
    assert result["result"]["new_status"] == "Approved"
    assert db.letters.docs[0]["status"] == "Approved"
    assert db.notifications.docs[0]["action_state"]["approve"]["status"] == "completed"
    assert db.notifications.docs[0]["read_by"] == ["approver"]
    assert db.notification_action_logs.docs[0]["action"] == "approve"


@pytest.mark.asyncio
async def test_notification_action_rejects_non_recipient():
    notification_id = ObjectId()
    db = FakeDatabase(
        notifications=[
            {
                "_id": notification_id,
                "type": NotificationType.APPROVAL_ASSIGNED.value,
                "category": NotificationCategory.APPROVALS.value,
                "resource_id": str(ObjectId()),
                "resource_type": "letter",
                "recipients": ["approver"],
                "read_by": [],
                "created_at": datetime.now(timezone.utc),
                "actions": [{"key": "approve", "label": "Approve", "method": "post"}],
                "data": {},
            }
        ]
    )
    service = NotificationService(db, manager=StubManager())

    with pytest.raises(Exception) as exc_info:
        await service.execute_action(
            str(notification_id),
            "other-user",
            "approve",
            current_user=SimpleNamespace(id="other-user", roles=["superadmin"]),
        )

    assert getattr(exc_info.value, "status_code", None) == 404


@pytest.mark.asyncio
async def test_email_service_skips_immediate_send_when_preference_disabled(monkeypatch):
    db = FakeDatabase(
        users=[
            {
                "_id": "user-1",
                "email": "user@example.com",
                "preferences": {"emailNotifications": False},
            }
        ]
    )
    service = EmailService(db)
    service.smtp_host = "smtp.example.com"
    service.smtp_user = "mailer"
    service.smtp_password = "secret"
    monkeypatch.setattr(email_service_module, "aiosmtplib", object())

    sent = {"called": False}

    async def _never_send(message):
        sent["called"] = True
        return True

    monkeypatch.setattr(service, "_send", _never_send)

    notification = Notification(
        type=NotificationType.NEW_UPLOAD,
        category=NotificationCategory.UPLOADS,
        resource_id="doc-1",
        resource_type="document",
        context=NotificationContext.PROJECT,
        recipients=["user-1"],
        data={"title": "New document uploaded"},
    )

    assert await service.send_immediate_notification("user-1", notification) is False
    assert sent["called"] is False


@pytest.mark.asyncio
async def test_document_service_create_document_emits_ui_ready_single_upload_payload():
    db = FakeDatabase()
    notifications = RecordingNotificationService()
    service = DocumentService(db=db, notification_service=notifications)
    document = Document(
        _id="507f1f77bcf86cd799439012",
        organization_id="org-1",
        project_id="proj-1",
        filename="contract.pdf",
        filetype="application/pdf",
        filesize=128,
        uploadType="incoming",
        letterNo="LET-001",
        date=datetime.now(timezone.utc),
        subject="Scope of work",
        status="draft",
        createdBy="uploader",
    )

    created = await service.create_document(document=document)

    assert created.filename == "contract.pdf"
    assert len(notifications.calls) == 1
    emitted = notifications.calls[0]
    assert emitted["event_type"] == NotificationType.NEW_UPLOAD
    assert emitted["resource_type"] == "document"
    assert emitted["context"] == NotificationContext.PROJECT
    assert emitted["actor_id"] == "uploader"
    assert emitted["data"] == {
        "title": "New document uploaded",
        "message": "Incoming document LET-001 uploaded. Subject: Scope of work",
        "document_id": emitted["resource_id"],
        "letter_no": "LET-001",
        "subject": "Scope of work",
        "filename": "contract.pdf",
        "upload_type": "incoming",
        "organization_id": "org-1",
        "project_id": "proj-1",
    }


@pytest.mark.asyncio
async def test_process_single_file_disables_per_file_upload_notifications():
    controller, _ = _make_controller()
    captured: Dict[str, Any] = {}

    async def fake_create_document(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(id="doc-1")

    controller.create_document = fake_create_document  # type: ignore[method-assign]
    upload = UploadFile(filename="demo.pdf", file=BytesIO(b"pdf"))

    result = await controller._process_single_file(
        {
            "filename": "demo.pdf",
            "upload_type": "incoming",
            "letter_no": "LET-42",
            "date": datetime.now(timezone.utc),
            "ocr_enabled": False,
        },
        {"demo.pdf": upload},
        "org-1",
        "proj-1",
        SimpleNamespace(id="uploader"),
    )

    assert result.success is True
    assert captured["emit_upload_notification"] is False


@pytest.mark.asyncio
async def test_bulk_upload_emits_single_summary_notification():
    notifications = RecordingNotificationService()
    controller, bulk_upload_service = _make_controller(notification_service=notifications)

    async def fake_process_single_file(row_data, *_args, **_kwargs):
        if row_data["filename"] == "good.pdf":
            return DocumentProcessingResult(
                filename="good.pdf",
                success=True,
                document_id="doc-1",
                row_number=1,
            )
        return DocumentProcessingResult(
            filename="bad.pdf",
            success=False,
            error="failed",
            row_number=2,
        )

    controller._process_single_file = fake_process_single_file  # type: ignore[method-assign]

    await controller._process_bulk_upload(
        "job-1",
        [{"filename": "good.pdf"}, {"filename": "bad.pdf"}],
        [],
        "org-1",
        "proj-1",
        SimpleNamespace(id="uploader"),
    )

    assert bulk_upload_service.completed is not None
    assert bulk_upload_service.completed["status"] == "completed_with_errors"
    assert len(notifications.calls) == 1
    emitted = notifications.calls[0]
    assert emitted["event_type"] == NotificationType.BULK_UPLOAD_COMPLETED
    assert emitted["resource_id"] == "proj-1"
    assert emitted["resource_type"] == "project"
    assert emitted["context"] == NotificationContext.PROJECT
    assert emitted["data"] == {
        "title": "Bulk upload completed",
        "message": "Bulk upload completed: 1 document uploaded, 1 failed",
        "job_id": "job-1",
        "successful_count": 1,
        "failed_count": 1,
        "organization_id": "org-1",
        "project_id": "proj-1",
    }


@pytest.mark.asyncio
async def test_bulk_upload_skips_summary_when_all_rows_fail():
    notifications = RecordingNotificationService()
    controller, _ = _make_controller(notification_service=notifications)

    async def fake_process_single_file(row_data, *_args, **_kwargs):
        return DocumentProcessingResult(
            filename=row_data["filename"],
            success=False,
            error="failed",
            row_number=1,
        )

    controller._process_single_file = fake_process_single_file  # type: ignore[method-assign]

    await controller._process_bulk_upload(
        "job-2",
        [{"filename": "bad.pdf"}],
        [],
        "org-1",
        "proj-1",
        SimpleNamespace(id="uploader"),
    )

    assert notifications.calls == []
