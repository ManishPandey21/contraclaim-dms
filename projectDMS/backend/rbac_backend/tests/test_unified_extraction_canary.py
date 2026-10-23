"""Canary routing for the unified extraction pipeline.

The safety property: while the global flag is off, only explicitly allowlisted
organisations get `pipeline_version="unified_v1"`, and the decision is recorded
on the job at creation. A worker honours the persisted version rather than
re-deciding, so a canary worker cannot silently upgrade every tenant, and
flipping the allowlist cannot retroactively change work already queued.
"""

from __future__ import annotations

from rbac_backend.core.config import Settings, settings
from rbac_backend.services.pipeline_routing import (
    LEGACY_PIPELINE,
    UNIFIED_PIPELINE,
    resolve_pipeline_version,
    uses_unified_pipeline,
)


def test_unified_extraction_is_off_by_default() -> None:
    assert Settings.model_fields["UNIFIED_EXTRACTION_ENABLED"].default is False


def test_canary_allowlist_is_empty_by_default() -> None:
    default = Settings.model_fields["UNIFIED_EXTRACTION_CANARY_ORG_IDS"].default

    assert default in (None, "", [], set())


def test_no_organisation_is_unified_while_disabled_with_an_empty_allowlist() -> None:
    version = resolve_pipeline_version(
        organization_id="org-1", enabled=False, canary_org_ids=set()
    )

    assert version == LEGACY_PIPELINE


def test_only_allowlisted_organisations_are_unified() -> None:
    allowlist = {"org-demo"}

    assert (
        resolve_pipeline_version(
            organization_id="org-demo", enabled=False, canary_org_ids=allowlist
        )
        == UNIFIED_PIPELINE
    )
    assert (
        resolve_pipeline_version(
            organization_id="org-other", enabled=False, canary_org_ids=allowlist
        )
        == LEGACY_PIPELINE
    )


def test_global_enablement_makes_every_organisation_unified() -> None:
    version = resolve_pipeline_version(
        organization_id="org-anything", enabled=True, canary_org_ids=set()
    )

    assert version == UNIFIED_PIPELINE


def test_a_missing_organisation_is_never_unified() -> None:
    # Fail closed: an unknown scope must not be swept into the canary.
    assert (
        resolve_pipeline_version(
            organization_id=None, enabled=False, canary_org_ids={"org-demo"}
        )
        == LEGACY_PIPELINE
    )


def test_allowlist_matching_is_exact_not_prefix() -> None:
    assert (
        resolve_pipeline_version(
            organization_id="org-demo-2", enabled=False, canary_org_ids={"org-demo"}
        )
        == LEGACY_PIPELINE
    )


def test_a_job_is_read_by_its_persisted_version_not_by_current_config() -> None:
    """Emptying the allowlist must not reclassify work already queued."""
    queued_job = {"pipeline_version": UNIFIED_PIPELINE}

    assert uses_unified_pipeline(queued_job) is True


def test_a_legacy_job_stays_legacy_even_if_the_flag_is_later_enabled() -> None:
    queued_job = {"pipeline_version": LEGACY_PIPELINE}

    assert uses_unified_pipeline(queued_job) is False


def test_a_job_with_no_recorded_version_is_treated_as_legacy() -> None:
    # Every job predating this change: fail closed onto the known-good path.
    assert uses_unified_pipeline({}) is False
    assert uses_unified_pipeline({"pipeline_version": None}) is False


def test_allowlist_parses_from_a_comma_separated_env_value() -> None:
    configured = Settings(UNIFIED_EXTRACTION_CANARY_ORG_IDS="org-a, org-b")

    assert configured.canary_org_id_set() == {"org-a", "org-b"}


def test_an_empty_allowlist_parses_to_no_organisations() -> None:
    assert Settings(UNIFIED_EXTRACTION_CANARY_ORG_IDS="").canary_org_id_set() == set()
    assert settings.canary_org_id_set() == set()


# --- The decision is recorded on the job, not re-derived -------------------

import pytest  # noqa: E402
from bson import ObjectId  # noqa: E402

from rbac_backend.services.document_service import DocumentService  # noqa: E402
from rbac_backend.tests.test_document_processing_jobs import (  # noqa: E402
    FakeDB,
    insert_document,
    make_document,
)


async def _queue(db: FakeDB) -> dict:
    service = DocumentService(db)
    document_id = ObjectId()
    document = make_document(document_id)
    await insert_document(db, document, document_id)
    job_id = await service.queue_document_processing(document, "letter.pdf")
    return await db.document_processing_jobs.find_one({"_id": job_id})


@pytest.mark.asyncio
async def test_a_queued_job_records_legacy_by_default(monkeypatch) -> None:
    monkeypatch.setattr(settings, "UNIFIED_EXTRACTION_ENABLED", False)
    monkeypatch.setattr(settings, "UNIFIED_EXTRACTION_CANARY_ORG_IDS", "")

    job = await _queue(FakeDB())

    assert job["pipeline_version"] == LEGACY_PIPELINE


@pytest.mark.asyncio
async def test_an_allowlisted_organisation_is_recorded_as_unified(monkeypatch) -> None:
    # make_document() belongs to organization "org-1".
    monkeypatch.setattr(settings, "UNIFIED_EXTRACTION_ENABLED", False)
    monkeypatch.setattr(settings, "UNIFIED_EXTRACTION_CANARY_ORG_IDS", "org-1")

    job = await _queue(FakeDB())

    assert job["pipeline_version"] == UNIFIED_PIPELINE


@pytest.mark.asyncio
async def test_a_non_allowlisted_organisation_stays_legacy(monkeypatch) -> None:
    monkeypatch.setattr(settings, "UNIFIED_EXTRACTION_ENABLED", False)
    monkeypatch.setattr(settings, "UNIFIED_EXTRACTION_CANARY_ORG_IDS", "org-other")

    job = await _queue(FakeDB())

    assert job["pipeline_version"] == LEGACY_PIPELINE
