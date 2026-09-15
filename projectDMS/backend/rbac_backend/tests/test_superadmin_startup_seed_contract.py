"""What the startup seeder does - and does not do - to the Super Admin role document.

Startup runs exactly one role seeder: `ensure_permission_catalog_and_superadmin`
(`main.startup_event`). ADR 0001 makes Super Admin authority name-based, so the
role document matters only through three facts, each pinned here against the
production shape measured on 2026-09-15 (`{_id: "superadmin", name: "Super Admin",
permissions: [...]}`, with no `is_system`, `is_active` or `scope`):

* it carries the whole permission catalogue after startup (display and direct
  permission readers depend on it);
* startup never reactivates a deactivated document - deactivation is the lockout
  lever ADR 0001 records, and a seeder silently undoing it would void it;
* startup never creates a missing document (`update_one`, no upsert), so the
  pre-deploy `system_role_audit.py` gate has to catch a missing one.

It also records, rather than fixes, that startup leaves `is_system` and `scope`
absent: normalising that metadata is a post-cutover item. When that lands, this
test is the one to update.

The "does not" tests each carry a positive control proving the seeder ran in the
same test; without one, a seeder that silently did nothing would pass them.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Dict, List

from rbac_backend.models.permission import DEFAULT_PERMISSIONS
from rbac_backend.services.data_initialization import ensure_permission_catalog_and_superadmin
from rbac_backend.services.permission_service import role_is_active

CATALOGUE = [p["name"] for p in DEFAULT_PERMISSIONS if p.get("name")]


class _Collection:
    def __init__(self, docs: List[Dict[str, Any]]):
        self.docs = docs
        self.reads = 0
        self.update_attempts = 0

    async def find_one(self, query: Dict[str, Any], *_args, **_kwargs):
        self.reads += 1
        for doc in self.docs:
            if all(doc.get(key) == value for key, value in query.items()):
                return dict(doc)
        return None

    async def insert_one(self, doc: Dict[str, Any]):
        self.docs.append(dict(doc))
        return SimpleNamespace(inserted_id=doc.get("_id"))

    async def update_one(self, query: Dict[str, Any], update: Dict[str, Any], upsert: bool = False):
        self.update_attempts += 1
        assert not upsert, "the startup seeder now upserts; re-pin the missing-document contract"
        for doc in self.docs:
            if all(doc.get(key) == value for key, value in query.items()):
                doc.update(update.get("$set", {}))
                for field, spec in (update.get("$addToSet") or {}).items():
                    current = list(doc.get(field) or [])
                    for value in spec["$each"] if isinstance(spec, dict) else [spec]:
                        if value not in current:
                            current.append(value)
                    doc[field] = current
                return SimpleNamespace(matched_count=1, modified_count=1)
        return SimpleNamespace(matched_count=0, modified_count=0)


def _database(roles: List[Dict[str, Any]]) -> SimpleNamespace:
    # The catalogue is already present, so the permission half of the seeder only reads.
    permissions = [{**p, "_id": f"perm-{i}", "is_active": True} for i, p in enumerate(DEFAULT_PERMISSIONS)]
    return SimpleNamespace(roles=_Collection(roles), permissions=_Collection(permissions))


def _seed(db: SimpleNamespace) -> None:
    asyncio.run(ensure_permission_catalog_and_superadmin(db))


def _superadmin(db: SimpleNamespace):
    return next((doc for doc in db.roles.docs if doc.get("_id") == "superadmin"), None)


def _missing_catalogue(doc: Dict[str, Any]) -> List[str]:
    return [name for name in CATALOGUE if name not in (doc.get("permissions") or [])]


def test_a_production_shaped_superadmin_document_holds_the_whole_catalogue_after_startup() -> None:
    db = _database([{"_id": "superadmin", "name": "Super Admin", "permissions": ["dms.document.view", "legacy:keep"]}])
    assert _missing_catalogue(_superadmin(db)), "precondition: the document starts short of the catalogue"

    _seed(db)

    doc = _superadmin(db)
    assert doc is not None
    assert _missing_catalogue(doc) == [], "catalogue permissions missing"
    assert "legacy:keep" in doc["permissions"], "the seeder removed a manual grant"
    assert role_is_active(doc), "a document without the lifecycle flag must read as active"


def test_startup_leaves_the_superadmin_metadata_fields_absent() -> None:
    """Recorded, not endorsed: normalising `is_system`/`scope`/`is_active` is post-cutover work."""
    db = _database([{"_id": "superadmin", "name": "Super Admin", "permissions": []}])

    _seed(db)

    doc = _superadmin(db)
    assert _missing_catalogue(doc) == [], "positive control: the seeder ran and granted the catalogue"
    assert {"is_system", "scope", "is_active"}.isdisjoint(doc), (
        "startup now writes Super Admin metadata; update this contract and the post-cutover item"
    )


def test_startup_never_reactivates_a_deactivated_superadmin_document() -> None:
    db = _database([{"_id": "superadmin", "name": "Super Admin", "permissions": [], "is_active": False}])

    _seed(db)

    doc = _superadmin(db)
    assert _missing_catalogue(doc) == [], "positive control: the seeder ran against this document"
    assert doc["is_active"] is False, "the startup seeder undid a Super Admin deactivation"
    assert not role_is_active(doc)


def test_startup_never_creates_a_missing_superadmin_document() -> None:
    db = _database([{"_id": "orgadmin", "name": "Organization Admin", "permissions": []}])

    _seed(db)

    assert db.permissions.reads >= len(CATALOGUE), "positive control: the seeder walked the catalogue"
    assert db.roles.update_attempts == 1, "positive control: the seeder attempted the Super Admin grant"
    assert _superadmin(db) is None, (
        "startup now creates the Super Admin document; the missing-document case in ADR 0001 changed"
    )
