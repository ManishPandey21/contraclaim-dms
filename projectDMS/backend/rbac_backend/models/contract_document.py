"""Contract Master v1 domain types.

A contract instrument — a GCC, an SCC, a Letter of Acceptance, an amendment — is
a legal document owned by an organisation that governs one or more contracts for
defined periods. This module holds the categorical values that model tells apart,
and the two evidence result shapes that must never be confused.

Nothing here is persisted or consumed yet: later tickets own the collections, the
resolver and the consumers. What this module fixes is which states are
*representable at all*.

Three rules shape it:

* **Absence is never authority.** Scope, instrument type and query mode are
  explicit discriminators. None of them may be inferred from ``None``, ``""`` or
  a missing field.
* **Nothing derived may authorise anything.** ``ProjectionStatus`` describes a
  derived projection and may only ever exclude material from evidence.
* **Evidence readiness is derived, never stored.** It is the query-time
  conjunction of applicability, current document authority, projection currency
  and actor authorisation, so no enum here carries it.

Three separate taxonomies exist in this domain and are never conflated:
``ContractDocumentType`` (legal instrument), ``CategoryKey`` (clause subject,
defined in ``services.contract_categorizer``), and ``relationship_role``
(relational only).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import Enum
from typing import Iterable, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "ApplicabilityLifecycleKind",
    "ApplicabilityQueryMode",
    "ApplicableInstrument",
    "Browse",
    "BrowseResult",
    "ContractDocumentType",
    "ContractScopeLevel",
    "CurrentState",
    "Historical",
    "LegalEffectKind",
    "LegalEffectScope",
    "ProjectionStatus",
    "PromotionState",
    "ScopeClassification",
    "TypeClassification",
    "require_applicable_instruments",
]


class ContractScopeLevel(str, Enum):
    """Whether an instrument is owned organisation-wide or anchored to a project.

    An explicit discriminator, deliberately not a nullable ``project_id``. The
    legacy upload path collapses a null and an empty project into the same value
    across several layers, which is exactly why intent has to be carried as its
    own categorical field.
    """

    ORGANIZATION = "organization"
    PROJECT = "project"


class ContractDocumentType(str, Enum):
    """The legal-instrument taxonomy. Authoritative once resolved.

    ``other_contractual_document`` is an affirmative classification an operator
    chooses deliberately — never the bucket for anything unrecognised. There is
    no ``UNKNOWN`` member: not knowing the type is a *classification state*
    (:class:`TypeClassification`), not a kind of instrument.
    """

    GENERAL_CONDITIONS = "general_conditions"
    PARTICULAR_CONDITIONS = "particular_conditions"
    LETTER_OF_ACCEPTANCE = "letter_of_acceptance"
    EMPLOYERS_REQUIREMENTS = "employers_requirements"
    TECHNICAL_SPECIFICATION = "technical_specification"
    BOQ = "boq"
    PRICE_SCHEDULE = "price_schedule"
    ADDENDUM = "addendum"
    CORRIGENDUM = "corrigendum"
    AMENDMENT = "amendment"
    CONTRACT_AGREEMENT = "contract_agreement"
    LETTER_OF_INTENT = "letter_of_intent"
    DRAWING = "drawing"
    CONTRACT_SCHEDULE = "contract_schedule"
    SUPPLEMENTAL_AGREEMENT = "supplemental_agreement"
    VARIATION_AGREEMENT = "variation_agreement"
    OTHER_CONTRACTUAL_DOCUMENT = "other_contractual_document"


class TypeClassification(str, Enum):
    """How far classification has progressed — a state of knowing, not a thing
    known. A suggestion never becomes authoritative by construction; it becomes
    ``TYPE_RESOLVED`` only through an operator's affirmative act."""

    TYPE_RESOLVED = "TYPE_RESOLVED"
    TYPE_SUGGESTED = "TYPE_SUGGESTED"
    TYPE_AMBIGUOUS = "TYPE_AMBIGUOUS"
    TYPE_UNKNOWN = "TYPE_UNKNOWN"


class ScopeClassification(str, Enum):
    """How far scope resolution has progressed for a migration candidate.

    ``ORG_SCOPE_CONFIRMED`` has no automatic path from the legacy corpus: the
    upload chain destroyed the distinction between "no project chosen" and "no
    project applies", so it is reachable only by operator adjudication.
    """

    PROJECT_SCOPE_RESOLVED = "PROJECT_SCOPE_RESOLVED"
    ORG_SCOPE_CONFIRMED = "ORG_SCOPE_CONFIRMED"
    AMBIGUOUS = "AMBIGUOUS"
    INVALID = "INVALID"


class ApplicabilityLifecycleKind(str, Enum):
    """The legal fact itself. Events are append-only; ``effective_to`` is derived
    from the stream and never stored as a primary value."""

    APPLIED = "APPLIED"
    WITHDRAWN = "WITHDRAWN"
    SUPERSEDED = "SUPERSEDED"


class LegalEffectKind(str, Enum):
    """Precedence between two instruments.

    Never derived from a filename, a stored section type, a graph priority, a
    retrieval score, or a document type alone.
    """

    OVERRIDE = "OVERRIDE"
    SUPERSEDE = "SUPERSEDE"


class LegalEffectScope(str, Enum):
    """How much of the target instrument an effect reaches."""

    WHOLE_DOCUMENT = "WHOLE_DOCUMENT"
    CLAUSE_REFERENCES = "CLAUSE_REFERENCES"


class ProjectionStatus(str, Enum):
    """State of a DERIVED projection. It may exclude material from evidence; it
    may never authorise any."""

    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    CURRENT = "CURRENT"
    FAILED = "FAILED"


class PromotionState(str, Enum):
    """How far a candidate has travelled toward being an authoritative record.

    There is deliberately no ``EVIDENCE_CAPABLE`` member. Evidence capability is
    the query-time conjunction of applicability, current document authority,
    projection currency and actor authorisation — every one of which can change
    without this record being touched. Storing it would create a second
    authority source that goes stale silently.
    """

    RECONCILIATION = "RECONCILIATION"
    AUTHORITATIVE = "AUTHORITATIVE"
    QUARANTINED = "QUARANTINED"


# --------------------------------------------------------------------------- #
# Query mode — a closed sum, not an enum plus an optional date
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Browse:
    """Catalogue browsing. Cannot enter an evidence consumer."""


@dataclass(frozen=True)
class CurrentState:
    """What governs this contract now. Carries no date: there is no implicit
    "today" anywhere in this model."""


@dataclass(frozen=True)
class Historical:
    """What governed this contract on a given date.

    ``event_date`` is required and non-optional by construction. An enum plus a
    nullable date would let ``Historical`` exist without one and push the check
    to runtime, where a missing date silently becomes "now" — the failure this
    shape exists to prevent.
    """

    event_date: date

    def __post_init__(self) -> None:
        if self.event_date is None:
            raise ValueError("Historical requires an event_date; None is not a date")
        if not isinstance(self.event_date, date):
            raise TypeError(
                f"Historical.event_date must be a date, got {type(self.event_date).__name__}"
            )


#: The only three ways to ask. No fallback in either direction: ``Historical``
#: never silently becomes ``CurrentState``, and ``Browse`` never becomes either.
ApplicabilityQueryMode = Union[Browse, CurrentState, Historical]


# --------------------------------------------------------------------------- #
# Result types — structurally distinct, not one shape with a flag
# --------------------------------------------------------------------------- #


class BrowseResult(BaseModel):
    """A catalogue entry. Answers "what exists?", never "what governs?".

    Deliberately carries no document version, no applicability basis and no
    contract — it has nothing an evidence consumer would need, so passing one
    into evidence cannot half-work.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_document_id: str
    organization_id: str
    contract_document_type: ContractDocumentType
    scope_level: ContractScopeLevel
    applicability_count: int = Field(ge=0)


class ApplicableInstrument(BaseModel):
    """An instrument that governs a specific contract, resolved canonically.

    Carries the identifiers a consumer needs to write provenance without
    reconstructing anything: the instrument, the canonical document, the exact
    version consumed, the contract it was resolved for, the classification
    generation in force, and the applicability event that is the legal basis.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_document_id: str
    document_id: str
    document_version_id: str
    organization_id: str
    project_id: str
    contract_id: str
    contract_document_type: ContractDocumentType
    classification_revision: int = Field(ge=1)
    applicability_event_id: str


def require_applicable_instruments(
    candidates: Iterable[object],
) -> Tuple[ApplicableInstrument, ...]:
    """Return ``candidates`` as instruments, or raise ``TypeError``.

    The boundary an evidence consumer accepts material through. Python does not
    enforce annotations at runtime, so a ``BrowseResult`` or a correctly-shaped
    dict would otherwise flow into a prompt as if it were resolved evidence —
    the annotation alone is documentation, not a guard.

    Rejects the whole batch on the first offender rather than filtering: silently
    dropping one bad element would let a caller believe it had passed more
    evidence than it did.
    """
    accepted: list[ApplicableInstrument] = []
    for index, candidate in enumerate(candidates):
        if not isinstance(candidate, ApplicableInstrument):
            raise TypeError(
                "evidence requires ApplicableInstrument, got "
                f"{type(candidate).__name__} at position {index}. A catalogue "
                "entry or a mapping is not resolved evidence; resolve it through "
                "the contract scope resolver first."
            )
        accepted.append(candidate)
    return tuple(accepted)
