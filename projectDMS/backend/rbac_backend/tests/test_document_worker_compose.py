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


def _extractors() -> list[str]:
    return [
        name
        for name, service in _services().items()
        if str(
            (service.get("environment") or {}).get(
                "START_DOCUMENT_EXTRACTION_WORKERS", ""
            )
        ).lower()
        == "true"
    ]


def test_only_the_document_workers_extract() -> None:
    assert sorted(_extractors()) == ["document-worker", "document-worker-canary"]


def test_the_canary_worker_is_scaled_to_zero_by_default() -> None:
    """It exists so a canary window needs no code change - not so it runs."""
    canary = _services()["document-worker-canary"]

    assert canary["deploy"]["replicas"] == "${DOCUMENT_WORKER_CANARY_REPLICAS:-0}"


def test_the_canary_worker_claims_only_unified_jobs() -> None:
    """This is what makes the canary real rather than a label.

    The claim predicate filters on the job's persisted pipeline_version, so a
    canary worker physically cannot pick up another tenant's legacy job.
    """
    env = _services()["document-worker-canary"]["environment"]

    assert env["DOCUMENT_WORKER_PIPELINE_VERSIONS"] == "unified_v1"


def test_the_two_extraction_workers_can_be_given_disjoint_claims() -> None:
    # The default worker's restriction is env-driven so it can be narrowed to
    # legacy_v0 for the window; the canary's is pinned to unified_v1.
    services = _services()
    default_env = services["document-worker"]["environment"]
    canary_env = services["document-worker-canary"]["environment"]

    assert "DOCUMENT_WORKER_PIPELINE_VERSIONS" in default_env
    assert canary_env["DOCUMENT_WORKER_PIPELINE_VERSIONS"] == "unified_v1"
    assert default_env["DOCUMENT_WORKER_PIPELINE_VERSIONS"] != "unified_v1"


def test_the_default_worker_fails_closed_when_the_variable_is_omitted() -> None:
    """An omitted DOCUMENT_WORKER_PIPELINE_VERSIONS must not mean "claim all".

    Empty means unrestricted. If the default worker defaulted to empty, an
    operator who started a canary but forgot to restrict this service would let
    it drain the canary's unified_v1 queue - the exact failure the canary is
    supposed to make impossible.
    """
    value = _services()["document-worker"]["environment"][
        "DOCUMENT_WORKER_PIPELINE_VERSIONS"
    ]

    assert value == "${DOCUMENT_WORKER_PIPELINE_VERSIONS:-legacy_v0}", (
        "the compose default must be legacy_v0, not empty"
    )


def test_no_worker_is_unrestricted_by_default() -> None:
    """Neither extraction service may default to claiming every version."""
    services = _services()

    for name in ("document-worker", "document-worker-canary"):
        value = str(
            services[name]["environment"]["DOCUMENT_WORKER_PIPELINE_VERSIONS"]
        )
        # A bare "${VAR:-}" or literal empty default is unrestricted.
        assert not value.endswith(":-}"), f"{name} defaults to unrestricted claims"
        assert value != "", f"{name} defaults to unrestricted claims"


def test_the_default_workers_configuration_is_disjoint_from_the_canary() -> None:
    """The two default claim sets must not overlap, as configured."""
    services = _services()
    default = services["document-worker"]["environment"][
        "DOCUMENT_WORKER_PIPELINE_VERSIONS"
    ]
    canary = services["document-worker-canary"]["environment"][
        "DOCUMENT_WORKER_PIPELINE_VERSIONS"
    ]

    # Strip the "${VAR:-default}" wrapper down to the effective default.
    default_effective = default.split(":-")[-1].rstrip("}")

    assert default_effective == "legacy_v0"
    assert canary == "unified_v1"
    assert set(default_effective.split(",")).isdisjoint(set(canary.split(",")))


def test_the_global_flag_defaults_to_off_on_every_service() -> None:
    """A configuration omission must never enable unified extraction globally."""
    for name, service in _services().items():
        env = service.get("environment") or {}
        if "UNIFIED_EXTRACTION_ENABLED" not in env:
            continue
        assert env["UNIFIED_EXTRACTION_ENABLED"] == (
            "${UNIFIED_EXTRACTION_ENABLED:-false}"
        ), f"{name} does not default the global unified flag to false"


def test_the_canary_allowlist_defaults_to_empty() -> None:
    """Empty allowlist = no organisation is on the unified pipeline."""
    for name, service in _services().items():
        env = service.get("environment") or {}
        if "UNIFIED_EXTRACTION_CANARY_ORG_IDS" not in env:
            continue
        assert env["UNIFIED_EXTRACTION_CANARY_ORG_IDS"] == (
            "${UNIFIED_EXTRACTION_CANARY_ORG_IDS:-}"
        ), f"{name} does not default the canary allowlist to empty"


def test_the_canary_worker_is_not_a_second_scheduler_owner() -> None:
    assert _services()["document-worker-canary"]["environment"]["RUN_SCHEDULER"] == "false"


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
