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


def _matches(doc, query):
    for key, condition in query.items():
        if key == "$or":
            if not any(_matches(doc, branch) for branch in condition):
                return False
        elif isinstance(condition, dict) and "$in" in condition:
            if str(doc.get(key)) not in {str(value) for value in condition["$in"]}:
                return False
        elif str(doc.get(key)) != str(condition):
            return False
    return True


class _FakeRoles:
    def __init__(self, docs):
        self.docs = [dict(d) for d in docs]

    async def find_one(self, query, *_args, **_kwargs):
        return next((dict(d) for d in self.docs if _matches(d, query)), None)

    def find(self, query=None, *_args, **_kwargs):
        matched = [dict(d) for d in self.docs if _matches(d, query or {})]

        class _Cursor:
            async def to_list(self, length=None):
                return matched

        return _Cursor()


#: The release `orguser` shape: an organisation-scope canonical role.
ORGUSER = {"_id": "orguser", "name": "Organization - User", "scope": "organization", "is_system": True, "is_active": True}


class _FakeDB:
    def __init__(self, seed=None, roles=None):
        self.users = _FakeUsers(seed)
        self.roles = _FakeRoles([ORGUSER] if roles is None else roles)


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


# --- OIDC_DEFAULT_ROLE (R-A9D) ----------------------------------------------
#
# A provisioned SSO user is stored with `OIDC_DEFAULT_ROLE` verbatim. The principal
# normalises role strings (`super-admin` -> `superadmin`) and ADR 0001 makes Super
# Admin authority name-based, so an unvalidated setting could mint a Super Admin on
# every first SSO login. The default role must be a canonical, non-system role key
# naming exactly one active role document; anything else refuses provisioning.

SUPERADMIN_DOC = {"_id": "superadmin", "name": "Super Admin", "scope": "system", "is_system": True, "is_active": True}
ORGADMIN_DOC = {"_id": "orgadmin", "name": "Organization Admin", "scope": "organization", "is_active": True}
BILLING_DOC = {"_id": "contraclaim_billing_admin", "name": "ContraClaim Billing Admin", "scope": "system", "is_active": True}
INACTIVE_DOC = {"_id": "retired", "name": "Retired", "scope": "organization", "is_active": False}
ROLES = [ORGUSER, SUPERADMIN_DOC, ORGADMIN_DOC, BILLING_DOC, INACTIVE_DOC]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "default_role, reason",
    [
        ("superadmin", "the Super Admin key"),
        ("super-admin", "an alias the principal turns into superadmin"),
        ("super admin", "an alias the principal turns into superadmin"),
        ("superadministrator", "an alias the principal turns into superadmin"),
        ("SuperAdmin", "a case variant of the Super Admin key"),
        ("superuser", "the dormant Super User key"),
        ("super-user", "an alias of the Super User key"),
        ("organization-admin", "a legacy spelling, not the canonical key"),
        ("OrgUser", "a case variant, not the canonical key"),
        ("ghost", "no role document"),
        ("retired", "a deactivated role"),
        ("contraclaim_billing_admin", "a system-scope role"),
    ],
)
async def test_an_unsafe_default_role_refuses_provisioning(default_role, reason):
    db = _FakeDB(roles=ROLES)
    with pytest.raises(OidcError):
        await resolve_or_provision_user(db, {"email": "new@corp.com"}, default_role=default_role)
    assert db.users.inserted == [], f"provisioned with {reason}"


@pytest.mark.asyncio
async def test_an_ambiguous_default_role_refuses_provisioning():
    lookalike = {"_id": "6650f0f0f0f0f0f0f0f0f0f0", "name": "orguser", "scope": "organization", "is_active": True}
    db = _FakeDB(roles=[ORGUSER, lookalike])
    with pytest.raises(OidcError):
        await resolve_or_provision_user(db, {"email": "new@corp.com"}, default_role="orguser")
    assert db.users.inserted == []


@pytest.mark.asyncio
async def test_a_canonical_organisation_role_is_provisioned_by_its_id():
    """Positive control for the refusals above, on the same role set."""
    db = _FakeDB(roles=ROLES)
    user = await resolve_or_provision_user(db, {"email": "new@corp.com"}, default_role="orguser")
    assert user["roles"] == ["orguser"]
    assert len(db.users.inserted) == 1


@pytest.mark.asyncio
async def test_an_existing_user_is_not_affected_by_the_default_role():
    db = _FakeDB(seed=[{"_id": "u-existing", "email": "a@corp.com", "roles": ["orgadmin"]}], roles=ROLES)
    user = await resolve_or_provision_user(db, {"email": "a@corp.com"}, default_role="super-admin")
    assert user["_id"] == "u-existing"
    assert db.users.inserted == []


@pytest.mark.parametrize("default_role", ["superadmin", "super-admin", "Super Admin", "superuser", "organization-admin"])
def test_production_configuration_refuses_a_system_or_non_canonical_oidc_default_role(default_role):
    from rbac_backend.core.config import Settings

    settings = Settings(**_production_settings(OIDC_DEFAULT_ROLE=default_role))
    with pytest.raises(ValueError, match="OIDC_DEFAULT_ROLE"):
        settings.validate_runtime_configuration()


def test_production_configuration_accepts_a_canonical_oidc_default_role():
    from rbac_backend.core.config import Settings

    Settings(**_production_settings(OIDC_DEFAULT_ROLE="orguser")).validate_runtime_configuration()


def _production_settings(**overrides):
    values = dict(
        ENVIRONMENT="production",
        DATABASE_URL="mongodb://mongo1:27017,mongo2:27017/contraclaim?replicaSet=rs0",
        SECRET_KEY="x" * 32,
        AWS_ACCESS_KEY_ID="aws-key",
        AWS_SECRET_ACCESS_KEY="aws-secret",
        AWS_BUCKET_NAME="bucket",
        OPENAI_API_KEY="openai-key",
        SMTP_USERNAME="smtp-user",
        SMTP_PASSWORD="smtp-password",
        CORS_ORIGINS='["https://app.contraclaim.com"]',
        LANGGRAPH_ENABLED=False,
        APP_REDIS_URL="redis://redis:6379/1",
        METRICS_TOKEN="metrics-token",
        RBAC_ENTITLEMENT_FAIL_OPEN=False,
        AUTH_COOKIE_SECURE=True,
        BACKUP_S3_BUCKET="backup-bucket",
        ANTIVIRUS_ENABLED=True,
        CLAMAV_FAIL_OPEN=False,
        OIDC_ENABLED=True,
        OIDC_ISSUER="https://idp.example.com",
        OIDC_CLIENT_ID="client",
        OIDC_CLIENT_SECRET="client-secret",
        OIDC_REDIRECT_URI="https://app.contraclaim.com/api/auth/sso/callback",
    )
    values.update(overrides)
    return values
