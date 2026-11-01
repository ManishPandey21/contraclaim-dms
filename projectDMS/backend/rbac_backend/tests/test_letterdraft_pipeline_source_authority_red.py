"""G-A5: the LangGraph letter-draft pipeline may only draft from AUTHORISED sources.

`LetterDraftGraph.run` assembles its drafting context from three widening
sources, and every one of them used to escape the actor's entitlement:

* the **letterNo document fallback** — `db.documents.find({"letterNo": ...})`
  with no actor at all. `letterNo` is a GLOBAL string that collides across
  projects and across organisations, so matching it is association, never
  authority. This is the second copy of the defect closed for
  `get_context_documents` in G-A3;
* the **curated / fallback document hydration** — `get_documents_by_ids`,
  which is documented as NOT scope-bounded and applies no publication
  authority either. A hand-rolled `in_scope()` compared each row against the
  LETTER's organisation/project rather than the ACTOR's entitlement, which is
  a workspace-consistency check wearing an authority costume;
* the **conversation thread** — `get_conversation_chain(letter_id,
  current_user=...)`, which accepts the actor and never reads it. The family it
  assembles comes from `previous_letter_id` edges, and a reply edge may cross
  projects and organisations.

What makes this materially worse than a transient read is the tail of the
pipeline: `AIService.generate_draft_with_langgraph` calls
`LetterService.record_langgraph_result`, which `$set`s `context_documents`,
`context_document_ids`, `draft_sources`, `background_summary`, `graph_thread`
and `plan_context_text` onto the letter and `$push`es an immutable
`draft_versions` entry carrying the body and its `sources`. Unauthorised
material therefore becomes DURABLE state that every later reader inherits
without ever revisiting the original authority.

So every test here asserts on four surfaces, not one:

1. the captured LLM prompts (plan and draft) — proving the filtering happens
   BEFORE synthesis. Stripping a marker out of the answer afterwards is too
   late, because the model already saw it;
2. the returned response;
3. the re-read persisted letter row;
4. the persisted `draft_versions` entry — the provenance snapshot.

Provenance is asserted as well as text: a foreign document id may not survive
in `context_document_ids` or in a `draft_sources` entry merely because its
summary was removed.

The positive cases are load-bearing too. `processing_status == "failed"` is
OPERATIONAL — the pipeline broke, which is not a verdict about the content —
so a last-known-good document must still contribute, and an in-scope
consumable document must still reach the prompt. A containment that empties
drafting is not a containment, it is an outage.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pytest
from bson import ObjectId

from rbac_backend.ai_workflows.langgraph import letter_pipeline
from rbac_backend.utils.error_handler import LetterError
from rbac_backend.models.ai_models import LangGraphDraftRequest
from rbac_backend.services import ai_service as ai_service_module
from rbac_backend.services import conversation_service as conversation_service_module
from rbac_backend.services.ai_service import AIService
from rbac_backend.services.falkor_graph_service import normalize_letter_code

# --- unique markers -------------------------------------------------------
#
# Every marker is unique so an assertion can never pass because the string
# happened to be absent for an unrelated reason.

SHARED_CODE = "GA5-SHARED-LETTERNO-20260831"
SAFE_MARKER = "SAFE_PROJECT_A_CONTEXT_20260831"
SIBLING_PROJECT_MARKER = "PROJECT_B_DRAFT_CONFIDENTIAL_20260831"
FOREIGN_ORG_MARKER = "ORG_B_DRAFT_CONFIDENTIAL_20260831"
BLOCKED_MARKER = "HUMAN_REVIEW_DRAFT_CONFIDENTIAL_20260831"
DUPLICATE_MARKER = "DUPLICATE_DRAFT_CONFIDENTIAL_20260831"
DELETED_MARKER = "DELETED_DRAFT_CONFIDENTIAL_20260831"
LAST_KNOWN_GOOD_MARKER = "LAST_KNOWN_GOOD_CONTEXT_20260831"
THREAD_SAFE_MARKER = "THREAD_PROJECT_A_CONTEXT_20260831"
THREAD_SIBLING_MARKER = "THREAD_PROJECT_B_CONFIDENTIAL_20260831"
THREAD_FOREIGN_MARKER = "THREAD_ORG_B_CONFIDENTIAL_20260831"
GRAPH_POISON_MARKER = "GRAPH_NODE_FOREIGN_SUBJECT_20260831"


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
            if "$ne" in expected and _same(actual, expected["$ne"]):
                return False
            continue
        if not _same(actual, expected):
            return False
    return True


class _Cursor:
    def __init__(self, rows: Iterable[Dict[str, Any]]) -> None:
        self.rows = [deepcopy(row) for row in rows]

    def sort(self, *_a: Any, **_k: Any) -> "_Cursor":
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

    async def find_one(self, query=None, projection=None, sort=None):
        for row in self.rows:
            if _matches(row, query):
                return deepcopy(row)
        return None

    def find(self, query=None, *_a: Any, **_k: Any) -> _Cursor:
        return _Cursor(row for row in self.rows if _matches(row, query))

    async def count_documents(self, query=None, **_k: Any) -> int:
        return len([row for row in self.rows if _matches(row, query)])

    async def insert_one(self, document: Dict[str, Any]):
        inserted = deepcopy(document)
        inserted.setdefault("_id", ObjectId())
        self.rows.append(inserted)
        return SimpleNamespace(inserted_id=inserted["_id"])

    async def update_one(self, query, update, upsert: bool = False):
        for row in self.rows:
            if not _matches(row, query):
                continue
            row.update(deepcopy(update.get("$set", {})))
            for key, value in (update.get("$push") or {}).items():
                row.setdefault(key, []).append(deepcopy(value))
            return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)


class _Database:
    def __init__(self, letters: List[Dict[str, Any]], documents: List[Dict[str, Any]]) -> None:
        self._collections: Dict[str, _Collection] = {
            "letters": _Collection(letters),
            "documents": _Collection(documents),
        }

    def __getitem__(self, name: str) -> _Collection:
        return self._collections.setdefault(name, _Collection())

    def __getattr__(self, name: str) -> _Collection:
        return self[name]


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _DisabledFalkor:
    """Most cases have nothing to do with the graph; say so explicitly."""

    enabled = False

    def get_thread(self, *_a: Any, **_k: Any) -> List[Dict[str, Any]]:  # pragma: no cover
        return []

    def upsert_letter_with_refs(self, *_a: Any, **_k: Any) -> None:  # pragma: no cover
        return None


class _PoisonedFalkor:
    """A shared `(:Letter {normCode})` node still carrying a foreign writer's fields.

    The node is MERGEd on `normCode` alone, so `subject` / `summary` / `date` /
    `direction` / `project` belong to whichever document wrote last — possibly
    another tenant's.
    """

    enabled = True

    def get_thread(self, code: str, depth: int = 6) -> List[Dict[str, Any]]:
        return [
            {
                "normCode": normalize_letter_code(code),
                "subject": GRAPH_POISON_MARKER,
                "summary": GRAPH_POISON_MARKER,
                "direction": "incoming",
                "date": "2019-01-01",
                "project": "proj-of-another-tenant",
            }
        ]

    def upsert_letter_with_refs(self, *_a: Any, **_k: Any) -> None:
        return None


class _NoRetrieval:
    """The vector store is not part of this boundary; keep it out of the run.

    Left real it reaches a live Qdrant, which makes an authority assertion
    depend on a network service being down in the right way.
    """

    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    async def search(self, *_a: Any, **_k: Any):
        return SimpleNamespace(results=[])


class _RecordingLLM:
    """Captures every prompt the pipeline builds, before any answer exists."""

    prompts: List[str] = []

    def __init__(self, *_a: Any, **_k: Any) -> None:
        pass

    async def generate(self, prompt: str, **_k: Any) -> str:
        type(self).prompts.append(prompt)
        if "senior reviewer" in prompt.lower():
            return ""
        return "Deterministic source-grounded draft."


# ---------------------------------------------------------------------------
# Fixtures for the world
# ---------------------------------------------------------------------------

NOW = datetime(2026, 8, 31, tzinfo=timezone.utc)


def _document(
    document_id: ObjectId,
    *,
    letter_no: str,
    organization_id: str = "org-A",
    project_id: str = "proj-A",
    summary: str = SAFE_MARKER,
    **overrides: Any,
) -> Dict[str, Any]:
    document = {
        "_id": document_id,
        "organization_id": organization_id,
        "project_id": project_id,
        "filename": f"{letter_no}.pdf",
        "filetype": "application/pdf",
        "filesize": 100,
        "uploadType": "incoming",
        "letterNo": letter_no,
        "letterNoNormalized": normalize_letter_code(letter_no),
        "date": NOW,
        "subject": summary,
        "status": "Processed",
        "summary": summary,
        "processing_status": "metadata_extracted",
        "duplicate_status": "unique",
        "lifecycle_state": "active",
        "createdAt": NOW,
        "updatedAt": NOW,
        "createdBy": "user-A",
        "uploadedBy": "user-A",
    }
    document.update(overrides)
    return document


def _letter(
    letter_id: ObjectId,
    *,
    organization_id: str = "org-A",
    project_id: str = "proj-A",
    subject: str = "Draft response",
    content: str = "Prepare a source-grounded response.",
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


def _org_actor() -> SimpleNamespace:
    """Organisation A, organisation-wide, no project assignments: the narrowest
    actor whose canonical row visibility includes an organisation-level letter."""
    return SimpleNamespace(
        id="user-org-A",
        roles=["orguser"],
        organization_id="org-A",
        organizations=["org-A"],
        projects=[],
    )


def _global_actor() -> SimpleNamespace:
    """A global actor, resolved through canonical authorization — not by name."""
    return SimpleNamespace(
        id="user-super",
        roles=["superadmin"],
        organization_id="org-A",
        organizations=["org-A", "org-B"],
        projects=["proj-A", "proj-B"],
    )


class _Run:
    def __init__(self, response: Any, letter_row: Dict[str, Any], prompts: List[str]) -> None:
        self.response = response
        self.letter_row = letter_row
        self.prompts = prompts

    @property
    def draft_versions(self) -> List[Dict[str, Any]]:
        return self.letter_row.get("draft_versions") or []

    def surfaces(self) -> Dict[str, str]:
        """Every place drafting material can come to rest, named for the failure text."""
        return {
            "the LLM prompts (filtering must precede synthesis)": "\n".join(self.prompts),
            "the returned draft response": repr(self.response.model_dump()),
            "the PERSISTED letter row": repr(self.letter_row),
            "the PERSISTED draft_versions snapshot": repr(self.draft_versions),
        }

    def provenance_ids(self) -> str:
        return repr(
            {
                "context_document_ids": self.letter_row.get("context_document_ids"),
                "context_documents": self.letter_row.get("context_documents"),
                "draft_sources": self.letter_row.get("draft_sources"),
                "background_summary": self.letter_row.get("background_summary"),
                "draft_versions_sources": [
                    version.get("sources") for version in self.draft_versions
                ],
            }
        )


async def _run_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    *,
    letters: List[Dict[str, Any]],
    documents: List[Dict[str, Any]],
    letter_id: ObjectId,
    actor: Any = None,
    document_ids: Optional[List[str]] = None,
    falkor: Any = None,
    request_project_id: Optional[str] = "proj-A",
    runs: int = 1,
) -> _Run:
    """Drive the REAL pipeline and the REAL persistence, exactly as the route does."""
    db = _Database(letters, documents)
    _RecordingLLM.prompts = []

    async def get_db():
        return db

    monkeypatch.setattr(letter_pipeline, "get_database", get_db)
    monkeypatch.setattr(conversation_service_module, "get_database", get_db)
    monkeypatch.setattr(ai_service_module, "get_database", get_db)
    monkeypatch.setattr(
        letter_pipeline, "FalkorGraphService", lambda: (falkor or _DisabledFalkor())
    )
    monkeypatch.setattr(letter_pipeline, "LLMGenerator", _RecordingLLM)
    monkeypatch.setattr(letter_pipeline, "RetrievalService", _NoRetrieval)

    request = LangGraphDraftRequest(
        letter_id=str(letter_id),
        subject="Draft response",
        recipient="Engineer",
        context="Respond on the basis of the linked correspondence.",
        document_ids=document_ids or [],
        organization_id="org-A",
        project_id=request_project_id,
        use_vector_store=False,
    )

    response = None
    for _ in range(runs):
        response = await AIService().generate_draft_with_langgraph(
            request, actor or _project_actor()
        )

    stored = await db["letters"].find_one({"_id": letter_id})
    return _Run(response, stored or {}, list(_RecordingLLM.prompts))


def _assert_absent(run: _Run, marker: str, why: str) -> None:
    for surface, text in run.surfaces().items():
        assert marker not in text, (
            f"{why}: the marker {marker!r} reached {surface}. "
            "Association is not authority — a shared letterNo, a reply edge or a "
            "curated id list does not entitle this actor to the material."
        )


def _assert_present(run: _Run, marker: str, why: str) -> None:
    combined = "\n".join(run.surfaces().values())
    assert marker in combined, (
        f"{why}: the authorised marker {marker!r} reached NONE of the drafting "
        "surfaces. Containment that empties drafting is an outage, not a boundary."
    )


# ---------------------------------------------------------------------------
# RED 1-2 — the letterNo document fallback crosses project and organisation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_sibling_project_document_sharing_the_letter_number_is_not_drafted_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An ORGANISATION-level letter with no project selection. `project_id` is
    # optional on a letter and the route passes through whatever the scope
    # resolver returned, so this is the shape in which the pipeline had no
    # project value to compare against - and the removed hand-rolled check
    # then waved every project in the tenant through.
    #
    # The project-tier actor used here cannot see that letter at all: its
    # canonical row visibility (`build_scope_query`, and `authorize_scope` on
    # `GET /letters/{id}`) is its assigned projects. This test once passed only
    # because the langgraph routes loaded the letter by bare id; they now
    # refuse it before the pipeline runs, so nothing - sibling-project
    # material included - reaches a prompt.
    letter_id, safe_id, foreign_id = ObjectId(), ObjectId(), ObjectId()
    with pytest.raises(LetterError) as exc:
        await _run_pipeline(
            monkeypatch,
            letters=[_letter(letter_id, project_id=None)],
            documents=[
                _document(safe_id, letter_no=SHARED_CODE),
                _document(
                    foreign_id,
                    letter_no=SHARED_CODE,
                    project_id="proj-B",
                    summary=SIBLING_PROJECT_MARKER,
                ),
            ],
            letter_id=letter_id,
            request_project_id=None,
        )

    assert exc.value.http_status == 404
    assert _RecordingLLM.prompts == [], "a refused letter still reached a prompt"


@pytest.mark.asyncio
async def test_a_foreign_organisation_document_sharing_the_letter_number_is_not_drafted_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The organisation-level letter is drafted by an organisation-wide actor,
    # the narrowest one entitled to it (see the sibling-project test above for
    # why a project-tier actor no longer reaches it).
    letter_id, safe_id, foreign_id = ObjectId(), ObjectId(), ObjectId()
    run = await _run_pipeline(
        monkeypatch,
        letters=[_letter(letter_id, project_id=None)],
        documents=[
            _document(safe_id, letter_no=SHARED_CODE),
            _document(
                foreign_id,
                letter_no=SHARED_CODE,
                organization_id="org-B",
                project_id="proj-B",
                summary=FOREIGN_ORG_MARKER,
            ),
        ],
        letter_id=letter_id,
        actor=_org_actor(),
        request_project_id=None,
    )

    _assert_absent(run, FOREIGN_ORG_MARKER, "cross-tenant letterNo fallback")
    assert str(foreign_id) not in run.provenance_ids(), (
        "a foreign-organisation document id survived in the persisted provenance"
    )


@pytest.mark.asyncio
async def test_a_project_tier_actor_cannot_draft_an_organisation_level_letter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    letter_id, foreign_id = ObjectId(), ObjectId()
    with pytest.raises(LetterError) as exc:
        await _run_pipeline(
            monkeypatch,
            letters=[_letter(letter_id, project_id=None)],
            documents=[
                _document(
                    foreign_id,
                    letter_no=SHARED_CODE,
                    organization_id="org-B",
                    project_id="proj-B",
                    summary=FOREIGN_ORG_MARKER,
                ),
            ],
            letter_id=letter_id,
            request_project_id=None,
        )

    assert exc.value.http_status == 404
    assert _RecordingLLM.prompts == [], "a refused letter still reached a prompt"


# ---------------------------------------------------------------------------
# RED 3-4 — publication authority is a SECOND, independent axis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_in_scope_document_awaiting_human_review_is_not_drafted_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    letter_id, safe_id, blocked_id = ObjectId(), ObjectId(), ObjectId()
    run = await _run_pipeline(
        monkeypatch,
        letters=[_letter(letter_id)],
        documents=[
            _document(safe_id, letter_no=SHARED_CODE),
            _document(
                blocked_id,
                letter_no=SHARED_CODE,
                summary=BLOCKED_MARKER,
                processing_status="human_review_required",
            ),
        ],
        letter_id=letter_id,
    )

    _assert_absent(run, BLOCKED_MARKER, "publication-denied document")
    assert str(blocked_id) not in run.provenance_ids(), (
        "a blocked document id survived in the persisted provenance"
    )


@pytest.mark.asyncio
async def test_a_quarantined_duplicate_is_not_drafted_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    letter_id, safe_id, duplicate_id = ObjectId(), ObjectId(), ObjectId()
    run = await _run_pipeline(
        monkeypatch,
        letters=[_letter(letter_id)],
        documents=[
            _document(safe_id, letter_no=SHARED_CODE),
            _document(
                duplicate_id,
                letter_no=SHARED_CODE,
                summary=DUPLICATE_MARKER,
                duplicate_status="duplicate",
                lifecycle_state="duplicate",
            ),
        ],
        letter_id=letter_id,
    )

    _assert_absent(run, DUPLICATE_MARKER, "quarantined duplicate")


@pytest.mark.asyncio
async def test_a_deleted_document_is_not_drafted_from(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    letter_id, safe_id, deleted_id = ObjectId(), ObjectId(), ObjectId()
    run = await _run_pipeline(
        monkeypatch,
        letters=[_letter(letter_id)],
        documents=[
            _document(safe_id, letter_no=SHARED_CODE),
            _document(
                deleted_id,
                letter_no=SHARED_CODE,
                summary=DELETED_MARKER,
                lifecycle_state="deleted",
            ),
        ],
        letter_id=letter_id,
    )

    _assert_absent(run, DELETED_MARKER, "soft-deleted document")


# ---------------------------------------------------------------------------
# RED 5 — the last-known-good positive. An OPERATIONAL failure is not a verdict.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_operational_processing_failure_does_not_retract_last_known_good(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`processing_status == "failed"` means the pipeline broke, not that the
    content is bad. Retracting on it turns an OCR crash into a drafting outage."""
    letter_id, failed_id = ObjectId(), ObjectId()
    run = await _run_pipeline(
        monkeypatch,
        letters=[_letter(letter_id)],
        documents=[
            _document(
                failed_id,
                letter_no=SHARED_CODE,
                summary=LAST_KNOWN_GOOD_MARKER,
                processing_status="failed",
            )
        ],
        letter_id=letter_id,
    )

    _assert_present(run, LAST_KNOWN_GOOD_MARKER, "operational failure suppressed content")


# ---------------------------------------------------------------------------
# RED 6-7 — the conversation thread crosses project and organisation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_sibling_project_thread_letter_cannot_reach_the_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    letter_id, prior_id = ObjectId(), ObjectId()
    run = await _run_pipeline(
        monkeypatch,
        letters=[
            _letter(letter_id, previous_letter_id=str(prior_id)),
            _letter(
                prior_id,
                project_id="proj-B",
                subject="Prior correspondence",
                content=THREAD_SIBLING_MARKER,
                letter_no="GA5-PRIOR-B-20260831",
            ),
        ],
        documents=[_document(ObjectId(), letter_no=SHARED_CODE)],
        letter_id=letter_id,
    )

    _assert_absent(run, THREAD_SIBLING_MARKER, "sibling-project thread letter")
    assert str(prior_id) not in run.provenance_ids(), (
        "a sibling-project letter id survived in the persisted draft provenance"
    )


@pytest.mark.asyncio
async def test_a_foreign_organisation_thread_letter_cannot_reach_the_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    letter_id, prior_id = ObjectId(), ObjectId()
    run = await _run_pipeline(
        monkeypatch,
        letters=[
            _letter(letter_id, previous_letter_id=str(prior_id)),
            _letter(
                prior_id,
                organization_id="org-B",
                project_id="proj-B",
                subject="Prior correspondence",
                content=THREAD_FOREIGN_MARKER,
                letter_no="GA5-PRIOR-ORGB-20260831",
            ),
        ],
        documents=[_document(ObjectId(), letter_no=SHARED_CODE)],
        letter_id=letter_id,
    )

    _assert_absent(run, THREAD_FOREIGN_MARKER, "cross-tenant thread letter")
    assert str(prior_id) not in run.provenance_ids(), (
        "a foreign-organisation letter id survived in the persisted draft provenance"
    )


# ---------------------------------------------------------------------------
# RED 8 — an unresolvable thread identity fails closed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_unresolvable_thread_edge_produces_no_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `previous_letter_id` pointing at nothing must not be narrated.

    The thread is hydrated canonically from `db.letters`; there is no secondary
    store to fall back on, and inventing a placeholder would be content without
    a source.
    """
    letter_id, dangling_id = ObjectId(), ObjectId()
    run = await _run_pipeline(
        monkeypatch,
        letters=[_letter(letter_id, previous_letter_id=str(dangling_id))],
        documents=[_document(ObjectId(), letter_no=SHARED_CODE)],
        letter_id=letter_id,
    )

    assert run.prompts, "the run produced no prompt to assert on"
    # The letter row itself legitimately still holds the dangling
    # `previous_letter_id`; what must stay clean is everything the RUN derived
    # from it.
    derived = {
        "the LLM prompts": " ".join(run.prompts),
        "the returned draft response": repr(run.response.model_dump()),
        "the persisted draft provenance": run.provenance_ids(),
        "the PERSISTED draft_versions snapshot": repr(run.draft_versions),
    }
    for surface, text in derived.items():
        assert str(dangling_id) not in text, (
            f"an unresolvable thread edge reached {surface}. A dangling "
            "`previous_letter_id` has no canonical row to hydrate from, and "
            "there is no secondary store to fall back on, so it must resolve "
            "to nothing rather than to a placeholder identity."
        )


# ---------------------------------------------------------------------------
# RED 9 — raw graph node properties are not drafting content
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_foreign_properties_on_the_shared_graph_node_never_persist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The graph identity is real here, so this case is NOT inapplicable.

    G-A1 pinned the RETURNED `graph_thread`. This pins the persisted one: the
    shared `(:Letter {normCode})` node is MERGEd on `normCode` alone, so a
    `subject` or `summary` sitting on it belongs to whichever document wrote
    last and must never become drafting content or durable letter state.
    """
    letter_id, safe_id = ObjectId(), ObjectId()
    run = await _run_pipeline(
        monkeypatch,
        letters=[_letter(letter_id)],
        documents=[_document(safe_id, letter_no=SHARED_CODE)],
        letter_id=letter_id,
        falkor=_PoisonedFalkor(),
    )

    _assert_absent(run, GRAPH_POISON_MARKER, "raw graph node property")
    for entry in run.letter_row.get("graph_thread") or []:
        assert set(entry) <= {"normCode"}, (
            f"the persisted graph_thread published {sorted(set(entry) - {'normCode'})} "
            "off the shared Letter node"
        )


# ---------------------------------------------------------------------------
# RED 10 — authority to the parent letter does not transfer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authorisation_to_the_parent_letter_does_not_authorise_a_curated_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A curated id list is a bookmark, not an entitlement.

    Whoever stored it is not necessarily the caller reading it, and this is the
    strongest form of the claim: the actor IS authorised for the letter, asks
    explicitly for the document, and still may not have it.
    """
    letter_id, foreign_id = ObjectId(), ObjectId()
    run = await _run_pipeline(
        monkeypatch,
        letters=[_letter(letter_id)],
        documents=[
            _document(
                foreign_id,
                letter_no="GA5-CURATED-B-20260831",
                organization_id="org-B",
                project_id="proj-B",
                summary=FOREIGN_ORG_MARKER,
            )
        ],
        letter_id=letter_id,
        document_ids=[str(foreign_id)],
    )

    _assert_absent(run, FOREIGN_ORG_MARKER, "explicitly curated foreign document")
    assert str(foreign_id) not in run.provenance_ids(), (
        "an explicitly requested but unauthorised document id survived in the "
        "persisted provenance"
    )


# ---------------------------------------------------------------------------
# RED 11-12 — persistence and rerun semantics
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_persisted_draft_version_provenance_lists_only_authorised_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`draft_versions` is immutable history. What lands in it lands forever."""
    letter_id, safe_id, foreign_id = ObjectId(), ObjectId(), ObjectId()
    run = await _run_pipeline(
        monkeypatch,
        letters=[_letter(letter_id)],
        documents=[
            _document(safe_id, letter_no=SHARED_CODE),
            _document(
                foreign_id,
                letter_no=SHARED_CODE,
                organization_id="org-B",
                project_id="proj-B",
                summary=FOREIGN_ORG_MARKER,
            ),
        ],
        letter_id=letter_id,
    )

    assert run.draft_versions, "the run persisted no draft version to assert on"
    snapshot = repr(run.draft_versions)
    assert FOREIGN_ORG_MARKER not in snapshot
    assert str(foreign_id) not in snapshot, (
        "a foreign document id is recorded as a source of the persisted draft "
        "version; provenance must name only authorised contributors"
    )


@pytest.mark.asyncio
async def test_a_rerun_does_not_reuse_a_stale_contaminated_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second run re-resolves authority; it does not inherit the first snapshot.

    The letter carries a pre-existing `context_document_ids` entry pointing at a
    foreign document — exactly the shape a pre-fix run would have left behind —
    and a clean rerun must neither re-hydrate it nor carry it forward.
    """
    letter_id, safe_id, foreign_id = ObjectId(), ObjectId(), ObjectId()
    run = await _run_pipeline(
        monkeypatch,
        letters=[
            _letter(
                letter_id,
                context_document_ids=[str(foreign_id)],
                context_documents=[{"id": str(foreign_id), "summary": FOREIGN_ORG_MARKER}],
            )
        ],
        documents=[
            _document(safe_id, letter_no=SHARED_CODE),
            _document(
                foreign_id,
                letter_no="GA5-STALE-B-20260831",
                organization_id="org-B",
                project_id="proj-B",
                summary=FOREIGN_ORG_MARKER,
            ),
        ],
        letter_id=letter_id,
        runs=2,
    )

    assert len(run.draft_versions) == 2, "the rerun did not produce a fresh version"
    for surface in ("context_document_ids", "context_documents", "draft_sources"):
        assert FOREIGN_ORG_MARKER not in repr(run.letter_row.get(surface)), (
            f"the stale contaminated `{surface}` survived a clean rerun"
        )
        assert str(foreign_id) not in repr(run.letter_row.get(surface)), (
            f"a stale unauthorised id survived a clean rerun in `{surface}`"
        )
    assert FOREIGN_ORG_MARKER not in repr(run.draft_versions)


# ---------------------------------------------------------------------------
# RED 13-14 — the positives. The boundary must not become an outage.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_authorised_thread_and_document_still_reach_the_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    letter_id, prior_id, safe_id = ObjectId(), ObjectId(), ObjectId()
    run = await _run_pipeline(
        monkeypatch,
        letters=[
            _letter(letter_id, previous_letter_id=str(prior_id)),
            _letter(
                prior_id,
                subject="Prior correspondence",
                content=THREAD_SAFE_MARKER,
                letter_no="GA5-PRIOR-A-20260831",
            ),
        ],
        documents=[_document(safe_id, letter_no=SHARED_CODE)],
        letter_id=letter_id,
    )

    _assert_present(run, SAFE_MARKER, "in-scope consumable document dropped")
    _assert_present(run, THREAD_SAFE_MARKER, "in-scope thread letter dropped")


@pytest.mark.asyncio
async def test_a_global_actor_is_resolved_through_canonical_authorisation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No role name is read at this seam; entitlement is asked of the canonical
    primitive, and the letter's own workspace narrows INSIDE it. A global actor
    therefore still drafts from project A's material and not from project B's."""
    letter_id, safe_id, foreign_id = ObjectId(), ObjectId(), ObjectId()
    run = await _run_pipeline(
        monkeypatch,
        letters=[_letter(letter_id)],
        documents=[
            _document(safe_id, letter_no=SHARED_CODE),
            _document(
                foreign_id,
                letter_no=SHARED_CODE,
                project_id="proj-B",
                summary=SIBLING_PROJECT_MARKER,
            ),
        ],
        letter_id=letter_id,
        actor=_global_actor(),
    )

    _assert_present(run, SAFE_MARKER, "global actor lost in-scope material")
    _assert_absent(run, SIBLING_PROJECT_MARKER, "global actor outside the active selection")
