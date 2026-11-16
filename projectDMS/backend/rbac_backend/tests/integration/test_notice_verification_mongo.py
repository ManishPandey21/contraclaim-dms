"""4B-14 (D-08, C-08): notice periods are resolved against the clause index and verified by a
second, authorised person; both actions are audited. Real HTTP, MongoDB and RBAC.

Before 4B-14 the client supplied the clause, the quote, the document and
``origin=clause_index``; the only check was that the quote contained the day
number. "Clause 99.9: notice within 60 days" recorded with ``origin=clause_index``
and verified by its own recorder made every claim of that type read "contract,
verified". ``set`` needed claim-edit only and ``verify`` claim-manage only; no
audit event was written.

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set); run in CI's
real-Mongo step.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from rbac_backend.tests.integration.test_claim_privileged_rbac_mongo import _add_actor
from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import _env
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, PROJ_A1

pytestmark = pytest.mark.integration

SET = "/api/claims/notice/requirements"
CLAUSE_TEXT = (
    "If the Contractor considers himself to be entitled to any extension of the Time for Completion, "
    "the Contractor's notice shall be given to the Engineer, describing the event or circumstance giving "
    "rise to the claim. The notice shall be given as soon as practicable, and not later than 28 days "
    "after the Contractor became aware, or should have become aware, of the event or circumstance."
)
QUOTE = "The notice shall be given as soon as practicable, and not later than 28 days after the Contractor became aware"
NO_CLAUSE_READ = "claim_editor_no_clause_read"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _seed(env: Any) -> None:
    await env.db.contract_clauses.insert_many(
        [
            {
                "_id": "cl-20-1-gcc",
                "org_id": ORG_A,
                "project_id": PROJ_A1,
                "is_current": True,
                "is_authorised_for_ai": True,
                "clause_no": "20.1",
                "clause_uid": "uid-gcc-20.1",
                "clause_title": "Contractor's Claims",
                "text": CLAUSE_TEXT,
                "document_id": "doc-gcc",
                "document_type": "GCC",
            },
            {
                "_id": "cl-8-4-gcc",
                "org_id": ORG_A,
                "project_id": PROJ_A1,
                "is_current": True,
                "is_authorised_for_ai": True,
                "clause_no": "8.4",
                "clause_uid": "uid-gcc-8.4",
                "clause_title": "Extension of Time for Completion",
                "text": "The Contractor shall be entitled subject to Sub-Clause 20.1 to an extension of the Time for Completion.",
                "document_id": "doc-gcc",
                "document_type": "GCC",
            },
        ]
    )
    await _add_actor(
        env.db, NO_CLAUSE_READ, NO_CLAUSE_READ, ["dms.claim.view", "dms.claim.edit", "dms.claim.manage"]
    )


def _payload(**extra: Any) -> dict:
    return {
        "project_id": PROJ_A1,
        "claim_type": "eot",
        "period_days": 28,
        "trigger": "awareness",
        "clause_no": "20.1",
        "quote": QUOTE,
        **extra,
    }


def _rid(response: Any) -> str:
    body = response.json()
    return str(body.get("id") or body["_id"])


def _code(response: Any) -> str:
    detail = response.json().get("detail")
    return detail.get("code", "") if isinstance(detail, dict) else ""


def test_a_quote_not_in_the_resolved_clause_is_refused(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            # The period is in the quote, but the quote is not clause 20.1's wording.
            invented = _payload(quote="Notice of any claim shall be given within 28 days of the event.")
            refused = await env.call("POST", "org_admin", SET, json=invented)
            assert refused.status_code == 422, refused.text[:300]
            assert _code(refused) == "notice_quote_not_in_clause"
            # Pointing at the right clause by uid does not help either.
            by_uid = await env.call("POST", "org_admin", SET, json={**invented, "clause_uid": "uid-gcc-20.1"})
            assert by_uid.status_code == 422 and _code(by_uid) == "notice_quote_not_in_clause"
            # Clause 8.4 resolves, but the 20.1 wording is not in it.
            wrong = await env.call("POST", "org_admin", SET, json=_payload(clause_no="8.4"))
            assert wrong.status_code == 422 and _code(wrong) == "notice_quote_not_in_clause"
            assert await env.db.claim_notice_requirements.count_documents({}) == 0

    _run(scenario())


def test_a_resolved_clause_takes_its_provenance_from_the_index(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            typed = _payload(
                clause_no="Sub-Clause 20.1",
                # A curly apostrophe, doubled spaces and different case: the same wording.
                quote=(
                    "THE Contractor\u2019s notice  shall be given to the Engineer, describing the event or "
                    "circumstance giving rise to the claim. The notice shall be given as soon as practicable, "
                    "and not later than 28 days"
                ),
                # Claims the client makes about provenance; the index's win.
                clause_uid="forged-uid",
                document_id="doc-forged",
                document_type="PCC",
                origin="entered",
            )
            created = await env.call("POST", "org_admin", SET, json=typed)
            assert created.status_code == 201, created.text[:300]
            body = created.json()
            assert body["origin"] == "clause_index"
            assert (body["clause_no"], body["document_type"]) == ("20.1", "GCC")
            stored = await env.db.claim_notice_requirements.find_one({"status": "current"})
            assert (stored["clause_uid"], stored["document_id"]) == ("uid-gcc-20.1", "doc-gcc")
            assert stored["verification"] == "extracted_unverified"

            unknown_type = await env.call("POST", "org_admin", SET, json=_payload(claim_type="anything"))
            assert unknown_type.status_code == 422, unknown_type.text[:300]

    _run(scenario())


def test_an_unknown_clause_is_stored_as_entered_from_the_contract(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            claimed = _payload(
                clause_no="99.9",
                quote="Notice of any claim shall be given within 60 days of the event.",
                period_days=60,
                origin="clause_index",
                clause_uid="uid-gcc-99.9",
                document_id="doc-gcc",
                document_type="GCC",
            )
            created = await env.call("POST", "org_admin", SET, json=claimed)
            assert created.status_code == 201, created.text[:300]
            stored = await env.db.claim_notice_requirements.find_one({"status": "current"})
            assert stored["origin"] == "entered_from_contract"
            assert (stored["clause_uid"], stored["document_id"], stored["document_type"]) == (None, None, None)
            assert stored["clause_no"] == "99.9"

    _run(scenario())


def test_the_recorder_cannot_verify_and_a_second_person_can_with_audit(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            created = await env.call("POST", "org_admin", SET, json=_payload())
            assert created.status_code == 201, created.text[:300]
            requirement_id = _rid(created)
            verify = f"{SET}/{requirement_id}/verify"

            own = await env.call("POST", "org_admin", verify)
            assert own.status_code == 403, own.text[:300]
            assert _code(own) == "notice_self_verification"
            assert (await env.db.claim_notice_requirements.find_one({"_id": requirement_id}))["verification"] == (
                "extracted_unverified"
            )

            # Two other people verify at once: both see it verified, one verification is recorded.
            results = await asyncio.gather(
                env.call("POST", "project_admin", verify), env.call("POST", "superadmin", verify)
            )
            assert [item.status_code for item in results] == [200, 200], [item.text[:300] for item in results]
            stored = await env.db.claim_notice_requirements.find_one({"_id": requirement_id})
            assert stored["verification"] == "verified"
            assert stored["verified_by"] and stored["verified_by"] != stored["created_by"]

            events = await env.db.audit_events.find(
                {"resource_id": requirement_id, "action": {"$regex": "^claim\\.notice_requirement\\."}}
            ).to_list(20)
            actions = sorted(event["action"] for event in events)
            assert actions == ["claim.notice_requirement.set", "claim.notice_requirement.verified"], actions
            by_action = {event["action"]: event for event in events}
            assert by_action["claim.notice_requirement.set"]["actor_id"] == stored["created_by"]
            assert by_action["claim.notice_requirement.verified"]["actor_id"] == stored["verified_by"]

            # A replacement is audited with what it replaced; the old record is history.
            replaced = await env.call("POST", "project_admin", SET, json=_payload(period_days=28, trigger="event"))
            assert replaced.status_code == 201, replaced.text[:300]
            event = await env.db.audit_events.find_one(
                {"action": "claim.notice_requirement.set", "resource_id": _rid(replaced)}
            )
            assert event["metadata"]["superseded_id"] == requirement_id
            stale = await env.call("POST", "org_admin", verify)
            assert stale.status_code == 409, stale.text[:300]

    _run(scenario())


def test_without_clause_read_rights_set_and_verify_are_refused(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _seed(env)
            refused = await env.call("POST", NO_CLAUSE_READ, SET, json=_payload())
            assert refused.status_code == 403, refused.text[:300]
            assert await env.db.claim_notice_requirements.count_documents({}) == 0

            created = await env.call("POST", "org_admin", SET, json=_payload())
            assert created.status_code == 201, created.text[:300]
            requirement_id = _rid(created)
            denied = await env.call("POST", NO_CLAUSE_READ, f"{SET}/{requirement_id}/verify")
            assert denied.status_code == 403, denied.text[:300]
            stored = await env.db.claim_notice_requirements.find_one({"_id": requirement_id})
            assert stored["verification"] == "extracted_unverified"
            assert await env.db.audit_events.count_documents({"action": "claim.notice_requirement.verified"}) == 0

    _run(scenario())
