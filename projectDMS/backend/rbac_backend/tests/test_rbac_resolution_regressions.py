from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from rbac_backend.core.security import CurrentUser, create_access_token, get_current_user
from rbac_backend.models.document import BulkUploadStatus
from rbac_backend.routers.permissions import check_permission_endpoint
from rbac_backend.services.allocation_service import AllocationService
from rbac_backend.services.bulk_upload_service import BulkUploadService
from rbac_backend.services.storage_settings_service import StorageSettingsService


class _Cursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def limit(self, value):
        self.rows = self.rows[:value]
        return self

    async def to_list(self, length=None):
        return list(self.rows if length is None else self.rows[:length])


class _Collection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.find_one_queries = []
        self.find_queries = []
        self.update_queries = []

    async def find_one(self, query):
        self.find_one_queries.append(query)
        if "email" in query:
            return next((row for row in self.rows if row.get("email") == query["email"]), None)
        if "_id" in query:
            expected = query["_id"]
            if isinstance(expected, dict) and "$in" in expected:
                return next((row for row in self.rows if row.get("_id") in expected["$in"]), None)
            return next((row for row in self.rows if row.get("_id") == expected), None)
        return None

    def find(self, query):
        self.find_queries.append(query)
        matches = [
            row
            for row in self.rows
            if all(row.get(key) == value for key, value in query.items())
        ]
        return _Cursor(matches)

    async def update_one(self, query, update):
        self.update_queries.append(query)
        return SimpleNamespace(matched_count=0)


@pytest.mark.asyncio
async def test_disabled_account_is_rejected_even_with_valid_unexpired_token(monkeypatch):
    monkeypatch.setattr("rbac_backend.core.security.settings.ALLOW_DEV_HEADERS", False)
    token = create_access_token({"sub": "disabled@example.com"})
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/api/users/me",
            "headers": [(b"authorization", f"Bearer {token}".encode())],
        }
    )
    db = SimpleNamespace(
        users=_Collection(
            [{"_id": "user-1", "email": "disabled@example.com", "roles": ["orguser"], "disabled": True}]
        )
    )

    with pytest.raises(HTTPException) as exc:
        await get_current_user(request, db)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_permission_check_rejects_cross_user_oracle():
    current_user = CurrentUser(
        id="user-1",
        username="user-1",
        email="user-1@example.com",
        roles=["orguser"],
    )
    permission_service = SimpleNamespace(user_has_permission=lambda *_args, **_kwargs: None)

    with pytest.raises(HTTPException) as exc:
        await check_permission_endpoint(
            {"user_id": "user-2", "permission": "dms.document.view"},
            permission_service,
            current_user,
        )
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_project_storage_read_uses_authoritative_compound_scope():
    projects = _Collection([{"_id": "proj-1", "organization_id": "org-1"}])
    settings = _Collection(
        [{"type": "project", "project_id": "proj-1", "org_id": "org-1", "inherit_from_org": True}]
    )
    service = StorageSettingsService(SimpleNamespace(projects=projects, storage_settings=settings))

    result = await service.get_project_settings("proj-1")

    assert result is not None
    assert settings.find_queries == [
        {"type": "project", "project_id": "proj-1", "org_id": "org-1"}
    ]


@pytest.mark.asyncio
async def test_bulk_job_cache_is_bound_to_persisted_tenant():
    service = BulkUploadService()
    service._active_jobs["job-1"] = BulkUploadStatus(
        job_id="job-1",
        organization_id="org-1",
        project_id="proj-1",
        requested_by="user-1",
        total_files=1,
        processed_files=0,
        successful_uploads=0,
        failed_uploads=0,
        status="processing",
        created_at=datetime.utcnow(),
    )

    assert await service.get_job_status("job-1", organization_id="org-2") is None
    assert await service.get_job_status("job-1", organization_id="org-1", project_id="proj-1") is not None


@pytest.mark.asyncio
async def test_allocation_update_selector_contains_authorized_scope():
    collection = _Collection()
    service = AllocationService(SimpleNamespace(expert_allocations=collection))
    payload = SimpleNamespace(model_dump=lambda **_kwargs: {"status": "revoked"})

    result = await service.update_allocation(
        "allocation-1",
        payload,
        SimpleNamespace(id="manager-1"),
        expected_organization_id="org-1",
        expected_project_id="proj-1",
    )

    assert result is None
    # The authoritative scope is part of the pre-update lookup, so a foreign
    # allocation is never mutated by ID alone.
    assert collection.update_queries == []
    assert collection.find_one_queries == [
        {"_id": "allocation-1", "organization_id": "org-1", "project_id": "proj-1"}
    ]
