# services/database_service.py

import hashlib
import logging
import os
from datetime import datetime
from typing import Optional, List, Dict, Any, Set, Tuple

from motor.motor_asyncio import AsyncIOMotorClient
from bson.objectid import ObjectId

from ..config.document_processing_config import DocumentProcessingConfig
try:
    from .llamaindex_service import LlamaIndexVectorService
    _LLAMA_INDEX_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    _LLAMA_INDEX_AVAILABLE = False

    class LlamaIndexVectorService:  # type: ignore
        def __init__(self, *args, **kwargs):
            raise ImportError("llama_index is not installed; enable it or switch to LangChain vector service.")
from .langchain_vector_service import LangChainVectorService
from ..utils.pipeline_logging import configure_pipeline_logger
from ..ingestion.chunk_ids import deterministic_chunk_id


class DocumentProcessingError(Exception):
    """Custom exception for document processing errors"""
    pass


logger = logging.getLogger(__name__)
configure_pipeline_logger(logger)
sync_logger = logging.getLogger("storage.sync")
configure_pipeline_logger(sync_logger)


class DatabaseService:
    """Service for database operations using async Motor client"""

    def __init__(self, config: DocumentProcessingConfig):
        self.config = config
        self._client: Optional[AsyncIOMotorClient] = None
        self._db = None
        self._vector_service: Optional[LlamaIndexVectorService] = None
        self._langchain_vector_service: Optional[LangChainVectorService] = None
        self._langchain_service_initialized: bool = False
        self.partial_failures: Dict[str, Any] = {}

    async def get_database(self):
        """Get database connection with proper error handling"""
        if self._db is None:
            try:
                self._client = AsyncIOMotorClient(self.config.mongo_uri)
                self._db = self._client[self.config.database_name]

                # Test connection
                await self._db.command("ping")
                logger.info("Connected to MongoDB database: %s", self.config.database_name)

            except Exception as exc:
                raise DocumentProcessingError(f"Failed to connect to database: {exc}") from exc

        return self._db

    async def close_connection(self):
        """Close database connection"""
        if self._client:
            self._client.close()
            self._client = None
            self._db = None

        if self._vector_service:
            try:
                await self._vector_service.close()
            except Exception as exc:
                logger.debug("Vector service close raised %s", exc)
            finally:
                self._vector_service = None
    def _normalize_metadata_references(self, references: Any) -> List[Dict[str, Any]]:
        """Normalize metadata references into storage-friendly structures."""
        normalized: List[Dict[str, Any]] = []
        if not references:
            return normalized

        seen: Set[Tuple[Optional[str], Optional[str], Optional[str]]] = set()
        for ref in references:
            if hasattr(ref, "model_dump"):
                try:
                    data = ref.model_dump(by_alias=True, exclude_none=True)  # type: ignore[attr-defined]
                except TypeError:
                    data = dict(ref)  # type: ignore[arg-type]
            elif isinstance(ref, dict):
                data = {k: v for k, v in ref.items() if v not in (None, "", [], {})}
            elif isinstance(ref, str):
                data = {"text": ref}
            elif ref is None:
                continue
            else:
                data = {"text": str(ref)}

            def _clean(value: Any) -> Optional[str]:
                if value is None:
                    return None
                if isinstance(value, str):
                    value = value.strip()
                else:
                    value = str(value).strip()
                return value or None

            letter_no = _clean(data.get("letterNo") or data.get("letter_no"))
            text_value = _clean(data.get("text"))
            date_value = _clean(data.get("date"))

            entry: Dict[str, Any] = {}
            primary = letter_no or text_value
            if primary:
                entry["letterNo"] = primary

            if date_value:
                try:
                    from ..utils.date_parser import format_date_ddmmyyyy
                    formatted = format_date_ddmmyyyy(date_value)
                    if formatted:
                        date_value = formatted
                except Exception:
                    pass
                entry["date"] = date_value

            if text_value and primary and text_value != primary:
                entry["text"] = text_value

            key = (
                entry.get("letterNo"),
                entry.get("date"),
                entry.get("text"),
            )
            if not entry or key in seen:
                continue
            seen.add(key)
            normalized.append(entry)

        return normalized
    
    async def save_document_data(
        self,
        document_id: Optional[str],
        file_path: str,
        parsed_metadata: Any,  # Assuming ParsedDocumentMetadata as dict or dataclass
        full_text: str,
        embedding_text: str,
    ) -> int:
        """
        Save document data to database and create embeddings.

        Returns:
            Number of embedding chunks created
        """
        try:
            logger.info(
                "[document_pipeline] Writing OCR and metadata to database (document_id=%s, file_path=%s)",
                document_id,
                file_path,
            )
            db = await self.get_database()

            # Save document metadata
            doc = await self._upsert_document_metadata(
                db, document_id, file_path, parsed_metadata, full_text
            )

            # Create and store embeddings. Indexing failures must not hide a
            # successful OCR/metadata extraction; record them as partial failures.
            try:
                chunks_created = await self._create_and_store_embeddings(db, doc, embedding_text)
            except Exception as exc:
                chunks_created = 0
                self.partial_failures["embeddings"] = {
                    "stage": "embeddings",
                    "message": str(exc),
                    "timestamp": datetime.utcnow(),
                }
                logger.warning(
                    "Embedding/vector indexing failed for document %s after metadata save: %s",
                    document_id or doc.get("_id"),
                    exc,
                )

            logger.info(
                "[document_pipeline] Saved document data and created %s embedding chunks",
                chunks_created,
            )
            return chunks_created

        except Exception as exc:
            logger.error("Failed to save document data: %s", exc)
            raise DocumentProcessingError(f"Database save failed: {exc}") from exc

    async def _upsert_document_metadata(
        self,
        db,
        document_id: Optional[str],
        file_path: str,
        parsed_metadata: Any,
        full_text: str,
    ) -> Dict[str, Any]:
        """Upsert document metadata to documents collection"""
        try:
            # Try to find existing document
            doc = await self._find_existing_document(db, document_id, file_path)

            # Prepare update data
            updates = {
                "ocrText": full_text,
                "updatedAt": datetime.utcnow(),
            }

            # Add parsed metadata fields (assuming dataclass or dict)
            if hasattr(parsed_metadata, "subject") and parsed_metadata.subject:
                updates["subject"] = parsed_metadata.subject
            if hasattr(parsed_metadata, "letter_no") and parsed_metadata.letter_no:
                updates["letterNo"] = parsed_metadata.letter_no
            if hasattr(parsed_metadata, "from_company") and parsed_metadata.from_company:
                updates["from"] = parsed_metadata.from_company
            if hasattr(parsed_metadata, "to_company") and parsed_metadata.to_company:
                updates["to"] = parsed_metadata.to_company
            if hasattr(parsed_metadata, "summary") and parsed_metadata.summary:
                updates["summary"] = parsed_metadata.summary
            if hasattr(parsed_metadata, "references") and parsed_metadata.references:
                normalized_refs = self._normalize_metadata_references(parsed_metadata.references)
                if normalized_refs:
                    updates["reference"] = normalized_refs
            if hasattr(parsed_metadata, "full_content") and parsed_metadata.full_content:
                updates["full_text"] = parsed_metadata.full_content
            if hasattr(parsed_metadata, "keywords") and parsed_metadata.keywords:
                updates["keywords"] = parsed_metadata.keywords
            if hasattr(parsed_metadata, "contractual_clauses") and parsed_metadata.contractual_clauses:
                updates["contractual_clauses"] = parsed_metadata.contractual_clauses

            # Parse date if available
            if hasattr(parsed_metadata, "date") and parsed_metadata.date:
                try:
                    from .text_processing_service import TextProcessingService

                    text_service = TextProcessingService(self.config)
                    parsed_date = text_service.parse_date_safe(parsed_metadata.date)
                    if parsed_date:
                        updates["date"] = parsed_date
                except Exception as exc:
                    logger.warning("Date parsing failed: %s", exc)

            if doc:
                await db.documents.update_one({"_id": doc["_id"]}, {"$set": updates})
                doc.update(updates)
            else:
                # Create new document record
                new_doc = {
                    "filename": os.path.basename(file_path),
                    "filepath_local": file_path,
                    "createdAt": datetime.utcnow(),
                    "updatedAt": datetime.utcnow(),
                    **updates,
                }
                result = await db.documents.insert_one(new_doc)
                new_doc["_id"] = result.inserted_id
                doc = new_doc

            return doc

        except Exception as exc:
            logger.error("Failed to upsert document metadata: %s", exc)
            raise

    async def _find_existing_document(
        self,
        db,
        document_id: Optional[str],
        file_path: str,
    ) -> Optional[Dict[str, Any]]:
        """Find existing document by ID or file path"""
        doc = None

        # Try to find by document ID first
        if document_id:
            try:
                doc = await db.documents.find_one({"_id": ObjectId(document_id)})
            except Exception:
                logger.warning("Invalid document ID format: %s", document_id)

        # Fallback to file path matching
        if not doc:
            filename = os.path.basename(file_path)
            doc = await db.documents.find_one({"filename": filename})

        return doc

    async def _create_and_store_embeddings(
        self,
        db,
        doc: Dict[str, Any],
        text: str,
    ) -> int:
        """Create embeddings via LlamaIndex and store bookkeeping records."""
        document_id = str(doc.get("_id"))

        if not getattr(self.config, "vector_store_enabled", True):
            logger.info("Vector store disabled; skipping embedding creation")
            await self._update_vector_sync_status(
                db,
                document_id,
                status="disabled",
                mongo_chunks=0,
                qdrant_chunks=None,
                details="Vector store disabled via configuration toggle",
            )
            return 0

        try:
            from .text_processing_service import TextProcessingService

            text_service = TextProcessingService(self.config)
            chunks = text_service.chunk_text(text)
            if not chunks:
                logger.warning("No text chunks to embed")
                await self._update_vector_sync_status(
                    db,
                    document_id,
                    status="empty",
                    mongo_chunks=0,
                    qdrant_chunks=None,
                    details="Text chunker produced no segments",
                )
                return 0

            vector_service = self._get_vector_service()

            organization_id = doc.get("organization_id")
            project_id = doc.get("project_id")

            def _maybe_str(value: Any) -> Optional[str]:
                if value is None:
                    return None
                return str(value)

            payloads: List[Dict[str, Any]] = []
            for index, chunk_text in enumerate(chunks):
                checksum = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
                chunk_id = deterministic_chunk_id(document_id, index, text=chunk_text[:50])
                metadata = {
                    "document_id": document_id,
                    "organization_id": _maybe_str(organization_id) or "",
                    "project_id": _maybe_str(project_id) or "",
                    "uploadType": str(doc.get("uploadType", "incoming")),
                    "letterNo": doc.get("letterNo"),
                    "filepath_local": doc.get("filepath_local"),
                    "filepath_s3": doc.get("filepath_s3"),
                    "chunk_index": index,
                    "source": "document_processing",
                    "chunk_id": chunk_id,
                    "embedding_model": self.config.openai_embedding_model,
                    "embedding_provider": "openai",
                    "embedding_version": getattr(self.config, "embedding_version", "v1"),
                    "chunking_version": getattr(self.config, "chunking_version", "v1"),
                }
                metadata["checksum_sha256"] = checksum
                payloads.append(
                    {
                        "text": chunk_text,
                        "metadata": metadata,
                        "checksum": checksum,
                        "chunk_id": chunk_id,
                    }
                )

            langchain_service = self._get_langchain_vector_service()
            expected_chunks = len(payloads)

            await self._update_vector_sync_status(
                db,
                document_id,
                status="pending",
                mongo_chunks=expected_chunks,
                qdrant_chunks=None,
            )
            sync_logger.info(
                "vector.sync.pending document_id=%s expected_chunks=%s",
                document_id,
                expected_chunks,
            )

            qdrant_chunks: Optional[int] = None
            qdrant_enabled = bool(
                langchain_service
                and langchain_service.enabled
                and getattr(self.config, "vector_dual_write_enabled", getattr(self.config, "dual_vector_write", True))
            )

            if qdrant_enabled:
                qdrant_chunks = await langchain_service.replace_document(payloads)
                if qdrant_chunks is None:
                    qdrant_chunks = 0
                sync_logger.info(
                    "vector.sync.qdrant document_id=%s chunks=%s",
                    document_id,
                    qdrant_chunks,
                )
                await self._update_vector_sync_status(
                    db,
                    document_id,
                    status="qdrant_synced",
                    mongo_chunks=expected_chunks,
                    qdrant_chunks=qdrant_chunks,
                )
            else:
                sync_logger.debug(
                    "vector.sync.skip_qdrant document_id=%s enabled=%s service_ready=%s",
                    document_id,
                    getattr(self.config, "vector_dual_write_enabled", getattr(self.config, "dual_vector_write", True)),
                    bool(langchain_service and langchain_service.enabled),
                )

            filter_query = {"document_id": document_id}
            existing_refs = await db.document_vectors.find(filter_query, {"vector_ref": 1, "_id": 0}).to_list(length=None)
            vector_refs = [item.get("vector_ref") for item in existing_refs if item.get("vector_ref")]
            if vector_refs:
                await vector_service.delete_vectors(vector_refs)

            delete_result = await db.document_vectors.delete_many(filter_query)
            if delete_result.deleted_count:
                logger.info("Removed %s existing vector chunks", delete_result.deleted_count)

            vector_results = await vector_service.index_chunks(payloads)

            now = datetime.utcnow()
            vector_docs = []
            for record in vector_results:
                metadata = dict(record["metadata"])
                vector_doc = {
                    **metadata,
                    "vector_ref": record["vector_ref"],
                    "embedding_id": metadata.get("embedding_id"),
                    "embedding_model": vector_service.embedding_model_name,
                    "embedding_dims": len(record["embedding"]),
                    "text": record["text"],
                    "num_tokens": len(record["text"].split()),
                    "checksum_sha256": metadata.get("checksum_sha256"),
                    "createdAt": now,
                }
                vector_docs.append(vector_doc)

            mongo_chunks = len(vector_docs)
            if vector_docs:
                await db.document_vectors.insert_many(vector_docs)
                logger.info("Stored %s vector chunks", mongo_chunks)

            final_status = "empty"
            details: Optional[str] = None
            if mongo_chunks == 0:
                final_status = "empty"
            elif not qdrant_enabled:
                final_status = "mongo_only"
            elif qdrant_chunks == mongo_chunks:
                final_status = "synced"
            else:
                final_status = "mismatch"
                details = f"Mongo chunks={mongo_chunks}, Qdrant chunks={qdrant_chunks}"
                if getattr(self.config, "vector_verify_after_write", False):
                    logger.warning(
                        "Vector chunk mismatch for %s: mongo=%s qdrant=%s",
                        document_id,
                        mongo_chunks,
                        qdrant_chunks,
                    )
                    sync_logger.warning(
                        "vector.sync.mismatch document_id=%s mongo=%s qdrant=%s",
                        document_id,
                        mongo_chunks,
                        qdrant_chunks,
                    )

            await self._update_vector_sync_status(
                db,
                document_id,
                status=final_status,
                mongo_chunks=mongo_chunks,
                qdrant_chunks=qdrant_chunks if qdrant_enabled else None,
                details=details,
            )
            sync_logger.info(
                "vector.sync.final document_id=%s status=%s mongo=%s qdrant=%s",
                document_id,
                final_status,
                mongo_chunks,
                qdrant_chunks if qdrant_enabled else "n/a",
            )

            return mongo_chunks

        except DocumentProcessingError:
            await self._update_vector_sync_status(
                db,
                document_id,
                status="error",
                details="DocumentProcessingError raised during embedding pipeline",
            )
            raise
        except Exception as exc:
            logger.error("Failed to create and store embeddings: %s", exc)
            await self._update_vector_sync_status(
                db,
                document_id,
                status="error",
                details=str(exc),
            )
            raise DocumentProcessingError(f"Embedding storage failed: {exc}") from exc

    def _get_langchain_vector_service(self) -> Optional[LangChainVectorService]:
        if not self._langchain_service_initialized:
            self._langchain_service_initialized = True
            try:
                service = LangChainVectorService(self.config)
                if service.enabled:
                    self._langchain_vector_service = service
                else:
                    self._langchain_vector_service = None
            except Exception as exc:
                logger.debug("LangChain vector service initialization failed: %s", exc)
                self._langchain_vector_service = None
        return self._langchain_vector_service

    async def _update_vector_sync_status(
        self,
        db,
        document_id: str,
        *,
        status: str,
        mongo_chunks: Optional[int] = None,
        qdrant_chunks: Optional[int] = None,
        details: Optional[str] = None,
    ) -> None:
        """Persist vector sync bookkeeping without raising pipeline errors."""
        payload: Dict[str, Any] = {
            "document_id": document_id,
            "sync_status": status,
            "updatedAt": datetime.utcnow(),
        }
        if mongo_chunks is not None:
            payload["mongo_chunks"] = int(mongo_chunks)
        if qdrant_chunks is not None:
            payload["qdrant_chunks"] = int(qdrant_chunks)
        if details:
            payload["details"] = details

        try:
            await db.vector_sync_status.update_one(
                {"document_id": document_id},
                {
                    "$set": payload,
                    "$setOnInsert": {"createdAt": datetime.utcnow()},
                },
                upsert=True,
            )
        except Exception as exc:  # pragma: no cover - bookkeeping should not fail pipeline
            sync_logger.debug(
                "vector.sync.status_update_failed document_id=%s error=%s",
                document_id,
                exc,
            )

    def _get_vector_service(self) -> LlamaIndexVectorService:
        if self._vector_service is None:
            if not _LLAMA_INDEX_AVAILABLE:
                raise DocumentProcessingError(
                    "llama_index is not installed; install it or configure LangChain vectors instead."
                )
            api_key = self.config.openai_api_key or self._get_openai_api_key()
            if not api_key:
                raise DocumentProcessingError("OpenAI API key not configured for embeddings")
            # Share the Motor client's underlying pymongo.MongoClient to avoid duplicate connections
            existing_client = None
            if self._client is not None:
                existing_client = getattr(self._client, "delegate", None)
            self._vector_service = LlamaIndexVectorService(
                mongo_uri=self.config.mongo_uri,
                database_name=self.config.database_name,
                collection_name=getattr(self.config, "vector_store_collection", "document_vectors"),
                embedding_model=self.config.openai_embedding_model,
                openai_api_key=api_key,
                existing_mongo_client=existing_client,
            )
        return self._vector_service

    def _get_openai_api_key(self) -> Optional[str]:
        """Get OpenAI API key from settings or environment"""
        try:
            from ..core.config import settings

            api_key = getattr(settings, "OPENAI_API_KEY", None)
            if api_key:
                return api_key
        except Exception:
            pass

        return os.getenv("OPENAI_API_KEY")

