from pydantic import BaseModel, Field, validator
from typing import Optional

import uuid
from typing import List, Optional
from datetime import datetime
from bson.objectid import ObjectId
from ..utils.datetime import now_utc, isoformat_z

class DocumentReference(BaseModel):
    documentId: str = Field(...)
    linkType: str = Field(..., pattern=r"^(direct|indirect)$")  # Enforce "direct" or "indirect"

# Proper Enclosure model to match what is actually stored/returned by the API
class Enclosure(BaseModel):
    id: str = Field(...)
    filename: str = Field(...)
    filepath_local: Optional[str] = Field(default=None)
    filepath_s3: Optional[str] = Field(default=None)
    presigned_url: Optional[str] = Field(default=None)
    filetype: str = Field(default="application/octet-stream")
    filesize: int = Field(default=0)
    uploadedAt: datetime = Field(default_factory=now_utc)
    uploadedBy: str = Field(...)

class Document(BaseModel):
    previous_letter_id: Optional[str] = None  # Immediate predecessor in chain
    chain_head_id: Optional[str] = None       # First letter in the chain
    chain_position: int = 1                   # Position in the chain (1-based)

    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    organization_id: str = Field(...)
    project_id: str = Field(...)
    project_name: Optional[str] = Field(default=None)
    filename: str = Field(...)
    filepath_local: str = Field(...)
    filepath_s3: str = Field(...)
    presigned_url: Optional[str] = None
    filetype: str = Field(...)
    filesize: int = Field(...)
    uploadType: str = Field(..., pattern=r"(?i)^(incoming|outgoing)$")  # Enforce "incoming" or "outgoing"
    letterNo: Optional[str] = Field(None)
    date: datetime = Field(...)
    subject: str = Field(...)
    from_: Optional[str] = Field(None, alias="from") #, alias="from"
    to: Optional[str] = Field(None)
    tags: List[str] = Field(default_factory=list)  # Store tag IDs
    subTags: List[str] = Field(default_factory=list) # Store subtag IDs
    status: str = Field(...)
    ocrEnabled: bool = Field(default=False)
    compressionEnabled: bool = Field(default=False)
    ocrText: Optional[str] = Field(None)
    full_text: Optional[str] = Field(default=None)
    # NEW: accept “Key_words” → store as `keywords`
    keywords: Optional[List[str]] = Field(default=None)
    # NEW: accept “Contractual Clauses” → store as `contractual_clauses`
    contractual_clauses: Optional[List[str]] = Field(default=None)
    reference: Optional[List[str]]= Field(default=None)
    # Auto-extracted concise summary (bullet points) from metadata pipeline
    summary: Optional[str] = Field(default=None)
    createdAt: datetime = Field(default_factory=now_utc)
    updatedAt: datetime = Field(default_factory=now_utc)
    createdBy: str = Field(...)
    version: str = Field(default="1.0")
    enclosures: List[Enclosure] = Field(default_factory=list)
    references: List[DocumentReference] = Field(default_factory=list)
    referencedBy: List[DocumentReference] = Field(default_factory=list)

    class Config:
        json_encoders = {
            ObjectId: str,
            datetime: isoformat_z,
        }

class DocumentUpdate(BaseModel):
    uploadType: Optional[str] = Field(None, pattern=r"(?i)^(incoming|outgoing)$")
    letterNo: Optional[str] = None
    date: Optional[datetime] = None
    subject: Optional[str] = None
    from_: Optional[str] = Field(None, alias="from")
    to: Optional[str] = None
    tags: Optional[List[str]] = None
    subTags: Optional[List[str]] = None
    status: Optional[str] = None

    class Config:
        populate_by_name = True
        
        
