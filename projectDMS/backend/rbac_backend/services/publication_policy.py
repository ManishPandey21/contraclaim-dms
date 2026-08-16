"""One server-side decision about whether a document's text may be consumed.

Three things are deliberately distinct:

    content exists
        != content is authoritative
            != content is retrieval/drafting eligible

The publication barrier in `DocumentProcessor` already withholds Qdrant vectors
and graph publication for a document with unresolved blocking quality findings.
It does not withhold the canonical Mongo text: `_upsert_document_metadata`
writes `ocrText` regardless, and several consumers read that field directly as
the document body. A review-required document was therefore still draftable.

This module is the guard, and it is deliberately *one* predicate rather than a
flag each consumer has to remember to check. A control that every caller must
opt into is how `EXTRACTION_FALLBACK_ENABLED` ended up with no readers at all.

It is driven by the authoritative state the pipeline already persists -
`documents.processing_status` - so there is no new field to keep in sync and no
second source of truth.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from ..models.processing_state import ProcessingState

#: States in which extracted content may be treated as authoritative.
#:
#: `STORED_ONLY` is included deliberately: an archive is stored without
#: extraction, so it has no extracted text to leak - and excluding it would
#: block archives from ordinary listing behaviour for no safety gain.
CONSUMABLE_STATES = frozenset(
    {
        ProcessingState.COMPLETED.value,
        ProcessingState.STORED_ONLY.value,
    }
)

#: Everything else is non-consumable, including the in-flight states. Content
#: that is still being processed has not passed anything yet, and "processing
#: incomplete" must not read as "processing passed".
BLOCKED_STATES = frozenset(
    {
        ProcessingState.QUEUED.value,
        ProcessingState.PROCESSING.value,
        ProcessingState.PARTIALLY_PROCESSED.value,
        ProcessingState.HUMAN_REVIEW_REQUIRED.value,
        ProcessingState.FAILED.value,
    }
)

#: Fields that carry document body text, in precedence order.
#:
#: `summary` is included. It is *derived from* the extracted text by the same
#: pipeline pass, so withholding `ocrText` while serving `summary` withholds
#: nothing - an adversarial review found exactly that bypass in three consumers,
#: including two this policy had supposedly already fixed.
_TEXT_FIELDS = ("body", "full_text", "ocrText", "summary")

#: Derived fields that must be withheld alongside the body text.
_DERIVED_FIELDS = ("summary",)


def is_consumable(document: Optional[Mapping[str, Any]]) -> bool:
    """May this document's extracted content be used authoritatively?

    Fails closed on an unrecognised state: a value nobody has heard of is not a
    licence to treat content as verified.

    Fails *open* on an ABSENT state, and that asymmetry is deliberate.
    `processing_status` postdates most of the corpus, so treating a missing
    field as blocked would silently remove every historical document from
    drafting and retrieval - an outage dressed up as a safety control. An
    absent field means "predates this pipeline"; an unrecognised value means
    "something wrote a state we do not understand", which is a real signal.
    """
    if not document:
        return False

    status = document.get("processing_status")
    if status is None or status == "":
        # Legacy document from before the state existed. See docstring.
        return True

    return str(status) in CONSUMABLE_STATES


def authoritative_text(document: Optional[Mapping[str, Any]]) -> str:
    """The document body, or empty when the document may not be consumed.

    Consumers that need the text for drafting, planning, arbitration or any
    other downstream reasoning should read it through here rather than reaching
    for `ocrText` directly, so the containment decision is made in one place.
    """
    if not is_consumable(document):
        return ""

    for field in _TEXT_FIELDS:
        value = (document or {}).get(field)
        if value:
            return str(value)
    return ""


def authoritative_summary(document: Optional[Mapping[str, Any]]) -> str:
    """The document summary, or empty when the document may not be consumed.

    A separate accessor because callers legitimately want the summary *instead
    of* the body - a short citation snippet, a planning hint. They must not get
    it for a blocked document: the summary is generated from the same extracted
    text and leaks the same unverified content in condensed form.

    Note for callers: prefer

        authoritative_summary(doc) or authoritative_text(doc)

    over ``doc.get("summary") or authoritative_text(doc)``. The latter
    short-circuits on a truthy summary and never evaluates the guard at all,
    which is how this bypass survived its first fix.
    """
    if not is_consumable(document):
        return ""

    for field in _DERIVED_FIELDS:
        value = (document or {}).get(field)
        if value:
            return str(value)
    return ""
