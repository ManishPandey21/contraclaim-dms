"""4B-13 (C-12): potential-claim signals, over real HTTP, MongoDB and RBAC.

Before 4B-13:
- a notice letter drafted from claim CLM-7 is routed to the claims skill, so its
  Letter Bench was listed as "a letter may be a claim not yet raised", and
  accepting it created a second claim;
- raising re-read nothing: a hindrance taken off the critical path (or moved to
  the Contractor, or archived) after the list was read still became a claim;
- for a board with no organisation, the duplicate lookup searched ``""`` while
  the create stored the user's organisation, so a second accept made a second
  claim (or, with the unique index, a 500).

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set); run in CI's
real-Mongo step.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any

import pytest

from rbac_backend.services.skills.system_skills import CLAIMS_SKILL_KEY, EOT_SKILL_KEY
from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import CLAIM, _env
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import (
    ORG_A,
    PROJ_A1,
)

pytestmark = pytest.mark.integration

SIGNALS = "/api/claims/bench/signals"
RAISE = "/api/claims/bench/potential"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _board(
    letter_id: str,
    *,
    trigger: Any = None,
    organization_id: Any = ORG_A,
    skill: str = CLAIMS_SKILL_KEY,
) -> dict:
    return {
        "letter_id": letter_id,
        "organization_id": organization_id,
        "project_id": PROJ_A1,
        "role": "contractor",
        "chain": [{"skill_key": skill}],
        "trigger": trigger,
        "incoming_meta": {
            "letter_no": f"IN/{letter_id}",
            "subject": "Access to Site",
            "letter_date": datetime(2026, 9, 1),
        },
        "stage": "dissecting",
    }


def _hindrance(_id: str, **extra: Any) -> dict:
    return {
        "_id": _id,
        "hindrance_ref": f"H-{_id}",
        "title": "Right of way not handed over",
        "start_date": datetime(2026, 9, 1),
        "critical_path_impact": True,
        "responsibility": "employer",
        "archived_at": None,
        "organization_id": ORG_A,
        "project_id": PROJ_A1,
        **extra,
    }


#: RU-1 (2026-11-17): letter signals need dms.claim.signal.view by explicit grant; raising a claim
#: from one also needs the letter itself readable.
SIGNAL_VIEWER = ["dms.claim.view", "dms.claim.create", "dms.document.view", "dms.claim.signal.view"]


async def _letters(env: Any, *letter_ids: str) -> None:
    """The incoming letters behind the boards, as documents of the project."""
    from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import _document

    await env.db.documents.insert_many([
        _document(letter_id, organization_id=ORG_A, project_id=PROJ_A1, upload_type="incoming",
                  subject="Access to Site", letter_no=f"IN/{letter_id}", sender="Engineer")
        for letter_id in letter_ids
    ])


async def _persona(env: Any, persona: str, permissions: list) -> None:
    """A project user (the built-in key build_scope_query needs for row access) with one
    custom role. The test organisation's projectuser role is edited to carry no document
    access, so document access comes only from the custom role - as a tenant may set it."""
    await env.db.roles.update_one({"_id": "projectuser"}, {"$pull": {"permissions": "dms.document.view"}})
    await env.db.roles.insert_one({"_id": f"role-{persona}", "name": f"role-{persona}", "permissions": permissions,
                                   "scope": "organization", "organization_id": ORG_A, "is_active": True})
    await env.db.users.insert_one({"_id": f"user-{persona}", "email": f"{persona}@example.com", "username": persona,
                                   "disabled": False, "account_type": "client_user", "organizations": [],
                                   "roles": ["projectuser", f"role-{persona}"], "organization_id": ORG_A,
                                   "projects": [PROJ_A1]})


async def _signal_viewer(env: Any) -> None:
    await _persona(env, "signals", SIGNAL_VIEWER)


async def _as_superadmin(env: Any, method: str, path: str, org: Any = ORG_A, **kwargs: Any) -> Any:
    """Letter Bench boards are read with ``drafting.request.view``, which a tenant role holds
    only through a drafting-expert allocation; Super Admin reads them."""
    headers = env.headers("superadmin")
    if org:
        headers["X-Org-Id"] = org
    else:
        headers.pop("X-Proj-Id", None)  # nothing selected in the navbar
    return await env.client.request(method, path, headers=headers, **kwargs)


def _claim_id(response: Any) -> str:
    claim = response.json()["claim"]
    return str(claim.get("id") or claim.get("_id"))


def _code(response: Any) -> str:
    body = response.json()
    detail = body.get("detail", body)
    return (detail.get("code") if isinstance(detail, dict) else None) or body.get("code") or ""


def test_a_notice_letter_drafted_from_a_claim_is_not_a_signal_and_cannot_be_raised(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            claim_trigger = {
                "type": "claim",
                "id": CLAIM,
                "label": "Claim CLM-001",
                "claim_type": "eot",
            }
            await env.db.letter_position_boards.insert_many(
                [
                    _board("L-from-claim", trigger=claim_trigger, skill=EOT_SKILL_KEY),
                    _board("L-incoming"),
                    _board(
                        "L-variation",
                        trigger={
                            "type": "variation",
                            "id": "v1",
                            "label": "Variation 1",
                        },
                    ),
                ]
            )
            await _letters(env, "L-from-claim", "L-incoming", "L-variation")
            await _signal_viewer(env)
            listed = await env.call("GET", "signals", SIGNALS)
            assert listed.status_code == 200, listed.text[:300]
            ids = {item["source_id"] for item in listed.json() if item["source_type"] == "letter_bench"}
            assert ids == {"L-incoming", "L-variation"}, ids

            before = await env.db.claims.count_documents({})
            refused = await env.call(
                "POST",
                "signals",
                RAISE,
                json={"source_type": "letter_bench", "source_id": "L-from-claim"},
            )
            assert refused.status_code == 422, refused.text[:300]
            assert _code(refused) == "signal_not_eligible"
            assert await env.db.claims.count_documents({}) == before

    _run(scenario())


@pytest.mark.parametrize(
    "change",
    [
        {"critical_path_impact": False},
        {"responsibility": "contractor"},
        {"archived_at": datetime(2026, 10, 1)},
    ],
    ids=["off-critical-path", "contractor-risk", "archived"],
)
def test_a_hindrance_that_is_no_longer_eligible_is_refused_on_raise(monkeypatch, change: dict) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.delay_events.insert_one(_hindrance("h-1"))
            listed = await env.call("GET", "org_admin", SIGNALS)
            assert "h-1" in {item["source_id"] for item in listed.json()}, listed.text[:300]
            # The hindrance changes after the list was read.
            await env.db.delay_events.update_one({"_id": "h-1"}, {"$set": change})
            refused = await env.call(
                "POST",
                "org_admin",
                RAISE,
                json={"source_type": "hindrance", "source_id": "h-1"},
            )
            assert refused.status_code == 422, refused.text[:300]
            assert _code(refused) == "signal_not_eligible"
            assert await env.db.claims.count_documents({"origin.id": "h-1"}) == 0

    _run(scenario())


def test_an_eligible_hindrance_is_raised_once(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.delay_events.insert_one(_hindrance("h-2", responsibility="concurrent"))
            first = await env.call(
                "POST",
                "org_admin",
                RAISE,
                json={"source_type": "hindrance", "source_id": "h-2"},
            )
            assert first.status_code == 200, first.text[:300]
            assert first.json()["created"] is True
            second = await env.call(
                "POST",
                "project_admin",
                RAISE,
                json={"source_type": "hindrance", "source_id": "h-2"},
            )
            assert second.status_code == 200, second.text[:300]
            assert second.json()["created"] is False
            assert _claim_id(second) == _claim_id(first)
            assert await env.db.claims.count_documents({"origin.id": "h-2"}) == 1
            listed = await env.call("GET", "org_admin", SIGNALS)
            assert "h-2" not in {item["source_id"] for item in listed.json()}

    _run(scenario())


def test_two_accepts_of_an_organisation_less_board_return_one_claim(
    monkeypatch,
) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.letter_position_boards.insert_many(
                [
                    _board("L-no-org", organization_id=None),
                    _board("L-no-org-2", organization_id=None),
                ]
            )
            await _letters(env, "L-no-org", "L-no-org-2")
            await _signal_viewer(env)
            # The claim takes the raiser's organisation, and a second accept finds it.
            payload = {"source_type": "letter_bench", "source_id": "L-no-org"}
            first = await env.call("POST", "signals", RAISE, json=payload)
            assert first.status_code == 200, first.text[:300]
            second = await env.call("POST", "signals", RAISE, json=payload)
            assert second.status_code == 200, second.text[:300]
            assert (first.json()["created"], second.json()["created"]) == (True, False)
            assert _claim_id(second) == _claim_id(first)
            rows = await env.db.claims.find({"origin.id": "L-no-org"}).to_list(10)
            assert len(rows) == 1 and rows[0]["organization_id"] == ORG_A

            # Super Admin with nothing selected: refused (the selection gate, or RU-1: no grant).
            payload = {"source_type": "letter_bench", "source_id": "L-no-org-2"}
            unscoped = await _as_superadmin(env, "POST", RAISE, org=None, json=payload)
            assert unscoped.status_code in (400, 403), unscoped.text[:300]
            assert await env.db.claims.count_documents({"origin.id": "L-no-org-2"}) == 0

            # Two concurrent accepts, then a third: still one claim. The origin unique index is
            # what production has (migration 20261116_0001); apply the real migration.
            from rbac_backend.migrations.v20261116_0001_claim_bench_indexes import (
                upgrade,
            )

            await upgrade(env.db, False)
            results = await asyncio.gather(*(env.call("POST", "signals", RAISE, json=payload) for _ in range(2)))
            assert all(item.status_code == 200 for item in results), [item.text[:300] for item in results]
            again = await env.call("POST", "signals", RAISE, json=payload)
            assert again.status_code == 200 and again.json()["created"] is False, again.text[:300]
            assert sorted(item.json()["created"] for item in results) == [False, True]
            assert len({_claim_id(item) for item in [*results, again]}) == 1
            assert await env.db.claims.count_documents({"origin.id": "L-no-org-2"}) == 1

    _run(scenario())


def test_signal_routes_are_off_when_the_bench_is_off(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch, mode="off") as env:
            await env.db.delay_events.insert_one(_hindrance("h-4"))
            assert (await env.call("GET", "org_admin", SIGNALS)).status_code == 404
            raised = await env.call(
                "POST",
                "org_admin",
                RAISE,
                json={"source_type": "hindrance", "source_id": "h-4"},
            )
            assert raised.status_code == 404, raised.text[:200]
            assert await env.db.claims.count_documents({"origin.id": "h-4"}) == 0

    _run(scenario())


def test_ru1_letter_signals_need_the_explicit_grant_and_a_readable_letter(monkeypatch) -> None:
    """RU-1 (owner decision 2026-11-17): dms.claim.signal.view, explicitly granted, project-scoped;
    no default role, never through Super Admin, a wildcard or dms.admin. A neutral title where the
    person may know the letter exists but not read it; nothing where the letter no longer resolves."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await env.db.letter_position_boards.insert_many([_board("L-open"), _board("L-gone")])
            await _letters(env, "L-open")  # L-gone's letter no longer exists
            await _signal_viewer(env)
            await _persona(env, "signals_blind", ["dms.claim.view", "dms.claim.create", "dms.claim.signal.view"])

            def letters(response: Any) -> list:
                assert response.status_code == 200, response.text[:300]
                return [item for item in response.json() if item["source_type"] == "letter_bench"]

            # No grant, no letter signals: org admin (dms.admin) and Super Admin included.
            assert letters(await env.call("GET", "org_admin", SIGNALS)) == []
            assert letters(await env.call("GET", "project_admin", SIGNALS)) == []
            assert letters(await _as_superadmin(env, "GET", SIGNALS)) == []

            full = letters(await env.call("GET", "signals", SIGNALS))
            assert [item["source_id"] for item in full] == ["L-open"]
            assert full[0]["title"] == "Access to Site" and full[0]["label"] == "Letter IN/L-open"

            blind_response = await env.call("GET", "signals_blind", SIGNALS)
            (neutral,) = letters(blind_response)
            assert neutral["source_id"] == "" and neutral["label"] == "Incoming letter"
            assert "cannot open the letter" in neutral["title"]
            for secret in ("Access to Site", "IN/L-open", "L-open", "L-gone"):
                assert secret not in blind_response.text, secret

            refused = await env.call("POST", "signals_blind", RAISE, json={"source_type": "letter_bench", "source_id": "L-open"})
            assert refused.status_code == 403, refused.text[:300]
            assert await env.db.claims.count_documents({"origin.id": "L-open"}) == 0
            raised = await env.call("POST", "signals", RAISE, json={"source_type": "letter_bench", "source_id": "L-open"})
            assert raised.status_code == 200, raised.text[:300]

    _run(scenario())
