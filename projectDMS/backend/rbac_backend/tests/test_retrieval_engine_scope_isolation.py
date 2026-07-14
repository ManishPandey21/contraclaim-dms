"""Cross-tenant isolation tests for the retrieval-engine endpoints.

These lock in Hotfix #1: the retrieval router must enforce tenant scope
deny-by-default via ``PolicyService``/``ScopeService`` instead of the legacy
``_ensure_scope`` helper, which silently skipped the organization check when
``current_user.organization_id`` was falsy and the project check when
``current_user.projects`` was empty -- letting a caller claim an arbitrary
``org_id``/``project_id`` in the request body and read another tenant's data.

The tests drive the real endpoint functions through the real ``ScopeService``
scope logic (backed by a fake Mongo-like db) so a regression in scope handling
fails here.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from rbac_backend.agents.models import AgentRequest
from rbac_backend.ingestion.models import IngestionJobCreate
from rbac_backend.retrieval.models import (
    RagResponse,
    SearchBackend,
    SearchFilters,
    SearchRequest,
    SearchResponse,
)
from rbac_backend.routers.retrieval_engine import (
    _authorize_scope,
    _require_scope_values,
    agent,
    create_ingestion_job,
    rag,
    search,
)
from rbac_backend.services.policy_service import PolicyService
from rbac_backend.services.scope_service import ScopeService


# --- Fakes ----------------------------------------------------------------


class _FakeCursor:
    def __init__(self, docs):
        self._docs = list(docs)

    async def to_list(self, length=None):
        return list(self._docs)


class _FakeCollection:
    def __init__(self, docs):
        self._docs = list(docs)

    def find(self, *_args, **_kwargs):
        return _FakeCursor(self._docs)

    async def find_one(self, query):
        def matches(doc):
            for key, expected in (query or {}).items():
                actual = doc.get(key)
                if isinstance(expected, dict) and "$in" in expected:
                    expected_values = {str(item) for item in expected["$in"]}
                    if actual not in expected["$in"] and str(actual) not in expected_values:
                        return False
                elif actual != expected and str(actual) != str(expected):
                    return False
            return True

        for doc in self._docs:
            if matches(doc):
                return dict(doc)
        return None


class _FakeDB:
    """Minimal stand-in for the membership collections ScopeService reads."""

    def __init__(self, memberships=(), project_memberships=(), projects=()):
        self.organization_memberships = _FakeCollection(memberships)
        self.project_memberships = _FakeCollection(project_memberships)
        self.projects = _FakeCollection(
            projects
            or (
                {"_id": "proj-A", "organization_id": "org-A"},
                {"_id": "proj-B", "organization_id": "org-B"},
            )
        )


class _PermissionService:
    async def user_has_permission(self, *_args, **_kwargs) -> bool:
        return True


class _EntitlementService:
    async def check_permission_entitlement(self, **_kwargs):
        return True, "ok"


class _AuditService:
    def __init__(self) -> None:
        self.events = []

    async def emit(self, **kwargs) -> None:
        self.events.append(kwargs)


def _policy(db: _FakeDB | None = None) -> PolicyService:
    """PolicyService wired to the REAL ScopeService so scope logic is exercised.

    Permission and entitlement are stubbed allow=True to isolate the scope
    decision -- the exact surface Hotfix #1 hardens.
    """
    return PolicyService(
        permission_service=_PermissionService(),
        scope_service=ScopeService(db=db or _FakeDB()),
        entitlement_service=_EntitlementService(),
        audit_service=_AuditService(),
    )


def _user(org="org-A", projects=("proj-A",), roles=("orguser",)):
    return SimpleNamespace(
        id="user-1",
        roles=list(roles),
        organization_id=org,
        organizations=[org] if org else [],
        projects=list(projects),
        account_type="client_user",
    )


class _RecordingRetrieval:
    def __init__(self) -> None:
        self.search_called = False
        self.rag_called = False

    async def search(self, request, _current_user) -> SearchResponse:
        self.search_called = True
        return SearchResponse(
            results=[], strategy_used=request.strategy, backend_used=SearchBackend.MONGO
        )

    async def rag(self, request, _current_user) -> RagResponse:
        self.rag_called = True
        return RagResponse(answer="ok", citations=[], strategy_used=request.strategy)


def _search_request(org: str, project: str) -> SearchRequest:
    return SearchRequest(query="What are the delay obligations?", filters=SearchFilters(org_id=org, project_id=project))


# --- Tests ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_search_denies_cross_tenant_request():
    svc = _RecordingRetrieval()
    # User belongs to org-A/proj-A but claims org-B/proj-B in the request body.
    with pytest.raises(HTTPException) as exc:
        await search(
            _search_request("org-B", "proj-B"),
            retrieval_service=svc,
            current_user=_user(),
            policy=_policy(),
        )

    assert exc.value.status_code == 403
    assert svc.search_called is False


@pytest.mark.asyncio
async def test_search_allows_in_scope_request():
    svc = _RecordingRetrieval()
    resp = await search(
        _search_request("org-A", "proj-A"),
        retrieval_service=svc,
        current_user=_user(),
        policy=_policy(),
    )

    assert svc.search_called is True
    assert resp.results == []


@pytest.mark.asyncio
async def test_rag_denies_cross_tenant_request():
    svc = _RecordingRetrieval()
    request = SearchRequest(
        query="summarise the claim", filters=SearchFilters(org_id="org-B", project_id="proj-B")
    )
    # RagRequest is a SearchRequest subclass; the base fields are what matters here.
    with pytest.raises(HTTPException) as exc:
        await rag(request, retrieval_service=svc, current_user=_user(), policy=_policy())

    assert exc.value.status_code == 403
    assert svc.rag_called is False


@pytest.mark.asyncio
async def test_legacy_bug_user_without_org_or_projects_cannot_claim_scope():
    """The exact regression Hotfix #1 closes.

    A non-superadmin with no organization and no project assignments must NOT be
    able to read an arbitrary tenant's data. The legacy ``_ensure_scope`` skipped
    both checks in this state and allowed the request through.
    """
    svc = _RecordingRetrieval()
    orphan = _user(org=None, projects=(), roles=("orguser",))

    with pytest.raises(HTTPException) as exc:
        await search(
            _search_request("org-X", "proj-X"),
            retrieval_service=svc,
            current_user=orphan,
            policy=_policy(),
        )

    assert exc.value.status_code == 403
    assert svc.search_called is False


@pytest.mark.asyncio
async def test_blank_scope_is_rejected_even_for_superadmin():
    # Empty strings satisfy the required-str model but must not pass scope.
    with pytest.raises(HTTPException) as exc:
        await _authorize_scope(_user(roles=("superadmin",)), "", "proj-A", policy=_policy())

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_superadmin_allowed_any_scope():
    svc = _RecordingRetrieval()
    resp = await search(
        _search_request("org-ZZZ", "proj-ZZZ"),
        retrieval_service=svc,
        current_user=_user(org="org-A", roles=("superadmin",)),
        policy=_policy(),
    )

    assert svc.search_called is True
    assert resp.strategy_used is not None


@pytest.mark.asyncio
async def test_agent_denies_cross_tenant_request():
    class _Agent:
        def __init__(self) -> None:
            self.ran = False

        async def run(self, _request, _current_user):
            self.ran = True
            return SimpleNamespace()

    agent_service = _Agent()
    request = AgentRequest(org_id="org-B", project_id="proj-B", user_goal="draft a reply")

    with pytest.raises(HTTPException) as exc:
        await agent(request, agent_service=agent_service, current_user=_user(), policy=_policy())

    assert exc.value.status_code == 403
    assert agent_service.ran is False


@pytest.mark.asyncio
async def test_create_ingestion_job_denies_cross_tenant_request():
    class _Ingestion:
        def __init__(self) -> None:
            self.created = False

        async def create_job(self, payload):
            self.created = True
            return payload

    ingestion = _Ingestion()
    payload = IngestionJobCreate(org_id="org-B", project_id="proj-B", document_id="doc-1")

    with pytest.raises(HTTPException) as exc:
        await create_ingestion_job(
            payload, ingestion_service=ingestion, current_user=_user(), policy=_policy()
        )

    assert exc.value.status_code == 403
    assert ingestion.created is False


def test_require_scope_values_rejects_blanks():
    for org, project in (("", "p"), ("o", ""), (" ", "p"), (None, "p"), ("o", None)):
        with pytest.raises(HTTPException) as exc:
            _require_scope_values(org, project)
        assert exc.value.status_code == 400

    assert _require_scope_values(" org-A ", " proj-A ") == ("org-A", "proj-A")


def test_restrict_to_grounding_drops_non_selected_documents():
    """Grounding guarantee: when a request is pinned to a document set, only
    evidence from those documents survives — so a contract appraisal can never
    draw on other uploads. Empty/None means unrestricted (existing behaviour)."""
    from rbac_backend.retrieval.models import SearchResult
    from rbac_backend.retrieval.service import _restrict_to_grounding

    selected = SearchResult(document_id="d1", chunk_id="c1", score=0.9, snippet="from selected")
    other = SearchResult(document_id="d2", chunk_id="c2", score=0.95, snippet="from another doc")

    kept = _restrict_to_grounding([selected, other], ["d1"])
    assert [r.document_id for r in kept] == ["d1"]  # d2 dropped even though higher score

    assert _restrict_to_grounding([selected, other], None) == [selected, other]
    assert _restrict_to_grounding([selected, other], []) == [selected, other]
