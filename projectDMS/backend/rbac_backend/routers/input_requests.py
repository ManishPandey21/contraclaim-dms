"""
Secure input request management API with comprehensive validation, proper authorization,
and clean architecture. Addresses ObjectId handling and performance issues.
"""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
import logging
import html
from datetime import datetime
from datetime import timezone
from bson import ObjectId

from ..core.security import get_current_user, CurrentUser
from ..core.database import get_database, get_db
from ..services.input_request_service import InputRequestService
from ..services.letter_service import LetterService
from ..dependencies import get_notification_service
from ..services.authorization_service import AuthorizationService
from ..services.policy_service import PolicyService
from ..models.input_request import (
    InputRequest, InputRequestCreate, InputRequestUpdate, 
    InputRequestResponse, InputRequestListResponse, SuggestedKeyPoints
)
from ..utils.validation import validate_input, sanitize_text, validate_object_id
from ..utils.error_handler import BaseDomainError, handle_exceptions, InputRequestError
from ..utils.rate_limiter import RateLimiter
from ..utils.audit_logger import AuditLogger
from ..utils.notification_service import NotificationService

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
        audit_logger: AuditLogger,
        notification_service: Optional[NotificationService] = None,
    ):
        self.input_request_service = input_request_service
        self.letter_service = letter_service
        self.auth_service = auth_service
        self.rate_limiter = rate_limiter
        self.audit_logger = audit_logger
        self.notification_service = notification_service

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
            
        except (BaseDomainError, HTTPException):
            raise
        except ValueError:
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

            await self._move_letter_to_input_status(validated_letter_id, letter, current_user)
            
            # Audit log
            await self.audit_logger.log_input_request_created(
                current_user.id, input_request.id, validated_letter_id
            )

            try:
                await self._send_input_request_email(
                    letter=letter,
                    input_request=input_request,
                    current_user=current_user,
                )
            except Exception:
                logger.warning(
                    "Input request created but email notification failed request_id=%s letter_id=%s",
                    input_request.id,
                    validated_letter_id,
                    exc_info=True,
                )
            
            return input_request
            
        except (BaseDomainError, HTTPException):
            raise
        except ValueError:
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
            
        except (BaseDomainError, HTTPException):
            raise
        except ValueError:
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
            
        except (BaseDomainError, HTTPException):
            raise
        except ValueError:
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
            generated = await self.input_request_service.generate_key_points(
                validated_letter_id
            )
            
            return SuggestedKeyPoints(
                letter_id=validated_letter_id,
                key_points=generated.key_points,
                generated_at=datetime.utcnow()
            )
            
        except (BaseDomainError, HTTPException):
            raise
        except ValueError:
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

    async def _move_letter_to_input_status(
        self,
        letter_id: str,
        letter: Any,
        current_user: CurrentUser,
    ) -> None:
        if (getattr(letter, "status", "") or "").lower() == "input":
            return

        now = datetime.now(timezone.utc)
        letter_query: Dict[str, Any] = {"_id": self._coerce_user_id(letter_id)}
        if letter_query["_id"] != letter_id:
            letter_query = {"$or": [letter_query, {"_id": letter_id}]}

        try:
            await self.letter_service.db.letters.update_one(
                letter_query,
                {
                    "$set": {
                        "status": "Input",
                        "statusStartDate": now,
                        "status_start_date": now,
                        "updatedAt": now,
                        "updated_at": now,
                    },
                    "$push": {
                        "status_history": {
                            "status": "Input",
                            "changed_at": now,
                            "actor_id": getattr(current_user, "id", None),
                            "comment": "Input requested",
                        }
                    },
                },
            )
        except Exception:
            logger.warning(
                "Input request created but failed to move letter to Input status letter_id=%s",
                letter_id,
                exc_info=True,
            )

    async def _send_input_request_email(
        self,
        *,
        letter: Any,
        input_request: InputRequest,
        current_user: CurrentUser,
    ) -> None:
        """Email the selected input provider with drafter contact details."""

        email_service = getattr(self.notification_service, "email_service", None)
        db = getattr(self.notification_service, "db", None)
        if not email_service or db is None:
            return

        requested_user = await self._find_user_by_reference(db, input_request.requested_from)
        if not requested_user or not requested_user.get("email"):
            logger.info(
                "Input request email skipped; selected user has no email user_id=%s",
                input_request.requested_from,
            )
            return

        drafter_user = None
        if getattr(letter, "assigned_to", None):
            drafter_user = await self._find_user_by_reference(db, str(letter.assigned_to))

        drafter_name = self._user_display_name(drafter_user) if drafter_user else "Not assigned"
        drafter_email = str(drafter_user.get("email")) if drafter_user and drafter_user.get("email") else "Not available"
        requester_name = getattr(current_user, "username", None) or getattr(current_user, "email", None) or "Contract manager"
        due_text = input_request.due_date.isoformat() if input_request.due_date else "Not specified"
        input_url = f"{email_service.app_url.rstrip('/')}/letters/{letter.id}/input"
        subject = f"Input requested for letter: {letter.title}"
        text_body = "\n".join(
            [
                f"Dear {self._user_display_name(requested_user)},",
                "",
                f"{requester_name} has requested your input for the following contract letter.",
                "",
                f"Letter: {letter.title}",
                f"Subject: {letter.subject}",
                f"Recipient: {letter.recipient}",
                f"Due date: {due_text}",
                "",
                "Requested input details:",
                input_request.details,
                "",
                "Contract drafter contact for response/clarification:",
                f"{drafter_name} <{drafter_email}>",
                "",
                f"Open request: {input_url}",
                "",
                "Best regards,",
                "ContraClaim DMS",
            ]
        )
        html_body = f"""
        <p>Dear {html.escape(self._user_display_name(requested_user))},</p>
        <p>{html.escape(requester_name)} has requested your input for the following contract letter.</p>
        <dl>
          <dt>Letter</dt><dd>{html.escape(str(letter.title))}</dd>
          <dt>Subject</dt><dd>{html.escape(str(letter.subject))}</dd>
          <dt>Recipient</dt><dd>{html.escape(str(letter.recipient))}</dd>
          <dt>Due date</dt><dd>{html.escape(due_text)}</dd>
        </dl>
        <p><strong>Requested input details</strong></p>
        <p>{html.escape(input_request.details).replace(chr(10), '<br>')}</p>
        <p><strong>Contract drafter contact for response/clarification</strong><br>
        {html.escape(drafter_name)} &lt;{html.escape(drafter_email)}&gt;</p>
        <p><a href="{html.escape(input_url)}">Open input request</a></p>
        <p>Best regards,<br>ContraClaim DMS</p>
        """
        sent = await email_service.send_share_email(
            to=[str(requested_user.get("email"))],
            subject=subject,
            html_body=html_body,
            text_body=text_body,
            organization_id=getattr(letter, "organization_id", None),
            project_id=getattr(letter, "project_id", None),
        )
        if not sent:
            logger.info(
                "Input request email was not sent by configured mail service request_id=%s",
                input_request.id,
            )

    @staticmethod
    def _coerce_user_id(value: str) -> ObjectId | str:
        try:
            return ObjectId(value)
        except Exception:
            return value

    async def _find_user_by_reference(self, db: Any, value: str) -> Optional[Dict[str, Any]]:
        reference = str(value)
        query_parts: List[Dict[str, Any]] = [
            {"_id": self._coerce_user_id(reference)},
            {"id": reference},
            {"email": reference},
            {"username": reference},
        ]
        if query_parts[0]["_id"] != reference:
            query_parts.append({"_id": reference})
        return await db.users.find_one({"$or": query_parts})

    @staticmethod
    def _user_display_name(user: Optional[Dict[str, Any]]) -> str:
        if not user:
            return "User"
        return str(
            user.get("full_name")
            or user.get("name")
            or user.get("username")
            or user.get("email")
            or "User"
        )


# Dependency injection
async def get_input_request_controller() -> InputRequestController:
    """Factory function for input request controller."""
    db = await get_database()
    notification_service = await get_notification_service()
    input_request_service = InputRequestService()
    letter_service = LetterService(db, notification_service=notification_service)
    auth_service = AuthorizationService()
    rate_limiter = RateLimiter(scope="input_requests")
    audit_logger = AuditLogger()
    
    return InputRequestController(
        input_request_service, letter_service, auth_service,
        rate_limiter, audit_logger, notification_service
    )


async def get_policy_service(db=Depends(get_db)) -> PolicyService:
    return PolicyService(db)


def _letter_scope(letter: Any) -> tuple[Optional[str], Optional[str]]:
    organization_id = getattr(letter, "organization_id", None) or getattr(letter, "org_id", None)
    project_id = getattr(letter, "project_id", None) or getattr(letter, "proj_id", None)
    if isinstance(letter, dict):
        organization_id = organization_id or letter.get("organization_id") or letter.get("org_id")
        project_id = project_id or letter.get("project_id") or letter.get("proj_id")
    return (
        str(organization_id) if organization_id else None,
        str(project_id) if project_id else None,
    )


async def _authorize_input_letter(
    policy: PolicyService,
    controller: InputRequestController,
    current_user: CurrentUser,
    letter_id: str,
    permission: str,
) -> Any:
    letter = await controller.letter_service.get_letter_by_id(validate_object_id(letter_id))
    if not letter:
        raise InputRequestError("Letter not found", status.HTTP_404_NOT_FOUND)
    organization_id, project_id = _letter_scope(letter)
    if not organization_id and not policy.scope_service.is_superadmin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Letter has no authoritative organization scope",
        )
    await policy.authorize(
        current_user,
        permission,
        organization_id=organization_id,
        project_id=project_id,
        letter_id=letter_id,
        resource_type="input_request",
        resource_id=letter_id,
    )
    return letter


async def _authorize_input_request(
    policy: PolicyService,
    controller: InputRequestController,
    current_user: CurrentUser,
    request_id: str,
    permission: str,
) -> Any:
    input_request = await controller.input_request_service.get_request_by_id(
        validate_object_id(request_id)
    )
    if not input_request:
        raise InputRequestError("Input request not found", status.HTTP_404_NOT_FOUND)
    await _authorize_input_letter(
        policy,
        controller,
        current_user,
        str(input_request.letter_id),
        permission,
    )
    return input_request


# API Endpoints
@router.get("/input-requests/letter/{letter_id}", response_model=InputRequestListResponse)
@handle_exceptions
async def get_input_requests_for_letter(
    letter_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    controller: InputRequestController = Depends(get_input_request_controller),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    """Get input requests for specific letter with pagination."""
    await _authorize_input_letter(
        policy, controller, current_user, letter_id, "input_requests:read"
    )
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
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    """Create input request for specific letter."""
    await _authorize_input_letter(
        policy, controller, current_user, letter_id, "input_requests:create"
    )
    return await controller.create_input_request(letter_id, request_data, current_user)


@router.post("/input-requests/{request_id}/respond", response_model=InputRequest)
@handle_exceptions
async def respond_to_input_request(
    request_id: str,
    response_data: InputRequestResponse,
    controller: InputRequestController = Depends(get_input_request_controller),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    """Respond to input request."""
    await _authorize_input_request(
        policy, controller, current_user, request_id, "input_requests:respond"
    )
    return await controller.respond_to_request(request_id, response_data, current_user)


@router.post("/input-requests/{request_id}/close", response_model=InputRequest)
@handle_exceptions
async def close_input_request(
    request_id: str,
    controller: InputRequestController = Depends(get_input_request_controller),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    """Close input request."""
    await _authorize_input_request(
        policy, controller, current_user, request_id, "input_requests:update"
    )
    return await controller.close_request(request_id, current_user)


@router.get("/input-requests/letter/{letter_id}/suggested-key-points", response_model=SuggestedKeyPoints)
@handle_exceptions
async def get_suggested_key_points_for_letter(
    letter_id: str,
    controller: InputRequestController = Depends(get_input_request_controller),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    """Get AI-suggested key points for letter."""
    await _authorize_input_letter(
        policy, controller, current_user, letter_id, "drafting.draft.create"
    )
    return await controller.get_suggested_key_points(letter_id, current_user)
