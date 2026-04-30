# services/document_processor.py

import asyncio
import logging
import time
import os
from pathlib import Path
from typing import Optional, Any, Dict

from .text_processing_service import TextProcessingService
from .database_service import DatabaseService
from .file_service import FileService
from .ocr_service import OCRService
from .openai_service import OpenAIService
from .pydantic_ai_service import PydanticAIService, PydanticAIMetadataError

from ..config.document_processing_config import DocumentProcessingConfig
from ..models.document_metadata import ParsedDocumentMetadata, ProcessingResult

from ..utils.exceptions import DocumentProcessingError
from ..utils.pipeline_logging import configure_pipeline_logger

logger = logging.getLogger(__name__)
configure_pipeline_logger(logger)

class DocumentProcessorError(DocumentProcessingError):
    """Alias for document processor errors"""
    pass

class DocumentProcessor:
    """Main document processor service"""
    
    def __init__(self, config: Optional[DocumentProcessingConfig] = None):
        self.config = config or DocumentProcessingConfig()
        self.ocr_service = OCRService(self.config)
        self.openai_service = OpenAIService(self.config)
        self.text_service = TextProcessingService(self.config)
        self.database_service = DatabaseService(self.config)
        self.file_service = FileService(self.config)

        self.pydantic_ai_service = PydanticAIService(self.config)

    
    async def process_document(
        self,
        pdf_path: str,
        path_structure: str,
        upload_type: str,
        document_id: Optional[str] = None
    ) -> ProcessingResult:
        """
        Main entry point for document processing.
        
        Args:
            pdf_path: Path to PDF file
            path_structure: Path structure for organization
            upload_type: Type of upload (incoming/outgoing)
            document_id: Optional document ID
            
        Returns:
            ProcessingResult object
            
        Raises:
            DocumentProcessingError: If processing fails
        """
        start_time = time.time()
        
        logger.info("[document_pipeline] Starting document processing for %s", pdf_path)

        file_id: Optional[str] = None
        processed_path: Optional[Path] = None

        try:
            # Validate input file
            input_path = Path(pdf_path)
            logger.info("[document_pipeline] Validating input file %s", input_path)
            if not input_path.exists():
                raise DocumentProcessingError(f"PDF file not found: {pdf_path}")

            file_size = input_path.stat().st_size
            if file_size > self.config.max_file_size_mb * 1024 * 1024:
                raise DocumentProcessingError(f"File too large: {file_size} bytes")

            # Step 1: Process PDF with OCR if needed
            logger.info("[document_pipeline] Starting OCR step for %s", input_path.name)
            processed_path, raw_ocr_text = await self.ocr_service.process_pdf(input_path)
            ocr_text_len = len(raw_ocr_text) if raw_ocr_text else 0
            logger.info(
                "[document_pipeline] OCR step completed for %s (raw_text_chars=%s, processed_path=%s)",
                input_path.name,
                ocr_text_len,
                processed_path,
            )

            # Step 2: Upload to OpenAI
            logger.info("[document_pipeline] Uploading %s to OpenAI", processed_path.name if processed_path else input_path.name)
            file_id = await self.openai_service.upload_file(str(processed_path))
            logger.info("[document_pipeline] Uploaded file_id=%s for %s", file_id, input_path.name)

            # Step 3: Extract content using OpenAI
            logger.info("[document_pipeline] Requesting content extraction for %s", input_path.name)
            extracted_content = await self.openai_service.process_document(file_id)
            extracted_length = len(extracted_content or "")
            logger.info("[document_pipeline] Content extraction complete for %s (chars=%s)", input_path.name, extracted_length)

            # Step 4: Parse extracted content
            metadata_source = "legacy_regex"
            metadata_debug: Optional[Dict[str, Any]] = None
            parsed_metadata: Optional[ParsedDocumentMetadata] = None

            if self.pydantic_ai_service.is_enabled:
                logger.info("[document_pipeline] [%s] Attempting metadata extraction via PydanticAI", input_path.name)
                try:
                    agent_result = await self.pydantic_ai_service.extract_metadata(
                        document_text=raw_ocr_text or extracted_content,
                        context={"filename": input_path.name, "upload_type": upload_type},
                    )
                except PydanticAIMetadataError as exc:
                    logger.warning(f"PydanticAI metadata extraction failed for {pdf_path}: {exc}")
                    logger.info("[document_pipeline] [%s] Falling back to legacy regex metadata parser", input_path.name)
                else:
                    if agent_result:
                        metadata_source = "pydantic_ai"
                        parsed_metadata = agent_result.metadata
                        metadata_debug = {**agent_result.debug, "raw_result": agent_result.raw_result}
                        logger.info("[document_pipeline] [%s] Metadata extracted successfully via PydanticAI", input_path.name)
                    else:
                        logger.info("[document_pipeline] [%s] PydanticAI returned no metadata; using legacy regex parser", input_path.name)
            else:
                logger.info(
                    "[document_pipeline] [%s] PydanticAI disabled or unavailable (use_pydantic_ai=%s, api_key=%s); using legacy regex parser",
                    input_path.name,
                    getattr(self.config, "use_pydantic_ai", False),
                    "set" if bool(getattr(self.config, "openai_api_key", None)) else "unset",
                )

            if parsed_metadata is None:
                logger.info("[document_pipeline] [%s] Parsing metadata with legacy regex parser", input_path.name)
                parsed_metadata = self.text_service.parse_extraction_report(extracted_content)
                metadata_source = "legacy_regex" if metadata_source != "pydantic_ai" else metadata_source



            # Step 5: Save results
            logger.info("[document_pipeline] Persisting OCR, metadata, and embeddings for %s", input_path.name)
            chunks_created = await self._save_results(
                extracted_content, raw_ocr_text, pdf_path, path_structure, 
                upload_type, document_id, parsed_metadata
            )

            processing_time = time.time() - start_time

            logger.info("[document_pipeline] Document processing completed successfully for %s in %.2fs", pdf_path, processing_time)

            return ProcessingResult(
                success=True,
                document_id=document_id,
                processed_path=str(processed_path),
                metadata=parsed_metadata,
                chunks_created=chunks_created,
                processing_time=processing_time,
                metadata_source=metadata_source,
                metadata_debug=metadata_debug
            )

        except Exception as e:
            processing_time = time.time() - start_time
            logger.error(f"[document_pipeline] Document processing failed for {pdf_path}: {e}")

            return ProcessingResult(
                success=False,
                error=str(e),
                processing_time=processing_time
            )
            
        finally:
            # Cleanup
            if file_id:
                try:
                    await self.openai_service.cleanup_file(file_id)
                except Exception as e:
                    logger.warning(f"Error during cleanup: {e}")
            
            # Close database connection
            try:
                await self.database_service.close_connection()
            except Exception as e:
                logger.warning(f"Error closing database connection: {e}")
    
    async def _save_results(
        self,
        extracted_content: str,
        raw_ocr_text: Optional[str],
        original_path: str,
        path_structure: str,
        upload_type: str,
        document_id: Optional[str],
        parsed_metadata: ParsedDocumentMetadata
    ) -> int:
        """Save processing results to file system and database"""
        try:
            # Save summary to file system
            await self.file_service.save_summary(
                extracted_content, original_path, path_structure, upload_type
            )
            
            # Determine text to use for different purposes
            text_for_db = raw_ocr_text or extracted_content
            text_for_embedding = raw_ocr_text or parsed_metadata.full_content or extracted_content
            
            # Save to database and create embeddings
            chunks_created = await self.database_service.save_document_data(
                document_id=document_id,
                file_path=original_path,
                parsed_metadata=parsed_metadata,
                full_text=text_for_db,
                embedding_text=text_for_embedding
            )
            
            return chunks_created
            
        except Exception as e:
            logger.error(f"Failed to save results: {e}")
            raise DocumentProcessingError(f"Failed to save results: {str(e)}")

# Factory function
def create_document_processor(config: Optional[DocumentProcessingConfig] = None) -> DocumentProcessor:
    """Create document processor instance"""
    return DocumentProcessor(config)

# Convenience function for backward compatibility
async def process_document(
    pdf_path: str,
    path_structure: str,
    upload_type: str,
    document_id: Optional[str] = None
) -> ProcessingResult:
    """Convenience function for document processing"""
    processor = create_document_processor()
    return await processor.process_document(pdf_path, path_structure, upload_type, document_id)


__all__ = ["DocumentProcessor", "create_document_processor", "process_document", "DocumentProcessorError"]




