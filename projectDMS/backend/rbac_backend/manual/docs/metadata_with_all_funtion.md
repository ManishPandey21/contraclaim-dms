Legacy reference snapshot of the historical metadata pipeline.

```python
import asyncio
import hashlib
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime
from dataclasses import dataclass, asdict
from contextlib import asynccontextmanager

# Async MongoDB driver
from motor.motor_asyncio import AsyncIOMotorClient
from bson.objectid import ObjectId

# OpenAI async client
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

@dataclass
class DocumentProcessingConfig:
    """Configuration for document processing"""
    uploads_dir: str = "uploads"
    process_dir: str = "uploads/process_file"
    ocr_language: str = "eng"
    max_file_size_mb: int = 100
    openai_timeout: float = 180.0
    embedding_model: str = "text-embedding-3-small"
    chunk_size: int = 3000
    chunk_overlap: int = 200
    
    # Database configuration
    mongo_uri: Optional[str] = None
    database_name: str = "contraclaim"
    
    def __post_init__(self):
        """Initialize configuration from environment if not provided"""
        if not self.mongo_uri:
            self.mongo_uri = os.getenv("DATABASE_URL", "mongodb://localhost:27017/contraclaim")
        
        # Try to get settings from project config
        try:
            from ..core.config import settings
            self.mongo_uri = getattr(settings, "DATABASE_URL", self.mongo_uri)
            self.database_name = getattr(settings, "DATABASE_NAME", self.database_name)
        except ImportError:
            pass

@dataclass
class ParsedDocumentMetadata:
    """Structured metadata extracted from documents"""
    date: Optional[str] = None
    subject: Optional[str] = None
    letter_no: Optional[str] = None
    from_company: Optional[str] = None
    to_company: Optional[str] = None
    references: List[str] = None
    summary: Optional[str] = None
    keywords: List[str] = None
    contractual_clauses: List[str] = None
    full_content: Optional[str] = None
    
    def __post_init__(self):
        if self.references is None:
            self.references = []
        if self.keywords is None:
            self.keywords = []
        if self.contractual_clauses is None:
            self.contractual_clauses = []
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class ProcessingResult:
    """Result of document processing"""
    success: bool
    document_id: Optional[str] = None
    processed_path: Optional[str] = None
    metadata: Optional[ParsedDocumentMetadata] = None
    chunks_created: int = 0
    error: Optional[str] = None
    processing_time: float = 0.0

class DocumentProcessingError(Exception):
    """Custom exception for document processing errors"""
    pass

class TextProcessingService:
    """Service for text processing and metadata parsing"""
    
    def __init__(self, config: DocumentProcessingConfig):
        self.config = config
    
    def chunk_text(self, text: str) -> List[str]:
        """
        Split text into chunks with overlap.
        
        Args:
            text: Text to chunk
            
        Returns:
            List of text chunks
        """
        text = text.strip()
        if not text:
            return []
        
        chunks = []
        start = 0
        text_length = len(text)
        
        while start < text_length:
            end = min(text_length, start + self.config.chunk_size)
            chunk = text[start:end]
            chunks.append(chunk)
            
            if end == text_length:
                break
            
            # Move start position with overlap
            start = max(start + self.config.chunk_size - self.config.chunk_overlap, start + 1)
        
        logger.info(f"Split text into {len(chunks)} chunks")
        return chunks
    
    def parse_extraction_report(self, report: str) -> ParsedDocumentMetadata:
        """
        Parse structured extraction report into metadata object.
        
        Args:
            report: Structured text report from LLM
            
        Returns:
            ParsedDocumentMetadata object
        """
        try:
            # Normalize text for parsing
            lines = report.splitlines()
            text = "\n".join(line.strip() for line in lines if line.strip())
            
            # Extract individual fields
            date_str = self._extract_field(text, [
                r"^\s*(?:1\)|-)?\s*Date\s*[:\-]\s*(.+)$",
                r"^\s*Date\s*\.\s*(.+)$",
                r"^\s*Dated?\s*[:\-]\s*(.+)$",
            ])
            
            subject = self._extract_field(text, [
                r"^\s*(?:5\)|-)?\s*Subject\s*[:\-]\s*(.+)$",
                r"^\s*Re\s*[:\-]\s*(.+)$",
            ])
            
            letter_no = self._extract_field(text, [
                r"^\s*(?:2\)|-)?\s*Letter\s*No\.?\s*[:\-]\s*(.+)$",
                r"^\s*Letter\s*Number\s*[:\-]\s*(.+)$",
                r"^\s*Ref(?:erence)?\s*No\.?\s*[:\-]\s*(.+)$",
            ])
            
            from_company = self._extract_field(text, [
                r"^\s*(?:3\)|-)?\s*From\s*(?:\(Company\))?\s*[:\-]\s*(.+)$",
                r"^\s*Sender\s*[:\-]\s*(.+)$",
                r"^\s*From\s*[:\-]\s*(.+)$",
            ])
            
            to_company = self._extract_field(text, [
                r"^\s*(?:4\)|-)?\s*To\s*(?:\(Company\))?\s*[:\-]\s*(.+)$",
                r"^\s*Recipient\s*[:\-]\s*(.+)$",
                r"^\s*To\s*[:\-]\s*(.+)$",
            ])
            
            # Extract list fields
            references = self._extract_list_field(text, [
                r"(?ims)^\s*(?:6\)|-)?\s*References?\s*(?:\(Ref\.?\))?\s*[:\-]?\s*(.+?)(?=\n\s*(?:\d+\)\s*[A-Z]|Summary|Key\s*Words|Contractual\s*Clauses|Full\s*content|$))"
            ])
            
            summary = self._extract_summary(text)
            
            keywords = self._extract_list_field(text, [
                r"(?ims)Key Words[:\-]?\s*(.+?)(?=\n\s*(?:\d+\)|Contractual Clauses|Full content|$))",
                r"(?ims)Key contractual words[:\-]?\s*(.+?)(?=\n\s*(?:\d+\)|Contractual Clauses|Full content|$))"
            ])
            
            contractual_clauses = self._extract_list_field(text, [
                r"(?ims)Contractual Clauses[:\-]?\s*(.+?)(?=\n\s*(?:\d+\)|Key Words|Full content|$))"
            ])
            
            # Filter out "Not found" entries
            if contractual_clauses and len(contractual_clauses) == 1:
                if re.search(r"^\s*not\s*found\s*$", contractual_clauses[0], re.IGNORECASE):
                    contractual_clauses = []
            
            full_content = self._extract_field(text, [
                r"(?is)^\s*(?:10\)|-)?\s*Full\s*content\s*[:\-]?\s*(.+)$"
            ])
            
            metadata = ParsedDocumentMetadata(
                date=date_str,
                subject=subject,
                letter_no=letter_no,
                from_company=from_company,
                to_company=to_company,
                references=references or [],
                summary=summary,
                keywords=keywords or [],
                contractual_clauses=contractual_clauses or [],
                full_content=full_content
            )
            
            logger.info("Successfully parsed document metadata")
            return metadata
            
        except Exception as e:
            logger.error(f"Failed to parse extraction report: {e}")
            # Return empty metadata object on parsing failure
            return ParsedDocumentMetadata()
    
    def _extract_field(self, text: str, patterns: List[str]) -> Optional[str]:
        """Extract single field using regex patterns"""
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
            if match:
                value = match.group(1).strip().rstrip(":")
                return value if value else None
        return None
    
    def _extract_list_field(self, text: str, patterns: List[str]) -> List[str]:
        """Extract list field using regex patterns"""
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL | re.MULTILINE)
            if match:
                block = match.group(1).strip()
                return self._parse_list_block(block)
        return []
    
    def _parse_list_block(self, block: str) -> List[str]:
        """Parse block of text into list items"""
        if not block:
            return []
        
        items = []
        for line in block.splitlines():
            # Remove bullet prefixes and clean up
            cleaned = re.sub(r"^[\-\u2013\u2022\*\d\.\)\s]+", "", line).strip()
            if cleaned:
                items.append(cleaned)
        
        # Handle comma-separated single line
        if len(items) == 1 and "," in items[0]:
            comma_items = [item.strip() for item in items[0].split(",") if item.strip()]
            if len(comma_items) > 1:
                items = comma_items
        
        # Clean up punctuation
        items = [re.sub(r"[;\.\s]+$", "", item).strip() for item in items if item.strip()]
        
        return items
    
    def _extract_summary(self, text: str) -> Optional[str]:
        """Extract summary with special formatting"""
        pattern = r"(?ims)^\s*(?:7\)|-)?\s*Summary\s*[:\-]?\s*(.+?)(?=\n\s*(?:\d+\)|Key Words|Contractual Clauses|Full content|$))"
        match = re.search(pattern, text)
        
        if match:
            raw_summary = match.group(1).strip()
            
            # Format as bullet points
            lines_clean = []
            for line in raw_summary.splitlines():
                cleaned = re.sub(r"^[\-\*\d\.\)\s]+", "", line).strip()
                if cleaned:
                    lines_clean.append(f"- {cleaned}")
            
            return "\n".join(lines_clean) if lines_clean else raw_summary
        
        return None
    
    def parse_date_safe(self, date_str: Optional[str]) -> Optional[datetime]:
        """Safely parse date string to datetime object"""
        if not date_str:
            return None
        
        patterns = [
            "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", 
            "%b %d, %Y", "%d %b %Y", "%B %d, %Y"
        ]
        
        for pattern in patterns:
            try:
                return datetime.strptime(date_str.strip(), pattern)
            except ValueError:
                continue
        
        logger.warning(f"Unable to parse date: {date_str}")
        return None

class DatabaseService:
    """Service for database operations using async Motor client"""
    
    def __init__(self, config: DocumentProcessingConfig):
        self.config = config
        self._client: Optional[AsyncIOMotorClient] = None
        self._db = None
    
    async def get_database(self):
        """Get database connection with proper error handling"""
        if self._db is None:
            try:
                self._client = AsyncIOMotorClient(self.config.mongo_uri)
                self._db = self._client[self.config.database_name]
                
                # Test connection
                await self._db.command('ping')
                logger.info(f"Connected to MongoDB database: {self.config.database_name}")
                
            except Exception as e:
                raise DocumentProcessingError(f"Failed to connect to database: {str(e)}")
        
        return self._db
    
    async def close_connection(self):
        """Close database connection"""
        if self._client:
            self._client.close()
            self._client = None
            self._db = None
    
    async def save_document_data(
        self,
        document_id: Optional[str],
        file_path: str,
        parsed_metadata: ParsedDocumentMetadata,
        full_text: str,
        embedding_text: str
    ) -> int:
        """
        Save document data to database and create embeddings.
        
        Returns:
            Number of embedding chunks created
        """
        try:
            db = await self.get_database()
            
            # Save document metadata
            doc = await self._upsert_document_metadata(
                db, document_id, file_path, parsed_metadata, full_text
            )
            
            # Create and store embeddings
            chunks_created = await self._create_and_store_embeddings(
                db, doc, embedding_text
            )
            
            logger.info(f"Saved document data and created {chunks_created} embedding chunks")
            return chunks_created
            
        except Exception as e:
            logger.error(f"Failed to save document data: {e}")
            raise DocumentProcessingError(f"Database save failed: {str(e)}")
    
    async def _upsert_document_metadata(
        self,
        db,
        document_id: Optional[str],
        file_path: str,
        parsed_metadata: ParsedDocumentMetadata,
        full_text: str
    ) -> Dict[str, Any]:
        """Upsert document metadata to documents collection"""
        try:
            # Try to find existing document
            doc = await self._find_existing_document(db, document_id, file_path)
            
            # Prepare update data
            updates = {
                "ocrText": full_text,
                "updatedAt": datetime.utcnow()
            }
            
            # Add parsed metadata fields
            if parsed_metadata.subject:
                updates["subject"] = parsed_metadata.subject
            if parsed_metadata.letter_no:
                updates["letterNo"] = parsed_metadata.letter_no
            if parsed_metadata.from_company:
                updates["from_"] = parsed_metadata.from_company
            if parsed_metadata.to_company:
                updates["to"] = parsed_metadata.to_company
            if parsed_metadata.summary:
                updates["summary"] = parsed_metadata.summary
            if parsed_metadata.references:
                updates["reference"] = parsed_metadata.references
            if parsed_metadata.keywords:
                updates["keywords"] = parsed_metadata.keywords
            if parsed_metadata.contractual_clauses:
                updates["contractual_clauses"] = parsed_metadata.contractual_clauses
            
            # Parse date if available
            if parsed_metadata.date:
                try:
                    text_service = TextProcessingService(self.config)
                    parsed_date = text_service.parse_date_safe(parsed_metadata.date)
                    if parsed_date:
                        updates["date"] = parsed_date
                except Exception as e:
                    logger.warning(f"Failed to parse date {parsed_metadata.date}: {e}")
            
            logger.debug(f"Upserting document with updates: {list(updates.keys())}")
            
            if doc:
                # Update existing document
                await db.documents.update_one(
                    {"_id": doc["_id"]}, 
                    {"$set": updates}
                )
                doc.update(updates)
            else:
                # Create new document
                doc = {
                    "filename": os.path.basename(file_path),
                    "filepath_local": file_path,
                    "filepath_s3": "",
                    "presigned_url": "",
                    "filetype": "application/pdf",
                    "filesize": 0,
                    "uploadType": "incoming",
                    "tags": [],
                    "subTags": [],
                    "status": "draft",
                    "ocrEnabled": True,
                    "compressionEnabled": False,
                    "createdAt": datetime.utcnow(),
                    "organization_id": "",
                    "project_id": "",
                    "createdBy": "system",
                    "version": "1.0",
                    "enclosures": [],
                    "references": [],
                    **updates
                }
                
                result = await db.documents.insert_one(doc)
                doc["_id"] = result.inserted_id
            
            logger.info("Document metadata saved successfully")
            return doc
            
        except Exception as e:
            logger.error(f"Failed to upsert document metadata: {e}")
            raise
    
    async def _find_existing_document(
        self, 
        db, 
        document_id: Optional[str], 
        file_path: str
    ) -> Optional[Dict[str, Any]]:
        """Find existing document by ID or file path"""
        doc = None
        
        # Try to find by document ID first
        if document_id:
            try:
                doc = await db.documents.find_one({"_id": ObjectId(document_id)})
            except Exception:
                logger.warning(f"Invalid document ID format: {document_id}")
        
        # Fallback to file path matching
        if not doc:
            filename = os.path.basename(file_path)
            doc = await db.documents.find_one({"filename": filename})
        
        return doc
    
    async def _create_and_store_embeddings(
        self,
        db,
        doc: Dict[str, Any],
        text: str
    ) -> int:
        """Create embeddings and store in vector collection"""
        try:
            text_service = TextProcessingService(self.config)
            
            # Chunk text
            chunks = text_service.chunk_text(text)
            if not chunks:
                logger.warning("No text chunks to embed")
                return 0
            
            # Create embeddings using OpenAI
            embeddings = await self._create_embeddings(chunks)
            
            if len(embeddings) != len(chunks):
                raise DocumentProcessingError(f"Embedding count mismatch: {len(embeddings)} != {len(chunks)}")
            
            # Remove existing vectors for this document
            filter_query = {"document_id": str(doc["_id"])}
            delete_result = await db.document_vectors.delete_many(filter_query)
            if delete_result.deleted_count > 0:
                logger.info(f"Removed {delete_result.deleted_count} existing vector chunks")
            
            # Prepare vector documents
            vector_docs = []
            for i, (chunk_text, embedding) in enumerate(zip(chunks, embeddings)):
                checksum = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
                
                vector_doc = {
                    "document_id": str(doc["_id"]),
                    "organization_id": str(doc.get("organization_id", "")),
                    "project_id": str(doc.get("project_id", "")),
                    "uploadType": str(doc.get("uploadType", "incoming")),
                    "letterNo": doc.get("letterNo"),
                    "filepath_local": doc.get("filepath_local"),
                    "filepath_s3": doc.get("filepath_s3"),
                    "chunk_index": i,
                    "text": chunk_text,
                    "embedding_model": self.config.embedding_model,
                    "embedding_dims": len(embedding),
                    "embedding": embedding,
                    "num_tokens": len(chunk_text.split()),
                    "checksum_sha256": checksum,
                    "createdAt": datetime.utcnow()
                }
                
                vector_docs.append(vector_doc)
            
            # Insert vector documents
            if vector_docs:
                await db.document_vectors.insert_many(vector_docs)
                logger.info(f"Stored {len(vector_docs)} vector chunks")
            
            return len(vector_docs)
            
        except Exception as e:
            logger.error(f"Failed to create and store embeddings: {e}")
            raise DocumentProcessingError(f"Embedding storage failed: {str(e)}")
    
    async def _create_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Create embeddings using OpenAI API"""
        try:
            api_key = self._get_openai_api_key()
            if not api_key:
                raise DocumentProcessingError("OpenAI API key not configured for embeddings")
            
            client = AsyncOpenAI(api_key=api_key, timeout=60.0)
            
            response = await client.embeddings.create(
                model=self.config.embedding_model,
                input=texts
            )
            
            embeddings = [data.embedding for data in response.data]
            logger.info(f"Created {len(embeddings)} embeddings using {self.config.embedding_model}")
            
            return embeddings
            
        except Exception as e:
            logger.error(f"Embedding creation failed: {e}")
            raise DocumentProcessingError(f"Embedding creation failed: {str(e)}")
    
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

class FileService:
    """Service for file system operations"""
    
    def __init__(self, config: DocumentProcessingConfig):
        self.config = config
    
    async def save_summary(
        self,
        content: str,
        source_path: str,
        path_structure: str,
        upload_type: str
    ) -> None:
        """Save processing summary to file system"""
        try:
            # Determine summary file path
            summary_dir = Path(self.config.uploads_dir) / path_structure
            
            if upload_type == "incoming":
                summary_filename = "incoming.md"
            elif upload_type == "outgoing":
                summary_filename = "outgoing.md"
            else:
                summary_filename = "projectid.md"
            
            summary_path = summary_dir / summary_filename
            
            # Create directory if it doesn't exist
            summary_dir.mkdir(parents=True, exist_ok=True)
            
            # Prepare content with header
            header = f"\n\n--- {os.path.basename(source_path)} ---\n"
            footer = "\n" + "=" * 50 + "\n"
            full_content = header + content + footer
            
            # Append to summary file (run in thread pool to avoid blocking)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None, 
                lambda: summary_path.open("a", encoding="utf-8").write(full_content)
            )
            
            logger.info(f"Summary saved to: {summary_path}")
            
        except Exception as e:
            logger.error(f"Failed to save summary: {e}")
            # Don't raise exception - file saving is not critical

class OCRService:
    """Service for OCR operations with proper error handling"""
    
    def __init__(self, config: DocumentProcessingConfig):
        self.config = config
        self._ocr_available = self._check_ocr_availability()
    
    def _check_ocr_availability(self) -> bool:
        """Check if OCR dependencies are available"""
        try:
            import ocrmypdf
            return True
        except ImportError:
            logger.warning("OCRmyPDF not available - scanned PDFs may not be processed correctly")
            return False
    
    def is_pdf_textual(self, pdf_path: Path, max_pages: int = 5) -> bool:
        """Check if PDF has extractable text"""
        try:
            # Try PyPDF2 first
            try:
                import PyPDF2
                with open(pdf_path, 'rb') as f:
                    reader = PyPDF2.PdfReader(f)
                    pages_to_scan = min(len(reader.pages), max_pages)
                    
                    for i in range(pages_to_scan):
                        text = reader.pages[i].extract_text() or ""
                        if text.strip():
                            return True
                    return False
                    
            except Exception:
                pass
            
            # Fallback to pdfplumber (more reliable)
            try:
                import pdfplumber
                with pdfplumber.open(pdf_path) as pdf:
                    pages_to_scan = min(len(pdf.pages), max_pages)
                    for i in range(pages_to_scan):
                        text = pdf.pages[i].extract_text() or ""
                        if text.strip():
                            return True
                    return False
                    
            except Exception:
                return False
                
        except Exception as e:
            logger.error(f"Error checking PDF text content: {e}")
            return False
    
    async def process_pdf(self, input_path: Path) -> Tuple[Path, Optional[str]]:
        """
        Process PDF with OCR if needed and return processed file path and raw OCR text.
        
        Args:
            input_path: Path to input PDF
            
        Returns:
            Tuple of (processed_file_path, raw_ocr_text)
            
        Raises:
            DocumentProcessingError: If processing fails
        """
        try:
            if not input_path.exists():
             raise DocumentProcessingError(f"Input file not found: {input_path}")
            
            # Create destination directory
            dest_dir = Path(self.config.process_dir)
            dest_dir.mkdir(parents=True, exist_ok=True)
            
            dest_path = dest_dir / input_path.name
            sidecar_txt_path = dest_path.with_suffix('.txt')
            
            raw_ocr_text = None
            
            if not self._ocr_available:
                # Just copy the file
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, lambda: self._copy_file(input_path, dest_path))
                logger.info("OCR not available - copied file without processing")
                return dest_path, None
            
            # Check if text layer exists
            has_text = await self._check_pdf_textual_async(input_path)
            
            if has_text:
                # Copy original file
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, lambda: self._copy_file(input_path, dest_path))
                logger.info("Text layer detected - using original PDF")
                
                # Try to generate sidecar text for better embeddings
                try:
                    raw_ocr_text = await self._extract_sidecar_text(dest_path, sidecar_txt_path)
                except Exception as e:
                    logger.warning(f"Failed to extract sidecar text: {e}")
            else:
                # Perform OCR
                logger.info("Running OCR preprocessing (no text layer detected)")
                raw_ocr_text = await self._run_ocr_with_sidecar(input_path, dest_path, sidecar_txt_path)
            
            return dest_path, raw_ocr_text
            
        except DocumentProcessingError:
            raise
        except Exception as e:
            logger.error(f"PDF processing failed: {e}")
            raise DocumentProcessingError(f"PDF processing failed: {str(e)}")
    
    def _copy_file(self, src: Path, dst: Path):
        """Copy file synchronously"""
        import shutil
        shutil.copy2(src, dst)
    
    async def _check_pdf_textual_async(self, pdf_path: Path) -> bool:
        """Check PDF text content asynchronously"""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self.is_pdf_textual, pdf_path)
    
    async def _run_ocr_with_sidecar(self, input_path: Path, output_path: Path, sidecar_path: Path) -> Optional[str]:
        """Run OCR and generate sidecar text file"""
        try:
            import ocrmypdf
            from ocrmypdf.exceptions import PriorOcrFoundError, EncryptedPdfError
            
            loop = asyncio.get_event_loop()
            
            try:
                # Run OCR in executor
                await loop.run_in_executor(
                    None,
                    lambda: ocrmypdf.ocr(
                        input_file=str(input_path),
                        output_file=str(output_path),
                        language=self.config.ocr_language,
                        rotate_pages=True,
                        deskew=True,
                        optimize=1,
                        force_ocr=True,  # Force OCR for scanned documents
                        jobs=min(2, os.cpu_count() or 2)
                    )
                )
                
                logger.info(f"OCR processing completed: {output_path}")
                
                # Generate sidecar text file
                return await self._extract_sidecar_text(output_path, sidecar_path)
                
            except PriorOcrFoundError:
                logger.info("Prior OCR found - using original file")
                await loop.run_in_executor(None, lambda: self._copy_file(input_path, output_path))
                return await self._extract_sidecar_text(output_path, sidecar_path)
                
            except EncryptedPdfError as e:
                logger.warning(f"PDF is encrypted: {e}")
                raise DocumentProcessingError("Cannot process encrypted PDF")
                        
        except Exception as e:
            logger.error(f"OCR processing failed: {e}")
            # Fallback: copy original file
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, lambda: self._copy_file(input_path, output_path))
                logger.info(f"Fallback: copied original file to {output_path}")
            except Exception as copy_error:
                logger.error(f"Failed to copy original file: {copy_error}")
                raise DocumentProcessingError(f"OCR failed and unable to copy original: {copy_error}")
            
            return None
    
    async def _extract_sidecar_text(self, pdf_path: Path, sidecar_path: Path) -> Optional[str]:
        """Extract text to sidecar file for better embeddings"""
        try:
            import pdfplumber
            
            loop = asyncio.get_event_loop()
            
            def extract_text():
                full_text = ""
                with pdfplumber.open(pdf_path) as pdf:
                    for page in pdf.pages:
                        text = page.extract_text() or ""
                        full_text += text + "\n"
                
                # Save to sidecar file
                sidecar_path.write_text(full_text, encoding='utf-8')
                return full_text.strip()
            
            raw_text = await loop.run_in_executor(None, extract_text)
            
            if raw_text:
                logger.info(f"Extracted text: {len(raw_text)} characters")
                return raw_text
            else:
                logger.warning("No text could be extracted from PDF")
                return None
                        
        except Exception as e:
            logger.warning(f"Failed to extract sidecar text: {e}")
            return None

class OpenAIService:
    """Service for OpenAI API interactions"""
    
    def __init__(self, config: DocumentProcessingConfig):
        self.config = config
        self._client = self._initialize_client()
    
    def _initialize_client(self) -> AsyncOpenAI:
        """Initialize OpenAI client"""
        try:
            api_key = self._get_api_key()
            if not api_key:
                raise DocumentProcessingError("OpenAI API key not configured")
            
            return AsyncOpenAI(api_key=api_key, timeout=self.config.openai_timeout)
            
        except ImportError:
            raise DocumentProcessingError("OpenAI library not available")
        except Exception as e:
            raise DocumentProcessingError(f"Failed to initialize OpenAI client: {e}")
    
    def _get_api_key(self) -> Optional[str]:
        """Get OpenAI API key from settings or environment"""
        try:
            from ..core.config import settings
            api_key = getattr(settings, "OPENAI_API_KEY", None)
            if api_key:
                return api_key
        except Exception:
            pass
        
        return os.getenv("OPENAI_API_KEY")
    
    async def upload_file(self, file_path: Path, max_retries: int = 3) -> str:
        """
        Upload file to OpenAI and return file ID.
        
        Args:
            file_path: Path to file to upload
            max_retries: Maximum number of retry attempts
            
        Returns:
            OpenAI file ID
            
        Raises:
            DocumentProcessingError: If upload fails
        """
        for attempt in range(max_retries):
            try:
                # Use async file reading
                loop = asyncio.get_event_loop()
                file_data = await loop.run_in_executor(None, file_path.read_bytes)
                
                response = await self._client.files.create(
                    file=("document.pdf", file_data, "application/pdf"),
                    purpose="assistants"
                )
                
                logger.info(f"File uploaded successfully. ID: {response.id}")
                return response.id
                
            except Exception as e:
                if attempt == max_retries - 1:
                    raise DocumentProcessingError(f"File upload failed after {max_retries} retries: {e}")
                
                logger.warning(f"Upload attempt {attempt + 1} failed: {e}")
                await asyncio.sleep(2 ** attempt)  # Exponential backoff
    
    async def process_document(self, file_id: str) -> str:
        """
        Process document using OpenAI and return extracted content.
        
        Args:
            file_id: OpenAI file ID
            
        Returns:
            Extracted content from document
            
        Raises:
            DocumentProcessingError: If processing fails
        """
        try:
            extraction_prompt = self._get_extraction_prompt()
            
            # Use chat completion API which supports file uploads
            response = await self._client.chat.completions.create(
                model=self._get_model_name(),
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": extraction_prompt},
                            {"type": "file", "file_id": file_id}
                        ]
                    }
                ],
                max_tokens=4000,
                temperature=0.1
            )
            
            if not response.choices or not response.choices[0].message.content:
                raise DocumentProcessingError("No content extracted from document")
            
            content = response.choices[0].message.content
            logger.info(f"Extracted content length: {len(content)}")
            
            return content
            
        except Exception as e:
            logger.error(f"Document processing failed: {e}")
            raise DocumentProcessingError(f"Document processing failed: {str(e)}")
    
    def _get_extraction_prompt(self) -> str:
        """Get extraction prompt for document processing"""
        return (
            "You are a Contract expert extracting structured metadata from letters. "
            "Extract the following fields from the attached PDF and return them in this exact format:\n\n"
            "1) Date: [extracted date or 'Not found']\n"
            "2) Letter No.: [extracted letter number or 'Not found']\n"
            "3) From (Company): [sender company or 'Not found']\n"
            "4) To (Company): [recipient company or 'Not found']\n"
            "5) Subject: [document subject or 'Not found']\n"
            "6) References: [list each reference on a new line with - prefix or 'Not found']\n"
            "7) Summary: [3-7 concise bullet points with - prefix summarizing the content]\n"
            "8) Key Words: [comma-separated list of key contractual words mentioned]\n"
            "9) Contractual Clauses: [comma-separated list of specific mentioned contractual clauses]\n"
            "10) Full content: [cleaned text of the full letter]\n"
        )
    
    def _get_model_name(self) -> str:
        """Get model name from settings or default"""
        try:
            from ..core.config import settings
            return getattr(settings, "OPENAI_MODEL", "gpt-4-turbo-preview")
        except Exception:
            return os.getenv("OPENAI_MODEL", "gpt-4-turbo-preview")
    
    async def cleanup_file(self, file_id: str) -> None:
        """Clean up uploaded file from OpenAI"""
        try:
            await self._client.files.delete(file_id)
            logger.info(f"File {file_id} deleted from OpenAI")
        except Exception as e:
            logger.warning(f"Error deleting file {file_id}: {e}")

class DocumentProcessor:
    """Main document processor service"""
    
    def __init__(self, config: Optional[DocumentProcessingConfig] = None):
        self.config = config or DocumentProcessingConfig()
        self.ocr_service = OCRService(self.config)
        self.openai_service = OpenAIService(self.config)
        self.text_service = TextProcessingService(self.config)
        self.database_service = DatabaseService(self.config)
        self.file_service = FileService(self.config)
    
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
        import time
        start_time = time.time()
        
        logger.info(f"Starting document processing for {pdf_path}")
        
        file_id: Optional[str] = None
        processed_path: Optional[Path] = None
        
        try:
            # Validate input file
            input_path = Path(pdf_path)
            if not input_path.exists():
                raise DocumentProcessingError(f"PDF file not found: {pdf_path}")
            
            if input_path.stat().st_size > self.config.max_file_size_mb * 1024 * 1024:
                raise DocumentProcessingError(f"File too large: {input_path.stat().st_size} bytes")
            
            # Step 1: Process PDF with OCR if needed
            processed_path, raw_ocr_text = await self.ocr_service.process_pdf(input_path)
            
            # Step 2: Upload to OpenAI
            file_id = await self.openai_service.upload_file(processed_path)
            
            # Step 3: Extract content using OpenAI
            extracted_content = await self.openai_service.process_document(file_id)
            
            # Step 4: Parse extracted content
            parsed_metadata = self.text_service.parse_extraction_report(extracted_content)
            
            # Step 5: Save results
            chunks_created = await self._save_results(
                extracted_content, raw_ocr_text, pdf_path, path_structure, 
                upload_type, document_id, parsed_metadata
            )
            
            processing_time = time.time() - start_time
            
            logger.info(f"Document processing completed successfully for {pdf_path} in {processing_time:.2f}s")
            
            return ProcessingResult(
                success=True,
                document_id=document_id,
                processed_path=str(processed_path),
                metadata=parsed_metadata,
                chunks_created=chunks_created,
                processing_time=processing_time
            )
            
        except Exception as e:
            processing_time = time.time() - start_time
            logger.error(f"Document processing failed for {pdf_path}: {e}")
            
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

# Legacy compatibility functions
def _parse_extraction_report(report: str) -> Dict[str, Any]:
    """Compat helper returning metadata as a plain dictionary."""
    service = TextProcessingService(DocumentProcessingConfig())
    metadata = service.parse_extraction_report(report or "")
    return metadata.to_dict()

def _upsert_document_metadata(db, file_path: str, parsed_metadata: Dict[str, Any], ocr_text: str) -> Dict[str, Any]:
    """Simplified synchronous upsert used by legacy tests."""
    # This is a synchronous compatibility function - for new code use the async version
    import asyncio
    
    async def async_upsert():
        config = DocumentProcessingConfig()
        db_service = DatabaseService(config)
        meta_obj = ParsedDocumentMetadata(**parsed_metadata)
        
        # Convert sync db to async (simplified)
        class MockAsyncDB:
            def __init__(self, sync_db):
                self.sync_db = sync_db
            
            async def find_one(self, query):
                return self.sync_db.documents.find_one(query)
            
            async def update_one(self, filter, update):
                return self.sync_db.documents.update_one(filter, update)
            
            async def insert_one(self, document):
                return self.sync_db.documents.insert_one(document)
        
        async_db = MockAsyncDB(db)
        doc = await db_service._upsert_document_metadata(async_db, None, file_path, meta_obj, ocr_text)
        return doc
    
    return asyncio.run(async_upsert())
```
