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
from pydantic import BaseModel, Field, field_validator

from ..core.database import get_database
from ..core.permissions import Permissions
from ..core.security import CurrentUser, get_current_user
from ..core.tenant_context import ActiveScope, active_scope
from datetime import date

from ..models.contract_document import (
    ApplicabilityLifecycleKind,
    Browse,
    ContractDocumentType,
    CurrentState,
    Historical,
)
from ..services.contract_candidate_authority import (
    authorize_candidate,
    require_organization_wide_scope,
    require_selected_organization,
    visible_candidates,
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
    NOT_ECHOED,
    CandidateNotFound,
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
    AlreadyPromoted,
    ContractPromotionService,
    NotPromotable,
    RevalidationRequired,
)
from ..services.contract_scope_resolver import (
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
from ..services.policy_service import PolicyService
from ..services.publication_policy import is_consumable, resolve_canonical_document
from ..utils.error_handler import BaseDomainError, ContractError

logger = logging.getLogger(__name__)

router = APIRouter()


# --------------------------------------------------------------------------- #
# dependencies
# --------------------------------------------------------------------------- #


async def get_db():
    return await get_database()


async def get_policy(db=Depends(get_db)) -> PolicyService:
    return PolicyService(db=db)


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
    #: The review row's ``document_fingerprint``, echoed back: the decision is
    #: then tied to the content the operator actually saw.
    expected_fingerprint: Optional[str] = None


class PromotionCommand(BaseModel):
    contract_id: Optional[str] = None
    #: An ISO date or nothing. Unknown stays unknown; a typo is a 422, never an
    #: "unknown" legal start.
    effective_from: Optional[date] = None

    @field_validator("effective_from", mode="before")
    @classmethod
    def _iso_date_only(cls, value: Any) -> Any:
        # Lax parsing would read 20260301 as a unix timestamp (1970-08-23).
        if value is None or isinstance(value, date):
            return value
        if not isinstance(value, str):
            raise ValueError("effective_from must be an ISO date string (YYYY-MM-DD)")
        return date.fromisoformat(value)


class CapabilityResponse(BaseModel):
    """Each flag: may this actor, under the CURRENT navbar selection, act on some
    record through that route. Answered by the same questions the route asks, so
    a flag is never true for a route that would refuse everything."""

    can_browse_catalogue: bool
    can_view_instruments: bool
    can_manage_classification: bool
    can_manage_applicability: bool
    can_upload_organization_scope: bool
    can_upload_project_scope: bool
    can_review_migration: bool
    can_promote: bool
    #: Inventory, materialise, unanchored candidates and organisation-scope
    #: decisions: organisation-wide scope, which no permission expresses.
    can_manage_organization_migration: bool = False


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
    # Production Documents are ObjectId-keyed; instruments carry str(_id). The
    # one identity resolver tries each spelling of this id, in a fixed order.
    document = await resolve_canonical_document(db, str(record.get("document_id") or ""))
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
    organization_id: str = Query(..., min_length=1),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy=Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Organisation-owned instruments, including those applicable to nothing.

    Catalogue membership is not evidence: a zero-applicability instrument is
    listed as CATALOGUED / UNASSIGNED and carries no applicability basis, so it
    cannot be mistaken for something a consumer may cite.
    """
    await selection.require_organization(organization_id)
    await policy.authorize(
        current_user,
        Permissions.CONTRACT_CATALOGUE_BROWSE,
        resource_type="contract_document",
        organization_id=organization_id,
        project_id=None,
        audit=False,
    )
    # The gate answers membership; rows are bounded. Organisation-level
    # instruments are the organisation's catalogue; a project's instrument is
    # listed to organisation-wide scope and to that project's members - and, with
    # a project selected, only when it is the selected one.
    organization_wide = await policy.scope_service.has_organization_wide_scope(
        current_user, organization_id=organization_id
    )
    assigned = (
        set() if organization_wide else await policy.scope_service.client_project_ids(current_user)
    )
    records = [
        record
        for record in await db[CONTRACT_DOCUMENTS_COLLECTION].find(
            {"organization_id": organization_id}
        ).to_list(length=None)
        if _catalogue_visible(record, organization_wide, assigned, selection.project_id)
    ]
    return CatalogueResponse(
        items=[CatalogueItem(**await _decorate(db, record)) for record in records]
    )


def _catalogue_visible(
    record: Dict[str, Any], organization_wide: bool, assigned: set, selected_project: Optional[str]
) -> bool:
    project_id = str(record.get("project_id") or "")
    if not project_id:
        return True
    if selected_project and project_id != selected_project:
        return False
    return organization_wide or project_id in assigned


async def _load_instrument(db, contract_document_id: str, selection: ActiveScope) -> Dict[str, Any]:
    """CL-4A record route: the instrument loaded INSIDE the selected organisation.

    Loaded with the selected organisation, so another organisation's instrument is
    the same 404 as a missing one - never a 403 that confirms it exists. A
    project's instrument then needs that project selected (400 / 403); an
    organisation-level one needs only its organisation - the same rule as a
    reconciliation candidate with no project.
    """
    require_selected_organization(selection, None)
    record = await db[CONTRACT_DOCUMENTS_COLLECTION].find_one(
        {"_id": contract_document_id, "organization_id": str(selection.organization_id or "")}
    )
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown instrument")
    if record.get("project_id"):
        await selection.require_project(record.get("project_id"), record.get("organization_id"))
    return record


# --------------------------------------------------------------------------- #
# 2. instrument detail
# --------------------------------------------------------------------------- #


@router.get("/contract-master/instruments/{contract_document_id}", response_model=InstrumentDetail)
async def get_instrument(
    contract_document_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy=Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Seven orthogonal dimensions, each derived server-side."""
    record = await _load_instrument(db, contract_document_id, selection)

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
    selection: ActiveScope = Depends(active_scope),
):
    record = await _load_instrument(db, contract_document_id, selection)
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
    selection: ActiveScope = Depends(active_scope),
):
    """Confirm or correct a classification through the T07 lifecycle.

    The revision check, the append-only fact and the projection invalidation all
    live in the service; this hands over the operator's intent and translates the
    two refusals it can produce into 409 and 422.
    """
    record = await _load_instrument(db, contract_document_id, selection)

    # A project's instrument is authorised at its project. Retyping an
    # organisation-level instrument changes what governs every project, so it
    # needs organisation-wide scope, not membership of one.
    if record.get("project_id"):
        await policy.authorize(
            current_user,
            Permissions.CONTRACT_MASTER_MANAGE,
            resource_type="contract_document",
            organization_id=record.get("organization_id"),
            project_id=record.get("project_id"),
        )
    else:
        await require_organization_wide_scope(
            policy,
            current_user,
            permission=Permissions.CONTRACT_MASTER_MANAGE,
            organization_id=str(record.get("organization_id")),
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
    selection: ActiveScope = Depends(active_scope),
):
    """Apply, withdraw or supersede - always as an explicit, confirmed act.

    Applicability is never inferred here from the upload's project, the
    instrument's scope, or the caller's navbar. The caller states the project and
    contract, and the event stream records what they said.
    """
    record = await _load_instrument(db, contract_document_id, selection)

    # The project it applies to must BE the selected one: refused, never rewritten.
    await selection.require_project(command.project_id, record.get("organization_id"))
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
    selection: ActiveScope = Depends(active_scope),
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

    # Contract evidence is project-specific, so it needs the navbar selection
    # (CL-4A, core/tenant_context.py): a selected project, validated against the
    # principal - global roles included - before anything else. Nothing is inferred:
    # not from the body's project, not from contract_id, not from the account's
    # home organisation. The body names the project it searches, and it must be the
    # selected one (403 context_forbidden, even for a member of both).
    selected_project = selection.require_selection()
    await selection.require_project(command.project_id)
    organization_id = str(selection.organization_id or "")
    # The one place a scope token is minted: the (organisation, project) pair is
    # proven, then PolicyService. Never the test-only constructor.
    scope = await authorize_contract_scope(
        policy,
        current_user,
        permission=Permissions.CONTRACT_MASTER_VIEW,
        organization_id=organization_id,
        project_id=selected_project,
        contract_id=command.contract_id,
        # A read, audited as the route always was: not per search.
        audit=False,
    )

    query_mode = _build_query_mode(command)

    service = ContractService()
    service._db = db
    service._vectors = db.document_vectors
    service._documents = db.documents

    request = ContractSearchRequest(
        query=command.query,
        organization_id=organization_id,
        project_id=selected_project,
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
    organization_id: str = Query(..., min_length=1),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy=Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Read-only preview. Writes nothing, including no reconciliation row.

    Every contract Document of the organisation, so organisation-wide scope: a
    member holding the permission for one project does not preview the rest.
    """
    await selection.require_organization(organization_id)
    await require_organization_wide_scope(
        policy,
        current_user,
        permission=Permissions.CONTRACT_MASTER_MANAGE,
        organization_id=organization_id,
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
    selection: ActiveScope = Depends(active_scope),
):
    """The separate, explicitly named write. Never a side effect of inventory."""
    require_selected_organization(selection, command.organization_id)
    await selection.require_organization(command.organization_id)
    await require_organization_wide_scope(
        policy,
        current_user,
        permission=Permissions.CONTRACT_MASTER_MANAGE,
        organization_id=command.organization_id,
    )
    service = ContractMigrationReconciliation(db)
    candidates = await service.inventory(organization_id=command.organization_id)
    written = await service.materialise_inventory(candidates)
    return {"materialised": written}


@router.get("/contract-master/reconciliation/candidates")
async def reconciliation_review(
    organization_id: str = Query(..., min_length=1),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy=Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """The review queue, with both axes reported independently.

    The gate answers membership; row visibility is bounded to the caller's
    projects. Organisation-wide scope sees every candidate; anyone else sees the
    candidates anchored to a project they may act in, and no unanchored one.
    With a project selected, only that project's candidates (and the selected
    organisation's unanchored ones) are listed.
    """
    await selection.require_organization(organization_id)
    await policy.authorize(
        current_user,
        Permissions.CONTRACT_MASTER_MANAGE,
        resource_type="contract_document",
        organization_id=organization_id,
        project_id=None,
        audit=False,
    )
    organization_wide = await policy.scope_service.has_organization_wide_scope(
        current_user, organization_id=organization_id
    )
    visible = await visible_candidates(
        policy,
        current_user,
        db=db,
        rows=await db[RECONCILIATION_COLLECTION].find(
            {"organization_id": organization_id}
        ).to_list(length=None),
        organization_wide=organization_wide,
        selected_project=selection.project_id,
    )
    fingerprints = await _document_fingerprints(db, [row for row, _ in visible])
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
                # Acting on an anchored row needs its project selected; an
                # unanchored or conflicted one is organisation business.
                "project_anchor": anchor.project_id,
                # Echo it as expected_fingerprint when adjudicating.
                "document_fingerprint": fingerprints.get(
                    str(row.get("canonical_document_id") or "")
                ),
                "anchor_conflict": anchor.conflict,
                "promotion_blocked_reasons": _promotion_blockers(row, anchor),
            }
            for row, anchor in visible
        ]
    }


async def _document_fingerprints(db, rows: List[Dict[str, Any]]) -> Dict[str, Optional[str]]:
    """``canonical_document_id`` -> the fingerprint an operator reviews, in one query."""
    from ..services.publication_policy import document_id_candidates

    keys: List[Any] = []
    for row in rows:
        keys.extend(document_id_candidates(str(row.get("canonical_document_id") or "")))
    if not keys:
        return {}
    found = await db["documents"].find(
        {"_id": {"$in": keys}}, {"checksum": 1, "sha256": 1}
    ).to_list(length=None)
    return {
        str(doc["_id"]): (
            str(doc.get("checksum") or doc.get("sha256"))
            if (doc.get("checksum") or doc.get("sha256"))
            else None
        )
        for doc in found
    }


def _promotion_blockers(row: Dict[str, Any], anchor: Any = None) -> List[str]:
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
    if anchor is not None and anchor.conflict:
        blockers.append(
            "anchor: conflicted - the project fields disagree, or name a project that "
            "is not an active project of this organisation; it can only be set aside"
        )
    if row.get("promoted"):
        blockers.append("promoted: already an instrument")
    return blockers


@router.post("/contract-master/reconciliation/candidates/{candidate_id}/claim")
async def claim_candidate(
    candidate_id: str,
    organization_id: str = Query(..., min_length=1),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy=Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    # CL-4A: held to the selected organisation here, and - once the candidate is
    # loaded - to the selected project when it has one (authorize_candidate).
    await selection.require_organization(organization_id)
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
        # Bound to the organisation just authorised: a candidate of any other
        # organisation is a 404, indistinguishable from one that does not exist.
        # Then authorised at the candidate's own project anchor, so membership of
        # the organisation is not authority over another project's candidate.
        await authorize_candidate(
            policy,
            current_user,
            db=db,
            candidate_id=candidate_id,
            organization_id=organization_id,
            permission=Permissions.CONTRACT_MASTER_MANAGE,
            # A candidate already decided at organisation scope, set aside, or
            # promoted is organisation business: a project-tier lease on it would
            # lock the organisation out of its own decision.
            organization_wide=lambda candidate: bool(candidate.get("promoted"))
            or candidate.get("scope_state")
            in (
                ScopeClassificationState.ORG_SCOPE_CONFIRMED.value,
                ScopeClassificationState.INVALID.value,
            ),
            selection=selection,
        )
        claim = await service.claim(
            candidate_id,
            organization_id=organization_id,
            operator_id=str(getattr(current_user, "id", "")),
            organization_wide=await policy.scope_service.has_organization_wide_scope(
                current_user, organization_id=organization_id
            ),
        )
    except CandidateNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ClaimUnavailable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return {"candidate_id": claim.candidate_id, "owner_token": claim.owner_token}


@router.post("/contract-master/reconciliation/candidates/{candidate_id}/adjudicate")
async def adjudicate_candidate(
    candidate_id: str,
    command: AdjudicationCommand,
    owner_token: str = Query(...),
    organization_id: str = Query(..., min_length=1),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy=Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """Record one operator's decision. Ownership is proven, not assumed."""
    await selection.require_organization(organization_id)
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
    try:
        # Parsed inside the try so an unknown value is the 422 below, not a 500.
        document_type = (
            ContractDocumentType(command.contract_document_type)
            if command.contract_document_type
            else None
        )
        scope_state = ScopeClassificationState(command.scope_state)
        # Declaring organisation scope creates organisation-wide authority, and
        # re-deciding a candidate already declared organisation-scope overrides
        # it; both need organisation scope whatever the anchor. Checked before
        # the lease, so a refusal reveals nothing about who holds the claim.
        authority = await authorize_candidate(
            policy,
            current_user,
            db=db,
            candidate_id=candidate_id,
            organization_id=organization_id,
            permission=Permissions.CONTRACT_MASTER_MANAGE,
            organization_wide=lambda candidate: (
                scope_state
                in (
                    ScopeClassificationState.ORG_SCOPE_CONFIRMED,
                    # INVALID is terminal for the organisation's whole migration.
                    ScopeClassificationState.INVALID,
                )
                or candidate.get("scope_state")
                == ScopeClassificationState.ORG_SCOPE_CONFIRMED.value
            ),
            selection=selection,
        )
        if authority.anchor.conflict and scope_state not in (
            ScopeClassificationState.INVALID,
            ScopeClassificationState.AMBIGUOUS,
        ):
            # A conflicted candidate never promotes; recording a promotable scope
            # on it would be a decision that leads nowhere.
            raise ValueError(
                "this candidate's project anchor is conflicted; it can only be set "
                "aside (INVALID) or left AMBIGUOUS"
            )
        if (
            scope_state is ScopeClassificationState.PROJECT_SCOPE_CONFIRMED
            and not authority.project_id
        ):
            # Project scope with no trustworthy project can never promote; an
            # operator must not record a decision that leads nowhere.
            raise ValueError(
                "project scope needs a trustworthy project anchor, and this candidate "
                "has none (its Document names no active project of this organisation)"
            )
        await ContractMigrationAdjudication(db).adjudicate(
            claim,
            organization_id=organization_id,
            scope_state=scope_state,
            contract_document_type=document_type,
            reason=command.reason,
            # Omitted skips the check; an explicit null means "I saw none".
            expected_fingerprint=(
                command.expected_fingerprint
                if "expected_fingerprint" in command.model_fields_set
                else NOT_ECHOED
            ),
        )
    except CandidateNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
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
    organization_id: str = Query(..., min_length=1),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy=Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """One candidate, by explicit selection, through the atomic primitive."""
    await selection.require_organization(organization_id)
    await policy.authorize(
        current_user,
        Permissions.CONTRACT_MASTER_MANAGE,
        resource_type="contract_document",
        organization_id=organization_id,
        project_id=None,
    )

    client = getattr(db, "client", None)
    try:
        # At the candidate's project anchor; an organisation-scope candidate
        # becomes an organisation-wide instrument, so it needs organisation scope.
        authority = await authorize_candidate(
            policy,
            current_user,
            db=db,
            candidate_id=candidate_id,
            organization_id=organization_id,
            permission=Permissions.CONTRACT_MASTER_MANAGE,
            organization_wide=lambda candidate: candidate.get("scope_state")
            == ScopeClassificationState.ORG_SCOPE_CONFIRMED.value,
            selection=selection,
        )
        if command.contract_id:
            # The first applicability is written at the anchored project.
            if authority.project_id:
                await policy.authorize(
                    current_user,
                    Permissions.CONTRACT_APPLICABILITY_MANAGE,
                    resource_type="contract_document",
                    organization_id=organization_id,
                    project_id=authority.project_id,
                )
            else:
                await require_organization_wide_scope(
                    policy,
                    current_user,
                    permission=Permissions.CONTRACT_APPLICABILITY_MANAGE,
                    organization_id=organization_id,
                )
        receipt = await ContractPromotionService(db, client).promote(
            candidate_id,
            organization_id=organization_id,
            actor_id=str(getattr(current_user, "id", "")),
            contract_id=command.contract_id,
            effective_from=(
                command.effective_from.isoformat() if command.effective_from else None
            ),
            expected_scope_state=authority.candidate.get("scope_state"),
            expected_project_id=authority.project_id or "",
        )
    except CandidateNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AlreadyPromoted as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
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
    organization_id: str = Query(..., min_length=1),
    project_id: Optional[str] = Query(None),
    current_user: CurrentUser = Depends(get_current_user),
    policy=Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    """What this actor may do, as the server sees it.

    Each capability is answered by *asking the policy service the same question
    the route itself would ask*, rather than by reading a permission list off
    the user or inspecting a role name. That matters twice over: the answer
    cannot drift from the enforcement, and there is nothing here for a client to
    test a role against - a client-side role check is a second copy of the
    permission model that can only ever hide a control the server would refuse
    anyway.

    Answered inside the navbar selection: an organisation or project filter may
    narrow it, never leave it.
    """
    selected_org, project_id = await selection.list_filters(organization_id, project_id)
    organization_id = selected_org or organization_id

    async def may(permission, *, scope_project_id=None) -> bool:
        # Only a refusal is an answer here. An outage, a conflict, an expired
        # session or a bug is not "you may not": it propagates as what it is,
        # rather than greying out the UI as though the caller lacked authority.
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
        except HTTPException as exc:
            if exc.status_code == status.HTTP_403_FORBIDDEN:
                return False
            raise
        except BaseDomainError as exc:
            if getattr(exc, "http_status", None) == status.HTTP_403_FORBIDDEN:
                return False
            raise

    can_upload = await may(Permissions.DOCUMENT_UPLOAD, scope_project_id=project_id)
    # Organisation-scope acts need organisation-wide scope, which no permission
    # expresses (see services/contract_candidate_authority.py).
    organization_wide = await policy.scope_service.has_organization_wide_scope(
        current_user, organization_id=organization_id
    )
    organisation_upload = organization_wide
    for capability in ORGANIZATION_SCOPE_UPLOAD_CAPABILITIES:
        if not await may(capability):
            organisation_upload = False
            break

    # The reconciliation and instrument routes all gate membership with MANAGE
    # at the organisation, then act per record: a project's record at its
    # project (which must be the selected one), an organisation-level record with
    # organisation-wide scope. The flags ask exactly that.
    can_manage = await may(Permissions.CONTRACT_MASTER_MANAGE)
    manage_selected_project = bool(project_id) and await may(
        Permissions.CONTRACT_MASTER_MANAGE, scope_project_id=project_id
    )
    manage_organisation_level = can_manage and organization_wide
    act_on_some_record = can_manage and (manage_selected_project or manage_organisation_level)
    return CapabilityResponse(
        can_browse_catalogue=await may(Permissions.CONTRACT_CATALOGUE_BROWSE),
        can_view_instruments=await may(Permissions.CONTRACT_MASTER_VIEW),
        can_manage_classification=act_on_some_record,
        # The applicability body names a project, which must be the selected one.
        can_manage_applicability=bool(project_id)
        and await may(Permissions.CONTRACT_APPLICABILITY_MANAGE, scope_project_id=project_id),
        can_upload_organization_scope=organisation_upload,
        can_upload_project_scope=can_upload,
        # The queue is 200 bounded to the caller's projects for any MANAGE holder.
        can_review_migration=can_manage,
        can_promote=act_on_some_record,
        can_manage_organization_migration=manage_organisation_level,
    )
