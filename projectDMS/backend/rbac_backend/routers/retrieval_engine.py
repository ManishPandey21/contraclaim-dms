from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..agents.service import DraftingAgentService
from ..agents.models import AgentRequest, AgentResponse
from ..core.config import settings
from ..core.database import get_db
from ..core.permissions import Permissions
from ..core.security import CurrentUser, get_current_user
from ..services.policy_service import PolicyService
from ..ingestion.models import IngestionJob, IngestionJobCreate
from ..ingestion.service import IngestionService
from ..observability.models import AnalyticsRequest
from ..observability.service import ObservabilityService
from ..retrieval.dependencies import (
    get_embedding_client,
    get_llm_generator,
    get_vector_client,
)
from ..retrieval.models import (
    ContractQARequest,
    ContractQAResponse,
    RagRequest,
    RagResponse,
    SearchRequest,
    SearchResponse,
)
from ..retrieval.reranker import RerankerService
from ..retrieval.service import RetrievalService
from ..retrieval.reconcile import VectorReconciler
from ..services.ai_guardrails import AIOutputGuardrailService
from ..services.contract_service import ContractService

router = APIRouter(prefix="/v1", tags=["retrieval-engine"])
logger = logging.getLogger(__name__)


async def get_observability(db=Depends(get_db)) -> ObservabilityService:
    return ObservabilityService(db)


async def get_retrieval_service(
    db=Depends(get_db), observability: ObservabilityService = Depends(get_observability)
) -> RetrievalService:
    llm_generator = get_llm_generator()
    return RetrievalService(
        db=db,
        embedding_client=get_embedding_client(),
        vector_client=get_vector_client(),
        llm_generator=llm_generator,
        observability=observability,
        reranker=RerankerService.from_settings(settings, llm_generator),
        guardrails=AIOutputGuardrailService.from_settings(settings),
    )


async def get_ingestion_service(
    db=Depends(get_db), observability: ObservabilityService = Depends(get_observability)
) -> IngestionService:
    return IngestionService(db=db, observability=observability)


async def get_agent_service(
    db=Depends(get_db),
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
    observability: ObservabilityService = Depends(get_observability),
) -> DraftingAgentService:
    return DraftingAgentService(
        db=db,
        retrieval_service=retrieval_service,
        llm_generator=get_llm_generator(),
        observability=observability,
    )


async def get_reconciler(
    db=Depends(get_db),
    embedding_client=Depends(get_embedding_client),
    vector_client=Depends(get_vector_client),
) -> VectorReconciler:
    return VectorReconciler(
        db=db, embedding_client=embedding_client, vector_client=vector_client
    )


async def get_policy_service(db=Depends(get_db)) -> PolicyService:
    return PolicyService(db=db)


def _require_scope_values(
    org_id: Optional[str],
    project_id: Optional[str],
    *,
    allow_organization_level: bool = False,
) -> tuple[str, Optional[str]]:
    """Reject blank/missing scope before it reaches the deny-by-default policy.

    Empty strings would otherwise pass straight through scope evaluation and
    return unfiltered (cross-tenant) results, so a blank organisation or a
    blank/whitespace project is always a 400.

    With ``allow_organization_level`` an explicit ``null`` project is accepted:
    it asks for organisation-level documents only. The policy still checks
    membership of the organisation, and ``RetrievalService`` decides whether
    this actor may see organisation-level rows at all.
    """
    org = (org_id or "").strip()
    if not org:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A non-empty organization and project scope is required",
        )
    if project_id is None and allow_organization_level:
        return org, None
    project = (project_id or "").strip()
    if not project:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A non-empty organization and project scope is required",
        )
    return org, project


def _refuse_org_level_contract(
    request: SearchRequest, retrieval_service: RetrievalService
) -> None:
    """Contract retrieval has no organisation-level scope: a 400, not a 500."""
    if request.filters.project_id is None and retrieval_service._is_contract_request(
        request
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Contract retrieval requires a project scope",
        )


async def _authorize_scope(
    current_user: CurrentUser,
    org_id: Optional[str],
    project_id: Optional[str],
    *,
    permission: str = Permissions.DOCUMENT_VIEW,
    policy: Optional[PolicyService] = None,
    allow_organization_level: bool = False,
) -> None:
    """Deny-by-default scope enforcement for the retrieval engine.

    Replaces the legacy ``_ensure_scope`` helper, which silently skipped the
    organization check when ``current_user.organization_id`` was falsy and the
    project check when ``current_user.projects`` was empty -- allowing a caller
    to claim an arbitrary ``org_id``/``project_id`` in the request body. The
    central ``PolicyService`` verifies RBAC permission, subscription entitlement,
    and tenant membership (``ScopeService.is_client_scope_allowed``) and emits an
    audit event, matching the documents router.
    """
    org, project = _require_scope_values(
        org_id, project_id, allow_organization_level=allow_organization_level
    )
    policy = policy or PolicyService()
    await policy.authorize(
        current_user,
        permission,
        resource_type="retrieval",
        organization_id=org,
        project_id=project,
    )


@router.post("/ingestion/jobs", response_model=IngestionJob)
async def create_ingestion_job(
    payload: IngestionJobCreate,
    ingestion_service: IngestionService = Depends(get_ingestion_service),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
) -> IngestionJob:
    await _authorize_scope(
        current_user,
        payload.org_id,
        payload.project_id,
        permission=Permissions.DOCUMENT_UPLOAD,
        policy=policy,
    )
    # The payload scope is the caller's claim; the document must live in it and
    # the caller must be allowed the document itself. Absent, foreign and
    # forbidden all answer the same 404, so nothing about another tenant's
    # documents - including whether one is a governed contract - is disclosed.
    document = await ingestion_service.document_in_scope(
        payload.document_id, payload.org_id, payload.project_id
    )
    if document is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
        )
    try:
        await policy.authorize_document(
            current_user, Permissions.DOCUMENT_UPLOAD, document
        )
    except HTTPException as exc:
        if exc.status_code == status.HTTP_403_FORBIDDEN:
            # Same answer as absent or foreign: a refusal specific to this
            # document must not reveal that it exists.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Document not found"
            ) from None
        raise
    # A governed contract's evidence is rebuilt by the contract reindex and
    # reprojection; this pipeline would prune it.
    if await ingestion_service.is_governed_contract(payload.document_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This document is governed by a Contract Master instrument; reindex "
                "it through the contract reindex, which rebuilds its projection"
            ),
        )
    job = await ingestion_service.create_job(payload)
    return job


@router.get("/ingestion/jobs/{job_id}", response_model=IngestionJob)
async def get_ingestion_job(
    job_id: str,
    ingestion_service: IngestionService = Depends(get_ingestion_service),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
) -> IngestionJob:
    job = await ingestion_service.get_job(job_id)
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Job not found"
        )
    await _authorize_scope(current_user, job.org_id, job.project_id, policy=policy)
    return job


@router.post("/retrieval/search", response_model=SearchResponse)
async def search(
    request: SearchRequest,
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
) -> SearchResponse:
    await _authorize_scope(
        current_user,
        request.filters.org_id,
        request.filters.project_id,
        policy=policy,
        allow_organization_level=True,
    )
    _refuse_org_level_contract(request, retrieval_service)
    return await retrieval_service.search(request, current_user)


@router.post("/retrieval/rag", response_model=RagResponse)
async def rag(
    request: RagRequest,
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
) -> RagResponse:
    await _authorize_scope(
        current_user,
        request.filters.org_id,
        request.filters.project_id,
        policy=policy,
        allow_organization_level=True,
    )
    _refuse_org_level_contract(request, retrieval_service)
    return await retrieval_service.rag(request, current_user)


@router.post("/retrieval/contract-qa", response_model=ContractQAResponse)
async def contract_qa(
    request: ContractQARequest,
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
) -> ContractQAResponse:
    request.filters.metadata["uploadType"] = "contract"
    request.filters.metadata["document_type"] = "contract"
    # Authorize the claimed scope up front so a foreign document_id cannot be
    # probed via the lookup below before tenant membership is verified.
    await _authorize_scope(
        current_user, request.filters.org_id, request.filters.project_id, policy=policy
    )
    contract_service = ContractService()
    if request.filters.document_id:
        document = await contract_service.get_contract_document(
            request.filters.document_id, current_user
        )
        if str(document.get("status") or "").lower() != "completed":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Contract is not ready for QA",
            )
        request.filters.org_id = str(
            document.get("organization_id") or request.filters.org_id
        )
        request.filters.project_id = str(
            document.get("project_id") or request.filters.project_id
        )
        request.filters.document_id = str(
            document.get("_id") or request.filters.document_id
        )
        # Re-authorize against the document's actual scope: the resolved
        # org/project may differ from the claimed filters.
        await _authorize_scope(
            current_user,
            request.filters.org_id,
            request.filters.project_id,
            policy=policy,
        )
    return await retrieval_service.contract_iterative_qa(request, current_user)


@router.post("/retrieval/agent", response_model=AgentResponse)
async def agent(
    request: AgentRequest,
    agent_service: DraftingAgentService = Depends(get_agent_service),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
) -> AgentResponse:
    await _authorize_scope(
        current_user, request.org_id, request.project_id, policy=policy
    )
    return await agent_service.run(request, current_user)


@router.get("/observability/logs")
async def list_logs(
    org_id: Optional[str] = Query(default=None),
    project_id: Optional[str] = Query(default=None),
    run_type: Optional[str] = Query(default=None),
    from_ts: Optional[str] = Query(default=None, alias="from"),
    to_ts: Optional[str] = Query(default=None, alias="to"),
    observability: ObservabilityService = Depends(get_observability),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    # Only superadmins can fetch unscoped logs
    if org_id and project_id:
        await _authorize_scope(
            current_user,
            org_id,
            project_id,
            permission=Permissions.REPORT_VIEW,
            policy=policy,
        )
    elif "superadmin" not in (current_user.roles or []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Analytics require scope"
        )
    try:
        start = datetime.fromisoformat(from_ts) if from_ts else None
        end = datetime.fromisoformat(to_ts) if to_ts else None
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid datetime format"
        ) from exc
    return await observability.get_logs(
        org_id=org_id, project_id=project_id, run_type=run_type, start=start, end=end
    )


@router.post("/observability/analytics")
async def analytics(
    request: AnalyticsRequest,
    observability: ObservabilityService = Depends(get_observability),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy_service),
):
    if request.org_id and request.project_id:
        await _authorize_scope(
            current_user,
            request.org_id,
            request.project_id,
            permission=Permissions.REPORT_VIEW,
            policy=policy,
        )
    elif "superadmin" not in (current_user.roles or []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Analytics require org_id and project_id",
        )
    return await observability.analytics(request)


@router.post("/admin/vector/reconcile")
async def reconcile_vectors(
    document_id: str = Query(...),
    org_id: str = Query(...),
    project_id: str = Query(...),
    namespace: Optional[str] = Query(default=None),
    reconciler: VectorReconciler = Depends(get_reconciler),
    current_user: CurrentUser = Depends(get_current_user),
):
    if "superadmin" not in (current_user.roles or []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superadmin required for reconciliation",
        )
    return await reconciler.reconcile_document(
        document_id=document_id,
        org_id=org_id,
        project_id=project_id,
        namespace=namespace,
    )
