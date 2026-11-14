"""G31 authority regressions for the evidence-graph backfill writer.

The public service seam snapshots source candidates before it writes durable
project events, verified links, and their audit payloads.  These tests make the
selection/write interval deterministic and assert the final Mongo state.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Callable, Iterable

import pytest

from rbac_backend.services import evidence_graph_backfill_service as backfill_module
from rbac_backend.core.permissions import Permissions
from rbac_backend.routers.evidence_graph import evidence_graph_backfill
from rbac_backend.services.evidence_graph_backfill_service import (
    EvidenceGraphBackfillService,
)


TEXT_MARKER = "EVIDENCE_BACKFILL_BLOCKED_TEXT_20260819"
LINK_MARKER = "EVIDENCE_BACKFILL_BLOCKED_LINK_20260819"


def _value(value: Any) -> Any:
    return getattr(value, "value", value)


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, expected in (query or {}).items():
        actual: Any = document
        for part in key.split("."):
            actual = actual.get(part) if isinstance(actual, dict) else None
        actual = _value(actual)
        if isinstance(expected, dict):
            if "$in" in expected and not any(
                str(actual) == str(_value(candidate))
                for candidate in expected["$in"]
            ):
                return False
            if "$ne" in expected and str(actual) == str(_value(expected["$ne"])):
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

    def sort(self, key: str, direction: int = 1) -> "_Cursor":
        self.documents.sort(
            key=lambda row: row.get(key) or datetime.min,
            reverse=direction == -1,
        )
        return self

    def skip(self, amount: int) -> "_Cursor":
        self.documents = self.documents[amount:]
        return self

    def limit(self, amount: int) -> "_Cursor":
        self.documents = self.documents[:amount]
        return self

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        documents = self.documents if length is None else self.documents[:length]
        if self.after_collect is not None:
            callback, self.after_collect = self.after_collect, None
            callback()
        return deepcopy(documents)


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

    async def find_one(self, query: dict[str, Any]):
        for document in self.documents:
            if _matches(document, query):
                return deepcopy(document)
        return None

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

    async def update_one(
        self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False
    ):
        for document in self.documents:
            if _matches(document, query):
                document.update(deepcopy(update.get("$set", {})))
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)


class _Database:
    SOURCE_COLLECTIONS = (
        "documents",
        "claims",
        "key_date_milestones",
        "variations",
        "bank_guarantees",
        "ipc_bills",
        "drawing_references",
        "delay_events",
        "programme_milestones",
    )

    def __init__(self, documents: Iterable[dict[str, Any]] = ()) -> None:
        self._collections = {
            name: _Collection(documents if name == "documents" else ())
            for name in self.SOURCE_COLLECTIONS
        }
        for name in (
            "project_events",
            "event_links",
            "ai_extractions",
            "audit_events",
        ):
            self._collections[name] = _Collection()

    def __getitem__(self, name: str) -> _Collection:
        return self._collections.setdefault(name, _Collection())

    def __getattr__(self, name: str) -> _Collection:
        try:
            return self[name]
        except KeyError as exc:  # pragma: no cover - normal mapping never raises
            raise AttributeError(name) from exc


def _user() -> SimpleNamespace:
    return SimpleNamespace(id="backfill-user", organization_id="org-A")


def _document(document_id: str = LINK_MARKER, **overrides: Any) -> dict[str, Any]:
    document = {
        "_id": document_id,
        "organization_id": "org-A",
        "project_id": "proj-A",
        "subject": f"{TEXT_MARKER} authoritative subject",
        "description": f"{TEXT_MARKER} authoritative evidence",
        "date": datetime(2026, 8, 19),
        "processing_status": "completed",
        "duplicate_status": "unique",
        "lifecycle_state": "active",
    }
    document.update(overrides)
    return document


async def _run(db: _Database) -> dict[str, Any]:
    return await EvidenceGraphBackfillService(db).run(
        {"organization_id": "org-A"},
        current_user=_user(),
        dry_run=False,
        project_id="proj-A",
    )


def _physical_state(db: _Database) -> dict[str, Any]:
    events = deepcopy(db.project_events.documents)
    links = deepcopy(db.event_links.documents)
    extractions = deepcopy(db.ai_extractions.documents)
    audits = deepcopy(db.audit_events.documents)
    persisted = events + links + extractions + audits
    statuses = {
        str(_value(row.get("status")))
        for row in events + links + extractions
        if row.get("status") is not None
    }
    return {
        "events": len(events),
        "links": len(links),
        "extractions": len(extractions),
        "audits": len(audits),
        "text_marker": TEXT_MARKER in repr(persisted),
        "link_marker": LINK_MARKER in repr(persisted),
        "material_statuses": statuses,
        "tenant_pairs": {
            (row.get("organization_id"), row.get("project_id"))
            for row in events + links
        },
    }


STATIC_MATRIX = [
    ("clean", {}, True),
    ("operational_failed", {"processing_status": "failed"}, True),
    (
        "human_review",
        {"processing_status": "human_review_required"},
        False,
    ),
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
async def test_static_authority_controls_final_backfill_state(
    case: str, authority: dict[str, Any], should_publish: bool
) -> None:
    db = _Database([_document(**authority)])

    report = await _run(db)
    state = _physical_state(db)

    assert (state["events"] == 1) is should_publish, (case, report, state)
    assert (state["links"] == 1) is should_publish, (case, report, state)
    assert (state["audits"] == 2) is should_publish, (case, report, state)
    assert state["extractions"] == 0
    assert state["text_marker"] is should_publish, (case, state)
    assert state["link_marker"] is should_publish, (case, state)
    if should_publish:
        assert state["material_statuses"] == {"open", "user_verified"}
    else:
        assert state["material_statuses"] == set()


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
async def test_backfill_rechecks_current_authority_after_candidate_selection(
    case: str, current_authority: dict[str, Any] | None
) -> None:
    db = _Database([_document()])
    db.documents.after_next_collect = _transition(db, current_authority)

    report = await _run(db)

    assert _physical_state(db) == {
        "events": 0,
        "links": 0,
        "extractions": 0,
        "audits": 0,
        "text_marker": False,
        "link_marker": False,
        "material_statuses": set(),
        "tenant_pairs": set(),
    }, (case, report)


@pytest.mark.asyncio
async def test_stale_candidate_without_canonical_provenance_fails_closed() -> None:
    db = _Database()
    db.documents.next_find_snapshot = [_document()]

    report = await _run(db)

    assert _physical_state(db)["events"] == 0, report
    assert _physical_state(db)["links"] == 0, report
    assert _physical_state(db)["audits"] == 0, report
    assert _physical_state(db)["text_marker"] is False
    assert _physical_state(db)["link_marker"] is False


@pytest.mark.asyncio
async def test_denied_reentry_preserves_last_known_good_without_new_publication() -> None:
    db = _Database([_document()])
    first = await _run(db)
    before = _physical_state(db)
    db.documents.documents[0].update(
        {
            "processing_status": "human_review_required",
            "subject": f"{TEXT_MARKER}-ATTEMPT-2",
            "description": f"{TEXT_MARKER}-ATTEMPT-2",
        }
    )

    second = await _run(db)
    after = _physical_state(db)

    assert first["created_project_events"] == 1
    assert second["created_project_events"] == 0
    assert second["created_event_links"] == 0
    assert after == before
    assert f"{TEXT_MARKER}-ATTEMPT-2" not in repr(
        db.project_events.documents
        + db.event_links.documents
        + db.audit_events.documents
    )


@pytest.mark.asyncio
async def test_backfill_candidate_and_writes_remain_in_caller_scope() -> None:
    foreign_text = f"{TEXT_MARKER}-FOREIGN"
    foreign_id = f"{LINK_MARKER}-FOREIGN"
    db = _Database(
        [
            _document(document_id="local-document"),
            _document(
                document_id=foreign_id,
                organization_id="org-B",
                project_id="proj-B",
                subject=foreign_text,
                description=foreign_text,
            ),
        ]
    )

    report = await _run(db)
    state = _physical_state(db)

    assert report["sources"]["documents"]["scanned"] == 1
    assert state["events"] == 1
    assert state["links"] == 1
    assert state["tenant_pairs"] == {("org-A", "proj-A")}
    assert foreign_text not in repr(db.project_events.documents + db.audit_events.documents)
    assert foreign_id not in repr(db.event_links.documents + db.audit_events.documents)


@pytest.mark.asyncio
async def test_public_backfill_route_authorizes_manage_and_preserves_scope() -> None:
    class _Policy:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def authorize(self, _user: Any, permission: Any, **scope: Any) -> None:
            self.calls.append({"permission": permission, **scope})

    db = _Database(
        [
            _document(document_id="route-local"),
            _document(
                document_id="route-foreign",
                organization_id="org-B",
                project_id="proj-B",
            ),
        ]
    )
    user = SimpleNamespace(
        id="route-user",
        email="route@example.com",
        organization_id="org-A",
        roles=["orgadmin"],
        projects=["proj-A"],
        organizations=[],
    )
    policy = _Policy()

    report = await evidence_graph_backfill(
        organization_id="org-A",
        project_id="proj-A",
        dry_run=False,
        limit_per_collection=500,
        db=db,
        current_user=user,
        policy=policy,
    )

    assert policy.calls == [
        {
            "permission": Permissions.EVIDENCE_GRAPH_MANAGE,
            "resource_type": "evidence_graph_backfill",
            "organization_id": "org-A",
            "project_id": "proj-A",
        }
    ]
    assert report["sources"]["documents"]["scanned"] == 1
    assert _physical_state(db)["tenant_pairs"] == {("org-A", "proj-A")}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "current_authority"),
    TOCTOU_MATRIX,
    ids=[f"neutralized-{case}" for case, _authority in TOCTOU_MATRIX],
)
async def test_transition_artifacts_return_when_only_current_gate_is_neutralized(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    current_authority: dict[str, Any] | None,
) -> None:
    db = _Database([_document()])
    db.documents.after_next_collect = _transition(db, current_authority)

    async def stale_allow(_db: Any, document_id: str):
        return SimpleNamespace(
            document_id=document_id,
            consumable=True,
            reason="neutralized_current_authority_gate",
        )

    # Mutation proof: selection, scope, idempotency and graph-writer behavior
    # remain real; only the new current-canonical authority decision is removed.
    monkeypatch.setattr(backfill_module, "resolve_document_authority", stale_allow)

    report = await _run(db)
    state = _physical_state(db)

    assert report["created_project_events"] == 1, case
    assert report["created_event_links"] == 1, case
    assert state["events"] == 1
    assert state["links"] == 1
    assert state["audits"] == 2
    assert state["text_marker"] is True
    assert state["link_marker"] is True
    assert state["material_statuses"] == {"open", "user_verified"}


@pytest.mark.asyncio
async def test_reentry_is_idempotent_even_when_current_gate_is_neutralized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _Database([_document()])
    await _run(db)
    before = _physical_state(db)
    db.documents.documents[0]["processing_status"] = "human_review_required"

    async def stale_allow(_db: Any, document_id: str):
        return SimpleNamespace(document_id=document_id, consumable=True, reason="neutralized")

    monkeypatch.setattr(backfill_module, "resolve_document_authority", stale_allow)
    report = await _run(db)

    assert report["created_project_events"] == 0
    assert report["created_event_links"] == 0
    assert _physical_state(db) == before
