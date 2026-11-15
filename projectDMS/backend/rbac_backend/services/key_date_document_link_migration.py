"""Deterministic read-only inventory for legacy Key Date/EOT evidence."""

from __future__ import annotations

from typing import Any

from .publication_policy import is_consumable, resolve_canonical_document


#: Each legacy source is tagged so the unified backfill can separate sources
#: whose EVENT is mechanically known (the document already sits on the event
#: row) from those that carry no event provenance at all.
SOURCE_KIND_BY_TARGET = {
    "key_date_achievement": "legacy_key_date_achievement_array",
    "eot_submission": "legacy_eot_submission_array",
    "eot_determination": "legacy_eot_determination_array",
    # No provable event ownership — inventory/reconciliation only.
    "key_date": "legacy_key_date_parent_array",
    "legacy_eot": "legacy_eot_application_array",
}

#: Sources whose event identity is proven by the record the document sits on.
EVENT_PROVEN_SOURCE_KINDS = frozenset(
    {
        "legacy_key_date_achievement_array",
        "legacy_eot_submission_array",
        "legacy_eot_determination_array",
    }
)

#: Fallback for a target type nobody has classified yet. An unmapped source
#: would otherwise get `source_kind=None`, which belongs to no module's
#: `source_kinds` and so would vanish from BOTH the writable module and the
#: reconciliation module — evidence silently disappearing rather than being
#: refused. It lands here instead: visible, and never writable.
UNCLASSIFIED_SOURCE_KIND = "legacy_key_date_unclassified_source"

#: Sources retained for reconciliation only: a milestone-level array cannot say
#: which event it belonged to, the deprecated EOT application shape must not
#: become new authority, and an unclassified source has proven nothing at all.
INVENTORY_ONLY_SOURCE_KINDS = frozenset(
    {
        "legacy_key_date_parent_array",
        "legacy_eot_application_array",
        UNCLASSIFIED_SOURCE_KIND,
    }
)

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
    "ambiguous_event",
    "missing_event",
    "orphan_event",
    "legacy_eot",
    "superseded_submission",
    "frozen_target",
    "manual_review",
)


async def _document_findings(
    db: Any,
    *,
    raw_document_id: Any,
    organization_id: str,
    project_id: str,
) -> tuple[str, list[str], str]:
    if not isinstance(raw_document_id, str) or not raw_document_id.strip():
        return str(raw_document_id or ""), ["invalid_id"], "invalid_id"
    document_id = raw_document_id.strip()
    document = await resolve_canonical_document(db, document_id)
    if document is None:
        return document_id, ["missing_document"], "missing_document"
    if str(document.get("lifecycle_state") or "") == "deleted":
        return document_id, ["deleted_document"], "deleted_document"
    if not is_consumable(document):
        return document_id, ["blocked_document"], "blocked_document"
    document_org = str(
        document.get("organization_id") or document.get("organizationId") or ""
    )
    document_project = str(
        document.get("project_id") or document.get("projectId") or ""
    )
    if document_org != organization_id:
        return document_id, ["cross_organisation"], "cross_organisation"
    if document_project != project_id:
        return document_id, ["cross_project"], "cross_project"
    return document_id, ["valid"], "manual_review"


async def _rows(collection: Any) -> list[dict[str, Any]]:
    rows = [row async for row in collection.find({})]
    rows.sort(key=lambda row: str(row.get("_id") or ""))
    return rows


async def classify_legacy_key_date_links(db: Any) -> dict[str, Any]:
    """Classify arrays/references without creating relationships or changing rows."""

    counts = {classification: 0 for classification in CLASSIFICATIONS}
    candidates: list[dict[str, Any]] = []
    metadata_references: list[dict[str, Any]] = []
    event_findings: list[dict[str, Any]] = []
    milestones = await _rows(db.key_date_milestones)
    milestone_by_id = {str(row.get("_id") or ""): row for row in milestones}
    achievements = await _rows(db.key_date_achievements)
    submissions = await _rows(db.key_date_eot_submissions)
    determinations = await _rows(db.key_date_eot_determinations)
    submission_items = await _rows(db.key_date_eot_submission_items)
    determination_items = await _rows(db.key_date_eot_determination_items)
    achievement_ids = {str(row.get("_id") or "") for row in achievements}
    submission_by_id = {str(row.get("_id") or ""): row for row in submissions}
    determination_by_id = {str(row.get("_id") or ""): row for row in determinations}
    submission_item_parents = {
        str(row.get("eot_submission_id") or "") for row in submission_items
    }
    determination_item_parents = {
        str(row.get("determination_id") or "") for row in determination_items
    }
    finding_keys: set[tuple[str, str, str]] = set()

    def event_owns_milestone(
        event: dict[str, Any],
        milestone: dict[str, Any] | None,
        *,
        allow_inherited_contract: bool = False,
    ) -> bool:
        if milestone is None:
            return False
        if any(
            str(event.get(field) or "") != str(milestone.get(field) or "")
            for field in ("organization_id", "project_id")
        ):
            return False
        event_contract = event.get("contract_id")
        if allow_inherited_contract and not event_contract:
            return True
        return str(event_contract or "primary") == str(
            milestone.get("contract_id") or "primary"
        )

    def add_event_finding(
        target_type: str,
        target_id: str,
        classification: str,
        *,
        detail: str,
    ) -> None:
        key = (target_type, target_id, classification)
        if key in finding_keys:
            return
        finding_keys.add(key)
        counts[classification] += 1
        event_findings.append(
            {
                "target_type": target_type,
                "target_id": target_id,
                "classification": classification,
                "findings": [classification],
                "detail": detail,
            }
        )

    for milestone in milestones:
        milestone_id = str(milestone.get("_id") or "")
        if milestone.get("actual_achievement_date") and f"{milestone_id}:ach" not in achievement_ids:
            add_event_finding(
                "key_date_achievement",
                f"{milestone_id}:ach",
                "missing_event",
                detail="Milestone has achievement state but no stable achievement event",
            )
    for achievement in achievements:
        achievement_id = str(achievement.get("_id") or "")
        milestone_id = str(achievement.get("milestone_id") or "")
        milestone = milestone_by_id.get(milestone_id)
        if (
            achievement_id != f"{milestone_id}:ach"
            or not event_owns_milestone(
                achievement, milestone, allow_inherited_contract=True
            )
        ):
            add_event_finding(
                "key_date_achievement",
                achievement_id,
                "orphan_event",
                detail="Achievement event is not the canonical stable event for its owned Key Date",
            )
    for submission in submissions:
        submission_id = str(submission.get("_id") or "")
        if submission_id not in submission_item_parents:
            add_event_finding(
                "eot_submission",
                submission_id,
                "missing_event",
                detail="EOT submission has no owned milestone item",
            )
    for determination in determinations:
        determination_id = str(determination.get("_id") or "")
        if determination_id not in determination_item_parents:
            add_event_finding(
                "eot_determination",
                determination_id,
                "missing_event",
                detail="EOT determination has no owned milestone item",
            )
        for submission_id in determination.get("eot_submission_ids") or []:
            submission = submission_by_id.get(str(submission_id))
            if not submission or any(
                str(submission.get(field) or "") != str(determination.get(field) or "")
                for field in ("organization_id", "project_id", "contract_id")
            ):
                add_event_finding(
                    "eot_determination",
                    determination_id,
                    "orphan_event",
                    detail="Determination references an absent or foreign-scope EOT submission",
                )
    for item in submission_items:
        parent_id = str(item.get("eot_submission_id") or "")
        parent = submission_by_id.get(parent_id)
        if parent is None:
            add_event_finding(
                "eot_submission",
                parent_id,
                "orphan_event",
                detail="Submission item has no owning EOT submission",
            )
        elif not event_owns_milestone(
            parent, milestone_by_id.get(str(item.get("key_date_id") or ""))
        ):
            add_event_finding(
                "eot_submission",
                parent_id,
                "orphan_event",
                detail="Submission item references a missing or foreign-scope Key Date",
            )
    for item in determination_items:
        parent_id = str(item.get("determination_id") or "")
        parent = determination_by_id.get(parent_id)
        if parent is None:
            add_event_finding(
                "eot_determination",
                parent_id,
                "orphan_event",
                detail="Determination item has no owning EOT determination",
            )
        elif not event_owns_milestone(
            parent, milestone_by_id.get(str(item.get("key_date_id") or ""))
        ):
            add_event_finding(
                "eot_determination",
                parent_id,
                "orphan_event",
                detail="Determination item references a missing or foreign-scope Key Date",
            )

    for milestone in milestones:
        milestone_id = str(milestone.get("_id") or "")
        if milestone.get("client_notification_ref") and not (
            milestone.get("linked_document_ids") or []
        ):
            metadata_references.append(
                {
                    "kind": "notification_metadata_without_document",
                    "target_type": "key_date_achievement",
                    "target_id": f"{milestone_id}:ach",
                    "reference": str(milestone.get("client_notification_ref")),
                }
            )

    sources: list[tuple[str, Any, str | None, str | None]] = [
        ("key_date", db.key_date_milestones, None, None),
        ("key_date_achievement", db.key_date_achievements, "milestone_id", "key_date"),
        ("eot_submission", db.key_date_eot_submissions, None, "project"),
        ("eot_determination", db.key_date_eot_determinations, None, "project"),
        ("legacy_eot", db.key_date_eot_applications, "milestone_id", "key_date"),
    ]

    for target_type, collection, parent_field, parent_type in sources:
        for event in await _rows(collection):
            target_id = str(event.get("_id") or "")
            organization_id = str(event.get("organization_id") or "")
            project_id = str(event.get("project_id") or "")
            parent_id = (
                str(event.get(parent_field) or "")
                if parent_field
                else project_id if parent_type == "project" else target_id
            )
            base_findings: list[str] = []
            if parent_field:
                parent = milestone_by_id.get(parent_id)
                if parent is None:
                    base_findings.append("orphan_event")
                else:
                    organization_id = organization_id or str(
                        parent.get("organization_id") or ""
                    )
                    project_id = project_id or str(parent.get("project_id") or "")
                    if (
                        str(event.get("organization_id") or organization_id)
                        != str(parent.get("organization_id") or "")
                        or str(event.get("project_id") or project_id)
                        != str(parent.get("project_id") or "")
                    ):
                        base_findings.append("orphan_event")
            if target_type == "legacy_eot":
                base_findings.extend(["legacy_eot", "manual_review"])
            if target_type == "eot_submission" and str(event.get("status") or "") == "superseded":
                base_findings.append("superseded_submission")
            if (
                target_type == "eot_submission" and event.get("locked_at")
            ) or (
                target_type == "eot_determination" and event.get("frozen_at")
            ):
                base_findings.append("frozen_target")

            seen: set[str] = set()
            occurrence: dict[str, int] = {}
            for raw_document_id in event.get("linked_document_ids") or []:
                normalized = (
                    raw_document_id.strip()
                    if isinstance(raw_document_id, str)
                    else str(raw_document_id or "")
                )
                occurrence[normalized] = occurrence.get(normalized, 0) + 1
                if normalized in seen:
                    document_id = normalized
                    findings = ["duplicate", *base_findings]
                    classification = "duplicate"
                else:
                    seen.add(normalized)
                    document_id, findings, classification = await _document_findings(
                        db,
                        raw_document_id=raw_document_id,
                        organization_id=organization_id,
                        project_id=project_id,
                    )
                    if (
                        "missing_document" in findings
                        and ("/" in document_id or "\\" in document_id)
                    ):
                        metadata_references.append(
                            {
                                "kind": "textual_reference_mistaken_for_document",
                                "target_type": target_type,
                                "target_id": target_id,
                                "reference": document_id,
                            }
                        )
                    findings.extend(base_findings)
                    if findings and findings[0] == "valid":
                        findings.append("ambiguous_role")
                        if target_type in {"key_date", "legacy_eot"}:
                            findings.append("ambiguous_event")
                        findings.append("manual_review")
                        classification = "manual_review"
                findings = list(dict.fromkeys(findings))
                for finding in findings:
                    if finding in counts:
                        counts[finding] += 1
                candidates.append(
                    {
                        "target_type": target_type,
                        "target_id": target_id,
                        "parent_type": parent_type,
                        "parent_id": parent_id or None,
                        "source_kind": SOURCE_KIND_BY_TARGET.get(
                            target_type, UNCLASSIFIED_SOURCE_KIND
                        ),
                        "legacy_field": "linked_document_ids",
                        "document_id": document_id,
                        "occurrence": occurrence[normalized],
                        "relationship_role": None,
                        "classification": classification,
                        "findings": findings,
                    }
                )

    candidates.sort(
        key=lambda row: (
            row["target_type"],
            row["target_id"],
            row["document_id"],
            row["occurrence"],
        )
    )
    metadata_references.sort(
        key=lambda row: (row["target_type"], row["target_id"], row["reference"])
    )
    event_findings.sort(
        key=lambda row: (row["target_type"], row["target_id"], row["classification"])
    )
    return {
        "dry_run": True,
        "candidate_count": len(candidates),
        "counts": counts,
        "candidates": candidates,
        "event_findings": event_findings,
        "metadata_references": metadata_references,
    }
