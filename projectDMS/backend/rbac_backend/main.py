import asyncio
import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
except ImportError:  # pragma: no cover - optional dependency
    AsyncIOScheduler = None
    CronTrigger = None

from .core.config import settings
from .core.csrf import validate_unsafe_cookie_request
from .routers import (
    ai_assistant,
    auth,
    contact,
    concerns,
    contracts,
    contract_appraisal,
    contract_master,
    claims,
    key_dates,
    variations,
    bank_guarantees,
    sla,
    dashboard,
    deep_planning,
    documents,
    email,
    email_share,
    email_groups,
    folder_structure,
    input_requests,
    letters,
    letter_drafting,
    letter_templates,
    notifications,
    parties,
    organizations,
    permissions,
    profiles,
    projects,
    performance,
    representatives,
    rbac_monetization,
    billing_webhooks,
    reports,
    roles,
    search,
    health,
    smtp_settings,
    sso,
    storage_sync,
    tags,
    tasks,
    users,
    storage_settings,
    retrieval_engine,
)
from .routers.ws import router as ws_router
from .dependencies import get_email_service
from .services.background_jobs import start_background_services, stop_background_services
from .services.contract_ingest_queue import (
    start_contract_ingest_queue,
    stop_contract_ingest_queue,
)
from .services.runtime_state import get_runtime_state
from .services.observability import observability_registry
from .observability.tracing import setup_tracing, current_trace_id


logger = logging.getLogger(__name__)

api_docs_enabled = bool(settings.ENABLE_API_DOCS) and str(settings.ENVIRONMENT).lower() != "production"
app = FastAPI(
    title="ContractDMS",
    version="1.0.0",
    docs_url="/docs" if api_docs_enabled else None,
    redoc_url="/redoc" if api_docs_enabled else None,
    openapi_url="/openapi.json" if api_docs_enabled else None,
)
_loop_handler_installed = False

# Distributed tracing (opt-in; no-op unless OTEL_ENABLED + libs installed).
setup_tracing(app)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS or ["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_context_middleware(request: Request, call_next):
    request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
    started = time.perf_counter()
    try:
        try:
            validate_unsafe_cookie_request(request)
        except Exception as exc:
            from fastapi import HTTPException

            if isinstance(exc, HTTPException):
                duration_ms = round((time.perf_counter() - started) * 1000, 2)
                await observability_registry.record_request(
                    method=request.method,
                    path=request.url.path,
                    status_code=exc.status_code,
                    duration_ms=duration_ms,
                )
                return JSONResponse(
                    status_code=exc.status_code,
                    content={"detail": exc.detail},
                    headers={"X-Request-ID": request_id},
                )
            raise
        response = await call_next(request)
    except Exception:
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        await observability_registry.record_request(
            method=request.method,
            path=request.url.path,
            status_code=500,
            duration_ms=duration_ms,
        )
        logger.exception(
            "request failed request_id=%s method=%s path=%s duration_ms=%s",
            request_id,
            request.method,
            request.url.path,
            duration_ms,
        )
        raise
    duration_ms = round((time.perf_counter() - started) * 1000, 2)
    response.headers["X-Request-ID"] = request_id
    await observability_registry.record_request(
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=duration_ms,
    )
    if duration_ms >= int(settings.SLOW_REQUEST_THRESHOLD_MS):
        logger.warning(
            "slow request request_id=%s method=%s path=%s status_code=%s duration_ms=%s threshold_ms=%s",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            settings.SLOW_REQUEST_THRESHOLD_MS,
        )
    logger.info(
        "request completed request_id=%s trace_id=%s method=%s path=%s status_code=%s duration_ms=%s",
        request_id,
        current_trace_id(),
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response


# Include routers
app.include_router(auth.router, prefix="/api", tags=["auth"])
app.include_router(sso.router, prefix="/api", tags=["sso"])
app.include_router(contact.router, prefix="/api", tags=["contact"])
app.include_router(users.router, prefix="/api", tags=["users"])
app.include_router(profiles.router, prefix="/api", tags=["profiles"])
app.include_router(documents.router, prefix="/api", tags=["documents"])
app.include_router(contracts.router, prefix="/api", tags=["contracts"])
app.include_router(claims.router, prefix="/api", tags=["claims"])
app.include_router(contract_appraisal.router, prefix="/api", tags=["contract-appraisal"])
app.include_router(contract_master.router, prefix="/api", tags=["contract-master"])
app.include_router(key_dates.router, prefix="/api", tags=["key-dates"])
app.include_router(variations.router, prefix="/api", tags=["variations"])
app.include_router(bank_guarantees.router, prefix="/api", tags=["bank-guarantees"])
app.include_router(sla.router, prefix="/api", tags=["sla"])
app.include_router(letters.router, prefix="/api", tags=["letters"])
app.include_router(letter_drafting.router, prefix="/api", tags=["letter-drafting"])
app.include_router(letter_drafting.session_router, prefix="/api", tags=["letter-drafting"])
app.include_router(input_requests.router, prefix="/api", tags=["input-requests"])
app.include_router(letter_templates.router, prefix="/api", tags=["letter-templates"])
app.include_router(notifications.router, prefix="/api", tags=["notifications"])
app.include_router(notifications.test_router, prefix="/api", tags=["notifications"])
app.include_router(ai_assistant.router, prefix="/api", tags=["ai-assistant"])
app.include_router(deep_planning.router, prefix="/api", tags=["deep-planning"])
app.include_router(search.router, prefix="/api", tags=["search"])
app.include_router(email_groups.router, prefix="/api", tags=["email-groups"])
app.include_router(email.router, prefix="/api/email/legacy", tags=["email-legacy"])
app.include_router(email_share.router, prefix="/api/email", tags=["email"])
app.include_router(parties.router, prefix="/api", tags=["parties"])
app.include_router(representatives.router, prefix="/api", tags=["representatives"])
app.include_router(concerns.router, prefix="/api", tags=["concerns"])
# Newly added routers to serve Organizations and Projects endpoints
app.include_router(organizations.router, prefix="/api", tags=["organizations"])
app.include_router(projects.router, prefix="/api", tags=["projects"])
app.include_router(roles.router, prefix="/api", tags=["roles"])
app.include_router(permissions.router, prefix="/api", tags=["permissions"])
app.include_router(rbac_monetization.router, prefix="/api", tags=["rbac-monetization"])
app.include_router(billing_webhooks.router, prefix="/api", tags=["billing"])
app.include_router(reports.router, prefix="/api", tags=["reports"])
app.include_router(tags.router, prefix="/api", tags=["tags"])
app.include_router(tasks.router, prefix="/api", tags=["tasks"])
app.include_router(folder_structure.router, prefix="/api", tags=["folder-structure"])
app.include_router(storage_sync.router, prefix="/api", tags=["storage"])
app.include_router(storage_settings.router, prefix="/api", tags=["storage-settings"])
app.include_router(smtp_settings.router, prefix="/api", tags=["smtp-settings"])
app.include_router(retrieval_engine.router, prefix="/api", tags=["retrieval-engine"])
app.include_router(dashboard.router, prefix="/api", tags=["dashboard"])
app.include_router(performance.router, prefix="/api", tags=["performance"])
app.include_router(ws_router)
app.include_router(health.router)

scheduler = AsyncIOScheduler() if AsyncIOScheduler else None


@app.on_event("startup")
async def startup_event() -> None:
    global _loop_handler_installed
    settings.validate_runtime_configuration()
    loop = asyncio.get_running_loop()
    if not _loop_handler_installed:
        previous_handler = loop.get_exception_handler()

        def _connection_reset_filter(loop, context):
            exception = context.get("exception")
            message = context.get("message", "")
            if isinstance(exception, ConnectionResetError) or "ConnectionResetError" in message:
                return
            if previous_handler is not None:
                previous_handler(loop, context)
            else:
                loop.default_exception_handler(context)

        loop.set_exception_handler(_connection_reset_filter)
        _loop_handler_installed = True

    if settings.START_BACKGROUND_SERVICES:
        await start_background_services()
    if settings.START_CONTRACT_QUEUE_WORKERS:
        await start_contract_ingest_queue()

    if scheduler is None or CronTrigger is None:
        return
    email_service = await get_email_service()
    scheduler.add_job(
        email_service.send_daily_digests,
        CronTrigger(hour=9, minute=0),
        id="daily_digests",
    )
    scheduler.add_job(
        email_service.send_weekly_digests,
        CronTrigger(day_of_week="sun", hour=9, minute=0),
        id="weekly_digests",
    )
    from .services.sla_service import run_sla_scan

    scheduler.add_job(
        run_sla_scan,
        CronTrigger(hour=8, minute=0),
        id="sla_deadline_scan",
    )
    from .services.key_date_service import run_key_date_notification_scan

    scheduler.add_job(
        run_key_date_notification_scan,
        CronTrigger(hour=8, minute=15),
        id="key_date_notification_scan",
    )
    from .services.bank_guarantee_service import run_bg_expiry_scan

    scheduler.add_job(
        run_bg_expiry_scan,
        CronTrigger(hour=8, minute=30),
        id="bg_expiry_scan",
    )
    scheduler.start()


@app.on_event("shutdown")
async def shutdown_event() -> None:
    if settings.START_CONTRACT_QUEUE_WORKERS:
        await stop_contract_ingest_queue()
    if settings.START_BACKGROUND_SERVICES:
        await stop_background_services()
    await get_runtime_state().close()
    if scheduler and scheduler.running:
        scheduler.shutdown()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
