"""Contract Document Appraisal API (Contract Appraisal Report — v2 Phase 1).

Generation runs as a background job (inline asyncio fallback; the Mongo job doc is
the durable status the UI polls). Every endpoint is gated by PolicyService and
scoped by (organization_id, project_id); approved reports are locked and
regeneration always creates a new version. Endpoints live on the existing
``/api/contracts`` surface.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from ..core.database import get_db
from ..core.permissions import Permissions
from ..core.security import CurrentUser, build_scope_query, get_current_user
from ..models.contract_appraisal import (
    AppraisalJob,
    AppraisalReport,
    DecisionRequest,
    GenerateAppraisalRequest,
    ReportEditRequest,
    ReviewComment,
    ReviewCommentRequest,
)
from ..services.contract_appraisal.service import AppraisalService
from ..services.policy_service import PolicyService

router = APIRouter()


async def get_policy(db=Depends(get_db)) -> PolicyService:
    return PolicyService(db=db)


async def _load_report(report_id: str, permission: str, db, current_user, policy) -> dict:
    report = await AppraisalService(db).get_report(report_id)
    if not report:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Appraisal report not found")
    await policy.authorize_document(current_user, permission, report, resource_type="contract_appraisal_report")
    return report


# --- generation + jobs ----------------------------------------------------


@router.post("/contracts/appraisal/generate", response_model=AppraisalJob, status_code=status.HTTP_202_ACCEPTED)
async def generate_appraisal(
    payload: GenerateAppraisalRequest,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    org = payload.organization_id or getattr(current_user, "organization_id", None)
    await policy.authorize(
        current_user,
        Permissions.CONTRACT_APPRAISAL_GENERATE,
        resource_type="contract_appraisal",
        organization_id=org,
        project_id=payload.project_id,
    )
    svc = AppraisalService(db)
    job = await svc.create_job(
        organization_id=org,
        project_id=payload.project_id,
        document_ids=payload.document_ids,
        current_user=current_user,
    )
    svc.schedule(job["_id"], current_user)
    return AppraisalJob(**job)


@router.get("/contracts/appraisal/jobs/{job_id}", response_model=AppraisalJob)
async def get_appraisal_job(
    job_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    job = await AppraisalService(db).get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    await policy.authorize_document(current_user, Permissions.CONTRACT_APPRAISAL_VIEW, job, resource_type="contract_appraisal_job")
    return AppraisalJob(**job)


@router.post("/contracts/appraisal/jobs/{job_id}/cancel", response_model=AppraisalJob)
async def cancel_appraisal_job(
    job_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    job = await AppraisalService(db).get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    await policy.authorize_document(current_user, Permissions.CONTRACT_APPRAISAL_GENERATE, job, resource_type="contract_appraisal_job")
    updated = await AppraisalService(db).cancel_job(job_id)
    return AppraisalJob(**(updated or job))


# --- reports --------------------------------------------------------------


@router.get("/contracts/appraisal", response_model=List[AppraisalReport])
async def list_appraisals(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user,
        Permissions.CONTRACT_APPRAISAL_VIEW,
        resource_type="contract_appraisal",
        organization_id=organization_id,
        project_id=project_id,
        audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    reports = await AppraisalService(db).list_reports(scope)
    return [AppraisalReport(**r) for r in reports]


@router.get("/contracts/appraisal/{report_id}", response_model=AppraisalReport)
async def get_appraisal(
    report_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    report = await _load_report(report_id, Permissions.CONTRACT_APPRAISAL_VIEW, db, current_user, policy)
    return AppraisalReport(**report)


@router.put("/contracts/appraisal/{report_id}", response_model=AppraisalReport)
async def edit_appraisal(
    report_id: str,
    payload: ReportEditRequest,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    report = await _load_report(report_id, Permissions.CONTRACT_APPRAISAL_EDIT, db, current_user, policy)
    if report.get("is_locked"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Approved report is locked; regenerate to create a new version")
    updated = await AppraisalService(db).edit_report(report, payload.model_dump(exclude_unset=True), current_user)
    return AppraisalReport(**(updated or report))


@router.post("/contracts/appraisal/{report_id}/approve", response_model=AppraisalReport)
async def approve_appraisal(
    report_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    report = await _load_report(report_id, Permissions.CONTRACT_APPRAISAL_APPROVE, db, current_user, policy)
    updated = await AppraisalService(db).approve(report, current_user)
    return AppraisalReport(**(updated or report))


@router.post("/contracts/appraisal/{report_id}/reject", response_model=AppraisalReport)
async def reject_appraisal(
    report_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    report = await _load_report(report_id, Permissions.CONTRACT_APPRAISAL_REJECT, db, current_user, policy)
    updated = await AppraisalService(db).reject(report, current_user)
    return AppraisalReport(**(updated or report))


@router.post("/contracts/appraisal/{report_id}/regenerate", response_model=AppraisalJob, status_code=status.HTTP_202_ACCEPTED)
async def regenerate_appraisal(
    report_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    report = await _load_report(report_id, Permissions.CONTRACT_APPRAISAL_GENERATE, db, current_user, policy)
    svc = AppraisalService(db)
    job = await svc.regenerate(report, current_user)
    svc.schedule(job["_id"], current_user)
    return AppraisalJob(**job)


@router.get("/contracts/appraisal/{report_id}/citations")
async def get_appraisal_citations(
    report_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    report = await _load_report(report_id, Permissions.CONTRACT_APPRAISAL_VIEW, db, current_user, policy)
    return {"report_id": report_id, "citations": report.get("citations", [])}


@router.get("/contracts/appraisal/{report_id}/export/docx")
async def export_appraisal_docx(
    report_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    report = await _load_report(report_id, Permissions.CONTRACT_APPRAISAL_EXPORT, db, current_user, policy)
    content = AppraisalService(db).build_docx(report)
    await AppraisalService(db)._emit(report, "exported", current_user)
    filename = f"contract-appraisal-v{report.get('report_version', 1)}.docx"
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- review comments ------------------------------------------------------


@router.post("/contracts/appraisal/{report_id}/review-comments", response_model=ReviewComment, status_code=status.HTTP_201_CREATED)
async def add_review_comment(
    report_id: str,
    payload: ReviewCommentRequest,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    report = await _load_report(report_id, Permissions.CONTRACT_APPRAISAL_VIEW, db, current_user, policy)
    comment = await AppraisalService(db).add_comment(
        report, payload.comment_text, current_user, section_reference=payload.section_reference
    )
    return ReviewComment(**comment)


@router.get("/contracts/appraisal/{report_id}/review-comments", response_model=List[ReviewComment])
async def list_review_comments(
    report_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    report = await _load_report(report_id, Permissions.CONTRACT_APPRAISAL_VIEW, db, current_user, policy)
    comments = await AppraisalService(db).list_comments(report["_id"])
    return [ReviewComment(**c) for c in comments]
