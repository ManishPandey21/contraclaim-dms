"""IPC category master: default seeding + org/project override resolution."""

from types import SimpleNamespace

import pytest

from rbac_backend.models.ipc_category import IPCCategoryCreate
from rbac_backend.services.ipc_category_service import IPCCategoryService


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


def _match(doc, query):
    for k, v in query.items():
        if isinstance(v, dict) and "$in" in v:
            if doc.get(k) not in v["$in"]:
                return False
        elif doc.get(k) != v:
            return False
    return True


class _Coll:
    def __init__(self):
        self.docs = {}

    async def count_documents(self, query):
        return sum(1 for d in self.docs.values() if _match(d, query))

    async def insert_one(self, doc):
        self.docs[doc["_id"]] = dict(doc)
        return SimpleNamespace(inserted_id=doc["_id"])

    async def insert_many(self, docs, ordered=False):
        for d in docs:
            self.docs[d["_id"]] = dict(d)
        return SimpleNamespace(inserted_ids=[d["_id"] for d in docs])

    async def find_one(self, query):
        for d in self.docs.values():
            if _match(d, query):
                return dict(d)
        return None

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
        return _Cursor([dict(d) for d in self.docs.values() if _match(d, query)])


class _DB:
    def __init__(self):
        self.ipc_categories = _Coll()


def _user(org="org-A"):
    return SimpleNamespace(id="u1", roles=["orgadmin"], organization_id=org)


@pytest.mark.asyncio
async def test_resolve_seeds_org_defaults_once():
    db = _DB()
    svc = IPCCategoryService(db)
    advances = await svc.resolve("org-A", kind="advance")
    codes = {c["code"] for c in advances}
    assert {"mobilisation_advance", "material_advance", "plant_advance",
            "price_escalation", "work_done_payment", "advance_against_work_done"} <= codes
    deductions = await svc.resolve("org-A", kind="deduction")
    assert {c["code"] for c in deductions} == {"income_tax", "labour_cess"}
    # Idempotent: a second resolve does not duplicate the seeded rows.
    total = await db.ipc_categories.count_documents({"organization_id": "org-A"})
    await svc.resolve("org-A")
    assert await db.ipc_categories.count_documents({"organization_id": "org-A"}) == total


@pytest.mark.asyncio
async def test_project_override_wins_over_org_entry():
    db = _DB()
    svc = IPCCategoryService(db)
    await svc.resolve("org-A")  # seed org defaults
    # Project-level override for the same code, plus a project-only addition.
    await svc.create(IPCCategoryCreate(
        kind="deduction", code="labour_cess", name="Labour Cess (1%)",
        organization_id="org-A", project_id="proj-1"), _user())
    await svc.create(IPCCategoryCreate(
        kind="deduction", code="retention", name="Retention",
        organization_id="org-A", project_id="proj-1"), _user())

    merged = await svc.resolve("org-A", project_id="proj-1", kind="deduction")
    by_code = {c["code"]: c for c in merged}
    assert by_code["labour_cess"]["name"] == "Labour Cess (1%)"   # project wins
    assert by_code["labour_cess"]["project_id"] == "proj-1"
    assert "retention" in by_code                                  # project addition
    assert by_code["income_tax"]["project_id"] is None            # untouched org default

    # A different project does not see proj-1's overrides.
    other = await svc.resolve("org-A", project_id="proj-2", kind="deduction")
    assert {c["code"]: c["name"] for c in other}["labour_cess"] == "Labour Cess"


@pytest.mark.asyncio
async def test_inactive_hidden_from_resolve_but_shown_in_manage():
    db = _DB()
    svc = IPCCategoryService(db)
    created = await svc.create(IPCCategoryCreate(
        kind="advance", code="plant_advance_extra", name="Plant Advance (extra)",
        organization_id="org-A", project_id=None), _user())
    await svc.update(created, {"active": False}, _user())
    codes = {c["code"] for c in await svc.resolve("org-A", kind="advance")}
    assert "plant_advance_extra" not in codes
    manage_codes = {c["code"] for c in await svc.list_manage("org-A")}
    assert "plant_advance_extra" in manage_codes


@pytest.mark.asyncio
async def test_duplicate_code_in_same_scope_rejected():
    db = _DB()
    svc = IPCCategoryService(db)
    await svc.resolve("org-A")  # seeds income_tax at org level
    with pytest.raises(ValueError):
        await svc.create(IPCCategoryCreate(
            kind="deduction", code="income_tax", name="Income Tax dup",
            organization_id="org-A", project_id=None), _user())
