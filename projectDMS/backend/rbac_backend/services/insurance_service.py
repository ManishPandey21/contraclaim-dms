"""Insurance Register service.

Pure date logic (status, days-remaining, summary, expiry-alert rules) plus CRUD,
duplicate-policy guard, the daily expiry-alert scan, and the admin-managed
Insurance Type master (lazily seeded per organization). Authorization is the
router's job via PolicyService. Mirrors bank_guarantee_service.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, status

from ..core.database import get_database
from ..models.insurance import (
    DEFAULT_INSURANCE_TYPES,
    EXPIRING_SOON_DAYS,
    Insurance,
    InsuranceCreate,
    InsuranceStatus,
    InsuranceType,
    InsuranceTypeCreate,
)
from .audit_event_service import AuditEventService

logger = logging.getLogger(__name__)

# Days before expiry that fire a reminder, plus on-expiry (0). 'expired' fires once past.
ALERT_THRESHOLDS = (90, 60, 30, 15, 7, 0)


def _as_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def days_remaining(ins: Dict[str, Any], now: Optional[datetime] = None) -> Optional[int]:
    now = now or datetime.utcnow()
    exp = _as_dt(ins.get("date_of_expiry"))
    return (exp.date() - now.date()).days if exp else None


def status_of(ins: Dict[str, Any], now: Optional[datetime] = None) -> Optional[str]:
    d = days_remaining(ins, now)
    if d is None:
        return None
    if d < 0:
        return InsuranceStatus.EXPIRED.value
    if d <= EXPIRING_SOON_DAYS:
        return InsuranceStatus.EXPIRING_SOON.value
    return InsuranceStatus.ACTIVE.value


def next_alert_date(ins: Dict[str, Any]) -> Optional[datetime]:
    """The earliest upcoming reminder date (90 days before expiry)."""
    exp = _as_dt(ins.get("date_of_expiry"))
    if exp is None:
        return None
    return exp - timedelta(days=ALERT_THRESHOLDS[0])


def due_alert_types(ins: Dict[str, Any], now: Optional[datetime] = None) -> List[str]:
    """Alert tags firing today: T-90/T-60/T-30/T-15/T-7, 'expiry' on the day, or
    'expired' once past."""
    d = days_remaining(ins, now)
    if d is None:
        return []
    if d < 0:
        return ["expired"]
    if d == 0:
        return ["expiry"]
    if d in ALERT_THRESHOLDS:
        return [f"T-{d}"]
    return []


def decorate(ins: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    b = dict(ins)
    b["status"] = status_of(b, now)
    b["days_remaining"] = days_remaining(b, now)
    b["next_alert_date"] = next_alert_date(b)
    return b


def insurance_summary(items: List[Dict[str, Any]], now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.utcnow()
    out = {k: 0 for k in ("total", "active", "expiring_soon", "expired", "missing")}
    total_sum = 0.0
    for ins in items:
        out["total"] += 1
        total_sum += float(ins.get("sum_insured") or 0.0)
        st = status_of(ins, now)
        if st == InsuranceStatus.EXPIRED.value:
            out["expired"] += 1
        elif st == InsuranceStatus.EXPIRING_SOON.value:
            out["expiring_soon"] += 1
        elif st == InsuranceStatus.ACTIVE.value:
            out["active"] += 1
    # "missing" requires a per-contract required-coverage config that does not
    # exist yet; surfaced as 0 until that master is added.
    out["total_sum_insured"] = round(total_sum, 2)
    return out


class InsuranceService:
    def __init__(self, db: Any = None) -> None:
        self.db = db
        self.audit = AuditEventService(db)

    async def _get_db(self) -> Any:
        return self.db if self.db is not None else await get_database()

    async def _assert_no_duplicate(
        self, db: Any, *, project_id: Any, contract_id: Any, insurance_type: Any,
        policy_number: Any, exclude_id: Optional[str] = None,
    ) -> None:
        """No duplicate policy number for the same contract + insurance type."""
        if not policy_number:
            return
        query: Dict[str, Any] = {
            "project_id": project_id,
            "contract_id": contract_id or "primary",
            "insurance_type": insurance_type,
            "policy_number": policy_number,
        }
        existing = await db.insurance_policies.find_one(query)
        if existing and str(existing.get("_id")) != str(exclude_id or ""):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A policy with this number already exists for this contract and insurance type",
            )

    async def create(self, payload: InsuranceCreate, current_user: Any) -> Dict[str, Any]:
        db = await self._get_db()
        doc = Insurance(**payload.model_dump()).model_dump(by_alias=True)
        if not doc.get("organization_id"):
            doc["organization_id"] = getattr(current_user, "organization_id", None)
        if not doc.get("contract_id"):
            doc["contract_id"] = "primary"
        await self._assert_no_duplicate(
            db, project_id=doc.get("project_id"), contract_id=doc.get("contract_id"),
            insurance_type=doc.get("insurance_type"), policy_number=doc.get("policy_number"),
        )
        doc["created_at"] = datetime.utcnow()
        doc["created_by"] = getattr(current_user, "id", None)
        doc["created_by_name"] = _actor_name(current_user)
        res = await db.insurance_policies.insert_one(doc)
        created = await db.insurance_policies.find_one({"_id": res.inserted_id}) or doc
        await self._emit("insurance.created", current_user, created, after=created)
        return decorate(created)

    async def get(self, insurance_id: str) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        b = await db.insurance_policies.find_one({"_id": insurance_id})
        return decorate(b) if b else None

    async def list(
        self, scope_filter: Dict[str, Any], *, project_id: Optional[str] = None,
        contract_id: Optional[str] = None, insurance_type: Optional[str] = None,
        insurance_company: Optional[str] = None, status: Optional[str] = None,
        uploaded_by: Optional[str] = None, q: Optional[str] = None,
        skip: int = 0, limit: int = 500,
    ) -> List[Dict[str, Any]]:
        db = await self._get_db()
        query: Dict[str, Any] = dict(scope_filter or {})
        for k, v in (
            ("project_id", project_id), ("contract_id", contract_id),
            ("insurance_type", insurance_type), ("insurance_company", insurance_company),
            ("created_by", uploaded_by),
        ):
            if v:
                query[k] = v
        if q:
            rx = {"$regex": str(q), "$options": "i"}
            query["$or"] = [
                {"contract_id": rx}, {"contractor_name": rx}, {"policy_number": rx},
                {"insurance_type": rx}, {"insurance_company": rx},
            ]
        cursor = db.insurance_policies.find(query).sort("date_of_expiry", 1).skip(skip).limit(limit)
        rows = [decorate(b) async for b in cursor]
        if status:
            rows = [r for r in rows if r.get("status") == status]
        return rows

    async def update(self, ins: Dict[str, Any], payload: Dict[str, Any], current_user: Any) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        update = {k: v for k, v in payload.items() if v is not None and k != "contract_id_set"}
        # Re-check the duplicate constraint when an identifying field changes.
        if any(k in update for k in ("policy_number", "insurance_type", "contract_id")):
            await self._assert_no_duplicate(
                db,
                project_id=ins.get("project_id"),
                contract_id=update.get("contract_id", ins.get("contract_id")),
                insurance_type=update.get("insurance_type", ins.get("insurance_type")),
                policy_number=update.get("policy_number", ins.get("policy_number")),
                exclude_id=ins.get("_id"),
            )
        update["updated_at"] = datetime.utcnow()
        update["updated_by"] = getattr(current_user, "id", None)
        updated = await db.insurance_policies.find_one_and_update(
            {"_id": ins["_id"]}, {"$set": update}, return_document=True
        )
        await self._emit("insurance.updated", current_user, ins, before=ins, after=updated)
        return decorate(updated or ins)

    async def replace_file(self, ins: Dict[str, Any], document_id: str, current_user: Any) -> Dict[str, Any]:
        db = await self._get_db()
        prev = ins.get("document_id")
        await db.insurance_policies.update_one(
            {"_id": ins["_id"]},
            {"$set": {"document_id": document_id, "updated_at": datetime.utcnow(),
                      "updated_by": getattr(current_user, "id", None)}},
        )
        await self._emit("insurance.file_replaced", current_user, ins, before={"document_id": prev}, after={"document_id": document_id})
        return decorate(await db.insurance_policies.find_one({"_id": ins["_id"]}) or ins)

    async def delete(self, ins: Dict[str, Any], current_user: Any) -> bool:
        db = await self._get_db()
        res = await db.insurance_policies.delete_one({"_id": ins["_id"]})
        await self._emit("insurance.deleted", current_user, ins, before=ins)
        return res.deleted_count > 0

    async def summary(self, scope_filter: Dict[str, Any], *, project_id: Optional[str] = None) -> Dict[str, Any]:
        db = await self._get_db()
        query: Dict[str, Any] = dict(scope_filter or {})
        if project_id:
            query["project_id"] = project_id
        items = [b async for b in db.insurance_policies.find(query)]
        return insurance_summary(items)

    async def alerts(self, scope_filter: Dict[str, Any], now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        db = await self._get_db()
        items = [b async for b in db.insurance_policies.find(dict(scope_filter or {}))]
        out = []
        for b in items:
            tags = due_alert_types(b, now)
            if tags:
                out.append({**decorate(b, now), "alert": tags[0]})
        return out

    async def _emit(self, action: str, current_user: Any, ins: Dict[str, Any], *, before: Any = None, after: Any = None) -> None:
        await self.audit.emit(
            action=action, actor_id=getattr(current_user, "id", None),
            resource_type="insurance", resource_id=str(ins.get("_id")),
            organization_id=ins.get("organization_id"), project_id=ins.get("project_id"),
            before=before, after=after,
        )


def _actor_name(current_user: Any) -> Optional[str]:
    parts = [getattr(current_user, "first_name", None), getattr(current_user, "last_name", None)]
    name = " ".join(p for p in parts if p).strip()
    return name or getattr(current_user, "username", None) or getattr(current_user, "email", None)


# --- Insurance Type master (admin-managed, lazily seeded) ------------------


class InsuranceTypeService:
    def __init__(self, db: Any = None) -> None:
        self.db = db
        self.audit = AuditEventService(db)

    async def _get_db(self) -> Any:
        return self.db if self.db is not None else await get_database()

    async def _ensure_seeded(self, db: Any, organization_id: Optional[str]) -> None:
        existing = await db.insurance_types.find_one({"organization_id": organization_id})
        if existing:
            return
        now = datetime.utcnow()
        for name in DEFAULT_INSURANCE_TYPES:
            doc = InsuranceType(
                name=name, organization_id=organization_id, is_default=True, created_at=now,
            ).model_dump(by_alias=True)
            try:
                await db.insurance_types.insert_one(doc)
            except Exception:  # pragma: no cover - tolerate concurrent first-seed
                pass

    async def list(self, organization_id: Optional[str], *, include_inactive: bool = False) -> List[Dict[str, Any]]:
        db = await self._get_db()
        await self._ensure_seeded(db, organization_id)
        query: Dict[str, Any] = {"organization_id": organization_id}
        if not include_inactive:
            query["is_active"] = True
        return [t async for t in db.insurance_types.find(query).sort("name", 1)]

    async def get(self, type_id: str) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        return await db.insurance_types.find_one({"_id": type_id})

    async def create(self, payload: InsuranceTypeCreate, current_user: Any) -> Dict[str, Any]:
        db = await self._get_db()
        doc = InsuranceType(**payload.model_dump()).model_dump(by_alias=True)
        if not doc.get("organization_id"):
            doc["organization_id"] = getattr(current_user, "organization_id", None)
        doc["created_by"] = getattr(current_user, "id", None)
        res = await db.insurance_types.insert_one(doc)
        created = await db.insurance_types.find_one({"_id": res.inserted_id}) or doc
        await self._emit("insurance_type.created", current_user, created, after=created)
        return created

    async def update(self, type_doc: Dict[str, Any], payload: Dict[str, Any], current_user: Any) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        update = {k: v for k, v in payload.items() if v is not None}
        update["updated_at"] = datetime.utcnow()
        update["updated_by"] = getattr(current_user, "id", None)
        updated = await db.insurance_types.find_one_and_update(
            {"_id": type_doc["_id"]}, {"$set": update}, return_document=True
        )
        await self._emit("insurance_type.updated", current_user, type_doc, before=type_doc, after=updated)
        return updated or type_doc

    async def delete(self, type_doc: Dict[str, Any], current_user: Any) -> bool:
        """Soft-delete: deactivate so historical policies keep their type label."""
        db = await self._get_db()
        await db.insurance_types.update_one({"_id": type_doc["_id"]}, {"$set": {"is_active": False, "updated_at": datetime.utcnow()}})
        await self._emit("insurance_type.deactivated", current_user, type_doc, before=type_doc)
        return True

    async def _emit(self, action: str, current_user: Any, t: Dict[str, Any], *, before: Any = None, after: Any = None) -> None:
        await self.audit.emit(
            action=action, actor_id=getattr(current_user, "id", None),
            resource_type="insurance_type", resource_id=str(t.get("_id")),
            organization_id=t.get("organization_id"), before=before, after=after,
        )


# --- daily expiry-alert scan (background job) -----------------------------


async def scan_insurance_expiry_alerts(db: Any, notification_service: Any = None, *, now: Optional[datetime] = None) -> Dict[str, int]:
    now = now or datetime.utcnow()
    items = [b async for b in db.insurance_policies.find({})]
    emitted = 0
    for b in items:
        for tag in due_alert_types(b, now):
            day = now.date().isoformat()
            dedupe = f"insurance:{b.get('_id')}:{tag}:{day}"
            try:
                existing = await db.insurance_notifications.find_one({"_id": dedupe})
            except Exception:  # pragma: no cover
                existing = None
            if existing:
                continue
            log = {
                "_id": dedupe, "insurance_id": str(b.get("_id")), "project_id": b.get("project_id"),
                "organization_id": b.get("organization_id"), "notification_type": tag,
                "trigger_date": now, "delivery_status": "pending", "read_status": False, "created_at": now,
            }
            try:
                await db.insurance_notifications.insert_one(log)
            except Exception:  # pragma: no cover
                continue
            if notification_service is None:
                continue
            overdue = tag == "expired"
            try:
                from ..models.notification import (
                    NotificationContext, NotificationPriority, NotificationSeverity, NotificationType,
                )

                exp = _as_dt(b.get("date_of_expiry"))
                exp_str = exp.date().isoformat() if exp else "?"
                policy = b.get("policy_number") or b.get("_id")
                msg = (
                    f"Insurance policy {policy} ({b.get('insurance_type') or ''}) expired on {exp_str}."
                    if overdue else
                    f"Insurance policy {policy} ({b.get('insurance_type') or ''}) expires on {exp_str} ({days_remaining(b, now)} days)."
                )
                await notification_service.emit(
                    NotificationType.KEYDATE_OVERDUE if overdue else NotificationType.KEYDATE_DUE,
                    str(b.get("_id")), "insurance",
                    context=NotificationContext.PROJECT,
                    priority=NotificationPriority.URGENT if overdue else NotificationPriority.HIGH,
                    severity=NotificationSeverity.ERROR if overdue else NotificationSeverity.WARNING,
                    data={
                        "title": "Insurance policy expired" if overdue else "Insurance expiry approaching",
                        "message": msg, "insurance_id": str(b.get("_id")),
                        "organization_id": b.get("organization_id"), "project_id": b.get("project_id"),
                    },
                    resource_link="/insurance", dedupe_key=dedupe,
                )
                await db.insurance_notifications.update_one({"_id": dedupe}, {"$set": {"delivery_status": "sent"}})
                emitted += 1
            except Exception:  # pragma: no cover
                pass
    return {"scanned": len(items), "emitted": emitted}


async def run_insurance_expiry_scan() -> Dict[str, int]:
    from ..core.database import get_database
    from ..dependencies import get_notification_service

    db = await get_database()
    try:
        notification_service = await get_notification_service(db)
    except Exception:  # pragma: no cover
        notification_service = None
    return await scan_insurance_expiry_alerts(db, notification_service)
