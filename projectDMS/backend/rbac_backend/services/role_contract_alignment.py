"""Align the canonical `orgadmin` / `projectadmin` role documents to this release (R-A9D).

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

What it does, and deliberately does not:

* only the documents whose `_id` is exactly `orgadmin` or `projectadmin`;
* only `$addToSet` of permissions the release definition of that role contains and
  the document does not already hold (by name, or by a stored permission `_id`);
* never removes a permission, never reads or writes users, never creates a missing
  document, never reactivates a deactivated one, never touches a document bound to
  an organisation or project (both are reported as warnings: that data is wrong);
* a second apply finds nothing to add and writes nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Set

from bson import ObjectId

from ..initial_data.default_roles import DEFAULT_ROLES

OPERATION = "role_contract_alignment/R-A9D"

#: Owner decision 2 (R-A9D). Adding a role here is a new grant and needs its own decision.
ALIGNED_ROLE_IDS = ("orgadmin", "projectadmin")

ALIGN = "align"
ALIGNED = "aligned"
ABSENT = "absent"
INACTIVE = "inactive"
ORGANIZATION_BOUND = "organization_bound"

NOTICES = [
    "Aligns only the orgadmin and projectadmin role documents to this release's DEFAULT_ROLES "
    "by adding permissions: it never removes a permission, never reads or writes users, and "
    "never creates or reactivates a role."
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


def _collection(db: Any, name: str) -> Any:
    try:
        return db[name]
    except Exception:
        return getattr(db, name)


async def held_permission_names(db: Any, stored: Iterable[Any]) -> Set[str]:
    """What a role document holds, by name. A stored permission `_id` counts as its name."""
    names: Set[str] = set()
    ids: List[ObjectId] = []
    for value in stored or []:
        text = str(value)
        if _PERMISSION_ID.match(text):
            ids.append(ObjectId(text))
        elif text:
            names.add(text)
    if ids:
        documents = await _collection(db, "permissions").find({"_id": {"$in": ids}}, {"name": 1}).to_list(length=None)
        resolved = {str(document.get("_id")) for document in documents if document.get("name")}
        names.update(str(document["name"]) for document in documents if document.get("name"))
        names.update(str(value) for value in ids if str(value) not in resolved)
    return names


def _bound(document: Dict[str, Any]) -> bool:
    return any(document.get(field) not in (None, "") for field in ("organization_id", "project_id"))


async def plan_role(db: Any, role_id: str) -> Dict[str, Any]:
    expected = release_contract(role_id)
    row: Dict[str, Any] = {"role_id": role_id, "expected": sorted(set(expected))}
    document = await _collection(db, "roles").find_one({"_id": role_id})
    if document is None:
        row.update(status=ABSENT, before=[], additions=[], retained_outside_contract=[])
        return row
    held = await held_permission_names(db, document.get("permissions"))
    additions = sorted(set(expected) - held)
    row.update(
        before=sorted(held),
        additions=additions,
        retained_outside_contract=sorted(held - set(expected)),
    )
    if document.get("is_active") is False:
        row["status"] = INACTIVE
    elif _bound(document):
        row["status"] = ORGANIZATION_BOUND
    else:
        row["status"] = ALIGN if additions else ALIGNED
    return row


async def align(db: Any, dry_run: bool = True) -> AlignmentResult:
    roles = _collection(db, "roles")
    operations: List[Dict[str, Any]] = []
    warnings: List[str] = []

    for role_id in ALIGNED_ROLE_IDS:
        row = await plan_role(db, role_id)
        status = row["status"]
        if status == INACTIVE:
            warnings.append(f"role '{role_id}' is deactivated: not aligned and not reactivated")
        elif status == ORGANIZATION_BOUND:
            warnings.append(f"role '{role_id}' is bound to an organisation or project: not aligned")
        elif status == ALIGN and not dry_run:
            result = await roles.update_one(
                {"_id": role_id, "is_active": {"$ne": False}},
                {
                    "$addToSet": {"permissions": {"$each": list(row["additions"])}},
                    "$set": {"updated_at": datetime.now(timezone.utc)},
                },
            )
            if not getattr(result, "matched_count", 0):
                warnings.append(f"role '{role_id}' changed while it was being aligned: not aligned")
        if not dry_run and status != ABSENT:
            stored = await roles.find_one({"_id": role_id})
            row["after"] = sorted(await held_permission_names(db, (stored or {}).get("permissions")))
        operations.append(row)

    return AlignmentResult(dry_run=dry_run, operations=operations, warnings=warnings, notices=list(NOTICES))
