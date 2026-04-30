"""
FalkorDB integration checks using the project's runtime configuration.

Run with:
    cd backend
    python scripts/test_falkordb_connection.py  # uses pytest under the hood
"""

from __future__ import annotations

import os
import sys
from typing import Iterator

import pytest
from dotenv import load_dotenv

# Ensure the repository root is available for imports
SCRIPT_DIR = os.path.abspath(os.path.dirname(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

# Load both root and backend .env files so settings align with the web app
ROOT_ENV = os.path.abspath(os.path.join(PROJECT_ROOT, "..", ".env"))
BACKEND_ENV = os.path.join(PROJECT_ROOT, ".env")
load_dotenv(ROOT_ENV, override=False)
load_dotenv(BACKEND_ENV, override=False)

from rbac_backend.config.document_processing_config import DocumentProcessingConfig
from rbac_backend.services.falkor_graph_service import (
    FalkorGraphError,
    FalkorGraphService,
    normalize_letter_code,
)


@pytest.fixture(scope="module")
def config() -> DocumentProcessingConfig:
    cfg = DocumentProcessingConfig()
    if not cfg.falkordb_enabled:
        pytest.skip("FalkorDB is disabled in configuration.")
    return cfg


@pytest.fixture(scope="module")
def service(config: DocumentProcessingConfig) -> Iterator[FalkorGraphService]:
    svc = FalkorGraphService()
    if not svc.enabled:
        pytest.skip("FalkorGraphService is not enabled (check FALKORDB_* settings).")

    # Print credentials being used
    print(f"\nFalkorDB connection details:")
    print(f"  Host: {svc.config.host}")
    print(f"  Port: {svc.config.port}")
    print(f"  Graph: {svc.config.graph_name}")
    print(f"  Password: {'*' * len(svc.config.password) if svc.config.password else 'None'}")

    try:
        svc.ensure_schema()
    except FalkorGraphError as exc:
        pytest.skip(f"Unable to connect to FalkorDB: {exc}")
    yield svc
    # No teardown required; individual tests clean up their data.


def test_connection(config: DocumentProcessingConfig, service: FalkorGraphService) -> None:
    client = service._client()  # type: ignore[attr-defined]
    assert client.ping() is True


def test_normalize_letter_code() -> None:
    assert normalize_letter_code("ABC/123") == "abc-123"
    assert normalize_letter_code("  Mixed Case Code  ") == "mixed-case-code"
    assert normalize_letter_code("") == ""


def test_letter_round_trip(service: FalkorGraphService) -> None:
    test_code = "PYTEST-FALKOR-001"
    norm = normalize_letter_code(test_code)

    letter = {
        "code": test_code,
        "normCode": norm,
        "subject": "Pytest Falkor Letter",
        "date": "2025-01-01",
        "direction": "incoming",
        "project": "pytest-project",
    }
    refs = [
        {
            "code": "PYTEST-REF-001",
            "normCode": normalize_letter_code("PYTEST-REF-001"),
            "type": "CITES",
            "source": "pytest",
        }
    ]

    try:
        service.upsert_letter_with_refs(letter, refs)
        fetched = service.get_letter(norm)
        assert fetched is not None
        assert fetched["code"] == test_code
    finally:
        try:
            service._execute(
                "MATCH (l:Letter {normCode:$code}) DETACH DELETE l",
                {"code": norm},
            )
            service._execute(
                "MATCH (l:Letter {normCode:$code}) DETACH DELETE l",
                {"code": refs[0]["normCode"]},
            )
        except Exception:
            pass


def test_thread_depth_zero(service: FalkorGraphService) -> None:
    norm = normalize_letter_code("THREAD-DEPTH-TEST")
    letter = {
        "code": "THREAD-DEPTH-TEST",
        "normCode": norm,
        "subject": "Thread depth test",
        "date": "2025-01-02",
        "direction": "incoming",
        "project": "pytest-thread",
    }

    try:
        service.upsert_letter_with_refs(letter, [])
        thread = service.get_thread(norm, depth=0)
        assert thread, "Thread should return at least the letter node itself."
        assert thread[0]["code"] == letter["code"]
    finally:
        try:
            service._execute(
                "MATCH (l:Letter {normCode:$code}) DETACH DELETE l",
                {"code": norm},
            )
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))