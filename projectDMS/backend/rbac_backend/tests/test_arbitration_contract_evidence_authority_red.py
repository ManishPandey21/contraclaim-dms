"""G-A9 / R19: arbitration pleadings may only cite AUTHORISED contract clauses.

``ArbitrationContextBuilder.build`` assembled its contract evidence through
``_contract_search_sources``, which called the **generic**
``ContractService.search_contracts`` and turned every returned chunk into a
``source_type="clause"`` row of the arbitration SOURCE LEDGER. Generic search is
explicitly not evidence mode:

* it resolves no applicability - an instrument catalogued in the organisation
  but never APPLIED to the project is indistinguishable from one that governs
  it;
* it resolves no projection currency - a projection a revision behind, or
  PENDING, or FAILED, contributes exactly like a current one;
* it applies no positive per-document publication authority - the subtractive
  helper it does reach fails OPEN on an identifier it cannot resolve, so an
  ORPHAN chunk whose canonical ``Document`` no longer resolves is admitted;
* and it spends its candidate limit BEFORE any of those exist, so ineligible
  high-scoring chunks starve an applicable clause out of the window entirely.

This is the same defect class G-A7 closed for drafting v2 and G-A8 closed for
the LangGraph letter pipeline, in a third engine with its own persistence tail.
That tail is what makes it material rather than transient. The source ledger is:

* rendered into the pleading by ``ArbitrationDraftGenerator`` (and by the LLM
  generator when one is configured), so clause text becomes draft text;
* hashed into ``stable_generation_input_hash``, which decides whether a run is
  reused;
* written verbatim into ``arbitration_draft_versions.source_ledger``, a row
  sealed by ``immutable_version_hash``;
* written as ``arbitration_generation_runs.source_ids``;
* and exported from that version by the DOCX/PDF exporter.

Unauthorised clause TEXT and unauthorised clause PROVENANCE therefore become
durable, hash-sealed pleading state. Arbitration is the most legally exposed
consumer in the tree, which is why the boundary is asserted on the persisted
artefact and not only on the returned context.

The accepted universe is positive and per contract::

      applicable to (project_id, contract_id) at the query mode
    & canonical Document positively resolvable
    & publication-consumable
    & projection-current

resolved by ``ContractScopeResolver`` for an actor who is canonically entitled
to the workspace. The context builder must CONSUME that; it may not reconstruct
it, and it may not fall back to generic search when it is empty or unavailable.

**Recorded gap, carried from G-A7/G-A8 and NOT silently narrowed.** The
arbitration draft does carry an optional ``contract_id``, but it is free text
that no code path validates or joins against ``contract_document_applicability``
- the case form copies it onto the draft and only the register queries
(``_scope_query``) ever read it. Treating it as canonical Contract Master
identity would be inventing an identity mapping, and keying evidence on it would
silently empty the ledger wherever the two labels differ. So this containment is
**PROJECT-EVIDENCE CONTAINMENT**: the union over the contracts that have
applicability in the AUTHORISED project. A clause governing sibling contract
``OTHER`` may still ground a pleading about ``MAIN``. That residual is bounded
and pinned at the resolver
(:func:`test_the_canonical_universe_separates_two_contracts`) so the identity
machinery stays exact; closing it needs the arbitration ``contract_id`` to be
made canonical, which is an owner decision.

Every containment test asserts on the surfaces the material actually reaches:

1. the returned context's ``source_ledger`` - text AND provenance;
2. the persisted ``arbitration_draft_versions`` row, including its immutable
   ``source_ledger`` and the rendered ``full_markdown``;
3. the persisted ``arbitration_generation_runs.source_ids``.

Text and PROVENANCE are asserted separately: an ineligible ``document_id`` may
not survive in the ledger merely because its clause text was removed.

The positives are load-bearing. ``processing_status == "failed"`` is OPERATIONAL
- extraction broke, which is not a verdict about the content - so a
last-known-good instrument must still contribute, and an applicable,
publication-consumable, projection-current clause must still reach the pleading.
A containment that empties contract evidence is an outage, not a boundary.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pytest
from bson import ObjectId

from rbac_backend.models.contract_document import CurrentState
from rbac_backend.services.arbitration_drafting import context as arbitration_context
from rbac_backend.services.arbitration_drafting.context import ArbitrationContextBuilder
from rbac_backend.services.arbitration_drafting.service import ArbitrationDraftingService
from rbac_backend.services.contract_scope_resolver import (
    ContractScopeResolutionError,
    resolve_authorized_project_universe,
)

# --- unique markers -------------------------------------------------------
#
# Every marker is unique, so an assertion can never pass because the string
# happened to be absent for an unrelated reason.

AUTHORISED_MARKER = "ARBITRATION_PROJECT_A_CONTRACT_AUTHORISED_20260831"
SIBLING_PROJECT_MARKER = "ARBITRATION_PROJECT_B_CONTRACT_CONFIDENTIAL_20260831"
NO_APPLICABILITY_MARKER = "ARBITRATION_CATALOGUE_ONLY_CONFIDENTIAL_20260831"
WITHDRAWN_MARKER = "ARBITRATION_WITHDRAWN_INSTRUMENT_CONFIDENTIAL_20260831"
SUPERSEDED_MARKER = "ARBITRATION_SUPERSEDED_INSTRUMENT_CONFIDENTIAL_20260831"
HUMAN_REVIEW_MARKER = "ARBITRATION_HUMAN_REVIEW_CONFIDENTIAL_20260831"
DUPLICATE_MARKER = "ARBITRATION_DUPLICATE_CLAUSE_CONFIDENTIAL_20260831"
DELETED_MARKER = "ARBITRATION_DELETED_CLAUSE_CONFIDENTIAL_20260831"
OPERATIONAL_FAILED_MARKER = "ARBITRATION_OPERATIONAL_FAILED_LAST_KNOWN_GOOD_20260831"
ORPHAN_MARKER = "ARBITRATION_ORPHAN_CLAUSE_CONFIDENTIAL_20260831"
LYING_CLAUSE_MARKER = "ARBITRATION_LYING_CLAUSE_CONFIDENTIAL_20260831"
STALE_PROJECTION_MARKER = "ARBITRATION_STALE_PROJECTION_CONFIDENTIAL_20260831"
PENDING_PROJECTION_MARKER = "ARBITRATION_PENDING_PROJECTION_CONFIDENTIAL_20260831"
FAILED_PROJECTION_MARKER = "ARBITRATION_FAILED_PROJECTION_CONFIDENTIAL_20260831"
CAPACITY_FILLER_MARKER = "ARBITRATION_CAPACITY_FILLER_CONFIDENTIAL_20260831"
CAPACITY_ELIGIBLE_MARKER = "ARBITRATION_CAPACITY_ELIGIBLE_AUTHORISED_20260831"
LEGACY_SEARCH_MARKER = "ARBITRATION_LEGACY_GENERIC_SEARCH_CONFIDENTIAL_20260831"

#: The one query term every seeded clause shares, so relevance is never the
#: reason a row is absent. Every seeded clause scores; only authority may
#: remove one.
QUERY_TERM = "extension"

APPLICABILITY = "contract_document_applicability"
APPLICABILITY_EVENTS = "contract_document_applicability_events"
CONTRACT_DOCUMENTS = "contract_documents"
CONTRACT_CLAUSES = "contract_clauses"


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
            if "$regex" in expected:
                import re

                if not isinstance(actual, str) or not re.search(
                    str(expected["$regex"]), actual, re.IGNORECASE
                ):
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
        #: Every filter this collection was asked for. Used to prove that an
        #: authority failure issues NO broad retrieval, rather than issuing one
        #: and discarding the result.
        self.queries: List[Optional[Dict[str, Any]]] = []
        self.find_raises: Optional[Exception] = None

    async def find_one(self, query=None, projection=None, sort=None, **_k: Any):
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

    def clause(
        self,
        *,
        document_id: str,
        text: str,
        org_id: str = "org-A",
        project_id: str = "proj-A",
        clause_no: str = "8.4",
        contract_id: Optional[str] = None,
        uid: Optional[str] = None,
    ) -> str:
        """A clause ROW. Its ``org_id``/``project_id`` are ingest provenance.

        Several tests deliberately stamp a row with the arbitration workspace
        while its canonical instrument says otherwise - that is the
        lying-provenance case, and the row must not authorise itself.
        """
        clause_uid = uid or self._next("clause")
        row: Dict[str, Any] = {}
        if contract_id is not None:
            # A derived row asserting contract identity. It is provenance, and
            # it must not be able to authorise anything.
            row["contract_id"] = contract_id
        self.clauses.append(
            {
                **row,
                "clause_uid": clause_uid,
                "org_id": org_id,
                "project_id": project_id,
                "is_current": True,
                "is_authorised_for_ai": True,
                "clause_no": clause_no,
                "clause_title": f"Extension of time {clause_uid}",
                "cleaned_text": (
                    f"The Contractor shall be entitled to an {QUERY_TERM} of time. {text}"
                ),
                "page_start": 12,
                "page_end": 13,
                "document_id": document_id,
                "document_type": "GCC",
            }
        )
        return clause_uid

    def database(self, drafts: Optional[List[Dict[str, Any]]] = None) -> _Database:
        return _Database(
            {
                "arbitration_drafts": list(drafts or []),
                "documents": self.documents,
                CONTRACT_DOCUMENTS: self.contract_documents,
                APPLICABILITY: self.applicability,
                APPLICABILITY_EVENTS: self.applicability_events,
                CONTRACT_CLAUSES: self.clauses,
            }
        )


# ---------------------------------------------------------------------------
# The generic door, modelled faithfully rather than conveniently
# ---------------------------------------------------------------------------


class _LegacyGenericSearch:
    """A faithful model of the generic ``search_contracts`` seam.

    Faithful means: it reproduces the two properties that make generic search
    the wrong door for evidence, and NOT more than that, so a RED here fails for
    a reason production actually has.

    1. **Row provenance is the only scope.** Generic search's match stage is
       ``{organization_id, project_id}`` taken from the REQUEST - which the
       arbitration builder fills from the DRAFT's workspace - applied to the
       DERIVED rows' own stamps. It knows nothing of applicability, lifecycle or
       projection currency, so any row stamped with the drafting workspace is
       admitted however its canonical instrument answers.
    2. **Publication authority is already partly contained, subtractively.**
       ``_assemble_results`` calls the shared ``blocked_document_ids`` on the
       candidate set before fusion, so a human-review / duplicate / deleted
       document IS excluded here today. The real helper is called rather than
       re-implemented, which also preserves its documented fail-OPEN behaviour
       on an identifier it cannot resolve - the orphan hole.

    One extra chunk is always returned ahead of the rows: a clause from a
    document with no canonical presence in this world at all. Generic search has
    no notion that could exclude it, so its marker is a direct witness that the
    broad door was open.
    """

    db: Any = None
    calls: List[Any] = []

    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    async def search_contracts(self, request: Any, *_a: Any, **_k: Any):
        from rbac_backend.services.publication_policy import blocked_document_ids

        type(self).calls.append(request)
        db = type(self).db
        rows = [
            row
            for row in db[CONTRACT_CLAUSES].rows
            if str(row.get("org_id")) == str(request.organization_id)
            and str(row.get("project_id")) == str(request.project_id)
        ]
        blocked = await blocked_document_ids(
            db, [row.get("document_id") for row in rows]
        )
        rows = [row for row in rows if str(row.get("document_id")) not in blocked]

        results = [
            SimpleNamespace(
                document_id="doc-generic-search",
                upload_id="doc-generic-search",
                clause_number="1.1",
                clause_title="Generic search residue",
                text=(
                    f"An {QUERY_TERM} of time clause served by generic search. "
                    f"{LEGACY_SEARCH_MARKER}"
                ),
                page_numbers=[1],
                page=1,
                score=0.99,
                file_name="contract.pdf",
            )
        ]
        for index, row in enumerate(rows):
            results.append(
                SimpleNamespace(
                    document_id=row.get("document_id"),
                    upload_id=row.get("document_id"),
                    clause_number=row.get("clause_no"),
                    clause_title=row.get("clause_title"),
                    text=row.get("cleaned_text"),
                    page_numbers=[row.get("page_start")],
                    page=row.get("page_start"),
                    score=0.9 - index * 0.001,
                    file_name="contract.pdf",
                )
            )
        # Generic search bounds its own window LONG before any authority exists:
        # `limit=5` is what the arbitration caller asks for.
        return SimpleNamespace(results=results[: int(getattr(request, "limit", 5) or 5)])


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
# Drafts and runs
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
        "title": f"Claim for {QUERY_TERM} of time",
        "case_details": {},
        "relief_sought": f"An {QUERY_TERM} of time and associated costs.",
        "manual_facts": "",
        "arbitration_clause": "Clause 20 arbitration.",
        "include_register_sources": True,
        "excluded_register_ids": [],
        "status": "draft",
    }


class _Ledger:
    """Every place arbitration contract evidence can come to rest."""

    def __init__(self, context: Dict[str, Any], db: _Database) -> None:
        self.context = context
        self.db = db

    @property
    def rows(self) -> List[Dict[str, Any]]:
        return list(self.context.get("source_ledger") or [])

    def clause_rows(self) -> List[Dict[str, Any]]:
        return [row for row in self.rows if row.get("source_type") == "clause"]

    def text(self) -> str:
        return repr(self.context)

    def provenance(self) -> set:
        ids: set = set()
        for row in self.rows:
            ids.add(str(row.get("source_id")))
            metadata = row.get("metadata") or {}
            for key in ("document_id", "upload_id"):
                if metadata.get(key):
                    ids.add(str(metadata[key]))
        return ids


async def _build_context(
    monkeypatch: pytest.MonkeyPatch,
    *,
    world: _World,
    draft: Optional[Dict[str, Any]] = None,
    actor: Any = None,
    mutate_db: Any = None,
) -> _Ledger:
    """Drive the REAL ``ArbitrationContextBuilder.build``, as the service does."""
    draft = draft or _draft()
    db = world.database([draft])
    if mutate_db is not None:
        mutate_db(db)
    _LegacyGenericSearch.calls = []
    _LegacyGenericSearch.db = db

    # The generic door, left OPEN and answering. Closing it in production is the
    # deliverable; stubbing it shut here would prove nothing. ``raising=False``
    # because the closed state is exactly the absence of the name - the
    # structural pin asserts that separately, and a patch that required the
    # symbol would turn the fix into a collection error.
    monkeypatch.setattr(
        arbitration_context, "ContractService", _LegacyGenericSearch, raising=False
    )
    monkeypatch.setattr(
        arbitration_context, "EvidenceGraphService", _DisabledEvidenceGraph
    )

    builder = ArbitrationContextBuilder(db)
    context = await builder.build(draft, [], [], [], actor or _project_actor())
    return _Ledger(context, db)


class _Run:
    """The persisted arbitration artefacts one generation writes."""

    def __init__(self, db: _Database, version: Dict[str, Any], run: Dict[str, Any]) -> None:
        self.db = db
        self.version = version
        self.run = run

    def surfaces(self) -> Dict[str, str]:
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
            metadata = row.get("metadata") or {}
            for key in ("document_id", "upload_id"):
                if metadata.get(key):
                    ids.add(str(metadata[key]))
        return ids


async def _generate(
    monkeypatch: pytest.MonkeyPatch,
    *,
    world: _World,
    draft: Optional[Dict[str, Any]] = None,
    actor: Any = None,
) -> _Run:
    """Drive the REAL service generation, through to the sealed version row."""
    from rbac_backend.models.arbitration_drafting import ArbitrationGenerateRequest

    draft = draft or _draft()
    db = world.database([draft])
    _LegacyGenericSearch.calls = []
    _LegacyGenericSearch.db = db
    monkeypatch.setattr(
        arbitration_context, "ContractService", _LegacyGenericSearch, raising=False
    )
    monkeypatch.setattr(
        arbitration_context, "EvidenceGraphService", _DisabledEvidenceGraph
    )

    service = ArbitrationDraftingService(db)
    await service.generate(
        str(draft["_id"]), ArbitrationGenerateRequest(), actor or _project_actor()
    )
    version = await db["arbitration_draft_versions"].find_one(
        {"draft_id": str(draft["_id"])}, sort=[("version", -1)]
    )
    run = await db["arbitration_generation_runs"].find_one(
        {"draft_id": str(draft["_id"])}
    )
    return _Run(db, version or {}, run or {})


def _assert_absent(ledger: _Ledger, marker: str, why: str) -> None:
    assert marker not in ledger.text(), (
        f"{why}: the marker {marker!r} reached the arbitration source ledger. A "
        "clause row's own org_id/project_id is ingest provenance, not legal "
        "applicability - only the canonical Contract Master universe may admit "
        "a clause to a pleading."
    )


def _assert_present(ledger: _Ledger, marker: str, why: str) -> None:
    assert marker in ledger.text(), (
        f"{why}: the authorised marker {marker!r} reached NONE of the "
        "arbitration context. Containment that empties contract evidence is an "
        "outage, not a boundary."
    )


def _assert_document_absent(ledger: _Ledger, document_id: str, why: str) -> None:
    assert document_id not in ledger.provenance(), (
        f"{why}: the ineligible document id {document_id!r} survived in the "
        "arbitration source ledger's provenance. Removing the text while "
        "keeping the id still records an unauthorised source as a contributor "
        "to a pleading, and the ledger is sealed into an immutable version row."
    )


def _assert_generic_search_unused(why: str) -> None:
    assert not _LegacyGenericSearch.calls, (
        f"{why}: generic ContractService.search_contracts was called from the "
        "arbitration evidence path. Generic search resolves no applicability, "
        "no projection currency and no positive publication authority, and it "
        "spends its candidate limit before any of them exist."
    )


# ---------------------------------------------------------------------------
# The canonical universe itself - the machinery the builder must consume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_canonical_universe_separates_two_contracts() -> None:
    """Exact ``(project_id, contract_id)`` identity, not ``contract_id`` alone.

    RED 2 ("wrong contract, same project") is asserted HERE and not at the
    builder. The arbitration draft carries a ``contract_id``, but nothing
    validates or joins it against ``contract_document_applicability`` - it is a
    free-text label the case form copies onto the draft, read only by the
    register scope query. Keying evidence on it would be inventing an identity
    mapping and would silently empty the ledger wherever the two labels differ.
    Marking the consumer-level case OWNER DECISION / NOT ENFORCEABLE AT THE
    CURRENT REQUEST CONTRACT is only honest if the identity machinery underneath
    is still exact, which is what this pins.
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
    assert universe.eligible_document_ids == {"doc-A-MAIN", "doc-A-OTHER"}
    assert universe.contract_ids == ("MAIN", "OTHER")


# ---------------------------------------------------------------------------
# RED 16 (positive) - the boundary must not be a switch-off
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_eligible_contract_clause_still_grounds_the_pleading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World()
    world.instrument(document_id="doc-A-MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)

    ledger = await _build_context(monkeypatch, world=world)

    _assert_present(ledger, AUTHORISED_MARKER, "an applicable, current instrument")
    assert ledger.clause_rows(), "no clause source reached the arbitration ledger at all"


@pytest.mark.asyncio
async def test_an_operationally_failed_document_keeps_its_last_known_good_clause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Model B. A broken pipeline is not a verdict about the content."""
    world = _World()
    world.instrument(document_id="doc-A-MAIN", processing_status="failed")
    world.clause(document_id="doc-A-MAIN", text=OPERATIONAL_FAILED_MARKER)

    ledger = await _build_context(monkeypatch, world=world)

    _assert_present(
        ledger,
        OPERATIONAL_FAILED_MARKER,
        "an OPERATIONAL processing failure hid consumable last-known-good evidence",
    )


@pytest.mark.asyncio
async def test_a_global_actor_keeps_the_breadth_canonical_authorisation_gives_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolved, never named. The actor's breadth comes from authorisation."""
    world = _World()
    world.instrument(document_id="doc-A-MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)

    ledger = await _build_context(monkeypatch, world=world, actor=_global_actor())

    _assert_present(ledger, AUTHORISED_MARKER, "a global actor lost lawful evidence")


# ---------------------------------------------------------------------------
# RED 1 / RED 9 - scope and lying provenance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_sibling_project_clause_cannot_reach_the_pleading(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 1. Same contract label, different project, higher relevance."""
    world = _World()
    world.instrument(document_id="doc-A-MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.instrument(document_id="doc-B-MAIN", project_id="proj-B", contract_id="MAIN")
    # Stamped with the ARBITRATION workspace. The row is lying about where it
    # belongs, which is exactly what ingest provenance can do.
    world.clause(document_id="doc-B-MAIN", text=SIBLING_PROJECT_MARKER)

    ledger = await _build_context(monkeypatch, world=world)

    _assert_absent(ledger, SIBLING_PROJECT_MARKER, "a SIBLING project's clause")
    _assert_document_absent(ledger, "doc-B-MAIN", "a SIBLING project's clause")
    _assert_present(ledger, AUTHORISED_MARKER, "the lawful clause beside the unlawful one")
    _assert_generic_search_unused("a sibling-project clause")


@pytest.mark.asyncio
async def test_a_clause_row_cannot_authorise_itself_by_asserting_contract_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 9. The row claims project AND contract; canonical fact disagrees."""
    world = _World()
    world.instrument(document_id="doc-A-MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER, contract_id="MAIN")
    world.instrument(document_id="doc-lying", project_id="proj-B", contract_id="MAIN")
    world.clause(
        document_id="doc-lying",
        text=LYING_CLAUSE_MARKER,
        project_id="proj-A",
        contract_id="MAIN",
    )

    ledger = await _build_context(monkeypatch, world=world)

    _assert_absent(ledger, LYING_CLAUSE_MARKER, "a clause row asserting contract identity")
    _assert_document_absent(ledger, "doc-lying", "a clause row asserting contract identity")
    _assert_present(ledger, AUTHORISED_MARKER, "the lawful clause")


# ---------------------------------------------------------------------------
# RED 3 / RED 4 - applicability and lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_catalogued_instrument_with_no_applicability_cannot_contribute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 3. Catalogued in the organisation, APPLIED to nothing."""
    world = _World()
    world.instrument(document_id="doc-A-MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.instrument(document_id="doc-catalogue", project_id=None, contract_id=None)
    world.clause(document_id="doc-catalogue", text=NO_APPLICABILITY_MARKER)

    ledger = await _build_context(monkeypatch, world=world)

    _assert_absent(ledger, NO_APPLICABILITY_MARKER, "a catalogue-only instrument")
    _assert_document_absent(ledger, "doc-catalogue", "a catalogue-only instrument")
    _assert_present(ledger, AUTHORISED_MARKER, "the lawful clause")


@pytest.mark.asyncio
async def test_a_withdrawn_instrument_cannot_govern_the_current_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 4. CurrentState replays the lifecycle; WITHDRAWN closes the interval."""
    world = _World()
    world.instrument(document_id="doc-A-MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.instrument(
        document_id="doc-withdrawn", lifecycle=("APPLIED", "WITHDRAWN")
    )
    world.clause(document_id="doc-withdrawn", text=WITHDRAWN_MARKER)

    ledger = await _build_context(monkeypatch, world=world)

    _assert_absent(ledger, WITHDRAWN_MARKER, "a WITHDRAWN instrument")
    _assert_document_absent(ledger, "doc-withdrawn", "a WITHDRAWN instrument")
    _assert_present(ledger, AUTHORISED_MARKER, "the lawful clause")


@pytest.mark.asyncio
async def test_a_superseded_instrument_cannot_govern_the_current_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World()
    world.instrument(document_id="doc-A-MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.instrument(
        document_id="doc-superseded", lifecycle=("APPLIED", "SUPERSEDED")
    )
    world.clause(document_id="doc-superseded", text=SUPERSEDED_MARKER)

    ledger = await _build_context(monkeypatch, world=world)

    _assert_absent(ledger, SUPERSEDED_MARKER, "a SUPERSEDED instrument")
    _assert_document_absent(ledger, "doc-superseded", "a SUPERSEDED instrument")
    _assert_present(ledger, AUTHORISED_MARKER, "the lawful clause")


# ---------------------------------------------------------------------------
# RED 5 / RED 6 / RED 8 - publication authority
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_human_review_document_cannot_contribute_a_clause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World()
    world.instrument(document_id="doc-A-MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.instrument(
        document_id="doc-human-review", processing_status="human_review_required"
    )
    world.clause(document_id="doc-human-review", text=HUMAN_REVIEW_MARKER)

    ledger = await _build_context(monkeypatch, world=world)

    _assert_absent(ledger, HUMAN_REVIEW_MARKER, "a HUMAN_REVIEW_REQUIRED document")
    _assert_document_absent(ledger, "doc-human-review", "a HUMAN_REVIEW_REQUIRED document")
    _assert_present(ledger, AUTHORISED_MARKER, "the lawful clause")


@pytest.mark.asyncio
async def test_a_duplicate_document_cannot_contribute_a_clause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World()
    world.instrument(document_id="doc-A-MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.instrument(document_id="doc-duplicate", duplicate_status="duplicate")
    world.clause(document_id="doc-duplicate", text=DUPLICATE_MARKER)

    ledger = await _build_context(monkeypatch, world=world)

    _assert_absent(ledger, DUPLICATE_MARKER, "a DUPLICATE document")
    _assert_document_absent(ledger, "doc-duplicate", "a DUPLICATE document")
    _assert_present(ledger, AUTHORISED_MARKER, "the lawful clause")


@pytest.mark.asyncio
async def test_a_deleted_document_cannot_contribute_a_clause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World()
    world.instrument(document_id="doc-A-MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.instrument(document_id="doc-deleted", lifecycle_state="deleted")
    world.clause(document_id="doc-deleted", text=DELETED_MARKER)

    ledger = await _build_context(monkeypatch, world=world)

    _assert_absent(ledger, DELETED_MARKER, "a DELETED document")
    _assert_document_absent(ledger, "doc-deleted", "a DELETED document")
    _assert_present(ledger, AUTHORISED_MARKER, "the lawful clause")


@pytest.mark.asyncio
async def test_an_orphan_clause_cannot_self_authorise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 8. The clause exists; its canonical Document does not.

    The subtractive helper fails OPEN on an id it cannot resolve, which is
    exactly the hole positive resolution closes. Clause provenance is not a
    substitute for a canonical Document.
    """
    world = _World()
    world.instrument(document_id="doc-A-MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.instrument(document_id="doc-orphan", canonical_document=False)
    world.clause(document_id="doc-orphan", text=ORPHAN_MARKER)

    ledger = await _build_context(monkeypatch, world=world)

    _assert_absent(ledger, ORPHAN_MARKER, "an ORPHAN clause")
    _assert_document_absent(ledger, "doc-orphan", "an ORPHAN clause")
    _assert_present(ledger, AUTHORISED_MARKER, "the lawful clause")


# ---------------------------------------------------------------------------
# RED 10 / RED 11 - projection currency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_stale_projection_cannot_contribute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 10. Status CURRENT, revision behind. Strict equality, never >=."""
    world = _World()
    world.instrument(document_id="doc-A-MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.instrument(
        document_id="doc-stale", classification_revision=4, projection_revision=3
    )
    world.clause(document_id="doc-stale", text=STALE_PROJECTION_MARKER)

    ledger = await _build_context(monkeypatch, world=world)

    _assert_absent(ledger, STALE_PROJECTION_MARKER, "a STALE projection")
    _assert_document_absent(ledger, "doc-stale", "a STALE projection")
    _assert_present(ledger, AUTHORISED_MARKER, "the lawful clause")


@pytest.mark.asyncio
async def test_a_pending_projection_cannot_contribute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World()
    world.instrument(document_id="doc-A-MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.instrument(document_id="doc-pending", projection_status="PENDING")
    world.clause(document_id="doc-pending", text=PENDING_PROJECTION_MARKER)

    ledger = await _build_context(monkeypatch, world=world)

    _assert_absent(ledger, PENDING_PROJECTION_MARKER, "a PENDING projection")
    _assert_document_absent(ledger, "doc-pending", "a PENDING projection")
    _assert_present(ledger, AUTHORISED_MARKER, "the lawful clause")


@pytest.mark.asyncio
async def test_a_failed_projection_cannot_contribute(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World()
    world.instrument(document_id="doc-A-MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.instrument(document_id="doc-failed-projection", projection_status="FAILED")
    world.clause(document_id="doc-failed-projection", text=FAILED_PROJECTION_MARKER)

    ledger = await _build_context(monkeypatch, world=world)

    _assert_absent(ledger, FAILED_PROJECTION_MARKER, "a FAILED projection")
    _assert_document_absent(ledger, "doc-failed-projection", "a FAILED projection")
    _assert_present(ledger, AUTHORISED_MARKER, "the lawful clause")


# ---------------------------------------------------------------------------
# RED 12 - candidate capacity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ineligible_high_scorers_cannot_starve_the_eligible_clause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 12. Containment must happen BEFORE the bound, not after it.

    Seventy ineligible rows, each stamped with the arbitration workspace and
    each scoring, plus one eligible clause seeded LAST. A post-limit filter
    returns an empty ledger; a pre-limit fence returns the eligible clause. The
    starvation presents as an outage, which is why it survived so long.
    """
    world = _World()
    world.instrument(document_id="doc-filler-source", project_id="proj-B", contract_id="MAIN")
    for index in range(70):
        world.clause(
            document_id="doc-filler-source",
            text=f"{CAPACITY_FILLER_MARKER}-{index}",
            uid=f"filler-{index:03d}",
        )
    world.instrument(document_id="doc-A-MAIN")
    world.clause(
        document_id="doc-A-MAIN", text=CAPACITY_ELIGIBLE_MARKER, uid="eligible-last"
    )

    ledger = await _build_context(monkeypatch, world=world)

    _assert_absent(ledger, CAPACITY_FILLER_MARKER, "ineligible high scorers")
    _assert_present(
        ledger,
        CAPACITY_ELIGIBLE_MARKER,
        "the ONE eligible clause was starved out of the candidate window",
    )


# ---------------------------------------------------------------------------
# RED 13 / RED 14 - empty and failure must not widen
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_valid_empty_universe_does_not_fall_back_to_generic_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 13. "Nothing applies" is an ANSWER, never a reason to search wider."""
    world = _World()
    world.instrument(document_id="doc-catalogue", project_id=None, contract_id=None)
    world.clause(document_id="doc-catalogue", text=NO_APPLICABILITY_MARKER)

    ledger = await _build_context(monkeypatch, world=world)

    assert not ledger.clause_rows(), (
        "a project with no applicable instrument still produced clause evidence"
    )
    _assert_absent(ledger, NO_APPLICABILITY_MARKER, "a valid-empty universe")
    _assert_absent(ledger, LEGACY_SEARCH_MARKER, "a valid-empty universe")
    _assert_generic_search_unused("a valid-empty universe")


@pytest.mark.asyncio
async def test_an_authority_failure_does_not_widen_and_issues_no_broad_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RED 14. "I could not tell" is not "nothing applies", and never "search wider".

    The absence of a marker is not sufficient evidence: the clause collection is
    asked whether it was queried AT ALL, so a broad query whose result happened
    to be discarded would still fail here.
    """
    world = _World()
    world.instrument(document_id="doc-A-MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)

    def _break_applicability(db: _Database) -> None:
        db[APPLICABILITY].find_raises = RuntimeError("applicability store unavailable")

    ledger = await _build_context(monkeypatch, world=world, mutate_db=_break_applicability)

    assert not ledger.clause_rows(), (
        "eligibility could not be resolved, yet clause evidence was produced anyway"
    )
    _assert_absent(ledger, AUTHORISED_MARKER, "an unresolvable universe")
    _assert_absent(ledger, LEGACY_SEARCH_MARKER, "an unresolvable universe")
    _assert_generic_search_unused("an unresolvable universe")
    assert not ledger.db[CONTRACT_CLAUSES].queries, (
        "an authority failure issued a query against contract_clauses anyway. "
        "There is no partial answer to give and no broader query that would be "
        "safer, so nothing may be queried at all."
    )


# ---------------------------------------------------------------------------
# R17 preservation - a fabricated principal stays fail-closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_rootless_worker_principal_admits_no_contract_evidence() -> None:
    """R17 is a completeness loss, not an authority leak, and stays that way.

    A principal fabricated FROM the resource being read carries no roles, so
    canonical authorisation denies it and its universe is EMPTY. G-A9 adds no
    permissive path for such an actor; closing R17 means passing the REAL actor,
    never widening the fabricated one.
    """
    world = _World()
    world.instrument(document_id="doc-A-MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    db = world.database()

    universe = await resolve_authorized_project_universe(
        db,
        _rootless_worker(_draft()),
        organization_id="org-A",
        project_id="proj-A",
        mode=CurrentState(),
    )

    assert universe.is_empty, (
        "a rootless fabricated principal resolved a NON-empty contract universe "
        "- R17 must stay fail-closed"
    )


# ---------------------------------------------------------------------------
# RED 15 - the persisted, hash-sealed pleading artefact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_persisted_arbitration_version_records_only_eligible_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The durable artefact, not just the transient context.

    ``arbitration_draft_versions`` is sealed by ``immutable_version_hash`` and
    is what the DOCX/PDF exporter renders, so an ineligible source that reaches
    it is a permanent record of an unauthorised contributor to a pleading.
    ``arbitration_generation_runs.source_ids`` is asserted alongside it because
    provenance survives there even when text does not.
    """
    world = _World()
    world.instrument(document_id="doc-A-MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.instrument(document_id="doc-B-MAIN", project_id="proj-B", contract_id="MAIN")
    world.clause(document_id="doc-B-MAIN", text=SIBLING_PROJECT_MARKER)
    world.instrument(document_id="doc-human-review", processing_status="human_review_required")
    world.clause(document_id="doc-human-review", text=HUMAN_REVIEW_MARKER)

    run = await _generate(monkeypatch, world=world)

    assert run.version, "no arbitration draft version was persisted at all"
    for surface, text in run.surfaces().items():
        for marker in (SIBLING_PROJECT_MARKER, HUMAN_REVIEW_MARKER, LEGACY_SEARCH_MARKER):
            assert marker not in text, (
                f"the marker {marker!r} survived in {surface}. The version row is "
                "sealed by immutable_version_hash and is what the exporter "
                "renders - an unauthorised clause there is permanent."
            )
    leaked = {"doc-B-MAIN", "doc-human-review", "doc-generic-search"} & run.provenance()
    assert not leaked, (
        f"the persisted arbitration artefacts recorded ineligible clause "
        f"provenance: {sorted(leaked)}"
    )
    assert AUTHORISED_MARKER in repr(run.version.get("source_ledger")), (
        "the lawful clause did not survive into the persisted version - "
        "containment that empties contract evidence is an outage"
    )


# ---------------------------------------------------------------------------
# RED 17 - the structural pin, and its positive twin
# ---------------------------------------------------------------------------


def _called_attribute_names(module: Any) -> set:
    tree = ast.parse(inspect.getsource(module))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute):
                names.add(func.attr)
            elif isinstance(func, ast.Name):
                names.add(func.id)
    return names


def test_the_arbitration_context_never_calls_generic_contract_search() -> None:
    """A permanent structural pin, by AST rather than by line number.

    Generic contract search is a named forbidden authority source in the frozen
    evidence model. This walks the module's call graph rather than matching
    substrings over a source blob, so a comment mentioning the name is not a
    failure and a renamed local variable is not a pass.
    """
    called = _called_attribute_names(arbitration_context)
    forbidden = called & {"search_contracts", "exact_clause_search"}
    assert not forbidden, (
        f"the arbitration evidence context calls generic contract search "
        f"{sorted(forbidden)}. Generic search resolves no applicability, no "
        "projection currency and no positive publication authority, and it "
        "spends its candidate limit before any of them exist - there is no "
        "point in its pipeline at which a fence would work."
    )


def test_the_arbitration_context_consumes_the_canonical_universe() -> None:
    """The positive twin: deleting contract evidence must not satisfy the pin."""
    called = _called_attribute_names(arbitration_context)
    assert "resolve_authorized_project_universe" in called, (
        "the arbitration context no longer resolves the canonical contract "
        "evidence universe. Removing contract evidence entirely satisfies the "
        "negative pin above and must not be mistaken for containment."
    )
