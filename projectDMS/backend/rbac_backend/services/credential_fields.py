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
PLAINTEXT_PASSWORD_FIELD = "password"  # must never exist; removed by every credential write

# Credential generation. Every credential write increments it in the SAME Mongo
# update that replaces the hash, and every access/step-up token carries the
# generation it was minted under as ``cred_v``. A token whose ``cred_v`` differs
# from the user's current generation is stale, whatever Redis says. Absent means
# generation 0 (users that predate the field); a token without ``cred_v`` is
# generation 0, so it stays valid only until the first credential change.
CREDENTIAL_VERSION_FIELD = "credential_version"
CREDENTIAL_VERSION_CLAIM = "cred_v"
NEW_USER_CREDENTIAL_VERSION = 1


def credential_version(doc: Optional[Dict[str, Any]]) -> int:
    """The user's current credential generation (0 when the field is absent)."""
    value = (doc or {}).get(CREDENTIAL_VERSION_FIELD)
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("credential_version must be a non-negative integer")
    return value


def token_credential_version(payload: Dict[str, Any]) -> Optional[int]:
    """The generation a token was minted under; None when the claim is malformed."""
    value = payload.get(CREDENTIAL_VERSION_CLAIM, 0)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def credential_version_filter(version: int) -> Dict[str, Any]:
    """Mongo filter matching a user still at ``version`` (compare-and-set guard).

    Generation 0 is also the absent field (``None`` in ``$in`` matches a missing
    field), so a pre-field user can be guarded the same way.
    """
    if version == 0:
        return {CREDENTIAL_VERSION_FIELD: {"$in": [0, None]}}
    return {CREDENTIAL_VERSION_FIELD: version}


def stored_password_hash(doc: Optional[Dict[str, Any]]) -> Optional[str]:
    """Return the credential login verifies, resolved the same way login does."""
    doc = doc or {}
    return doc.get(PASSWORD_HASH_FIELD) or doc.get(LEGACY_PASSWORD_HASH_FIELD)


def password_hash_update(new_hash: str) -> Dict[str, Any]:
    """One atomic Mongo update: new hash, no legacy or plaintext copy, next generation."""
    return {
        "$set": {PASSWORD_HASH_FIELD: new_hash},
        "$unset": {LEGACY_PASSWORD_HASH_FIELD: "", PLAINTEXT_PASSWORD_FIELD: ""},
        "$inc": {CREDENTIAL_VERSION_FIELD: 1},
    }
