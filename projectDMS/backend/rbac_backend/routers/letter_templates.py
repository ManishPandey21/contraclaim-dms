# Letter template management

from __future__ import annotations

import logging
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..core.database import get_db
from ..core.security import CurrentUser, get_current_user
from ..models.letter_template import (
    LetterTemplate,
    LetterTemplateCreate,
    LetterTemplateListResponse,
    LetterTemplateUpdate,
)
from ..services.authorization_service import AuthorizationService
from ..services.letter_template_service import LetterTemplateService
from ..services.policy_service import PolicyService
from ..utils.error_handler import BaseDomainError, AuthorizationError, TemplateError, handle_exceptions
from ..utils.rate_limiter import RateLimiter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/letter-templates", tags=["letter-templates"])


class LetterTemplateController:
    def __init__(
        self,
        template_service: LetterTemplateService,
        auth_service: AuthorizationService,
        rate_limiter: RateLimiter,
    ) -> None:
        self.template_service = template_service
        self.auth_service = auth_service
        self.rate_limiter = rate_limiter

    async def get_templates(
        self,
        pagination: Dict[str, int],
        filters: Dict[str, Optional[str]],
        current_user: CurrentUser,
    ) -> LetterTemplateListResponse:
        try:
            await self.rate_limiter.check_user_limit(current_user.id)
            await self.auth_service.require_permission(
                current_user, "letter_templates:read"
            )

            authorized_query = await self.auth_service.build_letter_template_query(
                current_user, filters
            )
            templates, total = await self.template_service.get_templates_paginated(
                authorized_query, pagination
            )

            return LetterTemplateListResponse(
                templates=templates,
                total=total,
                page=pagination["skip"] // pagination["limit"] + 1,
                limit=pagination["limit"],
            )
        except (BaseDomainError, HTTPException):
            raise
        except Exception as exc:
            logger.error("Failed to get templates: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Template service temporarily unavailable",
            )

    async def get_template(
        self, template_id: str, current_user: CurrentUser
    ) -> LetterTemplate:
        try:
            await self.rate_limiter.check_user_limit(current_user.id)
            await self.auth_service.require_permission(
                current_user, "letter_templates:read"
            )
            template = await self.template_service.get_template_by_id(template_id)
            if not template:
                raise TemplateError("Template not found", status.HTTP_404_NOT_FOUND)
            await self.auth_service.check_letter_template_access(
                current_user, template, "read"
            )
            return template
        except (BaseDomainError, HTTPException):
            raise
        except TemplateError:
            raise
        except Exception as exc:
            logger.error("Failed to get template %s: %s", template_id, exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Template service temporarily unavailable",
            )

    async def create_template(
        self, data: LetterTemplateCreate, current_user: CurrentUser
    ) -> LetterTemplate:
        try:
            await self.rate_limiter.check_user_limit(current_user.id, cost=2)
            await self.auth_service.require_permission(
                current_user, "letter_templates:create"
            )
            return await self.template_service.create_template(data, current_user)
        except (BaseDomainError, HTTPException):
            raise
        except TemplateError:
            raise
        except Exception as exc:
            logger.error("Failed to create template: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Template creation service temporarily unavailable",
            )

    async def update_template(
        self,
        template_id: str,
        update_data: LetterTemplateUpdate,
        current_user: CurrentUser,
    ) -> LetterTemplate:
        try:
            await self.rate_limiter.check_user_limit(current_user.id, cost=2)
            await self.auth_service.require_permission(
                current_user, "letter_templates:update"
            )
            template = await self.template_service.get_template_by_id(template_id)
            if not template:
                raise TemplateError("Template not found", status.HTTP_404_NOT_FOUND)
            await self.auth_service.check_letter_template_access(
                current_user, template, "update"
            )
            return await self.template_service.update_template(
                template_id, update_data, current_user
            )
        except (BaseDomainError, HTTPException):
            raise
        except TemplateError:
            raise
        except Exception as exc:
            logger.error("Failed to update template %s: %s", template_id, exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Template update service temporarily unavailable",
            )

    async def delete_template(
        self, template_id: str, current_user: CurrentUser
    ) -> Dict[str, str]:
        try:
            await self.rate_limiter.check_user_limit(current_user.id, cost=4)
            await self.auth_service.require_permission(
                current_user, "letter_templates:delete"
            )
            template = await self.template_service.get_template_by_id(template_id)
            if not template:
                raise TemplateError("Template not found", status.HTTP_404_NOT_FOUND)
            await self.auth_service.check_letter_template_access(
                current_user, template, "delete"
            )
            await self.template_service.delete_template(template_id, current_user)
            return {"message": "Template deleted successfully"}
        except (BaseDomainError, HTTPException):
            raise
        except TemplateError:
            raise
        except Exception as exc:
            logger.error("Failed to delete template %s: %s", template_id, exc)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Template deletion service temporarily unavailable",
            )


async def get_letter_template_controller() -> LetterTemplateController:
    template_service = LetterTemplateService()
    auth_service = AuthorizationService()
    rate_limiter = RateLimiter(max_requests=120, window_seconds=3600, scope="letter_templates")
    return LetterTemplateController(template_service, auth_service, rate_limiter)


async def get_policy_service(db=Depends(get_db)) -> PolicyService:
    return PolicyService(db)


async def _authorize_template_target(
    policy: PolicyService,
    controller: LetterTemplateController,
    current_user: CurrentUser,
    template_id: str,
    permission: str,
) -> LetterTemplate:
    template = await controller.template_service.get_template_by_id(template_id)
    if not template:
        raise TemplateError("Template not found", status.HTTP_404_NOT_FOUND)
    if (
        not template.organization_id
        and not policy.scope_service.is_superadmin(current_user)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Global or unscoped templates require Super Admin access",
        )
    await policy.authorize(
        current_user,
        permission,
        organization_id=template.organization_id,
        project_id=template.project_id,
        resource_type="letter_template",
        resource_id=template_id,
    )
    return template


@router.get("", response_model=LetterTemplateListResponse)
@handle_exceptions
async def list_templates(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    status_value: Optional[str] = Query(None, alias="status"),
    visibility: Optional[str] = Query(None),
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    controller: LetterTemplateController = Depends(get_letter_template_controller),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    filters = {
        "search": search,
        "category": category,
        "status": status_value,
        "visibility": visibility,
        "organization_id": organization_id,
        "project_id": project_id,
    }
    requested_org = organization_id or getattr(current_user, "organization_id", None)
    requested_project = project_id or getattr(current_user, "project_id", None)
    if not requested_org and not policy.scope_service.is_superadmin(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization scope is required")
    await policy.authorize(
        current_user,
        "letter_templates:read",
        organization_id=str(requested_org) if requested_org else None,
        project_id=str(requested_project) if requested_project else None,
        resource_type="letter_template_collection",
        audit=False,
    )
    pagination = {"skip": skip, "limit": limit}
    result = await controller.get_templates(pagination, filters, current_user)
    for template in result.templates:
        item_org = template.organization_id or requested_org
        await policy.authorize(
            current_user,
            "letter_templates:read",
            organization_id=str(item_org) if item_org else None,
            project_id=template.project_id,
            resource_type="letter_template",
            resource_id=str(template.id or ""),
            audit=False,
        )
    return result


@router.get("/{template_id}", response_model=LetterTemplate)
@handle_exceptions
async def get_template(
    template_id: str,
    controller: LetterTemplateController = Depends(get_letter_template_controller),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    template = await controller.template_service.get_template_by_id(template_id)
    if not template:
        raise TemplateError("Template not found", status.HTTP_404_NOT_FOUND)
    organization_id = template.organization_id or getattr(current_user, "organization_id", None)
    if not organization_id and not policy.scope_service.is_superadmin(current_user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization scope is required")
    await policy.authorize(
        current_user,
        "letter_templates:read",
        organization_id=str(organization_id) if organization_id else None,
        project_id=template.project_id,
        resource_type="letter_template",
        resource_id=template_id,
        audit=False,
    )
    return await controller.get_template(template_id, current_user)


@router.post("", response_model=LetterTemplate)
@handle_exceptions
async def create_template(
    data: LetterTemplateCreate,
    controller: LetterTemplateController = Depends(get_letter_template_controller),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    if not data.organization_id and not policy.scope_service.is_superadmin(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Organization scope is required for non-global templates",
        )
    await policy.authorize(
        current_user,
        "letter_templates:create",
        organization_id=data.organization_id,
        project_id=data.project_id,
        resource_type="letter_template",
    )
    return await controller.create_template(data, current_user)


@router.put("/{template_id}", response_model=LetterTemplate)
@handle_exceptions
async def update_template(
    template_id: str,
    data: LetterTemplateUpdate,
    controller: LetterTemplateController = Depends(get_letter_template_controller),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    await _authorize_template_target(
        policy, controller, current_user, template_id, "letter_templates:update"
    )
    return await controller.update_template(template_id, data, current_user)


@router.delete("/{template_id}")
@handle_exceptions
async def delete_template(
    template_id: str,
    controller: LetterTemplateController = Depends(get_letter_template_controller),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    await _authorize_template_target(
        policy, controller, current_user, template_id, "letter_templates:delete"
    )
    return await controller.delete_template(template_id, current_user)
