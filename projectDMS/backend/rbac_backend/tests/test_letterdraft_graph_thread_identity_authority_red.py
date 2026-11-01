"""G30/G32: what the letter pipeline PUBLISHES as `graph_thread` is identity only.

The pipeline strips every graph node to `{"normCode": code}` while assembling
its thread, with a comment explaining why: the shared `(:Letter {normCode})`
node is MERGEd on `normCode` alone across documents and tenants, so any
`subject` / `date` / `direction` / `code` / `project` still sitting on it is
legacy contamination owned by whichever document wrote last.

After the post-draft Falkor sync the pipeline re-reads the thread and
REASSIGNS `graph_thread` from that read. The re-read is passed through
`_filter_consumable_graph_entries`, which answers only "does a consumable
supporter exist for this code?" and appends the entry UNMODIFIED - so the
denial containment is re-applied and the property strip is not. The result
flows to `LetterGraphResult.graph_thread`, into the API response and into the
persisted letter: the strip performed earlier in the same function is undone by
the last write.

This pins the FINAL published value, not the intermediate one, because the
final value is what regressed.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional

import pytest
from bson import ObjectId

from rbac_backend.ai_workflows.langgraph import letter_pipeline
from rbac_backend.ai_workflows.langgraph.letter_pipeline import LetterDraftGraph
from rbac_backend.models.ai_models import (
    LangGraphDraftRequest,
    LetterDraftRequest,
    LetterDraftResponse,
)
from rbac_backend.services import conversation_service as conversation_service_module
from rbac_backend.services.falkor_graph_service import (
    GLOBAL_LETTER_PROPERTIES,
    normalize_letter_code,
)

PRIMARY_CODE = "LANGGRAPH-THREAD-PRIMARY-20260830"
REFERENCE_CODE = "LANGGRAPH-THREAD-REF-20260830"
#: Values that only a FOREIGN writer could have left on the shared node.
CONTAMINATION = {
    "code": "OTHER-TENANT-CODE-20260830",
    "subject": "OTHER_TENANT_SUBJECT_MARKER_20260830",
    "direction": "incoming",
    "date": "2019-01-01",
    "project": "proj-of-another-tenant",
}


def _same(left: Any, right: Any) -> bool:
    return str(getattr(left, "value", left)) == str(getattr(right, "value", right))


def _field(document: Dict[str, Any], key: str):
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

    async def find_one(self, query: Optional[Dict[str, Any]] = None, projection=None, sort=None):
        for row in self.rows:
            if _matches(row, query):
                return deepcopy(row)
        return None

    def find(self, query: Optional[Dict[str, Any]] = None, *_a: Any, **_k: Any) -> _Cursor:
        return _Cursor(row for row in self.rows if _matches(row, query))

    async def insert_one(self, document: Dict[str, Any]):
        inserted = deepcopy(document)
        inserted.setdefault("_id", ObjectId())
        self.rows.append(inserted)
        return SimpleNamespace(inserted_id=inserted["_id"])

    async def update_one(self, query: Dict[str, Any], update: Dict[str, Any], upsert: bool = False):
        for row in self.rows:
            if _matches(row, query):
                row.update(deepcopy(update.get("$set", {})))
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)


class _Database:
    def __init__(self, *, letter: Dict[str, Any], documents: List[Dict[str, Any]]) -> None:
        self._collections = {
            "letters": _Collection([letter]),
            "documents": _Collection(documents),
        }

    def __getitem__(self, name: str) -> _Collection:
        return self._collections.setdefault(name, _Collection())

    def __getattr__(self, name: str) -> _Collection:
        return self[name]


class _ContaminatedFalkor:
    """A graph whose shared nodes still carry another writer's properties.

    37 of 215 live nodes did on the day this was written, so this is the state
    the reader must survive, not a hypothetical one.
    """

    enabled = True

    def __init__(self) -> None:
        self.upserts: List[Dict[str, Any]] = []

    def _node(self, code: str) -> Dict[str, Any]:
        node = {"normCode": normalize_letter_code(code)}
        node.update(CONTAMINATION)
        return node

    def get_thread(self, code: str, depth: int = 6) -> List[Dict[str, Any]]:
        return [self._node(code), self._node(REFERENCE_CODE)]

    def upsert_letter_with_refs(self, letter, references, **kwargs: Any) -> None:
        self.upserts.append({"letter": deepcopy(letter), "references": deepcopy(references)})


class _DeterministicLLM:
    def __init__(self, *_args: Any, **_kwargs: Any) -> None:
        pass

    async def generate(self, prompt: str, **_kwargs: Any) -> str:
        if "senior reviewer" in prompt.lower():
            return ""
        return "Deterministic source-grounded draft."


def _document(document_id: ObjectId, *, letter_no: str, **overrides: Any) -> Dict[str, Any]:
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    document = {
        "_id": document_id,
        "organization_id": "org-A",
        "project_id": "proj-A",
        "filename": f"{letter_no}.pdf",
        "filetype": "application/pdf",
        "filesize": 100,
        "uploadType": "incoming",
        "letterNo": letter_no,
        "letterNoNormalized": normalize_letter_code(letter_no),
        "date": now,
        "subject": f"In-scope subject {letter_no}",
        "status": "Processed",
        "summary": "In-scope supporting context",
        "processing_status": "completed",
        "duplicate_status": "unique",
        "lifecycle_state": "active",
        "createdAt": now,
        "updatedAt": now,
        "createdBy": "user-A",
        "uploadedBy": "user-A",
    }
    document.update(overrides)
    return document


def _letter(letter_id: ObjectId) -> Dict[str, Any]:
    now = datetime(2026, 8, 30, tzinfo=timezone.utc)
    return {
        "_id": letter_id,
        "title": "Authority drafting run",
        "recipient": "Engineer",
        "subject": "Draft response",
        "content": "Prepare a source-grounded response.",
        "status": "Draft",
        "created_by": "user-A",
        "assigned_to": "user-A",
        "organization_id": "org-A",
        "project_id": "proj-A",
        "letter_no": PRIMARY_CODE,
        "date": now,
        "created_at": now,
        "updated_at": now,
    }


async def _fallback_draft(request: LetterDraftRequest, _current_user: Any) -> LetterDraftResponse:
    return LetterDraftResponse(subject=request.subject, body="Fallback draft", key_points=[])


async def _run(monkeypatch: pytest.MonkeyPatch):
    letter_id = ObjectId()
    context_id = ObjectId()
    db = _Database(
        letter=_letter(letter_id),
        documents=[
            _document(letter_id, letter_no=PRIMARY_CODE, subject="Primary draft identity"),
            _document(context_id, letter_no=REFERENCE_CODE),
        ],
    )
    graph = _ContaminatedFalkor()

    async def get_db():
        return db

    monkeypatch.setattr(letter_pipeline, "get_database", get_db)
    monkeypatch.setattr(conversation_service_module, "get_database", get_db)
    monkeypatch.setattr(letter_pipeline, "FalkorGraphService", lambda: graph)
    monkeypatch.setattr(letter_pipeline, "LLMGenerator", _DeterministicLLM)

    request = LangGraphDraftRequest(
        letter_id=str(letter_id),
        subject="Draft response",
        recipient="Engineer",
        context="Use the selected correspondence.",
        document_ids=[str(context_id)],
        organization_id="org-A",
        project_id="proj-A",
        use_vector_store=False,
        plan_override="Deterministic approved plan.",
    )
    actor = SimpleNamespace(
        # The pipeline now resolves every drafting source against this actor's
        # canonical entitlement (G-A5). org-A / proj-A is the workspace this
        # file's letter and documents already live in.
        id="user-A",
        roles=["orgadmin"],
        organization_id="org-A",
        organizations=["org-A"],
        projects=["proj-A"],
    )
    result = await LetterDraftGraph(_fallback_draft).run(request, actor)
    return result, graph


@pytest.mark.asyncio
async def test_published_graph_thread_carries_identity_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _graph = await _run(monkeypatch)

    assert result.graph_thread, "the run produced no graph thread to assert on"
    for entry in result.graph_thread:
        leaked = sorted(set(entry) - {"normCode"})
        assert not leaked, (
            f"LetterGraphResult.graph_thread published {leaked} off the shared "
            "Letter node; that node is MERGEd on normCode alone, so those values "
            "belong to whichever document wrote last - possibly another tenant's"
        )
        assert entry["normCode"]


@pytest.mark.asyncio
async def test_no_foreign_node_property_value_reaches_the_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result, _graph = await _run(monkeypatch)

    published = repr(result.graph_thread)
    for name, value in CONTAMINATION.items():
        assert value not in published, (
            f"foreign `{name}` value from the shared Letter node reached the "
            "published graph thread"
        )


@pytest.mark.asyncio
async def test_identity_only_matches_the_writers_global_property_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reader and writer are pinned to the same contract, not to two rules."""
    result, _graph = await _run(monkeypatch)

    for entry in result.graph_thread:
        assert set(entry) <= GLOBAL_LETTER_PROPERTIES
