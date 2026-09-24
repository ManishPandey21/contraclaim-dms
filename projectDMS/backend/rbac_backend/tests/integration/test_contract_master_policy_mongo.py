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
from typing import Any, AsyncIterator, Dict, Optional

import httpx
import jwt
import pytest
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

MONGODB_URI_ENV = "CONTRACT_MASTER_POLICY_MONGODB_URI"

pytestmark = pytest.mark.integration

ORG_A = "org-cm-a"
ORG_B = "org-cm-b"
#: Its own organisation, so the ObjectId rehearsal materialises only its document.
ORG_C = "org-cm-c"
PROJ_A1 = "proj-cm-a1"
PROJ_A2 = "proj-cm-a2"
PROJ_B1 = "proj-cm-b1"
PROJ_C1 = "proj-cm-c1"
#: Deactivated: cannot be selected, so it can never anchor a candidate.
PROJ_A3_INACTIVE = "proj-cm-a3-inactive"
CONTRACT = "contract-cm-1"

PREFIX = "contract-master-migration:contracts"
CAND_A_OPEN = f"{PREFIX}:doc-a-open"
CAND_B_OPEN = f"{PREFIX}:doc-b-open"
CAND_A_READY = f"{PREFIX}:doc-a-ready"
CAND_B_READY = f"{PREFIX}:doc-b-ready"
CAND_A_FOREIGN_DOC = f"{PREFIX}:doc-b-stray"
CAND_A_ORPHAN_DOC = f"{PREFIX}:doc-no-org"
CAND_MISSING = f"{PREFIX}:doc-nowhere"
#: Anchored by their canonical Document's own project (Documents register field).
CAND_P1_OPEN = f"{PREFIX}:doc-p1-open"
CAND_P2_OPEN = f"{PREFIX}:doc-p2-open"
CAND_P1_READY = f"{PREFIX}:doc-p1-ready"
CAND_P2_READY = f"{PREFIX}:doc-p2-ready"
#: No trustworthy project anchor: an organisation-level Document, and only a
#: session hint / scope hint pointing at a project.
CAND_UNANCHORED = f"{PREFIX}:doc-org-level"
#: Conflicted anchors: the two trusted fields disagree / the project is inactive.
CAND_CONFLICT = f"{PREFIX}:doc-conflict"
CAND_INACTIVE = f"{PREFIX}:doc-inactive"

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
    "project_admin_a1": {
        "roles": ["projectadmin"],
        "organization_id": ORG_A,
        "projects": [PROJ_A1],
    },
    "member_a1_a2": {
        "roles": ["projectuser"],
        "organization_id": ORG_A,
        "projects": [PROJ_A1, PROJ_A2],
    },
    "org_c_admin": {"roles": ["orgadmin"], "organization_id": ORG_C, "projects": []},
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


def _project_ready(
    candidate_id: str, document_id: str, project_id: str
) -> Dict[str, Any]:
    """Adjudicated project scope, anchored to the same project its Document names."""
    return {
        **_candidate(candidate_id, ORG_A, document_id, ready=True),
        "scope_state": "PROJECT_SCOPE_CONFIRMED",
        "project_id": project_id,
    }


def _document(
    document_id: Any, organization_id: str, project_id: Optional[str]
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
    # The unique indexes startup creates (core/database.py). Without them a write
    # that collides in production - the second applicability event without an
    # event_id - passes here.
    from rbac_backend.services.contract_document_store import (
        ensure_contract_document_indexes,
    )

    await ensure_contract_document_indexes(db)

    await db.organizations.insert_many(
        [
            {"_id": ORG_A, "name": "Org A"},
            {"_id": ORG_B, "name": "Org B"},
            {"_id": ORG_C, "name": "Org C"},
        ]
    )
    await db.projects.insert_many(
        [
            {"_id": PROJ_A1, "name": "A1", "organization_id": ORG_A},
            {"_id": PROJ_A2, "name": "A2", "organization_id": ORG_A},
            {"_id": PROJ_B1, "name": "B1", "organization_id": ORG_B},
            {"_id": PROJ_C1, "name": "C1", "organization_id": ORG_C},
            {
                "_id": PROJ_A3_INACTIVE,
                "name": "A3",
                "organization_id": ORG_A,
                "is_active": False,
            },
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
            for org in (ORG_A, ORG_B, ORG_C)
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
            {**_document("doc-no-org", ORG_A, PROJ_A1), "organization_id": None},
            _document("doc-p1-open", ORG_A, PROJ_A1),
            _document("doc-p2-open", ORG_A, PROJ_A2),
            _document("doc-p1-ready", ORG_A, PROJ_A1),
            _document("doc-p2-ready", ORG_A, PROJ_A2),
            _document("doc-org-level", ORG_A, None),
            _document("doc-conflict", ORG_A, PROJ_A2),
            _document("doc-inactive", ORG_A, PROJ_A3_INACTIVE),
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
            # Corrupt: an org-A candidate pointing at a document with no org.
            _candidate(CAND_A_ORPHAN_DOC, ORG_A, "doc-no-org", ready=True),
            _candidate(CAND_P1_OPEN, ORG_A, "doc-p1-open", ready=False),
            _candidate(CAND_P2_OPEN, ORG_A, "doc-p2-open", ready=False),
            _project_ready(CAND_P1_READY, "doc-p1-ready", PROJ_A1),
            _project_ready(CAND_P2_READY, "doc-p2-ready", PROJ_A2),
            {
                **_candidate(CAND_UNANCHORED, ORG_A, "doc-org-level", ready=False),
                # Hints are not authority: neither may anchor this candidate.
                "session_evidence": {"project_id": PROJ_A1},
                "scope_hint": PROJ_A1,
            },
            # candidate.project_id says A1, its Document says A2.
            _project_ready(CAND_CONFLICT, "doc-conflict", PROJ_A1),
            _candidate(CAND_INACTIVE, ORG_A, "doc-inactive", ready=True),
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
        self,
        persona: str,
        method: str,
        path: str,
        *,
        selected: Optional[str] = None,
        selected_org: Optional[str] = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """``selected`` / ``selected_org`` are the navbar selection (CL-4A headers)."""
        headers = {"Authorization": f"Bearer {_token(persona)}"}
        if selected:
            headers["X-Proj-Id"] = selected
        if selected_org:
            headers["X-Org-Id"] = selected_org
        return await self._client.request(method, path, headers=headers, **kwargs)

    async def claim(
        self,
        persona: str,
        candidate_id: str,
        organization_id: str,
        selected: Optional[str] = PROJ_A1,
    ):
        return await self.call(
            persona,
            "POST",
            f"/api/contract-master/reconciliation/candidates/{candidate_id}/claim",
            selected=selected,
            params={"organization_id": organization_id},
        )

    async def adjudicate(
        self,
        persona: str,
        candidate_id: str,
        organization_id: str,
        owner_token: str,
        scope_state: str = "ORG_SCOPE_CONFIRMED",
        selected: Optional[str] = PROJ_A1,
    ):
        return await self.call(
            persona,
            "POST",
            f"/api/contract-master/reconciliation/candidates/{candidate_id}/adjudicate",
            selected=selected,
            params={"organization_id": organization_id, "owner_token": owner_token},
            json={
                "scope_state": scope_state,
                "contract_document_type": "general_conditions",
                "reason": "operator confirmed",
            },
        )

    async def promote(
        self,
        persona: str,
        candidate_id: str,
        organization_id: str,
        contract_id: Optional[str] = None,
        selected: Optional[str] = PROJ_A1,
    ):
        return await self.call(
            persona,
            "POST",
            f"/api/contract-master/reconciliation/candidates/{candidate_id}/promote",
            selected=selected,
            params={"organization_id": organization_id},
            json={"contract_id": contract_id} if contract_id else {},
        )

    async def evidence(
        self,
        persona: str,
        project_id: str,
        selected_project: Optional[str] = None,
        selected_org: Optional[str] = None,
    ):
        """Search ``project_id``; the navbar selection travels as headers only."""
        headers = {"Authorization": f"Bearer {_token(persona)}"}
        if selected_project:
            headers["X-Proj-Id"] = selected_project
        if selected_org:
            headers["X-Org-Id"] = selected_org
        return await self._client.post(
            "/api/contract-master/evidence/search",
            json={
                "project_id": project_id,
                "contract_id": CONTRACT,
                "query": "variation",
                "query_mode": "current_state",
            },
            headers=headers,
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

    async def snapshot(self) -> Dict[str, list]:
        """Every document in every collection, except the refusal-side audit.

        Counts cannot see an in-place rewrite; this can. ``audit_events`` and the
        ``admin_review_items`` queue an audited deny feeds are left out: the
        policy records the refusal itself.
        """
        names = sorted(
            set(await self.db.list_collection_names())
            - {"audit_events", "admin_review_items"}
        )
        return {
            name: sorted(
                (await self.db[name].find({}).to_list(length=None)),
                key=lambda row: str(row.get("_id")),
            )
            for name in names
        }

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
            before = await env.snapshot()
            response = await env.claim("org_admin", CAND_B_OPEN, ORG_A)
            assert response.status_code == 404, response.text
            assert response.json() == NOT_FOUND
            assert await env.claims() == []
            # Nothing anywhere: the claim index may exist, no row may.
            after = await env.snapshot()
            after.pop("contract_migration_adjudication_claims", None)
            assert after == before

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
            everything = await env.snapshot()

            response = await env.adjudicate(
                "org_admin", CAND_B_OPEN, ORG_A, "stolen-token"
            )

            assert response.status_code == 404, response.text
            assert response.json() == NOT_FOUND
            assert await env.candidate(CAND_B_OPEN) == before
            assert "adjudicated_by" not in before
            # Including the planted claim row, untouched.
            assert await env.snapshot() == everything

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
            everything = await env.snapshot()

            response = await env.promote("org_admin", CAND_B_READY, ORG_A)

            assert response.status_code == 404, response.text
            assert response.json() == NOT_FOUND
            assert await env.snapshot() == everything
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


@pytest.mark.parametrize("candidate_id", [CAND_A_FOREIGN_DOC, CAND_A_ORPHAN_DOC])
def test_a_candidate_whose_document_is_not_its_orgs_fails_closed(candidate_id) -> None:
    """Candidate org == document org == instrument org, or nothing is written.

    Covers a document owned by another organisation and one owned by none.
    """

    async def scenario() -> None:
        async with _env() as env:
            everything = await env.snapshot()

            response = await env.promote("org_admin", candidate_id, ORG_A)

            assert response.status_code == 422, response.text
            assert "tenancy boundary" in response.text
            assert ORG_B not in response.text
            assert await env.snapshot() == everything

    _run(scenario)


# --------------------------------------------------------------------------- #
# evidence search
# --------------------------------------------------------------------------- #


def test_evidence_search_is_bound_to_the_selected_project() -> None:
    """Allowed only for the project selected in the navbar, for every role."""

    async def scenario() -> None:
        async with _env() as env:
            for persona, selected in (
                ("superadmin", PROJ_A1),
                ("org_admin", PROJ_A1),
                ("project_user", PROJ_A1),
                ("member_a1_a2", PROJ_A1),
            ):
                response = await env.evidence(
                    persona, PROJ_A1, selected_project=selected
                )
                assert response.status_code == 200, (persona, response.text)
                assert response.json()["outcome"] == "valid_empty"
            # The organisation header may accompany the project; it must agree.
            response = await env.evidence(
                "superadmin", PROJ_A1, selected_project=PROJ_A1, selected_org=ORG_A
            )
            assert response.status_code == 200, response.text

    _run(scenario)


def test_superadmin_cannot_search_outside_the_selected_project() -> None:
    async def scenario() -> None:
        async with _env() as env:
            for searched in (PROJ_B1, PROJ_A2):
                response = await env.evidence(
                    "superadmin", searched, selected_project=PROJ_A1
                )
                assert response.status_code == 403, (searched, response.text)
                assert response.json()["detail"]["code"] == "context_forbidden"

    _run(scenario)


def test_a_member_of_two_projects_is_held_to_the_selected_one() -> None:
    async def scenario() -> None:
        async with _env() as env:
            response = await env.evidence(
                "member_a1_a2", PROJ_A1, selected_project=PROJ_A2
            )
            assert response.status_code == 403, response.text
            assert response.json()["detail"]["code"] == "context_forbidden"

    _run(scenario)


def test_evidence_search_with_no_selected_project_is_400() -> None:
    """Nothing is inferred - not from the body's project, not from the account."""

    async def scenario() -> None:
        async with _env() as env:
            for persona, org in (
                ("superadmin", None),
                ("superadmin", ORG_A),
                ("org_admin", None),
                ("org_admin", ORG_A),
                ("project_user", None),
            ):
                response = await env.evidence(persona, PROJ_A1, selected_org=org)
                assert response.status_code == 400, (persona, org, response.text)
                assert response.json()["detail"]["code"] == "selection_required"

    _run(scenario)


def test_a_selection_the_caller_may_not_use_is_refused() -> None:
    async def scenario() -> None:
        async with _env() as env:
            for persona, selected in (
                ("project_user", PROJ_A2),
                ("foreign_org_admin", PROJ_A1),
                ("org_admin", PROJ_B1),
            ):
                response = await env.evidence(
                    persona, selected, selected_project=selected
                )
                assert response.status_code == 403, (persona, selected, response.text)
                assert response.json()["detail"]["code"] == "context_forbidden"

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


# --------------------------------------------------------------------------- #
# project-tier authority over reconciliation candidates
# --------------------------------------------------------------------------- #


def test_a_project_admin_acts_on_a_candidate_of_their_project() -> None:
    async def scenario() -> None:
        async with _env() as env:
            claimed = await env.claim("project_admin_a1", CAND_P1_OPEN, ORG_A)
            assert claimed.status_code == 200, claimed.text
            response = await env.adjudicate(
                "project_admin_a1",
                CAND_P1_OPEN,
                ORG_A,
                claimed.json()["owner_token"],
                scope_state="PROJECT_SCOPE_CONFIRMED",
            )
            assert response.status_code == 200, response.text
            row = await env.candidate(CAND_P1_OPEN)
            assert row["scope_state"] == "PROJECT_SCOPE_CONFIRMED"
            assert row["adjudicated_by"] == "user-project_admin_a1"

    _run(scenario)


@pytest.mark.parametrize("action", ["claim", "adjudicate", "promote"])
def test_a_project_admin_cannot_act_on_another_projects_candidate(action) -> None:
    """Project Admin of A1, same organisation, candidate anchored to A2: refused."""

    async def scenario() -> None:
        from rbac_backend.services.contract_migration_adjudication import (
            ADJUDICATION_CLAIM_LEASE,
            ADJUDICATION_CLAIMS_COLLECTION,
        )

        async with _env() as env:
            if action == "adjudicate":
                # The strongest position: a live claim already held.
                now = datetime.utcnow()
                await env.db[ADJUDICATION_CLAIMS_COLLECTION].insert_one(
                    {
                        "_id": f"contract-migration-claim:{CAND_P2_OPEN}",
                        "candidate_id": CAND_P2_OPEN,
                        "operator_id": "user-project_admin_a1",
                        "owner_token": "held-token",
                        "claimed_at": now,
                        "lease_expires_at": now + ADJUDICATION_CLAIM_LEASE,
                    }
                )
            everything = await env.snapshot()

            if action == "claim":
                response = await env.claim("project_admin_a1", CAND_P2_OPEN, ORG_A)
            elif action == "adjudicate":
                response = await env.adjudicate(
                    "project_admin_a1",
                    CAND_P2_OPEN,
                    ORG_A,
                    "held-token",
                    scope_state="PROJECT_SCOPE_CONFIRMED",
                )
            else:
                response = await env.promote(
                    "project_admin_a1", CAND_P2_READY, ORG_A, contract_id=CONTRACT
                )

            assert response.status_code == 403, response.text
            # A1 selected, candidate anchored to A2: the selection refuses first.
            # The policy layer beneath it is proven separately
            # (test_the_anchor_check_holds_without_the_selection).
            assert response.json()["detail"]["code"] == "context_forbidden"
            after = await env.snapshot()
            after.pop("contract_migration_adjudication_claims", None)
            everything.pop("contract_migration_adjudication_claims", None)
            assert after == everything
            if action != "adjudicate":
                assert await env.claims() == []

    _run(scenario)


@pytest.mark.parametrize("action", ["claim", "adjudicate"])
def test_a_project_admin_gets_no_authority_over_an_unanchored_candidate(action) -> None:
    """project_id=None is not organisation-wide authority for a project-tier role.

    The candidate carries a session hint and a scope hint naming the admin's own
    project; neither is an anchor.
    """

    async def scenario() -> None:
        async with _env() as env:
            everything = await env.snapshot()
            if action == "claim":
                response = await env.claim("project_admin_a1", CAND_UNANCHORED, ORG_A)
            else:
                response = await env.adjudicate(
                    "project_admin_a1", CAND_UNANCHORED, ORG_A, "any"
                )
            assert response.status_code == 403, response.text
            after = await env.snapshot()
            after.pop("contract_migration_adjudication_claims", None)
            everything.pop("contract_migration_adjudication_claims", None)
            assert after == everything
            assert await env.claims() == []

    _run(scenario)


def test_a_project_admin_cannot_declare_organisation_scope() -> None:
    """Adjudicating ORG_SCOPE_CONFIRMED mints organisation-wide authority."""

    async def scenario() -> None:
        async with _env() as env:
            claimed = await env.claim("project_admin_a1", CAND_P1_OPEN, ORG_A)
            assert claimed.status_code == 200, claimed.text
            before = await env.candidate(CAND_P1_OPEN)
            response = await env.adjudicate(
                "project_admin_a1",
                CAND_P1_OPEN,
                ORG_A,
                claimed.json()["owner_token"],
                scope_state="ORG_SCOPE_CONFIRMED",
            )
            assert response.status_code == 403, response.text
            assert await env.candidate(CAND_P1_OPEN) == before

    _run(scenario)


def test_a_project_admin_promotes_a_candidate_of_their_project() -> None:
    """Allowed: anchored to A1, promoted by the Project Admin of A1."""

    async def scenario() -> None:
        async with _env() as env:
            response = await env.promote(
                "project_admin_a1", CAND_P1_READY, ORG_A, contract_id=CONTRACT
            )
            assert response.status_code == 200, response.text
            assert response.json()["scope_level"] == "project"
            assert (await env.candidate(CAND_P1_READY))["promoted"] is True

    _run(scenario)


def test_a_project_admin_cannot_promote_to_organisation_scope() -> None:
    """An ORG_SCOPE_CONFIRMED candidate becomes an organisation-wide instrument."""

    async def scenario() -> None:
        async with _env() as env:
            everything = await env.snapshot()
            # CAND_A_READY: anchored to A1 by its Document, adjudicated ORG scope.
            response = await env.promote("project_admin_a1", CAND_A_READY, ORG_A)
            assert response.status_code == 403, response.text
            assert await env.snapshot() == everything

    _run(scenario)


@pytest.mark.parametrize(
    "candidate_id,selected",
    [(CAND_P1_OPEN, PROJ_A1), (CAND_P2_OPEN, PROJ_A2), (CAND_UNANCHORED, PROJ_A1)],
)
def test_an_org_admin_acts_on_every_candidate_of_the_organisation(
    candidate_id, selected
) -> None:
    async def scenario() -> None:
        async with _env() as env:
            claimed = await env.claim(
                "org_admin", candidate_id, ORG_A, selected=selected
            )
            assert claimed.status_code == 200, claimed.text
            response = await env.adjudicate(
                "org_admin",
                candidate_id,
                ORG_A,
                claimed.json()["owner_token"],
                selected=selected,
            )
            assert response.status_code == 200, response.text

    _run(scenario)


@pytest.mark.parametrize(
    "candidate_id,project", [(CAND_P1_READY, PROJ_A1), (CAND_P2_READY, PROJ_A2)]
)
def test_an_org_admin_promotes_a_project_scope_candidate(candidate_id, project) -> None:
    async def scenario() -> None:
        async with _env() as env:
            response = await env.promote(
                "org_admin", candidate_id, ORG_A, contract_id=CONTRACT, selected=project
            )
            assert response.status_code == 200, response.text
            assert response.json()["scope_level"] == "project"
            applicability = await env.db["contract_document_applicability"].find_one(
                {"contract_document_id": response.json()["contract_document_id"]}
            )
            assert applicability["project_id"] == project

    _run(scenario)


def test_the_review_queue_is_bounded_to_the_callers_projects() -> None:
    """A collection read: 200 bounded to assignments, never the whole organisation."""

    async def scenario() -> None:
        async with _env() as env:
            path = "/api/contract-master/reconciliation/candidates"
            mine = await env.call(
                "project_admin_a1",
                "GET",
                path,
                selected=PROJ_A1,
                params={"organization_id": ORG_A},
            )
            assert mine.status_code == 200, mine.text
            seen = {row["candidate_id"] for row in mine.json()["candidates"]}
            assert CAND_P1_OPEN in seen and CAND_P1_READY in seen
            assert not seen & {CAND_P2_OPEN, CAND_P2_READY, CAND_UNANCHORED}

            everything = await env.call(
                "org_admin",
                "GET",
                path,
                selected_org=ORG_A,
                params={"organization_id": ORG_A},
            )
            assert everything.status_code == 200, everything.text
            all_rows = {row["candidate_id"] for row in everything.json()["candidates"]}
            assert {CAND_P1_OPEN, CAND_P2_OPEN, CAND_UNANCHORED} <= all_rows

            narrowed = await env.call(
                "org_admin",
                "GET",
                path,
                selected=PROJ_A1,
                params={"organization_id": ORG_A},
            )
            narrowed_rows = {
                row["candidate_id"] for row in narrowed.json()["candidates"]
            }
            assert CAND_P1_OPEN in narrowed_rows and CAND_UNANCHORED in narrowed_rows
            assert not narrowed_rows & {CAND_P2_OPEN, CAND_P2_READY}

    _run(scenario)


def test_organisation_wide_migration_tools_need_organisation_tier() -> None:
    async def scenario() -> None:
        async with _env() as env:
            before = await env.snapshot()
            inventory = await env.call(
                "project_admin_a1",
                "GET",
                "/api/contract-master/reconciliation/inventory",
                params={"organization_id": ORG_A},
            )
            materialise = await env.call(
                "project_admin_a1",
                "POST",
                "/api/contract-master/reconciliation/materialise",
                selected_org=ORG_A,
                json={"organization_id": ORG_A},
            )
            assert inventory.status_code == 403, inventory.text
            assert materialise.status_code == 403, materialise.text
            assert await env.snapshot() == before

            allowed = await env.call(
                "org_admin",
                "GET",
                "/api/contract-master/reconciliation/inventory",
                params={"organization_id": ORG_A},
            )
            assert allowed.status_code == 200, allowed.text

    _run(scenario)


def test_materialising_again_converges_instead_of_failing() -> None:
    """Every org-A contract Document already has a candidate; a second run adds none."""

    async def scenario() -> None:
        from rbac_backend.services.contract_migration_reconciliation import (
            RECONCILIATION_COLLECTION,
        )

        async with _env() as env:
            rows = await env.db[RECONCILIATION_COLLECTION].find({}).to_list(length=None)
            for _ in range(2):
                response = await env.call(
                    "org_admin",
                    "POST",
                    "/api/contract-master/reconciliation/materialise",
                    selected_org=ORG_A,
                    json={"organization_id": ORG_A},
                )
                assert response.status_code == 200, response.text
                assert response.json() == {"materialised": 0}
            after = (
                await env.db[RECONCILIATION_COLLECTION].find({}).to_list(length=None)
            )
            assert after == rows

    _run(scenario)


# --------------------------------------------------------------------------- #
# a Document stored the way production stores it
# --------------------------------------------------------------------------- #


def test_an_objectid_keyed_legacy_document_promotes_through_the_normal_path() -> None:
    """Production Documents are ObjectId-keyed; the candidate records str(_id)."""

    async def scenario() -> None:
        from bson import ObjectId

        from rbac_backend.services.contract_document_store import (
            CONTRACT_DOCUMENTS_COLLECTION,
        )

        async with _env() as env:
            oid = ObjectId()
            await env.db.documents.insert_one(_document(oid, ORG_C, None))

            materialised = await env.call(
                "org_c_admin",
                "POST",
                "/api/contract-master/reconciliation/materialise",
                selected_org=ORG_C,
                json={"organization_id": ORG_C},
            )
            assert materialised.status_code == 200, materialised.text
            candidate_id = f"{PREFIX}:{oid}"
            row = await env.candidate(candidate_id)
            # The representation materialise actually stores.
            assert row["canonical_document_id"] == str(oid)

            claimed = await env.claim(
                "org_c_admin", candidate_id, ORG_C, selected=PROJ_C1
            )
            assert claimed.status_code == 200, claimed.text
            adjudicated = await env.adjudicate(
                "org_c_admin",
                candidate_id,
                ORG_C,
                claimed.json()["owner_token"],
                selected=PROJ_C1,
            )
            assert adjudicated.status_code == 200, adjudicated.text

            promoted = await env.promote(
                "org_c_admin", candidate_id, ORG_C, selected=PROJ_C1
            )
            assert promoted.status_code == 200, promoted.text
            instrument = await env.db[CONTRACT_DOCUMENTS_COLLECTION].find_one(
                {"_id": promoted.json()["contract_document_id"]}
            )
            assert instrument["organization_id"] == ORG_C
            assert instrument["document_id"] == str(oid)

            detail = await env.call(
                "org_c_admin",
                "GET",
                f"/api/contract-master/instruments/{instrument['_id']}",
                selected=PROJ_C1,
            )
            assert detail.status_code == 200, detail.text

    _run(scenario)


# --------------------------------------------------------------------------- #
# two independent layers, and the selection matrix across every route
# --------------------------------------------------------------------------- #


def test_the_anchor_check_holds_without_the_selection() -> None:
    """authorize_candidate refuses Project Admin A1 on an A2 candidate by policy alone.

    Over HTTP the selection refuses first (A1 selected -> A2 candidate is
    context_forbidden, and A2 cannot be selected by a non-member). This proves the
    policy layer beneath it is not relying on that.
    """

    async def scenario() -> None:
        from fastapi import HTTPException

        from rbac_backend.core.permissions import Permissions
        from rbac_backend.core.security import CurrentUser
        from rbac_backend.services.contract_candidate_authority import (
            authorize_candidate,
        )
        from rbac_backend.services.policy_service import PolicyService

        async with _env() as env:
            policy = PolicyService(db=env.db)
            admin = CurrentUser(
                id="user-project_admin_a1",
                username="project_admin_a1",
                email="project_admin_a1@example.com",
                roles=["projectadmin"],
                organization_id=ORG_A,
                projects=[PROJ_A1],
            )
            for candidate_id in (CAND_P2_OPEN, CAND_UNANCHORED):
                with pytest.raises(HTTPException) as refused:
                    await authorize_candidate(
                        policy,
                        admin,
                        db=env.db,
                        candidate_id=candidate_id,
                        organization_id=ORG_A,
                        permission=Permissions.CONTRACT_MASTER_MANAGE,
                    )
                assert refused.value.status_code == 403, candidate_id
            allowed = await authorize_candidate(
                policy,
                admin,
                db=env.db,
                candidate_id=CAND_P1_OPEN,
                organization_id=ORG_A,
                permission=Permissions.CONTRACT_MASTER_MANAGE,
            )
            assert allowed.project_id == PROJ_A1

    _run(scenario)


def test_session_and_scope_hints_never_anchor_a_candidate() -> None:
    async def scenario() -> None:
        from rbac_backend.services.contract_candidate_authority import (
            candidate_project_anchor,
            load_canonical_document,
        )

        async with _env() as env:
            row = await env.candidate(CAND_UNANCHORED)
            assert row["session_evidence"] == {"project_id": PROJ_A1}
            document = await load_canonical_document(env.db, row)
            assert await candidate_project_anchor(env.db, row, document) is None

    _run(scenario)


def test_with_a_project_selected_even_an_org_admin_is_held_to_it() -> None:
    """The selection is a request boundary, not a membership test."""

    async def scenario() -> None:
        async with _env() as env:
            before = await env.snapshot()
            for persona in ("org_admin", "superadmin"):
                response = await env.claim(
                    persona, CAND_P2_OPEN, ORG_A, selected=PROJ_A1
                )
                assert response.status_code == 403, (persona, response.text)
                assert response.json()["detail"]["code"] == "context_forbidden"
            assert await env.claims() == []
            after = await env.snapshot()
            after.pop("contract_migration_adjudication_claims", None)
            before.pop("contract_migration_adjudication_claims", None)
            assert after == before

    _run(scenario)


#: Every route, and what it answers per selection. "org" = only X-Org-Id sent;
#: None = no selection header at all.
#: The instrument is organisation-level (project None), seeded per test.
INSTRUMENT = "cd-matrix"


def _requests():
    body_evidence = {
        "project_id": PROJ_A1,
        "contract_id": CONTRACT,
        "query": "variation",
        "query_mode": "current_state",
    }
    q = {"organization_id": ORG_A}
    return {
        "catalogue": ("GET", "/api/contract-master/catalogue", {"params": q}),
        "instrument": ("GET", f"/api/contract-master/instruments/{INSTRUMENT}", {}),
        "projection": (
            "GET",
            f"/api/contract-master/instruments/{INSTRUMENT}/projection",
            {},
        ),
        "classification": (
            "POST",
            f"/api/contract-master/instruments/{INSTRUMENT}/classification",
            {"json": {"contract_document_type": "amendment", "expected_revision": 1}},
        ),
        "applicability": (
            "POST",
            f"/api/contract-master/instruments/{INSTRUMENT}/applicability",
            {
                "json": {
                    "project_id": PROJ_A1,
                    "contract_id": CONTRACT,
                    "kind": "APPLIED",
                }
            },
        ),
        "evidence": (
            "POST",
            "/api/contract-master/evidence/search",
            {"json": body_evidence},
        ),
        "inventory": (
            "GET",
            "/api/contract-master/reconciliation/inventory",
            {"params": q},
        ),
        "materialise": (
            "POST",
            "/api/contract-master/reconciliation/materialise",
            {"json": {"organization_id": ORG_A}},
        ),
        "review": (
            "GET",
            "/api/contract-master/reconciliation/candidates",
            {"params": q},
        ),
        "claim": (
            "POST",
            f"/api/contract-master/reconciliation/candidates/{CAND_P1_OPEN}/claim",
            {"params": q},
        ),
        "claim_unanchored": (
            "POST",
            f"/api/contract-master/reconciliation/candidates/{CAND_UNANCHORED}/claim",
            {"params": q},
        ),
        "adjudicate": (
            "POST",
            f"/api/contract-master/reconciliation/candidates/{CAND_P1_OPEN}/adjudicate",
            {
                "params": {**q, "owner_token": "none"},
                "json": {"scope_state": "PROJECT_SCOPE_CONFIRMED", "reason": "matrix"},
            },
        ),
        "promote": (
            "POST",
            f"/api/contract-master/reconciliation/candidates/{CAND_P1_READY}/promote",
            {"params": q, "json": {"contract_id": CONTRACT}},
        ),
        "capabilities": ("GET", "/api/contract-master/capabilities", {"params": q}),
    }


#: (persona, selection) -> {route: expected status}. Selection: A1 / A2 project,
#: "org" = organisation only, None = nothing selected.
MATRIX = {
    ("org_admin", PROJ_A1): {
        "catalogue": 200,
        "instrument": 200,
        "projection": 200,
        "classification": 200,
        "applicability": 201,
        "evidence": 200,
        "inventory": 200,
        "materialise": 200,
        "review": 200,
        "claim": 200,
        "claim_unanchored": 200,
        "adjudicate": 409,
        "promote": 200,
        "capabilities": 200,
    },
    ("org_admin", "org"): {
        # Organisation only: organisation-level work proceeds (the organisation-
        # level instrument, materialise, the unanchored candidate); anything that
        # belongs to a project (evidence, an applicability for A1, an anchored
        # candidate) needs that project selected.
        "catalogue": 200,
        "instrument": 200,
        "projection": 200,
        "classification": 200,
        "applicability": 400,
        "evidence": 400,
        "inventory": 200,
        "materialise": 200,
        "review": 200,
        "claim": 400,
        "claim_unanchored": 200,
        "adjudicate": 400,
        "promote": 400,
        "capabilities": 200,
    },
    ("org_admin", PROJ_A2): {
        # A2 is selected: the A1 candidates and the A1 search leave it.
        "catalogue": 200,
        "instrument": 200,
        "applicability": 403,
        "evidence": 403,
        "claim": 403,
        "adjudicate": 403,
        "promote": 403,
        "review": 200,
    },
    ("org_admin", None): {
        # Nothing selected: reads bounded by the policy proceed; every record
        # route and every write needs a selection (400).
        "catalogue": 200,
        "instrument": 400,
        "projection": 400,
        "classification": 400,
        "applicability": 400,
        "evidence": 400,
        "inventory": 200,
        "materialise": 400,
        "review": 200,
        "claim": 400,
        "claim_unanchored": 400,
        "adjudicate": 400,
        "promote": 400,
        "capabilities": 200,
    },
    ("project_admin_a1", "org"): {
        "catalogue": 200,
        "instrument": 200,
        "projection": 200,
        # Retyping the organisation-level instrument is organisation business.
        "classification": 403,
        "applicability": 400,
        "evidence": 400,
        "inventory": 403,
        "materialise": 403,
        "review": 200,
        "claim": 400,
        "claim_unanchored": 403,
        "adjudicate": 400,
        "promote": 400,
        "capabilities": 200,
    },
    ("project_admin_a1", None): {
        "catalogue": 200,
        "instrument": 400,
        "projection": 400,
        "classification": 400,
        "applicability": 400,
        "evidence": 400,
        "inventory": 403,
        "materialise": 400,
        "review": 200,
        "claim": 400,
        "claim_unanchored": 400,
        "adjudicate": 400,
        "promote": 400,
        "capabilities": 200,
    },
    ("superadmin", PROJ_A1): {
        "catalogue": 200,
        "instrument": 200,
        "projection": 200,
        "classification": 200,
        "applicability": 201,
        "evidence": 200,
        "inventory": 200,
        "materialise": 200,
        "review": 200,
        "claim": 200,
        "claim_unanchored": 200,
        "adjudicate": 409,
        "promote": 200,
        "capabilities": 200,
    },
    ("superadmin", "org"): {
        "catalogue": 200,
        "instrument": 200,
        "projection": 200,
        "classification": 200,
        "applicability": 400,
        "evidence": 400,
        "inventory": 200,
        "materialise": 200,
        "review": 200,
        "claim": 400,
        "claim_unanchored": 200,
        "adjudicate": 400,
        "promote": 400,
        "capabilities": 200,
    },
    ("superadmin", PROJ_B1): {
        # Selected org B: every org-A request leaves the selection. The org-A
        # instrument is looked up inside org B, so it is not found at all.
        "catalogue": 403,
        "instrument": 404,
        "projection": 404,
        "classification": 404,
        "applicability": 404,
        "evidence": 403,
        "inventory": 403,
        "materialise": 403,
        "review": 403,
        "claim": 403,
        "claim_unanchored": 403,
        "adjudicate": 403,
        "promote": 403,
        "capabilities": 403,
    },
    ("project_admin_a1", PROJ_A1): {
        # The organisation-level instrument is organisation business to retype.
        "catalogue": 200,
        "instrument": 200,
        "projection": 200,
        "classification": 403,
        "applicability": 201,
        "evidence": 200,
        "inventory": 403,
        "materialise": 403,
        "review": 200,
        "claim": 200,
        "claim_unanchored": 403,
        "adjudicate": 409,
        "promote": 200,
        "capabilities": 200,
    },
    ("project_admin_a1", PROJ_A2): {
        # Not a member of A2: the selection itself is refused, on every route.
        "catalogue": 403,
        "instrument": 403,
        "projection": 403,
        "classification": 403,
        "applicability": 403,
        "evidence": 403,
        "inventory": 403,
        "materialise": 403,
        "review": 403,
        "claim": 403,
        "claim_unanchored": 403,
        "adjudicate": 403,
        "promote": 403,
        "capabilities": 403,
    },
}


@pytest.mark.parametrize(
    "persona,selected",
    sorted(MATRIX, key=lambda key: (key[0], str(key[1]))),
    ids=lambda value: str(value),
)
def test_every_contract_master_route_under_every_selection(persona, selected) -> None:
    async def scenario() -> None:
        from rbac_backend.services.contract_document_store import (
            CONTRACT_DOCUMENTS_COLLECTION,
        )

        requests = _requests()
        # One database per row: within a row no answer depends on another route's
        # write (the adjudication uses a token nobody holds, and claim, promote and
        # classification touch different records), so a fresh seed per route bought
        # nothing but minutes.
        async with _env() as env:
            await env.db[CONTRACT_DOCUMENTS_COLLECTION].insert_one(
                {
                    "_id": INSTRUMENT,
                    "organization_id": ORG_A,
                    "document_id": "doc-a-open",
                    "contract_document_type": "general_conditions",
                    "scope_level": "organization",
                    "project_id": None,
                    "classification_revision": 1,
                    "projection_status": "PENDING",
                }
            )
            if selected == "org":
                selection = {"selected_org": ORG_A}
            else:
                selection = {"selected": selected}
            for route, expected in MATRIX[(persona, selected)].items():
                method, path, kwargs = requests[route]
                response = await env.call(persona, method, path, **selection, **kwargs)
                assert response.status_code != 500, (route, response.text)
                assert response.status_code == expected, (route, response.text)

    _run(scenario)


# --------------------------------------------------------------------------- #
# review round: anchors that fail, promotion that repeats, instruments
# --------------------------------------------------------------------------- #


def test_project_scope_promotion_works_from_the_inventory_with_no_hand_set_field() -> (
    None
):
    """Materialise -> claim -> adjudicate PROJECT -> promote, on a real ObjectId Document.

    Inventory never writes candidate.project_id; the project comes from the
    Document's own project, the trustworthy anchor.
    """

    async def scenario() -> None:
        from bson import ObjectId

        async with _env() as env:
            oid = ObjectId()
            await env.db.documents.insert_one(_document(oid, ORG_C, PROJ_C1))
            materialised = await env.call(
                "org_c_admin",
                "POST",
                "/api/contract-master/reconciliation/materialise",
                selected_org=ORG_C,
                json={"organization_id": ORG_C},
            )
            assert materialised.status_code == 200, materialised.text
            candidate_id = f"{PREFIX}:{oid}"
            assert "project_id" not in await env.candidate(candidate_id)

            claimed = await env.claim(
                "org_c_admin", candidate_id, ORG_C, selected=PROJ_C1
            )
            assert claimed.status_code == 200, claimed.text
            adjudicated = await env.adjudicate(
                "org_c_admin",
                candidate_id,
                ORG_C,
                claimed.json()["owner_token"],
                scope_state="PROJECT_SCOPE_CONFIRMED",
                selected=PROJ_C1,
            )
            assert adjudicated.status_code == 200, adjudicated.text
            promoted = await env.promote(
                "org_c_admin",
                candidate_id,
                ORG_C,
                contract_id=CONTRACT,
                selected=PROJ_C1,
            )
            assert promoted.status_code == 200, promoted.text
            assert promoted.json()["scope_level"] == "project"
            applicability = await env.db["contract_document_applicability"].find_one(
                {"contract_document_id": promoted.json()["contract_document_id"]}
            )
            assert applicability["project_id"] == PROJ_C1

    _run(scenario)


@pytest.mark.parametrize("candidate_id", [CAND_CONFLICT, CAND_INACTIVE])
def test_a_conflicted_anchor_is_organisation_business_and_never_promotes(
    candidate_id,
) -> None:
    """Disagreeing project fields, or an inactive project: not treated as org-owned."""

    async def scenario() -> None:
        async with _env() as env:
            before = await env.snapshot()
            refused = await env.claim(
                "project_admin_a1", candidate_id, ORG_A, selected=PROJ_A1
            )
            assert refused.status_code == 403, refused.text
            assert (
                refused.json()["detail"]
                == "Not authorized: organization_scope_required"
            )

            # Not stuck: organisation scope may act on it with only the org selected.
            claimed = await env.call(
                "org_admin",
                "POST",
                f"/api/contract-master/reconciliation/candidates/{candidate_id}/claim",
                selected_org=ORG_A,
                params={"organization_id": ORG_A},
            )
            assert claimed.status_code == 200, claimed.text

            after_claim = await env.snapshot()
            promoted = await env.call(
                "org_admin",
                "POST",
                f"/api/contract-master/reconciliation/candidates/{candidate_id}/promote",
                selected_org=ORG_A,
                params={"organization_id": ORG_A},
                json={"contract_id": CONTRACT},
            )
            assert promoted.status_code == 422, promoted.text
            assert "no trustworthy project anchor" in promoted.text
            assert await env.snapshot() == after_claim
            before.pop("contract_migration_adjudication_claims", None)
            after_claim.pop("contract_migration_adjudication_claims", None)
            assert after_claim == before

    _run(scenario)


def test_project_scope_needs_an_anchor_to_be_recorded() -> None:
    async def scenario() -> None:
        async with _env() as env:
            claimed = await env.call(
                "org_admin",
                "POST",
                f"/api/contract-master/reconciliation/candidates/{CAND_UNANCHORED}/claim",
                selected_org=ORG_A,
                params={"organization_id": ORG_A},
            )
            assert claimed.status_code == 200, claimed.text
            before = await env.candidate(CAND_UNANCHORED)
            response = await env.call(
                "org_admin",
                "POST",
                f"/api/contract-master/reconciliation/candidates/{CAND_UNANCHORED}/adjudicate",
                selected_org=ORG_A,
                params={
                    "organization_id": ORG_A,
                    "owner_token": claimed.json()["owner_token"],
                },
                json={"scope_state": "PROJECT_SCOPE_CONFIRMED", "reason": "no anchor"},
            )
            assert response.status_code == 422, response.text
            assert await env.candidate(CAND_UNANCHORED) == before

    _run(scenario)


def test_a_project_admin_cannot_override_an_organisation_scope_decision() -> None:
    """CAND_A_READY is anchored to A1 but already adjudicated ORG_SCOPE_CONFIRMED."""

    async def scenario() -> None:
        async with _env() as env:
            # Refused at the claim, so a project-tier lease cannot lock the
            # organisation out of its own decision.
            refused = await env.claim("project_admin_a1", CAND_A_READY, ORG_A)
            assert refused.status_code == 403, refused.text
            assert await env.claims() == []
            # Even holding a lease (seeded), the decision cannot be overridden.
            claimed = await env.claim("org_admin", CAND_A_READY, ORG_A)
            assert claimed.status_code == 200, claimed.text
            await env.db["contract_migration_adjudication_claims"].update_one(
                {"candidate_id": CAND_A_READY},
                {"$set": {"operator_id": "user-project_admin_a1"}},
            )
            before = await env.candidate(CAND_A_READY)
            for scope_state in ("PROJECT_SCOPE_CONFIRMED", "INVALID", "AMBIGUOUS"):
                response = await env.adjudicate(
                    "project_admin_a1",
                    CAND_A_READY,
                    ORG_A,
                    claimed.json()["owner_token"],
                    scope_state=scope_state,
                )
                assert response.status_code == 403, (scope_state, response.text)
            assert await env.candidate(CAND_A_READY) == before

    _run(scenario)


def test_a_second_promotion_is_a_409_and_changes_nothing() -> None:
    async def scenario() -> None:
        async with _env() as env:
            first = await env.promote("org_admin", CAND_A_READY, ORG_A)
            assert first.status_code == 200, first.text
            after_first = await env.snapshot()
            second = await env.promote("org_admin", CAND_A_READY, ORG_A)
            assert second.status_code == 409, second.text
            assert await env.snapshot() == after_first

    _run(scenario)


def test_a_promoted_candidate_cannot_be_re_adjudicated() -> None:
    async def scenario() -> None:
        async with _env() as env:
            assert (
                await env.promote("org_admin", CAND_A_READY, ORG_A)
            ).status_code == 200
            claimed = await env.claim("org_admin", CAND_A_READY, ORG_A)
            assert claimed.status_code == 200, claimed.text
            before = await env.candidate(CAND_A_READY)
            response = await env.adjudicate(
                "org_admin", CAND_A_READY, ORG_A, claimed.json()["owner_token"]
            )
            assert response.status_code == 409, response.text
            assert "already promoted" in response.text
            assert await env.candidate(CAND_A_READY) == before

    _run(scenario)


def test_a_decision_that_changes_inside_the_transaction_writes_nothing() -> None:
    """The promoted marker is a compare-and-set over the decision that was read."""

    async def scenario() -> None:
        from rbac_backend.services.contract_migration_reconciliation import (
            RECONCILIATION_COLLECTION,
        )
        from rbac_backend.services.contract_promotion import (
            ContractPromotionService,
            RevalidationRequired,
        )

        class ChangedUnderfoot(ContractPromotionService):
            async def _apply(self, plan, *, session):
                # A re-adjudication lands after the read, before the marker.
                await self._db[RECONCILIATION_COLLECTION].update_one(
                    {"_id": plan["candidate_id"]},
                    {"$set": {"type_state": "TYPE_SUGGESTED"}},
                    session=session,
                )
                await super()._apply(plan, session=session)

        async with _env() as env:
            everything = await env.snapshot()
            with pytest.raises(RevalidationRequired):
                await ChangedUnderfoot(env.db, env.mongo).promote(
                    CAND_A_READY, organization_id=ORG_A, actor_id="alice"
                )
            # The instrument, fact and receipt rolled back with the marker - and so
            # did the in-transaction change itself.
            assert await env.snapshot() == everything

    _run(scenario)


def test_the_promotion_project_must_be_the_anchor() -> None:
    """candidate.project_id A1 against a Document of A2: refused at the service."""

    async def scenario() -> None:
        from rbac_backend.services.contract_promotion import (
            ContractPromotionService,
            NotPromotable,
        )

        async with _env() as env:
            everything = await env.snapshot()
            with pytest.raises(NotPromotable, match="no trustworthy project anchor"):
                await ContractPromotionService(env.db, env.mongo).promote(
                    CAND_CONFLICT,
                    organization_id=ORG_A,
                    actor_id="alice",
                    contract_id=CONTRACT,
                )
            assert await env.snapshot() == everything

    _run(scenario)


async def _seed_instruments(env) -> None:
    from rbac_backend.services.contract_document_store import (
        CONTRACT_DOCUMENTS_COLLECTION,
    )

    base = {
        "contract_document_type": "general_conditions",
        "classification_revision": 1,
        "projection_status": "PENDING",
    }
    await env.db[CONTRACT_DOCUMENTS_COLLECTION].insert_many(
        [
            {
                **base,
                "_id": "cd-org",
                "document_id": "doc-org-level",
                "organization_id": ORG_A,
                "scope_level": "organization",
                "project_id": None,
            },
            {
                **base,
                "_id": "cd-a1",
                "document_id": "doc-p1-open",
                "organization_id": ORG_A,
                "scope_level": "project",
                "project_id": PROJ_A1,
            },
            {
                **base,
                "_id": "cd-a2",
                "document_id": "doc-p2-open",
                "organization_id": ORG_A,
                "scope_level": "project",
                "project_id": PROJ_A2,
            },
            {
                **base,
                "_id": "cd-b",
                "document_id": "doc-b-open",
                "organization_id": ORG_B,
                "scope_level": "organization",
                "project_id": None,
            },
        ]
    )


def test_the_catalogue_is_bounded_to_what_the_caller_may_see() -> None:
    async def scenario() -> None:
        async with _env() as env:
            await _seed_instruments(env)

            async def listed(persona, **selection):
                response = await env.call(
                    persona,
                    "GET",
                    "/api/contract-master/catalogue",
                    params={"organization_id": ORG_A},
                    **selection,
                )
                assert response.status_code == 200, response.text
                return {
                    item["contract_document_id"] for item in response.json()["items"]
                }

            assert await listed("project_admin_a1", selected=PROJ_A1) == {
                "cd-org",
                "cd-a1",
            }
            assert await listed("org_admin", selected=PROJ_A1) == {"cd-org", "cd-a1"}
            assert await listed("org_admin", selected_org=ORG_A) == {
                "cd-org",
                "cd-a1",
                "cd-a2",
            }

    _run(scenario)


def test_a_project_admin_classifies_their_projects_instrument_not_the_organisations() -> (
    None
):
    async def scenario() -> None:
        async with _env() as env:
            await _seed_instruments(env)
            body = {"contract_document_type": "amendment", "expected_revision": 1}
            path = "/api/contract-master/instruments/{}/classification"
            own = await env.call(
                "project_admin_a1",
                "POST",
                path.format("cd-a1"),
                selected=PROJ_A1,
                json=body,
            )
            assert own.status_code == 200, own.text
            org_level = await env.call(
                "project_admin_a1",
                "POST",
                path.format("cd-org"),
                selected=PROJ_A1,
                json=body,
            )
            assert org_level.status_code == 403, org_level.text
            by_org_admin = await env.call(
                "org_admin", "POST", path.format("cd-org"), selected=PROJ_A1, json=body
            )
            assert by_org_admin.status_code == 200, by_org_admin.text

    _run(scenario)


def test_another_organisations_instrument_is_indistinguishable_from_a_missing_one() -> (
    None
):
    async def scenario() -> None:
        async with _env() as env:
            await _seed_instruments(env)
            foreign = await env.call(
                "org_admin",
                "GET",
                "/api/contract-master/instruments/cd-b",
                selected=PROJ_A1,
            )
            missing = await env.call(
                "org_admin",
                "GET",
                "/api/contract-master/instruments/cd-none",
                selected=PROJ_A1,
            )
            assert (foreign.status_code, foreign.json()) == (
                missing.status_code,
                missing.json(),
            )
            assert foreign.status_code == 404

    _run(scenario)


# --------------------------------------------------------------------------- #
# third review round: indexes, fingerprints, leases, conflicted decisions
# --------------------------------------------------------------------------- #


def test_two_project_scope_promotions_in_one_database() -> None:
    """With the production unique indexes, every promotion writes its own event."""

    async def scenario() -> None:
        async with _env() as env:
            first = await env.promote(
                "org_admin",
                CAND_P1_READY,
                ORG_A,
                contract_id=CONTRACT,
                selected=PROJ_A1,
            )
            assert first.status_code == 200, first.text
            second = await env.promote(
                "org_admin",
                CAND_P2_READY,
                ORG_A,
                contract_id=CONTRACT,
                selected=PROJ_A2,
            )
            assert second.status_code == 200, second.text
            events = (
                await env.db["contract_document_applicability_events"]
                .find({})
                .to_list(length=None)
            )
            assert len(events) == 2
            assert all(event["event_id"] == event["_id"] for event in events)

    _run(scenario)


def test_a_document_that_already_has_an_instrument_is_not_promoted_again() -> None:
    """A different candidate for the same Document: 422, not a false 'already promoted'."""

    async def scenario() -> None:
        from rbac_backend.services.contract_document_store import (
            CONTRACT_DOCUMENTS_COLLECTION,
        )

        async with _env() as env:
            await env.db[CONTRACT_DOCUMENTS_COLLECTION].insert_one(
                {
                    "_id": "cd-existing",
                    "organization_id": ORG_A,
                    "document_id": "doc-a-ready",
                    "scope_level": "organization",
                    "project_id": None,
                }
            )
            everything = await env.snapshot()
            response = await env.promote("org_admin", CAND_A_READY, ORG_A)
            assert response.status_code == 422, response.text
            assert "already has an instrument" in response.text
            assert await env.snapshot() == everything

    _run(scenario)


def test_adjudication_records_the_fingerprint_it_reviewed() -> None:
    async def scenario() -> None:
        async with _env() as env:
            await env.db[RECONCILIATION_COLLECTION_NAME].update_one(
                {"_id": CAND_A_OPEN}, {"$unset": {"source_fingerprint": ""}}
            )
            claimed = await env.claim("org_admin", CAND_A_OPEN, ORG_A)
            response = await env.adjudicate(
                "org_admin", CAND_A_OPEN, ORG_A, claimed.json()["owner_token"]
            )
            assert response.status_code == 200, response.text
            assert (await env.candidate(CAND_A_OPEN))[
                "source_fingerprint"
            ] == "sha-doc-a-open"

            # The Document changes after review: promotion is refused, nothing written.
            await env.db.documents.update_one(
                {"_id": "doc-a-open"}, {"$set": {"checksum": "sha-changed"}}
            )
            everything = await env.snapshot()
            promoted = await env.promote("org_admin", CAND_A_OPEN, ORG_A)
            assert promoted.status_code == 409, promoted.text
            assert await env.snapshot() == everything

    _run(scenario)


def test_a_candidate_never_fingerprinted_cannot_promote_a_fingerprinted_document() -> (
    None
):
    async def scenario() -> None:
        async with _env() as env:
            await env.db[RECONCILIATION_COLLECTION_NAME].update_one(
                {"_id": CAND_A_READY}, {"$unset": {"source_fingerprint": ""}}
            )
            everything = await env.snapshot()
            response = await env.promote("org_admin", CAND_A_READY, ORG_A)
            assert response.status_code == 409, response.text
            assert await env.snapshot() == everything

    _run(scenario)


def test_a_successful_adjudication_releases_the_lease() -> None:
    async def scenario() -> None:
        async with _env() as env:
            claimed = await env.claim("org_admin", CAND_P1_OPEN, ORG_A)
            decided = await env.adjudicate(
                "org_admin",
                CAND_P1_OPEN,
                ORG_A,
                claimed.json()["owner_token"],
                scope_state="PROJECT_SCOPE_CONFIRMED",
            )
            assert decided.status_code == 200, decided.text
            assert await env.claims() == []
            again = await env.claim("project_admin_a1", CAND_P1_OPEN, ORG_A)
            assert again.status_code == 200, again.text

    _run(scenario)


def test_an_expired_lease_is_taken_over_and_no_longer_honoured() -> None:
    async def scenario() -> None:
        from datetime import timedelta

        async with _env() as env:
            past = datetime.utcnow() - timedelta(hours=2)
            await env.db["contract_migration_adjudication_claims"].insert_one(
                {
                    "_id": f"contract-migration-claim:{CAND_P1_OPEN}",
                    "candidate_id": CAND_P1_OPEN,
                    "operator_id": "user-somebody",
                    "owner_token": "stale-token",
                    "claimed_at": past,
                    "lease_expires_at": past + timedelta(minutes=30),
                }
            )
            before = await env.candidate(CAND_P1_OPEN)
            stale = await env.adjudicate(
                "org_admin",
                CAND_P1_OPEN,
                ORG_A,
                "stale-token",
                scope_state="PROJECT_SCOPE_CONFIRMED",
            )
            assert stale.status_code == 409, stale.text
            assert await env.candidate(CAND_P1_OPEN) == before

            taken = await env.claim("org_admin", CAND_P1_OPEN, ORG_A)
            assert taken.status_code == 200, taken.text
            (row,) = await env.claims()
            assert row["operator_id"] == "user-org_admin"
            assert row["owner_token"] == taken.json()["owner_token"]

    _run(scenario)


def test_a_conflicted_candidate_can_only_be_set_aside() -> None:
    """Organisation scope may mark it INVALID or AMBIGUOUS, never a promotable scope."""

    async def scenario() -> None:
        async with _env() as env:
            for scope_state, expected in (
                ("ORG_SCOPE_CONFIRMED", 422),
                ("PROJECT_SCOPE_CONFIRMED", 422),
                ("INVALID", 200),
            ):
                claimed = await env.call(
                    "org_admin",
                    "POST",
                    f"/api/contract-master/reconciliation/candidates/{CAND_INACTIVE}/claim",
                    selected_org=ORG_A,
                    params={"organization_id": ORG_A},
                )
                assert claimed.status_code == 200, (scope_state, claimed.text)
                response = await env.call(
                    "org_admin",
                    "POST",
                    f"/api/contract-master/reconciliation/candidates/{CAND_INACTIVE}/adjudicate",
                    selected_org=ORG_A,
                    params={
                        "organization_id": ORG_A,
                        "owner_token": claimed.json()["owner_token"],
                    },
                    json={"scope_state": scope_state, "reason": "conflicted anchor"},
                )
                assert response.status_code == expected, (scope_state, response.text)
                if expected != 200:
                    await env.db["contract_migration_adjudication_claims"].delete_many(
                        {}
                    )
            assert (await env.candidate(CAND_INACTIVE))["scope_state"] == "INVALID"

    _run(scenario)


def test_a_project_admin_cannot_invalidate_a_candidate() -> None:
    """INVALID is terminal for the whole organisation's migration."""

    async def scenario() -> None:
        async with _env() as env:
            claimed = await env.claim("project_admin_a1", CAND_P1_OPEN, ORG_A)
            assert claimed.status_code == 200, claimed.text
            before = await env.candidate(CAND_P1_OPEN)
            response = await env.adjudicate(
                "project_admin_a1",
                CAND_P1_OPEN,
                ORG_A,
                claimed.json()["owner_token"],
                scope_state="INVALID",
            )
            assert response.status_code == 403, response.text
            assert await env.candidate(CAND_P1_OPEN) == before

    _run(scenario)


def test_the_review_queue_says_what_each_row_needs() -> None:
    async def scenario() -> None:
        async with _env() as env:
            response = await env.call(
                "org_admin",
                "GET",
                "/api/contract-master/reconciliation/candidates",
                selected_org=ORG_A,
                params={"organization_id": ORG_A},
            )
            assert response.status_code == 200, response.text
            rows = {row["candidate_id"]: row for row in response.json()["candidates"]}
            assert rows[CAND_P1_OPEN]["project_anchor"] == PROJ_A1
            assert rows[CAND_P1_OPEN]["anchor_conflict"] is False
            assert rows[CAND_UNANCHORED]["project_anchor"] is None
            assert rows[CAND_INACTIVE]["anchor_conflict"] is True
            assert any(
                "anchor" in reason
                for reason in rows[CAND_INACTIVE]["promotion_blocked_reasons"]
            )

    _run(scenario)


def test_organisation_level_writes_need_a_selected_organisation() -> None:
    """CL-4A: a mutating operation states its scope; nothing selected is 400."""

    async def scenario() -> None:
        async with _env() as env:
            before = await env.snapshot()
            for persona in ("org_admin", "superadmin"):
                materialise = await env.call(
                    persona,
                    "POST",
                    "/api/contract-master/reconciliation/materialise",
                    json={"organization_id": ORG_A},
                )
                assert materialise.status_code == 400, (persona, materialise.text)
                claim = await env.claim(persona, CAND_UNANCHORED, ORG_A, selected=None)
                assert claim.status_code == 400, (persona, claim.text)
            after = await env.snapshot()
            # The claim route creates its index before authorising: an empty
            # collection, not a claim.
            assert after.pop("contract_migration_adjudication_claims", []) == []
            before.pop("contract_migration_adjudication_claims", None)
            assert after == before

    _run(scenario)


def test_an_organisation_level_instrument_needs_only_the_organisation_selected() -> (
    None
):
    async def scenario() -> None:
        async with _env() as env:
            await _seed_instruments(env)
            org_level = await env.call(
                "org_admin",
                "GET",
                "/api/contract-master/instruments/cd-org",
                selected_org=ORG_A,
            )
            assert org_level.status_code == 200, org_level.text
            project_level = await env.call(
                "org_admin",
                "GET",
                "/api/contract-master/instruments/cd-a1",
                selected_org=ORG_A,
            )
            assert project_level.status_code == 400, project_level.text
            assert project_level.json()["detail"]["code"] == "selection_required"

    _run(scenario)


RECONCILIATION_COLLECTION_NAME = "contract_document_reconciliation"


@pytest.mark.parametrize("receipt_exists", [False, True])
def test_a_write_conflict_is_only_already_promoted_when_a_receipt_says_so(
    receipt_exists,
) -> None:
    """WriteConflict (112) means someone else wrote first - not necessarily a promotion."""

    async def scenario() -> None:
        from pymongo.errors import OperationFailure

        from rbac_backend.services.contract_promotion import (
            PROMOTION_RECEIPTS_COLLECTION,
            AlreadyPromoted,
            ContractPromotionService,
            RevalidationRequired,
        )

        class Conflicted(ContractPromotionService):
            async def _apply(self, plan, *, session):
                raise OperationFailure("WriteConflict", code=112)

        async with _env() as env:
            if receipt_exists:
                await env.db[PROMOTION_RECEIPTS_COLLECTION].insert_one(
                    {"_id": f"promotion:{CAND_A_READY}"}
                )
            expected = AlreadyPromoted if receipt_exists else RevalidationRequired
            with pytest.raises(expected):
                await Conflicted(env.db, env.mongo).promote(
                    CAND_A_READY, organization_id=ORG_A, actor_id="alice"
                )

    _run(scenario)


def test_an_unrelated_duplicate_key_is_not_reported_as_already_promoted() -> None:
    async def scenario() -> None:
        from pymongo.errors import DuplicateKeyError

        from rbac_backend.services.contract_promotion import (
            AlreadyPromoted,
            ContractPromotionService,
            NotPromotable,
        )

        class Collides(ContractPromotionService):
            async def _apply(self, plan, *, session):
                raise DuplicateKeyError(
                    "E11000", code=11000, details={"keyPattern": {"event_id": 1}}
                )

        async with _env() as env:
            with pytest.raises(DuplicateKeyError) as raised:
                await Collides(env.db, env.mongo).promote(
                    CAND_A_READY, organization_id=ORG_A, actor_id="alice"
                )
            assert not isinstance(raised.value, (AlreadyPromoted, NotPromotable))

    _run(scenario)


# --------------------------------------------------------------------------- #
# verification round: leases, fingerprints, promotion inputs
# --------------------------------------------------------------------------- #


def test_organisation_scope_breaks_a_project_tier_lease() -> None:
    """A project-tier lease never locks the organisation out of its own decision."""

    async def scenario() -> None:
        async with _env() as env:
            held = await env.claim("project_admin_a1", CAND_P1_OPEN, ORG_A)
            assert held.status_code == 200, held.text
            taken = await env.claim("org_admin", CAND_P1_OPEN, ORG_A)
            assert taken.status_code == 200, taken.text
            (row,) = await env.claims()
            assert row["operator_id"] == "user-org_admin"
            assert row["organization_wide"] is True
            # The project-tier holder cannot take it back, nor use its old token.
            again = await env.claim("project_admin_a1", CAND_P1_OPEN, ORG_A)
            assert again.status_code == 409, again.text
            stale = await env.adjudicate(
                "project_admin_a1",
                CAND_P1_OPEN,
                ORG_A,
                held.json()["owner_token"],
                scope_state="PROJECT_SCOPE_CONFIRMED",
            )
            assert stale.status_code == 409, stale.text

    _run(scenario)


def test_an_operator_can_reclaim_their_own_lease_after_a_refusal() -> None:
    async def scenario() -> None:
        async with _env() as env:
            first = await env.claim("project_admin_a1", CAND_P1_OPEN, ORG_A)
            refused = await env.adjudicate(
                "project_admin_a1",
                CAND_P1_OPEN,
                ORG_A,
                first.json()["owner_token"],
                scope_state="INVALID",
            )
            assert refused.status_code == 403, refused.text
            second = await env.claim("project_admin_a1", CAND_P1_OPEN, ORG_A)
            assert second.status_code == 200, second.text
            assert second.json()["owner_token"] != first.json()["owner_token"]

    _run(scenario)


def test_a_lease_token_proves_nothing_for_another_operator() -> None:
    async def scenario() -> None:
        async with _env() as env:
            alice = await env.claim("org_admin", CAND_P1_OPEN, ORG_A)
            before = await env.candidate(CAND_P1_OPEN)
            response = await env.adjudicate(
                "project_admin_a1",
                CAND_P1_OPEN,
                ORG_A,
                alice.json()["owner_token"],
                scope_state="PROJECT_SCOPE_CONFIRMED",
            )
            assert response.status_code == 409, response.text
            assert "no longer live" in response.text
            assert await env.candidate(CAND_P1_OPEN) == before

    _run(scenario)


def test_an_echoed_fingerprint_ties_the_decision_to_what_was_reviewed() -> None:
    async def scenario() -> None:
        async with _env() as env:
            review = await env.call(
                "org_admin",
                "GET",
                "/api/contract-master/reconciliation/candidates",
                selected=PROJ_A1,
                params={"organization_id": ORG_A},
            )
            rows = {row["candidate_id"]: row for row in review.json()["candidates"]}
            seen = rows[CAND_P1_OPEN]["document_fingerprint"]
            assert seen == "sha-doc-p1-open"

            await env.db.documents.update_one(
                {"_id": "doc-p1-open"},
                {"$set": {"checksum": "sha-edited-after-review"}},
            )
            claimed = await env.claim("org_admin", CAND_P1_OPEN, ORG_A)
            before = await env.candidate(CAND_P1_OPEN)
            response = await env.call(
                "org_admin",
                "POST",
                f"/api/contract-master/reconciliation/candidates/{CAND_P1_OPEN}/adjudicate",
                selected=PROJ_A1,
                params={
                    "organization_id": ORG_A,
                    "owner_token": claimed.json()["owner_token"],
                },
                json={
                    "scope_state": "PROJECT_SCOPE_CONFIRMED",
                    "contract_document_type": "general_conditions",
                    "reason": "reviewed",
                    "expected_fingerprint": seen,
                },
            )
            assert response.status_code == 409, response.text
            assert "changed since it was reviewed" in response.text
            assert await env.candidate(CAND_P1_OPEN) == before

    _run(scenario)


def test_production_sha256_fingerprints_are_recorded_and_enforced() -> None:
    """Production Documents carry sha256, not checksum."""

    async def scenario() -> None:
        async with _env() as env:
            await env.db.documents.update_one(
                {"_id": "doc-a-open"},
                {"$unset": {"checksum": ""}, "$set": {"sha256": "a" * 64}},
            )
            claimed = await env.claim("org_admin", CAND_A_OPEN, ORG_A)
            decided = await env.adjudicate(
                "org_admin", CAND_A_OPEN, ORG_A, claimed.json()["owner_token"]
            )
            assert decided.status_code == 200, decided.text
            assert (await env.candidate(CAND_A_OPEN))["source_fingerprint"] == "a" * 64

            # The fingerprint vanishes from the document: refused, nothing written.
            await env.db.documents.update_one(
                {"_id": "doc-a-open"}, {"$unset": {"sha256": ""}}
            )
            everything = await env.snapshot()
            promoted = await env.promote("org_admin", CAND_A_OPEN, ORG_A)
            assert promoted.status_code == 409, promoted.text
            assert "no longer carries" in promoted.text
            assert await env.snapshot() == everything

    _run(scenario)


def test_promotion_inputs_are_validated_not_dropped() -> None:
    async def scenario() -> None:
        async with _env() as env:
            everything = await env.snapshot()
            bad_date = await env.call(
                "org_admin",
                "POST",
                f"/api/contract-master/reconciliation/candidates/{CAND_P1_READY}/promote",
                selected=PROJ_A1,
                params={"organization_id": ORG_A},
                json={"contract_id": CONTRACT, "effective_from": "01/03/2026"},
            )
            assert bad_date.status_code == 422, bad_date.text
            contract_at_org_scope = await env.promote(
                "org_admin", CAND_A_READY, ORG_A, contract_id=CONTRACT
            )
            assert contract_at_org_scope.status_code == 422, contract_at_org_scope.text
            assert "organisation-scoped" in contract_at_org_scope.text
            assert await env.snapshot() == everything

            dated = await env.call(
                "org_admin",
                "POST",
                f"/api/contract-master/reconciliation/candidates/{CAND_P1_READY}/promote",
                selected=PROJ_A1,
                params={"organization_id": ORG_A},
                json={"contract_id": CONTRACT, "effective_from": "2026-03-01"},
            )
            assert dated.status_code == 200, dated.text
            event = await env.db["contract_document_applicability_events"].find_one({})
            assert event["effective_at"] == "2026-03-01"

    _run(scenario)


def test_a_genuine_repeat_is_already_promoted_even_without_the_marker() -> None:
    """The instrument exists but the promoted flag was lost: 409, never 422."""

    async def scenario() -> None:
        async with _env() as env:
            assert (
                await env.promote("org_admin", CAND_A_READY, ORG_A)
            ).status_code == 200
            await env.db[RECONCILIATION_COLLECTION_NAME].update_one(
                {"_id": CAND_A_READY}, {"$unset": {"promoted": ""}}
            )
            everything = await env.snapshot()
            again = await env.promote("org_admin", CAND_A_READY, ORG_A)
            assert again.status_code == 409, again.text
            assert "already promoted" in again.text
            assert await env.snapshot() == everything

    _run(scenario)


# --------------------------------------------------------------------------- #
# final delta: the write is a compare-and-set; echoes and dates are exact
# --------------------------------------------------------------------------- #


def test_a_decision_recorded_mid_adjudication_is_not_overwritten(monkeypatch) -> None:
    """A replaced holder's in-flight write matches nothing once another decision lands."""

    async def scenario() -> None:
        from rbac_backend.services import contract_migration_adjudication as module
        from rbac_backend.services.contract_migration_adjudication import (
            CandidateClaim,
            ConflictingAdjudication,
            ContractMigrationAdjudication,
        )
        from rbac_backend.services.contract_migration_reconciliation import (
            ScopeClassificationState,
        )

        async with _env() as env:
            service = ContractMigrationAdjudication(env.db)
            await service.ensure_indexes()
            held = await service.claim(
                CAND_P1_OPEN, organization_id=ORG_A, operator_id="user-project_admin_a1"
            )
            real = module.resolve_canonical_document

            async def meanwhile(db, document_id, **kwargs):
                # Between this call's read and its write, the organisation decides.
                await env.db[RECONCILIATION_COLLECTION_NAME].update_one(
                    {"_id": CAND_P1_OPEN},
                    {
                        "$set": {
                            "scope_state": "ORG_SCOPE_CONFIRMED",
                            "adjudicated_by": "user-org_admin",
                            "adjudicated_at": datetime.utcnow(),
                        }
                    },
                )
                return await real(db, document_id, **kwargs)

            monkeypatch.setattr(module, "resolve_canonical_document", meanwhile)
            with pytest.raises(ConflictingAdjudication):
                await service.adjudicate(
                    CandidateClaim(
                        candidate_id=CAND_P1_OPEN,
                        operator_id="user-project_admin_a1",
                        owner_token=held.owner_token,
                    ),
                    organization_id=ORG_A,
                    scope_state=ScopeClassificationState.PROJECT_SCOPE_CONFIRMED,
                    contract_document_type=None,
                    reason="in flight",
                )
            row = await env.candidate(CAND_P1_OPEN)
            assert row["scope_state"] == "ORG_SCOPE_CONFIRMED"
            assert row["adjudicated_by"] == "user-org_admin"

    _run(scenario)


def test_an_explicit_null_fingerprint_echo_is_checked() -> None:
    """The review showed no fingerprint; the document has since gained one."""

    async def scenario() -> None:
        async with _env() as env:
            await env.db.documents.update_one(
                {"_id": "doc-p1-open"}, {"$set": {"checksum": "sha-arrived-later"}}
            )
            claimed = await env.claim("org_admin", CAND_P1_OPEN, ORG_A)
            before = await env.candidate(CAND_P1_OPEN)
            response = await env.call(
                "org_admin",
                "POST",
                f"/api/contract-master/reconciliation/candidates/{CAND_P1_OPEN}/adjudicate",
                selected=PROJ_A1,
                params={
                    "organization_id": ORG_A,
                    "owner_token": claimed.json()["owner_token"],
                },
                json={
                    "scope_state": "PROJECT_SCOPE_CONFIRMED",
                    "reason": "reviewed",
                    "expected_fingerprint": None,
                },
            )
            assert response.status_code == 409, response.text
            assert await env.candidate(CAND_P1_OPEN) == before

    _run(scenario)


def test_a_numeric_effective_date_is_refused_not_read_as_a_timestamp() -> None:
    async def scenario() -> None:
        async with _env() as env:
            everything = await env.snapshot()
            response = await env.call(
                "org_admin",
                "POST",
                f"/api/contract-master/reconciliation/candidates/{CAND_P1_READY}/promote",
                selected=PROJ_A1,
                params={"organization_id": ORG_A},
                json={"contract_id": CONTRACT, "effective_from": 20260301},
            )
            assert response.status_code == 422, response.text
            assert await env.snapshot() == everything

    _run(scenario)


def test_reclaiming_your_own_lease_does_not_extend_it() -> None:
    async def scenario() -> None:
        async with _env() as env:
            first = await env.claim("org_admin", CAND_P1_OPEN, ORG_A)
            (before,) = await env.claims()
            second = await env.claim("org_admin", CAND_P1_OPEN, ORG_A)
            assert second.status_code == 200, second.text
            (after,) = await env.claims()
            assert after["lease_expires_at"] == before["lease_expires_at"]
            assert after["owner_token"] == second.json()["owner_token"]
            assert after["owner_token"] != first.json()["owner_token"]

    _run(scenario)


# --------------------------------------------------------------------------- #
# capabilities mirror the routes they describe, under the current selection
# --------------------------------------------------------------------------- #

CAPABILITY_MATRIX = {
    # (persona, selection) -> expected flags. Selection: a project id, "org"
    # (only X-Org-Id), None (nothing selected), or "org+query" (only X-Org-Id,
    # with ?project_id=PROJ_A1 in the query - a filter, never the selection).
    ("superadmin", None): {
        # Nothing selected: every record route and write answers 400.
        "can_review_migration": True,
        "can_view_instruments": False,
        "can_promote": False,
        "can_manage_classification": False,
        "can_manage_applicability": False,
        "can_manage_organization_migration": False,
    },
    ("org_admin", None): {
        "can_review_migration": True,
        "can_view_instruments": False,
        "can_promote": False,
        "can_manage_classification": False,
        "can_manage_organization_migration": False,
    },
    ("project_admin_a1", "org+query"): {
        # The query names A1, but the navbar selected only the organisation.
        "can_promote": False,
        "can_manage_classification": False,
        "can_manage_applicability": False,
    },
    ("project_user", PROJ_A1): {
        "can_review_migration": False,
        "can_promote": False,
        "can_manage_classification": False,
        "can_manage_applicability": False,
        "can_manage_organization_migration": False,
        "can_upload_organization_scope": False,
    },
    ("project_admin_a1", PROJ_A1): {
        "can_review_migration": True,
        "can_promote": True,
        "can_manage_applicability": True,
        "can_manage_classification": True,
        "can_manage_organization_migration": False,
        "can_upload_organization_scope": False,
    },
    ("project_admin_a1", "org"): {
        # Nothing it may act on is actionable without its project selected.
        "can_review_migration": True,
        "can_promote": False,
        "can_manage_applicability": False,
        "can_manage_classification": False,
        "can_manage_organization_migration": False,
    },
    ("org_admin", "org"): {
        "can_review_migration": True,
        "can_promote": True,
        "can_manage_applicability": False,
        "can_manage_classification": True,
        "can_manage_organization_migration": True,
        "can_upload_organization_scope": True,
    },
    ("org_admin", PROJ_A1): {
        "can_review_migration": True,
        "can_promote": True,
        "can_manage_applicability": True,
        "can_manage_classification": True,
        "can_manage_organization_migration": True,
    },
}


@pytest.mark.parametrize(
    "persona,selected",
    sorted(CAPABILITY_MATRIX, key=lambda key: (key[0], str(key[1]))),
    ids=lambda value: str(value),
)
def test_capabilities_match_the_route_gates(persona, selected) -> None:
    async def scenario() -> None:
        async with _env() as env:
            params = {"organization_id": ORG_A}
            if selected in ("org", "org+query"):
                selection = {"selected_org": ORG_A}
                if selected == "org+query":
                    params["project_id"] = PROJ_A1
            else:
                selection = {"selected": selected}
            response = await env.call(
                persona,
                "GET",
                "/api/contract-master/capabilities",
                params=params,
                **selection,
            )
            assert response.status_code == 200, response.text
            body = response.json()
            for flag, expected in CAPABILITY_MATRIX[(persona, selected)].items():
                assert body[flag] is expected, (flag, body)

    _run(scenario)
