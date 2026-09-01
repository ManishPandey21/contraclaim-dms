"""Read-only classification for legacy IPC ``linked_document_ids``.

The current IPC model has no stable submission/certification/payment child
event identity, and its legacy array has no relationship role. This module is
therefore deliberately a dry-run inventory: it never guesses and never writes.
"""

from __future__ import annotations

from typing import Any

from .publication_policy import is_consumable, resolve_canonical_document


SOURCE_KIND = "legacy_linked_document_id"

CLASSIFICATIONS = (
    "valid",
    "missing_document",
    "deleted_document",
    "blocked_document",
    "cross_organisation",
    "cross_project",
    "invalid_id",
    "duplicate",
    "ambiguous_role",
    "manual_review",
)


async def classify_legacy_ipc_links(db: Any) -> dict[str, Any]:
    """Inventory IPC legacy candidates without mutating either collection."""

    counts = {classification: 0 for classification in CLASSIFICATIONS}
    candidates: list[dict[str, Any]] = []
    cursor = db.ipc_bills.find({"linked_document_ids": {"$exists": True}})
    async for ipc in cursor:
        ipc_id = str(ipc.get("_id") or "")
        organization_id = str(ipc.get("organization_id") or "")
        project_id = str(ipc.get("project_id") or "")
        seen: set[str] = set()
        occurrences: dict[str, int] = {}
        for raw_document_id in ipc.get("linked_document_ids") or []:
            valid_shape = isinstance(raw_document_id, str) and bool(raw_document_id.strip())
            document_id = raw_document_id.strip() if valid_shape else str(raw_document_id or "")
            occurrences[document_id] = occurrences.get(document_id, 0) + 1
            findings: list[str] = []
            classification = "manual_review"

            if not valid_shape:
                findings.append("invalid_id")
                classification = "invalid_id"
            elif document_id in seen:
                findings.append("duplicate")
                classification = "duplicate"
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
                elif str(document.get("organization_id") or document.get("organizationId") or "") != organization_id:
                    findings.append("cross_organisation")
                    classification = "cross_organisation"
                elif str(document.get("project_id") or document.get("projectId") or "") != project_id:
                    findings.append("cross_project")
                    classification = "cross_project"
                else:
                    # IPC's accepted model has NO child events: submission,
                    # certification and payment are embedded lifecycle data with
                    # no stable entity ids, so the relationship target is the
                    # bill itself. The only genuine ambiguity here is the ROLE,
                    # which a privileged operator adjudicates.
                    findings.extend(["valid", "ambiguous_role", "manual_review"])

            for finding in findings:
                counts[finding] += 1
            candidates.append(
                {
                    "ipc_id": ipc_id,
                    "target_type": "ipc_bill",
                    "target_id": ipc_id,
                    "parent_type": None,
                    "parent_id": None,
                    "source_kind": SOURCE_KIND,
                    "legacy_field": "linked_document_ids",
                    "document_id": document_id,
                    "occurrence": occurrences[document_id],
                    "relationship_role": None,
                    "classification": classification,
                    "findings": findings,
                }
            )

    return {
        "dry_run": True,
        "target_type": "ipc_bill",
        "candidate_count": len(candidates),
        "counts": counts,
        "candidates": candidates,
    }

