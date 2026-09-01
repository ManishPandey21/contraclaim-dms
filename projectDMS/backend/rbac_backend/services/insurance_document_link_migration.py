"""Deterministic legacy Insurance byte and relationship inventory."""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import UploadFile
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from ..models.document_relationship import DocumentRelationshipInput
from .document_relationship_service import DocumentRelationshipService
from .publication_policy import is_consumable, resolve_canonical_document


CLASSIFICATIONS = (
    "valid",
    "missing_file",
    "missing_document",
    "deleted_document",
    "blocked_document",
    "invalid_id",
    "cross_organisation",
    "cross_project",
    "duplicate_bytes",
    "existing_canonical_document",
    "ambiguous_document_identity",
    "ambiguous_event",
    "ambiguous_role",
    "orphan_file",
    "orphan_event",
    "hash_mismatch",
    "storage_error",
    "requires_canonicalization",
    "manual_review",
    "renewal_ownership_ambiguous",
    "extension_ownership_ambiguous",
    "endorsement_ownership_ambiguous",
    "certificate_ownership_ambiguous",
)


def _safe_legacy_path(root: Path, token: Any) -> Path | None:
    if not isinstance(token, str) or not token.strip():
        return None
    token = token.strip()
    if "/" in token or "\\" in token or ".." in token:
        return None
    root = root.resolve()
    candidate = (root / token).resolve()
    return candidate if candidate.parent == root else None


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _lease_expired(value: Any) -> bool:
    if not isinstance(value, datetime):
        return False
    now = datetime.now(timezone.utc)
    if value.tzinfo is None:
        now = now.replace(tzinfo=None)
    return value <= now


async def _rows(collection: Any, query: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [row async for row in collection.find(query)]
    rows.sort(key=lambda row: str(row.get("_id") or ""))
    return rows


async def _canonical_documents_for_hash(
    db: Any,
    *,
    sha256: str,
    organization_id: str,
    project_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    file_objects_collection = getattr(db, "file_objects", None)
    if file_objects_collection is None:
        return [], []
    file_objects = await _rows(
        file_objects_collection,
        {
            "sha256": sha256,
            "organization_id": organization_id,
            "project_id": project_id,
        },
    )
    if not file_objects:
        return [], []
    file_ids = [str(row.get("_id") or "") for row in file_objects]
    documents = await _rows(db.documents, {"file_object_id": {"$in": file_ids}})
    versions: list[dict[str, Any]] = []
    version_collection = getattr(db, "document_versions", None)
    if version_collection is not None:
        versions = await _rows(
            version_collection, {"file_object_id": {"$in": file_ids}}
        )
        version_document_ids = {
            str(version.get("document_id") or "") for version in versions
        }
        if version_document_ids:
            version_documents = await _rows(
                db.documents, {"_id": {"$in": list(version_document_ids)}}
            )
            known = {str(row.get("_id") or "") for row in documents}
            documents.extend(
                row
                for row in version_documents
                if str(row.get("_id") or "") not in known
            )
            documents.sort(key=lambda row: str(row.get("_id") or ""))
    enriched: list[dict[str, Any]] = []
    for document in documents:
        document_id = str(document.get("_id") or "")
        matching_versions = [
            version
            for version in versions
            if str(version.get("document_id") or "") == document_id
            and str(version.get("file_object_id") or "") in file_ids
        ]
        matching_versions.sort(
            key=lambda version: (
                not bool(version.get("is_current")),
                -int(version.get("version_number") or 0),
                str(version.get("_id") or ""),
            )
        )
        matching_version = matching_versions[0] if matching_versions else None
        matching_file_object_id = str(
            (matching_version or {}).get("file_object_id")
            or document.get("file_object_id")
            or ""
        )
        enriched.append(
            {
                **document,
                "_matching_file_object_id": matching_file_object_id,
                "_matching_document_version_id": str(
                    (matching_version or {}).get("_id")
                    or ""
                ),
            }
        )
    return file_objects, enriched


async def _classify_canonical_document(
    db: Any,
    *,
    raw_document_id: Any,
    organization_id: str,
    project_id: str,
) -> tuple[str, list[str], str]:
    if not isinstance(raw_document_id, str) or not raw_document_id.strip():
        return str(raw_document_id or ""), ["invalid_id"], "invalid_id"
    document_id = raw_document_id.strip()
    document = await resolve_canonical_document(db, document_id)
    if document is None:
        return document_id, ["missing_document"], "missing_document"
    if str(document.get("lifecycle_state") or "") == "deleted":
        return document_id, ["deleted_document"], "deleted_document"
    if not is_consumable(document):
        return document_id, ["blocked_document"], "blocked_document"
    document_org = str(document.get("organization_id") or document.get("organizationId") or "")
    document_project = str(document.get("project_id") or document.get("projectId") or "")
    if document_org != organization_id:
        return document_id, ["cross_organisation"], "cross_organisation"
    if document_project != project_id:
        return document_id, ["cross_project"], "cross_project"
    return document_id, ["valid", "ambiguous_role", "manual_review"], "manual_review"


async def classify_legacy_insurance_evidence(
    db: Any,
    *,
    legacy_root: Path,
) -> dict[str, Any]:
    """Inventory legacy tokens/arrays without mutating Mongo or source files."""

    counts = {classification: 0 for classification in CLASSIFICATIONS}
    candidates: list[dict[str, Any]] = []
    referenced_storage_keys: set[str] = set()
    policies = await _rows(db.insurance_policies, {})
    for insurance in policies:
        insurance_id = str(insurance.get("_id") or "")
        organization_id = str(insurance.get("organization_id") or "")
        project_id = str(insurance.get("project_id") or "")
        token = insurance.get("document_id")
        if token:
            if isinstance(token, str) and token.strip():
                referenced_storage_keys.add(token.strip().replace("\\", "/"))
            findings: list[str] = []
            classification = "manual_review"
            path = _safe_legacy_path(legacy_root, token)
            size = None
            sha256 = None
            document_id = None
            document_version_id = None
            document_candidates: list[dict[str, Any]] = []
            file_object_ids: list[str] = []
            if path is None:
                findings = ["invalid_id"]
                classification = "invalid_id"
            elif not path.is_file():
                findings = ["missing_file"]
                classification = "missing_file"
            else:
                try:
                    size, sha256 = _hash_file(path)
                    expected_hash = str(
                        insurance.get("document_sha256")
                        or insurance.get("legacy_sha256")
                        or ""
                    )
                    if expected_hash and expected_hash != sha256:
                        findings = ["hash_mismatch"]
                        classification = "hash_mismatch"
                    else:
                        file_objects, documents = await _canonical_documents_for_hash(
                            db,
                            sha256=sha256,
                            organization_id=organization_id,
                            project_id=project_id,
                        )
                        file_object_ids = [str(row.get("_id") or "") for row in file_objects]
                        if not file_objects:
                            findings = ["requires_canonicalization"]
                            classification = "requires_canonicalization"
                        elif not documents:
                            findings = ["orphan_file"]
                            classification = "orphan_file"
                        elif len(documents) > 1:
                            document_candidates = [
                                {
                                    "document_id": str(row.get("_id") or ""),
                                    "document_version_id": str(
                                        row.get("_matching_document_version_id") or ""
                                    )
                                    or None,
                                    "organization_id": str(
                                        row.get("organization_id")
                                        or row.get("organizationId")
                                        or ""
                                    ),
                                    "project_id": str(
                                        row.get("project_id")
                                        or row.get("projectId")
                                        or ""
                                    ),
                                }
                                for row in documents
                            ]
                            findings = [
                                "duplicate_bytes",
                                "ambiguous_document_identity",
                                "manual_review",
                            ]
                            classification = "manual_review"
                        else:
                            canonical = documents[0]
                            document_id = str(canonical.get("_id") or "")
                            document_version_id = str(
                                canonical.get("_matching_document_version_id") or ""
                            ) or None
                            document_candidates = [
                                {
                                    "document_id": document_id,
                                    "document_version_id": document_version_id,
                                    "organization_id": str(
                                        canonical.get("organization_id")
                                        or canonical.get("organizationId")
                                        or ""
                                    ),
                                    "project_id": str(
                                        canonical.get("project_id")
                                        or canonical.get("projectId")
                                        or ""
                                    ),
                                }
                            ]
                            document_org = str(
                                canonical.get("organization_id")
                                or canonical.get("organizationId")
                                or ""
                            )
                            document_project = str(
                                canonical.get("project_id")
                                or canonical.get("projectId")
                                or ""
                            )
                            if document_org != organization_id:
                                findings = ["cross_organisation"]
                                classification = "cross_organisation"
                            elif document_project != project_id:
                                findings = ["cross_project"]
                                classification = "cross_project"
                            elif str(canonical.get("lifecycle_state") or "") == "deleted":
                                findings = ["deleted_document"]
                                classification = "deleted_document"
                            elif not is_consumable(canonical):
                                findings = ["blocked_document"]
                                classification = "blocked_document"
                            else:
                                findings = [
                                    "existing_canonical_document",
                                    "ambiguous_document_identity",
                                    "ambiguous_role",
                                    "manual_review",
                                ]
                                classification = "manual_review"
                except OSError:
                    findings = ["storage_error"]
                    classification = "storage_error"
            for finding in findings:
                counts[finding] += 1
            candidates.append(
                {
                    "insurance_id": insurance_id,
                    "target_type": "insurance",
                    "target_id": insurance_id,
                    "parent_type": None,
                    "parent_id": None,
                    "source_kind": "legacy_file",
                    "storage_key": str(token),
                    "source_path": str(path) if path is not None else None,
                    "filename": insurance.get("document_name") or str(token),
                    "size": size,
                    "sha256": sha256,
                    "file_object_ids": file_object_ids,
                    "document_id": document_id,
                    "document_version_id": document_version_id,
                    "document_candidates": document_candidates,
                    "relationship_role": "policy",
                    "classification": classification,
                    "findings": findings,
                }
            )

        seen: set[str] = set()
        for occurrence, raw_document_id in enumerate(
            insurance.get("linked_document_ids") or [], start=1
        ):
            normalized = (
                raw_document_id.strip()
                if isinstance(raw_document_id, str)
                else str(raw_document_id or "")
            )
            document_id, findings, classification = await _classify_canonical_document(
                db,
                raw_document_id=raw_document_id,
                organization_id=organization_id,
                project_id=project_id,
            )
            if normalized in seen:
                findings = [finding for finding in findings if finding != "valid"]
                if "manual_review" not in findings:
                    findings.append("manual_review")
                classification = "manual_review"
            seen.add(normalized)
            for finding in findings:
                counts[finding] += 1
            candidates.append(
                {
                    "insurance_id": insurance_id,
                    "target_type": "insurance",
                    "target_id": insurance_id,
                    "parent_type": None,
                    "parent_id": None,
                    "source_kind": "legacy_linked_document_id",
                    "storage_key": None,
                    "source_path": None,
                    "filename": None,
                    "size": None,
                    "sha256": None,
                    "file_object_ids": [],
                    "document_id": document_id,
                    "occurrence": occurrence,
                    "relationship_role": None,
                    "classification": classification,
                    "findings": findings,
                }
            )

    # Inventory files that exist in legacy storage but are not referenced by
    # any current Insurance row. These cannot be assigned to a tenant or
    # policy from bytes alone, so they remain manual-review candidates.
    if legacy_root.exists():
        scan_error: OSError | None = None
        try:
            legacy_files = sorted(
                (
                    path
                    for path in legacy_root.resolve().rglob("*")
                    if path.is_file() and not path.is_symlink()
                ),
                key=lambda path: path.relative_to(legacy_root.resolve()).as_posix(),
            )
        except OSError as exc:
            legacy_files = []
            scan_error = exc
        if scan_error is not None:
            finding = "storage_error"
            counts[finding] += 1
            candidates.append(
                {
                    "insurance_id": None,
                    "target_type": "insurance",
                    "target_id": "storage-scan:insurance",
                    "parent_type": None,
                    "parent_id": None,
                    "source_kind": "legacy_storage_scan",
                    "storage_key": None,
                    "source_path": str(legacy_root),
                    "filename": None,
                    "size": None,
                    "sha256": None,
                    "file_object_ids": [],
                    "document_id": None,
                    "document_version_id": None,
                    "document_candidates": [],
                    "relationship_role": None,
                    "classification": finding,
                    "findings": [finding],
                    "error": str(scan_error),
                }
            )
        for path in legacy_files:
            storage_key = path.relative_to(legacy_root.resolve()).as_posix()
            if storage_key in referenced_storage_keys:
                continue
            try:
                size, sha256 = _hash_file(path)
                findings = ["orphan_file"]
                classification = "orphan_file"
            except OSError:
                size = None
                sha256 = None
                findings = ["storage_error"]
                classification = "storage_error"
            for finding in findings:
                counts[finding] += 1
            candidates.append(
                {
                    "insurance_id": None,
                    "target_type": "insurance",
                    "target_id": f"orphan:{storage_key}",
                    "parent_type": None,
                    "parent_id": None,
                    "source_kind": "orphan_legacy_file",
                    "storage_key": storage_key,
                    "source_path": str(path),
                    "filename": path.name,
                    "size": size,
                    "sha256": sha256,
                    "file_object_ids": [],
                    "document_id": None,
                    "document_version_id": None,
                    "document_candidates": [],
                    "relationship_role": None,
                    "classification": classification,
                    "findings": findings,
                }
            )

    candidates.sort(
        key=lambda row: (
            row["target_id"],
            row["source_kind"],
            str(row.get("storage_key") or row.get("document_id") or ""),
            int(row.get("occurrence") or 0),
        )
    )
    return {
        "dry_run": True,
        "target_type": "insurance",
        "candidate_count": len(candidates),
        "counts": counts,
        "candidates": candidates,
    }


async def canonicalize_legacy_insurance_file(
    db: Any,
    *,
    insurance_id: str,
    legacy_root: Path,
    current_user: Any,
    controller: Any,
    policy: Any,
    migration_run_id: str,
    approved_document_id: str | None = None,
) -> dict[str, Any]:
    """Canonicalize one classifier-approved legacy primary policy file."""

    insurance = await db.insurance_policies.find_one({"_id": insurance_id})
    if insurance is None:
        raise ValueError("Insurance policy not found")
    source_path = _safe_legacy_path(legacy_root, insurance.get("document_id"))
    if source_path is None or not source_path.is_file():
        raise ValueError("Legacy Insurance file candidate not found")
    _, claimed_sha256 = _hash_file(source_path)
    expected_hash = str(
        insurance.get("document_sha256") or insurance.get("legacy_sha256") or ""
    )
    if expected_hash and expected_hash != claimed_sha256:
        raise ValueError("Legacy Insurance file requires manual review: hash_mismatch")

    organization_id = str(insurance.get("organization_id") or "")
    project_id = str(insurance.get("project_id") or "")
    claim_id = (
        f"insurance-bytes:{organization_id}:{project_id}:{claimed_sha256}"
    )
    owner_token = uuid.uuid4().hex
    now = datetime.now(timezone.utc)
    lease_expires_at = now + timedelta(minutes=15)
    owner = False
    reuse_completed_claim = False
    created_document_id: str | None = None
    try:
        await db.insurance_document_migration_claims.insert_one(
            {
                "_id": claim_id,
                "insurance_id": insurance_id,
                "sha256": claimed_sha256,
                "status": "processing",
                "owner_token": owner_token,
                "migration_run_id": migration_run_id,
                "lease_expires_at": lease_expires_at,
                "created_at": now,
                "updated_at": now,
            }
        )
        owner = True
    except DuplicateKeyError:
        owner = False

    if not owner:
        deadline = asyncio.get_running_loop().time() + 10.0
        while asyncio.get_running_loop().time() < deadline:
            claim = await db.insurance_document_migration_claims.find_one({"_id": claim_id})
            if claim and claim.get("status") == "complete":
                completed_result = dict(claim["result"])
                if str(completed_result.get("insurance_id") or "") == insurance_id:
                    return completed_result
                if (
                    approved_document_id
                    and str(completed_result.get("document_id") or "")
                    == str(approved_document_id)
                ):
                    reuse_completed_claim = True
                    break
                raise ValueError(
                    "Legacy Insurance file requires manual review: "
                    "same bytes are already associated with another policy"
                )
            reclaimable = bool(
                claim
                and (
                    claim.get("status") == "failed"
                    or (
                        claim.get("status") == "processing"
                        and _lease_expired(claim.get("lease_expires_at"))
                    )
                )
            )
            if reclaimable:
                reclaim_query: dict[str, Any] = {
                    "_id": claim_id,
                    "owner_token": claim.get("owner_token"),
                }
                if claim.get("status") == "failed":
                    reclaim_query["status"] = "failed"
                else:
                    reclaim_query.update(
                        {
                            "status": "processing",
                            "lease_expires_at": {
                                "$lte": datetime.now(timezone.utc)
                            },
                        }
                    )
                reclaimed = await db.insurance_document_migration_claims.find_one_and_update(
                    reclaim_query,
                    {
                        "$set": {
                            "status": "processing",
                            "owner_token": owner_token,
                            "migration_run_id": migration_run_id,
                            "updated_at": datetime.now(timezone.utc),
                            "lease_expires_at": datetime.now(timezone.utc)
                            + timedelta(minutes=15),
                        },
                        "$unset": {"error_type": ""},
                    },
                    return_document=ReturnDocument.AFTER,
                )
                if reclaimed and reclaimed.get("owner_token") == owner_token:
                    owner = True
                    break
            await asyncio.sleep(0.05)
        if not owner and not reuse_completed_claim:
            raise ValueError("Legacy Insurance canonicalization is already in progress")

    try:
        if owner:
            await db.insurance_document_migration_claims.update_one(
                {"_id": claim_id, "owner_token": owner_token},
                {
                    "$set": {
                        "lease_expires_at": datetime.now(timezone.utc)
                        + timedelta(minutes=15),
                        "updated_at": datetime.now(timezone.utc),
                    }
                },
            )
        report = await classify_legacy_insurance_evidence(db, legacy_root=legacy_root)
        candidate = next(
            (
                row
                for row in report["candidates"]
                if row["target_id"] == str(insurance_id)
                and row["source_kind"] == "legacy_file"
            ),
            None,
        )
        if candidate is None:
            raise ValueError("Legacy Insurance file candidate not found")
        if candidate.get("sha256") != claimed_sha256:
            raise ValueError("Legacy Insurance file changed during canonicalization")
        approved_document: dict[str, Any] | None = None
        if approved_document_id:
            _, current_matches = await _canonical_documents_for_hash(
                db,
                sha256=claimed_sha256,
                organization_id=organization_id,
                project_id=project_id,
            )
            approved_document = next(
                (
                    row
                    for row in current_matches
                    if str(row.get("_id") or "") == str(approved_document_id)
                ),
                None,
            )
            if approved_document is not None:
                approved_org = str(
                    approved_document.get("organization_id")
                    or approved_document.get("organizationId")
                    or ""
                )
                approved_project = str(
                    approved_document.get("project_id")
                    or approved_document.get("projectId")
                    or ""
                )
                if approved_org != organization_id or approved_project != project_id:
                    approved_document = None
                elif not is_consumable(approved_document):
                    approved_document = None
                elif not approved_document.get("_matching_document_version_id"):
                    approved_document = None
        approved_reuse = bool(
            approved_document
            and candidate.get("classification") == "manual_review"
            and (
                "existing_canonical_document" in candidate.get("findings", [])
                or "duplicate_bytes" in candidate.get("findings", [])
            )
        )
        if candidate["classification"] not in {
            "requires_canonicalization",
            "orphan_file",
        } and not approved_reuse:
            raise ValueError(
                f"Legacy Insurance file requires manual review: {candidate['classification']}"
            )

        document_id = (
            str((approved_document or {}).get("_id") or "")
            if approved_reuse
            else None
        )
        document_version_id = (
            str(
                (approved_document or {}).get("_matching_document_version_id")
                or ""
            )
            or None
            if approved_reuse
            else None
        )
        reused_document = approved_reuse
        if not document_id:
            issue_date = insurance.get("date_of_issue")
            if not issue_date:
                raise ValueError("Legacy Insurance file has no policy issue date")
            date_str = (
                issue_date.isoformat()
                if hasattr(issue_date, "isoformat")
                else str(issue_date)
            )
            source_path = Path(str(candidate["source_path"] or ""))
            with source_path.open("rb") as source:
                upload = UploadFile(
                    file=source,
                    filename=str(candidate.get("filename") or source_path.name),
                )
                document = await controller.create_document(
                    background_tasks=None,
                    file=upload,
                    organization_id=str(insurance.get("organization_id") or ""),
                    project_id=str(insurance.get("project_id") or ""),
                    upload_type="incoming",
                    letter_no=str(insurance.get("policy_number") or insurance_id),
                    date_str=date_str,
                    current_user=current_user,
                    subject=f"Insurance policy {insurance.get('policy_number') or insurance_id}",
                    tags=["insurance"],
                    status="draft",
                    ocr_enabled=True,
                    compression_enabled=False,
                )
            document_id = str(document.id)
            created_document_id = document_id

            _, stored_documents = await _canonical_documents_for_hash(
                db,
                sha256=claimed_sha256,
                organization_id=str(insurance.get("organization_id") or ""),
                project_id=str(insurance.get("project_id") or ""),
            )
            stored_match = next(
                (
                    row
                    for row in stored_documents
                    if str(row.get("_id") or "") == document_id
                ),
                None,
            )
            if stored_match is None:
                raise ValueError(
                    "Canonical Insurance Document bytes differ from the verified legacy source"
                )
            document_version_id = str(
                stored_match.get("_matching_document_version_id") or ""
            ) or None

        if not document_version_id:
            raise ValueError("Canonical Insurance DocumentVersion could not be resolved")

        if owner:
            fenced = await db.insurance_document_migration_claims.update_one(
                {
                    "_id": claim_id,
                    "status": "processing",
                    "owner_token": owner_token,
                },
                {
                    "$set": {
                        "lease_expires_at": datetime.now(timezone.utc)
                        + timedelta(minutes=15),
                        "updated_at": datetime.now(timezone.utc),
                    }
                },
            )
            if not getattr(fenced, "matched_count", 0):
                raise ValueError(
                    "Legacy Insurance canonicalization lease was lost before linking"
                )

        idempotency_key = f"insurance-migration:{insurance_id}:{candidate['sha256']}"
        links = await DocumentRelationshipService(db, policy=policy).link_batch(
            current_user,
            "insurance",
            insurance_id,
            [
                DocumentRelationshipInput(
                    document_id=str(document_id),
                    document_version_id=str(document_version_id),
                    relationship_role="policy",
                )
            ],
            idempotency_key=idempotency_key,
            source="migration",
            source_metadata={
                "legacy_storage_key": candidate.get("storage_key"),
                "legacy_sha256": candidate.get("sha256"),
                "legacy_size": candidate.get("size"),
                "migration_run_id": migration_run_id,
                "semantic_reuse_approved": bool(approved_reuse),
                "approved_document_id": (
                    str(approved_document_id) if approved_reuse else None
                ),
            },
        )
        result = {
            "insurance_id": insurance_id,
            "document_id": str(document_id),
            "document_version_id": str(document_version_id),
            "relationship_id": str(links[0].id),
            "reused_document": reused_document,
            "source_preserved": Path(str(candidate["source_path"])).is_file(),
        }
        if owner:
            await db.insurance_document_migration_claims.update_one(
                {"_id": claim_id, "owner_token": owner_token},
                {
                    "$set": {
                        "status": "complete",
                        "result": result,
                        "updated_at": datetime.now(timezone.utc),
                        "lease_expires_at": None,
                    }
                },
            )
        return result
    except Exception as exc:
        compensation_error: Exception | None = None
        if created_document_id:
            try:
                await controller.compensate_failed_creation(
                    created_document_id,
                    current_user=current_user,
                    reason="insurance_legacy_relationship_failed",
                )
            except Exception as cleanup_exc:
                compensation_error = cleanup_exc
        if owner:
            await db.insurance_document_migration_claims.update_one(
                {"_id": claim_id, "owner_token": owner_token},
                {
                    "$set": {
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "updated_at": datetime.now(timezone.utc),
                        "lease_expires_at": None,
                        "created_document_id": created_document_id,
                        "compensation_status": (
                            "failed" if compensation_error else "complete"
                        )
                        if created_document_id
                        else "not_required",
                        "compensation_error_type": (
                            type(compensation_error).__name__
                            if compensation_error
                            else None
                        ),
                    }
                },
            )
        if compensation_error:
            raise RuntimeError(
                "Legacy Insurance canonicalization failed and compensation "
                f"requires manual review for Document {created_document_id}"
            ) from compensation_error
        raise
