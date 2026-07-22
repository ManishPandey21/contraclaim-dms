"""Arbitration pleadings drafting API."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field

from ..core.database import get_db
from ..core.permissions import Permissions
from ..core.security import CurrentUser, build_scope_query, get_current_user
from ..models.arbitration_drafting import (
    ArbitrationAgentRun,
    ArbitrationAgentRunRequest,
    ArbitrationBundleExport,
    ArbitrationBundleExportRequest,
    ArbitrationCase,
    ArbitrationCaseCreate,
    ArbitrationCaseUpdate,
    ArbitrationDraft,
    ArbitrationDraftCreate,
    ArbitrationDraftDetail,
    ArbitrationDraftUpdate,
    ArbitrationDraftVersion,
    ArbitrationEvidenceSearchRequest,
    ArbitrationEvidenceSearchResponse,
    ArbitrationGenerateRequest,
    ArbitrationGenerationRun,
    GenerationRunType,
    ArbitrationMatrixReviewRequest,
    ArbitrationMatrixRow,
    ArbitrationMatrixRowCreate,
    ArbitrationMatrixRowUpdate,
    ArbitrationReadinessResponse,
    ArbitrationReadinessApprovalRequest,
    ArbitrationRejoinderPermissionRequest,
    ArbitrationSelectedReferenceCreate,
    PleadingImportRequest,
    ReturnForRevisionRequest,
    ArbitrationWorkflowAccepted,
    ArbitrationWorkflowApprovalRequest,
    ArbitrationWorkflowCancelRequest,
    ArbitrationWorkflowCreateRequest,
    ArbitrationWorkflowFallbackRequest,
    ArbitrationWorkflowResumeRequest,
    ArbitrationWorkflowStateResponse,
    ArbitrationParagraphResponse,
    ArbitrationParagraphResponseUpdate,
)
from ..services.arbitration_drafting import ArbitrationCaseWorkspaceService, ArbitrationDraftingService
from ..services.arbitration_drafting.case_workspace import MATRIX_COLLECTIONS
from ..services.policy_service import PolicyService
from ..services.step_up_service import require_step_up
from ..services.arbitration_drafting.workflow_service import ArbitrationWorkflowService

router = APIRouter(prefix="/arbitration", tags=["arbitration-drafting"])


class ManualVersionRequest(BaseModel):
    full_markdown: str = Field(..., min_length=1)


class AddReferencesRequest(BaseModel):
    references: List[ArbitrationSelectedReferenceCreate] = Field(..., min_length=1)


async def get_policy(db=Depends(get_db)) -> PolicyService:
    return PolicyService(db=db)


async def _load_and_authorize(draft_id: str, permission: str, db, current_user, policy) -> dict:
    draft = await ArbitrationDraftingService(db).repo.get_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Arbitration draft not found")
    await policy.authorize_document(current_user, permission, draft, resource_type="arbitration_draft")
    return draft


async def _load_case_and_authorize(case_id: str, permission: str, db, current_user, policy) -> Dict[str, Any]:
    # Tenant scope on the lookup itself (cross-tenant ids 404 without leaking
    # existence); the policy check below still enforces permission + scope.
    case = await ArbitrationCaseWorkspaceService(db).get_case(case_id, scope=build_scope_query(current_user))
    await policy.authorize_document(current_user, permission, case, resource_type="arbitration_case")
    return case


@router.get("/cases", response_model=List[ArbitrationCase])
async def list_arbitration_cases(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    contract_id: Optional[str] = Query(None),
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
        resource_type="arbitration_case",
        organization_id=organization_id,
        project_id=project_id,
        audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    filters = {"project_id": project_id, "contract_id": contract_id, "status": status_value, "q": q}
    return [
        ArbitrationCase(**row)
        for row in await ArbitrationCaseWorkspaceService(db).list_cases(scope, filters, skip=skip, limit=limit)
    ]


@router.post("/cases", response_model=ArbitrationCase, status_code=status.HTTP_201_CREATED)
async def create_arbitration_case(
    payload: ArbitrationCaseCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    org = payload.organization_id or getattr(current_user, "organization_id", None)
    await policy.authorize(
        current_user,
        Permissions.ARBITRATION_CREATE,
        resource_type="arbitration_case",
        organization_id=org,
        project_id=payload.project_id,
    )
    return ArbitrationCase(**await ArbitrationCaseWorkspaceService(db).create_case(payload, current_user))


@router.get("/cases/{case_id}", response_model=ArbitrationCase)
async def get_arbitration_case(
    case_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    case = await _load_case_and_authorize(case_id, Permissions.ARBITRATION_VIEW, db, current_user, policy)
    return ArbitrationCase(**case)


@router.patch("/cases/{case_id}", response_model=ArbitrationCase)
async def update_arbitration_case(
    case_id: str,
    payload: ArbitrationCaseUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_case_and_authorize(case_id, Permissions.ARBITRATION_EDIT, db, current_user, policy)
    return ArbitrationCase(**await ArbitrationCaseWorkspaceService(db).update_case(case_id, payload, current_user))


@router.get("/cases/{case_id}/dashboard")
async def get_arbitration_case_dashboard(
    case_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_case_and_authorize(case_id, Permissions.ARBITRATION_VIEW, db, current_user, policy)
    return await ArbitrationCaseWorkspaceService(db).dashboard(case_id)


@router.get("/cases/{case_id}/readiness", response_model=ArbitrationReadinessResponse)
async def get_arbitration_case_readiness(
    case_id: str,
    draft_id: Optional[str] = Query(None),
    draft_type: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_case_and_authorize(case_id, Permissions.ARBITRATION_VIEW, db, current_user, policy)
    return ArbitrationReadinessResponse(
        **await ArbitrationCaseWorkspaceService(db).readiness(case_id, draft_id=draft_id, draft_type=draft_type)
    )


@router.post("/cases/{case_id}/approve-readiness")
async def approve_arbitration_case_readiness(
    case_id: str,
    payload: ArbitrationReadinessApprovalRequest | None = None,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_case_and_authorize(case_id, Permissions.ARBITRATION_APPROVE, db, current_user, policy)
    return await ArbitrationCaseWorkspaceService(db).approve_readiness(case_id, current_user, payload)


@router.post(
    "/cases/{case_id}/workflows",
    response_model=ArbitrationWorkflowAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_arbitration_workflow(
    case_id: str,
    payload: ArbitrationWorkflowCreateRequest,
    idempotency_key: Optional[str] = Header(None, alias="Idempotency-Key"),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_case_and_authorize(case_id, Permissions.ARBITRATION_GENERATE, db, current_user, policy)
    return ArbitrationWorkflowAccepted(
        **await ArbitrationWorkflowService(db).create(
            case_id, payload, current_user, idempotency_key=idempotency_key
        )
    )


@router.get("/cases/{case_id}/workflows/{run_id}/state", response_model=ArbitrationWorkflowStateResponse)
async def get_arbitration_workflow_state(
    case_id: str,
    run_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_case_and_authorize(case_id, Permissions.ARBITRATION_VIEW, db, current_user, policy)
    return ArbitrationWorkflowStateResponse(**await ArbitrationWorkflowService(db).get(case_id, run_id))


@router.get("/cases/{case_id}/workflows/{run_id}/events")
async def get_arbitration_workflow_events(
    case_id: str,
    run_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_case_and_authorize(case_id, Permissions.ARBITRATION_VIEW, db, current_user, policy)
    return await ArbitrationWorkflowService(db).events(case_id, run_id)


@router.post("/cases/{case_id}/workflows/{run_id}/resume", response_model=ArbitrationWorkflowStateResponse)
async def resume_arbitration_workflow(
    case_id: str,
    run_id: str,
    payload: ArbitrationWorkflowResumeRequest,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_case_and_authorize(case_id, Permissions.ARBITRATION_EDIT, db, current_user, policy)
    return ArbitrationWorkflowStateResponse(**await ArbitrationWorkflowService(db).resume(case_id, run_id, payload, current_user))


@router.post("/cases/{case_id}/workflows/{run_id}/cancel", response_model=ArbitrationWorkflowStateResponse)
async def cancel_arbitration_workflow(
    case_id: str,
    run_id: str,
    payload: ArbitrationWorkflowCancelRequest,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_case_and_authorize(case_id, Permissions.ARBITRATION_EDIT, db, current_user, policy)
    return ArbitrationWorkflowStateResponse(**await ArbitrationWorkflowService(db).cancel(case_id, run_id, payload, current_user))


@router.post("/cases/{case_id}/workflows/{run_id}/approvals/{gate}", response_model=ArbitrationWorkflowStateResponse)
async def approve_arbitration_workflow_gate(
    case_id: str,
    run_id: str,
    gate: str,
    payload: ArbitrationWorkflowApprovalRequest,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    permission = Permissions.ARBITRATION_EXPORT if gate == "export" else Permissions.ARBITRATION_APPROVE
    await _load_case_and_authorize(case_id, permission, db, current_user, policy)
    return ArbitrationWorkflowStateResponse(
        **await ArbitrationWorkflowService(db).approve_gate(case_id, run_id, gate, payload, current_user)
    )


@router.post("/cases/{case_id}/workflows/{run_id}/force-v2", response_model=ArbitrationWorkflowStateResponse)
async def force_v2_arbitration_workflow(
    case_id: str,
    run_id: str,
    payload: ArbitrationWorkflowFallbackRequest,
    request: Request,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_case_and_authorize(case_id, Permissions.ARBITRATION_ADMIN, db, current_user, policy)
    await require_step_up(request, current_user, action="arbitration.workflow.force_v2")
    return ArbitrationWorkflowStateResponse(**await ArbitrationWorkflowService(db).fallback(case_id, run_id, payload, current_user))


@router.get("/cases/{case_id}/workflows/operations/health")
async def get_arbitration_workflow_operational_health(
    case_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_case_and_authorize(case_id, Permissions.ARBITRATION_ADMIN, db, current_user, policy)
    return await ArbitrationWorkflowService(db).operations_health(case_id)


@router.get("/operations/workflows/{run_id}/checkpoints")
async def get_redacted_arbitration_workflow_checkpoints(
    run_id: str,
    limit: int = 50,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    service = ArbitrationWorkflowService(db)
    run = await service.repository.get_run(run_id)
    if not run:
        raise HTTPException(status_code=404, detail="Arbitration workflow run not found")
    await _load_case_and_authorize(str(run.get("case_id")), Permissions.ARBITRATION_ADMIN, db, current_user, policy)
    return await service.checkpoints(run_id, limit=limit)


@router.post("/cases/{case_id}/agents/{agent_type}/run", response_model=ArbitrationAgentRun)
async def run_arbitration_case_agent(
    case_id: str,
    agent_type: str,
    payload: ArbitrationAgentRunRequest,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_case_and_authorize(case_id, Permissions.ARBITRATION_GENERATE, db, current_user, policy)
    return ArbitrationAgentRun(**await ArbitrationCaseWorkspaceService(db).run_agent(case_id, agent_type, payload, current_user))


@router.post("/cases/{case_id}/agents/{agent_type}/queue", response_model=ArbitrationAgentRun, status_code=status.HTTP_202_ACCEPTED)
async def queue_arbitration_case_agent(
    case_id: str,
    agent_type: str,
    payload: ArbitrationAgentRunRequest,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_case_and_authorize(case_id, Permissions.ARBITRATION_GENERATE, db, current_user, policy)
    return ArbitrationAgentRun(**await ArbitrationCaseWorkspaceService(db).queue_agent_run(case_id, agent_type, payload, current_user))


@router.get("/cases/{case_id}/agent-runs", response_model=List[ArbitrationAgentRun])
async def list_arbitration_case_agent_runs(
    case_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_case_and_authorize(case_id, Permissions.ARBITRATION_VIEW, db, current_user, policy)
    return [ArbitrationAgentRun(**row) for row in await ArbitrationCaseWorkspaceService(db).list_agent_runs(case_id)]


@router.get("/cases/{case_id}/agent-runs/{run_id}", response_model=ArbitrationAgentRun)
async def get_arbitration_case_agent_run(
    case_id: str,
    run_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_case_and_authorize(case_id, Permissions.ARBITRATION_VIEW, db, current_user, policy)
    return ArbitrationAgentRun(**await ArbitrationCaseWorkspaceService(db).get_agent_run(case_id, run_id))


@router.get("/cases/{case_id}/exhibit-list")
async def get_arbitration_case_exhibit_list(
    case_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_case_and_authorize(case_id, Permissions.ARBITRATION_VIEW, db, current_user, policy)
    return await ArbitrationCaseWorkspaceService(db).exhibit_list(case_id)


@router.get("/cases/{case_id}/citation-audit")
async def get_arbitration_case_citation_audit(
    case_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_case_and_authorize(case_id, Permissions.ARBITRATION_VIEW, db, current_user, policy)
    return await ArbitrationCaseWorkspaceService(db).citation_audit(case_id)


@router.get("/cases/{case_id}/filing-bundle/manifest")
async def get_arbitration_case_filing_manifest(
    case_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_case_and_authorize(case_id, Permissions.ARBITRATION_VIEW, db, current_user, policy)
    return await ArbitrationCaseWorkspaceService(db).filing_bundle_manifest(case_id)


@router.get("/cases/{case_id}/filing-bundle/zip")
async def export_arbitration_case_bundle_zip(
    case_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_case_and_authorize(case_id, Permissions.ARBITRATION_EXPORT, db, current_user, policy)
    service = ArbitrationCaseWorkspaceService(db)
    return Response(
        content=await service.export_filing_bundle_zip(case_id, current_user),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="arbitration-case-bundle.zip"'},
    )


@router.get("/cases/{case_id}/filing-bundle/docx")
async def export_arbitration_case_bundle_docx(
    case_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_case_and_authorize(case_id, Permissions.ARBITRATION_EXPORT, db, current_user, policy)
    return Response(
        content=await ArbitrationCaseWorkspaceService(db).export_filing_bundle_docx(case_id, current_user),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": 'attachment; filename="arbitration-filing-bundle.docx"'},
    )


@router.get("/cases/{case_id}/filing-bundle/pdf")
async def export_arbitration_case_bundle_pdf(
    case_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_case_and_authorize(case_id, Permissions.ARBITRATION_EXPORT, db, current_user, policy)
    return Response(
        content=await ArbitrationCaseWorkspaceService(db).export_filing_bundle_pdf(case_id, current_user),
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="arbitration-filing-bundle.pdf"'},
    )


@router.post("/cases/{case_id}/filing-bundle/exports", response_model=ArbitrationBundleExport, status_code=status.HTTP_202_ACCEPTED)
async def queue_arbitration_case_bundle_export(
    case_id: str,
    payload: ArbitrationBundleExportRequest,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_case_and_authorize(case_id, Permissions.ARBITRATION_EXPORT, db, current_user, policy)
    return ArbitrationBundleExport(**await ArbitrationCaseWorkspaceService(db).queue_filing_bundle_export(case_id, payload.format, current_user))


@router.get("/cases/{case_id}/filing-bundle/exports/{export_id}", response_model=ArbitrationBundleExport)
async def get_arbitration_case_bundle_export(
    case_id: str,
    export_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_case_and_authorize(case_id, Permissions.ARBITRATION_EXPORT, db, current_user, policy)
    return ArbitrationBundleExport(**await ArbitrationCaseWorkspaceService(db).get_filing_bundle_export(case_id, export_id))


@router.get("/cases/{case_id}/filing-bundle/exports/{export_id}/download")
async def download_arbitration_case_bundle_export(
    case_id: str,
    export_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_case_and_authorize(case_id, Permissions.ARBITRATION_EXPORT, db, current_user, policy)
    artifact = await ArbitrationCaseWorkspaceService(db).get_filing_bundle_export_content(case_id, export_id)
    return Response(
        content=artifact["content"],
        media_type=artifact["content_type"],
        headers={"Content-Disposition": f'attachment; filename="{artifact["filename"]}"'},
    )


@router.get("/cases/{case_id}/{matrix_slug}", response_model=List[ArbitrationMatrixRow])
async def list_arbitration_case_matrix(
    case_id: str,
    matrix_slug: str,
    draft_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_case_and_authorize(case_id, Permissions.ARBITRATION_VIEW, db, current_user, policy)
    return [
        ArbitrationMatrixRow(**row)
        for row in await ArbitrationCaseWorkspaceService(db).list_matrix_rows(case_id, matrix_slug, draft_id=draft_id)
    ]


@router.post("/cases/{case_id}/{matrix_slug}", response_model=ArbitrationMatrixRow, status_code=status.HTTP_201_CREATED)
async def create_arbitration_case_matrix_row(
    case_id: str,
    matrix_slug: str,
    payload: ArbitrationMatrixRowCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_case_and_authorize(case_id, Permissions.ARBITRATION_EDIT, db, current_user, policy)
    return ArbitrationMatrixRow(**await ArbitrationCaseWorkspaceService(db).create_matrix_row(case_id, matrix_slug, payload, current_user))


@router.patch("/cases/{case_id}/{matrix_slug}", response_model=ArbitrationMatrixRow)
async def update_arbitration_case_matrix_row_from_body(
    case_id: str,
    matrix_slug: str,
    payload: ArbitrationMatrixRowUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_case_and_authorize(case_id, Permissions.ARBITRATION_EDIT, db, current_user, policy)
    return ArbitrationMatrixRow(**await ArbitrationCaseWorkspaceService(db).matrix_row_from_payload(case_id, matrix_slug, payload, current_user))


@router.patch("/cases/{case_id}/{matrix_slug}/{row_id}", response_model=ArbitrationMatrixRow)
async def update_arbitration_case_matrix_row(
    case_id: str,
    matrix_slug: str,
    row_id: str,
    payload: ArbitrationMatrixRowUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_case_and_authorize(case_id, Permissions.ARBITRATION_EDIT, db, current_user, policy)
    return ArbitrationMatrixRow(**await ArbitrationCaseWorkspaceService(db).update_matrix_row(case_id, matrix_slug, row_id, payload, current_user))


@router.post("/cases/{case_id}/{matrix_slug}/{row_id}/review", response_model=ArbitrationMatrixRow)
async def review_arbitration_case_matrix_row(
    case_id: str,
    matrix_slug: str,
    row_id: str,
    payload: ArbitrationMatrixReviewRequest,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    action = str(getattr(payload.action, "value", payload.action))
    permission = Permissions.ARBITRATION_APPROVE if action in {"approve", "reject"} else Permissions.ARBITRATION_EDIT
    await _load_case_and_authorize(case_id, permission, db, current_user, policy)
    return ArbitrationMatrixRow(
        **await ArbitrationCaseWorkspaceService(db).review_matrix_row(case_id, matrix_slug, row_id, payload, current_user)
    )


@router.post("/cases/{case_id}/rejoinder-matrix/{row_id}/permission", response_model=ArbitrationMatrixRow)
async def record_rejoinder_new_matter_permission(
    case_id: str,
    row_id: str,
    payload: ArbitrationRejoinderPermissionRequest,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_case_and_authorize(case_id, Permissions.ARBITRATION_APPROVE, db, current_user, policy)
    return ArbitrationMatrixRow(
        **await ArbitrationCaseWorkspaceService(db).record_rejoinder_permission(case_id, row_id, payload, current_user)
    )


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


@router.post("/drafts/{draft_id}/prepare-from-case")
async def prepare_arbitration_draft_from_case(
    draft_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    draft = await _load_and_authorize(draft_id, Permissions.ARBITRATION_EDIT, db, current_user, policy)
    if draft.get("case_id"):
        await _load_case_and_authorize(str(draft.get("case_id")), Permissions.ARBITRATION_VIEW, db, current_user, policy)
    result = await ArbitrationCaseWorkspaceService(db).prepare_draft_from_case(draft_id, current_user)
    return {**result, "draft": await ArbitrationDraftingService(db).detail(draft_id)}


@router.post("/drafts/{draft_id}/references", response_model=ArbitrationDraftDetail)
async def add_arbitration_references(
    draft_id: str,
    payload: AddReferencesRequest,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize(draft_id, Permissions.ARBITRATION_EDIT, db, current_user, policy)
    return ArbitrationDraftDetail(
        **await ArbitrationDraftingService(db).add_references(draft_id, payload.references, current_user)
    )


@router.delete("/drafts/{draft_id}/references/{reference_id}", response_model=ArbitrationDraftDetail)
async def remove_arbitration_reference(
    draft_id: str,
    reference_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize(draft_id, Permissions.ARBITRATION_EDIT, db, current_user, policy)
    return ArbitrationDraftDetail(
        **await ArbitrationDraftingService(db).remove_reference(draft_id, reference_id, current_user)
    )


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


@router.patch("/drafts/{draft_id}/paragraph-responses/{response_id}", response_model=ArbitrationParagraphResponse)
async def update_arbitration_paragraph_response(
    draft_id: str,
    response_id: str,
    payload: ArbitrationParagraphResponseUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize(draft_id, Permissions.ARBITRATION_EDIT, db, current_user, policy)
    return ArbitrationParagraphResponse(
        **await ArbitrationDraftingService(db).update_paragraph_response(draft_id, response_id, payload, current_user)
    )


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
    return ArbitrationDraftDetail(
        **await ArbitrationDraftingService(db).generate(
            draft_id,
            body,
            current_user,
            run_type=GenerationRunType.SECTION_REGENERATION,
        )
    )


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


@router.get("/drafts/{draft_id}/preview/docx")
async def preview_arbitration_docx(
    draft_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize(draft_id, Permissions.ARBITRATION_VIEW, db, current_user, policy)
    content = await ArbitrationDraftingService(db).preview_export(draft_id, "docx", current_user)
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": 'attachment; filename="arbitration-pleading-preview.docx"'},
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


@router.get("/drafts/{draft_id}/preview/pdf")
async def preview_arbitration_pdf(
    draft_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load_and_authorize(draft_id, Permissions.ARBITRATION_VIEW, db, current_user, policy)
    content = await ArbitrationDraftingService(db).preview_export(draft_id, "pdf", current_user)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": 'attachment; filename="arbitration-pleading-preview.pdf"'},
    )
