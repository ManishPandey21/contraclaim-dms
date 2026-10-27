"""A matrix row's ownership is server-controlled and cannot be moved by an update.

`PATCH /api/arbitration/cases/{case_id}/{matrix_slug}/{row_id}` authorises the
URL case, then `$set`s the client body onto the row. `case_id`,
`organization_id`, `project_id` and `contract_id` were not server-controlled, so
an editor of one case could move a row - approval included - into any other
case, in any tenant, that they cannot otherwise touch.

These tests drive the real arbitration router, `PolicyService`, `ScopeService`
and `ArbitrationCaseWorkspaceService` over an in-memory Mongo boundary. Only the
permission-grant and subscription-entitlement seams are patched (as in
`test_cl4a_documents_contracts_scope.py`), so case scope runs for real.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from rbac_backend.core.database import get_database as _original_get_database
from rbac_backend.core.database import get_db
from rbac_backend.core.errors import register_domain_error_handler
from rbac_backend.core.security import get_current_user
from rbac_backend.routers.arbitration_drafting import router as arbitration_router
from rbac_backend.services.entitlement_service import EntitlementService
from rbac_backend.services.permission_service import PermissionService
from rbac_backend.tests.test_hindrance_register_http import _Collection, _Database, _matches, _principal


class _WorkspaceCollection(_Collection):
    """`_Collection` plus the bulk writes that readiness recomputation uses."""

    async def update_many(self, query: dict[str, Any], update: dict[str, Any], *args: Any, **kwargs: Any):
        matched = 0
        for row in self.docs:
            if _matches(row, query):
                self._apply(row, update, inserting=False)
                matched += 1
        return SimpleNamespace(matched_count=matched, modified_count=matched)

    async def delete_many(self, query: dict[str, Any], *args: Any, **kwargs: Any):
        before = len(self.docs)
        self.docs[:] = [row for row in self.docs if not _matches(row, query)]
        return SimpleNamespace(deleted_count=before - len(self.docs))

    async def insert_many(self, documents: list[dict[str, Any]], *args: Any, **kwargs: Any):
        for document in documents:
            await self.insert_one(document)
        return SimpleNamespace(inserted_ids=[document.get("_id") for document in documents])


class _WorkspaceDatabase(_Database):
    def __getattr__(self, name: str) -> _Collection:
        if name.startswith("_") or name == "client":
            raise AttributeError(name)
        return self._collections.setdefault(name, _WorkspaceCollection(name))


ORG_A, ORG_B = "org-A", "org-B"
A1, A2, B1 = "proj-A1", "proj-A2", "proj-B1"
CASE_A1, CASE_A2, CASE_B1 = "case-a1", "case-a2", "case-b1"
INDEX = "document-index"

VIEW, EDIT, APPROVE = "dms.arbitration.view", "dms.arbitration.edit", "dms.arbitration.approve"

EDITOR_A1 = _principal("u-editor-a1", ["projectuser"], ORG_A, [A1])
REVIEWER_A1 = _principal("u-reviewer-a1", ["projectuser", "contract_reviewer"], ORG_A, [A1])
ADMIN_B = _principal("u-orgadmin-b", ["orgadmin"], ORG_B)

GRANTS: dict[str, set[str]] = {
    EDITOR_A1.id: {VIEW, EDIT},
    REVIEWER_A1.id: {VIEW, EDIT, APPROVE},
    ADMIN_B.id: {VIEW, EDIT, APPROVE},
}

OWNERSHIP_MOVES = [
    pytest.param({"case_id": CASE_B1, "organization_id": ORG_B, "project_id": B1}, id="into-a-foreign-tenant"),
    pytest.param({"case_id": CASE_A2, "project_id": A2}, id="into-a-sibling-project"),
    pytest.param({"case_id": CASE_B1}, id="case-only"),
    pytest.param({"organization_id": ORG_B}, id="organization-only"),
    pytest.param({"project_id": A2}, id="project-only"),
    pytest.param({"contract_id": "other-contract"}, id="contract-only"),
]


@pytest.fixture(autouse=True)
def _authorization_seams(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _has_permission(self, user_id: str, permission_name: str, **_kwargs: Any) -> bool:
        return permission_name in GRANTS.get(str(user_id), set())

    async def _entitled(self, **_kwargs: Any):
        return True, "ok"

    monkeypatch.setattr(PermissionService, "user_has_permission", _has_permission)
    monkeypatch.setattr(EntitlementService, "check_permission_entitlement", _entitled)


def _case(case_id: str, org: str, project: str) -> dict[str, Any]:
    return {
        "_id": case_id,
        "organization_id": org,
        "project_id": project,
        "contract_id": f"contract-{project}",
        "title": case_id,
        "status": "readiness_approved",
        "readiness_approved_by": "u-earlier-approver",
    }


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> _Database:
    database = _WorkspaceDatabase()
    database.projects.docs.extend(
        [
            {"_id": A1, "organization_id": ORG_A, "name": "A1", "is_active": True},
            {"_id": A2, "organization_id": ORG_A, "name": "A2", "is_active": True},
            {"_id": B1, "organization_id": ORG_B, "name": "B1", "is_active": True},
        ]
    )
    database.arbitration_cases.docs.extend(
        [_case(CASE_A1, ORG_A, A1), _case(CASE_A2, ORG_A, A2), _case(CASE_B1, ORG_B, B1)]
    )

    async def _get_database() -> _Database:
        return database

    for name, module in list(sys.modules.items()):
        if "rbac_backend" in name and getattr(module, "get_database", None) is _original_get_database:
            monkeypatch.setattr(module, "get_database", _get_database)
    return database


def _app(db: _Database, user: Any) -> FastAPI:
    app = FastAPI()
    register_domain_error_handler(app)
    app.include_router(arbitration_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[_original_get_database] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    return app


@asynccontextmanager
async def _as(db: _Database, user: Any):
    transport = httpx.ASGITransport(app=_app(db, user))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _rows(db: _Database) -> list[dict[str, Any]]:
    return db.arbitration_document_index.docs


def _snapshot(db: _Database) -> list[dict[str, Any]]:
    return [dict(row) for row in _rows(db)]


async def _approved_row(db: _Database) -> dict[str, Any]:
    async with _as(db, EDITOR_A1) as client:
        created = await client.post(f"/api/arbitration/cases/{CASE_A1}/{INDEX}", json={"title": "Exhibit", "source_id": "doc-a1"})
    assert created.status_code == 201, created.text
    row_id = created.json()["_id"]
    async with _as(db, REVIEWER_A1) as client:
        approved = await client.post(
            f"/api/arbitration/cases/{CASE_A1}/{INDEX}/{row_id}/review",
            json={"action": "approve", "reviewer_role": "contracts"},
        )
    assert approved.status_code == 200, approved.text
    assert approved.json()["approval_status"] == "approved"
    return approved.json()


# ---------------------------------------------------------------------------
# An update cannot move a row
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("move", OWNERSHIP_MOVES)
async def test_an_update_cannot_change_a_rows_ownership(db: _Database, move: dict[str, Any]) -> None:
    row = await _approved_row(db)
    before = _snapshot(db)
    async with _as(db, EDITOR_A1) as client:
        response = await client.patch(f"/api/arbitration/cases/{CASE_A1}/{INDEX}/{row['_id']}", json={**move, "title": "moved"})
    assert response.status_code == 400, response.text
    assert _snapshot(db) == before


@pytest.mark.parametrize("move", OWNERSHIP_MOVES)
async def test_a_body_addressed_update_cannot_change_a_rows_ownership(db: _Database, move: dict[str, Any]) -> None:
    row = await _approved_row(db)
    before = _snapshot(db)
    async with _as(db, EDITOR_A1) as client:
        response = await client.patch(f"/api/arbitration/cases/{CASE_A1}/{INDEX}", json={"_id": row["_id"], **move})
    assert response.status_code == 400, response.text
    assert _snapshot(db) == before


async def test_a_moved_row_never_reaches_the_destination_case(db: _Database) -> None:
    # The full sequence: approve in A1, move into B1 pointing at a B1 document,
    # then read B1 as its own tenant. Nothing may arrive.
    row = await _approved_row(db)
    async with _as(db, EDITOR_A1) as client:
        await client.patch(
            f"/api/arbitration/cases/{CASE_A1}/{INDEX}/{row['_id']}",
            json={"case_id": CASE_B1, "organization_id": ORG_B, "project_id": B1, "source_id": "doc-b1"},
        )
    async with _as(db, ADMIN_B) as client:
        listing = await client.get(f"/api/arbitration/cases/{CASE_B1}/{INDEX}")
    assert listing.status_code == 200, listing.text
    assert listing.json() == []
    destination = next(case for case in db.arbitration_cases.docs if case["_id"] == CASE_B1)
    assert destination["readiness_approved_by"] == "u-earlier-approver"
    assert all(stored["case_id"] == CASE_A1 for stored in _rows(db))


async def test_a_create_cannot_name_another_case(db: _Database) -> None:
    async with _as(db, EDITOR_A1) as client:
        response = await client.post(
            f"/api/arbitration/cases/{CASE_A1}/{INDEX}",
            json={"title": "Exhibit", "case_id": CASE_B1, "organization_id": ORG_B, "project_id": B1},
        )
    assert response.status_code == 400, response.text
    assert _rows(db) == []


# ---------------------------------------------------------------------------
# Clients that echo the row back still work
# ---------------------------------------------------------------------------


async def test_an_update_that_echoes_the_unchanged_ownership_still_applies(db: _Database) -> None:
    row = await _approved_row(db)
    echo = {key: row[key] for key in ("case_id", "organization_id", "project_id", "contract_id")}
    async with _as(db, EDITOR_A1) as client:
        response = await client.patch(f"/api/arbitration/cases/{CASE_A1}/{INDEX}/{row['_id']}", json={**echo, "title": "renamed"})
    assert response.status_code == 200, response.text
    stored = _rows(db)[0]
    assert stored["title"] == "renamed"
    assert (stored["case_id"], stored["organization_id"], stored["project_id"]) == (CASE_A1, ORG_A, A1)


async def test_a_create_that_echoes_its_own_case_still_applies(db: _Database) -> None:
    async with _as(db, EDITOR_A1) as client:
        response = await client.post(
            f"/api/arbitration/cases/{CASE_A1}/{INDEX}",
            json={"title": "Exhibit", "case_id": CASE_A1, "organization_id": ORG_A, "project_id": A1},
        )
    assert response.status_code == 201, response.text
    assert [stored["case_id"] for stored in _rows(db)] == [CASE_A1]


async def test_a_foreign_tenant_cannot_update_the_row_at_all(db: _Database) -> None:
    row = await _approved_row(db)
    before = _snapshot(db)
    async with _as(db, ADMIN_B) as client:
        response = await client.patch(f"/api/arbitration/cases/{CASE_A1}/{INDEX}/{row['_id']}", json={"title": "x"})
    assert response.status_code == 404, response.text
    assert _snapshot(db) == before
