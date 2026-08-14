"""Compose topology guards for the document-worker service.

RUN_SCHEDULER must stay false here: contract-worker is the single scheduler
owner and is documented as one replica. Two schedulers would double-fire cron
jobs; the leader lock is a safety net, not a licence.
"""

from __future__ import annotations

from pathlib import Path

import yaml

COMPOSE = Path(__file__).resolve().parents[3] / "docker-compose.prod.yml"


def _services() -> dict[str, dict]:
    data = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    return data["services"]


def test_compose_file_is_where_the_test_expects_it() -> None:
    assert COMPOSE.is_file(), f"docker-compose.prod.yml not found at {COMPOSE}"


def test_document_worker_service_exists() -> None:
    assert "document-worker" in _services()


def test_document_worker_runs_the_worker_entrypoint() -> None:
    service = _services()["document-worker"]

    assert service["command"] == ["python", "-m", "rbac_backend.worker"]


def test_document_worker_flag_matrix() -> None:
    env = _services()["document-worker"]["environment"]

    assert env["START_DOCUMENT_EXTRACTION_WORKERS"] == "true"
    assert env["START_BACKGROUND_SERVICES"] == "false"
    assert env["START_CONTRACT_QUEUE_WORKERS"] == "false"
    assert env["RUN_SCHEDULER"] == "false"


def test_backend_keeps_background_services_and_does_not_extract() -> None:
    env = _services()["backend"]["environment"]

    assert env["START_BACKGROUND_SERVICES"] == "true"
    assert env["START_DOCUMENT_EXTRACTION_WORKERS"] == "false"


def test_contract_worker_does_not_also_extract() -> None:
    env = _services()["contract-worker"]["environment"]

    assert env["START_DOCUMENT_EXTRACTION_WORKERS"] == "false"


def test_exactly_one_service_extracts_documents() -> None:
    extractors = [
        name
        for name, service in _services().items()
        if str(
            (service.get("environment") or {}).get(
                "START_DOCUMENT_EXTRACTION_WORKERS", ""
            )
        ).lower()
        == "true"
    ]

    assert extractors == ["document-worker"]


def test_contract_worker_remains_the_only_scheduler_owner() -> None:
    owners = [
        name
        for name, service in _services().items()
        if str((service.get("environment") or {}).get("RUN_SCHEDULER", "")).lower()
        == "true"
    ]

    assert owners == ["contract-worker"]


def test_document_worker_mounts_the_shared_uploads_volume() -> None:
    volumes = _services()["document-worker"]["volumes"]

    assert any(str(volume).startswith("backend_uploads:") for volume in volumes)


def test_document_worker_reaches_the_data_and_egress_networks() -> None:
    # It needs Mongo/Qdrant/Falkor (data-net) and outbound AI calls (egress-net).
    networks = _services()["document-worker"]["networks"]

    assert "data-net" in networks
    assert "egress-net" in networks


def test_document_worker_is_not_exposed_to_the_edge() -> None:
    service = _services()["document-worker"]

    assert "edge-net" not in (service.get("networks") or [])
    assert "ports" not in service
