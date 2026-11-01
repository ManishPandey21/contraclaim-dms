"""The backend owns the supported-file policy; the client renders it."""

from __future__ import annotations

from rbac_backend.core.config import settings
from rbac_backend.services.upload_policy import (
    MIME_EXTENSIONS,
    UploadPolicyService,
)


def test_policy_covers_every_surface() -> None:
    policy = UploadPolicyService.get_policy()

    assert set(policy) == {"document", "enclosure", "contract", "version"}


def test_document_policy_matches_the_configured_allowlist() -> None:
    policy = UploadPolicyService.get_policy()["document"]

    assert set(policy["mimes"]) == set(settings.ALLOWED_DOCUMENT_MIMES)
    assert policy["max_size_mb"] == settings.GENERAL_UPLOAD_MAX_FILE_SIZE_MB


def test_enclosure_policy_matches_its_own_allowlist() -> None:
    policy = UploadPolicyService.get_policy()["enclosure"]

    assert set(policy["mimes"]) == set(settings.ALLOWED_ENCLOSURE_MIMES)


def test_contract_policy_matches_its_own_allowlist() -> None:
    policy = UploadPolicyService.get_policy()["contract"]

    assert set(policy["mimes"]) == set(settings.ALLOWED_CONTRACT_MIMES)


def test_every_allowed_mime_has_at_least_one_extension() -> None:
    policy = UploadPolicyService.get_policy()

    for surface, entry in policy.items():
        for mime in entry["mimes"]:
            assert MIME_EXTENSIONS.get(mime), (
                f"{surface}: no extension mapped for {mime}"
            )


def test_extensions_are_dot_prefixed_and_lowercase() -> None:
    policy = UploadPolicyService.get_policy()["document"]

    for extension in policy["extensions"]:
        assert extension.startswith(".")
        assert extension == extension.lower()


def test_extensions_are_deduplicated_and_sorted() -> None:
    extensions = UploadPolicyService.get_policy()["document"]["extensions"]

    assert extensions == sorted(set(extensions))


def test_archive_extensions_follow_the_fail_closed_config() -> None:
    extensions = UploadPolicyService.get_policy()["document"]["extensions"]

    assert (".rar" in extensions) is settings.RAR_UPLOAD_ENABLED


def test_unsupported_types_are_absent() -> None:
    # The client picker historically offered .doc/.docx/.gif, which the backend
    # rejects with 415. The served policy must not reintroduce them.
    extensions = UploadPolicyService.get_policy()["document"]["extensions"]

    assert ".doc" not in extensions
    assert ".gif" not in extensions


def test_client_fallback_list_is_a_subset_of_the_served_policy() -> None:
    """The offline fallback must never offer more than the backend accepts.

    This is the guard against the drift that existed before: the picker offered
    .doc/.docx/.gif and the backend answered 415 after the upload.
    """
    import re
    from pathlib import Path

    api_source = (
        Path(__file__).resolve().parents[3]
        / "client"
        / "src"
        / "services"
        / "enhanced-api.ts"
    ).read_text(encoding="utf-8")

    match = re.search(
        r"FALLBACK_UPLOAD_EXTENSIONS\s*(?::[^=]+)?=\s*\[(.*?)\]",
        api_source,
        re.DOTALL,
    )
    assert match, "FALLBACK_UPLOAD_EXTENSIONS not found in enhanced-api.ts"

    # Parsed by extracting quoted literals rather than as JSON: the TypeScript
    # array is prettier-formatted with a trailing comma, which JSON rejects.
    fallback = set(re.findall(r"""["']([^"']+)["']""", match.group(1)))
    assert fallback, "FALLBACK_UPLOAD_EXTENSIONS parsed as empty"
    served = set(UploadPolicyService.get_policy()["document"]["extensions"])

    assert fallback <= served, f"client offers what the backend rejects: {fallback - served}"
