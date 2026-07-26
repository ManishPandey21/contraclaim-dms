"""SSO / OIDC (Phase 3 / M6) — unit tests for the verifiable parts.

The network flow (discovery, token exchange, JWKS verification) needs a live IdP
and is covered by manual verification, not here. These tests cover the pure
helpers and the deny-by-default find-or-provision logic.
"""

from __future__ import annotations

from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from rbac_backend.services.oidc_service import (
    OidcError,
    build_authorization_url,
    decode_oidc_id_token,
    email_domain_allowed,
    resolve_or_provision_user,
)


# --- Fakes ----------------------------------------------------------------


class _FakeUsers:
    def __init__(self, seed=None):
        self.docs = [dict(d) for d in (seed or [])]
        self.inserted = []

    async def find_one(self, query):
        for d in self.docs:
            if all(d.get(k) == v for k, v in query.items()):
                return dict(d)
        return None

    async def insert_one(self, doc):
        doc = dict(doc)
        doc["_id"] = f"u-{len(self.docs) + 1}"
        self.docs.append(doc)
        self.inserted.append(doc)
        return SimpleNamespace(inserted_id=doc["_id"])


class _FakeDB:
    def __init__(self, seed=None):
        self.users = _FakeUsers(seed)


# --- pure helpers ---------------------------------------------------------


def test_email_domain_allowlist():
    assert email_domain_allowed("a@anything.com", "") is True  # empty = allow all
    assert email_domain_allowed("a@example.com", "example.com") is True
    assert email_domain_allowed("a@other.com", "example.com") is False
    assert email_domain_allowed("a@foo.com", "example.com, foo.com") is True


def test_build_authorization_url_has_required_params():
    url = build_authorization_url(
        "https://idp.example.com/authorize",
        client_id="cid",
        redirect_uri="https://app/cb",
        scopes="openid email",
        state="st8",
        nonce="nc9",
    )
    assert url.startswith("https://idp.example.com/authorize?")
    assert "response_type=code" in url
    assert "client_id=cid" in url
    assert "state=st8" in url
    assert "nonce=nc9" in url
    assert "redirect_uri=https%3A%2F%2Fapp%2Fcb" in url


def test_decode_oidc_id_token_selects_kid_and_verifies_claims():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    public_jwk.update({"kid": "signing-key-1", "alg": "RS256", "use": "sig"})
    token = jwt.encode(
        {
            "sub": "idp-user-1",
            "email": "legal@example.com",
            "aud": "client-id",
            "iss": "https://idp.example.com",
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "signing-key-1"},
    )

    claims = decode_oidc_id_token(
        token,
        {"keys": [public_jwk]},
        audience="client-id",
        issuer="https://idp.example.com",
    )

    assert claims["sub"] == "idp-user-1"


def test_decode_oidc_id_token_rejects_unknown_key_id():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(
        {"sub": "idp-user-1", "aud": "client-id", "iss": "https://idp.example.com"},
        private_key,
        algorithm="RS256",
        headers={"kid": "unknown"},
    )
    with pytest.raises(OidcError, match="not found"):
        decode_oidc_id_token(
            token,
            {"keys": []},
            audience="client-id",
            issuer="https://idp.example.com",
        )


# --- find-or-provision ----------------------------------------------------


@pytest.mark.asyncio
async def test_existing_user_is_returned_not_reprovisioned():
    db = _FakeDB(seed=[{"_id": "u-existing", "email": "a@corp.com", "roles": ["orgadmin"], "disabled": False}])
    user = await resolve_or_provision_user(db, {"email": "a@corp.com"})
    assert user["_id"] == "u-existing"
    assert user["roles"] == ["orgadmin"]
    assert db.users.inserted == []


@pytest.mark.asyncio
async def test_disabled_user_is_denied():
    db = _FakeDB(seed=[{"email": "a@corp.com", "disabled": True}])
    with pytest.raises(OidcError):
        await resolve_or_provision_user(db, {"email": "a@corp.com"})


@pytest.mark.asyncio
async def test_new_user_is_provisioned_with_defaults():
    db = _FakeDB()
    user = await resolve_or_provision_user(
        db,
        {"email": "New.User@corp.com", "given_name": "New", "family_name": "User", "sub": "idp|123"},
        default_role="orguser",
        default_org_id="org-1",
    )
    assert user["email"] == "new.user@corp.com"  # normalized
    assert user["roles"] == ["orguser"]
    assert user["organization_id"] == "org-1"
    assert user["is_verified"] is True
    assert user["hashed_password"] == "!sso-no-password"  # password login disabled
    assert user["sso_provider"] == "oidc"
    assert len(db.users.inserted) == 1


@pytest.mark.asyncio
async def test_no_account_without_auto_provision_is_denied():
    db = _FakeDB()
    with pytest.raises(OidcError):
        await resolve_or_provision_user(db, {"email": "a@corp.com"}, auto_provision=False)


@pytest.mark.asyncio
async def test_domain_not_allowed_is_denied():
    db = _FakeDB()
    with pytest.raises(OidcError):
        await resolve_or_provision_user(db, {"email": "a@evil.com"}, allowed_domains="corp.com")


@pytest.mark.asyncio
async def test_missing_email_is_denied():
    db = _FakeDB()
    with pytest.raises(OidcError):
        await resolve_or_provision_user(db, {"sub": "idp|nomail"})
