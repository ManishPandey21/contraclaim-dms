# services/bulk_upload_service.py

"""
Bulk upload service for handling multiple document uploads with metadata processing.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from pathlib import Path
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi import UploadFile, BackgroundTasks  # needed at runtime (used in annotations and instantiation)

from ..core.security import CurrentUser  # for type hints
from ..models.document import (
    BulkUploadStatus, DocumentProcessingResult, DocumentProcessingTask
)
from ..core.database import get_database
from ..core.config import settings
from .metadata_processor_service import MetadataProcessorService
from .document_service import DocumentService

# Add imports for CSV encoding handling
import pandas as pd
try:
    import chardet
    _CHARDET_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    _CHARDET_AVAILABLE = False
import io
from tempfile import NamedTemporaryFile
import csv
import re

logger = logging.getLogger(__name__)

class BulkUploadService:
    """Service for managing bulk document uploads and processing."""

    def __init__(self):
        # Lazy initialization to avoid import-time errors
        self.db = None  # Will be initialized when needed
        self.metadata_service = MetadataProcessorService()
        self.document_service: Optional[DocumentService] = None
        self._active_jobs: Dict[str, BulkUploadStatus] = {}
         # Encoding fallback list for CSV processing
        self.encoding_fallbacks = ['utf-8', 'latin1', 'cp1252', 'iso-8859-1', 'windows-1252']

    async def _get_db(self):
        """Get database connection."""
        if self.db is None:
            self.db = await get_database()
        return self.db

    async def _get_document_service(self) -> DocumentService:
        """
        Lazily construct DocumentService once a DB connection is available.
        """
        if self.document_service is None:
            db = await self._get_db()
            self.document_service = DocumentService(db)
        return self.document_service

    def detect_csv_encoding(self, file_content: bytes, user_provided_encoding: Optional[str] = None) -> str:
        """Detect CSV file encoding using chardet, with user-provided override"""

        if user_provided_encoding:
            logger.info(f"Using user-provided encoding: {user_provided_encoding}")
            return user_provided_encoding

        if not _CHARDET_AVAILABLE:
            logger.info("chardet not installed; defaulting to utf-8 for CSV detection")
            return "utf-8"

        try:
            result = chardet.detect(file_content)
            detected_encoding = result.get('encoding', 'utf-8')
            confidence = result.get('confidence', 0)

            # Log detection results
            logger.info(f"Encoding detection: {detected_encoding} (confidence: {confidence})")

            # If confidence is low, prefer common encodings
            if confidence < 0.7:
                logger.warning(f"Low confidence ({confidence}) for encoding {detected_encoding}, using latin1 fallback")
                return 'latin1'  # Safe fallback that can decode any byte

            return detected_encoding
        except Exception as e:
            logger.warning(f"Encoding detection failed: {str(e)}, using utf-8 default")
            return 'utf-8'

    def read_csv_with_fallback(self, file_path: str) -> pd.DataFrame:
        """Read CSV with encoding detection and fallback"""

        # First, try to detect encoding
        try:
            with open(file_path, 'rb') as f:
                raw_data = f.read(10000)  # Read first 10KB for detection
                detected_encoding = self.detect_csv_encoding(raw_data)
        except Exception as e:
            logger.warning(f"Failed to detect encoding for {file_path}: {str(e)}")
            detected_encoding = 'utf-8'

        # Try encodings in order of preference
        encodings_to_try = [detected_encoding] + [enc for enc in self.encoding_fallbacks if enc != detected_encoding]

        last_error = None
        for encoding in encodings_to_try:
            try:
                logger.debug(f"Trying to read CSV with encoding: {encoding}")
                df = pd.read_csv(file_path, encoding=encoding, encoding_errors='replace')

                # Validate that we got reasonable data
                if not df.empty and len(df.columns) > 0:
                    logger.info(f"Successfully read CSV with encoding: {encoding}")
                    return self.clean_dataframe(df)

            except (UnicodeDecodeError, UnicodeError, Exception) as e:
                last_error = e
                logger.debug(f"Failed to read with {encoding}: {str(e)}")
                continue

        # If all encodings fail, raise a descriptive error
        raise ValueError(f"CSV processing failed: Unable to decode file with any supported encoding. Last error: {str(last_error)}")

    def read_csv_from_upload_file(self, upload_file: UploadFile, user_provided_encoding: Optional[str] = None) -> pd.DataFrame:
        """Read CSV from FastAPI UploadFile with encoding detection"""

        try:
            # Read file content as bytes
            file_content = upload_file.file.read()
            upload_file.file.seek(0)  # Reset file position

            # Detect encoding
            detected_encoding = self.detect_csv_encoding(file_content, user_provided_encoding)

            # Try encodings in order of preference
            encodings_to_try = [detected_encoding] + [enc for enc in self.encoding_fallbacks if enc != detected_encoding]

            last_error = None
            for encoding in encodings_to_try:
                try:
                    logger.debug(f"Trying to read uploaded CSV with encoding: {encoding}")

                    # Convert bytes to StringIO with detected encoding
                    text_content = file_content.decode(encoding, errors='replace')
                    csv_buffer = io.StringIO(text_content)

                    # Read CSV from buffer
                    df = pd.read_csv(csv_buffer)
                    # Fallback to semicolon-delimited if comma parsing collapsed columns
                    if df.shape[1] == 1 and ';' in text_content:
                        csv_buffer = io.StringIO(text_content)
                        df = pd.read_csv(csv_buffer, sep=';')

                    # Validate that we got reasonable data
                    if not df.empty and len(df.columns) > 0:
                        logger.info(f"Successfully read uploaded CSV with encoding: {encoding}")
                        cleaned = self.clean_dataframe(df)
                        normalized = self.normalize_csv_dataframe(cleaned)
                        return normalized

                except (UnicodeDecodeError, UnicodeError, Exception) as e:
                    last_error = e
                    logger.debug(f"Failed to read uploaded CSV with {encoding}: {str(e)}")
                    continue

            # If all encodings fail, raise descriptive error
            raise ValueError(f"CSV processing failed: Unable to decode uploaded file with any supported encoding. Please save your CSV as UTF-8 format or contact support.")

        except Exception as e:
            logger.error(f"Failed to process uploaded CSV file: {str(e)}")
            if 'utf-8' in str(e) and 'decode' in str(e):
                raise ValueError("CSV processing failed: File encoding error. Please save your CSV as 'CSV UTF-8' format in Excel.")
            else:
                raise ValueError(f"CSV processing failed: {str(e)}")
        finally:
            # Ensure file position is reset
            upload_file.file.seek(0)

    def clean_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """Clean the dataframe of problematic characters"""

        # Replace common problematic characters
        for col in df.select_dtypes(include=['object']).columns:
            if df[col].dtype == 'object':
                df[col] = df[col].astype(str).str.replace('\xa0', ' ', regex=False)  # Replace NBSP with regular space
                df[col] = df[col].str.replace('\ufffd', '', regex=False)  # Remove replacement characters
                df[col] = df[col].str.replace('\x96', '-', regex=False)  # Replace en-dash
                df[col] = df[col].str.replace('\x97', '--', regex=False)  # Replace em-dash
                df[col] = df[col].str.strip()  # Remove leading/trailing whitespace

        return df

    def normalize_csv_dataframe(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Normalize CSV headers to the canonical field names used by single-upload.
        - Strips BOM/NBSP, whitespace, and punctuation
        - Maps common aliases (upload_type -> upload_type, letterNo -> letter_no, subTags -> sub_tags, etc.)
        """
        alias_map = {
            "uploadtype": "upload_type",
            "upload_type": "upload_type",
            "letterno": "letter_no",
            "letter_no": "letter_no",
            "from": "from_",
            "from_": "from_",
            "to": "to",
            "tags": "tags",
            "subtags": "sub_tags",
            "sub_tags": "sub_tags",
            "subject": "subject",
            "date": "date",
            "status": "status",
            "ocr": "ocr_enabled",
            "ocr_enabled": "ocr_enabled",
            "ocrenabled": "ocr_enabled",
            "compressionenabled": "compression_enabled",
            "compression_enabled": "compression_enabled",
            "pathstructure": "path_structure",
            "path_structure": "path_structure",
            "pathstructure1": "path_structure1",
            "path_structure1": "path_structure1",
            "filename": "filename",
        }

        def _normalize_header(value: Any) -> str:
            text = str(value or "").replace("\ufeff", "").replace("\u00A0", " ").strip()
            text = re.sub(r"[\s\-\.]+", "", text)  # remove whitespace/dots/hyphens
            return text.lower()

        normalized_columns = []
        for col in df.columns:
            key = _normalize_header(col)
            normalized_columns.append(alias_map.get(key, key))

        df.columns = normalized_columns
        return df

    async def persist_upload_files(self, files: List[UploadFile]) -> List[Dict[str, str]]:
        """
        Persist UploadFile streams to temporary files so they remain readable after the request ends.
        Returns list of dicts with filename and temp_path.
        """
        stored_files: List[Dict[str, str]] = []

        for upload in files:
            if not upload or not upload.filename:
                continue

            temp_file = None
            try:
                await upload.seek(0)
                temp_file = NamedTemporaryFile(
                    delete=False,
                    suffix=Path(upload.filename).suffix,
                    prefix="bulk_upload_",
                )

                while True:
                    chunk = await upload.read(1024 * 1024)  # 1MB chunks
                    if not chunk:
                        break
                    temp_file.write(chunk)

                temp_file.flush()
                stored_files.append(
                    {"filename": upload.filename, "temp_path": temp_file.name}
                )
            except Exception as e:
                logger.error(f"Failed to persist file {getattr(upload, 'filename', 'unknown')}: {e}")
                if temp_file and temp_file.name and Path(temp_file.name).exists():
                    try:
                        Path(temp_file.name).unlink()
                    except Exception:
                        pass
                raise
            finally:
                if temp_file:
                    try:
                        temp_file.close()
                    except Exception:
                        pass

        return stored_files

    async def create_bulk_job(self, job_status: BulkUploadStatus) -> str:
        """Create a new bulk upload job."""
        try:
            db = await self._get_db()

            # Store in database
            job_dict = job_status.model_dump(by_alias=True)
            await db.bulk_upload_jobs.insert_one(job_dict)

            # Keep in memory for quick access
            self._active_jobs[job_status.job_id] = job_status

            logger.info(f"Created bulk upload job: {job_status.job_id}")
            return job_status.job_id

        except Exception as e:
            logger.error(f"Failed to create bulk job: {str(e)}")
            raise

    async def update_progress(
        self,
        job_id: str,
        processed_files: int,
        successful_uploads: int,
        failed_uploads: int,
        results: List[DocumentProcessingResult]
    ):
        """Update job progress."""
        try:
            db = await self._get_db()

            # Calculate processing rate
            job_status = await self.get_job_status(job_id)
            if job_status:
                elapsed_time = (datetime.utcnow() - job_status.created_at).total_seconds() / 60  # minutes
                processing_rate = processed_files / elapsed_time if elapsed_time > 0 else 0

                # Estimate completion time
                remaining_files = job_status.total_files - processed_files
                estimated_completion = None
                if processing_rate > 0 and remaining_files > 0:
                    estimated_minutes = remaining_files / processing_rate
                    estimated_completion = datetime.utcnow() + timedelta(minutes=estimated_minutes)

            # Update database
            update_data = {
                "processed_files": processed_files,
                "successful_uploads": successful_uploads,
                "failed_uploads": failed_uploads,
                "updated_at": datetime.utcnow(),
                "results": [result.model_dump(by_alias=True) for result in results],
                "processing_rate": processing_rate if 'processing_rate' in locals() else None,
                "estimated_completion": estimated_completion
            }

            await db.bulk_upload_jobs.update_one(
                {"job_id": job_id},
                {"$set": update_data}
            )

            # Update memory cache
            if job_id in self._active_jobs:
                job_status = self._active_jobs[job_id]
                job_status.processed_files = processed_files
                job_status.successful_uploads = successful_uploads
                job_status.failed_uploads = failed_uploads
                job_status.updated_at = datetime.utcnow()
                job_status.results = results
                if 'processing_rate' in locals():
                    job_status.processing_rate = processing_rate
                if 'estimated_completion' in locals():
                    job_status.estimated_completion = estimated_completion

            logger.debug(f"Updated job {job_id}: {processed_files}/{job_status.total_files if job_status else 'unknown'} processed")

        except Exception as e:
            logger.error(f"Failed to update progress for job {job_id}: {str(e)}")

    async def complete_job(
        self,
        job_id: str,
        status: str,
        successful_uploads: int,
        failed_uploads: int,
        results: List[DocumentProcessingResult]
    ):
        """Mark job as completed."""
        try:
            db = await self._get_db()

            update_data = {
                "status": status,
                "completed_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
                "successful_uploads": successful_uploads,
                "failed_uploads": failed_uploads,
                "results": [result.model_dump(by_alias=True) for result in results]
            }

            await db.bulk_upload_jobs.update_one(
                {"job_id": job_id},
                {"$set": update_data}
            )

            # Update memory cache
            if job_id in self._active_jobs:
                job_status = self._active_jobs[job_id]
                job_status.status = status
                job_status.completed_at = datetime.utcnow()
                job_status.successful_uploads = successful_uploads
                job_status.failed_uploads = failed_uploads
                job_status.results = results

            logger.info(f"Completed job {job_id} with status: {status}")

        except Exception as e:
            logger.error(f"Failed to complete job {job_id}: {str(e)}")

    async def fail_job(self, job_id: str, error_message: str):
        """Mark job as failed."""
        try:
            db = await self._get_db()

            update_data = {
                "status": "failed",
                "completed_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
                "error_message": error_message
            }

            await db.bulk_upload_jobs.update_one(
                {"job_id": job_id},
                {"$set": update_data}
            )

            # Update memory cache
            if job_id in self._active_jobs:
                job_status = self._active_jobs[job_id]
                job_status.status = "failed"
                job_status.completed_at = datetime.utcnow()
                job_status.error_message = error_message

            logger.error(f"Failed job {job_id}: {error_message}")

        except Exception as e:
            logger.error(f"Failed to update job failure for {job_id}: {str(e)}")

    async def get_job_status(self, job_id: str) -> Optional[BulkUploadStatus]:
        """Get job status."""
        try:
            # Check memory cache first
            if job_id in self._active_jobs:
                return self._active_jobs[job_id]

            # Check database
            db = await self._get_db()
            job_data = await db.bulk_upload_jobs.find_one({"job_id": job_id})

            if job_data:
                # Convert results back to objects
                results = [
                    DocumentProcessingResult(**result)
                    for result in job_data.get("results", [])
                ]
                job_data["results"] = results

                job_status = BulkUploadStatus(**job_data)

                # Cache active jobs
                if job_status.status in ["processing"]:
                    self._active_jobs[job_id] = job_status

                return job_status

            return None

        except Exception as e:
            logger.error(f"Failed to get job status for {job_id}: {str(e)}")
            return None

    async def cleanup_completed_jobs(self, older_than_hours: int = 24):
        """Clean up completed jobs older than specified hours."""
        try:
            db = await self._get_db()

            cutoff_time = datetime.utcnow() - timedelta(hours=older_than_hours)

            # Remove from database
            result = await db.bulk_upload_jobs.delete_many({
                "status": {"$in": ["completed", "completed_with_errors", "failed", "cancelled"]},
                "completed_at": {"$lt": cutoff_time}
            })

            # Remove from memory cache
            completed_jobs = [
                job_id for job_id, job_status in self._active_jobs.items()
                if job_status.status in ["completed", "completed_with_errors", "failed", "cancelled"]
                and job_status.completed_at
                and job_status.completed_at < cutoff_time
            ]

            for job_id in completed_jobs:
                del self._active_jobs[job_id]

            logger.info(f"Cleaned up {result.deleted_count} completed jobs older than {older_than_hours} hours")

        except Exception as e:
            logger.error(f"Failed to cleanup completed jobs: {str(e)}")

    async def process_documents_with_metadata(
        self,
        job_id: str,
        documents_data: List[Dict[str, Any]],
        organization_id: str,
        project_id: str
    ) -> List[DocumentProcessingResult]:
        """Process documents with metadata extraction in parallel."""
        try:
            results = []

            # Use ThreadPoolExecutor for parallel processing
            max_workers = min(len(documents_data), settings.BULK_UPLOAD_MAX_WORKERS or 5)

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all tasks
                future_to_doc = {
                    executor.submit(
                        self._process_single_document_with_metadata,
                        doc_data,
                        organization_id,
                        project_id
                    ): doc_data
                    for doc_data in documents_data
                }

                # Process completed tasks
                for future in as_completed(future_to_doc):
                    doc_data = future_to_doc[future]
                    try:
                        result = future.result()
                        results.append(result)

                        # Update progress after each completion
                        await self.update_progress(
                            job_id=job_id,
                            processed_files=len(results),
                            successful_uploads=sum(1 for r in results if r.success),
                            failed_uploads=sum(1 for r in results if not r.success),
                            results=results
                        )

                    except Exception as e:
                        logger.error(f"Task failed for document {doc_data.get('filename', 'unknown')}: {str(e)}")

                        # Create failure result
                        failure_result = DocumentProcessingResult(
                            filename=doc_data.get('filename', 'unknown'),
                            success=False,
                            error=str(e),
                            row_number=doc_data.get('_row_number', 0)
                        )
                        results.append(failure_result)

            return results

        except Exception as e:
            logger.error(f"Parallel processing failed for job {job_id}: {str(e)}")
            raise

    def _process_single_document_with_metadata(
        self,
        doc_data: Dict[str, Any],
        organization_id: str,
        project_id: str
    ) -> DocumentProcessingResult:
        """Process a single document with metadata extraction (sync method for executor)."""
        import asyncio

        # Create new event loop for this thread
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        try:
            return loop.run_until_complete(
                self._async_process_single_document_with_metadata(
                    doc_data, organization_id, project_id
                )
            )
        finally:
            loop.close()

    async def _async_process_single_document_with_metadata(
        self,
        doc_data: Dict[str, Any],
        organization_id: str,
        project_id: str
    ) -> DocumentProcessingResult:
        """Async processing of single document with metadata extraction."""
        import asyncio
        import backoff
        from pymongo.errors import AutoReconnect, NetworkTimeout
        filename = doc_data.get('filename', 'unknown')
        row_number = doc_data.get('_row_number', 0)
        start_time = datetime.utcnow()

        try:
            file_path = doc_data.get('file_path')
            if not file_path or not Path(file_path).exists():
                return DocumentProcessingResult(
                    filename=filename,
                    success=False,
                    error=f"File not found: {file_path}",
                    row_number=row_number
                )

            # Step 1: Create document record with retry for transient errors
            ds = await self._get_document_service()

            @backoff.on_exception(backoff.expo, (AutoReconnect, NetworkTimeout), max_tries=3)
            async def create_doc_with_retry():
                return await ds.create_document(
                    file_path=file_path,
                    filename=filename,
                    organization_id=organization_id,
                    project_id=project_id,
                    upload_type=doc_data.get('upload_type', 'incoming'),
                    letter_no=doc_data.get('letter_no', ''),
                    date=doc_data.get('date'),
                    current_user=doc_data.get('current_user'),
                    **{k: v for k, v in doc_data.items() if k not in ['filename', 'file_path', '_row_number']}
                )

            document = await create_doc_with_retry()

            result = DocumentProcessingResult(
                filename=filename,
                success=True,
                document_id=document.id,
                row_number=row_number,
                processing_time=(datetime.utcnow() - start_time).total_seconds()
            )

            # Step 2: Process metadata if OCR enabled
            if doc_data.get('ocr_enabled', False):
                try:
                    metadata_result = await self.metadata_service.process_document(
                        pdf_path=file_path,
                        path_structure=f"{organization_id}/{project_id}",
                        upload_type=doc_data.get('upload_type', 'incoming'),
                        document_id=document.id
                    )

                    if metadata_result.get('success', False):
                        result.metadata_extracted = True
                        result.ocr_completed = True
                        result.embeddings_created = metadata_result.get('chunks_created', 0)
                    else:
                        logger.warning(f"Metadata processing failed for {filename}: {metadata_result.get('error', 'Unknown error')}")

                except Exception as metadata_error:
                    logger.error(f"Metadata processing error for {filename}: {str(metadata_error)}")
                    # Don't fail the entire upload for metadata issues

            result.processing_time = (datetime.utcnow() - start_time).total_seconds()
            return result

        except Exception as e:
            logger.error(f"Failed to process document {filename}: {str(e)}")
            return DocumentProcessingResult(
                filename=filename,
                success=False,
                error=str(e),
                row_number=row_number,
                processing_time=(datetime.utcnow() - start_time).total_seconds()
            )

    async def cancel_job(self, job_id: str, reason: str = "Cancelled by user"):
        """Cancel a running job."""
        try:
            db = await self._get_db()

            # Update database
            update_data = {
                "status": "cancelled",
                "completed_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
                "error_message": reason
            }

            result = await db.bulk_upload_jobs.update_one(
                {"job_id": job_id, "status": "processing"},
                {"$set": update_data}
            )

            # Update memory cache
            if job_id in self._active_jobs:
                job_status = self._active_jobs[job_id]
                job_status.status = "cancelled"
                job_status.completed_at = datetime.utcnow()
                job_status.error_message = reason

            if result.modified_count > 0:
                logger.info(f"Cancelled job {job_id}: {reason}")
                return True
            else:
                logger.warning(f"Job {job_id} not found or not in processing state")
                return False

        except Exception as e:
            logger.error(f"Failed to cancel job {job_id}: {str(e)}")
            return False

    async def get_job_summary_stats(self) -> Dict[str, Any]:
        """Get summary statistics of all jobs."""
        try:
            db = await self._get_db()

            pipeline = [
                {
                    "$group": {
                        "_id": "$status",
                        "count": {"$sum": 1},
                        "total_files": {"$sum": "$total_files"},
                        "successful_uploads": {"$sum": "$successful_uploads"},
                        "failed_uploads": {"$sum": "$failed_uploads"}
                    }
                }
            ]

            results = await db.bulk_upload_jobs.aggregate(pipeline).to_list(length=None)

            stats = {
                "total_jobs": 0,
                "jobs_by_status": {},
                "total_files_processed": 0,
                "total_successful_uploads": 0,
                "total_failed_uploads": 0
            }

            for result in results:
                status = result["_id"]
                count = result["count"]

                stats["total_jobs"] += count
                stats["jobs_by_status"][status] = count
                stats["total_files_processed"] += result.get("total_files", 0)
                stats["total_successful_uploads"] += result.get("successful_uploads", 0)
                stats["total_failed_uploads"] += result.get("failed_uploads", 0)

            return stats

        except Exception as e:
            logger.error(f"Failed to get job summary stats: {str(e)}")
            return {}
