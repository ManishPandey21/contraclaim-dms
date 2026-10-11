#!/usr/bin/env python3
"""Read-only pre-deploy audit of who holds system roles, and of every stored role reference.

Super Admin authority attaches to the role name (docs/adr/0001): whoever carries
`superadmin` in `users.roles` after the principal's normaliser is a Super Admin,
whatever the role document says. So the control is the holder list, and this audit
is the gate that checks it (owner decisions Q9, Q14, Q15 - 2026-09-15).

Every reference is classified by `resolve_role_reference`, the resolver authorization
itself uses (`backend/rbac_backend/core/role_reference.py`). This script runs fed on
stdin inside an image that may predate that module, so it carries a verbatim copy
between the `role_reference` markers below; a test fails if the copy differs by a byte.
When the running image does ship the module, the audit also resolves every reference
with the image's own copy and fails on any difference.

It FAILS (exit 1) on any of:

* a Super User holder (the role is dormant; no account may hold it);
* no `superadmin` role document, or a deactivated one;
* a Super Admin holder count other than `--expected-superadmin-holders`;
* a stored role reference that resolves to no role document, to more than one, to a
  deactivated role, or through an alias of a system role (`super-admin`);
* a reference the running image's resolver classifies differently from this audit.

It WARNS (exit 0) only for a legacy spelling that authorization resolves one hop to
exactly one active, non-system canonical document (`organization-admin` -> `orgadmin`):
the account really carries that role's tier and permissions.

It exits 2 when the role store cannot be read: an audit that did not run is not a pass.

Output is role strings and counts only - never user ids, emails or names - and a read
error prints its type, never its message (a connection error can carry a URI).

Run it inside the backend container, fed on stdin so it works against an image that
does not ship it:

    docker compose ... exec -T backend python - --expected-superadmin-holders 2 < scripts/system_role_audit.py
"""

from __future__ import annotations

# ruff: noqa: E402 - the embedded role_reference copy carries its own `import re` mid-file.

import argparse
import asyncio
import sys
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_UNEVALUATED = 2

UserRoles = Tuple[Sequence[Any], bool]  # (stored role references, disabled)

# >>> role_reference: verbatim copy of backend/rbac_backend/core/role_reference.py - never edit here
"""One definition of what a stored role reference resolves to.

A `users.roles` entry becomes authority in two places: the principal
(`core.security`, which carries the role KEY - tier, scope, Super Admin bypass) and
the permission resolver (`services.permission_service`, which loads the role
DOCUMENT's permissions). The pre-deploy audit (`scripts/system_role_audit.py`) must
classify a reference exactly as those two treat it. All three use this module. The
audit carries a byte-identical copy because it runs, fed on stdin, inside an image
that may predate this module; `test_role_reference_single_definition.py` pins the copy.

Standard library only, and no `from __future__` import: the audit embeds this source.

Contract (owner decision, 2026-09-15):

* a reference that is a role document's `_id` resolves to that document;
* otherwise the key the principal carries for it (`normalize_role_key`: `ROLE_ALIASES`,
  else the lowercased reference) may be a canonical `_id` - ONE hop, never chained. That
  is a legacy spelling (`organization-admin` -> `orgadmin`, `DocController` ->
  `doccontroller`), resolved only when exactly one document answers: no other document
  may carry the reference as its exact name. Resolving exactly the key the principal
  already carries is what keeps tier and permissions equal;
* an alias of a system role (`super-admin`) never resolves to the system document.
  ADR 0001 keeps Super Admin authority name-based on the principal; the audit fails
  such a reference and role assignment stores the canonical id instead;
* a deactivated target resolves to nothing and revokes the reference - the principal
  drops its key too;
* anything else resolves to nothing. There is no second hop and no lookup by name.
"""

import re

#: Legacy role spellings and the canonical role key each one names. The principal's
#: table since the initial commit; one hop, never chained.
ROLE_ALIASES = {
    "organization-user": "orguser",
    "org-user": "orguser",
    "organization user": "orguser",
    "organizationuser": "orguser",
    "orguser": "orguser",
    "organization-admin": "orgadmin",
    "org-admin": "orgadmin",
    "organization admin": "orgadmin",
    "organizationadmin": "orgadmin",
    "orgadmin": "orgadmin",
    "project-user": "projectuser",
    "project user": "projectuser",
    "projectuser": "projectuser",
    "proj-user": "projectuser",
    "proj user": "projectuser",
    "projuser": "projectuser",
    "project-admin": "projectadmin",
    "project admin": "projectadmin",
    "projectadmin": "projectadmin",
    "proj-admin": "projectadmin",
    "proj admin": "projectadmin",
    "projadmin": "projectadmin",
    "super-admin": "superadmin",
    "super admin": "superadmin",
    "superadministrator": "superadmin",
}

SUPER_ADMIN = "superadmin"
SUPER_USER = "superuser"
SYSTEM_ROLE_KEYS = frozenset({SUPER_ADMIN, SUPER_USER})

CANONICAL = "canonical"
LEGACY_ALIAS = "legacy_alias"
SYSTEM_ALIAS = "system_alias"
INACTIVE = "inactive"
AMBIGUOUS = "ambiguous"
UNRESOLVED = "unresolved"

_NON_ALPHANUMERIC = re.compile(r"[^a-z0-9]")


def normalize_role_key(value):
    """The role key the principal carries for one stored reference."""
    text = str(value).strip().lower()
    return ROLE_ALIASES.get(text, ROLE_ALIASES.get(_NON_ALPHANUMERIC.sub("", text), text))


def legacy_alias_target(reference):
    """The canonical key `ROLE_ALIASES` names for a spelling, or None (used to refuse lookalike role names)."""
    text = str(reference).strip().lower()
    return ROLE_ALIASES.get(text, ROLE_ALIASES.get(_NON_ALPHANUMERIC.sub("", text)))


def role_is_active(document):
    """Soft delete writes `is_active=False`; a document without the flag predates it and is active."""
    return document.get("is_active") is not False


def role_is_system(document):
    scope = document.get("scope")
    if isinstance(scope, str) and scope.strip():
        return scope.strip().lower() == "system"
    return any(
        _NON_ALPHANUMERIC.sub("", str(document.get(field) or "").strip().lower()) in SYSTEM_ROLE_KEYS
        for field in ("_id", "name")
    )


def reference_text(reference):
    return "" if reference is None else str(reference).strip()


class RoleResolution:
    """What one stored reference resolves to, and what it may contribute."""

    __slots__ = ("reference", "key", "status", "role_id", "document", "revoked")

    def __init__(self, reference, key, status, role_id=None, document=None, revoked=False):
        self.reference = reference
        self.key = key
        self.status = status
        self.role_id = role_id
        self.document = document
        self.revoked = revoked

    @property
    def is_alias(self):
        return self.key != self.reference

    @property
    def grants_document(self):
        """Whether the role document's permissions apply."""
        return self.status in (CANONICAL, LEGACY_ALIAS)

    @property
    def keeps_role_key(self):
        """Whether the principal, and name-based grants, may carry `key`.

        A resolved reference keeps it. A system alias keeps it (ADR 0001) unless its
        document is deactivated. A reference that is not an alias and has no document
        keeps it, as it always has (a bare key). An alias that does not resolve, or
        resolves ambiguously or to a deactivated role, carries nothing.
        """
        if self.revoked:
            return False
        if self.status in (CANONICAL, LEGACY_ALIAS, SYSTEM_ALIAS):
            return True
        return self.status == UNRESOLVED and not self.is_alias

    def __eq__(self, other):
        if not isinstance(other, RoleResolution):
            return NotImplemented
        return (self.reference, self.key, self.status, self.role_id, self.revoked) == (
            other.reference,
            other.key,
            other.status,
            other.role_id,
            other.revoked,
        )

    def __repr__(self):
        return (
            f"RoleResolution({self.reference!r} -> {self.status}, role_id={self.role_id!r}, "
            f"revoked={self.revoked})"
        )


def resolve_role_reference(reference, documents):
    """Resolve one stored reference against role documents.

    `documents` may hold any superset of the documents that matter: those whose `_id`
    is the reference or its key, and those whose `name` is the reference.
    """
    text = reference_text(reference)
    if not text:
        return RoleResolution(text, "", UNRESOLVED)
    key = normalize_role_key(text)

    by_id = {}
    for document in documents or ():
        if document is not None:
            by_id.setdefault(str(document.get("_id")), document)

    direct = by_id.get(text)
    if direct is not None:
        if not role_is_active(direct):
            return RoleResolution(text, key, INACTIVE, role_id=text, revoked=True)
        return RoleResolution(text, key, CANONICAL, role_id=text, document=direct)

    target = by_id.get(key) if key != text else None
    if target is None:
        return RoleResolution(text, key, UNRESOLVED)
    target_id = str(target.get("_id"))

    if any(
        document.get("name") is not None and str(document.get("name")) == text and role_id != target_id
        for role_id, document in by_id.items()
    ):
        return RoleResolution(text, key, AMBIGUOUS, role_id=target_id)

    revoked = not role_is_active(target)
    if key in SYSTEM_ROLE_KEYS or role_is_system(target):
        return RoleResolution(text, key, SYSTEM_ALIAS, role_id=target_id, revoked=revoked)
    if revoked:
        return RoleResolution(text, key, INACTIVE, role_id=target_id, revoked=True)
    return RoleResolution(text, key, LEGACY_ALIAS, role_id=target_id, document=target)
# <<< role_reference

Resolver = Callable[[Any, List[Dict[str, Any]]], RoleResolution]


@dataclass
class Report:
    passes: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def lines(self) -> List[str]:
        return (
            [f"NOTE: {line}" for line in self.notes]
            + [f"PASS: {line}" for line in self.passes]
            + [f"WARN: {line}" for line in self.warnings]
            + [f"FAIL: {line}" for line in self.failures]
            + [f"System-role audit: {len(self.failures)} failure(s), {len(self.warnings)} warning(s)."]
        )

    @property
    def exit_code(self) -> int:
        return EXIT_FAIL if self.failures else EXIT_OK


def _tally(counter: Counter) -> str:
    return ", ".join(f"{label} x{count}" for label, count in sorted(counter.items()))


def _signature(resolution: RoleResolution) -> Tuple[Any, ...]:
    """Everything authorization reads from a resolution."""
    return (
        resolution.status,
        resolution.role_id,
        resolution.revoked,
        resolution.key,
        resolution.keeps_role_key,
        resolution.grants_document,
    )


def evaluate(
    role_docs: Iterable[Dict[str, Any]],
    users: Iterable[UserRoles],
    expected_superadmin_holders: int,
    runtime_resolve: Optional[Resolver] = None,
) -> Report:
    """Classify with the embedded resolver; when `runtime_resolve` is given, fail on any disagreement."""
    report = Report()
    documents = list(role_docs)
    users = list(users)
    by_id = {str(doc.get("_id")): doc for doc in documents}

    superadmin_doc = by_id.get(SUPER_ADMIN)
    if superadmin_doc is None:
        report.failures.append("no 'superadmin' role document exists")
    elif not role_is_active(superadmin_doc):
        report.failures.append("the 'superadmin' role document is deactivated; every holder is locked out")
    else:
        report.passes.append("the 'superadmin' role document exists and is not deactivated")

    holders = {"enabled": 0, "disabled": 0}
    superuser_references = 0
    unresolved: Counter = Counter()
    ambiguous: Counter = Counter()
    system_aliases: Counter = Counter()
    inactive: Counter = Counter()
    aliases: Counter = Counter()
    divergent: Counter = Counter()

    for references, disabled in users:
        holds_super_admin = False
        for raw in references or []:
            resolution = resolve_role_reference(raw, documents)
            text, key = resolution.reference, resolution.key
            if key == SUPER_USER or _NON_ALPHANUMERIC.sub("", text.lower()) == SUPER_USER:
                superuser_references += 1
            if key == SUPER_ADMIN and resolution.keeps_role_key:
                holds_super_admin = True

            if runtime_resolve is not None:
                theirs = runtime_resolve(raw, documents)
                if _signature(theirs) != _signature(resolution):
                    divergent[f"{text}: audit {resolution.status}, runtime {theirs.status}"] += 1

            if resolution.status == UNRESOLVED:
                unresolved[text] += 1
            elif resolution.status == AMBIGUOUS:
                ambiguous[f"{text} -> {resolution.role_id} and a role named '{text}'"] += 1
            elif resolution.status == SYSTEM_ALIAS:
                system_aliases[f"{text} -> {resolution.role_id} (alias of a system role)"] += 1
            elif resolution.status == INACTIVE:
                inactive[f"{text} -> {resolution.role_id}"] += 1
            elif resolution.status == LEGACY_ALIAS:
                aliases[f"{text} -> {resolution.role_id}"] += 1

        if holds_super_admin:
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
    if system_aliases:
        report.failures.append(f"role references resolving to an unexpected role: {_tally(system_aliases)}")
    if inactive:
        report.failures.append(f"role references to deactivated roles (they grant nothing): {_tally(inactive)}")
    if not (unresolved or ambiguous or system_aliases or inactive):
        report.passes.append("every role reference resolves to exactly one active role document")
    if aliases:
        whose = "authorization resolves" if runtime_resolve is not None else "the candidate's authorization resolves"
        report.warnings.append(
            f"legacy spellings that {whose} to their active canonical role (tier and permissions): {_tally(aliases)}"
        )

    if runtime_resolve is None:
        report.notes.append(
            "the running image has no core.role_reference and may not resolve legacy spellings at all; "
            "references are classified as the candidate resolves them"
        )
    elif divergent:
        report.failures.append(f"the audit and the running image resolve references differently: {_tally(divergent)}")
    else:
        report.passes.append("the running image resolves every reference exactly as this audit does")
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


def _runtime_resolver() -> Optional[Resolver]:
    try:
        from rbac_backend.core.role_reference import resolve_role_reference as runtime_resolve
    except ImportError:
        return None
    return runtime_resolve


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
        roles, users = asyncio.run(_collect_from_application())
    except Exception as exc:  # noqa: BLE001 - any failure to read means the audit did not run
        print(f"FAIL: the system-role audit could not read the role store ({type(exc).__name__})")
        return EXIT_UNEVALUATED

    report = evaluate(roles, users, args.expected_superadmin_holders, _runtime_resolver())
    for line in report.lines():
        print(line)
    return report.exit_code


if __name__ == "__main__":
    sys.exit(main())
