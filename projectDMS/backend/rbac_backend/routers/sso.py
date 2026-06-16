"""SSO (generic OIDC) endpoints (Phase 3 / M6).

  GET /api/auth/sso/login     -> redirect to the IdP (authorization-code flow)
  GET /api/auth/sso/callback  -> verify, find/provision the user, issue session

Intentionally unauthenticated (this IS the login flow); state + nonce are stored
in short-lived HttpOnly cookies and validated on callback (CSRF/replay defense).
On success the standard session cookie is issued so SSO users use the same RBAC
and session machinery as password users.
"""

from __future__ import annotations

import logging
import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

from ..core.config import settings
from ..core.database import get_db
from ..core.security import create_access_token
from ..services.authentication_service import AuthenticationService
from ..services.oidc_service import OidcError, OidcService, resolve_or_provision_user
from .auth import _set_auth_cookie

logger = logging.getLogger(__name__)
router = APIRouter()

_STATE_COOKIE = "oidc_state"
_NONCE_COOKIE = "oidc_nonce"
_STATE_TTL = 600  # seconds


def _require_enabled() -> None:
    if not settings.OIDC_ENABLED:
        raise HTTPException(status_code=404, detail="SSO is not enabled")


@router.get("/auth/sso/login")
async def sso_login():
    _require_enabled()
    state = secrets.token_urlsafe(24)
    nonce = secrets.token_urlsafe(24)
    try:
        url = await OidcService().authorization_url(state, nonce)
    except Exception as exc:  # noqa: BLE001
        logger.error("OIDC authorization URL failed: %s", exc)
        raise HTTPException(status_code=502, detail="SSO provider is unavailable")

    response = RedirectResponse(url, status_code=307)
    cookie_kwargs = dict(
        max_age=_STATE_TTL,
        httponly=True,
        secure=bool(settings.AUTH_COOKIE_SECURE),
        samesite="lax",
        path="/",
    )
    response.set_cookie(_STATE_COOKIE, state, **cookie_kwargs)
    response.set_cookie(_NONCE_COOKIE, nonce, **cookie_kwargs)
    return response


@router.get("/auth/sso/callback")
async def sso_callback(
    request: Request,
    code: str = Query(...),
    state: str = Query(...),
    db=Depends(get_db),
):
    _require_enabled()
    cookie_state = request.cookies.get(_STATE_COOKIE)
    cookie_nonce = request.cookies.get(_NONCE_COOKIE)
    if not cookie_state or not secrets.compare_digest(cookie_state, state):
        raise HTTPException(status_code=400, detail="Invalid or expired SSO state")

    try:
        claims = await OidcService().exchange_code_for_claims(code, cookie_nonce)
        user = await resolve_or_provision_user(
            db,
            claims,
            allowed_domains=settings.OIDC_ALLOWED_EMAIL_DOMAINS,
            auto_provision=settings.OIDC_AUTO_PROVISION,
            default_role=settings.OIDC_DEFAULT_ROLE,
            default_org_id=settings.OIDC_DEFAULT_ORG_ID or None,
        )
    except OidcError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.error("OIDC callback failed: %s", exc)
        raise HTTPException(status_code=502, detail="SSO sign-in failed")

    user_id = str(user.get("_id") or user.get("id") or "")
    expires = timedelta(minutes=60)
    auth_service = AuthenticationService()
    session_id = await auth_service.create_user_session(
        user_id,
        request.client.host if request.client else "sso",
        request.headers.get("user-agent", "sso"),
        expires,
    )
    token = create_access_token(
        {
            "sub": user.get("email"),
            "user_id": user_id,
            "roles": user.get("roles", []),
            "session_id": session_id,
        },
        expires_delta=expires,
    )

    redirect = RedirectResponse(settings.OIDC_POST_LOGIN_REDIRECT or "/", status_code=307)
    _set_auth_cookie(redirect, token, 3600)
    redirect.delete_cookie(_STATE_COOKIE, path="/")
    redirect.delete_cookie(_NONCE_COOKIE, path="/")
    return redirect
