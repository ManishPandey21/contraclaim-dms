"""G31 authority for document links stored by EvidenceRegisterService.

The mounted route handlers and real service are exercised through persistent
in-test Mongo boundaries. Assertions inspect final register, audit, project
event, and event-link state rather than collaborator calls.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from rbac_backend.core.tenant_context import ActiveScope
from rbac_backend.models.evidence_graph import EvidenceEntityType
from rbac_backend.models.evidence_registers import DelayEventCreate, DelayEventUpdate
from rbac_backend.routers import evidence_registers as evidence_register_routes
from rbac_backend.services import evidence_register_service as register_module
from rbac_backend.services import publication_policy as publication_policy_module


class _Cursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = [deepcopy(row) for row in rows]

    def sort(self, key: str, direction: int = 1):
        self.rows.sort(
            key=lambda row: row.get(key) or datetime.min,
            reverse=direction == -1,
        )
        return self

    def skip(self, amount: int):
        self.rows = self.rows[amount:]
        return self

    def limit(self, amount: int):
        self.rows = self.rows[:amount]
        return self

    def __aiter__(self):
        async def generate():
            for row in self.rows:
                yield deepcopy(row)

        return generate()


def _same(left: Any, right: Any) -> bool:
    return str(left) == str(right)


def _matches(row: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, expected in (query or {}).items():
        actual: Any = row
        for part in key.split("."):
            actual = actual.get(part) if isinstance(actual, dict) else None
        if isinstance(expected, dict) and "$in" in expected:
            if not any(_same(actual, candidate) for candidate in expected["$in"]):
                return False
        elif not _same(actual, expected):
            return False
    return True


class _Collection:
    def __init__(self) -> None:
        self.documents: dict[str, dict[str, Any]] = {}
        self.before_next_write: Callable[[], None] | None = None

    def _transition(self) -> None:
        if self.before_next_write is not None:
            callback, self.before_next_write = self.before_next_write, None
            callback()

    async def insert_one(self, row: dict[str, Any]):
        self._transition()
        stored = deepcopy(row)
        record_id = str(stored.get("_id") or f"id-{len(self.documents) + 1}")
        stored["_id"] = record_id
        self.documents[record_id] = stored
        return SimpleNamespace(inserted_id=record_id)

    async def find_one(self, query: dict[str, Any], *_args, **_kwargs):
        for row in self.documents.values():
            if _matches(row, query):
                return deepcopy(row)
        return None

    async def find_one_and_update(
        self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False, **_kwargs
    ):
        self._transition()
        for record_id, row in self.documents.items():
            if _matches(row, query):
                row.update(deepcopy(update.get("$set", {})))
                for field, amount in update.get("$inc", {}).items():
                    row[field] = int(row.get(field) or 0) + int(amount)
                self.documents[record_id] = row
                return deepcopy(row)
        if upsert:
            # Reference counters (`delay_event_reference_counters`) upsert on _id.
            row = {key: value for key, value in query.items() if not key.startswith("$")}
            row.update(deepcopy(update.get("$set", {})))
            for field, amount in update.get("$inc", {}).items():
                row[field] = int(amount)
            self.documents[str(row["_id"])] = row
            return deepcopy(row)
        return None

    def find(self, query: dict[str, Any]):
        return _Cursor(
            [row for row in self.documents.values() if _matches(row, query)]
        )


class _Database:
    def __init__(self) -> None:
        self._collections: dict[str, _Collection] = {}
        for name in (
            "audit_events",
            "project_events",
            "event_links",
            "drawing_references",
            "delay_events",
            "programme_milestones",
            "documents",
        ):
            self._collections[name] = _Collection()
        # The register resolves the organisation from the owning project.
        self._collections["projects"] = _Collection()
        self._collections["projects"].documents["proj-A"] = {
            "_id": "proj-A",
            "organization_id": "org-A",
        }

    def __getitem__(self, name: str) -> _Collection:
        return self._collections.setdefault(name, _Collection())

    def __getattr__(self, name: str) -> _Collection:
        return self[name]


class _Policy:
    async def authorize(self, *_args, **_kwargs) -> None:
        return None

    async def authorize_document(self, *_args, **_kwargs) -> None:
        return None


def _user() -> SimpleNamespace:
    return SimpleNamespace(id="user-A", organization_id="org-A")


def _selection(db: Any) -> ActiveScope:
    """The navbar selection the routes now require (core/tenant_context.py)."""
    return ActiveScope(db, _user(), "org-A", "proj-A")


def _document(document_id: str, **overrides: Any) -> dict[str, Any]:
    row = {
        "_id": document_id,
        "organization_id": "org-A",
        "project_id": "proj-A",
        "processing_status": "completed",
        "duplicate_status": "unique",
        "lifecycle_state": "active",
        "subject": f"Document {document_id}",
    }
    row.update(overrides)
    return row


def _delay_payload(
    linked_document_ids: list[str],
    *,
    evidence_link_ids: list[str] | None = None,
) -> DelayEventCreate:
    return DelayEventCreate(
        organization_id="org-A",
        project_id="proj-A",
        delay_ref="DELAY-AUTH-20260820",
        title="Manual access delay record",
        description="User-managed delay analysis",
        start_date=datetime(2026, 8, 20),
        linked_document_ids=linked_document_ids,
        evidence_link_ids=evidence_link_ids or [],
    )


def _seed(db: _Database, row: dict[str, Any]) -> None:
    db.documents.documents[str(row["_id"])] = deepcopy(row)


async def _create_through_route(
    monkeypatch: pytest.MonkeyPatch,
    db: _Database,
    payload: DelayEventCreate,
):
    real_resolve = publication_policy_module.resolve_canonical_document

    async def resolve_after_transition(database: Any, document_id: Any):
        database.delay_events._transition()
        return await real_resolve(database, document_id)

    monkeypatch.setattr(
        register_module,
        "resolve_canonical_document",
        resolve_after_transition,
        raising=False,
    )
    return await evidence_register_routes.create_delay_event(
        payload,
        db=db,
        current_user=_user(),
        policy=_Policy(),
        selection=_selection(db),
    )


async def _update_through_route(
    monkeypatch: pytest.MonkeyPatch,
    db: _Database,
    item_id: str,
    payload: DelayEventUpdate,
):
    real_resolve = publication_policy_module.resolve_canonical_document

    async def resolve_after_transition(database: Any, document_id: Any):
        database.delay_events._transition()
        return await real_resolve(database, document_id)

    monkeypatch.setattr(
        register_module,
        "resolve_canonical_document",
        resolve_after_transition,
        raising=False,
    )
    return await evidence_register_routes.update_delay_event(
        item_id,
        payload,
        db=db,
        current_user=_user(),
        policy=_Policy(),
        selection=_selection(db),
    )


def _transition_document(
    db: _Database,
    document_id: str,
    authority: dict[str, Any] | None,
) -> Callable[[], None]:
    def apply() -> None:
        if authority is None:
            db.documents.documents.pop(document_id, None)
            return
        db.documents.documents[document_id].update(deepcopy(authority))

    return apply


def _register_audits(db: _Database) -> list[dict[str, Any]]:
    return [
        row
        for row in db.audit_events.documents.values()
        if str(row.get("action") or "").startswith("delay_events.")
    ]


@pytest.mark.asyncio
async def test_blocked_document_link_is_omitted_without_losing_manual_register(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _Database()
    _seed(
        db,
        _document("doc-blocked", processing_status="human_review_required"),
    )

    response = await _create_through_route(
        monkeypatch,
        db,
        _delay_payload(["doc-blocked"], evidence_link_ids=["manual-evidence-1"]),
    )

    stored = db.delay_events.documents[response.id]
    assert stored["title"] == "Manual access delay record"
    assert stored["evidence_link_ids"] == ["manual-evidence-1"]
    assert stored["linked_document_ids"] == []
    assert len(db.project_events.documents) == 1
    assert len(db.event_links.documents) == 1
    event_link = next(iter(db.event_links.documents.values()))
    assert event_link["target_type"] == EvidenceEntityType.DELAY_EVENT
    assert event_link["target_id"] == response.id
    register_audit = next(
        row
        for row in db.audit_events.documents.values()
        if row["action"] == "delay_events.created"
    )
    assert register_audit["after"]["linked_document_ids"] == []
    assert register_audit["after"]["title"] == "Manual access delay record"


@pytest.mark.asyncio
async def test_manual_register_without_document_remains_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _Database()

    response = await _create_through_route(
        monkeypatch,
        db,
        _delay_payload([], evidence_link_ids=["manual-fact-link"]),
    )

    stored = db.delay_events.documents[response.id]
    assert stored["linked_document_ids"] == []
    assert stored["evidence_link_ids"] == ["manual-fact-link"]
    assert stored["description"] == "User-managed delay analysis"
    assert len(db.project_events.documents) == 1
    assert len(db.event_links.documents) == 1


@pytest.mark.asyncio
async def test_blocked_document_link_is_absent_from_register_audit_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _Database()
    _seed(
        db,
        _document("doc-audit-blocked", lifecycle_state="deleted"),
    )

    await _create_through_route(
        monkeypatch, db, _delay_payload(["doc-audit-blocked"])
    )

    register_audit = _register_audits(db)[0]
    assert register_audit["after"]["linked_document_ids"] == []
    assert register_audit["after"]["description"] == "User-managed delay analysis"


@pytest.mark.asyncio
async def test_register_event_and_link_are_independent_of_blocked_document_support(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _Database()
    _seed(
        db,
        _document("doc-event-blocked", duplicate_status="duplicate"),
    )

    response = await _create_through_route(
        monkeypatch, db, _delay_payload(["doc-event-blocked"])
    )

    assert len(db.project_events.documents) == 1
    assert len(db.event_links.documents) == 1
    event = next(iter(db.project_events.documents.values()))
    link = next(iter(db.event_links.documents.values()))
    assert event["source_entity_type"] == EvidenceEntityType.DELAY_EVENT
    assert event["source_entity_id"] == response.id
    assert link["target_type"] == EvidenceEntityType.DELAY_EVENT
    assert link["target_id"] == response.id
    assert "doc-event-blocked" not in str(event)
    assert "doc-event-blocked" not in str(link)


STATIC_AUTHORITY_MATRIX = [
    ("clean", {}, True),
    ("operational_failed", {"processing_status": "failed"}, True),
    ("human_review", {"processing_status": "human_review_required"}, False),
    ("duplicate_status", {"duplicate_status": "duplicate"}, False),
    ("duplicate_lifecycle", {"lifecycle_state": "duplicate"}, False),
    ("deleted", {"lifecycle_state": "deleted"}, False),
    ("missing", None, False),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "authority", "expected_allowed"),
    STATIC_AUTHORITY_MATRIX,
    ids=[case for case, _authority, _expected in STATIC_AUTHORITY_MATRIX],
)
async def test_static_document_link_authority_is_reflected_in_final_mongo_state(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    authority: dict[str, Any] | None,
    expected_allowed: bool,
) -> None:
    db = _Database()
    if authority is not None:
        _seed(db, _document("doc-static", **authority))

    response = await _create_through_route(
        monkeypatch, db, _delay_payload(["doc-static"])
    )

    stored = db.delay_events.documents[response.id]
    assert stored["linked_document_ids"] == (
        ["doc-static"] if expected_allowed else []
    ), case
    assert stored["title"] == "Manual access delay record"
    register_audit = _register_audits(db)[0]
    assert register_audit["after"]["linked_document_ids"] == stored[
        "linked_document_ids"
    ]
    assert len(db.project_events.documents) == 1
    assert len(db.event_links.documents) == 1
    assert not any(
        link.get("target_type") == EvidenceEntityType.DOCUMENT
        for link in db.event_links.documents.values()
    )


TOCTOU_AUTHORITY_MATRIX = [
    ("human_review", {"processing_status": "human_review_required"}),
    ("duplicate", {"duplicate_status": "duplicate"}),
    ("deleted", {"lifecycle_state": "deleted"}),
    ("missing", None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "authority"),
    TOCTOU_AUTHORITY_MATRIX,
    ids=[case for case, _authority in TOCTOU_AUTHORITY_MATRIX],
)
async def test_document_authority_is_rechecked_immediately_before_register_write(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    authority: dict[str, Any] | None,
) -> None:
    db = _Database()
    _seed(db, _document("doc-transition"))
    db.delay_events.before_next_write = _transition_document(
        db, "doc-transition", authority
    )

    response = await _create_through_route(
        monkeypatch, db, _delay_payload(["doc-transition"])
    )

    stored = db.delay_events.documents[response.id]
    assert stored["linked_document_ids"] == [], case
    assert stored["title"] == "Manual access delay record"
    assert _register_audits(db)[0]["after"]["linked_document_ids"] == []
    assert len(db.project_events.documents) == 1
    assert len(db.event_links.documents) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("blocked_first", [False, True], ids=["a_clean", "b_clean"])
async def test_each_supporting_document_is_authorized_independently(
    monkeypatch: pytest.MonkeyPatch,
    blocked_first: bool,
) -> None:
    db = _Database()
    clean_id = "doc-B" if blocked_first else "doc-A"
    blocked_id = "doc-A" if blocked_first else "doc-B"
    _seed(db, _document(clean_id))
    _seed(
        db,
        _document(blocked_id, processing_status="human_review_required"),
    )
    requested = [blocked_id, clean_id, clean_id] if blocked_first else [clean_id, blocked_id, clean_id]

    response = await _create_through_route(
        monkeypatch, db, _delay_payload(requested)
    )

    stored = db.delay_events.documents[response.id]
    assert stored["linked_document_ids"] == [clean_id]
    assert _register_audits(db)[0]["after"]["linked_document_ids"] == [clean_id]


@pytest.mark.asyncio
async def test_denied_reentry_cannot_republish_document_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _Database()
    _seed(db, _document("doc-reentry"))
    created = await _create_through_route(
        monkeypatch, db, _delay_payload(["doc-reentry"])
    )
    assert db.delay_events.documents[created.id]["linked_document_ids"] == [
        "doc-reentry"
    ]
    db.delay_events.before_next_write = _transition_document(
        db,
        "doc-reentry",
        {"processing_status": "human_review_required"},
    )

    updated = await _update_through_route(
        monkeypatch,
        db,
        created.id,
        DelayEventUpdate(
            title="Manual analysis retained",
            linked_document_ids=["doc-reentry"],
        ),
    )

    stored = db.delay_events.documents[updated.id]
    assert stored["title"] == "Manual analysis retained"
    assert stored["linked_document_ids"] == []
    assert _register_audits(db)[-1]["after"]["linked_document_ids"] == []
    assert len(db.project_events.documents) == 1
    assert len(db.event_links.documents) == 1


@pytest.mark.asyncio
async def test_unlink_is_scoped_and_preserves_other_valid_supporter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _Database()
    _seed(db, _document("doc-A"))
    _seed(db, _document("doc-B"))
    created = await _create_through_route(
        monkeypatch, db, _delay_payload(["doc-A", "doc-B"])
    )

    updated = await _update_through_route(
        monkeypatch,
        db,
        created.id,
        DelayEventUpdate(linked_document_ids=["doc-B"]),
    )

    assert db.delay_events.documents[updated.id]["linked_document_ids"] == [
        "doc-B"
    ]
    assert _register_audits(db)[-1]["after"]["linked_document_ids"] == [
        "doc-B"
    ]
    assert len(db.project_events.documents) == 1
    assert len(db.event_links.documents) == 1


@pytest.mark.asyncio
async def test_foreign_scope_documents_cannot_authorize_local_register_link(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _Database()
    _seed(db, _document("doc-local"))
    _seed(db, _document("doc-foreign-org", organization_id="org-B"))
    _seed(db, _document("doc-foreign-project", project_id="proj-B"))

    response = await _create_through_route(
        monkeypatch,
        db,
        _delay_payload(
            ["doc-foreign-org", "doc-local", "doc-foreign-project"]
        ),
    )

    stored = db.delay_events.documents[response.id]
    assert stored["linked_document_ids"] == ["doc-local"]
    assert _register_audits(db)[0]["after"]["linked_document_ids"] == [
        "doc-local"
    ]


# ---------------------------------------------------------------------------
# G-A15 / R9(b) — supporter retraction at the READ boundary
# ---------------------------------------------------------------------------
#
# Every case above asserts at a WRITE boundary. None of them reads the register
# back after the world changes underneath it, and that gap was the defect:
# `_authorized_document_links` filtered `linked_document_ids` on create and on
# the ids present in an update payload, while `_get` and `_list` returned the
# stored list verbatim. A supporter consumable when it was linked therefore
# stayed served as current support after its canonical Document lost publication
# authority, and an unrelated PATCH re-emitted it into the audit `after`
# envelope — the exact shape
# `test_blocked_document_link_is_absent_from_register_audit_envelope` already
# treats as a violation when it arrives by write.
#
# The invariant: a HISTORICAL STORED ASSOCIATION is not a CURRENTLY SERVABLE
# one. Storage keeps the provenance; the served representation answers the
# authority question at serve time.
#
# Authority primitive, chosen deliberately. `_authorized_document_links` is a
# POSITIVE resolution and fails CLOSED — an id it cannot resolve is dropped.
# `publication_policy.blocked_document_ids` is SUBTRACTIVE and fails OPEN by
# design: it returns only ids positively resolved AND positively unusable, so an
# orphaned id survives subtraction. Serving a supporter whose document no longer
# resolves is precisely what must not happen here, so the read path reuses the
# positive primitive the write path already uses — one predicate, one place.
# `test_mutation_subtractive_link_filter_lets_an_orphan_supporter_survive` pins
# that choice.

#: Non-publishable canonical states, from `publication_policy.is_consumable`.
#: `processing_status="failed"` is deliberately absent: it is OPERATIONAL, not a
#: verdict about the content, and under the last-known-good lifecycle it does
#: not retract a previous publication.
RETRACTING_STATES = [
    ("human_review", {"processing_status": "human_review_required"}),
    ("duplicate_status", {"duplicate_status": "duplicate"}),
    ("duplicate_lifecycle", {"lifecycle_state": "duplicate"}),
    ("deleted", {"lifecycle_state": "deleted"}),
]


async def _get_through_route(db: _Database, item_id: str):
    return await evidence_register_routes.get_delay_event(
        item_id, db=db, current_user=_user(), policy=_Policy(), selection=_selection(db)
    )


async def _list_delay_events(db: _Database) -> list[dict[str, Any]]:
    """The service list, under the scope filter the route builds for org-A.

    `build_scope_query` is exercised by the RBAC suites; what this needs is the
    read projection underneath it, so the scope filter is supplied directly.
    """
    return await register_module.EvidenceRegisterService(db).list_delay_events(
        {"organization_id": "org-A"}
    )


async def _seed_two_supporters(
    monkeypatch: pytest.MonkeyPatch, db: _Database
) -> Any:
    _seed(db, _document("doc-support"))
    _seed(db, _document("doc-keep"))
    created = await _create_through_route(
        monkeypatch, db, _delay_payload(["doc-support", "doc-keep"])
    )
    assert db.delay_events.documents[created.id]["linked_document_ids"] == [
        "doc-support",
        "doc-keep",
    ]
    return created


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "authority"),
    RETRACTING_STATES,
    ids=[case for case, _authority in RETRACTING_STATES],
)
async def test_a_supporter_that_loses_authority_is_retracted_from_register_reads(
    monkeypatch: pytest.MonkeyPatch, case: str, authority: dict[str, Any]
) -> None:
    db = _Database()
    created = await _seed_two_supporters(monkeypatch, db)

    _transition_document(db, "doc-support", authority)()

    served = await _get_through_route(db, created.id)
    listed = await _list_delay_events(db)

    assert served.linked_document_ids == ["doc-keep"], case
    assert [row["linked_document_ids"] for row in listed] == [["doc-keep"]], case


@pytest.mark.asyncio
async def test_an_operationally_failed_supporter_is_not_retracted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Model B: a crashed worker is not a verdict about the content.

    Over-filtering here would turn every OCR crash and Mongo timeout into a
    silent retraction of evidence a human already relied on.
    """
    db = _Database()
    created = await _seed_two_supporters(monkeypatch, db)

    _transition_document(db, "doc-support", {"processing_status": "failed"})()

    served = await _get_through_route(db, created.id)
    listed = await _list_delay_events(db)

    assert served.linked_document_ids == ["doc-support", "doc-keep"]
    assert [row["linked_document_ids"] for row in listed] == [
        ["doc-support", "doc-keep"]
    ]


@pytest.mark.asyncio
async def test_an_orphaned_supporter_id_fails_closed_at_serve_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An id that no longer resolves canonically cannot be served.

    This is the case a subtractive filter gets wrong: nothing positively
    judges an absent document unusable, so subtraction leaves it in place.
    """
    db = _Database()
    created = await _seed_two_supporters(monkeypatch, db)

    _transition_document(db, "doc-support", None)()

    served = await _get_through_route(db, created.id)
    listed = await _list_delay_events(db)

    assert served.linked_document_ids == ["doc-keep"]
    assert [row["linked_document_ids"] for row in listed] == [["doc-keep"]]


@pytest.mark.asyncio
async def test_a_currently_authorized_supporter_is_still_served(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = _Database()
    created = await _seed_two_supporters(monkeypatch, db)

    served = await _get_through_route(db, created.id)
    listed = await _list_delay_events(db)

    assert served.linked_document_ids == ["doc-support", "doc-keep"]
    assert [row["linked_document_ids"] for row in listed] == [
        ["doc-support", "doc-keep"]
    ]


@pytest.mark.asyncio
async def test_an_unrelated_update_does_not_reemit_a_retracted_supporter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A PATCH that never mentions `linked_document_ids` must not republish one.

    The audit `after` envelope is a claim about what the item NOW IS, and this
    file's own `test_blocked_document_link_is_absent_from_register_audit_envelope`
    already treats an unauthorized supporter there as a violation. `before` is
    the historical prior state and is left exactly as it was recorded.
    """
    db = _Database()
    created = await _seed_two_supporters(monkeypatch, db)
    _transition_document(
        db, "doc-support", {"processing_status": "human_review_required"}
    )()

    updated = await _update_through_route(
        monkeypatch, db, created.id, DelayEventUpdate(title="Revised delay title")
    )

    assert updated.title == "Revised delay title"
    assert updated.linked_document_ids == ["doc-keep"]
    assert _register_audits(db)[-1]["after"]["linked_document_ids"] == ["doc-keep"]


@pytest.mark.asyncio
async def test_the_historical_supporter_association_survives_in_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retraction is a serving decision, not a purge.

    Destroying the stored association would destroy the register's provenance —
    a record of what a human linked, and when. Storage keeps it; the read
    boundary declines to serve it.
    """
    db = _Database()
    created = await _seed_two_supporters(monkeypatch, db)
    _transition_document(
        db, "doc-support", {"processing_status": "human_review_required"}
    )()

    await _get_through_route(db, created.id)
    await _list_delay_events(db)
    await _update_through_route(
        monkeypatch, db, created.id, DelayEventUpdate(title="Revised delay title")
    )

    assert db.delay_events.documents[created.id]["linked_document_ids"] == [
        "doc-support",
        "doc-keep",
    ]


@pytest.mark.asyncio
async def test_authority_recovery_restores_a_retracted_supporter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Because storage is preserved, retraction is reversible.

    The association is evaluated at serve time, so a document that regains
    authority is served again without anyone re-linking it by hand.
    """
    db = _Database()
    created = await _seed_two_supporters(monkeypatch, db)
    _transition_document(
        db, "doc-support", {"processing_status": "human_review_required"}
    )()
    assert (await _get_through_route(db, created.id)).linked_document_ids == [
        "doc-keep"
    ]

    _transition_document(db, "doc-support", {"processing_status": "completed"})()

    assert (await _get_through_route(db, created.id)).linked_document_ids == [
        "doc-support",
        "doc-keep",
    ]


@pytest.mark.asyncio
async def test_mutation_returning_stored_ids_verbatim_reopens_stale_supporters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MUTATION A, made permanent: drop the serve-time projection."""
    db = _Database()
    created = await _seed_two_supporters(monkeypatch, db)
    _transition_document(
        db, "doc-support", {"processing_status": "human_review_required"}
    )()

    async def stored_verbatim(_service: Any, _db: Any, item: Any) -> Any:
        return item

    async def stored_verbatim_rows(_service: Any, _db: Any, items: Any) -> Any:
        return items

    monkeypatch.setattr(
        register_module.EvidenceRegisterService,
        "_current_authority_projection",
        stored_verbatim,
    )
    monkeypatch.setattr(
        register_module.EvidenceRegisterService,
        "_current_authority_projections",
        stored_verbatim_rows,
    )

    served = await _get_through_route(db, created.id)
    listed = await _list_delay_events(db)

    assert served.linked_document_ids == ["doc-support", "doc-keep"]
    assert [row["linked_document_ids"] for row in listed] == [
        ["doc-support", "doc-keep"]
    ]


@pytest.mark.asyncio
async def test_mutation_subtractive_link_filter_lets_an_orphan_supporter_survive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MUTATION B, made permanent: swap the positive resolution for subtraction.

    `blocked_document_ids` returns only ids positively resolved AND positively
    unusable, so an id whose document is gone is never in the blocked set and
    survives the filter. That is the fail-open behaviour the read path must not
    acquire.
    """
    db = _Database()
    created = await _seed_two_supporters(monkeypatch, db)
    _transition_document(db, "doc-support", None)()

    async def subtractive(
        _service: Any,
        db_arg: Any,
        document_ids: Any,
        *,
        organization_id: Any = None,
        project_id: Any = None,
    ) -> list[str]:
        blocked = await publication_policy_module.blocked_document_ids(
            db_arg, document_ids
        )
        return [
            str(value) for value in (document_ids or []) if str(value) not in blocked
        ]

    monkeypatch.setattr(
        register_module.EvidenceRegisterService,
        "_authorized_document_links",
        subtractive,
    )

    served = await _get_through_route(db, created.id)

    assert served.linked_document_ids == ["doc-support", "doc-keep"]
