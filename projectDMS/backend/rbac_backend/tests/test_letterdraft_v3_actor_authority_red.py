"""G-A10 / R17: the v3 drafting engine must carry the REAL actor, never a fabricated one.

`LangGraphDraftingEngine` runs its evidence, generation and validation stages on a
**background worker**. Only identifiers cross the durable Redis boundary, so there is
no HTTP request and therefore no `get_current_user` result to hand down. The engine
used to close that gap by synthesising a principal out of the resource it was about
to read::

    SimpleNamespace(id=run.created_by,
                    organization_id=letter.organization_id,
                    project_id=letter.project_id)

Authority derived from the resource being read is circular, and after the G-A6/G-A7
containments that object carries no roles at all, so `build_scope_query` denies it
everything (`{"_id": {"$in": []}}`) and `authorize_scope` refuses it every project.
The direction was safe. The consequence was not: a legitimate actor's OWN prior
correspondence and OWN contract evidence silently vanished from every v3 draft, and
from the immutable evidence snapshot it is sealed into.

R17 is therefore a *completeness* defect with a hard security constraint attached:
the only permitted fix is to carry a real principal. Re-fabricating a broader
stand-in — default roles, the letter's workspace treated as entitlement, a system or
superadmin impersonation, a "trusted engine" special case — would convert a
fail-closed gap into a cross-tenant disclosure.

The real actor exists: `DraftRun.created_by` is the durable identifier of the human
who started the run, written from the authenticated `current_user` at HTTP time. It
is an IDENTIFIER, not authority — so the fix re-resolves it against the entitlement
store at execution time. A revoked project assignment is honoured on the retry rather
than replayed from a cached role list.

Every test here asserts on the surfaces where drafting material comes to rest, not on
an in-memory return value alone:

1. the returned context/source ledger;
2. the PERSISTED ``letter_draft_runs`` row re-read from storage;
3. the IMMUTABLE ``letter_draft_evidence_snapshots`` provenance row.

Positives and negatives are both load-bearing. A v3 engine that returns nothing is
fail-closed and useless; a v3 engine that returns a sibling project's material is a
leak. Both are pinned, and the mutation proofs must move them in opposite directions.
"""

from __future__ import annotations

import ast
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pytest
from bson import ObjectId

from rbac_backend.models.letter_drafting import DraftRun
from rbac_backend.services import conversation_service as conversation_service_module
from rbac_backend.services.letter_drafting import context as context_module
from rbac_backend.services.letter_drafting import langgraph_engine as engine_module
from rbac_backend.services.letter_drafting.langgraph_engine import LangGraphDraftingEngine

# --- unique markers -------------------------------------------------------
#
# Every marker is unique, so an assertion can never pass because the string
# happened to be absent for an unrelated reason.

SIBLING_PROJECT_MARKER = "R17_PROJECT_B_CONFIDENTIAL_20260831"
FOREIGN_ORG_MARKER = "R17_ORG_B_CONFIDENTIAL_20260831"
LAWFUL_PRIOR_MARKER = "R17_PROJECT_A_PRIOR_AUTHORISED_20260831"
LAWFUL_CLAUSE_MARKER = "R17_PROJECT_A_CLAUSE_AUTHORISED_20260831"
SIBLING_CLAUSE_MARKER = "R17_PROJECT_B_CLAUSE_CONFIDENTIAL_20260831"
FOREIGN_CLAUSE_MARKER = "R17_ORG_B_CLAUSE_CONFIDENTIAL_20260831"

#: Shared by every seeded clause, so relevance is never why a row is absent.
QUERY_TERM = "extension"

APPLICABILITY = "contract_document_applicability"
APPLICABILITY_EVENTS = "contract_document_applicability_events"
CONTRACT_DOCUMENTS = "contract_documents"

NOW = datetime(2026, 8, 31, tzinfo=timezone.utc)

ENGINE_SOURCE = Path(engine_module.__file__)


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
        #: Every filter this collection was asked for, so a test can prove that
        #: an authority failure issued NO query rather than issuing one and
        #: discarding its result.
        self.queries: List[Optional[Dict[str, Any]]] = []

    async def find_one(self, query=None, projection=None, sort=None):
        self.queries.append(deepcopy(query))
        matches = [row for row in self.rows if _matches(row, query)]
        if sort:
            field, direction = sort[0]
            matches.sort(key=lambda row: _sort_key(row, field), reverse=direction < 0)
        return deepcopy(matches[0]) if matches else None

    def find(self, query=None, *_a: Any, **_k: Any) -> _Cursor:
        self.queries.append(deepcopy(query))
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
# The world — canonical records only, never an "eligible" shortcut flag
# ---------------------------------------------------------------------------


class _World:
    """Seeds users, letters and the canonical Contract Master records.

    Nothing here writes an eligibility flag. Contract eligibility is whatever
    ``resolve_authorized_project_universe`` derives from applicability events,
    the contract document record's projection fields and the canonical
    ``documents`` row — so a test cannot assert against a fixture's opinion.
    """

    def __init__(self) -> None:
        self.users: List[Dict[str, Any]] = []
        self.letters: List[Dict[str, Any]] = []
        self.documents: List[Dict[str, Any]] = []
        self.contract_documents: List[Dict[str, Any]] = []
        self.applicability: List[Dict[str, Any]] = []
        self.applicability_events: List[Dict[str, Any]] = []
        self.clauses: List[Dict[str, Any]] = []
        self.runs: List[Dict[str, Any]] = []
        self._seq = 0

    def _next(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq}"

    # -- principals ------------------------------------------------------
    def user(
        self,
        user_id: str,
        *,
        roles: Iterable[str],
        organization_id: Optional[str] = "org-A",
        organizations: Optional[Iterable[str]] = None,
        projects: Iterable[str] = (),
        disabled: bool = False,
    ) -> str:
        """A REAL row in the entitlement store — the only authority source."""
        self.users.append(
            {
                "_id": user_id,
                "username": user_id,
                "email": f"{user_id}@example.com",
                "roles": list(roles),
                "organization_id": organization_id,
                "organizations": list(
                    organizations if organizations is not None else ([organization_id] if organization_id else [])
                ),
                "projects": list(projects),
                "account_type": "client_user",
                "disabled": disabled,
            }
        )
        return user_id

    # -- correspondence ---------------------------------------------------
    def letter(
        self,
        letter_id: Any,
        *,
        organization_id: str = "org-A",
        project_id: Optional[str] = "proj-A",
        subject: str = "Anchor letter",
        content: str = "Prepare a source-grounded response.",
        **overrides: Any,
    ) -> Any:
        row = {
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
            "letter_no": f"GA10-{str(letter_id)[-6:]}",
            "date": NOW,
            "created_at": NOW,
            "updated_at": NOW,
        }
        row.update(overrides)
        self.letters.append(row)
        return letter_id

    # -- contract instruments ---------------------------------------------
    def instrument(
        self,
        *,
        document_id: str,
        organization_id: str = "org-A",
        project_id: Optional[str] = "proj-A",
        contract_id: Optional[str] = "MAIN",
        processing_status: str = "completed",
        classification_revision: int = 3,
        projection_revision: Optional[int] = None,
        projection_status: str = "CURRENT",
        lifecycle: Tuple[str, ...] = ("APPLIED",),
    ) -> str:
        self.documents.append(
            {
                "_id": document_id,
                "organization_id": organization_id,
                "project_id": project_id,
                "processing_status": processing_status,
                "duplicate_status": None,
                "lifecycle_state": None,
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

    def clause(
        self,
        *,
        document_id: str,
        text: str,
        org_id: str = "org-A",
        project_id: str = "proj-A",
        clause_no: str = "8.4",
    ) -> str:
        clause_uid = self._next("clause")
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

    # -- the queued v3 run -------------------------------------------------
    def draft_run(self, *, letter_id: Any, created_by: Optional[str], run_id: str = "run-v3") -> str:
        """A queued v3 run exactly as `LangGraphDraftingEngine.create` writes it.

        ``created_by`` is the ONLY actor identity that crosses the durable Redis
        boundary — the worker gets identifiers, never a user object.
        """
        self.runs.append(
            {
                "run_id": run_id,
                "letter_id": str(letter_id),
                "draft_type": "fresh",
                "mode": "draft",
                "role": "contractor",
                "inputs": {
                    "draft_type": "fresh",
                    "mode": "draft",
                    "role": "contractor",
                    "subject": "Notice of delay",
                    "recipient": "Engineer",
                    "purpose": "Record the delay and reserve entitlement.",
                    "requirements": f"State the contractual basis for an {QUERY_TERM} of time.",
                    "points": f"Clause 8.4 {QUERY_TERM} of time.",
                    "clauses_to_consider": ["8.4"],
                },
                "status": "running",
                "engine": "langgraph_v3",
                "engine_version": "test",
                "graph_version": "1",
                "state_schema_version": 1,
                "thread_id": "thread-v3",
                "execution_status": "running",
                "next_action": "poll",
                "started_at": NOW,
                "updated_at": NOW,
                "created_by": created_by,
            }
        )
        return run_id

    def database(self) -> _Database:
        return _Database(
            {
                "users": self.users,
                "letters": self.letters,
                "documents": self.documents,
                CONTRACT_DOCUMENTS: self.contract_documents,
                APPLICABILITY: self.applicability,
                APPLICABILITY_EVENTS: self.applicability_events,
                "contract_clauses": self.clauses,
                "letter_draft_runs": self.runs,
            }
        )


# ---------------------------------------------------------------------------
# Stubs — everything outside this boundary is held still on purpose
# ---------------------------------------------------------------------------


class _DisabledFalkor:
    """The graph is a different seam (pinned by test_graph_consumer_authority)."""

    enabled = False

    def get_thread(self, *_a: Any, **_k: Any) -> List[Dict[str, Any]]:  # pragma: no cover
        return []


class _NoContracts:
    """Generic contract search was removed from this path in G-A7; keep it out."""

    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    async def search_contracts(self, *_a: Any, **_k: Any):  # pragma: no cover
        raise AssertionError("drafting v3 must not reach generic contract search")


class _StubQueue:
    """`collect_evidence` never enqueues; the queue must not touch Redis here."""

    enabled = True

    async def enqueue(self, *_a: Any, **_k: Any) -> str:  # pragma: no cover
        return "job"


# ---------------------------------------------------------------------------
# Driver — the REAL v3 engine node against the REAL persistence
# ---------------------------------------------------------------------------


class _Evidence:
    """Every place v3 evidence comes to rest."""

    def __init__(self, db: _Database, run_id: str) -> None:
        self.db = db
        self.run_id = run_id

    @property
    def stored_row(self) -> Dict[str, Any]:
        for row in self.db["letter_draft_runs"].rows:
            if row.get("run_id") == self.run_id:
                return row
        return {}

    @property
    def evidence_snapshot(self) -> Dict[str, Any]:
        for row in self.db["letter_draft_evidence_snapshots"].rows:
            if row.get("run_id") == self.run_id:
                return row
        return {}

    @property
    def run(self) -> Optional[DraftRun]:
        row = self.stored_row
        if not row:
            return None
        payload = {key: value for key, value in row.items() if key != "_id"}
        return DraftRun(**payload)

    def surfaces(self) -> Dict[str, str]:
        return {
            "the PERSISTED letter_draft_runs row": repr(self.stored_row),
            "the IMMUTABLE evidence snapshot": repr(self.evidence_snapshot),
        }

    def provenance(self) -> str:
        return repr(
            {
                "stored_context_bundle": self.stored_row.get("context_bundle"),
                "stored_source_ids": [
                    source.get("source_id") for source in (self.stored_row.get("sources") or [])
                ],
                "stored_source_letter_ids": [
                    source.get("letter_id") for source in (self.stored_row.get("sources") or [])
                ],
                "stored_source_document_ids": [
                    source.get("document_id") for source in (self.stored_row.get("sources") or [])
                ],
                "evidence_snapshot_sources": self.evidence_snapshot.get("sources"),
            }
        )

    def source_types(self) -> List[str]:
        return [source.get("source_type") for source in (self.stored_row.get("sources") or [])]


async def _collect_evidence(
    monkeypatch: pytest.MonkeyPatch,
    world: _World,
    *,
    run_id: str = "run-v3",
) -> _Evidence:
    """Drive the REAL `LangGraphDraftingEngine.collect_evidence` graph node.

    Nothing about the actor is supplied by the test at call time — exactly as on
    the worker, the engine has only `payload["run_id"]` and must resolve the
    initiating principal itself. That is the whole subject of this suite.
    """
    db = world.database()

    async def _get_db():
        return db

    monkeypatch.setattr(conversation_service_module, "get_database", _get_db)
    monkeypatch.setattr(context_module, "ContractService", _NoContracts)
    monkeypatch.setattr(context_module, "FalkorGraphService", lambda *a, **k: _DisabledFalkor())

    engine = LangGraphDraftingEngine(db, queue=_StubQueue())
    await engine.collect_evidence({"run_id": run_id})
    return _Evidence(db, run_id)


def _assert_absent(evidence: _Evidence, marker: str, why: str) -> None:
    for surface, text in evidence.surfaces().items():
        assert marker not in text, (
            f"{why}: the marker {marker!r} reached {surface}. Restoring the real "
            "actor may not widen what that actor may see — the v3 worker must "
            "resolve exactly the authority the HTTP caller had, and no more."
        )


def _assert_present(evidence: _Evidence, marker: str, why: str) -> None:
    combined = "\n".join(evidence.surfaces().values())
    assert marker in combined, (
        f"{why}: the lawful marker {marker!r} reached NONE of the v3 drafting "
        "surfaces. A fabricated principal has no roles, so canonical scope "
        "resolution denies it everything and the actor's own evidence silently "
        "disappears from the sealed snapshot. That is an outage, not a boundary."
    )


def _assert_id_absent(evidence: _Evidence, identifier: Any, why: str) -> None:
    assert str(identifier) not in evidence.provenance(), (
        f"{why}: the unauthorised identifier {identifier} survived in the persisted "
        "v3 provenance. Removing the text while keeping the id still records an "
        "unauthorised source as a contributor, and the snapshot is immutable."
    )


# ---------------------------------------------------------------------------
# World builders shared by the positive and negative cases
# ---------------------------------------------------------------------------


def _project_world(
    *,
    created_by: Optional[str] = "user-A",
    actor_projects: Iterable[str] = ("proj-A",),
    actor_roles: Iterable[str] = ("projectuser",),
    actor_org: Optional[str] = "org-A",
    actor_orgs: Optional[Iterable[str]] = None,
    seed_actor: bool = True,
) -> Tuple[_World, Any, Any]:
    """Organisation A / project A anchor, with a lawful prior letter and clause.

    The sibling-project and foreign-organisation material is seeded in the SAME
    conversation family and the SAME clause collection, so exclusion has to come
    from authority rather than from an empty world.
    """
    world = _World()
    if seed_actor:
        world.user(
            "user-A",
            roles=actor_roles,
            organization_id=actor_org,
            organizations=actor_orgs,
            projects=actor_projects,
        )

    anchor_id = ObjectId()
    prior_id = ObjectId()
    world.letter(anchor_id, previous_letter_id=str(prior_id), conversation_id="conv-1")
    world.letter(
        prior_id,
        subject="Our position of 12 August",
        content=LAWFUL_PRIOR_MARKER,
        summary=LAWFUL_PRIOR_MARKER,
        conversation_id="conv-1",
    )

    lawful_doc = "doc-lawful-A"
    world.instrument(document_id=lawful_doc)
    world.clause(document_id=lawful_doc, text=LAWFUL_CLAUSE_MARKER)

    world.draft_run(letter_id=anchor_id, created_by=created_by)
    return world, anchor_id, prior_id


# ---------------------------------------------------------------------------
# RED 1 — a legitimate project actor regains its OWN prior correspondence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_legitimate_actor_regains_its_own_prior_correspondence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The narrowest realistic drafting actor, reading its own project's thread.

    Under the fabricated principal this is EMPTY: the synthesised object carries
    no roles, `build_scope_query` falls through to its deny-all branch, and the
    actor's own reply chain never reaches the draft.
    """
    world, _anchor, prior_id = _project_world()
    evidence = await _collect_evidence(monkeypatch, world)

    _assert_present(evidence, LAWFUL_PRIOR_MARKER, "in-scope prior correspondence")
    assert "prior_correspondence" in evidence.source_types(), (
        "the lawful prior letter did not survive as a prior_correspondence source; "
        f"source types were {evidence.source_types()}"
    )
    assert str(prior_id) in evidence.provenance(), (
        "the lawful prior letter's id is missing from the persisted provenance, so "
        "the draft does not record the source it was actually grounded in."
    )


# ---------------------------------------------------------------------------
# RED 2 — a legitimate project actor regains its OWN contract evidence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_legitimate_actor_regains_its_own_contract_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicitly applicable, canonical Document present, publication-consumable,
    projection-current — every positive condition the Contract Master model asks
    for. The only thing that could remove it is the actor, and the actor is
    entitled."""
    world, _anchor, _prior = _project_world()
    evidence = await _collect_evidence(monkeypatch, world)

    _assert_present(evidence, LAWFUL_CLAUSE_MARKER, "in-scope contract clause evidence")
    assert "contract_clause" in evidence.source_types(), (
        "the lawful clause did not survive as a contract_clause source; source types "
        f"were {evidence.source_types()}"
    )


# ---------------------------------------------------------------------------
# RED 3 — the sibling project stays denied after the functional fix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_sibling_project_stays_denied_to_the_real_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Project A only, ORGANISATION-level anchor.

    The anchor carries no project, so the anchor's own workspace cannot be what
    excludes project B — `Letter.project_id` is optional and an organisation-level
    anchor narrows only by organisation. The single thing standing between this
    actor and the sibling project is the actor's own canonical row visibility,
    which is exactly what R17 had to restore without widening.
    """
    world = _World()
    world.user("user-A", roles=["projectuser"], organization_id="org-A", projects=["proj-A"])

    anchor_id, lawful_id, sibling_id = ObjectId(), ObjectId(), ObjectId()
    # Linked by `previous_letter_id` edges rather than a shared `conversation_id`:
    # the conversation-id branch of `_load_conversation_family` silently drops
    # ObjectId-backed rows (the G-A4 correctness defect, deliberately not fixed
    # here), and a case that passed for THAT reason would be vacuous.
    world.letter(anchor_id, project_id=None, previous_letter_id=str(lawful_id))
    world.letter(
        lawful_id,
        project_id="proj-A",
        subject="Our position of 12 August",
        content=LAWFUL_PRIOR_MARKER,
        summary=LAWFUL_PRIOR_MARKER,
        previous_letter_id=str(sibling_id),
    )
    world.letter(
        sibling_id,
        project_id="proj-B",
        subject="Sibling project position",
        content=SIBLING_PROJECT_MARKER,
        summary=SIBLING_PROJECT_MARKER,
    )
    world.draft_run(letter_id=anchor_id, created_by="user-A")

    evidence = await _collect_evidence(monkeypatch, world)

    _assert_absent(evidence, SIBLING_PROJECT_MARKER, "sibling-project prior correspondence")
    _assert_id_absent(evidence, sibling_id, "sibling-project prior correspondence")
    # The lawful companion sits in the SAME conversation family, so the exclusion
    # is proved by authority rather than by an empty thread.
    _assert_present(evidence, LAWFUL_PRIOR_MARKER, "the lawful companion source")


@pytest.mark.asyncio
async def test_a_lying_clause_row_stays_denied_to_the_real_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clause-row provenance still cannot self-authorise.

    The row is stamped with the drafting workspace while its canonical instrument
    is applied to project B. That is the G-A7 boundary, and restoring the real
    actor may not reopen it.
    """
    world, _anchor, _prior = _project_world()
    sibling_doc = "doc-sibling-B"
    world.instrument(document_id=sibling_doc, project_id="proj-B")
    world.clause(document_id=sibling_doc, text=SIBLING_CLAUSE_MARKER, project_id="proj-A")

    evidence = await _collect_evidence(monkeypatch, world)

    _assert_absent(evidence, SIBLING_CLAUSE_MARKER, "sibling-project contract evidence")
    _assert_id_absent(evidence, sibling_doc, "sibling-project contract evidence")
    _assert_present(evidence, LAWFUL_CLAUSE_MARKER, "the lawful companion clause")


# ---------------------------------------------------------------------------
# RED 4 — a foreign organisation stays denied after the functional fix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_foreign_organisation_stays_denied_to_the_real_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ACTOR is foreign, which is the axis a real principal decides.

    A run queued against an organisation-A letter whose recorded actor belongs to
    organisation B must produce nothing at all: not the anchor's own thread, not
    its contract evidence. The letter's workspace is not an entitlement, so it
    cannot re-admit what the actor may not see.
    """
    world, _anchor, prior_id = _project_world(seed_actor=False)
    world.user(
        "user-A",
        roles=["projectuser"],
        organization_id="org-B",
        organizations=["org-B"],
        projects=["proj-B"],
    )

    evidence = await _collect_evidence(monkeypatch, world)

    _assert_absent(evidence, LAWFUL_PRIOR_MARKER, "an actor from another organisation")
    _assert_absent(evidence, LAWFUL_CLAUSE_MARKER, "an actor from another organisation")
    _assert_id_absent(evidence, prior_id, "an actor from another organisation")
    _assert_id_absent(evidence, "doc-lawful-A", "an actor from another organisation")


# ---------------------------------------------------------------------------
# RED 5 — a run with no resolvable actor FAILS CLOSED
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_run_with_no_recorded_actor_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`created_by` is absent. There is no principal to resolve, so the stage must
    refuse — never continue against a stand-in built from the letter."""
    world, _anchor, _prior = _project_world(created_by=None)

    with pytest.raises(Exception) as excinfo:
        await _collect_evidence(monkeypatch, world)

    assert not isinstance(excinfo.value, AssertionError), (
        "the actorless run produced evidence instead of refusing"
    )
    message = str(excinfo.value)
    assert "principal" in message.lower() or "actor" in message.lower(), (
        "an actorless v3 run must fail with an authority error naming the missing "
        f"principal; it failed with {message!r}"
    )


@pytest.mark.asyncio
async def test_a_run_whose_actor_no_longer_exists_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The identifier is present but resolves to nothing in the entitlement store —
    a deleted account, or an identifier that was never a user. Re-resolution at
    execution time is the point: a stale reference is a refusal, not a licence."""
    world, _anchor, _prior = _project_world(seed_actor=False)

    with pytest.raises(Exception):
        await _collect_evidence(monkeypatch, world)


@pytest.mark.asyncio
async def test_a_disabled_actor_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Authority is re-read at execution time, so a disabled account cannot have
    its entitlement replayed from the moment the run was queued."""
    world, _anchor, _prior = _project_world(seed_actor=False)
    world.user("user-A", roles=["projectuser"], projects=["proj-A"], disabled=True)

    with pytest.raises(Exception):
        await _collect_evidence(monkeypatch, world)


# ---------------------------------------------------------------------------
# RED 6 — structural: v3 may not build an authority principal from the letter
# ---------------------------------------------------------------------------


def _principal_shaped_calls(tree: ast.AST) -> List[Tuple[int, str]]:
    """Calls that construct an actor-shaped object out of local resource state.

    The shape, not the callable name, is what is banned: any call that supplies
    an identity keyword together with an entitlement keyword whose value is read
    off the run or the letter is a fabricated principal, whether it is spelled
    `SimpleNamespace`, `Mock`, a dataclass or a bare local class.
    """
    identity_keys = {"id", "user_id", "username", "email"}
    entitlement_keys = {"organization_id", "organizations", "project_id", "projects", "roles"}
    resource_names = {"letter", "run", "target", "domain", "payload", "state"}
    findings: List[Tuple[int, str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        supplied = {kw.arg for kw in node.keywords if kw.arg}
        if not (supplied & identity_keys) or not (supplied & entitlement_keys):
            continue
        for keyword in node.keywords:
            if keyword.arg not in entitlement_keys:
                continue
            for inner in ast.walk(keyword.value):
                name = None
                if isinstance(inner, ast.Name):
                    name = inner.id
                elif isinstance(inner, ast.Attribute) and isinstance(inner.value, ast.Name):
                    name = inner.value.id
                if name in resource_names:
                    findings.append(
                        (
                            getattr(node, "lineno", 0),
                            f"{keyword.arg}=<derived from {name}>",
                        )
                    )
    return findings


def test_the_v3_engine_never_builds_a_principal_out_of_the_letter() -> None:
    tree = ast.parse(ENGINE_SOURCE.read_text(encoding="utf-8"))
    findings = _principal_shaped_calls(tree)
    assert not findings, (
        "the v3 drafting engine constructs an authority principal from the resource "
        f"it is reading: {findings}. Authority derived from the row being read is "
        "circular. Resolve the real initiating actor instead."
    )


def test_the_v3_engine_resolves_a_real_stored_principal() -> None:
    """The positive twin of the guard above.

    Without it, deleting every principal construction — and every evidence stage
    with it — would satisfy the negative check. The module must actually resolve
    the initiating actor through the canonical helper.
    """
    source = ENGINE_SOURCE.read_text(encoding="utf-8")
    assert "resolve_stored_principal" in source, (
        "the v3 engine no longer resolves the initiating actor through the canonical "
        "principal resolver, so its background stages have no authority source."
    )


def test_the_v3_engine_never_monkey_patches_its_authority_gate() -> None:
    """`legacy._load_and_authorize = _already_authorized` replaced the real gate
    with a function that returned the letter unconditionally. Its only purpose was
    to survive a principal that could not pass authorisation — with the real actor
    there is nothing to bypass, and a patched gate is not a gate."""
    tree = ast.parse(ENGINE_SOURCE.read_text(encoding="utf-8"))
    patched: List[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Attribute) and target.attr.startswith("_load_and_authorize"):
                patched.append(getattr(node, "lineno", 0))
    assert not patched, (
        "the v3 engine reassigns an authorisation gate at runtime "
        f"(lines {patched}). One normal typed authority path, not a patched one."
    )


# ---------------------------------------------------------------------------
# RED 7 — the identity that reaches canonical authorisation is the real actor's
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_authority_identity_is_the_recorded_actor_not_the_letter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Behavioural, not an object-identity check.

    Two runs differ ONLY in `created_by`. If v3 regenerated authority from the
    letter, both would see the same evidence, because the letter is identical.
    The narrow actor must see less than the wide one.
    """
    wide = _World()
    wide.user("user-A", roles=["orguser"], organization_id="org-A", projects=[])
    wide.user("user-N", roles=["projectuser"], organization_id="org-A", projects=["proj-N"])

    anchor_id, prior_id = ObjectId(), ObjectId()
    wide.letter(anchor_id, previous_letter_id=str(prior_id), conversation_id="conv-1")
    wide.letter(
        prior_id,
        subject="Our position of 12 August",
        content=LAWFUL_PRIOR_MARKER,
        summary=LAWFUL_PRIOR_MARKER,
        conversation_id="conv-1",
    )
    wide.instrument(document_id="doc-lawful-A")
    wide.clause(document_id="doc-lawful-A", text=LAWFUL_CLAUSE_MARKER)

    narrow = deepcopy(wide)
    wide.draft_run(letter_id=anchor_id, created_by="user-A")
    narrow.draft_run(letter_id=anchor_id, created_by="user-N")

    wide_evidence = await _collect_evidence(monkeypatch, wide)
    narrow_evidence = await _collect_evidence(monkeypatch, narrow)

    _assert_present(wide_evidence, LAWFUL_PRIOR_MARKER, "the organisation-tier actor")
    _assert_absent(
        narrow_evidence,
        LAWFUL_PRIOR_MARKER,
        "an actor assigned only to an unrelated project",
    )
    _assert_absent(
        narrow_evidence,
        LAWFUL_CLAUSE_MARKER,
        "an actor assigned only to an unrelated project",
    )


# ---------------------------------------------------------------------------
# RED 8 — the resume / retry path carries the same real actor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_resume_path_carries_the_same_real_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`process_drafting_job` is re-entered for resume, retry and lease recovery,
    always with identifiers only. Each re-entry must re-resolve the same actor and
    must not degrade to a fabricated one on the second pass.

    The stage is naturally idempotent — it short-circuits once snapshots exist — so
    this drives a fresh run for the SAME actor and letter after the first completed,
    which is the shape a retry takes after a lease timeout.
    """
    world, anchor_id, _prior = _project_world()
    first = await _collect_evidence(monkeypatch, world)
    _assert_present(first, LAWFUL_PRIOR_MARKER, "the first attempt")

    world.draft_run(letter_id=anchor_id, created_by="user-A", run_id="run-v3-retry")
    retry = await _collect_evidence(monkeypatch, world, run_id="run-v3-retry")
    _assert_present(retry, LAWFUL_PRIOR_MARKER, "the retry attempt")
    _assert_present(retry, LAWFUL_CLAUSE_MARKER, "the retry attempt")


# ---------------------------------------------------------------------------
# RED 8b — the OTHER two worker stages carry the same real actor
#
# `collect_evidence` is not the only place the fabricated principal was built.
# The strategy-confirmation resume drives `execute_confirmed_domain_pipeline`
# (which additionally REPLACED the v2 authorisation gate with a function that
# returned the letter unconditionally) and then `commit_validation_stage`, whose
# refinement retrieval is a second door into the same persisted ledger. Fixing
# one stage and leaving the others actor-less would be a partial repair.
# ---------------------------------------------------------------------------


def _seeded_engine(monkeypatch: pytest.MonkeyPatch, world: _World) -> Tuple[Any, _Database]:
    db = world.database()

    async def _get_db():
        return db

    monkeypatch.setattr(conversation_service_module, "get_database", _get_db)
    monkeypatch.setattr(context_module, "ContractService", _NoContracts)
    monkeypatch.setattr(context_module, "FalkorGraphService", lambda *a, **k: _DisabledFalkor())
    return LangGraphDraftingEngine(db, queue=_StubQueue()), db


@pytest.mark.asyncio
async def test_the_domain_generation_stage_hands_the_v2_adapter_the_real_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The v2 domain adapter must be called with the resolved principal, and its
    real authorisation gate must still be in place when it is."""
    from rbac_backend.core.security import CurrentUser
    from rbac_backend.services.letter_drafting.service import DraftRunService

    world, anchor_id, _prior = _project_world()
    engine, db = _seeded_engine(monkeypatch, world)
    run = await engine.repository.get_by_run_id("run-v3")
    assert run is not None

    captured: Dict[str, Any] = {}
    original_gate = DraftRunService._load_and_authorize

    async def _capture(self, letter_id, request, current_user, **kwargs):
        captured["actor"] = current_user
        captured["gate_is_original"] = (
            self._load_and_authorize.__func__ is original_gate
            if hasattr(self._load_and_authorize, "__func__")
            else self._load_and_authorize is original_gate
        )
        return DraftRun(
            run_id="run-v2-child",
            letter_id=str(letter_id),
            draft_type="fresh",
            mode="draft",
            role="contractor",
            status="running",
            started_at=NOW,
            updated_at=NOW,
        )

    monkeypatch.setattr(DraftRunService, "create_run", _capture)
    await engine.execute_confirmed_domain_pipeline(run)

    actor = captured.get("actor")
    assert isinstance(actor, CurrentUser), (
        "the domain-generation stage handed the v2 adapter a "
        f"{type(actor).__name__}, not the canonical principal type"
    )
    assert actor.id == "user-A"
    assert actor.roles == ["projectuser"], (
        "the principal carries no real roles, so it was not read from the "
        "entitlement store"
    )
    assert actor.projects == ["proj-A"]
    assert captured.get("gate_is_original") is True, (
        "the v2 authorisation gate was replaced before the adapter ran; a patched "
        "gate is not a gate"
    )


@pytest.mark.asyncio
async def test_the_validation_stage_hands_the_cyclic_draft_the_real_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_run_cyclic_draft` re-retrieves refinement sources under the actor's own
    canonical universe, and whatever it returns is merged into the SAME persisted
    ledger. It must therefore see the real actor too."""
    from rbac_backend.core.security import CurrentUser
    from rbac_backend.models.letter_drafting import DraftArtifact, ValidationReport
    from rbac_backend.services.letter_drafting.service import DraftRunService

    world, anchor_id, _prior = _project_world()
    artifact = {"draft_letter": "Draft body", "source_integrity_notes": "n/a"}
    world.runs[0].update({"draft_artifact": artifact, "plan": "1. Facts."})
    world.draft_run(letter_id=anchor_id, created_by="user-A", run_id="run-v3-domain")
    world.runs[-1].update({"draft_artifact": artifact, "plan": "1. Facts."})

    engine, db = _seeded_engine(monkeypatch, world)

    captured: Dict[str, Any] = {}

    async def _capture(self, **kwargs):
        captured["actor"] = kwargs.get("current_user")
        return (
            DraftArtifact(**artifact),
            [],
            ValidationReport(),
            [],
            [],
            None,
            [],
        )

    monkeypatch.setattr(DraftRunService, "_run_cyclic_draft", _capture)
    await engine.commit_validation_stage("run-v3", "run-v3-domain")

    actor = captured.get("actor")
    assert isinstance(actor, CurrentUser), (
        "the validation stage handed the cyclic draft a "
        f"{type(actor).__name__}, not the canonical principal type"
    )
    assert actor.id == "user-A"
    assert actor.roles == ["projectuser"]
    assert actor.projects == ["proj-A"]


# ---------------------------------------------------------------------------
# RED 9 — the background path has ONE authority answer: the initiating actor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_background_worker_uses_the_initiating_actor_and_nothing_wider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """There is no system principal on this path, and no route to one.

    The worker's authority answer is A: the stored initiating actor, re-resolved.
    This pins that answer by proving that revoking the actor's project assignment
    between queueing and execution removes the evidence — a system principal or a
    cached role list would not notice.
    """
    world, anchor_id, _prior = _project_world()
    before = await _collect_evidence(monkeypatch, world)
    _assert_present(before, LAWFUL_PRIOR_MARKER, "the actor before its assignment was revoked")

    revoked = deepcopy(world)
    for user in revoked.users:
        if user["_id"] == "user-A":
            user["projects"] = []
    revoked.runs = []
    revoked.draft_run(letter_id=anchor_id, created_by="user-A", run_id="run-v3-revoked")

    after = await _collect_evidence(monkeypatch, revoked, run_id="run-v3-revoked")
    _assert_absent(after, LAWFUL_PRIOR_MARKER, "an actor whose project assignment was revoked")
    _assert_absent(after, LAWFUL_CLAUSE_MARKER, "an actor whose project assignment was revoked")


# ---------------------------------------------------------------------------
# RED 10 / RED 11 — the persisted DraftRun, positively and negatively
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_persisted_draft_run_records_the_lawful_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-read from storage and rehydrated through the model, not inspected in
    memory. This is the row every later reader inherits."""
    world, _anchor, prior_id = _project_world()
    evidence = await _collect_evidence(monkeypatch, world)

    run = evidence.run
    assert run is not None, "the v3 run was not persisted at all"
    assert str(prior_id) in [str(value) for value in run.context_bundle.prior_correspondence_ids], (
        "the persisted context bundle does not name the lawful prior letter, so the "
        "sealed evidence ledger under-reports what grounded this draft."
    )
    ledger_text = "\n".join(source.text or "" for source in run.sources)
    assert LAWFUL_PRIOR_MARKER in ledger_text
    assert LAWFUL_CLAUSE_MARKER in ledger_text
    assert evidence.stored_row.get("context_snapshot_id"), (
        "no immutable evidence snapshot id was written, so nothing was sealed"
    )
    assert evidence.evidence_snapshot, "the immutable evidence snapshot row is missing"


@pytest.mark.asyncio
async def test_the_persisted_draft_run_stays_clean_for_a_narrow_actor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Organisation-level anchor again, so the sibling project is excluded by the
    ACTOR and by nothing else, asserted on the rehydrated persisted row."""
    world = _World()
    world.user("user-A", roles=["projectuser"], organization_id="org-A", projects=["proj-A"])

    anchor_id, lawful_id, sibling_letter = ObjectId(), ObjectId(), ObjectId()
    world.letter(anchor_id, project_id=None, previous_letter_id=str(lawful_id))
    world.letter(
        lawful_id,
        project_id="proj-A",
        subject="Our position of 12 August",
        content=LAWFUL_PRIOR_MARKER,
        summary=LAWFUL_PRIOR_MARKER,
        previous_letter_id=str(sibling_letter),
    )
    world.letter(
        sibling_letter,
        project_id="proj-B",
        subject="Sibling project position",
        content=SIBLING_PROJECT_MARKER,
        summary=SIBLING_PROJECT_MARKER,
    )
    world.draft_run(letter_id=anchor_id, created_by="user-A")

    evidence = await _collect_evidence(monkeypatch, world)

    run = evidence.run
    assert run is not None
    assert str(lawful_id) in [
        str(value) for value in run.context_bundle.prior_correspondence_ids
    ], "the lawful companion is missing, so this negative would pass vacuously"
    assert str(sibling_letter) not in [
        str(value) for value in run.context_bundle.prior_correspondence_ids
    ]
    ledger = repr([source.model_dump(mode="json") for source in run.sources])
    assert SIBLING_PROJECT_MARKER not in ledger
    assert str(sibling_letter) not in repr(evidence.evidence_snapshot)


# ---------------------------------------------------------------------------
# RED 12 — global actor semantics survive, resolved and never named
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_global_actor_keeps_the_breadth_canonical_authorisation_gives_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No role-name check decides this — `build_scope_query` does.

    The anchor is ORGANISATION-level, so a global actor's canonical scope for this
    thread is organisation A: the sibling project inside org A is admitted, and the
    other tenant's letter is not, because the anchor narrows INSIDE the entitlement.
    That is the accepted G-A6 semantics, and carrying the real actor into v3 must
    reproduce it rather than substitute a narrower or wider stand-in.
    """
    world = _World()
    world.user("user-A", roles=["superadmin"], organization_id=None, organizations=[], projects=[])

    anchor_id, sibling_id, foreign_id = ObjectId(), ObjectId(), ObjectId()
    world.letter(anchor_id, project_id=None, previous_letter_id=str(sibling_id))
    world.letter(
        sibling_id,
        project_id="proj-B",
        subject="Sibling project position",
        content=SIBLING_PROJECT_MARKER,
        summary=SIBLING_PROJECT_MARKER,
        previous_letter_id=str(foreign_id),
    )
    world.letter(
        foreign_id,
        organization_id="org-B",
        project_id="proj-B",
        subject="Another tenant's position",
        content=FOREIGN_ORG_MARKER,
        summary=FOREIGN_ORG_MARKER,
    )
    world.draft_run(letter_id=anchor_id, created_by="user-A")

    evidence = await _collect_evidence(monkeypatch, world)

    _assert_present(
        evidence,
        SIBLING_PROJECT_MARKER,
        "a global actor inside organisation A",
    )
    _assert_absent(
        evidence,
        FOREIGN_ORG_MARKER,
        "a global actor anchored in organisation A",
    )


# ---------------------------------------------------------------------------
# The fabricated principal itself stays denied — it must never learn to pass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_old_fabricated_principal_is_still_refused_everything() -> None:
    """The exact object v3 used to build, asked of the canonical seams directly.

    Restoring the real actor must not be implemented by teaching a letter-derived
    principal to authorise. If this ever passes, the repair widened instead of
    propagating.
    """
    from rbac_backend.core.security import build_scope_query
    from rbac_backend.services.contract_scope_resolver import (
        resolve_authorized_project_universe,
    )
    from rbac_backend.services.letter_drafting.context import CurrentState

    world, _anchor, _prior = _project_world()
    db = world.database()

    fabricated = SimpleNamespace(id="user-A", organization_id="org-A", project_id="proj-A")

    # `build_scope_query` reads `.roles` unguarded, so this object does not even
    # reach the deny-all branch — it raises. Either outcome is a refusal; what is
    # asserted is that NO row-visibility filter is ever produced for it.
    try:
        scope = build_scope_query(fabricated)
    except Exception:
        scope = {"_id": {"$in": []}}
    assert scope == {"_id": {"$in": []}}, (
        "a principal fabricated from the resource being read acquired row "
        "visibility. Authority may never be derived from the row it authorises."
    )
    universe = await resolve_authorized_project_universe(
        db,
        fabricated,
        organization_id="org-A",
        project_id="proj-A",
        mode=CurrentState(),
    )
    assert not universe.eligible_document_ids, (
        "a fabricated principal resolved a non-empty contract evidence universe."
    )
