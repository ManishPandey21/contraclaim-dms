from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from rbac_backend.models.chronology import (
    AttachChronologyRequest,
    ChronologyDecisionRequest,
    ChronologyExtractRequest,
    ChronologyVerificationStatus,
    MatterChronologyCreate,
    MatterChronologyEventCreate,
)
from rbac_backend.services.chronology import ChronologyService


class _Cursor:
    def __init__(self, docs):
        self._docs = [dict(doc) for doc in docs]

    def sort(self, key, direction=1):
        if isinstance(key, list):
            for item_key, item_direction in reversed(key):
                self._docs.sort(key=lambda row: row.get(item_key) or datetime.min, reverse=item_direction == -1)
        else:
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

    async def insert_many(self, docs):
        ids = []
        for doc in docs:
            result = await self.insert_one(doc)
            ids.append(result.inserted_id)
        return SimpleNamespace(inserted_ids=ids)

    async def find_one(self, query, sort=None):
        rows = [doc for doc in self.docs.values() if _matches(doc, query)]
        if sort:
            for key, direction in reversed(sort):
                rows.sort(key=lambda row: row.get(key) or 0, reverse=direction == -1)
        return dict(rows[0]) if rows else None

    def find(self, query):
        return _Cursor([doc for doc in self.docs.values() if _matches(doc, query)])

    async def find_one_and_update(self, query, update, return_document=True):
        for key, doc in self.docs.items():
            if _matches(doc, query):
                doc.update(update.get("$set", {}))
                self.docs[key] = doc
                return dict(doc)
        return None

    async def update_one(self, query, update):
        await self.find_one_and_update(query, update)
        return SimpleNamespace(modified_count=1)

    async def delete_many(self, query):
        doomed = [key for key, doc in self.docs.items() if _matches(doc, query)]
        for key in doomed:
            del self.docs[key]
        return SimpleNamespace(deleted_count=len(doomed))


def _matches(doc, query):
    for key, expected in (query or {}).items():
        actual = doc
        for part in key.split("."):
            actual = actual.get(part) if isinstance(actual, dict) else None
        if isinstance(expected, dict):
            if "$exists" in expected:
                exists = actual is not None
                if exists != expected["$exists"]:
                    return False
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$regex" in expected:
                import re

                flags = re.I if expected.get("$options") == "i" else 0
                if not re.search(expected["$regex"], str(actual or ""), flags=flags):
                    return False
            if "$gte" in expected and actual < expected["$gte"]:
                return False
            if "$lte" in expected and actual > expected["$lte"]:
                return False
            continue
        if actual != expected:
            return False
    return True


class _DB:
    def __init__(self):
        self.matter_chronologies = _Collection()
        self.matter_chronology_events = _Collection()
        self.matter_chronology_event_revisions = _Collection()
        self.matter_chronology_exports = _Collection()
        self.documents = _Collection()
        self.project_events = _Collection()
        self.event_links = _Collection()
        self.ai_extractions = _Collection()
        self.arbitration_drafts = _Collection()
        self.arbitration_selected_references = _Collection()
        self.arbitration_claim_heads = _Collection()
        self.arbitration_paragraph_responses = _Collection()
        self.arbitration_generation_runs = _Collection()
        self.arbitration_draft_versions = _Collection()


class _Audit:
    async def emit(self, **_kwargs):
        return None


def _svc():
    service = ChronologyService(_DB())
    service.audit = _Audit()
    service.graph.audit = _Audit()
    return service


def _user():
    return SimpleNamespace(id="user-1", organization_id="org-A")


class _VerifierPolicy:
    """Holds `dms.chronology.verify`: review decisions check it in the service."""

    async def authorize_document(self, current_user, permission, document, *, resource_type="document"):
        return None


@pytest.mark.asyncio
async def test_extract_verify_syncs_chronology_event_to_graph():
    svc = _svc()
    await svc.db.documents.insert_one(
        {
            "_id": "doc-1",
            "organization_id": "org-A",
            "project_id": "proj-A",
            "date": datetime(2025, 4, 8),
            "subject": "CPL/2025/0142 Notification of Delay",
            "summary": "Late issue of Basement 2 Rev C drawing under GCC 8.4.",
            "filename": "delay-letter.pdf",
        }
    )
    chronology = await svc.create_chronology(
        MatterChronologyCreate(
            organization_id="org-A",
            project_id="proj-A",
            title="EOT chronology",
            chronology_type="eot_delay",
            party_perspective="claimant",
            selected_source_ids=["doc-1"],
        ),
        _user(),
    )

    extracted = await svc.extract_events(chronology["_id"], ChronologyExtractRequest(), _user())
    event = extracted["events"][0]
    verified = await svc.verify_event(chronology["_id"], event["_id"], _user(), ChronologyDecisionRequest(note="checked"), policy=_VerifierPolicy())
    revisions = await svc.event_revisions(chronology["_id"], event["_id"])

    assert extracted["events_created"] == 1
    assert verified["verification_status"] == ChronologyVerificationStatus.VERIFIED
    assert verified["project_event_id"]
    assert len(svc.db.project_events.docs) == 1
    assert len(svc.db.event_links.docs) >= 2
    assert [row["revision"] for row in revisions] == [1, 2]


@pytest.mark.asyncio
async def test_pleading_context_and_attach_use_only_verified_events_by_default():
    svc = _svc()
    await svc.db.documents.insert_one(
        {
            "_id": "doc-2",
            "organization_id": "org-A",
            "project_id": "proj-A",
            "processing_status": "completed",
        }
    )
    chronology = await svc.create_chronology(
        MatterChronologyCreate(organization_id="org-A", project_id="proj-A", title="Payment chronology"),
        _user(),
    )
    event = await svc.create_event(
        MatterChronologyEventCreate(
            chronology_id=chronology["_id"],
            event_date=datetime(2025, 5, 1),
            title="IPC 14 remained unpaid",
            description="IPC 14 payment remained disputed.",
            event_classification="payment",
            impact_type="cost",
            source_document_id="doc-2",
        ),
        _user(),
    )
    await svc.db.arbitration_drafts.insert_one(
        {
            "_id": "draft-1",
            "organization_id": "org-A",
            "project_id": "proj-A",
            "draft_type": "statement_of_claim",
            "party_role": "claimant",
            "dispute_type": "payment_dispute",
            "title": "SoC",
        }
    )

    empty_context = await svc.pleading_context(chronology["_id"])
    await svc.verify_event(chronology["_id"], event["_id"], _user(), policy=_VerifierPolicy())
    context = await svc.pleading_context(chronology["_id"])
    attached = await svc.attach_to_arbitration_draft("draft-1", AttachChronologyRequest(chronology_id=chronology["_id"]), _user())

    assert empty_context.source_ledger == []
    assert len(context.source_ledger) == 1
    assert context.source_ledger[0]["source_type"] == "chronology_event"
    assert attached["reference_count"] == 1
    assert next(iter(svc.db.arbitration_selected_references.docs.values()))["source_type"] == "chronology_event"
