"""4B-31 (C-22): determination letters and requests for information, pre-filled.
Real HTTP, real MongoDB replica set, real RBAC.

"Issue determination letter" and "Request information" open the Letter Bench
with the determination's findings and its information items, each with its
deadline and what follows if it is not supplied. The plan's proof: the Letter
Bench board carries the findings and the items.

- a request for information starts from a determination that asks for something,
  draft or approved; the board carries every item, earliest deadline first;
- a determination letter starts only from an approved determination, and only
  from the latest revision; the board carries each head's result and figures;
- the letter is written as the party that made the determination;
- the caller must be able to read the claim; opening is idempotent;
- neither board is offered back as a potential-claim signal.

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set); run in CI's
real-Mongo step.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict

import pytest

from rbac_backend.tests.integration.test_claim_privileged_rbac_mongo import _add_actor
from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import CLAIM, _env
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, PROJ_A1

pytestmark = pytest.mark.integration

BASE = f"/api/claims/{CLAIM}/determinations"
OPEN = "/api/letters/bench/open"
AUTHORITY = ["dms.claim.view", "dms.claim.edit", "dms.claim.determine"]
DRAFTER = ["dms.claim.view", "drafting.request.create"]
ITEMS = [
    {"item": "Daily site records for March 2026", "deadline": "2026-12-20T00:00:00Z",
     "consequence": "The head is determined on the records already supplied."},
    {"item": "The handover correspondence", "deadline": "2026-12-05T00:00:00Z", "consequence": None},
    {"item": "Revised programme", "deadline": None, "consequence": None},
]


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _code(response: Any) -> str:
    detail = response.json().get("detail")
    return detail.get("code", "") if isinstance(detail, dict) else ""


async def _ok(response: Any) -> Dict[str, Any]:
    assert response.status_code == 200, (response.status_code, response.text[:400])
    return response.json()


async def _prepared(env: Any, monkeypatch: Any) -> Dict[str, Any]:
    from rbac_backend.core.config import settings
    from rbac_backend.services.letter_drafting.bench import LetterBenchService

    monkeypatch.setattr(settings, "LETTER_BENCH_ROLLOUT_MODE", "primary")

    async def _no_analysis(self: Any, letter_id: str, current_user: Any) -> None:
        return None  # the first analysis calls the model; what is under test is what open carries

    monkeypatch.setattr(LetterBenchService, "run_dissect", _no_analysis)
    # Letters are drafting: the organisation needs a plan that includes it.
    await env.db.subscriptions.update_many({"organization_id": ORG_A}, {"$set": {"plan_code": "claims_commercial_desk"}})
    await env.db.projects.update_one({"_id": PROJ_A1}, {"$set": {"contractual_capacity": "engineer"}})
    for persona in ("author", "reviewer"):
        await _add_actor(env.db, persona, f"role-{persona}", AUTHORITY)
    await _add_actor(env.db, "drafter", "role-drafter", DRAFTER)
    await _add_actor(env.db, "blind", "role-blind", ["drafting.request.create"])
    await _ok(await env.call("POST", "org_admin", f"/api/claims/{CLAIM}/bench/open"))
    view = await _ok(await env.call("POST", "author", BASE, json={"origin": "authored"}))
    det = view["determination"]
    for finding in det["heads"]:
        view = await _ok(await env.call(
            "PUT", "author", f"{BASE}/{det['_id']}/heads/{finding['head_id']}",
            json={"version": view["determination"]["version"], "result": "partial", "days": 10, "amount": 1250000.5,
                  "findings": "Access was given ten days late; the rest is not shown.",
                  "information_required": ITEMS},
        ))
    return view["determination"]


async def _open(env: Any, persona: str, purpose: str, det_id: str) -> Any:
    return await env.call("POST", persona, OPEN, json={"trigger_type": purpose, "trigger_id": det_id})


def test_rfi_and_determination_letters_carry_the_findings_items_and_deadlines(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            det = await _prepared(env, monkeypatch)
            head_id = det["heads"][0]["head_id"]

            # Not approved yet: no determination letter.
            early = await _open(env, "drafter", "determination", det["_id"])
            assert early.status_code == 409 and _code(early) == "determination_letter_not_approved", early.text[:300]

            # A request for information from the draft.
            rfi = await _ok(await _open(env, "drafter", "information_request", det["_id"]))
            assert rfi["created"] is True
            board = await env.db.letter_position_boards.find_one({"letter_id": rfi["letter_id"]})
            trigger = board["trigger"]
            assert trigger["type"] == "information_request" and trigger["id"] == det["_id"]
            assert trigger["claim_id"] == CLAIM and trigger["determination_revision"] == 1
            assert [entry["item"] for entry in trigger["information_items"]] == [
                "The handover correspondence", "Daily site records for March 2026", "Revised programme",
            ], "earliest deadline first, undated last"
            assert [entry["deadline"] for entry in trigger["information_items"]] == ["2026-12-05", "2026-12-20", None]
            assert trigger["information_items"][1]["consequence"].startswith("The head is determined")
            assert all(entry["head_id"] == head_id for entry in trigger["information_items"])
            assert "heads" not in trigger, "a request for information carries the items, not the findings"
            assert board["role"] == "engineer", "written as the party that made the determination"
            assert "by 2026-12-05" in board["incoming_text"] and "no date set" in board["incoming_text"]
            letter = await env.db.letters.find_one({"_id": __import__("bson").ObjectId(rfi["letter_id"])})
            assert letter["subject"].startswith("Request for information:")
            assert letter["strategy_role"] == "engineer"

            # Opening again returns the same letter.
            again = await _ok(await _open(env, "drafter", "information_request", det["_id"]))
            assert again["created"] is False and again["letter_id"] == rfi["letter_id"]

            # Approve, then the determination letter.
            det = (await _ok(await env.call("POST", "author", f"{BASE}/{det['_id']}/submit",
                                            json={"version": det["version"], "reviewer_id": "user-reviewer"})))["determination"]
            await _ok(await env.call("POST", "reviewer", f"{BASE}/{det['_id']}/approve", json={"version": det["version"]}))
            issued = await _ok(await _open(env, "drafter", "determination", det["_id"]))
            assert issued["letter_id"] != rfi["letter_id"]
            board = await env.db.letter_position_boards.find_one({"letter_id": issued["letter_id"]})
            heads = board["trigger"]["heads"]
            assert heads and heads[0]["result"] == "partial" and heads[0]["days"] == 10
            assert heads[0]["amount"] == 1250000.5 and heads[0]["findings"].startswith("Access was given")
            assert len(board["trigger"]["information_items"]) == 3
            assert "10 days extension of time" in board["incoming_text"]
            assert "INR 12,50,000.50" in board["incoming_text"]
            assert "Entitlement established in part" in board["incoming_text"]

            # Neither board is a potential-claim signal, even routed to the claims skill.
            from rbac_backend.services.claim_signals import ClaimSignalService
            from rbac_backend.services.skills.system_skills import CLAIMS_SKILL_KEY

            await env.db.letter_position_boards.update_many(
                {}, {"$set": {"chain": [{"skill_key": CLAIMS_SKILL_KEY}]}}
            )
            signals = await ClaimSignalService(env.db).list(
                organization_id=board["organization_id"], hindrance_query=None,
                board_query={"organization_id": board["organization_id"]},
            )
            assert [s for s in signals if s.source_type == "letter_bench"] == []

    _run(scenario())


def test_letters_are_refused_without_the_claim_or_from_a_superseded_revision(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            det = await _prepared(env, monkeypatch)

            # Cannot read the claim: refused, and no letter or board is made.
            blind = await _open(env, "blind", "information_request", det["_id"])
            assert blind.status_code == 403, blind.text[:300]
            unknown = await _open(env, "drafter", "information_request", "no-such-determination")
            assert unknown.status_code == 404, unknown.text[:300]
            assert await env.db.letter_position_boards.count_documents({}) == 0
            assert await env.db.letters.count_documents({}) == 0

            # The project now acts for the Employer: the Engineer's determination is not issued here.
            await env.db.projects.update_one({"_id": PROJ_A1}, {"$set": {"contractual_capacity": "employer"}})
            moved = await _open(env, "drafter", "information_request", det["_id"])
            assert moved.status_code == 409 and _code(moved) == "determination_capacity_changed", moved.text[:300]
            await env.db.projects.update_one({"_id": PROJ_A1}, {"$set": {"contractual_capacity": "engineer"}})

            det = (await _ok(await env.call("POST", "author", f"{BASE}/{det['_id']}/submit",
                                            json={"version": det["version"], "reviewer_id": "user-reviewer"})))["determination"]
            await _ok(await env.call("POST", "reviewer", f"{BASE}/{det['_id']}/approve", json={"version": det["version"]}))
            await _ok(await env.call("POST", "author", f"{BASE}/{det['_id']}/revise", json={}))
            for purpose in ("determination", "information_request"):
                stale = await _open(env, "drafter", purpose, det["_id"])
                assert stale.status_code == 409 and _code(stale) == "determination_letter_superseded", stale.text[:300]
            assert await env.db.letter_position_boards.count_documents({}) == 0

    _run(scenario())


def test_the_drafter_who_opened_a_board_reads_it_read_only_and_nobody_else_gains_a_read(monkeypatch) -> None:
    """JG-2. A client drafter opens a Letter Bench from a determination (``drafting.request.create``)
    but reading a board needs ``drafting.request.view``, which a client user holds only through a
    staff expert allocation: they were refused the board they had just made. The opener now reads
    it, read-only, while they can still request drafting in its scope. ``drafting.request.view`` is
    not widened, no write is opened, and nobody else gains a read."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            det = await _prepared(env, monkeypatch)
            await _add_actor(env.db, "second_drafter", "role-second-drafter", DRAFTER)
            opened = await _ok(await _open(env, "drafter", "information_request", det["_id"]))
            bench = f"/api/letters/{opened['letter_id']}/bench"

            read = await _ok(await env.call("GET", "drafter", bench))
            assert read["read_only"] is True, "the opener reads without drafting rights"
            items = read["board"]["trigger"]["information_items"]
            assert [entry["item"] for entry in items][0] == "The handover correspondence"
            skills = await env.call("GET", "drafter", f"{bench}/skills")
            assert skills.status_code == 200, skills.text[:300]

            # Reading is all the opener gains: every write still needs drafting rights.
            for method, path, body in (
                ("POST", f"{bench}/dissect", None),
                ("PUT", f"{bench}/settings", {"board_version": read["board"]["board_version"]}),
            ):
                write = await env.call(method, "drafter", path, json=body)
                assert write.status_code == 403, (path, write.status_code, write.text[:300])

            # Someone else who could open it (and is told its letter id) does not gain a read.
            again = await _ok(await _open(env, "second_drafter", "information_request", det["_id"]))
            assert again["letter_id"] == opened["letter_id"] and again["created"] is False
            for persona in ("second_drafter", "blind"):
                refused = await env.call("GET", persona, bench)
                assert refused.status_code == 403, (persona, refused.status_code, refused.text[:300])
            foreign = await env.call("GET", "foreign_org_admin", bench)
            assert foreign.status_code in (403, 404), foreign.text[:300]
            anonymous = await env.client.get(bench)
            assert anonymous.status_code == 401

            # The read lasts only while the opener may still request drafting there.
            await env.db.roles.update_one({"_id": "role-drafter"}, {"$set": {"permissions": ["dms.claim.view"]}})
            lapsed = await env.call("GET", "drafter", bench)
            assert lapsed.status_code == 403, lapsed.text[:300]

    _run(scenario())
