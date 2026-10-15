"""Extraction review routes (Release 1.1.1).

Thin by design: every decision - selection, policy, stale-evidence refusal,
resolution and publication - is made in ``ExtractionReviewService``. The router
only parses the request and schedules the post-resolution publication after
the response, so a slow embedding provider cannot hold the reviewer's request
open. The document is already durably in ``partially_processed`` /
``review_resolved_publication_pending`` when that task starts, and
``POST .../finalize`` resumes it if the process dies first.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from bson import ObjectId
from pydantic import BaseModel, Field

from ..core.database import get_database
from ..core.security import CurrentUser, get_current_user
from ..core.tenant_context import ActiveScope, active_scope
from ..services.extraction_review_service import (
    ACTION_APPROVE,
    ACTION_REJECT,
    REVIEW_PUBLICATION_PENDING,
    ExtractionReviewService,
)
from ..utils.error_handler import BaseDomainError, handle_exceptions

logger = logging.getLogger(__name__)

router = APIRouter()


class PageReviewRequest(BaseModel):
    """What the reviewer saw. The reviewer and the time come from the server."""

    canonical_revision: int = Field(..., ge=1)
    page_checksum: str = Field(..., min_length=16, max_length=128)
    comment: Optional[str] = Field(default=None, max_length=2000)
    #: The page's decision version when it was loaded (0 = undecided).
    expected_decision_version: Optional[int] = Field(default=None, ge=0)


class FinalizeRequest(BaseModel):
    canonical_revision: int = Field(..., ge=1)


def _encode(payload: Any) -> Any:
    return jsonable_encoder(payload, custom_encoder={ObjectId: str})


async def _service() -> ExtractionReviewService:
    return ExtractionReviewService(await get_database())


async def _publish_in_background(service: ExtractionReviewService, document_id: str, revision: int) -> None:
    try:
        outcome = await service.publish_resolved(document_id, expected_revision=revision)
        logger.info("[extraction_review] Publication for %s: %s", document_id, outcome)
    except Exception:
        # The document stays in publication_pending; finalize resumes it.
        logger.exception("[extraction_review] Publication failed for %s", document_id)


def _schedule(
    background: BackgroundTasks, service: ExtractionReviewService, document_id: str, resolution: Dict[str, Any]
) -> None:
    if resolution.get("resolved") and resolution.get("state") == REVIEW_PUBLICATION_PENDING:
        background.add_task(
            _publish_in_background, service, document_id, int(resolution["canonical_revision"])
        )


@router.get("/document-extraction-reviews")
@handle_exceptions
async def list_extraction_review_queue(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current_user: CurrentUser = Depends(get_current_user),
    selection: ActiveScope = Depends(active_scope),
):
    """Documents awaiting extraction review in the caller's selection."""
    try:
        service = await _service()
        return _encode(await service.list_queue(current_user, selection, skip=skip, limit=limit))
    except (BaseDomainError, HTTPException):
        raise


@router.get("/documents/{id}/extraction-review")
@handle_exceptions
async def get_extraction_review(
    id: str,
    current_user: CurrentUser = Depends(get_current_user),
    selection: ActiveScope = Depends(active_scope),
):
    """The pages awaiting review, their machine evidence and any decisions."""
    try:
        service = await _service()
        return _encode(await service.get_review(id, current_user, selection))
    except (BaseDomainError, HTTPException):
        raise


async def _decide(
    id: str,
    page: int,
    action: str,
    body: PageReviewRequest,
    background: BackgroundTasks,
    current_user: CurrentUser,
    selection: ActiveScope,
) -> Any:
    service = await _service()
    result = await service.decide_page(
        id,
        page,
        action=action,
        canonical_revision=body.canonical_revision,
        page_checksum=body.page_checksum,
        comment=body.comment,
        expected_decision_version=body.expected_decision_version,
        current_user=current_user,
        selection=selection,
    )
    _schedule(background, service, id, result.get("resolution") or {})
    return _encode(result)


@router.post("/documents/{id}/extraction-review/pages/{page}/approve")
@handle_exceptions
async def approve_extraction_page(
    id: str,
    page: int,
    body: PageReviewRequest,
    background: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
    selection: ActiveScope = Depends(active_scope),
):
    """Approve the page's extracted canonical content as-is."""
    try:
        return await _decide(id, page, ACTION_APPROVE, body, background, current_user, selection)
    except (BaseDomainError, HTTPException):
        raise


@router.post("/documents/{id}/extraction-review/pages/{page}/reject")
@handle_exceptions
async def reject_extraction_page(
    id: str,
    page: int,
    body: PageReviewRequest,
    background: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
    selection: ActiveScope = Depends(active_scope),
):
    """Reject the page; the document stays in human review."""
    try:
        return await _decide(id, page, ACTION_REJECT, body, background, current_user, selection)
    except (BaseDomainError, HTTPException):
        raise


@router.post("/documents/{id}/extraction-review/finalize")
@handle_exceptions
async def finalize_extraction_review(
    id: str,
    body: FinalizeRequest,
    background: BackgroundTasks,
    current_user: CurrentUser = Depends(get_current_user),
    selection: ActiveScope = Depends(active_scope),
):
    """Resume a resolved review: resolve if complete, then (re)run publication."""
    try:
        service = await _service()
        resolution = await service.finalize(
            id, canonical_revision=body.canonical_revision, current_user=current_user, selection=selection
        )
        _schedule(background, service, id, resolution)
        return _encode(resolution)
    except (BaseDomainError, HTTPException):
        raise
