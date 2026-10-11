# services/metadata_processor_service.py

"""
Metadata processor service that integrates the enhanced metadata.py processing
with the application architecture for bulk upload and individual document processing.
"""

import asyncio
import logging
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

# Import correct processor and config
from ..services.document_processor import (
    DocumentProcessor,
    create_document_processor,
    DocumentProcessorError,
)
from ..config.document_processing_config import DocumentProcessingConfig

logger = logging.getLogger(__name__)

class DocumentProcessingError(Exception):
    """Custom exception for document processing errors"""
    pass

class MetadataProcessorService:
    """Service wrapper for metadata processing functionality."""

    def __init__(self, config: Optional[DocumentProcessingConfig] = None):
        """Initialize the metadata processor service."""
        self.config = config or DocumentProcessingConfig()
        self.processor = create_document_processor(self.config)

    async def process_document(
        self,
        pdf_path: str,
        path_structure: str,
        upload_type: str,
        document_id: Optional[str] = None,
        enable_ocr: bool = True,
        enable_embeddings: bool = True
    ) -> Dict[str, Any]:
        """
        Process a single document for metadata extraction.

        Args:
            pdf_path: Path to the PDF file to process
            path_structure: Organization/project path structure
            upload_type: Type of upload (incoming/outgoing)
            document_id: Optional document ID for database updates
            enable_ocr: Whether to enable OCR processing
            enable_embeddings: Whether to create vector embeddings

        Returns:
            Dictionary containing processing results
        """
        start_time = datetime.utcnow()

        try:
            logger.info(f"Starting metadata processing for: {pdf_path}")

            # Validate input file
            file_path = Path(pdf_path)
            if not file_path.exists():
                raise DocumentProcessingError(f"File not found: {pdf_path}")

            if not file_path.suffix.lower() == '.pdf':
                raise DocumentProcessingError(f"Only PDF files are supported, got: {file_path.suffix}")

            # Configure processing options
            processing_config = DocumentProcessingConfig(
                uploads_dir=self.config.uploads_dir,
                process_dir=self.config.process_dir,
                ocr_language=self.config.ocr_language,
                max_file_size_mb=self.config.max_file_size_mb,
                openai_timeout=self.config.openai_timeout,
                openai_embedding_model=self.config.openai_embedding_model,
                chunk_size=self.config.chunk_size,
                chunk_overlap=self.config.chunk_overlap,
                mongo_uri=self.config.mongo_uri,
                database_name=self.config.database_name
            )

            # Create processor with updated config
            processor = create_document_processor(processing_config)

            # Process the document
            logger.info("Starting metadata extraction for %s (PydanticAI enabled=%s)", pdf_path, processing_config.use_pydantic_ai)
            result = await processor.process_document(
                pdf_path=pdf_path,
                path_structure=path_structure,
                upload_type=upload_type,
                document_id=document_id
            )

            processing_time = (datetime.utcnow() - start_time).total_seconds()

            if is_dataclass(result):
                result_dict: Dict[str, Any] = asdict(result)
            elif hasattr(result, "model_dump"):
                result_dict = result.model_dump()
            elif hasattr(result, "dict"):
                result_dict = result.dict()
            elif isinstance(result, dict):
                result_dict = dict(result)
            else:
                raise TypeError(f"Unsupported processing result type: {type(result)!r}")

            metadata = result_dict.get("metadata")
            if metadata is not None:
                if is_dataclass(metadata):
                    result_dict["metadata"] = asdict(metadata)
                elif hasattr(metadata, "model_dump"):
                    result_dict["metadata"] = metadata.model_dump()
                elif hasattr(metadata, "dict"):
                    result_dict["metadata"] = metadata.dict()

            # Enrich result with additional information
            enriched_result = {
                **result_dict,
                "processing_time": processing_time,
                "file_path": pdf_path,
                "file_size": file_path.stat().st_size,
                "ocr_enabled": enable_ocr,
                "embeddings_enabled": enable_embeddings,
                "processed_at": datetime.utcnow().isoformat()
            }

            logger.info(f"Metadata processing completed for {pdf_path} in {processing_time:.2f}s")
            return enriched_result

        except DocumentProcessorError as e:
            processing_time = (datetime.utcnow() - start_time).total_seconds()
            logger.error(f"Metadata processing failed for {pdf_path}: {str(e)}")

            return {
                "success": False,
                "error": str(e),
                "processing_time": processing_time,
                "file_path": pdf_path,
                "processed_at": datetime.utcnow().isoformat()
            }

        except Exception as e:
            processing_time = (datetime.utcnow() - start_time).total_seconds()
            logger.error(f"Unexpected error processing {pdf_path}: {str(e)}")

            return {
                "success": False,
                "error": f"Unexpected processing error: {str(e)}",
                "processing_time": processing_time,
                "file_path": pdf_path,
                "processed_at": datetime.utcnow().isoformat()
            }

    async def process_batch_documents(
        self,
        documents_info: list[Dict[str, Any]],
        max_concurrent: int = 3
    ) -> list[Dict[str, Any]]:
        """
        Process multiple documents concurrently with controlled concurrency.

        Args:
            documents_info: List of document info dictionaries containing:
                - pdf_path: Path to PDF file
                - path_structure: Organization/project path
                - upload_type: incoming/outgoing
                - document_id: Optional document ID
            max_concurrent: Maximum number of concurrent processes

        Returns:
            List of processing results
        """
        try:
            logger.info(f"Starting batch processing of {len(documents_info)} documents")

            # Create semaphore to limit concurrency
            semaphore = asyncio.Semaphore(max_concurrent)

            async def process_with_semaphore(doc_info):
                async with semaphore:
                    return await self.process_document(
                        pdf_path=doc_info['pdf_path'],
                        path_structure=doc_info['path_structure'],
                        upload_type=doc_info['upload_type'],
                        document_id=doc_info.get('document_id'),
                        enable_ocr=doc_info.get('enable_ocr', True),
                        enable_embeddings=doc_info.get('enable_embeddings', True)
                    )

            # Process all documents concurrently
            tasks = [process_with_semaphore(doc_info) for doc_info in documents_info]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Handle exceptions in results
            processed_results = []
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"Document processing failed: {str(result)}")
                    processed_results.append({
                        "success": False,
                        "error": str(result),
                        "file_path": documents_info[i].get('pdf_path', 'unknown'),
                        "processed_at": datetime.utcnow().isoformat()
                    })
                else:
                    processed_results.append(result)

            successful_count = sum(1 for r in processed_results if r.get('success', False))
            logger.info(f"Batch processing completed: {successful_count}/{len(processed_results)} successful")

            return processed_results

        except Exception as e:
            logger.error(f"Batch processing failed: {str(e)}")
            raise DocumentProcessorError(f"Batch processing failed: {str(e)}")

    async def extract_metadata_only(
        self,
        pdf_path: str,
        skip_database_save: bool = False,
        skip_embeddings: bool = False
    ) -> Dict[str, Any]:
        """
        Extract metadata from PDF without full document processing.

        Args:
            pdf_path: Path to PDF file
            skip_database_save: Skip saving to database
            skip_embeddings: Skip creating vector embeddings

        Returns:
            Extracted metadata dictionary
        """
        try:
            logger.info(f"Extracting metadata from: {pdf_path}")

            # Create a lightweight processor for metadata extraction only
            from ..services.ocr_service import OCRService
            from ..services.openai_service import OpenAIService
            from ..services.text_processing_service import TextProcessingService

            config = self.config
            ocr_service = OCRService(config)
            openai_service = OpenAIService(config)
            text_service = TextProcessingService(config)

            # Process PDF
            input_path = Path(pdf_path)
            processed_path, raw_ocr_text = await ocr_service.process_pdf(input_path)

            # Upload to OpenAI and extract content
            file_id = openai_service.upload_file(processed_path)

            try:
                extracted_content = openai_service.extract_document_metadata(file_id)

                # Parse extracted content
                parsed_metadata = text_service.parse_extraction_report(extracted_content)

                result = {
                    "success": True,
                    "metadata": {
                        "date": parsed_metadata.date,
                        "subject": parsed_metadata.subject,
                        "letter_no": parsed_metadata.letter_no,
                        "from_company": parsed_metadata.from_company,
                        "to_company": parsed_metadata.to_company,
                        "references": parsed_metadata.references,
                        "summary": parsed_metadata.summary,
                        "keywords": parsed_metadata.keywords,
                        "contractual_clauses": parsed_metadata.contractual_clauses,
                        "full_content": parsed_metadata.full_content
                    },
                    "raw_content": extracted_content,
                    "ocr_text": raw_ocr_text,
                    "processed_file_path": str(processed_path),
                    "extracted_at": datetime.utcnow().isoformat()
                }

                logger.info(f"Metadata extraction completed for: {pdf_path}")
                return result

            finally:
                # Always cleanup the uploaded file
                openai_service.cleanup_file(file_id)

        except Exception as e:
            logger.error(f"Metadata extraction failed for {pdf_path}: {str(e)}")
            return {
                "success": False,
                "error": str(e),
                "file_path": pdf_path,
                "extracted_at": datetime.utcnow().isoformat()
            }

    async def validate_document_processability(self, pdf_path: str) -> Dict[str, Any]:
        """
        Validate if a document can be processed for metadata extraction.

        Args:
            pdf_path: Path to PDF file

        Returns:
            Validation result dictionary
        """
        try:
            file_path = Path(pdf_path)

            # Basic file checks
            if not file_path.exists():
                return {
                    "valid": False,
                    "error": "File does not exist",
                    "checks": {"file_exists": False}
                }

            if not file_path.suffix.lower() == '.pdf':
                return {
                    "valid": False,
                    "error": "Only PDF files are supported",
                    "checks": {"file_exists": True, "is_pdf": False}
                }

            file_size_mb = file_path.stat().st_size / (1024 * 1024)
            if file_size_mb > self.config.max_file_size_mb:
                return {
                    "valid": False,
                    "error": f"File too large: {file_size_mb:.1f}MB > {self.config.max_file_size_mb}MB",
                    "checks": {"file_exists": True, "is_pdf": True, "size_ok": False}
                }

            # Check if PDF has text or needs OCR
            from ..services.ocr_service import OCRService
            ocr_service = OCRService(self.config)
            has_text = ocr_service.is_pdf_textual(file_path)

            return {
                "valid": True,
                "file_size_mb": file_size_mb,
                "has_text_layer": has_text,
                "needs_ocr": not has_text,
                "checks": {
                    "file_exists": True,
                    "is_pdf": True,
                    "size_ok": True,
                    "readable": True
                }
            }

        except Exception as e:
            logger.error(f"Validation failed for {pdf_path}: {str(e)}")
            return {
                "valid": False,
                "error": f"Validation error: {str(e)}",
                "checks": {"validation_error": True}
            }

    async def get_processing_status(self, document_id: str) -> Optional[Dict[str, Any]]:
        """
        Get the processing status of a document.

        Args:
            document_id: ID of the document

        Returns:
            Processing status dictionary or None if not found
        """
        try:
            # This would typically query a processing status database
            # For now, we'll return a placeholder
            logger.info(f"Getting processing status for document: {document_id}")

            # In a real implementation, you would:
            # 1. Query the database for processing tasks
            # 2. Check if metadata extraction is complete
            # 3. Check if embeddings are created
            # 4. Return current status

            return {
                "document_id": document_id,
                "status": "completed",  # placeholder
                "last_updated": datetime.utcnow().isoformat()
            }

        except Exception as e:
            logger.error(f"Failed to get processing status for {document_id}: {str(e)}")
            return None

    def get_config(self) -> DocumentProcessingConfig:
        """Get the current processing configuration."""
        return self.config

    def update_config(self, **config_updates) -> None:
        """Update processing configuration."""
        for key, value in config_updates.items():
            if hasattr(self.config, key):
                setattr(self.config, key, value)
            else:
                logger.warning(f"Unknown config parameter: {key}")

        # Recreate processor with new config
        self.processor = create_document_processor(self.config)
        logger.info("Configuration updated and processor recreated")
