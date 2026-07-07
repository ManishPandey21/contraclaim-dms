"""Scheduled (cron) jobs runner — H2.

The APScheduler instance lives here (not inline in main.py) and is gated by the
RUN_SCHEDULER flag. Every job is wrapped in a Mongo leader lock so it fires
exactly once across all web replicas / worker processes.

Best practice: run this in the dedicated worker in production
(RUN_SCHEDULER=true on the worker, false on the web tier). The leader lock is
the safety net if more than one process ever starts the scheduler.
"""

from __future__ import annotations

import logging
from typing import Optional

from ..core.config import settings
from .scheduler_lock import with_leader_lock

logger = logging.getLogger(__name__)

try:  # APScheduler is optional in some minimal envs.
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
except Exception:  # pragma: no cover
    AsyncIOScheduler = None  # type: ignore[assignment]
    CronTrigger = None  # type: ignore[assignment]


async def start_scheduler() -> Optional["AsyncIOScheduler"]:
    """Create, populate and start the scheduler, or return None if disabled."""
    if not settings.RUN_SCHEDULER:
        logger.info("RUN_SCHEDULER disabled; cron jobs will not run in this process")
        return None
    if AsyncIOScheduler is None or CronTrigger is None:
        logger.warning("APScheduler unavailable; cron jobs disabled")
        return None

    from ..dependencies import get_email_service
    from .bank_guarantee_service import run_bg_expiry_scan
    from .insurance_service import run_insurance_expiry_scan
    from .contract_appraisal.service import AppraisalService
    from .key_date_service import run_key_date_notification_scan
    from .legal_word_service import run_legal_word_daily_publish
    from .reference_sync_service import run_reference_sync_reaper
    from .sla_service import run_sla_scan

    ttl = int(settings.SCHEDULER_LOCK_TTL_SECONDS)
    email_service = await get_email_service()

    async def reap_appraisal_jobs() -> int:
        # Fresh service per run; resolves its own DB handle (M1 stuck-job reaper).
        return await AppraisalService().reap_stuck_jobs()

    # (job_id, callable, trigger) — every callable is leader-locked.
    specs = [
        ("daily_digests", email_service.send_daily_digests, CronTrigger(hour=9, minute=0)),
        ("weekly_digests", email_service.send_weekly_digests, CronTrigger(day_of_week="sun", hour=9, minute=0)),
        ("sla_deadline_scan", run_sla_scan, CronTrigger(hour=8, minute=0)),
        ("key_date_notification_scan", run_key_date_notification_scan, CronTrigger(hour=8, minute=15)),
        ("bg_expiry_scan", run_bg_expiry_scan, CronTrigger(hour=8, minute=30)),
        ("insurance_expiry_scan", run_insurance_expiry_scan, CronTrigger(hour=8, minute=35)),
        ("legal_word_daily_publish", run_legal_word_daily_publish, CronTrigger(hour=8, minute=40)),
        ("reference_sync_reaper", run_reference_sync_reaper, CronTrigger(hour=8, minute=45)),
        ("appraisal_stuck_job_reaper", reap_appraisal_jobs, CronTrigger(minute="*/15")),
    ]

    scheduler = AsyncIOScheduler()
    for job_id, func, trigger in specs:
        scheduler.add_job(
            with_leader_lock(job_id, ttl)(func),
            trigger,
            id=job_id,
            max_instances=1,
            coalesce=True,
        )
    scheduler.start()
    logger.info("Scheduler started: %d leader-locked cron jobs", len(specs))
    return scheduler


async def stop_scheduler(scheduler: Optional["AsyncIOScheduler"]) -> None:
    if scheduler is not None and getattr(scheduler, "running", False):
        scheduler.shutdown(wait=False)
