from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status

from ..core.database import get_db
from ..core.security import get_current_user, CurrentUser
from ..schemas.profile import ProfileRead, ProfileUpdate, ChangePassword
from ..services.user_service import UserService
from ..services.upload_streaming import read_upload_within_limit

router = APIRouter(tags=["profiles"])

ALLOWED_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif"}
MAX_PROFILE_PHOTO_SIZE_BYTES = 5 * 1024 * 1024


@router.get("/profiles/health")
async def profiles_health():
    return {"status": "ok"}


@router.get("/profiles/me", response_model=ProfileRead)
async def read_my_profile(
    current_user: CurrentUser = Depends(get_current_user),
    db = Depends(get_db),
):
    """Return the authenticated user's profile details."""
    user_service = UserService(db)
    user = await user_service.get_user_by_id(current_user.id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    return ProfileRead(
        id=user.id,
        first_name=getattr(user, "first_name", None),
        last_name=getattr(user, "last_name", None),
        email=getattr(user, "email", current_user.email),
        job_title=getattr(user, "job_title", None),
        profile_photo_url=getattr(user, "profile_photo_url", None),
    )


@router.put("/profiles/me", response_model=ProfileRead)
async def update_my_profile(
    payload: ProfileUpdate,
    current_user: CurrentUser = Depends(get_current_user),
    db = Depends(get_db),
):
    """
    Update the authenticated user's profile details.
    Supports partial updates of: first_name, last_name, email, job_title.
    """
    user_service = UserService(db)
    # Ensure user exists
    user = await user_service.get_user_by_id(current_user.id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Build $set with provided fields only
    update_fields = {}
    if payload.first_name is not None:
        update_fields["first_name"] = payload.first_name
    if payload.last_name is not None:
        update_fields["last_name"] = payload.last_name
    if payload.email is not None:
        # Check email uniqueness (exclude self)
        try:
            from bson import ObjectId
            oid = ObjectId(str(current_user.id))
        except Exception:
            oid = str(current_user.id)
        existing = await db.users.find_one({"email": payload.email, "_id": {"$ne": oid}})
        if existing:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already in use")
        update_fields["email"] = payload.email
    if payload.job_title is not None:
        # job_title is tolerated even if not part of strict User model; the service returns a light object
        update_fields["job_title"] = payload.job_title
    if payload.profile_photo_url is not None:
        update_fields["profile_photo_url"] = payload.profile_photo_url

    if not update_fields:
        # Nothing to update; return current profile
        return ProfileRead(
            id=user.id,
            first_name=getattr(user, "first_name", None),
            last_name=getattr(user, "last_name", None),
            email=getattr(user, "email", current_user.email),
            job_title=getattr(user, "job_title", None),
            profile_photo_url=getattr(user, "profile_photo_url", None),
        )

    # Apply update
    try:
        from bson import ObjectId
        oid = ObjectId(str(current_user.id))
    except Exception:
        oid = str(current_user.id)

    result = await db.users.update_one({"_id": oid}, {"$set": update_fields})
    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    # Return updated profile
    updated = await user_service.get_user_by_id(current_user.id)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found after update")

    return ProfileRead(
        id=updated.id,
        first_name=getattr(updated, "first_name", None),
        last_name=getattr(updated, "last_name", None),
        email=getattr(updated, "email", current_user.email),
        job_title=getattr(updated, "job_title", None),
        profile_photo_url=getattr(updated, "profile_photo_url", None),
    )


@router.post("/profiles/photo", response_model=ProfileRead)
async def upload_profile_photo(
    file: UploadFile = File(...),
    current_user: CurrentUser = Depends(get_current_user),
    db=Depends(get_db),
):
    """Upload/update authenticated user's profile photo."""
    if file.content_type not in ALLOWED_IMAGE_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only image files are allowed (jpeg, png, webp, gif).",
        )

    # Capped while reading. The 5 MB rule below used to run *after* the whole
    # body was resident, so it bounded what was stored and not what was held.
    content = await read_upload_within_limit(file, MAX_PROFILE_PHOTO_SIZE_BYTES)
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded image is empty.",
        )

    if len(content) > MAX_PROFILE_PHOTO_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Profile photo must be 5MB or less.",
        )

    import base64

    data_url = f"data:{file.content_type};base64,{base64.b64encode(content).decode('utf-8')}"
    if len(data_url.encode("utf-8")) > 7 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Encoded profile photo is too large.",
        )

    user_service = UserService(db)
    user = await user_service.get_user_by_id(current_user.id)
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    try:
        from bson import ObjectId
        oid = ObjectId(str(current_user.id))
    except Exception:
        oid = str(current_user.id)

    result = await db.users.update_one(
        {"_id": oid},
        {"$set": {"profile_photo_url": data_url}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    updated = await user_service.get_user_by_id(current_user.id)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found after update")

    return ProfileRead(
        id=updated.id,
        first_name=getattr(updated, "first_name", None),
        last_name=getattr(updated, "last_name", None),
        email=getattr(updated, "email", current_user.email),
        job_title=getattr(updated, "job_title", None),
        profile_photo_url=getattr(updated, "profile_photo_url", None),
    )


@router.post("/profiles/change-password")
async def change_my_password(
    payload: ChangePassword,
    current_user: CurrentUser = Depends(get_current_user),
    db = Depends(get_db),
):
    """Change password for the authenticated user."""
    user_service = UserService(db)
    try:
        success = await user_service.change_password(
            user_id=current_user.id,
            old_password=payload.current_password,
            new_password=payload.new_password,
        )
        if not success:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Password change failed")
        return {"message": "Password changed successfully"}
    except ValueError as ve:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal error changing password")
