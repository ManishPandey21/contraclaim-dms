from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Set

from motor.motor_asyncio import AsyncIOMotorDatabase

from .chunker import chunk_text
from .enrichment import ChunkEnricher
from .models import Chunk, IngestionJob, IngestionJobCreate, IngestionOptions, IngestionStage, StageTiming, compute_content_hash
from ..observability.service import ObservabilityService
from ..retrieval.embeddings import EmbeddingClient
from ..retrieval.vector_client import VectorClient
from ..services.database_service import DocumentProcessingError

logger = logging.getLogger(__name__)


class IngestionPipeline:
    """Orchestrated ingestion pipeline with idempotent stages."""

    def __init__(
        self,
        db: AsyncIOMotorDatabase,
        embedding_client: EmbeddingClient,
        vector_client: VectorClient,
        observability_service: ObservabilityService,
    ) -> None:
        self.db = db
        self.embedding_client = embedding_client
        self.vector_client = vector_client
        self.observability = observability_service
        self.enricher = ChunkEnricher(embedding_client)

    async def create_job(self, payload: IngestionJobCreate) -> IngestionJob:
        job = IngestionJob(
            org_id=payload.org_id,
            project_id=payload.project_id,
            document_id=payload.document_id,
            options=payload.options,
            content_hash=payload.content_hash,
        )
        await self.db.ingestion_jobs.insert_one(job.model_dump(by_alias=True, exclude_none=True))
        return job

    async def get_job(self, job_id: str) -> Optional[IngestionJob]:
        doc = await self.db.ingestion_jobs.find_one({"job_id": job_id})
        if not doc:
            return None
        return IngestionJob(**doc)

    async def process_job(self, job_id: str) -> None:
        job_doc = await self.db.ingestion_jobs.find_one({"job_id": job_id})
        if not job_doc:
            logger.error("Ingestion job %s not found", job_id)
            return
        job = IngestionJob(**job_doc)
        stage_timings: List[StageTiming] = []
        timing_breakdown: Dict[str, float] = {}

        async def _update_stage(stage: IngestionStage, progress: float, error: Optional[str] = None):
            update = {
                "stage": stage.value,
                "status": stage.value,
                "progress": progress,
                "updated_at": datetime.utcnow(),
                "error": error,
                "stage_timings": [st.model_dump() for st in stage_timings],
            }
            await self.db.ingestion_jobs.update_one({"job_id": job_id}, {"$set": update})

        try:
            existing_chunks: Dict[str, Dict[str, Any]] = {
                chunk["chunk_id"]: chunk async for chunk in self.db.chunks.find({"document_id": job.document_id})
            }

            extract_started_at = datetime.utcnow()
            extract_start = time.perf_counter()
            await _update_stage(IngestionStage.EXTRACTING, progress=0.05)
            document = await self._load_document(job.document_id)
            text = self._extract_text(document)
            if not text:
                raise ValueError("Document has no text to ingest")
            job.content_hash = job.content_hash or compute_content_hash(text)
            if await self._is_dedup(job):
                stage_timings.append(
                    StageTiming(stage=IngestionStage.EXTRACTING.value, started_at=extract_started_at, completed_at=datetime.utcnow(), duration_ms=0.0)
                )
                await self._mark_complete(job_id, stage_timings, progress=1.0, deduped=True, content_hash=job.content_hash)
                return
            timing_breakdown["extracting"] = (time.perf_counter() - extract_start) * 1000
            stage_timings.append(
                StageTiming(
                    stage=IngestionStage.EXTRACTING.value,
                    started_at=extract_started_at,
                    completed_at=datetime.utcnow(),
                    duration_ms=timing_breakdown["extracting"],
                )
            )
            await _update_stage(IngestionStage.CHUNKING, progress=0.15)

            chunk_started_at = datetime.utcnow()
            chunk_start = time.perf_counter()
            chunks = chunk_text(
                document_id=str(job.document_id),
                org_id=job.org_id,
                project_id=job.project_id,
                text=text,
                chunk_size=job.options.chunk_size,
                chunk_overlap=job.options.chunk_overlap,
            )
            for c in chunks:
                c.embedding_model = job.options.embedding_model
                c.embedding_version = job.options.embedding_version
                c.embedding_provider = job.options.embedding_provider
                c.embedding_dim = job.options.embedding_dim or self.vector_client.config.qdrant_vector_size
                c.chunking_version = job.options.chunking_version
            timing_breakdown["chunking"] = (time.perf_counter() - chunk_start) * 1000
            stage_timings.append(
                StageTiming(
                    stage=IngestionStage.CHUNKING.value,
                    started_at=chunk_started_at,
                    completed_at=datetime.utcnow(),
                    duration_ms=timing_breakdown["chunking"],
                )
            )
            await _update_stage(IngestionStage.EMBEDDING, progress=0.4)

            embed_started_at = datetime.utcnow()
            embed_start = time.perf_counter()
            texts_to_embed: List[str] = []
            embed_indices: List[int] = []
            for idx, c in enumerate(chunks):
                cached = existing_chunks.get(c.id)
                same_hash = cached and cached.get("content_hash") == c.content_hash
                same_model = cached and cached.get("embedding_model") == c.embedding_model and cached.get("embedding_version") == c.embedding_version
                if same_hash and same_model:
                    continue
                embed_indices.append(idx)
                texts_to_embed.append(c.text_original)

            vectors: List[List[float]] = []
            if embed_indices:
                vectors = await self.embedding_client.embed(texts_to_embed, model=job.options.embedding_model)
            timing_breakdown["embedding"] = (time.perf_counter() - embed_start) * 1000
            stage_timings.append(
                StageTiming(
                    stage=IngestionStage.EMBEDDING.value,
                    started_at=embed_started_at,
                    completed_at=datetime.utcnow(),
                    duration_ms=timing_breakdown["embedding"],
                )
            )

            await _update_stage(IngestionStage.ENRICHING if job.options.enrichment_on else IngestionStage.INDEXING, progress=0.55)

            if job.options.enrichment_on:
                enrich_started_at = datetime.utcnow()
                enrich_start = time.perf_counter()
                await self.enricher.enrich(
                    chunks,
                    vectors,
                    strategies=job.options.enrichment_strategies or ["neighborhood"],
                )
                timing_breakdown["enriching"] = (time.perf_counter() - enrich_start) * 1000
                stage_timings.append(
                    StageTiming(
                        stage=IngestionStage.ENRICHING.value,
                        started_at=enrich_started_at,
                        completed_at=datetime.utcnow(),
                        duration_ms=timing_breakdown["enriching"],
                    )
                )

            await _update_stage(IngestionStage.INDEXING, progress=0.75)

            index_started_at = datetime.utcnow()
            index_start = time.perf_counter()
            await self._persist_chunks(chunks, job.content_hash, job.options)

            changed_chunks = [chunks[i] for i in embed_indices]
            if changed_chunks:
                await self.vector_client.upsert(
                    vectors,
                    [
                        {
                            "chunk_id": c.id,
                            "document_id": c.document_id,
                            "org_id": c.org_id,
                            "project_id": c.project_id,
                            "page_start": c.page_start,
                            "text": c.text_original,
                            "text_enriched": c.text_enriched,
                            "tags": c.tags,
                            "embedding_provider": c.embedding_provider,
                            "embedding_model": c.embedding_model,
                            "embedding_dim": c.embedding_dim,
                            "embedding_version": c.embedding_version,
                            "chunking_version": c.chunking_version,
                        }
                        for c in changed_chunks
                    ],
                    namespace=job.options.vector_namespace,
                )

            await self._prune_stale_vectors(
                job=job,
                current_chunks=set(c.id for c in chunks),
                namespace=job.options.vector_namespace,
            )
            await self._update_vector_sync(
                job=job,
                expected=len(chunks),
                namespace=job.options.vector_namespace,
            )
            await self.observability.log_run(
                run_type="embed_write_mongo",
                org_id=job.org_id,
                project_id=job.project_id,
                strategy="ingestion_pipeline",
                query=str(job.document_id),
                retrieved=[{"chunk_id": c.id} for c in chunks],
                breakdown_ms={"persist_ms": timing_breakdown.get("indexing", 0.0)},
            )
            if changed_chunks:
                await self.observability.log_run(
                    run_type="embed_write_qdrant",
                    org_id=job.org_id,
                    project_id=job.project_id,
                    strategy="ingestion_pipeline",
                    query=str(job.document_id),
                    retrieved=[{"chunk_id": c.id} for c in changed_chunks],
                    breakdown_ms={"qdrant_ms": timing_breakdown.get("indexing", 0.0)},
                )
            timing_breakdown["indexing"] = (time.perf_counter() - index_start) * 1000
            stage_timings.append(
                StageTiming(
                    stage=IngestionStage.INDEXING.value,
                    started_at=index_started_at,
                    completed_at=datetime.utcnow(),
                    duration_ms=timing_breakdown["indexing"],
                )
            )

            await self._mark_complete(job_id, stage_timings, progress=1.0, content_hash=job.content_hash)
            await self.observability.log_run(
                run_type="ingestion",
                org_id=job.org_id,
                project_id=job.project_id,
                strategy="ingestion_pipeline",
                query=str(job.document_id),
                retrieved=[],
                breakdown_ms=timing_breakdown,
            )
        except Exception as exc:
            logger.exception("Ingestion job %s failed: %s", job_id, exc)
            await self.db.ingestion_jobs.update_one(
                {"job_id": job_id},
                {
                    "$set": {
                        "stage": IngestionStage.FAILED.value,
                        "status": IngestionStage.FAILED.value,
                        "error": str(exc),
                        "updated_at": datetime.utcnow(),
                        "stage_timings": [st.model_dump() for st in stage_timings],
                    }
                },
            )

    async def _load_document(self, document_id: str) -> Dict[str, Any]:
        doc = await self.db.documents.find_one({"_id": document_id}) or await self.db.documents.find_one(
            {"id": document_id}
        )
        if not doc:
            raise ValueError(f"Document {document_id} not found")
        return doc

    def _extract_text(self, document: Dict[str, Any]) -> str:
        # This text becomes chunks and then vectors, so this is a publication
        # producer, not an internal transformation stage. Without the guard a
        # reindex of a blocked document would republish exactly the content the
        # publication barrier withheld.
        from ..services.publication_policy import is_consumable

        if not is_consumable(document):
            return ""

        for key in ("full_text", "ocrText", "text"):
            value = document.get(key)
            if value:
                return str(value)
        return ""

    async def _persist_chunks(self, chunks: List[Chunk], content_hash: Optional[str], options: IngestionOptions) -> None:
        if not chunks:
            return
        ops = []
        for chunk in chunks:
            payload = chunk.model_dump(by_alias=True, exclude_none=True)
            payload["content_hash"] = content_hash
            payload["chunking_version"] = options.chunking_version
            ops.append(
                {
                    "replace_one": {
                        "filter": {"chunk_id": chunk.id},
                        "replacement": payload,
                        "upsert": True,
                    }
                }
            )
        await self.db.chunks.bulk_write(
            [self._build_replace(op["replace_one"]) for op in ops], ordered=False
        )

        # Remove stale chunks no longer present
        current_ids = {c.id for c in chunks}
        stale_ids = []
        async for doc in self.db.chunks.find({"document_id": chunks[0].document_id}):
            if doc.get("chunk_id") not in current_ids:
                stale_ids.append(doc.get("chunk_id"))
        if stale_ids:
            await self.db.chunks.delete_many({"chunk_id": {"$in": stale_ids}})

    def _build_replace(self, spec):
        from pymongo import ReplaceOne

        return ReplaceOne(spec["filter"], spec["replacement"], upsert=spec.get("upsert", False))

    async def _prune_stale_vectors(self, job: IngestionJob, current_chunks: Set[str], namespace: Optional[str]) -> None:
        try:
            existing_ids = await self.vector_client.list_chunk_ids(
                {"org_id": job.org_id, "project_id": job.project_id, "document_id": job.document_id},
                namespace=namespace,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.debug("List chunk ids failed: %s", exc)
            return
        stale = [cid for cid in existing_ids if cid not in current_chunks]
        if stale:
            await self.vector_client.delete(stale, namespace=namespace)

    async def _update_vector_sync(self, job: IngestionJob, expected: int, namespace: Optional[str]) -> None:
        """Update vector_sync_status bookkeeping to clear stale statuses."""
        try:
            qdrant_ids = await self.vector_client.list_chunk_ids(
                {"org_id": job.org_id, "project_id": job.project_id, "document_id": job.document_id},
                namespace=namespace,
            )
            qdrant_count = len(qdrant_ids)
            status = "synced" if qdrant_count == expected else "mismatch"
            await self.db.vector_sync_status.update_one(
                {"document_id": job.document_id},
                {
                    "$set": {
                        "document_id": job.document_id,
                        "sync_status": status,
                        "updatedAt": datetime.utcnow(),
                        "mongo_chunks": expected,
                        "qdrant_chunks": qdrant_count,
                    },
                    "$setOnInsert": {"createdAt": datetime.utcnow()},
                },
                upsert=True,
            )
        except Exception as exc:  # pragma: no cover - best-effort
            logger.debug("Vector sync status update failed: %s", exc)

    async def _is_dedup(self, job: IngestionJob) -> bool:
        if not job.content_hash:
            return False
        existing = await self.db.ingestion_jobs.find_one(
            {
                "document_id": job.document_id,
                "project_id": job.project_id,
                "content_hash": job.content_hash,
                "stage": IngestionStage.DONE.value,
            }
        )
        return bool(existing)

    async def _mark_complete(
        self,
        job_id: str,
        stage_timings: List[StageTiming],
        progress: float,
        content_hash: Optional[str],
        deduped: bool = False,
    ) -> None:
        await self.db.ingestion_jobs.update_one(
            {"job_id": job_id},
            {
                "$set": {
                    "stage": IngestionStage.DONE.value,
                    "status": IngestionStage.DONE.value,
                    "progress": progress,
                    "updated_at": datetime.utcnow(),
                    "content_hash": content_hash,
                    "deduped": deduped,
                    "stage_timings": [st.model_dump() for st in stage_timings],
                }
            },
        )
