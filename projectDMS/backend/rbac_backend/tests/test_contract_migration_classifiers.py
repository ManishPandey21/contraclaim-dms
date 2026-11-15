"""Migration scope and type classifiers - two axes, neither automatic.

M09-01 - an empty legacy project never promotes organisation scope.
M09-02 - a retained audit null yields ambiguity plus a hint, never confirmation.
M09-02b - an absent audit row yields no inference in either direction.
M09-03 - scope and type resolve independently.
M09-04/05 - a suggested type cannot promote; unknown never maps to *other*.
M09-20 - a cross-organisation candidate fails closed.

Why M09-01/02/02b need mutation proof: every one of them asserts that an
inference is *not* drawn, and a classifier that draws no inferences at all passes
all three for free. The mutations therefore make the classifier confirm, and each
must turn its own RED - otherwise these tests are decoration.

The corpus fact underneath all of this: the upload path collapses null and empty
before any durable write, so a surviving audit null records "no project was in
the session" - an absence one level removed from the question. And the audit
write is best-effort, so an absent row is not even that.
"""

from __future__ import annotations

import pytest

from rbac_backend.models.contract_document import ContractDocumentType
from rbac_backend.services.contract_migration_classifiers import (
    ScopeSignal,
    TypeSuggestion,
    classify_scope,
    classify_type,
)
from rbac_backend.services.contract_migration_reconciliation import (
    ReconciliationCandidate,
    ScopeClassificationState,
    TypeClassificationState,
)


def _candidate(**overrides) -> ReconciliationCandidate:
    payload = {
        "candidate_id": "contract-master-migration:contracts:doc-1",
        "canonical_document_id": "doc-1",
        "organization_id": "org-1",
        "module": "contracts",
    }
    payload.update(overrides)
    return ReconciliationCandidate(**payload)


# --------------------------------------------------------------------------- #
# M09-01 / M09-02 / M09-02b : scope is never automatic
# --------------------------------------------------------------------------- #


def test_an_empty_legacy_project_never_promotes_organisation_scope():
    """M09-01."""
    outcome = classify_scope(
        _candidate(),
        signal=ScopeSignal(legacy_project_id="", audit_row_present=True, audit_project_id=None),
    )
    assert outcome.state is not ScopeClassificationState.ORG_SCOPE_CONFIRMED
    assert outcome.state is ScopeClassificationState.AMBIGUOUS


def test_a_null_legacy_project_never_promotes_organisation_scope():
    outcome = classify_scope(
        _candidate(),
        signal=ScopeSignal(legacy_project_id=None, audit_row_present=True, audit_project_id=None),
    )
    assert outcome.state is ScopeClassificationState.AMBIGUOUS


def test_a_retained_audit_null_yields_ambiguity_plus_a_recorded_hint():
    """M09-02. The hint is the honest content of the signal, not a decision."""
    outcome = classify_scope(
        _candidate(),
        signal=ScopeSignal(legacy_project_id=None, audit_row_present=True, audit_project_id=None),
    )
    assert outcome.state is ScopeClassificationState.AMBIGUOUS
    assert outcome.hint
    assert "no project was in the session" in outcome.hint.lower()


def test_an_absent_audit_row_yields_no_inference_in_either_direction():
    """M09-02b. The audit write is best-effort; absence carries nothing."""
    outcome = classify_scope(
        _candidate(),
        signal=ScopeSignal(legacy_project_id=None, audit_row_present=False, audit_project_id=None),
    )
    assert outcome.state is ScopeClassificationState.AMBIGUOUS
    assert outcome.hint is None, "an absent row was turned into a hint"


def test_an_absent_audit_row_does_not_argue_for_project_scope_either():
    outcome = classify_scope(
        _candidate(),
        signal=ScopeSignal(legacy_project_id=None, audit_row_present=False, audit_project_id=None),
    )
    assert outcome.state is not ScopeClassificationState.PROJECT_SCOPE_CONFIRMED
    assert outcome.state is not ScopeClassificationState.ORG_SCOPE_CONFIRMED


def test_a_real_legacy_project_confirms_project_scope():
    """The one automatic path that positive evidence does support."""
    outcome = classify_scope(
        _candidate(),
        signal=ScopeSignal(
            legacy_project_id="project-7", audit_row_present=True, audit_project_id="project-7"
        ),
    )
    assert outcome.state is ScopeClassificationState.PROJECT_SCOPE_CONFIRMED
    assert outcome.project_id == "project-7"


def test_organisation_scope_has_no_automatic_path_at_all():
    """No combination of legacy signals may reach ORG_SCOPE_CONFIRMED."""
    for legacy in (None, "", "   "):
        for present in (True, False):
            for audit in (None, "", "project-7"):
                outcome = classify_scope(
                    _candidate(),
                    signal=ScopeSignal(
                        legacy_project_id=legacy,
                        audit_row_present=present,
                        audit_project_id=audit,
                    ),
                )
                assert outcome.state is not ScopeClassificationState.ORG_SCOPE_CONFIRMED


def test_organisation_scope_is_reachable_only_by_operator_adjudication():
    import inspect

    from rbac_backend.services import contract_migration_classifiers

    source = inspect.getsource(contract_migration_classifiers.classify_scope)
    assert "ORG_SCOPE_CONFIRMED" not in source


# --------------------------------------------------------------------------- #
# M09-20 : cross-organisation
# --------------------------------------------------------------------------- #


def test_a_cross_organisation_candidate_fails_closed():
    """M09-20."""
    outcome = classify_scope(
        _candidate(organization_id="org-1"),
        signal=ScopeSignal(
            legacy_project_id="project-7",
            audit_row_present=True,
            audit_project_id="project-7",
            project_organization_id="org-2",
        ),
    )
    assert outcome.state is ScopeClassificationState.INVALID
    assert outcome.state is not ScopeClassificationState.PROJECT_SCOPE_CONFIRMED


def test_a_cross_organisation_invalid_is_terminal_not_ambiguous():
    outcome = classify_scope(
        _candidate(organization_id="org-1"),
        signal=ScopeSignal(
            legacy_project_id="project-7",
            audit_row_present=True,
            audit_project_id="project-7",
            project_organization_id="org-2",
        ),
    )
    assert outcome.state is not ScopeClassificationState.AMBIGUOUS
    assert outcome.terminal is True


# --------------------------------------------------------------------------- #
# M09-04 / M09-05 : type
# --------------------------------------------------------------------------- #


def test_a_suggested_type_cannot_promote_itself():
    """M09-04."""
    outcome = classify_type(
        _candidate(),
        suggestions=(
            TypeSuggestion(
                contract_document_type=ContractDocumentType.GENERAL_CONDITIONS,
                basis="filename",
            ),
        ),
    )
    assert outcome.state is TypeClassificationState.TYPE_SUGGESTED
    assert outcome.state is not TypeClassificationState.TYPE_RESOLVED


def test_three_agreeing_heuristics_are_still_not_resolution():
    """They read the same substring, so three agreements are one, counted thrice."""
    outcome = classify_type(
        _candidate(),
        suggestions=tuple(
            TypeSuggestion(
                contract_document_type=ContractDocumentType.GENERAL_CONDITIONS, basis=basis
            )
            for basis in ("filename", "appraisal_table", "clause_branch")
        ),
    )
    assert outcome.state is TypeClassificationState.TYPE_SUGGESTED


def test_type_resolution_requires_operator_confirmation():
    outcome = classify_type(
        _candidate(),
        suggestions=(),
        operator_confirmation=ContractDocumentType.PARTICULAR_CONDITIONS,
    )
    assert outcome.state is TypeClassificationState.TYPE_RESOLVED
    assert outcome.contract_document_type is ContractDocumentType.PARTICULAR_CONDITIONS


def test_unknown_never_maps_to_other_contractual_document():
    """M09-05. OTHER is an affirmative choice, not a bucket."""
    outcome = classify_type(_candidate(), suggestions=())
    assert outcome.state is TypeClassificationState.TYPE_UNKNOWN
    assert outcome.contract_document_type is None
    assert outcome.contract_document_type is not ContractDocumentType.OTHER_CONTRACTUAL_DOCUMENT


def test_no_code_path_defaults_to_other_contractual_document():
    import inspect

    from rbac_backend.services import contract_migration_classifiers

    source = inspect.getsource(contract_migration_classifiers.classify_type)
    assert "OTHER_CONTRACTUAL_DOCUMENT" not in source


def test_an_operator_may_deliberately_choose_other():
    outcome = classify_type(
        _candidate(),
        suggestions=(),
        operator_confirmation=ContractDocumentType.OTHER_CONTRACTUAL_DOCUMENT,
    )
    assert outcome.state is TypeClassificationState.TYPE_RESOLVED
    assert outcome.contract_document_type is ContractDocumentType.OTHER_CONTRACTUAL_DOCUMENT


# --------------------------------------------------------------------------- #
# M09-03 : the two axes are independent
# --------------------------------------------------------------------------- #


def test_scope_and_type_resolve_independently():
    """M09-03."""
    candidate = _candidate()
    scope = classify_scope(
        candidate,
        signal=ScopeSignal(
            legacy_project_id="project-7", audit_row_present=True, audit_project_id="project-7"
        ),
    )
    type_outcome = classify_type(candidate, suggestions=())

    assert scope.state is ScopeClassificationState.PROJECT_SCOPE_CONFIRMED
    assert type_outcome.state is TypeClassificationState.TYPE_UNKNOWN


def test_a_resolved_scope_is_never_spent_as_partial_type_authority():
    candidate = _candidate()
    classify_scope(
        candidate,
        signal=ScopeSignal(
            legacy_project_id="project-7", audit_row_present=True, audit_project_id="project-7"
        ),
    )
    type_outcome = classify_type(candidate, suggestions=())
    assert type_outcome.state is TypeClassificationState.TYPE_UNKNOWN


def test_the_type_classifier_cannot_see_the_scope_outcome():
    import inspect

    parameters = set(inspect.signature(classify_type).parameters)
    assert "scope" not in parameters
    assert "scope_state" not in parameters


def test_the_scope_classifier_cannot_see_the_type_outcome():
    import inspect

    parameters = set(inspect.signature(classify_scope).parameters)
    assert "type_state" not in parameters
    assert "contract_document_type" not in parameters


def test_a_resolved_type_does_not_advance_scope():
    candidate = _candidate()
    classify_type(
        candidate, suggestions=(), operator_confirmation=ContractDocumentType.GENERAL_CONDITIONS
    )
    scope = classify_scope(
        candidate,
        signal=ScopeSignal(legacy_project_id=None, audit_row_present=False, audit_project_id=None),
    )
    assert scope.state is ScopeClassificationState.AMBIGUOUS


# --------------------------------------------------------------------------- #
# no automatic applicability
# --------------------------------------------------------------------------- #


def test_the_classifiers_never_produce_applicability():
    import inspect

    from rbac_backend.services import contract_migration_classifiers

    import ast

    source = inspect.getsource(contract_migration_classifiers)
    tree = ast.parse(source)
    # Assert on code, not prose: the module docstring legitimately explains why
    # it produces no applicability, and a substring check fails on that sentence.
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not [name for name in imported if "applicab" in name.lower()]

    executable = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    ]
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.add(doc)
    literals = {c.value for c in executable if c.value not in docstrings}
    assert not [text for text in literals if "APPLIED" in text]


def test_a_classifier_outcome_is_not_an_applicable_instrument():
    from rbac_backend.models.contract_document import require_applicable_instruments

    outcome = classify_type(_candidate(), suggestions=())
    with pytest.raises(TypeError):
        require_applicable_instruments((outcome,))
