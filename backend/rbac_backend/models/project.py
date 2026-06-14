
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

    
