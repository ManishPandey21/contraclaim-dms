"""Backfill immutable storage records for existing document rows.

Run from the backend environment:
    python -m backend.rbac_backend.scripts.backfill_storage_architecture

The script is idempotent. It only creates file_objects/document_versions for
documents missing file_object_id and creates contract aggregate records for
contract documents missing contract_id.
"""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
from pathlib import Path
from typing import Any, Dict, Optional

from bson import ObjectId

from ..core.database import get_database
from ..models.storage_architecture import ContractAggregate, ContractVersion, FileObject, StorageProviderLocation
from ..services.file_object_service import FileObjectService
from ..services.storage_key_builder import StorageKeyBuilder


def _lookup_id(value: str) -> Any:
    try:
        return ObjectId(value)
    except Exception:
        return value


async def _create_file_object(db: Any, doc: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    local_path = doc.get("filepath_local")
    s3_key = doc.get("filepath_s3")
    data = None
    if local_path:
        path = Path(str(local_path))
        if path.exists() and path.is_file():
            data = path.read_bytes()
    if data is None and not s3_key:
        return None

    document_id = str(doc.get("_id"))
    digest = hashlib.sha256(data or b"").hexdigest() if data is not None else doc.get("sha256") or ""
    if digest:
        existing = await db.file_objects.find_one(
            {
                "sha256": digest,
                "organization_id": doc.get("organization_id"),
                "project_id": doc.get("project_id") or "",
                "document_type": str(doc.get("uploadType") or "document").lower(),
            }
        )
        if existing:
            return existing

    filename = str(doc.get("filename") or "document")
    locations = []
    if local_path:
        locations.append(StorageProviderLocation(provider="local", path=str(local_path), primary=True))
    if s3_key:
        locations.append(StorageProviderLocation(provider="s3", path=str(s3_key), primary=not locations))

    key_builder = StorageKeyBuilder()
    if str(doc.get("uploadType") or "").lower() == "contract":
        storage_key = key_builder.build_contract_key(
            organization=str(doc.get("organization_id") or "ORG"),
            project=str(doc.get("project_id") or "default"),
            safe_filename=filename,
            upload_id=str(doc.get("contract_upload_id") or document_id),
        )
    else:
        storage_key = key_builder.build_document_key(
            organization=str(doc.get("organization_id") or "ORG"),
            project=str(doc.get("project_id") or "PROJ"),
            upload_type=str(doc.get("uploadType") or "incoming"),
            letter_no=doc.get("letterNo"),
            original_filename=filename,
            upload_id=document_id,
        )

    file_object = FileObject(
        sha256=digest or f"legacy:{document_id}",
        size=len(data or b"") or int(doc.get("filesize") or 0),
        mime_type=str(doc.get("filetype") or mimetypes.guess_type(filename)[0] or "application/octet-stream"),
        original_filename=filename,
        stored_filename=Path(storage_key).name,
        storage_key=storage_key,
        primary_provider=locations[0].provider if locations else "legacy",
        locations=locations,
        organization_id=str(doc.get("organization_id") or ""),
        project_id=str(doc.get("project_id") or ""),
        document_id=document_id,
        document_ids=[document_id],
        document_type=str(doc.get("uploadType") or "document").lower(),
        upload_id=str(doc.get("contract_upload_id") or document_id),
        created_by=doc.get("createdBy"),
    )
    await db.file_objects.insert_one(file_object.model_dump(by_alias=True))
    return file_object.model_dump(by_alias=True)


async def backfill() -> None:
    db = await get_database()
    file_object_service = FileObjectService(db=db)
    cursor = db.documents.find(
        {
            "$or": [
                {"file_object_id": {"$exists": False}},
                {"current_version_id": {"$exists": False}},
                {"uploadType": "contract", "contract_id": {"$exists": False}},
            ],
            "lifecycle_state": {"$ne": "deleted"},
        }
    )
    processed = 0
    async for doc in cursor:
        document_id = str(doc.get("_id"))
        file_object = None
        if not doc.get("file_object_id"):
            file_object = await _create_file_object(db, doc)
        else:
            file_object = await db.file_objects.find_one({"_id": doc.get("file_object_id")})
        file_object_id = str(file_object.get("_id")) if file_object else doc.get("file_object_id")
        if not doc.get("current_version_id"):
            await file_object_service.attach_document_version(
                document_id=document_id,
                file_object_id=file_object_id,
                metadata_snapshot=doc,
                current_user=None,
                reason="backfill",
            )
        if str(doc.get("uploadType") or "").lower() == "contract" and not doc.get("contract_id"):
            contract = ContractAggregate(
                organization_id=str(doc.get("organization_id") or ""),
                project_id=str(doc.get("project_id") or ""),
                title=str(doc.get("filename") or "contract"),
                document_ids=[document_id],
                created_by=doc.get("createdBy"),
            )
            contract_result = await db.contracts.insert_one(contract.model_dump(by_alias=True))
            contract_id = str(contract_result.inserted_id)
            version = ContractVersion(
                contract_id=contract_id,
                document_id=document_id,
                upload_id=str(doc.get("contract_upload_id") or document_id),
                version_number=1,
                file_object_id=file_object_id,
                sha256=(file_object or {}).get("sha256"),
                filename=str(doc.get("filename") or "contract"),
                status=str(doc.get("status") or "queued"),
                ingestion_status=str(doc.get("status") or "queued"),
                created_by=doc.get("createdBy"),
            )
            version_result = await db.contract_versions.insert_one(version.model_dump(by_alias=True))
            await db.contracts.update_one(
                {"_id": contract_id},
                {"$set": {"current_version_id": str(version_result.inserted_id)}},
            )
            await db.documents.update_one(
                {"_id": _lookup_id(document_id)},
                {
                    "$set": {
                        "contract_id": contract_id,
                        "contract_version_id": str(version_result.inserted_id),
                    }
                },
            )
        processed += 1
    print(f"Backfill completed. Processed documents: {processed}")


if __name__ == "__main__":
    asyncio.run(backfill())
