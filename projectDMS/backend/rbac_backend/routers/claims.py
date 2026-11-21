"""Claim register API (Phase 4 / Module 1).

Tenant-scoped CRUD + status transitions for claims. Every endpoint is gated by
PolicyService (permission + entitlement + org/project scope) and lists are
filtered with build_scope_query, so Org A can never see Org B's claims.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from ..core.database import get_db
from ..core.permissions import Permissions
from ..core.security import CurrentUser, get_current_user, permission_scope_query
from ..core.tenant_context import ActiveScope, active_scope
from ..models.approval import ApprovalRecord, AssignBody, DecisionBody
from ..models.claim import (
    Claim,
    ClaimAssessment,
    ClaimCreate,
    ClaimRead,
    ClaimStatus,
    ClaimStatusUpdate,
    ClaimType,
    ClaimUpdate,
    allowed_next_statuses,
)
from ..services.approval_service import ApprovalError, ApprovalService
from ..services.claim_type import record_claim_type
from ..services.audit_event_service import AuditEventService
from ..services.claim_assessment_service import ClaimAssessmentService
from ..services.claim_contracts import (
    ClaimContractOptions,
    contract_label,
    describe as describe_contract_options,
    resolve_for_existing_claim,
    resolve_for_new_claim,
    valid_contracts,
)
from ..services.claim_legacy import coerce_legacy_amount, coerce_legacy_days, unrecognised_status
from ..services.claim_service import ClaimService
from ..services.claim_notice_requirements import NoticeRequirementService
from ..services.claims_notice import assess_notice
from ..services.sla_service import SlaService
from ..services.evidence_bundle_service import EvidenceBundleService
from ..services.document_relationship_service import (
    DocumentRelationshipError,
    DocumentRelationshipService,
)
from ..services.policy_service import PolicyService
from ..utils.error_handler import handle_exceptions

router = APIRouter()


class _NoticeWindows:
    """Per-request cache of each organisation's own notice periods, and of the
    periods recorded for each project from its contract."""

    def __init__(self, db) -> None:
        self._service = SlaService(db)
        self._requirements = NoticeRequirementService(db)
        self._cache: dict[str, dict] = {}
        self._project_cache: dict[tuple, dict] = {}

    async def requirement_for(self, claim: dict):
        scope = (str(claim.get("organization_id") or ""), str(claim.get("project_id") or ""))
        if scope not in self._project_cache:
            self._project_cache[scope] = await self._requirements.current_for_project(
                scope[0] or None, scope[1] or None
            )
        claim_type = str(getattr(claim.get("type"), "value", claim.get("type")) or "other")
        return self._project_cache[scope].get(claim_type)

    async def for_claim(self, claim: dict) -> dict:
        organization_id = str(claim.get("organization_id") or "")
        if organization_id not in self._cache:
            self._cache[organization_id] = await self._service.notice_window_overrides_for(
                organization_id or None
            )
        return self._cache[organization_id]


class _ContractLabels:
    """Per-request cache of each project's contract names (plan 4B-18)."""

    def __init__(self, db) -> None:
        self._db = db
        self._contract_labels: dict[tuple, dict] = {}

    async def contract_label_for(self, claim: dict):
        """The name of the claim's contract, read once per project in a request."""
        contract_id = str(claim.get("contract_id") or "")
        if not contract_id:
            return None
        scope = (str(claim.get("organization_id") or ""), str(claim.get("project_id") or ""))
        if scope not in self._contract_labels:
            query = {"project_id": scope[1]}
            if scope[0]:
                query["organization_id"] = scope[0]
            rows = await self._db.contract_master.find(
                query, {"contract_id": 1, "contract_name": 1, "contract_code": 1}
            ).to_list(length=200)
            self._contract_labels[scope] = {str(row.get("contract_id") or ""): contract_label(row) for row in rows}
        # A contract whose entry has since gone is still named by its key, never hidden.
        return self._contract_labels[scope].get(contract_id) or contract_id


async def _read_model(
    claim: dict, linked_document_ids: list, windows: _NoticeWindows, contract_name: Optional[str] = None
) -> ClaimRead:
    presented = {**claim, "linked_document_ids": linked_document_ids}
    for derived in (
        "notice_assessment", "allowed_status_transitions", "head_count", "bench_stage", "contract_label", "record_notes"
    ):
        presented.pop(derived, None)
    # D-10 (4B-19): a legacy record's figures and status are shown without guessing,
    # so one such row cannot make the register unreadable for the whole project.
    notes: List[str] = []
    amount, amount_recorded = coerce_legacy_amount(claim.get("amount_claimed"))
    days, days_recorded = coerce_legacy_days(claim.get("eot_days_claimed"))
    presented["amount_claimed"], presented["eot_days_claimed"] = amount, days
    if amount_recorded is not None:
        notes.append(f'The amount is recorded as "{amount_recorded}", which cannot be read as a figure. It needs re-entry.')
    if days_recorded is not None:
        notes.append(f'The days are recorded as "{days_recorded}", which is not a whole number of days. They need re-entry.')
    status_text = unrecognised_status(claim)
    if status_text:
        presented["status"] = status_text[:40]
        notes.append(
            f'The status "{status_text[:40]}" is not one the application recognises. '
            "The claim is read-only until it is corrected."
        )
    elif not presented.get("status"):
        presented["status"] = ClaimStatus.DRAFT
    # The board is read through the Bench; the register only needs its outline.
    bench = presented.pop("bench", None) or {}
    return ClaimRead(
        **presented,
        notice_assessment=assess_notice(
            claim,
            organisation_windows=await windows.for_claim(claim),
            requirement=await windows.requirement_for(claim),
        ),
        allowed_status_transitions=allowed_next_statuses(claim.get("status")),
        contract_label=contract_name,
        record_notes=notes,
        head_count=len(bench.get("heads") or []),
        bench_stage=bench.get("stage") if bench.get("heads") else None,
    )


async def _present_claim(claim: dict, db, current_user: CurrentUser) -> ClaimRead:
    linked = await DocumentRelationshipService(db).authorized_document_ids(
        current_user,
        "claim",
        str(claim.get("_id") or ""),
        legacy_document_ids=claim.get("linked_document_ids") or [],
    )
    return await _read_model(
        claim, linked, _NoticeWindows(db), await _ContractLabels(db).contract_label_for(claim)
    )


async def _present_claims(claims: list[dict], db, current_user: CurrentUser) -> list[ClaimRead]:
    ids_by_claim = await DocumentRelationshipService(
        db
    ).authorized_document_ids_for_claims(current_user, claims)
    windows = _NoticeWindows(db)
    labels = _ContractLabels(db)
    return [
        await _read_model(
            claim,
            ids_by_claim.get(str(claim.get("_id") or ""), []),
            windows,
            await labels.contract_label_for(claim),
        )
        for claim in claims
    ]


BENCH_FACT_FIELDS = ("type", "event_date", "awareness_date", "notice_date")


async def mark_bench_tests_stale(db, claim: dict) -> Optional[dict]:
    """After a change to a fact the heads were tested against: mark every tested
    head stale and reopen a locked board. Decisions people made are kept.

    One atomic update on the board as it now is (D-22, 4B-16, C-17). The earlier
    version compared the board version read before the fact change; a board write
    in between made it miss silently, leaving a locked board on tests that no
    longer matched. Array filters mark whichever heads are tested at the moment of
    the write, and the version bump makes any board save prepared before it fail
    with the usual "the Bench changed" conflict. There is no miss to retry.
    """
    if not isinstance(claim.get("bench"), dict):
        return None
    result = await db.claims.update_one(
        {"_id": claim["_id"], "bench": {"$type": "object"}},
        {
            "$set": {
                "bench.heads.$[tested].test_stale": True,
                "bench.stage": "heads",
                "bench.locked_at": None,
                "bench.locked_by": None,
            },
            "$inc": {"bench.board_version": 1},
        },
        array_filters=[{"tested.step_id": {"$nin": [None, ""]}}],
    )
    if not getattr(result, "matched_count", 0):
        return None
    updated = await db.claims.find_one({"_id": claim["_id"]})
    step_ids = [
        head["step_id"]
        for head in ((updated or {}).get("bench") or {}).get("heads") or []
        if head.get("step_id")
    ]
    if step_ids:
        await db.skill_steps.update_many({"step_id": {"$in": step_ids}}, {"$set": {"stale": True}})
    return updated


#: Statuses a claim may be created in through the register (4B-12, C-13). Later
#: statuses are reached by the lifecycle; "potential" only by a signal.
CREATABLE_STATUSES = frozenset({ClaimStatus.DRAFT, ClaimStatus.NOTIFIED})

#: Fields an update may clear with an explicit null. Others ignore a null.
#: Once a claim has a Claim Bench board, these come from its heads (D-14, 4B-17, C-07).
BOARD_DERIVED_FIELDS = ("amount_claimed", "eot_days_claimed")


def _board_derived_changes(claim: dict, changes: dict) -> list:
    """Board-derived fields the request would change. Unchanged values (the register
    form sends every field on each save) are dropped from ``changes`` instead."""
    if not isinstance(claim.get("bench"), dict):
        return []
    refused = []
    for key in BOARD_DERIVED_FIELDS:
        if key not in changes:
            continue
        sent, stored = changes[key], claim.get(key)
        if sent is None or (stored is not None and float(sent) == float(stored)):
            changes.pop(key)
        else:
            refused.append(key)
    return refused


CLEARABLE_FIELDS = frozenset(
    {
        "claim_ref", "description", "event_date", "awareness_date", "notice_date", "submission_date",
        "response_due_date", "amount_agreed", "eot_days_granted", "responsible_party_id",
    }
)


def _refuse(code: int, error_code: str, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail={"code": error_code, "user_message": message, "detail": message})


def _status_conflict() -> HTTPException:
    return _refuse(
        status.HTTP_409_CONFLICT,
        "claim_changed_concurrently",
        "Someone changed this claim's status at the same time. Reload it and try again.",
    )


def _require_status_transition(claim: dict, target: ClaimStatus) -> None:
    """Refuse a status move the lifecycle does not allow. Same status is a no-op."""
    current = str(claim.get("status") or ClaimStatus.DRAFT.value)
    if current == target.value:
        return
    allowed = allowed_next_statuses(current)
    if target not in allowed:
        options = ", ".join(item.value for item in allowed) or "none"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"A claim that is {current} cannot be moved to {target.value}. "
                f"Allowed next statuses: {options}."
            ),
        )


async def get_policy(db=Depends(get_db)) -> PolicyService:
    return PolicyService(db=db)


@router.get("/claims", response_model=List[ClaimRead])
@handle_exceptions
async def list_claims(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    claim_type: Optional[str] = Query(None, alias="type"),
    status_filter: Optional[str] = Query(None, alias="status"),
    responsible_party_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    organization_id, project_id = await selection.list_filters(organization_id, project_id)
    await policy.authorize(
        current_user,
        Permissions.CLAIM_VIEW,
        resource_type="claims",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id,
        audit=False,
    )
    # JG-7(c): custom-role holders see the claims of the organisation/projects where
    # they hold dms.claim.view by name; built-in roles are unchanged.
    scope = await permission_scope_query(
        current_user, Permissions.CLAIM_VIEW, organization_id=organization_id, project_id=project_id, db=db
    )
    claims = await ClaimService(db).list(
        scope,
        claim_type=claim_type,
        status=status_filter,
        responsible_party_id=responsible_party_id,
        skip=skip,
        limit=limit,
    )
    return await _present_claims(claims, db, current_user)


@router.get("/claims/contract-options", response_model=ClaimContractOptions)
@handle_exceptions
async def claim_contract_options(
    project_id: str = Query(..., min_length=1),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """The contracts a new claim on this project can be made under (plan 4B-18, D-23).

    One contract: it is the selection. Several: one must be chosen. None the
    caller can read: the claim is created with no contract and says so.
    """
    await selection.require_project(project_id, None)
    project = await db.projects.find_one({"_id": project_id}, {"organization_id": 1})
    organization_id = str((project or {}).get("organization_id") or "") or getattr(current_user, "organization_id", None)
    await policy.authorize(
        current_user,
        Permissions.CLAIM_VIEW,
        resource_type="claim",
        organization_id=organization_id,
        project_id=project_id,
        audit=False,
    )
    return describe_contract_options(
        await valid_contracts(db, policy, current_user, organization_id=organization_id, project_id=project_id)
    )


@router.post("/claims", response_model=ClaimRead, status_code=status.HTTP_201_CREATED)
@handle_exceptions
async def create_claim(
    payload: ClaimCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    # The body's project must BE the selection: refused, never rewritten. A claim
    # without a project cannot be created while the selection is a project.
    await selection.require_project(payload.project_id, payload.organization_id)
    if payload.origin is not None:
        # The origin ties a claim to the signal it was raised from, under a unique
        # index; only the signal path may set it (C-13).
        raise _refuse(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "claim_origin_server_owned",
            "A claim's origin is recorded by the system when a potential claim is raised; it cannot be set here.",
        )
    if ClaimStatus(payload.status) not in CREATABLE_STATUSES:
        raise _refuse(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "claim_create_status",
            "A new claim starts as a draft or as notified. Later statuses are reached by moving it on.",
        )
    org = payload.organization_id or getattr(current_user, "organization_id", None)
    await policy.authorize(
        current_user,
        Permissions.CLAIM_CREATE,
        resource_type="claim",
        organization_id=org,
        project_id=payload.project_id,
    )
    # The contract: the only one, the one chosen, or none identifiable (D-23, AQ-1).
    payload.contract_id = resolve_for_new_claim(
        payload.contract_id,
        await valid_contracts(db, policy, current_user, organization_id=org, project_id=payload.project_id),
    )
    legacy_ids = list(payload.linked_document_ids)
    claim_service = ClaimService(db)
    # The full form confirms the type a person chose; a defaulted "other" stays
    # provisional until someone confirms it (owner decision 2026-11-18).
    chosen_type = "type" in payload.model_fields_set and payload.type != ClaimType.OTHER
    created = await claim_service.create(payload, current_user, type_confirmed=chosen_type)
    if legacy_ids:
        try:
            await DocumentRelationshipService(db).replace_legacy_document_ids(
                current_user, "claim", str(created.get("_id") or ""), legacy_ids
            )
        except DocumentRelationshipError:
            await claim_service.delete(
                str(created.get("_id") or ""), current_user, before=created
            )
            raise
    return await _present_claim(created, db, current_user)


async def _load_authorized(
    claim_id: str, permission: str, db, current_user, policy, selection: ActiveScope
) -> dict:
    selection.require_selection()
    claim = await ClaimService(db).get(claim_id)
    if not claim:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Claim not found")
    # Project-less legacy claims are held to the selected organisation.
    await selection.require_record(claim, allow_unscoped=True)
    await policy.authorize_document(current_user, permission, claim, resource_type="claim")
    return claim


@router.get("/claims/{claim_id}", response_model=ClaimRead)
@handle_exceptions
async def get_claim(
    claim_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    claim = await _load_authorized(claim_id, Permissions.CLAIM_VIEW, db, current_user, policy, selection)
    return await _present_claim(claim, db, current_user)


@router.put("/claims/{claim_id}", response_model=ClaimRead)
@handle_exceptions
async def update_claim(
    claim_id: str,
    payload: ClaimUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    claim = await _load_authorized(claim_id, Permissions.CLAIM_EDIT, db, current_user, policy, selection)
    if claim.get("evidence_frozen_at"):
        raise _refuse(
            status.HTTP_409_CONFLICT,
            "claim_evidence_frozen",
            "This claim's record is frozen (it has been escalated to arbitration), so it cannot be edited.",
        )
    changes = payload.model_dump(exclude_unset=True)
    derived = _board_derived_changes(claim, changes)
    if derived:
        raise _refuse(
            status.HTTP_409_CONFLICT,
            "claim_amount_from_board",
            "This claim's amount and days come from its heads on the Claim Bench "
            f"({', '.join(derived)}). Change them there.",
        )
    if changes.get("contract_id") is not None:
        # A claim with no contract may be given one; a contract already set stays (D-23).
        changes["contract_id"] = resolve_for_existing_claim(
            claim,
            changes["contract_id"],
            await valid_contracts(
                db, policy, current_user,
                organization_id=claim.get("organization_id"), project_id=claim.get("project_id"),
            ),
        )
        if changes["contract_id"] == claim.get("contract_id"):
            changes.pop("contract_id")
    else:
        changes.pop("contract_id", None)
    clear = sorted(key for key, value in changes.items() if value is None and key in CLEARABLE_FIELDS)
    expected = None
    if changes.get("status") is not None:
        # The edit form carries a status too: the same lifecycle rule applies here.
        _require_status_transition(claim, ClaimStatus(changes["status"]))
        if changes["status"] != claim.get("status"):
            expected = {"status": claim.get("status")}
    legacy_ids = changes.pop("linked_document_ids", None)
    # A type the edit CHANGES is a person's choice: recorded as one, with its history and
    # the notice decisions it withdraws. The same type sent back by the form confirms nothing.
    new_type = changes.pop("type", None)
    if new_type is not None and str(getattr(new_type, "value", new_type)) == str(
        getattr(claim.get("type"), "value", claim.get("type")) or "other"
    ):
        new_type = None
    notice_before = (await _present_claim(claim, db, current_user)).notice_assessment if new_type else None
    claim_service = ClaimService(db)
    updated = await claim_service.update(
        claim_id, changes, current_user, before=claim, expected=expected, clear=clear
    )
    if updated is None and expected:
        raise _status_conflict()
    if updated and new_type is not None:
        updated = await record_claim_type(
            db, updated, new_type, getattr(current_user, "id", None), via="edit",
            notice_before=notice_before.model_dump(mode="json") if notice_before else None,
        )
    if updated and (
        new_type is not None
        or any(key in changes and changes[key] != claim.get(key) for key in BENCH_FACT_FIELDS)
    ):
        # The heads were tested against the dates and type as they were.
        updated = await mark_bench_tests_stale(db, updated) or updated
    if legacy_ids is not None:
        try:
            await DocumentRelationshipService(db).replace_legacy_document_ids(
                current_user, "claim", claim_id, legacy_ids
            )
        except DocumentRelationshipError:
            raise
        await claim_service.clear_legacy_document_ids(claim_id)
        updated = await claim_service.get(claim_id)
    return await _present_claim(updated or claim, db, current_user)



class ClaimTypeDecision(BaseModel):
    """A person's choice of claim type: confirms it, or changes it."""

    model_config = ConfigDict(extra="forbid")

    claim_type: ClaimType
    reason: Optional[str] = Field(default=None, max_length=500)


@router.post("/claims/{claim_id}/type", response_model=ClaimRead)
@handle_exceptions
async def confirm_claim_type(
    claim_id: str,
    payload: ClaimTypeDecision,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Confirm the claim's type, or change it (owner decision 2026-11-18).

    Until a person has chosen the type, the notice period recorded for it is not
    treated as the contract's period for this claim. A change withdraws the notice
    decisions made under the old type (kept in history) and marks the heads' tests
    stale, so the notice is assessed and decided again.
    """
    claim = await _load_authorized(claim_id, Permissions.CLAIM_EDIT, db, current_user, policy, selection)
    if claim.get("evidence_frozen_at"):
        raise _refuse(
            status.HTTP_409_CONFLICT,
            "claim_evidence_frozen",
            "This claim's record is frozen (it has been escalated to arbitration), so it cannot be edited.",
        )
    before = (await _present_claim(claim, db, current_user)).notice_assessment
    changed = payload.claim_type.value != str(getattr(claim.get("type"), "value", claim.get("type")) or "other")
    updated = await record_claim_type(
        db, claim, payload.claim_type, getattr(current_user, "id", None), via="confirm", reason=payload.reason,
        notice_before=before.model_dump(mode="json") if before else None,
    )
    if changed:
        updated = await mark_bench_tests_stale(db, updated) or updated
    await AuditEventService(db).emit(
        action="claim.type_changed" if changed else "claim.type_confirmed",
        actor_id=getattr(current_user, "id", None),
        resource_type="claim",
        resource_id=str(claim_id),
        organization_id=claim.get("organization_id"),
        project_id=claim.get("project_id"),
        before={"type": claim.get("type"), "type_confirmed": claim.get("type_confirmed")},
        after={"type": updated.get("type"), "type_confirmed": True, "reason": payload.reason},
    )
    return await _present_claim(updated, db, current_user)


class EvidenceSnapshotReview(BaseModel):
    """Re-pin a stale delay-evidence snapshot to the source as it now is, or withdraw it."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["repin", "withdraw"]
    reason: str = Field(..., min_length=5, max_length=1000)


@router.get("/claims/{claim_id}/evidence-snapshots")
@handle_exceptions
async def list_claim_evidence_snapshots(
    claim_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """What the claim's delay records rested on when it was escalated, and whether it still does.

    Checked against the sources on every read; a document the caller may not read is a
    restricted entry with no id, version or hash (D-25)."""
    from ..services import claim_evidence_snapshots as snapshots

    await _load_authorized(claim_id, Permissions.CLAIM_VIEW, db, current_user, policy, selection)
    rows = await snapshots.refresh(db, claim_id=claim_id)
    readable = await snapshots.readable_documents(db, current_user, rows)
    return {"snapshots": snapshots.visible(rows, readable),
            "stale": sum(1 for row in rows if row.get("status") == "stale")}


@router.post("/claims/{claim_id}/evidence-snapshots/{snapshot_id}/review")
@handle_exceptions
async def review_claim_evidence_snapshot(
    claim_id: str,
    snapshot_id: str,
    payload: EvidenceSnapshotReview,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Review a stale snapshot: re-pin (a new snapshot of the current version supersedes it) or
    withdraw. Needs claim management; the reason is recorded and audited."""
    from ..services import claim_evidence_snapshots as snapshots

    claim = await _load_authorized(claim_id, Permissions.CLAIM_MANAGE, db, current_user, policy, selection)
    stored = await db[snapshots.COLLECTION].find_one({"_id": snapshot_id, "claim_id": str(claim_id)})
    if stored and stored.get("document_id") not in await snapshots.readable_documents(db, current_user, [stored]):
        # Reviewing a document means reading it: a restricted one is reviewed by someone who may.
        raise _refuse(status.HTTP_403_FORBIDDEN, "evidence_restricted",
                      "You cannot review this evidence because you do not have access to the document.")
    reviewed = await snapshots.review(db, claim, snapshot_id, payload.action, payload.reason, getattr(current_user, "id", None))
    return snapshots.visible([reviewed], {reviewed.get("document_id")})[0]

@router.post("/claims/{claim_id}/status", response_model=ClaimRead)
@handle_exceptions
async def set_claim_status(
    claim_id: str,
    body: ClaimStatusUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    claim = await _load_authorized(claim_id, Permissions.CLAIM_EDIT, db, current_user, policy, selection)
    _require_status_transition(claim, body.status)
    if claim.get("status") == body.status.value:
        return await _present_claim(claim, db, current_user)
    # Compare-and-set on the status the move was checked against (C-13): of two
    # concurrent moves, one writes and the other answers 409.
    updated = await ClaimService(db).update(
        claim_id, {"status": body.status.value}, current_user, before=claim, expected={"status": claim.get("status")}
    )
    if updated is None:
        raise _status_conflict()
    return await _present_claim(updated, db, current_user)


@router.delete("/claims/{claim_id}", status_code=status.HTTP_204_NO_CONTENT)
@handle_exceptions
async def delete_claim(
    claim_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    await _load_authorized(claim_id, Permissions.CLAIM_DELETE, db, current_user, policy, selection)
    try:
        await DocumentRelationshipService(db, policy=policy).delete_target(
            current_user,
            "claim",
            claim_id,
            reason="Claim deleted",
        )
    except DocumentRelationshipError:
        raise
    return None


# --- approval workflow (Phase 4 / Module 3) -------------------------------
#
# Generic review/approval state machine bound to the claim. The drafter (claim
# creator) can assign + submit; a manager approves or returns and can never be
# the drafter (no self-approval — enforced in ApprovalService).


async def _approval_for(claim: dict, db) -> dict:
    return await ApprovalService(db).get_or_create(
        "claim",
        str(claim["_id"]),
        organization_id=claim.get("organization_id"),
        project_id=claim.get("project_id"),
        drafter_id=claim.get("created_by"),
    )


@router.post("/claims/{claim_id}/assess", response_model=ClaimAssessment)
async def assess_claim(
    claim_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Run a clause-grounded AI assessment of the claim against the project's
    contract (reuses the citation-enforced contract-QA engine). Gated by
    claims.assess; the assessment + its citations + trace are persisted."""
    claim = await _load_authorized(claim_id, Permissions.CLAIM_ASSESS, db, current_user, policy, selection)
    # Imported lazily — keeps the retrieval stack out of this router's import path.
    from .retrieval_engine import get_observability, get_retrieval_service

    observability = await get_observability(db=db)
    retrieval_service = await get_retrieval_service(db=db, observability=observability)
    record = await ClaimAssessmentService(db).assess(claim, current_user, retrieval_service)
    return ClaimAssessment(**record)


@router.get("/claims/{claim_id}/assessments", response_model=List[ClaimAssessment])
async def list_claim_assessments(
    claim_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    claim = await _load_authorized(claim_id, Permissions.CLAIM_VIEW, db, current_user, policy, selection)
    records = await ClaimAssessmentService(db).list(str(claim["_id"]))
    return [ClaimAssessment(**r) for r in records]


@router.get("/claims/{claim_id}/evidence-bundle")
@handle_exceptions
async def export_evidence_bundle(
    claim_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Stream a ZIP evidence bundle: claim record + in-scope linked correspondence
    + the claim's audit trail (CSV) + a manifest. Requires claim-view to read the
    claim and audit-view for the export; the export action is itself audited."""
    claim = await _load_authorized(claim_id, Permissions.CLAIM_VIEW, db, current_user, policy, selection)
    await policy.authorize(
        current_user,
        Permissions.AUDIT_VIEW,
        resource_type="claim",
        organization_id=claim.get("organization_id"),
        project_id=claim.get("project_id"),
    )
    audit = AuditEventService(db)
    events = await audit.query_events(
        organization_id=claim.get("organization_id"),
        project_id=claim.get("project_id"),
        limit=50000,
    )
    events = [e for e in events if str(e.get("resource_id")) == str(claim_id)]
    try:
        document_sources = await DocumentRelationshipService(db).authorized_document_sources(
            current_user,
            "claim",
            claim_id,
            legacy_document_ids=claim.get("linked_document_ids") or [],
        )
    except DocumentRelationshipError:
        raise
    document_ids = [source["document_id"] for source in document_sources]
    # D-25 (4B-46): the bundle is an export of the claim's evidence. If any item of the
    # claim's full evidence set is restricted for the caller, it is refused and audited,
    # naming no item, rather than exported with the restricted items silently left out.
    from ..services.claim_submission import EVIDENCE_RESTRICTED, claim_evidence_ids

    restricted = len(await claim_evidence_ids(db, claim) - {str(item) for item in document_ids})
    if restricted:
        await audit.emit(
            action="claim.evidence_export_refused",
            actor_id=getattr(current_user, "id", None),
            resource_type="claim",
            resource_id=str(claim_id),
            organization_id=claim.get("organization_id"),
            project_id=claim.get("project_id"),
            result="denied",
            reason=EVIDENCE_RESTRICTED,
            after={"restricted_items": restricted},
        )
        message = (
            f"{restricted} item{'s' if restricted != 1 else ''} of this claim's evidence "
            f"{'are' if restricted != 1 else 'is'} restricted for you, so you cannot export its evidence bundle. "
            "The claim's owner, or a person with access to all of its evidence, can export it."
        )
        raise HTTPException(
            status_code=403,
            detail={"code": EVIDENCE_RESTRICTED, "user_message": message, "detail": None, "restricted_items": restricted},
        )
    export_claim = {**claim, "linked_document_ids": document_ids}
    content = await EvidenceBundleService(db).build(
        export_claim,
        events,
        document_sources=document_sources,
        generated_by=getattr(current_user, "id", None),
    )
    await audit.emit(
        action="claim.evidence_exported",
        actor_id=getattr(current_user, "id", None),
        resource_type="claim",
        resource_id=str(claim_id),
        organization_id=claim.get("organization_id"),
        project_id=claim.get("project_id"),
        after={"audit_event_count": len(events), "bytes": len(content)},
    )
    ref = claim.get("claim_ref") or claim_id
    filename = f"evidence-{ref}-{datetime.utcnow().strftime('%Y%m%d')}.zip"
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/claims/{claim_id}/approval", response_model=ApprovalRecord)
async def get_claim_approval(
    claim_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    claim = await _load_authorized(claim_id, Permissions.CLAIM_VIEW, db, current_user, policy, selection)
    return ApprovalRecord(**await _approval_for(claim, db))


@router.post("/claims/{claim_id}/assign", response_model=ApprovalRecord)
async def assign_claim_reviewer(
    claim_id: str,
    body: AssignBody,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    claim = await _load_authorized(claim_id, Permissions.CLAIM_MANAGE, db, current_user, policy, selection)
    record = await _approval_for(claim, db)
    try:
        updated = await ApprovalService(db).assign(
            record, body.reviewer_id, getattr(current_user, "id", None),
            due_at=body.due_at, note=body.note,
        )
    except ApprovalError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return ApprovalRecord(**updated)


@router.post("/claims/{claim_id}/submit-for-review", response_model=ApprovalRecord)
async def submit_claim_for_review(
    claim_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    claim = await _load_authorized(claim_id, Permissions.CLAIM_EDIT, db, current_user, policy, selection)
    record = await _approval_for(claim, db)
    try:
        updated = await ApprovalService(db).submit_for_review(record, getattr(current_user, "id", None))
    except ApprovalError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return ApprovalRecord(**updated)


@router.post("/claims/{claim_id}/approve", response_model=ApprovalRecord)
async def approve_claim(
    claim_id: str,
    body: DecisionBody,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    claim = await _load_authorized(claim_id, Permissions.CLAIM_MANAGE, db, current_user, policy, selection)
    record = await _approval_for(claim, db)
    try:
        updated = await ApprovalService(db).approve(record, getattr(current_user, "id", None), comment=body.comment)
    except ApprovalError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return ApprovalRecord(**updated)


@router.post("/claims/{claim_id}/return", response_model=ApprovalRecord)
async def return_claim(
    claim_id: str,
    body: DecisionBody,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    claim = await _load_authorized(claim_id, Permissions.CLAIM_MANAGE, db, current_user, policy, selection)
    record = await _approval_for(claim, db)
    try:
        updated = await ApprovalService(db).return_for_changes(record, getattr(current_user, "id", None), comment=body.comment)
    except ApprovalError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    return ApprovalRecord(**updated)
