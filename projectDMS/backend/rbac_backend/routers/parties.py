"""
Secure party management API with comprehensive validation, proper authorization,
and clean architecture. Addresses complex authorization logic and performance issues.
"""

from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, status, Query
import logging
from datetime import datetime

from ..core.security import get_current_user, CurrentUser
from ..services.party_service import PartyService
from ..services.project_service import ProjectService
from ..services.authorization_service import AuthorizationService
from ..models.party import (
    Party, PartyCreate, PartyUpdate, PartyType, PartyListResponse
)
from ..utils.validation import validate_input, sanitize_text, validate_object_id
from ..utils.error_handler import handle_exceptions, PartyError
from ..utils.rate_limiter import RateLimiter
from ..utils.audit_logger import AuditLogger

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/parties",
    tags=["parties"],
    responses={404: {"description": "Not found"}}
)


class PartyController:
    """Secure party controller with comprehensive validation and authorization."""
    
    def __init__(
        self,
        party_service: PartyService,
        project_service: ProjectService,
        auth_service: AuthorizationService,
        rate_limiter: RateLimiter,
        audit_logger: AuditLogger
    ):
        self.party_service = party_service
        self.project_service = project_service
        self.auth_service = auth_service
        self.rate_limiter = rate_limiter
        self.audit_logger = audit_logger

    async def create_party(
        self, party_data: PartyCreate, current_user: CurrentUser
    ) -> Party:
        """Create party with comprehensive validation."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id, cost=3)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "parties:create")
            
            # Validate and sanitize input
            validated_data = await self._validate_party_input(party_data)
            
            # Business rule validation
            if (validated_data.type == PartyType.INDIVIDUAL and 
                validated_data.organization_id):
                raise PartyError(
                    "Individual parties cannot be directly associated with organizations",
                    status.HTTP_400_BAD_REQUEST
                )
            
            # Validate organization/project context if specified
            await self._validate_party_context(validated_data, current_user)
            
            # Create party
            party = await self.party_service.create_party(validated_data, current_user)
            
            # Audit log
            await self.audit_logger.log_party_created(
                current_user.id, party.id, party.name, party.type.value
            )
            
            return party
            
        except PartyError:
            raise
        except Exception as e:
            logger.error(f"Party creation failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Party creation service temporarily unavailable"
            )

    async def get_parties(
        self,
        pagination: dict,
        filters: dict,
        current_user: CurrentUser
    ) -> PartyListResponse:
        """Get parties with proper authorization and filtering."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "parties:read")
            
            # Build authorized query based on user scope
            authorized_query = await self.auth_service.build_party_query(
                current_user, filters
            )
            
            # Get parties with pagination
            parties, total_count = await self.party_service.get_parties_paginated(
                authorized_query, pagination
            )
            
            return PartyListResponse(
                parties=parties,
                total=total_count,
                page=pagination["skip"] // pagination["limit"] + 1,
                limit=pagination["limit"]
            )
            
        except Exception as e:
            logger.error(f"Failed to get parties: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Party service temporarily unavailable"
            )

    async def get_party(
        self, party_id: str, current_user: CurrentUser
    ) -> Party:
        """Get single party with authorization."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "parties:read")
            
            # Validate party ID
            validated_party_id = validate_object_id(party_id)
            
            # Get party
            party = await self.party_service.get_party_by_id(validated_party_id)
            if not party:
                raise PartyError(
                    f"Party with id {party_id} not found",
                    status.HTTP_404_NOT_FOUND
                )
            
            # Check authorization for this specific party
            await self.auth_service.check_party_access(current_user, party, "read")
            
            return party
            
        except PartyError:
            raise
        except Exception as e:
            logger.error(f"Failed to get party {party_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Party service temporarily unavailable"
            )

    async def update_party(
        self, party_id: str, update_data: PartyUpdate, current_user: CurrentUser
    ) -> Party:
        """Update party with validation and authorization."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id, cost=2)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "parties:update")
            
            # Validate party ID
            validated_party_id = validate_object_id(party_id)
            
            # Get existing party
            existing_party = await self.party_service.get_party_by_id(validated_party_id)
            if not existing_party:
                raise PartyError(
                    f"Party with id {party_id} not found",
                    status.HTTP_404_NOT_FOUND
                )
            
            # Check authorization for this specific party
            await self.auth_service.check_party_access(
                current_user, existing_party, "update"
            )
            
            # Validate update data
            validated_update = await self._validate_party_update(update_data)
            
            # Update party
            updated_party = await self.party_service.update_party(
                validated_party_id, validated_update, current_user
            )
            
            # Audit log
            changed_fields = list(validated_update.dict(exclude_unset=True).keys())
            await self.audit_logger.log_party_updated(
                current_user.id, validated_party_id, changed_fields
            )
            
            return updated_party
            
        except PartyError:
            raise
        except Exception as e:
            logger.error(f"Failed to update party {party_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Party update failed"
            )

    async def delete_party(
        self, party_id: str, current_user: CurrentUser
    ) -> dict:
        """Delete party with cascade validation."""
        try:
            # Rate limiting for destructive operations
            await self.rate_limiter.check_user_limit(current_user.id, cost=10)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "parties:delete")
            
            # Validate party ID
            validated_party_id = validate_object_id(party_id)
            
            # Get party for validation
            party = await self.party_service.get_party_by_id(validated_party_id)
            if not party:
                raise PartyError(
                    f"Party with id {party_id} not found",
                    status.HTTP_404_NOT_FOUND
                )
            
            # Check authorization for this specific party
            await self.auth_service.check_party_access(current_user, party, "delete")
            
            # Check for dependencies
            dependencies = await self.party_service.check_party_dependencies(validated_party_id)
            if dependencies:
                raise PartyError(
                    f"Cannot delete party with active dependencies: {', '.join(dependencies)}",
                    status.HTTP_409_CONFLICT
                )
            
            # Delete party with cascading
            await self.party_service.delete_party_with_cascade(
                validated_party_id, current_user
            )
            
            # Audit log
            await self.audit_logger.log_party_deleted(
                current_user.id, validated_party_id, party.name
            )
            
            return {"message": "Party deleted successfully"}
            
        except PartyError:
            raise
        except Exception as e:
            logger.error(f"Failed to delete party {party_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Party deletion failed"
            )

    async def associate_party_with_project(
        self, party_id: str, project_id: str, current_user: CurrentUser
    ) -> dict:
        """Associate party with project."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id, cost=2)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "parties:update")
            
            # Validate IDs
            validated_party_id = validate_object_id(party_id)
            validated_project_id = validate_object_id(project_id)
            
            # Verify party and project exist
            party = await self.party_service.get_party_by_id(validated_party_id)
            if not party:
                raise PartyError("Party not found", status.HTTP_404_NOT_FOUND)
            
            project = await self.project_service.get_project_by_id(validated_project_id)
            if not project:
                raise PartyError("Project not found", status.HTTP_404_NOT_FOUND)
            
            # Check authorization for both party and project
            await self.auth_service.check_party_access(current_user, party, "update")
            await self.auth_service.check_project_access(current_user, project, "associate_party")
            
            # Associate party with project
            await self.party_service.associate_with_project(
                validated_party_id, validated_project_id, current_user
            )
            
            # Audit log
            await self.audit_logger.log_party_project_associated(
                current_user.id, validated_party_id, validated_project_id
            )
            
            return {
                "message": f"Party {party_id} successfully associated with project {project_id}"
            }
            
        except PartyError:
            raise
        except Exception as e:
            logger.error(f"Failed to associate party with project: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Party-project association failed"
            )

    async def get_external_parties(
        self,
        pagination: dict,
        filters: dict,
        current_user: CurrentUser
    ) -> PartyListResponse:
        """Get external stakeholders not tied to specific organizations."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "parties:read")
            
            # Build query for external parties
            external_query = await self.auth_service.build_external_party_query(
                current_user, filters
            )
            
            # Get external parties
            parties, total_count = await self.party_service.get_external_parties_paginated(
                external_query, pagination
            )
            
            return PartyListResponse(
                parties=parties,
                total=total_count,
                page=pagination["skip"] // pagination["limit"] + 1,
                limit=pagination["limit"]
            )
            
        except Exception as e:
            logger.error(f"Failed to get external parties: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="External party service temporarily unavailable"
            )

    async def _validate_party_input(self, party_data: PartyCreate) -> PartyCreate:
        """Validate and sanitize party input."""
        return PartyCreate(
            name=sanitize_text(
                validate_input(party_data.name, max_length=200, required=True)
            ),
            type=party_data.type,
            contact_email=sanitize_text(
                validate_input(party_data.contact_email, max_length=255)
            ) if party_data.contact_email else None,
            contact_phone=sanitize_text(
                validate_input(party_data.contact_phone, max_length=50)
            ) if party_data.contact_phone else None,
            address=sanitize_text(
                validate_input(party_data.address, max_length=500)
            ) if party_data.address else None,
            organization_id=validate_object_id(party_data.organization_id)
                if party_data.organization_id else None,
            projects=[validate_object_id(pid) for pid in (party_data.projects or [])]
        )

    async def _validate_party_update(self, update_data: PartyUpdate) -> PartyUpdate:
        """Validate party update data."""
        validated_fields = {}
        
        if update_data.name is not None:
            validated_fields['name'] = sanitize_text(
                validate_input(update_data.name, max_length=200, required=True)
            )
        
        if update_data.contact_email is not None:
            validated_fields['contact_email'] = sanitize_text(
                validate_input(update_data.contact_email, max_length=255)
            )
        
        if update_data.contact_phone is not None:
            validated_fields['contact_phone'] = sanitize_text(
                validate_input(update_data.contact_phone, max_length=50)
            )
        
        if update_data.address is not None:
            validated_fields['address'] = sanitize_text(
                validate_input(update_data.address, max_length=500)
            )
        
        if update_data.projects is not None:
            validated_fields['projects'] = [
                validate_object_id(pid) for pid in update_data.projects
            ]
        
        return PartyUpdate(**validated_fields)

    async def _validate_party_context(
        self, party_data: PartyCreate, current_user: CurrentUser
    ):
        """Validate party organizational/project context."""
        if party_data.organization_id:
            await self.auth_service.check_organization_access(
                current_user, party_data.organization_id, "create_party"
            )
        
        if party_data.projects:
            for project_id in party_data.projects:
                project = await self.project_service.get_project_by_id(project_id)
                if project:
                    await self.auth_service.check_project_access(
                        current_user, project, "create_party"
                    )


# Dependency injection
async def get_party_controller() -> PartyController:
    """Factory function for party controller."""
    party_service = PartyService()
    project_service = ProjectService()
    auth_service = AuthorizationService()
    rate_limiter = RateLimiter()
    audit_logger = AuditLogger()
    
    return PartyController(
        party_service, project_service, auth_service,
        rate_limiter, audit_logger
    )


# API Endpoints
@router.post("/", response_model=Party, status_code=status.HTTP_201_CREATED)
@handle_exceptions
async def create_party(
    party_data: PartyCreate,
    controller: PartyController = Depends(get_party_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Create new party with validation."""
    return await controller.create_party(party_data, current_user)


@router.get("/", response_model=PartyListResponse)
@handle_exceptions
async def get_parties(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    type: Optional[PartyType] = Query(None),
    project_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    controller: PartyController = Depends(get_party_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get parties with filtering and pagination."""
    filters = {
        "type": type,
        "project_id": project_id,
        "search": search
    }
    pagination = {"skip": skip, "limit": limit}
    
    return await controller.get_parties(pagination, filters, current_user)


@router.get("/external", response_model=PartyListResponse)
@handle_exceptions
async def get_external_parties(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(None),
    party_type: Optional[PartyType] = Query(None),
    controller: PartyController = Depends(get_party_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get external stakeholders."""
    filters = {"search": search, "party_type": party_type}
    pagination = {"skip": skip, "limit": limit}
    return await controller.get_external_parties(pagination, filters, current_user)


@router.get("/{party_id}", response_model=Party)
@handle_exceptions
async def get_party(
    party_id: str,
    controller: PartyController = Depends(get_party_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get specific party by ID."""
    return await controller.get_party(party_id, current_user)


@router.put("/{party_id}", response_model=Party)
@handle_exceptions
async def update_party(
    party_id: str,
    update_data: PartyUpdate,
    controller: PartyController = Depends(get_party_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Update party with validation."""
    return await controller.update_party(party_id, update_data, current_user)


@router.delete("/{party_id}", status_code=status.HTTP_204_NO_CONTENT)
@handle_exceptions
async def delete_party(
    party_id: str,
    controller: PartyController = Depends(get_party_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Delete party with cascade validation."""
    return await controller.delete_party(party_id, current_user)


@router.post("/{party_id}/projects/{project_id}")
@handle_exceptions
async def associate_party_with_project(
    party_id: str,
    project_id: str,
    controller: PartyController = Depends(get_party_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Associate party with project."""
    return await controller.associate_party_with_project(party_id, project_id, current_user)
