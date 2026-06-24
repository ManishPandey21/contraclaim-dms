"""CSRF helpers for cookie-authenticated browser requests."""

from __future__ import annotations

import hmac
import secrets
from hashlib import sha256
from typing import Iterable
from urllib.parse import urlparse

from fastapi import HTTPException, Request, Response, status

from .config import settings

CSRF_COOKIE_NAME = "cc_csrf_token"
CSRF_HEADER_NAME = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
EXEMPT_PATHS = {
    "/api/login",
    "/api/contact",
    # Logout must not depend on a valid CSRF token: CSRF on logout is low risk
    # (worst case it just signs the user out), and gating it means a stale/missing
    # CSRF cookie leaves the user unable to log out. It only ever clears cookies.
    "/api/logout",
}


def _sign(value: str) -> str:
    key = str(settings.SECRET_KEY or "").encode("utf-8")
    return hmac.new(key, value.encode("utf-8"), sha256).hexdigest()


def create_csrf_token() -> str:
    nonce = secrets.token_urlsafe(32)
    return f"{nonce}.{_sign(nonce)}"


def validate_csrf_token(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    nonce, signature = token.rsplit(".", 1)
    if not nonce or not signature:
        return False
    return hmac.compare_digest(signature, _sign(nonce))


def set_csrf_cookie(response: Response, token: str | None = None, max_age: int | None = None) -> str:
    value = token or create_csrf_token()
    response.set_cookie(
        key=CSRF_COOKIE_NAME,
        value=value,
        max_age=max_age,
        httponly=False,
        secure=bool(settings.AUTH_COOKIE_SECURE),
        samesite=str(settings.AUTH_COOKIE_SAMESITE or "lax").lower(),
        domain=settings.AUTH_COOKIE_DOMAIN,
        path="/",
    )
    return value


def clear_csrf_cookie(response: Response) -> None:
    response.delete_cookie(
        key=CSRF_COOKIE_NAME,
        domain=settings.AUTH_COOKIE_DOMAIN,
        path="/",
    )
    # Also clear a host-only cookie set before AUTH_COOKIE_DOMAIN was configured.
    if settings.AUTH_COOKIE_DOMAIN:
        response.delete_cookie(key=CSRF_COOKIE_NAME, path="/")


def _normalized_origins(origins: Iterable[str]) -> set[str]:
    normalized: set[str] = set()
    for origin in origins or []:
        text = str(origin or "").strip().rstrip("/")
        if text:
            normalized.add(text)
    return normalized


def _same_origin(request: Request, origin: str) -> bool:
    parsed = urlparse(origin)
    if not parsed.scheme or not parsed.netloc:
        return False
    host = request.headers.get("host", "")
    return parsed.netloc.lower() == host.lower() and parsed.scheme.lower() == request.url.scheme.lower()


def validate_unsafe_cookie_request(request: Request) -> None:
    """Reject unsafe cookie-authenticated browser requests without a valid CSRF token."""

    if request.method.upper() in SAFE_METHODS:
        return

    path = request.url.path.rstrip("/") or "/"
    if path in EXEMPT_PATHS:
        return

    if not request.cookies.get(settings.AUTH_COOKIE_NAME):
        return

    origin = request.headers.get("origin")
    if origin:
        allowed = _normalized_origins(settings.CORS_ORIGINS)
        normalized_origin = origin.rstrip("/")
        if normalized_origin not in allowed and not _same_origin(request, normalized_origin):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Invalid request origin",
            )

    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    header_token = request.headers.get(CSRF_HEADER_NAME)
    if not cookie_token or not header_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing CSRF token",
        )
    if not hmac.compare_digest(cookie_token, header_token) or not validate_csrf_token(cookie_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid CSRF token",
        )
