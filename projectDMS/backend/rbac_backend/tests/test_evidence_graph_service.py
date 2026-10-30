from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from rbac_backend.models.evidence_graph import (
    EventLinkCreate,
    EventLinkStatus,
    EventRelationType,
    EvidenceEntityType,
    ProjectEventCreate,
    ProjectEventType,
)
from rbac_backend.services.evidence_graph_service import EvidenceGraphService


class _Cursor:
    def __init__(self, docs):
        self._docs = [dict(doc) for doc in docs]

    def sort(self, key, direction=1):
        reverse = direction == -1
        if isinstance(key, list):
            for item_key, item_direction in reversed(key):
                self._docs.sort(key=lambda row: row.get(item_key), reverse=item_direction == -1)
        else:
            self._docs.sort(key=lambda row: row.get(key), reverse=reverse)
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


def _matches(doc, query):
    for key, expected in (query or {}).items():
        actual = doc
        for part in key.split("."):
            actual = actual.get(part) if isinstance(actual, dict) else None
        if isinstance(expected, dict):
            if "$in" in expected and actual not in expected["$in"]:
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
        self.project_events = _Collection()
        self.event_links = _Collection()
        self.ai_extractions = _Collection()


class _Audit:
    async def emit(self, **_kwargs):
        return None


def _svc():
    service = EvidenceGraphService(_DB())
    service.audit = _Audit()
    return service


def _user():
    return SimpleNamespace(id="user-1")


@pytest.mark.asyncio
async def test_verify_and_reject_append_new_revisions():
    svc = _svc()
    link = await svc.suggest_link(
        EventLinkCreate(
            organization_id="org-A",
            project_id="proj-A",
            source_type=EvidenceEntityType.PROJECT_EVENT,
            source_id="evt-1",
            target_type=EvidenceEntityType.CLAUSE,
            target_id="GCC 8.4",
            relation_type=EventRelationType.GOVERNED_BY,
            confidence=0.9,
        ),
        current_user=_user(),
    )

    verified = await svc.verify_link(link["link_group_id"], _user(), note="checked")
    rejected = await svc.reject_link(link["link_group_id"], _user(), note="wrong target")

    assert link["revision"] == 1
    assert link["status"] == EventLinkStatus.AI_SUGGESTED
    assert verified["revision"] == 2
    assert verified["status"] == EventLinkStatus.USER_VERIFIED
    assert verified["metadata"]["decision_note"] == "checked"
    assert rejected["revision"] == 3
    assert rejected["status"] == EventLinkStatus.REJECTED

    latest = await svc.get_latest_link(link["link_group_id"])
    assert latest["revision"] == 3
    assert latest["status"] == EventLinkStatus.REJECTED


@pytest.mark.asyncio
async def test_timeline_uses_latest_link_revision_and_counts_statuses():
    svc = _svc()
    event = await svc.create_project_event(
        ProjectEventCreate(
            organization_id="org-A",
            project_id="proj-A",
            event_type=ProjectEventType.LETTER,
            event_date=datetime(2025, 5, 2),
            title="EOT claim",
        ),
        _user(),
    )
    link = await svc.suggest_link(
        EventLinkCreate(
            organization_id="org-A",
            project_id="proj-A",
            source_type=EvidenceEntityType.PROJECT_EVENT,
            source_id=event["_id"],
            target_type=EvidenceEntityType.CLAUSE,
            target_id="GCC 8.4",
            relation_type=EventRelationType.GOVERNED_BY,
            confidence=0.9,
        ),
        current_user=_user(),
    )
    await svc.verify_link(link["link_group_id"], _user())

    timeline = await svc.timeline({"organization_id": "org-A", "project_id": "proj-A"})

    assert timeline.summary.total_events == 1
    assert timeline.summary.graph_links == 1
    assert timeline.summary.ai_suggested == 0
    assert timeline.summary.user_verified == 1
    assert timeline.events[0].links[0].revision == 2
    assert timeline.events[0].links[0].status == EventLinkStatus.USER_VERIFIED


@pytest.mark.asyncio
async def test_approve_and_history_preserve_all_revisions():
    svc = _svc()
    link = await svc.suggest_link(
        EventLinkCreate(
            organization_id="org-A",
            project_id="proj-A",
            source_type=EvidenceEntityType.PROJECT_EVENT,
            source_id="evt-1",
            target_type=EvidenceEntityType.CLAUSE,
            target_id="GCC 20.1",
            relation_type=EventRelationType.GOVERNED_BY,
        ),
        current_user=_user(),
    )

    await svc.verify_link(link["link_group_id"], _user(), note="reviewed")
    approved = await svc.approve_link(link["link_group_id"], _user(), note="approved")
    history = await svc.get_link_history({"organization_id": "org-A"}, link["link_group_id"])

    assert approved["status"] == EventLinkStatus.APPROVED
    assert approved["revision"] == 3
    assert [item["revision"] for item in history] == [1, 2, 3]
    assert [item["status"] for item in history] == [
        EventLinkStatus.AI_SUGGESTED,
        EventLinkStatus.USER_VERIFIED,
        EventLinkStatus.APPROVED,
    ]


@pytest.mark.asyncio
async def test_downstream_links_exclude_ai_suggestions_unless_requested():
    svc = _svc()
    ai_link = await svc.suggest_link(
        EventLinkCreate(
            organization_id="org-A",
            project_id="proj-A",
            source_type=EvidenceEntityType.PROJECT_EVENT,
            source_id="evt-1",
            target_type=EvidenceEntityType.CLAUSE,
            target_id="GCC 8.4",
            relation_type=EventRelationType.GOVERNED_BY,
        ),
        current_user=_user(),
    )
    verified_seed = await svc.suggest_link(
        EventLinkCreate(
            organization_id="org-A",
            project_id="proj-A",
            source_type=EvidenceEntityType.PROJECT_EVENT,
            source_id="evt-2",
            target_type=EvidenceEntityType.CLAUSE,
            target_id="GCC 20.1",
            relation_type=EventRelationType.GOVERNED_BY,
        ),
        current_user=_user(),
    )
    await svc.verify_link(verified_seed["link_group_id"], _user())

    default_links = await svc.downstream_links({"organization_id": "org-A"})
    review_links = await svc.downstream_links({"organization_id": "org-A"}, include_ai_suggested=True)

    assert [link["link_group_id"] for link in default_links] == [verified_seed["link_group_id"]]
    assert {link["link_group_id"] for link in review_links} == {ai_link["link_group_id"], verified_seed["link_group_id"]}


@pytest.mark.asyncio
async def test_timeline_filters_by_metadata_and_link_targets():
    svc = _svc()
    event = await svc.create_project_event(
        ProjectEventCreate(
            organization_id="org-A",
            project_id="proj-A",
            event_type=ProjectEventType.CLAIM,
            event_date=datetime(2025, 5, 2),
            title="EOT claim",
            metadata={
                "claim_type": "eot",
                "location": "Basement 2",
                "delay_responsibility": "employer",
                "payment_status": "disputed",
                "clauses": ["GCC 8.4"],
            },
        ),
        _user(),
    )
    await svc.suggest_link(
        EventLinkCreate(
            organization_id="org-A",
            project_id="proj-A",
            source_type=EvidenceEntityType.PROJECT_EVENT,
            source_id=event["_id"],
            target_type=EvidenceEntityType.CLAUSE,
            target_id="GCC 8.4",
            relation_type=EventRelationType.GOVERNED_BY,
        ),
        current_user=_user(),
    )

    matched = await svc.timeline(
        {"organization_id": "org-A"},
        claim_type="eot",
        location="Basement 2",
        delay_responsibility="employer",
        payment_status="disputed",
        clause="8.4",
    )
    missed = await svc.timeline({"organization_id": "org-A"}, claim_type="variation")

    assert matched.summary.total_events == 1
    assert matched.summary.graph_links == 1
    assert missed.summary.total_events == 0


@pytest.mark.asyncio
async def test_ingest_document_metadata_is_idempotent_for_same_schema_hash():
    svc = _svc()
    metadata = SimpleNamespace(
        subject="CPL/2025/0142 Notification of Delay",
        summary="Late issue of Basement 2 Rev C Drawing under GCC 8.4",
        full_content="GCC 8.4 DWG-C-014 Rev C IPC 14",
        letter_no="CPL/2025/0142",
        from_company="Aurora Engineering",
        contractual_clauses=["GCC 8.4"],
    )
    document_data = {
        "_id": "doc-1",
        "organization_id": "org-A",
        "project_id": "proj-A",
        "date": datetime(2025, 4, 8),
        "package": "Structural",
        "reference": [{"letterNo": "AUR/2025/0089"}],
    }

    first = await svc.ingest_document_metadata(
        document_id="doc-1",
        document_data=document_data,
        metadata=metadata,
        metadata_source="test-model",
        upload_type="incoming",
        current_user=_user(),
    )
    second = await svc.ingest_document_metadata(
        document_id="doc-1",
        document_data=document_data,
        metadata=metadata,
        metadata_source="test-model",
        upload_type="incoming",
        current_user=_user(),
    )

    assert first["_id"] == second["_id"]
    assert len(svc.db.ai_extractions.docs) == 1
    assert len(svc.db.project_events.docs) == 1
    assert len(svc.db.event_links.docs) >= 3


@pytest.mark.asyncio
async def test_ingesting_a_quarantined_document_skips_without_raising():
    """The containment branch has to return, not blow up.

    `ingest_document_metadata` refuses to build persistent AI-extraction and
    project-event records from a non-consumable source. That branch logged the
    refusal through a `logger` the module never defined, so the one path whose
    whole job is to decline raised NameError instead of declining. Nothing
    caught it here; the guard held only because the exception aborted the
    function before any record was written.
    """
    svc = _svc()
    metadata = SimpleNamespace(
        subject="CPL/2025/0142 Notification of Delay",
        summary="Late issue of Basement 2 Rev C Drawing under GCC 8.4",
        full_content="GCC 8.4 DWG-C-014 Rev C IPC 14",
        letter_no="CPL/2025/0142",
        from_company="Aurora Engineering",
        contractual_clauses=["GCC 8.4"],
    )
    quarantined = {
        "_id": "doc-dup",
        "organization_id": "org-A",
        "project_id": "proj-A",
        "date": datetime(2025, 4, 8),
        "duplicate_status": "duplicate",
    }

    result = await svc.ingest_document_metadata(
        document_id="doc-dup",
        document_data=quarantined,
        metadata=metadata,
        metadata_source="test-model",
        upload_type="incoming",
        current_user=_user(),
    )

    assert result is None
    # Containment: nothing derived from a quarantined source is persisted.
    assert len(svc.db.ai_extractions.docs) == 0
    assert len(svc.db.project_events.docs) == 0
    assert len(svc.db.event_links.docs) == 0
