"""
Observability endpoints and background monitoring for storage synchronisation.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple, List, Set

from bson import ObjectId
from bson.errors import InvalidId

from fastapi import APIRouter, HTTPException, Body, Depends, Query, Request, status

from ..config.document_processing_config import DocumentProcessingConfig
from ..core.database import get_database
from ..core.security import CurrentUser, get_current_user
from ..services.step_up_service import require_step_up
from ..services.falkor_graph_service import FalkorGraphService, FalkorGraphError
from ..services.langchain_vector_service import LangChainVectorService
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
QDRANT_SLOW_THRESHOLD_MS = 7000.0
# A persistent degraded state is re-logged at WARNING at most this often; in
# between, unchanged issues drop to DEBUG so the monitor doesn't spam an
# identical warning every CHECK_INTERVAL_SECONDS (alarm fatigue).
WARN_REPEAT_SECONDS = 3600


def _should_emit_warning(
    signature: Tuple[str, ...],
    prev_signature: Optional[Tuple[str, ...]],
    now: float,
    last_warn_at: float,
) -> bool:
    """WARN when the issue set first appears / changes, or on the heartbeat;
    otherwise an unchanged, still-degraded state stays at DEBUG."""
    return signature != prev_signature or (now - last_warn_at) >= WARN_REPEAT_SECONDS


def _qdrant_hint(config: DocumentProcessingConfig, reason: Optional[str]) -> Optional[str]:
    """Actionable hint for the common Qdrant misconfigurations."""
    url = (config.qdrant_url or "")
    if reason == "qdrant_config_error":
        return config.qdrant_auth_configuration_error
    if reason == "client_init_failed" and config.qdrant_api_key and url.startswith("http://"):
        return (
            "QDRANT_URL is http:// but QDRANT_API_KEY is set - use an https:// URL "
            "or unset QDRANT_API_KEY"
        )
    if reason == "qdrant_client_missing":
        return "install the 'qdrant-client' package"
    return None

_monitor_task: Optional[asyncio.Task] = None


def _require_superadmin(current_user: CurrentUser) -> None:
    roles = {str(role).strip().lower() for role in (current_user.roles or [])}
    if "superadmin" not in roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Superadmin access required.",
        )


async def _require_superadmin_user(
    current_user: CurrentUser = Depends(get_current_user),
) -> CurrentUser:
    _require_superadmin(current_user)
    return current_user


def _user_id(current_user: Optional[CurrentUser]) -> Optional[str]:
    return getattr(current_user, "id", None) if current_user else None


async def _fetch_qdrant_total(
    config: DocumentProcessingConfig,
    method: str = "approx",
) -> Tuple[bool, Optional[int], Optional[str], Dict[str, Any]]:
    auth_error = config.qdrant_auth_configuration_error
    if auth_error:
        return False, None, "qdrant_config_error", {
            "attempts": 0,
            "exact": False,
            "method": method,
            "last_error": auth_error,
        }
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
            client = QdrantClient(**config.qdrant_client_kwargs())
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


async def _fetch_qdrant_document_count(
    config: DocumentProcessingConfig,
    document_id: str,
) -> Optional[int]:
    """Return the exact Qdrant vector count for one document, or None if unavailable."""
    if not config.qdrant_enabled or config.qdrant_auth_configuration_error or QdrantClient is None:
        return None

    def _count() -> Optional[int]:
        try:
            from qdrant_client.http import models as qmodels

            client = QdrantClient(**config.qdrant_client_kwargs())
            response = client.count(
                collection_name=config.qdrant_collection,
                count_filter=_qdrant_document_filter(qmodels, document_id),
                exact=True,
            )
            return int(getattr(response, "count", 0))
        except Exception as exc:  # pragma: no cover - external dependency
            logger.warning("Qdrant document count failed for %s: %s", document_id, exc)
            return None

    return await asyncio.to_thread(_count)


def _qdrant_document_filter(qmodels: Any, document_id: str):
    """Match native and LangChain Qdrant payload layouts without double-counting."""
    match = qmodels.MatchValue(value=document_id)
    return qmodels.Filter(
        should=[
            qmodels.FieldCondition(key="document_id", match=match),
            qmodels.FieldCondition(key="metadata.document_id", match=match),
        ]
    )
def _candidate_field_filters(field: str, raw_id: str) -> List[Dict[str, Any]]:
    """
    Build Mongo filters that try both ObjectId and string representations.

    Production data has historically stored document IDs as ObjectId in
    documents._id and as strings in sync/chunk/vector metadata. Repair and
    reconciliation paths must therefore accept either representation.
    """
    filters: List[Dict[str, Any]] = []
    if raw_id:
        try:
            filters.append({field: ObjectId(raw_id)})
        except (InvalidId, TypeError):
            pass
        filters.append({field: raw_id})
    return filters or [{field: raw_id}]


def _candidate_field_query(field: str, raw_id: str) -> Dict[str, Any]:
    filters = _candidate_field_filters(field, raw_id)
    return filters[0] if len(filters) == 1 else {"$or": filters}


def _candidate_id_filters(raw_id: str) -> List[Dict[str, Any]]:
    return _candidate_field_filters("_id", raw_id)


def _candidate_id_query(raw_id: str) -> Dict[str, Any]:
    return _candidate_field_query("_id", raw_id)


async def _find_document_by_id(db, document_id: str, projection: Optional[Dict[str, Any]] = None):
    return await db.documents.find_one(_candidate_id_query(document_id), projection)


async def _load_target_backlinks(db, target_id: str, cache: Dict[str, Optional[List[Dict[str, Any]]]]) -> Optional[List[Dict[str, Any]]]:
    """
    Retrieve (and cache) the referencedBy entries for a target document.
    """
    if target_id in cache:
        return cache[target_id]

    target_doc = await _find_document_by_id(db, target_id, {"referencedBy": 1})
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


async def _fetch_database_status(db) -> Dict[str, Any]:
    started = time.perf_counter()
    payload: Dict[str, Any] = {
        "mongo": {
            "available": False,
            "latency_ms": None,
            "database_name": getattr(db, "name", None),
            "collections": {},
            "error": None,
        }
    }
    try:
        await db.command("ping")
        latency_ms = (time.perf_counter() - started) * 1000.0
        payload["mongo"].update(
            {
                "available": True,
                "latency_ms": latency_ms,
                "collections": {
                    "documents": await db.documents.count_documents({}),
                    "chunks": await db.chunks.count_documents({}),
                    "document_vectors": await db.document_vectors.count_documents({}),
                    "vector_sync_status": await db.vector_sync_status.count_documents({}),
                    "storage_reconciliation_runs": await db.storage_reconciliation_runs.count_documents({}),
                },
            }
        )
    except Exception as exc:
        payload["mongo"]["error"] = str(exc)
    return payload


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
    doc = await _find_document_by_id(db, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    document_ref_query = _candidate_field_query("document_id", document_id)
    chunks = [c async for c in db.chunks.find(document_ref_query)]
    vector_client = vector_client or VectorClient(config)

    if chunks:
        embedding_client = embedding_client or EmbeddingClient(config)
        texts = [c.get("text_enriched") or c.get("text_original") or c.get("text") or "" for c in chunks]
        vectors = await embedding_client.embed(texts)
        await vector_client.upsert(
            vectors,
            [
                {
                    "chunk_id": c.get("chunk_id"),
                    "document_id": c.get("document_id"),
                    "org_id": c.get("org_id") or c.get("organization_id") or doc.get("organization_id"),
                    "project_id": c.get("project_id") or doc.get("project_id"),
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
            {
                "org_id": doc.get("organization_id"),
                "project_id": doc.get("project_id"),
                "document_id": document_id,
            },
            namespace=None,
        )
        mongo_chunks = len(chunks)
        qdrant_chunks = len(qdrant_ids)
        status = "synced" if qdrant_chunks == mongo_chunks else "mismatch"
    else:
        legacy_vectors = [v async for v in db.document_vectors.find(document_ref_query)]
        payloads: List[Dict[str, Any]] = []
        for index, chunk in enumerate(legacy_vectors):
            text = chunk.get("text") or chunk.get("text_enriched") or chunk.get("text_original") or ""
            if not isinstance(text, str) or not text.strip():
                continue
            organization_id = chunk.get("organization_id") or doc.get("organization_id")
            project_id = chunk.get("project_id") or doc.get("project_id")
            payloads.append(
                {
                    "text": text,
                    "metadata": {
                        "document_id": document_id,
                        "organization_id": organization_id,
                        "org_id": organization_id,
                        "project_id": project_id,
                        "uploadType": chunk.get("uploadType"),
                        "letterNo": chunk.get("letterNo"),
                        "filepath_local": chunk.get("filepath_local"),
                        "filepath_s3": chunk.get("filepath_s3"),
                        "chunk_index": chunk.get("chunk_index", index),
                        "chunk_id": chunk.get("chunk_id") or str(chunk.get("_id") or index),
                        "source": chunk.get("source", "legacy_vector_repair"),
                        "embedding_model": chunk.get("embedding_model"),
                        "embedding_provider": chunk.get("embedding_provider"),
                        "embedding_version": chunk.get("embedding_version"),
                        "chunking_version": chunk.get("chunking_version"),
                    },
                    "checksum": chunk.get("checksum_sha256"),
                }
            )

        if not payloads:
            raise HTTPException(status_code=404, detail="No chunks or legacy vector rows found for document")

        legacy_service = LangChainVectorService(config)
        if not legacy_service.enabled:
            raise HTTPException(status_code=503, detail="Qdrant vector service is not available")
        await legacy_service.replace_document(payloads)
        mongo_chunks = len(payloads)
        qdrant_count = await _fetch_qdrant_document_count(config, document_id)
        qdrant_chunks = int(qdrant_count or 0)
        status = "synced" if qdrant_chunks == mongo_chunks else "mismatch"

    await db.vector_sync_status.update_one(
        {"document_id": document_id},
        {
            "$set": {
                "document_id": document_id,
                "sync_status": status,
                "updatedAt": datetime.utcnow(),
                "mongo_chunks": mongo_chunks,
                "qdrant_chunks": qdrant_chunks,
            },
            "$setOnInsert": {"createdAt": datetime.utcnow()},
        },
        upsert=True,
    )
    return {
        "document_id": document_id,
        "mongo_chunks": mongo_chunks,
        "qdrant_chunks": qdrant_chunks,
        "status": status,
    }


async def _gather_storage_status(method: str = "approx") -> Dict[str, Any]:
    db = await get_database()
    config = DocumentProcessingConfig()
    now = datetime.utcnow()

    # document_vectors can contain legacy/fallback metadata rows without an
    # embedding. Count only rows that can be mirrored into a vector store.
    mongo_vectors = await db.document_vectors.count_documents({"embedding.0": {"$exists": True}})
    status_collection = db.vector_sync_status
    total_tracked = await status_collection.count_documents({})
    pending_syncs = await status_collection.count_documents({"sync_status": {"$in": ["pending", "qdrant_synced"]}})
    mismatch_syncs = await status_collection.count_documents({"sync_status": "mismatch"})
    error_syncs = await status_collection.count_documents({"sync_status": "error"})

    stale_threshold = now - timedelta(minutes=STALE_THRESHOLD_MINUTES)
    stale_entries = await status_collection.count_documents(
        {
            "updatedAt": {"$lt": stale_threshold},
            "sync_status": {"$ne": "synced"},
        }
    )

    refs_with_data = await db.documents.count_documents({"references": {"$exists": True, "$ne": []}})
    missing_backlinks = await _count_missing_backlinks(db)

    qdrant_available, qdrant_total, qdrant_reason, qdrant_stats = await _fetch_qdrant_total(config, method)
    qdrant_latency_ms = qdrant_stats.get("latency_ms")
    qdrant_attempts = int(qdrant_stats.get("attempts") or 0)
    qdrant_retries = max(qdrant_attempts - 1, 0)
    qdrant_exact = bool(qdrant_stats.get("exact"))
    qdrant_error = qdrant_stats.get("last_error")
    falkor_stats = await _fetch_falkor_stats(db)
    database_status = await _fetch_database_status(db)

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
        reason = qdrant_reason or "qdrant_unavailable"
        message = f"qdrant {reason}"
        detail = (qdrant_error or "").strip()
        if detail:
            message += f": {detail[:200]}"
        hint = _qdrant_hint(config, reason)
        if hint:
            message += f" — {hint}"
        issues.append(message)
    if qdrant_latency_ms is not None and qdrant_latency_ms > QDRANT_SLOW_THRESHOLD_MS:
        issues.append(f"qdrant_slow ({int(qdrant_latency_ms)}ms)")
    if qdrant_retries:
        issues.append(f"qdrant_retries ({qdrant_retries})")
    if not database_status.get("mongo", {}).get("available"):
        issues.append("mongodb_unavailable")

    status_label = "healthy" if not issues else "degraded"

    return {
        "status": status_label,
        "issues": issues,
        "timestamp": now.isoformat() + "Z",
        "database": database_status,
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
    method: str = Query("approx", pattern="^(approx|exact)$"),
    current_user: CurrentUser = Depends(_require_superadmin_user),
) -> Dict[str, Any]:
    """
    Return a summary of storage synchronisation health across MongoDB, Qdrant, and FalkorDB.
    """
    try:
        return await _gather_storage_status(method)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Failed to gather storage sync status")
        raise HTTPException(status_code=500, detail=f"Unable to gather storage status: {exc}")


async def _run_storage_monitor() -> None:
    # Log on transition, not on every poll: WARN when the issue set first appears
    # or changes, INFO on recovery, DEBUG while an unchanged problem persists, and
    # a low-frequency WARN heartbeat so a long-standing issue isn't fully silent.
    prev_signature: Optional[Tuple[str, ...]] = None
    last_warn_at = 0.0
    while True:
        try:
            status = await _gather_storage_status()
            issues = status.get("issues", [])
            signature = tuple(issues)
            now = time.monotonic()
            if issues:
                if _should_emit_warning(signature, prev_signature, now, last_warn_at):
                    logger.warning("Storage sync monitor detected issues: %s", "; ".join(issues))
                    last_warn_at = now
                else:
                    logger.debug("Storage sync still degraded: %s", "; ".join(issues))
            elif prev_signature:
                logger.info("Storage sync recovered; all checks healthy")
            else:
                logger.debug(
                    "Storage sync healthy (mongo=%s qdrant=%s)",
                    status["vector_store"]["mongo_chunk_count"],
                    status["vector_store"]["qdrant_chunk_count"],
                )
            prev_signature = signature
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
async def resync_document_vectors(
    document_id: str,
    request: Request,
    current_user: CurrentUser = Depends(_require_superadmin_user),
) -> Dict[str, Any]:
    """
    Resync vectors for a single document from Mongo chunks into Qdrant and update sync status.
    """
    await require_step_up(request, current_user, action="storage.repair")
    db = await get_database()
    config = DocumentProcessingConfig()
    return await _resync_document_vectors(document_id, db, config)


@router.post("/storage-sync/resync-bulk")
async def resync_bulk_vectors(
    request: Request,
    org_id: Optional[str] = Body(None),
    project_id: Optional[str] = Body(None),
    limit: int = Body(25),
    include_synced: bool = Body(False),
    current_user: CurrentUser = Depends(_require_superadmin_user),
) -> Dict[str, Any]:
    """
    Bulk repair: resync vectors for documents under an org/project.
    Defaults to only repairing stale/non-synced documents.
    """
    await require_step_up(request, current_user, action="storage.repair")
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


async def _persist_reconciliation_run(
    db,
    run_type: str,
    result: Dict[str, Any],
    current_user: Optional[CurrentUser],
) -> str:
    run = {
        "run_type": run_type,
        "status": "completed" if not result.get("failed") else "completed_with_failures",
        "org_id": result.get("org_id"),
        "project_id": result.get("project_id"),
        "dry_run": bool(result.get("dry_run")),
        "result": result,
        "created_by": _user_id(current_user),
        "created_at": datetime.utcnow(),
    }
    inserted = await db.storage_reconciliation_runs.insert_one(run)
    return str(inserted.inserted_id)


@router.post("/storage-sync/reconcile")
async def reconcile_vectors(
    request: Request,
    org_id: Optional[str] = Body(None),
    project_id: Optional[str] = Body(None),
    limit: int = Body(50),
    dry_run: bool = Body(False),
    current_user: CurrentUser = Depends(_require_superadmin_user),
) -> Dict[str, Any]:
    """
    Reconcile vector data between MongoDB chunks and Qdrant.
    - Detect missing or extra vectors in Qdrant for non-deleted documents.
    - Re-embed and upsert vectors for documents with mismatches unless dry_run is true.
    """
    await require_step_up(request, current_user, action="storage.repair")
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

        # Load chunk metadata from Mongo. Legacy documents may have
        # document_vectors only; reconcile them by exact document count.
        document_ref_query = _candidate_field_query("document_id", doc_id)
        chunks = [c async for c in db.chunks.find(document_ref_query)]
        legacy_vectors: List[Dict[str, Any]] = []
        compare_by_count_only = False
        if chunks:
            mongo_ids = {str(c.get("chunk_id")) for c in chunks if c.get("chunk_id")}
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
            mongo_count = len(mongo_ids)
            qdrant_count = len(qdrant_ids)
        else:
            legacy_vectors = [v async for v in db.document_vectors.find(document_ref_query)]
            mongo_count = len(
                [
                    v
                    for v in legacy_vectors
                    if isinstance(v.get("text") or v.get("text_enriched") or v.get("text_original"), str)
                    and (v.get("text") or v.get("text_enriched") or v.get("text_original") or "").strip()
                ]
            )
            if mongo_count == 0:
                details.append({"document_id": doc_id, "status": "no_chunks"})
                continue
            qdrant_count_result = await _fetch_qdrant_document_count(config, doc_id)
            if qdrant_count_result is None:
                failures.append({"document_id": doc_id, "error": "qdrant_count_failed"})
                continue
            qdrant_count = int(qdrant_count_result)
            missing_in_qdrant = set(range(max(mongo_count - qdrant_count, 0)))
            extra_in_qdrant = set(range(max(qdrant_count - mongo_count, 0)))
            compare_by_count_only = True

        if not missing_in_qdrant and not extra_in_qdrant:
            in_sync += 1
            details.append({"document_id": doc_id, "status": "in_sync"})
            continue

        if dry_run:
            details.append(
                {
                    "document_id": doc_id,
                    "status": "would_repair",
                    "mongo_chunks": mongo_count,
                    "qdrant_chunks": qdrant_count,
                    "missing_qdrant": len(missing_in_qdrant),
                    "extra_qdrant": len(extra_in_qdrant),
                    "compare_mode": "count" if compare_by_count_only else "chunk_id",
                }
            )
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
                    "compare_mode": "count" if compare_by_count_only else "chunk_id",
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

    result = {
        "requested": limit,
        "scanned": scanned,
        "repaired": repaired,
        "in_sync": in_sync,
        "failed": len(failures),
        "failures": failures,
        "details": details,
        "org_id": org_id,
        "project_id": project_id,
        "dry_run": dry_run,
    }
    result["run_id"] = await _persist_reconciliation_run(
        db, "vector", result, current_user
    )
    return result


@router.post("/storage-sync/reconcile-files")
async def reconcile_files(
    request: Request,
    org_id: Optional[str] = Body(None),
    project_id: Optional[str] = Body(None),
    limit: int = Body(100),
    current_user: CurrentUser = Depends(_require_superadmin_user),
) -> Dict[str, Any]:
    """
    Reconcile document/file metadata in MongoDB.
    This endpoint is intentionally diagnostic: it does not delete records or
    mutate storage. It reports DB/file integrity issues for superadmin review.
    """
    await require_step_up(request, current_user, action="storage.repair")
    if not org_id and not project_id:
        raise HTTPException(
            status_code=400,
            detail="Provide org_id or project_id to scope file reconciliation.",
        )
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=400, detail="Limit must be between 1 and 500.")

    db = await get_database()
    doc_filter: Dict[str, Any] = {"deleted": {"$ne": True}, "is_deleted": {"$ne": True}}
    if org_id:
        doc_filter["organization_id"] = org_id
    if project_id:
        doc_filter["project_id"] = project_id

    details: List[Dict[str, Any]] = []
    counters = {
        "missing_local_files": 0,
        "missing_storage_reference": 0,
        "storage_location_errors": 0,
        "documents_without_chunks": 0,
        "stuck_processing": 0,
        "missing_workspace_metadata": 0,
        "duplicate_file_hash_candidates": 0,
    }

    seen_hashes: Dict[str, str] = {}
    projection = {
        "_id": 1,
        "filename": 1,
        "filepath_local": 1,
        "filepath_s3": 1,
        "storage_locations": 1,
        "status": 1,
        "organization_id": 1,
        "project_id": 1,
        "file_hash": 1,
        "hash": 1,
        "updatedAt": 1,
    }
    cursor = db.documents.find(doc_filter, projection).sort("updatedAt", -1).limit(limit)
    scanned = 0
    async for doc in cursor:
        scanned += 1
        doc_id = str(doc.get("_id") or "")
        issues: List[str] = []
        local_path = doc.get("filepath_local")
        s3_path = doc.get("filepath_s3")
        storage_locations = doc.get("storage_locations") or []
        if local_path and not os.path.exists(str(local_path)):
            counters["missing_local_files"] += 1
            issues.append("missing_local_file")
        if not local_path and not s3_path and not storage_locations:
            counters["missing_storage_reference"] += 1
            issues.append("missing_storage_reference")
        if any(
            isinstance(loc, dict)
            and str(loc.get("status") or "").lower().startswith("error")
            for loc in storage_locations
        ):
            counters["storage_location_errors"] += 1
            issues.append("storage_location_error")
        chunk_count = await db.chunks.count_documents({"document_id": doc_id})
        if chunk_count == 0:
            counters["documents_without_chunks"] += 1
            issues.append("no_chunks")
        if str(doc.get("status") or "").strip().lower() in {"under process", "processing"}:
            counters["stuck_processing"] += 1
            issues.append("processing_status")
        if not doc.get("organization_id") or not doc.get("project_id"):
            counters["missing_workspace_metadata"] += 1
            issues.append("missing_workspace_metadata")
        file_hash = doc.get("file_hash") or doc.get("hash")
        if file_hash:
            if str(file_hash) in seen_hashes:
                counters["duplicate_file_hash_candidates"] += 1
                issues.append("duplicate_file_hash_candidate")
            else:
                seen_hashes[str(file_hash)] = doc_id
        if issues:
            details.append(
                {
                    "document_id": doc_id,
                    "filename": doc.get("filename"),
                    "status": doc.get("status"),
                    "issues": issues,
                    "chunk_count": chunk_count,
                    "filepath_local": local_path,
                    "filepath_s3": s3_path,
                    "storage_locations": storage_locations,
                }
            )

    orphan_chunks = 0
    chunk_cursor = db.chunks.find(
        {},
        {"document_id": 1},
    ).limit(limit * 2)
    async for chunk in chunk_cursor:
        doc_id = str(chunk.get("document_id") or "")
        if not doc_id:
            orphan_chunks += 1
            continue
        if not await _find_document_by_id(db, doc_id, {"_id": 1}):
            orphan_chunks += 1
    counters["orphan_chunks"] = orphan_chunks

    result = {
        "requested": limit,
        "scanned": scanned,
        "failed": 0,
        "issue_counts": counters,
        "details": details,
        "org_id": org_id,
        "project_id": project_id,
        "dry_run": True,
    }
    result["run_id"] = await _persist_reconciliation_run(
        db, "file", result, current_user
    )
    return result
