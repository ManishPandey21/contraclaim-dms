"""Worker process entrypoint for queue consumers."""

from __future__ import annotations

import asyncio
import logging
import signal

from .core.config import settings
from .core.database import connect as connect_database, disconnect as disconnect_database
from .services.background_jobs import start_background_services, stop_background_services
from .services.contract_ingest_queue import start_contract_ingest_queue, stop_contract_ingest_queue
from .services.letter_drafting.drafting_queue import start_drafting_queue, stop_drafting_queue
from .services.runtime_state import get_runtime_state
from .services.scheduler import start_scheduler, stop_scheduler

logger = logging.getLogger(__name__)


async def _run() -> None:
    settings.validate_runtime_configuration()
    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    # Establish the DB connection (also builds indexes) before running jobs.
    await connect_database()

    if settings.START_BACKGROUND_SERVICES:
        await start_background_services()
    if settings.START_CONTRACT_QUEUE_WORKERS:
        await start_contract_ingest_queue()
    if settings.START_DRAFTING_QUEUE_WORKERS:
        await start_drafting_queue()
    # H2: run the leader-locked cron scheduler here (set RUN_SCHEDULER=true on
    # the worker, false on the web tier, for a clean single-owner setup).
    scheduler = await start_scheduler()

    logger.info("Worker process started")
    try:
        await stop_event.wait()
    finally:
        await stop_scheduler(scheduler)
        if settings.START_CONTRACT_QUEUE_WORKERS:
            await stop_contract_ingest_queue()
        if settings.START_DRAFTING_QUEUE_WORKERS:
            await stop_drafting_queue()
        if settings.START_BACKGROUND_SERVICES:
            await stop_background_services()
        await disconnect_database()
        await get_runtime_state().close()
        logger.info("Worker process stopped")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
