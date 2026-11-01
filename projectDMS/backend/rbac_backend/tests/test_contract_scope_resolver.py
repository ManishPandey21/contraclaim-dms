"""ContractScopeResolver — the positive canonical eligible universe.

Owned by implementation ticket 05. Three release gates live here:

``L15-02``
    A candidate whose canonical Document does not resolve is excluded because it
    was never *in* the positive set — not because something judged it blocked.
    The shared ``blocked_document_ids`` helper deliberately fails OPEN on an id
    it cannot resolve (an orphaned point is not evidence that a document is
    blocked), which is correct for generic search and wrong for legal evidence.

``L15-06``
    A resolver failure hard-fails. It never degrades into a broader search.

``A-39``
    The two authority helpers cannot diverge in effect, because eligibility is a
    positive list built from the fail-closed per-document resolver rather than a
    subtraction using the fail-open batch filter.

Every assertion is on the resolver's returned outcome — which instruments come
back, which document ids are eligible — never on whether a helper was called. A
helper-call assertion would pass while the helper did nothing.

Real Mongo is not required: what is under test is composition logic. The
physical guarantees (index uniqueness, transactional overlap) were proven
against a real replica set by ticket 03.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Dict, List

import pytest

from rbac_backend.models.contract_document import (
    ApplicabilityLifecycleKind,
    ApplicableInstrument,
    Browse,
    BrowseResult,
    ContractDocumentType,
    ContractScopeLevel,
    CurrentState,
    Historical,
    ProjectionStatus,
)
from rbac_backend.services.contract_scope_resolver import (
    AuthorizedContractScope,
    ContractScopeResolutionError,
    ContractScopeResolver,
)

ORG = "org-1"
PROJECT = "project-1"
CONTRACT = "primary"


# --------------------------------------------------------------------------- #
# Minimal fakes — only what the resolver actually reads
# --------------------------------------------------------------------------- #


class _Cursor:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self._rows = rows

    async def to_list(self, length: Any = None) -> List[Dict[str, Any]]:
        return list(self._rows)

    def __aiter__(self):
        async def gen():
            for row in self._rows:
                yield row

        return gen()


class _Collection:
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self.rows = rows

    def find(self, query: Dict[str, Any], *args: Any, **kwargs: Any) -> _Cursor:
        return _Cursor([dict(r) for r in self.rows if _matches(r, query)])

    async def find_one(self, query: Dict[str, Any], *args: Any, **kwargs: Any):
        for row in self.rows:
            if _matches(row, query):
                return dict(row)
        return None


def _matches(row: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for key, expected in query.items():
        if isinstance(expected, dict) and "$in" in expected:
            if row.get(key) not in expected["$in"]:
                return False
        elif row.get(key) != expected:
            return False
    return True


class _Database:
    def __init__(
        self,
        *,
        documents: List[Dict[str, Any]],
        contract_documents: List[Dict[str, Any]],
        applicability: List[Dict[str, Any]],
        events: List[Dict[str, Any]],
        legal_effects: List[Dict[str, Any]] | None = None,
    ) -> None:
        self.documents = _Collection(documents)
        self._named = {
            "contract_documents": _Collection(contract_documents),
            "contract_document_applicability": _Collection(applicability),
            "contract_document_applicability_events": _Collection(events),
            "contract_document_legal_effects": _Collection(legal_effects or []),
        }

    def __getitem__(self, name: str) -> _Collection:
        return self._named[name]


def _document(document_id: str, *, consumable: bool = True) -> Dict[str, Any]:
    return {
        "_id": document_id,
        "organization_id": ORG,
        "project_id": PROJECT,
        "lifecycle_state": "active",
        "duplicate_status": "unique",
        # "failed" is deliberately OPERATIONAL, not adverse: the pipeline broke,
        # which is not a judgement about the document, so last-known-good stands.
        # The single adverse state is human review required.
        "processing_status": "metadata_extracted" if consumable else "human_review_required",
    }


def _instrument_row(
    contract_document_id: str,
    document_id: str,
    *,
    scope: ContractScopeLevel = ContractScopeLevel.PROJECT,
    projection_status: str = ProjectionStatus.CURRENT.value,
    classification_revision: int = 1,
    projection_revision: int | None = 1,
) -> Dict[str, Any]:
    return {
        "_id": contract_document_id,
        "organization_id": ORG,
        "document_id": document_id,
        "scope_level": scope.value,
        "scope_project_id": PROJECT if scope is ContractScopeLevel.PROJECT else None,
        "contract_document_type": ContractDocumentType.GENERAL_CONDITIONS.value,
        "classification_revision": classification_revision,
        "projection_revision": projection_revision,
        "projection_status": projection_status,
    }


def _applicability(applicability_id: str, contract_document_id: str, *, project=PROJECT, contract=CONTRACT):
    return {
        "_id": applicability_id,
        "organization_id": ORG,
        "contract_document_id": contract_document_id,
        "project_id": project,
        "contract_id": contract,
    }


def _applied(applicability_id: str, *, event_id: str = "evt-1", at: date | None = date(2026, 1, 1)):
    return {
        "_id": event_id,
        "event_id": event_id,
        "applicability_id": applicability_id,
        "kind": ApplicabilityLifecycleKind.APPLIED.value,
        "effective_at": at.isoformat() if at else None,
    }


def _withdrawn(applicability_id: str, *, event_id: str = "evt-2", at: date = date(2026, 6, 1)):
    return {
        "_id": event_id,
        "event_id": event_id,
        "applicability_id": applicability_id,
        "kind": ApplicabilityLifecycleKind.WITHDRAWN.value,
        "effective_at": at.isoformat(),
    }


def _scope(project: str = PROJECT, contract: str = CONTRACT) -> AuthorizedContractScope:
    """A scope token as the authorising factory would produce it."""
    return AuthorizedContractScope.for_tests(
        organization_id=ORG, project_id=project, contract_id=contract, actor_id="user-1"
    )


def _healthy_db() -> _Database:
    return _Database(
        documents=[_document("doc-1")],
        contract_documents=[_instrument_row("cd-1", "doc-1")],
        applicability=[_applicability("app-1", "cd-1")],
        events=[_applied("app-1")],
    )


# --------------------------------------------------------------------------- #
# The precondition type
# --------------------------------------------------------------------------- #


def test_scope_token_cannot_be_constructed_from_raw_scope_strings() -> None:
    """A caller must pass through generic authorisation to obtain a token.

    Raw ``organization_id``/``project_id``/``contract_id`` are not authority, and
    accepting them would let a caller reach the resolver without ever meeting
    PolicyService.
    """
    with pytest.raises(TypeError):
        AuthorizedContractScope(  # type: ignore[call-arg]
            organization_id=ORG, project_id=PROJECT, contract_id=CONTRACT, actor_id="user-1"
        )


def test_resolver_refuses_a_scope_that_is_not_an_authorized_token() -> None:
    resolver = ContractScopeResolver(_healthy_db())
    with pytest.raises(TypeError):
        _run(resolver.resolve({"organization_id": ORG}, CurrentState()))  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# L15-02 — the positive set excludes an unresolvable canonical Document
# --------------------------------------------------------------------------- #


def test_l15_02_unresolvable_canonical_document_is_excluded() -> None:
    """The instrument is applicable, but its canonical Document is gone.

    ``blocked_document_ids`` would NOT report it as blocked — an absent id is an
    orphaned point, deliberately not returned. A positive set excludes it
    because it never resolved in the first place.
    """
    db = _Database(
        documents=[],  # the canonical document does not exist
        contract_documents=[_instrument_row("cd-1", "doc-missing")],
        applicability=[_applicability("app-1", "cd-1")],
        events=[_applied("app-1")],
    )
    resolved = _run(ContractScopeResolver(db).resolve(_scope(), CurrentState()))

    assert resolved.eligible_document_ids == frozenset()
    assert resolved.instruments == ()


def test_l15_02_blocked_filter_alone_would_have_admitted_the_orphan() -> None:
    """Proves the two helpers genuinely differ, so the choice is load-bearing.

    If this ever stops holding, the positive-set requirement has quietly become
    decorative and the test above would pass for the wrong reason.
    """
    from rbac_backend.services.publication_policy import blocked_document_ids

    db = _Database(documents=[], contract_documents=[], applicability=[], events=[])
    blocked = _run(blocked_document_ids(db, ["doc-missing"]))
    assert blocked == set(), (
        "blocked_document_ids no longer fails open on a missing document; the "
        "positive-set rationale needs rechecking"
    )


def test_blocked_canonical_document_is_also_excluded() -> None:
    db = _Database(
        documents=[_document("doc-1", consumable=False)],
        contract_documents=[_instrument_row("cd-1", "doc-1")],
        applicability=[_applicability("app-1", "cd-1")],
        events=[_applied("app-1")],
    )
    resolved = _run(ContractScopeResolver(db).resolve(_scope(), CurrentState()))
    assert resolved.eligible_document_ids == frozenset()


def test_healthy_instrument_resolves_to_an_applicable_instrument() -> None:
    resolved = _run(ContractScopeResolver(_healthy_db()).resolve(_scope(), CurrentState()))

    assert resolved.eligible_document_ids == frozenset({"doc-1"})
    assert len(resolved.instruments) == 1
    instrument = resolved.instruments[0]
    assert isinstance(instrument, ApplicableInstrument)
    assert instrument.contract_document_id == "cd-1"
    assert instrument.document_id == "doc-1"
    assert instrument.contract_id == CONTRACT
    assert instrument.classification_revision == 1


# --------------------------------------------------------------------------- #
# A-39 — the positive set is what prevents helper divergence
# --------------------------------------------------------------------------- #


def test_a39_eligible_set_is_a_subset_of_positively_resolved_documents() -> None:
    """Two applicable instruments, one with a resolvable document and one without.

    A subtractive gate would return both. A positive gate returns one.
    """
    db = _Database(
        documents=[_document("doc-good")],
        contract_documents=[
            _instrument_row("cd-good", "doc-good"),
            _instrument_row("cd-orphan", "doc-gone"),
        ],
        applicability=[
            _applicability("app-good", "cd-good"),
            _applicability("app-orphan", "cd-orphan"),
        ],
        events=[_applied("app-good", event_id="e1"), _applied("app-orphan", event_id="e2")],
    )
    resolved = _run(ContractScopeResolver(db).resolve(_scope(), CurrentState()))

    assert resolved.eligible_document_ids == frozenset({"doc-good"})
    assert [i.document_id for i in resolved.instruments] == ["doc-good"]


# --------------------------------------------------------------------------- #
# L15-06 — resolver failure hard-fails
# --------------------------------------------------------------------------- #


class _ExplodingDatabase(_Database):
    def __getitem__(self, name: str):
        raise RuntimeError("datastore unavailable")


def test_l15_06_resolver_failure_raises_and_does_not_return_empty() -> None:
    """An empty result would be indistinguishable from 'nothing applies', which
    a caller may legitimately treat as a valid answer. A failure must not
    masquerade as one."""
    db = _ExplodingDatabase(documents=[], contract_documents=[], applicability=[], events=[])
    with pytest.raises(ContractScopeResolutionError):
        _run(ContractScopeResolver(db).resolve(_scope(), CurrentState()))


# --------------------------------------------------------------------------- #
# Contract identity and catalogue isolation
# --------------------------------------------------------------------------- #


def test_same_contract_id_in_a_sibling_project_does_not_resolve() -> None:
    db = _healthy_db()
    resolved = _run(ContractScopeResolver(db).resolve(_scope(project="project-2"), CurrentState()))
    assert resolved.eligible_document_ids == frozenset()


def test_sibling_contract_in_the_same_project_does_not_resolve() -> None:
    db = _healthy_db()
    resolved = _run(ContractScopeResolver(db).resolve(_scope(contract="C2"), CurrentState()))
    assert resolved.eligible_document_ids == frozenset()


def test_organisation_instrument_with_zero_applicability_is_not_evidence() -> None:
    """It may be catalogued. It is not evidence for any contract."""
    db = _Database(
        documents=[_document("doc-1")],
        contract_documents=[
            _instrument_row("cd-1", "doc-1", scope=ContractScopeLevel.ORGANIZATION)
        ],
        applicability=[],
        events=[],
    )
    resolved = _run(ContractScopeResolver(db).resolve(_scope(), CurrentState()))
    assert resolved.instruments == ()
    assert resolved.eligible_document_ids == frozenset()


def test_browse_returns_browse_results_never_instruments() -> None:
    db = _Database(
        documents=[_document("doc-1")],
        contract_documents=[
            _instrument_row("cd-1", "doc-1", scope=ContractScopeLevel.ORGANIZATION)
        ],
        applicability=[],
        events=[],
    )
    results = _run(ContractScopeResolver(db).browse_catalogue(_scope()))
    assert results and all(isinstance(r, BrowseResult) for r in results)
    assert not any(isinstance(r, ApplicableInstrument) for r in results)


# --------------------------------------------------------------------------- #
# Projection currency and query modes
# --------------------------------------------------------------------------- #


def test_stale_projection_is_excluded_from_the_eligible_set() -> None:
    db = _Database(
        documents=[_document("doc-1")],
        contract_documents=[
            _instrument_row("cd-1", "doc-1", classification_revision=2, projection_revision=1)
        ],
        applicability=[_applicability("app-1", "cd-1")],
        events=[_applied("app-1")],
    )
    resolved = _run(ContractScopeResolver(db).resolve(_scope(), CurrentState()))
    assert resolved.eligible_document_ids == frozenset()


def test_pending_projection_is_excluded_from_the_eligible_set() -> None:
    db = _Database(
        documents=[_document("doc-1")],
        contract_documents=[
            _instrument_row("cd-1", "doc-1", projection_status=ProjectionStatus.PENDING.value)
        ],
        applicability=[_applicability("app-1", "cd-1")],
        events=[_applied("app-1")],
    )
    resolved = _run(ContractScopeResolver(db).resolve(_scope(), CurrentState()))
    assert resolved.eligible_document_ids == frozenset()


def test_withdrawn_applicability_is_not_current() -> None:
    db = _healthy_db()
    db["contract_document_applicability_events"].rows.append(_withdrawn("app-1"))
    resolved = _run(ContractScopeResolver(db).resolve(_scope(), CurrentState()))
    assert resolved.instruments == ()


def test_historical_before_the_applied_date_does_not_resolve() -> None:
    resolved = _run(
        ContractScopeResolver(_healthy_db()).resolve(_scope(), Historical(event_date=date(2025, 6, 1)))
    )
    assert resolved.instruments == ()


def test_historical_after_the_applied_date_resolves() -> None:
    resolved = _run(
        ContractScopeResolver(_healthy_db()).resolve(_scope(), Historical(event_date=date(2026, 3, 1)))
    )
    assert [i.document_id for i in resolved.instruments] == ["doc-1"]


def test_historical_fails_closed_when_the_start_is_unknown() -> None:
    """An unknown legal start cannot answer a dated question — it may not be
    substituted with a creation timestamp or any other proxy."""
    db = _Database(
        documents=[_document("doc-1")],
        contract_documents=[_instrument_row("cd-1", "doc-1")],
        applicability=[_applicability("app-1", "cd-1")],
        events=[_applied("app-1", at=None)],
    )
    resolved = _run(ContractScopeResolver(db).resolve(_scope(), Historical(event_date=date(2026, 3, 1))))
    assert resolved.instruments == ()


def test_current_state_still_resolves_when_the_start_is_unknown() -> None:
    """An unknown start does not disqualify a current-state answer."""
    db = _Database(
        documents=[_document("doc-1")],
        contract_documents=[_instrument_row("cd-1", "doc-1")],
        applicability=[_applicability("app-1", "cd-1")],
        events=[_applied("app-1", at=None)],
    )
    resolved = _run(ContractScopeResolver(db).resolve(_scope(), CurrentState()))
    assert [i.document_id for i in resolved.instruments] == ["doc-1"]


def test_browse_mode_is_refused_by_the_evidence_entry_point() -> None:
    with pytest.raises(ContractScopeResolutionError):
        _run(ContractScopeResolver(_healthy_db()).resolve(_scope(), Browse()))


def test_resolver_does_not_persist_evidence_readiness() -> None:
    """Readiness is computed per call. Nothing is written back."""
    db = _healthy_db()
    before = [dict(r) for r in db["contract_documents"].rows]
    _run(ContractScopeResolver(db).resolve(_scope(), CurrentState()))
    assert [dict(r) for r in db["contract_documents"].rows] == before


# --------------------------------------------------------------------------- #


def _run(coro: Any) -> Any:
    import asyncio

    return asyncio.run(coro)
