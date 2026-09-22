"""Public HTTP seam for canonical entity-to-Document relationships.

Active project scope applies ONLY to the `delay_event` (Hindrance) target in this
phase (owner decision 2026-09-22): its forward routes need a matching selected
project, and the document reverse lookup hides Hindrance rows outside the
selection. Every other target type keeps its existing semantics - an unrelated
target is never refused over the selection headers.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

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


router = APIRouter()


async def get_document_relationship_service(db=Depends(get_db)) -> DocumentRelationshipService:
    return DocumentRelationshipService(db)


SCOPED_TARGET_TYPE = "delay_event"


async def _hold_to_selection(requested: RequestedScope, target_type: str, target_id: str) -> None:
    """A Hindrance target must be in the selected project (400 when none is selected)."""
    if target_type != SCOPED_TARGET_TYPE:
        return
    from ..services.hindrance_register_service import HindranceRegisterService

    selection = await requested.resolve()
    selection.require_selection()
    item = await HindranceRegisterService(requested.db).get(target_id)
    if item:
        await selection.require_record(item)


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
    await _hold_to_selection(requested, target_type, target_id)
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
    await _hold_to_selection(requested, target_type, target_id)
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
    await _hold_to_selection(requested, target_type, target_id)
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
    target = await service.link_target(link_id)
    if target:
        await _hold_to_selection(requested, *target)
    link = await service.remove(
        current_user,
        link_id,
        reason=body.reason,
        expected_revision=body.expected_revision,
    )
    return DocumentRelationshipResponse(link=link)


@router.get(
    "/document-links/{link_id}/history",
    response_model=DocumentRelationshipListResponse,
)
@handle_exceptions
async def document_link_history(
    link_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: DocumentRelationshipService = Depends(get_document_relationship_service),
    requested: RequestedScope = Depends(requested_scope),
) -> DocumentRelationshipListResponse:
    target = await service.link_target(link_id)
    if target:
        await _hold_to_selection(requested, *target)
    return DocumentRelationshipListResponse(links=await service.history(current_user, link_id))


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
    if any(link.target_type == SCOPED_TARGET_TYPE for link in links):
        try:
            selection = await requested.resolve()
        except TenantContextError:
            # An unusable selection hides every Hindrance row (fail closed for the
            # scoped target only); other rows keep their existing behaviour.
            selection = None
        links = [
            link
            for link in links
            if link.target_type != SCOPED_TARGET_TYPE or (selection is not None and selection.narrows(link.project_id))
        ]
    return DocumentRelationshipListResponse(links=links)


@router.get("/documents/{document_id}/link-dependencies")
@handle_exceptions
async def list_document_link_dependencies(
    document_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    service: DocumentRelationshipService = Depends(get_document_relationship_service),
) -> dict:
    links = await service.list_for_document(current_user, document_id)
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
