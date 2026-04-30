from pydantic import BaseModel, Field
from typing import Optional, Any
from datetime import datetime

class ErrorModel(BaseModel):
    error: dict

class Page(BaseModel):
    items: list[Any]
    total: int
    page: int = 1
    pageSize: int = 50

class BaseDoc(BaseModel):
    id: Optional[str] = Field(default=None, alias="_id")
    createdAt: datetime | None = None
    updatedAt: datetime | None = None

    class Config:
        populate_by_name = True
