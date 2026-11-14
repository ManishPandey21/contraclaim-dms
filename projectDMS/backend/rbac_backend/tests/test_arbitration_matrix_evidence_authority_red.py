"""G-A13 / R24: matrix APPROVAL is a curation verdict, not an applicability verdict.

G-A9 (R19) closed the AUTOMATIC contract-evidence door, G-A11 (R20) the
USER-SELECTED reference door and G-A12 (R21) the SUGGESTION door. This file
closes the fourth and last known arbitration evidence door, and it is a
different class again: a human has already APPROVED the row.

Two production paths publish approved case clause-matrix evidence:

* ``ArbitrationContextBuilder._clause_matrix_sources`` - the AUTOMATIC door.
  Every approved ``arbitration_clause_matrix`` row of the linked case becomes a
  ``source_ledger`` entry on every ``build``;
* ``ArbitrationContextBuilder._rehydrate_matrix_reference`` (slug
  ``clause-matrix``, reached when a stored selection carries
  ``metadata.matrix_row_id``) - the REHYDRATION door, which is what
  ``ArbitrationCaseWorkspaceService.prepare_draft_from_case`` writes.

Both resolved a row by ``case_id`` and then asked exactly two questions:

* is the row APPROVED (``_is_verified_source``);
* is the parent document currently publication-consumable
  (``consumable_derived_text``).

Neither asked:

* is the instrument APPLICABLE to this project at all, or merely catalogued;
* is it still in force, or WITHDRAWN / SUPERSEDED;
* is its clause projection CURRENT, or a revision behind, PENDING or FAILED;
* does the CALLER have canonical authority over the workspace being drafted in
  (neither door received a principal at all).

**HUMAN APPROVAL IS NOT LEGAL APPLICABILITY.** Approval records that a human
judged a source RELEVANT and chose it. It cannot create applicability,
publication authority, projection currency, Contract identity or LegalEffect,
and it is not a durable cache of any of them: a row approved while its
instrument governed stays approved after the instrument is withdrawn.

The correct model is INTERSECTION, never union::

      approved case matrix rows
    & applicable to (project_id, contract_id) at the query mode
    & canonical Document positively resolvable
    & publication-consumable
    & projection-current

resolved for an actor canonically entitled to the workspace - i.e.
``resolve_authorized_project_universe``, the seam G-A9 built and G-A11/G-A12
reused. Approval remains NECESSARY (an unapproved row is still refused);
canonical eligibility is now necessary too. Neither is sufficient alone.

Why this is material rather than cosmetic. A clause-matrix ledger row is:

* rendered into the pleading by the generator, so ``clause_text_excerpt`` -
  up to 900 characters of the parent document's extracted text - becomes draft
  text;
* hashed into ``stable_generation_input_hash``, which decides run reuse;
* written into ``arbitration_draft_versions.source_ledger``, a row sealed by
  ``immutable_version_hash`` and rendered by the DOCX/PDF exporter;
* written as ``arbitration_generation_runs.source_ids``;
* persisted into ``arbitration_selected_references`` by
  ``prepare_draft_from_case``, where it becomes the draft's standing evidence.

Text and PROVENANCE are therefore asserted separately. Blanking the excerpt
while keeping the row's ``source_id``, ``clause_number`` and citation still
records a foreign instrument as a contributor to a pleading, in a row that is
sealed and exported.

**Scope, stated rather than assumed.**

* The fence is CLAUSE-scoped and DOCUMENT-GOVERNED-scoped. A ``document-index``
  row, an issue/claim/quantum matrix row, and a clause row that names no
  document source at all are matrix-authored application content with no
  applicability aggregate; fencing them on the contract universe would delete
  counsel's own words from live pleadings.
  :func:`test_a_matrix_authored_clause_row_with_no_document_source_keeps_its_own_text`
  pins that.
* The synthesised arbitration agreement
  (``case:{case_id}:arbitration_clause``) is prose a user typed onto the case.
  It names no extraction, so there is no authority to resolve and it must stay
  unfenced - :func:`test_the_synthesised_arbitration_agreement_is_not_fenced`.
* PROJECT-EVIDENCE CONTAINMENT, carried unchanged from G-A7/G-A8/G-A9/G-A11/
  G-A12. The arbitration draft's ``contract_id`` is free text that nothing
  joins against ``contract_document_applicability``, and a matrix row's own
  ``contract_id`` is row provenance, not identity - using it would be exactly
  the self-authorisation this phase removes. A clause governing sibling
  contract ``OTHER`` inside the SAME authorised project therefore still
  contributes. Pinned, not implied, by
  :func:`test_r24_provides_project_evidence_containment_not_contract_identity`.
* Historical matrix state is untouched. An ineligible row keeps its approval
  and stays in ``arbitration_clause_matrix`` as case history; what changes is
  only what this generation's evidence ledger may claim.
"""

from __future__ import annotations

import ast
import inspect
from copy import deepcopy
from datetime import datetime
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pytest
from bson import ObjectId
from fastapi import HTTPException

from rbac_backend.services.arbitration_drafting import context as arbitration_context
from rbac_backend.services.arbitration_drafting.case_workspace import (
    ArbitrationCaseWorkspaceService,
)
from rbac_backend.services.arbitration_drafting.context import ArbitrationContextBuilder
from rbac_backend.services.arbitration_drafting.service import ArbitrationDraftingService
from rbac_backend.services.publication_policy import synthetic_case_clause_id

# --- unique markers -------------------------------------------------------
#
# Every marker lives ONLY inside clause TEXT (the row's `clause_text_excerpt`
# and its parent chunk's text), never inside an identifier, so a rejection
# receipt that echoes a requested id can never be mistaken for leaked evidence.

AUTHORISED_MARKER = "R24_APPROVED_ELIGIBLE_AUTHORISED_20260831"
SIBLING_PROJECT_MARKER = "R24_PROJECT_B_APPROVED_MATRIX_CONFIDENTIAL_20260831"
NO_APPLICABILITY_MARKER = "R24_CATALOGUE_ONLY_APPROVED_CONFIDENTIAL_20260831"
WITHDRAWN_MARKER = "R24_WITHDRAWN_APPROVED_CONFIDENTIAL_20260831"
SUPERSEDED_MARKER = "R24_SUPERSEDED_APPROVED_CONFIDENTIAL_20260831"
HUMAN_REVIEW_MARKER = "R24_HUMAN_REVIEW_APPROVED_CONFIDENTIAL_20260831"
DUPLICATE_MARKER = "R24_DUPLICATE_APPROVED_CONFIDENTIAL_20260831"
DELETED_MARKER = "R24_DELETED_APPROVED_CONFIDENTIAL_20260831"
ORPHAN_MARKER = "R24_ORPHAN_APPROVED_CONFIDENTIAL_20260831"
LYING_ROW_MARKER = "R24_LYING_ROW_APPROVED_CONFIDENTIAL_20260831"
STALE_PROJECTION_MARKER = "R24_STALE_PROJECTION_APPROVED_CONFIDENTIAL_20260831"
PENDING_PROJECTION_MARKER = "R24_PENDING_PROJECTION_APPROVED_CONFIDENTIAL_20260831"
FAILED_PROJECTION_MARKER = "R24_FAILED_PROJECTION_APPROVED_CONFIDENTIAL_20260831"
OPERATIONAL_FAILED_MARKER = "R24_OPERATIONAL_FAILED_LAST_KNOWN_GOOD_20260831"
UNAPPROVED_MARKER = "R24_UNAPPROVED_BUT_ELIGIBLE_CONFIDENTIAL_20260831"
SYNTHETIC_MARKER = "R24_SYNTHESISED_ARBITRATION_AGREEMENT_AUTHORISED_20260831"
IN_APP_MARKER = "R24_MATRIX_AUTHORED_OBLIGATION_AUTHORISED_20260831"
SIBLING_CONTRACT_MARKER = "R24_SIBLING_CONTRACT_SAME_PROJECT_20260831"

APPLICABILITY = "contract_document_applicability"
APPLICABILITY_EVENTS = "contract_document_applicability_events"
CONTRACT_DOCUMENTS = "contract_documents"
CONTRACT_CLAUSES = "contract_clauses"
DOCUMENT_VECTORS = "document_vectors"
CLAUSE_MATRIX = "arbitration_clause_matrix"
SELECTED_REFERENCES = "arbitration_selected_references"

CASE_ID = "case-1"


# ---------------------------------------------------------------------------
# Minimal in-memory Mongo, faithful to the operators the production code uses
# ---------------------------------------------------------------------------


def _same(left: Any, right: Any) -> bool:
    return str(getattr(left, "value", left)) == str(getattr(right, "value", right))


def _field(document: Dict[str, Any], key: str) -> Tuple[bool, Any]:
    value: Any = document
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            return False, None
        value = value[part]
    return True, value


def _matches(document: Dict[str, Any], query: Optional[Dict[str, Any]]) -> bool:
    for key, expected in (query or {}).items():
        if key == "$and":
            if not all(_matches(document, branch) for branch in expected):
                return False
            continue
        if key == "$or":
            if not any(_matches(document, branch) for branch in expected):
                return False
            continue
        exists, actual = _field(document, key)
        if isinstance(expected, dict):
            if "$exists" in expected and exists is not bool(expected["$exists"]):
                return False
            if "$in" in expected and not any(_same(actual, c) for c in expected["$in"]):
                return False
            if "$nin" in expected and any(_same(actual, c) for c in expected["$nin"]):
                return False
            if "$ne" in expected and _same(actual, expected["$ne"]):
                return False
            continue
        if not _same(actual, expected):
            return False
    return True


def _sort_key(row: Dict[str, Any], field: str) -> Tuple[int, str]:
    value = row.get(field)
    if value is None:
        return (0, "")
    if isinstance(value, (int, float)):
        return (1, "{:020.4f}".format(float(value)))
    return (1, value.isoformat() if isinstance(value, datetime) else str(value))


class _Cursor:
    def __init__(self, rows: Iterable[Dict[str, Any]]) -> None:
        self.rows = [deepcopy(row) for row in rows]

    def sort(self, key: Any = None, direction: int = 1) -> "_Cursor":
        if isinstance(key, str):
            self.rows.sort(key=lambda row: _sort_key(row, key), reverse=direction < 0)
        elif isinstance(key, list) and key:
            field, direction = key[0]
            self.rows.sort(key=lambda row: _sort_key(row, field), reverse=direction < 0)
        return self

    def skip(self, amount: int) -> "_Cursor":
        self.rows = self.rows[amount:]
        return self

    def limit(self, amount: int) -> "_Cursor":
        self.rows = self.rows[:amount]
        return self

    async def to_list(self, length: Optional[int] = None) -> List[Dict[str, Any]]:
        return deepcopy(self.rows if length is None else self.rows[:length])

    def __aiter__(self):
        async def _generate():
            for row in self.rows:
                yield deepcopy(row)

        return _generate()


class _Collection:
    def __init__(self, rows: Iterable[Dict[str, Any]] = ()) -> None:
        self.rows = [deepcopy(row) for row in rows]
        #: EVERY filter this collection was asked for, from `find` AND
        #: `find_one`, so a test can tell "the fence was in the query" from
        #: "the fence was applied to the result afterwards".
        self.queries: List[Optional[Dict[str, Any]]] = []
        self.find_raises: Optional[Exception] = None

    async def find_one(self, query=None, projection=None, sort=None, **_k: Any):
        self.queries.append(deepcopy(query))
        if self.find_raises is not None:
            raise self.find_raises
        matches = [row for row in self.rows if _matches(row, query)]
        if sort:
            field, direction = sort[0]
            matches.sort(key=lambda row: _sort_key(row, field), reverse=direction < 0)
        return deepcopy(matches[0]) if matches else None

    def find(self, query=None, *_a: Any, **_k: Any) -> _Cursor:
        self.queries.append(deepcopy(query))
        if self.find_raises is not None:
            raise self.find_raises
        return _Cursor(row for row in self.rows if _matches(row, query))

    async def count_documents(self, query=None, **_k: Any) -> int:
        return len([row for row in self.rows if _matches(row, query)])

    async def insert_one(self, document: Dict[str, Any], **_k: Any):
        inserted = deepcopy(document)
        inserted.setdefault("_id", ObjectId())
        self.rows.append(inserted)
        return SimpleNamespace(inserted_id=inserted["_id"])

    async def insert_many(self, documents: List[Dict[str, Any]], **_k: Any):
        ids = []
        for document in documents:
            result = await self.insert_one(document)
            ids.append(result.inserted_id)
        return SimpleNamespace(inserted_ids=ids)

    async def delete_many(self, query=None, **_k: Any):
        keep = [row for row in self.rows if not _matches(row, query)]
        removed = len(self.rows) - len(keep)
        self.rows = keep
        return SimpleNamespace(deleted_count=removed)

    def _apply(self, row: Dict[str, Any], update: Dict[str, Any]) -> None:
        row.update(deepcopy(update.get("$set", {})))
        for key, amount in (update.get("$inc") or {}).items():
            row[key] = int(row.get(key) or 0) + int(amount)
        for key, value in (update.get("$max") or {}).items():
            row[key] = max(int(row.get(key) or 0), int(value))
        for key, value in (update.get("$push") or {}).items():
            row.setdefault(key, []).append(deepcopy(value))

    async def update_one(self, query, update, upsert: bool = False, **_k: Any):
        for row in self.rows:
            if not _matches(row, query):
                continue
            self._apply(row, update)
            return SimpleNamespace(matched_count=1, modified_count=1)
        if upsert:
            stored = {k: v for k, v in (query or {}).items() if not k.startswith("$")}
            stored.update(deepcopy((update.get("$setOnInsert") or {})))
            self._apply(stored, update)
            stored.setdefault("_id", ObjectId())
            self.rows.append(stored)
            return SimpleNamespace(matched_count=0, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

    async def find_one_and_update(
        self, query, update, upsert: bool = False, return_document: Any = None, **_k: Any
    ):
        for row in self.rows:
            if not _matches(row, query):
                continue
            self._apply(row, update)
            return deepcopy(row)
        if upsert:
            stored = {k: v for k, v in (query or {}).items() if not k.startswith("$")}
            stored.update(deepcopy((update.get("$setOnInsert") or {})))
            self._apply(stored, update)
            stored.setdefault("_id", ObjectId())
            self.rows.append(stored)
            return deepcopy(stored)
        return None

    async def create_index(self, *_a: Any, **_k: Any) -> str:  # pragma: no cover
        return "index"


class _Database:
    def __init__(self, collections: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> None:
        self._collections: Dict[str, _Collection] = {
            name: _Collection(rows) for name, rows in (collections or {}).items()
        }

    def __getitem__(self, name: str) -> _Collection:
        return self._collections.setdefault(name, _Collection())

    def __getattr__(self, name: str) -> _Collection:
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]


# ---------------------------------------------------------------------------
# The contract world - built through the CANONICAL records, never shortcuts
# ---------------------------------------------------------------------------


Sequence_str = Tuple[str, ...]


class _World:
    """Seeds the canonical Contract Master records and the case clause matrix.

    Nothing here writes an "eligible" flag. Eligibility is whatever
    ``ContractScopeResolver`` derives from applicability events, the contract
    document record's projection fields and the canonical ``documents`` row - so
    a test cannot accidentally assert against a fixture's opinion. Approval is
    written only where a human would have written it: on the matrix row.
    """

    def __init__(self) -> None:
        self.documents: List[Dict[str, Any]] = []
        self.contract_documents: List[Dict[str, Any]] = []
        self.applicability: List[Dict[str, Any]] = []
        self.applicability_events: List[Dict[str, Any]] = []
        self.clauses: List[Dict[str, Any]] = []
        self.vectors: List[Dict[str, Any]] = []
        self.matrix_rows: List[Dict[str, Any]] = []
        self.other_matrices: Dict[str, List[Dict[str, Any]]] = {}
        self._seq = 0

    def ready_case(self, *, document_id: str = "doc-eligible") -> None:
        """The non-clause matrices a case-linked draft needs to be generatable.

        ``ArbitrationCaseWorkspaceService.assert_case_ready_for_draft`` refuses
        to generate from a case whose matrices are not ready, so a sealed-version
        proof has to seed them. They are deliberately BORING - one approved
        exhibit, one issue, one non-monetary non-delay claim, a within-limitation
        row and a completed pre-arbitration step - so that nothing here can
        supply the evidence the assertions are about.
        """
        self.other_matrices["arbitration_document_index"] = [
            {
                "_id": "di-1",
                "case_id": CASE_ID,
                "source_type": "document",
                "source_id": document_id,
                "exhibit_id": "C-1",
                "title": "Contract conditions",
                "document_type": "contract",
                "verification_status": "approved",
                "approval_status": "approved",
            }
        ]
        self.other_matrices["arbitration_issue_matrix"] = [
            {
                "_id": "iss-1",
                "case_id": CASE_ID,
                "issue": "Whether the Contractor is entitled to additional payment.",
                "status": "approved",
                "verification_status": "approved",
            }
        ]
        self.other_matrices["arbitration_claim_matrix"] = [
            {
                "_id": "clm-1",
                "case_id": CASE_ID,
                "claim_head": "Entitlement to additional payment for varied works",
                "facts": "The Engineer instructed varied works.",
                "relief": "A declaration of entitlement.",
                "verification_status": "approved",
                "approval_status": "approved",
            }
        ]
        self.other_matrices["arbitration_jurisdiction_matrix"] = [
            {
                "_id": "jur-1",
                "case_id": CASE_ID,
                "check_type": "limitation",
                "limitation_status": "within_limitation",
                "subject": "the claim",
                "verification_status": "approved",
                "approval_status": "approved",
            },
            {
                "_id": "jur-2",
                "case_id": CASE_ID,
                "check_type": "pre_arbitration_step",
                "step": "Engineer's determination",
                "required": True,
                "compliance_status": "complete",
                "verification_status": "approved",
                "approval_status": "approved",
            },
        ]

    def _next(self, prefix: str) -> str:
        self._seq += 1
        return "{}-{}".format(prefix, self._seq)

    def instrument(
        self,
        *,
        document_id: str,
        organization_id: str = "org-A",
        project_id: Optional[str] = "proj-A",
        contract_id: Optional[str] = "MAIN",
        processing_status: str = "completed",
        duplicate_status: Optional[str] = None,
        lifecycle_state: Optional[str] = None,
        classification_revision: int = 3,
        projection_revision: Optional[int] = None,
        projection_status: str = "CURRENT",
        lifecycle: Sequence_str = ("APPLIED",),
        canonical_document: bool = True,
    ) -> str:
        """One contract instrument and (optionally) its applicability.

        ``project_id=None`` means "catalogued in the organisation but applied to
        no project at all" - the zero-applicability case.
        ``canonical_document=False`` means the clause's ``document_id`` resolves
        to nothing - the ORPHAN case.
        """
        if canonical_document:
            self.documents.append(
                {
                    "_id": document_id,
                    "organization_id": organization_id,
                    "project_id": project_id,
                    "processing_status": processing_status,
                    "duplicate_status": duplicate_status,
                    "lifecycle_state": lifecycle_state,
                }
            )
        contract_document_id = self._next("cdoc")
        self.contract_documents.append(
            {
                "_id": contract_document_id,
                "organization_id": organization_id,
                "document_id": document_id,
                "document_version_id": "{}-v1".format(document_id),
                "contract_document_type": "general_conditions",
                "classification_revision": classification_revision,
                "projection_revision": (
                    classification_revision
                    if projection_revision is None
                    else projection_revision
                ),
                "projection_status": projection_status,
            }
        )
        if project_id and contract_id:
            applicability_id = self._next("appl")
            self.applicability.append(
                {
                    "_id": applicability_id,
                    "organization_id": organization_id,
                    "project_id": project_id,
                    "contract_id": contract_id,
                    "contract_document_id": contract_document_id,
                }
            )
            for index, kind in enumerate(lifecycle):
                self.applicability_events.append(
                    {
                        "_id": self._next("evt"),
                        "event_id": self._next("event"),
                        "applicability_id": applicability_id,
                        "kind": kind,
                        "effective_at": "2026-0{}-01".format(index + 1),
                    }
                )
        return contract_document_id

    def clause_chunk(
        self,
        *,
        document_id: str,
        text: str,
        organization_id: str = "org-A",
        project_id: str = "proj-A",
        clause_number: str = "8.4",
        vector_id: Optional[str] = None,
    ) -> str:
        """A ``document_vectors`` chunk - what ``clause_source_id`` names.

        Several tests deliberately stamp a chunk with the drafting workspace
        while its canonical instrument says otherwise. That is the
        lying-provenance case: the row records where it was FILED, never that
        the instrument governs anything.
        """
        row_id = vector_id or self._next("vec")
        self.vectors.append(
            {
                "_id": row_id,
                "organization_id": organization_id,
                "project_id": project_id,
                "document_id": document_id,
                "clause_number": clause_number,
                "title": "Extension of time {}".format(row_id),
                "text": "The Contractor shall be entitled to an extension of time. {}".format(text),
                "page_numbers": [12],
            }
        )
        return row_id

    def approved_clause_row(
        self,
        *,
        clause_source_id: Optional[str],
        excerpt: str,
        row_id: Optional[str] = None,
        case_id: str = CASE_ID,
        clause_number: str = "8.4",
        topic: str = "Extension of time",
        obligation: Optional[str] = None,
        approval_status: str = "approved",
        verification_status: str = "approved",
        **extra: Any,
    ) -> str:
        """One case clause-matrix row, in the shape the writer produces.

        ``approval_status`` / ``verification_status`` are the HUMAN curation
        verdict. Nothing else in this fixture claims authority - and where a
        test wants a row that LIES about its provenance it passes
        ``project_id`` / ``contract_id`` through ``extra`` explicitly, so the
        lie is visible in the test rather than baked into the helper.
        """
        identifier = row_id or self._next("cm")
        row = {
            "_id": identifier,
            "case_id": case_id,
            "clause_number": clause_number,
            "topic": topic,
            "clause_source_id": clause_source_id,
            "clause_text_excerpt": excerpt,
            "obligation_or_right": obligation,
            "approval_status": approval_status,
            "verification_status": verification_status,
        }
        row.update(extra)
        self.matrix_rows.append(row)
        return identifier

    def database(
        self,
        drafts: Optional[List[Dict[str, Any]]] = None,
        selected: Optional[List[Dict[str, Any]]] = None,
        cases: Optional[List[Dict[str, Any]]] = None,
    ) -> _Database:
        return _Database(
            {
                **{name: list(rows) for name, rows in self.other_matrices.items()},
                "arbitration_drafts": list(drafts or []),
                "arbitration_cases": list(cases or [_case()]),
                SELECTED_REFERENCES: list(selected or []),
                "documents": self.documents,
                CONTRACT_DOCUMENTS: self.contract_documents,
                APPLICABILITY: self.applicability,
                APPLICABILITY_EVENTS: self.applicability_events,
                CONTRACT_CLAUSES: self.clauses,
                DOCUMENT_VECTORS: self.vectors,
                CLAUSE_MATRIX: self.matrix_rows,
            }
        )


class _DisabledEvidenceGraph:
    """The evidence graph is a different seam (pinned by test_graph_consumer_authority)."""

    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    async def downstream_links(self, *_a: Any, **_k: Any) -> List[Dict[str, Any]]:
        return []


# ---------------------------------------------------------------------------
# Actors, cases, drafts and selections
# ---------------------------------------------------------------------------


def _project_actor() -> SimpleNamespace:
    """Organisation A, project A only. The narrowest realistic drafting actor."""
    return SimpleNamespace(
        id="user-A",
        email="user-a@example.com",
        roles=["projectuser"],
        organization_id="org-A",
        organizations=["org-A"],
        projects=["proj-A"],
    )


def _sibling_project_actor() -> SimpleNamespace:
    """Entitled to project B only - and drafting, by request, in project A."""
    return SimpleNamespace(
        id="user-B",
        email="user-b@example.com",
        roles=["projectuser"],
        organization_id="org-A",
        organizations=["org-A"],
        projects=["proj-B"],
    )


def _rootless_worker(draft: Dict[str, Any]) -> SimpleNamespace:
    """R17's shape: a principal FABRICATED from the resource being read."""
    return SimpleNamespace(
        id="worker",
        roles=[],
        organization_id=draft.get("organization_id"),
        project_id=draft.get("project_id"),
    )


def _case(case_id: str = CASE_ID) -> Dict[str, Any]:
    return {
        "_id": case_id,
        "organization_id": "org-A",
        "project_id": "proj-A",
        "title": "Extension of time dispute",
        "party_perspective": "claimant",
    }


def _draft(
    *,
    draft_id: str = "draft-1",
    organization_id: str = "org-A",
    project_id: str = "proj-A",
    contract_id: Optional[str] = None,
    case_id: Optional[str] = CASE_ID,
) -> Dict[str, Any]:
    return {
        "_id": draft_id,
        "case_id": case_id,
        "organization_id": organization_id,
        "project_id": project_id,
        "contract_id": contract_id,
        "draft_type": "statement_of_claim",
        "party_role": "claimant",
        "dispute_type": "other",
        "title": "Claim for extension of time",
        "case_details": {},
        "relief_sought": "An extension of time and associated costs.",
        "manual_facts": "",
        "arbitration_clause": "Clause 20 arbitration.",
        "include_register_sources": False,
        "excluded_register_ids": [],
        "status": "draft",
    }


def _matrix_selection(
    matrix_row_id: str,
    *,
    source_id: str = "clause-selected",
    reference_id: str = "ref-1",
    draft_id: str = "draft-1",
) -> Dict[str, Any]:
    """What ``prepare_draft_from_case`` persists: a selection naming a matrix row.

    ``label`` and ``snippet`` are deliberately client prose - the hydrator
    discards them and rebuilds from the server record, so no assertion here can
    pass because a marker was absent from something the client wrote.
    """
    return {
        "_id": reference_id,
        "draft_id": draft_id,
        "source_type": "clause",
        "source_id": source_id,
        "label": "Clause chosen from the approved case matrix",
        "citation": "8.4",
        "allowed_use": "clause",
        "metadata": {"matrix_row_id": matrix_row_id},
    }


# ---------------------------------------------------------------------------
# Drivers - the REAL production entry points, never a re-implementation
# ---------------------------------------------------------------------------


class _Outcome:
    """Everything one context assembly made observable.

    A REJECTION is an outcome, not an error the test swallows: the rejection
    receipt is inspected for leaked evidence exactly like a ledger is.
    """

    def __init__(
        self,
        db: _Database,
        context: Optional[Dict[str, Any]] = None,
        error: Optional[HTTPException] = None,
    ) -> None:
        self.db = db
        self.context = context
        self.error = error

    @property
    def rejected(self) -> bool:
        return self.error is not None

    @property
    def rows(self) -> List[Dict[str, Any]]:
        return list((self.context or {}).get("source_ledger") or [])

    def clause_rows(self) -> List[Dict[str, Any]]:
        return [
            row
            for row in self.rows
            if (row.get("metadata") or {}).get("matrix") == "clause-matrix"
            or row.get("source_origin") == "case_clause_matrix"
        ]

    def text(self) -> str:
        return repr(self.context) + repr(getattr(self.error, "detail", ""))

    def provenance(self) -> set:
        ids: set = set()
        for row in self.rows:
            ids.add(str(row.get("source_id")))
            if row.get("matrix_row_id"):
                ids.add(str(row.get("matrix_row_id")))
            metadata = row.get("metadata") or {}
            for key in ("document_id", "matrix_row_id", "source_document_id"):
                if metadata.get(key):
                    ids.add(str(metadata[key]))
        return ids

    def warnings(self) -> List[str]:
        return list((self.context or {}).get("context_warnings") or [])


async def _assemble(
    monkeypatch: pytest.MonkeyPatch,
    *,
    world: _World,
    draft: Optional[Dict[str, Any]] = None,
    references: Optional[List[Dict[str, Any]]] = None,
    actor: Any = None,
    mutate_db: Any = None,
) -> _Outcome:
    """Drive the REAL ``ArbitrationContextBuilder.build``, as ``_context`` does."""
    draft = draft or _draft()
    db = world.database([draft])
    if mutate_db is not None:
        mutate_db(db)
    monkeypatch.setattr(
        arbitration_context, "EvidenceGraphService", _DisabledEvidenceGraph
    )
    builder = ArbitrationContextBuilder(db)
    try:
        context = await builder.build(
            draft, deepcopy(references or []), [], [], actor or _project_actor()
        )
    except HTTPException as exc:
        return _Outcome(db, error=exc)
    return _Outcome(db, context=context)


class _Run:
    """The persisted arbitration artefacts one generation writes."""

    def __init__(
        self,
        db: _Database,
        version: Dict[str, Any],
        run: Dict[str, Any],
        error: Optional[HTTPException] = None,
    ) -> None:
        self.db = db
        self.version = version
        self.run = run
        self.error = error

    @property
    def rejected(self) -> bool:
        return self.error is not None

    def sealed_surfaces(self) -> Dict[str, str]:
        """What this generation SEALED - the row a tribunal eventually reads."""
        return {
            "persisted arbitration_draft_versions.source_ledger": repr(
                self.version.get("source_ledger")
            ),
            "persisted arbitration_draft_versions.full_markdown": str(
                self.version.get("full_markdown")
            ),
            "persisted arbitration_generation_runs": repr(self.run),
        }

    def provenance(self) -> set:
        ids = {str(item) for item in (self.run.get("source_ids") or [])}
        for row in self.version.get("source_ledger") or []:
            ids.add(str(row.get("source_id")))
            if row.get("matrix_row_id"):
                ids.add(str(row.get("matrix_row_id")))
        return ids


async def _seed_readiness_approval(db: _Database, draft: Dict[str, Any]) -> None:
    """Record the revision-bound readiness approval a case-linked draft requires.

    Generation from a case refuses without a COMMITTED receipt whose hashes match
    the case's current matrix and evidence state. That gate is a workflow
    control, not an evidence-authority control, and it is not what R24 is about -
    so the receipt is minted from the service's own artefact state rather than
    stubbed away, and every authority assertion still runs against the real
    ledger the real generation builds.
    """
    from datetime import timezone

    workspace = ArbitrationCaseWorkspaceService(db)
    case = await workspace.get_case(str(draft.get("case_id")))
    draft_type = str(draft.get("draft_type") or "statement_of_claim")
    artifact = await workspace._readiness_artifact_state(case, draft_type)
    await db["arbitration_workflow_approvals"].insert_one(
        {
            "_id": "receipt-readiness",
            "gate": "readiness",
            "case_id": str(case.get("_id")),
            "draft_id": None,
            "draft_type": draft_type,
            "receipt_status": "committed",
            "matrix_revision_set_id": artifact["matrix_revision_set_id"],
            "matrix_revision_hash": artifact["matrix_revision_hash"],
            "evidence_snapshot_hash": artifact["evidence_snapshot_hash"],
            "artifact_hash": artifact["artifact_hash"],
            "approver_id": "approver-1",
            "approver_role": "legal",
            "approved_at": datetime(2026, 8, 31, tzinfo=timezone.utc),
        }
    )
    # The pleading-plan gate is the second workflow control on a case-linked
    # full draft. Same reasoning: a governance gate, not an evidence-authority
    # one, so it is satisfied rather than removed.
    await db["arbitration_workflow_approvals"].insert_one(
        {
            "_id": "receipt-plan",
            "gate": "plan",
            "run_id": "plan-run-1",
            "artifact_hash": "plan-hash-1",
            "decision": "approved",
            "receipt_status": "committed",
            "approved_at": datetime(2026, 8, 31, tzinfo=timezone.utc),
        }
    )


#: The approved plan a case-linked full draft is started from.
PLEADING_PLAN = {
    "run_id": "plan-run-1",
    "plan_hash": "plan-hash-1",
    "status": "approved",
    "approval_receipt_id": "receipt-plan",
}


async def _generate(
    monkeypatch: pytest.MonkeyPatch,
    *,
    world: _World,
    draft: Optional[Dict[str, Any]] = None,
    selected: Optional[List[Dict[str, Any]]] = None,
    actor: Any = None,
    before_generate: Any = None,
) -> _Run:
    """Drive the REAL service generation, through to the sealed version row."""
    from rbac_backend.models.arbitration_drafting import ArbitrationGenerateRequest

    draft = draft or _draft()
    db = world.database([draft], selected=selected)
    monkeypatch.setattr(
        arbitration_context, "EvidenceGraphService", _DisabledEvidenceGraph
    )
    service = ArbitrationDraftingService(db)
    await _seed_readiness_approval(db, draft)
    if before_generate is not None:
        await before_generate(db, service)
    error: Optional[HTTPException] = None
    try:
        await service.generate(
            str(draft["_id"]),
            ArbitrationGenerateRequest(),
            actor or _project_actor(),
            pleading_plan=dict(PLEADING_PLAN),
        )
    except HTTPException as exc:
        error = exc
    version = await db["arbitration_draft_versions"].find_one(
        {"draft_id": str(draft["_id"])}, sort=[("version", -1)]
    )
    # The run belonging to THAT version, not the first row in the collection.
    # A draft that has been generated twice keeps its earlier run and its
    # earlier sealed version verbatim - immutable history, the carried owner
    # decision from G-A5...G-A12 - so a revalidation proof that read the oldest
    # run would be asserting about the wrong generation.
    run = None
    if (version or {}).get("generation_run_id"):
        run = await db["arbitration_generation_runs"].find_one(
            {"_id": version["generation_run_id"]}
        )
    if run is None:
        run = await db["arbitration_generation_runs"].find_one(
            {"draft_id": str(draft["_id"])}, sort=[("started_at", -1)]
        )
    return _Run(db, version or {}, run or {}, error=error)


def _assert_absent(outcome: Any, marker: str, why: str, *, surfaces: Any = None) -> None:
    if surfaces is None:
        surfaces = (
            outcome.sealed_surfaces()
            if isinstance(outcome, _Run)
            else {"context": outcome.text()}
        )
    for name, blob in surfaces.items():
        assert marker not in blob, (
            "{}: the marker {!r} reached {}. Human matrix approval records that "
            "a source was judged RELEVANT - never that the instrument legally "
            "governs this project. Only the canonical Contract Master universe "
            "may admit a clause to a pleading.".format(why, marker, name)
        )


def _assert_present(outcome: Any, marker: str, why: str) -> None:
    if isinstance(outcome, _Run):
        blob = "".join(outcome.sealed_surfaces().values())
    else:
        blob = outcome.text()
    assert marker in blob, (
        "{}: the authorised marker {!r} reached NOTHING. A containment that "
        "makes the approved clause matrix useless is an outage, not a "
        "boundary.".format(why, marker)
    )


def _assert_source_absent(outcome: Any, identifier: str, why: str) -> None:
    """Provenance, asserted separately from text.

    Blanking the excerpt while keeping the row's ``source_id`` or
    ``matrix_row_id`` still records a foreign instrument as a contributor to a
    pleading, in a row sealed by ``immutable_version_hash`` and rendered by the
    exporter.
    """
    assert identifier not in outcome.provenance(), (
        "{}: the ineligible identifier {!r} survived in the arbitration source "
        "ledger's provenance.".format(why, identifier)
    )


def _eligible_world() -> Tuple[_World, str, str]:
    """One applicable, consumable, projection-current instrument and its approved row."""
    world = _World()
    world.instrument(document_id="doc-eligible", project_id="proj-A", contract_id="MAIN")
    clause_id = world.clause_chunk(document_id="doc-eligible", text=AUTHORISED_MARKER)
    row_id = world.approved_clause_row(
        clause_source_id=clause_id, excerpt="Clause 8.4 {}".format(AUTHORISED_MARKER)
    )
    world.ready_case()
    return world, clause_id, row_id


async def _assert_lawful_row_still_contributes(
    monkeypatch: pytest.MonkeyPatch, world: _World, why: str
) -> None:
    """Non-vacuity: the SAME world still admits its lawful approved row.

    Every containment case seeds an eligible companion, so an exclusion can
    never pass because the whole path was broken.
    """
    outcome = await _assemble(monkeypatch, world=world)
    assert not outcome.rejected, (
        "{}: the LAWFUL companion row was rejected too ({!r}). The exclusion "
        "under test therefore proves nothing about authority.".format(
            why, getattr(outcome.error, "detail", None)
        )
    )
    _assert_present(outcome, AUTHORISED_MARKER, why)


# ---------------------------------------------------------------------------
# RED 1 - approved, but the instrument belongs to a SIBLING PROJECT
# ---------------------------------------------------------------------------


def _sibling_project_world() -> Tuple[_World, str, str]:
    world, _lawful_clause, _lawful_row = _eligible_world()
    world.instrument(document_id="doc-project-b", project_id="proj-B", contract_id="MAIN")
    clause_id = world.clause_chunk(
        document_id="doc-project-b",
        project_id="proj-A",  # filed into the drafting workspace; provenance, not authority
        text=SIBLING_PROJECT_MARKER,
    )
    row_id = world.approved_clause_row(
        clause_source_id=clause_id,
        excerpt="Clause 8.4 {}".format(SIBLING_PROJECT_MARKER),
    )
    return world, clause_id, row_id


@pytest.mark.asyncio
async def test_an_approved_sibling_project_row_never_reaches_the_automatic_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, clause_id, row_id = _sibling_project_world()

    outcome = await _assemble(monkeypatch, world=world)

    why = "an approved matrix row naming a sibling project's instrument"
    _assert_absent(outcome, SIBLING_PROJECT_MARKER, why)
    _assert_source_absent(outcome, clause_id, why)
    _assert_source_absent(outcome, row_id, why)
    await _assert_lawful_row_still_contributes(monkeypatch, world, why)


@pytest.mark.asyncio
async def test_an_approved_sibling_project_row_never_rehydrates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, clause_id, row_id = _sibling_project_world()

    outcome = await _assemble(
        monkeypatch,
        world=world,
        references=[_matrix_selection(row_id, source_id=clause_id)],
    )

    why = "an approved matrix SELECTION naming a sibling project's instrument"
    _assert_absent(outcome, SIBLING_PROJECT_MARKER, why)
    for row in outcome.rows:
        assert str(row.get("source_id")) != clause_id, (
            "{}: it survived in the rebuilt source ledger".format(why)
        )


@pytest.mark.asyncio
async def test_an_approved_sibling_project_row_never_reaches_the_sealed_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The load-bearing persistence proof for the automatic door."""
    world, clause_id, row_id = _sibling_project_world()

    run = await _generate(monkeypatch, world=world)

    why = "an approved sibling-project matrix row, at generation"
    _assert_absent(run, SIBLING_PROJECT_MARKER, why)
    _assert_source_absent(run, clause_id, why)
    _assert_source_absent(run, row_id, why)
    _assert_present(run, AUTHORISED_MARKER, why)


# ---------------------------------------------------------------------------
# RED 2 - approved, catalogued, publication-safe, applied to NOTHING
# ---------------------------------------------------------------------------


def _no_applicability_world() -> Tuple[_World, str, str]:
    world, _c, _r = _eligible_world()
    world.instrument(
        document_id="doc-catalogue-only", project_id=None, contract_id=None
    )
    clause_id = world.clause_chunk(
        document_id="doc-catalogue-only", text=NO_APPLICABILITY_MARKER
    )
    row_id = world.approved_clause_row(
        clause_source_id=clause_id,
        excerpt="Clause 8.4 {}".format(NO_APPLICABILITY_MARKER),
    )
    return world, clause_id, row_id


@pytest.mark.asyncio
async def test_an_approved_row_with_zero_applicability_has_no_influence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The load-bearing R24 case: catalogued is not applicable."""
    world, clause_id, row_id = _no_applicability_world()

    outcome = await _assemble(monkeypatch, world=world)

    why = "an approved matrix row whose instrument is catalogued but applied to no project"
    _assert_absent(outcome, NO_APPLICABILITY_MARKER, why)
    _assert_source_absent(outcome, clause_id, why)
    await _assert_lawful_row_still_contributes(monkeypatch, world, why)


@pytest.mark.asyncio
async def test_an_approved_row_with_zero_applicability_never_rehydrates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, clause_id, row_id = _no_applicability_world()

    outcome = await _assemble(
        monkeypatch,
        world=world,
        references=[_matrix_selection(row_id, source_id=clause_id)],
    )

    _assert_absent(
        outcome,
        NO_APPLICABILITY_MARKER,
        "an approved matrix SELECTION whose instrument applies to no project",
    )


@pytest.mark.asyncio
async def test_an_approved_row_with_zero_applicability_never_seals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, clause_id, row_id = _no_applicability_world()

    run = await _generate(monkeypatch, world=world)

    why = "an approved catalogue-only matrix row, at generation"
    _assert_absent(run, NO_APPLICABILITY_MARKER, why)
    _assert_source_absent(run, clause_id, why)
    _assert_present(run, AUTHORISED_MARKER, why)


# ---------------------------------------------------------------------------
# RED 3 / RED 4 - approval survives the instrument's lifecycle; authority does not
# ---------------------------------------------------------------------------


def _lifecycle_world(kind: str, marker: str) -> Tuple[_World, str, str]:
    world, _c, _r = _eligible_world()
    world.instrument(
        document_id="doc-{}".format(kind.lower()),
        project_id="proj-A",
        contract_id="MAIN",
        lifecycle=("APPLIED", kind),
    )
    clause_id = world.clause_chunk(
        document_id="doc-{}".format(kind.lower()), text=marker
    )
    row_id = world.approved_clause_row(
        clause_source_id=clause_id, excerpt="Clause 8.4 {}".format(marker)
    )
    return world, clause_id, row_id


@pytest.mark.asyncio
async def test_an_approved_row_for_a_withdrawn_instrument_is_excluded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, clause_id, _row_id = _lifecycle_world("WITHDRAWN", WITHDRAWN_MARKER)

    outcome = await _assemble(monkeypatch, world=world)

    why = "an approved matrix row whose instrument has been withdrawn"
    _assert_absent(outcome, WITHDRAWN_MARKER, why)
    _assert_source_absent(outcome, clause_id, why)
    await _assert_lawful_row_still_contributes(monkeypatch, world, why)


@pytest.mark.asyncio
async def test_an_approved_row_for_a_withdrawn_instrument_never_rehydrates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, clause_id, row_id = _lifecycle_world("WITHDRAWN", WITHDRAWN_MARKER)

    outcome = await _assemble(
        monkeypatch,
        world=world,
        references=[_matrix_selection(row_id, source_id=clause_id)],
    )

    _assert_absent(
        outcome,
        WITHDRAWN_MARKER,
        "an approved matrix SELECTION whose instrument has been withdrawn",
    )


@pytest.mark.asyncio
async def test_an_approved_row_for_a_superseded_instrument_is_excluded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, clause_id, _row_id = _lifecycle_world("SUPERSEDED", SUPERSEDED_MARKER)

    outcome = await _assemble(monkeypatch, world=world)

    why = "an approved matrix row whose instrument has been superseded"
    _assert_absent(outcome, SUPERSEDED_MARKER, why)
    _assert_source_absent(outcome, clause_id, why)
    await _assert_lawful_row_still_contributes(monkeypatch, world, why)


# ---------------------------------------------------------------------------
# RED 5 / RED 6 - publication authority is not overridden by approval
# ---------------------------------------------------------------------------


def _publication_world(marker: str, **document: Any) -> Tuple[_World, str, str]:
    world, _c, _r = _eligible_world()
    world.instrument(
        document_id="doc-blocked", project_id="proj-A", contract_id="MAIN", **document
    )
    clause_id = world.clause_chunk(document_id="doc-blocked", text=marker)
    row_id = world.approved_clause_row(
        clause_source_id=clause_id, excerpt="Clause 8.4 {}".format(marker)
    )
    return world, clause_id, row_id


@pytest.mark.asyncio
async def test_approval_does_not_override_human_review_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """May already hold through ``consumable_derived_text``; pinned permanently.

    The excerpt half was already correct before R24. The IDENTITY half was not:
    a blocked instrument kept its ledger row, citation and clause number.
    """
    world, clause_id, row_id = _publication_world(
        HUMAN_REVIEW_MARKER, processing_status="human_review_required"
    )

    outcome = await _assemble(monkeypatch, world=world)

    why = "an approved matrix row whose parent document is under human review"
    _assert_absent(outcome, HUMAN_REVIEW_MARKER, why)
    _assert_source_absent(outcome, clause_id, why)
    _assert_source_absent(outcome, row_id, why)
    await _assert_lawful_row_still_contributes(monkeypatch, world, why)


@pytest.mark.asyncio
async def test_approval_does_not_override_human_review_on_rehydration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, clause_id, row_id = _publication_world(
        HUMAN_REVIEW_MARKER, processing_status="human_review_required"
    )

    outcome = await _assemble(
        monkeypatch,
        world=world,
        references=[_matrix_selection(row_id, source_id=clause_id)],
    )

    _assert_absent(
        outcome,
        HUMAN_REVIEW_MARKER,
        "an approved matrix SELECTION whose parent document is under human review",
    )


@pytest.mark.asyncio
async def test_approval_does_not_override_a_confirmed_duplicate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, clause_id, row_id = _publication_world(
        DUPLICATE_MARKER, duplicate_status="duplicate"
    )

    outcome = await _assemble(monkeypatch, world=world)

    why = "an approved matrix row whose parent document is a confirmed duplicate"
    _assert_absent(outcome, DUPLICATE_MARKER, why)
    _assert_source_absent(outcome, clause_id, why)
    await _assert_lawful_row_still_contributes(monkeypatch, world, why)


@pytest.mark.asyncio
async def test_approval_does_not_override_a_deleted_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, clause_id, row_id = _publication_world(
        DELETED_MARKER, lifecycle_state="deleted"
    )

    outcome = await _assemble(monkeypatch, world=world)

    why = "an approved matrix row whose parent document was deleted"
    _assert_absent(outcome, DELETED_MARKER, why)
    _assert_source_absent(outcome, clause_id, why)
    await _assert_lawful_row_still_contributes(monkeypatch, world, why)


# ---------------------------------------------------------------------------
# RED 7 - the orphan: matrix existence cannot self-authorise
# ---------------------------------------------------------------------------


def _orphan_world() -> Tuple[_World, str, str]:
    world, _c, _r = _eligible_world()
    world.instrument(
        document_id="doc-orphan",
        project_id="proj-A",
        contract_id="MAIN",
        canonical_document=False,
    )
    clause_id = world.clause_chunk(document_id="doc-orphan", text=ORPHAN_MARKER)
    row_id = world.approved_clause_row(
        clause_source_id=clause_id, excerpt="Clause 8.4 {}".format(ORPHAN_MARKER)
    )
    return world, clause_id, row_id


@pytest.mark.asyncio
async def test_an_approved_orphan_row_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, clause_id, row_id = _orphan_world()

    outcome = await _assemble(monkeypatch, world=world)

    why = "an approved matrix row whose clause resolves to no canonical document"
    _assert_absent(outcome, ORPHAN_MARKER, why)
    _assert_source_absent(outcome, clause_id, why)
    await _assert_lawful_row_still_contributes(monkeypatch, world, why)


@pytest.mark.asyncio
async def test_an_approved_row_naming_an_unresolvable_clause_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The clause id names nothing at all - no chunk, no clause, no document."""
    world, _c, _r = _eligible_world()
    row_id = world.approved_clause_row(
        clause_source_id="clause-that-does-not-exist",
        excerpt="Clause 8.4 {}".format(ORPHAN_MARKER),
    )

    outcome = await _assemble(monkeypatch, world=world)

    why = "an approved matrix row naming a clause id that resolves to nothing"
    _assert_absent(outcome, ORPHAN_MARKER, why)
    _assert_source_absent(outcome, row_id, why)
    _assert_present(outcome, AUTHORISED_MARKER, why)


# ---------------------------------------------------------------------------
# RED 8 - the lying row: matrix provenance is not authority
# ---------------------------------------------------------------------------


def _lying_row_world() -> Tuple[_World, str, str]:
    world, _c, _r = _eligible_world()
    world.instrument(document_id="doc-lying", project_id="proj-B", contract_id="OTHER")
    clause_id = world.clause_chunk(
        document_id="doc-lying", project_id="proj-A", text=LYING_ROW_MARKER
    )
    row_id = world.approved_clause_row(
        clause_source_id=clause_id,
        excerpt="Clause 8.4 {}".format(LYING_ROW_MARKER),
        # Everything a row can assert about itself, asserted:
        organization_id="org-A",
        project_id="proj-A",
        contract_id="MAIN",
        human_approval_status="approved",
        readiness_status="approved",
        is_authorised_for_ai=True,
    )
    return world, clause_id, row_id


@pytest.mark.asyncio
async def test_a_lying_matrix_row_cannot_self_authorise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, clause_id, row_id = _lying_row_world()

    outcome = await _assemble(monkeypatch, world=world)

    why = (
        "an approved matrix row declaring the drafting project and contract while "
        "the canonical applicability record disagrees"
    )
    _assert_absent(outcome, LYING_ROW_MARKER, why)
    _assert_source_absent(outcome, clause_id, why)
    _assert_source_absent(outcome, row_id, why)
    await _assert_lawful_row_still_contributes(monkeypatch, world, why)


@pytest.mark.asyncio
async def test_a_lying_matrix_row_cannot_self_authorise_on_rehydration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, clause_id, row_id = _lying_row_world()

    outcome = await _assemble(
        monkeypatch,
        world=world,
        references=[_matrix_selection(row_id, source_id=clause_id)],
    )

    _assert_absent(
        outcome,
        LYING_ROW_MARKER,
        "an approved matrix SELECTION whose row lies about its project and contract",
    )


# ---------------------------------------------------------------------------
# RED 9 / RED 10 - projection currency, the two axes kept apart
# ---------------------------------------------------------------------------


def _projection_world(marker: str, **projection: Any) -> Tuple[_World, str, str]:
    world, _c, _r = _eligible_world()
    world.instrument(
        document_id="doc-projection",
        project_id="proj-A",
        contract_id="MAIN",
        **projection,
    )
    clause_id = world.clause_chunk(document_id="doc-projection", text=marker)
    row_id = world.approved_clause_row(
        clause_source_id=clause_id, excerpt="Clause 8.4 {}".format(marker)
    )
    return world, clause_id, row_id


@pytest.mark.asyncio
async def test_an_approved_row_on_a_stale_projection_is_excluded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """classification_revision N+1, projection_revision N, status CURRENT."""
    world, clause_id, _row = _projection_world(
        STALE_PROJECTION_MARKER,
        classification_revision=4,
        projection_revision=3,
        projection_status="CURRENT",
    )

    outcome = await _assemble(monkeypatch, world=world)

    why = "an approved matrix row whose clause projection is a revision behind"
    _assert_absent(outcome, STALE_PROJECTION_MARKER, why)
    _assert_source_absent(outcome, clause_id, why)
    await _assert_lawful_row_still_contributes(monkeypatch, world, why)


@pytest.mark.asyncio
async def test_an_approved_row_on_a_pending_projection_is_excluded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, clause_id, _row = _projection_world(
        PENDING_PROJECTION_MARKER, projection_status="PENDING"
    )

    outcome = await _assemble(monkeypatch, world=world)

    why = "an approved matrix row whose clause projection is PENDING"
    _assert_absent(outcome, PENDING_PROJECTION_MARKER, why)
    _assert_source_absent(outcome, clause_id, why)
    await _assert_lawful_row_still_contributes(monkeypatch, world, why)


@pytest.mark.asyncio
async def test_an_approved_row_on_a_failed_projection_is_excluded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, clause_id, _row = _projection_world(
        FAILED_PROJECTION_MARKER, projection_status="FAILED"
    )

    outcome = await _assemble(monkeypatch, world=world)

    why = "an approved matrix row whose clause projection FAILED"
    _assert_absent(outcome, FAILED_PROJECTION_MARKER, why)
    _assert_source_absent(outcome, clause_id, why)
    await _assert_lawful_row_still_contributes(monkeypatch, world, why)


# ---------------------------------------------------------------------------
# RED 11 - Model B: an OPERATIONAL failure is not a verdict about the content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_operationally_failed_instrument_keeps_its_last_known_good_clause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not over-filter. ``failed`` means extraction broke, not that the
    instrument stopped governing."""
    world = _World()
    world.instrument(
        document_id="doc-operational",
        project_id="proj-A",
        contract_id="MAIN",
        processing_status="failed",
    )
    clause_id = world.clause_chunk(
        document_id="doc-operational", text=OPERATIONAL_FAILED_MARKER
    )
    world.approved_clause_row(
        clause_source_id=clause_id,
        excerpt="Clause 8.4 {}".format(OPERATIONAL_FAILED_MARKER),
    )

    outcome = await _assemble(monkeypatch, world=world)

    _assert_present(
        outcome,
        OPERATIONAL_FAILED_MARKER,
        "an approved matrix row on an operationally failed but applicable instrument",
    )


# ---------------------------------------------------------------------------
# RED 16 / RED 17 - approval is necessary; canonical eligibility is necessary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_fully_eligible_approved_row_still_grounds_the_pleading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, clause_id, row_id = _eligible_world()

    outcome = await _assemble(monkeypatch, world=world)

    why = "a fully eligible approved matrix row"
    _assert_present(outcome, AUTHORISED_MARKER, why)
    assert outcome.clause_rows(), (
        "{}: the clause-matrix door produced no ledger row at all".format(why)
    )


@pytest.mark.asyncio
async def test_a_fully_eligible_approved_row_still_rehydrates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, clause_id, row_id = _eligible_world()

    outcome = await _assemble(
        monkeypatch,
        world=world,
        references=[_matrix_selection(row_id, source_id=clause_id)],
    )

    assert not outcome.rejected, (
        "a fully eligible approved matrix SELECTION was rejected: {!r}".format(
            getattr(outcome.error, "detail", None)
        )
    )
    _assert_present(outcome, AUTHORISED_MARKER, "a fully eligible approved selection")


@pytest.mark.asyncio
async def test_a_fully_eligible_approved_row_reaches_the_sealed_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, clause_id, row_id = _eligible_world()

    run = await _generate(monkeypatch, world=world)

    _assert_present(run, AUTHORISED_MARKER, "a fully eligible approved matrix row")


@pytest.mark.asyncio
async def test_an_unapproved_row_stays_out_even_when_canonically_eligible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Canonical eligibility is NECESSARY, not sufficient.

    Approval is a native business requirement of the case matrix, and R24 must
    not quietly convert the fence into a substitute for it.
    """
    world, _c, _r = _eligible_world()
    clause_id = world.clause_chunk(document_id="doc-eligible", text=UNAPPROVED_MARKER)
    world.approved_clause_row(
        clause_source_id=clause_id,
        excerpt="Clause 8.4 {}".format(UNAPPROVED_MARKER),
        approval_status="needs_review",
        verification_status="needs_review",
    )

    outcome = await _assemble(monkeypatch, world=world)

    _assert_absent(
        outcome,
        UNAPPROVED_MARKER,
        "an UNAPPROVED matrix row whose instrument happens to be eligible",
    )
    _assert_present(outcome, AUTHORISED_MARKER, "the approved companion row")


@pytest.mark.asyncio
async def test_an_unapproved_matrix_selection_is_still_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-existing 409 door, pinned so R24 cannot dissolve it."""
    world, _c, _r = _eligible_world()
    clause_id = world.clause_chunk(document_id="doc-eligible", text=UNAPPROVED_MARKER)
    row_id = world.approved_clause_row(
        clause_source_id=clause_id,
        excerpt="Clause 8.4 {}".format(UNAPPROVED_MARKER),
        approval_status="needs_review",
        verification_status="needs_review",
    )

    outcome = await _assemble(
        monkeypatch,
        world=world,
        references=[_matrix_selection(row_id, source_id=clause_id)],
    )

    assert outcome.rejected, "an unapproved matrix selection was accepted"
    _assert_absent(
        outcome, UNAPPROVED_MARKER, "an unapproved matrix selection"
    )


# ---------------------------------------------------------------------------
# RED 14 / RED 15 - approval is not a durable authority cache
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_regeneration_revalidates_an_approval_made_before_the_authority_changed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Lawful at approval time; withdrawn before the next sealed version.

    The row's approval flag never changes - a human approved it once and nobody
    revisited it. That is exactly the state R24 exists for.
    """
    world, clause_id, row_id = _eligible_world()

    async def _seal_then_withdraw(db: _Database, service: Any) -> None:
        from rbac_backend.models.arbitration_drafting import ArbitrationGenerateRequest

        await service.generate(
            "draft-1",
            ArbitrationGenerateRequest(),
            _project_actor(),
            pleading_plan=dict(PLEADING_PLAN),
        )
        first = await db["arbitration_draft_versions"].find_one({"draft_id": "draft-1"})
        assert AUTHORISED_MARKER in repr(first), (
            "the row under test was never lawful in the first place, so the "
            "re-run proves nothing about revalidation: {!r}".format(first)
        )
        applicability = db[APPLICABILITY].rows[0]
        await db[APPLICABILITY_EVENTS].insert_one(
            {
                "_id": "evt-withdrawn",
                "event_id": "event-withdrawn",
                "applicability_id": applicability["_id"],
                "kind": "WITHDRAWN",
                "effective_at": "2026-06-01",
            }
        )

    run = await _generate(monkeypatch, world=world, before_generate=_seal_then_withdraw)

    why = "an approved matrix row whose instrument was withdrawn after approval"
    _assert_absent(run, AUTHORISED_MARKER, why)
    _assert_source_absent(run, clause_id, why)
    _assert_source_absent(run, row_id, why)


@pytest.mark.asyncio
async def test_a_stored_matrix_selection_is_revalidated_on_every_rehydration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The REHYDRATION door independently. A lawful selection made yesterday is
    not a lawful selection today."""
    world, clause_id, row_id = _eligible_world()
    selection = _matrix_selection(row_id, source_id=clause_id)

    lawful = await _assemble(monkeypatch, world=world, references=[selection])
    _assert_present(lawful, AUTHORISED_MARKER, "the selection while it was lawful")

    def _withdraw(db: _Database) -> None:
        db[APPLICABILITY_EVENTS].rows.append(
            {
                "_id": "evt-withdrawn",
                "event_id": "event-withdrawn",
                "applicability_id": db[APPLICABILITY].rows[0]["_id"],
                "kind": "WITHDRAWN",
                "effective_at": "2026-06-01",
            }
        )

    outcome = await _assemble(
        monkeypatch, world=world, references=[selection], mutate_db=_withdraw
    )

    _assert_absent(
        outcome,
        AUTHORISED_MARKER,
        "a stored matrix selection whose instrument has since been withdrawn",
    )


# ---------------------------------------------------------------------------
# RED 20 / RED 21 - valid empty and authority failure may not widen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_when_every_approved_row_is_ineligible_the_matrix_contributes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No broadening to generic contract search, no fallback to unapproved rows."""
    world = _World()
    world.instrument(document_id="doc-project-b", project_id="proj-B", contract_id="MAIN")
    ineligible = world.clause_chunk(
        document_id="doc-project-b", project_id="proj-A", text=SIBLING_PROJECT_MARKER
    )
    world.approved_clause_row(
        clause_source_id=ineligible,
        excerpt="Clause 8.4 {}".format(SIBLING_PROJECT_MARKER),
    )
    world.approved_clause_row(
        clause_source_id=ineligible,
        excerpt="Clause 8.5 {}".format(UNAPPROVED_MARKER),
        approval_status="needs_review",
        verification_status="needs_review",
    )

    outcome = await _assemble(monkeypatch, world=world)

    assert not outcome.clause_rows(), (
        "every approved matrix row was ineligible, so the clause-matrix "
        "contribution must be EMPTY: {!r}".format(outcome.clause_rows())
    )
    _assert_absent(outcome, SIBLING_PROJECT_MARKER, "a valid-empty matrix contribution")
    _assert_absent(outcome, UNAPPROVED_MARKER, "a valid-empty matrix contribution")


@pytest.mark.asyncio
async def test_an_authority_resolution_failure_never_admits_an_approved_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure must not impersonate an answer, and must not be trusted away."""
    world, clause_id, row_id = _eligible_world()

    def _break_applicability(db: _Database) -> None:
        db[APPLICABILITY].find_raises = RuntimeError("applicability enumeration failed")

    outcome = await _assemble(monkeypatch, world=world, mutate_db=_break_applicability)

    why = "an approved matrix row when canonical eligibility could not be resolved"
    _assert_absent(outcome, AUTHORISED_MARKER, why)
    assert not outcome.clause_rows(), (
        "{}: the clause-matrix door contributed rows anyway".format(why)
    )
    assert any("eligibility" in warning for warning in outcome.warnings()), (
        "{}: the failure was collapsed into a silent empty ledger with no "
        "context warning: {!r}".format(why, outcome.warnings())
    )


# ---------------------------------------------------------------------------
# RED 22 - the lowest matrix consumer, called directly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_automatic_matrix_helper_refuses_without_a_principal() -> None:
    """A caller that omits the actor gets an EMPTY universe, never the draft's stamp."""
    world, clause_id, row_id = _eligible_world()
    db = world.database([_draft()])

    rows = await ArbitrationContextBuilder(db)._clause_matrix_sources(
        CASE_ID, 0, False, []
    )

    assert not rows, (
        "the lowest clause-matrix consumer admitted an approved row with no "
        "principal and no draft in hand. Absent authority must deny, never "
        "fall back to the row's own provenance: {!r}".format(rows)
    )


@pytest.mark.asyncio
async def test_a_rootless_worker_principal_cannot_open_the_matrix_door(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R17's shape: a principal fabricated from the resource being read."""
    world, clause_id, row_id = _eligible_world()
    draft = _draft()

    outcome = await _assemble(
        monkeypatch, world=world, draft=draft, actor=_rootless_worker(draft)
    )

    _assert_absent(
        outcome,
        AUTHORISED_MARKER,
        "an approved matrix row read for a principal fabricated from the draft",
    )


@pytest.mark.asyncio
async def test_an_actor_entitled_only_to_a_sibling_project_opens_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world, clause_id, row_id = _eligible_world()

    outcome = await _assemble(monkeypatch, world=world, actor=_sibling_project_actor())

    _assert_absent(
        outcome,
        AUTHORISED_MARKER,
        "an approved matrix row read by an actor entitled only to another project",
    )


# ---------------------------------------------------------------------------
# The WRITER door - prepare_draft_from_case must not persist an ineligible row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_case_writer_never_persists_an_ineligible_approved_clause_row() -> None:
    world, clause_id, row_id = _sibling_project_world()
    db = world.database([_draft()])

    references = await ArbitrationCaseWorkspaceService(db)._references_from_case_rows(
        "draft-1",
        [],
        [row for row in db[CLAUSE_MATRIX].rows],
        _project_actor(),
        draft=_draft(),
    )

    blob = repr(references)
    assert SIBLING_PROJECT_MARKER not in blob, (
        "prepare_draft_from_case persisted a sibling project's clause as the "
        "draft's standing evidence: {}".format(blob)
    )
    assert clause_id not in {str(ref.get("source_id")) for ref in references}, (
        "the ineligible clause's identifier was persisted as a selected reference"
    )
    assert AUTHORISED_MARKER in blob, (
        "the LAWFUL companion row was dropped too, so the exclusion proves "
        "nothing: {}".format(blob)
    )


@pytest.mark.asyncio
async def test_the_case_writer_refuses_every_clause_row_without_a_draft() -> None:
    """Fail-closed default: no draft in hand is an unanswerable question."""
    world, clause_id, row_id = _eligible_world()
    db = world.database([_draft()])

    references = await ArbitrationCaseWorkspaceService(db)._references_from_case_rows(
        "draft-1", [], [row for row in db[CLAUSE_MATRIX].rows], _project_actor()
    )

    assert AUTHORISED_MARKER not in repr(references), (
        "the case writer resolved a clause row with no draft, and therefore no "
        "workspace, in hand"
    )


# ---------------------------------------------------------------------------
# RED 19 - matrix history is preserved; it just stops being generation evidence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_excluded_row_remains_in_the_case_matrix_as_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R24 governs the generation ledger, not the case record.

    Deleting or rewriting the approval would destroy the provenance of a
    curation decision a human really made.
    """
    world, clause_id, row_id = _sibling_project_world()

    run = await _generate(monkeypatch, world=world)

    stored = [row for row in run.db[CLAUSE_MATRIX].rows if str(row.get("_id")) == row_id]
    assert stored, "the excluded matrix row was deleted from the case matrix"
    assert str(stored[0].get("approval_status")) == "approved", (
        "the historical approval was rewritten: {!r}".format(stored[0])
    )
    _assert_absent(
        run,
        SIBLING_PROJECT_MARKER,
        "an excluded row kept as case history",
    )


# ---------------------------------------------------------------------------
# The anti-over-block pins - a containment that empties the matrix is an outage
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_synthesised_arbitration_agreement_is_not_fenced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``case:{id}:arbitration_clause`` is prose typed onto the case.

    It names no extraction and no contract instrument, so there is no
    applicability to resolve. Fencing it would withhold the jurisdictional
    foundation of the pleading.
    """
    world = _World()
    world.approved_clause_row(
        clause_source_id=synthetic_case_clause_id(CASE_ID),
        excerpt="Arbitration agreement. {}".format(SYNTHETIC_MARKER),
        clause_number="Arbitration clause",
        topic="Arbitration agreement",
    )

    outcome = await _assemble(monkeypatch, world=world)

    _assert_present(
        outcome, SYNTHETIC_MARKER, "the synthesised arbitration agreement"
    )


@pytest.mark.asyncio
async def test_a_matrix_authored_clause_row_with_no_document_source_keeps_its_own_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A row that names no document is application content, not a derivative.

    Its ``clause_text_excerpt`` is already withheld by publication authority
    (there is no id to resolve), so nothing document-derived can escape - and
    counsel's own ``obligation_or_right`` must survive.
    """
    world = _World()
    world.approved_clause_row(
        clause_source_id=None,
        excerpt="Clause 8.4 {}".format(ORPHAN_MARKER),
        obligation=IN_APP_MARKER,
    )

    outcome = await _assemble(monkeypatch, world=world)

    _assert_present(outcome, IN_APP_MARKER, "a matrix-authored clause row")
    _assert_absent(
        outcome,
        ORPHAN_MARKER,
        "a matrix-authored clause row with an unresolvable excerpt",
    )


@pytest.mark.asyncio
async def test_r24_provides_project_evidence_containment_not_contract_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The carried owner decision, pinned rather than implied.

    The arbitration ``contract_id`` is free text nothing joins against
    ``contract_document_applicability``, and a matrix row's own ``contract_id``
    is provenance. R24 therefore contains to the PROJECT's applicable evidence,
    and a clause governing sibling contract ``OTHER`` inside the SAME authorised
    project still contributes. Stating it here stops the containment from being
    read as exact-contract validation it does not perform.
    """
    world, _c, _r = _eligible_world()
    world.instrument(
        document_id="doc-sibling-contract", project_id="proj-A", contract_id="OTHER"
    )
    clause_id = world.clause_chunk(
        document_id="doc-sibling-contract", text=SIBLING_CONTRACT_MARKER
    )
    world.approved_clause_row(
        clause_source_id=clause_id,
        excerpt="Clause 8.4 {}".format(SIBLING_CONTRACT_MARKER),
    )

    outcome = await _assemble(
        monkeypatch, world=world, draft=_draft(contract_id="MAIN")
    )

    _assert_present(
        outcome,
        SIBLING_CONTRACT_MARKER,
        "a clause of a SIBLING CONTRACT inside the same authorised project",
    )


# ---------------------------------------------------------------------------
# Structural pins - the doors themselves, not one of their outcomes
# ---------------------------------------------------------------------------


def _reachable_from(class_source: str, entry: str) -> Tuple[set, set]:
    """Method names and called names reachable from ``entry`` within the class.

    Walking the call graph rather than the whole module matters: the automatic
    contract-clause path and the selected-reference path already consume the
    canonical universe, so a module-wide search would pass no matter what the
    MATRIX doors do.
    """
    tree = ast.parse(class_source)
    methods: Dict[str, ast.AST] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.setdefault(node.name, node)

    seen: set = set()
    called: set = set()
    pending = [entry]
    while pending:
        name = pending.pop()
        if name in seen or name not in methods:
            continue
        seen.add(name)
        for node in ast.walk(methods[name]):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name):
                called.add(func.id)
            elif isinstance(func, ast.Attribute):
                called.add(func.attr)
                if (
                    isinstance(func.value, ast.Name)
                    and func.value.id == "self"
                    and func.attr in methods
                ):
                    pending.append(func.attr)
    return seen, called


@pytest.mark.parametrize(
    "entry", ["_clause_matrix_sources", "_rehydrate_matrix_reference"]
)
def test_both_matrix_doors_consult_the_shared_canonical_fence(entry: str) -> None:
    """One helper, two doors. Two subtly different implementations is the
    failure mode this programme keeps finding."""
    source = inspect.getsource(ArbitrationContextBuilder)
    _methods, called = _reachable_from(source, entry)
    assert "admits_clause_row" in called or "matrix_clause_fence" in called, (
        "{} does not consult the shared approved-matrix fence. Human approval "
        "must be INTERSECTED with the canonical eligible universe, never "
        "trusted as authority.".format(entry)
    )


def test_the_matrix_fence_is_built_on_the_canonical_universe() -> None:
    from rbac_backend.services.arbitration_drafting import matrix_evidence_authority

    called = {
        node.func.id
        for node in ast.walk(ast.parse(inspect.getsource(matrix_evidence_authority)))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "resolve_authorized_project_universe" in called, (
        "the approved-matrix fence does not reach "
        "resolve_authorized_project_universe. There is exactly one canonical "
        "eligibility answer and this must be it."
    )


def test_the_matrix_fence_does_not_reimplement_applicability() -> None:
    from rbac_backend.services.arbitration_drafting import matrix_evidence_authority

    tree = ast.parse(inspect.getsource(matrix_evidence_authority))
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        )
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
        and isinstance(node.body[0].value.value, str)
    }
    literals = {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    }
    for forbidden in (APPLICABILITY, APPLICABILITY_EVENTS, CONTRACT_DOCUMENTS):
        assert forbidden not in literals, (
            "{!r} is read directly by the approved-matrix fence. Contract "
            "eligibility has exactly one implementation; a second one here "
            "would drift from it silently.".format(forbidden)
        )
