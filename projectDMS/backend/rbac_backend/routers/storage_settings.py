from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..core.permissions import Permissions
from ..core.security import get_current_user, CurrentUser
from ..models.storage_settings import (
    OrganizationStorageSettings,
    ProjectStorageSettings,
    ResolvedStorageSettings,
)
from ..services.storage_settings_service import StorageSettingsService
from ..services.policy_service import PolicyService
from ..services.step_up_service import require_step_up

logger = logging.getLogger(__name__)

router = APIRouter()


async def get_storage_settings_service() -> StorageSettingsService:
    return StorageSettingsService()


async def get_policy_service() -> PolicyService:
    return PolicyService()


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


@router.get("/settings/storage/org/{org_id}", response_model=OrganizationStorageSettings)
async def get_org_storage_settings(
    org_id: str,
    service: StorageSettingsService = Depends(get_storage_settings_service),
    policy: PolicyService = Depends(get_policy_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    await policy.authorize(
        current_user,
        Permissions.SETTINGS_STORAGE_VIEW,
        organization_id=org_id,
        resource_type="organization_storage_settings",
        resource_id=org_id,
    )
    try:
        settings = await service.get_org_settings(org_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
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
    policy: PolicyService = Depends(get_policy_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    await require_step_up(request, current_user, action="storage.settings.manage")
    await policy.authorize(
        current_user,
        Permissions.SETTINGS_STORAGE_MANAGE,
        organization_id=org_id,
        resource_type="organization_storage_settings",
        resource_id=org_id,
    )
    _validate_short_name(payload.org_short_name)
    try:
        saved = await service.upsert_org_settings(org_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return saved


# Compatibility aliases for older client paths
@router.get("/storage-settings/org/{org_id}", response_model=OrganizationStorageSettings, deprecated=True)
async def get_org_storage_settings_alias(
    org_id: str,
    service: StorageSettingsService = Depends(get_storage_settings_service),
    policy: PolicyService = Depends(get_policy_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    logger.warning("Deprecated storage settings alias used", extra={"route_alias": "storage-settings/org", "actor_id": current_user.id})
    return await get_org_storage_settings(org_id, service, policy, current_user)


@router.put("/storage-settings/org/{org_id}", response_model=OrganizationStorageSettings, deprecated=True)
async def update_org_storage_settings_alias(
    org_id: str,
    payload: OrganizationStorageSettings,
    request: Request,
    service: StorageSettingsService = Depends(get_storage_settings_service),
    policy: PolicyService = Depends(get_policy_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    logger.warning("Deprecated storage settings alias used", extra={"route_alias": "storage-settings/org", "actor_id": current_user.id})
    return await update_org_storage_settings(org_id, payload, request, service, policy, current_user)


@router.get("/settings/storage/project/{project_id}", response_model=ProjectStorageSettings)
async def get_project_storage_settings(
    project_id: str,
    org_id: Optional[str] = None,
    service: StorageSettingsService = Depends(get_storage_settings_service),
    policy: PolicyService = Depends(get_policy_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    authoritative_org_id = await service.get_project_organization_id(project_id)
    if not authoritative_org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if org_id and str(org_id) != str(authoritative_org_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Project does not belong to organization")
    await policy.authorize(
        current_user,
        Permissions.SETTINGS_STORAGE_VIEW,
        organization_id=authoritative_org_id,
        project_id=project_id,
        resource_type="project_storage_settings",
        resource_id=project_id,
    )
    try:
        settings = await service.get_project_settings(project_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not settings:
        return ProjectStorageSettings(
            project_id=project_id,
            org_id=authoritative_org_id,
            inherit_from_org=True,
        )
    return settings


@router.put("/settings/storage/project/{project_id}", response_model=ProjectStorageSettings)
async def update_project_storage_settings(
    project_id: str,
    payload: ProjectStorageSettings,
    request: Request,
    service: StorageSettingsService = Depends(get_storage_settings_service),
    policy: PolicyService = Depends(get_policy_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    await require_step_up(request, current_user, action="storage.settings.manage")
    org_id = await service.get_project_organization_id(project_id)
    if not org_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if payload.org_id and str(payload.org_id) != str(org_id):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Project does not belong to organization")
    await policy.authorize(
        current_user,
        Permissions.SETTINGS_STORAGE_MANAGE,
        organization_id=org_id,
        project_id=project_id,
        resource_type="project_storage_settings",
        resource_id=project_id,
    )
    _validate_short_name(payload.project_short_name)
    try:
        saved = await service.upsert_project_settings(project_id, org_id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return saved


@router.get("/settings/storage/resolve", response_model=ResolvedStorageSettings)
async def resolve_storage_settings(
    org_id: str,
    project_id: Optional[str] = None,
    service: StorageSettingsService = Depends(get_storage_settings_service),
    policy: PolicyService = Depends(get_policy_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    if project_id:
        authoritative_org_id = await service.get_project_organization_id(project_id)
        if not authoritative_org_id or str(authoritative_org_id) != str(org_id):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Project does not belong to organization")
    await policy.authorize(
        current_user,
        Permissions.SETTINGS_STORAGE_VIEW,
        organization_id=org_id,
        project_id=project_id,
        resource_type="resolved_storage_settings",
    )
    try:
        resolved = await service.resolve_settings(org_id, project_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return resolved


@router.get("/storage-settings/project/{project_id}", response_model=ProjectStorageSettings, deprecated=True)
async def get_project_storage_settings_alias(
    project_id: str,
    org_id: Optional[str] = None,
    service: StorageSettingsService = Depends(get_storage_settings_service),
    policy: PolicyService = Depends(get_policy_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    logger.warning("Deprecated storage settings alias used", extra={"route_alias": "storage-settings/project", "actor_id": current_user.id})
    return await get_project_storage_settings(project_id, org_id, service, policy, current_user)


@router.put("/storage-settings/project/{project_id}", response_model=ProjectStorageSettings, deprecated=True)
async def update_project_storage_settings_alias(
    project_id: str,
    payload: ProjectStorageSettings,
    request: Request,
    service: StorageSettingsService = Depends(get_storage_settings_service),
    policy: PolicyService = Depends(get_policy_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    logger.warning("Deprecated storage settings alias used", extra={"route_alias": "storage-settings/project", "actor_id": current_user.id})
    return await update_project_storage_settings(project_id, payload, request, service, policy, current_user)


@router.get("/storage-settings/resolve", response_model=ResolvedStorageSettings, deprecated=True)
async def resolve_storage_settings_alias(
    org_id: str,
    project_id: Optional[str] = None,
    service: StorageSettingsService = Depends(get_storage_settings_service),
    policy: PolicyService = Depends(get_policy_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    logger.warning("Deprecated storage settings alias used", extra={"route_alias": "storage-settings/resolve", "actor_id": current_user.id})
    return await resolve_storage_settings(org_id, project_id, service, policy, current_user)
