from typing import List, Optional, Dict, Any
from pydantic import BaseModel, EmailStr, Field, model_validator, GetCoreSchemaHandler
from pydantic_core import core_schema
from bson import ObjectId

class PyObjectId(ObjectId):
    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler: GetCoreSchemaHandler):
        return core_schema.str_schema()

class Preferences(BaseModel):
    emailNotifications: Optional[bool] = True
    sharingAlerts: Optional[bool] = True

class User(BaseModel):
    id: PyObjectId = Field(default_factory=ObjectId, alias="_id")
    username: str = Field(...)
    email: EmailStr = Field(...)
    hashed_password: str = Field(..., exclude=True)  # Exclude password from response
    roles: List[str] = Field([])
    organization_id: Optional[str] = Field(None)
    organizations: List[str] = Field(default_factory=list)
    projects: List[str] = Field([])
    disabled: Optional[bool] = False
    preferences: Preferences = Field(default_factory=Preferences)

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}

    @model_validator(mode='after')
    def validate_role_based_fields(self):
        roles = self.roles or []
        # Super Admin: no tenant scoping fields should be set
        if 'superadmin' in roles:
            if self.organization_id is not None:
                raise ValueError("Super Admin cannot have an Organization ID")
            if self.projects:
                raise ValueError("Super Admin cannot have Projects")
            if self.organizations:
                raise ValueError("Super Admin cannot have Organizations")
        # Super User: must have at least one assigned organization
        elif 'superuser' in roles:
            if not self.organizations:
                raise ValueError("Super User must have at least one assigned organization")
            # If organization_id is provided, ensure it is within organizations
            try:
                if self.organization_id and str(self.organization_id) not in [str(o) for o in (self.organizations or [])]:
                    raise ValueError("organization_id must be one of the assigned organizations for Super User")
            except Exception:
                # best-effort, ignore type coercion issues
                pass
        # Organization-scoped roles
        elif 'orgadmin' in roles or 'orguser' in roles:
            if not self.organization_id:
                raise ValueError("Organization Admin or user must have an Organization ID")
        # Project-scoped roles
        elif 'projectadmin' in roles or 'projectuser' in roles:
            if not self.organization_id:
                raise ValueError("Project Admin or user must have an Organization ID")
            if not self.projects:
                raise ValueError("Project Admin or user must have Projects")
        return self

# Response model without sensitive data
class UserResponse(BaseModel):
    id: str
    username: str
    email: EmailStr
    roles: List[str] = Field([])
    organization_id: Optional[str] = Field(None)
    organizations: List[str] = Field(default_factory=list)
    projects: List[str] = Field([])
    disabled: Optional[bool] = False
    preferences: Preferences = Field(default_factory=Preferences)
    # New display fields for UI convenience
    organization_name: Optional[str] = None
    project_names: List[str] = Field(default_factory=list)
