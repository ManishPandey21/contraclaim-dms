import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple
from pathlib import Path
import tempfile

from pymongo import ReturnDocument
from pymongo.database import Database
from bson.objectid import ObjectId
from bson.errors import InvalidId

from ..core.database import get_database
from ..core.config import settings
from ..models.document import (
    Document,
    DocumentReference,
    DocumentUpdate,
    Enclosure,
    EnclosureResponse,
    ReferenceCreate,
)
from ..models.document_metadata import extracted_metadata_updates
from ..utils.file_validation import sniff_mime_from_bytes
from ..utils.date_parser import format_date_ddmmyyyy, parse_date_safely
from .common import fetch_paginated, validate_pagination
from .document_processor import create_document_processor
from ..utils.exceptions import DocumentProcessingError as DocumentProcessorError
from ..graph.graph_ingestion_service import GraphIngestionService
from ..utils.notification_service import NotificationService
from ..models.notification import NotificationContext, NotificationType
from ..utils.pipeline_logging import configure_pipeline_logger
from .reference_sync_service import ReferenceSyncService, ReferenceSyncError
from .falkor_graph_service import normalize_letter_code
from .evidence_graph_service import EvidenceGraphService
from .reference_parser import parse_legacy_reference_text

logger = logging.getLogger(__name__)
configure_pipeline_logger(logger)

class DocumentServiceError(Exception):
    """Custom exception for document service errors"""
    pass

class DocumentNotFoundError(DocumentServiceError):
    """Exception raised when document is not found"""
    pass

class InvalidDocumentIdError(DocumentServiceError):
    """Exception raised when document ID is invalid"""
    pass

class DocumentProcessingError(Exception):
    """Custom exception for document processing errors"""
    pass


class DocumentConflictError(DocumentServiceError):
    """Raised when a stale client attempts to overwrite a newer document."""

    def __init__(self, message: str, *, current_revision: Optional[int] = None) -> None:
        super().__init__(message)
        self.current_revision = current_revision

class DocumentService:
    """Async service for document CRUD operations with proper error handling"""
    
    def __init__(self, db: Optional[Database] = None, notification_service: Optional[NotificationService] = None):
        # Allow None for routers that do not pass DB; resolve lazily
        self.db = db
        self.notification_service = notification_service
        self.graph_ingestion = GraphIngestionService()
        self.evidence_graph = EvidenceGraphService(db)
        self.reference_sync_service = ReferenceSyncService(db)

    async def _get_db(self) -> Database:
        if self.db is not None:
            return self.db
        # Lazy import to avoid circulars
        from ..core.database import get_database
        return await get_database()

    async def _sync_current_document_to_falkor(
        self,
        document_id: str,
        *,
        metadata: Any = None,
        upload_type: Optional[str] = None,
        raise_on_error: bool = False,
    ) -> None:
        """Refresh the derived FalkorDB graph from the latest Mongo document."""
        try:
            current = await self.get_document(document_id)
            if not current:
                if raise_on_error:
                    raise DocumentNotFoundError("Document not found while refreshing reference graph")
                return
            payload = current.model_dump(by_alias=True)
            self.graph_ingestion.sync_document_to_falkor(
                document_id=current.id,
                document=payload,
                metadata=metadata,
                upload_type=upload_type or current.uploadType,
                raise_on_error=raise_on_error,
            )
        except Exception as exc:
            logger.debug("FalkorDB refresh failed for %s", document_id, exc_info=True)
            if raise_on_error:
                raise DocumentServiceError(
                    "References were linked, but the reference graph could not be updated. "
                    "Please try syncing again."
                ) from exc

    async def sync_reference_graph(self, document_id: str) -> None:
        """Refresh the reference graph and surface failures to interactive callers."""
        await self._sync_current_document_to_falkor(
            document_id,
            raise_on_error=True,
        )
    
    def _validate_document_id(self, document_id: str) -> ObjectId:
        """
        Validate and convert document ID to ObjectId.
        
        Args:
            document_id: Document ID string to validate
            
        Returns:
            ObjectId instance
            
        Raises:
            InvalidDocumentIdError: If ID format is invalid
        """
        if not document_id or not isinstance(document_id, str):
            raise InvalidDocumentIdError(f"Invalid document ID: {document_id}")
        
        try:
            return ObjectId(document_id)
        except InvalidId as e:
            raise InvalidDocumentIdError(f"Invalid ObjectId format: {document_id}") from e
    
    def _coerce_reference_entry(self, value: Any) -> Optional[Dict[str, Any]]:
        """Normalize raw reference payloads into a consistent dictionary."""
        if value is None:
            return None

        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return None
            parsed = parse_legacy_reference_text(raw)
            if parsed:
                return parsed
            return {"raw": raw, "text": raw}

        if hasattr(value, "model_dump"):
            try:
                data = value.model_dump(by_alias=True, exclude_none=True)  # type: ignore[attr-defined]
            except TypeError:
                data = dict(value)  # type: ignore[arg-type]
        elif hasattr(value, "dict"):
            data = value.dict()
        elif isinstance(value, dict):
            data = {k: v for k, v in value.items() if v not in (None, "", [], {})}
        else:
            raw = str(value).strip()
            return {"raw": raw} if raw else None

        def _clean(val: Any) -> str:
            if val is None:
                return ""
            if isinstance(val, str):
                return val.strip()
            return str(val).strip()

        letter = _clean(data.get("letterNo") or data.get("letter_no"))
        date = _clean(data.get("date"))
        text_value = _clean(data.get("text"))
        raw = _clean(data.get("raw") or letter or text_value)

        parsed_from_text = None
        if not letter and not date:
            parsed_from_text = parse_legacy_reference_text(raw or text_value)
        if parsed_from_text:
            letter = parsed_from_text["letterNo"]
            date = parsed_from_text["date"]
            raw = parsed_from_text["raw"]

        entry: Dict[str, Any] = {}
        if raw:
            entry["raw"] = raw
        if letter:
            entry["letterNo"] = letter
            if data.get("letter_no") or data.get("raw") or parsed_from_text:
                entry["letter_no"] = letter
        if date:
            entry["date"] = date
        if text_value and text_value != letter and not parsed_from_text:
            entry["text"] = text_value

        return entry if entry else None

    def _normalize_metadata_references(self, references: Any) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        if not references:
            return normalized

        seen: Set[Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]] = set()
        for ref in references:
            entry = self._coerce_reference_entry(ref)
            if not entry:
                continue

            date_value = entry.get("date")
            if date_value:
                try:
                    formatted = format_date_ddmmyyyy(date_value)
                    if formatted:
                        entry = dict(entry)
                        entry["date"] = formatted
                except Exception:
                    pass

            key = (
                entry.get("letterNo"),
                entry.get("date"),
                entry.get("text"),
                entry.get("raw"),
            )
            if key in seen:
                continue
            seen.add(key)

            has_structured_fields = any(
                entry.get(field) for field in ("letterNo", "letter_no", "date", "text")
            )
            if "raw" in entry and not has_structured_fields:
                entry = dict(entry)
                entry.pop("raw", None)
                if not entry:
                    continue

            normalized.append(entry)

        return normalized
    async def get_documents(self, skip: int = 0, limit: int = 50) -> List[Optional[Document]]:
        """
        Retrieve multiple documents with pagination.
        
        Args:
            skip: Number of documents to skip (default: 0)
            limit: Maximum number of documents to return (default: 50)
            
        Returns:
            List of Document instances
            
        Raises:
            DocumentServiceError: If retrieval fails
        """
        try:
            skip, limit = validate_pagination(skip, limit)

            logger.info(f"Retrieving documents: skip={skip}, limit={limit}")

            db = await self._get_db()
            raw_documents, _ = await fetch_paginated(
                db.documents,
                skip=skip,
                limit=limit,
            )
            documents: List[Optional[Document]] = []

            for doc_data in raw_documents:
                try:
                    document = Document(**doc_data) if doc_data else None
                    documents.append(document)
                except Exception as e:
                    logger.warning(
                        "Failed to parse document %s: %s",
                        doc_data.get("_id", "unknown") if isinstance(doc_data, dict) else "unknown",
                        e,
                    )
                    documents.append(None)  # Keep position but mark as failed
            
            logger.info(f"Retrieved {len(documents)} documents")
            return documents
            
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Failed to retrieve documents: {e}")
            raise DocumentServiceError(f"Document retrieval failed: {str(e)}")
    
    async def get_document(self, document_id: str) -> Optional[Document]:
        """
        Retrieve a single document by ID.

        Supports both Mongo ObjectId and string/UUID style IDs, since legacy data
        may store _id as a string. Tries ObjectId first, then falls back to raw string.
        """
        try:
            logger.debug(f"Retrieving document: {document_id}")
            db = await self._get_db()

            doc_data = None
            # Try as ObjectId
            try:
                oid = ObjectId(document_id)
                doc_data = await db.documents.find_one({"_id": oid})
            except Exception:
                # Fallback to string id
                doc_data = await db.documents.find_one({"_id": document_id})

            if not doc_data:
                logger.info(f"Document not found: {document_id}")
                return None
            if doc_data.get("lifecycle_state") == "deleted":
                logger.info(f"Document is soft-deleted: {document_id}")
                return None

            document = Document(**doc_data)
            logger.info(f"Retrieved document: {document_id}")
            return document

        except Exception as e:
            logger.error(f"Failed to retrieve document {document_id}: {e}")
            raise DocumentServiceError(f"Document retrieval failed: {str(e)}")

    def _resolve_upload_notification_context(self, document: Document) -> NotificationContext:
        project_id = getattr(document, "project_id", None)
        if project_id and str(project_id).strip():
            return NotificationContext.PROJECT
        return NotificationContext.ORGANIZATION

    def _build_upload_notification_data(self, document: Document, document_id: str) -> Dict[str, Any]:
        upload_type = str(getattr(document, "uploadType", "") or "document").strip().lower()
        upload_label = upload_type.title() if upload_type else "Document"
        document_label = getattr(document, "letterNo", None) or getattr(document, "filename", None) or "Document"
        subject = (getattr(document, "subject", None) or "").strip()

        message = f"{upload_label} document {document_label} uploaded"
        if subject:
            message = f"{message}. Subject: {subject}"

        return {
            "title": "New document uploaded",
            "message": message,
            "document_id": document_id,
            "letter_no": getattr(document, "letterNo", None),
            "subject": subject or None,
            "filename": getattr(document, "filename", None),
            "upload_type": getattr(document, "uploadType", None),
            "organization_id": getattr(document, "organization_id", None),
            "project_id": getattr(document, "project_id", None),
        }
    
    async def create_document(
        self,
        document: Optional[Document] = None,
        emit_upload_notification: bool = True,
        **kwargs,
    ) -> Document:
        """
        Create a new document.
        Accepts either a Document instance or keyword fields from routers.
        """
        try:
            generated_id: Optional[ObjectId] = None
            if document is None:
                # Build Document from kwargs expected by routers
                required = ["filename", "organization_id", "project_id", "upload_type", "letter_no", "date", "current_user"]
                for r in required:
                    if r not in kwargs:
                        raise ValueError(f"Missing required field: {r}")

                file_path = kwargs.get("file_path") or kwargs.get("filepath_local") or kwargs.get("filepath_s3")
                if not file_path:
                    raise ValueError("Missing required field: file_path")
                filename = kwargs["filename"]
                organization_id = kwargs["organization_id"]
                project_id = kwargs["project_id"]
                upload_type = kwargs["upload_type"]
                letter_no = kwargs["letter_no"]
                date = kwargs["date"]
                current_user = kwargs["current_user"]
                pathStructure = kwargs.get("pathStructure")
                pathStructure1 = kwargs.get("pathStructure1")
                filepath_s3 = kwargs.get("filepath_s3", "")

                file_bytes = None
                # Size will be computed only if provided
                filesize = 0
                if file_path and not str(file_path).startswith("s3://"):
                    try:
                        from pathlib import Path
                        p = Path(file_path)
                        if p.exists():
                            filesize = p.stat().st_size
                    except Exception:
                        pass

                # Determine mime
                try:
                    if file_bytes is None and file_path and not str(file_path).startswith("s3://"):
                        with open(file_path, "rb") as f:
                            file_bytes = f.read(5120)
                    mime = sniff_mime_from_bytes(file_bytes or b"")
                except Exception:
                    mime = "application/octet-stream"

                # Honor caller-provided status; default by direction only when omitted.
                status = kwargs.get("status")
                if not status:
                    status = "draft"
                    if upload_type and upload_type.lower() == "incoming":
                        status = "Received"
                    elif upload_type and upload_type.lower() == "outgoing":
                        status = "Sent"

                generated_id = ObjectId()
                document = Document(
                    _id=str(generated_id),
                    organization_id=organization_id,
                    project_id=project_id,
                    filename=filename,
                    filepath_local=file_path,
                    filepath_s3=filepath_s3,
                    pathStructure=pathStructure,
                    pathStructure1=pathStructure1,
                    filetype=mime,
                    filesize=filesize,
                    uploadType=upload_type,
                    letterNo=letter_no,
                    date=date,
                    subject=kwargs.get("subject") or "",
                    from_=kwargs.get("from_"),
                    to=kwargs.get("to"),
                    tags=kwargs.get("tags") or [],
                    subTags=kwargs.get("sub_tags") or [],
                    status=status,
                    ocrEnabled=kwargs.get("ocr_enabled", True),
                    compressionEnabled=kwargs.get("compression_enabled", False),
                    createdBy=getattr(current_user, "id", None) or getattr(current_user, "email", "system"),
                )

            if not isinstance(document, Document):
                raise ValueError("document must be a Document instance")

            logger.info("Creating new document")
            logger.info(
                "[document_pipeline] Upload received for %s (path=%s, upload_type=%s)",
                document.filename,
                getattr(document, "filepath_local", None),
                getattr(document, "uploadType", None),
            )

            # Convert document to dict and insert
            document_dict = document.model_dump(by_alias=True, exclude_unset=True)
            if "_id" in document_dict:
                doc_id_value = document_dict["_id"]
                if isinstance(doc_id_value, ObjectId):
                    document_dict["_id"] = doc_id_value
                elif isinstance(doc_id_value, str):
                    try:
                        document_dict["_id"] = ObjectId(doc_id_value)
                    except InvalidId as exc:
                        if generated_id is not None:
                            document_dict["_id"] = generated_id
                        else:
                            raise DocumentServiceError(f"Invalid document ID format: {doc_id_value}") from exc
                else:
                    document_dict["_id"] = ObjectId(str(doc_id_value))
            elif generated_id is not None:
                document_dict["_id"] = generated_id
            # Remove _id if present to let MongoDB generate it
            document_dict.pop("_id", None)

            if document_dict.get("letterNo"):
                document_dict["letterNoNormalized"] = normalize_letter_code(str(document_dict.get("letterNo")))
            else:
                document_dict["letterNoNormalized"] = None
            document_dict.setdefault("lifecycle_state", "active")
            document_dict.setdefault("_revision", 1)

            db = await self._get_db()
            result = await db.documents.insert_one(document_dict)

            if not result.inserted_id:
                raise DocumentServiceError("Failed to insert document - no ID returned")

            actor = kwargs.get("current_user")
            actor_id = (
                getattr(actor, "id", None)
                or getattr(actor, "email", None)
                or getattr(document, "createdBy", None)
            )

            # Emit notification for new upload
            if self.notification_service and emit_upload_notification:
                try:
                    doc_id = str(result.inserted_id)
                    await self.notification_service.emit(
                        NotificationType.NEW_UPLOAD,
                        doc_id,
                        "document",
                        context=self._resolve_upload_notification_context(document),
                        actor_id=actor_id,
                        data=self._build_upload_notification_data(document, doc_id),
                        actions=[
                            {
                                "key": "view_document",
                                "label": "View Document",
                                "method": "navigate",
                                "href": f"/documentviewer/{doc_id}",
                            }
                        ],
                        resource_link=f"/documentviewer/{doc_id}",
                        dedupe_key=f"document:new_upload:{doc_id}",
                    )
                    logger.info(f"Emitted NEW_UPLOAD notification for document {doc_id}")
                except Exception as e:
                    logger.error(f"Failed to emit NEW_UPLOAD for {result.inserted_id}: {e}")

            # Retrieve the created document
            created_doc_data = await db.documents.find_one({"_id": result.inserted_id})
            if not created_doc_data:
                raise DocumentServiceError("Failed to retrieve created document")
            created_document = Document(**created_doc_data)
            logger.info(f"Created document: {str(result.inserted_id)}")
            logger.info(
                "[document_pipeline] Document %s stored and ready for OCR queue",
                str(result.inserted_id),
            )
            return created_document

        except ValueError:
            raise
        except DocumentServiceError:
            raise
        except Exception as e:
            logger.error(f"Failed to create document: {e}")
            raise DocumentServiceError(f"Document creation failed: {str(e)}")

    
    async def get_document_by_id(self, document_id: str) -> Optional[Document]:
        return await self.get_document(document_id)

    async def enrich_document(self, document: Document) -> Document:
        """
        Enrich document for read operations:
         - Resolve project_name from project_id
         - Resolve Tag/Sub-Tag names from their IDs when possible
        This enrichment only affects read responses and does not modify data at rest.
        """
        try:
            db = await self._get_db()

            # Resolve project name
            project_name = None
            try:
                qid = ObjectId(document.project_id) if isinstance(document.project_id, str) and len(document.project_id) == 24 else document.project_id
                proj = await db.projects.find_one({"_id": qid})
                if proj:
                    project_name = proj.get("name") or proj.get("title")
            except Exception:
                project_name = None

            # Resolve tag names (keep original order; if already name, keep as is)
            tag_names: list[str] = []
            try:
                original_tags = list(document.tags or [])
                obj_ids = []
                name_map: Dict[str, str] = {}
                for t in original_tags:
                    if isinstance(t, str) and len(t) == 24:
                        try:
                            obj_ids.append(ObjectId(t))
                        except Exception:
                            # Not a valid ObjectId, treat as name
                            name_map[str(t)] = str(t)
                    else:
                        name_map[str(t)] = str(t)

                if obj_ids:
                    async for tag_doc in db.tags.find({"_id": {"$in": obj_ids}}):
                        _id = str(tag_doc.get("_id"))
                        name_map[_id] = str(tag_doc.get("name") or _id)

                for t in original_tags:
                    tag_names.append(name_map.get(str(t), str(t)))
            except Exception:
                tag_names = list(document.tags or [])

            # Resolve sub-tag names (keep original order; if already name, keep as is)
            subtag_names: list[str] = []
            try:
                original_subtags = list(document.subTags or [])
                obj_ids = []
                name_map: Dict[str, str] = {}
                for st in original_subtags:
                    if isinstance(st, str) and len(st) == 24:
                        try:
                            obj_ids.append(ObjectId(st))
                        except Exception:
                            name_map[str(st)] = str(st)
                    else:
                        name_map[str(st)] = str(st)

                if obj_ids:
                    async for st_doc in db.subtags.find({"_id": {"$in": obj_ids}}):
                        _id = str(st_doc.get("_id"))
                        name_map[_id] = str(st_doc.get("name") or _id)

                for st in original_subtags:
                    subtag_names.append(name_map.get(str(st), str(st)))
            except Exception:
                subtag_names = list(document.subTags or [])

            # Return enriched copy
            return document.model_copy(
                update={
                    "project_name": project_name,
                    "tags": tag_names or document.tags,
                    "subTags": subtag_names or document.subTags,
                }
            )
        except Exception:
            # On any enrichment failure, return the original document
            return document

    async def validate_update(self, update_data: DocumentUpdate, current_user: Any = None) -> DocumentUpdate:
        if isinstance(update_data, DocumentUpdate):
            return update_data
        try:
            return DocumentUpdate(**(update_data or {}))
        except Exception:
            # Fall back to empty update
            return DocumentUpdate()

    async def list_documents(self, query: Dict[str, Any], pagination: Dict[str, int]) -> tuple[list[Document], int]:
        db = await self._get_db()
        skip = int(pagination.get("skip", 0) or 0)
        limit = int(pagination.get("limit", 50) or 50)
        skip, limit = validate_pagination(skip, limit)
        query = dict(query or {})
        query.setdefault("lifecycle_state", {"$ne": "deleted"})
        raw_items, page = await fetch_paginated(
            db.documents,
            filter=query,
            sort=[("createdAt", -1), ("date", -1), ("_id", -1)],
            skip=skip,
            limit=limit,
            count_total=True,
        )
        items: list[Document] = []
        for raw_doc in raw_items:
            try:
                doc_data = dict(raw_doc)

                if not doc_data.get("createdAt"):
                    for key in ("created_at", "uploadedAt", "uploaded_at", "created_on", "createdOn"):
                        value = doc_data.get(key)
                        if value:
                            doc_data["createdAt"] = value
                            break

                if not doc_data.get("createdAt"):
                    object_id = doc_data.get("_id")
                    if isinstance(object_id, ObjectId):
                        generated = object_id.generation_time
                        doc_data["createdAt"] = generated
                        try:
                            await db.documents.update_one(
                                {"_id": object_id},
                                {"$set": {"createdAt": generated}},
                            )
                        except Exception:
                            logger.debug("Unable to persist inferred createdAt for %s", object_id, exc_info=True)

                if "updatedAt" not in doc_data:
                    for key in ("updated_at", "updated_on", "updatedOn"):
                        value = doc_data.get(key)
                        if value:
                            doc_data["updatedAt"] = value
                            break

                items.append(Document(**doc_data))
            except Exception:
                continue
        total = page.total if page.total is not None else len(items)
        return items, total

    async def add_enclosure(
        self,
        document_id: str,
        content: bytes,
        filename: str,
        current_user: Any = None,
        *,
        filepath_local: Optional[str] = None,
        filepath_s3: Optional[str] = None,
        storage_key: Optional[str] = None,
        file_object_id: Optional[str] = None,
        storage_locations: Optional[List[Dict[str, Any]]] = None,
        filetype: Optional[str] = None,
        filesize: Optional[int] = None,
    ) -> EnclosureResponse:
        db = await self._get_db()
        doc_oid = self._validate_document_id(document_id)
        enc = Enclosure(
            id=str(ObjectId()),
            filename=filename,
            filepath_local=filepath_local,
            filepath_s3=filepath_s3,
            presigned_url=None,
            filetype=filetype or sniff_mime_from_bytes(content, filename),
            filesize=int(filesize if filesize is not None else len(content or b"")),
            uploadedBy=(getattr(current_user, "id", None) or getattr(current_user, "email", "system")),
        )
        enc_doc = enc.model_dump(by_alias=True)
        if storage_key:
            enc_doc["storage_key"] = storage_key
        if file_object_id:
            enc_doc["file_object_id"] = file_object_id
        if storage_locations:
            enc_doc["storage_locations"] = storage_locations
        await db.documents.update_one({"_id": doc_oid}, {"$push": {"enclosures": enc_doc}})
        return EnclosureResponse(enclosure=enc, message="Enclosure added successfully")

    async def queue_document_processing(
        self,
        document: Document,
        file_path: str,
        *,
        requested_by: Optional[str] = None,
        force: bool = False,
    ) -> str:
        """Create a durable processing job for an uploaded document."""

        db = await self._get_db()
        now = datetime.utcnow()
        active_statuses = ["queued", "processing", "retrying"]
        if not force:
            existing = await db.document_processing_jobs.find_one(
                {
                    "document_id": document.id,
                    "status": {"$in": active_statuses},
                },
                sort=[("created_at", -1)],
            )
            if existing:
                job_id = str(existing["_id"])
                await db.documents.update_one(
                    {"_id": self._validate_document_id(document.id)},
                    {
                        "$set": {
                            "processing_status": existing.get("status", "queued"),
                            "processing_job_id": job_id,
                            "updatedAt": now,
                        }
                    },
                )
                return job_id

        job_id = str(ObjectId())
        job = {
            "_id": job_id,
            "document_id": document.id,
            "file_path": file_path,
            "sha256": getattr(document, "sha256", None),
            "organization_id": document.organization_id,
            "project_id": document.project_id,
            "upload_type": document.uploadType,
            "status": "queued",
            "stage": "queued",
            "attempts": 0,
            "max_attempts": 3,
            "requested_by": requested_by,
            "created_at": now,
            "queued_at": now,
            "updated_at": now,
        }
        await db.document_processing_jobs.insert_one(job)
        await db.documents.update_one(
            {"_id": self._validate_document_id(document.id)},
            {
                "$set": {
                    "processing_status": "queued",
                    "processing_job_id": job_id,
                    "processing_error": None,
                    "updatedAt": now,
                }
            },
        )
        logger.info(
            "[document_pipeline] Durable processing job queued document_id=%s job_id=%s file=%s",
            document.id,
            job_id,
            file_path,
        )
        return job_id

    async def get_processing_status(self, document_id: str) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        document = await self.get_document(document_id)
        if not document:
            return None
        job = None
        if getattr(document, "processing_job_id", None):
            job = await db.document_processing_jobs.find_one({"_id": document.processing_job_id})
        if not job:
            job = await db.document_processing_jobs.find_one(
                {"document_id": document_id},
                sort=[("created_at", -1)],
            )
        if not job:
            return {
                "_id": "",
                "document_id": document_id,
                "status": getattr(document, "processing_status", None) or "not_queued",
                "stage": None,
                "attempts": 0,
                "max_attempts": 0,
                "error": getattr(document, "processing_error", None),
                "metadata": getattr(document, "processing_metadata", None),
                "created_at": getattr(document, "createdAt", None) or datetime.utcnow(),
                "updated_at": getattr(document, "updatedAt", None) or datetime.utcnow(),
            }
        return job

    async def process_next_processing_jobs(self, *, limit: int = 3) -> int:
        """Claim and process queued durable jobs. Intended for worker/background loops."""

        processed = 0
        for _ in range(max(1, limit)):
            job = await self._claim_next_processing_job()
            if not job:
                break
            await self.process_document_job(str(job["_id"]), claimed_job=job)
            processed += 1
        return processed

    async def _claim_next_processing_job(self) -> Optional[Dict[str, Any]]:
        db = await self._get_db()
        now = datetime.utcnow()
        return await db.document_processing_jobs.find_one_and_update(
            {
                "status": {"$in": ["queued", "retrying"]},
                "$or": [
                    {"run_after": {"$exists": False}},
                    {"run_after": {"$lte": now}},
                ],
            },
            {
                "$set": {
                    "status": "processing",
                    "stage": "claimed",
                    "started_at": now,
                    "updated_at": now,
                },
                "$inc": {"attempts": 1},
            },
            sort=[("created_at", 1)],
            return_document=ReturnDocument.AFTER,
        )

    async def process_document_job(
        self,
        job_id: str,
        *,
        claimed_job: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Run a durable document-processing job and update terminal state."""

        db = await self._get_db()
        job = claimed_job or await db.document_processing_jobs.find_one({"_id": job_id})
        if not job:
            logger.warning("Document processing job %s not found", job_id)
            return False

        if job.get("status") in {"completed", "dead_lettered"}:
            return job.get("status") == "completed"

        if job.get("status") != "processing":
            job = await db.document_processing_jobs.find_one_and_update(
                {"_id": job_id, "status": {"$in": ["queued", "retrying"]}},
                {
                    "$set": {
                        "status": "processing",
                        "stage": "started",
                        "started_at": datetime.utcnow(),
                        "updated_at": datetime.utcnow(),
                    },
                    "$inc": {"attempts": 1},
                },
                return_document=ReturnDocument.AFTER,
            )
            if not job:
                return False

        document_id = str(job["document_id"])
        now = datetime.utcnow()
        await db.documents.update_one(
            {"_id": self._validate_document_id(document_id)},
            {
                "$set": {
                    "processing_status": "processing",
                    "processing_job_id": job_id,
                    "processing_error": None,
                    "updatedAt": now,
                }
            },
        )
        try:
            ok = await self.process_document_async(
                document_id=document_id,
                file_path=str(job.get("file_path") or ""),
                organization_id=job.get("organization_id"),
                project_id=job.get("project_id"),
                upload_type=job.get("upload_type"),
                job_id=job_id,
            )
        except Exception as exc:
            logger.exception("Document processing job failed document_id=%s job_id=%s", document_id, job_id)
            ok = False
            await self._mark_processing_failure(job, str(exc))

        if ok:
            completed_at = datetime.utcnow()
            await db.document_processing_jobs.update_one(
                {"_id": job_id},
                {
                    "$set": {
                        "status": "completed",
                        "stage": "completed",
                        "completed_at": completed_at,
                        "updated_at": completed_at,
                        "error": None,
                    }
                },
            )
            await db.documents.update_one(
                {"_id": self._validate_document_id(document_id)},
                {
                    "$set": {
                        "processing_status": "completed",
                        "processing_job_id": job_id,
                        "processed_at": completed_at,
                        "processing_error": None,
                        "updatedAt": completed_at,
                    }
                },
            )
            try:
                from .document_audit_service import DocumentAuditService
                from .observability import observability_registry

                document = await self.get_document(document_id)
                await DocumentAuditService().emit(
                    resource_type="document",
                    resource_id=document_id,
                    event_type="document.processing_completed",
                    organization_id=getattr(document, "organization_id", None),
                    project_id=getattr(document, "project_id", None),
                    metadata={"job_id": job_id},
                )
                await observability_registry.record_domain_event(
                    resource_type="document",
                    event_type="processing_completed",
                )
            except Exception:
                logger.debug("Failed to emit processing completion audit for %s", document_id, exc_info=True)
            return True

        latest = await db.document_processing_jobs.find_one({"_id": job_id})
        if latest and latest.get("status") not in {"failed", "retrying", "dead_lettered"}:
            failure_message = "Document processing failed"
            latest_error = latest.get("error")
            if isinstance(latest_error, dict) and latest_error.get("message"):
                failure_message = str(latest_error["message"])
            else:
                failed_document = await db.documents.find_one(
                    {"_id": self._validate_document_id(document_id)},
                    {"processing_error": 1},
                )
                document_error = (failed_document or {}).get("processing_error")
                if isinstance(document_error, dict) and document_error.get("message"):
                    failure_message = str(document_error["message"])
            await self._mark_processing_failure(latest, failure_message)
        return False

    async def _mark_processing_failure(self, job: Dict[str, Any], message: str) -> None:
        db = await self._get_db()
        now = datetime.utcnow()
        attempts = int(job.get("attempts") or 0)
        max_attempts = int(job.get("max_attempts") or 3)
        terminal = attempts >= max_attempts
        status_value = "dead_lettered" if terminal else "retrying"
        error = {
            "message": message,
            "timestamp": now,
            "attempts": attempts,
            "terminal": terminal,
        }
        update: Dict[str, Any] = {
            "$set": {
                "status": status_value,
                "stage": "failed",
                "error": error,
                "updated_at": now,
            }
        }
        if not terminal:
            update["$set"]["run_after"] = (now + timedelta(seconds=min(300, 30 * max(1, attempts)))).replace(microsecond=0)
        await db.document_processing_jobs.update_one({"_id": job["_id"]}, update)
        await db.documents.update_one(
            {"_id": self._validate_document_id(str(job["document_id"]))},
            {
                "$set": {
                    "processing_status": "failed" if terminal else "retrying",
                    "processing_error": error,
                    "updatedAt": now,
                }
            },
        )
        try:
            from .document_audit_service import DocumentAuditService
            from .observability import observability_registry

            await DocumentAuditService().emit(
                resource_type="document",
                resource_id=str(job["document_id"]),
                event_type="document.processing_failed",
                organization_id=job.get("organization_id"),
                project_id=job.get("project_id"),
                metadata={"job_id": job.get("_id"), "terminal": terminal, "message": message},
            )
            await observability_registry.record_domain_event(
                resource_type="document",
                event_type="processing_failed" if terminal else "processing_retrying",
            )
        except Exception:
            logger.debug("Failed to emit processing failure audit for %s", job.get("document_id"), exc_info=True)

    async def process_document_async(
        self,
        document_id: str,
        file_path: str,
        *,
        organization_id: Optional[str] = None,
        project_id: Optional[str] = None,
        upload_type: Optional[str] = None,
        job_id: Optional[str] = None,
    ) -> bool:
        """Background worker entrypoint for OCR/metadata processing."""

        logger.info("Processing document %s asynchronously", document_id)
        logger.info(
            "[document_pipeline] Starting asynchronous pipeline for %s (file=%s)",
            document_id,
            file_path,
        )
        metadata = None
        metadata_source: Optional[str] = None
        metadata_references: List[Dict[str, Any]] = []
        db: Optional[Database] = None
        doc_oid: Optional[ObjectId] = None
        try:
            db = await self._get_db()
            doc_oid = self._validate_document_id(document_id)
            stored = await db.documents.find_one({"_id": doc_oid})
            if not stored:
                logger.warning("Document %s not found for processing", document_id)
                return False

            document = Document(**stored)
            org_id = organization_id or document.organization_id
            proj_id = project_id or document.project_id
            upload = upload_type or document.uploadType
            logger.info(
                "[document_pipeline] Loaded document metadata for %s (org=%s, project=%s, upload_type=%s)",
                document_id,
                org_id,
                proj_id,
                upload,
            )
            now = datetime.utcnow()
            await db.documents.update_one(
                {"_id": doc_oid},
                {
                    "$set": {
                        "processing_status": "processing",
                        "processing_job_id": job_id or getattr(document, "processing_job_id", None),
                        "updatedAt": now,
                    }
                },
            )
            if job_id:
                await db.document_processing_jobs.update_one(
                    {"_id": job_id},
                    {"$set": {"stage": "materializing", "updated_at": now}},
                )

            local_path = Path(file_path)
            if not local_path.exists():
                s3_key = getattr(document, "filepath_s3", None) or file_path
                if s3_key:
                    try:
                        from .s3_service import S3Service

                        s3 = S3Service()
                        data = await s3.download_bytes(s3_key)
                        temp_dir = Path(tempfile.gettempdir()) / "document_processing"
                        temp_dir.mkdir(parents=True, exist_ok=True)
                        fallback_name = document.filename or Path(s3_key).name or f"{document_id}.bin"
                        local_path = temp_dir / fallback_name
                        local_path.write_bytes(data)
                        file_path = str(local_path)
                    except Exception as exc:
                        logger.error("Failed to download document %s from S3: %s", document_id, exc)
                        raise DocumentProcessorError(f"File unavailable for processing: {exc}") from exc
            else:
                file_path = str(local_path)

            processor = create_document_processor()
            try:
                if job_id:
                    await db.document_processing_jobs.update_one(
                        {"_id": job_id},
                        {"$set": {"stage": "extracting", "updated_at": datetime.utcnow()}},
                    )
                logger.info(
                    "[document_pipeline] Submitting document %s to processor (path_structure=%s)",
                    document_id,
                    f"{org_id}/{proj_id}",
                )
                result = await processor.process_document(
                    pdf_path=file_path,
                    path_structure=f"{org_id}/{proj_id}",
                    upload_type=upload,
                    document_id=document_id,
                )
            except DocumentProcessorError as exc:
                logger.error("Document processor failed for %s: %s", document_id, exc)
                result = None

            update_fields: Dict[str, Any] = {"updatedAt": datetime.utcnow()}

            if not result:
                update_fields["processing_error"] = {
                    "message": "Document processing failed",
                    "timestamp": datetime.utcnow(),
                }
            elif getattr(result, "success", False):
                metadata_source = getattr(result, "metadata_source", None) or "legacy_regex"
                logger.info(
                    "Document %s metadata extracted using %s parser",
                    document_id,
                    metadata_source,
                )
                logger.info(
                    "[document_pipeline] Metadata extraction finished for %s using %s parser",
                    document_id,
                    metadata_source,
                )
                metadata = getattr(result, "metadata", None)
                update_fields["ocrEnabled"] = True
                update_fields["processing_status"] = "metadata_extracted"
                update_fields["metadata_source"] = metadata_source
                update_fields["processing_metadata"] = {
                    "processed_at": datetime.utcnow(),
                    "processing_time": getattr(result, "processing_time", None),
                    "chunks_created": getattr(result, "chunks_created", None),
                    "metadata_source": metadata_source,
                }
                partial_failures = getattr(result, "partial_failures", None) or {}
                if partial_failures:
                    update_fields["processing_metadata"]["partial_failures"] = partial_failures

            if metadata:
                if getattr(metadata, "summary", None):
                    update_fields["summary"] = metadata.summary
                if getattr(metadata, "keywords", None):
                    update_fields["keywords"] = metadata.keywords
                if getattr(metadata, "contractual_clauses", None):
                    update_fields["contractual_clauses"] = metadata.contractual_clauses
                metadata_reference_values = getattr(metadata, "references", None)
                if metadata_reference_values is not None:
                    normalized_refs = self._normalize_metadata_references(
                        metadata_reference_values
                    )
                    update_fields["reference"] = normalized_refs
                    metadata_references = normalized_refs
                if getattr(metadata, "full_content", None):
                    update_fields["full_text"] = metadata.full_content
                if getattr(metadata, "subject", None):
                    update_fields["subject"] = metadata.subject
                if getattr(metadata, "letter_no", None):
                    update_fields["letterNo"] = metadata.letter_no
                    update_fields["letterNoNormalized"] = normalize_letter_code(str(metadata.letter_no))
                if getattr(metadata, "from_company", None):
                    update_fields["from"] = metadata.from_company
                if getattr(metadata, "to_company", None):
                    update_fields["to"] = metadata.to_company
                if getattr(metadata, "date", None):
                    try:
                        update_fields["date"] = parse_date_safely(metadata.date)
                    except Exception:
                        logger.debug("Unable to parse metadata date for %s", document_id)
                update_fields.update(extracted_metadata_updates(metadata))

                graph_document_payload = document.model_dump(by_alias=True)
                graph_document_payload.update(update_fields)
                try:
                    await self.graph_ingestion.ingest_document(
                        document_id=document_id,
                        document_data=graph_document_payload,
                        metadata=metadata,
                        metadata_source=metadata_source,
                        upload_type=upload,
                    )
                except Exception:
                    logger.debug("Graph ingestion failed for %s", document_id, exc_info=True)

                try:
                    await self.evidence_graph.ingest_document_metadata(
                        document_id=document_id,
                        document_data=graph_document_payload,
                        metadata=metadata,
                        metadata_source=metadata_source,
                        upload_type=upload,
                    )
                except Exception:
                    logger.debug("Evidence graph extraction failed for %s", document_id, exc_info=True)

                if getattr(result, "processed_path", None):
                    update_fields["processed_path"] = result.processed_path
                update_fields["processing_error"] = None
            else:
                update_fields["processing_error"] = {
                    "message": getattr(result, "error", "Document processing failed"),
                    "timestamp": datetime.utcnow(),
                }

            await db.documents.update_one({"_id": doc_oid}, {"$set": update_fields})
            logger.info("[document_pipeline] Database record updated for %s", document_id)
            if job_id:
                await db.document_processing_jobs.update_one(
                    {"_id": job_id},
                    {
                        "$set": {
                            "stage": "metadata_updated" if metadata else "failed",
                            "metadata": {
                                "metadata_source": metadata_source,
                                "chunks_created": getattr(result, "chunks_created", None) if result else None,
                                "partial_failures": getattr(result, "partial_failures", None) if result else None,
                            },
                            "updated_at": datetime.utcnow(),
                        }
                    },
                )

            if metadata is not None:
                try:
                    if job_id:
                        await db.document_processing_jobs.update_one(
                            {"_id": job_id},
                            {"$set": {"stage": "syncing_references", "updated_at": datetime.utcnow()}},
                        )
                    await self.reference_sync_service.sync_bidirectional(
                        document_id=document_id,
                        references=metadata_references,
                        source="parser",
                        default_link_type="indirect",
                        clear_existing=True,
                    )
                except ReferenceSyncError as exc:
                    raise DocumentProcessingError(
                        "Metadata was saved, but the extracted references could not be linked. "
                        "Please retry document processing."
                    ) from exc
                except Exception as exc:
                    logger.exception(
                        "Unexpected error while synchronising references for %s",
                        document_id,
                    )
                    raise DocumentProcessingError(
                        "Metadata was saved, but reference linking failed unexpectedly. "
                        "Please retry document processing."
                    ) from exc

                if job_id:
                    await db.document_processing_jobs.update_one(
                        {"_id": job_id},
                        {"$set": {"stage": "syncing_falkor", "updated_at": datetime.utcnow()}},
                    )
                await self._sync_current_document_to_falkor(
                    document_id,
                    metadata=metadata,
                    upload_type=upload,
                    raise_on_error=True,
                )
            return bool(result and getattr(result, "success", False) and metadata)
        except Exception as exc:
            logger.exception("Unexpected error while processing document %s", document_id)
            error = {
                "message": str(exc) or "Document processing failed",
                "timestamp": datetime.utcnow(),
            }
            try:
                if db is not None and doc_oid is not None:
                    await db.documents.update_one(
                        {"_id": doc_oid},
                        {
                            "$set": {
                                "processing_status": "failed",
                                "processing_error": error,
                                "updatedAt": datetime.utcnow(),
                            }
                        },
                    )
                if job_id and db is not None:
                    await db.document_processing_jobs.update_one(
                        {"_id": job_id},
                        {
                            "$set": {
                                "stage": "failed",
                                "error": error,
                                "updated_at": datetime.utcnow(),
                            }
                        },
                    )
            except Exception:
                logger.exception(
                    "Unable to persist processing failure for document %s",
                    document_id,
                )
            return False

    async def list_enclosures(self, document_id: str) -> List[Enclosure]:
        document = await self.get_document(document_id)
        if not document:
            raise DocumentNotFoundError("Document not found")
        return list(document.enclosures or [])

    async def remove_enclosure(self, document_id: str, enclosure_id: str) -> bool:
        db = await self._get_db()
        document = await self.get_document(document_id)
        if not document:
            raise DocumentNotFoundError("Document not found")

        remaining: List[Enclosure] = [
            enc for enc in document.enclosures or [] if enc.id != enclosure_id
        ]
        if len(remaining) == len(document.enclosures or []):
            raise DocumentServiceError("Enclosure not found")

        await db.documents.update_one(
            {"_id": self._validate_document_id(document_id)},
            {
                "$set": {
                    "enclosures": [enc.model_dump(by_alias=True) for enc in remaining],
                    "updatedAt": datetime.utcnow(),
                }
            },
        )
        return True

    async def list_references(
        self,
        document_id: str,
    ) -> Dict[str, Any]:
        document = await self.get_document(document_id)
        if not document:
            raise DocumentNotFoundError("Document not found")

        db = await self._get_db()
        parsed_references: List[Dict[str, Any]] = []
        raw_parsed = getattr(document, "reference", None)
        seen_parsed: Set[Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]] = set()

        try:
            if isinstance(raw_parsed, list):
                items = list(raw_parsed)
            elif isinstance(raw_parsed, str):
                items = [p.strip() for p in raw_parsed.splitlines() if p and p.strip()]
            else:
                items = []

            for item in items:
                entry = self._coerce_reference_entry(item)
                if not entry:
                    continue
                key = (
                    entry.get("letterNo"),
                    entry.get("date"),
                    entry.get("text"),
                    entry.get("raw"),
                )
                if key in seen_parsed:
                    continue
                seen_parsed.add(key)
                parsed_references.append(entry)
        except Exception:
            parsed_references = []

        linked: List[Dict[str, Any]] = []

        for ref in document.references or []:
            target_data: Optional[Dict[str, Any]] = None
            try:
                target_oid = self._validate_document_id(ref.documentId)
                target_data = await db.documents.find_one({"_id": target_oid})
            except InvalidDocumentIdError:
                target_data = await db.documents.find_one({"_id": ref.documentId})

            entry: Dict[str, Any] = {
                "id": ref.documentId,
                "documentId": ref.documentId,
                "linkType": ref.linkType,
                "description": ref.description,
                "linkedAt": ref.linkedAt.isoformat() if ref.linkedAt else None,
            }

            if target_data:
                try:
                    target_doc = Document(**target_data)
                    entry.update(
                        {
                            "letterNo": target_doc.letterNo,
                            "title": target_doc.subject,
                            "date": target_doc.date.isoformat() if target_doc.date else None,
                        }
                    )
                except Exception:
                    entry.update(
                        {
                            "letterNo": target_data.get("letterNo"),
                            "title": target_data.get("subject"),
                            "date": target_data.get("date"),
                        }
                    )

            linked.append(entry)

        return {"parsed": parsed_references, "linked": linked}

    async def add_reference(
        self,
        document_id: str,
        payload: ReferenceCreate,
        current_user: Any = None,
    ) -> Document:
        document = await self.get_document(document_id)
        if not document:
            raise DocumentNotFoundError("Document not found")

        target = await self.get_document(payload.referenced_document_id)
        if not target:
            raise DocumentNotFoundError("Referenced document not found")

        if document.id == target.id:
            raise DocumentServiceError("Document cannot reference itself")

        linked_by = getattr(current_user, "id", None) or getattr(
            current_user, "email", "system"
        )
        linked_at = datetime.utcnow()

        reference_payload = {
            "documentId": target.id,
            "letterNo": target.letterNo,
            "linkType": payload.link_type,
            "description": payload.description,
            "linkedAt": linked_at,
            "linkedBy": linked_by,
            "source": "manual",
        }

        manual_payloads: List[Dict[str, Any]] = []
        for ref in document.references or []:
            source_label = (ref.source or "").lower()
            if source_label not in ("", "manual"):
                continue
            if ref.documentId == target.id:
                continue
            manual_payloads.append(ref.model_dump(by_alias=True, exclude_none=True))
        manual_payloads.append(reference_payload)

        try:
            await self.reference_sync_service.sync_bidirectional(
                document_id=document_id,
                references=manual_payloads,
                source="manual",
                default_link_type=payload.link_type or "direct",
            )
        except ReferenceSyncError as exc:
            logger.error(
                "Reference sync failed for manual link %s -> %s: %s",
                document_id,
                payload.referenced_document_id,
                exc,
            )
            raise DocumentServiceError(str(exc)) from exc
        except Exception as exc:
            logger.exception(
                "Unexpected error while syncing manual reference %s -> %s",
                document_id,
                payload.referenced_document_id,
            )
            raise DocumentServiceError(
                f"Manual reference addition failed: {exc}"
            ) from exc

        updated = await self.get_document(document_id)
        if not updated or not any(ref.documentId == target.id for ref in updated.references or []):
            raise DocumentServiceError("Unable to resolve referenced document")

        await self._sync_current_document_to_falkor(document_id)
        return updated

    async def remove_reference(
        self,
        document_id: str,
        reference_document_id: str,
    ) -> Document:
        db = await self._get_db()
        document = await self.get_document(document_id)
        if not document:
            raise DocumentNotFoundError("Document not found")

        references = [
            ref
            for ref in document.references or []
            if not (
                ref.documentId == reference_document_id
                and (ref.source or "").lower() in ("", "manual")
            )
        ]
        if len(references) == len(document.references or []):
            raise DocumentServiceError("Reference not found")

        await db.documents.update_one(
            {"_id": self._validate_document_id(document_id)},
            {
                "$set": {
                    "references": [ref.model_dump(by_alias=True) for ref in references],
                    "updatedAt": datetime.utcnow(),
                }
            },
        )

        target = await self.get_document(reference_document_id)
        if target:
            remaining = [
                ref
                for ref in target.referencedBy or []
                if not (
                    ref.documentId == document.id
                    and (ref.source or "").lower() in ("", "manual")
                )
            ]
            await db.documents.update_one(
                {"_id": self._validate_document_id(target.id)},
                {
                    "$set": {
                        "referencedBy": [
                            ref.model_dump(by_alias=True) for ref in remaining
                        ],
                        "updatedAt": datetime.utcnow(),
                    }
                },
            )

        updated = await self.get_document(document_id)
        await self._sync_current_document_to_falkor(document_id)
        return updated

    async def list_linked_documents(self, document_id: str) -> List[Dict[str, Any]]:
        references = await self.list_references(document_id)
        return list(references.get("linked", []))

    async def get_documents_by_ids(self, document_ids: List[str]) -> List[Document]:
        """Fetch documents for the provided identifiers preserving the requested order."""
        if not document_ids:
            return []

        db = await self._get_db()
        normalized_ids: List[ObjectId] = []
        string_ids: List[str] = []
        for raw_id in document_ids:
            if not raw_id:
                continue
            try:
                normalized_ids.append(self._validate_document_id(str(raw_id)))
            except InvalidDocumentIdError:
                string_ids.append(str(raw_id))

        filters: List[Dict[str, Any]] = []
        if normalized_ids:
            filters.append({"_id": {"$in": normalized_ids}})
        if string_ids:
            filters.append({"_id": {"$in": string_ids}})

        if not filters:
            return []

        query = {"$or": filters} if len(filters) > 1 else filters[0]
        cursor = db.documents.find(query)
        raw_documents = await cursor.to_list(length=None)

        by_id: Dict[str, Document] = {}
        for doc_raw in raw_documents:
            try:
                parsed = Document(**doc_raw)
                by_id[str(parsed.id)] = parsed
            except Exception:
                continue

        ordered: List[Document] = []
        for identifier in document_ids:
            doc = by_id.get(str(identifier))
            if doc:
                ordered.append(doc)
        return ordered

    async def list_documents_by_letter_no(
        self,
        letter_no: str,
        limit: int = 10,
    ) -> List[Document]:
        """Return recent documents that share the given letter number."""
        if not letter_no:
            return []

        db = await self._get_db()
        cursor = (
            db.documents.find({"letterNo": letter_no})
            .sort("createdAt", -1)
            .limit(limit)
        )
        raw_documents = await cursor.to_list(length=limit)
        documents: List[Document] = []
        for raw in raw_documents:
            try:
                documents.append(Document(**raw))
            except Exception:
                continue
        return documents

    # ---------------------------------------------
    # Comments support for documents (used by FE)
    # ---------------------------------------------
    async def get_comments(self, document_id: str) -> List[Dict[str, Any]]:
        """
        Return comments for the given document as a list of dicts:
        [{ id, text, author, createdAt }, ...]
        """
        db = await self._get_db()
        # Validate document exists (raises or returns None)
        doc = await self.get_document(document_id)
        if not doc:
            raise DocumentNotFoundError("Document not found")

        out: List[Dict[str, Any]] = []
        async for c in db.document_comments.find(
            {"document_id": str(document_id)}
        ).sort("createdAt", 1):
            out.append(self._serialize_comment(c))
        return out

    async def add_comment(self, document_id: str, text: str, current_user: Any = None) -> Dict[str, Any]:
        """
        Add a comment to a document. Stores in 'document_comments' collection.
        Returns the inserted comment document.
        """
        if not text or not text.strip():
            raise DocumentServiceError("Comment text is required")

        db = await self._get_db()
        doc = await self.get_document(document_id)
        if not doc:
            raise DocumentNotFoundError("Document not found")

        author_id = getattr(current_user, "id", None) or getattr(current_user, "_id", None)
        author_email = getattr(current_user, "email", None)
        author_name = (
            getattr(current_user, "full_name", None)
            or getattr(current_user, "username", None)
            or getattr(current_user, "name", None)
            or author_email
            or author_id
            or "system"
        )
        roles_raw = getattr(current_user, "roles", None)
        author_roles = [str(role) for role in roles_raw] if isinstance(roles_raw, (list, tuple, set)) else []

        created_at = datetime.now(timezone.utc)

        payload = {
            "document_id": str(document_id),
            "text": text.strip(),
            "author": author_name,
            "author_id": author_id,
            "author_name": author_name,
            "author_email": author_email,
            "author_roles": author_roles,
            "createdAt": created_at,
        }
        result = await db.document_comments.insert_one(payload)
        payload["_id"] = result.inserted_id
        return self._serialize_comment(payload)

    def _serialize_comment(self, record: Dict[str, Any]) -> Dict[str, Any]:
        """Normalise raw Mongo records into API response dictionaries."""
        created_at = record.get("createdAt") or record.get("created_at")
        if isinstance(created_at, datetime):
            created_iso = created_at.astimezone(timezone.utc).isoformat()
        elif isinstance(created_at, str):
            created_iso = created_at
        else:
            created_iso = None

        author_name = (
            record.get("author_name")
            or record.get("authorName")
            or record.get("author")
        )
        author_email = record.get("author_email") or record.get("authorEmail")
        author_id = record.get("author_id") or record.get("authorId")
        author_roles = record.get("author_roles") or record.get("authorRoles") or []
        if isinstance(author_roles, tuple):
            author_roles = list(author_roles)
        elif not isinstance(author_roles, list):
            author_roles = [author_roles] if author_roles else []

        return {
            "id": str(record.get("_id", "")),
            "text": record.get("text", ""),
            "author": record.get("author") or author_name or author_email or author_id,
            "authorName": author_name,
            "authorEmail": author_email,
            "authorId": author_id,
            "authorRoles": author_roles,
            "createdAt": created_iso,
        }

    async def link_documents(
        self,
        source_document_id: str,
        target_document_id: str,
        link_type: str,
        description: Optional[str] = None,
        current_user: Any = None,
    ) -> Dict[str, Any]:
        if source_document_id == target_document_id:
            raise DocumentServiceError("Cannot link a document to itself")

        updated = await self.add_reference(
            source_document_id,
            ReferenceCreate(
                referenced_document_id=target_document_id,
                link_type=link_type,
                description=description,
            ),
            current_user=current_user,
        )

        linked_documents = [ref.documentId for ref in updated.references or []]
        return {
            "message": "Documents linked successfully",
            "linked_documents": linked_documents,
        }

    async def get_document_revision(self, document_id: str) -> Optional[int]:
        db = await self._get_db()
        try:
            doc_oid = self._validate_document_id(document_id)
            raw = await db.documents.find_one({"_id": doc_oid}, {"_revision": 1})
        except InvalidDocumentIdError:
            raw = await db.documents.find_one({"_id": document_id}, {"_revision": 1})
        if not raw:
            return None
        try:
            return int(raw.get("_revision") or 1)
        except Exception:
            return 1

    async def update_document(
        self,
        document_id: str,
        updated_document: Document,
        *,
        expected_revision: Optional[int] = None,
    ) -> Optional[Document]:
        """
        Update an existing document.
        
        Args:
            document_id: Document ID string
            updated_document: Document with updated data
            
        Returns:
            Updated document if successful, None if document not found
            
        Raises:
            DocumentServiceError: If update fails
            InvalidDocumentIdError: If document ID is invalid
        """
        try:
            doc_oid = self._validate_document_id(document_id)
            
            if not isinstance(updated_document, Document):
                raise ValueError("updated_document must be a Document instance")
            
            logger.info(f"Updating document: {document_id}")
            
            # Convert to dict and exclude unset values
            update_dict = updated_document.model_dump(
                by_alias=True,
                exclude_unset=True,
                exclude={"_id"}  # Don't update the ID field via dump options
            )

            # Hardening: make absolutely sure immutable identifiers never reach Mongo
            update_dict.pop("_id", None)
            update_dict.pop("id", None)

            if "letterNo" in update_dict:
                letter_value = update_dict.get("letterNo")
                update_dict["letterNoNormalized"] = (
                    normalize_letter_code(str(letter_value)) if letter_value else None
                )
            
            if not update_dict:
                logger.warning(f"No fields to update for document: {document_id}")
                return await self.get_document(document_id)
            
            db = await self._get_db()
            update_dict["updatedAt"] = datetime.utcnow()
            query: Dict[str, Any] = {"_id": doc_oid, "lifecycle_state": {"$ne": "deleted"}}
            if expected_revision is not None:
                query["_revision"] = int(expected_revision)
            result = await db.documents.update_one(
                query,
                {"$set": update_dict, "$inc": {"_revision": 1}}
            )
            
            if result.matched_count == 0:
                if expected_revision is not None:
                    current_revision = await self.get_document_revision(document_id)
                    if current_revision is not None:
                        raise DocumentConflictError(
                            "Document was modified by another user",
                            current_revision=current_revision,
                        )
                logger.info(f"Document not found for update: {document_id}")
                return None
            
            if result.modified_count == 0:
                logger.info(f"Document not modified (no changes): {document_id}")
            else:
                logger.info(f"Updated document: {document_id}")
            
            # Return the updated document
            return await self.get_document(document_id)
            
        except InvalidDocumentIdError:
            raise
        except ValueError:
            raise
        except DocumentServiceError:
            raise
        except Exception as e:
            logger.error(f"Failed to update document {document_id}: {e}")
            raise DocumentServiceError(f"Document update failed: {str(e)}")

    async def update_summary_metadata(
        self,
        document_id: str,
        update_fields: Dict[str, Any],
        *,
        updated_by: Optional[str] = None,
    ) -> Optional[Document]:
        """Persist manually edited summary metadata without reprocessing content."""
        if not update_fields:
            return await self.get_document(document_id)

        try:
            candidates: list[Any] = [document_id]
            try:
                candidates.append(ObjectId(document_id))
            except Exception:
                pass

            now = datetime.utcnow()
            set_payload: Dict[str, Any] = {
                **update_fields,
                "updatedAt": now,
                "updated_at": now,
                "summary_metadata_updated_at": now,
                "manual_summary_metadata_override": True,
            }
            if updated_by:
                set_payload["updated_by"] = updated_by
                set_payload["summary_metadata_updated_by"] = updated_by

            db = await self._get_db()
            result = await db.documents.update_one(
                {"_id": {"$in": candidates}, "lifecycle_state": {"$ne": "deleted"}},
                {"$set": set_payload, "$inc": {"_revision": 1}},
            )
            if result.matched_count == 0:
                return None
            return await self.get_document(document_id)
        except Exception as e:
            logger.error("Failed to update summary metadata for document %s: %s", document_id, e)
            raise DocumentServiceError(f"Document summary metadata update failed: {str(e)}")
    
    async def delete_document(
        self,
        document_id: str,
        *,
        expected_revision: Optional[int] = None,
    ) -> bool:
        """
        Delete a document by ID.
        
        Args:
            document_id: Document ID string
            
        Returns:
            True if document was deleted, False if not found
            
        Raises:
            DocumentServiceError: If deletion fails
            InvalidDocumentIdError: If document ID is invalid
        """
        try:
            doc_oid = self._validate_document_id(document_id)
            
            logger.info(f"Deleting document: {document_id}")
            
            db = await self._get_db()
            query: Dict[str, Any] = {"_id": doc_oid, "lifecycle_state": {"$ne": "deleted"}}
            if expected_revision is not None:
                query["_revision"] = int(expected_revision)
            result = await db.documents.update_one(
                query,
                {
                    "$set": {
                        "lifecycle_state": "deleted",
                        "deletedAt": datetime.utcnow(),
                        "updatedAt": datetime.utcnow(),
                    },
                    "$inc": {"_revision": 1},
                },
            )
            
            success = result.matched_count > 0
            if not success and expected_revision is not None:
                current_revision = await self.get_document_revision(document_id)
                if current_revision is not None:
                    raise DocumentConflictError(
                        "Document was modified by another user",
                        current_revision=current_revision,
                    )
            if success:
                logger.info(f"Deleted document: {document_id}")
            else:
                logger.info(f"Document not found for deletion: {document_id}")
            
            return success
            
        except InvalidDocumentIdError:
            raise
        except Exception as e:
            logger.error(f"Failed to delete document {document_id}: {e}")
            raise DocumentServiceError(f"Document deletion failed: {str(e)}")
    
    async def document_exists(self, document_id: str) -> bool:
        """
        Check if a document exists.
        
        Args:
            document_id: Document ID string
            
        Returns:
            True if document exists, False otherwise
            
        Raises:
            DocumentServiceError: If check fails
            InvalidDocumentIdError: If document ID is invalid
        """
        try:
            doc_oid = self._validate_document_id(document_id)
            
            db = await self._get_db()
            count = await db.documents.count_documents({"_id": doc_oid}, limit=1)
            return count > 0
            
        except InvalidDocumentIdError:
            raise
        except Exception as e:
            logger.error(f"Failed to check document existence {document_id}: {e}")
            raise DocumentServiceError(f"Document existence check failed: {str(e)}")
    
    def _normalize_metadata_references(self, references: Any) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        if not references:
            return normalized

        seen: Set[Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]] = set()
        for ref in references:
            entry = self._coerce_reference_entry(ref)
            if not entry:
                continue

            date_value = entry.get("date")
            if date_value:
                try:
                    formatted = format_date_ddmmyyyy(date_value)
                    if formatted:
                        entry = dict(entry)
                        entry["date"] = formatted
                except Exception:
                    pass

            key = (
                entry.get("letterNo"),
                entry.get("date"),
                entry.get("text"),
                entry.get("raw"),
            )
            if key in seen:
                continue
            seen.add(key)

            has_structured_fields = any(
                entry.get(field) for field in ("letterNo", "letter_no", "date", "text")
            )
            if "raw" in entry and not has_structured_fields:
                entry = dict(entry)
                entry.pop("raw", None)
                if not entry:
                    continue

            normalized.append(entry)

        return normalized
    async def get_documents_count(self, filter_dict: Optional[Dict[str, Any]] = None) -> int:
        """
        Get count of documents matching filter.
        
        Args:
            filter_dict: Optional filter dictionary
            
        Returns:
            Number of matching documents
            
        Raises:
            DocumentServiceError: If count fails
        """
        try:
            filter_dict = filter_dict or {}
            db = await self._get_db()
            count = await db.documents.count_documents(filter_dict)
            logger.debug(f"Document count: {count}")
            return count
            
        except Exception as e:
            logger.error(f"Failed to count documents: {e}")
            raise DocumentServiceError(f"Document count failed: {str(e)}")
    
    async def search_documents(self, query: Dict[str, Any], skip: int = 0, limit: int = 50) -> List[Optional[Document]]:
        """
        Search documents with a query.
        
        Args:
            query: MongoDB query dictionary
            skip: Number of documents to skip
            limit: Maximum number of documents to return
            
        Returns:
            List of matching Document instances
            
        Raises:
            DocumentServiceError: If search fails
        """
        try:
            skip, limit = validate_pagination(skip, limit)
            
            if not isinstance(query, dict):
                raise ValueError("Query must be a dictionary")
            
            logger.info(f"Searching documents with query: skip={skip}, limit={limit}")
            
            db = await self._get_db()
            raw_documents, _ = await fetch_paginated(
                db.documents,
                filter=query,
                skip=skip,
                limit=limit,
            )
            documents: List[Optional[Document]] = []

            for doc_data in raw_documents:
                try:
                    document = Document(**doc_data) if doc_data else None
                    documents.append(document)
                except Exception as e:
                    logger.warning(
                        "Failed to parse document %s: %s",
                        doc_data.get("_id", "unknown") if isinstance(doc_data, dict) else "unknown",
                        e,
                    )
                    documents.append(None)
            
            logger.info(f"Found {len(documents)} documents")
            return documents
            
        except ValueError:
            raise
        except Exception as e:
            logger.error(f"Failed to search documents: {e}")
            raise DocumentServiceError(f"Document search failed: {str(e)}")

# Factory function
def create_document_service(db: Database) -> DocumentService:
    """Create document service instance"""
    return DocumentService(db)
