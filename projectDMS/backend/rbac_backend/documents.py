from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Query, BackgroundTasks

from ..models.document import Document, DocumentReference, DocumentUpdate, BaseModel
from ..models.tag import Tag, Subtag  # Import Tag and Subtag models
from ..core.database import get_db
from ..core.security import get_current_user, CurrentUser, authorize_scope, build_scope_query
from ..core.config import settings
from ..services.document_linking_service import DocumentLinkingService
from typing import List, Optional, Dict, Union, Any, Literal
import os
from datetime import datetime, timezone
import boto3
from botocore.exceptions import ClientError
from pymongo import ReturnDocument, MongoClient
from bson.objectid import ObjectId
import logging
from bson import Binary, ObjectId
from bson.errors import InvalidId
from uuid import uuid4
import uuid
import urllib.parse
from pydantic import Field
import pymongo
from ..services.metadata import process_document
from ..services import metadata
from ..utils.file_validation import sniff_mime_from_bytes, is_allowed_mime
from ..utils.date_parser import parse_date_safely
from fastapi.responses import FileResponse, StreamingResponse
from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment

router = APIRouter()

# Allowed MIME types for uploads
ALLOWED_DOCUMENT_MIME = {"application/pdf"}
ALLOWED_ENCLOSURE_MIME = {"application/pdf", "image/png", "image/jpeg"}

# Request and response models for linking
class LinkDocumentsRequest(BaseModel):
    document_a_id: str
    document_b_id: str
    link_type: str = Field(..., pattern=r"^(incoming|outgoing)$")

class LinkDocumentsResponse(BaseModel):
    message: str
    linked_documents: List[str]

class ReferenceCreate(BaseModel):
    referenced_document_id: str  # Must match frontend
    link_type: Literal["direct", "indirect"]  # Allowed values

class DocumentListResponse(BaseModel):
    documents: List[Document]
    total: int

# Enclosure model for proper response structure
class EnclosureResponse(BaseModel):
    id: str
    filename: str
    presigned_url: str
    filetype: str
    filesize: int
    uploadedAt: str
    uploadedBy: str

# Configure Logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# AWS S3 client
s3 = boto3.client(
    "s3",
    aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
    aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    region_name=settings.AWS_REGION,
)
# Base uploads directory (configurable); use absolute path to avoid CWD issues in production
BASE_UPLOAD_DIR = os.path.abspath(settings.UPLOADS_DIR or "uploads")


def run_ocr_background(
    pdf_path: str,
    pathStructure1: str,
    uploadType: str,
    document_id: str  # Add parameter
):
    try:
        # ...
        process_document(
            pdf_path=pdf_path,
            pathStructure1=pathStructure1,
            uploadType=uploadType,
            document_id=document_id  # Pass to process_document
        )
        # ...

    except Exception as e:
        logger.error(f"OCR processing failed: {str(e)}")

# Helper function to populate tag and subtag names
async def populate_tags(doc: Dict[str, Any], db: MongoClient) -> Dict[str, Any]:
    """Populates tag and subtag names in a document.

    Important: Some legacy documents may already store tag/subtag NAMES instead of IDs.
    This function will only attempt to resolve values that look like valid ObjectIds.
    If none are valid ObjectIds, the original values are preserved (no exception).
    """
    # Tags
    try:
        if "tags" in doc and doc["tags"]:
            valid_tag_ids: list[ObjectId] = []
            for tag_id in doc["tags"]:
                try:
                    valid_tag_ids.append(ObjectId(tag_id))
                except Exception:
                    # Leave non-ObjectId entries as-is (likely already names)
                    continue
            if valid_tag_ids:
                tags = await db.tags.find({"_id": {"$in": valid_tag_ids}}).to_list(length=None)
                # If we successfully resolved any, replace with names; otherwise keep original
                if tags:
                    doc["tags"] = [tag.get("name", str(tag.get("_id", ""))) for tag in tags]
    except Exception:
        # Never let tag resolution break listing
        pass

    # SubTags
    try:
        if "subTags" in doc and doc["subTags"]:
            valid_subtag_ids: list[ObjectId] = []
            for subtag_id in doc["subTags"]:
                try:
                    valid_subtag_ids.append(ObjectId(subtag_id))
                except Exception:
                    continue
            if valid_subtag_ids:
                subtags = await db.subtags.find({"_id": {"$in": valid_subtag_ids}}).to_list(length=None)
                if subtags:
                    doc["subTags"] = [subtag.get("name", str(subtag.get("_id", ""))) for subtag in subtags]
    except Exception:
        pass

    return doc

# Robust datetime parser for legacy/mixed formats
def _safe_parse_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        # treat as epoch seconds if looks like seconds, or ms if very large
        try:
            if value > 1e12:
                return datetime.fromtimestamp(value / 1000)
            return datetime.fromtimestamp(value)
        except Exception:
            return datetime.now()
    if isinstance(value, str):
        s = value.strip()
        # Normalize trailing Z
        try:
            if s.endswith("Z"):
                from datetime import timezone
                return datetime.fromisoformat(s.replace("Z", "+00:00"))
            return datetime.fromisoformat(s)
        except Exception:
            pass
        # Try common patterns
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%d/%m/%Y %H:%M:%S"):
            try:
                return datetime.strptime(s, fmt)
            except Exception:
                continue
    return datetime.now()

@router.post("/documents", response_model=Document)
async def create_document(
    background_tasks: BackgroundTasks,

    previous_letter_id: Optional[str] = Form(None),  # New field for tracking chain

    file: UploadFile = File(...),
    organization_id: str = Form(...),
    project_id: str = Form(...),
    uploadType: str = Form(...),
    letterNo: Optional[str] = Form(None),
    date: str = Form(...),  # Changed from datetime to str for better parsing
    subject: Optional[str] = Form(""),  # Made optional with default
    from_: Optional[str] = Form(None, alias="from"),  # Accept 'from' key
    from_legacy: Optional[str] = Form(None, alias="from_"),  # Backward-compat: accept 'from_' key
    to: Optional[str] = Form(None),
    tags: Optional[List[str]] = Form(None),  # Expecting list of tag IDs
    subTags: Optional[List[str]] = Form(None),  # Expecting list of subtag IDs
    status: Optional[str] = Form(""),  # Made optional with default
    pathStructure: str = Form(...),
    pathStructure1: str = Form(...),
    ocrEnabled: str = Form("0"),
    compressionEnabled: str = Form("0"),
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    ocr_enabled_bool = ocrEnabled.lower() in ('true', '1', 't', 'y', 'yes')
    compression_enabled_bool = compressionEnabled.lower() in ('true', '1', 't', 'y', 'yes')

    try:
        logger.info(f"Starting document upload for user {current_user.id}")
        logger.info(f"Upload parameters: letterNo={letterNo}, uploadType={uploadType}, date={date}, ocrEnabled={ocrEnabled}")


        # Validate required fields
        if not file.filename:
            raise HTTPException(status_code=400, detail="No file provided")

        if not letterNo:
            raise HTTPException(status_code=400, detail="Letter number is required")

        # Read file once and validate MIME type (PDF only for main documents)
        content = await file.read()
        detected_mime = sniff_mime_from_bytes(content[:16] if len(content) > 16 else content)
        if not is_allowed_mime(detected_mime, ALLOWED_DOCUMENT_MIME):
            raise HTTPException(
                status_code=415,
                detail=f"Unsupported file type: {detected_mime}. Allowed types: {', '.join(sorted(ALLOWED_DOCUMENT_MIME))}"
            )

        # Parse date string to datetime
        try:
            if date:
                # Try different date formats
                for date_format in ["%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%Y-%m-%dT%H:%M:%S.%fZ"]:
                    try:
                        parsed_date = datetime.strptime(date, date_format)
                        break
                    except ValueError:
                        continue
                else:
                    # If no format worked, use current date
                    logger.warning(f"Could not parse date '{date}', using current date")
                    parsed_date = datetime.now()
            else:
                parsed_date = datetime.now()
        except Exception as e:
            logger.warning(f"Date parsing error: {str(e)}, using current date")
            parsed_date = datetime.now()

        # Create a unique filename
        file_extension = os.path.splitext(file.filename)[1]
        file_extension = file_extension.lower()
        unique_filename = f"{letterNo}_{uuid4()}{file_extension}"  #Added UUID for uniqueness

        # Local file path (use configured, writable base uploads dir)
        upload_dir = os.path.join(BASE_UPLOAD_DIR, pathStructure)
        try:
            os.makedirs(upload_dir, exist_ok=True)
        except PermissionError as e:
            logger.error(f"No write permission to uploads directory: {upload_dir} ({e})")
            raise HTTPException(
                status_code=500,
                detail="Server misconfigured: uploads directory is not writable. "
                       "Set UPLOADS_DIR to a writable absolute path and fix permissions."
            )
        local_filepath = os.path.join(upload_dir, unique_filename)

        # Replace backslashes with forward slashes
        normalized_path = local_filepath.replace('\\', '/')  # f"{pathStructure1}/{unique_filename}" # file_path

        # Save the file locally
        logger.info(f"Saving file to: {local_filepath}")
        with open(local_filepath, "wb") as buffer:
            buffer.write(content)

        logger.info(f"passing document to metadata.py: {normalized_path}")
        # Background OCR will be scheduled after DB insert to ensure we have the document ID.

        # Upload to S3 (with error handling)
        try:
            s3_filepath = f"documents/{unique_filename}"
            logger.info(f"Uploading to S3: {s3_filepath}")
            s3.upload_file(local_filepath, settings.AWS_BUCKET_NAME, s3_filepath)
            s3_url = f"https://{settings.AWS_BUCKET_NAME}.s3.{settings.AWS_REGION}.amazonaws.com/{s3_filepath}"
            logger.info(f"S3 upload successful: {s3_url}")
        except ClientError as e:
            logger.error(f"S3 upload failed: {str(e)}")
            # Continue without S3 for now, just use local path
            s3_url = local_filepath

        # Validate tags and subtags if provided
        validated_tags = tags or []
        validated_subtags = subTags or []

        # Create the document in the database with chain tracking
        from_final = from_ or from_legacy
        document = Document(
            organization_id=organization_id,
            project_id=project_id,
            filename=file.filename,
            filepath_local=local_filepath,
            filepath_s3=s3_url,
            presigned_url=s3_url,
            filetype=detected_mime,
            filesize=os.stat(local_filepath).st_size,
            uploadType=uploadType,
            letterNo=letterNo,
            date=parsed_date,
            subject=subject or "",
            from_=from_final or "",
            to=to or "",
            tags=validated_tags,
            subTags=validated_subtags,
            status=status or "draft",
            ocrEnabled=ocr_enabled_bool,
            compressionEnabled=compression_enabled_bool,
            createdBy=current_user.id,
            # Chain fields
            previous_letter_id=previous_letter_id,  # Set previous letter ID if provided
            chain_head_id=previous_letter_id or str(uuid4()),  # Set chain head ID
            chain_position=1 if previous_letter_id is None else 2,  # Set position based on previous letter
        )

        logger.info("Inserting document into database")
        result = await db.documents.insert_one(document.model_dump(by_alias=True))
        created_document = await db.documents.find_one({"_id": result.inserted_id})

        if not created_document:
            raise HTTPException(status_code=500, detail="Failed to create document in database")

        # Populate tag and subtag names
        created_document = await populate_tags(created_document, db)

        # Schedule OCR/metadata processing now that we have the document ID
        if ocr_enabled_bool:
            try:
                logger.info(f"Calling OCR in back_ground: {ocr_enabled_bool}")
                background_tasks.add_task(
                    run_ocr_background,
                    pdf_path=local_filepath,  # pass exact stored path to maximize match if id lookup fails
                    pathStructure1=pathStructure1,
                    uploadType=uploadType,
                    document_id=str(result.inserted_id)
                )
                logger.info(f"OCR background task added for document {str(result.inserted_id)}")
            except Exception as e:
                logger.error(f"Failed to setup OCR task: {str(e)}")

        logger.info(f"Document created successfully with ID: {result.inserted_id}")
        return Document(**created_document)


    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in create_document: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


# Register a static export route before dynamic '/documents/{id}' to avoid it being captured as an ID.
@router.get("/documents/export", include_in_schema=False)
async def export_documents_excel_alias(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    tags: Optional[List[str]] = Query(None),
    subTags: Optional[List[str]] = Query(None),
    letterNo: Optional[List[str]] = Query(None),
    date: Optional[datetime] = Query(None),
    subject: Optional[List[str]] = Query(None),
    from_: Optional[List[str]] = Query(None, alias="from"),
    from_legacy: Optional[List[str]] = Query(None, alias="from_"),  # Backward-compat
    to: Optional[List[str]] = Query(None),
    uploadType: Optional[str] = Query(None, regex=r"^(incoming|outgoing)$"),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    return await export_documents_excel(
        organization_id=organization_id,
        project_id=project_id,
        tags=tags,
        subTags=subTags,
        letterNo=letterNo,
        date=date,
        subject=subject,
        from_=from_,
        from_legacy=from_legacy,
        to=to,
        uploadType=uploadType,
        status=status,
        search=search,
        db=db,
        current_user=current_user
    )

@router.get("/documents/{id}", response_model=Document)
async def get_document(
    id: str,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    logger.info(f"Fetching document with ID: {id}")
    try:
        try:
            document_id = id
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid Document ID")
        document = await db.documents.find_one({"_id": document_id})
        if not document:
            logger.warning(f"Document not found: {id}")
            raise HTTPException(status_code=404, detail=f"Document not found: {id}")
    except HTTPException as e:
        raise e
    except Exception as e:
        logger.error(f"Database error: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

    # Generate presigned URL if S3 path is available; otherwise fall back to local path
    s3_url = document.get("filepath_s3")
    presigned_url = document.get("presigned_url") or ""
    try:
        if s3_url and isinstance(s3_url, str) and s3_url.startswith("https://"):
            logger.info(f"Generating presigned URL for: {s3_url}")
            key = s3_url.replace(
                f"https://{settings.AWS_BUCKET_NAME}.s3.{settings.AWS_REGION}.amazonaws.com/",
                ""
            )
            presigned_url = s3.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': settings.AWS_BUCKET_NAME,
                    'Key': key,
                    'ResponseContentDisposition': 'inline',
                    'ResponseContentType': 'application/pdf'
                },
                ExpiresIn=1800
            )
        else:
            # Serve from backend if stored locally
            presigned_url = f"/api/documents/{id}/download"
        logger.info(f"Generated presigned URL for document {id}: {presigned_url}")
    except Exception as e:
        logger.error(f"Failed to generate presigned URL for document {id}: {str(e)}")
        presigned_url = s3_url or document.get("filepath_local", "")

    # Authorization Check (Ensures only allowed users can access)
    authorize_scope(
        current_user,
        organization_id=str(document.get("organization_id")),
        project_id=(str(document.get("project_id")) if document.get("project_id") is not None else None)
    )

    # Populate tag and subtag names
    # document = await populate_tags(document, db)

    # Populate friendly from_/to/project fields and include both alias keys for compatibility
    doc = document

    # Resolve "from" display
    from_display = ""
    base_from = doc.get("from_") or doc.get("from") or ""
    if base_from:
        try:
            party = await db.parties.find_one({"_id": ObjectId(base_from)})
            from_display = party.get("name", base_from) if party else base_from
        except Exception:
            from_display = base_from

    # Resolve "to" display
    to_display = ""
    base_to = doc.get("to") or ""
    if base_to:
        try:
            p2 = await db.parties.find_one({"_id": ObjectId(base_to)})
            to_display = p2.get("name", base_to) if p2 else base_to
        except Exception:
            to_display = base_to

    # Resolve project name
    project_name = "Unknown Project"
    project_id_val = doc.get("project_id")
    project = None
    if project_id_val:
        project = await db.projects.find_one({"_id": project_id_val})
        if not project:
            try:
                project = await db.projects.find_one({"_id": ObjectId(project_id_val)})
            except Exception:
                project = None
        if project:
            project_name = project.get("name", "Unknown Project")

    # Fallback "from_" for outgoing letters using organization name when blank
    if (not from_display) or (str(from_display).strip() == ""):
        ut = str(doc.get("uploadType", "")).lower()
        if ut == "outgoing":
            org_name = None
            org_id = None
            if project and isinstance(project, dict):
                org_id = project.get("organization_id") or project.get("organizationId")
            else:
                proj_tmp = None
                if project_id_val:
                    proj_tmp = await db.projects.find_one({"_id": project_id_val})
                    if not proj_tmp:
                        try:
                            proj_tmp = await db.projects.find_one({"_id": ObjectId(project_id_val)})
                        except Exception:
                            proj_tmp = None
                if proj_tmp:
                    org_id = proj_tmp.get("organization_id") or proj_tmp.get("organizationId")
            if org_id:
                org = await db.organizations.find_one({"_id": org_id})
                if not org:
                    try:
                        org = await db.organizations.find_one({"_id": ObjectId(org_id)})
                    except Exception:
                        org = None
                if org:
                    org_name = org.get("name")
            from_display = org_name or "N/A"

    # Ensure id field exists as string
    if "_id" in doc and "id" not in doc:
        doc["id"] = str(doc["_id"])

    # Return document data with presigned URL and normalized fields
    logger.info(f"Successfully fetched document: {id}")
    return {
        **doc,
        "presigned_url": presigned_url,
        "from_": from_display or doc.get("from_", doc.get("from", "")),
        "from": from_display or doc.get("from_", doc.get("from", "")),
        "to": to_display or doc.get("to", ""),
        "project_name": project_name,
    }

@router.get("/documents/{id}/download")
async def download_document_file(
    id: str,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Stream the original document file when stored locally on the server.
    """
    # Authorization mirrors get_document; fetch first
    # Support both ObjectId and string-based identifiers
    lookup_filter = {"_id": id}
    try:
        lookup_filter = {"_id": ObjectId(id)}
    except Exception:
        # Leave lookup_filter as-is; some legacy docs persist string IDs
        pass

    document = await db.documents.find_one(lookup_filter)
    if not document and lookup_filter.get("_id") != id:
        # Fallback to direct string lookup if ObjectId coercion failed
        document = await db.documents.find_one({"_id": id})
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Optional: basic authorization (same structure as get_document)
    authorize_scope(
        current_user,
        organization_id=str(document.get("organization_id")),
        project_id=(str(document.get("project_id")) if document.get("project_id") is not None else None)
    )

    local_path = document.get("filepath_local")
    if not local_path:
        raise HTTPException(status_code=404, detail="Local file path not available")

    abs_path = os.path.abspath(local_path)
    if not abs_path.startswith(BASE_UPLOAD_DIR):
        logger.warning(f"Attempt to access file outside uploads dir: {abs_path}")
        raise HTTPException(status_code=403, detail="Access denied")

    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="Local file not found")

    return FileResponse(
        abs_path,
        media_type="application/pdf",
        filename=os.path.basename(abs_path),
    )

@router.get("/documents/{id}/enclosures/{enclosure_id}/download")
async def download_enclosure_file(
    id: str,
    enclosure_id: str,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Stream an enclosure file when stored locally on the server.
    """
    document = await db.documents.find_one({"_id": id})
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Authorization similar to get_document
    authorize_scope(
        current_user,
        organization_id=str(document.get("organization_id")),
        project_id=(str(document.get("project_id")) if document.get("project_id") is not None else None)
    )

    enclosures = document.get("enclosures", [])
    enclosure = next((e for e in enclosures if e.get("id") == enclosure_id), None)
    if not enclosure:
        raise HTTPException(status_code=404, detail="Enclosure not found")

    local_path = enclosure.get("filepath_local")
    if not local_path:
        raise HTTPException(status_code=404, detail="Local enclosure file path not available")

    abs_path = os.path.abspath(local_path)
    if not abs_path.startswith(BASE_UPLOAD_DIR):
        logger.warning(f"Attempt to access enclosure outside uploads dir: {abs_path}")
        raise HTTPException(status_code=403, detail="Access denied")

    if not os.path.isfile(abs_path):
        raise HTTPException(status_code=404, detail="Local enclosure file not found")

    # Use a generic media type; browser will handle based on extension if needed
    return FileResponse(
        abs_path,
        media_type=enclosure.get("filetype", "application/octet-stream"),
        filename=os.path.basename(abs_path),
    )

@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    tags: Optional[List[str]] = Query(None),  # Expecting list of tag IDs
    subTags: Optional[List[str]] = Query(None),  # Expecting list of subtag IDs
    letterNo: Optional[List[str]] = Query(None),
    date: Optional[datetime] = Query(None),
    subject: Optional[List[str]] = Query(None),
    from_: Optional[List[str]] = Query(None, alias="from"),
    from_legacy: Optional[List[str]] = Query(None, alias="from_"),
    to: Optional[List[str]] = Query(None),
    uploadType: Optional[str] = Query(None, regex=r"^(incoming|outgoing)$"),
    status: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    try:
        logger.info(f"Listing documents for user {current_user.id}")
        # Compose scope and explicit filters
        filters: List[Dict[str, Any]] = []

        # Scope filter (centralized, aligned with authorize_scope)
        scope_filter = build_scope_query(
            current_user,
            organization_id=organization_id,
            project_id=project_id,
            org_field="organization_id",
            project_field="project_id",
        )
        if scope_filter:
            filters.append(scope_filter)

        # Add filters to the query if they are provided
        if organization_id:
            filters.append({"organization_id": organization_id})
        if project_id:
            filters.append({"project_id": project_id})
        if tags:
            # Convert tag names to ObjectId instances for querying
            tag_ids = []
            for tag in tags:
                try:
                    tag_ids.append(ObjectId(tag))
                except Exception as e:
                    logger.warning(f"Invalid tag ID: {tag}, error: {str(e)}")
                    pass  # if not a valid ObjectId, just skip
            if tag_ids:
                filters.append({"tags": {"$in": tag_ids}})
        if subTags:
            # Convert subtag names to ObjectId instances for querying
            subtag_ids = []
            for subtag in subTags:
                try:
                    subtag_ids.append(ObjectId(subtag))
                except Exception as e:
                    logger.warning(f"Invalid subtag ID: {subtag}, error: {str(e)}")
                    pass
            if subtag_ids:
                filters.append({"subTags": {"$in": subtag_ids}})
        if letterNo:
            try:
                # If API sends single string, ensure list
                if isinstance(letterNo, str):
                    letter_no_list = [letterNo]
                else:
                    letter_no_list = letterNo
                filters.append({"letterNo": {"$in": letter_no_list}})
            except Exception:
                filters.append({"letterNo": {"$in": letterNo}})  # best-effort
        effective_from = from_ or from_legacy
        if effective_from:
            filters.append({"from_": effective_from})
        if to:
            filters.append({"to": to})
        if date:
            filters.append({"date": {"$lte": date}})
        if uploadType:
            filters.append({"uploadType": uploadType})
        if status:
            filters.append({"status": status})

        # Final query
        if not filters:
            final_query: Dict[str, Any] = {}
        elif len(filters) == 1:
            final_query = filters[0]
        else:
            final_query = {"$and": filters}

        logger.info(f"Query: {final_query}")

        # Get total count of documents matching the query (without skip/limit)
        try:
            total_count = await db.documents.count_documents(final_query)
            logger.info(f"Total documents found: {total_count}")
        except Exception as e:
            logger.error(f"Error counting documents: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Database error while counting documents: {str(e)}")

        # Perform the database query with pagination
        try:
            documents = await db.documents.find(final_query).skip(skip).limit(limit).to_list(length=limit)
            logger.info(f"Retrieved {len(documents)} documents")
        except Exception as e:
            logger.error(f"Error fetching documents: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Database error while fetching documents: {str(e)}")

        # Populate tag and subtag names
        populated_documents = []
        for doc in documents:
            try:
                # Convert ObjectId to string for the document ID
                if "_id" in doc:
                    doc["id"] = str(doc["_id"])

                populated_doc = await populate_tags(doc, db)

                # Populate from and to fields - check if they are ObjectIds or plain strings
                from_field = doc.get("from_") or doc.get("from")
                if from_field:
                    try:
                        # Try to find party by ObjectId first
                        from_party = await db.parties.find_one({"_id": ObjectId(from_field)})
                        if from_party:
                            populated_doc["from_"] = from_party.get("name", from_field)
                        else:
                            # If not found as ObjectId, use the string value directly
                            populated_doc["from_"] = from_field
                    except Exception:
                        # If ObjectId conversion fails, use the string value directly
                        populated_doc["from_"] = from_field
                else:
                    populated_doc["from_"] = ""

                to_field = doc.get("to")
                if to_field:
                    try:
                        # Try to find party by ObjectId first
                        to_party = await db.parties.find_one({"_id": ObjectId(to_field)})
                        if to_party:
                            populated_doc["to"] = to_party.get("name", to_field)
                        else:
                            # If not found as ObjectId, use the string value directly
                            populated_doc["to"] = to_field
                    except Exception:
                        # If ObjectId conversion fails, use the string value directly
                        populated_doc["to"] = to_field
                else:
                    populated_doc["to"] = ""

                # Populate project name (support string IDs, UUIDs, or ObjectIds)
                project_id = doc.get("project_id")
                populated_doc["project_name"] = "Unknown Project"
                project = None
                if project_id:
                    # Try direct string _id first
                    project = await db.projects.find_one({"_id": project_id})
                    if not project:
                        # Fallback to ObjectId lookup when applicable
                        try:
                            project = await db.projects.find_one({"_id": ObjectId(project_id)})
                        except Exception:
                            project = None
                    if project:
                        populated_doc["project_name"] = project.get("name", "Unknown Project")

                # Fallback "from_" for outgoing letters: use project organization name if missing
                if (not populated_doc.get("from_")) or (str(populated_doc.get("from_")).strip() == ""):
                    ut = str(doc.get("uploadType", "")).lower()
                    if ut == "outgoing":
                        org_name = None
                        org_id = None
                        # Use already-fetched project if available to get organization_id
                        if project and isinstance(project, dict):
                            org_id = project.get("organization_id") or project.get("organizationId")
                        else:
                            # Fetch project to obtain organization_id
                            proj_tmp = None
                            if project_id:
                                proj_tmp = await db.projects.find_one({"_id": project_id})
                                if not proj_tmp:
                                    try:
                                        proj_tmp = await db.projects.find_one({"_id": ObjectId(project_id)})
                                    except Exception:
                                        proj_tmp = None
                            if proj_tmp:
                                org_id = proj_tmp.get("organization_id") or proj_tmp.get("organizationId")
                        if org_id:
                            # Resolve organization name from organizations collection (supports string/UUID/ObjectId)
                            org = await db.organizations.find_one({"_id": org_id})
                            if not org:
                                try:
                                    org = await db.organizations.find_one({"_id": ObjectId(org_id)})
                                except Exception:
                                    org = None
                            if org:
                                org_name = org.get("name")
                        populated_doc["from_"] = org_name or "N/A"

                populated_documents.append(populated_doc)
            except Exception as e:
                logger.error(f"Error processing document {doc.get('_id', 'unknown')}: {str(e)}")
                # Continue processing other documents
                continue

        # Return documents with total count
        try:
            document_list = []
            for doc in populated_documents:
                try:
                    # Ensure required fields have default values
                    # Build a safe document dict ensuring Pydantic-required fields are present and valid types.
                    # Important: Some documents in DB may store 'Subject' (alias) instead of 'subject' and some fields may be None.
                    # We coalesce None/empty values and include the alias key explicitly for compatibility.
                    metadata_block = (
                        doc.get("metadata")
                        if isinstance(doc.get("metadata"), dict)
                        else {}
                    )

                    def _resolve_datetime(*candidates: Any) -> Optional[datetime]:
                        for candidate in candidates:
                            if candidate in (None, "", 0):
                                continue
                            try:
                                return parse_date_safely(candidate)
                            except Exception:
                                if isinstance(candidate, datetime):
                                    return candidate
                        return None

                    created_at_val = _resolve_datetime(
                        doc.get("createdAt"),
                        doc.get("created_at"),
                        doc.get("uploadedAt"),
                        doc.get("uploaded_at"),
                    ) or datetime.now(timezone.utc)

                    updated_at_val = _resolve_datetime(
                        doc.get("updatedAt"),
                        doc.get("updated_at"),
                        doc.get("modifiedAt"),
                        doc.get("modified_at"),
                    ) or created_at_val

                    date_val = _resolve_datetime(
                        doc.get("date"),
                        doc.get("Date"),
                        metadata_block.get("date") if isinstance(metadata_block, dict) else None,
                    ) or created_at_val

                    doc_data = {
                        **doc,
                        "id": doc.get("id", str(doc.get("_id", ""))),
                        "tags": doc.get("tags") if doc.get("tags") is not None else [],
                        "subTags": doc.get("subTags") if doc.get("subTags") is not None else [],
                        "filename": doc.get("filename", ""),
                        "uploadType": doc.get("uploadType", "incoming"),
                        "letterNo": doc.get("letterNo", ""),
                        # Provide both 'subject' (field name) and 'Subject' (alias) for maximum compatibility
                        "subject": doc.get("subject", doc.get("Subject" or "")),
                        "Subject": doc.get("Subject", doc.get("subject" or "")),
                        # Include both keys to maximize compatibility with consumers/tests
                        "from_": doc.get("from_", doc.get("from", "")),
                        "from": doc.get("from_", doc.get("from", "")),
                        "to": doc.get("to", ""),
                        "status": doc.get("status", "draft"),
                        # Ensure valid datetime (parse robustly and coalesce None/invalid)
                        "date": date_val,
                        "createdAt": created_at_val,
                        "updatedAt": updated_at_val,
                        "organization_id": doc.get("organization_id", ""),
                        "project_id": doc.get("project_id", ""),
                        "project_name": doc.get("project_name","Unknown Project"),
                        "filetype": doc.get("filetype", "application/pdf"),
                        "filesize": doc.get("filesize", 0),
                        "filepath_local": doc.get("filepath_local", ""),
                        "filepath_s3": doc.get("filepath_s3", ""),
                        "ocrEnabled": doc.get("ocrEnabled", False),
                        "compressionEnabled": doc.get("compressionEnabled", False),
                        "createdBy": doc.get("createdBy", ""),
                    }

                    document_obj = Document(**doc_data)
                    document_list.append(document_obj)
                except Exception as e:
                    logger.error(f"Error creating Document object for {doc.get('_id', 'unknown')}: {str(e)}")
                    # Continue processing other documents
                    continue

            logger.info(f"Successfully processed {len(document_list)} documents")
            return DocumentListResponse(documents=document_list, total=total_count)

        except Exception as e:
            logger.error(f"Error creating document list: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Error processing documents: {str(e)}")

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in list_documents: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

# ---------- Export to Excel (XLSX) ----------
def _build_excel_workbook(rows: List[Dict[str, Any]]) -> bytes:
    headers = [
        "Date", "Letter No.", "Direction", "From", "To", "Subject",
        "Tag", "Sub-Tag", "Status", "Project", "Upload Date"
    ]
    wb = Workbook()
    ws = wb.active
    ws.title = "Documents"

    # Header styles
    header_font = Font(bold=True, color="FF000000")
    header_fill = PatternFill("solid", fgColor="FFD9E1F2")  # light blue
    center = Alignment(vertical="center")

    # Write header
    for idx, title in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=idx, value=title)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = center

    # Column widths
    widths = [18, 12, 20, 20, 60, 18, 18, 16, 28, 22]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[chr(64 + i)].width = w

    # Data rows
    r = 2
    for row in rows:
        ws.cell(r, 1, row.get("date") or "")
        ws.cell(r, 2, row.get("letterNo") or "")
        ws.cell(r, 3, row.get("direction") or "")
        ws.cell(r, 4, row.get("from_") or "")
        ws.cell(r, 5, row.get("to") or "")
        ws.cell(r, 6, row.get("subject") or "")
        ws.cell(r, 7, row.get("tag") or "")
        ws.cell(r, 8, row.get("subTag") or "")
        ws.cell(r, 9, row.get("status") or "")
        ws.cell(r, 10, row.get("project") or "")
        ws.cell(r, 11, row.get("uploadDate") or "")
        r += 1

    stream = BytesIO()
    wb.save(stream)
    return stream.getvalue()


@router.get("/documents/export")
async def export_documents_excel(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    tags: Optional[List[str]] = Query(None),
    subTags: Optional[List[str]] = Query(None),
    letterNo: Optional[List[str]] = Query(None),
    date: Optional[datetime] = Query(None),
    subject: Optional[List[str]] = Query(None),
    from_: Optional[List[str]] = Query(None, alias="from"),
    from_legacy: Optional[List[str]] = Query(None, alias="from_"),
    to: Optional[List[str]] = Query(None),
    uploadType: Optional[str] = Query(None, regex=r"^(incoming|outgoing)$"),
    status: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Export filtered documents to Excel (.xlsx). Applies same authorization and filters
    as list_documents plus a simple 'search' across filename/subject/letterNo.
    """
    try:
        # Authorization and filters (centralized scope + explicit filters)
        filters: List[Dict[str, Any]] = []

        scope_filter = build_scope_query(
            current_user,
            organization_id=organization_id,
            project_id=project_id,
            org_field="organization_id",
            project_field="project_id"
        )
        if scope_filter:
            filters.append(scope_filter)

        # Apply provided filters (mirror list_documents best-effort)
        if organization_id:
            filters.append({"organization_id": organization_id})
        if project_id:
            filters.append({"project_id": project_id})
        if tags:
            tag_ids = []
            for tag in tags:
                try:
                    tag_ids.append(ObjectId(tag))
                except Exception:
                    # ignore invalid ids
                    pass
            if tag_ids:
                filters.append({"tags": {"$in": tag_ids}})
        if subTags:
            subtag_ids = []
            for sub in subTags:
                try:
                    subtag_ids.append(ObjectId(sub))
                except Exception:
                    pass
            if subtag_ids:
                filters.append({"subTags": {"$in": subtag_ids}})
        if letterNo:
            if isinstance(letterNo, str):
                filters.append({"letterNo": {"$in": [letterNo]}})
            else:
                filters.append({"letterNo": {"$in": letterNo}})
        effective_from = from_ or from_legacy
        if effective_from:
            filters.append({"from_": effective_from})
        if to:
            filters.append({"to": to})
        if date:
            filters.append({"date": {"$lte": date}})
        if uploadType:
            filters.append({"uploadType": uploadType})
        if status:
            filters.append({"status": status})

        # Simple search over filename/subject/letterNo
        if search:
            search = str(search).strip()
            if search:
                filters.append({
                    "$or": [
                        {"filename": {"$regex": search, "$options": "i"}},
                        {"subject": {"$regex": search, "$options": "i"}},
                        {"letterNo": {"$regex": search, "$options": "i"}},
                    ]
                })

        # Final query
        if not filters:
            final_query = {}
        elif len(filters) == 1:
            final_query = filters[0]
        else:
            final_query = {"$and": filters}

        # Fetch all matching docs
        docs = await db.documents.find(final_query).sort("createdAt", -1).to_list(length=None)

        rows: List[Dict[str, Any]] = []
        for doc in docs:
            try:
                # Populate tag/subtag names
                doc = await populate_tags(doc, db)

                # Resolve From/To names (reuse logic best-effort)
                from_display = ""
                base_from = doc.get("from_") or doc.get("from") or ""
                if base_from:
                    try:
                        party = await db.parties.find_one({"_id": ObjectId(base_from)})
                        from_display = party.get("name", base_from) if party else base_from
                    except Exception:
                        from_display = base_from

                to_display = ""
                base_to = doc.get("to") or ""
                if base_to:
                    try:
                        p2 = await db.parties.find_one({"_id": ObjectId(base_to)})
                        to_display = p2.get("name", base_to) if p2 else base_to
                    except Exception:
                        to_display = base_to

                # Resolve project name
                project_name = "Unknown Project"
                project_id_val = doc.get("project_id")
                if project_id_val:
                    proj = await db.projects.find_one({"_id": project_id_val})
                    if not proj:
                        try:
                            proj = await db.projects.find_one({"_id": ObjectId(project_id_val)})
                        except Exception:
                            proj = None
                    if proj:
                        project_name = proj.get("name", "Unknown Project")

                # Tags/Subtags (first or join)
                tag_name = ""
                if isinstance(doc.get("tags"), list) and doc["tags"]:
                    # tags are names after populate_tags if IDs were valid
                    tag_name = str(doc["tags"][0])

                subtag_name = ""
                if isinstance(doc.get("subTags"), list) and doc["subTags"]:
                    subtag_name = str(doc["subTags"][0])

                # Direction
                direction = "Incoming" if str(doc.get("uploadType", "")).lower() == "incoming" else "Outgoing"

                # Upload date string
                created_at = doc.get("createdAt")
                if isinstance(created_at, datetime):
                    upload_date = created_at.strftime("%Y-%m-%d %H:%M")
                else:
                    upload_date = str(created_at or "")

                rows.append({
                    "letterNo": doc.get("letterNo", ""),
                    "direction": direction,
                    "from_": from_display or "N/A",
                    "to": to_display or "",
                    "subject": doc.get("subject", doc.get("Subject", "")) or "",
                    "tag": tag_name,
                    "subTag": subtag_name,
                    "status": doc.get("status", "draft"),
                    "project": project_name,
                    "uploadDate": upload_date,
                })
            except Exception:
                # Skip problematic docs but continue
                continue

        xlsx_bytes = _build_excel_workbook(rows)
        headers = {
            "Content-Disposition": 'attachment; filename="documents_export.xlsx"'
        }
        return StreamingResponse(
            BytesIO(xlsx_bytes),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers=headers
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in export_documents_excel: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.put("/documents/{id}", response_model=Document)
async def update_document(
    id: str,
    document_update: DocumentUpdate,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    # Fetch the existing document
    try:
        document_id = id
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Document ID")

    existing_document = await db.documents.find_one({"_id": id})
    if not existing_document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Check authorization centrally
    authorize_scope(
        current_user,
        organization_id=str(existing_document.get("organization_id")),
        project_id=(str(existing_document.get("project_id")) if existing_document.get("project_id") is not None else None)
    )

    # Validate tag and subtag IDs, if provided
    validated_tags = []
    validated_subtags = []

    if document_update.tags:
        for tag_id in document_update.tags:
            try:
                ObjectId(tag_id)
            except (InvalidId, TypeError):
                raise HTTPException(status_code=400, detail=f"Invalid Tag ID: {tag_id}")

            tag = await db.tags.find_one({"_id": ObjectId(tag_id)})
            if not tag:
                raise HTTPException(status_code=400, detail=f"Tag not found: {tag_id}")
            validated_tags.append(tag_id)

    if document_update.subTags:
        for subtag_id in document_update.subTags:
            try:
                ObjectId(subtag_id)
            except (InvalidId, TypeError):
                raise HTTPException(status_code=400, detail=f"Invalid Subtag ID: {subtag_id}")

            subtag = await db.subtags.find_one({"_id": ObjectId(subtag_id)})
            if not subtag:
                raise HTTPException(status_code=400, detail=f"Subtag not found: {subtag_id}")
            validated_subtags.append(subtag_id)

    # Update only the allowed fields.  Do NOT allow updating file-related fields.
    update_data = document_update.model_dump(exclude_unset=True, by_alias=True)  # Exclude fields that weren't provided

    # Prevent updating file-related fields
    exclude_fields = {
      'id', 'filename', 'filepath_local', 'filepath_s3', 'filetype', 'filesize', 'createdBy', 'createdAt'
    }
    for field in exclude_fields:
        if field in update_data:
            del update_data[field]

    # Update tags and subtags if provided
    if validated_tags:
        update_data["tags"] = validated_tags
    if validated_subtags:
        update_data["subTags"] = validated_subtags

    # Ensure all editable metadata fields are accepted (only apply non-None values)
    for field in ["uploadType", "subject", "from_", "to", "status", "letterNo", "date"]:
        value = getattr(document_update, field, None)
        if value is not None:
            update_data[field] = value

    # Map `from_` to `from` for database compatibility
    if "from" in update_data:
        update_data["from_"] = update_data.pop("from")

    update_data['updatedAt'] = datetime.now() # Always update updatedAt

    updated_document = await db.documents.find_one_and_update(
         {"_id": document_id},
        { "$set": update_data },
        return_document=ReturnDocument.AFTER
    )

    if not updated_document:
        raise HTTPException(status_code=404, detail="Document not found") # Should not happen, but good practice

    # Populate tag and subtag names
    updated_document = await populate_tags(updated_document, db)

    return Document(**updated_document)

@router.delete("/documents/{id}/references/{reference_id}", response_model=Document)
async def delete_reference(
    id: str,
    reference_id: str,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    # Fetch the existing document
    document = await db.documents.find_one({"_id": id})
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Check authorization centrally
    authorize_scope(
        current_user,
        organization_id=str(document.get("organization_id")),
        project_id=(str(document.get("project_id")) if document.get("project_id") is not None else None)
    )

    # Find the reference to delete
    reference_to_delete = None
    for ref in document.get('references', []):
        if ref['documentId'] == reference_id:
            reference_to_delete = ref
            break

    if not reference_to_delete:
        raise HTTPException(status_code=404, detail="Reference not found")

    # Remove the reference from the document's references array
    updated_document = await db.documents.find_one_and_update(
        {"_id": id},
        {"$pull": {"references": reference_to_delete}},
        return_document=ReturnDocument.AFTER
    )

    # Remove the corresponding reference from the referencedBy array of the referenced document
    await db.documents.update_one(
        {"_id": reference_id},
        {"$pull": {"referencedBy": {"documentId": id}}}
    )

    if not updated_document:
        raise HTTPException(status_code=404, detail="Document not found") # Shouldn't happen

    return Document(**updated_document)

@router.delete("/documents/{id}/enclosures/{enclosure_id}", response_model=Document)
async def delete_enclosure(
    id: str,
    enclosure_id: str,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    # Fetch the existing document
    document = await db.documents.find_one({"_id": id})
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Check authorization centrally
    authorize_scope(
        current_user,
        organization_id=str(document.get("organization_id")),
        project_id=(str(document.get("project_id")) if document.get("project_id") is not None else None)
    )

    # Find the enclosure to delete
    enclosure_to_delete = None
    for enc in document.get('enclosures', []):
        if enc.get('id') == enclosure_id:
            enclosure_to_delete = enc
            break

    if not enclosure_to_delete:
         raise HTTPException(status_code=404, detail="Enclosure not found")

    # Remove the enclosure reference from the document's enclosures array
    updated_document = await db.documents.find_one_and_update(
        {"_id": id},
        {"$pull": {"enclosures": enclosure_to_delete}},
        return_document=ReturnDocument.AFTER
    )

    if not updated_document:
        raise HTTPException(status_code=404, detail="Document not found")  # Shouldn't happen

    return Document(**updated_document)

@router.get("/documents/{id}/enclosures", response_model=List[EnclosureResponse])
async def get_enclosures(
    id: str,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    try:
        logger.info(f"Fetching enclosures for document {id}")

        # Fetch the document
        document = await db.documents.find_one({"_id": id})
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")

        # Check authorization (same logic as get_document)
        authorize_scope(
            current_user,
            organization_id=str(document.get("organization_id")),
            project_id=(str(document.get("project_id")) if document.get("project_id") is not None else None)
        )

        # Get enclosures and format them properly
        enclosures = document.get("enclosures", [])
        formatted_enclosures = []

        for enc in enclosures:
            # Generate fresh presigned URL if needed
            presigned_url = enc.get("presigned_url", enc.get("filepath_s3", ""))
            if enc.get("filepath_s3", "").startswith("https://"):
                try:
                    # Extract key from S3 URL
                    key = enc["filepath_s3"].replace(f"https://{settings.AWS_BUCKET_NAME}.s3.{settings.AWS_REGION}.amazonaws.com/", "")
                    presigned_url = s3.generate_presigned_url(
                        'get_object',
                        Params={'Bucket': settings.AWS_BUCKET_NAME, 'Key': key},
                        ExpiresIn=1800  # 30 minutes
                    )
                except Exception as e:
                    logger.error(f"Failed to generate presigned URL for enclosure: {str(e)}")
                    presigned_url = enc.get("filepath_s3", "")
            # If not S3 URL, expose backend download endpoint for local files
            if not isinstance(presigned_url, str) or not presigned_url.startswith("https://"):
                presigned_url = f"/api/documents/{id}/enclosures/{enc.get('id')}/download"

            # Get uploader username from user ID
            uploader_name = "Unknown"
            uploader_id = enc.get("uploadedBy")
            if uploader_id:
                try:
                    # Try to find user by ID
                    user = await db.users.find_one({"_id": uploader_id})
                    if user:
                        uploader_name = user.get("username", uploader_id)
                    else:
                        # If user not found, use the ID itself
                        uploader_name = str(uploader_id)
                except Exception as e:
                    logger.error(f"Failed to fetch user info for {uploader_id}: {str(e)}")
                    uploader_name = str(uploader_id) if uploader_id else "Unknown"

            # Debug logging to see what data we have
            logger.info(f"Enclosure data: id={enc.get('id')}, filename={enc.get('filename')}, uploadedBy={enc.get('uploadedBy')}")

            formatted_enclosures.append(EnclosureResponse(
                id=enc.get("id", str(uuid.uuid4())),
                filename=enc.get("filename", "Unknown"),
                presigned_url=presigned_url,
                filetype=enc.get("filetype", "application/octet-stream"),
                filesize=enc.get("filesize", 0),
                uploadedAt=enc.get("uploadedAt", datetime.now().isoformat()),
                uploadedBy=uploader_name
            ))

        logger.info(f"Found {len(formatted_enclosures)} enclosures for document {id}")
        return formatted_enclosures

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in get_enclosures: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")

@router.get("/documents/{id}/references", response_model=List[DocumentReference])
async def get_references(
    id: str,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    # Fetch the document
    document = await db.documents.find_one({"_id": id})
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Check authorization (same logic as get_document)
    authorize_scope(
        current_user,
        organization_id=str(document.get("organization_id")),
        project_id=(str(document.get("project_id")) if document.get("project_id") is not None else None)
    )

    # Return the references array
    return [DocumentReference(**ref) for ref in document.get("references", [])]

@router.post("/documents/{id}/references", response_model=Document)
async def add_reference(
    id: str,
    reference_create: ReferenceCreate,
    #referenced_document_id: str = Form(...),
    #link_type: str = Form(..., regex=r"^(direct|indirect)$"),
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    # Fetch the existing document
    document = await db.documents.find_one({"_id": id})
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Check authorization (same logic as get_document)
    authorize_scope(
        current_user,
        organization_id=str(document.get("organization_id")),
        project_id=(str(document.get("project_id")) if document.get("project_id") is not None else None)
    )

    # Validate referenced document exists
    referenced_document = await db.documents.find_one({"_id": reference_create.referenced_document_id})
    if not referenced_document:
        raise HTTPException(status_code=404, detail="Referenced document not found")

    # Create a DocumentReference
    reference_ref = DocumentReference(documentId=reference_create.referenced_document_id, linkType=reference_create.link_type)

    # Add the reference to the document's references array
    updated_document = await db.documents.find_one_and_update(
        {"_id": id},
        {"$push": {"references": reference_ref.model_dump()}},
        return_document=ReturnDocument.AFTER
    )

    # Add a corresponding reference to the referencedBy array of the referenced document
    referenced_by_ref = DocumentReference(documentId=id, linkType=reference_create.link_type)
    await db.documents.update_one(
        {"_id": reference_create.referenced_document_id},
        {"$push": {"referencedBy": referenced_by_ref.model_dump()}}
    )

    if not updated_document:
        raise HTTPException(status_code=404, detail="Document not found") # Shouldn't happen

    return Document(**updated_document)

@router.post("/documents/link", response_model=LinkDocumentsResponse)
async def link_documents(
    link_request: LinkDocumentsRequest,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    # --- 1. Validate and Convert Document IDs ---
    try:
        doc_a_id = link_request.document_a_id  # New letter (e.g. Kanpur-LET-JVTI-CPM-01500-E01)
        doc_b_id = link_request.document_b_id  # Target letter in the chain (e.g. KPIL-GULERMAK-JV/UI/MUK1/1356)
        doc_a_object_id = ObjectId(doc_a_id)
        doc_b_object_id = ObjectId(doc_b_id)
    except Exception as e:
        logger.error(f"Error converting document IDs: {str(e)}")
        raise HTTPException(status_code=400, detail="Invalid document IDs provided.")

    # --- 2. Fetch Documents ---
    doc_a = await db.documents.find_one({"_id": doc_a_object_id})
    doc_b = await db.documents.find_one({"_id": doc_b_object_id})
    if not doc_a or not doc_b:
        raise HTTPException(status_code=404, detail="One or both documents not found.")

    # --- 3. Authorization Check (adapt your existing logic as needed) ---
    authorize_scope(
        current_user,
        organization_id=str(doc_a.get("organization_id")),
        project_id=(str(doc_a.get("project_id")) if doc_a.get("project_id") is not None else None)
    )
    authorize_scope(
        current_user,
        organization_id=str(doc_b.get("organization_id")),
        project_id=(str(doc_b.get("project_id")) if doc_b.get("project_id") is not None else None)
    )

    # --- 4. Create Links (Direct and Transitive) ---
    linked_docs = []  # To record which document IDs get linked

    async def create_link(source_id: ObjectId, target_id: ObjectId, link_type_val: str):
        """Helper function to create a link if one does not already exist."""
        # Check if a link from source to target already exists
        existing = await db.documents.find_one({
            "_id": source_id,
            "references.documentId": str(target_id)
        })
        if existing:
            return  # Avoid duplicate link
        new_link = DocumentReference(documentId=str(target_id), linkType=link_type_val)
        await db.documents.update_one(
            {"_id": source_id},
            {"$push": {"references": new_link.model_dump()}}
        )
        linked_docs.append(str(target_id))
        logger.info(f"Created {link_type_val} link from {source_id} to {target_id}.")

    # --- 5. Direct Link ---
    # Create a direct link from the new document (doc_a) to the target document (doc_b)
    await create_link(doc_a_object_id, doc_b_object_id, "direct")

    # --- 6. Transitive Linking ---
    # Retrieve all documents that the target document (doc_b) is linked to.
    # We assume that doc_b stores its references in a field "references"
    doc_b_references = doc_b.get("references", [])
    for ref in doc_b_references:
        target_doc_id = ref.get("documentId")
        if target_doc_id:
            # Create an indirect link from the new document to each document that doc_b references.
            # This covers the case: e.g. when linking letter 3 to letter 2,
            # if letter 2 is linked to letter 1 then letter 3 gets an indirect link to letter 1.
            await create_link(doc_a_object_id, ObjectId(target_doc_id), "indirect")

    # (Optional) For further transitivity, you might consider recursively propagating links.
    # Be cautious to avoid infinite loops; you may need to track visited IDs.

    return {"message": "Documents linked successfully", "linked_documents": linked_docs}

# New endpoint for retrieving linked documents
@router.get("/documents/{document_id}/linked", response_model=List[DocumentReference])
async def get_linked_documents(
    document_id: str,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    # --- 1. Validate Document ID ---
    try:
        doc_id = document_id
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid document ID provided.")

    document = await db.documents.find_one({"_id": doc_id})

    if not document:
        raise HTTPException(status_code=404, detail="Document not found.")

    # --- 2. Authorization Check (centralized) ---
    authorize_scope(
        current_user,
        organization_id=str(document.get("organization_id")),
        project_id=(str(document.get("project_id")) if document.get("project_id") is not None else None)
    )

    # --- 3. Retrieve and Return Linked Documents ---
    # The 'references' field already contains the linked documents.
    return [DocumentReference(**ref) for ref in document.get("references", [])]

@router.delete("/documents/{id}", status_code=204)
async def delete_document(
    id: str,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Delete a document. Authorization mirrors get_document/update_document.
    """
    # Fetch the existing document
    document = await db.documents.find_one({"_id": id})
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Authorization check
    authorize_scope(
        current_user,
        organization_id=str(document.get("organization_id")),
        project_id=(str(document.get("project_id")) if document.get("project_id") is not None else None)
    )

    # Remove all references to this document from other documents
    await db.documents.update_many(
        {"references.documentId": id},
        {"$pull": {"references": {"documentId": id}}}
    )

    # Remove all references from this document to other documents
    if document.get("references"):
        for reference in document["references"]:
            await db.documents.update_one(
                {"_id": reference["documentId"]},
                {"$pull": {"referencedBy": {"documentId": id}}}
            )

    await db.documents.delete_one({"_id": id})
    return


@router.post("/documents/{id}/enclosures", response_model=EnclosureResponse)

async def add_enclosure(
    id: str,
    file: UploadFile = File(...),
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    try:
        logger.info(f"Adding enclosure to document {id}: {file.filename}")

        # Fetch the existing document
        document = await db.documents.find_one({"_id": id})
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")

        # Check authorization centrally
        authorize_scope(
            current_user,
            organization_id=str(document.get("organization_id")),
            project_id=(str(document.get("project_id")) if document.get("project_id") is not None else None)
        )

        # Validate file
        if not file.filename:
            raise HTTPException(status_code=400, detail="No file provided")

        # Read file once and validate MIME type for enclosures (PDF/PNG/JPEG)
        content = await file.read()
        detected_mime = sniff_mime_from_bytes(content[:16] if len(content) > 16 else content)
        if not is_allowed_mime(detected_mime, ALLOWED_ENCLOSURE_MIME):
            raise HTTPException(
                status_code=415,
                detail=f"Unsupported file type: {detected_mime}. Allowed types: {', '.join(sorted(ALLOWED_ENCLOSURE_MIME))}"
            )

        # Create a unique filename for the enclosure
        file_extension = os.path.splitext(file.filename)[1]
        unique_filename = f"{uuid.uuid4()}{file_extension}"
        enclosure_id = str(uuid.uuid4())

        # Local file path for the enclosure
        enclosures_dir = os.path.join(BASE_UPLOAD_DIR, "enclosures")
        try:
            os.makedirs(enclosures_dir, exist_ok=True)
        except PermissionError as e:
            logger.error(f"No write permission to enclosures directory: {enclosures_dir} ({e})")
            raise HTTPException(
                status_code=500,
                detail="Server misconfigured: uploads/enclosures directory is not writable. "
                       "Set UPLOADS_DIR to a writable absolute path and fix permissions."
            )
        local_filepath = os.path.join(enclosures_dir, unique_filename)

        # Save the enclosure file locally
        logger.info(f"Saving enclosure to: {local_filepath}")
        with open(local_filepath, "wb") as buffer:
            buffer.write(content)

        # Upload the enclosure to S3 with error handling
        s3_url = local_filepath  # Fallback to local path
        try:
            s3_filepath = f"documents/enclosures/{unique_filename}"
            logger.info(f"Uploading enclosure to S3: {s3_filepath}")
            s3.upload_file(local_filepath, settings.AWS_BUCKET_NAME, s3_filepath)
            s3_url = f"https://{settings.AWS_BUCKET_NAME}.s3.{settings.AWS_REGION}.amazonaws.com/{s3_filepath}"
            logger.info(f"S3 upload successful: {s3_url}")
        except ClientError as e:
            logger.error(f"S3 upload failed: {str(e)}")
            # Continue with local path

        # Generate presigned URL for the enclosure
        presigned_url = s3_url
        if s3_url.startswith("https://"):
            try:
                # Extract key from S3 URL
                key = s3_url.replace(f"https://{settings.AWS_BUCKET_NAME}.s3.{settings.AWS_REGION}.amazonaws.com/", "")
                presigned_url = s3.generate_presigned_url(
                    'get_object',
                    Params={'Bucket': settings.AWS_BUCKET_NAME, 'Key': key},
                    ExpiresIn=1800  # 30 minutes
                )
            except Exception as e:
                logger.error(f"Failed to generate presigned URL: {str(e)}")
                presigned_url = s3_url

        # Create enclosure data structure
        enclosure_data = {
            "id": enclosure_id,
            "filename": file.filename,
            "filepath_local": local_filepath,
            "filepath_s3": s3_url,
            "presigned_url": presigned_url,
            "filetype": detected_mime,
            "filesize": os.stat(local_filepath).st_size,
            "uploadedAt": datetime.now().isoformat(),
            "uploadedBy": current_user.id,
        }

        # Add the enclosure to the document's enclosures array
        updated_document = await db.documents.find_one_and_update(
            {"_id": id},
            {"$push": {"enclosures": enclosure_data}},
            return_document=ReturnDocument.AFTER
        )

        if not updated_document:
            raise HTTPException(status_code=404, detail="Document not found")

        logger.info(f"Enclosure added successfully: {enclosure_id}")

        # Get current user's username for the response
        uploader_name = current_user.username if hasattr(current_user, 'username') else current_user.id

        # Return the enclosure response
        return EnclosureResponse(
            id=enclosure_id,
            filename=file.filename,
            presigned_url=presigned_url,
            filetype=detected_mime,
            filesize=os.stat(local_filepath).st_size,
            uploadedAt=datetime.now().isoformat(),
            uploadedBy=uploader_name
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in add_enclosure: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}")


@router.post("/documents/{id}/request-draft", response_model=Document)
async def request_draft_for_document(
    id: str,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Server-side guard: Reserve a draft request for this document.
    Sets status to 'Under Process' only if not already under process.
    Returns 409 Conflict if a draft is already in progress.
    """
    # Fetch the existing document
    document = await db.documents.find_one({"_id": id})
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Authorization check (same as get/update)
    authorize_scope(
        current_user,
        organization_id=str(document.get("organization_id")),
        project_id=(str(document.get("project_id")) if document.get("project_id") is not None else None)
    )

    current_status = str(document.get("status") or "").strip()
    if current_status.lower() == "under process".lower():
        # Conflict guard
        raise HTTPException(status_code=409, detail="Draft already in progress for this document")

    target_filter = {"_id": document["_id"]}
    updated_document = await db.documents.find_one_and_update(
        target_filter,
        {"$set": {"status": "Under Process", "updatedAt": datetime.now()}},
        return_document=ReturnDocument.AFTER
    )
    if not updated_document:
        raise HTTPException(status_code=404, detail="Document not found")
    # Populate tag/subtag names for parity with other responses
    updated_document = await populate_tags(updated_document, db)
    return Document(**updated_document)


@router.post("/documents/{id}/complete-draft", response_model=Document)
async def complete_draft_for_document(
    id: str,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Mark the document as replied after the associated draft letter is completed.
    Sets status to 'Replied'.
    """
    # Fetch the existing document
    lookup_filter = {"_id": id}
    try:
        lookup_filter = {"_id": ObjectId(id)}
    except Exception:
        pass

    document = await db.documents.find_one(lookup_filter)
    if not document and lookup_filter.get("_id") != id:
        document = await db.documents.find_one({"_id": id})
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")

    # Authorization check (same as get/update)
    authorize_scope(
        current_user,
        organization_id=str(document.get("organization_id")),
        project_id=(str(document.get("project_id")) if document.get("project_id") is not None else None)
    )

    target_filter = {"_id": document["_id"]}
    updated_document = await db.documents.find_one_and_update(
        target_filter,
        {"$set": {"status": "Replied", "updatedAt": datetime.now()}},
        return_document=ReturnDocument.AFTER
    )
    if not updated_document:
        raise HTTPException(status_code=404, detail="Document not found")
    updated_document = await populate_tags(updated_document, db)
    return Document(**updated_document)
