# models/email_models.py

from pydantic import BaseModel, EmailStr, Field, validator
from typing import List, Optional, Dict, Any
from datetime import datetime

class EmailRequest(BaseModel):
    """Request model for sending emails."""
    recipients: List[EmailStr] = Field(..., min_items=1, max_items=50)
    subject: str = Field(..., min_length=1, max_length=200)
    message: str = Field(..., min_length=1, max_length=5000)
    document_id: Optional[str] = None
    template_name: Optional[str] = None
    attachments: Optional[List[Dict[str, Any]]] = None

class EmailResponse(BaseModel):
    """Response model for email operations."""
    message: str
    recipients: List[str]
    status: str
    job_id: Optional[str] = None
    sent_at: Optional[datetime] = None

class EmailTemplate(BaseModel):
    """Model for email templates."""
    name: str = Field(..., min_length=1, max_length=100)
    subject: str = Field(..., max_length=200)
    description: Optional[str] = Field(default="", max_length=500)
    content_type: str = Field(default="html")
    variables: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class EmailAttachment(BaseModel):
    """Model for email attachments."""
    filename: str = Field(..., min_length=1, max_length=255)
    path: str = Field(..., min_length=1)
    content_type: Optional[str] = None
    size: Optional[int] = None
