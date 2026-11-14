"""G-A11 / R20: a USER-SELECTED arbitration clause is a request, not an authority.

G-A9 closed the AUTOMATIC contract-evidence door in arbitration drafting. This
file closes the other one, and it is a different door class: the user names the
source themselves.

``ArbitrationContextBuilder.rehydrate_selected_references`` takes the references
the client asked for and resolves each one against a server record. For a
``source_type="clause"`` reference that resolution was::

    {_id: <candidates>, organization_id: <draft>, project_id: <draft>}
      against document_vectors, then contract_clauses
      (falling back to {document_id: <the id the client sent>})

plus ``resolve_document_authority`` on the resolved parent document. So the
selected clause passed exactly ONE canonical test - publication - and even that
one did not REJECT it: a denied document kept its ledger row, its citation and
its provenance, with the snippet collapsed to the label and the row flagged.
Nothing on this path asked:

* is the instrument APPLICABLE to this project at all, or merely catalogued;
* is it still in force, or WITHDRAWN / SUPERSEDED;
* is its clause projection CURRENT, or a revision behind, PENDING or FAILED;
* does the CALLER have canonical authority over the workspace being drafted in.

The draft's own ``organization_id`` / ``project_id`` are the workspace the row
was FILED in. A derived ``document_vectors`` / ``contract_clauses`` row's stamp
is INGEST PROVENANCE. Neither records that an instrument legally governs
anything, and a user's selection records only that a human clicked it.

**USER SELECTION IS NOT AUTHORITY.** A user may REQUEST that a clause be
considered; the system may USE it only where it is currently eligible under the
same canonical boundary the automatic path consumes::

      applicable to (project_id, contract_id) at the query mode
    & canonical Document positively resolvable
    & publication-consumable
    & projection-current

resolved by ``ContractScopeResolver`` for an actor who is canonically entitled
to the workspace - i.e. ``resolve_authorized_project_universe``, the seam G-A9
already built beside this one. Selected sources are INTERSECTED with that
universe. They are never unioned with it: a selection cannot expand authority.

Why this is material rather than cosmetic. The hydrated reference is:

* persisted into ``arbitration_selected_references`` by ``create_draft`` and
  ``add_references``, where it becomes the draft's standing evidence;
* rebuilt into the ``source_ledger`` on every ``build``;
* rendered into the pleading by the generator, so clause text becomes draft text;
* hashed into ``stable_generation_input_hash``, which decides run reuse;
* written into ``arbitration_draft_versions.source_ledger``, a row sealed by
  ``immutable_version_hash`` and rendered by the DOCX/PDF exporter;
* written as ``arbitration_generation_runs.source_ids``.

Text and PROVENANCE are therefore asserted separately. Removing the clause text
while keeping its ``source_id`` still records an unauthorised instrument as a
contributor to a pleading, in a row that is sealed and exported.

**Native semantics for a selection that does not resolve.** The hydrator already
raises ``400 Selected arbitration source could not be verified in the current
scope`` when a reference resolves to nothing. An ineligible clause is exactly
that - it does not resolve within the authorised evidence universe - so it takes
the same door rather than a new one. Consequences, stated rather than hidden:
nothing is persisted for a rejected selection (RED 13 is satisfied by absence,
not by a parallel audit field), and a generation whose stored selection has
SINCE become ineligible fails closed instead of quietly dropping evidence out of
a pleading nobody re-read. Fail-visible is the house direction; it is still a
change in observable outcome.

**Scope, stated rather than assumed.**

* This fence is CLAUSE-scoped. A ``document``/``letter``/``claim`` reference is
  not a contract instrument and has no applicability aggregate; fencing those on
  the contract universe would delete legitimate correspondence from live
  pleadings. :func:`test_a_selected_correspondence_document_is_not_fenced_by_contract_applicability`
  pins that, so the containment cannot quietly become a blanket denial.
* PROJECT-EVIDENCE CONTAINMENT, carried unchanged from G-A7/G-A8/G-A9. The
  arbitration draft carries a ``contract_id``, but it is free text that nothing
  validates or joins against ``contract_document_applicability``; only the
  register scope query reads it. Keying selections on it would invent an
  identity mapping. A clause governing sibling contract ``OTHER`` inside the
  SAME authorised project can therefore still be selected for a draft about
  ``MAIN``. That residual is bounded and pinned at the resolver
  (:func:`test_the_canonical_universe_separates_two_contracts`); closing it is an
  owner decision.
* The APPROVED-MATRIX branch of the same hydrator (a reference carrying
  ``metadata.matrix_row_id``) is a different door: it resolves against
  case-scoped rows that a human approved, and it is recorded as a separate
  finding rather than absorbed here.

The positives are load-bearing. ``processing_status == "failed"`` is OPERATIONAL
- extraction broke, which is not a verdict about the content - so a
last-known-good instrument's clause must still be selectable, and an applicable,
publication-consumable, projection-current clause must still ground the pleading.
A containment that makes manual selection useless is an outage, not a boundary.
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

from rbac_backend.models.contract_document import CurrentState
from rbac_backend.services.arbitration_drafting import context as arbitration_context
from rbac_backend.services.arbitration_drafting.context import ArbitrationContextBuilder
from rbac_backend.services.arbitration_drafting.service import ArbitrationDraftingService
from rbac_backend.services.contract_scope_resolver import (
    resolve_authorized_project_universe,
)

# --- unique markers -------------------------------------------------------
#
# Every marker is unique and lives ONLY inside clause text, never inside an
# identifier, so an assertion can never pass because the string happened to be
# absent for an unrelated reason - and a rejection receipt that echoes the
# requested id can never be mistaken for leaked evidence.

AUTHORISED_MARKER = "R20_PROJECT_A_SELECTED_CLAUSE_AUTHORISED_20260831"
SIBLING_PROJECT_MARKER = "R20_PROJECT_B_SELECTED_CLAUSE_CONFIDENTIAL_20260831"
NO_APPLICABILITY_MARKER = "R20_CATALOGUE_ONLY_SELECTED_CONFIDENTIAL_20260831"
WITHDRAWN_MARKER = "R20_WITHDRAWN_SELECTED_CONFIDENTIAL_20260831"
SUPERSEDED_MARKER = "R20_SUPERSEDED_SELECTED_CONFIDENTIAL_20260831"
HUMAN_REVIEW_MARKER = "R20_HUMAN_REVIEW_SELECTED_CONFIDENTIAL_20260831"
DUPLICATE_MARKER = "R20_DUPLICATE_SELECTED_CONFIDENTIAL_20260831"
DELETED_MARKER = "R20_DELETED_SELECTED_CONFIDENTIAL_20260831"
ORPHAN_MARKER = "R20_ORPHAN_SELECTED_CONFIDENTIAL_20260831"
LYING_CLAUSE_MARKER = "R20_LYING_ROW_SELECTED_CONFIDENTIAL_20260831"
STALE_PROJECTION_MARKER = "R20_STALE_PROJECTION_SELECTED_CONFIDENTIAL_20260831"
PENDING_PROJECTION_MARKER = "R20_PENDING_PROJECTION_SELECTED_CONFIDENTIAL_20260831"
FAILED_PROJECTION_MARKER = "R20_FAILED_PROJECTION_SELECTED_CONFIDENTIAL_20260831"
OPERATIONAL_FAILED_MARKER = "R20_OPERATIONAL_FAILED_LAST_KNOWN_GOOD_20260831"
FOREIGN_TENANT_MARKER = "R20_FOREIGN_TENANT_SELECTED_CONFIDENTIAL_20260831"
CORRESPONDENCE_MARKER = "R20_CORRESPONDENCE_DOCUMENT_AUTHORISED_20260831"

APPLICABILITY = "contract_document_applicability"
APPLICABILITY_EVENTS = "contract_document_applicability_events"
CONTRACT_DOCUMENTS = "contract_documents"
CONTRACT_CLAUSES = "contract_clauses"
DOCUMENT_VECTORS = "document_vectors"
SELECTED_REFERENCES = "arbitration_selected_references"

#: The clause stores a selected reference can resolve against. The fence must
#: apply to BOTH, and every lookup issued against either of them must carry it.
CLAUSE_STORES = (DOCUMENT_VECTORS, CONTRACT_CLAUSES)


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
        return (1, f"{float(value):020.4f}")
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
        #: `find_one`. A selected reference is resolved with `find_one`, so a
        #: harness that only recorded `find` could not tell the difference
        #: between "the fence was in the query" and "the fence was applied to
        #: the result afterwards" - which is the distinction this phase is
        #: about.
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
    """Seeds the canonical Contract Master records for one test.

    Nothing here writes an "eligible" flag. Eligibility is whatever
    ``ContractScopeResolver`` derives from applicability events, the contract
    document record's projection fields and the canonical ``documents`` row - so
    a test cannot accidentally assert against a fixture's opinion.
    """

    def __init__(self) -> None:
        self.documents: List[Dict[str, Any]] = []
        self.contract_documents: List[Dict[str, Any]] = []
        self.applicability: List[Dict[str, Any]] = []
        self.applicability_events: List[Dict[str, Any]] = []
        self.clauses: List[Dict[str, Any]] = []
        self.vectors: List[Dict[str, Any]] = []
        self._seq = 0

    def _next(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq}"

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
        ``canonical_document=False`` means the selected clause's ``document_id``
        resolves to nothing - the ORPHAN case.
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
                "document_version_id": f"{document_id}-v1",
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
                        "effective_at": f"2026-0{index + 1}-01",
                    }
                )
        return contract_document_id

    def selectable_clause(
        self,
        *,
        document_id: str,
        text: str,
        organization_id: str = "org-A",
        project_id: str = "proj-A",
        clause_number: str = "8.4",
        vector_id: Optional[str] = None,
    ) -> str:
        """A ``document_vectors`` chunk - what a selected clause resolves to.

        ``document_vectors`` is the live store for a ``source_type="clause"``
        selection: it is the collection the reference hydrator queries first,
        and the only one of the two whose rows carry ``organization_id`` /
        ``project_id`` (``contract_clauses`` stamps ``org_id``), so it is the one
        a scoped lookup can actually match.

        Several tests deliberately stamp a chunk with the drafting workspace
        while its canonical instrument says otherwise. That is the
        lying-provenance case: the row records where it was FILED, never that the
        instrument governs anything, and it must not authorise itself.

        Returns the id a client would send as ``source_id``.
        """
        row_id = vector_id or self._next("vec")
        self.vectors.append(
            {
                "_id": row_id,
                "organization_id": organization_id,
                "project_id": project_id,
                "document_id": document_id,
                "clause_number": clause_number,
                "title": f"Extension of time {row_id}",
                "text": (
                    f"The Contractor shall be entitled to an extension of time. {text}"
                ),
                "page_numbers": [12],
            }
        )
        return row_id

    def database(
        self,
        drafts: Optional[List[Dict[str, Any]]] = None,
        selected: Optional[List[Dict[str, Any]]] = None,
        letters: Optional[List[Dict[str, Any]]] = None,
    ) -> _Database:
        return _Database(
            {
                "arbitration_drafts": list(drafts or []),
                SELECTED_REFERENCES: list(selected or []),
                "letters": list(letters or []),
                "documents": self.documents,
                CONTRACT_DOCUMENTS: self.contract_documents,
                APPLICABILITY: self.applicability,
                APPLICABILITY_EVENTS: self.applicability_events,
                CONTRACT_CLAUSES: self.clauses,
                DOCUMENT_VECTORS: self.vectors,
            }
        )


class _DisabledEvidenceGraph:
    """The evidence graph is a different seam (pinned by test_graph_consumer_authority)."""

    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    async def downstream_links(self, *_a: Any, **_k: Any) -> List[Dict[str, Any]]:
        return []


# ---------------------------------------------------------------------------
# Actors
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


def _global_actor() -> SimpleNamespace:
    """A global actor, resolved through canonical authorization - not by name."""
    return SimpleNamespace(
        id="user-super",
        email="super@example.com",
        roles=["superadmin"],
        organization_id="org-A",
        organizations=["org-A", "org-B"],
        projects=["proj-A", "proj-B"],
    )


def _rootless_worker(draft: Dict[str, Any]) -> SimpleNamespace:
    """R17's shape: a principal FABRICATED from the resource being read.

    It has no roles, so canonical authorisation denies it. Pinned here so a
    later change cannot make fabricated principals permissive while widening a
    shared authority seam.
    """
    return SimpleNamespace(
        id="worker",
        roles=[],
        organization_id=draft.get("organization_id"),
        project_id=draft.get("project_id"),
    )


# ---------------------------------------------------------------------------
# Drafts, selections and the surfaces a selection reaches
# ---------------------------------------------------------------------------


def _draft(
    *,
    draft_id: str = "draft-1",
    organization_id: str = "org-A",
    project_id: str = "proj-A",
    contract_id: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "_id": draft_id,
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
        "include_register_sources": True,
        "excluded_register_ids": [],
        "status": "draft",
    }


def _selection(
    source_id: str,
    *,
    source_type: str = "clause",
    reference_id: str = "ref-1",
    draft_id: str = "draft-1",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """What the CLIENT sends, in the shape the repository stores.

    ``label`` and ``snippet`` are deliberately client prose: the hydrator
    discards them and rebuilds from the server record, so no assertion in this
    file can pass because a marker was absent from something the client wrote.
    """
    return {
        "_id": reference_id,
        "draft_id": draft_id,
        "source_type": source_type,
        "source_id": source_id,
        "label": "Clause selected by the user",
        "citation": "8.4",
        "allowed_use": "clause" if source_type == "clause" else "fact",
        "metadata": dict(metadata or {}),
    }


class _Outcome:
    """Everything one hydration attempt made observable.

    A REJECTION is an outcome, not an error the test swallows: the rejection
    receipt is inspected for leaked evidence exactly like a ledger is. It echoes
    the requested identifier by design - that is a 400 telling the caller which
    selection failed - so provenance is asserted against the LEDGER only, and
    every marker lives in clause TEXT so a receipt can never look like a leak.
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

    def text(self) -> str:
        return repr(self.context) + repr(getattr(self.error, "detail", ""))

    def provenance(self) -> set:
        ids: set = set()
        for row in self.rows:
            ids.add(str(row.get("source_id")))
            metadata = row.get("metadata") or {}
            for key in ("document_id", "upload_id", "source_document_id"):
                if metadata.get(key):
                    ids.add(str(metadata[key]))
        return ids

    def clause_queries(self) -> List[Optional[Dict[str, Any]]]:
        queries: List[Optional[Dict[str, Any]]] = []
        for name in CLAUSE_STORES:
            queries.extend(self.db[name].queries)
        return queries


def _evidence_lookups(
    queries: List[Optional[Dict[str, Any]]]
) -> List[Optional[Dict[str, Any]]]:
    """The clause-store lookups that can ADMIT a row, not the ones that deny.

    Two different reads hit these collections. An EVIDENCE lookup is scoped to
    the drafting workspace and decides what a selection resolves to - it is the
    one that can put clause text in front of a tribunal, and it is the one that
    must carry the canonical fence in its query. The central re-resolution in
    ``_annotate_source_quality`` also reads them, by bare ``_id``, for a row
    that is ALREADY in the ledger; its only possible outcome is to deny that
    row. Requiring a fence there would assert nothing about containment, so the
    discriminator is the workspace scope rather than a call-site name.
    """
    return [query for query in queries if "organization_id" in (query or {})]


async def _select(
    monkeypatch: pytest.MonkeyPatch,
    *,
    world: _World,
    references: List[Dict[str, Any]],
    draft: Optional[Dict[str, Any]] = None,
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
            draft, deepcopy(references), [], [], actor or _project_actor()
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

    def surfaces(self) -> Dict[str, str]:
        """The sealed row PLUS the draft's standing selection store.

        Separated because they answer different questions. A stored selection
        row that was lawful when it was made is immutable history - the same
        carried owner decision as G-A5...G-A10 - so a test about a selection
        that LATER lost its authority asserts on the sealed surfaces only. A
        test about a selection that was never lawful asserts on both: it must
        not exist anywhere.
        """
        return {
            **self.sealed_surfaces(),
            "persisted arbitration_selected_references": repr(
                self.db[SELECTED_REFERENCES].rows
            ),
        }

    def provenance(self) -> set:
        ids = {str(item) for item in (self.run.get("source_ids") or [])}
        for row in self.version.get("source_ledger") or []:
            ids.add(str(row.get("source_id")))
            metadata = row.get("metadata") or {}
            for key in ("document_id", "upload_id", "source_document_id"):
                if metadata.get(key):
                    ids.add(str(metadata[key]))
        return ids


async def _generate(
    monkeypatch: pytest.MonkeyPatch,
    *,
    world: _World,
    selected: List[Dict[str, Any]],
    draft: Optional[Dict[str, Any]] = None,
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
    if before_generate is not None:
        await before_generate(db, service)
    error: Optional[HTTPException] = None
    try:
        await service.generate(
            str(draft["_id"]), ArbitrationGenerateRequest(), actor or _project_actor()
        )
    except HTTPException as exc:
        error = exc
    version = await db["arbitration_draft_versions"].find_one(
        {"draft_id": str(draft["_id"])}, sort=[("version", -1)]
    )
    run = await db["arbitration_generation_runs"].find_one(
        {"draft_id": str(draft["_id"])}
    )
    return _Run(db, version or {}, run or {}, error=error)


def _assert_absent(
    outcome: Any, marker: str, why: str, *, surfaces: Any = None
) -> None:
    if surfaces is None:
        surfaces = (
            outcome.surfaces()
            if isinstance(outcome, _Run)
            else {"context": outcome.text()}
        )
    for name, blob in surfaces.items():
        assert marker not in blob, (
            f"{why}: the marker {marker!r} reached {name}. A user's selection "
            "records that a human clicked a row - never that the instrument "
            "legally governs this project. Only the canonical Contract Master "
            "universe may admit a clause to a pleading."
        )


def _assert_present(outcome: Any, marker: str, why: str) -> None:
    if isinstance(outcome, _Run):
        blob = "".join(outcome.surfaces().values())
    else:
        blob = outcome.text()
    assert marker in blob, (
        f"{why}: the authorised marker {marker!r} reached NOTHING. A "
        "containment that makes manual selection useless is an outage, not a "
        "boundary."
    )


def _assert_source_absent(outcome: Any, source_id: str, why: str) -> None:
    """The ledger records the identifier the SELECTION named.

    That is the chunk id the client sent, not the parent document id, so this
    is the identity that has to disappear: it is what
    ``arbitration_generation_runs.source_ids`` stores and what the sealed
    version row cites.
    """
    assert source_id not in outcome.provenance(), (
        f"{why}: the ineligible source id {source_id!r} survived in the "
        "arbitration source ledger's provenance. Removing the clause text while "
        "keeping the identifier still records an unauthorised instrument as a "
        "contributor to a pleading, in a row sealed by immutable_version_hash "
        "and rendered by the exporter."
    )


async def _assert_lawful_selection_still_works(
    monkeypatch: pytest.MonkeyPatch, world: _World, lawful_source_id: str, why: str
) -> None:
    """Non-vacuity: the SAME world still admits its lawful selection.

    Every containment case seeds an eligible companion instrument and proves it
    here, so an exclusion can never pass because the whole path was broken, or
    because a hydration failure short-circuited before authority was consulted.
    """
    outcome = await _select(
        monkeypatch, world=world, references=[_selection(lawful_source_id)]
    )
    assert not outcome.rejected, (
        f"{why}: the LAWFUL companion selection was rejected too "
        f"({getattr(outcome.error, 'detail', None)!r}). The exclusion under test "
        "therefore proves nothing about authority."
    )
    _assert_present(outcome, AUTHORISED_MARKER, why)


def _eligible_world() -> Tuple[_World, str]:
    """One applicable, consumable, projection-current instrument and its clause."""
    world = _World()
    world.instrument(document_id="doc-eligible", project_id="proj-A", contract_id="MAIN")
    source_id = world.selectable_clause(
        document_id="doc-eligible", text=AUTHORISED_MARKER
    )
    return world, source_id


# ---------------------------------------------------------------------------
# The canonical universe itself - the machinery the hydrator must consume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_canonical_universe_separates_two_contracts() -> None:
    """Exact ``(project_id, contract_id)`` identity, not ``contract_id`` alone.

    The consumer-level "wrong contract, same project" case is OWNER DECISION /
    NOT ENFORCEABLE AT THE CURRENT REQUEST CONTRACT, exactly as G-A9 recorded:
    the arbitration draft's ``contract_id`` is free text nothing joins against
    ``contract_document_applicability``. Marking it so is only honest if the
    identity machinery underneath stays exact, which is what this pins.
    """
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.instrument(document_id="doc-B-MAIN", project_id="proj-B", contract_id="MAIN")
    world.instrument(document_id="doc-A-OTHER", project_id="proj-A", contract_id="OTHER")
    db = world.database()

    universe = await resolve_authorized_project_universe(
        db,
        _global_actor(),
        organization_id="org-A",
        project_id="proj-A",
        mode=CurrentState(),
    )

    assert "doc-B-MAIN" not in universe.eligible_document_ids, (
        "the same contract_id in a SIBLING project entered the universe - "
        "contract identity is the pair, never the contract_id alone"
    )
    assert "doc-A-MAIN" in universe.eligible_document_ids
    assert "doc-A-OTHER" in universe.eligible_document_ids


# ---------------------------------------------------------------------------
# RED 1-9: what a selection may NOT do
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_selected_sibling_project_clause_cannot_ground_a_pleading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 1. The row is stamped with THIS workspace; the instrument is not.

    An ingest stamp is where a derived row was filed. The instrument behind this
    one has applicability to project B only, so it governs nothing in project A -
    and the user selecting it changes that not at all.
    """
    world, lawful = _eligible_world()
    world.instrument(document_id="doc-sibling", project_id="proj-B", contract_id="MAIN")
    selected = world.selectable_clause(
        document_id="doc-sibling",
        text=SIBLING_PROJECT_MARKER,
        project_id="proj-A",  # the lie
    )

    outcome = await _select(monkeypatch, world=world, references=[_selection(selected)])

    _assert_absent(outcome, SIBLING_PROJECT_MARKER, "a selected sibling-project clause")
    _assert_source_absent(outcome, selected, "a selected sibling-project clause")
    await _assert_lawful_selection_still_works(
        monkeypatch, world, lawful, "a selected sibling-project clause"
    )


@pytest.mark.asyncio
async def test_a_selected_zero_applicability_clause_cannot_ground_a_pleading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 2. Catalogued in the organisation is not applied to the project."""
    world, lawful = _eligible_world()
    world.instrument(document_id="doc-catalogue", project_id=None, contract_id=None)
    selected = world.selectable_clause(
        document_id="doc-catalogue", text=NO_APPLICABILITY_MARKER
    )

    outcome = await _select(monkeypatch, world=world, references=[_selection(selected)])

    _assert_absent(outcome, NO_APPLICABILITY_MARKER, "a selected catalogue-only clause")
    _assert_source_absent(outcome, selected, "a selected catalogue-only clause")
    await _assert_lawful_selection_still_works(
        monkeypatch, world, lawful, "a selected catalogue-only clause"
    )


@pytest.mark.asyncio
async def test_a_selected_withdrawn_instrument_cannot_ground_a_pleading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 3. A pleading is written now and cites what governs now."""
    world, lawful = _eligible_world()
    world.instrument(
        document_id="doc-withdrawn",
        project_id="proj-A",
        contract_id="MAIN",
        lifecycle=("APPLIED", "WITHDRAWN"),
    )
    selected = world.selectable_clause(
        document_id="doc-withdrawn", text=WITHDRAWN_MARKER
    )

    outcome = await _select(monkeypatch, world=world, references=[_selection(selected)])

    _assert_absent(outcome, WITHDRAWN_MARKER, "a selected withdrawn instrument")
    _assert_source_absent(outcome, selected, "a selected withdrawn instrument")
    await _assert_lawful_selection_still_works(
        monkeypatch, world, lawful, "a selected withdrawn instrument"
    )


@pytest.mark.asyncio
async def test_a_selected_superseded_instrument_cannot_ground_a_pleading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 3. Superseded is the other half of lifecycle, and not the same event."""
    world, lawful = _eligible_world()
    world.instrument(
        document_id="doc-superseded",
        project_id="proj-A",
        contract_id="MAIN",
        lifecycle=("APPLIED", "SUPERSEDED"),
    )
    selected = world.selectable_clause(
        document_id="doc-superseded", text=SUPERSEDED_MARKER
    )

    outcome = await _select(monkeypatch, world=world, references=[_selection(selected)])

    _assert_absent(outcome, SUPERSEDED_MARKER, "a selected superseded instrument")
    _assert_source_absent(outcome, selected, "a selected superseded instrument")
    await _assert_lawful_selection_still_works(
        monkeypatch, world, lawful, "a selected superseded instrument"
    )


@pytest.mark.asyncio
async def test_a_selected_human_review_document_cannot_ground_a_pleading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 4. Publication authority already gated the TEXT; it did not reject.

    A denied document kept its ledger row, its citation and its identifier, with
    the snippet collapsed to the label. That row is sealed into the version and
    exported, so a blocked instrument still appeared as a contributor.
    """
    world, lawful = _eligible_world()
    world.instrument(
        document_id="doc-human-review",
        project_id="proj-A",
        contract_id="MAIN",
        processing_status="human_review_required",
    )
    selected = world.selectable_clause(
        document_id="doc-human-review", text=HUMAN_REVIEW_MARKER
    )

    outcome = await _select(monkeypatch, world=world, references=[_selection(selected)])

    _assert_absent(outcome, HUMAN_REVIEW_MARKER, "a selected human-review document")
    _assert_source_absent(outcome, selected, "a selected human-review document")
    await _assert_lawful_selection_still_works(
        monkeypatch, world, lawful, "a selected human-review document"
    )


@pytest.mark.asyncio
async def test_a_selected_duplicate_document_cannot_ground_a_pleading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 5. Selection does not override publication authority."""
    world, lawful = _eligible_world()
    world.instrument(
        document_id="doc-duplicate",
        project_id="proj-A",
        contract_id="MAIN",
        duplicate_status="duplicate",
    )
    selected = world.selectable_clause(
        document_id="doc-duplicate", text=DUPLICATE_MARKER
    )

    outcome = await _select(monkeypatch, world=world, references=[_selection(selected)])

    _assert_absent(outcome, DUPLICATE_MARKER, "a selected duplicate document")
    _assert_source_absent(outcome, selected, "a selected duplicate document")
    await _assert_lawful_selection_still_works(
        monkeypatch, world, lawful, "a selected duplicate document"
    )


@pytest.mark.asyncio
async def test_a_selected_deleted_document_cannot_ground_a_pleading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 5. Same for a soft-deleted instrument."""
    world, lawful = _eligible_world()
    world.instrument(
        document_id="doc-deleted",
        project_id="proj-A",
        contract_id="MAIN",
        lifecycle_state="deleted",
    )
    selected = world.selectable_clause(document_id="doc-deleted", text=DELETED_MARKER)

    outcome = await _select(monkeypatch, world=world, references=[_selection(selected)])

    _assert_absent(outcome, DELETED_MARKER, "a selected deleted document")
    _assert_source_absent(outcome, selected, "a selected deleted document")
    await _assert_lawful_selection_still_works(
        monkeypatch, world, lawful, "a selected deleted document"
    )


@pytest.mark.asyncio
async def test_a_selected_orphan_clause_cannot_self_authorise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 6. The chunk exists; the document it claims to come from does not.

    Positive resolution is the rule: a source that cannot be traced to a
    canonical Document is not proved safe, and a user pointing at it directly
    does not make it so.
    """
    world, lawful = _eligible_world()
    world.instrument(
        document_id="doc-orphan",
        project_id="proj-A",
        contract_id="MAIN",
        canonical_document=False,
    )
    selected = world.selectable_clause(document_id="doc-orphan", text=ORPHAN_MARKER)

    outcome = await _select(monkeypatch, world=world, references=[_selection(selected)])

    _assert_absent(outcome, ORPHAN_MARKER, "a selected orphan clause")
    _assert_source_absent(outcome, selected, "a selected orphan clause")
    await _assert_lawful_selection_still_works(
        monkeypatch, world, lawful, "a selected orphan clause"
    )


@pytest.mark.asyncio
async def test_a_selected_lying_clause_row_cannot_self_authorise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 7. The row asserts the workspace AND the contract; both are provenance.

    A derived row that names ``contract_id`` looks like identity. It is still the
    ingest stamp, and the canonical applicability record disagrees with it.
    """
    world, lawful = _eligible_world()
    world.instrument(document_id="doc-lying", project_id="proj-B", contract_id="MAIN")
    selected = world.selectable_clause(
        document_id="doc-lying",
        text=LYING_CLAUSE_MARKER,
        organization_id="org-A",
        project_id="proj-A",
    )
    world.vectors[-1]["contract_id"] = "MAIN"

    outcome = await _select(
        monkeypatch,
        world=world,
        references=[_selection(selected)],
        draft=_draft(contract_id="MAIN"),
    )

    _assert_absent(outcome, LYING_CLAUSE_MARKER, "a selected lying clause row")
    _assert_source_absent(outcome, selected, "a selected lying clause row")
    await _assert_lawful_selection_still_works(
        monkeypatch, world, lawful, "a selected lying clause row"
    )


@pytest.mark.asyncio
async def test_a_selected_stale_projection_cannot_ground_a_pleading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 8. ``CURRENT`` at revision N while the document is at N+1."""
    world, lawful = _eligible_world()
    world.instrument(
        document_id="doc-stale",
        project_id="proj-A",
        contract_id="MAIN",
        classification_revision=4,
        projection_revision=3,
        projection_status="CURRENT",
    )
    selected = world.selectable_clause(
        document_id="doc-stale", text=STALE_PROJECTION_MARKER
    )

    outcome = await _select(monkeypatch, world=world, references=[_selection(selected)])

    _assert_absent(outcome, STALE_PROJECTION_MARKER, "a selected stale projection")
    _assert_source_absent(outcome, selected, "a selected stale projection")
    await _assert_lawful_selection_still_works(
        monkeypatch, world, lawful, "a selected stale projection"
    )


@pytest.mark.asyncio
async def test_a_selected_pending_projection_cannot_ground_a_pleading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 9. The revision matches; the projection has not finished."""
    world, lawful = _eligible_world()
    world.instrument(
        document_id="doc-pending",
        project_id="proj-A",
        contract_id="MAIN",
        projection_status="PENDING",
    )
    selected = world.selectable_clause(
        document_id="doc-pending", text=PENDING_PROJECTION_MARKER
    )

    outcome = await _select(monkeypatch, world=world, references=[_selection(selected)])

    _assert_absent(outcome, PENDING_PROJECTION_MARKER, "a selected PENDING projection")
    _assert_source_absent(outcome, selected, "a selected PENDING projection")
    await _assert_lawful_selection_still_works(
        monkeypatch, world, lawful, "a selected PENDING projection"
    )


@pytest.mark.asyncio
async def test_a_selected_failed_projection_cannot_ground_a_pleading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 9. A FAILED projection is not a current one."""
    world, lawful = _eligible_world()
    world.instrument(
        document_id="doc-failed-projection",
        project_id="proj-A",
        contract_id="MAIN",
        projection_status="FAILED",
    )
    selected = world.selectable_clause(
        document_id="doc-failed-projection", text=FAILED_PROJECTION_MARKER
    )

    outcome = await _select(monkeypatch, world=world, references=[_selection(selected)])

    _assert_absent(outcome, FAILED_PROJECTION_MARKER, "a selected FAILED projection")
    _assert_source_absent(outcome, selected, "a selected FAILED projection")
    await _assert_lawful_selection_still_works(
        monkeypatch, world, lawful, "a selected FAILED projection"
    )


# ---------------------------------------------------------------------------
# RED 10-11: what a selection must STILL do
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_valid_selected_clause_still_grounds_a_pleading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 10. Applicable, resolvable, consumable, current - it contributes."""
    world, lawful = _eligible_world()

    outcome = await _select(monkeypatch, world=world, references=[_selection(lawful)])

    assert not outcome.rejected, (
        "a lawful selection was rejected: "
        f"{getattr(outcome.error, 'detail', None)!r}"
    )
    _assert_present(outcome, AUTHORISED_MARKER, "a lawful selection")
    assert lawful in outcome.provenance(), (
        "the lawful selection lost its provenance - a contributor a pleading "
        "cites must remain identifiable in the ledger"
    )


@pytest.mark.asyncio
async def test_an_operationally_failed_document_keeps_its_selected_clause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 11. ``processing_status='failed'`` is OPERATIONAL, not adverse.

    Extraction broke; nothing judged the content. Model B keeps the
    last-known-good instrument consumable, and confusing that with an adverse
    authority verdict would silently delete lawful evidence from a live pleading.
    """
    world = _World()
    world.instrument(
        document_id="doc-operational-failed",
        project_id="proj-A",
        contract_id="MAIN",
        processing_status="failed",
    )
    selected = world.selectable_clause(
        document_id="doc-operational-failed", text=OPERATIONAL_FAILED_MARKER
    )

    outcome = await _select(monkeypatch, world=world, references=[_selection(selected)])

    assert not outcome.rejected, (
        "an operationally failed document's clause was refused: "
        f"{getattr(outcome.error, 'detail', None)!r}. Operational failure is not "
        "an adverse authority judgement."
    )
    _assert_present(
        outcome, OPERATIONAL_FAILED_MARKER, "a last-known-good instrument"
    )


@pytest.mark.asyncio
async def test_a_selected_correspondence_document_is_not_fenced_by_contract_applicability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fence is CLAUSE-scoped, and that is a deliberate boundary.

    A letter or exhibit is not a contract instrument: it has no applicability
    aggregate, and it never will. Fencing ``document`` selections on the contract
    universe would delete legitimate correspondence from live pleadings - an
    outage dressed as a containment. This is the pin that stops the R20 fence
    from quietly becoming a blanket denial of everything a user selects.
    """
    world, _lawful = _eligible_world()
    world.documents.append(
        {
            "_id": "doc-correspondence",
            "organization_id": "org-A",
            "project_id": "proj-A",
            "processing_status": "completed",
            "filename": "EOT notice",
            "summary": f"Contractor notice of delay. {CORRESPONDENCE_MARKER}",
        }
    )

    outcome = await _select(
        monkeypatch,
        world=world,
        references=[_selection("doc-correspondence", source_type="document")],
    )

    assert not outcome.rejected, (
        "a correspondence document selection was rejected: "
        f"{getattr(outcome.error, 'detail', None)!r}. It is not a contract "
        "instrument and has no applicability record to satisfy."
    )
    _assert_present(outcome, CORRESPONDENCE_MARKER, "a selected correspondence document")


# ---------------------------------------------------------------------------
# Actor scope: the selection is resolved FOR SOMEBODY
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_actor_outside_the_workspace_admits_no_selected_clause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The draft's stamp is not the caller's entitlement.

    The hydrator resolved selections against the DRAFT's workspace alone, so who
    was asking never entered the decision. Eligibility is per actor.
    """
    world, lawful = _eligible_world()

    outcome = await _select(
        monkeypatch,
        world=world,
        references=[_selection(lawful)],
        actor=_sibling_project_actor(),
    )

    _assert_absent(
        outcome, AUTHORISED_MARKER, "an actor entitled only to a sibling project"
    )
    _assert_source_absent(
        outcome, lawful, "an actor entitled only to a sibling project"
    )


@pytest.mark.asyncio
async def test_a_rootless_worker_principal_admits_no_selected_clause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R17's shape, pinned here too: a fabricated principal has no roles.

    Canonical authorisation denies it, so its universe is empty and it may
    select nothing. G-A11 adds no permissive path for such an actor; closing
    R17 means PASSING the real actor, never widening the fake one.
    """
    world, lawful = _eligible_world()
    draft = _draft()

    outcome = await _select(
        monkeypatch,
        world=world,
        references=[_selection(lawful)],
        draft=draft,
        actor=_rootless_worker(draft),
    )

    _assert_absent(outcome, AUTHORISED_MARKER, "a rootless worker principal")
    _assert_source_absent(outcome, lawful, "a rootless worker principal")


# ---------------------------------------------------------------------------
# RED 15-16: identifiers are opaque, and the UI is not a control
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_foreign_tenant_clause_with_the_same_identifier_cannot_be_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 15. A client-supplied id names an object; it proves nothing about it.

    Both tenants hold a chunk under the same identifier. Canonical ownership
    decides, never the opaque string the caller sent.
    """
    world, lawful = _eligible_world()
    world.instrument(
        document_id="doc-foreign",
        organization_id="org-B",
        project_id="proj-B",
        contract_id="MAIN",
    )
    world.selectable_clause(
        document_id="doc-foreign",
        text=FOREIGN_TENANT_MARKER,
        organization_id="org-B",
        project_id="proj-B",
        vector_id=lawful,  # the SAME identifier as the lawful chunk
    )

    outcome = await _select(monkeypatch, world=world, references=[_selection(lawful)])

    _assert_absent(outcome, FOREIGN_TENANT_MARKER, "a colliding foreign-tenant chunk")
    _assert_present(outcome, AUTHORISED_MARKER, "a colliding foreign-tenant chunk")


@pytest.mark.asyncio
async def test_a_direct_api_selection_the_ui_would_never_offer_is_still_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 16. Frontend filtering is convenience; the server re-resolves.

    The caller sends the parent DOCUMENT id of a non-applicable instrument as a
    clause selection - a shape the evidence-search endpoint never returns. The
    hydrator's own fallback happily resolves a document id to its first chunk,
    so the only thing between that and a pleading is canonical authority.
    """
    world, lawful = _eligible_world()
    world.instrument(document_id="doc-not-applied", project_id=None, contract_id=None)
    world.selectable_clause(
        document_id="doc-not-applied", text=NO_APPLICABILITY_MARKER
    )

    outcome = await _select(
        monkeypatch,
        world=world,
        references=[_selection("doc-not-applied")],
    )

    _assert_absent(outcome, NO_APPLICABILITY_MARKER, "a hand-made API selection")
    _assert_source_absent(outcome, "doc-not-applied", "a hand-made API selection")
    await _assert_lawful_selection_still_works(
        monkeypatch, world, lawful, "a hand-made API selection"
    )


@pytest.mark.asyncio
async def test_client_supplied_provenance_cannot_authorise_a_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A selection that DECLARES itself authoritative is still only a request.

    The client controls ``metadata``. If a stored ``verification_status`` or
    ``source_origin`` could stand in for a resolution, the whole boundary would
    be one JSON field wide.
    """
    world, lawful = _eligible_world()
    world.instrument(document_id="doc-declared", project_id=None, contract_id=None)
    selected = world.selectable_clause(
        document_id="doc-declared", text=NO_APPLICABILITY_MARKER
    )

    outcome = await _select(
        monkeypatch,
        world=world,
        references=[
            _selection(
                selected,
                metadata={
                    "verification_status": "verified",
                    "source_origin": "authoritative:contract_clauses",
                    "authoritative_sha256": "deadbeef",
                },
            )
        ],
    )

    _assert_absent(outcome, NO_APPLICABILITY_MARKER, "client-declared provenance")
    _assert_source_absent(outcome, "doc-declared", "client-declared provenance")
    await _assert_lawful_selection_still_works(
        monkeypatch, world, lawful, "client-declared provenance"
    )


# ---------------------------------------------------------------------------
# RED 17-18: an empty answer and an unanswerable one, neither of which widens
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_selected_clause_lookup_carries_the_canonical_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Containment BEFORE the read, not filtering after it.

    Removing an unauthorised row after it has been read - and after its text has
    reached an assembled context, a prompt or a hash - is not containment. Every
    query issued against a clause store must already carry the eligible-document
    fence, so an ineligible chunk is never in memory to be forgotten about.
    """
    world, lawful = _eligible_world()

    outcome = await _select(monkeypatch, world=world, references=[_selection(lawful)])

    queries = _evidence_lookups(outcome.clause_queries())
    assert queries, "no clause-store evidence lookup was issued at all"
    unfenced = [
        query
        for query in queries
        if not isinstance((query or {}).get("document_id"), dict)
        or "$in" not in (query or {}).get("document_id", {})
    ]
    assert not unfenced, (
        "clause-store lookups were issued without the canonical eligible-document "
        f"fence: {unfenced}. The universe must be part of the QUERY - a fence "
        "applied to the result is a filter, and a filter is one refactor away "
        "from being dropped."
    )


@pytest.mark.asyncio
async def test_a_wholly_rejected_selection_never_broadens_the_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 17. Nothing applicable is an ANSWER, never a reason to search wider.

    The one selected reference is ineligible and there is no lawful companion.
    The request fails closed, and the lookups that were issued still carry the
    fence: no second, broader attempt is made to find the row "somehow".
    """
    world = _World()
    world.instrument(document_id="doc-none", project_id=None, contract_id=None)
    selected = world.selectable_clause(
        document_id="doc-none", text=NO_APPLICABILITY_MARKER
    )

    outcome = await _select(monkeypatch, world=world, references=[_selection(selected)])

    _assert_absent(outcome, NO_APPLICABILITY_MARKER, "a wholly rejected selection")
    for query in _evidence_lookups(outcome.clause_queries()):
        document_id = (query or {}).get("document_id")
        assert isinstance(document_id, dict) and "$in" in document_id, (
            "a clause-store lookup was retried without the canonical fence after "
            f"the selection was refused: {query}"
        )


@pytest.mark.asyncio
async def test_an_authority_failure_issues_no_clause_lookup_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 18. Unanswerable is not empty, and it is certainly not permissive.

    When the canonical universe cannot be computed, the only safe number of
    clause rows to read is zero. Asserted on the collections' recorded queries
    rather than inferred from a missing marker, so "it failed for another
    reason" cannot pass as containment.
    """
    world, lawful = _eligible_world()

    def _break_applicability(db: _Database) -> None:
        db[APPLICABILITY].find_raises = RuntimeError("applicability store unavailable")

    outcome = await _select(
        monkeypatch,
        world=world,
        references=[_selection(lawful)],
        mutate_db=_break_applicability,
    )

    _assert_absent(outcome, AUTHORISED_MARKER, "an unresolvable authority")
    assert not _evidence_lookups(outcome.clause_queries()), (
        "clause stores were queried even though eligibility could not be "
        f"resolved: {outcome.clause_queries()}. There is no partial answer to "
        "give and no broader query that would be safer."
    )


# ---------------------------------------------------------------------------
# RED 12-14: the persistence tail, which is what makes this material
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_persisted_arbitration_version_never_records_an_ineligible_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 12. The sealed row is the surface that matters.

    ``arbitration_draft_versions.source_ledger`` is hashed by
    ``immutable_version_hash`` and rendered by the DOCX/PDF exporter, and
    ``arbitration_generation_runs.source_ids`` records the same identities. A
    selection that reaches those has reached the tribunal.
    """
    world, _lawful = _eligible_world()
    world.instrument(document_id="doc-sealed", project_id="proj-B", contract_id="MAIN")
    selected = world.selectable_clause(
        document_id="doc-sealed", text=SIBLING_PROJECT_MARKER, project_id="proj-A"
    )

    run = await _generate(
        monkeypatch, world=world, selected=[_selection(selected)]
    )

    _assert_absent(run, SIBLING_PROJECT_MARKER, "a persisted ineligible selection")
    _assert_source_absent(run, selected, "a persisted ineligible selection")


@pytest.mark.asyncio
async def test_a_valid_selection_survives_into_the_persisted_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The persistence tail still carries lawful selected evidence."""
    world, lawful = _eligible_world()

    run = await _generate(monkeypatch, world=world, selected=[_selection(lawful)])

    assert not run.rejected, (
        f"a lawful selection blocked generation: {getattr(run.error, 'detail', None)!r}"
    )
    _assert_present(run, AUTHORISED_MARKER, "a lawful persisted selection")
    assert lawful in run.provenance()


@pytest.mark.asyncio
async def test_add_references_never_persists_an_ineligible_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 13. A refused selection leaves NOTHING behind.

    Not a row, not an identifier, not a "requested" audit crumb inside the
    evidence store. ``arbitration_selected_references`` is the draft's standing
    evidence - every later ``build`` rebuilds the ledger from it - so a rejected
    id living there is a leak with a delay fuse, not a record.
    """
    from rbac_backend.models.arbitration_drafting import (
        ArbitrationSelectedReferenceCreate,
    )

    world, _lawful = _eligible_world()
    world.instrument(document_id="doc-refused", project_id=None, contract_id=None)
    selected = world.selectable_clause(
        document_id="doc-refused", text=NO_APPLICABILITY_MARKER
    )
    draft = _draft()
    db = world.database([draft])
    monkeypatch.setattr(
        arbitration_context, "EvidenceGraphService", _DisabledEvidenceGraph
    )
    service = ArbitrationDraftingService(db)

    with pytest.raises(HTTPException):
        await service.add_references(
            str(draft["_id"]),
            [
                ArbitrationSelectedReferenceCreate(
                    source_type="clause",
                    source_id=selected,
                    label="Clause selected by the user",
                )
            ],
            _project_actor(),
        )

    stored = repr(db[SELECTED_REFERENCES].rows)
    assert NO_APPLICABILITY_MARKER not in stored
    assert selected not in stored and "doc-refused" not in stored, (
        "a refused selection was persisted into arbitration_selected_references: "
        f"{stored}. Every later build rebuilds the source ledger from that "
        "collection, so a stored rejection becomes evidence at the next run."
    )


@pytest.mark.asyncio
async def test_create_draft_never_persists_an_ineligible_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 13, at the other write door. Draft creation carries selections too."""
    from rbac_backend.models.arbitration_drafting import (
        ArbitrationDraftCreate,
        ArbitrationSelectedReferenceCreate,
    )

    world, _lawful = _eligible_world()
    world.instrument(document_id="doc-at-create", project_id=None, contract_id=None)
    selected = world.selectable_clause(
        document_id="doc-at-create", text=NO_APPLICABILITY_MARKER
    )
    db = world.database([])
    monkeypatch.setattr(
        arbitration_context, "EvidenceGraphService", _DisabledEvidenceGraph
    )
    service = ArbitrationDraftingService(db)

    with pytest.raises(HTTPException):
        await service.create_draft(
            ArbitrationDraftCreate(
                organization_id="org-A",
                project_id="proj-A",
                draft_type="statement_of_claim",
                party_role="claimant",
                title="Claim for extension of time",
                selected_references=[
                    ArbitrationSelectedReferenceCreate(
                        source_type="clause",
                        source_id=selected,
                        label="Clause selected by the user",
                    )
                ],
            ),
            _project_actor(),
        )

    stored = repr(db[SELECTED_REFERENCES].rows)
    assert NO_APPLICABILITY_MARKER not in stored
    assert "doc-at-create" not in stored, (
        f"a refused selection was persisted at draft creation: {stored}"
    )


@pytest.mark.asyncio
async def test_a_stored_selection_is_revalidated_when_authority_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 14. Yesterday's answer is not today's authority.

    The selection is made through the REAL ``add_references`` while it is still
    lawful, so what sits in ``arbitration_selected_references`` is the hydrated
    row production would store - authoritative provenance, verified status and
    the clause text of the day. The instrument is then WITHDRAWN, and the draft
    is regenerated.

    Re-resolution has to happen at the new run: a stored row asserting its own
    authority is precisely the thing that must not be believed. The stored row
    itself is immutable history and is left alone (same carried owner decision
    as G-A5...G-A10), so the assertion is on what this generation SEALED.
    """
    from rbac_backend.models.arbitration_drafting import (
        ArbitrationSelectedReferenceCreate,
    )

    world, lawful = _eligible_world()

    async def _select_then_withdraw(db: _Database, service: Any) -> None:
        await service.add_references(
            "draft-1",
            [
                ArbitrationSelectedReferenceCreate(
                    source_type="clause",
                    source_id=lawful,
                    label="Clause selected by the user",
                )
            ],
            _project_actor(),
        )
        stored = repr(db[SELECTED_REFERENCES].rows)
        assert AUTHORISED_MARKER in stored and "authoritative:" in stored, (
            "the selection under test was not stored in the hydrated form "
            f"production persists, so the re-run proves nothing: {stored}"
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

    run = await _generate(
        monkeypatch,
        world=world,
        selected=[],
        before_generate=_select_then_withdraw,
    )

    why = "a stored selection whose instrument was withdrawn"
    _assert_absent(run, AUTHORISED_MARKER, why, surfaces=run.sealed_surfaces())
    assert lawful not in {str(item) for item in (run.run.get("source_ids") or [])}, (
        f"{why}: the withdrawn instrument was still recorded as a contributor "
        "in arbitration_generation_runs.source_ids"
    )
    for row in run.version.get("source_ledger") or []:
        assert str(row.get("source_id")) != lawful, (
            f"{why}: it survived in the sealed arbitration_draft_versions row"
        )


# ---------------------------------------------------------------------------
# Structural pin - the door itself, not one of its outcomes
# ---------------------------------------------------------------------------


def _reachable_from(class_source: str, entry: str) -> Tuple[set, set]:
    """Method names and called names reachable from ``entry`` within the class.

    Walking the call graph rather than the whole module matters: the automatic
    contract-evidence path already consumes the canonical universe, so a
    module-wide search would pass no matter what the SELECTED-reference path
    does. Only what this door actually calls counts.
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


def test_the_selected_reference_door_consumes_the_canonical_universe() -> None:
    """The positive half of the pin: the fence must be the CANONICAL one.

    Its negative twin below forbids the ad-hoc alternatives. Both are needed:
    deleting clause support entirely would satisfy a "does not do X" assertion,
    and re-implementing applicability locally would satisfy a "does authority"
    one. A fourth Contract authority implementation is the failure mode this
    programme keeps finding.
    """
    source = inspect.getsource(ArbitrationContextBuilder)
    _methods, called = _reachable_from(source, "rehydrate_selected_references")
    assert "resolve_authorized_project_universe" in called, (
        "the selected-reference hydrator does not reach "
        "resolve_authorized_project_universe. A user's selection must be "
        "INTERSECTED with the canonical eligible universe - the same one the "
        "automatic path consumes - never authorised by the row it names."
    )


def test_the_selected_reference_door_does_not_reimplement_applicability() -> None:
    """No local re-derivation of eligibility beside the canonical resolver.

    Reading ``contract_document_applicability`` (or its events) from this file
    would be a second answer to a question that already has one, and the two
    would drift.
    """
    tree = ast.parse(inspect.getsource(arbitration_context))
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
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    for forbidden in (APPLICABILITY, APPLICABILITY_EVENTS):
        assert forbidden not in literals, (
            f"{forbidden!r} is read directly by the arbitration context module. "
            "Contract eligibility has exactly one implementation; a second one "
            "here would drift from it silently."
        )
    assert "search_contracts" not in called, (
        "generic contract search is a named forbidden authority source in the "
        "frozen model, and G-A9 removed it from this module."
    )
