"""Upload scope durability, retry conflict and immutability.

U12-04 - scope intent survives session TTL expiry.
U12-07 - scope cannot silently change on idempotent retry.
U12-08 - authoritative scope cannot change through a generic metadata edit.
U12-10 - uploaded + classified + projection-pending is not evidence-capable.

Real Mongo for U12-04 and U12-07: the first is about surviving a TTL the
database enforces, and the second about state that persists between two separate
requests. Both are claims about durability, and a fixture that keeps everything
in memory cannot fail either of them.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

import pytest

pytestmark = pytest.mark.integration

MONGODB_URI = os.environ.get("CONTRACT_DURABILITY_MONGODB_URI")

if not MONGODB_URI:  # pragma: no cover - environment gate
    pytest.skip(
        "CONTRACT_DURABILITY_MONGODB_URI is not set; this suite needs a disposable Mongo",
        allow_module_level=True,
    )

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from rbac_backend.core.permissions import Permissions  # noqa: E402
from rbac_backend.models.contract_document import (  # noqa: E402
    ContractDocumentType,
    CurrentState,
)
from rbac_backend.models.contract_upload_scope import parse_upload_scope  # noqa: E402
from rbac_backend.services.contract_document_store import (  # noqa: E402
    APPLICABILITY_COLLECTION,
    APPLICABILITY_EVENTS_COLLECTION,
    CONTRACT_DOCUMENTS_COLLECTION,
)
from rbac_backend.services.contract_scope_resolver import (  # noqa: E402
    AuthorizedContractScope,
    ContractScopeResolver,
)
from rbac_backend.services.contract_upload_durability import (  # noqa: E402
    ScopeConflictOnRetry,
    ScopeIsImmutableAfterPromotion,
    UploadDurabilityService,
)
from rbac_backend.services.contract_upload_scope_service import (  # noqa: E402
    ORGANIZATION_SCOPE_UPLOAD_CAPABILITIES,
    ContractUploadScopeService,
)

ORG = "org-durability"
PROJECT = "project-durability"
CONTRACT = "contract-durability"


@asynccontextmanager
async def _database():
    client = AsyncIOMotorClient(MONGODB_URI, serverSelectionTimeoutMS=6000)
    name = f"contract_durability_{uuid.uuid4().hex[:10]}"
    db = client[name]
    try:
        await db["projects"].insert_one({"_id": PROJECT, "organization_id": ORG})
        yield db
    finally:
        await client.drop_database(name)
        client.close()


def _capabilities(granted: Set[str]):
    async def has_capability(*, actor_id, capability, organization_id, project_id):
        return str(capability) in granted

    return has_capability


def _upload_service(db, granted: Optional[Set[str]] = None) -> ContractUploadScopeService:
    if granted is None:
        granted = {str(c) for c in ORGANIZATION_SCOPE_UPLOAD_CAPABILITIES}
    return ContractUploadScopeService(db, has_capability=_capabilities(granted))


# --------------------------------------------------------------------------- #
# U12-04 : intent outlives the session
# --------------------------------------------------------------------------- #


def test_scope_intent_survives_session_ttl_expiry():
    """U12-04."""

    async def scenario():
        async with _database() as db:
            await db["contract_upload_sessions"].create_index(
                "expiresAt", expireAfterSeconds=0, background=False
            )
            await db["contract_upload_sessions"].insert_one(
                {
                    "_id": "session-1",
                    "upload_id": "upload-doc-1",
                    "project_id": PROJECT,
                    "expiresAt": datetime.now(timezone.utc) - timedelta(hours=1),
                }
            )

            service = _upload_service(db, {str(Permissions.DOCUMENT_UPLOAD)})
            await service.create_candidate(
                parse_upload_scope({"scope_level": "project", "project_id": PROJECT}),
                canonical_document_id="doc-1",
                organization_id=ORG,
                actor_id="bob",
            )

            # The session is reaped.
            await db["contract_upload_sessions"].delete_many({})

            durable = await UploadDurabilityService(db).durable_scope("doc-1")
            assert durable == {"scope_level": "project", "project_id": PROJECT}

    asyncio.run(scenario())


def test_an_expired_session_does_not_become_organisation_scope():
    async def scenario():
        async with _database() as db:
            service = _upload_service(db, {str(Permissions.DOCUMENT_UPLOAD)})
            await service.create_candidate(
                parse_upload_scope({"scope_level": "project", "project_id": PROJECT}),
                canonical_document_id="doc-1",
                organization_id=ORG,
                actor_id="bob",
            )
            await db["contract_upload_sessions"].delete_many({})

            durable = await UploadDurabilityService(db).durable_scope("doc-1")
            assert durable["scope_level"] != "organization"

    asyncio.run(scenario())


def test_the_durable_reader_never_consults_the_session_collection():
    import ast
    import inspect

    from rbac_backend.services import contract_upload_durability

    source = inspect.getsource(contract_upload_durability.UploadDurabilityService.durable_scope)
    tree = ast.parse(source.lstrip())
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert "contract_upload_sessions" not in literals


# --------------------------------------------------------------------------- #
# U12-07 : retry conflict
# --------------------------------------------------------------------------- #


def test_a_retry_cannot_silently_change_scope():
    """U12-07."""

    async def scenario():
        async with _database() as db:
            service = _upload_service(db, {str(Permissions.DOCUMENT_UPLOAD)})
            await service.create_candidate(
                parse_upload_scope({"scope_level": "project", "project_id": PROJECT}),
                canonical_document_id="doc-1",
                organization_id=ORG,
                actor_id="bob",
            )

            durability = UploadDurabilityService(db)
            with pytest.raises(ScopeConflictOnRetry) as excinfo:
                await durability.check_retry("doc-1", scope_level="organization")
            assert "rejected" in str(excinfo.value)

    asyncio.run(scenario())


def test_a_retry_cannot_silently_change_the_project_anchor():
    async def scenario():
        async with _database() as db:
            await db["projects"].insert_one({"_id": "project-other", "organization_id": ORG})
            service = _upload_service(db, {str(Permissions.DOCUMENT_UPLOAD)})
            await service.create_candidate(
                parse_upload_scope({"scope_level": "project", "project_id": PROJECT}),
                canonical_document_id="doc-1",
                organization_id=ORG,
                actor_id="bob",
            )

            with pytest.raises(ScopeConflictOnRetry):
                await UploadDurabilityService(db).check_retry(
                    "doc-1", scope_level="project", project_id="project-other"
                )

    asyncio.run(scenario())


def test_an_identical_retry_is_accepted():
    async def scenario():
        async with _database() as db:
            service = _upload_service(db, {str(Permissions.DOCUMENT_UPLOAD)})
            await service.create_candidate(
                parse_upload_scope({"scope_level": "project", "project_id": PROJECT}),
                canonical_document_id="doc-1",
                organization_id=ORG,
                actor_id="bob",
            )

            await UploadDurabilityService(db).check_retry(
                "doc-1", scope_level="project", project_id=PROJECT
            )

    asyncio.run(scenario())


def test_a_retry_compares_against_the_durable_row_not_the_session():
    async def scenario():
        async with _database() as db:
            service = _upload_service(db, {str(Permissions.DOCUMENT_UPLOAD)})
            await service.create_candidate(
                parse_upload_scope({"scope_level": "project", "project_id": PROJECT}),
                canonical_document_id="doc-1",
                organization_id=ORG,
                actor_id="bob",
            )
            # A session that disagrees must not win the comparison.
            await db["contract_upload_sessions"].insert_one(
                {"_id": "s", "upload_id": "upload-doc-1", "project_id": "project-other"}
            )

            with pytest.raises(ScopeConflictOnRetry):
                await UploadDurabilityService(db).check_retry(
                    "doc-1", scope_level="organization"
                )

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# U12-08 : immutability after promotion
# --------------------------------------------------------------------------- #


async def _promoted_instrument(db) -> str:
    instrument_id = "contract-document:promoted-1"
    await db[CONTRACT_DOCUMENTS_COLLECTION].insert_one(
        {
            "_id": instrument_id,
            "organization_id": ORG,
            "document_id": "doc-1",
            "document_version_id": "doc-1-v1",
            "contract_document_type": ContractDocumentType.GENERAL_CONDITIONS.value,
            "scope_level": "project",
            "project_id": PROJECT,
            "classification_revision": 1,
            "projection_status": "PENDING",
        }
    )
    return instrument_id


def test_scope_level_cannot_change_through_a_generic_metadata_edit():
    """U12-08."""

    async def scenario():
        async with _database() as db:
            instrument_id = await _promoted_instrument(db)

            with pytest.raises(ScopeIsImmutableAfterPromotion):
                await UploadDurabilityService(db).apply_metadata_edit(
                    instrument_id, {"scope_level": "organization"}
                )

            record = await db[CONTRACT_DOCUMENTS_COLLECTION].find_one({"_id": instrument_id})
            assert record["scope_level"] == "project"

    asyncio.run(scenario())


def test_the_project_anchor_cannot_change_through_a_generic_metadata_edit():
    async def scenario():
        async with _database() as db:
            instrument_id = await _promoted_instrument(db)

            with pytest.raises(ScopeIsImmutableAfterPromotion):
                await UploadDurabilityService(db).apply_metadata_edit(
                    instrument_id, {"project_id": "project-other"}
                )

            record = await db[CONTRACT_DOCUMENTS_COLLECTION].find_one({"_id": instrument_id})
            assert record["project_id"] == PROJECT

    asyncio.run(scenario())


def test_a_mixed_edit_is_rejected_whole_not_half_applied():
    async def scenario():
        async with _database() as db:
            instrument_id = await _promoted_instrument(db)

            with pytest.raises(ScopeIsImmutableAfterPromotion):
                await UploadDurabilityService(db).apply_metadata_edit(
                    instrument_id, {"title": "new title", "scope_level": "organization"}
                )

            record = await db[CONTRACT_DOCUMENTS_COLLECTION].find_one({"_id": instrument_id})
            assert "title" not in record
            assert record["scope_level"] == "project"

    asyncio.run(scenario())


def test_an_ordinary_metadata_edit_still_works():
    async def scenario():
        async with _database() as db:
            instrument_id = await _promoted_instrument(db)

            await UploadDurabilityService(db).apply_metadata_edit(
                instrument_id, {"title": "Volume 2 - General Conditions"}
            )

            record = await db[CONTRACT_DOCUMENTS_COLLECTION].find_one({"_id": instrument_id})
            assert record["title"] == "Volume 2 - General Conditions"

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# U12-10 : not evidence-capable
# --------------------------------------------------------------------------- #


def test_uploaded_classified_and_projection_pending_is_not_evidence_capable():
    """U12-10."""

    async def scenario():
        async with _database() as db:
            instrument_id = await _promoted_instrument(db)
            await db["documents"].insert_one(
                {"_id": "doc-1", "organization_id": ORG, "project_id": PROJECT}
            )
            applicability_id = f"applicability:{instrument_id}"
            await db[APPLICABILITY_COLLECTION].insert_one(
                {
                    "_id": applicability_id,
                    "organization_id": ORG,
                    "project_id": PROJECT,
                    "contract_id": CONTRACT,
                    "contract_document_id": instrument_id,
                }
            )
            await db[APPLICABILITY_EVENTS_COLLECTION].insert_one(
                {
                    "_id": "event-1",
                    "applicability_id": applicability_id,
                    "kind": "APPLIED",
                    "effective_at": "2021-01-01",
                }
            )

            scope = AuthorizedContractScope.for_tests(
                organization_id=ORG,
                project_id=PROJECT,
                contract_id=CONTRACT,
                actor_id="alice",
            )
            resolved = await ContractScopeResolver(db).resolve(scope, CurrentState())

            # Uploaded, classified, applicable - and still not evidence, because
            # the projection is pending.
            assert resolved.eligible_document_ids == frozenset()

    asyncio.run(scenario())
