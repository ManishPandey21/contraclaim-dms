"""Where a user's password hash lives on the ``users`` document.

The single source for every credential write and read (create, admin reset,
self-service change, login, step-up). Deliberately dependency-free so any
auth module can import it without a cycle.
"""

from typing import Any, Dict, Optional

# The one credential field every login reader verifies against. ``passwordHash``
# is a legacy name kept only as a read fallback for documents that never had
# the canonical field; every write sets the canonical field and removes the
# legacy one, so a document can never carry two diverging hashes again.
PASSWORD_HASH_FIELD = "hashed_password"
LEGACY_PASSWORD_HASH_FIELD = "passwordHash"


def stored_password_hash(doc: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return the credential login verifies, resolved the same way login does."""
    doc = doc or {}
    return doc.get(PASSWORD_HASH_FIELD) or doc.get(LEGACY_PASSWORD_HASH_FIELD)


def password_hash_update(new_hash: str) -> Dict[str, Any]:
    """Mongo update that replaces the credential and drops the legacy copy."""
    return {
        "$set": {PASSWORD_HASH_FIELD: new_hash},
        "$unset": {LEGACY_PASSWORD_HASH_FIELD: ""},
    }
