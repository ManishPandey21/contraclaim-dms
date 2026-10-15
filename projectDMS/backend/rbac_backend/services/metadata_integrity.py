"""Correspondence metadata integrity: extraction quality and human edits.

Two rules live here so the two writers (``DatabaseService`` inside the
processor, and ``DocumentService.process_document_async``) cannot drift:

* **Quality.** A degraded extraction must not look like a clean one, and an
  empty reference list from a degraded extraction is "unknown", not "zero
  references". Only an authoritative result may clear existing links.
* **Human edits.** Reprocessing never overwrites a field a person edited.

Target states, of which this module persists the first three as
``metadata_quality.status`` (processing failure is already
``processing_error``): COMPLETE, COMPLETE_WITH_WARNINGS, PARTIAL_EXTRACTION,
FAILED. Field-level provenance (page, span, model, prompt and schema version,
confidence, review state) is the next phase; see
``docs/audits/document-ingestion-implementation-roadmap.md``.
"""

from __future__ import annotations

import copy
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

QUALITY_COMPLETE = "complete"
QUALITY_COMPLETE_WITH_WARNINGS = "complete_with_warnings"
QUALITY_PARTIAL_EXTRACTION = "partial_extraction"

#: Metadata sources that did not come from a successful AI extraction. The OCR
#: fallback never extracts references, so its empty list is not authoritative.
DEGRADED_METADATA_SOURCES = frozenset({"ocr_fallback_regex"})

#: The correspondence header facts. All of them empty means nothing usable was
#: extracted, whatever the parser reported.
CORE_FIELDS: Tuple[str, ...] = ("date", "letter_no", "from_company", "to_company", "subject")


def _is_empty(value: Any) -> bool:
    return value in (None, "", [], {})


def source_text_complete_signal(
    extraction_completeness: Optional[str],
    pages_human_review: Optional[Iterable[int]] = None,
) -> Optional[bool]:
    """Whether the text the extraction read was the whole document.

    ``None`` means the pipeline gives no signal (legacy_v0); that keeps the
    previous behaviour rather than inventing one. The unified pipeline reports
    completeness, and a page waiting for review is a page nobody has read.
    """
    if pages_human_review and list(pages_human_review):
        return False
    if extraction_completeness is None:
        return None
    return str(extraction_completeness).lower() == "complete"


def assess_metadata_quality(
    metadata: Any,
    *,
    metadata_source: Optional[str],
    partial_failures: Optional[Mapping[str, Any]] = None,
    source_text_complete: Optional[bool] = None,
) -> Dict[str, Any]:
    """Classify one extraction result.

    ``references_authoritative`` is True only when the reference list is a
    complete answer: then an empty list really means "this letter cites
    nothing", and a non-empty one may remove links it no longer contains. It is
    decided per field - a failure in another field does not touch it - and is
    False when the References field failed, the result is the deterministic
    fallback, an AI stage failed, nothing at all was read, or the source text
    the extraction read was incomplete (pages missing or awaiting review, or
    the text cut short before the model saw it).

    Incomplete source text also degrades the header fields: a value read from
    part of the letter (often another letter's number quoted in the body) may
    fill an empty field but never replaces a stored one.
    """
    field_failures = dict(getattr(metadata, "field_failures", None) or {})
    missing_core = [name for name in CORE_FIELDS if _is_empty(getattr(metadata, name, None))]
    no_core = len(missing_core) == len(CORE_FIELDS)
    ai_failure_record = (partial_failures or {}).get("ai_extraction")
    # A failed OCR-text call does not degrade a result that PydanticAI then
    # extracted successfully from the same OCR text. Any other stage (text
    # withheld, whole-file reply discarded) left PydanticAI nothing to read.
    ai_failure = bool(ai_failure_record) and not (
        metadata_source == "pydantic_ai"
        and isinstance(ai_failure_record, Mapping)
        and ai_failure_record.get("stage") == "ocr_text_extraction"
    )
    fallback = (metadata_source or "") in DEGRADED_METADATA_SOURCES
    source_incomplete = source_text_complete is False
    # The model saw only the head of the text. The header lives there, so the
    # header fields stand; a reference later in the letter may have been cut.
    source_truncated = bool((partial_failures or {}).get("source_text_truncated"))

    warnings: List[str] = []
    if field_failures:
        warnings.append("field_parse_failed")
    if fallback:
        warnings.append("ai_extraction_unavailable_deterministic_fallback")
    if ai_failure:
        warnings.append("ai_extraction_partial_failure")
    if no_core:
        warnings.append("no_core_metadata_extracted")
    elif missing_core:
        warnings.append("missing_core_fields")
    if source_incomplete:
        warnings.append("source_text_incomplete")
    if source_truncated:
        warnings.append("source_text_truncated")

    degraded = bool(field_failures) or fallback or ai_failure or no_core or source_incomplete
    if degraded:
        status = QUALITY_PARTIAL_EXTRACTION
    elif missing_core or source_truncated:
        status = QUALITY_COMPLETE_WITH_WARNINGS
    else:
        status = QUALITY_COMPLETE

    # Why the reference list is not a complete answer. Codes only - never
    # document content - so the record is safe to persist and log.
    not_authoritative_because = [
        cause
        for cause, applies in (
            ("references_field_failed", "references" in field_failures),
            ("deterministic_fallback", fallback),
            ("ai_extraction_failed", ai_failure),
            ("no_core_metadata", no_core),
            ("source_text_incomplete", source_incomplete),
            ("source_text_truncated", source_truncated),
        )
        if applies
    ]
    references_authoritative = not not_authoritative_because
    return {
        "status": status,
        "degraded": degraded,
        # The whole result is a guess or partial read, not just one field:
        # it may fill gaps but must not replace values already stored. A clean
        # AI read of a letter with no header (no_core) is not a guess; empty
        # values are never written anyway.
        "source_degraded": fallback or ai_failure or source_incomplete,
        "references_authoritative": references_authoritative,
        "references_not_authoritative_because": not_authoritative_because,
        "field_failures": field_failures,
        "missing_core_fields": missing_core,
        "warnings": warnings,
        "metadata_source": metadata_source,
    }


def reference_sync_skip_record(quality: Mapping[str, Any]) -> Dict[str, Any]:
    """The audit record for a run whose references were not synchronised.

    Reason codes only; no reference text, letter number or other content.
    """
    return {
        "stage": "reference_sync",
        # Links are never removed by such a run; with `added_only` the
        # references it did read were linked additively.
        "action": "added_only" if references_may_add(quality) else "preserved_existing",
        "reason": "reference_extraction_not_authoritative",
        "causes": list(quality.get("references_not_authoritative_because") or []),
    }


def references_may_add(quality: Mapping[str, Any]) -> bool:
    """A non-authoritative list whose entries may still be linked additively.

    Only when the sole reason is that the model saw just the head of the text:
    what it read there is a real reading, it simply may have missed later
    references. Any other cause (a failed or unreadable References field,
    the fallback, an AI failure, pages missing from the middle) makes the
    entries themselves doubtful, so nothing is added.
    """
    return list(quality.get("references_not_authoritative_because") or []) == [
        "source_text_truncated"
    ]


def merge_reference_lists(stored: Any, new: Iterable[Any]) -> List[Any]:
    """The stored reference list plus any new entries it does not hold.

    Used for an additive (non-authoritative) run: nothing already stored is
    dropped, and an entry is added once, keyed by its normalised number.
    """
    from .falkor_graph_service import normalize_letter_code

    def _key(entry: Any) -> str:
        if isinstance(entry, Mapping):
            value = entry.get("letterNo") or entry.get("letter_no") or entry.get("raw") or ""
        else:
            value = entry
        return normalize_letter_code(str(value or ""))

    merged: List[Any] = list(stored) if isinstance(stored, list) else []
    seen = {_key(entry) for entry in merged}
    for entry in new or []:
        key = _key(entry)
        if key and key not in seen:
            merged.append(entry)
            seen.add(key)
    return merged


def stored_references_authoritative(stored: Mapping[str, Any]) -> bool:
    """Whether a document's stored ``reference`` list may be treated as complete.

    Documents processed before ``metadata_quality`` existed carry no marker and
    keep the previous behaviour.
    """
    quality = stored.get("metadata_quality") if isinstance(stored, Mapping) else None
    if not isinstance(quality, Mapping):
        return True
    return bool(quality.get("references_authoritative", True))


def merge_degraded_snapshot(
    updates: Dict[str, Any],
    stored: Optional[Mapping[str, Any]],
    quality: Optional[Mapping[str, Any]],
) -> bool:
    """Merge, rather than replace, the ``metadata`` snapshot on a degraded run.

    A clean run's snapshot is authoritative and replaces the stored one. A
    degraded run's snapshot is missing whatever failed, so replacing would
    erase the previous record of those fields. Returns True when merged.
    """
    if not quality or not quality.get("degraded") or not stored:
        return False
    previous = stored.get("metadata")
    current = updates.get("metadata")
    if not isinstance(previous, Mapping) or not isinstance(current, dict):
        return False
    # On a degraded run an empty value means "not read", not "absent".
    updates["metadata"] = {
        **previous,
        **{key: value for key, value in current.items() if not _is_empty(value)},
    }
    return True


# --- Human edits --------------------------------------------------------------

#: Human-editable document field -> the keys it owns in a reprocessing update
#: (top level) and in the stored ``metadata`` snapshot.
HUMAN_EDITABLE_FIELDS: Dict[str, Dict[str, Tuple[str, ...]]] = {
    # PUT /documents/{id}
    "letterNo": {"top": ("letterNo", "letterNoNormalized"), "snapshot": ("letter_no",)},
    "date": {"top": ("date",), "snapshot": ("date",)},
    "subject": {"top": ("subject",), "snapshot": ("subject",)},
    "from": {"top": ("from",), "snapshot": ("from_company",)},
    "to": {"top": ("to",), "snapshot": ("to_company",)},
    # PATCH /documents/{id}/summary-metadata
    "asset_type": {"top": ("asset_type",), "snapshot": ("asset_type",)},
    "location": {"top": ("location",), "snapshot": ("location",)},
    "work_type": {"top": ("work_type",), "snapshot": ("work_type",)},
    "issue_nature": {"top": ("issue_nature",), "snapshot": ("issue_nature",)},
    "claim_category": {"top": ("claim_category",), "snapshot": ("claim_category",)},
    "alleged_responsibility": {
        "top": ("alleged_responsibility",),
        "snapshot": ("alleged_responsibility", "responsibility"),
    },
    "keywords": {"top": ("keywords",), "snapshot": ("keywords",)},
    "additional_keywords": {"top": ("additional_keywords",), "snapshot": ("additional_keywords",)},
    "extracted_tags": {"top": ("extracted_tags",), "snapshot": ("tags",)},
    "extracted_subTags": {"top": ("extracted_subTags",), "snapshot": ("subTags", "sub_tags")},
}

#: Every field the summary-metadata PATCH can write. A document edited there
#: before per-field markers existed carries only the document-level
#: ``manual_summary_metadata_override`` flag, so all of them are protected.
SUMMARY_METADATA_FIELDS: Tuple[str, ...] = (
    "asset_type",
    "location",
    "work_type",
    "issue_nature",
    "claim_category",
    "alleged_responsibility",
    "keywords",
    "additional_keywords",
    "extracted_tags",
    "extracted_subTags",
)

#: Top-level keys in a summary-metadata update -> the human-editable field.
_TOP_KEY_TO_FIELD: Dict[str, str] = {
    key: field for field, keys in HUMAN_EDITABLE_FIELDS.items() for key in keys["top"]
}


def effective_human_edited_fields(stored: Mapping[str, Any]) -> List[str]:
    """The fields reprocessing must not overwrite for this stored document."""
    recorded = stored.get("human_edited_fields")
    fields = {str(item) for item in recorded or [] if str(item) in HUMAN_EDITABLE_FIELDS}
    if stored.get("manual_summary_metadata_override") and recorded is None:
        fields.update(SUMMARY_METADATA_FIELDS)
    return sorted(fields)


def merge_human_edited_fields(stored: Mapping[str, Any], edited_keys: Iterable[str]) -> List[str]:
    """The marker list after a human edit touching ``edited_keys``.

    Keys may be top-level document keys or ``metadata.*`` paths; unknown keys
    are ignored.
    """
    fields = set(effective_human_edited_fields(stored))
    for key in edited_keys:
        name = str(key)
        if name.startswith("metadata."):
            continue
        field = _TOP_KEY_TO_FIELD.get(name) or (name if name in HUMAN_EDITABLE_FIELDS else None)
        if field:
            fields.add(field)
    return sorted(fields)


def _snapshot_form(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.strftime("%d-%m-%Y")
    return value


def protect_human_edited_fields(
    updates: Dict[str, Any],
    stored: Optional[Mapping[str, Any]],
    *,
    dropped_keys: Optional[List[str]] = None,
) -> List[str]:
    """Drop from a reprocessing ``updates`` dict every key a person owns.

    The replacement ``metadata`` snapshot carries the person's value for those
    keys (the stored top-level field, which PUT edits write), so the snapshot
    never contradicts the protected field. Returns the protected field names
    that were present in ``updates``; top-level keys removed are appended to
    ``dropped_keys`` when given.
    """
    if not stored:
        return []
    protected: List[str] = []
    stored_snapshot = stored.get("metadata") if isinstance(stored.get("metadata"), Mapping) else {}
    new_snapshot = updates.get("metadata") if isinstance(updates.get("metadata"), dict) else None
    for field in effective_human_edited_fields(stored):
        keys = HUMAN_EDITABLE_FIELDS[field]
        touched = False
        for key in keys["top"]:
            if key in updates:
                updates.pop(key)
                touched = True
                if dropped_keys is not None:
                    dropped_keys.append(key)
        if new_snapshot is not None:
            human_value = stored.get(keys["top"][0])
            for key in keys["snapshot"]:
                if not _is_empty(human_value):
                    replacement = _snapshot_form(human_value)
                elif key in stored_snapshot:
                    replacement = stored_snapshot[key]
                else:
                    if key in new_snapshot:
                        new_snapshot.pop(key)
                        touched = True
                    continue
                if new_snapshot.get(key) != replacement:
                    touched = touched or key in new_snapshot
                new_snapshot[key] = replacement
        if touched:
            protected.append(field)
    return protected


# --- Degraded sources and publication ------------------------------------------

#: Top-level document key written by reprocessing -> ParsedDocumentMetadata
#: attribute it comes from.
TOP_KEY_TO_METADATA_ATTR: Dict[str, str] = {
    "letterNo": "letter_no",
    "subject": "subject",
    "from": "from_company",
    "to": "to_company",
    "date": "date",
    "summary": "summary",
    "keywords": "keywords",
    "additional_keywords": "additional_keywords",
    "contractual_clauses": "contractual_clauses",
    "key_reply_points": "key_reply_points",
    "asset_type": "asset_type",
    "location": "location",
    "specific_area": "specific_area",
    "chainage_from": "chainage_from",
    "chainage_to": "chainage_to",
    "work_type": "work_type",
    "issue_nature": "issue_nature",
    "claim_category": "claim_category",
    "alleged_responsibility": "alleged_responsibility",
    "priority": "priority",
    "linked_event_suggested": "linked_event_suggested",
    "reference_chain": "reference_chain",
    "extracted_tags": "tags",
    "extracted_subTags": "sub_tags",
}

#: Metadata attribute -> its key in the stored snapshot (a by-alias dump).
_SNAPSHOT_KEY: Dict[str, str] = {"sub_tags": "subTags"}

#: Keys that travel with another key and are kept or replaced together.
_PAIRED_KEYS: Dict[str, Tuple[str, ...]] = {"letterNo": ("letterNoNormalized",)}


def keep_stored_values_on_degraded_source(
    updates: Dict[str, Any],
    stored: Optional[Mapping[str, Any]],
    quality: Optional[Mapping[str, Any]],
    *,
    dropped_keys: Optional[List[str]] = None,
) -> List[str]:
    """On a degraded *source* (OCR fallback, AI failure, nothing extracted),
    fill empty fields only; never replace a value already stored.

    A run whose extraction succeeded field by field is not affected: its valid
    fields are authoritative even when one other field failed.
    """
    if not quality or not quality.get("source_degraded") or not stored:
        return []
    kept: List[str] = []
    snapshot = updates.get("metadata") if isinstance(updates.get("metadata"), dict) else None
    for key, attr in TOP_KEY_TO_METADATA_ATTR.items():
        if key in updates and not _is_empty(stored.get(key)):
            updates.pop(key)
            kept.append(key)
            for paired in _PAIRED_KEYS.get(key, ()):
                updates.pop(paired, None)
            if snapshot is not None:
                # The snapshot must agree with the value that was kept.
                snapshot[_SNAPSHOT_KEY.get(attr, attr)] = _snapshot_form(stored.get(key))
    if dropped_keys is not None:
        dropped_keys.extend(kept)
    return kept


def metadata_for_publication(
    metadata: Any,
    stored: Optional[Mapping[str, Any]],
    kept_keys: Iterable[str],
) -> Any:
    """The extraction result with every kept stored value put back.

    Graph and evidence publication read the parsed metadata ahead of the
    document, so without this a human correction (or a clean value kept over a
    degraded run) would be published as the AI's value under a different key.
    """
    if not stored or metadata is None:
        return metadata
    overlay: Dict[str, Any] = {}
    for key in kept_keys:
        attr = TOP_KEY_TO_METADATA_ATTR.get(key)
        value = stored.get(key)
        if attr and not _is_empty(value):
            overlay[attr] = _snapshot_form(value)
    if not overlay:
        return metadata
    if hasattr(metadata, "model_copy"):
        return metadata.model_copy(update=overlay)
    clone = copy.copy(metadata)
    for attr, value in overlay.items():
        setattr(clone, attr, value)
    return clone


__all__ = [
    "CORE_FIELDS",
    "DEGRADED_METADATA_SOURCES",
    "HUMAN_EDITABLE_FIELDS",
    "QUALITY_COMPLETE",
    "QUALITY_COMPLETE_WITH_WARNINGS",
    "QUALITY_PARTIAL_EXTRACTION",
    "SUMMARY_METADATA_FIELDS",
    "assess_metadata_quality",
    "TOP_KEY_TO_METADATA_ATTR",
    "effective_human_edited_fields",
    "keep_stored_values_on_degraded_source",
    "metadata_for_publication",
    "merge_degraded_snapshot",
    "merge_human_edited_fields",
    "protect_human_edited_fields",
    "merge_reference_lists",
    "reference_sync_skip_record",
    "references_may_add",
    "source_text_complete_signal",
    "stored_references_authoritative",
]
