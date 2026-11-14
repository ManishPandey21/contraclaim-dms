"""G-A7 / R14: drafting v2 may only ground a draft in AUTHORISED contract clauses.

Two seams in ``DraftContextBuilder`` put Contract/Clause material into the LLM
prompt and into the persisted ``DraftRun``, and neither consulted the frozen
Contract Master evidence authority:

* ``_contract_clause_sources`` called ``ContractService.search_contracts`` - the
  **generic** search path. Generic search is explicitly not evidence mode: it
  resolves no applicability, no projection currency and no per-document positive
  publication authority, and it spends its ``candidate_limit`` before any of
  those exist;
* ``_clause_record_sources`` queried ``db.contract_clauses`` directly on
  ``{org_id, project_id, is_current, is_authorised_for_ai}`` - that is the CLAUSE
  ROW's own ingest provenance - took the first 60 rows, and only then subtracted
  ``blocked_document_ids``, a helper whose own docstring says it fails OPEN on an
  identifier it cannot resolve.

``ContractClause.project_id`` and ``ContractClause.contract_id`` are ingest
provenance. They record where a row came from; they cannot establish that an
instrument legally governs anything. A row that claims the drafting project is
therefore self-authorising, and a row whose canonical document was never applied
to that project - or was withdrawn from it, or sent to human review, or whose
clause projection is a revision behind - is indistinguishable from a lawful one.

The accepted universe is positive and per contract::

      applicable to (project_id, contract_id) at the query mode
    & canonical Document positively resolvable
    & publication-consumable
    & projection-current

resolved by ``ContractScopeResolver`` for an actor who is canonically entitled to
the workspace. Drafting v2 must CONSUME that; it may not reconstruct it.

**Recorded gap, not a silent narrowing.** A drafting request carries no contract
identifier - neither ``Letter`` nor ``DraftRunCreateRequest`` has one - so the
consumer cannot ask the per-contract question the model is built around. The
containment therefore resolves every contract that has applicability in the
project and unions the answers, which keeps every canonical test intact but
cannot separate sibling contracts INSIDE one project. That residual is asserted
at the resolver level here (``test_the_canonical_universe_separates_two_contracts``)
and is carried as an owner decision, not papered over.

Assertions are made on five surfaces, following G-A5/G-A6: the rendered LLM
prompt captured BEFORE an answer exists (so filtering is proven to precede prompt
construction and model invocation), the returned ``DraftRun``, the re-read
``letter_draft_runs`` row, the immutable ``letter_draft_evidence_snapshots`` row
and the gunzipped ``draft_context_packs`` document. Text and PROVENANCE are
asserted separately: an ineligible clause id may not survive as "debug metadata"
merely because its text was removed.
"""

from __future__ import annotations

import gzip
import json
from copy import deepcopy
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pytest
from bson import ObjectId

from rbac_backend.models.contract_document import CurrentState
from rbac_backend.models.letter_drafting import DraftRun, DraftRunCreateRequest
from rbac_backend.services import conversation_service as conversation_service_module
from rbac_backend.services.contract_scope_resolver import (
    ContractScopeResolutionError,
    resolve_authorized_project_universe,
)
from rbac_backend.services.letter_drafting import context as context_module
from rbac_backend.services.letter_drafting import generator as generator_module
from rbac_backend.services.letter_drafting.service import DraftRunService

# --- unique markers -------------------------------------------------------
#
# Every marker is unique, so an assertion can never pass because the string
# happened to be absent for an unrelated reason.

AUTHORISED_MARKER = "PROJECT_A_CONTRACT_CLAUSE_AUTHORISED_20260831"
SIBLING_PROJECT_MARKER = "PROJECT_B_CONTRACT_CLAUSE_CONFIDENTIAL_20260831"
NO_APPLICABILITY_MARKER = "CATALOGUE_ONLY_CONTRACT_CLAUSE_CONFIDENTIAL_20260831"
WITHDRAWN_MARKER = "WITHDRAWN_INSTRUMENT_CLAUSE_CONFIDENTIAL_20260831"
HUMAN_REVIEW_MARKER = "HUMAN_REVIEW_CONTRACT_CLAUSE_CONFIDENTIAL_20260831"
DUPLICATE_MARKER = "DUPLICATE_CONTRACT_CLAUSE_CONFIDENTIAL_20260831"
DELETED_MARKER = "DELETED_CONTRACT_CLAUSE_CONFIDENTIAL_20260831"
OPERATIONAL_FAILED_MARKER = "OPERATIONAL_FAILED_CLAUSE_LAST_KNOWN_GOOD_20260831"
ORPHAN_MARKER = "ORPHAN_CONTRACT_CLAUSE_CONFIDENTIAL_20260831"
STALE_PROJECTION_MARKER = "STALE_PROJECTION_CLAUSE_CONFIDENTIAL_20260831"
PENDING_PROJECTION_MARKER = "PENDING_PROJECTION_CLAUSE_CONFIDENTIAL_20260831"
FAILED_PROJECTION_MARKER = "FAILED_PROJECTION_CLAUSE_CONFIDENTIAL_20260831"
CAPACITY_FILLER_MARKER = "CAPACITY_FILLER_CLAUSE_CONFIDENTIAL_20260831"
LEGACY_SEARCH_MARKER = "LEGACY_GENERIC_SEARCH_CLAUSE_CONFIDENTIAL_20260831"
REFINEMENT_MARKER = "REFINEMENT_CLAUSE_CONFIDENTIAL_20260831"

#: The one query term every seeded clause shares, so relevance is never the
#: reason a row is absent. Every seeded clause scores; only authority may
#: remove one.
QUERY_TERM = "extension"

APPLICABILITY = "contract_document_applicability"
APPLICABILITY_EVENTS = "contract_document_applicability_events"
CONTRACT_DOCUMENTS = "contract_documents"


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
    return (1, value.isoformat() if isinstance(value, datetime) else str(value))


class _Cursor:
    def __init__(self, rows: Iterable[Dict[str, Any]]) -> None:
        self.rows = [deepcopy(row) for row in rows]

    def sort(self, key: Any = None, direction: int = 1) -> "_Cursor":
        if isinstance(key, str):
            self.rows.sort(key=lambda row: _sort_key(row, key), reverse=direction < 0)
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

    async def find_one(self, query=None, projection=None, sort=None):
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

    async def insert_one(self, document: Dict[str, Any]):
        inserted = deepcopy(document)
        inserted.setdefault("_id", ObjectId())
        self.rows.append(inserted)
        return SimpleNamespace(inserted_id=inserted["_id"])

    async def update_one(self, query, update, upsert: bool = False, **_k: Any):
        for row in self.rows:
            if not _matches(row, query):
                continue
            row.update(deepcopy(update.get("$set", {})))
            for key, amount in (update.get("$inc") or {}).items():
                row[key] = int(row.get(key) or 0) + int(amount)
            for key, value in (update.get("$push") or {}).items():
                row.setdefault(key, []).append(deepcopy(value))
            return SimpleNamespace(matched_count=1, modified_count=1)
        if upsert:
            stored = {k: v for k, v in (query or {}).items() if not k.startswith("$")}
            stored.update(deepcopy(update.get("$set", {})))
            stored.setdefault("_id", ObjectId())
            self.rows.append(stored)
            return SimpleNamespace(matched_count=0, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)

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


NOW = datetime(2026, 8, 31, tzinfo=timezone.utc)


class _World:
    """Seeds the canonical Contract Master records for one test.

    Nothing here writes an "eligible" flag. Eligibility is whatever
    ``ContractScopeResolver`` derives from applicability events, the contract
    document record's projection fields and the canonical ``documents`` row -
    so a test cannot accidentally assert against a fixture's opinion.
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
        """Create one contract instrument and (optionally) its applicability.

        ``project_id=None`` means "catalogued in the organisation but applied to
        no project at all" - the zero-applicability case.
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
        uid: Optional[str] = None,
    ) -> str:
        """A clause ROW. Its ``org_id``/``project_id`` are ingest provenance.

        Several tests deliberately stamp a row with the drafting workspace while
        its canonical instrument says otherwise - that is the lying-provenance
        case, and the row must not be able to authorise itself.
        """
        clause_uid = uid or self._next("clause")
        self.clauses.append(
            {
                "clause_uid": clause_uid,
                "org_id": org_id,
                "project_id": project_id,
                "is_current": True,
                "is_authorised_for_ai": True,
                "clause_no": clause_no,
                "clause_title": f"Extension of time {clause_uid}",
                "cleaned_text": f"The Contractor shall be entitled to an {QUERY_TERM}. {text}",
                "page_start": 12,
                "page_end": 13,
                "document_id": document_id,
                "document_type": "GCC",
            }
        )
        return clause_uid

    def database(self, letters: List[Dict[str, Any]]) -> _Database:
        return _Database(
            {
                "letters": letters,
                "documents": self.documents,
                CONTRACT_DOCUMENTS: self.contract_documents,
                APPLICABILITY: self.applicability,
                APPLICABILITY_EVENTS: self.applicability_events,
                "contract_clauses": self.clauses,
            }
        )


# ``Sequence`` of ``str`` without importing typing.Sequence into the signature
# namespace, which shadows nothing else in this module.
Sequence_str = Tuple[str, ...]


# ---------------------------------------------------------------------------
# Stubs - everything outside this boundary is held still on purpose
# ---------------------------------------------------------------------------


class _DisabledFalkor:
    """The graph is a different seam (pinned by test_graph_consumer_authority)."""

    enabled = False

    def get_thread(self, *_a: Any, **_k: Any) -> List[Dict[str, Any]]:  # pragma: no cover
        return []


class _LegacyGenericSearch:
    """The generic ``search_contracts`` path, answering with a foreign clause.

    It models the property that matters and nothing else: generic search knows
    nothing about applicability, so it will happily return a chunk that the
    canonical universe excludes. If drafting still consults it, the marker
    arrives.
    """

    calls: List[Any] = []

    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    async def search_contracts(self, request: Any, *_a: Any, **_k: Any):
        type(self).calls.append(request)
        return SimpleNamespace(
            results=[
                SimpleNamespace(
                    document_id="doc-generic-search",
                    upload_id="upload-generic-search",
                    clause_number="8.4",
                    clause_title="Extension of time",
                    text=f"An {QUERY_TERM} of time applies. {LEGACY_SEARCH_MARKER}",
                    chunk_index=0,
                    page_numbers=[7],
                    page=7,
                    score=0.99,
                    file_name="generic.pdf",
                    source_filename="generic.pdf",
                )
            ]
        )


class _RecordingLLM:
    """Captures every rendered prompt BEFORE an answer exists."""

    prompts: List[str] = []

    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    async def generate(self, prompt: str, **_k: Any) -> str:
        type(self).prompts.append(prompt)
        return (
            "Draft Letter:\nDeterministic source-grounded draft.\n\n"
            "Source Integrity Notes:\nGrounded in the authorised ledger only."
        )


def _letter(
    letter_id: Any,
    *,
    organization_id: str = "org-A",
    project_id: Optional[str] = "proj-A",
    subject: str = "Extension of time",
    content: str = "Prepare a source-grounded response.",
    **overrides: Any,
) -> Dict[str, Any]:
    letter = {
        "_id": letter_id,
        "title": subject,
        "recipient": "Engineer",
        "subject": subject,
        "content": content,
        "status": "Draft",
        "created_by": "user-A",
        "assigned_to": "user-A",
        "organization_id": organization_id,
        "project_id": project_id,
        "letter_no": f"GA7-{str(letter_id)[-6:]}",
        "date": NOW,
        "created_at": NOW,
        "updated_at": NOW,
    }
    letter.update(overrides)
    return letter


def _project_actor() -> SimpleNamespace:
    """Organisation A, project A only. The narrowest realistic drafting actor."""
    return SimpleNamespace(
        id="user-A",
        roles=["projectuser"],
        organization_id="org-A",
        organizations=["org-A"],
        projects=["proj-A"],
    )


def _global_actor() -> SimpleNamespace:
    """A global actor, resolved through canonical authorization - not by name."""
    return SimpleNamespace(
        id="user-super",
        roles=["superadmin"],
        organization_id="org-A",
        organizations=["org-A", "org-B"],
        projects=["proj-A", "proj-B"],
    )


class _Run:
    """Every place drafting material can come to rest on the v2 path."""

    def __init__(self, run: DraftRun, db: _Database, prompts: List[str]) -> None:
        self.run = run
        self.db = db
        self.prompts = prompts

    @property
    def stored_row(self) -> Dict[str, Any]:
        for row in self.db["letter_draft_runs"].rows:
            if row.get("run_id") == self.run.run_id:
                return row
        return {}

    @property
    def evidence_snapshot(self) -> Dict[str, Any]:
        for row in self.db["letter_draft_evidence_snapshots"].rows:
            if row.get("run_id") == self.run.run_id:
                return row
        return {}

    @property
    def context_pack(self) -> Dict[str, Any]:
        for row in self.db["draft_context_packs"].rows:
            if row.get("run_id") != self.run.run_id:
                continue
            blob = row.get("compressed_data")
            if blob is None:
                return dict(row)
            return json.loads(gzip.decompress(bytes(blob)).decode("utf-8"))
        return {}

    def surfaces(self) -> Dict[str, str]:
        return {
            "the rendered LLM prompt (filtering must precede synthesis)": "\n".join(
                self.prompts
            ),
            "the returned DraftRun": repr(self.run.model_dump(mode="json")),
            "the PERSISTED letter_draft_runs row": repr(self.stored_row),
            "the IMMUTABLE evidence snapshot": repr(self.evidence_snapshot),
            "the PERSISTED context pack": repr(self.context_pack),
        }

    def provenance(self) -> str:
        return repr(
            {
                "source_ids": [source.source_id for source in self.run.sources],
                "source_document_ids": [source.document_id for source in self.run.sources],
                "stored_sources": self.stored_row.get("sources"),
                "evidence_snapshot_sources": self.evidence_snapshot.get("sources"),
                "context_pack_source_ids": self.context_pack.get("source_ids"),
                "context_pack": self.context_pack,
            }
        )


def _request(**overrides: Any) -> DraftRunCreateRequest:
    payload: Dict[str, Any] = {
        "mode": "draft",
        "draft_type": "fresh",
        "role": "contractor",
        "subject": f"Notice of {QUERY_TERM} of time",
        "recipient": "Engineer",
        "purpose": f"Record the delay and claim an {QUERY_TERM}.",
        "requirements": "State the contractual basis and the action required.",
        "trigger_event": "Access to the north zone was withheld.",
        "required_action": "Confirm revised access within seven days.",
        "points": f"Rely on the {QUERY_TERM} of time provisions.",
        "plan_override": "1. Facts. 2. Contractual basis. 3. Required action.",
    }
    payload.update(overrides)
    return DraftRunCreateRequest(**payload)


async def _create_run(
    monkeypatch: pytest.MonkeyPatch,
    *,
    world: _World,
    letters: List[Dict[str, Any]],
    letter_id: Any,
    actor: Any = None,
    request: Optional[DraftRunCreateRequest] = None,
    mutate_db: Any = None,
) -> _Run:
    """Drive the REAL v2 drafting service and its REAL DraftRun persistence.

    Only the parent-letter gate is stubbed, and it is stubbed to GRANT: that is
    precisely the premise under test - the actor IS authorised for the anchor
    letter. Nothing about the contract-evidence boundary is bypassed.
    """
    db = world.database(letters)
    if mutate_db is not None:
        mutate_db(db)
    _RecordingLLM.prompts = []
    _LegacyGenericSearch.calls = []

    async def _get_db():
        return db

    monkeypatch.setattr(conversation_service_module, "get_database", _get_db)
    monkeypatch.setattr(context_module, "ContractService", _LegacyGenericSearch)
    monkeypatch.setattr(context_module, "FalkorGraphService", lambda *a, **k: _DisabledFalkor())
    monkeypatch.setattr(generator_module, "LLMGenerator", _RecordingLLM)

    service = DraftRunService(db)

    async def _parent_letter_is_authorised(target_id: str, *_a: Any, **_k: Any):
        return await service.letter_service.get_letter(str(target_id))

    monkeypatch.setattr(service, "_load_and_authorize", _parent_letter_is_authorised)

    run = await service.create_run(
        str(letter_id), request or _request(), actor or _project_actor()
    )
    return _Run(run, db, list(_RecordingLLM.prompts))


def _assert_absent(run: _Run, marker: str, why: str) -> None:
    for surface, text in run.surfaces().items():
        assert marker not in text, (
            f"{why}: the marker {marker!r} reached {surface}. A clause row's own "
            "org_id/contract_id is ingest provenance, not legal applicability - "
            "only the canonical Contract Master universe may admit a clause."
        )


def _assert_present(run: _Run, marker: str, why: str) -> None:
    combined = "\n".join(run.surfaces().values())
    assert marker in combined, (
        f"{why}: the authorised marker {marker!r} reached NONE of the drafting "
        "surfaces. Containment that empties contract evidence is an outage, not "
        "a boundary."
    )


def _assert_document_absent(run: _Run, document_id: str, why: str) -> None:
    assert document_id not in run.provenance(), (
        f"{why}: the ineligible document id {document_id!r} survived in the "
        "persisted DraftRun provenance. Removing the text while keeping the id "
        "still records an unauthorised source as a contributor, and the evidence "
        "snapshot is immutable."
    )


# ---------------------------------------------------------------------------
# The canonical universe itself - the machinery drafting must consume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_canonical_universe_separates_two_contracts() -> None:
    """Exact ``(project_id, contract_id)`` identity, not ``contract_id`` alone.

    This is asserted at the resolver because the drafting consumer carries no
    contract identifier (the recorded gap). The identity machinery it consumes
    must still be exact, or the gap would be unbounded rather than bounded to
    "sibling contracts inside one authorised project".
    """
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.instrument(document_id="doc-B-MAIN", project_id="proj-B", contract_id="MAIN")
    world.instrument(document_id="doc-A-OTHER", project_id="proj-A", contract_id="OTHER")
    db = world.database([])

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


@pytest.mark.asyncio
async def test_an_unentitled_actor_gets_an_empty_universe_not_a_wider_one() -> None:
    """Denial is an ANSWER (empty), never a reason to widen."""
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    db = world.database([])

    outsider = SimpleNamespace(
        id="user-outsider",
        roles=["projectuser"],
        organization_id="org-A",
        organizations=["org-A"],
        projects=["proj-Z"],
    )
    universe = await resolve_authorized_project_universe(
        db, outsider, organization_id="org-A", project_id="proj-A", mode=CurrentState()
    )
    assert universe.eligible_document_ids == frozenset()


@pytest.mark.asyncio
async def test_a_resolution_failure_raises_rather_than_returning_empty() -> None:
    """An outage must never be able to impersonate "nothing applies"."""
    world = _World()
    world.instrument(document_id="doc-A-MAIN")
    db = world.database([])
    db[APPLICABILITY].find_raises = RuntimeError("applicability store unavailable")

    with pytest.raises(ContractScopeResolutionError):
        await resolve_authorized_project_universe(
            db,
            _project_actor(),
            organization_id="org-A",
            project_id="proj-A",
            mode=CurrentState(),
        )


# ---------------------------------------------------------------------------
# RED 14 (positive) - the boundary must not be a switch-off
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_eligible_contract_clause_still_grounds_the_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    anchor = ObjectId()

    run = await _create_run(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    _assert_present(run, AUTHORISED_MARKER, "an applicable, current, consumable clause")


# ---------------------------------------------------------------------------
# RED 1 / RED 9 - sibling project, same contract_id, LYING clause provenance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_sibling_project_clause_cannot_authorise_itself_by_row_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Project A / contract MAIN and Project B / contract MAIN both exist.

    The Project-B clause ROW is stamped ``org-A``/``proj-A`` - which is exactly
    what a mis-ingested or cross-linked row looks like, and exactly what the old
    query trusted. Its canonical instrument is applicable only to Project B, so
    it must have zero material influence.
    """
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.instrument(document_id="doc-B-MAIN", project_id="proj-B", contract_id="MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.clause(
        document_id="doc-B-MAIN",
        text=SIBLING_PROJECT_MARKER,
        org_id="org-A",
        project_id="proj-A",
    )
    anchor = ObjectId()

    run = await _create_run(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    _assert_absent(run, SIBLING_PROJECT_MARKER, "sibling-project contract clause")
    _assert_document_absent(run, "doc-B-MAIN", "sibling-project contract clause")
    _assert_present(run, AUTHORISED_MARKER, "the lawful clause beside the unlawful one")


# ---------------------------------------------------------------------------
# RED 3 - catalogued in the organisation, applicable to nothing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_catalogue_document_with_no_applicability_is_not_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.instrument(document_id="doc-catalogue", project_id=None, contract_id=None)
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.clause(document_id="doc-catalogue", text=NO_APPLICABILITY_MARKER)
    anchor = ObjectId()

    run = await _create_run(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    _assert_absent(run, NO_APPLICABILITY_MARKER, "a catalogue-only contract document")
    _assert_document_absent(run, "doc-catalogue", "a catalogue-only contract document")


# ---------------------------------------------------------------------------
# RED 4 - lifecycle: withdrawn / superseded applicability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_withdrawn_instrument_does_not_govern_the_current_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.instrument(
        document_id="doc-withdrawn",
        project_id="proj-A",
        contract_id="MAIN",
        lifecycle=("APPLIED", "WITHDRAWN"),
    )
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.clause(document_id="doc-withdrawn", text=WITHDRAWN_MARKER)
    anchor = ObjectId()

    run = await _create_run(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    _assert_absent(run, WITHDRAWN_MARKER, "a withdrawn instrument")
    _assert_document_absent(run, "doc-withdrawn", "a withdrawn instrument")


@pytest.mark.asyncio
async def test_a_superseded_instrument_does_not_govern_the_current_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.instrument(
        document_id="doc-superseded",
        project_id="proj-A",
        contract_id="MAIN",
        lifecycle=("APPLIED", "SUPERSEDED"),
    )
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.clause(document_id="doc-superseded", text=WITHDRAWN_MARKER)
    anchor = ObjectId()

    run = await _create_run(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    _assert_absent(run, WITHDRAWN_MARKER, "a superseded instrument")


# ---------------------------------------------------------------------------
# RED 5 / RED 6 - publication authority on the CANONICAL document
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_clause_of_a_human_review_document_cannot_influence_a_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.instrument(
        document_id="doc-review",
        project_id="proj-A",
        contract_id="MAIN",
        processing_status="human_review_required",
    )
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.clause(document_id="doc-review", text=HUMAN_REVIEW_MARKER)
    anchor = ObjectId()

    run = await _create_run(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    _assert_absent(run, HUMAN_REVIEW_MARKER, "a document in human review")
    _assert_document_absent(run, "doc-review", "a document in human review")


@pytest.mark.asyncio
async def test_a_clause_of_a_duplicate_document_cannot_influence_a_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.instrument(
        document_id="doc-duplicate",
        project_id="proj-A",
        contract_id="MAIN",
        duplicate_status="duplicate",
    )
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.clause(document_id="doc-duplicate", text=DUPLICATE_MARKER)
    anchor = ObjectId()

    run = await _create_run(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    _assert_absent(run, DUPLICATE_MARKER, "a confirmed duplicate document")


@pytest.mark.asyncio
async def test_a_clause_of_a_deleted_document_cannot_influence_a_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.instrument(
        document_id="doc-deleted",
        project_id="proj-A",
        contract_id="MAIN",
        lifecycle_state="deleted",
    )
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.clause(document_id="doc-deleted", text=DELETED_MARKER)
    anchor = ObjectId()

    run = await _create_run(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    _assert_absent(run, DELETED_MARKER, "a deleted document")


# ---------------------------------------------------------------------------
# RED 7 - Model-B: an OPERATIONAL failure is not an adverse verdict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_operationally_failed_document_keeps_its_last_known_good_clause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``processing_status=failed`` means the worker broke, not that the content
    is bad. Excluding it here would turn an outage into silent evidence loss."""
    world = _World()
    world.instrument(
        document_id="doc-operational",
        project_id="proj-A",
        contract_id="MAIN",
        processing_status="failed",
    )
    world.clause(document_id="doc-operational", text=OPERATIONAL_FAILED_MARKER)
    anchor = ObjectId()

    run = await _create_run(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    _assert_present(
        run, OPERATIONAL_FAILED_MARKER, "an operational processing failure (last known good)"
    )


# ---------------------------------------------------------------------------
# RED 8 - the orphan clause
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_orphan_clause_whose_document_does_not_resolve_is_excluded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No canonical ``documents`` row exists for this identifier.

    The subtractive ``blocked_document_ids`` helper fails OPEN on exactly this
    case; the positive universe fails closed, because the row was never in it.
    """
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.instrument(
        document_id="doc-orphan",
        project_id="proj-A",
        contract_id="MAIN",
        canonical_document=False,
    )
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.clause(document_id="doc-orphan", text=ORPHAN_MARKER)
    anchor = ObjectId()

    run = await _create_run(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    _assert_absent(run, ORPHAN_MARKER, "an orphaned clause")
    _assert_document_absent(run, "doc-orphan", "an orphaned clause")


# ---------------------------------------------------------------------------
# RED 10 / RED 11 - projection currency, both halves independently
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_stale_projection_cannot_influence_a_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Status claims CURRENT; the revision is one behind. Both halves count."""
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.instrument(
        document_id="doc-stale",
        project_id="proj-A",
        contract_id="MAIN",
        classification_revision=4,
        projection_revision=3,
        projection_status="CURRENT",
    )
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.clause(document_id="doc-stale", text=STALE_PROJECTION_MARKER)
    anchor = ObjectId()

    run = await _create_run(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    _assert_absent(run, STALE_PROJECTION_MARKER, "a stale clause projection")
    _assert_document_absent(run, "doc-stale", "a stale clause projection")


@pytest.mark.asyncio
async def test_a_pending_projection_cannot_influence_a_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Revisions match; the projection has not been rebuilt. Still ineligible."""
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.instrument(
        document_id="doc-pending",
        project_id="proj-A",
        contract_id="MAIN",
        projection_status="PENDING",
    )
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.clause(document_id="doc-pending", text=PENDING_PROJECTION_MARKER)
    anchor = ObjectId()

    run = await _create_run(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    _assert_absent(run, PENDING_PROJECTION_MARKER, "a PENDING clause projection")


@pytest.mark.asyncio
async def test_a_failed_projection_cannot_influence_a_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.instrument(
        document_id="doc-projfailed",
        project_id="proj-A",
        contract_id="MAIN",
        projection_status="FAILED",
    )
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.clause(document_id="doc-projfailed", text=FAILED_PROJECTION_MARKER)
    anchor = ObjectId()

    run = await _create_run(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    _assert_absent(run, FAILED_PROJECTION_MARKER, "a FAILED clause projection")


# ---------------------------------------------------------------------------
# RED 12 - candidate capacity: containment must precede the source's own limit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ineligible_clauses_cannot_starve_the_eligible_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The structured clause source fetches a bounded window before it ranks.

    Seventy ineligible rows are seeded FIRST, so a window taken before the
    canonical fence never reaches the eligible row at all - the eligible clause
    is starved out by rows that were never allowed to compete.
    """
    world = _World()
    world.instrument(
        document_id="doc-noise",
        project_id="proj-A",
        contract_id="MAIN",
        lifecycle=("APPLIED", "WITHDRAWN"),
    )
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    for index in range(70):
        world.clause(
            document_id="doc-noise",
            text=f"{CAPACITY_FILLER_MARKER} filler {index}",
            uid=f"noise-{index:03d}",
        )
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER, uid="eligible-last")
    anchor = ObjectId()

    run = await _create_run(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    _assert_absent(run, CAPACITY_FILLER_MARKER, "70 ineligible high-ranking clauses")
    _assert_present(
        run,
        AUTHORISED_MARKER,
        "the eligible clause behind 70 ineligible ones (containment must run "
        "BEFORE the candidate window, not after it)",
    )


# ---------------------------------------------------------------------------
# RED 16 - valid empty never broadens
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_applicable_contract_yields_valid_empty_not_a_broader_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clause rows exist and claim the workspace; nothing is applicable.

    "Nothing applies" is a legitimate answer. Falling back to the rows' own
    provenance, or to generic contract search, would turn it into a leak.
    """
    world = _World()
    world.clause(document_id="doc-unknown", text=NO_APPLICABILITY_MARKER)
    anchor = ObjectId()

    run = await _create_run(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    _assert_absent(run, NO_APPLICABILITY_MARKER, "a valid-empty contract universe")
    _assert_absent(run, LEGACY_SEARCH_MARKER, "a valid-empty contract universe")
    assert not _LegacyGenericSearch.calls, (
        "generic ContractService.search_contracts was called from the drafting "
        "evidence chain. Generic search is not evidence mode: it resolves no "
        "applicability, no projection currency and no positive publication "
        "authority, and it spends its candidate limit before any of them exist."
    )


# ---------------------------------------------------------------------------
# RED 17 - an authority failure never broadens
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_authority_failure_issues_no_broad_clause_retrieval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.clause(document_id="doc-unknown", text=ORPHAN_MARKER)
    anchor = ObjectId()

    def _break_applicability(db: _Database) -> None:
        db[APPLICABILITY].find_raises = RuntimeError("applicability store unavailable")

    run = await _create_run(
        monkeypatch,
        world=world,
        letters=[_letter(anchor)],
        letter_id=anchor,
        mutate_db=_break_applicability,
    )

    _assert_absent(run, AUTHORISED_MARKER, "an authority-resolution failure")
    _assert_absent(run, ORPHAN_MARKER, "an authority-resolution failure")
    assert not run.db["contract_clauses"].queries, (
        "the clause store was queried after contract eligibility could not be "
        "resolved. An authority failure has no partial answer and no legacy "
        "path to fall back to."
    )
    assert not _LegacyGenericSearch.calls, (
        "generic contract search ran after an authority failure - that is the "
        "exact door the Contract Master model closes."
    )


# ---------------------------------------------------------------------------
# RED 15 - a global actor keeps canonical breadth, resolved not named
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_global_actor_keeps_exactly_the_canonical_breadth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.instrument(document_id="doc-B-MAIN", project_id="proj-B", contract_id="MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.clause(
        document_id="doc-B-MAIN",
        text=SIBLING_PROJECT_MARKER,
        org_id="org-A",
        project_id="proj-A",
    )
    anchor = ObjectId()

    run = await _create_run(
        monkeypatch,
        world=world,
        letters=[_letter(anchor)],
        letter_id=anchor,
        actor=_global_actor(),
    )

    _assert_present(run, AUTHORISED_MARKER, "a global actor drafting in project A")
    _assert_absent(
        run,
        SIBLING_PROJECT_MARKER,
        "a global actor drafting in project A (breadth comes from the ANCHORED "
        "project's canonical universe, never from the actor's role name)",
    )


# ---------------------------------------------------------------------------
# The legacy generic-search seam - removed from the drafting evidence chain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generic_contract_search_is_not_part_of_the_drafting_evidence_chain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``search_contracts`` resolves no applicability at all.

    Its answer is scoped by the LETTER's workspace and by the actor's coarse
    org/project entitlement, and by nothing in the Contract Master model. A
    chunk it returns is therefore unauthorised by construction, whatever the
    canonical universe says.
    """
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    anchor = ObjectId()

    run = await _create_run(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    _assert_absent(run, LEGACY_SEARCH_MARKER, "the legacy generic contract search")
    _assert_document_absent(run, "doc-generic-search", "the legacy generic contract search")
    assert not _LegacyGenericSearch.calls, (
        "generic ContractService.search_contracts is still called by the "
        "drafting evidence chain."
    )


# ---------------------------------------------------------------------------
# RED 13 - the persisted DraftRun carries only eligible provenance
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_persisted_draft_run_records_only_eligible_clause_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every ineligible shape at once, asserted on the DURABLE surfaces.

    The evidence snapshot is immutable, so an id kept "for debugging" is a
    permanent record that an unauthorised instrument contributed to this draft.
    """
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.instrument(document_id="doc-B-MAIN", project_id="proj-B", contract_id="MAIN")
    world.instrument(document_id="doc-catalogue", project_id=None, contract_id=None)
    world.instrument(
        document_id="doc-review",
        project_id="proj-A",
        contract_id="MAIN",
        processing_status="human_review_required",
    )
    world.instrument(
        document_id="doc-stale",
        project_id="proj-A",
        contract_id="MAIN",
        classification_revision=9,
        projection_revision=8,
    )
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    for document_id, marker in (
        ("doc-B-MAIN", SIBLING_PROJECT_MARKER),
        ("doc-catalogue", NO_APPLICABILITY_MARKER),
        ("doc-review", HUMAN_REVIEW_MARKER),
        ("doc-stale", STALE_PROJECTION_MARKER),
    ):
        world.clause(document_id=document_id, text=marker)
    anchor = ObjectId()

    run = await _create_run(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    for document_id in ("doc-B-MAIN", "doc-catalogue", "doc-review", "doc-stale"):
        _assert_document_absent(run, document_id, "the combined ineligible set")
    for marker in (
        SIBLING_PROJECT_MARKER,
        NO_APPLICABILITY_MARKER,
        HUMAN_REVIEW_MARKER,
        STALE_PROJECTION_MARKER,
    ):
        _assert_absent(run, marker, "the combined ineligible set")

    stored_clause_sources = [
        source
        for source in (run.stored_row.get("sources") or [])
        if source.get("source_type") == "contract_clause"
    ]
    assert stored_clause_sources, (
        "the persisted run recorded NO contract clause at all - the eligible "
        "clause must still be a recorded contributor"
    )
    for source in stored_clause_sources:
        assert source.get("document_id") == "doc-A-MAIN"


# ---------------------------------------------------------------------------
# R17 is preserved, not "fixed" here
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_rootless_worker_principal_admits_no_contract_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The v3 engine synthesises its principal from the LETTER it is reading.

    That principal carries no roles, so canonical authorisation denies it and
    the contract universe is empty. Fail-closed, and pinned HERE so nobody
    "fixes" the resulting v3 evidence gap by widening the fabricated actor -
    authority derived from the resource being read is circular. R17 is carried
    separately and must be closed by passing the REAL actor.
    """
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    anchor = ObjectId()

    fabricated = SimpleNamespace(
        id="user-A", organization_id="org-A", project_id="proj-A"
    )
    run = await _create_run(
        monkeypatch,
        world=world,
        letters=[_letter(anchor)],
        letter_id=anchor,
        actor=fabricated,
    )

    _assert_absent(
        run,
        AUTHORISED_MARKER,
        "a principal fabricated from the letter being drafted (no roles, so "
        "canonical authorisation denies it)",
    )


# ---------------------------------------------------------------------------
# The refinement seam - the second door into the same persisted DraftRun
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_refinement_retrieval_admits_only_eligible_clauses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cyclic redraft loop re-queries clauses and MERGES what it finds into
    ``working_sources`` - the same list that is persisted into the ``DraftRun``,
    the evidence snapshot and the context pack. A second retrieval door on the
    same persistence chain needs the same canonical universe."""
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.instrument(
        document_id="doc-refine-blocked",
        project_id="proj-A",
        contract_id="MAIN",
        processing_status="human_review_required",
    )
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER, clause_no="8.4")
    world.clause(
        document_id="doc-refine-blocked", text=REFINEMENT_MARKER, clause_no="8.4"
    )
    db = world.database([])

    monkeypatch.setattr(context_module, "ContractService", _LegacyGenericSearch)
    _LegacyGenericSearch.calls = []

    service = DraftRunService(db)
    context = SimpleNamespace(
        active_workspace={"organization_id": "org-A", "project_id": "proj-A"}
    )

    sources = await service._retrieve_refinement_sources(
        ["8.4"], context, _project_actor()
    )

    rendered = repr([source.model_dump(mode="json") for source in sources])
    assert REFINEMENT_MARKER not in rendered, (
        "the refinement loop admitted a clause whose canonical document is in "
        "human review, and that source is merged into the persisted DraftRun"
    )
    assert "doc-refine-blocked" not in rendered, (
        "the refinement loop kept an ineligible document id as provenance"
    )
    assert LEGACY_SEARCH_MARKER not in rendered, (
        "the refinement loop still reaches the generic contract search path"
    )
    assert AUTHORISED_MARKER in rendered, (
        "the refinement loop returned nothing for an ELIGIBLE clause - "
        "containment that empties refinement is an outage, not a boundary"
    )
