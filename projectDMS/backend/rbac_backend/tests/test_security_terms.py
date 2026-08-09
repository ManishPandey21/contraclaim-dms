from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from rbac_backend.models.security_terms import SecurityTermsAcceptRequest, SecurityTermsVersionCreate
from rbac_backend.services.security_terms_service import SecurityTermsService


class _Cursor:
    def __init__(self, docs):
        self._docs = list(docs)

    def sort(self, key, direction=1):
        reverse = direction == -1
        self._docs = sorted(self._docs, key=lambda doc: doc.get(key) or datetime.min, reverse=reverse)
        return self

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class _Coll:
    def __init__(self):
        self.docs = {}

    async def insert_one(self, doc):
        doc = dict(doc)
        doc.setdefault("_id", f"auto-{len(self.docs) + 1}")
        if doc["_id"] in self.docs:
            # Mongo enforces _id uniqueness; the fake must too or key-collision
            # regressions pass here and 500 in production.
            raise DuplicateKeyError(f"E11000 duplicate key error: _id {doc['_id']}")
        self.docs[doc["_id"]] = dict(doc)
        return SimpleNamespace(inserted_id=doc["_id"])

    async def find_one(self, query):
        for doc in self.docs.values():
            if self._match(doc, query):
                return dict(doc)
        return None

    def find(self, query=None):
        query = query or {}
        return _Cursor([dict(doc) for doc in self.docs.values() if self._match(doc, query)])

    async def update_one(self, query, update):
        for key, doc in self.docs.items():
            if self._match(doc, query):
                doc.update(update.get("$set", {}))
                self.docs[key] = doc
                return SimpleNamespace(modified_count=1)
        return SimpleNamespace(modified_count=0)

    async def update_many(self, query, update):
        count = 0
        for key, doc in list(self.docs.items()):
            if self._match(doc, query):
                doc.update(update.get("$set", {}))
                self.docs[key] = doc
                count += 1
        return SimpleNamespace(modified_count=count)

    @staticmethod
    def _match(doc, query):
        for key, value in (query or {}).items():
            if doc.get(key) != value:
                return False
        return True


class _DB:
    def __init__(self):
        self.terms_versions = _Coll()
        self.security_terms_acceptances = _Coll()
        self.audit_events = _Coll()


def _user(user_id="u1", org_id="org-A", roles=None):
    return SimpleNamespace(
        id=user_id,
        email=f"{user_id}@example.com",
        roles=roles or ["orgadmin"],
        organization_id=org_id,
    )


@pytest.mark.asyncio
async def test_default_active_terms_are_created_and_require_acceptance():
    db = _DB()
    status = await SecurityTermsService(db).status_for_user(_user())

    assert status["requires_acceptance"] is True
    assert status["active_version"]["version"] == "2026.1"
    assert status["active_version"]["terms_hash"]
    assert len(db.terms_versions.docs) == 1


@pytest.mark.asyncio
async def test_acceptance_records_user_org_version_hash_ip_user_agent_and_method():
    db = _DB()
    svc = SecurityTermsService(db)

    accepted = await svc.accept_active(
        _user(),
        SecurityTermsAcceptRequest(accepted=True, acceptance_method="checkbox_accept_continue"),
        ip_address="203.0.113.7",
        user_agent="pytest-browser",
    )
    status = await svc.status_for_user(_user())

    assert status["requires_acceptance"] is False
    assert accepted["user_id"] == "u1"
    assert accepted["org_id"] == "org-A"
    assert accepted["terms_version"] == "2026.1"
    assert accepted["ip_address"] == "203.0.113.7"
    assert accepted["user_agent"] == "pytest-browser"
    assert accepted["terms_hash"] == status["active_version"]["terms_hash"]
    assert accepted["acceptance_method"] == "checkbox_accept_continue"
    assert any(event["action"] == "security_terms.accepted" for event in db.audit_events.docs.values())


@pytest.mark.asyncio
async def test_new_active_terms_version_requires_acceptance_again():
    db = _DB()
    svc = SecurityTermsService(db)
    user = _user()
    await svc.accept_active(user, SecurityTermsAcceptRequest(accepted=True), ip_address="127.0.0.1", user_agent="test")
    assert (await svc.status_for_user(user))["requires_acceptance"] is False

    new_version = await svc.create_version(
        SecurityTermsVersionCreate(
            version="2026.2",
            title="Updated Terms",
            body="Updated mandatory security privacy anti-piracy terms body for all users.",
            effective_date=datetime(2026, 7, 1),
            is_active=True,
        ),
        _user(user_id="admin", roles=["superadmin"]),
    )
    status = await svc.status_for_user(user)

    assert new_version["is_active"] is True
    assert status["active_version"]["version"] == "2026.2"
    assert status["requires_acceptance"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("second_org", [None, "org-B"])
async def test_accepting_again_under_a_different_org_scope_does_not_collide(second_org):
    """Acceptance is per (user, org, version, hash), so a second org scope inserts a new row.

    The row key used to omit ``org_id`` while the lookup included it, so this
    path missed the existing row and then collided on ``_id`` — surfacing as an
    opaque 500 and a "Failed to accept security terms" toast.
    """
    db = _DB()
    svc = SecurityTermsService(db)
    await svc.accept_active(
        _user(org_id="org-A"), SecurityTermsAcceptRequest(accepted=True), ip_address=None, user_agent=None
    )

    second_user = _user(org_id=second_org)
    accepted = await svc.accept_active(
        second_user, SecurityTermsAcceptRequest(accepted=True), ip_address=None, user_agent=None
    )

    assert accepted["org_id"] == second_org
    assert (await svc.status_for_user(second_user))["requires_acceptance"] is False
    # The org-A acceptance must survive as its own row.
    assert (await svc.status_for_user(_user(org_id="org-A")))["requires_acceptance"] is False
    assert len(db.security_terms_acceptances.docs) == 2


@pytest.mark.asyncio
async def test_concurrent_acceptance_insert_is_idempotent_not_a_500():
    """A racing insert must record acceptance, not raise DuplicateKeyError."""
    db = _DB()
    svc = SecurityTermsService(db)
    user = _user()
    active = await svc.active_version()
    # Simulate the row landing between this request's lookup and its insert.
    row_id = SecurityTermsService._acceptance_id(
        user_id="u1", org_id="org-A", version=active["version"], terms_hash=active["terms_hash"]
    )
    db.security_terms_acceptances.docs[row_id] = {
        "_id": row_id,
        "user_id": "u1",
        "org_id": "org-A",
        "terms_version": active["version"],
        "terms_hash": active["terms_hash"],
        "accepted_at": datetime(2026, 1, 1),
        "acceptance_method": "checkbox_accept_continue",
    }
    original_find_one = db.security_terms_acceptances.find_one

    async def _find_one_missing_once(query):
        db.security_terms_acceptances.find_one = original_find_one
        return None

    db.security_terms_acceptances.find_one = _find_one_missing_once

    accepted = await svc.accept_active(
        user, SecurityTermsAcceptRequest(accepted=True), ip_address="203.0.113.7", user_agent="pytest"
    )

    assert accepted["terms_version"] == active["version"]
    assert (await svc.status_for_user(user))["requires_acceptance"] is False
    assert len(db.security_terms_acceptances.docs) == 1
    assert db.security_terms_acceptances.docs[row_id]["ip_address"] == "203.0.113.7"


@pytest.mark.asyncio
async def test_rejects_acceptance_without_checkbox_confirmation():
    db = _DB()
    svc = SecurityTermsService(db)

    with pytest.raises(HTTPException) as exc:
        await svc.accept_active(_user(), SecurityTermsAcceptRequest(accepted=False), ip_address=None, user_agent=None)

    assert exc.value.status_code == 400
