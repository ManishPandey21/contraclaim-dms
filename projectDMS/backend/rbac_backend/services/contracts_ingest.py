import asyncio
import hashlib
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import uuid
from collections import Counter
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, AsyncGenerator, Set, Sequence
import tempfile

from ..core.config import settings
from ..config.document_processing_config import DocumentProcessingConfig
from .contract_categorizer import create_contract_categorizer
from .contract_graph_service import (
    ContractGraphIdentityError,
    ClauseGraphPayload,
    ContractGraphService,
    DocumentGraphPayload,
)
from .publication_policy import is_publication_blocked, resolve_canonical_document
from ..retrieval.embeddings import EmbeddingClient
from ..retrieval.generator import LLMGenerator
from ..retrieval.source_metadata import normalize_source_payload
from ..retrieval.vector_client import VectorClient
from .usage_metering_service import UsageEventType, UsageMeteringService

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
    """Represents a complete contract clause"""
    clause_number: str  # e.g., "1.2.3" or "Article 5"
    clause_title: str   # e.g., "Payment Terms"
    clause_text: str    # Full text of the clause
    clause_type: str    # "section", "clause", "article", "paragraph"
    start_position: int # Character position in document
    end_position: int   # Character position in document
    level: int          # Hierarchy level (1 for main, 2 for sub, etc.)
    parent_number: Optional[str] = None  # Parent clause number if nested
    clause_id: Optional[str] = None  # Stable clause identifier when available
    toc_path: Optional[List[str]] = None  # Hierarchical path from Marker/LLM
    chunk_type: Optional[str] = None
    ai_chunked: bool = False
    ai_confidence: Optional[float] = None


@dataclass
class ParsedPage:
    """Lightweight representation of a page worth of text."""

    number: int
    text: str
    start: int
    end: int


@dataclass
class ParsedDocument:
    """Full document text plus page-aware spans for PDF grounding."""

    text: str
    pages: List[ParsedPage]
    file_path: str


@dataclass
class MarkerResult:
    """Outputs from Marker PDF -> Markdown extraction."""

    markdown_text: str
    markdown_path: Optional[str]
    toc: List[str]
    header_offsets: List[Dict[str, Any]]
    source: str = "marker"


@dataclass
class ClauseSpan:
    """Clause span extracted from Markdown with offsets."""

    clause_id: str
    heading: str
    path: List[str]
    start_offset: int
    end_offset: int


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

    async def extract_text(self, file_path: Path) -> ParsedDocument:
        """Extract text from document with proper error handling and page spans"""
        try:
            file_path = Path(file_path).resolve()
            if not file_path.exists():
                raise DocumentParsingError(f"File not found: {file_path}")
            if not file_path.is_file():
                raise DocumentParsingError(f"Path is not a file: {file_path}")

            extension = file_path.suffix.lower()

            # Try format-specific parsers
            if extension == '.pdf' and 'pdfminer' in self.parsers:
                parsed = await self._extract_pdf_text(file_path)
            elif extension == '.docx' and 'docx' in self.parsers:
                doc_text = await self._extract_docx_text(file_path)
                parsed = self._single_page_doc(doc_text, file_path)
            else:
                # Last resort: try to read as plain text
                text = await self._extract_plain_text(file_path)
                parsed = self._single_page_doc(text, file_path)

            if not parsed.text or not parsed.text.strip():
                raise DocumentParsingError("No text content extracted from document")

            logger.info(f"Successfully extracted text: {len(parsed.text)} characters")
            return parsed

        except DocumentParsingError:
            raise
        except Exception as e:
            raise DocumentParsingError(f"Text extraction failed: {str(e)}")

    async def _extract_pdf_text(self, file_path: Path) -> ParsedDocument:
        """Extract text from PDF using pdfminer with page spans for grounding."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._extract_pdf_text_sync, file_path)

    @staticmethod
    def _extract_pdf_text_sync(file_path: Path) -> ParsedDocument:
        """Synchronous pdfminer extraction, run through an executor by callers."""
        from pdfminer.high_level import extract_pages
        from pdfminer.layout import LTTextContainer

        page_texts: List[str] = []
        with open(file_path, "rb") as handle:
            for page_layout in extract_pages(handle):
                parts: List[str] = []
                for element in page_layout:
                    if isinstance(element, LTTextContainer):
                        parts.append(element.get_text())
                page_texts.append("\n".join(parts).strip())

        if not page_texts:
            return ParsedDocument(text="", pages=[], file_path=str(file_path))

        combined_text = "\n\n".join(page_texts)
        pages: List[ParsedPage] = []
        offset = 0
        for idx, text in enumerate(page_texts, start=1):
            start = offset
            end = start + len(text)
            pages.append(ParsedPage(number=idx, text=text, start=start, end=end))
            offset = end + 2  # account for the two newlines added between pages

        # Correct the last page end offset (no trailing separator in combined_text)
        if pages:
            pages[-1] = ParsedPage(
                number=pages[-1].number,
                text=pages[-1].text,
                start=pages[-1].start,
                end=pages[-1].start + len(pages[-1].text),
            )

        return ParsedDocument(text=combined_text, pages=pages, file_path=str(file_path))

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

    @staticmethod
    def _single_page_doc(text: str, file_path: Path) -> ParsedDocument:
        """Wrap raw text into a ParsedDocument with a single page entry."""
        return ParsedDocument(
            text=text,
            pages=[
                ParsedPage(
                    number=1,
                    text=text,
                    start=0,
                    end=len(text),
                )
            ],
            file_path=str(file_path),
        )


class ContractTextPreprocessor:
    """Clean page text before contract chunking while preserving legal meaning."""

    _PAGE_NUMBER_RE = re.compile(
        r"^(?:page\s*)?(?:[-–—]?\s*)?\d{1,5}(?:\s*(?:of|/)\s*\d{1,5})?(?:\s*[-–—]?)?$",
        re.IGNORECASE,
    )
    _CLAUSE_HEADING_RE = re.compile(
        r"^\s*(?:(?:GCC|SCC|PCC|Clause|Section|Article|Sub-Clause)\s+)?\d+(?:\.\d+){0,5}\b",
        re.IGNORECASE,
    )
    _WATERMARK_RE = re.compile(
        r"^(?:confidential|draft|sample|specimen|copy|generated\s+by|printed\s+by)\b",
        re.IGNORECASE,
    )

    def clean_document(self, parsed_doc: ParsedDocument) -> Tuple[ParsedDocument, List[Dict[str, Any]]]:
        if not parsed_doc.pages:
            return parsed_doc, []

        repeated_margin_lines = self._detect_repeated_margin_lines(parsed_doc.pages)
        cleaned_pages: List[ParsedPage] = []
        page_audit: List[Dict[str, Any]] = []
        offset = 0

        for page in parsed_doc.pages:
            cleaned_text, removed = self.clean_page_text(page.text, page.number, repeated_margin_lines)
            start = offset
            end = start + len(cleaned_text)
            cleaned_pages.append(
                ParsedPage(
                    number=page.number,
                    text=cleaned_text,
                    start=start,
                    end=end,
                )
            )
            offset = end + 2
            page_audit.append(
                {
                    "page_number": page.number,
                    "raw_text_length": len(page.text or ""),
                    "cleaned_text_length": len(cleaned_text),
                    "removed_lines": removed,
                }
            )

        combined_text = "\n\n".join(page.text for page in cleaned_pages)
        if cleaned_pages:
            last = cleaned_pages[-1]
            cleaned_pages[-1] = ParsedPage(
                number=last.number,
                text=last.text,
                start=last.start,
                end=last.start + len(last.text),
            )
        return ParsedDocument(text=combined_text, pages=cleaned_pages, file_path=parsed_doc.file_path), page_audit

    def clean_page_text(
        self,
        text: str,
        page_number: int,
        repeated_margin_lines: Set[str],
    ) -> Tuple[str, List[str]]:
        if not text:
            return "", []

        cleaned_lines: List[str] = []
        removed: List[str] = []
        previous_blank = False

        raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        line_count = len(raw_lines)
        for line_index, raw_line in enumerate(raw_lines):
            line = raw_line.rstrip()
            stripped = line.strip()
            normalized = self._normalize_line(stripped)
            in_margin = line_index < 5 or line_index >= max(0, line_count - 5)

            should_remove = False
            if stripped:
                should_remove = (
                    (in_margin and self._is_page_number(stripped, page_number))
                    or (normalized in repeated_margin_lines and not self._looks_like_clause_heading(stripped))
                    or self._looks_like_watermark(stripped)
                )

            if should_remove:
                removed.append(stripped[:160])
                continue

            if not stripped:
                if previous_blank:
                    continue
                previous_blank = True
                cleaned_lines.append("")
                continue

            previous_blank = False
            cleaned_lines.append(line)

        return "\n".join(cleaned_lines).strip(), removed[:50]

    def _detect_repeated_margin_lines(self, pages: Sequence[ParsedPage]) -> Set[str]:
        if len(pages) < 3:
            return set()

        counter: Counter[str] = Counter()
        for page in pages:
            lines = [line.strip() for line in (page.text or "").splitlines() if line.strip()]
            if not lines:
                continue
            margin_lines = lines[:5] + lines[-5:]
            seen_on_page: Set[str] = set()
            for line in margin_lines:
                normalized = self._normalize_line(line)
                if not normalized or len(normalized) < 4 or self._looks_like_clause_heading(line):
                    continue
                seen_on_page.add(normalized)
            counter.update(seen_on_page)

        threshold = max(3, int(len(pages) * 0.30))
        return {line for line, count in counter.items() if count >= threshold}

    @classmethod
    def _is_page_number(cls, line: str, page_number: int) -> bool:
        if not cls._PAGE_NUMBER_RE.match(line.strip()):
            return False
        digits = re.findall(r"\d+", line)
        if not digits:
            return False
        return str(page_number) in digits or len(line.strip()) <= 6

    @classmethod
    def _looks_like_clause_heading(cls, line: str) -> bool:
        return bool(cls._CLAUSE_HEADING_RE.match(line.strip()))

    @classmethod
    def _looks_like_watermark(cls, line: str) -> bool:
        stripped = line.strip()
        return bool(cls._WATERMARK_RE.match(stripped))

    @staticmethod
    def _normalize_line(line: str) -> str:
        lowered = re.sub(r"\s+", " ", line.strip().lower())
        lowered = re.sub(r"\b\d{1,5}\b", "#", lowered)
        return lowered.strip(" -–—|")


class MarkerService:
    """Optional Marker integration to extract Markdown from contract PDFs."""

    def __init__(self, config: DocumentProcessingConfig):
        self.config = config
        self.enabled = bool(getattr(config, "marker_enabled", False))
        if self.enabled:
            self.command_template = (getattr(config, "marker_cmd", None) or "marker").strip()
        else:
            self.command_template = ""
        output_root = getattr(config, "marker_output_dir", None)
        if not output_root:
            output_root = str(Path(config.uploads_dir) / "marker")
        self.output_root = Path(output_root).expanduser()
        try:
            self.output_root.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    def _is_available(self) -> bool:
        if not self.enabled or not self.command_template:
            return False
        cmd = shlex.split(self.command_template)
        if not cmd:
            return False
        return shutil.which(cmd[0]) is not None

    def _build_command(self, input_path: Path, output_dir: Path) -> List[str]:
        if "{input}" in self.command_template or "{output_dir}" in self.command_template:
            cmd_text = self.command_template.format(
                input=str(input_path),
                output_dir=str(output_dir),
            )
            return shlex.split(cmd_text)
        cmd = shlex.split(self.command_template)
        cmd.append(str(input_path))
        return cmd

    def _pick_markdown(self, output_dir: Path) -> Optional[Path]:
        candidates = list(output_dir.rglob("*.md"))
        if not candidates:
            return None
        try:
            return max(candidates, key=lambda p: p.stat().st_size)
        except Exception:
            return candidates[0]

    @staticmethod
    def _extract_heading_offsets(markdown_text: str) -> List[Dict[str, Any]]:
        offsets: List[Dict[str, Any]] = []
        cursor = 0
        for line in markdown_text.splitlines(keepends=True):
            stripped = line.lstrip()
            if stripped.startswith("#"):
                level = len(stripped) - len(stripped.lstrip("#"))
                heading = stripped[level:].strip()
                offsets.append(
                    {
                        "level": level,
                        "heading": heading,
                        "start_offset": cursor,
                    }
                )
            cursor += len(line)
        return offsets

    async def extract_markdown(self, input_path: Path, upload_id: str) -> Optional[MarkerResult]:
        if not input_path.exists() or input_path.suffix.lower() != ".pdf":
            return None
        if not self._is_available():
            return None

        output_dir = self.output_root / upload_id
        output_dir.mkdir(parents=True, exist_ok=True)
        cmd = self._build_command(input_path, output_dir)

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                cwd=str(output_dir),
                capture_output=True,
                text=True,
            )
        except Exception as exc:
            logger.warning("Marker execution failed: %s", exc)
            return None

        if result.returncode != 0:
            logger.warning("Marker failed (code=%s): %s", result.returncode, result.stderr)
            return None

        markdown_path = self._pick_markdown(output_dir)
        if not markdown_path:
            logger.warning("Marker output missing markdown for %s", input_path.name)
            return None

        try:
            markdown_text = markdown_path.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            logger.warning("Failed to read Marker markdown output: %s", exc)
            return None

        header_offsets = self._extract_heading_offsets(markdown_text)
        toc = [entry.get("heading") for entry in header_offsets if entry.get("heading")]

        return MarkerResult(
            markdown_text=markdown_text,
            markdown_path=str(markdown_path),
            toc=toc,
            header_offsets=header_offsets,
            source="marker",
        )


class ClauseExtractor:
    """Enhanced clause extraction that preserves complete clauses"""
    
    # Comprehensive patterns for legal document structures
    CLAUSE_PATTERNS = [
        # "CLAUSE 1.2.3 - Title" or "CLAUSE 1.2.3: Title"
        r'^\s*(CLAUSE|SECTION|ARTICLE)\s+([\d\.]+)\s*[-:]?\s*(.*)$',
        # "GCC 8.4 - Extension of Time" or "SCC 20.1: Loss and Expense"
        r'^\s*((?:GCC|SCC|PCC|Sub-Clause))\s+(\d+(?:\.\d+){0,5})\s*[-:]?\s*(.*)$',
        # Structural divisions: "SCHEDULE 1 - Title", "APPENDIX A: Title",
        # "ANNEXURE II", "PART 2 - Title" (kept as top-level structural headings).
        r'^\s*(SCHEDULE|APPENDIX|ANNEXURE|ANNEX|PART)\s+([0-9]+(?:\.[0-9]+)*|[A-Z]{1,3}|[IVXLC]+)\b\s*[-:.]?\s*(.*)$',
        # "1.2.3 Title" (numbered with title)
        r'^\s*(\d+(?:\.\d+){0,5})\s+([A-Z][\w\s,()/&\'"-]{2,})\s*$',
        # "1.2.3. Title" (with period)
        r'^\s*(\d+(?:\.\d+){0,5})\.\s+([A-Z][\w\s,()/&\'"-]{2,})\s*$',
        # Standalone numbered sub-clause. Bare integers are often page numbers,
        # so require at least one dot when there is no title on the line.
        r'^\s*(\d+(?:\.\d+){1,5})\s*$',
    ]

    # Structural divisions carry their keyword in the clause number ("Schedule 1")
    # so they never collide with a numeric clause "1".
    _STRUCTURAL_TYPES = {"SCHEDULE", "APPENDIX", "ANNEXURE", "ANNEX", "PART"}
    
    def __init__(self):
        self.patterns = [re.compile(p, re.MULTILINE | re.IGNORECASE) for p in self.CLAUSE_PATTERNS]

    @staticmethod
    def _parse_clause_match(match: re.Match[str]) -> Tuple[str, str, str]:
        groups = [str(group or "").strip() for group in match.groups()]
        if not groups:
            return "clause", "", ""

        first = groups[0].upper()
        if first in ClauseExtractor._STRUCTURAL_TYPES:
            clause_type = first.lower()
            number = groups[1] if len(groups) > 1 else ""
            clause_number = f"{first.title()} {number}".strip()
            clause_title = groups[2] if len(groups) > 2 else ""
        elif first in {"CLAUSE", "SECTION", "ARTICLE", "GCC", "SCC", "PCC", "SUB-CLAUSE"}:
            clause_type = first.lower()
            clause_number = groups[1] if len(groups) > 1 else ""
            clause_title = groups[2] if len(groups) > 2 else ""
        else:
            clause_type = "clause"
            clause_number = groups[0]
            clause_title = groups[1] if len(groups) > 1 else ""

        clause_number = clause_number.rstrip(".")
        return clause_type, clause_number, clause_title
    
    def extract_clauses(self, text: str) -> List[ClauseInfo]:
        """
        Extract complete clauses from contract text.
        Returns list of ClauseInfo objects with complete clause content.
        """
        if not text or not text.strip():
            return []
        
        # Normalize line endings
        normalized_text = text.replace('\r\n', '\n').replace('\r', '\n')
        lines = normalized_text.split('\n')
        
        clause_markers = []  # List of (line_idx, clause_number, clause_title, clause_type)
        
        # First pass: identify all clause headers
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            
            for pattern in self.patterns:
                match = pattern.match(line)
                if match:
                    clause_type, clause_number, clause_title = self._parse_clause_match(match)
                    if not clause_number:
                        continue
                    if self._looks_like_false_positive_heading(stripped, clause_number, clause_title):
                        continue
                    clause_markers.append((idx, clause_number, clause_title, clause_type))
                    break
        
        if not clause_markers:
            # No clauses found, treat entire text as single section
            return [ClauseInfo(
                clause_number="1",
                clause_title="Complete Document",
                clause_text=text,
                clause_type="document",
                start_position=0,
                end_position=len(text),
                level=1
            )]
        
        # Second pass: extract complete clause content
        clauses = []
        
        for i, (line_idx, clause_number, clause_title, clause_type) in enumerate(clause_markers):
            # Find start position
            start_line = line_idx
            start_pos = sum(len(lines[j]) + 1 for j in range(start_line))  # +1 for newline
            
            # Find end position (start of next clause or end of document)
            if i < len(clause_markers) - 1:
                end_line = clause_markers[i + 1][0]
            else:
                end_line = len(lines)
            
            # Extract complete clause text
            clause_lines = lines[start_line:end_line]
            clause_text = '\n'.join(clause_lines).strip()
            
            # Calculate hierarchy level based on clause numbering
            level = clause_number.count('.') + 1 if '.' in clause_number else 1
            
            # Determine parent clause number
            parent_number = None
            if '.' in clause_number:
                parent_number = '.'.join(clause_number.split('.')[:-1])
            
            clause_info = ClauseInfo(
                clause_number=clause_number,
                clause_title=clause_title or f"{clause_type.title()} {clause_number}",
                clause_text=clause_text,
                clause_type=clause_type.lower(),
                start_position=start_pos,
                end_position=start_pos + len(clause_text),
                level=level,
                parent_number=parent_number
            )
            
            clauses.append(clause_info)
        
        return clauses

    @staticmethod
    def _looks_like_false_positive_heading(line: str, clause_number: str, clause_title: str) -> bool:
        if not line:
            return True
        if ContractTextPreprocessor._PAGE_NUMBER_RE.match(line) and "." not in clause_number and not clause_title:
            return True
        if re.fullmatch(r"\d{1,5}", line.strip()) and "." not in clause_number:
            return True
        if re.search(r"\.{5,}\s*\d{1,5}\s*$", line):
            return True
        title = (clause_title or "").strip()
        if title and not re.search(r"[A-Za-z]", title):
            return True
        # Very high bare integer headings in contracts are usually page numbers
        # or table artifacts unless explicitly prefixed as a clause/section.
        if "." not in clause_number and clause_number.isdigit() and int(clause_number) > 80:
            return True
        return False
    
    def split_long_clause(self, clause: ClauseInfo, max_length: int = 6000) -> List[Dict[str, Any]]:
        """
        Split a long clause intelligently at paragraph or sentence boundaries.
        Returns list of clause chunks that maintain context.
        """
        if len(clause.clause_text) <= max_length:
            return [{
                'text': clause.clause_text,
                'clause_number': clause.clause_number,
                'clause_title': clause.clause_title,
                'chunk_index': 0,
                'is_complete': True
            }]
        
        # Split at paragraph boundaries first
        paragraphs = clause.clause_text.split('\n\n')
        chunks = []
        current_chunk = []
        current_length = 0
        chunk_index = 0
        
        header = f"{clause.clause_type.upper()} {clause.clause_number}"
        if clause.clause_title:
            header += f": {clause.clause_title}"
        header += "\n\n"
        
        for para in paragraphs:
            para_len = len(para)
            
            # If single paragraph exceeds max_length, split at sentences
            if para_len > max_length:
                sentences = re.split(r'([.!?]\s+)', para)
                for sent in sentences:
                    if current_length + len(sent) > max_length and current_chunk:
                        chunk_text = header + '\n\n'.join(current_chunk)
                        chunks.append({
                            'text': chunk_text,
                            'clause_number': clause.clause_number,
                            'clause_title': clause.clause_title,
                            'chunk_index': chunk_index,
                            'is_complete': False
                        })
                        current_chunk = []
                        current_length = len(header)
                        chunk_index += 1
                    
                    current_chunk.append(sent)
                    current_length += len(sent)
            
            elif current_length + para_len > max_length and current_chunk:
                chunk_text = header + '\n\n'.join(current_chunk)
                chunks.append({
                    'text': chunk_text,
                    'clause_number': clause.clause_number,
                    'clause_title': clause.clause_title,
                    'chunk_index': chunk_index,
                    'is_complete': False
                })
                current_chunk = [para]
                current_length = len(header) + para_len
                chunk_index += 1
            else:
                current_chunk.append(para)
                current_length += para_len
        
        # Save remaining chunk
        if current_chunk:
            chunk_text = header + '\n\n'.join(current_chunk)
            chunks.append({
                'text': chunk_text,
                'clause_number': clause.clause_number,
                'clause_title': clause.clause_title,
                'chunk_index': chunk_index,
                'is_complete': len(chunks) == 0
            })
        
        return chunks


class ClauseExtractionWorker:
    """LLM-backed clause span extraction for Marker markdown."""

    def __init__(
        self,
        generator: LLMGenerator,
        enabled: bool,
        max_chars: int,
        model: Optional[str] = None,
    ):
        self.generator = generator
        self.enabled = enabled
        self.max_chars = max_chars
        self.model = model

    async def extract_spans(self, markdown_text: str) -> List[ClauseSpan]:
        if not self.enabled or not markdown_text:
            return []
        if self.max_chars and len(markdown_text) > self.max_chars:
            logger.info("Clause extraction skipped: markdown length %s exceeds limit %s", len(markdown_text), self.max_chars)
            return []

        prompt = (
            "Extract clause spans from the contract markdown below.\n"
            "Return a JSON array of objects with fields:\n"
            "clause_id, heading, path, start_offset, end_offset.\n"
            "Offsets are 0-based character positions in the given markdown string.\n"
            "Use path as a list of headings from top-level to the clause heading.\n"
            "Return JSON only.\n\n"
            "MARKDOWN:\n"
            f"{markdown_text}\n"
        )

        raw = await self.generator.generate(prompt, max_tokens=1400, model=self.model)
        data = self._parse_json_array(raw)
        spans: List[ClauseSpan] = []
        for item in data:
            heading = str(item.get("heading") or item.get("title") or "").strip()
            if not heading:
                continue
            start_offset = self._coerce_int(item.get("start_offset"))
            end_offset = self._coerce_int(item.get("end_offset"))
            if start_offset is None or end_offset is None or end_offset <= start_offset:
                continue
            path = self._normalize_path(item.get("path"), heading)
            clause_id = str(item.get("clause_id") or f"{heading}:{start_offset}").strip()
            spans.append(
                ClauseSpan(
                    clause_id=clause_id,
                    heading=heading,
                    path=path,
                    start_offset=start_offset,
                    end_offset=end_offset,
                )
            )
        return spans

    @staticmethod
    def _normalize_path(value: Any, heading: str) -> List[str]:
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        if isinstance(value, str):
            parts = [p.strip() for p in value.split(">") if p.strip()]
            return parts if parts else [heading]
        return [heading]

    @staticmethod
    def _coerce_int(value: Any) -> Optional[int]:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_json_array(raw: str) -> List[Dict[str, Any]]:
        if not raw:
            return []
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                return [item for item in data if isinstance(item, dict)]
            if isinstance(data, dict):
                items = data.get("clauses") or data.get("items") or []
                if isinstance(items, list):
                    return [item for item in items if isinstance(item, dict)]
        except json.JSONDecodeError:
            pass

        start = raw.find("[")
        end = raw.rfind("]")
        if start != -1 and end != -1 and end > start:
            try:
                data = json.loads(raw[start : end + 1])
                if isinstance(data, list):
                    return [item for item in data if isinstance(item, dict)]
            except Exception:
                return []
        return []


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
                self._create_ocr_status_indexes(),
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
            await self.db.document_vectors.create_index(
                [
                    ("uploadType", 1),
                    ("organization_id", 1),
                    ("project_id", 1),
                    ("document_id", 1),
                    ("clause_number", 1),
                    ("clause_start_position", 1),
                    ("chunk_index", 1),
                ],
                name="docvec_contract_clause_lookup_idx",
                background=True,
            )
            await self.db.document_vectors.create_index(
                [("chunk_id", 1)],
                name="docvec_chunk_id_idx",
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

    async def _create_ocr_status_indexes(self) -> None:
        """Create indexes for page/batch OCR audit collections."""
        try:
            await self.db.contract_ocr_batches.create_index(
                [("document_id", 1), ("page_start", 1), ("page_end", 1)],
                name="contract_ocr_batches_doc_pages_idx",
                background=True,
            )
            await self.db.contract_ocr_batches.create_index(
                [("upload_id", 1), ("status", 1)],
                name="contract_ocr_batches_upload_status_idx",
                background=True,
            )
            await self.db.contract_ocr_pages.create_index(
                [("document_id", 1), ("page_number", 1)],
                name="contract_ocr_pages_doc_page_idx",
                unique=True,
                background=True,
            )
            await self.db.contract_ocr_pages.create_index(
                [("upload_id", 1), ("status", 1)],
                name="contract_ocr_pages_upload_status_idx",
                background=True,
            )
        except Exception as e:
            logger.debug(f"OCR status index creation failed: {e}")

    async def upsert_ocr_batch(
        self,
        *,
        document_id: str,
        upload_id: str,
        organization_id: str,
        project_id: Optional[str],
        page_start: int,
        page_end: int,
        status: str,
        error: Optional[str] = None,
        retry_count: int = 0,
    ) -> str:
        batch_id = f"{document_id}:{page_start}-{page_end}"
        now = datetime.utcnow()
        await self.db.contract_ocr_batches.update_one(
            {"batch_id": batch_id},
            {
                "$set": {
                    "batch_id": batch_id,
                    "document_id": document_id,
                    "upload_id": upload_id,
                    "organization_id": str(organization_id),
                    "project_id": str(project_id) if project_id else None,
                    "page_start": page_start,
                    "page_end": page_end,
                    "status": status,
                    "error": error,
                    "retry_count": retry_count,
                    "updatedAt": now,
                    "completed_at": now if status in {"completed", "failed", "skipped"} else None,
                },
                "$setOnInsert": {"createdAt": now, "started_at": now},
            },
            upsert=True,
        )
        return batch_id

    async def upsert_ocr_pages(self, page_records: List[Dict[str, Any]]) -> None:
        if not page_records:
            return
        now = datetime.utcnow()
        for record in page_records:
            document_id = str(record.get("document_id") or "")
            page_number = int(record.get("page_number") or 0)
            if not document_id or page_number <= 0:
                continue
            update_doc = {
                **record,
                "document_id": document_id,
                "page_number": page_number,
                "updatedAt": now,
            }
            await self.db.contract_ocr_pages.update_one(
                {"document_id": document_id, "page_number": page_number},
                {"$set": update_doc, "$setOnInsert": {"createdAt": now}},
                upsert=True,
            )

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
        file_size: Optional[int] = None,
        extra: Optional[Dict[str, Any]] = None,
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
            if extra:
                for key, value in extra.items():
                    if value is None or key in set_fields:
                        continue
                    set_fields[key] = value

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
            contract_document_ids = sorted(
                {
                    str(record.get("document_id"))
                    for record in records
                    if record.get("document_id") and record.get("uploadType") == "contract"
                }
            )
            if contract_document_ids:
                await self.db.document_vectors.delete_many(
                    {
                        "uploadType": "contract",
                        "document_id": {"$in": contract_document_ids},
                    }
                )
            await self.db.document_vectors.insert_many(records)
            logger.info(f"Inserted {len(records)} document vector records")
        except Exception as e:
            raise IngestionError(f"Failed to insert document vectors: {str(e)}")


class ContractIngestor:
    """Main service for contract document ingestion"""

    def __init__(self, db, config: Optional[IngestionConfig] = None):
        self.config = config or IngestionConfig()
        self.processing_config = DocumentProcessingConfig()
        # Keep the embedding model used for ingestion aligned with the global processing config/env
        self.config.EMBEDDING_MODEL = self.processing_config.openai_embedding_model
        self.db_service = DatabaseService(db)
        self.parser = DocumentParser()
        self.text_preprocessor = ContractTextPreprocessor()
        self.clause_extractor = ClauseExtractor()
        self.vector_service = self._initialize_vector_service(self.processing_config)
        self.marker_service = MarkerService(self.processing_config)
        clause_enabled = bool(
            self.processing_config.clause_extraction_enabled
            and self.processing_config.ai_enabled
            and self.processing_config.openai_api_key
        )
        self.clause_worker = ClauseExtractionWorker(
            LLMGenerator(self.processing_config),
            enabled=clause_enabled,
            max_chars=self.processing_config.clause_extraction_max_chars,
            model=self.processing_config.clause_extraction_model,
        )
        self.embedding_client = EmbeddingClient(self.processing_config)
        self.vector_client = VectorClient(self.processing_config)
        self.hasher = FileHasher()
        self.categorizer = create_contract_categorizer(settings)
        self.contract_graph = ContractGraphService()
        self.usage_metering_service = UsageMeteringService(db)
        self._indexes_ready = False

    def _initialize_vector_service(
        self, vector_config: Optional[DocumentProcessingConfig] = None
    ) -> Optional[LlamaIndexVectorService]:
        vector_config = vector_config or DocumentProcessingConfig()
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

    async def _extract_and_prepare_document(
        self,
        file_path: Path,
        *,
        upload_id: str,
        document_id: str,
        organization_id: str,
        project_id: Optional[str],
        filename: str,
        final_tags: List[str],
        file_size: Optional[int],
        retry_ocr_pages: Optional[List[int]] = None,
    ) -> Tuple[ParsedDocument, Dict[str, Any]]:
        if file_path.suffix.lower() != ".pdf":
            raw_doc = await self.parser.extract_text(file_path)
            cleaned_doc, page_audit = (
                self.text_preprocessor.clean_document(raw_doc)
                if self.processing_config.contract_text_cleaning_enabled
                else (raw_doc, [])
            )
            return cleaned_doc, {
                "ocr_required": False,
                "ocr_pages_total": len(cleaned_doc.pages),
                "ocr_failed_pages": [],
                "page_cleaning": page_audit,
            }

        await self.db_service.upsert_job_status(
            upload_id,
            str(file_path),
            filename,
            "processing",
            organization_id=organization_id,
            project_id=project_id,
            tags=final_tags,
            file_size=file_size,
            extra={
                "processing_stage": "ocr",
                "stage_label": "Extracting PDF pages and OCR batches",
                "progress": 25,
            },
        )

        raw_doc, page_records = await self._extract_pdf_pages_with_ocr_batches(
            file_path,
            upload_id=upload_id,
            document_id=document_id,
            organization_id=organization_id,
            project_id=project_id,
            retry_ocr_pages=retry_ocr_pages,
        )
        cleaned_doc, page_audit = (
            self.text_preprocessor.clean_document(raw_doc)
            if self.processing_config.contract_text_cleaning_enabled
            else (raw_doc, [])
        )
        cleaned_by_page = {page.number: page.text for page in cleaned_doc.pages}
        audit_by_page = {item.get("page_number"): item for item in page_audit}
        for record in page_records:
            page_number = int(record.get("page_number") or 0)
            record["cleaned_text"] = cleaned_by_page.get(page_number, "")
            record["cleaned_text_length"] = len(record["cleaned_text"])
            record["cleaning"] = audit_by_page.get(page_number, {})
            record["source_pdf_page_link"] = self._source_pdf_page_link(document_id, page_number)
        await self.db_service.upsert_ocr_pages(page_records)

        summary = self._summarize_ocr_records(page_records)
        summary["page_cleaning"] = page_audit
        await self.db_service.upsert_job_status(
            upload_id,
            str(file_path),
            filename,
            "processing",
            organization_id=organization_id,
            project_id=project_id,
            tags=final_tags,
            file_size=file_size,
            extra={
                "processing_stage": "parsing",
                "stage_label": "Cleaned OCR text for clause extraction",
                "progress": 40,
                **summary,
            },
        )
        return cleaned_doc, summary

    async def _extract_pdf_pages_with_ocr_batches(
        self,
        file_path: Path,
        *,
        upload_id: str,
        document_id: str,
        organization_id: str,
        project_id: Optional[str],
        retry_ocr_pages: Optional[List[int]] = None,
    ) -> Tuple[ParsedDocument, List[Dict[str, Any]]]:
        """Delegate page-level extraction to the shared engine.

        Behaviour is unchanged: the same per-page threshold, the same
        contiguous batching, the same OCRmyPDF invocation, the same
        contract_ocr_pages/contract_ocr_batches records. What moved is where
        that logic lives - the engine is now shared with the general document
        path, and everything contract-specific (upload_id, the collections,
        usage metering) stays here behind the PageStore seam.
        """
        from .extraction.engine import PageExtractionEngine
        from .extraction.models import PageExtractionPolicy
        from .extraction.ocrmypdf_runner import OcrMyPdfRunner
        from .extraction_adapters.contract_page_store import ContractPageStore

        async def _meter(
            *, page_count: int, page_numbers: Sequence[int], retry: bool
        ) -> None:
            await self.usage_metering_service.check_and_record(
                event_type=UsageEventType.OCR_PAGE,
                organization_id=organization_id,
                project_id=project_id,
                quantity=page_count,
                metadata={
                    "operation": "contract_ocr",
                    "document_id": document_id,
                    "upload_id": upload_id,
                    "page_numbers": list(page_numbers),
                    "retry": retry,
                },
            )

        store = ContractPageStore(
            db_service=self.db_service,
            document_id=document_id,
            upload_id=upload_id,
            organization_id=organization_id,
            project_id=project_id,
        )
        engine = PageExtractionEngine(
            policy=PageExtractionPolicy(
                ocr_enabled=self.processing_config.ocr_enabled,
                min_text_chars_per_page=max(
                    0, int(self.processing_config.contract_ocr_min_text_chars_per_page)
                ),
                batch_size=max(1, int(self.processing_config.contract_ocr_batch_size)),
                # Contracts have no attempt boundary today; preserved as-is so
                # this refactor changes no behaviour.
                max_ocr_pages_per_attempt=0,
                ocr_language=self.processing_config.ocr_language,
            ),
            ocr_runner=OcrMyPdfRunner(
                work_dir=BASE_UPLOAD_PATH / "ocr_batches" / upload_id
            ),
            store=store,
            meter=_meter,
        )

        result = await engine.extract(file_path, retry_pages=retry_ocr_pages)
        parsed = self._combine_pages(
            [
                ParsedPage(number=page.number, text=page.text, start=0, end=0)
                for page in result.pages
            ],
            file_path,
        )
        return parsed, store.records

    @staticmethod
    def _combine_pages(pages: Sequence[ParsedPage], file_path: Path) -> ParsedDocument:
        combined_text = "\n\n".join(page.text or "" for page in pages)
        combined_pages: List[ParsedPage] = []
        offset = 0
        for page in pages:
            text = page.text or ""
            start = offset
            end = start + len(text)
            combined_pages.append(ParsedPage(number=page.number, text=text, start=start, end=end))
            offset = end + 2
        if combined_pages:
            last = combined_pages[-1]
            combined_pages[-1] = ParsedPage(
                number=last.number,
                text=last.text,
                start=last.start,
                end=last.start + len(last.text),
            )
        return ParsedDocument(text=combined_text, pages=combined_pages, file_path=str(file_path))

    @staticmethod
    def _source_pdf_page_link(document_id: str, page_number: int) -> str:
        return f"contract:{document_id}#page={page_number}"

    @staticmethod
    def _summarize_ocr_records(page_records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
        failed = [
            int(record.get("page_number") or 0)
            for record in page_records
            if str(record.get("status") or "").endswith("failed")
        ]
        batch_ids = {record.get("batch_id") for record in page_records if record.get("batch_id")}
        statuses = Counter(str(record.get("status") or "unknown") for record in page_records)
        return {
            "ocr_pages_total": len(page_records),
            "ocr_failed_pages": [page for page in failed if page > 0],
            "ocr_batches_total": len(batch_ids),
            "ocr_page_status_counts": dict(statuses),
        }

    async def ingest_file(
        self,
        organization_id: str,
        project_id: Optional[str],
        file_path: str,
        filename: Optional[str] = None,
        tags: Optional[List[str]] = None,
        upload_id: Optional[str] = None,
        document_id: Optional[str] = None,
        retry_ocr_pages: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """
        Ingest a contract file with proper error handling and logging.
        Uses intelligent clause extraction instead of arbitrary chunking.
        """
        try:
            if not self._indexes_ready:
                await self.db_service.ensure_indexes()
                self._indexes_ready = True

            file_path_obj = Path(file_path).resolve()
            await self._validate_file(file_path_obj)

            if not filename:
                filename = file_path_obj.name

            if not upload_id:
                upload_id = str(uuid.uuid4())
            if not document_id:
                document_id = upload_id

            final_tags = tags or []

            # Get file size
            file_size = None
            try:
                file_size = file_path_obj.stat().st_size
            except OSError:
                pass

            await self._assert_current_publication_authority(document_id)

            logger.info(f"Starting ingestion for {filename} (upload_id: {upload_id})")

            # Update job status to processing
            await self.db_service.upsert_job_status(
                upload_id,
                str(file_path_obj),
                filename,
                "processing",
                organization_id=organization_id,
                project_id=project_id,
                tags=final_tags,
                file_size=file_size,
                extra={
                    "processing_stage": "parsing",
                    "stage_label": "Extracting text",
                    "progress": 40,
                },
            )

            # Extract text from document. For PDFs, this is page/batch aware:
            # raw page text is kept in contract_ocr_pages, while the cleaned
            # ParsedDocument drives clause chunking and embeddings.
            parsed_doc, ocr_summary = await self._extract_and_prepare_document(
                file_path_obj,
                upload_id=upload_id,
                document_id=document_id,
                organization_id=organization_id,
                project_id=project_id,
                filename=filename,
                final_tags=final_tags,
                file_size=file_size,
                retry_ocr_pages=retry_ocr_pages,
            )
            text = parsed_doc.text
            logger.info(f"Extracted {len(text)} characters from {filename}")
            if not text or not text.strip():
                failed_pages = ocr_summary.get("ocr_failed_pages") or []
                hint = f"; failed OCR pages: {failed_pages}" if failed_pages else ""
                raise DocumentParsingError(f"No usable cleaned text extracted from document{hint}")

            # Get organization and project names for categorization
            org_name = await self.db_service.get_organization_name(organization_id)
            proj_name = await self.db_service.get_project_name(project_id) if project_id else None

            # Categorize document
            categories = await self._categorize_text(text, org_name, proj_name)
            logger.info(f"Categorization complete: {len(categories)} categories")
            await self.db_service.upsert_job_status(
                upload_id,
                str(file_path_obj),
                filename,
                "processing",
                organization_id=organization_id,
                project_id=project_id,
                tags=final_tags,
                file_size=file_size,
                extra={
                    "processing_stage": "categorization",
                    "stage_label": "Categorizing contract",
                    "progress": 50,
                },
            )

            # PHASE 1: MARKER + CLAUSE EXTRACTION
            marker_result: Optional[MarkerResult] = None
            markdown_text = text
            clause_source = "contracts_ingest_regex"
            map_pages = True

            try:
                marker_result = await self.marker_service.extract_markdown(file_path_obj, upload_id)
            except Exception as exc:
                logger.warning("Marker extraction failed: %s", exc)
                marker_result = None

            if marker_result and marker_result.markdown_text:
                # Marker remains useful for headings/TOC, but chunking uses
                # cleaned text to keep embeddings free of repeated OCR noise.
                await self.db_service.upsert_job_status(
                    upload_id,
                    str(file_path_obj),
                    filename,
                    "processing",
                    organization_id=organization_id,
                    project_id=project_id,
                    tags=final_tags,
                    file_size=file_size,
                    extra={
                        "marker_markdown_path": marker_result.markdown_path,
                        "marker_toc": marker_result.toc,
                        "marker_headings": marker_result.header_offsets,
                        "marker_source": marker_result.source,
                    },
                )

            clause_spans: List[ClauseSpan] = []
            ai_confidence: Optional[float] = None
            if (
                self.processing_config.contract_ai_chunking_enabled
                and self.clause_worker.enabled
            ):
                clause_spans = await self.clause_worker.extract_spans(markdown_text)
                clause_spans, ai_confidence = self._validate_ai_clause_spans(clause_spans, markdown_text)

            if clause_spans:
                clauses = self._spans_to_clauses(
                    clause_spans,
                    markdown_text,
                    ai_chunked=True,
                    ai_confidence=ai_confidence,
                )
                clause_source = "contracts_ingest_ai_clause_chunks"
                map_pages = True
            else:
                clauses = self.clause_extractor.extract_clauses(text)
                clause_source = "contracts_ingest_regex"
                map_pages = True

            logger.info("Extracted %s clauses from %s (source=%s)", len(clauses), filename, clause_source)
            await self.db_service.upsert_job_status(
                upload_id,
                str(file_path_obj),
                filename,
                "processing",
                organization_id=organization_id,
                project_id=project_id,
                tags=final_tags,
                file_size=file_size,
                extra={
                    "processing_stage": "clause_extraction",
                    "stage_label": f"Extracted {len(clauses)} clauses",
                    "progress": 65,
                },
            )

            # Build payloads with clause metadata
            payloads = self._build_clause_payloads(
                parsed_doc,
                clauses,
                final_tags,
                upload_id,
                document_id,
                organization_id,
                project_id,
                filename,
                str(file_path_obj),
                map_pages=map_pages,
                clause_source=clause_source,
            )
            logger.info(f"Built {len(payloads)} payloads from {len(clauses)} clauses")
            await self.db_service.upsert_job_status(
                upload_id,
                str(file_path_obj),
                filename,
                "processing",
                organization_id=organization_id,
                project_id=project_id,
                tags=final_tags,
                file_size=file_size,
                extra={
                    "processing_stage": "embedding",
                    "stage_label": f"Embedding {len(payloads)} clause segments",
                    "progress": 75,
                },
            )

            # Process payloads through vector service if available
            vector_results: Optional[List[Dict[str, Any]]] = None
            if self.vector_service:
                try:
                    vector_results = await self.vector_service.index_chunks(payloads, persist=False)
                    records = self._create_vector_records(vector_results)
                except Exception as e:
                    logger.warning(f"Vector embedding failed, using fallback: {e}")
                    records = self._payloads_to_records(payloads)
            else:
                # Fallback: create records without embeddings
                records = self._payloads_to_records(payloads)

            qdrant_chunks = await self._index_clause_vectors(
                payloads,
                vector_results,
                document_id=document_id,
            )
            await self.db_service.upsert_job_status(
                upload_id,
                str(file_path_obj),
                filename,
                "processing",
                organization_id=organization_id,
                project_id=project_id,
                tags=final_tags,
                file_size=file_size,
                extra={
                    "processing_stage": "vector_storage",
                    "stage_label": "Writing vector records",
                    "progress": 90,
                    "qdrant_chunks": qdrant_chunks,
                },
            )

            # Graph sync (FalkorDB) - organization/project scoped
            graph_skipped_reason: Optional[str] = None
            if self.contract_graph.enabled:
                try:
                    section_type, priority = self._detect_section_type(filename)
                    clause_nodes = self._build_clause_graph_nodes(payloads, section_type, priority)
                    doc_payload = DocumentGraphPayload(
                        doc_id=document_id,
                        title=filename,
                        version=None,
                        organization_id=str(organization_id),
                        project_id=str(project_id) if project_id else None,
                        section_type=section_type,
                        priority=priority,
                    )
                    if clause_nodes:
                        await self._assert_current_publication_authority(document_id)
                        self.contract_graph.upsert_contract_graph(doc_payload, clause_nodes)
                    else:
                        graph_skipped_reason = "no_clause_nodes"
                except ContractGraphIdentityError as exc:
                    # Expected domain state, NOT an outage: the contract graph is
                    # project-scoped and this document has no project. Recorded
                    # so "completed" never implies "present in the graph" - the
                    # previous code logged this at the same level as a transport
                    # failure, so an entire class of documents was silently
                    # absent from every graph-backed feature.
                    graph_skipped_reason = "not_representable: missing project scope"
                    logger.info("Contract graph skipped for %s: %s", filename, exc)
                except Exception as exc:
                    graph_skipped_reason = f"graph_error: {type(exc).__name__}"
                    logger.warning("Contract graph ingestion skipped for %s: %s", filename, exc)

            # Insert into database
            await self._assert_current_publication_authority(document_id)
            await self.db_service.insert_document_vectors(records)

            # Guard against silently completing a contract whose semantic
            # vectors never persisted. When Qdrant dual-write is enabled and we
            # produced clause payloads, writing zero vectors means the vector
            # backend was unreachable (e.g. the 401 auth failure): the document
            # would be invisible to contract QA / vector search. Fail loudly so
            # the job is marked "failed" and can be re-indexed, instead of
            # reporting "completed" for an unsearchable document.
            if self.processing_config.qdrant_enabled and payloads and not qdrant_chunks:
                raise IngestionError(
                    f"Vector indexing wrote 0 of {len(payloads)} clause chunks for "
                    f"{filename} (document_id={document_id}). Qdrant dual-write is "
                    "enabled but the vector store was unreachable, so the contract "
                    "would not be searchable. Refusing to mark it completed."
                )

            # Update job status to completed
            await self.db_service.upsert_job_status(
                upload_id,
                str(file_path_obj),
                filename,
                "completed",
                categories=categories,
                organization_id=organization_id,
                project_id=project_id,
                tags=final_tags,
                file_size=file_size,
                extra={
                    "qdrant_chunks": qdrant_chunks,
                    "graph_indexed": graph_skipped_reason is None,
                    "graph_skipped_reason": graph_skipped_reason,
                    "processing_stage": "completed",
                    "stage_label": "Processing complete",
                    "progress": 100,
                },
            )

            logger.info(f"Successfully ingested {filename}")

            return {
                "ok": True,
                "upload_id": upload_id,
                "document_id": document_id,
                "file": filename,
                "clauses": len(clauses),
                "chunks": len(records),
                "categories": categories,
                "qdrant_chunks": qdrant_chunks,
            }

        except DocumentParsingError as e:
            logger.error(f"Document parsing failed for {filename}: {str(e)}")
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
                "document_id": document_id,
                "file": filename,
                "error": str(e)
            }
        except IngestionError as e:
            logger.error(f"Ingestion failed for {filename}: {str(e)}")
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
                "document_id": document_id,
                "file": filename,
                "error": str(e)
            }
        except Exception as e:
            logger.error(f"Unexpected error during ingestion of {filename}: {str(e)}")
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

    @staticmethod
    def _detect_section_type(filename: Optional[str]) -> tuple[Optional[str], int]:
        """Lightweight section-type guess based on filename."""
        if not filename:
            return None, 5
        upper_name = filename.upper()
        if "SCC" in upper_name:
            return "SCC", 1
        if "GCC" in upper_name:
            return "GCC", 2
        return None, 5

    def _build_clause_graph_nodes(
        self,
        payloads: List[Dict[str, Any]],
        section_type: Optional[str],
        priority: int,
    ) -> List[ClauseGraphPayload]:
        """Collect unique clauses for graph upsert (first chunk per clause)."""
        seen: Dict[str, ClauseGraphPayload] = {}
        for item in payloads:
            metadata = item.get("metadata") or {}
            clause_id = metadata.get("clause_id") or metadata.get("clause_number")
            if not clause_id:
                continue
            if clause_id in seen:
                continue
            clause_number = metadata.get("clause_number") or clause_id
            seen[clause_id] = ClauseGraphPayload(
                clause_id=str(clause_id),
                clause_number=str(clause_number),
                title=metadata.get("clause_title"),
                text_content=item.get("text"),
                page_number=metadata.get("page_number"),
                section_type=section_type,
                priority=priority,
                is_active=True,
            )
        return list(seen.values())

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

    def _map_pages_for_clause(self, clause: ClauseInfo, parsed_doc: ParsedDocument) -> List[int]:
        """Return page numbers (1-based) that overlap this clause span."""
        if not parsed_doc.pages:
            return []
        span_start = clause.start_position
        span_end = clause.end_position
        pages: List[int] = []
        for page in parsed_doc.pages:
            if span_start <= page.end and span_end >= page.start:
                pages.append(page.number)
        return pages

    @staticmethod
    def _extract_clause_tags(text: str) -> List[str]:
        """Pull out structured clause tags like GCC 8.3 or SCC 13.2 when present."""
        if not text:
            return []
        pattern = re.compile(r"\b(?:GCC|SCC|PCC?|Clause|Article)\s*\d+(?:\.\d+)*", re.IGNORECASE)
        found: Set[str] = set()
        for match in pattern.findall(text):
            normalized = match.strip()
            if normalized:
                found.add(normalized)
        return sorted(found)

    @staticmethod
    def _infer_clause_number(heading: str) -> Optional[str]:
        if not heading:
            return None
        match = re.search(r"\b(?:Clause|Section|Article)\s+([0-9A-Za-z.\-()]+)", heading, re.IGNORECASE)
        if match:
            return match.group(1)
        match = re.search(r"\b(\d+(?:\.\d+){0,4})\b", heading)
        if match:
            return match.group(1)
        return None

    @staticmethod
    def _infer_clause_type(heading: str) -> str:
        if not heading:
            return "clause"
        upper = heading.strip().upper()
        if upper.startswith("SECTION"):
            return "section"
        if upper.startswith("ARTICLE"):
            return "article"
        if upper.startswith("CLAUSE"):
            return "clause"
        return "clause"

    def _validate_ai_clause_spans(
        self,
        spans: List[ClauseSpan],
        source_text: str,
    ) -> Tuple[List[ClauseSpan], Optional[float]]:
        if not spans or not source_text:
            return [], None

        ordered = sorted(spans, key=lambda span: (span.start_offset, span.end_offset))
        valid: List[ClauseSpan] = []
        cursor = 0
        covered = 0
        for span in ordered:
            start = int(span.start_offset)
            end = int(span.end_offset)
            if start < 0 or end > len(source_text) or end <= start:
                continue
            if start < cursor:
                logger.info("AI clause chunking rejected overlapping span: %s", span.heading)
                return [], None
            clause_text = source_text[start:end].strip()
            if not clause_text:
                continue
            valid.append(span)
            covered += len(re.sub(r"\s+", "", clause_text))
            cursor = end

        if not valid:
            return [], None

        source_chars = len(re.sub(r"\s+", "", source_text))
        if source_chars <= 0:
            return [], None
        coverage = min(1.0, covered / source_chars)
        confidence = round(coverage, 3)
        min_confidence = float(self.processing_config.contract_ai_chunking_min_confidence)
        if confidence < min_confidence:
            logger.info(
                "AI clause chunking coverage %.3f below threshold %.3f; using deterministic fallback",
                confidence,
                min_confidence,
            )
            return [], confidence
        return valid, confidence

    def _spans_to_clauses(
        self,
        spans: List[ClauseSpan],
        markdown_text: str,
        *,
        ai_chunked: bool = False,
        ai_confidence: Optional[float] = None,
    ) -> List[ClauseInfo]:
        clauses: List[ClauseInfo] = []
        for idx, span in enumerate(spans, start=1):
            start = max(0, min(span.start_offset, len(markdown_text)))
            end = max(start, min(span.end_offset, len(markdown_text)))
            clause_text = markdown_text[start:end].strip()
            if not clause_text:
                continue

            heading = span.heading.strip()
            clause_number = self._infer_clause_number(heading) or str(idx)
            clause_title = heading or f"Clause {clause_number}"
            clause_type = self._infer_clause_type(heading)
            level = len(span.path) if span.path else 1
            parent_number = None
            if "." in clause_number:
                parent_number = ".".join(clause_number.split(".")[:-1])

            clauses.append(
                ClauseInfo(
                    clause_number=clause_number,
                    clause_title=clause_title,
                    clause_text=clause_text,
                    clause_type=clause_type,
                    start_position=start,
                    end_position=end,
                    level=level,
                    parent_number=parent_number,
                    clause_id=span.clause_id,
                    toc_path=span.path,
                    chunk_type=clause_type,
                    ai_chunked=ai_chunked,
                    ai_confidence=ai_confidence,
                )
            )
        return clauses

    def _build_clause_payloads(
        self,
        parsed_doc: ParsedDocument,
        clauses: List[ClauseInfo],
        final_tags: List[str],
        upload_id: str,
        document_id: str,
        organization_id: str,
        project_id: Optional[str],
        filename: str,
        source_path: str,
        map_pages: bool = True,
        clause_source: str = "contracts_ingest",
    ) -> List[Dict[str, Any]]:
        """
        Build embedding payloads from extracted clauses.
        Per markdown guide: Each clause is split intelligently,
        and complete clause metadata is preserved.
        """
        payloads: List[Dict[str, Any]] = []

        for clause in clauses:
            page_numbers = self._map_pages_for_clause(clause, parsed_doc) if map_pages else []
            clause_tags = self._extract_clause_tags(clause.clause_text)
            section_heading = clause.clause_title or f"{clause.clause_type.title()} {clause.clause_number}"
            page_start = min(page_numbers) if page_numbers else None
            page_end = max(page_numbers) if page_numbers else None
            source_pdf_page_link = self._source_pdf_page_link(document_id, page_start) if page_start else None
            chunk_type = clause.chunk_type or clause.clause_type or "clause"
            # Split long clauses intelligently
            clause_chunks = self.clause_extractor.split_long_clause(
                clause, 
                max_length=self.config.CHUNK_SIZE
            )

            is_complete = len(clause_chunks) == 1

            for chunk_data in clause_chunks:
                chunk_text = chunk_data['text']
                chunk_checksum = hashlib.sha256(chunk_text.encode("utf-8")).hexdigest()
                toc_path = clause.toc_path or []
                enriched_parts = [
                    f"Clause {chunk_data['clause_number']}: {chunk_data['clause_title']}",
                    f"Section: {section_heading}",
                ]
                if toc_path:
                    enriched_parts.append(f"Hierarchy: {' > '.join(toc_path)}")
                if page_numbers:
                    enriched_parts.append(f"Pages: {', '.join(str(page) for page in page_numbers)}")
                if clause_tags:
                    enriched_parts.append(f"Clause tags: {', '.join(clause_tags)}")
                if final_tags:
                    enriched_parts.append(f"Document tags: {', '.join(final_tags)}")
                enriched_parts.append(chunk_text)
                text_enriched = "\n".join(enriched_parts)

                metadata = normalize_source_payload({
                    "upload_id": upload_id,
                    "document_id": document_id,
                    "contract_id": document_id,
                    "organization_id": str(organization_id),
                    "project_id": str(project_id) if project_id else None,
                    "uploadType": "contract",
                    "document_type": "contract",
                    "letterNo": None,
                    "source_file": source_path,
                    "source_filename": filename,
                    "file_name": filename,
                    "file_path": source_path,
                    "filename": filename,
                    "section": section_heading,
                    "section_heading": section_heading,
                    "section_title": section_heading,
                    
                    # NEW CLAUSE METADATA FIELDS (per markdown guide)
                    "clause_number": chunk_data['clause_number'],
                    "clause_no": chunk_data['clause_number'],
                    "clause_title": chunk_data['clause_title'],
                    "clause_type": clause.clause_type,
                    "chunk_type": chunk_type,
                    "clause_level": clause.level,
                    "parent_clause_number": clause.parent_number,
                    "chunk_index": chunk_data['chunk_index'],
                    "is_complete_clause": chunk_data['is_complete'],
                    "clause_start_position": clause.start_position,
                    "clause_end_position": clause.end_position,
                    "clause_tags": clause_tags,
                    "clause_id": clause.clause_id,
                    "toc_path": clause.toc_path or [],
                    "ai_chunked": bool(clause.ai_chunked),
                    "ai_confidence": clause.ai_confidence,

                    # Page grounding
                    "page_number": page_numbers[0] if page_numbers else None,
                    "page": page_numbers[0] if page_numbers else None,
                    "page_numbers": page_numbers,
                    "page_start": page_start,
                    "page_end": page_end,
                    "source_pdf_page_link": source_pdf_page_link,
                    
                    "tags": final_tags,
                    "checksum_sha256": chunk_checksum,
                    "source": clause_source,
                    "text_source": "cleaned_contract_text",
                    "text_enriched": text_enriched,
                })

                payloads.append({
                    "text": chunk_text,
                    "metadata": metadata,
                    "checksum": chunk_checksum
                })

        return payloads

    @staticmethod
    def _build_chunk_id(upload_id: str, clause_number: Optional[str], chunk_index: int, checksum: str) -> str:
        seed = f"{upload_id}:{clause_number or 'clause'}:{chunk_index}:{checksum}"
        return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))

    def _build_qdrant_chunk(
        self,
        metadata: Dict[str, Any],
        text: str,
        embedding_dim: int,
    ) -> Dict[str, Any]:
        upload_id = metadata.get("upload_id") or metadata.get("document_id") or "contract"
        clause_number = metadata.get("clause_number")
        chunk_index = metadata.get("chunk_index") or 0
        checksum = metadata.get("checksum_sha256") or hashlib.sha256(text.encode("utf-8")).hexdigest()
        chunk_id = self._build_chunk_id(str(upload_id), clause_number, int(chunk_index), checksum)

        extra_payload = {
            "upload_id": metadata.get("upload_id"),
            "uploadType": metadata.get("uploadType"),
            "document_type": "contract",
            "contract_id": metadata.get("contract_id"),
            "clause_id": metadata.get("clause_id"),
            "clause_number": clause_number,
            "clause_no": metadata.get("clause_no") or clause_number,
            "clause_title": metadata.get("clause_title"),
            "clause_type": metadata.get("clause_type"),
            "chunk_type": metadata.get("chunk_type"),
            "clause_level": metadata.get("clause_level"),
            "parent_clause_number": metadata.get("parent_clause_number"),
            "chunk_index": chunk_index,
            "is_complete_clause": metadata.get("is_complete_clause"),
            "clause_start_position": metadata.get("clause_start_position"),
            "clause_end_position": metadata.get("clause_end_position"),
            "clause_tags": metadata.get("clause_tags") or [],
            "toc_path": metadata.get("toc_path") or [],
            "file_name": metadata.get("file_name") or metadata.get("filename"),
            "file_path": metadata.get("file_path") or metadata.get("source_file"),
            "source_filename": metadata.get("source_filename") or metadata.get("filename"),
            "source_file": metadata.get("source_file"),
            "section": metadata.get("section"),
            "section_heading": metadata.get("section_heading"),
            "section_title": metadata.get("section_title") or metadata.get("section_heading"),
            "page_number": metadata.get("page_number"),
            "page_numbers": metadata.get("page_numbers") or [],
            "page_start": metadata.get("page_start"),
            "page_end": metadata.get("page_end"),
            "source_pdf_page_link": metadata.get("source_pdf_page_link"),
            "ai_chunked": bool(metadata.get("ai_chunked")),
            "ai_confidence": metadata.get("ai_confidence"),
            "text_source": metadata.get("text_source"),
            "organization_id": metadata.get("organization_id"),
            "project_id": metadata.get("project_id"),
            "checksum_sha256": checksum,
        }

        return {
            "chunk_id": chunk_id,
            "document_id": metadata.get("document_id") or upload_id,
            "org_id": metadata.get("organization_id"),
            "project_id": metadata.get("project_id"),
            "page_start": metadata.get("page_start") or metadata.get("page_number") or metadata.get("page"),
            "page_end": metadata.get("page_end") or metadata.get("page_number") or metadata.get("page"),
            "text": text,
            "text_enriched": metadata.get("text_enriched"),
            "tags": metadata.get("tags", []),
            "embedding_provider": "openai",
            "embedding_model": self.config.EMBEDDING_MODEL,
            "embedding_dim": embedding_dim,
            "embedding_version": "v1",
            "chunking_version": "contract_clause_v1",
            "payload": extra_payload,
        }

    async def _index_clause_vectors(
        self,
        payloads: List[Dict[str, Any]],
        vector_results: Optional[List[Dict[str, Any]]] = None,
        *,
        document_id: Optional[str] = None,
    ) -> int:
        if not payloads or not self.processing_config.qdrant_enabled or not self.vector_client.enabled:
            return 0

        batch_size = max(1, int(self.config.EMBEDDING_BATCH_SIZE))
        total = 0

        embeddings: Optional[List[List[float]]] = None
        if vector_results and len(vector_results) == len(payloads):
            embeddings = [item.get("embedding") or [] for item in vector_results]
            if any(not emb for emb in embeddings):
                embeddings = None

        if embeddings is None:
            embeddings = []
            for idx in range(0, len(payloads), batch_size):
                batch = payloads[idx : idx + batch_size]
                texts = [item.get("text") or "" for item in batch]
                vectors = await self.embedding_client.embed(texts, model=self.config.EMBEDDING_MODEL)
                embeddings.extend(vectors)

        for idx in range(0, len(payloads), batch_size):
            batch = payloads[idx : idx + batch_size]
            batch_vectors = embeddings[idx : idx + batch_size]
            if not batch_vectors:
                continue
            chunks = [
                self._build_qdrant_chunk(item.get("metadata") or {}, item.get("text") or "", len(vector))
                for item, vector in zip(batch, batch_vectors)
            ]
            await self._assert_current_publication_authority(document_id)
            total += await self.vector_client.upsert(batch_vectors, chunks)

        return total

    async def _assert_current_publication_authority(
        self,
        document_id: Optional[str],
    ) -> None:
        document = await resolve_canonical_document(self.db_service.db, document_id)
        if is_publication_blocked(document):
            raise IngestionError(
                f"Document {document_id or '<missing>'} is not authoritative for contract publication"
            )

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
            checksum_value = checksum or metadata.get("checksum_sha256") or hashlib.sha256(
                text_chunk.encode("utf-8")
            ).hexdigest()
            chunk_id = metadata.get("chunk_id") or self._build_chunk_id(
                str(metadata.get("upload_id") or metadata.get("document_id") or "contract"),
                metadata.get("clause_number"),
                int(metadata.get("chunk_index") or 0),
                checksum_value,
            )

            record = {
                **metadata,
                "chunk_id": chunk_id,
                "vector_ref": None,
                "embedding_id": metadata.get("embedding_id"),
                "embedding_dims": 0,
                "embedding_model": self.config.EMBEDDING_MODEL,
                "text": text_chunk,
                "num_tokens": len(text_chunk.split()),
                "createdAt": now,
            }

            record.setdefault("tags", metadata.get("tags", []))
            record["clause_tags"] = metadata.get("clause_tags") or []
            record["toc_path"] = metadata.get("toc_path") or []
            record["page_numbers"] = metadata.get("page_numbers") or []
            if record["page_numbers"]:
                record.setdefault("page_number", record["page_numbers"][0])
                record.setdefault("page", record["page_numbers"][0])
            record.setdefault(
                "section_heading",
                metadata.get("section_heading") or metadata.get("clause_title"),
            )
            record.setdefault(
                "file_name",
                metadata.get("file_name") or metadata.get("filename"),
            )
            record.setdefault(
                "file_path",
                metadata.get("file_path") or metadata.get("source_file"),
            )
            record.setdefault("section", metadata.get("section"))
            record.setdefault("document_type", "contract")
            record.setdefault("text_enriched", metadata.get("text_enriched"))

            if not record.get("checksum_sha256"):
                record["checksum_sha256"] = checksum_value

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
            checksum_value = metadata.get("checksum_sha256") or hashlib.sha256(
                text_chunk.encode("utf-8")
            ).hexdigest()
            chunk_id = metadata.get("chunk_id") or self._build_chunk_id(
                str(metadata.get("upload_id") or metadata.get("document_id") or "contract"),
                metadata.get("clause_number"),
                int(metadata.get("chunk_index") or 0),
                checksum_value,
            )

            record = {
                **metadata,
                "chunk_id": chunk_id,
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
                record["checksum_sha256"] = checksum_value

            record.setdefault("tags", metadata.get("tags", []))
            record["clause_tags"] = metadata.get("clause_tags") or []
            record["toc_path"] = metadata.get("toc_path") or []
            record["page_numbers"] = metadata.get("page_numbers") or []
            if record["page_numbers"]:
                record.setdefault("page_number", record["page_numbers"][0])
                record.setdefault("page", record["page_numbers"][0])
            record.setdefault(
                "section_heading",
                metadata.get("section_heading") or metadata.get("clause_title"),
            )
            record.setdefault(
                "file_name",
                metadata.get("file_name") or metadata.get("filename"),
            )
            record.setdefault(
                "file_path",
                metadata.get("file_path") or metadata.get("source_file"),
            )
            record.setdefault("section", metadata.get("section"))
            record.setdefault("document_type", "contract")
            record.setdefault("text_enriched", metadata.get("text_enriched"))
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
    document_id: Optional[str] = None,
    retry_ocr_pages: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """Convenience function for file ingestion"""
    ingestor = create_contract_ingestor(db)
    return await ingestor.ingest_file(
        organization_id, project_id, file_path, filename, tags, upload_id, document_id, retry_ocr_pages
    )


def schedule_ingest(
    db,
    organization_id: str,
    project_id: Optional[str],
    file_path: str,
    filename: Optional[str],
    tags: Optional[List[str]],
    upload_id: Optional[str],
    document_id: Optional[str],
) -> None:
    """Schedule ingestion as background task"""
    try:
        task = asyncio.create_task(
            ingest_file(db, organization_id, project_id, file_path, filename, tags, upload_id, document_id)
        )
        logger.info(f"Scheduled ingestion task for {filename or file_path}")
    except Exception as e:
        logger.error(f"Failed to schedule ingestion: {e}")
