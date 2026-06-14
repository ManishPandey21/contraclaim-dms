"""Operational health endpoints for container orchestration."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import tempfile
from typing import Any, Dict

from fastapi import APIRouter, Header, HTTPException, Response, status
from fastapi.responses import PlainTextResponse
from redis.asyncio import Redis

from ..core.config import settings
from ..core.database import get_database
from ..services.runtime_state import get_runtime_state
from ..services.observability import observability_registry

router = APIRouter(tags=["health"])


def _status(ok: bool) -> str:
    return "ok" if ok else "failed"


def _has_metrics_token(x_metrics_token: str | None) -> bool:
    expected = str(settings.METRICS_TOKEN or "").strip()
    return bool(expected and x_metrics_token == expected)


def _health_error(exc: Exception, include_details: bool) -> str:
    return str(exc) if include_details else "check failed"


@router.get("/health/live")
async def liveness() -> Dict[str, Any]:
    return {
        "status": "ok",
        "service": "backend",
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.get("/health")
async def legacy_health() -> Dict[str, Any]:
    """Backward-compatible health endpoint used by existing Compose files."""

    return await liveness()


@router.get("/health/ready")
async def readiness(
    response: Response,
    x_metrics_token: str | None = Header(default=None),
) -> Dict[str, Any]:
    checks: Dict[str, Dict[str, Any]] = {}
    include_details = _has_metrics_token(x_metrics_token)

    mongo_ok = False
    try:
        db = await get_database()
        await db.command("ping")
        mongo_ok = True
    except Exception as exc:
        checks["mongo"] = {"status": "failed", "error": _health_error(exc, include_details)}
    else:
        checks["mongo"] = {"status": "ok"}

    redis_ok = True
    if settings.CONTRACT_QUEUE_ENABLED:
        redis_ok = False
        redis_url = settings.CONTRACT_QUEUE_REDIS_URL or settings.FALKORDB_URL
        try:
            redis = Redis.from_url(
                redis_url,
                decode_responses=True,
                socket_connect_timeout=2.0,
                socket_timeout=2.0,
                retry_on_timeout=False,
            )
            try:
                await redis.ping()
                redis_ok = True
            finally:
                await redis.aclose()
        except Exception as exc:
            checks["contract_queue_redis"] = {
                "status": "failed",
                "error": _health_error(exc, include_details),
            }
        else:
            checks["contract_queue_redis"] = {"status": "ok"}
    else:
        checks["contract_queue_redis"] = {"status": "skipped", "reason": "disabled"}

    runtime_redis_ok = True
    runtime_redis_url = settings.RUNTIME_STATE_REDIS_URL or settings.APP_REDIS_URL
    if runtime_redis_url:
        runtime_redis_ok = False
        try:
            redis = await get_runtime_state().get_redis()
            if redis is None:
                raise RuntimeError("runtime Redis unavailable")
            await redis.ping()
            runtime_redis_ok = True
        except Exception as exc:
            checks["runtime_redis"] = {
                "status": "failed",
                "error": _health_error(exc, include_details),
            }
        else:
            checks["runtime_redis"] = {"status": "ok"}
    else:
        checks["runtime_redis"] = {"status": "skipped", "reason": "not configured"}

    config_ok = True
    try:
        settings.validate_runtime_configuration()
    except Exception as exc:
        config_ok = False
        checks["configuration"] = {
            "status": "failed",
            "error": _health_error(exc, include_details),
        }
    else:
        checks["configuration"] = {"status": "ok"}

    storage_ok = False
    storage_root = settings.SECURE_UPLOADS_DIR or settings.UPLOADS_DIR
    if storage_root:
        try:
            path = Path(storage_root).expanduser().resolve()
            path.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=path, prefix=".ready-", delete=True) as handle:
                handle.write(b"ok")
                handle.flush()
            storage_ok = True
        except Exception as exc:
            checks["local_storage"] = {
                "status": "failed",
                "error": _health_error(exc, include_details),
            }
        else:
            checks["local_storage"] = {"status": "ok"}
            if include_details:
                checks["local_storage"]["path"] = str(path)
    else:
        checks["local_storage"] = {"status": "failed", "error": "No storage directory configured"}

    ready = mongo_ok and redis_ok and runtime_redis_ok and config_ok and storage_ok
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE

    return {
        "status": "ready" if ready else "not_ready",
        "service": "backend",
        "timestamp": datetime.utcnow().isoformat(),
        "checks": checks,
    }


@router.get("/health/observability")
async def observability_health(
    x_metrics_token: str | None = Header(default=None),
) -> Dict[str, Any]:
    if str(settings.ENVIRONMENT).lower() == "production" and not _has_metrics_token(x_metrics_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid metrics token")
    snapshot = observability_registry.snapshot()
    return {
        "status": "ok",
        "service": "backend",
        "timestamp": datetime.utcnow().isoformat(),
        "metrics_enabled": bool(settings.METRICS_ENABLED),
        "snapshot": snapshot,
    }


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(x_metrics_token: str | None = Header(default=None)) -> PlainTextResponse:
    if not settings.METRICS_ENABLED:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Metrics disabled")
    expected = str(settings.METRICS_TOKEN or "").strip()
    if expected and x_metrics_token != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid metrics token")
    return PlainTextResponse(
        observability_registry.render_prometheus(),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
