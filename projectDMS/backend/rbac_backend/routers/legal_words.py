from __future__ import annotations

from datetime import date
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..core.security import CurrentUser, get_current_user, require_permission
from ..models.legal_word import (
    LegalWord,
    LegalWordAISuggestionResponse,
    LegalWordCreate,
    LegalWordListResponse,
    LegalWordPublishRequest,
    LegalWordPublicationType,
    LegalWordPublishedListResponse,
    LegalWordScheduleRequest,
    LegalWordSearchRequest,
    LegalWordSearchResponse,
    LegalWordSource,
    LegalWordStatus,
    LegalWordUpdate,
    TodayLegalWordsResponse,
)
from ..services.legal_word_service import LegalWordService, LegalWordServiceError
from ..utils.error_handler import handle_exceptions


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/legal-words", tags=["legal-words"])
admin_router = APIRouter(prefix="/admin/legal-words", tags=["legal-words-admin"])
LEGAL_WORD_ADMIN_PERMISSION = "system:admin"


async def get_legal_word_service() -> LegalWordService:
    return LegalWordService()


def _raise_service_error(exc: LegalWordServiceError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=str(exc))


@router.get("/today", response_model=TodayLegalWordsResponse)
@handle_exceptions
async def get_today_legal_words(
    date_value: Optional[date] = Query(default=None, alias="date"),
    service: LegalWordService = Depends(get_legal_word_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Return the platform-wide contractual/legal words published for a date."""
    _ = current_user
    try:
        return await service.get_today_words(date_value)
    except LegalWordServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to get today's legal words")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Legal words service temporarily unavailable",
        ) from exc


@router.post("/search", response_model=LegalWordSearchResponse)
@handle_exceptions
async def search_legal_word(
    payload: LegalWordSearchRequest,
    service: LegalWordService = Depends(get_legal_word_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Search a contractual/legal word or submit a missing word for review."""
    try:
        return await service.search_or_request_word(payload.query, current_user)
    except LegalWordServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to search legal word")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Legal words search temporarily unavailable",
        ) from exc


@router.get("/published", response_model=LegalWordPublishedListResponse)
@handle_exceptions
async def list_published_legal_words(
    skip: int = Query(0, ge=0),
    limit: int = Query(6, ge=1, le=200),
    search: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
    published_date: Optional[date] = Query(default=None),
    publication_type: Optional[LegalWordPublicationType] = Query(default=None),
    service: LegalWordService = Depends(get_legal_word_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Return all published contractual/legal words as publication events, newest first."""
    _ = current_user
    try:
        words, total = await service.list_published_words(
            search=search,
            category=category,
            published_date=published_date,
            publication_type=publication_type.value if publication_type else None,
            skip=skip,
            limit=limit,
        )
        return LegalWordPublishedListResponse(
            words=words,
            total=total,
            page=skip // limit + 1,
            limit=limit,
        )
    except LegalWordServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to list published legal words")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Published legal words service temporarily unavailable",
        ) from exc


@admin_router.get("", response_model=LegalWordListResponse)
@handle_exceptions
async def list_admin_legal_words(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    status_value: Optional[LegalWordStatus] = Query(default=None, alias="status"),
    source: Optional[LegalWordSource] = Query(default=None),
    category: Optional[str] = Query(default=None),
    published_date: Optional[date] = Query(default=None),
    publication_type: Optional[LegalWordPublicationType] = Query(default=None),
    search: Optional[str] = Query(default=None),
    service: LegalWordService = Depends(get_legal_word_service),
    current_user: CurrentUser = Depends(get_current_user),
    _admin_allowed: bool = Depends(require_permission(LEGAL_WORD_ADMIN_PERMISSION)),
):
    """List legal word records for admin review and management."""
    _ = _admin_allowed
    try:
        words, total = await service.list_words(
            status=status_value.value if status_value else None,
            source=source.value if source else None,
            category=category,
            published_date=published_date,
            publication_type=publication_type.value if publication_type else None,
            search=search,
            skip=skip,
            limit=limit,
        )
        return LegalWordListResponse(
            words=words,
            total=total,
            page=skip // limit + 1,
            limit=limit,
        )
    except LegalWordServiceError as exc:
        raise _raise_service_error(exc) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to list admin legal words")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Legal words admin service temporarily unavailable",
        ) from exc


@admin_router.post("", response_model=LegalWord)
@handle_exceptions
async def create_admin_legal_word(
    payload: LegalWordCreate,
    service: LegalWordService = Depends(get_legal_word_service),
    current_user: CurrentUser = Depends(get_current_user),
    _admin_allowed: bool = Depends(require_permission(LEGAL_WORD_ADMIN_PERMISSION)),
):
    """Create a contractual/legal word record for admin review or approval."""
    _ = _admin_allowed
    try:
        return await service.create_word(payload, current_user)
    except LegalWordServiceError as exc:
        raise _raise_service_error(exc) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to create legal word")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Legal word creation service temporarily unavailable",
        ) from exc


@admin_router.post("/suggest-ai", response_model=LegalWordAISuggestionResponse)
@handle_exceptions
async def suggest_admin_legal_words_with_ai(
    service: LegalWordService = Depends(get_legal_word_service),
    current_user: CurrentUser = Depends(get_current_user),
    _admin_allowed: bool = Depends(require_permission(LEGAL_WORD_ADMIN_PERMISSION)),
):
    """Suggest contractual/legal words with AI and return publication eligibility."""
    _ = _admin_allowed
    try:
        return await service.suggest_words_with_ai(current_user)
    except LegalWordServiceError as exc:
        raise _raise_service_error(exc) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to suggest legal words with AI")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="AI legal word suggestion service temporarily unavailable",
        ) from exc


@admin_router.patch("/{word_id}", response_model=LegalWord)
@handle_exceptions
async def update_admin_legal_word(
    word_id: str,
    payload: LegalWordUpdate,
    service: LegalWordService = Depends(get_legal_word_service),
    current_user: CurrentUser = Depends(get_current_user),
    _admin_allowed: bool = Depends(require_permission(LEGAL_WORD_ADMIN_PERMISSION)),
):
    """Update legal word content, status, or scheduling fields."""
    _ = _admin_allowed
    try:
        return await service.update_word(word_id, payload, current_user)
    except LegalWordServiceError as exc:
        raise _raise_service_error(exc) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to update legal word %s", word_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Legal word update service temporarily unavailable",
        ) from exc


@admin_router.post("/{word_id}/approve", response_model=LegalWord)
@handle_exceptions
async def approve_admin_legal_word(
    word_id: str,
    service: LegalWordService = Depends(get_legal_word_service),
    current_user: CurrentUser = Depends(get_current_user),
    _admin_allowed: bool = Depends(require_permission(LEGAL_WORD_ADMIN_PERMISSION)),
):
    """Approve a complete word so it becomes eligible for publication."""
    _ = _admin_allowed
    try:
        return await service.approve_word(word_id, current_user)
    except LegalWordServiceError as exc:
        raise _raise_service_error(exc) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to approve legal word %s", word_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Legal word approval service temporarily unavailable",
        ) from exc


@admin_router.post("/{word_id}/reject", response_model=LegalWord)
@handle_exceptions
async def reject_admin_legal_word(
    word_id: str,
    service: LegalWordService = Depends(get_legal_word_service),
    current_user: CurrentUser = Depends(get_current_user),
    _admin_allowed: bool = Depends(require_permission(LEGAL_WORD_ADMIN_PERMISSION)),
):
    """Reject a pending or requested legal word."""
    _ = _admin_allowed
    try:
        return await service.reject_word(word_id, current_user)
    except LegalWordServiceError as exc:
        raise _raise_service_error(exc) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to reject legal word %s", word_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Legal word rejection service temporarily unavailable",
        ) from exc


@admin_router.post("/{word_id}/deactivate", response_model=LegalWord)
@handle_exceptions
async def deactivate_admin_legal_word(
    word_id: str,
    service: LegalWordService = Depends(get_legal_word_service),
    current_user: CurrentUser = Depends(get_current_user),
    _admin_allowed: bool = Depends(require_permission(LEGAL_WORD_ADMIN_PERMISSION)),
):
    """Deactivate a word so it is hidden and ineligible for future publication."""
    _ = _admin_allowed
    try:
        return await service.deactivate_word(word_id, current_user)
    except LegalWordServiceError as exc:
        raise _raise_service_error(exc) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to deactivate legal word %s", word_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Legal word deactivation service temporarily unavailable",
        ) from exc


@admin_router.post("/{word_id}/schedule", response_model=LegalWord)
@handle_exceptions
async def schedule_admin_legal_word(
    word_id: str,
    payload: LegalWordScheduleRequest,
    service: LegalWordService = Depends(get_legal_word_service),
    current_user: CurrentUser = Depends(get_current_user),
    _admin_allowed: bool = Depends(require_permission(LEGAL_WORD_ADMIN_PERMISSION)),
):
    """Schedule an approved word for a future daily publication date."""
    _ = _admin_allowed
    try:
        return await service.schedule_word(
            word_id,
            payload.scheduled_date,
            current_user,
        )
    except LegalWordServiceError as exc:
        raise _raise_service_error(exc) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to schedule legal word %s", word_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Legal word scheduling service temporarily unavailable",
        ) from exc


@admin_router.post("/{word_id}/publish", response_model=TodayLegalWordsResponse)
@handle_exceptions
async def publish_admin_legal_word(
    word_id: str,
    payload: LegalWordPublishRequest | None = None,
    service: LegalWordService = Depends(get_legal_word_service),
    current_user: CurrentUser = Depends(get_current_user),
    _admin_allowed: bool = Depends(require_permission(LEGAL_WORD_ADMIN_PERMISSION)),
):
    """Publish an approved eligible word into the shared daily word set."""
    _ = _admin_allowed
    try:
        return await service.publish_word_immediately(
            word_id,
            current_user,
            for_date=payload.published_date if payload else None,
        )
    except LegalWordServiceError as exc:
        raise _raise_service_error(exc) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to publish legal word %s", word_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Legal word publishing service temporarily unavailable",
        ) from exc


@admin_router.post("/{word_id}/unpublish", response_model=LegalWord)
@handle_exceptions
async def unpublish_admin_legal_word(
    word_id: str,
    payload: LegalWordPublishRequest | None = None,
    service: LegalWordService = Depends(get_legal_word_service),
    current_user: CurrentUser = Depends(get_current_user),
    _admin_allowed: bool = Depends(require_permission(LEGAL_WORD_ADMIN_PERMISSION)),
):
    """Remove a word from a publication date while preserving the word record."""
    _ = _admin_allowed
    try:
        return await service.unpublish_word(
            word_id,
            current_user,
            for_date=payload.published_date if payload else None,
        )
    except LegalWordServiceError as exc:
        raise _raise_service_error(exc) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to unpublish legal word %s", word_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Legal word unpublishing service temporarily unavailable",
        ) from exc


@admin_router.delete("/{word_id}", response_model=LegalWord)
@handle_exceptions
async def delete_admin_legal_word(
    word_id: str,
    service: LegalWordService = Depends(get_legal_word_service),
    current_user: CurrentUser = Depends(get_current_user),
    _admin_allowed: bool = Depends(require_permission(LEGAL_WORD_ADMIN_PERMISSION)),
):
    """Delete a word record and remove it from publication sets."""
    _ = _admin_allowed
    try:
        return await service.delete_word(word_id, current_user)
    except LegalWordServiceError as exc:
        raise _raise_service_error(exc) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to delete legal word %s", word_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Legal word delete service temporarily unavailable",
        ) from exc


__all__ = ["admin_router", "router", "get_legal_word_service"]
