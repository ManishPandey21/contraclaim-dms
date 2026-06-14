"""Worker process entrypoint for queue consumers."""

from __future__ import annotations

import asyncio
import logging
import signal

from .core.config import settings
from .services.background_jobs import start_background_services, stop_background_services
from .services.contract_ingest_queue import start_contract_ingest_queue, stop_contract_ingest_queue
from .services.runtime_state import get_runtime_state

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

    if settings.START_BACKGROUND_SERVICES:
        await start_background_services()
    if settings.START_CONTRACT_QUEUE_WORKERS:
        await start_contract_ingest_queue()

    logger.info("Worker process started")
    try:
        await stop_event.wait()
    finally:
        if settings.START_CONTRACT_QUEUE_WORKERS:
            await stop_contract_ingest_queue()
        if settings.START_BACKGROUND_SERVICES:
            await stop_background_services()
        await get_runtime_state().close()
        logger.info("Worker process stopped")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
