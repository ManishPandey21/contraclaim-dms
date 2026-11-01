"""Read-only classification and census of legacy Variation Document links.

Before CL-2 a Variation carried two relationship-shaped fields:

* ``linked_document_ids`` - a flat, role-less array that the PUT route accepted
  verbatim (including forged foreign ids);
* ``letter_reference`` - free text naming a letter number.

Neither is authority. This module turns both into *candidates* for the unified
legacy backfill (``services/legacy_relationship_backfill.py``), re-resolving each
one against the current canonical Document and the Variation's own scope. It never
writes, and it never deletes or rewrites either legacy field: the backfill adds
canonical links beside them, so no legacy data is lost.

Role resolution (never guessed):

* array member that is NOT correspondence -> ``supporting_document``, the generic
  role (same precedent as Claim's legacy array);
* array member that IS correspondence -> role ambiguous (correspondence,
  submission or approval); an operator adjudicates;
* ``letter_reference`` resolving to exactly one in-scope correspondence Document
  -> ``correspondence`` (same precedent as Claim ``linked_letter_ids``).

``variation_legacy_census`` aggregates the same classification into counts only -
no ids, numbers, subjects or names - for a pre-migration production read.
"""

from __future__ import annotations

from typing import Any, Optional

from .entity_adapter_registry import CORRESPONDENCE_UPLOAD_TYPES, is_correspondence_document
from .falkor_graph_service import normalize_letter_code
from .publication_policy import is_consumable, resolve_canonical_document


ARRAY_SOURCE_KIND = "legacy_linked_document_id"
LETTER_SOURCE_KIND = "legacy_letter_reference"
SOURCE_KINDS = frozenset({ARRAY_SOURCE_KIND, LETTER_SOURCE_KIND})

CLASSIFICATIONS = (
    "valid_correspondence",
    "valid_supporting_document",
    "missing_document",
    "deleted_document",
    "blocked_document",
    "cross_organisation",
    "cross_project",
    "project_null",
    "invalid_id",
    "duplicate",
    "ambiguous",
    "non_correspondence",
)


def _document_scope(document: dict[str, Any]) -> tuple[str, str]:
    return (
        str(document.get("organization_id") or document.get("organizationId") or ""),
        str(document.get("project_id") or document.get("projectId") or ""),
    )


def _upload_type_bucket(document: Optional[dict[str, Any]]) -> str:
    if not document:
        return "unresolved"
    raw = document.get("uploadType")
    if raw is None:
        raw = document.get("upload_type")
    value = str(raw or "").strip().lower()
    if not value:
        return "missing"
    if value in CORRESPONDENCE_UPLOAD_TYPES or value == "contract":
        return value
    return "other"


async def _classify_document(
    db: Any,
    document_id: str,
    *,
    organization_id: str,
    project_id: str,
) -> tuple[str, Optional[dict[str, Any]]]:
    document = await resolve_canonical_document(db, document_id)
    if document is None:
        return "missing_document", None
    if str(document.get("lifecycle_state") or "") == "deleted":
        return "deleted_document", document
    if not is_consumable(document):
        return "blocked_document", document
    document_org, document_project = _document_scope(document)
    if document_org != organization_id:
        return "cross_organisation", document
    if document_project != project_id:
        return "cross_project", document
    return "valid", document


async def _letter_matches(
    db: Any, reference: str, *, organization_id: str, project_id: str
) -> list[dict[str, Any]]:
    """In-scope Documents whose letter number is this reference.

    Only the Variation's own organisation/project is searched: letter numbers are
    not unique across tenants, so a match elsewhere proves nothing and looking
    would be a cross-tenant probe.
    """
    normalized = normalize_letter_code(reference)
    scope = {"organization_id": organization_id, "project_id": project_id}
    found: dict[str, dict[str, Any]] = {}
    for query in (
        {**scope, "letterNoNormalized": normalized},
        {**scope, "letterNo": reference},
    ):
        async for row in db.documents.find(query):
            if str(row.get("lifecycle_state") or "") == "deleted" or not is_consumable(row):
                continue
            found[str(row.get("_id"))] = row
    return list(found.values())


def _candidate(
    variation_id: str,
    *,
    source_kind: str,
    legacy_field: str,
    document_id: str,
    occurrence: int,
    classification: str,
    findings: list[str],
    relationship_role: Optional[str],
    upload_type: str,
    **extra: Any,
) -> dict[str, Any]:
    return {
        "variation_id": variation_id,
        "target_type": "variation",
        "target_id": variation_id,
        "parent_type": None,
        "parent_id": None,
        "source_kind": source_kind,
        "legacy_field": legacy_field,
        "document_id": document_id,
        "occurrence": occurrence,
        "relationship_role": relationship_role,
        "classification": classification,
        "findings": findings,
        "document_upload_type": upload_type,
        **extra,
    }


async def classify_legacy_variation_links(db: Any) -> dict[str, Any]:
    """Classify every legacy Variation candidate without mutating anything."""

    counts = {classification: 0 for classification in CLASSIFICATIONS}
    candidates: list[dict[str, Any]] = []
    cursor = db.variations.find(
        {"$or": [{"linked_document_ids": {"$exists": True}}, {"letter_reference": {"$exists": True}}]}
    )
    async for variation in cursor:
        variation_id = str(variation.get("_id") or "")
        organization_id = str(variation.get("organization_id") or "")
        project_id = str(variation.get("project_id") or "")
        seen: set[str] = set()
        occurrences: dict[str, int] = {}

        for raw_document_id in variation.get("linked_document_ids") or []:
            valid_shape = isinstance(raw_document_id, str) and bool(raw_document_id.strip())
            document_id = raw_document_id.strip() if valid_shape else str(raw_document_id or "")
            occurrences[document_id] = occurrences.get(document_id, 0) + 1
            role: Optional[str] = None
            document: Optional[dict[str, Any]] = None
            if not valid_shape:
                classification, findings = "invalid_id", ["invalid_id"]
            elif document_id in seen:
                classification, findings = "duplicate", ["duplicate"]
            elif not organization_id or not project_id:
                classification, findings = "project_null", ["project_null"]
            else:
                seen.add(document_id)
                outcome, document = await _classify_document(
                    db, document_id, organization_id=organization_id, project_id=project_id
                )
                if outcome != "valid":
                    classification, findings = outcome, [outcome]
                elif is_correspondence_document(document):
                    # Correspondence, submission or approval: the flat array does
                    # not say which, so an operator adjudicates.
                    classification = "valid_correspondence"
                    findings = ["valid", "ambiguous_role", "manual_review"]
                else:
                    classification = "valid_supporting_document"
                    findings = ["valid"]
                    role = "supporting_document"
            for finding in findings:
                counts[finding] = counts.get(finding, 0) + 1
            candidates.append(
                _candidate(
                    variation_id,
                    source_kind=ARRAY_SOURCE_KIND,
                    legacy_field="linked_document_ids",
                    document_id=document_id,
                    occurrence=occurrences[document_id],
                    classification=classification,
                    findings=findings,
                    relationship_role=role,
                    upload_type=_upload_type_bucket(document),
                )
            )

        reference = variation.get("letter_reference")
        if not isinstance(reference, str) or not reference.strip():
            continue
        reference = reference.strip()
        matched_ids: list[str] = []
        document = None
        role = None
        document_id = ""
        if not organization_id or not project_id:
            classification, findings = "project_null", ["project_null"]
        else:
            matches = await _letter_matches(
                db, reference, organization_id=organization_id, project_id=project_id
            )
            matched_ids = sorted(str(row.get("_id")) for row in matches)
            if not matches:
                classification, findings = "missing_document", ["missing_document"]
            elif len(matches) > 1:
                classification, findings = "ambiguous", ["ambiguous_reference"]
            else:
                document = matches[0]
                document_id = str(document.get("_id") or "")
                if document_id in seen:
                    classification, findings = "duplicate", ["duplicate"]
                elif not is_correspondence_document(document):
                    classification, findings = "non_correspondence", ["non_correspondence"]
                else:
                    classification, findings = "valid_correspondence", ["valid"]
                    role = "correspondence"
        for finding in findings:
            counts[finding] = counts.get(finding, 0) + 1
        candidates.append(
            _candidate(
                variation_id,
                source_kind=LETTER_SOURCE_KIND,
                legacy_field="letter_reference",
                document_id=document_id,
                occurrence=1,
                classification=classification,
                findings=findings,
                relationship_role=role,
                upload_type=_upload_type_bucket(document),
                matched_document_ids=matched_ids,
            )
        )

    return {
        "dry_run": True,
        "target_type": "variation",
        "candidate_count": len(candidates),
        "counts": counts,
        "candidates": candidates,
    }


async def variation_legacy_census(
    db: Any,
    *,
    organization_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> dict[str, Any]:
    """READ-ONLY, counts-only census of legacy Variation links.

    Output carries no identifiers, letter numbers, subjects or party names, so it
    can be read out of production. It issues only ``find`` / ``count_documents``.
    """
    scope: dict[str, Any] = {}
    if organization_id:
        scope["organization_id"] = organization_id
    if project_id:
        scope["project_id"] = project_id

    variation_rows = await db.variations.count_documents(scope)
    rows_with_links = await db.variations.count_documents(
        {**scope, "linked_document_ids": {"$exists": True, "$ne": []}}
    )
    rows_with_letter_reference = await db.variations.count_documents(
        {**scope, "letter_reference": {"$nin": [None, ""]}}
    )
    canonical_links = await db.entity_document_links.count_documents(
        {**scope, "target_type": "variation", "removed_at": None}
    )

    report = await classify_legacy_variation_links(db)
    in_scope_ids = {
        str(row.get("_id"))
        async for row in db.variations.find(scope, {"_id": 1})
    }
    array_counts = {classification: 0 for classification in CLASSIFICATIONS}
    letter_counts = {classification: 0 for classification in CLASSIFICATIONS}
    upload_types = {bucket: 0 for bucket in ("incoming", "outgoing", "contract", "other", "missing", "unresolved")}
    total_linked_ids = 0
    for candidate in report["candidates"]:
        if candidate["target_id"] not in in_scope_ids:
            continue
        if candidate["source_kind"] == ARRAY_SOURCE_KIND:
            total_linked_ids += 1
            array_counts[candidate["classification"]] += 1
            # Only first occurrences that were actually resolved: a duplicate,
            # malformed or scope-less entry has no Document of its own to type.
            if candidate["classification"] not in {"duplicate", "invalid_id", "project_null"}:
                upload_types[candidate["document_upload_type"]] += 1
        else:
            letter_counts[candidate["classification"]] += 1

    return {
        "read_only": True,
        "scope": {"organization": bool(organization_id), "project": bool(project_id)},
        "variation_rows": variation_rows,
        "rows_with_linked_document_ids": rows_with_links,
        "total_linked_ids": total_linked_ids,
        "linked_ids": {
            "valid_correspondence": array_counts["valid_correspondence"],
            "valid_supporting_document": array_counts["valid_supporting_document"],
            "missing_documents": array_counts["missing_document"],
            "deleted_documents": array_counts["deleted_document"],
            "blocked_documents": array_counts["blocked_document"],
            "foreign_org_ids": array_counts["cross_organisation"],
            "foreign_project_ids": array_counts["cross_project"],
            "duplicate_ids": array_counts["duplicate"],
            "invalid_ids": array_counts["invalid_id"],
            "project_null_rows": array_counts["project_null"],
        },
        # By the Document each first-occurrence, well-formed id resolves to;
        # ``missing`` is a Document with no uploadType, ``unresolved`` an id
        # with no Document row. ``total_linked_ids`` counts every array entry.
        "linked_document_upload_types": upload_types,
        "rows_with_letter_reference": rows_with_letter_reference,
        "letter_references": {
            "resolved_correspondence": letter_counts["valid_correspondence"],
            "resolved_non_correspondence": letter_counts["non_correspondence"],
            "unresolved": letter_counts["missing_document"],
            "ambiguous": letter_counts["ambiguous"],
            "already_in_array": letter_counts["duplicate"],
            "project_null_rows": letter_counts["project_null"],
        },
        "canonical_variation_links": canonical_links,
    }
