"""Archive uploads skip the letter-number requirement and the processing job."""

from __future__ import annotations

import inspect

from rbac_backend.core.config import Settings
from rbac_backend.routers import documents as documents_module
from rbac_backend.services.archive_policy import ArchiveIntakePolicy


def _create_document_source() -> str:
    return inspect.getsource(documents_module.DocumentController.create_document)


def test_router_consults_the_archive_policy() -> None:
    assert "ArchiveIntakePolicy" in inspect.getsource(documents_module)


def test_letter_number_check_runs_after_mime_detection() -> None:
    source = _create_document_source()

    validate_at = source.index("validate_spooled_upload")
    letter_check_at = source.index("requires_letter_number")

    assert validate_at < letter_check_at, (
        "The letter-number requirement depends on the detected MIME type, which "
        "is not known until the spooled upload has been validated."
    )


def test_the_old_unconditional_letter_check_is_gone() -> None:
    source = _create_document_source()

    assert "not file.filename or not letter_no" not in source, (
        "The unconditional pre-spool letter-number check would reject an "
        "archive before its MIME was ever known."
    )


def test_a_filename_is_still_always_required() -> None:
    # Dropping the letter-number requirement must not drop the filename one.
    source = _create_document_source()

    assert "file.filename" in source


def test_job_creation_is_gated_on_the_archive_policy() -> None:
    assert "creates_processing_job" in _create_document_source()


def test_policy_exempts_archives_end_to_end() -> None:
    policy = ArchiveIntakePolicy(rar_enabled=False)

    assert policy.requires_letter_number("application/zip") is False
    assert policy.creates_processing_job("application/zip") is False
    assert policy.requires_letter_number("application/pdf") is True
    assert policy.creates_processing_job("application/pdf") is True


def test_disabled_rar_is_rejected_by_validation_before_archive_exemption() -> None:
    # A .rar never reaches the exemption: the allowlist rejects it first.
    assert (
        "application/vnd.rar"
        not in Settings(RAR_UPLOAD_ENABLED=False).ALLOWED_DOCUMENT_MIMES
    )


def test_domain_error_group_is_preserved() -> None:
    assert "except (DocumentError, HTTPException)" in _create_document_source()
