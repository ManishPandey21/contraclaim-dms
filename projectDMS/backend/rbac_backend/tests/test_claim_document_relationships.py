from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pymongo.errors import DuplicateKeyError

from rbac_backend.core.database import get_db
from rbac_backend.core.database import ensure_indexes
from rbac_backend.core.security import CurrentUser, get_current_user
from rbac_backend.routers.document_relationships import router as relationship_router
from rbac_backend.routers.document_relationships import get_document_relationship_service
from rbac_backend.routers.claims import router as claims_router
from rbac_backend.core.permissions import Permissions
from rbac_backend.services.document_relationship_service import DocumentRelationshipService
from rbac_backend.tests.selection_fixtures import pin_selection


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    def _field_matches(value: Any, condition: Any) -> bool:
        if not isinstance(condition, dict):
            if isinstance(value, list):
                return condition in value
            return value == condition
        if "$in" in condition and value not in condition["$in"]:
            return False
        if "$nin" in condition and value in condition["$nin"]:
            return False
        if "$ne" in condition and value == condition["$ne"]:
            return False
        if "$exists" in condition and (value is not None) != bool(condition["$exists"]):
            return False
        if "$regex" in condition:
            import re as _re

            flags = _re.IGNORECASE if "i" in str(condition.get("$options") or "") else 0
            if not isinstance(value, str) or not _re.search(condition["$regex"], value, flags):
                return False
        # Range operators. Mongo never matches a field it cannot compare, so a
        # missing value fails rather than passing.
        for operator, compare in (
            ("$lt", lambda a, b: a < b),
            ("$lte", lambda a, b: a <= b),
            ("$gt", lambda a, b: a > b),
            ("$gte", lambda a, b: a >= b),
        ):
            if operator in condition:
                if value is None:
                    return False
                try:
                    if not compare(value, condition[operator]):
                        return False
                except TypeError:
                    return False
        # Fail closed on anything unimplemented. Silently returning True here
        # made lease-expiry queries (`lease_expires_at: {"$lte": now}`) match
        # unconditionally, so every test that depended on one was vacuous.
        unsupported = set(condition) - {
            "$in", "$nin", "$ne", "$exists", "$lt", "$lte", "$gt", "$gte",
            "$regex", "$options",
        }
        if unsupported:
            raise NotImplementedError(
                f"_matches does not implement {sorted(unsupported)}; implement it "
                "rather than letting the query match everything"
            )
        return True

    for key, condition in query.items():
        if key == "$or":
            if not any(_matches(document, candidate) for candidate in condition):
                return False
            continue
        if key == "$and":
            if not all(_matches(document, candidate) for candidate in condition):
                return False
            continue
        if not _field_matches(document.get(key), condition):
            return False
    return True


class _Cursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self.documents = documents

    def sort(self, field: str, direction: int = 1):
        self.documents.sort(key=lambda item: item.get(field), reverse=direction < 0)
        return self

    def skip(self, count: int):
        self.documents = self.documents[count:]
        return self

    def limit(self, count: int):
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
    def __init__(self, name: str, documents: list[dict[str, Any]] | None = None) -> None:
        self.name = name
        self.documents = [deepcopy(document) for document in documents or []]
        self.sessions: list[Any] = []

    async def find_one(
        self,
        query: dict[str, Any],
        *args: Any,
        sort: list[tuple[str, int]] | None = None,
        **kwargs: Any,
    ):
        matches = [item for item in self.documents if _matches(item, query)]
        if sort:
            for field, direction in reversed(sort):
                matches.sort(key=lambda item: item.get(field), reverse=direction < 0)
        return deepcopy(matches[0]) if matches else None

    def find(self, query: dict[str, Any], *args: Any, **kwargs: Any):
        return _Cursor([deepcopy(item) for item in self.documents if _matches(item, query)])

    async def count_documents(self, query: dict[str, Any], *args: Any, **kwargs: Any) -> int:
        return len([item for item in self.documents if _matches(item, query)])

    def _require_unique_id(self, identifier: Any) -> None:
        """Every Mongo collection has a unique `_id` index.

        Omitting this made any design that fences on `_id` — the legacy backfill
        candidate claim, for one — untestable at unit level: the second write
        simply succeeded. It must hold on BOTH insert paths, or an upsert
        quietly reintroduces the hole.
        """
        if any(row.get("_id") == identifier for row in self.documents):
            raise DuplicateKeyError(f"duplicate _id in {self.name}")

    async def insert_one(self, document: dict[str, Any], *args: Any, **kwargs: Any):
        self.sessions.append(kwargs.get("session"))
        stored = deepcopy(document)
        stored.setdefault("_id", f"{self.name}-{len(self.documents) + 1}")
        self._require_unique_id(stored["_id"])
        if self.name == "entity_document_links" and stored.get("removed_at") is None:
            identity = (
                stored.get("organization_id"),
                stored.get("project_id"),
                stored.get("target_type"),
                stored.get("target_id"),
                stored.get("document_id"),
                stored.get("relationship_role"),
            )
            for current in self.documents:
                current_identity = (
                    current.get("organization_id"),
                    current.get("project_id"),
                    current.get("target_type"),
                    current.get("target_id"),
                    current.get("document_id"),
                    current.get("relationship_role"),
                )
                if current.get("removed_at") is None and current_identity == identity:
                    raise DuplicateKeyError("duplicate active relationship")
        self.documents.append(stored)
        return SimpleNamespace(inserted_id=stored["_id"])

    async def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *args: Any,
        upsert: bool = False,
        **kwargs: Any,
    ):
        self.sessions.append(kwargs.get("session"))
        for item in self.documents:
            if not _matches(item, query):
                continue
            item.update(deepcopy(update.get("$set", {})))
            for field in update.get("$unset", {}):
                item.pop(field, None)
            for field, amount in update.get("$inc", {}).items():
                item[field] = int(item.get(field) or 0) + int(amount)
            return SimpleNamespace(matched_count=1, modified_count=1)
        if upsert:
            inserted = {key: value for key, value in query.items() if not key.startswith("$")}
            inserted.update(deepcopy(update.get("$setOnInsert", {})))
            inserted.update(deepcopy(update.get("$set", {})))
            inserted.setdefault("_id", f"{self.name}-{len(self.documents) + 1}")
            self._require_unique_id(inserted["_id"])
            self.documents.append(deepcopy(inserted))
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=inserted["_id"])
        return SimpleNamespace(matched_count=0, modified_count=0)

    async def update_many(self, query: dict[str, Any], update: dict[str, Any], *args: Any, **kwargs: Any):
        count = 0
        for item in self.documents:
            if _matches(item, query):
                item.update(deepcopy(update.get("$set", {})))
                count += 1
        return SimpleNamespace(matched_count=count, modified_count=count)

    async def find_one_and_update(
        self, query: dict[str, Any], update: dict[str, Any], *args: Any, **kwargs: Any
    ):
        """Motor returns the PRE-image unless return_document asks otherwise.

        Re-running the original query afterwards is not equivalent: an update
        that changes a field the query filters on would then match nothing and
        report "no document", the opposite of what Mongo does.
        """
        before = None
        for item in self.documents:
            if _matches(item, query):
                before = deepcopy(item)
                break
        await self.update_one(query, update, *args, **kwargs)
        if not kwargs.get("return_document"):
            return before
        if before is None:
            return None
        return await self.find_one({"_id": before.get("_id")})

    async def delete_one(self, query: dict[str, Any], *args: Any, **kwargs: Any):
        for index, item in enumerate(self.documents):
            if _matches(item, query):
                self.documents.pop(index)
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)


class _Database:
    def __init__(self) -> None:
        self.claims = _Collection(
            "claims",
            [
                {
                    "_id": "claim-1",
                    "claim_ref": "CLM-001",
                    "title": "Delay claim",
                    "organization_id": "org-1",
                    "project_id": "project-1",
                    "status": "draft",
                    "linked_document_ids": [],
                }
            ],
        )
        self.documents = _Collection(
            "documents",
            [
                {
                    "_id": "doc-1",
                    "filename": "Notice.pdf",
                    "subject": "Notice of delay",
                    # A received notice is incoming correspondence; the
                    # correspondence roles are only accepted for such Documents.
                    "uploadType": "incoming",
                    "organization_id": "org-1",
                    "project_id": "project-1",
                    "processing_status": "metadata_extracted",
                    "lifecycle_state": "active",
                    "current_version_id": "version-1",
                }
            ],
        )
        self.document_versions = _Collection(
            "document_versions",
            [
                {
                    "_id": "version-1",
                    "document_id": "doc-1",
                    "version_number": 1,
                    "is_current": True,
                    "file_object_id": "file-v1",
                }
            ],
        )
        self.entity_document_links = _Collection("entity_document_links")
        self.audit_events = _Collection("audit_events")
        self.ipc_bills = _Collection("ipc_bills")
        self.bank_guarantees = _Collection("bank_guarantees")


    def __getitem__(self, name: str) -> "_Collection":
        """Motor exposes collections by subscript as well as attribute, and
        lazily. Mirror that so production code may use either form."""
        existing = getattr(self, name, None)
        if existing is None:
            existing = _Collection(name)
            setattr(self, name, existing)
        return existing

class _Session:
    def __init__(self) -> None:
        self.transaction_started = False
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        if self.transaction_started and exc_type is None:
            self.committed = True

    def start_transaction(self):
        self.transaction_started = True
        return self


class _Client:
    def __init__(self) -> None:
        self.session = _Session()
        self.start_count = 0

    async def start_session(self):
        self.start_count += 1
        return self.session


class _IndexCollection:
    def __init__(self, name: str, sink: list[tuple[str, Any, dict[str, Any]]]) -> None:
        self.name = name
        self.sink = sink

    async def create_index(self, keys: Any, **kwargs: Any):
        self.sink.append((self.name, keys, kwargs))
        return kwargs.get("name") or "idx"


class _IndexDatabase:
    def __init__(self) -> None:
        self.calls: list[tuple[str, Any, dict[str, Any]]] = []

    def __getattr__(self, name: str):
        return _IndexCollection(name, self.calls)

    def __getitem__(self, name: str):
        return _IndexCollection(name, self.calls)


def _user() -> CurrentUser:
    return CurrentUser(
        id="user-1",
        username="architect",
        email="architect@example.com",
        roles=["superadmin"],
        organizations=[],
        projects=[],
        disabled=False,
    )


def _relationship_app(db: _Database, *, policy: Any = None, user: Any = None) -> FastAPI:
    app = FastAPI()
    app.include_router(relationship_router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = user or _user
    # The selection the browser sends: the claim's own project (CL-4A).
    pin_selection(app, db, "org-1", "project-1")
    if policy is not None:
        app.dependency_overrides[get_document_relationship_service] = lambda: DocumentRelationshipService(
            db, policy=policy
        )
    return app


def _claim_app(db: _Database) -> FastAPI:
    app = _relationship_app(db)
    app.include_router(claims_router, prefix="/api")
    return app


@pytest.mark.asyncio
async def test_claim_batch_link_is_persisted_visible_and_audited() -> None:
    db = _Database()
    transport = httpx.ASGITransport(app=_relationship_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/entities/claim/claim-1/document-links:batch",
            json={
                "links": [
                    {
                        "document_id": "doc-1",
                        "relationship_role": "notice",
                        "description": "Contractual notice",
                    }
                ],
                "idempotency_key": "claim-link-request-1",
            },
        )
        listed = await client.get("/api/entities/claim/claim-1/document-links")

    assert response.status_code == 201, response.text
    assert listed.status_code == 200, listed.text
    assert [item["document_id"] for item in listed.json()["links"]] == ["doc-1"]
    assert db.entity_document_links.documents[0]["target_type"] == "claim"
    assert db.entity_document_links.documents[0]["target_id"] == "claim-1"
    assert db.entity_document_links.documents[0]["organization_id"] == "org-1"
    assert db.entity_document_links.documents[0]["project_id"] == "project-1"
    assert db.entity_document_links.documents[0]["relationship_role"] == "notice"
    assert db.entity_document_links.documents[0]["removed_at"] is None
    assert any(
        event.get("action") == "document_relationship.linked"
        and event.get("resource_id") == db.entity_document_links.documents[0]["_id"]
        for event in db.audit_events.documents
    )


@pytest.mark.asyncio
async def test_claim_unlink_is_soft_and_visible_in_authorized_history() -> None:
    db = _Database()
    transport = httpx.ASGITransport(app=_relationship_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/entities/claim/claim-1/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "notice"}]},
        )
        link = created.json()["links"][0]
        removed = await client.post(
            f"/api/document-links/{link['_id']}:remove",
            json={"reason": "Linked to the wrong claim", "expected_revision": 1},
        )
        active = await client.get("/api/entities/claim/claim-1/document-links")
        history = await client.get(f"/api/document-links/{link['_id']}/history")

    assert removed.status_code == 200, removed.text
    assert active.json()["links"] == []
    assert history.status_code == 200, history.text
    historical = history.json()["links"][0]
    assert historical["removed_at"] is not None
    assert historical["removed_by"] == "user-1"
    assert historical["removal_reason"] == "Linked to the wrong claim"
    assert historical["_revision"] == 2
    assert any(
        event.get("action") == "document_relationship.unlinked"
        and event.get("resource_id") == link["_id"]
        for event in db.audit_events.documents
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("document_changes", "expected_status"),
    [
        ({"lifecycle_state": "deleted"}, 409),
        ({"duplicate_status": "duplicate"}, 409),
        ({"processing_status": "human_review_required"}, 409),
        ({"project_id": None}, 409),
        ({"project_id": "project-2"}, 403),
        ({"organization_id": "org-2"}, 403),
    ],
)
async def test_claim_link_rejects_non_authoritative_or_foreign_documents(
    document_changes: dict[str, Any], expected_status: int
) -> None:
    db = _Database()
    db.documents.documents[0].update(document_changes)
    transport = httpx.ASGITransport(app=_relationship_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/entities/claim/claim-1/document-links:batch",
            json={
                "organization_id": "org-1",
                "project_id": "project-1",
                "links": [{"document_id": "doc-1", "relationship_role": "notice"}],
            },
        )

    assert response.status_code == expected_status, response.text
    assert db.entity_document_links.documents == []
    assert not any(
        event.get("action") == "document_relationship.linked"
        for event in db.audit_events.documents
    )


@pytest.mark.asyncio
async def test_claim_link_rejects_a_role_outside_the_claim_vocabulary() -> None:
    db = _Database()
    transport = httpx.ASGITransport(app=_relationship_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/entities/claim/claim-1/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "invoice"}]},
        )

    assert response.status_code == 422, response.text
    assert db.entity_document_links.documents == []


@pytest.mark.asyncio
async def test_forward_and_reverse_reads_recheck_current_document_authority() -> None:
    db = _Database()
    transport = httpx.ASGITransport(app=_relationship_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/entities/claim/claim-1/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "notice"}]},
        )
        reverse = await client.get("/api/documents/doc-1/entity-links")
        db.documents.documents[0]["lifecycle_state"] = "deleted"
        blocked_forward = await client.get("/api/entities/claim/claim-1/document-links")
        blocked_reverse = await client.get("/api/documents/doc-1/entity-links")

    assert created.status_code == 201, created.text
    assert reverse.status_code == 200, reverse.text
    assert reverse.json()["links"][0]["target_type"] == "claim"
    assert reverse.json()["links"][0]["target_id"] == "claim-1"
    assert reverse.json()["links"][0]["target_label"] == "CLM-001"
    assert blocked_forward.json()["links"] == []
    assert blocked_reverse.json()["links"] == []


@pytest.mark.asyncio
async def test_document_dependency_endpoint_reports_active_claim_links() -> None:
    db = _Database()
    transport = httpx.ASGITransport(app=_relationship_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/entities/claim/claim-1/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "notice"}]},
        )
        dependencies = await client.get("/api/documents/doc-1/link-dependencies")

    assert created.status_code == 201, created.text
    assert dependencies.status_code == 200, dependencies.text
    assert dependencies.json() == {
        "document_id": "doc-1",
        "active_count": 1,
        "dependencies": [
            {
                "link_id": created.json()["links"][0]["_id"],
                "target_type": "claim",
                "target_id": "claim-1",
                "target_label": "CLM-001",
                "relationship_role": "notice",
                "frozen": False,
            }
        ],
    }


@pytest.mark.asyncio
async def test_link_and_audit_share_one_transaction_when_supported() -> None:
    db = _Database()
    db.client = _Client()
    transport = httpx.ASGITransport(app=_relationship_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/entities/claim/claim-1/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "notice"}]},
        )

    assert response.status_code == 201, response.text
    assert db.client.start_count == 1
    assert db.client.session.transaction_started is True
    assert db.client.session.committed is True
    assert db.entity_document_links.sessions == [db.client.session]
    assert [session for session in db.audit_events.sessions if session is not None] == [
        db.client.session
    ]


@pytest.mark.asyncio
async def test_unlink_and_audit_share_one_transaction_when_supported() -> None:
    db = _Database()
    db.client = _Client()
    transport = httpx.ASGITransport(app=_relationship_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/entities/claim/claim-1/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "notice"}]},
        )
        link = created.json()["links"][0]
        db.entity_document_links.sessions.clear()
        db.audit_events.sessions.clear()
        removed = await client.post(
            f"/api/document-links/{link['_id']}:remove",
            json={"reason": "Wrong Claim", "expected_revision": 1},
        )

    assert removed.status_code == 200, removed.text
    assert db.client.start_count == 2
    assert db.entity_document_links.sessions == [db.client.session]
    assert [session for session in db.audit_events.sessions if session is not None] == [
        db.client.session
    ]


@pytest.mark.asyncio
async def test_concurrent_duplicate_claim_links_are_idempotent_and_audited_once() -> None:
    db = _Database()
    transport = httpx.ASGITransport(app=_relationship_app(db))
    payload = {
        "links": [{"document_id": "doc-1", "relationship_role": "notice"}],
        "idempotency_key": "concurrent-claim-notice",
    }
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        first, second = await asyncio.gather(
            client.post(
                "/api/entities/claim/claim-1/document-links:batch", json=payload
            ),
            client.post(
                "/api/entities/claim/claim-1/document-links:batch", json=payload
            ),
        )

    assert first.status_code == 201, first.text
    assert second.status_code == 201, second.text
    assert first.json()["links"][0]["_id"] == second.json()["links"][0]["_id"]
    assert len(db.entity_document_links.documents) == 1
    assert len(
        [
            event
            for event in db.audit_events.documents
            if event.get("action") == "document_relationship.linked"
        ]
    ) == 1


@pytest.mark.asyncio
async def test_claim_evidence_freeze_pins_versions_and_blocks_future_mutation() -> None:
    db = _Database()
    transport = httpx.ASGITransport(app=_relationship_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/entities/claim/claim-1/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "notice"}]},
        )
        link = created.json()["links"][0]
        frozen = await client.post(
            "/api/entities/claim/claim-1/document-links:freeze",
            json={"reason": "Submission evidence issued"},
        )
        blocked_link = await client.post(
            "/api/entities/claim/claim-1/document-links:batch",
            json={
                "links": [
                    {"document_id": "doc-1", "relationship_role": "supporting_document"}
                ]
            },
        )
        blocked_remove = await client.post(
            f"/api/document-links/{link['_id']}:remove",
            json={"reason": "Should be locked", "expected_revision": 2},
        )

    assert frozen.status_code == 200, frozen.text
    assert frozen.json()["links"][0]["document_version_id"] == "version-1"
    assert frozen.json()["links"][0]["_revision"] == 2
    assert db.claims.documents[0]["evidence_frozen_at"] is not None
    assert db.claims.documents[0]["evidence_frozen_by"] == "user-1"
    assert blocked_link.status_code == 409, blocked_link.text
    assert blocked_remove.status_code == 409, blocked_remove.text
    assert any(
        event.get("action") == "document_relationships.frozen"
        for event in db.audit_events.documents
    )


@pytest.mark.asyncio
async def test_claim_evidence_freeze_migrates_and_pins_authorized_legacy_supporters() -> None:
    db = _Database()
    db.claims.documents[0]["linked_document_ids"] = ["doc-1"]
    transport = httpx.ASGITransport(app=_relationship_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        frozen = await client.post(
            "/api/entities/claim/claim-1/document-links:freeze",
            json={"reason": "Legacy evidence freeze"},
        )

    assert frozen.status_code == 200, frozen.text
    assert len(frozen.json()["links"]) == 1
    assert frozen.json()["links"][0]["document_id"] == "doc-1"
    assert frozen.json()["links"][0]["document_version_id"] == "version-1"
    assert db.entity_document_links.documents[0]["source"] == "legacy_compatibility"
    assert "linked_document_ids" not in db.claims.documents[0]


@pytest.mark.asyncio
async def test_frozen_claim_reads_resolve_the_pinned_file_object_not_the_current_version() -> None:
    db = _Database()
    db.documents.documents[0]["file_object_id"] = "file-v1"
    transport = httpx.ASGITransport(app=_relationship_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        await client.post(
            "/api/entities/claim/claim-1/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "notice"}]},
        )
        await client.post(
            "/api/entities/claim/claim-1/document-links:freeze",
            json={"reason": "Submission evidence issued"},
        )
        db.document_versions.documents.append(
            {
                "_id": "version-2",
                "document_id": "doc-1",
                "version_number": 2,
                "is_current": True,
                "file_object_id": "file-v2",
            }
        )
        db.documents.documents[0].update(
            {"current_version_id": "version-2", "file_object_id": "file-v2"}
        )
        listed = await client.get("/api/entities/claim/claim-1/document-links")

    assert listed.status_code == 200, listed.text
    link = listed.json()["links"][0]
    assert link["document_version_id"] == "version-1"
    assert link["document"]["file_object_id"] == "file-v1"
    assert link["document"]["resolved_version_id"] == "version-1"


@pytest.mark.asyncio
async def test_claim_evidence_freeze_fails_atomically_when_current_version_is_missing() -> None:
    db = _Database()
    db.documents.documents[0]["current_version_id"] = "missing-version"
    transport = httpx.ASGITransport(app=_relationship_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/entities/claim/claim-1/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "notice"}]},
        )
        frozen = await client.post(
            "/api/entities/claim/claim-1/document-links:freeze",
            json={"reason": "Submission evidence issued"},
        )

    assert created.status_code == 201, created.text
    assert frozen.status_code == 409, frozen.text
    assert db.entity_document_links.documents[0]["document_version_id"] is None
    assert db.entity_document_links.documents[0]["_revision"] == 1
    assert db.claims.documents[0].get("evidence_frozen_at") is None
    assert not any(
        event.get("action") == "document_relationships.frozen"
        for event in db.audit_events.documents
    )


@pytest.mark.asyncio
async def test_legacy_claim_document_writes_use_canonical_relationship_service() -> None:
    db = _Database()
    transport = httpx.ASGITransport(app=_claim_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/claims",
            json={
                "title": "Legacy client claim",
                "organization_id": "org-1",
                "project_id": "project-1",
                "linked_document_ids": ["doc-1"],
            },
        )
        claim_id = created.json()["_id"]
        fetched = await client.get(f"/api/claims/{claim_id}")
        updated = await client.put(
            f"/api/claims/{claim_id}", json={"linked_document_ids": []}
        )

    assert created.status_code == 201, created.text
    assert fetched.status_code == 200, fetched.text
    assert created.json()["linked_document_ids"] == ["doc-1"]
    assert fetched.json()["linked_document_ids"] == ["doc-1"]
    stored_claim = next(item for item in db.claims.documents if item["_id"] == claim_id)
    assert "linked_document_ids" not in stored_claim
    assert len(db.entity_document_links.documents) == 1
    relationship = db.entity_document_links.documents[0]
    assert relationship["target_type"] == "claim"
    assert relationship["relationship_role"] == "supporting_document"
    assert relationship["source"] == "legacy_compatibility"
    assert updated.status_code == 200, updated.text
    assert updated.json()["linked_document_ids"] == []
    assert relationship["removed_at"] is not None


@pytest.mark.asyncio
async def test_legacy_claim_read_through_rechecks_current_document_authority() -> None:
    db = _Database()
    db.claims.documents[0]["linked_document_ids"] = ["doc-1"]
    transport = httpx.ASGITransport(app=_claim_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        allowed = await client.get("/api/claims/claim-1")
        db.documents.documents[0]["lifecycle_state"] = "deleted"
        blocked = await client.get("/api/claims/claim-1")

    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["linked_document_ids"] == ["doc-1"]
    assert blocked.status_code == 200, blocked.text
    assert blocked.json()["linked_document_ids"] == []


@pytest.mark.asyncio
async def test_legacy_claim_read_through_is_visible_in_reverse_and_dependency_reads() -> None:
    db = _Database()
    db.claims.documents[0]["linked_document_ids"] = ["doc-1"]
    transport = httpx.ASGITransport(app=_relationship_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        reverse = await client.get("/api/documents/doc-1/entity-links")
        dependencies = await client.get("/api/documents/doc-1/link-dependencies")
        db.documents.documents[0]["lifecycle_state"] = "deleted"
        blocked = await client.get("/api/documents/doc-1/entity-links")

    assert reverse.status_code == 200, reverse.text
    assert reverse.json()["links"][0]["source"] == "legacy_read_through"
    assert reverse.json()["links"][0]["target_id"] == "claim-1"
    assert dependencies.json()["active_count"] == 1
    assert blocked.json()["links"] == []


class _PermissionPolicy:
    async def authorize_document(self, actor: Any, permission: str, document: Any, **kwargs: Any):
        if permission not in actor.permissions:
            from fastapi import HTTPException

            raise HTTPException(status_code=403, detail="Denied")


def _permission_user(*permissions: str) -> Any:
    return SimpleNamespace(
        id="limited-user",
        organization_id="org-1",
        project_id="project-1",
        permissions=set(permissions),
    )


@pytest.mark.asyncio
async def test_link_requires_both_claim_edit_and_document_view() -> None:
    for permissions in (
        (Permissions.CLAIM_EDIT,),
        (Permissions.DOCUMENT_VIEW,),
    ):
        db = _Database()
        user = _permission_user(*permissions)
        transport = httpx.ASGITransport(
            app=_relationship_app(db, policy=_PermissionPolicy(), user=lambda: user)
        )
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/entities/claim/claim-1/document-links:batch",
                json={"links": [{"document_id": "doc-1", "relationship_role": "notice"}]},
            )
        assert response.status_code == 403, response.text
        assert db.entity_document_links.documents == []


@pytest.mark.asyncio
async def test_reverse_read_omits_claim_without_claim_view_permission() -> None:
    db = _Database()
    db.entity_document_links.documents.append(
        {
            "_id": "link-1",
            "organization_id": "org-1",
            "project_id": "project-1",
            "target_type": "claim",
            "target_id": "claim-1",
            "document_id": "doc-1",
            "relationship_role": "notice",
            "source": "user",
            "created_at": "2026-08-20T00:00:00",
            "removed_at": None,
            "_revision": 1,
        }
    )
    user = _permission_user(Permissions.DOCUMENT_VIEW)
    transport = httpx.ASGITransport(
        app=_relationship_app(db, policy=_PermissionPolicy(), user=lambda: user)
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/documents/doc-1/entity-links")

    assert response.status_code == 200, response.text
    assert response.json()["links"] == []


@pytest.mark.asyncio
async def test_link_history_requires_current_document_view_permission() -> None:
    db = _Database()
    db.entity_document_links.documents.append(
        {
            "_id": "link-1",
            "organization_id": "org-1",
            "project_id": "project-1",
            "target_type": "claim",
            "target_id": "claim-1",
            "document_id": "doc-1",
            "relationship_role": "notice",
            "source": "user",
            "description": "must not leak",
            "removed_at": None,
            "_revision": 1,
        }
    )
    user = _permission_user(Permissions.CLAIM_VIEW)
    transport = httpx.ASGITransport(
        app=_relationship_app(db, policy=_PermissionPolicy(), user=lambda: user)
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/document-links/link-1/history")

    assert response.status_code == 403, response.text
    assert "must not leak" not in response.text


@pytest.mark.asyncio
async def test_deleting_claim_soft_removes_relationships_and_audits_in_one_transaction() -> None:
    db = _Database()
    db.client = _Client()
    transport = httpx.ASGITransport(app=_claim_app(db))
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        created = await client.post(
            "/api/entities/claim/claim-1/document-links:batch",
            json={"links": [{"document_id": "doc-1", "relationship_role": "notice"}]},
        )
        db.entity_document_links.sessions.clear()
        db.audit_events.sessions.clear()
        deleted = await client.delete("/api/claims/claim-1")

    assert created.status_code == 201, created.text
    assert deleted.status_code == 204, deleted.text
    assert db.claims.documents == []
    assert db.entity_document_links.documents[0]["removed_at"] is not None
    assert db.entity_document_links.sessions == [db.client.session]
    actions = [event.get("action") for event in db.audit_events.documents]
    assert "document_relationship.unlinked" in actions
    assert "claim.deleted" in actions
    transactional_sessions = [
        session
        for event, session in zip(db.audit_events.documents[-2:], db.audit_events.sessions[-2:])
        if event.get("action") in {"document_relationship.unlinked", "claim.deleted"}
    ]
    assert transactional_sessions == [db.client.session, db.client.session]


@pytest.mark.asyncio
async def test_startup_indexes_define_canonical_relationship_constraints() -> None:
    db = _IndexDatabase()
    await ensure_indexes(db)
    relationship_calls = [call for call in db.calls if call[0] == "entity_document_links"]

    assert any(
        keys
        == [
            ("organization_id", 1),
            ("project_id", 1),
            ("target_type", 1),
            ("target_id", 1),
            ("document_id", 1),
            ("relationship_role", 1),
        ]
        and options.get("unique") is True
        and options.get("partialFilterExpression") == {"removed_at": None}
        for _, keys, options in relationship_calls
    )
    assert any(
        keys
        == [
            ("organization_id", 1),
            ("project_id", 1),
            ("target_type", 1),
            ("target_id", 1),
            ("removed_at", 1),
        ]
        for _, keys, _ in relationship_calls
    )
    assert any(
        keys
        == [
            ("organization_id", 1),
            ("project_id", 1),
            ("document_id", 1),
            ("removed_at", 1),
        ]
        for _, keys, _ in relationship_calls
    )
    assert any(
        keys == [("target_type", 1), ("target_id", 1), ("created_at", -1)]
        for _, keys, _ in relationship_calls
    )
    assert any(
        keys == [("document_id", 1), ("created_at", -1)]
        for _, keys, _ in relationship_calls
    )


# -- the fake obeys the constraints every Mongo collection has ---------------


@pytest.mark.asyncio
async def test_the_fake_collection_enforces_id_uniqueness_on_insert() -> None:
    collection = _Collection("widgets", [{"_id": "w-1"}])

    with pytest.raises(DuplicateKeyError):
        await collection.insert_one({"_id": "w-1", "note": "second"})

    assert len(collection.documents) == 1


@pytest.mark.asyncio
async def test_the_fake_collection_enforces_id_uniqueness_on_upsert() -> None:
    """The upsert path must not be a way around the constraint.

    `link_batch` writes through `update_one(..., upsert=True)`, so an upsert
    that quietly appended a duplicate `_id` would let a test observe two rows
    where real Mongo permits one.
    """
    collection = _Collection("widgets", [{"_id": "w-1", "state": "old"}])

    with pytest.raises(DuplicateKeyError):
        await collection.update_one(
            {"_id": "w-1", "missing_field": "no match"},
            {"$setOnInsert": {"state": "new"}},
            upsert=True,
        )

    assert len(collection.documents) == 1
    assert collection.documents[0]["state"] == "old"


@pytest.mark.asyncio
async def test_the_fake_collection_refuses_operators_it_cannot_evaluate() -> None:
    """Fail closed. Silently returning True for an unimplemented operator made
    every lease-expiry query match unconditionally."""
    collection = _Collection("widgets", [{"_id": "w-1", "name": "abc"}])

    with pytest.raises(NotImplementedError):
        await collection.find_one({"name": {"$elemMatch": {"$eq": "abc"}}})


@pytest.mark.asyncio
async def test_the_fake_collection_evaluates_regex_like_mongo() -> None:
    collection = _Collection("widgets", [{"_id": "w-1", "name": "ABC"}, {"_id": "w-2", "name": None}])

    assert (await collection.find_one({"name": {"$regex": "^a", "$options": "i"}}))["_id"] == "w-1"
    assert await collection.find_one({"name": {"$regex": "^a"}}) is None


@pytest.mark.asyncio
async def test_the_fake_collection_compares_ranges() -> None:
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    collection = _Collection(
        "leases",
        [
            {"_id": "live", "expires_at": now + timedelta(minutes=10)},
            {"_id": "stale", "expires_at": now - timedelta(minutes=10)},
        ],
    )

    expired = await collection.find_one({"expires_at": {"$lte": now}})

    assert expired is not None and expired["_id"] == "stale"
