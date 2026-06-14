# services/text_processing_service.py
import re
import logging
from datetime import datetime
from typing import List, Optional

from ..models.document_metadata import ParsedDocumentMetadata
from ..utils.date_parser import format_date_ddmmyyyy

logger = logging.getLogger(__name__)

class TextProcessingService:
    """Service for text processing and metadata parsing"""
    
    def __init__(self, config):
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
        
        try:
            from langchain_text_splitters import RecursiveCharacterTextSplitter

            splitter = RecursiveCharacterTextSplitter(
                chunk_size=self.config.chunk_size,
                chunk_overlap=self.config.chunk_overlap,
                separators=["\n\n", "\n", ". ", " "],
            )
            langchain_chunks = [
                chunk.strip()
                for chunk in splitter.split_text(text)
                if chunk and chunk.strip()
            ]
            if langchain_chunks:
                logger.info("LangChain splitter produced %d chunks", len(langchain_chunks))
                return langchain_chunks
        except ImportError:
            logger.debug("langchain-text-splitters not available; using fallback chunker")
        except Exception as exc:
            logger.warning("LangChain splitter failed: %s", exc)

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
            logger.info("Legacy regex metadata parser engaged (report length=%d)", len(report or ""))
            # Normalize text for parsing
            lines = (report or "").splitlines()
            text = "\n".join(line.strip() for line in lines if line.strip())
            
            # Extract individual fields
            date_str = self._extract_field(text, [
                r"^\s*(?:1\)|-)?.*?Date\s*[:\-]\s*(.+)$",
                r"^\s*Date\s*\.\s*(.+)$",
                r"^\s*Dated?\s*[:\-]\s*(.+)$",
            ])
            date_str = format_date_ddmmyyyy(date_str)
            
            subject = self._extract_field(text, [
                r"^\s*(?:5\)|-)?.*?Subject\s*[:\-]\s*(.+)$",
                r"^\s*Re\s*[:\-]\s*(.+)$",
            ])
            
            letter_no = self._extract_field(text, [
                r"^\s*(?:2\)|-)?.*?Letter\s*No\.?\s*[:\-]\s*(.+)$",
                r"^\s*Letter\s*Number\s*[:\-]\s*(.+)$",
                r"^\s*Ref(?:erence)?\s*No\.?\s*[:\-]\s*(.+)$",
            ])
            
            from_company = self._extract_field(text, [
                r"^\s*(?:3\)|-)?.*?From\s*(?:\(Company\))?\s*[:\-]\s*(.+)$",
                r"^\s*Sender\s*[:\-]\s*(.+)$",
                r"^\s*From\s*[:\-]\s*(.+)$",
            ])
            
            to_company = self._extract_field(text, [
                r"^\s*(?:4\)|-)?.*?To\s*(?:\(Company\))?\s*[:\-]\s*(.+)$",
                r"^\s*Recipient\s*[:\-]\s*(.+)$",
                r"^\s*To\s*[:\-]\s*(.+)$",
            ])
            
            # Extract list fields
            references = self._extract_list_field(text, [
                r"(?ims)^\s*(?:6\)|-)?.*?References?\s*(?:\(Ref\.?\))?\s*[:\-]?\s*(.+?)(?=\n\s*(?:\d+\)\s*[A-Z]|Summary|Key\s*Words|Contractual\s*Clauses|Full\s*content|$))"
            ])
            
            summary = self._extract_summary(text)
            
            keywords = self._extract_list_field(text, [
                r"(?ims)^.*?Key Words[:\-]?\s*(.+?)(?=\n\s*(?:\d+\)|Contractual Clauses|Full content|$))",
                r"(?ims)^.*?Key contractual words[:\-]?\s*(.+?)(?=\n\s*(?:\d+\)|Contractual Clauses|Full content|$))"
            ])
            
            contractual_clauses = self._extract_list_field(text, [
                r"(?ims)^.*?Contractual Clauses[:\-]?\s*(.+?)(?=\n\s*(?:\d+\)|Key Words|Full content|$))"
            ])
            
            # Filter out "Not found" entries
            if contractual_clauses and len(contractual_clauses) == 1:
                if re.search(r"^\s*not\s*found\s*$", contractual_clauses[0], re.IGNORECASE):
                    contractual_clauses = []
            
            full_content = self._extract_field(text, [
                r"(?is)^\s*(?:10\)|-)?.*?Full\s*content\s*[:\-]?\s*(.+)$"
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
        """Extract list field using regex patterns
        Note: Search against (text + "\
End:") so '$' can match properly without forcing End: immediately after."""
        for pattern in patterns:
            match = re.search(pattern, text + "\nEnd:", flags=re.IGNORECASE | re.DOTALL | re.MULTILINE)
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
        """Extract summary with special formatting.
        Note: Search against (text + "\
End:") so '$' can match the artificial end marker."""
        pattern = r"(?ims)^\s*(?:7\)|-)?.*?Summary\s*[:\-]?\s*(.+?)(?=\n\s*(?:\d+\)|Key Words|Contractual Clauses|Full content|$))"
        match = re.search(pattern, text + "\nEnd:")
        
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
        
        cleaned = date_str.strip()
        cleaned = re.sub(r"(\d{1,2})(st|nd|rd|th)", r"\1", cleaned, flags=re.IGNORECASE)

        patterns = [
            "%Y-%m-%d",              # 2024-01-15
            "%d-%m-%Y",              # 15-01-2024
            "%d/%m/%Y",              # 15/01/2024
            "%m/%d/%Y",              # 01/15/2024
            "%Y/%m/%d",              # 2024/01/15
            "%d-%b-%Y",              # 15-Jan-2024
            "%d %b %Y",              # 15 Jan 2024
            "%b %d, %Y",             # Jan 15, 2024
            "%dth %b %Y",            # 15th Jan 2024
            "%dst %b %Y",            # 1st Jan 2024
            "%dnd %b %Y",            # 2nd Jan 2024
            "%drd %b %Y",            # 3rd Jan 2024
            "%dth %B %Y",            # 15th January 2024
            "%dst %B %Y",            # 1st January 2024
            "%dnd %B %Y",            # 2nd January 2024
            "%drd %B %Y",            # 3rd January 2024
            "%dth %b, %Y",           # 15th Jan, 2024
            "%dst %b, %Y",           # 1st Jan, 2024
            "%dnd %b, %Y",           # 2nd Jan, 2024
            "%drd %b, %Y",           # 3rd Jan, 2024
            "%B %dth, %Y",           # January 15th, 2024
            "%B %dst, %Y",           # January 1st, 2024
            "%B %dnd, %Y",           # January 2nd, 2024
            "%B %drd, %Y",           # January 3rd, 2024
            "%b %dth, %Y",           # Jan 15th, 2024
            "%b %dst, %Y",           # Jan 1st, 2024
            "%b %dnd, %Y",           # Jan 2nd, 2024
            "%b %drd, %Y",           # Jan 3rd, 2024
            "%Y-%m-%dT%H:%M:%S",     # 2024-01-15T13:45:00
            "%Y-%m-%d %H:%M:%S",     # 2024-01-15 13:45:00
            "%Y-%m-%dT%H:%M:%S.%f",  # 2024-01-15T13:45:00.123456
            "%Y-%m-%d %H:%M:%S.%f",  # 2024-01-15 13:45:00.123456
            "%d.%m.%Y",              # 15.01.2024 (common in your documents)
            "%dth %b. %Y",           # 15th Jan. 2024
            "%dst %b. %Y",           # 1st Jan. 2024
            "%dnd %b. %Y",           # 2nd Jan. 2024
            "%drd %b. %Y",           # 3rd Jan. 2024
        ]
        
        for pattern in patterns:
            try:
                return datetime.strptime(cleaned, pattern)
            except ValueError:
                continue
        
        logger.warning(f"Unable to parse date: {date_str}")
        return None

