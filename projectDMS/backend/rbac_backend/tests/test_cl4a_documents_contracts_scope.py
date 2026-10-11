"""CL-4A: the selected navbar project bounds Documents, Contract Documents /
Contract Master, document search and the dashboard totals.

``EffectiveScope = membership/role scope ∩ explicit selected project``, against the
REAL ``resolve_active_scope`` (``active_scope`` is not overridden: the selection is
sent as ``X-Proj-Id`` / ``X-Org-Id`` exactly like the browser) and the real
``PolicyService`` / ``ScopeService`` over an in-memory Mongo boundary; only the
permission and entitlement seams are monkeypatched.

* member of A1 + A2, selected A1, record A1       -> 200
* member of A1 + A2, selected A2, record A1       -> 403 ``context_forbidden``
* no project selected, record-level               -> 400 ``selection_required``
* non-member                                      -> refused (403)
* list: selected A1 / A2 -> that project only; nothing selected -> bounded
* superadmin: a selection still constrains; record-level needs one; no selection lists broadly
* create with a body project other than the selection -> 403; a list filter naming
  another project or organisation -> 403.
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import httpx
import pytest
from bson import ObjectId
from fastapi import FastAPI

from rbac_backend.core.database import get_database as _original_get_database
from rbac_backend.core.database import get_db
from rbac_backend.core.security import get_current_user
from rbac_backend.routers.contract_clauses import router as clauses_router
from rbac_backend.routers.contract_master import router as contract_master_router
from rbac_backend.routers.contracts import router as contracts_router
from rbac_backend.routers.dashboard import router as dashboard_router
from rbac_backend.routers.documents import (
    DocumentController,
    download_all_project_documents,
    get_document_controller,
)
from rbac_backend.routers.documents import router as documents_router
from rbac_backend.routers.search import router as search_router
from rbac_backend.services.authorization_service import AuthorizationService
from rbac_backend.services.document_service import DocumentService
from rbac_backend.services.entitlement_service import EntitlementService
from rbac_backend.services.permission_service import PermissionService
from rbac_backend.services.policy_service import PolicyService
from rbac_backend.tests.selection_fixtures import selection as pinned
from rbac_backend.tests.test_hindrance_register_http import (
    _Collection,
    _Database,
    _matches,
    _principal,
)

ORG_A, ORG_B = "org-A", "org-B"
A1, A2, B1 = "proj-A1", "proj-A2", "proj-B1"

DOC_A1 = ObjectId()
DOC_A2 = ObjectId()
DOC_B1 = ObjectId()
DOC_ORG_A = ObjectId()  # organisation-level Document (project_id "")
CON_A1 = ObjectId()  # contract Document in A1
CON_ORG_A = ObjectId()  # organisation-level contract Document

ALL = {
    "dms.document.view",
    "dms.document.upload",
    "dms.document.download",
    "dms.document.edit_metadata",
    "dms.document.link_reference",
    "dms.comment.add",
    "drafting.request.create",
    "dms.dashboard.view",
    "dms.contract.master.view",
    "dms.contract.master.manage",
    "dms.contract.clause.read",
}

MEMBER_AB = _principal("u-member-ab", ["projectadmin"], ORG_A, [A1, A2])
MEMBER_A2 = _principal("u-member-a2", ["projectadmin"], ORG_A, [A2])
ORG_ADMIN_A = _principal("u-orgadmin-a", ["orgadmin"], ORG_A)
SUPERADMIN = _principal("u-root", ["superadmin"], None)

GRANTS: dict[str, set[str]] = {MEMBER_AB.id: ALL, MEMBER_A2.id: ALL, ORG_ADMIN_A.id: ALL}


@pytest.fixture(autouse=True)
def _authorization_seams(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _has_permission(self, user_id: str, permission_name: str, **_kwargs: Any) -> bool:
        return permission_name in GRANTS.get(str(user_id), set())

    async def _entitled(self, **_kwargs: Any):
        return True, "ok"

    monkeypatch.setattr(PermissionService, "user_has_permission", _has_permission)
    monkeypatch.setattr(EntitlementService, "check_permission_entitlement", _entitled)


class _AggregatingCollection(_Collection):
    """Enough of ``aggregate`` for ``/search/*``: $match, $skip, $limit, $count."""

    def aggregate(self, pipeline: list[dict[str, Any]]):
        rows = [dict(row) for row in self.docs]
        for stage in pipeline:
            if "$match" in stage:
                match = {k: v for k, v in stage["$match"].items() if k != "$text"}
                rows = [row for row in rows if _matches(row, match)]
            elif "$skip" in stage:
                rows = rows[stage["$skip"]:]
            elif "$limit" in stage:
                rows = rows[: stage["$limit"]]
            elif "$count" in stage:
                rows = [{stage["$count"]: len(rows)}] if rows else []

        class _Result:
            async def to_list(self, _length: Any = None):
                return rows

        return _Result()


def _document(document_id: ObjectId, org: str, project: str, **overrides: Any) -> dict[str, Any]:
    row = {
        "_id": document_id,
        "organization_id": org,
        "project_id": project,
        "name": f"letter {project or 'org'}",
        "filename": f"{document_id}.pdf",
        "filetype": "application/pdf",
        "filesize": 10,
        "uploadType": "incoming",
        "letterNo": f"LTR-{project or 'ORG'}",
        "date": datetime(2026, 9, 1),
        "subject": f"Subject {project or 'org'}",
        "tags": [],
        "subTags": [],
        "status": "Received",
        "createdAt": datetime(2026, 9, 1),
        "updatedAt": datetime(2026, 9, 1),
        "createdBy": "tester",
        "processing_status": "completed",
        "duplicate_status": "unique",
        "lifecycle_state": "active",
        "enclosures": [],
        "references": [],
        "referencedBy": [],
    }
    row.update(overrides)
    return row


def _seeded() -> _Database:
    db = _Database()
    db._collections["documents"] = _AggregatingCollection("documents")
    db.projects.docs.extend(
        [
            {"_id": A1, "organization_id": ORG_A, "name": "A1", "is_active": True},
            {"_id": A2, "organization_id": ORG_A, "name": "A2", "is_active": True},
            {"_id": B1, "organization_id": ORG_B, "name": "B1", "is_active": True},
        ]
    )
    db.documents.docs.extend(
        [
            _document(DOC_A1, ORG_A, A1),
            _document(DOC_A2, ORG_A, A2),
            _document(DOC_B1, ORG_B, B1),
            _document(DOC_ORG_A, ORG_A, ""),
            _document(CON_A1, ORG_A, A1, uploadType="contract", status="completed"),
            _document(CON_ORG_A, ORG_A, "", uploadType="contract", status="completed"),
        ]
    )
    db.contract_master.docs.extend(
        [
            {"_id": "cm-A1", "organization_id": ORG_A, "project_id": A1, "contract_id": "primary"},
            {"_id": "cm-A2", "organization_id": ORG_A, "project_id": A2, "contract_id": "primary"},
        ]
    )
    db.contract_ingest_jobs.docs.append(
        {"_id": "job-org", "upload_id": "up-org", "status": "queued", "organization_id": ORG_A, "project_id": ""}
    )
    return db


@pytest.fixture
def db(monkeypatch: pytest.MonkeyPatch) -> _Database:
    """Route every module's ``get_database`` to the in-memory database."""
    database = _seeded()

    async def _get_database() -> _Database:
        return database

    for name, module in list(sys.modules.items()):
        if "rbac_backend" in name and getattr(module, "get_database", None) is _original_get_database:
            monkeypatch.setattr(module, "get_database", _get_database)
    return database


def _app(db: _Database, user: Any) -> FastAPI:
    app = FastAPI()
    for router in (
        documents_router,
        search_router,
        dashboard_router,
        contracts_router,
        contract_master_router,
        clauses_router,
    ):
        app.include_router(router, prefix="/api")

    def _controller() -> DocumentController:
        controller = DocumentController(
            document_service=DocumentService(db),  # type: ignore[arg-type]
            file_service=None,  # type: ignore[arg-type]
            export_service=None,  # type: ignore[arg-type]
            auth_service=AuthorizationService(),
            bulk_upload_service=None,  # type: ignore[arg-type]
        )
        controller.policy_service = PolicyService(db)
        return controller

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[_original_get_database] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_document_controller] = _controller
    return app


@asynccontextmanager
async def _as(db: _Database, user: Any, project: str | None, org: str | None = None):
    """A client whose requests carry the navbar selection (None = nothing selected)."""
    headers = {}
    if project:
        headers["X-Proj-Id"] = project
    if org:
        headers["X-Org-Id"] = org
    transport = httpx.ASGITransport(app=_app(db, user))
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
        yield client


def _code(response: httpx.Response) -> str | None:
    detail = response.json().get("detail")
    return detail.get("code") if isinstance(detail, dict) else None


def _ids(response: httpx.Response) -> set[str]:
    return {str(row.get("_id") or row.get("id")) for row in response.json()["documents"]}


# ---------------------------------------------------------------------------
# Documents: record-level routes
# ---------------------------------------------------------------------------


async def test_document_record_is_held_to_the_selected_project(db: _Database) -> None:
    async with _as(db, MEMBER_AB, A1) as client:
        response = await client.get(f"/api/documents/{DOC_A1}")
        assert response.status_code == 200, response.text
        assert response.json()["project_id"] == A1

    async with _as(db, MEMBER_AB, A2) as client:
        for path in (
            f"/api/documents/{DOC_A1}",
            f"/api/documents/{DOC_A1}/references",
            f"/api/documents/{DOC_A1}/enclosures",
            f"/api/documents/{DOC_A1}/comments",
            f"/api/documents/{DOC_A1}/linked",
            f"/api/documents/{DOC_A1}/audit-events",
        ):
            response = await client.get(path)
            assert response.status_code == 403, (path, response.text)
            assert _code(response) == "context_forbidden", path
            assert A1 not in response.text

        response = await client.put(f"/api/documents/{DOC_A1}", json={"subject": "moved"})
        assert response.status_code == 403 and _code(response) == "context_forbidden", response.text
        assert (await db.documents.find_one({"_id": DOC_A1}))["subject"] == f"Subject {A1}"


async def test_document_record_without_a_selection_is_400_before_the_load(db: _Database) -> None:
    async with _as(db, MEMBER_AB, None) as client:
        for method, path in (
            ("GET", f"/api/documents/{DOC_A1}"),
            ("GET", f"/api/documents/{ObjectId()}"),  # same answer whether or not the id exists
            ("GET", f"/api/documents/{DOC_A1}/download"),
            ("GET", f"/api/documents/{DOC_A1}/comments"),
            ("POST", f"/api/documents/{DOC_A1}/sync-references"),
            ("POST", f"/api/documents/{DOC_A1}/process"),
        ):
            response = await client.request(method, path)
            assert response.status_code == 400, (path, response.text)
            assert _code(response) == "selection_required", path


async def test_tenant_context_errors_surface_through_try_blocks_not_as_500(db: _Database) -> None:
    """``request-draft`` runs inside ``try/except Exception`` and ``@handle_exceptions``."""
    async with _as(db, MEMBER_AB, None) as client:
        response = await client.post(f"/api/documents/{DOC_A1}/request-draft")
        assert response.status_code == 400 and _code(response) == "selection_required", response.text
    async with _as(db, MEMBER_AB, A2) as client:
        response = await client.post(f"/api/documents/{DOC_A1}/request-draft")
        assert response.status_code == 403 and _code(response) == "context_forbidden", response.text
    assert (await db.documents.find_one({"_id": DOC_A1}))["status"] == "Received"


async def test_a_non_member_is_refused(db: _Database) -> None:
    # Declaring a project outside the principal's scope is refused by the resolver ...
    async with _as(db, MEMBER_A2, A1) as client:
        response = await client.get(f"/api/documents/{DOC_A1}")
        assert response.status_code == 403 and _code(response) == "context_forbidden", response.text
    # ... and its own project cannot reach a record in another one.
    async with _as(db, MEMBER_A2, A2) as client:
        response = await client.get(f"/api/documents/{DOC_A1}")
        assert response.status_code == 403, response.text


async def test_superadmin_is_constrained_by_an_explicit_selection(db: _Database) -> None:
    async with _as(db, SUPERADMIN, A1) as client:
        assert (await client.get(f"/api/documents/{DOC_A1}")).status_code == 200
        # An organisation-level Document is held to the selected organisation only.
        assert (await client.get(f"/api/documents/{DOC_ORG_A}")).status_code == 200
    async with _as(db, SUPERADMIN, A2) as client:
        response = await client.get(f"/api/documents/{DOC_A1}")
        assert response.status_code == 403 and _code(response) == "context_forbidden", response.text
    async with _as(db, SUPERADMIN, B1) as client:
        response = await client.get(f"/api/documents/{DOC_ORG_A}")
        assert response.status_code == 403 and _code(response) == "context_forbidden", response.text
    async with _as(db, SUPERADMIN, None) as client:
        response = await client.get(f"/api/documents/{DOC_A1}")
        assert response.status_code == 400 and _code(response) == "selection_required", response.text


async def test_document_link_holds_both_source_and_target(db: _Database) -> None:
    body = {"source_document_id": str(DOC_A1), "target_document_id": str(DOC_A2), "link_type": "direct"}
    async with _as(db, MEMBER_AB, A1) as client:
        response = await client.post("/api/documents/link", json=body)
        assert response.status_code == 403 and _code(response) == "context_forbidden", response.text
    assert (await db.documents.find_one({"_id": DOC_A1}))["references"] == []


# ---------------------------------------------------------------------------
# Documents: create / bulk upload / download-all
# ---------------------------------------------------------------------------


def _upload_form(project: str, org: str = ORG_A) -> dict[str, Any]:
    return {
        "data": {
            "organization_id": org,
            "project_id": project,
            "uploadType": "incoming",
            "letterNo": "LTR-NEW",
            "date": "2026-09-01",
        },
        "files": {"file": ("new.pdf", b"%PDF-1.4\n%%EOF\n", "application/pdf")},
    }


async def test_document_create_requires_the_form_project_to_be_the_selection(db: _Database) -> None:
    async with _as(db, MEMBER_AB, A1) as client:
        response = await client.post("/api/documents", **_upload_form(A2))
        assert response.status_code == 403 and _code(response) == "context_forbidden", response.text
        response = await client.post(
            "/api/documents/bulk-upload",
            data={"organization_id": ORG_A, "project_id": A2},
            files=[
                ("csv_file", ("meta.csv", b"filename\n", "text/csv")),
                ("files", ("a.pdf", b"%PDF-1.4\n", "application/pdf")),
            ],
        )
        assert response.status_code == 403 and _code(response) == "context_forbidden", response.text
    async with _as(db, MEMBER_AB, None) as client:
        response = await client.post("/api/documents", **_upload_form(A1))
        assert response.status_code == 400 and _code(response) == "selection_required", response.text
    assert await db.documents.count_documents({"letterNo": "LTR-NEW"}) == 0


async def test_download_all_requires_the_selected_project_and_is_shadowed(db: _Database) -> None:
    class _NeverArchive:
        async def create_project_archive(self, **_kwargs: Any):
            raise AssertionError("the archive was built for a project outside the selection")

    with pytest.raises(Exception) as refused:
        await download_all_project_documents(
            project_id=A2,
            type="complete",
            upload_type=None,
            year=None,
            month=None,
            document_id=None,
            service=_NeverArchive(),  # type: ignore[arg-type]
            current_user=MEMBER_AB,
            selection=pinned(db, ORG_A, A1),
        )
    assert getattr(refused.value, "status_code", None) == 403

    # Recorded debt: GET /documents/{id} is registered first and answers this path.
    async with _as(db, MEMBER_AB, A1) as client:
        response = await client.get("/api/documents/download-all", params={"project_id": A1})
        assert response.status_code == 404, response.text


# ---------------------------------------------------------------------------
# Documents: lists
# ---------------------------------------------------------------------------


async def test_document_list_is_pinned_to_the_selected_project(db: _Database) -> None:
    async with _as(db, MEMBER_AB, A1) as client:
        response = await client.get("/api/documents")
        assert response.status_code == 200, response.text
        assert _ids(response) == {str(DOC_A1), str(CON_A1)}  # org-level Documents not listed
        linkable = await client.get("/api/document-search")
        assert linkable.status_code == 200, linkable.text
        assert str(DOC_A2) not in _ids(linkable) and str(DOC_A1) in _ids(linkable)
    async with _as(db, MEMBER_AB, A2) as client:
        assert _ids(await client.get("/api/documents")) == {str(DOC_A2)}
    async with _as(db, MEMBER_AB, None) as client:
        ids = _ids(await client.get("/api/documents"))
        assert {str(DOC_A1), str(DOC_A2)} <= ids and str(DOC_B1) not in ids
    async with _as(db, SUPERADMIN, None) as client:
        assert str(DOC_B1) in _ids(await client.get("/api/documents"))
    async with _as(db, SUPERADMIN, A2) as client:
        # Organisation roles and superadmin also see the selected organisation's
        # organisation-level Documents - never another project's.
        assert _ids(await client.get("/api/documents")) == {str(DOC_A2), str(DOC_ORG_A), str(CON_ORG_A)}


async def test_the_register_lists_organisation_level_documents_to_organisation_roles_only(db: _Database) -> None:
    """The navbar always selects a project when the organisation has one; an
    organisation-level Document must not vanish from the register for the roles
    that listed it before, and must not start appearing for a project-tier user."""
    async with _as(db, ORG_ADMIN_A, A1) as client:
        response = await client.get("/api/documents")
        assert response.status_code == 200, response.text
        assert _ids(response) == {str(DOC_A1), str(CON_A1), str(DOC_ORG_A), str(CON_ORG_A)}
        # The link picker still offers project Documents only.
        assert str(DOC_ORG_A) not in _ids(await client.get("/api/document-search"))
    async with _as(db, MEMBER_AB, A1) as client:
        assert str(DOC_ORG_A) not in _ids(await client.get("/api/documents"))


async def test_references_never_cross_the_selection(db: _Database) -> None:
    """Adding or removing a reference rewrites the referenced Document's backlink,
    and a reference listing shows the referenced Document's number, subject and date."""
    body = {"referenced_document_id": str(DOC_A2), "link_type": "direct"}
    async with _as(db, ORG_ADMIN_A, A1) as client:
        refused = await client.post(f"/api/documents/{DOC_ORG_A}/references", json=body)
        assert refused.status_code == 403 and _code(refused) == "context_forbidden", refused.text
        removed = await client.delete(f"/api/documents/{DOC_ORG_A}/references/{DOC_A2}")
        assert removed.status_code == 403 and _code(removed) == "context_forbidden", removed.text
    assert (await db.documents.find_one({"_id": DOC_A2}))["referencedBy"] == []
    assert (await db.documents.find_one({"_id": DOC_ORG_A}))["references"] == []

    # A reference recorded before CL-4A (org-level Document -> A1 and A2 letters).
    await db.documents.update_one(
        {"_id": DOC_ORG_A},
        {"$set": {"references": [
            {"documentId": str(DOC_A1), "linkType": "direct", "linkedAt": datetime(2026, 9, 2)},
            {"documentId": str(DOC_A2), "linkType": "direct", "linkedAt": datetime(2026, 9, 2)},
        ]}},
    )
    async with _as(db, ORG_ADMIN_A, A1) as client:
        listed = await client.get(f"/api/documents/{DOC_ORG_A}/references")
        assert listed.status_code == 200, listed.text
        assert [row["documentId"] for row in listed.json()["linked"]] == [str(DOC_A1)]
        linked = await client.get(f"/api/documents/{DOC_ORG_A}/linked")
        assert linked.status_code == 200, linked.text
        assert [row["documentId"] for row in linked.json()] == [str(DOC_A1)]


async def test_a_document_list_filter_may_narrow_the_selection_never_leave_it(db: _Database) -> None:
    async with _as(db, MEMBER_AB, A1) as client:
        assert _ids(await client.get("/api/documents", params={"project_id": A1})) == {str(DOC_A1), str(CON_A1)}
        for params in ({"project_id": A2}, {"organization_id": ORG_B}):
            for path in ("/api/documents", "/api/document-search", "/api/documents/export"):
                response = await client.get(path, params=params)
                assert response.status_code == 403, (path, params, response.text)
                assert _code(response) == "context_forbidden"


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def _result_ids(response: httpx.Response) -> set[str]:
    return {str(row["_id"]) for row in response.json()["results"]}


async def test_search_is_pinned_to_the_selected_project(db: _Database) -> None:
    async with _as(db, MEMBER_AB, A1) as client:
        response = await client.get("/api/search/documents")
        assert response.status_code == 200, response.text
        assert _result_ids(response) == {str(DOC_A1), str(CON_A1)}
        for params in ({"projects": A2}, {"organizations": ORG_B}):
            refused = await client.get("/api/search/documents", params=params)
            assert refused.status_code == 403 and _code(refused) == "context_forbidden", refused.text
        suggestions = await client.get("/api/search/suggestions", params={"q": "letter"})
        assert suggestions.status_code == 200
        assert suggestions.json()["suggestions"] == [f"letter {A1}"]
    async with _as(db, MEMBER_AB, None) as client:
        ids = _result_ids(await client.get("/api/search/documents"))
        assert {str(DOC_A1), str(DOC_A2)} <= ids and str(DOC_B1) not in ids


async def test_semantic_search_passes_the_selection_through_and_no_longer_500s(db: _Database) -> None:
    """S6 called S1 with only four kwargs, so ``Query(...)`` defaults leaked in (500)."""
    async with _as(db, MEMBER_AB, A2) as client:
        response = await client.post("/api/search/semantic", json={"query": "letter", "limit": 10})
        assert response.status_code == 200, response.text
        assert _result_ids(response) == {str(DOC_A2)}


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


async def test_dashboard_totals_are_computed_within_the_selected_project(db: _Database) -> None:
    async with _as(db, MEMBER_AB, A1) as client:
        stats = (await client.get("/api/dashboard/stats")).json()
        assert stats["totalDocuments"] == 2 and stats["totalLetters"] == 1
        assert [row["id"] for row in stats["projects"]] == [A1]
        assert [row["id"] for row in stats["organizations"]] == [ORG_A]
    async with _as(db, MEMBER_AB, None) as client:
        stats = (await client.get("/api/dashboard/stats")).json()
        assert {row["id"] for row in stats["projects"]} == {A1, A2}
    async with _as(db, SUPERADMIN, None, org=ORG_B) as client:
        stats = (await client.get("/api/dashboard/stats")).json()
        assert stats["totalDocuments"] == 1 and [row["id"] for row in stats["projects"]] == [B1]


# ---------------------------------------------------------------------------
# Contract Master
# ---------------------------------------------------------------------------


async def test_contract_master_record_list_and_create(db: _Database) -> None:
    async with _as(db, MEMBER_AB, A1) as client:
        assert (await client.get("/api/contracts/master/cm-A1")).status_code == 200
        assert [row["_id"] for row in (await client.get("/api/contracts/master")).json()] == ["cm-A1"]
        refused = await client.get("/api/contracts/master", params={"project_id": A2})
        assert refused.status_code == 403 and _code(refused) == "context_forbidden"
        created = await client.post("/api/contracts/master", json={"organization_id": ORG_A, "project_id": A2})
        assert created.status_code == 403 and _code(created) == "context_forbidden", created.text
    async with _as(db, MEMBER_AB, A2) as client:
        response = await client.get("/api/contracts/master/cm-A1")
        assert response.status_code == 403 and _code(response) == "context_forbidden"
        assert [row["_id"] for row in (await client.get("/api/contracts/master")).json()] == ["cm-A2"]
    async with _as(db, MEMBER_AB, None) as client:
        response = await client.get("/api/contracts/master/cm-A1")
        assert response.status_code == 400 and _code(response) == "selection_required"
        response = await client.get("/api/contracts/master/does-not-exist")
        assert response.status_code == 400 and _code(response) == "selection_required"
        assert {row["_id"] for row in (await client.get("/api/contracts/master")).json()} == {"cm-A1", "cm-A2"}
    assert await db.contract_master.count_documents({}) == 2


# ---------------------------------------------------------------------------
# Contract Documents
# ---------------------------------------------------------------------------


async def test_contract_document_routes_hold_the_contract(db: _Database) -> None:
    async with _as(db, MEMBER_AB, A2) as client:
        for method, path in (
            ("GET", f"/api/contracts/{CON_A1}/download"),
            ("POST", f"/api/contracts/{CON_A1}/reindex"),
            ("GET", f"/api/contracts/{CON_A1}/clauses"),
        ):
            response = await client.request(method, path)
            assert response.status_code == 403, (path, response.text)
            assert _code(response) == "context_forbidden", path
    async with _as(db, MEMBER_AB, None) as client:
        for method, path in (
            ("GET", f"/api/contracts/{CON_A1}/download"),
            ("POST", f"/api/contracts/{CON_A1}/clauses/index"),
        ):
            response = await client.request(method, path)
            assert response.status_code == 400 and _code(response) == "selection_required", (path, response.text)


async def test_contract_list_is_pinned_and_filters_cannot_leave_the_selection(db: _Database) -> None:
    async with _as(db, MEMBER_AB, A1) as client:
        response = await client.get("/api/contracts/list")
        assert response.status_code == 200, response.text
        assert {row["document_id"] for row in response.json()["uploads"]} == {str(CON_A1)}
        refused = await client.get("/api/contracts/list", params={"project_id": A2})
        assert refused.status_code == 403 and _code(refused) == "context_forbidden"
        refused = await client.post("/api/contracts/search", json={"query": "delay", "project_id": A2})
        assert refused.status_code == 403 and _code(refused) == "context_forbidden"


async def test_contract_upload_session_scope_against_the_selection(db: _Database) -> None:
    session = {"filename": "contract.pdf", "organization_id": ORG_A}
    async with _as(db, MEMBER_AB, A1) as client:
        response = await client.post("/api/contracts/upload-session", json={**session, "project_id": A2})
        assert response.status_code == 403 and _code(response) == "context_forbidden", response.text
        # With a project selected an organisation-level upload is outside it.
        response = await client.post("/api/contracts/upload-session", json=session)
        assert response.status_code == 403 and _code(response) == "context_forbidden", response.text
        response = await client.post("/api/contracts/upload-session", json={**session, "project_id": A1})
        assert response.status_code == 200, response.text
    async with _as(db, MEMBER_AB, None) as client:
        response = await client.post("/api/contracts/upload-session", json={**session, "project_id": A1})
        assert response.status_code == 400 and _code(response) == "selection_required", response.text
    async with _as(db, SUPERADMIN, None, org=ORG_A) as client:
        # Nothing selected: an organisation-level upload keeps working in the selected organisation ...
        response = await client.post("/api/contracts/upload-session", json=session)
        assert response.status_code == 200, response.text
        # ... and so does polling an organisation-level job.
        status = await client.get("/api/contracts/status", params={"upload_id": "up-org"})
        assert status.status_code == 200, status.text
        refused = await client.post(
            "/api/contracts/upload-session", json={**session, "organization_id": ORG_B}
        )
        assert refused.status_code == 403 and _code(refused) == "context_forbidden", refused.text
