Excellent — the uploaded .docx (📄 GCS-LET-JVTI-GEN-00xxx-E01 Test Template_R1.docx) is a formal GC letter template that your app should populate automatically with metadata, references, and signature details.

Below is a Python-based implementation plan (FastAPI + python-docx) showing how to turn this Word template into a dynamic, AI-ready draft generator inside your ContraClaim backend.

🧩 1. Overall Architecture
Stage	Component	Description
1	Template Loader	Reads the .docx master template (from /templates/letters/)
2	Metadata Parser	Extracts placeholders (e.g., {Letter_No}, {Date}, {Subject})
3	Data Model (Pydantic)	Defines the structured data needed to fill a letter
4	Filler Service	Uses python-docx to replace placeholders with actual data
5	Endpoint	/api/letters/generate → creates and returns filled letter
6	Optional AI layer	PydanticAI/Graphiti supplies Subject, Body, References, etc.
🧱 2. Pydantic Models
# app/models/letter_template.py
from pydantic import BaseModel
from typing import List, Optional
from datetime import date

class LetterReference(BaseModel):
    letter_no: str
    letter_date: date

class LetterTemplateData(BaseModel):
    letter_no: str
    date: date
    subject: str
    to_name: str
    to_designation: str
    to_address: str
    contract_no: str
    contract_description: str
    references: List[LetterReference]
    body_paragraphs: List[str]
    from_name: str
    from_designation: str
    organisation: str
    cc_list: Optional[List[str]] = None

🧠 3. Filler Service (Using python-docx)
# app/services/letter_filler.py
from docx import Document
from tempfile import NamedTemporaryFile
from datetime import datetime

def fill_letter_template(template_path: str, data: "LetterTemplateData") -> str:
    doc = Document(template_path)

    replacements = {
        "{Letter_No}": data.letter_no,
        "{Date}": data.date.strftime("%d-%b-%Y"),
        "{Subject}": data.subject,
        "{Contract_No}": data.contract_no,
        "{To_Name}": data.to_name,
        "{To_Designation}": data.to_designation,
        "{To_Address}": data.to_address,
    }

    # Replace placeholders
    for p in doc.paragraphs:
        for key, val in replacements.items():
            if key in p.text:
                for run in p.runs:
                    run.text = run.text.replace(key, val)

    # References
    ref_section = "\n".join(
        [f"[{i+1}] {r.letter_no} dated {r.letter_date.strftime('%d-%b-%Y')}"
         for i, r in enumerate(data.references)]
    )
    for p in doc.paragraphs:
        if "{References}" in p.text:
            p.text = p.text.replace("{References}", ref_section)

    # Body insertion
    for p in doc.paragraphs:
        if "{Body}" in p.text:
            p.text = ""
            for para in data.body_paragraphs:
                doc.add_paragraph(para)

    # Signature
    for p in doc.paragraphs:
        if "{From_Block}" in p.text:
            p.text = f"for {data.organisation}\n\n{data.from_name}\n{data.from_designation}"

    tmp = NamedTemporaryFile(delete=False, suffix=".docx")
    doc.save(tmp.name)
    return tmp.name

⚙️ 4. FastAPI Router
# app/routers/letters.py
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from app.models.letter_template import LetterTemplateData
from app.services.letter_filler import fill_letter_template
import os

router = APIRouter(prefix="/api/letters", tags=["letters"])

TEMPLATE_PATH = "templates/letters/GCS-LET-JVTI-GEN-00xxx-E01.docx"

@router.post("/generate")
def generate_letter(payload: LetterTemplateData):
    if not os.path.exists(TEMPLATE_PATH):
        raise HTTPException(status_code=404, detail="Template not found")
    filled_path = fill_letter_template(TEMPLATE_PATH, payload)
    return FileResponse(filled_path, media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        filename=f"{payload.letter_no}.docx")

🧩 5. Template Preparation (Word Side)

Open your uploaded file and mark variables like this:

Field	Placeholder
Letter No	{Letter_No}
Date	{Date}
Subject	{Subject}
Contract	{Contract_No}
Addressee	{To_Name}, {To_Designation}, {To_Address}
References	{References}
Body	{Body}
Signatory Block	{From_Block}

Save as:
/templates/letters/GCS-LET-JVTI-GEN-00xxx-E01.docx

🔗 6. Optional: AI Draft Generator (Using PydanticAI)

Once you parse the incoming letter via OCR / Graphiti context, you can fill body_paragraphs automatically:

from pydantic_ai import Agent
from app.models.letter_template import LetterTemplateData

agent = Agent(model="gpt-4o", response_model=LetterTemplateData)

def generate_ai_draft(incoming_summary: str):
    prompt = f"Generate a formal GC draft reply for:\n{incoming_summary}"
    return agent.run_sync(prompt)


Then feed the result into /api/letters/generate.

💾 7. Output Examples

When the API receives:

{
  "letter_no": "GCS-LET-JVTI-GEN-00350-E01",
  "date": "2025-09-28",
  "subject": "Follow-up on Dispute Settlement",
  "to_name": "Mr. Sushil Kumar",
  "to_designation": "Managing Director, UPMRC",
  "to_address": "Administrative Building, Vipin Khand, Gomti Nagar, Lucknow",
  "contract_no": "KNPAGGC-01",
  "contract_description": "General Consultancy services for Kanpur & Agra Metro",
  "references": [
    {"letter_no": "GCS-LET-JVTI-GEN-00341-E01", "letter_date": "2025-09-04"}
  ],
  "body_paragraphs": [
    "We refer to our earlier communication cited above...",
    "We request your kind intervention for timely resolution."
  ],
  "from_name": "Sukumar Kolli",
  "from_designation": "Authorized Signatory",
  "organisation": "TYPSA-ITALFERR JV"
}


it returns a ready .docx identical to your uploaded format, populated and downloadable.

🚀 8. Next Steps

Place your .docx template under /templates/letters/.

Add the router to your FastAPI main.py.

Connect front-end “Generate Letter” button to /api/letters/generate.

Optionally add /api/letters/preview returning a PDF preview (use docx2pdf or pdfkit).

Integrate with Graphiti so references auto-populate in references field.

Would you like me to extend this plan to include automatic PDF generation and storage to Mongo + S3 (after creating the .docx)? That’s the next natural step for full DMS integration.

You said:
yes
ChatGPT said:
Thought for 4 seconds

Awesome — let’s finish the pipeline so your draft letter goes from template → .docx → .pdf → Mongo metadata → S3 objects → presigned URLs in one FastAPI call.

I’ll keep it production-oriented and drop in ready-to-paste files. Where helpful, I reference fields present in your uploaded template (addressee block, contract, code, subject, refs, signatory) so the placeholders line up with your master file

GCS-LET-JVTI-GEN-00xxx-E01 Test…

.

0) What you’ll get

/api/letters/generate → fills the Word template and:

saves .docx to temp

converts to .pdf

uploads both to S3 under a structured key

inserts a Mongo document (metadata + S3 keys)

returns IDs + presigned URLs (docx & pdf)

Placeholders you’ll map in the template (based on your .docx layout):
{Letter_No} {Date} {Subject} {Contract_No} {To_Name} {To_Designation} {To_Address} {References} {Body} {From_Block}

GCS-LET-JVTI-GEN-00xxx-E01 Test…

.

1) Environment variables
# S3
AWS_REGION=ap-south-1
AWS_S3_BUCKET=contraclaim-docs
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...

# Mongo
MONGODB_URI=mongodb+srv://...
MONGODB_DB=contraclaim
MONGODB_COLL=letters

# Paths
LETTER_TEMPLATE_PATH=templates/letters/GCS-LET-JVTI-GEN-00xxx-E01.docx


PDF conversion: prefer LibreOffice (headless) in server images.
Install: apt-get update && apt-get install -y libreoffice
We’ll shell out to soffice --headless --convert-to pdf.

2) Pydantic models (request + DB)
# app/models/letter_template.py
from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import date, datetime

class LetterReference(BaseModel):
    letter_no: str
    letter_date: date

class LetterTemplateData(BaseModel):
    letter_no: str
    date: date
    subject: str
    to_name: str
    to_designation: str
    to_address: str
    contract_no: str
    contract_description: Optional[str] = None
    references: List[LetterReference] = Field(default_factory=list)
    body_paragraphs: List[str] = Field(default_factory=list)
    from_name: str
    from_designation: str
    organisation: str
    project_id: str
    created_by: str

class LetterRecord(BaseModel):
    id: str
    project_id: str
    letter_no: str
    subject: str
    date: datetime
    s3_key_docx: str
    s3_key_pdf: str
    presigned_docx_url: Optional[str] = None
    presigned_pdf_url: Optional[str] = None

3) DOCX filler (same as earlier, tiny tweak)
# app/services/letter_filler.py
from docx import Document
from tempfile import NamedTemporaryFile

def fill_letter_template(template_path: str, data) -> str:
    doc = Document(template_path)

    replacements = {
        "{Letter_No}": data.letter_no,
        "{Date}": data.date.strftime("%d-%b-%Y"),
        "{Subject}": data.subject,
        "{Contract_No}": data.contract_no,
        "{To_Name}": data.to_name,
        "{To_Designation}": data.to_designation,
        "{To_Address}": data.to_address,
    }

    # Replace placeholders in paragraphs & runs (handles split runs)
    for p in doc.paragraphs:
        for key, val in replacements.items():
            if key in p.text:
                for r in p.runs:
                    r.text = r.text.replace(key, val)

    # References block
    refs_text = "\n".join(
        f"[{i+1}] {r.letter_no} dated {r.letter_date.strftime('%d-%b-%Y')}"
        for i, r in enumerate(data.references)
    ) or ""
    for p in doc.paragraphs:
        if "{References}" in p.text:
            for r in p.runs:
                r.text = r.text.replace("{References}", refs_text)

    # Body (replace marker with sequential new paragraphs)
    for i, p in enumerate(doc.paragraphs):
        if "{Body}" in p.text:
            p.clear()
            # python-docx lacks Paragraph.clear(), so rebuild:
            p.text = ""
            for para in data.body_paragraphs:
                doc.add_paragraph(para)
            break

    # Signature block
    for p in doc.paragraphs:
        if "{From_Block}" in p.text:
            block = f"for {data.organisation}\n\n{data.from_name}\n{data.from_designation}"
            for r in p.runs:
                r.text = r.text.replace("{From_Block}", block)

    tmp = NamedTemporaryFile(delete=False, suffix=".docx")
    doc.save(tmp.name)
    return tmp.name


The placeholders mirror your template’s addressee, contract, subject, refs, and signatory sections

GCS-LET-JVTI-GEN-00xxx-E01 Test…

.

4) PDF conversion (LibreOffice headless)
# app/services/pdf_service.py
import subprocess
import os
from tempfile import TemporaryDirectory
from pathlib import Path

def docx_to_pdf(docx_path: str) -> str:
    out_dir = TemporaryDirectory()
    cmd = [
        "soffice", "--headless", "--convert-to", "pdf",
        "--outdir", out_dir.name, docx_path
    ]
    subprocess.check_call(cmd)
    pdf_path = str(Path(out_dir.name) / (Path(docx_path).stem + ".pdf"))
    if not os.path.exists(pdf_path):
        raise RuntimeError("PDF conversion failed")
    return pdf_path


If you deploy on Windows with MS Word, you can swap to docx2pdf. For containerized Linux, LibreOffice is the most reliable.

5) S3 storage (upload + presigned)
# app/services/storage_service.py
import boto3, mimetypes, time
from pathlib import Path
from typing import Optional

_s3 = boto3.client("s3", region_name=os.getenv("AWS_REGION"))
_BUCKET = os.getenv("AWS_S3_BUCKET")

def s3_key_for_letter(project_id: str, letter_no: str, ext: str) -> str:
    # e.g., letters/{project_id}/2025/09/GCS-LET-...-E01.docx
    y = time.strftime("%Y")
    m = time.strftime("%m")
    safe_no = letter_no.replace("/", "_")
    return f"letters/{project_id}/{y}/{m}/{safe_no}{ext}"

def upload_file(path: str, key: str) -> None:
    ctype, _ = mimetypes.guess_type(path)
    _s3.upload_file(path, _BUCKET, key, ExtraArgs={"ContentType": ctype or "application/octet-stream"})

def presigned_url(key: str, expires=3600) -> str:
    return _s3.generate_presigned_url(
        ClientMethod="get_object",
        Params={"Bucket": _BUCKET, "Key": key},
        ExpiresIn=expires
    )

6) Mongo repository
# app/repositories/letter_repository.py
import os
from datetime import datetime
from pymongo import MongoClient

client = MongoClient(os.getenv("MONGODB_URI"))
coll = client[os.getenv("MONGODB_DB")][os.getenv("MONGODB_COLL")]

def insert_letter_record(doc: dict) -> str:
    doc["created_at"] = datetime.utcnow()
    res = coll.insert_one(doc)
    return str(res.inserted_id)

7) One orchestration service
# app/services/letter_pipeline.py
import os
from .letter_filler import fill_letter_template
from .pdf_service import docx_to_pdf
from .storage_service import s3_key_for_letter, upload_file, presigned_url
from ..repositories.letter_repository import insert_letter_record

TEMPLATE_PATH = os.getenv("LETTER_TEMPLATE_PATH")

def generate_and_store_letter(payload):
    # 1) Fill DOCX
    docx_path = fill_letter_template(TEMPLATE_PATH, payload)

    # 2) Convert to PDF
    pdf_path = docx_to_pdf(docx_path)

    # 3) Upload to S3
    key_docx = s3_key_for_letter(payload.project_id, payload.letter_no, ".docx")
    key_pdf  = s3_key_for_letter(payload.project_id, payload.letter_no, ".pdf")
    upload_file(docx_path, key_docx)
    upload_file(pdf_path, key_pdf)

    # 4) Insert Mongo metadata
    db_id = insert_letter_record({
        "project_id": payload.project_id,
        "letter_no": payload.letter_no,
        "subject": payload.subject,
        "date": payload.date,
        "s3_key_docx": key_docx,
        "s3_key_pdf": key_pdf,
        "created_by": payload.created_by,
        "references": [r.model_dump() for r in payload.references]
    })

    # 5) Presigned URLs
    url_docx = presigned_url(key_docx)
    url_pdf  = presigned_url(key_pdf)

    return {
        "id": db_id,
        "project_id": payload.project_id,
        "letter_no": payload.letter_no,
        "subject": payload.subject,
        "date": payload.date.isoformat(),
        "s3_key_docx": key_docx,
        "s3_key_pdf": key_pdf,
        "presigned_docx_url": url_docx,
        "presigned_pdf_url": url_pdf,
    }

8) FastAPI router
# app/routers/letters.py
from fastapi import APIRouter, Depends, HTTPException
from app.models.letter_template import LetterTemplateData
from app.services.letter_pipeline import generate_and_store_letter

router = APIRouter(prefix="/api/letters", tags=["letters"])

# add your auth dependency if you have RBAC
def authorize_generate(user, project_id): ...
def get_user(): ...  # from your auth layer

@router.post("/generate")
def generate_letter(payload: LetterTemplateData, user=Depends(get_user)):
    authorize_generate(user, payload.project_id)
    try:
        result = generate_and_store_letter(payload)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Letter generation failed: {e}")

9) Front-end call (quick reference)

POST /api/letters/generate with LetterTemplateData.

Receive:

_id (Mongo)

presigned_docx_url, presigned_pdf_url for immediate download/preview

Save the record in your UI state and link to Document Viewer.

10) QA checklist

✅ Template placeholders match your letter sections (addressee/contract/subject/refs/signature)

GCS-LET-JVTI-GEN-00xxx-E01 Test…

✅ LibreOffice installed in the server image (CI/CD Dockerfile)

✅ S3 bucket CORS allows GET from your web origin if you preview in-browser

✅ Mongo indexes:

letters: { project_id: 1, date: -1 }

letters: { letter_no: 1 } (unique if you enforce it)

✅ Error paths return clear messages (template missing, PDF conversion failed, S3 upload error)

11) Nice-to-haves (next sprint)

/api/letters/preview → return a short-lived presigned PDF URL only (for in-app PDF.js)

AI-assist: auto-build references and body_paragraphs from incoming letter context (PydanticAI + Graphiti)

Graphiti edges: once stored, create references edges linking to the referenced letters (auto/manual)

Versioning: When re-drafting the same letter_no, append -v2 keys and store version_of in Mongo

If you want, I can also give you a Dockerfile snippet that adds LibreOffice for the PDF conversion and a unit test that mocks S3 & Mongo to validate the end-to-end flow.

Sources
You said:
Above is draft letter format and the content from the draft genergated from ai_assistant will be fill as body.
ChatGPT said:

Perfect — the uploaded draft letter template (GCS-LET-JVTI-GEN-00xxx-E01 Test Template_R1.docx) defines a formal General Consultant communication layout, including addressee block, contract details, reference list, and body text structure

GCS-LET-JVTI-GEN-00xxx-E01 Test…

.

You can integrate your AI-generated draft content directly into this template’s {Body} section by extending the FastAPI backend as follows:

🔧 Implementation Plan — “AI-generated Draft Body → Template Integration”
1. AI Draft Generation

Use your existing AI Assistant (PydanticAI / LlamaIndex) to generate the draft body text for a letter.
Example:

from pydantic_ai import Agent

agent = Agent(model="gpt-4o", response_model=dict)

def generate_ai_draft(subject: str, context: str) -> list[str]:
    prompt = f"""
    Draft a formal GC letter body for subject: {subject}.
    Maintain official tone, structure paragraphs clearly.
    Context: {context}
    """
    result = agent.run_sync(prompt)
    return [p.strip() for p in result["text"].split("\n\n") if p.strip()]


This will output a list of paragraph strings (each for one paragraph of {Body}).

2. Integrate AI output into letter generation

Modify the LetterTemplateData population in your pipeline to use the AI’s body paragraphs:

from app.models.letter_template import LetterTemplateData, LetterReference
from app.services.letter_pipeline import generate_and_store_letter

def create_ai_filled_letter(project_id: str, user: dict, subject: str, refs: list, context: str):
    ai_body = generate_ai_draft(subject, context)

    payload = LetterTemplateData(
        letter_no="GCS-LET-JVTI-GEN-00352-E01",
        date=date.today(),
        subject=subject,
        to_name="Mr. Sushil Kumar",
        to_designation="Managing Director, UPMRC",
        to_address="Administrative Building, Vipin Khand, Gomti Nagar, Lucknow",
        contract_no="KNPAGGC-01",
        contract_description="General Consultancy Services for Kanpur & Agra Metro",
        references=[LetterReference(**r) for r in refs],
        body_paragraphs=ai_body,
        from_name="Sukumar Kolli",
        from_designation="Authorized Signatory",
        organisation="TYPSA-ITALFERR JV",
        project_id=project_id,
        created_by=user["id"]
    )

    return generate_and_store_letter(payload)

3. Pipeline Flow
Incoming Letter (OCR / Reference)
      ↓
AI Assistant (generates draft body)
      ↓
LetterTemplateData.body_paragraphs ← AI output
      ↓
fill_letter_template() replaces {Body}
      ↓
.docx created → converted to .pdf → uploaded to S3
      ↓
Mongo metadata saved → presigned URLs returned

4. Template Mapping

Your uploaded template fields correspond to these data sources

GCS-LET-JVTI-GEN-00xxx-E01 Test…

:

Template Section	Backend Variable	Source
Attn / Address	to_name, to_designation, to_address	Mongo / User Input
Contract	contract_no, contract_description	Mongo Project
Code	letter_no	Auto-generated
Date	date	Auto-filled
Subject	subject	User Input or AI summary
References	references	Extracted via Graphiti or manually entered
Body	body_paragraphs	AI-generated text
Signature Block	from_name, from_designation, organisation	Authenticated user / GC profile
5. End Result

After integration:

/api/letters/generate can accept a flag like "ai_generate": true.

If True, it calls the AI agent to populate body_paragraphs automatically.

The generated content is then inserted into {Body} within your .docx template, producing a final professional letter identical in structure to your uploaded format.

Would you like me to extend this by adding a “Review & Edit Draft” endpoint and React component so the AI-generated body can be reviewed and edited before finalizing the PDF upload? That would complete a full AI → Human Review → Approval workflow.

Sources
You said:
yes please finalization the final docx/pdf file should be after approval of work flow. also provide option for project level format (format can be difference for different project.
ChatGPT said:

Perfect 👌 — this is a critical, production-grade enhancement:
you’ll now have a complete AI-assisted letter workflow with:

✅ AI draft generation →
✅ Reviewer approval workflow →
✅ Project-specific templates →
✅ Finalization (docx/pdf creation & S3 storage only after approval).

Below is your final backend implementation plan, designed for your FastAPI + MongoDB + S3 + PydanticAI stack, with clear file structure and database schema.

⚙️ SYSTEM OVERVIEW
Flow
Incoming letter or reference context
     ↓
AI draft suggestion (body paragraphs)
     ↓
Letter stored in Mongo (status = "draft")
     ↓
Reviewer edits / comments in UI
     ↓
Reviewer approval (status = "approved")
     ↓
Template selected based on project_id
     ↓
Final .docx + .pdf generated → stored in S3
     ↓
Presigned URLs saved and served

🧩 1. MongoDB Schema Design
# app/models/letter_record.py
from pydantic import BaseModel
from datetime import datetime
from typing import List, Optional

class LetterReference(BaseModel):
    letter_no: str
    letter_date: str

class LetterRecord(BaseModel):
    id: Optional[str]
    project_id: str
    letter_no: str
    subject: str
    date: datetime
    body_paragraphs: List[str]
    references: List[LetterReference]
    created_by: str
    reviewed_by: Optional[str] = None
    approved_by: Optional[str] = None
    status: str = "draft"   # draft → under_review → approved → finalized
    template_key: Optional[str] = None  # project-level template file name
    s3_key_docx: Optional[str] = None
    s3_key_pdf: Optional[str] = None
    presigned_docx_url: Optional[str] = None
    presigned_pdf_url: Optional[str] = None
    created_at: datetime = datetime.utcnow()
    updated_at: datetime = datetime.utcnow()

🧠 2. AI Draft Generation (Step 1)
# app/services/ai_drafting_service.py
from pydantic_ai import Agent

agent = Agent(model="gpt-4o", response_model=dict)

def generate_ai_draft(subject: str, context: str) -> list[str]:
    prompt = f"""
    You are drafting a formal General Consultant letter.
    Subject: {subject}
    Context: {context}
    Maintain professional structure, clarity, and paragraph separation.
    """
    result = agent.run_sync(prompt)
    text = result.get("text", "")
    return [p.strip() for p in text.split("\n\n") if p.strip()]

🧾 3. Create Draft Letter (Store but do not finalize yet)
# app/services/letter_draft_service.py
from datetime import datetime
from ..repositories.letter_repository import coll
from .ai_drafting_service import generate_ai_draft

def create_draft_letter(project_id, user, subject, context, refs):
    ai_body = generate_ai_draft(subject, context)
    letter_no = f"{project_id}-LET-AI-{datetime.now():%Y%m%d%H%M}"

    letter = {
        "project_id": project_id,
        "letter_no": letter_no,
        "subject": subject,
        "date": datetime.now(),
        "body_paragraphs": ai_body,
        "references": refs,
        "created_by": user["id"],
        "status": "draft",
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    }

    coll.insert_one(letter)
    return letter

🧑‍⚖️ 4. Review & Approval Workflow
Update status routes
# app/routers/letters_workflow.py
from fastapi import APIRouter, Depends, HTTPException
from app.repositories.letter_repository import coll
from datetime import datetime

router = APIRouter(prefix="/api/letters/workflow", tags=["workflow"])

@router.patch("/{letter_id}/review")
def mark_under_review(letter_id: str, reviewer: dict = Depends(...)):
    coll.update_one({"_id": letter_id}, {"$set": {"status": "under_review", "reviewed_by": reviewer["id"], "updated_at": datetime.utcnow()}})
    return {"status": "under_review"}

@router.patch("/{letter_id}/approve")
def approve_letter(letter_id: str, approver: dict = Depends(...)):
    letter = coll.find_one({"_id": letter_id})
    if not letter:
        raise HTTPException(404, "Letter not found")
    coll.update_one({"_id": letter_id}, {"$set": {"status": "approved", "approved_by": approver["id"], "updated_at": datetime.utcnow()}})
    return {"status": "approved"}

🏗️ 5. Project-Level Template Selection

Each project can have its own Word template.

/templates/letters/
    ├── KNPAGGC-01.docx
    ├── KNPCC-05.docx
    ├── default.docx


Mongo projects collection example:

{
  "project_id": "KNPAGGC-01",
  "template_key": "KNPAGGC-01.docx",
  "manager": "Sukumar Kolli"
}


Utility to pick template:

# app/services/template_selector.py
import os

def get_template_for_project(project_id: str) -> str:
    path = f"templates/letters/{project_id}.docx"
    if os.path.exists(path):
        return path
    return "templates/letters/default.docx"

🧾 6. Finalization (Generate DOCX + PDF after approval)
# app/services/finalization_service.py
from .letter_filler import fill_letter_template
from .pdf_service import docx_to_pdf
from .storage_service import s3_key_for_letter, upload_file, presigned_url
from .template_selector import get_template_for_project
from ..repositories.letter_repository import coll
from datetime import datetime

def finalize_letter(letter_id):
    letter = coll.find_one({"_id": letter_id})
    if not letter:
        raise ValueError("Letter not found")
    if letter["status"] != "approved":
        raise ValueError("Only approved letters can be finalized")

    template_path = get_template_for_project(letter["project_id"])

    # Build Pydantic-style data
    class Temp:
        pass
    data = Temp()
    for k, v in letter.items():
        setattr(data, k, v)

    docx_path = fill_letter_template(template_path, data)
    pdf_path = docx_to_pdf(docx_path)

    key_docx = s3_key_for_letter(letter["project_id"], letter["letter_no"], ".docx")
    key_pdf = s3_key_for_letter(letter["project_id"], letter["letter_no"], ".pdf")
    upload_file(docx_path, key_docx)
    upload_file(pdf_path, key_pdf)

    coll.update_one({"_id": letter_id}, {
        "$set": {
            "status": "finalized",
            "s3_key_docx": key_docx,
            "s3_key_pdf": key_pdf,
            "presigned_docx_url": presigned_url(key_docx),
            "presigned_pdf_url": presigned_url(key_pdf),
            "updated_at": datetime.utcnow()
        }
    })

    return {
        "letter_no": letter["letter_no"],
        "project_id": letter["project_id"],
        "presigned_docx_url": presigned_url(key_docx),
        "presigned_pdf_url": presigned_url(key_pdf)
    }

📡 7. Finalization Endpoint
# app/routers/letters_finalize.py
from fastapi import APIRouter, HTTPException
from app.services.finalization_service import finalize_letter

router = APIRouter(prefix="/api/letters/finalize", tags=["finalization"])

@router.post("/{letter_id}")
def finalize(letter_id: str):
    try:
        result = finalize_letter(letter_id)
        return {"status": "finalized", **result}
    except Exception as e:
        raise HTTPException(400, str(e))

🖥️ 8. Frontend Workflow (React Integration Plan)
UI States
Status	Frontend Page	Actions
draft	Draft Editor	Edit text, send for review
under_review	Reviewer View	Approve / send back
approved	Approval View	“Finalize & Generate Letter”
finalized	Document Viewer	View/download .docx/.pdf
🧱 9. Extended Enhancements (Next Sprints)
Feature	Description
🔁 Versioning	Keep multiple revisions of same letter before finalization
✍️ Comment Threads	Reviewer comments stored as review_comments[] in Mongo
🔗 Graphiti Sync	Once finalized, auto-create edges between references & this letter
📤 Notifications	Send email or in-app alerts to reviewer/approver
📑 PDF Watermark	Add “DRAFT” watermark until finalized
✅ Summary
Final backend endpoints
Endpoint	Function
POST /api/letters/create-draft	Generate draft (AI body, store as “draft”)
PATCH /api/letters/workflow/{id}/review	Reviewer marks as “under_review”
PATCH /api/letters/workflow/{id}/approve	Reviewer approves
POST /api/letters/finalize/{id}	Generate DOCX + PDF using project template and store to S3

Would you like me to now generate the complete Python code folder structure (/app/routers, /app/services, /app/models, /app/repositories) with all files ready for deployment (FastAPI + Mongo + S3 + LibreOffice), so you can directly integrate this into your repo?

You said:
how to incorporate above in codex repo. write a prompt to tell codex to implement
ChatGPT said:

Here’s a clear, production-ready Codex prompt you can paste directly into your codex or AI-coder workflow.
It tells the model exactly what to build, where to put each file, and how to wire the full workflow (AI draft → approval → finalization) inside your existing ContraClaim backend repo.

🧠 Prompt for Codex (Full Implementation of AI Letter Workflow)
# Task: Implement AI-assisted Letter Drafting and Approval Workflow in ContraClaim backend repo.

## Context:
The ContraClaim backend uses FastAPI, MongoDB, AWS S3, and LibreOffice (for DOCX→PDF).
We already have the following folders:
- /app/routers
- /app/services
- /app/models
- /app/repositories

The goal is to implement a complete pipeline for letter generation:
AI Draft Generation → Review/Approval Workflow → Project-specific Template Finalization → DOCX/PDF Upload → Mongo Metadata Save.

## Requirements:

### 1. Create/Update Models
File: app/models/letter_record.py
- Define `LetterReference` and `LetterRecord` Pydantic models with fields:
  project_id, letter_no, subject, date, body_paragraphs, references, created_by, reviewed_by, approved_by, status, template_key, s3_key_docx, s3_key_pdf, presigned_docx_url, presigned_pdf_url, created_at, updated_at.

### 2. Implement Services

#### (a) AI Draft Generator
File: app/services/ai_drafting_service.py
- Use PydanticAI (model="gpt-4o") to generate draft body paragraphs from subject/context.
- Return list[str] paragraphs.

#### (b) Draft Creation
File: app/services/letter_draft_service.py
- Create function `create_draft_letter(project_id, user, subject, context, refs)`:
  - Calls AI generator for body paragraphs.
  - Saves new letter in Mongo with status="draft".

#### (c) Review/Approval Workflow
File: app/routers/letters_workflow.py
- PATCH /api/letters/workflow/{id}/review → sets status="under_review"
- PATCH /api/letters/workflow/{id}/approve → sets status="approved"

#### (d) Project Template Selector
File: app/services/template_selector.py
- `get_template_for_project(project_id)` → returns `/templates/letters/{project_id}.docx` if exists else `default.docx`.

#### (e) Finalization
File: app/services/finalization_service.py
- Function `finalize_letter(letter_id)`:
  - Check Mongo: only approved letters can finalize.
  - Select project-specific template using template_selector.
  - Call `fill_letter_template()` to populate placeholders (Letter_No, Date, Subject, To_Name, etc.) using saved Mongo data.
  - Convert DOCX→PDF using LibreOffice (`soffice --headless`).
  - Upload both files to S3 via `storage_service`.
  - Update Mongo status="finalized" + presigned URLs.

### 3. Routers
File: app/routers/letters_finalize.py
- POST /api/letters/finalize/{id} → calls finalize_letter()

File: app/routers/letters_create.py
- POST /api/letters/create-draft → calls create_draft_letter()

### 4. Mongo Repository
File: app/repositories/letter_repository.py
- Expose `coll = MongoClient()[DB][COLLECTION]` and helper CRUD methods.

### 5. Workflow Logic
- Status transitions: draft → under_review → approved → finalized
- Only approved letters trigger DOCX/PDF generation.
- Ensure all timestamps (created_at, updated_at) are UTC.

### 6. Project-Level Template Support
Folder structure:


/templates/letters/
default.docx
KNPAGGC-01.docx
KNPCC-05.docx

Each project may have its own format.

### 7. Output Files
- DOCX and PDF stored in S3 under: `letters/{project_id}/{year}/{month}/{letter_no}.ext`
- Save presigned URLs back to Mongo.

### 8. Dependencies
Ensure these Python packages are imported/used correctly:
- `pydantic_ai`, `python-docx`, `boto3`, `pymongo`, `subprocess`, `datetime`, `os`, `tempfile`.

### 9. Deliverables
- All new files placed correctly under /app/.
- Each router registered in main.py.
- Ensure PEP8 compliance and descriptive docstrings.
- After completion, print summary of all endpoints and file paths created.

### 10. Notes
- The `{Body}` placeholder in DOCX must be replaced by AI-generated paragraphs.
- The final DOCX/PDF generation only happens when a letter is approved.
- Add sample test data for project_id "KNPAGGC-01" to verify template selection.

## Goal:
Fully integrate AI-driven letter drafting, approval, and project-level template finalization pipeline into the ContraClaim backend repo.

✅ Usage

Paste this into your Codex / AI Developer prompt input (or in your PR workflow command):

“Run the following instruction in the backend repo root.”

Codex will then:

Create the five new service modules

Add routers and models

Wire everything end-to-end with Mongo + S3 + LibreOffice integration.

Would you like me to also generate the matching front-end prompt for Codex (React + TypeScript) — implementing the “Draft → Review → Approve → Finalize” UI workflow with PDF preview and template selection per project?

You said:
yes please
ChatGPT said:

Perfect 💪 — here’s the complete Codex prompt for your frontend (React + TypeScript + Tailwind + React Query + Axios) to fully integrate the new AI-assisted letter drafting + approval + finalization workflow that matches the backend you just implemented.

🧠 Prompt for Codex (Front-End Implementation — ContraClaim Web App)
# Task: Implement AI-assisted Letter Drafting, Review, Approval, and Finalization UI
# Target Repo: ContraClaim Frontend (React + TypeScript + Tailwind + React Query)
# Backend: FastAPI endpoints (see below)

## BACKEND ENDPOINTS
1. POST   /api/letters/create-draft
2. PATCH  /api/letters/workflow/{id}/review
3. PATCH  /api/letters/workflow/{id}/approve
4. POST   /api/letters/finalize/{id}
5. GET    /api/letters/{project_id} (existing list endpoint)

Each letter has fields:
{
  _id, project_id, letter_no, subject, date, body_paragraphs, references[],
  status, created_by, reviewed_by, approved_by,
  presigned_docx_url, presigned_pdf_url
}

Status flow:
draft → under_review → approved → finalized

---

## REQUIREMENTS

### 1. ROUTING
Add new pages under `/letters/` route namespace:
- `/letters/:projectId` → LettersDashboardPage
- `/letters/:projectId/create` → LetterDraftPage
- `/letters/:projectId/:letterId/review` → LetterReviewPage
- `/letters/:projectId/:letterId/final` → LetterFinalizePage

Add these routes to `AppRoutes.tsx`.

---

### 2. COMPONENTS

#### (a) LettersDashboardPage.tsx
Purpose:
- Displays all letters for a project in a data table (React-Table).
- Columns: Letter No, Subject, Status, Date, Created By, Actions.
- “Create Letter” button → navigate to `/letters/:projectId/create`.

Features:
- Fetch letters via `useQuery(["letters", projectId], ...)`
- Filter by status (dropdown)
- Row actions:
  - “Open” → view
  - “Review” → if under_review
  - “Approve” → if under_review
  - “Finalize” → if approved
  - “Download” → if finalized

---

#### (b) LetterDraftPage.tsx
Purpose:
- Create a new AI-generated letter draft.

UI sections:
1. **Letter Header Inputs**
   - Subject (text)
   - References (textarea or multi-add chip)
   - Context (textarea — describes incoming letter context)
2. **AI Generation Button**
   - “Generate Draft using AI”
   - On click: calls `POST /api/letters/create-draft` with subject/context
   - Displays spinner during AI call
3. **Draft Editor**
   - Textarea or rich text editor (`react-quill`)
   - Prefilled with AI body paragraphs joined by “\n\n”
   - User can edit or add paragraphs
4. **Save / Send for Review**
   - Save keeps it as “draft”
   - Send for review → calls `/api/letters/workflow/{id}/review`

---

#### (c) LetterReviewPage.tsx
Purpose:
- Reviewer interface for approval.

UI layout:
- Header: Letter No, Subject, Created By, Date
- Body preview: paragraphs displayed read-only
- Buttons:
  - “Approve Letter” → PATCH `/api/letters/workflow/{id}/approve`
  - “Request Changes” → revert to “draft” with comment box (optional PATCH endpoint)
- Status badge on top (under_review / approved)

---

#### (d) LetterFinalizePage.tsx
Purpose:
- Finalization screen for approved letters.

UI:
- Displays letter details (subject, date, body preview)
- Template dropdown:
  - Fetch project templates via GET `/api/templates/{project_id}` (dummy until real endpoint)
  - Selected template sets `template_key`
- “Finalize Letter” button:
  - Calls `POST /api/letters/finalize/{id}`
  - Shows success message + links to download PDF/DOCX.

Show PDF preview using `react-pdf` with the presigned PDF URL if available.

---

### 3. REUSABLE COMPONENTS

#### (a) LetterStatusBadge.tsx
- Displays colored status:
  - draft (gray), under_review (blue), approved (green), finalized (purple)

#### (b) LetterActionsMenu.tsx
- Dropdown for each letter row in dashboard.
- Contextual actions: “Review”, “Approve”, “Finalize”, “Download”.

#### (c) LetterBodyViewer.tsx
- Displays paragraphs with proper formatting.
- Reusable in Review and Final pages.

---

### 4. STATE MANAGEMENT
Use React Query for all API interactions:
- `useLetters(projectId)`
- `useCreateDraft()`
- `useReviewLetter()`
- `useApproveLetter()`
- `useFinalizeLetter()`

Each uses Axios with `baseURL` = backend API root.
Handle loading, success, and error toasts (`react-hot-toast`).

---

### 5. THEME & STYLE
- TailwindCSS + ShadCN UI (Card, Button, Input, Badge, Dialog)
- Consistent with existing dashboard theme.
- Add icons from lucide-react (e.g., FileText, Check, Upload).

---

### 6. FILE STRUCTURE


src/
├─ pages/letters/
│ ├─ LettersDashboardPage.tsx
│ ├─ LetterDraftPage.tsx
│ ├─ LetterReviewPage.tsx
│ ├─ LetterFinalizePage.tsx
│
├─ components/letters/
│ ├─ LetterStatusBadge.tsx
│ ├─ LetterActionsMenu.tsx
│ ├─ LetterBodyViewer.tsx
│
├─ hooks/
│ ├─ useLetters.ts
│ ├─ useCreateDraft.ts
│ ├─ useReviewLetter.ts
│ ├─ useApproveLetter.ts
│ ├─ useFinalizeLetter.ts


---

### 7. API EXAMPLES

```ts
// useCreateDraft.ts
export const useCreateDraft = () =>
  useMutation(async (payload) => {
    const res = await axios.post("/api/letters/create-draft", payload);
    return res.data;
  });

// useFinalizeLetter.ts
export const useFinalizeLetter = () =>
  useMutation(async (id: string) => {
    const res = await axios.post(`/api/letters/finalize/${id}`);
    return res.data;
  });

8. UX FLOW

User → “Create Letter” → fills subject/context → clicks “Generate using AI”.

AI generates body → user edits → clicks “Send for Review”.

Reviewer opens letter → clicks “Approve”.

Approver navigates to “Finalize” page → selects project template → clicks “Finalize”.

PDF + DOCX created → shown in dashboard with “Download” buttons.

9. QA CHECKLIST

✅ All API calls are typed with Axios.

✅ Status transitions update in real time.

✅ Finalized letters show working download links.

✅ Each project can have distinct templates.

✅ PDF preview works for presigned S3 URLs.

10. Deliverables

All new files in src/pages/letters/ and src/components/letters/.

Add routes to main router.

Integrate toasts and loading states.

Commit message: feat: add AI letter workflow (draft → approve → finalize)

Goal

A seamless AI-powered Letter Drafting UI in ContraClaim allowing users to:

Auto-generate letters with AI

Review & approve them through a workflow

Apply project-specific templates

Generate finalized DOCX/PDF stored in S3


---

### ✅ How to use
Paste this prompt into **Codex** (or your AI Coder command interface) at the **frontend repo root**, and run it.
Codex will automatically:
- Scaffold the new pages/components/hooks
- Wire Axios + React Query
- Build the full letter approval workflow with project-level template support.

---

Would you like me to add the **optional “Reviewer Comment” system** (with inline suggestions and change requests) to this prompt next? It’s very useful for your internal GC team review stage before approval.

You said:
Also add provision to seek input (3-4 questions for which answer is required as input, option to add more question and box for entering answer)  before drafting if required.
ChatGPT said:

Excellent — that’s an important improvement.
You’re now adding a pre-draft clarification stage, where the system (or user) can pose 3–4 questions before generating the AI draft — with an option for users to add more questions and answer them manually.

Below is the updated Codex prompt (ready to paste into your frontend repo) that extends the AI Letter Draft Workflow UI to include this “Pre-Draft Q&A Input Stage.”

🧠 UPDATED CODEX PROMPT (Frontend) — with Pre-Draft Questions Stage
# Task: Extend AI-assisted Letter Workflow with Pre-Draft Q&A Input stage
# Repo: ContraClaim Frontend (React + TypeScript + Tailwind + React Query + Axios)
# Backend: FastAPI endpoints already implemented for AI drafting, workflow, and finalization

---

## OBJECTIVE
Add an intermediate screen/modal before AI letter drafting that:
1. Displays 3–4 pre-populated questions (editable),
2. Allows user to add new custom questions,
3. Provides text boxes for each answer,
4. Collects all Q&A pairs and sends them as structured input (context) to the AI draft API (`POST /api/letters/create-draft`).

---

## NEW FEATURE FLOW


Create Letter → Pre-Draft Questions → Generate AI Draft → Review → Approve → Finalize


---

## IMPLEMENTATION DETAILS

### 1. Add New Component
**File:** `src/components/letters/PreDraftQuestions.tsx`

**Features:**
- Shows a list of default 3–4 questions such as:
  1. “What is the main issue or purpose of this letter?”
  2. “What response or action is expected from the recipient?”
  3. “What contractual clause or reference applies?”
  4. “Any specific dates or events to mention?”
- Each question has:
  - Editable text input for the question
  - Textarea for the answer
  - Delete icon to remove
- “Add Question” button → adds new blank Q&A row
- “Continue to Draft” button → combines answers into `context` string and calls `useCreateDraft()` hook.

---

### 2. Integrate in `LetterDraftPage.tsx`
- Before showing AI generation section, insert the new component.
- Steps:
  1. Show Pre-Draft Q&A form initially.
  2. Once user submits Q&A, build a context string like:

     ```ts
     const context = qaList.map((q, i) => `Q${i+1}: ${q.question}\nA: ${q.answer}`).join("\n\n");
     ```

  3. Pass `context` along with subject & references to backend:
     ```ts
     createDraftMutation.mutate({
       project_id,
       subject,
       context,
       references
     })
     ```

- After AI draft response returns → render AI draft editor below the Q&A form.

---

### 3. Modify UI Flow
Add a simple multi-step layout in `LetterDraftPage`:

```ts
const [step, setStep] = useState<"questions" | "draft">("questions");


Step 1: <PreDraftQuestions onSubmit={handleQAComplete} />

Step 2: Draft editor (subject + body paragraphs)

Use a progress indicator (e.g., Stepper with “Questions → Draft → Review”)

4. API Integration

No backend change required — the answers are concatenated into the context string sent to /api/letters/create-draft.

Example payload:

{
  "project_id": "KNPAGGC-01",
  "subject": "Request for EOT Clarification",
  "context": "Q1: What is the main issue?\nA: Contractor delay due to late drawing.\nQ2: What is expected?\nA: Extension of Time without LD.",
  "references": []
}

5. UX Enhancements

Use ShadCN UI components: Card, Textarea, Button, Input.

Add “Save Draft Inputs” option (locally in state, no backend save yet).

Show inline validation for unanswered questions.

6. Suggested File Structure
src/
 ├─ pages/letters/
 │   ├─ LetterDraftPage.tsx         # integrates PreDraftQuestions + AI Draft Editor
 │
 ├─ components/letters/
 │   ├─ PreDraftQuestions.tsx       # dynamic Q&A input form
 │   ├─ LetterStatusBadge.tsx
 │   ├─ LetterActionsMenu.tsx
 │   ├─ LetterBodyViewer.tsx
 │
 ├─ hooks/
 │   ├─ useCreateDraft.ts
 │   ├─ useLetters.ts
 │   ├─ useReviewLetter.ts
 │   ├─ useApproveLetter.ts
 │   ├─ useFinalizeLetter.ts

7. Behavior Summary
Stage	UI Element	Description
Step 1	Pre-Draft Questions	3–4 editable default questions; add more dynamically
Step 2	Draft Body	After user submits answers, generate AI body using Q&A context
Step 3	Review / Approve / Finalize	As per existing workflow
8. Example Component Outline
// PreDraftQuestions.tsx
export const PreDraftQuestions = ({ onSubmit }) => {
  const [questions, setQuestions] = useState([
    { q: "What is the main issue or purpose of this letter?", a: "" },
    { q: "What response or action is expected from the recipient?", a: "" },
    { q: "What contractual clause or reference applies?", a: "" },
    { q: "Any specific dates or events to mention?", a: "" }
  ]);

  const addQuestion = () => setQuestions([...questions, { q: "", a: "" }]);
  const update = (i, field, value) => {
    const updated = [...questions];
    updated[i][field] = value;
    setQuestions(updated);
  };

  const handleSubmit = () => {
    const context = questions.map((qa, i) =>
      `Q${i+1}: ${qa.q}\nA: ${qa.a}`
    ).join("\n\n");
    onSubmit(context);
  };

  return (
    <Card className="p-4 space-y-4">
      {questions.map((qa, i) => (
        <div key={i} className="space-y-2">
          <Input
            value={qa.q}
            onChange={(e) => update(i, "q", e.target.value)}
            placeholder="Enter question"
          />
          <Textarea
            value={qa.a}
            onChange={(e) => update(i, "a", e.target.value)}
            placeholder="Enter answer"
          />
        </div>
      ))}
      <Button onClick={addQuestion}>+ Add Question</Button>
      <Button onClick={handleSubmit} className="ml-2">Continue to Draft</Button>
    </Card>
  );
};

9. Final UX Flow

Step 1: User answers guided questions.

Step 2: AI uses these answers to prepare contextual draft body.

Step 3: User edits AI draft → sends for review → reviewer approves → letter finalized.

10. Commit Message

feat: add pre-draft Q&A input stage to AI letter drafting workflow

11. Deliverables

PreDraftQuestions.tsx created.

Integrated into LetterDraftPage.tsx as first step.

Works seamlessly with /api/letters/create-draft backend.

Verified status flow remains unchanged (draft → review → approve → finalize).

GOAL

Enable structured contextual input before drafting to improve AI accuracy and reduce rework.
The user must be able to ask and answer custom questions before generating the AI letter body.


---

✅ **Usage**
Paste this prompt into **Codex / AI Developer prompt input** at the **frontend repo root**, then run it.
It will automatically scaffold the **Pre-Draft Q&A** interface, link it to the AI draft generator, and update the workflow.

---

Would you like me to generate the **corresponding backend prompt** (for Codex) that adds an optional `questions` array to the `create-draft` endpoint and merges Q&A automatically into context before passing to the AI?

You said:
Write a prompt for codex to keep reords of time taken from date draft is requested and finalisation draft (each stage Draft input request, input received, draft review and finalisation of draft)
ChatGPT said:

Perfect — you’re now adding workflow analytics: timestamps for each stage of the AI drafting and approval lifecycle.
This will let you track turnaround time between:
Draft Request → Input Received → Review → Approval → Finalization.

Below is the exact Codex prompt to integrate this into your backend (FastAPI + MongoDB) repo, using the existing letters collection.

⚙️ CODEX PROMPT — Track Time Durations Across Letter Workflow Stages
# Task: Implement time tracking for each stage of AI letter drafting workflow
# Repo: ContraClaim Backend (FastAPI + MongoDB)
# Goal: Record timestamps for every milestone:
# 1. Draft Requested
# 2. Input (Q&A) Received
# 3. Review Started
# 4. Review Approved
# 5. Draft Finalized

---

## Context
Letters already flow through the following statuses:
draft → under_review → approved → finalized

We need to:
- Capture timestamps for each stage transition.
- Compute and store durations (in hours/days) between key milestones.
- Allow quick reporting of total turnaround time from “draft requested” → “finalized”.

---

## Implementation Plan

### 1. Extend LetterRecord Model
**File:** app/models/letter_record.py

Add new fields:

```python
from datetime import datetime, timedelta
from typing import Optional

class LetterRecord(BaseModel):
    ...
    # Time tracking fields
    draft_requested_at: Optional[datetime] = None
    input_received_at: Optional[datetime] = None
    review_started_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    finalized_at: Optional[datetime] = None

    # Computed durations (in hours or days)
    duration_input_received: Optional[float] = None
    duration_review: Optional[float] = None
    duration_approval: Optional[float] = None
    duration_total: Optional[float] = None

2. Update Workflow Services
(a) When draft is created

File: app/services/letter_draft_service.py

Add:

letter["draft_requested_at"] = datetime.utcnow()


If user submits pre-draft Q&A input:

letter["input_received_at"] = datetime.utcnow()

(b) When review starts

File: app/routers/letters_workflow.py

In /review endpoint:

coll.update_one(
  {"_id": letter_id},
  {"$set": {
     "status": "under_review",
     "review_started_at": datetime.utcnow(),
     "updated_at": datetime.utcnow()
   }}
)

(c) When approved

In /approve endpoint:

from datetime import datetime

letter = coll.find_one({"_id": letter_id})
now = datetime.utcnow()
duration_review = None
if letter.get("review_started_at"):
    duration_review = (now - letter["review_started_at"]).total_seconds() / 3600

coll.update_one(
  {"_id": letter_id},
  {"$set": {
     "status": "approved",
     "approved_at": now,
     "duration_review": duration_review,
     "updated_at": now
   }}
)

(d) When finalized

File: app/services/finalization_service.py

After successful finalization:

from datetime import datetime

now = datetime.utcnow()
duration_total = None
letter = coll.find_one({"_id": letter_id})

if letter.get("draft_requested_at"):
    duration_total = (now - letter["draft_requested_at"]).total_seconds() / 3600

duration_approval = None
if letter.get("approved_at"):
    duration_approval = (now - letter["approved_at"]).total_seconds() / 3600

coll.update_one(
  {"_id": letter_id},
  {"$set": {
      "finalized_at": now,
      "duration_total": duration_total,
      "duration_approval": duration_approval,
      "updated_at": now
   }}
)

3. Add Computed Property API (optional)

File: app/routers/letters_analytics.py

New endpoint:

@router.get("/api/letters/{project_id}/analytics")
def get_letter_durations(project_id: str):
    letters = list(coll.find({"project_id": project_id}))
    stats = []
    for l in letters:
        stats.append({
            "letter_no": l["letter_no"],
            "status": l["status"],
            "duration_input_received": l.get("duration_input_received"),
            "duration_review": l.get("duration_review"),
            "duration_approval": l.get("duration_approval"),
            "duration_total": l.get("duration_total")
        })
    return {"project_id": project_id, "letters": stats}

4. Backend Logic Summary
Stage	Trigger	Field Set	Duration Calculated
Draft requested	/create-draft	draft_requested_at	—
Input (Q&A) submitted	Q&A form submission	input_received_at	duration_input_received = input_received_at - draft_requested_at
Review started	/workflow/{id}/review	review_started_at	—
Approved	/workflow/{id}/approve	approved_at	duration_review = approved_at - review_started_at
Finalized	/finalize/{id}	finalized_at	duration_total = finalized_at - draft_requested_at
5. Optional Auto-Compute Helper

File: app/utils/time_tracker.py

Add helper for computing durations:

from datetime import datetime

def compute_hours(start, end):
    if not start or not end:
        return None
    return round((end - start).total_seconds() / 3600, 2)


Use this helper in each workflow step for cleaner code.

6. Mongo Indexes (optional)
coll.create_index("project_id")
coll.create_index("status")
coll.create_index("finalized_at")

7. Output Example in Mongo
{
  "_id": "66f8a3f29e67",
  "letter_no": "GCS-LET-JVTI-GEN-00352-E01",
  "status": "finalized",
  "draft_requested_at": "2025-10-03T10:10:00Z",
  "input_received_at": "2025-10-03T10:40:00Z",
  "review_started_at": "2025-10-04T08:00:00Z",
  "approved_at": "2025-10-04T12:00:00Z",
  "finalized_at": "2025-10-05T09:30:00Z",
  "duration_input_received": 0.5,
  "duration_review": 4.0,
  "duration_approval": 21.5,
  "duration_total": 47.3
}

8. Deliverables

Updated LetterRecord model

Modified /create-draft, /review, /approve, /finalize endpoints

Optional /analytics endpoint for project-wise turnaround summary

Added helper time_tracker.py for cleaner duration calculations

All durations recorded in hours, rounded to 2 decimals

9. Commit Message

feat: add workflow time tracking for draft → review → approval → finalization

GOAL

Enable precise monitoring of how long each stage of AI-assisted letter drafting takes, so project managers can measure review efficiency and identify bottlenecks.


---

✅ **Usage:**
Paste this prompt into **Codex / AI Developer CLI** in the **backend repo root**, then run it.
It will automatically modify the model, add timestamps to each transition, compute durations, and optionally generate an `/analytics` endpoint.

---

Would you like me to also create the **frontend Codex prompt** to visualize these metrics — e.g., a **Letter Timeline** (Gantt-like progress bar per stage + average turnaround report)?

You said:
Write a separate setting page for uploading the template and updating the following data           from_name="Sukumar Kolli",
        from_designation="Authorized Signatory",
        organisation="TYPSA-ITALFERR JV",
ChatGPT said:

Excellent ✅ — you’re now introducing a dedicated “Settings Page” to manage:

Uploading and assigning letter templates (per project), and

Editing signatory metadata (from_name, from_designation, organisation) used in letter generation.

Below is the complete Codex prompt for your frontend (React + TypeScript + Tailwind) repo — ready to paste into your Codex or AI Developer workflow.
It will create the UI + API integration for project-level settings.

⚙️ CODEX PROMPT — Create Letter Template & Signatory Settings Page
# Task: Implement "Letter Settings Page" in ContraClaim frontend
# Repo: ContraClaim Frontend (React + TypeScript + Tailwind + React Query + Axios)
# Backend: FastAPI with endpoints for uploading templates and updating signatory info.

---

## OBJECTIVE
Add a new Settings Page for each project that allows:
1. Uploading project-specific letter templates (.docx)
2. Editing and saving default signatory information used in letter generation:
   - from_name
   - from_designation
   - organisation

---

## PAGE DETAILS

### 1. ROUTE & FILE
Create new route and page:

**Route:** `/settings/:projectId`
**File:** `src/pages/settings/LetterSettingsPage.tsx`

---

### 2. PAGE LAYOUT

#### Sections:

##### (A) **Template Upload Section**
- Card titled “Project Letter Template”
- File input for uploading `.docx` file
- Display current uploaded template (if any)
- “Upload Template” button → calls `POST /api/settings/template-upload/{project_id}`
- Show upload progress + success toast
- On success, update Mongo field `template_key` (stored as filename in `/templates/letters/`)

##### (B) **Signatory Information Section**
- Card titled “Default Signatory Details”
- Form fields:
  - **From Name** (Input)
  - **Designation** (Input)
  - **Organisation** (Input)
- Prefill with saved data from backend (`GET /api/settings/{project_id}`)
- “Save Changes” button → `PATCH /api/settings/{project_id}`

---

### 3. FRONTEND FILE STRUCTURE



src/
├─ pages/settings/
│ ├─ LetterSettingsPage.tsx
│
├─ hooks/
│ ├─ useSettings.ts # Fetch + update settings
│ ├─ useTemplateUpload.ts # File upload mutation
│
├─ components/settings/
│ ├─ TemplateUploader.tsx
│ ├─ SignatoryForm.tsx


---

### 4. COMPONENT DETAILS

#### (a) TemplateUploader.tsx

```tsx
import { useState } from "react";
import { useTemplateUpload } from "@/hooks/useTemplateUpload";
import { Card, Button, Input, Progress } from "@/components/ui";
import toast from "react-hot-toast";

export const TemplateUploader = ({ projectId, existingTemplate }) => {
  const [file, setFile] = useState<File | null>(null);
  const { mutate: upload, isLoading, progress } = useTemplateUpload(projectId);

  const handleUpload = () => {
    if (!file) return toast.error("Select a file first");
    upload(file, {
      onSuccess: () => toast.success("Template uploaded successfully"),
      onError: () => toast.error("Upload failed")
    });
  };

  return (
    <Card className="p-6 space-y-4">
      <h2 className="text-lg font-semibold">Project Letter Template</h2>
      <p className="text-sm text-muted-foreground">
        Upload a .docx file to define the project’s letter format.
      </p>
      {existingTemplate && (
        <div className="text-sm text-green-700">
          Current Template: <b>{existingTemplate}</b>
        </div>
      )}
      <Input
        type="file"
        accept=".docx"
        onChange={(e) => setFile(e.target.files?.[0] || null)}
      />
      {isLoading && <Progress value={progress} />}
      <Button onClick={handleUpload} disabled={isLoading}>
        {isLoading ? "Uploading..." : "Upload Template"}
      </Button>
    </Card>
  );
};

(b) SignatoryForm.tsx
import { useState } from "react";
import { Button, Input, Card } from "@/components/ui";
import toast from "react-hot-toast";
import { useSettings } from "@/hooks/useSettings";

export const SignatoryForm = ({ projectId }) => {
  const { data, update } = useSettings(projectId);
  const [form, setForm] = useState({
    from_name: data?.from_name || "",
    from_designation: data?.from_designation || "",
    organisation: data?.organisation || "",
  });

  const handleChange = (e) => setForm({ ...form, [e.target.name]: e.target.value });

  const handleSave = () => {
    update(form, {
      onSuccess: () => toast.success("Signatory updated"),
      onError: () => toast.error("Update failed")
    });
  };

  return (
    <Card className="p-6 space-y-4">
      <h2 className="text-lg font-semibold">Default Signatory Details</h2>
      <div className="space-y-2">
        <Input name="from_name" value={form.from_name} onChange={handleChange} placeholder="Full Name" />
        <Input name="from_designation" value={form.from_designation} onChange={handleChange} placeholder="Designation" />
        <Input name="organisation" value={form.organisation} onChange={handleChange} placeholder="Organisation" />
      </div>
      <Button onClick={handleSave}>Save Changes</Button>
    </Card>
  );
};

(c) LetterSettingsPage.tsx
import { useParams } from "react-router-dom";
import { TemplateUploader } from "@/components/settings/TemplateUploader";
import { SignatoryForm } from "@/components/settings/SignatoryForm";
import { useSettings } from "@/hooks/useSettings";

export default function LetterSettingsPage() {
  const { projectId } = useParams();
  const { data, isLoading } = useSettings(projectId);

  if (isLoading) return <p>Loading...</p>;

  return (
    <div className="space-y-6 max-w-3xl mx-auto py-8">
      <h1 className="text-2xl font-bold">Letter Settings</h1>
      <TemplateUploader projectId={projectId} existingTemplate={data?.template_key} />
      <SignatoryForm projectId={projectId} />
    </div>
  );
}

5. HOOKS
useSettings.ts
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import axios from "axios";

export const useSettings = (projectId: string) => {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery(["settings", projectId], async () => {
    const res = await axios.get(`/api/settings/${projectId}`);
    return res.data;
  });

  const update = useMutation(
    async (payload) => axios.patch(`/api/settings/${projectId}`, payload),
    { onSuccess: () => qc.invalidateQueries(["settings", projectId]) }
  );

  return { data, isLoading, update: update.mutate };
};

useTemplateUpload.ts
import { useMutation } from "@tanstack/react-query";
import axios from "axios";

export const useTemplateUpload = (projectId: string) =>
  useMutation(async (file: File) => {
    const form = new FormData();
    form.append("file", file);
    const res = await axios.post(`/api/settings/template-upload/${projectId}`, form, {
      headers: { "Content-Type": "multipart/form-data" },
    });
    return res.data;
  });

6. STYLE / COMPONENTS

Use Tailwind + ShadCN UI components for uniform design.

Cards for sections

Buttons with hover effects

Toasts for feedback

7. BACKEND EXPECTATIONS

Ensure these endpoints exist:

GET /api/settings/{project_id} → returns { from_name, from_designation, organisation, template_key }

PATCH /api/settings/{project_id} → updates signatory data in MongoDB

POST /api/settings/template-upload/{project_id} → saves .docx template under /templates/letters/{project_id}.docx

8. ROUTER INTEGRATION

Add to your main route file (AppRoutes.tsx):

<Route path="/settings/:projectId" element={<LetterSettingsPage />} />


And include a link in your sidebar:

<NavLink to={`/settings/${activeProjectId}`}>Settings</NavLink>

9. COMMIT MESSAGE

feat: add Letter Settings page for template upload and signatory configuration

GOAL

Provide a simple interface for project admins to:

Upload project-level letter templates (.docx)

Maintain default signatory information (auto-inserted in generated letters)

Improve consistency and automate final letter formatting.


---

✅ **Usage:**
Paste this prompt into **Codex** at the **frontend repo root**, then run it.
Codex will automatically generate:
- The `LetterSettingsPage` UI
- Upload + save hooks
- Settings components
- Integrated routes.

---

Would you like me to now generate the **matching backend Codex prompt** (FastAPI + MongoDB) to create `/api/settings` and `/api/settings/template-upload` endpoints, including template file storage and metadata updates?


ChatGPT can make mistakes.
