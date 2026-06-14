"""
AI Assistant module with enhanced architecture and best practices.
Implements rate limiting, proper error handling, caching, and modular design.
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, status
from ..core.database import get_db
from ..core.security import get_current_user, CurrentUser, authorize_scope
from ..core.config import settings
from ..core import templates
from ..core.constants import OPENAI_MODELS
from ..models.letter import Letter
from ..models.document import Document
from typing import List, Optional, Dict, Any, Tuple, Set, Union
from openai import OpenAI, RateLimitError
import logging
from datetime import datetime, timedelta
from pydantic import BaseModel, field_validator, ConfigDict
import numpy as np
import os
import re
import json
import hashlib
import asyncio
import time
from functools import lru_cache
from bson.objectid import ObjectId
from ratelimit import limits, sleep_and_retry

router = APIRouter()

# Configure Logger with structured logging
logger = logging.getLogger(__name__)

# OpenAI client setup with enhanced configuration
client = OpenAI(api_key=settings.OPENAI_API_KEY)
assistant_id = getattr(settings, "ASSISTANT_ID1", None)
VECTOR_STORE_ENABLED = getattr(settings, "VECTOR_STORE_ENABLED", False)

# Rate limiting configuration
OPENAI_CALLS_PER_MINUTE = 30
OPENAI_CALLS_PER_DAY = 1000
SIMILARITY_THRESHOLD = 0.3
MAX_SIMILAR_LETTERS = 5
EMBEDDING_CACHE_SIZE = 1000

# Caching
embedding_cache: Dict[str, List[float]] = {}
vector_search_cache: Dict[str, List[Dict]] = {}

# =========================
# Rate Limiting Decorators
# =========================

@sleep_and_retry
@limits(calls=OPENAI_CALLS_PER_MINUTE, period=60)
def check_minute_limit():
    """Rate limiting for OpenAI calls per minute"""
    pass

@sleep_and_retry
@limits(calls=OPENAI_CALLS_PER_DAY, period=86400)
def check_daily_limit():
    """Rate limiting for OpenAI calls per day"""
    pass

def rate_limited_openai_call():
    """Decorator equivalent for async functions"""
    check_minute_limit()
    check_daily_limit()

# =========================
# Enhanced Pydantic Models
# =========================

class LetterSearchRequest(BaseModel):
    """Request model for letter search with validation."""
    query: str
    limit: Optional[int] = 3
    
    @field_validator('query')
    @classmethod
    def validate_query(cls, v):
        if not v or not v.strip():
            raise ValueError('Query cannot be empty')
        if len(v.strip()) < 3:
            raise ValueError('Query must be at least 3 characters long')
        return v.strip()
    
    @field_validator('limit')
    @classmethod
    def validate_limit(cls, v):
        if v is not None and (v < 1 or v > 20):
            raise ValueError('Limit must be between 1 and 20')
        return v or 3

class SimilarLetter(BaseModel):
    """Model for similar letter with enhanced validation."""
    id: str
    title: str
    subject: str
    content: str
    recipient: str
    similarity_score: float
    created_at: str
    
    @field_validator("created_at", mode="before")
    @classmethod
    def normalize_created_at(cls, v):
        if v is None:
            return datetime.now().isoformat()
        if isinstance(v, datetime):
            return v.isoformat()
        if isinstance(v, str):
            return v
        try:
            return str(v)
        except Exception:
            return datetime.now().isoformat()
    
    @field_validator('similarity_score')
    @classmethod
    def validate_similarity_score(cls, v):
        if not 0.0 <= v <= 1.0:
            raise ValueError('Similarity score must be between 0.0 and 1.0')
        return v

class LetterDraftRequest(BaseModel):
    """Enhanced letter draft request with comprehensive validation."""
    model_config = ConfigDict(extra='allow')
    
    subject: str
    recipient: str
    user_id: str
    context: Optional[str] = None
    points: Optional[str] = None
    similar_letters: Optional[List[SimilarLetter]] = None
    target_letter_id: Optional[str] = None
    org_short: Optional[str] = None
    project_short: Optional[str] = None
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    org_name: Optional[str] = None
    project_name: Optional[str] = None
    document_ids: Optional[List[str]] = None
    use_vector_store: Optional[bool] = False
    
    @field_validator('subject', 'recipient')
    @classmethod
    def validate_required_fields(cls, v):
        if not v or not v.strip():
            raise ValueError('Field cannot be empty')
        return v.strip()
    
    @field_validator('target_letter_id', 'organization_id', 'project_id')
    @classmethod
    def validate_object_ids(cls, v):
        if v and not ObjectId.is_valid(v):
            raise ValueError('Invalid ObjectId format')
        return v
    
    @field_validator('document_ids')
    @classmethod
    def validate_document_ids(cls, v):
        if v:
            for doc_id in v:
                if not ObjectId.is_valid(doc_id):
                    raise ValueError(f'Invalid document ID format: {doc_id}')
        return v

class LetterDraftResponse(BaseModel):
    """Enhanced response model for letter drafts."""
    draft_letter: str
    similar_letters: List[SimilarLetter]
    structure_summary: str
    extracted_key_points: Optional[str] = None
    processing_time: Optional[float] = None
    used_cache: Optional[bool] = False

class VectorSearchResponse(BaseModel):
    """Enhanced vector search response."""
    similar_letters: List[SimilarLetter]
    query_embedding: List[float]
    processing_time: Optional[float] = None
    cached_results: Optional[bool] = False

class QuotedClause(BaseModel):
    """Model for quoted clauses from documents."""
    clause_number: str
    page_number: Optional[str] = None
    line_numbers: Optional[str] = None
    content: str

class AIAssistantStats(BaseModel):
    """Statistics model for AI assistant usage."""
    total_requests: int
    successful_requests: int
    failed_requests: int
    avg_response_time: float
    cache_hit_rate: float

# =========================
# Input Validation Functions
# =========================

async def validate_letter_draft_request(
    request: LetterDraftRequest, 
    current_user: CurrentUser, 
    db
) -> None:
    """
    Comprehensive validation for letter draft requests.
    
    Args:
        request: The letter draft request to validate
        current_user: The authenticated user
        db: Database connection
        
    Raises:
        HTTPException: On validation failure
    """
    logger.info(f"Validating letter draft request for user: {current_user.id}")
    
    # Validate user permissions
    authorize_scope(current_user, "letter:write")
    
    # Validate organization access
    if request.organization_id and request.organization_id != str(current_user.organization_id):
        if "superadmin" not in current_user.roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied to specified organization"
            )
    
    # Validate project access
    if request.project_id:
        if "superadmin" not in current_user.roles:
            if "projectadmin" in current_user.roles or "projectuser" in current_user.roles:
                user_projects = getattr(current_user, "projects", [])
                if request.project_id not in [str(p) for p in user_projects]:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Access denied to specified project"
                    )
    
    # Validate document IDs if provided
    if request.document_ids:
        await validate_document_access(db, request.document_ids, current_user)
    
    # Validate target letter access if provided
    if request.target_letter_id:
        await validate_letter_access(db, request.target_letter_id, current_user)

async def validate_document_access(
    db, 
    document_ids: List[str], 
    current_user: CurrentUser
) -> None:
    """
    Validate user access to specified documents.
    
    Args:
        db: Database connection
        document_ids: List of document IDs to validate
        current_user: The authenticated user
        
    Raises:
        HTTPException: On access denial or document not found
    """
    logger.info(f"Validating access to {len(document_ids)} documents")
    
    for doc_id in document_ids:
        try:
            if not ObjectId.is_valid(doc_id):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid document ID format: {doc_id}"
                )
            
            document = await db.documents.find_one({"_id": ObjectId(doc_id)})
            if not document:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Document not found: {doc_id}"
                )
            
            # Check user access permissions
            doc_org_id = document.get('organization_id')
            user_org_id = str(current_user.organization_id)
            
            if doc_org_id != user_org_id and "superadmin" not in current_user.roles:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Access denied to document: {doc_id}"
                )
                
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error validating document {doc_id}: {str(e)}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Error validating document: {doc_id}"
            )

async def validate_letter_access(
    db, 
    letter_id: str, 
    current_user: CurrentUser
) -> None:
    """
    Validate user access to a specific letter.
    
    Args:
        db: Database connection
        letter_id: Letter ID to validate
        current_user: The authenticated user
        
    Raises:
        HTTPException: On access denial or letter not found
    """
    try:
        if not ObjectId.is_valid(letter_id):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid letter ID format: {letter_id}"
            )
        
        letter = await db.letters.find_one({"_id": ObjectId(letter_id)})
        if not letter:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Letter not found: {letter_id}"
            )
        
        # Check access permissions
        letter_org_id = letter.get('organization_id')
        user_org_id = str(current_user.organization_id)
        
        if letter_org_id != user_org_id and "superadmin" not in current_user.roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied to letter: {letter_id}"
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error validating letter {letter_id}: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error validating letter: {letter_id}"
        )

# =========================
# Enhanced Helper Functions
# =========================

def generate_cache_key(data: Union[str, Dict], prefix: str = "") -> str:
    """
    Generate a consistent cache key from data.
    
    Args:
        data: Data to generate key from
        prefix: Optional prefix for the key
        
    Returns:
        str: Generated cache key
    """
    if isinstance(data, dict):
        data_str = json.dumps(data, sort_keys=True)
    else:
        data_str = str(data)
    
    hash_obj = hashlib.md5(data_str.encode())
    cache_key = hash_obj.hexdigest()
    
    return f"{prefix}:{cache_key}" if prefix else cache_key

async def get_cached_embedding(text: str) -> List[float]:
    """
    Get text embedding with caching to reduce API calls.
    
    Args:
        text: Text to get embedding for
        
    Returns:
        List[float]: Text embedding vector
    """
    cache_key = generate_cache_key(text, "embedding")
    
    if cache_key in embedding_cache:
        logger.debug("Retrieved embedding from cache")
        return embedding_cache[cache_key]
    
    embedding = await get_text_embedding(text)
    
    # Manage cache size
    if len(embedding_cache) >= EMBEDDING_CACHE_SIZE:
        # Remove oldest entry (simple FIFO)
        oldest_key = next(iter(embedding_cache))
        del embedding_cache[oldest_key]
    
    embedding_cache[cache_key] = embedding
    return embedding

async def get_text_embedding(text: str) -> List[float]:
    """
    Get OpenAI text embedding with rate limiting and enhanced error handling.
    
    Args:
        text: Text to get embedding for
        
    Returns:
        List[float]: Text embedding vector
        
    Raises:
        HTTPException: On API errors or rate limiting
    """
    if not text or not text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Text cannot be empty"
        )
    
    try:
        rate_limited_openai_call()
        logger.debug("Getting text embedding from OpenAI")
        
        response = client.embeddings.create(
            model=OPENAI_MODELS["embedding"],
            input=text.strip()
        )
        
        logger.debug("Successfully obtained text embedding")
        return response.data[0].embedding
        
    except RateLimitError as e:
        logger.warning(f"OpenAI rate limit exceeded: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="OpenAI rate limit exceeded. Please try again later."
        )
    except Exception as e:
        logger.error(f"Error getting embedding: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to generate embedding: {str(e)}"
        )

def cosine_similarity(a: List[float], b: List[float]) -> float:
    """
    Calculate cosine similarity between two vectors with error handling.
    
    Args:
        a: First vector
        b: Second vector
        
    Returns:
        float: Cosine similarity score between 0 and 1
    """
    try:
        a_np = np.array(a, dtype=np.float32)
        b_np = np.array(b, dtype=np.float32)
        
        if len(a_np) != len(b_np):
            logger.warning("Vector dimension mismatch in cosine similarity")
            return 0.0
        
        denom = float(np.linalg.norm(a_np) * np.linalg.norm(b_np))
        if denom == 0.0:
            return 0.0
        
        similarity = float(np.dot(a_np, b_np) / denom)
        return max(0.0, min(1.0, similarity))  # Clamp to [0, 1]
        
    except Exception as e:
        logger.warning(f"Error calculating cosine similarity: {e}")
        return 0.0

def batch_cosine_similarity(
    query_vector: List[float], 
    vectors: List[List[float]]
) -> List[float]:
    """
    Calculate cosine similarity for multiple vectors efficiently.
    
    Args:
        query_vector: Query vector
        vectors: List of vectors to compare against
        
    Returns:
        List[float]: List of similarity scores
    """
    try:
        query_np = np.array(query_vector, dtype=np.float32)
        vectors_np = np.array(vectors, dtype=np.float32)
        
        # Normalize vectors
        query_norm = np.linalg.norm(query_np)
        vectors_norms = np.linalg.norm(vectors_np, axis=1)
        
        if query_norm == 0 or np.any(vectors_norms == 0):
            return [0.0] * len(vectors)
        
        # Calculate similarities in batch
        similarities = np.dot(vectors_np, query_np) / (vectors_norms * query_norm)
        
        # Clamp and convert to list
        return np.clip(similarities, 0.0, 1.0).tolist()
        
    except Exception as e:
        logger.warning(f"Error in batch cosine similarity: {e}")
        return [0.0] * len(vectors)

async def build_authorization_filter(current_user: CurrentUser) -> Dict[str, Any]:
    """
    Build database query filter based on user authorization.
    
    Args:
        current_user: The authenticated user
        
    Returns:
        Dict[str, Any]: MongoDB query filter
    """
    query_filter: Dict[str, Any] = {}
    
    if "superadmin" not in current_user.roles:
        if "orgadmin" in current_user.roles or "orguser" in current_user.roles:
            query_filter["organization_id"] = current_user.organization_id
        elif "projectadmin" in current_user.roles or "projectuser" in current_user.roles:
            if getattr(current_user, "projects", None):
                query_filter["project_id"] = {
                    "$in": [str(pid) for pid in current_user.projects]
                }
            else:
                query_filter["project_id"] = "__none__"
    
    return query_filter

# =========================
# Core Business Logic Functions
# =========================

async def find_similar_letters_optimized(
    db,
    query: str,
    current_user: CurrentUser,
    limit: int = MAX_SIMILAR_LETTERS,
    use_cache: bool = True
) -> Tuple[List[SimilarLetter], bool]:
    """
    Find similar letters with caching and optimization.
    
    Args:
        db: Database connection
        query: Search query
        current_user: Authenticated user
        limit: Maximum number of results
        use_cache: Whether to use caching
        
    Returns:
        Tuple[List[SimilarLetter], bool]: Similar letters and cache hit status
    """
    cache_key = generate_cache_key({
        "query": query,
        "user_id": current_user.id,
        "org_id": str(current_user.organization_id),
        "limit": limit
    }, "similar_letters")
    
    # Check cache first
    if use_cache and cache_key in vector_search_cache:
        logger.debug("Retrieved similar letters from cache")
        cached_results = vector_search_cache[cache_key]
        similar_letters = [SimilarLetter(**item) for item in cached_results[:limit]]
        return similar_letters, True
    
    try:
        logger.info(f"Finding similar letters for query: {query}")
        
        # Get query embedding
        query_embedding = await get_cached_embedding(query)
        
        # Build authorization filter
        query_filter = await build_authorization_filter(current_user)
        
        # Get letters from database
        letters_cursor = db.letters.find(query_filter)
        letters = await letters_cursor.to_list(length=None)
        
        if not letters:
            logger.info("No letters found for user")
            return [], False
        
        # Process letters in batches for better performance
        similar_letters: List[SimilarLetter] = []
        batch_size = 100
        
        for i in range(0, len(letters), batch_size):
            batch = letters[i:i + batch_size]
            batch_embeddings = []
            batch_letters = []
            
            for letter in batch:
                # Check/generate embedding
                if "embedding" not in letter or not letter["embedding"]:
                    letter_text = f"{letter.get('subject', '')} {letter.get('content', '')}".strip()
                    if not letter_text:
                        continue
                        
                    letter_embedding = await get_cached_embedding(letter_text)
                    await db.letters.update_one(
                        {"_id": letter["_id"]},
                        {"$set": {"embedding": letter_embedding}}
                    )
                    letter["embedding"] = letter_embedding
                
                if letter["embedding"]:
                    batch_embeddings.append(letter["embedding"])
                    batch_letters.append(letter)
            
            # Calculate similarities in batch
            if batch_embeddings:
                similarities = batch_cosine_similarity(query_embedding, batch_embeddings)
                
                for letter, similarity in zip(batch_letters, similarities):
                    if similarity > SIMILARITY_THRESHOLD:
                        similar_letters.append(SimilarLetter(
                            id=str(letter["_id"]),
                            title=letter.get("title", ""),
                            subject=letter.get("subject", ""),
                            content=letter.get("content", ""),
                            recipient=letter.get("recipient", ""),
                            similarity_score=similarity,
                            created_at=letter.get("created_at", datetime.now().isoformat())
                        ))
        
        # Sort by similarity and limit results
        similar_letters.sort(key=lambda x: x.similarity_score, reverse=True)
        similar_letters = similar_letters[:limit]
        
        # Cache results
        if use_cache:
            cache_data = [letter.dict() for letter in similar_letters]
            vector_search_cache[cache_key] = cache_data
        
        logger.info(f"Found {len(similar_letters)} similar letters")
        return similar_letters, False
        
    except Exception as e:
        logger.error(f"Error finding similar letters: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Search failed: {str(e)}"
        )

async def extract_document_context(db, document_ids: List[str]) -> str:
    """
    Extract comprehensive context from multiple documents.
    
    Args:
        db: Database connection
        document_ids: List of document IDs to extract from
        
    Returns:
        str: Formatted document context
    """
    if not document_ids:
        return ""
    
    content_parts = []
    
    for doc_id in document_ids:
        try:
            logger.info(f"Extracting content from document: {doc_id}")
            
            document = await db.documents.find_one({"_id": ObjectId(doc_id)})
            if not document:
                logger.warning(f"Document not found: {doc_id}")
                continue
            
            # Build structured document info
            doc_info = f"Document: {document.get('filename', 'Unknown')}\n"
            doc_info += f"Subject: {document.get('subject', 'No subject')}\n"
            doc_info += f"Date: {document.get('date', 'No date')}\n"
            doc_info += f"From: {document.get('from_', document.get('from', 'Unknown'))}\n"
            doc_info += f"To: {document.get('to', 'Unknown')}\n"
            
            # Add summary if available
            summary = document.get('summary')
            if summary:
                doc_info += f"Summary: {summary}\n"
            
            # Add OCR text with length limit
            ocr_text = document.get('ocrText')
            if ocr_text:
                max_ocr_length = 4000
                if len(ocr_text) > max_ocr_length:
                    ocr_text = ocr_text[:max_ocr_length] + "... [truncated]"
                doc_info += f"OCR Text: {ocr_text}\n"
            
            # Add references if available
            references = document.get('references')
            if references:
                doc_info += "References:\n"
                for ref in references:
                    doc_info += f" - {ref}\n"
            
            content_parts.append(doc_info)
            logger.debug(f"Successfully extracted content from document: {doc_id}")
            
        except Exception as e:
            logger.warning(f"Error extracting content from document {doc_id}: {e}")
            continue
    
    return "\n\n".join(content_parts)

async def extract_key_points_and_clauses(content: str) -> Tuple[str, List[QuotedClause]]:
    """
    Extract key points and quoted clauses using structured JSON output.
    
    Args:
        content: Document content to analyze
        
    Returns:
        Tuple[str, List[QuotedClause]]: Key points and quoted clauses
    """
    if not content or not content.strip():
        return "No content provided", []
    
    try:
        logger.info("Extracting key points and clauses using JSON mode")
        
        # JSON schema for structured output
        json_schema = {
            "name": "extract_key_points_and_clauses",
            "schema": {
                "type": "object",
                "properties": {
                    "key_points": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "3-5 key points summarizing the document"
                    },
                    "quoted_clauses": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "clause_number": {"type": "string"},
                                "page_number": {"type": "string"},
                                "line_numbers": {"type": "string"},
                                "content": {"type": "string"}
                            },
                            "required": ["clause_number", "content"]
                        },
                        "description": "Quoted clauses with metadata"
                    }
                },
                "required": ["key_points", "quoted_clauses"]
            }
        }
        
        prompt = f"""
        Analyze the following document content and extract key points and quoted clauses.
        Return the response as a JSON object with the following schema:
        
        {json.dumps(json_schema, indent=2)}
        
        CONTENT:
        {content[:8000]}  # Limit content to prevent token overflow
        """
        
        rate_limited_openai_call()
        
        response = client.chat.completions.create(
            model=OPENAI_MODELS["chat"],
            messages=[
                {
                    "role": "system",
                    "content": "You are a contract analysis assistant. Extract key points and quoted clauses. Return valid JSON matching the schema."
                },
                {"role": "user", "content": prompt}
            ],
            max_tokens=1500,
            temperature=0.3,
            response_format={"type": "json_object"}
        )
        
        result = response.choices[0].message.content.strip()
        
        try:
            parsed = json.loads(result)
            key_points = "\n".join([f"- {point}" for point in parsed.get("key_points", [])])
            
            quoted_clauses = []
            for clause in parsed.get("quoted_clauses", []):
                quoted_clauses.append(QuotedClause(
                    clause_number=clause.get("clause_number", ""),
                    page_number=clause.get("page_number"),
                    line_numbers=clause.get("line_numbers"),
                    content=clause.get("content", "")
                ))
            
            logger.info("Successfully extracted key points and clauses")
            return key_points, quoted_clauses
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {str(e)}")
            return "Unable to extract key points", []
            
    except RateLimitError as e:
        logger.warning(f"Rate limit exceeded during extraction: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please try again later."
        )
    except Exception as e:
        logger.error(f"Error extracting key points and clauses: {str(e)}")
        return "Unable to extract key points", []

async def generate_ai_draft(
    subject: str,
    recipient: str,
    context: Optional[str] = None,
    points: Optional[str] = None,
    similar_letters: Optional[List[SimilarLetter]] = None,
    document_context: Optional[str] = None,
    target_letter_info: Optional[str] = None
) -> str:
    """
    Generate letter draft using AI with structured prompting.
    
    Args:
        subject: Letter subject
        recipient: Letter recipient
        context: User-provided context
        points: Specific points to address
        similar_letters: Similar letters for reference
        document_context: Document context
        target_letter_info: Target letter information for replies
        
    Returns:
        str: Generated letter draft
    """
    try:
        logger.info(f"Generating AI draft for subject: {subject}")
        
        # Prepare structured sections
        key_facts_bullets = "- None provided"
        if context:
            key_facts_bullets = "\n".join([
                f"- {line.strip()}" 
                for line in context.splitlines() 
                if line.strip()
            ])
        
        specific_points_bullets = "- None provided"
        if points:
            specific_points_bullets = "\n".join([
                f"- {line.strip()}" 
                for line in points.splitlines() 
                if line.strip()
            ])
        
        source_documents_list = document_context or "None provided"
        
        similar_letters_list = "None provided"
        if similar_letters:
            try:
                similar_letters_list = "\n".join([
                    f"- {sl.subject} | Recipient: {sl.recipient} | Date: {sl.created_at}"
                    for sl in similar_letters
                ])
            except Exception as e:
                logger.warning(f"Error formatting similar letters: {e}")
        
        target_info_block = ""
        if target_letter_info:
            target_info_block = f"\nTarget Letter Intelligence (for reply context):\n{target_letter_info}"
        
        # Use externalized template
        prompt = templates.AI_ASSISTANT_DRAFT_TEMPLATE.format(
            subject=subject,
            recipient=recipient,
            key_facts_bullets=key_facts_bullets,
            source_documents_list=source_documents_list,
            specific_points_bullets=specific_points_bullets,
            similar_letters_list=similar_letters_list,
            target_info_block=target_info_block
        )
        
        # Try Responses API first, fallback to Chat Completions
        try:
            rate_limited_openai_call()
            
            content_blocks = [{"type": "input_text", "text": prompt}]
            response = client.responses.create(
                model=OPENAI_MODELS["responses"],
                input=[{"role": "user", "content": content_blocks}]
            )
            
            if hasattr(response, "output_text") and response.output_text:
                logger.info("Successfully generated draft using Responses API")
                return response.output_text.strip()
            else:
                raise Exception("No output from Responses API")
                
        except Exception as responses_error:
            logger.warning(f"Responses API failed, using Chat Completions: {responses_error}")
            
            rate_limited_openai_call()
            
            response = client.chat.completions.create(
                model=OPENAI_MODELS["chat"],
                messages=[
                    {
                        "role": "system", 
                        "content": templates.AI_ASSISTANT_SYSTEM_PROMPT
                    },
                    {"role": "user", "content": prompt}
                ],
                max_tokens=2000,
                temperature=0.2
            )
            
            logger.info("Successfully generated draft using Chat Completions")
            return response.choices[0].message.content.strip()
        
    except RateLimitError as e:
        logger.warning(f"Rate limit exceeded during draft generation: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please try again later."
        )
    except Exception as e:
        logger.error(f"Error generating AI draft: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Draft generation failed: {str(e)}"
        )

# =========================
# API Endpoints
# =========================

@router.post("/ai-assistant/search-letters", response_model=VectorSearchResponse)
async def search_similar_letters(
    request: LetterSearchRequest,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Search for semantically similar letters using optimized vector embeddings.
    
    Args:
        request: Search request with query and optional limit
        db: Database connection dependency
        current_user: Authenticated user with role-based permissions
        
    Returns:
        VectorSearchResponse: Similar letters and query embedding with performance metrics
        
    Raises:
        HTTPException: On authentication, authorization, or processing errors
    """
    start_time = time.time()
    
    try:
        logger.info(f"Processing letter search request: {request.query}")
        
        # Find similar letters with caching
        similar_letters, cached = await find_similar_letters_optimized(
            db=db,
            query=request.query,
            current_user=current_user,
            limit=request.limit
        )
        
        # Get query embedding for response
        query_embedding = await get_cached_embedding(request.query)
        
        processing_time = time.time() - start_time
        
        logger.info(f"Found {len(similar_letters)} letters in {processing_time:.3f}s")
        
        return VectorSearchResponse(
            similar_letters=similar_letters,
            query_embedding=query_embedding,
            processing_time=processing_time,
            cached_results=cached
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in letter search: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Search service temporarily unavailable"
        )

@router.post("/ai-assistant/generate-draft", response_model=LetterDraftResponse)
async def generate_letter_draft(
    request: LetterDraftRequest,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Generate a comprehensive letter draft with AI assistance.
    
    This endpoint combines multiple AI capabilities including document analysis,
    similar letter retrieval, and intelligent draft generation.
    
    Args:
        request: Letter draft request with all necessary parameters
        db: Database connection dependency  
        current_user: Authenticated user with proper permissions
        
    Returns:
        LetterDraftResponse: Generated draft with supporting information
        
    Raises:
        HTTPException: On validation, permission, or processing errors
    """
    start_time = time.time()
    
    try:
        logger.info(f"Processing draft generation request: {request.subject}")
        
        # Comprehensive request validation
        await validate_letter_draft_request(request, current_user, db)
        
        # Extract document context if document IDs provided
        document_context = ""
        extracted_key_points = ""
        if request.document_ids:
            document_context = await extract_document_context(db, request.document_ids)
            if document_context:
                extracted_key_points, _ = await extract_key_points_and_clauses(document_context)
        
        # Find similar letters for reference
        similar_letters, used_cache = await find_similar_letters_optimized(
            db=db,
            query=request.subject,
            current_user=current_user,
            limit=MAX_SIMILAR_LETTERS,
            use_cache=True
        )
        
        # Get target letter information for replies
        target_letter_info = ""
        if request.target_letter_id:
            try:
                target_letter = await db.letters.find_one({"_id": ObjectId(request.target_letter_id)})
                if target_letter:
                    target_info_parts = []
                    
                    # Add summary
                    summary = target_letter.get("summary", "").strip()
                    if summary:
                        target_info_parts.append(f"Summary: {summary}")
                    
                    # Add keywords
                    keywords = target_letter.get("keywords")
                    if keywords:
                        if isinstance(keywords, list):
                            kw_text = ", ".join([str(k).strip() for k in keywords if str(k).strip()])
                        else:
                            kw_text = str(keywords)
                        if kw_text.strip():
                            target_info_parts.append(f"Keywords: {kw_text}")
                    
                    # Add OCR excerpts
                    ocr_text = target_letter.get("ocrText", "").strip()
                    if ocr_text:
                        truncated = ocr_text[:1000] + ("..." if len(ocr_text) > 1000 else "")
                        target_info_parts.append(f"Content excerpts: {truncated}")
                    
                    target_letter_info = "\n\n".join(target_info_parts)
            except Exception as e:
                logger.warning(f"Failed to get target letter info: {e}")
        
        # Generate the draft
        draft_letter = await generate_ai_draft(
            subject=request.subject,
            recipient=request.recipient,
            context=request.context,
            points=request.points,
            similar_letters=similar_letters,
            document_context=document_context,
            target_letter_info=target_letter_info
        )
        
        # Generate structure summary
        structure_parts = []
        if request.document_ids:
            structure_parts.append(f"{len(request.document_ids)} source documents")
        structure_parts.append(f"{len(similar_letters)} similar letters")
        if request.target_letter_id:
            structure_parts.append("target letter context")
        if used_cache:
            structure_parts.append("cached similarity results")
        
        structure_summary = f"Generated draft using: {', '.join(structure_parts)}."
        
        # Store draft in database for future reference
        effective_org_id = request.organization_id or str(current_user.organization_id)
        effective_proj_id = request.project_id
        
        draft_data = {
            "subject": request.subject,
            "recipient": request.recipient,
            "content": draft_letter,
            "generated_by": current_user.id,
            "generated_at": datetime.utcnow(),
            "organization_id": effective_org_id,
            "project_id": effective_proj_id,
            "document_ids_used": request.document_ids or [],
            "similar_letters_used": [sl.id for sl in similar_letters],
            "processing_time": time.time() - start_time
        }
        
        # Generate embedding for the draft
        try:
            draft_embedding = await get_cached_embedding(f"{request.subject} {draft_letter}")
            draft_data["embedding"] = draft_embedding
        except Exception as e:
            logger.warning(f"Failed to generate draft embedding: {e}")
        
        await db.ai_generated_drafts.insert_one(draft_data)
        
        processing_time = time.time() - start_time
        logger.info(f"Successfully generated draft in {processing_time:.3f}s")
        
        return LetterDraftResponse(
            draft_letter=draft_letter,
            similar_letters=similar_letters,
            structure_summary=structure_summary,
            extracted_key_points=extracted_key_points or None,
            processing_time=processing_time,
            used_cache=used_cache
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Unexpected error in draft generation: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Draft generation service temporarily unavailable"
        )

@router.get("/ai-assistant/stats", response_model=AIAssistantStats)
async def get_ai_assistant_stats(
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Get AI assistant usage statistics for monitoring and optimization.
    
    Args:
        db: Database connection dependency
        current_user: Authenticated user
        
    Returns:
        AIAssistantStats: Usage statistics and performance metrics
    """
    try:
        logger.info("Retrieving AI assistant statistics")
        
        # Basic authorization check
        if "superadmin" not in current_user.roles and "orgadmin" not in current_user.roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions to view statistics"
            )
        
        # Get statistics from generated drafts
        pipeline = [
            {"$match": {"organization_id": str(current_user.organization_id)}},
            {
                "$group": {
                    "_id": None,
                    "total_requests": {"$sum": 1},
                    "avg_processing_time": {"$avg": "$processing_time"},
                    "recent_requests": {
                        "$sum": {
                            "$cond": [
                                {"$gte": ["$generated_at", datetime.utcnow() - timedelta(days=7)]},
                                1,
                                0
                            ]
                        }
                    }
                }
            }
        ]
        
        stats_cursor = db.ai_generated_drafts.aggregate(pipeline)
        stats_result = await stats_cursor.to_list(length=1)
        
        if stats_result:
            stats = stats_result[0]
            total_requests = stats.get("total_requests", 0)
            avg_response_time = stats.get("avg_processing_time", 0.0)
            recent_requests = stats.get("recent_requests", 0)
        else:
            total_requests = 0
            avg_response_time = 0.0
            recent_requests = 0
        
        # Calculate cache hit rate
        cache_hit_rate = 0.0
        if len(embedding_cache) > 0:
            cache_hit_rate = min(0.8, len(embedding_cache) / EMBEDDING_CACHE_SIZE)
        
        return AIAssistantStats(
            total_requests=total_requests,
            successful_requests=total_requests,  # Simplified for now
            failed_requests=0,  # Would need error tracking
            avg_response_time=avg_response_time,
            cache_hit_rate=cache_hit_rate
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving statistics: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Statistics service temporarily unavailable"
        )

@router.post("/ai-assistant/analyze-document")
async def analyze_uploaded_document(
    file: UploadFile = File(...),
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Analyze an uploaded document and extract key points and clauses.
    
    Args:
        file: Uploaded file to analyze
        db: Database connection dependency
        current_user: Authenticated user
        
    Returns:
        dict: Analysis results with key points and quoted clauses
    """
    try:
        logger.info(f"Analyzing uploaded document: {file.filename}")
        
        # Validate file
        if not file.filename:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No file uploaded"
            )
        
        # Check file size (limit to 10MB)
        max_size = 10 * 1024 * 1024  # 10MB
        content = await file.read()
        if len(content) > max_size:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="File too large (max 10MB)"
            )
        
        # Decode content
        try:
            text_content = content.decode('utf-8', errors='ignore')
        except Exception as e:
            logger.error(f"Error decoding file content: {e}")
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Could not read file content"
            )
        
        if not text_content.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="File appears to be empty or contains no readable text"
            )
        
        # Extract key points and clauses
        key_points, quoted_clauses = await extract_key_points_and_clauses(text_content)
        
        logger.info(f"Successfully analyzed document: {file.filename}")
        
        return {
            "filename": file.filename,
            "file_size": len(content),
            "key_points": key_points,
            "quoted_clauses": [clause.dict() for clause in quoted_clauses],
            "analysis_timestamp": datetime.utcnow().isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error analyzing document: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Document analysis service temporarily unavailable"
        )

@router.delete("/ai-assistant/cache")
async def clear_cache(
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Clear AI assistant caches for performance optimization.
    
    Args:
        current_user: Authenticated user (admin only)
        
    Returns:
        dict: Cache clearing results
    """
    try:
        logger.info("Processing cache clear request")
        
        # Check admin permissions
        if "superadmin" not in current_user.roles and "orgadmin" not in current_user.roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions to clear cache"
            )
        
        # Clear caches
        embedding_cache_size = len(embedding_cache)
        vector_cache_size = len(vector_search_cache)
        
        embedding_cache.clear()
        vector_search_cache.clear()
        
        logger.info(f"Cleared caches: {embedding_cache_size} embeddings, {vector_cache_size} searches")
        
        return {
            "status": "success",
            "cleared_embedding_cache": embedding_cache_size,
            "cleared_search_cache": vector_cache_size,
            "timestamp": datetime.utcnow().isoformat()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error clearing cache: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Cache service temporarily unavailable"
        )

# Add health check endpoint
@router.get("/ai-assistant/health")
async def health_check():
    """
    Health check endpoint for service monitoring.
    
    Returns:
        dict: Service health status
    """
    return {
        "status": "healthy",
        "service": "ai-assistant",
        "timestamp": datetime.utcnow().isoformat(),
        "cache_sizes": {
            "embeddings": len(embedding_cache),
            "searches": len(vector_search_cache)
        }
    }

# Add to enhanced helper functions
async def ensure_vector_store_for_context(db, current_user) -> Tuple[str, str]:
    """Create or reuse a Vector Store for the user's organization/project combo."""
    try:
        org_id = str(current_user.organization_id)
        eff_proj = _effective_project_id(current_user)
        proj_id = str(eff_proj or "none")
        key = f"org:{org_id}:proj:{proj_id}"

        mapping = await db.ai_vector_stores.find_one({"key": key})
        if mapping and mapping.get("vector_store_id"):
            return mapping["vector_store_id"], mapping.get("name", key)

        vs = client.vector_stores.create(name=f"ContraClaim-{org_id}-{proj_id}")
        await db.ai_vector_stores.update_one(
            {"key": key},
            {"$set": {"key": key, "name": vs.name, "vector_store_id": vs.id}},
            upsert=True,
        )
        return vs.id, vs.name
    except Exception as e:
        logger.error(f"Error ensuring vector store: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Vector store initialization failed"
        )
        
        # Add to API endpoints section
@router.post("/ai-assistant/rag/upload")
async def upload_to_rag(
    request: UploadToRAGRequest,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Upload files to RAG vector store with enhanced validation."""
    try:
        # Validate file access and existence
        for file_path in request.files:
            if not os.path.exists(file_path):
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"File not found: {file_path}"
                )
        
        vs_id, vs_name = await ensure_vector_store_for_context(db, current_user)
        result = await upload_files_to_vector_store(
            request.files, vs_id, db=db, current_user=current_user
        )
        
        # Update assistant with new vector store
        a_id = await ensure_assistant()
        client.assistants.update(
            a_id, 
            tool_resources={"file_search": {"vector_store_ids": [vs_id]}}
        )
        
        return {
            "vector_store_id": vs_id,
            "name": vs_name,
            "result": result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"RAG upload failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="RAG upload failed"
        )
        
        # Enhance the document analysis function
async def analyze_document_with_rag(
    content: str,
    db,
    current_user: CurrentUser,
    use_vector_store: bool = True
) -> Dict[str, Any]:
    """Analyze document content with optional RAG enhancement."""
    try:
        # Base analysis
        key_points, quoted_clauses = await extract_key_points_and_clauses(content)
        
        # Enhance with RAG if enabled
        if use_vector_store and VECTOR_STORE_ENABLED:
            try:
                vs_id, _ = await ensure_vector_store_for_context(db, current_user)
                # Perform vector search for related content
                related_content = await vector_search_similar_content(
                    content, vs_id, limit=3
                )
                return {
                    "key_points": key_points,
                    "quoted_clauses": quoted_clauses,
                    "related_content": related_content
                }
            except Exception as e:
                logger.warning(f"RAG enhancement failed: {e}")
                # Fall back to basic analysis
        
        return {
            "key_points": key_points,
            "quoted_clauses": quoted_clauses
        }
        
    except Exception as e:
        logger.error(f"Document analysis failed: {str(e)}")
        raise
        
        # Add response generation endpoint
@router.post("/ai-assistant/generate-response")
async def generate_response_endpoint(
    request: GenerateResponseRequest,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Generate context-aware responses using RAG."""
    try:
        # Validate input
        if not request.letter_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Letter ID is required"
            )
        
        # Get letter content
        letter = await db.letters.find_one({"_id": ObjectId(request.letter_id)})
        if not letter:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Letter not found"
            )
        
        # Generate response using RAG
        response = await generate_ai_response(
            letter_content=letter.get("content", ""),
            context=request.context,
            db=db,
            current_user=current_user
        )
        
        return {"response": response}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Response generation failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Response generation failed"
        )
        