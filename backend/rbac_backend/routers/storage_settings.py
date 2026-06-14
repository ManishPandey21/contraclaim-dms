from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..core.security import get_current_user, CurrentUser, authorize_scope
from ..models.storage_settings import (
    OrganizationStorageSettings,
    ProjectStorageSettings,
    ResolvedStorageSettings,
)
from ..services.storage_settings_service import StorageSettingsService
from ..services.step_up_service import require_step_up

logger = logging.getLogger(__name__)

router = APIRouter()


async def get_storage_settings_service() -> StorageSettingsService:
    return StorageSettingsService()


def _validate_short_name(name: Optional[str]) -> None:
    if not name:
        return
    if len(name) > 10:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Short name must be <= 10 characters",
        )
    if not name.isalnum():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Short name must be alphanumeric",
        )


def _ensure_role(current_user: CurrentUser, org_id: Optional[str], project_id: Optional[str] = None) -> None:
    """
    Reuse authorize_scope for scoping; superadmin bypasses.
    """
    roles = {str(r).lower() for r in (current_user.roles or [])}
    if "superadmin" in roles:
        return
    authorize_scope(current_user, organization_id=org_id, project_id=project_id)


@router.get("/settings/storage/org/{org_id}", response_model=OrganizationStorageSettings)
async def get_org_storage_settings(
    org_id: str,
    service: StorageSettingsService = Depends(get_storage_settings_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    _ensure_role(current_user, org_id)
    settings = await service.get_org_settings(org_id)
    if not settings:
        # Return an empty settings object with defaults
        return OrganizationStorageSettings(org_id=org_id, providers=[])
    return settings


@router.put("/settings/storage/org/{org_id}", response_model=OrganizationStorageSettings)
async def update_org_storage_settings(
    org_id: str,
    payload: OrganizationStorageSettings,
    request: Request,
    service: StorageSettingsService = Depends(get_storage_settings_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    await require_step_up(request, current_user, action="storage.settings.manage")
    _ensure_role(current_user, org_id)
    _validate_short_name(payload.org_short_name)
    saved = await service.upsert_org_settings(org_id, payload)
    return saved


# Compatibility aliases for older client paths
@router.get("/storage-settings/org/{org_id}", response_model=OrganizationStorageSettings)
async def get_org_storage_settings_alias(
    org_id: str,
    service: StorageSettingsService = Depends(get_storage_settings_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    return await get_org_storage_settings(org_id, service, current_user)


@router.put("/storage-settings/org/{org_id}", response_model=OrganizationStorageSettings)
async def update_org_storage_settings_alias(
    org_id: str,
    payload: OrganizationStorageSettings,
    request: Request,
    service: StorageSettingsService = Depends(get_storage_settings_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    await require_step_up(request, current_user, action="storage.settings.manage")
    return await update_org_storage_settings(org_id, payload, request, service, current_user)


@router.get("/settings/storage/project/{project_id}", response_model=ProjectStorageSettings)
async def get_project_storage_settings(
    project_id: str,
    org_id: Optional[str] = None,
    service: StorageSettingsService = Depends(get_storage_settings_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    _ensure_role(current_user, org_id, project_id)
    settings = await service.get_project_settings(project_id)
    if not settings:
        return ProjectStorageSettings(
            project_id=project_id,
            org_id=org_id or current_user.organization_id or "",
            inherit_from_org=True,
        )
    return settings


@router.put("/settings/storage/project/{project_id}", response_model=ProjectStorageSettings)
async def update_project_storage_settings(
    project_id: str,
    payload: ProjectStorageSettings,
    request: Request,
    service: StorageSettingsService = Depends(get_storage_settings_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    await require_step_up(request, current_user, action="storage.settings.manage")
    org_id = payload.org_id or current_user.organization_id
    if not org_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="org_id is required")
    _ensure_role(current_user, org_id, project_id)
    _validate_short_name(payload.project_short_name)
    saved = await service.upsert_project_settings(project_id, org_id, payload)
    return saved


@router.get("/settings/storage/resolve", response_model=ResolvedStorageSettings)
async def resolve_storage_settings(
    org_id: str,
    project_id: Optional[str] = None,
    service: StorageSettingsService = Depends(get_storage_settings_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    _ensure_role(current_user, org_id, project_id)
    resolved = await service.resolve_settings(org_id, project_id)
    return resolved


@router.get("/storage-settings/project/{project_id}", response_model=ProjectStorageSettings)
async def get_project_storage_settings_alias(
    project_id: str,
    org_id: Optional[str] = None,
    service: StorageSettingsService = Depends(get_storage_settings_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    return await get_project_storage_settings(project_id, org_id, service, current_user)


@router.put("/storage-settings/project/{project_id}", response_model=ProjectStorageSettings)
async def update_project_storage_settings_alias(
    project_id: str,
    payload: ProjectStorageSettings,
    request: Request,
    service: StorageSettingsService = Depends(get_storage_settings_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    await require_step_up(request, current_user, action="storage.settings.manage")
    return await update_project_storage_settings(project_id, payload, request, service, current_user)


@router.get("/storage-settings/resolve", response_model=ResolvedStorageSettings)
async def resolve_storage_settings_alias(
    org_id: str,
    project_id: Optional[str] = None,
    service: StorageSettingsService = Depends(get_storage_settings_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    return await resolve_storage_settings(org_id, project_id, service, current_user)
