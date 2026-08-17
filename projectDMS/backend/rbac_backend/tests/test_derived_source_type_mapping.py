"""What `source_id` actually names, per `source_type` - traced from the writers.

The helper's first version assumed every `source_id` named a document. The
second assumed a hardcoded list of "application" types. Both were guesses. The
production resolver already carries the real map
(`arbitration_drafting/context.py:_rehydrate_direct_reference`), and it
disagrees with the guess in two places that matter:

* ``letter`` was classified as an independent application record, so its
  authority check was SKIPPED entirely. But the resolver looks a letter id up
  in ``("letters", "documents")`` - if it lands in ``documents`` it is an
  extraction-controlled document, and skipping the check is a bypass.

* ``clause`` was classified as document-derived and its ``source_id`` resolved
  as a document id. It is not one: it names a ``document_vectors`` /
  ``contract_clauses`` child row. The lookup therefore never matched, and the
  helper denied every clause row while never once consulting the authority of
  the document the clause actually came from.

So the classification is now derived from the same map the resolver uses,
rather than restated beside it - the drift these two errors are made of is the
same failure mode as three duplicated role-alias tables.

The rule the map implies:

* the id lands in ``documents``           -> that document's authority governs
* the id lands in a child collection      -> its PARENT document's authority
* the id lands in an application record   -> document authority does not apply
* the type is unknown, or a document-ish
  id resolves to nothing                  -> deny (fail closed)
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import pytest

from rbac_backend.services.publication_policy import consumable_derived_text

NOTE = "DERIVED RELEVANCE NOTE"


class _Collection:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    async def find_one(self, query: Dict[str, Any], *a: Any, **k: Any):
        for row in self.rows:
            if all(_matches(row, key, value) for key, value in query.items()):
                return row
        return None


def _matches(row: Dict[str, Any], key: str, expected: Any) -> bool:
    actual = row.get(key)
    if isinstance(expected, dict) and "$in" in expected:
        return any(str(actual) == str(candidate) for candidate in expected["$in"])
    return str(actual) == str(expected)


class _DB:
    def __init__(self, **collections: List[Dict[str, Any]]) -> None:
        self._collections = {
            name: _Collection(rows) for name, rows in collections.items()
        }

    def __getitem__(self, name: str) -> _Collection:
        return self._collections.setdefault(name, _Collection([]))

    def __getattr__(self, name: str) -> _Collection:
        return self[name]


def _text(db: _DB, row: Dict[str, Any]) -> str:
    return asyncio.run(consumable_derived_text(db, row, "relevance_note"))


def _row(source_type: Optional[str], source_id: Any) -> Dict[str, Any]:
    row = {"source_id": source_id, "relevance_note": NOTE}
    if source_type is not None:
        row["source_type"] = source_type
    return row


CLEAN = {"_id": "doc-1", "processing_status": "metadata_extracted"}
BLOCKED = {"_id": "doc-1", "processing_status": "human_review_required"}


# --- document: source_id IS the canonical document id --------------------------
#
# Writer: `_document_indexing` (deterministic.py:164) and `_document_understanding`
# (llm.py:606) both set source_id = str(documents._id). Neither model defines a
# `source_type` field on `documents`, so `document.get("source_type") or
# "document"` is always exactly "document".


def test_a_document_source_follows_its_own_authority() -> None:
    assert _text(_DB(documents=[CLEAN]), _row("document", "doc-1")) == NOTE
    assert _text(_DB(documents=[BLOCKED]), _row("document", "doc-1")) == ""


def test_an_expert_report_resolves_in_documents() -> None:
    """`expert_report` maps to ("documents",) - a document under another name."""
    assert _text(_DB(documents=[CLEAN]), _row("expert_report", "doc-1")) == NOTE
    assert _text(_DB(documents=[BLOCKED]), _row("expert_report", "doc-1")) == ""


# --- clause: source_id names the CHILD row, not the document -------------------


def test_a_clause_source_resolves_through_to_its_parent_document() -> None:
    """The linkage the previous version never made.

    A clause id looked up in `documents` matches nothing, so the helper denied
    without ever asking the document anything.
    """
    clause = {"_id": "clause-9", "document_id": "doc-1", "text": "..."}

    assert _text(_DB(contract_clauses=[clause], documents=[CLEAN]), _row("clause", "clause-9")) == NOTE
    assert _text(_DB(contract_clauses=[clause], documents=[BLOCKED]), _row("clause", "clause-9")) == ""


def test_a_vector_clause_resolves_through_to_its_parent_document() -> None:
    chunk = {"_id": "vec-3", "document_id": "doc-1", "text": "..."}

    assert _text(_DB(document_vectors=[chunk], documents=[BLOCKED]), _row("clause", "vec-3")) == ""
    assert _text(_DB(document_vectors=[chunk], documents=[CLEAN]), _row("clause", "vec-3")) == NOTE


def test_a_clause_whose_parent_document_is_gone_is_denied() -> None:
    clause = {"_id": "clause-9", "document_id": "ghost"}

    assert _text(_DB(contract_clauses=[clause], documents=[CLEAN]), _row("clause", "clause-9")) == ""


# --- letter: ambiguous by design, so it must be resolved, not assumed ----------


def test_a_letter_id_that_lands_in_documents_obeys_document_authority() -> None:
    """The bypass. `letter` was on the skip list, so this returned the note."""
    assert _text(_DB(documents=[BLOCKED]), _row("letter", "doc-1")) == ""


def test_a_letter_id_that_lands_in_the_letters_collection_is_application_content() -> None:
    """A `letters` row is authored in-app: status/history, no extraction state.

    Document publication authority has nothing to say about it.
    """
    letter = {"_id": "ltr-1", "subject": "Notice of delay"}

    assert _text(_DB(letters=[letter], documents=[BLOCKED]), _row("letter", "ltr-1")) == NOTE


def test_a_soft_deleted_letter_is_still_quarantined() -> None:
    """The quarantine axis is not document-only.

    `letters` rows carry `lifecycle_state`, and letter_service filters
    `!= "deleted"` when listing them (`letter_service.py:734`). The
    application-record branch returned consumable without asking, so a letter
    the user had deleted kept feeding its derived matrix text into drafting.
    """
    deleted = {"_id": "ltr-1", "lifecycle_state": "deleted"}

    assert _text(_DB(letters=[deleted]), _row("letter", "ltr-1")) == ""


def test_a_quarantined_duplicate_letter_is_denied() -> None:
    duplicate = {"_id": "ltr-1", "duplicate_status": "duplicate"}

    assert _text(_DB(letters=[duplicate]), _row("letter", "ltr-1")) == ""


def test_an_ordinary_application_record_is_unaffected_by_the_quarantine_check() -> None:
    """It must add the quarantine axis without introducing an over-block.

    Application records carry no `processing_status`, so the only thing
    `is_consumable` can say about a healthy one is yes.
    """
    assert _text(_DB(claims=[{"_id": "claim-1"}]), _row("claim", "claim-1")) == NOTE


def test_an_unresolvable_letter_is_denied() -> None:
    """It might have been a document; nothing proves it was not."""
    assert _text(_DB(), _row("letter", "nowhere")) == ""


# --- independent application records: authority must NOT suppress them ---------


@pytest.mark.parametrize(
    "source_type",
    ["claim", "variation", "payment_event", "bank_guarantee", "chronology_event", "project_event"],
)
def test_an_application_record_is_not_governed_by_document_authority(
    source_type: str,
) -> None:
    """Over-blocking is a real failure too, not the safe side of the trade.

    These ids name claims, variations, IPC bills, guarantees and chronology
    events. Resolving them as documents finds nothing and denies - silently
    deleting legitimate matrix evidence from a live filing.
    """
    assert _text(_DB(documents=[BLOCKED]), _row(source_type, "app-record-1")) == NOTE


def test_an_application_record_needs_no_lookup_at_all() -> None:
    """No collections registered anywhere - it must still come through."""
    assert _text(_DB(), _row("claim", "claim-1")) == NOTE


# --- missing / unknown source_type --------------------------------------------


def test_a_row_without_source_type_is_treated_as_document_derived() -> None:
    """Proven, not assumed: the only writer that omits it is the document index.

    `_insert_matrix_row` persists the body verbatim (`ArbitrationMatrixRow` is
    `extra="allow"` and adds no default), and `document-index` is the only
    matrix whose body carries `source_id` at all. So a legacy row with a
    `source_id` and no `source_type` came from the document index.
    """
    assert _text(_DB(documents=[CLEAN]), _row(None, "doc-1")) == NOTE
    assert _text(_DB(documents=[BLOCKED]), _row(None, "doc-1")) == ""


def test_an_unknown_source_type_is_denied_as_unknown_origin() -> None:
    """Distinct from a KNOWN application source, which is allowed above.

    A type nobody has classified could be document-derived, so it cannot be
    waved through the way `claim` is - but it also must not silently pretend to
    be a document id.
    """
    assert _text(_DB(documents=[CLEAN]), _row("some_future_type", "doc-1")) == ""


def test_the_registry_covers_every_declared_arbitration_source_type() -> None:
    """Fail the build when the enum grows, instead of denying at runtime.

    This is the same shape as the processing_status drift guard: the runtime
    rule stays predictable because a build-time test forces the decision.
    """
    from rbac_backend.models.arbitration_drafting import ArbitrationSourceType
    from rbac_backend.services.publication_policy import DERIVED_SOURCE_COLLECTIONS

    declared = {member.value for member in ArbitrationSourceType}
    # `manual_fact` is user-typed prose that never claims a server record.
    unclassified = declared - set(DERIVED_SOURCE_COLLECTIONS) - {"manual_fact"}

    assert not unclassified, (
        f"arbitration source types with no entry in DERIVED_SOURCE_COLLECTIONS: "
        f"{sorted(unclassified)}. Decide which collection each source_id names."
    )


# --- malformed and missing input ----------------------------------------------


def test_a_malformed_source_id_is_denied_not_crashed() -> None:
    for bad in ["", None, "not-an-objectid", 12345, {"$ne": None}]:
        assert _text(_DB(documents=[CLEAN]), _row("document", bad)) == ""


def test_a_missing_source_document_is_denied() -> None:
    assert _text(_DB(documents=[]), _row("document", "doc-1")) == ""


def test_an_empty_row_yields_nothing() -> None:
    assert _text(_DB(documents=[CLEAN]), {}) == ""


def test_a_lookup_failure_denies() -> None:
    class _Broken:
        async def find_one(self, *a: Any, **k: Any):
            raise RuntimeError("mongo unavailable")

    class _BrokenDB:
        def __getitem__(self, name: str) -> Any:
            return _Broken()

        def __getattr__(self, name: str) -> Any:
            return _Broken()

    assert asyncio.run(
        consumable_derived_text(_BrokenDB(), _row("document", "doc-1"), "relevance_note")
    ) == ""


# --- cross-tenant --------------------------------------------------------------


def test_authority_is_read_from_the_document_the_id_names() -> None:
    """A row pointing at another tenant's blocked document is still denied.

    The helper returns the ROW's own text, never the resolved document's, so
    this is not a disclosure path - but the authority decision must still come
    from the record the id actually names, not from a same-tenant lookalike.
    """
    db = _DB(
        documents=[
            {"_id": "doc-foreign", "organization_id": "org-2", "processing_status": "human_review_required"},
        ]
    )

    assert _text(db, _row("document", "doc-foreign")) == ""
