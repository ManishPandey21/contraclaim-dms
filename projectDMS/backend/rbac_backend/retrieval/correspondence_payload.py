"""The one Qdrant payload contract for correspondence vectors (DI-B1).

Correspondence vectors used to be written by LangChain's ``add_texts``, which
nests everything under ``metadata`` and names the organisation
``organization_id``. Every tenant-scoped reader (`VectorClient.search`, and so
`RetrievalService.search`, the RAG endpoints and drafting) filters the FLAT
``org_id`` / ``project_id`` fields that the native writers produce. The two
never met: an uploaded letter was stored, counted as synced, and invisible to
every scoped search.

This module is the only place a correspondence point's payload is built, and
it builds the shape the readers already filter on. Its parts are kept apart on
purpose:

* **authority** - ``org_id`` (and its ``organization_id`` alias, same value) and
  ``project_id``. Taken only from the stored document record, normalised to one
  string form, and written LAST so no descriptive field can shadow them. A
  document with no organisation is refused: an unscoped point is exactly the
  vector a tenant filter cannot contain.
* **identity** - ``document_id``, ``chunk_id``, ``chunk_index``,
  ``qdrant_point_id``, ``doc_type`` / ``uploadType``, ``source``.
* **descriptive metadata** - an allowlist of extracted document fields. It can
  never carry an authority key, and never carries AI reply advice
  (``key_reply_points`` and relatives): that is advice about a reply, not
  content of the letter, and must not travel as evidence (DI-N6).

``project_id`` is ``None`` for an organisation-level document. A project-scoped
search matches ``project_id`` by equality, so an organisation-level letter is
not returned to a project search - the same answer ``build_scope_query`` gives
for the Mongo rows.

``payload_schema_version`` marks canonical points. A point without it was
written by an earlier writer and is found by the backfill census, not by
readers: see ``docs/audits/correspondence-qdrant-backfill-plan.md``.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import date, datetime
from typing import Any, Dict, List, Mapping, Optional, Sequence

from bson import ObjectId

from ..ingestion.chunk_ids import deterministic_chunk_id
from .authority import is_authority_key
from .source_metadata import normalize_source_payload

PAYLOAD_SCHEMA_VERSION_FIELD = "payload_schema_version"
CORRESPONDENCE_PAYLOAD_SCHEMA_VERSION = "correspondence.v1"

#: ``doc_type`` of correspondence. Drafting narrows on ``doc_type == "letter"``.
CORRESPONDENCE_DOC_TYPE = "letter"
CORRESPONDENCE_UPLOAD_TYPES = frozenset({"incoming", "outgoing"})

#: Extracted document fields a correspondence point may describe itself with.
DESCRIPTIVE_FIELDS = (
    "letterNo",
    "subject",
    "summary",
    "keywords",
    "additional_keywords",
    "contractual_clauses",
    "asset_type",
    "location",
    "specific_area",
    "chainage_from",
    "chainage_to",
    "work_type",
    "issue_nature",
    "claim_category",
    "priority",
    "reference_chain",
    "extracted_tags",
    "extracted_subTags",
)

#: The model's interpretation of the letter - who it holds responsible, which
#: event it suggests linking - rather than a description of it. Kept on the
#: Mongo row (its readers are unchanged) but never in a vector payload, where it
#: would be returned beside the letter's text as if it were evidence.
INTERPRETIVE_FIELDS = (
    "alleged_responsibility",
    "linked_event_suggested",
)

#: AI reply advice. Never source evidence, so never in a vector payload.
ADVISORY_FIELDS = frozenset(
    {
        "key_reply_points",
        "points_to_address",
        "suggested_response_considerations",
        "reply_recommendations",
        "reply_recommendation",
        "suggested_reply",
        "recommended_response",
    }
)


#: The LLM extraction report's reply-advice item ("24) Key Reply Points - Points
#: to be Addressed While Responding: ..."; item 10 in the older layout). When a
#: letter has no OCR text and no parsed full content, the WHOLE report becomes
#: the text that would be chunked and embedded, and this item with it.
#:
#: Such text is the model's report, not the letter, so it is REFUSED for
#: indexing rather than cleaned: an earlier revision stripped the item, and
#: every stripping rule left a way for advice to leak past it or for the letter
#: body to be dropped silently. Detection only has to find the heading.
#:
#: The model is asked for Markdown, so the heading may arrive bolded, as a
#: Markdown heading, bullet or table cell, numbered "24)", "24.", "(24)" or
#: "24 -", with a parenthetical, in the singular, or under its long label
#: "Points to be Addressed While Responding". A numbered "Key Reply Points" (or
#: the long label in full) is report-specific and is recognised anywhere.
#: Anything weaker is recognised only in text shaped like the report, so a
#: letter or minutes saying "Key reply points:" or "5. Points to be addressed:"
#: is kept.
_LEAD = r"^[ \t]*(?:[#>*_\-(|][ \t]*)*"
_MARK = r"(?:\*\*|__)?[ \t]*"
_NUMBER = r"(\d+)[ \t]*(?:\\?[.):]|[-\u2013\u2014])?[ \t]*"
#: After the label: a colon, a dash, a joiner to the long label ("/", "&",
#: ","), a parenthetical, or the end of the line.
_TAIL_END = (
    r"(?![A-Za-z0-9])[ \t]*(?:\([^)\n]*\)[ \t]*)?(?:\*\*|__)?[ \t]*"
    r"(?::|[-\u2013\u2014/&,|]|$)"
)
_KEY_REPLY = r"Key[ \t]*Reply[ \t]*Points?"
_LONG_LABEL = r"Points[ \t]*to[ \t]*be[ \t]*Addressed"
_LONG_LABEL_FULL = _LONG_LABEL + r"[ \t]*While[ \t]*Responding"
#: Report-specific anywhere: a numbered "Key Reply Point(s)", or the numbered
#: long label in full ("... While Responding").
_NUMBERED_ADVISORY_HEADING = re.compile(
    _LEAD
    + _NUMBER
    + _MARK
    + r"(?:"
    + _KEY_REPLY
    + r"|"
    + _LONG_LABEL_FULL
    + r")"
    + _TAIL_END,
    re.IGNORECASE | re.MULTILINE,
)
#: Ordinary correspondence and minutes say "5. Points to be addressed:", so the
#: short long-label - and any unnumbered heading - counts only in text shaped
#: like the report.
_REPORT_ONLY_ADVISORY_HEADING = re.compile(
    _LEAD
    + r"(?:"
    + _NUMBER
    + r")?"
    + _MARK
    + r"(?:"
    + _KEY_REPLY
    + r"|"
    + _LONG_LABEL
    + r")"
    + _TAIL_END,
    re.IGNORECASE | re.MULTILINE,
)
#: Every other report item label (``text_processing_service._ITEM_LABELS``);
#: a test keeps the two in step. ``(?![A-Za-z0-9])`` rather than ``\b``:
#: ``_`` is a word character, so ``\b`` fails before a ``__`` bold marker.
_REPORT_LABELS = (
    r"(?:Date|Letter[ \t]*(?:No|Number)|From|To|Subject|References?"
    r"|Asset[ \t]*Type|Location|Specific[ \t]*Area|Chainage[ \t]*(?:From|To)"
    r"|Work[ \t]*Type|Issue[ \t]*Nature|Claim[ \t]*Category"
    r"|Alleged[ \t]*Responsibility|Priority|(?:Additional[ \t]*)?Key[ \t]*Words"
    r"|Additional[ \t]*Keywords|Linked[ \t]*Event[ \t]*Suggested"
    r"|Reference[ \t]*Chain|Summary|Contractual[ \t]*Clauses|Full[ \t]*Content"
    r"|(?:extracted[_ \t-]*)?(?:sub[ \t]*)?tags|subTags)"
)
_REPORT_ITEM = re.compile(
    _LEAD
    + r"(?:(\d+)[ \t]*[.)][ \t]*)?"
    + _MARK
    + _REPORT_LABELS
    + r"(?![A-Za-z0-9])[^:\n]{0,60}?(?:\*\*|__)?[ \t]*:",
    re.IGNORECASE,
)
_REPORT_SHAPE_MIN_ITEMS = 3


def _is_report_shaped(text: str) -> bool:
    return (
        sum(1 for line in (text or "").splitlines() if _REPORT_ITEM.match(line))
        >= _REPORT_SHAPE_MIN_ITEMS
    )


def has_advisory_report_item(text: str) -> bool:
    """Whether ``text`` carries the extraction report's reply-advice item."""
    text = text or ""
    if _NUMBERED_ADVISORY_HEADING.search(text):
        return True
    return bool(_REPORT_ONLY_ADVISORY_HEADING.search(text)) and _is_report_shaped(text)


def rows_need_reprocess(document: Mapping[str, Any], row_texts: Sequence[str]) -> bool:
    """Whether stored ``document_vectors`` rows may not be re-embedded as they are.

    Rows written by this module's writer never carry the advice (report text is
    refused before chunking; they carry the schema marker). Older rows may have
    been chunked from the LLM report itself, and a row boundary can split the
    advice so that no single row shows its heading. For those, the whole stored
    text decides: if the document's text carries the advice, the rows are the
    report, and the document must be reprocessed.
    """
    if any(has_advisory_report_item(text) for text in row_texts):
        return True
    if has_advisory_report_item("\n".join(row_texts)):
        return True
    stored_text = str(document.get("full_text") or document.get("ocrText") or "")
    return has_advisory_report_item(stored_text)


class CorrespondencePayloadError(ValueError):
    """A correspondence point could not be given a valid authority/identity.

    Raised instead of publishing: the caller's existing error handling marks
    the indexing step failed (``vector_sync_status = error``).
    """


def refuse_report_derived_text(
    document_id: str, text: str, *, recorded_source: bool = False
) -> None:
    """Refuse to index text that is the LLM extraction report, not the letter.

    Raised before chunking, so nothing is published; the writer's existing
    handling marks the sync ``error`` and records a partial failure. The
    message names the document only - never the text.
    """
    from ..services.source_text import is_extraction_report_text

    # The advice heading, or the report's numbered item layout: a reply cut
    # off before its reply-advice item is still the report, not the letter.
    # ``recorded_source``: the writer labelled this text as the extracted
    # source, so only the advice heading (never in a letter) is judged.
    if has_advisory_report_item(text) or (
        not recorded_source and is_extraction_report_text(text)
    ):
        raise CorrespondencePayloadError(
            f"document {document_id}: the text to index is the extraction report "
            "(it carries the reply-advice item), not the letter; reprocess the "
            "document or supply its content"
        )


def canonical_scope_id(value: Any, field: str) -> Optional[str]:
    """One string form for an authority id, or ``None`` when absent.

    ``ObjectId("…")`` and ``"…"`` must produce the same payload value, or a
    point written from an ObjectId-typed row is invisible to a string-typed
    search. Anything that is not an id (a dict, a list, a bool) is refused
    rather than stringified into a value no filter will ever match.
    """
    if value is None:
        return None
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise CorrespondencePayloadError(
            f"{field} must be an id, not {type(value).__name__}"
        )
    text = str(value).strip()
    return text or None


def correspondence_point_id(document_id: str, chunk_id: str) -> str:
    """The Qdrant point id: a UUID, stable per (document, chunk).

    Unchanged from the earlier LangChain writer, so a reindex of an existing
    document overwrites its legacy points in place instead of duplicating them.
    """
    return str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"contraclaim:qdrant:{document_id}:{chunk_id}")
    )


def _json_safe(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return value


def _doc_type(upload_type: str) -> str:
    return (
        CORRESPONDENCE_DOC_TYPE
        if upload_type in CORRESPONDENCE_UPLOAD_TYPES
        else upload_type
    )


def build_correspondence_chunks(
    document: Mapping[str, Any],
    texts: Sequence[str],
    *,
    embedding_model: Optional[str],
    embedding_version: str = "v1",
    chunking_version: str = "v1",
) -> List[Dict[str, Any]]:
    """Build every chunk of one correspondence document.

    Each item carries ``payload`` (the canonical Qdrant payload), ``point_id``,
    ``chunk_id``, ``text`` and ``checksum``, plus ``metadata`` - the Mongo
    ``document_vectors`` row contract, which is a different store with its own
    readers and is left exactly as it was.
    """
    document_id = canonical_scope_id(document.get("_id"), "document_id")
    if not document_id:
        raise CorrespondencePayloadError("document has no id")
    org_id = canonical_scope_id(document.get("organization_id"), "organization_id")
    if not org_id:
        raise CorrespondencePayloadError(
            f"document {document_id} has no organisation; refusing to publish an "
            "unscoped correspondence vector"
        )
    project_id = canonical_scope_id(document.get("project_id"), "project_id")
    upload_type = str(document.get("uploadType") or "incoming").strip().lower()

    descriptive: Dict[str, Any] = {}
    stored: Dict[str, Any] = {}
    for field in DESCRIPTIVE_FIELDS + INTERPRETIVE_FIELDS:
        value = document.get(field)
        if value in (None, "", [], {}):
            continue
        # Belt and braces: the allowlist holds neither kind of key today; this
        # keeps a future edit to it from quietly adding one.
        if is_authority_key(field) or field in ADVISORY_FIELDS:
            continue
        stored[field] = value
        if field in INTERPRETIVE_FIELDS:
            continue
        descriptive[field] = _json_safe(value)
    tags = document.get("tags")
    if isinstance(tags, list) and tags:
        descriptive["tags"] = _json_safe(tags)

    chunks: List[Dict[str, Any]] = []
    for index, text in enumerate(texts):
        if has_advisory_report_item(text):
            raise CorrespondencePayloadError(
                f"document {document_id} chunk {index} carries the extraction report's "
                "reply advice (Key Reply Points); strip it before indexing"
            )
        checksum = hashlib.sha256(text.encode("utf-8")).hexdigest()
        chunk_id = deterministic_chunk_id(document_id, index, text=text[:50])
        point_id = correspondence_point_id(document_id, chunk_id)
        payload: Dict[str, Any] = {
            **descriptive,
            "document_id": document_id,
            "chunk_id": chunk_id,
            "chunk_index": index,
            "qdrant_point_id": point_id,
            "page": None,
            "text": text,
            "doc_type": _doc_type(upload_type),
            "uploadType": upload_type,
            "source": "document_processing",
            "checksum_sha256": checksum,
            "embedding_model": embedding_model,
            "embedding_provider": "openai",
            "embedding_version": embedding_version,
            "chunking_version": chunking_version,
            PAYLOAD_SCHEMA_VERSION_FIELD: CORRESPONDENCE_PAYLOAD_SCHEMA_VERSION,
            # Authority last: nothing above may shadow it.
            "org_id": org_id,
            "organization_id": org_id,
            "project_id": project_id,
        }
        payload = normalize_source_payload(payload)
        metadata: Dict[str, Any] = {
            "document_id": document_id,
            "organization_id": org_id,
            "project_id": project_id or "",
            "uploadType": upload_type,
            "letterNo": document.get("letterNo"),
            "filepath_local": document.get("filepath_local"),
            "filepath_s3": document.get("filepath_s3"),
            "chunk_index": index,
            "source": "document_processing",
            "chunk_id": chunk_id,
            "embedding_model": embedding_model,
            "embedding_provider": "openai",
            "embedding_version": embedding_version,
            "chunking_version": chunking_version,
            **{k: v for k, v in stored.items() if k != "letterNo"},
            "checksum_sha256": checksum,
            PAYLOAD_SCHEMA_VERSION_FIELD: CORRESPONDENCE_PAYLOAD_SCHEMA_VERSION,
        }
        chunks.append(
            {
                "chunk_id": chunk_id,
                "point_id": point_id,
                "text": text,
                "checksum": checksum,
                "payload": payload,
                "metadata": metadata,
            }
        )
    return chunks


def assert_canonical_correspondence_payload(payload: Mapping[str, Any]) -> None:
    """Refuse to write a point that is not a canonical, scoped payload."""
    if (
        payload.get(PAYLOAD_SCHEMA_VERSION_FIELD)
        != CORRESPONDENCE_PAYLOAD_SCHEMA_VERSION
    ):
        # Names the missing marker only: an exception message must never carry
        # a value read from a payload (test_extracted_content_not_logged).
        raise CorrespondencePayloadError(
            "not a canonical correspondence payload (payload_schema_version is not "
            "correspondence.v1)"
        )
    org_id = payload.get("org_id")
    if not isinstance(org_id, str) or not org_id:
        raise CorrespondencePayloadError("canonical payload has no org_id")
    if payload.get("organization_id") != org_id:
        raise CorrespondencePayloadError("org_id and organization_id disagree")
    project_id = payload.get("project_id")
    if project_id is not None and (not isinstance(project_id, str) or not project_id):
        raise CorrespondencePayloadError(
            "project_id must be a non-empty string or None"
        )
    if not payload.get("document_id") or not payload.get("chunk_id"):
        raise CorrespondencePayloadError(
            "canonical payload has no document/chunk identity"
        )
    leaked = ADVISORY_FIELDS.union(INTERPRETIVE_FIELDS).intersection(payload)
    if leaked:
        raise CorrespondencePayloadError(
            f"advisory fields in a vector payload: {sorted(leaked)}"
        )
    if has_advisory_report_item(str(payload.get("text") or "")):
        raise CorrespondencePayloadError("reply advice in a vector payload's text")


#: Point classes reported by the backfill census.
POINT_CANONICAL = "canonical_correspondence"
POINT_LEGACY_ENVELOPE = "legacy_langchain_envelope"
POINT_NATIVE_FLAT = "native_flat_unversioned"
POINT_UNSCOPED = "unscoped"


def classify_vector_payload(payload: Mapping[str, Any]) -> str:
    """Which writer contract a stored point follows. Read-only; used by the census.

    * canonical - this module's output.
    * legacy envelope - the pre-DI-B1 LangChain ``add_texts`` shape
      (``{page_content, metadata: {organization_id, ...}}``): invisible to every
      tenant-scoped reader, reindex required.
    * native flat - flat ``org_id`` without a schema version: contract token
      chunks and the manual/repair pipelines. Readable; not a correspondence
      backfill target.
    * unscoped - no organisation anywhere. Never readable by a scoped search,
      and never publishable again; delete after the reindex.
    """
    payload = payload or {}
    if (
        payload.get(PAYLOAD_SCHEMA_VERSION_FIELD)
        == CORRESPONDENCE_PAYLOAD_SCHEMA_VERSION
    ):
        return POINT_CANONICAL
    metadata = payload.get("metadata")
    nested = metadata if isinstance(metadata, Mapping) else {}
    flat_org = payload.get("org_id") or payload.get("organization_id")
    nested_org = nested.get("organization_id") or nested.get("org_id")
    if not flat_org and not nested_org:
        return POINT_UNSCOPED
    if flat_org:
        return POINT_NATIVE_FLAT
    return POINT_LEGACY_ENVELOPE
