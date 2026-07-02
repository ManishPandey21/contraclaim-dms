from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from rbac_backend.models.contract_models import ContractSearchRequest, StatusResponse
from rbac_backend.routers.contracts import (
    get_contract_status,
    list_contract_uploads,
    reindex_contract,
    search_contracts,
    upload_contract_chunk,
    upload_contracts_multipart,
)


class _DenyPolicy:
    def __init__(self) -> None:
        self.calls = []

    async def authorize(self, current_user, permission, **kwargs):
        self.calls.append({"permission": permission, **kwargs})
        raise HTTPException(status_code=403, detail="denied")


class _FakeUpload:
    filename = "contract.pdf"

    async def read(self):
        raise AssertionError("file bytes were read before upload authorization")

    async def seek(self, _offset: int):
        raise AssertionError("file was rewound before upload authorization")


class _FakeContractService:
    def __init__(self) -> None:
        self.created_session = False
        self.validated_session = False

    async def create_upload_session(self, *_args, **_kwargs):
        self.created_session = True
        raise AssertionError("upload session was created before upload authorization")

    async def validate_upload_session(self, *_args, **_kwargs):
        self.validated_session = True
        return {
            "organization_id": "org-A",
            "project_id": "proj-A",
            "safe_filename": "contract.pdf",
        }

    async def list_uploads(self, *_args, **_kwargs):
        raise AssertionError("contract list service was called before authorization")

    async def search_contracts(self, *_args, **_kwargs):
        raise AssertionError("contract search service was called before authorization")


class _FakeStatusService:
    async def get_job_status(self, *_args, **_kwargs):
        return StatusResponse(
            upload_id="upload-1",
            document_id="doc-1",
            status="completed",
            organization_id="org-A",
            project_id="proj-A",
        )


def _user():
    return SimpleNamespace(id="user-1", roles=["orgadmin"], organization_id="org-A", projects=["proj-A"])


@pytest.mark.asyncio
async def test_multipart_without_session_requires_policy_before_reading_file():
    policy = _DenyPolicy()
    service = _FakeContractService()

    with pytest.raises(HTTPException) as exc:
        await upload_contracts_multipart(
            files=[_FakeUpload()],
            organization_id="org-A",
            project_id="proj-A",
            upload_ids=None,
            tags=None,
            contract_service=service,
            file_service=SimpleNamespace(),
            current_user=_user(),
            policy=policy,
        )

    assert exc.value.status_code == 403
    assert policy.calls[0]["permission"] == "dms.document.upload"
    assert policy.calls[0]["organization_id"] == "org-A"
    assert policy.calls[0]["project_id"] == "proj-A"
    assert service.created_session is False


@pytest.mark.asyncio
async def test_multipart_existing_session_authorizes_saved_scope_before_reading_file():
    policy = _DenyPolicy()
    service = _FakeContractService()

    with pytest.raises(HTTPException) as exc:
        await upload_contracts_multipart(
            files=[_FakeUpload()],
            organization_id=None,
            project_id=None,
            upload_ids=["upload-1"],
            tags=None,
            contract_service=service,
            file_service=SimpleNamespace(),
            current_user=_user(),
            policy=policy,
        )

    assert exc.value.status_code == 403
    assert service.validated_session is True
    assert policy.calls[0]["permission"] == "dms.document.upload"
    assert policy.calls[0]["organization_id"] == "org-A"
    assert policy.calls[0]["project_id"] == "proj-A"


@pytest.mark.asyncio
async def test_chunk_upload_authorizes_session_scope_before_reading_chunk():
    policy = _DenyPolicy()
    service = _FakeContractService()

    with pytest.raises(HTTPException) as exc:
        await upload_contract_chunk(
            chunk=_FakeUpload(),
            upload_id="upload-1",
            filename="contract.pdf",
            chunkIndex=0,
            totalChunks=1,
            organization_id=None,
            project_id=None,
            tags=None,
            contract_service=service,
            file_service=SimpleNamespace(),
            current_user=_user(),
            policy=policy,
        )

    assert exc.value.status_code == 403
    assert service.validated_session is True
    assert policy.calls[0]["permission"] == "dms.document.upload"
    assert policy.calls[0]["organization_id"] == "org-A"
    assert policy.calls[0]["project_id"] == "proj-A"


@pytest.mark.asyncio
async def test_contract_status_requires_policy_on_resolved_job_scope():
    policy = _DenyPolicy()

    with pytest.raises(HTTPException) as exc:
        await get_contract_status(
            upload_id="upload-1",
            contract_service=_FakeStatusService(),
            current_user=_user(),
            policy=policy,
        )

    assert exc.value.status_code == 403
    assert policy.calls[0]["permission"] == "dms.document.view"
    assert policy.calls[0]["organization_id"] == "org-A"
    assert policy.calls[0]["project_id"] == "proj-A"


@pytest.mark.asyncio
async def test_contract_list_requires_policy_before_service_query():
    policy = _DenyPolicy()

    with pytest.raises(HTTPException) as exc:
        await list_contract_uploads(
            organization_id="org-A",
            project_id="proj-A",
            limit=50,
            skip=0,
            contract_service=_FakeContractService(),
            current_user=_user(),
            policy=policy,
        )

    assert exc.value.status_code == 403
    assert policy.calls[0]["permission"] == "dms.document.view"
    assert policy.calls[0]["organization_id"] == "org-A"
    assert policy.calls[0]["project_id"] == "proj-A"


@pytest.mark.asyncio
async def test_contract_search_requires_policy_before_service_query():
    policy = _DenyPolicy()

    with pytest.raises(HTTPException) as exc:
        await search_contracts(
            request=ContractSearchRequest(query="extension of time", organization_id="org-A", project_id="proj-A"),
            contract_service=_FakeContractService(),
            current_user=_user(),
            policy=policy,
        )

    assert exc.value.status_code == 403
    assert policy.calls[0]["permission"] == "dms.document.view"
    assert policy.calls[0]["organization_id"] == "org-A"
    assert policy.calls[0]["project_id"] == "proj-A"


class _FakeReindexService:
    """Resolves the document scope, then fails loudly if anything tries to
    kick off ingestion before authorization has passed."""

    async def get_contract_document(self, _document_id, _current_user):
        return {
            "organization_id": "org-A",
            "project_id": "proj-A",
            "filename": "contract.pdf",
            "contract_upload_id": "upload-1",
            "filepath_local": "/tmp/contract.pdf",
            "tags": [],
        }

    async def update_job_status(self, *_args, **_kwargs):
        raise AssertionError("reindex was queued before authorization")

    async def update_contract_document(self, *_args, **_kwargs):
        raise AssertionError("contract status was mutated before authorization")


@pytest.mark.asyncio
async def test_reindex_requires_policy_on_resolved_document_scope():
    policy = _DenyPolicy()

    with pytest.raises(HTTPException) as exc:
        await reindex_contract(
            document_id="doc-1",
            contract_service=_FakeReindexService(),
            current_user=_user(),
            policy=policy,
        )

    assert exc.value.status_code == 403
    assert policy.calls[0]["permission"] == "dms.document.edit_metadata"
    assert policy.calls[0]["organization_id"] == "org-A"
    assert policy.calls[0]["project_id"] == "proj-A"
