"""Claim register API (Phase 4 / Module 1).

Tenant-scoped CRUD + status transitions for claims. Every endpoint is gated by
PolicyService (permission + entitlement + org/project scope) and lists are
filtered with build_scope_query, so Org A can never see Org B's claims.
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response

from ..core.database import get_db
from ..core.permissions import Permissions
from ..core.security import CurrentUser, build_scope_query, get_current_user
from ..core.tenant_context import ActiveScope, active_scope
from ..models.approval import ApprovalRecord, AssignBody, DecisionBody
from ..models.claim import (
    Claim,
    ClaimAssessment,
    ClaimCreate,
    ClaimRead,
    ClaimStatus,
    ClaimStatusUpdate,
    ClaimUpdate,
    allowed_next_statuses,
)
from ..services.approval_service import ApprovalError, ApprovalService
from ..services.audit_event_service import AuditEventService
from ..services.claim_assessment_service import ClaimAssessmentService
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


async def _read_model(claim: dict, linked_document_ids: list, windows: _NoticeWindows) -> ClaimRead:
    presented = {**claim, "linked_document_ids": linked_document_ids}
    for derived in ("notice_assessment", "allowed_status_transitions", "head_count", "bench_stage"):
        presented.pop(derived, None)
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
    return await _read_model(claim, linked, _NoticeWindows(db))


async def _present_claims(claims: list[dict], db, current_user: CurrentUser) -> list[ClaimRead]:
    ids_by_claim = await DocumentRelationshipService(
        db
    ).authorized_document_ids_for_claims(current_user, claims)
    windows = _NoticeWindows(db)
    return [
        await _read_model(claim, ids_by_claim.get(str(claim.get("_id") or ""), []), windows)
        for claim in claims
    ]


BENCH_FACT_FIELDS = ("type", "event_date", "awareness_date", "notice_date")


async def mark_bench_tests_stale(db, claim: dict) -> Optional[dict]:
    """After a change to a fact the heads were tested against: mark every tested
    head stale and reopen a locked board. Decisions people made are kept."""
    bench = claim.get("bench")
    if not isinstance(bench, dict) or not bench.get("heads"):
        return None
    heads = [
        {**head, "test_stale": True} if head.get("step_id") else head for head in bench["heads"]
    ]
    version = int(bench.get("board_version") or 0)
    update = {
        "bench.heads": heads,
        "bench.stage": "heads",
        "bench.locked_at": None,
        "bench.locked_by": None,
        "bench.board_version": version + 1,
    }
    result = await db.claims.update_one(
        {"_id": claim["_id"], "bench.board_version": version}, {"$set": update}
    )
    if not getattr(result, "matched_count", 0):
        return None
    step_ids = [head["step_id"] for head in bench["heads"] if head.get("step_id")]
    if step_ids:
        await db.skill_steps.update_many({"step_id": {"$in": step_ids}}, {"$set": {"stale": True}})
    return await db.claims.find_one({"_id": claim["_id"]})


#: Statuses a claim may be created in through the register (4B-12, C-13). Later
#: statuses are reached by the lifecycle; "potential" only by a signal.
CREATABLE_STATUSES = frozenset({ClaimStatus.DRAFT, ClaimStatus.NOTIFIED})

#: Fields an update may clear with an explicit null. Others ignore a null.
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
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    claims = await ClaimService(db).list(
        scope,
        claim_type=claim_type,
        status=status_filter,
        responsible_party_id=responsible_party_id,
        skip=skip,
        limit=limit,
    )
    return await _present_claims(claims, db, current_user)


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
    legacy_ids = list(payload.linked_document_ids)
    claim_service = ClaimService(db)
    created = await claim_service.create(payload, current_user)
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
    clear = sorted(key for key, value in changes.items() if value is None and key in CLEARABLE_FIELDS)
    expected = None
    if changes.get("status") is not None:
        # The edit form carries a status too: the same lifecycle rule applies here.
        _require_status_transition(claim, ClaimStatus(changes["status"]))
        if changes["status"] != claim.get("status"):
            expected = {"status": claim.get("status")}
    legacy_ids = changes.pop("linked_document_ids", None)
    claim_service = ClaimService(db)
    updated = await claim_service.update(
        claim_id, changes, current_user, before=claim, expected=expected, clear=clear
    )
    if updated is None and expected:
        raise _status_conflict()
    if updated and any(
        key in changes and changes[key] != claim.get(key) for key in BENCH_FACT_FIELDS
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
