"""
Secure concern management API with comprehensive validation, proper authorization,
and clean architecture. Addresses all security vulnerabilities and performance issues.
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
import logging
from datetime import datetime

from ..core.security import get_current_user, CurrentUser
from ..services.concern_service import ConcernService
from ..services.party_service import PartyService
from ..services.authorization_service import AuthorizationService
from ..models.concern import (
    Concern, ConcernCreate, ConcernUpdate, ConcernListResponse
)
from ..utils.validation import validate_input, sanitize_text, validate_object_id
from ..utils.error_handler import handle_exceptions, ConcernError
from ..utils.rate_limiter import RateLimiter
from ..utils.audit_logger import AuditLogger

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/concerns",
    tags=["concerns"],
    responses={404: {"description": "Not found"}}
)


class ConcernController:
    """Secure concern controller with comprehensive validation and authorization."""

    def __init__(
        self,
        concern_service: ConcernService,
        party_service: PartyService,
        auth_service: AuthorizationService,
        rate_limiter: RateLimiter,
        audit_logger: AuditLogger
    ):
        self.concern_service = concern_service
        self.party_service = party_service
        self.auth_service = auth_service
        self.rate_limiter = rate_limiter
        self.audit_logger = audit_logger

    async def create_concern(
        self, concern_data: ConcernCreate, current_user: CurrentUser
    ) -> Concern:
        """Create concern with comprehensive validation and authorization."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id, cost=3)

            # Authorization check
            await self.auth_service.require_permission(current_user, "concerns:create")

            # Validate and sanitize input
            validated_data = await self._validate_concern_input(concern_data)

            # Validate party access if party is specified
            if validated_data.party_id:
                party = await self.party_service.get_party_by_id(validated_data.party_id)
                if not party:
                    raise ConcernError(
                        f"Party with id {validated_data.party_id} not found",
                        status.HTTP_404_NOT_FOUND
                    )

                # Check authorization for this specific party
                await self.auth_service.check_party_access(
                    current_user, party, "create_concern"
                )

            # Create concern
            concern = await self.concern_service.create_concern(
                validated_data, current_user
            )

            # Audit log
            await self.audit_logger.log_concern_created(
                current_user.id, concern.id, party_id=validated_data.party_id
            )

            return concern

        except (ConcernError, HTTPException):
            raise
        except Exception as e:
            logger.error(f"Concern creation failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Concern creation service temporarily unavailable"
            )

    async def get_concerns(
        self,
        pagination: dict,
        filters: dict,
        current_user: CurrentUser
    ) -> ConcernListResponse:
        """Get concerns with proper authorization and filtering."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)

            # Authorization check
            await self.auth_service.require_permission(current_user, "concerns:read")

            # Build authorized query based on user scope
            authorized_query = await self.auth_service.build_concern_query(
                current_user, filters
            )

            # Get concerns with pagination
            concerns, total_count = await self.concern_service.get_concerns_paginated(
                authorized_query, pagination
            )

            return ConcernListResponse(
                concerns=concerns,
                total=total_count,
                page=pagination["skip"] // pagination["limit"] + 1,
                limit=pagination["limit"]
            )

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Failed to get concerns: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Concern service temporarily unavailable"
            )

    async def get_concern(
        self, concern_id: str, current_user: CurrentUser
    ) -> Concern:
        """Get single concern with authorization."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)

            # Authorization check
            await self.auth_service.require_permission(current_user, "concerns:read")

            # Validate concern ID
            validated_concern_id = validate_object_id(concern_id)

            # Get concern
            concern = await self.concern_service.get_concern_by_id(validated_concern_id)
            if not concern:
                raise ConcernError(
                    f"Concern with id {concern_id} not found",
                    status.HTTP_404_NOT_FOUND
                )

            # Check authorization for this specific concern
            await self.auth_service.check_concern_access(current_user, concern, "read")

            return concern

        except (ConcernError, HTTPException):
            raise
        except Exception as e:
            logger.error(f"Failed to get concern {concern_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Concern service temporarily unavailable"
            )

    async def update_concern(
        self, concern_id: str, update_data: ConcernUpdate, current_user: CurrentUser
    ) -> Concern:
        """Update concern with validation and authorization."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id, cost=2)

            # Authorization check
            await self.auth_service.require_permission(current_user, "concerns:update")

            # Validate concern ID
            validated_concern_id = validate_object_id(concern_id)

            # Get existing concern
            existing_concern = await self.concern_service.get_concern_by_id(validated_concern_id)
            if not existing_concern:
                raise ConcernError(
                    f"Concern with id {concern_id} not found",
                    status.HTTP_404_NOT_FOUND
                )

            # Check authorization for this specific concern
            await self.auth_service.check_concern_access(
                current_user, existing_concern, "update"
            )

            # Validate update data
            validated_update = await self._validate_concern_update(update_data)

            # Update concern
            updated_concern = await self.concern_service.update_concern(
                validated_concern_id, validated_update, current_user
            )

            # Audit log
            changed_fields = list(validated_update.dict(exclude_unset=True).keys())
            await self.audit_logger.log_concern_updated(
                current_user.id, validated_concern_id, changed_fields
            )

            return updated_concern

        except (ConcernError, HTTPException):
            raise
        except Exception as e:
            logger.error(f"Failed to update concern {concern_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Concern update failed"
            )

    async def delete_concern(
        self, concern_id: str, current_user: CurrentUser
    ) -> dict:
        """Delete concern with authorization checks."""
        try:
            # Rate limiting for destructive operations
            await self.rate_limiter.check_user_limit(current_user.id, cost=5)

            # Authorization check
            await self.auth_service.require_permission(current_user, "concerns:delete")

            # Validate concern ID
            validated_concern_id = validate_object_id(concern_id)

            # Get concern for validation
            concern = await self.concern_service.get_concern_by_id(validated_concern_id)
            if not concern:
                raise ConcernError(
                    f"Concern with id {concern_id} not found",
                    status.HTTP_404_NOT_FOUND
                )

            # Check authorization for this specific concern
            await self.auth_service.check_concern_access(
                current_user, concern, "delete"
            )

            # Delete concern
            await self.concern_service.delete_concern(validated_concern_id, current_user)

            # Audit log
            await self.audit_logger.log_concern_deleted(
                current_user.id, validated_concern_id, name=concern.name or "unnamed"
            )

            return {"message": "Concern deleted successfully"}

        except (ConcernError, HTTPException):
            raise
        except Exception as e:
            logger.error(f"Failed to delete concern {concern_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Concern deletion failed"
            )

    async def _validate_concern_input(self, concern_data: ConcernCreate) -> ConcernCreate:
        """Validate and sanitize concern input."""
        return ConcernCreate(
            name=sanitize_text(
                validate_input(concern_data.name, max_length=200, required=True)
            ) if concern_data.name else None,
            email=sanitize_text(
                validate_input(concern_data.email, max_length=255)
            ) if concern_data.email else None,
            description=sanitize_text(
                validate_input(concern_data.description, max_length=2000)
            ) if concern_data.description else None,
            party_id=validate_object_id(concern_data.party_id)
                if concern_data.party_id else None,
            status=concern_data.status or "open",
            priority=concern_data.priority or "medium"
        )

    async def _validate_concern_update(self, update_data: ConcernUpdate) -> ConcernUpdate:
        """Validate concern update data."""
        validated_fields = {}

        if update_data.name is not None:
            validated_fields['name'] = sanitize_text(
                validate_input(update_data.name, max_length=200)
            )

        if update_data.email is not None:
            validated_fields['email'] = sanitize_text(
                validate_input(update_data.email, max_length=255)
            )

        if update_data.description is not None:
            validated_fields['description'] = sanitize_text(
                validate_input(update_data.description, max_length=2000)
            )

        if update_data.status is not None:
            validated_fields['status'] = update_data.status

        if update_data.priority is not None:
            validated_fields['priority'] = update_data.priority

        return ConcernUpdate(**validated_fields)


# Dependency injection
async def get_concern_controller() -> ConcernController:
    """Factory function for concern controller."""
    concern_service = ConcernService()
    party_service = PartyService()
    auth_service = AuthorizationService()
    rate_limiter = RateLimiter(scope="concerns")
    audit_logger = AuditLogger()

    return ConcernController(
        concern_service, party_service, auth_service,
        rate_limiter, audit_logger
    )


# API Endpoints
@router.post("/", response_model=Concern, status_code=status.HTTP_201_CREATED)
@handle_exceptions
async def create_concern(
    concern_data: ConcernCreate,
    controller: ConcernController = Depends(get_concern_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Create new concern with validation."""
    return await controller.create_concern(concern_data, current_user)


@router.get("/", response_model=ConcernListResponse)
@handle_exceptions
async def get_concerns(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    party_id: Optional[str] = Query(None, alias="partyId"),
    search: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    priority: Optional[str] = Query(None),
    controller: ConcernController = Depends(get_concern_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get concerns with filtering and pagination."""
    filters = {
        "party_id": party_id,
        "search": search,
        "status": status,
        "priority": priority
    }
    pagination = {"skip": skip, "limit": limit}

    return await controller.get_concerns(pagination, filters, current_user)


@router.get("/{concern_id}", response_model=Concern)
@handle_exceptions
async def get_concern(
    concern_id: str,
    controller: ConcernController = Depends(get_concern_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get specific concern by ID."""
    return await controller.get_concern(concern_id, current_user)


@router.put("/{concern_id}", response_model=Concern)
@handle_exceptions
async def update_concern(
    concern_id: str,
    update_data: ConcernUpdate,
    controller: ConcernController = Depends(get_concern_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Update concern with validation."""
    return await controller.update_concern(concern_id, update_data, current_user)


@router.delete("/{concern_id}", status_code=status.HTTP_204_NO_CONTENT)
@handle_exceptions
async def delete_concern(
    concern_id: str,
    controller: ConcernController = Depends(get_concern_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Delete concern with authorization checks."""
    return await controller.delete_concern(concern_id, current_user)
