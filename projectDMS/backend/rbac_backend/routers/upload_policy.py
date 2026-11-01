"""Serve the canonical upload policy to the client."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..core.security import CurrentUser, get_current_user
from ..services.upload_policy import UploadPolicyService
from ..utils.error_handler import BaseDomainError

router = APIRouter(prefix="/config", tags=["config"])


@router.get("/upload-policy")
async def get_upload_policy(
    current_user: CurrentUser = Depends(get_current_user),
) -> dict:
    """Return the allowed MIME types, extensions, and size caps per surface.

    Authenticated but not scoped: the policy is deployment configuration, not
    tenant data, and every authenticated user needs it to render a file picker
    that matches what the backend will actually accept.
    """
    try:
        return {"policy": UploadPolicyService.get_policy()}
    except (BaseDomainError, HTTPException):
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(
            status_code=500, detail="Upload policy unavailable"
        ) from exc
