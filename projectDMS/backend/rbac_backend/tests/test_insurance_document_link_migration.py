from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pymongo.errors import DuplicateKeyError

from rbac_backend.core.permissions import Permissions
from rbac_backend.models.document import Document

from rbac_backend.services.insurance_document_link_migration import (
    canonicalize_legacy_insurance_file,
    classify_legacy_insurance_evidence,
)
from rbac_backend.tests.test_claim_document_relationships import (
    _Collection,
    _Database,
    _PermissionPolicy,
)


class _MigrationDocumentController:
    def __init__(self, db: _Database) -> None:
        self.db = db
        self.calls = 0

    async def create_document(self, **kwargs):
        self.calls += 1
        content = await kwargs["file"].read()
        sha256 = hashlib.sha256(content).hexdigest()
        document = {
            "_id": "canonical-insurance-doc-1",
            "organization_id": kwargs["organization_id"],
            "project_id": kwargs["project_id"],
            "filename": kwargs["file"].filename,
            "filetype": "application/pdf",
            "filesize": len(content),
            "uploadType": "incoming",
            "letterNo": kwargs["letter_no"],
            "date": "2026-08-01T00:00:00Z",
            "subject": kwargs["subject"],
            "status": "draft",
            "createdBy": "migration-user",
            "processing_status": "failed",
            "lifecycle_state": "active",
            "sha256": sha256,
            "file_object_id": "canonical-file-object-1",
            "current_version_id": "canonical-version-1",
        }
        self.db.file_objects.documents.append(
            {
                "_id": "canonical-file-object-1",
                "organization_id": document["organization_id"],
                "project_id": document["project_id"],
                "sha256": sha256,
            }
        )
        self.db.document_versions.documents.append(
            {
                "_id": "canonical-version-1",
                "document_id": document["_id"],
                "file_object_id": "canonical-file-object-1",
                "version_number": 1,
                "is_current": True,
            }
        )
        self.db.documents.documents.append(document)
        return Document(**document)

    async def compensate_failed_creation(self, document_id: str, **kwargs):
        document = next(
            (row for row in self.db.documents.documents if row.get("_id") == document_id),
            None,
        )
        file_object_id = (document or {}).get("file_object_id")
        self.db.documents.documents = [
            row for row in self.db.documents.documents if row.get("_id") != document_id
        ]
        self.db.document_versions.documents = [
            row for row in self.db.document_versions.documents if row.get("document_id") != document_id
        ]
        self.db.file_objects.documents = [
            row for row in self.db.file_objects.documents if row.get("_id") != file_object_id
        ]


class _DeleteMigrationTargetAfterCreateController(_MigrationDocumentController):
    async def create_document(self, **kwargs):
        document = await super().create_document(**kwargs)
        self.db.insurance_policies.documents.clear()
        return document


class _ChangedBytesController(_MigrationDocumentController):
    async def create_document(self, **kwargs):
        document = await super().create_document(**kwargs)
        self.db.file_objects.documents[-1]["sha256"] = "f" * 64
        return document


class _LeaseStolenAfterCreateController(_MigrationDocumentController):
    async def create_document(self, **kwargs):
        document = await super().create_document(**kwargs)
        claim = self.db.insurance_document_migration_claims.documents[0]
        claim["owner_token"] = "replacement-owner"
        claim["lease_expires_at"] = datetime.now(timezone.utc) + timedelta(minutes=15)
        return document


class _ReusingOrphanFileController(_MigrationDocumentController):
    async def create_document(self, **kwargs):
        self.calls += 1
        content = await kwargs["file"].read()
        digest = hashlib.sha256(content).hexdigest()
        file_object = next(
            row for row in self.db.file_objects.documents if row.get("sha256") == digest
        )
        document = {
            "_id": "canonical-insurance-doc-1",
            "organization_id": kwargs["organization_id"],
            "project_id": kwargs["project_id"],
            "filename": kwargs["file"].filename,
            "filetype": "application/pdf",
            "filesize": len(content),
            "uploadType": "incoming",
            "letterNo": kwargs["letter_no"],
            "date": "2026-08-01T00:00:00Z",
            "subject": kwargs["subject"],
            "status": "draft",
            "createdBy": "migration-user",
            "processing_status": "failed",
            "lifecycle_state": "active",
            "sha256": digest,
            "file_object_id": file_object["_id"],
            "current_version_id": "canonical-version-1",
        }
        self.db.documents.documents.append(document)
        self.db.document_versions.documents.append(
            {
                "_id": "canonical-version-1",
                "document_id": document["_id"],
                "file_object_id": file_object["_id"],
                "version_number": 1,
                "is_current": True,
            }
        )
        return Document(**document)


class _UniqueCollection(_Collection):
    async def insert_one(self, document, *args, **kwargs):
        if any(row.get("_id") == document.get("_id") for row in self.documents):
            raise DuplicateKeyError("duplicate migration claim")
        return await super().insert_one(document, *args, **kwargs)


class _LeaseCollection(_UniqueCollection):
    async def find_one_and_update(self, query, update, *args, **kwargs):
        for row in self.documents:
            if row.get("_id") != query.get("_id"):
                continue
            if row.get("owner_token") != query.get("owner_token"):
                continue
            if row.get("status") != query.get("status"):
                continue
            expiry_condition = query.get("lease_expires_at") or {}
            if "$lte" in expiry_condition and not (
                row.get("lease_expires_at") <= expiry_condition["$lte"]
            ):
                continue
            row.update(deepcopy(update.get("$set", {})))
            for field in update.get("$unset", {}):
                row.pop(field, None)
            return deepcopy(row)
        return None


async def test_insurance_legacy_inventory_is_deterministic_read_only_and_hash_safe(
    tmp_path: Path,
) -> None:
    legacy_root = tmp_path / "insurance"
    legacy_root.mkdir()
    policy_bytes = b"legacy-policy-bytes"
    (legacy_root / "policy-a.pdf").write_bytes(policy_bytes)

    db = _Database()
    db.insurance_policies = _Collection(
        "insurance_policies",
        [
            {
                "_id": "insurance-1",
                "organization_id": "org-1",
                "project_id": "project-1",
                "policy_number": "POL-1",
                "document_id": "policy-a.pdf",
                "document_name": "Policy A.pdf",
                "linked_document_ids": ["doc-1", "missing-doc"],
            },
            {
                "_id": "insurance-2",
                "organization_id": "org-1",
                "project_id": "project-1",
                "policy_number": "POL-2",
                "document_id": "missing-policy.pdf",
                "linked_document_ids": [],
            },
        ],
    )
    db.file_objects = _Collection("file_objects")
    db.insurance_document_migration_claims = _UniqueCollection(
        "insurance_document_migration_claims"
    )
    before_policies = deepcopy(db.insurance_policies.documents)
    before_documents = deepcopy(db.documents.documents)
    before_links = deepcopy(db.entity_document_links.documents)

    first = await classify_legacy_insurance_evidence(db, legacy_root=legacy_root)
    second = await classify_legacy_insurance_evidence(db, legacy_root=legacy_root)

    assert first == second
    assert first["dry_run"] is True
    assert first["candidate_count"] == 4
    assert first["counts"]["requires_canonicalization"] == 1
    assert first["counts"]["missing_file"] == 1
    assert first["counts"]["missing_document"] == 1
    assert first["counts"]["ambiguous_role"] == 1
    legacy = next(row for row in first["candidates"] if row["storage_key"] == "policy-a.pdf")
    assert legacy["classification"] == "requires_canonicalization"
    assert legacy["relationship_role"] == "policy"
    assert legacy["size"] == len(policy_bytes)
    assert legacy["sha256"] == hashlib.sha256(policy_bytes).hexdigest()
    assert legacy["target_type"] == "insurance"
    assert legacy["target_id"] == "insurance-1"
    ambiguous = next(row for row in first["candidates"] if row.get("document_id") == "doc-1")
    assert ambiguous["classification"] == "manual_review"
    assert ambiguous["relationship_role"] is None
    assert "ambiguous_role" in ambiguous["findings"]
    assert db.insurance_policies.documents == before_policies
    assert db.documents.documents == before_documents
    assert db.entity_document_links.documents == before_links


async def test_repeated_legacy_document_id_is_not_mislabeled_as_duplicate_bytes(
    tmp_path: Path,
) -> None:
    legacy_root = tmp_path / "insurance"
    legacy_root.mkdir()
    db = _Database()
    db.insurance_policies = _Collection(
        "insurance_policies",
        [
            {
                "_id": "insurance-1",
                "organization_id": "org-1",
                "project_id": "project-1",
                "linked_document_ids": ["doc-1", "doc-1"],
            }
        ],
    )
    db.file_objects = _Collection("file_objects")

    report = await classify_legacy_insurance_evidence(db, legacy_root=legacy_root)

    repeated = [
        row
        for row in report["candidates"]
        if row["source_kind"] == "legacy_linked_document_id"
    ]
    assert len(repeated) == 2
    assert all("duplicate_bytes" not in row["findings"] for row in repeated)
    assert report["counts"]["duplicate_bytes"] == 0
    assert repeated[1]["classification"] == "manual_review"


async def test_classifier_inventories_unreferenced_legacy_files_as_orphans(
    tmp_path: Path,
) -> None:
    legacy_root = tmp_path / "insurance"
    legacy_root.mkdir()
    orphan = legacy_root / "unreferenced-policy.pdf"
    orphan.write_bytes(b"orphan insurance evidence")
    db = _Database()
    db.insurance_policies = _Collection("insurance_policies", [])

    report = await classify_legacy_insurance_evidence(db, legacy_root=legacy_root)

    assert report["candidate_count"] == 1
    candidate = report["candidates"][0]
    assert candidate["source_kind"] == "orphan_legacy_file"
    assert candidate["target_id"] == "orphan:unreferenced-policy.pdf"
    assert candidate["classification"] == "orphan_file"
    assert candidate["sha256"] == hashlib.sha256(orphan.read_bytes()).hexdigest()
    assert orphan.exists()


async def test_classifier_reports_legacy_storage_scan_failure() -> None:
    class BrokenLegacyRoot:
        def exists(self) -> bool:
            return True

        def resolve(self):
            return self

        def rglob(self, _pattern: str):
            raise OSError("injected legacy storage scan failure")

        def __str__(self) -> str:
            return "broken-insurance-root"

    db = _Database()
    db.insurance_policies = _Collection("insurance_policies", [])

    report = await classify_legacy_insurance_evidence(
        db, legacy_root=BrokenLegacyRoot()
    )

    assert report["candidate_count"] == 1
    assert report["counts"]["storage_error"] == 1
    candidate = report["candidates"][0]
    assert candidate["source_kind"] == "legacy_storage_scan"
    assert candidate["classification"] == "storage_error"


async def test_same_hash_document_requires_explicit_semantic_approval(
    tmp_path: Path,
) -> None:
    legacy_root = tmp_path / "insurance"
    legacy_root.mkdir()
    content = b"same-bytes-do-not-prove-policy-identity"
    (legacy_root / "policy-a.pdf").write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    db = _Database()
    db.insurance_policies = _Collection(
        "insurance_policies",
        [
            {
                "_id": "insurance-1",
                "organization_id": "org-1",
                "project_id": "project-1",
                "policy_number": "POL-1",
                "date_of_issue": "2026-08-01T00:00:00Z",
                "document_id": "policy-a.pdf",
            }
        ],
    )
    db.file_objects = _Collection(
        "file_objects",
        [
            {
                "_id": "shared-file",
                "organization_id": "org-1",
                "project_id": "project-1",
                "sha256": digest,
            }
        ],
    )
    db.documents = _Collection(
        "documents",
        [
            {
                "_id": "unrelated-document",
                "organization_id": "org-1",
                "project_id": "project-1",
                "filename": "unrelated.pdf",
                "file_object_id": "shared-file",
                "current_version_id": "unrelated-version",
                "lifecycle_state": "active",
                "processing_status": "failed",
            },
            {
                "_id": "other-document",
                "organization_id": "org-1",
                "project_id": "project-1",
                "filename": "other.pdf",
                "file_object_id": "historical-file",
                "current_version_id": "other-version",
                "lifecycle_state": "active",
                "processing_status": "failed",
            },
        ],
    )
    db.document_versions = _Collection(
        "document_versions",
        [
            {
                "_id": "unrelated-version",
                "document_id": "unrelated-document",
                "file_object_id": "shared-file",
                "version_number": 1,
                "is_current": True,
            }
        ],
    )
    db.insurance_document_migration_claims = _UniqueCollection(
        "insurance_document_migration_claims"
    )

    report = await classify_legacy_insurance_evidence(db, legacy_root=legacy_root)
    candidate = report["candidates"][0]

    assert candidate["classification"] == "manual_review"
    assert "existing_canonical_document" in candidate["findings"]
    assert "ambiguous_document_identity" in candidate["findings"]
    with pytest.raises(ValueError, match="manual review"):
        await canonicalize_legacy_insurance_file(
            db,
            insurance_id="insurance-1",
            legacy_root=legacy_root,
            current_user=SimpleNamespace(
                id="migration-user",
                permissions={Permissions.INSURANCE_EDIT, Permissions.DOCUMENT_VIEW},
            ),
            controller=_MigrationDocumentController(db),
            policy=_PermissionPolicy(),
            migration_run_id="run-1",
        )
    assert db.entity_document_links.documents == []


async def test_approved_existing_document_pins_the_exact_matching_version(
    tmp_path: Path,
) -> None:
    legacy_root = tmp_path / "insurance"
    legacy_root.mkdir()
    content = b"historical-policy-version"
    (legacy_root / "policy-a.pdf").write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    db = _Database()
    db.insurance_policies = _Collection(
        "insurance_policies",
        [
            {
                "_id": "insurance-1",
                "organization_id": "org-1",
                "project_id": "project-1",
                "policy_number": "POL-1",
                "date_of_issue": "2026-08-01T00:00:00Z",
                "document_id": "policy-a.pdf",
            }
        ],
    )
    db.file_objects = _Collection(
        "file_objects",
        [
            {"_id": "historical-file", "organization_id": "org-1", "project_id": "project-1", "sha256": digest},
            {"_id": "current-file", "organization_id": "org-1", "project_id": "project-1", "sha256": "f" * 64},
        ],
    )
    db.documents = _Collection(
        "documents",
        [
            {
                "_id": "approved-document",
                "organization_id": "org-1",
                "project_id": "project-1",
                "filename": "policy.pdf",
                "file_object_id": "current-file",
                "current_version_id": "current-version",
                "lifecycle_state": "active",
                "processing_status": "failed",
            }
        ],
    )
    db.document_versions = _Collection(
        "document_versions",
        [
            {"_id": "historical-version", "document_id": "approved-document", "file_object_id": "historical-file", "version_number": 1, "is_current": False},
            {"_id": "current-version", "document_id": "approved-document", "file_object_id": "current-file", "version_number": 2, "is_current": True},
            {"_id": "other-version", "document_id": "other-document", "file_object_id": "historical-file", "version_number": 1, "is_current": True},
        ],
    )
    db.insurance_document_migration_claims = _UniqueCollection(
        "insurance_document_migration_claims"
    )

    result = await canonicalize_legacy_insurance_file(
        db,
        insurance_id="insurance-1",
        legacy_root=legacy_root,
        current_user=SimpleNamespace(
            id="migration-user",
            permissions={Permissions.INSURANCE_EDIT, Permissions.DOCUMENT_VIEW},
        ),
        controller=_MigrationDocumentController(db),
        policy=_PermissionPolicy(),
        migration_run_id="run-1",
        approved_document_id="approved-document",
    )

    assert result["document_id"] == "approved-document"
    assert result["document_version_id"] == "historical-version"
    assert db.entity_document_links.documents[0]["document_version_id"] == "historical-version"


async def test_hash_inventory_rejects_foreign_scope_document_even_when_file_object_matches(
    tmp_path: Path,
) -> None:
    legacy_root = tmp_path / "insurance"
    legacy_root.mkdir()
    content = b"scoped-policy"
    (legacy_root / "policy-a.pdf").write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    db = _Database()
    db.insurance_policies = _Collection(
        "insurance_policies",
        [{"_id": "insurance-1", "organization_id": "org-1", "project_id": "project-1", "document_id": "policy-a.pdf"}],
    )
    db.file_objects = _Collection(
        "file_objects",
        [{"_id": "file-1", "organization_id": "org-1", "project_id": "project-1", "sha256": digest}],
    )
    db.documents = _Collection(
        "documents",
        [{"_id": "foreign-document", "organization_id": "org-2", "project_id": "project-1", "file_object_id": "file-1", "lifecycle_state": "active"}],
    )
    db.document_versions = _Collection("document_versions")

    report = await classify_legacy_insurance_evidence(db, legacy_root=legacy_root)

    assert report["candidates"][0]["classification"] == "cross_organisation"
    assert report["counts"]["cross_organisation"] == 1


async def test_top_level_file_object_match_cannot_substitute_for_version_ownership(
    tmp_path: Path,
) -> None:
    legacy_root = tmp_path / "insurance"
    legacy_root.mkdir()
    content = b"version-owned-bytes"
    (legacy_root / "policy-a.pdf").write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    db = _Database()
    db.insurance_policies = _Collection(
        "insurance_policies",
        [{
            "_id": "insurance-1",
            "organization_id": "org-1",
            "project_id": "project-1",
            "date_of_issue": "2026-08-01T00:00:00Z",
            "document_id": "policy-a.pdf",
        }],
    )
    db.file_objects = _Collection(
        "file_objects",
        [{"_id": "file-1", "organization_id": "org-1", "project_id": "project-1", "sha256": digest}],
    )
    db.documents = _Collection(
        "documents",
        [{
            "_id": "document-1",
            "organization_id": "org-1",
            "project_id": "project-1",
            "file_object_id": "file-1",
            "current_version_id": "unverified-version",
            "lifecycle_state": "active",
            "processing_status": "failed",
        }],
    )
    db.document_versions = _Collection("document_versions")
    db.insurance_document_migration_claims = _UniqueCollection(
        "insurance_document_migration_claims"
    )

    report = await classify_legacy_insurance_evidence(db, legacy_root=legacy_root)
    assert report["candidates"][0]["document_version_id"] is None

    with pytest.raises(ValueError, match="manual review"):
        await canonicalize_legacy_insurance_file(
            db,
            insurance_id="insurance-1",
            legacy_root=legacy_root,
            current_user=SimpleNamespace(
                id="migration-user",
                permissions={Permissions.INSURANCE_EDIT, Permissions.DOCUMENT_VIEW},
            ),
            controller=_MigrationDocumentController(db),
            policy=_PermissionPolicy(),
            migration_run_id="run-1",
            approved_document_id="document-1",
        )


async def test_migration_compensates_new_canonical_artifacts_when_link_fails(
    tmp_path: Path,
) -> None:
    legacy_root = tmp_path / "insurance"
    legacy_root.mkdir()
    source = legacy_root / "policy-a.pdf"
    source.write_bytes(b"legacy-policy-bytes")
    db = _Database()
    db.insurance_policies = _Collection(
        "insurance_policies",
        [{
            "_id": "insurance-1",
            "organization_id": "org-1",
            "project_id": "project-1",
            "policy_number": "POL-1",
            "date_of_issue": "2026-08-01T00:00:00Z",
            "document_id": "policy-a.pdf",
        }],
    )
    db.file_objects = _Collection("file_objects")
    db.insurance_document_migration_claims = _UniqueCollection(
        "insurance_document_migration_claims"
    )

    with pytest.raises(Exception, match="target not found"):
        await canonicalize_legacy_insurance_file(
            db,
            insurance_id="insurance-1",
            legacy_root=legacy_root,
            current_user=SimpleNamespace(
                id="migration-user",
                permissions={Permissions.INSURANCE_EDIT, Permissions.DOCUMENT_VIEW},
            ),
            controller=_DeleteMigrationTargetAfterCreateController(db),
            policy=_PermissionPolicy(),
            migration_run_id="run-1",
        )

    assert source.read_bytes() == b"legacy-policy-bytes"
    assert all(row.get("_id") != "canonical-insurance-doc-1" for row in db.documents.documents)
    assert db.file_objects.documents == []
    assert all(
        row.get("document_id") != "canonical-insurance-doc-1"
        for row in db.document_versions.documents
    )
    assert db.entity_document_links.documents == []


async def test_migration_reconciles_stored_hash_before_creating_authority(
    tmp_path: Path,
) -> None:
    legacy_root = tmp_path / "insurance"
    legacy_root.mkdir()
    source = legacy_root / "policy-a.pdf"
    source.write_bytes(b"verified-source-bytes")
    db = _Database()
    db.insurance_policies = _Collection(
        "insurance_policies",
        [{
            "_id": "insurance-1",
            "organization_id": "org-1",
            "project_id": "project-1",
            "policy_number": "POL-1",
            "date_of_issue": "2026-08-01T00:00:00Z",
            "document_id": "policy-a.pdf",
        }],
    )
    db.file_objects = _Collection("file_objects")
    db.insurance_document_migration_claims = _UniqueCollection(
        "insurance_document_migration_claims"
    )

    with pytest.raises(ValueError, match="bytes differ"):
        await canonicalize_legacy_insurance_file(
            db,
            insurance_id="insurance-1",
            legacy_root=legacy_root,
            current_user=SimpleNamespace(
                id="migration-user",
                permissions={Permissions.INSURANCE_EDIT, Permissions.DOCUMENT_VIEW},
            ),
            controller=_ChangedBytesController(db),
            policy=_PermissionPolicy(),
            migration_run_id="run-1",
        )

    assert source.read_bytes() == b"verified-source-bytes"
    assert db.entity_document_links.documents == []
    assert all(row.get("_id") != "canonical-insurance-doc-1" for row in db.documents.documents)


async def test_stale_processing_lease_is_reclaimed_deterministically(
    tmp_path: Path,
) -> None:
    legacy_root = tmp_path / "insurance"
    legacy_root.mkdir()
    source = legacy_root / "policy-a.pdf"
    source.write_bytes(b"legacy-policy-bytes")
    digest = hashlib.sha256(b"legacy-policy-bytes").hexdigest()
    db = _Database()
    db.insurance_policies = _Collection(
        "insurance_policies",
        [{
            "_id": "insurance-1",
            "organization_id": "org-1",
            "project_id": "project-1",
            "policy_number": "POL-1",
            "date_of_issue": "2026-08-01T00:00:00Z",
            "document_id": "policy-a.pdf",
        }],
    )
    db.file_objects = _Collection("file_objects")
    db.insurance_document_migration_claims = _LeaseCollection(
        "insurance_document_migration_claims",
        [{
            "_id": f"insurance-bytes:org-1:project-1:{digest}",
            "insurance_id": "insurance-1",
            "sha256": digest,
            "status": "processing",
            "owner_token": "crashed-owner",
            "lease_expires_at": datetime.now(timezone.utc) - timedelta(minutes=1),
        }],
    )

    result = await canonicalize_legacy_insurance_file(
        db,
        insurance_id="insurance-1",
        legacy_root=legacy_root,
        current_user=SimpleNamespace(
            id="migration-user",
            permissions={Permissions.INSURANCE_EDIT, Permissions.DOCUMENT_VIEW},
        ),
        controller=_MigrationDocumentController(db),
        policy=_PermissionPolicy(),
        migration_run_id="run-recovery",
    )

    assert result["document_id"] == "canonical-insurance-doc-1"
    claim = db.insurance_document_migration_claims.documents[0]
    assert claim["status"] == "complete"
    assert claim["owner_token"] != "crashed-owner"


async def test_lost_migration_lease_is_fenced_before_relationship_authority(
    tmp_path: Path,
) -> None:
    legacy_root = tmp_path / "insurance"
    legacy_root.mkdir()
    source = legacy_root / "policy-a.pdf"
    source.write_bytes(b"legacy-policy-bytes")
    db = _Database()
    db.insurance_policies = _Collection(
        "insurance_policies",
        [{
            "_id": "insurance-1",
            "organization_id": "org-1",
            "project_id": "project-1",
            "policy_number": "POL-1",
            "date_of_issue": "2026-08-01T00:00:00Z",
            "document_id": "policy-a.pdf",
        }],
    )
    db.file_objects = _Collection("file_objects")
    db.insurance_document_migration_claims = _UniqueCollection(
        "insurance_document_migration_claims"
    )

    with pytest.raises(ValueError, match="lease was lost"):
        await canonicalize_legacy_insurance_file(
            db,
            insurance_id="insurance-1",
            legacy_root=legacy_root,
            current_user=SimpleNamespace(
                id="migration-user",
                permissions={Permissions.INSURANCE_EDIT, Permissions.DOCUMENT_VIEW},
            ),
            controller=_LeaseStolenAfterCreateController(db),
            policy=_PermissionPolicy(),
            migration_run_id="run-fenced",
        )

    assert db.entity_document_links.documents == []
    assert all(row.get("_id") != "canonical-insurance-doc-1" for row in db.documents.documents)
    assert source.read_bytes() == b"legacy-policy-bytes"


async def test_retry_reuses_retained_orphan_file_object_after_compensation(
    tmp_path: Path,
) -> None:
    legacy_root = tmp_path / "insurance"
    legacy_root.mkdir()
    source = legacy_root / "policy-a.pdf"
    source.write_bytes(b"legacy-policy-bytes")
    digest = hashlib.sha256(b"legacy-policy-bytes").hexdigest()
    db = _Database()
    db.insurance_policies = _Collection(
        "insurance_policies",
        [{
            "_id": "insurance-1",
            "organization_id": "org-1",
            "project_id": "project-1",
            "policy_number": "POL-1",
            "date_of_issue": "2026-08-01T00:00:00Z",
            "document_id": "policy-a.pdf",
        }],
    )
    db.file_objects = _Collection(
        "file_objects",
        [{
            "_id": "retained-file-object",
            "organization_id": "org-1",
            "project_id": "project-1",
            "sha256": digest,
            "document_ids": [],
        }],
    )
    db.documents = _Collection("documents")
    db.document_versions = _Collection("document_versions")
    db.insurance_document_migration_claims = _UniqueCollection(
        "insurance_document_migration_claims"
    )

    report = await classify_legacy_insurance_evidence(db, legacy_root=legacy_root)
    assert report["candidates"][0]["classification"] == "orphan_file"

    result = await canonicalize_legacy_insurance_file(
        db,
        insurance_id="insurance-1",
        legacy_root=legacy_root,
        current_user=SimpleNamespace(
            id="migration-user",
            permissions={Permissions.INSURANCE_EDIT, Permissions.DOCUMENT_VIEW},
        ),
        controller=_ReusingOrphanFileController(db),
        policy=_PermissionPolicy(),
        migration_run_id="run-retry",
    )

    assert result["document_id"] == "canonical-insurance-doc-1"
    assert len(db.file_objects.documents) == 1
    assert db.entity_document_links.documents[0]["document_version_id"] == "canonical-version-1"


async def test_legacy_canonicalization_is_idempotent_and_preserves_source_file(
    tmp_path: Path,
) -> None:
    legacy_root = tmp_path / "insurance"
    legacy_root.mkdir()
    source = legacy_root / "policy-a.pdf"
    source.write_bytes(b"legacy-policy-bytes")
    db = _Database()
    db.insurance_policies = _Collection(
        "insurance_policies",
        [
            {
                "_id": "insurance-1",
                "organization_id": "org-1",
                "project_id": "project-1",
                "policy_number": "POL-1",
                "date_of_issue": "2026-08-01T00:00:00Z",
                "document_id": "policy-a.pdf",
            }
        ],
    )
    db.file_objects = _Collection("file_objects")
    db.insurance_document_migration_claims = _UniqueCollection(
        "insurance_document_migration_claims"
    )
    controller = _MigrationDocumentController(db)
    actor = SimpleNamespace(
        id="migration-user",
        permissions={Permissions.INSURANCE_EDIT, Permissions.DOCUMENT_VIEW},
    )

    first = await canonicalize_legacy_insurance_file(
        db,
        insurance_id="insurance-1",
        legacy_root=legacy_root,
        current_user=actor,
        controller=controller,
        policy=_PermissionPolicy(),
        migration_run_id="run-1",
    )
    second = await canonicalize_legacy_insurance_file(
        db,
        insurance_id="insurance-1",
        legacy_root=legacy_root,
        current_user=actor,
        controller=controller,
        policy=_PermissionPolicy(),
        migration_run_id="run-2",
    )

    assert first["document_id"] == second["document_id"] == "canonical-insurance-doc-1"
    assert first["relationship_id"] == second["relationship_id"]
    assert controller.calls == 1
    assert source.read_bytes() == b"legacy-policy-bytes"
    assert len(db.file_objects.documents) == 1
    assert len([row for row in db.documents.documents if row.get("_id") == "canonical-insurance-doc-1"]) == 1
    assert len(db.entity_document_links.documents) == 1
    link = db.entity_document_links.documents[0]
    assert link["source"] == "migration"
    assert link["relationship_role"] == "policy"
    assert link["metadata"]["legacy_storage_key"] == "policy-a.pdf"
    assert link["metadata"]["legacy_sha256"] == hashlib.sha256(b"legacy-policy-bytes").hexdigest()
    assert link["metadata"]["migration_run_id"] == "run-1"
