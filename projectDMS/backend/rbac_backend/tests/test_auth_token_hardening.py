"""Access-token hardening (Week 2.4 + 2.5).

W2.4 — every access token carries ``type="access"``; tokens that are not access
tokens (notably step-up tokens, which share the signing key) must never
authenticate a session.

W2.5 — a logged-out / invalidated session must stop authenticating immediately,
even within the token TTL. ``get_current_user`` checks session liveness when a
Redis session store is present.
"""

from __future__ import annotations

import time
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import rbac_backend.services.authentication_service as auth_mod
import rbac_backend.services.runtime_state as runtime_mod
from rbac_backend.core.security import CurrentUser, create_access_token, get_current_user
from rbac_backend.services.authentication_service import AuthenticationService
from rbac_backend.services.step_up_service import StepUpService
import jwt
from rbac_backend.core.config import settings


# --- Fakes ----------------------------------------------------------------


class _FakeUsers:
    def __init__(self, doc):
        self._doc = doc

    async def find_one(self, _query):
        return self._doc


class _NoRevokedRoles:
    """`get_current_user` asks for soft-deleted role references (R-A9B); none here."""

    def find(self, *_args, **_kwargs):
        class _Cursor:
            async def to_list(self, length=None):
                return []

        return _Cursor()


class _FakeDB:
    def __init__(self, user_doc):
        self.users = _FakeUsers(user_doc)
        self.roles = _NoRevokedRoles()


class _FakeRequest:
    def __init__(self, headers=None, cookies=None):
        self.headers = headers or {}
        self.cookies = cookies or {}


class _FakeRedis:
    def __init__(self, *, session_exists: bool):
        self._session_exists = session_exists

    async def get(self, _key):
        return None  # no min_iat floor configured

    async def exists(self, _key):
        return 1 if self._session_exists else 0


class _FakeRuntime:
    def __init__(self, redis):
        self._redis = redis

    @property
    def redis_url(self):
        return "redis://test-runtime"

    async def get_redis(self):
        return self._redis


_USER_DOC = {
    "_id": "user-1",
    "email": "a@example.com",
    "username": "a",
    "roles": ["orguser"],
    "organization_id": "org-A",
    "projects": ["proj-A"],
}


def _patch_runtime(monkeypatch, redis):
    runtime = _FakeRuntime(redis)
    monkeypatch.setattr(runtime_mod, "get_runtime_state", lambda: runtime)
    monkeypatch.setattr(auth_mod, "get_runtime_state", lambda: runtime)


# --- W2.4: JWT type claim -------------------------------------------------


def test_access_token_is_stamped_with_type_access():
    token = create_access_token({"sub": "a@example.com", "user_id": "user-1"})
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    assert payload["type"] == "access"


# --- Regression: min_iat invalidation must not nuke freshly-minted tokens -----


class _MinIatRedis:
    """Redis fake exposing a ``user_jwt_min_iat`` floor and an active session."""

    def __init__(self, min_iat: int):
        self._min_iat = str(int(min_iat)).encode()

    async def get(self, key):
        return self._min_iat if "user_jwt_min_iat" in str(key) else None

    async def exists(self, _key):
        return 1  # session is active; isolate the min_iat behaviour


def test_access_token_is_stamped_with_iat():
    # Without an iat claim, get_current_user reads iat=0 and any min_iat marker
    # rejects every token. The token must carry an issued-at timestamp.
    token = create_access_token({"sub": "a@example.com", "user_id": "user-1"})
    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    assert "iat" in payload
    assert abs(int(payload["iat"]) - int(time.time())) < 60


@pytest.mark.asyncio
async def test_fresh_token_survives_past_min_iat_floor(monkeypatch):
    # A min_iat marker set an hour ago must NOT reject a token minted now.
    past_floor = int(time.time()) - 3600
    _patch_runtime(monkeypatch, _MinIatRedis(past_floor))
    token = create_access_token(
        {"sub": "a@example.com", "user_id": "user-1", "roles": ["orguser"], "session_id": "sess-1"}
    )
    request = _FakeRequest(headers={"authorization": f"Bearer {token}"})
    result = await get_current_user(request, _FakeDB(_USER_DOC))
    assert isinstance(result, CurrentUser)


@pytest.mark.asyncio
async def test_token_predating_min_iat_floor_is_rejected(monkeypatch):
    # Invalidation still works: a token issued before the floor is rejected.
    future_floor = int(time.time()) + 3600
    _patch_runtime(monkeypatch, _MinIatRedis(future_floor))
    token = create_access_token(
        {"sub": "a@example.com", "user_id": "user-1", "roles": ["orguser"], "session_id": "sess-1"}
    )
    request = _FakeRequest(headers={"authorization": f"Bearer {token}"})
    with pytest.raises(HTTPException) as exc:
        await get_current_user(request, _FakeDB(_USER_DOC))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_step_up_token_cannot_authenticate_session():
    step_up = StepUpService().create_token(user_id="user-1", action="*")
    request = _FakeRequest(headers={"authorization": f"Bearer {step_up}"})
    with pytest.raises(HTTPException) as exc:
        await get_current_user(request, _FakeDB(_USER_DOC))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_non_access_type_token_is_rejected():
    forged = jwt.encode(
        {"sub": "a@example.com", "type": "refresh"},
        settings.SECRET_KEY,
        algorithm=settings.ALGORITHM,
    )
    request = _FakeRequest(headers={"authorization": f"Bearer {forged}"})
    with pytest.raises(HTTPException) as exc:
        await get_current_user(request, _FakeDB(_USER_DOC))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_valid_access_token_authenticates_without_redis(monkeypatch):
    runtime = SimpleNamespace(redis_url=None)
    monkeypatch.setattr(runtime_mod, "get_runtime_state", lambda: runtime)
    token = create_access_token({"sub": "a@example.com", "user_id": "user-1", "roles": ["orguser"]})
    request = _FakeRequest(headers={"authorization": f"Bearer {token}"})
    # No Redis configured -> session/min_iat checks are skipped.
    result = await get_current_user(request, _FakeDB(_USER_DOC))
    assert isinstance(result, CurrentUser)
    assert result.email == "a@example.com"


# --- W2.5: session invalidation -------------------------------------------


@pytest.mark.asyncio
async def test_session_lifecycle_invalidate_makes_inactive():
    svc = AuthenticationService()
    svc._runtime_state = _FakeRuntime(_InMemoryRedis())
    token = await svc.create_user_session("user-1", "1.2.3.4", "agent", timedelta(minutes=60))
    assert await svc.is_session_active(token) is True
    await svc.invalidate_session(token)
    assert await svc.is_session_active(token) is False


@pytest.mark.asyncio
async def test_get_current_user_rejects_invalidated_session(monkeypatch):
    _patch_runtime(monkeypatch, _FakeRedis(session_exists=False))
    token = create_access_token(
        {"sub": "a@example.com", "user_id": "user-1", "roles": ["orguser"], "session_id": "sess-1"}
    )
    request = _FakeRequest(headers={"authorization": f"Bearer {token}"})
    with pytest.raises(HTTPException) as exc:
        await get_current_user(request, _FakeDB(_USER_DOC))
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_accepts_active_session(monkeypatch):
    _patch_runtime(monkeypatch, _FakeRedis(session_exists=True))
    token = create_access_token(
        {"sub": "a@example.com", "user_id": "user-1", "roles": ["orguser"], "session_id": "sess-1"}
    )
    request = _FakeRequest(headers={"authorization": f"Bearer {token}"})
    result = await get_current_user(request, _FakeDB(_USER_DOC))
    assert isinstance(result, CurrentUser)


class _InMemoryRedis:
    """A fuller fake used for the session-lifecycle test."""

    def __init__(self):
        self.store = {}
        self.sets = {}

    async def setex(self, key, _ttl, val):
        self.store[key] = val

    async def get(self, key):
        return self.store.get(key)

    async def exists(self, key):
        return 1 if key in self.store else 0

    async def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)

    async def sadd(self, key, *vals):
        self.sets.setdefault(key, set()).update(vals)

    async def expire(self, _key, _ttl):
        return None

    async def srem(self, key, *vals):
        self.sets.get(key, set()).difference_update(vals)


# --- Verifier security properties -----------------------------------------
#
# The session verifier is `jwt.decode(token, SECRET_KEY, algorithms=[HS256])`
# inside `get_current_user`. These pin what it must refuse, so a PyJWT upgrade
# cannot quietly relax any of it. Every one of them is a property the library
# is responsible for; none asserts a version.


def _unauthenticated(token: str) -> HTTPException:
    """Run `get_current_user` against `token` and return the refusal."""
    import asyncio

    request = _FakeRequest(headers={"authorization": f"Bearer {token}"})

    async def _run():
        with pytest.raises(HTTPException) as exc:
            await get_current_user(request, _FakeDB(_USER_DOC))
        return exc.value

    return asyncio.run(_run())


def _signed(header_extra: dict, payload: dict, *, key=None, algorithm: str = "HS256") -> str:
    return jwt.encode(
        payload,
        key if key is not None else settings.SECRET_KEY,
        algorithm=algorithm,
        headers=header_extra,
    )


def test_encode_decode_round_trip_preserves_every_claim():
    now = int(time.time())
    claims = {"sub": "a@example.com", "user_id": "user-1", "iat": now, "exp": now + 300}
    decoded = jwt.decode(
        jwt.encode(claims, settings.SECRET_KEY, algorithm=settings.ALGORITHM),
        settings.SECRET_KEY,
        algorithms=[settings.ALGORITHM],
    )
    assert decoded == claims


def test_a_token_with_an_unknown_critical_extension_is_refused():
    """RFC 7515 4.1.11: an extension listed in `crit` that the verifier does
    not understand makes the JWS invalid.

    PyJWT 2.10.1 accepts such a token (CVE-2026-32597 / PYSEC-2026-120), so a
    signer who can mint a token can also attach policy headers the verifier
    silently ignores — and a gateway that *does* understand `crit` disagrees
    with the backend about the same token.
    """
    now = int(time.time())
    token = _signed(
        {"crit": ["x-custom-policy"], "x-custom-policy": "require-mfa"},
        {
            "sub": "a@example.com",
            "user_id": "user-1",
            "type": "access",
            "iat": now,
            "exp": now + 300,
        },
    )

    assert _unauthenticated(token).status_code == 401


def test_an_expired_token_is_refused():
    now = int(time.time())
    token = _signed(
        {},
        {
            "sub": "a@example.com",
            "user_id": "user-1",
            "type": "access",
            "iat": now - 7200,
            "exp": now - 3600,
        },
    )

    assert _unauthenticated(token).status_code == 401


def test_a_token_signed_with_the_wrong_key_is_refused():
    now = int(time.time())
    token = _signed(
        {},
        {
            "sub": "a@example.com",
            "user_id": "user-1",
            "type": "access",
            "iat": now,
            "exp": now + 300,
        },
        key="not-the-application-signing-key-not-the-application-key",
    )

    assert _unauthenticated(token).status_code == 401


def test_a_token_signed_with_a_disallowed_algorithm_is_refused():
    """The verifier allows exactly one algorithm; HS512 over the same secret
    must not authenticate."""
    now = int(time.time())
    token = _signed(
        {},
        {
            "sub": "a@example.com",
            "user_id": "user-1",
            "type": "access",
            "iat": now,
            "exp": now + 300,
        },
        algorithm="HS512",
    )

    assert _unauthenticated(token).status_code == 401


def test_an_unsigned_token_is_refused():
    """`alg: none` is the original JWT verification bypass."""
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "a@example.com",
            "user_id": "user-1",
            "type": "access",
            "iat": now,
            "exp": now + 300,
        },
        key="",
        algorithm="none",
    )

    assert _unauthenticated(token).status_code == 401


@pytest.mark.parametrize(
    "token",
    [
        "not-a-token",
        "a.b",
        "a.b.c.d",
        "....",
        "eyJhbGciOiJIUzI1NiJ9..",
    ],
)
def test_a_malformed_token_is_refused(token):
    assert _unauthenticated(token).status_code == 401


def test_a_tampered_payload_is_refused():
    """Re-encoding the payload of a valid token invalidates its signature."""
    import base64
    import json as _json

    now = int(time.time())
    valid = create_access_token({"sub": "a@example.com", "user_id": "user-1"})
    header_b64, payload_b64, signature_b64 = valid.split(".")
    payload = _json.loads(base64.urlsafe_b64decode(payload_b64 + "=="))
    payload["sub"] = "attacker@example.com"
    tampered_payload = (
        base64.urlsafe_b64encode(_json.dumps(payload, separators=(",", ":")).encode())
        .rstrip(b"=")
        .decode()
    )
    assert now  # the token carries an iat; see test_access_token_is_stamped_with_iat

    assert _unauthenticated(f"{header_b64}.{tampered_payload}.{signature_b64}").status_code == 401


# --- OIDC id_token verification -------------------------------------------
#
# `test_sso.py` already covers the happy path and unknown-kid rejection. These
# add the claim checks the verifier delegates to PyJWT: audience, issuer, and
# the signature itself.


def _oidc_material():
    """An RS256 key pair and the matching public JWK set."""
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    public_jwk.update({"kid": "test-kid", "alg": "RS256", "use": "sig"})
    return private_key, {"keys": [public_jwk]}


def _id_token(private_key, *, audience: str, issuer: str):
    now = int(time.time())
    return jwt.encode(
        {
            "sub": "oidc-user",
            "email": "a@example.com",
            "aud": audience,
            "iss": issuer,
            "iat": now,
            "exp": now + 300,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-kid"},
    )


@pytest.mark.parametrize(
    "audience,issuer",
    [
        ("someone-else", "https://issuer.test"),
        ("client-1", "https://attacker.test"),
    ],
)
def test_oidc_id_token_with_a_wrong_audience_or_issuer_is_refused(audience, issuer):
    from rbac_backend.services.oidc_service import OidcError, decode_oidc_id_token

    private_key, jwks = _oidc_material()
    token = _id_token(private_key, audience=audience, issuer=issuer)

    with pytest.raises(OidcError):
        decode_oidc_id_token(token, jwks, audience="client-1", issuer="https://issuer.test")


def test_oidc_id_token_signed_by_a_foreign_key_is_refused():
    """The kid matches, so key selection succeeds; only the signature check
    stands between a foreign signer and a session."""
    from rbac_backend.services.oidc_service import OidcError, decode_oidc_id_token

    _, jwks = _oidc_material()
    foreign_key, _ = _oidc_material()
    token = _id_token(foreign_key, audience="client-1", issuer="https://issuer.test")

    with pytest.raises(OidcError):
        decode_oidc_id_token(token, jwks, audience="client-1", issuer="https://issuer.test")


# --- Properties the 2026-10-01 PyJWT refresh must keep --------------------
#
# PyJWT 2.13.0 -> 2.15.1 closes thirteen advisories. The verifiers above already
# pin signature, expiry, algorithm, `alg: none`, `crit` and malformed input.
# These pin the remaining decode semantics the application relies on, so the
# upgrade - or any later one - cannot relax them unnoticed.


def _nested_header_token(depth: int) -> str:
    """A token whose protected header is valid JSON nested `depth` levels deep."""
    import base64

    def _b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    header = '{"alg":"HS256","kid":"test-kid","x":' + "[" * depth + "]" * depth + "}"
    payload = '{"sub":"a@example.com","type":"access"}'
    return f"{_b64(header.encode())}.{_b64(payload.encode())}.c2ln"


def test_a_token_without_a_subject_is_refused():
    """A correctly signed, unexpired access token that names nobody must not
    authenticate - the subject is what the principal is built from."""
    now = int(time.time())
    token = _signed({}, {"user_id": "user-1", "type": "access", "iat": now, "exp": now + 300})

    assert _unauthenticated(token).status_code == 401


def test_a_token_signed_with_an_empty_key_is_refused():
    """A token HMAC-signed with an empty key is a forgery anyone can mint."""
    import hashlib
    import hmac
    import json as _json
    import base64

    def _b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    now = int(time.time())
    signing_input = (
        _b64(_json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        + "."
        + _b64(
            _json.dumps(
                {"sub": "a@example.com", "type": "access", "iat": now, "exp": now + 300}
            ).encode()
        )
    )
    signature = _b64(hmac.new(b"", signing_input.encode(), hashlib.sha256).digest())

    assert _unauthenticated(f"{signing_input}.{signature}").status_code == 401


def test_the_verifier_refuses_to_verify_against_an_empty_key():
    """If SECRET_KEY were ever empty, decoding must fail rather than accept a
    token signed with the same empty key."""
    now = int(time.time())
    with pytest.raises(jwt.PyJWTError):
        token = jwt.encode({"sub": "a", "exp": now + 300}, "", algorithm="HS256")
        jwt.decode(token, "", algorithms=["HS256"])


def test_a_deeply_nested_header_is_refused_not_crashed_on():
    """GHSA-8wjv-2p76-3863: PyJWT 2.13.0 let RecursionError escape decode on a
    deeply nested header. The session verifier must answer 401."""
    assert _unauthenticated(_nested_header_token(50_000)).status_code == 401


def test_oidc_refuses_a_deeply_nested_header_as_an_oidc_error():
    """The OIDC callback reads the unverified header before anything else and
    only translates PyJWTError, so a RecursionError there was a 500."""
    from rbac_backend.services.oidc_service import OidcError, decode_oidc_id_token

    _, jwks = _oidc_material()
    with pytest.raises(OidcError):
        decode_oidc_id_token(
            _nested_header_token(50_000),
            jwks,
            audience="client-1",
            issuer="https://issuer.test",
        )


def test_oidc_refuses_an_hmac_token_keyed_with_the_providers_public_key():
    """Algorithm confusion: an attacker signs HS256 using the provider's public
    RSA key as the HMAC secret. The allow-list must refuse it before any key
    is used."""
    from cryptography.hazmat.primitives import serialization

    from rbac_backend.services.oidc_service import OidcError, decode_oidc_id_token

    private_key, jwks = _oidc_material()
    public_pem = private_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    import base64
    import hashlib
    import hmac
    import json as _json

    def _b64(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    now = int(time.time())
    signing_input = (
        _b64(_json.dumps({"alg": "HS256", "kid": "test-kid"}).encode())
        + "."
        + _b64(
            _json.dumps(
                {
                    "sub": "oidc-user",
                    "email": "a@example.com",
                    "aud": "client-1",
                    "iss": "https://issuer.test",
                    "iat": now,
                    "exp": now + 300,
                }
            ).encode()
        )
    )
    forged = signing_input + "." + _b64(
        hmac.new(public_pem, signing_input.encode(), hashlib.sha256).digest()
    )

    with pytest.raises(OidcError):
        decode_oidc_id_token(forged, jwks, audience="client-1", issuer="https://issuer.test")


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Known gap, identical on PyJWT 2.13.0 and 2.15.1: decode_oidc_id_token does "
        "not pass options={'require': ['exp', 'iat']}, so an id_token without exp is "
        "accepted. Fixed separately from the dependency refresh; strict, so the fix "
        "must remove this marker."
    ),
)
def test_oidc_id_token_without_an_expiry_is_refused():
    """`exp` is mandatory for an id_token (OIDC Core 3.1.3.7); one that never
    expires must not establish a session."""
    from rbac_backend.services.oidc_service import OidcError, decode_oidc_id_token

    private_key, jwks = _oidc_material()
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": "oidc-user",
            "email": "a@example.com",
            "aud": "client-1",
            "iss": "https://issuer.test",
            "iat": now,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "test-kid"},
    )

    with pytest.raises(OidcError):
        decode_oidc_id_token(token, jwks, audience="client-1", issuer="https://issuer.test")


def test_a_step_up_token_for_one_user_cannot_authorise_another():
    service = StepUpService()
    token = service.create_token(user_id="user-1", action="*")

    service.verify_token(token=token, user_id="user-1", action="documents.delete")
    with pytest.raises(HTTPException) as exc:
        service.verify_token(token=token, user_id="user-2", action="documents.delete")
    assert exc.value.status_code == 403


def test_a_step_up_token_scoped_to_one_action_cannot_authorise_another():
    service = StepUpService()
    token = service.create_token(user_id="user-1", action="documents.delete")

    with pytest.raises(HTTPException) as exc:
        service.verify_token(token=token, user_id="user-1", action="users.delete")
    assert exc.value.status_code == 403
