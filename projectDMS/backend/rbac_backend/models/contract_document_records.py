"""Persisted Contract Master records.

The domain *types* live in :mod:`rbac_backend.models.contract_document` and are
frozen by implementation ticket 02. This module is what actually lands in Mongo.

Every field carries exactly one semantic class, and the class is stated in its
description so a later reader cannot mistake one for another:

``AUTHORITATIVE``
    The legal answer. Nothing else may answer the same question.
``DERIVED``
    Computed from authoritative state. May exclude material from evidence; may
    never authorise any.
``AUDIT``
    A record of who did what, when. Never consulted to decide anything.
``OPERATIONAL``
    Process bookkeeping — claims, leases, run ids. Never a legal fact.
``PROVENANCE``
    A record of what a run consumed. Never a reusable authority token.

Two absences are deliberate and load-bearing:

* there is no ``effective_to`` anywhere — it is derived from the append-only
  lifecycle event stream, and a stored copy would be a second answer that can
  disagree with the first;
* there is no ``evidence_capable`` / ``is_evidence_ready`` / ``searchable``
  field — evidence readiness is the query-time conjunction of applicability,
  current document authority, projection currency and actor authorisation, every
  one of which changes without this record being touched.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

from .contract_document import (
    ApplicabilityLifecycleKind,
    ContractDocumentType,
    ContractScopeLevel,
    LegalEffectKind,
    LegalEffectScope,
    ProjectionStatus,
    PromotionState,
)

__all__ = [
    "ApplicabilityLifecycleEventRecord",
    "ContractDocumentApplicabilityRecord",
    "ContractDocumentClassificationFactRecord",
    "ContractDocumentLegalEffectRecord",
    "ContractDocumentRecord",
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ContractDocumentRecord(BaseModel):
    """An organisation-owned legal instrument."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    contract_document_id: str = Field(alias="_id", description="AUTHORITATIVE")
    organization_id: str = Field(description="AUTHORITATIVE")

    #: The canonical 1:1 instrument identity. A direct field, not a link row —
    #: a second representation of the same fact is a second thing to keep in sync.
    document_id: str = Field(description="AUTHORITATIVE")

    #: Explicit discriminator. Never inferred from a null or empty project.
    scope_level: ContractScopeLevel = Field(description="AUTHORITATIVE")

    #: The structural project anchor, present iff scope_level is project.
    #: Immutable once authoritative, and never derived from the first
    #: applicability — deriving it would make ownership time-varying while link
    #: identity assumes it fixed.
    scope_project_id: Optional[str] = Field(default=None, description="AUTHORITATIVE")

    contract_document_type: Optional[ContractDocumentType] = Field(
        default=None, description="AUTHORITATIVE (current value of the fact stream)"
    )

    #: Monotonic generation, +1 per resolved classification fact. An integer, not
    #: a timestamp, so nothing can select "newest"; not filename-derived, so no
    #: mutable value acts as identity.
    classification_revision: int = Field(default=0, ge=0, description="AUTHORITATIVE")

    projection_revision: Optional[int] = Field(default=None, ge=0, description="DERIVED")
    projection_status: ProjectionStatus = Field(
        default=ProjectionStatus.PENDING, description="DERIVED"
    )

    promotion_state: PromotionState = Field(
        default=PromotionState.RECONCILIATION, description="OPERATIONAL"
    )

    created_at: datetime = Field(default_factory=_now, description="AUDIT")
    created_by: Optional[str] = Field(default=None, description="AUDIT")


class ContractDocumentClassificationFactRecord(BaseModel):
    """One append-only assertion of what kind of instrument this is.

    A correction appends a new fact and increments the document's revision.
    Nothing here is ever updated or deleted, so the history of what was believed,
    and when, survives a correction.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    fact_id: str = Field(alias="_id", description="AUTHORITATIVE")
    contract_document_id: str = Field(description="AUTHORITATIVE")
    contract_document_type: ContractDocumentType = Field(description="AUTHORITATIVE")

    #: The generation this fact establishes. Unique per instrument.
    revision: int = Field(ge=1, description="AUTHORITATIVE")

    #: How the type was established: an operator confirming, or a migration
    #: adjudication. A filename heuristic is never a basis.
    basis: str = Field(description="AUTHORITATIVE")

    actor_id: Optional[str] = Field(default=None, description="AUDIT")
    asserted_at: datetime = Field(default_factory=_now, description="AUDIT")


class ContractDocumentApplicabilityRecord(BaseModel):
    """The stable aggregate: this instrument, that contract.

    Deliberately holds no interval. The aggregate supplies the index; the
    lifecycle events supply the authority, and ``effective_to`` is computed by
    replaying them.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    applicability_id: str = Field(alias="_id", description="AUTHORITATIVE")
    organization_id: str = Field(description="AUTHORITATIVE")
    contract_document_id: str = Field(description="AUTHORITATIVE")
    project_id: str = Field(min_length=1, description="AUTHORITATIVE")
    contract_id: str = Field(min_length=1, description="AUTHORITATIVE")

    created_at: datetime = Field(default_factory=_now, description="AUDIT")


class ApplicabilityLifecycleEventRecord(BaseModel):
    """The legal fact itself. Append-only; never updated, never deleted."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    event_id: str = Field(alias="_id", description="AUTHORITATIVE")
    applicability_id: str = Field(description="AUTHORITATIVE")
    kind: ApplicabilityLifecycleKind = Field(description="AUTHORITATIVE")

    #: LEGAL effective time. ``None`` means UNKNOWN — never "since forever", and
    #: never substituted from a creation timestamp, an upload date or an audit
    #: row. A historical question that needs a proven start fails closed instead.
    effective_at: Optional[date] = Field(default=None, description="AUTHORITATIVE")

    #: Wall-clock time the event was written. Distinct from legal time: replay
    #: orders by ``effective_at``, so a newest-created event never wins by virtue
    #: of being newest.
    recorded_at: datetime = Field(default_factory=_now, description="AUDIT")

    actor_id: Optional[str] = Field(default=None, description="AUDIT")
    reason: Optional[str] = Field(default=None, description="AUDIT")


class ContractDocumentLegalEffectRecord(BaseModel):
    """One instrument overriding or superseding another, for one contract.

    Precedence comes from here and from nowhere else — never from a filename, a
    stored section type, a graph priority, a retrieval score, or a document type
    alone.
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    effect_id: str = Field(alias="_id", description="AUTHORITATIVE")
    organization_id: str = Field(description="AUTHORITATIVE")
    source_contract_document_id: str = Field(description="AUTHORITATIVE")
    target_contract_document_id: str = Field(description="AUTHORITATIVE")
    effect: LegalEffectKind = Field(description="AUTHORITATIVE")
    scope: LegalEffectScope = Field(description="AUTHORITATIVE")

    #: Populated when scope is CLAUSE_REFERENCES; empty for WHOLE_DOCUMENT.
    clause_references: List[str] = Field(default_factory=list, description="AUTHORITATIVE")

    project_id: str = Field(min_length=1, description="AUTHORITATIVE")
    contract_id: str = Field(min_length=1, description="AUTHORITATIVE")
    effective_from: Optional[date] = Field(default=None, description="AUTHORITATIVE")

    actor_id: Optional[str] = Field(default=None, description="AUDIT")
    created_at: datetime = Field(default_factory=_now, description="AUDIT")
