"""Grounded letter summary routes (Release 1.2).

Thin: the gate, staleness and withholding are decided in
``GroundedSummaryService``. Both routes authorise exactly like
``GET /documents/{id}`` - the navbar selection must hold the record, then
``PolicyService.authorize_document`` decides - so a summary is visible to
precisely the people who may open the document.

A dedicated route rather than a wider document payload: whether a stored
summary is current needs the canonical head, and the detailed summary is
larger than anything the register and card views should carry.
"""

from __future__ import annotations

from typing import Any

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder

from ..core.database import get_database
from ..core.permissions import Permissions
from ..core.security import CurrentUser, get_current_user
from ..core.tenant_context import ActiveScope, active_scope
from ..services.grounded_summary.service import GroundedSummaryService
from ..utils.error_handler import BaseDomainError, handle_exceptions

router = APIRouter()


def _encode(payload: Any) -> Any:
    return jsonable_encoder(payload, custom_encoder={ObjectId: str})


async def _service() -> GroundedSummaryService:
    return GroundedSummaryService(await get_database())


@router.get("/documents/{id}/grounded-summary")
@handle_exceptions
async def get_grounded_summary(
    id: str,
    current_user: CurrentUser = Depends(get_current_user),
    selection: ActiveScope = Depends(active_scope),
):
    """Short and detailed summary, only when they describe the current evidence."""
    try:
        service = await _service()
        return _encode(await service.read_for_user(id, current_user, selection))
    except (BaseDomainError, HTTPException):
        raise


@router.post("/documents/{id}/grounded-summary/generate")
@handle_exceptions
async def generate_grounded_summary(
    id: str,
    current_user: CurrentUser = Depends(get_current_user),
    selection: ActiveScope = Depends(active_scope),
):
    """Generate (or regenerate) for the current canonical revision, on demand.

    The lazy path for documents processed before Release 1.2. Never a corpus
    backfill: one document per request, by a user who may edit its metadata.
    """
    try:
        service = await _service()
        document = await service.authorised_document(
            id, current_user, selection, Permissions.DOCUMENT_EDIT_METADATA
        )
        await service.generate(str(document["_id"]), trigger="on_demand")
        refreshed = await service.authorised_document(id, current_user, selection, Permissions.DOCUMENT_VIEW)
        return _encode(await service.view(refreshed))
    except (BaseDomainError, HTTPException):
        raise
