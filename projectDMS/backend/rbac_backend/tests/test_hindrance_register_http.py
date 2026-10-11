"""Hindrance & Constraint Register — HTTP contract.

The register extends the existing ``delay_events`` collection. Every test here
drives the mounted routers through ``httpx.ASGITransport`` with the REAL
``PolicyService`` / ``ScopeService`` / ``DocumentRelationshipService``; only two
seams are replaced:

* ``PermissionService.user_has_permission`` answers from an explicit per-user
  grant table, so a test states exactly which permissions a principal holds;
* ``EntitlementService.check_permission_entitlement`` allows, because
  subscription gating is proven elsewhere and is not what these tests are about.

Tenant scope, project ownership, document authority and audit all run for real
against an in-memory Mongo boundary that implements the operators the service
uses and fails loudly on anything it does not.
"""

from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Iterable

import httpx
import pytest
from fastapi import FastAPI
from pymongo.errors import DuplicateKeyError

from rbac_backend.core.database import get_db
from rbac_backend.core.security import CurrentUser, get_current_user
from rbac_backend.routers.document_relationships import router as relationship_router
from rbac_backend.routers.evidence_registers import router as registers_router
from rbac_backend.routers.hindrances import router as hindrances_router
from rbac_backend.services.entitlement_service import EntitlementService
from rbac_backend.services.evidence_graph_service import EvidenceGraphService
from rbac_backend.services.permission_service import PermissionService


# ---------------------------------------------------------------------------
# In-memory Mongo boundary
# ---------------------------------------------------------------------------

_MISSING = object()


def _field(document: dict[str, Any], dotted: str) -> Any:
    value: Any = document
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return _MISSING
        value = value[part]
    return value


def _condition(value: Any, condition: Any) -> bool:
    present = value is not _MISSING
    actual = value if present else None
    if not isinstance(condition, dict) or not any(str(k).startswith("$") for k in condition):
        if isinstance(actual, list) and not isinstance(condition, list):
            return condition in actual
        return actual == condition
    for operator, expected in condition.items():
        if operator == "$in":
            if isinstance(actual, list):
                if not any(item in expected for item in actual):
                    return False
            elif actual not in expected:
                return False
        elif operator == "$nin":
            if actual in expected:
                return False
        elif operator == "$ne":
            if actual == expected:
                return False
        elif operator == "$exists":
            if present != bool(expected):
                return False
        elif operator == "$type":
            if expected != "string" or not isinstance(actual, str):
                return False
        elif operator in {"$gt", "$gte", "$lt", "$lte"}:
            if actual is None:
                return False
            try:
                ok = {
                    "$gt": actual > expected,
                    "$gte": actual >= expected,
                    "$lt": actual < expected,
                    "$lte": actual <= expected,
                }[operator]
            except TypeError:
                return False
            if not ok:
                return False
        elif operator == "$regex":
            flags = re.IGNORECASE if "i" in str(condition.get("$options") or "") else 0
            if not isinstance(actual, str) or not re.search(expected, actual, flags):
                return False
        elif operator == "$options":
            continue
        else:
            raise NotImplementedError(f"fake Mongo does not implement {operator}")
    return True


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, condition in (query or {}).items():
        if key == "$and":
            if not all(_matches(document, item) for item in condition):
                return False
        elif key == "$or":
            if not any(_matches(document, item) for item in condition):
                return False
        elif key.startswith("$"):
            raise NotImplementedError(f"fake Mongo does not implement {key}")
        elif not _condition(_field(document, key), condition):
            return False
    return True


def _sort_key(value: Any) -> tuple[int, Any]:
    if value is None:
        return (0, "")
    if isinstance(value, datetime):
        return (1, value.isoformat())
    if isinstance(value, (int, float)):
        return (1, f"{value:020.6f}")
    return (1, str(value))


class _Cursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = documents

    def sort(self, key: Any, direction: int = 1):
        keys = key if isinstance(key, list) else [(key, direction)]
        for field, order in reversed(keys):
            self.documents.sort(
                key=lambda row: _sort_key(None if _field(row, field) is _MISSING else _field(row, field)),
                reverse=order < 0,
            )
        return self

    def skip(self, count: int):
        self.documents = self.documents[count:]
        return self

    def limit(self, count: int):
        if count:
            self.documents = self.documents[:count]
        return self

    async def to_list(self, length: int | None = None):
        return deepcopy(self.documents if length is None else self.documents[:length])

    def __aiter__(self):
        self._iterator = iter(deepcopy(self.documents))
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _Collection:
    def __init__(self, name: str, unique: Iterable[tuple[tuple[str, ...], Any]] = ()) -> None:
        self.name = name
        self.docs: list[dict[str, Any]] = []
        self.unique = list(unique)

    def _check_unique(self, candidate: dict[str, Any], ignore: dict[str, Any] | None = None) -> None:
        if any(row is not ignore and row.get("_id") == candidate.get("_id") for row in self.docs):
            raise DuplicateKeyError(f"duplicate _id in {self.name}")
        for fields, applies in self.unique:
            if not applies(candidate):
                continue
            identity = tuple(candidate.get(field) for field in fields)
            for row in self.docs:
                if row is ignore or not applies(row):
                    continue
                if tuple(row.get(field) for field in fields) == identity:
                    raise DuplicateKeyError(f"duplicate {fields} in {self.name}")

    async def create_index(self, *args: Any, **kwargs: Any) -> str:
        return kwargs.get("name") or "idx"

    async def find_one(self, query: dict[str, Any] | None = None, *args: Any, sort: Any = None, **kwargs: Any):
        rows = [row for row in self.docs if _matches(row, query or {})]
        if sort:
            rows = _Cursor(rows).sort(sort).documents
        return deepcopy(rows[0]) if rows else None

    def find(self, query: dict[str, Any] | None = None, *args: Any, **kwargs: Any):
        return _Cursor([deepcopy(row) for row in self.docs if _matches(row, query or {})])

    async def count_documents(self, query: dict[str, Any] | None = None, *args: Any, **kwargs: Any) -> int:
        return len([row for row in self.docs if _matches(row, query or {})])

    async def insert_one(self, document: dict[str, Any], *args: Any, **kwargs: Any):
        stored = deepcopy(document)
        stored.setdefault("_id", f"{self.name}-{len(self.docs) + 1}")
        self._check_unique(stored)
        self.docs.append(stored)
        return SimpleNamespace(inserted_id=stored["_id"])

    @staticmethod
    def _apply(row: dict[str, Any], update: dict[str, Any], *, inserting: bool) -> None:
        unsupported = set(update) - {"$set", "$unset", "$inc", "$setOnInsert"}
        if unsupported:
            raise NotImplementedError(f"fake Mongo does not implement {sorted(unsupported)}")
        if inserting:
            row.update(deepcopy(update.get("$setOnInsert", {})))
        row.update(deepcopy(update.get("$set", {})))
        for field in update.get("$unset", {}):
            row.pop(field, None)
        for field, amount in update.get("$inc", {}).items():
            row[field] = int(row.get(field) or 0) + int(amount)

    async def update_one(self, query: dict[str, Any], update: dict[str, Any], *args: Any, upsert: bool = False, **kwargs: Any):
        for row in self.docs:
            if _matches(row, query):
                candidate = deepcopy(row)
                self._apply(candidate, update, inserting=False)
                self._check_unique(candidate, ignore=row)
                row.clear()
                row.update(candidate)
                return SimpleNamespace(matched_count=1, modified_count=1, upserted_id=None)
        if not upsert:
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=None)
        inserted = {key: value for key, value in query.items() if not key.startswith("$") and not isinstance(value, dict)}
        self._apply(inserted, update, inserting=True)
        inserted.setdefault("_id", f"{self.name}-{len(self.docs) + 1}")
        self._check_unique(inserted)
        self.docs.append(inserted)
        return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=inserted["_id"])

    async def find_one_and_update(self, query: dict[str, Any], update: dict[str, Any], *args: Any, upsert: bool = False, return_document: Any = False, **kwargs: Any):
        before = await self.find_one(query)
        result = await self.update_one(query, update, upsert=upsert)
        if before is None and result.upserted_id is None:
            return None
        if not return_document:
            return before
        target = before["_id"] if before is not None else result.upserted_id
        return await self.find_one({"_id": target})

    async def delete_one(self, query: dict[str, Any], *args: Any, **kwargs: Any):
        for index, row in enumerate(self.docs):
            if _matches(row, query):
                self.docs.pop(index)
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)


def _is_str_ref(row: dict[str, Any]) -> bool:
    return isinstance(row.get("hindrance_ref"), str)


def _active(row: dict[str, Any]) -> bool:
    return row.get("removed_at") is None


class _Database:
    def __init__(self) -> None:
        self._collections: dict[str, _Collection] = {
            "delay_events": _Collection(
                "delay_events",
                [(("organization_id", "project_id", "hindrance_ref"), _is_str_ref)],
            ),
            "entity_document_links": _Collection(
                "entity_document_links",
                [(("organization_id", "project_id", "target_type", "target_id", "document_id", "relationship_role"), _active)],
            ),
            "delay_event_links": _Collection(
                "delay_event_links",
                [(("organization_id", "project_id", "delay_event_id", "target_type", "target_id"), _active)],
            ),
        }

    def __getattr__(self, name: str) -> _Collection:
        if name.startswith("_") or name == "client":
            raise AttributeError(name)
        return self._collections.setdefault(name, _Collection(name))

    def __getitem__(self, name: str) -> _Collection:
        return getattr(self, name)


# ---------------------------------------------------------------------------
# Principals, grants and seed data
# ---------------------------------------------------------------------------

HINDRANCE_ALL = {
    "dms.hindrance.view",
    "dms.hindrance.create",
    "dms.hindrance.edit",
    "dms.hindrance.archive",
}
READ_TARGETS = {"dms.document.view", "dms.keydate.view", "dms.evidence_graph.view"}


def _principal(user_id: str, roles: list[str], org: str | None, projects: list[str] = ()) -> CurrentUser:
    return CurrentUser(
        id=user_id,
        username=user_id,
        email=f"{user_id}@example.com",
        roles=roles,
        organization_id=org,
        organizations=[org] if org else [],
        projects=list(projects),
    )


ORG_A_ADMIN = _principal("u-orgadmin-a", ["orgadmin"], "org-A")
PROJECT_A1_ADMIN = _principal("u-projadmin-a1", ["projectadmin"], "org-A", ["proj-A1"])
PROJECT_A2_USER = _principal("u-projuser-a2", ["projectuser"], "org-A", ["proj-A2"])
VIEWER_A1 = _principal("u-viewer-a1", ["projectuser"], "org-A", ["proj-A1"])
EDITOR_A1 = _principal("u-editor-a1", ["projectuser"], "org-A", ["proj-A1"])
NO_PERMISSION_A1 = _principal("u-noperm-a1", ["projectuser"], "org-A", ["proj-A1"])
ORG_B_ADMIN = _principal("u-orgadmin-b", ["orgadmin"], "org-B")
SUPERADMIN = _principal("u-root", ["superadmin"], None)

GRANTS: dict[str, set[str]] = {
    ORG_A_ADMIN.id: HINDRANCE_ALL | READ_TARGETS,
    PROJECT_A1_ADMIN.id: HINDRANCE_ALL | READ_TARGETS,
    PROJECT_A2_USER.id: {"dms.hindrance.view", "dms.hindrance.create", "dms.hindrance.edit"} | READ_TARGETS,
    VIEWER_A1.id: {"dms.hindrance.view"} | READ_TARGETS,
    EDITOR_A1.id: {"dms.hindrance.view", "dms.hindrance.create", "dms.hindrance.edit", "dms.document.view"},
    NO_PERMISSION_A1.id: READ_TARGETS,
    ORG_B_ADMIN.id: HINDRANCE_ALL | READ_TARGETS,
}


@pytest.fixture(autouse=True)
def _authorization_seams(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _has_permission(self, user_id: str, permission_name: str, **_kwargs: Any) -> bool:
        return permission_name in GRANTS.get(str(user_id), set())

    async def _entitled(self, **_kwargs: Any):
        return True, "ok"

    monkeypatch.setattr(PermissionService, "user_has_permission", _has_permission)
    monkeypatch.setattr(EntitlementService, "check_permission_entitlement", _entitled)


def _document(document_id: str, org: str, project: str, **overrides: Any) -> dict[str, Any]:
    row = {
        "_id": document_id,
        "organization_id": org,
        "project_id": project,
        "filename": f"{document_id}.pdf",
        "subject": f"Site record {document_id}",
        "processing_status": "completed",
        "duplicate_status": "unique",
        "lifecycle_state": "active",
        "current_version_id": f"{document_id}-v1",
    }
    row.update(overrides)
    return row


def _seeded() -> _Database:
    db = _Database()
    db.projects.docs.extend(
        [
            {"_id": "proj-A1", "organization_id": "org-A", "name": "Metro Package A1"},
            {"_id": "proj-A2", "organization_id": "org-A", "name": "Metro Package A2"},
            {"_id": "proj-B1", "organization_id": "org-B", "name": "Harbour B1"},
        ]
    )
    db.documents.docs.extend(
        [
            _document("doc-A1", "org-A", "proj-A1"),
            _document("doc-A1-second", "org-A", "proj-A1"),
            _document("doc-A2", "org-A", "proj-A2"),
            _document("doc-B1", "org-B", "proj-B1"),
            _document("doc-A1-blocked", "org-A", "proj-A1", processing_status="human_review_required"),
        ]
    )
    db.programme_milestones.docs.extend(
        [
            {"_id": "act-A1", "organization_id": "org-A", "project_id": "proj-A1", "milestone_ref": "ACT-110", "title": "Pier P4 piling", "milestone_type": "programme_activity", "status": "in_progress", "planned_date": datetime(2026, 3, 1)},
            {"_id": "act-A2", "organization_id": "org-A", "project_id": "proj-A2", "milestone_ref": "ACT-210", "title": "Viaduct span", "milestone_type": "programme_activity", "status": "planned", "planned_date": datetime(2026, 4, 1)},
            {"_id": "act-B1", "organization_id": "org-B", "project_id": "proj-B1", "milestone_ref": "ACT-910", "title": "Quay wall", "milestone_type": "programme_activity", "status": "planned", "planned_date": datetime(2026, 5, 1)},
        ]
    )
    db.key_date_milestones.docs.extend(
        [
            {"_id": "kd-A1", "organization_id": "org-A", "project_id": "proj-A1", "milestone_ref": "KD-03", "title": "Access to Station S2", "status": "pending", "due_date": datetime(2026, 6, 30)},
            {"_id": "kd-A2", "organization_id": "org-A", "project_id": "proj-A2", "milestone_ref": "KD-07", "title": "Depot handover", "status": "pending"},
            {"_id": "kd-B1", "organization_id": "org-B", "project_id": "proj-B1", "milestone_ref": "KD-91", "title": "Berth 1", "status": "pending"},
        ]
    )
    db.key_date_eot_submissions.docs.extend(
        [
            {"_id": "eot-A1", "organization_id": "org-A", "project_id": "proj-A1", "revision_label": "EOT-1", "status": "draft", "locked_at": None},
            {"_id": "eot-B1", "organization_id": "org-B", "project_id": "proj-B1", "revision_label": "EOT-9", "status": "draft", "locked_at": None},
        ]
    )
    return db


def _app(db: _Database, user: CurrentUser) -> FastAPI:
    app = FastAPI()
    app.include_router(hindrances_router, prefix="/api")
    app.include_router(registers_router, prefix="/api")
    app.include_router(relationship_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: user
    return app


_AUTO: Any = object()


def _default_selection(user: CurrentUser) -> str | None:
    """The navbar selection an ordinary session of this principal would send."""
    if user.projects:
        return user.projects[0]
    return {"org-A": "proj-A1", "org-B": "proj-B1"}.get(user.organization_id or "")


@asynccontextmanager
async def _as(db: _Database, user: CurrentUser, project: Any = _AUTO, org: str | None = None):
    """A client for ``user`` whose requests carry the navbar selection as the browser does.

    ``project=None`` sends no selection at all.
    """
    selected = _default_selection(user) if project is _AUTO else project
    headers = {}
    if selected:
        headers["X-Proj-Id"] = selected
    if org:
        headers["X-Org-Id"] = org
    transport = httpx.ASGITransport(app=_app(db, user))
    async with httpx.AsyncClient(transport=transport, base_url="http://test", headers=headers) as client:
        yield client


def _payload(**overrides: Any) -> dict[str, Any]:
    body = {
        "project_id": "proj-A1",
        "event_type": "hindrance",
        "title": "Site access blocked at Station S2",
        "description": "Police barricading prevented crane access.",
        "category": "site_access",
        "start_date": "2026-02-10T00:00:00",
        "responsible_party": "Employer",
        "affected_party": "Contractor",
        "responsibility": "employer",
        "location": "Station S2",
        "impact_description": "Crane mobilisation stood down for two shifts.",
    }
    body.update(overrides)
    return body


async def _create(db: _Database, user: CurrentUser = PROJECT_A1_ADMIN, **overrides: Any) -> dict[str, Any]:
    async with _as(db, user, project=overrides.get("project_id", "proj-A1")) as client:
        response = await client.post("/api/hindrances", json=_payload(**overrides))
    assert response.status_code == 201, response.text
    return response.json()


def _audits(db: _Database, action: str) -> list[dict[str, Any]]:
    return [row for row in db.audit_events.docs if row.get("action") == action]


def _stored(db: _Database, item_id: str) -> dict[str, Any]:
    return next(row for row in db.delay_events.docs if row["_id"] == item_id)


# ---------------------------------------------------------------------------
# 1-4, 16: create / list / get / edit and the generated reference
# ---------------------------------------------------------------------------


async def test_create_persists_a_project_scoped_record_with_a_generated_reference() -> None:
    db = _seeded()

    created = await _create(db)

    assert created["hindrance_ref"] == "HIN-0001"
    assert created["event_type"] == "hindrance"
    assert created["organization_id"] == "org-A"  # derived from the project, never guessed
    assert created["project_id"] == "proj-A1"
    assert created["status"] == "open"
    assert created["created_by"] == PROJECT_A1_ADMIN.id
    stored = _stored(db, created["id"])
    assert stored["hindrance_ref"] == "HIN-0001"
    assert stored["category"] == "site_access"
    assert stored["responsible_party"] == "Employer"
    assert stored["affected_party"] == "Contractor"
    assert stored["impact_description"].startswith("Crane mobilisation")
    assert stored["archived_at"] is None


async def test_each_event_type_numbers_independently_within_its_project() -> None:
    db = _seeded()

    first = await _create(db)
    second = await _create(db, title="Second hindrance")
    constraint = await _create(db, event_type="constraint", title="Night-work restriction")
    delay = await _create(db, event_type="delay_event", title="Late drawings")
    other_project = await _create(db, user=PROJECT_A2_USER, project_id="proj-A2")

    assert [first["hindrance_ref"], second["hindrance_ref"]] == ["HIN-0001", "HIN-0002"]
    assert constraint["hindrance_ref"] == "CNS-0001"
    assert delay["hindrance_ref"] == "DLY-0001"
    assert other_project["hindrance_ref"] == "HIN-0001"


async def test_the_reference_is_server_owned_and_immutable() -> None:
    db = _seeded()
    created = await _create(db)

    async with _as(db, PROJECT_A1_ADMIN) as client:
        supplied = await client.post("/api/hindrances", json=_payload(hindrance_ref="HIN-9999"))
        renamed = await client.patch(f"/api/hindrances/{created['id']}", json={"hindrance_ref": "HIN-9999"})
        moved = await client.patch(f"/api/hindrances/{created['id']}", json={"project_id": "proj-A2"})

    assert supplied.status_code == 422
    assert renamed.status_code == 422
    assert moved.status_code == 422
    assert _stored(db, created["id"])["hindrance_ref"] == "HIN-0001"
    assert _stored(db, created["id"])["project_id"] == "proj-A1"


async def test_concurrent_creates_never_share_a_reference() -> None:
    db = _seeded()

    async with _as(db, PROJECT_A1_ADMIN) as client:
        responses = await asyncio.gather(
            *[client.post("/api/hindrances", json=_payload(title=f"Concurrent {index}")) for index in range(12)]
        )

    assert all(response.status_code == 201 for response in responses), [r.text for r in responses]
    references = [response.json()["hindrance_ref"] for response in responses]
    assert len(set(references)) == 12
    assert sorted(references) == [f"HIN-{index:04d}" for index in range(1, 13)]


async def test_reference_generation_recovers_when_the_counter_lags_existing_rows() -> None:
    db = _seeded()
    db.delay_events.docs.append(
        {
            "_id": "imported-1",
            "organization_id": "org-A",
            "project_id": "proj-A1",
            "event_type": "hindrance",
            "hindrance_ref": "HIN-0001",
            "title": "Imported before the counter existed",
            "start_date": datetime(2026, 1, 5),
            "status": "open",
        }
    )

    created = await _create(db)

    assert created["hindrance_ref"] == "HIN-0002"


async def test_list_filters_searches_sorts_and_paginates_within_scope() -> None:
    db = _seeded()
    await _create(db, title="Utility clash at P4", category="utility_diversion", start_date="2026-01-05T00:00:00", responsibility="employer")
    await _create(db, event_type="constraint", title="Night-work ban", category="local_restrictions", start_date="2026-02-05T00:00:00", responsibility="neutral")
    await _create(db, title="Drawing approval pending", category="drawing_approval", start_date="2026-03-05T00:00:00", responsibility="employer", status="under_review")

    async with _as(db, PROJECT_A1_ADMIN) as client:
        everything = (await client.get("/api/hindrances", params={"project_id": "proj-A1", "sort": "start_date", "order": "asc"})).json()
        by_type = (await client.get("/api/hindrances", params={"event_type": "constraint"})).json()
        by_status = (await client.get("/api/hindrances", params={"status": "under_review"})).json()
        by_responsibility = (await client.get("/api/hindrances", params={"responsibility": "employer"})).json()
        searched = (await client.get("/api/hindrances", params={"q": "UTILITY"})).json()
        dated = (await client.get("/api/hindrances", params={"start_from": "2026-02-01T00:00:00", "start_to": "2026-02-28T00:00:00"})).json()
        page = (await client.get("/api/hindrances", params={"sort": "start_date", "order": "asc", "skip": 1, "limit": 1})).json()
        injected = await client.get("/api/hindrances", params={"sort": "organization_id"})

    assert everything["total"] == 3
    assert [row["title"] for row in everything["items"]] == ["Utility clash at P4", "Night-work ban", "Drawing approval pending"]
    assert [row["hindrance_ref"] for row in by_type["items"]] == ["CNS-0001"]
    assert [row["title"] for row in by_status["items"]] == ["Drawing approval pending"]
    assert by_responsibility["total"] == 2
    assert [row["title"] for row in searched["items"]] == ["Utility clash at P4"]
    assert [row["title"] for row in dated["items"]] == ["Night-work ban"]
    assert page["total"] == 3 and page["skip"] == 1 and page["limit"] == 1
    assert [row["title"] for row in page["items"]] == ["Night-work ban"]
    assert injected.status_code == 422


async def test_search_treats_the_query_as_text_not_a_pattern() -> None:
    db = _seeded()
    await _create(db, title="Access (gate 3) closed")
    await _create(db, title="Something else")

    async with _as(db, PROJECT_A1_ADMIN) as client:
        literal = (await client.get("/api/hindrances", params={"q": "(gate 3)"})).json()
        wildcard = (await client.get("/api/hindrances", params={"q": ".*"})).json()

    assert [row["title"] for row in literal["items"]] == ["Access (gate 3) closed"]
    assert wildcard["total"] == 0


async def test_get_and_edit_round_trip_with_audit() -> None:
    db = _seeded()
    created = await _create(db)

    async with _as(db, PROJECT_A1_ADMIN) as client:
        fetched = await client.get(f"/api/hindrances/{created['id']}")
        edited = await client.patch(
            f"/api/hindrances/{created['id']}",
            json={"title": "Site access blocked (revised)", "category": "statutory_approval", "claim_status": "notified"},
        )

    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["hindrance_ref"] == "HIN-0001"
    assert edited.status_code == 200, edited.text
    body = edited.json()
    assert body["title"] == "Site access blocked (revised)"
    assert body["category"] == "statutory_approval"
    assert body["claim_status"] == "notified"
    assert body["updated_by"] == PROJECT_A1_ADMIN.id
    [update_audit] = _audits(db, "delay_events.updated")
    assert update_audit["actor_id"] == PROJECT_A1_ADMIN.id
    assert update_audit["before"]["title"] == "Site access blocked at Station S2"
    assert update_audit["after"]["title"] == "Site access blocked (revised)"
    [create_audit] = _audits(db, "delay_events.created")
    assert create_audit["resource_id"] == created["id"]
    assert create_audit["project_id"] == "proj-A1"


async def test_operational_status_is_separate_from_claim_status() -> None:
    db = _seeded()

    async with _as(db, PROJECT_A1_ADMIN) as client:
        legacy_claim_state = await client.post("/api/hindrances", json=_payload(status="claimed"))
        resolved = await client.post(
            "/api/hindrances",
            json=_payload(status="resolved", claim_status="claimed", end_date="2026-02-12T00:00:00"),
        )

    assert legacy_claim_state.status_code == 422
    assert resolved.status_code == 201, resolved.text
    assert resolved.json()["status"] == "resolved"
    assert resolved.json()["claim_status"] == "claimed"


async def test_a_simple_constraint_needs_no_delay_assessment() -> None:
    db = _seeded()

    created = await _create(
        db,
        event_type="constraint",
        title="Monsoon working restriction",
        category="adverse_weather",
        responsibility="neutral",
    )

    assert created["critical_path_impact"] is False
    assert created["claimed_days"] is None
    assert created["assessed_days"] is None
    assert created["entitlement"] is None
    assert created["claim_status"] is None


# ---------------------------------------------------------------------------
# 5: archive / restore
# ---------------------------------------------------------------------------


async def test_archive_hides_from_the_default_list_blocks_edits_and_restores() -> None:
    db = _seeded()
    created = await _create(db)
    item = f"/api/hindrances/{created['id']}"

    async with _as(db, PROJECT_A1_ADMIN) as client:
        no_reason = await client.post(f"{item}/archive", json={})
        archived = await client.post(f"{item}/archive", json={"reason": "Raised in error"})
        default_list = (await client.get("/api/hindrances")).json()
        with_archived = (await client.get("/api/hindrances", params={"include_archived": "true"})).json()
        detail = await client.get(item)
        edit = await client.patch(item, json={"title": "Edited while archived"})
        again = await client.post(f"{item}/archive", json={"reason": "Twice"})
        restored = await client.post(f"{item}/restore", json={"reason": "Raised correctly after all"})
        after_restore = (await client.get("/api/hindrances")).json()

    assert no_reason.status_code == 422
    assert archived.status_code == 200, archived.text
    assert archived.json()["archived_at"] is not None
    assert archived.json()["archived_by"] == PROJECT_A1_ADMIN.id
    assert default_list["total"] == 0
    assert [row["id"] for row in with_archived["items"]] == [created["id"]]
    assert detail.status_code == 200 and detail.json()["archive_reason"] == "Raised in error"
    assert edit.status_code == 409
    assert again.status_code == 409
    assert restored.status_code == 200 and restored.json()["archived_at"] is None
    assert [row["id"] for row in after_restore["items"]] == [created["id"]]
    assert _stored(db, created["id"])["title"] == "Site access blocked at Station S2"
    [archive_audit] = _audits(db, "delay_events.archived")
    assert archive_audit["reason"] == "Raised in error"
    assert len(_audits(db, "delay_events.restored")) == 1
    assert len(db.delay_events.docs) == 1  # never hard-deleted


async def test_archive_requires_the_archive_permission() -> None:
    db = _seeded()
    created = await _create(db, user=EDITOR_A1)

    async with _as(db, EDITOR_A1) as client:
        refused = await client.post(f"/api/hindrances/{created['id']}/archive", json={"reason": "No"})

    assert refused.status_code == 403
    assert _stored(db, created["id"])["archived_at"] is None


# ---------------------------------------------------------------------------
# 6: explicit null clears, omission preserves
# ---------------------------------------------------------------------------


async def test_patch_distinguishes_an_omitted_field_from_an_explicit_null() -> None:
    db = _seeded()
    created = await _create(db, end_date="2026-02-20T00:00:00", cause="Police order")
    item = f"/api/hindrances/{created['id']}"

    async with _as(db, PROJECT_A1_ADMIN) as client:
        cleared = await client.patch(item, json={"location": None, "end_date": None})
        null_title = await client.patch(item, json={"title": None})
        null_type = await client.patch(item, json={"event_type": None})
        reversed_dates = await client.patch(item, json={"end_date": "2026-01-01T00:00:00"})

    assert cleared.status_code == 200, cleared.text
    stored = _stored(db, created["id"])
    assert stored["location"] is None
    assert stored["end_date"] is None
    assert stored["cause"] == "Police order"  # omitted, therefore untouched
    assert stored["description"] == "Police barricading prevented crane access."
    assert null_title.status_code == 422
    assert null_type.status_code == 422
    assert reversed_dates.status_code == 422
    assert stored["title"] == "Site access blocked at Station S2"


async def test_legacy_patch_also_clears_an_explicit_null() -> None:
    db = _seeded()
    created = await _create(db, event_type="delay_event", cause="Late drawings")

    async with _as(db, PROJECT_A1_ADMIN) as client:
        cleared = await client.patch(f"/api/delay-events/{created['id']}", json={"cause": None})
        null_title = await client.patch(f"/api/delay-events/{created['id']}", json={"title": None})

    assert cleared.status_code == 200, cleared.text
    assert _stored(db, created["id"])["cause"] is None
    assert null_title.status_code == 422


# ---------------------------------------------------------------------------
# 7-12: scope and refusals
# ---------------------------------------------------------------------------


async def test_project_user_list_is_bounded_to_their_assignment() -> None:
    db = _seeded()
    await _create(db, title="A1 record")
    await _create(db, user=PROJECT_A2_USER, project_id="proj-A2", title="A2 record")

    async with _as(db, PROJECT_A2_USER) as client:
        unselected = await client.get("/api/hindrances")
        own = await client.get("/api/hindrances", params={"project_id": "proj-A2"})
        foreign = await client.get("/api/hindrances", params={"project_id": "proj-A1"})
        legacy = await client.get("/api/delay-events")

    assert unselected.status_code == 200
    assert [row["title"] for row in unselected.json()["items"]] == ["A2 record"]
    assert [row["title"] for row in own.json()["items"]] == ["A2 record"]
    assert foreign.status_code == 403
    assert [row["title"] for row in legacy.json()] == ["A2 record"]


async def test_organisation_scope_isolates_tenants() -> None:
    db = _seeded()
    await _create(db, title="Org A record")

    async with _as(db, ORG_A_ADMIN) as client:
        org_a = (await client.get("/api/hindrances")).json()
    async with _as(db, ORG_B_ADMIN) as client:
        org_b = await client.get("/api/hindrances")
        org_b_asks_for_a = await client.get("/api/hindrances", params={"organization_id": "org-A"})
        org_b_asks_for_a_project = await client.get("/api/hindrances", params={"project_id": "proj-A1"})
    async with _as(db, SUPERADMIN) as client:
        platform = (await client.get("/api/hindrances")).json()

    assert [row["title"] for row in org_a["items"]] == ["Org A record"]
    assert org_b.status_code == 200 and org_b.json()["total"] == 0
    assert org_b_asks_for_a.status_code == 403
    assert org_b_asks_for_a_project.status_code == 403
    assert platform["total"] == 1


async def test_foreign_organisation_cannot_create_in_another_tenant() -> None:
    db = _seeded()

    async with _as(db, ORG_B_ADMIN) as client:
        implicit = await client.post("/api/hindrances", json=_payload())
        explicit = await client.post("/api/hindrances", json=_payload(organization_id="org-A"))

    assert implicit.status_code == 403
    assert explicit.status_code == 403
    assert db.delay_events.docs == []


async def test_foreign_project_cannot_be_written_by_a_project_user() -> None:
    db = _seeded()

    async with _as(db, PROJECT_A2_USER) as client:
        refused = await client.post("/api/hindrances", json=_payload(project_id="proj-A1"))
        legacy = await client.post(
            "/api/delay-events",
            json={"project_id": "proj-A1", "delay_ref": "D-1", "title": "x", "start_date": "2026-01-01T00:00:00"},
        )

    assert refused.status_code == 403
    assert legacy.status_code == 403
    assert db.delay_events.docs == []


async def test_foreign_detail_and_update_are_refused_on_both_apis() -> None:
    db = _seeded()
    created = await _create(db)

    for user in (ORG_B_ADMIN, PROJECT_A2_USER):
        async with _as(db, user) as client:
            detail = await client.get(f"/api/hindrances/{created['id']}")
            legacy_detail = await client.get(f"/api/delay-events/{created['id']}")
            update = await client.patch(f"/api/hindrances/{created['id']}", json={"title": "Hijacked"})
            legacy_update = await client.patch(f"/api/delay-events/{created['id']}", json={"title": "Hijacked"})
            archive = await client.post(f"/api/hindrances/{created['id']}/archive", json={"reason": "x"})
            history = await client.get(f"/api/hindrances/{created['id']}/history")
        assert (detail.status_code, legacy_detail.status_code) == (403, 403), user.id
        assert (update.status_code, legacy_update.status_code) == (403, 403), user.id
        assert archive.status_code == 403
        assert history.status_code == 403

    assert _stored(db, created["id"])["title"] == "Site access blocked at Station S2"
    assert _stored(db, created["id"])["archived_at"] is None


async def test_permissions_deny_by_default_and_view_is_read_only() -> None:
    db = _seeded()
    created = await _create(db)

    async with _as(db, NO_PERMISSION_A1) as client:
        denied_list = await client.get("/api/hindrances")
        denied_detail = await client.get(f"/api/hindrances/{created['id']}")
        denied_create = await client.post("/api/hindrances", json=_payload())
    async with _as(db, VIEWER_A1) as client:
        viewer_list = await client.get("/api/hindrances")
        viewer_create = await client.post("/api/hindrances", json=_payload())
        viewer_edit = await client.patch(f"/api/hindrances/{created['id']}", json={"title": "x"})

    assert (denied_list.status_code, denied_detail.status_code, denied_create.status_code) == (403, 403, 403)
    assert viewer_list.status_code == 200 and viewer_list.json()["total"] == 1
    assert (viewer_create.status_code, viewer_edit.status_code) == (403, 403)


async def test_evidence_graph_access_alone_no_longer_reaches_the_register() -> None:
    db = _seeded()
    GRANTS["u-graph-only"] = {"dms.evidence_graph.view", "dms.evidence_graph.manage"}
    graph_only = _principal("u-graph-only", ["projectadmin"], "org-A", ["proj-A1"])
    try:
        async with _as(db, graph_only) as client:
            legacy_create = await client.post(
                "/api/delay-events",
                json={"project_id": "proj-A1", "delay_ref": "D-1", "title": "x", "start_date": "2026-01-01T00:00:00"},
            )
            legacy_list = await client.get("/api/delay-events")
    finally:
        GRANTS.pop("u-graph-only", None)

    assert legacy_create.status_code == 403
    assert legacy_list.status_code == 403


async def test_evidence_graph_manage_is_not_a_write_bypass_on_the_compatibility_api() -> None:
    """Owner decision (hindrance staging certification): /api/delay-events stays on
    dms.hindrance.*. A principal that can read the register and holds
    dms.evidence_graph.manage, but neither dms.hindrance.create nor .edit, must get
    403 on compatibility create and update. The reads and the editor's PATCH are
    positive controls: they prove the requests reach authorization with valid bodies."""
    db = _seeded()
    created = await _create(db)
    GRANTS["u-graph-manager"] = {"dms.hindrance.view"} | {
        "dms.evidence_graph.view",
        "dms.evidence_graph.verify",
        "dms.evidence_graph.manage",
    }
    graph_manager = _principal("u-graph-manager", ["projectuser"], "org-A", ["proj-A1"])
    body = {"project_id": "proj-A1", "delay_ref": "D-2", "title": "x", "start_date": "2026-01-01T00:00:00"}
    try:
        async with _as(db, graph_manager) as client:
            listed = await client.get("/api/delay-events")
            fetched = await client.get(f"/api/delay-events/{created['id']}")
            legacy_create = await client.post("/api/delay-events", json=body)
            legacy_update = await client.patch(f"/api/delay-events/{created['id']}", json={"title": "bypass"})
        async with _as(db, EDITOR_A1) as client:
            control_update = await client.patch(f"/api/delay-events/{created['id']}", json={"title": "editor control"})
    finally:
        GRANTS.pop("u-graph-manager", None)

    assert (listed.status_code, fetched.status_code) == (200, 200)
    assert legacy_create.status_code == 403, legacy_create.text
    assert legacy_update.status_code == 403, legacy_update.text
    assert control_update.status_code == 200, control_update.text
    assert len(db.delay_events.docs) == 1
    assert _stored(db, created["id"])["title"] == "editor control"


async def test_superadmin_cannot_file_a_record_under_the_wrong_organisation() -> None:
    db = _seeded()

    # Since the active-scope decision (2026-09-22) the selection is checked first: a body
    # naming another organisation or project than the selected one never reaches the service.
    async with _as(db, SUPERADMIN, project="proj-B1") as client:
        mismatched = await client.post("/api/hindrances", json=_payload(project_id="proj-B1", organization_id="org-A"))
        missing_project = await client.post("/api/hindrances", json=_payload(project_id="proj-missing"))
        derived = await client.post("/api/hindrances", json=_payload(project_id="proj-B1"))
    async with _as(db, SUPERADMIN, project="proj-missing") as client:
        missing_selection = await client.post("/api/hindrances", json=_payload(project_id="proj-missing"))

    assert mismatched.status_code == 403 and mismatched.json()["detail"]["code"] == "context_forbidden"
    assert missing_project.status_code == 403
    assert missing_selection.status_code == 403
    assert derived.status_code == 201 and derived.json()["organization_id"] == "org-B"


# ---------------------------------------------------------------------------
# 13: document evidence through the canonical relationship service
# ---------------------------------------------------------------------------


async def test_documents_link_through_the_canonical_relationship_service() -> None:
    db = _seeded()
    created = await _create(db)
    target = f"/api/entities/delay_event/{created['id']}/document-links"

    async with _as(db, PROJECT_A1_ADMIN) as client:
        linked = await client.post(
            f"{target}:batch",
            json={"links": [{"document_id": "doc-A1", "relationship_role": "site_record"}]},
        )
        forward = (await client.get(target)).json()["links"]
        reverse = (await client.get("/api/documents/doc-A1/entity-links")).json()["links"]
        other_project = await client.post(f"{target}:batch", json={"links": [{"document_id": "doc-A2", "relationship_role": "site_record"}]})
        other_org = await client.post(f"{target}:batch", json={"links": [{"document_id": "doc-B1", "relationship_role": "site_record"}]})
        blocked = await client.post(f"{target}:batch", json={"links": [{"document_id": "doc-A1-blocked", "relationship_role": "site_record"}]})
        wrong_role = await client.post(f"{target}:batch", json={"links": [{"document_id": "doc-A1-second", "relationship_role": "ipc_submission"}]})

    assert linked.status_code == 201, linked.text
    assert [link["document_id"] for link in forward] == ["doc-A1"]
    assert forward[0]["target_label"] == "HIN-0001"
    [back] = [link for link in reverse if link["target_type"] == "delay_event"]
    assert back["target_id"] == created["id"]
    assert back["target_route"] == f"/hindrances/{created['id']}"
    assert other_project.status_code == 403
    assert other_org.status_code == 403
    assert blocked.status_code == 409
    assert wrong_role.status_code == 422
    assert len(_audits(db, "document_relationship.linked")) == 1


async def test_document_unlink_is_soft_and_foreign_actors_cannot_link() -> None:
    db = _seeded()
    created = await _create(db)
    target = f"/api/entities/delay_event/{created['id']}/document-links"
    async with _as(db, PROJECT_A1_ADMIN) as client:
        [link] = (await client.post(f"{target}:batch", json={"links": [{"document_id": "doc-A1", "relationship_role": "notice"}]})).json()["links"]

    async with _as(db, ORG_B_ADMIN) as client:
        foreign_read = await client.get(target)
        foreign_link = await client.post(f"{target}:batch", json={"links": [{"document_id": "doc-A1-second", "relationship_role": "notice"}]})
        foreign_unlink = await client.post(f"/api/document-links/{link['_id']}:remove", json={"reason": "x", "expected_revision": link["_revision"]})
    async with _as(db, VIEWER_A1) as client:
        viewer_link = await client.post(f"{target}:batch", json={"links": [{"document_id": "doc-A1-second", "relationship_role": "notice"}]})
    async with _as(db, PROJECT_A2_USER) as client:
        foreign_project_unlink = await client.post(f"/api/document-links/{link['_id']}:remove", json={"reason": "x", "expected_revision": link["_revision"]})
    async with _as(db, PROJECT_A1_ADMIN) as client:
        removed = await client.post(f"/api/document-links/{link['_id']}:remove", json={"reason": "Wrong notice", "expected_revision": link["_revision"]})
        after = (await client.get(target)).json()["links"]

    assert foreign_read.status_code == 403
    assert foreign_link.status_code == 403
    assert foreign_unlink.status_code == 403
    assert viewer_link.status_code == 403
    assert foreign_project_unlink.status_code == 403
    assert removed.status_code == 200, removed.text
    assert any(row["_id"] == "doc-A1" for row in db.documents.docs)  # only the relationship goes
    assert after == []
    assert db.entity_document_links.docs[0]["removed_at"] is not None  # history kept
    assert len(_audits(db, "document_relationship.unlinked")) == 1


async def test_archived_record_refuses_new_evidence() -> None:
    db = _seeded()
    created = await _create(db)
    async with _as(db, PROJECT_A1_ADMIN) as client:
        await client.post(
            f"/api/entities/delay_event/{created['id']}/document-links:batch",
            json={"links": [{"document_id": "doc-A1-second", "relationship_role": "site_record"}]},
        )
        await client.post(f"/api/hindrances/{created['id']}/links", json={"target_type": "key_date", "target_id": "kd-A1"})
        await client.post(f"/api/hindrances/{created['id']}/archive", json={"reason": "Closed out"})
        kept_documents = (await client.get(f"/api/entities/delay_event/{created['id']}/document-links")).json()["links"]
        kept_links = (await client.get(f"/api/hindrances/{created['id']}/links")).json()["links"]
        refused = await client.post(
            f"/api/entities/delay_event/{created['id']}/document-links:batch",
            json={"links": [{"document_id": "doc-A1", "relationship_role": "notice"}]},
        )
        activity = await client.post(f"/api/hindrances/{created['id']}/links", json={"target_type": "programme_milestone", "target_id": "act-A1"})

    assert refused.status_code == 409
    assert activity.status_code == 409
    # Archiving is non-destructive: existing evidence and relationships stay.
    assert [row["document_id"] for row in kept_documents] == ["doc-A1-second"]
    assert [row["target_id"] for row in kept_links] == ["kd-A1"]


async def test_legacy_linked_document_ids_are_served_through_the_canonical_read() -> None:
    db = _seeded()
    db.delay_events.docs.append(
        {
            "_id": "legacy-delay-1",
            "organization_id": "org-A",
            "project_id": "proj-A1",
            "delay_ref": "D-17",
            "title": "Legacy delay",
            "start_date": datetime(2025, 11, 1),
            "responsibility": "employer",
            "status": "claimed",
            "linked_document_ids": ["doc-A1", "doc-A1-blocked"],
        }
    )

    async with _as(db, PROJECT_A1_ADMIN) as client:
        forward = (await client.get("/api/entities/delay_event/legacy-delay-1/document-links")).json()["links"]
        reverse = (await client.get("/api/documents/doc-A1/entity-links")).json()["links"]

    assert [(link["document_id"], link["source"]) for link in forward] == [("doc-A1", "legacy_read_through")]
    assert [link["target_id"] for link in reverse if link["target_type"] == "delay_event"] == ["legacy-delay-1"]


# ---------------------------------------------------------------------------
# 14-15: activity / key-date / EOT links
# ---------------------------------------------------------------------------


async def test_activity_links_are_scoped_idempotent_reversible_and_audited() -> None:
    db = _seeded()
    created = await _create(db)
    links = f"/api/hindrances/{created['id']}/links"

    async with _as(db, PROJECT_A1_ADMIN) as client:
        linked = await client.post(links, json={"target_type": "programme_milestone", "target_id": "act-A1", "description": "Piling stood down"})
        repeated = await client.post(links, json={"target_type": "programme_milestone", "target_id": "act-A1"})
        other_project = await client.post(links, json={"target_type": "programme_milestone", "target_id": "act-A2"})
        other_org = await client.post(links, json={"target_type": "programme_milestone", "target_id": "act-B1"})
        missing = await client.post(links, json={"target_type": "programme_milestone", "target_id": "act-none"})
        unsupported = await client.post(links, json={"target_type": "claim", "target_id": "x"})
        listed = (await client.get(links)).json()
        reverse = (await client.get("/api/hindrances/affecting/programme_milestone/act-A1")).json()

    assert linked.status_code == 201, linked.text
    link = linked.json()
    assert link["relationship_role"] == "affects_activity"
    assert link["target"]["label"] == "ACT-110"
    assert repeated.status_code == 200 and repeated.json()["id"] == link["id"]
    assert other_project.status_code == 403
    assert other_org.status_code == 403
    assert missing.status_code == 404
    assert unsupported.status_code == 422
    assert [row["target_id"] for row in listed["links"]] == ["act-A1"]
    assert [row["hindrance"]["id"] for row in reverse["items"]] == [created["id"]]
    assert len(_audits(db, "delay_event_links.linked")) == 1

    async with _as(db, PROJECT_A1_ADMIN) as client:
        removed = await client.post(f"{links}/{link['id']}/remove", json={"reason": "Not affected"})
        removed_again = await client.post(f"{links}/{link['id']}/remove", json={"reason": "Twice"})
        after = (await client.get(links)).json()
        reverse_after = (await client.get("/api/hindrances/affecting/programme_milestone/act-A1")).json()

    assert removed.status_code == 200 and removed.json()["removed_at"] is not None
    assert removed_again.status_code == 409
    assert after["links"] == []
    assert reverse_after["items"] == []
    assert len(_audits(db, "delay_event_links.unlinked")) == 1
    assert len(db.delay_event_links.docs) == 1  # soft removal keeps history


async def test_linking_requires_edit_on_the_record_and_view_on_the_target() -> None:
    db = _seeded()
    created = await _create(db)
    links = f"/api/hindrances/{created['id']}/links"

    async with _as(db, VIEWER_A1) as client:
        viewer = await client.post(links, json={"target_type": "key_date", "target_id": "kd-A1"})
        viewer_list = await client.get(links)
    async with _as(db, EDITOR_A1) as client:  # can edit hindrances, cannot view key dates
        blind = await client.post(links, json={"target_type": "key_date", "target_id": "kd-A1"})
    async with _as(db, ORG_B_ADMIN) as client:
        foreign = await client.post(links, json={"target_type": "key_date", "target_id": "kd-A1"})
        foreign_list = await client.get(links)
        foreign_reverse = await client.get("/api/hindrances/affecting/key_date/kd-A1")

    async with _as(db, ORG_A_ADMIN) as client:
        # Same organisation, other project: the org admin may VIEW the target,
        # but a relationship never crosses the entry's own project.
        cross_project = await client.post(links, json={"target_type": "key_date", "target_id": "kd-A2"})
        cross_project_activity = await client.post(links, json={"target_type": "programme_milestone", "target_id": "act-A2"})

    assert cross_project.status_code == 403
    assert cross_project_activity.status_code == 403
    assert viewer.status_code == 403
    assert viewer_list.status_code == 200
    assert blind.status_code == 403
    assert foreign.status_code == 403
    assert foreign_list.status_code == 403
    assert foreign_reverse.status_code == 403
    assert db.delay_event_links.docs == []


async def test_key_date_and_eot_links_with_reverse_lookup_and_missing_targets() -> None:
    db = _seeded()
    created = await _create(db)
    links = f"/api/hindrances/{created['id']}/links"

    async with _as(db, PROJECT_A1_ADMIN) as client:
        key_date = await client.post(links, json={"target_type": "key_date", "target_id": "kd-A1"})
        foreign_key_date = await client.post(links, json={"target_type": "key_date", "target_id": "kd-B1"})
        eot = await client.post(links, json={"target_type": "eot_submission", "target_id": "eot-A1"})
        foreign_eot = await client.post(links, json={"target_type": "eot_submission", "target_id": "eot-B1"})
        reverse = (await client.get("/api/hindrances/affecting/key_date/kd-A1")).json()
        eot_reverse = (await client.get("/api/hindrances/affecting/eot_submission/eot-A1")).json()

    assert key_date.status_code == 201, key_date.text
    assert key_date.json()["relationship_role"] == "impacts_key_date"
    assert key_date.json()["target"]["label"] == "KD-03"
    assert key_date.json()["target"]["route"] == "/key-dates/kd-A1"
    assert foreign_key_date.status_code == 403
    assert eot.status_code == 201 and eot.json()["relationship_role"] == "supports_eot_submission"
    assert foreign_eot.status_code == 403
    assert [row["hindrance"]["hindrance_ref"] for row in reverse["items"]] == ["HIN-0001"]
    assert [row["hindrance"]["id"] for row in eot_reverse["items"]] == [created["id"]]

    db.key_date_milestones.docs[:] = [row for row in db.key_date_milestones.docs if row["_id"] != "kd-A1"]
    async with _as(db, PROJECT_A1_ADMIN) as client:
        after_delete = (await client.get(links)).json()["links"]

    by_target = {row["target_id"]: row for row in after_delete}
    assert by_target["kd-A1"]["target_available"] is False
    assert by_target["kd-A1"]["target"] is None
    assert by_target["eot-A1"]["target_available"] is True


# ---------------------------------------------------------------------------
# 17: audit history
# ---------------------------------------------------------------------------


async def test_history_lists_the_records_own_audit_trail() -> None:
    db = _seeded()
    created = await _create(db)
    item = f"/api/hindrances/{created['id']}"
    other = await _create(db, title="Unrelated")

    async with _as(db, PROJECT_A1_ADMIN) as client:
        await client.patch(item, json={"title": "Revised"})
        await client.post(f"{item}/links", json={"target_type": "key_date", "target_id": "kd-A1"})
        await client.post(f"{item}/archive", json={"reason": "Superseded by HIN-0002"})
        history = await client.get(f"{item}/history")

    assert history.status_code == 200, history.text
    entries = history.json()["entries"]
    actions = [entry["action"] for entry in entries]
    assert set(actions) >= {"delay_events.created", "delay_events.updated", "delay_event_links.linked", "delay_events.archived"}
    assert all(entry.get("resource_id") != other["id"] for entry in entries)
    update = next(entry for entry in entries if entry["action"] == "delay_events.updated")
    assert "title" in update["changed_fields"]
    assert "updated_at" not in update["changed_fields"]
    assert "before" not in update and "after" not in update  # history is a summary, not a data dump


# ---------------------------------------------------------------------------
# 18: timeline propagation is fail-visible and never duplicated
# ---------------------------------------------------------------------------


async def test_timeline_failure_is_recorded_not_swallowed_and_can_be_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    db = _seeded()
    real_create = EvidenceGraphService.create_project_event

    async def _graph_down(self, payload, current_user):
        raise RuntimeError("graph store unavailable")

    monkeypatch.setattr(EvidenceGraphService, "create_project_event", _graph_down)
    created = await _create(db)

    assert created["timeline_sync_status"] == "failed"
    assert "RuntimeError" in created["timeline_sync_error"]
    assert created["timeline_event_id"] is None
    assert db.project_events.docs == []
    [failure] = _audits(db, "delay_events.timeline_sync_failed")
    assert failure["result"] == "failure"
    assert failure["resource_id"] == created["id"]

    async with _as(db, PROJECT_A1_ADMIN) as client:
        failed_only = (await client.get("/api/hindrances", params={"timeline_sync_status": "failed"})).json()
    assert [row["id"] for row in failed_only["items"]] == [created["id"]]

    monkeypatch.setattr(EvidenceGraphService, "create_project_event", real_create)
    async with _as(db, PROJECT_A1_ADMIN) as client:
        retried = await client.post(f"/api/hindrances/{created['id']}/timeline-sync")
        retried_again = await client.post(f"/api/hindrances/{created['id']}/timeline-sync")
    async with _as(db, VIEWER_A1) as client:
        viewer_retry = await client.post(f"/api/hindrances/{created['id']}/timeline-sync")

    assert retried.status_code == 200, retried.text
    assert retried.json()["timeline_sync_status"] == "synced"
    assert retried.json()["timeline_sync_error"] is None
    assert retried_again.status_code == 200
    assert len(db.project_events.docs) == 1
    assert retried.json()["timeline_event_id"] == db.project_events.docs[0]["_id"]
    assert viewer_retry.status_code == 403


async def test_timeline_mapping_is_deterministic_and_updates_in_place() -> None:
    db = _seeded()
    hindrance = await _create(db)
    constraint = await _create(db, event_type="constraint", title="Night-work restriction", responsibility="neutral")
    delay = await _create(db, event_type="delay_event", title="Late GFC drawings", responsibility="employer")

    events = {row["source_entity_id"]: row for row in db.project_events.docs}
    assert {row["source_entity_type"] for row in events.values()} == {"delay_event"}
    assert events[hindrance["id"]]["event_type"] == "hindrance"
    assert events[constraint["id"]]["event_type"] == "constraint"
    assert events[delay["id"]]["event_type"] == "delay"
    assert events[hindrance["id"]]["title"] == "HIN-0001 - Site access blocked at Station S2"
    assert events[delay["id"]]["metadata"]["delay_responsibility"] == "employer"
    assert events[hindrance["id"]]["metadata"]["register_event_type"] == "hindrance"
    relations = {row["target_id"]: row["relation_type"] for row in db.event_links.docs}
    assert relations[delay["id"]] == "causes_delay"
    assert relations[hindrance["id"]] == "affects"
    assert relations[constraint["id"]] == "affects"

    async with _as(db, PROJECT_A1_ADMIN) as client:
        edited = await client.patch(
            f"/api/hindrances/{hindrance['id']}",
            json={"title": "Site access blocked (police order)", "end_date": "2026-02-14T00:00:00", "status": "resolved"},
        )
        await client.post(f"/api/hindrances/{hindrance['id']}/archive", json={"reason": "Closed"})

    assert edited.status_code == 200 and edited.json()["timeline_sync_status"] == "synced"
    refreshed = next(row for row in db.project_events.docs if row["source_entity_id"] == hindrance["id"])
    assert len(db.project_events.docs) == 3  # updated in place, never duplicated
    assert refreshed["title"] == "HIN-0001 - Site access blocked (police order)"
    assert refreshed["event_end_date"] == datetime(2026, 2, 14)
    assert refreshed["status"] == "closed"
    assert refreshed["metadata"]["archived"] is True


# ---------------------------------------------------------------------------
# Compatibility with records written before this register existed
# ---------------------------------------------------------------------------


async def test_legacy_rows_without_new_fields_remain_readable_on_both_apis() -> None:
    db = _seeded()
    db.delay_events.docs.append(
        {
            "_id": "legacy-delay-2",
            "organization_id": "org-A",
            "project_id": "proj-A1",
            "delay_ref": "D-7",
            "title": "Legacy delay without a type",
            "start_date": datetime(2025, 10, 1),
            "responsibility": "contractor",
            "status": "claimed",
            "linked_document_ids": [],
            "evidence_link_ids": [],
            "metadata": {},
            "created_at": datetime(2025, 10, 2),
        }
    )

    async with _as(db, PROJECT_A1_ADMIN) as client:
        canonical = await client.get("/api/hindrances/legacy-delay-2")
        legacy = await client.get("/api/delay-events/legacy-delay-2")
        typed = (await client.get("/api/hindrances", params={"event_type": "delay_event"})).json()
        hindrances_only = (await client.get("/api/hindrances", params={"event_type": "hindrance"})).json()

    assert canonical.status_code == 200, canonical.text
    body = canonical.json()
    assert body["event_type"] == "delay_event"
    assert body["hindrance_ref"] is None
    assert body["delay_ref"] == "D-7"
    assert body["status"] == "claimed"
    assert legacy.status_code == 200
    assert [row["id"] for row in typed["items"]] == ["legacy-delay-2"]
    assert hindrances_only["total"] == 0
    assert "event_type" not in _stored(db, "legacy-delay-2")  # read-time default, no migration write


async def test_legacy_delay_events_api_keeps_working_on_the_same_service() -> None:
    db = _seeded()

    async with _as(db, PROJECT_A1_ADMIN) as client:
        created = await client.post(
            "/api/delay-events",
            json={"project_id": "proj-A1", "delay_ref": "D-100", "title": "Late drawings", "start_date": "2026-01-01T00:00:00", "responsibility": "employer"},
        )
        listed = await client.get("/api/delay-events")
        canonical = await client.get(f"/api/hindrances/{created.json()['_id']}")

    assert created.status_code == 201, created.text
    # The compatibility API keeps its historical `_id` serialisation.
    assert created.json()["event_type"] == "delay_event"
    assert created.json()["hindrance_ref"] == "DLY-0001"
    assert created.json()["delay_ref"] == "D-100"
    assert [row["_id"] for row in listed.json()] == [created.json()["_id"]]
    assert canonical.status_code == 200
    assert len(_audits(db, "delay_events.created")) == 1


async def test_backfill_projects_register_rows_with_the_register_mapping() -> None:
    from rbac_backend.services.evidence_graph_backfill_service import EvidenceGraphBackfillService

    db = _seeded()
    db.delay_events.docs.extend(
        [
            {"_id": "h-no-event", "organization_id": "org-A", "project_id": "proj-A1", "event_type": "constraint", "hindrance_ref": "CNS-0009", "title": "Unprojected constraint", "start_date": datetime(2026, 1, 1)},
            {"_id": "legacy-no-event", "organization_id": "org-A", "project_id": "proj-A1", "delay_ref": "D-3", "title": "Unprojected legacy delay", "start_date": datetime(2025, 1, 1)},
        ]
    )

    await EvidenceGraphBackfillService(db).run({"organization_id": "org-A"}, current_user=ORG_A_ADMIN, dry_run=False)

    events = {row["source_entity_id"]: row["event_type"] for row in db.project_events.docs if row.get("source_entity_type") == "delay_event"}
    assert events == {"h-no-event": "constraint", "legacy-no-event": "delay"}


# ---------------------------------------------------------------------------
# Owner decision 2026-09-22: the selected navbar project is an authorization
# boundary. EffectiveScope = Entitlement ∩ Navbar Selection. A member of BOTH
# projects is the subject throughout: membership alone must not be enough.
# ---------------------------------------------------------------------------

PROJECT_AB_ADMIN = _principal("u-projadmin-ab", ["projectadmin"], "org-A", ["proj-A1", "proj-A2"])
GRANTS[PROJECT_AB_ADMIN.id] = HINDRANCE_ALL | READ_TARGETS


async def _two_projects(db: _Database) -> tuple[dict[str, Any], dict[str, Any]]:
    a1 = await _create(db, PROJECT_AB_ADMIN, project_id="proj-A1", title="A1 entry")
    a2 = await _create(db, PROJECT_AB_ADMIN, project_id="proj-A2", title="A2 entry")
    return a1, a2


def _code(response: httpx.Response) -> str | None:
    detail = response.json().get("detail")
    return detail.get("code") if isinstance(detail, dict) else None


def _refused(response: httpx.Response) -> bool:
    return response.status_code == 403 and _code(response) == "context_forbidden"


def _selection_required(response: httpx.Response) -> bool:
    return response.status_code == 400 and _code(response) == "selection_required"


async def test_scope_list_follows_the_selected_project_and_never_goes_global() -> None:
    db = _seeded()
    a1, a2 = await _two_projects(db)
    b1 = await _create(db, ORG_B_ADMIN, project_id="proj-B1", title="B1 entry")

    async with _as(db, PROJECT_AB_ADMIN, project="proj-A1") as client:
        selected_a1 = (await client.get("/api/hindrances")).json()["items"]
        filter_mismatch = await client.get("/api/hindrances", params={"project_id": "proj-A2"})
    async with _as(db, PROJECT_AB_ADMIN, project="proj-A2") as client:
        selected_a2 = (await client.get("/api/hindrances")).json()["items"]
    async with _as(db, PROJECT_AB_ADMIN, project=None) as client:
        unselected = (await client.get("/api/hindrances")).json()["items"]

    assert [row["id"] for row in selected_a1] == [a1["id"]]
    assert [row["id"] for row in selected_a2] == [a2["id"]]
    assert {row["id"] for row in unselected} == {a1["id"], a2["id"]}
    assert b1["id"] not in {row["id"] for row in unselected}
    assert _refused(filter_mismatch), "a query filter widened the selected scope"


async def test_scope_detail_requires_the_selected_project() -> None:
    db = _seeded()
    a1, _ = await _two_projects(db)
    async with _as(db, PROJECT_AB_ADMIN, project="proj-A1") as client:
        allowed = await client.get(f"/api/hindrances/{a1['id']}")
    async with _as(db, PROJECT_AB_ADMIN, project="proj-A2") as client:
        mismatched = await client.get(f"/api/hindrances/{a1['id']}")
    async with _as(db, PROJECT_AB_ADMIN, project=None) as client:
        unselected = await client.get(f"/api/hindrances/{a1['id']}")

    assert allowed.status_code == 200
    assert _refused(mismatched), mismatched.text
    assert "proj-A1" not in mismatched.text, "the refusal disclosed the record's project"
    assert _selection_required(unselected), unselected.text
    [audit] = _audits(db, "tenant.context.rejected")
    assert audit["project_id"] == "proj-A1", "the audit must keep the attempted target"


async def test_scope_create_never_rewrites_the_project() -> None:
    db = _seeded()
    async with _as(db, PROJECT_AB_ADMIN, project="proj-A1") as client:
        allowed = await client.post("/api/hindrances", json=_payload(project_id="proj-A1"))
    async with _as(db, PROJECT_AB_ADMIN, project="proj-A2") as client:
        mismatched = await client.post("/api/hindrances", json=_payload(project_id="proj-A1"))
    async with _as(db, PROJECT_AB_ADMIN, project=None) as client:
        unselected = await client.post("/api/hindrances", json=_payload(project_id="proj-A1"))

    assert allowed.status_code == 201 and allowed.json()["project_id"] == "proj-A1"
    assert _refused(mismatched), mismatched.text
    assert _selection_required(unselected), unselected.text
    assert len(db.delay_events.docs) == 1


async def test_scope_every_record_operation_is_refused_under_another_selection() -> None:
    db = _seeded()
    a1, _ = await _two_projects(db)
    archived = await _create(db, PROJECT_AB_ADMIN, project_id="proj-A1", title="archived A1")
    async with _as(db, PROJECT_AB_ADMIN, project="proj-A1") as client:
        assert (await client.post(f"/api/hindrances/{archived['id']}/archive", json={"reason": "x"})).status_code == 200

    base = f"/api/hindrances/{a1['id']}"
    document_batch = f"/api/entities/delay_event/{a1['id']}/document-links:batch"
    document_body = {"links": [{"document_id": "doc-A1", "relationship_role": "site_record"}]}
    async with _as(db, PROJECT_AB_ADMIN, project="proj-A2") as client:
        responses = {
            "get": await client.get(base),
            "patch": await client.patch(base, json={"title": "moved"}),
            "archive": await client.post(f"{base}/archive", json={"reason": "x"}),
            "restore": await client.post(f"/api/hindrances/{archived['id']}/restore", json={"reason": "x"}),
            "timeline_sync": await client.post(f"{base}/timeline-sync"),
            "history": await client.get(f"{base}/history"),
            "links": await client.get(f"{base}/links"),
            "link_milestone": await client.post(f"{base}/links", json={"target_type": "programme_milestone", "target_id": "act-A1"}),
            "link_key_date": await client.post(f"{base}/links", json={"target_type": "key_date", "target_id": "kd-A1"}),
            "link_eot": await client.post(f"{base}/links", json={"target_type": "eot_submission", "target_id": "eot-A1"}),
            "document_link": await client.post(document_batch, json=document_body),
            "document_links": await client.get(f"/api/entities/delay_event/{a1['id']}/document-links"),
        }
    async with _as(db, PROJECT_AB_ADMIN, project=None) as client:
        unselected = {
            "patch": await client.patch(base, json={"title": "moved"}),
            "archive": await client.post(f"{base}/archive", json={"reason": "x"}),
            "timeline_sync": await client.post(f"{base}/timeline-sync"),
            "history": await client.get(f"{base}/history"),
            "link": await client.post(f"{base}/links", json={"target_type": "key_date", "target_id": "kd-A1"}),
            "document_link": await client.post(document_batch, json=document_body),
        }

    for operation, response in responses.items():
        assert _refused(response), f"{operation}: {response.status_code} {response.text}"
    for operation, response in unselected.items():
        assert _selection_required(response), f"{operation} without selection: {response.status_code} {response.text}"
    assert _stored(db, a1["id"])["title"] == "A1 entry"
    assert _stored(db, a1["id"])["archived_at"] is None
    assert db.delay_event_links.docs == [] and db.entity_document_links.docs == []


async def test_scope_unlink_by_link_id_is_bound_by_the_selection() -> None:
    db = _seeded()
    a1, _ = await _two_projects(db)
    async with _as(db, PROJECT_AB_ADMIN, project="proj-A1") as client:
        relationship = (await client.post(f"/api/hindrances/{a1['id']}/links", json={"target_type": "key_date", "target_id": "kd-A1"})).json()
        linked = await client.post(
            f"/api/entities/delay_event/{a1['id']}/document-links:batch",
            json={"links": [{"document_id": "doc-A1", "relationship_role": "site_record"}]},
        )
    link_id = db.entity_document_links.docs[0]["_id"]
    assert linked.status_code == 201, linked.text
    async with _as(db, PROJECT_AB_ADMIN, project="proj-A2") as client:
        unlink = await client.post(f"/api/hindrances/{a1['id']}/links/{relationship['id']}/remove", json={"reason": "x"})
        document_unlink = await client.post(f"/api/document-links/{link_id}:remove", json={"reason": "x", "expected_revision": 1})
        document_history = await client.get(f"/api/document-links/{link_id}/history")

    assert _refused(unlink), unlink.text
    assert _refused(document_unlink), document_unlink.text
    assert _refused(document_history), document_history.text
    assert db.delay_event_links.docs[0]["removed_at"] is None
    assert db.entity_document_links.docs[0]["removed_at"] is None


async def test_scope_link_targets_must_be_in_the_selected_project() -> None:
    db = _seeded()
    a1, _ = await _two_projects(db)
    base = f"/api/hindrances/{a1['id']}/links"
    async with _as(db, PROJECT_AB_ADMIN, project="proj-A1") as client:
        foreign = {
            "milestone": await client.post(base, json={"target_type": "programme_milestone", "target_id": "act-A2"}),
            "key_date": await client.post(base, json={"target_type": "key_date", "target_id": "kd-A2"}),
            "document": await client.post(
                f"/api/entities/delay_event/{a1['id']}/document-links:batch",
                json={"links": [{"document_id": "doc-A2", "relationship_role": "site_record"}]},
            ),
        }
        same = await client.post(base, json={"target_type": "eot_submission", "target_id": "eot-A1"})

    for name, response in foreign.items():
        assert response.status_code == 403, f"{name}: {response.status_code} {response.text}"
    assert same.status_code == 201, same.text


async def test_scope_non_member_is_refused_whatever_it_selects() -> None:
    db = _seeded()
    a1, _ = await _two_projects(db)
    async with _as(db, PROJECT_A2_USER, project="proj-A1") as client:
        selected_foreign = await client.get(f"/api/hindrances/{a1['id']}")
        listed = await client.get("/api/hindrances")
    async with _as(db, PROJECT_A2_USER, project="proj-A2") as client:
        own_selection = await client.get(f"/api/hindrances/{a1['id']}")

    assert _refused(selected_foreign), selected_foreign.text
    assert _refused(listed), "an unreachable selection was accepted for a listing"
    assert own_selection.status_code == 403


async def test_scope_superadmin_is_bound_by_an_explicit_selection() -> None:
    db = _seeded()
    a1, _ = await _two_projects(db)
    async with _as(db, SUPERADMIN, project="proj-A2") as client:
        mismatched = await client.get(f"/api/hindrances/{a1['id']}")
        mismatched_patch = await client.patch(f"/api/hindrances/{a1['id']}", json={"title": "x"})
    async with _as(db, SUPERADMIN, project=None) as client:
        unselected = await client.get(f"/api/hindrances/{a1['id']}")
        listed = await client.get("/api/hindrances")
    async with _as(db, SUPERADMIN, project="proj-A1") as client:
        matched = await client.get(f"/api/hindrances/{a1['id']}")

    assert _refused(mismatched) and _refused(mismatched_patch)
    assert _selection_required(unselected), unselected.text
    assert listed.status_code == 200 and listed.json()["total"] == 2
    assert matched.status_code == 200


async def test_scope_compatibility_api_is_not_a_bypass() -> None:
    db = _seeded()
    a1, a2 = await _two_projects(db)
    async with _as(db, PROJECT_AB_ADMIN, project="proj-A2") as client:
        detail = await client.get(f"/api/delay-events/{a1['id']}")
        patch = await client.patch(f"/api/delay-events/{a1['id']}", json={"title": "moved"})
        create = await client.post(
            "/api/delay-events",
            json={"project_id": "proj-A1", "delay_ref": "D-9", "title": "x", "start_date": "2026-01-01T00:00:00"},
        )
        listed = await client.get("/api/delay-events")
    async with _as(db, PROJECT_AB_ADMIN, project=None) as client:
        unselected_detail = await client.get(f"/api/delay-events/{a1['id']}")
        unselected_patch = await client.patch(f"/api/delay-events/{a1['id']}", json={"title": "moved"})
    async with _as(db, PROJECT_AB_ADMIN, project="proj-A1") as client:
        matched = await client.get(f"/api/delay-events/{a1['id']}")

    assert _refused(detail) and _refused(patch) and _refused(create)
    assert [row["_id"] for row in listed.json()] == [a2["id"]]
    assert _selection_required(unselected_detail) and _selection_required(unselected_patch)
    assert matched.status_code == 200
    assert _stored(db, a1["id"])["title"] == "A1 entry"


async def test_scope_reverse_lookups_hide_other_projects_hindrances() -> None:
    db = _seeded()
    a1, _ = await _two_projects(db)
    async with _as(db, PROJECT_AB_ADMIN, project="proj-A1") as client:
        await client.post(
            f"/api/entities/delay_event/{a1['id']}/document-links:batch",
            json={"links": [{"document_id": "doc-A1", "relationship_role": "site_record"}]},
        )
        await client.post(f"/api/hindrances/{a1['id']}/links", json={"target_type": "key_date", "target_id": "kd-A1"})
    # An EOT submission relationship on the same document. It was the selection-blind
    # control here until CL-4A bound every core target; it is now held exactly like the
    # Hindrance row. The selection-blind control is a synthetic adapter that does not opt
    # in (test_active_scope_variation_hindrance_http.py, CL-3B real-Mongo suite).
    db.key_date_baselines.docs.append(
        {"_id": "bl-A1", "organization_id": "org-A", "project_id": "proj-A1", "contract_id": "primary", "status": "frozen"}
    )
    db.entity_document_links.docs.append(
        {
            **deepcopy(db.entity_document_links.docs[0]),
            "_id": "edl-eot",
            "target_type": "eot_submission",
            "target_id": "eot-A1",
            "relationship_role": "supporting_document",
        }
    )

    async def reverse(project: Any) -> tuple[list[str], httpx.Response]:
        async with _as(db, PROJECT_AB_ADMIN, project=project) as client:
            links = (await client.get("/api/documents/doc-A1/entity-links")).json()["links"]
            affecting = await client.get("/api/hindrances/affecting/key_date/kd-A1")
        return sorted(link["target_type"] for link in links), affecting

    selected_a1, affecting_a1 = await reverse("proj-A1")
    selected_a2, affecting_a2 = await reverse("proj-A2")
    unselected, _ = await reverse(None)

    assert "delay_event" in selected_a1
    assert "delay_event" not in selected_a2, "Hindrance A1 leaked through the document viewer under A2"
    assert "delay_event" in unselected
    assert "eot_submission" in selected_a1, f"EOT row missing: {selected_a1}"
    assert "eot_submission" in unselected
    assert "eot_submission" not in selected_a2, "EOT A1 leaked through the document viewer under A2"
    assert affecting_a1.status_code == 200 and len(affecting_a1.json()["items"]) == 1
    assert _refused(affecting_a2), affecting_a2.text

    async with _as(db, ORG_B_ADMIN, project="proj-B1") as client:
        foreign = await client.get("/api/documents/doc-A1/entity-links")
    assert foreign.status_code == 403
