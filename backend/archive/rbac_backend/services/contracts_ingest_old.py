import asyncio
import hashlib
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, AsyncGenerator
import tempfile

from ..core.config import settings
from ..config.document_processing_config import DocumentProcessingConfig
from .contract_categorizer import create_contract_categorizer

LLAMA_INDEX_IMPORT_ERROR: Optional[Exception] = None
try:
    from .llamaindex_service import LlamaIndexVectorService  # type: ignore
except Exception as exc:  # pragma: no cover - optional dependency
    LlamaIndexVectorService = None  # type: ignore
    LLAMA_INDEX_IMPORT_ERROR = exc

logger = logging.getLogger(__name__)

if LLAMA_INDEX_IMPORT_ERROR:
    logger.warning(
        "LlamaIndex components unavailable; contract ingestion will skip vector indexing: %s",
        LLAMA_INDEX_IMPORT_ERROR,
    )






def _resolve_uploads_dir() -> Path:

    candidate = Path(getattr(settings, "UPLOADS_DIR", "uploads/contracts") or "uploads/contracts").expanduser()

    try:

        candidate.mkdir(parents=True, exist_ok=True)

        test_file = candidate / ".write_test"

        with test_file.open("wb") as handle:

            handle.write(b"test")

        test_file.unlink(missing_ok=True)

        return candidate

    except Exception:

        fallback = Path(tempfile.gettempdir()) / "contract_uploads"

        fallback.mkdir(parents=True, exist_ok=True)

        return fallback





BASE_UPLOAD_PATH: Path = _resolve_uploads_dir()

BASE_UPLOAD_DIR: str = str(BASE_UPLOAD_PATH)



@dataclass

class IngestionConfig:

    """Configuration for document ingestion"""

    CHUNK_SIZE: int = 4000

    CHUNK_OVERLAP: int = 350

    MIN_CHUNK_LENGTH: int = 100

    EMBEDDING_BATCH_SIZE: int = 16

    MAX_RETRIES: int = 3

    EMBEDDING_MODEL: str = "text-embedding-3-small"



@dataclass

class ClauseInfo:

    clause_number: str

    clause_title: str

    clause_text: str

    clause_type: str

    level: int

    parent_number: Optional[str]

    start_position: int

    end_position: int



class DocumentParsingError(Exception):

    """Custom exception for document parsing errors"""

    pass



class IngestionError(Exception):

    """Custom exception for ingestion errors"""

    pass



class DocumentParser:

    """Service for parsing different document formats"""

    

    def __init__(self):

        self.parsers = self._initialize_parsers()

    

    def _initialize_parsers(self) -> Dict[str, str]:

        """Initialize available parsers"""

        parsers = {}

        # Check for pdfminer

        try:

            from pdfminer.high_level import extract_text as pdf_extract_text

            parsers['pdfminer'] = 'fallback'

            logger.info("PDFMiner parser available")

        except ImportError:

            logger.info("PDFMiner parser not available")

        

        # Check for python-docx

        try:

            import docx

            parsers['docx'] = 'fallback'

            logger.info("Python-docx parser available")

        except ImportError:

            logger.info("Python-docx parser not available")

        

        return parsers

    

    async def extract_text(self, file_path: Path) -> str:

        """Extract text from document with proper error handling"""

        try:

            file_path = Path(file_path).resolve()

            

            if not file_path.exists():

                raise DocumentParsingError(f"File not found: {file_path}")

            

            if not file_path.is_file():

                raise DocumentParsingError(f"Path is not a file: {file_path}")

            

            extension = file_path.suffix.lower()

            
            # Try format-specific parsers

            if extension == '.pdf' and 'pdfminer' in self.parsers:

                text = await self._extract_pdf_text(file_path)

            elif extension == '.docx' and 'docx' in self.parsers:

                text = await self._extract_docx_text(file_path)

            else:

                # Last resort: try to read as plain text

                text = await self._extract_plain_text(file_path)

            

            if not text or not text.strip():

                raise DocumentParsingError("No text content extracted from document")

            

            logger.info(f"Successfully extracted text: {len(text)} characters")

            return text

            

        except DocumentParsingError:

            raise

        except Exception as e:

            raise DocumentParsingError(f"Text extraction failed: {str(e)}")

    

    async def _extract_pdf_text(self, file_path: Path) -> str:

        """Extract text from PDF using pdfminer"""

        from pdfminer.high_level import extract_text as pdf_extract_text

        

        return pdf_extract_text(str(file_path)) or ""

    

    async def _extract_docx_text(self, file_path: Path) -> str:

        """Extract text from DOCX using python-docx"""

        import docx

        

        doc = docx.Document(str(file_path))

        parts = []

        

        for paragraph in doc.paragraphs:

            if paragraph.text:

                parts.append(paragraph.text)

        

        return '\n'.join(parts)

    

    async def _extract_plain_text(self, file_path: Path) -> str:

        """Extract text as plain text file"""

        try:

            return file_path.read_text(encoding='utf-8')

        except UnicodeDecodeError:

            return file_path.read_text(encoding='utf-8', errors='ignore')



class TextChunker:

    """Service for chunking text into manageable pieces"""

    

    def __init__(self, config: IngestionConfig):

        self.config = config

        self._langchain_available = self._check_langchain()

    

    def _check_langchain(self) -> bool:

        """Check if LangChain is available"""

        try:

            from langchain.text_splitter import RecursiveCharacterTextSplitter

            return True

        except ImportError:

            logger.info("LangChain not available, using fallback chunker")

            return False

    

    def chunk_text(self, text: str) -> List[str]:

        """Chunk text with clause awareness"""

        try:

            # First pass: clause-aware splitting

            coarse_parts = self._clause_aware_split(text)

            

            # Second pass: recursive chunking

            if self._langchain_available:

                chunks = self._langchain_chunk(coarse_parts)

            else:

                chunks = self._manual_chunk(coarse_parts)

            

            # Filter short chunks

            filtered_chunks = [

                chunk.strip() for chunk in chunks 

                if chunk and len(chunk.strip()) >= self.config.MIN_CHUNK_LENGTH

            ]

            

            if not filtered_chunks:

                raise ValueError("No valid chunks produced after processing")

            

            logger.info(f"Text chunked into {len(filtered_chunks)} pieces")

            return filtered_chunks

            

        except Exception as e:

            raise IngestionError(f"Text chunking failed: {str(e)}")

    

    def _clause_aware_split(self, text: str) -> List[str]:

        """Split text by legal-style headings and clauses"""

        # Normalize line endings

        normalized = text.replace('\r\n', '\n').replace('\r', '\n')

        

        # Pattern for legal headings

        pattern = re.compile(

            r'(?im)^(?:section|clause|article)\s+[\w\.\-]+[:\.\-\s]+|^\s*\d+(\.\d+){0,3}\s+',

            re.MULTILINE | re.IGNORECASE

        )

        

        parts = []

        current_part = []

        

        for line in normalized.split('\n'):

            if pattern.match(line.strip()):

                if current_part:

                    parts.append('\n'.join(current_part).strip())

                    current_part = []

            current_part.append(line)

        

        if current_part:

            parts.append('\n'.join(current_part).strip())

        

        # Fallback to paragraph splitting if no matches found

        if len(parts) <= 1:

            parts = [p.strip() for p in text.split('\n\n') if p.strip()]

        

        return [part for part in parts if part]

    

    def _langchain_chunk(self, parts: List[str]) -> List[str]:

        """Use LangChain recursive chunker"""

        from langchain.text_splitter import RecursiveCharacterTextSplitter

        

        text = '\n\n'.join(parts)

        

        splitter = RecursiveCharacterTextSplitter(

            separators=['\n\n\n', '\n\n', '\n', '. ', ' ', ''],

            chunk_size=self.config.CHUNK_SIZE,

            chunk_overlap=self.config.CHUNK_OVERLAP,

            length_function=len

        )

        

        return splitter.split_text(text)

    

    def _manual_chunk(self, parts: List[str]) -> List[str]:

        """Manual chunking fallback"""

        text = '\n\n'.join(parts)

        chunks = []

        

        i = 0

        text_length = len(text)

        

        while i < text_length:

            end = min(text_length, i + self.config.CHUNK_SIZE)

            chunk = text[i:end]

            chunks.append(chunk)

            

            if end == text_length:

                break

            

            i = max(i + self.config.CHUNK_SIZE - self.config.CHUNK_OVERLAP, i + 1)

        

        return chunks



class ClauseExtractor:

    """Extracts structured clauses and performs smart splitting for large sections."""

    CLAUSE_PATTERN = re.compile(

        r"(?im)^(?P<label>section|clause|article|schedule|annex|appendix|chapter|part)?"

        r"\s*(?P<number>\d+(?:\.\d+){0,4}|[IVXLCM]+(?:\.\d+)*)"

        r"(?:\s*(?:\([a-z0-9]+\)))?"

        r"(?P<punct>[\.\)])?"

        r"\s*(?P<title>[^\n]*)$"

    )

    NATURAL_BREAKS = ("\n\n", "\n", ". ", "; ")

    def __init__(self, max_clause_length: int = 6000):

        self.max_clause_length = max_clause_length

    def extract_clauses(self, text: str) -> List[ClauseInfo]:

        """Identify clause boundaries and build structured clause data."""

        if not text:

            return []

        normalized, mapping = self._normalize_text(text)

        matches = list(self.CLAUSE_PATTERN.finditer(normalized))

        if not matches:

            fallback = self._fallback_clause(text)

            return [fallback] if fallback else []

        clauses: List[ClauseInfo] = []

        text_len = len(text)

        for idx, match in enumerate(matches):

            start_norm = match.start()

            end_norm = matches[idx + 1].start() if idx + 1 < len(matches) else len(normalized)

            start_pos = self._norm_index_to_original(mapping, start_norm, text_len)

            end_pos = self._compute_clause_end(mapping, end_norm, start_pos, text_len)

            raw_segment = text[start_pos:end_pos]

            stripped_segment = raw_segment.strip()

            if stripped_segment:

                leading = raw_segment.find(stripped_segment[0])

                trailing = len(raw_segment) - raw_segment.rfind(stripped_segment[-1]) - 1

                start_pos += max(leading, 0)

                end_pos -= max(trailing, 0)

            clause_text = text[start_pos:end_pos] if end_pos > start_pos else raw_segment.strip()

            clause_number = self._normalize_clause_number(match.group("number"), idx)

            level = self._infer_level(clause_number)

            clause = ClauseInfo(

                clause_number=clause_number,

                clause_title=self._extract_title(match),

                clause_text=clause_text or stripped_segment,

                clause_type=self._infer_clause_type(match.group("label"), clause_number, level),

                level=level,

                parent_number=self._compute_parent_number(clause_number),

                start_position=start_pos,

                end_position=end_pos,

            )

            clauses.append(clause)

        return clauses

    def split_long_clause(self, clause: ClauseInfo) -> List[str]:

        """Split clauses longer than max_clause_length at natural boundaries."""

        text = clause.clause_text or ""

        if not text:

            return []

        if len(text) <= self.max_clause_length:

            return [text.strip()]

        segments: List[str] = []

        start = 0

        text_len = len(text)

        while start < text_len:

            tentative_end = min(start + self.max_clause_length, text_len)

            split_pos = self._find_split_position(text, start, tentative_end)

            if split_pos <= start:

                split_pos = tentative_end

            segment = text[start:split_pos].strip()

            if segment:

                segments.append(segment)

            start = split_pos

        return segments or [text.strip()]

    def _normalize_text(self, text: str) -> Tuple[str, List[int]]:

        normalized_chars: List[str] = []

        mapping: List[int] = []

        for idx, char in enumerate(text):

            if char == "\r":

                continue

            normalized_chars.append(char)

            mapping.append(idx)

        return "".join(normalized_chars), mapping

    def _norm_index_to_original(self, mapping: List[int], index: int, text_len: int) -> int:

        if not mapping:

            return max(0, min(index, text_len))

        clamped = max(0, min(index, len(mapping) - 1))

        return mapping[clamped]

    def _compute_clause_end(self, mapping: List[int], end_norm: int, fallback_start: int, text_len: int) -> int:

        if end_norm <= 0:

            return fallback_start

        if not mapping:

            return min(text_len, end_norm)

        if end_norm - 1 >= len(mapping):

            return text_len

        return min(text_len, mapping[end_norm - 1] + 1)

    def _normalize_clause_number(self, raw_number: Optional[str], idx: int) -> str:

        candidate = (raw_number or "").strip()

        if not candidate:

            return str(idx + 1)

        return candidate.rstrip(".").strip()

    def _extract_title(self, match: re.Match) -> str:

        title = (match.group("title") or "").strip(" \t:-\u2014")

        return title or "Untitled"

    def _infer_level(self, clause_number: str) -> int:

        if not clause_number:

            return 1

        return max(1, len([part for part in clause_number.split(".") if part]))

    def _compute_parent_number(self, clause_number: str) -> Optional[str]:

        if not clause_number or "." not in clause_number:

            return None

        parts = [part for part in clause_number.split(".") if part]

        if len(parts) <= 1:

            return None

        return ".".join(parts[:-1])

    def _infer_clause_type(self, label: Optional[str], clause_number: str, level: int) -> str:

        if label:

            lowered = label.lower()

            if lowered in {"section", "clause", "article"}:

                return lowered

        if re.fullmatch(r"[IVXLCM]+", clause_number.upper()):

            return "article"

        return "section" if level == 1 else "clause"

    def _find_split_position(self, text: str, start: int, tentative_end: int) -> int:

        window = text[start:tentative_end]

        for delimiter in self.NATURAL_BREAKS:

            idx = window.rfind(delimiter)

            if idx > 50:

                return start + idx + len(delimiter)

        return tentative_end

    def _fallback_clause(self, text: str) -> Optional[ClauseInfo]:

        stripped = text.strip()

        if not stripped:

            return None

        start = text.find(stripped)

        end = start + len(stripped)

        return ClauseInfo(

            clause_number="1",

            clause_title="Document",

            clause_text=stripped,

            clause_type="document",

            level=1,

            parent_number=None,

            start_position=max(start, 0),

            end_position=max(end, start),

        )



class FileHasher:

    """Service for computing file hashes"""

    

    @staticmethod

    async def compute_hash(file_path: Path, algorithm: str = 'sha256') -> str:

        """Compute file hash asynchronously"""

        try:

            hash_obj = hashlib.new(algorithm)

            

            with open(file_path, 'rb') as f:

                while chunk := f.read(8192):

                    hash_obj.update(chunk)

            

            return hash_obj.hexdigest()

            

        except Exception as e:

            logger.warning(f"Hash computation failed: {e}")

            return f"no-{algorithm}:{file_path.name}"



class DatabaseService:

    """Service for database operations during ingestion"""

    

    def __init__(self, db):

        self.db = db

    

    async def ensure_indexes(self) -> None:

        """Ensure required database indexes exist"""

        try:

            # Create indexes in background

            await asyncio.gather(

                self._create_document_vectors_index(),

                self._create_ingest_jobs_index(),

                return_exceptions=True

            )

        except Exception as e:

            logger.warning(f"Index creation failed: {e}")

    

    async def _create_document_vectors_index(self) -> None:

        """Create index for document_vectors collection"""

        try:

            await self.db.document_vectors.create_index([

                ("organization_id", 1),

                ("project_id", 1),

                ("uploadType", 1),

                ("createdAt", -1)

            ], name="docvec_org_proj_type_created_idx", background=True)
            await self.db.document_vectors.create_index(
                [("text", "text"), ("clause_title", "text")],
                name="docvec_text_idx",
                background=True,
            )

        except Exception as e:

            logger.debug(f"Document vectors index creation failed: {e}")

    

    async def _create_ingest_jobs_index(self) -> None:

        """Create index for contract_ingest_jobs collection"""

        try:

            await self.db.contract_ingest_jobs.create_index([

                ("upload_id", 1)

            ], name="contract_ingest_jobs_upload_id_idx", background=True)

        except Exception as e:

            logger.debug(f"Ingest jobs index creation failed: {e}")

    

    async def upsert_job_status(

        self,

        upload_id: str,

        file_path: str,

        filename: str,

        status: str,

        categories: Optional[List[str]] = None,

        error: Optional[str] = None,

        organization_id: Optional[str] = None,

        project_id: Optional[str] = None,

        tags: Optional[List[str]] = None,

        file_size: Optional[int] = None

    ) -> None:

        """Upsert job status in database"""

        try:

            set_fields: Dict[str, Any] = {
                "upload_id": upload_id,
                "file_path": str(file_path),
                "filename": filename,
                "status": status,
                "error": error,
                "updatedAt": datetime.utcnow(),
            }

            if categories is not None:
                set_fields["categories"] = categories
            if organization_id:
                set_fields["organization_id"] = organization_id
            if project_id:
                set_fields["project_id"] = project_id
            if file_size is not None:
                set_fields["size"] = file_size
            if tags is not None:
                set_fields["tags"] = tags

            set_on_insert: Dict[str, Any] = {
                "createdAt": datetime.utcnow(),
                "saved_path": str(file_path),
            }
            if file_size is not None:
                set_on_insert.setdefault("size", file_size)
            if tags is not None:
                set_on_insert.setdefault("tags", tags)

            used_keys = set(set_fields.keys())
            await self.db.contract_ingest_jobs.update_one(

                {"upload_id": upload_id},

                {

                    "$set": set_fields,

                    "$setOnInsert": {k: v for k, v in set_on_insert.items() if k not in used_keys}

                },

                upsert=True

            )

        except Exception as e:

            logger.warning(f"Job status update failed: {e}")

    

    async def get_organization_name(self, organization_id: str) -> Optional[str]:

        """Get organization name by ID"""

        try:

            # Try string ID first

            doc = await self.db.organizations.find_one({"_id": organization_id})

            

            if not doc:

                # Try ObjectId

                from bson.objectid import ObjectId

                try:

                    doc = await self.db.organizations.find_one({"_id": ObjectId(organization_id)})

                except Exception:

                    pass

            

            return doc.get("name") or doc.get("title") if doc else None

            

        except Exception as e:

            logger.warning(f"Failed to get organization name: {e}")

            return None

    

    async def get_project_name(self, project_id: str) -> Optional[str]:

        """Get project name by ID"""

        try:

            # Try string ID first

            doc = await self.db.projects.find_one({"_id": project_id})

            

            if not doc:

                # Try ObjectId

                from bson.objectid import ObjectId

                try:

                    doc = await self.db.projects.find_one({"_id": ObjectId(project_id)})

                except Exception:

                    pass

            

            return doc.get("name") or doc.get("title") if doc else None

            

        except Exception as e:

            logger.warning(f"Failed to get project name: {e}")

            return None

    

    async def insert_document_vectors(self, records: List[Dict[str, Any]]) -> None:

        """Insert document vector records"""

        if not records:

            return

        

        try:

            await self.db.document_vectors.insert_many(records)

            logger.info(f"Inserted {len(records)} document vector records")

        except Exception as e:

            raise IngestionError(f"Failed to insert document vectors: {str(e)}")



class ContractIngestor:

    """Main service for contract document ingestion"""

    

    def __init__(self, db, config: Optional[IngestionConfig] = None):

        self.config = config or IngestionConfig()

        self.db_service = DatabaseService(db)

        self.parser = DocumentParser()

        self.clause_extractor = ClauseExtractor()

        self.vector_service = self._initialize_vector_service()

        self.hasher = FileHasher()

        self.categorizer = create_contract_categorizer(settings)



    def _initialize_vector_service(self) -> Optional[LlamaIndexVectorService]:

        vector_config = DocumentProcessingConfig()

        if not getattr(vector_config, "vector_store_enabled", True):

            logger.info("Vector store disabled; contract ingestion will skip embeddings")

            return None

        if not vector_config.openai_api_key:

            logger.warning("OpenAI API key missing; contract ingestion will skip embeddings")

            return None

        try:

            return LlamaIndexVectorService(

                mongo_uri=vector_config.mongo_uri,

                database_name=vector_config.database_name,

                collection_name=getattr(vector_config, "vector_store_collection", "document_vectors"),

                embedding_model=self.config.EMBEDDING_MODEL,

                openai_api_key=vector_config.openai_api_key,

            )

        except Exception as exc:

            logger.error("Failed to initialize LlamaIndex vector service: %s", exc)

            return None



    

    async def ingest_file(

        self,

        organization_id: str,

        project_id: Optional[str],

        file_path: str,

        filename: Optional[str] = None,

        tags: Optional[List[str]] = None,

        upload_id: Optional[str] = None

    ) -> Dict[str, Any]:

        """

        Ingest a contract file with proper error handling and logging.

        

        Args:

            organization_id: Organization identifier

            project_id: Optional project identifier

            file_path: Path to the file to ingest

            filename: Optional filename override

            tags: Optional list of tags

            upload_id: Optional upload identifier

            

        Returns:

            Dictionary with ingestion results

        """

        # Initialize variables

        upload_id = upload_id or str(uuid.uuid4())

        file_path_obj = Path(file_path).resolve()

        filename = filename or file_path_obj.name
        file_size = None
        try:
            file_size = file_path_obj.stat().st_size
        except OSError:
            pass

        

        logger.info(f"Starting ingestion for file: {filename} (upload_id: {upload_id})")

        

        # Ensure indexes

        await self.db_service.ensure_indexes()

        

        # Update job status to processing

        await self.db_service.upsert_job_status(

            upload_id,
            str(file_path_obj),
            filename,
            "processing",
            organization_id=organization_id,
            project_id=project_id,
            tags=tags or [],
            file_size=file_size,

        )

        

        try:

            # Validate file

            await self._validate_file(file_path_obj)

            

            # Compute file hash

            checksum = await self.hasher.compute_hash(file_path_obj)

            

            # Extract text

            text = await self.parser.extract_text(file_path_obj)

            

            # Extract structured clauses

            clauses = self.clause_extractor.extract_clauses(text)

            

            # Get context for categorization

            org_name, proj_name = await asyncio.gather(

                self.db_service.get_organization_name(organization_id),

                self.db_service.get_project_name(project_id) if project_id else asyncio.sleep(0, result=None)

            )

            

            # Categorize contract

            auto_categories = await self._categorize_text(text, org_name, proj_name)

            

            # Merge tags with auto-categories

            final_tags = sorted(set((tags or []) + auto_categories))

            payloads = self._build_clause_payloads(
                clauses=clauses,
                final_tags=final_tags,
                upload_id=upload_id,
                organization_id=organization_id,
                project_id=project_id,
                filename=filename,
                source_path=str(file_path_obj),
            )

            records: List[Dict[str, Any]] = []
            if not payloads:
                logger.warning("No clause payloads generated for %s", filename)
            elif self.vector_service:
                vector_results = await self.vector_service.index_chunks(payloads)
                records = self._create_vector_records(vector_results)
            else:
                logger.info(
                    "Vector service unavailable; persisting clause payloads without embeddings for %s",
                    filename,
                )
                records = self._payloads_to_records(payloads)

            if records:
                await self.db_service.insert_document_vectors(records)

            

            # Update job status to completed

            await self.db_service.upsert_job_status(

                upload_id,
                str(file_path_obj),
                filename,
                "completed",
                auto_categories,
                organization_id=organization_id,
                project_id=project_id,
                tags=final_tags,
                file_size=file_size,

            )

            

            result = {

                "ok": True,

                "upload_id": upload_id,

                "file": filename,

                "chunks": len(records),

                "checksum": checksum,

                "categories": auto_categories

            }

            

            logger.info(f"Successfully ingested {filename}: {len(records)} chunks")

            return result

            

        except Exception as e:

            logger.error(f"Ingestion failed for {filename}: {str(e)}")

            

            # Update job status to failed

            await self.db_service.upsert_job_status(

                upload_id,
                str(file_path_obj),
                filename,
                "failed",
                error=str(e),
                organization_id=organization_id,
                project_id=project_id,
                tags=tags or [],
                file_size=file_size,

            )

            

            return {

                "ok": False,

                "upload_id": upload_id,

                "file": filename,

                "error": str(e)

            }

    

    async def _validate_file(self, file_path: Path) -> None:

        """Validate file before processing"""

        if not file_path.exists():

            raise IngestionError(f"File not found: {file_path}")

        

        if not file_path.is_file():

            raise IngestionError(f"Path is not a file: {file_path}")

        

        # Check file size (optional)

        max_size = getattr(settings, 'MAX_FILE_SIZE_MB', 100) * 1024 * 1024

        if file_path.stat().st_size > max_size:

            raise IngestionError(f"File too large: {file_path.stat().st_size} bytes")

    

    async def _categorize_text(

        self, 

        text: str, 

        org_name: Optional[str], 

        proj_name: Optional[str]

    ) -> List[str]:

        """Categorize text with error handling"""

        try:

            return await self.categorizer.categorize(text, org_name, proj_name)

        except Exception as e:

            logger.warning(f"Text categorization failed: {e}")

            return []

    def _build_clause_payloads(
        self,
        clauses: List[ClauseInfo],
        final_tags: List[str],
        upload_id: str,
        organization_id: str,
        project_id: Optional[str],
        filename: str,
        source_path: str,
    ) -> List[Dict[str, Any]]:
        payloads: List[Dict[str, Any]] = []
        for clause in clauses:
            clause_chunks = self.clause_extractor.split_long_clause(clause)
            if not clause_chunks:
                continue
            is_complete = len(clause_chunks) == 1
            for chunk_index, chunk_text in enumerate(clause_chunks):
                chunk_checksum = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
                metadata = {
                    "upload_id": upload_id,
                    "document_id": upload_id,
                    "organization_id": str(organization_id),
                    "project_id": str(project_id) if project_id else None,
                    "uploadType": "contract",
                    "letterNo": None,
                    "source_file": source_path,
                    "source_filename": filename,
                    "filename": filename,
                    "chunk_index": chunk_index,
                    "tags": final_tags,
                    "checksum_sha256": chunk_checksum,
                    "source": "contracts_ingest",
                    "clause_number": clause.clause_number,
                    "clause_title": clause.clause_title,
                    "clause_type": clause.clause_type,
                    "clause_level": clause.level,
                    "parent_clause_number": clause.parent_number,
                    "is_complete_clause": is_complete,
                    "clause_start_position": clause.start_position,
                    "clause_end_position": clause.end_position,
                }
                payloads.append(
                    {
                        "text": chunk_text,
                        "metadata": metadata,
                        "checksum": chunk_checksum,
                    }
                )
        return payloads

    def _payloads_to_records(
        self,
        payloads: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Fallback path when vector service is unavailable."""
        if not payloads:
            return []
        now = datetime.utcnow()
        records: List[Dict[str, Any]] = []
        for item in payloads:
            metadata = dict(item.get("metadata") or {})
            text_chunk = item.get("text") or ""
            checksum = item.get("checksum")
            record = {
                **metadata,
                "vector_ref": None,
                "embedding_id": metadata.get("embedding_id"),
                "embedding": [],
                "embedding_dims": 0,
                "embedding_model": self.config.EMBEDDING_MODEL,
                "text": text_chunk,
                "num_tokens": len(text_chunk.split()),
                "createdAt": now,
            }
            if not record.get("checksum_sha256"):
                record["checksum_sha256"] = checksum or hashlib.sha256(
                    text_chunk.encode("utf-8")
                ).hexdigest()
            records.append(record)
        return records

    

    def _create_vector_records(
        self,
        vector_results: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Create database records for document vectors"""
        if not vector_results:
            return []

        now = datetime.utcnow()
        records: List[Dict[str, Any]] = []

        for item in vector_results:
            metadata = dict(item.get("metadata") or {})
            text_chunk = item.get("text") or ""
            embedding = item.get("embedding") or []
            record = {
                **metadata,
                "vector_ref": item.get("vector_ref"),
                "embedding_id": metadata.get("embedding_id"),
                "embedding": embedding,
                "embedding_dims": len(embedding),
                "embedding_model": self.vector_service.embedding_model_name if self.vector_service else self.config.EMBEDDING_MODEL,
                "text": text_chunk,
                "num_tokens": len(text_chunk.split()),
                "createdAt": now,
            }
            if not record.get("checksum_sha256"):
                record["checksum_sha256"] = hashlib.sha256(text_chunk.encode("utf-8")).hexdigest()
            record.setdefault("tags", metadata.get("tags", []))
            records.append(record)

        return records



# Factory function

def create_contract_ingestor(db, config: Optional[IngestionConfig] = None) -> ContractIngestor:

    """Create contract ingestor instance"""

    return ContractIngestor(db, config)



# Convenience function for backward compatibility

async def ingest_file(

    db,

    organization_id: str,

    project_id: Optional[str],

    file_path: str,

    filename: Optional[str] = None,

    tags: Optional[List[str]] = None,

    upload_id: Optional[str] = None,

) -> Dict[str, Any]:

    """Convenience function for file ingestion"""

    ingestor = create_contract_ingestor(db)

    return await ingestor.ingest_file(

        organization_id, project_id, file_path, filename, tags, upload_id

    )



def schedule_ingest(

    db,

    organization_id: str,

    project_id: Optional[str],

    file_path: str,

    filename: Optional[str],

    tags: Optional[List[str]],

    upload_id: Optional[str],

) -> None:

    """Schedule ingestion as background task"""

    try:

        task = asyncio.create_task(

            ingest_file(db, organization_id, project_id, file_path, filename, tags, upload_id)

        )

        logger.info(f"Scheduled ingestion task for {filename or file_path}")

    except Exception as e:

        logger.error(f"Failed to schedule ingestion: {e}")

