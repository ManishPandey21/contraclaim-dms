from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

from fastapi import Depends, HTTPException, status, Request
import jwt
from jwt import PyJWTError as JWTError
from passlib.context import CryptContext
from .config import settings
from .database import get_db  # Corrected import
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, EmailStr, Field
import uuid
import re
from bson import ObjectId
from pydantic import ConfigDict
from ..services.permission_service import PermissionService
from ..utils.audit_logger import get_audit_logger


import logging

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

logger = logging.getLogger(__name__)


class _SessionStoreUnavailableError(Exception):
    """Raised when the configured session store cannot answer revocation checks."""


async def _handle_session_store_unavailable(exc: Exception) -> None:
    """C2: apply the fail-closed/fail-open policy for session-store outages.

    Revocation state (logout, forced JWT invalidation, lockout) lives in the
    runtime Redis. When that store is configured but unreachable we either deny
    authentication with a 503 (default — a revoked session must not outlive a
    Redis outage) or, when AUTH_SESSION_FAIL_CLOSED=false, log loudly and skip
    the checks for this request. 503 rather than 401 on purpose: the client's
    401 handler would clear the session and bounce users to login; 503 keeps
    their cookie for when the store returns.
    """
    try:
        from ..services.observability import observability_registry

        await observability_registry.record_domain_event(
            resource_type="auth", event_type="session_store_unavailable"
        )
    except Exception:
        pass
    if settings.AUTH_SESSION_FAIL_CLOSED:
        logger.error(
            "Session store unavailable; failing closed (AUTH_SESSION_FAIL_CLOSED=true): %s", exc
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service temporarily unavailable",
        ) from exc
    logger.warning(
        "Session store unavailable; AUTH_SESSION_FAIL_CLOSED=false — proceeding WITHOUT "
        "revocation checks (logout/lockout not enforced for this request): %s",
        exc,
    )

# Compatibility role aliases to normalize various role naming schemes
ROLE_ALIASES = {
    "organization-user": "orguser",
    "org-user": "orguser",
    "organization user": "orguser",
    "organizationuser": "orguser",
    "orguser": "orguser",
    "organization-admin": "orgadmin",
    "org-admin": "orgadmin",
    "organization admin": "orgadmin",
    "organizationadmin": "orgadmin",
    "orgadmin": "orgadmin",
    "project-user": "projectuser",
    "project user": "projectuser",
    "projectuser": "projectuser",
    "proj-user": "projectuser",
    "proj user": "projectuser",
    "projuser": "projectuser",
    "project-admin": "projectadmin",
    "project admin": "projectadmin",
    "projectadmin": "projectadmin",
    "proj-admin": "projectadmin",
    "proj admin": "projectadmin",
    "projadmin": "projectadmin",
    "super-admin": "superadmin",
    "super admin": "superadmin",
    "superadministrator": "superadmin",
}

def _normalize_roles_list(roles):
    out = []
    for r in (roles or []):
        s = str(r).strip().lower()
        s = ROLE_ALIASES.get(s, ROLE_ALIASES.get(re.sub(r"[^a-z0-9]", "", s), s))
        out.append(s)
    # de-duplicate while preserving order
    result = []
    seen = set()
    for r in out:
        if r not in seen:
            result.append(r)
            seen.add(r)
    return result

# Moved from organizations.py
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/token")  # Use correct API path
class CurrentUser(BaseModel):
    id: str
    username: str
    email: EmailStr
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    job_title: Optional[str] = None
    roles: List[str]
    organization_id: Optional[str] = None
    organizations: List[str] = Field(default_factory=list)
    projects: List[str] = Field(default_factory=list)
    account_type: str = "client_user"
    disabled: bool = False
# Moved from organizations.py
async def get_current_user(request: Request, db = Depends(get_db)):
    """
    Resolve current user from:
    1) Bearer token when present and non-placeholder
    2) HttpOnly auth cookie
    3) Optional dev headers (only when ALLOW_DEV_HEADERS=True): X-User-Id, X-User-Role(s), X-Org-Id, X-Proj-Id
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # Try Authorization: Bearer <token>, then the HttpOnly cookie. Browser
    # clients can carry stale legacy Authorization headers from older builds;
    # those must not override a fresh cookie set by /api/login.
    auth_header = request.headers.get("authorization", "")
    token_candidates: list[str] = []
    if isinstance(auth_header, str) and auth_header.lower().startswith("bearer "):
        bearer_token = auth_header.split(" ", 1)[1].strip()
        if bearer_token and bearer_token.lower() not in {"null", "undefined", "none"}:
            token_candidates.append(bearer_token)
    cookie_token = request.cookies.get(settings.AUTH_COOKIE_NAME)
    if isinstance(cookie_token, str) and cookie_token.strip():
        cookie_token = cookie_token.strip()
        if cookie_token not in token_candidates:
            token_candidates.append(cookie_token)

    for token in token_candidates:
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            # Reject non-access tokens. Step-up tokens (typ="step_up") share the
            # signing key and must never authenticate a normal session; tokens with
            # an explicit non-"access" type are rejected too. Missing type ==
            # legacy access token (back-compat during rollout).
            if payload.get("typ") == "step_up" or payload.get("type") not in (None, "access"):
                continue
            email: str = payload.get("sub")
            iat: int = payload.get("iat", 0)
            if email:
                user = await db.users.find_one({"email": email})
                if user:
                    user_id_str = str(user["_id"])
                    
                    # JWT Invalidation Check (Phase 3)
                    from ..services.runtime_state import get_runtime_state
                    runtime = get_runtime_state()
                    # C2: only deployments that configure a runtime Redis have an
                    # authoritative revocation store; for them, an unreachable
                    # store must not silently skip the checks (fail-open logout).
                    if runtime.redis_url:
                        try:
                            redis = await runtime.get_redis()
                            if redis is None:
                                raise _SessionStoreUnavailableError("runtime Redis unreachable")
                            min_iat = await redis.get(f"user_jwt_min_iat:{user_id_str}")
                            if min_iat and iat < int(min_iat):
                                raise credentials_exception

                            # Session invalidation: a logged-out or expired session must
                            # immediately stop authenticating, even within the token TTL.
                            session_id = payload.get("session_id")
                            if session_id:
                                from ..services.authentication_service import AuthenticationService

                                if not await AuthenticationService().is_session_active(str(session_id)):
                                    raise credentials_exception
                        except HTTPException:
                            raise  # revocation denials keep their 401 semantics
                        except Exception as exc:
                            # Store configured but failing (connection refused,
                            # timeout mid-call, stale client): apply the policy.
                            await _handle_session_store_unavailable(exc)

                    # Derive organizations for superuser/similar users if not present
                    orgs = user.get("organizations", [])
                    org_id = user.get("organization_id")
                    if (not orgs) and org_id:
                        try:
                            orgs = [str(org_id)]
                        except Exception:
                            orgs = [org_id]
                    roles = _normalize_roles_list(user.get("roles", []))
                    org_id_sanitized = org_id
                    orgs_sanitized = orgs or []
                    projects_sanitized = (user.get("projects", []) or [])
                    if "superadmin" in roles:
                        org_id_sanitized = None
                        orgs_sanitized = []
                        projects_sanitized = []
                    return CurrentUser(
                        id=str(user["_id"]),
                        username=user.get("username", email),
                        email=user.get("email", email),
                        first_name=user.get("first_name") or user.get("firstName"),
                        last_name=user.get("last_name") or user.get("lastName"),
                        job_title=user.get("job_title") or user.get("jobTitle"),
                        roles=roles,
                        organization_id=org_id_sanitized,
                        organizations=orgs_sanitized,
                        projects=projects_sanitized,
                        account_type=user.get("account_type", "client_user"),
                        disabled=user.get("disabled", False),
                    )
        except JWTError:
            # Try the next credential source before falling through to dev mode.
            continue
        except HTTPException as exc:
            if exc.status_code == status.HTTP_503_SERVICE_UNAVAILABLE:
                # C2 fail-closed: a session-store outage must surface as an
                # outage, not be swallowed by the next-credential fallback.
                raise
            # Revocation denials (401) fall through to the next candidate; the
            # final deny below still applies if none authenticate.
            continue
        except Exception:
            # Any unexpected token error -> try the next credential source.
            continue

    # Fallback: Dev headers (explicitly disabled unless ALLOW_DEV_HEADERS is True)
    if not settings.ALLOW_DEV_HEADERS:
        raise credentials_exception

    x_user_id = request.headers.get("x-user-id")
    x_user_roles = request.headers.get("x-user-role") or request.headers.get("x-user-roles")
    x_org_id = request.headers.get("x-org-id")
    x_proj_id = request.headers.get("x-proj-id")

    if x_user_id:
        # Parse roles
        roles: list[str] = []
        if x_user_roles:
            try:
                import json
                parsed = json.loads(x_user_roles)
                if isinstance(parsed, list):
                    roles = [str(r) for r in parsed]
            except Exception:
                roles = [r.strip() for r in str(x_user_roles).split(",") if r.strip()]
        roles = [str(r).lower() for r in roles]
        if not roles:
            raise credentials_exception

        # Try load real user if possible
        user_doc = await db.users.find_one({"_id": x_user_id}) or await db.users.find_one({"email": x_user_id})
        username = (user_doc.get("username") if user_doc else None) or str(x_user_id)
        first_name = (user_doc.get("first_name") or user_doc.get("firstName")) if user_doc else None
        last_name = (user_doc.get("last_name") or user_doc.get("lastName")) if user_doc else None
        job_title = (user_doc.get("job_title") or user_doc.get("jobTitle")) if user_doc else None
        # Use example.com (RFC 2606) to keep fabricated emails valid for EmailStr while indicating non-production
        email = (user_doc.get("email") if user_doc else None) or f"{x_user_id}@example.com"
        org_id = x_org_id or (user_doc.get("organization_id") if user_doc else None)
        projects = [x_proj_id] if x_proj_id else (user_doc.get("projects", []) if user_doc else [])
        disabled = bool(user_doc.get("disabled")) if user_doc else False
        orgs = (user_doc.get("organizations", []) if user_doc else [])
        # If orgs empty but org_id present, seed with single org for convenience
        if not orgs and org_id:
            try:
                orgs = [str(org_id)]
            except Exception:
                orgs = [org_id]

        # Sanitize for superadmin: reflect global access (no tenant scoping)
        if "superadmin" in roles:
            org_id = None
            orgs = []
            projects = []

        return CurrentUser(
            id=str(user_doc["_id"]) if user_doc and user_doc.get("_id") else str(x_user_id),
            username=username,
            email=email,
            first_name=first_name,
            last_name=last_name,
            job_title=job_title,
            roles=roles,
            organization_id=org_id,
            organizations=orgs,
            projects=projects,
            account_type=(user_doc.get("account_type") if user_doc else None) or "client_user",
            disabled=disabled,
        )

    # No valid auth found
    raise credentials_exception
    

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    now = datetime.utcnow()
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    # Stamp issued-at. The token-invalidation check in get_current_user compares
    # the token's iat against a per-user `user_jwt_min_iat` marker. Without iat the
    # verifier falls back to iat=0, which is < any marker, so it rejects EVERY token
    # for that user — freshly minted ones included — and the user can never log in.
    to_encode.setdefault("iat", now)
    # Mark the audience of this token so non-access tokens (e.g. step-up tokens,
    # which share the signing key) cannot be replayed as a session credential.
    to_encode.setdefault("type", "access")
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt

async def get_current_active_user(current_user: CurrentUser = Depends(get_current_user)):
    if current_user.disabled:
        raise HTTPException(status_code=400, detail="Inactive user")
    return current_user

def require_permission(permission_name: str):
    """
    Dependency factory that enforces a specific permission for the current request.
    """
    normalized = (permission_name or "").strip()
    permission_service = PermissionService()
    audit_logger = get_audit_logger()

    async def permission_checker(current_user: CurrentUser = Depends(get_current_active_user)):
        roles = set(_normalize_roles_list(getattr(current_user, "roles", []) or []))
        if "superadmin" in roles:
            try:
                await audit_logger.log_permission_check(
                    current_user.id,
                    normalized,
                    True,
                    resource_type="permission",
                    resource_id=normalized,
                )
            except Exception as exc:
                import logging
                logging.getLogger(__name__).error("Audit log failed during permission check: %s", exc)
            return True

        has_perm = await permission_service.user_has_permission(
            current_user.id,
            normalized,
            log=False,
            resource_type="permission",
            resource_id=normalized,
        )

        try:
            await audit_logger.log_permission_check(
                current_user.id,
                normalized,
                has_perm,
                resource_type="permission",
                resource_id=normalized,
            )
        except Exception as exc:
            import logging
            logging.getLogger(__name__).error("Audit log failed during permission check: %s", exc)

        if not has_perm:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required permission: {normalized}",
            )
        return True

    return permission_checker

# NOTE: The legacy `has_permission(permission)` dependency and `require_roles(*roles)`
# were removed in the Week-1 authorization consolidation. `has_permission` queried
# `db.roles` directly and swallowed errors into a superadmin-bypass; both diverged
# from the canonical permission source. Use `require_permission(...)` (canonical
# dependency) or `PolicyService.authorize(...)` (scoped, entitled, audited gate)
# instead. For boolean predicates use `PolicyService.has_permission(...)`.
# See docs/AUTHZ.md.

# RBAC scope helpers
def effective_project_ids(user: CurrentUser) -> List[str]:
    """
    Return list of project IDs (as strings) the user is assigned to.
    """
    try:
        return [str(p) for p in getattr(user, "projects", []) if p is not None]
    except Exception:
        return []

def authorize_scope(
    current_user: CurrentUser,
    organization_id: Optional[str] = None,
    project_id: Optional[str] = None,
    db=None,
) -> None:
    """
    Centralized scope enforcement:
      - superadmin: global access
      - orgadmin/orguser: restricted to their organization and all projects within it
      - projectadmin/projectuser: restricted to their organization and assigned project(s) only
    Raises HTTPException 403 on violations.
    """
    roles = set(current_user.roles or [])
    if "superadmin" in roles:
        return

    if "superuser" in roles:
        allowed_orgs = {str(o) for o in (getattr(current_user, "organizations", []) or []) if o}
        org_id_val = getattr(current_user, "organization_id", None)
        if org_id_val:
            allowed_orgs.add(str(org_id_val))
        allowed_projects = effective_project_ids(current_user)

        # Deny-by-default: a superuser with no granted organizations has no scope.
        # (Mirrors build_scope_query, which returns an empty-result filter here.)
        if not allowed_orgs:
            raise HTTPException(status_code=403, detail="Not authorized")
        if organization_id is not None and str(organization_id) not in allowed_orgs:
            raise HTTPException(status_code=403, detail="Not authorized for this organization")
        if project_id is not None and (allowed_projects and str(project_id) not in allowed_projects):
            raise HTTPException(status_code=403, detail="Not authorized for this project")
        return

    # Organization-scoped roles
    if "orgadmin" in roles or "orguser" in roles:
        if organization_id is not None and str(organization_id) != str(current_user.organization_id):
            raise HTTPException(status_code=403, detail="Not authorized for this organization")
        if project_id is not None:
            proj_ids = effective_project_ids(current_user)
            # If user has explicit project assignments, enforce them
            if proj_ids and str(project_id) not in proj_ids:
                raise HTTPException(status_code=403, detail="Not authorized for this project")
        return

    # Project-scoped roles
    if "projectadmin" in roles or "projectuser" in roles:
        proj_ids = effective_project_ids(current_user)
        if project_id is None or str(project_id) not in proj_ids:
            raise HTTPException(status_code=403, detail="Not authorized for this project")
        if organization_id is not None and str(organization_id) != str(current_user.organization_id):
            raise HTTPException(status_code=403, detail="Not authorized for this organization")
        return

    # External Experts (assigned to specific projects, but not necessarily in the organization)
    expert_roles = {"contraclaim_expert_drafter", "contraclaim_expert_reviewer", "contraclaim_drafting_manager", "contract_expert"}
    if roles & expert_roles:
        proj_ids = effective_project_ids(current_user)
        if project_id is None or str(project_id) not in proj_ids:
            raise HTTPException(status_code=403, detail="Not authorized for this project as an expert")
        # Do not check organization_id here because experts may work cross-org.
        return

    # Default deny
    raise HTTPException(status_code=403, detail="Not authorized")

def _expand_object_ids(values: List[str]) -> List[Any]:
    """
    Expand string identifiers into both string and ObjectId variants when possible.
    This avoids type-mismatch misses for collections storing ObjectIds.
    """
    expanded: List[Any] = []
    for value in values or []:
        if value is None:
            continue
        value_str = str(value)
        if value_str not in expanded:
            expanded.append(value_str)
        try:
            oid = ObjectId(value_str)
        except Exception:
            continue
        if oid not in expanded:
            expanded.append(oid)
    return expanded

def build_scope_query(
    current_user: CurrentUser,
    organization_id: Optional[str] = None,
    project_id: Optional[str] = None,
    org_field: str = "organization_id",
    project_field: Optional[str] = "project_id",
    id_field: str = "_id",
) -> Dict[str, Any]:
    """
    Build a MongoDB query filter enforcing scope for list endpoints, aligned with authorize_scope rules.
    - superadmin: unrestricted (honors explicit filters if provided)
    - orgadmin/orguser: restricted to user's organization (all projects within it)
    - projectadmin/projectuser: restricted to assigned project(s) and their organization
    """
    roles = set([str(r).lower() for r in (current_user.roles or [])])

    # Helper to return an empty-result filter
    def _deny_all() -> Dict[str, Any]:
        return {"_id": {"$in": []}}

    if "superadmin" in roles:
        q: Dict[str, Any] = {}
        if organization_id is not None:
            q[org_field] = str(organization_id)
        if project_id is not None:
            if project_field:
                q[project_field] = str(project_id)
            else:
                q[id_field] = str(project_id)
        return q

    if "superuser" in roles:
        allowed_orgs = {str(o) for o in (getattr(current_user, "organizations", []) or []) if o}
        org_id_val = getattr(current_user, "organization_id", None)
        if org_id_val:
            allowed_orgs.add(str(org_id_val))
        allowed_projects = [str(p) for p in (getattr(current_user, "projects", []) or []) if p]
        if not allowed_orgs:
            return _deny_all()
        q = {org_field: {"$in": _expand_object_ids(list(allowed_orgs))}}
        if organization_id is not None and str(organization_id) not in allowed_orgs:
            return _deny_all()
        if project_id is not None:
            if project_field:
                q[project_field] = {"$in": _expand_object_ids([project_id])}
            else:
                q[id_field] = {"$in": _expand_object_ids([project_id])}
        elif allowed_projects and project_field:
            q[project_field] = {"$in": _expand_object_ids(allowed_projects)}
        return q

    if "orgadmin" in roles or "orguser" in roles:
        org_id_val = getattr(current_user, "organization_id", None)
        if not org_id_val:
            return _deny_all()
        if organization_id is not None and str(organization_id) != str(org_id_val):
            return _deny_all()
        q = {org_field: str(org_id_val)}
        # If user has explicit project assignments, optionally restrict
        proj_ids = [str(p) for p in (getattr(current_user, "projects", []) or [])]
        if project_id is not None:
            if project_field:
                q[project_field] = {"$in": _expand_object_ids([project_id])}
            else:
                q[id_field] = {"$in": _expand_object_ids([project_id])}
        elif proj_ids and project_field:
            q[project_field] = {"$in": _expand_object_ids(proj_ids)}
        return q

    if "projectadmin" in roles or "projectuser" in roles:
        org_id_val = getattr(current_user, "organization_id", None)
        proj_ids = [str(p) for p in (getattr(current_user, "projects", []) or [])]
        if not proj_ids:
            return _deny_all()
        if organization_id is not None and (org_id_val is None or str(organization_id) != str(org_id_val)):
            return _deny_all()
        q: Dict[str, Any] = {}
        if org_id_val is not None:
            q[org_field] = str(org_id_val)
        if project_id is not None:
            if str(project_id) not in proj_ids:
                return _deny_all()
            if project_field:
                q[project_field] = {"$in": _expand_object_ids([project_id])}
            else:
                q[id_field] = {"$in": _expand_object_ids([project_id])}
        else:
            if project_field:
                q[project_field] = {"$in": _expand_object_ids(proj_ids)}
            else:
                q[id_field] = {"$in": _expand_object_ids(proj_ids)}
        return q

    expert_roles = {"contraclaim_expert_drafter", "contraclaim_expert_reviewer", "contraclaim_drafting_manager", "contract_expert"}
    if roles & expert_roles:
        proj_ids = [str(p) for p in (getattr(current_user, "projects", []) or [])]
        if not proj_ids:
            return _deny_all()
        q: Dict[str, Any] = {}
        if project_id is not None:
            if str(project_id) not in proj_ids:
                return _deny_all()
            if project_field:
                q[project_field] = {"$in": _expand_object_ids([project_id])}
            else:
                q[id_field] = {"$in": _expand_object_ids([project_id])}
        else:
            if project_field:
                q[project_field] = {"$in": _expand_object_ids(proj_ids)}
            else:
                q[id_field] = {"$in": _expand_object_ids(proj_ids)}
        return q

    return {"_id": {"$in": []}}


def validate_role_assignment(actor: CurrentUser, target_roles: List[str]) -> None:
    """
    Prevent privilege escalation on role assignments.
    Rules:
      - superadmin: may assign any role.
      - orgadmin: may not assign 'superadmin' or 'orgadmin'.
      - orguser: may not assign roles.
      - projectadmin: may not assign 'superadmin', 'orgadmin', or 'projectadmin'.
      - projectuser: may not assign roles.
      - others: deny.
    Raises HTTPException 403 on violations.
    """
    actor_roles = set(_normalize_roles_list(actor.roles or []))
    targets = set(_normalize_roles_list(target_roles or []))

    if "superadmin" in actor_roles:
        return

    if not actor_roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to assign roles")

    if "orgadmin" in actor_roles:
        if {"superadmin"} & targets:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Organization-scoped roles cannot assign superadmin")
        if {"orgadmin"} & targets:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to assign organization admin role")
        return

    if "orguser" in actor_roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to assign roles")

    if "projectadmin" in actor_roles:
        if {"superadmin", "orgadmin"} & targets:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Project-scoped roles may not assign organization or system roles")
        if {"projectadmin"} & targets:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to assign project admin role")
        return

    if "projectuser" in actor_roles:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to assign roles")

    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authorized to assign roles")


class TokenData(BaseModel):
    username: str | None = None
