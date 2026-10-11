"""Batched subtag lookup: one read, bounded, authorized before it touches subtags.

The Documents page used to fetch ``/tags/{id}/subtags`` once per tag in the
catalogue on every mount, which alone could exhaust the per-user Tags read
budget. ``GET /tags/subtags/batch`` replaces that loop with one request that
costs one read unit, and resolves the requested ids against the caller's
authorized tag universe inside the database query itself.

These tests pin the controller contract and the exact queries the service
issues; ``integration/test_tags_subtag_batch_mongo.py`` proves isolation over
real HTTP, Mongo and RBAC.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest
from bson import ObjectId
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from rbac_backend.core.errors import register_domain_error_handler
from rbac_backend.core.security import get_current_user
from rbac_backend.models.tag import SubtagLookupItem
from rbac_backend.routers import tags as tags_router
from rbac_backend.routers.tags import (
    SUBTAG_BATCH_MAX_TAG_IDS,
    TAGS_READ_RATE_LIMIT_SCOPE,
    TAGS_WRITE_RATE_LIMIT_SCOPE,
    TagController,
    get_tag_controller,
)
from rbac_backend.services.tag_service import SUBTAG_BATCH_MAX_RESULTS, TagService
from rbac_backend.utils.rate_limiter import RateLimiter

AUTHORIZED_QUERY = {"$or": [{"visibility": "global"}, {"organization_id": {"$in": ["org-a"]}}]}


class _NoRedisState:
    async def get_redis(self):
        return None


def _limiter(scope: str, max_requests: int) -> RateLimiter:
    limiter = RateLimiter(max_requests=max_requests, window_seconds=600, scope=f"{scope}:{uuid4().hex}")
    limiter._runtime_state = cast(Any, _NoRedisState())
    return limiter


class _Auth:
    def __init__(self) -> None:
        self.queries: list[Any] = []

    async def require_permission(self, *_a, **_k):
        return None

    async def build_tag_query(self, current_user, filters=None):
        self.queries.append(filters)
        return dict(AUTHORIZED_QUERY)


class _Service:
    def __init__(self, result: list[SubtagLookupItem] | None = None) -> None:
        self.calls: list[tuple[Any, list[str]]] = []
        self.result = result or []

    async def get_subtag_lookup_for_tags(self, authorized_query, tag_ids):
        self.calls.append((authorized_query, list(tag_ids)))
        return self.result, False


def _controller(service=None, auth=None, read_max: int = 50, write_max: int = 50):
    read = _limiter(TAGS_READ_RATE_LIMIT_SCOPE, read_max)
    write = _limiter(TAGS_WRITE_RATE_LIMIT_SCOPE, write_max)
    controller = TagController(
        cast(Any, service or _Service()), cast(Any, auth or _Auth()), read, write, cast(Any, None)
    )
    return controller, read, write


def _user():
    return SimpleNamespace(id=f"user-{uuid4().hex}", organization_id="org-a", roles=["orgadmin"])


def _bucket_count(limiter: RateLimiter, user) -> float:
    bucket = limiter._buckets.get(limiter._scoped(f"user:{user.id}"))
    return float(bucket["count"]) if bucket else 0.0


# --- controller contract --------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_passes_the_authorized_query_and_deduplicated_ids():
    service, auth = _Service(), _Auth()
    controller, *_ = _controller(service, auth)
    a, b = str(ObjectId()), "legacy-tag-b"

    result = await controller.get_subtags_batch([a, b, a, f" {b} ", a], _user())

    assert result.subtags == [] and result.truncated is False
    assert service.calls == [(AUTHORIZED_QUERY, [a, b])]
    # The authorized universe comes from the caller, never from request filters.
    assert auth.queries == [{}]


@pytest.mark.asyncio
async def test_empty_batch_succeeds_without_touching_the_database():
    service, auth = _Service(), _Auth()
    controller, *_ = _controller(service, auth)

    result = await controller.get_subtags_batch([], _user())

    assert result.subtags == []
    assert service.calls == [] and auth.queries == []


@pytest.mark.asyncio
async def test_more_than_the_maximum_unique_ids_is_422():
    service = _Service()
    controller, *_ = _controller(service)
    ids = [str(ObjectId()) for _ in range(SUBTAG_BATCH_MAX_TAG_IDS + 1)]

    with pytest.raises(HTTPException) as exc:
        await controller.get_subtags_batch(ids, _user())

    assert exc.value.status_code == 422
    assert service.calls == []


@pytest.mark.asyncio
async def test_duplicates_do_not_count_towards_the_maximum():
    service = _Service()
    controller, *_ = _controller(service)
    ids = [str(ObjectId()) for _ in range(SUBTAG_BATCH_MAX_TAG_IDS)]

    await controller.get_subtags_batch(ids + ids, _user())

    assert len(service.calls[0][1]) == SUBTAG_BATCH_MAX_TAG_IDS


@pytest.mark.parametrize("bad", ["", "   ", "x" * 65])
@pytest.mark.asyncio
async def test_blank_or_oversized_ids_are_422(bad):
    service = _Service()
    controller, *_ = _controller(service)

    with pytest.raises(HTTPException) as exc:
        await controller.get_subtags_batch([str(ObjectId()), bad], _user())

    assert exc.value.status_code == 422
    assert service.calls == []


@pytest.mark.asyncio
async def test_a_batch_costs_one_read_unit_and_no_write_units():
    controller, read, write = _controller(read_max=2, write_max=50)
    user = _user()
    ids = [str(ObjectId()) for _ in range(SUBTAG_BATCH_MAX_TAG_IDS)]

    await controller.get_subtags_batch(ids, user)
    assert _bucket_count(read, user) == 1
    await controller.get_subtags_batch(ids, user)
    assert _bucket_count(read, user) == 2
    assert _bucket_count(write, user) == 0

    with pytest.raises(HTTPException) as exc:
        await controller.get_subtags_batch(ids, user)
    assert exc.value.status_code == 429
    assert 1 <= int(exc.value.headers["Retry-After"]) <= 600


# --- the queries the service issues ---------------------------------------------


class _Cursor:
    def __init__(self, docs):
        self._docs = docs
        self.sorted_by = None
        self.limited_to = None

    def sort(self, *args):
        self.sorted_by = args
        return self

    def limit(self, n):
        self.limited_to = n
        return self

    async def to_list(self, length=None):
        docs = self._docs if self.limited_to is None else self._docs[: self.limited_to]
        return list(docs if length is None else docs[:length])


class _Collection:
    def __init__(self, docs=None):
        self.docs = docs or []
        self.finds: list[tuple[Any, Any]] = []
        self.cursors: list[_Cursor] = []

    def find(self, query, projection=None):
        self.finds.append((query, projection))
        cursor = _Cursor(self.docs)
        self.cursors.append(cursor)
        return cursor

    def __getattr__(self, name):
        raise AssertionError(f"batch lookup must not call {name} (no per-row fan-out)")


def _service(tag_docs, subtag_docs) -> tuple[TagService, _Collection, _Collection, _Collection]:
    service = TagService()
    tags, subtags, documents = _Collection(tag_docs), _Collection(subtag_docs), _Collection()
    service._tags, service._subtags, service._documents = tags, subtags, documents
    return service, tags, subtags, documents


@pytest.mark.asyncio
async def test_authorized_parents_constrain_the_subtag_read():
    own = ObjectId()
    foreign = ObjectId()
    # The database answers the parent query with the one tag the caller may see.
    service, tags, subtags, documents = _service(
        [{"_id": own}],
        [{"_id": ObjectId(), "name": "Late payment", "tag_id": str(own)}],
    )

    items, truncated = await service.get_subtag_lookup_for_tags(AUTHORIZED_QUERY, [str(own), str(foreign)])

    [(parent_query, parent_projection)] = tags.finds
    assert parent_query == {
        "$and": [
            AUTHORIZED_QUERY,
            {"is_active": True},
            {"_id": {"$in": [str(own), own, str(foreign), foreign]}},
        ]
    }
    assert parent_projection == {"_id": 1}
    [(subtag_query, subtag_projection)] = subtags.finds
    # Only the authorized parent reaches the subtag query, in both id forms.
    assert subtag_query == {"is_active": True, "tag_id": {"$in": [str(own), own]}}
    assert subtag_projection == {"_id": 1, "name": 1, "tag_id": 1}
    assert documents.finds == []
    assert [item.model_dump(by_alias=True) for item in items] == [
        {"_id": items[0].id, "name": "Late payment", "tag_id": str(own)}
    ]
    assert truncated is False


@pytest.mark.asyncio
async def test_no_authorized_parent_means_no_subtag_query():
    service, tags, subtags, _ = _service([], [{"_id": ObjectId(), "name": "x", "tag_id": "t"}])

    items, truncated = await service.get_subtag_lookup_for_tags(AUTHORIZED_QUERY, [str(ObjectId())])

    assert items == [] and truncated is False
    assert len(tags.finds) == 1
    assert subtags.finds == []


@pytest.mark.asyncio
async def test_legacy_string_ids_and_object_id_subtag_parents_both_resolve():
    legacy_tag = "legacy-tag-1"
    modern = ObjectId()
    service, tags, subtags, _ = _service(
        [{"_id": legacy_tag}, {"_id": modern}],
        [
            {"_id": "legacy-sub", "name": "Legacy child", "tag_id": legacy_tag},
            {"_id": ObjectId(), "name": "Modern child", "tag_id": modern},  # parent stored as ObjectId
        ],
    )

    items, _ = await service.get_subtag_lookup_for_tags(AUTHORIZED_QUERY, [legacy_tag, str(modern)])

    assert tags.finds[0][0]["$and"][2] == {"_id": {"$in": [legacy_tag, str(modern), modern]}}
    assert subtags.finds[0][0]["tag_id"] == {"$in": [legacy_tag, str(modern), modern]}
    assert {(item.name, item.tag_id) for item in items} == {
        ("Legacy child", legacy_tag),
        ("Modern child", str(modern)),
    }


@pytest.mark.asyncio
async def test_result_size_is_bounded_and_reports_truncation():
    parent = ObjectId()
    docs = [{"_id": ObjectId(), "name": f"s{i}", "tag_id": str(parent)} for i in range(SUBTAG_BATCH_MAX_RESULTS + 1)]
    service, _, subtags, _ = _service([{"_id": parent}], docs)

    items, truncated = await service.get_subtag_lookup_for_tags(AUTHORIZED_QUERY, [str(parent)])

    assert len(items) == SUBTAG_BATCH_MAX_RESULTS
    assert truncated is True
    # The database itself is told the bound, so its sort is top-k, not total.
    assert subtags.cursors[0].limited_to == SUBTAG_BATCH_MAX_RESULTS + 1
    assert subtags.cursors[0].sorted_by == ("name", 1)


# --- the route --------------------------------------------------------------------


def _client(controller: TagController, monkeypatch) -> TestClient:
    calls: list[dict[str, Any]] = []

    async def _authorize(*_a, **kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(tags_router.PolicyService, "authorize", _authorize)
    app = FastAPI()
    register_domain_error_handler(app)
    app.include_router(tags_router.router, prefix="/api")
    user = _user()
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_tag_controller] = lambda: controller
    client = TestClient(app, raise_server_exceptions=False)
    client.policy_calls = calls  # type: ignore[attr-defined]
    return client


def test_route_resolves_to_the_batch_handler_not_the_tag_by_id_route(monkeypatch):
    item = SubtagLookupItem(_id="s1", name="Late payment", tag_id="t1")
    service = _Service([item])
    controller, *_ = _controller(service)
    client = _client(controller, monkeypatch)

    response = client.get("/api/tags/subtags/batch", params=[("tag_ids", "t1"), ("tag_ids", "t2")])

    assert response.status_code == 200, response.text
    assert response.json() == {"subtags": [{"_id": "s1", "name": "Late payment", "tag_id": "t1"}], "truncated": False}
    assert service.calls[0][1] == ["t1", "t2"]
    # Route-level document-view gate, as GET /tags.
    assert client.policy_calls[0]["resource_type"] == "tag"  # type: ignore[attr-defined]


def test_route_without_ids_is_an_empty_success(monkeypatch):
    service = _Service()
    controller, *_ = _controller(service)

    response = _client(controller, monkeypatch).get("/api/tags/subtags/batch")

    assert response.status_code == 200
    assert response.json() == {"subtags": [], "truncated": False}
    assert service.calls == []


def test_route_over_the_maximum_is_422(monkeypatch):
    controller, *_ = _controller()
    params = [("tag_ids", f"t{i}") for i in range(SUBTAG_BATCH_MAX_TAG_IDS + 1)]

    response = _client(controller, monkeypatch).get("/api/tags/subtags/batch", params=params)

    assert response.status_code == 422


def test_route_rate_limited_is_429_with_retry_after(monkeypatch):
    controller, read, _ = _controller(read_max=1)
    client = _client(controller, monkeypatch)

    assert client.get("/api/tags/subtags/batch", params=[("tag_ids", "t1")]).status_code == 200
    response = client.get("/api/tags/subtags/batch", params=[("tag_ids", "t1")])

    assert response.status_code == 429
    assert response.json()["detail"] == "Rate limit exceeded"
    assert int(response.headers["retry-after"]) >= 1
