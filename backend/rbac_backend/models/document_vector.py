from __future__ import annotations
from pydantic import BaseModel, Field, field_validator, ValidationInfo
from typing import List, Optional, Literal
from datetime import datetime

class DocumentVector(BaseModel):
    # DB identity
    id: Optional[str] = Field(default=None, alias="_id")

    # Linkage (hard filters for multi-tenancy & traceability)
    document_id: str = Field(..., description="documents._id (string/ObjectId as str)")
    organization_id: str = Field(..., description="Tenant org id")
    project_id: str = Field(..., description="Project id within org")

    # Source hints (useful for filters and debugging)
    uploadType: Literal["incoming", "outgoing"] = Field(..., description="Document upload type")
    letterNo: Optional[str] = Field(default=None, description="Letter number if present")
    filepath_local: Optional[str] = Field(default=None, description="Local absolute path, if available")
    filepath_s3: Optional[str] = Field(default=None, description="S3 path, if available")

    # Chunk metadata
    page_num: Optional[int] = Field(default=None, ge=1, description="1-based page number if known")
    chunk_index: int = Field(..., ge=0, description="0-based chunk index within the document")
    text: str = Field(..., min_length=1, description="Text content of this chunk")

    # Embedding fields
    embedding_model: str = Field(default="text-embedding-3-small")
    embedding_dims: int = Field(default=1536, ge=1)
    embedding: List[float] = Field(..., description="Vector of length embedding_dims")

    # Diagnostics / housekeeping
    num_tokens: Optional[int] = Field(default=None, ge=0, description="Tokenizer count for `text`")
    chunk_chars: Optional[int] = Field(default=None, ge=0, description="len(text)")
    checksum_sha256: Optional[str] = Field(default=None, description="Checksum of `text`/source slice")
    createdAt: datetime = Field(default_factory=datetime.utcnow)

    # --- Validators ---

    @field_validator("embedding")
    @classmethod
    def _validate_embedding_len(cls, v, info: ValidationInfo):
        # Access sibling fields via info.data in Pydantic v2
        dims = int((info.data or {}).get("embedding_dims", 0) or 0)
        if dims and len(v) != dims:
            raise ValueError(f"embedding length {len(v)} != embedding_dims {dims}")
        return v

    @field_validator("chunk_chars", mode="before")
    @classmethod
    def _default_chunk_chars(cls, v, info: ValidationInfo):
        # Auto-compute chunk length when not provided (best-effort before validation)
        if v is None:
            text = (info.data or {}).get("text")
            if isinstance(text, str):
                return len(text)
        return v

    class Config:
        populate_by_name = True
        from_attributes = True
        extra = "ignore"
