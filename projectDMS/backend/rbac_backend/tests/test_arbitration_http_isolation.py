"""HTTP-level multi-tenant isolation tests for the arbitration API (ARB-107).

Follows the ``test_http_isolation.py`` pattern: the real FastAPI app and real
PolicyService/ScopeService run against overridden ``get_db``/``get_current_user``
with a seeded two-tenant dataset. Permission and entitlement gates are stubbed
to "allow" so the tests prove *tenant scope* end-to-end. The RBAC-hardening
project/organization ownership guard (``ScopeService.project_belongs_to_organization``)
stays live: the fixture seeds a ``projects`` collection so same-tenant access
resolves, and a dedicated test pins that a mismatched project/org denies.

- an Org-B user can never read or mutate Org-A arbitration cases, matrix rows,
  agent runs, or exhibit lists (404 from the scoped case lookup);
- an Org-B user cannot read Org-A drafts (403 from the policy scope check);
- list endpoints only return the caller's tenant;
- a superadmin retains access.

Deterministic and infra-free: no MongoDB, no app startup events.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

try:
    from fastapi.testclient import TestClient

    from rbac_backend.core.database import get_db
    from rbac_backend.core.security import get_current_user
    from rbac_backend.main import app
    from rbac_backend.services.audit_event_service import AuditEventService
    from rbac_backend.services.entitlement_service import EntitlementService
    from rbac_backend.services.permission_service import PermissionService
    from rbac_backend.services.step_up_service import StepUpService
    from rbac_backend.services.arbitration_drafting.workflow_service import ArbitrationWorkflowService

    _IMPORTS_OK = True
except Exception as exc:  # pragma: no cover - environment without httpx/TestClient
    _IMPORTS_OK = False
    _IMPORT_ERROR = str(exc)

pytestmark = pytest.mark.skipif(not _IMPORTS_OK, reason="TestClient/app unavailable")


class _FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def sort(self, *args, **kwargs):
        return self

    def skip(self, *args, **kwargs):
        return self

    def limit(self, value):
        self.rows = self.rows[:value]
        return self

    async def to_list(self, length=None):
        return list(self.rows if length is None else self.rows[:length])

    def __aiter__(self):
        self._iter = iter(list(self.rows))
        return self

    async def __anext__(self):
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _FakeCollection:
    def __init__(self, rows=None):
        self.rows = [dict(row) for row in (rows or [])]

    def find(self, query=None, *args, **kwargs):
        query = query or {}
        return _FakeCursor([row for row in self.rows if self._matches(row, query)])

    async def find_one(self, query=None, *args, **kwargs):
        rows = await self.find(query).to_list()
        return rows[0] if rows else None

    async def insert_one(self, row):
        self.rows.append(dict(row))
        return SimpleNamespace(inserted_id=row.get("_id"))

    async def insert_many(self, rows):
        self.rows.extend(dict(row) for row in rows)
        return SimpleNamespace(inserted_ids=[row.get("_id") for row in rows])

    async def delete_many(self, query=None):
        query = query or {}
        before = len(self.rows)
        self.rows = [row for row in self.rows if not self._matches(row, query)]
        return SimpleNamespace(deleted_count=before - len(self.rows))

    async def update_one(self, query=None, update=None, *args, **kwargs):
        row = await self.find_one(query)
        if row and update and "$set" in update:
            row.update(update["$set"])
        return SimpleNamespace(matched_count=1 if row else 0, modified_count=1 if row else 0)

    async def update_many(self, query=None, update=None, *args, **kwargs):
        rows = await self.find(query).to_list()
        if update and "$set" in update:
            for row in rows:
                row.update(update["$set"])
        return SimpleNamespace(matched_count=len(rows), modified_count=len(rows))

    async def find_one_and_update(self, query=None, update=None, *args, **kwargs):
        row = await self.find_one(query)
        if row and update and "$set" in update:
            row.update(update["$set"])
        return row

    def _matches(self, row, query):
        for key, expected in query.items():
            actual = row.get(key)
            if isinstance(expected, dict):
                if "$exists" in expected:
                    if bool(key in row) != bool(expected["$exists"]):
                        return False
                    continue
                if "$in" in expected:
                    if actual not in expected["$in"]:
                        return False
                    continue
                if "$ne" in expected:
                    if actual == expected["$ne"]:
                        return False
                    continue
                if "$regex" in expected:
                    import re as _re

                    if not _re.search(str(expected["$regex"]), str(actual or ""), flags=_re.I):
                        return False
                    continue
                return False
            if key == "$or":
                continue
            if actual != expected:
                return False
        return True


_MATRIX_COLLECTIONS = [
    "arbitration_document_index",
    "arbitration_chronology_matrix",
    "arbitration_clause_matrix",
    "arbitration_issue_matrix",
    "arbitration_claim_matrix",
    "arbitration_defence_matrix",
    "arbitration_counterclaim_matrix",
    "arbitration_rejoinder_matrix",
    "arbitration_quantum_annexures",
    "arbitration_notice_compliance",
    "arbitration_jurisdiction_matrix",
    "arbitration_expert_alignment",
]


class _FakeDb:
    def __init__(self):
        self.arbitration_cases = _FakeCollection(
            [
                {
                    "_id": "case-a1",
                    "organization_id": "org-A",
                    "project_id": "proj-a1",
                    "title": "Org A EOT case",
                    "party_perspective": "claimant",
                    "status": "matrix_preparation",
                },
                {
                    "_id": "case-b1",
                    "organization_id": "org-B",
                    "project_id": "proj-b1",
                    "title": "Org B payment case",
                    "party_perspective": "respondent",
                    "status": "matrix_preparation",
                },
            ]
        )
        for name in _MATRIX_COLLECTIONS:
            setattr(self, name, _FakeCollection([]))
        self.arbitration_document_index = _FakeCollection(
            [
                {
                    "_id": "doc-row-a1",
                    "case_id": "case-a1",
                    "organization_id": "org-A",
                    "project_id": "proj-a1",
                    "source_id": "doc-1",
                    "source_type": "document",
                    "title": "Org A delay notice",
                    "exhibit_prefix": "C",
                    "exhibit_number": 1,
                    "exhibit_id": "C-1",
                    "approval_status": "approved",
                    "verification_status": "verified",
                }
            ]
        )
        self.arbitration_agent_runs = _FakeCollection(
            [
                {
                    "_id": "run-a1",
                    "case_id": "case-a1",
                    "agent_type": "document-indexing",
                    "status": "completed",
                }
            ]
        )
        self.arbitration_readiness_checks = _FakeCollection([])
        self.arbitration_workflow_approvals = _FakeCollection([])
        self.arbitration_workflow_runs = _FakeCollection(
            [
                {
                    "_id": "workflow-a1",
                    "case_id": "case-a1",
                    "organization_id": "org-A",
                    "project_id": "proj-a1",
                    "engine": "arbitration_v2",
                    "status": "awaiting_matrix_review",
                }
            ]
        )
        self.arbitration_workflow_events = _FakeCollection([])
        self.arbitration_bundle_exports = _FakeCollection([])
        self.arbitration_drafts = _FakeCollection(
            [
                {
                    "_id": "draft-a1",
                    "case_id": "case-a1",
                    "organization_id": "org-A",
                    "project_id": "proj-a1",
                    "draft_type": "statement_of_claim",
                    "party_role": "claimant",
                    "title": "Org A SoC",
                    "status": "draft",
                }
            ]
        )
        self.arbitration_draft_versions = _FakeCollection([])
        self.arbitration_selected_references = _FakeCollection([])
        self.arbitration_claim_heads = _FakeCollection([])
        self.arbitration_paragraph_responses = _FakeCollection([])
        self.arbitration_generation_runs = _FakeCollection([])
        self.organization_memberships = _FakeCollection([])
        self.project_memberships = _FakeCollection([])
        self.audit_events = _FakeCollection([])
        self.tasks = _FakeCollection([])
        # RBAC hardening: is_client_scope_allowed cross-checks that the request's
        # project actually belongs to the request's organization via db.projects
        # (fail-closed when the collection/rows are missing). Seed real ownership
        # rows so same-tenant access resolves and the guard stays exercised.
        self.projects = _FakeCollection(
            [
                {"_id": "proj-a1", "organization_id": "org-A", "name": "Org A project"},
                {"_id": "proj-b1", "organization_id": "org-B", "name": "Org B project"},
            ]
        )

    def __getitem__(self, name):
        return getattr(self, name)


def _user(*, org, roles=("orguser",), projects=()):
    return SimpleNamespace(
        id=f"user-{org or 'root'}",
        username=f"user-{org or 'root'}",
        email=f"user-{org or 'root'}@example.com",
        roles=list(roles),
        organization_id=org,
        organizations=[org] if org else [],
        projects=list(projects),
        account_type="client_user",
        disabled=False,
    )


ORG_A_USER = {"org": "org-A"}
ORG_B_USER = {"org": "org-B"}
SUPERADMIN = {"org": None, "roles": ("superadmin",)}


@pytest.fixture()
def client(monkeypatch):
    """TestClient with a fresh two-tenant db; permission/entitlement gates allow."""

    async def _allow_permission(*_args, **_kwargs):
        return True

    async def _allow_entitlement(*_args, **_kwargs):
        return True, "ok"

    async def _no_audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(PermissionService, "user_has_permission", _allow_permission)
    monkeypatch.setattr(EntitlementService, "check_permission_entitlement", _allow_entitlement)
    monkeypatch.setattr(AuditEventService, "emit", _no_audit)

    db = _FakeDb()

    async def _override_db():
        yield db

    app.dependency_overrides[get_db] = _override_db

    def _as(user_kwargs):
        app.dependency_overrides[get_current_user] = lambda: _user(**user_kwargs)
        return TestClient(app)  # no context manager -> startup events do not run

    try:
        yield SimpleNamespace(as_user=_as, db=db)
    finally:
        app.dependency_overrides.clear()


def test_case_list_is_tenant_scoped(client):
    data = client.as_user(ORG_A_USER).get("/api/arbitration/cases").json()
    assert {row["organization_id"] for row in data} == {"org-A"}

    data = client.as_user(ORG_B_USER).get("/api/arbitration/cases").json()
    assert {row["organization_id"] for row in data} == {"org-B"}

    data = client.as_user(SUPERADMIN).get("/api/arbitration/cases").json()
    assert {row["organization_id"] for row in data} == {"org-A", "org-B"}


def test_case_get_denies_cross_tenant(client):
    assert client.as_user(ORG_A_USER).get("/api/arbitration/cases/case-a1").status_code == 200
    assert client.as_user(ORG_B_USER).get("/api/arbitration/cases/case-a1").status_code == 404
    assert client.as_user(SUPERADMIN).get("/api/arbitration/cases/case-a1").status_code == 200


def test_case_patch_denies_cross_tenant(client):
    cross = client.as_user(ORG_B_USER).patch(
        "/api/arbitration/cases/case-a1", json={"title": "Hijacked"}
    )
    assert cross.status_code == 404

    same = client.as_user(ORG_A_USER).patch(
        "/api/arbitration/cases/case-a1", json={"title": "Org A EOT case (amended)"}
    )
    assert same.status_code == 200
    assert same.json()["title"] == "Org A EOT case (amended)"
    # Cross-tenant attempt must not have mutated anything.
    stored = client.db.arbitration_cases.rows[0]
    assert stored["title"] == "Org A EOT case (amended)"


def test_checkpoint_operations_require_matching_step_up_and_tenant(client, monkeypatch):
    async def _checkpoints(self, run_id, limit=50):
        return [{"checkpoint_id": "redacted", "run_id": run_id}]

    monkeypatch.setattr(ArbitrationWorkflowService, "checkpoints", _checkpoints)
    route = "/api/arbitration/operations/workflows/workflow-a1/checkpoints"

    org_a = client.as_user(ORG_A_USER)
    assert org_a.get(route).status_code == 403

    wrong_token = StepUpService().create_token(
        user_id="user-org-B", action="arbitration.workflow.checkpoints"
    )
    assert org_a.get(route, headers={"x-step-up-token": wrong_token}).status_code == 403

    valid_token = StepUpService().create_token(
        user_id="user-org-A", action="arbitration.workflow.checkpoints"
    )
    accepted = org_a.get(route, headers={"x-step-up-token": valid_token})
    assert accepted.status_code == 200
    assert accepted.json()[0]["checkpoint_id"] == "redacted"

    org_b_token = StepUpService().create_token(
        user_id="user-org-B", action="arbitration.workflow.checkpoints"
    )
    assert client.as_user(ORG_B_USER).get(
        route, headers={"x-step-up-token": org_b_token}
    ).status_code == 404


def test_matrix_rows_deny_cross_tenant(client):
    same = client.as_user(ORG_A_USER).get("/api/arbitration/cases/case-a1/document-index")
    assert same.status_code == 200
    assert {row["exhibit_id"] for row in same.json()} == {"C-1"}

    assert client.as_user(ORG_B_USER).get("/api/arbitration/cases/case-a1/document-index").status_code == 404

    cross_create = client.as_user(ORG_B_USER).post(
        "/api/arbitration/cases/case-a1/document-index",
        json={"title": "Planted row", "source_type": "document", "source_id": "doc-x"},
    )
    assert cross_create.status_code == 404
    assert all(row.get("title") != "Planted row" for row in client.db.arbitration_document_index.rows)

    same_create = client.as_user(ORG_A_USER).post(
        "/api/arbitration/cases/case-a1/document-index",
        json={"title": "Org A drawing", "source_type": "document", "source_id": "doc-2"},
    )
    assert same_create.status_code == 201
    assert same_create.json()["exhibit_id"] == "C-2"


def test_agent_runs_and_exhibit_list_deny_cross_tenant(client):
    assert client.as_user(ORG_A_USER).get("/api/arbitration/cases/case-a1/agent-runs").status_code == 200
    assert client.as_user(ORG_B_USER).get("/api/arbitration/cases/case-a1/agent-runs").status_code == 404
    assert client.as_user(ORG_B_USER).get("/api/arbitration/cases/case-a1/exhibit-list").status_code == 404
    assert client.as_user(ORG_B_USER).get("/api/arbitration/cases/case-a1/readiness").status_code == 404


def test_phase5_operational_health_is_case_scoped_and_identifier_free(client):
    same = client.as_user(ORG_A_USER).get(
        "/api/arbitration/cases/case-a1/workflows/operations/health"
    )
    assert same.status_code == 200
    assert same.json()["sample"]["workflows"] == 1
    assert "workflow-a1" not in same.text
    assert client.as_user(ORG_B_USER).get(
        "/api/arbitration/cases/case-a1/workflows/operations/health"
    ).status_code == 404


def test_draft_detail_denies_cross_tenant(client):
    same = client.as_user(ORG_A_USER).get("/api/arbitration/drafts/draft-a1")
    assert same.status_code == 200
    assert same.json()["title"] == "Org A SoC"

    # Draft lookup is policy-guarded (scope check on the loaded document): 403.
    assert client.as_user(ORG_B_USER).get("/api/arbitration/drafts/draft-a1").status_code == 403


def test_case_create_rejects_foreign_organization(client):
    resp = client.as_user(ORG_B_USER).post(
        "/api/arbitration/cases",
        json={"organization_id": "org-A", "project_id": "proj-a1", "title": "Cuckoo case"},
    )
    assert resp.status_code == 403
    assert all(row.get("title") != "Cuckoo case" for row in client.db.arbitration_cases.rows)


def test_project_org_ownership_guard_denies_mismatched_case(client):
    """Pin the RBAC-hardening guard: a case whose project belongs to another org
    is denied even for a user inside the case's own organization (tampered or
    legacy data must fail closed via ScopeService.project_belongs_to_organization)."""
    client.db.arbitration_cases.rows.append(
        {
            "_id": "case-a-tampered",
            "organization_id": "org-A",
            "project_id": "proj-b1",  # belongs to org-B
            "title": "Org A case pointing at Org B project",
            "party_perspective": "claimant",
            "status": "matrix_preparation",
        }
    )

    resp = client.as_user(ORG_A_USER).get("/api/arbitration/cases/case-a-tampered")
    assert resp.status_code == 403

    # The legitimate same-org case remains accessible - the guard is targeted.
    assert client.as_user(ORG_A_USER).get("/api/arbitration/cases/case-a1").status_code == 200
