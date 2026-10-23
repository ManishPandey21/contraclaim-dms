"""The document extraction loop must be independently switchable.

START_BACKGROUND_SERVICES bundles cleanup, assignment alerts, subscription
lifecycle, and the document loop. Moving OCR off the web tier by flipping that
bundle would silently stop assignment alerts and subscription billing - so the
extraction loop gets its own flag instead.
"""

from __future__ import annotations

import inspect

from rbac_backend.core.config import settings
from rbac_backend.services import background_jobs


def test_setting_exists_and_defaults_to_false() -> None:
    assert hasattr(settings, "START_DOCUMENT_EXTRACTION_WORKERS")
    assert settings.START_DOCUMENT_EXTRACTION_WORKERS in (True, False)


def test_setting_default_is_off_so_the_web_tier_does_not_opt_in_silently() -> None:
    from rbac_backend.core.config import Settings

    field = Settings.model_fields["START_DOCUMENT_EXTRACTION_WORKERS"]

    assert field.default is False


def test_dedicated_start_and_stop_functions_exist() -> None:
    assert inspect.iscoroutinefunction(
        background_jobs.start_document_extraction_workers
    )
    assert inspect.iscoroutinefunction(background_jobs.stop_document_extraction_workers)


def test_background_services_no_longer_starts_the_document_loop() -> None:
    source = inspect.getsource(background_jobs.start_background_services)

    assert "periodic_document_processing_jobs" not in source, (
        "The document loop must start from start_document_extraction_workers, "
        "not from the START_BACKGROUND_SERVICES bundle."
    )


def test_background_services_still_starts_the_other_three_tasks() -> None:
    source = inspect.getsource(background_jobs.start_background_services)

    assert "periodic_cleanup" in source
    assert "periodic_assignment_alerts" in source
    assert "periodic_subscription_lifecycle" in source


def test_document_worker_start_owns_the_document_loop() -> None:
    source = inspect.getsource(background_jobs.start_document_extraction_workers)

    assert "periodic_document_processing_jobs" in source


async def test_start_is_idempotent_so_two_calls_do_not_double_claim() -> None:
    await background_jobs.start_document_extraction_workers()
    try:
        first = background_jobs._document_extraction_task
        await background_jobs.start_document_extraction_workers()
        second = background_jobs._document_extraction_task

        assert first is second
    finally:
        await background_jobs.stop_document_extraction_workers()


async def test_stop_clears_the_task_and_is_safe_to_repeat() -> None:
    await background_jobs.start_document_extraction_workers()
    await background_jobs.stop_document_extraction_workers()

    assert background_jobs._document_extraction_task is None

    # A second stop must not raise.
    await background_jobs.stop_document_extraction_workers()


def test_main_honours_the_new_flag() -> None:
    from rbac_backend import main

    source = inspect.getsource(main)

    assert "START_DOCUMENT_EXTRACTION_WORKERS" in source
    assert "start_document_extraction_workers" in source
    assert "stop_document_extraction_workers" in source


def test_worker_entrypoint_honours_the_new_flag() -> None:
    from rbac_backend import worker

    source = inspect.getsource(worker._run)

    assert "START_DOCUMENT_EXTRACTION_WORKERS" in source
    assert "start_document_extraction_workers" in source
    assert "stop_document_extraction_workers" in source
