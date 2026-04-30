import asyncio
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
except ImportError:  # pragma: no cover - optional dependency
    AsyncIOScheduler = None
    CronTrigger = None

from .core.config import settings
from .routers import (
    ai_assistant,
    auth,
    contracts,
    dashboard,
    deep_planning,
    documents,
    email_share,
    email_groups,
    letters,
    letter_templates,
    notifications,
    parties,
    organizations,
    permissions,
    profiles,
    projects,
    representatives,
    reports,
    roles,
    storage_sync,
    tags,
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


app = FastAPI(title="ContractDMS", version="1.0.0")
_loop_handler_installed = False

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS or ["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(auth.router, prefix="/api", tags=["auth"])
app.include_router(users.router, prefix="/api", tags=["users"])
app.include_router(profiles.router, prefix="/api", tags=["profiles"])
app.include_router(documents.router, prefix="/api", tags=["documents"])
app.include_router(contracts.router, prefix="/api", tags=["contracts"])
app.include_router(letters.router, prefix="/api", tags=["letters"])
app.include_router(letter_templates.router, prefix="/api", tags=["letter-templates"])
app.include_router(notifications.router, prefix="/api", tags=["notifications"])
app.include_router(ai_assistant.router, prefix="/api", tags=["ai-assistant"])
app.include_router(deep_planning.router, prefix="/api", tags=["deep-planning"])
app.include_router(email_groups.router, prefix="/api", tags=["email-groups"])
app.include_router(email_share.router, prefix="/api/email", tags=["email"])
app.include_router(parties.router, prefix="/api", tags=["parties"])
app.include_router(representatives.router, prefix="/api", tags=["representatives"])
# Newly added routers to serve Organizations and Projects endpoints
app.include_router(organizations.router, prefix="/api", tags=["organizations"])
app.include_router(projects.router, prefix="/api", tags=["projects"])
app.include_router(roles.router, prefix="/api", tags=["roles"])
app.include_router(permissions.router, prefix="/api", tags=["permissions"])
app.include_router(reports.router, prefix="/api", tags=["reports"])
app.include_router(tags.router, prefix="/api", tags=["tags"])
app.include_router(storage_sync.router, prefix="/api", tags=["storage"])
app.include_router(storage_settings.router, prefix="/api", tags=["storage-settings"])
app.include_router(retrieval_engine.router, prefix="/api", tags=["retrieval-engine"])
app.include_router(dashboard.router, prefix="/api", tags=["dashboard"])
app.include_router(ws_router)

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

    await start_background_services()
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
    scheduler.start()


@app.on_event("shutdown")
async def shutdown_event() -> None:
    await stop_contract_ingest_queue()
    await stop_background_services()
    if scheduler and scheduler.running:
        scheduler.shutdown()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
