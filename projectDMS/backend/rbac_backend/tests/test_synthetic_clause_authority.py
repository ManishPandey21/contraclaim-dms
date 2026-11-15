"""The arbitration agreement is not an extracted clause, and must not be gated as one.

When a case has no scoped contract clauses, `_clause_interpretation`
(`deterministic.py:249`) synthesises one row from `case["arbitration_clause"]` -
a field a user types onto the arbitration case record - under the identifier
``case:{case_id}:arbitration_clause``.

That identifier names nothing in `contract_clauses` or `document_vectors`, so
resolving it as a clause child id fails closed and withholds the arbitration
agreement itself: the jurisdictional foundation of the pleading, and text that
document publication authority was never meant to govern. Fixing the clause
leak created this over-block; both have to hold at once.

HONEST LIMIT, stated because it matters: `clause_source_id` is not in
`PRIVILEGED_MATRIX_FIELDS`, so a client can write it and could claim the
synthetic identifier on a real clause row. That buys an attacker nothing here -
`clause_text_excerpt` is equally unprivileged, so anyone able to spoof the id
can already write the text directly. The gate exists to stop STALE EXTRACTION
flowing automatically, not to stop a user typing prose into their own case.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from rbac_backend.services.publication_policy import (
    resolve_derived_authority,
    synthetic_case_clause_id,
)

EXCERPT = "Disputes shall be referred to arbitration seated in New Delhi."


class _Collection:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    async def find_one(self, query: Dict[str, Any], *a: Any, **k: Any):
        expected = query.get("_id") if "_id" in query else query.get("document_id")
        for row in self.rows:
            key = "_id" if "_id" in query else "document_id"
            if isinstance(expected, dict) and "$in" in expected:
                if any(str(row.get(key)) == str(c) for c in expected["$in"]):
                    return row
            elif str(row.get(key)) == str(expected):
                return row
        return None


class _DB:
    def __init__(self, **collections: List[Dict[str, Any]]) -> None:
        self._collections = {n: _Collection(r) for n, r in collections.items()}

    def __getitem__(self, name: str) -> _Collection:
        return self._collections.setdefault(name, _Collection([]))

    def __getattr__(self, name: str) -> _Collection:
        return self[name]


def _decide(db: _DB, source_id: Any, source_type: str = "clause"):
    return asyncio.run(resolve_derived_authority(db, source_type, source_id))


BLOCKED = {"_id": "doc-1", "processing_status": "human_review_required"}
CLEAN = {"_id": "doc-1", "processing_status": "metadata_extracted"}
REAL_CLAUSE = {"_id": "clause-21-2", "document_id": "doc-1"}


# --- the identifier is synthetic, and says so ----------------------------------


def test_the_writer_and_the_resolver_share_one_identifier_shape() -> None:
    """If these drift the exemption silently stops matching."""
    assert synthetic_case_clause_id("case-1") == "case:case-1:arbitration_clause"


def test_a_synthetic_arbitration_clause_is_not_a_persisted_extracted_clause() -> None:
    decision = _decide(_DB(documents=[BLOCKED]), synthetic_case_clause_id("case-1"))

    assert decision.consumable is True
    assert decision.reason == "application_generated_source"


def test_the_synthetic_clause_survives_with_no_clause_collections_at_all() -> None:
    assert _decide(_DB(), synthetic_case_clause_id("case-9")).consumable is True


# --- real clauses keep their authority -----------------------------------------


def test_a_real_clause_still_resolves_to_its_parent_document() -> None:
    db = _DB(contract_clauses=[REAL_CLAUSE], documents=[CLEAN])

    assert _decide(db, "clause-21-2").consumable is True


def test_a_real_clause_with_a_blocked_parent_is_still_denied() -> None:
    """The exemption must not become a hole for genuine extracted clauses."""
    db = _DB(contract_clauses=[REAL_CLAUSE], documents=[BLOCKED])

    assert _decide(db, "clause-21-2").consumable is False


def test_an_unresolvable_clause_id_is_still_denied() -> None:
    assert _decide(_DB(documents=[CLEAN]), "clause-does-not-exist").consumable is False


# --- the exemption is exact, not a prefix ---------------------------------------


def test_a_near_miss_identifier_does_not_claim_the_exemption() -> None:
    """A loose `startswith("case:")` would have been a much wider hole."""
    for lookalike in [
        "case:case-1:arbitration_clause:extra",
        "case:case-1:contract_clause",
        "casecase-1arbitration_clause",
        "case:arbitration_clause",
        " case:case-1:arbitration_clause",
    ]:
        assert _decide(_DB(documents=[CLEAN]), lookalike).consumable is False, lookalike


def test_a_stored_clause_is_not_reachable_through_the_synthetic_namespace() -> None:
    """The exemption must not resolve, or expose, any persisted clause row."""
    stored = {"_id": synthetic_case_clause_id("case-1"), "document_id": "doc-1"}
    db = _DB(contract_clauses=[stored], documents=[BLOCKED])

    decision = _decide(db, synthetic_case_clause_id("case-1"))

    # Allowed as an application-generated source, and specifically NOT because
    # a stored clause row was consulted - the blocked parent is never reached.
    assert decision.reason == "application_generated_source"


def test_the_exemption_is_only_for_document_governed_lookups() -> None:
    """A claim id shaped like the sentinel is still an application record."""
    decision = _decide(_DB(), synthetic_case_clause_id("case-1"), source_type="claim")

    assert decision.consumable is True


# --- end to end through the agent ----------------------------------------------


def test_the_arbitration_agreement_reaches_the_ledger() -> None:
    from rbac_backend.services.arbitration_drafting.context import (
        ArbitrationContextBuilder,
    )

    class _Cursor:
        def __init__(self, rows):
            self.rows = list(rows)

        def sort(self, *a, **k):
            return self

        async def to_list(self, length: Optional[int] = None):
            return list(self.rows)

    class _MatrixCollection(_Collection):
        def find(self, query=None):
            return _Cursor(self.rows)

    db = _DB(documents=[BLOCKED])
    db._collections["arbitration_clause_matrix"] = _MatrixCollection(
        [
            {
                "_id": "cm-1",
                "case_id": "case-1",
                "clause_number": "Arbitration clause",
                "topic": "Arbitration agreement",
                "clause_source_id": synthetic_case_clause_id("case-1"),
                "clause_text_excerpt": EXCERPT,
                "approval_status": "approved",
                "verification_status": "approved",
            }
        ]
    )

    rows = asyncio.run(
        ArbitrationContextBuilder(db)._clause_matrix_sources("case-1", 0, False, [])
    )

    assert rows and EXCERPT in str(rows[0]["snippet"]), (
        "the arbitration agreement was withheld from the ledger by a clause "
        "authority check that had no clause to check"
    )
