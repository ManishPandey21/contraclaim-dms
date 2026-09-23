"""CL-4A: the selected navbar project bounds Claims, IPC / Bills and Insurance.

``EffectiveScope = membership/role scope ∩ explicit selected project``, against the
REAL ``resolve_active_scope`` (``active_scope`` is not overridden) with the real
``PolicyService`` / ``ScopeService``; only the permission and entitlement seams are
monkeypatched. The in-memory Mongo boundary is the Hindrance HTTP suite's.

* member of A1 + A2, selected A1, record A1    -> 200
* member of A1 + A2, selected A2, record A1    -> 403 ``context_forbidden``
* no project selected, record-level           -> 400 ``selection_required``
* non-member                                  -> refused (403)
* lists narrow to the selection; with none they stay bounded by membership
* superadmin: an explicit selection still constrains; record-level needs one
* create: the body's project must BE the selection (refused, never rewritten)
* a list filter naming another project        -> 403 ``context_forbidden``
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from rbac_backend.core.database import get_db
from rbac_backend.core.security import get_current_user
from rbac_backend.routers.claims import router as claims_router
from rbac_backend.routers.insurance import router as insurance_router
from rbac_backend.routers.ipc_bills import router as ipc_router
from rbac_backend.services.entitlement_service import EntitlementService
from rbac_backend.services.permission_service import PermissionService
from rbac_backend.tests.test_hindrance_register_http import _Database, _principal

ALL = {
    "dms.claim.view",
    "dms.claim.create",
    "dms.claim.edit",
    "dms.claim.delete",
    "dms.ipc.view",
    "dms.ipc.create",
    "dms.ipc.edit",
    "dms.ipc.delete",
    "dms.ipc.export",
    "dms.insurance.view",
    "dms.insurance.create",
    "dms.insurance.edit",
    "dms.insurance.delete",
    "dms.insurance.export",
    "dms.document.view",
}

MEMBER_AB = _principal("u-member-ab", ["projectadmin"], "org-A", ["proj-A1", "proj-A2"])
MEMBER_A2 = _principal("u-member-a2", ["projectadmin"], "org-A", ["proj-A2"])
SUPERADMIN = _principal("u-root", ["superadmin"], None)

GRANTS: dict[str, set[str]] = {MEMBER_AB.id: ALL, MEMBER_A2.id: ALL}
#: The scope each entitlement (subscription) check was asked about.
ENTITLEMENT_CALLS: list[dict[str, Any]] = []


@pytest.fixture(autouse=True)
def _authorization_seams(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _has_permission(self, user_id: str, permission_name: str, **_kwargs: Any) -> bool:
        return permission_name in GRANTS.get(str(user_id), set())

    async def _entitled(self, **kwargs: Any):
        ENTITLEMENT_CALLS.append(kwargs)
        return True, "ok"

    ENTITLEMENT_CALLS.clear()

    monkeypatch.setattr(PermissionService, "user_has_permission", _has_permission)
    monkeypatch.setattr(EntitlementService, "check_permission_entitlement", _entitled)


def _claim(claim_id: str, org: str, project: str | None) -> dict[str, Any]:
    return {
        "_id": claim_id,
        "claim_ref": claim_id.upper(),
        "title": f"Claim {claim_id}",
        "type": "eot",
        "status": "draft",
        "organization_id": org,
        "project_id": project,
        "linked_document_ids": [],
        "created_at": datetime(2026, 9, 1),
    }


def _ipc(ipc_id: str, org: str, project: str) -> dict[str, Any]:
    return {
        "_id": ipc_id,
        "ipc_number": ipc_id.upper(),
        "status": "draft",
        "organization_id": org,
        "project_id": project,
        "linked_document_ids": [],
        "created_at": datetime(2026, 9, 1),
    }


def _insurance(insurance_id: str, org: str, project: str) -> dict[str, Any]:
    return {
        "_id": insurance_id,
        "policy_number": insurance_id.upper(),
        "insurance_type": "Contractor's All Risk",
        "organization_id": org,
        "project_id": project,
        "date_of_issue": datetime(2026, 1, 1),
        "date_of_expiry": datetime(2027, 1, 1),
        "linked_document_ids": [],
        "created_at": datetime(2026, 9, 1),
    }


def _seeded() -> _Database:
    db = _Database()
    db.projects.docs.extend(
        [
            {"_id": "proj-A1", "organization_id": "org-A", "is_active": True},
            {"_id": "proj-A2", "organization_id": "org-A", "is_active": True},
            {"_id": "proj-B1", "organization_id": "org-B", "is_active": True},
        ]
    )
    for suffix, org, project in (("a1", "org-A", "proj-A1"), ("a2", "org-A", "proj-A2"), ("b1", "org-B", "proj-B1")):
        db.claims.docs.append(_claim(f"claim-{suffix}", org, project))
        db.ipc_bills.docs.append(_ipc(f"ipc-{suffix}", org, project))
        db.insurance_policies.docs.append(_insurance(f"ins-{suffix}", org, project))
    # A legacy claim with no project: held to the selected organisation only.
    db.claims.docs.append(_claim("claim-legacy-a", "org-A", None))
    return db


def _app(db: _Database, user: Any) -> FastAPI:
    app = FastAPI()
    for router in (claims_router, ipc_router, insurance_router):
        app.include_router(router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    return app


@asynccontextmanager
async def _as(db: _Database, user: Any, project: str | None):
    """A client whose requests carry ``project`` as the navbar selection (None = nothing selected)."""
    headers = {"X-Proj-Id": project} if project else {}
    transport = httpx.ASGITransport(app=_app(db, user))
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
        yield client


def _code(response: httpx.Response) -> str | None:
    try:
        detail = response.json().get("detail")
    except ValueError:
        return None
    return detail.get("code") if isinstance(detail, dict) else None


def _forbidden(response: httpx.Response) -> bool:
    return response.status_code == 403 and _code(response) == "context_forbidden"


def _selection_required(response: httpx.Response) -> bool:
    return response.status_code == 400 and _code(response) == "selection_required"


RECORDS = [
    pytest.param("/api/claims/claim-{}", id="claim"),
    pytest.param("/api/ipc-bills/ipc-{}", id="ipc"),
    pytest.param("/api/insurance/ins-{}", id="insurance"),
]
LISTS = [
    pytest.param("/api/claims", "claim-{}", id="claims"),
    pytest.param("/api/ipc-bills", "ipc-{}", id="ipc"),
    pytest.param("/api/insurance", "ins-{}", id="insurance"),
]
FILTERED_LISTS = [
    "/api/claims",
    "/api/ipc-bills",
    "/api/ipc-bills/summary",
    "/api/ipc-bills/export",
    "/api/insurance",
    "/api/insurance/summary",
    "/api/insurance/alerts",
    "/api/insurance/export",
]


def _ids(response: httpx.Response) -> set[str]:
    return {row["_id"] for row in response.json()}


# ---------------------------------------------------------------------------
# Record-level routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("path", RECORDS)
async def test_record_follows_the_selected_project(path: str) -> None:
    db = _seeded()
    url = path.format("a1")
    async with _as(db, MEMBER_AB, "proj-A1") as client:
        allowed = await client.get(url)
    async with _as(db, MEMBER_AB, "proj-A2") as client:
        mismatched = await client.get(url)
    async with _as(db, MEMBER_AB, None) as client:
        unselected = await client.get(url)
        unselected_missing = await client.get(path.format("missing"))
    assert allowed.status_code == 200, allowed.text
    assert _forbidden(mismatched), mismatched.text
    assert "proj-A1" not in mismatched.text
    assert _selection_required(unselected), unselected.text
    # The same answer whether or not the id exists.
    assert _selection_required(unselected_missing), unselected_missing.text


@pytest.mark.asyncio
@pytest.mark.parametrize("path", RECORDS)
async def test_non_member_is_refused_whatever_it_selects(path: str) -> None:
    db = _seeded()
    url = path.format("a1")
    async with _as(db, MEMBER_A2, "proj-A1") as client:
        declared_a1 = await client.get(url)
    async with _as(db, MEMBER_A2, "proj-A2") as client:
        own_selection = await client.get(url)
    assert _forbidden(declared_a1), declared_a1.text
    assert own_selection.status_code == 403, own_selection.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "url", "body"),
    [
        ("PUT", "/api/claims/claim-a1", {"title": "moved"}),
        ("POST", "/api/claims/claim-a1/status", {"status": "submitted"}),
        ("DELETE", "/api/claims/claim-a1", None),
        ("GET", "/api/claims/claim-a1/approval", None),
        ("GET", "/api/claims/claim-a1/evidence-bundle", None),
        ("PUT", "/api/ipc-bills/ipc-a1", {"remarks": "moved"}),
        ("DELETE", "/api/ipc-bills/ipc-a1", None),
        ("GET", "/api/ipc-bills/export?ipc_id=ipc-a1", None),
        ("PUT", "/api/insurance/ins-a1", {"remarks": "moved"}),
        ("DELETE", "/api/insurance/ins-a1", None),
        ("GET", "/api/insurance/ins-a1/file", None),
        ("POST", "/api/insurance/ins-a1/replace-file", {"document_id": "token.pdf"}),
    ],
)
async def test_record_writes_and_actions_are_bound_by_the_selection(method: str, url: str, body: Any) -> None:
    db = _seeded()
    before = (list(db.claims.docs), list(db.ipc_bills.docs), list(db.insurance_policies.docs))
    async with _as(db, MEMBER_AB, "proj-A2") as client:
        mismatched = await client.request(method, url, json=body)
    async with _as(db, MEMBER_AB, None) as client:
        unselected = await client.request(method, url, json=body)
    assert _forbidden(mismatched), mismatched.text
    assert _selection_required(unselected), unselected.text
    assert (db.claims.docs, db.ipc_bills.docs, db.insurance_policies.docs) == before


@pytest.mark.asyncio
async def test_record_writes_succeed_under_the_records_own_project() -> None:
    db = _seeded()
    async with _as(db, MEMBER_AB, "proj-A1") as client:
        claim = await client.put("/api/claims/claim-a1", json={"title": "agreed"})
        insurance = await client.put("/api/insurance/ins-a1", json={"remarks": "agreed"})
        deleted = await client.delete("/api/ipc-bills/ipc-a1")
    assert claim.status_code == 200 and claim.json()["title"] == "agreed", claim.text
    assert insurance.status_code == 200, insurance.text
    assert deleted.status_code == 204, deleted.text


@pytest.mark.asyncio
async def test_project_less_legacy_claim_is_held_to_the_selected_organisation() -> None:
    db = _seeded()
    async with _as(db, MEMBER_AB, "proj-A1") as client:
        in_org = await client.get("/api/claims/claim-legacy-a/approval")
    async with _as(db, MEMBER_AB, None) as client:
        unselected = await client.get("/api/claims/claim-legacy-a/approval")
    async with _as(db, SUPERADMIN, "proj-B1") as client:
        other_org = await client.get("/api/claims/claim-legacy-a/approval")
    # The selection lets it through; membership and permission still decide (200 here).
    # (GET /claims/{id} of a project-less claim answers 409 from the relationship
    # presenter, which needs an explicit project - unrelated to the selection.)
    assert in_org.status_code == 200, in_org.text
    assert _selection_required(unselected), unselected.text
    assert _forbidden(other_org), other_org.text


# ---------------------------------------------------------------------------
# Lists, summaries, alerts, exports
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "row"), LISTS)
async def test_list_narrows_to_the_selection_and_stays_bounded_without_one(path: str, row: str) -> None:
    db = _seeded()
    async with _as(db, MEMBER_AB, "proj-A1") as client:
        a1 = await client.get(path)
    async with _as(db, MEMBER_AB, "proj-A2") as client:
        a2 = await client.get(path)
    async with _as(db, MEMBER_AB, None) as client:
        unselected = await client.get(path)
    assert a1.status_code == 200 and _ids(a1) == {row.format("a1")}, a1.text
    assert a2.status_code == 200 and _ids(a2) == {row.format("a2")}, a2.text
    assert unselected.status_code == 200, unselected.text
    # Bounded by membership: the member's own projects, never the foreign org.
    assert {row.format("a1"), row.format("a2")} <= _ids(unselected)
    assert row.format("b1") not in _ids(unselected)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", FILTERED_LISTS)
async def test_list_filter_may_not_leave_the_selection(path: str) -> None:
    db = _seeded()
    async with _as(db, MEMBER_AB, "proj-A1") as client:
        other_project = await client.get(path, params={"project_id": "proj-A2"})
        other_org = await client.get(path, params={"organization_id": "org-B"})
        own = await client.get(path, params={"project_id": "proj-A1"})
    assert _forbidden(other_project), other_project.text
    assert _forbidden(other_org), other_org.text
    assert own.status_code == 200, own.text


@pytest.mark.asyncio
async def test_register_export_is_pinned_to_the_selection() -> None:
    db = _seeded()
    async with _as(db, MEMBER_AB, "proj-A1") as client:
        ipc = await client.get("/api/ipc-bills/export")
        insurance = await client.get("/api/insurance/export")
    assert ipc.status_code == 200, ipc.text
    assert "IPC-A1" in ipc.text and "IPC-A2" not in ipc.text
    assert insurance.status_code == 200, insurance.text
    assert "INS-A1" in insurance.text and "INS-A2" not in insurance.text


@pytest.mark.asyncio
async def test_summaries_follow_the_selection() -> None:
    db = _seeded()
    async with _as(db, MEMBER_AB, "proj-A1") as client:
        ipc = await client.get("/api/ipc-bills/summary")
        insurance = await client.get("/api/insurance/summary")
    async with _as(db, MEMBER_AB, None) as client:
        ipc_all = await client.get("/api/ipc-bills/summary")
        insurance_all = await client.get("/api/insurance/summary")
    for response in (ipc, insurance, ipc_all, insurance_all):
        assert response.status_code == 200, response.text
    assert ipc.json()["total_ipcs"] == 1 and ipc_all.json()["total_ipcs"] == 2
    assert insurance.json()["total"] == 1 and insurance_all.json()["total"] == 2


@pytest.mark.asyncio
async def test_claim_list_gates_on_the_callers_own_organisation_without_a_filter() -> None:
    """No org filter and no selection: the subscription gate is asked about the caller's
    own organisation (it was asked about ``None`` before CL-4A), like the other lists."""
    db = _seeded()
    async with _as(db, MEMBER_AB, None) as client:
        response = await client.get("/api/claims")
    assert response.status_code == 200, response.text
    gate = [call for call in ENTITLEMENT_CALLS if call.get("permission") == "dms.claim.view"]
    assert gate and all(call["organization_id"] == "org-A" for call in gate), gate


# ---------------------------------------------------------------------------
# Superadmin
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "row"), LISTS)
async def test_superadmin_is_bound_by_an_explicit_selection(path: str, row: str) -> None:
    db = _seeded()
    record = f"{path}/{row.format('a1')}"
    async with _as(db, SUPERADMIN, "proj-A2") as client:
        mismatched = await client.get(record)
        narrowed = await client.get(path)
    async with _as(db, SUPERADMIN, None) as client:
        unselected = await client.get(record)
        broad = await client.get(path)
    async with _as(db, SUPERADMIN, "proj-A1") as client:
        allowed = await client.get(record)
    assert _forbidden(mismatched), mismatched.text
    assert _ids(narrowed) == {row.format("a2")}
    assert _selection_required(unselected), unselected.text
    assert broad.status_code == 200, broad.text
    assert {row.format(s) for s in ("a1", "a2", "b1")} <= _ids(broad)
    assert allowed.status_code == 200, allowed.text


# ---------------------------------------------------------------------------
# Creates
# ---------------------------------------------------------------------------


CREATES = [
    pytest.param("/api/claims", {"title": "New claim", "type": "eot"}, "claims", id="claim"),
    pytest.param("/api/ipc-bills", {"ipc_number": "IPC-NEW"}, "ipc_bills", id="ipc"),
    pytest.param(
        "/api/insurance",
        {"policy_number": "INS-NEW", "insurance_type": "Contractor's All Risk"},
        "insurance_policies",
        id="insurance",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("path", "body", "collection"), CREATES)
async def test_create_body_project_must_be_the_selection(path: str, body: dict, collection: str) -> None:
    db = _seeded()
    payload = {**body, "organization_id": "org-A", "project_id": "proj-A1"}
    count = len(getattr(db, collection).docs)
    async with _as(db, MEMBER_AB, "proj-A2") as client:
        mismatched = await client.post(path, json=payload)
    async with _as(db, MEMBER_AB, None) as client:
        unselected = await client.post(path, json=payload)
    assert _forbidden(mismatched), mismatched.text
    assert _selection_required(unselected), unselected.text
    assert len(getattr(db, collection).docs) == count

    async with _as(db, MEMBER_AB, "proj-A1") as client:
        created = await client.post(path, json=payload)
    assert created.status_code == 201, created.text
    assert created.json()["project_id"] == "proj-A1"


@pytest.mark.asyncio
async def test_project_less_claim_create_is_refused_not_rewritten() -> None:
    db = _seeded()
    payload = {"title": "No project", "organization_id": "org-A"}
    count = len(db.claims.docs)
    async with _as(db, MEMBER_AB, "proj-A1") as client:
        selected = await client.post("/api/claims", json=payload)
    async with _as(db, MEMBER_AB, None) as client:
        unselected = await client.post("/api/claims", json=payload)
    assert _forbidden(selected), selected.text
    assert _selection_required(unselected), unselected.text
    assert len(db.claims.docs) == count


# ---------------------------------------------------------------------------
# Intentionally global routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_insurance_type_master_is_not_selection_bound() -> None:
    db = _seeded()
    async with _as(db, MEMBER_AB, None) as client:
        unselected = await client.get("/api/insurance/types")
    assert unselected.status_code == 200, unselected.text
