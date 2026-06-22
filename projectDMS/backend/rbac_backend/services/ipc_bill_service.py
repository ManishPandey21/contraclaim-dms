"""IPC / Contractor Bill Register service.

Pure base-currency roll-ups (per-component multi-currency conversion, net payable,
balance, cumulative value, percent billed/approved) plus tenant-scoped persistence
and audit. Conversion rates are carried on each currency amount (fixed at award,
sourced from Contract Master on the frontend).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from ..core.database import get_database
from ..models.ipc_bill import IPCBillCreate
from .audit_event_service import AuditEventService

from ..models.ipc_bill import DEDUCTION_COMPONENTS

_APPROVED_LIKE = {"approved", "partially_paid", "paid"}
_PENDING_LIKE = {"draft", "submitted", "under_verification", "verified"}


def component_base(items: Any) -> float:
    """Sum a list of {amount, conversion_rate} lines, converted to base.

    Used for deduction/recovery lines and for payment records (both carry
    `amount` + `conversion_rate`)."""
    total = 0.0
    for it in items or []:
        amount = float(it.get("amount") or 0.0)
        rate = it.get("conversion_rate")
        total += amount * (1.0 if rate is None else float(rate))
    return total


def line_total(items: Any, col: str) -> float:
    """Sum one perspective column (claimed/verified/approved) of the line items,
    converted to base."""
    total = 0.0
    for it in items or []:
        amount = float(it.get(col) or 0.0)
        rate = it.get("conversion_rate")
        total += amount * (1.0 if rate is None else float(rate))
    return total


def deductions_col_base(deductions: Any, col: str) -> float:
    """Sum one perspective column (claimed/verified/approved) across all
    deduction components, in base currency."""
    d = deductions or {}
    return sum(line_total(d.get(comp), col) for comp in DEDUCTION_COMPONENTS)


def payments_base(payments: Any) -> float:
    return component_base(payments)


def _ipc_metrics(ipc: Dict[str, Any]) -> Dict[str, float]:
    line_items = ipc.get("line_items")
    ded = ipc.get("deductions") or {}
    claimed = line_total(line_items, "claimed")
    verified = line_total(line_items, "verified")
    approved = line_total(line_items, "approved")
    approved_ded = deductions_col_base(ded, "approved")
    net_payable = approved - approved_ded
    paid = payments_base(ipc.get("payments"))
    return {
        "claimed_total_base": round(claimed, 2),
        "verified_total_base": round(verified, 2),
        "approved_total_base": round(approved, 2),
        "total_deductions_base": round(approved_ded, 2),
        "net_payable_base": round(net_payable, 2),
        "paid_base": round(paid, 2),
        "balance_payable_base": round(net_payable - paid, 2),
    }


def decorate(ipc: Dict[str, Any]) -> Dict[str, Any]:
    d = dict(ipc)
    m = _ipc_metrics(d)
    d.update(m)
    ocv = d.get("original_contract_value")
    ocv = float(ocv) if ocv else 0.0
    d["percent_billed"] = round(m["claimed_total_base"] / ocv * 100.0, 4) if ocv else None
    d["percent_approved"] = round(m["approved_total_base"] / ocv * 100.0, 4) if ocv else None
    return d


def ipc_summary(ipcs: List[Dict[str, Any]], original_contract_value: Optional[float] = None,
                base_currency: str = "INR") -> Dict[str, Any]:
    ocv = original_contract_value
    if ocv is None:
        for i in ipcs:
            if i.get("original_contract_value"):
                ocv = float(i["original_contract_value"])
                break
    ocv = float(ocv or 0.0)

    totals = {"claimed": 0.0, "approved": 0.0, "net": 0.0, "paid": 0.0, "balance": 0.0}
    pending = approved = paid = 0
    for i in ipcs:
        m = _ipc_metrics(i)
        totals["claimed"] += m["claimed_total_base"]
        totals["approved"] += m["approved_total_base"]
        totals["net"] += m["net_payable_base"]
        totals["paid"] += m["paid_base"]
        totals["balance"] += m["balance_payable_base"]
        status = str(i.get("status") or "").lower()
        if status == "paid":
            paid += 1
        if status in _APPROVED_LIKE:
            approved += 1
        elif status in _PENDING_LIKE:
            pending += 1

    return {
        "total_ipcs": len(ipcs),
        "base_currency": base_currency,
        "total_claimed_base": round(totals["claimed"], 2),
        "total_approved_base": round(totals["approved"], 2),
        "total_net_payable_base": round(totals["net"], 2),
        "total_paid_base": round(totals["paid"], 2),
        "total_balance_payable_base": round(totals["balance"], 2),
        "cumulative_ipc_value_base": round(totals["net"], 2),
        "percent_of_contract_billed": round(totals["claimed"] / ocv * 100.0, 4) if ocv else 0.0,
        "percent_of_contract_approved": round(totals["approved"] / ocv * 100.0, 4) if ocv else 0.0,
        "pending_count": pending,
        "approved_count": approved,
        "paid_count": paid,
    }


class IPCBillService:
    def __init__(self, db: Any = None) -> None:
        self.db = db
        self.audit = AuditEventService(db)

    async def _get_db(self) -> Any:
        return self.db if self.db is not None else await get_database()

    async def _contract_master(self, org: Optional[str], project_id: Optional[str], contract_id: Optional[str]):
        from .contract_master_service import ContractMasterService

        if not project_id:
            return None
        return await ContractMasterService(self.db).get_for_scope(org, project_id, contract_id or "primary")

    async def create(self, payload: IPCBillCreate, current_user: Any) -> Dict[str, Any]:
        from ..models.ipc_bill import IPCBill

        db = await self._get_db()
        doc = IPCBill(**payload.model_dump()).model_dump(by_alias=True)
        if not doc.get("organization_id"):
            doc["organization_id"] = getattr(current_user, "organization_id", None)
        if not doc.get("contract_id"):
            doc["contract_id"] = "primary"
        # Default base currency + contract value from Contract Master when omitted.
        cm = await self._contract_master(doc.get("organization_id"), doc.get("project_id"), doc.get("contract_id"))
        if cm:
            if not doc.get("base_currency"):
                doc["base_currency"] = cm.get("currency") or "INR"
            if doc.get("original_contract_value") is None:
                doc["original_contract_value"] = (
                    cm.get("total_contract_value_base")
                    if cm.get("total_contract_value_base") is not None
                    else cm.get("original_contract_value")
                )
        doc["created_at"] = datetime.utcnow()
        doc["created_by"] = getattr(current_user, "id", None)
        res = await db.ipc_bills.insert_one(doc)
        created = await db.ipc_bills.find_one({"_id": res.inserted_id}) or doc
        await self._emit("ipc_bill.created", current_user, created, after=created)
        return decorate(created)

    async def get(self, ipc_id: str) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        i = await db.ipc_bills.find_one({"_id": ipc_id})
        return decorate(i) if i else None

    async def list(self, scope_filter: Dict[str, Any], *, project_id: Optional[str] = None,
                   contract_id: Optional[str] = None, status: Optional[str] = None,
                   payment_status: Optional[str] = None, currency: Optional[str] = None,
                   date_from: Optional[datetime] = None, date_to: Optional[datetime] = None,
                   skip: int = 0, limit: int = 500) -> List[Dict[str, Any]]:
        db = await self._get_db()
        query: Dict[str, Any] = dict(scope_filter or {})
        if project_id:
            query["project_id"] = project_id
        if contract_id:
            query["contract_id"] = contract_id
        if status:
            query["status"] = status
        if currency:
            query["base_currency"] = currency.strip().upper()
        if date_from or date_to:
            rng: Dict[str, Any] = {}
            if date_from:
                rng["$gte"] = date_from
            if date_to:
                rng["$lte"] = date_to
            query["ipc_date"] = rng
        cursor = db.ipc_bills.find(query).sort("created_at", -1).skip(skip).limit(limit)
        rows = [decorate(i) async for i in cursor]
        # payment_status is a derived filter (balance based), applied in-memory.
        if payment_status == "paid":
            rows = [r for r in rows if (r.get("balance_payable_base") or 0) <= 0 and (r.get("paid_base") or 0) > 0]
        elif payment_status == "partial":
            rows = [r for r in rows if (r.get("paid_base") or 0) > 0 and (r.get("balance_payable_base") or 0) > 0]
        elif payment_status == "unpaid":
            rows = [r for r in rows if (r.get("paid_base") or 0) <= 0]
        return rows

    async def update(self, ipc: Dict[str, Any], payload: Dict[str, Any], current_user: Any) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        update = {k: v for k, v in payload.items() if v is not None}
        # Append a revision entry capturing the status + remarks change.
        revision = {
            "revision_number": int(ipc.get("current_revision") or 0) + 1,
            "status": update.get("status", ipc.get("status")),
            "remarks": update.get("remarks"),
            "changed_by": getattr(current_user, "id", None),
            "changed_at": datetime.utcnow(),
        }
        update["current_revision"] = revision["revision_number"]
        update["updated_at"] = datetime.utcnow()
        update["updated_by"] = getattr(current_user, "id", None)
        updated = await db.ipc_bills.find_one_and_update(
            {"_id": ipc["_id"]},
            {"$set": update, "$push": {"revisions": revision}},
            return_document=True,
        )
        await self._emit("ipc_bill.updated", current_user, ipc, before=ipc, after=updated)
        return decorate(updated or ipc)

    async def delete(self, ipc: Dict[str, Any], current_user: Any) -> bool:
        db = await self._get_db()
        res = await db.ipc_bills.delete_one({"_id": ipc["_id"]})
        await self._emit("ipc_bill.deleted", current_user, ipc, before=ipc)
        return res.deleted_count > 0

    async def summary(self, scope_filter: Dict[str, Any], *, project_id: Optional[str] = None,
                      contract_id: Optional[str] = None, original_contract_value: Optional[float] = None) -> Dict[str, Any]:
        db = await self._get_db()
        query: Dict[str, Any] = dict(scope_filter or {})
        if project_id:
            query["project_id"] = project_id
        if contract_id:
            query["contract_id"] = contract_id
        ipcs = [i async for i in db.ipc_bills.find(query)]
        base_currency = next((i.get("base_currency") for i in ipcs if i.get("base_currency")), "INR")
        if original_contract_value is None:
            cm = await self._contract_master(
                query.get("organization_id"), project_id, contract_id,
            )
            if cm:
                original_contract_value = (
                    cm.get("total_contract_value_base")
                    if cm.get("total_contract_value_base") is not None
                    else cm.get("original_contract_value")
                )
        return ipc_summary(ipcs, original_contract_value, base_currency)

    async def _emit(self, action: str, current_user: Any, ipc: Dict[str, Any], *, before: Any = None, after: Any = None) -> None:
        await self.audit.emit(
            action=action,
            actor_id=getattr(current_user, "id", None),
            resource_type="ipc_bill",
            resource_id=str(ipc.get("_id")),
            organization_id=ipc.get("organization_id"),
            project_id=ipc.get("project_id"),
            before=before,
            after=after,
        )
