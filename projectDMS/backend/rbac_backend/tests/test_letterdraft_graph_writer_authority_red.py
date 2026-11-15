"""G31 publication authority for the legacy LetterDraftGraph Falkor writer.

The public workflow is exercised end to end. Mongo and Falkor are persistent
in-test system boundaries so assertions describe final graph state rather than
collaborator call counts.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import os
from types import SimpleNamespace
from typing import Any, Callable, Iterable
import uuid

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
from rbac_backend.services import publication_policy as publication_policy_module
from rbac_backend.services.falkor_graph_service import normalize_letter_code


SOURCE_MARKER = "LANGGRAPH_BLOCKED_SRC_20260819"
REFERENCE_CODE = "LANGGRAPH-BLOCKED-REF-20260819"
PRIMARY_CODE = "LANGGRAPH-DRAFT-PRIMARY-20260819"


def _value(value: Any) -> Any:
    return getattr(value, "value", value)


def _same(left: Any, right: Any) -> bool:
    return str(_value(left)) == str(_value(right))


def _field(document: dict[str, Any], key: str) -> tuple[bool, Any]:
    value: Any = document
    for part in key.split("."):
        if not isinstance(value, dict) or part not in value:
            return False, None
        value = value[part]
    return True, value


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
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
            if "$in" in expected and not any(_same(actual, candidate) for candidate in expected["$in"]):
                return False
            if "$ne" in expected and _same(actual, expected["$ne"]):
                return False
            continue
        if not _same(actual, expected):
            return False
    return True


class _Cursor:
    def __init__(self, documents: Iterable[dict[str, Any]]) -> None:
        self.documents = [deepcopy(document) for document in documents]

    def sort(self, key: Any, direction: int = 1) -> "_Cursor":
        keys = key if isinstance(key, list) else [(key, direction)]
        for item_key, item_direction in reversed(keys):
            self.documents.sort(
                key=lambda row: row.get(item_key) or datetime.min,
                reverse=item_direction == -1,
            )
        return self

    def skip(self, amount: int) -> "_Cursor":
        self.documents = self.documents[amount:]
        return self

    def limit(self, amount: int) -> "_Cursor":
        self.documents = self.documents[:amount]
        return self

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        rows = self.documents if length is None else self.documents[:length]
        return deepcopy(rows)

    def __aiter__(self):
        async def generate():
            for document in self.documents:
                yield deepcopy(document)

        return generate()


class _Collection:
    def __init__(self, documents: Iterable[dict[str, Any]] = ()) -> None:
        self.documents = [deepcopy(document) for document in documents]

    async def find_one(self, query: dict[str, Any], projection=None, sort=None):
        rows = [document for document in self.documents if _matches(document, query)]
        for key, direction in reversed(sort or []):
            rows.sort(key=lambda row: row.get(key) or 0, reverse=direction == -1)
        return deepcopy(rows[0]) if rows else None

    def find(self, query: dict[str, Any], *_a: Any, **_k: Any) -> _Cursor:
        return _Cursor(document for document in self.documents if _matches(document, query))

    async def insert_one(self, document: dict[str, Any]):
        inserted = deepcopy(document)
        inserted.setdefault("_id", ObjectId())
        self.documents.append(inserted)
        return SimpleNamespace(inserted_id=inserted["_id"])

    async def update_one(self, query: dict[str, Any], update: dict[str, Any], upsert: bool = False):
        for document in self.documents:
            if _matches(document, query):
                document.update(deepcopy(update.get("$set", {})))
                return SimpleNamespace(matched_count=1, modified_count=1)
        if upsert:
            created = {key: value for key, value in query.items() if not key.startswith("$")}
            created.update(deepcopy(update.get("$set", {})))
            self.documents.append(created)
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=created.get("_id"))
        return SimpleNamespace(matched_count=0, modified_count=0)


class _Database:
    def __init__(self, *, letter: dict[str, Any], documents: list[dict[str, Any]]) -> None:
        self.letters = _Collection([letter])
        self.documents = _Collection(documents)
        self.document_comments = _Collection()
        self.app_settings = _Collection()
        self._collections = {
            "letters": self.letters,
            "documents": self.documents,
            "document_comments": self.document_comments,
            "app_settings": self.app_settings,
        }

    def __getitem__(self, name: str) -> _Collection:
        return self._collections.setdefault(name, _Collection())


class _PhysicalFalkor:
    """Persistent graph boundary with the ownership semantics used by Falkor."""

    enabled = True

    def __init__(self) -> None:
        self.nodes: dict[str, dict[str, Any]] = {}
        self.edges: dict[tuple[str, str, str, str], dict[str, Any]] = {}
        self.before_next_upsert: Callable[[], None] | None = None
        self.upsert_history: list[dict[str, Any]] = []

    def get_thread(self, _code: str, depth: int = 6) -> list[dict[str, Any]]:
        return [deepcopy(node) for node in self.nodes.values()]

    def upsert_letter_with_refs(
        self,
        letter: dict[str, Any],
        references: list[dict[str, Any]],
        *,
        cleanup: bool | None = None,
        owner_document_id: str | None = None,
    ) -> None:
        if self.before_next_upsert is not None:
            callback, self.before_next_upsert = self.before_next_upsert, None
            callback()

        src = normalize_letter_code(letter.get("normCode") or letter.get("code") or "")
        owner = str(owner_document_id or "")
        self.nodes.setdefault(src, {"normCode": src})
        for reference in references:
            dst = normalize_letter_code(
                reference.get("normCode") or reference.get("code") or ""
            )
            relation = "REPLIES_TO" if reference.get("type") == "REPLIES_TO" else "CITES"
            self.nodes.setdefault(dst, {"normCode": dst})
            key = (src, relation, dst, owner)
            self.edges[key] = {
                "source": src,
                "relation": relation,
                "target": dst,
                "owner_document_id": owner,
                "writer_source": reference.get("source"),
            }
        self.upsert_history.append(
            {
                "source": src,
                "references": deepcopy(references),
                "cleanup": cleanup,
                "owner_document_id": owner,
            }
        )

    def owned_edges(self, *, target: str | None = None) -> list[dict[str, Any]]:
        rows = list(self.edges.values())
        if target is not None:
            normalized = normalize_letter_code(target)
            rows = [row for row in rows if row["target"] == normalized]
        return deepcopy(rows)

    def clear_edges(self) -> None:
        self.edges.clear()


class _DeterministicLLM:
    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def generate(self, prompt: str, **_kwargs) -> str:
        if "senior reviewer" in prompt.lower():
            return ""
        return "Deterministic source-grounded draft."


def _document(
    document_id: ObjectId,
    *,
    letter_no: str,
    **overrides: Any,
) -> dict[str, Any]:
    now = datetime(2026, 8, 19, tzinfo=timezone.utc)
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
        "subject": f"{SOURCE_MARKER} {letter_no}",
        "status": "Processed",
        "summary": f"{SOURCE_MARKER} supporting context",
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


def _letter(letter_id: ObjectId) -> dict[str, Any]:
    now = datetime(2026, 8, 19, tzinfo=timezone.utc)
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


def _transition(db: _Database, document_id: ObjectId, authority: dict[str, Any] | None):
    def apply() -> None:
        if authority is None:
            db.documents.documents = [
                row for row in db.documents.documents if not _same(row.get("_id"), document_id)
            ]
            return
        for row in db.documents.documents:
            if _same(row.get("_id"), document_id):
                row.update(deepcopy(authority))
                return

    return apply


async def _fallback_draft(
    request: LetterDraftRequest, _current_user: Any
) -> LetterDraftResponse:
    return LetterDraftResponse(subject=request.subject, body="Fallback draft", key_points=[])


def _request(letter_id: ObjectId, document_ids: list[ObjectId]) -> LangGraphDraftRequest:
    return LangGraphDraftRequest(
        letter_id=str(letter_id),
        subject="Draft response",
        recipient="Engineer",
        context="Use the selected correspondence.",
        document_ids=[str(document_id) for document_id in document_ids],
        organization_id="org-A",
        project_id="proj-A",
        use_vector_store=False,
        plan_override="Deterministic approved plan.",
    )


def _user() -> SimpleNamespace:
    # The pipeline now resolves every drafting source against this actor's
    # canonical entitlement (G-A5), so the fixture has to carry one. org-A /
    # proj-A is exactly the workspace this file's letter and documents live in,
    # so nothing this file asserts changes.
    return SimpleNamespace(
        id="user-A",
        roles=["orgadmin"],
        organization_id="org-A",
        organizations=["org-A"],
        projects=["proj-A"],
    )


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    db: _Database,
    graph: _PhysicalFalkor,
    request: LangGraphDraftRequest,
):
    async def get_db():
        return db

    monkeypatch.setattr(letter_pipeline, "get_database", get_db)
    monkeypatch.setattr(conversation_service_module, "get_database", get_db)

    # Apply the deterministic transition immediately before the caller's
    # publication-time canonical read. On the unfixed path no such read exists,
    # so the fake Falkor boundary applies it immediately before persistence.
    real_resolve_authority = publication_policy_module.resolve_document_authority

    async def resolve_after_transition(database: Any, document_id: Any):
        if graph.before_next_upsert is not None:
            callback, graph.before_next_upsert = graph.before_next_upsert, None
            callback()
        return await real_resolve_authority(database, document_id)

    monkeypatch.setattr(
        publication_policy_module,
        "resolve_document_authority",
        resolve_after_transition,
    )
    monkeypatch.setattr(letter_pipeline, "FalkorGraphService", lambda: graph)
    monkeypatch.setattr(letter_pipeline, "LLMGenerator", _DeterministicLLM)
    return await LetterDraftGraph(_fallback_draft).run(request, _user())


def _setup(
    context_documents: list[dict[str, Any]],
) -> tuple[_Database, _PhysicalFalkor, ObjectId]:
    letter_id = ObjectId()
    primary_document = _document(letter_id, letter_no=PRIMARY_CODE, subject="Primary draft identity")
    return (
        _Database(letter=_letter(letter_id), documents=[primary_document, *context_documents]),
        _PhysicalFalkor(),
        letter_id,
    )


STATIC_MATRIX = [
    ("clean", {}, True),
    ("operational_failed", {"processing_status": "failed"}, True),
    ("human_review", {"processing_status": "human_review_required"}, False),
    ("duplicate_status", {"duplicate_status": "duplicate"}, False),
    ("duplicate_lifecycle", {"lifecycle_state": "duplicate"}, False),
    ("deleted", {"lifecycle_state": "deleted"}, False),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "authority", "should_publish"),
    STATIC_MATRIX,
    ids=[case for case, _authority, _expected in STATIC_MATRIX],
)
async def test_static_canonical_authority_controls_letterdraft_graph_assertions(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    authority: dict[str, Any],
    should_publish: bool,
) -> None:
    context_id = ObjectId()
    db, graph, letter_id = _setup(
        [_document(context_id, letter_no=REFERENCE_CODE, **authority)]
    )

    result = await _run(monkeypatch, db, graph, _request(letter_id, [context_id]))
    edges = graph.owned_edges(target=REFERENCE_CODE)

    assert bool(edges) is should_publish, (case, edges, result.warnings)
    assert all(edge["owner_document_id"] for edge in edges)
    assert all(edge["relation"] == "CITES" for edge in edges)
    assert all(edge["relation"] != "REPLIES_TO" for edge in graph.owned_edges())


@pytest.mark.asyncio
async def test_each_reference_edge_is_owned_by_its_context_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context_id = ObjectId()
    db, graph, letter_id = _setup([_document(context_id, letter_no=REFERENCE_CODE)])

    await _run(monkeypatch, db, graph, _request(letter_id, [context_id]))

    edges = graph.owned_edges(target=REFERENCE_CODE)
    assert len(edges) == 1
    assert edges[0]["owner_document_id"] == str(context_id)


TOCTOU_MATRIX = [
    ("human_review", {"processing_status": "human_review_required"}),
    ("duplicate", {"duplicate_status": "duplicate"}),
    ("deleted", {"lifecycle_state": "deleted"}),
    ("missing", None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("case", "authority"),
    TOCTOU_MATRIX,
    ids=[case for case, _authority in TOCTOU_MATRIX],
)
async def test_letterdraft_rechecks_canonical_authority_immediately_before_falkor_write(
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    authority: dict[str, Any] | None,
) -> None:
    context_id = ObjectId()
    db, graph, letter_id = _setup([_document(context_id, letter_no=REFERENCE_CODE)])
    graph.before_next_upsert = _transition(db, context_id, authority)

    result = await _run(monkeypatch, db, graph, _request(letter_id, [context_id]))

    assert graph.owned_edges(target=REFERENCE_CODE) == [], case
    assert normalize_letter_code(REFERENCE_CODE) not in graph.nodes
    assert not any(edge["owner_document_id"] == "" for edge in graph.owned_edges())
    assert normalize_letter_code(REFERENCE_CODE) not in {
        row.get("normCode") for row in result.graph_thread
    }


@pytest.mark.asyncio
@pytest.mark.integration
async def test_letterdraft_denied_transition_writes_no_physical_falkor_assertion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prove the workflow invariant against the real FalkorDB engine."""
    pytest.importorskip("redis")
    from rbac_backend.services.falkor_graph_service import (
        FalkorGraphConfig,
        FalkorGraphService,
    )

    from rbac_backend.tests.authority_band_graph import (
        disposable_graph_name,
        drop_disposable_graph,
    )

    graph_name = disposable_graph_name("letterdraft")
    graph = FalkorGraphService(
        FalkorGraphConfig(
            host=os.environ.get("FALKOR_TEST_HOST", "localhost"),
            port=int(os.environ.get("FALKOR_TEST_PORT", "6380")),
            graph_name=graph_name,
            password=None,
            enabled=True,
            cleanup=False,
        )
    )
    try:
        graph._get_client().ping()
    except Exception as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"FalkorDB not reachable for workflow proof: {exc}")

    context_id = ObjectId()
    db, _fake_graph, letter_id = _setup(
        [_document(context_id, letter_no=REFERENCE_CODE)]
    )
    graph.before_next_upsert = _transition(
        db, context_id, {"processing_status": "human_review_required"}
    )

    try:
        # Ensure the isolated graph exists even when the safe workflow performs
        # no authority-controlled write at all.
        graph._execute("MERGE (:Probe {id: 'letterdraft-authority'})")
        result = await _run(monkeypatch, db, graph, _request(letter_id, [context_id]))
        physical = graph._execute(
            "MATCH (:Letter {normCode: $source})-[r]->"
            "(:Letter {normCode: $target}) RETURN count(r)",
            {
                "source": normalize_letter_code(PRIMARY_CODE),
                "target": normalize_letter_code(REFERENCE_CODE),
            },
        )
        rows = physical[1] if len(physical) > 1 else []
        assert (int(rows[0][0][1]) if rows else 0) == 0
        target = graph._execute(
            "MATCH (l:Letter {normCode: $target}) RETURN count(l)",
            {"target": normalize_letter_code(REFERENCE_CODE)},
        )
        target_rows = target[1] if len(target) > 1 else []
        assert (int(target_rows[0][0][1]) if target_rows else 0) == 0
        assert normalize_letter_code(REFERENCE_CODE) not in {
            row.get("normCode") for row in result.graph_thread
        }
    finally:
        # A swallowed teardown is what left `g31_letterdraft_d0fdb2ac7c` on the
        # shared instance: the delete failed and nothing recorded it. The
        # sanctioned helper retries, verifies against GRAPH.LIST and RAISES,
        # and refuses any name this run did not mint.
        drop_disposable_graph(graph._get_client(), graph_name)


@pytest.mark.asyncio
async def test_valid_supporter_survives_while_stale_blocked_supporter_cannot_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    supporter_a = ObjectId()
    supporter_b = ObjectId()
    db, graph, letter_id = _setup(
        [
            _document(supporter_a, letter_no=REFERENCE_CODE),
            _document(supporter_b, letter_no=REFERENCE_CODE),
        ]
    )

    await _run(monkeypatch, db, graph, _request(letter_id, [supporter_a]))
    graph.before_next_upsert = _transition(
        db, supporter_b, {"processing_status": "human_review_required"}
    )
    await _run(monkeypatch, db, graph, _request(letter_id, [supporter_b]))

    edges = graph.owned_edges(target=REFERENCE_CODE)
    assert {edge["owner_document_id"] for edge in edges} == {str(supporter_a)}
    assert len(graph.nodes) == 2
    assert all(set(node) == {"normCode"} for node in graph.nodes.values())


@pytest.mark.asyncio
async def test_denied_reentry_cannot_republish_a_purged_owned_assertion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context_id = ObjectId()
    db, graph, letter_id = _setup([_document(context_id, letter_no=REFERENCE_CODE)])
    request = _request(letter_id, [context_id])

    await _run(monkeypatch, db, graph, request)
    assert graph.owned_edges(target=REFERENCE_CODE)
    graph.clear_edges()
    graph.before_next_upsert = _transition(
        db, context_id, {"processing_status": "human_review_required"}
    )

    result = await _run(monkeypatch, db, graph, request)

    assert graph.owned_edges(target=REFERENCE_CODE) == []
    assert normalize_letter_code(REFERENCE_CODE) not in {
        row.get("normCode") for row in result.graph_thread
    }
