"""Archives are stored intact: scanned and de-duplicated, never unpacked.

RAR stays off by default. Phase 0 measured that the deployed ClamAV links
libclamunrar and recurses into archives (proven with EICAR in a ZIP), but RAR
itself was never functionally proven - no archiver was available to build a
valid sample. The flag is the gate for that missing proof.
"""

from __future__ import annotations

import pytest

from rbac_backend.core.config import Settings, settings
from rbac_backend.services.archive_policy import ArchiveIntakePolicy
from rbac_backend.utils.file_validation import sniff_mime_from_bytes

ZIP_MAGIC = b"PK\x03\x04" + b"\x00" * 32
RAR4_MAGIC = b"Rar!\x1a\x07\x00" + b"\x00" * 32
RAR5_MAGIC = b"Rar!\x1a\x07\x01\x00" + b"\x00" * 32


def test_zip_is_sniffed() -> None:
    assert sniff_mime_from_bytes(ZIP_MAGIC, "evidence.zip") == "application/zip"


def test_rar_v4_and_v5_are_sniffed() -> None:
    assert sniff_mime_from_bytes(RAR4_MAGIC, "evidence.rar") == "application/vnd.rar"
    assert sniff_mime_from_bytes(RAR5_MAGIC, "evidence.rar") == "application/vnd.rar"


def test_docx_still_wins_over_zip_for_the_same_magic() -> None:
    # DOCX is a zip container; the existing filename-hint branch must still win.
    assert sniff_mime_from_bytes(ZIP_MAGIC, "letter.docx") == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )


def test_zip_without_a_zip_extension_is_still_a_zip() -> None:
    assert sniff_mime_from_bytes(ZIP_MAGIC, "bundle") == "application/zip"


def test_pdf_is_unaffected_by_the_new_branches() -> None:
    assert sniff_mime_from_bytes(b"%PDF-1.7\n", "letter.pdf") == "application/pdf"


def test_zip_is_admitted_by_both_default_allowlists() -> None:
    """The code's own default admits ZIP.

    Asserted against the declared default rather than the `settings` singleton
    on purpose: an environment that sets ALLOWED_DOCUMENT_MIMES explicitly
    overrides the default entirely, so a deployment must add ZIP to its own
    .env before this takes effect. Testing the singleton would pass or fail
    based on the ambient .env rather than on the code.
    """
    document_default = Settings.model_fields["ALLOWED_DOCUMENT_MIMES"].default
    enclosure_default = Settings.model_fields["ALLOWED_ENCLOSURE_MIMES"].default

    assert "application/zip" in document_default
    assert "application/zip" in enclosure_default


def test_env_allowlist_overrides_the_default_entirely() -> None:
    # Documents the deployment consequence above, so it cannot be forgotten.
    configured = Settings(ALLOWED_DOCUMENT_MIMES={"application/pdf"})

    assert configured.ALLOWED_DOCUMENT_MIMES == {"application/pdf"}
    assert "application/zip" not in configured.ALLOWED_DOCUMENT_MIMES


def test_rar_is_disabled_by_default() -> None:
    disabled = Settings(RAR_UPLOAD_ENABLED=False)

    assert "application/vnd.rar" not in disabled.ALLOWED_DOCUMENT_MIMES
    assert "application/vnd.rar" not in disabled.ALLOWED_ENCLOSURE_MIMES
    assert (
        ArchiveIntakePolicy(rar_enabled=False).is_admitted("application/vnd.rar")
        is False
    )


def test_rar_can_be_admitted_only_with_the_explicit_enablement() -> None:
    enabled = Settings(RAR_UPLOAD_ENABLED=True)

    assert "application/vnd.rar" in enabled.ALLOWED_DOCUMENT_MIMES
    assert "application/vnd.rar" in enabled.ALLOWED_ENCLOSURE_MIMES
    assert (
        ArchiveIntakePolicy(rar_enabled=True).is_admitted("application/vnd.rar") is True
    )


def test_an_allowlist_cannot_bypass_the_rar_proof_gate() -> None:
    # Configuring the MIME directly must not sneak RAR past the flag.
    with pytest.raises(ValueError, match="RAR_UPLOAD_ENABLED"):
        Settings(
            RAR_UPLOAD_ENABLED=False,
            ALLOWED_DOCUMENT_MIMES={"application/pdf", "application/vnd.rar"},
        )


def test_policy_identifies_archives() -> None:
    policy = ArchiveIntakePolicy(rar_enabled=False)

    assert policy.is_archive("application/zip") is True
    assert policy.is_archive("application/vnd.rar") is True
    assert policy.is_archive("application/pdf") is False
    assert policy.is_archive(None) is False


def test_archive_detection_tolerates_mime_parameters() -> None:
    assert ArchiveIntakePolicy.is_archive("application/zip; charset=binary") is True


def test_archives_do_not_require_a_letter_number() -> None:
    policy = ArchiveIntakePolicy(rar_enabled=False)

    assert policy.requires_letter_number("application/zip") is False
    assert policy.requires_letter_number("application/pdf") is True


def test_archives_never_create_a_processing_job() -> None:
    policy = ArchiveIntakePolicy(rar_enabled=True)

    assert policy.creates_processing_job("application/zip") is False
    assert policy.creates_processing_job("application/vnd.rar") is False
    assert policy.creates_processing_job("application/pdf") is True


def test_is_archive_is_independent_of_admission() -> None:
    # A RAR is an archive whether or not this deployment admits it; the two
    # questions must not collapse into one.
    policy = ArchiveIntakePolicy(rar_enabled=False)

    assert policy.is_archive("application/vnd.rar") is True
    assert policy.is_admitted("application/vnd.rar") is False
