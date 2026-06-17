"""Claim register (Phase 4 / Module 1): service behaviour + cross-tenant gate."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from rbac_backend.models.claim import ClaimCreate
from rbac_backend.routers.claims import create_claim
from rbac_backend.services.claim_service import ClaimService
from rbac_backend.services.policy_service import PolicyService
from rbac_backend.services.scope_service import ScopeService


# --- fakes ----------------------------------------------------------------


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, *_a, **_k):
        return self

    def skip(self, n):
        self._docs = self._docs[n:]
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class _Claims:
    def __init__(self):
        self.docs = {}

    async def insert_one(self, doc):
        _id = doc.get("_id") or f"c{len(self.docs) + 1}"
        doc = dict(doc)
        doc["_id"] = _id
        self.docs[_id] = doc
        return SimpleNamespace(inserted_id=_id)

    async def find_one(self, query):
        d = self.docs.get(query.get("_id"))
        return dict(d) if d else None

    async def find_one_and_update(self, query, update, return_document=True):
        d = self.docs.get(query.get("_id"))
        if not d:
            return None
        d.update(update.get("$set", {}))
        return dict(d)

    async def delete_one(self, query):
        existed = query.get("_id") in self.docs
        self.docs.pop(query.get("_id"), None)
        return SimpleNamespace(deleted_count=1 if existed else 0)

    def find(self, query):
        scalar = {k: v for k, v in query.items() if not isinstance(v, dict)}
        matched = [d for d in self.docs.values() if all(d.get(k) == v for k, v in scalar.items())]
        return _Cursor(matched)


class _DB:
    def __init__(self):
        self.claims = _Claims()


class _ScopeCursor:
    async def to_list(self, length=None):
        return []


class _ScopeColl:
    def find(self, *_a, **_k):
        return _ScopeCursor()


class _ScopeDB:
    organization_memberships = _ScopeColl()
    project_memberships = _ScopeColl()


class _PermAllow:
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
        permission_service=_PermAllow(),
        scope_service=ScopeService(db=_ScopeDB()),
        entitlement_service=_EntAllow(),
        audit_service=_Audit(),
    )


def _user(org="org-A", roles=("orguser",), projects=("proj-A",)):
    return SimpleNamespace(
        id="u1", roles=list(roles), organization_id=org,
        organizations=[org] if org else [], projects=list(projects), account_type="client_user",
    )


# --- service --------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_defaults_org_and_metadata():
    svc = ClaimService(_DB())
    claim = await svc.create(ClaimCreate(title="EOT — monsoon delay", type="eot", project_id="proj-A"), _user(org="org-A"))
    assert claim["organization_id"] == "org-A"
    assert claim["project_id"] == "proj-A"
    assert claim["created_by"] == "u1"
    assert claim["type"] == "eot"
    assert claim["status"] == "draft"


@pytest.mark.asyncio
async def test_list_applies_scope_and_type_filter():
    db = _DB()
    svc = ClaimService(db)
    await svc.create(ClaimCreate(title="A", type="eot"), _user())
    await svc.create(ClaimCreate(title="B", type="variation"), _user())
    eot = await svc.list({"organization_id": "org-A"}, claim_type="eot")
    assert len(eot) == 1 and eot[0]["type"] == "eot"
    # a foreign-org scope filter returns nothing
    assert await svc.list({"organization_id": "org-B"}) == []


@pytest.mark.asyncio
async def test_update_and_delete():
    db = _DB()
    svc = ClaimService(db)
    claim = await svc.create(ClaimCreate(title="A", type="eot"), _user())
    updated = await svc.update(claim["_id"], {"status": "submitted", "amount_claimed": 100000.0}, _user(), before=claim)
    assert updated["status"] == "submitted"
    assert updated["updated_by"] == "u1"
    assert await svc.delete(claim["_id"], _user(), before=claim) is True


# --- cross-tenant gate ----------------------------------------------------


@pytest.mark.asyncio
async def test_create_claim_denies_cross_tenant():
    payload = ClaimCreate(title="X", organization_id="org-B", project_id="proj-B")
    with pytest.raises(HTTPException) as exc:
        await create_claim(payload, db=_DB(), current_user=_user(org="org-A"), policy=_policy())
    assert exc.value.status_code == 403
