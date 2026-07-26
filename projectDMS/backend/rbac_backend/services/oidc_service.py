"""Generic OpenID Connect (OIDC) SSO (Phase 3 / M6).

Implements the authorization-code flow against any standards-compliant OIDC
provider (Microsoft Entra ID, Google, Okta, Auth0, Keycloak, ...). On a
successful callback the existing session cookie is issued, so SSO users flow
through the same RBAC/session machinery as password users.

Network calls (discovery, token exchange, JWKS verification) require a live IdP
and are therefore not unit-tested; the pure helpers and the find-or-provision
logic ARE unit-tested (see tests/test_sso.py).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional
from urllib.parse import urlencode

from ..core.config import settings


class OidcError(Exception):
    """Raised for any recoverable OIDC failure (denied login, bad config)."""


# --- Pure helpers (unit-tested) ------------------------------------------------


def email_domain_allowed(email: str, allowed_domains_csv: str) -> bool:
    """An empty allowlist permits any domain; otherwise the email's domain must match."""
    allowed = [d.strip().lower() for d in (allowed_domains_csv or "").split(",") if d.strip()]
    if not allowed:
        return True
    domain = (email or "").rsplit("@", 1)[-1].lower()
    return domain in allowed


def build_authorization_url(
    authorization_endpoint: str,
    *,
    client_id: str,
    redirect_uri: str,
    scopes: str,
    state: str,
    nonce: str,
) -> str:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "scope": scopes,
        "state": state,
        "nonce": nonce,
    }
    sep = "&" if "?" in authorization_endpoint else "?"
    return f"{authorization_endpoint}{sep}{urlencode(params)}"


def decode_oidc_id_token(
    id_token: str,
    jwks: Dict[str, Any],
    *,
    audience: str,
    issuer: str,
) -> Dict[str, Any]:
    """Verify an OIDC ID token against exactly one matching JWK.

    Selecting the signing key by ``kid`` before decoding avoids accepting a
    provider-controlled key set as a generic key object.  Only the algorithms
    explicitly supported by this service are allowed.
    """
    import jwt

    allowed_algorithms = {"RS256", "ES256"}
    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError as exc:
        raise OidcError(f"id_token header is invalid: {exc}") from exc

    algorithm = str(header.get("alg") or "")
    key_id = str(header.get("kid") or "")
    if algorithm not in allowed_algorithms or not key_id:
        raise OidcError("id_token uses an unsupported algorithm or has no key id")

    candidates = [
        key
        for key in (jwks.get("keys") or [])
        if str(key.get("kid") or "") == key_id
        and str(key.get("alg") or algorithm) == algorithm
    ]
    if len(candidates) != 1:
        raise OidcError("OIDC signing key was not found or was ambiguous")

    try:
        signing_key = jwt.PyJWK.from_dict(candidates[0], algorithm=algorithm).key
        return jwt.decode(
            id_token,
            signing_key,
            algorithms=[algorithm],
            audience=audience,
            issuer=issuer,
        )
    except jwt.PyJWTError as exc:
        raise OidcError(f"id_token verification failed: {exc}") from exc


async def resolve_or_provision_user(
    db: Any,
    claims: Dict[str, Any],
    *,
    allowed_domains: str = "",
    auto_provision: bool = True,
    default_role: str = "orguser",
    default_org_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Map verified OIDC claims to a local user, provisioning one if allowed.

    Deny-by-default: rejects when there is no email, the domain is not allowlisted,
    the account is disabled, or no account exists and auto-provisioning is off.
    """
    email = (claims.get("email") or "").strip().lower()
    if not email:
        raise OidcError("OIDC provider did not return an email claim")
    if not email_domain_allowed(email, allowed_domains):
        raise OidcError(f"Email domain is not allowed for SSO: {email}")

    existing = await db.users.find_one({"email": email})
    if existing:
        if existing.get("disabled"):
            raise OidcError("Account is disabled")
        return existing

    if not auto_provision:
        raise OidcError("No account exists for this user and auto-provisioning is disabled")

    given = claims.get("given_name") or claims.get("name") or email.split("@")[0]
    doc = {
        "email": email,
        "username": email,
        "first_name": str(given)[:50],
        "last_name": str(claims.get("family_name") or "")[:50],
        "roles": [default_role] if default_role else [],
        "organization_id": default_org_id or None,
        "organizations": [default_org_id] if default_org_id else [],
        "account_type": "client_user",
        "is_verified": True,  # the IdP verified the email
        "disabled": False,
        "hashed_password": "!sso-no-password",  # password login is impossible
        "sso_provider": "oidc",
        "sso_subject": claims.get("sub"),
        "created_at": datetime.utcnow(),
    }
    result = await db.users.insert_one(doc)
    created = await db.users.find_one({"_id": result.inserted_id})
    return created or doc


# --- Service (network; requires a live IdP) ------------------------------------


class OidcService:
    def __init__(self, settings_obj: Any = None) -> None:
        self.s = settings_obj or settings
        self._discovery: Optional[Dict[str, Any]] = None

    def is_enabled(self) -> bool:
        return bool(getattr(self.s, "OIDC_ENABLED", False))

    async def discovery(self) -> Dict[str, Any]:
        if self._discovery:
            return self._discovery
        import httpx

        url = str(self.s.OIDC_ISSUER).rstrip("/") + "/.well-known/openid-configuration"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            self._discovery = resp.json()
        return self._discovery

    async def authorization_url(self, state: str, nonce: str) -> str:
        disc = await self.discovery()
        return build_authorization_url(
            disc["authorization_endpoint"],
            client_id=self.s.OIDC_CLIENT_ID,
            redirect_uri=self.s.OIDC_REDIRECT_URI,
            scopes=self.s.OIDC_SCOPES,
            state=state,
            nonce=nonce,
        )

    async def exchange_code_for_claims(self, code: str, nonce: Optional[str]) -> Dict[str, Any]:
        disc = await self.discovery()
        import httpx
        async with httpx.AsyncClient(timeout=10) as client:
            token_resp = await client.post(
                disc["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.s.OIDC_REDIRECT_URI,
                    "client_id": self.s.OIDC_CLIENT_ID,
                    "client_secret": self.s.OIDC_CLIENT_SECRET,
                },
            )
            token_resp.raise_for_status()
            tokens = token_resp.json()
            id_token = tokens.get("id_token")
            if not id_token:
                raise OidcError("OIDC token response did not include an id_token")
            jwks_resp = await client.get(disc["jwks_uri"])
            jwks_resp.raise_for_status()
            jwks = jwks_resp.json()

        claims = decode_oidc_id_token(
            id_token,
            jwks,
            audience=self.s.OIDC_CLIENT_ID,
            issuer=self.s.OIDC_ISSUER,
        )

        if nonce and claims.get("nonce") != nonce:
            raise OidcError("OIDC nonce mismatch")
        return claims
