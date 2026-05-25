"""Legacy metadata compatibility helpers.

Older routers import ``rbac_backend.services.metadata`` directly. The active
implementation lives in ``document_processor`` and ``text_processing_service``;
this module keeps those legacy imports stable without carrying a second,
divergent parser implementation.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, Optional

from ..config.document_processing_config import DocumentProcessingConfig
from .document_processor import process_document as _process_document_async
from .text_processing_service import TextProcessingService

logger = logging.getLogger(__name__)


def _metadata_to_legacy_dict(metadata: Any) -> Dict[str, Any]:
    """Return field names used by historical metadata callers."""

    if metadata is None:
        return {}
    if hasattr(metadata, "model_dump"):
        data = metadata.model_dump()
    elif hasattr(metadata, "dict"):
        data = metadata.dict()
    elif isinstance(metadata, dict):
        data = metadata
    else:
        data = {}

    return {
        "date": data.get("date"),
        "subject": data.get("subject"),
        "letterNo": data.get("letter_no") or data.get("letterNo"),
        "from_": data.get("from_company") or data.get("from_"),
        "to": data.get("to_company") or data.get("to"),
        "reference": data.get("references") or data.get("reference") or [],
        "summary": data.get("summary"),
        "full_content": data.get("full_content"),
        "keywords": data.get("keywords") or [],
        "contractual_clauses": data.get("contractual_clauses") or [],
    }


def parse_extraction_report_fixed(report: str) -> Dict[str, Any]:
    """Parse an LLM extraction report into legacy dictionary keys."""

    parser = TextProcessingService(DocumentProcessingConfig())
    return _metadata_to_legacy_dict(parser.parse_extraction_report(report or ""))


def _parse_date_safe_fixed(date_str: Optional[str]) -> Optional[datetime]:
    """Parse dates using the maintained text processing service."""

    parser = TextProcessingService(DocumentProcessingConfig())
    return parser.parse_date_safe(date_str)


def upsert_document_metadata_db_fixed(
    db: Any,
    document_id: str,
    pdf_path: str,
    parsed: Dict[str, Any],
    full_text: str,
) -> Dict[str, Any]:
    """Update document metadata for synchronous legacy callers.

    This function intentionally stays small. New code should use
    ``DatabaseService.save_document_data`` through ``DocumentProcessor``.
    """

    from bson.objectid import ObjectId

    lookup_candidates = []
    if document_id:
        lookup_candidates.append({"_id": document_id})
        try:
            lookup_candidates.append({"_id": ObjectId(document_id)})
        except Exception:
            pass
    if pdf_path:
        lookup_candidates.append({"filepath_local": pdf_path})

    doc = None
    for query in lookup_candidates:
        try:
            doc = db.documents.find_one(query)
        except Exception:
            doc = None
        if doc:
            break

    updates: Dict[str, Any] = {
        "ocrText": full_text,
        "updatedAt": datetime.utcnow(),
    }
    field_map = {
        "subject": "subject",
        "letterNo": "letterNo",
        "from_": "from_",
        "to": "to",
        "summary": "summary",
        "reference": "reference",
        "keywords": "keywords",
        "contractual_clauses": "contractual_clauses",
        "full_content": "full_content",
    }
    for source_key, target_key in field_map.items():
        value = parsed.get(source_key)
        if value:
            updates[target_key] = value

    parsed_date = _parse_date_safe_fixed(parsed.get("date"))
    if parsed_date:
        updates["date"] = parsed_date

    if doc:
        db.documents.update_one({"_id": doc["_id"]}, {"$set": updates})
        return db.documents.find_one({"_id": doc["_id"]}) or {}

    placeholder = {
        "filename": str(pdf_path).split("/")[-1].split("\\")[-1],
        "filepath_local": pdf_path,
        "filetype": "application/pdf",
        "filesize": 0,
        "uploadType": "incoming",
        "status": "draft",
        "ocrEnabled": True,
        "compressionEnabled": False,
        "createdAt": datetime.utcnow(),
        "updatedAt": datetime.utcnow(),
        **updates,
    }
    result = db.documents.insert_one(placeholder)
    placeholder["_id"] = result.inserted_id
    return placeholder


async def process_document_async(
    pdf_path: str,
    pathStructure1: str = "",
    uploadType: str = "incoming",
    document_id: Optional[str] = None,
):
    """Async compatibility wrapper using the maintained processor."""

    return await _process_document_async(
        pdf_path=pdf_path,
        path_structure=pathStructure1,
        upload_type=uploadType,
        document_id=document_id,
    )


def process_document(
    pdf_path: str,
    pathStructure1: str = "",
    uploadType: str = "incoming",
    document_id: Optional[str] = None,
):
    """Synchronous compatibility wrapper used by legacy background tasks."""

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            process_document_async(pdf_path, pathStructure1, uploadType, document_id)
        )

    logger.info("Scheduling legacy metadata processing for %s", pdf_path)
    return loop.create_task(
        process_document_async(pdf_path, pathStructure1, uploadType, document_id)
    )


__all__ = [
    "parse_extraction_report_fixed",
    "_parse_date_safe_fixed",
    "upsert_document_metadata_db_fixed",
    "process_document",
    "process_document_async",
]
