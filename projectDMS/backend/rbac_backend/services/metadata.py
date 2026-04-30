# Fixed parsing functions based on metadata_complete_process.py
# Drop-in replacement for the parsing logic in your new metadata.py

import re
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

def parse_extraction_report_fixed(report: str) -> Dict[str, Any]:
    """
    logger.info("Legacy metadata parser (metadata.py) engaged")
    Fixed parsing function based on the working metadata_complete_process.py
    This function uses the exact same regex patterns and logic that work properly.
    
    Args:
        report: The structured report text from OpenAI
        
    Returns:
        Dictionary with parsed metadata fields
    """
    lines = report.splitlines()
    # Join wrapped lines for simpler regex - exact same normalization
    text = "
".join(l.strip() for l in lines if l.strip())

    def find(patterns: List[str]) -> Optional[str]:
        """Helper to find single field values"""
        for pat in patterns:
            m = re.search(pat, text, flags=re.IGNORECASE | re.MULTILINE)
            if m:
                val = m.group(1).strip().rstrip(":")
                return val if val else None
        return None

    # Extract single fields using exact patterns from working version
    date_str = find([
        r"^\s*(?:1\)|-)?.*?Date\s*[:\-]\s*(.+)$",
        r"^\s*Date\s*\.\s*(.+)$", 
        r"^\s*Dated?\s*[:\-]\s*(.+)$",
    ])

    subject = find([
        r"^\s*(?:5\)|-)?.*?Subject\s*[:\-]\s*(.+)$",
        r"^\s*Re\s*[:\-]\s*(.+)$",
    ])

    letter_no = find([
        r"^\s*(?:2\)|-)?.*?Letter\s*No\.?\s*[:\-]\s*(.+)$",
        r"^\s*Letter\s*Number\s*[:\-]\s*(.+)$",
        r"^\s*Ref(?:erence)?\s*No\.?\s*[:\-]\s*(.+)$",
    ])

    from_company = find([
        r"^\s*(?:3\)|-)?.*?From\s*(?:\(Company\))?\s*[:\-]\s*(.+)$",
        r"^\s*Sender\s*[:\-]\s*(.+)$", 
        r"^\s*From\s*[:\-]\s*(.+)$",
    ])

    to_company = find([
        r"^\s*(?:4\)|-)?.*?To\s*(?:\(Company\))?\s*[:\-]\s*(.+)$",
        r"^\s*Recipient\s*[:\-]\s*(.+)$",
        r"^\s*To\s*[:\-]\s*(.+)$",
    ])

    # References: capture block following 'References' heading - EXACT working pattern
    refs = []
    refs_block = re.search(
        r"(?ims)^\s*(?:6\)|-)?.*?References?\s*(?:\(Ref\.?\))?\s*[:\-]?\s*(.+?)(?=
\s*(?:\d+\)\s*[A-Z]|Summary|Key\s*Words|Contractual\s*Clauses|Full\s*content|$))",
        text + "
End:",
    )

    if refs_block:
        raw = refs_block.group(1).strip()
        # split bullets / lines
        for line in raw.splitlines():
            # strip common bullet prefixes: -, –, •, 1), 1., etc.
            t = re.sub(r"^[\-\u2013\u2022\*\d\.\)\s]+", "", line).strip()
            if t:
                refs.append(t)

    # Summary: capture block following 'Summary' heading - EXACT working pattern
    summary_val: Optional[str] = None
    summary_block = re.search(
        r"(?ims)^\s*(?:7\)|-)?.*?Summary\s*[:\-]?\s*(.+?)(?=
\s*(?:\d+\)|Key Words|Contractual Clauses|Full content|$))",
        text + "
End:",
    )

    if summary_block:
        # Keep lines as provided; trim bullets to a clean list text
        raw = summary_block.group(1).strip()
        # Normalize bullets to "- " prefix
        lines_clean: List[str] = []
        for ln in raw.splitlines():
            t = re.sub(r"^[\-\*\d\.\)\s]+", "", ln).strip()
            if t:
                lines_clean.append(f"- {t}")
        summary_val = "
".join(lines_clean) if lines_clean else raw

    # Helper to parse list blocks under headings - EXACT working function
    def _parse_list_block_fixed(text: str, label_patterns: List[str]) -> List[str]:
        """Find a heading and return bullet/line items under it."""
        block = None
        for pat in label_patterns:
            m = re.search(pat, text + "
End:", flags=re.IGNORECASE | re.DOTALL | re.MULTILINE)
            if m:
                block = m.group(1).strip()
                break

        if not block:
            return []

        items: List[str] = []
        for ln in block.splitlines():
            t = re.sub(r"^[\-\*\d\.\)\s]+", "", ln).strip()
            if t:
                items.append(t)

        # If the block is a single line with comma-separated values, split it
        if len(items) == 1 and ("," in items[0]):
            split_items = [p.strip() for p in items[0].split(",") if p.strip()]
            # Normalize minor trailing punctuation
            items = [re.sub(r"[;\.\s]+$", "", it).strip() for it in split_items if it]

        # Final trim of each item
        items = [re.sub(r"[;\.\s]+$", "", it).strip() for it in items if it]
        return items

    # Keywords - using exact working patterns
    keywords = _parse_list_block_fixed(
        text,
        [
            r"(?ims)^.*?Key Words[:\-]?\s*(.+?)(?=
\s*(?:\d+\)|Contractual Clauses|Full content|$))",
            r"(?ims)^.*?Key contractual words[:\-]?\s*(.+?)(?=
\s*(?:\d+\)|Contractual Clauses|Full content|$))"
        ]
    )

    # Contractual Clauses - using exact working patterns  
    contractual_clauses = _parse_list_block_fixed(
        text,
        [
            r"(?ims)^.*?Contractual Clauses[:\-]?\s*(.+?)(?=
\s*(?:\d+\)|Key Words|Full content|$))"
        ]
    )

    # Treat 'Not found' as absence rather than a literal value - EXACT working logic
    if contractual_clauses and len(contractual_clauses) == 1 and re.search(r"^\s*not\s*found\s*$", contractual_clauses[0], flags=re.IGNORECASE):
        contractual_clauses = []

    # Full content section - exact working pattern
    full_content = None
    fc = re.search(r"(?is)^\s*(?:10\)|-)?.*?Full\s*content\s*[:\-]?\s*(.+)$", text)
    if fc:
        full_content = fc.group(1).strip()

    return {
        "date": date_str,
        "subject": subject,
        "letterNo": letter_no,
        "from_": from_company,
        "to": to_company,
        "reference": refs,
        "summary": summary_val,
        "full_content": full_content,
        "keywords": keywords or [],
        "contractual_clauses": contractual_clauses or [],
    }

def _parse_date_safe_fixed(date_str: Optional[str]) -> Optional[datetime]:
    """Parse date safely using exact working patterns"""
    if not date_str:
        return None

    patterns = ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%b %d, %Y", "%d %b %Y", "%B %d, %Y"]
    for fmt in patterns:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except Exception:
            continue
    
    return None

def upsert_document_metadata_db_fixed(db: Any, document_id: str, pdf_path: str, parsed: Dict[str, Any], full_text: str) -> Dict[str, Any]:
    """
    Fixed database upsert function based on the working metadata_complete_process.py
    This uses the exact same logic for finding, creating, and updating documents.
    
    Args:
        db: MongoDB database connection
        document_id: Document ID (optional)
        pdf_path: Path to the PDF file
        parsed: Parsed metadata dictionary 
        full_text: Full OCR text content
        
    Returns:
        Updated document dictionary
    """
    from bson.objectid import ObjectId
    import os
    
    # Try id lookups first: string _id, then ObjectId fallback - EXACT working logic
    doc = None
    if document_id:
        try:
            doc = db.documents.find_one({"_id": document_id})
        except Exception:
            doc = None

        if not doc:
            try:
                doc = db.documents.find_one({"_id": ObjectId(document_id)})
            except Exception:
                doc = None

    if not doc:
        # Try to find document by file path with multiple normalization variants - EXACT working logic
        candidates = set([
            pdf_path,
            os.path.abspath(pdf_path),
            pdf_path.replace("\\", "/"),
            pdf_path.replace("/", "\\"),
            os.path.abspath(pdf_path).replace("\\", "/"),
            os.path.abspath(pdf_path).replace("/", "\\"),
        ])

        doc = None
        for cand in candidates:
            doc = db.documents.find_one({"filepath_local": cand})
            if doc:
                break

    # Prepare updates with proper field mapping - EXACT working field names
    updates: Dict[str, Any] = {
        "ocrText": full_text,
        "updatedAt": datetime.now()
    }

    # Map fields using exact working field names
    if parsed.get("subject"):
        updates["subject"] = parsed["subject"]
    if parsed.get("letterNo"):
        updates["letterNo"] = parsed["letterNo"]
    if parsed.get("from_"):
        updates["from_"] = parsed["from_"]
    if parsed.get("to"):
        updates["to"] = parsed["to"]
    if parsed.get("summary"):
        updates["summary"] = parsed["summary"]

    pdate = _parse_date_safe_fixed(parsed.get("date"))
    if pdate:
        updates["date"] = pdate

    # Use exact field names from working version for new arrays
    if parsed.get("reference"):
        updates["reference"] = parsed["reference"]

    # These match the field names expected by the Document model
    if parsed.get("keywords"):
        updates["keywords"] = parsed["keywords"]  # ← maps "Keywords"

    if parsed.get("contractual_clauses"):
        updates["contractual_clauses"] = parsed["contractual_clauses"]  # ← maps "Contractual Clauses"

    # Debug logging to verify what's being saved
    logger.debug(f"DEBUG - Saving to DB: {list(updates.keys())}")

    # Upsert into documents (plural) - EXACT working logic
    if doc:
        db.documents.update_one({"_id": doc.get("_id")}, {"$set": updates})
        doc = db.documents.find_one({"_id": doc.get("_id")})
    else:
        # Create a new document if none exists - EXACT working structure
        placeholder = {
            "filename": os.path.basename(pdf_path),
            "filepath_local": pdf_path,
            "filepath_s3": "",
            "presigned_url": "",
            "filetype": "application/pdf",
            "filesize": 0,
            "uploadType": "incoming",
            "letterNo": updates.get("letterNo"),
            "date": updates.get("date") or datetime.now(),
            "subject": updates.get("subject") or "",
            "from_": updates.get("from_", ""),
            "to": updates.get("to", ""),
            "reference": updates.get("reference", []),
            "summary": updates.get("summary"),
            "keywords": updates.get("keywords", []),  # NEW
            "contractual_clauses": updates.get("contractual_clauses", []),  # NEW
            "tags": [],
            "subTags": [],
            "status": "draft",
            "ocrEnabled": True,
            "compressionEnabled": False,
            "ocrText": full_text,
            "createdAt": datetime.now(),
            "updatedAt": datetime.now(),
            "organization_id": "",
            "project_id": "",
            "createdBy": "system",
            "version": "1.0",
            "enclosures": [],
            "references": [],
            "_id": str(os.urandom(12).hex()),
        }

        db.documents.insert_one(placeholder)
        doc = placeholder

    logger.info("✅ Document metadata saved to 'documents'.")

    # Mirror to 'document' (singular) if that collection is present - EXACT working logic
    try:
        if "document" in db.list_collection_names():
            db.document.update_one(
                {"filepath_local": doc.get("filepath_local")},
                {"$set": updates, "$setOnInsert": {
                    "filename": doc.get("filename"),
                    "createdAt": doc.get("createdAt", datetime.now()),
                    "organization_id": doc.get("organization_id", ""),
                    "project_id": doc.get("project_id", ""),
                    "uploadType": doc.get("uploadType", "incoming"),
                    "version": doc.get("version", "1.0"),
                }},
                upsert=True,
            )
            logger.info("✅ Mirrored metadata to 'document'.")
        else:
            logger.info("ℹ️ 'document' collection not present; mirror skipped.")
    except Exception as e:
        logger.warning(f"⚠️ Mirror to 'document' failed: {e}")

    return doc or {}

# Usage Instructions:
# 1. Replace your parse_extraction_report function with parse_extraction_report_fixed
# 2. Replace your upsert database function with upsert_document_metadata_db_fixed  
# 3. Make sure to use the exact field names returned by the parsing function
# 4. The database update logic handles both single and plural collection names

# Example usage in your existing code:
"""
# Instead of:
# parsed = self.parse_extraction_report(report)

# Use:
parsed = parse_extraction_report_fixed(report) 

# Instead of your current database update:
# doc = await self.upsert_document_metadata(db, document_id, filepath, parsed_metadata, full_text)

# Use:
doc = upsert_document_metadata_db_fixed(db, document_id, filepath, parsed, full_text)
"""

