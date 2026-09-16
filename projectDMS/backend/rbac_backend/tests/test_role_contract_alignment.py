"""R-A9D owner decision 2: align the production `orgadmin` / `projectadmin` role documents
to this release's `DEFAULT_ROLES` contract, and nothing else.

Production's copied role documents predate the release definitions (the copy's
`orgadmin` stores 54 permissions and lacks `roles:*`, `organizations:update`,
`dms.task.manage`). Until R-A9B those two roles still reached `dms.task.manage` /
`dms.project.manage` through permission-alias fan-out; removing the fan-out removed
that access. The owner approved restoring it for these two roles by aligning their
documents to the release contract, and explicitly NOT for `projectuser`, whose
release definition never contained it.

The contract pinned here:

* only `orgadmin` and `projectadmin`, matched by exact `_id`;
* only additions, each of which the release definition of that role contains;
  a permission the document already holds (by name or by permission `_id`) is not
  re-added, and nothing is ever removed;
* users, other roles, deactivated roles (never reactivated) and organisation-bound
  documents are left alone; a missing document is not created;
* a dry run writes nothing; a second apply finds nothing to add and writes nothing;
* a fresh install (no role documents yet) is silent;
* it is an explicit cutover operation, never a catalogued migration: it must not run
  unreviewed on `migrate_database --apply`, and a catalogue entry would expire the
  proven fresh-install / upgrade-path evidence.
"""

from __future__ import annotations

import copy
import pathlib
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from bson import ObjectId

from rbac_backend.core.permissions import ORG_TIER_ONLY_PERMISSIONS
from rbac_backend.initial_data.default_roles import DEFAULT_ROLES
from rbac_backend.migrations import MIGRATIONS
from rbac_backend.services import role_contract_alignment as alignment

RELEASE = {role["_id"]: list(role["permissions"]) for role in DEFAULT_ROLES}


class _Roles:
    def __init__(self, docs: List[Dict[str, Any]]):
        self.docs = {doc["_id"]: copy.deepcopy(doc) for doc in docs}
        self.writes: List[tuple] = []

    async def find_one(self, query, *_args, **_kwargs):
        doc = self.docs.get(query.get("_id"))
        return copy.deepcopy(doc) if doc is not None else None

    async def update_one(self, query, update, **_kwargs):
        self.writes.append((copy.deepcopy(query), copy.deepcopy(update)))
        doc = self.docs.get(query.get("_id"))
        if doc is None or (query.get("is_active") == {"$ne": False} and doc.get("is_active") is False):
            return SimpleNamespace(matched_count=0, modified_count=0)
        doc.setdefault("permissions", [])
        pulled = update.get("$pull", {}).get("permissions")
        if pulled is not None:
            wanted = {str(value) for value in pulled["$in"]}
            doc["permissions"] = [value for value in doc["permissions"] if str(value) not in wanted]
        held = doc["permissions"]
        for value in update.get("$addToSet", {}).get("permissions", {}).get("$each", []):
            if value not in held:
                held.append(value)
        doc.update(update.get("$set", {}))
        return SimpleNamespace(matched_count=1, modified_count=1)

    def __getattr__(self, name):
        raise AssertionError(f"roles.{name} must not be called")


class _Untouchable:
    def __init__(self, name: str):
        self._name = name

    def __getattr__(self, attribute):
        raise AssertionError(f"{self._name}.{attribute} must not be called")


class _Permissions:
    def __init__(self, docs: List[Dict[str, Any]]):
        self.docs = docs

    def find(self, query, *_args, **_kwargs):
        wanted = {str(value) for value in query["_id"]["$in"]}
        matched = [dict(doc) for doc in self.docs if str(doc["_id"]) in wanted]

        class _Cursor:
            async def to_list(self, length=None):
                return matched

        return _Cursor()


def _db(roles: List[Dict[str, Any]], permissions: List[Dict[str, Any]] = ()) -> SimpleNamespace:
    return SimpleNamespace(roles=_Roles(roles), users=_Untouchable("users"), permissions=_Permissions(list(permissions)))


def _production_shaped(role_id: str, held: List[str], **fields) -> Dict[str, Any]:
    """The copied production shape: no `is_active` / `is_system` / `scope`, a display name."""
    return {"_id": role_id, "name": role_id.title(), "permissions": list(held), **fields}


def _row(result, role_id: str) -> Dict[str, Any]:
    return next(row for row in result.operations if row["role_id"] == role_id)


ORGADMIN_HELD = ["dms.document.view", "dms.project.manage", "dms.dashboard.view", "legacy:kept-outside-contract"]
PROJECTADMIN_HELD = ["dms.document.view", "tags:read"]


@pytest.fixture
def production():
    return _db(
        [
            _production_shaped("orgadmin", ORGADMIN_HELD),
            _production_shaped("projectadmin", PROJECTADMIN_HELD),
            _production_shaped("projectuser", ["dms.document.view"]),
            _production_shaped("orguser", ["dms.document.view"]),
        ]
    )


# --------------------------------------------------------------------------- #
# The plan
# --------------------------------------------------------------------------- #


def test_only_the_owner_approved_roles_are_aligned() -> None:
    assert alignment.ALIGNED_ROLE_IDS == ("orgadmin", "projectadmin")


async def test_the_additions_are_exactly_the_release_contract_minus_what_is_held(production) -> None:
    result = await alignment.align(production, dry_run=True)

    for role_id, held in (("orgadmin", ORGADMIN_HELD), ("projectadmin", PROJECTADMIN_HELD)):
        row = _row(result, role_id)
        assert row["status"] == "align"
        assert row["expected"] == sorted(set(RELEASE[role_id]))
        assert row["additions"] == sorted(set(RELEASE[role_id]) - set(held))
        assert row["before"] == sorted(set(held))
        assert row["retained_outside_contract"] == sorted(set(held) - set(RELEASE[role_id]))


async def test_no_addition_is_outside_the_release_definition_of_its_role(production) -> None:
    result = await alignment.align(production, dry_run=True)
    for row in result.operations:
        assert set(row["additions"]) <= set(RELEASE.get(row["role_id"], [])), row["role_id"]


async def test_the_owner_named_management_permissions_are_restored(production) -> None:
    result = await alignment.align(production, dry_run=True)
    assert "dms.task.manage" in _row(result, "orgadmin")["additions"]
    projectadmin = _row(result, "projectadmin")["additions"]
    assert {"dms.task.manage", "dms.project.manage"} <= set(projectadmin)
    assert not set(projectadmin) & ORG_TIER_ONLY_PERMISSIONS, "project admin gained organisation-tier authority"


async def test_projectuser_and_other_roles_are_never_touched(production) -> None:
    result = await alignment.align(production, dry_run=False)
    assert {row["role_id"] for row in result.operations} == {"orgadmin", "projectadmin"}
    assert production.roles.docs["projectuser"]["permissions"] == ["dms.document.view"]
    assert production.roles.docs["orguser"]["permissions"] == ["dms.document.view"]
    assert all(query["_id"] in alignment.ALIGNED_ROLE_IDS for query, _ in production.roles.writes)


# --------------------------------------------------------------------------- #
# Dry run, apply, re-apply
# --------------------------------------------------------------------------- #


async def test_a_dry_run_writes_nothing(production) -> None:
    before = copy.deepcopy(production.roles.docs)
    await alignment.align(production, dry_run=True)
    assert production.roles.writes == []
    assert production.roles.docs == before


async def test_apply_adds_exactly_the_additions_and_removes_nothing(production) -> None:
    result = await alignment.align(production, dry_run=False)

    for role_id, held in (("orgadmin", ORGADMIN_HELD), ("projectadmin", PROJECTADMIN_HELD)):
        stored = production.roles.docs[role_id]["permissions"]
        assert set(stored) == set(held) | set(RELEASE[role_id])
        assert stored[: len(held)] == held, "existing permissions were reordered or removed"
        assert len(stored) == len(set(stored)), "a permission was stored twice"
        row = _row(result, role_id)
        assert row["after"] == sorted(set(stored))
        assert "is_active" not in production.roles.docs[role_id], "alignment wrote lifecycle metadata"


async def test_a_second_apply_finds_nothing_and_writes_nothing(production) -> None:
    await alignment.align(production, dry_run=False)
    writes = len(production.roles.writes)
    snapshot = copy.deepcopy(production.roles.docs)

    again = await alignment.align(production, dry_run=False)

    assert len(production.roles.writes) == writes, "the second apply wrote"
    assert production.roles.docs == snapshot
    assert {row["status"] for row in again.operations} == {"aligned"}
    assert all(row["additions"] == [] for row in again.operations)
    assert again.warnings == []


async def test_users_are_never_read_or_written(production) -> None:
    await alignment.align(production, dry_run=True)
    await alignment.align(production, dry_run=False)  # `_Untouchable` raises on any access


# --------------------------------------------------------------------------- #
# Documents the alignment must leave alone
# --------------------------------------------------------------------------- #


async def test_a_deactivated_role_is_not_aligned_and_not_reactivated() -> None:
    db = _db([_production_shaped("orgadmin", ["dms.document.view"], is_active=False)])
    result = await alignment.align(db, dry_run=False)

    assert _row(result, "orgadmin")["status"] == "inactive"
    assert db.roles.writes == []
    assert db.roles.docs["orgadmin"]["is_active"] is False
    assert db.roles.docs["orgadmin"]["permissions"] == ["dms.document.view"]
    assert any("orgadmin" in warning for warning in result.warnings)


async def test_an_organisation_bound_document_is_not_aligned() -> None:
    db = _db([_production_shaped("projectadmin", [], organization_id="org-A")])
    result = await alignment.align(db, dry_run=False)

    assert _row(result, "projectadmin")["status"] == "organization_bound"
    assert db.roles.writes == []
    assert any("projectadmin" in warning for warning in result.warnings)


async def test_a_missing_document_is_not_created_and_a_fresh_install_is_silent() -> None:
    db = _db([])
    result = await alignment.align(db, dry_run=False)

    assert {row["status"] for row in result.operations} == {"absent"}
    assert db.roles.docs == {} and db.roles.writes == []
    assert result.warnings == []


async def test_a_release_seeded_database_is_already_aligned() -> None:
    db = _db([dict(role) for role in DEFAULT_ROLES])
    result = await alignment.align(db, dry_run=True)
    assert {row["status"] for row in result.operations} == {"aligned"}
    assert result.warnings == []


async def test_a_permission_stored_by_id_counts_as_held() -> None:
    task_manage = ObjectId()
    db = _db(
        [_production_shaped("orgadmin", [str(task_manage)])],
        permissions=[{"_id": task_manage, "name": "dms.task.manage"}],
    )
    result = await alignment.align(db, dry_run=True)
    assert "dms.task.manage" not in _row(result, "orgadmin")["additions"]
    assert "dms.task.manage" in _row(result, "orgadmin")["before"]


# --------------------------------------------------------------------------- #
# The evidence command
# --------------------------------------------------------------------------- #


async def test_the_report_counts_proposed_additions_and_flags_nothing_unapproved(production) -> None:
    from rbac_backend.scripts import align_role_contract as command

    rows = command.summarise((await alignment.align(production, dry_run=True)).operations)
    orgadmin = next(row for row in rows if row["role_id"] == "orgadmin")
    assert orgadmin["before_count"] == len(ORGADMIN_HELD)
    assert orgadmin["proposed_additions_count"] == len(set(RELEASE["orgadmin"]) - set(ORGADMIN_HELD))
    assert all(row["unapproved_additions"] == [] for row in rows)


def test_the_command_fails_on_an_unapproved_addition_or_a_second_pass_that_writes() -> None:
    from rbac_backend.scripts import align_role_contract as command

    clean = {"unapproved_additions_total": 0, "warnings": [], "second_apply_is_noop": True}
    assert command.exit_code(clean) == 0
    assert command.exit_code({**clean, "warnings": ["role 'orgadmin' is deactivated"]}) == 2
    assert command.exit_code({**clean, "unapproved_additions_total": 1}) == 3
    assert command.exit_code({**clean, "second_apply_is_noop": False}) == 3

    rows = command.summarise(
        [{"role_id": "projectadmin", "status": "align", "before": [], "expected": [], "additions": ["platform.admin"], "retained_outside_contract": []}]
    )
    assert rows[0]["unapproved_additions"] == ["platform.admin"], "the report stopped checking additions against the contract"


# --------------------------------------------------------------------------- #
# Catalogue
# --------------------------------------------------------------------------- #


def test_the_alignment_is_an_explicit_operation_and_never_catalogued() -> None:
    assert "role_contract_alignment" not in {migration.name for migration in MIGRATIONS}
    assert alignment.align not in {migration.upgrade for migration in MIGRATIONS}
    migrations_dir = pathlib.Path(MIGRATIONS[0].upgrade.__code__.co_filename).parent
    assert not list(migrations_dir.glob("v*role_contract*")), "the alignment was added as a migration module"


# --------------------------------------------------------------------------- #
# R-A9E owner decision F-A9D-1: explicit, bounded removals
#
# Production's `orgadmin` and `projectadmin` documents store `billing.plan.manage`,
# which gates the platform-wide plan catalogue with no tenant scope and is outside
# the release role contract. The owner approved removing exactly that permission
# from exactly those two roles, as a least-privilege cutover decision, and
# explicitly did NOT approve removing the other production-only extras: those are
# preserved and reported.
#
# A removal is a revocation, so it carries the D4-B contract: the holders' cached
# authority is announced before the write and invalidated after it, and a removal
# that cannot be announced is not made.
# --------------------------------------------------------------------------- #


BILLING_MANAGE = "billing.plan.manage"
#: Production-only extras the owner did NOT approve removing, outside BOTH role contracts.
PRESERVED_EXTRAS = ["billing.plan.view", "drafting.request.create", "drafting.final.view"]


class _Users:
    """Reads are allowed only where the contract needs holders; writes never are."""

    def __init__(self, docs: List[Dict[str, Any]]):
        self.docs = [copy.deepcopy(doc) for doc in docs]
        self.reads = 0

    def find(self, query, *_args, **_kwargs):
        self.reads += 1
        docs = [copy.deepcopy(doc) for doc in self.docs]

        class _Cursor:
            async def to_list(self, length=None):
                return docs

        return _Cursor()

    def __getattr__(self, name):
        raise AssertionError(f"users.{name} must not be called")


def _db_with_holders(
    roles: List[Dict[str, Any]],
    users: List[Dict[str, Any]],
    permissions: List[Dict[str, Any]] = (),
) -> SimpleNamespace:
    return SimpleNamespace(roles=_Roles(roles), users=_Users(users), permissions=_Permissions(list(permissions)))


@pytest.fixture
def announcements(monkeypatch):
    """Record the D4-B announcement sequence around the writes."""
    calls: List[tuple] = []

    async def begin(user_ids):
        calls.append(("begin", list(user_ids)))

    async def complete(user_ids):
        calls.append(("complete", list(user_ids)))
        return True

    async def cancel(user_ids):
        calls.append(("cancel", list(user_ids)))

    monkeypatch.setattr(alignment, "begin_authority_change", begin)
    monkeypatch.setattr(alignment, "complete_authority_change", complete)
    monkeypatch.setattr(alignment, "cancel_authority_change", cancel)
    return calls


@pytest.fixture
def production_with_extras():
    return _db_with_holders(
        [
            _production_shaped("orgadmin", [*ORGADMIN_HELD, BILLING_MANAGE, *PRESERVED_EXTRAS]),
            _production_shaped("projectadmin", [*PROJECTADMIN_HELD, BILLING_MANAGE, *PRESERVED_EXTRAS]),
            _production_shaped("projectuser", ["dms.document.view", BILLING_MANAGE]),
            _production_shaped("orguser", ["dms.document.view", BILLING_MANAGE]),
        ],
        [
            {"_id": "u-org", "roles": ["orgadmin"]},
            {"_id": "u-org-legacy", "roles": ["organization-admin"]},
            {"_id": "u-proj", "roles": ["project-admin"]},
            {"_id": "u-other", "roles": ["projectuser"]},
        ],
    )


def test_the_owner_approved_removals_are_exactly_billing_plan_manage_for_two_roles() -> None:
    assert alignment.OWNER_APPROVED_REMOVALS == {
        "orgadmin": (BILLING_MANAGE,),
        "projectadmin": (BILLING_MANAGE,),
    }
    assert set(alignment.OWNER_APPROVED_REMOVALS) <= set(alignment.ALIGNED_ROLE_IDS)


def test_no_owner_approved_removal_is_inside_the_release_contract_of_its_role() -> None:
    for role_id, removals in alignment.OWNER_APPROVED_REMOVALS.items():
        assert not set(removals) & set(alignment.release_contract(role_id)), role_id


async def test_the_plan_classifies_additions_owner_removals_and_preserved_extras(
    production_with_extras,
) -> None:
    result = await alignment.align(production_with_extras, dry_run=True)

    for role_id, held in (("orgadmin", ORGADMIN_HELD), ("projectadmin", PROJECTADMIN_HELD)):
        row = _row(result, role_id)
        assert row["status"] == "align"
        assert row["removals"] == [BILLING_MANAGE]
        assert row["unapproved_removals"] == []
        assert row["additions"] == sorted(set(RELEASE[role_id]) - set(held) - set(PRESERVED_EXTRAS))
        assert BILLING_MANAGE not in row["retained_outside_contract"], "an approved removal was reported as kept"
        assert set(PRESERVED_EXTRAS) <= set(row["retained_outside_contract"])


async def test_apply_removes_only_the_approved_permission_and_preserves_the_other_extras(
    production_with_extras, announcements
) -> None:
    result = await alignment.align(production_with_extras, dry_run=False)

    for role_id in alignment.ALIGNED_ROLE_IDS:
        stored = production_with_extras.roles.docs[role_id]["permissions"]
        assert BILLING_MANAGE not in stored
        assert set(PRESERVED_EXTRAS) <= set(stored), "an unapproved extra was removed"
        assert set(RELEASE[role_id]) <= set(stored), "the canonical additions were not applied"
        assert _row(result, role_id)["after"] == sorted(set(stored))
    assert result.warnings == []


async def test_a_dry_run_removes_nothing_and_announces_nothing(production_with_extras, announcements) -> None:
    before = copy.deepcopy(production_with_extras.roles.docs)
    await alignment.align(production_with_extras, dry_run=True)

    assert production_with_extras.roles.writes == []
    assert production_with_extras.roles.docs == before
    assert announcements == []


async def test_a_permission_stored_by_id_is_removed_by_its_stored_token(announcements) -> None:
    billing = ObjectId()
    db = _db_with_holders(
        [_production_shaped("orgadmin", [str(billing), "dms.document.view"])],
        [{"_id": "u-org", "roles": ["orgadmin"]}],
        permissions=[{"_id": billing, "name": BILLING_MANAGE}],
    )

    result = await alignment.align(db, dry_run=False)

    assert _row(result, "orgadmin")["removals"] == [BILLING_MANAGE]
    assert str(billing) not in [str(value) for value in db.roles.docs["orgadmin"]["permissions"]]


async def test_a_second_apply_after_a_removal_finds_nothing_and_writes_nothing(
    production_with_extras, announcements
) -> None:
    await alignment.align(production_with_extras, dry_run=False)
    writes = len(production_with_extras.roles.writes)
    snapshot = copy.deepcopy(production_with_extras.roles.docs)

    again = await alignment.align(production_with_extras, dry_run=False)

    assert len(production_with_extras.roles.writes) == writes, "the second apply wrote"
    assert production_with_extras.roles.docs == snapshot
    assert {row["status"] for row in again.operations} == {"aligned"}
    assert all(row["removals"] == [] and row["additions"] == [] for row in again.operations)


async def test_a_role_that_does_not_store_the_permission_is_not_pulled(production, announcements) -> None:
    result = await alignment.align(production, dry_run=False)

    assert all(row["removals"] == [] for row in result.operations)
    assert all("$pull" not in update for _, update in production.roles.writes)
    assert announcements == [], "an addition-only alignment announced an authority change"


async def test_roles_outside_the_owner_decision_never_lose_a_permission(
    production_with_extras, announcements
) -> None:
    await alignment.align(production_with_extras, dry_run=False)

    for role_id in ("projectuser", "orguser"):
        assert production_with_extras.roles.docs[role_id]["permissions"] == ["dms.document.view", BILLING_MANAGE]


# --------------------------------------------------------------------------- #
# A removal is a revocation: the D4-B contract applies to it
# --------------------------------------------------------------------------- #


async def test_a_removal_announces_its_holders_before_the_write_and_invalidates_after(
    production_with_extras, announcements
) -> None:
    await alignment.align(production_with_extras, dry_run=False)

    assert announcements, "a revocation was written without announcing it"
    assert announcements[0][0] == "begin"
    assert {"u-org", "u-org-legacy"} <= set(announcements[0][1]), "a legacy-spelling holder was not announced"
    assert "u-other" not in announcements[0][1], "a user who does not hold the role was announced"
    assert [name for name, _ in announcements].count("complete") == len(alignment.OWNER_APPROVED_REMOVALS)
    assert announcements[-1][0] == "complete"
    assert any("u-proj" in ids for name, ids in announcements if name == "begin"), "project-admin holder missed"


async def test_a_removal_that_cannot_be_announced_is_not_made(production_with_extras, monkeypatch) -> None:
    from rbac_backend.services.permission_service import AuthorityChangeUnavailableError

    async def refuse(user_ids):
        raise AuthorityChangeUnavailableError("Authorization cache unavailable")

    monkeypatch.setattr(alignment, "begin_authority_change", refuse)
    before = copy.deepcopy(production_with_extras.roles.docs)

    result = await alignment.align(production_with_extras, dry_run=False)

    assert production_with_extras.roles.writes == [], "the role was changed without a working cache"
    assert production_with_extras.roles.docs == before
    assert len(result.warnings) == len(alignment.ALIGNED_ROLE_IDS)
    assert all("cache" in warning.lower() for warning in result.warnings)


async def test_a_removal_is_not_made_when_its_holders_cannot_be_read(
    production_with_extras, announcements
) -> None:
    def explode(*_args, **_kwargs):
        raise RuntimeError("users unavailable")

    production_with_extras.users.find = explode
    before = copy.deepcopy(production_with_extras.roles.docs)

    result = await alignment.align(production_with_extras, dry_run=False)

    assert production_with_extras.roles.writes == []
    assert production_with_extras.roles.docs == before
    assert result.warnings and all("holders" in warning or "cache" in warning for warning in result.warnings)


# --------------------------------------------------------------------------- #
# The evidence command
# --------------------------------------------------------------------------- #


async def test_the_report_carries_the_removal_classification(production_with_extras) -> None:
    from rbac_backend.scripts import align_role_contract as command

    rows = command.summarise((await alignment.align(production_with_extras, dry_run=True)).operations)
    orgadmin = next(row for row in rows if row["role_id"] == "orgadmin")

    assert orgadmin["proposed_removals"] == [BILLING_MANAGE]
    assert orgadmin["proposed_removals_count"] == 1
    assert orgadmin["unapproved_removals"] == []
    assert set(PRESERVED_EXTRAS) <= set(orgadmin["retained_outside_contract"])


def test_the_command_fails_on_a_removal_outside_the_owner_decision() -> None:
    from rbac_backend.scripts import align_role_contract as command

    clean = {
        "unapproved_additions_total": 0,
        "unapproved_removals_total": 0,
        "warnings": [],
        "second_apply_is_noop": True,
    }
    assert command.exit_code(clean) == 0
    assert command.exit_code({**clean, "unapproved_removals_total": 1}) == 3

    rows = command.summarise(
        [
            {
                "role_id": "orgadmin",
                "status": "align",
                "before": [],
                "expected": [],
                "additions": [],
                "removals": ["dms.document.view"],
                "unapproved_removals": ["dms.document.view"],
                "retained_outside_contract": [],
            }
        ]
    )
    assert rows[0]["unapproved_removals"] == ["dms.document.view"]
