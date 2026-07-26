"""
Secure authentication API with comprehensive security measures including rate limiting,
account lockout, and proper session management. Addresses all security vulnerabilities.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request, Response, Header
from typing import Optional
import logging
from datetime import timedelta, datetime
import jwt
from pydantic import BaseModel, Field

from ..core.security import get_current_user, CurrentUser, create_access_token
from ..core.csrf import clear_csrf_cookie, create_csrf_token, set_csrf_cookie
from ..core.database import get_db
from ..core.config import settings
from ..services.authentication_service import AuthenticationService
from ..services.authorization_service import AuthorizationService
from ..services.permission_service import PermissionService
from ..services.user_service import UserService
from ..models.user_models import (
    LoginRequest, LoginResponse, TokenResponse, RefreshTokenRequest, UserResponse
)
from ..utils.validation import validate_email, validate_input
from ..utils.error_handler import handle_exceptions, AuthenticationError
from ..utils.rate_limiter import RateLimiter
from ..utils.audit_logger import AuditLogger
from ..services.step_up_service import StepUpService, STEP_UP_TTL_MINUTES

logger = logging.getLogger(__name__)
router = APIRouter()


def _set_auth_cookie(response: Response, token: str, max_age: int) -> None:
    response.set_cookie(
        key=settings.AUTH_COOKIE_NAME,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=bool(settings.AUTH_COOKIE_SECURE),
        samesite=str(settings.AUTH_COOKIE_SAMESITE or "lax").lower(),
        domain=settings.AUTH_COOKIE_DOMAIN,
        path="/",
    )
    set_csrf_cookie(response, max_age=max_age)


def _clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.AUTH_COOKIE_NAME,
        domain=settings.AUTH_COOKIE_DOMAIN,
        path="/",
    )
    # Also clear a host-only cookie set before AUTH_COOKIE_DOMAIN was configured,
    # otherwise the stale cookie keeps the user logged in after logout.
    if settings.AUTH_COOKIE_DOMAIN:
        response.delete_cookie(key=settings.AUTH_COOKIE_NAME, path="/")
    clear_csrf_cookie(response)


class StepUpRequest(BaseModel):
    password: str = Field(..., min_length=1, max_length=200)
    action: str = Field(default="*", max_length=120)


class StepUpResponse(BaseModel):
    step_up_token: str
    token_type: str = "step_up"
    expires_in: int = STEP_UP_TTL_MINUTES * 60


class CsrfTokenResponse(BaseModel):
    csrf_token: str


class AuthController:
    """Secure authentication controller with comprehensive security measures."""
    
    def __init__(
        self,
        auth_service: AuthenticationService,
        user_service: UserService,
        authorization_service: AuthorizationService,
        rate_limiter: RateLimiter,
        audit_logger: AuditLogger
    ):
        self.auth_service = auth_service
        self.user_service = user_service
        self.authorization_service = authorization_service
        self.rate_limiter = rate_limiter
        self.audit_logger = audit_logger
        self.permission_service = PermissionService()

    def _extract_session_id(self, token: str) -> Optional[str]:
        """
        Pull the session_id claim out of a JWT. Returns None on any parsing/validation error.
        """
        if not token:
            return None
        try:
            payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
            session_id = payload.get("session_id")
            return str(session_id) if session_id else None
        except Exception as exc:
            logger.warning("Failed to extract session_id from token: %s", exc)
            return None

    async def login_user(
        self,
        login_data: LoginRequest,
        client_ip: str,
        user_agent: str
    ) -> LoginResponse:
        """Authenticate user with comprehensive security validation."""
        try:
            # Rate limiting for login attempts (prevent brute force)
            await self.rate_limiter.check_ip_limit(
                client_ip,
                cost=1,
                window_seconds=settings.LOGIN_IP_RATE_LIMIT_WINDOW,
                max_requests=settings.LOGIN_IP_RATE_LIMIT_REQUESTS,
            )
            
            # Email-based rate limiting
            await self.rate_limiter.check_email_limit(
                login_data.email,
                cost=1,
                window_seconds=settings.LOGIN_EMAIL_RATE_LIMIT_WINDOW,
                max_requests=settings.LOGIN_EMAIL_RATE_LIMIT_REQUESTS,
            )
            
            # Validate input
            validated_email = validate_email(login_data.email)
            password = validate_input(login_data.password, max_length=200, required=True)
            
            # Authenticate user with secure methods
            user = await self.auth_service.authenticate_user_secure(
                validated_email, password
            )
            
            # Check account status
            if not user:
                # SECURITY (H5): increment the per-account failed-attempt counter
                # on this (primary) login path. authenticate_user_secure() returns
                # None on a bad password without touching the counter, so account
                # lockout in AuthenticationService never fired for the main app
                # route. Look up the account by email and, if it exists, record the
                # failed attempt so lockout engages after the configured threshold.
                # The response stays generic to avoid user enumeration.
                try:
                    known_user = await self.user_service.get_user_by_email(validated_email)
                    if known_user is not None and getattr(known_user, "id", None):
                        await self.auth_service.increment_failed_attempts(known_user.id)
                except Exception:
                    # Never let lockout bookkeeping break the login response.
                    pass
                await self.audit_logger.log_login_failed(
                    validated_email,
                    "invalid_credentials",
                    client_ip=client_ip
                )
                # Generic error to prevent user enumeration
                raise AuthenticationError(
                    "Invalid email or password",
                    status.HTTP_401_UNAUTHORIZED
                )
            
            # Check if account is locked
            if await self.auth_service.is_account_locked(user.id):
                await self.audit_logger.log_login_failed(
                    validated_email,
                    "account_locked",
                    client_ip=client_ip
                )
                raise AuthenticationError(
                    "Account is temporarily locked",
                    status.HTTP_423_LOCKED
                )
            
            # Check if account is disabled
            if user.disabled:
                await self.audit_logger.log_login_failed(
                    validated_email,
                    "account_disabled",
                    client_ip=client_ip
                )
                raise AuthenticationError(
                    "Account is disabled",
                    status.HTTP_401_UNAUTHORIZED
                )
            
            # Reset failed attempts on successful login
            await self.auth_service.reset_failed_attempts(user.id)
            
            # Create access token bound to a single session token
            access_token_expires = timedelta(minutes=60)  # Configurable
            session_id = await self.auth_service.create_user_session(
                user.id, client_ip, user_agent, access_token_expires
            )
            access_token = create_access_token(
                data={
                    "sub": user.email,
                    "user_id": str(user.id),
                    "roles": user.roles,
                    "session_id": session_id
                },
                expires_delta=access_token_expires
            )
            
            # Update last login
            await self.user_service.update_last_login(user.id)
            
            # Audit log successful login
            await self.audit_logger.log_login_successful(
                user.id,
                user.email,
                client_ip=client_ip,
                session_id=session_id,
            )
            
            # Build user response
            user_info = await self._build_user_response(user)
            
            return LoginResponse(
                access_token=access_token,
                token_type="bearer",
                expires_in=3600,
                user=user_info
            )
            
        except (AuthenticationError, HTTPException):
            raise
        except Exception as e:
            logger.error(f"Login failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Authentication service temporarily unavailable"
            )

    async def refresh_token(
        self,
        current_user: CurrentUser,
        jwt_token: str
    ) -> TokenResponse:
        """Refresh access token with proper validation."""
        try:
            # Rate limiting for token refresh
            await self.rate_limiter.check_user_limit(current_user.id, cost=2)

            session_id = self._extract_session_id(jwt_token)
            if not session_id:
                await self.audit_logger.log_token_refresh_failed(
                    current_user.id, "session_missing"
                )
                raise AuthenticationError(
                    "Session has expired",
                    status.HTTP_401_UNAUTHORIZED
                )
            
            # Validate current user is still active
            user = await self.user_service.get_user_by_id(current_user.id)
            if not user or user.disabled:
                await self.audit_logger.log_token_refresh_failed(
                    current_user.id, "user_inactive"
                )
                raise AuthenticationError(
                    "User account is no longer active",
                    status.HTTP_401_UNAUTHORIZED
                )
            
            # Validate session is still active
            if not await self.auth_service.is_session_active(session_id):
                await self.audit_logger.log_token_refresh_failed(
                    current_user.id, "session_expired"
                )
                raise AuthenticationError(
                    "Session has expired",
                    status.HTTP_401_UNAUTHORIZED
                )
            
            # Create new access token
            access_token_expires = timedelta(minutes=60)
            access_token = create_access_token(
                data={
                    "sub": user.email,
                    "user_id": str(user.id),
                    "roles": user.roles,
                    "session_id": session_id,
                },
                expires_delta=access_token_expires
            )
            
            # Extend session
            await self.auth_service.extend_session(session_id, access_token_expires)
            
            # Audit log
            await self.audit_logger.log_token_refreshed(current_user.id)
            
            return TokenResponse(
                access_token=access_token,
                token_type="bearer",
                expires_in=3600
            )
            
        except (AuthenticationError, HTTPException):
            raise
        except Exception as e:
            logger.error(f"Token refresh failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Token refresh service temporarily unavailable"
            )

    async def logout_user(
        self,
        current_user: CurrentUser,
        jwt_token: str
    ) -> dict:
        """Logout user and invalidate session."""
        try:
            session_id = self._extract_session_id(jwt_token)
            # Invalidate session
            if session_id:
                await self.auth_service.invalidate_session(session_id)
            
            # Audit log
            await self.audit_logger.log_user_logged_out(
                current_user.id, current_user.email
            )
            
            return {"message": "Logged out successfully"}
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Logout failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Logout service temporarily unavailable"
            )

    async def get_current_user_info(self, current_user: CurrentUser) -> dict:
        """Get current user information with validation."""
        try:
            if current_user.disabled:
                raise AuthenticationError(
                    "User account is no longer active",
                    status.HTTP_401_UNAUTHORIZED
                )

            user = await self.user_service.get_user_by_id(str(current_user.id))
            if not user:
                raise AuthenticationError(
                    "User account is no longer active",
                    status.HTTP_401_UNAUTHORIZED
                )

            return await self._build_user_response(user)
            
        except (AuthenticationError, HTTPException):
            raise
        except Exception as e:
            logger.error(f"Get user info failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="User service temporarily unavailable"
            )

    async def _build_user_response(self, user: any) -> dict:
        """
        Build a response payload compatible with models.user.UserResponse.
        Mirrors logic used in users.py for consistency.
        """
        # Resolve names
        org_name = None
        if getattr(user, "organization_id", None):
            try:
                org_name = await self.user_service.get_organization_name(user.organization_id)
            except Exception:
                org_name = None

        project_names: list[str] = []
        if getattr(user, "projects", None):
            try:
                project_names = await self.user_service.get_project_names(user.projects)
            except Exception:
                project_names = []

        # Derive required fields with safe defaults
        first_name = getattr(user, "first_name", None) or getattr(user, "username", "") or "user"
        last_name = getattr(user, "last_name", None) or "-"
        organizations = getattr(user, "organizations", []) or []
        permissions = getattr(user, "permissions", []) or []
        effective_permissions = await self.permission_service.get_effective_permission_names(
            str(getattr(user, "id", ""))
        )
        if effective_permissions:
            permissions = effective_permissions
        is_active = not bool(getattr(user, "disabled", False))
        is_verified = bool(getattr(user, "is_verified", False))
        preferences = getattr(user, "preferences", None) or {
            "emailNotifications": True,
            "sharingAlerts": True,
        }
        created_at = getattr(user, "created_at", None) or datetime.utcnow()
        last_login = getattr(user, "last_login", None)

        return {
            "id": str(getattr(user, "id", "")),
            "username": getattr(user, "username", "") or getattr(user, "email", ""),
            "email": getattr(user, "email", ""),
            "first_name": first_name,
            "last_name": last_name,
            "roles": getattr(user, "roles", []) or [],
            "organization_id": str(getattr(user, "organization_id", "")) if getattr(user, "organization_id", None) else None,
            "organizations": [str(o) for o in organizations],
            "projects": [str(pid) for pid in (getattr(user, "projects", []) or [])],
            "permissions": [str(p) for p in permissions],
            "account_type": getattr(user, "account_type", "client_user") or "client_user",
            "is_active": is_active,
            "is_verified": is_verified,
            "preferences": preferences,
            "created_at": created_at,
            "last_login": last_login,
            "organization_name": org_name,
            "project_names": project_names,
        }


# Dependency injection
async def get_auth_controller(db = Depends(get_db)) -> AuthController:
    """Factory function for auth controller."""
    auth_service = AuthenticationService()
    user_service = UserService(db)
    authorization_service = AuthorizationService()
    rate_limiter = RateLimiter(scope="auth")
    audit_logger = AuditLogger()

    return AuthController(
        auth_service, user_service, authorization_service,
        rate_limiter, audit_logger
    )


# API Endpoints
@router.post("/login", response_model=LoginResponse)
@handle_exceptions
async def login(
    login_data: LoginRequest,
    request: Request,
    response: Response,
    controller: AuthController = Depends(get_auth_controller)
):
    """Login user with comprehensive security validation."""
    client_ip = request.client.host
    user_agent = request.headers.get("user-agent", "unknown")
    
    login_response = await controller.login_user(login_data, client_ip, user_agent)
    _set_auth_cookie(response, login_response.access_token, login_response.expires_in)
    return login_response


@router.post("/refresh", response_model=TokenResponse)
@handle_exceptions
async def refresh_token(
    request: Request,
    response: Response,
    authorization: str = Header(default=""),
    controller: AuthController = Depends(get_auth_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Refresh access token with proper validation."""
    token = authorization.replace("Bearer ", "").strip()
    if not token:
        token = request.cookies.get(settings.AUTH_COOKIE_NAME, "")
    token_response = await controller.refresh_token(current_user, token)
    _set_auth_cookie(response, token_response.access_token, token_response.expires_in)
    return token_response


@router.post("/logout")
@handle_exceptions
async def logout(
    request: Request,
    response: Response,
    authorization: str = Header(default=""),
    controller: AuthController = Depends(get_auth_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Logout user and invalidate session."""
    token = authorization.replace("Bearer ", "").strip()
    if not token:
        token = request.cookies.get(settings.AUTH_COOKIE_NAME, "")
    result = await controller.logout_user(current_user, token)
    _clear_auth_cookie(response)
    return result


@router.get("/csrf-token", response_model=CsrfTokenResponse)
async def get_csrf_token(response: Response):
    """Issue a signed CSRF token cookie for browser clients."""
    token = create_csrf_token()
    set_csrf_cookie(response, token=token)
    return CsrfTokenResponse(csrf_token=token)


@router.get("/me", response_model=UserResponse)
@handle_exceptions
async def get_current_user_info(
    controller: AuthController = Depends(get_auth_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get current user information."""
    return await controller.get_current_user_info(current_user)


@router.post("/step-up", response_model=StepUpResponse)
@handle_exceptions
async def issue_step_up_token(
    payload: StepUpRequest,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Verify password and return a short-lived token for dangerous actions."""
    token = await StepUpService(db).issue_after_password(
        current_user=current_user,
        password=payload.password,
        action=payload.action,
    )
    return StepUpResponse(step_up_token=token)
