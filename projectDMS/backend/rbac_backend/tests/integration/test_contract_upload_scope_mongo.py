"""Upload scope discriminated union.

U12-01 - explicit organisation scope persists a positive discriminator.
U12-02 - project scope requires a server-validated anchor.
U12-03 - missing or blank project never implies organisation; rejected.
U12-05 - a project-tier actor cannot create an organisation-scope instrument.
U12-06 - a selected contract creates no applied event.
U12-11 - a multipart batch preserves explicit scope for every file.
U12-12 - a legacy omitted project goes to rejection, never organisation scope.

U12-03 and U12-12 need mutation proof for the same reason: both assert that a
falsy value is *not* reinterpreted, and code that reinterprets nothing passes
them for free. The mutations reintroduce the legacy inference, which must turn
them red.

Real Mongo for U12-05: the point is that authorisation is decided by capability
at a resource scope, and the project anchor is validated against real rows
rather than a fixture's say-so.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Set, Tuple

import pytest

pytestmark = pytest.mark.integration

MONGODB_URI = os.environ.get("CONTRACT_UPLOAD_MONGODB_URI")

if not MONGODB_URI:  # pragma: no cover - environment gate
    pytest.skip(
        "CONTRACT_UPLOAD_MONGODB_URI is not set; this suite needs a disposable Mongo",
        allow_module_level=True,
    )

from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402
from pydantic import ValidationError  # noqa: E402

from rbac_backend.core.permissions import Permissions  # noqa: E402
from rbac_backend.models.contract_upload_scope import (  # noqa: E402
    OrganizationScopeUpload,
    ProjectScopeUpload,
    parse_upload_scope,
)
from rbac_backend.services.contract_document_store import LEGAL_COLLECTIONS  # noqa: E402
from rbac_backend.services.contract_migration_reconciliation import (  # noqa: E402
    RECONCILIATION_COLLECTION,
    ScopeClassificationState,
)
from rbac_backend.services.contract_upload_scope_service import (  # noqa: E402
    ORGANIZATION_SCOPE_UPLOAD_CAPABILITIES,
    ContractUploadScopeService,
    ScopeAuthorisationDenied,
    UnknownProjectAnchor,
)

ORG = "org-upload"
PROJECT = "project-upload"


@asynccontextmanager
async def _database():
    client = AsyncIOMotorClient(MONGODB_URI, serverSelectionTimeoutMS=4000)
    name = f"contract_upload_{uuid.uuid4().hex[:10]}"
    db = client[name]
    try:
        await db["projects"].insert_one({"_id": PROJECT, "organization_id": ORG})
        yield db
    finally:
        await client.drop_database(name)
        client.close()


def _capabilities(granted: Set[str]):
    calls: List[Dict[str, Any]] = []

    async def has_capability(*, actor_id, capability, organization_id, project_id):
        calls.append(
            {
                "actor_id": actor_id,
                "capability": str(capability),
                "organization_id": organization_id,
                "project_id": project_id,
            }
        )
        return str(capability) in granted

    return has_capability, calls


def _org_admin():
    return _capabilities({str(c) for c in ORGANIZATION_SCOPE_UPLOAD_CAPABILITIES})


def _project_tier():
    return _capabilities({str(Permissions.DOCUMENT_UPLOAD)})


async def _legal_snapshot(db) -> Dict[str, int]:
    return {name: await db[name].count_documents({}) for name in LEGAL_COLLECTIONS}


# --------------------------------------------------------------------------- #
# U12-03 / U12-12 : nothing falsy becomes a scope
# --------------------------------------------------------------------------- #


def test_an_omitted_discriminator_is_rejected():
    """U12-12. Legacy shape: no scope_level at all."""
    with pytest.raises(ValueError) as excinfo:
        parse_upload_scope({"project_id": None})
    assert "rejected" in str(excinfo.value).lower()


def test_an_empty_discriminator_is_rejected():
    with pytest.raises(ValueError):
        parse_upload_scope({"scope_level": ""})


def test_a_blank_discriminator_is_rejected():
    with pytest.raises(ValueError):
        parse_upload_scope({"scope_level": "   "})


def test_a_missing_project_never_implies_organisation_scope():
    """U12-03."""
    with pytest.raises(ValidationError):
        parse_upload_scope({"scope_level": "project"})


def test_a_blank_project_never_implies_organisation_scope():
    for blank in ("", "   "):
        with pytest.raises(ValidationError):
            parse_upload_scope({"scope_level": "project", "project_id": blank})


def test_an_omitted_project_does_not_become_organisation_scope_anywhere():
    """The legacy inference must not exist on any path."""
    for payload in ({"project_id": ""}, {"project_id": None}, {}):
        with pytest.raises(ValueError):
            scope = parse_upload_scope(payload)
            assert not isinstance(scope, OrganizationScopeUpload)


def test_there_is_no_unknown_scope_option():
    with pytest.raises(ValueError) as excinfo:
        parse_upload_scope({"scope_level": "unknown"})
    assert "unknown" in str(excinfo.value).lower()


def test_project_id_with_organisation_scope_is_rejected_not_ignored():
    with pytest.raises(ValidationError) as excinfo:
        parse_upload_scope({"scope_level": "organization", "project_id": PROJECT})
    assert "rejected rather than ignored" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# U12-01 : the positive discriminator persists
# --------------------------------------------------------------------------- #


def test_explicit_organisation_scope_persists_a_positive_discriminator():
    """U12-01."""

    async def scenario():
        async with _database() as db:
            has_capability, _ = _org_admin()
            service = ContractUploadScopeService(db, has_capability=has_capability)
            scope = parse_upload_scope({"scope_level": "organization"})

            await service.authorise(scope, actor_id="alice", organization_id=ORG)
            candidate = await service.create_candidate(
                scope, canonical_document_id="doc-1", organization_id=ORG, actor_id="alice"
            )

            row = await db[RECONCILIATION_COLLECTION].find_one({"_id": candidate.candidate_id})
            assert row["scope_level"] == "organization"
            assert row["scope_state"] == ScopeClassificationState.ORG_SCOPE_CONFIRMED.value
            assert row["project_id"] is None

    asyncio.run(scenario())


def test_explicit_project_scope_persists_its_anchor():
    async def scenario():
        async with _database() as db:
            has_capability, _ = _project_tier()
            service = ContractUploadScopeService(db, has_capability=has_capability)
            scope = parse_upload_scope({"scope_level": "project", "project_id": PROJECT})

            await service.authorise(scope, actor_id="bob", organization_id=ORG)
            candidate = await service.create_candidate(
                scope, canonical_document_id="doc-2", organization_id=ORG, actor_id="bob"
            )

            row = await db[RECONCILIATION_COLLECTION].find_one({"_id": candidate.candidate_id})
            assert row["scope_level"] == "project"
            assert row["project_id"] == PROJECT
            assert row["scope_state"] == ScopeClassificationState.PROJECT_SCOPE_CONFIRMED.value

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# U12-02 : the anchor is server-validated
# --------------------------------------------------------------------------- #


def test_project_scope_requires_a_server_validated_anchor():
    """U12-02."""

    async def scenario():
        async with _database() as db:
            has_capability, _ = _project_tier()
            service = ContractUploadScopeService(db, has_capability=has_capability)
            scope = parse_upload_scope(
                {"scope_level": "project", "project_id": "project-that-does-not-exist"}
            )

            with pytest.raises(UnknownProjectAnchor):
                await service.create_candidate(
                    scope, canonical_document_id="doc-3", organization_id=ORG, actor_id="bob"
                )

            assert await db[RECONCILIATION_COLLECTION].count_documents({}) == 0

    asyncio.run(scenario())


def test_a_project_from_another_organisation_is_not_a_valid_anchor():
    async def scenario():
        async with _database() as db:
            await db["projects"].insert_one(
                {"_id": "project-elsewhere", "organization_id": "org-other"}
            )
            has_capability, _ = _project_tier()
            service = ContractUploadScopeService(db, has_capability=has_capability)
            scope = parse_upload_scope(
                {"scope_level": "project", "project_id": "project-elsewhere"}
            )

            with pytest.raises(UnknownProjectAnchor):
                await service.create_candidate(
                    scope, canonical_document_id="doc-4", organization_id=ORG, actor_id="bob"
                )

    asyncio.run(scenario())


def test_an_unknown_anchor_is_never_downgraded_to_organisation_scope():
    async def scenario():
        async with _database() as db:
            has_capability, _ = _org_admin()
            service = ContractUploadScopeService(db, has_capability=has_capability)
            scope = parse_upload_scope({"scope_level": "project", "project_id": "nope"})

            with pytest.raises(UnknownProjectAnchor):
                await service.create_candidate(
                    scope, canonical_document_id="doc-5", organization_id=ORG, actor_id="alice"
                )

            rows = await db[RECONCILIATION_COLLECTION].find({}).to_list(length=None)
            assert rows == []

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# U12-05 : capability, not role name
# --------------------------------------------------------------------------- #


def test_a_project_tier_actor_cannot_create_an_organisation_scope_instrument():
    """U12-05."""

    async def scenario():
        async with _database() as db:
            has_capability, _ = _project_tier()
            service = ContractUploadScopeService(db, has_capability=has_capability)
            scope = parse_upload_scope({"scope_level": "organization"})

            with pytest.raises(ScopeAuthorisationDenied) as excinfo:
                await service.authorise(scope, actor_id="bob", organization_id=ORG)

            message = str(excinfo.value)
            assert str(Permissions.CONTRACT_MASTER_MANAGE) in message

    asyncio.run(scenario())


def test_all_three_capabilities_are_required_together():
    async def scenario():
        async with _database() as db:
            full = {str(c) for c in ORGANIZATION_SCOPE_UPLOAD_CAPABILITIES}
            for missing in full:
                has_capability, _ = _capabilities(full - {missing})
                service = ContractUploadScopeService(db, has_capability=has_capability)
                with pytest.raises(ScopeAuthorisationDenied):
                    await service.authorise(
                        parse_upload_scope({"scope_level": "organization"}),
                        actor_id="alice",
                        organization_id=ORG,
                    )

    asyncio.run(scenario())


def test_capabilities_are_checked_at_a_resource_scope():
    async def scenario():
        async with _database() as db:
            has_capability, calls = _org_admin()
            service = ContractUploadScopeService(db, has_capability=has_capability)
            await service.authorise(
                parse_upload_scope({"scope_level": "organization"}),
                actor_id="alice",
                organization_id=ORG,
            )

            assert calls
            for call in calls:
                assert call["organization_id"] == ORG
                assert call["project_id"] is None

    asyncio.run(scenario())


def test_no_role_name_appears_anywhere_in_the_authorisation_path():
    import inspect

    from rbac_backend.core.security import ROLE_ALIASES
    from rbac_backend.services import contract_upload_scope_service

    source = inspect.getsource(contract_upload_scope_service)
    for role in set(ROLE_ALIASES) | set(ROLE_ALIASES.values()):
        assert str(role) not in source, role


# --------------------------------------------------------------------------- #
# U12-06 : an upload is not a legal fact
# --------------------------------------------------------------------------- #


def test_a_selected_contract_creates_no_applied_event():
    """U12-06."""

    async def scenario():
        async with _database() as db:
            before = await _legal_snapshot(db)
            has_capability, _ = _project_tier()
            service = ContractUploadScopeService(db, has_capability=has_capability)
            scope = parse_upload_scope({"scope_level": "project", "project_id": PROJECT})

            candidate = await service.create_candidate(
                scope,
                canonical_document_id="doc-6",
                organization_id=ORG,
                actor_id="bob",
                selected_contract_id="contract-7",
            )

            # Nothing authoritative was created.
            assert await _legal_snapshot(db) == before

            row = await db[RECONCILIATION_COLLECTION].find_one({"_id": candidate.candidate_id})
            # The selection is recorded as context, and named as such.
            assert row["selected_contract_id"] == "contract-7"
            assert "applicability" not in row
            assert "contract_document_type" not in row

    asyncio.run(scenario())


def test_an_upload_produces_a_candidate_not_an_instrument():
    async def scenario():
        async with _database() as db:
            has_capability, _ = _org_admin()
            service = ContractUploadScopeService(db, has_capability=has_capability)
            candidate = await service.create_candidate(
                parse_upload_scope({"scope_level": "organization"}),
                canonical_document_id="doc-7",
                organization_id=ORG,
                actor_id="alice",
            )

            row = await db[RECONCILIATION_COLLECTION].find_one({"_id": candidate.candidate_id})
            assert "classification_revision" not in row
            assert "projection_status" not in row

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# U12-11 : a batch shares one scope
# --------------------------------------------------------------------------- #


def test_a_batch_preserves_explicit_scope_for_every_file():
    """U12-11."""

    async def scenario():
        async with _database() as db:
            has_capability, _ = _project_tier()
            service = ContractUploadScopeService(db, has_capability=has_capability)
            scope = parse_upload_scope({"scope_level": "project", "project_id": PROJECT})

            created = await service.create_batch_candidates(
                scope,
                canonical_document_ids=["doc-a", "doc-b", "doc-c"],
                organization_id=ORG,
                actor_id="bob",
            )

            assert len(created) == 3
            rows = await db[RECONCILIATION_COLLECTION].find({}).to_list(length=None)
            assert len(rows) == 3
            assert {row["scope_level"] for row in rows} == {"project"}
            assert {row["project_id"] for row in rows} == {PROJECT}

    asyncio.run(scenario())


def test_type_stays_per_file_while_scope_is_shared():
    async def scenario():
        async with _database() as db:
            has_capability, _ = _project_tier()
            service = ContractUploadScopeService(db, has_capability=has_capability)
            await service.create_batch_candidates(
                parse_upload_scope({"scope_level": "project", "project_id": PROJECT}),
                canonical_document_ids=["doc-a", "doc-b"],
                organization_id=ORG,
                actor_id="bob",
            )

            rows = await db[RECONCILIATION_COLLECTION].find({}).to_list(length=None)
            # Every file is independently unclassified; the batch decided scope
            # only.
            assert {row["type_state"] for row in rows} == {"TYPE_UNKNOWN"}

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# candidate creation never rewrites authority
# --------------------------------------------------------------------------- #
#
# candidate.project_id is a trust anchor (services/contract_candidate_authority.py)
# and the candidate id is deterministic, so a replayed or repeated upload reaches
# an existing row. Creation is insert-only for everything that row decides.

PROJECT_OTHER = "project-upload-other"
ORG_FOREIGN = "org-upload-foreign"


def _candidate_id(document_id: str) -> str:
    from rbac_backend.services.contract_migration_reconciliation import candidate_identity

    return candidate_identity(module="contracts", canonical_document_id=document_id)


async def _seed_row(db, document_id: str, **fields) -> Dict[str, Any]:
    row = {
        "_id": _candidate_id(document_id),
        "candidate_id": _candidate_id(document_id),
        "canonical_document_id": document_id,
        "organization_id": ORG,
        "module": "contracts",
        "scope_level": "project",
        "project_id": PROJECT,
        "scope_state": "PROJECT_SCOPE_CONFIRMED",
        "type_state": "TYPE_UNKNOWN",
        **fields,
    }
    await db[RECONCILIATION_COLLECTION].insert_one(row)
    return await db[RECONCILIATION_COLLECTION].find_one({"_id": row["_id"]})


def _decided(**extra) -> Dict[str, Any]:
    from datetime import datetime

    return {
        "type_state": "TYPE_RESOLVED",
        "contract_document_type": "general_conditions",
        "adjudicated_by": "user-org-admin",
        "adjudicated_at": datetime(2026, 9, 1, 12, 0),
        "adjudication_reason": "reviewed",
        "source_fingerprint": "sha-reviewed",
        **extra,
    }


def _project_scope(project_id: str = PROJECT):
    return parse_upload_scope({"scope_level": "project", "project_id": project_id})


def _organization_scope():
    return parse_upload_scope({"scope_level": "organization"})


async def _row(db, document_id: str):
    return await db[RECONCILIATION_COLLECTION].find_one({"_id": _candidate_id(document_id)})


def test_an_adjudicated_candidate_is_never_rewritten_by_a_later_upload():
    async def scenario():
        from rbac_backend.services.contract_upload_scope_service import (
            CandidateScopeConflict,
        )

        async with _database() as db:
            await db["projects"].insert_one({"_id": PROJECT_OTHER, "organization_id": ORG})
            before = await _seed_row(db, "doc-decided", **_decided())
            has_capability, _ = _org_admin()
            service = ContractUploadScopeService(db, has_capability=has_capability)

            for scope in (_organization_scope(), _project_scope(PROJECT_OTHER)):
                with pytest.raises(CandidateScopeConflict):
                    await service.create_candidate(
                        scope,
                        canonical_document_id="doc-decided",
                        organization_id=ORG,
                        actor_id="bob",
                    )
                assert await _row(db, "doc-decided") == before

            # The same scope again is a compatible replay: nothing reset.
            replay = await service.create_candidate(
                _project_scope(),
                canonical_document_id="doc-decided",
                organization_id=ORG,
                actor_id="bob",
            )
            assert await _row(db, "doc-decided") == before
            assert (replay.scope_level, replay.project_id) == ("project", PROJECT)

    asyncio.run(scenario())


def test_a_promoted_candidate_is_never_rewritten_by_a_later_upload():
    async def scenario():
        from datetime import datetime

        from rbac_backend.services.contract_upload_scope_service import (
            CandidateScopeConflict,
        )

        async with _database() as db:
            before = await _seed_row(
                db,
                "doc-promoted",
                **_decided(promoted=True, promoted_at=datetime(2026, 9, 2, 9, 0)),
            )
            has_capability, _ = _org_admin()
            service = ContractUploadScopeService(db, has_capability=has_capability)

            await service.create_candidate(
                _project_scope(),
                canonical_document_id="doc-promoted",
                organization_id=ORG,
                actor_id="bob",
            )
            assert await _row(db, "doc-promoted") == before
            with pytest.raises(CandidateScopeConflict):
                await service.create_candidate(
                    _organization_scope(),
                    canonical_document_id="doc-promoted",
                    organization_id=ORG,
                    actor_id="bob",
                )
            assert await _row(db, "doc-promoted") == before

    asyncio.run(scenario())


def test_another_organisations_candidate_is_refused_and_untouched():
    async def scenario():
        from rbac_backend.services.contract_upload_scope_service import (
            CandidateScopeConflict,
        )

        async with _database() as db:
            before = await _seed_row(
                db,
                "doc-foreign",
                organization_id=ORG_FOREIGN,
                project_id="project-foreign",
                **_decided(),
            )
            has_capability, _ = _org_admin()
            service = ContractUploadScopeService(db, has_capability=has_capability)

            for scope in (_project_scope(), _organization_scope()):
                with pytest.raises(CandidateScopeConflict) as refused:
                    await service.create_candidate(
                        scope,
                        canonical_document_id="doc-foreign",
                        organization_id=ORG,
                        actor_id="bob",
                    )
                # The refusal names neither organisation.
                assert ORG_FOREIGN not in str(refused.value)
                assert await _row(db, "doc-foreign") == before
            assert await db[RECONCILIATION_COLLECTION].count_documents({}) == 1

    asyncio.run(scenario())


def test_an_exact_replay_is_idempotent_and_keeps_later_decisions():
    async def scenario():
        async with _database() as db:
            has_capability, _ = _project_tier()
            service = ContractUploadScopeService(db, has_capability=has_capability)
            first = await service.create_candidate(
                _project_scope(),
                canonical_document_id="doc-replay",
                organization_id=ORG,
                actor_id="bob",
                selected_contract_id="contract-1",
            )
            # A decision is recorded on it afterwards.
            await db[RECONCILIATION_COLLECTION].update_one(
                {"_id": first.candidate_id}, {"$set": _decided()}
            )
            before = await _row(db, "doc-replay")

            second = await service.create_candidate(
                _project_scope(),
                canonical_document_id="doc-replay",
                organization_id=ORG,
                actor_id="carol",
                selected_contract_id="contract-2",
            )

            assert second == first
            assert await _row(db, "doc-replay") == before

    asyncio.run(scenario())


def test_a_conflicting_same_org_replay_fails_closed_without_mutation():
    async def scenario():
        from rbac_backend.services.contract_upload_scope_service import (
            CandidateScopeConflict,
        )

        async with _database() as db:
            await db["projects"].insert_one({"_id": PROJECT_OTHER, "organization_id": ORG})
            has_capability, _ = _org_admin()
            service = ContractUploadScopeService(db, has_capability=has_capability)
            await service.create_candidate(
                _project_scope(),
                canonical_document_id="doc-conflict",
                organization_id=ORG,
                actor_id="bob",
            )
            before = await _row(db, "doc-conflict")

            for scope in (_project_scope(PROJECT_OTHER), _organization_scope()):
                with pytest.raises(CandidateScopeConflict):
                    await service.create_candidate(
                        scope,
                        canonical_document_id="doc-conflict",
                        organization_id=ORG,
                        actor_id="bob",
                    )
                assert await _row(db, "doc-conflict") == before
            assert before["project_id"] == PROJECT

    asyncio.run(scenario())


def test_a_materialised_legacy_row_is_not_given_an_upload_scope():
    """Inventory rows carry no scope decision; an upload must not invent one onto them."""

    async def scenario():
        from rbac_backend.services.contract_upload_scope_service import (
            CandidateScopeConflict,
        )

        async with _database() as db:
            before = await _seed_row(
                db,
                "doc-legacy",
                scope_state="UNRESOLVED",
                project_id=None,
                scope_level=None,
                session_evidence={"project_id": PROJECT},
            )
            has_capability, _ = _project_tier()
            service = ContractUploadScopeService(db, has_capability=has_capability)
            with pytest.raises(CandidateScopeConflict):
                await service.create_candidate(
                    _project_scope(),
                    canonical_document_id="doc-legacy",
                    organization_id=ORG,
                    actor_id="bob",
                )
            assert await _row(db, "doc-legacy") == before

    asyncio.run(scenario())


def test_the_organisation_id_is_stored_and_matched_as_a_string():
    async def scenario():
        from bson import ObjectId

        async with _database() as db:
            org_oid = ObjectId()
            await db["projects"].insert_one({"_id": "project-oid", "organization_id": str(org_oid)})
            has_capability, _ = _project_tier()
            service = ContractUploadScopeService(db, has_capability=has_capability)
            created = await service.create_candidate(
                _project_scope("project-oid"),
                canonical_document_id="doc-oid",
                organization_id=org_oid,
                actor_id="bob",
            )
            row = await _row(db, "doc-oid")
            assert row["organization_id"] == str(org_oid)
            assert created.organization_id == str(org_oid)

    asyncio.run(scenario())


def test_a_batch_cannot_rewrite_an_existing_candidate():
    async def scenario():
        from rbac_backend.services.contract_upload_scope_service import (
            CandidateScopeConflict,
        )

        async with _database() as db:
            decided = await _seed_row(db, "doc-b-decided", **_decided())
            foreign = await _seed_row(
                db, "doc-b-foreign", organization_id=ORG_FOREIGN, **_decided()
            )
            has_capability, _ = _project_tier()
            service = ContractUploadScopeService(db, has_capability=has_capability)

            with pytest.raises(CandidateScopeConflict):
                await service.create_batch_candidates(
                    _project_scope(),
                    canonical_document_ids=["doc-b-new", "doc-b-decided", "doc-b-foreign"],
                    organization_id=ORG,
                    actor_id="bob",
                )

            assert await _row(db, "doc-b-decided") == decided
            assert await _row(db, "doc-b-foreign") == foreign
            new = await _row(db, "doc-b-new")
            assert (new["organization_id"], new["project_id"]) == (ORG, PROJECT)

    asyncio.run(scenario())
