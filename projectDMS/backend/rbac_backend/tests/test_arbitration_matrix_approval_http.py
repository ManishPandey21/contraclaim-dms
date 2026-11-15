"""Unauthorised matrix-row edits and reviews leave content and approval unchanged.

Drives the real arbitration router, `PolicyService` and `ScopeService` over an
in-memory Mongo boundary; only the permission-grant and subscription-entitlement
seams are patched (as in `test_cl4a_documents_contracts_scope.py`).
"""

from __future__ import annotations

import copy
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
A1, B1 = "proj-A1", "proj-B1"
CASE_A1, INDEX, ROW = "case-a1", "document-index", "row-a1"
VIEW, EDIT, APPROVE = "dms.arbitration.view", "dms.arbitration.edit", "dms.arbitration.approve"

EDITOR_A1 = _principal("u-editor-a1", ["projectuser"], ORG_A, [A1])
READER_A1 = _principal("u-reader-a1", ["projectuser", "contract_reviewer"], ORG_A, [A1])
ADMIN_B = _principal("u-orgadmin-b", ["orgadmin", "contract_reviewer"], ORG_B)

GRANTS: dict[str, set[str]] = {
    EDITOR_A1.id: {VIEW, EDIT},
    READER_A1.id: {VIEW},
    ADMIN_B.id: {VIEW, EDIT, APPROVE},
}


@pytest.fixture(autouse=True)
def _authorization_seams(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _has_permission(self, user_id: str, permission_name: str, **_kwargs: Any) -> bool:
        return permission_name in GRANTS.get(str(user_id), set())

    async def _entitled(self, **_kwargs: Any):
        return True, "ok"

    monkeypatch.setattr(PermissionService, "user_has_permission", _has_permission)
    monkeypatch.setattr(EntitlementService, "check_permission_entitlement", _entitled)


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> _Database:
    database = _WorkspaceDatabase()
    database.projects.docs.extend(
        [
            {"_id": A1, "organization_id": ORG_A, "name": "A1", "is_active": True},
            {"_id": B1, "organization_id": ORG_B, "name": "B1", "is_active": True},
        ]
    )
    database.arbitration_cases.docs.append(
        {"_id": CASE_A1, "organization_id": ORG_A, "project_id": A1, "contract_id": "contract-a1", "title": "Case A1"}
    )
    database.arbitration_document_index.docs.append(
        {
            "_id": ROW,
            "case_id": CASE_A1,
            "organization_id": ORG_A,
            "project_id": A1,
            "title": "Delay notice",
            "source_id": "doc-a",
            "exhibit_id": "C-1",
            "approval_status": "approved",
            "human_approval_status": "approved",
            "verification_status": "verified",
            "readiness_status": "ready",
            "review_status": "approved",
            "review_required_roles": ["contracts"],
            "review_completed_roles": ["contracts"],
            "created_by": "u-author",
        }
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


def _snapshot(db: _Database) -> list[dict[str, Any]]:
    return copy.deepcopy(db.arbitration_document_index.docs)


@pytest.mark.parametrize(
    "user,expected",
    [pytest.param(READER_A1, 403, id="same-tenant-reader"), pytest.param(ADMIN_B, 404, id="foreign-tenant")],
)
async def test_an_unauthorised_edit_changes_neither_content_nor_approval(db: _Database, user: Any, expected: int) -> None:
    before = _snapshot(db)
    async with _as(db, user) as client:
        response = await client.patch(f"/api/arbitration/cases/{CASE_A1}/{INDEX}/{ROW}", json={"source_id": "doc-b"})
    assert response.status_code == expected, response.text
    assert _snapshot(db) == before


@pytest.mark.parametrize(
    "user,expected",
    [pytest.param(READER_A1, 403, id="same-tenant-reader"), pytest.param(ADMIN_B, 404, id="foreign-tenant")],
)
async def test_an_unauthorised_review_changes_nothing(db: _Database, user: Any, expected: int) -> None:
    before = _snapshot(db)
    async with _as(db, user) as client:
        response = await client.post(
            f"/api/arbitration/cases/{CASE_A1}/{INDEX}/{ROW}/review", json={"action": "reject", "reviewer_role": "contracts", "comment": "x"}
        )
    assert response.status_code == expected, response.text
    assert _snapshot(db) == before


async def test_an_authorised_edit_through_the_api_returns_the_row_to_review(db: _Database) -> None:
    async with _as(db, EDITOR_A1) as client:
        response = await client.patch(f"/api/arbitration/cases/{CASE_A1}/{INDEX}/{ROW}", json={"source_id": "doc-b"})
    assert response.status_code == 200, response.text
    assert response.json()["approval_status"] == "needs_review"
    assert response.json()["review_completed_roles"] == []


async def test_an_edit_that_tries_to_keep_the_approval_is_rejected_whole(db: _Database) -> None:
    before = _snapshot(db)
    async with _as(db, EDITOR_A1) as client:
        response = await client.patch(
            f"/api/arbitration/cases/{CASE_A1}/{INDEX}/{ROW}", json={"source_id": "doc-b", "approval_status": "approved"}
        )
    assert response.status_code == 422, response.text
    assert _snapshot(db) == before
