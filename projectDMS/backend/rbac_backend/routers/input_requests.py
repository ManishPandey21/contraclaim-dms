"""
Secure input request management API with comprehensive validation, proper authorization,
and clean architecture. Addresses ObjectId handling and performance issues.
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
import logging
from datetime import datetime

from ..core.security import get_current_user, CurrentUser
from ..core.database import get_database
from ..services.input_request_service import InputRequestService
from ..services.letter_service import LetterService
from ..dependencies import get_notification_service
from ..services.authorization_service import AuthorizationService
from ..models.input_request import (
    InputRequest, InputRequestCreate, InputRequestUpdate, 
    InputRequestResponse, InputRequestListResponse, SuggestedKeyPoints
)
from ..utils.validation import validate_input, sanitize_text, validate_object_id
from ..utils.error_handler import handle_exceptions, InputRequestError
from ..utils.rate_limiter import RateLimiter
from ..utils.audit_logger import AuditLogger

logger = logging.getLogger(__name__)
router = APIRouter(tags=["input_requests"])


class InputRequestController:
    """Secure input request controller with comprehensive validation and authorization."""
    
    def __init__(
        self,
        input_request_service: InputRequestService,
        letter_service: LetterService,
        auth_service: AuthorizationService,
        rate_limiter: RateLimiter,
        audit_logger: AuditLogger
    ):
        self.input_request_service = input_request_service
        self.letter_service = letter_service
        self.auth_service = auth_service
        self.rate_limiter = rate_limiter
        self.audit_logger = audit_logger

    async def get_input_requests_for_letter(
        self,
        letter_id: str,
        pagination: dict,
        current_user: CurrentUser
    ) -> InputRequestListResponse:
        """Get input requests for a specific letter with authorization."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "input_requests:read")
            
            # Validate and normalize letter ID
            validated_letter_id = validate_object_id(letter_id)
            
            # Get letter and verify access
            letter = await self.letter_service.get_letter_by_id(validated_letter_id)
            if not letter:
                raise InputRequestError(
                    "Letter not found",
                    status.HTTP_404_NOT_FOUND
                )
            
            # Check authorization for this specific letter
            await self.auth_service.check_letter_access(current_user, letter, "read")
            
            # Get input requests with pagination
            requests, total_count = await self.input_request_service.get_requests_for_letter_paginated(
                validated_letter_id, pagination
            )
            
            return InputRequestListResponse(
                requests=requests,
                total=total_count,
                page=pagination["skip"] // pagination["limit"] + 1,
                limit=pagination["limit"]
            )
            
        except InputRequestError:
            raise
        except Exception as e:
            logger.error(f"Failed to get input requests for letter {letter_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Input request service temporarily unavailable"
            )

    async def create_input_request(
        self,
        letter_id: str,
        request_data: InputRequestCreate,
        current_user: CurrentUser
    ) -> InputRequest:
        """Create input request with comprehensive validation."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id, cost=3)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "input_requests:create")
            
            # Validate inputs
            validated_letter_id = validate_object_id(letter_id)
            validated_data = await self._validate_request_input(request_data)
            
            # Get letter and verify access
            letter = await self.letter_service.get_letter_by_id(validated_letter_id)
            if not letter:
                raise InputRequestError(
                    "Letter not found",
                    status.HTTP_404_NOT_FOUND
                )
            
            # Check authorization for creating requests on this letter
            await self.auth_service.check_letter_access(
                current_user, letter, "create_input_request"
            )
            
            # Create input request
            input_request = await self.input_request_service.create_request(
                validated_letter_id, validated_data, current_user
            )
            
            # Audit log
            await self.audit_logger.log_input_request_created(
                current_user.id, input_request.id, validated_letter_id
            )
            
            return input_request
            
        except InputRequestError:
            raise
        except Exception as e:
            logger.error(f"Failed to create input request: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Input request creation failed"
            )

    async def respond_to_request(
        self,
        request_id: str,
        response_data: InputRequestResponse,
        current_user: CurrentUser
    ) -> InputRequest:
        """Respond to input request with validation."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id, cost=2)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "input_requests:respond")
            
            # Validate inputs
            validated_request_id = validate_object_id(request_id)
            validated_response = await self._validate_response_input(response_data)
            
            # Get input request
            input_request = await self.input_request_service.get_request_by_id(
                validated_request_id
            )
            if not input_request:
                raise InputRequestError(
                    "Input request not found",
                    status.HTTP_404_NOT_FOUND
                )
            
            # Get associated letter for authorization
            letter = await self.letter_service.get_letter_by_id(input_request.letter_id)
            if not letter:
                raise InputRequestError(
                    "Associated letter not found",
                    status.HTTP_404_NOT_FOUND
                )
            
            # Check authorization for responding to requests on this letter
            await self.auth_service.check_letter_access(
                current_user, letter, "respond_to_input_request"
            )
            
            # Update request with response
            updated_request = await self.input_request_service.add_response(
                validated_request_id, validated_response, current_user
            )
            
            # Audit log
            await self.audit_logger.log_input_request_responded(
                current_user.id, validated_request_id
            )
            
            return updated_request
            
        except InputRequestError:
            raise
        except Exception as e:
            logger.error(f"Failed to respond to request {request_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Response submission failed"
            )

    async def close_request(
        self,
        request_id: str,
        current_user: CurrentUser
    ) -> InputRequest:
        """Close input request with authorization."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "input_requests:update")
            
            # Validate request ID
            validated_request_id = validate_object_id(request_id)
            
            # Get input request
            input_request = await self.input_request_service.get_request_by_id(
                validated_request_id
            )
            if not input_request:
                raise InputRequestError(
                    "Input request not found",
                    status.HTTP_404_NOT_FOUND
                )
            
            # Authorization check - only request creator or admin can close
            if (input_request.requested_by != current_user.id and
                not await self.auth_service.has_permission(current_user, "input_requests:admin")):
                raise InputRequestError(
                    "Not authorized to close this request",
                    status.HTTP_403_FORBIDDEN
                )
            
            # Close request
            closed_request = await self.input_request_service.close_request(
                validated_request_id, current_user
            )
            
            # Audit log
            await self.audit_logger.log_input_request_closed(
                current_user.id, validated_request_id
            )
            
            return closed_request
            
        except InputRequestError:
            raise
        except Exception as e:
            logger.error(f"Failed to close request {request_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Request closure failed"
            )

    async def get_suggested_key_points(
        self,
        letter_id: str,
        current_user: CurrentUser
    ) -> SuggestedKeyPoints:
        """Get AI-suggested key points for a letter."""
        try:
            # Rate limiting for AI operations
            await self.rate_limiter.check_user_limit(current_user.id, cost=5)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "input_requests:read")
            
            # Validate letter ID
            validated_letter_id = validate_object_id(letter_id)
            
            # Get letter and verify access
            letter = await self.letter_service.get_letter_by_id(validated_letter_id)
            if not letter:
                raise InputRequestError(
                    "Letter not found",
                    status.HTTP_404_NOT_FOUND
                )
            
            # Check authorization for this letter
            await self.auth_service.check_letter_access(current_user, letter, "read")
            
            # Get suggested key points (with caching)
            key_points = await self.input_request_service.generate_key_points(
                validated_letter_id, letter
            )
            
            return SuggestedKeyPoints(
                letter_id=validated_letter_id,
                key_points=key_points,
                generated_at=datetime.utcnow()
            )
            
        except InputRequestError:
            raise
        except Exception as e:
            logger.error(f"Failed to get key points for {letter_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Key points generation failed"
            )

    async def _validate_request_input(
        self, request_data: InputRequestCreate
    ) -> InputRequestCreate:
        """Validate and sanitize input request data."""
        return InputRequestCreate(
            requested_from=sanitize_text(
                validate_input(request_data.requested_from, max_length=200, required=True)
            ),
            details=sanitize_text(
                validate_input(request_data.details, max_length=2000, required=True)
            ),
            key_points=sanitize_text(
                validate_input(request_data.key_points, max_length=1000)
            ) if request_data.key_points else None,
            due_date=request_data.due_date,  # Already validated by Pydantic
            reference_letter_id=validate_object_id(request_data.reference_letter_id)
                if request_data.reference_letter_id else None
        )

    async def _validate_response_input(
        self, response_data: InputRequestResponse
    ) -> InputRequestResponse:
        """Validate and sanitize response data."""
        return InputRequestResponse(
            message=sanitize_text(
                validate_input(response_data.message, max_length=2000, required=True)
            )
        )


# Dependency injection
async def get_input_request_controller() -> InputRequestController:
    """Factory function for input request controller."""
    db = await get_database()
    notification_service = await get_notification_service()
    input_request_service = InputRequestService()
    letter_service = LetterService(db, notification_service=notification_service)
    auth_service = AuthorizationService()
    rate_limiter = RateLimiter()
    audit_logger = AuditLogger()
    
    return InputRequestController(
        input_request_service, letter_service, auth_service,
        rate_limiter, audit_logger
    )


# API Endpoints
@router.get("/input-requests/letter/{letter_id}", response_model=InputRequestListResponse)
@handle_exceptions
async def get_input_requests_for_letter(
    letter_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    controller: InputRequestController = Depends(get_input_request_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get input requests for specific letter with pagination."""
    pagination = {"skip": skip, "limit": limit}
    return await controller.get_input_requests_for_letter(
        letter_id, pagination, current_user
    )


@router.post("/input-requests/letter/{letter_id}", response_model=InputRequest, status_code=201)
@handle_exceptions
async def create_input_request_for_letter(
    letter_id: str,
    request_data: InputRequestCreate,
    controller: InputRequestController = Depends(get_input_request_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Create input request for specific letter."""
    return await controller.create_input_request(letter_id, request_data, current_user)


@router.post("/input-requests/{request_id}/respond", response_model=InputRequest)
@handle_exceptions
async def respond_to_input_request(
    request_id: str,
    response_data: InputRequestResponse,
    controller: InputRequestController = Depends(get_input_request_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Respond to input request."""
    return await controller.respond_to_request(request_id, response_data, current_user)


@router.post("/input-requests/{request_id}/close", response_model=InputRequest)
@handle_exceptions
async def close_input_request(
    request_id: str,
    controller: InputRequestController = Depends(get_input_request_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Close input request."""
    return await controller.close_request(request_id, current_user)


@router.get("/input-requests/letter/{letter_id}/suggested-key-points", response_model=SuggestedKeyPoints)
@handle_exceptions
async def get_suggested_key_points_for_letter(
    letter_id: str,
    controller: InputRequestController = Depends(get_input_request_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get AI-suggested key points for letter."""
    return await controller.get_suggested_key_points(letter_id, current_user)


# Legacy endpoints for backward compatibility (deprecated)
@router.get("/input_requests", response_model=List[dict], deprecated=True)
@handle_exceptions
async def list_items_legacy(
    controller: InputRequestController = Depends(get_input_request_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Legacy endpoint - use /input-requests/letter/{letter_id} instead."""
    # This is a deprecated endpoint maintained for compatibility
    # Returns empty list and logs usage for migration tracking
    logger.warning(f"User {current_user.id} used deprecated endpoint /input_requests")
    return []
