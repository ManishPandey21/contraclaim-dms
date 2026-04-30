from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from ..agents.service import DraftingAgentService
from ..agents.models import AgentRequest, AgentResponse
from ..core.database import get_db
from ..core.security import CurrentUser, get_current_user
from ..ingestion.models import IngestionJob, IngestionJobCreate
from ..ingestion.service import IngestionService
from ..observability.models import AnalyticsRequest
from ..observability.service import ObservabilityService
from ..retrieval.dependencies import get_embedding_client, get_llm_generator, get_vector_client
from ..retrieval.models import ContractQARequest, ContractQAResponse, RagRequest, RagResponse, SearchRequest, SearchResponse
from ..retrieval.service import RetrievalService
from ..retrieval.reconcile import VectorReconciler
from ..services.contract_service import ContractService

router = APIRouter(prefix="/v1", tags=["retrieval-engine"])
logger = logging.getLogger(__name__)


async def get_observability(db=Depends(get_db)) -> ObservabilityService:
    return ObservabilityService(db)


async def get_retrieval_service(db=Depends(get_db), observability: ObservabilityService = Depends(get_observability)) -> RetrievalService:
    return RetrievalService(
        db=db,
        embedding_client=get_embedding_client(),
        vector_client=get_vector_client(),
        llm_generator=get_llm_generator(),
        observability=observability,
    )


async def get_ingestion_service(db=Depends(get_db), observability: ObservabilityService = Depends(get_observability)) -> IngestionService:
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
    return VectorReconciler(db=db, embedding_client=embedding_client, vector_client=vector_client)


def _ensure_scope(org_id: str, project_id: str, current_user: CurrentUser) -> None:
    if "superadmin" in (current_user.roles or []):
        return
    if current_user.organization_id and current_user.organization_id != org_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cross-org access denied")
    if current_user.projects and project_id not in current_user.projects:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Project access denied")


@router.post("/ingestion/jobs", response_model=IngestionJob)
async def create_ingestion_job(
    payload: IngestionJobCreate,
    ingestion_service: IngestionService = Depends(get_ingestion_service),
    current_user: CurrentUser = Depends(get_current_user),
) -> IngestionJob:
    _ensure_scope(payload.org_id, payload.project_id, current_user)
    job = await ingestion_service.create_job(payload)
    return job


@router.get("/ingestion/jobs/{job_id}", response_model=IngestionJob)
async def get_ingestion_job(
    job_id: str,
    ingestion_service: IngestionService = Depends(get_ingestion_service),
    current_user: CurrentUser = Depends(get_current_user),
) -> IngestionJob:
    job = await ingestion_service.get_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found")
    _ensure_scope(job.org_id, job.project_id, current_user)
    return job


@router.post("/retrieval/search", response_model=SearchResponse)
async def search(
    request: SearchRequest,
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
    current_user: CurrentUser = Depends(get_current_user),
) -> SearchResponse:
    _ensure_scope(request.filters.org_id, request.filters.project_id, current_user)
    return await retrieval_service.search(request, current_user)


@router.post("/retrieval/rag", response_model=RagResponse)
async def rag(
    request: RagRequest,
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
    current_user: CurrentUser = Depends(get_current_user),
) -> RagResponse:
    _ensure_scope(request.filters.org_id, request.filters.project_id, current_user)
    return await retrieval_service.rag(request, current_user)


@router.post("/retrieval/contract-qa", response_model=ContractQAResponse)
async def contract_qa(
    request: ContractQARequest,
    retrieval_service: RetrievalService = Depends(get_retrieval_service),
    current_user: CurrentUser = Depends(get_current_user),
) -> ContractQAResponse:
    contract_service = ContractService()
    if request.filters.document_id:
        document = await contract_service.get_contract_document(request.filters.document_id, current_user)
        if str(document.get("status") or "").lower() != "completed":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Contract is not ready for QA")
        request.filters.org_id = str(document.get("organization_id") or request.filters.org_id)
        request.filters.project_id = str(document.get("project_id") or request.filters.project_id)
        request.filters.document_id = str(document.get("_id") or request.filters.document_id)
    _ensure_scope(request.filters.org_id, request.filters.project_id, current_user)
    return await retrieval_service.contract_iterative_qa(request, current_user)


@router.post("/retrieval/agent", response_model=AgentResponse)
async def agent(
    request: AgentRequest,
    agent_service: DraftingAgentService = Depends(get_agent_service),
    current_user: CurrentUser = Depends(get_current_user),
) -> AgentResponse:
    _ensure_scope(request.org_id, request.project_id, current_user)
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
):
    # Only superadmins can fetch unscoped logs
    if org_id and project_id:
        _ensure_scope(org_id, project_id, current_user)
    elif "superadmin" not in (current_user.roles or []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Analytics require scope")
    try:
        start = datetime.fromisoformat(from_ts) if from_ts else None
        end = datetime.fromisoformat(to_ts) if to_ts else None
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid datetime format") from exc
    return await observability.get_logs(org_id=org_id, project_id=project_id, run_type=run_type, start=start, end=end)


@router.post("/observability/analytics")
async def analytics(
    request: AnalyticsRequest,
    observability: ObservabilityService = Depends(get_observability),
    current_user: CurrentUser = Depends(get_current_user),
):
    if request.org_id and request.project_id:
        _ensure_scope(request.org_id, request.project_id, current_user)
    elif "superadmin" not in (current_user.roles or []):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Analytics require org_id and project_id")
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
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Superadmin required for reconciliation")
    return await reconciler.reconcile_document(document_id=document_id, org_id=org_id, project_id=project_id, namespace=namespace)
