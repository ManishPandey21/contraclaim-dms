"""One MongoDB transaction around each skill-publication decision (plan D-05a, 4B-05).

Recording an approval, revoking one, publishing and deprecating each write the
version document (``approval_revision``, or ``status``) inside the same
transaction as their other writes. Two of these acting on one version therefore
touch the same document, and MongoDB's write-conflict detection aborts one of
them: neither can commit on a stale view of the other. ``with_transaction``
retries the aborted callback, which then re-reads what the other one committed.

A standalone MongoDB server cannot run transactions. Then publication refuses
(503) instead of falling back to unserialised writes. Only an in-memory test
double, which has no client at all, runs the callback without a session.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Dict, Optional, TypeVar

from fastapi import HTTPException

from ..audit_event_service import AuditEventService

T = TypeVar("T")

#: The collection the decisions serialise on; the registry's SKILL_VERSIONS.
SKILL_VERSIONS = "skill_versions"


def session_kwargs(session: Optional[Any]) -> Dict[str, Any]:
    return {"session": session} if session is not None else {}


def _is_transactions_unsupported(exc: Exception) -> bool:
    text = str(exc)
    return (
        "Transaction numbers are only allowed on a replica set member or mongos" in text
        or "transactions are not supported" in text.lower()
        or getattr(exc, "code", None) == 20  # IllegalOperation
    )


async def run_in_transaction(db: Any, callback: Callable[[Optional[Any]], Awaitable[T]]) -> T:
    client = getattr(db, "client", None)
    start_session = getattr(client, "start_session", None)
    if not callable(start_session):
        # In-memory test double: no client, no concurrency, nothing to serialise.
        return await callback(None)
    session = await start_session()
    try:
        async with session:
            return await session.with_transaction(callback)
    except HTTPException:
        raise
    except Exception as exc:
        if _is_transactions_unsupported(exc):
            raise HTTPException(
                status_code=503,
                detail="Skill publication needs a MongoDB replica set; this server cannot run transactions",
            ) from exc
        raise


async def audit_skill_decision(
    db: Any,
    session: Optional[Any],
    *,
    action: str,
    actor_id: Optional[str],
    skill_key: str,
    version: str,
    metadata: Optional[Dict[str, Any]] = None,
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
) -> None:
    """Write the audit event for a publication decision inside its transaction (4B-05).

    The event commits or aborts with the decision it records: a retried callback
    writes it once more in the retry, and an aborted one leaves none behind.
    """
    await AuditEventService(db).emit(
        action=action,
        actor_id=actor_id or None,
        resource_type="skill_version",
        resource_id=f"{skill_key}@{version}",
        before=before,
        after=after,
        metadata={"skill_key": skill_key, "version": version, **(metadata or {})},
        session=session,
    )


__all__ = ["SKILL_VERSIONS", "audit_skill_decision", "run_in_transaction", "session_kwargs"]
