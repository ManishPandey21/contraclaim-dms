"""post_deploy_verify.sh must cover the Task 7.6 canary checks, read-only.

These are static checks on the script. They cannot prove it behaves correctly
against a live stack - only an authorised production window can do that - but
they do prove the checks exist, that the script parses, and that it never
mutates anything. A verification script that silently lost a check, or quietly
gained a mutating command, is worse than no script.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "post_deploy_verify.sh"


@pytest.fixture(scope="module")
def script_text() -> str:
    assert SCRIPT.is_file(), f"post_deploy_verify.sh not found at {SCRIPT}"
    return SCRIPT.read_text(encoding="utf-8")


def test_the_script_parses() -> None:
    bash = shutil.which("bash")
    if not bash:
        pytest.skip("bash unavailable")

    result = subprocess.run([bash, "-n", str(SCRIPT)], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr


def test_the_script_stops_on_error(script_text: str) -> None:
    assert "set -euo pipefail" in script_text


#: Every check Task 7.6 Step 6 requires the script to make, with a fragment
#: that must appear for the check to be present.
REQUIRED_CHECKS = {
    "deployed commit": "rev-parse HEAD",
    "deployed branch": "--abbrev-ref HEAD",
    "compose file set": "com.docker.compose.project.config_files",
    "global unified flag": "UNIFIED_EXTRACTION_ENABLED",
    "canary allowlist": "UNIFIED_EXTRACTION_CANARY_ORG_IDS",
    "worker claim restriction": "DOCUMENT_WORKER_PIPELINE_VERSIONS",
    "canary replica count": "canary_replicas",
    "scheduler ownership": "RUN_SCHEDULER",
    "web tier is not an extractor": "START_DOCUMENT_EXTRACTION_WORKERS",
    "page evidence index": "document_ocr_pages",
    "extraction head index": "document_extraction_heads",
    "task 7.6 migration": "20260814_0001",
    "archive rar policy": "RAR_UPLOAD_ENABLED",
    "archive mime policy": "ALLOWED_DOCUMENT_MIMES",
    "disjoint claim domains": "disjoint",
    # R-A8Y: Gate 5 live antivirus - loaded signature age, clean scan, EICAR.
    "clamav readiness": "clamav_readiness_check",
    "clamav readiness library": "scripts/lib/clamav_readiness.sh",
}


@pytest.mark.parametrize("label,fragment", sorted(REQUIRED_CHECKS.items()))
def test_required_check_is_present(script_text: str, label: str, fragment: str) -> None:
    assert fragment in script_text, f"post_deploy_verify.sh lost the {label} check"


def test_an_unrestricted_worker_during_canary_is_a_hard_failure(
    script_text: str,
) -> None:
    """Not a warning. An unrestricted worker defeats the whole isolation
    property, so it must fail the gate.
    """
    assert re.search(
        r'fail "Canary mode with an UNRESTRICTED document-worker', script_text
    )


def test_overlapping_claim_domains_are_a_hard_failure(script_text: str) -> None:
    assert re.search(r'fail "Claim domains overlap', script_text)


def test_a_missing_document_worker_is_a_hard_failure(script_text: str) -> None:
    assert re.search(r'fail "No running document-worker', script_text)


def test_the_web_tier_acting_as_an_extractor_is_a_hard_failure(
    script_text: str,
) -> None:
    assert re.search(r"fail \"backend has START_DOCUMENT_EXTRACTION_WORKERS=true",
                     script_text)


#: Commands that would change production state. The script is a verifier.
FORBIDDEN = [
    r"docker compose[^\n|]*\bup\b",
    r"docker compose[^\n|]*\bdown\b",
    r"docker compose[^\n|]*\brestart\b",
    r"docker compose[^\n|]*\bscale\b",
    r"docker compose[^\n|]*\bstop\b",
    r"docker[^\n|]*\bprune\b",
    r"\bcreate_index\(",
    r"\bdrop_index\(",
    r"\binsert_one\(",
    r"\bupdate_one\(",
    r"\bdelete_one\(",
    r"\bdelete_many\(",
    r"\bdrop\(\)",
    r"\brm -rf\b",
]


@pytest.mark.parametrize("pattern", FORBIDDEN)
def test_the_script_never_mutates_production(script_text: str, pattern: str) -> None:
    match = re.search(pattern, script_text)

    assert match is None, (
        f"post_deploy_verify.sh contains a mutating command: {match.group(0)!r}"
    )


def test_the_script_reads_state_from_containers_not_only_from_env_files(
    script_text: str,
) -> None:
    """.env is what someone intended; the container is what is running."""
    assert "docker inspect" in script_text
    assert "container_env" in script_text
