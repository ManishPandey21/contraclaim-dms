"""Email helper for notification fan-out and digest delivery."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from email.message import EmailMessage
import re
from typing import Any, Dict, Iterable, List, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

try:  # pragma: no cover - optional dependency
    import aiosmtplib
except Exception:  # noqa: BLE001
    aiosmtplib = None

from ..models.notification import Notification, NotificationCategory

logger = logging.getLogger(__name__)


def _coerce_object_id(value: str) -> ObjectId | str:
    try:
        return ObjectId(value)
    except Exception:  # noqa: BLE001
        return value


class EmailService:
    """Send immediate notification emails and digest summaries."""

    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        notification_service: Optional["NotificationService"] = None,
    ) -> None:
        self.db = db
        self.notification_service = notification_service

        self.smtp_host = os.getenv("SMTP_HOST")
        self.smtp_port = int(os.getenv("SMTP_PORT", "587"))
        self.smtp_user = os.getenv("SMTP_USER")
        self.smtp_password = os.getenv("SMTP_PASSWORD")
        self.from_email = os.getenv("FROM_EMAIL", "noreply@contraclaim.com")
        self.app_url = os.getenv("APP_URL", "http://localhost:5173")

    @property
    def _smtp_enabled(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_password and aiosmtplib)

    async def send_immediate_notification(self, user_id: str, notification: Notification) -> bool:
        """Send email immediately for high-priority events."""
        if not self._smtp_enabled:
            logger.debug("SMTP not configured; skipping immediate email")
            return False

        user = await self._load_user(user_id)
        if not user or not self._email_notifications_enabled(user):
            return False
        if not user.get("email"):
            return False

        subject = self._build_subject(notification)
        body = self._build_body(notification, user.get("first_name") or user.get("username") or "there")

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.from_email
        message["To"] = user.get("email")
        message.set_content(body)

        return await self._send(message)

    async def send_digest(
        self,
        user_id: str,
        *,
        period: str,
        category: Optional[NotificationCategory],
        since: datetime,
    ) -> bool:
        """Email unread notifications since a time window."""
        if not self._smtp_enabled:
            return False

        notifications = await self._collect_unread(user_id, category, since)
        if not notifications:
            return False

        user = await self._load_user(user_id)
        if not user or not self._email_notifications_enabled(user):
            return False
        if not user.get("email"):
            return False

        subject = f"{period.title()} {category.value.title() if category else 'Notifications'} Digest"
        body_lines = [
            f"Hello {user.get('first_name') or user.get('username') or 'there'},",
            "",
            f"Here is your {period} summary for {category.value if category else 'notifications'}:",
            "",
        ]
        for item in notifications:
            created_at = item.created_at.strftime("%Y-%m-%d %H:%M")
            summary = item.data.get("title") or item.data.get("message") or item.resource_type
            body_lines.append(f"- [{created_at}] {item.type.value.replace('_', ' ').title()}: {summary}")
        body_lines.append("")
        body_lines.append(f"View all in ContraClaim: {self.app_url}/notifications")
        body_lines.append("")
        body_lines.append("Best regards,\nContraClaim DMS")

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.from_email
        message["To"] = user.get("email")
        message.set_content("\n".join(body_lines))

        return await self._send(message)

    async def send_daily_digests(self) -> None:
        if not self._smtp_enabled:
            return
        since = datetime.utcnow() - timedelta(days=1)
        await self._send_digest_cycle("daily", since)

    async def send_weekly_digests(self) -> None:
        if not self._smtp_enabled:
            return
        since = datetime.utcnow() - timedelta(days=7)
        await self._send_digest_cycle("weekly", since)

    async def _send_digest_cycle(self, period: str, since: datetime) -> None:
        categories = [None, *list(NotificationCategory)]
        cursor = self.db.users.find({"disabled": {"$ne": True}})
        users = await cursor.to_list(length=None)
        for user in users:
            if not self._email_notifications_enabled(user):
                continue
            user_id = str(user.get("_id"))
            for category in categories:
                try:
                    await self.send_digest(user_id, period=period, category=category, since=since)
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "Digest email failed for user %s (%s): %s",
                        user_id,
                        category,
                        exc,
                    )

    async def _collect_unread(
        self,
        user_id: str,
        category: Optional[NotificationCategory],
        since: datetime,
    ) -> List[Notification]:
        if self.notification_service:
            return await self.notification_service.unread_since(user_id, category=category, since=since)

        query: Dict[str, Any] = {
            "recipients": user_id,
            "read_by": {"$ne": user_id},
            "created_at": {"$gte": since},
        }
        if category:
            query["category"] = category.value
        cursor = self.db.notifications.find(query).sort("created_at", -1)
        docs = await cursor.to_list(length=None)
        return [Notification(**doc) for doc in docs]

    @staticmethod
    def _email_notifications_enabled(user: Dict[str, Any]) -> bool:
        preferences = user.get("preferences") or {}
        if not isinstance(preferences, dict):
            return True
        return bool(preferences.get("emailNotifications", True))

    async def _load_user(self, user_id: str) -> Optional[dict]:
        return await self.db.users.find_one({"_id": _coerce_object_id(user_id)})

    def _build_subject(self, notification: Notification) -> str:
        return (
            f"{notification.category.value.title()} update: "
            f"{notification.type.value.replace('_', ' ').title()}"
        )

    def _build_body(self, notification: Notification, recipient_name: str) -> str:
        lines = [
            f"Hello {recipient_name},",
            "",
            f"You have a new {notification.category.value} notification.",
            f"Type: {notification.type.value.replace('_', ' ').title()}",
            f"Resource: {notification.resource_type} ({notification.resource_id})",
        ]
        if notification.data:
            for key, value in notification.data.items():
                lines.append(f"- {key}: {value}")
        lines.extend(
            [
                "",
                f"View in ContraClaim: {self.app_url}/notifications",
                "",
                "Best regards,",
                "ContraClaim DMS",
            ]
        )
        return "\n".join(lines)

    async def _send(self, message: EmailMessage) -> bool:
        if not self._smtp_enabled:
            return False
        try:
            smtp = aiosmtplib.SMTP(hostname=self.smtp_host, port=self.smtp_port)
            await smtp.connect()
            # connect() already negotiates STARTTLS when supported, so avoid
            # calling starttls() again which raises "Connection already using TLS".
            await smtp.login(self.smtp_user, self.smtp_password)
            await smtp.send_message(message)
            await smtp.quit()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("Email send failed: %s", exc)
            return False

    async def fetch_document(self, document_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a document by id, handling both ObjectId and UUID identifiers."""
        doc_id = _coerce_object_id(document_id)
        document = await self.db.documents.find_one({"_id": doc_id})
        if document and "_id" in document:
            document["_id"] = str(document["_id"])
        return document

    async def send_share_email(
        self,
        *,
        to: List[str],
        subject: str,
        html_body: Optional[str] = None,
        text_body: Optional[str] = None,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
    ) -> bool:
        """Send a document share email with optional HTML body."""
        if not to:
            logger.warning("Attempted to send share email without recipients")
            return False

        if not self._smtp_enabled:
            logger.info("SMTP disabled; skipping share email send")
            return False

        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = self.from_email
        message["To"] = ", ".join(to)
        if cc:
            message["Cc"] = ", ".join(cc)
        if bcc:
            message["Bcc"] = ", ".join(bcc)

        plain_fallback = text_body or _strip_html(html_body or "")
        if html_body:
            message.set_content(plain_fallback or subject)
            message.add_alternative(html_body, subtype="html")
        else:
            message.set_content(plain_fallback or subject)

        return await self._send(message)


def _strip_html(value: str) -> str:
    if not value:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", value, flags=re.IGNORECASE)
    text = re.sub(r"</p>", "\n\n", text, flags=re.IGNORECASE)
    return re.sub(r"<[^>]+>", "", text).strip()


# Circular import guard
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from ..utils.notification_service import NotificationService
