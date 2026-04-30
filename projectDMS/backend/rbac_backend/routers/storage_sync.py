"""
Observability endpoints and background monitoring for storage synchronisation.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple, List, Set

from bson import ObjectId
from bson.errors import InvalidId

from fastapi import APIRouter, HTTPException, Body, Query

from ..config.document_processing_config import DocumentProcessingConfig
from ..core.database import get_database
from ..services.falkor_graph_service import FalkorGraphService, FalkorGraphError
from ..retrieval.embeddings import EmbeddingClient
from ..retrieval.vector_client import VectorClient

try:
    from qdrant_client import QdrantClient
    from qdrant_client.http.exceptions import UnexpectedResponse
except ImportError:  # pragma: no cover - optional dependency
    QdrantClient = None  # type: ignore[assignment]
    UnexpectedResponse = Exception  # type: ignore[assignment]


router = APIRouter()
logger = logging.getLogger("storage.sync")

CHECK_INTERVAL_SECONDS = 300
STALE_THRESHOLD_MINUTES = 15
QDRANT_HEALTH_MAX_ATTEMPTS = 3
QDRANT_HEALTH_BACKOFF_SECONDS = 0.5
QDRANT_SLOW_THRESHOLD_MS = 2000.0

_monitor_task: Optional[asyncio.Task] = None


async def _fetch_qdrant_total(
    config: DocumentProcessingConfig,
    method: str = "approx",
) -> Tuple[bool, Optional[int], Optional[str], Dict[str, Any]]:
    if not config.qdrant_enabled:
        return False, None, "disabled", {"attempts": 0, "exact": False, "method": method}
    if QdrantClient is None:
        return False, None, "qdrant_client_missing", {"attempts": 0, "exact": False, "method": method}

    exact = method == "exact"

    def _count() -> Dict[str, Any]:
        attempts = 0
        last_error: Optional[str] = None
        latency_ms: Optional[float] = None
        try:
            client = QdrantClient(
                url=config.qdrant_url,
                api_key=config.qdrant_api_key,
                timeout=config.qdrant_timeout,
            )
        except Exception as exc:
            return {
                "available": False,
                "count": None,
                "reason": "client_init_failed",
                "latency_ms": None,
                "attempts": attempts,
                "last_error": str(exc),
                "exact": False,
            }

        for attempt in range(QDRANT_HEALTH_MAX_ATTEMPTS):
            attempts = attempt + 1
            start = time.perf_counter()
            try:
                response = client.count(
                    collection_name=config.qdrant_collection,
                    exact=exact,
                )
                latency_ms = (time.perf_counter() - start) * 1000.0
                return {
                    "available": True,
                    "count": int(getattr(response, "count", 0)),
                    "reason": None,
                    "latency_ms": latency_ms,
                    "attempts": attempts,
                    "last_error": None,
                    "exact": exact,
                    "method": method,
                }
            except UnexpectedResponse as exc:
                latency_ms = (time.perf_counter() - start) * 1000.0
                message = getattr(exc, "response_text", str(exc))
                if "doesn't exist" in message or "Not found" in message:
                    logger.info(
                        "Qdrant collection '%s' missing; treating count as 0",
                        config.qdrant_collection,
                    )
                    return {
                        "available": True,
                        "count": 0,
                        "reason": None,
                        "latency_ms": latency_ms,
                        "attempts": attempts,
                        "last_error": None,
                        "exact": exact,
                        "method": method,
                    }
                last_error = message
            except Exception as exc:  # pragma: no cover - diagnostics only
                latency_ms = (time.perf_counter() - start) * 1000.0
                last_error = str(exc)

            if attempt < QDRANT_HEALTH_MAX_ATTEMPTS - 1:
                time.sleep(QDRANT_HEALTH_BACKOFF_SECONDS * (2**attempt))

        logger.warning(
            "Qdrant count failed after %s attempts: %s",
            attempts,
            last_error,
        )
        return {
            "available": False,
            "count": None,
            "reason": "unreachable",
            "latency_ms": latency_ms,
            "attempts": attempts,
            "last_error": last_error,
            "exact": exact,
            "method": method,
        }

    result = await asyncio.to_thread(_count)
    return (
        bool(result.get("available")),
        result.get("count"),
        result.get("reason"),
        result,
    )


def _candidate_id_filters(raw_id: str) -> List[Dict[str, Any]]:
    """
    Build a set of Mongo filters that try both ObjectId and string representations.
    """
    filters: List[Dict[str, Any]] = []
    if raw_id:
        try:
            filters.append({"_id": ObjectId(raw_id)})
        except (InvalidId, TypeError):
            pass
        filters.append({"_id": raw_id})
    return filters or [{"_id": raw_id}]


async def _load_target_backlinks(db, target_id: str, cache: Dict[str, Optional[List[Dict[str, Any]]]]) -> Optional[List[Dict[str, Any]]]:
    """
    Retrieve (and cache) the referencedBy entries for a target document.
    """
    if target_id in cache:
        return cache[target_id]

    filters = _candidate_id_filters(target_id)
    query: Dict[str, Any]
    if len(filters) == 1:
        query = filters[0]
    else:
        query = {"$or": filters}

    target_doc = await db.documents.find_one(query, {"referencedBy": 1})
    if not target_doc:
        cache[target_id] = None
        return None

    backlinks_raw = target_doc.get("referencedBy") or []
    normalized: List[Dict[str, Any]] = []
    for entry in backlinks_raw:
        if isinstance(entry, dict):
            normalized.append(entry)
        elif hasattr(entry, "model_dump"):
            try:
                normalized.append(entry.model_dump(by_alias=True, exclude_none=True))  # type: ignore[attr-defined]
            except Exception:
                continue
    cache[target_id] = normalized
    return normalized


async def _count_missing_backlinks(db) -> int:
    """
    Count unique documents that are referenced but do not list the source in referencedBy.
    """
    cursor = db.documents.find(
        {"references": {"$exists": True, "$ne": []}},
        {"references": 1},
    )

    missing_targets: Set[str] = set()
    backlink_cache: Dict[str, Optional[List[Dict[str, Any]]]] = {}

    async for doc in cursor:
        source_id = str(doc.get("_id", ""))
        if not source_id:
            continue
        references = doc.get("references") or []
        for ref in references:
            target_id = ref.get("documentId")
            if not target_id:
                continue
            backlink_entries = await _load_target_backlinks(db, target_id, backlink_cache)
            if not backlink_entries:
                missing_targets.add(str(target_id))
                continue

            source_key = (ref.get("source") or "parser").lower()
            if not any(
                (entry.get("documentId") == source_id)
                and (entry.get("source") or "").lower() == source_key
                for entry in backlink_entries
            ):
                missing_targets.add(str(target_id))

    return len(missing_targets)


async def _fetch_falkor_stats(db=None) -> Dict[str, Any]:
    svc = FalkorGraphService()
    if not svc.enabled:
        return {"enabled": False, "available": False, "reason": "disabled"}

    try:
        overall_nodes = 0
        overall_edges = 0
        per_org: Dict[tuple[str, str], Dict[str, Any]] = {}

        def _run_counts() -> None:
            nonlocal overall_nodes, overall_edges, per_org
            try:
                resp_nodes = svc._execute("MATCH (n) RETURN count(n) as nodes")
                rows_nodes = svc._parse_rows(resp_nodes)
                overall_nodes = int(rows_nodes[0].get("nodes", 0)) if rows_nodes else 0
            except Exception:
                overall_nodes = 0
            try:
                resp_edges = svc._execute("MATCH ()-[r]->() RETURN count(r) as edges")
                rows_edges = svc._parse_rows(resp_edges)
                overall_edges = int(rows_edges[0].get("edges", 0)) if rows_edges else 0
            except Exception:
                overall_edges = 0
            try:
                resp = svc._execute(
                    "MATCH (n:Letter) RETURN n.organization_id as org, n.project_id as project, count(n) as nodes"
                )
                rows = svc._parse_rows(resp)
                for row in rows:
                    key = (str(row.get("org") or ""), str(row.get("project") or ""))
                    per_org[key] = {"nodes": int(row.get("nodes", 0)), "edges": 0}
            except Exception:
                per_org = {}
            try:
                resp_e = svc._execute(
                    "MATCH (a:Letter)-[r]->(b:Letter) RETURN a.organization_id as org, a.project_id as project, count(r) as edges"
                )
                rows_e = svc._parse_rows(resp_e)
                for row in rows_e:
                    key = (str(row.get("org") or ""), str(row.get("project") or ""))
                    entry = per_org.get(key) or {"nodes": 0}
                    entry["edges"] = int(row.get("edges", 0))
                    per_org[key] = entry
            except Exception:
                pass

        await asyncio.to_thread(_run_counts)

        per_org_list = []
        for (org, project), val in per_org.items():
            nodes = val.get("nodes", 0) or 0
            edges = val.get("edges", 0) or 0
            avg_degree = (edges * 2 / nodes) if nodes else 0.0
            per_org_list.append(
                {
                    "org": org or None,
                    "project": project or None,
                    "nodes": nodes,
                    "edges": edges,
                    "avg_degree": avg_degree,
                }
            )

        # Attach organization/project names if the database handle was provided
        if db is not None and per_org_list:
            org_ids = {row["org"] for row in per_org_list if row.get("org")}
            project_ids = {row["project"] for row in per_org_list if row.get("project")}

            org_names: Dict[str, str] = {}
            project_names: Dict[str, str] = {}

            if org_ids:
                cursor = db.organizations.find({"_id": {"$in": list(org_ids)}}, {"name": 1})
                async for org in cursor:
                    org_names[str(org["_id"])] = org.get("name") or str(org["_id"])

            if project_ids:
                cursor_p = db.projects.find({"_id": {"$in": list(project_ids)}}, {"name": 1})
                async for proj in cursor_p:
                    project_names[str(proj["_id"])] = proj.get("name") or str(proj["_id"])

            for row in per_org_list:
                if row.get("org"):
                    row["org_name"] = org_names.get(str(row["org"]))
                if row.get("project"):
                    row["project_name"] = project_names.get(str(row["project"]))

    except FalkorGraphError as exc:
        return {"enabled": True, "available": False, "reason": str(exc)}
    except Exception as exc:  # pragma: no cover - defensive
        return {"enabled": True, "available": False, "reason": str(exc)}

    overall_avg = (overall_edges * 2 / overall_nodes) if overall_nodes else 0.0
    return {
        "enabled": True,
        "available": True,
        "nodes": overall_nodes,
        "edges": overall_edges,
        "avg_degree": overall_avg,
        "per_org": per_org_list,
    }


async def _resync_document_vectors(
    document_id: str,
    db,
    config: DocumentProcessingConfig,
    embedding_client: Optional[EmbeddingClient] = None,
    vector_client: Optional[VectorClient] = None,
) -> Dict[str, Any]:
    """
    Re-embed and upsert vectors for a single document into Qdrant, then update sync status.
    Shared by single-doc and bulk repair endpoints.
    """
    doc = await db.documents.find_one({"_id": document_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    chunks = [c async for c in db.chunks.find({"document_id": document_id})]
    if not chunks:
        raise HTTPException(status_code=404, detail="No chunks found for document")

    embedding_client = embedding_client or EmbeddingClient(config)
    vector_client = vector_client or VectorClient(config)

    texts = [c.get("text_enriched") or c.get("text_original") or c.get("text") or "" for c in chunks]
    vectors = await embedding_client.embed(texts)
    await vector_client.upsert(
        vectors,
        [
            {
                "chunk_id": c.get("chunk_id"),
                "document_id": c.get("document_id"),
                "org_id": c.get("org_id"),
                "project_id": c.get("project_id"),
                "page_start": c.get("page_start"),
                "text": c.get("text_original") or c.get("text"),
                "text_enriched": c.get("text_enriched"),
                "tags": c.get("tags", []),
                "embedding_provider": c.get("embedding_provider"),
                "embedding_model": c.get("embedding_model"),
                "embedding_dim": c.get("embedding_dim"),
                "embedding_version": c.get("embedding_version"),
                "chunking_version": c.get("chunking_version"),
            }
            for c in chunks
        ],
        namespace=None,
    )
    qdrant_ids = await vector_client.list_chunk_ids(
        {"org_id": doc.get("organization_id"), "project_id": doc.get("project_id"), "document_id": document_id},
        namespace=None,
    )
    status = "synced" if len(qdrant_ids) == len(chunks) else "mismatch"
    await db.vector_sync_status.update_one(
        {"document_id": document_id},
        {
            "$set": {
                "document_id": document_id,
                "sync_status": status,
                "updatedAt": datetime.utcnow(),
                "mongo_chunks": len(chunks),
                "qdrant_chunks": len(qdrant_ids),
            },
            "$setOnInsert": {"createdAt": datetime.utcnow()},
        },
        upsert=True,
    )
    return {
        "document_id": document_id,
        "mongo_chunks": len(chunks),
        "qdrant_chunks": len(qdrant_ids),
        "status": status,
    }


async def _gather_storage_status(method: str = "approx") -> Dict[str, Any]:
    db = await get_database()
    config = DocumentProcessingConfig()
    now = datetime.utcnow()

    mongo_vectors = await db.document_vectors.count_documents({})
    status_collection = db.vector_sync_status
    total_tracked = await status_collection.count_documents({})
    pending_syncs = await status_collection.count_documents({"sync_status": {"$in": ["pending", "qdrant_synced"]}})
    mismatch_syncs = await status_collection.count_documents({"sync_status": "mismatch"})
    error_syncs = await status_collection.count_documents({"sync_status": "error"})

    stale_threshold = now - timedelta(minutes=STALE_THRESHOLD_MINUTES)
    stale_entries = await status_collection.count_documents({"updatedAt": {"$lt": stale_threshold}})

    refs_with_data = await db.documents.count_documents({"references": {"$exists": True, "$ne": []}})
    missing_backlinks = await _count_missing_backlinks(db)

    qdrant_available, qdrant_total, qdrant_reason, qdrant_stats = await _fetch_qdrant_total(config, method)
    qdrant_latency_ms = qdrant_stats.get("latency_ms")
    qdrant_attempts = int(qdrant_stats.get("attempts") or 0)
    qdrant_retries = max(qdrant_attempts - 1, 0)
    qdrant_exact = bool(qdrant_stats.get("exact"))
    qdrant_error = qdrant_stats.get("last_error")
    falkor_stats = await _fetch_falkor_stats(db)

    # Document totals
    mongo_documents = await db.documents.count_documents({})
    qdrant_documents = await status_collection.count_documents({"qdrant_chunks": {"$gt": 0}})
    falkor_documents = falkor_stats.get("nodes") if isinstance(falkor_stats, dict) else None

    issues: list[str] = []
    if mismatch_syncs:
        issues.append(f"{mismatch_syncs} vector mismatches")
    if error_syncs:
        issues.append(f"{error_syncs} vector sync errors")
    if stale_entries:
        issues.append(f"{stale_entries} stale vector sync entries (> {STALE_THRESHOLD_MINUTES} minutes)")
    if missing_backlinks:
        issues.append(f"{missing_backlinks} reference backlinks missing")
    if config.qdrant_enabled and not qdrant_available:
        issues.append(qdrant_reason or "qdrant_unavailable")
    if qdrant_latency_ms is not None and qdrant_latency_ms > QDRANT_SLOW_THRESHOLD_MS:
        issues.append(f"qdrant_slow ({int(qdrant_latency_ms)}ms)")
    if qdrant_retries:
        issues.append(f"qdrant_retries ({qdrant_retries})")

    status_label = "healthy" if not issues else "degraded"

    return {
        "status": status_label,
        "issues": issues,
        "timestamp": now.isoformat() + "Z",
        "vector_store": {
            "mongo_chunk_count": mongo_vectors,
            "qdrant_chunk_count": qdrant_total,
            "qdrant_available": qdrant_available,
            "qdrant_enabled": config.qdrant_enabled,
            "vector_store_enabled": config.vector_store_enabled,
            "qdrant_latency_ms": qdrant_latency_ms,
            "qdrant_retries": qdrant_retries,
            "qdrant_exact": qdrant_exact,
            "qdrant_error": qdrant_error,
            "qdrant_timeout_s": config.qdrant_timeout,
            "qdrant_method": method,
        },
        "falkor": falkor_stats,
        "sync_status": {
            "tracked_documents": total_tracked,
            "pending": pending_syncs,
            "mismatch": mismatch_syncs,
            "errors": error_syncs,
            "stale": stale_entries,
        },
        "documents": {
            "mongo": mongo_documents,
            "qdrant": qdrant_documents,
            "falkor": falkor_documents,
        },
        "references": {
            "documents_with_references": refs_with_data,
            "documents_missing_backlinks": missing_backlinks,
        },
    }


@router.get("/storage-sync/status", tags=["storage"])
async def storage_sync_status(
    method: str = Query("approx", pattern="^(approx|exact)$")
) -> Dict[str, Any]:
    """
    Return a summary of storage synchronisation health across MongoDB, Qdrant, and FalkorDB.
    """
    try:
        return await _gather_storage_status(method)
    except Exception as exc:
        logger.exception("Failed to gather storage sync status")
        raise HTTPException(status_code=500, detail=f"Unable to gather storage status: {exc}")


async def _run_storage_monitor() -> None:
    while True:
        try:
            status = await _gather_storage_status()
            issues = status.get("issues", [])
            if issues:
                logger.warning("Storage sync monitor detected issues: %s", "; ".join(issues))
            else:
                logger.debug(
                    "Storage sync healthy (mongo=%s qdrant=%s)",
                    status["vector_store"]["mongo_chunk_count"],
                    status["vector_store"]["qdrant_chunk_count"],
                )
        except asyncio.CancelledError:
            break
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("Storage sync monitor iteration failed")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


@router.on_event("startup")
async def start_storage_monitor() -> None:
    global _monitor_task
    if _monitor_task is None:
        _monitor_task = asyncio.create_task(_run_storage_monitor())
        logger.info("Storage sync monitor started (interval=%ss)", CHECK_INTERVAL_SECONDS)


@router.on_event("shutdown")
async def stop_storage_monitor() -> None:
    global _monitor_task
    if _monitor_task:
        _monitor_task.cancel()
        try:
            await _monitor_task
        except asyncio.CancelledError:
            pass
        _monitor_task = None
        logger.info("Storage sync monitor stopped")


@router.post("/storage-sync/resync-doc")
async def resync_document_vectors(document_id: str) -> Dict[str, Any]:
    """
    Resync vectors for a single document from Mongo chunks into Qdrant and update sync status.
    """
    db = await get_database()
    config = DocumentProcessingConfig()
    return await _resync_document_vectors(document_id, db, config)


@router.post("/storage-sync/resync-bulk")
async def resync_bulk_vectors(
    org_id: Optional[str] = Body(None),
    project_id: Optional[str] = Body(None),
    limit: int = Body(25),
    include_synced: bool = Body(False),
) -> Dict[str, Any]:
    """
    Bulk repair: resync vectors for documents under an org/project.
    Defaults to only repairing stale/non-synced documents.
    """
    if not org_id and not project_id:
        raise HTTPException(
            status_code=400,
            detail="Provide org_id or project_id to scope bulk resync.",
        )
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="Limit must be between 1 and 200.")

    db = await get_database()
    config = DocumentProcessingConfig()
    embedding_client: Optional[EmbeddingClient] = None
    vector_client: Optional[VectorClient] = None

    cutoff = datetime.utcnow() - timedelta(minutes=STALE_THRESHOLD_MINUTES)
    doc_filter: Dict[str, Any] = {}
    if org_id:
        doc_filter["organization_id"] = org_id
    if project_id:
        doc_filter["project_id"] = project_id

    cursor = (
        db.documents.find(doc_filter, {"_id": 1, "updatedAt": 1})
        .sort("updatedAt", -1)
        .limit(max(limit * 3, limit))
    )

    processed: List[Dict[str, Any]] = []
    failures: List[Dict[str, Any]] = []
    succeeded = 0

    async for doc in cursor:
        doc_id = str(doc.get("_id"))
        if not doc_id:
            continue

        status = await db.vector_sync_status.find_one({"document_id": doc_id})
        if (
            not include_synced
            and status
            and status.get("sync_status") == "synced"
            and status.get("updatedAt") is not None
            and status.get("updatedAt") > cutoff
        ):
            continue

        try:
            embedding_client = embedding_client or EmbeddingClient(config)
            vector_client = vector_client or VectorClient(config)
            result = await _resync_document_vectors(
                doc_id, db, config, embedding_client, vector_client
            )
            succeeded += 1
            processed.append({"document_id": doc_id, "status": result.get("status")})
        except HTTPException as exc:
            failures.append({"document_id": doc_id, "error": exc.detail})
        except Exception as exc:  # pragma: no cover - defensive
            failures.append({"document_id": doc_id, "error": str(exc)})

        if succeeded + len(failures) >= limit:
            break

    if not processed and not failures:
        raise HTTPException(
            status_code=404,
            detail="No documents matched bulk resync criteria.",
    )

    return {
        "requested": limit,
        "succeeded": succeeded,
        "failed": len(failures),
        "processed": processed,
        "errors": failures,
        "org_id": org_id,
        "project_id": project_id,
    }


@router.post("/storage-sync/reconcile")
async def reconcile_vectors(
    org_id: Optional[str] = Body(None),
    project_id: Optional[str] = Body(None),
    limit: int = Body(50),
) -> Dict[str, Any]:
    """
    Reconcile vector data between MongoDB chunks and Qdrant.
    - Detect missing or extra vectors in Qdrant for non-deleted documents.
    - Re-embed and upsert vectors for documents with mismatches.
    """
    if not org_id and not project_id:
        raise HTTPException(
            status_code=400,
            detail="Provide org_id or project_id to scope reconciliation.",
        )
    if limit < 1 or limit > 200:
        raise HTTPException(status_code=400, detail="Limit must be between 1 and 200.")

    db = await get_database()
    config = DocumentProcessingConfig()
    embedding_client: Optional[EmbeddingClient] = None
    vector_client = VectorClient(config)

    doc_filter: Dict[str, Any] = {"deleted": {"$ne": True}}
    # Some datasets may use is_deleted; keep them included unless explicitly true
    doc_filter["is_deleted"] = {"$ne": True}
    if org_id:
        doc_filter["organization_id"] = org_id
    if project_id:
        doc_filter["project_id"] = project_id

    cursor = (
        db.documents.find(doc_filter, {"_id": 1, "organization_id": 1, "project_id": 1})
        .sort("updatedAt", -1)
        .limit(limit)
    )

    scanned = 0
    repaired = 0
    in_sync = 0
    failures: List[Dict[str, Any]] = []
    details: List[Dict[str, Any]] = []

    async for doc in cursor:
        doc_id = str(doc.get("_id") or "")
        if not doc_id:
            continue
        scanned += 1

        # Load chunk metadata from Mongo
        chunks = [c async for c in db.chunks.find({"document_id": doc_id})]
        if not chunks:
            details.append({"document_id": doc_id, "status": "no_chunks"})
            continue

        mongo_ids = {str(c.get("chunk_id")) for c in chunks if c.get("chunk_id")}

        # Fetch Qdrant chunk IDs for this document
        try:
            qdrant_ids = set(
                await vector_client.list_chunk_ids(
                    {
                        "org_id": doc.get("organization_id"),
                        "project_id": doc.get("project_id"),
                        "document_id": doc_id,
                    },
                    limit=max(1000, len(mongo_ids) + 100),
                )
            )
        except Exception as exc:  # pragma: no cover - external dependency
            failures.append({"document_id": doc_id, "error": f"qdrant_list_failed: {exc}"})
            continue

        missing_in_qdrant = mongo_ids - qdrant_ids
        extra_in_qdrant = qdrant_ids - mongo_ids

        if not missing_in_qdrant and not extra_in_qdrant:
            in_sync += 1
            details.append({"document_id": doc_id, "status": "in_sync"})
            continue

        try:
            embedding_client = embedding_client or EmbeddingClient(config)
            result = await _resync_document_vectors(
                doc_id, db, config, embedding_client, vector_client
            )
            repaired += 1
            details.append(
                {
                    "document_id": doc_id,
                    "status": "repaired",
                    "mongo_chunks": result.get("mongo_chunks"),
                    "qdrant_chunks": result.get("qdrant_chunks"),
                    "missing_qdrant": len(missing_in_qdrant),
                    "extra_qdrant": len(extra_in_qdrant),
                }
            )
        except Exception as exc:  # pragma: no cover - diagnostics
            failures.append(
                {
                    "document_id": doc_id,
                    "error": f"reconcile_failed: {exc}",
                    "missing_qdrant": len(missing_in_qdrant),
                    "extra_qdrant": len(extra_in_qdrant),
                }
            )

    return {
        "requested": limit,
        "scanned": scanned,
        "repaired": repaired,
        "in_sync": in_sync,
        "failed": len(failures),
        "failures": failures,
        "details": details,
        "org_id": org_id,
        "project_id": project_id,
    }
