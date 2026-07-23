"""Approval workflow (Phase 4 / Module 3): state machine + no-self-approval gate."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from rbac_backend.routers.claims import approve_claim
from rbac_backend.models.approval import DecisionBody
from rbac_backend.services.approval_service import ApprovalError, ApprovalService
from rbac_backend.services.policy_service import PolicyService
from rbac_backend.services.scope_service import ScopeService


# --- fakes ----------------------------------------------------------------


class _Approvals:
    def __init__(self):
        self.docs = {}

    async def find_one(self, query):
        if "_id" in query:
            d = self.docs.get(query["_id"])
            return dict(d) if d else None
        for d in self.docs.values():
            if all(d.get(k) == v for k, v in query.items()):
                return dict(d)
        return None

    async def insert_one(self, doc):
        self.docs[doc["_id"]] = dict(doc)
        return SimpleNamespace(inserted_id=doc["_id"])

    async def find_one_and_update(self, query, update, return_document=True):
        d = self.docs.get(query.get("_id"))
        if not d:
            return None
        d.update(update.get("$set", {}))
        for k, v in update.get("$push", {}).items():
            d.setdefault(k, []).append(v)
        self.docs[d["_id"]] = d
        return dict(d)


class _Claims:
    def __init__(self, claim):
        self._claim = claim

    async def find_one(self, query):
        return dict(self._claim) if query.get("_id") == self._claim["_id"] else None


class _DB:
    def __init__(self, claim=None):
        self.approvals = _Approvals()
        if claim is not None:
            self.claims = _Claims(claim)


class _ScopeCursor:
    async def to_list(self, length=None):
        return []


class _ScopeColl:
    def find(self, *_a, **_k):
        return _ScopeCursor()


class _Projects:
    async def find_one(self, _query):
        return {"_id": "proj-A", "organization_id": "org-A"}


class _ScopeDB:
    organization_memberships = _ScopeColl()
    project_memberships = _ScopeColl()
    projects = _Projects()


class _Allow:
    async def user_has_permission(self, *_a, **_k):
        return True


class _EntAllow:
    async def check_permission_entitlement(self, **_k):
        return True, "ok"


class _Audit:
    async def emit(self, **_k):
        return None


def _policy():
    return PolicyService(
        permission_service=_Allow(),
        scope_service=ScopeService(db=_ScopeDB()),
        entitlement_service=_EntAllow(),
        audit_service=_Audit(),
    )


def _user(uid="mgr", org="org-A"):
    return SimpleNamespace(
        id=uid, roles=["orgadmin"], organization_id=org,
        organizations=[org], projects=["proj-A"], account_type="client_user",
    )


# --- state machine --------------------------------------------------------


@pytest.mark.asyncio
async def test_happy_path_assign_submit_approve():
    svc = ApprovalService(_DB())
    rec = await svc.get_or_create("claim", "c1", organization_id="org-A", drafter_id="drafter")
    rec = await svc.assign(rec, "rev1", "mgr")
    assert rec["state"] == "assigned" and rec["reviewer_id"] == "rev1"
    rec = await svc.submit_for_review(rec, "drafter")
    assert rec["state"] == "in_review"
    rec = await svc.approve(rec, "mgr", comment="ok")
    assert rec["state"] == "approved" and rec["decided_by"] == "mgr"
    assert [e["action"] for e in rec["history"]] == ["assigned", "submitted", "approved"]


@pytest.mark.asyncio
async def test_drafter_cannot_self_approve():
    svc = ApprovalService(_DB())
    rec = await svc.get_or_create("claim", "c2", drafter_id="drafter")
    rec = await svc.assign(rec, "drafter", "mgr")
    rec = await svc.submit_for_review(rec, "drafter")
    with pytest.raises(ApprovalError):
        await svc.approve(rec, "drafter")


@pytest.mark.asyncio
async def test_cannot_approve_before_review():
    svc = ApprovalService(_DB())
    rec = await svc.get_or_create("claim", "c3", drafter_id="drafter")
    with pytest.raises(ApprovalError):
        await svc.approve(rec, "mgr")


@pytest.mark.asyncio
async def test_return_then_resubmit():
    svc = ApprovalService(_DB())
    rec = await svc.get_or_create("claim", "c4", drafter_id="drafter")
    rec = await svc.assign(rec, "rev1", "mgr")
    rec = await svc.submit_for_review(rec, "drafter")
    rec = await svc.return_for_changes(rec, "mgr", comment="add clause refs")
    assert rec["state"] == "returned" and rec["decision_comment"] == "add clause refs"
    rec = await svc.submit_for_review(rec, "drafter")
    assert rec["state"] == "in_review"


# --- router self-approval gate (surfaces as 409) --------------------------


@pytest.mark.asyncio
async def test_approve_endpoint_blocks_self_approval():
    claim = {"_id": "c5", "organization_id": "org-A", "project_id": "proj-A", "created_by": "drafter"}
    db = _DB(claim)
    svc = ApprovalService(db)
    rec = await svc.get_or_create("claim", "c5", drafter_id="drafter")
    rec = await svc.assign(rec, "drafter", "mgr")
    await svc.submit_for_review(rec, "drafter")

    # The drafter (created_by) tries to approve their own claim → 409.
    with pytest.raises(HTTPException) as exc:
        await approve_claim(
            "c5", DecisionBody(comment="self"), db=db,
            current_user=_user(uid="drafter"), policy=_policy(),
        )
    assert exc.value.status_code == 409
