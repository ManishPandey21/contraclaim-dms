from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from bson import ObjectId

from ..core.database import get_database
from ..models.contract_models import (
    ContractClauseChunk,
    ContractListResponse,
    ContractSearchRequest,
    ContractSearchResponse,
    ContractUploadRecord,
    StatusResponse,
)
from ..utils.error_handler import ContractError

logger = logging.getLogger(__name__)


class ContractService:
    """Service handling contract ingestion, status tracking, listing, and search."""

    def __init__(self) -> None:
        self._db = None
        self._jobs = None
        self._documents = None
        self._vectors = None
        self._ingestor = None

    async def _get_db(self):
        if self._db is None:
            self._db = await get_database()
        return self._db

    async def _get_jobs(self):
        if self._jobs is None:
            db = await self._get_db()
            self._jobs = db.contract_ingest_jobs
        return self._jobs

    async def _get_documents(self):
        if self._documents is None:
            db = await self._get_db()
            self._documents = db.documents
        return self._documents

    async def _get_vectors(self):
        if self._vectors is None:
            db = await self._get_db()
            self._vectors = db.document_vectors
        return self._vectors

    async def _get_ingestor(self):
        if self._ingestor is None:
            db = await self._get_db()
            try:
                from .contracts_ingest import create_contract_ingestor  # Local import to avoid heavy deps at startup
            except Exception as exc:  # pragma: no cover - optional dependency
                logger.error("Contract ingestion unavailable: %s", exc)
                raise ContractError("Contract ingestion dependencies are missing", 500) from exc
            self._ingestor = create_contract_ingestor(db)
        return self._ingestor

    async def update_job_status(
        self,
        upload_id: str,
        status: str,
        *,
        error: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        jobs = await self._get_jobs()
        now = datetime.utcnow()
        
        # Fields that should always be updated
        set_fields: Dict[str, Any] = {
            "status": status,
            "error": error,
            "updatedAt": now,
        }
        
        # Fields that should only be set on insert
        set_on_insert_fields: Dict[str, Any] = {
            "upload_id": upload_id,
            "createdAt": now,
        }
        
        if metadata:
            filtered = {k: v for k, v in metadata.items() if v is not None}
            
            # Fields that should only be set on insert (not updated)
            insert_only_fields = {"saved_path", "createdAt"}
            
            for key, value in filtered.items():
                if key in insert_only_fields:
                    # Only set on insert, don't update existing values
                    set_on_insert_fields[key] = value
                else:
                    # Can be updated on subsequent calls
                    set_fields[key] = value
        
        update_doc: Dict[str, Any] = {
            "$set": set_fields,
            "$setOnInsert": set_on_insert_fields,
        }
        
        await jobs.update_one({"upload_id": upload_id}, update_doc, upsert=True)

    async def ingest_contract(
        self,
        file_path: Path,
        organization_id: str,
        project_id: Optional[str],
        filename: str,
        tags: List[str],
        upload_id: str,
        current_user: Any,
    ) -> Dict[str, Any]:
        ingestor = await self._get_ingestor()
        return await ingestor.ingest_file(
            organization_id=organization_id,
            project_id=project_id,
            file_path=str(file_path),
            filename=filename,
            tags=tags,
            upload_id=upload_id,
        )

    async def get_job_status(self, upload_id: str, current_user: Any) -> StatusResponse:
        jobs = await self._get_jobs()
        doc = await jobs.find_one({"upload_id": upload_id})
        if not doc:
            raise ContractError("Upload job not found", 404)
        return StatusResponse(**self._normalize_job(doc))

    async def list_uploads(
        self,
        organization_id: str,
        project_id: Optional[str],
        limit: int,
        skip: int,
    ) -> ContractListResponse:
        jobs = await self._get_jobs()
        criteria: Dict[str, Any] = {"organization_id": organization_id}
        if project_id:
            criteria["project_id"] = project_id
        cursor = (
            jobs.find(criteria)
            .sort([("createdAt", -1), ("updatedAt", -1)])
            .skip(skip)
            .limit(limit)
        )
        records = await cursor.to_list(length=limit)
        total = await jobs.count_documents(criteria)
        uploads = [ContractUploadRecord(**self._normalize_job(record)) for record in records]
        return ContractListResponse(uploads=uploads, count=total)

    async def search_contracts(
        self,
        request: ContractSearchRequest,
        current_user: Any,
    ) -> ContractSearchResponse:
        start_time = time.perf_counter()
        collection = await self._get_vectors()
        query_filter: Dict[str, Any] = {"uploadType": "contract"}
        if request.organization_id:
            query_filter["organization_id"] = request.organization_id
        if request.project_id:
            query_filter["project_id"] = request.project_id
        if request.document_id:
            query_filter["upload_id"] = request.document_id
        if request.tags:
            query_filter["tags"] = {"$all": request.tags}

        text_query = (request.query or "").strip()
        regex = None
        if text_query:
            escaped_terms = [re.escape(term) for term in text_query.split() if term.strip()]
            if escaped_terms:
                regex = "|".join(escaped_terms)
                query_filter["text"] = {"$regex": regex, "$options": "i"}

        sort_fields: List[Any] = [("createdAt", -1)]
        if regex:
            sort_fields = [("clause_start_position", 1), ("createdAt", -1)]

        cursor = (
            collection.find(query_filter)
            .sort(sort_fields)
            .skip(request.skip)
            .limit(request.limit * 3)
        )
        raw_chunks = await cursor.to_list(length=request.limit * 3)

        per_doc_limit = request.chunks_per_doc or request.limit
        max_docs = request.top_docs or None
        per_doc_counter: Dict[str, int] = {}
        normalized: List[Dict[str, Any]] = []
        for chunk in raw_chunks:
            doc_id = str(
                chunk.get("upload_id")
                or chunk.get("document_id")
                or chunk.get("_id", "")
            )
            if max_docs is not None and doc_id not in per_doc_counter and len(per_doc_counter) >= max_docs:
                continue
            current_count = per_doc_counter.get(doc_id, 0)
            if current_count >= per_doc_limit:
                continue
            per_doc_counter[doc_id] = current_count + 1
            score = 0.0
            if regex:
                text_value = chunk.get("text") or ""
                matches = re.findall(regex, text_value, flags=re.IGNORECASE)
                score = float(len(matches))
                chunk["score"] = score
            normalized.append(self._normalize_vector_chunk(chunk))
            if len(normalized) >= request.limit:
                break

        took_ms = int((time.perf_counter() - start_time) * 1000)
        response = ContractSearchResponse(
            results=[ContractClauseChunk(**item) for item in normalized[: request.limit]],
            summary=None,
            ai_summary_title=None,
            took_ms=took_ms,
        )
        return response

    def _normalize_job(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(doc)
        upload_id = data.get("upload_id") or data.get("_id")
        if isinstance(upload_id, ObjectId):
            upload_id = str(upload_id)
        data["upload_id"] = str(upload_id)
        data.setdefault("status", "unknown")
        data.setdefault("tags", [])
        if "created_at" in data and "createdAt" not in data:
            data["createdAt"] = data.pop("created_at")
        if "updated_at" in data and "updatedAt" not in data:
            data["updatedAt"] = data.pop("updated_at")
        return data

    def _normalize_vector_chunk(self, doc: Dict[str, Any]) -> Dict[str, Any]:
        data = dict(doc)
        data.pop("_id", None)
        for key in ("document_id", "upload_id"):
            value = data.get(key)
            if isinstance(value, ObjectId):
                data[key] = str(value)
        if "tags" not in data or data["tags"] is None:
            data["tags"] = []
        score_val = data.get("score")
        if score_val is None:
            score_val = 0.0
        try:
            data["score"] = float(score_val)
        except (TypeError, ValueError):
            data["score"] = 0.0
        return data
