"""G-A8 / R18: the LangGraph letter pipeline may only draft from AUTHORISED clauses.

``LetterDraftGraph.run``'s ``retrieve_sources`` node built its contract evidence
by calling the **generic** ``ContractService.search_contracts`` and turning every
returned chunk into a ``DraftSource(source_type="contract_clause")``. Generic
search is explicitly not evidence mode:

* it resolves no applicability - an instrument catalogued in the organisation but
  never APPLIED to the drafting project is indistinguishable from one that
  governs it;
* it resolves no projection currency - a clause projection a revision behind, or
  PENDING, or FAILED, contributes exactly like a current one;
* it applies no positive per-document publication authority - its subtractive
  helper fails OPEN on an identifier it cannot resolve, so an ORPHAN chunk whose
  canonical ``Document`` no longer resolves is admitted;
* and it spends its ``candidate_limit`` BEFORE any of those exist, so ineligible
  high-scoring chunks starve an applicable clause out of the window entirely.

This is the same defect class G-A7 closed for drafting v2, in a different engine
with a different persistence tail. That tail is what makes it material rather
than transient: ``AIService.generate_draft_with_langgraph`` calls
``LetterService.record_langgraph_result``, which ``$set``s ``draft_sources`` onto
the letter and ``$push``es an immutable ``draft_versions`` entry. Unauthorised
clause text and unauthorised clause PROVENANCE therefore become durable state
that every later reader inherits without revisiting the original authority.

The accepted universe is positive and per contract::

      applicable to (project_id, contract_id) at the query mode
    & canonical Document positively resolvable
    & publication-consumable
    & projection-current

resolved by ``ContractScopeResolver`` for an actor who is canonically entitled to
the workspace. The pipeline must CONSUME that; it may not reconstruct it, and it
may not fall back to generic search when it is empty or unavailable.

**Recorded gap, carried from G-A7, not silently narrowed.** Neither ``Letter``
nor ``LangGraphDraftRequest`` carries a contract identifier, so this consumer
cannot ask the per-``(project_id, contract_id)`` question the frozen model is
built around. The containment is therefore **PROJECT-EVIDENCE CONTAINMENT**: it
unions every contract that has applicability in the AUTHORISED project. A clause
governing sibling contract ``OTHER`` may still ground a draft about ``MAIN``.
That residual is bounded and is pinned at the resolver
(:func:`test_the_canonical_universe_separates_two_contracts`) so the identity
machinery stays exact; closing it needs contract identity on the drafting
request, which is an owner decision.

Every containment test asserts on FOUR surfaces, following G-A5:

1. the captured LLM prompts - proving filtering precedes prompt construction AND
   model invocation. A source stripped after the model saw it is already a leak;
2. the returned response;
3. the re-read persisted letter row;
4. the persisted ``draft_versions`` entry.

Text and PROVENANCE are asserted separately: an ineligible ``document_id`` may
not survive in ``draft_sources`` merely because its clause text was removed.

The positives are load-bearing. ``processing_status == "failed"`` is OPERATIONAL
- the pipeline broke, which is not a verdict about the content - so a
last-known-good instrument must still contribute, and an applicable,
publication-consumable, projection-current clause must still reach the prompt. A
containment that empties contract evidence is an outage, not a boundary.
"""

from __future__ import annotations

import ast
import inspect
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pytest
from bson import ObjectId

from rbac_backend.ai_workflows.langgraph import letter_pipeline
from rbac_backend.models.ai_models import LangGraphDraftRequest
from rbac_backend.models.contract_document import CurrentState
from rbac_backend.services import ai_service as ai_service_module
from rbac_backend.services import conversation_service as conversation_service_module
from rbac_backend.services.ai_service import AIService
from rbac_backend.services.contract_scope_resolver import (
    ContractScopeResolutionError,
    resolve_authorized_project_universe,
)
from rbac_backend.services.falkor_graph_service import normalize_letter_code

# --- unique markers -------------------------------------------------------
#
# Every marker is unique, so an assertion can never pass because the string
# happened to be absent for an unrelated reason.

AUTHORISED_MARKER = "LANGGRAPH_PROJECT_A_CONTRACT_AUTHORISED_20260831"
SIBLING_PROJECT_MARKER = "LANGGRAPH_PROJECT_B_CONTRACT_CONFIDENTIAL_20260831"
NO_APPLICABILITY_MARKER = "LANGGRAPH_CATALOGUE_ONLY_CONFIDENTIAL_20260831"
WITHDRAWN_MARKER = "LANGGRAPH_WITHDRAWN_INSTRUMENT_CONFIDENTIAL_20260831"
SUPERSEDED_MARKER = "LANGGRAPH_SUPERSEDED_INSTRUMENT_CONFIDENTIAL_20260831"
HUMAN_REVIEW_MARKER = "LANGGRAPH_HUMAN_REVIEW_CONFIDENTIAL_20260831"
DUPLICATE_MARKER = "LANGGRAPH_DUPLICATE_CLAUSE_CONFIDENTIAL_20260831"
DELETED_MARKER = "LANGGRAPH_DELETED_CLAUSE_CONFIDENTIAL_20260831"
OPERATIONAL_FAILED_MARKER = "LANGGRAPH_OPERATIONAL_FAILED_LAST_KNOWN_GOOD_20260831"
ORPHAN_MARKER = "LANGGRAPH_ORPHAN_CLAUSE_CONFIDENTIAL_20260831"
LYING_CLAUSE_MARKER = "LANGGRAPH_LYING_CLAUSE_CONFIDENTIAL_20260831"
STALE_PROJECTION_MARKER = "LANGGRAPH_STALE_PROJECTION_CONFIDENTIAL_20260831"
PENDING_PROJECTION_MARKER = "LANGGRAPH_PENDING_PROJECTION_CONFIDENTIAL_20260831"
FAILED_PROJECTION_MARKER = "LANGGRAPH_FAILED_PROJECTION_CONFIDENTIAL_20260831"
CAPACITY_FILLER_MARKER = "LANGGRAPH_CAPACITY_FILLER_CONFIDENTIAL_20260831"
CAPACITY_ELIGIBLE_MARKER = "LANGGRAPH_CAPACITY_ELIGIBLE_AUTHORISED_20260831"
LEGACY_SEARCH_MARKER = "LANGGRAPH_LEGACY_GENERIC_SEARCH_CONFIDENTIAL_20260831"

#: The one query term every seeded clause shares, so relevance is never the
#: reason a row is absent. Every seeded clause scores; only authority may
#: remove one.
QUERY_TERM = "extension"

APPLICABILITY = "contract_document_applicability"
APPLICABILITY_EVENTS = "contract_document_applicability_events"
CONTRACT_DOCUMENTS = "contract_documents"

SHARED_CODE = "GA8-ANCHOR-20260831"


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

        Several tests deliberately stamp a row with the drafting workspace while
        its canonical instrument says otherwise - that is the lying-provenance
        case, and the row must not be able to authorise itself.
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


# ---------------------------------------------------------------------------
# Stubs - everything outside this boundary is held still on purpose
# ---------------------------------------------------------------------------


class _DisabledFalkor:
    """The graph is a different seam (pinned by test_graph_consumer_authority)."""

    enabled = False

    def get_thread(self, *_a: Any, **_k: Any) -> List[Dict[str, Any]]:  # pragma: no cover
        return []

    def upsert_letter_with_refs(self, *_a: Any, **_k: Any) -> None:  # pragma: no cover
        return None


def _chunk(row: Dict[str, Any], index: int, score: float) -> SimpleNamespace:
    return SimpleNamespace(
        document_id=row.get("document_id"),
        upload_id=row.get("document_id"),
        clause_number=row.get("clause_no"),
        clause_title=row.get("clause_title"),
        text=row.get("cleaned_text"),
        chunk_index=index,
        page_numbers=[row.get("page_start")],
        page=row.get("page_start"),
        score=score,
        file_name="contract.pdf",
        source_filename="contract.pdf",
        file_path=None,
        source_file=None,
    )


class _LegacyGenericSearch:
    """A faithful model of the generic ``search_contracts`` seam.

    Faithful means: it reproduces the two properties that make generic search
    the wrong door for evidence, and NOT more than that, so a RED here fails for
    a reason production actually has.

    1. **Row provenance is the only scope.** Generic search's match stage is
       ``{organization_id, project_id}`` taken from the REQUEST - which the
       pipeline fills from the anchor letter's workspace - applied to the
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
            for row in db["contract_clauses"].rows
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
                file_path=None,
                source_file=None,
            )
        ]
        results.extend(
            _chunk(row, index, 0.9 - index * 0.001) for index, row in enumerate(rows)
        )
        limit = int(getattr(request, "limit", 6) or 6)
        return SimpleNamespace(results=results[:limit])


class _NoRetrieval:
    """The letter vector store is a different seam; keep it out of the run.

    Left real it reaches a live Qdrant, which makes an authority assertion
    depend on a network service being down in the right way.
    """

    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    async def search(self, *_a: Any, **_k: Any):
        return SimpleNamespace(results=[])


class _RecordingLLM:
    """Captures every rendered prompt BEFORE an answer exists."""

    prompts: List[str] = []

    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    async def generate(self, prompt: str, **_k: Any) -> str:
        type(self).prompts.append(prompt)
        if "senior reviewer" in prompt.lower():
            return ""
        return "Deterministic source-grounded draft."


# ---------------------------------------------------------------------------
# Letters and actors
# ---------------------------------------------------------------------------


def _letter(
    letter_id: Any,
    *,
    organization_id: str = "org-A",
    project_id: Optional[str] = "proj-A",
    subject: str = "Notice of extension of time",
    content: str = "Prepare a source-grounded response about the extension of time.",
    letter_no: str = SHARED_CODE,
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
        "letter_no": letter_no,
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
    """Every place LangGraph drafting material can come to rest."""

    def __init__(
        self,
        response: Any,
        letter_row: Dict[str, Any],
        prompts: List[str],
        db: _Database,
    ) -> None:
        self.response = response
        self.letter_row = letter_row
        self.prompts = prompts
        self.db = db

    @property
    def draft_versions(self) -> List[Dict[str, Any]]:
        return self.letter_row.get("draft_versions") or []

    def surfaces(self) -> Dict[str, str]:
        return {
            "the LLM prompts (filtering must precede synthesis)": "\n".join(self.prompts),
            "the returned LangGraph response": repr(self.response.model_dump()),
            "the PERSISTED letter row": repr(self.letter_row),
            "the PERSISTED draft_versions snapshot": repr(self.draft_versions),
        }

    def provenance(self) -> str:
        return repr(
            {
                "response_sources": [
                    source.model_dump() for source in (self.response.sources or [])
                ],
                "letter_draft_sources": self.letter_row.get("draft_sources"),
                "context_document_ids": self.letter_row.get("context_document_ids"),
                "draft_versions_sources": [
                    version.get("sources") for version in self.draft_versions
                ],
            }
        )

    def clause_sources(self) -> List[Dict[str, Any]]:
        return [
            source
            for source in (self.letter_row.get("draft_sources") or [])
            if source.get("source_type") == "contract_clause"
        ]


async def _run_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    world: _World,
    letters: List[Dict[str, Any]],
    letter_id: Any,
    actor: Any = None,
    mutate_db: Any = None,
    subject: str = "Notice of extension of time",
) -> _Run:
    """Drive the REAL pipeline and the REAL persistence, exactly as the route does."""
    db = world.database(letters)
    if mutate_db is not None:
        mutate_db(db)
    _RecordingLLM.prompts = []
    _LegacyGenericSearch.calls = []
    _LegacyGenericSearch.db = db

    async def _get_db():
        return db

    monkeypatch.setattr(letter_pipeline, "get_database", _get_db)
    monkeypatch.setattr(conversation_service_module, "get_database", _get_db)
    monkeypatch.setattr(ai_service_module, "get_database", _get_db)
    monkeypatch.setattr(letter_pipeline, "FalkorGraphService", lambda *a, **k: _DisabledFalkor())
    monkeypatch.setattr(letter_pipeline, "LLMGenerator", _RecordingLLM)
    monkeypatch.setattr(letter_pipeline, "RetrievalService", _NoRetrieval)
    monkeypatch.setattr(letter_pipeline, "get_embedding_client", lambda *a, **k: None)
    monkeypatch.setattr(letter_pipeline, "get_vector_client", lambda *a, **k: None)
    monkeypatch.setattr(letter_pipeline, "get_llm_generator", lambda *a, **k: None)
    # The generic door, left OPEN and answering. Closing it in production is the
    # deliverable; stubbing it shut here would prove nothing. ``raising=False``
    # because the closed state is exactly the absence of the name - the
    # structural pin below asserts that separately, and a patch that required
    # the symbol would turn the fix into a collection error.
    monkeypatch.setattr(
        letter_pipeline, "ContractService", _LegacyGenericSearch, raising=False
    )

    request = LangGraphDraftRequest(
        letter_id=str(letter_id),
        subject=subject,
        recipient="Engineer",
        context=f"Respond on the contractual basis for an {QUERY_TERM} of time.",
        points=f"Rely on the {QUERY_TERM} of time provisions.",
        document_ids=[],
        organization_id="org-A",
        project_id="proj-A",
        # The contract-evidence branch is gated on this flag; the whole boundary
        # is unreachable with it off.
        use_vector_store=True,
    )

    response = await AIService().generate_draft_with_langgraph(request, actor or _project_actor())
    stored = await db["letters"].find_one({"_id": letter_id})
    return _Run(response, stored or {}, list(_RecordingLLM.prompts), db)


def _assert_absent(run: _Run, marker: str, why: str) -> None:
    for surface, text in run.surfaces().items():
        assert marker not in text, (
            f"{why}: the marker {marker!r} reached {surface}. A clause row's own "
            "org_id/project_id is ingest provenance, not legal applicability - "
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
        "persisted LangGraph provenance. Removing the text while keeping the id "
        "still records an unauthorised source as a contributor to this draft, "
        "and the pushed draft_versions entry is immutable."
    )


def _assert_generic_search_unused(run: _Run, why: str) -> None:
    assert not _LegacyGenericSearch.calls, (
        f"{why}: generic ContractService.search_contracts was called from the "
        "LangGraph evidence path. Generic search resolves no applicability, no "
        "projection currency and no positive publication authority, and it "
        "spends its candidate limit before any of them exist."
    )


# ---------------------------------------------------------------------------
# The canonical universe itself - the machinery the pipeline must consume
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_canonical_universe_separates_two_contracts() -> None:
    """Exact ``(project_id, contract_id)`` identity, not ``contract_id`` alone.

    RED 2 ("wrong contract, same project") is asserted HERE and not at the
    pipeline, because ``LangGraphDraftRequest`` carries no contract identifier:
    see the module docstring. Marking the consumer-level case OWNER DECISION /
    NOT ENFORCEABLE AT THE CURRENT REQUEST CONTRACT is only honest if the
    identity machinery underneath is still exact, which is what this pins.
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


# ---------------------------------------------------------------------------
# RED 16 (positive) - the boundary must not be a switch-off
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_eligible_contract_clause_still_grounds_the_langgraph_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    anchor = ObjectId()

    run = await _run_pipeline(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    _assert_present(
        run,
        AUTHORISED_MARKER,
        "an applicable, publication-consumable, projection-current clause",
    )
    assert run.clause_sources(), (
        "the authorised clause did not survive as a persisted contract_clause "
        "source; provenance must record what actually contributed"
    )
    _assert_generic_search_unused(run, "the eligible-clause case")


@pytest.mark.asyncio
async def test_the_lawful_clause_survives_beside_the_unlawful_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Containment is selective, not a blanket refusal when anything is wrong."""
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

    run = await _run_pipeline(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    _assert_present(run, AUTHORISED_MARKER, "the lawful clause beside an unlawful one")
    _assert_absent(run, SIBLING_PROJECT_MARKER, "a sibling project's clause")


# ---------------------------------------------------------------------------
# RED 1 - sibling project, same contract_id
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_sibling_projects_contract_clause_cannot_reach_the_langgraph_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Project B's instrument, same ``contract_id``, for an actor confined to A.

    The derived row is stamped with the DRAFTING workspace, which is the only
    shape in which a sibling instrument's clause can present itself to a query
    scoped by row provenance - and it is a shape that occurs, because the stamp
    is written at ingest and is never revisited when the parent document's
    applicability changes. Seeding it honestly stamped would make the test pass
    for the wrong reason (the provenance filter, not the authority) and would
    stay green under every mutation.
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

    run = await _run_pipeline(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    _assert_absent(run, SIBLING_PROJECT_MARKER, "a sibling project's contract clause")
    _assert_document_absent(run, "doc-B-MAIN", "a sibling project's contract clause")
    _assert_present(
        run,
        AUTHORISED_MARKER,
        "the eligible companion instrument seeded beside the excluded one",
    )


# ---------------------------------------------------------------------------
# RED 9 - the lying clause row
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_clause_row_cannot_authorise_itself_by_claiming_the_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The row asserts BOTH identifiers and the canonical records disagree.

    ``project_id="proj-A"`` and ``contract_id="MAIN"`` are stamped on the clause
    row itself, exactly the pair the frozen model is keyed on, while the
    canonical ``ContractDocument``/applicability records say the instrument
    governs project B. Derived provenance may NARROW a query or carry
    attribution; it may never establish that an instrument legally governs
    anything, and that has to hold for ``contract_id`` as well as
    ``project_id``.
    """
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.instrument(document_id="doc-B-MAIN", project_id="proj-B", contract_id="MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.clause(
        document_id="doc-B-MAIN",
        text=LYING_CLAUSE_MARKER,
        org_id="org-A",
        project_id="proj-A",
        contract_id="MAIN",
    )
    anchor = ObjectId()

    run = await _run_pipeline(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    _assert_absent(run, LYING_CLAUSE_MARKER, "a clause row lying about its workspace")
    _assert_document_absent(run, "doc-B-MAIN", "a clause row lying about its workspace")
    _assert_present(
        run,
        AUTHORISED_MARKER,
        "the eligible companion instrument seeded beside the excluded one",
    )


# ---------------------------------------------------------------------------
# RED 3 - zero applicability
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_organisation_catalogue_instrument_with_no_applicability_is_excluded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.instrument(document_id="doc-catalogue", project_id=None, contract_id=None)
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.clause(document_id="doc-catalogue", text=NO_APPLICABILITY_MARKER)
    anchor = ObjectId()

    run = await _run_pipeline(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    _assert_absent(
        run,
        NO_APPLICABILITY_MARKER,
        "an instrument catalogued in the organisation but APPLIED to no project",
    )
    _assert_document_absent(run, "doc-catalogue", "a zero-applicability instrument")
    _assert_present(run, AUTHORISED_MARKER, "the applicable instrument beside it")


# ---------------------------------------------------------------------------
# RED 4 - withdrawn / superseded at CurrentState
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_withdrawn_instrument_does_not_govern_the_current_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.instrument(
        document_id="doc-withdrawn",
        project_id="proj-A",
        contract_id="MAIN",
        lifecycle=("APPLIED", "WITHDRAWN"),
    )
    world.clause(document_id="doc-withdrawn", text=WITHDRAWN_MARKER)
    anchor = ObjectId()

    run = await _run_pipeline(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    _assert_absent(
        run,
        WITHDRAWN_MARKER,
        "an instrument WITHDRAWN from this project (query mode is CurrentState)",
    )
    _assert_document_absent(run, "doc-withdrawn", "a withdrawn instrument")
    _assert_present(
        run,
        AUTHORISED_MARKER,
        "the eligible companion instrument seeded beside the excluded one",
    )


@pytest.mark.asyncio
async def test_a_superseded_instrument_does_not_govern_the_current_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.instrument(
        document_id="doc-superseded",
        project_id="proj-A",
        contract_id="MAIN",
        lifecycle=("APPLIED", "SUPERSEDED"),
    )
    world.clause(document_id="doc-superseded", text=SUPERSEDED_MARKER)
    anchor = ObjectId()

    run = await _run_pipeline(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    _assert_absent(run, SUPERSEDED_MARKER, "an instrument SUPERSEDED for this project")
    _assert_document_absent(run, "doc-superseded", "a superseded instrument")
    _assert_present(
        run,
        AUTHORISED_MARKER,
        "the eligible companion instrument seeded beside the excluded one",
    )


# ---------------------------------------------------------------------------
# RED 5 / RED 6 - publication authority
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_human_review_document_cannot_ground_the_langgraph_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.instrument(
        document_id="doc-human-review",
        project_id="proj-A",
        contract_id="MAIN",
        processing_status="human_review_required",
    )
    world.clause(document_id="doc-human-review", text=HUMAN_REVIEW_MARKER)
    anchor = ObjectId()

    run = await _run_pipeline(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    _assert_absent(run, HUMAN_REVIEW_MARKER, "a document held for human review")
    _assert_document_absent(run, "doc-human-review", "a document held for human review")
    _assert_present(
        run,
        AUTHORISED_MARKER,
        "the eligible companion instrument seeded beside the excluded one",
    )


@pytest.mark.asyncio
async def test_a_duplicate_document_cannot_ground_the_langgraph_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.instrument(
        document_id="doc-duplicate",
        project_id="proj-A",
        contract_id="MAIN",
        duplicate_status="duplicate",
    )
    world.clause(document_id="doc-duplicate", text=DUPLICATE_MARKER)
    anchor = ObjectId()

    run = await _run_pipeline(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    _assert_absent(run, DUPLICATE_MARKER, "a document marked duplicate")
    _assert_document_absent(run, "doc-duplicate", "a document marked duplicate")
    _assert_present(
        run,
        AUTHORISED_MARKER,
        "the eligible companion instrument seeded beside the excluded one",
    )


@pytest.mark.asyncio
async def test_a_deleted_document_cannot_ground_the_langgraph_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.instrument(
        document_id="doc-deleted",
        project_id="proj-A",
        contract_id="MAIN",
        lifecycle_state="deleted",
    )
    world.clause(document_id="doc-deleted", text=DELETED_MARKER)
    anchor = ObjectId()

    run = await _run_pipeline(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    _assert_absent(run, DELETED_MARKER, "a deleted document")
    _assert_document_absent(run, "doc-deleted", "a deleted document")
    _assert_present(
        run,
        AUTHORISED_MARKER,
        "the eligible companion instrument seeded beside the excluded one",
    )


# ---------------------------------------------------------------------------
# RED 7 - operational failure is NOT a content verdict (Model B)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_operationally_failed_document_keeps_its_last_known_good_clause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``processing_status == "failed"`` says the PIPELINE broke.

    It is not a verdict about the content, and denying it would be an outage
    dressed as a containment.
    """
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.instrument(
        document_id="doc-failed",
        project_id="proj-A",
        contract_id="MAIN",
        processing_status="failed",
    )
    world.clause(document_id="doc-failed", text=OPERATIONAL_FAILED_MARKER)
    anchor = ObjectId()

    run = await _run_pipeline(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    _assert_present(
        run,
        OPERATIONAL_FAILED_MARKER,
        "an OPERATIONALLY failed document's last-known-good clause",
    )


# ---------------------------------------------------------------------------
# RED 8 - the orphan derived clause
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_orphan_clause_whose_document_does_not_resolve_is_excluded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Positive resolution, not subtraction.

    ``blocked_document_ids`` fails OPEN on an identifier it cannot resolve - its
    own docstring calls an absent id "a different problem", which is right for
    generic search and wrong for legal evidence.
    """
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.instrument(
        document_id="doc-orphan",
        project_id="proj-A",
        contract_id="MAIN",
        canonical_document=False,
    )
    world.clause(document_id="doc-orphan", text=ORPHAN_MARKER)
    anchor = ObjectId()

    run = await _run_pipeline(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    _assert_absent(run, ORPHAN_MARKER, "a clause whose canonical Document does not resolve")
    _assert_document_absent(run, "doc-orphan", "an orphan clause")
    _assert_present(
        run,
        AUTHORISED_MARKER,
        "the eligible companion instrument seeded beside the excluded one",
    )


# ---------------------------------------------------------------------------
# RED 10 / RED 11 - projection currency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_stale_clause_projection_cannot_reach_the_langgraph_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``projection_status`` CURRENT is not enough; the REVISION must match."""
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.instrument(
        document_id="doc-stale",
        project_id="proj-A",
        contract_id="MAIN",
        classification_revision=4,
        projection_revision=3,
        projection_status="CURRENT",
    )
    world.clause(document_id="doc-stale", text=STALE_PROJECTION_MARKER)
    anchor = ObjectId()

    run = await _run_pipeline(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    _assert_absent(
        run,
        STALE_PROJECTION_MARKER,
        "a clause projection one revision behind its classification",
    )
    _assert_document_absent(run, "doc-stale", "a stale clause projection")
    _assert_present(
        run,
        AUTHORISED_MARKER,
        "the eligible companion instrument seeded beside the excluded one",
    )


@pytest.mark.asyncio
async def test_a_pending_clause_projection_cannot_reach_the_langgraph_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Matching revision, status PENDING - the projection is not usable yet."""
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.instrument(
        document_id="doc-pending",
        project_id="proj-A",
        contract_id="MAIN",
        projection_status="PENDING",
    )
    world.clause(document_id="doc-pending", text=PENDING_PROJECTION_MARKER)
    anchor = ObjectId()

    run = await _run_pipeline(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    _assert_absent(run, PENDING_PROJECTION_MARKER, "a PENDING clause projection")
    _assert_document_absent(run, "doc-pending", "a PENDING clause projection")
    _assert_present(
        run,
        AUTHORISED_MARKER,
        "the eligible companion instrument seeded beside the excluded one",
    )


@pytest.mark.asyncio
async def test_a_failed_clause_projection_cannot_reach_the_langgraph_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.instrument(
        document_id="doc-projection-failed",
        project_id="proj-A",
        contract_id="MAIN",
        projection_status="FAILED",
    )
    world.clause(document_id="doc-projection-failed", text=FAILED_PROJECTION_MARKER)
    anchor = ObjectId()

    run = await _run_pipeline(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    _assert_absent(run, FAILED_PROJECTION_MARKER, "a FAILED clause projection")
    _assert_document_absent(run, "doc-projection-failed", "a FAILED clause projection")
    _assert_present(
        run,
        AUTHORISED_MARKER,
        "the eligible companion instrument seeded beside the excluded one",
    )


# ---------------------------------------------------------------------------
# RED 12 - candidate capacity. The critical one.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ineligible_clauses_cannot_starve_the_eligible_one_out_of_the_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Containment must happen BEFORE the candidate limit, not after it.

    Seventy ineligible rows are seeded ahead of the one eligible row. If
    eligibility is applied to the RESULT of a bounded query, the window is spent
    before authority is consulted and the lawful clause never arrives - a leak
    that presents as an outage. Only a fence inside the query survives this.
    """
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.instrument(document_id="doc-B-MAIN", project_id="proj-B", contract_id="MAIN")
    for index in range(70):
        world.clause(
            document_id="doc-B-MAIN",
            text=f"{CAPACITY_FILLER_MARKER} {index}",
            org_id="org-A",
            project_id="proj-A",
            uid=f"filler-{index:03d}",
        )
    world.clause(
        document_id="doc-A-MAIN",
        text=CAPACITY_ELIGIBLE_MARKER,
        uid="eligible-last",
    )
    anchor = ObjectId()

    run = await _run_pipeline(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    _assert_absent(run, CAPACITY_FILLER_MARKER, "seventy ineligible clause rows")
    _assert_present(
        run,
        CAPACITY_ELIGIBLE_MARKER,
        "the one eligible clause, seeded LAST behind seventy ineligible rows",
    )


# ---------------------------------------------------------------------------
# RED 13 - valid empty
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_nothing_applicable_is_an_answer_and_never_widens_to_generic_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """"Nothing governs this project" is an ANSWER, not a reason to search wider."""
    world = _World()
    world.instrument(document_id="doc-catalogue", project_id=None, contract_id=None)
    world.clause(document_id="doc-catalogue", text=NO_APPLICABILITY_MARKER)
    anchor = ObjectId()

    run = await _run_pipeline(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    assert not run.clause_sources(), (
        "a valid-empty contract universe still produced contract_clause sources"
    )
    _assert_absent(run, NO_APPLICABILITY_MARKER, "a valid-empty universe")
    _assert_absent(run, LEGACY_SEARCH_MARKER, "a valid-empty universe")
    _assert_generic_search_unused(run, "a valid-empty universe")


# ---------------------------------------------------------------------------
# RED 14 - authority failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_authority_failure_issues_no_retrieval_at_all(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An outage must never be able to impersonate "nothing applies" - or widen.

    The assertion is recorded on the fake collection, not inferred from the
    absence of a marker: the clause store must not be QUERIED, and the generic
    door must not be opened, when eligibility cannot be resolved.
    """
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    anchor = ObjectId()

    def _break_applicability(db: _Database) -> None:
        db[APPLICABILITY].find_raises = RuntimeError("applicability store unavailable")

    run = await _run_pipeline(
        monkeypatch,
        world=world,
        letters=[_letter(anchor)],
        letter_id=anchor,
        mutate_db=_break_applicability,
    )

    assert not run.db["contract_clauses"].queries, (
        "the clause store was queried after canonical eligibility FAILED to "
        "resolve. There is no partial answer to give and no broader query that "
        "would be safer."
    )
    _assert_generic_search_unused(run, "an authority-resolution failure")
    assert not run.clause_sources(), (
        "an authority-resolution failure still produced contract_clause sources"
    )


@pytest.mark.asyncio
async def test_a_resolution_failure_raises_rather_than_returning_empty() -> None:
    """At the resolver itself: an outage is not an empty set."""
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
# RED 15 - durable material influence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_persisted_letter_records_only_eligible_clause_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tail is what makes this material rather than transient.

    ``record_langgraph_result`` ``$set``s ``draft_sources`` onto the letter and
    ``$push``es an immutable ``draft_versions`` entry. An ineligible clause id
    that survives there records an unauthorised contributor to this draft
    forever, whether or not its text survived with it.
    """
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    world.instrument(document_id="doc-B-MAIN", project_id="proj-B", contract_id="MAIN")
    world.instrument(
        document_id="doc-human-review",
        project_id="proj-A",
        contract_id="MAIN",
        processing_status="human_review_required",
    )
    world.clause(document_id="doc-A-MAIN", text=AUTHORISED_MARKER)
    world.clause(
        document_id="doc-B-MAIN",
        text=SIBLING_PROJECT_MARKER,
        org_id="org-A",
        project_id="proj-A",
    )
    world.clause(document_id="doc-human-review", text=HUMAN_REVIEW_MARKER)
    anchor = ObjectId()

    run = await _run_pipeline(
        monkeypatch, world=world, letters=[_letter(anchor)], letter_id=anchor
    )

    stored_ids = {
        str(source.get("document_id"))
        for source in run.clause_sources()
        if source.get("document_id")
    }
    assert stored_ids <= {"doc-A-MAIN"}, (
        f"the persisted letter recorded ineligible clause provenance: {stored_ids}"
    )
    assert run.draft_versions, "no draft_versions entry was pushed; the tail was not exercised"
    _assert_document_absent(run, "doc-B-MAIN", "the persisted draft provenance")
    _assert_present(
        run,
        AUTHORISED_MARKER,
        "the eligible companion instrument seeded beside the excluded one",
    )
    _assert_document_absent(run, "doc-human-review", "the persisted draft provenance")
    _assert_present(
        run,
        AUTHORISED_MARKER,
        "the eligible companion instrument seeded beside the excluded one",
    )
    _assert_absent(run, SIBLING_PROJECT_MARKER, "the persisted draft")
    _assert_absent(run, HUMAN_REVIEW_MARKER, "the persisted draft")


# ---------------------------------------------------------------------------
# A global actor keeps exactly the breadth canonical authorisation gives it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_global_actor_keeps_exactly_the_canonical_breadth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Breadth is RESOLVED, never named.

    The universe is per (organisation, project) even for a global actor: the
    active selection bounds it, so project B's instrument stays out of a draft
    anchored in project A.
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

    run = await _run_pipeline(
        monkeypatch,
        world=world,
        letters=[_letter(anchor)],
        letter_id=anchor,
        actor=_global_actor(),
    )

    _assert_present(run, AUTHORISED_MARKER, "a global actor's in-selection clause")
    _assert_absent(
        run,
        SIBLING_PROJECT_MARKER,
        "a global actor drafting in project A (the selection bounds the universe)",
    )


# ---------------------------------------------------------------------------
# R17 preservation - fail-closed must NOT be widened by this phase
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_rootless_worker_principal_admits_no_contract_evidence() -> None:
    """R17 is preserved here, not solved here.

    The v3 engine fabricates ``SimpleNamespace(id=..., organization_id=...,
    project_id=...)`` FROM the letter it is reading - authority derived from the
    resource, which is circular. Such a principal has no roles, so canonical
    authorisation denies it and its universe is EMPTY. That direction is
    correct and must stay: R18 may not be closed by making fabricated actors
    more permissive.
    """
    world = _World()
    world.instrument(document_id="doc-A-MAIN", project_id="proj-A", contract_id="MAIN")
    db = world.database([])

    fabricated = SimpleNamespace(
        id="worker", organization_id="org-A", project_id="proj-A"
    )
    universe = await resolve_authorized_project_universe(
        db,
        fabricated,
        organization_id="org-A",
        project_id="proj-A",
        mode=CurrentState(),
    )

    assert universe.eligible_document_ids == frozenset(), (
        "a principal fabricated from the resource it is reading was granted "
        "contract evidence. Authority must be PASSED to the worker, never "
        "reconstructed from the row it is processing."
    )


# ---------------------------------------------------------------------------
# RED 17 - the structural pin against regressing to the broad seam
# ---------------------------------------------------------------------------


def _called_attributes(module) -> set:
    """Every attribute NAME this module calls, via AST, not text matching.

    Deliberately not a line-number or substring assertion: those break on
    unrelated edits and pass on a call spelled slightly differently.
    """
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


def test_the_langgraph_pipeline_never_calls_generic_contract_search() -> None:
    """The broad seam may not come back.

    ``search_contracts`` is generic browse. It resolves no applicability, no
    projection currency and no positive publication authority, and it spends its
    candidate limit before any of them exist - so there is no point in its
    pipeline at which a fence would work. It has no place in an evidence path.
    """
    called = _called_attributes(letter_pipeline)
    assert "search_contracts" not in called, (
        "letter_pipeline.py calls generic ContractService.search_contracts "
        "again. Contract evidence must come from the canonical universe "
        "(resolve_authorized_project_universe), never from generic search."
    )
    assert "exact_clause_search" not in called, (
        "letter_pipeline.py reached generic contract search through "
        "exact_clause_search, which is search_contracts with a clause filter."
    )


def test_the_langgraph_pipeline_consumes_the_canonical_universe() -> None:
    """The positive half of the pin.

    Removing the generic call without consuming the canonical seam would also
    satisfy the negative assertion above, by having no contract evidence at all.
    """
    called = _called_attributes(letter_pipeline)
    assert "resolve_authorized_project_universe" in called, (
        "letter_pipeline.py no longer resolves the canonical contract evidence "
        "universe. Contract context must be CONTAINED, not switched off."
    )
