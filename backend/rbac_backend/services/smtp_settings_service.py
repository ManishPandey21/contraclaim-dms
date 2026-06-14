from __future__ import annotations

import base64
import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from typing import Any, Dict, Iterable, List, Optional

from bson import ObjectId
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

try:  # pragma: no cover - optional dependency
    import aiosmtplib
except Exception:  # noqa: BLE001
    aiosmtplib = None

from ..core.config import settings
from ..core.security import CurrentUser
from ..models.smtp_settings import (
    SmtpSettingsResponse,
    SmtpSettingsUpdate,
    SmtpSettingsUpsert,
    normalize_encryption,
)

logger = logging.getLogger(__name__)


@dataclass
class SmtpRuntimeConfig:
    source: str
    host: str
    port: int
    username: Optional[str]
    password: Optional[str]
    sender_email: str
    sender_name: Optional[str]
    encryption: str

    @property
    def sender_header(self) -> str:
        if self.sender_name:
            return f"{self.sender_name} <{self.sender_email}>"
        return self.sender_email

    @property
    def login_required(self) -> bool:
        return bool(self.username and self.password)


def _coerce_object_id(value: str) -> ObjectId | str:
    try:
        return ObjectId(str(value))
    except Exception:  # noqa: BLE001
        return str(value)


def _expand_id(value: str) -> List[Any]:
    expanded: List[Any] = [str(value)]
    try:
        expanded.append(ObjectId(str(value)))
    except Exception:  # noqa: BLE001
        pass
    return expanded


def _role_names(current_user: CurrentUser) -> set[str]:
    return {str(role).lower() for role in (getattr(current_user, "roles", []) or [])}


class SmtpSettingsService:
    def __init__(self, db: AsyncIOMotorDatabase) -> None:
        self.db = db
        self.collection = getattr(db, "smtp_settings", None)
        if self.collection is None:
            try:
                self.collection = db["smtp_settings"]
            except Exception:  # noqa: BLE001
                self.collection = None
        self._fernet = Fernet(self._encryption_key())

    @staticmethod
    def _encryption_key() -> bytes:
        configured = os.getenv("SMTP_SETTINGS_ENCRYPTION_KEY", "").strip()
        if configured:
            try:
                Fernet(configured.encode("utf-8"))
                return configured.encode("utf-8")
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError("SMTP_SETTINGS_ENCRYPTION_KEY must be a valid Fernet key") from exc
        material = str(settings.SECRET_KEY or "contraclaim-smtp-settings").encode("utf-8")
        return base64.urlsafe_b64encode(hashlib.sha256(material).digest())

    def encrypt_password(self, password: Optional[str]) -> Optional[str]:
        if password is None:
            return None
        return self._fernet.encrypt(password.encode("utf-8")).decode("utf-8")

    def decrypt_password(self, encrypted_password: Optional[str]) -> Optional[str]:
        if not encrypted_password:
            return None
        try:
            return self._fernet.decrypt(str(encrypted_password).encode("utf-8")).decode("utf-8")
        except InvalidToken:
            logger.warning("Unable to decrypt SMTP password")
            return None

    async def get_project_org_id(self, project_id: str) -> str:
        project = await self.db.projects.find_one({"_id": {"$in": _expand_id(project_id)}})
        if not project:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
        org_id = project.get("organization_id") or project.get("organizationId")
        if not org_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Project is missing organization scope",
            )
        return str(org_id)

    def _ensure_manage_org(self, current_user: CurrentUser, organization_id: str) -> None:
        roles = _role_names(current_user)
        if "superadmin" in roles:
            return
        if "orgadmin" not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization admin role required")
        if str(getattr(current_user, "organization_id", "")) != str(organization_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized for this organization")

    async def ensure_manage_organization(self, current_user: CurrentUser, organization_id: str) -> None:
        self._ensure_manage_org(current_user, organization_id)

    async def ensure_manage_project(self, current_user: CurrentUser, project_id: str) -> str:
        org_id = await self.get_project_org_id(project_id)
        self._ensure_manage_org(current_user, org_id)
        return org_id

    def _query_organization(self, organization_id: str) -> Dict[str, Any]:
        return {
            "scope_type": "organization",
            "organization_id": {"$in": [str(organization_id), *_expand_id(organization_id)]},
            "project_id": None,
        }

    def _query_project(self, project_id: str) -> Dict[str, Any]:
        return {
            "scope_type": "project",
            "project_id": {"$in": [str(project_id), *_expand_id(project_id)]},
        }

    @staticmethod
    def _response(doc: Optional[Dict[str, Any]]) -> Optional[SmtpSettingsResponse]:
        if not doc:
            return None
        payload = dict(doc)
        if payload.get("_id") is not None:
            payload["_id"] = str(payload["_id"])
        if payload.get("organization_id") is not None:
            payload["organization_id"] = str(payload["organization_id"])
        if payload.get("project_id") is not None:
            payload["project_id"] = str(payload["project_id"])
        payload["password_configured"] = bool(payload.get("encrypted_password"))
        payload.pop("encrypted_password", None)
        return SmtpSettingsResponse.model_validate(payload)

    async def get_organization_settings(
        self, organization_id: str, current_user: Optional[CurrentUser] = None
    ) -> Optional[SmtpSettingsResponse]:
        if current_user:
            await self.ensure_manage_organization(current_user, organization_id)
        if self.collection is None:
            return None
        doc = await self.collection.find_one(self._query_organization(organization_id))
        return self._response(doc)

    async def get_project_settings(
        self, project_id: str, current_user: Optional[CurrentUser] = None
    ) -> Optional[SmtpSettingsResponse]:
        if current_user:
            await self.ensure_manage_project(current_user, project_id)
        if self.collection is None:
            return None
        doc = await self.collection.find_one(self._query_project(project_id))
        return self._response(doc)

    async def upsert_organization_settings(
        self,
        organization_id: str,
        payload: SmtpSettingsUpsert | SmtpSettingsUpdate,
        current_user: CurrentUser,
    ) -> SmtpSettingsResponse:
        await self.ensure_manage_organization(current_user, organization_id)
        return await self._upsert(
            query=self._query_organization(organization_id),
            base={"scope_type": "organization", "organization_id": str(organization_id), "project_id": None},
            payload=payload,
            current_user=current_user,
        )

    async def upsert_project_settings(
        self,
        project_id: str,
        payload: SmtpSettingsUpsert | SmtpSettingsUpdate,
        current_user: CurrentUser,
    ) -> SmtpSettingsResponse:
        org_id = await self.ensure_manage_project(current_user, project_id)
        return await self._upsert(
            query=self._query_project(project_id),
            base={"scope_type": "project", "organization_id": str(org_id), "project_id": str(project_id)},
            payload=payload,
            current_user=current_user,
        )

    async def _upsert(
        self,
        *,
        query: Dict[str, Any],
        base: Dict[str, Any],
        payload: SmtpSettingsUpsert | SmtpSettingsUpdate,
        current_user: CurrentUser,
    ) -> SmtpSettingsResponse:
        if self.collection is None:
            raise HTTPException(status_code=500, detail="SMTP settings collection is unavailable")
        existing = await self.collection.find_one(query)
        if not existing and isinstance(payload, SmtpSettingsUpdate):
            missing = {"host", "port", "username", "password", "sender_email"} - set(payload.model_fields_set)
            if missing:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Missing required fields for new SMTP settings: {', '.join(sorted(missing))}",
                )

        data = payload.model_dump(exclude_unset=True, exclude_none=True)
        password = data.pop("password", None)
        if "encryption" in data:
            data["encryption"] = normalize_encryption(data["encryption"])
        now = datetime.utcnow()
        data["updated_at"] = now
        data["updated_by"] = str(current_user.id)
        if password is not None:
            data["encrypted_password"] = self.encrypt_password(password)

        update = {
            "$set": data,
            "$setOnInsert": {
                **base,
                "created_at": now,
                "created_by": str(current_user.id),
            },
        }
        saved = await self.collection.find_one_and_update(
            query,
            update,
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        response = self._response(saved)
        if not response:
            raise HTTPException(status_code=500, detail="Failed to save SMTP settings")
        return response

    def _runtime_from_doc(self, source: str, doc: Optional[Dict[str, Any]]) -> Optional[SmtpRuntimeConfig]:
        active = doc.get("is_active", doc.get("active", True)) if doc else False
        if not doc or not active:
            return None
        host = str(doc.get("host") or doc.get("smtp_host") or "").strip()
        sender_email = str(doc.get("sender_email") or "").strip()
        if not host or not sender_email:
            logger.warning("Skipping incomplete SMTP settings source=%s", source)
            return None
        password = self.decrypt_password(doc.get("encrypted_password") or doc.get("encrypted_secret"))
        username = doc.get("username")
        if username and (doc.get("encrypted_password") or doc.get("encrypted_secret")) and not password:
            logger.warning("Skipping SMTP settings with undecryptable password source=%s", source)
            return None
        return SmtpRuntimeConfig(
            source=source,
            host=host,
            port=int(doc.get("port") or doc.get("smtp_port") or 587),
            username=str(username).strip() if username else None,
            password=password,
            sender_email=sender_email,
            sender_name=str(doc.get("sender_name") or "").strip() or None,
            encryption=normalize_encryption(doc.get("encryption")),
        )

    def env_runtime_config(self) -> Optional[SmtpRuntimeConfig]:
        host = os.getenv("SMTP_HOST") or settings.SMTP_HOST
        username = os.getenv("SMTP_USER") or os.getenv("SMTP_USERNAME") or settings.SMTP_USERNAME
        password = os.getenv("SMTP_PASSWORD") or settings.SMTP_PASSWORD
        sender_email = (
            os.getenv("FROM_EMAIL")
            or os.getenv("SMTP_FROM_EMAIL")
            or settings.SMTP_FROM_EMAIL
            or "noreply@contraclaim.com"
        )
        encryption = os.getenv("SMTP_ENCRYPTION") or os.getenv("SMTP_ENCRYPTION_TYPE") or "starttls"
        if not host or not sender_email:
            return None
        return SmtpRuntimeConfig(
            source="env",
            host=str(host),
            port=int(os.getenv("SMTP_PORT") or settings.SMTP_PORT or 587),
            username=str(username).strip() if username else None,
            password=str(password) if password else None,
            sender_email=str(sender_email),
            sender_name=os.getenv("SMTP_FROM_NAME") or os.getenv("FROM_NAME") or None,
            encryption=normalize_encryption(encryption),
        )

    async def resolve_candidates(
        self,
        *,
        organization_id: Optional[str],
        project_id: Optional[str],
    ) -> List[SmtpRuntimeConfig]:
        candidates: List[SmtpRuntimeConfig] = []
        if self.collection is None:
            env_config = self.env_runtime_config()
            return [env_config] if env_config else []
        if project_id:
            project_doc = await self.collection.find_one(self._query_project(str(project_id)))
            project_config = self._runtime_from_doc("project", project_doc)
            if project_config:
                candidates.append(project_config)
        if organization_id:
            org_doc = await self.collection.find_one(self._query_organization(str(organization_id)))
            org_config = self._runtime_from_doc("organization", org_doc)
            if org_config:
                candidates.append(org_config)
        env_config = self.env_runtime_config()
        if env_config:
            candidates.append(env_config)
        return candidates

    async def send_message(self, message: EmailMessage, config: SmtpRuntimeConfig) -> bool:
        if not aiosmtplib:
            logger.warning("SMTP dependency unavailable; cannot send email source=%s", config.source)
            return False
        smtp = None
        try:
            use_tls = config.encryption == "ssl_tls"
            start_tls = config.encryption == "starttls"
            smtp = aiosmtplib.SMTP(
                hostname=config.host,
                port=config.port,
                use_tls=use_tls,
                start_tls=start_tls,
            )
            await smtp.connect()
            if config.login_required:
                await smtp.login(config.username, config.password)
            await smtp.send_message(message)
            await smtp.quit()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "SMTP send failed source=%s host=%s port=%s encryption=%s error=%s",
                config.source,
                config.host,
                config.port,
                config.encryption,
                exc,
            )
            try:
                if smtp:
                    await smtp.quit()
            except Exception:  # noqa: BLE001
                pass
            return False

    async def test_connection(self, config: SmtpRuntimeConfig) -> tuple[bool, str]:
        if not aiosmtplib:
            return False, "SMTP dependency is not available"
        smtp = None
        try:
            smtp = aiosmtplib.SMTP(
                hostname=config.host,
                port=config.port,
                use_tls=config.encryption == "ssl_tls",
                start_tls=config.encryption == "starttls",
            )
            await smtp.connect()
            if config.login_required:
                await smtp.login(config.username, config.password)
            await smtp.noop()
            await smtp.quit()
            return True, "SMTP connection successful"
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "SMTP connection test failed source=%s host=%s port=%s encryption=%s error=%s",
                config.source,
                config.host,
                config.port,
                config.encryption,
                exc,
            )
            try:
                if smtp:
                    await smtp.quit()
            except Exception:  # noqa: BLE001
                pass
            return False, str(exc)

    async def runtime_for_organization(self, organization_id: str) -> Optional[SmtpRuntimeConfig]:
        candidates = await self.resolve_candidates(organization_id=organization_id, project_id=None)
        return candidates[0] if candidates else None

    async def runtime_for_project(self, project_id: str) -> Optional[SmtpRuntimeConfig]:
        org_id = await self.get_project_org_id(project_id)
        candidates = await self.resolve_candidates(organization_id=org_id, project_id=project_id)
        return candidates[0] if candidates else None
