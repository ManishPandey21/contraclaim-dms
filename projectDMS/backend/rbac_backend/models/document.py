# Enhanced document_models.py with Bulk Upload Support

from pydantic import BaseModel, Field, ConfigDict, model_validator
from typing import Optional, List, Any, Dict
import uuid
from datetime import datetime
from bson.objectid import ObjectId
from ..utils.datetime import now_utc, isoformat_z

class DocumentReference(BaseModel):
    documentId: str = Field(...)
    linkType: str = Field(default="indirect", pattern=r"^(direct|indirect)$")
    description: Optional[str] = Field(default=None)
    linkedAt: datetime = Field(default_factory=now_utc)
    linkedBy: Optional[str] = Field(default=None)
    letterNo: Optional[str] = Field(default=None)
    source: Optional[str] = Field(default=None)
    metadata: Optional[Dict[str, Any]] = Field(default=None)

class MetadataReference(BaseModel):
    """Structured metadata extracted from source documents."""

    letter_no: Optional[str] = Field(default=None, alias="letterNo")
    date: Optional[str] = None
    text: Optional[str] = None
    description: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    @model_validator(mode="before")
    @classmethod
    def _coerce_raw(cls, value: Any):
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return value
        if value is None:
            return {}
        return {"text": str(value)}

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

class StorageLocation(BaseModel):
    """Represents one stored copy of a document."""

    provider: str = Field(..., description="e.g., local, s3, azure")
    path: Optional[str] = Field(default=None, description="Local path or object key")
    url: Optional[str] = Field(default=None, description="Direct or presigned URL, if available")
    status: Optional[str] = Field(default="ok")

class Document(BaseModel):
    previous_letter_id: Optional[str] = None
    chain_head_id: Optional[str] = None
    chain_position: int = 1
    id: str = Field(default_factory=lambda: str(uuid.uuid4()), alias="_id")
    organization_id: str = Field(...)
    project_id: str = Field(default="")
    project_name: Optional[str] = Field(default=None)
    filename: str = Field(...)
    filepath_local: Optional[str] = Field(default=None)
    filepath_s3: Optional[str] = Field(default=None)
    presigned_url: Optional[str] = None
    filetype: str = Field(...)
    filesize: int = Field(...)
    uploadType: str = Field(..., pattern=r"(?i)^(incoming|outgoing|contract)$")
    letterNo: Optional[str] = Field(None)
    letterNoNormalized: Optional[str] = Field(default=None)
    date: datetime = Field(...)
    subject: str = Field(...)
    from_: Optional[str] = Field(None, alias="from")
    to: Optional[str] = Field(None)
    tags: List[str] = Field(default_factory=list)
    subTags: List[str] = Field(default_factory=list)
    status: str = Field(...)
    ocrEnabled: bool = Field(default=False)
    compressionEnabled: bool = Field(default=False)
    ocrText: Optional[str] = Field(None)
    full_text: Optional[str] = Field(default=None)
    keywords: Optional[List[str]] = Field(default=None)
    additional_keywords: Optional[List[str]] = Field(default=None)
    contractual_clauses: Optional[List[str]] = Field(default=None)
    key_reply_points: Optional[List[str]] = Field(default=None)
    asset_type: Optional[str] = Field(default=None)
    location: Optional[str] = Field(default=None)
    specific_area: Optional[str] = Field(default=None)
    chainage_from: Optional[str] = Field(default=None)
    chainage_to: Optional[str] = Field(default=None)
    work_type: Optional[str] = Field(default=None)
    issue_nature: Optional[str] = Field(default=None)
    claim_category: Optional[str] = Field(default=None)
    alleged_responsibility: Optional[str] = Field(default=None)
    priority: Optional[str] = Field(default=None)
    linked_event_suggested: Optional[str] = Field(default=None)
    reference_chain: Optional[str] = Field(default=None)
    extracted_tags: Optional[List[str]] = Field(default=None)
    extracted_subTags: Optional[List[str]] = Field(default=None)
    metadata: Optional[Dict[str, Any]] = Field(default=None)
    reference: Optional[List[MetadataReference]] = Field(default=None)
    summary: Optional[str] = Field(default=None)
    processing_status: Optional[str] = Field(default=None)
    processing_job_id: Optional[str] = Field(default=None)
    processing_metadata: Optional[Dict[str, Any]] = Field(default=None)
    processing_error: Optional[Dict[str, Any]] = Field(default=None)
    processed_path: Optional[str] = Field(default=None)
    metadata_source: Optional[str] = Field(default=None)
    processed_at: Optional[datetime] = Field(default=None)
    contract_upload_id: Optional[str] = Field(default=None)
    contract_categories: List[str] = Field(default_factory=list)
    contract_error: Optional[str] = Field(default=None)
    sha256: Optional[str] = Field(default=None)
    page_count: Optional[int] = Field(default=None)
    duplicate_status: Optional[str] = Field(default=None)
    duplicate_of: Optional[str] = Field(default=None)
    revision_of: Optional[str] = Field(default=None)
    duplicate_review: Optional[Dict[str, Any]] = Field(default=None)
    createdAt: datetime = Field(default_factory=now_utc)
    updatedAt: datetime = Field(default_factory=now_utc)
    createdBy: str = Field(...)
    version: str = Field(default="1.0")
    enclosures: List[Enclosure] = Field(default_factory=list)
    references: List[DocumentReference] = Field(default_factory=list)
    referencedBy: List[DocumentReference] = Field(default_factory=list)
    storage_locations: List[StorageLocation] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True, json_encoders={ObjectId: str, datetime: isoformat_z})

    @model_validator(mode="before")
    @classmethod
    def _coerce_ids(cls, data):
        """
        Accept MongoDB ObjectId values by converting them to strings on input.
        Also tolerate incoming 'id' field by mapping it to alias '_id'.
        """
        if isinstance(data, dict):
            d = dict(data)
            # Normalize _id / id to string
            if isinstance(d.get("_id"), ObjectId):
                d["_id"] = str(d["_id"])
            if "_id" not in d and "id" in d and isinstance(d["id"], (str, ObjectId)):
                d["_id"] = str(d["id"])
            # Normalize common foreign keys
            for key in ("organization_id", "project_id"):
                if isinstance(d.get(key), ObjectId):
                    d[key] = str(d[key])

            references = d.get("reference")
            if references is not None:
                if isinstance(references, (list, tuple)):
                    normalized_refs = []
                    for item in references:
                        if isinstance(item, MetadataReference):
                            normalized_refs.append(item.model_dump(by_alias=True, exclude_none=True))
                        elif isinstance(item, dict):
                            normalized_refs.append(item)
                        elif item is not None:
                            normalized_refs.append({"text": str(item)})
                    d["reference"] = normalized_refs
                elif isinstance(references, str):
                    d["reference"] = [{"text": references}]

            return d
        return data

class DocumentUpdate(BaseModel):
    uploadType: Optional[str] = Field(None, pattern=r"(?i)^(incoming|outgoing|contract)$")
    letterNo: Optional[str] = None
    date: Optional[datetime] = None
    subject: Optional[str] = None
    from_: Optional[str] = Field(None, alias="from")
    to: Optional[str] = None
    tags: Optional[List[str]] = None
    subTags: Optional[List[str]] = None
    status: Optional[str] = None
    pathStructure: Optional[str] = None
    pathStructure1: Optional[str] = None

    model_config = ConfigDict(populate_by_name=True)


class DocumentSummaryExtractedMetadataUpdate(BaseModel):
    asset_type: Optional[str] = None
    location: Optional[str] = None
    work_type: Optional[str] = None
    issue_nature: Optional[str] = None
    claim_category: Optional[str] = None
    alleged_responsibility: Optional[str] = Field(default=None, alias="responsibility")

    model_config = ConfigDict(populate_by_name=True)


class DocumentSummaryMetadataUpdate(BaseModel):
    extracted_metadata: Optional[DocumentSummaryExtractedMetadataUpdate] = None
    keywords: Optional[List[str]] = None
    additional_keywords: Optional[List[str]] = None
    extracted_tags: Optional[List[str]] = None
    extracted_sub_tags: Optional[List[str]] = None

    model_config = ConfigDict(populate_by_name=True)


# NEW: Bulk Upload Models

class BulkUploadRequest(BaseModel):
    """Request model for bulk document upload."""
    organization_id: str = Field(...)
    project_id: str = Field(...)
    csv_metadata: str = Field(..., description="Base64 encoded CSV content")
    files: List[str] = Field(..., description="List of base64 encoded files")

class DocumentProcessingResult(BaseModel):
    """Result of processing a single document in bulk upload."""
    filename: str = Field(...)
    success: bool = Field(...)
    document_id: Optional[str] = Field(default=None)
    error: Optional[str] = Field(default=None)
    row_number: int = Field(default=0)
    processing_time: Optional[float] = Field(default=None)
    metadata_extracted: bool = Field(default=False)
    ocr_completed: bool = Field(default=False)
    embeddings_created: int = Field(default=0)
    status: Optional[str] = Field(default=None)
    job_id: Optional[str] = Field(default=None)
    message: Optional[str] = Field(default=None)

    model_config = ConfigDict(json_encoders={datetime: isoformat_z})


class DocumentProcessingJobStatus(BaseModel):
    job_id: str = Field(..., alias="_id")
    document_id: str
    status: str = Field(..., description="queued, processing, completed, failed, retrying, dead_lettered")
    stage: Optional[str] = Field(default=None)
    file_path: Optional[str] = Field(default=None)
    attempts: int = Field(default=0)
    max_attempts: int = Field(default=3)
    error: Optional[Dict[str, Any]] = Field(default=None)
    metadata: Optional[Dict[str, Any]] = Field(default=None)
    created_at: datetime = Field(default_factory=now_utc)
    queued_at: Optional[datetime] = Field(default=None)
    started_at: Optional[datetime] = Field(default=None)
    completed_at: Optional[datetime] = Field(default=None)
    updated_at: datetime = Field(default_factory=now_utc)

    model_config = ConfigDict(populate_by_name=True, json_encoders={ObjectId: str, datetime: isoformat_z})

    @model_validator(mode="before")
    @classmethod
    def _coerce_job_id(cls, data):
        if isinstance(data, dict):
            d = dict(data)
            if isinstance(d.get("_id"), ObjectId):
                d["_id"] = str(d["_id"])
            if "_id" not in d and "job_id" in d:
                d["_id"] = str(d["job_id"])
            return d
        return data

class BulkUploadStatus(BaseModel):
    """Status tracking for bulk upload jobs."""
    job_id: str = Field(...)
    total_files: int = Field(...)
    processed_files: int = Field(...)
    successful_uploads: int = Field(...)
    failed_uploads: int = Field(...)
    status: str = Field(..., description="processing, completed, completed_with_errors, failed")
    created_at: datetime = Field(default_factory=now_utc)
    updated_at: datetime = Field(default_factory=now_utc)
    completed_at: Optional[datetime] = Field(default=None)
    error_message: Optional[str] = Field(default=None)
    results: List[DocumentProcessingResult] = Field(default_factory=list)
    
    # Progress metrics
    progress_percentage: Optional[float] = Field(default=None)
    estimated_completion: Optional[datetime] = Field(default=None)
    processing_rate: Optional[float] = Field(default=None)  # files per minute
    
    model_config = ConfigDict(json_encoders={datetime: isoformat_z})

    @model_validator(mode="after")
    def _calculate_progress(self):
        if self.total_files > 0:
            self.progress_percentage = round((self.processed_files / self.total_files) * 100, 2)
        else:
            self.progress_percentage = 0.0
        return self

class BulkUploadResponse(BaseModel):
    """Response model for bulk upload initiation."""
    job_id: str = Field(...)
    message: str = Field(...)
    total_files: int = Field(...)
    status: str = Field(...)
    created_at: datetime = Field(default_factory=now_utc)
    
    model_config = ConfigDict(json_encoders={datetime: isoformat_z})

class DocumentListResponse(BaseModel):
    """Response model for document listings."""
    documents: List[Document] = Field(...)
    total: int = Field(...)
    page: int = Field(default=1)
    size: int = Field(default=20)
    has_next: bool = Field(default=False)
    has_previous: bool = Field(default=False)

class EnclosureResponse(BaseModel):
    """Response model for enclosure operations."""
    enclosure: Enclosure = Field(...)
    message: str = Field(default="Enclosure added successfully")

class ReferenceCreate(BaseModel):
    """Model for creating document references."""
    referenced_document_id: str = Field(...)
    link_type: str = Field(..., pattern=r"^(direct|indirect)$")
    description: Optional[str] = Field(default=None)

class LinkDocumentsRequest(BaseModel):
    """Request model for linking documents."""
    source_document_id: str = Field(...)
    target_document_id: str = Field(...)
    link_type: str = Field(..., pattern=r"^(direct|indirect)$")
    description: Optional[str] = Field(default=None)

class LinkDocumentsResponse(BaseModel):
    """Response model for document linking."""
    message: str = Field(...)
    link_created: bool = Field(...)
    source_document: Document = Field(...)
    target_document: Document = Field(...)

# CSV Template Model
class CSVTemplateRow(BaseModel):
    """Model representing a row in the CSV template."""
    filename: str = Field(..., description="Name of the file to upload")
    upload_type: str = Field(..., pattern=r"^(incoming|outgoing)$", description="incoming or outgoing")
    letter_no: str = Field(..., description="Letter/document number")
    date: str = Field(..., description="Document date (YYYY-MM-DD format)")
    subject: str = Field(..., description="Document subject/title")
    from_: Optional[str] = Field(None, alias="from", description="Sender/from company")
    to: Optional[str] = Field(None, description="Recipient/to company")
    tags: Optional[str] = Field(None, description="Comma-separated tags")
    sub_tags: Optional[str] = Field(None, description="Comma-separated sub-tags")
    status: Optional[str] = Field(default="draft", description="Document status")
    ocr_enabled: Optional[str] = Field(default="true", description="Enable OCR processing (true/false)")
    
    model_config = ConfigDict(
        populate_by_name=True,
        json_schema_extra={
            "example": {
                "filename": "sample-contract-001.pdf",
                "upload_type": "incoming",
                "letter_no": "CONTRACT-2024-001",
                "date": "2024-01-15",
                "subject": "Service Agreement Contract",
                "from": "ABC Corporation",
                "to": "XYZ Services Ltd",
                "tags": "contract,legal,service",
                "sub_tags": "high-priority,annual",
                "status": "draft",
                "ocr_enabled": "true",
            }
        },
    )

# Validation Models
class FileValidationResult(BaseModel):
    """Result of file validation."""
    is_valid: bool = Field(...)
    filename: str = Field(...)
    file_size: int = Field(...)
    file_type: str = Field(...)
    mime_type: str = Field(...)
    error: Optional[str] = Field(default=None)
    warnings: List[str] = Field(default_factory=list)

class CSVValidationResult(BaseModel):
    """Result of CSV validation."""
    is_valid: bool = Field(...)
    total_rows: int = Field(...)
    valid_rows: int = Field(...)
    invalid_rows: int = Field(...)
    errors: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[Dict[str, Any]] = Field(default_factory=list)
    missing_files: List[str] = Field(default_factory=list)
    duplicate_files: List[str] = Field(default_factory=list)

# Background Task Models
class DocumentProcessingTask(BaseModel):
    """Model for document processing background tasks."""
    task_id: str = Field(...)
    document_id: str = Field(...)
    task_type: str = Field(..., description="ocr, metadata_extraction, embedding")
    status: str = Field(..., description="pending, processing, completed, failed")
    created_at: datetime = Field(default_factory=now_utc)
    started_at: Optional[datetime] = Field(default=None)
    completed_at: Optional[datetime] = Field(default=None)
    error_message: Optional[str] = Field(default=None)
    result: Optional[Dict[str, Any]] = Field(default=None)
    
    model_config = ConfigDict(json_encoders={datetime: isoformat_z})
