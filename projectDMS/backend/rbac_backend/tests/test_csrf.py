from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from rbac_backend.core import csrf
from rbac_backend.core.config import settings


class _Url:
    path = "/api/documents"
    scheme = "http"


class _Request:
    method = "POST"
    url = _Url()
    headers = {"host": "localhost:8000", "origin": "http://localhost:8000"}

    def __init__(self, cookies):
        self.cookies = cookies


def test_csrf_token_roundtrip_validates():
    token = csrf.create_csrf_token()

    assert csrf.validate_csrf_token(token)


def test_logout_is_csrf_exempt():
    # A logout POST without a CSRF token must not be rejected — it only clears
    # cookies, and gating it would leave users unable to log out.
    request = SimpleNamespace(
        method="POST",
        url=SimpleNamespace(path="/api/logout", scheme="http"),
        headers={"host": "localhost:8000", "origin": "http://localhost:8000"},
        cookies={settings.AUTH_COOKIE_NAME: "session"},
    )
    assert csrf.validate_unsafe_cookie_request(request) is None


def test_csrf_rejects_missing_header_for_cookie_auth():
    request = _Request({settings.AUTH_COOKIE_NAME: "session"})

    with pytest.raises(HTTPException) as exc:
        csrf.validate_unsafe_cookie_request(request)

    assert exc.value.status_code == 403
    assert exc.value.detail == "Missing CSRF token"


def test_csrf_accepts_matching_cookie_and_header_for_cookie_auth():
    token = csrf.create_csrf_token()
    request = _Request(
        {
            settings.AUTH_COOKIE_NAME: "session",
            csrf.CSRF_COOKIE_NAME: token,
        }
    )
    request.headers = {
        **request.headers,
        csrf.CSRF_HEADER_NAME: token,
    }

    csrf.validate_unsafe_cookie_request(request)


def test_csrf_skips_bearer_only_api_requests():
    request = _Request({})

    csrf.validate_unsafe_cookie_request(request)
