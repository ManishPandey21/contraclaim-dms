"""
Secure authentication API with comprehensive security measures including rate limiting,
account lockout, and proper session management. Addresses all security vulnerabilities.
"""

from fastapi import APIRouter, Depends, HTTPException, status, Request, Header
from typing import Optional
import logging
from datetime import timedelta, datetime
from jose import jwt

from ..core.security import get_current_user, CurrentUser, create_access_token
from ..core.database import get_db
from ..core.config import settings
from ..services.authentication_service import AuthenticationService
from ..services.authorization_service import AuthorizationService
from ..services.user_service import UserService
from ..models.user_models import (
    LoginRequest, LoginResponse, TokenResponse, RefreshTokenRequest
)
from ..utils.validation import validate_email, validate_input
from ..utils.error_handler import handle_exceptions, AuthenticationError
from ..utils.rate_limiter import RateLimiter
from ..utils.audit_logger import AuditLogger

logger = logging.getLogger(__name__)
router = APIRouter()


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
                cost=5,
                window_seconds=900,  # 15 minutes
                max_requests=5  # Max 5 attempts per IP per 15 min
            )
            
            # Email-based rate limiting
            await self.rate_limiter.check_email_limit(
                login_data.email,
                cost=3,
                window_seconds=3600,  # 1 hour
                max_requests=10  # Max 10 attempts per email per hour
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
            
        except AuthenticationError:
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
            
        except AuthenticationError:
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
            
        except Exception as e:
            logger.error(f"Logout failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Logout service temporarily unavailable"
            )

    async def get_current_user_info(self, current_user: CurrentUser) -> CurrentUser:
        """Get current user information with validation."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)
            
            # Validate user is still active
            user = await self.user_service.get_user_by_id(current_user.id)
            if not user or user.disabled:
                raise AuthenticationError(
                    "User account is no longer active",
                    status.HTTP_401_UNAUTHORIZED
                )
            
            return current_user
            
        except AuthenticationError:
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
    rate_limiter = RateLimiter()
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
    controller: AuthController = Depends(get_auth_controller)
):
    """Login user with comprehensive security validation."""
    client_ip = request.client.host
    user_agent = request.headers.get("user-agent", "unknown")
    
    return await controller.login_user(login_data, client_ip, user_agent)


@router.post("/refresh", response_model=TokenResponse)
@handle_exceptions
async def refresh_token(
    authorization: str = Header(default=""),
    controller: AuthController = Depends(get_auth_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Refresh access token with proper validation."""
    token = authorization.replace("Bearer ", "").strip()
    return await controller.refresh_token(current_user, token)


@router.post("/logout")
@handle_exceptions
async def logout(
    authorization: str = Header(default=""),
    controller: AuthController = Depends(get_auth_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Logout user and invalidate session."""
    token = authorization.replace("Bearer ", "").strip()
    return await controller.logout_user(current_user, token)


@router.get("/me", response_model=CurrentUser)
@handle_exceptions
async def get_current_user_info(
    controller: AuthController = Depends(get_auth_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get current user information."""
    return await controller.get_current_user_info(current_user)
