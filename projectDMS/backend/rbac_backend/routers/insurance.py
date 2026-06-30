"""Insurance Register API. PolicyService-gated, tenant-scoped, audited.

Mirrors the Bank Guarantee router: list / summary / alerts / export, full CRUD,
file replace, and the admin-managed Insurance Type master.
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Body, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse

from ..core.config import settings
from ..core.database import get_db
from ..core.permissions import Permissions
from ..core.security import CurrentUser, build_scope_query, get_current_user
from ..models.insurance import (
    Insurance,
    InsuranceCreate,
    InsuranceSummary,
    InsuranceType,
    InsuranceTypeCreate,
    InsuranceTypeUpdate,
    InsuranceUpdate,
)
from ..services.antivirus_service import AntivirusService
from ..services.insurance_service import InsuranceService, InsuranceTypeService
from ..services.policy_service import PolicyService

router = APIRouter()

# Inline policy-file upload: PDF / JPG / PNG, max 20 MB. Stored under the
# backed-up uploads volume and served back through GET /insurance/{id}/file.
_ALLOWED_EXT = {".pdf", ".jpg", ".jpeg", ".png"}
_CONTENT_TYPES = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
_MAX_UPLOAD_BYTES = 20 * 1024 * 1024


def _insurance_dir() -> Path:
    directory = Path(settings.UPLOADS_DIR) / "insurance"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _insurance_file_path(token: Optional[str]) -> Path:
    """Resolve a stored token to a path, guarding against path traversal."""
    if not token or "/" in token or "\\" in token or ".." in token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid file reference")
    base = _insurance_dir().resolve()
    path = (base / token).resolve()
    if path.parent != base:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid file reference")
    return path


async def get_policy(db=Depends(get_db)) -> PolicyService:
    return PolicyService(db=db)


async def _load(insurance_id: str, permission: str, db, current_user, policy) -> dict:
    ins = await InsuranceService(db).get(insurance_id)
    if not ins:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Insurance policy not found")
    await policy.authorize_document(current_user, permission, ins, resource_type="insurance")
    return ins


# --- register --------------------------------------------------------------


@router.get("/insurance", response_model=List[Insurance])
async def list_insurance(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    contract_id: Optional[str] = Query(None),
    insurance_type: Optional[str] = Query(None, alias="type"),
    insurance_company: Optional[str] = Query(None, alias="company"),
    status_filter: Optional[str] = Query(None, alias="status"),
    uploaded_by: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=2000),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user, Permissions.INSURANCE_VIEW, resource_type="insurance",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id, audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    items = await InsuranceService(db).list(
        scope, project_id=project_id, contract_id=contract_id, insurance_type=insurance_type,
        insurance_company=insurance_company, status=status_filter, uploaded_by=uploaded_by,
        q=q, skip=skip, limit=limit,
    )
    return [Insurance(**i) for i in items]


@router.get("/insurance/summary", response_model=InsuranceSummary)
async def insurance_summary(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user, Permissions.INSURANCE_VIEW, resource_type="insurance",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id, audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    return InsuranceSummary(**await InsuranceService(db).summary(scope, project_id=project_id))


@router.get("/insurance/alerts", response_model=List[Insurance])
async def insurance_alerts(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user, Permissions.INSURANCE_VIEW, resource_type="insurance",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id, audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    return [Insurance(**i) for i in await InsuranceService(db).alerts(scope)]


@router.get("/insurance/export")
async def export_insurance(
    format: str = Query("csv", pattern="^(csv|xlsx|pdf)$"),
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user, Permissions.INSURANCE_EXPORT, resource_type="insurance",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id, audit=False,
    )
    from ..services import contract_controls_export as cx

    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    rows = await InsuranceService(db).list(scope, project_id=project_id, limit=5000)
    return cx.export_response("insurance-register", cx.INSURANCE_COLUMNS, rows, format)


# --- insurance type master (admin-managed) ---------------------------------


@router.get("/insurance/types", response_model=List[InsuranceType])
async def list_insurance_types(
    organization_id: Optional[str] = Query(None),
    include_inactive: bool = Query(False),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user, Permissions.INSURANCE_VIEW, resource_type="insurance",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        audit=False,
    )
    org = organization_id or getattr(current_user, "organization_id", None)
    return [InsuranceType(**t) for t in await InsuranceTypeService(db).list(org, include_inactive=include_inactive)]


@router.post("/insurance/types", response_model=InsuranceType, status_code=status.HTTP_201_CREATED)
async def create_insurance_type(
    payload: InsuranceTypeCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    org = payload.organization_id or getattr(current_user, "organization_id", None)
    await policy.authorize(
        current_user, Permissions.INSURANCE_MANAGE_TYPES, resource_type="insurance",
        organization_id=org,
    )
    return InsuranceType(**await InsuranceTypeService(db).create(payload, current_user))


@router.put("/insurance/types/{type_id}", response_model=InsuranceType)
async def update_insurance_type(
    type_id: str,
    payload: InsuranceTypeUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    service = InsuranceTypeService(db)
    existing = await service.get(type_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Insurance type not found")
    await policy.authorize(
        current_user, Permissions.INSURANCE_MANAGE_TYPES, resource_type="insurance",
        organization_id=existing.get("organization_id"),
    )
    return InsuranceType(**(await service.update(existing, payload.model_dump(exclude_unset=True), current_user) or existing))


@router.delete("/insurance/types/{type_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_insurance_type(
    type_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    service = InsuranceTypeService(db)
    existing = await service.get(type_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Insurance type not found")
    await policy.authorize(
        current_user, Permissions.INSURANCE_MANAGE_TYPES, resource_type="insurance",
        organization_id=existing.get("organization_id"),
    )
    await service.delete(existing, current_user)
    return None


# --- CRUD ------------------------------------------------------------------


@router.post("/insurance", response_model=Insurance, status_code=status.HTTP_201_CREATED)
async def create_insurance(
    payload: InsuranceCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    org = payload.organization_id or getattr(current_user, "organization_id", None)
    await policy.authorize(
        current_user, Permissions.INSURANCE_CREATE, resource_type="insurance",
        organization_id=org, project_id=payload.project_id,
    )
    return Insurance(**await InsuranceService(db).create(payload, current_user))


@router.post("/insurance/upload")
async def upload_insurance_file(
    file: UploadFile = File(...),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    """Validate + virus-scan + store a policy file; returns the token to attach.

    The caller persists the returned ``document_id`` (+ name/content type) on the
    insurance record via create / update / replace-file.
    """
    await policy.authorize(
        current_user, Permissions.INSURANCE_CREATE, resource_type="insurance",
        organization_id=getattr(current_user, "organization_id", None),
    )
    original_name = file.filename or "policy"
    ext = os.path.splitext(original_name)[1].lower()
    if ext not in _ALLOWED_EXT:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only PDF, JPG, or PNG files are allowed")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="The uploaded file is empty")
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File exceeds the 20 MB limit")

    token = f"{uuid.uuid4().hex}{ext}"
    path = _insurance_dir() / token
    path.write_bytes(content)

    # Antivirus: fail-closed in production (enforced by config validation).
    if settings.ANTIVIRUS_ENABLED:
        is_clean, detail = await AntivirusService().scan_file(path)
        if not is_clean:
            try:
                path.unlink()
            except OSError:
                pass
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Antivirus scan rejected this file: {detail}")

    return {
        "document_id": token,
        "document_name": original_name,
        "content_type": file.content_type or _CONTENT_TYPES.get(ext, "application/octet-stream"),
    }


@router.get("/insurance/{insurance_id}", response_model=Insurance)
async def get_insurance(
    insurance_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    return Insurance(**await _load(insurance_id, Permissions.INSURANCE_VIEW, db, current_user, policy))


@router.get("/insurance/{insurance_id}/file")
async def get_insurance_file(
    insurance_id: str,
    download: bool = Query(False),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    """Stream the policy file for inline preview (or attachment when download=true)."""
    ins = await _load(insurance_id, Permissions.INSURANCE_VIEW, db, current_user, policy)
    token = ins.get("document_id")
    path = _insurance_file_path(token)
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No file attached")
    media_type = ins.get("document_content_type") or _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
    filename = ins.get("document_name") or token
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        content_disposition_type="attachment" if download else "inline",
    )


@router.put("/insurance/{insurance_id}", response_model=Insurance)
async def update_insurance(
    insurance_id: str,
    payload: InsuranceUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    ins = await _load(insurance_id, Permissions.INSURANCE_EDIT, db, current_user, policy)
    updated = await InsuranceService(db).update(ins, payload.model_dump(exclude_unset=True), current_user)
    return Insurance(**(updated or ins))


@router.post("/insurance/{insurance_id}/replace-file", response_model=Insurance)
async def replace_insurance_file(
    insurance_id: str,
    document_id: str = Body(..., embed=True),
    document_name: Optional[str] = Body(None, embed=True),
    document_content_type: Optional[str] = Body(None, embed=True),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    ins = await _load(insurance_id, Permissions.INSURANCE_EDIT, db, current_user, policy)
    return Insurance(**await InsuranceService(db).replace_file(
        ins, document_id, current_user,
        document_name=document_name, document_content_type=document_content_type,
    ))


@router.delete("/insurance/{insurance_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_insurance(
    insurance_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    ins = await _load(insurance_id, Permissions.INSURANCE_DELETE, db, current_user, policy)
    await InsuranceService(db).delete(ins, current_user)
    return None
