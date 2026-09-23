"""Contract Master v1 over real HTTP, real Mongo and the real PolicyService.

``test_contract_master_routes_mongo.py`` stands a recording ``FakePolicy`` in for
``contract_master_api.get_policy``. That keeps its functional assertions cheap,
but it also meant the real dependency was never resolved by any test - and the
real one imported a module that does not exist, so in production every
``/api/contract-master/*`` route failed with ``ModuleNotFoundError`` before its
first line ran. Nothing here overrides a dependency:

* the principal comes from the real ``get_current_user`` reading a signed JWT;
* every decision runs through the real ``get_policy`` -> ``PolicyService`` ->
  ``PermissionService`` / ``EntitlementService`` / ``ScopeService`` chain;
* roles and permissions are the real seeds, with a subscription row per org;
* the database arrives through the ``core.database`` module globals, as in
  production.

What it pins, beyond "the routes boot":

* **Reconciliation mutations are bound to the authorised organisation.** The
  claim, adjudicate and promote routes authorise the caller against
  ``?organization_id=`` and then act on ``{candidate_id}``. A manager of X who
  names X and supplies a candidate of Y must get the same 404 as for a candidate
  that does not exist - and every such refusal is followed by a database
  assertion, because a 404 alone proves nothing about what was written first.
* **Evidence search mints its scope through ``authorize_contract_scope``**, never
  through the test-only constructor, and never for an organisation of ``"None"``.

Opt-in: set ``CONTRACT_MASTER_POLICY_MONGODB_URI`` to a disposable replica set
(promotion is transactional). Every test creates and drops its own database.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator, Dict

import httpx
import jwt
import pytest
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

MONGODB_URI_ENV = "CONTRACT_MASTER_POLICY_MONGODB_URI"

pytestmark = pytest.mark.integration

ORG_A = "org-cm-a"
ORG_B = "org-cm-b"
PROJ_A1 = "proj-cm-a1"
PROJ_A2 = "proj-cm-a2"
PROJ_B1 = "proj-cm-b1"
CONTRACT = "contract-cm-1"

PREFIX = "contract-master-migration:contracts"
CAND_A_OPEN = f"{PREFIX}:doc-a-open"
CAND_B_OPEN = f"{PREFIX}:doc-b-open"
CAND_A_READY = f"{PREFIX}:doc-a-ready"
CAND_B_READY = f"{PREFIX}:doc-b-ready"
CAND_A_FOREIGN_DOC = f"{PREFIX}:doc-b-stray"
CAND_MISSING = f"{PREFIX}:doc-nowhere"

NOT_FOUND = {"detail": "reconciliation candidate not found"}

PERSONAS: Dict[str, Dict[str, Any]] = {
    "org_admin": {"roles": ["orgadmin"], "organization_id": ORG_A, "projects": []},
    "foreign_org_admin": {
        "roles": ["orgadmin"],
        "organization_id": ORG_B,
        "projects": [],
    },
    "project_user": {
        "roles": ["projectuser"],
        "organization_id": ORG_A,
        "projects": [PROJ_A1],
    },
    "superadmin": {"roles": ["superadmin"], "organization_id": None, "projects": []},
}


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #


def _uri() -> str:
    uri = os.getenv(MONGODB_URI_ENV)
    if not uri:
        pytest.skip(f"set {MONGODB_URI_ENV} to a disposable MongoDB replica set")
    return uri


def _candidate(
    candidate_id: str, organization_id: str, document_id: str, *, ready: bool
) -> Dict[str, Any]:
    from rbac_backend.services.contract_migration_reconciliation import (
        ScopeClassificationState,
        TypeClassificationState,
    )

    row: Dict[str, Any] = {
        "_id": candidate_id,
        "candidate_id": candidate_id,
        "canonical_document_id": document_id,
        "organization_id": organization_id,
        "module": "contracts",
        "scope_state": ScopeClassificationState.AMBIGUOUS.value,
        "type_state": TypeClassificationState.TYPE_UNKNOWN.value,
        "session_evidence": {},
    }
    if ready:
        row.update(
            {
                "scope_state": ScopeClassificationState.ORG_SCOPE_CONFIRMED.value,
                "type_state": TypeClassificationState.TYPE_RESOLVED.value,
                "contract_document_type": "general_conditions",
                "adjudicated_by": "seed-operator",
                "source_fingerprint": f"sha-{document_id}",
            }
        )
    return row


def _document(
    document_id: str, organization_id: str, project_id: str
) -> Dict[str, Any]:
    return {
        "_id": document_id,
        "organization_id": organization_id,
        "project_id": project_id,
        "uploadType": "contract",
        "checksum": f"sha-{document_id}",
        "current_version_id": f"{document_id}-v1",
        "lifecycle_state": "active",
        "createdBy": "seed-user",
    }


async def _seed(db: Any) -> None:
    from rbac_backend.services.contract_migration_reconciliation import (
        RECONCILIATION_COLLECTION,
    )
    from rbac_backend.services.data_initialization import (
        initialize_permissions,
        initialize_roles,
    )

    await initialize_permissions(db)
    await initialize_roles(db)
    assert await db.roles.find_one({"_id": "superuser"}) is None

    await db.organizations.insert_many(
        [{"_id": ORG_A, "name": "Org A"}, {"_id": ORG_B, "name": "Org B"}]
    )
    await db.projects.insert_many(
        [
            {"_id": PROJ_A1, "name": "A1", "organization_id": ORG_A},
            {"_id": PROJ_A2, "name": "A2", "organization_id": ORG_A},
            {"_id": PROJ_B1, "name": "B1", "organization_id": ORG_B},
        ]
    )
    now = datetime.utcnow()
    await db.subscriptions.insert_many(
        [
            {
                "_id": f"sub-{org}",
                "organization_id": org,
                "project_id": None,
                "package_id": None,
                "status": "active",
                "billing_status": "active",
                "plan_code": "dms_enterprise",
                "updated_at": now,
            }
            for org in (ORG_A, ORG_B)
        ]
    )
    for persona, spec in PERSONAS.items():
        await db.users.insert_one(
            {
                "_id": f"user-{persona}",
                "email": f"{persona}@example.com",
                "username": persona,
                "disabled": False,
                "account_type": "client_user",
                "organizations": [],
                **spec,
            }
        )

    await db.documents.insert_many(
        [
            _document("doc-a-open", ORG_A, PROJ_A1),
            _document("doc-b-open", ORG_B, PROJ_B1),
            _document("doc-a-ready", ORG_A, PROJ_A1),
            _document("doc-b-ready", ORG_B, PROJ_B1),
            _document("doc-b-stray", ORG_B, PROJ_B1),
        ]
    )
    await db[RECONCILIATION_COLLECTION].insert_many(
        [
            _candidate(CAND_A_OPEN, ORG_A, "doc-a-open", ready=False),
            _candidate(CAND_B_OPEN, ORG_B, "doc-b-open", ready=False),
            _candidate(CAND_A_READY, ORG_A, "doc-a-ready", ready=True),
            _candidate(CAND_B_READY, ORG_B, "doc-b-ready", ready=True),
            # Corrupt: an org-A candidate pointing at an org-B document.
            _candidate(CAND_A_FOREIGN_DOC, ORG_A, "doc-b-stray", ready=True),
        ]
    )


def _app() -> FastAPI:
    from rbac_backend.routers import contract_master_api

    app = FastAPI()
    app.include_router(contract_master_api.router, prefix="/api")
    assert not app.dependency_overrides
    return app


def _token(persona: str) -> str:
    from rbac_backend.core.config import settings

    now = int(time.time())
    return jwt.encode(
        {
            "sub": f"{persona}@example.com",
            "type": "access",
            "iat": now,
            "exp": now + 3600,
        },
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )


class Env:
    def __init__(self, db: Any, mongo: Any, client: httpx.AsyncClient) -> None:
        self.db = db
        self.mongo = mongo
        self._client = client

    async def call(
        self, persona: str, method: str, path: str, **kwargs: Any
    ) -> httpx.Response:
        return await self._client.request(
            method,
            path,
            headers={"Authorization": f"Bearer {_token(persona)}"},
            **kwargs,
        )

    async def claim(self, persona: str, candidate_id: str, organization_id: str):
        return await self.call(
            persona,
            "POST",
            f"/api/contract-master/reconciliation/candidates/{candidate_id}/claim",
            params={"organization_id": organization_id},
        )

    async def adjudicate(
        self, persona: str, candidate_id: str, organization_id: str, owner_token: str
    ):
        return await self.call(
            persona,
            "POST",
            f"/api/contract-master/reconciliation/candidates/{candidate_id}/adjudicate",
            params={"organization_id": organization_id, "owner_token": owner_token},
            json={
                "scope_state": "ORG_SCOPE_CONFIRMED",
                "contract_document_type": "general_conditions",
                "reason": "operator confirmed",
            },
        )

    async def promote(self, persona: str, candidate_id: str, organization_id: str):
        return await self.call(
            persona,
            "POST",
            f"/api/contract-master/reconciliation/candidates/{candidate_id}/promote",
            params={"organization_id": organization_id},
            json={},
        )

    async def evidence(self, persona: str, project_id: str, **headers: str):
        return await self._client.post(
            "/api/contract-master/evidence/search",
            json={
                "project_id": project_id,
                "contract_id": CONTRACT,
                "query": "variation",
                "query_mode": "current_state",
            },
            headers={"Authorization": f"Bearer {_token(persona)}", **headers},
        )

    async def candidate(self, candidate_id: str) -> Dict[str, Any]:
        from rbac_backend.services.contract_migration_reconciliation import (
            RECONCILIATION_COLLECTION,
        )

        return await self.db[RECONCILIATION_COLLECTION].find_one({"_id": candidate_id})

    async def claims(self) -> list:
        from rbac_backend.services.contract_migration_adjudication import (
            ADJUDICATION_CLAIMS_COLLECTION,
        )

        return (
            await self.db[ADJUDICATION_CLAIMS_COLLECTION].find({}).to_list(length=None)
        )

    async def legal_counts(self) -> Dict[str, int]:
        from rbac_backend.services.contract_document_store import LEGAL_COLLECTIONS
        from rbac_backend.services.contract_promotion import (
            PROMOTION_RECEIPTS_COLLECTION,
        )

        names = set(LEGAL_COLLECTIONS) | {PROMOTION_RECEIPTS_COLLECTION}
        return {name: await self.db[name].count_documents({}) for name in sorted(names)}


@asynccontextmanager
async def _env() -> AsyncIterator[Env]:
    from rbac_backend.core import database as database_module
    from rbac_backend.core.config import settings

    uri = _uri()
    assert not getattr(settings, "APP_REDIS_URL", None) and not getattr(
        settings, "RUNTIME_STATE_REDIS_URL", None
    ), (
        "unset APP_REDIS_URL / RUNTIME_STATE_REDIS_URL: a local Redis turns cache fail-closed into false reds"
    )

    mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5_000)
    hello = await mongo.admin.command("hello")
    if not hello.get("setName") or not hello.get("isWritablePrimary"):
        mongo.close()
        pytest.fail(f"{MONGODB_URI_ENV} is not a writable replica set")
    name = f"contract_master_policy_{uuid.uuid4().hex[:12]}"
    db = mongo[name]

    saved = (database_module.client, database_module.database)
    database_module.client = mongo
    database_module.database = db
    try:
        await _seed(db)
        transport = httpx.ASGITransport(app=_app(), raise_app_exceptions=False)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            yield Env(db, mongo, client)
    finally:
        database_module.client, database_module.database = saved
        await mongo.drop_database(name)
        mongo.close()


def _run(scenario) -> Any:
    return asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# the real dependency resolves
# --------------------------------------------------------------------------- #


def test_a_contract_master_route_boots_through_the_real_policy_dependency() -> None:
    async def scenario() -> None:
        async with _env() as env:
            response = await env.call(
                "org_admin",
                "GET",
                "/api/contract-master/catalogue",
                params={"organization_id": ORG_A},
            )
            assert response.status_code == 200, response.text
            assert response.json() == {"items": []}
            # The real policy refuses what it should, too: this is not a bypass.
            foreign = await env.call(
                "org_admin",
                "GET",
                "/api/contract-master/catalogue",
                params={"organization_id": ORG_B},
            )
            assert foreign.status_code == 403, foreign.text
            capabilities = await env.call(
                "org_admin",
                "GET",
                "/api/contract-master/capabilities",
                params={"organization_id": ORG_A},
            )
            assert capabilities.status_code == 200, capabilities.text
            assert capabilities.json()["can_review_migration"] is True

    _run(scenario)


# --------------------------------------------------------------------------- #
# claim
# --------------------------------------------------------------------------- #


def test_cross_org_claim_is_not_found_and_creates_no_claim_row() -> None:
    async def scenario() -> None:
        async with _env() as env:
            response = await env.claim("org_admin", CAND_B_OPEN, ORG_A)
            assert response.status_code == 404, response.text
            assert response.json() == NOT_FOUND
            assert await env.claims() == []

    _run(scenario)


def test_a_missing_candidate_and_a_foreign_one_are_indistinguishable() -> None:
    async def scenario() -> None:
        async with _env() as env:
            missing = await env.claim("org_admin", CAND_MISSING, ORG_A)
            foreign = await env.claim("org_admin", CAND_B_OPEN, ORG_A)
            assert (missing.status_code, missing.json()) == (
                foreign.status_code,
                foreign.json(),
            )
            assert ORG_B not in foreign.text
            assert await env.claims() == []

    _run(scenario)


def test_naming_the_foreign_organisation_is_still_refused_by_policy() -> None:
    async def scenario() -> None:
        async with _env() as env:
            response = await env.claim("org_admin", CAND_B_OPEN, ORG_B)
            assert response.status_code == 403, response.text
            assert await env.claims() == []

    _run(scenario)


def test_same_org_claim_still_works() -> None:
    async def scenario() -> None:
        async with _env() as env:
            response = await env.claim("org_admin", CAND_A_OPEN, ORG_A)
            assert response.status_code == 200, response.text
            assert response.json()["candidate_id"] == CAND_A_OPEN
            (row,) = await env.claims()
            assert row["candidate_id"] == CAND_A_OPEN
            assert row["operator_id"] == "user-org_admin"

    _run(scenario)


# --------------------------------------------------------------------------- #
# adjudicate
# --------------------------------------------------------------------------- #


def test_cross_org_adjudication_is_not_found_even_with_a_live_owner_token() -> None:
    """The owner token proves the lease, not the tenancy."""

    async def scenario() -> None:
        from rbac_backend.services.contract_migration_adjudication import (
            ADJUDICATION_CLAIMS_COLLECTION,
            ADJUDICATION_CLAIM_LEASE,
        )

        async with _env() as env:
            now = datetime.utcnow()
            # A live claim on candidate Y held by the org-X manager - the
            # strongest position an attacker could reach.
            await env.db[ADJUDICATION_CLAIMS_COLLECTION].insert_one(
                {
                    "_id": f"contract-migration-claim:{CAND_B_OPEN}",
                    "candidate_id": CAND_B_OPEN,
                    "operator_id": "user-org_admin",
                    "owner_token": "stolen-token",
                    "claimed_at": now,
                    "lease_expires_at": now + ADJUDICATION_CLAIM_LEASE,
                }
            )
            before = await env.candidate(CAND_B_OPEN)

            response = await env.adjudicate(
                "org_admin", CAND_B_OPEN, ORG_A, "stolen-token"
            )

            assert response.status_code == 404, response.text
            assert response.json() == NOT_FOUND
            assert await env.candidate(CAND_B_OPEN) == before
            assert "adjudicated_by" not in before

    _run(scenario)


def test_cross_org_adjudication_without_a_claim_reveals_nothing_about_claim_state() -> (
    None
):
    """Candidate scope is checked before the lease, so no 409 hints that Y exists."""

    async def scenario() -> None:
        async with _env() as env:
            before = await env.candidate(CAND_B_OPEN)
            response = await env.adjudicate("org_admin", CAND_B_OPEN, ORG_A, "guessed")
            assert response.status_code == 404, response.text
            assert response.json() == NOT_FOUND
            assert await env.candidate(CAND_B_OPEN) == before

    _run(scenario)


def test_same_org_claim_then_adjudicate_still_works() -> None:
    async def scenario() -> None:
        async with _env() as env:
            claimed = await env.claim("org_admin", CAND_A_OPEN, ORG_A)
            assert claimed.status_code == 200, claimed.text
            token = claimed.json()["owner_token"]

            response = await env.adjudicate("org_admin", CAND_A_OPEN, ORG_A, token)

            assert response.status_code == 200, response.text
            row = await env.candidate(CAND_A_OPEN)
            assert row["scope_state"] == "ORG_SCOPE_CONFIRMED"
            assert row["type_state"] == "TYPE_RESOLVED"
            assert row["adjudicated_by"] == "user-org_admin"

    _run(scenario)


def test_same_org_adjudication_without_a_live_claim_is_still_409() -> None:
    async def scenario() -> None:
        async with _env() as env:
            before = await env.candidate(CAND_A_OPEN)
            response = await env.adjudicate(
                "org_admin", CAND_A_OPEN, ORG_A, "not-a-claim"
            )
            assert response.status_code == 409, response.text
            assert await env.candidate(CAND_A_OPEN) == before

    _run(scenario)


# --------------------------------------------------------------------------- #
# promote
# --------------------------------------------------------------------------- #


def test_cross_org_promotion_is_not_found_and_writes_nothing() -> None:
    async def scenario() -> None:
        async with _env() as env:
            counts = await env.legal_counts()
            before = await env.candidate(CAND_B_READY)

            response = await env.promote("org_admin", CAND_B_READY, ORG_A)

            assert response.status_code == 404, response.text
            assert response.json() == NOT_FOUND
            # No instrument, classification fact, applicability, applicability
            # event or receipt - and no promoted marker on the candidate.
            assert await env.legal_counts() == counts
            assert await env.candidate(CAND_B_READY) == before
            assert not before.get("promoted")

    _run(scenario)


def test_same_org_promotion_still_works() -> None:
    async def scenario() -> None:
        from rbac_backend.services.contract_document_store import (
            CONTRACT_DOCUMENTS_COLLECTION,
        )

        async with _env() as env:
            response = await env.promote("org_admin", CAND_A_READY, ORG_A)

            assert response.status_code == 200, response.text
            body = response.json()
            assert body["candidate_id"] == CAND_A_READY
            assert body["scope_level"] == "organization"
            instrument = await env.db[CONTRACT_DOCUMENTS_COLLECTION].find_one(
                {"_id": body["contract_document_id"]}
            )
            assert instrument["organization_id"] == ORG_A
            assert (await env.candidate(CAND_A_READY))["promoted"] is True

    _run(scenario)


def test_a_candidate_pointing_at_another_orgs_document_fails_closed() -> None:
    """Candidate org == document org == instrument org, or nothing is written."""

    async def scenario() -> None:
        async with _env() as env:
            counts = await env.legal_counts()
            before = await env.candidate(CAND_A_FOREIGN_DOC)

            response = await env.promote("org_admin", CAND_A_FOREIGN_DOC, ORG_A)

            assert response.status_code == 422, response.text
            assert ORG_B not in response.text
            assert await env.legal_counts() == counts
            assert await env.candidate(CAND_A_FOREIGN_DOC) == before

    _run(scenario)


# --------------------------------------------------------------------------- #
# evidence search
# --------------------------------------------------------------------------- #


def test_evidence_search_succeeds_for_an_authorised_org_project_pair() -> None:
    async def scenario() -> None:
        async with _env() as env:
            for persona in ("org_admin", "project_user"):
                response = await env.evidence(persona, PROJ_A1)
                assert response.status_code == 200, (persona, response.text)
                assert response.json()["outcome"] == "valid_empty"

    _run(scenario)


def test_evidence_search_refuses_a_project_the_user_is_not_in() -> None:
    async def scenario() -> None:
        async with _env() as env:
            response = await env.evidence("project_user", PROJ_A2)
            assert response.status_code == 403, response.text

    _run(scenario)


def test_evidence_search_refuses_a_foreign_org_project_pair() -> None:
    async def scenario() -> None:
        async with _env() as env:
            for persona, project in (
                ("org_admin", PROJ_B1),
                ("foreign_org_admin", PROJ_A1),
            ):
                response = await env.evidence(persona, project)
                assert response.status_code == 403, (persona, project, response.text)

    _run(scenario)


def test_evidence_search_with_no_organisation_context_fails_closed() -> None:
    """A global role's organisation is its validated selection. None is not "None"."""

    async def scenario() -> None:
        async with _env() as env:
            for headers in ({}, {"X-Org-Id": ORG_A}):
                response = await env.evidence("superadmin", PROJ_A1, **headers)
                assert response.status_code == 400, response.text
                assert response.json()["detail"]["code"] == "selection_required"

    _run(scenario)


def test_authorize_contract_scope_refuses_an_unpaired_project_even_for_superadmin() -> (
    None
):
    """PolicyService lets superadmin through without asking where the project lives.

    The minted token claims an authorised (organisation, project) pair, so the
    helper proves the pair itself rather than trusting a caller that skipped it.
    """

    async def scenario() -> None:
        from fastapi import HTTPException

        from rbac_backend.core.permissions import Permissions
        from rbac_backend.core.security import CurrentUser
        from rbac_backend.services.contract_scope_resolver import (
            authorize_contract_scope,
        )
        from rbac_backend.services.policy_service import PolicyService

        async with _env() as env:
            policy = PolicyService(db=env.db)
            superadmin = CurrentUser(
                id="user-superadmin",
                username="superadmin",
                email="superadmin@example.com",
                roles=["superadmin"],
            )
            with pytest.raises(HTTPException) as refused:
                await authorize_contract_scope(
                    policy,
                    superadmin,
                    permission=Permissions.CONTRACT_MASTER_VIEW,
                    organization_id=ORG_A,
                    project_id=PROJ_B1,
                    contract_id=CONTRACT,
                )
            assert refused.value.status_code == 403
            scope = await authorize_contract_scope(
                policy,
                superadmin,
                permission=Permissions.CONTRACT_MASTER_VIEW,
                organization_id=ORG_A,
                project_id=PROJ_A1,
                contract_id=CONTRACT,
            )
            assert (scope.organization_id, scope.project_id) == (ORG_A, PROJ_A1)

    _run(scenario)
