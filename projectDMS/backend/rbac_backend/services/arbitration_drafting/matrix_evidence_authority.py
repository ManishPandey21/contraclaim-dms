"""One canonical eligibility answer for APPROVED case clause-matrix evidence.

**HUMAN APPROVAL IS NOT LEGAL APPLICABILITY.** A ``clause-matrix`` row records
that a human read a clause and judged it RELEVANT to the case. That is a
curation verdict. It cannot create - and is not a durable cache of -

* applicability of the instrument to this project,
* publication authority over the extracted text,
* projection currency of the clause index,
* Contract identity, or
* LegalEffect / precedence.

A row approved while its instrument governed stays approved after the instrument
is withdrawn, superseded, re-classified or sent back to human review; nothing
revisits the flag. So approval and canonical eligibility must be INTERSECTED,
never unioned::

      approved matrix rows
    & the canonical eligible universe for THIS ACTOR

Both conditions are necessary and neither is sufficient. The approval gate stays
exactly where it was (``_is_verified_source`` / ``_is_ready_row``); this module
adds the second half and nothing else.

There is exactly ONE implementation of the second half in the codebase -
``resolve_authorized_project_universe`` - and this module consumes it rather
than deriving eligibility again. G-A9 (automatic contract evidence), G-A11
(user-selected references) and G-A12 (evidence-search suggestions) all read the
same seam; a fourth answer here would drift from the other three silently, which
is the failure mode this programme keeps finding.

Two doors share this fence and must not diverge:

* ``ArbitrationContextBuilder._clause_matrix_sources`` - the automatic ledger;
* ``ArbitrationContextBuilder._rehydrate_matrix_reference`` - the rehydration of
  a stored selection that names a matrix row, which is what
  ``ArbitrationCaseWorkspaceService.prepare_draft_from_case`` writes.

WHAT IS FENCED, AND WHAT DELIBERATELY IS NOT.

The fence applies to a clause-matrix row that NAMES A DOCUMENT-GOVERNED CLAUSE
SOURCE. Three shapes are deliberately outside it, because fencing them would be
an outage dressed as a containment:

* the synthesised arbitration agreement (``case:{id}:arbitration_clause``) -
  prose a user typed onto the case record. There is no extraction behind it, so
  there is no authority to resolve, and withholding it would remove the
  jurisdictional foundation of the pleading;
* a row that names NO clause source at all - matrix-authored application
  content. Its ``clause_text_excerpt`` is already withheld by publication
  authority (there is no id to resolve), so nothing document-derived can escape,
  and counsel's own ``obligation_or_right`` must survive;
* every non-clause matrix (document index, issues, claims, quantum, notices,
  jurisdiction). Those are case records, not contract instruments, and have no
  applicability aggregate.

FAIL CLOSED, AND NEVER WIDEN. An unresolvable universe and an empty universe
both refuse every document-governed row. Neither falls back to the row's own
``project_id`` / ``contract_id`` / approval flag: those record where the row was
FILED and who signed it off, never that the instrument governs anything. A
failure is surfaced as a context warning rather than collapsed into a silent
empty ledger, because a pleading assembled without the contract it turns on must
not look identical to one where no contract applies.

IDENTITY: PROJECT-EVIDENCE CONTAINMENT, carried unchanged from G-A7/G-A8/G-A9/
G-A11/G-A12. The arbitration draft's ``contract_id`` is free text that nothing
joins against the applicability records, so a clause governing a sibling
contract inside the same authorised project is still admitted. Using the matrix
row's own ``contract_id`` to narrow that would be precisely the
self-authorisation this module removes.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ...models.contract_document import CurrentState
from ..contract_scope_resolver import resolve_authorized_project_universe
from ..publication_policy import resolve_derived_authority

#: The ``AuthorityDecision.reason`` that means "this identifier names content the
#: APPLICATION authored, not an extraction". Only the synthesised arbitration
#: agreement produces it for a clause source. Kept as an exact-match set rather
#: than a prefix test so a new reason cannot silently opt out of the fence.
APPLICATION_AUTHORED_REASONS = frozenset({"application_generated_source"})

#: The message a consumer records when eligibility could not be ANSWERED. A
#: failure and an empty universe both produce zero matrix clause evidence, but
#: only one of them is a broken lookup, and the two must stay distinguishable.
UNRESOLVED_WARNING = (
    "Approved clause-matrix evidence was omitted: contract eligibility could "
    "not be resolved ({reason})."
)


def clause_source_id(row: Optional[Dict[str, Any]]) -> Any:
    """The id a clause-matrix row uses to name its source clause.

    ``clause_source_id`` is the field the writer sets; ``source_id`` is accepted
    because ``ArbitrationMatrixRow`` is ``extra="allow"`` and older rows carry
    it. Deliberately NOT ``document_id``: no clause-matrix writer sets one, and
    accepting it would let a client name a parent document directly.
    """
    if not row:
        return None
    return row.get("clause_source_id") or row.get("source_id")


class ApprovedMatrixClauseFence:
    """Canonical eligibility for approved clause-matrix rows, resolved once.

    One instance covers one assembly pass: the universe is resolved at most once
    however many rows are examined, and each distinct clause source is resolved
    at most once. A page of approved rows must not become a page of applicability
    enumerations.
    """

    def __init__(
        self,
        db: Any,
        *,
        organization_id: Any,
        project_id: Any,
        current_user: Any,
        warnings: Optional[list] = None,
    ) -> None:
        self._db = db
        self._organization_id = str(organization_id or "")
        self._project_id = str(project_id or "")
        self._current_user = current_user
        self._warnings = warnings
        self._eligible: Optional[frozenset] = None
        self._resolved = False
        self._row_cache: Dict[str, bool] = {}

    async def eligible_document_ids(self) -> Optional[frozenset]:
        """``None`` means unanswerable; an empty set means nothing applies.

        Both refuse every document-governed row, and neither may widen: a matrix
        approval cannot expand authority, so there is no broader lookup that
        would be safer than none.
        """
        if self._resolved:
            return self._eligible
        self._resolved = True
        if not self._organization_id or not self._project_id:
            # No workspace in hand is an unanswerable question, not an empty
            # answer. A caller that omits the draft must not resolve rows
            # against the matrix row's own provenance.
            self._eligible = None
            self._warn("no drafting workspace in hand")
            return self._eligible
        try:
            universe = await resolve_authorized_project_universe(
                self._db,
                self._current_user,
                organization_id=self._organization_id,
                project_id=self._project_id,
                mode=CurrentState(),
            )
        except Exception as exc:  # noqa: BLE001 - recorded, never degraded
            self._eligible = None
            self._warn(str(exc))
            return self._eligible
        self._eligible = frozenset(str(item) for item in universe.eligible_document_ids)
        return self._eligible

    def _warn(self, reason: str) -> None:
        if self._warnings is None:
            return
        message = UNRESOLVED_WARNING.format(reason=reason)
        if message not in self._warnings:
            self._warnings.append(message)

    async def admits_clause_row(self, row: Optional[Dict[str, Any]]) -> bool:
        """May this APPROVED clause-matrix row contribute to this pleading?

        Approval is assumed to have been checked by the caller - it is the
        matrix's own gate and this module does not restate it. What is decided
        here is only the canonical half.
        """
        source_id = clause_source_id(row)
        if source_id in (None, ""):
            # Names no contract instrument: matrix-authored application content.
            # Its derived excerpt is already withheld by publication authority
            # (there is no id to resolve), so there is nothing to contain.
            return True
        key = str(source_id)
        if key in self._row_cache:
            return self._row_cache[key]
        self._row_cache[key] = await self._decide(source_id)
        return self._row_cache[key]

    async def _decide(self, source_id: Any) -> bool:
        try:
            decision = await resolve_derived_authority(self._db, "clause", source_id)
        except Exception:  # noqa: BLE001 - uncertainty denies on a serve path
            return False
        if decision.reason in APPLICATION_AUTHORED_REASONS:
            # Application-authored content under a minted identifier. No
            # extraction, no instrument, nothing for applicability to say.
            return True
        if not decision.consumable:
            # Publication authority already denies: human review, quarantine,
            # an orphaned id, or a document that resolves to nothing. Positive
            # resolution is required, so uncertainty denies.
            return False
        document_id = decision.document_id
        if not document_id:
            return False
        eligible = await self.eligible_document_ids()
        if not eligible:
            return False
        return str(document_id) in eligible


def matrix_clause_fence(
    db: Any,
    draft: Optional[Dict[str, Any]],
    current_user: Any,
    warnings: Optional[list] = None,
) -> ApprovedMatrixClauseFence:
    """Build the fence from the draft whose pleading is being assembled.

    A missing draft or a missing principal produces a fence that refuses every
    document-governed row rather than one that trusts the matrix. Absent
    authority denies; it never falls back.
    """
    return ApprovedMatrixClauseFence(
        db,
        organization_id=(draft or {}).get("organization_id"),
        project_id=(draft or {}).get("project_id"),
        current_user=current_user,
        warnings=warnings,
    )


__all__ = [
    "APPLICATION_AUTHORED_REASONS",
    "ApprovedMatrixClauseFence",
    "UNRESOLVED_WARNING",
    "clause_source_id",
    "matrix_clause_fence",
]
