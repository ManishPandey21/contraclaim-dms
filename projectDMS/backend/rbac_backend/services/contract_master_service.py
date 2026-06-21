"""Contract Master service.

Pure date/value helpers (effective completion, DLP end, per-BG-type required-up-to
date) plus tenant-scoped persistence + audit. The ``bg_required_up_to`` formula is
the single source the Bank Guarantee register uses to auto-compute and recompute
its contractual required-up-to dates (e.g. when EOT moves the completion date).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from ..core.database import get_database
from ..models.contract_master import (
    DEFAULT_BG_VALIDITY_RULES,
    ContractMaster,
    ContractMasterCreate,
)
from .audit_event_service import AuditEventService


def _as_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def effective_completion_date(cm: Dict[str, Any]) -> Optional[datetime]:
    """The completion date in force: revised, else original."""
    return _as_dt(cm.get("revised_completion_date")) or _as_dt(cm.get("original_completion_date"))


def dlp_end_date(cm: Dict[str, Any]) -> Optional[datetime]:
    base = effective_completion_date(cm)
    dlp = cm.get("defect_liability_period_days")
    if base is None or dlp is None:
        return None
    return base + timedelta(days=int(dlp))


def _rule_for(cm: Dict[str, Any], bg_type: str) -> Dict[str, Any]:
    rules = {**DEFAULT_BG_VALIDITY_RULES, **(cm.get("bg_validity_rules") or {})}
    return rules.get(bg_type) or rules.get("default") or DEFAULT_BG_VALIDITY_RULES["default"]


def bg_required_up_to(cm: Dict[str, Any], bg_type: str) -> Optional[datetime]:
    """Contractual required-up-to date for a BG type (pure).

    required = base(basis) + offset_days, where basis is the effective completion
    date or the DLP end date.
    """
    rule = _rule_for(cm, bg_type)
    base = dlp_end_date(cm) if rule.get("basis") == "dlp_end" else effective_completion_date(cm)
    if base is None:
        return None
    return base + timedelta(days=int(rule.get("offset_days") or 0))


def total_contract_value_base(cm: Dict[str, Any]) -> Optional[float]:
    """Total contract value in the base currency.

    Sum each contract currency's value converted at its award-fixed rate. With no
    multi-currency breakdown, fall back to the single-currency current/original
    value so existing contracts are unaffected.
    """
    currencies = cm.get("contract_currencies") or []
    if currencies:
        total = 0.0
        for entry in currencies:
            value = entry.get("contract_value")
            if value is None:
                continue
            rate = entry.get("conversion_rate")
            rate = 1.0 if rate is None else float(rate)
            total += float(value) * rate
        return round(total, 2)
    single = cm.get("current_contract_value")
    if single is None:
        single = cm.get("original_contract_value")
    return round(float(single), 2) if single is not None else None


def decorate(cm: Dict[str, Any]) -> Dict[str, Any]:
    c = dict(cm)
    c["effective_completion_date"] = effective_completion_date(c)
    c["dlp_end_date"] = dlp_end_date(c)
    c["total_contract_value_base"] = total_contract_value_base(c)
    return c


class ContractMasterService:
    def __init__(self, db: Any = None) -> None:
        self.db = db
        self.audit = AuditEventService(db)

    async def _get_db(self) -> Any:
        return self.db if self.db is not None else await get_database()

    async def get_for_scope(self, organization_id: Optional[str], project_id: str,
                            contract_id: str = "primary") -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        query: Dict[str, Any] = {"project_id": project_id, "contract_id": contract_id}
        if organization_id:
            query["organization_id"] = organization_id
        cm = await db.contract_master.find_one(query)
        return decorate(cm) if cm else None

    async def get(self, master_id: str) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        cm = await db.contract_master.find_one({"_id": master_id})
        return decorate(cm) if cm else None

    async def list(self, scope_filter: Dict[str, Any], *, project_id: Optional[str] = None) -> List[Dict[str, Any]]:
        db = await self._get_db()
        query: Dict[str, Any] = dict(scope_filter or {})
        if project_id:
            query["project_id"] = project_id
        return [decorate(c) async for c in db.contract_master.find(query).sort("contract_id", 1)]

    async def create(self, payload: ContractMasterCreate, current_user: Any) -> Dict[str, Any]:
        db = await self._get_db()
        doc = ContractMaster(**payload.model_dump()).model_dump(by_alias=True)
        if not doc.get("organization_id"):
            doc["organization_id"] = getattr(current_user, "organization_id", None)
        # current value starts at the original baseline.
        if doc.get("current_contract_value") is None:
            doc["current_contract_value"] = doc.get("original_contract_value")
        doc["created_at"] = datetime.utcnow()
        doc["created_by"] = getattr(current_user, "id", None)
        res = await db.contract_master.insert_one(doc)
        created = await db.contract_master.find_one({"_id": res.inserted_id}) or doc
        await self._emit("contract_master.created", current_user, created, after=created)
        return decorate(created)

    async def update(self, cm: Dict[str, Any], payload: Dict[str, Any], current_user: Any) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        update = {k: v for k, v in payload.items() if v is not None}
        update["updated_at"] = datetime.utcnow()
        update["updated_by"] = getattr(current_user, "id", None)
        updated = await db.contract_master.find_one_and_update(
            {"_id": cm["_id"]}, {"$set": update}, return_document=True
        )
        await self._emit("contract_master.updated", current_user, cm, before=cm, after=updated)
        return decorate(updated or cm)

    async def revise_completion(self, cm: Dict[str, Any], revised_completion_date: datetime,
                                current_user: Any, *, remarks: Optional[str] = None) -> Dict[str, Any]:
        """Move the revised completion date (the EOT hook). Original is preserved;
        the Bank Guarantee register recomputes its required-up-to dates from this."""
        db = await self._get_db()
        await db.contract_master.update_one(
            {"_id": cm["_id"]},
            {"$set": {"revised_completion_date": revised_completion_date,
                      "updated_at": datetime.utcnow(), "updated_by": getattr(current_user, "id", None)}},
        )
        await self._emit("contract_master.completion_revised", current_user, cm,
                         after={"revised_completion_date": str(revised_completion_date), "remarks": remarks})
        return decorate(await db.contract_master.find_one({"_id": cm["_id"]}) or cm)

    async def sync_current_value(self, organization_id: Optional[str], project_id: str,
                                 contract_id: str, current_value: float) -> None:
        """Keep current_contract_value = original + cumulative approved variation.
        Best-effort; called by the Variation register on change."""
        db = await self._get_db()
        query: Dict[str, Any] = {"project_id": project_id, "contract_id": contract_id or "primary"}
        if organization_id:
            query["organization_id"] = organization_id
        await db.contract_master.update_one(query, {"$set": {"current_contract_value": round(float(current_value), 2)}})

    def bg_required_dates(self, cm: Dict[str, Any]) -> List[Dict[str, Any]]:
        rules = {**DEFAULT_BG_VALIDITY_RULES, **(cm.get("bg_validity_rules") or {})}
        out: List[Dict[str, Any]] = []
        for bg_type, rule in rules.items():
            if bg_type == "default":
                continue
            out.append({
                "bg_type": bg_type, "basis": rule.get("basis", "completion"),
                "offset_days": int(rule.get("offset_days") or 0),
                "required_up_to": bg_required_up_to(cm, bg_type),
            })
        return out

    async def _emit(self, action: str, current_user: Any, cm: Dict[str, Any], *, before: Any = None, after: Any = None) -> None:
        await self.audit.emit(
            action=action, actor_id=getattr(current_user, "id", None),
            resource_type="contract_master", resource_id=str(cm.get("_id")),
            organization_id=cm.get("organization_id"), project_id=cm.get("project_id"),
            before=before, after=after,
        )
