"""Email helper for notification fan-out and digest delivery."""

from __future__ import annotations

import logging
import hashlib
import html
import mimetypes
import os
import secrets
from datetime import datetime, timedelta
from email.message import EmailMessage
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

try:  # pragma: no cover - optional dependency
    import aiosmtplib
except Exception:  # noqa: BLE001
    aiosmtplib = None

from ..models.notification import Notification, NotificationCategory, NotificationDeliveryStatus
from ..core.config import settings
from .file_object_service import FileObjectService
from .s3_service import S3Service
from .smtp_settings_service import SmtpRuntimeConfig, SmtpSettingsService

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

        self.smtp_host = os.getenv("SMTP_HOST") or settings.SMTP_HOST
        self.smtp_port = int(os.getenv("SMTP_PORT") or settings.SMTP_PORT or "587")
        self.smtp_user = os.getenv("SMTP_USER") or os.getenv("SMTP_USERNAME") or settings.SMTP_USERNAME
        self.smtp_password = os.getenv("SMTP_PASSWORD") or settings.SMTP_PASSWORD
        self.from_email = (
            os.getenv("FROM_EMAIL")
            or os.getenv("SMTP_FROM_EMAIL")
            or settings.SMTP_FROM_EMAIL
            or "noreply@contraclaim.com"
        )
        self.from_name = os.getenv("FROM_NAME") or os.getenv("SMTP_FROM_NAME")
        self.app_url = os.getenv("APP_URL", "http://localhost:5173")
        self.public_api_url = (
            os.getenv("PUBLIC_API_URL")
            or os.getenv("BACKEND_PUBLIC_URL")
            or os.getenv("API_BASE_URL")
            or "http://localhost:8000/api"
        ).rstrip("/")
        self.share_token_ttl_days = int(os.getenv("DOCUMENT_SHARE_TOKEN_TTL_DAYS", "30"))
        self.share_attachment_max_mb = int(os.getenv("SHARE_EMAIL_ATTACHMENT_MAX_MB", "15"))
        self.s3_service = S3Service()
        self.file_object_service = FileObjectService(db=db, s3_service=self.s3_service)

    @property
    def _smtp_enabled(self) -> bool:
        return bool(self.smtp_host and self.from_email and aiosmtplib)

    async def send_immediate_notification(self, user_id: str, notification: Notification) -> bool:
        """Send email immediately for high-priority events."""
        user = await self._load_user(user_id)
        log_id = await self._create_delivery_log(notification, user_id, "email")

        if not self._smtp_enabled:
            logger.debug("SMTP not configured; skipping immediate email")
            await self._finish_delivery_log(
                log_id,
                NotificationDeliveryStatus.SKIPPED,
                error_code="smtp_disabled",
                error_message="SMTP is not configured",
            )
            return False

        if not user or not await self._email_notifications_enabled(user_id, user, notification.type.value):
            await self._finish_delivery_log(
                log_id,
                NotificationDeliveryStatus.SKIPPED,
                error_code="preference_disabled",
                error_message="Email notifications are disabled for this user or event",
            )
            return False
        if not user.get("email"):
            await self._finish_delivery_log(
                log_id,
                NotificationDeliveryStatus.SKIPPED,
                error_code="missing_email",
                error_message="User has no email address",
            )
            return False

        rendered = await self._render_notification_email(
            notification,
            user.get("first_name") or user.get("username") or "there",
        )

        message = EmailMessage()
        message["Subject"] = rendered.subject
        message["From"] = self.from_email
        message["To"] = user.get("email")
        message.set_content(rendered.text_body)
        if rendered.html_body:
            message.add_alternative(rendered.html_body, subtype="html")

        sent = await self._send(message)
        await self._finish_delivery_log(
            log_id,
            NotificationDeliveryStatus.SENT if sent else NotificationDeliveryStatus.FAILED,
            error_code=None if sent else "send_failed",
            error_message=None if sent else "SMTP send failed",
        )
        return sent

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
        if not user or not await self._email_notifications_enabled(user_id, user):
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
            user_id = str(user.get("_id"))
            if not await self._email_notifications_enabled(user_id, user):
                continue
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

    async def _email_notifications_enabled(
        self,
        user_id: str,
        user: Dict[str, Any],
        event_type: Optional[str] = None,
    ) -> bool:
        preference = await self.db.notification_preferences.find_one({"user_id": str(user_id)})
        if preference:
            if not preference.get("email_notifications_enabled", True):
                return False
            if "email" not in (preference.get("default_channels") or ["email"]):
                return False
            if event_type:
                event_settings = preference.get("event_settings") or {}
                current = event_settings.get(event_type) if isinstance(event_settings, dict) else None
                if isinstance(current, dict):
                    if current.get("enabled") is False:
                        return False
                    if current.get("channels") and "email" not in current.get("channels"):
                        return False
            return True

        preferences = user.get("preferences") or {}
        if not isinstance(preferences, dict):
            return True
        return bool(preferences.get("emailNotifications", True))

    async def _load_user(self, user_id: str) -> Optional[dict]:
        return await self.db.users.find_one({"_id": _coerce_object_id(user_id)})

    def _build_subject(self, notification: Notification) -> str:
        return notification.title or (
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
                f"View in ContraClaim: {self._build_resource_url(notification)}",
                "",
                "Best regards,",
                "ContraClaim DMS",
            ]
        )
        return "\n".join(lines)

    async def _render_notification_email(
        self,
        notification: Notification,
        recipient_name: str,
    ) -> SimpleNamespace:
        variables = self._template_variables(notification, recipient_name)
        template = await self._load_template(notification.type.value, "email")
        if template:
            subject = self._render_template_string(template.get("subject_template", ""), variables)
            text_body = self._render_template_string(template.get("body_template", ""), variables)
            html_template = template.get("html_template")
            html_body = self._render_template_string(html_template, variables) if html_template else None
            return SimpleNamespace(subject=subject, text_body=text_body, html_body=html_body)

        return SimpleNamespace(
            subject=self._build_subject(notification),
            text_body=self._build_body(notification, recipient_name),
            html_body=self._build_default_html(notification, recipient_name),
        )

    async def _load_template(self, event_type: str, channel: str) -> Optional[Dict[str, Any]]:
        return await self.db.notification_templates.find_one(
            {
                "event_type": event_type,
                "channel": channel,
                "locale": "en",
                "active": True,
            }
        )

    def _template_variables(self, notification: Notification, recipient_name: str) -> Dict[str, Any]:
        title = notification.title or notification.data.get("title") or notification.type.value.replace("_", " ").title()
        message = notification.message or notification.data.get("message") or ""
        return {
            "recipient_name": recipient_name,
            "title": title,
            "message": message,
            "event_type": notification.type.value,
            "category": notification.category.value,
            "resource_type": notification.resource_type,
            "resource_id": notification.resource_id,
            "resource_url": self._build_resource_url(notification),
            "app_url": self.app_url.rstrip("/"),
            **{key: value for key, value in (notification.data or {}).items() if value is not None},
        }

    @staticmethod
    def _render_template_string(template: Optional[str], variables: Dict[str, Any]) -> str:
        if not template:
            return ""

        class SafeDict(dict):
            def __missing__(self, key):
                return "{" + key + "}"

        return str(template).format_map(SafeDict(variables))

    def _build_resource_url(self, notification: Notification) -> str:
        link = notification.resource_link or "/notifications"
        if link.startswith("http://") or link.startswith("https://"):
            return link
        return f"{self.app_url.rstrip('/')}/{link.lstrip('/')}"

    def _build_default_html(self, notification: Notification, recipient_name: str) -> str:
        title = notification.title or notification.data.get("title") or notification.type.value.replace("_", " ").title()
        message = notification.message or notification.data.get("message") or ""
        url = self._build_resource_url(notification)
        return (
            "<!doctype html><html><body>"
            f"<p>Hello {recipient_name},</p>"
            f"<h2>{title}</h2>"
            f"<p>{message}</p>"
            f"<p><a href=\"{url}\">Open in ContraClaim</a></p>"
            "<p>Best regards,<br>ContraClaim DMS</p>"
            "</body></html>"
        )

    async def _create_delivery_log(self, notification: Notification, user_id: str, channel: str) -> Optional[Any]:
        now = datetime.utcnow()
        payload = {
            "notification_id": str(notification.id) if notification.id else None,
            "user_id": str(user_id),
            "channel": channel,
            "status": NotificationDeliveryStatus.PENDING.value,
            "provider": "smtp",
            "attempt_count": 0,
            "created_at": now,
            "updated_at": now,
        }
        try:
            result = await self.db.notification_delivery_logs.insert_one(payload)
            return result.inserted_id
        except Exception as exc:  # noqa: BLE001
            logger.warning("Unable to create notification delivery log: %s", exc)
            return None

    async def _finish_delivery_log(
        self,
        log_id: Optional[Any],
        status: NotificationDeliveryStatus,
        *,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        if not log_id:
            return
        now = datetime.utcnow()
        payload: Dict[str, Any] = {
            "status": status.value,
            "last_attempt_at": now,
            "updated_at": now,
            "error_code": error_code,
            "error_message": error_message,
        }
        if status in {NotificationDeliveryStatus.SENT, NotificationDeliveryStatus.FAILED}:
            payload["attempt_count"] = 1
        try:
            await self.db.notification_delivery_logs.update_one({"_id": log_id}, {"$set": payload})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Unable to update notification delivery log: %s", exc)

    async def send_test_notification_email(
        self,
        *,
        target_user_id: str,
        actor_id: str,
        event_type: str = "new_upload",
    ) -> Dict[str, Any]:
        from ..models.notification import NotificationType

        try:
            resolved_type = NotificationType(event_type)
        except ValueError:
            resolved_type = NotificationType.NEW_UPLOAD

        notification = Notification(
            type=resolved_type,
            category=NotificationCategory.UPLOADS,
            resource_id="notification-test",
            resource_type="system",
            recipients=[target_user_id],
            created_by=actor_id,
            title="Test notification email",
            message="This is a test notification email from ContraClaim DMS.",
            resource_link="/notifications",
            data={
                "title": "Test notification email",
                "message": "This is a test notification email from ContraClaim DMS.",
            },
        )
        sent = await self.send_immediate_notification(target_user_id, notification)
        latest = await self.db.notification_delivery_logs.find_one(
            {"user_id": target_user_id, "channel": "email"},
            sort=[("created_at", -1)],
        )
        if latest and "_id" in latest:
            latest["_id"] = str(latest["_id"])
        return {"sent": sent, "delivery_log": latest}

    def _env_smtp_config(self) -> Optional[SmtpRuntimeConfig]:
        return SmtpSettingsService(self.db).env_runtime_config()

    async def _send(self, message: EmailMessage, config: Optional[SmtpRuntimeConfig] = None) -> bool:
        config = config or self._env_smtp_config()
        if not config:
            return False
        return await SmtpSettingsService(self.db).send_message(message, config)

    async def send_contact_email(
        self,
        *,
        recipient: str,
        name: str,
        email: str,
        message_body: str,
        organization: Optional[str] = None,
        phone: Optional[str] = None,
    ) -> bool:
        """Send a public landing-page contact request to the configured recipient."""
        subject_name = re.sub(r"[\r\n]+", " ", name).strip()[:80] or "Website visitor"
        safe_recipient = re.sub(r"[\r\n]+", "", recipient).strip()
        safe_email = re.sub(r"[\r\n]+", "", email).strip()

        message = EmailMessage()
        message["Subject"] = f"ContraClaim DMS contact request from {subject_name}"
        message["From"] = self.from_email
        message["To"] = safe_recipient
        message["Reply-To"] = safe_email

        lines = [
            "A new contact request was submitted from the ContraClaim DMS landing page.",
            "",
            f"Name: {name}",
            f"Email: {safe_email}",
            f"Organization: {organization or 'Not provided'}",
            f"Phone: {phone or 'Not provided'}",
            "",
            "Message:",
            message_body,
        ]
        message.set_content("\n".join(lines))

        html_lines = [
            "<!doctype html><html><body>",
            "<h2>ContraClaim DMS contact request</h2>",
            "<p>A new contact request was submitted from the landing page.</p>",
            "<dl>",
            f"<dt>Name</dt><dd>{html.escape(name)}</dd>",
            f"<dt>Email</dt><dd>{html.escape(safe_email)}</dd>",
            f"<dt>Organization</dt><dd>{html.escape(organization or 'Not provided')}</dd>",
            f"<dt>Phone</dt><dd>{html.escape(phone or 'Not provided')}</dd>",
            "</dl>",
            "<h3>Message</h3>",
            f"<p>{html.escape(message_body).replace(chr(10), '<br>')}</p>",
            "</body></html>",
        ]
        message.add_alternative("".join(html_lines), subtype="html")
        return await self._send(message)

    async def fetch_document(self, document_id: str) -> Optional[Dict[str, Any]]:
        """Fetch a document by id, handling both ObjectId and UUID identifiers."""
        doc_id = _coerce_object_id(document_id)
        document = await self.db.documents.find_one({"_id": doc_id})
        if document and "_id" in document:
            document["_id"] = str(document["_id"])
        return document

    @staticmethod
    def share_token_hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def build_public_download_url(self, token: str) -> str:
        return f"{self.public_api_url}/email/public-share/{token}/download"

    async def create_document_share_token(
        self,
        *,
        document: Dict[str, Any],
        created_by: Optional[str],
        recipients: Optional[List[str]] = None,
        delivery_methods: Optional[Dict[str, bool]] = None,
    ) -> str:
        token = secrets.token_urlsafe(32)
        now = datetime.utcnow()
        expires_at = now + timedelta(days=max(1, self.share_token_ttl_days))
        document_id = str(document.get("_id") or document.get("id") or "")
        await self.db.document_share_tokens.insert_one(
            {
                "token_hash": self.share_token_hash(token),
                "document_id": document_id,
                "organization_id": document.get("organization_id") or document.get("organizationId"),
                "project_id": document.get("project_id") or document.get("projectId"),
                "created_by": created_by,
                "recipients": recipients or [],
                "delivery_methods": delivery_methods or {},
                "created_at": now,
                "expires_at": expires_at,
                "revoked_at": None,
                "access_count": 0,
                "last_accessed_at": None,
            }
        )
        return token

    async def resolve_public_share(self, token: str) -> Optional[Dict[str, Any]]:
        token_hash = self.share_token_hash(token)
        now = datetime.utcnow()
        share = await self.db.document_share_tokens.find_one(
            {
                "token_hash": token_hash,
                "revoked_at": None,
                "expires_at": {"$gt": now},
            }
        )
        if not share:
            return None
        document = await self.fetch_document(str(share.get("document_id")))
        if not document:
            return None
        await self.db.document_share_tokens.update_one(
            {"_id": share["_id"]},
            {
                "$inc": {"access_count": 1},
                "$set": {"last_accessed_at": now},
            },
        )
        return {"share": share, "document": document}

    async def read_document_file(self, document: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        filename = str(document.get("filename") or document.get("title") or "document")
        content_type = str(document.get("filetype") or "application/octet-stream")

        local_path = document.get("filepath_local")
        if local_path:
            path = Path(str(local_path))
            if path.exists() and path.is_file():
                self.file_object_service.assert_local_path_allowed(str(path))
                data = path.read_bytes()
                return {"content": data, "filename": filename, "content_type": content_type}

        for location in document.get("storage_locations") or []:
            if not isinstance(location, dict):
                continue
            provider = str(location.get("provider") or "").lower()
            location_path = location.get("path")
            if provider == "local" and location_path:
                path = Path(str(location_path))
                if path.exists() and path.is_file():
                    self.file_object_service.assert_local_path_allowed(str(path))
                    data = path.read_bytes()
                    return {"content": data, "filename": filename, "content_type": content_type}
            if provider == "s3" and location_path:
                data = await self.s3_service.download_bytes(str(location_path))
                return {"content": data, "filename": filename, "content_type": content_type}

        s3_key = document.get("filepath_s3")
        if s3_key:
            data = await self.s3_service.download_bytes(str(s3_key))
            return {"content": data, "filename": filename, "content_type": content_type}

        return None

    async def send_share_email(
        self,
        *,
        to: List[str],
        subject: str,
        html_body: Optional[str] = None,
        text_body: Optional[str] = None,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        organization_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> bool:
        """Send a document share email with optional HTML body."""
        if not to:
            logger.warning("Attempted to send share email without recipients")
            return False

        smtp_settings = SmtpSettingsService(self.db)
        candidates = await smtp_settings.resolve_candidates(
            organization_id=organization_id,
            project_id=project_id,
        )
        if not candidates:
            logger.info("SMTP disabled; skipping share email send")
            return False

        for config in candidates:
            message = self._build_share_message(
                to=to,
                cc=cc,
                bcc=bcc,
                subject=subject,
                from_header=config.sender_header,
                html_body=html_body,
                text_body=text_body,
                attachments=attachments,
            )
            if await self._send(message, config):
                logger.info("Share email sent using SMTP source=%s", config.source)
                return True
            logger.warning("Share email failed using SMTP source=%s; trying fallback if available", config.source)
        return False

    @staticmethod
    def _build_share_message(
        *,
        to: List[str],
        subject: str,
        from_header: str,
        html_body: Optional[str] = None,
        text_body: Optional[str] = None,
        cc: Optional[List[str]] = None,
        bcc: Optional[List[str]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> EmailMessage:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = from_header
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

        for attachment in attachments or []:
            content = attachment.get("content")
            filename = attachment.get("filename") or "attachment"
            content_type = attachment.get("content_type") or mimetypes.guess_type(filename)[0] or "application/octet-stream"
            if not isinstance(content, (bytes, bytearray)):
                continue
            maintype, _, subtype = content_type.partition("/")
            message.add_attachment(
                bytes(content),
                maintype=maintype or "application",
                subtype=subtype or "octet-stream",
                filename=filename,
            )

        return message


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
