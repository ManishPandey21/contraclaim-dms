from fastapi import APIRouter, Depends, HTTPException
from ..core.database import get_db
from ..core.security import get_current_user, CurrentUser
from ..models.letter import Letter
from ..models.document import Document
from typing import List, Optional
from pydantic import BaseModel
import logging
from openai import OpenAI
from ..core.config import settings
import numpy as np
from bson.objectid import ObjectId
from datetime import datetime

router = APIRouter()
logger = logging.getLogger(__name__)

# OpenAI client setup
client = OpenAI(api_key=settings.OPENAI_API_KEY)

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

class QuotedClause(BaseModel):
    clause_number: str
    page_number: Optional[str] = None
    line_numbers: Optional[str] = None
    content: str

class DeepPlanningResponse(BaseModel):
    draft_letter: str
    extracted_key_points: str
    quoted_clauses: List[QuotedClause]
    similar_letters: List[dict]
    structure_summary: str

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

async def extract_document_content(db, document_ids: List[str]) -> str:
    """Extract content from multiple documents for context."""
    content_parts = []
    
    for doc_id in document_ids:
        try:
            document = await db.documents.find_one({"_id": ObjectId(doc_id)})
            if document:
                # Extract key information from document
                doc_info = f"Document: {document.get('filename', 'Unknown')}\n"
                doc_info += f"Subject: {document.get('subject', 'No subject')}\n"
                doc_info += f"Date: {document.get('date', 'No date')}\n"
                doc_info += f"From: {document.get('from_', document.get('from', 'Unknown'))}\n"
                doc_info += f"To: {document.get('to', 'Unknown')}\n"
                
                # Add references if available
                if document.get('references'):
                    doc_info += "References:\n"
                    for ref in document.get('references', []):
                        doc_info += f"  - {ref}\n"
                
                content_parts.append(doc_info)
        except Exception as e:
            logger.warning(f"Error extracting content from document {doc_id}: {e}")
            continue
    
    return "\n\n".join(content_parts)

async def find_similar_letters(db, subject: str, organization_id: Optional[str] = None, project_id: Optional[str] = None) -> List[dict]:
    """Find similar letters based on subject similarity."""
    try:
        query_embedding = await get_text_embedding(subject)
        
        # Build filter based on organization and project
        query_filter = {}
        if organization_id:
            query_filter["organization_id"] = organization_id
        if project_id:
            query_filter["project_id"] = project_id
        
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
    target_letter_info: Optional[str] = None
) -> str:
    """Generate a letter draft using OpenAI with document context."""
    try:
        # Build the prompt for AI (Expert Contract Manager Protocol)
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

        # Use the extracted document context as a readable list for reference (do not expand into narrative)
        source_documents_list = (document_context or "").strip() or "None provided"

        # Similar letters are for tone/style cues only; not to be used for facts
        similar_letters_list = ""
        if similar_letters:
            try:
                similar_letters_list = "\n".join([
                    f"- {sl.get('subject','').strip()} | Recipient: {sl.get('recipient','').strip()} | Date: {sl.get('created_at','')}"
                    for sl in similar_letters if isinstance(sl, dict)
                ])
            except Exception:
                similar_letters_list = ""

        # Precompute target letter intelligence block to avoid f-string backslash usage
        target_info_block = ""
        try:
            if target_letter_info and target_letter_info.strip():
                target_info_block = "\nTarget Letter Intelligence (for reply context):\n" + target_letter_info.strip()
        except Exception:
            target_info_block = ""

        prompt = f"""Draft a formal contractual letter based exclusively on the following information. Do not include any facts not present below. Do not fabricate or assume missing information. If critical information is missing, omit that field or explicitly request confirmation.

Information Sources (Your Foundation):
Primary Subject:
- {subject}

Key Facts & Context:
{key_facts_bullets}

Source Documents for Reference:
{source_documents_list}

Specific Points to Address:
{specific_points_bullets}

Similar letters for style only (do not rely on them for facts):
{similar_letters_list if similar_letters_list else 'None provided'}
{target_info_block}

Letter Requirements & Structure (Plain Text Only):
Produce a ready-to-send formal letter in this exact sequence using plain text (no Markdown/HTML).

Our Reference: [include only if provided; otherwise omit]
Your Reference: [include only if provided; otherwise omit]
Date: Current Date

Addressee: [include full name, title, company, address only if provided; otherwise omit]

Subject: Clear, formal subject referencing numbers only if provided.

Salutation:
- If a named contact (last name) is provided: 'Dear Mr. {{LastName}},' or 'Dear Ms. {{LastName}},'
- Otherwise: 'Dear Sir/Madam,'

Body:
Paragraph 1: Purpose. State the reason for writing immediately, referencing any prior correspondence only if provided.
Paragraph 2: Factual Recitation. Chronologically and objectively state relevant facts, citing provided sources (e.g., 'As per Daily Report #284...' or 'Pursuant to Clause 8.4.i...'). Only include clause numbers if exactly provided; otherwise request confirmation.
Paragraph 3: Contractual Position. State the contractual basis with specific clause numbers/subsections only if provided. If not, write: 'Please provide the correct Clause number for [topic].'
Paragraph 4: Action Required/Proposal. Explicit requests/proposals with deadlines only if supported by sources.
Paragraph 5: Closing. Professional closing statement.

Formal Closing:
- 'Yours faithfully,' if no named contact
- 'Yours sincerely,' if using a name

Signature Block:
- Include Sender's Name, Title, Company, and Contact Information only if provided; otherwise omit or ask for confirmation.

Constraints:
- Tone: Formal, professional, objective.
- Language: Clear, concise, unambiguous.
- Assumptions: Strictly prohibited. Omit or ask for confirmation if information is missing.
- Grounding: Use only the facts and documents provided above. Hallucination is unacceptable.
- Format: Plain text only.
- Output: Return only the fully formatted letter with appropriate line breaks and spacing."""

        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are an Expert Contract Manager with 25 years of experience in construction and commercial law. You draft precise, unambiguous, and legally sound correspondence. You adhere strictly to the facts and source documents provided. You never invent information, assume terms, or use placeholder text. If a required detail is missing, omit it or clearly request confirmation. Output plain text only."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=2000,
            temperature=0.2
        )
        
        return response.choices[0].message.content.strip()
        
    except Exception as e:
        logger.error(f"Error generating draft with AI: {str(e)}")
        raise HTTPException(status_code=500, detail=f"AI draft generation failed: {str(e)}")

async def extract_key_points_and_clauses(content: str) -> tuple:
    """Extract key points and quoted clauses from document content."""
    try:
        prompt = f"""
Analyze the following document content and extract:

1. Key points and summary (3-5 bullet points)
2. Quoted clauses with their numbers, page references, and content

CONTENT:
{content}

Return the response in this exact format:

KEY POINTS:
- [First key point]
- [Second key point]
- [Third key point]

QUOTED CLAUSES:
- Clause [number]: [content] (Page [page], Lines [lines])
- Clause [number]: [content] (Page [page])
- Clause [number]: [content]
"""
        
        response = client.chat.completions.create(
            model="gpt-4",
            messages=[
                {"role": "system", "content": "You are a contract analysis assistant that extracts key points and quoted clauses."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1500,
            temperature=0.3
        )
        
        result = response.choices[0].message.content.strip()
        
        # Parse the response
        key_points_section = ""
        clauses_section = ""
        quoted_clauses = []
        
        if "KEY POINTS:" in result and "QUOTED CLAUSES:" in result:
            parts = result.split("QUOTED CLAUSES:")
            key_points_section = parts[0].replace("KEY POINTS:", "").strip()
            clauses_section = parts[1].strip()
            
            # Parse clauses
            for line in clauses_section.split('\n'):
                line = line.strip()
                if line.startswith('- Clause'):
                    # Parse clause format: "Clause [number]: [content] (Page [page], Lines [lines])"
                    clause_parts = line.split(':', 1)
                    if len(clause_parts) > 1:
                        clause_number = clause_parts[0].replace('- Clause', '').strip()
                        rest = clause_parts[1].strip()
                        
                        # Extract page and line numbers if present
                        page_number = None
                        line_numbers = None
                        content = rest
                        
                        if '(' in rest and ')' in rest:
                            paren_start = rest.find('(')
                            paren_end = rest.find(')')
                            if paren_start != -1 and paren_end != -1:
                                notes = rest[paren_start + 1:paren_end]
                                content = rest[:paren_start].strip()
                                
                                if 'Page' in notes:
                                    page_part = notes.split('Page')[1].split(',')[0].strip()
                                    page_number = page_part
                                if 'Lines' in notes:
                                    lines_part = notes.split('Lines')[1].strip()
                                    line_numbers = lines_part
                        
                        quoted_clauses.append({
                            "clause_number": clause_number,
                            "page_number": page_number,
                            "line_numbers": line_numbers,
                            "content": content
                        })
        
        return key_points_section, quoted_clauses
        
    except Exception as e:
        logger.error(f"Error extracting key points and clauses: {str(e)}")
        return "Unable to extract key points", []

@router.post("/deep-planning/generate-draft", response_model=DeepPlanningResponse)
async def generate_deep_planning_draft(
    request: DeepPlanningRequest,
    db = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user)
):
    """Generate a comprehensive letter draft with deep planning capabilities."""
    try:
        logger.info(f"Generating deep planning draft for subject: {request.subject}")
        
        # Extract content from driving documents
        document_context = await extract_document_content(db, request.document_ids)
        
        # Find similar letters for reference
        similar_letters = await find_similar_letters(
            db, 
            request.subject, 
            request.organization_id, 
            request.project_id
        )
        
        # Extract key points and quoted clauses from document context
        key_points, quoted_clauses = await extract_key_points_and_clauses(document_context)
        
        # Enrich with target letter intelligence if a reply is being drafted
        target_letter_info = ""
        try:
            if request.target_letter_id:
                tl = await db.letters.find_one({"_id": ObjectId(request.target_letter_id)}) or \
                     await db.letters.find_one({"_id": request.target_letter_id})
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
            target_letter_info=target_letter_info
        )
        
        # Generate structure summary
        structure_summary = f"Generated draft based on {len(request.document_ids)} driving documents and {len(similar_letters)} similar letters."
        
        return DeepPlanningResponse(
            draft_letter=draft_letter,
            extracted_key_points=key_points,
            quoted_clauses=quoted_clauses,
            similar_letters=similar_letters,
            structure_summary=structure_summary
        )
        
    except Exception as e:
        logger.error(f"Error in deep planning draft generation: {str(e)}")
        raise HTTPException(status_code=500, detail=f"Deep planning failed: {str(e)}")
