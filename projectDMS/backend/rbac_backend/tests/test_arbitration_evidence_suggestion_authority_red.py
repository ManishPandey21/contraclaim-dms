"""G-A12 / R21: a SUGGESTION is a disclosure, and disclosure needs authority.

G-A9 closed the AUTOMATIC arbitration contract-evidence door; G-A11 closed the
USER-SELECTED one. This file closes the third, and it is a different door class
again: nobody has chosen anything yet. The server is deciding what to OFFER.

``ArbitrationDraftingService.evidence_search`` -> ``_search_clauses`` resolved
its candidates as::

    {organization_id: <draft>, project_id: <draft>,
     $or: [clause_number|clause_title|text ~ query]}
      against document_vectors
      .limit(<the caller's limit>)

and then, per surviving row, gated ONLY the ``snippet`` on
``resolve_document_authority``. Everything else about the candidate was
returned unconditionally: ``source_id``, ``label`` (clause number + clause
title), ``citation``, ``clause_number``, ``page_numbers`` - and, decisively, the
candidate's EXISTENCE.

Nothing on that path asked:

* is the instrument APPLICABLE to this project at all, or merely catalogued;
* is it still in force, or WITHDRAWN / SUPERSEDED;
* is its clause projection CURRENT, or a revision behind, PENDING or FAILED;
* does the CALLER have canonical authority over the workspace being drafted in;
* does a canonical Document stand behind this derived row at all.

**"R20 will reject it later" is not a defence.** R20 governs whether a selected
source may GROUND a pleading. R21 governs what the server is willing to SAY. By
the time the hydrator refuses a clause, the response has already told the caller
that clause 8.4 of an instrument they have no authority over exists, what it is
called, and which document it belongs to. A later rejection cannot un-disclose
it. So every assertion here is about ABSENCE FROM THE RESPONSE, never about a
flag on a returned row.

**A derived row does not authorise itself.** ``document_vectors`` carries
``organization_id`` / ``project_id`` / ``document_id``, and every one of those
is INGEST PROVENANCE - it records where the chunk was FILED. The draft's own
stamp is the workspace the pleading is being written in. Neither records that an
instrument legally governs anything, so neither may decide what is disclosed.
The answer comes from the same canonical boundary the other two doors consume::

      applicable to (project_id, contract_id) at the query mode
    & canonical Document positively resolvable
    & publication-consumable
    & projection-current

resolved by ``ContractScopeResolver`` for an actor who is canonically entitled
to the workspace - i.e. ``resolve_authorized_project_universe``, the seam G-A9
built and G-A11 reused. There is no second implementation of applicability here
and there must never be one.

**Containment before the limit, not after it.** The fence is part of the QUERY,
placed ahead of ``.limit()``. Filtering a broad top-K afterwards produces a
response that LOOKS clean while ineligible candidates silently eat the caller's
capacity - the eligible clause the user needed is simply missing, with no
warning. :func:`test_ineligible_candidates_cannot_starve_an_eligible_one` is the
non-negotiable proof, and MUTATION D exists to show it is not vacuous.

**The correspondence surface is the same door.** ``_search_documents`` sits
beside ``_search_clauses`` behind the same route and disclosed ``subject`` /
``letterNo`` / ``filename`` / ``_id`` for documents whose publication authority
denies consumption - it withheld only the snippet, and then fell back to
``subject``, which it had already published as the label. A quarantined
duplicate, a deleted document and a document sent to human review were all
offered as evidence. That surface is fenced on publication authority, and
deliberately NOT on contract applicability: a letter is not a contract
instrument, has no applicability aggregate, and fencing it on one would delete
lawful correspondence from every pleading.
:func:`test_a_lawful_correspondence_document_is_still_suggested` pins that, so
the containment cannot quietly become a blanket denial.

**Positives are load-bearing.** ``processing_status == "failed"`` is
OPERATIONAL - extraction broke, which is not a verdict about the content - so a
last-known-good instrument's clause must still be suggested (Model B). An
applicable, publication-consumable, projection-current clause must still be
offered, and must still be accepted when it is selected through the REAL R20
path. A suggestion endpoint that suggests nothing is an outage, not a boundary.

**Recorded scope, not assumed.** PROJECT-EVIDENCE CONTAINMENT, carried unchanged
from G-A7/G-A8/G-A9/G-A11: the arbitration draft's ``contract_id`` is free text
that nothing validates or joins against ``contract_document_applicability``, so
keying suggestions on it would invent an identity mapping. A clause of sibling
contract ``OTHER`` inside the SAME authorised project can still be suggested for
a draft about ``MAIN``. Bounded, pinned at the resolver, and an owner decision to
close.
"""

from __future__ import annotations

import ast
import inspect
import re
from copy import deepcopy
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pytest
from bson import ObjectId
from fastapi import HTTPException

from rbac_backend.models.arbitration_drafting import ArbitrationEvidenceSearchRequest
from rbac_backend.services.arbitration_drafting import service as arbitration_service
from rbac_backend.services.arbitration_drafting.service import ArbitrationDraftingService

# --- unique markers -------------------------------------------------------
#
# Every marker lives ONLY inside clause text or a document subject, never inside
# an identifier, so an assertion can never pass because the string happened to
# be absent for an unrelated reason.

AUTHORISED_MARKER = "R21_PROJECT_A_SUGGESTION_AUTHORISED_20260831"
SIBLING_PROJECT_MARKER = "R21_PROJECT_B_SUGGESTION_CONFIDENTIAL_20260831"
FOREIGN_ORG_MARKER = "R21_FOREIGN_ORG_SUGGESTION_CONFIDENTIAL_20260831"
NO_APPLICABILITY_MARKER = "R21_CATALOGUE_ONLY_SUGGESTION_CONFIDENTIAL_20260831"
WITHDRAWN_MARKER = "R21_WITHDRAWN_SUGGESTION_CONFIDENTIAL_20260831"
SUPERSEDED_MARKER = "R21_SUPERSEDED_SUGGESTION_CONFIDENTIAL_20260831"
HUMAN_REVIEW_MARKER = "R21_HUMAN_REVIEW_SUGGESTION_CONFIDENTIAL_20260831"
DUPLICATE_MARKER = "R21_DUPLICATE_SUGGESTION_CONFIDENTIAL_20260831"
DELETED_MARKER = "R21_DELETED_SUGGESTION_CONFIDENTIAL_20260831"
ORPHAN_MARKER = "R21_ORPHAN_SUGGESTION_CONFIDENTIAL_20260831"
LYING_CLAUSE_MARKER = "R21_LYING_ROW_SUGGESTION_CONFIDENTIAL_20260831"
STALE_PROJECTION_MARKER = "R21_STALE_PROJECTION_SUGGESTION_CONFIDENTIAL_20260831"
PENDING_PROJECTION_MARKER = "R21_PENDING_PROJECTION_SUGGESTION_CONFIDENTIAL_20260831"
FAILED_PROJECTION_MARKER = "R21_FAILED_PROJECTION_SUGGESTION_CONFIDENTIAL_20260831"
OPERATIONAL_FAILED_MARKER = "R21_OPERATIONAL_FAILED_LAST_KNOWN_GOOD_20260831"
DOC_AUTHORISED_MARKER = "R21_CORRESPONDENCE_SUGGESTION_AUTHORISED_20260831"
DOC_HUMAN_REVIEW_MARKER = "R21_CORRESPONDENCE_HUMAN_REVIEW_CONFIDENTIAL_20260831"
DOC_DUPLICATE_MARKER = "R21_CORRESPONDENCE_DUPLICATE_CONFIDENTIAL_20260831"
DOC_DELETED_MARKER = "R21_CORRESPONDENCE_DELETED_CONFIDENTIAL_20260831"

APPLICABILITY = "contract_document_applicability"
APPLICABILITY_EVENTS = "contract_document_applicability_events"
CONTRACT_DOCUMENTS = "contract_documents"
DOCUMENT_VECTORS = "document_vectors"
DOCUMENTS = "documents"

#: The query every suggestion test issues. It matches on clause TEXT, so a
#: candidate's exclusion can never be an artefact of a clause number that
#: happened not to match.
QUERY = "entitled to an extension"


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
                # The suggestion path is Mongo LEXICAL search: `$regex` with
                # `$options: "i"`. A harness that ignored it would match every
                # row and could not tell a fence from a coincidence.
                flags = re.IGNORECASE if "i" in str(expected.get("$options") or "") else 0
                if actual is None or not re.search(str(expected["$regex"]), str(actual), flags):
                    return False
            continue
        if not _same(actual, expected):
            return False
    return True


class _Cursor:
    def __init__(self, rows: Iterable[Dict[str, Any]]) -> None:
        self.rows = [deepcopy(row) for row in rows]

    def sort(self, key: Any = None, direction: int = 1) -> "_Cursor":
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
        #: `find_one`. Pre-limit containment is proved from the QUERY, not from
        #: the result: a fence applied to the rows afterwards produces the same
        #: response on a small fixture and a starved one in production.
        self.queries: List[Optional[Dict[str, Any]]] = []
        self.find_raises: Optional[Exception] = None

    async def find_one(self, query=None, projection=None, sort=None, **_k: Any):
        self.queries.append(deepcopy(query))
        if self.find_raises is not None:
            raise self.find_raises
        matches = [row for row in self.rows if _matches(row, query)]
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


class _World:
    """Seeds the canonical Contract Master records for one test.

    Nothing here writes an "eligible" flag. Eligibility is whatever
    ``ContractScopeResolver`` derives from applicability events, the contract
    document record's projection fields and the canonical ``documents`` row - so
    a test cannot assert against a fixture's opinion of itself.
    """

    def __init__(self) -> None:
        self.documents: List[Dict[str, Any]] = []
        self.contract_documents: List[Dict[str, Any]] = []
        self.applicability: List[Dict[str, Any]] = []
        self.applicability_events: List[Dict[str, Any]] = []
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
        lifecycle: Tuple[str, ...] = ("APPLIED",),
        canonical_document: bool = True,
    ) -> str:
        """One contract instrument and (optionally) its applicability.

        ``project_id=None`` means "catalogued in the organisation but applied to
        no project at all" - the zero-applicability case.
        ``canonical_document=False`` means the derived chunk's ``document_id``
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
                    classification_revision if projection_revision is None else projection_revision
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

    def chunk(
        self,
        *,
        document_id: str,
        text: str,
        organization_id: str = "org-A",
        project_id: str = "proj-A",
        clause_number: str = "8.4",
        clause_title: str = "Extension of Time",
        vector_id: Optional[str] = None,
    ) -> str:
        """A ``document_vectors`` chunk - what the suggestion path searches.

        Several tests deliberately stamp a chunk with the DRAFTING workspace
        while its canonical instrument says otherwise. That is the
        lying-provenance case: the row records where it was FILED, never that
        the instrument governs anything, so it must not authorise itself.
        """
        row_id = vector_id or self._next("vec")
        self.vectors.append(
            {
                "_id": row_id,
                "organization_id": organization_id,
                "project_id": project_id,
                "document_id": document_id,
                "clause_number": clause_number,
                "clause_title": clause_title,
                "text": f"The Contractor shall be entitled to an extension of time. {text}",
                "page_numbers": [12],
            }
        )
        return row_id

    def correspondence(
        self,
        *,
        subject: str,
        document_id: Optional[str] = None,
        organization_id: str = "org-A",
        project_id: str = "proj-A",
        processing_status: str = "completed",
        duplicate_status: Optional[str] = None,
        lifecycle_state: Optional[str] = None,
    ) -> str:
        """A canonical ``documents`` row - what the correspondence surface searches."""
        row_id = document_id or self._next("doc")
        self.documents.append(
            {
                "_id": row_id,
                "organization_id": organization_id,
                "project_id": project_id,
                "subject": f"Notice - entitled to an extension of time - {subject}",
                "letterNo": f"LTR-{row_id}",
                "filename": f"{row_id}.pdf",
                "summary": f"Summary body {subject}",
                "processing_status": processing_status,
                "duplicate_status": duplicate_status,
                "lifecycle_state": lifecycle_state,
            }
        )
        return row_id

    def database(self, drafts: Optional[List[Dict[str, Any]]] = None) -> _Database:
        return _Database(
            {
                "arbitration_drafts": list(drafts or []),
                DOCUMENTS: self.documents,
                CONTRACT_DOCUMENTS: self.contract_documents,
                APPLICABILITY: self.applicability,
                APPLICABILITY_EVENTS: self.applicability_events,
                DOCUMENT_VECTORS: self.vectors,
            }
        )


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
    """Entitled to project B only - and asking for suggestions inside project A."""
    return SimpleNamespace(
        id="user-B",
        email="user-b@example.com",
        roles=["projectuser"],
        organization_id="org-A",
        organizations=["org-A"],
        projects=["proj-B"],
    )


def _rootless_worker(draft: Dict[str, Any]) -> SimpleNamespace:
    """R17's shape: a principal FABRICATED from the resource being read.

    It has no roles, so canonical authorisation denies it. Pinned here so a
    later change cannot make fabricated principals permissive on a disclosure
    surface.
    """
    return SimpleNamespace(
        id="worker",
        roles=[],
        organization_id=draft.get("organization_id"),
        project_id=draft.get("project_id"),
    )


# ---------------------------------------------------------------------------
# Drafts and the one surface a suggestion reaches
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
        "status": "draft",
    }


class _Suggestions:
    """Everything one suggestion request made observable.

    Assertions are about ABSENCE FROM THE RESPONSE. ``text()`` flattens the
    WHOLE payload - identifiers, labels, citations, clause numbers, snippets and
    page numbers together - because R21 is violated by any one of them.
    """

    def __init__(
        self,
        db: _Database,
        response: Optional[Dict[str, Any]] = None,
        error: Optional[HTTPException] = None,
    ) -> None:
        self.db = db
        self.response = response
        self.error = error

    @property
    def rejected(self) -> bool:
        return self.error is not None

    @property
    def rows(self) -> List[Dict[str, Any]]:
        return list((self.response or {}).get("results") or [])

    @property
    def count(self) -> int:
        return len(self.rows)

    def text(self) -> str:
        return repr(self.response)

    def source_ids(self) -> set:
        return {str(row.get("source_id")) for row in self.rows}

    def clause_numbers(self) -> set:
        return {str(row.get("clause_number")) for row in self.rows if row.get("clause_number")}

    def vector_queries(self) -> List[Optional[Dict[str, Any]]]:
        return list(self.db[DOCUMENT_VECTORS].queries)

    def document_queries(self) -> List[Optional[Dict[str, Any]]]:
        return list(self.db[DOCUMENTS].queries)


async def _suggest(
    world: _World,
    *,
    actor: Optional[SimpleNamespace] = None,
    draft: Optional[Dict[str, Any]] = None,
    query: str = QUERY,
    limit: int = 20,
) -> _Suggestions:
    """Drive the REAL suggestion service method, end to end.

    ``evidence_search`` is the exact function the route calls; nothing is
    stubbed between it and the collections. A rejection is an OUTCOME the test
    inspects, not an error it swallows.
    """
    draft = draft or _draft()
    db = world.database(drafts=[draft])
    service = ArbitrationDraftingService(db)
    payload = ArbitrationEvidenceSearchRequest(query=query, limit=limit)
    try:
        response = await service.evidence_search(
            str(draft["_id"]), payload, actor if actor is not None else _project_actor()
        )
    except HTTPException as exc:
        return _Suggestions(db, error=exc)
    return _Suggestions(db, response=response)


def _eligible_world() -> Tuple[_World, str]:
    """One instrument that is eligible on every axis, plus its chunk."""
    world = _World()
    world.instrument(document_id="doc-ok")
    vector_id = world.chunk(document_id="doc-ok", text=AUTHORISED_MARKER, clause_number="8.4")
    return world, vector_id


# ===========================================================================
# RED 1 - a sibling project's clause is not disclosed
# ===========================================================================


@pytest.mark.asyncio
async def test_a_sibling_project_clause_is_never_suggested():
    """The chunk is FILED in project A. Its instrument governs project B only.

    Row provenance is where a chunk was filed. It is not a statement that the
    instrument governs the workspace being drafted in, and it may not put the
    clause in front of a user.
    """
    world = _World()
    world.instrument(document_id="doc-ok")
    world.chunk(document_id="doc-ok", text=AUTHORISED_MARKER, clause_number="8.4")

    # Applied to project B, but its chunk is stamped with project A.
    world.instrument(document_id="doc-sibling", project_id="proj-B", contract_id="OTHER")
    sibling = world.chunk(
        document_id="doc-sibling",
        text=SIBLING_PROJECT_MARKER,
        project_id="proj-A",
        clause_number="14.7",
        clause_title="Sibling Payment Terms",
    )

    result = await _suggest(world)

    assert not result.rejected
    assert SIBLING_PROJECT_MARKER not in result.text()
    assert sibling not in result.source_ids()
    assert "doc-sibling" not in result.text()
    assert "14.7" not in result.clause_numbers()
    assert "Sibling Payment Terms" not in result.text()
    # And the response is not merely empty: the lawful clause still stands.
    assert AUTHORISED_MARKER in result.text()
    assert result.count == 1


# ===========================================================================
# RED 2 - a foreign organisation's clause is not disclosed
# ===========================================================================


@pytest.mark.asyncio
async def test_a_foreign_organisation_clause_is_never_suggested():
    world = _World()
    world.instrument(document_id="doc-ok")
    world.chunk(document_id="doc-ok", text=AUTHORISED_MARKER)

    world.instrument(document_id="doc-foreign", organization_id="org-B", project_id="proj-B")
    foreign = world.chunk(
        document_id="doc-foreign",
        text=FOREIGN_ORG_MARKER,
        organization_id="org-A",
        project_id="proj-A",
        clause_number="20.1",
    )

    result = await _suggest(world)

    assert FOREIGN_ORG_MARKER not in result.text()
    assert foreign not in result.source_ids()
    assert "doc-foreign" not in result.text()
    assert AUTHORISED_MARKER in result.text()


# ===========================================================================
# RED 3 - catalogued but applied to nothing
# ===========================================================================


@pytest.mark.asyncio
async def test_a_zero_applicability_instrument_is_never_suggested():
    """Visible in the organisation's catalogue; applied to no project at all."""
    world = _World()
    world.instrument(document_id="doc-ok")
    world.chunk(document_id="doc-ok", text=AUTHORISED_MARKER)

    world.instrument(document_id="doc-catalogue", project_id=None, contract_id=None)
    catalogued = world.chunk(
        document_id="doc-catalogue", text=NO_APPLICABILITY_MARKER, clause_number="3.1"
    )

    result = await _suggest(world)

    assert NO_APPLICABILITY_MARKER not in result.text()
    assert catalogued not in result.source_ids()
    assert "doc-catalogue" not in result.text()
    assert AUTHORISED_MARKER in result.text()


# ===========================================================================
# RED 4 - withdrawn / superseded at the query mode
# ===========================================================================


@pytest.mark.asyncio
async def test_a_withdrawn_instrument_is_never_suggested():
    world = _World()
    world.instrument(document_id="doc-ok")
    world.chunk(document_id="doc-ok", text=AUTHORISED_MARKER)

    world.instrument(document_id="doc-withdrawn", lifecycle=("APPLIED", "WITHDRAWN"))
    withdrawn = world.chunk(
        document_id="doc-withdrawn", text=WITHDRAWN_MARKER, clause_number="4.2"
    )

    result = await _suggest(world)

    assert WITHDRAWN_MARKER not in result.text()
    assert withdrawn not in result.source_ids()
    assert AUTHORISED_MARKER in result.text()


@pytest.mark.asyncio
async def test_a_superseded_instrument_is_never_suggested():
    world = _World()
    world.instrument(document_id="doc-ok")
    world.chunk(document_id="doc-ok", text=AUTHORISED_MARKER)

    world.instrument(document_id="doc-superseded", lifecycle=("APPLIED", "SUPERSEDED"))
    superseded = world.chunk(
        document_id="doc-superseded", text=SUPERSEDED_MARKER, clause_number="4.3"
    )

    result = await _suggest(world)

    assert SUPERSEDED_MARKER not in result.text()
    assert superseded not in result.source_ids()
    assert AUTHORISED_MARKER in result.text()


# ===========================================================================
# RED 5 / RED 6 - publication authority denies the canonical Document
# ===========================================================================


@pytest.mark.asyncio
async def test_a_human_review_instrument_leaks_nothing_not_even_its_identity():
    """The old gate withheld the snippet and published everything else.

    Withholding the text while returning the clause number, the title and the
    document id still tells the caller that a clause of an instrument under
    human review exists and what it is called.
    """
    world = _World()
    world.instrument(document_id="doc-ok")
    world.chunk(document_id="doc-ok", text=AUTHORISED_MARKER)

    world.instrument(document_id="doc-review", processing_status="human_review_required")
    review = world.chunk(
        document_id="doc-review",
        text=HUMAN_REVIEW_MARKER,
        clause_number="17.6",
        clause_title="Limitation of Liability",
    )

    result = await _suggest(world)

    assert HUMAN_REVIEW_MARKER not in result.text()
    assert review not in result.source_ids()
    assert "doc-review" not in result.text()
    assert "17.6" not in result.clause_numbers()
    assert "Limitation of Liability" not in result.text()
    assert AUTHORISED_MARKER in result.text()


@pytest.mark.asyncio
async def test_a_quarantined_duplicate_is_never_suggested():
    world = _World()
    world.instrument(document_id="doc-ok")
    world.chunk(document_id="doc-ok", text=AUTHORISED_MARKER)

    world.instrument(document_id="doc-dup", duplicate_status="duplicate")
    duplicate = world.chunk(document_id="doc-dup", text=DUPLICATE_MARKER, clause_number="5.5")

    result = await _suggest(world)

    assert DUPLICATE_MARKER not in result.text()
    assert duplicate not in result.source_ids()
    assert AUTHORISED_MARKER in result.text()


@pytest.mark.asyncio
async def test_a_deleted_instrument_is_never_suggested():
    world = _World()
    world.instrument(document_id="doc-ok")
    world.chunk(document_id="doc-ok", text=AUTHORISED_MARKER)

    world.instrument(document_id="doc-deleted", lifecycle_state="deleted")
    deleted = world.chunk(document_id="doc-deleted", text=DELETED_MARKER, clause_number="6.6")

    result = await _suggest(world)

    assert DELETED_MARKER not in result.text()
    assert deleted not in result.source_ids()
    assert AUTHORISED_MARKER in result.text()


# ===========================================================================
# RED 7 / RED 8 - a derived row cannot authorise itself
# ===========================================================================


@pytest.mark.asyncio
async def test_an_orphan_chunk_cannot_self_authorize():
    """The chunk exists; no canonical Document stands behind it.

    Positive resolution, not subtraction. A subtractive
    ``blocked_document_ids`` fence fails OPEN on an id it cannot resolve, which
    is exactly how an orphan escapes.
    """
    world = _World()
    world.instrument(document_id="doc-ok")
    world.chunk(document_id="doc-ok", text=AUTHORISED_MARKER)

    world.instrument(document_id="doc-orphan", canonical_document=False)
    orphan = world.chunk(document_id="doc-orphan", text=ORPHAN_MARKER, clause_number="9.9")

    result = await _suggest(world)

    assert ORPHAN_MARKER not in result.text()
    assert orphan not in result.source_ids()
    assert "doc-orphan" not in result.text()
    assert AUTHORISED_MARKER in result.text()


@pytest.mark.asyncio
async def test_a_lying_chunk_cannot_self_authorize():
    """The chunk claims the drafting workspace. Canonical applicability disagrees.

    There is no applicability aggregate for this instrument at all, and the row
    saying ``project_id: proj-A`` does not create one.
    """
    world = _World()
    world.instrument(document_id="doc-ok")
    world.chunk(document_id="doc-ok", text=AUTHORISED_MARKER)

    world.instrument(document_id="doc-liar", project_id=None, contract_id=None)
    liar = world.chunk(
        document_id="doc-liar",
        text=LYING_CLAUSE_MARKER,
        organization_id="org-A",
        project_id="proj-A",
        clause_number="11.1",
    )

    result = await _suggest(world)

    assert LYING_CLAUSE_MARKER not in result.text()
    assert liar not in result.source_ids()
    assert AUTHORISED_MARKER in result.text()


# ===========================================================================
# RED 9 / RED 10 - projection currency
# ===========================================================================


@pytest.mark.asyncio
async def test_a_stale_projection_is_never_suggested():
    """``projection_status`` says CURRENT; the revision says a classification behind."""
    world = _World()
    world.instrument(document_id="doc-ok")
    world.chunk(document_id="doc-ok", text=AUTHORISED_MARKER)

    world.instrument(
        document_id="doc-stale",
        classification_revision=4,
        projection_revision=3,
        projection_status="CURRENT",
    )
    stale = world.chunk(document_id="doc-stale", text=STALE_PROJECTION_MARKER, clause_number="7.1")

    result = await _suggest(world)

    assert STALE_PROJECTION_MARKER not in result.text()
    assert stale not in result.source_ids()
    assert AUTHORISED_MARKER in result.text()


@pytest.mark.asyncio
async def test_a_pending_projection_is_never_suggested():
    world = _World()
    world.instrument(document_id="doc-ok")
    world.chunk(document_id="doc-ok", text=AUTHORISED_MARKER)

    world.instrument(document_id="doc-pending", projection_status="PENDING")
    pending = world.chunk(
        document_id="doc-pending", text=PENDING_PROJECTION_MARKER, clause_number="7.2"
    )

    result = await _suggest(world)

    assert PENDING_PROJECTION_MARKER not in result.text()
    assert pending not in result.source_ids()
    assert AUTHORISED_MARKER in result.text()


@pytest.mark.asyncio
async def test_a_failed_projection_is_never_suggested():
    world = _World()
    world.instrument(document_id="doc-ok")
    world.chunk(document_id="doc-ok", text=AUTHORISED_MARKER)

    world.instrument(document_id="doc-failproj", projection_status="FAILED")
    failed = world.chunk(
        document_id="doc-failproj", text=FAILED_PROJECTION_MARKER, clause_number="7.3"
    )

    result = await _suggest(world)

    assert FAILED_PROJECTION_MARKER not in result.text()
    assert failed not in result.source_ids()
    assert AUTHORISED_MARKER in result.text()


# ===========================================================================
# RED 11 - Model B: operational failure is not a verdict
# ===========================================================================


@pytest.mark.asyncio
async def test_an_operationally_failed_instrument_keeps_its_last_known_good_clause():
    """``processing_status == "failed"`` means the pipeline broke.

    An OCR crash, a Mongo timeout and an S3 error all land there. None of them
    is a judgement about the content, so the previously published clause is
    still consumable - and still suggestable.
    """
    world = _World()
    world.instrument(document_id="doc-lkg", processing_status="failed")
    lkg = world.chunk(document_id="doc-lkg", text=OPERATIONAL_FAILED_MARKER, clause_number="8.4")

    result = await _suggest(world)

    assert OPERATIONAL_FAILED_MARKER in result.text()
    assert lkg in result.source_ids()


# ===========================================================================
# RED 12 - CAPACITY. The load-bearing pre-limit proof.
# ===========================================================================


@pytest.mark.asyncio
async def test_ineligible_candidates_cannot_starve_an_eligible_one():
    """Twelve ineligible candidates rank ahead of one eligible clause; limit is 3.

    Post-filtering a broad top-K returns a response that LOOKS clean and is
    missing the only clause the user could lawfully have used, with nothing to
    say so. Containment therefore belongs in the QUERY, ahead of ``.limit()``.
    """
    world = _World()
    for index in range(12):
        world.instrument(document_id=f"doc-noise-{index}", project_id=None, contract_id=None)
        world.chunk(
            document_id=f"doc-noise-{index}",
            text=f"{NO_APPLICABILITY_MARKER}-{index}",
            organization_id="org-A",
            project_id="proj-A",
            clause_number=f"99.{index}",
        )
    world.instrument(document_id="doc-ok")
    eligible = world.chunk(document_id="doc-ok", text=AUTHORISED_MARKER, clause_number="8.4")

    result = await _suggest(world, limit=3)

    assert NO_APPLICABILITY_MARKER not in result.text()
    assert eligible in result.source_ids()
    assert AUTHORISED_MARKER in result.text()


@pytest.mark.asyncio
async def test_the_eligible_universe_is_part_of_the_clause_query_not_a_filter_over_it():
    """Structural twin of the capacity test, asserted on the QUERY itself.

    A fixture small enough to fit under the limit cannot distinguish a fenced
    query from a fenced result. The recorded filter can.
    """
    world, _ = _eligible_world()
    world.instrument(document_id="doc-catalogue", project_id=None, contract_id=None)
    world.chunk(document_id="doc-catalogue", text=NO_APPLICABILITY_MARKER)

    result = await _suggest(world)

    evidence_queries = [query for query in result.vector_queries() if query and "$or" in query]
    assert evidence_queries, "the clause suggestion search issued no lexical query"
    for query in evidence_queries:
        fence = query.get("document_id")
        assert isinstance(fence, dict) and "$in" in fence, (
            f"the clause suggestion query carries no canonical document fence: {query!r}"
        )
        assert "doc-catalogue" not in {str(item) for item in fence["$in"]}


# ===========================================================================
# RED 13 - a valid empty answer never broadens
# ===========================================================================


@pytest.mark.asyncio
async def test_a_valid_empty_universe_returns_no_clause_and_searches_no_wider():
    """Nothing applicable governs this project. That is an ANSWER.

    It is never a reason to fall back to a broader query - falling back to
    generic contract search is exactly the door the Contract Master model
    closes.
    """
    world = _World()
    world.instrument(document_id="doc-catalogue", project_id=None, contract_id=None)
    world.chunk(document_id="doc-catalogue", text=NO_APPLICABILITY_MARKER)
    world.instrument(document_id="doc-sibling", project_id="proj-B", contract_id="OTHER")
    world.chunk(document_id="doc-sibling", text=SIBLING_PROJECT_MARKER, project_id="proj-A")

    result = await _suggest(world)

    assert not result.rejected
    assert result.count == 0
    assert NO_APPLICABILITY_MARKER not in result.text()
    assert SIBLING_PROJECT_MARKER not in result.text()
    # No unfenced clause query may have been issued at all.
    for query in result.vector_queries():
        if query and "$or" in query:
            fence = query.get("document_id")
            assert isinstance(fence, dict) and "$in" in fence and not fence["$in"]


# ===========================================================================
# RED 14 - authority failure is not an empty answer
# ===========================================================================


@pytest.mark.asyncio
async def test_an_authority_resolution_failure_refuses_rather_than_searching_broadly():
    """"I could not tell" and "nothing applies" must not look the same.

    A degraded suggestion list is the worst outcome available: it looks like a
    lawful answer and is not one.
    """
    world = _World()
    world.instrument(document_id="doc-ok")
    world.chunk(document_id="doc-ok", text=AUTHORISED_MARKER)
    draft = _draft()
    db = world.database(drafts=[draft])
    db[APPLICABILITY].find_raises = RuntimeError("applicability enumeration exploded")

    service = ArbitrationDraftingService(db)
    payload = ArbitrationEvidenceSearchRequest(query=QUERY, limit=20)
    with pytest.raises(HTTPException) as excinfo:
        await service.evidence_search("draft-1", payload, _project_actor())

    assert excinfo.value.status_code in {409, 503}
    assert AUTHORISED_MARKER not in repr(excinfo.value.detail)
    # And no broad clause query was issued after the failure.
    for query in db[DOCUMENT_VECTORS].queries:
        if query and "$or" in query:
            fence = query.get("document_id")
            assert isinstance(fence, dict) and "$in" in fence


# ===========================================================================
# RED 15 - the positive. A lawful clause is still offered.
# ===========================================================================


@pytest.mark.asyncio
async def test_a_lawful_clause_is_still_suggested_with_its_text():
    world, vector_id = _eligible_world()

    result = await _suggest(world)

    assert result.count == 1
    row = result.rows[0]
    assert row["source_type"] == "clause"
    assert str(row["source_id"]) == vector_id
    assert row["clause_number"] == "8.4"
    assert "Extension of Time" in row["label"]
    assert AUTHORISED_MARKER in (row.get("snippet") or "")
    assert row["page_numbers"] == [12]


# ===========================================================================
# RED 16 - the server enforces authority, not the UI
# ===========================================================================


@pytest.mark.asyncio
async def test_an_actor_without_canonical_authority_over_the_workspace_sees_nothing():
    """The draft's stamp is not the actor's entitlement.

    ``_search_clauses`` resolved candidates from the DRAFT's workspace with no
    reference to who was asking, so any caller who reached the service saw the
    project's clauses.
    """
    world, _ = _eligible_world()

    result = await _suggest(world, actor=_sibling_project_actor())

    assert result.count == 0
    assert AUTHORISED_MARKER not in result.text()


@pytest.mark.asyncio
async def test_a_fabricated_principal_is_denied_on_the_disclosure_surface():
    world, _ = _eligible_world()
    draft = _draft()

    result = await _suggest(world, draft=draft, actor=_rootless_worker(draft))

    assert result.count == 0
    assert AUTHORISED_MARKER not in result.text()


# ===========================================================================
# RED 17 - counts and metadata carry existence
# ===========================================================================


@pytest.mark.asyncio
async def test_ineligible_candidates_contribute_nothing_to_the_response_size():
    """A hidden row that still inflates a count is still disclosure."""
    world = _World()
    world.instrument(document_id="doc-ok")
    world.chunk(document_id="doc-ok", text=AUTHORISED_MARKER, clause_number="8.4")
    for index, (marker, kwargs) in enumerate(
        [
            (NO_APPLICABILITY_MARKER, {"project_id": None, "contract_id": None}),
            (WITHDRAWN_MARKER, {"lifecycle": ("APPLIED", "WITHDRAWN")}),
            (HUMAN_REVIEW_MARKER, {"processing_status": "human_review_required"}),
            (STALE_PROJECTION_MARKER, {"classification_revision": 4, "projection_revision": 3}),
        ]
    ):
        world.instrument(document_id=f"doc-bad-{index}", **kwargs)
        world.chunk(
            document_id=f"doc-bad-{index}",
            text=marker,
            organization_id="org-A",
            project_id="proj-A",
            clause_number=f"88.{index}",
        )

    result = await _suggest(world)

    assert result.count == 1, f"ineligible candidates contributed to the response: {result.rows!r}"
    assert len(result.source_ids()) == 1


# ===========================================================================
# RED 18 - canonical eligibility INTERSECTS the caller's narrowing
# ===========================================================================


@pytest.mark.asyncio
async def test_canonical_eligibility_intersects_the_callers_query_rather_than_replacing_it():
    """An eligible clause that does not match the caller's terms stays out.

    The fence narrows; it never becomes the answer. Replacing the caller's
    query with the eligible universe would turn a search box into a dump of
    every applicable clause.
    """
    world = _World()
    world.instrument(document_id="doc-ok")
    matching = world.chunk(document_id="doc-ok", text=AUTHORISED_MARKER, clause_number="8.4")
    world.instrument(document_id="doc-other")
    # Eligible on every axis, but nothing in it matches the query terms.
    world.vectors.append(
        {
            "_id": "vec-unrelated",
            "organization_id": "org-A",
            "project_id": "proj-A",
            "document_id": "doc-other",
            "clause_number": "2.1",
            "clause_title": "Definitions",
            "text": "In this Contract the following words shall have the meanings assigned.",
            "page_numbers": [1],
        }
    )

    result = await _suggest(world)

    assert result.source_ids() == {matching}


# ===========================================================================
# RED 19 - the suggestion and selection universes are the same universe
# ===========================================================================


@pytest.mark.asyncio
async def test_a_suggested_clause_round_trips_through_the_real_selection_path():
    """Whatever R21 offers, R20 must accept. Otherwise the UI offers refusals.

    This is the alignment proof: both doors consume
    ``resolve_authorized_project_universe``, so a suggestion cannot be a
    selection the server will reject, and a rejected selection cannot have been
    a suggestion.
    """
    world, vector_id = _eligible_world()
    draft = _draft()
    db = world.database(drafts=[draft])
    service = ArbitrationDraftingService(db)
    actor = _project_actor()

    suggested = await service.evidence_search(
        "draft-1", ArbitrationEvidenceSearchRequest(query=QUERY, limit=20), actor
    )
    assert suggested["results"], "nothing was suggested, so the round trip proves nothing"
    offered = suggested["results"][0]
    assert str(offered["source_id"]) == vector_id

    hydrated = await service.context_builder.rehydrate_selected_references(
        draft,
        [
            {
                "source_type": offered["source_type"],
                "source_id": offered["source_id"],
                "label": offered["label"],
                "allowed_use": offered.get("allowed_use") or "clause",
                "metadata": {},
            }
        ],
        current_user=actor,
    )

    assert len(hydrated) == 1
    assert AUTHORISED_MARKER in repr(hydrated[0])


@pytest.mark.asyncio
async def test_a_clause_the_selection_path_refuses_is_never_offered_as_a_suggestion():
    """The inverse direction of the same alignment."""
    world = _World()
    world.instrument(document_id="doc-review", processing_status="human_review_required")
    review = world.chunk(document_id="doc-review", text=HUMAN_REVIEW_MARKER, clause_number="17.6")
    draft = _draft()
    db = world.database(drafts=[draft])
    service = ArbitrationDraftingService(db)
    actor = _project_actor()

    suggested = await service.evidence_search(
        "draft-1", ArbitrationEvidenceSearchRequest(query=QUERY, limit=20), actor
    )
    assert suggested["results"] == []

    with pytest.raises(HTTPException) as excinfo:
        await service.context_builder.rehydrate_selected_references(
            draft,
            [
                {
                    "source_type": "clause",
                    "source_id": review,
                    "label": "Clause the user typed in by hand",
                    "allowed_use": "clause",
                    "metadata": {},
                }
            ],
            current_user=actor,
        )
    assert excinfo.value.status_code == 400


# ===========================================================================
# The correspondence surface behind the same route
# ===========================================================================


@pytest.mark.asyncio
async def test_a_lawful_correspondence_document_is_still_suggested():
    """The contract fence is CLAUSE-scoped, deliberately.

    A letter is not a contract instrument and has no applicability aggregate.
    Fencing correspondence on the contract universe would delete lawful letters
    from every pleading - an outage dressed as a containment.
    """
    world = _World()
    lawful = world.correspondence(subject=DOC_AUTHORISED_MARKER)

    result = await _suggest(world)

    assert DOC_AUTHORISED_MARKER in result.text()
    assert lawful in result.source_ids()


@pytest.mark.asyncio
async def test_a_publication_denied_correspondence_document_is_never_suggested():
    """It withheld the snippet, then republished ``subject`` as the label.

    Three separate states, one door: a document under human review, a confirmed
    duplicate and a deleted document were each offered as selectable evidence
    with their subject, letter number and identifier intact.
    """
    world = _World()
    lawful = world.correspondence(subject=DOC_AUTHORISED_MARKER)
    review = world.correspondence(
        subject=DOC_HUMAN_REVIEW_MARKER, processing_status="human_review_required"
    )
    duplicate = world.correspondence(subject=DOC_DUPLICATE_MARKER, duplicate_status="duplicate")
    deleted = world.correspondence(subject=DOC_DELETED_MARKER, lifecycle_state="deleted")

    result = await _suggest(world)

    for marker in (DOC_HUMAN_REVIEW_MARKER, DOC_DUPLICATE_MARKER, DOC_DELETED_MARKER):
        assert marker not in result.text()
    for denied in (review, duplicate, deleted):
        assert denied not in result.source_ids()
        assert f"LTR-{denied}" not in result.text()
    assert lawful in result.source_ids()


@pytest.mark.asyncio
async def test_denied_correspondence_cannot_starve_a_lawful_document():
    """The correspondence surface has its own pre-limit obligation."""
    world = _World()
    for index in range(12):
        world.correspondence(
            subject=f"{DOC_DUPLICATE_MARKER}-{index}", duplicate_status="duplicate"
        )
    lawful = world.correspondence(subject=DOC_AUTHORISED_MARKER)

    result = await _suggest(world, limit=3)

    assert DOC_DUPLICATE_MARKER not in result.text()
    assert lawful in result.source_ids()


# ===========================================================================
# Structural pins - the seam, not a second implementation of it
# ===========================================================================


def _reachable_calls(root_name: str) -> set:
    """Every function name called from ``root_name``, transitively, in the service module."""
    module = ast.parse(inspect.getsource(arbitration_service))
    functions: Dict[str, ast.AST] = {}
    for node in ast.walk(module):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[node.name] = node

    seen: set = set()
    called: set = set()
    stack = [root_name]
    while stack:
        name = stack.pop()
        if name in seen or name not in functions:
            continue
        seen.add(name)
        for node in ast.walk(functions[name]):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            callee = getattr(func, "id", None) or getattr(func, "attr", None)
            if callee:
                called.add(callee)
                stack.append(callee)
    return called


def test_the_suggestion_door_consumes_the_canonical_universe():
    """Reachability from ``evidence_search``, not a module-wide grep.

    A module-wide search would be satisfied by any other call site and would
    keep passing after someone deleted the fence from this door.
    """
    called = _reachable_calls("evidence_search")
    assert "resolve_authorized_project_universe" in called, (
        "the arbitration suggestion door no longer resolves the canonical "
        f"eligible universe; it calls {sorted(called)}"
    )


def test_the_suggestion_door_does_not_reimplement_applicability_or_fall_back_to_generic_search():
    """The negative twin. One implementation of applicability, never two."""
    called = _reachable_calls("evidence_search")
    assert "search_contracts" not in called, (
        "generic contract search resolves no applicability and spends its "
        "candidate limit before any authority exists; it must not feed suggestions"
    )
    source = inspect.getsource(arbitration_service)
    body = source[source.index("async def evidence_search") :]
    body = body[: body.index("\n    async def refresh_source_ledger")]
    for forbidden in ("contract_document_applicability", "blocked_document_ids"):
        assert forbidden not in body, (
            f"the suggestion door reads {forbidden} directly instead of "
            "asking the canonical resolver"
        )
