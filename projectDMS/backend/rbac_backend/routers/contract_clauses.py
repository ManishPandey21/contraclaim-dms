"""Clause Index API — read + editorial actions for the contract viewer (Phase 4)."""

from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..core.database import get_db
from ..core.security import CurrentUser, get_current_user
from ..services.contract_clause.agent import ClauseChunkingAgent
from ..services.contract_clause.index_service import (
    ClauseIndexService,
    ClauseMergeError,
    ClauseNotFoundError,
)
from ..services.contract_clause.storage_service import ClauseScopeError
from ..services.contract_service import ContractService
from ..services.policy_service import PolicyService

router = APIRouter(tags=["contract-clauses"])
logger = logging.getLogger(__name__)

# Keep strong references to fire-and-forget indexing tasks so they are not
# garbage-collected before completion.
_BACKGROUND_TASKS: set = set()


async def _run_clause_indexing(db, current_user, document_id: str) -> None:
    try:
        agent = ClauseChunkingAgent(db=db, policy_service=PolicyService(db=db))
        summary = await agent.process_document(current_user, document_id)
        logger.info(
            "clause indexing complete for %s: clauses=%s tables=%s low_conf=%s mods=%s",
            document_id, summary.clauses_detected, summary.tables_detected,
            summary.low_confidence_chunks, summary.modifications_detected,
        )
    except Exception as exc:  # pragma: no cover - background best-effort
        logger.exception("clause indexing failed for %s: %s", document_id, exc)


async def get_clause_index_service(db=Depends(get_db)) -> ClauseIndexService:
    return ClauseIndexService(db=db, policy_service=PolicyService(db=db))


async def get_contract_service() -> ContractService:
    return ContractService()


class ClauseUpdateRequest(BaseModel):
    clause_title: Optional[str] = None
    mark_verified: bool = False
    is_superseded: Optional[bool] = None
    superseded_by_clause_id: Optional[str] = None


class ClauseMergeRequest(BaseModel):
    clause_uids: List[str] = Field(..., min_length=2)


class ClauseSplitRequest(BaseModel):
    split_at: int = Field(..., gt=0)


@router.post("/contracts/{document_id}/clauses/index", status_code=status.HTTP_202_ACCEPTED)
async def run_clause_indexing(
    document_id: str,
    db=Depends(get_db),
    contract_service: ContractService = Depends(get_contract_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Kick off clause-wise indexing for a contract document (Phase 2/3 pipeline).

    Authorization + scope are checked synchronously (so a 403/422 surfaces here);
    the heavy extraction/embedding/graph work runs in the background.
    """
    document = await contract_service.get_contract_document(document_id, current_user)
    resolved_id = str(document.get("_id") or document_id)
    org_id = str(document.get("organization_id") or "")
    project_id = str(document.get("project_id") or "")
    contract_id = str(document.get("contract_id") or resolved_id)

    agent = ClauseChunkingAgent(db=db, policy_service=PolicyService(db=db))
    try:
        await agent.storage.authorize_processing_run(
            current_user,
            org_id=org_id,
            project_id=project_id,
            contract_id=contract_id,
            document_id=resolved_id,
        )
    except ClauseScopeError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    task = asyncio.create_task(_run_clause_indexing(db, current_user, resolved_id))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
    return {"status": "processing", "document_id": resolved_id}


@router.get("/contracts/{document_id}/clauses")
async def list_document_clauses(
    document_id: str,
    service: ClauseIndexService = Depends(get_clause_index_service),
    contract_service: ContractService = Depends(get_contract_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """List clause records for a contract document (Clause Index tab)."""
    document = await contract_service.get_contract_document(document_id, current_user)
    org_id = str(document.get("organization_id") or "")
    project_id = str(document.get("project_id") or "")
    clauses = await service.list_clauses(
        current_user, document_id=document_id, org_id=org_id, project_id=project_id
    )
    return {"document_id": document_id, "count": len(clauses), "clauses": clauses}


@router.patch("/contracts/clauses/{clause_uid}")
async def update_clause(
    clause_uid: str,
    payload: ClauseUpdateRequest,
    service: ClauseIndexService = Depends(get_clause_index_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Edit clause title, mark verified, or mark superseded."""
    try:
        return await service.update_clause(
            current_user,
            clause_uid,
            clause_title=payload.clause_title,
            mark_verified=payload.mark_verified,
            is_superseded=payload.is_superseded,
            superseded_by_clause_id=payload.superseded_by_clause_id,
        )
    except ClauseNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clause not found")


@router.post("/contracts/clauses/{clause_uid}/regenerate-embedding")
async def regenerate_clause_embedding(
    clause_uid: str,
    service: ClauseIndexService = Depends(get_clause_index_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Queue the clause for re-embedding."""
    try:
        return await service.regenerate_embedding(current_user, clause_uid)
    except ClauseNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clause not found")


@router.post("/contracts/clauses/{clause_uid}/split")
async def split_clause(
    clause_uid: str,
    payload: ClauseSplitRequest,
    service: ClauseIndexService = Depends(get_clause_index_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Split a clause into two parts at a character offset."""
    try:
        return {"parts": await service.split_clause(current_user, clause_uid, payload.split_at)}
    except ClauseNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clause not found")
    except ClauseMergeError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))


@router.post("/contracts/clauses/merge")
async def merge_clauses(
    payload: ClauseMergeRequest,
    service: ClauseIndexService = Depends(get_clause_index_service),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Merge multiple clauses into the first."""
    try:
        return await service.merge_clauses(current_user, payload.clause_uids)
    except ClauseNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clause not found")
    except ClauseMergeError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
