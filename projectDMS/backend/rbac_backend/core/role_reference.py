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
