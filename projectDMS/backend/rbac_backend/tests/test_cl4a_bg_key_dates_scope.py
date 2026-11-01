"""CL-4A: the selected navbar project bounds Bank Guarantees and Key Dates / EOT.

``EffectiveScope = membership/role scope ∩ explicit selected project``, proven over
HTTP against the REAL ``resolve_active_scope`` (no ``active_scope`` override) with the
real ``PolicyService`` / ``ScopeService``; only the permission and entitlement seams
are monkeypatched. Harness: the Hindrance HTTP suite's in-memory Mongo boundary.

* member of A1 + A2, selected A1, record A1   -> 200
* member of A1 + A2, selected A2, record A1   -> 403 ``context_forbidden``
* no project selected, record-level / write   -> 400 ``selection_required``
* non-member                                  -> refused (403)
* lists narrow to the selection; a filter naming another project -> 403;
  no selection -> bounded by membership (superadmin: broad)
* a create / import / workflow call naming another project -> 403, never rewritten
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
from rbac_backend.routers.bank_guarantees import router as bg_router
from rbac_backend.routers.key_dates import router as key_dates_router
from rbac_backend.services.entitlement_service import EntitlementService
from rbac_backend.services.permission_service import PermissionService
from rbac_backend.tests.test_hindrance_register_http import _Database, _principal

BG_ALL = {
    "dms.bankguarantee.view",
    "dms.bankguarantee.create",
    "dms.bankguarantee.edit",
    "dms.bankguarantee.delete",
    "dms.bankguarantee.extend",
    "dms.bankguarantee.release",
    "dms.bankguarantee.export",
}
KD_ALL = {
    "dms.keydate.view",
    "dms.keydate.create",
    "dms.keydate.edit",
    "dms.keydate.delete",
    "dms.keydate.eot_submit",
    "dms.keydate.eot_approve",
    "dms.keydate.baseline.freeze",
    "dms.keydate.eot.lock_submission",
    "dms.keydate.eot.determine",
    "dms.keydate.eot.freeze_determination",
    "dms.keydate.eot.supersede",
    "dms.keydate.achievement",
    "dms.keydate.export",
    "dms.document.view",
}

MEMBER_AB = _principal("u-member-ab", ["projectadmin"], "org-A", ["proj-A1", "proj-A2"])
MEMBER_A2 = _principal("u-member-a2", ["projectadmin"], "org-A", ["proj-A2"])
SUPERADMIN = _principal("u-root", ["superadmin"], None)

GRANTS: dict[str, set[str]] = {
    MEMBER_AB.id: BG_ALL | KD_ALL,
    MEMBER_A2.id: BG_ALL | KD_ALL,
}


@pytest.fixture(autouse=True)
def _authorization_seams(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _has_permission(
        self, user_id: str, permission_name: str, **_kwargs: Any
    ) -> bool:
        return permission_name in GRANTS.get(str(user_id), set())

    async def _entitled(self, **_kwargs: Any):
        return True, "ok"

    monkeypatch.setattr(PermissionService, "user_has_permission", _has_permission)
    monkeypatch.setattr(EntitlementService, "check_permission_entitlement", _entitled)


def _bg(bg_id: str, org: str, project: str) -> dict[str, Any]:
    return {
        "_id": bg_id,
        "bg_type": "performance",
        "bg_number": bg_id.upper(),
        "currency": "INR",
        "bg_status": "valid",
        "organization_id": org,
        "project_id": project,
        "linked_document_ids": [],
        "current_revision": 0,
        "created_at": datetime(2026, 1, 1),
    }


def _kd(kd_id: str, org: str, project: str) -> dict[str, Any]:
    return {
        "_id": kd_id,
        "milestone_ref": kd_id.upper(),
        "title": f"Milestone {kd_id}",
        "organization_id": org,
        "project_id": project,
        "contract_id": "primary",
        "contractual_week_number": 4,
        "status": "pending",
        "linked_document_ids": [],
        "created_at": datetime(2026, 1, 1),
    }


def _seeded() -> _Database:
    db = _Database()
    db.projects.docs.extend(
        [
            {
                "_id": "proj-A1",
                "organization_id": "org-A",
                "name": "A1",
                "is_active": True,
            },
            {
                "_id": "proj-A2",
                "organization_id": "org-A",
                "name": "A2",
                "is_active": True,
            },
            {
                "_id": "proj-B1",
                "organization_id": "org-B",
                "name": "B1",
                "is_active": True,
            },
        ]
    )
    db.bank_guarantees.docs.extend(
        [
            _bg("bg-A1", "org-A", "proj-A1"),
            _bg("bg-A2", "org-A", "proj-A2"),
            _bg("bg-B1", "org-B", "proj-B1"),
        ]
    )
    db.key_date_milestones.docs.extend(
        [
            _kd("kd-A1", "org-A", "proj-A1"),
            _kd("kd-A2", "org-A", "proj-A2"),
            _kd("kd-B1", "org-B", "proj-B1"),
        ]
    )
    db.key_date_eot_submissions.docs.append(
        {
            "_id": "eots-A1",
            "organization_id": "org-A",
            "project_id": "proj-A1",
            "contract_id": "primary",
            "revision_number": 1,
            "revision_label": "EOT-1",
            "status": "draft",
            "items": [],
            "created_at": datetime(2026, 2, 1),
        }
    )
    db.key_date_eot_determinations.docs.append(
        {
            "_id": "eotd-A1",
            "organization_id": "org-A",
            "project_id": "proj-A1",
            "contract_id": "primary",
            "status": "under_review",
            "eot_submission_ids": ["eots-A1"],
            "items": [],
            "created_at": datetime(2026, 2, 2),
        }
    )
    return db


def _app(db: _Database, user: Any) -> FastAPI:
    app = FastAPI()
    app.include_router(bg_router, prefix="/api")
    app.include_router(key_dates_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    return app


@asynccontextmanager
async def _as(db: _Database, user: Any, project: str | None):
    """A client whose requests carry ``project`` as the navbar selection (None = nothing selected)."""
    headers = {"X-Proj-Id": project} if project else {}
    transport = httpx.ASGITransport(app=_app(db, user))
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=headers
    ) as client:
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


def _ids(response: httpx.Response) -> set[str]:
    return {row.get("_id") or row.get("id") for row in response.json()}


# ---------------------------------------------------------------------------
# Record-level routes: held to the selection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/api/bank-guarantees/bg-A1",
        "/api/bank-guarantees/bg-A1/history",
        "/api/bank-guarantees/bg-A1/events",
        "/api/key-dates/kd-A1",
        "/api/key-dates/kd-A1/eots",
        "/api/key-dates/kd-A1/history",
        "/api/key-dates/eot-submissions/eots-A1/export",
        "/api/key-dates/eot-determinations/eotd-A1/export",
    ],
)
async def test_record_reads_follow_the_selected_project(path: str) -> None:
    db = _seeded()
    async with _as(db, MEMBER_AB, "proj-A1") as client:
        allowed = await client.get(path)
    async with _as(db, MEMBER_AB, "proj-A2") as client:
        mismatched = await client.get(path)
    async with _as(db, MEMBER_AB, None) as client:
        unselected = await client.get(path)
    assert allowed.status_code == 200, allowed.text
    assert _forbidden(mismatched), mismatched.text
    assert _selection_required(unselected), unselected.text
    assert "proj-A1" not in mismatched.text


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/bank-guarantees/bg-A1", "/api/key-dates/kd-A1"])
async def test_non_member_is_refused_whatever_it_selects(path: str) -> None:
    db = _seeded()
    async with _as(db, MEMBER_A2, "proj-A1") as client:
        declared_a1 = await client.get(path)
    async with _as(db, MEMBER_A2, "proj-A2") as client:
        own_selection = await client.get(path)
    assert _forbidden(declared_a1), declared_a1.text
    assert own_selection.status_code == 403, own_selection.text


@pytest.mark.asyncio
async def test_missing_record_answers_selection_first_then_404() -> None:
    db = _seeded()
    async with _as(db, MEMBER_AB, None) as client:
        unselected_bg = await client.get("/api/bank-guarantees/missing")
        unselected_kd = await client.get("/api/key-dates/missing")
    async with _as(db, MEMBER_AB, "proj-A1") as client:
        missing_bg = await client.get("/api/bank-guarantees/missing")
        missing_kd = await client.get("/api/key-dates/missing")
    assert _selection_required(unselected_bg) and _selection_required(unselected_kd)
    assert missing_bg.status_code == 404 and missing_kd.status_code == 404


@pytest.mark.asyncio
async def test_bg_writes_are_bound_by_the_selection() -> None:
    db = _seeded()
    async with _as(db, MEMBER_AB, "proj-A2") as client:
        put = await client.put("/api/bank-guarantees/bg-A1", json={"remarks": "moved"})
        release = await client.post("/api/bank-guarantees/bg-A1/release", json={})
        delete = await client.delete("/api/bank-guarantees/bg-A1")
    async with _as(db, MEMBER_AB, None) as client:
        put_unselected = await client.put(
            "/api/bank-guarantees/bg-A1", json={"remarks": "moved"}
        )
    assert _forbidden(put) and _forbidden(release) and _forbidden(delete), (
        put.text,
        release.text,
        delete.text,
    )
    assert _selection_required(put_unselected), put_unselected.text
    row = next(row for row in db.bank_guarantees.docs if row["_id"] == "bg-A1")
    assert row.get("remarks") is None and row["bg_status"] == "valid"

    async with _as(db, MEMBER_AB, "proj-A1") as client:
        ok_put = await client.put(
            "/api/bank-guarantees/bg-A1", json={"remarks": "agreed"}
        )
    assert ok_put.status_code == 200, ok_put.text


@pytest.mark.asyncio
async def test_achievement_and_eot_writes_hold_the_parent() -> None:
    db = _seeded()
    body = {"actual_achievement_date": "2026-03-01T00:00:00Z"}
    async with _as(db, MEMBER_AB, "proj-A2") as client:
        achievement = await client.post("/api/key-dates/kd-A1/achievement", json=body)
        lock = await client.post("/api/key-dates/eot-submissions/eots-A1/lock")
        freeze = await client.post("/api/key-dates/eot-determinations/eotd-A1/freeze")
    async with _as(db, MEMBER_AB, None) as client:
        achievement_unselected = await client.post(
            "/api/key-dates/kd-A1/achievement", json=body
        )
        lock_unselected = await client.post(
            "/api/key-dates/eot-submissions/eots-A1/lock"
        )
    assert _forbidden(achievement) and _forbidden(lock) and _forbidden(freeze)
    assert _selection_required(achievement_unselected) and _selection_required(
        lock_unselected
    )
    assert not db.key_date_achievements.docs
    assert db.key_date_eot_submissions.docs[0]["status"] == "draft"


# ---------------------------------------------------------------------------
# Lists: narrowed, never widened
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path,prefix", [("/api/bank-guarantees", "bg"), ("/api/key-dates", "kd")]
)
async def test_lists_narrow_to_the_selection_and_stay_bounded_without_one(
    path: str, prefix: str
) -> None:
    db = _seeded()
    async with _as(db, MEMBER_AB, "proj-A1") as client:
        a1 = await client.get(path)
        leave = await client.get(path, params={"project_id": "proj-A2"})
        leave_org = await client.get(path, params={"organization_id": "org-B"})
    async with _as(db, MEMBER_AB, "proj-A2") as client:
        a2 = await client.get(path)
    async with _as(db, MEMBER_AB, None) as client:
        unselected = await client.get(path)
    assert a1.status_code == 200 and _ids(a1) == {f"{prefix}-A1"}, a1.text
    assert a2.status_code == 200 and _ids(a2) == {f"{prefix}-A2"}, a2.text
    assert _forbidden(leave), leave.text
    assert _forbidden(leave_org), leave_org.text
    assert unselected.status_code == 200 and _ids(unselected) == {
        f"{prefix}-A1",
        f"{prefix}-A2",
    }, unselected.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        "/api/bank-guarantees/summary",
        "/api/bank-guarantees/alerts",
        "/api/bank-guarantees/export",
        "/api/key-dates/dashboard",
        "/api/key-dates/export",
    ],
)
async def test_summaries_and_exports_refuse_a_filter_outside_the_selection(
    path: str,
) -> None:
    db = _seeded()
    async with _as(db, MEMBER_AB, "proj-A1") as client:
        selected = await client.get(path)
        leave = await client.get(path, params={"project_id": "proj-A2"})
    assert selected.status_code == 200, selected.text
    assert _forbidden(leave), leave.text


# ---------------------------------------------------------------------------
# Creates, imports and project-level workflow routes: refused, never rewritten
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_creates_never_rewrite_the_payload_project() -> None:
    db = _seeded()
    bg_body = {
        "project_id": "proj-A1",
        "organization_id": "org-A",
        "bg_number": "BG-NEW",
    }
    kd_body = {
        "project_id": "proj-A1",
        "organization_id": "org-A",
        "title": "KD-NEW",
        "contractual_week_number": 2,
        "project_start_date": "2026-01-01T00:00:00Z",
    }
    async with _as(db, MEMBER_AB, "proj-A2") as client:
        bg_mismatch = await client.post("/api/bank-guarantees", json=bg_body)
        kd_mismatch = await client.post("/api/key-dates", json=kd_body)
    async with _as(db, MEMBER_AB, None) as client:
        bg_unselected = await client.post("/api/bank-guarantees", json=bg_body)
        kd_unselected = await client.post("/api/key-dates", json=kd_body)
    assert _forbidden(bg_mismatch) and _forbidden(kd_mismatch), (
        bg_mismatch.text,
        kd_mismatch.text,
    )
    assert _selection_required(bg_unselected) and _selection_required(kd_unselected)
    assert not any(row.get("bg_number") == "BG-NEW" for row in db.bank_guarantees.docs)
    assert not any(row.get("title") == "KD-NEW" for row in db.key_date_milestones.docs)

    async with _as(db, MEMBER_AB, "proj-A1") as client:
        created = await client.post("/api/bank-guarantees", json=bg_body)
    assert created.status_code == 201, created.text
    assert created.json()["project_id"] == "proj-A1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path", ["/api/bank-guarantees/import/preview", "/api/key-dates/import/preview"]
)
async def test_csv_import_form_project_must_be_the_selection(path: str) -> None:
    db = _seeded()
    form = {"organization_id": "org-A", "project_id": "proj-A1"}
    files = {"file": ("import.csv", b"bg_number\nBG-9\n", "text/csv")}
    async with _as(db, MEMBER_AB, "proj-A2") as client:
        mismatched = await client.post(path, data=form, files=files)
    async with _as(db, MEMBER_AB, None) as client:
        unselected = await client.post(path, data=form, files=files)
    assert _forbidden(mismatched), mismatched.text
    assert _selection_required(unselected), unselected.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path,kwargs",
    [
        ("get", "/api/key-dates/workflow", {"params": {"project_id": "proj-A1"}}),
        ("get", "/api/key-dates/revisions", {"params": {"project_id": "proj-A1"}}),
        (
            "get",
            "/api/key-dates/baseline/export",
            {"params": {"project_id": "proj-A1"}},
        ),
        ("get", "/api/key-dates/history/export", {"params": {"project_id": "proj-A1"}}),
        ("post", "/api/key-dates/recalculate", {"params": {"project_id": "proj-A1"}}),
        (
            "post",
            "/api/key-dates/baseline/freeze",
            {
                "json": {
                    "project_id": "proj-A1",
                    "organization_id": "org-A",
                    "confirmation": True,
                }
            },
        ),
        (
            "post",
            "/api/key-dates/eot-submissions",
            {"json": {"project_id": "proj-A1", "organization_id": "org-A"}},
        ),
        (
            "post",
            "/api/key-dates/eot-determinations",
            {
                "json": {
                    "project_id": "proj-A1",
                    "organization_id": "org-A",
                    "eot_submission_ids": ["eots-A1"],
                }
            },
        ),
    ],
)
async def test_workflow_routes_hold_the_named_project(
    method: str, path: str, kwargs: dict[str, Any]
) -> None:
    db = _seeded()
    async with _as(db, MEMBER_AB, "proj-A2") as client:
        mismatched = await getattr(client, method)(path, **kwargs)
    async with _as(db, MEMBER_AB, None) as client:
        unselected = await getattr(client, method)(path, **kwargs)
    assert _forbidden(mismatched), mismatched.text
    assert _selection_required(unselected), unselected.text
    assert len(db.key_date_eot_submissions.docs) == 1
    assert len(db.key_date_eot_determinations.docs) == 1


@pytest.mark.asyncio
async def test_workflow_summary_answers_for_the_selected_project() -> None:
    db = _seeded()
    async with _as(db, MEMBER_AB, "proj-A1") as client:
        summary = await client.get(
            "/api/key-dates/workflow", params={"project_id": "proj-A1"}
        )
    assert summary.status_code == 200, summary.text


# ---------------------------------------------------------------------------
# Superadmin: an explicit selection still constrains
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "record,path,prefix",
    [
        ("/api/bank-guarantees/bg-A1", "/api/bank-guarantees", "bg"),
        ("/api/key-dates/kd-A1", "/api/key-dates", "kd"),
    ],
)
async def test_superadmin_is_bound_by_an_explicit_selection(
    record: str, path: str, prefix: str
) -> None:
    db = _seeded()
    async with _as(db, SUPERADMIN, "proj-A2") as client:
        mismatched = await client.get(record)
        narrowed = await client.get(path)
    async with _as(db, SUPERADMIN, None) as client:
        unselected = await client.get(record)
        broad = await client.get(path)
    async with _as(db, SUPERADMIN, "proj-A1") as client:
        allowed = await client.get(record)
    assert _forbidden(mismatched), mismatched.text
    assert narrowed.status_code == 200 and _ids(narrowed) == {f"{prefix}-A2"}, (
        narrowed.text
    )
    assert _selection_required(unselected), unselected.text
    assert broad.status_code == 200 and {
        f"{prefix}-A1",
        f"{prefix}-A2",
        f"{prefix}-B1",
    } <= _ids(broad)
    assert allowed.status_code == 200, allowed.text
