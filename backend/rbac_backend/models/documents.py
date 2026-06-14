from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

class Reference(BaseModel):
    id: str
    type: Optional[str] = None
    title: Optional[str] = None

class Enclosure(BaseModel):
    id: str
    name: Optional[str] = None

class DocumentIn(BaseModel):
    title: str
    date: Optional[str] = None
    letterNo: Optional[str] = None
    subject: Optional[str] = None
    from_: Optional[str] = Field(default=None, alias="from")
    to: Optional[str] = None
    tags: List[str] = []
    projectId: Optional[str] = None
    # NEW: accept “Key_words” → store as `keywords`
    keywords: Optional[List[str]] = Field(default=None, alias="Key_words")
    # NEW: accept “Contractual Clauses” → store as `contractual_clauses`
    contractual_clauses: Optional[List[str]] = Field(default=None, alias="Contractual Clauses")
    summary: Optional[str] = None
    content: Optional[str] = None
    references: List[Reference] = []
    enclosures: List[Enclosure] = []

class DocumentOut(DocumentIn):
    id: str
    createdAt: datetime
    updatedAt: datetime
