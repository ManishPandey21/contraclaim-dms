"""Public HTTP seam for canonical entity-to-Document relationships."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Response, status

from ..core.database import get_db
from ..core.security import CurrentUser, get_current_user
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

#: RFC 9745 structured Deprecation value: deprecated 2026-09-21 00:00 UTC.
HISTORY_DEPRECATED_AT = "@1789948800"


async def get_document_relationship_service(db=Depends(get_db)) -> DocumentRelationshipService:
    return DocumentRelationshipService(db)


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
) -> DocumentRelationshipListResponse:
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
) -> DocumentRelationshipListResponse:
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
) -> DocumentRelationshipListResponse:
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
) -> DocumentRelationshipResponse:
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
) -> DocumentRelationshipResponse:
    """Current state of one link, including its removal tombstone. Not history."""
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
) -> DocumentRelationshipListResponse:
    """Deprecated: returns the link's CURRENT row as a one-element list.

    No revision history is stored - links are soft-removed in place - so this
    route never returned history despite its name. It is kept for compatibility;
    use ``GET /document-links/{link_id}``. The change record is the
    ``document_relationship.linked`` / ``.unlinked`` audit trail.
    """
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
) -> DocumentRelationshipListResponse:
    return DocumentRelationshipListResponse(
        links=await service.list_for_document(current_user, document_id)
    )


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
