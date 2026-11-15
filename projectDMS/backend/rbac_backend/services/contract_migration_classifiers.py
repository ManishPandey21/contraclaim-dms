"""Scope and type classifiers for legacy contract candidates.

Two axes, deliberately independent, and neither resolvable automatically where
the corpus lacks the evidence.

**Scope.** There is no automatic path to ``ORG_SCOPE_CONFIRMED`` — the constant
does not appear in this module at all. The reason is a property of the legacy
data rather than caution: the upload path collapsed null and empty before any
durable write, so a surviving audit null records only "no project was in the
session". That is an absence one level removed from the question being asked,
and the question is whether somebody *decided* this document belongs to the
organisation. Nobody did. It lifts the candidate to ``AMBIGUOUS`` with a hint,
and an operator decides.

The audit write was also best-effort and its failures were swallowed, so an
**absent** row carries no information in either direction — not toward
organisation scope, and not toward project scope either. It produces no hint,
because a hint derived from a write that may simply have failed is a fabrication.

**Type.** Heuristic agreement is not resolution. The filename detector, the
appraisal table and the clause branch all read the same substring, so three
agreeing heuristics are one heuristic counted three times. Suggestions inform an
operator; only an operator resolves. ``TYPE_UNKNOWN`` never maps to
``other_contractual_document`` — that is an affirmative classification somebody
chooses, not a bucket for the unrecognised.

**Independence.** Neither classifier can see the other's outcome; their
signatures make it impossible. A resolved axis is not partial authority over the
other one, and neither axis produces applicability: the corpus contains no legal
applicability evidence at all.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from ..models.contract_document import ContractDocumentType
from .contract_migration_reconciliation import (
    ReconciliationCandidate,
    ScopeClassificationState,
    TypeClassificationState,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ScopeOutcome",
    "ScopeSignal",
    "TypeOutcome",
    "TypeSuggestion",
    "classify_scope",
    "classify_type",
]

#: What a retained audit null actually records. Written out because the phrasing
#: is the entire argument: it is not "this is an organisation document".
_AUDIT_NULL_HINT = (
    "the creation audit retained a null project, which records only that "
    "no project was in the session - not that the document is organisation-owned"
)


@dataclass(frozen=True)
class ScopeSignal:
    """Everything the legacy corpus can say about one candidate's scope."""

    legacy_project_id: Optional[str]
    audit_row_present: bool
    audit_project_id: Optional[str]
    #: The owning organisation of `legacy_project_id`, where it resolves.
    project_organization_id: Optional[str] = None


@dataclass(frozen=True)
class ScopeOutcome:
    state: ScopeClassificationState
    project_id: Optional[str] = None
    hint: Optional[str] = None
    terminal: bool = False


@dataclass(frozen=True)
class TypeSuggestion:
    """A guess, with the basis that produced it. Never a resolution."""

    contract_document_type: ContractDocumentType
    basis: str


@dataclass(frozen=True)
class TypeOutcome:
    state: TypeClassificationState
    contract_document_type: Optional[ContractDocumentType] = None
    suggestions: Tuple[TypeSuggestion, ...] = ()


def classify_scope(
    candidate: ReconciliationCandidate, *, signal: ScopeSignal
) -> ScopeOutcome:
    """Classify scope from legacy signals alone.

    Takes no type input, and can reach exactly two conclusions on its own:
    project scope where a real project resolves, and ambiguity everywhere else.
    Organisation scope is an operator decision and is not expressible here.
    """
    legacy_project = (signal.legacy_project_id or "").strip()

    if legacy_project:
        owning_org = (signal.project_organization_id or "").strip()
        if owning_org and owning_org != candidate.organization_id:
            # Fail closed and stay closed: a candidate whose project belongs to
            # another organisation is not an ambiguity to be adjudicated, it is
            # a tenancy violation.
            logger.warning(
                "candidate %s references project %s owned by %s, not %s",
                candidate.candidate_id,
                legacy_project,
                owning_org,
                candidate.organization_id,
            )
            return ScopeOutcome(
                state=ScopeClassificationState.INVALID,
                terminal=True,
                hint="the referenced project belongs to a different organisation",
            )
        return ScopeOutcome(
            state=ScopeClassificationState.PROJECT_SCOPE_CONFIRMED,
            project_id=legacy_project,
        )

    if signal.audit_row_present:
        # A retained null is weak evidence of a question, not of an answer.
        return ScopeOutcome(state=ScopeClassificationState.AMBIGUOUS, hint=_AUDIT_NULL_HINT)

    # No row at all. The audit write was best-effort, so this is silence, and
    # silence gets no hint - a hint here would be inventing a finding from a
    # write that may simply have failed.
    return ScopeOutcome(state=ScopeClassificationState.AMBIGUOUS, hint=None)


def classify_type(
    candidate: ReconciliationCandidate,
    *,
    suggestions: Sequence[TypeSuggestion],
    operator_confirmation: Optional[ContractDocumentType] = None,
) -> TypeOutcome:
    """Classify instrument type.

    Takes no scope input. Only ``operator_confirmation`` can produce
    ``TYPE_RESOLVED``; suggestions accumulate but never promote themselves, no
    matter how many agree.
    """
    if operator_confirmation is not None:
        return TypeOutcome(
            state=TypeClassificationState.TYPE_RESOLVED,
            contract_document_type=operator_confirmation,
            suggestions=tuple(suggestions),
        )

    if suggestions:
        return TypeOutcome(
            state=TypeClassificationState.TYPE_SUGGESTED,
            contract_document_type=None,
            suggestions=tuple(suggestions),
        )

    # Unknown stays unknown. There is deliberately no fallback member here.
    return TypeOutcome(state=TypeClassificationState.TYPE_UNKNOWN)
