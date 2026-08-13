"""Backfill EOT determination origin/attribution and repair contractual dates.

Brings records written before the origin (P3) and per-claim attribution (P4)
work up to the current shape, and re-derives every milestone's in-force
contractual date from the frozen record.

The recompute is the part with contractual consequence. Determinations frozen
out of revision order used to write their grant date straight onto the
milestone, so a project where EOT-1 was determined after EOT-2 is holding a
date that was pulled backwards. This migration corrects that — and reports every
date it changes as a warning, because a silently adjusted contractual date is
exactly the kind of change a contract team must see rather than discover.
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..models.key_date import EOTDeterminationOrigin
from ..services.key_date_revision_service import KeyDateRevisionService
from .runner import MigrationResult


VERSION = "20260813_0001"
NAME = "key_date_eot_attribution"
DESCRIPTION = (
    "Backfill determination origin, per-item source submission and covered claims, "
    "initialise document/letter link arrays, and re-derive contractual dates from "
    "all frozen determinations in contractual order."
)


async def _rows(cursor: Any) -> List[Dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=None)
    return [row async for row in cursor]


async def upgrade(db: Any, dry_run: bool) -> MigrationResult:
    operations: List[Dict[str, Any]] = []
    warnings: List[str] = []

    determinations = await _rows(db.key_date_eot_determinations.find({}))
    submissions = await _rows(db.key_date_eot_submissions.find({}))
    submission_by_id = {str(row.get("_id")): row for row in submissions}

    # 1. Origin + link arrays on determinations.
    needs_origin = [row for row in determinations if not row.get("origin")]
    operations.append({
        "operation": "backfill_determination_origin",
        "collection": "key_date_eot_determinations",
        "count": len(needs_origin),
        "value": EOTDeterminationOrigin.CONTRACTOR_SUBMISSION.value,
        "note": "records predating employer-initiated determinations",
    })

    # 2. Link arrays on submissions + explicit supersession defaults.
    needs_submission_defaults = [
        row for row in submissions
        if row.get("linked_document_ids") is None
        or row.get("superseded_by_submission_id", "missing") == "missing"
    ]
    operations.append({
        "operation": "backfill_submission_defaults",
        "collection": "key_date_eot_submissions",
        "count": len(needs_submission_defaults),
    })

    # 3. Per-item attribution. The pre-P4 code attributed a milestone to the
    #    LAST covered submission, so the backfill reproduces that rule exactly —
    #    a migration must not silently restate what a historical record meant.
    item_updates: List[Dict[str, Any]] = []
    for determination in determinations:
        covered_ids = [str(value) for value in determination.get("eot_submission_ids") or []]
        covered = sorted(
            (submission_by_id[value] for value in covered_ids if value in submission_by_id),
            key=lambda row: int(row.get("revision_number") or 0),
        )
        claims_by_ref: Dict[str, List[Dict[str, Any]]] = {}
        for submission in covered:
            for claim in await _rows(db.key_date_eot_submission_items.find(
                {"eot_submission_id": str(submission.get("_id"))}
            )):
                claims_by_ref.setdefault(
                    str(claim.get("milestone_ref") or "").casefold(), []
                ).append({
                    "eot_submission_id": str(submission.get("_id")),
                    "revision_label": submission.get("revision_label"),
                    "submitted_date": claim.get("eot_submitted_date"),
                    "claimed_extension_days": claim.get("claimed_extension_days"),
                })
        for item in await _rows(db.key_date_eot_determination_items.find(
            {"determination_id": str(determination.get("_id"))}
        )):
            if item.get("source_submission_id") and item.get("covered_claims") is not None:
                continue
            claims = claims_by_ref.get(str(item.get("milestone_ref") or "").casefold(), [])
            item_updates.append({
                "_id": item.get("_id"),
                "source_submission_id": claims[-1]["eot_submission_id"] if claims else None,
                "covered_claims": claims,
            })
    operations.append({
        "operation": "backfill_determination_item_attribution",
        "collection": "key_date_eot_determination_items",
        "count": len(item_updates),
        "note": "source = latest covered submission, reproducing the pre-P4 rule",
    })

    # 4. Re-derive contractual dates per project/contract holding frozen determinations.
    scopes: List[Dict[str, Any]] = []
    for determination in determinations:
        if not determination.get("frozen_at"):
            continue
        scope = {
            "organization_id": determination.get("organization_id"),
            "project_id": determination.get("project_id"),
            "contract_id": determination.get("contract_id") or "primary",
        }
        if scope not in scopes:
            scopes.append(scope)

    if dry_run:
        operations.append({
            "operation": "recompute_contractual_dates",
            "scopes": len(scopes),
            "changes": [],
            "note": "dry run - no dates evaluated or written",
        })
        return MigrationResult(
            version=VERSION, name=NAME, status="dry_run",
            operations=operations, warnings=warnings,
        )

    for determination in determinations:
        await db.key_date_eot_determinations.update_one(
            {"_id": determination.get("_id")},
            {"$set": {
                "origin": determination.get("origin")
                or EOTDeterminationOrigin.CONTRACTOR_SUBMISSION.value,
                "linked_document_ids": determination.get("linked_document_ids") or [],
                "linked_letter_ids": determination.get("linked_letter_ids") or [],
            }},
        )
    for submission in submissions:
        await db.key_date_eot_submissions.update_one(
            {"_id": submission.get("_id")},
            {"$set": {
                "linked_document_ids": submission.get("linked_document_ids") or [],
                "linked_letter_ids": submission.get("linked_letter_ids") or [],
                "superseded_by_submission_id": submission.get("superseded_by_submission_id"),
                "superseded_reason": submission.get("superseded_reason"),
                "superseded_at": submission.get("superseded_at"),
                "superseded_by": submission.get("superseded_by"),
            }},
        )
    for update in item_updates:
        await db.key_date_eot_determination_items.update_one(
            {"_id": update["_id"]},
            {"$set": {
                "source_submission_id": update["source_submission_id"],
                "covered_claims": update["covered_claims"],
            }},
        )

    service = KeyDateRevisionService(db)
    all_changes: List[Dict[str, Any]] = []
    for scope in scopes:
        changes = await service.recompute_effective_dates(
            scope["organization_id"], scope["project_id"], scope["contract_id"],
        )
        for change in changes:
            all_changes.append({**change, "project_id": scope["project_id"]})
            warnings.append(
                f"Contractual date changed for {change.get('milestone_ref')} in project "
                f"{scope['project_id']}: {change.get('previous_contractual_date')} -> "
                f"{change.get('effective_contractual_date')}. Review before accepting."
            )
    operations.append({
        "operation": "recompute_contractual_dates",
        "scopes": len(scopes),
        "changes": all_changes,
    })

    return MigrationResult(
        version=VERSION, name=NAME, status="applied",
        operations=operations, warnings=warnings,
    )
