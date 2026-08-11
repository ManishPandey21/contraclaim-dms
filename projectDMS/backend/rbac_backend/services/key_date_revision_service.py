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

from pymongo import ReturnDocument

from ..models.csv_import import CSVImportPreview, CSVImportResult, CSVImportRow
from ..models.key_date import (
    BaselineStatus,
    EOTDeterminationCreate,
    EOTDeterminationResult,
    EOTDeterminationStatus,
    EOTDeterminationUpdate,
    EOTSubmissionCreate,
    EOTSubmissionItemInput,
    EOTSubmissionStatus,
    EOTSubmissionUpdate,
)
from .audit_event_service import AuditEventService
from .key_date_service import KeyDateError, _as_dt, current_key_date


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
    parsed = _as_dt(value)
    return parsed.date().isoformat() if parsed else ""


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

    async def assert_baseline_editable(
        self, organization_id: Optional[str], project_id: str, contract_id: str = "primary"
    ) -> None:
        baseline = await self.baseline(organization_id, project_id, contract_id)
        if baseline and baseline.get("status") == BaselineStatus.FROZEN.value:
            raise KeyDateError(
                "Original Key Dates are frozen. Record future changes through an EOT submission."
            )

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

    async def _determination_item_docs(
        self,
        determination_id: str,
        submissions: Sequence[Dict[str, Any]],
        items: Sequence[Any],
    ) -> List[Dict[str, Any]]:
        covered_by_ref: Dict[str, Tuple[Dict[str, Any], Dict[str, Any]]] = {}
        for submission in sorted(submissions, key=lambda row: int(row.get("revision_number") or 0)):
            full = await self.get_submission(str(submission["_id"]))
            for item in (full or {}).get("items", []):
                covered_by_ref[_clean_ref(item.get("milestone_ref")).casefold()] = (submission, item)

        docs: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            ref = _clean_ref(item.milestone_ref)
            key = ref.casefold()
            if key in seen:
                raise KeyDateError(f"Duplicate determination item for Milestone Ref '{ref}'")
            seen.add(key)
            covered = covered_by_ref.get(key)
            if not covered:
                raise KeyDateError(
                    f"Milestone Ref '{ref}' is not included in any covered EOT submission"
                )
            _submission, submitted_item = covered
            milestone = await self.db.key_date_milestones.find_one(
                {"_id": str(submitted_item.get("key_date_id"))}
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
                "submitted_date": submitted_item.get("eot_submitted_date"),
                "claimed_extension_days": submitted_item.get("claimed_extension_days"),
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
        submissions: List[Dict[str, Any]] = []
        for submission_id in unique_ids:
            submission = await self.db.key_date_eot_submissions.find_one({"_id": submission_id, **scope})
            if not submission:
                raise KeyDateError("Every covered EOT submission must belong to the selected project/contract")
            if submission.get("status") != EOTSubmissionStatus.LOCKED.value:
                raise KeyDateError(f"{submission.get('revision_label')} must be locked before determination")
            submissions.append(submission)
        if not submissions:
            raise KeyDateError("Select at least one EOT submission for determination")

        for superseded_id in payload.supersedes_determination_ids:
            linked = await self.db.key_date_eot_determinations.find_one({"_id": str(superseded_id), **scope})
            if not linked:
                raise KeyDateError("A superseded determination is outside the selected project/contract")

        determination_id = _id()
        item_docs = await self._determination_item_docs(determination_id, submissions, payload.items)
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
            "covered_revision_labels": [row.get("revision_label") for row in submissions],
            "determination_reference": payload.determination_reference,
            "determination_date": payload.determination_date,
            "approval_grant_reference": payload.approval_grant_reference,
            "approved_by": payload.approved_by,
            "status": status,
            "remarks": payload.remarks,
            "supersedes_determination_ids": [str(value) for value in payload.supersedes_determination_ids],
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
                str(determination["_id"]), submissions, payload.items
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

        now = datetime.utcnow()
        # Apply only effective milestone-level grants. Rejected/no-change/pending
        # items deliberately carry forward the existing contractual date.
        for item in items:
            result = _enum_value(item.get("determination_result"))
            grant_date = _as_dt(item.get("eot_granted_date"))
            if result not in EFFECTIVE_RESULTS or not grant_date:
                continue
            await self.db.key_date_milestones.update_one(
                {"_id": str(item.get("key_date_id"))},
                {"$set": {
                    "current_approved_key_date": grant_date,
                    "current_determination_id": str(determination["_id"]),
                    "current_determination_frozen_at": now,
                    "eot_status": "approved",
                    "updated_at": now,
                    "updated_by": getattr(current_user, "id", None),
                }},
            )

        updated = await self.db.key_date_eot_determinations.find_one_and_update(
            {"_id": determination["_id"], "frozen_at": None},
            {"$set": {"frozen_at": now, "frozen_by": getattr(current_user, "id", None)}},
            return_document=ReturnDocument.AFTER,
        )
        if not updated:
            raced = await self.db.key_date_eot_determinations.find_one({"_id": determination["_id"]})
            if raced and raced.get("frozen_at"):
                return await self.get_determination(str(determination["_id"]))
            raise KeyDateError("The determination could not be frozen")
        await self._emit(
            "keydate.eot_determination.frozen", current_user, updated, source=source,
            after={"status": status, "covers": updated.get("covered_revision_labels"), "frozen_at": str(now)},
        )
        return await self.get_determination(str(determination["_id"]))

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
        pending = [
            row for row in submissions
            if row.get("status") in OPEN_SUBMISSION_STATUSES and str(row.get("_id")) not in covered_by_frozen
        ]
        effective = [
            row for row in determinations
            if row.get("frozen_at")
            and any(
                item.get("determination_result") in EFFECTIVE_RESULTS and item.get("eot_granted_date")
                for item in row.get("items") or []
            )
        ]
        effective.sort(key=lambda row: row.get("frozen_at") or datetime.min)
        current_baseline = "Original"
        if effective:
            labels = effective[-1].get("covered_revision_labels") or []
            current_baseline = " + ".join(labels) if labels else "Frozen determination"
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
        if determination:
            for submission_id in determination.get("eot_submission_ids") or []:
                full = await self.get_submission(str(submission_id))
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
                    if ref and not submitted_item:
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
                    if not errors:
                        parsed.append(EOTDeterminationItemInput(
                            milestone_ref=ref,
                            eot_granted_date=granted_date,
                            granted_extension_days=granted_days,
                            determination_result=result,
                            remarks=_clean_ref(raw.get("remarks")) or None,
                        ))
                    data.update({
                        "description": (milestone or {}).get("description") or (milestone or {}).get("title"),
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
