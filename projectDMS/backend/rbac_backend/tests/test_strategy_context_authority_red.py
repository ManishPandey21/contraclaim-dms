"""G-A4: strategy-context synthesis must exclude unauthorised source material
BEFORE it reaches the prompt, and before it is PERSISTED onto the letter.

`POST /api/letters/{letter_id}/strategy/context` authorised the LETTER
(`controller.get_letter(letter_id, current_user)`) and then called
`StrategyContextService.generate_context(letter_id)` with **no actor**. Inside:

* the thread came from `ConversationService.get_conversation_chain(letter_id,
  current_user=None)` — the actor argument is accepted and never read. The
  traversal is Mongo-only: `db.letters.find({"conversation_id": ...})`,
  `get_letter(previous_letter_id)` and `db.letters.find({"previous_letter_id":
  ...})`, none of them scope-filtered. A letter in a sibling project or a
  foreign organisation reachable along the reply chain joined the family, and
  `_format_letter_entry` put its `content` into the synthesis.

  The fixture chains through `previous_letter_id` because that is the branch
  that is actually LIVE. The other two branches build `Letter(**doc)` from the
  raw row, and production stores `letters._id` as an ObjectId
  (`create_letter` pops `_id` and lets Mongo mint one) while `Letter.id` is a
  plain `str`, so every row they find raises `ValidationError` and is swallowed
  by `except Exception: continue`. That is a separate pre-existing silent-drop
  defect; it is recorded, NOT fixed here — fixing it would widen the family,
  and the containment below holds either way because it is applied to the
  assembled family rather than to one traversal branch;
* the curated documents came from the UNSCOPED `get_documents_by_ids`, and
  `_format_document_entry` read `summary` / `full_text` / `ocrText` with no
  publication gate — the class G-A3 closed for the context-document endpoint;
* the result was then written back onto the letter by the route, so
  `contractor_context`, `engineer_context`, `employer_context` and
  `thread_letters` became DURABLE state, read later by drafting, reports and
  every future request that never revisits the original source authority.

That last part is why response-only assertions are insufficient here. Each test
below asserts on three surfaces:

1. the **captured LLM prompt** — proof that filtering happens before synthesis,
   not after it. Once unauthorised text has influenced the model the boundary is
   already violated, and stripping the marker from the answer does not undo it;
2. the **persisted letter row**, re-read from the store after the call;
3. the returned response, including `timeline`, which carries each thread
   letter's `subject`.

Accepted, do not reopen:

* thread letters are bounded by canonical `build_scope_query` on the ACTOR,
  narrowed — not authorised — by the anchor letter's own organisation/project.
  A shared `conversation_id`, a reply edge, a matching organisation or the fact
  that the parent letter is authorised are NOT authority;
* curated documents go through the G-A3 seam
  (`DocumentService.get_documents_by_ids_in_scope`), which applies scope AND
  `is_consumable`. No strategy-context-specific publication policy exists;
* `processing_status == "failed"` is OPERATIONAL under Model-B, so such a
  document still contributes. Do not "harden" that into a denial;
* a clean regeneration must not leave a contaminated prior value in place. A
  role with no authorised sources CLEARS its stored context rather than
  silently preserving the previous one.

The thread path reads no graph: `_load_conversation_family` touches only
`db.letters`. There is therefore no raw-graph-property poisoning case and no
graph-identity-without-canonical-resolution case at this seam, and none is
manufactured here.
"""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any, Dict, Iterable, List, Optional

import pytest
from bson.objectid import ObjectId

from rbac_backend.core.security import CurrentUser
from rbac_backend.models.ai_models import StrategyContextRequest
from rbac_backend.routers.letters import generate_letter_strategy_context
from rbac_backend.services import strategy_context_service as strategy_module
from rbac_backend.services.conversation_service import ConversationService
from rbac_backend.services.letter_service import LetterService

ORG_A = "org-A"
ORG_B = "org-B"
PROJ_A = "proj-A"
PROJ_B = "proj-B"
PROJ_C = "proj-C"

CONVERSATION_ID = "conv-strategy-1"

ANCHOR_ID = "6531a1b2c3d4e5f601020401"
THREAD_SAFE_ID = "6531a1b2c3d4e5f601020402"
THREAD_SIBLING_ID = "6531a1b2c3d4e5f601020403"
THREAD_FOREIGN_ID = "6531a1b2c3d4e5f601020404"

ANCHOR_MARKER = "ANCHOR_ORG_A_PROJ_A_SAFE_MARKER_20260831"
THREAD_SAFE_MARKER = "THREAD_ORG_A_PROJ_A_SAFE_MARKER_20260831"
THREAD_SIBLING_MARKER = "THREAD_PROJECT_B_STRATEGY_CONFIDENTIAL_20260831"
THREAD_FOREIGN_MARKER = "THREAD_ORG_B_STRATEGY_CONFIDENTIAL_20260831"

CURATED_SAFE_MARKER = "CURATED_ORG_A_PROJ_A_SAFE_MARKER_20260831"
CURATED_SIBLING_MARKER = "CURATED_PROJECT_B_STRATEGY_CONFIDENTIAL_20260831"
CURATED_FOREIGN_MARKER = "CURATED_ORG_B_STRATEGY_CONFIDENTIAL_20260831"
CURATED_REVIEW_MARKER = "CURATED_HUMAN_REVIEW_CONFIDENTIAL_20260831"
CURATED_DUPLICATE_MARKER = "CURATED_DUPLICATE_CONFIDENTIAL_20260831"
CURATED_DELETED_MARKER = "CURATED_DELETED_CONFIDENTIAL_20260831"
CURATED_OPERATIONAL_MARKER = "CURATED_OPERATIONAL_FAILURE_LAST_KNOWN_GOOD_20260831"

STICKY_MARKER = "PRIOR_UNSAFE_RUN_STICKY_MARKER_20260831"
PRIOR_STORED_MARKER = "PRIOR_AUTHORISED_STORED_VALUE_20260831"

#: Never permitted to reach the prompt, the persisted letter, or the response.
FORBIDDEN_MARKERS = (
    THREAD_SIBLING_MARKER,
    THREAD_FOREIGN_MARKER,
    CURATED_SIBLING_MARKER,
    CURATED_FOREIGN_MARKER,
    CURATED_REVIEW_MARKER,
    CURATED_DUPLICATE_MARKER,
    CURATED_DELETED_MARKER,
)

#: Persisted strategy fields. These are the durable surface.
PERSISTED_CONTEXT_FIELDS = (
    "contractor_context",
    "engineer_context",
    "employer_context",
)


# ---------------------------------------------------------------------------
# Mongo boundary. The scope filter IS the thing under test, so the fake applies
# it for real, and `update_one` really mutates the stored row so the durable
# assertion reads what production would have written.
# ---------------------------------------------------------------------------


def _same(left: Any, right: Any) -> bool:
    return str(left) == str(right)


def _matches(row: Dict[str, Any], query: Optional[Dict[str, Any]]) -> bool:
    for key, expected in (query or {}).items():
        if key == "$and":
            if not all(_matches(row, branch) for branch in expected):
                return False
            continue
        if key == "$or":
            if not any(_matches(row, branch) for branch in expected):
                return False
            continue
        actual = row.get(key)
        if isinstance(expected, dict):
            if "$in" in expected:
                candidates = expected["$in"]
                if isinstance(actual, list):
                    if not any(_same(item, c) for item in actual for c in candidates):
                        return False
                    continue
                if not any(_same(actual, candidate) for candidate in candidates):
                    return False
                continue
            if "$ne" in expected:
                if _same(actual, expected["$ne"]):
                    return False
                continue
            if "$exists" in expected and (key in row) is not bool(expected["$exists"]):
                return False
            continue
        if not _same(actual, expected):
            return False
    return True


def _sort_key(row: Dict[str, Any], spec: Dict[str, Any]):
    key: List[Any] = []
    for field in spec:
        value = row.get(field)
        key.append((value is None, "" if value is None else str(value)))
    return key


class _UpdateResult:
    def __init__(self, matched: int, modified: int) -> None:
        self.matched_count = matched
        self.modified_count = modified


class _Cursor:
    def __init__(self, rows: Iterable[Dict[str, Any]]) -> None:
        self.rows = [deepcopy(row) for row in rows]

    def sort(self, spec: Any = None, direction: Any = None) -> "_Cursor":
        if isinstance(spec, str):
            spec = {spec: direction or 1}
        if isinstance(spec, dict):
            self.rows.sort(key=lambda row: _sort_key(row, spec))
        return self

    def limit(self, amount: int) -> "_Cursor":
        self.rows = self.rows[:amount]
        return self

    async def to_list(self, length: Optional[int] = None) -> List[Dict[str, Any]]:
        return deepcopy(self.rows if length is None else self.rows[:length])


class _Collection:
    def __init__(self, rows: Iterable[Dict[str, Any]] = ()) -> None:
        self.rows = [deepcopy(row) for row in rows]

    def find(self, query: Optional[Dict[str, Any]] = None, *_a: Any, **_k: Any) -> _Cursor:
        return _Cursor(row for row in self.rows if _matches(row, query))

    async def find_one(self, query: Optional[Dict[str, Any]] = None, *_a: Any, **_k: Any):
        for row in self.rows:
            if _matches(row, query):
                return deepcopy(row)
        return None

    async def count_documents(self, query: Optional[Dict[str, Any]] = None, *_a: Any, **_k: Any) -> int:
        return sum(1 for row in self.rows if _matches(row, query))

    async def update_one(self, query: Dict[str, Any], operations: Dict[str, Any], *_a: Any, **_k: Any):
        for row in self.rows:
            if not _matches(row, query):
                continue
            changed = False
            for field, value in (operations.get("$set") or {}).items():
                if row.get(field) != value:
                    row[field] = value
                    changed = True
            for field, value in (operations.get("$push") or {}).items():
                row.setdefault(field, []).append(value)
                changed = True
            return _UpdateResult(1, 1 if changed else 0)
        return _UpdateResult(0, 0)


class _Database:
    def __init__(self, collections: Dict[str, List[Dict[str, Any]]]) -> None:
        self._collections: Dict[str, _Collection] = {
            name: _Collection(rows) for name, rows in collections.items()
        }

    def __getitem__(self, name: str) -> _Collection:
        return self._collections.setdefault(name, _Collection())

    def __getattr__(self, name: str) -> _Collection:
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]


# ---------------------------------------------------------------------------
# The synthesis boundary. The stub records every prompt the service builds and
# then fails, so the deterministic `_merge_context` fallback supplies the text.
# Recording the prompt is the point: it is where material influence begins.
# ---------------------------------------------------------------------------


class _RecordingCompletions:
    def __init__(self, sink: List[Any]) -> None:
        self._sink = sink

    async def create(self, *_a: Any, **kwargs: Any):
        self._sink.append(kwargs.get("messages"))
        raise RuntimeError("synthesis backend unavailable in tests")


class _RecordingChat:
    def __init__(self, sink: List[Any]) -> None:
        self.completions = _RecordingCompletions(sink)


class _RecordingOpenAI:
    """Captures what was about to be sent to the model."""

    sink: List[Any] = []

    def __init__(self, *_a: Any, **_k: Any) -> None:
        self.chat = _RecordingChat(self.sink)


class _StubConfig:
    openai_api_key = "test-key-not-used"
    openai_timeout = 1.0
    openai_model = "test-model"
    max_output_tokens = 400


@pytest.fixture(autouse=True)
def _synthesis_recorder(monkeypatch):
    _RecordingOpenAI.sink = []
    monkeypatch.setattr(strategy_module, "AsyncOpenAI", _RecordingOpenAI)
    monkeypatch.setattr(strategy_module, "DocumentProcessingConfig", _StubConfig)
    return _RecordingOpenAI.sink


#: The curated-document branch constructs `DocumentService()` with no handle and
#: lets it resolve one lazily, exactly as production does. Binding the lazy
#: resolver rather than injecting a service keeps that branch faithful, so the
#: pre-fix leak reproduces here instead of being hidden by an unreachable
#: database.
_CURRENT_DB: Dict[str, Any] = {"db": None}


@pytest.fixture(autouse=True)
def _database_binding(monkeypatch):
    from rbac_backend.core import database as database_module

    async def _fake_get_database():
        db = _CURRENT_DB["db"]
        assert db is not None, "no fake database bound for this test"
        return db

    monkeypatch.setattr(database_module, "get_database", _fake_get_database)


# ---------------------------------------------------------------------------
# Fixtures: one conversation whose members straddle a sibling project and a
# foreign organisation, plus a curated document set covering both boundaries.
# ---------------------------------------------------------------------------


def _letter(
    *,
    letter_id: str,
    organization_id: str,
    project_id: Optional[str],
    marker: str,
    previous_letter_id: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "_id": ObjectId(letter_id),
        "title": f"{marker} TITLE",
        "recipient": "recipient party",
        "subject": f"{marker} SUBJECT",
        "content": f"{marker} BODY TEXT",
        "status": "Draft",
        "created_by": "user-seed",
        "assigned_to": "user-seed",
        "organization_id": organization_id,
        "project_id": project_id,
        "conversation_id": CONVERSATION_ID,
        "previous_letter_id": previous_letter_id,
        "letter_no": f"LTR-{marker[:8]}",
    }
    if extra:
        row.update(extra)
    return row


def _document(
    *,
    doc_id: str,
    organization_id: str,
    project_id: str,
    marker: str,
    processing_status: Optional[str] = "metadata_extracted",
    duplicate_status: Optional[str] = None,
    lifecycle_state: Optional[str] = None,
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "_id": doc_id,
        "organization_id": organization_id,
        "project_id": project_id,
        "filename": f"{marker}.pdf",
        "filetype": "application/pdf",
        "filesize": 2048,
        "uploadType": "incoming",
        "letterNo": f"DOC-{marker[:8]}",
        "date": "2026-08-31T00:00:00Z",
        "subject": f"{marker} SUBJECT",
        "summary": f"{marker} SUMMARY BODY",
        "ocrText": f"{marker} OCR BODY",
        "status": "active",
        "createdAt": "2026-08-31T00:00:00Z",
        "updatedAt": "2026-08-31T00:00:00Z",
        "createdBy": "user-seed",
    }
    if processing_status is not None:
        row["processing_status"] = processing_status
    if duplicate_status is not None:
        row["duplicate_status"] = duplicate_status
    if lifecycle_state is not None:
        row["lifecycle_state"] = lifecycle_state
    return row


CURATED_IDS = [
    "cdoc-safe",
    "cdoc-sibling",
    "cdoc-foreign",
    "cdoc-review",
    "cdoc-duplicate",
    "cdoc-deleted",
    "cdoc-failed",
]

ALL_DOCUMENTS = [
    _document(
        doc_id="cdoc-safe",
        organization_id=ORG_A,
        project_id=PROJ_A,
        marker=CURATED_SAFE_MARKER,
    ),
    _document(
        doc_id="cdoc-sibling",
        organization_id=ORG_A,
        project_id=PROJ_B,
        marker=CURATED_SIBLING_MARKER,
    ),
    _document(
        doc_id="cdoc-foreign",
        organization_id=ORG_B,
        project_id=PROJ_C,
        marker=CURATED_FOREIGN_MARKER,
    ),
    _document(
        doc_id="cdoc-review",
        organization_id=ORG_A,
        project_id=PROJ_A,
        marker=CURATED_REVIEW_MARKER,
        processing_status="human_review_required",
    ),
    _document(
        doc_id="cdoc-duplicate",
        organization_id=ORG_A,
        project_id=PROJ_A,
        marker=CURATED_DUPLICATE_MARKER,
        duplicate_status="duplicate",
    ),
    _document(
        doc_id="cdoc-deleted",
        organization_id=ORG_A,
        project_id=PROJ_A,
        marker=CURATED_DELETED_MARKER,
        lifecycle_state="deleted",
    ),
    _document(
        doc_id="cdoc-failed",
        organization_id=ORG_A,
        project_id=PROJ_A,
        marker=CURATED_OPERATIONAL_MARKER,
        processing_status="failed",
    ),
]


def _letter_rows() -> List[Dict[str, Any]]:
    return [
        # Reply chain: anchor -> safe -> sibling project -> foreign organisation.
        _letter(
            letter_id=ANCHOR_ID,
            organization_id=ORG_A,
            project_id=PROJ_A,
            marker=ANCHOR_MARKER,
            previous_letter_id=THREAD_SAFE_ID,
            extra={
                "context_document_ids": list(CURATED_IDS),
                # Left over from an earlier, unbounded run. A clean regeneration
                # must not preserve it.
                "employer_context": STICKY_MARKER,
                # Legitimately stored by an authorised reader. A caller whose
                # entitlement resolves to nothing must not wipe it.
                "contractor_context": PRIOR_STORED_MARKER,
            },
        ),
        _letter(
            letter_id=THREAD_SAFE_ID,
            organization_id=ORG_A,
            project_id=PROJ_A,
            marker=THREAD_SAFE_MARKER,
            previous_letter_id=THREAD_SIBLING_ID,
        ),
        _letter(
            letter_id=THREAD_SIBLING_ID,
            organization_id=ORG_A,
            project_id=PROJ_B,
            marker=THREAD_SIBLING_MARKER,
            previous_letter_id=THREAD_FOREIGN_ID,
        ),
        _letter(
            letter_id=THREAD_FOREIGN_ID,
            organization_id=ORG_B,
            project_id=PROJ_C,
            marker=THREAD_FOREIGN_MARKER,
        ),
    ]


def _user(
    roles: List[str],
    *,
    organization_id: Optional[str],
    projects: List[str],
    organizations: Optional[List[str]] = None,
) -> CurrentUser:
    if organizations is None:
        organizations = [organization_id] if organization_id else []
    return CurrentUser(
        id="user-1",
        username="strategist",
        email="strategist@example.com",
        roles=roles,
        organization_id=organization_id,
        organizations=organizations,
        projects=projects,
    )


PROJECT_A_ACTOR = _user(["projectuser"], organization_id=ORG_A, projects=[PROJ_A])
ORG_A_ADMIN = _user(["orgadmin"], organization_id=ORG_A, projects=[])
SUPERADMIN = _user(["superadmin"], organization_id=None, projects=[])
#: Outside `build_scope_query`'s recognised tier set, so it denies all. That
#: carried G-A2 owner decision is NOT solved here; what is pinned is that such a
#: caller cannot DESTROY state it is not entitled to read.
DENIED_ACTOR = _user(["reporter"], organization_id=ORG_A, projects=[PROJ_A])


class _Controller:
    """The router's controller seam.

    `get_letter` stands in for the real letter authorisation and SUCCEEDS: the
    point is that authorisation to the ANCHOR letter must not transfer to the
    rest of the thread or to the curated documents.
    """

    def __init__(self, db: _Database) -> None:
        self.letter_service = LetterService(db)
        self.conversation_service = ConversationService(self.letter_service)
        # ConversationService resolves its own handle lazily; bind the fake so
        # the traversal never reaches a real database.
        self.conversation_service._db = db
        self.get_letter_calls: List[Any] = []

    async def get_letter(self, letter_id: str, current_user: Any):
        self.get_letter_calls.append((letter_id, current_user))
        return await self.letter_service.get_letter(letter_id)


def _build():
    db = _Database({"letters": _letter_rows(), "documents": ALL_DOCUMENTS})
    _CURRENT_DB["db"] = db
    return db, _Controller(db)


async def _run(current_user: CurrentUser):
    db, controller = _build()
    response = await generate_letter_strategy_context(
        letter_id=ANCHOR_ID,
        request=StrategyContextRequest(),
        controller=controller,
        current_user=current_user,
    )
    persisted = await db.letters.find_one({"_id": ObjectId(ANCHOR_ID)})
    return response, persisted, controller


def _text(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    return json.dumps(value, default=str)


def _prompt_text() -> str:
    return json.dumps(_RecordingOpenAI.sink, default=str)


def _persisted_context_text(persisted: Dict[str, Any]) -> str:
    return json.dumps(
        {
            field: persisted.get(field)
            for field in PERSISTED_CONTEXT_FIELDS + ("thread_letters",)
        },
        default=str,
    )


# ---------------------------------------------------------------------------
# Curated-document boundary — scope.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_sibling_project_curated_document_cannot_influence_strategy_context():
    response, persisted, _ = await _run(PROJECT_A_ACTOR)

    assert CURATED_SIBLING_MARKER not in _prompt_text()
    assert CURATED_SIBLING_MARKER not in _persisted_context_text(persisted)
    assert CURATED_SIBLING_MARKER not in _text(response)


@pytest.mark.asyncio
async def test_a_foreign_organisation_curated_document_cannot_influence_strategy_context():
    response, persisted, _ = await _run(PROJECT_A_ACTOR)

    assert CURATED_FOREIGN_MARKER not in _prompt_text()
    assert CURATED_FOREIGN_MARKER not in _persisted_context_text(persisted)
    assert CURATED_FOREIGN_MARKER not in _text(response)


# ---------------------------------------------------------------------------
# Curated-document boundary — publication authority.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_human_review_curated_document_cannot_influence_strategy_context():
    response, persisted, _ = await _run(PROJECT_A_ACTOR)

    assert CURATED_REVIEW_MARKER not in _prompt_text()
    assert CURATED_REVIEW_MARKER not in _persisted_context_text(persisted)
    assert CURATED_REVIEW_MARKER not in _text(response)


@pytest.mark.asyncio
async def test_a_quarantined_duplicate_curated_document_cannot_influence_strategy_context():
    _response, persisted, _ = await _run(PROJECT_A_ACTOR)

    assert CURATED_DUPLICATE_MARKER not in _prompt_text()
    assert CURATED_DUPLICATE_MARKER not in _persisted_context_text(persisted)


@pytest.mark.asyncio
async def test_a_soft_deleted_curated_document_cannot_influence_strategy_context():
    _response, persisted, _ = await _run(PROJECT_A_ACTOR)

    assert CURATED_DELETED_MARKER not in _prompt_text()
    assert CURATED_DELETED_MARKER not in _persisted_context_text(persisted)


# ---------------------------------------------------------------------------
# Thread boundary. `conversation_id` and a reply edge are association, not
# authority.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_sibling_project_thread_letter_cannot_influence_strategy_context():
    response, persisted, _ = await _run(PROJECT_A_ACTOR)

    assert THREAD_SIBLING_MARKER not in _prompt_text()
    assert THREAD_SIBLING_MARKER not in _persisted_context_text(persisted)
    assert THREAD_SIBLING_MARKER not in _text(response)


@pytest.mark.asyncio
async def test_a_foreign_organisation_thread_letter_cannot_influence_strategy_context():
    response, persisted, _ = await _run(PROJECT_A_ACTOR)

    assert THREAD_FOREIGN_MARKER not in _prompt_text()
    assert THREAD_FOREIGN_MARKER not in _persisted_context_text(persisted)
    assert THREAD_FOREIGN_MARKER not in _text(response)


@pytest.mark.asyncio
async def test_unauthorised_thread_letters_are_absent_from_persisted_provenance():
    """`thread_letters` is stored provenance, not a display list."""
    response, persisted, _ = await _run(PROJECT_A_ACTOR)

    stored = [str(value) for value in (persisted.get("thread_letters") or [])]
    assert THREAD_SIBLING_ID not in stored
    assert THREAD_FOREIGN_ID not in stored
    assert THREAD_SIBLING_ID not in [str(v) for v in response.thread_letters]
    assert THREAD_FOREIGN_ID not in [str(v) for v in response.thread_letters]

    timeline_ids = [str(entry.letter_id) for entry in response.timeline]
    assert THREAD_SIBLING_ID not in timeline_ids
    assert THREAD_FOREIGN_ID not in timeline_ids


@pytest.mark.asyncio
async def test_parent_letter_authorisation_does_not_transfer_to_the_thread():
    response, _persisted, controller = await _run(PROJECT_A_ACTOR)

    assert controller.get_letter_calls, "the anchor letter must still be authorised"

    allowed = {ANCHOR_ID, THREAD_SAFE_ID}
    assert set(str(v) for v in response.thread_letters) <= allowed


# ---------------------------------------------------------------------------
# Durable persistence — the surface that outlives the request.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actor",
    [PROJECT_A_ACTOR, ORG_A_ADMIN, SUPERADMIN],
    ids=["project-tier", "organisation-tier", "global"],
)
async def test_no_unauthorised_marker_reaches_the_persisted_strategy_context(actor):
    _response, persisted, _ = await _run(actor)
    stored = _persisted_context_text(persisted)

    for marker in FORBIDDEN_MARKERS:
        assert marker not in stored, f"unauthorised marker persisted: {marker}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actor",
    [PROJECT_A_ACTOR, ORG_A_ADMIN, SUPERADMIN],
    ids=["project-tier", "organisation-tier", "global"],
)
async def test_no_unauthorised_marker_reaches_the_synthesis_prompt(actor):
    """Filtering must precede synthesis. Stripping the answer is too late."""
    await _run(actor)
    prompts = _prompt_text()

    for marker in FORBIDDEN_MARKERS:
        assert marker not in prompts, f"unauthorised marker reached synthesis: {marker}"


@pytest.mark.asyncio
async def test_a_contaminated_prior_context_does_not_survive_a_clean_regeneration():
    """A role with no authorised sources CLEARS its stored context.

    Otherwise the `None` a filtered role now produces is dropped by the generic
    update path and the previous, unbounded run's text stays on the letter
    forever.
    """
    _response, persisted, _ = await _run(PROJECT_A_ACTOR)

    assert STICKY_MARKER not in _persisted_context_text(persisted)
    assert not persisted.get("employer_context")


@pytest.mark.asyncio
async def test_a_denied_caller_reads_nothing_and_destroys_nothing():
    """Fail closed must not mean fail destructive.

    A caller whose entitlement resolves to no rows produced no view of this
    letter. Persisting their empty view would erase context other readers
    legitimately stored.
    """
    response, persisted, _ = await _run(DENIED_ACTOR)

    assert response.thread_letters == []
    assert not response.contractor_context
    assert PRIOR_STORED_MARKER in str(persisted.get("contractor_context"))
    assert STICKY_MARKER in str(persisted.get("employer_context"))


# ---------------------------------------------------------------------------
# Positives. A containment fix that empties strategy context is not a fix.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_in_scope_thread_letter_still_contributes():
    response, persisted, _ = await _run(PROJECT_A_ACTOR)

    assert THREAD_SAFE_MARKER in _prompt_text()
    assert THREAD_SAFE_MARKER in _persisted_context_text(persisted)
    assert THREAD_SAFE_ID in [str(v) for v in response.thread_letters]


@pytest.mark.asyncio
async def test_the_anchor_letter_still_contributes():
    _response, persisted, _ = await _run(PROJECT_A_ACTOR)

    assert ANCHOR_MARKER in _persisted_context_text(persisted)


@pytest.mark.asyncio
async def test_the_in_scope_consumable_curated_document_still_contributes():
    _response, persisted, _ = await _run(PROJECT_A_ACTOR)

    assert CURATED_SAFE_MARKER in _prompt_text()
    assert CURATED_SAFE_MARKER in _persisted_context_text(persisted)


@pytest.mark.asyncio
async def test_an_operational_processing_failure_does_not_retract_last_known_good():
    """`failed` is OPERATIONAL - the pipeline broke without reaching a verdict.

    Only an outcome ABOUT THE CONTENT may retract a previous publication. Do
    not "harden" this into a denial.
    """
    _response, persisted, _ = await _run(PROJECT_A_ACTOR)

    assert CURATED_OPERATIONAL_MARKER in _persisted_context_text(persisted)


@pytest.mark.asyncio
async def test_a_global_actor_is_bounded_by_the_anchor_letter_not_denied():
    """Global semantics preserved through canonical scope, not a role check."""
    response, persisted, _ = await _run(SUPERADMIN)
    stored = _persisted_context_text(persisted)

    assert THREAD_SAFE_MARKER in stored
    assert CURATED_SAFE_MARKER in stored
    assert THREAD_SIBLING_MARKER not in stored
    assert THREAD_FOREIGN_MARKER not in stored
    assert THREAD_FOREIGN_ID not in [str(v) for v in response.thread_letters]


# ---------------------------------------------------------------------------
# Structural pins — fail closed by construction, not by remembering.
# ---------------------------------------------------------------------------


def test_strategy_context_generation_cannot_be_requested_without_an_actor():
    import inspect

    signature = inspect.signature(strategy_module.StrategyContextService.generate_context)
    assert "current_user" in signature.parameters
    assert signature.parameters["current_user"].default is inspect.Parameter.empty


def test_the_authorised_conversation_seam_requires_an_actor():
    import inspect

    seam = getattr(ConversationService, "get_authorized_conversation_chain", None)
    assert seam is not None, (
        "the scoped thread seam must exist separately from the generic "
        "get_conversation_chain, which has other callers"
    )
    signature = inspect.signature(seam)
    assert "current_user" in signature.parameters
    assert signature.parameters["current_user"].default is inspect.Parameter.empty
