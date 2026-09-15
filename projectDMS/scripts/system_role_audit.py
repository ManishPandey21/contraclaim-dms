#!/usr/bin/env python3
"""Read-only pre-deploy audit of who holds system roles.

Super Admin authority attaches to the role name (docs/adr/0001): whoever carries
`superadmin` in `users.roles` after the principal's normaliser is a Super Admin,
whatever the role document says. So the control is the holder list, and this audit
is the gate that checks it (owner decisions Q9, Q14, Q15 - 2026-09-15).

It FAILS (exit 1) on any of:

* a Super User holder (the role is dormant; no account may hold it);
* no `superadmin` role document, or a deactivated one;
* a Super Admin holder count other than `--expected-superadmin-holders`;
* a stored role reference that resolves to no role document, to more than one, or to
  an unexpected role: a reference whose normalised key is not the id of the document
  it resolves to, or an alias spelling of a system-scope role.

It WARNS (exit 0) only for a legacy alias spelling that resolves to exactly the
intended non-system canonical document (e.g. `organization-admin` -> `orgadmin`), and
for a reference to a deactivated role, which grants nothing.

It exits 2 when the role store cannot be read: an audit that did not run is not a pass.

Output is role strings and counts only - never user ids, emails or names - and a read
error prints its type, never its message (a connection error can carry a URI).

Run it inside the backend container, fed on stdin so it works against an image that
does not ship it:

    docker compose ... exec -T backend python - --expected-superadmin-holders 2 < scripts/system_role_audit.py
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Sequence, Tuple

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_UNEVALUATED = 2

SUPER_ADMIN = "superadmin"
SUPER_USER = "superuser"
SYSTEM_ROLE_KEYS = {SUPER_ADMIN, SUPER_USER}

Normalizer = Callable[[List[str]], List[str]]
UserRoles = Tuple[Sequence[Any], bool]  # (stored role references, disabled)


def _compact(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").strip().lower())


def _scope(doc: Dict[str, Any]) -> str:
    scope = doc.get("scope")
    if isinstance(scope, str) and scope.strip():
        return scope.strip().lower()
    if _compact(doc.get("_id")) in SYSTEM_ROLE_KEYS or _compact(doc.get("name")) in SYSTEM_ROLE_KEYS:
        return "system"
    if doc.get("project_id"):
        return "project"
    return "organization"


@dataclass
class Report:
    passes: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)

    def lines(self) -> List[str]:
        return (
            [f"PASS: {line}" for line in self.passes]
            + [f"WARN: {line}" for line in self.warnings]
            + [f"FAIL: {line}" for line in self.failures]
            + [f"System-role audit: {len(self.failures)} failure(s), {len(self.warnings)} warning(s)."]
        )

    @property
    def exit_code(self) -> int:
        return EXIT_FAIL if self.failures else EXIT_OK


def _tally(counter: Counter) -> str:
    return ", ".join(f"{label} x{count}" for label, count in sorted(counter.items()))


def evaluate(
    role_docs: Iterable[Dict[str, Any]],
    users: Iterable[UserRoles],
    expected_superadmin_holders: int,
    normalize: Normalizer,
) -> Report:
    report = Report()
    by_id: Dict[str, Dict[str, Any]] = {}
    by_name: Dict[str, List[str]] = {}
    for doc in role_docs:
        role_id = str(doc.get("_id"))
        by_id[role_id] = doc
        if doc.get("name"):
            by_name.setdefault(str(doc["name"]), []).append(role_id)

    superadmin_doc = by_id.get(SUPER_ADMIN)
    if superadmin_doc is None:
        report.failures.append("no 'superadmin' role document exists")
    elif superadmin_doc.get("is_active") is False:
        report.failures.append("the 'superadmin' role document is deactivated; every holder is locked out")
    else:
        report.passes.append("the 'superadmin' role document exists and is not deactivated")

    holders = {"enabled": 0, "disabled": 0}
    superuser_references = 0
    unresolved: Counter = Counter()
    ambiguous: Counter = Counter()
    unexpected: Counter = Counter()
    aliases: Counter = Counter()
    inactive: Counter = Counter()

    for references, disabled in users:
        principal_keys = set()
        for raw in references or []:
            text = str(raw).strip()
            key = (normalize([text]) or [""])[0]
            if key == SUPER_USER or _compact(text) == SUPER_USER:
                superuser_references += 1
            if key == SUPER_ADMIN:
                principal_keys.add(SUPER_ADMIN)

            matches = {candidate for candidate in dict.fromkeys([text, key]) if candidate in by_id}
            matches.update(by_name.get(text, []))
            if not matches:
                unresolved[text] += 1
                continue
            if len(matches) > 1:
                ambiguous[f"{text} -> {sorted(matches)}"] += 1
                continue
            (role_id,) = matches
            doc = by_id[role_id]
            if key != role_id:
                unexpected[f"{text} -> {role_id} (the principal would carry '{key}')"] += 1
            elif text != role_id and _scope(doc) == "system":
                unexpected[f"{text} -> {role_id} (alias of a system role)"] += 1
            elif doc.get("is_active") is False:
                inactive[f"{text} -> {role_id}"] += 1
            elif text != role_id:
                aliases[f"{text} -> {role_id}"] += 1

        if SUPER_ADMIN in principal_keys:
            holders["disabled" if disabled else "enabled"] += 1

    total_holders = holders["enabled"] + holders["disabled"]
    holder_detail = f"{total_holders} ({holders['enabled']} enabled, {holders['disabled']} disabled)"
    if total_holders == expected_superadmin_holders:
        report.passes.append(f"Super Admin holders = {holder_detail}, as approved")
    else:
        report.failures.append(
            f"Super Admin holders = {holder_detail}, approved = {expected_superadmin_holders}; "
            "investigate before deploying"
        )

    if superuser_references:
        report.failures.append(f"{superuser_references} Super User role reference(s); the role is dormant")
    else:
        report.passes.append("no account holds the dormant Super User role")

    if unresolved:
        report.failures.append(f"role references with no role document: {_tally(unresolved)}")
    if ambiguous:
        report.failures.append(f"role references matching more than one role document: {_tally(ambiguous)}")
    if unexpected:
        report.failures.append(f"role references resolving to an unexpected role: {_tally(unexpected)}")
    if not (unresolved or ambiguous or unexpected):
        report.passes.append("every role reference resolves to exactly one expected role document")
    if aliases:
        report.warnings.append(f"legacy alias spellings resolving to their canonical role: {_tally(aliases)}")
    if inactive:
        report.warnings.append(f"references to deactivated roles (they grant nothing): {_tally(inactive)}")
    return report


async def collect(db) -> Tuple[List[Dict[str, Any]], List[UserRoles]]:
    """Read only what the audit needs: role metadata, and each user's roles and disabled flag."""
    roles = [
        doc
        async for doc in db.roles.find(
            {}, {"name": 1, "is_active": 1, "scope": 1, "is_system": 1, "project_id": 1}
        )
    ]
    users = [
        (list(doc.get("roles") or []), bool(doc.get("disabled")))
        async for doc in db.users.find({}, {"roles": 1, "disabled": 1, "_id": 0})
    ]
    return roles, users


async def _collect_from_application():
    from rbac_backend.core.database import get_database

    return await collect(await get_database())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only pre-deploy system-role audit.")
    parser.add_argument(
        "--expected-superadmin-holders",
        type=int,
        required=True,
        help="approved number of accounts holding Super Admin",
    )
    args = parser.parse_args(argv)
    if args.expected_superadmin_holders < 0:
        parser.error("--expected-superadmin-holders must be zero or more")

    try:
        from rbac_backend.core.security import _normalize_roles_list

        roles, users = asyncio.run(_collect_from_application())
    except Exception as exc:  # noqa: BLE001 - any failure to read means the audit did not run
        print(f"FAIL: the system-role audit could not read the role store ({type(exc).__name__})")
        return EXIT_UNEVALUATED

    report = evaluate(roles, users, args.expected_superadmin_holders, _normalize_roles_list)
    for line in report.lines():
        print(line)
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
