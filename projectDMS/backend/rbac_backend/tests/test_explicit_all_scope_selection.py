"""Explicit All Organisations / All Projects selection (owner policy, 2026-10-05).

Super Admin:
  * All Organisations + All Projects  => global
  * Organisation A    + All Projects  => Organisation A only
  * Organisation A    + Project A1    => Project A1 only
Organisation user / admin of Organisation A (2026-10-06 clarification):
  * Organisation A    + All Projects  => every project of Organisation A (default)
  * Organisation A    + Project A1    => Project A1 only
  * All Organisations, another organisation, or another organisation's project
    => refused 403
All Projects is contextual and is a selection instruction, not authority: it is
evaluated inside the selected organisation and the principal's own grants
(build_scope_query), so for a project user it means its assigned projects. All
Organisations is Super Admin's alone. An empty selection stays bounded by the
role's entitlement - never global.

The navbar sends ALL explicitly as ``__all__`` in ``X-Org-Id`` / ``X-Proj-Id``
(`client/src/services/active-scope.ts`). `resolve_active_scope` turns it into
``None`` plus an ``all_*`` flag, so the sentinel never reaches a query.

Deep planning is the consumer bound here. A token-authenticated Super Admin
principal carries no organisation, so before this change the navbar never
bounded the drafting context at all; these tests drive the route through the
real headers.
"""

import asyncio
import json
from typing import Any, Dict, List, Optional

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import rbac_backend.routers.deep_planning as dp
from rbac_backend.core.security import CurrentUser
from rbac_backend.core.tenant_context import ALL_SELECTION, resolve_active_scope
from rbac_backend.main import app
from rbac_backend.tests.test_deep_planning_document_scope import (
    D_A1,
    D_A2,
    D_B1,
    _document,
    _world,
)
from rbac_backend.tests.test_deep_planning_target_letter_scope import (
    ORG_A,
    ORG_B,
    PROJ_A1,
    PROJ_A2,
    PROJ_B1,
    QUERY,
    _Collection,
    _DB,
    _target,
    _user,
)

ALL = ALL_SELECTION
PROJ_A3 = "proj-a3"
D_A3 = "DOC_A3"
SUPERADMIN = _user(["superadmin"])  # token shape: no organisation, no projects


class _Projects(_Collection):
    """`projects` as resolve_active_scope queries it (`_id $in`, `is_active $ne False`)."""

    async def find_one(self, filt: Optional[Dict[str, Any]] = None, *_args, **_kwargs):
        self.find_one_filters.append(filt or {})
        wanted = [str(v) for v in (filt or {}).get("_id", {}).get("$in", [])]
        for row in self.rows:
            if str(row["_id"]) in wanted and row.get("is_active", True) is not False:
                return dict(row)
        return None


def _db(documents: List[Dict[str, Any]], letters: Optional[List[Dict[str, Any]]] = None) -> _DB:
    db = _DB(letters or [])
    db.documents = _Collection(documents)
    db.projects = _Projects(
        [
            {"_id": PROJ_A1, "organization_id": ORG_A},
            {"_id": PROJ_A2, "organization_id": ORG_A},
            {"_id": PROJ_A3, "organization_id": ORG_A},
            {"_id": PROJ_B1, "organization_id": ORG_B},
        ]
    )
    return db


# --------------------------------------------------------------------------
# resolve_active_scope: the explicit ALL value
# --------------------------------------------------------------------------
def _resolve(user: CurrentUser, org: str, project: str):
    return asyncio.run(resolve_active_scope(_db([]), user, organization_id=org, project_id=project))


def test_superadmin_all_all_resolves_to_explicit_global():
    scope = _resolve(SUPERADMIN, ALL, ALL)
    assert (scope.organization_id, scope.project_id) == (None, None)
    assert scope.all_organizations and scope.all_projects


def test_superadmin_organisation_and_all_projects_resolves_to_that_organisation():
    scope = _resolve(SUPERADMIN, ORG_A, ALL)
    assert (scope.organization_id, scope.project_id) == (ORG_A, None)
    assert scope.all_projects and not scope.all_organizations


def test_superadmin_organisation_and_project_resolves_to_that_project():
    scope = _resolve(SUPERADMIN, ORG_A, PROJ_A1)
    assert (scope.organization_id, scope.project_id) == (ORG_A, PROJ_A1)
    assert not scope.all_projects and not scope.all_organizations


def test_all_organisations_with_a_specific_project_is_refused():
    with pytest.raises(HTTPException) as exc:
        _resolve(SUPERADMIN, ALL, PROJ_A1)
    assert exc.value.status_code == 403


def test_stale_project_from_the_previous_organisation_is_refused():
    with pytest.raises(HTTPException) as exc:
        _resolve(SUPERADMIN, ORG_A, PROJ_B1)
    assert exc.value.status_code == 403


NON_SUPERADMIN = [
    _user(["orgadmin"], org=ORG_A),
    _user(["orguser"], org=ORG_A),
    _user(["projectadmin"], org=ORG_A, projects=[PROJ_A1]),
    _user(["projectuser"], org=ORG_A, projects=[PROJ_A1]),
    _user(["contraclaim_expert_drafter"], projects=[PROJ_A1], account_type="contraclaim_staff"),
    _user(["superuser"], orgs=[ORG_A]),
]
NON_SUPERADMIN_IDS = ["orgadmin", "orguser", "projectadmin", "projectuser", "expert", "superuser"]


@pytest.mark.parametrize("user", NON_SUPERADMIN, ids=NON_SUPERADMIN_IDS)
@pytest.mark.parametrize("project", [ALL, "", PROJ_A1])
def test_other_roles_cannot_select_all_organisations(user, project):
    with pytest.raises(HTTPException) as exc:
        _resolve(user, ALL, project)
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "context_forbidden"


@pytest.mark.parametrize("role", ["orgadmin", "orguser"])
def test_organisation_user_all_projects_resolves_to_its_own_organisation(role):
    scope = _resolve(_user([role], org=ORG_A), ORG_A, ALL)
    assert (scope.organization_id, scope.project_id) == (ORG_A, None)
    assert scope.all_projects and not scope.all_organizations


@pytest.mark.parametrize("role", ["orgadmin", "orguser"])
@pytest.mark.parametrize("project", [ALL, ""])
def test_organisation_user_forged_other_organisation_is_refused(role, project):
    with pytest.raises(HTTPException) as exc:
        _resolve(_user([role], org=ORG_A), ORG_B, project)
    assert exc.value.status_code == 403


@pytest.mark.parametrize("role", ["orgadmin", "orguser"])
@pytest.mark.parametrize("org", [ORG_A, ORG_B, ""])
def test_organisation_user_forged_other_organisation_project_is_refused(role, org):
    with pytest.raises(HTTPException) as exc:
        _resolve(_user([role], org=ORG_A), org, PROJ_B1)
    assert exc.value.status_code == 403


def test_expert_without_an_organisation_grant_cannot_name_one():
    user = _user(["contraclaim_expert_drafter"], projects=[PROJ_A1], account_type="contraclaim_staff")
    with pytest.raises(HTTPException):
        _resolve(user, ORG_A, ALL)


# --------------------------------------------------------------------------
# Deep planning bound to the navbar selection
# --------------------------------------------------------------------------
class _Run:
    def __init__(self, status: int, body: Any, prompts: List[str], db: _DB):
        self.status = status
        self.body = body
        self.prompt_text = "\n".join(prompts)
        self.called_model = bool(prompts)
        self.db = db
        self.response_text = json.dumps(body, default=str)


@pytest.fixture
def draft(monkeypatch):
    def _go(user: CurrentUser, db: _DB, headers: Dict[str, str], **payload_overrides) -> _Run:
        prompts: List[str] = []

        def _embedding(model: str, input: str):  # noqa: A002 - OpenAI signature
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

        async def _get_db():
            yield db

        monkeypatch.setattr(dp, "rate_limited_openai_call", lambda: None)
        monkeypatch.setattr(dp.client.embeddings, "create", _embedding)
        monkeypatch.setattr(dp.client.chat.completions, "create", _chat)
        monkeypatch.setattr(dp.PolicyService, "authorize", _allow)
        app.dependency_overrides[dp.get_db] = _get_db
        app.dependency_overrides[dp.get_current_user] = lambda: user
        try:
            payload = {
                "document_ids": [],
                "subject": "Reply to notice",
                "recipient": "Engineer",
                "user_id": user.id,
            }
            payload.update(payload_overrides)
            resp = TestClient(app).post("/api/deep-planning/generate-draft", json=payload, headers=headers)
        finally:
            app.dependency_overrides.pop(dp.get_db, None)
            app.dependency_overrides.pop(dp.get_current_user, None)
        try:
            body = resp.json()
        except ValueError:
            body = resp.text
        return _Run(resp.status_code, body, prompts, db)

    return _go


def _nav(org: str, project: str) -> Dict[str, str]:
    return {"X-Org-Id": org, "X-Proj-Id": project}


def _used(run: _Run, *tags: str) -> None:
    assert run.status == 200, run.body
    for tag in tags:
        assert f"{tag}_TEXT" in run.prompt_text, tag


def _refused(run: _Run, tag: str) -> None:
    assert run.status == 404, run.body
    assert tag not in run.response_text
    assert not run.called_model
    assert run.db.ai_generated_drafts.inserted == []


def test_superadmin_all_all_drafts_from_any_organisation(draft):
    docs, ids = _world()
    run = draft(SUPERADMIN, _db(docs), _nav(ALL, ALL), document_ids=[ids[D_A1], ids[D_B1]])
    _used(run, D_A1, D_B1)


def test_superadmin_organisation_all_projects_is_that_organisation_only(draft):
    docs, ids = _world()
    _used(draft(SUPERADMIN, _db(docs), _nav(ORG_A, ALL), document_ids=[ids[D_A1], ids[D_A2]]), D_A1, D_A2)
    _refused(draft(SUPERADMIN, _db(docs), _nav(ORG_A, ALL), document_ids=[ids[D_B1]]), D_B1)


def test_superadmin_organisation_and_project_is_that_project_only(draft):
    docs, ids = _world()
    _used(draft(SUPERADMIN, _db(docs), _nav(ORG_A, PROJ_A1), document_ids=[ids[D_A1]]), D_A1)
    _refused(draft(SUPERADMIN, _db(docs), _nav(ORG_A, PROJ_A1), document_ids=[ids[D_A2]]), D_A2)
    _refused(draft(SUPERADMIN, _db(docs), _nav(ORG_A, PROJ_A1), document_ids=[ids[D_B1]]), D_B1)


def test_navbar_project_bounds_similar_letters_and_reply_target(draft):
    a2 = _target("LTR_A2", ORG_A, PROJ_A2)
    a2["embedding"] = QUERY  # would be the top similar letter if it were in scope
    b1 = _target("LTR_B1", ORG_B, PROJ_B1)
    run = draft(SUPERADMIN, _db([], [a2, b1]), _nav(ORG_A, PROJ_A1), target_letter_id=str(b1["_id"]))
    assert run.status == 200, run.body
    assert "LTR_A2" not in run.prompt_text and "LTR_B1" not in run.prompt_text
    assert run.body["similar_letters"] == []


def test_body_cannot_leave_the_navbar_selection(draft):
    docs, ids = _world()
    run = draft(SUPERADMIN, _db(docs), _nav(ORG_A, PROJ_A1), project_id=PROJ_A2, document_ids=[ids[D_A2]])
    assert run.status == 403, run.body
    assert run.body["detail"]["code"] == "context_forbidden"
    assert not run.called_model


def test_stale_project_header_is_refused_before_any_read(draft):
    docs, ids = _world()
    db = _db(docs)
    run = draft(SUPERADMIN, db, _nav(ORG_A, PROJ_B1), document_ids=[ids[D_B1]])
    assert run.status == 403, run.body
    assert db.documents.find_one_filters == []
    assert not run.called_model


@pytest.mark.parametrize("user", NON_SUPERADMIN, ids=NON_SUPERADMIN_IDS)
def test_other_roles_sending_all_organisations_are_refused(draft, user):
    docs, ids = _world()
    run = draft(user, _db(docs), _nav(ALL, ALL), document_ids=[ids[D_A1]])
    assert run.status == 403, run.body
    assert not run.called_model


# --------------------------------------------------------------------------
# Organisation user / admin of Organisation A: the A-J matrix (G, the
# persisted stale project, is a client concern - see the TenantContext tests;
# its server half is F)
# --------------------------------------------------------------------------
def _org_world():
    docs, ids = _world()
    a3 = _document(D_A3, ORG_A, PROJ_A3)
    docs.append(a3)
    ids[D_A3] = str(a3["_id"])
    return docs, ids


ORG_ROLES = pytest.mark.parametrize("role", ["orguser", "orgadmin"])


@ORG_ROLES
def test_a_default_all_projects_reaches_every_project_of_the_organisation(draft, role):
    docs, ids = _org_world()
    run = draft(_user([role], org=ORG_A), _db(docs), _nav(ORG_A, ALL),
                document_ids=[ids[D_A1], ids[D_A2], ids[D_A3]])
    _used(run, D_A1, D_A2, D_A3)


@ORG_ROLES
def test_b_all_projects_never_reaches_another_organisation(draft, role):
    docs, ids = _org_world()
    _refused(draft(_user([role], org=ORG_A), _db(docs), _nav(ORG_A, ALL), document_ids=[ids[D_B1]]), D_B1)


@ORG_ROLES
def test_c_specific_project_is_that_project_only(draft, role):
    docs, ids = _org_world()
    user = _user([role], org=ORG_A)
    _used(draft(user, _db(docs), _nav(ORG_A, PROJ_A1), document_ids=[ids[D_A1]]), D_A1)
    _refused(draft(user, _db(docs), _nav(ORG_A, PROJ_A1), document_ids=[ids[D_A2]]), D_A2)


@ORG_ROLES
def test_d_returning_to_all_projects_restores_the_organisation(draft, role):
    docs, ids = _org_world()
    user = _user([role], org=ORG_A)
    _refused(draft(user, _db(docs), _nav(ORG_A, PROJ_A1), document_ids=[ids[D_A3]]), D_A3)
    _used(draft(user, _db(docs), _nav(ORG_A, ALL), document_ids=[ids[D_A3]]), D_A3)


@ORG_ROLES
@pytest.mark.parametrize("project", [ALL, ""])
def test_e_forged_organisation_is_refused(draft, role, project):
    docs, ids = _org_world()
    run = draft(_user([role], org=ORG_A), _db(docs), _nav(ORG_B, project), document_ids=[ids[D_B1]])
    assert run.status == 403, run.body
    assert not run.called_model


@ORG_ROLES
@pytest.mark.parametrize("org", [ORG_A, ORG_B])
def test_f_forged_other_organisation_project_is_refused(draft, role, org):
    docs, ids = _org_world()
    db = _db(docs)
    run = draft(_user([role], org=ORG_A), db, _nav(org, PROJ_B1), document_ids=[ids[D_B1]])
    assert run.status == 403, run.body
    assert db.documents.find_one_filters == []
    assert not run.called_model


@ORG_ROLES
def test_h_legacy_empty_selection_is_the_organisation_never_global(draft, role):
    docs, ids = _org_world()
    user = _user([role], org=ORG_A)
    _used(draft(user, _db(docs), {}, document_ids=[ids[D_A1], ids[D_A3]]), D_A1, D_A3)
    _refused(draft(user, _db(docs), {}, document_ids=[ids[D_B1]]), D_B1)


@ORG_ROLES
def test_i_similar_letters_and_reply_target_stay_in_the_organisation(draft, role):
    a3 = _target("LTR_A3", ORG_A, PROJ_A3)
    a3["embedding"] = QUERY
    b1 = _target("LTR_B1", ORG_B, PROJ_B1)
    b1["embedding"] = QUERY
    run = draft(_user([role], org=ORG_A), _db([], [a3, b1]), _nav(ORG_A, ALL), target_letter_id=str(b1["_id"]))
    assert run.status == 200, run.body
    assert [letter["id"] for letter in run.body["similar_letters"]] == [str(a3["_id"])]
    assert "LTR_B1" not in run.prompt_text


@ORG_ROLES
def test_j_mixed_ids_never_leak_the_other_organisation(draft, role):
    docs, ids = _org_world()
    run = draft(_user([role], org=ORG_A), _db(docs), _nav(ORG_A, ALL), document_ids=[ids[D_A1], ids[D_B1]])
    _refused(run, D_B1)
    assert D_A1 not in run.response_text


# --------------------------------------------------------------------------
# Project tier: All Projects is the assigned projects, never the organisation
# --------------------------------------------------------------------------
@pytest.mark.parametrize("role", ["projectuser", "projectadmin"])
def test_project_tier_all_projects_is_its_assigned_projects(draft, role):
    docs, ids = _org_world()
    user = _user([role], org=ORG_A, projects=[PROJ_A1, PROJ_A2])
    _used(draft(user, _db(docs), _nav(ORG_A, ALL), document_ids=[ids[D_A1], ids[D_A2]]), D_A1, D_A2)
    _refused(draft(user, _db(docs), _nav(ORG_A, ALL), document_ids=[ids[D_A3]]), D_A3)
    _refused(draft(user, _db(docs), _nav(ORG_A, ALL), document_ids=[ids[D_B1]]), D_B1)


@pytest.mark.parametrize(
    "user",
    [
        _user(["orgadmin"], org=ORG_A),
        _user(["projectuser"], org=ORG_A, projects=[PROJ_A1]),
        _user(["contraclaim_expert_drafter"], projects=[PROJ_A1], account_type="contraclaim_staff"),
    ],
    ids=["orgadmin", "projectuser", "expert"],
)
def test_other_roles_with_a_cleared_navbar_stay_inside_their_entitlement(draft, user):
    docs, ids = _world()
    _refused(draft(user, _db(docs), {}, document_ids=[ids[D_B1]]), D_B1)


def test_project_user_selected_project_narrows_to_it(draft):
    docs, ids = _world()
    user = _user(["projectuser"], org=ORG_A, projects=[PROJ_A1, PROJ_A2])
    _refused(draft(user, _db(docs), _nav(ORG_A, PROJ_A1), document_ids=[ids[D_A2]]), D_A2)
