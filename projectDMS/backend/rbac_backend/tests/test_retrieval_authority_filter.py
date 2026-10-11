"""Caller metadata filters may narrow the authorised retrieval scope, never replace it.

`POST /api/v1/retrieval/search` authorises `filters.org_id` / `filters.project_id`
through `PolicyService`, then built the vector filter as

    {"org_id": <authorised>, "project_id": <authorised>, ..., **filters.metadata}

so a caller-supplied `filters.metadata.org_id` silently replaced the authorised
organisation, and `project_id: None` / a list value widened it. The Mongo contract
fallback (`_search_contract_mongo`) copied metadata keys straight onto the query
the same way.

These tests drive the real route function, the real `PolicyService` +
`ScopeService`, the real `RetrievalService`, and the real `VectorClient` against
both its in-memory index and a real local Qdrant engine
(`QdrantClient(":memory:")`), so the filter that decides visibility is the one
production builds - not a dict inspected in isolation.

`POST /api/v1/retrieval/agent` had the same defect one level up: it authorised
`request.org_id` / `request.project_id` and then searched with the caller's
separate `request.filters`, and it loaded and appended to `agent_conversations`
by caller-supplied `conversation_id` alone.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from fastapi import HTTPException
from qdrant_client import QdrantClient, models

from rbac_backend.agents.models import AgentRequest
from rbac_backend.agents.service import DraftingAgentService
from rbac_backend.config.document_processing_config import DocumentProcessingConfig
from rbac_backend.retrieval.models import (
    SearchBackend,
    SearchFilters,
    SearchRequest,
    SearchResponse,
)
from rbac_backend.retrieval.service import RetrievalService
from rbac_backend.retrieval.vector_client import VectorClient
from rbac_backend.routers.retrieval_engine import search
from rbac_backend.services.policy_service import PolicyService
from rbac_backend.services.scope_service import ScopeService


# --- Mongo-shaped fakes that evaluate the real predicates --------------------


def _resolve(doc: Dict[str, Any], path: str) -> Any:
    current: Any = doc
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _eq(actual: Any, expected: Any) -> bool:
    if isinstance(actual, list) and not isinstance(expected, list):
        return expected in actual
    return actual == expected


def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for key, expected in (query or {}).items():
        if key == "$and":
            if not all(_matches(doc, c) for c in expected):
                return False
            continue
        if key == "$or":
            if not any(_matches(doc, c) for c in expected):
                return False
            continue
        if key.startswith("$"):
            raise AssertionError(f"unsupported top-level operator in fake: {key}")
        actual = _resolve(doc, key)
        if isinstance(expected, dict) and expected and all(k.startswith("$") for k in expected):
            for op, operand in expected.items():
                if op == "$in":
                    if not any(_eq(actual, item) for item in operand):
                        return False
                elif op == "$all":
                    if not all(_eq(actual, item) for item in operand):
                        return False
                elif op == "$ne":
                    if _eq(actual, operand):
                        return False
                elif op == "$exists":
                    if (actual is not None) != bool(operand):
                        return False
                else:
                    raise AssertionError(f"unsupported operator in fake: {op}")
            continue
        if not _eq(actual, expected):
            return False
    return True


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def limit(self, _n):
        return self

    def sort(self, *_a, **_k):
        return self

    def __aiter__(self):
        self._iter = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration:
            raise StopAsyncIteration

    async def to_list(self, length=None):
        return list(self._docs)


class _Collection:
    def __init__(self, docs=()):
        self.docs: List[Dict[str, Any]] = [copy.deepcopy(d) for d in docs]
        self.queries: List[Dict[str, Any]] = []
        self.updates: List[Dict[str, Any]] = []

    def find(self, query=None, *_a, **_k):
        self.queries.append(query or {})
        return _Cursor([copy.deepcopy(d) for d in self.docs if _matches(d, query or {})])

    async def find_one(self, query=None, *_a, **_k):
        self.queries.append(query or {})
        for doc in self.docs:
            if _matches(doc, query or {}):
                return copy.deepcopy(doc)
        return None

    async def insert_one(self, doc):
        self.docs.append(copy.deepcopy(doc))
        return SimpleNamespace(inserted_id=doc.get("_id"))

    async def update_one(self, query, update, upsert=False):
        self.updates.append({"query": query, "update": update})
        for doc in self.docs:
            if _matches(doc, query):
                for field, value in (update.get("$set") or {}).items():
                    doc[field] = value
                for field, value in (update.get("$push") or {}).items():
                    doc.setdefault(field, []).append(value)
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)


class _DB:
    def __init__(self, **collections):
        self._collections = {k: _Collection(v) for k, v in collections.items()}
        self._collections.setdefault(
            "projects",
            _Collection(
                [
                    {"_id": "proj-A", "organization_id": "org-A"},
                    {"_id": "proj-A2", "organization_id": "org-A"},
                    {"_id": "proj-B", "organization_id": "org-B"},
                ]
            ),
        )

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        return self._collections.setdefault(name, _Collection())

    def __getitem__(self, name):
        return getattr(self, name)


# --- Wiring ------------------------------------------------------------------


class _Allow:
    async def user_has_permission(self, *_a, **_k):
        return True

    async def check_permission_entitlement(self, **_k):
        return True, "ok"

    async def check_and_record(self, **_k):
        return None

    async def emit(self, **_k):
        return None


def _policy(db) -> PolicyService:
    allow = _Allow()
    return PolicyService(
        permission_service=allow,
        scope_service=ScopeService(db=db),
        entitlement_service=allow,
        audit_service=allow,
        usage_metering_service=allow,
    )


def _user(roles=("projectuser",), org="org-A", projects=("proj-A",)):
    return SimpleNamespace(
        id="user-A",
        roles=list(roles),
        organization_id=org,
        organizations=[org] if org else [],
        projects=list(projects),
        account_type="client_user",
    )


class _Embed:
    async def embed(self, texts, model=None):
        return [[1.0, 0.5] for _ in texts]


class _Gen:
    async def generate(self, prompt, max_tokens=512, model=None, **_k):
        return "draft"


class _Obs:
    async def log_run(self, **_k):
        return None


_CHUNKS = [
    ("chunk-A", "org-A", "proj-A", "doc-A", "alpha text for A", "delay"),
    ("chunk-A-pay", "org-A", "proj-A", "doc-A", "alpha payment text for A", "payment"),
    ("chunk-A2", "org-A", "proj-A2", "doc-A2", "alpha text for A2", "delay"),
    ("chunk-B", "org-B", "proj-B", "doc-B", "alpha text for B", "delay"),
]


def _payload(chunk_id, org, project, doc, text, section):
    return {
        "org_id": org,
        "project_id": project,
        "document_id": doc,
        "chunk_id": chunk_id,
        "page": 1,
        "text": text,
        "tags": [],
        "section": section,
    }


def _memory_vector_client() -> VectorClient:
    config = DocumentProcessingConfig()
    config.qdrant_url = None
    vc = VectorClient(config)
    for row in _CHUNKS:
        vc._memory_index.append(
            {
                "vector": [1.0, 0.5],
                "payload": _payload(*row),
                "namespace": vc.collection_name or config.qdrant_collection,
            }
        )
    return vc


def _qdrant_vector_client() -> VectorClient:
    """The real VectorClient driving a real (local, in-process) Qdrant engine."""
    config = DocumentProcessingConfig()
    config.qdrant_url = None
    config.qdrant_vector_name = None
    vc = VectorClient(config)
    collection = vc.collection_name or config.qdrant_collection
    client = QdrantClient(":memory:")
    client.create_collection(
        collection,
        vectors_config=models.VectorParams(size=2, distance=models.Distance.COSINE),
    )
    client.upsert(
        collection,
        points=[
            models.PointStruct(id=i + 1, vector=[1.0, 0.5], payload=_payload(*row))
            for i, row in enumerate(_CHUNKS)
        ],
    )
    vc._client = client
    vc._qmodels = models
    vc.enabled = True
    return vc


VECTOR_BACKENDS = [
    pytest.param(_memory_vector_client, id="memory-index"),
    pytest.param(_qdrant_vector_client, id="qdrant-local"),
]


def _service(db, vc) -> RetrievalService:
    return RetrievalService(
        db=db,
        embedding_client=_Embed(),
        vector_client=vc,
        llm_generator=_Gen(),
        observability=_Obs(),
    )


async def _search(vc_factory, metadata: Optional[Dict[str, Any]] = None, user=None) -> SearchResponse:
    db = _DB()
    request = SearchRequest(
        query="alpha",
        limit=20,
        backend=SearchBackend.QDRANT,
        filters=SearchFilters(org_id="org-A", project_id="proj-A", metadata=metadata or {}),
    )
    return await search(
        request,
        retrieval_service=_service(db, vc_factory()),
        current_user=user or _user(),
        policy=_policy(db),
    )


def _chunks(resp: SearchResponse) -> set:
    return {r.chunk_id for r in resp.results}


AUTHORISED = {"chunk-A", "chunk-A-pay"}


# --- Vector path through the real route ----------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("vc_factory", VECTOR_BACKENDS)
async def test_no_user_filters_returns_authorised_scope_only(vc_factory):
    assert _chunks(await _search(vc_factory)) == AUTHORISED


@pytest.mark.asyncio
@pytest.mark.parametrize("vc_factory", VECTOR_BACKENDS)
async def test_ordinary_metadata_still_narrows(vc_factory):
    assert _chunks(await _search(vc_factory, {"section": "payment"})) == {"chunk-A-pay"}


@pytest.mark.asyncio
@pytest.mark.parametrize("vc_factory", VECTOR_BACKENDS)
async def test_own_org_in_metadata_works_and_does_not_widen(vc_factory):
    got = _chunks(await _search(vc_factory, {"org_id": "org-A"}))
    assert got == AUTHORISED


@pytest.mark.asyncio
@pytest.mark.parametrize("vc_factory", VECTOR_BACKENDS)
@pytest.mark.parametrize(
    "metadata",
    [
        pytest.param({"org_id": "org-B"}, id="foreign-org"),
        pytest.param({"project_id": "proj-B"}, id="foreign-project"),
        pytest.param({"org_id": "org-B", "project_id": "proj-B"}, id="foreign-org+project"),
        pytest.param({"project_id": "proj-A2"}, id="sibling-project-same-org"),
        pytest.param({"project_id": None}, id="null-project-widens"),
        pytest.param({"org_id": ["org-A", "org-B"]}, id="list-org-widens"),
        pytest.param({"project_id": ["proj-A", "proj-A2", "proj-B"]}, id="list-project-widens"),
        pytest.param({"organization_id": "org-B"}, id="alias-organization_id"),
        pytest.param({"tenant_id": "org-B", "workspace_id": "proj-B"}, id="alias-tenant-workspace"),
        pytest.param({"metadata.org_id": "org-B"}, id="nested-dotted"),
        pytest.param({"ORG_ID": "org-B"}, id="case-variant"),
    ],
)
async def test_caller_metadata_cannot_override_or_widen_scope(vc_factory, metadata):
    got = _chunks(await _search(vc_factory, metadata))
    # Zero foreign records, and nothing outside the authorised project.
    assert got <= AUTHORISED, got


@pytest.mark.asyncio
@pytest.mark.parametrize("vc_factory", VECTOR_BACKENDS)
async def test_org_tier_user_is_bounded_by_the_authorised_filter_not_metadata(vc_factory):
    user = _user(roles=("orguser",), projects=())
    got = _chunks(await _search(vc_factory, {"org_id": "org-B", "project_id": "proj-B"}, user=user))
    assert got <= AUTHORISED, got


@pytest.mark.asyncio
async def test_foreign_scope_in_the_authorised_fields_is_still_refused_by_the_gate():
    db = _DB()
    request = SearchRequest(
        query="alpha",
        backend=SearchBackend.QDRANT,
        filters=SearchFilters(org_id="org-B", project_id="proj-B"),
    )
    with pytest.raises(HTTPException) as exc:
        await search(
            request,
            retrieval_service=_service(db, _memory_vector_client()),
            current_user=_user(),
            policy=_policy(db),
        )
    assert exc.value.status_code == 403


# --- Mongo fallbacks (contract document_vectors, legacy chunks) ---------------


def _contract_rows():
    return [
        {
            "_id": f"dv-{org}-{project}",
            "uploadType": "contract",
            "organization_id": org,
            "project_id": project,
            "document_id": f"doc-{project}",
            "chunk_id": f"dv-{org}-{project}",
            "text": "alpha clause text",
        }
        for org, project in (("org-A", "proj-A"), ("org-A", "proj-A2"), ("org-B", "proj-B"))
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata",
    [
        pytest.param({"organization_id": "org-B"}, id="foreign-org"),
        pytest.param({"project_id": "proj-B"}, id="foreign-project"),
        pytest.param({"organization_id": "org-B", "project_id": "proj-B"}, id="foreign-both"),
        pytest.param({"project_id": "proj-A2"}, id="sibling-project"),
        pytest.param({"$or": [{"organization_id": "org-B"}]}, id="operator-key"),
    ],
)
async def test_contract_mongo_fallback_cannot_be_redirected(metadata):
    db = _DB(document_vectors=_contract_rows())
    svc = _service(db, _memory_vector_client())
    request = SearchRequest(
        query="alpha",
        backend=SearchBackend.MONGO,
        filters=SearchFilters(
            org_id="org-A",
            project_id="proj-A",
            metadata={"uploadType": "contract", **metadata},
        ),
    )
    try:
        rows = await svc._search_contract_mongo(request)
    except AssertionError:
        # The fake refuses operators it cannot evaluate; production Mongo would
        # have evaluated this one. Either way it must not have reached the query.
        pytest.fail(f"operator key reached the Mongo query: {db.document_vectors.queries[-1]}")
    got = {r["payload"]["chunk_id"] for r in rows}
    assert got <= {"dv-org-A-proj-A"}, got
    for query in db.document_vectors.queries:
        assert query.get("organization_id") == "org-A"
        assert query.get("project_id") == "proj-A"


@pytest.mark.asyncio
async def test_contract_mongo_fallback_keeps_ordinary_narrowing():
    rows = _contract_rows()
    rows[0]["clause_no"] = "8.4"
    extra = dict(rows[0], _id="dv-A-other", chunk_id="dv-A-other", clause_no="13.3")
    db = _DB(document_vectors=rows + [extra])
    svc = _service(db, _memory_vector_client())
    request = SearchRequest(
        query="alpha",
        backend=SearchBackend.MONGO,
        filters=SearchFilters(
            org_id="org-A", project_id="proj-A", metadata={"uploadType": "contract", "clause_no": "8.4"}
        ),
    )
    got = {r["payload"]["chunk_id"] for r in await svc._search_contract_mongo(request)}
    assert got == {"dv-org-A-proj-A"}


@pytest.mark.asyncio
async def test_legacy_chunk_fallback_keeps_authority_fields():
    chunks = [
        {"org_id": o, "project_id": p, "document_id": d, "chunk_id": c, "text": t, "metadata": {"section": s}}
        for (c, o, p, d, t, s) in _CHUNKS
    ]
    db = _DB(chunks=chunks)
    svc = _service(db, _memory_vector_client())
    request = SearchRequest(
        query="alpha",
        backend=SearchBackend.MONGO,
        filters=SearchFilters(org_id="org-A", project_id="proj-A", metadata={"org_id": "org-B", "section": "delay"}),
    )
    got = {r["payload"]["chunk_id"] for r in await svc._search_mongo(request)}
    assert got == {"chunk-A"}


# --- The authority/user split itself -----------------------------------------


def test_authority_keys_cover_every_scope_field_the_stores_use():
    from rbac_backend.retrieval.authority import AUTHORITY_FILTER_KEYS

    # Payload fields (`VectorClient.upsert`, clause payloads) and Mongo fields
    # (`document_vectors`, `chunks`, `contract_clauses`) that carry tenancy.
    assert {"org_id", "organization_id", "project_id", "tenant_id", "workspace_id"} <= set(
        AUTHORITY_FILTER_KEYS
    )


@pytest.mark.parametrize(
    "key",
    [
        "org_id", "organization_id", "project_id", "tenant_id", "workspace_id",
        "ORG_ID", " project_id ", "metadata.org_id", "payload.project_id",
        "metadata.metadata.organization_id", "org-id", "$or", "$where", "$expr",
    ],
)
def test_user_metadata_filters_drops_authority_and_operator_keys(key):
    from rbac_backend.retrieval.authority import user_metadata_filters

    assert user_metadata_filters({key: "org-B", "section": "delay"}) == {"section": "delay"}


def test_user_metadata_filters_tolerates_empty_input():
    from rbac_backend.retrieval.authority import user_metadata_filters

    assert user_metadata_filters(None) == {}
    assert user_metadata_filters({}) == {}


# --- /v1/retrieval/agent: authorised scope is the only scope -----------------


class _RecordingRetrieval:
    def __init__(self):
        self.requests: List[SearchRequest] = []

    async def search(self, request, current_user, log_run=True):
        self.requests.append(request)
        return SearchResponse(results=[], strategy_used=request.strategy, backend_used=SearchBackend.MONGO)


def _agent(db, retrieval) -> DraftingAgentService:
    return DraftingAgentService(
        db=db, retrieval_service=retrieval, llm_generator=_Gen(), observability=_Obs()
    )


@pytest.mark.asyncio
async def test_agent_search_uses_the_authorised_scope_not_caller_filters():
    retrieval = _RecordingRetrieval()
    request = AgentRequest(
        org_id="org-A",
        project_id="proj-A",
        incoming_text="please reply",
        filters=SearchFilters(org_id="org-B", project_id="proj-B", metadata={"org_id": "org-B"}),
    )
    await _agent(_DB(), retrieval).run(request, _user())

    (sent,) = retrieval.requests
    assert (sent.filters.org_id, sent.filters.project_id) == ("org-A", "proj-A")


@pytest.mark.asyncio
async def test_agent_keeps_caller_narrowing_filters():
    retrieval = _RecordingRetrieval()
    request = AgentRequest(
        org_id="org-A",
        project_id="proj-A",
        incoming_text="please reply",
        filters=SearchFilters(org_id="org-A", project_id="proj-A", document_id="doc-A", tags=["t"]),
    )
    await _agent(_DB(), retrieval).run(request, _user())

    (sent,) = retrieval.requests
    assert sent.filters.document_id == "doc-A" and sent.filters.tags == ["t"]


@pytest.mark.asyncio
async def test_agent_cannot_append_to_another_tenants_conversation():
    foreign = {
        "conversation_id": "conv-B",
        "org_id": "org-B",
        "project_id": "proj-B",
        "participants": ["user-B"],
        "messages": [{"role": "user", "content": "SECRET-B"}],
    }
    db = _DB(agent_conversations=[foreign])
    before = copy.deepcopy(db.agent_conversations.docs)
    request = AgentRequest(
        org_id="org-A", project_id="proj-A", incoming_text="x", conversation_id="conv-B"
    )

    with pytest.raises(HTTPException) as exc:
        await _agent(db, _RecordingRetrieval()).run(request, _user())

    assert exc.value.status_code == 404
    assert db.agent_conversations.docs == before
    assert db.agent_messages.docs == []


@pytest.mark.asyncio
async def test_agent_continues_its_own_conversation():
    own = {"conversation_id": "conv-A", "org_id": "org-A", "project_id": "proj-A", "participants": ["user-A"]}
    db = _DB(agent_conversations=[own])
    request = AgentRequest(org_id="org-A", project_id="proj-A", incoming_text="x", conversation_id="conv-A")

    resp = await _agent(db, _RecordingRetrieval()).run(request, _user())

    assert resp.conversation_id == "conv-A"
    assert len(db.agent_conversations.docs) == 1
    assert db.agent_conversations.docs[0].get("messages")
