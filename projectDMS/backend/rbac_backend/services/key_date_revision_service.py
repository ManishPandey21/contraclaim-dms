"""Project/contract key-date baseline and successive EOT revision workflow.

This service deliberately keeps contractor submissions separate from client
determinations. Pending submissions have stable EOT-N identities, submission
items snapshot the contractual date applicable at submission, and only a frozen
determination may change a milestone's current contractual date.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from bson import ObjectId
from pymongo import ReturnDocument

from ..models.csv_import import CSVImportPreview, CSVImportResult, CSVImportRow
from ..models.key_date import (
    BaselineStatus,
    EOTDeterminationCreate,
    EOTDeterminationOrigin,
    EOTDeterminationResult,
    EOTDeterminationStatus,
    EOTDeterminationUpdate,
    EOTSubmissionCreate,
    EOTSubmissionItemInput,
    EOTSubmissionStatus,
    EOTSubmissionUpdate,
    SubmissionOutcome,
)
from .audit_event_service import AuditEventService
from .key_date_service import KeyDateError, _as_dt, current_key_date, format_date


FINAL_DETERMINATION_STATUSES = {
    EOTDeterminationStatus.GRANTED.value,
    EOTDeterminationStatus.PARTIALLY_GRANTED.value,
    EOTDeterminationStatus.REJECTED.value,
    EOTDeterminationStatus.NO_EXTENSION.value,
    EOTDeterminationStatus.SUPERSEDED.value,
}
EFFECTIVE_RESULTS = {
    EOTDeterminationResult.GRANTED.value,
    EOTDeterminationResult.PARTIALLY_GRANTED.value,
}
OPEN_SUBMISSION_STATUSES = {
    EOTSubmissionStatus.SUBMITTED.value,
    EOTSubmissionStatus.LOCKED.value,
}
SUBMISSION_TEMPLATE_HEADERS = [
    "milestone_ref",
    "description",
    "original_contractual_date",
    "current_contractual_date",
    "eot_submitted_date",
    "claimed_extension_days",
    "remarks",
]
DETERMINATION_TEMPLATE_HEADERS = [
    "milestone_ref",
    "description",
    "source_submission_ref",
    "submitted_date",
    "current_contractual_date",
    "eot_granted_date",
    "granted_extension_days",
    "determination_result",
    "remarks",
]


def _id() -> str:
    return str(uuid.uuid4())


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


def _clean_ref(value: Any) -> str:
    return str(value or "").strip()


def _iso(value: Any) -> str:
    """Day-first rendering for CSV previews and templates (see format_date)."""
    return format_date(value)


async def _cursor_list(cursor: Any) -> List[Dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=None)
    return [row async for row in cursor]


class KeyDateRevisionService:
    def __init__(self, db: Any) -> None:
        self.db = db
        self.audit = AuditEventService(db)

    @staticmethod
    def scope(
        organization_id: Optional[str], project_id: str, contract_id: str = "primary"
    ) -> Dict[str, Any]:
        scope: Dict[str, Any] = {"project_id": str(project_id), "contract_id": str(contract_id or "primary")}
        if organization_id:
            scope["organization_id"] = str(organization_id)
        return scope

    async def baseline(
        self, organization_id: Optional[str], project_id: str, contract_id: str = "primary"
    ) -> Optional[Dict[str, Any]]:
        return await self.db.key_date_baselines.find_one(self.scope(organization_id, project_id, contract_id))

    # NOTE: the frozen-baseline guard lives in
    # KeyDateService._assert_original_baseline_editable, which every write path
    # already calls. A duplicate here was dead code and is deliberately absent —
    # see test_only_one_frozen_baseline_guard_exists.

    async def freeze_baseline(
        self,
        organization_id: Optional[str],
        project_id: str,
        contract_id: str,
        current_user: Any,
        *,
        source: str = "API",
    ) -> Dict[str, Any]:
        scope = self.scope(organization_id, project_id, contract_id)
        existing = await self.db.key_date_baselines.find_one(scope)
        if existing and existing.get("status") == BaselineStatus.FROZEN.value:
            return existing

        milestone_query: Dict[str, Any] = {"project_id": str(project_id)}
        if organization_id:
            milestone_query["organization_id"] = str(organization_id)
        milestones = await _cursor_list(
            self.db.key_date_milestones.find(milestone_query).sort("milestone_ref", 1)
        )
        milestones = [
            row for row in milestones
            if str(row.get("contract_id") or "primary") == str(contract_id or "primary")
        ]
        if not milestones:
            raise KeyDateError("Add at least one Original Key Date before freezing the baseline")

        active_legacy = await self.db.key_date_eot_applications.find_one({
            "project_id": str(project_id),
            **({"organization_id": str(organization_id)} if organization_id else {}),
            "status": {"$in": ["draft", "submitted", "under_review"]},
        })
        if active_legacy:
            raise KeyDateError(
                "Resolve or migrate active legacy milestone EOT applications before freezing the project baseline"
            )

        seen: set[str] = set()
        snapshot: List[Dict[str, Any]] = []
        for milestone in milestones:
            ref = _clean_ref(milestone.get("milestone_ref"))
            if not ref:
                raise KeyDateError("Every milestone requires a stable Milestone Ref before baseline freeze")
            key = ref.casefold()
            if key in seen:
                raise KeyDateError(f"Duplicate Milestone Ref '{ref}' in this project")
            seen.add(key)
            original = _as_dt(milestone.get("original_planned_key_date"))
            if not original:
                raise KeyDateError(f"Milestone {ref} has no Original Contractual Key Date")
            snapshot.append({
                "key_date_id": str(milestone.get("_id")),
                "milestone_ref": ref,
                "title": milestone.get("title"),
                "description": milestone.get("description"),
                "contractual_week_number": milestone.get("contractual_week_number"),
                "original_contractual_date": original,
                "responsible_party_id": milestone.get("responsible_party_id"),
                "remarks": milestone.get("remarks"),
            })

        now = datetime.utcnow()
        doc = {
            "_id": str((existing or {}).get("_id") or _id()),
            **scope,
            "status": BaselineStatus.FROZEN.value,
            "revision_number": 0,
            "items": snapshot,
            "frozen_at": now,
            "frozen_by": getattr(current_user, "id", None),
            "created_at": (existing or {}).get("created_at") or now,
            "created_by": (existing or {}).get("created_by") or getattr(current_user, "id", None),
            "next_revision_number": int((existing or {}).get("next_revision_number") or 0),
        }
        if existing:
            updated = await self.db.key_date_baselines.find_one_and_update(
                {"_id": existing["_id"], "status": {"$ne": BaselineStatus.FROZEN.value}},
                {"$set": doc},
                return_document=ReturnDocument.AFTER,
            )
            if not updated:
                return await self.db.key_date_baselines.find_one(scope)
            doc = updated
        else:
            try:
                await self.db.key_date_baselines.insert_one(doc)
            except Exception as exc:
                raced = await self.db.key_date_baselines.find_one(scope)
                if raced and raced.get("status") == BaselineStatus.FROZEN.value:
                    return raced
                raise KeyDateError("Unable to freeze baseline because another update won the race") from exc

        await self._emit(
            "keydate.baseline.frozen", current_user, doc, source=source,
            after={"revision_number": 0, "item_count": len(snapshot)},
        )
        return doc

    async def _assert_links_in_scope(
        self,
        organization_id: Optional[str],
        project_id: str,
        *,
        document_ids: Optional[Sequence[str]] = None,
        letter_ids: Optional[Sequence[str]] = None,
    ) -> None:
        """Every linked document/letter must live in the same org and project.

        An unvalidated link is a cross-tenant read primitive: the id is supplied
        by the caller and later resolved on the reader's behalf. Anything that
        does not resolve inside the EOT's own scope — wrong org, wrong project,
        deleted, or not a valid id at all — is refused rather than stored.
        """
        for label, raw_ids, collection in (
            ("document", document_ids, self.db.documents),
            ("letter", letter_ids, self.db.letters),
        ):
            wanted = [_clean_ref(value) for value in (raw_ids or []) if _clean_ref(value)]
            if not wanted:
                continue
            object_ids = []
            for value in wanted:
                try:
                    object_ids.append(ObjectId(value))
                except Exception:
                    raise KeyDateError(
                        f"Linked {label} '{value}' is outside the selected project"
                    ) from None
            query: Dict[str, Any] = {"_id": {"$in": object_ids}, "project_id": str(project_id)}
            if organization_id:
                query["organization_id"] = str(organization_id)
            found = {str(row.get("_id")) for row in await _cursor_list(collection.find(query))}
            missing = [value for value in wanted if value not in found]
            if missing:
                raise KeyDateError(
                    f"Linked {label} '{missing[0]}' is outside the selected project"
                )

    async def _milestones_by_ref(
        self, organization_id: Optional[str], project_id: str, contract_id: str = "primary"
    ) -> Dict[str, Dict[str, Any]]:
        query: Dict[str, Any] = {"project_id": str(project_id)}
        if organization_id:
            query["organization_id"] = str(organization_id)
        rows = [
            row for row in await _cursor_list(self.db.key_date_milestones.find(query))
            if str(row.get("contract_id") or "primary") == str(contract_id or "primary")
        ]
        return {
            _clean_ref(row.get("milestone_ref")).casefold(): row
            for row in rows
            if _clean_ref(row.get("milestone_ref"))
        }

    async def _submission_item_docs(
        self,
        organization_id: Optional[str],
        project_id: str,
        contract_id: str,
        submission_id: str,
        items: Sequence[EOTSubmissionItemInput],
    ) -> List[Dict[str, Any]]:
        milestones = await self._milestones_by_ref(organization_id, project_id, contract_id)
        docs: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            ref = _clean_ref(item.milestone_ref)
            key = ref.casefold()
            if key in seen:
                raise KeyDateError(f"Duplicate EOT item for Milestone Ref '{ref}'")
            seen.add(key)
            milestone = milestones.get(key)
            if not milestone:
                raise KeyDateError(f"Milestone Ref '{ref}' does not belong to the selected project")
            docs.append({
                "_id": _id(),
                "eot_submission_id": submission_id,
                "key_date_id": str(milestone.get("_id")),
                "milestone_ref": ref,
                "description": milestone.get("description") or milestone.get("title"),
                "original_contractual_date": _as_dt(milestone.get("original_planned_key_date")),
                "contractual_date_at_submission": current_key_date(milestone),
                "eot_submitted_date": item.eot_submitted_date,
                "claimed_extension_days": item.claimed_extension_days,
                "remarks": item.remarks,
            })
        return docs

    async def create_submission(
        self, payload: EOTSubmissionCreate, current_user: Any, *, source: str = "API"
    ) -> Dict[str, Any]:
        organization_id = payload.organization_id or getattr(current_user, "organization_id", None)
        scope = self.scope(organization_id, payload.project_id, payload.contract_id)
        baseline = await self.db.key_date_baselines.find_one({**scope, "status": BaselineStatus.FROZEN.value})
        if not baseline:
            raise KeyDateError("Freeze the Original Key Date baseline before creating an EOT submission")

        await self._assert_links_in_scope(
            organization_id, payload.project_id,
            document_ids=payload.linked_document_ids, letter_ids=payload.linked_letter_ids,
        )
        submission_id = _id()
        item_docs = await self._submission_item_docs(
            organization_id, payload.project_id, payload.contract_id, submission_id, payload.items
        )
        counter = await self.db.key_date_baselines.find_one_and_update(
            {"_id": baseline["_id"], "status": BaselineStatus.FROZEN.value},
            {"$inc": {"next_revision_number": 1}},
            return_document=ReturnDocument.AFTER,
        )
        if not counter:
            raise KeyDateError("The baseline changed while the EOT revision was being allocated")
        revision = int(counter.get("next_revision_number") or 0)
        if revision < 1:
            raise KeyDateError("Unable to allocate the next EOT revision number")

        requested_status = _enum_value(payload.status)
        if requested_status not in {EOTSubmissionStatus.DRAFT.value, EOTSubmissionStatus.SUBMITTED.value}:
            raise KeyDateError("New EOT submissions must start as draft or submitted")
        now = datetime.utcnow()
        doc = {
            "_id": submission_id,
            **scope,
            "revision_number": revision,
            "revision_label": f"EOT-{revision}",
            "eot_reference": payload.eot_reference,
            "contractor_submission_date": payload.contractor_submission_date,
            "contractor_letter_reference": payload.contractor_letter_reference,
            "claim_cutoff_date": payload.claim_cutoff_date,
            "status": requested_status,
            "remarks": payload.remarks,
            "linked_document_ids": list(payload.linked_document_ids or []),
            "linked_letter_ids": list(payload.linked_letter_ids or []),
            "created_at": now,
            "created_by": getattr(current_user, "id", None),
            "locked_at": None,
            "locked_by": None,
            "items_count": len(item_docs),
        }
        try:
            await self.db.key_date_eot_submissions.insert_one(doc)
            if item_docs:
                await self.db.key_date_eot_submission_items.insert_many(item_docs)
        except Exception as exc:
            try:
                await self.db.key_date_eot_submission_items.delete_many({"eot_submission_id": submission_id})
                await self.db.key_date_eot_submissions.delete_one({"_id": submission_id})
            except Exception:
                pass
            raise KeyDateError("Could not create the EOT submission revision") from exc

        await self._emit(
            "keydate.eot_submission.created", current_user, doc, source=source,
            after={"revision": revision, "item_count": len(item_docs), "status": requested_status},
        )
        return await self.get_submission(submission_id)

    async def get_submission(self, submission_id: str) -> Optional[Dict[str, Any]]:
        doc = await self.db.key_date_eot_submissions.find_one({"_id": str(submission_id)})
        if not doc:
            return None
        items = await _cursor_list(
            self.db.key_date_eot_submission_items.find({"eot_submission_id": str(submission_id)}).sort(
                "milestone_ref", 1
            )
        )
        return {**doc, "items": items}

    async def list_submissions(
        self, organization_id: Optional[str], project_id: str, contract_id: str = "primary"
    ) -> List[Dict[str, Any]]:
        rows = await _cursor_list(
            self.db.key_date_eot_submissions.find(
                self.scope(organization_id, project_id, contract_id)
            ).sort("revision_number", 1)
        )
        for row in rows:
            row["items"] = await _cursor_list(
                self.db.key_date_eot_submission_items.find(
                    {"eot_submission_id": str(row.get("_id"))}
                ).sort("milestone_ref", 1)
            )
        return rows

    async def update_submission(
        self,
        submission: Dict[str, Any],
        payload: EOTSubmissionUpdate,
        current_user: Any,
        *,
        source: str = "API",
    ) -> Dict[str, Any]:
        if submission.get("status") == EOTSubmissionStatus.LOCKED.value or submission.get("locked_at"):
            raise KeyDateError("Locked EOT submissions are immutable")
        update = payload.model_dump(exclude_unset=True, exclude={"items"})
        if "status" in update:
            status = _enum_value(update["status"])
            if status not in {EOTSubmissionStatus.DRAFT.value, EOTSubmissionStatus.SUBMITTED.value}:
                raise KeyDateError("Use the lock action to finalize an EOT submission")
            update["status"] = status
        if "linked_document_ids" in update or "linked_letter_ids" in update:
            await self._assert_links_in_scope(
                submission.get("organization_id"), submission["project_id"],
                document_ids=update.get("linked_document_ids"),
                letter_ids=update.get("linked_letter_ids"),
            )
        update["updated_at"] = datetime.utcnow()
        update["updated_by"] = getattr(current_user, "id", None)

        new_items: Optional[List[Dict[str, Any]]] = None
        if payload.items is not None:
            new_items = await self._submission_item_docs(
                submission.get("organization_id"), submission["project_id"],
                submission.get("contract_id", "primary"), str(submission["_id"]), payload.items
            )
            update["items_count"] = len(new_items)

        result = await self.db.key_date_eot_submissions.find_one_and_update(
            {"_id": submission["_id"], "locked_at": None},
            {"$set": update},
            return_document=ReturnDocument.AFTER,
        )
        if not result:
            raise KeyDateError("The EOT submission was locked by another user")
        if new_items is not None:
            await self.db.key_date_eot_submission_items.delete_many(
                {"eot_submission_id": str(submission["_id"])}
            )
            if new_items:
                await self.db.key_date_eot_submission_items.insert_many(new_items)
        await self._emit(
            "keydate.eot_submission.updated", current_user, result, source=source,
            before={"status": submission.get("status")},
            after={"status": result.get("status"), "item_count": result.get("items_count")},
        )
        return await self.get_submission(str(submission["_id"]))

    async def lock_submission(
        self, submission: Dict[str, Any], current_user: Any, *, source: str = "API"
    ) -> Dict[str, Any]:
        if submission.get("status") == EOTSubmissionStatus.LOCKED.value and submission.get("locked_at"):
            return await self.get_submission(str(submission["_id"]))
        full = await self.get_submission(str(submission["_id"]))
        if not (full or {}).get("items"):
            raise KeyDateError("Add at least one affected milestone before locking the EOT submission")
        if not _clean_ref(submission.get("contractor_letter_reference")):
            raise KeyDateError("Contractor Letter Reference is required before locking the submission")
        if not _as_dt(submission.get("contractor_submission_date")):
            raise KeyDateError("Contractor Submission Date is required before locking the submission")
        now = datetime.utcnow()
        updated = await self.db.key_date_eot_submissions.find_one_and_update(
            {
                "_id": submission["_id"],
                "status": {"$in": [EOTSubmissionStatus.DRAFT.value, EOTSubmissionStatus.SUBMITTED.value]},
                "locked_at": None,
            },
            {"$set": {
                "status": EOTSubmissionStatus.LOCKED.value,
                "locked_at": now,
                "locked_by": getattr(current_user, "id", None),
            }},
            return_document=ReturnDocument.AFTER,
        )
        if not updated:
            raced = await self.db.key_date_eot_submissions.find_one({"_id": submission["_id"]})
            if raced and raced.get("status") == EOTSubmissionStatus.LOCKED.value:
                return await self.get_submission(str(submission["_id"]))
            raise KeyDateError("Only a draft or submitted EOT can be locked")
        await self._emit(
            "keydate.eot_submission.locked", current_user, updated, source=source,
            after={"revision": updated.get("revision_number"), "locked_at": str(now)},
        )
        return await self.get_submission(str(submission["_id"]))

    async def _determined_submission_ids(
        self,
        organization_id: Optional[str],
        project_id: Optional[str],
        contract_id: str = "primary",
        *,
        exclude_determination_id: Optional[str] = None,
    ) -> set[str]:
        """Submissions already answered by a frozen determination."""
        if not project_id:
            return set()
        rows = await _cursor_list(
            self.db.key_date_eot_determinations.find(
                self.scope(organization_id, project_id, contract_id)
            )
        )
        return {
            str(submission_id)
            for row in rows
            if row.get("frozen_at") and str(row.get("_id")) != str(exclude_determination_id or "")
            for submission_id in row.get("eot_submission_ids") or []
        }

    @staticmethod
    def submission_outcomes(
        submissions: Sequence[Dict[str, Any]],
        determinations: Sequence[Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        """Derive each submission's determination outcome. Never persisted.

        A submission is answered only by a *frozen* determination that covers it
        and actually rules on a milestone it claimed. When a later submission has
        been answered and this one has not, the earlier claim was never determined
        in its own right — which is a materially different contractual position
        from simply awaiting a decision.
        """
        frozen = [
            row for row in determinations
            if row.get("frozen_at") and _enum_value(row.get("status")) in FINAL_DETERMINATION_STATUSES
        ]
        covering: Dict[str, List[Dict[str, Any]]] = {}
        for determination in frozen:
            for submission_id in determination.get("eot_submission_ids") or []:
                covering.setdefault(str(submission_id), []).append(determination)

        claimed_refs = {
            str(row.get("_id")): {
                _clean_ref(item.get("milestone_ref")).casefold()
                for item in row.get("items") or []
            }
            for row in submissions
        }
        answered_refs_by_revision: List[Tuple[int, set]] = []
        for row in submissions:
            submission_id = str(row.get("_id"))
            matched = {
                _clean_ref(item.get("milestone_ref")).casefold()
                for determination in covering.get(submission_id, [])
                for item in determination.get("items") or []
            } & claimed_refs.get(submission_id, set())
            if matched:
                answered_refs_by_revision.append((int(row.get("revision_number") or 0), matched))

        out: Dict[str, Dict[str, Any]] = {}
        for row in submissions:
            submission_id = str(row.get("_id"))
            status = _enum_value(row.get("status"))
            refs = claimed_refs.get(submission_id, set())
            determining: List[str] = []
            results: List[str] = []
            for determination in covering.get(submission_id, []):
                hits = [
                    item for item in determination.get("items") or []
                    if _clean_ref(item.get("milestone_ref")).casefold() in refs
                ]
                if hits:
                    determining.append(str(determination.get("_id")))
                    results += [_enum_value(item.get("determination_result")) for item in hits]

            if row.get("superseded_by_submission_id"):
                outcome = SubmissionOutcome.SUPERSEDED.value
            elif status == EOTSubmissionStatus.WITHDRAWN.value:
                outcome = SubmissionOutcome.WITHDRAWN.value
            elif status == EOTSubmissionStatus.DRAFT.value:
                outcome = SubmissionOutcome.DRAFT.value
            elif results:
                if all(result == EOTDeterminationResult.GRANTED.value for result in results):
                    outcome = SubmissionOutcome.ACCEPTED.value
                elif not any(result in EFFECTIVE_RESULTS for result in results):
                    outcome = SubmissionOutcome.REJECTED.value
                else:
                    outcome = SubmissionOutcome.PARTIALLY_ACCEPTED.value
            elif any(
                revision > int(row.get("revision_number") or 0) and (answered & refs)
                for revision, answered in answered_refs_by_revision
            ):
                outcome = SubmissionOutcome.NOT_SEPARATELY_DETERMINED.value
            else:
                outcome = SubmissionOutcome.PENDING.value
            out[submission_id] = {
                "determination_outcome": outcome,
                "determining_determination_ids": determining,
            }
        return out

    async def supersede_submission(
        self,
        submission: Dict[str, Any],
        superseded_by_submission_id: str,
        reason: str,
        current_user: Any,
        *,
        source: str = "API",
    ) -> Dict[str, Any]:
        """Explicitly replace a locked submission with a later one.

        Never inferred: a later EOT on the same milestone is usually an additional
        claim, not a replacement, so supersession is an audited act with a reason.
        """
        if not _clean_ref(reason):
            raise KeyDateError("A supersession requires a reason")
        if submission.get("superseded_by_submission_id"):
            raise KeyDateError("This EOT submission has already been superseded")
        if submission.get("status") != EOTSubmissionStatus.LOCKED.value:
            raise KeyDateError("Only a locked EOT submission can be superseded")

        scope = self.scope(
            submission.get("organization_id"),
            submission["project_id"],
            submission.get("contract_id", "primary"),
        )
        replacement = await self.db.key_date_eot_submissions.find_one(
            {"_id": str(superseded_by_submission_id), **scope}
        )
        if not replacement:
            raise KeyDateError("The superseding submission must belong to the same project/contract")
        if int(replacement.get("revision_number") or 0) <= int(submission.get("revision_number") or 0):
            raise KeyDateError("A submission can only be superseded by a later EOT revision")
        if replacement.get("superseded_by_submission_id"):
            raise KeyDateError("The superseding submission has itself been superseded")

        determined = await self._determined_submission_ids(
            submission.get("organization_id"),
            submission["project_id"],
            submission.get("contract_id", "primary"),
        )
        if str(submission["_id"]) in determined:
            raise KeyDateError(
                "This EOT submission has already been determined and cannot be superseded"
            )

        now = datetime.utcnow()
        updated = await self.db.key_date_eot_submissions.find_one_and_update(
            {"_id": submission["_id"], "superseded_by_submission_id": None},
            {"$set": {
                "status": EOTSubmissionStatus.SUPERSEDED.value,
                "superseded_by_submission_id": str(superseded_by_submission_id),
                "superseded_reason": _clean_ref(reason),
                "superseded_at": now,
                "superseded_by": getattr(current_user, "id", None),
            }},
            return_document=ReturnDocument.AFTER,
        )
        if not updated:
            raise KeyDateError("The EOT submission was superseded by another user")
        await self._emit(
            "keydate.eot_submission.superseded", current_user, updated, source=source,
            before={"status": submission.get("status")},
            after={
                "superseded_by": str(superseded_by_submission_id),
                "reason": _clean_ref(reason),
            },
        )
        return await self.get_submission(str(submission["_id"]))

    async def _determination_item_docs(
        self,
        determination_id: str,
        submissions: Sequence[Dict[str, Any]],
        items: Sequence[Any],
        *,
        organization_id: Optional[str] = None,
        project_id: Optional[str] = None,
        contract_id: str = "primary",
    ) -> List[Dict[str, Any]]:
        # Keyed on milestone ref, but keeping EVERY covered claim rather than the
        # last one seen: a determination may answer EOT-1 and EOT-2 where both
        # claimed the same milestone, and both claims must stay traceable.
        covered_by_ref: Dict[str, List[Tuple[Dict[str, Any], Dict[str, Any]]]] = {}
        for submission in sorted(submissions, key=lambda row: int(row.get("revision_number") or 0)):
            full = await self.get_submission(str(submission["_id"]))
            for item in (full or {}).get("items", []):
                covered_by_ref.setdefault(
                    _clean_ref(item.get("milestone_ref")).casefold(), []
                ).append((submission, item))

        already_determined = await self._determined_submission_ids(
            organization_id, project_id, contract_id, exclude_determination_id=determination_id
        )

        # An employer-initiated determination answers no claim, so its milestones
        # are resolved against the project register rather than a submission.
        milestones_by_ref: Optional[Dict[str, Dict[str, Any]]] = None
        if not submissions:
            milestones_by_ref = await self._milestones_by_ref(
                organization_id, str(project_id), contract_id
            )

        docs: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            ref = _clean_ref(item.milestone_ref)
            key = ref.casefold()
            if key in seen:
                raise KeyDateError(f"Duplicate determination item for Milestone Ref '{ref}'")
            seen.add(key)
            covered = covered_by_ref.get(key) or []
            covered_claims: List[Dict[str, Any]] = []
            source_submission_id: Optional[str] = None
            if covered:
                covered_claims = [
                    {
                        "eot_submission_id": str(submission.get("_id")),
                        "revision_label": submission.get("revision_label"),
                        "submitted_date": claim.get("eot_submitted_date"),
                        "claimed_extension_days": claim.get("claimed_extension_days"),
                    }
                    for submission, claim in covered
                ]
                requested = _clean_ref(getattr(item, "source_submission_id", None))
                if requested:
                    chosen = next(
                        (pair for pair in covered if str(pair[0].get("_id")) == requested), None
                    )
                    if not chosen:
                        raise KeyDateError(
                            f"The source submission named for {ref} is not among this "
                            f"determination's covered EOT submissions"
                        )
                else:
                    # D6: answer the oldest claim still outstanding on this milestone.
                    chosen = next(
                        (pair for pair in covered if str(pair[0].get("_id")) not in already_determined),
                        covered[0],
                    )
                submission, submitted_item = chosen
                source_submission_id = str(submission.get("_id"))
                milestone = await self.db.key_date_milestones.find_one(
                    {"_id": str(submitted_item.get("key_date_id"))}
                )
                submitted_date = submitted_item.get("eot_submitted_date")
                claimed_days = submitted_item.get("claimed_extension_days")
            elif milestones_by_ref is not None:
                milestone = milestones_by_ref.get(key)
                if not milestone:
                    raise KeyDateError(f"Milestone Ref '{ref}' does not belong to the selected project")
                submitted_date = None
                claimed_days = None
            else:
                raise KeyDateError(
                    f"Milestone Ref '{ref}' is not included in any covered EOT submission"
                )
            if not milestone:
                raise KeyDateError(f"Milestone Ref '{ref}' no longer exists")
            result = _enum_value(item.determination_result)
            if result in EFFECTIVE_RESULTS and not item.eot_granted_date:
                raise KeyDateError(f"Granted Date is required for {ref} when result is {result}")
            docs.append({
                "_id": _id(),
                "determination_id": determination_id,
                "key_date_id": str(milestone.get("_id")),
                "milestone_ref": ref,
                "description": milestone.get("description") or milestone.get("title"),
                "contractual_date_before_determination": current_key_date(milestone),
                "source_submission_id": source_submission_id,
                "submitted_date": submitted_date,
                "claimed_extension_days": claimed_days,
                "covered_claims": covered_claims,
                "eot_granted_date": item.eot_granted_date,
                "granted_extension_days": item.granted_extension_days,
                "determination_result": result,
                "remarks": item.remarks,
            })
        return docs

    async def create_determination(
        self, payload: EOTDeterminationCreate, current_user: Any, *, source: str = "API"
    ) -> Dict[str, Any]:
        organization_id = payload.organization_id or getattr(current_user, "organization_id", None)
        scope = self.scope(organization_id, payload.project_id, payload.contract_id)
        unique_ids = list(dict.fromkeys(str(value) for value in payload.eot_submission_ids))
        origin = _enum_value(payload.origin)
        if origin == EOTDeterminationOrigin.EMPLOYER_INITIATED.value:
            if unique_ids:
                raise KeyDateError(
                    "An employer-initiated determination cannot also cover Contractor EOT submissions"
                )
            if not _clean_ref(payload.remarks):
                raise KeyDateError(
                    "An employer-initiated determination requires a reason recording why it was issued"
                )
        submissions: List[Dict[str, Any]] = []
        for submission_id in unique_ids:
            submission = await self.db.key_date_eot_submissions.find_one({"_id": submission_id, **scope})
            if not submission:
                raise KeyDateError("Every covered EOT submission must belong to the selected project/contract")
            if submission.get("status") != EOTSubmissionStatus.LOCKED.value:
                raise KeyDateError(f"{submission.get('revision_label')} must be locked before determination")
            submissions.append(submission)
        if not submissions and origin != EOTDeterminationOrigin.EMPLOYER_INITIATED.value:
            raise KeyDateError("Select at least one EOT submission for determination")

        for superseded_id in payload.supersedes_determination_ids:
            linked = await self.db.key_date_eot_determinations.find_one({"_id": str(superseded_id), **scope})
            if not linked:
                raise KeyDateError("A superseded determination is outside the selected project/contract")

        await self._assert_links_in_scope(
            organization_id, payload.project_id,
            document_ids=payload.linked_document_ids, letter_ids=payload.linked_letter_ids,
        )
        determination_id = _id()
        item_docs = await self._determination_item_docs(
            determination_id, submissions, payload.items,
            organization_id=organization_id, project_id=payload.project_id,
            contract_id=payload.contract_id,
        )
        status = _enum_value(payload.status)
        if status == EOTDeterminationStatus.SUPERSEDED.value:
            if not payload.supersedes_determination_ids or not _clean_ref(payload.remarks):
                raise KeyDateError(
                    "A superseding determination requires an explicit prior determination link and correction reason"
                )
        now = datetime.utcnow()
        doc = {
            "_id": determination_id,
            **scope,
            "eot_submission_ids": unique_ids,
            "origin": origin,
            "covered_revision_labels": [row.get("revision_label") for row in submissions],
            "determination_reference": payload.determination_reference,
            "determination_date": payload.determination_date,
            "approval_grant_reference": payload.approval_grant_reference,
            "approved_by": payload.approved_by,
            "status": status,
            "remarks": payload.remarks,
            "supersedes_determination_ids": [str(value) for value in payload.supersedes_determination_ids],
            "linked_document_ids": list(payload.linked_document_ids or []),
            "linked_letter_ids": list(payload.linked_letter_ids or []),
            "created_at": now,
            "created_by": getattr(current_user, "id", None),
            "frozen_at": None,
            "frozen_by": None,
            "items_count": len(item_docs),
        }
        try:
            await self.db.key_date_eot_determinations.insert_one(doc)
            if item_docs:
                await self.db.key_date_eot_determination_items.insert_many(item_docs)
        except Exception as exc:
            try:
                await self.db.key_date_eot_determination_items.delete_many(
                    {"determination_id": determination_id}
                )
                await self.db.key_date_eot_determinations.delete_one({"_id": determination_id})
            except Exception:
                pass
            raise KeyDateError("Could not create the EOT determination") from exc
        await self._emit(
            "keydate.eot_determination.created", current_user, doc, source=source,
            after={"covers": doc["covered_revision_labels"], "status": status, "item_count": len(item_docs)},
        )
        return await self.get_determination(determination_id)

    async def get_determination(self, determination_id: str) -> Optional[Dict[str, Any]]:
        doc = await self.db.key_date_eot_determinations.find_one({"_id": str(determination_id)})
        if not doc:
            return None
        items = await _cursor_list(
            self.db.key_date_eot_determination_items.find(
                {"determination_id": str(determination_id)}
            ).sort("milestone_ref", 1)
        )
        return {**doc, "items": items}

    async def list_determinations(
        self, organization_id: Optional[str], project_id: str, contract_id: str = "primary"
    ) -> List[Dict[str, Any]]:
        rows = await _cursor_list(
            self.db.key_date_eot_determinations.find(
                self.scope(organization_id, project_id, contract_id)
            ).sort("created_at", 1)
        )
        for row in rows:
            row["items"] = await _cursor_list(
                self.db.key_date_eot_determination_items.find(
                    {"determination_id": str(row.get("_id"))}
                ).sort("milestone_ref", 1)
            )
        return rows

    async def update_determination(
        self,
        determination: Dict[str, Any],
        payload: EOTDeterminationUpdate,
        current_user: Any,
        *,
        source: str = "API",
    ) -> Dict[str, Any]:
        if determination.get("frozen_at"):
            raise KeyDateError("Frozen determinations are immutable")
        update = payload.model_dump(exclude_unset=True, exclude={"items"})
        if "status" in update:
            update["status"] = _enum_value(update["status"])
        if "supersedes_determination_ids" in update:
            update["supersedes_determination_ids"] = [str(value) for value in update["supersedes_determination_ids"]]
        if "linked_document_ids" in update or "linked_letter_ids" in update:
            await self._assert_links_in_scope(
                determination.get("organization_id"), determination["project_id"],
                document_ids=update.get("linked_document_ids"),
                letter_ids=update.get("linked_letter_ids"),
            )
        update["updated_at"] = datetime.utcnow()
        update["updated_by"] = getattr(current_user, "id", None)
        resulting_status = update.get("status", determination.get("status"))
        resulting_supersedes = update.get(
            "supersedes_determination_ids", determination.get("supersedes_determination_ids") or []
        )
        resulting_reason = update.get("remarks", determination.get("remarks"))
        if resulting_status == EOTDeterminationStatus.SUPERSEDED.value and (
            not resulting_supersedes or not _clean_ref(resulting_reason)
        ):
            raise KeyDateError(
                "A superseding determination requires an explicit prior determination link and correction reason"
            )
        for superseded_id in resulting_supersedes:
            linked = await self.db.key_date_eot_determinations.find_one({
                "_id": str(superseded_id),
                **self.scope(
                    determination.get("organization_id"),
                    determination.get("project_id"),
                    determination.get("contract_id", "primary"),
                ),
            })
            if not linked:
                raise KeyDateError("A superseded determination is outside the selected project/contract")

        new_items: Optional[List[Dict[str, Any]]] = None
        if payload.items is not None:
            submissions = []
            for submission_id in determination.get("eot_submission_ids") or []:
                submission = await self.db.key_date_eot_submissions.find_one({"_id": str(submission_id)})
                if submission:
                    submissions.append(submission)
            new_items = await self._determination_item_docs(
                str(determination["_id"]), submissions, payload.items,
                organization_id=determination.get("organization_id"),
                project_id=determination.get("project_id"),
                contract_id=determination.get("contract_id", "primary"),
            )
            update["items_count"] = len(new_items)

        updated = await self.db.key_date_eot_determinations.find_one_and_update(
            {"_id": determination["_id"], "frozen_at": None},
            {"$set": update},
            return_document=ReturnDocument.AFTER,
        )
        if not updated:
            raise KeyDateError("The determination was frozen by another user")
        if new_items is not None:
            await self.db.key_date_eot_determination_items.delete_many(
                {"determination_id": str(determination["_id"])}
            )
            if new_items:
                await self.db.key_date_eot_determination_items.insert_many(new_items)
        await self._emit(
            "keydate.eot_determination.updated", current_user, updated, source=source,
            before={"status": determination.get("status")}, after={"status": updated.get("status")},
        )
        return await self.get_determination(str(determination["_id"]))

    async def freeze_determination(
        self, determination: Dict[str, Any], current_user: Any, *, source: str = "API"
    ) -> Dict[str, Any]:
        if determination.get("frozen_at"):
            return await self.get_determination(str(determination["_id"]))
        status = _enum_value(determination.get("status"))
        if status not in FINAL_DETERMINATION_STATUSES:
            raise KeyDateError("Set a final determination status before freezing")
        if not _clean_ref(determination.get("determination_reference")):
            raise KeyDateError("Determination Reference is required before freezing")
        if not _as_dt(determination.get("determination_date")):
            raise KeyDateError("Determination Date is required before freezing")
        if status in {
            EOTDeterminationStatus.GRANTED.value,
            EOTDeterminationStatus.PARTIALLY_GRANTED.value,
        } and not _clean_ref(determination.get("approval_grant_reference")):
            raise KeyDateError("Approval / Grant Reference is required for a granted determination")

        full = await self.get_determination(str(determination["_id"]))
        items = (full or {}).get("items") or []
        if not items:
            raise KeyDateError("Add at least one milestone determination before freezing")
        for item in items:
            result = _enum_value(item.get("determination_result"))
            if result in EFFECTIVE_RESULTS and not _as_dt(item.get("eot_granted_date")):
                raise KeyDateError(
                    f"Granted Date is required for {item.get('milestone_ref')} when result is {result}"
                )

        scope = self.scope(
            determination.get("organization_id"),
            determination.get("project_id"),
            determination.get("contract_id", "primary"),
        )
        baseline = await self.db.key_date_baselines.find_one(
            {**scope, "status": BaselineStatus.FROZEN.value}
        )
        baseline_ids = {str(row.get("key_date_id")) for row in (baseline or {}).get("items") or []}
        for item in items:
            if str(item.get("key_date_id")) not in baseline_ids:
                raise KeyDateError(
                    f"Milestone {item.get('milestone_ref')} is not part of the frozen Original baseline"
                )

        now = datetime.utcnow()
        updated = await self.db.key_date_eot_determinations.find_one_and_update(
            {"_id": determination["_id"], "frozen_at": None},
            {"$set": {"frozen_at": now, "frozen_by": getattr(current_user, "id", None)}},
            return_document=ReturnDocument.AFTER,
        )
        if not updated:
            raced = await self.db.key_date_eot_determinations.find_one({"_id": determination["_id"]})
            if not (raced and raced.get("frozen_at")):
                raise KeyDateError("The determination could not be frozen")
            # Another writer won the freeze. Recomputing is idempotent, so
            # converging again here is harmless and self-healing.
            await self.recompute_effective_dates(
                scope.get("organization_id"), scope["project_id"], scope["contract_id"],
                current_user=current_user,
            )
            return await self.get_determination(str(determination["_id"]))

        # Never write a grant date straight onto the milestone: a determination
        # frozen out of revision order would otherwise pull the contractual date
        # backwards. Re-derive the whole picture from the frozen record instead.
        await self.recompute_effective_dates(
            scope.get("organization_id"), scope["project_id"], scope["contract_id"],
            current_user=current_user,
        )
        await self._emit(
            "keydate.eot_determination.frozen", current_user, updated, source=source,
            after={"status": status, "covers": updated.get("covered_revision_labels"), "frozen_at": str(now)},
        )
        return await self.get_determination(str(determination["_id"]))

    @staticmethod
    def _replay_determinations(
        determinations: Sequence[Dict[str, Any]],
        effective: Dict[str, Optional[datetime]],
        governing: Dict[str, Optional[Dict[str, Any]]],
    ) -> None:
        """Apply every frozen, non-superseded determination in contractual order.

        Shared by the date recomputation and the summary label so the header can
        never name a determination that governs no milestone.
        """
        superseded_ids = {
            str(value)
            for row in determinations
            if row.get("frozen_at")
            for value in row.get("supersedes_determination_ids") or []
        }
        applicable = [
            row for row in determinations
            if row.get("frozen_at")
            and _enum_value(row.get("status")) in FINAL_DETERMINATION_STATUSES
            and str(row.get("_id")) not in superseded_ids
        ]
        applicable.sort(key=lambda row: (
            _as_dt(row.get("determination_date")) or _as_dt(row.get("frozen_at")) or datetime.min,
            _as_dt(row.get("frozen_at")) or datetime.min,
            str(row.get("_id")),
        ))
        for determination in applicable:
            for item in determination.get("items") or []:
                if _enum_value(item.get("determination_result")) not in EFFECTIVE_RESULTS:
                    continue  # rejected / no-change / pending carry the date forward
                granted = _as_dt(item.get("eot_granted_date"))
                if not granted:
                    continue
                key_date_id = str(item.get("key_date_id"))
                if key_date_id not in effective:
                    raise KeyDateError(
                        f"Determination {determination.get('determination_reference') or determination.get('_id')} "
                        f"grants milestone {item.get('milestone_ref')}, which is absent from the frozen baseline"
                    )
                effective[key_date_id] = granted
                governing[key_date_id] = determination

    async def recompute_effective_dates(
        self,
        organization_id: Optional[str],
        project_id: str,
        contract_id: str = "primary",
        *,
        current_user: Any = None,
    ) -> List[Dict[str, Any]]:
        """Re-derive every milestone's in-force contractual date from the frozen record.

        Seeded from the frozen Original baseline snapshot and replayed over every
        frozen, non-superseded determination in contractual order (the Employer's
        ``determination_date``, not the order rows happened to be frozen in), so
        determining EOT-1 after EOT-2 cannot regress a milestone. Writes only where
        the value actually changes and returns the changes it made, which makes it
        both idempotent and safe to expose as a repair action.
        """
        scope = self.scope(organization_id, project_id, contract_id)
        baseline = await self.db.key_date_baselines.find_one(
            {**scope, "status": BaselineStatus.FROZEN.value}
        )
        if not baseline:
            return []

        effective: Dict[str, Optional[datetime]] = {}
        governing: Dict[str, Optional[Dict[str, Any]]] = {}
        for row in baseline.get("items") or []:
            key_date_id = str(row.get("key_date_id"))
            effective[key_date_id] = _as_dt(row.get("original_contractual_date"))
            governing[key_date_id] = None

        determinations = await self.list_determinations(organization_id, project_id, contract_id)
        self._replay_determinations(determinations, effective, governing)

        now = datetime.utcnow()
        changes: List[Dict[str, Any]] = []
        for key_date_id, target in effective.items():
            milestone = await self.db.key_date_milestones.find_one({"_id": key_date_id})
            if not milestone:
                continue
            previous = _as_dt(milestone.get("current_approved_key_date"))
            if previous == target:
                continue
            determination = governing[key_date_id]
            await self.db.key_date_milestones.update_one(
                {"_id": key_date_id},
                {"$set": {
                    "current_approved_key_date": target,
                    "current_determination_id": str(determination["_id"]) if determination else None,
                    "current_determination_frozen_at": (
                        _as_dt(determination.get("frozen_at")) if determination else None
                    ),
                    "eot_status": "approved" if determination else None,
                    "updated_at": now,
                    "updated_by": getattr(current_user, "id", None),
                }},
            )
            changes.append({
                "key_date_id": key_date_id,
                "milestone_ref": milestone.get("milestone_ref"),
                "previous_contractual_date": previous,
                "effective_contractual_date": target,
                "determination_id": str(determination["_id"]) if determination else None,
            })
        return changes

    async def workflow_summary(
        self, organization_id: Optional[str], project_id: str, contract_id: str = "primary"
    ) -> Dict[str, Any]:
        baseline = await self.baseline(organization_id, project_id, contract_id)
        submissions = await self.list_submissions(organization_id, project_id, contract_id)
        determinations = await self.list_determinations(organization_id, project_id, contract_id)
        covered_by_frozen = {
            str(submission_id)
            for determination in determinations
            if determination.get("frozen_at") and determination.get("status") in FINAL_DETERMINATION_STATUSES
            for submission_id in determination.get("eot_submission_ids") or []
        }
        outcomes = self.submission_outcomes(submissions, determinations)
        for row in submissions:
            row.update(outcomes.get(str(row.get("_id")), {}))
        pending = [
            row for row in submissions
            if row.get("status") in OPEN_SUBMISSION_STATUSES and str(row.get("_id")) not in covered_by_frozen
        ]
        # Name only a determination that actually governs a milestone's in-force
        # date. One frozen last but superseded by contractual ordering governs
        # nothing and must not be announced as the current baseline.
        seed_dates: Dict[str, Optional[datetime]] = {}
        governing: Dict[str, Optional[Dict[str, Any]]] = {}
        for row in (baseline or {}).get("items") or []:
            key_date_id = str(row.get("key_date_id"))
            seed_dates[key_date_id] = _as_dt(row.get("original_contractual_date"))
            governing[key_date_id] = None
        self._replay_determinations(determinations, seed_dates, governing)
        in_force = [row for row in governing.values() if row]
        in_force.sort(key=lambda row: (
            _as_dt(row.get("determination_date")) or _as_dt(row.get("frozen_at")) or datetime.min,
            _as_dt(row.get("frozen_at")) or datetime.min,
            str(row.get("_id")),
        ))
        current_baseline = "Original"
        if in_force:
            labels = in_force[-1].get("covered_revision_labels") or []
            if labels:
                current_baseline = " + ".join(labels)
            elif _enum_value(in_force[-1].get("origin")) == EOTDeterminationOrigin.EMPLOYER_INITIATED.value:
                current_baseline = "Employer Determination"
            else:
                current_baseline = "Frozen determination"
        elif baseline and baseline.get("status") == BaselineStatus.FROZEN.value:
            milestone_query: Dict[str, Any] = {"project_id": str(project_id)}
            if organization_id:
                milestone_query["organization_id"] = str(organization_id)
            milestone_rows = await _cursor_list(self.db.key_date_milestones.find(milestone_query))
            milestone_rows = [
                row for row in milestone_rows
                if str(row.get("contract_id") or "primary") == str(contract_id or "primary")
            ]
            if any(
                _as_dt(row.get("current_approved_key_date"))
                and _as_dt(row.get("current_approved_key_date")) != _as_dt(row.get("original_planned_key_date"))
                for row in milestone_rows
            ):
                current_baseline = "Legacy Approved EOT"
        return {
            "project_id": str(project_id),
            "contract_id": str(contract_id or "primary"),
            "baseline_status": (baseline or {}).get("status", BaselineStatus.DRAFT.value),
            "baseline_frozen_at": (baseline or {}).get("frozen_at"),
            "baseline_frozen_by": (baseline or {}).get("frozen_by"),
            "current_contractual_baseline": current_baseline,
            "latest_eot_submission": submissions[-1].get("revision_label") if submissions else None,
            "pending_determinations": len(pending),
            "open_eot_submissions": len(pending),
            "employer_initiated_determinations": sum(
                1 for row in determinations
                if _enum_value(row.get("origin")) == EOTDeterminationOrigin.EMPLOYER_INITIATED.value
            ),
            "not_separately_determined": sum(
                1 for row in submissions
                if row.get("determination_outcome")
                == SubmissionOutcome.NOT_SEPARATELY_DETERMINED.value
            ),
            "oldest_pending_submission": pending[0].get("revision_label") if pending else None,
            "submissions": submissions,
            "determinations": determinations,
        }

    async def submission_csv_preview(
        self, submission: Dict[str, Any], content: bytes
    ) -> Tuple[CSVImportPreview, List[EOTSubmissionItemInput]]:
        if submission.get("locked_at"):
            raise KeyDateError("Locked EOT submissions cannot be changed through CSV")
        return await self._csv_preview(
            content,
            submission=submission,
            determination=None,
        )

    async def determination_csv_preview(
        self, determination: Dict[str, Any], content: bytes
    ) -> Tuple[CSVImportPreview, List[Any]]:
        if determination.get("frozen_at"):
            raise KeyDateError("Frozen determinations cannot be changed through CSV")
        return await self._csv_preview(
            content,
            submission=None,
            determination=determination,
        )

    async def _csv_preview(
        self,
        content: bytes,
        *,
        submission: Optional[Dict[str, Any]],
        determination: Optional[Dict[str, Any]],
    ) -> Tuple[CSVImportPreview, List[Any]]:
        from ..models.key_date import EOTDeterminationItemInput

        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise KeyDateError("CSV must use UTF-8 encoding") from exc
        reader = csv.DictReader(io.StringIO(text))
        headers = [str(value or "").strip() for value in (reader.fieldnames or [])]
        if "milestone_ref" not in headers:
            raise KeyDateError("CSV requires a milestone_ref column")
        protected = {
            "project_id", "organization_id", "contract_id", "revision_number",
            "original_planned_key_date", "previous_eot_submitted_date", "previous_eot_granted_date",
            "origin", "determination_id", "source_submission_id", "superseded_by_submission_id",
            "linked_document_ids", "linked_letter_ids",
        }
        blocked = sorted(set(headers) & protected)
        if blocked:
            raise KeyDateError("Protected historical/scope columns are not importable: " + ", ".join(blocked))

        milestone_map = await self._milestones_by_ref(
            (submission or determination or {}).get("organization_id"),
            (submission or determination or {}).get("project_id"),
            (submission or determination or {}).get("contract_id", "primary"),
        )
        submitted_by_ref: Dict[str, Dict[str, Any]] = {}
        submission_id_by_label: Dict[str, str] = {}
        covered_required = bool((determination or {}).get("eot_submission_ids"))
        if determination:
            for submission_id in determination.get("eot_submission_ids") or []:
                full = await self.get_submission(str(submission_id))
                if full:
                    submission_id_by_label[
                        _clean_ref(full.get("revision_label")).casefold()
                    ] = str(full.get("_id"))
                for item in (full or {}).get("items", []):
                    submitted_by_ref[_clean_ref(item.get("milestone_ref")).casefold()] = item

        parsed: List[Any] = []
        preview_rows: List[CSVImportRow] = []
        seen: set[str] = set()
        specific_submission_header = None
        if submission:
            specific_submission_header = f"eot_{submission.get('revision_number')}_submitted_date"
        for row_number, raw in enumerate(reader, start=2):
            errors: List[str] = []
            warnings: List[str] = []
            ref = _clean_ref(raw.get("milestone_ref"))
            key = ref.casefold()
            milestone = milestone_map.get(key)
            if not ref:
                errors.append("milestone_ref is required")
            elif key in seen:
                errors.append("duplicate milestone_ref in CSV")
            elif not milestone:
                errors.append("milestone_ref does not belong to the selected project")
            seen.add(key)
            data: Dict[str, Any] = {"milestone_ref": ref}
            try:
                if submission:
                    raw_date = raw.get("eot_submitted_date") or raw.get(specific_submission_header or "")
                    submitted_date = _as_dt(raw_date)
                    if not submitted_date:
                        errors.append("eot_submitted_date is required and must be ISO format")
                    days_raw = _clean_ref(raw.get("claimed_extension_days"))
                    claimed_days = int(days_raw) if days_raw else None
                    if claimed_days is not None and claimed_days < 0:
                        errors.append("claimed_extension_days cannot be negative")
                    if submitted_date:
                        parsed.append(EOTSubmissionItemInput(
                            milestone_ref=ref,
                            eot_submitted_date=submitted_date,
                            claimed_extension_days=claimed_days,
                            remarks=_clean_ref(raw.get("remarks")) or None,
                        ))
                    data.update({
                        "description": (milestone or {}).get("description") or (milestone or {}).get("title"),
                        "current_contractual_date": _iso(current_key_date(milestone or {})),
                        "eot_submitted_date": _iso(submitted_date),
                        "claimed_extension_days": claimed_days,
                        "remarks": _clean_ref(raw.get("remarks")) or None,
                    })
                else:
                    submitted_item = submitted_by_ref.get(key)
                    # Employer-initiated determinations cover no submission, so the
                    # milestone is validated against the project register instead.
                    if ref and covered_required and not submitted_item:
                        errors.append("milestone_ref is not in a covered EOT submission")
                    result = _clean_ref(raw.get("determination_result") or "pending").lower()
                    if result not in {value.value for value in EOTDeterminationResult}:
                        errors.append("invalid determination_result")
                    granted_date = _as_dt(raw.get("eot_granted_date"))
                    if result in EFFECTIVE_RESULTS and not granted_date:
                        errors.append("eot_granted_date is required for granted results")
                    days_raw = _clean_ref(raw.get("granted_extension_days"))
                    granted_days = int(days_raw) if days_raw else None
                    if granted_days is not None and granted_days < 0:
                        errors.append("granted_extension_days cannot be negative")
                    source_label = _clean_ref(raw.get("source_submission_ref"))
                    source_id: Optional[str] = None
                    if source_label:
                        source_id = submission_id_by_label.get(source_label.casefold())
                        if not source_id:
                            errors.append(
                                "source_submission_ref is not one of this determination's covered submissions"
                            )
                    if not errors:
                        parsed.append(EOTDeterminationItemInput(
                            milestone_ref=ref,
                            eot_granted_date=granted_date,
                            granted_extension_days=granted_days,
                            determination_result=result,
                            source_submission_id=source_id,
                            remarks=_clean_ref(raw.get("remarks")) or None,
                        ))
                    data.update({
                        "description": (milestone or {}).get("description") or (milestone or {}).get("title"),
                        "source_submission_ref": source_label or None,
                        "submitted_date": _iso((submitted_item or {}).get("eot_submitted_date")),
                        "current_contractual_date": _iso(current_key_date(milestone or {})),
                        "eot_granted_date": _iso(granted_date),
                        "granted_extension_days": granted_days,
                        "determination_result": result,
                        "remarks": _clean_ref(raw.get("remarks")) or None,
                    })
            except (TypeError, ValueError):
                errors.append("extension days must be a whole number")
            preview_rows.append(CSVImportRow(
                row_number=row_number,
                data=data,
                errors=errors,
                warnings=warnings,
                duplicate=any("duplicate" in error for error in errors),
            ))

        invalid = sum(1 for row in preview_rows if row.errors)
        module = "key_date_eot_submission" if submission else "key_date_eot_determination"
        required = ["milestone_ref", "eot_submitted_date"] if submission else ["milestone_ref", "determination_result"]
        template = SUBMISSION_TEMPLATE_HEADERS if submission else DETERMINATION_TEMPLATE_HEADERS
        preview = CSVImportPreview(
            module=module,
            total_rows=len(preview_rows),
            valid_rows=len(preview_rows) - invalid,
            invalid_rows=invalid,
            can_import=bool(preview_rows) and invalid == 0,
            rows=preview_rows,
            required_headers=required,
            template_headers=template,
        )
        if invalid:
            parsed = []
        return preview, parsed

    async def import_submission_csv(
        self, submission: Dict[str, Any], content: bytes, current_user: Any
    ) -> CSVImportResult:
        preview, items = await self.submission_csv_preview(submission, content)
        if not preview.can_import:
            return CSVImportResult(**preview.model_dump(), imported_count=0, created_ids=[])
        updated = await self.update_submission(
            submission, EOTSubmissionUpdate(items=items), current_user, source="CSV"
        )
        return CSVImportResult(
            **preview.model_dump(), imported_count=len(items),
            created_ids=[str(item.get("_id")) for item in updated.get("items") or []],
        )

    async def import_determination_csv(
        self, determination: Dict[str, Any], content: bytes, current_user: Any
    ) -> CSVImportResult:
        preview, items = await self.determination_csv_preview(determination, content)
        if not preview.can_import:
            return CSVImportResult(**preview.model_dump(), imported_count=0, created_ids=[])
        updated = await self.update_determination(
            determination, EOTDeterminationUpdate(items=items), current_user, source="CSV"
        )
        return CSVImportResult(
            **preview.model_dump(), imported_count=len(items),
            created_ids=[str(item.get("_id")) for item in updated.get("items") or []],
        )

    async def _emit(
        self,
        action: str,
        current_user: Any,
        resource: Dict[str, Any],
        *,
        source: str,
        before: Optional[Dict[str, Any]] = None,
        after: Optional[Dict[str, Any]] = None,
    ) -> None:
        await self.audit.emit(
            action=action,
            actor_id=getattr(current_user, "id", None),
            resource_type="key_date_revision",
            resource_id=str(resource.get("_id")),
            organization_id=resource.get("organization_id"),
            project_id=resource.get("project_id"),
            before=before,
            after=after,
            metadata={
                "contract_id": resource.get("contract_id", "primary"),
                "revision": resource.get("revision_number"),
                "source": source,
            },
        )
