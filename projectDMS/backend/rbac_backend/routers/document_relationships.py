"""Public HTTP seam for canonical entity-to-Document relationships.

Active project scope (``core/tenant_context.py``) binds the target types whose
adapter sets ``active_scope_enforced`` - Variation and Hindrance (CL-3A),
Programme Milestone and Chronology event (CL-3B). For
those targets every forward route (link, list, freeze, get/remove/history of a
link) needs a selected project that the target is in: 400 ``selection_required``
with nothing selected, 403 ``context_forbidden`` for a target in another project.
The Document reverse lookups hide their rows outside the selection, and Link to
Record offers them only for a Document in the selected project. Every other
target type keeps its existing semantics - it is never refused over the selection
headers.
"""

from __future__ import annotations

from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Response, status

from ..core.database import get_db
from ..core.security import CurrentUser, get_current_user
from ..core.tenant_context import RequestedScope, TenantContextError, requested_scope
from ..models.document_relationship import (
    DocumentRelationshipBatchRequest,
    DocumentRelationshipFreezeRequest,
    DocumentRelationshipListResponse,
    DocumentRelationshipRemovalRequest,
    DocumentRelationshipResponse,
)
from ..services.document_relationship_service import (
    DocumentRelationshipService,
)
from ..utils.error_handler import handle_exceptions
from ..utils.rate_limiter import RateLimiter


router = APIRouter()

#: RFC 9745 structured Deprecation value: deprecated 2026-09-21 00:00 UTC.
HISTORY_DEPRECATED_AT = "@1789948800"


#: Link-to-Record listing loads each candidate through its adapter (a few
#: queries per row, up to LINK_TARGET_SCAN_LIMIT rows) and the dialog queries on
#: every search change, so it gets its own bounded, scoped budget.
LINK_TARGETS_RATE_LIMITER = RateLimiter(max_requests=120, window_seconds=60, scope="document_link_targets")


async def get_document_relationship_service(db=Depends(get_db)) -> DocumentRelationshipService:
    return DocumentRelationshipService(db)


async def _hold_to_selection(
    requested: RequestedScope,
    service: DocumentRelationshipService,
    target_type: str,
    target_id: str,
) -> None:
    """A selection-bound target must be in the selected project (400 when none is selected).

    A missing target passes through so the relationship operation answers its own
    404 - but only once a project is selected, so the refusal order is the same
    whether or not the id exists.
    """
    if service.active_scope_adapter(target_type) is None:
        return
    selection = await requested.resolve()
    selection.require_selection()
    context = await service.active_scope_target(target_type, target_id)
    # A target with no project of its own can hold no canonical link: let the
    # service answer its own 409, which says that, rather than a scope refusal.
    if context is not None and context.project_id:
        await selection.require_project(context.project_id, context.organization_id)


async def _hold_link_to_selection(
    requested: RequestedScope,
    service: DocumentRelationshipService,
    link_id: str,
) -> None:
    target = await service.link_target(link_id)
    if target:
        await _hold_to_selection(requested, service, *target)


async def _visible_under_selection(
    requested: RequestedScope,
    service: DocumentRelationshipService,
    links: list,
) -> list:
    """Reverse lookups: drop selection-bound rows outside the selected project.

    With no project selected the rows stay, bounded by membership exactly like a
    register list (``list_for_document`` already authorized each target). An
    unusable selection (403 on resolve) hides every selection-bound row - fail
    closed for those targets only. Other rows keep their existing behaviour.
    """
    if not any(service.active_scope_adapter(link.target_type) for link in links):
        return links
    try:
        selection = await requested.resolve()
    except TenantContextError:
        selection = None
    return [
        link
        for link in links
        if service.active_scope_adapter(link.target_type) is None
        or (selection is not None and selection.narrows(link.project_id))
    ]


@router.post(
    "/entities/{target_type}/{target_id}/document-links:batch",
    response_model=DocumentRelationshipListResponse,
    status_code=status.HTTP_201_CREATED,
)
@handle_exceptions
async def batch_link_documents(
    target_type: str,
    target_id: str,
    body: DocumentRelationshipBatchRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: DocumentRelationshipService = Depends(get_document_relationship_service),
    requested: RequestedScope = Depends(requested_scope),
) -> DocumentRelationshipListResponse:
    await _hold_to_selection(requested, service, target_type, target_id)
    links = await service.link_batch(
        current_user,
        target_type,
        target_id,
        body.links,
        idempotency_key=body.idempotency_key,
    )
    return DocumentRelationshipListResponse(links=links)


@router.get(
    "/entities/{target_type}/{target_id}/document-links",
    response_model=DocumentRelationshipListResponse,
)
@handle_exceptions
async def list_entity_document_links(
    target_type: str,
    target_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: DocumentRelationshipService = Depends(get_document_relationship_service),
    requested: RequestedScope = Depends(requested_scope),
) -> DocumentRelationshipListResponse:
    await _hold_to_selection(requested, service, target_type, target_id)
    links = await service.list_for_target(current_user, target_type, target_id)
    return DocumentRelationshipListResponse(links=links)


@router.post(
    "/entities/{target_type}/{target_id}/document-links:freeze",
    response_model=DocumentRelationshipListResponse,
)
@handle_exceptions
async def freeze_entity_document_links(
    target_type: str,
    target_id: str,
    body: DocumentRelationshipFreezeRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: DocumentRelationshipService = Depends(get_document_relationship_service),
    requested: RequestedScope = Depends(requested_scope),
) -> DocumentRelationshipListResponse:
    await _hold_to_selection(requested, service, target_type, target_id)
    return DocumentRelationshipListResponse(
        links=await service.freeze(
            current_user, target_type, target_id, reason=body.reason
        )
    )


@router.post(
    "/document-links/{link_id}:remove",
    response_model=DocumentRelationshipResponse,
)
@handle_exceptions
async def remove_document_link(
    link_id: str,
    body: DocumentRelationshipRemovalRequest,
    current_user: CurrentUser = Depends(get_current_user),
    service: DocumentRelationshipService = Depends(get_document_relationship_service),
    requested: RequestedScope = Depends(requested_scope),
) -> DocumentRelationshipResponse:
    await _hold_link_to_selection(requested, service, link_id)
    link = await service.remove(
        current_user,
        link_id,
        reason=body.reason,
        expected_revision=body.expected_revision,
    )
    return DocumentRelationshipResponse(link=link)


@router.get(
    "/document-links/{link_id}",
    response_model=DocumentRelationshipResponse,
)
@handle_exceptions
async def get_document_link(
    link_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: DocumentRelationshipService = Depends(get_document_relationship_service),
    requested: RequestedScope = Depends(requested_scope),
) -> DocumentRelationshipResponse:
    """Current state of one link, including its removal tombstone. Not history."""
    await _hold_link_to_selection(requested, service, link_id)
    return DocumentRelationshipResponse(link=await service.get(current_user, link_id))


@router.get(
    "/document-links/{link_id}/history",
    response_model=DocumentRelationshipListResponse,
    deprecated=True,
)
@handle_exceptions
async def document_link_history(
    link_id: str,
    response: Response,
    current_user: CurrentUser = Depends(get_current_user),
    service: DocumentRelationshipService = Depends(get_document_relationship_service),
    requested: RequestedScope = Depends(requested_scope),
) -> DocumentRelationshipListResponse:
    """Deprecated: returns the link's CURRENT row as a one-element list.

    No revision history is stored - links are soft-removed in place - so this
    route never returned history despite its name. It is kept for compatibility;
    use ``GET /document-links/{link_id}``. The change record is the
    ``document_relationship.linked`` / ``.unlinked`` audit trail.
    """
    await _hold_link_to_selection(requested, service, link_id)
    links = await service.history(current_user, link_id)
    response.headers["Deprecation"] = HISTORY_DEPRECATED_AT
    response.headers["Link"] = (
        f'</api/document-links/{quote(link_id, safe="")}>; rel="successor-version"'
    )
    return DocumentRelationshipListResponse(links=links)


@router.get(
    "/documents/{document_id}/entity-links",
    response_model=DocumentRelationshipListResponse,
)
@handle_exceptions
async def list_document_entity_links(
    document_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: DocumentRelationshipService = Depends(get_document_relationship_service),
    requested: RequestedScope = Depends(requested_scope),
) -> DocumentRelationshipListResponse:
    links = await service.list_for_document(current_user, document_id)
    links = await _visible_under_selection(requested, service, links)
    return DocumentRelationshipListResponse(links=links)


@router.get("/documents/{document_id}/link-targets")
@handle_exceptions
async def list_document_link_targets(
    document_id: str,
    target_type: str = Query(..., min_length=1),
    q: Optional[str] = Query(None, max_length=200),
    limit: int = Query(25, ge=1, le=50),
    current_user: CurrentUser = Depends(get_current_user),
    service: DocumentRelationshipService = Depends(get_document_relationship_service),
    requested: RequestedScope = Depends(requested_scope),
) -> dict:
    """Register records the caller may link this Document to ("Link to Record").

    Offers only targets in the Document's own organisation/project on which the
    caller holds the target's manage permission. The write itself still goes
    through ``POST /entities/{target_type}/{target_id}/document-links:batch``,
    which re-authorizes everything. A selection-bound target type needs a
    selected project (the write will), and offers nothing for a Document outside it.
    """
    await LINK_TARGETS_RATE_LIMITER.check_user_limit(str(getattr(current_user, "id", "") or ""))
    selected_project_id: Optional[str] = None
    if service.active_scope_adapter(target_type) is not None:
        selection = await requested.resolve()
        selected_project_id = selection.require_selection()
    targets = await service.link_targets_for_document(
        current_user,
        document_id,
        target_type,
        query=q or "",
        limit=limit,
        selected_project_id=selected_project_id,
    )
    return {"document_id": document_id, "target_type": target_type, "targets": targets}


@router.get("/documents/{document_id}/link-target-types")
@handle_exceptions
async def list_document_link_target_types(
    document_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: DocumentRelationshipService = Depends(get_document_relationship_service),
    requested: RequestedScope = Depends(requested_scope),
) -> dict:
    """Register types the caller could link this Document to (CL-3B).

    Drives whether the Document Viewer offers "Link to Record" at all. Decided
    from the caller's own manage permissions on the Document's scope - no
    register record is read, so nothing about which records exist leaks. A
    selection-bound type appears only while the navbar selection is the
    Document's project; an unusable selection leaves those types out rather
    than refusing the request.
    """
    await LINK_TARGETS_RATE_LIMITER.check_user_limit(str(getattr(current_user, "id", "") or ""))

    async def selected_project_id() -> Optional[str]:
        try:
            return (await requested.resolve()).project_id
        except TenantContextError:
            return None

    target_types = await service.link_target_types_for_document(
        current_user, document_id, selected_project_id=selected_project_id
    )
    return {"document_id": document_id, "target_types": target_types}


@router.get("/documents/{document_id}/link-dependencies")
@handle_exceptions
async def list_document_link_dependencies(
    document_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: DocumentRelationshipService = Depends(get_document_relationship_service),
    requested: RequestedScope = Depends(requested_scope),
) -> dict:
    links = await _visible_under_selection(
        requested, service, await service.list_for_document(current_user, document_id)
    )
    return {
        "document_id": document_id,
        "active_count": len(links),
        "dependencies": [
            {
                "link_id": link.id,
                "target_type": link.target_type,
                "target_id": link.target_id,
                "target_label": link.target_label,
                "relationship_role": link.relationship_role,
                "frozen": link.frozen_at is not None,
            }
            for link in links
        ],
    }
