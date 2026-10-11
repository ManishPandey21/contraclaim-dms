"""Align the canonical `orgadmin` / `projectadmin` role documents to this release.

Production's role documents predate the release `DEFAULT_ROLES`: the startup seeder
only refreshes `superadmin`, so the copied `orgadmin` stores 54 permissions and
lacks `roles:*`, `organizations:update` and `dms.task.manage`. Until R-A9B those
roles still reached `dms.task.manage` / `dms.project.manage` through permission-alias
fan-out; closing the fan-out removed that access. Owner decision 2 (R-A9D): the
release definitions of these two roles are the intended contract, so their documents
are brought to it - and no other role's (`projectuser` keeps what it stores; its
release definition never held task or project management).

This is an explicit cutover OPERATION, deliberately not a catalogued migration: a
catalogue entry would run on every `migrate_database --apply` without the owner
reviewing the diff, and would expire the fresh-install and upgrade-path evidence
(`test_gate6_fresh_install_evidence.py` pins the catalogue that was proven). It runs
through `python -m rbac_backend.scripts.align_role_contract` (inspect by default).

Every permission the operation looks at falls into exactly one of three classes, and
the report names all three:

A. **canonical additions** - in the release definition of the role, not held;
B. **owner-approved removals** - held, outside the release definition, and listed in
   `OWNER_APPROVED_REMOVALS` (owner decision F-A9D-1, R-A9E: `billing.plan.manage`
   for `orgadmin` and `projectadmin`, because it gates the platform-wide plan
   catalogue with no tenant scope);
C. **everything else the document holds outside the release contract** - PRESERVED
   and reported, never removed. The owner decision is deliberately not generalised
   into "remove every production-only permission".

What it does, and deliberately does not:

* only the documents whose `_id` is exactly `orgadmin` or `projectadmin`;
* only `$addToSet` of class-A permissions, and `$pull` of class-B permissions - a
  permission the document already holds (by name, or by a stored permission `_id`)
  is not re-added, and a removal that the release definition of the role contains is
  refused, not executed;
* never writes users, never creates a missing document, never reactivates a
  deactivated one, never touches a document bound to an organisation or project
  (both are reported as warnings: that data is wrong);
* a second apply finds nothing to add or remove and writes nothing.

**A removal is a revocation, so it carries the D4-B contract.** An addition can only
make a cached decision briefly more restrictive, but a removal would leave a cached
grant MORE permissive than the stored role for a whole cache TTL. So the removal path
reads the role's holders - by id and by legacy spelling, both of which receive this
document's permissions - announces the authority change before the write and
invalidates it afterwards, exactly as `RoleService` does. If the holders cannot be
read, or the announcement cannot be made, **the role is not changed at all**.

**Owner-approved grants are a separate, narrower operation (`grant`).** A role outside
`ALIGNED_ROLE_IDS` receives exactly the permissions `OWNER_APPROVED_GRANTS` names for it
- today the four `dms.hindrance.*` permissions for `contractmgr_org` - and nothing else
from its release definition. Additions only, same lifecycle rules, same command.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Set, Tuple

from bson import ObjectId

from ..core.role_reference import normalize_role_key
from ..initial_data.default_roles import DEFAULT_ROLES
from .permission_service import (
    begin_authority_change,
    cancel_authority_change,
    complete_authority_change,
)

OPERATION = "role_contract_alignment/R-A9D"

#: Owner decision 2 (R-A9D). Adding a role here is a new grant and needs its own decision.
ALIGNED_ROLE_IDS = ("orgadmin", "projectadmin")

#: Owner decision F-A9D-1 (R-A9E). Adding an entry here REVOKES a live grant and needs
#: its own owner decision; a permission listed for a role whose release definition
#: contains it is refused rather than removed.
OWNER_APPROVED_REMOVALS: Dict[str, Tuple[str, ...]] = {
    "orgadmin": ("billing.plan.manage",),
    "projectadmin": ("billing.plan.manage",),
}

#: Owner decision (Hindrance staging certification, 2026-09-21). An explicit, bounded
#: grant to a role OUTSIDE ALIGNED_ROLE_IDS: exactly these permissions and nothing else
#: from that role's release definition. Adding an entry here is a new grant and needs its
#: own owner decision; a grant outside the role's release definition is refused.
OWNER_APPROVED_GRANTS: Dict[str, Tuple[str, ...]] = {
    "contractmgr_org": (
        "dms.hindrance.view",
        "dms.hindrance.create",
        "dms.hindrance.edit",
        "dms.hindrance.archive",
    ),
}

ALIGN = "align"
ALIGNED = "aligned"
ABSENT = "absent"
INACTIVE = "inactive"
ORGANIZATION_BOUND = "organization_bound"
#: A grant naming a permission outside the role's release definition: never applied.
REFUSED = "refused"

NOTICES = [
    "Aligns only the orgadmin and projectadmin role documents to this release's DEFAULT_ROLES: "
    "it adds the permissions that contract defines, removes only the permissions the owner "
    "approved by name (F-A9D-1), preserves and reports every other permission the document "
    "holds outside the contract, and never writes users or creates or reactivates a role."
]

_PERMISSION_ID = re.compile(r"^[0-9a-fA-F]{24}$")


@dataclass(frozen=True)
class AlignmentResult:
    dry_run: bool
    operations: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    notices: List[str] = field(default_factory=list)


def release_contract(role_id: str) -> List[str]:
    """The permissions this release defines for a role, in definition order."""
    for role in DEFAULT_ROLES:
        if role.get("_id") == role_id:
            return list(dict.fromkeys(str(permission) for permission in role.get("permissions") or []))
    raise KeyError(f"{role_id!r} is not a release default role")


def approved_removals(role_id: str) -> Tuple[str, ...]:
    """The permissions the owner approved removing from this role, by name."""
    return tuple(OWNER_APPROVED_REMOVALS.get(role_id, ()))


def _collection(db: Any, name: str) -> Any:
    try:
        return db[name]
    except Exception:
        return getattr(db, name)


async def held_permission_index(db: Any, stored: Iterable[Any]) -> Dict[str, List[Any]]:
    """What a role document holds, by name, mapped to the values it actually stores.

    A stored permission `_id` counts as its name, and the token it is stored as is
    what a removal has to `$pull` - the name alone would not match it.
    """
    index: Dict[str, List[Any]] = {}
    ids: List[ObjectId] = []
    by_id: Dict[str, List[Any]] = {}
    for value in stored or []:
        text = str(value)
        if _PERMISSION_ID.match(text):
            ids.append(ObjectId(text))
            by_id.setdefault(text, []).append(value)
        elif text:
            index.setdefault(text, []).append(value)
    if ids:
        documents = await _collection(db, "permissions").find({"_id": {"$in": ids}}, {"name": 1}).to_list(length=None)
        names = {str(document["_id"]): str(document["name"]) for document in documents if document.get("name")}
        for text, tokens in by_id.items():
            index.setdefault(names.get(text, text), []).extend(tokens)
    return index


async def held_permission_names(db: Any, stored: Iterable[Any]) -> Set[str]:
    """What a role document holds, by name. A stored permission `_id` counts as its name."""
    return set(await held_permission_index(db, stored))


def _bound(document: Dict[str, Any]) -> bool:
    return any(document.get(field) not in (None, "") for field in ("organization_id", "project_id"))


def _lifecycle_status(document: Dict[str, Any], pending: bool) -> str:
    """The one status ladder both operations share: lifecycle first, then pending work."""
    if document.get("is_active") is False:
        return INACTIVE
    if _bound(document):
        return ORGANIZATION_BOUND
    return ALIGN if pending else ALIGNED


def _reference_names_role(reference: Any, role_id: str) -> bool:
    """Whether a stored `users.roles` entry can resolve to this role, legacy spellings included."""
    text = str(reference).strip()
    return text == role_id or normalize_role_key(text) == role_id


async def _holders_of_role(db: Any, role_id: str) -> List[str]:
    """Every user whose `users.roles` can resolve to this role. Read-only."""
    cursor = _collection(db, "users").find({}, {"_id": 1, "roles": 1})
    return [
        str(user["_id"])
        for user in await cursor.to_list(length=None)
        if any(_reference_names_role(reference, role_id) for reference in user.get("roles") or [])
    ]


async def plan_role(db: Any, role_id: str) -> Dict[str, Any]:
    expected = release_contract(role_id)
    approved = set(approved_removals(role_id))
    #: A removal the release definition of this role contains would fight the alignment.
    #: It is reported and NOT executed.
    unapproved = sorted(approved & set(expected))
    row: Dict[str, Any] = {
        "role_id": role_id,
        "expected": sorted(set(expected)),
        "unapproved_removals": unapproved,
    }
    document = await _collection(db, "roles").find_one({"_id": role_id})
    if document is None:
        row.update(status=ABSENT, before=[], additions=[], removals=[], retained_outside_contract=[])
        return row
    index = await held_permission_index(db, document.get("permissions"))
    held = set(index)
    removals = sorted((approved - set(expected)) & held)
    additions = sorted(set(expected) - held)
    row.update(
        before=sorted(held),
        additions=additions,
        removals=removals,
        retained_outside_contract=sorted(held - set(expected) - set(removals)),
    )
    row["_tokens"] = [token for name in removals for token in index[name]]
    row["status"] = _lifecycle_status(document, bool(additions or removals))
    return row


async def _apply_role(db: Any, row: Dict[str, Any], warnings: List[str]) -> None:
    """Write one role: removals first (a failure then leaves it less permissive), then additions."""
    roles = _collection(db, "roles")
    role_id = row["role_id"]
    query = {"_id": role_id, "is_active": {"$ne": False}}
    now = datetime.now(timezone.utc)

    if not row["removals"]:
        result = await roles.update_one(
            query,
            {"$addToSet": {"permissions": {"$each": list(row["additions"])}}, "$set": {"updated_at": now}},
        )
        if not getattr(result, "matched_count", 0):
            warnings.append(f"role '{role_id}' changed while it was being aligned: not aligned")
        return

    try:
        holders = await _holders_of_role(db, role_id)
    except Exception as exc:
        warnings.append(
            f"role '{role_id}' not changed: its holders could not be read, so the revoked permission "
            f"could stay in their cached authority: {exc}"
        )
        return
    try:
        await begin_authority_change(holders)
    except Exception as exc:
        warnings.append(
            f"role '{role_id}' not changed: the authorization cache could not be told about the "
            f"revocation, so the revoked permission would stay readable for a cache TTL: {exc}"
        )
        return

    applied = False
    try:
        result = await roles.update_one(
            query,
            {"$pull": {"permissions": {"$in": list(row["_tokens"])}}, "$set": {"updated_at": now}},
        )
        applied = bool(getattr(result, "matched_count", 0))
        if not applied:
            warnings.append(f"role '{role_id}' changed while it was being aligned: not aligned")
        elif row["additions"]:
            added = await roles.update_one(
                query,
                {"$addToSet": {"permissions": {"$each": list(row["additions"])}}, "$set": {"updated_at": now}},
            )
            if not getattr(added, "matched_count", 0):
                warnings.append(
                    f"role '{role_id}': the approved permission was removed but the release contract "
                    f"was not added back - re-run the alignment"
                )
    finally:
        if not applied:
            await cancel_authority_change(holders)
        else:
            try:
                joined = await _holders_of_role(db, role_id)
            except Exception as exc:
                #: Nothing is cleared: the announced holders keep their pending markers, which
                #: outlive every entry that could predate the change.
                warnings.append(
                    f"role '{role_id}' changed, but its holders could not be re-read; their cached "
                    f"authority stays bypassed until the pending markers expire: {exc}"
                )
            else:
                await complete_authority_change(list(dict.fromkeys([*holders, *joined])))


async def align(db: Any, dry_run: bool = True) -> AlignmentResult:
    roles = _collection(db, "roles")
    operations: List[Dict[str, Any]] = []
    warnings: List[str] = []

    for role_id in ALIGNED_ROLE_IDS:
        row = await plan_role(db, role_id)
        status = row["status"]
        if row["unapproved_removals"]:
            warnings.append(
                f"role '{role_id}': {', '.join(row['unapproved_removals'])} is inside the release contract "
                f"and was not removed - the owner decision and the release contract disagree"
            )
        if status == INACTIVE:
            warnings.append(f"role '{role_id}' is deactivated: not aligned and not reactivated")
        elif status == ORGANIZATION_BOUND:
            warnings.append(f"role '{role_id}' is bound to an organisation or project: not aligned")
        elif status == ALIGN and not dry_run:
            await _apply_role(db, row, warnings)
        if not dry_run and status != ABSENT:
            stored = await roles.find_one({"_id": role_id})
            row["after"] = sorted(await held_permission_names(db, (stored or {}).get("permissions")))
        row.pop("_tokens", None)
        operations.append(row)

    return AlignmentResult(dry_run=dry_run, operations=operations, warnings=warnings, notices=list(NOTICES))


GRANT_NOTICES = [
    "Grants only the permissions the owner approved by name (OWNER_APPROVED_GRANTS) to roles "
    "outside the alignment: it adds nothing else from their release definitions, removes "
    "nothing, never writes users and never creates or reactivates a role."
]


async def plan_grant(db: Any, role_id: str) -> Dict[str, Any]:
    granted = list(dict.fromkeys(OWNER_APPROVED_GRANTS.get(role_id, ())))
    row: Dict[str, Any] = {
        "role_id": role_id,
        "expected": sorted(granted),
        "outside_release_contract": sorted(set(granted) - set(release_contract(role_id))),
        "removals": [],
        "unapproved_removals": [],
    }
    document = await _collection(db, "roles").find_one({"_id": role_id})
    if document is None:
        row.update(status=ABSENT, before=[], additions=[], retained_outside_contract=[])
        return row
    held = await held_permission_names(db, document.get("permissions"))
    additions = sorted(set(granted) - held)
    row.update(before=sorted(held), additions=additions, retained_outside_contract=sorted(held - set(granted)))
    row["status"] = REFUSED if row["outside_release_contract"] else _lifecycle_status(document, bool(additions))
    return row


async def grant(db: Any, dry_run: bool = True) -> AlignmentResult:
    """Apply OWNER_APPROVED_GRANTS. Additions only, so it takes the silent path: a stale
    cached decision can only be more restrictive than the stored role."""
    roles = _collection(db, "roles")
    operations: List[Dict[str, Any]] = []
    warnings: List[str] = []

    for role_id in OWNER_APPROVED_GRANTS:
        row = await plan_grant(db, role_id)
        status = row["status"]
        if status == REFUSED:
            warnings.append(
                f"role '{role_id}': {', '.join(row['outside_release_contract'])} is outside its release "
                f"definition - the grant was refused, not applied"
            )
        elif status == INACTIVE:
            warnings.append(f"role '{role_id}' is deactivated: not granted and not reactivated")
        elif status == ORGANIZATION_BOUND:
            warnings.append(f"role '{role_id}' is bound to an organisation or project: not granted")
        elif status == ALIGN and not dry_run:
            await _apply_role(db, row, warnings)
        if not dry_run and status != ABSENT:
            stored = await roles.find_one({"_id": role_id})
            row["after"] = sorted(await held_permission_names(db, (stored or {}).get("permissions")))
        operations.append(row)

    return AlignmentResult(dry_run=dry_run, operations=operations, warnings=warnings, notices=list(GRANT_NOTICES))
