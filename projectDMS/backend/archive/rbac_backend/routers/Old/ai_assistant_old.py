
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from ..core.database import get_db
from ..core.security import get_current_user, CurrentUser, authorize_scope
from ..core.config import settings
from typing import List, Optional, Dict, Any, Tuple, Set
from openai import OpenAI
import logging
from datetime import datetime, timedelta
from pydantic import BaseModel, field_validator, ConfigDict
import numpy as np
import os
import re
from bson.objectid import ObjectId
import hashlib
import asyncio

router = APIRouter()

# Configure Logger
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# OpenAI client setup (New SDK v1+)
client = OpenAI(api_key=settings.OPENAI_API_KEY)
assistant_id = getattr(settings, "ASSISTANT_ID1", None)
OPENAI_RESPONSES_MODEL = getattr(settings, "OPENAI_RESPONSES_MODEL", "gpt-4.1")

# =========================
# Pydantic Models
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
    # Accept extra keys (e.g., camelCase from FE) so we can read them via getattr if provided
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
# Embeddings / Similarity
# =========================
async def get_text_embedding(text: str) -> List[float]:
    """Get OpenAI text embedding for the given text."""
    try:
        response = client.embeddings.create(
            model="text-embedding-3-small",
            input=text
        )
        return response.data[0].embedding
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



# =========================
# Search in existing letters
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
                # restrict to any of user's assigned projects
                if getattr(current_user, "projects", None):
                    query_filter["project_id"] = {"$in": [str(pid) for pid in current_user.projects]}
                else:
                    # No project assignments -> no results
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
                    created_at=(letter.get("created_at") or datetime.now().isoformat())
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
# ContraClaim Assistants + RAG wiring
# =========================
ASSISTANTS_MODEL = getattr(settings, "OPENAI_ASSISTANTS_MODEL", "gpt-4.1")  # or "gpt-4.1"

CONTRACLAIM_DEFAULT_INSTRUCTIONS = """
You are ContraClaim: draft new letters, reply to incoming letters, and review drafts.
Maintain a formal contractual tone. When files are attached via file_search:
- Extract main points, dates, references, context, issues, and tone.
For replies: address each point, cite relevant prior correspondence by date/ID, keep dates/refs consistent.
For reviews: list concrete edits and compliance gaps with clause mapping if available.
Output text when asked, with headings and <ul><li> lists for assumptions/references.
"""

async def ensure_assistant() -> str:
    """Return an assistant_id; reuse settings.ASSISTANT_ID if present, else create."""
    global assistant_id
    if assistant_id:
        return assistant_id
    a = client.assistants.create(
        name="ContraClaim",
        model=ASSISTANTS_MODEL,
        instructions=CONTRACLAIM_DEFAULT_INSTRUCTIONS,
        tools=[{"type": "file_search"}],
    )
    assistant_id = a.id
    return assistant_id

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

async def upload_files_to_vector_store(filepaths: List[str], vector_store_id: str, db=None, current_user: Optional[CurrentUser]=None) -> Dict[str, Any]:
    """
    De-dup by SHA256; store registry in ai_files:
      { sha256, path, file_id, vector_store_id, org_id, project_id, created_at }
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

            f = client.files.create(file=open(p, "rb"), purpose="assistants")
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
        client.vector_stores.file_batches.upload_and_poll(vector_store_id, file_ids=file_ids)

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
    base = (
        "Use attached project correspondence (via file_search) to ensure factual & tonal consistency.\n"
        "Draft a clear, formal reply letter that:\n"
        " • references relevant prior letters when appropriate,\n"
        " • addresses each key point precisely,\n"
        " • keeps dates/refs consistent,\n"
        " • maintains professional tone.\n\n"
        f"Subject: {subject}\n"
        f"Recipient: {recipient}\n"
        f"Context/Background: {context or 'None provided'}\n"
        f"Key points to address: {points or 'None provided'}\n\n"
        "Return:\n"
        "1) A formal letter draft with date, subject line, salutation, body, and closing,\n"
        "2) A short bullet list of assumptions (if any),\n"
        "3) A list of referenced prior letters (with identifiers) drawn from the attached files.\n"
        "\nOutput strictly in text format. Use headings for 'Assumptions' and 'Referenced prior letters', and format lists with <ul><li> items.\n"
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

# ---- New prompt builders for response & summary ----
def build_contra_reply_prompt_html(
    subject: str,
    original_content: str,
    additional_context: Optional[str]
) -> str:
    """
    Ask ContraClaim to draft a formal response letter, using file_search to ground facts/clauses.
    """
    return (
        "Use attached project documents (via file_search) — tender packs, GCC/SCC, ERs, SHE, prior correspondence — "
        "to ensure factual accuracy and correct clause/letter citations. Draft a formal response letter in text.\n\n"
        "Requirements:\n"
        "• Include date, subject line (prefix with 'Re:'), salutation, body, and closing.\n"
        "• Address each issue raised; cite clause numbers and prior letters by date/ID where applicable.\n"
        "• Keep dates/references consistent with the documents.\n"
        "• If any assumptions are needed, add an 'Assumptions' section using <ul><li>.\n\n"
        f"Subject: {subject or 'N/A'}\n\n"
        "Original letter content follows:\n"
        f"{original_content or '[No content provided]'}\n\n"
        f"Additional context: {additional_context or 'None'}\n"
        "Output strictly in text.\n"
    )

def build_contra_summary_prompt(
    subject: str,
    original_content: str
) -> str:
    """
    Ask ContraClaim to summarize a letter, optionally grounding with file_search for dates/clauses.
    Output plain-text bullets (not HTML) to be drop-in compatible with your endpoint contract.
    """
    return (
        "Summarize the following letter in 3–5 bullet points.\n"
        "Use attached project documents (via file_search) only if needed to confirm key dates/clauses, "
        "but keep the summary focused.\n"
        "Cover: purpose, obligations/entitlements, time/cost exposure, requested actions, deadlines.\n"
        "Output plain text bullets, each starting with '- '. No preamble or epilogue.\n\n"
        f"Subject: {subject or 'N/A'}\n\n"
        "Letter content:\n"
        f"{original_content or '[No content provided]'}\n"
    )


async def get_relevant_project_files(current_user: CurrentUser, db) -> List[str]:
    """
    Get project files relevant to the current user's organization and project
    """
    # Get organization and project details
    org_short = None
    project_short = None
    
    # Try to get organization short name
    try:
        org_doc = await db.organizations.find_one({"_id": ObjectId(current_user.organization_id)})
        if org_doc:
            org_name = org_doc.get("name") or org_doc.get("title") or None
            org_short = make_short_name(org_name)
    except Exception:
        pass
    
    # Try to get project short name
    try:
        project_id = _effective_project_id(current_user)
        if project_id:
            proj_doc = await db.projects.find_one({"_id": ObjectId(project_id)})
            if proj_doc:
                proj_name = proj_doc.get("name") or proj_doc.get("title") or None
                project_short = make_short_name(proj_name)
    except Exception:
        pass
    
    # Build the path to project files
    if org_short and project_short:
        base_dir = os.path.join("uploads", org_short, project_short)
        if os.path.isdir(base_dir):
            # Get all relevant files (PDFs, Word docs, text files)
            relevant_files = []
            for fn in os.listdir(base_dir):
                if fn.lower().endswith((".pdf", ".docx", ".txt", ".xlsx")):
                    full_path = os.path.join(base_dir, fn)
                    relevant_files.append(full_path)
            return relevant_files
    
    return []


async def run_contraclaim_with_rag(db, current_user: "CurrentUser", user_message_html: str, org_project_files: List[str]) -> str:
    print(f"DEBUG: Starting RAG flow with {len(org_project_files)} project files")
    print(f"DEBUG: Message length: {len(user_message_html)}")
    
    a_id = await ensure_assistant()
    print(f"DEBUG: Assistant ID: {a_id}")
    
    vs_id, vs_name = await ensure_vector_store_for_context(db, current_user)
    print(f"DEBUG: Vector store ID: {vs_id}, Name: {vs_name}")

    # upload + dedupe
    print(f"DEBUG: Uploading files to vector store...")
    upload_result = await upload_files_to_vector_store(org_project_files, vs_id, db=db, current_user=current_user)
    print(f"DEBUG: Upload result: {upload_result}")

    # attach VS to assistant
    print(f"DEBUG: Attaching vector store to assistant...")
    client.assistants.update(a_id, tool_resources={"file_search": {"vector_store_ids": [vs_id]}})
    print(f"DEBUG: Vector store attached successfully")

    # create thread → run → poll (non-blocking sleep)
    print(f"DEBUG: Creating OpenAI thread...")
    thread = client.threads.create(messages=[{"role": "user", "content": user_message_html}])
    print(f"DEBUG: Thread created: {thread.id}")
    
    print(f"DEBUG: Starting assistant run...")
    run = client.threads.runs.create(thread_id=thread.id, assistant_id=a_id)
    print(f"DEBUG: Run created: {run.id}, Status: {run.status}")

    poll_count = 0
    while True:
        r = client.threads.runs.retrieve(thread_id=thread.id, run_id=run.id)
        poll_count += 1
        print(f"DEBUG: Poll #{poll_count}, Status: {r.status}")
        
        if r.status in ("completed", "failed", "cancelled", "expired"):
            break
        await asyncio.sleep(0.9)

    print(f"DEBUG: Final run status: {r.status}")
    if r.status != "completed":
        print(f"DEBUG: Run failed with status: {r.status}")
        if hasattr(r, 'last_error') and r.last_error:
            print(f"DEBUG: Last error: {r.last_error}")
        raise RuntimeError(f"Assistant run did not complete: {r.status}")

    print(f"DEBUG: Retrieving messages from thread...")
    msgs = client.threads.messages.list(thread_id=thread.id)
    print(f"DEBUG: Found {len(msgs.data)} messages")
    
    for i, m in enumerate(msgs.data):
        print(f"DEBUG: Message {i}: Role={m.role}, Content blocks={len(m.content)}")
        if m.role == "assistant":
            parts = []
            for j, c in enumerate(m.content):
                print(f"DEBUG: Content block {j}: Type={c.type}")
                if c.type == "text":
                    text_content = c.text.value
                    print(f"DEBUG: Text content length: {len(text_content)}")
                    print(f"DEBUG: Text preview: {text_content[:200]}...")
                    parts.append(text_content)
            html = "\n".join(parts).strip()
            if html:
                print(f"DEBUG: Returning assistant response, length: {len(html)}")
                return html
    
    print(f"DEBUG: No assistant response text found!")
    raise RuntimeError("No assistant response text found")



# =========================
# Draft generation (prepares prompt)
# =========================
def analyze_letter_structure(similar_letters: List[SimilarLetter]) -> str:
    """Analyze the structure and tone of similar letters."""
    if not similar_letters:
        return "No similar letters found for analysis."

    analysis = "Based on similar letters in your organization:\n\n"

    # Analyze common patterns
    common_openings: List[str] = []
    common_closings: List[str] = []
    tone_indicators: List[str] = []

    for letter in similar_letters:
        content = (letter.content or "").lower()

        # Extract opening patterns
        if content.startswith("dear"):
            common_openings.append("Formal greeting with 'Dear Sir'")
        elif "greetings" in content[:100]:
            common_openings.append("Professional greeting")

        # Extract closing patterns
        if "sincerely" in content[-200:]:
            common_closings.append("Formal closing with 'Sincerely'")
        elif "best regards" in content[-200:]:
            common_closings.append("Professional closing with 'Best Regards'")

        # Analyze tone
        if any(word in content for word in ["please", "kindly", "appreciate"]):
            tone_indicators.append("Polite and courteous")
        if any(word in content for word in ["urgent", "immediate", "asap"]):
            tone_indicators.append("Urgent tone")

    if common_openings:
        analysis += f"• Common openings: {', '.join(set(common_openings))}\n"
    if common_closings:
        analysis += f"• Common closings: {', '.join(set(common_closings))}\n"
    if tone_indicators:
        analysis += f"• Typical tone: {', '.join(set(tone_indicators))}\n"

    analysis += f"\n• Structure follows organizational standards with {len(similar_letters)} similar letters as reference."

    return analysis

def make_short_name(value: Optional[str]) -> str:
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

def read_text_if_exists(path: str) -> Optional[str]:
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            return f.read()
    except Exception as e:
        logger.warning(f"Could not read attachment file {path}: {e}")
        return None

# =========================
# Compatibility wrappers for vector helpers
# These map the public helper names used by generate_letter_draft
# to the underscore-prefixed implementations defined below.
# =========================
async def fetch_top_vector_chunks_for_query(
    db,
    organization_id: str,
    project_id: Optional[str],
    query_embedding: List[float],
    max_rows: int = 2000,
    top_docs: int = 5,
    chunks_per_doc: int = 2,
) -> List[Dict[str, Any]]:
    return await _fetch_vector_chunks_for_query(
        db=db,
        org_id=str(organization_id) if organization_id is not None else None,
        proj_id=str(project_id) if project_id is not None else None,
        query_embedding=query_embedding,
        max_scan=max_rows,
        top_docs=top_docs,
        chunks_per_doc=chunks_per_doc,
    )

async def fetch_chunks_by_letter_no(
    db,
    organization_id: str,
    project_id: Optional[str],
    letter_no: str,
    max_chunks: int = 6,
) -> List[Dict[str, Any]]:
    return await _fetch_chunks_by_letter_no(
        db=db,
        org_id=str(organization_id) if organization_id is not None else None,
        proj_id=str(project_id) if project_id is not None else None,
        letter_no=str(letter_no),
        max_chunks=max_chunks,
    )

async def fetch_chunks_for_internal_letter(
    db,
    letter_id: str,
    organization_id: str,
    project_id: Optional[str],
    max_chunks: int = 6,
) -> List[Dict[str, Any]]:
    return await _fetch_chunks_for_internal_letter(
        db=db,
        letter_id=str(letter_id),
        org_id=str(organization_id) if organization_id is not None else None,
        proj_id=str(project_id) if project_id is not None else None,
        max_chunks=max_chunks,
    )









async def collect_letter_numbers(db, similar_letters: List[SimilarLetter], target_letter_doc: Optional[dict]) -> List[str]:
    """
    Resolve a set of letter numbers (letter_no) from:
    - similar letters (by id -> letters collection)
    - the target letter itself
    - the target letter's internal/external references
    """
    letter_nos = set()
    try:
        # From similar letters
        for sl in similar_letters or []:
            try:
                doc = await db.letters.find_one({"_id": ObjectId(sl.id)}) or await db.letters.find_one({"_id": sl.id})
                if doc and doc.get("letter_no"):
                    letter_nos.add(str(doc.get("letter_no")))
            except Exception:
                continue

        # Target letter itself
        try:
            if target_letter_doc and target_letter_doc.get("letter_no"):
                letter_nos.add(str(target_letter_doc.get("letter_no")))
        except Exception:
            pass

        # References from target letter
        try:
            if target_letter_doc and isinstance(target_letter_doc.get("references"), list):
                for ref in target_letter_doc.get("references"):
                    rtype = ref.get("type")
                    if rtype == "internal" and ref.get("letter_id"):
                        try:
                            ref_doc = await db.letters.find_one({"_id": ObjectId(ref.get("letter_id"))}) or await db.letters.find_one({"_id": ref.get("letter_id")})
                            if ref_doc and ref_doc.get("letter_no"):
                                letter_nos.add(str(ref_doc.get("letter_no")))
                        except Exception:
                            continue
                    elif rtype == "external" and ref.get("letter_no"):
                        letter_nos.add(str(ref.get("letter_no")))
        except Exception:
            pass
    except Exception:
        pass
    return list(letter_nos)

# =========================
# Vector retrieval helpers (Mongo document_vectors)
# =========================
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
    Returns a list of {document_id, text, score, chunk_index, uploadType, letterNo, createdAt}.
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


async def _fetch_chunks_by_letter_no(
    db,
    org_id: str,
    proj_id: Optional[str],
    letter_no: str,
    max_chunks: int = 6,
) -> List[Dict[str, Any]]:
    """
    For a given letter number, find documents in documents collection (scoped to org/proj)
    and return up to max_chunks chunks from document_vectors for those documents.
    """
    try:
        f_doc: Dict[str, Any] = {
            "organization_id": str(org_id),
            "letterNo": str(letter_no),
        }
        if proj_id is not None:
            f_doc["project_id"] = str(proj_id)

        docs = await db.documents.find(f_doc).limit(5).to_list(length=5)
        if not docs:
            return []

        chunks: List[Dict[str, Any]] = []
        for d in docs:
            did = str(d.get("_id"))
            f_vec = {"document_id": did}
            # Also enforce org/proj to be extra safe
            f_vec["organization_id"] = str(org_id)
            if proj_id is not None:
                f_vec["project_id"] = str(proj_id)

            vecs = await db.document_vectors.find(f_vec).sort("chunk_index", 1).limit(max_chunks).to_list(length=max_chunks)
            for v in vecs:
                chunks.append({
                    "document_id": str(v.get("document_id")),
                    "text": v.get("text", ""),
                    "score": None,  # not similarity-ranked here
                    "chunk_index": int(v.get("chunk_index", 0)),
                    "uploadType": v.get("uploadType", ""),
                    "letterNo": v.get("letterNo"),
                    "createdAt": v.get("createdAt"),
                })

        return chunks[:max_chunks]
    except Exception as e:
        logger.warning(f"Fetch by letter_no failed: {e}")
        return []


async def _fetch_chunks_for_internal_letter(
    db,
    letter_id: str,
    org_id: Optional[str],
    proj_id: Optional[str],
    max_chunks: int = 6,
) -> List[Dict[str, Any]]:
    """
    Resolve internal letter by ID, then fetch chunks by its letter_no within org/proj.
    """
    try:
        lt = None
        try:
            lt = await db.letters.find_one({"_id": ObjectId(letter_id)})
        except Exception:
            lt = await db.letters.find_one({"_id": letter_id})
        if not lt:
            return []

        letter_no = lt.get("letter_no")
        eff_org = str(org_id or lt.get("organization_id") or "")
        eff_proj = str(proj_id or lt.get("project_id") or "") if (proj_id or lt.get("project_id")) else None
        if not letter_no or not eff_org:
            return []

        return await _fetch_chunks_by_letter_no(
            db=db,
            org_id=eff_org,
            proj_id=eff_proj,
            letter_no=str(letter_no),
            max_chunks=max_chunks,
        )
    except Exception as e:
        logger.warning(f"Fetch internal letter chunks failed: {e}")
        return []


def _format_vector_chunks_for_prompt(chunks: List[Dict[str, Any]]) -> str:
    """
    Format vector chunks into a readable section for the user prompt.
    """
    if not chunks:
        return ""
    # If 'score' present, sort desc
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
        body = (c.get("text") or "").strip()
        if body:
            # Lightweight trimming to avoid oversized payloads
            if len(body) > 1800:
                body = body[:1800] + " ..."
            lines.append(header)
            lines.append(body)
            lines.append("")  # spacer
    return "\n".join(lines).strip()


async def generate_draft_with_ai(
    subject: str,
    recipient: str,
    similar_letters: List[SimilarLetter],
    context: Optional[str] = None,
    points: Optional[str] = None,
    structure_analysis: str = "",
    target_letter_content: Optional[str] = None,
    extra_attachments: Optional[str] = None
) -> str:
    """
    PREPARES the user message (HTML request) for ContraClaim Assistants flow.
    The endpoint will execute the Assistants+RAG run where db/current_user are available.
    """
    attachments_overview = ""
    if similar_letters:
        attachments_overview = "Similar letters referenced (IDs/Subjects):\n"
        for i, letter in enumerate(similar_letters, 1):
            date_str = letter.created_at if letter.created_at else ""
            attachments_overview += f"{i}) ID: {letter.id} | Subject: {letter.subject} | Recipient: {letter.recipient} | Date: {date_str}\n"

    user_msg_html = build_contra_user_prompt_html(
        subject=subject,
        recipient=recipient,
        context=context,
        points=points,
        structure_analysis=structure_analysis,
        target_letter_content=target_letter_content or "",
        attachments_overview_text=attachments_overview,
        extra_attachments_text=extra_attachments or ""
    )

    # Special marker so caller knows to run Assistants flow
    return f"__ASSISTANTS_USER_HTML__\n{user_msg_html}"

def generate_fallback_draft(subject: str, recipient: str) -> str:
    """Generate a basic fallback draft when AI is unavailable."""
    return f"""Date: {datetime.now().strftime('%Y-%m-%d')}

Subject: {subject or 'N/A'}

Dear {recipient or 'Sir/Madam'},

I hope this letter finds you well.

I am writing to you regarding {subject or 'the above matter'}. [Please provide specific details about your request or communication here.]

We would appreciate your attention to this matter and look forward to your response.

Thank you for your time and consideration.

Sincerely,
[Your Name]
[Your Title]
[Organization Name]"""

# =========================
# Input Requests → Points aggregation (used in drafting)
# =========================
async def collect_input_requests_points(db, letter_id: str) -> str:
    """
    Collect key points (and responses when present) from input_requests for a given letter_id,
    to be appended into the 'points' section for drafting.
    """
    try:
        pts_lines = []
        cursor = db.input_requests.find({"letter_id": letter_id}).sort("created_at", 1)
        items = await cursor.to_list(length=None)
        for ir in items:
            kp = (ir.get("key_points") or "").strip()
            details = (ir.get("details") or "").strip()
            resp = (ir.get("response") or "").strip()
            status = (ir.get("status") or "").strip().lower()

            # Prefer key_points text as the line item; fallback to details
            base = kp if kp else details
            if not base:
                continue

            line = f"- {base}"
            if resp:
                line += f" | Response: {resp}"
            elif status and status != "closed":
                line += " | Response: pending"
            pts_lines.append(line)

        return "\n".join(pts_lines)
    except Exception as e:
        logger.warning(f"Failed to collect input request points for letter {letter_id}: {e}")
        return ""

# =========================
# Draft endpoint (executes Assistants + RAG)
# =========================
@router.post("/ai-assistant/generate-draft", response_model=LetterDraftResponse)
async def generate_letter_draft(
    request: LetterDraftRequest,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Generate a professional letter draft using AI + RAG based on similar letters and project docs."""
    try:
        print(f"\n=== AI DRAFT GENERATION START ===")
        print(f"DEBUG: Request received - Subject: '{request.subject}', Recipient: '{request.recipient}'")
        print(f"DEBUG: User ID: {current_user.id}, Org ID: {current_user.organization_id}")
        print(f"DEBUG: Context: {request.context}")
        print(f"DEBUG: Points: {request.points}")
        
        logger.info(f"Generating draft for subject: {request.subject}, recipient: {request.recipient}")

        # Only use similar letters if provided by the frontend; do not auto-search
        similar_letters = request.similar_letters or []
        if not similar_letters:
            print(f"DEBUG: No similar letters provided; skipping search. Frontend must call /ai-assistant/search-letters explicitly to supply similar letters.")

        # Analyze structure and tone from similar letters
        structure_analysis = ""
        if similar_letters:
            structure_analysis = analyze_letter_structure(similar_letters)
            print(f"DEBUG: Structure analysis: {structure_analysis[:100]}...")

        # Fetch the target letter content if a reply is being drafted
        target_letter_content = ""
        target_letter_refs: List[Dict[str, Any]] = []
        target_letter_doc: Optional[Dict[str, Any]] = None
        if request.target_letter_id:
            print(f"DEBUG: Fetching target letter: {request.target_letter_id}")
            try:
                target_letter = await db.letters.find_one({"_id": ObjectId(request.target_letter_id)}) or \
                                await db.letters.find_one({"_id": request.target_letter_id})
                if target_letter:
                    target_letter_doc = target_letter
                    target_letter_refs = target_letter.get("references", []) or []
                    target_letter_content = (
                        "This is a reply to the following letter:\n\n"
                        f"Subject: {target_letter.get('subject', '')}\n\n"
                        f"Content:\n{target_letter.get('content', '')}"
                    )
                    print(f"DEBUG: Target letter content length: {len(target_letter_content)}")
                    print(f"DEBUG: Target letter references count: {len(target_letter_refs)}")
            except Exception as e:
                print(f"DEBUG: Error fetching target letter: {e}")
                logger.error(f"Could not fetch target letter: {e}")

        # Build context from document_vectors (org/project scoped), include referenced letters; fallback to filesystem if none
        extra_attachments = ""
        try:
            print(f"DEBUG: Building vector-based context from document_vectors...")

            # Determine effective org/project ids for filtering
            vect_org_id = getattr(request, "organization_id", None) or getattr(request, "organizationId", None) or getattr(current_user, "organization_id", None)
            vect_proj_id = getattr(request, "project_id", None) or getattr(request, "projectId", None) or _effective_project_id(current_user)
            print(f"DEBUG: Vector filter org={vect_org_id}, proj={vect_proj_id}")

            # Build a semantic query from user inputs and target letter content
            search_text_parts = [
                request.subject or "",
                request.context or "",
                request.points or "",
                target_letter_content or "",
            ]
            search_text = "\n".join([p for p in search_text_parts if p]).strip()
            query_embedding = await get_text_embedding(search_text or (request.subject or ""))
            print(f"DEBUG: Query embedding generated for vector search")

            # Fetch top chunks semantically similar within org/project
        except Exception as e:
            print(f"DEBUG: Error preparing vector-based context preamble: {e}")
            logger.warning(f"Vector preamble failed: {e}")

        # Prepare prompt (with vector-based context preferred)
        print(f"DEBUG: Preparing AI prompt...")

        try:
            vect_org_id = getattr(request, "organization_id", None) or getattr(request, "organizationId", None) or getattr(current_user, "organization_id", None)
            vect_proj_id = getattr(request, "project_id", None) or getattr(request, "projectId", None) or _effective_project_id(current_user)

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
                ref_chunks: List[Dict[str, Any]] = []
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

                # Dedupe by checksum/text
                seen_keys = set()
                def _key(c: Dict[str, Any]) -> str:
                    return c.get("checksum") or (c.get("text", "")[:64] + str(c.get("chunk_index")))
                ordered: List[Dict[str, Any]] = []
                for c in (ref_chunks + top_chunks):
                    k = _key(c)
                    if k in seen_keys:
                        continue
                    seen_keys.add(k)
                    ordered.append(c)

                # Build excerpts text
                parts: List[str] = []
                for c in ordered[:12]:
                    header = f"[Ref: {c.get('uploadType','?')} | LetterNo: {c.get('letterNo') or '-'} | Doc: {c.get('document_id') or '-'} | Chunk: {c.get('chunk_index')}]"
                    # keep chunk text reasonably bounded
                    t = c.get("text", "")
                    if len(t) > 1800:
                        t = t[:1800] + "...(truncated)"
                    parts.append(f"{header}\n{t}")
                if parts:
                    vector_excerpts = "\n\n".join(parts)

            # Prefer vector content; fall back to filesystem-based extra_attachments already prepared above
            if vector_excerpts:
                print(f"DEBUG: Using vector-based excerpts for attachments (len={len(vector_excerpts)})")
                extra_attachments = vector_excerpts
            else:
                print(f"DEBUG: No vector excerpts found; keeping filesystem-derived attachments (len={len(extra_attachments)})")
        except Exception as ve:
            logger.warning(f"Vector prompt augmentation failed: {ve}")

        # Enrich points with any input_requests tied to the target letter
        effective_points = request.points or ""
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
        print(f"DEBUG: Prepared prompt length: {len(prepared)}")
        print(f"DEBUG: Prompt starts with ASSISTANTS marker: {prepared.startswith('__ASSISTANTS_USER_HTML__')}")

        # Collect all project docs (separate tender packs) from uploads/<org>/<proj>
        provided_org_short = getattr(request, "org_short", None)
        if provided_org_short:
            org_short = make_short_name(provided_org_short)
        else:
            provided_org_name = getattr(request, "org_name", None)
            if provided_org_name:
                org_short = make_short_name(provided_org_name)
            else:
                org_name = None
                try:
                    req_org_id = getattr(request, "organization_id", None) or getattr(request, "organizationId", None) or getattr(current_user, "organization_id", None)
                    org_doc = None
                    if req_org_id:
                        org_doc = await db.organizations.find_one({"_id": req_org_id}) or await db.organizations.find_one({"_id": ObjectId(req_org_id)})
                    if org_doc:
                        org_name = org_doc.get("name") or org_doc.get("title") or None
                except Exception as e:
                    logger.warning(f"Could not resolve organization name: {e}")
                org_short = make_short_name(org_name)

        provided_project_short = getattr(request, "project_short", None)
        if provided_project_short:
            project_short = make_short_name(provided_project_short)
        else:
            provided_project_name = getattr(request, "project_name", None)
            if provided_project_name:
                project_short = make_short_name(provided_project_name)
            else:
                proj_name = None
                project_id = getattr(request, "project_id", None) or getattr(request, "projectId", None) or _effective_project_id(current_user)
                if project_id:
                    try:
                        proj_doc = await db.projects.find_one({"_id": project_id}) or await db.projects.find_one({"_id": ObjectId(project_id)})
                        if proj_doc:
                            proj_name = proj_doc.get("name") or proj_doc.get("title") or None
                    except Exception as e:
                        logger.warning(f"Could not resolve project name: {e}")
                project_short = make_short_name(proj_name)

        base_dir = os.path.join("uploads", org_short, project_short)
        org_project_files = []
        if os.path.isdir(base_dir):
            for fn in os.listdir(base_dir):
                if fn.lower().endswith((".pdf")): # ".docx", ".txt"
                    org_project_files.append(os.path.join(base_dir, fn))
        
        print(f"DEBUG: Found {len(org_project_files)} project files: {org_project_files}")

        # Execute with OpenAI Responses API (gpt-5) using incoming/outgoing as attachments
        if prepared.startswith("__ASSISTANTS_USER_HTML__"):
            print(f"DEBUG: Using OpenAI Assistants API flow...")
            user_message_html = prepared.split("__ASSISTANTS_USER_HTML__", 1)[1].strip()
            print(f"DEBUG: User message HTML length: {len(user_message_html)}")
            print(f"DEBUG: User message preview: {user_message_html[:200]}...")
            
            # Build attachments from incoming/outgoing (.pdf) if present (support both case variants)
            file_ids = []
            try:
                print(f"DEBUG: Preparing file uploads for OpenAI...")
                incoming_candidates = [os.path.join(base_dir, "incoming.pdf"), os.path.join(base_dir, "Incoming.pdf")]
                outgoing_candidates = [os.path.join(base_dir, "outgoing.pdf"), os.path.join(base_dir, "Outgoing.pdf")]
                paths = []
                inc = next((p for p in incoming_candidates if os.path.isfile(p)), None)
                out = next((p for p in outgoing_candidates if os.path.isfile(p)), None)
                if inc: 
                    paths.append(inc)
                    print(f"DEBUG: Will upload incoming file: {inc}")
                if out: 
                    paths.append(out)
                    print(f"DEBUG: Will upload outgoing file: {out}")
                    
                for p in paths:
                    try:
                        print(f"DEBUG: Uploading file to OpenAI: {p}")
                        f = client.files.create(file=open(p, "rb"), purpose="assistants")
                        file_ids.append(f.id)
                        print(f"DEBUG: File uploaded successfully, ID: {f.id}")
                    except Exception as fe:
                        print(f"DEBUG: Failed to upload file {p}: {fe}")
                        logger.warning(f"Failed to upload attachment {p}: {fe}")
            except Exception as e:
                print(f"DEBUG: Attachment preparation failed: {e}")
                logger.warning(f"Attachment preparation failed: {e}")

            # Compose input content
            content_blocks = [{"type": "input_text", "text": user_message_html}]
            for fid in file_ids:
                content_blocks.append({"type": "input_file", "file_id": fid})
            
            print(f"DEBUG: Content blocks prepared: {len(content_blocks)} blocks")
            print(f"DEBUG: File IDs attached: {file_ids}")

            try:
                print(f"DEBUG: Calling OpenAI Responses API with model: {OPENAI_RESPONSES_MODEL}")
                resp = client.responses.create(
                    model=OPENAI_RESPONSES_MODEL,
                    input=[{"role": "user", "content": content_blocks}],
                )
                print(f"DEBUG: OpenAI Responses API call successful")
                print(f"DEBUG: Response type: {type(resp)}")
                print(f"DEBUG: Response has output_text: {hasattr(resp, 'output_text')}")
                
                if hasattr(resp, "output_text") and resp.output_text:
                    draft_content = resp.output_text
                    print(f"DEBUG: Got output_text, length: {len(draft_content)}")
                    print(f"DEBUG: Output preview: {draft_content[:200]}...")
                else:
                    # Fallback stringify
                    draft_content = str(resp)
                    print(f"DEBUG: Using fallback stringify, length: {len(draft_content)}")
                    print(f"DEBUG: Stringify preview: {draft_content[:200]}...")
            except Exception as e:
                print(f"DEBUG: Responses API failed: {e}")
                logger.error(f"Responses API failed; falling back to Assistants. Reason: {e}")
                try:
                    print(f"DEBUG: Falling back to Assistants API...")
                    draft_content = await run_contraclaim_with_rag(
                        db=db,
                        current_user=current_user,
                        user_message_html=user_message_html,
                        org_project_files=org_project_files
                    )
                    print(f"DEBUG: Assistants API successful, content length: {len(draft_content)}")
                    print(f"DEBUG: Assistants output preview: {draft_content[:200]}...")
                except Exception as e2:
                    print(f"DEBUG: Assistants API also failed: {e2}")
                    logger.error(f"Assistants run failed; using fallback. Reason: {e2}")
                    draft_content = generate_fallback_draft(request.subject, request.recipient)
                    print(f"DEBUG: Using fallback draft, length: {len(draft_content)}")
        else:
            print(f"DEBUG: Using prepared content directly (no AI call)")
            draft_content = prepared

        print(f"DEBUG: Final draft content length: {len(draft_content)}")
        print(f"DEBUG: Final draft preview: {draft_content[:300]}...")

        # Store the generated draft in the database for future reference
        effective_org_id = getattr(request, "organization_id", None) or getattr(current_user, "organization_id", None)
        effective_proj_id = getattr(request, "project_id", None) or _effective_project_id(current_user)
        draft_data: Dict[str, Any] = {
            "subject": request.subject,
            "recipient": request.recipient,
            "content": draft_content,
            "generated_by": current_user.id,
            "generated_at": datetime.now(),
            "similar_letters_used": [letter.id for letter in similar_letters] if similar_letters else [],
            "organization_id": effective_org_id,
            "project_id": effective_proj_id
        }

        # Generate embedding for the draft
        print(f"DEBUG: Generating embedding for draft...")
        draft_embedding = await get_text_embedding(f"{request.subject} {draft_content}")
        draft_data["embedding"] = draft_embedding
        print(f"DEBUG: Embedding generated, length: {len(draft_embedding)}")

        print(f"DEBUG: Storing draft in database...")
        await db.ai_generated_drafts.insert_one(draft_data)

        logger.info("Draft generated successfully")
        print(f"=== AI DRAFT GENERATION COMPLETE ===\n")

        return LetterDraftResponse(
            draft_letter=draft_content,
            similar_letters=similar_letters or [],
            structure_summary=structure_analysis
        )

    except Exception as e:
        print(f"DEBUG: ERROR in generate_letter_draft: {str(e)}")
        print(f"DEBUG: Error type: {type(e)}")
        import traceback
        print(f"DEBUG: Traceback: {traceback.format_exc()}")
        logger.error(f"Error in generate_letter_draft: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Draft generation failed: {str(e)}")

# =========================
# RAG: upload/list/remove endpoints
# =========================
class UploadToRAGRequest(BaseModel):
    files: List[str]  # absolute/relative server paths

@router.post("/ai-assistant/rag/upload")
async def upload_to_rag(
    request: UploadToRAGRequest,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    try:
        vs_id, vs_name = await ensure_vector_store_for_context(db, current_user)
        result = await upload_files_to_vector_store(request.files, vs_id, db=db, current_user=current_user)
        a_id = await ensure_assistant()
        client.assistants.update(a_id, tool_resources={"file_search": {"vector_store_ids": [vs_id]}})
        return {"vector_store_id": vs_id, "name": vs_name, "result": result}
    except Exception as e:
        logger.error(f"Upload to RAG failed: {e}")
        raise HTTPException(status_code=500, detail=f"Upload to RAG failed: {str(e)}")

@router.post("/ai-assistant/rag/upload-multipart")
async def upload_multipart_to_rag(
    files: List[UploadFile] = File(...),
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    try:
        # resolve org/project short names
        org_name = None
        org_doc = await db.organizations.find_one({"_id": current_user.organization_id}) or \
                  await db.organizations.find_one({"_id": ObjectId(current_user.organization_id)})
        if org_doc:
            org_name = org_doc.get("name") or org_doc.get("title") or None
        org_short = make_short_name(org_name)

        proj_name = None
        project_id = getattr(current_user, 'project_id', None)
        if project_id:
            proj_doc = await db.projects.find_one({"_id": project_id}) or \
                       await db.projects.find_one({"_id": ObjectId(project_id)})
            if proj_doc:
                proj_name = proj_doc.get("name") or proj_doc.get("title") or None
        project_short = make_short_name(proj_name)

        base_dir = os.path.join("uploads", org_short, project_short)
        os.makedirs(base_dir, exist_ok=True)

        saved_paths = []
        for f in files:
            dest = os.path.join(base_dir, f.filename)
            with open(dest, "wb") as out:
                out.write(await f.read())
            saved_paths.append(dest)

        vs_id, vs_name = await ensure_vector_store_for_context(db, current_user)
        result = await upload_files_to_vector_store(saved_paths, vs_id, db=db, current_user=current_user)

        a_id = await ensure_assistant()
        client.assistants.update(a_id, tool_resources={"file_search": {"vector_store_ids": [vs_id]}})

        return {"saved": saved_paths, "vector_store_id": vs_id, "name": vs_name, "result": result}
    except Exception as e:
        logger.error(f"Multipart upload to RAG failed: {e}")
        raise HTTPException(status_code=500, detail=f"Multipart upload to RAG failed: {str(e)}")

@router.get("/ai-assistant/rag/list")
async def list_project_rag_files(
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    try:
        vs_id, _ = await ensure_vector_store_for_context(db, current_user)
        files = await db.ai_files.find({"vector_store_id": vs_id}).sort("created_at", -1).to_list(length=200)
        return {"vector_store_id": vs_id, "files": files}
    except Exception as e:
        logger.error(f"List RAG files failed: {e}")
        raise HTTPException(status_code=500, detail=f"List RAG files failed: {str(e)}")

class RemoveRAGFileRequest(BaseModel):
    file_id: str

@router.post("/ai-assistant/rag/remove")
async def remove_project_rag_file(
    request: RemoveRAGFileRequest,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    try:
        vs_id, _ = await ensure_vector_store_for_context(db, current_user)
        client.vector_stores.files.delete(vector_store_id=vs_id, file_id=request.file_id)
        try:
            client.files.delete(request.file_id)
        except Exception:
            pass
        await db.ai_files.delete_many({"vector_store_id": vs_id, "file_id": request.file_id})
        return {"ok": True, "file_id": request.file_id}
    except Exception as e:
        logger.error(f"Remove RAG file failed: {e}")
        raise HTTPException(status_code=500, detail=f"Remove RAG file failed: {str(e)}")

# =========================
# History / Aliases
# =========================
@router.get("/ai-assistant/letter-history")
async def get_ai_letter_history(
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Get history of AI-generated letter drafts."""
    try:
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

        return {"drafts": drafts}

    except Exception as e:
        logger.error(f"Error getting AI letter history: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Failed to get history: {str(e)}")

# Alias endpoint to match frontend expectation
@router.post("/ai-assistant/enhanced-draft", response_model=LetterDraftResponse)
async def enhanced_draft(
    request: LetterDraftRequest,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    return await generate_letter_draft(request, db, current_user)

# =========================
# Lightweight extract-metadata (stub)
# =========================
class ExtractedMetadata(BaseModel):
    filename: str
    date: Optional[str] = None
    letterNo: Optional[str] = None
    from_: Optional[str] = None
    to: Optional[str] = None
    subject: Optional[str] = None
    references: List[str] = []
    summary: Optional[str] = None

@router.post("/ai-assistant/extract-metadata")
async def extract_metadata_endpoint(
    file: UploadFile = File(...),
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    try:
        # Lightweight acknowledgement — full pipeline can be wired later.
        return ExtractedMetadata(
            filename=file.filename,
            summary="File received. Automated metadata extraction pipeline not yet enabled."
        )
    except Exception as e:
        logger.error(f"Error extracting metadata: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to extract metadata")

# =========================
# Aliases for frontend hooks
# =========================
@router.post("/ai-assistant/search-similar-letters", response_model=VectorSearchResponse)
async def search_similar_letters_alias(
    request: LetterSearchRequest,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    return await search_similar_letters(request, db, current_user)

@router.post("/ai-assistant/generate-enhanced-draft", response_model=LetterDraftResponse)
async def generate_enhanced_draft_alias(
    request: LetterDraftRequest,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    return await generate_letter_draft(request, db, current_user)

# =========================
# Response & Summarize — now Assistants + file_search (RAG)
# =========================
class GenerateResponseRequest(BaseModel):
    letter_id: str
    context: Optional[str] = None

@router.post("/ai-assistant/generate-response")
async def generate_response_endpoint(
    request: GenerateResponseRequest,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Generate a suggested response to a letter using Assistants + file_search (RAG).
    Falls back to a simple HTML template on error.
    """
    try:
        # Load source letter
        letter = None
        try:
            letter = await db.letters.find_one({"_id": ObjectId(request.letter_id)})
        except Exception:
            letter = await db.letters.find_one({"_id": request.letter_id})

        subject = letter.get("subject", "") if letter else ""
        content = letter.get("content", "") if letter else ""

        # Build org/project uploads dir and gather files (tender packs, etc.)
        org_name = None
        try:
            org_doc = await db.organizations.find_one({"_id": current_user.organization_id}) or \
                      await db.organizations.find_one({"_id": ObjectId(current_user.organization_id)})
            if org_doc:
                org_name = org_doc.get("name") or org_doc.get("title") or None
        except Exception as e:
            logger.warning(f"Could not resolve organization name: {e}")
        org_short = make_short_name(org_name)

        proj_name = None
        project_id = _effective_project_id(current_user)
        if project_id:
            try:
                proj_doc = await db.projects.find_one({"_id": project_id}) or \
                           await db.projects.find_one({"_id": ObjectId(project_id)})
                if proj_doc:
                    proj_name = proj_doc.get("name") or proj_doc.get("title") or None
            except Exception as e:
                logger.warning(f"Could not resolve project name: {e}")
        project_short = make_short_name(proj_name)

        base_dir = os.path.join("uploads", org_short, project_short)
        org_project_files: List[str] = []
        if os.path.isdir(base_dir):
            for fn in os.listdir(base_dir):
                if fn.lower().endswith((".pdf")): # , ".docx", ".txt"
                    org_project_files.append(os.path.join(base_dir, fn))

        # Compose user message for ContraClaim (HTML response letter)
        user_message_html = build_contra_reply_prompt_html(
            subject=subject,
            original_content=content,
            additional_context=request.context
        )

        try:
            html = await run_contraclaim_with_rag(
                db=db,
                current_user=current_user,
                user_message_html=user_message_html,
                org_project_files=org_project_files
            )
            return {"response": html}
        except Exception as e:
            logger.error(f"Assistants run (generate-response) failed: {e}")
            # Fallback simple HTML
            text = (
                f"""<p>Dear Sir/Madam,</p>
<p>Thank you for your letter regarding "{subject}". We have reviewed the contents and will provide the necessary actions.</p>
<p>{('Context: ' + request.context) if request.context else ''}</p>
<p>Please let us know if any further information is required.</p>
<p>Sincerely,<br/>[Your Name]<br/>[Organization]</p>"""
            )
            return {"response": text}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating response: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate response")


# ===== Enhanced Context Retrieval for context-aware replies =====
async def get_enhanced_context(db, letter_id: str, current_user: CurrentUser) -> Dict[str, Any]:
    """
    Get comprehensive context including related letters in the same conversation and related documents.
    """
    try:
        # Target letter
        letter = await db.letters.find_one({"_id": ObjectId(letter_id)}) or await db.letters.find_one({"_id": letter_id})
        if not letter:
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

        return {
            "conversation_history": conversation_letters,
            "related_documents": related_docs,
        }
    except Exception as e:
        logger.warning(f"get_enhanced_context failed: {e}")
        return {"conversation_history": [], "related_documents": []}


# ===== Improved Prompt Engineering for replies =====
def build_enhanced_reply_prompt(original_letter: Dict[str, Any], context: Dict[str, Any], additional_instructions: str = "") -> str:
    """
    Build a comprehensive prompt that includes conversation-aware context and related document counts.
    """
    if original_letter is None:
        original_letter = {}
    subject = original_letter.get("subject", "")
    date = original_letter.get("date", "")
    content = original_letter.get("content", "")

    conv_len = len(context.get("conversation_history", []) or [])
    rel_docs_len = len(context.get("related_documents", []) or [])

    return f"""
You are drafting a formal reply to the following letter. Use the provided context to maintain consistency.

ORIGINAL LETTER:
Subject: {subject}
Date: {date}
Content: {content}

CONTEXT:
- Conversation History: {conv_len} previous letters in this thread
- Related Documents: {rel_docs_len} relevant documents
- Project Specifications and contracts: Available via file_search

INSTRUCTIONS:
1. Address all points raised in the original letter.
2. Maintain formal tone and professional language.
3. Reference relevant contract clauses where appropriate.
4. Follow organizational formatting standards.
5. {additional_instructions}

Respond with the complete drafted letter only.
""".strip()


# ===== Cross-Reference Validation =====
async def validate_references(letter_data: Dict[str, Any], db) -> List[str]:
    """
    Validate all references in a letter exist and are correct.
    Returns a list of validation error messages (empty if valid).
    """
    invalid_refs: List[str] = []
    try:
        refs = letter_data.get("references", []) or []
        for ref in refs:
            rtype = (ref.get("type") or "").lower()
            if rtype == "internal" and ref.get("letter_id"):
                try:
                    exists = await db.letters.find_one({"_id": ObjectId(ref.get("letter_id"))}) or \
                             await db.letters.find_one({"_id": ref.get("letter_id")})
                    if not exists:
                        invalid_refs.append(f"Internal reference {ref.get('letter_id')} not found")
                except Exception:
                    invalid_refs.append(f"Internal reference {ref.get('letter_id')} invalid")
            # TODO: external references validation (e.g., verify external letter_no format or registry)
    except Exception as e:
        logger.warning(f"validate_references failed: {e}")
    return invalid_refs


# ===== Conversation-Aware Response Generation Endpoint =====
@router.post("/ai-assistant/generate-context-aware-response")
async def generate_context_aware_response(
    letter_id: str,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    """
    Generate a context-aware reply by gathering conversation history and related documents,
    then building an enhanced prompt and generating a reply via RAG + Assistants.
    """
    try:
        # Load target letter
        target_letter = await db.letters.find_one({"_id": ObjectId(letter_id)}) or await db.letters.find_one({"_id": letter_id})
        if not target_letter:
            raise HTTPException(status_code=404, detail="Target letter not found")

        # Context (conversation + related docs)
        context = await get_enhanced_context(db, letter_id, current_user)
        # Optional: validate references on the target letter
        _ref_issues = await validate_references(target_letter, db)

        # Build prompt
        prompt = build_enhanced_reply_prompt(target_letter, context)

        # Build org/project uploads dir and gather files (reuse logic from generate_response_endpoint)
        org_name = None
        try:
            org_doc = await db.organizations.find_one({"_id": current_user.organization_id}) or \
                      await db.organizations.find_one({"_id": ObjectId(current_user.organization_id)})
            if org_doc:
                org_name = org_doc.get("name") or org_doc.get("title") or None
        except Exception as e:
            logger.warning(f"Could not resolve organization name: {e}")
        org_short = make_short_name(org_name)

        proj_name = None
        project_id = _effective_project_id(current_user)
        if project_id:
            try:
                proj_doc = await db.projects.find_one({"_id": project_id}) or \
                           await db.projects.find_one({"_id": ObjectId(project_id)})
                if proj_doc:
                    proj_name = proj_doc.get("name") or proj_doc.get("title") or None
            except Exception as e:
                logger.warning(f"Could not resolve project name: {e}")
        project_short = make_short_name(proj_name)

        base_dir = os.path.join("uploads", org_short, project_short)
        org_project_files: List[str] = []
        if os.path.isdir(base_dir):
            for fn in os.listdir(base_dir):
                if fn.lower().endswith((".pdf",)):
                    org_project_files.append(os.path.join(base_dir, fn))

        # Generate response
        response_html = await run_contraclaim_with_rag(
            db=db,
            current_user=current_user,
            user_message_html=prompt,
            org_project_files=org_project_files
        )

        return {"drafted_response": response_html, "reference_warnings": _ref_issues}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating context-aware response: {e}")
        raise HTTPException(status_code=500, detail="Failed to generate context-aware response")

