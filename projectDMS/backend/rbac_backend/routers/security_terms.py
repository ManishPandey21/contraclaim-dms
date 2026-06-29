from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from ..core.database import get_db
from ..core.security import CurrentUser, get_current_user
from ..models.security_terms import (
    SecurityTermsAcceptance,
    SecurityTermsAcceptRequest,
    SecurityTermsAcceptancesResponse,
    SecurityTermsStatus,
    SecurityTermsVersion,
    SecurityTermsVersionCreate,
)
from ..services.policy_service import PolicyService
from ..services.security_terms_service import SecurityTermsService

router = APIRouter(prefix="/security-terms")


async def _require_terms_admin(current_user: CurrentUser, db) -> None:
    roles = {str(role).lower() for role in (getattr(current_user, "roles", []) or [])}
    if "superadmin" in roles:
        return
    if await PolicyService(db=db).has_permission(current_user, "settings:edit"):
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Terms administration requires settings edit access")


def _client_ip(request: Request) -> Optional[str]:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",", 1)[0].strip()
        if first:
            return first
    return request.client.host if request.client else None


@router.get("/status", response_model=SecurityTermsStatus)
async def security_terms_status(
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    return await SecurityTermsService(db).status_for_user(current_user)


@router.post("/accept", response_model=SecurityTermsAcceptance)
async def accept_security_terms(
    payload: SecurityTermsAcceptRequest,
    request: Request,
    user_agent: Optional[str] = Header(None, alias="User-Agent"),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    return await SecurityTermsService(db).accept_active(
        current_user,
        payload,
        ip_address=_client_ip(request),
        user_agent=user_agent,
    )


@router.get("/acceptances", response_model=SecurityTermsAcceptancesResponse)
async def list_my_security_terms_acceptances(
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    acceptances = await SecurityTermsService(db).list_acceptances_for_user(current_user)
    return SecurityTermsAcceptancesResponse(acceptances=[SecurityTermsAcceptance(**item) for item in acceptances])


@router.get("/versions", response_model=List[SecurityTermsVersion])
async def list_security_terms_versions(
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    await _require_terms_admin(current_user, db)
    return [SecurityTermsVersion(**item) for item in await SecurityTermsService(db).list_versions()]


@router.post("/versions", response_model=SecurityTermsVersion, status_code=status.HTTP_201_CREATED)
async def create_security_terms_version(
    payload: SecurityTermsVersionCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    await _require_terms_admin(current_user, db)
    return SecurityTermsVersion(**await SecurityTermsService(db).create_version(payload, current_user))


@router.post("/versions/{version_id}/activate", response_model=SecurityTermsVersion)
async def activate_security_terms_version(
    version_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    await _require_terms_admin(current_user, db)
    return SecurityTermsVersion(**await SecurityTermsService(db).activate_version(version_id, current_user))
