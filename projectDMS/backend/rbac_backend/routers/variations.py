"""Variation Register API. PolicyService-gated, tenant-scoped, audited.

Active project scope (CL-3A, ``core/tenant_context.py``, the mechanism the
Hindrance register introduced): the navbar selection (``X-Org-Id`` /
``X-Proj-Id``) is a request boundary, intersected with membership. With a project
selected, a record, a create body or a list filter in another project is 403
``context_forbidden`` - for a member of both projects and for superadmin alike.
Record-level and mutating routes with nothing selected are 400
``selection_required``. Lists with nothing selected stay bounded by
``build_scope_query``, never global. A body's project is refused, never rewritten.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..core.database import get_db
from ..core.permissions import Permissions
from ..core.security import CurrentUser, build_scope_query, get_current_user
from ..core.tenant_context import ActiveScope, active_scope
from ..models.variation import (
    Variation,
    VariationCreate,
    VariationSummary,
    VariationUpdate,
)
from ..services.document_relationship_service import (
    DocumentRelationshipError,
    DocumentRelationshipService,
)
from ..services.policy_service import PolicyService
from ..services.variation_service import LEGACY_WRITE_REFUSAL, VariationService

router = APIRouter()


async def get_policy(db=Depends(get_db)) -> PolicyService:
    return PolicyService(db=db)


async def _load(variation_id: str, permission: str, db, current_user, policy, selection: ActiveScope) -> dict:
    """Load a Variation held to the selected project (400 when none is selected), then authorize."""
    selection.require_selection()
    v = await VariationService(db).get(variation_id)
    if not v:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Variation not found")
    # A legacy Variation with no project stays readable and deletable (CL-2);
    # one that names a project is held to the selection.
    await selection.require_record(v, allow_unscoped=True)
    await policy.authorize_document(current_user, permission, v, resource_type="variation")
    return v


async def _list_scope(
    selection: ActiveScope, organization_id: Optional[str], project_id: Optional[str]
) -> tuple[Optional[str], Optional[str]]:
    """A list filter may narrow the selection, never leave it. Nothing selected: unchanged."""
    if not selection.has_project:
        return organization_id, project_id
    if project_id or organization_id:
        await selection.require_project(project_id or selection.project_id, organization_id)
    return selection.organization_id, selection.project_id


async def _present_variation(variation: dict, db, current_user: CurrentUser) -> Variation:
    """``linked_document_ids`` as the viewer may see it: canonical + legacy ids
    whose Document is in the Variation's own scope and currently viewable."""
    presented = dict(variation)
    try:
        presented["linked_document_ids"] = await DocumentRelationshipService(db).authorized_document_ids(
            current_user,
            "variation",
            str(variation.get("_id") or ""),
            legacy_document_ids=variation.get("linked_document_ids") or [],
        )
    except DocumentRelationshipError as exc:
        # A legacy row without an explicit organisation/project can hold no
        # canonical link (409 from the relationship service). The record itself
        # was already authorized by the caller, so it must stay readable - with
        # no Documents presented, never with its raw array.
        if exc.status_code != status.HTTP_409_CONFLICT:
            raise
        presented["linked_document_ids"] = []
    return Variation(**presented)


async def _present_variations(variations: list[dict], db, current_user: CurrentUser) -> list[Variation]:
    ids_by_variation = await DocumentRelationshipService(db).authorized_document_ids_for_targets(
        current_user, "variation", variations
    )
    return [
        Variation(**{**v, "linked_document_ids": ids_by_variation.get(str(v.get("_id") or ""), [])})
        for v in variations
    ]


def _refuse_legacy_write() -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=LEGACY_WRITE_REFUSAL)


@router.get("/variations", response_model=List[Variation])
async def list_variations(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    contract_id: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    variation_type: Optional[str] = Query(None, alias="type"),
    skip: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=2000),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    organization_id, project_id = await _list_scope(selection, organization_id, project_id)
    await policy.authorize(
        current_user, Permissions.VARIATION_VIEW, resource_type="variations",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id, audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    items = await VariationService(db).list(
        scope, project_id=project_id, contract_id=contract_id,
        status=status_filter, variation_type=variation_type, skip=skip, limit=limit,
    )
    return await _present_variations(items, db, current_user)


@router.get("/variations/summary", response_model=VariationSummary)
async def variation_summary(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    contract_id: Optional[str] = Query(None),
    original_contract_value: Optional[float] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    organization_id, project_id = await _list_scope(selection, organization_id, project_id)
    await policy.authorize(
        current_user, Permissions.VARIATION_VIEW, resource_type="variations",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id, audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    return VariationSummary(**await VariationService(db).summary(
        scope, project_id=project_id, contract_id=contract_id, original_contract_value=original_contract_value,
    ))


@router.get("/variations/export")
async def export_variations(
    format: str = Query("csv", pattern="^(csv|xlsx|pdf)$"),
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    contract_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    organization_id, project_id = await _list_scope(selection, organization_id, project_id)
    await policy.authorize(
        current_user, Permissions.VARIATION_EXPORT, resource_type="variations",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id, audit=False,
    )
    from ..services import contract_controls_export as cx

    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    rows = await VariationService(db).list(scope, project_id=project_id, contract_id=contract_id, limit=5000)
    return cx.export_response("variation-register", cx.VARIATION_COLUMNS, rows, format)


@router.post("/variations", response_model=Variation, status_code=status.HTTP_201_CREATED)
async def create_variation(
    payload: VariationCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    # The body's project must BE the selection: refused, never rewritten.
    await selection.require_project(payload.project_id, payload.organization_id)
    org = payload.organization_id or selection.organization_id
    await policy.authorize(
        current_user, Permissions.VARIATION_CREATE, resource_type="variation",
        organization_id=org, project_id=payload.project_id,
    )
    # Raw ids carry no role and were never authorized against the Document:
    # refused before anything is written. An empty array is a no-op.
    if payload.linked_document_ids:
        raise _refuse_legacy_write()
    if not payload.organization_id and org:
        # The owner organisation of the validated selected project.
        payload = payload.model_copy(update={"organization_id": org})
    created = await VariationService(db).create(payload, current_user)
    return await _present_variation(created, db, current_user)


@router.get("/variations/{variation_id}", response_model=Variation)
async def get_variation(
    variation_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    return await _present_variation(
        await _load(variation_id, Permissions.VARIATION_VIEW, db, current_user, policy, selection), db, current_user
    )


@router.put("/variations/{variation_id}", response_model=Variation)
async def update_variation(
    variation_id: str,
    payload: VariationUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    # Status moves into approved/rejected require the approve permission.
    perm = Permissions.VARIATION_EDIT
    if payload.status in {"approved", "rejected"}:
        perm = Permissions.VARIATION_APPROVE
    v = await _load(variation_id, perm, db, current_user, policy, selection)
    changes = payload.model_dump(exclude_unset=True)
    if "linked_document_ids" in changes:
        requested = changes.pop("linked_document_ids")
        # Echoing back exactly what this caller was shown (a client that PUTs the
        # whole record it read) is compatible and ignored. Anything else - adding,
        # forging, clearing, or guessing the raw stored array - is a relationship
        # write and goes through the canonical endpoint. Comparing with the
        # presented ids, never the raw array, keeps this from becoming an oracle
        # for legacy ids the caller may not see.
        shown = set((await _present_variation(v, db, current_user)).linked_document_ids)
        if requested is None or {str(item) for item in requested} != shown:
            raise _refuse_legacy_write()
    updated = await VariationService(db).update(v, changes, current_user)
    return await _present_variation(updated or v, db, current_user)


@router.delete("/variations/{variation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_variation(
    variation_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    selection: ActiveScope = Depends(active_scope),
):
    v = await _load(variation_id, Permissions.VARIATION_DELETE, db, current_user, policy, selection)
    try:
        await VariationService(db).delete(v, current_user, policy=policy)
    except DocumentRelationshipError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return None
