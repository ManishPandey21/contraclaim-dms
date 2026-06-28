"""Contract Document Appraisal API (Contract Appraisal Report — v2 Phase 1).

Generation runs as a background job (inline asyncio fallback; the Mongo job doc is
the durable status the UI polls). Every endpoint is gated by PolicyService and
scoped by (organization_id, project_id); approved reports are locked and
regeneration always creates a new version. Endpoints live on the existing
``/api/contracts`` surface.
"""

from __future__ import annotations

import asyncio
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from ..core.database import get_db
from ..core.permissions import Permissions
from ..core.security import CurrentUser, build_scope_query, get_current_user
from ..models.contract_appraisal import (
    AppraisalJob,
    AppraisalReport,
    ClauseEntry,
    ContractKeyDate,
    ContractObligation,
    ContractRisk,
    DecisionRequest,
    GenerateAppraisalRequest,
    RegisterItemUpdate,
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
    # Generate-once: a live report already exists for this exact selection. The
    # caller should view/edit it, or delete it to regenerate.
    existing = await svc.find_existing_report(org, payload.project_id, payload.document_ids)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "An appraisal already exists for this selection. Open it, or delete it to regenerate.",
                "report_id": str(existing["_id"]),
            },
        )
    job = await svc.create_job(
        organization_id=org,
        project_id=payload.project_id,
        document_ids=payload.document_ids,
        current_user=current_user,
    )
    svc.schedule(job["_id"], current_user)
    return AppraisalJob(**job)


@router.get("/contracts/appraisal/existing", response_model=Optional[AppraisalReport])
async def existing_appraisal(
    organization_id: Optional[str] = Query(None),
    project_id: str = Query(...),
    document_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    """Return the live report for an (org, project, document) selection, or null."""
    org = organization_id or getattr(current_user, "organization_id", None)
    await policy.authorize(
        current_user,
        Permissions.CONTRACT_APPRAISAL_VIEW,
        resource_type="contract_appraisal",
        organization_id=org,
        project_id=project_id,
        audit=False,
    )
    document_ids = [document_id] if document_id else []
    report = await AppraisalService(db).find_existing_report(org, project_id, document_ids)
    return AppraisalReport(**report) if report else None


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


@router.delete("/contracts/appraisal/{report_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_appraisal(
    report_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    """Delete a report (and its registers/comments) so the selection can be
    generated again. Gated by the generate permission — whoever may regenerate
    may delete-to-regenerate."""
    report = await _load_report(report_id, Permissions.CONTRACT_APPRAISAL_GENERATE, db, current_user, policy)
    if report.get("is_locked"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Approved report is locked; use Regenerate to create a new version instead of deleting.",
        )
    await AppraisalService(db).delete_report(report, current_user)
    return None


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


@router.post("/contracts/appraisal/{report_id}/create-registers")
async def create_registers(
    report_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    report = await _load_report(report_id, Permissions.CONTRACT_APPRAISAL_CREATE_REGISTERS, db, current_user, policy)
    counts = await AppraisalService(db).create_registers(report, current_user)
    return {"report_id": report_id, "created": counts}


@router.get("/contracts/appraisal/{report_id}/export/pdf")
async def export_appraisal_pdf(
    report_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    report = await _load_report(report_id, Permissions.CONTRACT_APPRAISAL_EXPORT, db, current_user, policy)
    svc = AppraisalService(db)
    # reportlab is synchronous and can be slow for large reports — run it off the
    # event loop so it doesn't block other requests (M4).
    content = await asyncio.to_thread(svc.build_pdf, report)
    await svc._emit(report, "exported", current_user)
    filename = f"contract-appraisal-v{report.get('report_version', 1)}.pdf"
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- registers + clause library (Phase 2) ---------------------------------

_REGISTER_MODELS = {
    "obligations": ContractObligation,
    "risks": ContractRisk,
    "key_dates": ContractKeyDate,
}


async def _list_register(
    register: str, organization_id, project_id, report_id, db, current_user, policy
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
    rows = await AppraisalService(db).list_register(register, scope, report_id=report_id)
    model = _REGISTER_MODELS[register]
    return [model(**r) for r in rows]


async def _update_register(register: str, item_id, payload, db, current_user, policy):
    svc = AppraisalService(db)
    item = await svc.get_register_item(register, item_id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Register item not found")
    await policy.authorize_document(current_user, Permissions.CONTRACT_APPRAISAL_EDIT, item, resource_type=f"contract_{register}")
    updated = await svc.update_register_item(register, item_id, payload.model_dump(exclude_unset=True), current_user, before=item)
    return _REGISTER_MODELS[register](**(updated or item))


@router.get("/contracts/obligations", response_model=List[ContractObligation])
async def list_obligations(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    report_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    return await _list_register("obligations", organization_id, project_id, report_id, db, current_user, policy)


@router.put("/contracts/obligations/{item_id}", response_model=ContractObligation)
async def update_obligation(
    item_id: str,
    payload: RegisterItemUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    return await _update_register("obligations", item_id, payload, db, current_user, policy)


@router.get("/contracts/risks", response_model=List[ContractRisk])
async def list_risks(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    report_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    return await _list_register("risks", organization_id, project_id, report_id, db, current_user, policy)


@router.put("/contracts/risks/{item_id}", response_model=ContractRisk)
async def update_risk(
    item_id: str,
    payload: RegisterItemUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    return await _update_register("risks", item_id, payload, db, current_user, policy)


@router.get("/contracts/key-dates", response_model=List[ContractKeyDate])
async def list_key_dates(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    report_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    return await _list_register("key_dates", organization_id, project_id, report_id, db, current_user, policy)


@router.put("/contracts/key-dates/{item_id}", response_model=ContractKeyDate)
async def update_key_date(
    item_id: str,
    payload: RegisterItemUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    return await _update_register("key_dates", item_id, payload, db, current_user, policy)


@router.get("/contracts/clauses", response_model=List[ClauseEntry])
async def list_clauses(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user,
        Permissions.CONTRACT_APPRAISAL_VIEW,
        resource_type="contract_clauses",
        organization_id=organization_id,
        project_id=project_id,
        audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    clauses = await AppraisalService(db).list_clauses(scope, q=q, limit=limit)
    return [ClauseEntry(**c) for c in clauses]


@router.get("/contracts/appraisal/{report_id}/export/docx")
async def export_appraisal_docx(
    report_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    report = await _load_report(report_id, Permissions.CONTRACT_APPRAISAL_EXPORT, db, current_user, policy)
    svc = AppraisalService(db)
    # python-docx is synchronous — render off the event loop (M4).
    content = await asyncio.to_thread(svc.build_docx, report)
    await svc._emit(report, "exported", current_user)
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
