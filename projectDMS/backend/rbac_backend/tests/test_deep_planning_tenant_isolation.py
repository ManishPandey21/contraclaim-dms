"""Tenant isolation of the deep-planning similar-letter context.

`POST /api/deep-planning/generate-draft` looks up "similar letters" and feeds them
to two places: the LLM drafting prompt (subject / recipient / date) and the
response body (`similar_letters`, including each letter's full `content`). The
draft record persisted to `ai_generated_drafts` keeps their ids.

The invariant pinned here: similar-letter retrieval is the intersection of the
similarity criteria and the caller's authorised tenant scope, decided before any
record reaches the prompt, the response, the persisted draft, or the embedding
write-back. An empty scope means no letters, never "no filter".

The fake database evaluates the Mongo filter the router actually sends, so these
tests fail if any layer drops or rebuilds the scope predicate - asserting on a
query builder alone would not catch that.

Entry authorization (`PolicyService.authorize`) is stubbed to allow: it needs
subscription, permission and expert-allocation fixtures and is not what is under
test. `authorize_scope` runs for real.
"""

import json
from typing import Any, Dict, List, Optional

import pytest
from bson import ObjectId
from fastapi.testclient import TestClient

import rbac_backend.routers.deep_planning as dp
from rbac_backend.core.security import CurrentUser
from rbac_backend.main import app

ORG_A = "org-a"
ORG_B = "org-b"
PROJ_A1 = "proj-a1"
PROJ_A2 = "proj-a2"
PROJ_B1 = "proj-b1"

MATCH = [1.0, 0.0, 0.0]
UNRELATED = [0.0, 0.0, 1.0]


# --------------------------------------------------------------------------
# A fake Mongo that evaluates the filters the router sends
# --------------------------------------------------------------------------
def _same(stored: Any, wanted: Any) -> bool:
    if isinstance(stored, ObjectId) or isinstance(wanted, ObjectId):
        return str(stored) == str(wanted)
    return stored == wanted


def _field_matches(row: Dict[str, Any], field: str, cond: Any) -> bool:
    value = row.get(field)
    if isinstance(cond, dict) and any(str(k).startswith("$") for k in cond):
        for op, arg in cond.items():
            if op == "$in":
                if not any(_same(value, item) for item in arg):
                    return False
            elif op == "$nin":
                if any(_same(value, item) for item in arg):
                    return False
            elif op == "$ne":
                if _same(value, arg):
                    return False
            elif op == "$exists":
                if (field in row) != bool(arg):
                    return False
            else:
                raise AssertionError(f"fake Mongo does not support {op}")
        return True
    return _same(value, cond)


def _matches(row: Dict[str, Any], filt: Dict[str, Any]) -> bool:
    for key, cond in (filt or {}).items():
        if key == "$and":
            if not all(_matches(row, sub) for sub in cond):
                return False
        elif key == "$or":
            if not any(_matches(row, sub) for sub in cond):
                return False
        elif not _field_matches(row, key, cond):
            return False
    return True


class _Cursor:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows

    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, n: int):
        self._rows = self._rows[:n]
        return self

    async def to_list(self, length: Optional[int] = None):
        return list(self._rows[: length if length else len(self._rows)])


class _Collection:
    def __init__(self, rows: Optional[List[Dict[str, Any]]] = None):
        self.rows = list(rows or [])
        self.find_filters: List[Dict[str, Any]] = []
        self.updated_ids: List[Any] = []
        self.inserted: List[Dict[str, Any]] = []

    def find(self, filt: Optional[Dict[str, Any]] = None, *_args, **_kwargs):
        self.find_filters.append(filt or {})
        return _Cursor([dict(r) for r in self.rows if _matches(r, filt or {})])

    async def find_one(self, filt: Optional[Dict[str, Any]] = None, *_args, **_kwargs):
        for row in self.rows:
            if _matches(row, filt or {}):
                return dict(row)
        return None

    async def update_one(self, filt: Dict[str, Any], _update: Dict[str, Any], *_args, **_kwargs):
        self.updated_ids.append(filt.get("_id"))
        return None

    async def insert_one(self, doc: Dict[str, Any]):
        self.inserted.append(doc)
        return None


class _DB:
    def __init__(self, letters: List[Dict[str, Any]]):
        self.letters = _Collection(letters)
        self.documents = _Collection()
        self.ai_generated_drafts = _Collection()


def _letter(marker: str, org: Optional[str], project: Optional[str], *, embedding=MATCH) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "_id": ObjectId(),
        "title": f"title {marker}",
        "subject": f"subject {marker}",
        "content": f"content body {marker}",
        "recipient": f"recipient {marker}",
        "organization_id": org,
        "project_id": project,
    }
    if embedding is not None:
        row["embedding"] = embedding
    return row


def _tenant_letters() -> List[Dict[str, Any]]:
    return [
        _letter("MARK_A1", ORG_A, PROJ_A1),
        _letter("MARK_A2", ORG_A, PROJ_A2),
        _letter("MARK_B1", ORG_B, PROJ_B1),
        # A foreign letter with no stored embedding: the router would compute and
        # write one back. That write must never reach another tenant's record.
        _letter("MARK_B_NOEMB", ORG_B, PROJ_B1, embedding=None),
    ]


ALL_MARKERS = {"MARK_A1", "MARK_A2", "MARK_B1", "MARK_B_NOEMB"}


def _user(roles: List[str], *, org: Optional[str] = None, orgs: Optional[List[str]] = None,
          projects: Optional[List[str]] = None, account_type: str = "client_user") -> CurrentUser:
    return CurrentUser(
        id="u-1",
        username="tester",
        email="tester@example.com",
        roles=roles,
        organization_id=org,
        organizations=orgs or [],
        projects=projects or [],
        account_type=account_type,
    )


class _Run:
    def __init__(self, status: int, body: Any, prompts: List[str], db: _DB):
        self.status = status
        self.body = body
        self.prompt_text = "\n".join(prompts)
        self.db = db

    @property
    def returned_markers(self) -> set:
        text = json.dumps(self.body, default=str)
        return {m for m in ALL_MARKERS if m in text}

    @property
    def prompt_markers(self) -> set:
        return {m for m in ALL_MARKERS if m in self.prompt_text}


@pytest.fixture
def run_draft(monkeypatch):
    """Drive the real endpoint for a principal and capture every sink."""

    def _embedding(model: str, input: str):  # noqa: A002 - OpenAI signature
        vec = UNRELATED if "UNRELATED" in input else MATCH

        class _D:
            embedding = vec

        class _R:
            data = [_D()]

        return _R()

    def _go(user: CurrentUser, letters: List[Dict[str, Any]], **payload_overrides) -> _Run:
        db = _DB(letters)
        prompts: List[str] = []

        def _chat(model: str, messages: List[Dict[str, str]], **_kwargs):
            prompts.extend(str(m.get("content", "")) for m in messages)

            class _M:
                content = "Drafted letter body."

            class _C:
                message = _M()

            class _R:
                choices = [_C()]

            return _R()

        async def _allow(self, *_args, **_kwargs):
            return None

        async def _db():
            yield db

        monkeypatch.setattr(dp, "rate_limited_openai_call", lambda: None)
        monkeypatch.setattr(dp.client.embeddings, "create", _embedding)
        monkeypatch.setattr(dp.client.chat.completions, "create", _chat)
        monkeypatch.setattr(dp.PolicyService, "authorize", _allow)
        app.dependency_overrides[dp.get_db] = _db
        app.dependency_overrides[dp.get_current_user] = lambda: user
        try:
            payload = {
                "document_ids": [],
                "subject": "Delay notice",
                "recipient": "Engineer",
                "user_id": user.id,
                "organization_id": None,
                "project_id": None,
            }
            payload.update(payload_overrides)
            resp = TestClient(app).post("/api/deep-planning/generate-draft", json=payload)
        finally:
            app.dependency_overrides.pop(dp.get_db, None)
            app.dependency_overrides.pop(dp.get_current_user, None)
        try:
            body = resp.json()
        except ValueError:
            body = resp.text
        return _Run(resp.status_code, body, prompts, db)

    return _go


def _assert_only(run: _Run, expected: set) -> None:
    assert run.status == 200, run.body
    assert run.returned_markers == expected, run.body
    assert run.prompt_markers == expected, run.prompt_text
    persisted = run.db.ai_generated_drafts.inserted
    assert len(persisted) == 1
    allowed_ids = {
        str(r["_id"]) for r in run.db.letters.rows if any(m in r["subject"] for m in expected)
    }
    assert set(persisted[0]["similar_letters_used"]) <= allowed_ids
    for updated in run.db.letters.updated_ids:
        assert str(updated) in allowed_ids, "embedding written back to an out-of-scope letter"


# --------------------------------------------------------------------------
# Ordinary organisation scope
# --------------------------------------------------------------------------
def test_org_admin_context_holds_only_own_organisation(run_draft):
    run = run_draft(_user(["orgadmin"], org=ORG_A), _tenant_letters())
    _assert_only(run, {"MARK_A1", "MARK_A2"})


def test_org_user_with_project_assignments_is_bounded_to_them(run_draft):
    # build_scope_query: an org-tier user with explicit project assignments is
    # restricted to them, and authorize_scope enforces the same.
    run = run_draft(_user(["orguser"], org=ORG_A, projects=[PROJ_A1]), _tenant_letters())
    _assert_only(run, {"MARK_A1"})


# --------------------------------------------------------------------------
# Project scope
# --------------------------------------------------------------------------
def test_project_user_context_holds_only_assigned_project(run_draft):
    run = run_draft(_user(["projectuser"], org=ORG_A, projects=[PROJ_A1]), _tenant_letters())
    _assert_only(run, {"MARK_A1"})


def test_project_admin_selected_project_excludes_sibling_project(run_draft):
    user = _user(["projectadmin"], org=ORG_A, projects=[PROJ_A1, PROJ_A2])
    run = run_draft(user, _tenant_letters(), project_id=PROJ_A2)
    _assert_only(run, {"MARK_A2"})


def test_project_user_cannot_select_a_foreign_project(run_draft):
    run = run_draft(
        _user(["projectuser"], org=ORG_A, projects=[PROJ_A1]), _tenant_letters(), project_id=PROJ_B1
    )
    assert run.status == 403
    assert run.db.letters.find_filters == []


# --------------------------------------------------------------------------
# Roles outside the organisation / project tiers
# --------------------------------------------------------------------------
def test_expert_drafter_context_is_bounded_to_assigned_projects(run_draft):
    # The gate authorises the expert for projects[0] when no project is sent;
    # retrieval previously applied no tenant predicate at all for this role.
    user = _user(["contraclaim_expert_drafter"], projects=[PROJ_A1], account_type="contraclaim_staff")
    run = run_draft(user, _tenant_letters())
    _assert_only(run, {"MARK_A1"})


def test_expert_drafter_with_explicit_project_is_bounded_to_it(run_draft):
    user = _user(["contraclaim_expert_drafter"], projects=[PROJ_A1, PROJ_B1], account_type="contraclaim_staff")
    run = run_draft(user, _tenant_letters(), project_id=PROJ_A1)
    _assert_only(run, {"MARK_A1"})


def test_contract_manager_org_alone_is_refused_before_retrieval(run_draft):
    # contractmgr_org is organisation-scoped by its role document but is not one
    # of the name-based tiers authorize_scope recognises, so the gate default-
    # denies it. Pinned so a gate change cannot silently open an unfiltered path.
    run = run_draft(_user(["contractmgr_org"], org=ORG_A), _tenant_letters())
    assert run.status == 403
    assert run.db.letters.find_filters == []
    assert run.prompt_text == ""


def test_contract_manager_org_with_org_tier_role_holds_only_own_organisation(run_draft):
    run = run_draft(_user(["contractmgr_org", "orguser"], org=ORG_A), _tenant_letters())
    _assert_only(run, {"MARK_A1", "MARK_A2"})


# --------------------------------------------------------------------------
# Elevated roles
# --------------------------------------------------------------------------
def test_superadmin_without_selection_is_global(run_draft):
    run = run_draft(_user(["superadmin"]), _tenant_letters())
    assert run.status == 200
    assert run.returned_markers == ALL_MARKERS


def test_superadmin_with_navbar_selection_is_bounded_to_it(run_draft):
    # EffectiveScope = Entitlement ∩ Navbar Selection: for Super Admin,
    # organization_id is the validated active selection.
    run = run_draft(_user(["superadmin"], org=ORG_A), _tenant_letters())
    _assert_only(run, {"MARK_A1", "MARK_A2"})


def test_superadmin_explicit_request_scope_is_honoured(run_draft):
    run = run_draft(_user(["superadmin"]), _tenant_letters(), organization_id=ORG_B)
    _assert_only(run, {"MARK_B1", "MARK_B_NOEMB"})


def test_dormant_super_user_does_not_gain_super_admin_reach(run_draft):
    run = run_draft(_user(["superuser"], orgs=[ORG_A]), _tenant_letters())
    _assert_only(run, {"MARK_A1", "MARK_A2"})


# --------------------------------------------------------------------------
# Empty scope never means "no filter"
# --------------------------------------------------------------------------
async def _direct(user: CurrentUser, letters, monkeypatch, **kwargs) -> List[dict]:
    db = _DB(letters)

    def _embedding(model: str, input: str):  # noqa: A002
        class _D:
            embedding = MATCH

        class _R:
            data = [_D()]

        return _R()

    monkeypatch.setattr(dp, "rate_limited_openai_call", lambda: None)
    monkeypatch.setattr(dp.client.embeddings, "create", _embedding)
    return await dp.find_similar_letters(db, "Delay notice", current_user=user, **kwargs)


@pytest.mark.parametrize(
    "user",
    [
        _user(["contraclaim_expert_drafter"], account_type="contraclaim_staff"),
        _user(["projectuser"], org=ORG_A),
        _user(["orgadmin"]),
        _user(["superuser"]),
        _user(["contractmgr_org"], org=ORG_A),
        _user([]),
    ],
    ids=["expert-no-projects", "project-user-no-projects", "org-admin-no-org", "superuser-no-orgs",
         "contractmgr-org-only", "no-roles"],
)
def test_empty_scope_returns_no_similar_letters(user, monkeypatch):
    import asyncio

    result = asyncio.run(_direct(user, _tenant_letters(), monkeypatch))
    assert result == []


def test_missing_principal_returns_no_similar_letters(monkeypatch):
    import asyncio

    result = asyncio.run(_direct(None, _tenant_letters(), monkeypatch))
    assert result == []


# --------------------------------------------------------------------------
# Functional guard rails
# --------------------------------------------------------------------------
def test_threshold_ranking_and_limit_survive_within_scope(run_draft):
    in_scope = [_letter(f"MARK_A1_{i}", ORG_A, PROJ_A1, embedding=[1.0, float(i) / 10, 0.0]) for i in range(7)]
    weak = _letter("MARK_A1_WEAK", ORG_A, PROJ_A1, embedding=UNRELATED)
    foreign = _letter("MARK_B1", ORG_B, PROJ_B1)
    run = run_draft(_user(["projectuser"], org=ORG_A, projects=[PROJ_A1]), in_scope + [weak, foreign])

    assert run.status == 200, run.body
    returned = run.body["similar_letters"]
    assert len(returned) == 5
    scores = [item["similarity_score"] for item in returned]
    assert scores == sorted(scores, reverse=True)
    # The closest five are i = 0..4 (smaller off-axis component).
    assert [item["subject"] for item in returned] == [f"subject MARK_A1_{i}" for i in range(5)]
    text = json.dumps(run.body)
    assert "MARK_A1_WEAK" not in text
    assert "MARK_B1" not in text and "MARK_B1" not in run.prompt_text
    # Legitimate same-tenant context still reaches the drafting prompt.
    assert "subject MARK_A1_0" in run.prompt_text


def test_zero_in_scope_matches_yields_empty_context(run_draft):
    letters = [_letter("MARK_B1", ORG_B, PROJ_B1)]
    run = run_draft(_user(["orgadmin"], org=ORG_A), letters)
    assert run.status == 200, run.body
    assert run.body["similar_letters"] == []
    assert "MARK_B1" not in run.prompt_text
    assert run.db.ai_generated_drafts.inserted[0]["similar_letters_used"] == []
