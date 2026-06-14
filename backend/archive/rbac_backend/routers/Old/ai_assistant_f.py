from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from ..core.database import get_db
from ..core.security import get_current_user, CurrentUser, authorize_scope
from ..core.config import settings
from typing import List, Optional, Dict, Any, Tuple, Set
from openai import OpenAI, RateLimitError, APIConnectionError, APIError
import logging
from datetime import datetime, timedelta
from pydantic import BaseModel, field_validator, ConfigDict
import numpy as np
import os
import re
from bson.objectid import ObjectId
import hashlib
import asyncio
import backoff
from functools import wraps

router = APIRouter()

# Configure Logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(name)s - %(message)s")
logger = logging.getLogger(__name__)

# =========================
# Configuration & Constants
# =========================
# Externalized configuration
AI_CONFIG = {
    "assistant_id": getattr(settings, "ASSISTANT_ID1", None),
    "responses_model": getattr(settings, "OPENAI_RESPONSES_MODEL", "gpt-4.1"),
    "assistants_model": getattr(settings, "OPENAI_ASSISTANTS_MODEL", "gpt-4.1"),
    "embeddings_model": getattr(settings, "OPENAI_EMBEDDINGS_MODEL", "text-embedding-3-small"),
    "max_poll_duration": getattr(settings, "OPENAI_MAX_POLL_DURATION", 300),  # 5 minutes
    "max_retries": getattr(settings, "OPENAI_MAX_RETRIES", 5),
    "rate_limit_per_minute": getattr(settings, "OPENAI_RATE_LIMIT_PER_MINUTE", 10),
}

# Externalized prompts
PROMPTS = {
    "contraclaim_instructions": """
    You are ContraClaim: draft new letters, reply to incoming letters, and review drafts.
    Maintain a formal contractual tone. When files are attached via file_search:
    - Extract main points, dates, references, context, issues, and tone.
    For replies: address each point, cite relevant prior correspondence by date/ID, keep dates/refs consistent.
    For reviews: list concrete edits and compliance gaps with clause mapping if available.
    Output text when asked, with headings and <ul><li> lists for assumptions/references.
    """,
    "contraclaim_user_prompt": """
    Use attached project correspondence (via file_search) to ensure factual & tonal consistency.
    Draft a clear, formal reply letter that:
     • references relevant prior letters when appropriate,
     • addresses each key point precisely,
     • keeps dates/refs consistent,
     • maintains professional tone.
    
    Subject: {subject}
    Recipient: {recipient}
    Context/Background: {context}
    Key points to address: {points}
    
    Return:
    1) A formal letter draft with date, subject line, salutation, body, and closing,
    2) A short bullet list of assumptions (if any),
    3) A list of referenced prior letters (with identifiers) drawn from the attached files.
    
    Output strictly in text format. Use headings for 'Assumptions' and 'Referenced prior letters', and format lists with <ul><li> items.
    """,
    "contraclaim_reply_prompt": """
    Use attached project documents (via file_search) — tender packs, GCC/SCC, ERs, SHE, prior correspondence —
    to ensure factual accuracy and correct clause/letter citations. Draft a formal response letter in text.
    
    Requirements:
    • Include date, subject line (prefix with 'Re:'), salutation, body, and closing.
    • Address each issue raised; cite clause numbers and prior letters by date/ID where applicable.
    • Keep dates/references consistent with the documents.
    • If any assumptions are needed, add an 'Assumptions' section using <ul><li>.
    
    Subject: {subject}
    
    Original letter content follows:
    {original_content}
    
    Additional context: {additional_context}
    Output strictly in text.
    """,
    "contraclaim_summary_prompt": """
    Summarize the following letter in 3–5 bullet points.
    Use attached project documents (via file_search) only if needed to confirm key dates/clauses,
    but keep the summary focused.
    Cover: purpose, obligations/entitlements, time/cost exposure, requested actions, deadlines.
    Output plain text bullets, each starting with '- '. No preamble or epilogue.
    
    Subject: {subject}
    
    Letter content:
    {original_content}
    """
}

# OpenAI client setup (New SDK v1+)
client = OpenAI(api_key=settings.OPENAI_API_KEY)

# =========================
# Rate Limiting & Backoff Decorators
# =========================
def openai_rate_limit(max_per_minute: int = AI_CONFIG["rate_limit_per_minute"]):
    """Rate limiting decorator for OpenAI API calls"""
    min_interval = 60.0 / max_per_minute
    last_call_time = 0
    
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            nonlocal last_call_time
            current_time = datetime.utcnow().timestamp()
            elapsed = current_time - last_call_time
            
            if elapsed < min_interval:
                await asyncio.sleep(min_interval - elapsed)
            
            last_call_time = datetime.utcnow().timestamp()
            return await func(*args, **kwargs)
        return wrapper
    return decorator

def openai_backoff(func):
    """Backoff decorator for OpenAI API calls with exponential retry"""
    @backoff.on_exception(
        backoff.expo,
        (RateLimitError, APIConnectionError, APIError),
        max_tries=AI_CONFIG["max_retries"],
        max_time=AI_CONFIG["max_poll_duration"],
        logger=logger
    )
    @wraps(func)
    async def wrapper(*args, **kwargs):
        return await func(*args, **kwargs)
    return wrapper

# =========================
# OpenAI Client Wrappers with Rate Limiting & Backoff
# =========================
@openai_rate_limit()
@openai_backoff
async def openai_embeddings_create(text: str) -> List[float]:
    """Get OpenAI text embedding with rate limiting and backoff"""
    try:
        response = client.embeddings.create(
            model=AI_CONFIG["embeddings_model"],
            input=text
        )
        return response.data[0].embedding
    except Exception as e:
        logger.error(f"Error getting embedding: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to generate embedding: {str(e)}")

@openai_rate_limit()
@openai_backoff
async def openai_assistants_create(**kwargs):
    """Create assistant with rate limiting and backoff"""
    return client.assistants.create(**kwargs)

@openai_rate_limit()
@openai_backoff
async def openai_assistants_update(assistant_id, **kwargs):
    """Update assistant with rate limiting and backoff"""
    return client.assistants.update(assistant_id, **kwargs)

@openai_rate_limit()
@openai_backoff
async def openai_vector_stores_create(**kwargs):
    """Create vector store with rate limiting and backoff"""
    return client.vector_stores.create(**kwargs)

@openai_rate_limit()
@openai_backoff
async def openai_files_create(**kwargs):
    """Create file with rate limiting and backoff"""
    return client.files.create(**kwargs)

@openai_rate_limit()
@openai_backoff
async def openai_vector_stores_file_batches_upload(vector_store_id, **kwargs):
    """Upload files to vector store with rate limiting and backoff"""
    return client.vector_stores.file_batches.upload_and_poll(vector_store_id, **kwargs)

@openai_rate_limit()
@openai_backoff
async def openai_threads_create(**kwargs):
    """Create thread with rate limiting and backoff"""
    return client.threads.create(**kwargs)

@openai_rate_limit()
@openai_backoff
async def openai_threads_runs_create(**kwargs):
    """Create run with rate limiting and backoff"""
    return client.threads.runs.create(**kwargs)

@openai_rate_limit()
@openai_backoff
async def openai_threads_runs_retrieve(**kwargs):
    """Retrieve run with rate limiting and backoff"""
    return client.threads.runs.retrieve(**kwargs)

@openai_rate_limit()
@openai_backoff
async def openai_threads_messages_list(**kwargs):
    """List messages with rate limiting and backoff"""
    return client.threads.messages.list(**kwargs)

@openai_rate_limit()
@openai_backoff
async def openai_responses_create(**kwargs):
    """Create response with rate limiting and backoff"""
    return client.responses.create(**kwargs)

# =========================
# Pydantic Models (unchanged)
# =========================
class LetterSearchRequest(BaseModel):
    query: str
    limit: Optional[int] = 3

class SimilarLetter(BaseModel):
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
            return ""
        if isinstance(v, datetime):
            return v.isoformat()
        if isinstance(v, str):
            return v
        try:
            return str(v)
        except Exception:
            return ""

class LetterDraftRequest(BaseModel):
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

class LetterDraftResponse(BaseModel):
    draft_letter: str
    similar_letters: List[SimilarLetter]
    structure_summary: str

class VectorSearchResponse(BaseModel):
    similar_letters: List[SimilarLetter]
    query_embedding: List[float]

# =========================
# Embeddings / Similarity (updated)
# =========================
async def get_text_embedding(text: str) -> List[float]:
    """Get OpenAI text embedding for the given text."""
    return await openai_embeddings_create(text)

def cosine_similarity(a: List[float], b: List[float]) -> float:
    """Calculate cosine similarity between two vectors."""
    a_np = np.array(a, dtype=np.float32)
    b_np = np.array(b, dtype=np.float32)
    denom = float(np.linalg.norm(a_np) * np.linalg.norm(b_np))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a_np, b_np) / denom)

# =========================
# Search in existing letters (updated logging)
# =========================
@router.post("/ai-assistant/search-letters", response_model=VectorSearchResponse)
async def search_similar_letters(
    request: LetterSearchRequest,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Search for semantically similar letters using vector embeddings."""
    try:
        logger.info(f"Searching for letters similar to: {request.query}")

        # Get embedding for the search query
        query_embedding = await get_text_embedding(request.query)

        # Authorization check - only get letters user has access to
        query_filter: Dict[str, Any] = {}
        if "superadmin" not in current_user.roles:
            if "orgadmin" in current_user.roles or "orguser" in current_user.roles:
                query_filter["organization_id"] = current_user.organization_id
            elif "projectadmin" in current_user.roles or "projectuser" in current_user.roles:
                if getattr(current_user, "projects", None):
                    query_filter["project_id"] = {"$in": [str(pid) for pid in current_user.projects]}
                else:
                    query_filter["project_id"] = "__none__"

        # Get letters from the letters collection
        letters_cursor = db.letters.find(query_filter)
        letters = await letters_cursor.to_list(length=None)

        similar_letters: List[SimilarLetter] = []

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
                similar_letters.append(SimilarLetter(
                    id=str(letter["_id"]),
                    title=letter.get("title", ""),
                    subject=letter.get("subject", ""),
                    content=letter.get("content", ""),
                    recipient=letter.get("recipient", ""),
                    similarity_score=similarity,
                    created_at=(letter.get("created_at") or datetime.utcnow().isoformat())
                ))

        # Sort by similarity score and limit results
        similar_letters.sort(key=lambda x: x.similarity_score, reverse=True)
        similar_letters = similar_letters[: (request.limit or 3)]

        logger.info(f"Found {len(similar_letters)} similar letters")

        return VectorSearchResponse(
            similar_letters=similar_letters,
            query_embedding=query_embedding
        )

    except Exception as e:
        logger.error(f"Error in search_similar_letters: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

# =========================
# ContraClaim Assistants + RAG wiring (updated)
# =========================
async def ensure_assistant() -> str:
    """Return an assistant_id; reuse settings.ASSISTANT_ID if present, else create."""
    global AI_CONFIG
    if AI_CONFIG["assistant_id"]:
        return AI_CONFIG["assistant_id"]
    
    a = await openai_assistants_create(
        name="ContraClaim",
        model=AI_CONFIG["assistants_model"],
        instructions=PROMPTS["contraclaim_instructions"],
        tools=[{"type": "file_search"}],
    )
    AI_CONFIG["assistant_id"] = a.id
    return AI_CONFIG["assistant_id"]

async def ensure_vector_store_for_context(db, current_user) -> Tuple[str, str]:
    """Create or reuse a Vector Store for the user's organization/project combo."""
    org_id = str(current_user.organization_id)
    eff_proj = _effective_project_id(current_user)
    proj_id = str(eff_proj or "none")
    key = f"org:{org_id}:proj:{proj_id}"

    mapping = await db.ai_vector_stores.find_one({"key": key})
    if mapping and mapping.get("vector_store_id"):
        return mapping["vector_store_id"], mapping.get("name", key)

    vs = await openai_vector_stores_create(name=f"ContraClaim-{org_id}-{proj_id}")
    await db.ai_vector_stores.update_one(
        {"key": key},
        {"$set": {"key": key, "name": vs.name, "vector_store_id": vs.id}},
        upsert=True,
    )
    return vs.id, vs.name

async def upload_files_to_vector_store(filepaths: List[str], vector_store_id: str, db=None, current_user: Optional[CurrentUser]=None) -> Dict[str, Any]:
    """
    De-dup by SHA256; store registry in ai_files.
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
            if db:
                exists = await db.ai_files.find_one({"sha256": sha, "vector_store_id": vector_store_id})
            if exists and exists.get("file_id"):
                skipped.append({"path": p, "file_id": exists["file_id"]})
                continue

            f = await openai_files_create(file=open(p, "rb"), purpose="assistants")
            file_ids.append(f.id)
            uploaded.append({"path": p, "file_id": f.id})

            if db:
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
        await openai_vector_stores_file_batches_upload(vector_store_id, file_ids=file_ids)

    return {"uploaded": uploaded, "skipped": skipped, "failed": failed}

def build_contra_user_prompt_html(
    subject: str,
    recipient: str,
    context: Optional[str],
    points: Optional[str],
    structure_analysis: str,
    target_letter_content: Optional[str],
    attachments_overview_text: str,
    extra_attachments_text: Optional[str],
) -> str:
    """Build user prompt using externalized template"""
    base = PROMPTS["contraclaim_user_prompt"].format(
        subject=subject,
        recipient=recipient,
        context=context or 'None provided',
        points=points or 'None provided'
    )
    
    if structure_analysis:
        base += f"\nAnalysis of organizational letter structure and tone:\n{structure_analysis}\n"
    if attachments_overview_text:
        base += "\nAttachment overview (for reference only):\n" + attachments_overview_text + "\n"
    if target_letter_content:
        base += "\nTarget letter context (for reference only):\n" + target_letter_content + "\n"
    if extra_attachments_text:
        base += "\nAdditional inline correspondence excerpts:\n" + extra_attachments_text + "\n"
    return base

def build_contra_reply_prompt_html(
    subject: str,
    original_content: str,
    additional_context: Optional[str]
) -> str:
    """Build reply prompt using externalized template"""
    return PROMPTS["contraclaim_reply_prompt"].format(
        subject=subject or 'N/A',
        original_content=original_content or '[No content provided]',
        additional_context=additional_context or 'None'
    )

def build_contra_summary_prompt(
    subject: str,
    original_content: str
) -> str:
    """Build summary prompt using externalized template"""
    return PROMPTS["contraclaim_summary_prompt"].format(
        subject=subject or 'N/A',
        original_content=original_content or '[No content provided]'
    )

# =========================
# Refactored Assistant Polling with Exponential Backoff
# =========================
async def poll_assistant_run(thread_id: str, run_id: str, max_duration: int = AI_CONFIG["max_poll_duration"]) -> Any:
    """
    Poll assistant run with exponential backoff and max duration
    """
    start_time = datetime.utcnow()
    poll_count = 0
    base_delay = 0.5  # Start with 500ms
    
    while True:
        # Check if we've exceeded max duration
        if (datetime.utcnow() - start_time).total_seconds() > max_duration:
            raise TimeoutError(f"Assistant run exceeded maximum poll duration of {max_duration} seconds")
        
        # Get run status with backoff and rate limiting
        r = await openai_threads_runs_retrieve(thread_id=thread_id, run_id=run_id)
        poll_count += 1
        
        logger.debug(f"Poll #{poll_count}, Status: {r.status}")
        
        if r.status in ("completed", "failed", "cancelled", "expired"):
            return r
        
        # Exponential backoff with jitter
        delay = base_delay * (2 ** poll_count) + (random.random() * 0.1)
        await asyncio.sleep(min(delay, 10))  # Cap at 10 seconds

async def run_contraclaim_with_rag(db, current_user: "CurrentUser", user_message_html: str, org_project_files: List[str]) -> str:
    """Run ContraClaim with RAG - refactored with better error handling and logging"""
    logger.info(f"Starting RAG flow with {len(org_project_files)} project files")
    
    a_id = await ensure_assistant()
    logger.debug(f"Assistant ID: {a_id}")
    
    vs_id, vs_name = await ensure_vector_store_for_context(db, current_user)
    logger.debug(f"Vector store ID: {vs_id}, Name: {vs_name}")

    # Upload files to vector store
    logger.debug("Uploading files to vector store...")
    upload_result = await upload_files_to_vector_store(org_project_files, vs_id, db=db, current_user=current_user)
    logger.debug(f"Upload result: {upload_result}")

    # Attach vector store to assistant
    logger.debug("Attaching vector store to assistant...")
    await openai_assistants_update(a_id, tool_resources={"file_search": {"vector_store_ids": [vs_id]}})
    logger.debug("Vector store attached successfully")

    # Create thread and run
    logger.debug("Creating OpenAI thread...")
    thread = await openai_threads_create(messages=[{"role": "user", "content": user_message_html}])
    logger.debug(f"Thread created: {thread.id}")
    
    logger.debug("Starting assistant run...")
    run = await openai_threads_runs_create(thread_id=thread.id, assistant_id=a_id)
    logger.debug(f"Run created: {run.id}, Status: {run.status}")

    # Poll run with exponential backoff
    try:
        final_run = await poll_assistant_run(thread.id, run.id)
        logger.debug(f"Final run status: {final_run.status}")
        
        if final_run.status != "completed":
            logger.error(f"Run failed with status: {final_run.status}")
            if hasattr(final_run, 'last_error') and final_run.last_error:
                logger.error(f"Last error: {final_run.last_error}")
            raise RuntimeError(f"Assistant run did not complete: {final_run.status}")

        # Retrieve messages
        logger.debug("Retrieving messages from thread...")
        msgs = await openai_threads_messages_list(thread_id=thread.id)
        logger.debug(f"Found {len(msgs.data)} messages")
        
        for i, m in enumerate(msgs.data):
            logger.debug(f"Message {i}: Role={m.role}, Content blocks={len(m.content)}")
            if m.role == "assistant":
                parts = []
                for j, c in enumerate(m.content):
                    logger.debug(f"Content block {j}: Type={c.type}")
                    if c.type == "text":
                        text_content = c.text.value
                        logger.debug(f"Text content length: {len(text_content)}")
                        parts.append(text_content)
                html = "\n".join(parts).strip()
                if html:
                    logger.info(f"Returning assistant response, length: {len(html)}")
                    return html
        
        logger.error("No assistant response text found!")
        raise RuntimeError("No assistant response text found")
    
    except TimeoutError as e:
        logger.error(f"Assistant run timed out: {e}")
        raise HTTPException(status_code=504, detail="Assistant run timed out")
    except Exception as e:
        logger.error(f"Assistant run failed: {e}")
        raise

# =========================
# Refactored Draft Generation Functions
# =========================
async def prepare_draft_context(db, request: LetterDraftRequest, current_user: CurrentUser, target_letter_doc: Optional[dict] = None) -> Tuple[str, str]:
    """Prepare context for draft generation - refactored from generate_letter_draft"""
    try:
        # Determine effective org/project ids for filtering
        vect_org_id = getattr(request, "organization_id", None) or getattr(request, "organizationId", None) or getattr(current_user, "organization_id", None)
        vect_proj_id = getattr(request, "project_id", None) or getattr(request, "projectId", None) or _effective_project_id(current_user)
        logger.debug(f"Vector filter org={vect_org_id}, proj={vect_proj_id}")

        # Build a semantic query from user inputs and target letter content
        target_letter_content = ""
        target_letter_refs = []
        
        if target_letter_doc:
            target_letter_refs = target_letter_doc.get("references", []) or []
            target_letter_content = (
                "This is a reply to the following letter:\n\n"
                f"Subject: {target_letter_doc.get('subject', '')}\n\n"
                f"Content:\n{target_letter_doc.get('content', '')}"
            )

        search_text_parts = [
            request.subject or "",
            request.context or "",
            request.points or "",
            target_letter_content or "",
        ]
        search_text = "\n".join([p for p in search_text_parts if p]).strip()
        
        # Only attempt embedding if we have org scope
        vector_excerpts = ""
        if vect_org_id:
            q_embed = await get_text_embedding(search_text or (request.subject or ""))
            
            # Semantically similar chunks
            top_chunks = await fetch_top_vector_chunks_for_query(
                db=db,
                organization_id=str(vect_org_id),
                project_id=str(vect_proj_id) if vect_proj_id else None,
                query_embedding=q_embed,
                max_rows=2000,
                top_docs=5,
                chunks_per_doc=2,
            )
            
            # Chunks from referenced letters
            ref_chunks = await fetch_reference_chunks(
                db=db,
                target_letter_refs=target_letter_refs,
                vect_org_id=vect_org_id,
                vect_proj_id=vect_proj_id
            )

            # Dedupe and build excerpts text
            vector_excerpts = await process_vector_chunks(top_chunks + ref_chunks)
        
        return vector_excerpts, target_letter_content
        
    except Exception as e:
        logger.warning(f"Vector context preparation failed: {e}")
        return "", ""

async def fetch_reference_chunks(db, target_letter_refs: List[Dict[str, Any]], vect_org_id: str, vect_proj_id: str) -> List[Dict[str, Any]]:
    """Fetch chunks from referenced letters"""
    ref_chunks = []
    try:
        for ref in target_letter_refs or []:
            rtype = (ref.get("type") or "").lower()
            if rtype == "internal" and ref.get("letter_id"):
                rc = await fetch_chunks_for_internal_letter(
                    db=db,
                    letter_id=str(ref.get("letter_id")),
                    organization_id=str(vect_org_id),
                    project_id=str(vect_proj_id) if vect_proj_id else None,
                    max_chunks=4,
                )
                ref_chunks.extend(rc or [])
            elif rtype == "external" and ref.get("letter_no"):
                rc = await fetch_chunks_by_letter_no(
                    db=db,
                    organization_id=str(vect_org_id),
                    project_id=str(vect_proj_id) if vect_proj_id else None,
                    letter_no=str(ref.get("letter_no")),
                    max_chunks=4,
                )
                ref_chunks.extend(rc or [])
    except Exception as re:
        logger.warning(f"Failed fetching reference chunks: {re}")
    
    return ref_chunks

async def process_vector_chunks(chunks: List[Dict[str, Any]], max_chunks: int = 12) -> str:
    """Process and format vector chunks for prompt"""
    # Dedupe by checksum/text
    seen_keys = set()
    
    def chunk_key(c: Dict[str, Any]) -> str:
        return c.get("checksum") or (c.get("text", "")[:64] + str(c.get("chunk_index")))
    
    ordered = []
    for c in chunks:
        k = chunk_key(c)
        if k in seen_keys:
            continue
        seen_keys.add(k)
        ordered.append(c)
    
    # Build excerpts text
    parts = []
    for c in ordered[:max_chunks]:
        header = f"[Ref: {c.get('uploadType','?')} | LetterNo: {c.get('letterNo') or '-'} | Doc: {c.get('document_id') or '-'} | Chunk: {c.get('chunk_index')}]"
        # Keep chunk text reasonably bounded
        t = c.get("text", "")
        if len(t) > 1800:
            t = t[:1800] + "...(truncated)"
        parts.append(f"{header}\n{t}")
    
    return "\n\n".join(parts) if parts else ""

# =========================
# Draft endpoint (refactored)
# =========================
@router.post("/ai-assistant/generate-draft", response_model=LetterDraftResponse)
async def generate_letter_draft(
    request: LetterDraftRequest,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Generate a professional letter draft using AI + RAG based on similar letters and project docs."""
    try:
        logger.info(f"Generating draft for subject: {request.subject}, recipient: {request.recipient}")

        # Only use similar letters if provided by the frontend; do not auto-search
        similar_letters = request.similar_letters or []
        if not similar_letters:
            logger.debug("No similar letters provided; skipping search.")

        # Analyze structure and tone from similar letters
        structure_analysis = analyze_letter_structure(similar_letters) if similar_letters else ""
        logger.debug(f"Structure analysis: {structure_analysis[:100]}...")

        # Fetch the target letter content if a reply is being drafted
        target_letter_doc = None
        if request.target_letter_id:
            logger.debug(f"Fetching target letter: {request.target_letter_id}")
            try:
                target_letter_doc = await db.letters.find_one({"_id": ObjectId(request.target_letter_id)}) or \
                                    await db.letters.find_one({"_id": request.target_letter_id})
            except Exception as e:
                logger.error(f"Could not fetch target letter: {e}")

        # Prepare context for draft generation
        extra_attachments, target_letter_content = await prepare_draft_context(
            db, request, current_user, target_letter_doc
        )

        # Enrich points with any input_requests tied to the target letter
        effective_points = await enrich_points_with_input_requests(
            db, request, request.points or ""
        )

        # Generate draft with AI
        prepared = await generate_draft_with_ai(
            subject=request.subject,
            recipient=request.recipient,
            similar_letters=similar_letters,
            context=request.context,
            points=effective_points,
            structure_analysis=structure_analysis,
            target_letter_content=target_letter_content,
            extra_attachments=extra_attachments
        )

        # Get project files for RAG
        org_project_files = await get_project_files_for_rag(request, current_user, db)
        logger.debug(f"Found {len(org_project_files)} project files")

        # Execute with OpenAI
        if prepared.startswith("__ASSISTANTS_USER_HTML__"):
            user_message_html = prepared.split("__ASSISTANTS_USER_HTML__", 1)[1].strip()
            logger.debug(f"User message HTML length: {len(user_message_html)}")
            
            # Try Responses API first, fall back to Assistants API
            try:
                draft_content = await try_responses_api(user_message_html, org_project_files)
            except Exception as e:
                logger.warning(f"Responses API failed, falling back to Assistants: {e}")
                try:
                    draft_content = await run_contraclaim_with_rag(
                        db=db,
                        current_user=current_user,
                        user_message_html=user_message_html,
                        org_project_files=org_project_files
                    )
                except Exception as e2:
                    logger.error(f"Assistants API also failed: {e2}")
                    draft_content = generate_fallback_draft(request.subject, request.recipient)
        else:
            draft_content = prepared

        # Store the generated draft in the database
        await store_generated_draft(db, request, current_user, draft_content, similar_letters)

        logger.info("Draft generated successfully")
        return LetterDraftResponse(
            draft_letter=draft_content,
            similar_letters=similar_letters or [],
            structure_summary=structure_analysis
        )

    except Exception as e:
        logger.error(f"Error in generate_letter_draft: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Draft generation failed: {str(e)}")

# =========================
# Additional Helper Functions
# =========================
async def enrich_points_with_input_requests(db, request: LetterDraftRequest, effective_points: str) -> str:
    """Enrich points with input requests data"""
    try:
        target_id_for_ir = getattr(request, "target_letter_id", None)
        if target_id_for_ir:
            ir_points = await collect_input_requests_points(db, str(target_id_for_ir))
            if ir_points:
                if effective_points:
                    effective_points = f"{effective_points}\n{ir_points}"
                else:
                    effective_points = ir_points
    except Exception as e:
        logger.warning(f"Failed to append input request points: {e}")
    
    return effective_points

async def get_project_files_for_rag(request: LetterDraftRequest, current_user: CurrentUser, db) -> List[str]:
    """Get project files for RAG processing"""
    org_short = await get_org_short_name(request, current_user, db)
    project_short = await get_project_short_name(request, current_user, db)
    
    base_dir = os.path.join("uploads", org_short, project_short)
    org_project_files = []
    
    if os.path.isdir(base_dir):
        for fn in os.listdir(base_dir):
            if fn.lower().endswith((".pdf",)):  # Only PDFs for now
                org_project_files.append(os.path.join(base_dir, fn))
    
    return org_project_files

async def try_responses_api(user_message_html: str, org_project_files: List[str]) -> str:
    """Try to use the Responses API with fallback"""
    file_ids = []
    
    for p in org_project_files:
        try:
            if os.path.isfile(p):
                f = await openai_files_create(file=open(p, "rb"), purpose="assistants")
                file_ids.append(f.id)
        except Exception as fe:
            logger.warning(f"Failed to upload file {p}: {fe}")
    
    content_blocks = [{"type": "input_text", "text": user_message_html}]
    for fid in file_ids:
        content_blocks.append({"type": "input_file", "file_id": fid})
    
    resp = await openai_responses_create(
        model=AI_CONFIG["responses_model"],
        input=[{"role": "user", "content": content_blocks}],
    )
    
    if hasattr(resp, "output_text") and resp.output_text:
        return resp.output_text
    else:
        return str(resp)

async def store_generated_draft(db, request: LetterDraftRequest, current_user: CurrentUser, draft_content: str, similar_letters: List[SimilarLetter]):
    """Store the generated draft in the database"""
    effective_org_id = getattr(request, "organization_id", None) or getattr(current_user, "organization_id", None)
    effective_proj_id = getattr(request, "project_id", None) or _effective_project_id(current_user)
    
    draft_data = {
        "subject": request.subject,
        "recipient": request.recipient,
        "content": draft_content,
        "generated_by": current_user.id,
        "generated_at": datetime.utcnow(),
        "similar_letters_used": [letter.id for letter in similar_letters] if similar_letters else [],
        "organization_id": effective_org_id,
        "project_id": effective_proj_id
    }
    
    # Generate embedding for the draft
    draft_embedding = await get_text_embedding(f"{request.subject} {draft_content}")
    draft_data["embedding"] = draft_embedding
    
    await db.ai_generated_drafts.insert_one(draft_data)

# =========================
# The rest of the code remains largely the same with logging improvements
# =========================