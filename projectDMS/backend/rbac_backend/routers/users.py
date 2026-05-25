# Improved users.py
"""
Secure user management and authentication with comprehensive validation,
rate limiting, and enhanced security. Addresses authentication vulnerabilities
and implements proper session management.
"""

from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Query, Request, Response
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from typing import List, Optional, Dict, Any
from datetime import timedelta, datetime
import logging
import re

from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError

from ..core.security import (
    create_access_token, get_password_hash, verify_password, get_current_user, CurrentUser, require_permission
)
from ..core.config import settings
from ..core.database import get_db
from ..services.step_up_service import require_step_up
from ..services.user_service import UserService, UserServiceError
from ..services.authentication_service import AuthenticationService
from ..services.authorization_service import AuthorizationService
from ..services.role_service import RoleService
from ..models.user_models import (
    User, UserCreate, UserUpdate, UserResponse, UserListResponse,
    LoginRequest, LoginResponse, TokenResponse
)
from ..models.user import Preferences
from ..utils.validation import (
    validate_input, sanitize_text, validate_email, validate_password_strength, validate_object_id
)
from ..utils.error_handler import handle_exceptions, BaseDomainError, UserError, AuthenticationError
from ..utils.rate_limiter import RateLimiter
from ..utils.audit_logger import AuditLogger
from ..utils.notification_service import NotificationService

logger = logging.getLogger(__name__)
router = APIRouter()

ROLE_KEYS_ORGADMIN = {"orgadmin", "organizationadmin"}
ROLE_KEYS_PROJECTADMIN = {"projectadmin", "projectadministrator"}

LEGACY_AUTH_SUNSET = "2026-08-19"


def _mark_legacy_auth_response(response: Response, successor_path: str) -> None:
    response.headers["Deprecation"] = "true"
    response.headers["Sunset"] = LEGACY_AUTH_SUNSET
    response.headers["Link"] = f"<{successor_path}>; rel=\"successor-version\""

def _normalize_role_key(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    return re.sub(r"[^a-z0-9]", "", raw)

# Dependency helpers to access Request safely (avoid FastAPI treating 'request' as a query param)
def _client_ip_dep(request: Request) -> str:
    try:
        return request.client.host if request and request.client else "unknown"
    except Exception:
        return "unknown"

def _user_agent_dep(request: Request) -> str:
    try:
        return request.headers.get("user-agent", "unknown")
    except Exception:
        return "unknown"


class UserCreatePayload(BaseModel):
    """
    Lightweight create-user payload that matches what the frontend sends.

    Extra fields are ignored so older clients (e.g., sending organization_id only)
    don't break validation. The controller will expand this into the full
    UserCreate model expected by the service layer.
    """

    model_config = ConfigDict(extra="ignore")

    username: str
    email: EmailStr
    first_name: str
    last_name: str
    password: str
    roles: List[str] = Field(default_factory=list)
    organization_id: Optional[str] = None
    organizations: Optional[List[str]] = None
    projects: List[str] = Field(default_factory=list)
    permissions: Optional[List[str]] = None
    preferences: Optional[Preferences] = None


class UserController:
    """Secure user controller with comprehensive authentication and authorization."""
    
    def __init__(
        self,
        user_service: UserService,
        auth_service: AuthorizationService,
        authentication_service: AuthenticationService,
        rate_limiter: RateLimiter,
        audit_logger: AuditLogger,
        notification_service: NotificationService
    ):
        self.user_service = user_service
        self.auth_service = auth_service
        self.authentication_service = authentication_service
        self.rate_limiter = rate_limiter
        self.audit_logger = audit_logger
        self.notification_service = notification_service
        self.role_service = RoleService()

    async def authenticate_user(
        self,
        login_data: LoginRequest,
        client_ip: str,
        user_agent: str
    ) -> LoginResponse:
        """Authenticate user with comprehensive security measures."""
        try:
            # Rate limiting for login attempts (prevent brute force)
            await self.rate_limiter.check_ip_limit(
                client_ip,
                cost=1,
                window_seconds=settings.LOGIN_IP_RATE_LIMIT_WINDOW,
                max_requests=settings.LOGIN_IP_RATE_LIMIT_REQUESTS,
            )
            
            # Additional email-based rate limiting
            await self.rate_limiter.check_email_limit(
                login_data.email,
                cost=1,
                window_seconds=settings.LOGIN_EMAIL_RATE_LIMIT_WINDOW,
                max_requests=settings.LOGIN_EMAIL_RATE_LIMIT_REQUESTS,
            )
            
            # Validate input
            validated_email = validate_email(login_data.email)
            password = validate_input(login_data.password, max_length=200, required=True)
            
            # Get user by email (prevent user enumeration by using consistent timing)
            user = await self.user_service.get_user_by_email_secure(validated_email)
            
            # Check if account is locked
            if user and await self.authentication_service.is_account_locked(user.id):
                await self.audit_logger.log_login_failed(
                    validated_email,
                    "account_locked",
                    client_ip=client_ip
                )
                raise AuthenticationError(
                    "Account is temporarily locked due to suspicious activity",
                    status.HTTP_423_LOCKED
                )
            
            # Verify credentials
            if not user or not await self.authentication_service.verify_password_secure(
                password, user.hashed_password
            ):
                # Log failed attempt
                await self.audit_logger.log_login_failed(
                    validated_email,
                    "invalid_credentials",
                    client_ip=client_ip
                )
                
                # Increment failed attempts if user exists
                if user:
                    await self.authentication_service.increment_failed_attempts(user.id)
                
                # Generic error message to prevent user enumeration
                raise AuthenticationError(
                    "Invalid email or password",
                    status.HTTP_401_UNAUTHORIZED
                )
            
            # Check if user is disabled
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
            await self.authentication_service.reset_failed_attempts(user.id)
            
            # Create access token with user context
            access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
            access_token = create_access_token(
                data={
                    "sub": user.email,
                    "user_id": str(user.id),
                    "roles": user.roles
                },
                expires_delta=access_token_expires
            )
            
            # Store session information
            session_id = await self.authentication_service.create_user_session(
                user.id, client_ip, user_agent, access_token_expires
            )
            
            # Update last login
            await self.user_service.update_last_login(user.id)
            
            # Audit log successful login
            await self.audit_logger.log_login_successful(
                str(user.id), user.email, client_ip=client_ip, session_id=session_id
            )
            
            # Build user response
            user_response = await self._build_user_response(user)
            
            return LoginResponse(
                access_token=access_token,
                token_type="bearer",
                expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
                user=user_response
            )
            
        except (AuthenticationError, UserError):
            raise
        except Exception as e:
            logger.error(f"Authentication failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Authentication service temporarily unavailable"
            )

    async def create_user(
        self,
        user_data: UserCreatePayload,
        current_user: CurrentUser
    ) -> UserResponse:
        """Create user with comprehensive validation and authorization."""
        try:
            # Rate limiting for user creation
            await self.rate_limiter.check_user_limit(current_user.id, cost=10)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "users:create")
            
            # Validate and sanitize input
            validated_data = await self._validate_user_input(user_data)
            
            # Check for existing user
            existing_user = await self.user_service.check_user_exists(
                validated_data.email, validated_data.username
            )
            if existing_user:
                raise UserError(
                    "User with this email or username already exists",
                    status.HTTP_409_CONFLICT
                )
            
            # Validate role assignments and organizational context
            await self._validate_user_context(validated_data, current_user)
            
            # Create user
            user = await self.user_service.create_user(
                validated_data, validated_data.password
            )
            
            # Audit log
            await self.audit_logger.log_user_created(
                current_user.id, user.id, user.email, validated_data.roles
            )
            
            # Send welcome email (background task)
            # background_tasks.add_task(
            #     self.notification_service.send_welcome_email, user
            # )
            
            # Build response
            user_response = await self._build_user_response(user)
            
            return user_response
            
        except (UserError, AuthenticationError):
            raise
        except (ValueError, ValidationError, UserServiceError) as e:
            raise UserError(str(e), status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error(f"User creation failed: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="User creation service temporarily unavailable"
            )

    async def get_users(
        self,
        pagination: Dict[str, int],
        filters: Dict[str, Any],
        current_user: CurrentUser
    ) -> UserListResponse:
        """Get users with filtering and authorization."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "users:read")
            
            # Build authorized query
            authorized_query = await self.auth_service.build_user_query(
                current_user, filters
            )
            
            # Get users with pagination
            users, total_count = await self.user_service.get_users_paginated(
                authorized_query, pagination
            )
            
            # Build response objects
            user_responses = []
            for user in users:
                user_response = await self._build_user_response(user)
                user_responses.append(user_response)
            
            return UserListResponse(
                users=user_responses,
                total=total_count,
                page=pagination["skip"] // pagination["limit"] + 1,
                limit=pagination["limit"]
            )
            
        except (BaseDomainError, HTTPException, ValueError):
            raise
        except Exception as e:
            logger.error(f"Failed to get users: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="User service temporarily unavailable"
            )

    async def get_user(
        self,
        user_id: str,
        current_user: CurrentUser
    ) -> UserResponse:
        """Get single user with authorization."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "users:read")
            
            # Validate user ID
            validated_user_id = validate_object_id(user_id)
            
            # Get user
            user = await self.user_service.get_user_by_id(validated_user_id)
            if not user:
                raise UserError("User not found", status.HTTP_404_NOT_FOUND)
            
            # Check authorization for this specific user
            await self.auth_service.check_user_access(current_user, user, "read")
            
            # Build response
            user_response = await self._build_user_response(user)
            
            return user_response
            
        except (BaseDomainError, HTTPException, ValueError):
            raise
        except Exception as e:
            logger.error(f"Failed to get user {user_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="User service temporarily unavailable"
            )

    async def update_user(
        self,
        user_id: str,
        update_data: UserUpdate,
        current_user: CurrentUser
    ) -> UserResponse:
        """Update user with validation and authorization."""
        try:
            # Rate limiting
            await self.rate_limiter.check_user_limit(current_user.id, cost=5)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "users:update")
            
            # Validate user ID
            validated_user_id = validate_object_id(user_id)
            
            # Get existing user
            existing_user = await self.user_service.get_user_by_id(validated_user_id)
            if not existing_user:
                raise UserError("User not found", status.HTTP_404_NOT_FOUND)
            
            # Check authorization for this specific user
            await self.auth_service.check_user_access(current_user, existing_user, "update")
            
            # Validate update data
            validated_update = await self._validate_user_update(update_data, existing_user)

            if validated_update.roles is not None:
                await self._enforce_role_assignment_scope(
                    current_user,
                    validated_update.roles,
                    getattr(existing_user, "organization_id", None),
                    getattr(existing_user, "projects", []) or [],
                )
            
            # Check for conflicts if updating email or username
            if validated_update.email or validated_update.username:
                existing_with_credentials = await self.user_service.check_user_exists(
                    validated_update.email or existing_user.email,
                    validated_update.username or existing_user.username,
                    exclude_user_id=validated_user_id
                )
                if existing_with_credentials:
                    raise UserError(
                        "User with this email or username already exists",
                        status.HTTP_409_CONFLICT
                    )
            
            # Hash new password if provided
            if validated_update.password:
                validated_update.hashed_password = get_password_hash(validated_update.password)
                validated_update.password = None  # Don't store plain password
            
            # Update user
            updated_user = await self.user_service.update_user(
                validated_user_id, validated_update
            )
            
            # Audit log
            changed_fields = self._get_changed_fields(existing_user, validated_update)
            await self.audit_logger.log_user_updated(
                current_user.id, validated_user_id, changed_fields
            )
            
            # Invalidate sessions if critical fields changed
            if any(field in changed_fields for field in ['roles', 'disabled', 'password']):
                await self.authentication_service.invalidate_user_sessions(validated_user_id)
            
            # Build response
            user_response = await self._build_user_response(updated_user)
            
            return user_response
            
        except UserError:
            raise
        except Exception as e:
            logger.error(f"Failed to update user {user_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"User update failed: {str(e)}"
            )

    async def delete_user(
        self,
        user_id: str,
        current_user: CurrentUser
    ) -> Dict[str, str]:
        """Delete user with comprehensive checks."""
        try:
            # Rate limiting for destructive operations
            await self.rate_limiter.check_user_limit(current_user.id, cost=20)
            
            # Authorization check
            await self.auth_service.require_permission(current_user, "users:delete")
            
            # Validate user ID
            validated_user_id = validate_object_id(user_id)
            
            # Get user for validation
            user = await self.user_service.get_user_by_id(validated_user_id)
            if not user:
                raise UserError("User not found", status.HTTP_404_NOT_FOUND)
            
            # Check authorization for this specific user
            await self.auth_service.check_user_access(current_user, user, "delete")
            
            # Prevent self-deletion
            if str(validated_user_id) == str(current_user.id):
                raise UserError(
                    "Cannot delete your own account",
                    status.HTTP_400_BAD_REQUEST
                )
            
            # Check for dependencies (created documents, etc.)
            dependencies = await self.user_service.check_user_dependencies(validated_user_id)
            if dependencies:
                raise UserError(
                    f"Cannot delete user with active dependencies: {', '.join(dependencies)}",
                    status.HTTP_409_CONFLICT
                )
            
            # Delete user (this should also invalidate sessions)
            delete_success = await self.user_service.delete_user(validated_user_id)
            if not delete_success:
                raise UserError("User not found", status.HTTP_404_NOT_FOUND)
            
            # Audit log
            await self.audit_logger.log_user_deleted(
                current_user.id, validated_user_id, user.email
            )
            
            return {"message": "User deleted successfully"}
            
        except UserError:
            raise
        except Exception as e:
            logger.error(f"Failed to delete user {user_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="User deletion failed"
            )

    async def logout_user(
        self,
        current_user: CurrentUser,
        session_token: str
    ) -> Dict[str, str]:
        """Logout user and invalidate session."""
        try:
            # Invalidate current session
            await self.authentication_service.invalidate_session(session_token)
            
            # Audit log
            await self.audit_logger.log_user_logged_out(current_user.id, current_user.email)
            
            return {"message": "Logged out successfully"}
            
        except Exception as e:
            logger.error(f"Logout failed for user {current_user.id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Logout service temporarily unavailable"
            )

    async def _validate_user_input(self, user_data: UserCreatePayload) -> UserCreate:
        """Validate and sanitize user input from the client payload."""
        try:
            validated_email = validate_email(user_data.email)

            validated_first_name = sanitize_text(
                validate_input(
                    user_data.first_name,
                    max_length=50,
                    required=True,
                )
            )
            validated_last_name = sanitize_text(
                validate_input(
                    user_data.last_name,
                    max_length=50,
                    required=True,
                )
            )

            validated_username = sanitize_text(
                validate_input(
                    user_data.username,
                    max_length=50,
                    required=True,
                    pattern=r"^[a-zA-Z0-9_.-]+$",  # Alphanumeric, underscore, dot, dash only
                )
            )

            password_validation = validate_password_strength(user_data.password)
            if not password_validation.is_valid:
                raise UserError(
                    f"Password requirements not met: {password_validation.error}",
                    status.HTTP_400_BAD_REQUEST,
                )

            validated_roles: List[str] = []
            for role in (user_data.roles or []):
                normalized_role = sanitize_text(str(role).lower())
                if normalized_role:
                    validated_roles.append(normalized_role)
            if not validated_roles:
                raise UserError("At least one role must be provided", status.HTTP_400_BAD_REQUEST)

            organization_id = user_data.organization_id.strip() if user_data.organization_id else None

            projects: List[str] = []
            for project in (user_data.projects or []):
                project_id = str(project).strip()
                if project_id:
                    projects.append(project_id)

            organizations = list(user_data.organizations or [])
            if not organizations and organization_id:
                organizations = [organization_id]

            permissions: List[str] = []
            for permission in (user_data.permissions or []):
                normalized_permission = sanitize_text(str(permission).lower())
                if normalized_permission:
                    permissions.append(normalized_permission)

            preferences = user_data.preferences or Preferences()

            return UserCreate(
                username=validated_username,
                email=validated_email,
                password=user_data.password,  # Will be hashed later
                roles=validated_roles,
                first_name=validated_first_name,
                last_name=validated_last_name,
                organization_id=organization_id,
                organizations=organizations,
                projects=projects,
                permissions=permissions,
                preferences=preferences,
            )
        except ValidationError as exc:
            raise UserError(str(exc), status.HTTP_400_BAD_REQUEST)
        except ValueError as exc:
            raise UserError(str(exc), status.HTTP_400_BAD_REQUEST)

    async def _validate_user_update(
        self, update_data: UserUpdate, existing_user: User
    ) -> UserUpdate:
        """Validate user update data."""
        validated_fields = {}
        
        if update_data.first_name is not None:
            validated_fields['first_name'] = sanitize_text(
                validate_input(
                    update_data.first_name,
                    max_length=50,
                    required=True,
                )
            )

        if update_data.last_name is not None:
            validated_fields['last_name'] = sanitize_text(
                validate_input(
                    update_data.last_name,
                    max_length=50,
                    required=True,
                )
            )

        if update_data.username is not None:
            validated_fields['username'] = sanitize_text(
                validate_input(
                    update_data.username,
                    max_length=50,
                    required=True,
                    pattern=r'^[a-zA-Z0-9_.-]+$'
                )
            )
        
        if update_data.email is not None:
            validated_fields['email'] = validate_email(update_data.email)
        
        if update_data.password is not None:
            password_validation = validate_password_strength(update_data.password)
            if not password_validation.is_valid:
                raise UserError(
                    f"Password requirements not met: {password_validation.error}",
                    status.HTTP_400_BAD_REQUEST
                )
            validated_fields['password'] = update_data.password
        
        if update_data.roles is not None:
            validated_roles = []
            for role in update_data.roles:
                normalized_role = sanitize_text(str(role).lower())
                validated_roles.append(normalized_role)
            validated_fields['roles'] = validated_roles
        
        if update_data.disabled is not None:
            validated_fields['disabled'] = update_data.disabled
        
        if update_data.organization_id is not None:
            validated_fields['organization_id'] = update_data.organization_id
        
        if update_data.projects is not None:
            validated_fields['projects'] = update_data.projects
        
        return UserUpdate(**validated_fields)

    async def _validate_user_context(
        self, user_data: UserCreate, current_user: CurrentUser
    ):
        """Validate user organizational context and role assignments."""
        actor_roles = set(current_user.roles or [])
        if "superadmin" not in actor_roles and not ({"orgadmin", "projectadmin"} & actor_roles):
            raise UserError(
                "Not authorized to manage users",
                status.HTTP_403_FORBIDDEN,
            )
        # Role assignment validation
        await self.auth_service.validate_role_assignment(current_user, user_data.roles)
        
        # Organizational context validation
        user_roles = set(current_user.roles or [])
        
        if "superadmin" in user_roles:
            # Superadmin can create users in any organization
            pass
        else:
            # Other roles must create users within their own organization
            current_org = getattr(current_user, "organization_id", None)
            if not current_org:
                raise UserError(
                    "Not authorized to create users without organization context",
                    status.HTTP_403_FORBIDDEN
                )
            if user_data.organization_id and str(user_data.organization_id) != str(current_org):
                raise UserError(
                    "Not authorized to create user in another organization",
                    status.HTTP_403_FORBIDDEN
                )
            # Set user's organization to current user's organization
            user_data.organization_id = str(current_org)

        # Project-scoped roles must include project assignments; project-scoped actors cannot assign outside their projects
        target_roles = set(user_data.roles or [])
        if {"projectadmin", "projectuser"} & target_roles:
            if not user_data.projects:
                raise UserError(
                    "Project-scoped users must have at least one project assignment",
                    status.HTTP_400_BAD_REQUEST,
                )
            if {"projectadmin", "projectuser"} & user_roles:
                allowed_projects = {str(p) for p in getattr(current_user, "projects", []) or [] if p}
                if allowed_projects and not set(user_data.projects).issubset(allowed_projects):
                    raise UserError(
                        "Not authorized to assign users to projects outside your scope",
                        status.HTTP_403_FORBIDDEN,
                    )

        await self._enforce_role_assignment_scope(
            current_user,
            user_data.roles or [],
            user_data.organization_id,
            user_data.projects or [],
        )

    async def _resolve_role_scope(self, role_id: str) -> dict:
        role = await self.role_service.get_role_by_id(role_id)
        if not role:
            role = await self.role_service.get_role_by_name(role_id)
        if role:
            return {
                "id": role.id,
                "name": role.name,
                "scope": role.scope,
                "organization_id": role.organization_id,
                "project_id": role.project_id,
            }
        key = str(role_id or "").lower()
        if key in {"superadmin", "superuser"}:
            scope = "system"
        elif "project" in key:
            scope = "project"
        elif "org" in key:
            scope = "organization"
        else:
            scope = "organization"
        return {
            "id": role_id,
            "name": role_id,
            "scope": scope,
            "organization_id": None,
            "project_id": None,
        }

    async def _enforce_role_assignment_scope(
        self,
        current_user: CurrentUser,
        target_roles: List[str],
        target_org_id: Optional[str],
        target_projects: List[str],
    ) -> None:
        role_names = set(current_user.roles or [])
        if "superadmin" in role_names:
            return

        if not ({"orgadmin", "projectadmin"} & role_names):
            raise UserError(
                "Not authorized to assign roles",
                status.HTTP_403_FORBIDDEN,
            )

        actor_org_id = str(getattr(current_user, "organization_id", "") or "")
        actor_projects = {str(p) for p in (getattr(current_user, "projects", []) or []) if p}
        target_projects_set = {str(p) for p in (target_projects or []) if p}

        for role_id in target_roles or []:
            role_meta = await self._resolve_role_scope(role_id)
            scope = (role_meta.get("scope") or "organization").lower()
            role_keys = {
                _normalize_role_key(role_meta.get("id")),
                _normalize_role_key(role_meta.get("name")),
            }
            if scope == "system":
                raise UserError("Not authorized to assign system roles", status.HTTP_403_FORBIDDEN)

            if "orgadmin" in role_names:
                if role_keys & ROLE_KEYS_ORGADMIN:
                    raise UserError("Not authorized to assign organization admin role", status.HTTP_403_FORBIDDEN)
                if scope not in {"organization", "project"}:
                    raise UserError("Invalid role scope for organization admin", status.HTTP_403_FORBIDDEN)
                if role_meta.get("organization_id") and str(role_meta["organization_id"]) != actor_org_id:
                    raise UserError("Not authorized to assign roles outside your organization", status.HTTP_403_FORBIDDEN)
                if scope == "project":
                    if not target_projects_set:
                        raise UserError("Project roles require project assignment", status.HTTP_403_FORBIDDEN)
                    role_project = role_meta.get("project_id")
                    if role_project and str(role_project) not in target_projects_set:
                        raise UserError("Project role must match user's project assignment", status.HTTP_403_FORBIDDEN)
                continue

            if "projectadmin" in role_names:
                if role_keys & ROLE_KEYS_PROJECTADMIN:
                    raise UserError("Not authorized to assign project admin role", status.HTTP_403_FORBIDDEN)
                if scope != "project":
                    raise UserError("Project admins may only assign project roles", status.HTTP_403_FORBIDDEN)
                if not target_projects_set:
                    raise UserError("Project roles require project assignment", status.HTTP_403_FORBIDDEN)
                role_project = role_meta.get("project_id")
                if role_project and str(role_project) not in actor_projects:
                    raise UserError("Not authorized to assign roles outside your project scope", status.HTTP_403_FORBIDDEN)
                if target_projects_set and not target_projects_set.issubset(actor_projects):
                    raise UserError("Not authorized to assign roles outside your project scope", status.HTTP_403_FORBIDDEN)

    async def _build_user_response(self, user: User) -> UserResponse:
        """Build user response with resolved names (compatible with models.user.UserResponse)."""
        # Resolve names
        org_name = None
        if getattr(user, "organization_id", None):
            org_name = await self.user_service.get_organization_name(user.organization_id)

        project_names: List[str] = []
        if getattr(user, "projects", None):
            project_names = await self.user_service.get_project_names(user.projects)

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

        return UserResponse(
            id=str(getattr(user, "id", "")),
            username=getattr(user, "username", "") or getattr(user, "email", ""),
            email=getattr(user, "email", ""),
            first_name=first_name,
            last_name=last_name,
            roles=getattr(user, "roles", []) or [],
            organization_id=str(getattr(user, "organization_id", "")) if getattr(user, "organization_id", None) else None,
            organizations=[str(o) for o in organizations],
            projects=[str(pid) for pid in (getattr(user, "projects", []) or [])],
            permissions=[str(p) for p in permissions],
            account_type=getattr(user, "account_type", "client_user") or "client_user",
            is_active=is_active,
            is_verified=is_verified,
            preferences=preferences,
            created_at=created_at,
            last_login=last_login,
            organization_name=org_name,
            project_names=project_names,
        )

    def _get_changed_fields(self, original: User, update: UserUpdate) -> List[str]:
        """Get list of fields that were changed."""
        changed_fields = []
        
        for field_name in update.__fields_set__:
            if hasattr(original, field_name):
                old_value = getattr(original, field_name)
                new_value = getattr(update, field_name)
                if old_value != new_value:
                    changed_fields.append(field_name)
        
        return changed_fields


# Dependency injection
async def get_user_controller(db = Depends(get_db)) -> UserController:
    """Factory function for user controller."""
    user_service = UserService(db)
    auth_service = AuthorizationService()
    authentication_service = AuthenticationService()
    rate_limiter = RateLimiter(
        requests_per_minute=60,
        window_seconds=3600
    )
    audit_logger = AuditLogger()
    notification_service = NotificationService(db)
    
    return UserController(
        user_service, auth_service, authentication_service,
        rate_limiter, audit_logger, notification_service
    )


# API Endpoints
@router.post("/token", response_model=LoginResponse, deprecated=True)
@handle_exceptions
async def login_for_access_token(
    login_data: LoginRequest,
    response: Response,
    client_ip: str = Depends(_client_ip_dep),
    user_agent: str = Depends(_user_agent_dep),
    controller: UserController = Depends(get_user_controller)
):
    """Authenticate user and return access token."""
    _mark_legacy_auth_response(response, "/api/login")
    return await controller.authenticate_user(login_data, client_ip, user_agent)


@router.post("/users", response_model=UserResponse)
@handle_exceptions
async def create_user(
    user_data: UserCreatePayload,
    controller: UserController = Depends(get_user_controller),
    current_user: CurrentUser = Depends(get_current_user),
    _: None = Depends(require_permission("users:create")),
):
    """Create new user with validation."""
    return await controller.create_user(user_data, current_user)


@router.get("/users", response_model=UserListResponse)
@handle_exceptions
async def get_users(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(None),
    role: Optional[str] = Query(None),
    organization_id: Optional[str] = Query(None),
    disabled: Optional[bool] = Query(None),
    controller: UserController = Depends(get_user_controller),
    current_user: CurrentUser = Depends(get_current_user),
    _: None = Depends(require_permission("users:read")),
):
    """Get users with filtering and pagination."""
    filters = {
        'search': search,
        'role': role,
        'organization_id': organization_id,
        'disabled': disabled
    }
    pagination = {'skip': skip, 'limit': limit}
    
    return await controller.get_users(pagination, filters, current_user)


@router.get("/users/me", response_model=UserResponse)
@handle_exceptions
async def get_current_user_profile(
    controller: UserController = Depends(get_user_controller),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get current user's profile."""
    return await controller.get_user(str(current_user.id), current_user)


@router.get("/users/{user_id}", response_model=UserResponse)
@handle_exceptions
async def get_user(
    user_id: str,
    controller: UserController = Depends(get_user_controller),
    current_user: CurrentUser = Depends(get_current_user),
    _: None = Depends(require_permission("users:read")),
):
    """Get specific user by ID."""
    return await controller.get_user(user_id, current_user)


@router.put("/users/{user_id}", response_model=UserResponse)
@handle_exceptions
async def update_user(
    user_id: str,
    update_data: UserUpdate,
    controller: UserController = Depends(get_user_controller),
    current_user: CurrentUser = Depends(get_current_user),
    _: None = Depends(require_permission("users:update")),
):
    """Update user with validation."""
    return await controller.update_user(user_id, update_data, current_user)


@router.delete("/users/{user_id}")
@handle_exceptions
async def delete_user(
    user_id: str,
    request: Request,
    controller: UserController = Depends(get_user_controller),
    current_user: CurrentUser = Depends(get_current_user),
    _: None = Depends(require_permission("users:delete")),
):
    """Delete user with dependency checks."""
    await require_step_up(request, current_user, action="users.delete")
    return await controller.delete_user(user_id, current_user)


@router.post("/logout", deprecated=True)
@handle_exceptions
async def logout_user(
    request: Request,
    response: Response,
    controller: UserController = Depends(get_user_controller),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Logout current user and invalidate session."""
    _mark_legacy_auth_response(response, "/api/logout")
    session_token = request.headers.get("authorization", "").replace("Bearer ", "")
    if not session_token:
        session_token = request.cookies.get(settings.AUTH_COOKIE_NAME, "")
    return await controller.logout_user(current_user, session_token)


# Additional security endpoints
@router.post("/users/{user_id}/lock")
@handle_exceptions
async def lock_user_account(
    user_id: str,
    request: Request,
    controller: UserController = Depends(get_user_controller),
    current_user: CurrentUser = Depends(get_current_user),
    _: None = Depends(require_permission("users:update")),
):
    """Lock user account for security purposes."""
    await require_step_up(request, current_user, action="users.lock")
    await controller.auth_service.require_permission(current_user, "users:lock")
    
    validated_user_id = validate_object_id(user_id)
    await controller.authentication_service.lock_user_account(validated_user_id)
    await controller.audit_logger.log_user_account_locked(current_user.id, validated_user_id)
    
    return {"message": "User account locked successfully"}


@router.post("/users/{user_id}/unlock")
@handle_exceptions
async def unlock_user_account(
    user_id: str,
    request: Request,
    controller: UserController = Depends(get_user_controller),
    current_user: CurrentUser = Depends(get_current_user),
    _: None = Depends(require_permission("users:update")),
):
    """Unlock user account."""
    await require_step_up(request, current_user, action="users.unlock")
    await controller.auth_service.require_permission(current_user, "users:unlock")
    
    validated_user_id = validate_object_id(user_id)
    await controller.authentication_service.unlock_user_account(validated_user_id)
    await controller.audit_logger.log_user_account_unlocked(current_user.id, validated_user_id)
    
    return {"message": "User account unlocked successfully"}
