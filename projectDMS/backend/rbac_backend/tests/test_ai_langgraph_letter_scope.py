"""Tenant isolation for the `/api/ai-assistant/langgraph/*` letter routes.

The controller authorised the *requested* organisation/project through
`PolicyService`, but the letter itself was loaded (`LetterService.get_letter`,
inside the graph's `load_state`) and written (`record_langgraph_result`) by bare
`{"_id": letter_id}`. A user authorised for org A who supplied an org-B letter id
read that letter into the drafting graph and overwrote its draft, plan and
strategy fields.

These tests drive the real controller, the real `PolicyService` + `ScopeService`
and the real `AIService` / `LetterService` persistence against an in-memory
letters collection that evaluates the actual Mongo predicates (`$and`, `$in`).
Only the LLM-bearing graph is replaced, by a stub that reads the letter the way
`load_state` does - by bare id - so a missing scope check is observable as a read
of foreign content, not just as a write.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional

import pytest
from bson import ObjectId
from fastapi import HTTPException

from rbac_backend.ai_workflows.langgraph import LetterGraphResult
from rbac_backend.models.ai_models import (
    LangGraphDraftRequest,
    LetterDraftResponse,
    StrategyPlanRequest,
)
from rbac_backend.routers.ai_assistant import AIAssistantController
from rbac_backend.services import ai_service as ai_service_module
from rbac_backend.services.ai_service import AIService
from rbac_backend.services.policy_service import PolicyService
from rbac_backend.services.scope_service import ScopeService
from rbac_backend.utils.error_handler import handle_exceptions


# --- A Mongo-shaped letters collection that evaluates real predicates --------


def _eq(actual: Any, expected: Any) -> bool:
    if isinstance(actual, list):
        return any(_eq(item, expected) for item in actual)
    return actual == expected


def _matches(doc: Dict[str, Any], query: Dict[str, Any]) -> bool:
    for key, expected in (query or {}).items():
        if key == "$and":
            if not all(_matches(doc, clause) for clause in expected):
                return False
            continue
        if key == "$or":
            if not any(_matches(doc, clause) for clause in expected):
                return False
            continue
        if key.startswith("$"):
            raise AssertionError(f"unsupported operator in fake: {key}")
        actual = doc.get(key)
        if isinstance(expected, dict):
            for op, operand in expected.items():
                if op == "$in":
                    if not any(_eq(actual, item) for item in operand):
                        return False
                elif op == "$exists":
                    if (key in doc) != bool(operand):
                        return False
                else:
                    raise AssertionError(f"unsupported operator in fake: {op}")
            continue
        if not _eq(actual, expected):
            return False
    return True


class _Letters:
    def __init__(self, docs: List[Dict[str, Any]]):
        self.docs = [copy.deepcopy(d) for d in docs]
        self.find_one_queries: List[Dict[str, Any]] = []
        self.update_calls: List[Dict[str, Any]] = []

    async def find_one(self, query, projection=None):
        self.find_one_queries.append(query)
        for doc in self.docs:
            if _matches(doc, query):
                return copy.deepcopy(doc)
        return None

    async def update_one(self, query, update, upsert=False):
        self.update_calls.append({"query": query, "update": update})
        for doc in self.docs:
            if _matches(doc, query):
                for field, value in (update.get("$set") or {}).items():
                    doc[field] = value
                for field, value in (update.get("$push") or {}).items():
                    doc.setdefault(field, []).append(value)
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)


class _EmptyCursor:
    async def to_list(self, length=None):
        return []


class _MembershipCollection:
    async def find_one(self, *_args, **_kwargs):
        return None

    def find(self, *_args, **_kwargs):
        return _EmptyCursor()


class _Projects(_MembershipCollection):
    def __init__(self, docs: List[Dict[str, Any]]):
        self.docs = docs

    async def find_one(self, query, *_args, **_kwargs):
        for doc in self.docs:
            if _matches(doc, query):
                return dict(doc)
        return None


class _DB:
    def __init__(self, letters: List[Dict[str, Any]]):
        self.letters = _Letters(letters)
        self.organization_memberships = _MembershipCollection()
        self.project_memberships = _MembershipCollection()
        self.projects = _Projects(
            [
                {"_id": "proj-A", "organization_id": "org-A"},
                {"_id": "proj-A2", "organization_id": "org-A"},
                {"_id": "proj-B", "organization_id": "org-B"},
            ]
        )


# --- Seeds -------------------------------------------------------------------

LETTER_A = ObjectId()
LETTER_A2 = ObjectId()  # org A, project A2
LETTER_B = ObjectId()
MISSING = ObjectId()

_SEED_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _letter(oid: ObjectId, org: str, project: str, tag: str) -> Dict[str, Any]:
    return {
        "_id": oid,
        "organization_id": org,
        "project_id": project,
        "subject": f"SECRET-{tag} subject",
        "title": f"SECRET-{tag} title",
        "recipient": "Engineer",
        "created_by": "seed",
        "assigned_to": "seed",
        "content": f"SECRET-{tag} body",
        "status": "draft",
        "draft_output": f"{tag} original draft",
        "draft_plan": f"{tag} original plan",
        "strategy_plan": f"{tag} original strategy",
        "graph_status": f"{tag}-status",
        "graph_run_id": f"run-{tag}",
        "graph_started_at": _SEED_TIME,
        "graph_completed_at": _SEED_TIME,
        "summary_points": [f"{tag} point"],
        "metadata": {"tag": tag},
        "updated_at": _SEED_TIME,
        "draft_versions": [],
    }


def _seed() -> List[Dict[str, Any]]:
    return [
        _letter(LETTER_A, "org-A", "proj-A", "A"),
        _letter(LETTER_A2, "org-A", "proj-A2", "A2"),
        _letter(LETTER_B, "org-B", "proj-B", "B"),
    ]


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

    async def check_user_limit(self, *_a, **_k):
        return None


def _policy(db: _DB) -> PolicyService:
    allow = _Allow()
    return PolicyService(
        permission_service=allow,
        scope_service=ScopeService(db=db),
        entitlement_service=allow,
        audit_service=allow,
        usage_metering_service=allow,
    )


class _RecordingGraph:
    """Stands in for `LetterDraftGraph`: reads the letter exactly as `load_state`
    does (bare `_id`) and echoes its subject into the draft."""

    runs: List[str] = []

    def __init__(self, _draft_callback):
        pass

    async def run(self, request, current_user):
        db = await ai_service_module.get_database()
        letter = await db.letters.find_one({"_id": ObjectId(request.letter_id)})
        if not letter:
            raise ValueError(f"Letter {request.letter_id} not found")
        type(self).runs.append(request.letter_id)
        now = datetime.now(timezone.utc)
        return LetterGraphResult(
            letter_id=request.letter_id,
            run_id="run-attacker",
            plan="ATTACKER PLAN",
            draft=LetterDraftResponse(
                subject=letter["subject"], body=f"read: {letter['content']}", key_points=[]
            ),
            status="analysis_only" if request.analysis_only else "completed",
            warnings=[],
            trace=[],
            started_at=now,
            completed_at=now,
            tone_approach={"tone": "ATTACKER"},
        )


@pytest.fixture
def env(monkeypatch):
    db = _DB(_seed())

    async def _get_database():
        return db

    monkeypatch.setattr(ai_service_module, "get_database", _get_database)
    monkeypatch.setattr(ai_service_module, "LetterDraftGraph", _RecordingGraph)
    _RecordingGraph.runs = []
    controller = AIAssistantController(
        ai_service=AIService(),
        cache_service=SimpleNamespace(),
        rate_limiter=_Allow(),
        llm_config_service=SimpleNamespace(),
        policy_service=_policy(db),
    )
    return SimpleNamespace(db=db, controller=controller)


def _user(roles=("orguser",), org="org-A", projects=("proj-A",)):
    return SimpleNamespace(
        id="user-A",
        roles=list(roles),
        organization_id=org,
        organizations=[org] if org else [],
        projects=list(projects),
        account_type="client_user",
    )


def _draft(letter_id: Any, **extra) -> LangGraphDraftRequest:
    return LangGraphDraftRequest(
        letter_id=str(letter_id), subject="Re: x", recipient="Engineer", **extra
    )


def _strategy(letter_id: Any, **extra) -> StrategyPlanRequest:
    return StrategyPlanRequest(
        letter_id=str(letter_id), subject="Re: x", recipient="Engineer", role="contractor", **extra
    )


def _snapshot(db: _DB, oid: ObjectId) -> Dict[str, Any]:
    return copy.deepcopy(next(d for d in db.letters.docs if d["_id"] == oid))


async def _as_route(coro_factory):
    """Apply the routes' own `handle_exceptions`, which maps domain errors to
    their HTTP status exactly as production does."""

    @handle_exceptions
    async def route():
        return await coro_factory()

    return await route()


# Every mutating entry point under /api/ai-assistant/langgraph/*.
async def _call_draft(env, user, letter_id, **extra):
    return await _as_route(
        lambda: env.controller.generate_langgraph_draft(_draft(letter_id, **extra), user)
    )


async def _call_background(env, user, letter_id, **extra):
    return await _as_route(
        lambda: env.controller.generate_langgraph_draft(
            _draft(letter_id, analysis_only=True, **extra), user
        )
    )


async def _call_strategy(env, user, letter_id, **extra):
    return await _as_route(
        lambda: env.controller.generate_strategy_plan(_strategy(letter_id, **extra), user)
    )


MUTATING = [
    pytest.param(_call_draft, id="draft"),
    pytest.param(_call_background, id="background"),
    pytest.param(_call_strategy, id="strategy-plan"),
]


# --- Cross-tenant: denied, and nothing about org B moves ---------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("call", MUTATING)
async def test_foreign_org_letter_is_denied_and_byte_for_byte_unchanged(env, call):
    before = _snapshot(env.db, LETTER_B)

    with pytest.raises(HTTPException) as exc:
        await call(env, _user(), LETTER_B, organization_id="org-A", project_id="proj-A")

    assert exc.value.status_code == 404
    assert _snapshot(env.db, LETTER_B) == before
    # Never read into the graph, never written.
    assert _RecordingGraph.runs == []
    assert [c for c in env.db.letters.update_calls] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("call", MUTATING)
async def test_foreign_org_letter_without_explicit_scope_is_denied(env, call):
    before = _snapshot(env.db, LETTER_B)

    with pytest.raises(HTTPException) as exc:
        await call(env, _user(), LETTER_B)

    assert exc.value.status_code == 404
    assert _snapshot(env.db, LETTER_B) == before
    assert _RecordingGraph.runs == []
    assert env.db.letters.update_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("call", MUTATING)
async def test_same_org_foreign_project_is_denied_for_project_tier_user(env, call):
    user = _user(roles=("projectuser",), projects=("proj-A",))
    before = _snapshot(env.db, LETTER_A2)

    with pytest.raises(HTTPException) as exc:
        await call(env, user, LETTER_A2, organization_id="org-A", project_id="proj-A")

    assert exc.value.status_code == 404
    assert _snapshot(env.db, LETTER_A2) == before
    assert _RecordingGraph.runs == []
    assert env.db.letters.update_calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize("call", MUTATING)
async def test_explicit_project_narrows_even_inside_entitlement(env, call):
    user = _user(roles=("projectuser",), projects=("proj-A", "proj-A2"))
    before = _snapshot(env.db, LETTER_A2)

    with pytest.raises(HTTPException) as exc:
        await call(env, user, LETTER_A2, organization_id="org-A", project_id="proj-A")

    assert exc.value.status_code == 404
    assert _snapshot(env.db, LETTER_A2) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("call", MUTATING)
@pytest.mark.parametrize("letter_id", [MISSING, "not-an-object-id"], ids=["missing", "malformed"])
async def test_unknown_and_malformed_ids_answer_like_a_foreign_one(env, call, letter_id):
    with pytest.raises(HTTPException) as exc:
        await call(env, _user(), letter_id)

    # Same status as a foreign letter: the route must not distinguish the two.
    assert exc.value.status_code == 404
    assert env.db.letters.update_calls == []


# --- Still works -------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("call", MUTATING)
async def test_same_org_same_project_still_reads_and_writes(env, call):
    result = await call(env, _user(), LETTER_A, organization_id="org-A", project_id="proj-A")

    assert result.letter_id == str(LETTER_A)
    assert _RecordingGraph.runs == [str(LETTER_A)]
    assert len(env.db.letters.update_calls) == 1
    after = _snapshot(env.db, LETTER_A)
    assert after["updated_at"] != _SEED_TIME


@pytest.mark.asyncio
async def test_multi_project_user_reaches_letter_in_second_project_without_explicit_scope(env):
    # The controller defaults the policy project to the user's FIRST project; that
    # default must not narrow which of the user's own letters are reachable.
    user = _user(roles=("projectuser",), projects=("proj-A", "proj-A2"))
    result = await _call_draft(env, user, LETTER_A2)

    assert result.letter_id == str(LETTER_A2)
    assert _snapshot(env.db, LETTER_A2)["draft_output"] == "read: SECRET-A2 body"


@pytest.mark.asyncio
async def test_superadmin_reaches_the_selected_org(env):
    # Super Admin's organization_id is the navbar selection (X-Org-Id); metered
    # routes need one. Selecting org B reaches org B's letter.
    user = _user(roles=("superadmin",), org="org-B", projects=())
    result = await _call_draft(env, user, LETTER_B)

    assert result.letter_id == str(LETTER_B)


@pytest.mark.asyncio
async def test_superadmin_selection_narrows(env):
    user = _user(roles=("superadmin",), org="org-A", projects=())
    before = _snapshot(env.db, LETTER_B)

    with pytest.raises(HTTPException) as exc:
        await _call_draft(env, user, LETTER_B)

    assert exc.value.status_code == 404
    assert _snapshot(env.db, LETTER_B) == before


# --- The write repeats the scope ---------------------------------------------


@pytest.mark.asyncio
async def test_write_predicate_repeats_the_scope(env):
    await _call_draft(env, _user(), LETTER_A, organization_id="org-A", project_id="proj-A")

    (call,) = env.db.letters.update_calls
    query = call["query"]
    assert query != {"_id": LETTER_A}
    # The predicate itself must refuse the foreign row.
    assert _matches(_snapshot(env.db, LETTER_A), query)
    foreign = dict(_snapshot(env.db, LETTER_B), _id=LETTER_A)
    assert not _matches(foreign, query)


@pytest.mark.asyncio
async def test_record_langgraph_result_refuses_a_row_outside_scope(env):
    """Defence in depth: even called directly, the persistence helper cannot
    write through a scope that does not contain the letter."""
    from rbac_backend.services.letter_service import LetterNotFoundError, LetterService
    from rbac_backend.core.security import build_scope_query

    before = _snapshot(env.db, LETTER_B)
    now = datetime.now(timezone.utc)
    result = LetterGraphResult(
        letter_id=str(LETTER_B), run_id="r", plan="p",
        draft=LetterDraftResponse(subject="s", body="b", key_points=[]),
        status="completed", warnings=[], trace=[], started_at=now, completed_at=now,
    )
    with pytest.raises(LetterNotFoundError):
        await LetterService(env.db).record_langgraph_result(
            str(LETTER_B), result, scope=build_scope_query(_user())
        )
    assert _snapshot(env.db, LETTER_B) == before


# --- Read route --------------------------------------------------------------


# DRAFTING_REQUEST_VIEW is gated on expert allocation for client roles, so the
# read route is exercised with Super Admin, whose selection is the only bound.


@pytest.mark.asyncio
async def test_run_snapshot_outside_selection_is_404(env):
    user = _user(roles=("superadmin",), org="org-A", projects=())
    with pytest.raises(HTTPException) as exc:
        await _as_route(lambda: env.controller.get_langgraph_run(str(LETTER_B), user))
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_run_snapshot_inside_selection_is_served(env):
    user = _user(roles=("superadmin",), org="org-B", projects=())
    result = await env.controller.get_langgraph_run(str(LETTER_B), user)
    assert result.letter_id == str(LETTER_B)


@pytest.mark.asyncio
async def test_denial_detail_does_not_distinguish_foreign_from_missing(env):
    details = []
    for letter_id in (LETTER_B, MISSING, "not-an-object-id"):
        with pytest.raises(HTTPException) as exc:
            await _call_draft(env, _user(), letter_id)
        details.append((exc.value.status_code, exc.value.detail))
    assert details[0] == details[1] == details[2]
