from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from ..services.background_jobs import submit_background_job
from ..observability.service import ObservabilityService
from ..retrieval.dependencies import get_embedding_client, get_vector_client
from .models import IngestionJob, IngestionJobCreate
from .pipeline import IngestionPipeline

logger = logging.getLogger(__name__)


class IngestionService:
    """Facade to create ingestion jobs and trigger the pipeline."""

    def __init__(self, db: AsyncIOMotorDatabase, observability: ObservabilityService):
        self.db = db
        self.observability = observability
        self.pipeline = IngestionPipeline(
            db=db,
            embedding_client=get_embedding_client(),
            vector_client=get_vector_client(),
            observability_service=observability,
        )

    async def create_job(self, payload: IngestionJobCreate) -> IngestionJob:
        job = await self.pipeline.create_job(payload)
        await submit_background_job(
            "ingestion-pipeline", self.pipeline.process_job, job.id
        )
        return job

    async def document_in_scope(
        self, document_id: str, org_id: str, project_id: str
    ) -> Optional[Dict[str, Any]]:
        """The canonical document, only when it lives in exactly this scope."""
        from ..services.publication_policy import resolve_canonical_document

        document = await resolve_canonical_document(self.db, document_id)
        if document is None:
            return None
        if str(document.get("organization_id") or "") != str(org_id or ""):
            return None
        if str(document.get("project_id") or "") != str(project_id or ""):
            return None
        return document

    async def is_governed_contract(self, document_id: str) -> bool:
        """True when a Contract Master instrument names this document.

        Resolved through the canonical Document so an ObjectId-keyed document
        named by its string id and a legacy string-keyed one answer alike.
        """
        from ..services.document_service import governed_by_contract_master
        from ..services.publication_policy import resolve_canonical_document

        document = await resolve_canonical_document(self.db, document_id)
        stored_id = document.get("_id") if document else None
        return await governed_by_contract_master(self.db, document_id, stored_id)

    async def get_job(self, job_id: str) -> Optional[IngestionJob]:
        return await self.pipeline.get_job(job_id)
