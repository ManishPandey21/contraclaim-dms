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

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from ..models.processing_state import ProcessingState

#: The terminal verdicts AGAINST the latest run. These are the only states that
#: deny consumption.
#:
#: The policy is a deny-list on adverse judgement, not an allow-list on success,
#: and that inversion is the correction for G33. An allow-list of
#: {completed, stored_only} denied the main path outright, because
#: `metadata_extracted` - not `completed` - is where a normally processed
#: document actually comes to rest: `_checkpoint_extraction_attempt` writes
#: `completed` (document_service.py:1133), the caller then sets
#: `metadata_extracted` (:1687) and applies it last (:1777).
ADVERSE_STATES = frozenset(
    {
        ProcessingState.HUMAN_REVIEW_REQUIRED.value,
    }
)

#: The pipeline broke. NOT a judgement about the document.
#:
#: Both production writers of `failed` are operational: `_mark_processing_failure`
#: (document_service.py:1472) fires when retries are exhausted after an exception
#: in the job loop, and :1927 is a bare `except Exception` around the whole run.
#: An OCR crash, a Mongo timeout and an S3 error all land here.
#:
#: Treating that as adverse retracted last-known-good content because a worker
#: died, which contradicts the lifecycle: only an outcome ABOUT THE CONTENT may
#: invalidate a previous publication. A failure to reach a verdict is not a
#: verdict.
OPERATIONAL_STATES = frozenset({ProcessingState.FAILED.value})

#: States that mean the pipeline is still working. Under the last-known-good
#: lifecycle these do NOT retract a previous publication: a reprocess starting
#: is not evidence the existing content is bad, only an outcome is. Retries are
#: routine (`retrying` is written on two paths, max_attempts=3) and a crashed
#: worker can strand a document here, so retracting on entry would turn ordinary
#: operations into availability outages.
IN_FLIGHT_STATES = frozenset(
    {
        "queued",
        "processing",
        "retrying",
        ProcessingState.PARTIALLY_PROCESSED.value,
    }
)

#: States that mean the latest run finished without an adverse verdict.
SETTLED_STATES = frozenset(
    {
        "metadata_extracted",
        "skipped",
        ProcessingState.COMPLETED.value,
        ProcessingState.STORED_ONLY.value,
    }
)

#: Every value production code is known to write. Vocabulary drift is caught by
#: a test (`test_no_unclassified_state_is_written_by_production_code`), not by
#: denying unknown values at runtime - denying them at runtime is precisely what
#: hid the main path in G33.
KNOWN_STATES = (
    ADVERSE_STATES | OPERATIONAL_STATES | IN_FLIGHT_STATES | SETTLED_STATES
)

#: Retained for callers that still import it; equal to the adverse set.
BLOCKED_STATES = ADVERSE_STATES

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

    Content is authoritative UNLESS something has judged it adverse. Only a
    terminal verdict against the latest run - human review or failure - denies
    consumption.

    Everything else is either settled without objection or still in flight, and
    under the last-known-good lifecycle an in-flight run does not retract the
    previous publication.

    An unrecognised state is ALLOWED, which reverses the earlier rule. Denying
    unknown values looks like the safe default and is not: the value that a
    healthy document rests in (`metadata_extracted`) was itself unrecognised,
    so the "safe" default silently hid every successfully processed document
    from drafting, planning, arbitration, chronology and retrieval. Drift is
    caught at build time by
    `test_no_unclassified_state_is_written_by_production_code`, which fails when
    production starts writing a value nobody has classified - a visible failure
    instead of a silent outage.

    An absent state is allowed too: `processing_status` postdates most of the
    corpus, and a legacy row acquires modern semantics the moment it is
    reprocessed.
    """
    if not document:
        return False

    # Quarantine is a second, independent axis. Duplicate detection marks a
    # confirmed duplicate with `duplicate_status`/`lifecycle_state` and never
    # touches `processing_status`, which by then already reads
    # `metadata_extracted` from the extraction pass that ran moments earlier.
    # Reading only the processing axis therefore declared a quarantined
    # duplicate fully authoritative - the subsystem that decided "do not treat
    # this as real" was invisible to the gate every consumer trusts.
    if str(document.get("duplicate_status") or "") == "duplicate":
        return False
    if str(document.get("lifecycle_state") or "") in {"duplicate", "deleted"}:
        return False

    status = document.get("processing_status")
    if status is None or status == "":
        return True

    return str(status) not in ADVERSE_STATES


def is_publication_blocked(document: Optional[Mapping[str, Any]]) -> bool:
    """The writer-side view: may this document be (re)published?

    Defined as the exact complement of `is_consumable` rather than as a second
    reimplementation of the same rules. An earlier version restated them and
    immediately drifted: it omitted the quarantine axis, so a confirmed
    duplicate was neither consumable NOR blocked, and any writer trusting it as
    the complement would have happily republished quarantined content.

    Deriving it removes the possibility of that drift instead of asserting the
    absence of it in a docstring.
    """
    return not is_consumable(document)


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


async def has_consumable_supporter(db: Any, norm_code: str) -> bool:
    """Is any consumable document still behind this graph node?

    FalkorDB merges letter nodes on ``normCode`` alone and stores no
    ``document_id``, so the graph cannot say which document contributed a fact.
    Two documents - in different organisations - collapse onto one node, and
    `ON MATCH SET` lets the later writer overwrite the earlier one's properties.

    Attribution therefore has to come from Mongo, keyed by the same code. That
    is already how the letter-deletion cascade decides whether a node is safe to
    remove ("still owned by another live record"); this adds the publication
    state to that rule, because a supporter that nobody may consume is not a
    reason to keep serving a fact.

    Deliberately NOT organisation-scoped. The node is global, so the question is
    global too: while any tenant has a consumable document behind this code, the
    node stays and stays usable. Scoping the lookup to one organisation would
    let one tenant's invalidation delete another tenant's knowledge - the exact
    failure the shared-key design makes possible.
    """
    if not db or not norm_code:
        return False

    # `documents` stores the normalized code; `letters` stores the RAW
    # `letter_no` and has no normalized column - querying a
    # `letter_no_normalized` field that is written nowhere would silently find
    # no supporter and let a node another live letter depends on be deleted.
    from .falkor_graph_service import normalize_letter_code

    try:
        cursor = db["documents"].find(
            {"letterNoNormalized": norm_code, "lifecycle_state": {"$ne": "deleted"}}
        )
        async for record in cursor:
            if is_consumable(record):
                return True
    except Exception:
        # Cannot establish support. Fail SAFE for deletion: report a supporter
        # so nothing is destroyed on the strength of a failed lookup.
        return True

    try:
        cursor = db["letters"].find({})
        async for record in cursor:
            raw = record.get("letter_no") or record.get("letterNo")
            if not raw:
                continue
            if normalize_letter_code(str(raw)) != norm_code:
                continue
            if is_consumable(record):
                return True
    except Exception:
        return True

    return False


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


@dataclass(frozen=True)
class AuthorityDecision:
    """Why a document's content may or may not be used, not just whether."""

    document_id: Optional[str]
    consumable: bool
    reason: str


async def resolve_document_authority(db: Any, document_id: Optional[str]):
    """Resolve authority from the CANONICAL document record.

    The reason this exists: `is_consumable(record)` is vacuous when the record
    has no authority fields. `document_vectors` and `contract_clauses` carry no
    `processing_status`, so the guard returned True for a clause belonging to a
    blocked document and the caller fell through to raw text (G34). A guard that
    runs but reads the wrong record is equivalent to no guard.

    Downstream records carry a `document_id`. That is the only thing they can be
    trusted for, so authority is resolved from it rather than from whatever
    dictionary the caller happens to hold.

    Fails CLOSED on a missing id, a missing document, or a lookup error. That is
    the opposite of `has_consumable_supporter`, deliberately: this decides
    whether to SERVE content, where uncertainty must deny, while that one
    decides whether to DELETE a shared node, where uncertainty must preserve.
    """
    if not document_id:
        return AuthorityDecision(None, False, "no_document_id")

    try:
        document = await resolve_canonical_document(db, document_id)
    except Exception:
        return AuthorityDecision(str(document_id), False, "resolution_error")

    if not document:
        return AuthorityDecision(str(document_id), False, "document_not_found")

    if str(document.get("duplicate_status") or "") == "duplicate" or str(
        document.get("lifecycle_state") or ""
    ) in {"duplicate", "deleted"}:
        return AuthorityDecision(str(document_id), False, "quarantined")

    status = str(document.get("processing_status") or "")
    if status in ADVERSE_STATES:
        return AuthorityDecision(
            str(document_id), False, "adverse_quality_judgement"
        )
    if status in OPERATIONAL_STATES:
        # The run broke without reaching a verdict; last-known-good stands.
        return AuthorityDecision(
            str(document_id), True, "operational_failure_last_known_good"
        )
    if not status:
        return AuthorityDecision(str(document_id), True, "legacy")
    if status in IN_FLIGHT_STATES:
        return AuthorityDecision(str(document_id), True, "in_flight_last_known_good")
    return AuthorityDecision(str(document_id), True, "settled")


def document_id_candidates(document_id: Any) -> list:
    """Every form this identifier might be stored as, most likely first.

    `documents._id` is ObjectId-keyed - `document_service` pops any supplied
    `_id` before `insert_one`, so Mongo always generates one - while callers
    almost always hold the string form from a URL or a payload. Querying with
    the raw string silently matches nothing.

    Returning candidates instead of guessing one form is deliberate. The
    previous single-shot version relied on real and non-real ids being
    syntactically distinguishable: a plain string that happened to be valid
    24-hex would convert to an ObjectId that matches a DIFFERENT document, or
    none at all, with no error either way. Trying both forms cannot do that.
    """
    candidates = [document_id]
    try:
        from bson import ObjectId

        oid = ObjectId(str(document_id))
        if oid not in candidates:
            candidates.append(oid)
    except Exception:
        pass
    return candidates


async def resolve_canonical_document(db: Any, document_id: Any):
    """The one place that turns an identifier into the canonical document.

    Every authority decision resolves through here so identifier handling is
    not reimplemented per caller - four sites had already drifted into querying
    `{"_id": <raw string>}` against an ObjectId-keyed collection, which fails
    closed and silently returns nothing.
    """
    if not document_id:
        return None
    for key in document_id_candidates(document_id):
        try:
            found = await db.documents.find_one({"_id": key})
        except Exception:
            raise
        if found:
            return found
    return None


def _as_mapping(document: Any) -> Optional[Mapping[str, Any]]:
    """Accept either a Mongo dict or a Pydantic/attr document object.

    Callers in letter_drafting hold model objects and reach fields with
    getattr, so a Mapping-only guard silently could not be applied there at
    all - which is part of why that package never imported this module.
    """
    if document is None or isinstance(document, Mapping):
        return document
    dump = getattr(document, "model_dump", None)
    if callable(dump):
        try:
            return dump()
        except Exception:
            pass
    return {
        key: getattr(document, key, None)
        for key in (
            "processing_status",
            "duplicate_status",
            "lifecycle_state",
            *_TEXT_FIELDS,
        )
    }


def consumable_text(document: Any) -> str:
    """authoritative_text for callers holding a model object rather than a dict."""
    return authoritative_text(_as_mapping(document))


def consumable_summary(document: Any) -> str:
    """authoritative_summary for callers holding a model object."""
    return authoritative_summary(_as_mapping(document))
