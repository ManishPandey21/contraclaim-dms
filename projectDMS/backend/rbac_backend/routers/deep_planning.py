from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from ..core.database import get_db
from ..core.security import get_current_user, CurrentUser, authorize_scope, build_scope_query
from ..models.letter import Letter
from ..models.document import Document
from typing import List, Optional, Dict, Tuple, Any
from pydantic import BaseModel, field_validator
import logging
from openai import OpenAI, RateLimitError
from ..core.config import settings
from ..services.upload_streaming import read_upload_within_limit
from ..core import templates
from ..core.constants import OPENAI_MODELS
from ..services.policy_service import PolicyService
import numpy as np
from bson.objectid import ObjectId
from datetime import datetime
import asyncio
import os
import re
import hashlib
import time
import json
from ratelimit import limits, sleep_and_retry

# One server-side decision about whether a document's extracted text may be
# consumed. Planning prompts are a downstream knowledge consumer like any other.
from ..services.publication_policy import (
    authoritative_summary,
    authoritative_text,
    is_consumable,
    resolve_document_authority,
)
from ..utils.error_handler import BaseDomainError

router = APIRouter()
logger = logging.getLogger(__name__)


class _SafePromptValues(dict):
    """Leave unknown placeholders intact instead of crashing prompt rendering."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"

# OpenAI client setup with enhanced configuration
client = OpenAI(api_key=settings.OPENAI_API_KEY)
VECTOR_STORE_ENABLED = getattr(settings, "VECTOR_STORE_ENABLED", False)

# Rate limiting configuration
OPENAI_CALLS_PER_MINUTE = 30
OPENAI_CALLS_PER_DAY = 1000

# =========================
# Pydantic Models
# =========================
class QuotedClause(BaseModel):
    clause_number: str
    page_number: Optional[str] = None
    line_numbers: Optional[str] = None
    content: str

class DeepPlanningRequest(BaseModel):
    document_ids: List[str]
    subject: str
    recipient: str
    user_id: str
    context: Optional[str] = None
    points: Optional[str] = None
    target_letter_id: Optional[str] = None
    organization_id: Optional[str] = None
    project_id: Optional[str] = None
    org_short: Optional[str] = None
    project_short: Optional[str] = None
    org_name: Optional[str] = None
    project_name: Optional[str] = None
    use_vector_store: Optional[bool] = False

class DeepPlanningResponse(BaseModel):
    draft_letter: str
    extracted_key_points: str
    quoted_clauses: List[QuotedClause]
    similar_letters: List[dict]
    structure_summary: str

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
# Vector Store Helpers (from ai_assistant.py)
# =========================
async def ensure_vector_store_for_context(db, current_user) -> Tuple[str, str]:
    """Create or reuse a Vector Store for the user's organization/project combo."""
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

def _sha256_file(path: str) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return None

async def upload_files_to_vector_store(filepaths: List[str], vector_store_id: str, db=None, current_user: Optional[CurrentUser]=None) -> Dict[str, Any]:
    """
    De-dup by SHA256; store registry in ai_files
    """
    uploaded, skipped, failed, file_ids = [], [], [], []
    org_id = str(getattr(current_user, "organization_id", ""))
    proj_id = str(getattr(current_user, "project_id", ""))

    for p in filepaths:
        try:
            if not os.path.exists(p):
                failed.append({"path": p, "error": "not found"})
                continue
            sha = _sha256_file(p) or f"nosha:{os.path.basename(p)}"
            exists = None
            if db is not None:
                exists = await db.ai_files.find_one({"sha256": sha, "vector_store_id": vector_store_id})
            if exists and exists.get("file_id"):
                skipped.append({"path": p, "file_id": exists["file_id"]})
                continue

            f = client.files.create(file=open(p, "rb"), purpose="assistants")
            file_ids.append(f.id)
            uploaded.append({"path": p, "file_id": f.id})

            if db is not None:
                await db.ai_files.insert_one({
                    "sha256": sha,
                    "path": p,
                    "file_id": f.id,
                    "vector_store_id": vector_store_id,
                    "org_id": org_id,
                    "project_id": proj_id,
                    "created_at": datetime.utcnow()
                })
        except Exception as e:
            failed.append({"path": p, "error": str(e)})

    if file_ids:
        client.vector_stores.file_batches.upload_and_poll(vector_store_id, file_ids=file_ids)

    return {"uploaded": uploaded, "skipped": skipped, "failed": failed}

async def _fetch_vector_chunks_for_query(
    db,
    org_id: str,
    proj_id: Optional[str],
    query_embedding: List[float],
    max_scan: int = 2000,
    top_docs: int = 5,
    chunks_per_doc: int = 2,
) -> List[Dict[str, Any]]:
    """
    Fetch top similar chunks for a query from document_vectors filtered by org/project.
    """
    try:
        f: Dict[str, Any] = {
            "organization_id": str(org_id),
            "uploadType": {"$in": ["incoming", "outgoing"]},
        }
        if proj_id is not None:
            f["project_id"] = str(proj_id)

        cursor = db.document_vectors.find(f).sort("createdAt", -1).limit(max_scan)
        rows = await cursor.to_list(length=max_scan)

        # Resolve authority once per source document. Chunks are
        # extraction-controlled content and carry document_id, so eligibility
        # comes from the canonical document rather than the chunk itself.
        _authority: Dict[str, bool] = {}

        async def _ok(doc_id: str) -> bool:
            if doc_id not in _authority:
                _authority[doc_id] = (
                    await resolve_document_authority(db, doc_id)
                ).consumable
            return _authority[doc_id]

        # Group by document_id and compute similarities
        by_doc: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            emb = r.get("embedding") or []
            if not emb or len(emb) != len(query_embedding):
                continue
            score = cosine_similarity(query_embedding, emb)
            entry = {
                "document_id": str(r.get("document_id")),
                "text": r.get("text", ""),
                "score": float(score),
                "chunk_index": int(r.get("chunk_index", 0)),
                "uploadType": r.get("uploadType", ""),
                "letterNo": r.get("letterNo"),
                "createdAt": r.get("createdAt"),
                "_authority_ok": await _ok(str(r.get("document_id"))),
            }
            by_doc.setdefault(entry["document_id"], []).append(entry)

        # For each doc, sort its chunks by score desc and keep top chunks_per_doc
        doc_best: List[Tuple[str, float, List[Dict[str, Any]]]] = []
        for doc_id, chunks in by_doc.items():
            chunks.sort(key=lambda x: x["score"], reverse=True)
            max_score = chunks[0]["score"] if chunks else 0.0
            doc_best.append((doc_id, max_score, chunks[:chunks_per_doc]))

        # Sort docs by their best score and pick top_docs
        doc_best.sort(key=lambda t: t[1], reverse=True)
        result: List[Dict[str, Any]] = []
        for _, _, chunks in doc_best[:top_docs]:
            result.extend(chunks)

        return result
    except Exception as e:
        logger.warning(f"Vector chunk fetch failed: {e}")
        return []

def _format_vector_chunks_for_prompt(chunks: List[Dict[str, Any]]) -> str:
    """
    Format vector chunks into a readable section for the user prompt.
    """
    if not chunks:
        return ""

    def score_key(c: Dict[str, Any]) -> float:
        try:
            return float(c.get("score") or 0.0)
        except Exception:
            return 0.0

    try:
        chunks = sorted(chunks, key=score_key, reverse=True)
    except Exception:
        pass

    lines: List[str] = []
    lines.append("Vector-derived correspondence excerpts (scoped to your organization/project):")
    for i, c in enumerate(chunks, 1):
        meta = []
        if c.get("uploadType"):
            meta.append(f"Type: {c['uploadType']}")
        if c.get("letterNo"):
            meta.append(f"LetterNo: {c['letterNo']}")
        if c.get("document_id"):
            meta.append(f"Doc: {c['document_id']}")
        if c.get("score") is not None:
            try:
                meta.append(f"Sim: {float(c['score']):.3f}")
            except Exception:
                pass
        header = f"[{i}] " + " | ".join(meta) if meta else f"[{i}]"
        # Chunk text is extraction-controlled. The sibling path
        # (extract_document_content) is guarded; this one fed vector chunks
        # straight into the planning prompt. Chunks carry document_id, so
        # eligibility resolves back to the source document.
        body = (c.get("text") or "").strip() if c.get("_authority_ok", True) else ""
        if body:
            if len(body) > 1800:
                body = body[:1800] + " ..."
            lines.append(header)
            lines.append(body)
            lines.append("")  # spacer
    return "\n".join(lines).strip()

# =========================
# Enhanced Helper Functions
# =========================
async def get_text_embedding(text: str) -> List[float]:
    """Get OpenAI text embedding for the given text with rate limiting."""
    try:
        rate_limited_openai_call()
        logger.info("Getting text embedding from OpenAI")

        response = client.embeddings.create(
            model=OPENAI_MODELS["embedding"],
            input=text
        )
        logger.debug("Successfully obtained text embedding")
        return response.data[0].embedding
    except RateLimitError as e:
        logger.warning(f"OpenAI rate limit exceeded: {str(e)}")
        raise HTTPException(status_code=429, detail="OpenAI rate limit exceeded. Please try again later.")
    except (BaseDomainError, HTTPException):
        raise
    except Exception as e:
        logger.error(f"Error getting embedding: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to generate embedding: {str(e)}")

def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Calculate cosine similarity between two vectors."""
    a_np = np.array(a, dtype=np.float32)
    b_np = np.array(b, dtype=np.float32)
    denom = float(np.linalg.norm(a_np) * np.linalg.norm(b_np))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a_np, b_np) / denom)

def make_short_name(value: Optional[str]) -> str:
    """Create a short name for organization/project from a full name."""
    try:
        if not value:
            return "untitled"
        s = value.lower()
        s = re.sub(r'[^a-z0-9]+', '-', s)
        s = re.sub(r'-{2,}', '-', s)
        s = s.strip('-')
        s = s[:10]
        return s if s else "untitled"
    except Exception:
        return "untitled"

def _effective_project_id(current_user) -> Optional[str]:
    """Return current_user.project_id if present, otherwise first entry from current_user.projects (if any)."""
    try:
        proj = getattr(current_user, "project_id", None)
        if proj:
            return str(proj)
        projects = getattr(current_user, "projects", None)
        if projects and isinstance(projects, list) and len(projects) > 0:
            return str(projects[0])
    except Exception:
        pass
    return None

async def validate_document_ids(db, document_ids: List[str], current_user: CurrentUser) -> None:
    """Validate document IDs and check user access permissions."""
    logger.info(f"Validating document IDs: {document_ids}")

    for doc_id in document_ids:
        try:
            # Validate ObjectId format
            if not ObjectId.is_valid(doc_id):
                raise HTTPException(status_code=400, detail=f"Invalid document ID format: {doc_id}")

            # Check if document exists
            document = await db.documents.find_one({"_id": ObjectId(doc_id)})
            if not document:
                raise HTTPException(status_code=404, detail=f"Document not found: {doc_id}")

            # Check user access permissions
            doc_org_id = document.get('organization_id')
            user_org_id = getattr(current_user, 'organization_id', None)

            if doc_org_id != user_org_id:
                raise HTTPException(status_code=403, detail=f"Access denied to document: {doc_id}")

        except (BaseDomainError, HTTPException):
            raise
        except Exception as e:
            logger.error(f"Error validating document {doc_id}: {str(e)}")
            raise HTTPException(status_code=500, detail=f"Error validating document: {doc_id}")

# =========================
# Enhanced Core Functionality
# =========================
async def extract_document_content(db, document_ids: List[str]) -> str:
    """Extract content from multiple documents for context, including OCR text and summary."""
    content_parts = []

    for doc_id in document_ids:
        try:
            logger.info(f"Extracting content from document: {doc_id}")
            document = await db.documents.find_one({"_id": ObjectId(doc_id)})
            if document:
                # Extract key information from document
                doc_info = f"Document: {document.get('filename', 'Unknown')}\n"
                doc_info += f"Subject: {document.get('subject', 'No subject')}\n"
                doc_info += f"Date: {document.get('date', 'No date')}\n"
                doc_info += f"From: {document.get('from_', document.get('from', 'Unknown'))}\n"
                doc_info += f"To: {document.get('to', 'Unknown')}\n"

                # Add summary if available. Gated: the summary is derived
                # from the same extracted text, so serving it for a blocked
                # document withholds nothing.
                summary = authoritative_summary(document)
                if summary:
                    doc_info += f"Summary: {summary}\n"

                # Add OCR text if available (truncate if too long).
                # Read through the publication policy: a document with
                # unresolved extraction-quality findings must not reach the
                # planning prompt. See services/publication_policy.py.
                ocr_text = authoritative_text(document)
                if ocr_text:
                    # Limit OCR text length to prevent token overflow
                    max_ocr_length = 4000
                    if len(ocr_text) > max_ocr_length:
                        ocr_text = ocr_text[:max_ocr_length] + "... [truncated]"
                    # Source text when the document has it, else the LLM's
                    # Item 25 fallback - so it is not labelled "OCR".
                    doc_info += f"Document Text: {ocr_text}\n"

                # Add references if available
                if document.get('references'):
                    doc_info += "References:\n"
                    for ref in document.get('references', []):
                        doc_info += f"  - {ref}\n"

                content_parts.append(doc_info)
                logger.debug(f"Successfully extracted content from document: {doc_id}")
        except Exception as e:
            logger.warning(f"Error extracting content from document {doc_id}: {e}")
            continue

    return "\n\n".join(content_parts)

async def get_vector_store_context(db, query: str, organization_id: str, project_id: Optional[str] = None) -> Optional[str]:
    """Retrieve relevant context from vector store."""
    if not VECTOR_STORE_ENABLED:
        logger.info("Vector store support is disabled")
        return None

    try:
        logger.info("Querying vector store for relevant context")
        query_embedding = await get_text_embedding(query)

        # Fetch top chunks from document_vectors collection
        top_chunks = await _fetch_vector_chunks_for_query(
            db=db,
            org_id=organization_id,
            proj_id=project_id,
            query_embedding=query_embedding,
            max_scan=2000,
            top_docs=5,
            chunks_per_doc=2,
        )

        # Format the chunks for inclusion in the prompt
        vector_context = _format_vector_chunks_for_prompt(top_chunks)

        if vector_context:
            logger.info(f"Retrieved {len(top_chunks)} vector chunks for context")
            return vector_context
        else:
            logger.info("No relevant vector chunks found")
            return None

    except Exception as e:
        logger.error(f"Error querying vector store: {str(e)}")
        return None

async def find_similar_letters(db, subject: str, organization_id: Optional[str] = None,
                              project_id: Optional[str] = None, current_user: CurrentUser = None) -> List[dict]:
    """Find letters similar to `subject` within the caller's authorised tenant scope.

    The candidate set is the caller's row visibility from `build_scope_query`,
    narrowed by the requested organisation/project; similarity is computed only
    over that set. These letters reach the drafting prompt, the response body and
    the persisted draft, so scope is applied here, before any of them see a row.
    A role outside every scope tier, or a principal with no organisation/project,
    gets the deny-all filter - never an unfiltered query.
    """
    try:
        if current_user is None:
            return []
        query_filter = build_scope_query(
            current_user,
            organization_id=organization_id,
            project_id=project_id,
        )
        if query_filter == {"_id": {"$in": []}}:
            return []

        logger.info(f"Finding similar letters for subject: {subject}")
        query_embedding = await get_text_embedding(subject)

        letters_cursor = db.letters.find(query_filter)
        letters = await letters_cursor.to_list(length=None)

        similar_letters = []

        for letter in letters:
            # Check/generate embedding
            if "embedding" not in letter or not letter["embedding"]:
                letter_text = f"{letter.get('subject', '')} {letter.get('content', '')}".strip()
                if not letter_text:
                    continue
                letter_embedding = await get_text_embedding(letter_text)
                await db.letters.update_one(
                    {"_id": letter["_id"]},
                    {"$set": {"embedding": letter_embedding}}
                )
                letter["embedding"] = letter_embedding

            # Calculate similarity
            similarity = cosine_similarity(query_embedding, letter["embedding"])
            if similarity > 0.3:  # Threshold for relevance
                similar_letters.append({
                    "id": str(letter["_id"]),
                    "title": letter.get("title", ""),
                    "subject": letter.get("subject", ""),
                    "content": letter.get("content", ""),
                    "recipient": letter.get("recipient", ""),
                    "similarity_score": similarity,
                    "created_at": (letter.get("created_at") or datetime.now()).isoformat()
                })

        # Sort by similarity score and limit results
        similar_letters.sort(key=lambda x: x["similarity_score"], reverse=True)
        logger.info(f"Found {len(similar_letters)} similar letters")
        return similar_letters[:5]  # Return top 5 similar letters

    except Exception as e:
        logger.error(f"Error finding similar letters: {str(e)}")
        return []

async def generate_draft_with_ai(
    subject: str,
    recipient: str,
    document_context: str,
    user_context: Optional[str] = None,
    points: Optional[str] = None,
    similar_letters: List[dict] = None,
    target_letter_info: Optional[str] = None,
    vector_store_context: Optional[str] = None
) -> str:
    """Generate a letter draft using OpenAI with document context, OCR text, and summary."""
    try:
        logger.info("Generating draft with AI")

        # Prepare structured sections per protocol
        key_facts_bullets = ""
        if user_context:
            key_facts_bullets = "\n".join([f"- {line.strip()}" for line in user_context.splitlines() if line.strip()])
        else:
            key_facts_bullets = "- None provided"

        specific_points_bullets = ""
        if points:
            specific_points_bullets = "\n".join([f"- {line.strip()}" for line in points.splitlines() if line.strip()])
        else:
            specific_points_bullets = "- None provided"

        # Use the extracted document context as a readable list for reference
        source_documents_list = (document_context or "").strip() or "None provided"

        # Similar letters are for tone/style cues only
        similar_letters_list = ""
        if similar_letters:
            try:
                similar_letters_list = "\n".join([
                    f"- {sl.get('subject','').strip()} | Recipient: {sl.get('recipient','').strip()} | Date: {sl.get('created_at','')}"
                    for sl in similar_letters if isinstance(sl, dict)
                ])
            except Exception:
                similar_letters_list = ""

        # Precompute target letter intelligence block
        target_info_block = ""
        try:
            if target_letter_info and target_letter_info.strip():
                target_info_block = "\nTarget Letter Intelligence (for reply context):\n" + target_letter_info.strip()
        except Exception:
            target_info_block = ""

        # Add vector store context if available
        vector_context_block = ""
        if vector_store_context:
            vector_context_block = f"\nVector Store Context:\n{vector_store_context}"

        # Use externalized template
        prompt = templates.LETTER_DRAFT_PROMPT_TEMPLATE.format_map(
            _SafePromptValues(
                subject=subject,
                key_facts_bullets=key_facts_bullets,
                source_documents_list=source_documents_list,
                specific_points_bullets=specific_points_bullets,
                similar_letters_list=similar_letters_list if similar_letters_list else "None provided",
                target_info_block=target_info_block,
                vector_context_block=vector_context_block,
            )
        )

        rate_limited_openai_call()
        response = client.chat.completions.create(
            model=OPENAI_MODELS["chat"],
            messages=[
                {"role": "system", "content": templates.CONTRACT_MANAGER_SYSTEM_PROMPT},
                {"role": "user", "content": prompt}
            ],
            max_tokens=2000,
            temperature=0.2
        )

        logger.info("Successfully generated draft using Chat Completions API")
        return response.choices[0].message.content.strip()

    except RateLimitError as e:
        logger.warning(f"OpenAI rate limit exceeded during draft generation: {str(e)}")
        raise HTTPException(status_code=429, detail="OpenAI rate limit exceeded. Please try again later.")
    except (BaseDomainError, HTTPException):
        raise
    except Exception as e:
        logger.error(f"Error generating draft with AI: {str(e)}")
        raise HTTPException(status_code=500, detail=f"AI draft generation failed: {str(e)}")

async def extract_key_points_and_clauses(content: str) -> tuple:
    """Extract key points and quoted clauses from document content using JSON mode."""
    try:
        logger.info("Extracting key points and clauses from document content using JSON mode")

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
                        "description": "Quoted clauses with their metadata"
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
{content}
"""

        rate_limited_openai_call()
        request_payload = {
            "model": OPENAI_MODELS["chat"],
            "messages": [
                {
                    "role": "system",
                    "content": "You are a contract analysis assistant that extracts key points and quoted clauses. Return your response as valid JSON matching the provided schema.",
                },
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 1500,
            "temperature": 0.3,
        }
        try:
            response = client.chat.completions.create(
                **request_payload,
                response_format={"type": "json_object"},
            )
        except TypeError:
            response = client.chat.completions.create(**request_payload)

        result = response.choices[0].message.content.strip()

        # Parse the JSON response
        try:
            parsed = json.loads(result)
            key_points = "\n".join([f"- {point}" for point in parsed.get("key_points", [])])

            # Convert to QuotedClause objects
            quoted_clauses = []
            for clause in parsed.get("quoted_clauses", []):
                quoted_clauses.append({
                    "clause_number": clause.get("clause_number", ""),
                    "page_number": clause.get("page_number"),
                    "line_numbers": clause.get("line_numbers"),
                    "content": clause.get("content", "")
                })

            logger.info("Successfully extracted key points and clauses using JSON mode")
            return key_points, quoted_clauses

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse JSON response, falling back to plaintext parsing: {str(e)}")

            key_points: List[str] = []
            quoted_clauses: List[Dict[str, Optional[str]]] = []
            section = None
            for raw_line in result.splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                upper = line.upper().rstrip(":")
                if upper == "KEY POINTS":
                    section = "key_points"
                    continue
                if upper == "QUOTED CLAUSES":
                    section = "quoted_clauses"
                    continue
                if not line.startswith("-"):
                    continue

                content = line.lstrip("- ").strip()
                if section == "key_points":
                    key_points.append(content)
                    continue

                if section == "quoted_clauses":
                    match = re.match(
                        r"(?P<clause_number>[^:]+):\s*(?P<content>.*?)(?:\s*\(Page\s*(?P<page_number>[^,)]+)(?:,\s*Lines?\s*(?P<line_numbers>[^)]+))?\))?$",
                        content,
                        flags=re.IGNORECASE,
                    )
                    if match:
                        quoted_clauses.append(
                            {
                                "clause_number": match.group("clause_number").strip(),
                                "page_number": (match.group("page_number") or "").strip() or None,
                                "line_numbers": (match.group("line_numbers") or "").strip() or None,
                                "content": match.group("content").strip(),
                            }
                        )
                    else:
                        quoted_clauses.append(
                            {
                                "clause_number": "",
                                "page_number": None,
                                "line_numbers": None,
                                "content": content,
                            }
                        )

            if key_points or quoted_clauses:
                return "\n".join(f"- {point}" for point in key_points), quoted_clauses

            logger.error("Plaintext fallback parsing also failed for key points/clauses extraction")
            return "Unable to extract key points", []

    except Exception as e:
        logger.error(f"Error extracting key points and clauses: {str(e)}")
        return "Unable to extract key points", []

# =========================
# Enhanced Context Retrieval
# =========================
async def get_enhanced_context(db, letter_id: str, current_user: CurrentUser) -> Dict[str, Any]:
    """
    Get comprehensive context including related letters in the same conversation and related documents.
    """
    try:
        logger.info(f"Getting enhanced context for letter: {letter_id}")

        # Target letter
        letter = await db.letters.find_one({"_id": ObjectId(letter_id)}) or await db.letters.find_one({"_id": letter_id})
        if not letter:
            logger.warning(f"Letter not found: {letter_id}")
            return {"conversation_history": [], "related_documents": []}

        # Conversation letters (if conversation_id present)
        conversation_filter: Dict[str, Any] = {}
        if letter.get("conversation_id"):
            conversation_filter["conversation_id"] = letter.get("conversation_id")
        else:
            # Fallback: same subject and org/project within a window
            conversation_filter = {
                "organization_id": letter.get("organization_id"),
                "project_id": letter.get("project_id"),
                "subject": {"$regex": letter.get("subject", "") or "", "$options": "i"},
            }

        conversation_letters = await db.letters.find(conversation_filter).sort("date", 1).limit(10).to_list(length=10)

        # Related documents via references, letter_no, or fuzzy subject match
        related_docs_query = {
            "$or": [
                {"references.documentId": str(letter_id)},
                {"letterNo": letter.get("letter_no") or letter.get("letterNo")},
                {"subject": {"$regex": letter.get("subject", "") or "", "$options": "i"}},
            ],
            "organization_id": str(letter.get("organization_id", "")),
        }
        # project scoping when present
        if letter.get("project_id"):
            related_docs_query["project_id"] = str(letter.get("project_id"))

        related_docs = await db.documents.find(related_docs_query).limit(5).to_list(length=5)

        logger.info(f"Found {len(conversation_letters)} conversation letters and {len(related_docs)} related documents")
        return {
            "conversation_history": conversation_letters,
            "related_documents": related_docs,
        }
    except Exception as e:
        logger.warning(f"get_enhanced_context failed: {e}")
        return {"conversation_history": [], "related_documents": []}

# =========================
# Enhanced Main Endpoint
# =========================
@router.post("/deep-planning/generate-draft", response_model=DeepPlanningResponse)
async def generate_deep_planning_draft(
    request: DeepPlanningRequest,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Generate a comprehensive letter draft with deep planning capabilities."""
    try:
        logger.info(f"Generating deep planning draft for subject: {request.subject}")

        # Authorization check
        org_id = request.organization_id or getattr(current_user, "organization_id", None)
        proj_id = request.project_id or (getattr(current_user, "projects", []) or [None])[0]
        authorize_scope(current_user, organization_id=org_id, project_id=proj_id)
        await PolicyService(db).authorize(
            current_user,
            "drafting.draft.create",
            resource_type="deep_planning",
            organization_id=org_id,
            project_id=proj_id,
        )

        # Validate document IDs
        await validate_document_ids(db, request.document_ids, current_user)

        # Extract content from driving documents (including OCR text and summary)
        document_context = await extract_document_content(db, request.document_ids)

        # Get vector store context if enabled and requested
        vector_store_context = None
        if request.use_vector_store and VECTOR_STORE_ENABLED:
            vector_store_context = await get_vector_store_context(
                db,
                f"{request.subject} {request.context or ''}",
                request.organization_id or current_user.organization_id,
                request.project_id
            )

        # Find similar letters for reference. `org_id` is the organisation the
        # gate just authorised: the request's, else the principal's - for Super
        # Admin that is the validated navbar selection, so a selected org bounds
        # the context and no selection keeps the consolidated view.
        similar_letters = await find_similar_letters(
            db,
            request.subject,
            org_id,
            request.project_id,
            current_user
        )

        # Extract key points and quoted clauses from document context
        key_points, quoted_clauses = await extract_key_points_and_clauses(document_context)

        # Enrich with target letter intelligence if a reply is being drafted
        target_letter_info = ""
        try:
            if request.target_letter_id:
                tl = None
                if ObjectId.is_valid(request.target_letter_id):
                    tl = await db.letters.find_one({"_id": ObjectId(request.target_letter_id)})
                if not tl:
                    tl = await db.letters.find_one({"_id": request.target_letter_id})
                if tl:
                    parts = []
                    # Summary
                    _summary = (tl.get("summary") or "").strip()
                    if _summary:
                        parts.append(f"Summary:\n{_summary}")
                    # Keywords
                    _kws = tl.get("keywords")
                    if _kws:
                        if isinstance(_kws, list):
                            kw_text = "\n".join([f"- {str(k).strip()}" for k in _kws if str(k).strip()])
                        else:
                            kw_text = str(_kws)
                        if kw_text.strip():
                            parts.append(f"Keywords:\n{kw_text}")
                    # Contractual clauses
                    _clauses = tl.get("contractual_clauses") or tl.get("contractualClauses")
                    if _clauses:
                        if isinstance(_clauses, list):
                            cl_text = "\n".join([f"- {str(c).strip()}" for c in _clauses if str(c).strip()])
                        else:
                            cl_text = str(_clauses)
                        if cl_text.strip():
                            parts.append(f"Contractual Clauses:\n{cl_text}")
                    # References
                    _refs = tl.get("reference") or tl.get("references")
                    if _refs:
                        if isinstance(_refs, list):
                            ref_text = "\n".join([f"- {str(r).strip()}" for r in _refs if str(r).strip()])
                        else:
                            ref_text = str(_refs)
                        if ref_text.strip():
                            parts.append(f"References:\n{ref_text}")
                    # OCR text (excerpts)
                    # `tl` is a db.letters record. No production code writes
                    # processing_status/duplicate_status/lifecycle_state to that
                    # collection, so a publication guard here reads fields that
                    # never exist and can never block - it looked like a gate
                    # and was not one. Letters are drafted correspondence, not
                    # extracted documents, so the extraction-authority policy
                    # genuinely does not apply; the honest form is to say so
                    # rather than call a predicate that always returns True.
                    _ocr = (tl.get("ocrText") or tl.get("ocr_text") or "").strip()
                    if _ocr:
                        truncated = _ocr[:1200] + (" ...(truncated)" if len(_ocr) > 1200 else "")
                        parts.append(f"OCR Text (excerpts):\n{truncated}")
                    if parts:
                        target_letter_info = "\n\n".join(parts)
        except Exception as e:
            logger.warning(f"Failed to enrich target letter intelligence: {e}")

        # Generate the actual draft
        draft_letter = await generate_draft_with_ai(
            subject=request.subject,
            recipient=request.recipient,
            document_context=document_context,
            user_context=request.context,
            points=request.points,
            similar_letters=similar_letters,
            target_letter_info=target_letter_info,
            vector_store_context=vector_store_context
        )

        # Generate structure summary
        structure_summary = f"Generated draft based on {len(request.document_ids)} driving documents and {len(similar_letters)} similar letters."
        if vector_store_context:
            structure_summary += " Enhanced with vector store context."

        # Store the generated draft in the database for future reference.
        # This is best-effort and must not block draft delivery.
        effective_org_id = request.organization_id or getattr(current_user, "organization_id", None)
        effective_proj_id = request.project_id or _effective_project_id(current_user)
        draft_data: Dict[str, Any] = {
            "subject": request.subject,
            "recipient": request.recipient,
            "content": draft_letter,
            "generated_by": current_user.id,
            "generated_at": datetime.now(),
            "document_ids_used": request.document_ids,
            "similar_letters_used": [letter["id"] for letter in similar_letters] if similar_letters else [],
            "organization_id": effective_org_id,
            "project_id": effective_proj_id,
            "used_vector_store": request.use_vector_store and VECTOR_STORE_ENABLED,
        }

        try:
            draft_embedding = await get_text_embedding(f"{request.subject} {draft_letter}")
            draft_data["embedding"] = draft_embedding
        except Exception as exc:
            logger.warning("Draft embedding generation skipped: %s", exc)

        try:
            drafts_collection = getattr(db, "ai_generated_drafts", None)
            if drafts_collection is not None:
                await drafts_collection.insert_one(draft_data)
        except Exception as exc:
            logger.warning("Persisting generated draft skipped: %s", exc)

        logger.info("Successfully generated deep planning draft")
        return DeepPlanningResponse(
            draft_letter=draft_letter,
            extracted_key_points=key_points,
            quoted_clauses=quoted_clauses,
            similar_letters=similar_letters,
            structure_summary=structure_summary
        )

    except (BaseDomainError, HTTPException):
        raise
    except Exception as e:
        logger.error(f"Error in deep planning draft generation: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Deep planning failed: {str(e)}")

# =========================
# Additional Endpoints
# =========================
@router.get("/deep-planning/history")
async def get_deep_planning_history(
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get history of deep planning generated drafts."""
    try:
        logger.info("Retrieving deep planning history")
        org_id = getattr(current_user, "organization_id", None)
        proj_id = (getattr(current_user, "projects", None) or [None])[0]
        await PolicyService(db).authorize(
            current_user,
            "drafting.request.view",
            resource_type="deep_planning_history",
            organization_id=org_id,
            project_id=proj_id,
        )

        query_filter: Dict[str, Any] = {"generated_by": current_user.id}

        # Authorization check
        if "superadmin" not in current_user.roles:
            if "orgadmin" in current_user.roles or "orguser" in current_user.roles:
                query_filter["organization_id"] = current_user.organization_id
            elif "projectadmin" in current_user.roles or "projectuser" in current_user.roles:
                if getattr(current_user, "projects", None):
                    query_filter["project_id"] = {"$in": [str(pid) for pid in current_user.projects]}
                else:
                    query_filter["project_id"] = "__none__"

        drafts_cursor = db.ai_generated_drafts.find(query_filter).sort("generated_at", -1).limit(20)
        drafts = await drafts_cursor.to_list(length=20)

        logger.info(f"Retrieved {len(drafts)} history items")
        return {"drafts": drafts}

    except (BaseDomainError, HTTPException):
        raise
    except Exception as e:
        logger.error(f"Error getting deep planning history: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to get history: {str(e)}")

@router.post("/deep-planning/analyze-document")
async def analyze_document(
    file: UploadFile = File(...),
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Analyze a document and extract key points and clauses."""
    try:
        logger.info(f"Analyzing document: {file.filename}")
        org_id = getattr(current_user, "organization_id", None)
        proj_id = (getattr(current_user, "projects", None) or [None])[0]
        await PolicyService(db).authorize(
            current_user,
            "dms.document.view",
            resource_type="deep_planning_analysis",
            organization_id=org_id,
            project_id=proj_id,
        )

        # Read file content, capped while reading. This had no application-level
        # size limit at all before R-A8S.
        content = await read_upload_within_limit(
            file, max(1, int(settings.GENERAL_UPLOAD_MAX_FILE_SIZE_MB)) * 1024 * 1024
        )
        text_content = content.decode('utf-8', errors='ignore')

        # Extract key points and clauses
        key_points, quoted_clauses = await extract_key_points_and_clauses(text_content)

        logger.info("Successfully analyzed document")
        return {
            "filename": file.filename,
            "key_points": key_points,
            "quoted_clauses": quoted_clauses
        }

    except (BaseDomainError, HTTPException):
        raise
    except Exception as e:
        logger.error(f"Error analyzing document: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Document analysis failed: {str(e)}")
