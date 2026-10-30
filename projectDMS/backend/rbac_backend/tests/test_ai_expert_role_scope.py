"""Expert roles on the surfaces the B1/B2 fix touched.

`build_scope_query` bounds the ContraClaim expert roles by `current_user.projects`
and deliberately ignores the organisation (`authorize_scope`: "experts may work
cross-org"); the policy gate answers them through client membership or
`expert_allocations`. These tests drive the real controller / route functions,
the real `PolicyService` + `ScopeService`, and a permission service that grants
exactly what the SEEDED role documents (`initial_data/default_roles.py`) grant -
no allow-all stub - so each result is the one production would give.
"""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any, Dict

import pytest
from bson import ObjectId
from fastapi import HTTPException

from rbac_backend.agents.models import AgentRequest
from rbac_backend.initial_data.default_roles import DEFAULT_ROLES
from rbac_backend.models.ai_models import LangGraphDraftRequest
from rbac_backend.models.rbac_monetization import AccountType
from rbac_backend.retrieval.models import SearchBackend, SearchFilters, SearchRequest
from rbac_backend.routers import retrieval_engine
from rbac_backend.routers.ai_assistant import AIAssistantController
from rbac_backend.services import ai_service as ai_service_module
from rbac_backend.services.ai_service import AIService
from rbac_backend.services.permission_service import equivalent_permissions
from rbac_backend.services.policy_service import PolicyService
from rbac_backend.services.scope_service import ScopeService
from rbac_backend.utils.error_handler import handle_exceptions

import test_ai_langgraph_letter_scope as lg
import test_retrieval_authority_filter as rt

EXPERT_ROLES = [
    "contraclaim_expert_drafter",
    "contraclaim_expert_reviewer",
    "contraclaim_drafting_manager",
    "contract_expert",  # named in core/security.py, no role document exists
]
_ASSIGNMENT_ROLE = {
    "contraclaim_expert_drafter": "drafter",
    "contraclaim_expert_reviewer": "reviewer",
    "contraclaim_drafting_manager": "drafting_manager",
    "contract_expert": "drafter",
}

LETTER_C = ObjectId()  # org C / proj-C: another tenant the expert once worked on


# --- A Mongo matcher wide enough for the allocation query --------------------


def _match(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for key, expected in (query or {}).items():
        if key == "$and":
            if not all(_match(doc, c) for c in expected):
                return False
            continue
        if key == "$or":
            if not any(_match(doc, c) for c in expected):
                return False
            continue
        present = key in doc
        actual = doc.get(key)
        if isinstance(expected, dict) and expected and all(k.startswith("$") for k in expected):
            for op, operand in expected.items():
                if op == "$in":
                    if not any(actual == o for o in operand):
                        return False
                elif op == "$exists":
                    if present != bool(operand):
                        return False
                elif op == "$lte":
                    if actual is None or actual > operand:
                        return False
                elif op == "$gte":
                    if actual is None or actual < operand:
                        return False
                else:
                    raise AssertionError(f"unsupported operator {op}")
            continue
        if actual != expected:
            return False
    return True


class _Coll(lg._Letters):
    def find(self, query=None, *_a, **_k):
        rows = [copy.deepcopy(d) for d in self.docs if _match(d, query or {})]
        return rt._Cursor(rows)

    async def find_one(self, query=None, projection=None, *_a, **_k):
        self.find_one_queries.append(query)
        for doc in self.docs:
            if _match(doc, query or {}):
                return copy.deepcopy(doc)
        return None


_ORG_OF = {"proj-A": "org-A", "proj-A2": "org-A", "proj-B": "org-B", "proj-C": "org-C"}


class _DB:
    def __init__(self, role: str, projects_with_allocation=("proj-A",)):
        letters = lg._seed() + [lg._letter(LETTER_C, "org-C", "proj-C", "C")]
        self.letters = lg._Letters(letters)
        self.organization_memberships = _Coll([])
        self.project_memberships = _Coll([])
        self.projects = _Coll(
            [
                {"_id": "proj-A", "organization_id": "org-A"},
                {"_id": "proj-A2", "organization_id": "org-A"},
                {"_id": "proj-B", "organization_id": "org-B"},
                {"_id": "proj-C", "organization_id": "org-C"},
            ]
        )
        self.expert_allocations = _Coll(
            [
                {
                    "expert_user_id": "expert-A",
                    "organization_id": _ORG_OF[project],
                    "project_id": project,
                    "status": "active",
                    "assignment_role": _ASSIGNMENT_ROLE[role],
                    # The allocation grants the role's seeded permissions; a bare
                    # drafter/reviewer allocation grants only drafting.draft.* /
                    # drafting.review.*, which would not reach the runs route.
                    "permissions_granted": sorted(_SeededPermissions({role}).held),
                }
                for project in projects_with_allocation
            ]
        )


class _SeededPermissions:
    """Grants exactly the seeded role document's permissions (one-hop
    equivalents, as `PermissionService.user_has_permission` resolves them)."""

    def __init__(self, roles):
        held = set()
        for role in DEFAULT_ROLES:
            if role["_id"] in roles:
                held.update(role.get("permissions") or [])
        self.held = held

    async def user_has_permission(self, _user_id, permission, **_k):
        keys = equivalent_permissions(permission) or {permission}
        return bool(keys & self.held) or "*" in self.held


class _Allow(lg._Allow):
    pass


def _policy(db, roles) -> PolicyService:
    allow = _Allow()
    return PolicyService(
        permission_service=_SeededPermissions(roles),
        scope_service=ScopeService(db=db),
        entitlement_service=allow,
        audit_service=allow,
        usage_metering_service=allow,
    )


def _expert(role, projects=("proj-A",), org="org-A"):
    return SimpleNamespace(
        id="expert-A",
        roles=[role],
        organization_id=org,
        organizations=[org] if org else [],
        projects=list(projects),
        account_type=AccountType.CONTRACLAIM_STAFF.value,
    )


@pytest.fixture
def make_env(monkeypatch):
    def _make(role, **db_kwargs):
        db = _DB(role, **db_kwargs)

        async def _get_database():
            return db

        monkeypatch.setattr(ai_service_module, "get_database", _get_database)
        monkeypatch.setattr(ai_service_module, "LetterDraftGraph", lg._RecordingGraph)
        lg._RecordingGraph.runs = []
        controller = AIAssistantController(
            ai_service=AIService(),
            cache_service=SimpleNamespace(),
            rate_limiter=_Allow(),
            llm_config_service=SimpleNamespace(),
            policy_service=_policy(db, {role}),
        )
        return SimpleNamespace(db=db, controller=controller, role=role)

    return _make


async def _status(coro_factory) -> int:
    @handle_exceptions
    async def route():
        return await coro_factory()

    try:
        await route()
    except HTTPException as exc:
        return exc.status_code
    return 200


def _draft(env, user, letter_id, **extra):
    return lambda: env.controller.generate_langgraph_draft(
        LangGraphDraftRequest(letter_id=str(letter_id), subject="s", recipient="r", **extra), user
    )


def _runs(env, user, letter_id):
    return lambda: env.controller.get_langgraph_run(str(letter_id), user)


def _unchanged(env, before):
    return all(lg._snapshot(env.db, oid) == snap for oid, snap in before.items())


def _snapshots(env):
    return {oid: lg._snapshot(env.db, oid) for oid in (lg.LETTER_B, lg.LETTER_A2, LETTER_C)}


# --- A. /api/ai-assistant/langgraph/* ----------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("role", EXPERT_ROLES)
@pytest.mark.parametrize(
    "letter_id,extra",
    [
        pytest.param(lg.LETTER_B, {}, id="foreign-org-letter"),
        pytest.param(lg.LETTER_B, {"organization_id": "org-B", "project_id": "proj-B"}, id="foreign-org-and-project-claimed"),
        pytest.param(lg.LETTER_B, {"project_id": "proj-B"}, id="foreign-project-claimed"),
        pytest.param(lg.LETTER_A2, {}, id="same-org-sibling-project"),
    ],
)
async def test_expert_cannot_reach_foreign_or_sibling_letters(make_env, role, letter_id, extra):
    env = make_env(role)
    before = _snapshots(env)
    user = _expert(role)

    draft = await _status(_draft(env, user, letter_id, **extra))
    runs = await _status(_runs(env, user, letter_id))

    assert draft in (403, 404), draft
    assert runs in (403, 404), runs
    assert lg._RecordingGraph.runs == []
    assert env.db.letters.update_calls == []
    assert _unchanged(env, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", EXPERT_ROLES)
async def test_expert_cross_org_project_does_not_ride_the_selected_orgs_gate(make_env, role):
    """The expert's `projects` still lists org C's project (e.g. an allocation
    that ended), but the gate is answered for org A. The letter must belong to
    the organisation the gate authorised, or the org-C subscription, entitlement,
    allocation and metering checks never ran for a write to an org-C letter."""
    env = make_env(role)
    before = _snapshots(env)
    user = _expert(role, projects=("proj-A", "proj-C"))

    draft = await _status(_draft(env, user, LETTER_C))
    runs = await _status(_runs(env, user, LETTER_C))

    assert draft in (403, 404), draft
    assert runs in (403, 404), runs
    assert env.db.letters.update_calls == []
    assert _unchanged(env, before)


@pytest.mark.asyncio
async def test_drafting_manager_still_drafts_its_own_letter(make_env):
    env = make_env("contraclaim_drafting_manager")
    assert await _status(_draft(env, _expert(env.role), lg.LETTER_A)) == 200
    assert lg._RecordingGraph.runs == [str(lg.LETTER_A)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role", ["contraclaim_expert_drafter", "contraclaim_expert_reviewer", "contraclaim_drafting_manager"]
)
async def test_allocated_expert_still_reads_its_own_run(make_env, role):
    env = make_env(role)
    assert await _status(_runs(env, _expert(role), lg.LETTER_A)) == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["contraclaim_expert_drafter", "contraclaim_expert_reviewer", "contract_expert"])
async def test_expert_without_create_permission_cannot_draft_at_all(make_env, role):
    env = make_env(role)
    assert await _status(_draft(env, _expert(role), lg.LETTER_A)) == 403


# --- B/C. /api/v1/retrieval/search and /api/v1/retrieval/agent ----------------


@pytest.mark.asyncio
@pytest.mark.parametrize("role", EXPERT_ROLES)
@pytest.mark.parametrize(
    "org,project,metadata",
    [
        pytest.param("org-A", "proj-A", {"org_id": "org-B", "project_id": "proj-B"}, id="foreign-metadata"),
        pytest.param("org-B", "proj-B", {}, id="foreign-org-project"),
        pytest.param("org-A", "proj-A2", {}, id="same-org-sibling-project"),
        pytest.param("org-A", "proj-A", {}, id="own-scope"),
    ],
)
async def test_expert_retrieval_search(role, org, project, metadata):
    db = rt._DB()
    request = SearchRequest(
        query="alpha",
        limit=20,
        backend=SearchBackend.QDRANT,
        filters=SearchFilters(org_id=org, project_id=project, metadata=metadata),
    )
    try:
        resp = await retrieval_engine.search(
            request,
            retrieval_service=rt._service(db, rt._memory_vector_client()),
            current_user=_expert(role),
            policy=_policy(_DB(role), {role}),
        )
        got = rt._chunks(resp)
    except HTTPException as exc:
        assert exc.status_code == 403
        got = set()
    assert got <= rt.AUTHORISED, got


@pytest.mark.asyncio
@pytest.mark.parametrize("role", EXPERT_ROLES)
@pytest.mark.parametrize(
    "org,project,filters",
    [
        pytest.param("org-A", "proj-A", SearchFilters(org_id="org-B", project_id="proj-B"), id="foreign-filters"),
        pytest.param("org-B", "proj-B", None, id="foreign-scope"),
        pytest.param("org-A", "proj-A2", None, id="same-org-sibling-project"),
    ],
)
async def test_expert_retrieval_agent(role, org, project, filters):
    retrieval = rt._RecordingRetrieval()
    agent_service = rt._agent(rt._DB(), retrieval)
    request = AgentRequest(org_id=org, project_id=project, incoming_text="x", filters=filters)
    try:
        await retrieval_engine.agent(
            request,
            agent_service=agent_service,
            current_user=_expert(role),
            policy=_policy(_DB(role), {role}),
        )
    except HTTPException as exc:
        assert exc.status_code == 403
    for sent in retrieval.requests:
        assert (sent.filters.org_id, sent.filters.project_id) == ("org-A", "proj-A")


# --- Org-less expert: `organization_id` None, stale foreign project ----------
#
# Staff experts need not belong to any client organisation. With no org, the
# runs route used to ask the policy about `current_user.organization_id` (None):
# the allocation query then had no organisation term, any allocation passed, and
# the letter was bounded by `projects` alone - a stale foreign project was enough.

ALLOCATED_ROLES = ["contraclaim_expert_drafter", "contraclaim_expert_reviewer", "contraclaim_drafting_manager"]


async def _run_body(coro_factory):
    @handle_exceptions
    async def route():
        return await coro_factory()

    try:
        return 200, await route()
    except HTTPException as exc:
        return exc.status_code, exc.detail


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ALLOCATED_ROLES)
@pytest.mark.parametrize(
    "letter_id,stale", [(lg.LETTER_B, "proj-B"), (LETTER_C, "proj-C")], ids=["org-B", "org-C"]
)
async def test_orgless_expert_cannot_read_a_run_through_a_stale_foreign_project(
    make_env, role, letter_id, stale
):
    env = make_env(role)  # allocation: org A / proj-A only
    before = _snapshots(env)
    user = _expert(role, projects=("proj-A", stale), org=None)

    status, body = await _run_body(_runs(env, user, letter_id))

    assert status in (403, 404), status
    assert "SECRET" not in str(body) and "run-" not in str(body)
    assert env.db.letters.update_calls == []
    assert _unchanged(env, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ALLOCATED_ROLES)
async def test_orgless_expert_cannot_draft_through_a_stale_foreign_project(make_env, role):
    env = make_env(role)
    before = _snapshots(env)
    user = _expert(role, projects=("proj-A", "proj-C"), org=None)

    status = await _status(_draft(env, user, LETTER_C))

    assert status in (403, 404), status
    assert env.db.letters.update_calls == []
    assert _unchanged(env, before)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ALLOCATED_ROLES)
async def test_orgless_expert_unlisted_foreign_letter_is_plain_not_found(make_env, role):
    env = make_env(role)
    user = _expert(role, projects=("proj-A",), org=None)

    foreign = await _run_body(_runs(env, user, lg.LETTER_B))
    missing = await _run_body(_runs(env, user, lg.MISSING))

    assert foreign == missing
    assert foreign[0] == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ALLOCATED_ROLES)
async def test_orgless_expert_reads_its_allocated_letter(make_env, role):
    env = make_env(role)
    user = _expert(role, projects=("proj-A",), org=None)

    status, body = await _run_body(_runs(env, user, lg.LETTER_A))

    assert status == 200
    assert body.letter_id == str(lg.LETTER_A)


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ALLOCATED_ROLES)
async def test_cross_org_expert_work_is_allowed_when_the_target_is_allocated(make_env, role):
    """Experts work across organisations by design - when the TARGET's own
    organisation/project allocation says so."""
    env = make_env(role, projects_with_allocation=("proj-A", "proj-C"))
    user = _expert(role, projects=("proj-A", "proj-C"), org=None)

    status, body = await _run_body(_runs(env, user, LETTER_C))

    assert status == 200
    assert body.letter_id == str(LETTER_C)
