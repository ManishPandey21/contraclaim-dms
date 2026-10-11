from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace

import pytest
from bson import ObjectId

from rbac_backend.models.key_date import (
    EOTDeterminationCreate,
    EOTDeterminationItemInput,
    EOTSubmissionCreate,
    EOTSubmissionItemInput,
    EOTSubmissionUpdate,
)
from rbac_backend.core.permissions import CLIENT_DMS_PERMISSIONS, Permissions
from rbac_backend.initial_data.default_permissions import DEFAULT_PERMISSIONS
from rbac_backend.services.key_date_revision_export import determination_table, history_table
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


async def _locked_submission(
    svc, letter_reference, submission_date, submitted_date, claimed_days, milestone_ref="KD-01",
):
    submission = await svc.create_submission(EOTSubmissionCreate(
        organization_id="org-A", project_id="proj-A",
        contractor_submission_date=submission_date,
        contractor_letter_reference=letter_reference,
        status="submitted",
        items=[EOTSubmissionItemInput(
            milestone_ref=milestone_ref, eot_submitted_date=submitted_date,
            claimed_extension_days=claimed_days,
        )],
    ), _user())
    return await svc.lock_submission(submission, _user())


async def _frozen_determination(
    svc, submission_ids, reference, determination_date, granted_date, *,
    supersedes=None, milestone_ref="KD-01",
):
    determination = await svc.create_determination(EOTDeterminationCreate(
        organization_id="org-A", project_id="proj-A",
        eot_submission_ids=submission_ids,
        determination_reference=reference,
        determination_date=determination_date,
        approval_grant_reference=f"{reference}/GRANT",
        status="partially_granted",
        supersedes_determination_ids=list(supersedes or []),
        remarks="Correction of an earlier determination" if supersedes else None,
        items=[EOTDeterminationItemInput(
            milestone_ref=milestone_ref, eot_granted_date=granted_date,
            granted_extension_days=1, determination_result="partially_granted",
        )],
    ), _user())
    return await svc.freeze_determination(determination, _user())


@pytest.mark.asyncio
async def test_later_determination_of_an_earlier_eot_does_not_regress_the_contractual_date():
    """The Employer may answer EOT-2 before EOT-1. Freezing the older, less
    generous determination afterwards must not pull the milestone back in."""
    db = _DB()
    await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())
    eot1 = await _locked_submission(svc, "CON/EOT-1", datetime(2026, 2, 1), datetime(2026, 3, 1), 60)
    eot2 = await _locked_submission(svc, "CON/EOT-2", datetime(2026, 4, 1), datetime(2026, 5, 1), 120)

    await _frozen_determination(
        svc, [eot2["_id"]], "CLIENT/2026/200", datetime(2026, 7, 1), datetime(2026, 9, 1),
    )
    await _frozen_determination(
        svc, [eot1["_id"]], "CLIENT/2026/145", datetime(2026, 6, 1), datetime(2026, 5, 25),
    )

    milestone = await db.key_date_milestones.find_one({"_id": "m-1"})
    assert milestone["current_approved_key_date"] == datetime(2026, 9, 1)


@pytest.mark.asyncio
async def test_recompute_effective_dates_is_idempotent():
    db = _DB()
    await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())
    eot1 = await _locked_submission(svc, "CON/EOT-1", datetime(2026, 2, 1), datetime(2026, 3, 1), 60)
    await _frozen_determination(
        svc, [eot1["_id"]], "CLIENT/2026/145", datetime(2026, 6, 1), datetime(2026, 5, 25),
    )

    settled = await db.key_date_milestones.find_one({"_id": "m-1"})
    changes = await svc.recompute_effective_dates("org-A", "proj-A", "primary")

    assert changes == []
    after = await db.key_date_milestones.find_one({"_id": "m-1"})
    assert after["current_approved_key_date"] == settled["current_approved_key_date"]


@pytest.mark.asyncio
async def test_superseded_determination_is_excluded_from_the_effective_set():
    db = _DB()
    await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())
    eot1 = await _locked_submission(svc, "CON/EOT-1", datetime(2026, 2, 1), datetime(2026, 3, 1), 60)
    eot2 = await _locked_submission(
        svc, "CON/EOT-2", datetime(2026, 4, 1), datetime(2026, 5, 1), 120, milestone_ref="KD-02",
    )

    superseded = await _frozen_determination(
        svc, [eot1["_id"]], "CLIENT/2026/145", datetime(2026, 6, 1), datetime(2026, 9, 1),
    )
    await _frozen_determination(
        svc, [eot2["_id"]], "CLIENT/2026/210", datetime(2026, 7, 1), datetime(2026, 8, 1),
        supersedes=[superseded["_id"]], milestone_ref="KD-02",
    )

    # KD-01's only grant came from the superseded determination, so it reverts to
    # the frozen Original baseline rather than keeping a withdrawn extension.
    kd01 = await db.key_date_milestones.find_one({"_id": "m-1"})
    kd02 = await db.key_date_milestones.find_one({"_id": "m-2"})
    assert kd01["current_approved_key_date"] == datetime(2026, 1, 1)
    assert kd02["current_approved_key_date"] == datetime(2026, 8, 1)


@pytest.mark.asyncio
async def test_register_projection_ignores_an_unfrozen_determination():
    """A determination the Employer has not frozen is a proposal, not the
    milestone's contractual status."""
    db = _DB()
    await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())
    eot1 = await _locked_submission(svc, "CON/EOT-1", datetime(2026, 2, 1), datetime(2026, 3, 1), 60)
    await svc.create_determination(EOTDeterminationCreate(
        organization_id="org-A", project_id="proj-A",
        eot_submission_ids=[eot1["_id"]],
        determination_reference="CLIENT/2026/DRAFT",
        determination_date=datetime(2026, 6, 1),
        status="granted",
        items=[EOTDeterminationItemInput(
            milestone_ref="KD-01", eot_granted_date=datetime(2026, 5, 25),
            granted_extension_days=1, determination_result="granted",
        )],
    ), _user())

    projection = await KeyDateService(db)._workflow_summary_for(["m-1"])

    assert projection["m-1"]["latest_eot_status"] == "pending"
    assert projection["m-1"]["pending_eot_count"] == 1


async def _seed_link_targets(db):
    """One in-scope document + letter, and two out-of-scope decoys."""
    ids = {
        "document": ObjectId(),
        "letter": ObjectId(),
        "other_project_document": ObjectId(),
        "other_org_letter": ObjectId(),
    }
    await db.documents.insert_one({
        "_id": ids["document"], "organization_id": "org-A", "project_id": "proj-A",
    })
    await db.documents.insert_one({
        "_id": ids["other_project_document"], "organization_id": "org-A", "project_id": "proj-B",
    })
    await db.letters.insert_one({
        "_id": ids["letter"], "organization_id": "org-A", "project_id": "proj-A",
    })
    await db.letters.insert_one({
        "_id": ids["other_org_letter"], "organization_id": "org-B", "project_id": "proj-A",
    })
    return {name: str(value) for name, value in ids.items()}


@pytest.mark.asyncio
async def test_submission_letter_links_round_trip_without_legacy_document_membership():
    db = _DB()
    await _seed(db)
    links = await _seed_link_targets(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())

    submission = await svc.create_submission(EOTSubmissionCreate(
        organization_id="org-A", project_id="proj-A",
        contractor_submission_date=datetime(2026, 2, 1),
        contractor_letter_reference="CON/EOT-1", status="submitted",
        linked_document_ids=[],
        linked_letter_ids=[links["letter"]],
        items=[EOTSubmissionItemInput(
            milestone_ref="KD-01", eot_submitted_date=datetime(2026, 3, 1), claimed_extension_days=60,
        )],
    ), _user())

    assert submission.get("linked_document_ids") in (None, [])
    assert submission["linked_letter_ids"] == [links["letter"]]


@pytest.mark.asyncio
async def test_submission_direct_service_rejects_ambiguous_legacy_document_intent():
    db = _DB()
    await _seed(db)
    links = await _seed_link_targets(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())

    with pytest.raises(KeyDateError, match="manual review"):
        await svc.create_submission(EOTSubmissionCreate(
            organization_id="org-A", project_id="proj-A",
            contractor_submission_date=datetime(2026, 2, 1),
            contractor_letter_reference="CON/EOT-1", status="submitted",
            linked_document_ids=[links["other_project_document"]],
            items=[EOTSubmissionItemInput(
                milestone_ref="KD-01", eot_submitted_date=datetime(2026, 3, 1), claimed_extension_days=60,
            )],
        ), _user())


@pytest.mark.asyncio
async def test_determination_rejects_a_letter_from_another_organisation():
    db = _DB()
    await _seed(db)
    links = await _seed_link_targets(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())
    eot1 = await _locked_submission(svc, "CON/EOT-1", datetime(2026, 2, 1), datetime(2026, 3, 1), 60)

    with pytest.raises(KeyDateError, match="outside the selected project"):
        await svc.create_determination(EOTDeterminationCreate(
            organization_id="org-A", project_id="proj-A",
            eot_submission_ids=[eot1["_id"]],
            determination_reference="CLIENT/2026/145",
            determination_date=datetime(2026, 6, 1),
            approval_grant_reference="CLIENT/2026/145/GRANT",
            status="partially_granted",
            linked_letter_ids=[links["other_org_letter"]],
            items=[EOTDeterminationItemInput(
                milestone_ref="KD-01", eot_granted_date=datetime(2026, 5, 25),
                granted_extension_days=1, determination_result="partially_granted",
            )],
        ), _user())


@pytest.mark.asyncio
async def test_submission_direct_service_rejects_unparseable_legacy_document_intent():
    db = _DB()
    await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())

    with pytest.raises(KeyDateError, match="manual review"):
        await svc.create_submission(EOTSubmissionCreate(
            organization_id="org-A", project_id="proj-A",
            contractor_submission_date=datetime(2026, 2, 1),
            contractor_letter_reference="CON/EOT-1", status="submitted",
            linked_document_ids=["not-an-object-id"],
            items=[EOTSubmissionItemInput(
                milestone_ref="KD-01", eot_submitted_date=datetime(2026, 3, 1), claimed_extension_days=60,
            )],
        ), _user())


def _employer_determination(**overrides):
    payload = {
        "organization_id": "org-A", "project_id": "proj-A",
        "origin": "employer_initiated",
        "eot_submission_ids": [],
        "determination_reference": "CLIENT/2026/OWN-1",
        "determination_date": datetime(2026, 6, 1),
        "approval_grant_reference": "CLIENT/2026/OWN-1/GRANT",
        "status": "granted",
        "remarks": "Engineer's own-initiative extension under sub-clause 8.5",
        "items": [EOTDeterminationItemInput(
            milestone_ref="KD-01", eot_granted_date=datetime(2026, 4, 1),
            granted_extension_days=90, determination_result="granted",
        )],
    }
    payload.update(overrides)
    return EOTDeterminationCreate(**payload)


@pytest.mark.asyncio
async def test_employer_initiated_determination_without_any_submission_moves_the_date():
    db = _DB()
    await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())

    determination = await svc.create_determination(_employer_determination(), _user())
    assert determination["eot_submission_ids"] == []
    assert determination["origin"] == "employer_initiated"

    await svc.freeze_determination(determination, _user())

    milestone = await db.key_date_milestones.find_one({"_id": "m-1"})
    assert milestone["current_approved_key_date"] == datetime(2026, 4, 1)
    # The item carries no contractor claim, because there was no submission.
    item = determination["items"][0]
    assert item["submitted_date"] is None
    assert item["claimed_extension_days"] is None


@pytest.mark.asyncio
async def test_employer_initiated_determination_requires_a_reason():
    db = _DB()
    await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())

    with pytest.raises(KeyDateError, match="reason"):
        await svc.create_determination(_employer_determination(remarks="   "), _user())


@pytest.mark.asyncio
async def test_employer_initiated_determination_cannot_also_cover_submissions():
    db = _DB()
    await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())
    eot1 = await _locked_submission(svc, "CON/EOT-1", datetime(2026, 2, 1), datetime(2026, 3, 1), 60)

    with pytest.raises(KeyDateError, match="employer-initiated"):
        await svc.create_determination(
            _employer_determination(eot_submission_ids=[eot1["_id"]]), _user(),
        )


@pytest.mark.asyncio
async def test_contractor_origin_still_requires_a_covered_submission():
    db = _DB()
    await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())

    with pytest.raises(KeyDateError, match="at least one EOT submission"):
        await svc.create_determination(
            _employer_determination(origin="contractor_submission", remarks=None), _user(),
        )


@pytest.mark.asyncio
async def test_employer_initiated_determination_rejects_a_milestone_outside_the_project():
    db = _DB()
    await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())

    with pytest.raises(KeyDateError, match="does not belong to the selected project"):
        await svc.create_determination(_employer_determination(items=[
            EOTDeterminationItemInput(
                milestone_ref="KD-99", eot_granted_date=datetime(2026, 4, 1),
                granted_extension_days=90, determination_result="granted",
            ),
        ]), _user())


@pytest.mark.asyncio
async def test_employer_initiated_determination_csv_matches_project_milestones():
    db = _DB()
    await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())
    determination = await svc.create_determination(_employer_determination(), _user())

    preview, items = await svc.determination_csv_preview(
        determination,
        b"milestone_ref,eot_granted_date,granted_extension_days,determination_result,remarks\n"
        b"KD-02,2026-03-01,45,granted,\n",
    )

    assert preview.can_import, [row.errors for row in preview.rows]
    assert [item.milestone_ref for item in items] == ["KD-02"]


@pytest.mark.asyncio
async def test_workflow_summary_counts_employer_initiated_determinations():
    db = _DB()
    await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())
    determination = await svc.create_determination(_employer_determination(), _user())
    await svc.freeze_determination(determination, _user())

    summary = await svc.workflow_summary("org-A", "proj-A")

    assert summary["employer_initiated_determinations"] == 1
    assert summary["current_contractual_baseline"] == "Employer Determination"


def test_complete_history_export_includes_employer_initiated_determinations():
    """A grant the Employer issued of its own motion changed the contractual
    date, so it must be visible in the history, not just its effect."""
    milestones = [{
        "milestone_ref": "KD-01", "title": "Milestone", "original_planned_key_date": datetime(2026, 1, 1),
        "current_approved_key_date": datetime(2026, 4, 1),
    }]
    determinations = [{
        "_id": "d1", "eot_submission_ids": [], "origin": "employer_initiated",
        "determination_reference": "CLIENT/2026/OWN-1", "status": "granted",
        "frozen_at": datetime(2026, 6, 1),
        "items": [{
            "milestone_ref": "KD-01", "eot_granted_date": datetime(2026, 4, 1),
            "granted_extension_days": 90, "determination_result": "granted",
        }],
    }]

    headers, rows = history_table(milestones, [], determinations)

    assert "CLIENT/2026/OWN-1 Granted" in headers
    assert "CLIENT/2026/OWN-1 Status" in headers
    granted_index = headers.index("CLIENT/2026/OWN-1 Granted")
    assert rows[0][granted_index] == datetime(2026, 4, 1)
    assert rows[0][granted_index + 1] == "granted"


async def _two_claims_on_kd01(svc):
    eot1 = await _locked_submission(svc, "CON/EOT-1", datetime(2026, 2, 1), datetime(2026, 3, 1), 60)
    eot2 = await _locked_submission(svc, "CON/EOT-2", datetime(2026, 4, 1), datetime(2026, 5, 1), 120)
    return eot1, eot2


def _combined_determination(submission_ids, *, source_submission_id=None, reference="CLIENT/2026/145"):
    return EOTDeterminationCreate(
        organization_id="org-A", project_id="proj-A",
        eot_submission_ids=submission_ids,
        determination_reference=reference,
        determination_date=datetime(2026, 7, 1),
        approval_grant_reference=f"{reference}/GRANT",
        status="partially_granted",
        items=[EOTDeterminationItemInput(
            milestone_ref="KD-01", eot_granted_date=datetime(2026, 4, 15),
            granted_extension_days=45, determination_result="partially_granted",
            source_submission_id=source_submission_id,
        )],
    )


@pytest.mark.asyncio
async def test_combined_determination_keeps_every_covered_claim():
    """One determination answering EOT-1 and EOT-2 must not discard EOT-1's claim."""
    db = _DB()
    await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())
    eot1, eot2 = await _two_claims_on_kd01(svc)

    determination = await svc.create_determination(
        _combined_determination([eot1["_id"], eot2["_id"]]), _user(),
    )

    item = determination["items"][0]
    assert [claim["revision_label"] for claim in item["covered_claims"]] == ["EOT-1", "EOT-2"]
    assert [claim["submitted_date"] for claim in item["covered_claims"]] == [
        datetime(2026, 3, 1), datetime(2026, 5, 1),
    ]
    assert [claim["claimed_extension_days"] for claim in item["covered_claims"]] == [60, 120]
    # Default source is the earliest covered claim that is still undetermined.
    assert item["source_submission_id"] == eot1["_id"]
    assert item["submitted_date"] == datetime(2026, 3, 1)
    assert item["claimed_extension_days"] == 60


@pytest.mark.asyncio
async def test_determination_item_can_name_its_source_submission():
    db = _DB()
    await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())
    eot1, eot2 = await _two_claims_on_kd01(svc)

    determination = await svc.create_determination(
        _combined_determination([eot1["_id"], eot2["_id"]], source_submission_id=eot2["_id"]),
        _user(),
    )

    item = determination["items"][0]
    assert item["source_submission_id"] == eot2["_id"]
    assert item["submitted_date"] == datetime(2026, 5, 1)
    assert item["claimed_extension_days"] == 120
    assert len(item["covered_claims"]) == 2


@pytest.mark.asyncio
async def test_determination_item_rejects_a_source_outside_the_covered_submissions():
    db = _DB()
    await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())
    eot1, eot2 = await _two_claims_on_kd01(svc)

    with pytest.raises(KeyDateError, match="source"):
        await svc.create_determination(
            _combined_determination([eot1["_id"]], source_submission_id=eot2["_id"]), _user(),
        )


@pytest.mark.asyncio
async def test_source_defaults_to_the_earliest_undetermined_submission():
    db = _DB()
    await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())
    eot1, eot2 = await _two_claims_on_kd01(svc)
    await _frozen_determination(
        svc, [eot1["_id"]], "CLIENT/2026/100", datetime(2026, 6, 1), datetime(2026, 3, 20),
    )

    determination = await svc.create_determination(
        _combined_determination([eot1["_id"], eot2["_id"]], reference="CLIENT/2026/210"), _user(),
    )

    item = determination["items"][0]
    assert item["source_submission_id"] == eot2["_id"]
    assert len(item["covered_claims"]) == 2


@pytest.mark.asyncio
async def test_determination_csv_honours_source_submission_ref():
    db = _DB()
    await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())
    eot1, eot2 = await _two_claims_on_kd01(svc)
    determination = await svc.create_determination(
        _combined_determination([eot1["_id"], eot2["_id"]]), _user(),
    )

    result = await svc.import_determination_csv(
        determination,
        b"milestone_ref,source_submission_ref,eot_granted_date,granted_extension_days,determination_result,remarks\n"
        b"KD-01,EOT-2,2026-04-15,45,partially_granted,\n",
        _user(),
    )

    assert result.imported_count == 1, [row.errors for row in result.rows]
    refreshed = await svc.get_determination(str(determination["_id"]))
    assert refreshed["items"][0]["source_submission_id"] == eot2["_id"]
    assert refreshed["items"][0]["submitted_date"] == datetime(2026, 5, 1)


@pytest.mark.asyncio
async def test_determination_csv_rejects_an_unknown_source_submission_ref():
    db = _DB()
    await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())
    eot1, eot2 = await _two_claims_on_kd01(svc)
    determination = await svc.create_determination(
        _combined_determination([eot1["_id"]]), _user(),
    )

    preview, items = await svc.determination_csv_preview(
        determination,
        b"milestone_ref,source_submission_ref,eot_granted_date,granted_extension_days,determination_result\n"
        b"KD-01,EOT-7,2026-04-15,45,partially_granted\n",
    )

    assert not preview.can_import
    assert items == []
    assert any("source_submission_ref" in error for error in preview.rows[0].errors)


@pytest.mark.asyncio
async def test_determination_csv_rejects_the_source_submission_id_column():
    db = _DB()
    await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())
    eot1, _eot2 = await _two_claims_on_kd01(svc)
    determination = await svc.create_determination(
        _combined_determination([eot1["_id"]]), _user(),
    )

    with pytest.raises(KeyDateError, match="Protected"):
        await svc.determination_csv_preview(
            determination,
            b"milestone_ref,source_submission_id,determination_result\nKD-01,anything,rejected\n",
        )


def test_determination_export_names_the_submission_each_grant_answers():
    determination = {
        "_id": "d1", "covered_revision_labels": ["EOT-1", "EOT-2"],
        "items": [{
            "milestone_ref": "KD-01", "description": "Milestone",
            "submitted_date": datetime(2026, 3, 1), "eot_granted_date": datetime(2026, 4, 15),
            "claimed_extension_days": 60, "granted_extension_days": 45,
            "determination_result": "partially_granted",
            "source_submission_id": "s1",
            "covered_claims": [
                {"eot_submission_id": "s1", "revision_label": "EOT-1",
                 "submitted_date": datetime(2026, 3, 1), "claimed_extension_days": 60},
                {"eot_submission_id": "s2", "revision_label": "EOT-2",
                 "submitted_date": datetime(2026, 5, 1), "claimed_extension_days": 120},
            ],
        }],
    }

    headers, rows = determination_table(determination)

    assert "Against Submission" in headers
    assert rows[0][headers.index("Against Submission")] == "EOT-1"
    assert "Other Claims Covered" in headers
    assert rows[0][headers.index("Other Claims Covered")] == "EOT-2"


async def _outcome(svc, submission_id):
    summary = await svc.workflow_summary("org-A", "proj-A")
    row = next(r for r in summary["submissions"] if str(r["_id"]) == str(submission_id))
    return row["determination_outcome"]


@pytest.mark.asyncio
async def test_submission_outcome_is_pending_before_any_determination_freezes():
    db = _DB()
    await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())
    eot1 = await _locked_submission(svc, "CON/EOT-1", datetime(2026, 2, 1), datetime(2026, 3, 1), 60)

    assert await _outcome(svc, eot1["_id"]) == "pending"


@pytest.mark.asyncio
async def test_submission_outcome_becomes_not_separately_determined_when_a_later_eot_is_answered():
    """EOT-1 and EOT-2 outstanding; the Employer answers only EOT-2. EOT-1 was
    never determined in its own right and must say so, not sit at 'pending'."""
    db = _DB()
    await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())
    eot1, eot2 = await _two_claims_on_kd01(svc)
    await _frozen_determination(
        svc, [eot2["_id"]], "CLIENT/2026/210", datetime(2026, 7, 1), datetime(2026, 9, 1),
    )

    assert await _outcome(svc, eot1["_id"]) == "not_separately_determined"
    assert await _outcome(svc, eot2["_id"]) == "partially_accepted"

    summary = await svc.workflow_summary("org-A", "proj-A")
    assert summary["not_separately_determined"] == 1


@pytest.mark.asyncio
async def test_submission_outcome_is_rejected_when_nothing_was_granted():
    db = _DB()
    await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())
    eot1 = await _locked_submission(svc, "CON/EOT-1", datetime(2026, 2, 1), datetime(2026, 3, 1), 60)
    determination = await svc.create_determination(EOTDeterminationCreate(
        organization_id="org-A", project_id="proj-A",
        eot_submission_ids=[eot1["_id"]],
        determination_reference="CLIENT/2026/NO", determination_date=datetime(2026, 6, 1),
        status="rejected",
        items=[EOTDeterminationItemInput(
            milestone_ref="KD-01", granted_extension_days=0, determination_result="rejected",
        )],
    ), _user())
    await svc.freeze_determination(determination, _user())

    assert await _outcome(svc, eot1["_id"]) == "rejected"
    milestone = await db.key_date_milestones.find_one({"_id": "m-1"})
    assert milestone["current_approved_key_date"] == datetime(2026, 1, 1)


@pytest.mark.asyncio
async def test_supersede_records_the_replacement_and_the_reason():
    db = _DB()
    await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())
    eot1, eot2 = await _two_claims_on_kd01(svc)

    superseded = await svc.supersede_submission(
        eot1, eot2["_id"], "EOT-1 withdrawn and reissued as EOT-2", _user(),
    )

    assert superseded["status"] == "superseded"
    assert superseded["superseded_by_submission_id"] == eot2["_id"]
    assert superseded["superseded_reason"] == "EOT-1 withdrawn and reissued as EOT-2"
    assert superseded["superseded_at"] is not None
    assert await _outcome(svc, eot1["_id"]) == "superseded"


@pytest.mark.asyncio
async def test_supersede_requires_a_reason():
    db = _DB()
    await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())
    eot1, eot2 = await _two_claims_on_kd01(svc)

    with pytest.raises(KeyDateError, match="reason"):
        await svc.supersede_submission(eot1, eot2["_id"], "   ", _user())


@pytest.mark.asyncio
async def test_supersede_requires_a_later_submission():
    db = _DB()
    await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())
    eot1, eot2 = await _two_claims_on_kd01(svc)

    with pytest.raises(KeyDateError, match="later"):
        await svc.supersede_submission(eot2, eot1["_id"], "wrong direction", _user())


@pytest.mark.asyncio
async def test_supersede_is_refused_once_a_frozen_determination_covers_the_submission():
    db = _DB()
    await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())
    eot1, eot2 = await _two_claims_on_kd01(svc)
    await _frozen_determination(
        svc, [eot1["_id"]], "CLIENT/2026/145", datetime(2026, 6, 1), datetime(2026, 3, 20),
    )

    with pytest.raises(KeyDateError, match="determined"):
        await svc.supersede_submission(eot1, eot2["_id"], "too late", _user())


@pytest.mark.asyncio
async def test_supersede_rejects_a_cycle():
    db = _DB()
    await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())
    eot1, eot2 = await _two_claims_on_kd01(svc)
    await svc.supersede_submission(eot1, eot2["_id"], "reissued", _user())
    refreshed = await svc.get_submission(str(eot2["_id"]))

    with pytest.raises(KeyDateError, match="superseded"):
        await svc.supersede_submission(refreshed, eot1["_id"], "circular", _user())


def test_complete_history_export_annotates_submission_outcomes():
    milestones = [{
        "milestone_ref": "KD-01", "title": "Milestone", "original_planned_key_date": datetime(2026, 1, 1),
        "current_approved_key_date": datetime(2026, 1, 1),
    }]
    submissions = [
        {"_id": "e1", "revision_number": 1, "revision_label": "EOT-1",
         "items": [{"milestone_ref": "KD-01", "eot_submitted_date": datetime(2026, 3, 1)}]},
    ]

    headers, rows = history_table(
        milestones, submissions, [], outcomes={"e1": "not_separately_determined"},
    )

    assert "EOT-1 Outcome" in headers
    assert rows[0][headers.index("EOT-1 Outcome")] == "not_separately_determined"


@pytest.mark.asyncio
async def test_baseline_label_names_the_determination_actually_in_force():
    """Found in browser verification: the header read 'EOT-2' while every
    milestone's date came from the EOT-1 determination. A determination frozen
    last but superseded by contractual ordering governs nothing, and must not be
    announced as the current contractual baseline."""
    db = _DB()
    await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())
    eot1, eot2 = await _two_claims_on_kd01(svc)

    # EOT-1 determined first, and its grant is the one that sticks.
    await _frozen_determination(
        svc, [eot1["_id"]], "CLIENT/2026/145", datetime(2026, 7, 1), datetime(2026, 4, 15),
    )
    # EOT-2 determined afterwards but dated earlier: no effect on any date.
    await _frozen_determination(
        svc, [eot2["_id"]], "CLIENT/2026/LATE", datetime(2026, 6, 1), datetime(2026, 2, 10),
    )

    milestone = await db.key_date_milestones.find_one({"_id": "m-1"})
    assert milestone["current_approved_key_date"] == datetime(2026, 4, 15)

    summary = await svc.workflow_summary("org-A", "proj-A")
    assert summary["current_contractual_baseline"] == "EOT-1", summary["current_contractual_baseline"]


@pytest.mark.parametrize("raw,expected", [
    ("15-04-2026", datetime(2026, 4, 15)),
    ("15/04/2026", datetime(2026, 4, 15)),
    ("01-02-2026", datetime(2026, 2, 1)),          # day-first, never month-first
    ("2026-04-15", datetime(2026, 4, 15)),          # ISO still accepted
    ("2026-04-15T00:00:00", datetime(2026, 4, 15)),
])
def test_dates_parse_day_first_as_well_as_iso(raw, expected):
    from rbac_backend.services.key_date_service import _as_dt
    assert _as_dt(raw) == expected


def test_unparseable_dates_are_still_rejected():
    from rbac_backend.services.key_date_service import _as_dt
    assert _as_dt("not a date") is None
    assert _as_dt("32-01-2026") is None


def test_exports_render_dates_day_first():
    from rbac_backend.services.key_date_revision_export import _cell
    assert _cell(datetime(2026, 4, 15)) == "15-04-2026"
    assert _cell(None) == ""


def test_history_export_rows_are_day_first():
    milestones = [{
        "milestone_ref": "KD-01", "title": "M", "original_planned_key_date": datetime(2026, 1, 1),
        "current_approved_key_date": datetime(2026, 4, 15),
    }]
    submissions = [{
        "_id": "e1", "revision_number": 1, "revision_label": "EOT-1",
        "items": [{"milestone_ref": "KD-01", "eot_submitted_date": datetime(2026, 3, 1)}],
    }]
    _headers, rows = history_table(milestones, submissions, [])
    from rbac_backend.services.key_date_revision_export import _cell
    rendered = [_cell(value) for value in rows[0]]
    assert "01-01-2026" in rendered
    assert "15-04-2026" in rendered
    assert not any("2026-01-01" == value for value in rendered)


@pytest.mark.asyncio
async def test_determination_csv_accepts_day_first_dates():
    """Exports are day-first, so the download -> edit -> re-upload round trip
    must parse what the export produced."""
    db = _DB()
    await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())
    eot1 = await _locked_submission(svc, "CON/EOT-1", datetime(2026, 2, 1), datetime(2026, 3, 1), 60)
    determination = await svc.create_determination(
        _combined_determination([eot1["_id"]]), _user(),
    )

    preview, items = await svc.determination_csv_preview(
        determination,
        b"milestone_ref,eot_granted_date,granted_extension_days,determination_result\n"
        b"KD-01,15-04-2026,45,partially_granted\n",
    )

    assert preview.can_import, [row.errors for row in preview.rows]
    assert items[0].eot_granted_date == datetime(2026, 4, 15)


@pytest.mark.asyncio
async def test_submission_csv_accepts_day_first_dates():
    db = _DB()
    await _seed(db)
    svc = KeyDateRevisionService(db)
    await svc.freeze_baseline("org-A", "proj-A", "primary", _user())
    submission = await svc.create_submission(EOTSubmissionCreate(
        organization_id="org-A", project_id="proj-A", status="draft", items=[],
    ), _user())

    preview, items = await svc.submission_csv_preview(
        submission,
        b"milestone_ref,eot_submitted_date,claimed_extension_days\nKD-01,01-05-2026,120\n",
    )

    assert preview.can_import, [row.errors for row in preview.rows]
    assert items[0].eot_submitted_date == datetime(2026, 5, 1)
    assert preview.rows[0].data["eot_submitted_date"] == "01-05-2026"


def test_only_one_frozen_baseline_guard_exists():
    """`KeyDateRevisionService.assert_baseline_editable` was never called; the
    live guard is `KeyDateService._assert_original_baseline_editable`. Two
    near-identically named methods, one inert, is a trap — a new write path can
    call the dead one and ship with no guard at all."""
    assert not hasattr(KeyDateRevisionService, "assert_baseline_editable")
    assert hasattr(KeyDateService, "_assert_original_baseline_editable")


def test_legacy_per_milestone_eot_routes_are_marked_deprecated():
    """The project-level revision workflow supersedes the per-milestone EOT
    endpoints. They still work, but must advertise their status."""
    from rbac_backend.routers.key_dates import router

    legacy_paths = {
        "/key-dates/{milestone_id}/eot",
        "/key-dates/{milestone_id}/eots",
        "/key-dates/{milestone_id}/eot/{eot_id}/review",
        "/key-dates/{milestone_id}/history",
    }
    seen = {
        route.path: bool(getattr(route, "deprecated", False))
        for route in router.routes
        if getattr(route, "path", None) in legacy_paths
    }
    assert set(seen) == legacy_paths, f"legacy route set changed: {sorted(seen)}"
    assert all(seen.values()), f"not marked deprecated: {sorted(k for k, v in seen.items() if not v)}"

    # The replacement surface must NOT be flagged.
    current = [
        route for route in router.routes
        if getattr(route, "path", "").startswith("/key-dates/eot-submissions")
    ]
    assert current and not any(getattr(route, "deprecated", False) for route in current)


def test_revision_workflow_sensitive_actions_have_backend_permission_catalog_entries():
    expected = {
        Permissions.KEYDATE_BASELINE_FREEZE,
        Permissions.KEYDATE_EOT_LOCK_SUBMISSION,
        Permissions.KEYDATE_EOT_DETERMINE,
        Permissions.KEYDATE_EOT_FREEZE_DETERMINATION,
        Permissions.KEYDATE_EOT_SUPERSEDE,
    }
    assert expected <= set(CLIENT_DMS_PERMISSIONS)
    assert expected <= {row["_id"] for row in DEFAULT_PERMISSIONS}
