"""G31 chronology document-derived publication authority regressions.

The production service seam spans document selection, AI extraction, chronology
event/revision persistence, and later verified project-event/link publication.
These tests assert the final durable state at each public service operation.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Callable, Iterable

import pytest
from fastapi import HTTPException

from rbac_backend.models.chronology import (
    ChronologyExtractRequest,
    ChronologyVerificationStatus,
    MatterChronologyCreate,
    MatterChronologyEventCreate,
)
from rbac_backend.services.chronology import ChronologyService


EXTRACTION_MARKER = "CHRONOLOGY_BLOCKED_EXTRACTION_20260819"
EVENT_MARKER = "CHRONOLOGY_BLOCKED_EVENT_20260819"
LINK_MARKER = "CHRONOLOGY_BLOCKED_LINK_20260819"
MANUAL_MARKER = "CHRONOLOGY_MANUAL_INDEPENDENT_20260819"


def _value(value: Any) -> Any:
    return getattr(value, "value", value)


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, expected in (query or {}).items():
        actual: Any = document
        exists = True
        for part in key.split("."):
            if not isinstance(actual, dict) or part not in actual:
                exists = False
                actual = None
                break
            actual = actual[part]
        actual = _value(actual)
        if isinstance(expected, dict):
            if "$exists" in expected and exists is not bool(expected["$exists"]):
                return False
            if "$in" in expected and not any(
                str(actual) == str(_value(candidate)) for candidate in expected["$in"]
            ):
                return False
            if "$ne" in expected and str(actual) == str(_value(expected["$ne"])):
                return False
            if "$gte" in expected and actual < expected["$gte"]:
                return False
            if "$lte" in expected and actual > expected["$lte"]:
                return False
            continue
        if str(actual) != str(_value(expected)):
            return False
    return True


class _Cursor:
    def __init__(
        self,
        documents: Iterable[dict[str, Any]],
        after_collect: Callable[[], None] | None = None,
    ) -> None:
        self.documents = [deepcopy(document) for document in documents]
        self.after_collect = after_collect

    def sort(self, key: Any, direction: int = 1) -> "_Cursor":
        keys = key if isinstance(key, list) else [(key, direction)]
        for item_key, item_direction in reversed(keys):
            self.documents.sort(
                key=lambda row: row.get(item_key) or datetime.min,
                reverse=item_direction == -1,
            )
        return self

    def skip(self, amount: int) -> "_Cursor":
        self.documents = self.documents[amount:]
        return self

    def limit(self, amount: int) -> "_Cursor":
        self.documents = self.documents[:amount]
        return self

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        rows = self.documents if length is None else self.documents[:length]
        if self.after_collect is not None:
            callback, self.after_collect = self.after_collect, None
            callback()
        return deepcopy(rows)

    def __aiter__(self):
        async def generate():
            for document in self.documents:
                yield deepcopy(document)

        return generate()


class _Collection:
    def __init__(self, documents: Iterable[dict[str, Any]] = ()) -> None:
        self.documents = [deepcopy(document) for document in documents]
        self.after_next_collect: Callable[[], None] | None = None
        self.next_find_snapshot: list[dict[str, Any]] | None = None

    async def insert_one(self, document: dict[str, Any]):
        inserted = deepcopy(document)
        inserted.setdefault("_id", f"id-{len(self.documents) + 1}")
        self.documents.append(inserted)
        return SimpleNamespace(inserted_id=inserted["_id"])

    async def find_one(self, query: dict[str, Any], sort=None):
        rows = [document for document in self.documents if _matches(document, query)]
        for key, direction in reversed(sort or []):
            rows.sort(key=lambda row: row.get(key) or 0, reverse=direction == -1)
        return deepcopy(rows[0]) if rows else None

    def find(self, query: dict[str, Any]) -> _Cursor:
        source = (
            self.next_find_snapshot
            if self.next_find_snapshot is not None
            else self.documents
        )
        self.next_find_snapshot = None
        rows = [document for document in source if _matches(document, query)]
        callback, self.after_next_collect = self.after_next_collect, None
        return _Cursor(rows, callback)

    async def find_one_and_update(
        self, query: dict[str, Any], update: dict[str, Any], return_document=True
    ):
        for document in self.documents:
            if _matches(document, query):
                document.update(deepcopy(update.get("$set", {})))
                return deepcopy(document)
        return None

    async def update_one(
        self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False
    ):
        updated = await self.find_one_and_update(query, update)
        return SimpleNamespace(
            matched_count=int(updated is not None),
            modified_count=int(updated is not None),
        )

    async def delete_many(self, query: dict[str, Any]):
        kept = [document for document in self.documents if not _matches(document, query)]
        deleted = len(self.documents) - len(kept)
        self.documents = kept
        return SimpleNamespace(deleted_count=deleted)


class _Database:
    COLLECTIONS = (
        "matter_chronologies",
        "matter_chronology_events",
        "matter_chronology_event_revisions",
        "matter_chronology_exports",
        "documents",
        "ai_extractions",
        "project_events",
        "event_links",
        "audit_events",
        "admin_review_items",
        "arbitration_drafts",
        "arbitration_selected_references",
        "arbitration_claim_heads",
        "arbitration_paragraph_responses",
        "arbitration_generation_runs",
        "arbitration_draft_versions",
    )

    def __init__(self, documents: Iterable[dict[str, Any]] = ()) -> None:
        self._collections = {name: _Collection() for name in self.COLLECTIONS}
        self._collections["documents"] = _Collection(documents)

    def __getitem__(self, name: str) -> _Collection:
        return self._collections.setdefault(name, _Collection())

    def __getattr__(self, name: str) -> _Collection:
        return self[name]


def _user() -> SimpleNamespace:
    return SimpleNamespace(id="chronology-user", organization_id="org-A")


def _document(document_id: str = "doc-A", **overrides: Any) -> dict[str, Any]:
    document = {
        "_id": document_id,
        "organization_id": "org-A",
        "project_id": "proj-A",
        "date": datetime(2026, 8, 19),
        "subject": f"{EVENT_MARKER} Notice under Clause 8.4",
        "summary": f"{EXTRACTION_MARKER} derived summary",
        "description": f"{LINK_MARKER} supporting delay evidence",
        "full_content": "Delay notice under Clause 8.4 on 2026-08-19",
        "filename": "authority-chronology.pdf",
        "processing_status": "completed",
        "duplicate_status": "unique",
        "lifecycle_state": "active",
    }
    document.update(overrides)
    return document


async def _setup(
    db: _Database, *, selected_source_ids: list[str] | None = None
) -> tuple[ChronologyService, dict[str, Any]]:
    service = ChronologyService(db)
    chronology = await service.create_chronology(
        MatterChronologyCreate(
            organization_id="org-A",
            project_id="proj-A",
            title="Authority chronology",
            selected_source_ids=selected_source_ids or ["doc-A"],
        ),
        _user(),
    )
    # Creation audit is setup state, not document-derived publication.
    db.audit_events.documents.clear()
    return service, chronology


async def _extract_and_verify(
    service: ChronologyService, chronology_id: str
) -> dict[str, Any]:
    result = await service.extract_events(
        chronology_id, ChronologyExtractRequest(), _user()
    )
    if result["events"]:
        await service.verify_event(chronology_id, result["events"][0]["_id"], _user())
    return result


def _state(db: _Database, chronology_id: str) -> dict[str, Any]:
    collections = {
        "ai_extractions": db.ai_extractions.documents,
        "chronology_events": db.matter_chronology_events.documents,
        "revisions": db.matter_chronology_event_revisions.documents,
        "project_events": db.project_events.documents,
        "event_links": db.event_links.documents,
        "audits": db.audit_events.documents,
    }
    persisted = [row for rows in collections.values() for row in rows]
    chronology = next(
        row for row in db.matter_chronologies.documents if str(row.get("_id")) == chronology_id
    )
    statuses = {
        str(_value(row.get("verification_status") or row.get("status")))
        for name in ("chronology_events", "project_events", "event_links")
        for row in collections[name]
        if row.get("verification_status") is not None or row.get("status") is not None
    }
    return {
        **{name: len(rows) for name, rows in collections.items()},
        "extraction_marker": EXTRACTION_MARKER in repr(persisted),
        "event_marker": EVENT_MARKER in repr(persisted),
        "link_marker": LINK_MARKER in repr(persisted),
        "material_statuses": statuses,
        "summary_counts": deepcopy(chronology.get("summary_counts") or {}),
    }


STATIC_MATRIX = [
    ("clean", {}, True),
    ("operational_failed", {"processing_status": "failed"}, True),
    ("human_review", {"processing_status": "human_review_required"}, False),
    ("duplicate_status", {"duplicate_status": "duplicate"}, False),
    ("duplicate_lifecycle", {"lifecycle_state": "duplicate"}, False),
    ("deleted", {"lifecycle_state": "deleted"}, False),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "authority", "should_publish"),
    STATIC_MATRIX,
    ids=[case for case, _authority, _expected in STATIC_MATRIX],
)
async def test_static_document_authority_controls_all_chronology_derivatives(
    case: str, authority: dict[str, Any], should_publish: bool
) -> None:
    db = _Database([_document(**authority)])
    service, chronology = await _setup(db)

    result = await _extract_and_verify(service, chronology["_id"])
    state = _state(db, chronology["_id"])

    for collection in (
        "ai_extractions",
        "chronology_events",
        "revisions",
        "project_events",
        "event_links",
    ):
        assert (state[collection] > 0) is should_publish, (case, result, state)
    for marker in ("extraction_marker", "event_marker", "link_marker"):
        assert state[marker] is should_publish, (case, state)
    if should_publish:
        assert state["material_statuses"] >= {"verified", "open", "user_verified"}
        assert state["summary_counts"].get("total") == 1
    else:
        assert state["material_statuses"] == set()
        assert state["summary_counts"].get("total", 0) == 0


TOCTOU_MATRIX = [
    ("human_review", {"processing_status": "human_review_required"}),
    ("duplicate", {"duplicate_status": "duplicate"}),
    ("deleted", {"lifecycle_state": "deleted"}),
    ("missing", None),
]


def _transition(
    db: _Database, authority: dict[str, Any] | None
) -> Callable[[], None]:
    def apply() -> None:
        if authority is None:
            db.documents.documents.clear()
        else:
            db.documents.documents[0].update(deepcopy(authority))

    return apply


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "current_authority"),
    TOCTOU_MATRIX,
    ids=[case for case, _authority in TOCTOU_MATRIX],
)
async def test_extraction_rechecks_authority_after_document_selection(
    case: str, current_authority: dict[str, Any] | None
) -> None:
    db = _Database([_document()])
    service, chronology = await _setup(db)
    db.documents.after_next_collect = _transition(db, current_authority)

    result = await service.extract_events(
        chronology["_id"], ChronologyExtractRequest(), _user()
    )
    state = _state(db, chronology["_id"])

    assert result["events_created"] == 0, case
    assert state["ai_extractions"] == 0
    assert state["chronology_events"] == 0
    assert state["revisions"] == 0
    assert state["project_events"] == 0
    assert state["event_links"] == 0
    assert state["extraction_marker"] is False
    assert state["event_marker"] is False
    assert state["link_marker"] is False
    assert state["summary_counts"].get("total", 0) == 0


@pytest.mark.asyncio
async def test_stale_selected_document_without_canonical_source_fails_closed() -> None:
    db = _Database()
    service, chronology = await _setup(db)
    db.documents.next_find_snapshot = [_document()]

    result = await service.extract_events(
        chronology["_id"], ChronologyExtractRequest(), _user()
    )
    state = _state(db, chronology["_id"])

    assert result["documents_scanned"] == 1
    assert result["events_created"] == 0
    assert state["ai_extractions"] == 0
    assert state["chronology_events"] == 0
    assert state["revisions"] == 0
    assert state["extraction_marker"] is False
    assert state["event_marker"] is False


@pytest.mark.asyncio
async def test_manual_chronology_event_remains_independent_and_verifiable() -> None:
    db = _Database()
    service, chronology = await _setup(db, selected_source_ids=[])
    event = await service.create_event(
        MatterChronologyEventCreate(
            chronology_id=chronology["_id"],
            event_date=datetime(2026, 8, 19),
            title=MANUAL_MARKER,
            description=f"{MANUAL_MARKER} counsel-authored note",
            verification_status=ChronologyVerificationStatus.AI_SUGGESTED,
        ),
        _user(),
    )

    verified = await service.verify_event(chronology["_id"], event["_id"], _user())

    assert verified["verification_status"] == ChronologyVerificationStatus.VERIFIED
    assert MANUAL_MARKER in repr(db.matter_chronology_events.documents)
    assert MANUAL_MARKER in repr(db.project_events.documents)
    assert len(db.project_events.documents) == 1


@pytest.mark.asyncio
async def test_direct_document_provenance_cannot_self_authorize_event_creation() -> None:
    db = _Database([_document(processing_status="human_review_required")])
    service, chronology = await _setup(db)
    payload = MatterChronologyEventCreate(
        chronology_id=chronology["_id"],
        event_date=datetime(2026, 8, 19),
        title=EVENT_MARKER,
        description=LINK_MARKER,
        source_document_id="doc-A",
        source_spans=[{"page": 1, "text": EXTRACTION_MARKER}],
    )

    try:
        await service.create_event(payload, _user())
    except HTTPException:
        pass

    assert db.matter_chronology_events.documents == []
    assert db.matter_chronology_event_revisions.documents == []
    assert _state(db, chronology["_id"])["summary_counts"].get("total", 0) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "current_authority"),
    TOCTOU_MATRIX,
    ids=[f"verify-{case}" for case, _authority in TOCTOU_MATRIX],
)
async def test_verification_rechecks_authority_before_project_event_and_links(
    case: str, current_authority: dict[str, Any] | None
) -> None:
    db = _Database([_document()])
    service, chronology = await _setup(db)
    extracted = await service.extract_events(
        chronology["_id"], ChronologyExtractRequest(), _user()
    )
    event_id = extracted["events"][0]["_id"]
    _transition(db, current_authority)()
    before_revisions = len(db.matter_chronology_event_revisions.documents)

    try:
        await service.verify_event(chronology["_id"], event_id, _user())
    except HTTPException:
        pass

    stored = await db.matter_chronology_events.find_one({"_id": event_id})
    assert stored["verification_status"] == ChronologyVerificationStatus.AI_SUGGESTED, case
    assert len(db.matter_chronology_event_revisions.documents) == before_revisions
    assert db.project_events.documents == []
    assert db.event_links.documents == []
    assert {"open", "user_verified"}.isdisjoint(
        _state(db, chronology["_id"])["material_statuses"]
    )


@pytest.mark.asyncio
async def test_denied_reentry_creates_no_new_or_overwritten_derivatives() -> None:
    db = _Database([_document()])
    service, chronology = await _setup(db)
    await _extract_and_verify(service, chronology["_id"])
    before = _state(db, chronology["_id"])
    db.documents.documents[0].update(
        {
            "processing_status": "human_review_required",
            "subject": f"{EVENT_MARKER}-ATTEMPT-2",
            "summary": f"{EXTRACTION_MARKER}-ATTEMPT-2",
            "description": f"{LINK_MARKER}-ATTEMPT-2",
        }
    )

    result = await service.extract_events(
        chronology["_id"], ChronologyExtractRequest(), _user()
    )
    after = _state(db, chronology["_id"])

    assert result["events_created"] == 0
    for collection in (
        "ai_extractions",
        "chronology_events",
        "revisions",
        "project_events",
        "event_links",
    ):
        assert after[collection] == before[collection]
    assert "ATTEMPT-2" not in repr(
        db.ai_extractions.documents
        + db.matter_chronology_events.documents
        + db.matter_chronology_event_revisions.documents
        + db.project_events.documents
        + db.event_links.documents
        + db.audit_events.documents
    )


@pytest.mark.asyncio
async def test_document_selection_and_publication_remain_in_chronology_scope() -> None:
    foreign_marker = "CHRONOLOGY_FOREIGN_TENANT_20260819"
    db = _Database(
        [
            _document(),
            _document(
                document_id="doc-B",
                organization_id="org-B",
                project_id="proj-B",
                subject=foreign_marker,
                summary=foreign_marker,
                description=foreign_marker,
            ),
        ]
    )
    service, chronology = await _setup(db, selected_source_ids=["doc-A", "doc-B"])

    result = await _extract_and_verify(service, chronology["_id"])

    assert result["documents_scanned"] == 1
    assert len(db.matter_chronology_events.documents) == 1
    assert len(db.project_events.documents) == 1
    assert {row.get("organization_id") for row in db.project_events.documents} == {"org-A"}
    assert {row.get("project_id") for row in db.project_events.documents} == {"proj-A"}
    assert foreign_marker not in repr(
        db.ai_extractions.documents
        + db.matter_chronology_events.documents
        + db.project_events.documents
        + db.event_links.documents
        + db.audit_events.documents
    )


@pytest.mark.asyncio
async def test_mutation_neutralizing_current_authority_gate_restores_unsafe_publication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def always_authoritative(_service: ChronologyService, _document_id: str) -> bool:
        return True

    monkeypatch.setattr(
        ChronologyService,
        "_has_current_document_authority",
        always_authoritative,
    )
    db = _Database([_document(processing_status="human_review_required")])
    service, chronology = await _setup(db)

    result = await _extract_and_verify(service, chronology["_id"])
    state = _state(db, chronology["_id"])

    assert result["events_created"] == 1
    assert state["ai_extractions"] == 1
    assert state["chronology_events"] == 1
    assert state["revisions"] == 2
    assert state["project_events"] == 1
    assert state["event_links"] >= 1
    assert state["material_statuses"] >= {"verified", "open", "user_verified"}
    assert state["event_marker"] is True
    assert state["link_marker"] is True


# ---------------------------------------------------------------------------
# G-A15 / R8 — caller-supplied event scope authority
# ---------------------------------------------------------------------------
#
# Slice 11 above pins DOCUMENT authority on the chronology seam: which sources
# may become derived material. R8 is the other half of the same seam and was
# never covered — WHOSE TENANT the derived material lands in.
#
# `MatterChronologyEventCreate` inherits `organization_id` / `project_id` from
# `MatterChronologyEventBase`, so both are request-body fields, while
# `POST /api/chronologies/{id}/events` authorizes only the PARENT chronology.
# Resolving scope as `payload_value or chronology_value` let the caller win, and
# the caller's values flowed into `matter_chronology_events`, `project_events`,
# `event_links` and `audit_events` — visible to the foreign tenant through its
# own `build_scope_query`-derived `list_project_events` read.
#
# The invariant these pin: the AUTHORIZED PARENT CHRONOLOGY defines the event's
# authority scope. Actor entitlement is not parent identity — a globally
# entitled actor may act on this chronology, not relocate its events.
#
# Classification of the five caller-settable scope-shaped fields, read from
# `PolicyService.authorize_document` (`policy_service.py:281-295`), which
# resolves authority from `organization_id` / `project_id` and nothing else:
#
#   organization_id, project_id      -> AUTHORITY. Pinned here.
#   contract_id, matter_id, claim_id -> METADATA. No gate reads them; they are
#       list filters, and `MatterChronologyUpdate` already lets a chronology
#       change all three under an org/project-only gate. Deliberately NOT
#       restricted — `test_caller_metadata_fields_do_not_redirect_event_authority`
#       pins that classification so a later change has to face it.

SCOPE_MARKER = "CHRONOLOGY_FOREIGN_SCOPE_EVENT_20260901"


class _ScopedPolicy:
    """A policy double that authorizes the RESOURCE HANDED TO IT and nothing else.

    Deliberately mirrors `PolicyService.authorize_document`: it reads the
    resource's own `organization_id` / `project_id`. That is the whole point of
    R8 — the route hands it the chronology, so nothing in the request body is
    ever authorized, however authoritative the field name looks.
    """

    def __init__(
        self,
        organization_id: str = "org-A",
        project_ids: tuple[str, ...] = ("proj-A",),
    ) -> None:
        self.organization_id = organization_id
        self.project_ids = project_ids
        self.authorized: list[tuple[str, Any, Any]] = []

    async def authorize_document(
        self,
        current_user: Any,
        permission: str,
        document: Any,
        *,
        resource_type: str = "document",
    ) -> None:
        organization_id = (document or {}).get("organization_id")
        project_id = (document or {}).get("project_id")
        if organization_id != self.organization_id or (
            project_id is not None and project_id not in self.project_ids
        ):
            raise HTTPException(status_code=403, detail="out of scope")
        self.authorized.append((permission, organization_id, project_id))


class _GlobalPolicy(_ScopedPolicy):
    """A globally entitled actor: every organisation and project authorizes."""

    async def authorize_document(
        self,
        current_user: Any,
        permission: str,
        document: Any,
        *,
        resource_type: str = "document",
    ) -> None:
        self.authorized.append(
            (
                permission,
                (document or {}).get("organization_id"),
                (document or {}).get("project_id"),
            )
        )


def _global_user() -> SimpleNamespace:
    return SimpleNamespace(id="global-user", organization_id="org-B", roles=["superadmin"])


def _scope_payload(chronology_id: str, **overrides: Any) -> MatterChronologyEventCreate:
    fields: dict[str, Any] = {
        "chronology_id": chronology_id,
        "event_date": datetime(2026, 9, 1),
        "title": SCOPE_MARKER,
        "description": f"{SCOPE_MARKER} injected narrative",
        "source_document_id": "doc-A",
        "source_spans": [{"page": 1, "text": SCOPE_MARKER}],
        "contract_clauses": ["8.4"],
    }
    fields.update(overrides)
    return MatterChronologyEventCreate(**fields)


def _persisted(db: _Database) -> list[dict[str, Any]]:
    """Every durable row this seam can reach, across all stores."""
    return (
        db.matter_chronology_events.documents
        + db.matter_chronology_event_revisions.documents
        + db.project_events.documents
        + db.event_links.documents
        + db.audit_events.documents
        + db.ai_extractions.documents
    )


def _foreign_scoped_rows(db: _Database) -> list[dict[str, Any]]:
    """Rows persisted under any tenant other than the authorized org-A/proj-A."""
    foreign = []
    for row in _persisted(db):
        organization_id = row.get("organization_id")
        project_id = row.get("project_id")
        if organization_id not in (None, "org-A") or project_id not in (None, "proj-A"):
            foreign.append(row)
    return foreign


async def _create_through_route(
    db: _Database,
    chronology_id: str,
    payload: MatterChronologyEventCreate,
    *,
    policy: Any,
    user: Any,
) -> dict[str, Any]:
    from rbac_backend.routers import chronology as chronology_router

    created = await chronology_router.create_chronology_event(
        chronology_id=chronology_id,
        payload=payload,
        db=db,
        current_user=user,
        policy=policy,
    )
    return created.model_dump(by_alias=True)


async def _verify_through_route(
    db: _Database, chronology_id: str, event_id: str, *, policy: Any, user: Any
) -> dict[str, Any]:
    from rbac_backend.routers import chronology as chronology_router

    verified = await chronology_router.verify_chronology_event(
        chronology_id=chronology_id,
        event_id=event_id,
        payload=None,
        db=db,
        current_user=user,
        policy=policy,
    )
    return verified.model_dump(by_alias=True)


async def _foreign_project_events(db: _Database) -> list[dict[str, Any]]:
    """Exactly the query `routers/evidence_graph.py` builds for an org-B caller."""
    from rbac_backend.services.evidence_graph_service import EvidenceGraphService

    return await EvidenceGraphService(db).list_project_events({"organization_id": "org-B"})


SCOPE_INJECTION_MATRIX = [
    ("foreign_organization", {"organization_id": "org-B", "project_id": "proj-B"}),
    ("foreign_organization_only", {"organization_id": "org-B"}),
    ("sibling_project", {"organization_id": "org-A", "project_id": "proj-B"}),
    ("sibling_project_only", {"project_id": "proj-B"}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "injected"),
    SCOPE_INJECTION_MATRIX,
    ids=[case for case, _injected in SCOPE_INJECTION_MATRIX],
)
async def test_a_caller_supplied_event_scope_cannot_leave_the_authorized_chronology(
    case: str, injected: dict[str, Any]
) -> None:
    db = _Database([_document()])
    service, chronology = await _setup(db)
    policy = _ScopedPolicy()

    with pytest.raises(HTTPException) as refusal:
        await _create_through_route(
            db,
            chronology["_id"],
            _scope_payload(chronology["_id"], **injected),
            policy=policy,
            user=_user(),
        )

    # A scope refusal keeps its own status: the client turns 401 into a forced
    # logout and 403 into a toast, so a masked 401 here would log the user out.
    assert refusal.value.status_code == 403, case
    assert _foreign_scoped_rows(db) == [], case
    assert SCOPE_MARKER not in repr(_persisted(db)), case
    assert db.matter_chronology_events.documents == [], case
    assert db.project_events.documents == [], case
    assert db.event_links.documents == [], case
    assert await _foreign_project_events(db) == [], case
    # The parent chronology was authorized; the payload's scope never was.
    assert policy.authorized == [("dms.chronology.edit", "org-A", "proj-A")], case


@pytest.mark.asyncio
async def test_an_event_omitting_scope_inherits_the_authorized_chronology_scope() -> None:
    db = _Database([_document()])
    service, chronology = await _setup(db)

    created = await _create_through_route(
        db,
        chronology["_id"],
        _scope_payload(chronology["_id"]),
        policy=_ScopedPolicy(),
        user=_user(),
    )

    assert created["organization_id"] == "org-A"
    assert created["project_id"] == "proj-A"
    assert len(db.matter_chronology_events.documents) == 1
    assert SCOPE_MARKER in repr(db.matter_chronology_events.documents)
    assert _foreign_scoped_rows(db) == []


@pytest.mark.asyncio
async def test_an_event_supplying_the_chronologys_own_scope_is_still_accepted() -> None:
    db = _Database([_document()])
    service, chronology = await _setup(db)

    created = await _create_through_route(
        db,
        chronology["_id"],
        _scope_payload(chronology["_id"], organization_id="org-A", project_id="proj-A"),
        policy=_ScopedPolicy(),
        user=_user(),
    )

    assert created["organization_id"] == "org-A"
    assert created["project_id"] == "proj-A"
    assert len(db.matter_chronology_events.documents) == 1
    assert _foreign_scoped_rows(db) == []


@pytest.mark.asyncio
async def test_caller_metadata_fields_do_not_redirect_event_authority() -> None:
    """`contract_id` / `matter_id` / `claim_id` are metadata, and stay writable.

    Nothing authorizes on them, so restricting them would be over-fitting. What
    must hold is that setting them cannot move the event's AUTHORITY scope.
    """
    db = _Database([_document()])
    service, chronology = await _setup(db)

    created = await _create_through_route(
        db,
        chronology["_id"],
        _scope_payload(
            chronology["_id"],
            contract_id="contract-B",
            matter_id="matter-B",
            claim_id="claim-B",
        ),
        policy=_ScopedPolicy(),
        user=_user(),
    )
    verified = await _verify_through_route(
        db, chronology["_id"], created["_id"], policy=_ScopedPolicy(), user=_user()
    )

    assert created["contract_id"] == "contract-B"
    assert created["matter_id"] == "matter-B"
    assert created["claim_id"] == "claim-B"
    assert verified["organization_id"] == "org-A"
    assert verified["project_id"] == "proj-A"
    assert _foreign_scoped_rows(db) == []
    assert await _foreign_project_events(db) == []


@pytest.mark.asyncio
async def test_verified_event_publishes_downstream_state_only_under_the_parent_scope() -> None:
    db = _Database([_document()])
    service, chronology = await _setup(db)
    created = await _create_through_route(
        db,
        chronology["_id"],
        _scope_payload(chronology["_id"]),
        policy=_ScopedPolicy(),
        user=_user(),
    )

    await _verify_through_route(
        db, chronology["_id"], created["_id"], policy=_ScopedPolicy(), user=_user()
    )

    assert len(db.project_events.documents) == 1
    assert len(db.event_links.documents) >= 1
    assert len(db.audit_events.documents) >= 1
    for store in ("project_events", "event_links", "audit_events"):
        rows = getattr(db, store).documents
        assert {row.get("organization_id") for row in rows} == {"org-A"}, store
        assert {row.get("project_id") for row in rows} == {"proj-A"}, store
    assert _foreign_scoped_rows(db) == []
    assert await _foreign_project_events(db) == []


@pytest.mark.asyncio
async def test_a_pre_existing_foreign_scoped_event_row_cannot_publish_downstream() -> None:
    """Legacy containment: a row already carrying foreign scope, verified now.

    The create seam refuses new contamination, but rows written before it
    existed are still verifiable. Authority is the parent chronology, so
    verification must re-anchor them rather than propagate what is stored.
    """
    db = _Database([_document()])
    service, chronology = await _setup(db)
    await db.matter_chronology_events.insert_one(
        {
            "_id": "legacy-event",
            "chronology_id": chronology["_id"],
            "organization_id": "org-B",
            "project_id": "proj-B",
            "title": SCOPE_MARKER,
            "description": f"{SCOPE_MARKER} legacy row",
            "event_date": datetime(2026, 9, 1),
            "source_document_id": "doc-A",
            "verification_status": ChronologyVerificationStatus.AI_SUGGESTED.value,
        }
    )

    await _verify_through_route(
        db, chronology["_id"], "legacy-event", policy=_ScopedPolicy(), user=_user()
    )

    assert {row.get("organization_id") for row in db.project_events.documents} == {"org-A"}
    assert {row.get("project_id") for row in db.project_events.documents} == {"proj-A"}
    assert {row.get("organization_id") for row in db.event_links.documents} == {"org-A"}
    assert {row.get("organization_id") for row in db.audit_events.documents} == {"org-A"}
    assert await _foreign_project_events(db) == []
    stored = await db.matter_chronology_events.find_one({"_id": "legacy-event"})
    assert stored["organization_id"] == "org-A"
    assert stored["project_id"] == "proj-A"


@pytest.mark.asyncio
async def test_a_global_actor_cannot_relocate_an_event_out_of_its_parent_chronology() -> None:
    """Actor entitlement is not parent-resource identity.

    A Super Admin legitimately holds org-B. That entitles them to act in org-B;
    it does not make an org-A chronology's event an org-B event. Nothing in the
    API supports relocation, so the authorized parent still decides.
    """
    db = _Database([_document()])
    service, chronology = await _setup(db)
    policy = _GlobalPolicy()

    with pytest.raises(HTTPException) as refusal:
        await _create_through_route(
            db,
            chronology["_id"],
            _scope_payload(chronology["_id"], organization_id="org-B", project_id="proj-B"),
            policy=policy,
            user=_global_user(),
        )

    assert refusal.value.status_code == 403
    assert db.matter_chronology_events.documents == []
    assert await _foreign_project_events(db) == []
    assert SCOPE_MARKER not in repr(_persisted(db))


@pytest.mark.asyncio
async def test_a_global_actor_may_still_create_an_event_in_the_parent_scope() -> None:
    db = _Database([_document()])
    service, chronology = await _setup(db)

    created = await _create_through_route(
        db,
        chronology["_id"],
        _scope_payload(chronology["_id"]),
        policy=_GlobalPolicy(),
        user=_global_user(),
    )

    assert created["organization_id"] == "org-A"
    assert created["project_id"] == "proj-A"
    assert _foreign_scoped_rows(db) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "injected"),
    SCOPE_INJECTION_MATRIX,
    ids=[f"direct-{case}" for case, _injected in SCOPE_INJECTION_MATRIX],
)
async def test_direct_service_create_event_cannot_be_handed_foreign_scope(
    case: str, injected: dict[str, Any]
) -> None:
    """The service is the seam, not the router.

    Route-only validation would leave every other caller — and every future one
    — unprotected, so the refusal has to hold with no router in the picture.
    """
    db = _Database([_document()])
    service, chronology = await _setup(db)

    with pytest.raises(HTTPException) as refusal:
        await service.create_event(_scope_payload(chronology["_id"], **injected), _user())

    assert refusal.value.status_code == 403, case
    assert _foreign_scoped_rows(db) == [], case
    assert db.matter_chronology_events.documents == [], case
    assert SCOPE_MARKER not in repr(_persisted(db)), case


@pytest.mark.asyncio
async def test_the_extraction_path_still_publishes_under_the_chronology_scope() -> None:
    """The internal caller supplies the chronology's own scope; it must survive."""
    db = _Database([_document()])
    service, chronology = await _setup(db)

    result = await _extract_and_verify(service, chronology["_id"])

    assert result["events_created"] == 1
    assert len(db.matter_chronology_events.documents) == 1
    assert len(db.project_events.documents) == 1
    assert _foreign_scoped_rows(db) == []


def _caller_wins(
    _service: ChronologyService, data: dict[str, Any], chronology: dict[str, Any]
) -> None:
    """The pre-fix `payload_value or chronology_value` resolution, restored."""
    for field in ("organization_id", "project_id"):
        data[field] = data.get(field) or chronology.get(field)


async def _row_scope(
    _service: ChronologyService, event: dict[str, Any]
) -> tuple[Any, Any]:
    """The pre-fix publication scope: whatever the stored row happens to carry."""
    return event.get("organization_id"), event.get("project_id")


@pytest.mark.asyncio
async def test_mutation_restoring_caller_wins_scope_resolution_reopens_foreign_injection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MUTATION A, half one, made permanent: the create seam.

    Restore `payload_value or chronology_value` and the foreign-scoped
    `matter_chronology_events` row — and its revision envelope — come straight
    back. A guard nothing can break is not a guard.
    """
    monkeypatch.setattr(ChronologyService, "_apply_chronology_authority_scope", _caller_wins)
    db = _Database([_document()])
    service, chronology = await _setup(db)

    created = await _create_through_route(
        db,
        chronology["_id"],
        _scope_payload(chronology["_id"], organization_id="org-B", project_id="proj-B"),
        policy=_ScopedPolicy(),
        user=_user(),
    )

    assert created["organization_id"] == "org-B"
    assert created["project_id"] == "proj-B"
    assert _foreign_scoped_rows(db) != []
    assert SCOPE_MARKER in repr(db.matter_chronology_events.documents)


@pytest.mark.asyncio
async def test_mutation_publishing_from_the_stored_row_reopens_foreign_project_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MUTATION A, half two, made permanent: the publication seam.

    Neutralize both halves and the reproduced defect returns whole — the
    injected governance artefact is persisted into `project_events`,
    `event_links` and `audit_events` under org-B, and comes back out of the
    FOREIGN tenant's own `list_project_events` read. This is the assertion that
    makes the fix's second half load-bearing rather than decorative: with only
    the create seam restored, verification still re-anchors the row.
    """
    monkeypatch.setattr(ChronologyService, "_apply_chronology_authority_scope", _caller_wins)
    monkeypatch.setattr(ChronologyService, "_event_publication_scope", _row_scope)
    db = _Database([_document()])
    service, chronology = await _setup(db)
    created = await _create_through_route(
        db,
        chronology["_id"],
        _scope_payload(chronology["_id"], organization_id="org-B", project_id="proj-B"),
        policy=_ScopedPolicy(),
        user=_user(),
    )

    await _verify_through_route(
        db, chronology["_id"], created["_id"], policy=_ScopedPolicy(), user=_user()
    )

    foreign = await _foreign_project_events(db)
    assert len(foreign) == 1
    assert foreign[0].get("title") == SCOPE_MARKER
    assert {row.get("organization_id") for row in db.project_events.documents} == {"org-B"}
    assert {row.get("organization_id") for row in db.event_links.documents} == {"org-B"}
    assert "org-B" in repr(db.audit_events.documents)
