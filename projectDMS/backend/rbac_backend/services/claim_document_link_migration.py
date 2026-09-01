"""Claim legacy relationship classifier for the unified backfill.

Claim already migrates `linked_document_ids` whenever a user edits the claim
(`DocumentRelationshipService.replace_legacy_document_ids`), and un-migrated ids
stay visible through the `legacy_read_through` merge. This classifier covers the
remainder: claims nobody has edited since the canonical model landed.

Role resolution is NOT a guess. Claim's legacy role is already accepted and
shipped in three places that agree — `ClaimEntityAdapter.legacy_relationship_role`,
the role `replace_legacy_document_ids` writes, and the role users already see in
the read-through view. This classifier reads it from the adapter rather than
restating it, so the three cannot drift apart.

`deleted_target` is deliberately absent: `ClaimService.delete` hard-deletes, so a
deleted claim simply stops being iterated. A selection naming a vanished claim is
caught by the orchestrator's `missing_target`.
"""

from __future__ import annotations

from typing import Any

from .entity_adapter_registry import ClaimEntityAdapter
from .publication_policy import (
    document_id_candidates,
    is_consumable,
    resolve_canonical_document,
)


CLASSIFICATIONS = (
    "valid",
    "missing_document",
    "deleted_document",
    "blocked_document",
    "cross_organisation",
    "cross_project",
    "project_null",
    "invalid_id",
    "duplicate",
    "frozen_target",
    "manual_review",
)

SOURCE_KIND = "legacy_linked_document_id"

# `claims.linked_letter_ids` is a DIFFERENT field with different semantics: the
# UI routes it to the Letter drafting workspace, `Letter` carries no canonical
# Document id, and this repository has no letter->document resolver (the only
# possible join is the normalised letter number, which CLAUDE.md documents as
# shared across documents and organisations). Historical values are therefore
# mixed, and each is classified on its own evidence. Only an id that genuinely
# resolves to a canonical Document — and to nothing else — may migrate.
LETTER_SOURCE_KIND = "legacy_linked_letter_id"

LETTER_CLASSIFICATIONS = CLASSIFICATIONS + (
    "letter_register_record",
    "ambiguous_identity",
    "missing_identity",
)

# A letter-sourced Document is correspondence, which is a first-class role in
# Claim's accepted vocabulary — not the generic supporting_document fallback.
LETTER_RELATIONSHIP_ROLE = "correspondence"


async def classify_legacy_claim_links(db: Any) -> dict[str, Any]:
    """Inventory Claim legacy candidates without mutating anything."""

    legacy_role = ClaimEntityAdapter.legacy_relationship_role
    counts = {classification: 0 for classification in CLASSIFICATIONS}
    candidates: list[dict[str, Any]] = []

    cursor = db.claims.find({"linked_document_ids": {"$exists": True}})
    async for claim in cursor:
        claim_id = str(claim.get("_id") or "")
        organization_id = str(claim.get("organization_id") or "")
        project_id = str(claim.get("project_id") or "")
        frozen = bool(claim.get("evidence_frozen_at"))
        seen: set[str] = set()
        occurrences: dict[str, int] = {}

        for raw_document_id in claim.get("linked_document_ids") or []:
            valid_shape = isinstance(raw_document_id, str) and bool(
                raw_document_id.strip()
            )
            document_id = (
                raw_document_id.strip() if valid_shape else str(raw_document_id or "")
            )
            occurrences[document_id] = occurrences.get(document_id, 0) + 1
            findings: list[str] = []
            classification = "manual_review"
            relationship_role = None

            if not valid_shape:
                findings.append("invalid_id")
                classification = "invalid_id"
            elif document_id in seen:
                findings.append("duplicate")
                classification = "duplicate"
            elif frozen:
                # Claim's accepted model refuses relationship writes on a frozen
                # claim. Legacy membership does not earn a retrospective
                # exception; report it and stop.
                findings.append("frozen_target")
                classification = "frozen_target"
            elif not project_id:
                # v1 policy is project-scoped; fail closed rather than guess.
                findings.append("project_null")
                classification = "project_null"
            else:
                seen.add(document_id)
                document = await resolve_canonical_document(db, document_id)
                if document is None:
                    findings.append("missing_document")
                    classification = "missing_document"
                elif str(document.get("lifecycle_state") or "") == "deleted":
                    findings.append("deleted_document")
                    classification = "deleted_document"
                elif not is_consumable(document):
                    findings.append("blocked_document")
                    classification = "blocked_document"
                elif (
                    str(
                        document.get("organization_id")
                        or document.get("organizationId")
                        or ""
                    )
                    != organization_id
                ):
                    findings.append("cross_organisation")
                    classification = "cross_organisation"
                elif (
                    str(document.get("project_id") or document.get("projectId") or "")
                    != project_id
                ):
                    findings.append("cross_project")
                    classification = "cross_project"
                else:
                    # Authority is clean and the role is determined, so this is
                    # genuinely valid — unlike registers whose legacy array
                    # carries no role information.
                    findings.append("valid")
                    classification = "valid"
                    relationship_role = legacy_role

            for finding in findings:
                counts[finding] = counts.get(finding, 0) + 1
            candidates.append(
                {
                    "claim_id": claim_id,
                    "target_type": "claim",
                    "target_id": claim_id,
                    "parent_type": None,
                    "parent_id": None,
                    "source_kind": SOURCE_KIND,
                    "legacy_field": "linked_document_ids",
                    "document_id": document_id,
                    "occurrence": occurrences[document_id],
                    "relationship_role": relationship_role,
                    "classification": classification,
                    "findings": findings,
                }
            )

    return {
        "dry_run": True,
        "target_type": "claim",
        "candidate_count": len(candidates),
        "counts": counts,
        "candidates": candidates,
    }


async def classify_legacy_claim_letter_links(db: Any) -> dict[str, Any]:
    """Inventory `claims.linked_letter_ids` without mutating anything.

    Both namespaces are probed explicitly. A token that exists as a Document AND
    as a Letter is `ambiguous_identity`, never silently resolved to whichever
    lookup happened to run first.
    """

    counts = {classification: 0 for classification in LETTER_CLASSIFICATIONS}
    candidates: list[dict[str, Any]] = []

    cursor = db.claims.find({"linked_letter_ids": {"$exists": True}})
    async for claim in cursor:
        claim_id = str(claim.get("_id") or "")
        organization_id = str(claim.get("organization_id") or "")
        project_id = str(claim.get("project_id") or "")
        frozen = bool(claim.get("evidence_frozen_at"))
        seen: set[str] = set()
        occurrences: dict[str, int] = {}

        for raw_id in claim.get("linked_letter_ids") or []:
            valid_shape = isinstance(raw_id, str) and bool(raw_id.strip())
            legacy_id = raw_id.strip() if valid_shape else str(raw_id or "")
            occurrences[legacy_id] = occurrences.get(legacy_id, 0) + 1
            findings: list[str] = []
            classification = "manual_review"
            relationship_role = None

            if not valid_shape:
                findings.append("invalid_id")
                classification = "invalid_id"
            elif legacy_id in seen:
                findings.append("duplicate")
                classification = "duplicate"
            elif frozen:
                findings.append("frozen_target")
                classification = "frozen_target"
            elif not project_id:
                findings.append("project_null")
                classification = "project_null"
            else:
                seen.add(legacy_id)
                document = await resolve_canonical_document(db, legacy_id)
                letter = await _find_letter(db, legacy_id)

                if document is not None and letter is not None:
                    # Same token in two namespaces: refuse rather than guess.
                    findings.append("ambiguous_identity")
                    classification = "ambiguous_identity"
                elif document is None and letter is not None:
                    # A Letter register row is correspondence, not canonical
                    # Document content, and must never self-authorise.
                    findings.append("letter_register_record")
                    classification = "letter_register_record"
                elif document is None:
                    findings.append("missing_identity")
                    classification = "missing_identity"
                elif str(document.get("lifecycle_state") or "") == "deleted":
                    findings.append("deleted_document")
                    classification = "deleted_document"
                elif not is_consumable(document):
                    findings.append("blocked_document")
                    classification = "blocked_document"
                elif (
                    str(
                        document.get("organization_id")
                        or document.get("organizationId")
                        or ""
                    )
                    != organization_id
                ):
                    findings.append("cross_organisation")
                    classification = "cross_organisation"
                elif (
                    str(document.get("project_id") or document.get("projectId") or "")
                    != project_id
                ):
                    findings.append("cross_project")
                    classification = "cross_project"
                else:
                    findings.append("valid")
                    classification = "valid"
                    relationship_role = LETTER_RELATIONSHIP_ROLE

            for finding in findings:
                counts[finding] = counts.get(finding, 0) + 1
            candidates.append(
                {
                    "claim_id": claim_id,
                    "target_type": "claim",
                    "target_id": claim_id,
                    "parent_type": None,
                    "parent_id": None,
                    "source_kind": LETTER_SOURCE_KIND,
                    "legacy_field": "linked_letter_ids",
                    "document_id": legacy_id,
                    "occurrence": occurrences[legacy_id],
                    "relationship_role": relationship_role,
                    "classification": classification,
                    "findings": findings,
                }
            )

    return {
        "dry_run": True,
        "target_type": "claim",
        "candidate_count": len(candidates),
        "counts": counts,
        "candidates": candidates,
    }


def _letters_collection(db: Any) -> Any:
    """The letters namespace, however this handle exposes collections."""
    collection = getattr(db, "letters", None)
    if collection is not None:
        return collection
    try:
        return db["letters"]
    except Exception:
        return None


async def _find_letter(db: Any, legacy_id: Any) -> dict[str, Any] | None:
    """Probe the letters namespace explicitly.

    Errors are NOT swallowed. Returning None on a failed probe would make a
    token that exists in both namespaces look like a clean Document and let it
    auto-migrate — the ambiguity guard would be defeated by the very condition
    it exists for. Resolution uncertainty fails closed by propagating.
    """
    collection = _letters_collection(db)
    if collection is None:
        return None
    for candidate in document_id_candidates(str(legacy_id)):
        row = await collection.find_one({"_id": candidate})
        if row:
            return row
    return None
