"""Classification lifecycle and the projection currency fence.

Owned by implementation ticket 07, which is deliberately merged. Advancing the
authoritative classification and invalidating the derived projection are one
change, because the state in between — revision N+1 confirmed while projection N
still reads as current evidence — must never be deployable.

Release gates here:

``C08-02``
    A correction to N+1 makes projection N unusable **for evidence**, while the
    rows themselves stay readable for non-evidence surfaces.
``C08-08``
    Renaming the source file changes no authoritative type.
``C08-09``
    A correction creates no applicability and no legal effect.
``C08-10`` / ``M09-15``
    A stale projection is never presented as complete, and promotion alone does
    not imply projection currency.

Assertions are on final state — what the resolver returns, what the records
hold — never on whether an invalidation helper ran. A helper-call assertion
would pass while the helper did nothing.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import pytest

from rbac_backend.models.contract_document import (
    ApplicabilityLifecycleKind,
    ContractDocumentType,
    ContractScopeLevel,
    CurrentState,
    ProjectionStatus,
    TypeClassification,
)
from rbac_backend.services.contract_classification_service import (
    ClassificationRevisionConflict,
    ContractClassificationService,
    SuggestionIsNotAuthority,
)
from rbac_backend.services.contract_scope_resolver import (
    AuthorizedContractScope,
    ContractScopeResolver,
)

ORG = "org-1"
PROJECT = "project-1"
CONTRACT = "primary"
CD = "cd-1"
DOC = "doc-1"


# --------------------------------------------------------------------------- #
# Fakes — only what the lifecycle and the resolver read
# --------------------------------------------------------------------------- #


class _Cursor:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self._rows = rows

    async def to_list(self, length: Any = None) -> List[Dict[str, Any]]:
        return list(self._rows)


class _Collection:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    def find(self, query: Dict[str, Any], *a: Any, **k: Any) -> _Cursor:
        return _Cursor([dict(r) for r in self.rows if _matches(r, query)])

    async def find_one(self, query: Dict[str, Any], *a: Any, **k: Any) -> Optional[Dict[str, Any]]:
        for row in self.rows:
            if _matches(row, query):
                return dict(row)
        return None

    async def insert_one(self, doc: Dict[str, Any], *a: Any, **k: Any) -> Any:
        if any(r.get("_id") == doc.get("_id") for r in self.rows):
            raise RuntimeError(f"duplicate _id {doc.get('_id')}")
        self.rows.append(dict(doc))
        return type("R", (), {"inserted_id": doc.get("_id")})()

    async def update_one(self, query: Dict[str, Any], update: Dict[str, Any], *a: Any, **k: Any) -> Any:
        for row in self.rows:
            if _matches(row, query):
                row.update(update.get("$set") or {})
                return type("R", (), {"matched_count": 1, "modified_count": 1})()
        return type("R", (), {"matched_count": 0, "modified_count": 0})()


def _matches(row: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for key, expected in query.items():
        if isinstance(expected, dict) and "$in" in expected:
            if row.get(key) not in expected["$in"]:
                return False
        elif row.get(key) != expected:
            return False
    return True


class _Database:
    def __init__(self, **collections: List[Dict[str, Any]]) -> None:
        self.documents = _Collection(collections.get("documents", []))
        self._named = {
            "contract_documents": _Collection(collections.get("contract_documents", [])),
            "contract_document_classification_facts": _Collection(
                collections.get("classification_facts", [])
            ),
            "contract_document_applicability": _Collection(collections.get("applicability", [])),
            "contract_document_applicability_events": _Collection(collections.get("events", [])),
            "contract_document_legal_effects": _Collection(collections.get("legal_effects", [])),
        }

    def __getitem__(self, name: str) -> _Collection:
        return self._named[name]


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _fixture(
    *,
    classification_revision: int = 1,
    projection_revision: Optional[int] = 1,
    projection_status: str = ProjectionStatus.CURRENT.value,
    filename: str = "GCC-conditions.pdf",
) -> _Database:
    return _Database(
        documents=[
            {
                "_id": DOC,
                "organization_id": ORG,
                "project_id": PROJECT,
                "filename": filename,
                "lifecycle_state": "active",
                "duplicate_status": "unique",
                "processing_status": "metadata_extracted",
            }
        ],
        contract_documents=[
            {
                "_id": CD,
                "organization_id": ORG,
                "document_id": DOC,
                "scope_level": ContractScopeLevel.PROJECT.value,
                "scope_project_id": PROJECT,
                "contract_document_type": ContractDocumentType.GENERAL_CONDITIONS.value,
                "classification_revision": classification_revision,
                "projection_revision": projection_revision,
                "projection_status": projection_status,
            }
        ],
        classification_facts=[
            {
                "_id": "fact-1",
                "contract_document_id": CD,
                "contract_document_type": ContractDocumentType.GENERAL_CONDITIONS.value,
                "revision": 1,
                "basis": "operator_confirmed",
            }
        ],
        applicability=[
            {
                "_id": "app-1",
                "organization_id": ORG,
                "contract_document_id": CD,
                "project_id": PROJECT,
                "contract_id": CONTRACT,
            }
        ],
        events=[
            {
                "_id": "evt-1",
                "event_id": "evt-1",
                "applicability_id": "app-1",
                "kind": ApplicabilityLifecycleKind.APPLIED.value,
                "effective_at": "2026-01-01",
            }
        ],
    )


def _scope() -> AuthorizedContractScope:
    return AuthorizedContractScope.for_tests(
        organization_id=ORG, project_id=PROJECT, contract_id=CONTRACT, actor_id="user-1"
    )


def _eligible(db: _Database) -> frozenset:
    return _run(ContractScopeResolver(db).resolve(_scope(), CurrentState())).eligible_document_ids


def _record(db: _Database) -> Dict[str, Any]:
    return db["contract_documents"].rows[0]


# --------------------------------------------------------------------------- #
# Suggestion is not authority
# --------------------------------------------------------------------------- #


def test_a_suggestion_does_not_advance_the_authoritative_revision() -> None:
    db = _fixture()
    service = ContractClassificationService(db)

    _run(
        service.suggest(
            CD,
            contract_document_type=ContractDocumentType.PARTICULAR_CONDITIONS,
            basis="filename_heuristic",
        )
    )

    assert _record(db)["classification_revision"] == 1
    assert _record(db)["contract_document_type"] == ContractDocumentType.GENERAL_CONDITIONS.value


def test_a_suggestion_cannot_be_recorded_as_resolved() -> None:
    db = _fixture()
    service = ContractClassificationService(db)
    with pytest.raises(SuggestionIsNotAuthority):
        _run(
            service.confirm(
                CD,
                contract_document_type=ContractDocumentType.PARTICULAR_CONDITIONS,
                expected_revision=1,
                actor_id="user-1",
                basis="filename_heuristic",
            )
        )


def test_suggestion_state_is_distinct_from_the_instrument_taxonomy() -> None:
    assert TypeClassification.TYPE_SUGGESTED.value != ContractDocumentType.GENERAL_CONDITIONS.value


# --------------------------------------------------------------------------- #
# C08-08 — a filename is not authority
# --------------------------------------------------------------------------- #


def test_c08_08_renaming_the_source_file_changes_no_authoritative_type() -> None:
    db = _fixture(filename="GCC-conditions.pdf")
    before_type = _record(db)["contract_document_type"]
    before_revision = _record(db)["classification_revision"]

    db.documents.rows[0]["filename"] = "SCC-particular-conditions-AMENDED.pdf"

    assert _record(db)["contract_document_type"] == before_type
    assert _record(db)["classification_revision"] == before_revision
    assert _eligible(db) == frozenset({DOC})


# --------------------------------------------------------------------------- #
# C08-02 — correction invalidates the previous projection FOR EVIDENCE
# --------------------------------------------------------------------------- #


def test_c08_02_correction_makes_the_previous_projection_unusable_for_evidence() -> None:
    db = _fixture()
    assert _eligible(db) == frozenset({DOC}), "fixture should start evidence-eligible"

    _run(
        ContractClassificationService(db).confirm(
            CD,
            contract_document_type=ContractDocumentType.PARTICULAR_CONDITIONS,
            expected_revision=1,
            actor_id="user-1",
        )
    )

    assert _record(db)["classification_revision"] == 2
    assert _eligible(db) == frozenset(), (
        "projection built for revision 1 is still evidence-current after a "
        "correction to revision 2"
    )


def test_c08_02_the_rows_themselves_remain_readable() -> None:
    """Invalidation is logical, not physical: nothing is deleted."""
    db = _fixture()
    _run(
        ContractClassificationService(db).confirm(
            CD,
            contract_document_type=ContractDocumentType.PARTICULAR_CONDITIONS,
            expected_revision=1,
            actor_id="user-1",
        )
    )

    record = _record(db)
    assert record["document_id"] == DOC
    assert record["projection_revision"] == 1, "the old generation marker was destroyed"
    assert len(db["contract_document_classification_facts"].rows) == 2


def test_correction_leaves_projection_pending_not_current() -> None:
    db = _fixture()
    _run(
        ContractClassificationService(db).confirm(
            CD,
            contract_document_type=ContractDocumentType.AMENDMENT,
            expected_revision=1,
            actor_id="user-1",
        )
    )
    assert _record(db)["projection_status"] == ProjectionStatus.PENDING.value


def test_classification_history_is_append_only() -> None:
    db = _fixture()
    service = ContractClassificationService(db)
    _run(service.confirm(CD, contract_document_type=ContractDocumentType.AMENDMENT, expected_revision=1, actor_id="u"))
    _run(service.confirm(CD, contract_document_type=ContractDocumentType.ADDENDUM, expected_revision=2, actor_id="u"))

    facts = db["contract_document_classification_facts"].rows
    assert [f["revision"] for f in facts] == [1, 2, 3]
    assert facts[0]["contract_document_type"] == ContractDocumentType.GENERAL_CONDITIONS.value
    assert _record(db)["classification_revision"] == 3


def test_classification_revision_cannot_regress() -> None:
    db = _fixture(classification_revision=3)
    with pytest.raises(ClassificationRevisionConflict):
        _run(
            ContractClassificationService(db).confirm(
                CD,
                contract_document_type=ContractDocumentType.AMENDMENT,
                expected_revision=1,
                actor_id="user-1",
            )
        )
    assert _record(db)["classification_revision"] == 3


def test_two_callers_believing_the_same_revision_cannot_both_win() -> None:
    db = _fixture()
    service = ContractClassificationService(db)
    _run(service.confirm(CD, contract_document_type=ContractDocumentType.AMENDMENT, expected_revision=1, actor_id="a"))
    with pytest.raises(ClassificationRevisionConflict):
        _run(service.confirm(CD, contract_document_type=ContractDocumentType.ADDENDUM, expected_revision=1, actor_id="b"))


# --------------------------------------------------------------------------- #
# The fence needs BOTH conditions
# --------------------------------------------------------------------------- #


def test_current_status_alone_does_not_qualify_a_stale_generation() -> None:
    db = _fixture(classification_revision=4, projection_revision=3, projection_status=ProjectionStatus.CURRENT.value)
    assert _eligible(db) == frozenset()


def test_matching_revision_alone_does_not_qualify_a_pending_projection() -> None:
    db = _fixture(classification_revision=4, projection_revision=4, projection_status=ProjectionStatus.PENDING.value)
    assert _eligible(db) == frozenset()


def test_matching_revision_alone_does_not_qualify_a_failed_projection() -> None:
    db = _fixture(classification_revision=4, projection_revision=4, projection_status=ProjectionStatus.FAILED.value)
    assert _eligible(db) == frozenset()


def test_both_conditions_together_qualify() -> None:
    db = _fixture(classification_revision=4, projection_revision=4, projection_status=ProjectionStatus.CURRENT.value)
    assert _eligible(db) == frozenset({DOC})


# --------------------------------------------------------------------------- #
# C08-10 / M09-15 — promotion does not imply projection currency
# --------------------------------------------------------------------------- #


def test_m09_15_a_freshly_promoted_instrument_is_not_evidence_capable() -> None:
    """Revision 1, projection never run: applicable, but not yet searchable."""
    db = _fixture(classification_revision=1, projection_revision=None, projection_status=ProjectionStatus.PENDING.value)
    assert _eligible(db) == frozenset()


def test_c08_10_stale_projection_yields_no_evidence_rather_than_partial() -> None:
    db = _fixture(classification_revision=2, projection_revision=1)
    resolved = _run(ContractScopeResolver(db).resolve(_scope(), CurrentState()))
    assert resolved.instruments == ()
    assert resolved.eligible_document_ids == frozenset()


# --------------------------------------------------------------------------- #
# C08-09 — a correction touches no other authority axis
# --------------------------------------------------------------------------- #


def test_c08_09_correction_creates_no_applicability_event() -> None:
    db = _fixture()
    before = [dict(e) for e in db["contract_document_applicability_events"].rows]

    _run(
        ContractClassificationService(db).confirm(
            CD,
            contract_document_type=ContractDocumentType.PARTICULAR_CONDITIONS,
            expected_revision=1,
            actor_id="user-1",
        )
    )

    assert [dict(e) for e in db["contract_document_applicability_events"].rows] == before


def test_c08_09_correction_creates_no_legal_effect() -> None:
    """Reclassifying to a 'stronger' instrument does not make it override
    anything. Precedence is a separate canonical fact."""
    db = _fixture()

    _run(
        ContractClassificationService(db).confirm(
            CD,
            contract_document_type=ContractDocumentType.PARTICULAR_CONDITIONS,
            expected_revision=1,
            actor_id="user-1",
        )
    )

    assert db["contract_document_legal_effects"].rows == []


def test_c08_09_correction_does_not_alter_scope_or_canonical_document() -> None:
    db = _fixture()
    _run(
        ContractClassificationService(db).confirm(
            CD,
            contract_document_type=ContractDocumentType.AMENDMENT,
            expected_revision=1,
            actor_id="user-1",
        )
    )
    record = _record(db)
    assert record["scope_level"] == ContractScopeLevel.PROJECT.value
    assert record["scope_project_id"] == PROJECT
    assert record["document_id"] == DOC


def test_correction_does_not_remove_the_applicability_aggregate() -> None:
    db = _fixture()
    _run(
        ContractClassificationService(db).confirm(
            CD,
            contract_document_type=ContractDocumentType.AMENDMENT,
            expected_revision=1,
            actor_id="user-1",
        )
    )
    assert len(db["contract_document_applicability"].rows) == 1


# --------------------------------------------------------------------------- #
# No evidence-ready flag, no derived rollback
# --------------------------------------------------------------------------- #


def test_no_evidence_ready_field_is_written() -> None:
    db = _fixture()
    _run(
        ContractClassificationService(db).confirm(
            CD,
            contract_document_type=ContractDocumentType.AMENDMENT,
            expected_revision=1,
            actor_id="user-1",
        )
    )
    banned = {"evidence_ready", "evidence_capable", "ready_for_ai", "searchable", "is_current_for_evidence"}
    assert not (set(_record(db)) & banned)


def test_marking_the_projection_failed_does_not_roll_back_classification() -> None:
    """A derived-store failure is not a legal failure.

    If an embedder outage could retract a confirmed classification, a derived
    system would hold a veto over an authoritative legal fact.
    """
    db = _fixture()
    service = ContractClassificationService(db)
    _run(service.confirm(CD, contract_document_type=ContractDocumentType.AMENDMENT, expected_revision=1, actor_id="u"))
    assert _record(db)["classification_revision"] == 2

    _run(service.mark_projection_failed(CD, revision=2, reason="embedder unavailable"))

    assert _record(db)["classification_revision"] == 2
    assert _record(db)["contract_document_type"] == ContractDocumentType.AMENDMENT.value
    assert _record(db)["projection_status"] == ProjectionStatus.FAILED.value
    assert _eligible(db) == frozenset()


def test_a_late_failure_for_a_superseded_revision_changes_nothing() -> None:
    """``revision`` fences the write: a report about revision 1 arriving after
    revision 2 was confirmed must not stamp FAILED over revision 2's state."""
    db = _fixture()
    service = ContractClassificationService(db)
    _run(service.confirm(CD, contract_document_type=ContractDocumentType.AMENDMENT, expected_revision=1, actor_id="u"))
    before = _record(db)["projection_status"]

    _run(service.mark_projection_failed(CD, revision=1, reason="late revision-1 worker"))

    assert _record(db)["classification_revision"] == 2
    assert _record(db)["projection_status"] == before
