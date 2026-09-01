from __future__ import annotations

import hashlib
from textwrap import shorten
from typing import Any, Dict, Iterable, List, Optional

from fastapi import HTTPException, status

from ...models.arbitration_drafting import ArbitrationSelectedReferenceCreate
from ...models.contract_document import CurrentState
from ..contract_scope_resolver import (
    ProjectEvidenceUniverse,
    resolve_authorized_project_universe,
)
from ..document_relationship_service import DocumentRelationshipService
from ..evidence_graph_service import EvidenceGraphService
from .matrix_evidence_authority import matrix_clause_fence
from .matrix_registry import MATRIX_COLLECTIONS

#: A bound on the clause rows read out of an ALREADY ELIGIBLE universe, and on
#: the rows that reach the ledger. Both apply after the canonical fence, never
#: before it: bounding first and authorising afterwards lets ineligible rows
#: spend the window and starve a lawful clause out of a pleading entirely - a
#: leak that presents as an outage.
CONTRACT_CLAUSE_CANDIDATE_LIMIT = 60
CONTRACT_CLAUSE_SOURCE_LIMIT = 5


VERIFIED_SOURCE_STATUSES = {
    "approved",
    "edited_verified",
    "ready",
    "selected",
    "supported",
    "user_verified",
    "valid",
    "verified",
}

REVIEW_ONLY_SOURCE_STATUSES = {
    "ai_suggested",
    "draft",
    "needs_review",
    "pending",
    "under_review",
}


async def _collect(cursor: Any) -> List[Dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return [dict(item) for item in await cursor.to_list(length=None)]
    return [dict(item) async for item in cursor]


def condense(value: Any, width: int = 700) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    if len(text) <= width:
        return text
    return shorten(text, width=width, placeholder="...")


def source_hash(source: Dict[str, Any]) -> str:
    metadata = source.get("metadata") or {}
    raw = "|".join(
        [
            str(source.get("source_type") or ""),
            str(source.get("source_id") or ""),
            str(source.get("citation") or ""),
            str(source.get("snippet") or ""),
            ",".join(str(page) for page in source.get("page_numbers") or []),
            str(source.get("verification_status") or ""),
            str(metadata.get("matrix_row_id") or ""),
            str(metadata.get("authoritative_revision_id") or ""),
            str(metadata.get("authoritative_sha256") or ""),
            str(metadata.get("authoritative_updated_at") or ""),
        ]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _first_present(row: Dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, "", []):
            return value
    return None


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _string_list(value: Any) -> List[str]:
    return [str(item) for item in _as_list(value) if item not in (None, "")]


def _status_values(row: Dict[str, Any]) -> set[str]:
    return {
        str(row.get(key) or "").strip().lower()
        for key in ["verification_status", "approval_status", "human_approval_status", "readiness_status", "status"]
        if row.get(key) is not None
    }


def _is_rejected(row: Dict[str, Any]) -> bool:
    return bool(_status_values(row) & {"duplicate", "rejected", "superseded"})


def _is_verified_source(row: Dict[str, Any], *, include_review_sources: bool = False) -> bool:
    statuses = _status_values(row)
    if _is_rejected(row):
        return False
    if statuses & VERIFIED_SOURCE_STATUSES:
        return True
    if include_review_sources and (not statuses or statuses & REVIEW_ONLY_SOURCE_STATUSES):
        return True
    return False


def _allowed_use(value: Any, fallback: str = "fact") -> str:
    raw = str(value or fallback).strip().lower()
    if raw in {
        "annexure",
        "background",
        "chronology",
        "clause",
        "expert",
        "fact",
        "notice",
        "quantum",
    }:
        return raw
    if raw.startswith("soc_") or raw.startswith("sod_") or raw.startswith("rejoinder_") or raw == "counterclaim":
        return "chronology"
    return fallback


def _numeric_amount(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    import re as _re

    cleaned = _re.sub(r"[^0-9.\-]", "", str(value))
    if cleaned in {"", "-", "."}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _money(value: Any, currency: Optional[str] = None) -> Optional[str]:
    if value in (None, ""):
        return None
    return f"{currency or ''} {value}".strip()


from ..publication_policy import (
    authoritative_summary,
    authoritative_text,
    consumable_derived_text,
    document_id_candidates,
    is_consumable,
    resolve_derived_authority,
    resolve_document_authority,
)


#: Fields the reference hydrator computes for SAFETY rather than for display.
#:
#: `_ledger_row` rebuilds the ledger row from a fixed key literal, so a field
#: that is not copied across simply ceases to exist - and a downstream guard on
#: it becomes unreachable without any error. That is how `authority_denied`
#: spent its whole life as dead code while looking like an implemented control.
#:
#: A test asserts every name here survives construction, so adding a safety
#: field without wiring it through fails the build instead of going quiet.
LEDGER_SAFETY_FIELDS = ("authority_denied",)

#: Chronology fields that are RECORDED ABOUT a document rather than EXTRACTED
#: FROM it, so publication authority does not reach them. Dates, parties, page
#: numbers, letter numbers, classifications and ids keep a withheld event
#: identifiable and reviewable; without them a blocked row is unciteable.
#:
#: Deliberately an allowlist. `metadata["event"]` used to be `{**row, **event}`,
#: which re-published the very span the label and snippet had just withheld -
#: and a denylist over that spread would fail open the moment a writer added a
#: field. Extracted text (`event`, `title`, `description`, `source_spans`) is
#: absent here on purpose and re-added only after an authority decision.
SAFE_EVENT_METADATA_FIELDS = (
    "date",
    "document_ref",
    "party_responsible",
    "clause",
    "issue_link",
    "claim_link",
    "pleading_use",
    "chronology_id",
    "chronology_event_id",
    "evidence",
)

#: The same rule for fields copied off the chronology event itself.
SAFE_CHRONOLOGY_EVENT_FIELDS = (
    "event_date",
    "date_text",
    "date_type",
    "source_document_id",
    "source_document_name",
    "source_page",
    "letter_no",
    "from_party",
    "to_party",
    "contract_clauses",
    "issue_tags",
    "claim_heads",
    "event_classification",
    "impact_type",
    "confidence_score",
    "verification_status",
    "pleading_use",
    "related_document_ids",
)


def _safe_event_metadata(
    row: Dict[str, Any],
    event: Optional[Dict[str, Any]],
    gated_event: Any,
    gated_description: Any,
) -> Dict[str, Any]:
    """Construct the published event metadata; never filter a raw spread.

    The two text fields are re-admitted only in the form the authority check
    already returned, so this cannot disagree with the label and snippet built
    from the same decision a few lines above.
    """
    metadata: Dict[str, Any] = {
        key: row.get(key) for key in SAFE_EVENT_METADATA_FIELDS if row.get(key) not in (None, "")
    }
    for key in SAFE_CHRONOLOGY_EVENT_FIELDS:
        value = (event or {}).get(key)
        if value not in (None, "", []):
            metadata[f"event_{key}"] = value
    if gated_event:
        metadata["event"] = gated_event
    if gated_description:
        metadata["event_description"] = gated_description
    manual_notes = (event or {}).get("manual_notes")
    if manual_notes:
        # Authored by counsel, not extracted - authority has no jurisdiction.
        metadata["event_manual_notes"] = manual_notes
    return metadata

#: Every flag `_annotate_source_quality` can emit.
#:
#: Exported because the filing gate has to decide which of these block a filing,
#: and it must decide over the REAL vocabulary. It previously restated its own
#: set - `manual_or_unverified`, `source_drift`, `stale` - none of which any
#: code produces, so the only per-source safety branch in the filing gate was
#: dead. Deriving the gate's set from this one means a new flag cannot be
#: invented without a filing decision being made.
PRODUCIBLE_QUALITY_FLAGS = frozenset(
    {
        "missing_citation",
        "missing_snippet",
        "authority_denied",
        "missing_exhibit_id",
        "missing_source_link",
        "not_verified_for_filing",
        "user_supplied",
        "unverified_ai_suggestion",
    }
)


class ArbitrationContextBuilder:
    def __init__(self, db: Any) -> None:
        self.db = db

    async def build(
        self,
        draft: Dict[str, Any],
        references: List[Dict[str, Any]],
        claim_heads: List[Dict[str, Any]],
        paragraph_responses: List[Dict[str, Any]],
        current_user: Any,
        *,
        include_unverified_graph_links: bool = False,
    ) -> Dict[str, Any]:
        context_warnings: List[str] = []
        references = await self.rehydrate_selected_references(
            draft, references, current_user=current_user
        )
        source_ledger = [self._ledger_row(ref, idx) for idx, ref in enumerate(references, start=1)]
        source_ledger.extend(
            await self._case_workspace_sources(
                draft,
                len(source_ledger),
                current_user=current_user,
                include_review_sources=include_unverified_graph_links,
                context_warnings=context_warnings,
            )
        )
        source_ledger.extend(
            await self._contract_clause_sources(
                draft,
                current_user,
                len(source_ledger),
                context_warnings,
            )
        )
        source_ledger.extend(
            await self._verified_graph_sources(
                draft,
                include_unverified_graph_links,
                len(source_ledger),
                context_warnings,
            )
        )
        source_ledger = self._dedupe(source_ledger)
        await self._annotate_source_quality(source_ledger, context_warnings)
        matrix_context = self._matrix_context(source_ledger)
        self._expert_consistency_warnings(matrix_context, context_warnings)
        missing = self._missing_evidence(draft, source_ledger, claim_heads, paragraph_responses)
        return {
            "draft": draft,
            "source_ledger": source_ledger,
            "matrix_context": matrix_context,
            "claim_heads": claim_heads,
            "paragraph_responses": paragraph_responses,
            "missing_evidence": missing,
            "context_warnings": context_warnings,
        }

    async def rehydrate_selected_references(
        self,
        draft: Dict[str, Any],
        references: List[Dict[str, Any]],
        *,
        current_user: Any,
    ) -> List[Dict[str, Any]]:
        """Resolve selected IDs against scoped server records and discard client prose.

        Manual facts remain available for working drafts, but they are explicitly
        marked unverified and are blocked by filing validation. Every other
        selected reference must resolve to an authoritative scoped record or an
        approved matrix revision belonging to the linked case.

        `current_user` is REQUIRED and keyword-only. A selected CLAUSE is
        resolved inside the evidence universe canonical authority gives THIS
        ACTOR, so a hydration without a principal is not a weaker check - it is
        an unanswerable question. Keyword-only and mandatory means a caller that
        forgets fails loudly here rather than silently hydrating against the
        draft's own workspace stamp, which is what this path used to do.
        """

        # The canonical universe is resolved at most ONCE per call, lazily: a
        # draft may carry many selections, and a page of them must not turn into
        # a page of applicability enumerations.
        fence: Dict[str, Any] = {}
        hydrated: List[Dict[str, Any]] = []
        for reference in references or []:
            source_type = str(reference.get("source_type") or "")
            if source_type == "manual_fact":
                hydrated.append(
                    {
                        **reference,
                        "metadata": {
                            **(reference.get("metadata") or {}),
                            "source_origin": "manual_user_input",
                            "verification_status": "needs_review",
                        },
                    }
                )
                continue
            matrix_row_id = (reference.get("metadata") or {}).get("matrix_row_id")
            if matrix_row_id:
                matrix_reference = await self._rehydrate_matrix_reference(
                    draft,
                    str(matrix_row_id),
                    source_type,
                    current_user=current_user,
                    fence=fence,
                )
                if matrix_reference:
                    hydrated.append({**matrix_reference, "_id": reference.get("_id"), "draft_id": reference.get("draft_id")})
                    continue
            authoritative = await self._rehydrate_direct_reference(
                draft, reference, current_user, fence
            )
            if not authoritative:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "message": "Selected arbitration source could not be verified in the current scope",
                        "source_type": source_type,
                        "source_id": reference.get("source_id"),
                    },
                )
            hydrated.append({**authoritative, "_id": reference.get("_id"), "draft_id": reference.get("draft_id")})
        return hydrated

    async def _rehydrate_matrix_reference(
        self,
        draft: Dict[str, Any],
        matrix_row_id: str,
        source_type: str,
        *,
        current_user: Any = None,
        fence: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Resolve a selection that names an APPROVED case matrix row.

        `current_user` and `fence` are supplied by
        `rehydrate_selected_references`, the only production caller. They
        default so that a caller which omits them resolves a CLAUSE row inside
        an EMPTY universe - i.e. refuses it - rather than inside the matrix
        row's own provenance. Absent authority denies; it never falls back.

        The fence object is memoised in the same per-hydration dict the direct
        reference path uses, so a page of matrix selections resolves the
        canonical universe once.
        """
        case_id = draft.get("case_id")
        if not case_id:
            return None
        matrix_fence = (fence if fence is not None else {}).setdefault(
            "matrix", matrix_clause_fence(self.db, draft, current_user)
        )
        scope = {
            "_id": matrix_row_id,
            "case_id": case_id,
            "deleted_at": {"$exists": False},
        }
        for slug, collection_name in MATRIX_COLLECTIONS.items():
            row = await self.db[collection_name].find_one(scope)
            if not row:
                continue
            if row.get("organization_id") and str(row.get("organization_id")) != str(draft.get("organization_id")):
                return None
            if row.get("project_id") and str(row.get("project_id")) != str(draft.get("project_id")):
                return None
            if not _is_verified_source(row):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "message": "Unapproved matrix rows cannot be selected as drafting evidence",
                        "matrix": slug,
                        "matrix_row_id": matrix_row_id,
                    },
                )
            if slug == "document-index":
                return {
                    "source_type": row.get("source_type") or "document",
                    "source_id": str(row.get("source_id")),
                    "label": row.get("title") or row.get("document_type") or row.get("exhibit_id") or "Case document",
                    "citation": row.get("exhibit_id") or row.get("letter_no") or row.get("title"),
                    # Derived from the source document's extracted text, so it
                    # is gated by that document's CURRENT authority - not by the
                    # matrix approval flag, which says nothing about extraction
                    # trust. Falls back to document_type, which is metadata.
                    "snippet": await consumable_derived_text(
                        self.db, row, "relevance_note", "summary"
                    ) or row.get("document_type"),
                    "page_numbers": row.get("page_numbers") or [],
                    "letter_no": row.get("letter_no"),
                    "allowed_use": row.get("allowed_use") or "fact",
                    "metadata": {
                        "matrix": slug,
                        "matrix_row_id": row.get("_id"),
                        "source_origin": "approved_matrix_revision",
                        "verification_status": "approved",
                        "exhibit_id": row.get("exhibit_id"),
                    },
                }
            if slug == "clause-matrix":
                if not await matrix_fence.admits_clause_row(row):
                    # HUMAN APPROVAL IS NOT LEGAL APPLICABILITY. The row says a
                    # human judged this clause relevant; only the canonical
                    # Contract Master universe says whether the instrument
                    # governs this project right now. Returning None takes the
                    # hydrator's existing door - `400 Selected arbitration
                    # source could not be verified in the current scope` - so a
                    # refused selection is never persisted and never appears as
                    # a rejected-but-recorded ledger row.
                    return None
                return {
                    "source_type": "clause",
                    "source_id": str(row.get("clause_source_id") or row.get("source_id") or row.get("_id")),
                    "label": row.get("topic") or row.get("clause_number") or "Clause matrix row",
                    "citation": row.get("clause_number") or row.get("topic"),
                    # Derived from the parent document, like the ledger path.
                    "snippet": await consumable_derived_text(
                        self.db,
                        row,
                        "clause_text_excerpt",
                        source_type="clause",
                        source_id=row.get("clause_source_id"),
                    ) or row.get("obligation_or_right"),
                    "page_numbers": row.get("page_numbers") or [],
                    "clause_number": row.get("clause_number"),
                    "allowed_use": "clause",
                    "metadata": {
                        "matrix": slug,
                        "matrix_row_id": row.get("_id"),
                        "source_origin": "approved_matrix_revision",
                        "verification_status": "approved",
                        "risk": row.get("risk"),
                    },
                }
            return {
                "source_type": source_type,
                "source_id": str(row.get("source_id") or row.get("_id")),
                "label": row.get("title") or row.get("claim_head") or row.get("issue") or row.get("topic") or slug,
                "citation": row.get("citation") or row.get("claim_no") or row.get("issue_no"),
                # `facts` is matrix content authored in-app; summary and
                # relevance_note are derived from the source document.
                "snippet": row.get("facts") or await consumable_derived_text(
                    self.db, row, "summary", "relevance_note"
                ),
                "page_numbers": row.get("page_numbers") or [],
                "allowed_use": row.get("allowed_use") or "fact",
                "metadata": {
                    "matrix": slug,
                    "matrix_row_id": row.get("_id"),
                    "source_origin": "approved_matrix_revision",
                    "verification_status": "approved",
                },
            }
        return None

    async def _selected_clause_universe(
        self,
        draft: Dict[str, Any],
        current_user: Any,
        fence: Dict[str, Any],
    ) -> Optional[List[str]]:
        """The eligible documents a selected CLAUSE may be resolved inside.

        The SAME canonical universe the automatic contract-evidence path
        consumes, resolved through the SAME seam - there is no second
        implementation of applicability, lifecycle, publication or projection
        currency here, and there must never be one.

        ``None`` means the question could not be ANSWERED; an empty list means
        nothing applicable governs this project. Both refuse every clause
        selection, and neither may widen: a selection cannot expand authority,
        so there is no broader lookup that would be safer than none.

        Memoised in ``fence`` for the duration of one hydration call.
        """
        if "eligible" not in fence:
            universe = await self._contract_evidence_universe(draft, current_user, [])
            fence["eligible"] = (
                None if universe is None else sorted(universe.eligible_document_ids)
            )
        return fence["eligible"]

    async def _rehydrate_direct_reference(
        self,
        draft: Dict[str, Any],
        reference: Dict[str, Any],
        current_user: Any = None,
        fence: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Resolve ONE selected reference against a server record.

        `current_user` and `fence` are supplied by
        `rehydrate_selected_references`, the only production caller. They
        default so that a caller which omits them resolves a CLAUSE inside an
        EMPTY universe - i.e. refuses it - rather than inside the draft's own
        workspace stamp. Absent authority denies; it never falls back.
        """
        source_type = str(reference.get("source_type") or "")
        source_id = str(reference.get("source_id") or "")
        if not source_id:
            return None
        eligible_documents: Optional[List[str]] = None
        if source_type == "clause":
            # USER SELECTION IS NOT AUTHORITY. A clause selection names a
            # contract instrument, and only the canonical Contract Master
            # universe decides whether that instrument governs this project
            # right now - not the row's ingest stamp, not the identifier the
            # client sent, not the fact that the UI offered it.
            #
            # The universe is resolved BEFORE any clause store is read, and it
            # is part of the QUERY below rather than a filter over its result.
            # An unauthorised chunk that is read is an unauthorised chunk one
            # refactor away from being kept: it has already been in memory
            # beside the ledger, the prompt and the input hash.
            #
            # Deliberately CLAUSE-scoped. A letter, exhibit, claim or event is
            # not a contract instrument and has no applicability aggregate;
            # fencing those here would delete lawful correspondence from live
            # pleadings, which is an outage dressed as a containment.
            eligible_documents = await self._selected_clause_universe(
                draft, current_user, fence if fence is not None else {}
            )
            if not eligible_documents:
                return None
        collection_names = {
            "document": ("documents",),
            "letter": ("letters", "documents"),
            "clause": ("document_vectors", "contract_clauses"),
            "claim": ("claims",),
            "variation": ("variations",),
            "payment_event": ("ipc_bills",),
            "bank_guarantee": ("bank_guarantees",),
            "chronology_event": ("matter_chronology_events",),
            "project_event": ("project_events",),
            "expert_report": ("documents",),
        }.get(source_type, ())
        for collection_name in collection_names:
            # Both fixes together, deliberately. The identifier bug (raw
            # string against ObjectId-keyed collections) was masking the scope
            # bug: almost nothing matched, so the conditional filter never had
            # the chance to widen. Fixing the id alone would have turned a
            # silent availability bug into a cross-tenant leak of
            # claim/variation/payment/bank-guarantee/chronology records, which
            # apply no document-authority check because they are independent
            # application records - tenant scope is their only gate.
            query: Dict[str, Any] = {
                "_id": {"$in": document_id_candidates(source_id)},
                "organization_id": draft.get("organization_id"),
                "project_id": draft.get("project_id"),
            }
            if eligible_documents is not None:
                query["document_id"] = {"$in": eligible_documents}
            record = await self.db[collection_name].find_one(query)
            if not record and source_type == "clause" and collection_name == "document_vectors":
                # The client may hold the PARENT document id rather than a chunk
                # id. That widens which row answers, never which documents are
                # eligible, so the fence is INTERSECTED with the requested id
                # instead of being replaced by it.
                query.pop("_id", None)
                query["document_id"] = {
                    "$in": [
                        candidate
                        for candidate in (eligible_documents or [])
                        if str(candidate) == source_id
                    ]
                }
                record = await self.db[collection_name].find_one(query)
            if not record:
                continue
            label = (
                record.get("subject")
                or record.get("filename")
                or record.get("clause_title")
                or record.get("claim_number")
                or record.get("variation_number")
                or record.get("_id")
            )
            citation = (
                record.get("letterNo")
                or record.get("clause_number")
                or record.get("claim_number")
                or record.get("variation_number")
                or record.get("filename")
            )
            # G34: these records come from document_vectors / contract_clauses,
            # which carry no processing_status - so asking THEM whether they are
            # consumable always said yes, and the chain fell through to raw
            # text. A guard that reads the wrong record is no guard.
            #
            # But WHICH record is canonical depends on the collection that
            # matched, and an earlier version of this guard ignored that: it
            # always resolved `record["document_id"]`, a field only
            # document_vectors/contract_clauses carry. For `documents` the
            # record IS canonical, and letters/claims/variations/events have no
            # such field at all - so 8 of the 9 source types resolved to
            # "no_document_id", were denied, and had their snippet silently
            # collapsed to the label while still reporting verified/strong.
            if collection_name in {"document_vectors", "contract_clauses"}:
                # Partial record: resolve back to the document it came from.
                decision_ok = (
                    await resolve_document_authority(
                        self.db,
                        record.get("document_id") or record.get("documentId"),
                    )
                ).consumable
            elif collection_name == "documents":
                # Already the canonical document.
                decision_ok = is_consumable(record)
            elif record.get("source_document_id"):
                # A chronology event is only an application record when a human
                # wrote it. `ChronologySuggestionService` lifts `description`
                # straight out of a document's extracted span
                # (`chronology.py:530`) and records `source_document_id`
                # alongside it - so an event WITH that pointer is a document
                # derivative, and the snippet chain below reaches
                # `record["description"]`. Lumping it in with claims and
                # variations meant the one path in this file that was never
                # converted handed the span back verbatim.
                #
                # Keyed on the provenance field the writer actually sets, not on
                # source_type: a manual event has no such pointer and stays
                # application content, which is why this is not a blanket gate.
                decision_ok = (
                    await resolve_document_authority(
                        self.db, record.get("source_document_id")
                    )
                ).consumable
            else:
                # Independent application records (letters, claims, variations,
                # manually authored chronology events). They are not
                # extraction-controlled document content and carry their own
                # approval gate via _is_verified_source, so the
                # document-authority model does not apply to them.
                decision_ok = True

            authority_denied = not decision_ok
            if not decision_ok:
                snippet = label
            else:
                snippet = (
                    authoritative_summary(record)
                    or authoritative_text(record)
                    or record.get("text")
                    or record.get("text_enriched")
                    or record.get("description")
                    or label
                )
            return {
                "source_type": source_type,
                "source_id": source_id,
                "authority_denied": authority_denied,
                "label": str(label or source_id),
                "citation": citation,
                "snippet": condense(snippet, 650),
                "page_numbers": record.get("page_numbers") or [],
                "clause_number": record.get("clause_number"),
                "letter_no": record.get("letterNo"),
                "allowed_use": reference.get("allowed_use") or ("clause" if source_type == "clause" else "fact"),
                "metadata": {
                    "source_origin": f"authoritative:{collection_name}",
                    "verification_status": "verified",
                    "authoritative_revision_id": record.get("current_version_id") or record.get("version_id"),
                    "authoritative_updated_at": record.get("updated_at"),
                    "authoritative_sha256": record.get("sha256"),
                },
            }
        return None

    def _ledger_row(self, ref: Dict[str, Any], idx: int) -> Dict[str, Any]:
        """Project a hydrated reference onto the ledger schema.

        This is a fixed-key rebuild, which is why it needs
        `LEDGER_SAFETY_FIELDS`: anything the hydrator computes that is not
        named in the literal below is dropped here, silently, and every
        downstream check on it becomes dead code. `authority_denied` was
        exactly that - computed at `_rehydrate_direct_reference`, dropped
        here, and then tested for in `_annotate_source_quality` where it could
        never be true.
        """
        citation = ref.get("citation") or ref.get("clause_number") or ref.get("letter_no") or ref.get("label")
        row = {
            "source_key": f"S{idx}",
            "source_id": str(ref.get("source_id") or ref.get("_id") or idx),
            "source_type": ref.get("source_type"),
            "allowed_use": ref.get("allowed_use") or "fact",
            "permitted_uses": _string_list(ref.get("permitted_uses") or ref.get("allowed_use") or "fact"),
            "label": ref.get("label") or ref.get("citation") or f"Source {idx}",
            "citation": citation,
            "snippet": condense(ref.get("snippet") or ref.get("metadata", {}).get("text"), 650),
            "page_numbers": ref.get("page_numbers") or [],
            "clause_number": ref.get("clause_number"),
            "letter_no": ref.get("letter_no"),
            "verification_status": ref.get("metadata", {}).get("verification_status") or ref.get("verification_status") or "selected",
            "is_user_supplied": ref.get("source_type") == "manual_fact",
            "source_origin": ref.get("metadata", {}).get("source_origin") or "selected_reference",
            "quality_flags": [],
            "metadata": ref.get("metadata") or {},
            "source_hash": "",
        }
        for field in LEDGER_SAFETY_FIELDS:
            row[field] = ref.get(field)
        if row.get("authority_denied"):
            # The hydrator already collapsed the snippet to the label, so
            # `missing_snippet` cannot fire. Without this the row still reports
            # the metadata's "verified" and scores strong, and a blocked
            # document reaches the drafting prompt as trusted evidence.
            row["verification_status"] = "authority_denied"
        row["source_hash"] = source_hash(row)
        return row

    async def _case_workspace_sources(
        self,
        draft: Dict[str, Any],
        offset: int,
        *,
        current_user: Any,
        include_review_sources: bool,
        context_warnings: List[str],
    ) -> List[Dict[str, Any]]:
        case_id = draft.get("case_id")
        if not case_id:
            return []
        rows: List[Dict[str, Any]] = []
        rows.extend(await self._document_index_sources(str(case_id), offset + len(rows), include_review_sources, context_warnings))
        rows.extend(await self._chronology_matrix_sources(str(case_id), offset + len(rows), include_review_sources, context_warnings))
        rows.extend(
            await self._clause_matrix_sources(
                str(case_id),
                offset + len(rows),
                include_review_sources,
                context_warnings,
                draft=draft,
                current_user=current_user,
            )
        )
        rows.extend(await self._issue_matrix_sources(str(case_id), offset + len(rows), include_review_sources, context_warnings))
        rows.extend(await self._claim_defence_matrix_sources(str(case_id), offset + len(rows), include_review_sources, context_warnings))
        rows.extend(await self._quantum_matrix_sources(str(case_id), offset + len(rows), include_review_sources, context_warnings))
        rows.extend(await self._notice_matrix_sources(str(case_id), offset + len(rows), include_review_sources, context_warnings))
        rows.extend(await self._jurisdiction_matrix_sources(str(case_id), offset + len(rows), include_review_sources, context_warnings))
        rows.extend(await self._expert_alignment_sources(str(case_id), offset + len(rows), include_review_sources, context_warnings))
        rows.extend(
            await self._register_sources(
                draft,
                offset + len(rows),
                include_review_sources,
                context_warnings,
                current_user,
            )
        )
        return rows

    async def _jurisdiction_matrix_sources(
        self,
        case_id: str,
        offset: int,
        include_review_sources: bool,
        context_warnings: List[str],
    ) -> List[Dict[str, Any]]:
        try:
            rows = await _collect(
                self.db.arbitration_jurisdiction_matrix.find({"case_id": case_id, "deleted_at": {"$exists": False}}).sort("created_at", 1)
            )
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for row in rows:
            if not _is_verified_source(row, include_review_sources=include_review_sources):
                continue
            check_type = str(row.get("check_type") or "jurisdiction")
            if check_type == "limitation":
                label = f"Limitation: {row.get('subject') or row.get('limitation_subject_id') or 'case'}"
                snippet = "\n".join(
                    part
                    for part in [
                        f"Status: {row.get('limitation_status')}" if row.get("limitation_status") else "",
                        f"Base date: {row.get('limitation_base_date')}" if row.get("limitation_base_date") else "",
                        f"Expiry: {row.get('limitation_expiry_date')}" if row.get("limitation_expiry_date") else "",
                        str(row.get("basis") or ""),
                    ]
                    if part
                )
                citation = row.get("limitation_status") or check_type
            elif check_type == "pre_arbitration_step":
                label = f"Pre-arbitration step: {row.get('step')}"
                snippet = "\n".join(
                    part
                    for part in [
                        f"Required: {row.get('required')}" if row.get("required") is not None else "",
                        f"Compliance: {row.get('compliance_status')}" if row.get("compliance_status") else "",
                        str(row.get("contractual_requirement") or ""),
                    ]
                    if part
                )
                citation = row.get("compliance_status") or check_type
            else:
                label = "Arbitration clause scope check"
                snippet = "\n".join(
                    part
                    for part in [
                        f"Scope status: {row.get('scope_status')}" if row.get("scope_status") else "",
                        str(row.get("claim_description") or ""),
                        str(row.get("notes") or ""),
                    ]
                    if part
                )
                citation = row.get("scope_status") or check_type
            ledger_row = {
                "source_key": f"S{offset + len(out) + 1}",
                "source_id": str(row.get("_id")),
                "source_type": "jurisdiction_check",
                "allowed_use": "fact",
                "permitted_uses": ["background", "fact"],
                "label": label,
                "citation": citation,
                "snippet": condense(snippet, 650),
                "page_numbers": [],
                "clause_number": None,
                "letter_no": None,
                "verification_status": row.get("approval_status") or "approved",
                "is_user_supplied": True,
                "source_origin": "case_jurisdiction_matrix",
                "matrix_row_id": row.get("_id"),
                "quality_flags": [],
                "metadata": {
                    "case_id": case_id,
                    "matrix": "jurisdiction-matrix",
                    "matrix_row_id": row.get("_id"),
                    "check_type": check_type,
                    "limitation_status": row.get("limitation_status"),
                    "compliance_status": row.get("compliance_status"),
                    "scope_status": row.get("scope_status"),
                },
                "source_hash": "",
            }
            ledger_row["source_hash"] = source_hash(ledger_row)
            out.append(ledger_row)
        return out

    async def _expert_alignment_sources(
        self,
        case_id: str,
        offset: int,
        include_review_sources: bool,
        context_warnings: List[str],
    ) -> List[Dict[str, Any]]:
        try:
            rows = await _collect(
                self.db.arbitration_expert_alignment.find({"case_id": case_id, "deleted_at": {"$exists": False}}).sort("created_at", 1)
            )
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for row in rows:
            if not _is_verified_source(row, include_review_sources=include_review_sources):
                continue
            snippet = "\n".join(
                str(part)
                for part in [
                    row.get("methodology"),
                    row.get("notes"),
                    *(row.get("contradictions") or []),
                ]
                if part
            )
            ledger_row = {
                "source_key": f"S{offset + len(out) + 1}",
                "source_id": str(row.get("expert_report_source_id") or row.get("_id")),
                "source_type": "expert_report",
                "allowed_use": "expert",
                "permitted_uses": ["expert", "fact"],
                "label": f"{row.get('expert_type') or 'expert'} alignment: claim {row.get('claim_no')}",
                "citation": row.get("claim_no") or row.get("_id"),
                "snippet": condense(snippet, 650),
                "page_numbers": [],
                "clause_number": None,
                "letter_no": None,
                "verification_status": row.get("alignment_status") or row.get("approval_status") or "approved",
                "is_user_supplied": False,
                "source_origin": "case_expert_alignment",
                "matrix_row_id": row.get("_id"),
                "quality_flags": [],
                "metadata": {
                    "case_id": case_id,
                    "matrix": "expert-alignment",
                    "matrix_row_id": row.get("_id"),
                    "expert_type": row.get("expert_type"),
                    "claim_no": row.get("claim_no"),
                    "pleaded_amount": row.get("pleaded_amount"),
                    "verified_amount": row.get("verified_amount"),
                    "calculation_match": row.get("calculation_match"),
                    "concurrency_addressed": row.get("concurrency_addressed"),
                    "contradictions": row.get("contradictions") or [],
                    "risk_flags": row.get("risk_flags") or [],
                },
                "source_hash": "",
            }
            ledger_row["source_hash"] = source_hash(ledger_row)
            out.append(ledger_row)
        return out

    async def _document_index_sources(
        self,
        case_id: str,
        offset: int,
        include_review_sources: bool,
        context_warnings: List[str],
    ) -> List[Dict[str, Any]]:
        try:
            rows = await _collect(
                self.db.arbitration_document_index.find({"case_id": case_id, "deleted_at": {"$exists": False}}).sort("created_at", 1)
            )
        except Exception:
            return []
        case = await self.db.arbitration_cases.find_one({"_id": case_id}) or {}
        out: List[Dict[str, Any]] = []
        for row in rows:
            if not _is_verified_source(row, include_review_sources=include_review_sources):
                continue
            if not row.get("source_id"):
                context_warnings.append(f"Document index row has no source id and was skipped: {row.get('title') or row.get('_id')}")
                continue
            authoritative = None
            authoritative_collection = None
            for collection_name in ("documents", "letters"):
                collection = getattr(self.db, collection_name, None)
                if collection is None:
                    continue
                # documents._id is ObjectId-keyed; the raw string matched
                # nothing and every approved source was skipped as "outside
                # case scope".
                query: Dict[str, Any] = {
                    "_id": {"$in": document_id_candidates(row.get("source_id"))}
                }
                for scope_key in ("organization_id", "project_id"):
                    # Unconditional - see the note in _rehydrate_direct_reference.
                    query[scope_key] = case.get(scope_key)
                authoritative = await collection.find_one(query)
                if authoritative:
                    authoritative_collection = collection_name
                    break
            if not authoritative:
                context_warnings.append(
                    f"Approved document source is missing or outside the case scope and was skipped: {row.get('source_id')}"
                )
                continue
            recorded_revision = str(row.get("source_revision_id") or row.get("current_version_id") or "")
            current_revision = str(authoritative.get("current_version_id") or authoritative.get("version_id") or "")
            if recorded_revision and current_revision and recorded_revision != current_revision:
                context_warnings.append(
                    f"Approved document source revision drifted and was skipped: {row.get('source_id')}"
                )
                continue
            if include_review_sources and not (_status_values(row) & VERIFIED_SOURCE_STATUSES):
                context_warnings.append(f"Review-only document index source included: {row.get('title') or row.get('exhibit_id')}")
            allowed = _allowed_use(row.get("allowed_use"), "fact")
            exhibit_id = row.get("exhibit_id")
            ledger_row = {
                "source_key": f"S{offset + len(out) + 1}",
                "source_id": str(row.get("source_id")),
                "source_type": row.get("source_type") or "document",
                "allowed_use": allowed,
                "permitted_uses": sorted(set([allowed, *[_allowed_use(item, allowed) for item in _as_list(row.get("permitted_uses"))]])),
                "label": authoritative.get("subject") or authoritative.get("filename") or row.get("title") or row.get("document_type") or exhibit_id or "Case document",
                "citation": exhibit_id or authoritative.get("letterNo") or row.get("letter_no") or authoritative.get("filename") or row.get("title"),
                # Guarded. The sibling rehydration path was fixed for G34 and
                # this one was missed: it read summary/ocrText straight off the
                # canonical document. Repairing the lookup above without this
                # would have turned a fail-closed availability bug into a real
                # leak, by making blocked documents resolvable for the first
                # time.
                # relevance_note is derived from this same document's extracted
                # text, so it cannot be a fallback for the guarded fields above -
                # that would hand back exactly what they withheld. It is gated by
                # the same authority; document_type is metadata and stays.
                "snippet": condense(
                    authoritative_summary(authoritative)
                    or authoritative_text(authoritative)
                    or await consumable_derived_text(self.db, row, "relevance_note")
                    or row.get("document_type"),
                    650,
                ),
                "page_numbers": authoritative.get("page_numbers") or row.get("page_numbers") or [],
                "clause_number": None,
                "letter_no": row.get("letter_no"),
                "verification_status": row.get("verification_status") or row.get("human_approval_status") or "approved",
                "is_user_supplied": False,
                "source_origin": "case_document_index",
                "matrix_row_id": row.get("_id"),
                "exhibit_id": exhibit_id,
                "quality_flags": [],
                "metadata": {
                    "case_id": case_id,
                    "matrix": "document-index",
                    "matrix_row_id": row.get("_id"),
                    "document_date": row.get("document_date"),
                    "document_type": row.get("document_type"),
                    "source_file_link": row.get("source_file_link"),
                    "issue_tags": row.get("issue_tags") or [],
                    "claim_tags": row.get("claim_tags") or [],
                    "risk_flags": row.get("risk_flags") or [],
                    "exhibit_id": exhibit_id,
                    "authoritative_collection": authoritative_collection,
                    "authoritative_revision_id": current_revision or None,
                    "authoritative_updated_at": authoritative.get("updated_at"),
                    "authoritative_sha256": authoritative.get("sha256"),
                },
                "source_hash": "",
            }
            ledger_row["source_hash"] = source_hash(ledger_row)
            out.append(ledger_row)
        return out

    async def _chronology_matrix_sources(
        self,
        case_id: str,
        offset: int,
        include_review_sources: bool,
        context_warnings: List[str],
    ) -> List[Dict[str, Any]]:
        try:
            rows = await _collect(
                self.db.arbitration_chronology_matrix.find({"case_id": case_id, "deleted_at": {"$exists": False}}).sort("date", 1)
            )
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for row in rows:
            if not _is_verified_source(row, include_review_sources=include_review_sources):
                continue
            event = await self._load_chronology_event(row)
            # `row["event"]` is `f"{title}. {description}"` composed by the
            # adapter from the source document's extracted span, so the LABEL
            # carries the same text the snippet below is gated on. A label is
            # not free-standing metadata here: it is displayed, and it is what
            # counsel picks evidence by.
            gated_event = await consumable_derived_text(
                self.db,
                row,
                "event",
                source_id=row.get("source_document_id")
                or (event or {}).get("source_document_id"),
            )
            if gated_event:
                title = gated_event
            elif row.get("event"):
                # Withheld. The event's own `title` is no safer - it is written
                # by `_title(source_doc, raw_text)` from the same extraction -
                # so degrade to a static placeholder rather than vanishing: an
                # unlabelled ledger row cannot be reviewed or cited.
                title = "Chronology event (source withheld)"
            else:
                title = (event or {}).get("title") or "Chronology event"
            citation = row.get("document_ref") or row.get("date") or (event or {}).get("date_text") or (event or {}).get("letter_no") or title
            # `description` is lifted from the source document's extracted text
            # (`chronology.py:530` sets it from span_text), so it is a document
            # derivative and cannot outlive that document's authority - the same
            # stale-derivative rule as relevance_note. `impact`, `evidence` and
            # `manual_notes` are authored in-app and stay ungated, and an event
            # with no source_document_id is a manual entry that document
            # authority has no jurisdiction over.
            description = (event or {}).get("description")
            if description and (event or {}).get("source_document_id"):
                description = await consumable_derived_text(
                    self.db, event, "description"
                )
            gated_description = description
            snippet = row.get("impact") or row.get("evidence") or description or (event or {}).get("manual_notes")
            allowed = _allowed_use(row.get("pleading_use") or (event or {}).get("pleading_use"), "chronology")
            ledger_row = {
                "source_key": f"S{offset + len(out) + 1}",
                "source_id": str(row.get("chronology_event_id") or row.get("_id")),
                "source_type": "chronology_event",
                "allowed_use": allowed,
                "permitted_uses": sorted(set([allowed, "chronology", "fact"])),
                "label": condense(title, 180),
                "citation": citation,
                "snippet": condense(snippet, 650),
                "page_numbers": [event.get("source_page")] if event and event.get("source_page") else [],
                "clause_number": row.get("clause") or ", ".join((event or {}).get("contract_clauses") or []) or None,
                "letter_no": (event or {}).get("letter_no"),
                "verification_status": row.get("verification_status") or (event or {}).get("verification_status") or "verified",
                "is_user_supplied": False,
                "source_origin": "case_chronology_matrix",
                "matrix_row_id": row.get("_id"),
                "quality_flags": [],
                "metadata": {
                    "case_id": case_id,
                    "matrix": "chronology-matrix",
                    "matrix_row_id": row.get("_id"),
                    "chronology_id": row.get("chronology_id") or (event or {}).get("chronology_id"),
                    "chronology_event_id": row.get("chronology_event_id"),
                    "party_responsible": row.get("party_responsible") or (event or {}).get("responsible_party"),
                    "issue_link": row.get("issue_link"),
                    "claim_link": row.get("claim_link"),
                    "event": _safe_event_metadata(row, event, gated_event, gated_description),
                },
                "source_hash": "",
            }
            ledger_row["source_hash"] = source_hash(ledger_row)
            out.append(ledger_row)
        return out

    async def _load_chronology_event(self, row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        event_id = row.get("chronology_event_id")
        chronology_id = row.get("chronology_id")
        if not event_id:
            return None
        query: Dict[str, Any] = {"_id": event_id}
        if chronology_id:
            query["chronology_id"] = chronology_id
        try:
            event = await self.db.matter_chronology_events.find_one(query)
        except Exception:
            return None
        return dict(event) if event else None

    async def _clause_matrix_sources(
        self,
        case_id: str,
        offset: int,
        include_review_sources: bool,
        context_warnings: List[str],
        *,
        draft: Optional[Dict[str, Any]] = None,
        current_user: Any = None,
    ) -> List[Dict[str, Any]]:
        """Approved case clause-matrix rows, INTERSECTED with canonical eligibility.

        `draft` and `current_user` default so that a caller which omits them
        resolves every document-governed row inside an EMPTY universe - i.e.
        refuses it. The matrix row's own `project_id` / `contract_id` /
        approval flag record where it was filed and who signed it off, never
        that the instrument governs anything, so there is no weaker check to
        fall back to.
        """
        matrix_fence = matrix_clause_fence(
            self.db, draft, current_user, context_warnings
        )
        try:
            rows = await _collect(
                self.db.arbitration_clause_matrix.find({"case_id": case_id, "deleted_at": {"$exists": False}}).sort("created_at", 1)
            )
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for row in rows:
            if not _is_verified_source(row, include_review_sources=include_review_sources):
                continue
            if not await matrix_fence.admits_clause_row(row):
                # Approval is NECESSARY and not SUFFICIENT. The row is dropped
                # entirely rather than blanked: keeping its source_id, clause
                # number and citation would still record a foreign instrument
                # as a contributor to a pleading, in a row sealed by
                # immutable_version_hash and rendered by the exporter. The case
                # matrix itself is untouched - historical approval is case
                # history, not this generation's evidence.
                continue
            citation = row.get("clause_number") or row.get("topic") or row.get("_id")
            # `clause_text_excerpt` is up to 900 characters of the parent
            # document's extracted text; `obligation_or_right` is authored in
            # the matrix. The clause id lives under `clause_source_id` and the
            # row carries no source_type, so both have to be supplied.
            snippet = await consumable_derived_text(
                self.db,
                row,
                "clause_text_excerpt",
                source_type="clause",
                source_id=row.get("clause_source_id"),
            ) or row.get("obligation_or_right")
            ledger_row = {
                "source_key": f"S{offset + len(out) + 1}",
                "source_id": str(row.get("clause_source_id") or row.get("_id")),
                "source_type": "clause",
                "allowed_use": "clause",
                "permitted_uses": ["clause"],
                "label": row.get("topic") or f"Clause {citation}",
                "citation": citation,
                "snippet": condense(snippet, 650),
                "page_numbers": row.get("page_numbers") or [],
                "clause_number": row.get("clause_number"),
                "letter_no": None,
                "verification_status": row.get("approval_status") or "approved",
                "is_user_supplied": False,
                "source_origin": "case_clause_matrix",
                "matrix_row_id": row.get("_id"),
                "quality_flags": [],
                "metadata": {
                    "case_id": case_id,
                    "matrix": "clause-matrix",
                    "matrix_row_id": row.get("_id"),
                    "claimant_use": row.get("claimant_use"),
                    "respondent_use": row.get("respondent_use"),
                    "related_evidence_ids": row.get("related_evidence_ids") or [],
                    "risk": row.get("risk"),
                },
                "source_hash": "",
            }
            ledger_row["source_hash"] = source_hash(ledger_row)
            out.append(ledger_row)
        return out

    async def _issue_matrix_sources(
        self,
        case_id: str,
        offset: int,
        include_review_sources: bool,
        context_warnings: List[str],
    ) -> List[Dict[str, Any]]:
        try:
            rows = await _collect(
                self.db.arbitration_issue_matrix.find({"case_id": case_id, "deleted_at": {"$exists": False}}).sort("created_at", 1)
            )
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for row in rows:
            if not _is_verified_source(row, include_review_sources=include_review_sources):
                continue
            snippet = "\n".join(
                part
                for part in [
                    f"Issue: {row.get('issue')}" if row.get("issue") else "",
                    f"Claimant: {row.get('claimant_position')}" if row.get("claimant_position") else "",
                    f"Respondent: {row.get('respondent_position')}" if row.get("respondent_position") else "",
                    f"Required finding: {row.get('required_finding')}" if row.get("required_finding") else "",
                ]
                if part
            )
            ledger_row = {
                "source_key": f"S{offset + len(out) + 1}",
                "source_id": str(row.get("_id")),
                "source_type": "issue_matrix",
                "allowed_use": "fact",
                "permitted_uses": ["background", "fact"],
                "label": row.get("issue") or row.get("issue_no") or "Issue matrix row",
                "citation": row.get("issue_no") or row.get("issue_type") or row.get("_id"),
                "snippet": condense(snippet, 650),
                "page_numbers": [],
                "clause_number": ", ".join(_string_list(row.get("clause_ids"))) or None,
                "letter_no": None,
                "verification_status": row.get("status") or row.get("approval_status") or "approved",
                "is_user_supplied": True,
                "source_origin": "case_issue_matrix",
                "matrix_row_id": row.get("_id"),
                "quality_flags": [],
                "metadata": {"case_id": case_id, "matrix": "issue-matrix", "matrix_row_id": row.get("_id"), "issue_type": row.get("issue_type")},
                "source_hash": "",
            }
            ledger_row["source_hash"] = source_hash(ledger_row)
            out.append(ledger_row)
        return out

    async def _claim_defence_matrix_sources(
        self,
        case_id: str,
        offset: int,
        include_review_sources: bool,
        context_warnings: List[str],
    ) -> List[Dict[str, Any]]:
        matrix_specs = [
            ("claim-matrix", self.db.arbitration_claim_matrix, "claim_matrix", "claim_no", "claim_head", "facts", "claim"),
            ("defence-matrix", self.db.arbitration_defence_matrix, "defence_matrix", "source_claim_no", "defence", "positive_case", "fact"),
            ("counterclaim-matrix", self.db.arbitration_counterclaim_matrix, "counterclaim_matrix", "counterclaim_no", "breach", "facts", "claim"),
            ("rejoinder-matrix", self.db.arbitration_rejoinder_matrix, "rejoinder_matrix", "source_sod_para", "claimant_reply", "nature_of_defence", "fact"),
        ]
        out: List[Dict[str, Any]] = []
        for slug, collection, source_type, citation_key, label_key, snippet_key, use in matrix_specs:
            try:
                rows = await _collect(collection.find({"case_id": case_id, "deleted_at": {"$exists": False}}).sort("created_at", 1))
            except Exception:
                continue
            for row in rows:
                if not _is_verified_source(row, include_review_sources=include_review_sources):
                    continue
                if slug == "claim-matrix":
                    snippet = "\n".join(str(part) for part in [row.get("facts"), row.get("causation"), row.get("relief"), row.get("weakness")] if part)
                elif slug == "counterclaim-matrix":
                    snippet = "\n".join(str(part) for part in [row.get("facts"), row.get("breach"), row.get("causation"), row.get("relief")] if part)
                elif slug == "rejoinder-matrix":
                    snippet = "\n".join(str(part) for part in [row.get("nature_of_defence"), row.get("claimant_reply"), row.get("reply_to_counterclaim")] if part)
                else:
                    snippet = "\n".join(str(part) for part in [row.get("admission_denial"), row.get("defence"), row.get("positive_case")] if part)
                allowed = "quantum" if row.get("amount_or_days") or row.get("amount") else use
                ledger_row = {
                    "source_key": f"S{offset + len(out) + 1}",
                    "source_id": str(row.get("_id")),
                    "source_type": source_type,
                    "allowed_use": _allowed_use(allowed, "fact"),
                    "permitted_uses": sorted(set(["fact", _allowed_use(allowed, "fact")])),
                    "label": row.get(label_key) or row.get(snippet_key) or row.get(citation_key) or slug,
                    "citation": row.get(citation_key) or row.get("_id"),
                    "snippet": condense(snippet, 650),
                    "page_numbers": [],
                    "clause_number": ", ".join(_string_list(row.get("clause_ids"))) or None,
                    "letter_no": None,
                    "verification_status": row.get("readiness_status") or row.get("approval_status") or "approved",
                    "is_user_supplied": False,
                    "source_origin": f"case_{slug}",
                    "matrix_row_id": row.get("_id"),
                    "quality_flags": [],
                    "metadata": {
                        "case_id": case_id,
                        "matrix": slug,
                        "matrix_row_id": row.get("_id"),
                        "evidence_ids": row.get("evidence_ids") or [],
                        "notice_ids": row.get("notice_ids") or [],
                        "calculation_id": row.get("calculation_id"),
                        "amount_or_days": row.get("amount_or_days"),
                        "new_matter": row.get("new_matter"),
                        "permission_required": row.get("permission_required"),
                        "permission_obtained": row.get("permission_obtained"),
                        "permission_source_id": row.get("permission_source_id"),
                        "permission_approved_by": row.get("permission_approved_by"),
                        "permission_approved_at": row.get("permission_approved_at"),
                    },
                    "source_hash": "",
                }
                ledger_row["source_hash"] = source_hash(ledger_row)
                out.append(ledger_row)
        return out

    async def _quantum_matrix_sources(
        self,
        case_id: str,
        offset: int,
        include_review_sources: bool,
        context_warnings: List[str],
    ) -> List[Dict[str, Any]]:
        try:
            rows = await _collect(
                self.db.arbitration_quantum_annexures.find({"case_id": case_id, "deleted_at": {"$exists": False}}).sort("created_at", 1)
            )
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for row in rows:
            if not _is_verified_source(row, include_review_sources=include_review_sources):
                continue
            amount = _money(row.get("amount"), row.get("currency"))
            snippet = "\n".join(str(part) for part in [row.get("formula"), row.get("assumptions"), amount] if part)
            ledger_row = {
                "source_key": f"S{offset + len(out) + 1}",
                "source_id": str(row.get("calculation_id") or row.get("_id")),
                "source_type": "quantum_annexure",
                "allowed_use": "quantum",
                "permitted_uses": ["annexure", "quantum"],
                "label": row.get("calculation_type") or row.get("calculation_id") or "Quantum annexure",
                "citation": row.get("calculation_id") or row.get("calculation_type") or row.get("_id"),
                "snippet": condense(snippet, 650),
                "page_numbers": [],
                "clause_number": None,
                "letter_no": None,
                "verification_status": row.get("approval_status") or "approved",
                "is_user_supplied": False,
                "source_origin": "case_quantum_annexure",
                "matrix_row_id": row.get("_id"),
                "quality_flags": [],
                "metadata": {
                    "case_id": case_id,
                    "matrix": "quantum-annexures",
                    "matrix_row_id": row.get("_id"),
                    "source_records": row.get("source_records") or [],
                    "calculation_type": row.get("calculation_type"),
                    "amount": row.get("amount"),
                    "currency": row.get("currency"),
                    "tax_treatment": row.get("tax_treatment"),
                    "cost_head": row.get("cost_head"),
                    "critical_path_days": row.get("critical_path_days"),
                    "delay_event_ids": row.get("delay_event_ids") or [],
                },
                "source_hash": "",
            }
            ledger_row["source_hash"] = source_hash(ledger_row)
            out.append(ledger_row)
        return out

    async def _notice_matrix_sources(
        self,
        case_id: str,
        offset: int,
        include_review_sources: bool,
        context_warnings: List[str],
    ) -> List[Dict[str, Any]]:
        try:
            rows = await _collect(
                self.db.arbitration_notice_compliance.find({"case_id": case_id, "deleted_at": {"$exists": False}}).sort("created_at", 1)
            )
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for row in rows:
            if not _is_verified_source(row, include_review_sources=include_review_sources):
                continue
            # UI rows use requirement/risk_note; agent rows use contractual_requirement/risk.
            snippet = "\n".join(
                str(part)
                for part in [
                    row.get("requirement") or row.get("contractual_requirement"),
                    row.get("compliance_status"),
                    row.get("risk_note") or row.get("risk"),
                ]
                if part
            )
            ledger_row = {
                "source_key": f"S{offset + len(out) + 1}",
                "source_id": str(row.get("_id")),
                "source_type": "notice_compliance",
                "allowed_use": "notice",
                "permitted_uses": ["fact", "notice"],
                "label": row.get("notice_ref") or "Notice compliance",
                "citation": row.get("notice_ref") or row.get("notice_date") or row.get("_id"),
                "snippet": condense(snippet, 650),
                "page_numbers": [],
                "clause_number": row.get("clause") or row.get("clause_number"),
                "letter_no": row.get("notice_ref"),
                "verification_status": row.get("approval_status") or "approved",
                "is_user_supplied": False,
                "source_origin": "case_notice_compliance",
                "matrix_row_id": row.get("_id"),
                "quality_flags": [],
                "metadata": {"case_id": case_id, "matrix": "notice-compliance", "matrix_row_id": row.get("_id")},
                "source_hash": "",
            }
            ledger_row["source_hash"] = source_hash(ledger_row)
            out.append(ledger_row)
        return out

    async def _register_sources(
        self,
        draft: Dict[str, Any],
        offset: int,
        include_review_sources: bool,
        context_warnings: List[str],
        current_user: Any,
    ) -> List[Dict[str, Any]]:
        if not draft.get("project_id"):
            return []
        # Audit P2: registers are included by default, but the user controls it.
        if draft.get("include_register_sources") is False:
            context_warnings.append(
                "Project register sources (claims, variations, IPCs, bank guarantees) are disabled for this draft."
            )
            return []
        out: List[Dict[str, Any]] = []
        out.extend(await self._claim_register_sources(draft, offset + len(out), include_review_sources))
        out.extend(await self._variation_register_sources(draft, offset + len(out), include_review_sources))
        out.extend(
            await self._ipc_register_sources(
                draft,
                offset + len(out),
                include_review_sources,
                current_user,
                context_warnings,
            )
        )
        out.extend(
            await self._bank_guarantee_sources(
                draft,
                offset + len(out),
                include_review_sources,
                current_user,
                context_warnings,
            )
        )
        excluded = {str(item) for item in draft.get("excluded_register_ids") or [] if item}
        if excluded:
            kept = [row for row in out if str(row.get("source_id")) not in excluded]
            removed_count = len(out) - len(kept)
            if removed_count:
                context_warnings.append(
                    f"{removed_count} register source(s) excluded from this draft by user selection."
                )
            out = kept
        return out

    def _scope_query(self, draft: Dict[str, Any], *, require_contract: bool = False) -> Optional[Dict[str, Any]]:
        query: Dict[str, Any] = {"project_id": draft.get("project_id")}
        if draft.get("organization_id"):
            query["organization_id"] = draft.get("organization_id")
        if draft.get("contract_id"):
            query["contract_id"] = draft.get("contract_id")
        elif require_contract:
            return None
        return query

    async def _claim_register_sources(self, draft: Dict[str, Any], offset: int, include_review_sources: bool) -> List[Dict[str, Any]]:
        query = self._scope_query(draft)
        if not query:
            return []
        if not include_review_sources:
            query["status"] = {"$in": ["notified", "submitted", "under_review", "agreed", "rejected", "disputed", "closed"]}
        try:
            rows = await _collect(self.db.claims.find(query).sort("updated_at", -1).limit(10))
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for row in rows:
            amount = _money(row.get("amount_claimed"), row.get("currency"))
            eot = f"{row.get('eot_days_claimed')} days" if row.get("eot_days_claimed") is not None else None
            snippet = "\n".join(str(part) for part in [row.get("description"), amount, eot] if part)
            ledger_row = {
                "source_key": f"S{offset + len(out) + 1}",
                "source_id": str(row.get("_id")),
                "source_type": "claim",
                "allowed_use": "quantum" if amount or eot else "fact",
                "permitted_uses": ["fact", "quantum"],
                "label": row.get("title") or row.get("claim_ref") or "Claim register",
                "citation": row.get("claim_ref") or row.get("title") or row.get("_id"),
                "snippet": condense(snippet, 650),
                "page_numbers": [],
                "clause_number": ", ".join(row.get("contract_clauses") or []) or None,
                "letter_no": None,
                "verification_status": row.get("status") or "register",
                "is_user_supplied": False,
                "source_origin": "claim_register",
                "quality_flags": [],
                "metadata": {
                    "claim_type": row.get("type"),
                    "status": row.get("status"),
                    # Relationship membership is not evidence authority. Claim-linked
                    # Documents enter drafting only through separately authorized
                    # Document sources, never through this legacy register field.
                    "linked_document_ids": [],
                    "linked_letter_ids": row.get("linked_letter_ids") or [],
                },
                "source_hash": "",
            }
            ledger_row["source_hash"] = source_hash(ledger_row)
            out.append(ledger_row)
        return out

    async def _variation_register_sources(self, draft: Dict[str, Any], offset: int, include_review_sources: bool) -> List[Dict[str, Any]]:
        query = self._scope_query(draft)
        if not query:
            return []
        if not include_review_sources:
            query["status"] = {"$in": ["submitted", "under_review", "recommended", "approved", "rejected"]}
        try:
            rows = await _collect(self.db.variations.find(query).sort("updated_at", -1).limit(10))
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for row in rows:
            snippet = "\n".join(
                str(part)
                for part in [
                    row.get("description"),
                    _money(row.get("submitted_amount")),
                    _money(row.get("approved_amount")),
                    row.get("remarks"),
                ]
                if part
            )
            ledger_row = {
                "source_key": f"S{offset + len(out) + 1}",
                "source_id": str(row.get("_id")),
                "source_type": "variation",
                "allowed_use": "quantum" if row.get("submitted_amount") or row.get("approved_amount") else "fact",
                "permitted_uses": ["fact", "quantum"],
                "label": row.get("variation_number") or "Variation register",
                "citation": row.get("variation_number") or row.get("letter_reference") or row.get("_id"),
                "snippet": condense(snippet, 650),
                "page_numbers": [],
                "clause_number": None,
                "letter_no": row.get("letter_reference"),
                "verification_status": row.get("status") or "register",
                "is_user_supplied": False,
                "source_origin": "variation_register",
                "quality_flags": [],
                "metadata": {"variation_type": row.get("variation_type"), "linked_document_ids": row.get("linked_document_ids") or []},
                "source_hash": "",
            }
            ledger_row["source_hash"] = source_hash(ledger_row)
            out.append(ledger_row)
        return out

    async def _ipc_register_sources(
        self,
        draft: Dict[str, Any],
        offset: int,
        include_review_sources: bool,
        current_user: Any,
        context_warnings: List[str],
    ) -> List[Dict[str, Any]]:
        query = self._scope_query(draft)
        if not query:
            return []
        if not include_review_sources:
            query["status"] = {"$in": ["submitted", "under_verification", "verified", "approved", "partially_paid", "paid", "rejected"]}
        try:
            rows = await _collect(self.db.ipc_bills.find(query).sort("ipc_date", -1).limit(10))
        except Exception:
            return []
        try:
            authorized_ids = await DocumentRelationshipService(
                self.db
            ).authorized_document_ids_for_targets(current_user, "ipc_bill", rows)
        except Exception:
            authorized_ids = {}
            context_warnings.append(
                "IPC document relationships could not be re-authorized and were omitted."
            )
        out: List[Dict[str, Any]] = []
        for row in rows:
            currency = row.get("base_currency") or "INR"
            snippet = "\n".join(
                str(part)
                for part in [
                    row.get("ipc_period"),
                    _money(row.get("claimed_total_base"), currency),
                    _money(row.get("approved_total_base"), currency),
                    _money(row.get("net_payable_base"), currency),
                    row.get("remarks"),
                ]
                if part
            )
            ledger_row = {
                "source_key": f"S{offset + len(out) + 1}",
                "source_id": str(row.get("_id")),
                "source_type": "payment_event",
                "allowed_use": "quantum",
                "permitted_uses": ["fact", "quantum"],
                "label": row.get("ipc_number") or "IPC bill",
                "citation": row.get("ipc_number") or row.get("ipc_period") or row.get("_id"),
                "snippet": condense(snippet, 650),
                "page_numbers": [],
                "clause_number": None,
                "letter_no": None,
                "verification_status": row.get("status") or "register",
                "is_user_supplied": False,
                "source_origin": "ipc_register",
                "quality_flags": [],
                "metadata": {
                    "ipc_date": row.get("ipc_date"),
                    "status": row.get("status"),
                    "letter_references": row.get("letter_references") or [],
                    "linked_document_ids": authorized_ids.get(str(row.get("_id") or ""), []),
                },
                "source_hash": "",
            }
            ledger_row["source_hash"] = source_hash(ledger_row)
            out.append(ledger_row)
        return out

    async def _bank_guarantee_sources(
        self,
        draft: Dict[str, Any],
        offset: int,
        include_review_sources: bool,
        current_user: Any,
        context_warnings: List[str],
    ) -> List[Dict[str, Any]]:
        query = self._scope_query(draft)
        if not query:
            return []
        if not include_review_sources:
            query["bg_status"] = {"$in": ["submitted", "valid", "extension_required", "extended", "expired", "released", "encashment_under_process", "encashed"]}
        try:
            rows = await _collect(self.db.bank_guarantees.find(query).sort("updated_at", -1).limit(10))
        except Exception:
            return []
        try:
            from ..bank_guarantee_evidence_service import BankGuaranteeEvidenceService

            authorized_evidence = await BankGuaranteeEvidenceService(
                self.db
            ).authorized_event_evidence(current_user, rows)
        except Exception:
            authorized_evidence = {}
            context_warnings.append(
                "Bank Guarantee document relationships could not be re-authorized and were omitted."
            )
        out: List[Dict[str, Any]] = []
        for row in rows:
            snippet = "\n".join(
                str(part)
                for part in [
                    row.get("issuing_bank"),
                    _money(row.get("bg_amount"), row.get("currency")),
                    row.get("bg_expiry_date"),
                    row.get("remarks"),
                ]
                if part
            )
            ledger_row = {
                "source_key": f"S{offset + len(out) + 1}",
                "source_id": str(row.get("_id")),
                "source_type": "bank_guarantee",
                "allowed_use": "fact",
                "permitted_uses": ["fact", "quantum"],
                "label": row.get("bg_number") or row.get("bg_type") or "Bank guarantee",
                "citation": row.get("bg_number") or row.get("_id"),
                "snippet": condense(snippet, 650),
                "page_numbers": [],
                "clause_number": None,
                "letter_no": None,
                "verification_status": row.get("bg_status") or "register",
                "is_user_supplied": False,
                "source_origin": "bank_guarantee_register",
                "quality_flags": [],
                "metadata": {
                    "bg_type": row.get("bg_type"),
                    "event_evidence": authorized_evidence.get(str(row.get("_id") or ""), []),
                },
                "source_hash": "",
            }
            ledger_row["source_hash"] = source_hash(ledger_row)
            out.append(ledger_row)
        return out

    async def _contract_evidence_universe(
        self,
        draft: Dict[str, Any],
        current_user: Any,
        context_warnings: List[str],
    ) -> Optional[ProjectEvidenceUniverse]:
        """The canonical eligible universe for this pleading.

        A pleading does not decide what governs a contract; it asks. The answer
        is POSITIVE - applicable at the query mode, canonical Document
        positively resolvable, publication-consumable, projection-current - and
        it is resolved for THIS ACTOR, not for the draft's own workspace stamp.

        ``None`` means the question could not be ANSWERED, which is a different
        thing from "nothing applies". Both produce zero contract evidence, and
        neither may widen: falling back to generic contract search is exactly
        the door the Contract Master model closes. The distinction is surfaced
        as a context warning rather than collapsed into an empty ledger,
        because an arbitration draft assembled without the contract it turns on
        must not look identical to one where no contract applies.

        Query mode is ``CurrentState``, chosen rather than defaulted. A
        pleading is written now and cites what governs now; ``Historical`` is
        deliberately not inferred from a case or letter date, because an
        implicit "as at" would manufacture legal evidence for a date nobody
        asked about.

        Scope note, carried from G-A7/G-A8 and not narrowed silently: the
        arbitration draft's ``contract_id`` is free text that nothing validates
        or joins against ``contract_document_applicability`` - only the register
        scope query reads it - so it is not Contract Master identity and is not
        treated as such here. This is therefore PROJECT-EVIDENCE containment:
        the union over the contracts that have applicability in the authorised
        project. Sibling contracts inside one project are not separable until
        that identity is made canonical, which is an owner decision.
        """
        organization_id = str(draft.get("organization_id") or "")
        project_id = str(draft.get("project_id") or "")
        if not organization_id or not project_id:
            return None
        try:
            return await resolve_authorized_project_universe(
                self.db,
                current_user,
                organization_id=organization_id,
                project_id=project_id,
                mode=CurrentState(),
            )
        except Exception as exc:
            context_warnings.append(
                f"Contract clause evidence was omitted: contract eligibility could not be resolved ({exc})."
            )
            return None

    async def _contract_clause_sources(
        self,
        draft: Dict[str, Any],
        current_user: Any,
        offset: int,
        context_warnings: List[str],
    ) -> List[Dict[str, Any]]:
        """Contract clause evidence, fenced by the canonical universe.

        The generic ``ContractService.search_contracts`` supplement that used to
        sit here was REMOVED rather than filtered. Generic search resolves no
        applicability, no projection currency and no positive per-document
        publication authority - its subtractive helper fails OPEN on an id it
        cannot resolve - and it spends its candidate limit before any of them
        exist, so there is no point in its pipeline at which a fence would work.
        Everything it produced reached the source ledger, the generated
        pleading, the input hash and the immutable ``arbitration_draft_versions``
        row the exporter renders.

        A clause row's own ``org_id`` / ``project_id`` / ``contract_id`` are
        INGEST PROVENANCE: they record where the row came from, never that the
        instrument legally governs anything. They narrow the query; they do not
        authorise it. ``is_authorised_for_ai`` does not help either - it is
        written once at clause-index time from clause quality and is never
        revisited when the parent document's authority changes.

        So ``document_id: {"$in": eligible}`` is part of the QUERY, not a filter
        over its result, and the subtractive ``blocked_document_ids`` is absent
        from this path rather than kept beside the universe: a second predicate
        that looks like authority is how the real one stops being read.
        """
        query = " ".join(
            [
                str(draft.get("title") or ""),
                str(draft.get("manual_facts") or ""),
                str(draft.get("relief_sought") or ""),
                str(draft.get("arbitration_clause") or ""),
            ]
        ).strip()
        organization_id = str(draft.get("organization_id") or "")
        project_id = str(draft.get("project_id") or "")
        if not query or not project_id:
            return []

        universe = await self._contract_evidence_universe(draft, current_user, context_warnings)
        if universe is None:
            # Eligibility could not be resolved. There is no partial answer to
            # give and no broader query that would be safer, so nothing is
            # queried at all.
            return []
        eligible = sorted(universe.eligible_document_ids)
        if not eligible:
            # Valid empty. Nothing applicable governs this project, which is an
            # answer a pleading may act on - never a reason to search wider.
            return []

        terms = {token for token in query[:800].lower().split() if len(token) > 3}
        try:
            records = await _collect(
                self.db.contract_clauses.find(
                    {
                        "org_id": organization_id,
                        "project_id": project_id,
                        "is_current": True,
                        "is_authorised_for_ai": True,
                        "document_id": {"$in": eligible},
                    }
                ).limit(CONTRACT_CLAUSE_CANDIDATE_LIMIT)
            )
        except Exception as exc:
            context_warnings.append(
                f"Contract clause evidence was omitted: clause retrieval failed ({exc})."
            )
            return []

        scored: List[tuple] = []
        for record in records:
            haystack = " ".join(
                str(part or "")
                for part in (
                    record.get("cleaned_text"),
                    record.get("clause_title"),
                    record.get("clause_no"),
                )
            ).lower()
            hits = sum(1 for term in terms if term in haystack)
            score = hits / max(len(terms), 1)
            if score > 0:
                scored.append((score, record))
        scored.sort(key=lambda item: item[0], reverse=True)

        rows: List[Dict[str, Any]] = []
        for idx, (_score, record) in enumerate(
            scored[:CONTRACT_CLAUSE_SOURCE_LIMIT], start=offset + 1
        ):
            clause_number = record.get("clause_no")
            clause_title = record.get("clause_title")
            document_id = record.get("document_id")
            page_numbers = [
                page for page in (record.get("page_start"), record.get("page_end")) if page
            ]
            row = {
                "source_key": f"S{idx}",
                "source_id": str(document_id or record.get("clause_uid") or idx),
                "source_type": "clause",
                "allowed_use": "clause",
                "permitted_uses": ["clause"],
                "label": f"{clause_number or 'Clause'} {clause_title or ''}".strip(),
                "citation": clause_number or clause_title,
                "snippet": condense(record.get("cleaned_text"), 650),
                "page_numbers": page_numbers,
                "clause_number": clause_number,
                "letter_no": None,
                "verification_status": "retrieved_clause",
                "is_user_supplied": False,
                # Renamed from `contract_search`: this is no longer contract
                # search, and a label that still said so would make the frozen
                # boundary unreadable from the persisted ledger.
                "source_origin": "contract_evidence",
                "quality_flags": [],
                "metadata": {
                    "document_id": document_id,
                    "clause_uid": record.get("clause_uid"),
                    "document_type": record.get("document_type"),
                },
                "source_hash": "",
            }
            row["source_hash"] = source_hash(row)
            rows.append(row)
        return rows

    async def _link_source_document_id(self, link: Dict[str, Any]) -> Optional[str]:
        """The document an evidence link's text ultimately came from, if any.

        A document-target link names it outright. A clause-target link carries
        the same `evidence_text` but points at a clause reference, so it has to
        go back through the chronology event that produced both.
        """
        if str(link.get("target_type") or "") == "document" and link.get("target_id"):
            return str(link.get("target_id"))
        event_id = (link.get("metadata") or {}).get("chronology_event_id")
        if not event_id:
            return None
        event = await self._load_chronology_event({"chronology_event_id": event_id})
        return (event or {}).get("source_document_id")

    async def _verified_graph_sources(
        self,
        draft: Dict[str, Any],
        include_unverified_graph_links: bool,
        offset: int,
        context_warnings: List[str],
    ) -> List[Dict[str, Any]]:
        if not draft.get("project_id"):
            return []
        if include_unverified_graph_links:
            context_warnings.append("Unverified AI-suggested graph links were included for review mode only.")
        try:
            rows = await EvidenceGraphService(self.db).downstream_links(
                {
                    "organization_id": draft.get("organization_id"),
                    "project_id": draft.get("project_id"),
                },
                include_ai_suggested=include_unverified_graph_links,
            )
        except Exception:
            return []
        out: List[Dict[str, Any]] = []
        for idx, link in enumerate(rows[:10], start=offset + 1):
            # `evidence_text` is the chronology event's `description` copied on
            # to the link (`chronology.py:713`, `:733`), which is span text
            # lifted from the source document. Gating it where it was born and
            # not where it was copied is no gate at all.
            source_document_id = await self._link_source_document_id(link)
            evidence_text = (
                await consumable_derived_text(
                    self.db,
                    link,
                    "evidence_text",
                    # An evidence link's OWN `source_type` is the graph edge's
                    # source entity ("project_event"), not the provenance of
                    # this text. Left to infer, the helper read that field and
                    # waved the row through as an application record.
                    source_type="document",
                    source_id=source_document_id,
                )
                if source_document_id
                # No document behind it - manual evidence, no jurisdiction.
                else link.get("evidence_text")
            )
            row = {
                "source_key": f"S{idx}",
                "source_id": str(link.get("link_group_id") or link.get("_id")),
                "source_type": "event_link",
                "allowed_use": "chronology",
                "permitted_uses": ["chronology", "fact"],
                "label": f"{link.get('source_type')} {link.get('relation_type')} {link.get('target_type')}",
                "citation": link.get("relation_type"),
                "snippet": condense(evidence_text, 650),
                "page_numbers": [],
                "clause_number": None,
                "letter_no": None,
                "verification_status": link.get("status") or "verified",
                "is_user_supplied": False,
                "source_origin": "evidence_graph",
                "quality_flags": [],
                "metadata": {"link_group_id": link.get("link_group_id"), "relation_type": link.get("relation_type")},
                "source_hash": "",
            }
            row["source_hash"] = source_hash(row)
            out.append(row)
        return out

    def _missing_evidence(
        self,
        draft: Dict[str, Any],
        source_ledger: List[Dict[str, Any]],
        claim_heads: List[Dict[str, Any]],
        paragraph_responses: List[Dict[str, Any]],
    ) -> List[str]:
        missing: List[str] = []
        if not source_ledger:
            missing.append("No selected or retrieved evidence is available for this pleading.")
        if draft.get("manual_facts") and not any(row.get("source_type") != "manual_fact" for row in source_ledger):
            missing.append("Manual facts are user-provided and require independent source support before filing.")
        if draft.get("claim_amount") and not any(row.get("allowed_use") == "quantum" for row in source_ledger):
            missing.append("Claim amount is entered but no quantum/payment source is selected.")
        if draft.get("draft_type") == "rejoinder" and not paragraph_responses:
            missing.append("Statement of Defence paragraphs must be imported for paragraph-wise rejoinder replies.")
        for head in claim_heads:
            if not head.get("supporting_source_ids"):
                missing.append(f"Claim head needs support: {head.get('description')}")
            else:
                known_ids = {str(row.get("source_id")) for row in source_ledger}
                unknown = [str(item) for item in head.get("supporting_source_ids") or [] if str(item) not in known_ids]
                if unknown:
                    missing.append(f"Claim head references unavailable source ids: {', '.join(unknown)}")
        known_ids = {str(row.get("source_id")) for row in source_ledger}
        for response in paragraph_responses:
            unknown = [str(item) for item in response.get("supporting_source_ids") or [] if str(item) not in known_ids]
            if unknown:
                missing.append(f"Paragraph {response.get('source_paragraph_number')} references unavailable source ids: {', '.join(unknown)}")
        for row in source_ledger:
            if row.get("source_origin") == "case_document_index":
                if not row.get("exhibit_id"):
                    missing.append(f"Document index source has no exhibit id: {row.get('label')}")
                if "missing_source_link" in row.get("quality_flags", []):
                    missing.append(f"Document index source has no file/source link: {row.get('label')}")
        return missing

    def _dedupe(self, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen = set()
        out: List[Dict[str, Any]] = []
        for row in rows:
            key = (row.get("source_type"), row.get("source_id"), row.get("citation"))
            if key in seen:
                continue
            seen.add(key)
            row["source_key"] = f"S{len(out) + 1}"
            out.append(row)
        return out

    def _matrix_context(self, source_ledger: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        groups = {
            "documents": [],
            "chronology": [],
            "clauses": [],
            "issues": [],
            "claims": [],
            "defences": [],
            "counterclaims": [],
            "rejoinder_replies": [],
            "quantum": [],
            "notices": [],
            "experts": [],
            "jurisdiction": [],
        }
        origin_map = {
            "case_document_index": "documents",
            "case_chronology_matrix": "chronology",
            "case_clause_matrix": "clauses",
            "case_issue_matrix": "issues",
            "case_claim-matrix": "claims",
            "case_defence-matrix": "defences",
            "case_counterclaim-matrix": "counterclaims",
            "case_rejoinder-matrix": "rejoinder_replies",
            "case_quantum_annexure": "quantum",
            "case_notice_compliance": "notices",
            "case_expert_alignment": "experts",
            "case_jurisdiction_matrix": "jurisdiction",
        }
        for row in source_ledger:
            group = origin_map.get(str(row.get("source_origin") or ""))
            if group:
                groups[group].append(row)
        return groups

    def _expert_consistency_warnings(
        self,
        matrix_context: Dict[str, List[Dict[str, Any]]],
        context_warnings: List[str],
    ) -> None:
        """Guide §12.1: pleaded amounts and delay claims must align with expert records."""
        expert_rows = matrix_context.get("experts") or []
        claim_rows = matrix_context.get("claims") or []
        experts_by_claim: Dict[tuple[str, str], Dict[str, Any]] = {}
        for row in expert_rows:
            metadata = row.get("metadata") or {}
            claim_no = str(metadata.get("claim_no") or "")
            expert_type = str(metadata.get("expert_type") or "")
            if claim_no and expert_type:
                experts_by_claim[(expert_type, claim_no)] = row
            for contradiction in metadata.get("contradictions") or []:
                message = f"Expert alignment contradiction: {contradiction}"
                if message not in context_warnings:
                    context_warnings.append(message)
        for claim in claim_rows:
            metadata = claim.get("metadata") or {}
            claim_no = str(claim.get("citation") or "")
            pleaded = _numeric_amount(metadata.get("amount_or_days"))
            if pleaded is None or not claim_no:
                continue
            quantum_expert = experts_by_claim.get(("quantum", claim_no))
            if not quantum_expert:
                continue
            verified = _numeric_amount((quantum_expert.get("metadata") or {}).get("verified_amount"))
            if verified is not None and abs(verified - pleaded) >= 0.01:
                context_warnings.append(
                    f"Pleaded amount {pleaded} for claim {claim_no} does not match the expert-verified amount {verified}."
                )
        for (expert_type, claim_no), row in experts_by_claim.items():
            metadata = row.get("metadata") or {}
            if expert_type == "delay" and not metadata.get("concurrency_addressed"):
                context_warnings.append(
                    f"Concurrency has not been addressed for delay claim {claim_no}; align the pleading with the delay expert."
                )

    async def _annotate_source_quality(self, rows: List[Dict[str, Any]], context_warnings: List[str]) -> None:
        for row in rows:
            flags: List[str] = list(row.get("quality_flags") or [])
            if not row.get("citation"):
                flags.append("missing_citation")
            if not row.get("snippet"):
                flags.append("missing_snippet")
            # Authority is RE-RESOLVED here, centrally, from the row's own
            # provenance - not trusted from a flag the builder may or may not
            # have set. `authority_denied` was written by exactly one of the
            # ledger builders; the rest gated their snippet and dropped the
            # decision, so this check was dead for them and a blocked document
            # scored strong/verified. Deriving the decision from source_type +
            # source_id means a builder cannot emit a document-governed row that
            # escapes it. An explicit builder denial is still honoured.
            denied = bool(row.get("authority_denied"))
            db = getattr(self, "db", None)
            if not denied and getattr(db, "documents", None) is not None:
                decision = await resolve_derived_authority(
                    db,
                    row.get("source_type"),
                    (row.get("metadata") or {}).get("source_document_id") or row.get("source_id"),
                )
                denied = not decision.consumable
            if denied:
                row["authority_denied"] = True
                if str(row.get("verification_status") or "") in VERIFIED_SOURCE_STATUSES:
                    row["verification_status"] = "authority_denied"
                flags.append("authority_denied")
            if row.get("source_origin") == "case_document_index":
                metadata = row.get("metadata") or {}
                if not row.get("exhibit_id"):
                    flags.append("missing_exhibit_id")
                if not row.get("source_id") and not metadata.get("source_file_link"):
                    flags.append("missing_source_link")
                if row.get("verification_status") not in VERIFIED_SOURCE_STATUSES:
                    flags.append("not_verified_for_filing")
            if row.get("is_user_supplied"):
                flags.append("user_supplied")
            if str(row.get("verification_status") or "").startswith("ai_"):
                flags.append("unverified_ai_suggestion")
            row["quality_flags"] = sorted(set(flags))
            row["evidence_strength"] = "strong" if not flags else ("medium" if flags == ["missing_snippet"] else "needs_review")
        if any("user_supplied" in row.get("quality_flags", []) for row in rows):
            context_warnings.append("Manual fact sources are not independent evidence and require legal review.")
        if any("unverified_ai_suggestion" in row.get("quality_flags", []) for row in rows):
            context_warnings.append("Source ledger includes unverified AI graph suggestions.")
