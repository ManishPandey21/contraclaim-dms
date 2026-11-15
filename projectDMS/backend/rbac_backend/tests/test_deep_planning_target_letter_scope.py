"""Tenant isolation of the deep-planning reply target (`target_letter_id`).

`POST /api/deep-planning/generate-draft` accepts a `target_letter_id` and, when
it resolves, renders that letter's summary, keywords, contractual clauses,
references and an OCR excerpt into the drafting prompt as "Target Letter
Intelligence". The generated draft is returned to the caller and persisted, so
anything in the prompt can come back out.

The invariant pinned here: the target letter is looked up only inside the
caller's authorised tenant scope (`build_scope_query`, narrowed by the
organisation the gate authorised and the requested project). An out-of-scope
target behaves exactly like a nonexistent one - no content in the prompt, the
response or the saved draft, and no observable difference that would confirm
the id exists.

The fake LLM echoes its prompt into the draft. That models the worst case - a
model that paraphrases its context - so the response, the persisted draft and
every text sent to the embedding API are checked against the same markers as
the prompt. Every collection the router touches is recorded, so a write
anywhere other than the caller's own draft (a letter-embedding backfill, a
reference or metadata update) fails the test.

Entry authorization (`PolicyService.authorize`) is stubbed to allow; it needs
subscription, permission and expert-allocation fixtures and is not under test.
`authorize_scope` runs for real.
"""

import asyncio
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

# Targets are far from the query, so they never enter through the similar-letter
# path: anything of theirs that reaches a sink came through target_letter_id.
UNRELATED = [0.0, 0.0, 1.0]
QUERY = [1.0, 0.0, 0.0]

INTEL_HEADER = "Target Letter Intelligence"


# --------------------------------------------------------------------------
# A fake Mongo that evaluates the filters the router sends
# --------------------------------------------------------------------------
def _same(stored: Any, wanted: Any) -> bool:
    if isinstance(stored, ObjectId) != isinstance(wanted, ObjectId):
        return False  # Mongo does not equate an ObjectId with its hex string
    return stored == wanted


def _field_matches(row: Dict[str, Any], field: str, cond: Any) -> bool:
    value = row.get(field)
    if isinstance(cond, dict) and any(str(k).startswith("$") for k in cond):
        for op, arg in cond.items():
            if op == "$in":
                if not any(_same(value, item) for item in arg):
                    return False
            elif op == "$regex":
                return False  # not used on the paths under test
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

    async def to_list(self, length: Optional[int] = None):
        return list(self._rows[: length if length else len(self._rows)])


class _Collection:
    def __init__(self, rows: Optional[List[Dict[str, Any]]] = None):
        self.rows = list(rows or [])
        self.find_one_filters: List[Dict[str, Any]] = []
        self.updated_ids: List[Any] = []
        self.inserted: List[Dict[str, Any]] = []

    def find(self, filt: Optional[Dict[str, Any]] = None, *_args, **_kwargs):
        return _Cursor([dict(r) for r in self.rows if _matches(r, filt or {})])

    async def find_one(self, filt: Optional[Dict[str, Any]] = None, *_args, **_kwargs):
        self.find_one_filters.append(filt or {})
        for row in self.rows:
            if _matches(row, filt or {}):
                return dict(row)
        return None

    async def update_one(self, filt: Dict[str, Any], _update: Dict[str, Any], *_args, **_kwargs):
        self.updated_ids.append(filt.get("_id"))

    async def insert_one(self, doc: Dict[str, Any]):
        self.inserted.append(doc)


class _DB:
    def __init__(self, letters: List[Dict[str, Any]]):
        self.letters = _Collection(letters)
        self.documents = _Collection()
        self.ai_generated_drafts = _Collection()
        self.extra: Dict[str, _Collection] = {}

    def __getattr__(self, name: str) -> _Collection:
        # Any collection the test did not seed is created empty and recorded.
        if name.startswith("__"):
            raise AttributeError(name)
        return self.extra.setdefault(name, _Collection())

    def writes_outside_drafts(self) -> Dict[str, Any]:
        written = [("letters", self.letters), ("documents", self.documents), *self.extra.items()]
        return {
            name: {"updated": coll.updated_ids, "inserted": coll.inserted}
            for name, coll in written
            if coll.updated_ids or coll.inserted
        }


def _target(tag: str, org: str, project: str, *, _id: Any = None) -> Dict[str, Any]:
    """A letter whose every intelligence field carries `tag`."""
    return {
        "_id": _id if _id is not None else ObjectId(),
        "subject": f"subject {tag}",
        "content": f"content {tag}",
        "recipient": f"recipient {tag}",
        "organization_id": org,
        "project_id": project,
        "summary": f"summary {tag}_SUMMARY",
        "keywords": [f"{tag}_KEYWORD"],
        "contractual_clauses": [f"{tag}_CLAUSE"],
        "references": [f"{tag}_REFERENCE"],
        "ocrText": f"ocr {tag}_OCR",
        "embedding": UNRELATED,
    }


T_A1 = "TGT_A1"
T_A2 = "TGT_A2"
T_B1 = "TGT_B1"


def _world():
    a1 = _target(T_A1, ORG_A, PROJ_A1)
    a2 = _target(T_A2, ORG_A, PROJ_A2)
    b1 = _target(T_B1, ORG_B, PROJ_B1)
    return [a1, a2, b1], {T_A1: str(a1["_id"]), T_A2: str(a2["_id"]), T_B1: str(b1["_id"])}


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
    def __init__(self, status: int, body: Any, prompts: List[str], db: _DB, embedded: List[str]):
        self.status = status
        self.body = body
        self.prompt_text = "\n".join(prompts)
        self.db = db
        self.response_text = json.dumps(body, default=str)
        self.saved_text = json.dumps(db.ai_generated_drafts.inserted, default=str)
        self.embedded_text = "\n".join(embedded)

    def leaked(self, tag: str) -> List[str]:
        sinks = {
            "prompt": self.prompt_text,
            "response": self.response_text,
            "saved_draft": self.saved_text,
            "embedding_input": self.embedded_text,
        }
        return [name for name, text in sinks.items() if tag in text]


@pytest.fixture
def run_draft(monkeypatch):
    def _go(user: CurrentUser, letters: List[Dict[str, Any]], **payload_overrides) -> _Run:
        db = _DB(letters)
        prompts: List[str] = []
        embedded: List[str] = []

        def _embedding(model: str, input: str):  # noqa: A002 - OpenAI signature
            embedded.append(str(input))

            class _D:
                embedding = QUERY

            class _R:
                data = [_D()]

            return _R()

        def _chat(model: str, messages: List[Dict[str, str]], **_kwargs):
            text = "\n".join(str(m.get("content", "")) for m in messages)
            prompts.append(text)

            class _M:
                content = "DRAFT ECHO:\n" + text

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
                "subject": "Reply to notice",
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
        return _Run(resp.status_code, body, prompts, db, embedded)

    return _go


def _assert_withheld(run: _Run, tag: str) -> None:
    assert run.status == 200, run.body
    assert run.leaked(tag) == [], f"{tag} reached {run.leaked(tag)}"
    assert INTEL_HEADER not in run.prompt_text
    # No side effect anywhere: no letter embedding backfill, no reference or
    # metadata write; the only write is the caller's own draft.
    assert run.db.writes_outside_drafts() == {}
    assert len(run.db.ai_generated_drafts.inserted) == 1


def _assert_used(run: _Run, tag: str) -> None:
    assert run.status == 200, run.body
    assert INTEL_HEADER in run.prompt_text
    for field in ("SUMMARY", "KEYWORD", "CLAUSE", "REFERENCE", "OCR"):
        assert f"{tag}_{field}" in run.prompt_text, field


# --------------------------------------------------------------------------
# Organisation tier
# --------------------------------------------------------------------------
def test_org_admin_cannot_target_a_foreign_organisation_letter(run_draft):
    letters, ids = _world()
    run = run_draft(_user(["orgadmin"], org=ORG_A), letters, target_letter_id=ids[T_B1])
    _assert_withheld(run, T_B1)


def test_org_admin_target_in_own_organisation_still_works(run_draft):
    letters, ids = _world()
    run = run_draft(_user(["orgadmin"], org=ORG_A), letters, target_letter_id=ids[T_A2])
    _assert_used(run, T_A2)


def test_org_user_cannot_target_a_foreign_organisation_letter(run_draft):
    letters, ids = _world()
    run = run_draft(_user(["orguser"], org=ORG_A), letters, target_letter_id=ids[T_B1])
    _assert_withheld(run, T_B1)


def test_org_user_with_project_assignments_cannot_target_sibling_project(run_draft):
    letters, ids = _world()
    user = _user(["orguser"], org=ORG_A, projects=[PROJ_A1])
    run = run_draft(user, letters, target_letter_id=ids[T_A2])
    _assert_withheld(run, T_A2)


# --------------------------------------------------------------------------
# Project tier
# --------------------------------------------------------------------------
def test_project_user_cannot_target_sibling_project_in_same_organisation(run_draft):
    letters, ids = _world()
    user = _user(["projectuser"], org=ORG_A, projects=[PROJ_A1])
    run = run_draft(user, letters, target_letter_id=ids[T_A2])
    _assert_withheld(run, T_A2)


def test_project_user_cannot_target_a_foreign_organisation_letter(run_draft):
    letters, ids = _world()
    user = _user(["projectuser"], org=ORG_A, projects=[PROJ_A1])
    run = run_draft(user, letters, target_letter_id=ids[T_B1])
    _assert_withheld(run, T_B1)


def test_project_user_target_in_assigned_project_still_works(run_draft):
    letters, ids = _world()
    user = _user(["projectuser"], org=ORG_A, projects=[PROJ_A1])
    run = run_draft(user, letters, target_letter_id=ids[T_A1])
    _assert_used(run, T_A1)


def test_project_admin_selected_project_excludes_other_assigned_project(run_draft):
    letters, ids = _world()
    user = _user(["projectadmin"], org=ORG_A, projects=[PROJ_A1, PROJ_A2])
    run = run_draft(user, letters, project_id=PROJ_A1, target_letter_id=ids[T_A2])
    _assert_withheld(run, T_A2)


# --------------------------------------------------------------------------
# Roles outside the organisation / project tiers
# --------------------------------------------------------------------------
def test_expert_cannot_target_a_letter_outside_assigned_projects(run_draft):
    letters, ids = _world()
    user = _user(["contraclaim_expert_drafter"], projects=[PROJ_A1], account_type="contraclaim_staff")
    run = run_draft(user, letters, target_letter_id=ids[T_B1])
    _assert_withheld(run, T_B1)


def test_expert_target_in_assigned_project_still_works(run_draft):
    letters, ids = _world()
    user = _user(["contraclaim_expert_drafter"], projects=[PROJ_A1], account_type="contraclaim_staff")
    run = run_draft(user, letters, target_letter_id=ids[T_A1])
    _assert_used(run, T_A1)


def test_dormant_super_user_cannot_target_outside_granted_organisations(run_draft):
    # authorize_scope still admits a superuser with granted organisations.
    letters, ids = _world()
    run = run_draft(_user(["superuser"], orgs=[ORG_A]), letters, target_letter_id=ids[T_B1])
    _assert_withheld(run, T_B1)


# --------------------------------------------------------------------------
# Super Admin
# --------------------------------------------------------------------------
def test_superadmin_with_selected_organisation_cannot_target_another(run_draft):
    letters, ids = _world()
    run = run_draft(_user(["superadmin"], org=ORG_A), letters, target_letter_id=ids[T_B1])
    _assert_withheld(run, T_B1)


def test_superadmin_with_selected_project_cannot_target_sibling_project(run_draft):
    letters, ids = _world()
    run = run_draft(
        _user(["superadmin"], org=ORG_A), letters, project_id=PROJ_A1, target_letter_id=ids[T_A2]
    )
    _assert_withheld(run, T_A2)


def test_superadmin_without_selection_reaches_any_target(run_draft):
    letters, ids = _world()
    run = run_draft(_user(["superadmin"]), letters, target_letter_id=ids[T_B1])
    _assert_used(run, T_B1)


# --------------------------------------------------------------------------
# Malformed, nonexistent, and the existence oracle
# --------------------------------------------------------------------------
@pytest.mark.parametrize("target", ["not-an-object-id", str(ObjectId()), ""])
def test_malformed_or_nonexistent_target_drafts_without_intelligence(run_draft, target):
    letters, _ids = _world()
    run = run_draft(_user(["orgadmin"], org=ORG_A), letters, target_letter_id=target)
    assert run.status == 200, run.body
    assert INTEL_HEADER not in run.prompt_text


def test_out_of_scope_target_is_indistinguishable_from_nonexistent(run_draft):
    letters, ids = _world()
    user = _user(["orgadmin"], org=ORG_A)
    foreign = run_draft(user, letters, target_letter_id=ids[T_B1])
    missing = run_draft(user, letters, target_letter_id=str(ObjectId()))
    assert foreign.status == missing.status == 200
    assert foreign.prompt_text == missing.prompt_text
    assert foreign.body["structure_summary"] == missing.body["structure_summary"]


def test_string_keyed_target_in_scope_still_resolves(run_draft):
    letters, _ids = _world()
    letters.append(_target("TGT_STR", ORG_A, PROJ_A1, _id="legacy-letter-7"))
    run = run_draft(_user(["orgadmin"], org=ORG_A), letters, target_letter_id="legacy-letter-7")
    _assert_used(run, "TGT_STR")


def test_string_keyed_foreign_target_is_withheld(run_draft):
    letters, _ids = _world()
    letters.append(_target("TGT_STR_B", ORG_B, PROJ_B1, _id="legacy-letter-9"))
    run = run_draft(_user(["orgadmin"], org=ORG_A), letters, target_letter_id="legacy-letter-9")
    _assert_withheld(run, "TGT_STR_B")


# --------------------------------------------------------------------------
# Fail closed: no scope means no lookup at all
# --------------------------------------------------------------------------
def test_role_refused_at_the_gate_never_looks_up_the_target(run_draft):
    letters, ids = _world()
    run = run_draft(_user(["contractmgr_org"], org=ORG_A), letters, target_letter_id=ids[T_A1])
    assert run.status == 403
    assert run.db.letters.find_one_filters == []
    assert run.prompt_text == ""


@pytest.mark.parametrize(
    "user",
    [
        _user(["contraclaim_expert_drafter"], account_type="contraclaim_staff"),
        _user(["projectuser"], org=ORG_A),
        _user(["orgadmin"]),
        _user(["superuser"]),
        _user(["contractmgr_org"], org=ORG_A),
        _user([]),
        None,
    ],
    ids=["expert-no-projects", "project-user-no-projects", "org-admin-no-org", "superuser-no-orgs",
         "contractmgr-org-only", "no-roles", "no-principal"],
)
def test_empty_scope_never_reads_the_target(user):
    letters, ids = _world()
    db = _DB(letters)
    found = asyncio.run(dp.find_scoped_letter(db, ids[T_A1], user))
    assert found is None
    assert db.letters.find_one_filters == []
