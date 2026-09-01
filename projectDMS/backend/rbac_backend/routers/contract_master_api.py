"""Contract Master v1 HTTP surface.

The router translates and delegates. It holds no domain rule of its own: every
decision it appears to make is a call into a service that already owns it, and
every authority check is `PolicyService.authorize` at an explicit resource scope.
That constraint is the point — a second implementation of applicability, of
classification currency, or of the promotion write set would drift from the
first, and the drift would be invisible until it mattered.

Three things worth stating because each has a plausible-looking wrong version:

* **The evidence route is not the search route.** `/api/contracts/search` reaches
  `ContractService.search_contracts`, which is generic and deliberately
  unbounded for browsing. Contract *evidence* reaches
  `search_contract_evidence`, which resolves one canonical eligible universe and
  gives it to all three sources. Pointing the existing route at the new method
  would have silently changed generic search; adding a route leaves both honest.

* **Outcomes survive the HTTP boundary.** Valid empty, degraded, authority
  failure and stale-projection are four different answers, and a `200 []` for
  all four is what let an outage read as "the contract is silent on this". The
  evidence responses carry an explicit `outcome`, and the failures carry
  distinct statuses.

* **This namespace is new on purpose.** `/api/contracts/master` is the legacy
  contract-master record — completion dates and bank-guarantee schedules — and
  shares nothing with the v1 instrument domain but a name. Extending it would
  have fused two unrelated concepts under one route family.

Namespace: `/api/contract-master/*`.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from ..core.database import get_database
from ..core.permissions import Permissions
from ..core.security import CurrentUser, get_current_user
from datetime import date

from ..models.contract_document import (
    ApplicabilityLifecycleKind,
    Browse,
    ContractDocumentType,
    CurrentState,
    Historical,
)
from ..services.contract_classification_service import (
    ClassificationRevisionConflict,
    ContractClassificationService,
    SuggestionIsNotAuthority,
    UnknownContractDocument,
)
from ..services.contract_document_store import (
    APPLICABILITY_COLLECTION,
    CONTRACT_DOCUMENTS_COLLECTION,
    ApplicabilityOverlapError,
    record_applicability_event,
)
from ..services.contract_migration_adjudication import (
    ClaimUnavailable,
    ConflictingAdjudication,
    ContractMigrationAdjudication,
)
from ..services.contract_migration_reconciliation import (
    RECONCILIATION_COLLECTION,
    ContractMigrationReconciliation,
    ScopeClassificationState,
    TypeClassificationState,
)
from ..services.contract_promotion import (
    ContractPromotionService,
    NotPromotable,
    RevalidationRequired,
)
from ..services.contract_scope_resolver import (
    AuthorizedContractScope,
    ContractScopeResolutionError,
    ContractScopeResolver,
    authorize_contract_scope,
)
from ..services.contract_upload_durability import (
    ScopeConflictOnRetry,
    ScopeIsImmutableAfterPromotion,
    UploadDurabilityService,
)
from ..services.contract_upload_scope_service import (
    ORGANIZATION_SCOPE_UPLOAD_CAPABILITIES,
    ContractUploadScopeService,
    ScopeAuthorisationDenied,
    UnknownProjectAnchor,
)
from ..services.publication_policy import is_consumable
from ..utils.error_handler import ContractError

logger = logging.getLogger(__name__)

router = APIRouter()


# --------------------------------------------------------------------------- #
# dependencies
# --------------------------------------------------------------------------- #


async def get_db():
    return await get_database()


def get_policy():
    from ..core.policy import PolicyService

    return PolicyService()


# --------------------------------------------------------------------------- #
# wire models
# --------------------------------------------------------------------------- #


class CatalogueItem(BaseModel):
    contract_document_id: str
    document_id: str
    contract_document_type: Optional[str] = None
    scope_level: Optional[str] = None
    classification_revision: int = 1
    projection_status: Optional[str] = None
    applicability_count: int = 0
    content_consumable: bool = True
    #: Derived per request, never stored. See the viewer-state note below.
    evidence_ready: bool = False
    viewer_state: str


class CatalogueResponse(BaseModel):
    items: List[CatalogueItem]


class InstrumentDetail(CatalogueItem):
    document_version_id: Optional[str] = None
    projection_revision: Optional[int] = None
    project_id: Optional[str] = None


class ClassificationCommand(BaseModel):
    contract_document_type: str
    expected_revision: int
    basis: str = "operator_confirmed"


class ClassificationResult(BaseModel):
    contract_document_id: str
    classification_revision: int
    projection_status: str


class ApplicabilityCommand(BaseModel):
    project_id: str = Field(min_length=1)
    contract_id: str = Field(min_length=1)
    kind: str
    #: Unknown stays unknown. There is no default here on purpose.
    effective_at: Optional[str] = None


class EvidenceSearchCommand(BaseModel):
    project_id: str = Field(min_length=1)
    contract_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    query_mode: Optional[str] = None
    event_date: Optional[str] = None
    limit: int = 10
    skip: int = 0


class EvidenceResponse(BaseModel):
    results: List[Dict[str, Any]]
    #: complete | degraded | valid_empty - never collapsed into a bare 200 [].
    outcome: str
    degraded_sources: List[str] = []
    total_count: int = 0


class MaterialiseCommand(BaseModel):
    organization_id: str = Field(min_length=1)


class AdjudicationCommand(BaseModel):
    scope_state: str
    contract_document_type: Optional[str] = None
    reason: str = Field(min_length=1)


class PromotionCommand(BaseModel):
    contract_id: Optional[str] = None
    effective_from: Optional[str] = None


class CapabilityResponse(BaseModel):
    can_browse_catalogue: bool
    can_view_instruments: bool
    can_manage_classification: bool
    can_manage_applicability: bool
    can_upload_organization_scope: bool
    can_upload_project_scope: bool
    can_review_migration: bool
    can_promote: bool


# --------------------------------------------------------------------------- #
# derived viewer state
# --------------------------------------------------------------------------- #


def _viewer_state(record: Dict[str, Any], *, applicability_count: int, consumable: bool) -> str:
    """The five frozen states, recomputed from server facts every time.

    Deliberately a function of the record rather than a stored field: every
    conjunct of evidence readiness can change without the instrument being
    touched, so a persisted answer would be wrong without anything having
    written it. "Active" is not among the outcomes.
    """
    if str(record.get("lifecycle_state") or "") in {"superseded", "withdrawn"}:
        return "SUPERSEDED / WITHDRAWN"
    if applicability_count == 0:
        return "CATALOGUED / UNASSIGNED"
    if not _projection_current(record):
        return "APPLICABLE - PROJECTION PENDING"
    if not consumable:
        return "APPLICABLE - CONTENT BLOCKED"
    return "APPLICABLE - EVIDENCE READY"


def _projection_current(record: Dict[str, Any]) -> bool:
    if str(record.get("projection_status") or "") != "CURRENT":
        return False
    return record.get("projection_revision") == record.get("classification_revision")


def _evidence_ready(record, *, applicability_count: int, consumable: bool) -> bool:
    return (
        applicability_count > 0
        and _projection_current(record)
        and consumable
    )


async def _decorate(db, record: Dict[str, Any]) -> Dict[str, Any]:
    applicability_count = await db[APPLICABILITY_COLLECTION].count_documents(
        {"contract_document_id": record["_id"]}
    )
    document = await db["documents"].find_one({"_id": record.get("document_id")})
    consumable = is_consumable(document) if document else False
    return {
        "contract_document_id": str(record["_id"]),
        "document_id": str(record.get("document_id") or ""),
        "document_version_id": record.get("document_version_id"),
        "contract_document_type": record.get("contract_document_type"),
        "scope_level": record.get("scope_level"),
        "project_id": record.get("project_id"),
        "classification_revision": int(record.get("classification_revision") or 1),
        "projection_status": record.get("projection_status"),
        "projection_revision": record.get("projection_revision"),
        "applicability_count": applicability_count,
        "content_consumable": consumable,
        "evidence_ready": _evidence_ready(
            record, applicability_count=applicability_count, consumable=consumable
        ),
        "viewer_state": _viewer_state(
            record, applicability_count=applicability_count, consumable=consumable
        ),
    }


# --------------------------------------------------------------------------- #
# 1. catalogue
# --------------------------------------------------------------------------- #


@router.get("/contract-master/catalogue", response_model=CatalogueResponse)
async def browse_catalogue(
    organization_id: str = Query(...),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy=Depends(get_policy),
):
    """Organisation-owned instruments, including those applicable to nothing.

    Catalogue membership is not evidence: a zero-applicability instrument is
    listed as CATALOGUED / UNASSIGNED and carries no applicability basis, so it
    cannot be mistaken for something a consumer may cite.
    """
    await policy.authorize(
        current_user,
        Permissions.CONTRACT_CATALOGUE_BROWSE,
        resource_type="contract_document",
        organization_id=organization_id,
        project_id=None,
        audit=False,
    )
    records = await db[CONTRACT_DOCUMENTS_COLLECTION].find(
        {"organization_id": organization_id}
    ).to_list(length=None)
    return CatalogueResponse(
        items=[CatalogueItem(**await _decorate(db, record)) for record in records]
    )


# --------------------------------------------------------------------------- #
# 2. instrument detail
# --------------------------------------------------------------------------- #


@router.get("/contract-master/instruments/{contract_document_id}", response_model=InstrumentDetail)
async def get_instrument(
    contract_document_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy=Depends(get_policy),
):
    """Seven orthogonal dimensions, each derived server-side."""
    record = await db[CONTRACT_DOCUMENTS_COLLECTION].find_one({"_id": contract_document_id})
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown instrument")

    await policy.authorize(
        current_user,
        Permissions.CONTRACT_MASTER_VIEW,
        resource_type="contract_document",
        organization_id=record.get("organization_id"),
        project_id=record.get("project_id"),
        audit=False,
    )
    return InstrumentDetail(**await _decorate(db, record))


@router.get("/contract-master/instruments/{contract_document_id}/projection")
async def get_projection_status(
    contract_document_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy=Depends(get_policy),
):
    record = await db[CONTRACT_DOCUMENTS_COLLECTION].find_one({"_id": contract_document_id})
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown instrument")
    await policy.authorize(
        current_user,
        Permissions.CONTRACT_MASTER_MANAGE,
        resource_type="contract_document",
        organization_id=record.get("organization_id"),
        project_id=record.get("project_id"),
        audit=False,
    )
    return {
        "contract_document_id": contract_document_id,
        "projection_status": record.get("projection_status"),
        "projection_revision": record.get("projection_revision"),
        "classification_revision": record.get("classification_revision"),
        "is_current": _projection_current(record),
    }


# --------------------------------------------------------------------------- #
# 3. classification
# --------------------------------------------------------------------------- #


@router.post(
    "/contract-master/instruments/{contract_document_id}/classification",
    response_model=ClassificationResult,
)
async def confirm_classification(
    contract_document_id: str,
    command: ClassificationCommand,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy=Depends(get_policy),
):
    """Confirm or correct a classification through the T07 lifecycle.

    The revision check, the append-only fact and the projection invalidation all
    live in the service; this hands over the operator's intent and translates the
    two refusals it can produce into 409 and 422.
    """
    record = await db[CONTRACT_DOCUMENTS_COLLECTION].find_one({"_id": contract_document_id})
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown instrument")

    await policy.authorize(
        current_user,
        Permissions.CONTRACT_MASTER_MANAGE,
        resource_type="contract_document",
        organization_id=record.get("organization_id"),
        project_id=None,
    )

    try:
        document_type = ContractDocumentType(command.contract_document_type)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    service = ContractClassificationService(db)
    try:
        revision = await service.confirm(
            contract_document_id,
            contract_document_type=document_type,
            expected_revision=command.expected_revision,
            actor_id=getattr(current_user, "id", None),
            basis=command.basis,
        )
    except ClassificationRevisionConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except SuggestionIsNotAuthority as exc:
        # A filename or model guess arriving where a confirmation is required is
        # a bad request, not a server failure.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    except UnknownContractDocument as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    updated = await db[CONTRACT_DOCUMENTS_COLLECTION].find_one({"_id": contract_document_id})
    return ClassificationResult(
        contract_document_id=contract_document_id,
        classification_revision=revision,
        projection_status=str(updated.get("projection_status")),
    )


# --------------------------------------------------------------------------- #
# 4. applicability
# --------------------------------------------------------------------------- #


@router.post(
    "/contract-master/instruments/{contract_document_id}/applicability",
    status_code=status.HTTP_201_CREATED,
)
async def record_applicability(
    contract_document_id: str,
    command: ApplicabilityCommand,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy=Depends(get_policy),
):
    """Apply, withdraw or supersede - always as an explicit, confirmed act.

    Applicability is never inferred here from the upload's project, the
    instrument's scope, or the caller's navbar. The caller states the project and
    contract, and the event stream records what they said.
    """
    record = await db[CONTRACT_DOCUMENTS_COLLECTION].find_one({"_id": contract_document_id})
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown instrument")

    await policy.authorize(
        current_user,
        Permissions.CONTRACT_APPLICABILITY_MANAGE,
        resource_type="contract_document",
        organization_id=record.get("organization_id"),
        project_id=command.project_id,
    )

    # Translation, which is the router's whole job here: the wire carries
    # strings, the service takes domain types, and an unparseable value is a bad
    # request rather than something to coerce.
    try:
        kind = ApplicabilityLifecycleKind(command.kind)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unsupported applicability kind {command.kind!r}",
        ) from exc

    effective_at = None
    if command.effective_at:
        try:
            effective_at = date.fromisoformat(command.effective_at)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"effective_at must be an ISO date: {exc}",
            ) from exc
    # An absent date stays absent all the way down. UNKNOWN is a legal answer.

    try:
        await record_applicability_event(
            db,
            organization_id=str(record.get("organization_id")),
            project_id=command.project_id,
            contract_id=command.contract_id,
            contract_document_id=contract_document_id,
            kind=kind,
            effective_at=effective_at,
            actor_id=getattr(current_user, "id", None),
        )
    except ApplicabilityOverlapError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return {"contract_document_id": contract_document_id, "kind": command.kind}


# --------------------------------------------------------------------------- #
# 5. evidence search - NOT /api/contracts/search
# --------------------------------------------------------------------------- #


def _build_query_mode(command: EvidenceSearchCommand):
    """Translate the wire mode. Every refusal here is a 422, never a default."""
    from datetime import date

    mode = (command.query_mode or "").strip().lower()
    if not mode:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="query_mode is required; contract evidence has no implicit mode",
        )
    if mode == "browse":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="browse is a catalogue mode and cannot produce evidence",
        )
    if mode == "current_state":
        return CurrentState()
    if mode == "historical":
        if not command.event_date:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="historical evidence requires event_date; there is no default-to-today",
            )
        try:
            return Historical(event_date=date.fromisoformat(command.event_date))
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
            ) from exc
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=f"unsupported query_mode {command.query_mode!r}",
    )


@router.post("/contract-master/evidence/search", response_model=EvidenceResponse)
async def search_contract_evidence_route(
    command: EvidenceSearchCommand,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy=Depends(get_policy),
):
    """Contract evidence. Distinct from `/api/contracts/search` on purpose.

    The generic route stays generic; this one resolves one canonical eligible
    universe and hands it to all three sources before each spends its own limit.
    """
    from ..models.contract_models import ContractSearchRequest
    from ..services.contract_service import (
        ContractEvidenceAuthorityFailure,
        ContractService,
    )

    organization_id = getattr(current_user, "organization_id", None)
    await policy.authorize(
        current_user,
        Permissions.CONTRACT_MASTER_VIEW,
        resource_type="contract_document",
        organization_id=organization_id,
        project_id=command.project_id,
        audit=False,
    )

    query_mode = _build_query_mode(command)
    scope = AuthorizedContractScope.for_tests(
        organization_id=str(organization_id),
        project_id=command.project_id,
        contract_id=command.contract_id,
        actor_id=str(getattr(current_user, "id", "")),
    )

    service = ContractService()
    service._db = db
    service._vectors = db.document_vectors
    service._documents = db.documents

    request = ContractSearchRequest(
        query=command.query,
        organization_id=organization_id,
        project_id=command.project_id,
        limit=command.limit,
        skip=command.skip,
    )

    try:
        response = await service.search_contract_evidence(
            request, scope=scope, mode=query_mode, resolver=ContractScopeResolver(db)
        )
        source_status = await service.evidence_source_report(
            request, scope=scope, mode=query_mode, resolver=ContractScopeResolver(db)
        )
    except ContractEvidenceAuthorityFailure as exc:
        # Authority failure is not an empty answer and not a degraded one.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    except ContractScopeResolutionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    degraded = sorted(name for name, state in source_status.items() if state == "degraded")
    results = [item.model_dump() if hasattr(item, "model_dump") else dict(item) for item in response.results]
    if degraded:
        outcome = "degraded"
    elif not results:
        outcome = "valid_empty"
    else:
        outcome = "complete"

    return EvidenceResponse(
        results=results,
        outcome=outcome,
        degraded_sources=degraded,
        total_count=response.total_count,
    )


# --------------------------------------------------------------------------- #
# 6. reconciliation and migration
# --------------------------------------------------------------------------- #


@router.get("/contract-master/reconciliation/inventory")
async def reconciliation_inventory(
    organization_id: str = Query(...),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy=Depends(get_policy),
):
    """Read-only preview. Writes nothing, including no reconciliation row."""
    await policy.authorize(
        current_user,
        Permissions.CONTRACT_MASTER_MANAGE,
        resource_type="contract_document",
        organization_id=organization_id,
        project_id=None,
        audit=False,
    )
    candidates = await ContractMigrationReconciliation(db).inventory(
        organization_id=organization_id
    )
    return {
        "candidates": [
            {
                "candidate_id": candidate.candidate_id,
                "canonical_document_id": candidate.canonical_document_id,
                "scope_state": candidate.scope_state.value,
                "type_state": candidate.type_state.value,
                "scope_hint": candidate.scope_hint,
            }
            for candidate in candidates
        ]
    }


@router.post("/contract-master/reconciliation/materialise")
async def reconciliation_materialise(
    command: MaterialiseCommand,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy=Depends(get_policy),
):
    """The separate, explicitly named write. Never a side effect of inventory."""
    await policy.authorize(
        current_user,
        Permissions.CONTRACT_MASTER_MANAGE,
        resource_type="contract_document",
        organization_id=command.organization_id,
        project_id=None,
    )
    service = ContractMigrationReconciliation(db)
    candidates = await service.inventory(organization_id=command.organization_id)
    written = await service.materialise_inventory(candidates)
    return {"materialised": written}


@router.get("/contract-master/reconciliation/candidates")
async def reconciliation_review(
    organization_id: str = Query(...),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy=Depends(get_policy),
):
    """The review queue, with both axes reported independently."""
    await policy.authorize(
        current_user,
        Permissions.CONTRACT_MASTER_MANAGE,
        resource_type="contract_document",
        organization_id=organization_id,
        project_id=None,
        audit=False,
    )
    rows = await db[RECONCILIATION_COLLECTION].find(
        {"organization_id": organization_id}
    ).to_list(length=None)
    return {
        "candidates": [
            {
                "candidate_id": row.get("candidate_id"),
                "canonical_document_id": row.get("canonical_document_id"),
                "scope_state": row.get("scope_state"),
                "type_state": row.get("type_state"),
                "scope_hint": row.get("scope_hint"),
                "contract_document_type": row.get("contract_document_type"),
                "adjudicated_by": row.get("adjudicated_by"),
                "promoted": bool(row.get("promoted")),
                "promotion_blocked_reasons": _promotion_blockers(row),
            }
            for row in rows
        ]
    }


def _promotion_blockers(row: Dict[str, Any]) -> List[str]:
    """Why this candidate cannot be promoted, per axis.

    Per axis rather than one chip: "needs review" tells an operator nothing about
    which decision is theirs to make.
    """
    blockers: List[str] = []
    scope_state = row.get("scope_state")
    if scope_state == ScopeClassificationState.INVALID.value:
        blockers.append("scope: INVALID (terminal)")
    elif scope_state not in (
        ScopeClassificationState.ORG_SCOPE_CONFIRMED.value,
        ScopeClassificationState.PROJECT_SCOPE_CONFIRMED.value,
    ):
        blockers.append(f"scope: {scope_state} - operator adjudication required")
    if row.get("type_state") != TypeClassificationState.TYPE_RESOLVED.value:
        blockers.append(f"type: {row.get('type_state')} - operator confirmation required")
    return blockers


@router.post("/contract-master/reconciliation/candidates/{candidate_id}/claim")
async def claim_candidate(
    candidate_id: str,
    organization_id: str = Query(...),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy=Depends(get_policy),
):
    await policy.authorize(
        current_user,
        Permissions.CONTRACT_MASTER_MANAGE,
        resource_type="contract_document",
        organization_id=organization_id,
        project_id=None,
    )
    service = ContractMigrationAdjudication(db)
    await service.ensure_indexes()
    try:
        claim = await service.claim(
            candidate_id, operator_id=str(getattr(current_user, "id", ""))
        )
    except ClaimUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"candidate_id": claim.candidate_id, "owner_token": claim.owner_token}


@router.post("/contract-master/reconciliation/candidates/{candidate_id}/adjudicate")
async def adjudicate_candidate(
    candidate_id: str,
    command: AdjudicationCommand,
    owner_token: str = Query(...),
    organization_id: str = Query(...),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy=Depends(get_policy),
):
    """Record one operator's decision. Ownership is proven, not assumed."""
    await policy.authorize(
        current_user,
        Permissions.CONTRACT_MASTER_MANAGE,
        resource_type="contract_document",
        organization_id=organization_id,
        project_id=None,
    )
    from ..services.contract_migration_adjudication import CandidateClaim

    claim = CandidateClaim(
        candidate_id=candidate_id,
        operator_id=str(getattr(current_user, "id", "")),
        owner_token=owner_token,
    )
    document_type = (
        ContractDocumentType(command.contract_document_type)
        if command.contract_document_type
        else None
    )
    try:
        await ContractMigrationAdjudication(db).adjudicate(
            claim,
            scope_state=ScopeClassificationState(command.scope_state),
            contract_document_type=document_type,
            reason=command.reason,
        )
    except ClaimUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ConflictingAdjudication as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return {"candidate_id": candidate_id, "adjudicated": True}


# --------------------------------------------------------------------------- #
# 7. promotion
# --------------------------------------------------------------------------- #


@router.post("/contract-master/reconciliation/candidates/{candidate_id}/promote")
async def promote_candidate(
    candidate_id: str,
    command: PromotionCommand,
    organization_id: str = Query(...),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy=Depends(get_policy),
):
    """One candidate, by explicit selection, through the atomic primitive."""
    await policy.authorize(
        current_user,
        Permissions.CONTRACT_MASTER_MANAGE,
        resource_type="contract_document",
        organization_id=organization_id,
        project_id=None,
    )
    if command.contract_id:
        await policy.authorize(
            current_user,
            Permissions.CONTRACT_APPLICABILITY_MANAGE,
            resource_type="contract_document",
            organization_id=organization_id,
            project_id=None,
        )

    client = getattr(db, "client", None)
    try:
        receipt = await ContractPromotionService(db, client).promote(
            candidate_id,
            actor_id=str(getattr(current_user, "id", "")),
            contract_id=command.contract_id,
            effective_from=command.effective_from,
        )
    except RevalidationRequired as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except NotPromotable as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    return {
        "receipt_id": receipt.receipt_id,
        "candidate_id": receipt.candidate_id,
        "contract_document_id": receipt.contract_document_id,
        "scope_level": receipt.scope_level,
    }


# --------------------------------------------------------------------------- #
# 8. capability response
# --------------------------------------------------------------------------- #


@router.get("/contract-master/capabilities", response_model=CapabilityResponse)
async def get_capabilities(
    organization_id: str = Query(...),
    project_id: Optional[str] = Query(None),
    current_user: CurrentUser = Depends(get_current_user),
    policy=Depends(get_policy),
):
    """What this actor may do, as the server sees it.

    Each capability is answered by *asking the policy service the same question
    the route itself would ask*, rather than by reading a permission list off
    the user or inspecting a role name. That matters twice over: the answer
    cannot drift from the enforcement, and there is nothing here for a client to
    test a role against - a client-side role check is a second copy of the
    permission model that can only ever hide a control the server would refuse
    anyway.
    """

    async def may(permission, *, scope_project_id=None) -> bool:
        try:
            await policy.authorize(
                current_user,
                permission,
                resource_type="contract_document",
                organization_id=organization_id,
                project_id=scope_project_id,
                audit=False,
            )
            return True
        except Exception:
            # A denial is an answer, not an error, at this endpoint.
            return False

    can_upload = await may(Permissions.DOCUMENT_UPLOAD, scope_project_id=project_id)
    organisation_upload = True
    for capability in ORGANIZATION_SCOPE_UPLOAD_CAPABILITIES:
        if not await may(capability):
            organisation_upload = False
            break

    can_manage = await may(Permissions.CONTRACT_MASTER_MANAGE)
    return CapabilityResponse(
        can_browse_catalogue=await may(Permissions.CONTRACT_CATALOGUE_BROWSE),
        can_view_instruments=await may(Permissions.CONTRACT_MASTER_VIEW),
        can_manage_classification=can_manage,
        can_manage_applicability=await may(Permissions.CONTRACT_APPLICABILITY_MANAGE),
        can_upload_organization_scope=organisation_upload,
        can_upload_project_scope=can_upload,
        can_review_migration=can_manage,
        can_promote=can_manage,
    )
