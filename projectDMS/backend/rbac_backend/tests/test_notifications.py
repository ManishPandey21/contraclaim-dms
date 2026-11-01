import pytest

from backend.rbac_backend.models.notification import (
    Notification,
    NotificationCategory,
    NotificationContext,
    NotificationResponse,
    NotificationType,
)
from backend.rbac_backend.services.rbac_service import RBACService
from backend.rbac_backend.utils.notification_service import should_send_immediate_email


def test_deadline_reminders_trigger_immediate_email():
    # The contract-control deadline reminders must reach the user by email.
    for event in (
        NotificationType.KEYDATE_DUE,
        NotificationType.KEYDATE_OVERDUE,
        NotificationType.CLAIM_DEADLINE_APPROACHING,
        NotificationType.CLAIM_DEADLINE_BREACHED,
    ):
        assert should_send_immediate_email(event, ["in_app", "websocket", "email"]) is True
    # Honour the channel set: no email channel → no immediate email.
    assert should_send_immediate_email(NotificationType.KEYDATE_OVERDUE, ["in_app"]) is False
    # Non-allowlisted informational events do not email.
    assert should_send_immediate_email(NotificationType.APPROVAL_COMPLETED, ["email"]) is False


class FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *args, **kwargs):
        return self

    def skip(self, *args, **kwargs):
        return self

    def limit(self, *args, **kwargs):
        return self

    async def to_list(self, length=None):
        return list(self._docs)


class FakeCollection:
    def __init__(self, docs):
        self.docs = list(docs)

    async def find_one(self, query):
        for doc in self.docs:
            if _matches(doc, query):
                return doc
        return None

    def find(self, query, projection=None):
        filtered = []
        for doc in self.docs:
            if _matches(doc, query):
                if projection:
                    filtered.append({key: doc.get(key) for key in projection})
                else:
                    filtered.append(doc)
        return FakeCursor(filtered)


class FakeDatabase:
    def __init__(self, *, users=None, documents=None, letters=None):
        self.users = FakeCollection(users or [])
        self.documents = FakeCollection(documents or [])
        self.letters = FakeCollection(letters or [])

    def __getitem__(self, item):
        return getattr(self, item)


def _matches(doc, query):
    for key, expected in query.items():
        if key == "disabled":
            if "$ne" in expected and doc.get("disabled") == expected["$ne"]:
                return False
        elif key == "$or":
            if not any(_matches(doc, clause) for clause in expected):
                return False
        elif isinstance(expected, dict) and "$ne" in expected:
            if doc.get(key) == expected["$ne"]:
                return False
        else:
            if isinstance(expected, dict) and "$in" in expected:
                values = expected["$in"]
                if doc.get(key) not in values and not set(values).intersection(
                    set(doc.get(key, [])) if isinstance(doc.get(key), list) else []
                ):
                    return False
            else:
                value = doc.get(key)
                if isinstance(value, list):
                    if expected not in value:
                        return False
                elif value != expected:
                    return False
    return True


@pytest.mark.asyncio
async def test_notification_response_unread_flag():
    notification = Notification(
        type=NotificationType.NEW_UPLOAD,
        category=NotificationCategory.UPLOADS,
        resource_id="doc1",
        resource_type="document",
        context=NotificationContext.ORGANIZATION,
        recipients=["user-1", "user-2"],
        read_by=["user-2"],
        data={"title": "File uploaded"},
    )
    resp = NotificationResponse.from_notification(notification, "user-1")
    assert resp.unread is True
    resp2 = NotificationResponse.from_notification(notification, "user-2")
    assert resp2.unread is False


@pytest.mark.asyncio
async def test_rbac_resolves_scoped_recipients():
    db = FakeDatabase(
        users=[
            {"_id": "u1", "roles": ["orgadmin"], "organization_id": "org-1"},
            {"_id": "u2", "roles": ["projectadmin"], "projects": ["proj-1"]},
            {"_id": "u3", "roles": ["viewer"], "organization_id": "org-1"},
        ],
        documents=[
            {
                "_id": "doc-1",
                "organization_id": "org-1",
                "project_id": "proj-1",
                "createdBy": "u3",
            }
        ],
    )
    rbac = RBACService(db)
    recipients = await rbac.get_notifiable_users(
        NotificationType.NEW_UPLOAD,
        "doc-1",
        "document",
        NotificationContext.PROJECT,
    )
    assert set(recipients) == {"u1", "u2"}
