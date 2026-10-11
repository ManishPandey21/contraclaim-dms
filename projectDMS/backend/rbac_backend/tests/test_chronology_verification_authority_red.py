"""A0a: chronology verification authority (design D2, D2b, D2c + verified mark-duplicate).

A chronology event's ``verification_status`` decides whether arbitration
drafting treats it as authoritative (``pleading_context``, the arbitration
chronology adapter, and the verified evidence-graph links). Before this fix:

* D2  - create and generic update accepted ``verification_status`` under
  ``dms.chronology.edit`` alone, so an editor could publish an event as
  verified without ``dms.chronology.verify``.
* D2b - create accepted the server-owned sync fields ``project_event_id`` /
  ``event_link_ids`` / ``ai_extraction_id``; a forged ``project_event_id``
  made ``_sync_verified_event`` return early, so verification never reached
  the timeline.
* D2c - a content edit to a verified event left it ``edited_verified`` (still
  verified), so changed content stayed authoritative without anyone verifying
  it.
* mark-duplicate needed only ``dms.chronology.edit``, even when it took a
  verified event out of drafting.

The rules pinned here: the generic create/update paths never set review state;
verify, reject and verified mark-duplicate need ``dms.chronology.verify`` and
check it in the service, not only in the router; a material edit to a verified
event returns it to ``needs_review`` whoever makes it; leaving the verified
state withdraws the event's verified graph links.
"""

from __future__ import annotations

import inspect
from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from rbac_backend.models.chronology import (
    ChronologyDecisionRequest,
    ChronologyDuplicateRequest,
    ChronologyVerificationStatus,
    MatterChronologyEventCreate,
    MatterChronologyEventUpdate,
)
from rbac_backend.services.chronology import ChronologyService
from rbac_backend.services.evidence_graph_service import EvidenceGraphService
from rbac_backend.tests.test_chronology_evidence_authority_red import (
    _Database,
    _document,
    _selected,
    _setup,
    _user,
)

EDIT = "dms.chronology.edit"
VERIFY = "dms.chronology.verify"
VIEW = "dms.chronology.view"

VERIFIED = ChronologyVerificationStatus.VERIFIED.value
EDITED_VERIFIED = ChronologyVerificationStatus.EDITED_VERIFIED.value
NEEDS_REVIEW = ChronologyVerificationStatus.NEEDS_REVIEW.value
AI_SUGGESTED = ChronologyVerificationStatus.AI_SUGGESTED.value
DUPLICATE = ChronologyVerificationStatus.DUPLICATE.value
REJECTED = ChronologyVerificationStatus.REJECTED.value

#: States that publish or decide an event. None may arrive through create/update.
DECISION_STATES = [VERIFIED, EDITED_VERIFIED, REJECTED, DUPLICATE]


class _Policy:
    """Authorizes exactly the granted permissions inside one org/project.

    Mirrors ``PolicyService.authorize_document``: it reads the resource's own
    ``organization_id`` / ``project_id`` and nothing from the request.
    """

    def __init__(self, *granted: str, organization_id: str = "org-A", project_ids: tuple[str, ...] = ("proj-A",)) -> None:
        self.granted = set(granted)
        self.organization_id = organization_id
        self.project_ids = project_ids
        self.calls: list[str] = []

    async def authorize_document(self, current_user: Any, permission: str, document: Any, *, resource_type: str = "document") -> None:
        self.calls.append(permission)
        organization_id = (document or {}).get("organization_id")
        project_id = (document or {}).get("project_id")
        if permission not in self.granted:
            raise HTTPException(status_code=403, detail=f"missing {permission}")
        if organization_id != self.organization_id or (project_id is not None and project_id not in self.project_ids):
            raise HTTPException(status_code=403, detail="out of scope")


def _editor() -> _Policy:
    return _Policy(VIEW, EDIT)


def _verifier() -> _Policy:
    return _Policy(VIEW, EDIT, VERIFY)


def _verifier_user() -> SimpleNamespace:
    return SimpleNamespace(id="verifier-user", organization_id="org-A")


async def _db_and_chronology() -> tuple[_Database, ChronologyService, dict[str, Any]]:
    db = _Database([_document("doc-A"), _document("doc-B")])
    service, chronology = await _setup(db)
    return db, service, chronology


async def _candidate(service: ChronologyService, chronology: dict[str, Any], **extra: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "chronology_id": chronology["_id"],
        "event_date": datetime(2026, 3, 10),
        "title": "Access to Pier P14 not provided",
        "description": "Contractor letter records that access was not provided.",
        "source_document_id": "doc-A",
        "contract_clauses": ["Clause 8.4"],
    }
    fields.update(extra)
    return await service.create_event(MatterChronologyEventCreate(**fields), _user())


async def _verified(service: ChronologyService, chronology: dict[str, Any], **extra: Any) -> dict[str, Any]:
    event = await _candidate(service, chronology, **extra)
    return await _route_verify(service.db, chronology["_id"], event["_id"], _verifier())


def _stored(db: _Database, event_id: str) -> dict[str, Any]:
    return next(row for row in db.matter_chronology_events.documents if row["_id"] == event_id)


async def _route_create(db: _Database, chronology_id: str, payload: MatterChronologyEventCreate, policy: _Policy) -> dict[str, Any]:
    from rbac_backend.routers import chronology as chronology_router

    created = await chronology_router.create_chronology_event(
        chronology_id=chronology_id, payload=payload, db=db, current_user=_user(), policy=policy,
        selection=_selected(db, _user()),
    )
    return created.model_dump(by_alias=True)


async def _route_update(db: _Database, chronology_id: str, event_id: str, payload: MatterChronologyEventUpdate, policy: _Policy) -> dict[str, Any]:
    from rbac_backend.routers import chronology as chronology_router

    updated = await chronology_router.update_chronology_event(
        chronology_id=chronology_id, event_id=event_id, payload=payload, db=db, current_user=_user(), policy=policy,
        selection=_selected(db, _user()),
    )
    return updated.model_dump(by_alias=True)


async def _route_verify(db: _Database, chronology_id: str, event_id: str, policy: _Policy) -> dict[str, Any]:
    from rbac_backend.routers import chronology as chronology_router

    verified = await chronology_router.verify_chronology_event(
        chronology_id=chronology_id, event_id=event_id, payload=None, db=db, current_user=_verifier_user(),
        policy=policy, selection=_selected(db, _verifier_user()),
    )
    return verified.model_dump(by_alias=True)


async def _route_reject(db: _Database, chronology_id: str, event_id: str, policy: _Policy) -> dict[str, Any]:
    from rbac_backend.routers import chronology as chronology_router

    rejected = await chronology_router.reject_chronology_event(
        chronology_id=chronology_id, event_id=event_id, payload=None, db=db, current_user=_verifier_user(),
        policy=policy, selection=_selected(db, _verifier_user()),
    )
    return rejected.model_dump(by_alias=True)


async def _route_mark_duplicate(db: _Database, chronology_id: str, event_id: str, duplicate_of: str, policy: _Policy) -> dict[str, Any]:
    from rbac_backend.routers import chronology as chronology_router

    marked = await chronology_router.mark_chronology_event_duplicate(
        chronology_id=chronology_id, event_id=event_id,
        payload=ChronologyDuplicateRequest(duplicate_of_event_id=duplicate_of), db=db, current_user=_user(),
        policy=policy, selection=_selected(db, _user()),
    )
    return marked.model_dump(by_alias=True)


# --- 1. EDIT-ONLY CREATE -------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("state", DECISION_STATES)
async def test_edit_only_create_cannot_arrive_in_a_decision_state(state: str) -> None:
    db, _service, chronology = await _db_and_chronology()
    payload = MatterChronologyEventCreate(chronology_id=chronology["_id"], title="Forged verified event", verification_status=state)

    with pytest.raises(HTTPException) as refusal:
        await _route_create(db, chronology["_id"], payload, _editor())

    assert refusal.value.status_code == 403
    assert db.matter_chronology_events.documents == []
    assert db.project_events.documents == [] and db.event_links.documents == []


@pytest.mark.asyncio
@pytest.mark.parametrize("state", DECISION_STATES)
async def test_a_verifier_also_cannot_create_an_event_already_decided(state: str) -> None:
    """Creation and verification are separate acts: a verifier creates, then verifies."""
    db, _service, chronology = await _db_and_chronology()
    payload = MatterChronologyEventCreate(chronology_id=chronology["_id"], title="Pre-verified event", verification_status=state)

    with pytest.raises(HTTPException) as refusal:
        await _route_create(db, chronology["_id"], payload, _verifier())

    assert refusal.value.status_code == 403
    assert db.matter_chronology_events.documents == []


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [AI_SUGGESTED, NEEDS_REVIEW])
async def test_candidate_states_remain_creatable_with_edit(state: str) -> None:
    db, _service, chronology = await _db_and_chronology()
    payload = MatterChronologyEventCreate(chronology_id=chronology["_id"], title="Manual candidate", verification_status=state)

    created = await _route_create(db, chronology["_id"], payload, _editor())

    assert created["verification_status"] == state


# --- 2. EDIT-ONLY UPDATE -------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("state", DECISION_STATES)
async def test_edit_only_update_cannot_move_a_candidate_into_a_decision_state(state: str) -> None:
    db, service, chronology = await _db_and_chronology()
    event = await _candidate(service, chronology)

    with pytest.raises(HTTPException) as refusal:
        await _route_update(db, chronology["_id"], event["_id"], MatterChronologyEventUpdate(verification_status=state), _editor())

    assert refusal.value.status_code == 403
    assert _stored(db, event["_id"])["verification_status"] == AI_SUGGESTED
    assert db.project_events.documents == []


@pytest.mark.asyncio
async def test_generic_update_is_not_a_verification_path_even_for_a_verifier() -> None:
    db, service, chronology = await _db_and_chronology()
    event = await _candidate(service, chronology)

    with pytest.raises(HTTPException) as refusal:
        await _route_update(db, chronology["_id"], event["_id"], MatterChronologyEventUpdate(verification_status=VERIFIED), _verifier())

    assert refusal.value.status_code == 403
    assert _stored(db, event["_id"])["verification_status"] == AI_SUGGESTED


@pytest.mark.asyncio
async def test_echoing_the_unchanged_status_on_update_is_not_a_transition() -> None:
    db, service, chronology = await _db_and_chronology()
    event = await _candidate(service, chronology)

    updated = await _route_update(
        db, chronology["_id"], event["_id"],
        MatterChronologyEventUpdate(title="Retitled candidate", verification_status=AI_SUGGESTED), _editor(),
    )

    assert updated["verification_status"] == AI_SUGGESTED
    assert updated["title"] == "Retitled candidate"


# --- 3. NORMAL EDIT --------------------------------------------------------------


@pytest.mark.asyncio
async def test_edit_only_caller_may_still_edit_a_candidate() -> None:
    db, service, chronology = await _db_and_chronology()
    event = await _candidate(service, chronology)

    updated = await _route_update(
        db, chronology["_id"], event["_id"], MatterChronologyEventUpdate(title="Corrected title", manual_notes="checked"), _editor()
    )

    assert updated["title"] == "Corrected title"
    assert updated["verification_status"] == AI_SUGGESTED


# --- 4. EXPLICIT VERIFY ------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_route_with_verify_permission_verifies() -> None:
    db, service, chronology = await _db_and_chronology()
    event = await _candidate(service, chronology)

    verified = await _route_verify(db, chronology["_id"], event["_id"], _verifier())

    assert verified["verification_status"] == VERIFIED


@pytest.mark.asyncio
async def test_verify_route_without_verify_permission_is_refused() -> None:
    db, service, chronology = await _db_and_chronology()
    event = await _candidate(service, chronology)

    with pytest.raises(HTTPException) as refusal:
        await _route_verify(db, chronology["_id"], event["_id"], _editor())

    assert refusal.value.status_code == 403
    assert _stored(db, event["_id"])["verification_status"] == AI_SUGGESTED
    assert db.project_events.documents == []


# --- 5/6. MATERIAL EDIT OF A VERIFIED EVENT ----------------------------------------

MATERIAL_EDITS: list[tuple[str, dict[str, Any]]] = [
    ("event_date", {"event_date": datetime(2026, 3, 11)}),
    ("event_end_date", {"event_end_date": datetime(2026, 4, 21)}),
    ("date_text", {"date_text": "11.03.2026"}),
    ("date_type", {"date_type": "approximate"}),
    ("title", {"title": "Access delayed by the Employer"}),
    ("description", {"description": "Rewritten narrative"}),
    ("event_classification", {"event_classification": "breach"}),
    ("from_party", {"from_party": "Employer"}),
    ("to_party", {"to_party": "Engineer"}),
    ("responsible_party", {"responsible_party": "Employer"}),
    ("supports_party", {"supports_party": "claimant"}),
    ("impact_type", {"impact_type": "time"}),
    ("impact_days", {"impact_days": 42.0}),
    ("impact_amount", {"impact_amount": 1000000.0}),
    ("issue_tags", {"issue_tags": ["site_access"]}),
    ("claim_heads", {"claim_heads": ["eot"]}),
    ("contract_clauses", {"contract_clauses": ["Clause 2.1"]}),
    ("source_document_id", {"source_document_id": "doc-B"}),
    ("source_page", {"source_page": 3}),
    ("source_paragraph", {"source_paragraph": "4.2"}),
    ("source_spans", {"source_spans": [{"page": 3, "text": "other span"}]}),
    ("letter_no", {"letter_no": "AB/P2/999"}),
    ("pleading_use", {"pleading_use": "soc_breach"}),
    ("manual_notes", {"manual_notes": "Counsel note now used as the snippet"}),
    ("confidence_score", {"confidence_score": 0.99}),
    ("related_event_ids", {"related_event_ids": ["ev-other"]}),
    ("duplicate_of_event_id", {"duplicate_of_event_id": "ev-other"}),
    ("metadata", {"metadata": {"content_hash": "changed"}}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("field", "change"), MATERIAL_EDITS, ids=[field for field, _ in MATERIAL_EDITS])
async def test_material_edit_by_an_editor_returns_a_verified_event_to_review(field: str, change: dict[str, Any]) -> None:
    db, service, chronology = await _db_and_chronology()
    event = await _verified(service, chronology)

    updated = await _route_update(db, chronology["_id"], event["_id"], MatterChronologyEventUpdate(**change), _editor())

    assert updated["verification_status"] == NEEDS_REVIEW, field
    assert _stored(db, event["_id"])["verification_status"] == NEEDS_REVIEW, field


@pytest.mark.asyncio
@pytest.mark.parametrize(("field", "change"), MATERIAL_EDITS[:6], ids=[field for field, _ in MATERIAL_EDITS[:6]])
async def test_material_edit_by_a_verifier_through_update_still_returns_to_review(field: str, change: dict[str, Any]) -> None:
    db, service, chronology = await _db_and_chronology()
    event = await _verified(service, chronology)

    updated = await _route_update(db, chronology["_id"], event["_id"], MatterChronologyEventUpdate(**change), _verifier())

    assert updated["verification_status"] == NEEDS_REVIEW, field


@pytest.mark.asyncio
@pytest.mark.parametrize(("field", "change"), [("annexure_no", {"annexure_no": "C-12"}), ("legal_relevance", {"legal_relevance": "Supports notice compliance"})])
async def test_presentation_only_edit_keeps_existing_behaviour(field: str, change: dict[str, Any]) -> None:
    """Neither field is read by drafting, arbitration, exports or the graph."""
    db, service, chronology = await _db_and_chronology()
    event = await _verified(service, chronology)

    updated = await _route_update(db, chronology["_id"], event["_id"], MatterChronologyEventUpdate(**change), _editor())

    assert updated["verification_status"] == EDITED_VERIFIED, field


@pytest.mark.asyncio
async def test_echoing_unchanged_values_on_a_verified_event_is_not_a_material_edit() -> None:
    db, service, chronology = await _db_and_chronology()
    event = await _verified(service, chronology)

    updated = await _route_update(
        db, chronology["_id"], event["_id"],
        MatterChronologyEventUpdate(title=event["title"], event_date=event["event_date"], verification_status=VERIFIED),
        _editor(),
    )

    assert updated["verification_status"] in {VERIFIED, EDITED_VERIFIED}


# --- 7. REVERIFY AFTER EDIT, WITH HISTORY ------------------------------------------


@pytest.mark.asyncio
async def test_reverify_after_material_edit_restores_verified_and_history_shows_each_step() -> None:
    db, service, chronology = await _db_and_chronology()
    event = await _verified(service, chronology)

    await _route_update(db, chronology["_id"], event["_id"], MatterChronologyEventUpdate(title="Changed title"), _editor())
    reverified = await _route_verify(db, chronology["_id"], event["_id"], _verifier())

    assert reverified["verification_status"] == VERIFIED
    revisions = await service.event_revisions(chronology["_id"], event["_id"])
    actions = [row["action"] for row in revisions]
    assert actions == ["created", "verified", "edited", "verified"]
    edited = revisions[2]
    assert edited["before"]["verification_status"] == VERIFIED
    assert edited["before"]["title"] == "Access to Pier P14 not provided"
    assert edited["after"]["verification_status"] == NEEDS_REVIEW
    assert edited["after"]["title"] == "Changed title"
    assert "title" in (edited.get("note") or "")
    assert revisions[3]["created_by"] == "verifier-user"
    assert revisions[3]["created_at"] is not None


# --- 8/9. MARK-DUPLICATE -------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("prior", ["verified", "edited_verified"])
async def test_edit_only_caller_cannot_mark_a_verified_event_duplicate(prior: str) -> None:
    db, service, chronology = await _db_and_chronology()
    original = await _candidate(service, chronology, title="Original")
    event = await _verified(service, chronology)
    if prior == "edited_verified":
        await _route_update(db, chronology["_id"], event["_id"], MatterChronologyEventUpdate(annexure_no="C-1"), _editor())
        assert _stored(db, event["_id"])["verification_status"] == EDITED_VERIFIED

    with pytest.raises(HTTPException) as refusal:
        await _route_mark_duplicate(db, chronology["_id"], event["_id"], original["_id"], _editor())

    assert refusal.value.status_code == 403
    assert _stored(db, event["_id"])["verification_status"] == prior


@pytest.mark.asyncio
async def test_verifier_may_mark_a_verified_event_duplicate() -> None:
    db, service, chronology = await _db_and_chronology()
    original = await _candidate(service, chronology, title="Original")
    event = await _verified(service, chronology)

    marked = await _route_mark_duplicate(db, chronology["_id"], event["_id"], original["_id"], _verifier())

    assert marked["verification_status"] == DUPLICATE
    assert marked["duplicate_of_event_id"] == original["_id"]


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [AI_SUGGESTED, NEEDS_REVIEW])
async def test_edit_only_caller_may_still_mark_a_candidate_duplicate(state: str) -> None:
    db, service, chronology = await _db_and_chronology()
    original = await _candidate(service, chronology, title="Original")
    event = await _candidate(service, chronology, verification_status=state)

    marked = await _route_mark_duplicate(db, chronology["_id"], event["_id"], original["_id"], _editor())

    assert marked["verification_status"] == DUPLICATE


# --- 10/11. SERVER-OWNED SYNC FIELDS ---------------------------------------------

SYNC_FIELDS: list[tuple[str, Any]] = [
    ("project_event_id", "forged-project-event"),
    ("event_link_ids", ["forged-link-group"]),
    ("ai_extraction_id", "forged-extraction"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("field", "value"), SYNC_FIELDS, ids=[field for field, _ in SYNC_FIELDS])
async def test_create_refuses_server_owned_sync_fields(field: str, value: Any) -> None:
    db, _service, chronology = await _db_and_chronology()
    payload = MatterChronologyEventCreate(chronology_id=chronology["_id"], title="Candidate", **{field: value})

    with pytest.raises(HTTPException) as refusal:
        await _route_create(db, chronology["_id"], payload, _verifier())

    assert refusal.value.status_code == 422
    assert db.matter_chronology_events.documents == []


@pytest.mark.asyncio
async def test_a_forged_project_event_id_cannot_suppress_the_server_sync() -> None:
    """A row already carrying a client-forged id (pre-fix data) still syncs on verify."""
    db, service, chronology = await _db_and_chronology()
    event = await _candidate(service, chronology)
    for row in db.matter_chronology_events.documents:
        if row["_id"] == event["_id"]:
            row["project_event_id"] = "forged-project-event"
            row["event_link_ids"] = ["forged-link-group"]

    verified = await _route_verify(db, chronology["_id"], event["_id"], _verifier())

    synced = [row for row in db.project_events.documents if (row.get("metadata") or {}).get("chronology_event_id") == event["_id"]]
    assert len(synced) == 1
    assert verified["project_event_id"] == synced[0]["_id"] != "forged-project-event"
    assert "forged-link-group" not in verified["event_link_ids"]
    assert verified["event_link_ids"], "verification must write its own evidence links"


# --- 13. DIRECT SERVICE INVOCATION -----------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("state", DECISION_STATES)
async def test_service_create_refuses_decision_states_without_any_router(state: str) -> None:
    db, service, chronology = await _db_and_chronology()

    with pytest.raises(HTTPException) as refusal:
        await service.create_event(
            MatterChronologyEventCreate(chronology_id=chronology["_id"], title="Direct", verification_status=state), _user()
        )

    assert refusal.value.status_code == 403
    assert db.matter_chronology_events.documents == []


@pytest.mark.asyncio
async def test_service_update_refuses_a_status_transition_without_any_router() -> None:
    db, service, chronology = await _db_and_chronology()
    event = await _candidate(service, chronology)

    with pytest.raises(HTTPException) as refusal:
        await service.update_event(chronology["_id"], event["_id"], MatterChronologyEventUpdate(verification_status=VERIFIED), _user())

    assert refusal.value.status_code == 403
    assert _stored(db, event["_id"])["verification_status"] == AI_SUGGESTED


@pytest.mark.asyncio
@pytest.mark.parametrize(("field", "value"), SYNC_FIELDS, ids=[field for field, _ in SYNC_FIELDS])
async def test_service_create_refuses_sync_fields_without_any_router(field: str, value: Any) -> None:
    db, service, chronology = await _db_and_chronology()

    with pytest.raises(HTTPException) as refusal:
        await service.create_event(MatterChronologyEventCreate(chronology_id=chronology["_id"], title="Direct", **{field: value}), _user())

    assert refusal.value.status_code == 422


@pytest.mark.parametrize("method", ["verify_event", "reject_event", "mark_duplicate"])
def test_service_transitions_require_a_policy_argument(method: str) -> None:
    """No default: an internal caller that forgets authority fails, it does not skip it."""
    parameter = inspect.signature(getattr(ChronologyService, method)).parameters.get("policy")
    assert parameter is not None, method
    assert parameter.default is inspect.Parameter.empty, method
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY, method


@pytest.mark.asyncio
async def test_service_verify_checks_verify_permission_itself() -> None:
    db, service, chronology = await _db_and_chronology()
    event = await _candidate(service, chronology)
    editor = _editor()

    with pytest.raises(HTTPException) as refusal:
        await service.verify_event(chronology["_id"], event["_id"], _user(), policy=editor)

    assert refusal.value.status_code == 403
    assert VERIFY in editor.calls
    assert _stored(db, event["_id"])["verification_status"] == AI_SUGGESTED
    assert db.project_events.documents == []


@pytest.mark.asyncio
async def test_service_reject_checks_verify_permission_itself() -> None:
    db, service, chronology = await _db_and_chronology()
    event = await _verified(service, chronology)

    with pytest.raises(HTTPException) as refusal:
        await service.reject_event(chronology["_id"], event["_id"], _user(), policy=_editor())

    assert refusal.value.status_code == 403
    assert _stored(db, event["_id"])["verification_status"] == VERIFIED


@pytest.mark.asyncio
async def test_service_verified_mark_duplicate_checks_verify_permission_itself() -> None:
    db, service, chronology = await _db_and_chronology()
    original = await _candidate(service, chronology, title="Original")
    event = await _verified(service, chronology)

    with pytest.raises(HTTPException) as refusal:
        await service.mark_duplicate(
            chronology["_id"], event["_id"], ChronologyDuplicateRequest(duplicate_of_event_id=original["_id"]), _user(),
            policy=_editor(),
        )

    assert refusal.value.status_code == 403
    assert _stored(db, event["_id"])["verification_status"] == VERIFIED


@pytest.mark.asyncio
async def test_service_verify_refuses_a_foreign_scoped_chronology_for_the_verifier() -> None:
    db, service, chronology = await _db_and_chronology()
    event = await _candidate(service, chronology)
    foreign_verifier = _Policy(VIEW, EDIT, VERIFY, organization_id="org-B", project_ids=("proj-B",))

    with pytest.raises(HTTPException) as refusal:
        await service.verify_event(chronology["_id"], event["_id"], _user(), policy=foreign_verifier)

    assert refusal.value.status_code == 403
    assert _stored(db, event["_id"])["verification_status"] == AI_SUGGESTED


@pytest.mark.asyncio
async def test_service_material_edit_rule_holds_without_any_router() -> None:
    db, service, chronology = await _db_and_chronology()
    event = await _verified(service, chronology)

    updated = await service.update_event(chronology["_id"], event["_id"], MatterChronologyEventUpdate(title="Direct change"), _user())

    assert updated["verification_status"] == NEEDS_REVIEW


# --- 14. DOWNSTREAM AUTHORITATIVE READ -------------------------------------------


async def _verified_links_for(db: _Database, event_id: str) -> list[dict[str, Any]]:
    links = await EvidenceGraphService(db).downstream_links({"organization_id": "org-A", "project_id": "proj-A"})
    return [link for link in links if (link.get("metadata") or {}).get("chronology_event_id") == event_id]


async def _demote_by_edit(db, service, chronology, event):
    await _route_update(db, chronology["_id"], event["_id"], MatterChronologyEventUpdate(title="Changed after verification"), _editor())


async def _demote_by_reject(db, service, chronology, event):
    await _route_reject(db, chronology["_id"], event["_id"], _verifier())


async def _demote_by_duplicate(db, service, chronology, event):
    original = await _candidate(service, chronology, title="Original")
    await _route_mark_duplicate(db, chronology["_id"], event["_id"], original["_id"], _verifier())


DEMOTIONS = [("material_edit", _demote_by_edit), ("reject", _demote_by_reject), ("mark_duplicate", _demote_by_duplicate)]


@pytest.mark.asyncio
@pytest.mark.parametrize(("how", "demote"), DEMOTIONS, ids=[how for how, _ in DEMOTIONS])
async def test_leaving_the_verified_state_withdraws_every_authoritative_read(how: str, demote: Any) -> None:
    from rbac_backend.services.arbitration_drafting.agents.deterministic import _verified as adapter_verified

    db, service, chronology = await _db_and_chronology()
    event = await _verified(service, chronology)
    assert await _verified_links_for(db, event["_id"]), "precondition: verification published links"

    await demote(db, service, chronology, event)

    stored = _stored(db, event["_id"])
    assert not adapter_verified(stored), how
    context = await service.pleading_context(chronology["_id"])
    assert all(row["source_id"] != event["_id"] for row in context.source_ledger), how
    assert await _verified_links_for(db, event["_id"]) == [], how
    project_event = next(row for row in db.project_events.documents if row["_id"] == stored["project_event_id"])
    assert project_event["status"] != "open", how


@pytest.mark.asyncio
async def test_reverification_republishes_links_from_the_current_content() -> None:
    db, service, chronology = await _db_and_chronology()
    event = await _verified(service, chronology)
    await _route_update(db, chronology["_id"], event["_id"], MatterChronologyEventUpdate(description="Corrected narrative"), _editor())

    await _route_verify(db, chronology["_id"], event["_id"], _verifier())

    links = await _verified_links_for(db, event["_id"])
    assert links and all(link.get("evidence_text") == "Corrected narrative" for link in links)
    stored = _stored(db, event["_id"])
    project_event = next(row for row in db.project_events.documents if row["_id"] == stored["project_event_id"])
    assert project_event["status"] == "open"
    assert project_event["description"] == "Corrected narrative"
    assert len([row for row in db.project_events.documents if (row.get("metadata") or {}).get("chronology_event_id") == event["_id"]]) == 1


@pytest.mark.asyncio
async def test_linking_a_new_event_to_a_verified_event_is_a_material_edit() -> None:
    from rbac_backend.models.chronology import ChronologyLinkRequest
    from rbac_backend.routers import chronology as chronology_router

    db, service, chronology = await _db_and_chronology()
    other = await _candidate(service, chronology, title="Related event")
    event = await _verified(service, chronology)

    linked = await chronology_router.link_chronology_event(
        chronology_id=chronology["_id"], event_id=event["_id"], payload=ChronologyLinkRequest(related_event_ids=[other["_id"]]),
        db=db, current_user=_user(), policy=_editor(), selection=_selected(db, _user()),
    )

    assert linked.verification_status == NEEDS_REVIEW
    assert await _verified_links_for(db, event["_id"]) == []
