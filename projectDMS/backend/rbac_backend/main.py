import asyncio
import logging
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from .core.config import settings
from .core.csrf import validate_unsafe_cookie_request
from .routers import (
    ai_assistant,
    arbitration_drafting,
    auth,
    chronology,
    client_errors,
    contact,
    concerns,
    contracts,
    contract_appraisal,
    contract_clauses,
    contract_master,
    claims,
    key_dates,
    variations,
    bank_guarantees,
    insurance,
    ipc_bills,
    evidence_graph,
    evidence_registers,
    ipc_categories,
    sla,
    dashboard,
    deep_planning,
    documents,
    email,
    email_share,
    email_groups,
    folder_structure,
    input_requests,
    legal_words,
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
    security_terms,
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
from .services.background_jobs import start_background_services, stop_background_services
from .services.contract_ingest_queue import (
    start_contract_ingest_queue,
    stop_contract_ingest_queue,
)
from .services.runtime_state import get_runtime_state
from .core.database import connect as connect_database, disconnect as disconnect_database
from .services.scheduler import start_scheduler, stop_scheduler
from .services.observability import observability_registry
from .observability.tracing import setup_tracing, current_trace_id


logger = logging.getLogger(__name__)

# Some endpoints are inherently multi-second because they run agentic,
# multi-LLM-call loops (draft + critique + iterative retrieval). Holding them to
# the same 2s slow-request threshold as a plain CRUD call just produces noise, so
# give those paths a higher threshold while everything else stays tight.
SLOW_REQUEST_PATH_OVERRIDES_MS: dict[str, int] = {
    "/api/v1/retrieval/contract-qa": 17000,
    "/api/v1/retrieval/agent": 17000,
    "/api/v1/retrieval/rag": 13000,
}


def _slow_request_threshold_ms(path: str) -> int:
    override = SLOW_REQUEST_PATH_OVERRIDES_MS.get(path)
    if override is not None:
        return override
    return int(settings.SLOW_REQUEST_THRESHOLD_MS)


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
    # Enumerated rather than "*": with credentialed CORS, keep the preflight
    # surface to exactly what the client sends.
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-CSRF-Token",
        "X-Request-ID",
        "X-Requested-With",
        "X-Step-Up-Token",
    ],
    expose_headers=["X-Request-ID", "Content-Disposition"],
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
    slow_threshold_ms = _slow_request_threshold_ms(request.url.path)
    if duration_ms >= slow_threshold_ms:
        logger.warning(
            "slow request request_id=%s method=%s path=%s status_code=%s duration_ms=%s threshold_ms=%s",
            request_id,
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
            slow_threshold_ms,
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
app.include_router(client_errors.router, prefix="/api", tags=["client-errors"])
app.include_router(users.router, prefix="/api", tags=["users"])
app.include_router(profiles.router, prefix="/api", tags=["profiles"])
app.include_router(documents.router, prefix="/api", tags=["documents"])
app.include_router(contracts.router, prefix="/api", tags=["contracts"])
app.include_router(claims.router, prefix="/api", tags=["claims"])
app.include_router(contract_appraisal.router, prefix="/api", tags=["contract-appraisal"])
app.include_router(contract_clauses.router, prefix="/api", tags=["contract-clauses"])
app.include_router(contract_master.router, prefix="/api", tags=["contract-master"])
app.include_router(key_dates.router, prefix="/api", tags=["key-dates"])
app.include_router(variations.router, prefix="/api", tags=["variations"])
app.include_router(bank_guarantees.router, prefix="/api", tags=["bank-guarantees"])
app.include_router(insurance.router, prefix="/api", tags=["insurance"])
app.include_router(ipc_bills.router, prefix="/api", tags=["ipc-bills"])
app.include_router(evidence_graph.router, prefix="/api", tags=["evidence-graph"])
app.include_router(evidence_registers.router, prefix="/api", tags=["evidence-registers"])
app.include_router(arbitration_drafting.router, prefix="/api", tags=["arbitration-drafting"])
app.include_router(chronology.router, prefix="/api", tags=["chronology"])
app.include_router(ipc_categories.router, prefix="/api", tags=["ipc-categories"])
app.include_router(sla.router, prefix="/api", tags=["sla"])
app.include_router(letters.router, prefix="/api", tags=["letters"])
app.include_router(letter_drafting.router, prefix="/api", tags=["letter-drafting"])
app.include_router(letter_drafting.session_router, prefix="/api", tags=["letter-drafting"])
app.include_router(letter_drafting.ops_router, prefix="/api", tags=["letter-drafting-operations"])
app.include_router(input_requests.router, prefix="/api", tags=["input-requests"])
app.include_router(legal_words.router, prefix="/api", tags=["legal-words"])
app.include_router(legal_words.admin_router, prefix="/api", tags=["legal-words-admin"])
app.include_router(letter_templates.router, prefix="/api", tags=["letter-templates"])
app.include_router(notifications.router, prefix="/api", tags=["notifications"])
app.include_router(notifications.test_router, prefix="/api", tags=["notifications"])
app.include_router(ai_assistant.router, prefix="/api", tags=["ai-assistant"])
app.include_router(deep_planning.router, prefix="/api", tags=["deep-planning"])
app.include_router(search.router, prefix="/api", tags=["search"])
app.include_router(security_terms.router, prefix="/api", tags=["security-terms"])
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


@app.on_event("startup")
async def startup_event() -> None:
    global _loop_handler_installed
    settings.validate_runtime_configuration()

    # H1: establish the MongoDB connection and build all indexes (perf indexes,
    # unique constraints for webhook/share-token idempotency, TTL cleanups) and,
    # in production, validate the replica set. Index creation is kicked off as a
    # background task so it never blocks startup. A bad/standalone DB fails fast
    # in production; in dev/test we keep the previous lazy behaviour so the app
    # can start before Mongo is reachable.
    try:
        await connect_database()
    except Exception:
        environment = str(getattr(settings, "ENVIRONMENT", "development") or "development").lower()
        if environment == "production":
            raise
        logger.warning(
            "Database connect() failed at startup; continuing with lazy connection",
            exc_info=True,
        )

    # Seed the permission catalog + grant the superadmin role complete rights so
    # the Permissions page always reflects the full set (incl. the registers)
    # without a manual migration. Best-effort: never block startup.
    try:
        from .services.data_initialization import ensure_permission_catalog_and_superadmin
        await ensure_permission_catalog_and_superadmin()
    except Exception:
        logger.warning("Permission catalog seed at startup failed", exc_info=True)

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

    # H2: cron jobs run via a gated, leader-locked scheduler so they fire once
    # across replicas (see services.scheduler). Disabled when RUN_SCHEDULER=false.
    app.state.scheduler = await start_scheduler()


@app.on_event("shutdown")
async def shutdown_event() -> None:
    if settings.START_CONTRACT_QUEUE_WORKERS:
        await stop_contract_ingest_queue()
    if settings.START_BACKGROUND_SERVICES:
        await stop_background_services()
    await get_runtime_state().close()
    await stop_scheduler(getattr(app.state, "scheduler", None))
    # H1: close the MongoDB client + cancel any in-flight index creation.
    await disconnect_database()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
