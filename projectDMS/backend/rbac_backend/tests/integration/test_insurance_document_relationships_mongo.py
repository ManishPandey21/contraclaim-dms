"""Real Mongo and local-file checks for the Insurance tracer.

Opt-in only. The URI must name a disposable test replica set; every test uses
and drops a random database while all legacy/canonical files live under
pytest's temporary directory.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from rbac_backend.migrations.v20260820_0001_entity_document_links import upgrade
from rbac_backend.models.document_relationship import DocumentRelationshipInput
from rbac_backend.routers.insurance import delete_insurance
from rbac_backend.services.document_relationship_service import DocumentRelationshipService
from rbac_backend.services.file_object_service import FileObjectService
from rbac_backend.services.file_service import SecureFileService
from rbac_backend.services.insurance_document_link_migration import (
    canonicalize_legacy_insurance_file,
    classify_legacy_insurance_evidence,
)
from rbac_backend.services.insurance_service import InsuranceService
from rbac_backend.tests.integration.test_claim_document_relationships_mongo import (
    InjectedFailure,
    _AllowPolicy,
    _FailingCollection,
    _FaultDatabase,
)


MONGODB_URI_ENV = "INSURANCE_TRACER_MONGODB_URI"
PDF_BYTES = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\n%%EOF\n"


def _actor() -> Any:
    return SimpleNamespace(id="insurance-tracer-verifier", organization_id="org-1")


#: Collections written inside the relationship/backfill transaction.
#:
#: MongoDB cannot create the same namespace implicitly in two transactions at
#: once: the loser aborts with a WriteConflict carrying TransientTransactionError.
#: A database that has never written one of these can therefore lose a migration
#: to a namespace race that has nothing to do with the data.
#:
#: A real deployment already gets this guarantee: `core.database.ensure_indexes`
#: (invoked by migration v20260629_0001_startup_index_baseline) creates the
#: `audit_events` indexes, and v20260820_0001 creates `entity_document_links`.
#: This harness runs only the single relationship migration, so it must give the
#: same guarantee before any concurrent transactional work begins.
#:
#: This is namespace readiness only — deliberately no index opinions, so it can
#: never drift from the real bootstrap. `test_backfill_cold_start_readiness`
#: pins that every name here is one the real bootstrap owns, and that the list
#: covers every collection the transaction actually writes.
TRANSACTION_PARTICIPANT_COLLECTIONS = (
    "entity_document_links",
    "documents",
    "audit_events",
)


async def _new_database() -> tuple[Any, Any]:
    uri = os.getenv(MONGODB_URI_ENV)
    if not uri:
        pytest.skip(f"set {MONGODB_URI_ENV} to a test-only MongoDB replica set")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5_000)
    hello = await client.admin.command("hello")
    if not hello.get("setName") or not hello.get("isWritablePrimary"):
        client.close()
        pytest.fail(f"{MONGODB_URI_ENV} is not a writable replica set")
    database = client[f"insurance_tracer_verification_{uuid.uuid4().hex}"]
    await upgrade(database, dry_run=False)
    existing = set(await database.list_collection_names())
    for name in TRANSACTION_PARTICIPANT_COLLECTIONS:
        if name not in existing:
            await database.create_collection(name)
    return client, database


async def _seed_policy(database: Any, policy_id: str, token: str) -> None:
    await database.insurance_policies.insert_one(
        {
            "_id": policy_id,
            "policy_number": f"POL-{policy_id}",
            "insurance_type": "Marine Cargo Insurance",
            "organization_id": "org-1",
            "project_id": "project-1",
            "date_of_issue": datetime(2026, 8, 1, tzinfo=timezone.utc),
            "date_of_expiry": datetime(2027, 8, 1, tzinfo=timezone.utc),
            "document_id": token,
            "document_name": f"{policy_id}.pdf",
        }
    )


class _CanonicalFileController:
    """Exercises the real FileObject/DocumentVersion services on real Mongo."""

    def __init__(
        self,
        database: Any,
        canonical_root: Path,
        *,
        fail_before_store: bool = False,
        delete_target_after_store: bool = False,
    ):
        self.database = database
        self.fail_before_store = fail_before_store
        self.delete_target_after_store = delete_target_after_store
        self.file_objects = FileObjectService(
            database,
            file_service=SecureFileService(base_dir=str(canonical_root)),
        )
        self.calls = 0

    async def create_document(self, *, file: Any, organization_id: str, project_id: str, **kwargs: Any):
        self.calls += 1
        if self.fail_before_store:
            raise InjectedFailure("injected canonical storage failure")
        content = await file.read()
        result = await self.file_objects.store_bytes(
            content=content,
            organization_id=organization_id,
            project_id=project_id,
            original_filename=str(file.filename),
            storage_key=f"insurance/{uuid.uuid4().hex}/{file.filename}",
            current_user=kwargs.get("current_user"),
            document_type="incoming",
            content_type="application/pdf",
        )
        document_id = str(uuid.uuid4())
        await self.database.documents.insert_one(
            {
                "_id": document_id,
                "filename": str(file.filename),
                "organization_id": organization_id,
                "project_id": project_id,
                "processing_status": "metadata_extracted",
                "lifecycle_state": "active",
                "file_object_id": result["file_object_id"],
                "sha256": result["sha256"],
            }
        )
        version_id = await self.file_objects.attach_document_version(
            document_id=document_id,
            file_object_id=result["file_object_id"],
            metadata_snapshot={"source": "insurance_migration"},
            current_user=kwargs.get("current_user"),
            reason="insurance_legacy_canonicalization",
        )
        await self.database.documents.update_one(
            {"_id": document_id}, {"$set": {"current_version_id": version_id}}
        )
        if self.delete_target_after_store:
            self.delete_target_after_store = False
            await self.database.insurance_policies.delete_many({})
        return SimpleNamespace(id=document_id)

    async def compensate_failed_creation(self, document_id: str, **kwargs: Any) -> None:
        document = await self.database.documents.find_one({"_id": document_id})
        file_object_id = str((document or {}).get("file_object_id") or "")
        await self.database.document_versions.delete_many({"document_id": document_id})
        await self.database.documents.delete_one({"_id": document_id})
        await self.database.file_objects.update_one(
            {"_id": file_object_id},
            {
                "$pull": {"document_ids": document_id},
                "$unset": {"document_id": ""},
            },
        )


@pytest.mark.asyncio
async def test_real_mongo_concurrent_link_and_audit_rollback(tmp_path: Path) -> None:
    client, database = await _new_database()
    try:
        await _seed_policy(database, "insurance-1", "legacy-1.pdf")
        for document_id in ("doc-1", "doc-2"):
            await database.documents.insert_one(
                {
                    "_id": document_id,
                    "filename": f"{document_id}.pdf",
                    "organization_id": "org-1",
                    "project_id": "project-1",
                    "processing_status": "metadata_extracted",
                    "lifecycle_state": "active",
                    "current_version_id": f"{document_id}:v1",
                }
            )
            await database.document_versions.insert_one(
                {
                    "_id": f"{document_id}:v1",
                    "document_id": document_id,
                    "file_object_id": f"{document_id}:file",
                    "version_number": 1,
                    "is_current": True,
                }
            )
        request = [DocumentRelationshipInput(document_id="doc-1", relationship_role="policy")]
        service = DocumentRelationshipService(database, policy=_AllowPolicy())
        first, second = await asyncio.gather(
            service.link_batch(_actor(), "insurance", "insurance-1", request, idempotency_key="same"),
            service.link_batch(_actor(), "insurance", "insurance-1", request, idempotency_key="same"),
        )
        assert first[0].id == second[0].id
        assert await database.entity_document_links.count_documents({"removed_at": None}) == 1
        assert await database.audit_events.count_documents({"action": "document_relationship.linked"}) == 1

        fault_database = _FaultDatabase(
            database,
            audit_events=_FailingCollection(
                database.audit_events,
                fail_insert=lambda row: row.get("action") == "document_relationship.linked",
            ),
        )
        with pytest.raises(InjectedFailure, match="injected insert failure"):
            await DocumentRelationshipService(fault_database, policy=_AllowPolicy()).link_batch(
                _actor(),
                "insurance",
                "insurance-1",
                [DocumentRelationshipInput(document_id="doc-2", relationship_role="certificate")],
                idempotency_key="rollback",
            )
        assert await database.entity_document_links.count_documents({"document_id": "doc-2"}) == 0
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_concurrent_canonicalization_is_idempotent(tmp_path: Path) -> None:
    client, database = await _new_database()
    try:
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        source = legacy_root / "same.pdf"
        source.write_bytes(PDF_BYTES)
        await _seed_policy(database, "insurance-1", source.name)
        controller = _CanonicalFileController(database, tmp_path / "canonical")

        first, second = await asyncio.gather(
            canonicalize_legacy_insurance_file(
                database, insurance_id="insurance-1", legacy_root=legacy_root,
                current_user=_actor(), controller=controller, policy=_AllowPolicy(),
                migration_run_id="run-a",
            ),
            canonicalize_legacy_insurance_file(
                database, insurance_id="insurance-1", legacy_root=legacy_root,
                current_user=_actor(), controller=controller, policy=_AllowPolicy(),
                migration_run_id="run-b",
            ),
        )

        assert first["document_id"] == second["document_id"]
        assert first["relationship_id"] == second["relationship_id"]
        assert await database.file_objects.count_documents({}) == 1
        assert await database.documents.count_documents({}) == 1
        assert await database.document_versions.count_documents({}) == 1
        assert await database.entity_document_links.count_documents({"removed_at": None}) == 1
        assert source.read_bytes() == PDF_BYTES
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_duplicate_bytes_require_explicit_document_reuse_approval(tmp_path: Path) -> None:
    client, database = await _new_database()
    try:
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        (legacy_root / "first.pdf").write_bytes(PDF_BYTES)
        (legacy_root / "second.pdf").write_bytes(PDF_BYTES)
        await _seed_policy(database, "insurance-1", "first.pdf")
        await _seed_policy(database, "insurance-2", "second.pdf")
        controller = _CanonicalFileController(database, tmp_path / "canonical")

        first = await canonicalize_legacy_insurance_file(
            database, insurance_id="insurance-1", legacy_root=legacy_root,
            current_user=_actor(), controller=controller, policy=_AllowPolicy(),
            migration_run_id="run-first",
        )
        second = await canonicalize_legacy_insurance_file(
            database, insurance_id="insurance-2", legacy_root=legacy_root,
            current_user=_actor(), controller=controller, policy=_AllowPolicy(),
            migration_run_id="run-second",
            approved_document_id=first["document_id"],
        )

        assert second["reused_document"] is True
        assert second["document_id"] == first["document_id"]
        assert controller.calls == 1
        assert await database.file_objects.count_documents({}) == 1
        assert await database.documents.count_documents({}) == 1
        assert await database.entity_document_links.count_documents({"removed_at": None}) == 2
        assert (legacy_root / "first.pdf").exists()
        assert (legacy_root / "second.pdf").exists()
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_concurrent_same_bytes_across_policies_do_not_duplicate_authority(
    tmp_path: Path,
) -> None:
    client, database = await _new_database()
    try:
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        (legacy_root / "first.pdf").write_bytes(PDF_BYTES)
        (legacy_root / "second.pdf").write_bytes(PDF_BYTES)
        await _seed_policy(database, "insurance-1", "first.pdf")
        await _seed_policy(database, "insurance-2", "second.pdf")
        controller = _CanonicalFileController(database, tmp_path / "canonical")

        outcomes = await asyncio.gather(
            canonicalize_legacy_insurance_file(
                database, insurance_id="insurance-1", legacy_root=legacy_root,
                current_user=_actor(), controller=controller, policy=_AllowPolicy(),
                migration_run_id="run-first",
            ),
            canonicalize_legacy_insurance_file(
                database, insurance_id="insurance-2", legacy_root=legacy_root,
                current_user=_actor(), controller=controller, policy=_AllowPolicy(),
                migration_run_id="run-second",
            ),
            return_exceptions=True,
        )

        assert len([outcome for outcome in outcomes if isinstance(outcome, dict)]) == 1
        rejected = [outcome for outcome in outcomes if isinstance(outcome, Exception)]
        assert len(rejected) == 1
        assert "manual review" in str(rejected[0]).lower()
        assert await database.file_objects.count_documents({}) == 1
        assert await database.documents.count_documents({}) == 1
        assert await database.document_versions.count_documents({}) == 1
        assert await database.entity_document_links.count_documents({"removed_at": None}) == 1
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_stale_processing_claim_is_reclaimed(tmp_path: Path) -> None:
    client, database = await _new_database()
    try:
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        source = legacy_root / "stale.pdf"
        source.write_bytes(PDF_BYTES)
        await _seed_policy(database, "insurance-1", source.name)
        digest = hashlib.sha256(PDF_BYTES).hexdigest()
        await database.insurance_document_migration_claims.insert_one(
            {
                "_id": f"insurance-bytes:org-1:project-1:{digest}",
                "insurance_id": "insurance-1",
                "sha256": digest,
                "status": "processing",
                "owner_token": "crashed-owner",
                "lease_expires_at": datetime.now(timezone.utc) - timedelta(minutes=1),
            }
        )

        result = await canonicalize_legacy_insurance_file(
            database, insurance_id="insurance-1", legacy_root=legacy_root,
            current_user=_actor(),
            controller=_CanonicalFileController(database, tmp_path / "canonical"),
            policy=_AllowPolicy(), migration_run_id="run-recovery",
        )

        assert result["document_id"]
        claim = await database.insurance_document_migration_claims.find_one(
            {"_id": f"insurance-bytes:org-1:project-1:{digest}"}
        )
        assert claim["status"] == "complete"
        assert claim["owner_token"] != "crashed-owner"
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_failed_canonicalization_preserves_source_and_authority(tmp_path: Path) -> None:
    client, database = await _new_database()
    try:
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        source = legacy_root / "failure.pdf"
        source.write_bytes(PDF_BYTES)
        await _seed_policy(database, "insurance-1", source.name)
        controller = _CanonicalFileController(
            database, tmp_path / "canonical", fail_before_store=True
        )

        with pytest.raises(InjectedFailure, match="canonical storage failure"):
            await canonicalize_legacy_insurance_file(
                database, insurance_id="insurance-1", legacy_root=legacy_root,
                current_user=_actor(), controller=controller, policy=_AllowPolicy(),
                migration_run_id="run-failure",
            )
        assert source.read_bytes() == PDF_BYTES
        assert await database.file_objects.count_documents({}) == 0
        assert await database.documents.count_documents({}) == 0
        assert await database.document_versions.count_documents({}) == 0
        assert await database.entity_document_links.count_documents({}) == 0
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_post_store_link_failure_compensates_canonical_artifacts(
    tmp_path: Path,
) -> None:
    client, database = await _new_database()
    try:
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        source = legacy_root / "post-store.pdf"
        source.write_bytes(PDF_BYTES)
        await _seed_policy(database, "insurance-1", source.name)
        controller = _CanonicalFileController(
            database,
            tmp_path / "canonical",
            delete_target_after_store=True,
        )

        with pytest.raises(Exception, match="target not found"):
            await canonicalize_legacy_insurance_file(
                database, insurance_id="insurance-1", legacy_root=legacy_root,
                current_user=_actor(), controller=controller, policy=_AllowPolicy(),
                migration_run_id="run-post-store-failure",
            )

        assert source.read_bytes() == PDF_BYTES
        assert await database.file_objects.count_documents({}) == 1
        assert await database.documents.count_documents({}) == 0
        assert await database.document_versions.count_documents({}) == 0
        assert await database.entity_document_links.count_documents({}) == 0
        assert len(list((tmp_path / "canonical").rglob("*.pdf"))) == 1

        await _seed_policy(database, "insurance-1", source.name)
        recovered = await canonicalize_legacy_insurance_file(
            database, insurance_id="insurance-1", legacy_root=legacy_root,
            current_user=_actor(), controller=controller, policy=_AllowPolicy(),
            migration_run_id="run-post-store-retry",
        )
        assert recovered["document_id"]
        assert await database.file_objects.count_documents({}) == 1
        assert await database.documents.count_documents({}) == 1
        assert await database.document_versions.count_documents({}) == 1
        assert await database.entity_document_links.count_documents({"removed_at": None}) == 1
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_hash_mismatch_dry_run_is_read_only(tmp_path: Path) -> None:
    client, database = await _new_database()
    try:
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        source = legacy_root / "mismatch.pdf"
        source.write_bytes(PDF_BYTES)
        await _seed_policy(database, "insurance-1", source.name)
        await database.insurance_policies.update_one(
            {"_id": "insurance-1"}, {"$set": {"legacy_sha256": "0" * 64}}
        )
        before = await database.insurance_policies.find_one({"_id": "insurance-1"})

        report = await classify_legacy_insurance_evidence(database, legacy_root=legacy_root)

        assert report["candidates"][0]["classification"] == "hash_mismatch"
        assert report["candidates"][0]["sha256"] == hashlib.sha256(PDF_BYTES).hexdigest()
        assert await database.insurance_policies.find_one({"_id": "insurance-1"}) == before
        assert await database.entity_document_links.count_documents({}) == 0
        assert source.read_bytes() == PDF_BYTES
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_missing_legacy_source_fails_without_authoritative_state(
    tmp_path: Path,
) -> None:
    client, database = await _new_database()
    try:
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        await _seed_policy(database, "insurance-1", "missing.pdf")

        with pytest.raises(ValueError, match="candidate not found"):
            await canonicalize_legacy_insurance_file(
                database,
                insurance_id="insurance-1",
                legacy_root=legacy_root,
                current_user=_actor(),
                controller=_CanonicalFileController(database, tmp_path / "canonical"),
                policy=_AllowPolicy(),
                migration_run_id="run-missing-source",
            )

        assert await database.insurance_document_migration_claims.count_documents({}) == 0
        assert await database.documents.count_documents({}) == 0
        assert await database.document_versions.count_documents({}) == 0
        assert await database.file_objects.count_documents({}) == 0
        assert await database.entity_document_links.count_documents({}) == 0
        assert not (legacy_root / "missing.pdf").exists()
    finally:
        await client.drop_database(database.name)
        client.close()


@pytest.mark.asyncio
async def test_real_mongo_insurance_deletion_preserves_documents_files_and_legacy_source(
    tmp_path: Path,
) -> None:
    client, database = await _new_database()
    try:
        legacy_root = tmp_path / "legacy"
        legacy_root.mkdir()
        source = legacy_root / "policy.pdf"
        source.write_bytes(PDF_BYTES)
        await _seed_policy(database, "insurance-1", source.name)
        controller = _CanonicalFileController(database, tmp_path / "canonical")
        migrated = await canonicalize_legacy_insurance_file(
            database,
            insurance_id="insurance-1",
            legacy_root=legacy_root,
            current_user=_actor(),
            controller=controller,
            policy=_AllowPolicy(),
            migration_run_id="run-delete-preservation",
        )
        await delete_insurance(
            insurance_id="insurance-1",
            db=database,
            current_user=_actor(),
            policy=_AllowPolicy(),
        )

        assert await database.insurance_policies.count_documents({"_id": "insurance-1"}) == 0
        assert await database.documents.count_documents({"_id": migrated["document_id"]}) == 1
        assert await database.document_versions.count_documents({"document_id": migrated["document_id"]}) == 1
        assert await database.file_objects.count_documents({}) == 1
        assert await database.entity_document_links.count_documents({"removed_at": None}) == 0
        assert await database.entity_document_links.count_documents({"removed_at": {"$ne": None}}) == 1
        assert await database.audit_events.count_documents({"action": "insurance.deleted"}) == 1
        # The surviving audit must be the transactional one emitted by delete_target,
        # which alone carries the deletion reason.
        deleted_audit = await database.audit_events.find_one({"action": "insurance.deleted"})
        assert deleted_audit is not None
        assert deleted_audit.get("reason") == "Insurance policy deleted"
        assert await database.audit_events.count_documents(
            {"action": "document_relationship.unlinked"}
        ) == 1
        assert source.read_bytes() == PDF_BYTES
        assert len(list((tmp_path / "canonical").rglob("*.pdf"))) == 1
    finally:
        await client.drop_database(database.name)
        client.close()
