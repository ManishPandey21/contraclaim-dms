
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator
from bson import ObjectId
from typing import Optional


class Project(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    name: str = Field(...)
    shortName: Optional[str] = Field(default=None)  # Short name for project
    organization_id: str = Field(
        ...,
        validation_alias=AliasChoices("organization_id", "organizationId"),
    )  # Foreign key to Organization
    projectCode: Optional[str] = Field(default=None)
    panNumber: Optional[str] = Field(default=None)
    gstNumber: Optional[str] = Field(default=None)
    address: Optional[str] = Field(default=None)
    city: Optional[str] = Field(default=None)
    state: Optional[str] = Field(default=None)
    pinCode: Optional[str] = Field(default=None)
    adminName: Optional[str] = Field(default=None)
    adminEmail: Optional[str] = Field(default=None)
    adminContact: Optional[str] = Field(default=None)
    billingEnabled: Optional[bool] = Field(default=False)
    # Add other relevant fields like start date, end date, status, etc.

    @field_validator("id", "organization_id", mode="before")
    @classmethod
    def _stringify_object_ids(cls, value):
        if isinstance(value, ObjectId):
            return str(value)
        return value


class ProjectStats(BaseModel):
    """Live per-project counts rendered on the project cards.

    Returned by GET /projects/stats for every project the caller is allowed to
    list, so the UI never issues one request per card.
    """

    project_id: str = Field(...)
    letterCount: int = Field(default=0, ge=0)
    incomingCount: int = Field(default=0, ge=0)
    outgoingCount: int = Field(default=0, ge=0)
    teamSize: int = Field(default=0, ge=0)

    
