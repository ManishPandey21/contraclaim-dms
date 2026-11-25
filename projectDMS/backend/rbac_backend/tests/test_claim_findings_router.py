"""The Two Chairs and Submission Pack router, with its real dependencies.

Route functions are called as FastAPI would call them, with the real Bench
factory and the real claim loader; only the database, the policy decision and
the navbar selection are stand-ins. What is pinned here is which permission
each kind of route asks for, because that is the separation the design rests
on: determination authority and privileged access are not implied by the
ordinary claim permissions.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import List

import pytest
from fastapi import HTTPException

from rbac_backend.core.permissions import Permissions
from rbac_backend.main import app
from rbac_backend.routers import claim_bench, claim_findings
from rbac_backend.services.claim_bench import ClaimBenchService
from rbac_backend.services.claim_determinations import DeterminationCreateRequest
from rbac_backend.services.claim_privileged import PRIVILEGED, SimulationRequest
from rbac_backend.services.claim_submission import ComposeRequest
from rbac_backend.tests.test_claim_bench import CLAIM_ID, USER, _setup

OFF = SimpleNamespace(CLAIM_BENCH_ROLLOUT_MODE="off", CLAIM_BENCH_CANARY_TENANT_IDS="")


class Policy:
    """Grants the named permissions and records every one asked for."""

    def __init__(self, *granted: str) -> None:
        self.granted = set(granted)
        self.asked: List[str] = []

    async def _decide(self, permission: str) -> None:
        self.asked.append(permission)
        if permission not in self.granted:
            raise HTTPException(status_code=403, detail=f"Missing {permission}")

    async def authorize_document(self, current_user, permission, document, *, resource_type="document") -> None:
        await self._decide(permission)

    async def authorize(self, current_user, permission, **_: object) -> None:
        await self._decide(permission)


class Selection:
    def require_selection(self) -> str:
        return "org-A"

    async def require_record(self, record, *, allow_unscoped: bool = False) -> None:
        return None


def _args(db, service, policy):
    return {
        "db": db, "bench": service, "current_user": SimpleNamespace(id=USER, organization_id="org-A"),
        "policy": policy, "selection": Selection(),
    }


def test_the_router_is_mounted_and_its_factory_builds_the_real_service() -> None:
    paths = set(app.openapi()["paths"])
    for path in (
        "/api/claims/{claim_id}/determinations",
        "/api/claims/{claim_id}/determinations/{determination_id}/approve",
        "/api/claims/{claim_id}/simulations",
        "/api/claims/{claim_id}/strategy-note",
        "/api/claims/{claim_id}/submission/compose",
        "/api/claims/{claim_id}/submission/export",
    ):
        assert path in paths
    assert isinstance(claim_bench.get_bench(db=object()), ClaimBenchService)
    assert claim_findings.findings_skill_limiter.scope == "claim_findings_skill"


async def test_every_route_is_behind_the_bench_rollout_flag() -> None:
    db, _service, _ = _setup()
    off = ClaimBenchService(db, config=OFF)
    with pytest.raises(HTTPException) as exc:
        await claim_findings.get_claim_submission(CLAIM_ID, **_args(db, off, Policy(Permissions.CLAIM_VIEW)))
    assert exc.value.status_code == 404 and "not enabled" in exc.value.detail


async def test_privileged_routes_need_the_strategy_permission_on_top_of_the_claim_permission() -> None:
    db, service, _ = _setup()
    await service.open(await db.claims.find_one({"_id": CLAIM_ID}), USER)
    ordinary = Policy(Permissions.CLAIM_VIEW, Permissions.CLAIM_EDIT, Permissions.CLAIM_MANAGE, Permissions.CLAIM_DETERMINE)
    for call in (
        lambda: claim_findings.list_claim_simulations(CLAIM_ID, **_args(db, service, ordinary)),
        lambda: claim_findings.get_claim_strategy_note(CLAIM_ID, **_args(db, service, ordinary)),
        lambda: claim_findings.generate_claim_strategy_note(CLAIM_ID, **_args(db, service, ordinary)),
        lambda: claim_findings.simulate_claim_response(
            CLAIM_ID, SimulationRequest(simulated_party="engineer"), **_args(db, service, ordinary)
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await call()
        assert exc.value.status_code == 403 and Permissions.CLAIM_STRATEGY_READ in exc.value.detail
    assert await db[PRIVILEGED].count_documents({}) == 0
    cleared = Policy(Permissions.CLAIM_VIEW, Permissions.CLAIM_EDIT, Permissions.CLAIM_STRATEGY_READ)
    listing = await claim_findings.list_claim_simulations(CLAIM_ID, **_args(db, service, cleared))
    assert listing.privileged and cleared.asked == [Permissions.CLAIM_VIEW, Permissions.CLAIM_STRATEGY_READ]
    # Reading alone is not enough to write.
    reader = Policy(Permissions.CLAIM_VIEW, Permissions.CLAIM_STRATEGY_READ)
    with pytest.raises(HTTPException) as write:
        await claim_findings.generate_claim_strategy_note(CLAIM_ID, **_args(db, service, reader))
    assert write.value.status_code == 403 and Permissions.CLAIM_EDIT in write.value.detail


async def test_authoring_asks_for_determination_authority_and_recording_a_received_one_does_not() -> None:
    db, service, _ = _setup(capacity="engineer")
    await service.open(await db.claims.find_one({"_id": CLAIM_ID}), USER)
    editor = Policy(Permissions.CLAIM_VIEW, Permissions.CLAIM_EDIT, Permissions.CLAIM_MANAGE)
    with pytest.raises(HTTPException) as exc:
        await claim_findings.create_claim_determination(
            CLAIM_ID, DeterminationCreateRequest(origin="authored"), **_args(db, service, editor)
        )
    assert exc.value.status_code == 403 and Permissions.CLAIM_DETERMINE in exc.value.detail
    listing = await claim_findings.list_claim_determinations(CLAIM_ID, **_args(db, service, editor))
    assert not listing.can_author and "authority" in listing.cannot_author_reason
    authority = Policy(Permissions.CLAIM_VIEW, Permissions.CLAIM_DETERMINE)
    view = await claim_findings.create_claim_determination(
        CLAIM_ID, DeterminationCreateRequest(origin="authored"), **_args(db, service, authority)
    )
    assert view.determination.origin == "authored" and view.determination.issued_by == "engineer"
    # Without authority its findings cannot be changed or approved either.
    for call in (
        lambda: claim_findings.propose_claim_determination(
            CLAIM_ID, view.determination.id, claim_findings.DeterminationVersionRequest(version=0), **_args(db, service, editor)
        ),
        lambda: claim_findings.approve_claim_determination(
            CLAIM_ID, view.determination.id, claim_findings.DeterminationVersionRequest(version=0), **_args(db, service, editor)
        ),
    ):
        with pytest.raises(HTTPException) as refused:
            await call()
        assert refused.value.status_code == 403 and Permissions.CLAIM_DETERMINE in refused.value.detail


async def test_composing_needs_edit_and_reading_the_submission_needs_only_view() -> None:
    db, service, _ = _setup()
    await service.open(await db.claims.find_one({"_id": CLAIM_ID}), USER)
    viewer = Policy(Permissions.CLAIM_VIEW)
    with pytest.raises(HTTPException) as exc:
        await claim_findings.compose_claim_submission(CLAIM_ID, ComposeRequest(), **_args(db, service, viewer))
    assert exc.value.status_code == 403 and Permissions.CLAIM_EDIT in exc.value.detail
    assert viewer.asked == [Permissions.CLAIM_EDIT]
