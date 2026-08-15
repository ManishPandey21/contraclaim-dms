# services/document_processor.py

import asyncio
import logging
import time
import os
import re
from pathlib import Path
from typing import Optional, Any, Dict, Sequence
from uuid import uuid4

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
        document_id: Optional[str] = None,
        skip_embeddings: bool = False,
        organization_id: Optional[str] = None,
        project_id: Optional[str] = None,
        extraction_run_id: Optional[str] = None,
        retry_pages: Optional[Sequence[int]] = None,
    ) -> ProcessingResult:
        """
        Main entry point for document processing.

        Args:
            pdf_path: Path to PDF file
            path_structure: Path structure for organization
            upload_type: Type of upload (incoming/outgoing)
            document_id: Optional document ID
            skip_embeddings: Defer vector creation (duplicate-check pending);
                OCR and metadata extraction still run and are persisted.

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

            # Step 1: Page-wise extraction. Pages with a usable text layer keep
            # their native text; only thin pages are OCR'd. This replaces the
            # document-level is_pdf_textual decision, which inspected the first
            # five pages and, on finding any text, skipped OCR for the whole
            # document - leaving scanned pages indexed as empty.
            from .extraction_adapters.document_page_store import DocumentPageStore

            logger.info(
                "[document_pipeline] Starting page-wise extraction for %s",
                input_path.name,
            )
            resolved_document_id = document_id or input_path.stem
            run_id = extraction_run_id or str(uuid4())
            db = await self.database_service.get_database()
            page_store = DocumentPageStore(
                db=db,
                document_id=resolved_document_id,
                organization_id=organization_id or "",
                project_id=project_id,
                extraction_run_id=run_id,
            )
            extraction = await self.ocr_service.process_pdf_pagewise(
                input_path,
                store=page_store,
                document_id=resolved_document_id,
                retry_pages=retry_pages,
            )
            processed_path = input_path
            raw_ocr_text = extraction.combined_text or None
            ocr_text_len = len(raw_ocr_text) if raw_ocr_text else 0
            logger.info(
                "[document_pipeline] Extraction completed for %s "
                "(chars=%s, ocr_pages=%s, failed=%s, deferred=%s, completeness=%s)",
                input_path.name,
                ocr_text_len,
                extraction.ocr_pages_total,
                extraction.ocr_failed_pages,
                extraction.ocr_deferred_pages,
                extraction.completeness.value,
            )

            # Step 2: Upload to OpenAI
            partial_failures: Dict[str, Any] = {}
            extracted_content = ""
            metadata_source = "legacy_regex"

            if raw_ocr_text and raw_ocr_text.strip():
                logger.info(
                    "[document_pipeline] Requesting content extraction from OCR text for %s (chars=%s)",
                    input_path.name,
                    len(raw_ocr_text),
                )
                try:
                    extracted_content = await self.openai_service.process_text(
                        raw_ocr_text,
                        filename=input_path.name,
                    )
                    metadata_source = "openai_text_legacy_regex"
                except Exception as exc:
                    logger.warning(
                        "[document_pipeline] OpenAI OCR-text extraction failed for %s; "
                        "falling back to deterministic OCR metadata parser: %s",
                        input_path.name,
                        exc,
                    )
                    partial_failures["ai_extraction"] = {
                        "stage": "ocr_text_extraction",
                        "message": str(exc),
                    }
                    extracted_content = self._build_ocr_fallback_report(
                        raw_ocr_text,
                        filename=input_path.name,
                    )
                    metadata_source = "ocr_fallback_regex"
            else:
                logger.info("[document_pipeline] Uploading %s to OpenAI", processed_path.name if processed_path else input_path.name)
                file_id = await self.openai_service.upload_file(str(processed_path))
                logger.info("[document_pipeline] Uploaded file_id=%s for %s", file_id, input_path.name)

                # Step 3: Extract content using OpenAI
                logger.info("[document_pipeline] Requesting content extraction for %s", input_path.name)
                extracted_content = await self.openai_service.process_document(file_id)
            extracted_length = len(extracted_content or "")
            logger.info("[document_pipeline] Content extraction complete for %s (chars=%s)", input_path.name, extracted_length)

            # Step 4: Parse extracted content
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
                if metadata_source not in {"pydantic_ai", "openai_text_legacy_regex", "ocr_fallback_regex"}:
                    metadata_source = "legacy_regex"



            # Step 5: Save results
            logger.info("[document_pipeline] Persisting OCR, metadata, and embeddings for %s", input_path.name)
            chunks_created = await self._save_results(
                extracted_content, raw_ocr_text, pdf_path, path_structure,
                upload_type, document_id, parsed_metadata,
                skip_embeddings=skip_embeddings,
            )
            partial_failures.update(dict(getattr(self.database_service, "partial_failures", {}) or {}))

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
                metadata_debug=metadata_debug,
                partial_failures=partial_failures,
                extraction_result=extraction,
                extraction_completeness=extraction.completeness.value,
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

    def _build_ocr_fallback_report(self, ocr_text: str, *, filename: str) -> str:
        """Build a structured report from OCR text when AI extraction is unavailable."""

        text = (ocr_text or "").strip()
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        first_lines = lines[:80]
        filename_letter_no = self._letter_no_from_filename(filename)

        def first_match(patterns: list[str], source: str = text) -> Optional[str]:
            for pattern in patterns:
                match = re.search(pattern, source, flags=re.IGNORECASE | re.MULTILINE)
                if match:
                    value = re.sub(r"\s+", " ", match.group(1)).strip(" :-")
                    if value:
                        return value[:250]
            return None

        date = first_match(
            [
                r"\bDate\s*[:\-]?\s*([0-3]?\d[./-][01]?\d[./-](?:19|20)?\d{2})",
                r"\bDated?\s*[:\-]?\s*([0-3]?\d[./-][01]?\d[./-](?:19|20)?\d{2})",
                r"\b([0-3]?\d[./-][01]?\d[./-](?:19|20)\d{2})\b",
            ]
        )
        letter_no = first_match(
            [
                r"\bLetter\s*No\.?\s*[:\-]?\s*([A-Z0-9][A-Z0-9/_.\-\s]{4,90})",
                r"\b(?:Our\s+)?Ref(?:erence)?\s*(?:No\.?)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9/_.\-\s]{4,90})",
            ]
        ) or filename_letter_no
        subject = self._subject_from_lines(first_lines) or filename_letter_no
        from_company = first_match([r"^\s*From\s*[:\-]\s*(.+)$"])
        to_company = first_match([r"^\s*To\s*[:\-]\s*(.+)$"])
        summary_lines = [line for line in lines if len(line) >= 24][:5]
        summary = "\n".join(f"- {line[:220]}" for line in summary_lines) or f"- OCR completed for {filename}"
        keywords = self._keywords_from_text(" ".join(first_lines), letter_no=letter_no, subject=subject)

        def value_or_null(value: Optional[str]) -> str:
            return value if value else "null"

        return "\n".join(
            [
                f"1) Date: {value_or_null(date)}",
                f"2) Letter No.: {value_or_null(letter_no)}",
                f"3) From (Company): {value_or_null(from_company)}",
                f"4) To (Company): {value_or_null(to_company)}",
                f"5) Subject: {value_or_null(subject)}",
                "6) References: null",
                "7) Asset Type: null",
                "8) Location: null",
                "9) Specific Area: null",
                "11) Chainage From: null",
                "12) Chainage To: null",
                "13) Work Type: null",
                "14) Issue Nature: null",
                "15) Claim Category: null",
                "16) Alleged Responsibility: null",
                "17) Priority: null",
                f"18) Key Words: {', '.join(keywords) if keywords else 'null'}",
                "19) Linked Event Suggested: null",
                "20) Reference Chain: null",
                f"21) Key Words: {', '.join(keywords) if keywords else 'null'}",
                f"22) Summary: {summary}",
                "23) Contractual Clauses: null",
                "24) Key Reply Points - Points to be Addressed While Responding: null",
                f"25) Full Content: {text}",
                "26) extracted_tags: null",
                "27) extracted_subTags: null",
            ]
        )

    def _letter_no_from_filename(self, filename: str) -> Optional[str]:
        stem = Path(filename or "").stem.strip()
        stem = re.sub(r"^[a-f0-9]{24}_", "", stem, flags=re.IGNORECASE)
        return stem or None

    def _subject_from_lines(self, lines: list[str]) -> Optional[str]:
        for index, line in enumerate(lines):
            match = re.match(r"^\s*(?:Sub(?:ject)?|Re)\s*[:\-]\s*(.+)$", line, flags=re.IGNORECASE)
            if not match:
                continue
            subject = match.group(1).strip()
            if index + 1 < len(lines):
                next_line = lines[index + 1].strip()
                if next_line and not re.match(r"^(Ref|Date|To|From)\b", next_line, flags=re.IGNORECASE):
                    subject = f"{subject} {next_line}".strip()
            return re.sub(r"\s+", " ", subject)[:250] if subject else None
        return None

    def _keywords_from_text(self, text: str, *, letter_no: Optional[str], subject: Optional[str]) -> list[str]:
        candidates: list[str] = []
        for value in (letter_no, subject):
            if value:
                candidates.extend(part.strip(" ,.;:()[]") for part in re.split(r"[\s,/]+", value))
        candidates.extend(
            match.group(0)
            for match in re.finditer(r"\b[A-Z][A-Za-z0-9/-]{3,}\b", text or "")
        )
        seen = set()
        keywords: list[str] = []
        for candidate in candidates:
            normalized = candidate.strip()
            key = normalized.lower()
            if len(normalized) < 4 or key in seen:
                continue
            seen.add(key)
            keywords.append(normalized)
            if len(keywords) >= 20:
                break
        return keywords
    
    async def _save_results(
        self,
        extracted_content: str,
        raw_ocr_text: Optional[str],
        original_path: str,
        path_structure: str,
        upload_type: str,
        document_id: Optional[str],
        parsed_metadata: ParsedDocumentMetadata,
        skip_embeddings: bool = False,
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
                embedding_text=text_for_embedding,
                skip_embeddings=skip_embeddings,
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




