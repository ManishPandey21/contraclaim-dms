from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from rbac_backend.models.evidence_graph import EventLinkStatus, EvidenceEntityType
from rbac_backend.models.evidence_registers import (
    DelayEventCreate,
    DelayResponsibility,
    DrawingReferenceCreate,
    ProgrammeMilestoneCreate,
)
from rbac_backend.services.evidence_graph_backfill_service import EvidenceGraphBackfillService
from rbac_backend.services.evidence_graph_reconciliation_service import EvidenceGraphReconciliationService
from rbac_backend.services.evidence_register_service import EvidenceRegisterService


class _Cursor:
    def __init__(self, docs):
        self._docs = [dict(doc) for doc in docs]

    def sort(self, key, direction=1):
        self._docs.sort(key=lambda row: row.get(key) or datetime.min, reverse=direction == -1)
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class _Collection:
    def __init__(self):
        self.docs = {}

    async def insert_one(self, doc):
        doc = dict(doc)
        _id = doc.get("_id") or f"id-{len(self.docs) + 1}"
        doc["_id"] = _id
        self.docs[_id] = doc
        return SimpleNamespace(inserted_id=_id)

    async def find_one(self, query):
        for doc in self.docs.values():
            if _matches(doc, query):
                return dict(doc)
        return None

    def find(self, query):
        return _Cursor([doc for doc in self.docs.values() if _matches(doc, query)])

    async def find_one_and_update(self, query, update, return_document=True, upsert=False):
        for _id, doc in self.docs.items():
            if _matches(doc, query):
                doc.update(update.get("$set", {}))
                for field, amount in update.get("$inc", {}).items():
                    doc[field] = int(doc.get(field) or 0) + int(amount)
                self.docs[_id] = doc
                return dict(doc)
        if upsert:
            # Reference counters (`delay_event_reference_counters`) upsert on _id.
            doc = {key: value for key, value in query.items() if not key.startswith("$")}
            doc.update(update.get("$set", {}))
            for field, amount in update.get("$inc", {}).items():
                doc[field] = int(amount)
            self.docs[doc["_id"]] = doc
            return dict(doc)
        return None

    async def count_documents(self, query):
        return len([doc for doc in self.docs.values() if _matches(doc, query)])


def _matches(doc, query):
    for key, expected in (query or {}).items():
        actual = doc
        for part in key.split("."):
            actual = actual.get(part) if isinstance(actual, dict) else None
        if isinstance(expected, dict):
            if "$in" in expected and actual not in expected["$in"]:
                return False
            continue
        if actual != expected:
            return False
    return True


class _DB:
    def __init__(self):
        for name in (
            "audit_events",
            "project_events",
            "event_links",
            "ai_extractions",
            "drawing_references",
            "delay_events",
            "programme_milestones",
            "documents",
            "claims",
            "key_date_milestones",
            "variations",
            "bank_guarantees",
            "ipc_bills",
            "delay_event_reference_counters",
        ):
            setattr(self, name, _Collection())

    def __getitem__(self, name):
        return getattr(self, name)


def _user():
    return SimpleNamespace(id="user-1", organization_id="org-A")


@pytest.mark.asyncio
async def test_register_creates_domain_record_project_event_and_link():
    db = _DB()
    svc = EvidenceRegisterService(db)

    drawing = await svc.create_drawing_reference(
        DrawingReferenceCreate(
            organization_id="org-A",
            project_id="proj-A",
            drawing_number="DWG-C-014",
            title="Basement slab reinforcement",
            revision="C",
            issue_date=datetime(2025, 2, 12),
            location="Basement 2",
        ),
        _user(),
    )
    delay = await svc.create_delay_event(
        DelayEventCreate(
            organization_id="org-A",
            project_id="proj-A",
            delay_ref="D-001",
            title="Late drawing issue",
            start_date=datetime(2025, 2, 12),
            responsibility=DelayResponsibility.EMPLOYER,
            location="Basement 2",
        ),
        _user(),
    )
    milestone = await svc.create_programme_milestone(
        ProgrammeMilestoneCreate(
            organization_id="org-A",
            project_id="proj-A",
            milestone_ref="M-07",
            title="Basement structure complete",
            planned_date=datetime(2025, 2, 28),
            location="Basement 2",
        ),
        _user(),
    )

    assert drawing["_id"] in db.drawing_references.docs
    assert delay["_id"] in db.delay_events.docs
    assert milestone["_id"] in db.programme_milestones.docs
    assert len(db.project_events.docs) == 3
    assert len(db.event_links.docs) == 3
    assert {link["target_type"] for link in db.event_links.docs.values()} == {
        EvidenceEntityType.DRAWING,
        EvidenceEntityType.DELAY_EVENT,
        EvidenceEntityType.PROGRAMME_MILESTONE,
    }


@pytest.mark.asyncio
async def test_backfill_dry_run_reports_without_mutation_and_backfill_creates_graph_rows():
    db = _DB()
    await db.documents.insert_one(
        {
            "_id": "doc-1",
            "organization_id": "org-A",
            "project_id": "proj-A",
            "subject": "Notification of delay",
            "date": datetime(2025, 4, 8),
        }
    )
    svc = EvidenceGraphBackfillService(db)

    dry = await svc.run({"organization_id": "org-A"}, current_user=_user(), dry_run=True, project_id="proj-A")
    applied = await svc.run({"organization_id": "org-A"}, current_user=_user(), dry_run=False, project_id="proj-A")

    assert dry["sources"]["documents"]["missing_project_events"] == 1
    assert len(db.project_events.docs) == 1
    assert len(db.event_links.docs) == 1
    assert applied["created_project_events"] == 1
    assert next(iter(db.event_links.docs.values()))["status"] == EventLinkStatus.USER_VERIFIED


@pytest.mark.asyncio
async def test_reconciliation_counts_latest_links_and_deleted_document_links():
    db = _DB()
    await db.documents.insert_one({"_id": "doc-1", "organization_id": "org-A", "project_id": "proj-A", "is_deleted": True})
    await db.project_events.insert_one({"_id": "evt-1", "organization_id": "org-A", "project_id": "proj-A"})
    await db.event_links.insert_one(
        {
            "_id": "lnk-1",
            "organization_id": "org-A",
            "project_id": "proj-A",
            "link_group_id": "grp-1",
            "revision": 1,
            "status": EventLinkStatus.APPROVED.value,
            "source_type": EvidenceEntityType.PROJECT_EVENT.value,
            "source_id": "evt-1",
            "target_type": EvidenceEntityType.DOCUMENT.value,
            "target_id": "doc-1",
            "created_at": datetime.utcnow(),
        }
    )

    report = await EvidenceGraphReconciliationService(db).report({"organization_id": "org-A"}, project_id="proj-A")

    assert report["mongo_project_events"] == 1
    assert report["mongo_latest_links"] == 1
    assert report["verified_or_approved_links"] == 1
    assert report["deleted_document_links"] == [
        {"link_group_id": "grp-1", "revision": 1, "side": "target", "document_id": "doc-1"}
    ]
