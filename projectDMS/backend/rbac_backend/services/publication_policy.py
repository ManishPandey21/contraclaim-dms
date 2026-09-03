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

import re
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

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


def _collection(db: Any, name: str) -> Any:
    """Reach a collection by either access style.

    Motor supports both `db["documents"]` and `db.documents`, and callers here
    are split between them. Picking one silently broke the other.
    """
    try:
        return db[name]
    except Exception:
        return getattr(db, name, None)


async def _find_by_id(db: Any, name: str, record_id: Any):
    """Look a record up by `_id`, trying every form the id may be stored as.

    Same reasoning as `resolve_canonical_document`: string-vs-ObjectId drift
    fails closed and silently, so both forms are tried rather than guessed.
    """
    collection = _collection(db, name)
    if collection is None:
        return None
    for key in document_id_candidates(record_id):
        found = await collection.find_one({"_id": key})
        if found:
            return found
    return None


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


async def blocked_document_ids(db: Any, document_ids: Any) -> set:
    """The document ids in this set whose canonical record is NOT consumable.

    ONE authority-aware retrieval filter, shared by every retrieval stack.
    `RetrievalService.search` had this logic; `ContractService.search_contracts`
    was a second, parallel implementation that never resolved authority at all,
    so a blocked or quarantined contract's clause text reached the drafting
    prompt, the pleading and the public API. Two services implementing the same
    containment differently is the defect; the cure is that they share the
    filter, not that each remembers to call `is_consumable`.

    Returns only ids POSITIVELY resolved and POSITIVELY judged unusable. An id
    absent from Mongo entirely is an orphaned point - a different problem -
    and is deliberately not returned, so this cannot silently swallow unrelated
    rows. Logical denial, not physical purge: a stale vector or clause left
    behind by a best-effort delete is contained here regardless.
    """
    ids = [str(doc_id) for doc_id in (document_ids or []) if doc_id]
    if not ids or getattr(db, "documents", None) is None:
        return set()

    query_ids: list = []
    for doc_id in ids:
        query_ids.extend(document_id_candidates(doc_id))

    blocked: set = set()
    cursor = db.documents.find({"_id": {"$in": query_ids}})
    async for doc in cursor:
        resolved = str(doc.get("_id") or doc.get("id") or "")
        if resolved and not is_consumable(doc):
            blocked.add(resolved)
    return blocked


async def resolvable_document_ids(db: Any, document_ids: Any) -> set:
    """The subset whose canonical Mongo document actually EXISTS.

    The companion to :func:`blocked_document_ids`, and deliberately a separate
    predicate rather than a widening of it. `blocked_document_ids` returns only
    ids it positively resolved and positively judged unusable, because for a
    lexical or vector hit an id absent from Mongo is an orphaned store row -
    a different problem, and dropping those silently would change unrelated
    behaviour.

    A GRAPH candidate is not that. The node outlives its document: nothing
    deletes a `(:Clause)` node and a `(:Letter)` node is shared identity keyed
    on `normCode` alone, so graph residue with no canonical support is the
    NORMAL state, not an anomaly. Absence of provenance is not absence of
    restriction, so every graph-derived consumer fences on this and fails
    CLOSED - including on a lookup error, which cannot be read as "everything
    resolves".

    One implementation, so the retrieval stack and the contract stack cannot
    drift apart the way they did before they shared `blocked_document_ids`.
    """
    ids = [str(doc_id) for doc_id in (document_ids or []) if doc_id]
    if not ids or _collection(db, "documents") is None:
        return set()

    query_ids: list = []
    for doc_id in ids:
        query_ids.extend(document_id_candidates(doc_id))

    found: set = set()
    try:
        cursor = db.documents.find({"_id": {"$in": query_ids}}, {"_id": 1})
        async for doc in cursor:
            resolved = str(doc.get("_id") or "")
            if resolved:
                found.add(resolved)
    except Exception:
        return set()  # cannot resolve -> nothing is provably resolvable
    return found


async def graph_codes_denied(db: Any, codes: Any) -> set:
    """Normalized letter codes whose graph support may NOT be consumed.

    A FalkorDB `(:Letter {normCode})` node is GLOBAL - it is shared by every
    document, in every tenant, that cites the same code (G32). So the node
    itself carries no authority; the question "may this graph fact be used?"
    can only be answered by the documents that support that code in Mongo.

    A code is consumable while at least one supporting document is currently
    consumable - the same last-known-good supporter rule the letter-deletion
    cascade already uses. A code with NO resolvable supporter is DENIED: graph
    provenance that cannot be resolved to an authoritative source must not
    influence legal output (fail closed on serving uncertainty).

    Returns the denied subset, so callers filter rather than re-implement.
    """
    from .falkor_graph_service import normalize_letter_code

    wanted = [str(code) for code in (codes or []) if code]
    if not wanted:
        return set()

    denied: set = set()
    for code in wanted:
        try:
            # ALL supporters, not one. `find_one` returns an arbitrary document in
            # Mongo natural order, so "is any supporter consumable?" became
            # order-dependent: a blocked supporter listed first denied a code that
            # a clean supporter legitimately supports. Iterate instead.
            #
            # `letters` stores the RAW `letter_no` - `letterNoNormalized` is only
            # ever written to `documents` - so that side is normalized in Python,
            # the same trap `has_consumable_supporter` documents and avoids.
            consumable_supporter = False
            saw_supporter = False

            documents = _collection(db, "documents")
            if documents is not None:
                cursor = documents.find({"letterNoNormalized": code})
                async for row in cursor:
                    saw_supporter = True
                    if is_consumable(row):
                        consumable_supporter = True
                        break

            if not consumable_supporter:
                letters = _collection(db, "letters")
                if letters is not None:
                    async for row in letters.find({}):
                        raw = row.get("letterNoNormalized") or row.get("letter_no") or row.get("letterNo")
                        if not raw or normalize_letter_code(str(raw)) != code:
                            continue
                        saw_supporter = True
                        # The `letters` register is an INDEX, not an authority
                        # source. Its rows carry no processing_status /
                        # duplicate_status / lifecycle_state, so is_consumable()
                        # takes its "absent state is allowed (legacy rows)"
                        # branch and returns True for EVERY live register row -
                        # a self-authorising supporter that silently overrode a
                        # blocked document, including one owned by another
                        # tenant. Authority must resolve to a backing document.
                        backing_id = (
                            row.get("document_id")
                            or row.get("documentId")
                            or row.get("source_document_id")
                        )
                        if not backing_id:
                            continue  # no resolvable authority -> not a supporter
                        backing = await _find_by_id(db, "documents", backing_id)
                        if backing is not None and is_consumable(backing):
                            consumable_supporter = True
                            break
        except Exception:
            denied.add(code)  # resolution error -> fail closed
            continue
        if not saw_supporter or not consumable_supporter:
            denied.add(code)
    return denied


async def resolve_canonical_document(
    db: Any,
    document_id: Any,
    *,
    session: Any = None,
):
    """The one place that turns an identifier into the canonical document.

    Every authority decision resolves through here so identifier handling is
    not reimplemented per caller - four sites had already drifted into querying
    `{"_id": <raw string>}` against an ObjectId-keyed collection, which fails
    closed and silently returns nothing.
    """
    if not document_id:
        return None
    session_kwargs = {"session": session} if session is not None else {}
    for key in document_id_candidates(document_id):
        try:
            found = await _collection(db, "documents").find_one(
                {"_id": key},
                **session_kwargs,
            )
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


#: What a `source_id` actually names, per `source_type`.
#:
#: This is the one registry; `arbitration_drafting/context.py` resolves selected
#: references through it too. Keeping a second, restated list of "document-ish"
#: types beside it is what produced two bugs at once: `letter` was assumed to be
#: an application record and skipped its authority check altogether, while
#: `clause` was assumed to be a document id and so never resolved at all.
#:
#: Order matters - the first collection that matches decides which authority
#: model applies, exactly as the reference resolver does it.
DERIVED_SOURCE_COLLECTIONS: Mapping[str, Tuple[str, ...]] = {
    "document": ("documents",),
    "expert_report": ("documents",),
    "letter": ("letters", "documents"),
    "clause": ("document_vectors", "contract_clauses"),
    "drawing": ("documents",),
    "claim": ("claims",),
    "variation": ("variations",),
    "payment_event": ("ipc_bills",),
    "bank_guarantee": ("bank_guarantees",),
    "chronology_event": ("matter_chronology_events",),
    "project_event": ("project_events",),
    "event_link": ("event_links",),
    "delay_event": ("matter_chronology_events",),
    "key_date": ("key_dates",),
    "programme_milestone": ("key_dates",),
    "issue_matrix": ("arbitration_issue_matrix",),
    "jurisdiction_check": ("arbitration_jurisdiction_matrix",),
}

#: Identifiers the application MINTS for content it authored itself, which
#: therefore name no stored record at all.
#:
#: The arbitration agreement is the case in point: when a case has no scoped
#: contract clauses, the clause agent synthesises one matrix row from
#: `arbitration_cases.arbitration_clause` - text a user typed onto the case -
#: under this identifier. Resolving it as a clause child id finds nothing and
#: denies, which withholds the jurisdictional basis of the pleading. Document
#: publication authority has nothing to say about it: there is no extraction.
#:
#: The pattern is anchored deliberately. A `startswith("case:")` test would let
#: any id beginning with that prefix opt out of authority entirely.
_SYNTHETIC_SOURCE_ID = re.compile(r"^case:[^:]+:arbitration_clause$")

#: Source types that are user/application authored and name no extracted
#: document - so document publication authority does not apply to them and
#: denying them would over-block legitimate content. `manual_fact` is prose the
#: user typed into a working draft.
INDEPENDENT_APPLICATION_TYPES = frozenset({"manual_fact"})

#: Event families whose PROVENANCE decides authority, per record rather than per
#: type. A chronology/project/delay event or an evidence link may carry span
#: text lifted from a document (writer stamps `source_document_id`, or
#: `source_entity_type=document` + `source_entity_id`), or it may be a manual
#: entry with no document behind it. The coarse type test cannot tell them
#: apart, so the record must be loaded and its provenance read.
EVENT_PROVENANCE_TYPES: Mapping[str, str] = {
    "chronology_event": "matter_chronology_events",
    "project_event": "project_events",
    "delay_event": "matter_chronology_events",
    "event_link": "event_links",
}


#: Per matrix slug: (source_type, id-field-in-row, derived-fields-to-withhold).
#: Only these three slugs carry text lifted from a document; the rest hold
#: matrix-authored application content (facts, findings, claim heads) that
#: document publication authority does not govern.
MATRIX_DERIVATIVE_FIELDS: Mapping[str, Tuple[str, str, Tuple[str, ...]]] = {
    "document-index": ("document", "source_id", ("relevance_note", "summary", "document_type")),
    "clause-matrix": ("clause", "clause_source_id", ("clause_text_excerpt",)),
    "chronology-matrix": ("chronology_event", "chronology_event_id", ("event",)),
}


async def safe_matrix_rows(db: Any, rows: Any, matrix_slug: str) -> list:
    """Withhold a matrix row's derived fields when its source is not publishable.

    ONE projection for the matrix publication boundary. The internal agent read
    (`_authorised_matrix_rows`) already gates these; the API read
    (`GET /cases/{id}/{matrix_slug}` -> `list_matrix_rows`) did not, so a blocked
    document's `relevance_note`/`summary`/`clause_text_excerpt`/`event` was
    serialized raw. Both must agree on eligibility.

    Resolution reuses the same per-record authority resolver as the ledger, so a
    matrix row cannot disagree with a ledger row about the same source. A slug
    that carries no document-derived field is returned unchanged - application
    content is not over-blocked. A copy is returned; storage is never mutated.
    """
    config = MATRIX_DERIVATIVE_FIELDS.get(matrix_slug)
    if not config:
        return [dict(row) for row in (rows or [])]
    source_type, id_field, derivative_fields = config
    resolved: list = []
    cache: Dict[str, bool] = {}
    for record in rows or []:
        row = dict(record)
        source_id = row.get(id_field) or row.get("source_id")
        key = f"{source_type}:{source_id}"
        if key not in cache:
            try:
                cache[key] = (await resolve_derived_authority(db, source_type, source_id)).consumable
            except Exception:
                cache[key] = False
        if not cache[key]:
            # Carry the decision, not just the redaction: evidence-status and
            # readiness consumers must be able to see that this row's source is
            # blocked, or a content-blanked row still counts as supported.
            row["authority_denied"] = True
            for field in derivative_fields:
                if field in row:
                    row[field] = ""
        resolved.append(row)
    return resolved


async def safe_event_records(
    db: Any,
    records: Any,
    derivative_fields: Tuple[str, ...],
    *,
    span_fields: Tuple[str, ...] = (),
    dict_fields: Tuple[str, ...] = (),
) -> list:
    """Withhold document-derived fields on event records the source now blocks.

    ONE projection for every event-serving consumer - the API routes, the
    timeline, the chronology export - instead of authority logic copied into
    each. The provenance resolver is already correct; the leak was that these
    read paths returned raw Mongo dicts.

    Independent metadata (dates, parties, ids, status) is always preserved: a
    blocked document must stay identifiable, and a manual event that shares the
    collection must be untouched. Only the named derivative fields - text lifted
    from or classified out of the document body - are blanked, and only when the
    record's originating document is currently non-consumable.

    A copy is returned; the stored record is never mutated. Physical staleness
    is fine - logical publishability is what this governs.
    """
    resolved: list = []
    cache: Dict[str, bool] = {}
    for record in records or []:
        row = dict(record)
        parent = event_document_provenance(row)
        if parent is None:
            # A document-ingest suggested link carries no direct provenance; its
            # document is reachable only through the project_event it points at
            # (`evidence_graph_service.py:823`). Make that one hop, or the whole
            # class of metadata links leaks blocked evidence_text/spans.
            for id_field, type_field in (("source_id", "source_type"), ("target_id", "target_type")):
                if str(row.get(type_field) or "").lower() == "project_event" and row.get(id_field):
                    try:
                        event = await _find_by_id(db, "project_events", row.get(id_field))
                    except Exception:
                        event = None
                    if event:
                        parent = event_document_provenance(event)
                        if parent:
                            break
        if parent:
            key = str(parent)
            if key not in cache:
                try:
                    cache[key] = (await resolve_document_authority(db, parent)).consumable
                except Exception:
                    cache[key] = False  # fail closed on resolution error
            if not cache[key]:
                for field in derivative_fields:
                    if field in row:
                        row[field] = ""
                for field in dict_fields:
                    # For a document-derived event the whole metadata dict is
                    # populated from the extraction (`evidence_graph_service.py:783`),
                    # so blank it to an empty dict of the same type - never leave
                    # a stray key, never break consumers expecting a mapping.
                    if isinstance(row.get(field), dict):
                        row[field] = {}
                for field in span_fields:
                    spans = row.get(field)
                    if isinstance(spans, list):
                        row[field] = [
                            {k: ("" if str(k).lower() in {"text", "excerpt", "snippet"} else v)
                             for k, v in (span or {}).items()}
                            if isinstance(span, dict) else span
                            for span in spans
                        ]
        resolved.append(row)
    return resolved


def event_document_provenance(record: Mapping[str, Any]) -> Optional[str]:
    """The originating document id of an event record, or None if manual.

    Two shapes exist and both are read from the actual writers:
    `source_document_id` (chronology/project events, `chronology.py:530`,
    `evidence_graph_service.py:149`) and `source_entity_type=document` with
    `source_entity_id` (event links, `evidence_graph_service.py:737`).
    """
    direct = record.get("source_document_id") or record.get("document_id")
    if direct:
        return str(direct)
    # project_events name the document via source_entity_*; event_links via
    # target_* (`evidence_graph_service.py:744`, arbitration `_link_source_document_id`).
    for type_field, id_field in (("source_entity_type", "source_entity_id"), ("target_type", "target_id")):
        if str(record.get(type_field) or "").lower() == "document" and record.get(id_field):
            return str(record.get(id_field))
    return None


def synthetic_case_clause_id(case_id: Any) -> str:
    """Mint the synthetic arbitration-agreement identifier for a case.

    Shared with the writer so the two cannot drift: if the writer's format
    changed, the exemption below would silently stop matching and the
    arbitration agreement would vanish from drafting again.
    """
    return f"case:{case_id}:arbitration_clause"


#: Collections whose rows ARE the canonical document.
CANONICAL_DOCUMENT_COLLECTIONS = frozenset({"documents"})

#: Partial records extracted FROM a document. They carry no authority fields of
#: their own (G34), so the parent has to be resolved.
DOCUMENT_CHILD_COLLECTIONS = frozenset({"document_vectors", "contract_clauses"})


def _is_document_governed(source_type: str) -> bool:
    """Could an id of this type name extraction-controlled content?"""
    collections = DERIVED_SOURCE_COLLECTIONS.get(source_type)
    if not collections:
        return True  # unknown origin - assume it might be
    return any(
        name in CANONICAL_DOCUMENT_COLLECTIONS or name in DOCUMENT_CHILD_COLLECTIONS
        for name in collections
    )


async def resolve_derived_authority(
    db: Any, source_type: Optional[str], source_id: Any
) -> AuthorityDecision:
    """Decide authority against the object the id ACTUALLY names.

    Three outcomes, deliberately distinguished:

    * KNOWN NON-DOCUMENT APPLICATION SOURCE - a claim, variation, IPC bill,
      guarantee or chronology event. Document publication authority has no
      jurisdiction over these, and denying them would delete legitimate matrix
      evidence from a live filing. Allowed without a lookup.
    * DOCUMENT-GOVERNED - the id resolves into `documents` (directly, or via a
      clause/vector child row). The canonical document decides.
    * UNKNOWN AUTHORITY ORIGIN - an unclassified type, or a document-ish id that
      resolves to nothing. Denied, because nothing proves it is safe.
    """
    resolved_type = str(source_type or "document")
    if resolved_type in INDEPENDENT_APPLICATION_TYPES:
        # User-authored prose that names no server record at all (manual_fact).
        # Document publication authority has no jurisdiction; RBAC governs it.
        return AuthorityDecision(
            document_id=source_id,
            consumable=True,
            reason="independent_application_record",
        )
    if isinstance(source_id, str) and _SYNTHETIC_SOURCE_ID.match(source_id):
        # Application-authored content under a minted identifier. There is no
        # extraction behind it, so there is no authority to resolve.
        return AuthorityDecision(
            document_id=source_id,
            consumable=True,
            reason="application_generated_source",
        )
    if resolved_type in EVENT_PROVENANCE_TYPES:
        # Per-record provenance: load the event, read where it came from.
        if not source_id:
            return AuthorityDecision(
                document_id=source_id, consumable=False, reason="no_source_id"
            )
        try:
            record = await _find_by_id(db, EVENT_PROVENANCE_TYPES[resolved_type], source_id)
        except Exception:
            return AuthorityDecision(
                document_id=source_id, consumable=False, reason="resolution_error"
            )
        if record is None:
            # The id names no event. For authority-controlled derivative content
            # we cannot prove it is safe, so fail closed.
            return AuthorityDecision(
                document_id=source_id, consumable=False, reason="event_not_found"
            )
        parent = event_document_provenance(record)
        if not parent:
            # Manual/application event - no document behind it.
            return AuthorityDecision(
                document_id=source_id,
                consumable=True,
                reason="independent_application_record",
            )
        return await resolve_document_authority(db, parent)
    if not _is_document_governed(resolved_type):
        return AuthorityDecision(
            document_id=source_id, consumable=True, reason="independent_application_record"
        )
    collections = DERIVED_SOURCE_COLLECTIONS.get(resolved_type)
    if not collections:
        return AuthorityDecision(
            document_id=source_id, consumable=False, reason="unknown_source_type"
        )
    if not source_id:
        return AuthorityDecision(
            document_id=source_id, consumable=False, reason="no_source_id"
        )

    for name in collections:
        try:
            if name in CANONICAL_DOCUMENT_COLLECTIONS:
                # One implementation of canonical resolution, not two.
                decision = await resolve_document_authority(db, source_id)
                if decision.reason == "document_not_found":
                    continue
                return decision
            record = await _find_by_id(db, name, source_id)
            if not record and name in DOCUMENT_CHILD_COLLECTIONS:
                # Callers sometimes hold the parent document id rather than the
                # chunk id; the reference resolver accepts both.
                collection = _collection(db, name)
                record = collection and await collection.find_one(
                    {"document_id": source_id}
                )
        except Exception:
            return AuthorityDecision(
                document_id=source_id, consumable=False, reason="resolution_error"
            )
        if not record:
            continue
        if name in DOCUMENT_CHILD_COLLECTIONS:
            return await resolve_document_authority(
                db, record.get("document_id") or record.get("documentId")
            )
        # Not extraction-controlled, so the processing axis has nothing to say
        # about it - but quarantine does. `letters` carries `lifecycle_state`
        # and letter_service already filters deleted rows out of its listings,
        # so returning True unconditionally here let a soft-deleted letter keep
        # feeding its derived matrix text into drafting. `is_consumable` adds
        # only the quarantine axis for a record with no processing_status, so
        # this cannot over-block a healthy one.
        return AuthorityDecision(
            document_id=source_id,
            consumable=is_consumable(record),
            reason="independent_application_record",
        )
    return AuthorityDecision(
        document_id=source_id, consumable=False, reason="source_not_found"
    )


async def consumable_derived_text(
    db: Any,
    row: Optional[Mapping[str, Any]],
    *fields: str,
    source_type: Optional[str] = None,
    source_id: Any = None,
) -> str:
    """Text DERIVED from an extracted document, gated by that document's authority.

    A persistent derivative cannot outlive the publication authority of its
    source. An arbitration index row's `relevance_note` is condensed from a
    document's summary/full_content at derivation time; once stored it looks
    like independent, human-approved matrix evidence, and the matrix approval
    flag says nothing about whether the underlying extraction is still trusted.

    Write-time guarding alone cannot fix that: authority changes AFTER the
    derivative exists. A document that was clean when indexed and later sent to
    human review leaves a stale note behind. So the check happens at read time,
    using the `source_id` these rows already carry.

    A MISSING `source_type` is read as `document`, and that is a traced fact
    rather than a convenient default: `document-index` is the only matrix whose
    writer puts a `source_id` in the row body at all
    (`agents/deterministic.py:162`), and `ArbitrationMatrixRow` is `extra="allow"`
    with no default for the field. A row carrying a `source_id` and no
    `source_type` therefore came from the document index.

    `source_type`/`source_id` may be supplied explicitly for rows that name
    their source under some other key. A clause-matrix row is the case that
    forced this: it stores a `contract_clauses` child id under
    `clause_source_id` and carries no `source_type` at all, so inferring from
    the row alone resolved a CHILD id as a DOCUMENT id and found nothing -
    denying every clause row while never consulting the document the clause
    text actually came from.
    """
    if not row:
        return ""

    source_id = (
        source_id
        or row.get("source_id")
        or row.get("clause_source_id")
        or row.get("source_document_id")
        or row.get("document_id")
    )
    decision = await resolve_derived_authority(
        db, source_type or row.get("source_type"), source_id
    )
    if not decision.consumable:
        return ""
    for field in fields:
        value = row.get(field)
        if value:
            return str(value)
    return ""
