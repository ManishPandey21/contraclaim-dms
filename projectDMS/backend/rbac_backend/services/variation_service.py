"""Variation Register service.

Pure roll-up calculations (cumulative approved variation, revised contract value,
percentage variation) plus tenant-scoped persistence + audit. Authorization is
enforced by the router via PolicyService.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from ..core.database import get_database
from ..models.variation import Variation, VariationCreate, VariationStatus, VariationType
from .audit_event_service import AuditEventService

# Statuses still in flight (not approved/rejected/superseded).
PENDING_STATUSES = {
    VariationStatus.DRAFT.value, VariationStatus.SUBMITTED.value,
    VariationStatus.UNDER_REVIEW.value, VariationStatus.RECOMMENDED.value,
}


def _sv(value: Any) -> Any:
    """Normalise an enum member (or string) to its plain string value."""
    return value.value if hasattr(value, "value") else value


def variation_base_amounts(variation: Dict[str, Any]) -> "tuple[float, float]":
    """(submitted, approved) for one variation, in the contract base currency.

    When a per-currency split is present, each currency's portion is converted at
    its award-fixed rate; otherwise the flat amounts are taken to be in the base
    currency already.
    """
    cas = variation.get("currency_amounts") or []
    if cas:
        sub = sum(
            float(ca.get("submitted_amount") or 0.0) * float(ca.get("conversion_rate") or 1.0)
            for ca in cas
        )
        app = sum(
            float(ca.get("approved_amount") or 0.0) * float(ca.get("conversion_rate") or 1.0)
            for ca in cas
        )
        return round(sub, 2), round(app, 2)
    return float(variation.get("submitted_amount") or 0.0), float(variation.get("approved_amount") or 0.0)


def signed_approved(variation: Dict[str, Any]) -> float:
    """Base-currency approved amount, signed by variation type (negative reduces value)."""
    _, approved = variation_base_amounts(variation)
    amount = abs(approved)
    return -amount if _sv(variation.get("variation_type")) == VariationType.NEGATIVE.value else amount


def variation_summary(variations: List[Dict[str, Any]], original_contract_value: Optional[float] = None) -> Dict[str, Any]:
    """Compute the contract-value roll-up from a set of variations (pure)."""
    ocv = original_contract_value
    if ocv is None:
        for v in variations:
            if v.get("original_contract_value"):
                ocv = float(v["original_contract_value"])
                break
    ocv = float(ocv or 0.0)

    # All roll-ups are in the base currency so a mixed-currency register adds up.
    total_submitted = sum(variation_base_amounts(v)[0] for v in variations)
    total_approved = sum(variation_base_amounts(v)[1] for v in variations
                         if _sv(v.get("status")) == VariationStatus.APPROVED.value)
    cumulative = sum(signed_approved(v) for v in variations
                     if _sv(v.get("status")) == VariationStatus.APPROVED.value)
    revised = ocv + cumulative
    pct = (cumulative / ocv * 100.0) if ocv else 0.0
    return {
        "original_contract_value": round(ocv, 2),
        "total_submitted_amount": round(total_submitted, 2),
        "total_approved_amount": round(total_approved, 2),
        "cumulative_approved_variation": round(cumulative, 2),
        "revised_contract_value": round(revised, 2),
        "percentage_variation": round(pct, 4),
        "pending_variation_count": sum(1 for v in variations if _sv(v.get("status")) in PENDING_STATUSES),
        "approved_variation_count": sum(1 for v in variations if _sv(v.get("status")) == VariationStatus.APPROVED.value),
        "rejected_variation_count": sum(1 for v in variations if _sv(v.get("status")) == VariationStatus.REJECTED.value),
    }


def decorate(variation: Dict[str, Any]) -> Dict[str, Any]:
    v = dict(variation)
    sub_base, app_base = variation_base_amounts(v)
    v["submitted_amount_base"] = round(sub_base, 2)
    v["approved_amount_base"] = round(app_base, 2)
    sub = v.get("submitted_amount")
    app = v.get("approved_amount")
    if sub is not None and app is not None:
        # Single-currency: keep the original submitted-vs-approved gap.
        v["difference_amount"] = float(sub) - float(app)
    elif v.get("currency_amounts"):
        # Multi-currency: report the gap in the base currency.
        v["difference_amount"] = round(sub_base - app_base, 2)
    else:
        v["difference_amount"] = None
    return v


class AmbiguousLegacyVariationRelationshipError(ValueError):
    """A raw ``linked_document_ids`` write carries no role and no authorization.

    New Variation evidence goes through the canonical relationship endpoint
    (``/entities/variation/{id}/document-links``); legacy arrays are read-only
    discovery input for the operator backfill.
    """


LEGACY_WRITE_REFUSAL = (
    "Variation linked_document_ids is read-only legacy data; link Documents "
    "through /entities/variation/{id}/document-links"
)


class VariationService:
    def __init__(self, db: Any = None) -> None:
        self.db = db
        self.audit = AuditEventService(db)

    async def _get_db(self) -> Any:
        return self.db if self.db is not None else await get_database()

    async def _contract_master(self, org: Optional[str], project_id: Optional[str], contract_id: Optional[str]) -> Optional[Dict[str, Any]]:
        from .contract_master_service import ContractMasterService

        if not project_id:
            return None
        return await ContractMasterService(self.db).get_for_scope(org, project_id, contract_id or "primary")

    async def _sync_contract_value(self, org: Optional[str], project_id: Optional[str], contract_id: Optional[str]) -> None:
        """Keep contract_master.current_contract_value = original + cumulative approved."""
        cm = await self._contract_master(org, project_id, contract_id)
        if not cm:
            return
        db = await self._get_db()
        query: Dict[str, Any] = {"project_id": project_id}
        if org:
            query["organization_id"] = org
        if contract_id:
            query["contract_id"] = contract_id
        variations = [v async for v in db.variations.find(query)]
        summary = variation_summary(variations, cm.get("original_contract_value"))
        from .contract_master_service import ContractMasterService

        await ContractMasterService(db).sync_current_value(org, project_id, contract_id or "primary", summary["revised_contract_value"])

    async def create(self, payload: VariationCreate, current_user: Any) -> Dict[str, Any]:
        db = await self._get_db()
        if getattr(payload, "linked_document_ids", None):
            raise AmbiguousLegacyVariationRelationshipError(LEGACY_WRITE_REFUSAL)
        doc = Variation(**payload.model_dump()).model_dump(by_alias=True)
        # An empty create array carries no relationship intent; canonical
        # relationships own every link write.
        doc.pop("linked_document_ids", None)
        if not doc.get("organization_id"):
            doc["organization_id"] = getattr(current_user, "organization_id", None)
        if not doc.get("contract_id"):
            doc["contract_id"] = "primary"
        # Default the original contract value from the contract master when omitted.
        if doc.get("original_contract_value") is None:
            cm = await self._contract_master(doc.get("organization_id"), doc.get("project_id"), doc.get("contract_id"))
            if cm and cm.get("original_contract_value") is not None:
                doc["original_contract_value"] = cm["original_contract_value"]
        doc["created_at"] = datetime.utcnow()
        doc["created_by"] = getattr(current_user, "id", None)
        res = await db.variations.insert_one(doc)
        created = await db.variations.find_one({"_id": res.inserted_id}) or doc
        await self._emit("variation.created", current_user, created, after=created)
        await self._sync_contract_value(created.get("organization_id"), created.get("project_id"), created.get("contract_id"))
        return decorate(created)

    async def get(self, variation_id: str) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        v = await db.variations.find_one({"_id": variation_id})
        return decorate(v) if v else None

    async def list(self, scope_filter: Dict[str, Any], *, project_id: Optional[str] = None,
                   contract_id: Optional[str] = None, status: Optional[str] = None,
                   variation_type: Optional[str] = None, skip: int = 0, limit: int = 500) -> List[Dict[str, Any]]:
        db = await self._get_db()
        query: Dict[str, Any] = dict(scope_filter or {})
        if project_id:
            query["project_id"] = project_id
        if contract_id:
            query["contract_id"] = contract_id
        if status:
            query["status"] = status
        if variation_type:
            query["variation_type"] = variation_type
        cursor = db.variations.find(query).sort("created_at", -1).skip(skip).limit(limit)
        return [decorate(v) async for v in cursor]

    async def update(self, variation: Dict[str, Any], payload: Dict[str, Any], current_user: Any) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        if "linked_document_ids" in payload:
            raise AmbiguousLegacyVariationRelationshipError(LEGACY_WRITE_REFUSAL)
        update = {k: v for k, v in payload.items() if v is not None}
        update["updated_at"] = datetime.utcnow()
        update["updated_by"] = getattr(current_user, "id", None)
        updated = await db.variations.find_one_and_update(
            {"_id": variation["_id"]}, {"$set": update}, return_document=True
        )
        await self._emit("variation.updated", current_user, variation, before=variation, after=updated)
        target = updated or variation
        await self._sync_contract_value(target.get("organization_id"), target.get("project_id"), target.get("contract_id"))
        return decorate(target)

    async def delete(self, variation: Dict[str, Any], current_user: Any, *, policy: Any = None) -> bool:
        """Delete through the canonical target cleanup.

        ``delete_target`` authorizes ``dms.variation.delete`` on the canonical
        scope, deletes the row, soft-removes every active link and audits each
        ``document_relationship.unlinked`` plus ``variation.deleted`` in one
        transaction. Linked Documents are never touched.
        """
        from .document_relationship_service import DocumentRelationshipService

        db = await self._get_db()
        if not variation.get("organization_id") or not variation.get("project_id"):
            # A legacy row without explicit scope can hold no canonical link (the
            # relationship service refuses scope-less targets), so there is
            # nothing to clean up - and delete_target's 409 must not make it
            # undeletable. The caller has already authorized dms.variation.delete.
            result = await db.variations.delete_one({"_id": variation["_id"]})
            await self._emit("variation.deleted", current_user, variation, before=variation)
            await self._sync_contract_value(variation.get("organization_id"), variation.get("project_id"), variation.get("contract_id"))
            return bool(getattr(result, "deleted_count", 0))
        await DocumentRelationshipService(db, policy=policy).delete_target(
            current_user,
            "variation",
            str(variation["_id"]),
            reason="Variation deleted",
        )
        await self._sync_contract_value(variation.get("organization_id"), variation.get("project_id"), variation.get("contract_id"))
        return True

    async def summary(self, scope_filter: Dict[str, Any], *, project_id: Optional[str] = None,
                      contract_id: Optional[str] = None, original_contract_value: Optional[float] = None) -> Dict[str, Any]:
        db = await self._get_db()
        query: Dict[str, Any] = dict(scope_filter or {})
        if project_id:
            query["project_id"] = project_id
        if contract_id:
            query["contract_id"] = contract_id
        variations = [v async for v in db.variations.find(query)]
        return variation_summary(variations, original_contract_value)

    async def _emit(self, action: str, current_user: Any, variation: Dict[str, Any], *, before: Any = None, after: Any = None) -> None:
        await self.audit.emit(
            action=action,
            actor_id=getattr(current_user, "id", None),
            resource_type="variation",
            resource_id=str(variation.get("_id")),
            organization_id=variation.get("organization_id"),
            project_id=variation.get("project_id"),
            before=before,
            after=after,
        )
