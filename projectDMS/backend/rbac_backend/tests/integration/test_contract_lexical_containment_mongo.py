"""Real MongoDB proof of lexical candidate containment.

Owned by implementation ticket 09. Opt-in, like the other tracer suites here.

Two defects are deliberately kept apart:

* **Publication authority ordering is already correct.** The blocked-document
  filter runs before fusion, ranking, ``total_count`` and pagination. This ticket
  does not touch it and must not "fix" it again.
* **Candidate capacity is the real defect.** Each source fetches under its own
  bound *before* applicability exists, so N high-scoring inapplicable clauses can
  fill the window and starve an applicable one that would have ranked just
  outside it. Authority filtering never gets to see the survivor, because it was
  never fetched.

Why real Mongo: the starvation is an interaction between ``$sort`` and
``$limit``. A fake collection that ignores the limit returns every row and the
test passes whether or not the fix exists — the failure mode cannot even be
expressed. This suite drives the **real** ``ContractService._lexical_candidates``
aggregation against a real server.

Every assertion reads the returned candidates. Asserting that a ``$match`` stage
contains ``document_id`` would pass while the predicate sat after the limit.

T09 adds capability only. It does not switch contract search into evidence mode
— ticket 12 is the sole cutover, so no state exists where the three sources
search different universes.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from typing import Any, Dict, List, Optional

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from rbac_backend.services.contract_lexical_containment import (
    EvidenceScopeUnbounded,
    compose_evidence_match_stage,
    lexical_candidates_for_evidence,
)

MONGODB_URI_ENV = "CONTRACT_MASTER_MONGODB_URI"

ORG = "org-t09"
PROJECT_A = "project-a"
PROJECT_B = "project-b"
CANDIDATE_LIMIT = 5


def _uri() -> str:
    uri = os.getenv(MONGODB_URI_ENV)
    if not uri:
        pytest.skip(f"set {MONGODB_URI_ENV} to a test-only MongoDB instance")
    return uri


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


class _Fixture:
    def __init__(self, client: AsyncIOMotorClient, name: str) -> None:
        self.client = client
        self.db = client[name]
        self.name = name
        self.vectors = self.db.document_vectors


async def _fresh() -> _Fixture:
    client = AsyncIOMotorClient(_uri(), serverSelectionTimeoutMS=15000)
    return _Fixture(client, f"cm_t09_{uuid.uuid4().hex[:12]}")


async def _drop(fixture: _Fixture) -> None:
    await fixture.client.drop_database(fixture.name)
    fixture.client.close()


def _chunk(
    document_id: str,
    *,
    clause_number: str,
    text: str,
    project_id: str = PROJECT_A,
    contract_id: str = "primary",
    is_current: bool = True,
    is_authorised_for_ai: bool = True,
) -> Dict[str, Any]:
    return {
        "_id": uuid.uuid4().hex,
        "uploadType": "contract",
        "organization_id": ORG,
        "project_id": project_id,
        # Deliberately present and deliberately NOT trusted: no candidate store
        # is authoritative for contract identity.
        "contract_id": contract_id,
        "document_id": document_id,
        "clause_number": clause_number,
        "clause_start_position": 0,
        "text": text,
        "text_enriched": text,
        "is_current": is_current,
        "is_authorised_for_ai": is_authorised_for_ai,
        "createdAt": "2026-01-01",
    }


async def _seed(fixture: _Fixture, rows: List[Dict[str, Any]]) -> None:
    await fixture.vectors.insert_many(rows)


def _base_match() -> Dict[str, Any]:
    return {"uploadType": "contract", "organization_id": ORG}


async def _candidates(
    fixture: _Fixture,
    *,
    eligible: Optional[List[str]],
    match_stage: Optional[Dict[str, Any]] = None,
    limit: int = CANDIDATE_LIMIT,
) -> List[Dict[str, Any]]:
    return await lexical_candidates_for_evidence(
        fixture.vectors,
        match_stage=match_stage if match_stage is not None else _base_match(),
        regex="extension|time|clause",
        candidate_limit=limit,
        eligible_document_ids=eligible,
    )


def _returned_documents(candidates: List[Dict[str, Any]]) -> set:
    return {c.get("document_id") for c in candidates}


# --------------------------------------------------------------------------- #
# L15-01 — the starvation tracer
# --------------------------------------------------------------------------- #


def test_l15_01_applicable_clause_survives_higher_scoring_inapplicable_ones() -> None:
    """The load-bearing physical proof.

    Six inapplicable clauses match the query many times each; one applicable
    clause matches once. With ``candidate_limit = 5`` the applicable clause
    cannot win on score — it survives only because the ineligible ones never
    entered the candidate window.
    """

    async def scenario() -> None:
        fixture = await _fresh()
        try:
            noisy = [
                _chunk(
                    f"doc-noise-{i}",
                    clause_number=f"{i}.1",
                    # repeated terms => higher regex match count => higher score
                    text="extension of time clause extension of time clause extension time",
                )
                for i in range(6)
            ]
            applicable = _chunk(
                "doc-applicable",
                clause_number="8.4",
                text="extension",  # deliberately the weakest scorer
            )
            await _seed(fixture, noisy + [applicable])

            # Sanity, through the REAL production pipeline: without containment
            # the applicable clause IS starved. Driving ContractService here
            # rather than passing an empty eligible set proves the starvation
            # exists in the live path, not merely in this module.
            from rbac_backend.services.contract_service import ContractService

            service = ContractService.__new__(ContractService)
            unbounded = await service._lexical_candidates(
                fixture.vectors, _base_match(), "extension|time|clause", CANDIDATE_LIMIT
            )
            assert len(unbounded) == CANDIDATE_LIMIT
            assert "doc-applicable" not in _returned_documents(unbounded), (
                "fixture is wrong: the applicable clause was not actually starved, "
                "so this tracer would pass without the fix"
            )

            # With containment it survives.
            contained = await _candidates(fixture, eligible=["doc-applicable"])
            assert _returned_documents(contained) == {"doc-applicable"}
        finally:
            await _drop(fixture)

    _run(scenario())


def test_ineligible_documents_never_consume_candidate_capacity() -> None:
    async def scenario() -> None:
        fixture = await _fresh()
        try:
            rows = [
                _chunk(f"doc-{i}", clause_number=f"{i}.1", text="clause clause clause")
                for i in range(10)
            ]
            await _seed(fixture, rows)

            contained = await _candidates(fixture, eligible=["doc-3", "doc-7"])
            assert _returned_documents(contained) == {"doc-3", "doc-7"}
            assert len(contained) <= CANDIDATE_LIMIT
        finally:
            await _drop(fixture)

    _run(scenario())


# --------------------------------------------------------------------------- #
# L15-03 — contract isolation comes from the eligible set, not from the rows
# --------------------------------------------------------------------------- #


def test_l15_03_sibling_contract_in_the_same_project_is_isolated() -> None:
    async def scenario() -> None:
        fixture = await _fresh()
        try:
            await _seed(
                fixture,
                [
                    _chunk("doc-c1", clause_number="8.4", text="extension of time", contract_id="C1"),
                    _chunk("doc-c2", clause_number="8.4", text="extension of time", contract_id="C2"),
                ],
            )
            contained = await _candidates(fixture, eligible=["doc-c1"])
            assert _returned_documents(contained) == {"doc-c1"}
        finally:
            await _drop(fixture)

    _run(scenario())


def test_l15_03_same_contract_id_across_projects_is_isolated() -> None:
    """`primary` in two projects are two different contracts."""

    async def scenario() -> None:
        fixture = await _fresh()
        try:
            await _seed(
                fixture,
                [
                    _chunk("doc-a", clause_number="8.4", text="extension of time", project_id=PROJECT_A),
                    _chunk("doc-b", clause_number="8.4", text="extension of time", project_id=PROJECT_B),
                ],
            )
            contained = await _candidates(fixture, eligible=["doc-a"])
            assert _returned_documents(contained) == {"doc-a"}
        finally:
            await _drop(fixture)

    _run(scenario())


def test_row_contract_id_is_never_the_isolation_mechanism() -> None:
    """A row claiming contract C1 is not eligible unless the canonical set says so.

    No candidate store is authoritative for contract identity, so a stored
    ``contract_id`` must not be able to admit anything.
    """

    async def scenario() -> None:
        fixture = await _fresh()
        try:
            await _seed(
                fixture,
                [_chunk("doc-liar", clause_number="8.4", text="extension of time", contract_id="C1")],
            )
            contained = await _candidates(fixture, eligible=[])
            assert contained == []
        finally:
            await _drop(fixture)

    _run(scenario())


# --------------------------------------------------------------------------- #
# L15-05 — empty eligibility
# --------------------------------------------------------------------------- #


def test_l15_05_empty_eligible_set_issues_no_query_and_returns_nothing() -> None:
    async def scenario() -> None:
        fixture = await _fresh()
        try:
            await _seed(
                fixture,
                [_chunk(f"doc-{i}", clause_number=f"{i}.1", text="clause") for i in range(3)],
            )

            class _Exploding:
                def aggregate(self, *a: Any, **k: Any):
                    raise AssertionError("a query was issued for an empty eligible set")

            result = await lexical_candidates_for_evidence(
                _Exploding(),
                match_stage=_base_match(),
                regex="clause",
                candidate_limit=CANDIDATE_LIMIT,
                eligible_document_ids=[],
            )
            assert result == []
        finally:
            await _drop(fixture)

    _run(scenario())


def test_empty_eligible_set_does_not_widen_to_everything() -> None:
    async def scenario() -> None:
        fixture = await _fresh()
        try:
            await _seed(
                fixture,
                [_chunk(f"doc-{i}", clause_number=f"{i}.1", text="clause") for i in range(3)],
            )
            assert await _candidates(fixture, eligible=[]) == []
        finally:
            await _drop(fixture)

    _run(scenario())


# --------------------------------------------------------------------------- #
# Composition is intersection, never replacement
# --------------------------------------------------------------------------- #


def test_caller_document_filter_is_intersected_not_replaced() -> None:
    async def scenario() -> None:
        fixture = await _fresh()
        try:
            await _seed(
                fixture,
                [
                    _chunk("doc-1", clause_number="8.4", text="extension of time"),
                    _chunk("doc-2", clause_number="8.4", text="extension of time"),
                ],
            )
            caller = {**_base_match(), "$or": [{"document_id": "doc-2"}, {"upload_id": "doc-2"}]}

            # Caller wants doc-2; eligibility allows only doc-1 => intersection is empty.
            contained = await _candidates(fixture, eligible=["doc-1"], match_stage=caller)
            assert contained == [], "the eligible set replaced the caller filter instead of intersecting"

            # Caller wants doc-2 and it is eligible => it survives.
            both = await _candidates(fixture, eligible=["doc-1", "doc-2"], match_stage=caller)
            assert _returned_documents(both) == {"doc-2"}
        finally:
            await _drop(fixture)

    _run(scenario())


def test_composition_does_not_mutate_the_callers_match_stage() -> None:
    caller = {"uploadType": "contract", "organization_id": ORG}
    before = dict(caller)
    compose_evidence_match_stage(caller, ["doc-1"])
    assert caller == before


# --------------------------------------------------------------------------- #
# DEBT-14 — evidence mode always carries an organisation constraint
# --------------------------------------------------------------------------- #


def test_debt_14_evidence_mode_refuses_a_match_stage_without_an_organisation() -> None:
    """Generic search omits the organisation predicate for global actors.

    That is acceptable for a global view and unacceptable for contract evidence:
    a guard that exists only as a side effect of another guard is one refactor
    away from vanishing.
    """
    with pytest.raises(EvidenceScopeUnbounded):
        compose_evidence_match_stage({"uploadType": "contract"}, ["doc-1"])


def test_debt_14_blank_organisation_is_also_refused() -> None:
    with pytest.raises(EvidenceScopeUnbounded):
        compose_evidence_match_stage({"uploadType": "contract", "organization_id": ""}, ["doc-1"])


# --------------------------------------------------------------------------- #
# C08-11 / DEBT-04 — currency and AI-authorisation filters are applied
# --------------------------------------------------------------------------- #


def test_c08_11_non_current_clauses_are_withheld() -> None:
    async def scenario() -> None:
        fixture = await _fresh()
        try:
            await _seed(
                fixture,
                [
                    _chunk("doc-1", clause_number="8.4", text="extension of time", is_current=False),
                    _chunk("doc-2", clause_number="8.4", text="extension of time", is_current=True),
                ],
            )
            contained = await _candidates(fixture, eligible=["doc-1", "doc-2"])
            assert _returned_documents(contained) == {"doc-2"}
        finally:
            await _drop(fixture)

    _run(scenario())


def test_c08_11_non_authorised_clauses_are_withheld() -> None:
    async def scenario() -> None:
        fixture = await _fresh()
        try:
            await _seed(
                fixture,
                [
                    _chunk(
                        "doc-1",
                        clause_number="8.4",
                        text="extension of time",
                        is_authorised_for_ai=False,
                    ),
                    _chunk("doc-2", clause_number="8.4", text="extension of time"),
                ],
            )
            contained = await _candidates(fixture, eligible=["doc-1", "doc-2"])
            assert _returned_documents(contained) == {"doc-2"}
        finally:
            await _drop(fixture)

    _run(scenario())


# --------------------------------------------------------------------------- #
# No live cutover
# --------------------------------------------------------------------------- #


def test_the_generic_lexical_path_is_untouched() -> None:
    """T09 is capability only.

    ``ContractService._lexical_candidates`` keeps its existing signature and
    behaviour, so nothing in the live path changed and no partial cutover is
    possible before ticket 12.
    """
    import inspect

    from rbac_backend.services.contract_service import ContractService

    parameters = list(inspect.signature(ContractService._lexical_candidates).parameters)
    assert parameters == [
        "self",
        "collection",
        "match_stage",
        "regex",
        "candidate_limit",
        "category_terms",
    ], f"the live lexical entry point changed shape: {parameters}"
