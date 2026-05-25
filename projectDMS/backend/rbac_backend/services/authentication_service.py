"""services/authentication_service.py

Improved authentication service that authenticates against MongoDB users collection
while keeping the original lightweight in-memory session handling so existing routers
continue to work.
"""

from __future__ import annotations

import secrets
import time
import json
from datetime import timedelta, datetime
from typing import Optional, Dict, Any

from ..core.database import get_database
from ..core.security import verify_password
from .runtime_state import get_runtime_state


class AuthenticationService:
    """Async-friendly authentication helper used by the auth router."""

    def __init__(self) -> None:
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._failed_attempts: Dict[str, int] = {}
        self._locked_accounts: Dict[str, float] = {}
        self._db = None
        self._runtime_state = get_runtime_state()

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
        redis = await self._runtime_state.get_redis()
        if redis is not None:
            return bool(await redis.exists(self._lock_key(str(user_id))))
        until = self._locked_accounts.get(str(user_id))
        return bool(until and until > time.time())

    async def increment_failed_attempts(self, user_id: str) -> None:
        uid = str(user_id)
        redis = await self._runtime_state.get_redis()
        if redis is not None:
            attempts = await redis.incr(self._failed_key(uid))
            if int(attempts) == 1:
                await redis.expire(self._failed_key(uid), 3600)
            if int(attempts) >= 5:
                await redis.setex(self._lock_key(uid), 5 * 60, "1")
            return
        self._failed_attempts[uid] = self._failed_attempts.get(uid, 0) + 1
        if self._failed_attempts[uid] >= 5:
            self._locked_accounts[uid] = time.time() + 5 * 60

    async def reset_failed_attempts(self, user_id: str) -> None:
        redis = await self._runtime_state.get_redis()
        if redis is not None:
            await redis.delete(self._failed_key(str(user_id)), self._lock_key(str(user_id)))
            return
        self._failed_attempts.pop(str(user_id), None)

    async def lock_user_account(self, user_id: str) -> None:
        redis = await self._runtime_state.get_redis()
        if redis is not None:
            await redis.setex(self._lock_key(str(user_id)), 60 * 60, "1")
            return
        self._locked_accounts[str(user_id)] = time.time() + 60 * 60

    async def unlock_user_account(self, user_id: str) -> None:
        redis = await self._runtime_state.get_redis()
        if redis is not None:
            await redis.delete(self._lock_key(str(user_id)), self._failed_key(str(user_id)))
            return
        self._locked_accounts.pop(str(user_id), None)

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------
    async def create_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + timedelta(minutes=60)
        redis = await self._runtime_state.get_redis()
        if redis is not None:
            await redis.setex(
                self._session_key(token),
                60 * 60,
                json.dumps({"user_id": str(user_id), "expires_at": expires_at.isoformat()}),
            )
            await redis.sadd(self._user_sessions_key(str(user_id)), token)
            await redis.expire(self._user_sessions_key(str(user_id)), 60 * 60)
            return token
        self._sessions[token] = {"user_id": str(user_id), "expires_at": expires_at}
        return token

    async def create_user_session(
        self, user_id: str, client_ip: str, user_agent: str, expires_delta: timedelta
    ) -> str:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.utcnow() + (expires_delta or timedelta(minutes=60))
        ttl = max(1, int((expires_delta or timedelta(minutes=60)).total_seconds()))
        session_data = {
            "user_id": str(user_id),
            "client_ip": client_ip,
            "user_agent": user_agent,
            "expires_at": expires_at.isoformat(),
        }
        redis = await self._runtime_state.get_redis()
        if redis is not None:
            await redis.setex(self._session_key(token), ttl, json.dumps(session_data))
            await redis.sadd(self._user_sessions_key(str(user_id)), token)
            await redis.expire(self._user_sessions_key(str(user_id)), ttl)
            return token
        self._sessions[token] = {
            "user_id": str(user_id),
            "client_ip": client_ip,
            "user_agent": user_agent,
            "expires_at": expires_at,
        }
        return token

    async def is_session_active(self, session_token: str) -> bool:
        redis = await self._runtime_state.get_redis()
        if redis is not None:
            return bool(await redis.exists(self._session_key(session_token)))
        data = self._sessions.get(session_token)
        if not data:
            return False
        return data.get("expires_at", datetime.utcnow()) > datetime.utcnow()

    async def extend_session(self, session_token: str, expires_delta: timedelta) -> None:
        redis = await self._runtime_state.get_redis()
        ttl = max(1, int((expires_delta or timedelta(minutes=60)).total_seconds()))
        if redis is not None:
            raw = await redis.get(self._session_key(session_token))
            if not raw:
                return
            try:
                data = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
            except Exception:
                data = {}
            data["expires_at"] = (datetime.utcnow() + (expires_delta or timedelta(minutes=60))).isoformat()
            await redis.setex(self._session_key(session_token), ttl, json.dumps(data))
            user_id = str(data.get("user_id") or "")
            if user_id:
                await redis.sadd(self._user_sessions_key(user_id), session_token)
                await redis.expire(self._user_sessions_key(user_id), ttl)
            return
        data = self._sessions.get(session_token)
        if not data:
            return
        data["expires_at"] = datetime.utcnow() + (expires_delta or timedelta(minutes=60))

    async def invalidate_session(self, session_token: str) -> None:
        redis = await self._runtime_state.get_redis()
        if redis is not None:
            raw = await redis.get(self._session_key(session_token))
            if raw:
                try:
                    data = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
                    user_id = str(data.get("user_id") or "")
                    if user_id:
                        await redis.srem(self._user_sessions_key(user_id), session_token)
                except Exception:
                    pass
            await redis.delete(self._session_key(session_token))
            return
        self._sessions.pop(session_token, None)

    async def invalidate_user_sessions(self, user_id: str) -> None:
        uid = str(user_id)
        redis = await self._runtime_state.get_redis()
        if redis is not None:
            tokens = await redis.smembers(self._user_sessions_key(uid))
            keys = [self._session_key(token.decode("utf-8") if isinstance(token, bytes) else str(token)) for token in tokens]
            if keys:
                await redis.delete(*keys)
            await redis.delete(self._user_sessions_key(uid))
            return
        to_remove = [token for token, data in self._sessions.items() if data.get("user_id") == uid]
        for token in to_remove:
            self._sessions.pop(token, None)

    @staticmethod
    def _session_key(token: str) -> str:
        return f"auth:session:{token}"

    @staticmethod
    def _user_sessions_key(user_id: str) -> str:
        return f"auth:user_sessions:{user_id}"

    @staticmethod
    def _failed_key(user_id: str) -> str:
        return f"auth:failed_attempts:{user_id}"

    @staticmethod
    def _lock_key(user_id: str) -> str:
        return f"auth:locked:{user_id}"
