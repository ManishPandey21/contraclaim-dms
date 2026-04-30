# Improved representatives.py
"""
Secure representative management with comprehensive validation, proper authorization,
and clean architecture. Addresses complex nested logic and security vulnerabilities.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import List, Optional, Dict, Any, Union
from pydantic import EmailStr
import logging
from datetime import datetime
from enum import Enum

from ..core.security import get_current_user, CurrentUser, authorize_scope
from ..core.database import get_db
from ..services.representative_service import RepresentativeService
from ..services.party_service import PartyService
from ..services.project_service import ProjectService
from ..services.organization_service import OrganizationService
from ..services.authorization_service import AuthorizationService
from ..models.representative import (
    Representative, RepresentativeCreate, RepresentativeUpdate,
    RepresentativeLevel, RepresentativeResponse, RepresentativeListResponse
)
from ..utils.validation import (
    validate_input, sanitize_text, validate_email, validate_phone, validate_object_id
)
from ..utils.error_handler import handle_exceptions, RepresentativeError
from ..utils.rate_limiter import RateLimiter
from ..utils.audit_logger import AuditLogger

logger = logging.getLogger(__name__)
router = APIRouter()


class RepresentativeController:
    """Secure representative controller with comprehensive validation and authorization."""
    
    def __init__(
        self,
        rep_service: RepresentativeService,
        party_service: PartyService,
        project_service: ProjectService,
        org_service: OrganizationService,
        auth_service: AuthorizationService,
        rate_limiter: RateLimiter,
        audit_logger: AuditLogger
    ):
        self.rep_service = rep_service
        self.party_service = party_service
        self.project_service = project_service
        self.org_service = org_service
        self.auth_service = auth_service
        self.rate_limiter = rate_limiter
        self.audit_logger = audit_logger

    async def create_party_representative(
        self,
        party_id: str,
        rep_data: RepresentativeCreate,
        current_user: CurrentUser
    ) -> Representative:
        """Create representative for a party with validation."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id, cost=3)
            
            # Validate inputs
            validated_party_id = validate_object_id(party_id)
            validated_rep_data = await self._validate_representative_input(rep_data)
            
            # Get party and validate access
            party = await self.party_service.get_party_by_id(validated_party_id)
            if not party:
                raise RepresentativeError("Party not found", status.HTTP_404_NOT_FOUND)
            
            # Authorization check
            await self.auth_service.check_party_access(current_user, party, "create_representative")
            
            # Handle primary representative logic atomically
            if validated_rep_data.is_primary:
                await self._handle_primary_representative_change(
                    party_id=validated_party_id,
                    level=RepresentativeLevel.PARTY
                )
            
            # Create representative
            representative = await self.rep_service.create_representative(
                party_id=validated_party_id,
                rep_data=validated_rep_data,
                level=RepresentativeLevel.PARTY,
                current_user=current_user
            )

            # Audit log
            await self._safe_audit_log(
                "log_representative_created",
                current_user.id,
                representative.id,
                "party",
                validated_party_id,
            )
            
            return representative
            
        except RepresentativeError:
            raise
        except Exception as e:
            logger.error(f"Failed to create party representative: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Representative creation service temporarily unavailable"
            )

    async def create_organization_representative(
        self,
        organization_id: str,
        rep_data: RepresentativeCreate,
        current_user: CurrentUser
    ) -> Representative:
        """Create representative at organization level."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id, cost=3)
            
            # Validate inputs
            validated_org_id = self._normalize_identifier(
                organization_id, "Organization"
            )
            validated_rep_data = await self._validate_representative_input(rep_data)
            
            # Verify organization exists and authorize
            organization = await self.org_service.get_organization_by_id(validated_org_id)
            if not organization:
                raise RepresentativeError("Organization not found", status.HTTP_404_NOT_FOUND)
            
            await self.auth_service.check_organization_access(
                current_user, validated_org_id, "create_representative"
            )
            
            # Handle primary representative logic
            if validated_rep_data.is_primary:
                await self._handle_primary_representative_change(
                    organization_id=validated_org_id,
                    level=RepresentativeLevel.ORGANIZATION
                )
            
            # Create representative
            representative = await self.rep_service.create_representative(
                organization_id=validated_org_id,
                rep_data=validated_rep_data,
                level=RepresentativeLevel.ORGANIZATION,
                current_user=current_user
            )

            # Audit log
            await self._safe_audit_log(
                "log_representative_created",
                current_user.id,
                representative.id,
                "organization",
                validated_org_id,
            )
            
            return representative
            
        except RepresentativeError:
            raise
        except Exception as e:
            logger.error(f"Failed to create organization representative: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Representative creation service temporarily unavailable"
            )

    async def create_project_representative(
        self,
        project_id: str,
        rep_data: RepresentativeCreate,
        current_user: CurrentUser
    ) -> Representative:
        """Create representative at project level."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id, cost=3)
            
            # Normalize identifiers
            validated_project_id = self._normalize_identifier(project_id, "Project")
            validated_rep_data = await self._validate_representative_input(rep_data)
            
            # Verify project exists and get organization context
            project = await self.project_service.get_project_by_id(validated_project_id)
            if not project:
                raise RepresentativeError("Project not found", status.HTTP_404_NOT_FOUND)
            
            # Authorization check
            await self.auth_service.check_project_access(
                current_user, project, "create_representative"
            )
            
            # Handle primary representative logic
            if validated_rep_data.is_primary:
                await self._handle_primary_representative_change(
                    project_id=validated_project_id,
                    level=RepresentativeLevel.PROJECT
                )
            
            # Create representative
            org_id = project.get("organization_id") or project.get("organizationId")
            if not org_id:
                raise RepresentativeError(
                    "Project missing organization context",
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                )

            payload = validated_rep_data.model_copy(
                update={
                    "project_id": validated_project_id,
                    "organization_id": org_id,
                    "level": RepresentativeLevel.PROJECT,
                }
            )

            representative = await self.rep_service.create_representative(
                party_id=None,
                rep_data=payload,
                level=RepresentativeLevel.PROJECT,
                current_user=current_user,
            )

            # Audit log
            await self._safe_audit_log(
                "log_representative_created",
                current_user.id,
                representative.id,
                "project",
                validated_project_id,
            )
            
            return representative
            
        except RepresentativeError:
            raise
        except Exception as e:
            logger.error(f"Failed to create project representative: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Representative creation service temporarily unavailable"
            )

    async def get_representatives(
        self,
        pagination: Dict[str, int],
        filters: Dict[str, Any],
        current_user: CurrentUser
    ) -> RepresentativeListResponse:
        """Get representatives with filtering and authorization."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)
            
            # Build authorized query
            authorized_query = await self.auth_service.build_representative_query(
                current_user, filters
            )
            
            # Get representatives with pagination
            representatives, total_count = await self.rep_service.get_representatives_paginated(
                authorized_query, pagination
            )
            
            return RepresentativeListResponse(
                representatives=representatives,
                total=total_count,
                page=pagination["skip"] // pagination["limit"] + 1,
                limit=pagination["limit"]
            )
            
        except Exception as e:
            logger.error(f"Failed to get representatives: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Representative service temporarily unavailable"
            )

    async def get_party_representatives(
        self,
        party_id: str,
        current_user: CurrentUser
    ) -> List[Representative]:
        """Get representatives for a specific party."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)
            
            # Validate party ID
            validated_party_id = validate_object_id(party_id)
            
            # Get party and authorize
            party = await self.party_service.get_party_by_id(validated_party_id)
            if not party:
                raise RepresentativeError("Party not found", status.HTTP_404_NOT_FOUND)
            
            await self.auth_service.check_party_access(current_user, party, "read")
            
            # Get representatives
            representatives = await self.rep_service.get_representatives_for_party(
                validated_party_id
            )
            
            return representatives
            
        except RepresentativeError:
            raise
        except Exception as e:
            logger.error(f"Failed to get party representatives: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Representative service temporarily unavailable"
            )

    async def get_project_representatives(
        self,
        project_id: str,
        include_head_office: bool,
        current_user: CurrentUser
    ) -> List[Representative]:
        """Get representatives for a project with optional head office inclusion."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)
            
            # Normalize project ID
            validated_project_id = self._normalize_identifier(project_id, "Project")
            
            # Get project and authorize
            project = await self.project_service.get_project_by_id(validated_project_id)
            if not project:
                raise RepresentativeError("Project not found", status.HTTP_404_NOT_FOUND)
            
            await self.auth_service.check_project_access(current_user, project, "read")
            
            # Get representatives with proper scope
            org_id = project.get("organization_id") or project.get("organizationId")
            representatives = await self.rep_service.get_representatives_for_project(
                validated_project_id, org_id, include_head_office
            )
            
            return representatives
            
        except RepresentativeError:
            raise
        except Exception as e:
            logger.error(f"Failed to get project representatives: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Representative service temporarily unavailable"
            )

    async def update_representative(
        self,
        representative_id: str,
        update_data: RepresentativeUpdate,
        current_user: CurrentUser
    ) -> Representative:
        """Update representative with validation and authorization."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id, cost=2)
            
            # Validate inputs
            validated_rep_id = validate_object_id(representative_id)
            validated_update = await self._validate_representative_update(update_data)
            
            # Get existing representative
            existing_rep = await self.rep_service.get_representative_by_id(validated_rep_id)
            if not existing_rep:
                raise RepresentativeError(
                    "Representative not found",
                    status.HTTP_404_NOT_FOUND
                )
            
            # Authorization check based on representative's context
            await self._authorize_representative_operation(
                current_user, existing_rep, "update"
            )
            
            # Handle primary representative changes
            if validated_update.is_primary and validated_update.is_primary != existing_rep.is_primary:
                await self._handle_primary_representative_change_for_update(existing_rep)
            
            # Update representative
            updated_rep = await self.rep_service.update_representative(
                validated_rep_id, validated_update, current_user
            )

            # Audit log
            changed_fields = self._get_changed_fields(existing_rep, validated_update)
            await self._safe_audit_log(
                "log_representative_updated",
                current_user.id,
                validated_rep_id,
                changed_fields,
            )
            
            return updated_rep
            
        except RepresentativeError:
            raise
        except Exception as e:
            logger.error(f"Failed to update representative {representative_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Representative update failed"
            )

    async def delete_representative(
        self,
        representative_id: str,
        current_user: CurrentUser
    ) -> Dict[str, str]:
        """Delete representative with authorization checks."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id, cost=5)
            
            # Validate representative ID
            validated_rep_id = validate_object_id(representative_id)
            
            # Get representative for authorization
            representative = await self.rep_service.get_representative_by_id(validated_rep_id)
            if not representative:
                raise RepresentativeError(
                    "Representative not found",
                    status.HTTP_404_NOT_FOUND
                )
            
            # Authorization check
            await self._authorize_representative_operation(
                current_user, representative, "delete"
            )
            
            # Delete representative
            await self.rep_service.delete_representative(validated_rep_id, current_user)

            # Audit log
            await self._safe_audit_log(
                "log_representative_deleted",
                current_user.id,
                validated_rep_id,
                representative.name,
            )
            
            return {"message": "Representative deleted successfully"}
            
        except RepresentativeError:
            raise
        except Exception as e:
            logger.error(f"Failed to delete representative {representative_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Representative deletion failed"
            )

    async def _validate_representative_input(
        self, rep_data: RepresentativeCreate
    ) -> RepresentativeCreate:
        """Comprehensive representative input validation aligned with data model."""
        return RepresentativeCreate(
            party_id=rep_data.party_id,
            organization_id=rep_data.organization_id,
            project_id=rep_data.project_id,
            name=sanitize_text(
                validate_input(rep_data.name, max_length=200, required=True)
            ),
            email=validate_email(rep_data.email),
            contact_number=validate_phone(rep_data.contact_number)
            if rep_data.contact_number
            else None,
            designation=sanitize_text(
                validate_input(rep_data.designation, max_length=200)
            )
            if rep_data.designation
            else None,
            is_primary=bool(rep_data.is_primary),
            level=rep_data.level or RepresentativeLevel.ORGANIZATION,
            use_head_office=bool(rep_data.use_head_office),
        )

    async def _validate_representative_update(
        self, update_data: RepresentativeUpdate
    ) -> RepresentativeUpdate:
        """Validate representative update data."""
        validated_fields = {}
        
        if update_data.name is not None:
            validated_fields['name'] = sanitize_text(
                validate_input(update_data.name, max_length=200, required=True)
            )
        
        if update_data.email is not None:
            validated_fields['email'] = validate_email(update_data.email)
        
        if update_data.phone is not None:
            validated_fields['phone'] = validate_phone(update_data.phone)
        
        if update_data.designation is not None:
            validated_fields['designation'] = sanitize_text(
                validate_input(update_data.designation, max_length=200)
            )
        
        if update_data.address is not None:
            validated_fields['address'] = sanitize_text(
                validate_input(update_data.address, max_length=500)
            )
        
        # Boolean fields
        for field in ['is_primary', 'is_active', 'use_head_office']:
            if getattr(update_data, field, None) is not None:
                validated_fields[field] = getattr(update_data, field)
        
        return RepresentativeUpdate(**validated_fields)

    async def _authorize_representative_operation(
        self,
        current_user: CurrentUser,
        representative: Representative,
        operation: str
    ):
        """Centralized authorization for representative operations."""
        # Check based on representative's context
        if representative.party_id:
            party = await self.party_service.get_party_by_id(representative.party_id)
            if party:
                await self.auth_service.check_party_access(current_user, party, operation)
        
        elif representative.project_id:
            project = await self.project_service.get_project_by_id(representative.project_id)
            if project:
                await self.auth_service.check_project_access(current_user, project, operation)
        
        elif representative.organization_id:
            await self.auth_service.check_organization_access(
                current_user, representative.organization_id, operation
            )
        else:
            # Fallback authorization check
            await self.auth_service.require_permission(
                current_user, f"representatives:{operation}"
            )

    async def _handle_primary_representative_change(
        self,
        level: RepresentativeLevel,
        party_id: Optional[str] = None,
        organization_id: Optional[str] = None,
        project_id: Optional[str] = None
    ):
        """Handle primary representative changes atomically."""
        if level == RepresentativeLevel.PARTY and party_id:
            await self.rep_service.unset_primary_representatives(party_id=party_id)
        elif level == RepresentativeLevel.ORGANIZATION and organization_id:
            await self.rep_service.unset_primary_representatives(organization_id=organization_id)
        elif level == RepresentativeLevel.PROJECT and project_id:
            await self.rep_service.unset_primary_representatives(project_id=project_id)

    def _normalize_identifier(self, identifier: str, label: str) -> str:
        value = (identifier or "").strip()
        if not value:
            raise RepresentativeError(
                f"{label} identifier is required", status.HTTP_422_UNPROCESSABLE_ENTITY
            )
        return value

    async def _safe_audit_log(self, method_name: str, *args):
        if not self.audit_logger:
            return
        method = getattr(self.audit_logger, method_name, None)
        if not method:
            return
        try:
            await method(*args)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Audit log skipped (%s): %s", method_name, exc)

    async def _handle_primary_representative_change_for_update(
        self, existing_rep: Representative
    ):
        """Handle primary representative change during update."""
        if existing_rep.level == RepresentativeLevel.PARTY:
            await self.rep_service.unset_primary_representatives(party_id=existing_rep.party_id)
        elif existing_rep.level == RepresentativeLevel.ORGANIZATION:
            await self.rep_service.unset_primary_representatives(organization_id=existing_rep.organization_id)
        elif existing_rep.level == RepresentativeLevel.PROJECT:
            await self.rep_service.unset_primary_representatives(project_id=existing_rep.project_id)

    def _get_changed_fields(
        self, original: Representative, update: RepresentativeUpdate
    ) -> List[str]:
        """Get list of fields that were changed."""
        changed_fields = []
        
        for field_name in update.__fields_set__:
            if hasattr(original, field_name):
                old_value = getattr(original, field_name)
                new_value = getattr(update, field_name)
                if old_value != new_value:
                    changed_fields.append(field_name)
        
        return changed_fields


# Dependency injection
async def get_representative_controller() -> RepresentativeController:
    """Factory function for representative controller."""
    rep_service = RepresentativeService()
    party_service = PartyService()
    project_service = ProjectService()
    org_service = OrganizationService()
    auth_service = AuthorizationService()
    rate_limiter = RateLimiter(
        requests_per_minute=100,
        window_seconds=3600
    )
    audit_logger = AuditLogger()
    
    return RepresentativeController(
        rep_service, party_service, project_service, org_service,
        auth_service, rate_limiter, audit_logger
    )


# API Endpoints - Party Representatives
@router.post("/parties/{party_id}/representatives", response_model=Representative)
@handle_exceptions
async def add_party_representative(
    party_id: str,
    rep_data: RepresentativeCreate,
    controller: RepresentativeController = Depends(get_representative_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Add representative to a party."""
    return await controller.create_party_representative(party_id, rep_data, current_user)


@router.get("/parties/{party_id}/representatives", response_model=List[Representative])
@handle_exceptions
async def get_party_representatives(
    party_id: str,
    controller: RepresentativeController = Depends(get_representative_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get representatives for a party."""
    return await controller.get_party_representatives(party_id, current_user)


# API Endpoints - Organization Representatives
@router.post("/organizations/{organization_id}/representatives", response_model=Representative)
@handle_exceptions
async def add_organization_representative(
    organization_id: str,
    rep_data: RepresentativeCreate,
    controller: RepresentativeController = Depends(get_representative_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Add representative at organization level."""
    return await controller.create_organization_representative(organization_id, rep_data, current_user)


@router.get("/organizations/{organization_id}/representatives", response_model=List[Representative])
@handle_exceptions
async def get_organization_representatives(
    organization_id: str,
    controller: RepresentativeController = Depends(get_representative_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get representatives for an organization."""
    # Simple passthrough - detailed logic in controller
    return await controller.rep_service.get_representatives_for_organization(organization_id)


# API Endpoints - Project Representatives
@router.post("/projects/{project_id}/representatives", response_model=Representative)
@handle_exceptions
async def add_project_representative(
    project_id: str,
    rep_data: RepresentativeCreate,
    controller: RepresentativeController = Depends(get_representative_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Add representative at project level."""
    return await controller.create_project_representative(project_id, rep_data, current_user)


@router.get("/projects/{project_id}/representatives", response_model=List[Representative])
@handle_exceptions
async def get_project_representatives(
    project_id: str,
    include_head_office: bool = Query(True),
    controller: RepresentativeController = Depends(get_representative_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get representatives for a project."""
    return await controller.get_project_representatives(project_id, include_head_office, current_user)


# API Endpoints - Generic Representative Operations
@router.get("/representatives", response_model=RepresentativeListResponse)
@handle_exceptions
async def get_all_representatives(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    level: Optional[RepresentativeLevel] = Query(None),
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    controller: RepresentativeController = Depends(get_representative_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get all representatives with filtering."""
    filters = {
        'level': level,
        'organization_id': organization_id,
        'project_id': project_id,
        'search': search
    }
    pagination = {'skip': skip, 'limit': limit}
    
    return await controller.get_representatives(pagination, filters, current_user)


@router.put("/representatives/{representative_id}", response_model=Representative)
@handle_exceptions
async def update_representative(
    representative_id: str,
    update_data: RepresentativeUpdate,
    controller: RepresentativeController = Depends(get_representative_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Update representative by ID."""
    return await controller.update_representative(representative_id, update_data, current_user)


@router.delete("/representatives/{representative_id}")
@handle_exceptions
async def delete_representative(
    representative_id: str,
    controller: RepresentativeController = Depends(get_representative_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Delete representative by ID."""
    return await controller.delete_representative(representative_id, current_user)
