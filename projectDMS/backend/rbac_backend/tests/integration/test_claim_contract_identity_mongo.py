"""4B-18 (D-23, AQ-1, C-05): a claim says which contract it is made under.
Real HTTP, real MongoDB replica set, real RBAC.

Before 4B-18 a claim had no contract, so on a project with several contracts
nothing tied a claim to one of them.

Opt-in: ``CLAIM_BENCH_MONGODB_URI`` (a disposable replica set); run in CI's
real-Mongo step.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from rbac_backend.migrations.v20261122_0001_claim_contract_index import upgrade as apply_m04
from rbac_backend.tests.integration.test_claim_routes_real_deps_mongo import CLAIM, _env
from rbac_backend.tests.integration.test_claim_signals_mongo import RAISE, _hindrance
from rbac_backend.tests.integration.test_variation_relationships_cl2_mongo import ORG_A, ORG_B, PROJ_A1, PROJ_A2

pytestmark = pytest.mark.integration

CLAIMS = "/api/claims"
OPTIONS = "/api/claims/contract-options"


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _code(response: Any) -> str:
    detail = response.json().get("detail")
    return detail.get("code", "") if isinstance(detail, dict) else ""


def _contract(contract_id: str, name: str, *, project: str = PROJ_A1, org: str = ORG_A) -> dict:
    return {
        "_id": f"cm-{org}-{project}-{contract_id}",
        "organization_id": org,
        "project_id": project,
        "contract_id": contract_id,
        "contract_name": name,
    }


async def _contracts(env: Any, *rows: dict) -> None:
    if rows:
        await env.db.contract_master.insert_many(list(rows))


def _new_claim(**fields: Any) -> dict:
    return {"title": "Late access to the depot", "type": "eot", "organization_id": ORG_A, "project_id": PROJ_A1, **fields}


async def _create(env: Any, persona: str = "org_admin", **fields: Any) -> Any:
    return await env.call("POST", persona, CLAIMS, json=_new_claim(**fields))


async def _options(env: Any, persona: str = "org_admin", project: str = PROJ_A1) -> Any:
    return await env.call("GET", persona, OPTIONS, project=project, params={"project_id": project})


MAIN = _contract("primary", "Main works: viaduct and stations")
DEPOT = _contract("pkg-depot", "Depot package")


# ------------------------------------------------------------ on a new claim


def test_with_no_contract_on_the_project_the_claim_is_created_without_one_and_says_so(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            options = await _options(env)
            assert options.status_code == 200, options.text[:200]
            assert options.json() == {"options": [], "selected": None, "must_choose": False}
            created = await _create(env)
            assert created.status_code == 201, created.text[:300]
            assert created.json()["contract_id"] is None and created.json()["contract_label"] is None

    _run(scenario())


def test_the_only_contract_is_selected_without_asking_and_is_shown_by_name(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _contracts(env, MAIN)
            assert (await _options(env)).json() == {
                "options": [{"contract_id": "primary", "label": "Main works: viaduct and stations"}],
                "selected": "primary",
                "must_choose": False,
            }
            created = await _create(env)
            assert created.status_code == 201, created.text[:300]
            body = created.json()
            assert body["contract_id"] == "primary" and body["contract_label"] == "Main works: viaduct and stations"
            stored = await env.db.claims.find_one({"title": "Late access to the depot"})
            assert stored["contract_id"] == "primary"
            listed = await env.call("GET", "org_admin", CLAIMS, params={"project_id": PROJ_A1})
            row = next(item for item in listed.json() if item["title"] == "Late access to the depot")
            assert row["contract_label"] == "Main works: viaduct and stations"

    _run(scenario())


def test_with_several_contracts_one_must_be_chosen_and_nothing_is_guessed(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _contracts(env, MAIN, DEPOT)
            options = (await _options(env)).json()
            assert options["must_choose"] is True and options["selected"] is None
            assert [item["label"] for item in options["options"]] == ["Depot package", "Main works: viaduct and stations"]

            unchosen = await _create(env)
            assert unchosen.status_code == 422 and _code(unchosen) == "claim_contract_required", unchosen.text[:300]
            offered = {item["contract_id"] for item in unchosen.json()["detail"]["options"]}
            assert offered == {"primary", "pkg-depot"}
            assert await env.db.claims.count_documents({"title": "Late access to the depot"}) == 0

            chosen = await _create(env, contract_id="pkg-depot")
            assert chosen.status_code == 201, chosen.text[:300]
            assert chosen.json()["contract_id"] == "pkg-depot" and chosen.json()["contract_label"] == "Depot package"

    _run(scenario())


@pytest.mark.parametrize("named", ["no-such-contract", "pkg-other-project", "pkg-foreign"])
def test_a_contract_that_is_not_this_projects_is_refused(monkeypatch, named: str) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _contracts(
                env,
                MAIN,
                _contract("pkg-other-project", "Another project's contract", project=PROJ_A2),
                # Another organisation's contract that happens to name the same project id.
                _contract("pkg-foreign", "Foreign contract", org=ORG_B),
            )
            refused = await _create(env, contract_id=named)
            assert refused.status_code == 422 and _code(refused) == "claim_contract_not_valid", refused.text[:300]
            # Neither is offered, so the one that is this project's is still the only choice.
            assert [item["contract_id"] for item in (await _options(env)).json()["options"]] == ["primary"]
            assert await env.db.claims.count_documents({"title": "Late access to the depot"}) == 0

    _run(scenario())


def test_someone_who_cannot_read_the_contract_register_is_offered_no_contract(monkeypatch) -> None:
    """D-23: a valid contract is one the person can read. They are not told others exist."""

    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _contracts(env, MAIN, DEPOT)
            # Take the two ways a project admin holds contract-register read away.
            await env.db.roles.update_one(
                {"_id": "projectadmin"},
                {"$pull": {"permissions": {"$in": ["dms.contract.master.view", "projects:read", "dms.admin"]}}},
            )
            options = await _options(env, "project_admin")
            assert options.status_code == 200, options.text[:200]
            assert options.json() == {"options": [], "selected": None, "must_choose": False}
            named = await _create(env, "project_admin", contract_id="primary")
            assert named.status_code == 422 and _code(named) == "claim_contract_not_valid", named.text[:200]
            # The claim can still be raised; it carries no contract and says so, until
            # someone who can read the register gives it one.
            unnamed = await _create(env, "project_admin")
            assert unnamed.status_code == 201, unnamed.text[:200]
            assert unnamed.json()["contract_id"] is None and unnamed.json()["contract_label"] is None

    _run(scenario())


# ------------------------------------------------------- on an existing claim


def test_a_claim_with_no_contract_can_be_given_one_and_then_it_is_fixed(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _contracts(env, MAIN, DEPOT)
            path = f"{CLAIMS}/{CLAIM}"
            before = await env.call("GET", "org_admin", path)
            assert before.json()["contract_id"] is None, "a claim made before 4B-18 has none"

            invalid = await env.call("PUT", "org_admin", path, json={"contract_id": "no-such-contract"})
            assert invalid.status_code == 422 and _code(invalid) == "claim_contract_not_valid"

            given = await env.call("PUT", "org_admin", path, json={"contract_id": "primary"})
            assert given.status_code == 200, given.text[:300]
            assert given.json()["contract_id"] == "primary"

            same = await env.call("PUT", "org_admin", path, json={"contract_id": "primary", "description": "Updated"})
            assert same.status_code == 200 and same.json()["description"] == "Updated"

            moved = await env.call("PUT", "org_admin", path, json={"contract_id": "pkg-depot"})
            assert moved.status_code == 409 and _code(moved) == "claim_contract_fixed", moved.text[:300]
            cleared = await env.call("PUT", "org_admin", path, json={"contract_id": None})
            assert cleared.status_code == 200
            assert (await env.db.claims.find_one({"_id": CLAIM}))["contract_id"] == "primary"

    _run(scenario())


def test_an_edit_that_does_not_mention_the_contract_leaves_a_legacy_claim_without_one(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _contracts(env, MAIN)
            edited = await env.call("PUT", "org_admin", f"{CLAIMS}/{CLAIM}", json={"description": "Wording tidied"})
            assert edited.status_code == 200, edited.text[:300]
            # Nothing is filled in behind the person's back, even with one contract on the project.
            assert edited.json()["contract_id"] is None
            assert "contract_id" not in (await env.db.claims.find_one({"_id": CLAIM}))

    _run(scenario())


# --------------------------------------------------------- raised from a signal


@pytest.mark.parametrize(
    "contracts, source_contract, expected",
    [
        ((), None, None),
        ((MAIN,), None, "primary"),
        ((MAIN, DEPOT), "pkg-depot", "pkg-depot"),
        ((MAIN, DEPOT), None, None),  # several and the record names none: left unidentified, not guessed
        ((MAIN, DEPOT), "not-a-contract", None),
    ],
)
def test_a_claim_raised_from_a_hindrance_takes_the_records_contract_or_none(
    monkeypatch, contracts: tuple, source_contract: Any, expected: Any
) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _contracts(env, *contracts)
            extra = {"contract_id": source_contract} if source_contract else {}
            await env.db.delay_events.insert_one(_hindrance("h-9", **extra))
            raised = await env.call("POST", "org_admin", RAISE, json={"source_type": "hindrance", "source_id": "h-9"})
            assert raised.status_code == 200, raised.text[:300]
            stored = await env.db.claims.find_one({"origin.id": "h-9"})
            assert stored["contract_id"] == expected

    _run(scenario())


# ------------------------------------------------------------- reading, tenancy


def test_the_options_are_for_someone_who_can_see_claims_on_that_project(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            await _contracts(env, MAIN)
            for persona in ("foreign_org_admin", "other_project_admin"):
                refused = await _options(env, persona)
                assert refused.status_code in (400, 403, 404), (persona, refused.status_code)
            anonymous = await env.client.get(OPTIONS, params={"project_id": PROJ_A1})
            assert anonymous.status_code == 401

    _run(scenario())


def test_the_index_migration_changes_no_claim(monkeypatch) -> None:
    async def scenario() -> None:
        async with _env(monkeypatch) as env:
            before = await env.db.claims.find_one({"_id": CLAIM})
            dry = await apply_m04(env.db, True)
            assert [op["name"] for op in dry.operations] == ["claim_scope_contract"]
            assert "claim_scope_contract" not in await env.db.claims.index_information()
            applied = await apply_m04(env.db, False)
            assert not applied.warnings
            assert "claim_scope_contract" in await env.db.claims.index_information()
            await apply_m04(env.db, False)  # repeatable
            assert await env.db.claims.find_one({"_id": CLAIM}) == before

    _run(scenario())
