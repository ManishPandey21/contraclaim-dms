"""Arbitration pleadings drafting API."""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field

from ..core.database import get_db
from ..core.permissions import Permissions
from ..core.security import CurrentUser, build_scope_query, get_current_user
from ..models.arbitration_drafting import (
    ArbitrationDraft,
    ArbitrationDraftCreate,
    ArbitrationDraftDetail,
    ArbitrationDraftUpdate,
    ArbitrationDraftVersion,
    ArbitrationEvidenceSearchRequest,
    ArbitrationEvidenceSearchResponse,
    ArbitrationGenerateRequest,
    ArbitrationGenerationRun,
    PleadingImportRequest,
    ReturnForRevisionRequest,
)
from ..services.arbitration_drafting import ArbitrationDraftingService
from ..services.policy_service import PolicyService

router = APIRouter(prefix="/arbitration", tags=["arbitration-drafting"])


class ManualVersionRequest(BaseModel):
    full_markdown: str = Field(..., min_length=1)


async def get_policy(db=Depends(get_db)) -> PolicyService:
    return PolicyService(db=db)


async def _load_and_authorize(draft_id: str, permission: str, db, current_user, policy) -> dict:
    draft = await ArbitrationDraftingService(db).repo.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Arbitration draft not found")
    await policy.authorize_document(current_user, permission, draft, resource_type="arbitration_draft")
    return draft


@router.get("/drafts", response_model=List[ArbitrationDraft])
async def list_arbitration_drafts(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    contract_id: Optional[str] = Query(None),
    draft_type: Optional[str] = Query(None),
    party_role: Optional[str] = Query(None),
    dispute_type: Optional[str] = Query(None),
    status_value: Optional[str] = Query(None, alias="status"),
    q: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=250),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user,
        Permissions.ARBITRATION_VIEW,
        resource_type="arbitration_draft",
        organization_id=organization_id,
        project_id=project_id,
        audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    filters = {
        "project_id": project_id,
        "contract_id": contract_id,
        "draft_type": draft_type,
        "party_role": party_role,
        "dispute_type": dispute_type,
        "status": status_value,
        "q": q,
    }
    return [ArbitrationDraft(**row) for row in await ArbitrationDraftingService(db).list_drafts(scope, filters, skip=skip, limit=limit)]


@router.post("/drafts", response_model=ArbitrationDraftDetail, status_code=status.HTTP_201_CREATED)
async def create_arbitration_draft(
    payload: ArbitrationDraftCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    org = payload.organization_id or getattr(current_user, "organization_id", None)
    await policy.authorize(
        current_user,
        Permissions.ARBITRATION_CREATE,
        resource_type="arbitration_draft",
        organization_id=org,
        project_id=payload.project_id,
    )
    return ArbitrationDraftDetail(**await ArbitrationDraftingService(db).create_draft(payload, current_user))


@router.get("/drafts/{draft_id}", response_model=ArbitrationDraftDetail)
async def get_arbitration_draft(
    draft_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize(draft_id, Permissions.ARBITRATION_VIEW, db, current_user, policy)
    return ArbitrationDraftDetail(**await ArbitrationDraftingService(db).detail(draft_id))


@router.patch("/drafts/{draft_id}", response_model=ArbitrationDraftDetail)
async def update_arbitration_draft(
    draft_id: str,
    payload: ArbitrationDraftUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize(draft_id, Permissions.ARBITRATION_EDIT, db, current_user, policy)
    return ArbitrationDraftDetail(**await ArbitrationDraftingService(db).update_draft(draft_id, payload, current_user))


@router.delete("/drafts/{draft_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_arbitration_draft(
    draft_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize(draft_id, Permissions.ARBITRATION_ADMIN, db, current_user, policy)
    await ArbitrationDraftingService(db).delete_draft(draft_id, current_user)
    return None


@router.post("/drafts/{draft_id}/evidence/search", response_model=ArbitrationEvidenceSearchResponse)
async def search_arbitration_evidence(
    draft_id: str,
    payload: ArbitrationEvidenceSearchRequest,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize(draft_id, Permissions.ARBITRATION_VIEW, db, current_user, policy)
    return ArbitrationEvidenceSearchResponse(**await ArbitrationDraftingService(db).evidence_search(draft_id, payload, current_user))


@router.post("/drafts/{draft_id}/evidence/refresh")
async def refresh_arbitration_source_ledger(
    draft_id: str,
    payload: ArbitrationGenerateRequest | None = None,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize(draft_id, Permissions.ARBITRATION_VIEW, db, current_user, policy)
    body = payload or ArbitrationGenerateRequest()
    return await ArbitrationDraftingService(db).refresh_source_ledger(
        draft_id,
        current_user,
        include_unverified_graph_links=body.include_unverified_graph_links,
    )


@router.get("/drafts/{draft_id}/source-ledger")
async def get_arbitration_source_ledger(
    draft_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize(draft_id, Permissions.ARBITRATION_VIEW, db, current_user, policy)
    return await ArbitrationDraftingService(db).refresh_source_ledger(draft_id, current_user)


@router.post("/drafts/{draft_id}/paragraph-responses/import-soc")
async def import_soc_paragraphs(
    draft_id: str,
    payload: PleadingImportRequest,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize(draft_id, Permissions.ARBITRATION_EDIT, db, current_user, policy)
    return await ArbitrationDraftingService(db).import_pleading_paragraphs(draft_id, payload, current_user)


@router.post("/drafts/{draft_id}/paragraph-responses/import-defence")
async def import_defence_paragraphs(
    draft_id: str,
    payload: PleadingImportRequest,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize(draft_id, Permissions.ARBITRATION_EDIT, db, current_user, policy)
    return await ArbitrationDraftingService(db).import_pleading_paragraphs(draft_id, payload, current_user)


@router.post("/drafts/{draft_id}/paragraph-responses/generate", response_model=ArbitrationDraftDetail)
async def generate_paragraph_responses(
    draft_id: str,
    payload: ArbitrationGenerateRequest,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize(draft_id, Permissions.ARBITRATION_GENERATE, db, current_user, policy)
    return ArbitrationDraftDetail(**await ArbitrationDraftingService(db).generate(draft_id, payload, current_user))


@router.post("/drafts/{draft_id}/generate", response_model=ArbitrationDraftDetail)
async def generate_arbitration_draft(
    draft_id: str,
    payload: ArbitrationGenerateRequest,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize(draft_id, Permissions.ARBITRATION_GENERATE, db, current_user, policy)
    return ArbitrationDraftDetail(**await ArbitrationDraftingService(db).generate(draft_id, payload, current_user))


@router.post("/drafts/{draft_id}/sections/{section_key}/regenerate", response_model=ArbitrationDraftDetail)
async def regenerate_arbitration_section(
    draft_id: str,
    section_key: str,
    payload: ArbitrationGenerateRequest | None = None,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize(draft_id, Permissions.ARBITRATION_GENERATE, db, current_user, policy)
    body = payload or ArbitrationGenerateRequest()
    body.section_key = section_key
    return ArbitrationDraftDetail(**await ArbitrationDraftingService(db).generate(draft_id, body, current_user))


@router.post("/drafts/{draft_id}/versions", response_model=ArbitrationDraftVersion, status_code=status.HTTP_201_CREATED)
async def save_arbitration_version(
    draft_id: str,
    payload: ManualVersionRequest,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize(draft_id, Permissions.ARBITRATION_EDIT, db, current_user, policy)
    return ArbitrationDraftVersion(**await ArbitrationDraftingService(db).create_manual_version(draft_id, payload.full_markdown, current_user))


@router.get("/drafts/{draft_id}/versions", response_model=List[ArbitrationDraftVersion])
async def list_arbitration_versions(
    draft_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize(draft_id, Permissions.ARBITRATION_VIEW, db, current_user, policy)
    return [ArbitrationDraftVersion(**row) for row in await ArbitrationDraftingService(db).list_versions(draft_id)]


@router.get("/drafts/{draft_id}/versions/{version}", response_model=ArbitrationDraftVersion)
async def get_arbitration_version(
    draft_id: str,
    version: int,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize(draft_id, Permissions.ARBITRATION_VIEW, db, current_user, policy)
    return ArbitrationDraftVersion(**await ArbitrationDraftingService(db).get_version(draft_id, version))


@router.post("/drafts/{draft_id}/approve", response_model=ArbitrationDraftDetail)
async def approve_arbitration_draft(
    draft_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize(draft_id, Permissions.ARBITRATION_APPROVE, db, current_user, policy)
    return ArbitrationDraftDetail(**await ArbitrationDraftingService(db).approve(draft_id, current_user))


@router.post("/drafts/{draft_id}/return-for-revision", response_model=ArbitrationDraftDetail)
async def return_arbitration_for_revision(
    draft_id: str,
    payload: ReturnForRevisionRequest,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize(draft_id, Permissions.ARBITRATION_APPROVE, db, current_user, policy)
    return ArbitrationDraftDetail(**await ArbitrationDraftingService(db).return_for_revision(draft_id, payload.reason, current_user))


@router.get("/drafts/{draft_id}/runs/{run_id}", response_model=ArbitrationGenerationRun)
async def get_arbitration_run(
    draft_id: str,
    run_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize(draft_id, Permissions.ARBITRATION_VIEW, db, current_user, policy)
    return ArbitrationGenerationRun(**await ArbitrationDraftingService(db).get_run(draft_id, run_id))


@router.get("/drafts/{draft_id}/audit")
async def get_arbitration_audit(
    draft_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    draft = await _load_and_authorize(draft_id, Permissions.ARBITRATION_AUDIT, db, current_user, policy)
    cursor = db.audit_events.find({"resource_type": "arbitration_draft", "resource_id": str(draft.get("_id"))}).sort("created_at", -1)
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=500)
    return [row async for row in cursor]


@router.get("/drafts/{draft_id}/export/docx")
async def export_arbitration_docx(
    draft_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize(draft_id, Permissions.ARBITRATION_EXPORT, db, current_user, policy)
    content = await ArbitrationDraftingService(db).export(draft_id, "docx", current_user)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": 'attachment; filename="arbitration-pleading.docx"'},
    )


@router.get("/drafts/{draft_id}/export/pdf")
async def export_arbitration_pdf(
    draft_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize(draft_id, Permissions.ARBITRATION_EXPORT, db, current_user, policy)
    content = await ArbitrationDraftingService(db).export(draft_id, "pdf", current_user)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="arbitration-pleading.pdf"'},
    )
