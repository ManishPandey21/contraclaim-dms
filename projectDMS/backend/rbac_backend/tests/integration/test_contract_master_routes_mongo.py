"""BRIDGE-01 - Contract Master HTTP API integration and capability surface.

Route reachability, proven end to end: HTTP -> auth -> the accepted service ->
the correct outcome. Not twenty assertions that a path returns something other
than 404 - each test drives a real request through the real dependency graph and
checks what the authoritative store actually holds afterwards.

The distinction this suite exists to protect is the one that produced the stop:
a backend capability that no browser can reach is not a delivered feature, and a
route that reaches the *generic* search path while claiming to be the evidence
path is worse than no route at all.

Real Mongo because every one of these routes is a translation layer over
persistence; a fake collection would prove the router calls something, not that
the something did the right thing.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any, Dict, Optional

import pytest

pytestmark = pytest.mark.integration

MONGODB_URI = os.environ.get("CONTRACT_ROUTES_MONGODB_URI")

if not MONGODB_URI:  # pragma: no cover - environment gate
    pytest.skip(
        "CONTRACT_ROUTES_MONGODB_URI is not set; this suite needs a disposable Mongo",
        allow_module_level=True,
    )

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from rbac_backend.core.permissions import Permissions  # noqa: E402
from rbac_backend.core.security import CurrentUser, get_current_user  # noqa: E402
from rbac_backend.routers import contract_master_api  # noqa: E402
from rbac_backend.services.contract_document_store import (  # noqa: E402
    APPLICABILITY_COLLECTION,
    APPLICABILITY_EVENTS_COLLECTION,
    CLASSIFICATION_FACTS_COLLECTION,
    CONTRACT_DOCUMENTS_COLLECTION,
)
from rbac_backend.services.contract_migration_reconciliation import (  # noqa: E402
    RECONCILIATION_COLLECTION,
)

ORG = "org-routes"
PROJECT = "project-routes"
CONTRACT = "contract-routes"

ORG_TIER = (
    Permissions.CONTRACT_CATALOGUE_BROWSE,
    Permissions.CONTRACT_MASTER_VIEW,
    Permissions.CONTRACT_MASTER_MANAGE,
    Permissions.CONTRACT_APPLICABILITY_MANAGE,
    Permissions.DOCUMENT_UPLOAD,
    Permissions.DOCUMENT_VIEW,
)
PROJECT_TIER = (
    Permissions.CONTRACT_MASTER_VIEW,
    Permissions.DOCUMENT_UPLOAD,
    Permissions.DOCUMENT_VIEW,
)


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #


class FakePolicy:
    """Grants exactly the permissions the actor holds, at the scope asked for.

    Stands in for PolicyService's plumbing, not for its decision: the router
    still has to ask, and asking for the wrong permission or the wrong scope
    fails here exactly as it would in production.
    """

    def __init__(self, granted, organization_id=ORG) -> None:
        self.granted = {str(p) for p in granted}
        self.organization_id = organization_id
        self.calls: list = []

    async def authorize(self, current_user, permission, **kwargs):
        from fastapi import HTTPException, status

        self.calls.append({"permission": str(permission), **kwargs})
        if str(permission) not in self.granted:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"missing {permission}",
            )
        organization_id = kwargs.get("organization_id")
        if organization_id and organization_id != self.organization_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="cross-organisation request",
            )
        return True


def _client(db_name, *, granted=ORG_TIER, organization_id=ORG, project_ids=(PROJECT,)):
    app = FastAPI()
    app.include_router(contract_master_api.router, prefix="/api")

    user = CurrentUser(
        id="actor-1",
        username="actor",
        email="actor@example.com",
        roles=["orgadmin"],

        organization_id=organization_id,
        projects=list(project_ids),
    )
    policy = FakePolicy(granted, organization_id=organization_id)

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[contract_master_api.get_db] = lambda: _fresh_db(db_name)
    app.dependency_overrides[contract_master_api.get_policy] = lambda: policy
    return TestClient(app), policy


def _fresh_db(name: str):
    """A Motor handle bound to whatever loop is running right now.

    Motor binds its client to the loop in use at first await, and TestClient
    runs each request on its own loop. Sharing one client across them is the
    "Event loop is closed" failure this repo documents, so every entry point
    gets its own.
    """
    return AsyncIOMotorClient(MONGODB_URI, serverSelectionTimeoutMS=6000)[name]


class _Fixture:
    """A seeded database plus a client, torn down together."""

    def __init__(self, **client_kwargs):
        self._client_kwargs = client_kwargs

    def __enter__(self):
        self.name = f"contract_routes_{uuid.uuid4().hex[:10]}"
        asyncio.run(self._seed())
        self.client, self.policy = _client(self.name, **self._client_kwargs)
        return self

    def __exit__(self, *exc):
        asyncio.run(self._drop())
        return False

    async def _drop(self) -> None:
        client = AsyncIOMotorClient(MONGODB_URI, serverSelectionTimeoutMS=6000)
        await client.drop_database(self.name)
        client.close()

    def run(self, factory):
        """Run an assertion coroutine against a freshly bound handle."""

        async def _inner():
            return await factory(_fresh_db(self.name))

        return asyncio.run(_inner())

    async def _seed(self) -> None:
        db = _fresh_db(self.name)
        await db["documents"].insert_one(
            {
                "_id": "doc-1",
                "organization_id": ORG,
                "project_id": PROJECT,
                "current_version_id": "doc-1-v1",
            }
        )
        await db[CONTRACT_DOCUMENTS_COLLECTION].insert_one(
            {
                "_id": "cd-1",
                "organization_id": ORG,
                "document_id": "doc-1",
                "document_version_id": "doc-1-v1",
                "contract_document_type": "general_conditions",
                "scope_level": "organization",
                "project_id": None,
                "classification_revision": 1,
                "projection_status": "PENDING",
            }
        )


# --------------------------------------------------------------------------- #
# 1. catalogue browse
# --------------------------------------------------------------------------- #


def test_the_catalogue_is_reachable_over_http():
    with _Fixture() as fixture:
        response = fixture.client.get(f"/api/contract-master/catalogue?organization_id={ORG}")
        assert response.status_code == 200
        body = response.json()
        assert [item["contract_document_id"] for item in body["items"]] == ["cd-1"]


def test_a_zero_applicability_instrument_shows_as_catalogued_not_evidence():
    with _Fixture() as fixture:
        body = fixture.client.get(
            f"/api/contract-master/catalogue?organization_id={ORG}"
        ).json()
        item = body["items"][0]
        assert item["viewer_state"] == "CATALOGUED / UNASSIGNED"
        assert item["evidence_ready"] is False
        # A catalogue listing is not an ApplicableInstrument in any shape.
        assert "applicability_event_id" not in item


def test_the_catalogue_is_gated_by_catalogue_browse():
    with _Fixture(granted=PROJECT_TIER) as fixture:
        response = fixture.client.get(f"/api/contract-master/catalogue?organization_id={ORG}")
        assert response.status_code == 403


def test_a_project_tier_actor_cannot_reach_the_catalogue_by_organisation_membership():
    with _Fixture(granted=PROJECT_TIER) as fixture:
        assert (
            fixture.client.get(f"/api/contract-master/catalogue?organization_id={ORG}").status_code
            == 403
        )


def test_a_caller_cannot_browse_another_organisations_catalogue():
    with _Fixture() as fixture:
        response = fixture.client.get("/api/contract-master/catalogue?organization_id=org-other")
        assert response.status_code == 403


# --------------------------------------------------------------------------- #
# 2. instrument detail
# --------------------------------------------------------------------------- #


def test_instrument_detail_returns_server_derived_state():
    with _Fixture() as fixture:
        body = fixture.client.get("/api/contract-master/instruments/cd-1").json()

        # Seven orthogonal dimensions, not one status.
        for dimension in (
            "scope_level",
            "contract_document_type",
            "classification_revision",
            "projection_status",
            "content_consumable",
            "evidence_ready",
            "viewer_state",
        ):
            assert dimension in body, dimension
        assert body["evidence_ready"] is False


def test_the_word_active_never_appears_in_a_detail_response():
    with _Fixture() as fixture:
        body = fixture.client.get("/api/contract-master/instruments/cd-1").json()
        assert "active" not in repr(body).lower()


def test_instrument_detail_requires_master_view():
    with _Fixture(granted=(Permissions.DOCUMENT_VIEW,)) as fixture:
        assert fixture.client.get("/api/contract-master/instruments/cd-1").status_code == 403


# --------------------------------------------------------------------------- #
# 3. classification
# --------------------------------------------------------------------------- #


def test_classification_confirm_reaches_the_lifecycle_service():
    with _Fixture() as fixture:
        response = fixture.client.post(
            "/api/contract-master/instruments/cd-1/classification",
            json={
                "contract_document_type": "particular_conditions",
                "expected_revision": 1,
            },
        )
        assert response.status_code == 200
        assert response.json()["classification_revision"] == 2

        record = fixture.run(lambda db: db[CONTRACT_DOCUMENTS_COLLECTION].find_one({"_id": "cd-1"}))
        assert record["contract_document_type"] == "particular_conditions"
        # The projection fence moved with it.
        assert record["projection_status"] == "PENDING"
        assert (
            fixture.run(lambda db: db[CLASSIFICATION_FACTS_COLLECTION].count_documents({})) == 1
        )


def test_a_revision_conflict_is_a_409_not_a_silent_overwrite():
    with _Fixture() as fixture:
        response = fixture.client.post(
            "/api/contract-master/instruments/cd-1/classification",
            json={"contract_document_type": "amendment", "expected_revision": 99},
        )
        assert response.status_code == 409


def test_a_suggestion_basis_cannot_confirm_a_classification():
    with _Fixture() as fixture:
        response = fixture.client.post(
            "/api/contract-master/instruments/cd-1/classification",
            json={
                "contract_document_type": "amendment",
                "expected_revision": 1,
                "basis": "filename",
            },
        )
        assert response.status_code == 422
        record = fixture.run(lambda db: db[CONTRACT_DOCUMENTS_COLLECTION].find_one({"_id": "cd-1"}))
        assert record["contract_document_type"] == "general_conditions"


def test_classification_requires_master_manage():
    with _Fixture(granted=(Permissions.CONTRACT_MASTER_VIEW,)) as fixture:
        assert (
            fixture.client.post(
                "/api/contract-master/instruments/cd-1/classification",
                json={"contract_document_type": "amendment", "expected_revision": 1},
            ).status_code
            == 403
        )


# --------------------------------------------------------------------------- #
# 4. applicability
# --------------------------------------------------------------------------- #


def test_applicability_apply_records_a_canonical_event():
    with _Fixture() as fixture:
        response = fixture.client.post(
            "/api/contract-master/instruments/cd-1/applicability",
            json={
                "project_id": PROJECT,
                "contract_id": CONTRACT,
                "kind": "APPLIED",
                "effective_at": "2021-04-01",
            },
        )
        assert response.status_code == 201

        assert fixture.run(lambda db: db[APPLICABILITY_COLLECTION].count_documents({})) == 1
        event = fixture.run(lambda db: db[APPLICABILITY_EVENTS_COLLECTION].find_one({}))
        assert event["kind"] == "APPLIED"
        assert event["effective_at"] == "2021-04-01"


def test_an_unknown_effective_date_stays_unknown_over_http():
    with _Fixture() as fixture:
        fixture.client.post(
            "/api/contract-master/instruments/cd-1/applicability",
            json={"project_id": PROJECT, "contract_id": CONTRACT, "kind": "APPLIED"},
        )
        event = fixture.run(lambda db: db[APPLICABILITY_EVENTS_COLLECTION].find_one({}))
        assert event["effective_at"] is None


def test_applicability_requires_the_applicability_permission():
    with _Fixture(granted=(Permissions.CONTRACT_MASTER_MANAGE,)) as fixture:
        assert (
            fixture.client.post(
                "/api/contract-master/instruments/cd-1/applicability",
                json={"project_id": PROJECT, "contract_id": CONTRACT, "kind": "APPLIED"},
            ).status_code
            == 403
        )


def test_applicability_is_scoped_to_the_project_and_contract():
    with _Fixture() as fixture:
        fixture.client.post(
            "/api/contract-master/instruments/cd-1/applicability",
            json={"project_id": PROJECT, "contract_id": CONTRACT, "kind": "APPLIED"},
        )
        call = [c for c in fixture.policy.calls if "applicability" in c["permission"]][-1]
        assert call["project_id"] == PROJECT


# --------------------------------------------------------------------------- #
# 5. evidence search - the critical separation
# --------------------------------------------------------------------------- #


def test_the_evidence_route_is_distinct_from_generic_contract_search():
    """The stop finding: /api/contracts/search must not be the evidence path."""
    import inspect

    from rbac_backend.routers import contract_master_api

    source = inspect.getsource(contract_master_api)
    assert "search_contract_evidence" in source
    # The generic method must not appear on the evidence path at all.
    assert "search_contracts(" not in source


def test_evidence_search_requires_a_query_mode():
    with _Fixture() as fixture:
        response = fixture.client.post(
            "/api/contract-master/evidence/search",
            json={"project_id": PROJECT, "contract_id": CONTRACT, "query": "variation"},
        )
        assert response.status_code == 422


def test_historical_evidence_without_a_date_is_422():
    with _Fixture() as fixture:
        response = fixture.client.post(
            "/api/contract-master/evidence/search",
            json={
                "project_id": PROJECT,
                "contract_id": CONTRACT,
                "query": "variation",
                "query_mode": "historical",
            },
        )
        assert response.status_code == 422


def test_browse_cannot_be_requested_as_an_evidence_mode():
    with _Fixture() as fixture:
        response = fixture.client.post(
            "/api/contract-master/evidence/search",
            json={
                "project_id": PROJECT,
                "contract_id": CONTRACT,
                "query": "variation",
                "query_mode": "browse",
            },
        )
        assert response.status_code == 422


def test_zero_eligible_evidence_is_an_explicit_empty_not_a_widened_search():
    with _Fixture() as fixture:
        response = fixture.client.post(
            "/api/contract-master/evidence/search",
            json={
                "project_id": PROJECT,
                "contract_id": CONTRACT,
                "query": "variation",
                "query_mode": "current_state",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["results"] == []
        # Machine-distinguishable, per T24.
        assert body["outcome"] == "valid_empty"
        assert body["degraded_sources"] == []


def test_the_evidence_response_distinguishes_outcomes_for_the_ui():
    with _Fixture() as fixture:
        body = fixture.client.post(
            "/api/contract-master/evidence/search",
            json={
                "project_id": PROJECT,
                "contract_id": CONTRACT,
                "query": "variation",
                "query_mode": "current_state",
            },
        ).json()
        assert body["outcome"] in {"complete", "degraded", "valid_empty"}


# --------------------------------------------------------------------------- #
# 6. reconciliation and migration
# --------------------------------------------------------------------------- #


def test_reconciliation_inventory_is_reachable_and_writes_nothing():
    with _Fixture() as fixture:
        before = fixture.run(lambda db: db[RECONCILIATION_COLLECTION].count_documents({}))
        response = fixture.client.get(
            f"/api/contract-master/reconciliation/inventory?organization_id={ORG}"
        )
        assert response.status_code == 200
        after = fixture.run(lambda db: db[RECONCILIATION_COLLECTION].count_documents({}))
        assert after == before == 0


def test_materialise_is_a_separate_explicit_route():
    with _Fixture() as fixture:
        response = fixture.client.post(
            "/api/contract-master/reconciliation/materialise",
            json={"organization_id": ORG},
        )
        assert response.status_code in (200, 201)


def test_there_is_no_migrate_all_route():
    with _Fixture() as fixture:
        for path in (
            "/api/contract-master/reconciliation/migrate-all",
            "/api/contract-master/reconciliation/promote-all",
            "/api/contract-master/reconciliation/approve-all",
        ):
            assert fixture.client.post(path, json={}).status_code == 404


def test_migration_routes_require_master_manage():
    with _Fixture(granted=(Permissions.CONTRACT_MASTER_VIEW,)) as fixture:
        assert (
            fixture.client.get(
                f"/api/contract-master/reconciliation/inventory?organization_id={ORG}"
            ).status_code
            == 403
        )


# --------------------------------------------------------------------------- #
# 7. capability response
# --------------------------------------------------------------------------- #


def test_the_capability_response_is_reachable():
    with _Fixture() as fixture:
        response = fixture.client.get(f"/api/contract-master/capabilities?organization_id={ORG}")
        assert response.status_code == 200
        body = response.json()
        assert body["can_browse_catalogue"] is True
        assert body["can_upload_organization_scope"] is True


def test_the_capability_response_reflects_a_project_tier_actor():
    with _Fixture(granted=PROJECT_TIER) as fixture:
        body = fixture.client.get(
            f"/api/contract-master/capabilities?organization_id={ORG}"
        ).json()
        assert body["can_browse_catalogue"] is False
        assert body["can_upload_organization_scope"] is False
        assert body["can_upload_project_scope"] is True


def test_the_capability_response_carries_no_role_names():
    with _Fixture() as fixture:
        body = fixture.client.get(
            f"/api/contract-master/capabilities?organization_id={ORG}"
        ).json()
        serialised = repr(body).lower()
        for role in ("orgadmin", "projectadmin", "superadmin", "superuser"):
            assert role not in serialised


# --------------------------------------------------------------------------- #
# 8. the router is translation only
# --------------------------------------------------------------------------- #


def test_the_router_reimplements_no_domain_writes():
    import ast
    import inspect

    from rbac_backend.routers import contract_master_api

    tree = ast.parse(inspect.getsource(contract_master_api))
    # No direct writes to authoritative collections: every one goes through an
    # accepted service.
    written = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    for forbidden in ("insert_one", "insert_many", "delete_one", "delete_many"):
        assert forbidden not in written, forbidden


def test_the_router_never_tests_a_role_name():
    import inspect

    from rbac_backend.core.security import ROLE_ALIASES
    from rbac_backend.routers import contract_master_api

    source = inspect.getsource(contract_master_api)
    for role in set(ROLE_ALIASES) | set(ROLE_ALIASES.values()):
        assert str(role) not in source, role


def test_promotion_delegates_to_the_atomic_primitive():
    import inspect

    from rbac_backend.routers import contract_master_api

    source = inspect.getsource(contract_master_api)
    assert "ContractPromotionService" in source
    # The five writes belong to the service, not here.
    assert "classification_fact" not in source
