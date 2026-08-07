from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status

from ..core.database import get_db
from ..core.permissions import Permissions
from ..core.security import CurrentUser, get_current_user
from ..models.smtp_settings import (
    SmtpSettingsResponse,
    SmtpSettingsTestResponse,
    SmtpSettingsUpdate,
    SmtpSettingsUpsert,
)
from ..services.smtp_settings_service import SmtpSettingsService
from ..services.policy_service import PolicyService
from ..services.step_up_service import require_step_up

router = APIRouter(prefix="/smtp-settings", tags=["smtp-settings"])


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="SMTP settings not found")


@router.get("/organization/{organization_id}", response_model=SmtpSettingsResponse)
async def get_organization_smtp_settings(
    organization_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    service = SmtpSettingsService(db)
    await PolicyService(db).authorize(current_user, Permissions.SETTINGS_SMTP_VIEW, organization_id=organization_id, resource_type="smtp_settings")
    settings = await service.get_organization_settings(organization_id)
    if not settings:
        raise _not_found()
    return settings


@router.post("/organization/{organization_id}", response_model=SmtpSettingsResponse)
async def create_organization_smtp_settings(
    organization_id: str,
    payload: SmtpSettingsUpsert,
    request: Request,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    await require_step_up(request, current_user, action="smtp.manage")
    service = SmtpSettingsService(db)
    await PolicyService(db).authorize(current_user, Permissions.SETTINGS_SMTP_MANAGE, organization_id=organization_id, resource_type="smtp_settings")
    return await service.upsert_organization_settings(organization_id, payload, current_user, authorization_checked=True)


@router.put("/organization/{organization_id}", response_model=SmtpSettingsResponse)
async def update_organization_smtp_settings(
    organization_id: str,
    payload: SmtpSettingsUpdate,
    request: Request,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    await require_step_up(request, current_user, action="smtp.manage")
    service = SmtpSettingsService(db)
    await PolicyService(db).authorize(current_user, Permissions.SETTINGS_SMTP_MANAGE, organization_id=organization_id, resource_type="smtp_settings")
    return await service.upsert_organization_settings(organization_id, payload, current_user, authorization_checked=True)


@router.post("/organization/{organization_id}/test", response_model=SmtpSettingsTestResponse)
async def test_organization_smtp_settings(
    organization_id: str,
    request: Request,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    await require_step_up(request, current_user, action="smtp.test")
    service = SmtpSettingsService(db)
    await PolicyService(db).authorize(current_user, Permissions.SETTINGS_SMTP_MANAGE, organization_id=organization_id, resource_type="smtp_settings_test")
    config = await service.runtime_for_organization(organization_id)
    if not config:
        raise _not_found()
    ok, message = await service.test_connection(config)
    return SmtpSettingsTestResponse(ok=ok, source=config.source, message=message)


@router.get("/project/{project_id}", response_model=SmtpSettingsResponse)
async def get_project_smtp_settings(
    project_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    service = SmtpSettingsService(db)
    organization_id = await service.get_project_org_id(project_id)
    await PolicyService(db).authorize(current_user, Permissions.SETTINGS_SMTP_VIEW, organization_id=organization_id, project_id=project_id, resource_type="smtp_settings")
    settings = await service.get_project_settings(project_id)
    if not settings:
        raise _not_found()
    return settings


@router.post("/project/{project_id}", response_model=SmtpSettingsResponse)
async def create_project_smtp_settings(
    project_id: str,
    payload: SmtpSettingsUpsert,
    request: Request,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    await require_step_up(request, current_user, action="smtp.manage")
    service = SmtpSettingsService(db)
    organization_id = await service.get_project_org_id(project_id)
    await PolicyService(db).authorize(current_user, Permissions.SETTINGS_SMTP_MANAGE, organization_id=organization_id, project_id=project_id, resource_type="smtp_settings")
    return await service.upsert_project_settings(project_id, payload, current_user, authorization_checked=True)


@router.put("/project/{project_id}", response_model=SmtpSettingsResponse)
async def update_project_smtp_settings(
    project_id: str,
    payload: SmtpSettingsUpdate,
    request: Request,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    await require_step_up(request, current_user, action="smtp.manage")
    service = SmtpSettingsService(db)
    organization_id = await service.get_project_org_id(project_id)
    await PolicyService(db).authorize(current_user, Permissions.SETTINGS_SMTP_MANAGE, organization_id=organization_id, project_id=project_id, resource_type="smtp_settings")
    return await service.upsert_project_settings(project_id, payload, current_user, authorization_checked=True)


@router.post("/project/{project_id}/test", response_model=SmtpSettingsTestResponse)
async def test_project_smtp_settings(
    project_id: str,
    request: Request,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    await require_step_up(request, current_user, action="smtp.test")
    service = SmtpSettingsService(db)
    organization_id = await service.get_project_org_id(project_id)
    await PolicyService(db).authorize(current_user, Permissions.SETTINGS_SMTP_MANAGE, organization_id=organization_id, project_id=project_id, resource_type="smtp_settings_test")
    config = await service.runtime_for_project(project_id)
    if not config:
        raise _not_found()
    ok, message = await service.test_connection(config)
    return SmtpSettingsTestResponse(ok=ok, source=config.source, message=message)
