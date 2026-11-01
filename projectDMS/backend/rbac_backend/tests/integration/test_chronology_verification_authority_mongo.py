"""A0a over real HTTP, Mongo and RBAC: chronology verification authority.

Same harness discipline as CL-3B (signed JWT through the real
``get_current_user``, real ``PolicyService`` / scope chain, real role and
permission seeds, navbar selection as ``X-Proj-Id``). Adds one persona the seeds
do not have: ``chronology_editor``, a project-tier role holding
``dms.chronology.edit`` but not ``dms.chronology.verify``, so "edit never confers
verify" is tested against the real permission resolution.

Opt-in through ``RELATIONSHIP_CL3B_MONGODB_URI`` (a disposable replica set); CI
runs it in the replica-set step and fails if it skips.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from rbac_backend.tests.integration.test_cl3b_programme_chronology_mongo import (
    CHR_A1,
    CHR_B1,
    ORG_A,
    ORG_B,
    PROJ_A1,
    PROJ_A2,
    PROJ_B1,
    _env,
    _event,
)

pytestmark = pytest.mark.integration

EDITOR = "chronology_editor"
EDITOR_ROLE = "chronology_editor_test"
CANDIDATE_A1 = "ev-a0a-candidate-a1"
VERIFIED_A1 = "ev-a0a-verified-a1"
CANDIDATE_B1 = "ev-a0a-candidate-b1"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _seed_a0a(db: Any) -> None:
    template = await db.roles.find_one({"_id": "projectadmin"})
    assert template is not None, "seeded projectadmin role"
    assert "dms.chronology.verify" in template["permissions"] and "dms.chronology.edit" in template["permissions"]
    editor_role = dict(template)
    editor_role.update(
        {
            "_id": EDITOR_ROLE,
            "name": EDITOR_ROLE,
            # `dms.admin` implies every `dms.*` permission (`PolicyService.has_permission`),
            # so a role holding it holds verify whatever else it lists.
            "permissions": [p for p in template["permissions"] if p not in {"dms.chronology.verify", "dms.admin"}],
        }
    )
    await db.roles.insert_one(editor_role)
    await db.users.insert_one(
        {"_id": f"user-{EDITOR}", "email": f"{EDITOR}@example.com", "username": EDITOR, "disabled": False,
         "account_type": "client_user", "organizations": [], "roles": [EDITOR_ROLE],
         "organization_id": ORG_A, "projects": [PROJ_A1]}
    )
    await db.matter_chronology_events.insert_many(
        [
            _event(CANDIDATE_A1, CHR_A1, "A0a candidate", scope=(ORG_A, PROJ_A1), verification_status="ai_suggested"),
            _event(VERIFIED_A1, CHR_A1, "A0a verified", scope=(ORG_A, PROJ_A1), verification_status="verified"),
            _event(CANDIDATE_B1, CHR_B1, "A0a foreign candidate", scope=(ORG_B, PROJ_B1), verification_status="ai_suggested"),
        ]
    )


async def _status(db: Any, event_id: str) -> str:
    row = await db.matter_chronology_events.find_one({"_id": event_id})
    return str(row["verification_status"])


def _events(chronology_id: str) -> str:
    return f"/api/chronologies/{chronology_id}/events"


def test_editor_persona_holds_edit_but_not_verify() -> None:
    async def scenario() -> None:
        async with _env() as env:
            await _seed_a0a(env.db)
            # Positive control: the editor can edit a candidate in its own project.
            ok = await env.call("PATCH", EDITOR, f"{_events(CHR_A1)}/{CANDIDATE_A1}", PROJ_A1, json={"title": "Edited"})
            assert ok.status_code == 200, ok.text
            refused = await env.call("POST", EDITOR, f"{_events(CHR_A1)}/{CANDIDATE_A1}/verify", PROJ_A1, json={})
            assert refused.status_code == 403, refused.text
            assert await _status(env.db, CANDIDATE_A1) == "ai_suggested"

    _run(scenario())


@pytest.mark.parametrize("state", ["verified", "edited_verified", "rejected", "duplicate"])
def test_editor_cannot_create_an_event_in_a_decision_state(state: str) -> None:
    async def scenario() -> None:
        async with _env() as env:
            await _seed_a0a(env.db)
            response = await env.call(
                "POST", EDITOR, _events(CHR_A1), PROJ_A1,
                json={"chronology_id": CHR_A1, "title": "Forged", "verification_status": state},
            )
            assert response.status_code == 403, response.text
            assert await env.db.matter_chronology_events.find_one({"title": "Forged"}) is None

    _run(scenario())


def test_editor_cannot_verify_through_generic_update() -> None:
    async def scenario() -> None:
        async with _env() as env:
            await _seed_a0a(env.db)
            response = await env.call(
                "PATCH", EDITOR, f"{_events(CHR_A1)}/{CANDIDATE_A1}", PROJ_A1, json={"verification_status": "verified"}
            )
            assert response.status_code == 403, response.text
            assert await _status(env.db, CANDIDATE_A1) == "ai_suggested"

    _run(scenario())


def test_editor_material_edit_returns_a_verified_event_to_review_and_withdraws_it() -> None:
    async def scenario() -> None:
        async with _env() as env:
            await _seed_a0a(env.db)
            verified = await env.call("POST", "project_admin", f"{_events(CHR_A1)}/{CANDIDATE_A1}/verify", PROJ_A1, json={})
            assert verified.status_code == 200, verified.text
            assert verified.json()["project_event_id"]

            edited = await env.call(
                "PATCH", EDITOR, f"{_events(CHR_A1)}/{CANDIDATE_A1}", PROJ_A1, json={"event_date": "2026-02-12T00:00:00"}
            )
            assert edited.status_code == 200, edited.text
            assert edited.json()["verification_status"] == "needs_review"

            project_event = await env.db.project_events.find_one({"_id": verified.json()["project_event_id"]})
            assert project_event["status"] != "open"
            from rbac_backend.services.evidence_graph_service import EvidenceGraphService

            links = await EvidenceGraphService(env.db).downstream_links({"organization_id": ORG_A, "project_id": PROJ_A1})
            assert not [link for link in links if (link.get("metadata") or {}).get("chronology_event_id") == CANDIDATE_A1]

            reverified = await env.call("POST", "project_admin", f"{_events(CHR_A1)}/{CANDIDATE_A1}/verify", PROJ_A1, json={})
            assert reverified.status_code == 200, reverified.text
            assert reverified.json()["verification_status"] == "verified"
            history = await env.call("GET", "project_admin", f"{_events(CHR_A1)}/{CANDIDATE_A1}/revisions", PROJ_A1)
            assert [row["action"] for row in history.json()][-3:] == ["verified", "edited", "verified"]

    _run(scenario())


def test_editor_cannot_mark_a_verified_event_duplicate_but_a_verifier_can() -> None:
    async def scenario() -> None:
        async with _env() as env:
            await _seed_a0a(env.db)
            body = {"duplicate_of_event_id": CANDIDATE_A1}
            refused = await env.call("POST", EDITOR, f"{_events(CHR_A1)}/{VERIFIED_A1}/mark-duplicate", PROJ_A1, json=body)
            assert refused.status_code == 403, refused.text
            assert await _status(env.db, VERIFIED_A1) == "verified"

            allowed = await env.call("POST", "project_admin", f"{_events(CHR_A1)}/{VERIFIED_A1}/mark-duplicate", PROJ_A1, json=body)
            assert allowed.status_code == 200, allowed.text
            assert allowed.json()["verification_status"] == "duplicate"

    _run(scenario())


def test_editor_may_still_mark_a_candidate_duplicate() -> None:
    async def scenario() -> None:
        async with _env() as env:
            await _seed_a0a(env.db)
            response = await env.call(
                "POST", EDITOR, f"{_events(CHR_A1)}/{CANDIDATE_A1}/mark-duplicate", PROJ_A1,
                json={"duplicate_of_event_id": VERIFIED_A1},
            )
            assert response.status_code == 200, response.text

    _run(scenario())


@pytest.mark.parametrize("field,value", [("project_event_id", "forged"), ("event_link_ids", ["forged"]), ("ai_extraction_id", "forged")])
def test_create_refuses_server_owned_sync_fields(field: str, value: Any) -> None:
    async def scenario() -> None:
        async with _env() as env:
            await _seed_a0a(env.db)
            response = await env.call(
                "POST", "project_admin", _events(CHR_A1), PROJ_A1, json={"chronology_id": CHR_A1, "title": "Sync", field: value}
            )
            assert response.status_code == 422, response.text

    _run(scenario())


#: (persona, selected project, chronology, event, expected status). Positive controls first.
VERIFY_SCOPE_MATRIX = [
    ("project_admin", PROJ_A1, CHR_A1, CANDIDATE_A1, 200),
    ("org_admin", PROJ_A1, CHR_A1, CANDIDATE_A1, 200),
    ("superadmin", PROJ_A1, CHR_A1, CANDIDATE_A1, 200),
    # Foreign project: a verifier of A2 selecting A1, and selecting its own A2.
    ("other_project_admin", PROJ_A1, CHR_A1, CANDIDATE_A1, 403),
    ("other_project_admin", PROJ_A2, CHR_A1, CANDIDATE_A1, 403),
    # Foreign organisation.
    ("foreign_org_admin", PROJ_B1, CHR_A1, CANDIDATE_A1, 403),
    ("foreign_org_admin", PROJ_A1, CHR_A1, CANDIDATE_A1, 403),
    # Organisation role reaching outside its organisation.
    ("org_admin", PROJ_B1, CHR_B1, CANDIDATE_B1, 403),
    ("org_admin", PROJ_A1, CHR_B1, CANDIDATE_B1, 403),
    # Project member of A1+A2 whose selection is A2 cannot act on A1.
    ("member_ab", PROJ_A2, CHR_A1, CANDIDATE_A1, 403),
    # A project user holds no chronology permission at all.
    ("project_user", PROJ_A1, CHR_A1, CANDIDATE_A1, 403),
]


@pytest.mark.parametrize(
    ("persona", "project", "chronology", "event", "expected"),
    VERIFY_SCOPE_MATRIX,
    ids=[f"{p}-{s}-{e}" for p, s, _c, e, _x in VERIFY_SCOPE_MATRIX],
)
def test_verify_authority_never_widens_tenant_scope(persona: str, project: str, chronology: str, event: str, expected: int) -> None:
    async def scenario() -> None:
        async with _env() as env:
            await _seed_a0a(env.db)
            before = await _status(env.db, event)
            response = await env.call("POST", persona, f"{_events(chronology)}/{event}/verify", project, json={})
            assert response.status_code == expected, response.text
            if expected != 200:
                assert await _status(env.db, event) == before

    _run(scenario())


def test_super_admin_without_a_selection_follows_the_existing_selection_rule() -> None:
    async def scenario() -> None:
        async with _env() as env:
            await _seed_a0a(env.db)
            response = await env.call("POST", "superadmin", f"{_events(CHR_A1)}/{CANDIDATE_A1}/verify", None, json={})
            assert response.status_code == 400, response.text
            assert await _status(env.db, CANDIDATE_A1) == "ai_suggested"

    _run(scenario())
