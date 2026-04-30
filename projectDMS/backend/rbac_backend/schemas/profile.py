from pydantic import BaseModel, EmailStr
from typing import Optional

class ProfileUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[EmailStr] = None
    job_title: Optional[str] = None
    profile_photo_url: Optional[str] = None

class ProfileRead(BaseModel):
    id: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: EmailStr
    job_title: Optional[str] = None
    profile_photo_url: Optional[str] = None

    class Config:
        arbitrary_types_allowed = True

class ChangePassword(BaseModel):
    current_password: str
    new_password: str
