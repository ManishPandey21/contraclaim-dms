"""Phase 8: enclosures, annexures and attachments as first-class child Documents.

A child is an ordinary ``documents`` row that names its parent in
``parent_document_id``. It takes its organisation and project from the parent,
server side, and is authorised on that scope like any other document. Its bytes
are the parent's immutable ``FileObject``, reused and never copied.

Identity is ``(parent_document_id, relationship_type, file sha256)``, held by
the partial unique index ``uq_documents_child_identity``
(``CHILD_DOCUMENT_INDEXES``, created at startup):

* The same bytes under another parent are a separate child.
* The same bytes under the same parent and relationship are the same child, so
  a retry, or a concurrent duplicate, returns the existing one.
* The same bytes under the same parent with a different relationship are a
  separate child, because the relationship is part of the identity.

A child is never refused because its sha256 already exists elsewhere in the
project: the upload duplicate precheck guards letters, and this is not one.

This module is the domain and service layer only. Creating the processing job,
the ``DocumentVersion`` and the canonical evidence, and wiring the enclosure
upload route to this service, are Phase 8 PR B.

Lifecycle:

* **Soft delete** goes through ``DocumentService.delete_document``. That keeps
  the entity-link delete block and the cascade cleanup.
* **A parent's soft delete** cascades to its live children in the same
  transaction, marking each with ``lifecycle_cascade_parent_id``. A child with
  active entity links blocks the parent's delete, exactly as the parent's own
  links do.
* **Restore** is refused while the parent is deleted.
* **Purge** removes the child row only once it is soft-deleted and unlinked. It
  then releases the child's claim on the ``FileObject``, and never deletes
  bytes. Once nothing references the ``FileObject`` any more, it is marked
  ``unreferenced_at``. That mark is a candidate for a future reviewed
  collector, not a deletion.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Mapping, Optional

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import status
from pydantic import ValidationError as PydanticValidationError
from pymongo.errors import DuplicateKeyError

from ..core.permissions import Permissions
from ..models.document import Document
from ..utils.error_handler import DocumentError
from .document_audit_service import DocumentAuditService

CHILD_RELATIONSHIP_TYPES = frozenset({"enclosure", "annexure", "attachment"})

#: Only child rows carry a string `parent_document_id`, so both indexes are
#: partial: top-level documents are not indexed and cannot conflict.
_CHILD_ROWS = {"parent_document_id": {"$type": "string"}}

#: Created at startup by `core.database.ensure_indexes`.
CHILD_DOCUMENT_INDEXES: List[Dict[str, Any]] = [
    {
        "keys": [("parent_document_id", 1), ("relationship_type", 1), ("sha256", 1)],
        "name": "uq_documents_child_identity",
        "unique": True,
        "partialFilterExpression": _CHILD_ROWS,
    },
    {
        "keys": [("parent_document_id", 1), ("lifecycle_state", 1), ("createdAt", 1)],
        "name": "ix_documents_children_by_parent",
        "partialFilterExpression": _CHILD_ROWS,
    },
]


async def ensure_child_document_indexes(db: Any) -> None:
    for index in CHILD_DOCUMENT_INDEXES:
        options = {key: value for key, value in index.items() if key != "keys"}
        await db.documents.create_index(index["keys"], background=True, **options)

#: Lifecycle states in which a parent may not take a new child.
_CLOSED_PARENT_STATES = frozenset({"deleted", "duplicate"})


class ChildDocumentError(DocumentError):
    """Base class for child-document refusals."""


class ChildDocumentNotFoundError(ChildDocumentError):
    def __init__(self, message: str = "Document not found") -> None:
        super().__init__(message, status.HTTP_404_NOT_FOUND)


class ChildDocumentValidationError(ChildDocumentError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status.HTTP_422_UNPROCESSABLE_ENTITY)


class ChildDocumentConflictError(ChildDocumentError):
    def __init__(self, message: str) -> None:
        super().__init__(message, status.HTTP_409_CONFLICT)


@dataclass(frozen=True)
class ChildCreation:
    document: Dict[str, Any]
    #: False when an existing child with the same identity was returned.
    created: bool


@dataclass(frozen=True)
class ChildPurgeResult:
    purged: bool
    file_object_id: Optional[str]
    #: References to the FileObject that remain after the purge, of any kind:
    #: owning document ids, document rows, enclosure entries, document versions.
    remaining_references: int
    #: True when this purge released the last reference.
    file_object_unreferenced: bool


def child_audit_metadata(child: Mapping[str, Any]) -> Dict[str, Any]:
    """The audit metadata for a child. Identifiers only.

    The audit trail is readable more widely than the files it describes, so it
    never carries storage keys, paths, provider locations or file names.
    """
    provenance = child.get("parent_provenance") or {}
    return {
        "parent_document_id": child.get("parent_document_id"),
        "relationship_type": child.get("relationship_type"),
        "file_object_id": child.get("file_object_id"),
        "enclosure_entry_id": provenance.get("enclosure_entry_id"),
        "source_page_range": child.get("source_page_range"),
    }


async def active_child_ids(
    db: Any, parent_document_id: str, *, session: Any = None
) -> List[str]:
    """Ids of the parent's children that are not soft-deleted."""
    session_kwargs = {"session": session} if session is not None else {}
    cursor = db.documents.find(
        {
            "parent_document_id": str(parent_document_id),
            "lifecycle_state": {"$ne": "deleted"},
        },
        projection={"_id": 1},
        **session_kwargs,
    )
    return [str(row["_id"]) async for row in cursor]


def _object_id(value: Any) -> ObjectId:
    try:
        return ObjectId(str(value))
    except (InvalidId, TypeError) as exc:
        raise ChildDocumentNotFoundError() from exc


def _actor_id(current_user: Any) -> str:
    return str(
        getattr(current_user, "id", None)
        or getattr(current_user, "email", None)
        or "system"
    )


def _validated_page_range(
    source_page_range: Optional[Mapping[str, Any]],
) -> Optional[Dict[str, int]]:
    if source_page_range is None:
        return None
    try:
        start = int(source_page_range["start"])
        end = int(source_page_range["end"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ChildDocumentValidationError(
            "source_page_range needs integer start and end"
        ) from exc
    if set(source_page_range) - {"start", "end"} or start < 1 or end < start:
        raise ChildDocumentValidationError(
            "source_page_range must be 1 <= start <= end"
        )
    return {"start": start, "end": end}


class ChildDocumentService:
    def __init__(
        self, db: Any = None, *, policy: Any = None, document_service: Any = None
    ) -> None:
        self.db = db
        self._policy = policy
        self._document_service = document_service

    async def _get_db(self) -> Any:
        if self.db is not None:
            return self.db
        from ..core.database import get_database

        self.db = await get_database()
        return self.db

    async def _get_policy(self) -> Any:
        if self._policy is None:
            from .policy_service import PolicyService

            self._policy = PolicyService(await self._get_db())
        return self._policy

    @property
    def document_service(self) -> Any:
        if self._document_service is None:
            from .document_service import DocumentService

            self._document_service = DocumentService(db=self.db)
        return self._document_service

    async def _audit(
        self,
        current_user: Any,
        child: Mapping[str, Any],
        event_type: str,
        metadata: Dict[str, Any],
    ) -> None:
        await DocumentAuditService(await self._get_db()).emit(
            resource_type="document",
            resource_id=str(child["_id"]),
            event_type=event_type,
            actor_id=_actor_id(current_user),
            organization_id=child.get("organization_id"),
            project_id=child.get("project_id"),
            metadata=metadata,
        )

    async def _load_document(self, document_id: Any) -> Dict[str, Any]:
        db = await self._get_db()
        row = await db.documents.find_one({"_id": _object_id(document_id)})
        if not row:
            raise ChildDocumentNotFoundError()
        return row

    async def _load_child(
        self, current_user: Any, child_id: Any, permission: str
    ) -> Dict[str, Any]:
        row = await self._load_document(child_id)
        # Authorise before revealing anything about the row, including
        # whether it is a child.
        await (await self._get_policy()).authorize_document(
            current_user, permission, row
        )
        if not isinstance(row.get("parent_document_id"), str):
            raise ChildDocumentNotFoundError("Child document not found")
        return row

    # ------------------------------------------------------------------ #
    # Creation
    # ------------------------------------------------------------------ #

    async def create_child(
        self,
        current_user: Any,
        *,
        parent_document_id: str,
        relationship_type: str,
        file_object_id: str,
        enclosure_entry_id: Optional[str] = None,
        source_page_range: Optional[Mapping[str, Any]] = None,
    ) -> ChildCreation:
        """Create the child, or return the existing child with the same identity.

        Scope, sha256, size, MIME type and upload type are all taken from the
        parent and the FileObject on the server. None of them is a parameter.
        """
        if relationship_type not in CHILD_RELATIONSHIP_TYPES:
            raise ChildDocumentValidationError(
                f"relationship_type must be one of {sorted(CHILD_RELATIONSHIP_TYPES)}"
            )
        page_range = _validated_page_range(source_page_range)

        db = await self._get_db()
        parent = await self._load_document(parent_document_id)
        await (await self._get_policy()).authorize_document(
            current_user, Permissions.DOCUMENT_EDIT_METADATA, parent
        )
        parent_id = str(parent["_id"])
        if isinstance(parent.get("parent_document_id"), str):
            raise ChildDocumentConflictError(
                "A child document cannot itself take children"
            )
        if (
            parent.get("lifecycle_state") in _CLOSED_PARENT_STATES
            or parent.get("duplicate_status") == "duplicate"
        ):
            raise ChildDocumentConflictError(
                "The parent document is deleted or a confirmed duplicate"
            )

        organization_id = str(parent.get("organization_id") or "")
        project_id = str(parent.get("project_id") or "")
        # Scoped lookup: another tenant's file object and a missing one answer alike.
        file_object = await db.file_objects.find_one(
            {
                "_id": str(file_object_id),
                "organization_id": organization_id,
                "project_id": project_id,
            }
        )
        if not file_object or not file_object.get("sha256"):
            raise ChildDocumentNotFoundError("File object not found")

        entry: Optional[Dict[str, Any]] = None
        if enclosure_entry_id is not None:
            entry = next(
                (
                    e
                    for e in parent.get("enclosures") or []
                    if str(e.get("id")) == str(enclosure_entry_id)
                ),
                None,
            )
            if entry is None:
                raise ChildDocumentValidationError(
                    "The enclosure entry is not on this parent"
                )
            if str(entry.get("file_object_id") or "") != str(file_object["_id"]):
                raise ChildDocumentValidationError(
                    "The enclosure entry names a different file object"
                )

        identity = {
            "parent_document_id": parent_id,
            "relationship_type": relationship_type,
            "sha256": file_object["sha256"],
        }
        existing = await db.documents.find_one(identity)
        if existing is None:
            if entry is not None and entry.get("child_document_id"):
                raise ChildDocumentConflictError(
                    "The enclosure entry is already linked to another child"
                )
            row = self._child_row(
                current_user, parent, file_object, entry, relationship_type, page_range
            )
            try:
                await db.documents.insert_one(row)
            except DuplicateKeyError:
                existing = await db.documents.find_one(identity)
                if existing is None:  # pragma: no cover - the index guarantees a winner
                    raise
            else:
                await self._link(db, parent_id, entry, row)
                await self._audit(
                    current_user,
                    row,
                    "document.child_created",
                    child_audit_metadata(row),
                )
                return ChildCreation(document=row, created=True)

        if existing.get("lifecycle_state") == "deleted":
            raise ChildDocumentConflictError(
                "A deleted child with this identity exists; restore it instead of re-attaching"
            )
        await self._link(db, parent_id, entry, existing)
        return ChildCreation(document=existing, created=False)

    @staticmethod
    def _child_row(
        current_user: Any,
        parent: Mapping[str, Any],
        file_object: Mapping[str, Any],
        entry: Optional[Mapping[str, Any]],
        relationship_type: str,
        page_range: Optional[Dict[str, int]],
    ) -> Dict[str, Any]:
        now = datetime.utcnow()
        actor = _actor_id(current_user)
        filename = str(
            (entry or {}).get("filename")
            or file_object.get("original_filename")
            or "enclosure"
        )
        locations = file_object.get("locations") or []
        local = next(
            (
                loc.get("path")
                for loc in locations
                if loc.get("provider") == "local" and loc.get("path")
            ),
            None,
        )
        s3 = next(
            (
                loc.get("path")
                for loc in locations
                if loc.get("provider") == "s3" and loc.get("path")
            ),
            None,
        )
        child_oid = ObjectId()
        row: Dict[str, Any] = {
            "_id": child_oid,
            "organization_id": str(parent.get("organization_id") or ""),
            "project_id": str(parent.get("project_id") or ""),
            "project_name": parent.get("project_name"),
            "parent_document_id": str(parent["_id"]),
            "relationship_type": relationship_type,
            "source_page_range": page_range,
            "parent_provenance": {
                "parent_letter_no": parent.get("letterNo"),
                "parent_upload_type": parent.get("uploadType"),
                "enclosure_entry_id": str(entry["id"]) if entry is not None else None,
                "attached_by": actor,
                "attached_at": now,
            },
            "original_filename": filename,
            "filename": filename,
            "filetype": file_object.get("mime_type") or "application/octet-stream",
            "filesize": int(file_object.get("size") or 0),
            "sha256": file_object["sha256"],
            "file_object_id": str(file_object["_id"]),
            "storage_key": file_object.get("storage_key"),
            "filepath_local": local,
            "filepath_s3": s3,
            "storage_locations": [
                {
                    "provider": loc.get("provider"),
                    "path": loc.get("path"),
                    "status": loc.get("status", "ok"),
                }
                for loc in locations
            ],
            "uploadType": parent.get("uploadType"),
            # A child is not a letter: it stays out of letter numbering, letter
            # chains and duplicate-letter detection.
            "letterNo": None,
            "letterNoNormalized": None,
            "date": parent.get("date") or now,
            "subject": filename,
            "status": parent.get("status") or "draft",
            "tags": [],
            "subTags": [],
            "ocrEnabled": False,
            "lifecycle_state": "active",
            "_revision": 1,
            "enclosures": [],
            "references": [],
            "referencedBy": [],
            "createdBy": actor,
            "createdAt": now,
            "updatedAt": now,
        }
        try:
            Document(**{**row, "_id": str(child_oid)})
        except PydanticValidationError as exc:
            raise ChildDocumentValidationError(
                f"The child would not be a valid document: {exc}"
            ) from exc
        return row

    @staticmethod
    async def _link(
        db: Any,
        parent_id: str,
        entry: Optional[Mapping[str, Any]],
        child: Mapping[str, Any],
    ) -> None:
        """Record the child on its FileObject and on its enclosure entry. Idempotent."""
        child_id = str(child["_id"])
        await db.file_objects.update_one(
            {"_id": child["file_object_id"]}, {"$addToSet": {"document_ids": child_id}}
        )
        if entry is None:
            return
        result = await db.documents.update_one(
            {
                "_id": ObjectId(parent_id),
                "enclosures": {
                    "$elemMatch": {
                        "id": entry["id"],
                        "child_document_id": {"$in": [None, child_id]},
                    }
                },
            },
            {"$set": {"enclosures.$.child_document_id": child_id}},
        )
        if not result.matched_count:
            raise ChildDocumentConflictError(
                "The enclosure entry is already linked to another child"
            )

    # ------------------------------------------------------------------ #
    # Reads
    # ------------------------------------------------------------------ #

    async def get_child(self, current_user: Any, child_id: str) -> Dict[str, Any]:
        return await self._load_child(current_user, child_id, Permissions.DOCUMENT_VIEW)

    async def list_children(
        self, current_user: Any, parent_document_id: str
    ) -> List[Dict[str, Any]]:
        """The parent's live children, bounded to the parent's own scope."""
        parent = await self._load_document(parent_document_id)
        await (await self._get_policy()).authorize_document(
            current_user, Permissions.DOCUMENT_VIEW, parent
        )
        db = await self._get_db()
        return (
            await db.documents.find(
                {
                    "parent_document_id": str(parent["_id"]),
                    "organization_id": parent.get("organization_id"),
                    "project_id": parent.get("project_id"),
                    "lifecycle_state": {"$ne": "deleted"},
                }
            )
            .sort("createdAt", 1)
            .to_list(length=None)
        )

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    async def soft_delete_child(
        self, current_user: Any, child_id: str
    ) -> Dict[str, Any]:
        child = await self._load_child(
            current_user, child_id, Permissions.DOCUMENT_DELETE
        )
        if child.get("lifecycle_state") != "deleted":
            await self.document_service.delete_document(str(child["_id"]))
            await self._audit(
                current_user,
                child,
                "document.child_deleted",
                child_audit_metadata(child),
            )
        return await self._load_document(child["_id"])

    async def restore_child(self, current_user: Any, child_id: str) -> Dict[str, Any]:
        child = await self._load_child(
            current_user, child_id, Permissions.DOCUMENT_DELETE
        )
        if child.get("lifecycle_state") != "deleted":
            raise ChildDocumentConflictError("Only a deleted child can be restored")
        db = await self._get_db()
        parent = await db.documents.find_one(
            {"_id": _object_id(child["parent_document_id"])}
        )
        if not parent or parent.get("lifecycle_state") == "deleted":
            raise ChildDocumentConflictError("Restore the parent document first")
        result = await db.documents.update_one(
            {"_id": child["_id"], "lifecycle_state": "deleted"},
            {
                "$set": {"lifecycle_state": "active", "updatedAt": datetime.utcnow()},
                "$unset": {"deletedAt": "", "lifecycle_cascade_parent_id": ""},
                "$inc": {"_revision": 1},
            },
        )
        if not result.matched_count:
            raise ChildDocumentConflictError(
                "The child changed while it was being restored"
            )
        await self._audit(
            current_user, child, "document.child_restored", child_audit_metadata(child)
        )
        return await self._load_document(child["_id"])

    async def purge_child(self, current_user: Any, child_id: str) -> ChildPurgeResult:
        child = await self._load_child(
            current_user, child_id, Permissions.DOCUMENT_DELETE
        )
        if child.get("lifecycle_state") != "deleted":
            raise ChildDocumentConflictError("Soft-delete the child before purging it")
        db = await self._get_db()
        child_key = str(child["_id"])
        if await db.entity_document_links.count_documents(
            {"document_id": child_key, "removed_at": None}
        ):
            raise ChildDocumentConflictError(
                "The child still has active entity relationships"
            )
        file_object_id = child.get("file_object_id")

        async def remove(session: Any) -> bool:
            session_kwargs = {"session": session} if session is not None else {}
            deleted = await db.documents.delete_one(
                {"_id": child["_id"], "lifecycle_state": "deleted"}, **session_kwargs
            )
            if not deleted.deleted_count:
                return False
            await db.document_versions.delete_many(
                {"document_id": child_key}, **session_kwargs
            )
            await db.documents.update_one(
                {"_id": _object_id(child["parent_document_id"])},
                {"$unset": {"enclosures.$[link].child_document_id": ""}},
                array_filters=[{"link.child_document_id": child_key}],
                **session_kwargs,
            )
            if file_object_id:
                await db.file_objects.update_one(
                    {"_id": file_object_id},
                    {"$pull": {"document_ids": child_key}},
                    **session_kwargs,
                )
            return True

        purged = await self._in_transaction(db, remove)
        if not purged:
            raise ChildDocumentConflictError(
                "The child changed while it was being purged"
            )

        remaining = (
            await self.file_object_references(db, file_object_id)
            if file_object_id
            else 0
        )
        unreferenced = False
        if file_object_id and remaining == 0:
            marked = await db.file_objects.update_one(
                {"_id": file_object_id, "unreferenced_at": {"$exists": False}},
                {"$set": {"unreferenced_at": datetime.utcnow()}},
            )
            unreferenced = bool(marked.modified_count)
        await self._audit(
            current_user,
            child,
            "document.child_purged",
            {
                **child_audit_metadata(child),
                "remaining_references": remaining,
                "file_object_unreferenced": unreferenced,
            },
        )
        return ChildPurgeResult(
            purged=True,
            file_object_id=file_object_id,
            remaining_references=remaining,
            file_object_unreferenced=unreferenced,
        )

    @staticmethod
    async def file_object_references(db: Any, file_object_id: str) -> int:
        """Count every remaining claim on a FileObject. Soft-deleted rows count.

        ``document_ids`` alone is not trusted. Before this change a dedupe hit
        never recorded its owner, so documents, enclosure entries and versions
        are counted directly as well.
        """
        file_object = await db.file_objects.find_one(
            {"_id": file_object_id}, {"document_ids": 1}
        )
        owners = len((file_object or {}).get("document_ids") or [])
        rows = await db.documents.count_documents(
            {
                "$or": [
                    {"file_object_id": file_object_id},
                    {"enclosures.file_object_id": file_object_id},
                ]
            }
        )
        versions = await db.document_versions.count_documents(
            {"file_object_id": file_object_id}
        )
        return owners + rows + versions

    @staticmethod
    async def _in_transaction(db: Any, work: Any) -> Any:
        client = getattr(db, "client", None)
        start_session = getattr(client, "start_session", None)
        if not callable(start_session):
            return await work(None)
        session = await start_session()
        async with session:
            return await session.with_transaction(work)
