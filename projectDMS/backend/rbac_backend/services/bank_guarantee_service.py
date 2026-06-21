"""Bank Guarantee Register service.

Pure logic (extension-required, days-to-expiry, summary, expiry-alert rules) plus
the extend/release workflow that writes an immutable extension history, and the
daily expiry-alert scan. Authorization is the router's job via PolicyService.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from ..core.database import get_database
from ..models.bank_guarantee import (
    BankGuarantee,
    BankGuaranteeCreate,
    BGExtendRequest,
    BGExtensionHistory,
    BGStatus,
)
from .audit_event_service import AuditEventService

logger = logging.getLogger(__name__)

ALERT_THRESHOLDS = (45, 30)


def _as_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def _sv(value: Any) -> str:
    """Normalise an enum member (or string) to its plain string value."""
    return value.value if hasattr(value, "value") else str(value or "")


def days_to_expiry(bg: Dict[str, Any], now: Optional[datetime] = None) -> Optional[int]:
    now = now or datetime.utcnow()
    exp = _as_dt(bg.get("bg_expiry_date"))
    return (exp.date() - now.date()).days if exp else None


def extension_required(bg: Dict[str, Any], now: Optional[datetime] = None) -> bool:
    """Required when the BG expires before it is contractually required and it is
    not released/encashed. Per spec §11."""
    status = _sv(bg.get("bg_status"))
    if status in {BGStatus.RELEASED.value, BGStatus.ENCASHED.value}:
        return False
    exp = _as_dt(bg.get("bg_expiry_date"))
    req = _as_dt(bg.get("contractual_required_up_to"))
    if exp is None or req is None:
        return False
    return exp.date() < req.date()


def next_alert_date(bg: Dict[str, Any]) -> Optional[datetime]:
    exp = _as_dt(bg.get("bg_expiry_date"))
    if exp is None:
        return None
    return exp - timedelta(days=ALERT_THRESHOLDS[0])


def due_alert_types(bg: Dict[str, Any], now: Optional[datetime] = None) -> List[str]:
    """Alert tags firing today: T-45 / T-30 when extension is still required, or
    'expired'. Per spec §12 — only when expiry < required and not released."""
    if not extension_required(bg, now):
        # Past expiry while still required is handled below; otherwise no alert.
        pass
    status = _sv(bg.get("bg_status"))
    if status in {BGStatus.RELEASED.value, BGStatus.ENCASHED.value}:
        return []
    exp = _as_dt(bg.get("bg_expiry_date"))
    req = _as_dt(bg.get("contractual_required_up_to"))
    if exp is None or req is None or exp.date() >= req.date():
        return []
    d = days_to_expiry(bg, now)
    if d is None:
        return []
    if d < 0:
        return ["expired"]
    if d in ALERT_THRESHOLDS:
        return [f"T-{d}"]
    return []


def _base_amount(bg: Dict[str, Any]) -> float:
    """BG amount in the contract base currency (amount x award-fixed rate)."""
    amount = float(bg.get("bg_amount") or 0.0)
    rate = bg.get("conversion_rate")
    return amount * (1.0 if rate is None else float(rate))


def decorate(bg: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    b = dict(bg)
    b["extension_required"] = extension_required(b, now)
    b["days_to_expiry"] = days_to_expiry(b, now)
    b["next_alert_date"] = next_alert_date(b)
    b["bg_amount_base"] = round(_base_amount(b), 2) if b.get("bg_amount") is not None else None
    return b


def bg_summary(bgs: List[Dict[str, Any]], now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.utcnow()
    out = {k: 0 for k in ("total", "valid", "extension_required", "expiring_45", "expiring_30", "expired", "released")}
    total_amount = 0.0
    for b in bgs:
        out["total"] += 1
        # Convert to the base currency so a mixed-currency register sums correctly.
        total_amount += _base_amount(b)
        status = _sv(b.get("bg_status"))
        if status == BGStatus.RELEASED.value:
            out["released"] += 1
            continue
        if extension_required(b, now):
            out["extension_required"] += 1
        d = days_to_expiry(b, now)
        if d is not None and d < 0:
            out["expired"] += 1
        else:
            if status == BGStatus.VALID.value:
                out["valid"] += 1
            if d is not None and d <= 45 and extension_required(b, now):
                out["expiring_45"] += 1
            if d is not None and d <= 30 and extension_required(b, now):
                out["expiring_30"] += 1
    out["total_bg_amount"] = round(total_amount, 2)
    return out


class BankGuaranteeService:
    def __init__(self, db: Any = None) -> None:
        self.db = db
        self.audit = AuditEventService(db)

    async def _get_db(self) -> Any:
        return self.db if self.db is not None else await get_database()

    async def _contract_master(self, org: Any, project_id: Any, contract_id: Any) -> Optional[Dict[str, Any]]:
        from .contract_master_service import ContractMasterService

        if not project_id:
            return None
        return await ContractMasterService(self.db).get_for_scope(org, project_id, contract_id or "primary")

    async def create(self, payload: BankGuaranteeCreate, current_user: Any) -> Dict[str, Any]:
        db = await self._get_db()
        doc = BankGuarantee(**payload.model_dump()).model_dump(by_alias=True)
        if not doc.get("organization_id"):
            doc["organization_id"] = getattr(current_user, "organization_id", None)
        if not doc.get("contract_id"):
            doc["contract_id"] = "primary"
        # Default the contractual required-up-to date from the contract master
        # (per BG-type validity rule) when omitted.
        if not doc.get("contractual_required_up_to"):
            cm = await self._contract_master(doc.get("organization_id"), doc.get("project_id"), doc.get("contract_id"))
            if cm:
                from .contract_master_service import bg_required_up_to

                req = bg_required_up_to(cm, _sv(doc.get("bg_type")))
                if req is not None:
                    doc["contractual_required_up_to"] = req
        doc["created_at"] = datetime.utcnow()
        doc["created_by"] = getattr(current_user, "id", None)
        res = await db.bank_guarantees.insert_one(doc)
        created = await db.bank_guarantees.find_one({"_id": res.inserted_id}) or doc
        await self._emit("bank_guarantee.created", current_user, created, after=created)
        return decorate(created)

    async def recompute_required_dates(self, organization_id: Any, project_id: Any, contract_id: Any, current_user: Any) -> int:
        """Recompute contractual_required_up_to for every non-released BG under a
        contract from the contract master. Called when the contract completion date
        moves (e.g. EOT granted), so extension_required + alerts re-evaluate."""
        db = await self._get_db()
        cm = await self._contract_master(organization_id, project_id, contract_id)
        if not cm:
            return 0
        from .contract_master_service import bg_required_up_to

        query: Dict[str, Any] = {"project_id": project_id, "bg_status": {"$nin": [BGStatus.RELEASED.value, BGStatus.ENCASHED.value]}}
        if organization_id:
            query["organization_id"] = organization_id
        if contract_id:
            query["contract_id"] = contract_id
        changed = 0
        async for bg in db.bank_guarantees.find(query):
            req = bg_required_up_to(cm, _sv(bg.get("bg_type")))
            if req is None or req == _as_dt(bg.get("contractual_required_up_to")):
                continue
            await db.bank_guarantees.update_one(
                {"_id": bg["_id"]}, {"$set": {"contractual_required_up_to": req, "updated_at": datetime.utcnow()}}
            )
            await self._emit("bank_guarantee.required_date_recomputed", current_user, bg, after={"required_up_to": str(req)})
            changed += 1
        return changed

    async def get(self, bg_id: str) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        b = await db.bank_guarantees.find_one({"_id": bg_id})
        return decorate(b) if b else None

    async def list(self, scope_filter: Dict[str, Any], *, project_id: Optional[str] = None,
                   contract_id: Optional[str] = None, status: Optional[str] = None,
                   bg_type: Optional[str] = None, skip: int = 0, limit: int = 500) -> List[Dict[str, Any]]:
        db = await self._get_db()
        query: Dict[str, Any] = dict(scope_filter or {})
        for k, v in (("project_id", project_id), ("contract_id", contract_id), ("bg_status", status), ("bg_type", bg_type)):
            if v:
                query[k] = v
        cursor = db.bank_guarantees.find(query).sort("bg_expiry_date", 1).skip(skip).limit(limit)
        return [decorate(b) async for b in cursor]

    async def update(self, bg: Dict[str, Any], payload: Dict[str, Any], current_user: Any) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        update = {k: v for k, v in payload.items() if v is not None}
        update["updated_at"] = datetime.utcnow()
        update["updated_by"] = getattr(current_user, "id", None)
        updated = await db.bank_guarantees.find_one_and_update(
            {"_id": bg["_id"]}, {"$set": update}, return_document=True
        )
        await self._emit("bank_guarantee.updated", current_user, bg, before=bg, after=updated)
        return decorate(updated or bg)

    async def delete(self, bg: Dict[str, Any], current_user: Any) -> bool:
        db = await self._get_db()
        res = await db.bank_guarantees.delete_one({"_id": bg["_id"]})
        await self._emit("bank_guarantee.deleted", current_user, bg, before=bg)
        return res.deleted_count > 0

    async def extend(self, bg: Dict[str, Any], req: BGExtendRequest, current_user: Any) -> Dict[str, Any]:
        db = await self._get_db()
        now = datetime.utcnow()
        revision = int(bg.get("current_revision") or 0) + 1
        history = BGExtensionHistory(
            bg_id=str(bg["_id"]), project_id=bg.get("project_id"), organization_id=bg.get("organization_id"),
            revision_number=revision,
            previous_expiry_date=_as_dt(bg.get("bg_expiry_date")),
            new_expiry_date=req.revised_expiry_date,
            previous_required_up_to=_as_dt(bg.get("contractual_required_up_to")),
            new_required_up_to=req.revised_required_up_to or _as_dt(bg.get("contractual_required_up_to")),
            claim_expiry_date=req.revised_claim_expiry_date,
            extension_letter_reference=req.extension_letter_reference,
            extension_date=req.extension_date or now,
            remarks=req.remarks,
            created_by=getattr(current_user, "id", None),
        ).model_dump(by_alias=True)
        await db.bg_extension_history.insert_one(history)
        new_set: Dict[str, Any] = {
            "bg_expiry_date": req.revised_expiry_date,
            "bg_status": BGStatus.EXTENDED.value,
            "last_extension_date": req.extension_date or now,
            "current_revision": revision,
            "updated_at": now, "updated_by": getattr(current_user, "id", None),
        }
        if req.revised_claim_expiry_date is not None:
            new_set["claim_expiry_date"] = req.revised_claim_expiry_date
        if req.revised_required_up_to is not None:
            new_set["contractual_required_up_to"] = req.revised_required_up_to
        if req.linked_document_ids is not None:
            new_set["linked_document_ids"] = req.linked_document_ids
        await db.bank_guarantees.update_one({"_id": bg["_id"]}, {"$set": new_set})
        await self._emit("bank_guarantee.extended", current_user, bg, after={"revision": revision, "new_expiry": str(req.revised_expiry_date)})
        return decorate(await db.bank_guarantees.find_one({"_id": bg["_id"]}) or {**bg, **new_set})

    async def release(self, bg: Dict[str, Any], current_user: Any, *, remarks: Optional[str] = None) -> Dict[str, Any]:
        db = await self._get_db()
        await db.bank_guarantees.update_one(
            {"_id": bg["_id"]},
            {"$set": {"bg_status": BGStatus.RELEASED.value, "updated_at": datetime.utcnow(),
                      "updated_by": getattr(current_user, "id", None), **({"remarks": remarks} if remarks else {})}},
        )
        await self._emit("bank_guarantee.released", current_user, bg, after={"status": "released"})
        return decorate(await db.bank_guarantees.find_one({"_id": bg["_id"]}) or bg)

    async def list_history(self, bg_id: str) -> List[Dict[str, Any]]:
        db = await self._get_db()
        cursor = db.bg_extension_history.find({"bg_id": str(bg_id)}).sort("revision_number", 1)
        return [h async for h in cursor]

    async def summary(self, scope_filter: Dict[str, Any], *, project_id: Optional[str] = None) -> Dict[str, Any]:
        db = await self._get_db()
        query: Dict[str, Any] = dict(scope_filter or {})
        if project_id:
            query["project_id"] = project_id
        bgs = [b async for b in db.bank_guarantees.find(query)]
        return bg_summary(bgs)

    async def alerts(self, scope_filter: Dict[str, Any], now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        db = await self._get_db()
        bgs = [b async for b in db.bank_guarantees.find(dict(scope_filter or {}))]
        out = []
        for b in bgs:
            tags = due_alert_types(b, now)
            if tags:
                out.append({**decorate(b, now), "alert": tags[0]})
        return out

    async def _emit(self, action: str, current_user: Any, bg: Dict[str, Any], *, before: Any = None, after: Any = None) -> None:
        await self.audit.emit(
            action=action, actor_id=getattr(current_user, "id", None),
            resource_type="bank_guarantee", resource_id=str(bg.get("_id")),
            organization_id=bg.get("organization_id"), project_id=bg.get("project_id"),
            before=before, after=after,
        )


# --- daily expiry-alert scan (background job) -----------------------------


async def scan_bg_expiry_alerts(db: Any, notification_service: Any = None, *, now: Optional[datetime] = None) -> Dict[str, int]:
    now = now or datetime.utcnow()
    cursor = db.bank_guarantees.find({"bg_status": {"$nin": [BGStatus.RELEASED.value, BGStatus.ENCASHED.value]}})
    bgs = [b async for b in cursor]
    emitted = 0
    for b in bgs:
        for tag in due_alert_types(b, now):
            day = now.date().isoformat()
            dedupe = f"bg:{b.get('_id')}:{tag}:{day}"
            try:
                existing = await db.bg_notifications.find_one({"_id": dedupe})
            except Exception:  # pragma: no cover
                existing = None
            if existing:
                continue
            log = {
                "_id": dedupe, "bg_id": str(b.get("_id")), "project_id": b.get("project_id"),
                "organization_id": b.get("organization_id"), "notification_type": tag,
                "trigger_date": now, "delivery_status": "pending", "read_status": False, "created_at": now,
            }
            try:
                await db.bg_notifications.insert_one(log)
            except Exception:  # pragma: no cover
                continue
            if notification_service is None:
                continue
            overdue = tag == "expired"
            try:
                from ..models.notification import (
                    NotificationContext, NotificationPriority, NotificationSeverity, NotificationType,
                )

                exp = _as_dt(b.get("bg_expiry_date"))
                exp_str = exp.date().isoformat() if exp else "?"
                msg = (
                    f"Bank guarantee {b.get('bg_number') or b.get('_id')} expired on {exp_str} and is still required."
                    if overdue else
                    f"Bank guarantee {b.get('bg_number') or b.get('_id')} expires on {exp_str} ({days_to_expiry(b, now)} days) and needs extension."
                )
                await notification_service.emit(
                    NotificationType.KEYDATE_OVERDUE if overdue else NotificationType.KEYDATE_DUE,
                    str(b.get("_id")), "bank_guarantees",
                    context=NotificationContext.PROJECT,
                    priority=NotificationPriority.URGENT if overdue else NotificationPriority.HIGH,
                    severity=NotificationSeverity.ERROR if overdue else NotificationSeverity.WARNING,
                    data={
                        "title": "Bank guarantee expired" if overdue else "Bank guarantee expiry approaching",
                        "message": msg, "bg_id": str(b.get("_id")),
                        "organization_id": b.get("organization_id"), "project_id": b.get("project_id"),
                    },
                    resource_link="/bank-guarantees", dedupe_key=dedupe,
                )
                await db.bg_notifications.update_one({"_id": dedupe}, {"$set": {"delivery_status": "sent"}})
                emitted += 1
            except Exception:  # pragma: no cover
                pass
    return {"scanned": len(bgs), "emitted": emitted}


async def run_bg_expiry_scan() -> Dict[str, int]:
    from ..core.database import get_database
    from ..dependencies import get_notification_service

    db = await get_database()
    try:
        notification_service = await get_notification_service(db)
    except Exception:  # pragma: no cover
        notification_service = None
    return await scan_bg_expiry_alerts(db, notification_service)
