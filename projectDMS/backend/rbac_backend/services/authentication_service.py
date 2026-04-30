"""services/authentication_service.py

Improved authentication service that authenticates against MongoDB users collection
while keeping the original lightweight in-memory session handling so existing routers
continue to work.
"""

from __future__ import annotations

import secrets
import time
from datetime import timedelta, datetime
from typing import Optional, Dict, Any

from ..core.database import get_database
from ..core.security import verify_password


class AuthenticationService:
    """Async-friendly authentication helper used by the auth router."""

    def __init__(self) -> None:
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._failed_attempts: Dict[str, int] = {}
        self._locked_accounts: Dict[str, float] = {}
        self._db = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    async def _get_db(self):
        if self._db is None:
            self._db = await get_database()
        return self._db

    def _to_light_user(self, doc: Dict[str, Any]):
        class _User:
            pass

        user = _User()
        user.id = str(doc.get("_id") or doc.get("id") or "")
        user.email = doc.get("email")
        user.username = doc.get("username") or (user.email.split("@")[0] if user.email else "")
        user.roles = doc.get("roles", [])
        user.disabled = bool(doc.get("disabled", False))
        user.organization_id = doc.get("organization_id")
        user.projects = doc.get("projects", [])
        user.preferences = doc.get("preferences", {})
        user.created_at = doc.get("created_at")
        user.last_login = doc.get("last_login")
        # Preserve hashed password for subsequent checks if needed
        user.hashed_password = doc.get("hashed_password") or doc.get("passwordHash") or ""
        return user

    # ------------------------------------------------------------------
    # Credentials & account security
    # ------------------------------------------------------------------
    async def authenticate_user_secure(self, email: str, password: str) -> Optional[Any]:
        if not email or not password:
            return None

        db = await self._get_db()
        doc = await db.users.find_one({"email": email})
        if not doc:
            return None

        hashed = doc.get("hashed_password") or doc.get("passwordHash")
        if not hashed or not verify_password(password, hashed):
            return None

        return self._to_light_user(doc)

    async def verify_password_secure(self, plain_password: str, hashed_password: str) -> bool:
        try:
            return verify_password(plain_password, hashed_password)
        except Exception:
            return False

    async def is_account_locked(self, user_id: str) -> bool:
        until = self._locked_accounts.get(str(user_id))
        return bool(until and until > time.time())

    async def increment_failed_attempts(self, user_id: str) -> None:
        uid = str(user_id)
        self._failed_attempts[uid] = self._failed_attempts.get(uid, 0) + 1
        if self._failed_attempts[uid] >= 5:
            self._locked_accounts[uid] = time.time() + 5 * 60

    async def reset_failed_attempts(self, user_id: str) -> None:
        self._failed_attempts.pop(str(user_id), None)

    async def lock_user_account(self, user_id: str) -> None:
        self._locked_accounts[str(user_id)] = time.time() + 60 * 60

    async def unlock_user_account(self, user_id: str) -> None:
        self._locked_accounts.pop(str(user_id), None)

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------
    async def create_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(minutes=60)
        self._sessions[token] = {"user_id": str(user_id), "expires_at": expires_at}
        return token

    async def create_user_session(
        self, user_id: str, client_ip: str, user_agent: str, expires_delta: timedelta
    ) -> str:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + (expires_delta or timedelta(minutes=60))
        self._sessions[token] = {
            "user_id": str(user_id),
            "client_ip": client_ip,
            "user_agent": user_agent,
            "expires_at": expires_at,
        }
        return token

    async def is_session_active(self, session_token: str) -> bool:
        data = self._sessions.get(session_token)
        if not data:
            return False
        return data.get("expires_at", datetime.utcnow()) > datetime.utcnow()

    async def extend_session(self, session_token: str, expires_delta: timedelta) -> None:
        data = self._sessions.get(session_token)
        if not data:
            return
        data["expires_at"] = datetime.utcnow() + (expires_delta or timedelta(minutes=60))

    async def invalidate_session(self, session_token: str) -> None:
        self._sessions.pop(session_token, None)

    async def invalidate_user_sessions(self, user_id: str) -> None:
        uid = str(user_id)
        to_remove = [token for token, data in self._sessions.items() if data.get("user_id") == uid]
        for token in to_remove:
            self._sessions.pop(token, None)
