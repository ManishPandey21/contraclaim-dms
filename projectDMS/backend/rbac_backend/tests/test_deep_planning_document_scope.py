"""Tenant scope of the deep-planning driving documents (`document_ids`).

`POST /api/deep-planning/generate-draft` takes a list of `document_ids` and
renders each document's filename, subject, parties, summary, extracted text and
references into the drafting prompt. The generated draft is returned to the
caller and persisted together with the id list, and an embedding of it is
computed, so anything in the prompt can come back out.

The invariant pinned here: every requested document must lie inside the
caller's authorised tenant scope - `build_scope_query`, narrowed by the
organisation the gate authorised and the requested project - or the request is
refused before any document content is read into a prompt. Checking the
organisation alone is not enough: a project-tier caller (or an organisation
user with project assignments, or a Super Admin with a project selected) would
otherwise pull a sibling project's document into context.

Refusal is all-or-nothing. A list that mixes an authorised id with an
unauthorised one is refused whole, so the unauthorised document reaches no
prompt, response, saved draft, stored id list or embedding input. An
out-of-scope document is indistinguishable from a nonexistent one.

The harness (fake Mongo that evaluates the router's filters, echoing fake LLM,
recorded embedding inputs) is shared with the `target_letter_id` tests.
Entry authorization (`PolicyService.authorize`) is stubbed to allow;
`authorize_scope` runs for real.
"""

import asyncio
import json
from typing import Any, Dict, List, Optional

import pytest
from bson import ObjectId
from fastapi import HTTPException
from fastapi.testclient import TestClient

import rbac_backend.routers.deep_planning as dp
from rbac_backend.core.security import CurrentUser
from rbac_backend.main import app
from rbac_backend.tests.test_deep_planning_target_letter_scope import (
    ORG_A,
    ORG_B,
    PROJ_A1,
    PROJ_A2,
    PROJ_B1,
    QUERY,
    _Collection,
    _DB,
    _user,
)

D_A1 = "DOC_A1"
D_A2 = "DOC_A2"
D_B1 = "DOC_B1"


def _document(tag: str, org: str, project: str) -> Dict[str, Any]:
    """A document whose every prompt-bound field carries `tag`."""
    return {
        "_id": ObjectId(),
        "filename": f"{tag}_FILENAME.pdf",
        "subject": f"{tag}_SUBJECT",
        "from_": f"{tag}_FROM",
        "to": f"{tag}_TO",
        "organization_id": org,
        "project_id": project,
        "summary": f"{tag}_SUMMARY",
        "ocrText": f"{tag}_TEXT",
        "references": [f"{tag}_REFERENCE"],
    }


def _world():
    docs = [_document(D_A1, ORG_A, PROJ_A1), _document(D_A2, ORG_A, PROJ_A2), _document(D_B1, ORG_B, PROJ_B1)]
    return docs, {D_A1: str(docs[0]["_id"]), D_A2: str(docs[1]["_id"]), D_B1: str(docs[2]["_id"])}


class _Run:
    def __init__(self, status: int, body: Any, prompts: List[str], db: _DB, embedded: List[str]):
        self.status = status
        self.body = body
        self.prompts = prompts
        self.embedded = embedded
        self.db = db
        self.prompt_text = "\n".join(prompts)
        self.response_text = json.dumps(body, default=str)
        self.saved_text = json.dumps(db.ai_generated_drafts.inserted, default=str)
        self.embedded_text = "\n".join(embedded)

    def leaked(self, tag: str, doc_id: Optional[str] = None) -> List[str]:
        sinks = {
            "prompt": self.prompt_text,
            "response": self.response_text,
            "saved_draft": self.saved_text,
            "embedding_input": self.embedded_text,
        }
        hits = [name for name, text in sinks.items() if tag in text]
        # The saved draft records the id list it was built from; an
        # unauthorised id there is a metadata reference to a foreign document.
        if doc_id and doc_id in self.saved_text:
            hits.append("saved_draft_document_ids")
        return hits


@pytest.fixture
def run_draft(monkeypatch):
    def _go(user: CurrentUser, documents: List[Dict[str, Any]], document_ids: List[str], **payload_overrides) -> _Run:
        db = _DB([])
        db.documents = _Collection(documents)
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
                "document_ids": document_ids,
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


def _assert_refused(run: _Run, tag: str, doc_id: str) -> None:
    """Refused before any model call, with nothing of `tag` anywhere."""
    assert run.status == 404, run.body
    assert run.leaked(tag, doc_id) == [], f"{tag} reached {run.leaked(tag, doc_id)}"
    assert run.prompts == [], "the model was called for a refused request"
    assert run.embedded == [], "an embedding was computed for a refused request"
    assert run.db.ai_generated_drafts.inserted == []
    assert run.db.writes_outside_drafts() == {}


def _assert_used(run: _Run, tag: str) -> None:
    assert run.status == 200, run.body
    for field in ("FILENAME", "SUBJECT", "SUMMARY", "TEXT", "REFERENCE"):
        assert f"{tag}_{field}" in run.prompt_text, field
    assert len(run.db.ai_generated_drafts.inserted) == 1


# --------------------------------------------------------------------------
# Project tier
# --------------------------------------------------------------------------
def test_project_user_own_project_document_is_used(run_draft):
    docs, ids = _world()
    user = _user(["projectuser"], org=ORG_A, projects=[PROJ_A1])
    _assert_used(run_draft(user, docs, [ids[D_A1]]), D_A1)


def test_project_user_sibling_project_document_is_refused(run_draft):
    docs, ids = _world()
    user = _user(["projectuser"], org=ORG_A, projects=[PROJ_A1])
    _assert_refused(run_draft(user, docs, [ids[D_A2]]), D_A2, ids[D_A2])


def test_project_user_foreign_organisation_document_is_refused(run_draft):
    docs, ids = _world()
    user = _user(["projectuser"], org=ORG_A, projects=[PROJ_A1])
    _assert_refused(run_draft(user, docs, [ids[D_B1]]), D_B1, ids[D_B1])


def test_project_admin_own_project_document_is_used(run_draft):
    docs, ids = _world()
    user = _user(["projectadmin"], org=ORG_A, projects=[PROJ_A1])
    _assert_used(run_draft(user, docs, [ids[D_A1]]), D_A1)


def test_project_admin_sibling_project_document_is_refused(run_draft):
    docs, ids = _world()
    user = _user(["projectadmin"], org=ORG_A, projects=[PROJ_A1])
    _assert_refused(run_draft(user, docs, [ids[D_A2]]), D_A2, ids[D_A2])


def test_project_admin_selected_project_excludes_other_assigned_project(run_draft):
    docs, ids = _world()
    user = _user(["projectadmin"], org=ORG_A, projects=[PROJ_A1, PROJ_A2])
    run = run_draft(user, docs, [ids[D_A2]], project_id=PROJ_A1)
    _assert_refused(run, D_A2, ids[D_A2])


# --------------------------------------------------------------------------
# Organisation tier
# --------------------------------------------------------------------------
def test_org_user_without_assignments_uses_any_project_in_own_organisation(run_draft):
    docs, ids = _world()
    _assert_used(run_draft(_user(["orguser"], org=ORG_A), docs, [ids[D_A2]]), D_A2)


def test_org_user_with_project_assignments_cannot_use_sibling_project(run_draft):
    # Effective scope: an organisation user with explicit project assignments
    # sees those projects only (build_scope_query, org tier).
    docs, ids = _world()
    user = _user(["orguser"], org=ORG_A, projects=[PROJ_A1])
    _assert_refused(run_draft(user, docs, [ids[D_A2]]), D_A2, ids[D_A2])


def test_org_user_foreign_organisation_document_is_refused(run_draft):
    docs, ids = _world()
    _assert_refused(run_draft(_user(["orguser"], org=ORG_A), docs, [ids[D_B1]]), D_B1, ids[D_B1])


def test_org_admin_uses_documents_across_own_organisation(run_draft):
    docs, ids = _world()
    run = run_draft(_user(["orgadmin"], org=ORG_A), docs, [ids[D_A1], ids[D_A2]])
    _assert_used(run, D_A1)
    _assert_used(run, D_A2)


def test_org_admin_foreign_organisation_document_is_refused(run_draft):
    docs, ids = _world()
    _assert_refused(run_draft(_user(["orgadmin"], org=ORG_A), docs, [ids[D_B1]]), D_B1, ids[D_B1])


# --------------------------------------------------------------------------
# Roles outside the organisation / project tiers
# --------------------------------------------------------------------------
def test_expert_assigned_project_document_is_used(run_draft):
    docs, ids = _world()
    user = _user(["contraclaim_expert_drafter"], projects=[PROJ_A1], account_type="contraclaim_staff")
    _assert_used(run_draft(user, docs, [ids[D_A1]]), D_A1)


def test_expert_unassigned_project_document_is_refused(run_draft):
    docs, ids = _world()
    user = _user(["contraclaim_expert_drafter"], projects=[PROJ_A1], account_type="contraclaim_staff")
    _assert_refused(run_draft(user, docs, [ids[D_A2]]), D_A2, ids[D_A2])


def test_dormant_super_user_granted_organisation_document_is_used(run_draft):
    docs, ids = _world()
    _assert_used(run_draft(_user(["superuser"], orgs=[ORG_A]), docs, [ids[D_A1]]), D_A1)


def test_dormant_super_user_ungranted_organisation_document_is_refused(run_draft):
    docs, ids = _world()
    run = run_draft(_user(["superuser"], orgs=[ORG_A]), docs, [ids[D_B1]])
    _assert_refused(run, D_B1, ids[D_B1])


# --------------------------------------------------------------------------
# Super Admin: the navbar selection bounds the context
# --------------------------------------------------------------------------
def test_superadmin_selected_organisation_refuses_another_organisation(run_draft):
    docs, ids = _world()
    _assert_refused(run_draft(_user(["superadmin"], org=ORG_A), docs, [ids[D_B1]]), D_B1, ids[D_B1])


def test_superadmin_selected_organisation_uses_its_documents(run_draft):
    docs, ids = _world()
    _assert_used(run_draft(_user(["superadmin"], org=ORG_A), docs, [ids[D_A2]]), D_A2)


def test_superadmin_selected_project_refuses_sibling_project(run_draft):
    docs, ids = _world()
    run = run_draft(_user(["superadmin"], org=ORG_A), docs, [ids[D_A2]], project_id=PROJ_A1)
    _assert_refused(run, D_A2, ids[D_A2])


def test_superadmin_selected_project_uses_its_documents(run_draft):
    docs, ids = _world()
    run = run_draft(_user(["superadmin"], org=ORG_A), docs, [ids[D_A1]], project_id=PROJ_A1)
    _assert_used(run, D_A1)


def test_superadmin_without_selection_has_the_consolidated_view(run_draft):
    # Same policy as similar letters and the reply target: no navbar
    # selection is the consolidated (global) view for Super Admin.
    docs, ids = _world()
    _assert_used(run_draft(_user(["superadmin"]), docs, [ids[D_B1]]), D_B1)


# --------------------------------------------------------------------------
# Mixed lists are refused whole
# --------------------------------------------------------------------------
def test_mixed_list_is_refused_whole_and_nothing_enters_context(run_draft):
    docs, ids = _world()
    user = _user(["projectuser"], org=ORG_A, projects=[PROJ_A1])
    run = run_draft(user, docs, [ids[D_A1], ids[D_A2]])
    _assert_refused(run, D_A2, ids[D_A2])
    # The authorised document does not reach a model either: no partial run.
    assert run.leaked(D_A1) == []


def test_mixed_list_unauthorised_first_is_refused_whole(run_draft):
    docs, ids = _world()
    user = _user(["projectadmin"], org=ORG_A, projects=[PROJ_A1])
    run = run_draft(user, docs, [ids[D_B1], ids[D_A1]])
    _assert_refused(run, D_B1, ids[D_B1])
    assert run.leaked(D_A1) == []


# --------------------------------------------------------------------------
# Malformed, nonexistent, and the existence oracle
# --------------------------------------------------------------------------
@pytest.mark.parametrize("bad", ["not-an-object-id", "", "{\"$ne\": null}"])
def test_malformed_id_is_a_400_and_reads_nothing(run_draft, bad):
    docs, _ids = _world()
    run = run_draft(_user(["orgadmin"], org=ORG_A), docs, [bad])
    assert run.status == 400, run.body
    assert run.db.documents.find_one_filters == []
    assert run.prompts == [] and run.embedded == []
    assert run.db.ai_generated_drafts.inserted == []


def test_nonexistent_id_is_refused(run_draft):
    docs, _ids = _world()
    missing = str(ObjectId())
    run = run_draft(_user(["orgadmin"], org=ORG_A), docs, [missing])
    _assert_refused(run, "NO_SUCH_TAG", missing)


def _normalised(run: _Run, doc_id: str) -> str:
    return run.response_text.replace(doc_id, "<id>")


@pytest.mark.parametrize(
    "user,foreign_tag",
    [
        (_user(["projectuser"], org=ORG_A, projects=[PROJ_A1]), D_A2),
        (_user(["orgadmin"], org=ORG_A), D_B1),
    ],
    ids=["sibling-project", "foreign-organisation"],
)
def test_out_of_scope_is_indistinguishable_from_nonexistent(run_draft, user, foreign_tag):
    docs, ids = _world()
    missing_id = str(ObjectId())
    foreign = run_draft(user, docs, [ids[foreign_tag]])
    missing = run_draft(user, docs, [missing_id])
    assert foreign.status == missing.status == 404
    assert _normalised(foreign, ids[foreign_tag]) == _normalised(missing, missing_id)


# --------------------------------------------------------------------------
# Fail closed: no scope means no read at all
# --------------------------------------------------------------------------
def test_role_refused_at_the_gate_never_reads_a_document(run_draft):
    docs, ids = _world()
    run = run_draft(_user(["contractmgr_org"], org=ORG_A), docs, [ids[D_A1]])
    assert run.status == 403
    assert run.db.documents.find_one_filters == []
    assert run.prompts == []


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
def test_empty_scope_refuses_without_reading(user):
    docs, ids = _world()
    db = _DB([])
    db.documents = _Collection(docs)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(dp.validate_document_ids(db, [ids[D_A1]], user))
    assert exc.value.status_code == 404
    assert db.documents.find_one_filters == []


def test_empty_id_list_needs_no_scope_and_reads_nothing():
    db = _DB([])
    db.documents = _Collection(_world()[0])
    asyncio.run(dp.validate_document_ids(db, [], None))
    assert db.documents.find_one_filters == []
