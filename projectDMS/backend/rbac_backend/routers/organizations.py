# Improved organizations.py
"""
Secure organization management with comprehensive validation, proper authorization,
and clean architecture. Addresses security vulnerabilities and performance issues.
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status, Query
from typing import List, Optional, Dict, Any
import logging
from datetime import datetime
import re

from ..core.security import get_current_user, CurrentUser, authorize_scope, require_permission
from ..core.database import get_db
from ..services.organization_service import OrganizationService
from ..services.authorization_service import AuthorizationService
from ..services.step_up_service import require_step_up
from ..models.organization import (
    Organization, OrganizationCreate, OrganizationUpdate, 
    OrganizationResponse, OrganizationListResponse
)
from ..utils.validation import (
    validate_input, sanitize_text, validate_pan_number, 
    validate_gst_number, validate_email, validate_phone
)
from ..utils.error_handler import handle_exceptions, OrganizationError
from ..utils.rate_limiter import RateLimiter
from ..utils.audit_logger import AuditLogger

logger = logging.getLogger(__name__)
router = APIRouter()


class OrganizationController:
    """Secure organization controller with comprehensive validation and authorization."""
    
    def __init__(
        self,
        org_service: OrganizationService,
        auth_service: AuthorizationService,
        rate_limiter: RateLimiter,
        audit_logger: AuditLogger
    ):
        self.org_service = org_service
        self.auth_service = auth_service
        self.rate_limiter = rate_limiter
        self.audit_logger = audit_logger

    async def create_organization(
        self,
        org_data: OrganizationCreate,
        current_user: CurrentUser
    ) -> Organization:
        """Create organization with comprehensive validation."""
        try:
            # Rate limiting for expensive operations
            await self.rate_limiter.check_user_limit(current_user.id, cost=10)
            
            # Authorization check - only superadmin can create organizations
            await self.auth_service.require_role(current_user, "superadmin")
            
            # Validate and sanitize input
            validated_data = await self._validate_organization_input(org_data)
            
            # Check for duplicates
            await self._check_organization_duplicates(validated_data)
            
            # Create organization
            organization = await self.org_service.create_organization(
                validated_data, current_user
            )
            
            # Audit log
            await self.audit_logger.log_organization_created(
                current_user.id, organization.id, organization.name
            )
            
            return organization
            
        except OrganizationError:
            raise
        except Exception as e:
            logger.error(f"Organization creation failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Organization creation service temporarily unavailable"
            )

    async def get_organizations(
        self,
        pagination: Dict[str, int],
        filters: Dict[str, Any],
        current_user: CurrentUser
    ) -> OrganizationListResponse:
        """Get organizations with proper authorization and filtering."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)
            
            # Build authorized query based on user role
            authorized_query = await self.auth_service.build_organization_query(
                current_user, filters
            )
            
            # Get organizations with pagination
            organizations, total_count = await self.org_service.get_organizations_paginated(
                authorized_query, pagination
            )
            
            # Normalize to OrganizationResponse to satisfy response_model and avoid validation errors
            resp_orgs = []
            for org in organizations:
                if hasattr(org, "model_dump"):
                    base_data = org.model_dump(by_alias=True, exclude_none=False)  # type: ignore[attr-defined]
                elif isinstance(org, dict):
                    base_data = dict(org)
                else:
                    base_data = {
                        "_id": getattr(org, "id", getattr(org, "_id", None)),
                        "name": getattr(org, "name", None),
                        "shortName": getattr(org, "shortName", None),
                        "email": getattr(org, "email", None),
                        "phone": getattr(org, "phone", None),
                        "panNumber": getattr(org, "panNumber", None),
                        "gstNumber": getattr(org, "gstNumber", None),
                        "address": getattr(org, "address", None),
                        "city": getattr(org, "city", None),
                        "state": getattr(org, "state", None),
                        "pinCode": getattr(org, "pinCode", None),
                        "adminName": getattr(org, "adminName", None),
                        "adminEmail": getattr(org, "adminEmail", None),
                        "adminContact": getattr(org, "adminContact", None),
                        "billingEnabled": getattr(org, "billingEnabled", False),
                        "is_active": getattr(org, "is_active", True),
                        "created_at": getattr(org, "created_at", None),
                        "updated_at": getattr(org, "updated_at", None),
                        "created_by": getattr(org, "created_by", None),
                        "user_count": getattr(org, "user_count", None),
                        "project_count": getattr(org, "project_count", None),
                        "document_count": getattr(org, "document_count", None),
                    }

                base_data.setdefault("_id", base_data.get("id"))
                base_data.setdefault("projectsCount", base_data.get("project_count"))
                base_data.setdefault("employeesCount", base_data.get("user_count"))
                base_data.setdefault("lettersCount", base_data.get("document_count"))

                resp_orgs.append(OrganizationResponse.model_validate(base_data))

            return OrganizationListResponse(
                organizations=resp_orgs,
                total=total_count,
                page=pagination["skip"] // pagination["limit"] + 1,
                limit=pagination["limit"]
            )
            
        except Exception as e:
            logger.error(f"Failed to get organizations: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Organization service temporarily unavailable"
            )

    async def get_organization(
        self,
        organization_id: str,
        current_user: CurrentUser
    ) -> Organization:
        """Get single organization with authorization."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)
            
            # Authorization check
            await self.auth_service.check_organization_access(
                current_user, organization_id, "read"
            )
            
            # Get organization
            organization = await self.org_service.get_organization_by_id(organization_id)
            if not organization:
                raise OrganizationError(
                    "Organization not found", 
                    status.HTTP_404_NOT_FOUND
                )
            
            return organization
            
        except OrganizationError:
            raise
        except Exception as e:
            logger.error(f"Failed to get organization {organization_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Organization service temporarily unavailable"
            )

    async def update_organization(
        self,
        organization_id: str,
        update_data: OrganizationUpdate,
        current_user: CurrentUser
    ) -> Organization:
        """Update organization with validation and authorization."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id, cost=5)
            
            # Authorization check
            await self.auth_service.check_organization_access(
                current_user, organization_id, "update"
            )
            
            # Get existing organization
            existing_org = await self.org_service.get_organization_by_id(organization_id)
            if not existing_org:
                raise OrganizationError(
                    "Organization not found",
                    status.HTTP_404_NOT_FOUND
                )
            
            # Validate update data
            validated_update = await self._validate_organization_update(
                update_data, organization_id
            )
            
            # Update organization
            updated_org = await self.org_service.update_organization(
                organization_id, validated_update, current_user
            )
            
            # Audit log
            changed_fields = self._get_changed_fields(existing_org, validated_update)
            await self.audit_logger.log_organization_updated(
                current_user.id, organization_id, changed_fields
            )
            
            return updated_org
            
        except OrganizationError:
            raise
        except Exception as e:
            logger.error(f"Failed to update organization {organization_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Organization update failed"
            )

    async def delete_organization(
        self,
        organization_id: str,
        current_user: CurrentUser
    ) -> Dict[str, str]:
        """Delete organization with comprehensive checks."""
        try:
            # Rate limiting for destructive operations
            await self.rate_limiter.check_user_limit(current_user.id, cost=20)
            
            # Authorization check - only superadmin or org admin can delete
            await self.auth_service.check_organization_access(
                current_user, organization_id, "delete"
            )
            
            # Get organization for audit
            organization = await self.org_service.get_organization_by_id(organization_id)
            if not organization:
                raise OrganizationError(
                    "Organization not found",
                    status.HTTP_404_NOT_FOUND
                )
            
            # Check for dependencies (users, projects, etc.)
            dependencies = await self.org_service.check_organization_dependencies(
                organization_id
            )
            if dependencies:
                raise OrganizationError(
                    f"Cannot delete organization with active dependencies: {', '.join(dependencies)}",
                    status.HTTP_409_CONFLICT
                )
            
            # Delete organization
            await self.org_service.delete_organization(organization_id, current_user)
            
            # Audit log
            await self.audit_logger.log_organization_deleted(
                current_user.id, organization_id, organization.name
            )
            
            return {"message": "Organization deleted successfully"}
            
        except OrganizationError:
            raise
        except Exception as e:
            logger.error(f"Failed to delete organization {organization_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Organization deletion failed"
            )

    async def _validate_organization_input(
        self, org_data: OrganizationCreate
    ) -> OrganizationCreate:
        """Comprehensive input validation and sanitization."""
        return OrganizationCreate(
            name=sanitize_text(validate_input(org_data.name, max_length=200, required=True)),
            shortName=sanitize_text(validate_input(org_data.shortName, max_length=10)),
            email=validate_email(org_data.email) if org_data.email else None,
            phone=validate_phone(org_data.phone) if org_data.phone else None,
            panNumber=validate_pan_number(org_data.panNumber) if org_data.panNumber else None,
            gstNumber=validate_gst_number(org_data.gstNumber) if org_data.gstNumber else None,
            address=sanitize_text(validate_input(org_data.address, max_length=500)) if org_data.address else None,
            city=sanitize_text(validate_input(org_data.city, max_length=100)) if org_data.city else None,
            state=sanitize_text(validate_input(org_data.state, max_length=100)) if org_data.state else None,
            pinCode=validate_input(
                org_data.pinCode, 
                pattern=r'^\d{6}$' if org_data.pinCode else None
            ) if org_data.pinCode else None,
            adminName=sanitize_text(validate_input(org_data.adminName, max_length=200)) if org_data.adminName else None,
            adminEmail=validate_email(org_data.adminEmail) if org_data.adminEmail else None,
            adminContact=validate_phone(org_data.adminContact) if org_data.adminContact else None,
            billingEnabled=org_data.billingEnabled or False
        )

    async def _validate_organization_update(
        self, update_data: OrganizationUpdate, org_id: str
    ) -> OrganizationUpdate:
        """Validate organization update data."""
        validated_fields = {}
        
        if update_data.name is not None:
            validated_fields['name'] = sanitize_text(
                validate_input(update_data.name, max_length=200, required=True)
            )
            
        if update_data.shortName is not None:
            validated_fields['shortName'] = sanitize_text(
                validate_input(update_data.shortName, max_length=10)
            )
            
        if update_data.email is not None:
            validated_fields['email'] = validate_email(update_data.email)
            
        if update_data.phone is not None:
            validated_fields['phone'] = validate_phone(update_data.phone)
            
        if update_data.panNumber is not None:
            validated_fields['panNumber'] = validate_pan_number(update_data.panNumber)
            
        if update_data.gstNumber is not None:
            validated_fields['gstNumber'] = validate_gst_number(update_data.gstNumber)
            
        # Validate other fields...
        for field in ['address', 'city', 'state', 'adminName']:
            if getattr(update_data, field, None) is not None:
                validated_fields[field] = sanitize_text(
                    validate_input(getattr(update_data, field), max_length=500)
                )
        
        if update_data.adminEmail is not None:
            validated_fields['adminEmail'] = validate_email(update_data.adminEmail)
            
        if update_data.adminContact is not None:
            validated_fields['adminContact'] = validate_phone(update_data.adminContact)
            
        if update_data.billingEnabled is not None:
            validated_fields['billingEnabled'] = update_data.billingEnabled
        
        return OrganizationUpdate(**validated_fields)

    async def _check_organization_duplicates(
        self, org_data: OrganizationCreate
    ):
        """Check for duplicate organizations."""
        # Check name duplicate
        if await self.org_service.organization_exists_by_name(org_data.name):
            raise OrganizationError(
                "Organization with this name already exists",
                status.HTTP_409_CONFLICT
            )
        
        # Check PAN duplicate
        if org_data.panNumber:
            if await self.org_service.organization_exists_by_pan(org_data.panNumber):
                raise OrganizationError(
                    "Organization with this PAN number already exists",
                    status.HTTP_409_CONFLICT
                )

    def _get_changed_fields(
        self, original: Organization, update: OrganizationUpdate
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
async def get_organization_controller() -> OrganizationController:
    """Factory function for organization controller."""
    org_service = OrganizationService()
    auth_service = AuthorizationService()
    rate_limiter = RateLimiter(
        requests_per_minute=100,
        window_seconds=3600
    )
    audit_logger = AuditLogger()
    
    return OrganizationController(
        org_service, auth_service, rate_limiter, audit_logger
    )


# API Endpoints
@router.post("/organizations", response_model=Organization)
@handle_exceptions
async def create_organization(
    org_data: OrganizationCreate,
    controller: OrganizationController = Depends(get_organization_controller),
    current_user: CurrentUser = Depends(get_current_user),
    _: None = Depends(require_permission("organizations:create")),
):
    """Create new organization with validation."""
    return await controller.create_organization(org_data, current_user)


@router.get("/organizations", response_model=OrganizationListResponse)
@handle_exceptions
async def get_organizations(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    name: Optional[str] = Query(None),
    city: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    controller: OrganizationController = Depends(get_organization_controller),
    current_user: CurrentUser = Depends(get_current_user),
    _: None = Depends(require_permission("organizations:read")),
):
    """Get organizations with filtering and pagination."""
    filters = {
        'name': name,
        'city': city,
        'state': state
    }
    pagination = {'skip': skip, 'limit': limit}
    
    return await controller.get_organizations(pagination, filters, current_user)


@router.get("/organizations/{organization_id}", response_model=Organization)
@handle_exceptions
async def get_organization(
    organization_id: str,
    controller: OrganizationController = Depends(get_organization_controller),
    current_user: CurrentUser = Depends(get_current_user),
    _: None = Depends(require_permission("organizations:read")),
):
    """Get specific organization by ID."""
    return await controller.get_organization(organization_id, current_user)


@router.put("/organizations/{organization_id}", response_model=Organization)
@handle_exceptions
async def update_organization(
    organization_id: str,
    update_data: OrganizationUpdate,
    controller: OrganizationController = Depends(get_organization_controller),
    current_user: CurrentUser = Depends(get_current_user),
    _: None = Depends(require_permission("organizations:update")),
):
    """Update organization with validation."""
    return await controller.update_organization(organization_id, update_data, current_user)


@router.delete("/organizations/{organization_id}")
@handle_exceptions
async def delete_organization(
    organization_id: str,
    request: Request,
    controller: OrganizationController = Depends(get_organization_controller),
    current_user: CurrentUser = Depends(get_current_user),
    _: None = Depends(require_permission("organizations:delete")),
):
    """Delete organization with dependency checks."""
    await require_step_up(request, current_user, action="organizations.delete")
    return await controller.delete_organization(organization_id, current_user)


# Additional utility endpoints
@router.get("/organizations/{organization_id}/stats")
@handle_exceptions
async def get_organization_stats(
    organization_id: str,
    controller: OrganizationController = Depends(get_organization_controller),
    current_user: CurrentUser = Depends(get_current_user),
    _: None = Depends(require_permission("organizations:read")),
):
    """Get organization statistics."""
    await controller.auth_service.check_organization_access(
        current_user, organization_id, "read"
    )
    
    return await controller.org_service.get_organization_stats(organization_id)


@router.post("/organizations/{organization_id}/validate")
@handle_exceptions
async def validate_organization_data(
    organization_id: str,
    controller: OrganizationController = Depends(get_organization_controller),
    current_user: CurrentUser = Depends(get_current_user),
    _: None = Depends(require_permission("organizations:update")),
):
    """Validate organization data integrity."""
    await controller.auth_service.check_organization_access(
        current_user, organization_id, "admin"
    )
    
    return await controller.org_service.validate_organization_data(organization_id)
