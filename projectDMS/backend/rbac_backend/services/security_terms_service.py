from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status
from pymongo.errors import DuplicateKeyError

from ..models.security_terms import SecurityTermsAcceptRequest, SecurityTermsVersionCreate
from .audit_event_service import AuditEventService


DEFAULT_TERMS_VERSION = "2026.1"
DEFAULT_TERMS_TITLE = "Security, Privacy & Anti-Piracy Terms"
DEFAULT_TERMS_BODY = """Security, Privacy & Anti-Piracy Terms

By using Contraclaim DMS, you agree to use the platform only for lawful contract administration, claims management, document management, and related business purposes.

Security obligations:
1. Keep your login credentials confidential and do not share accounts.
2. Access only the organizations, projects, contracts, documents, registers, reports, and AI outputs you are authorized to use.
3. Do not attempt to bypass role-based access controls, subscription controls, storage controls, audit logging, rate limits, or security monitoring.
4. Report suspected unauthorized access, data leakage, malware, phishing, or account compromise promptly to your organization administrator.

Privacy obligations:
1. Upload and process only documents and personal data that your organization is authorized to handle.
2. Do not export, share, disclose, or reuse project data outside approved business, contractual, legal, or regulatory purposes.
3. Review AI-generated material before reliance. The system may assist with drafting, search, appraisal, chronology, and evidence organization, but users remain responsible for validation and lawful use.
4. Preserve confidentiality of client, contractor, employer, consultant, expert, tribunal, project, commercial, and personal information.

Anti-piracy and acceptable-use obligations:
1. Do not upload pirated, stolen, unlawfully copied, or unauthorized copyrighted material.
2. Do not use Contraclaim DMS to create, distribute, conceal, or facilitate infringement, counterfeit records, falsified evidence, or unauthorized copies.
3. Do not reverse engineer, scrape, resell, sublicense, or make unauthorized automated use of the platform or its generated outputs.
4. Do not remove proprietary notices or misuse templates, workflows, models, reports, or exports outside licensed use.

Evidence and legal workflow notice:
1. Maintain accurate source documents, chronology, references, claims, registers, correspondence, and approvals.
2. Mark unsupported assertions as requiring evidence and do not knowingly submit fabricated facts, dates, clauses, amounts, or documents.
3. Legal or contractual outputs should be reviewed by authorized professionals before external submission or reliance.

Acceptance is recorded with your user, organization, version, timestamp, IP address, browser user agent, terms hash, and acceptance method. Continued use requires acceptance of the active version. If these terms are updated, you must accept the new active version before accessing the dashboard and application modules.
"""


class SecurityTermsService:
    def __init__(self, db: Any) -> None:
        self.db = db
        self.audit = AuditEventService(db)

    @staticmethod
    def terms_hash(*, version: str, effective_date: datetime, body: str) -> str:
        material = "\n".join(
            [
                str(version or "").strip(),
                effective_date.isoformat(),
                "\n".join(str(body or "").replace("\r\n", "\n").replace("\r", "\n").splitlines()),
            ]
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    @staticmethod
    def _org_id(current_user: Any) -> Optional[str]:
        org = getattr(current_user, "organization_id", None)
        return str(org) if org else None

    @staticmethod
    def _user_id(current_user: Any) -> str:
        return str(getattr(current_user, "id", "") or "")

    @staticmethod
    def _acceptance_id(*, user_id: str, org_id: Optional[str], version: str, terms_hash: str) -> str:
        """Derive ``_id`` from the same natural key the lookup and unique index use.

        Acceptance identity is per (user, org, version, hash). Deriving ``_id``
        from a narrower key made a second acceptance under a different org scope
        miss the org-scoped lookup and then collide on insert.
        """
        material = "|".join([user_id, org_id or "", version, terms_hash])
        return f"terms-accept-{hashlib.sha1(material.encode('utf-8')).hexdigest()[:24]}"

    @staticmethod
    def _normalize_version(doc: Dict[str, Any]) -> Dict[str, Any]:
        doc = dict(doc)
        if "_id" not in doc and "id" in doc:
            doc["_id"] = doc["id"]
        return doc

    async def ensure_default_active(self) -> Dict[str, Any]:
        active = await self.db.terms_versions.find_one({"is_active": True})
        if active:
            return self._normalize_version(active)

        existing = await self.db.terms_versions.find_one({"version": DEFAULT_TERMS_VERSION})
        if existing:
            await self.db.terms_versions.update_one(
                {"_id": existing["_id"]},
                {"$set": {"is_active": True, "activated_at": datetime.utcnow(), "activated_by": "system"}},
            )
            refreshed = await self.db.terms_versions.find_one({"_id": existing["_id"]}) or existing
            refreshed["is_active"] = True
            return self._normalize_version(refreshed)

        effective = datetime(2026, 6, 30)
        payload = SecurityTermsVersionCreate(
            version=DEFAULT_TERMS_VERSION,
            title=DEFAULT_TERMS_TITLE,
            body=DEFAULT_TERMS_BODY,
            effective_date=effective,
            is_active=True,
        )
        doc = {
            "_id": "security-terms-default-2026-1",
            **payload.model_dump(),
            "terms_hash": self.terms_hash(version=payload.version, effective_date=payload.effective_date, body=payload.body),
            "created_at": datetime.utcnow(),
            "created_by": "system",
            "activated_at": datetime.utcnow(),
            "activated_by": "system",
        }
        await self.db.terms_versions.insert_one(doc)
        return self._normalize_version(doc)

    async def active_version(self) -> Dict[str, Any]:
        return await self.ensure_default_active()

    async def list_versions(self) -> List[Dict[str, Any]]:
        cursor = self.db.terms_versions.find({}).sort("created_at", -1)
        return [self._normalize_version(doc) async for doc in cursor]

    async def create_version(self, payload: SecurityTermsVersionCreate, current_user: Any) -> Dict[str, Any]:
        existing = await self.db.terms_versions.find_one({"version": payload.version})
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Terms version already exists")

        now = datetime.utcnow()
        doc = {
            "_id": f"terms-{hashlib.sha1((payload.version + str(now.timestamp())).encode('utf-8')).hexdigest()[:24]}",
            **payload.model_dump(),
            "terms_hash": self.terms_hash(version=payload.version, effective_date=payload.effective_date, body=payload.body),
            "created_at": now,
            "created_by": self._user_id(current_user),
            "activated_at": now if payload.is_active else None,
            "activated_by": self._user_id(current_user) if payload.is_active else None,
        }
        if payload.is_active:
            await self.db.terms_versions.update_many({"is_active": True}, {"$set": {"is_active": False}})
        await self.db.terms_versions.insert_one(doc)
        await self.audit.emit(
            action="security_terms.version.created",
            actor_id=self._user_id(current_user),
            resource_type="security_terms_version",
            resource_id=doc["_id"],
            organization_id=self._org_id(current_user),
            after={"version": doc["version"], "is_active": doc["is_active"], "terms_hash": doc["terms_hash"]},
        )
        if payload.is_active:
            await self.audit.emit(
                action="security_terms.version.activated",
                actor_id=self._user_id(current_user),
                resource_type="security_terms_version",
                resource_id=doc["_id"],
                organization_id=self._org_id(current_user),
                after={"version": doc["version"], "terms_hash": doc["terms_hash"]},
            )
        return self._normalize_version(doc)

    async def activate_version(self, version_id: str, current_user: Any) -> Dict[str, Any]:
        doc = await self.db.terms_versions.find_one({"_id": version_id})
        if not doc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Terms version not found")
        now = datetime.utcnow()
        await self.db.terms_versions.update_many({"is_active": True}, {"$set": {"is_active": False}})
        await self.db.terms_versions.update_one(
            {"_id": version_id},
            {"$set": {"is_active": True, "activated_at": now, "activated_by": self._user_id(current_user)}},
        )
        updated = await self.db.terms_versions.find_one({"_id": version_id}) or doc
        updated["is_active"] = True
        updated["activated_at"] = now
        updated["activated_by"] = self._user_id(current_user)
        await self.audit.emit(
            action="security_terms.version.activated",
            actor_id=self._user_id(current_user),
            resource_type="security_terms_version",
            resource_id=version_id,
            organization_id=self._org_id(current_user),
            after={"version": updated.get("version"), "terms_hash": updated.get("terms_hash")},
        )
        return self._normalize_version(updated)

    async def accepted_for_active(self, current_user: Any, active: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        active = active or await self.active_version()
        user_id = self._user_id(current_user)
        if not user_id:
            return None
        org_id = self._org_id(current_user)
        accepted = await self.db.security_terms_acceptances.find_one(
            {
                "user_id": user_id,
                "org_id": org_id,
                "terms_version": active.get("version"),
                "terms_hash": active.get("terms_hash"),
            }
        )
        return dict(accepted) if accepted else None

    async def status_for_user(self, current_user: Any) -> Dict[str, Any]:
        active = await self.active_version()
        accepted = await self.accepted_for_active(current_user, active)
        return {
            "requires_acceptance": accepted is None,
            "active_version": active,
            "accepted_acceptance": accepted,
        }

    async def accept_active(
        self,
        current_user: Any,
        request: SecurityTermsAcceptRequest,
        *,
        ip_address: Optional[str],
        user_agent: Optional[str],
    ) -> Dict[str, Any]:
        if not request.accepted:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Terms must be accepted")
        active = await self.active_version()
        existing = await self.accepted_for_active(current_user, active)
        now = datetime.utcnow()
        doc = {
            "user_id": self._user_id(current_user),
            "org_id": self._org_id(current_user),
            "terms_version": active["version"],
            "accepted_at": now,
            "ip_address": ip_address,
            "user_agent": user_agent,
            "terms_hash": active["terms_hash"],
            "acceptance_method": request.acceptance_method or "checkbox_accept_continue",
        }
        if existing:
            doc["_id"] = existing["_id"]
            await self.db.security_terms_acceptances.update_one({"_id": existing["_id"]}, {"$set": doc})
        else:
            doc["_id"] = self._acceptance_id(
                user_id=doc["user_id"],
                org_id=doc["org_id"],
                version=doc["terms_version"],
                terms_hash=doc["terms_hash"],
            )
            try:
                await self.db.security_terms_acceptances.insert_one(doc)
            except DuplicateKeyError:
                # Concurrent accepts: the row landed between the lookup and this
                # insert. Acceptance is idempotent, so record it rather than 500.
                await self.db.security_terms_acceptances.update_one(
                    {
                        "user_id": doc["user_id"],
                        "org_id": doc["org_id"],
                        "terms_version": doc["terms_version"],
                        "terms_hash": doc["terms_hash"],
                    },
                    {"$set": {key: value for key, value in doc.items() if key != "_id"}},
                )
        await self.audit.emit(
            action="security_terms.accepted",
            actor_id=doc["user_id"],
            resource_type="security_terms_acceptance",
            resource_id=doc["_id"],
            organization_id=doc.get("org_id"),
            after={
                "terms_version": doc["terms_version"],
                "terms_hash": doc["terms_hash"],
                "acceptance_method": doc["acceptance_method"],
            },
        )
        return doc

    async def list_acceptances_for_user(self, current_user: Any) -> List[Dict[str, Any]]:
        query = {"user_id": self._user_id(current_user), "org_id": self._org_id(current_user)}
        cursor = self.db.security_terms_acceptances.find(query).sort("accepted_at", -1)
        return [dict(doc) async for doc in cursor]
