# Letter template management

from __future__ import annotations

import logging
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..core.security import CurrentUser, get_current_user
from ..models.letter_template import (
    LetterTemplate,
    LetterTemplateCreate,
    LetterTemplateListResponse,
    LetterTemplateUpdate,
)
from ..services.authorization_service import AuthorizationService
from ..services.letter_template_service import LetterTemplateService
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
        except (AuthorizationError, HTTPException):
            raise
        except (BaseDomainError, HTTPException):
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
        except (AuthorizationError, HTTPException):
            raise
        except (BaseDomainError, HTTPException):
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
        except (AuthorizationError, HTTPException):
            raise
        except (BaseDomainError, HTTPException):
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
        except (AuthorizationError, HTTPException):
            raise
        except (BaseDomainError, HTTPException):
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
):
    filters = {
        "search": search,
        "category": category,
        "status": status_value,
        "visibility": visibility,
        "organization_id": organization_id,
        "project_id": project_id,
    }
    pagination = {"skip": skip, "limit": limit}
    return await controller.get_templates(pagination, filters, current_user)


@router.get("/{template_id}", response_model=LetterTemplate)
@handle_exceptions
async def get_template(
    template_id: str,
    controller: LetterTemplateController = Depends(get_letter_template_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    return await controller.get_template(template_id, current_user)


@router.post("", response_model=LetterTemplate)
@handle_exceptions
async def create_template(
    data: LetterTemplateCreate,
    controller: LetterTemplateController = Depends(get_letter_template_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    return await controller.create_template(data, current_user)


@router.put("/{template_id}", response_model=LetterTemplate)
@handle_exceptions
async def update_template(
    template_id: str,
    data: LetterTemplateUpdate,
    controller: LetterTemplateController = Depends(get_letter_template_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    return await controller.update_template(template_id, data, current_user)


@router.delete("/{template_id}")
@handle_exceptions
async def delete_template(
    template_id: str,
    controller: LetterTemplateController = Depends(get_letter_template_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    return await controller.delete_template(template_id, current_user)
