from __future__ import annotations

import logging
from typing import Optional

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
        await submit_background_job("ingestion-pipeline", self.pipeline.process_job, job.id)
        return job

    async def get_job(self, job_id: str) -> Optional[IngestionJob]:
        return await self.pipeline.get_job(job_id)

