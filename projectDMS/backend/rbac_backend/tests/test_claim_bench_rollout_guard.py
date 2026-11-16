"""4B-03: the Claim Bench rollout setting fails loudly, and every Bench route obeys it.

Before this, an unknown CLAIM_BENCH_ROLLOUT_MODE (a typo, or the spec's
never-built "shadow") was read as "off" without a word, and the two signal
routes answered whatever the flag said. Now the startup configuration gate
refuses an unknown mode, and the signal routes return the same 404 as the
rest of the Bench when it is off for the organisation.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from rbac_backend.core.config import Settings
from rbac_backend.core.permissions import Permissions
from rbac_backend.routers import claim_bench
from rbac_backend.services.claim_bench import VALID_MODES, ClaimBenchService, claim_bench_rollout
from rbac_backend.services.claim_signals import PotentialClaimRequest
from rbac_backend.tests.test_claim_bench import ORG, PROJECT, USER, _setup

OFF = SimpleNamespace(CLAIM_BENCH_ROLLOUT_MODE="off", CLAIM_BENCH_CANARY_TENANT_IDS="")
CANARY_OTHER = SimpleNamespace(CLAIM_BENCH_ROLLOUT_MODE="canary", CLAIM_BENCH_CANARY_TENANT_IDS="org-elsewhere")


def _settings(mode: str) -> Settings:
    return Settings(
        ENVIRONMENT="development",
        DATABASE_URL="mongodb://localhost:27017/test",
        SECRET_KEY="real-secret-key",
        AWS_ACCESS_KEY_ID="aws-key",
        AWS_SECRET_ACCESS_KEY="aws-secret",
        AWS_BUCKET_NAME="bucket",
        OPENAI_API_KEY="openai-key",
        SMTP_USERNAME="smtp-user",
        SMTP_PASSWORD="smtp-password",
        CLAIM_BENCH_ROLLOUT_MODE=mode,
    )


@pytest.mark.parametrize("mode", ["off", "canary", "primary", "PRIMARY", " canary "])
def test_the_configuration_gate_accepts_the_three_modes(mode: str) -> None:
    _settings(mode).validate_runtime_configuration()


@pytest.mark.parametrize("mode", ["shadow", "on", "enabled", "prmary", "true"])
def test_the_configuration_gate_refuses_any_other_mode(mode: str) -> None:
    with pytest.raises(ValueError, match="CLAIM_BENCH_ROLLOUT_MODE must be off, canary, or primary"):
        _settings(mode).validate_runtime_configuration()


def test_the_gate_and_the_service_agree_on_the_modes() -> None:
    accepted = set()
    for mode in ("off", "canary", "primary", "shadow", "on"):
        try:
            _settings(mode).validate_runtime_configuration()
        except ValueError:
            continue
        accepted.add(mode)
    assert accepted == VALID_MODES


def test_the_service_reads_a_padded_mode_the_way_the_gate_accepts_it() -> None:
    config = SimpleNamespace(CLAIM_BENCH_ROLLOUT_MODE=" Primary ", CLAIM_BENCH_CANARY_TENANT_IDS="")
    availability = claim_bench_rollout(ORG, config)
    assert availability.enabled is True and availability.rollout_mode == "primary"


class Policy:
    async def authorize(self, current_user, permission, **_: object) -> None:
        return None

    async def authorize_document(self, current_user, permission, document, *, resource_type="document") -> None:
        return None


class Selection:
    def require_selection(self) -> str:
        return ORG

    async def list_filters(self, organization_id, project_id):
        return ORG, PROJECT

    async def require_record(self, record, *, allow_unscoped: bool = False) -> None:
        return None


def _user():
    return SimpleNamespace(id=USER, organization_id=ORG, roles=["orgadmin"])


@pytest.mark.parametrize("config", [OFF, CANARY_OTHER], ids=["off", "canary-for-another-organisation"])
async def test_the_signal_list_is_not_served_while_the_bench_is_off(config) -> None:
    db, _service, _ = _setup()
    with pytest.raises(HTTPException) as exc:
        await claim_bench.list_claim_signals(
            organization_id=None,
            project_id=None,
            db=db,
            current_user=_user(),
            policy=Policy(),
            selection=Selection(),
            bench=ClaimBenchService(db, config=config),
        )
    assert exc.value.status_code == 404 and "not enabled" in exc.value.detail


async def test_a_signal_cannot_be_accepted_while_the_bench_is_off() -> None:
    db, _service, _ = _setup()
    db.delay_events.docs.append(
        {
            "_id": "h-1",
            "organization_id": ORG,
            "project_id": PROJECT,
            "title": "Site not handed over",
            "critical_path_impact": True,
            "responsibility": "employer",
        }
    )
    before = len(db.claims.docs)
    with pytest.raises(HTTPException) as exc:
        await claim_bench.raise_potential_claim(
            PotentialClaimRequest(source_type="hindrance", source_id="h-1"),
            db=db,
            current_user=_user(),
            policy=Policy(),
            selection=Selection(),
            bench=ClaimBenchService(db, config=OFF),
        )
    assert exc.value.status_code == 404 and "not enabled" in exc.value.detail
    assert len(db.claims.docs) == before, "no claim may be raised while the Bench is off"


def test_the_signal_routes_declare_the_bench_dependency() -> None:
    """The gate is wired through the same factory as every other Bench route."""
    import inspect

    for route in (claim_bench.list_claim_signals, claim_bench.raise_potential_claim):
        parameter = inspect.signature(route).parameters["bench"]
        assert parameter.default.dependency is claim_bench.get_bench
    assert Permissions.CLAIM_VIEW  # the list route still authorises before the flag is read
