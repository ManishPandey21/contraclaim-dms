# models/user_models.py
# Shim models to satisfy routers, re-exporting from unified user.py and defining missing schemas.

from __future__ import annotations

from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field

# Re-export primary user models from user.py
from .user import (
    User,          # DB user model
    UserCreate,    # Create payload
    UserUpdate,    # Update payload
    UserResponse,  # API response shape for a user
)

class UserListResponse(BaseModel):
    """Paginated list of users response."""
    users: List[UserResponse]
    total: int
    page: int
    limit: int

class LoginRequest(BaseModel):
    """Login request payload."""
    email: EmailStr
    password: str = Field(..., min_length=1)

class LoginResponse(BaseModel):
    """Login response with access token and user info."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse

class TokenResponse(BaseModel):
    """Access token response (for refresh or similar flows)."""
    access_token: str
    token_type: str = "bearer"
    expires_in: int

class RefreshTokenRequest(BaseModel):
    """Optional refresh token/session exchange request (not always used)."""
    session_token: Optional[str] = None
