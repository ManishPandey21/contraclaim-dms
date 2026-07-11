# duplicate_detection_service.py
"""Two-stage duplicate-upload detection.

Stage 1 (at upload, before any processing):
  - exact file duplicate  -> same SHA-256 inside the organisation/project;
    the upload is rejected before OCR and no document record is created.
  - possible document duplicate -> same normalized letter number but a
    different file hash; the upload proceeds through OCR/metadata extraction
    marked ``duplicate_status="pending"`` with downstream artifacts
    (vectors, reference links, FalkorDB, publication) deferred.

Stage 2 (after OCR/metadata extraction):
  - the pending document is compared with the existing candidate on the
    extracted letter number, letter date, sender, recipient, subject,
    references, page count, text fingerprint, and content similarity, then
    classified as ``duplicate`` / ``revision`` / ``separate``.
"""
from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Dict, List, Optional, Set

from bson.errors import InvalidId
from bson.objectid import ObjectId
from pymongo.database import Database

from .falkor_graph_service import normalize_letter_code

logger = logging.getLogger(__name__)

# Verdicts for the stage-1 precheck
VERDICT_EXACT_FILE_DUPLICATE = "exact_file_duplicate"
VERDICT_POSSIBLE_DOCUMENT_DUPLICATE = "possible_document_duplicate"
VERDICT_UNIQUE = "unique"

# Classifications for the stage-2 review
CLASSIFICATION_DUPLICATE = "duplicate"
CLASSIFICATION_REVISION = "revision"
CLASSIFICATION_SEPARATE = "separate"

# Lifecycle / status markers used to keep pending and confirmed duplicates
# out of published listings (list queries exclude these states).
DUPLICATE_STATUS_PENDING = "pending"
LIFECYCLE_DUPLICATE_REVIEW = "duplicate_review"
LIFECYCLE_DUPLICATE = "duplicate"

# Content-similarity thresholds. Fingerprint equality always wins; these
# only arbitrate near-duplicates vs revisions vs unrelated documents.
DUPLICATE_SIMILARITY_THRESHOLD = 0.90
REVISION_SIMILARITY_THRESHOLD = 0.45

_WS_RE = re.compile(r"\s+")
_MAX_SHINGLE_WORDS = 20000


def _normalize_text(value: Optional[str]) -> str:
    if not value:
        return ""
    return _WS_RE.sub(" ", str(value)).strip().lower()


def text_fingerprint(value: Optional[str]) -> Optional[str]:
    """SHA-256 over whitespace/case-normalized text; None when there is no text."""
    normalized = _normalize_text(value)
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def content_similarity(a: Optional[str], b: Optional[str]) -> Optional[float]:
    """Jaccard similarity over 3-word shingles (cheap near-duplicate signal)."""
    norm_a = _normalize_text(a)
    norm_b = _normalize_text(b)
    if not norm_a or not norm_b:
        return None

    def shingles(text: str) -> Set[str]:
        words = text.split()[:_MAX_SHINGLE_WORDS]
        if len(words) < 3:
            return {" ".join(words)} if words else set()
        return {" ".join(words[i : i + 3]) for i in range(len(words) - 2)}

    set_a = shingles(norm_a)
    set_b = shingles(norm_b)
    if not set_a or not set_b:
        return None
    union = len(set_a | set_b)
    if union == 0:
        return None
    return len(set_a & set_b) / union


def _string_similarity(a: Optional[str], b: Optional[str]) -> Optional[float]:
    norm_a = _normalize_text(a)
    norm_b = _normalize_text(b)
    if not norm_a or not norm_b:
        return None
    return SequenceMatcher(None, norm_a, norm_b).ratio()


def _date_key(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if value:
        text = str(value).strip()
        return text[:10] if text else None
    return None


def _reference_letter_set(document: Dict[str, Any]) -> Set[str]:
    normalized: Set[str] = set()
    for ref in document.get("reference") or []:
        if isinstance(ref, dict):
            letter = ref.get("letterNo") or ref.get("letter_no") or ref.get("text")
        else:
            letter = ref
        code = normalize_letter_code(str(letter or ""))
        if code:
            normalized.add(code)
    return normalized


class DuplicateDetectionService:
    """Detects duplicate uploads before and after OCR/metadata extraction."""

    def __init__(self, db: Optional[Database] = None) -> None:
        self._db = db

    async def _get_db(self) -> Database:
        if self._db is not None:
            return self._db
        from ..core.database import get_database

        return await get_database()

    # ------------------------------------------------------------------ #
    # Stage 1 - upload-time precheck                                      #
    # ------------------------------------------------------------------ #
    async def precheck_upload(
        self,
        *,
        organization_id: str,
        project_id: str,
        letter_no: Optional[str],
        sha256: Optional[str],
    ) -> Dict[str, Any]:
        """Classify an incoming upload before any record is created.

        Both checks are scoped to the selected organisation and project and
        ignore deleted documents. The exact-hash check runs first so a
        byte-identical re-upload is always blocked regardless of the letter
        number the user typed.
        """
        db = await self._get_db()
        base_query: Dict[str, Any] = {
            "organization_id": str(organization_id),
            "project_id": str(project_id),
            "lifecycle_state": {"$ne": "deleted"},
        }
        projection = {"_id": 1, "letterNo": 1, "subject": 1, "filename": 1}

        if sha256:
            hash_query = dict(base_query)
            hash_query["sha256"] = sha256
            existing = await db.documents.find_one(hash_query, projection)
            if existing:
                return {
                    "verdict": VERDICT_EXACT_FILE_DUPLICATE,
                    "existing_document_id": str(existing["_id"]),
                    "existing_letter_no": existing.get("letterNo"),
                    "existing_subject": existing.get("subject"),
                }

        normalized_letter = normalize_letter_code(str(letter_no or ""))
        if normalized_letter:
            letter_query = dict(base_query)
            letter_query["letterNoNormalized"] = normalized_letter
            existing = await db.documents.find_one(letter_query, projection)
            if existing:
                return {
                    "verdict": VERDICT_POSSIBLE_DOCUMENT_DUPLICATE,
                    "existing_document_id": str(existing["_id"]),
                    "existing_letter_no": existing.get("letterNo"),
                    "existing_subject": existing.get("subject"),
                }

        return {"verdict": VERDICT_UNIQUE, "existing_document_id": None}

    # ------------------------------------------------------------------ #
    # Stage 2 - post-extraction classification                            #
    # ------------------------------------------------------------------ #
    async def classify_pending_document(self, document_id: str) -> Dict[str, Any]:
        """Compare a ``duplicate_status='pending'`` document with its candidate.

        Persists the verdict on the document (releasing it or quarantining it)
        and emits an audit event. Returns the review payload including the
        ``classification`` key.
        """
        db = await self._get_db()
        try:
            doc_oid = ObjectId(str(document_id))
        except (InvalidId, TypeError):
            raise ValueError(f"Invalid document_id: {document_id}")

        document = await db.documents.find_one({"_id": doc_oid})
        if not document:
            raise ValueError(f"Document {document_id} not found")

        candidate = await self._resolve_candidate(db, document)
        now = datetime.utcnow()

        if not candidate:
            review = {
                "classification": CLASSIFICATION_SEPARATE,
                "reason": "No existing document with a matching letter number was found after extraction.",
                "checked_at": now,
            }
            await db.documents.update_one(
                {"_id": doc_oid},
                {
                    "$set": {
                        "duplicate_status": "unique",
                        "duplicate_of": None,
                        "duplicate_review": review,
                        "lifecycle_state": "active",
                        "updatedAt": now,
                    }
                },
            )
            return review

        signals = self._compare_documents(document, candidate)
        classification = self._classify(signals)
        review = {
            "classification": classification,
            "existing_document_id": str(candidate["_id"]),
            "existing_letter_no": candidate.get("letterNo"),
            "signals": signals,
            "checked_at": now,
        }

        if classification == CLASSIFICATION_DUPLICATE:
            update = {
                "duplicate_status": CLASSIFICATION_DUPLICATE,
                "duplicate_of": str(candidate["_id"]),
                "duplicate_review": review,
                "lifecycle_state": LIFECYCLE_DUPLICATE,
                "updatedAt": now,
            }
        elif classification == CLASSIFICATION_REVISION:
            update = {
                "duplicate_status": CLASSIFICATION_REVISION,
                "duplicate_of": None,
                "revision_of": str(candidate["_id"]),
                "duplicate_review": review,
                "lifecycle_state": "active",
                "updatedAt": now,
            }
        else:
            update = {
                "duplicate_status": "unique",
                "duplicate_of": None,
                "duplicate_review": review,
                "lifecycle_state": "active",
                "updatedAt": now,
            }

        await db.documents.update_one({"_id": doc_oid}, {"$set": update})
        await self._emit_audit(document, candidate, review)
        return review

    async def _resolve_candidate(
        self,
        db: Database,
        document: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Stage-1 candidate if it still exists, else re-resolve by letter number."""
        candidate_id = document.get("duplicate_of")
        if candidate_id:
            try:
                candidate = await db.documents.find_one(
                    {"_id": ObjectId(str(candidate_id)), "lifecycle_state": {"$ne": "deleted"}}
                )
                if candidate:
                    return candidate
            except (InvalidId, TypeError):
                pass

        normalized_letter = document.get("letterNoNormalized") or normalize_letter_code(
            str(document.get("letterNo") or "")
        )
        if not normalized_letter:
            return None
        return await db.documents.find_one(
            {
                "_id": {"$ne": document["_id"]},
                "organization_id": str(document.get("organization_id") or ""),
                "project_id": str(document.get("project_id") or ""),
                "letterNoNormalized": normalized_letter,
                "lifecycle_state": {"$nin": ["deleted", LIFECYCLE_DUPLICATE, LIFECYCLE_DUPLICATE_REVIEW]},
            }
        )

    def _compare_documents(
        self,
        uploaded: Dict[str, Any],
        existing: Dict[str, Any],
    ) -> Dict[str, Any]:
        uploaded_text = uploaded.get("full_text") or uploaded.get("ocrText")
        existing_text = existing.get("full_text") or existing.get("ocrText")

        uploaded_fp = text_fingerprint(uploaded_text)
        existing_fp = text_fingerprint(existing_text)

        uploaded_refs = _reference_letter_set(uploaded)
        existing_refs = _reference_letter_set(existing)
        if uploaded_refs and existing_refs:
            reference_overlap = len(uploaded_refs & existing_refs) / len(uploaded_refs | existing_refs)
        else:
            reference_overlap = None

        uploaded_pages = uploaded.get("page_count")
        existing_pages = existing.get("page_count")
        page_count_match: Optional[bool] = None
        if uploaded_pages and existing_pages:
            page_count_match = int(uploaded_pages) == int(existing_pages)

        return {
            "letter_no_match": bool(
                normalize_letter_code(str(uploaded.get("letterNo") or ""))
                and normalize_letter_code(str(uploaded.get("letterNo") or ""))
                == normalize_letter_code(str(existing.get("letterNo") or ""))
            ),
            "date_match": (
                _date_key(uploaded.get("date")) == _date_key(existing.get("date"))
                if _date_key(uploaded.get("date")) and _date_key(existing.get("date"))
                else None
            ),
            "sender_match": (
                _normalize_text(uploaded.get("from")) == _normalize_text(existing.get("from"))
                if _normalize_text(uploaded.get("from")) and _normalize_text(existing.get("from"))
                else None
            ),
            "recipient_match": (
                _normalize_text(uploaded.get("to")) == _normalize_text(existing.get("to"))
                if _normalize_text(uploaded.get("to")) and _normalize_text(existing.get("to"))
                else None
            ),
            "subject_similarity": _string_similarity(uploaded.get("subject"), existing.get("subject")),
            "reference_overlap": reference_overlap,
            "page_count_match": page_count_match,
            "text_fingerprint_match": (
                uploaded_fp == existing_fp if uploaded_fp and existing_fp else None
            ),
            "content_similarity": content_similarity(uploaded_text, existing_text),
        }

    def _classify(self, signals: Dict[str, Any]) -> str:
        similarity = signals.get("content_similarity")
        subject_similarity = signals.get("subject_similarity")
        letter_match = bool(signals.get("letter_no_match"))

        if signals.get("text_fingerprint_match") is True:
            return CLASSIFICATION_DUPLICATE

        if similarity is not None and similarity >= DUPLICATE_SIMILARITY_THRESHOLD:
            # Near-identical content: duplicate when the metadata agrees too.
            metadata_agrees = letter_match or (
                signals.get("date_match") is True
                and subject_similarity is not None
                and subject_similarity >= 0.8
            )
            if metadata_agrees and signals.get("page_count_match") is not False:
                return CLASSIFICATION_DUPLICATE
            return CLASSIFICATION_REVISION

        if letter_match:
            if similarity is not None and similarity >= REVISION_SIMILARITY_THRESHOLD:
                return CLASSIFICATION_REVISION
            if similarity is None and (
                signals.get("date_match") is True
                or (subject_similarity is not None and subject_similarity >= 0.7)
                or (signals.get("reference_overlap") or 0) >= 0.5
            ):
                # No text available on one side; same letter number plus
                # agreeing metadata is treated as a revision, not a duplicate.
                return CLASSIFICATION_REVISION
            if similarity is not None and similarity < REVISION_SIMILARITY_THRESHOLD:
                return CLASSIFICATION_SEPARATE
            return CLASSIFICATION_REVISION

        return CLASSIFICATION_SEPARATE

    async def _emit_audit(
        self,
        document: Dict[str, Any],
        candidate: Dict[str, Any],
        review: Dict[str, Any],
    ) -> None:
        try:
            from .document_audit_service import DocumentAuditService

            classification = review.get("classification")
            event_type = {
                CLASSIFICATION_DUPLICATE: "document.duplicate_upload_detected",
                CLASSIFICATION_REVISION: "document.duplicate_revision_detected",
            }.get(classification, "document.duplicate_check_cleared")

            serializable_review = dict(review)
            checked_at = serializable_review.get("checked_at")
            if isinstance(checked_at, datetime):
                serializable_review["checked_at"] = checked_at.isoformat()

            await DocumentAuditService().emit(
                resource_type="document",
                resource_id=str(document["_id"]),
                event_type=event_type,
                organization_id=document.get("organization_id"),
                project_id=document.get("project_id"),
                metadata={
                    "existing_document_id": str(candidate["_id"]),
                    "letterNo": document.get("letterNo"),
                    "review": serializable_review,
                },
            )
        except Exception:
            logger.debug(
                "Failed to emit duplicate-detection audit for %s",
                document.get("_id"),
                exc_info=True,
            )


__all__ = [
    "DuplicateDetectionService",
    "VERDICT_EXACT_FILE_DUPLICATE",
    "VERDICT_POSSIBLE_DOCUMENT_DUPLICATE",
    "VERDICT_UNIQUE",
    "CLASSIFICATION_DUPLICATE",
    "CLASSIFICATION_REVISION",
    "CLASSIFICATION_SEPARATE",
    "DUPLICATE_STATUS_PENDING",
    "LIFECYCLE_DUPLICATE",
    "LIFECYCLE_DUPLICATE_REVIEW",
    "content_similarity",
    "text_fingerprint",
]
