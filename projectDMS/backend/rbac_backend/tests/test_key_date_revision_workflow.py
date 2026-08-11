from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest

from rbac_backend.models.key_date import (
    EOTDeterminationCreate,
    EOTDeterminationItemInput,
    EOTSubmissionCreate,
    EOTSubmissionItemInput,
    EOTSubmissionUpdate,
)
from rbac_backend.core.permissions import CLIENT_DMS_PERMISSIONS, Permissions
from rbac_backend.initial_data.default_permissions import DEFAULT_PERMISSIONS
from rbac_backend.services.key_date_revision_export import history_table
from rbac_backend.services.key_date_revision_service import KeyDateRevisionService
from rbac_backend.services.key_date_service import KeyDateError, KeyDateService


def _matches(doc, query):
    for key, expected in (query or {}).items():
        actual = doc.get(key)
        if isinstance(expected, dict):
            if "$in" in expected:
                values = expected["$in"]
                if isinstance(actual, list):
                    if not any(value in values for value in actual):
                        return False
                elif actual not in values:
                    return False
            elif "$ne" in expected:
                if actual == expected["$ne"]:
                    return False
            else:
                return False
        elif actual != expected:
            return False
    return True


class _Cursor:
    def __init__(self, rows):
        self.rows = [deepcopy(row) for row in rows]

    def sort(self, key, direction=1):
        self.rows.sort(key=lambda row: (row.get(key) is None, row.get(key)), reverse=direction < 0)
        return self

    def skip(self, count):
        self.rows = self.rows[count:]
        return self

    def limit(self, count):
        self.rows = self.rows[:count]
        return self

    async def to_list(self, length=None):
        return deepcopy(self.rows if length is None else self.rows[:length])

    def __aiter__(self):
        self._index = 0
        return self

    async def __anext__(self):
        if self._index >= len(self.rows):
            raise StopAsyncIteration
        row = deepcopy(self.rows[self._index])
        self._index += 1
        return row


class _Collection:
    def __init__(self):
        self.rows = []

    async def insert_one(self, doc):
        self.rows.append(deepcopy(doc))
        return SimpleNamespace(inserted_id=doc.get("_id"))

    async def insert_many(self, docs):
        self.rows.extend(deepcopy(list(docs)))
        return SimpleNamespace(inserted_ids=[doc.get("_id") for doc in docs])

    async def find_one(self, query):
        return next((deepcopy(row) for row in self.rows if _matches(row, query)), None)

    def find(self, query):
        return _Cursor([row for row in self.rows if _matches(row, query)])

    async def find_one_and_update(self, query, update, return_document=True):
        for row in self.rows:
            if not _matches(row, query):
                continue
            for key, value in update.get("$set", {}).items():
                row[key] = deepcopy(value)
            for key, value in update.get("$inc", {}).items():
                row[key] = int(row.get(key) or 0) + int(value)
            return deepcopy(row)
        return None

    async def update_one(self, query, update, upsert=False):
        updated = await self.find_one_and_update(query, update)
        if updated:
            return SimpleNamespace(modified_count=1)
        if upsert:
            doc = {key: value for key, value in query.items() if not isinstance(value, dict)}
            doc.update(update.get("$setOnInsert", {}))
            doc.update(update.get("$set", {}))
            await self.insert_one(doc)
            return SimpleNamespace(modified_count=0, upserted_id=doc.get("_id"))
        return SimpleNamespace(modified_count=0)

    async def delete_one(self, query):
        for index, row in enumerate(self.rows):
            if _matches(row, query):
                self.rows.pop(index)
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    async def delete_many(self, query):
        before = len(self.rows)
        self.rows = [row for row in self.rows if not _matches(row, query)]
        return SimpleNamespace(deleted_count=before - len(self.rows))


class _DB:
    def __init__(self):
        self._collections = {}

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self._collections.setdefault(name, _Collection())


def _user():
    return SimpleNamespace(id="user-1", organization_id="org-A")


async def _seed(db):
    originals = {
        "KD-01": datetime(2026, 1, 1),
        "KD-02": datetime(2026, 1, 15),
    }
    for index, (ref, date) in enumerate(originals.items(), start=1):
        await db.key_date_milestones.insert_one({
            "_id": f"m-{index}", "organization_id": "org-A", "project_id": "proj-A",
            "milestone_ref": ref, "title": ref, "description": f"Milestone {ref}",
            "contractual_week_number": index, "original_planned_key_date": date,
            "current_approved_key_date": date, "current_revision": 0,
        })
    return originals


@pytest.mark.asyncio
async def test_eot2_while_eot1_pending_then_later_partial_grant_preserves_snapshots():
    db = _DB()
    originals = await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())

    async def submission(ref, first, second):
        created = await svc.create_submission(EOTSubmissionCreate(
            organization_id="org-A", project_id="proj-A", contract_id="primary",
            contractor_submission_date=datetime(2026, 2, 1),
            contractor_letter_reference=ref, status="submitted",
            items=[
                EOTSubmissionItemInput(milestone_ref="KD-01", eot_submitted_date=first, claimed_extension_days=60),
                EOTSubmissionItemInput(milestone_ref="KD-02", eot_submitted_date=second, claimed_extension_days=60),
            ],
        ), _user())
        return await svc.lock_submission(created, _user())

    eot1 = await submission("CON/EOT-1", datetime(2026, 3, 1), datetime(2026, 3, 15))
    eot2 = await submission("CON/EOT-2", datetime(2026, 5, 1), datetime(2026, 5, 15))

    assert (eot1["revision_number"], eot2["revision_number"]) == (1, 2)
    assert eot1["status"] == eot2["status"] == "locked"
    summary = await svc.workflow_summary("org-A", "proj-A")
    assert summary["current_contractual_baseline"] == "Original"
    assert summary["pending_determinations"] == 2
    assert summary["oldest_pending_submission"] == "EOT-1"
    assert {item["contractual_date_at_submission"] for item in eot2["items"]} == set(originals.values())

    determination = await svc.create_determination(EOTDeterminationCreate(
        organization_id="org-A", project_id="proj-A", contract_id="primary",
        eot_submission_ids=[eot1["_id"]], determination_reference="CLIENT/DET-1",
        determination_date=datetime(2026, 4, 1), approval_grant_reference="CLIENT/GRANT-1",
        status="partially_granted",
        items=[
            EOTDeterminationItemInput(
                milestone_ref="KD-01", eot_granted_date=datetime(2026, 2, 15),
                granted_extension_days=45, determination_result="partially_granted",
            ),
            EOTDeterminationItemInput(
                milestone_ref="KD-02", granted_extension_days=0, determination_result="rejected",
            ),
        ],
    ), _user())
    await svc.freeze_determination(determination, _user())

    kd1 = await db.key_date_milestones.find_one({"_id": "m-1"})
    kd2 = await db.key_date_milestones.find_one({"_id": "m-2"})
    assert kd1["current_approved_key_date"] == datetime(2026, 2, 15)
    assert kd2["current_approved_key_date"] == originals["KD-02"]
    # EOT-2 remains exactly as submitted against the Original position.
    eot2_after = await svc.get_submission(eot2["_id"])
    assert {item["contractual_date_at_submission"] for item in eot2_after["items"]} == set(originals.values())
    summary = await svc.workflow_summary("org-A", "proj-A")
    assert summary["current_contractual_baseline"] == "EOT-1"
    assert summary["pending_determinations"] == 1
    assert summary["latest_eot_submission"] == "EOT-2"


@pytest.mark.asyncio
async def test_frozen_baseline_and_locked_submission_are_immutable_and_csv_protects_scope():
    db = _DB()
    await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())
    with pytest.raises(KeyDateError, match="frozen"):
        await KeyDateService(db).update(
            await db.key_date_milestones.find_one({"_id": "m-1"}),
            {"title": "Rewritten"}, _user(),
        )

    submission = await svc.create_submission(EOTSubmissionCreate(
        organization_id="org-A", project_id="proj-A", contractor_submission_date=datetime(2026, 2, 1),
        contractor_letter_reference="CON/EOT-1", status="submitted",
        items=[EOTSubmissionItemInput(
            milestone_ref="KD-01", eot_submitted_date=datetime(2026, 3, 1), claimed_extension_days=60,
        )],
    ), _user())
    locked = await svc.lock_submission(submission, _user())
    with pytest.raises(KeyDateError, match="immutable"):
        await svc.update_submission(
            locked, payload=EOTSubmissionUpdate(remarks="rewrite"), current_user=_user()
        )
    with pytest.raises(KeyDateError, match="Protected"):
        await svc.submission_csv_preview(
            submission,
            b"milestone_ref,project_id,eot_submitted_date\nKD-01,other,2026-04-01\n",
        )


@pytest.mark.asyncio
async def test_atomic_counter_allocates_unbounded_unique_revisions():
    db = _DB()
    await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())

    async def create(index):
        return await svc.create_submission(EOTSubmissionCreate(
            organization_id="org-A", project_id="proj-A", status="draft",
            eot_reference=f"EOT/{index}", items=[],
        ), _user())

    submissions = await asyncio.gather(*(create(index) for index in range(1, 13)))
    assert sorted(row["revision_number"] for row in submissions) == list(range(1, 13))
    assert len({row["revision_label"] for row in submissions}) == 12


def test_complete_history_export_has_dynamic_submitted_granted_status_columns():
    milestones = [{
        "milestone_ref": "KD-01", "title": "Milestone", "original_planned_key_date": datetime(2026, 1, 1),
        "current_approved_key_date": datetime(2026, 2, 15),
    }]
    submissions = [
        {"_id": "e1", "revision_number": 1, "revision_label": "EOT-1", "items": [{"milestone_ref": "KD-01", "eot_submitted_date": datetime(2026, 3, 1)}]},
        {"_id": "e2", "revision_number": 2, "revision_label": "EOT-2", "items": [{"milestone_ref": "KD-01", "eot_submitted_date": datetime(2026, 5, 1)}]},
    ]
    determinations = [{
        "eot_submission_ids": ["e1"], "status": "partially_granted", "frozen_at": datetime(2026, 4, 1),
        "items": [{"milestone_ref": "KD-01", "eot_granted_date": datetime(2026, 2, 15), "determination_result": "partially_granted"}],
    }]
    headers, rows = history_table(milestones, submissions, determinations)
    assert headers == [
        "Ref", "Description", "Original Date",
        "EOT-1 Submitted", "EOT-1 Granted", "EOT-1 Status",
        "EOT-2 Submitted", "EOT-2 Granted", "EOT-2 Status",
        "Current Contractual Date", "Actual Achievement Date",
    ]
    assert rows[0][3] == datetime(2026, 3, 1)
    assert rows[0][4] == datetime(2026, 2, 15)
    assert rows[0][8] == "pending"


def test_revision_workflow_sensitive_actions_have_backend_permission_catalog_entries():
    expected = {
        Permissions.KEYDATE_BASELINE_FREEZE,
        Permissions.KEYDATE_EOT_LOCK_SUBMISSION,
        Permissions.KEYDATE_EOT_DETERMINE,
        Permissions.KEYDATE_EOT_FREEZE_DETERMINATION,
    }
    assert expected <= set(CLIENT_DMS_PERMISSIONS)
    assert expected <= {row["_id"] for row in DEFAULT_PERMISSIONS}
