# Complete Letter Drafting Process Analysis & Improvement Plan

## Document Overview

**Purpose**: Comprehensive analysis of the current letter drafting workflow with detailed improvement recommendations and a complete backend redesign plan.

**Date**: February 2025  
**Status**: Analysis Complete - Ready for Implementation

---

## Table of Contents

1. [Current Process Overview](#current-process-overview)
2. [Detailed Step-by-Step Workflow](#detailed-step-by-step-workflow)
3. [Current Architecture Analysis](#current-architecture-analysis)
4. [Identified Gaps & Issues](#identified-gaps--issues)
5. [Improvement Recommendations](#improvement-recommendations)
6. [New Backend Architecture Plan](#new-backend-architecture-plan)
7. [Implementation Roadmap](#implementation-roadmap)

---

## 1. Current Process Overview

### 1.1 Workflow Stages

The letter drafting process follows a **7-stage workflow**:

```
All Letters → Input → Strategy → Draft → Review → Approval → Completed
```

**Stage Descriptions**:

- **Input**: Gather requirements and clarifications
- **Strategy**: Generate strategic plan with AI assistance
- **Draft**: Create letter content using AI
- **Review**: Reviewer feedback and validation
- **Approval**: Final sign-off
- **Completed**: Archived and finalized

### 1.2 Key Components

**Frontend (React + TypeScript)**:

- `LetterStrategicPlanPage.tsx` - Strategy planning interface
- `LetterDraftPage.tsx` - Draft editing interface
- `LinkedDocumentSelector` - Context document selection
- `StrategyPlanDisplay` - Structured plan visualization
- `BackgroundSummary` - Context aggregation display

**Backend (FastAPI + Python)**:

- `letter_pipeline.py` - LangGraph-inspired orchestration
- `ai_service.py` - AI generation coordination
- `letter_service.py` - Letter CRUD operations
- `falkor_graph_service.py` - Knowledge graph integration
- `contract_service.py` - Contract clause retrieval
- `retrieval_service.py` - Vector search & RAG

**Data Stores**:

- MongoDB - Letter metadata, plans, drafts
- Qdrant - Vector embeddings for semantic search
- FalkorDB - Knowledge graph for relationships
- S3 - Final document storage (DOCX/PDF)

---

## 2. Detailed Step-by-Step Workflow

### Step 1: Letter Creation & Input Stage

**User Actions**:

1. User creates new letter with basic metadata
2. Fills in subject, recipient, initial requirements
3. Responds to input requests for clarifications

**System Actions**:

- Creates letter record in MongoDB
- Sets status to "Input"
- Records `draft_requested_at` timestamp

**Data Captured**:

```json
{
  "letter_no": "GCS-LET-JVTI-GEN-00352-E01",
  "subject": "Request for EOT Clarification",
  "recipient": "Mr. Sushil Kumar, MD UPMRC",
  "content": "Initial requirements text",
  "status": "Input",
  "draft_requested_at": "2025-02-03T10:00:00Z"
}
```

**Duration Tracking**: `draft_requested_at` → `input_received_at`

---

### Step 2: Strategic Planning Stage

**User Actions**:

1. Select role perspective (Contractor/Engineer/Employer)
2. Generate three-way context (AI analyzes correspondence history)
3. Select supporting documents via `LinkedDocumentSelector`
4. Generate background summary
5. Generate strategic plan using LangGraph
6. Review and edit plan
7. Approve plan to proceed

**System Actions**:

#### 2.1 Role Selection & Context Generation

```python
# POST /api/strategy/contexts/{letter_id}
# Generates contractor, engineer, employer perspectives
```

**AI Process**:

- Retrieves conversation chain (previous letters)
- Analyzes from each party's viewpoint
- Generates contextual summaries

**Output**:

```json
{
  "contractor_context": "Contractor's perspective summary...",
  "engineer_context": "Engineer's perspective summary...",
  "employer_context": "Employer's perspective summary..."
}
```

#### 2.2 Document Selection

- User selects relevant contracts, clauses, previous letters
- System validates scope (organization/project match)
- Stores `context_document_ids` array

#### 2.3 Background Generation

```python
# POST /api/ai-assistant/langgraph/background
# analysis_only=true
```

**Pipeline Nodes**:

1. **load_state**: Verify letter exists
2. **collect_context**:
   - Fetch selected documents
   - Load conversation chain
   - Retrieve FalkorDB graph thread
   - Extract document comments
3. **plan_response**: Generate heuristic plan
4. **Return**: Background summary without draft

**Output**:

```json
{
  "background_summary": [
    {
      "id": "uuid",
      "text": "Document summary...",
      "type": "document",
      "documents": ["doc_id_1"]
    }
  ],
  "summary_points": ["Key point 1", "Key point 2"]
}
```

#### 2.4 Strategic Plan Generation

```python
# POST /api/ai-assistant/langgraph/strategy-plan
```

**LLM Prompt Structure**:

```
Subject: {subject}
Recipient: {recipient}
Role: {contractor/engineer/employer}

Contractor Context: {contractor_context}
Engineer Context: {engineer_context}
Employer Context: {employer_context}

Requirements: {requirements}
Linked Letters: {letter_codes}
Sources: {retrieved_sources}

Generate a structured strategic plan with:
1. TONE & APPROACH
2. CONTENT STRUCTURE
3. SPECIFIC RESPONSES
4. RISK MITIGATION
5. DESIRED OUTCOME
```

**Output Structure**:

```json
{
  "plan": "Full strategic plan text...",
  "tone_approach": {
    "overall_tone": "Firm and compliance-focused",
    "key_messaging_strategy": "...",
    "relationship_management_approach": "..."
  },
  "content_structure": {
    "opening_strategy": "...",
    "key_points_order": ["point1", "point2"],
    "contractual_references": ["Clause 4.2", "Letter XYZ"],
    "closing_approach": "..."
  },
  "specific_responses": [
    {
      "contractor_point": "Issue raised",
      "response_strategy": "How to address",
      "evidence_references": ["doc_id"],
      "contractual_basis": "Clause reference"
    }
  ],
  "risk_mitigation": {
    "legal_risks": ["Risk 1", "Risk 2"],
    "relationship_risks": ["Risk 1"],
    "project_impact_considerations": ["Impact 1"]
  },
  "desired_outcome": {
    "immediate_action": "Expected response",
    "next_steps": ["Step 1", "Step 2"],
    "fallback_positions": ["Fallback 1"]
  }
}
```

**Data Persisted**:

```json
{
  "strategy_plan": "Full plan text",
  "strategic_outline": {
    /* structured plan */
  },
  "summary_points": ["point1", "point2"],
  "strategy_role": "contractor",
  "strategy_recipient": "Employer",
  "strategy_started_at": "2025-02-03T11:00:00Z",
  "strategy_completed_at": "2025-02-03T11:15:00Z",
  "duration_strategy": 0.25
}
```

**Duration Tracking**: `input_received_at` → `strategy_completed_at`

---

### Step 3: Draft Generation Stage

**User Actions**:

1. Navigate to draft page
2. Review strategic plan
3. Optionally add pre-draft questions (3-4 Q&A pairs)
4. Generate AI draft
5. Edit draft content
6. Save and submit for review

**System Actions**:

#### 3.1 Pre-Draft Questions (Optional)

```typescript
// User can add custom questions before drafting
const questions = [
  { q: "What is the main issue?", a: "Contractor delay..." },
  { q: "What response is expected?", a: "EOT approval..." },
];
// Concatenated into context string
```

#### 3.2 Source Retrieval

```python
# Node: retrieve_sources
```

**Retrieval Strategy**:

1. **Contract Clauses** (Qdrant vector search):
   - Query: Subject + context + requirements
   - Filters: organization_id, project_id, doc_type="contract"
   - Returns: Clause chunks with metadata

2. **Correspondence History** (Hybrid search):
   - RAG Fusion strategy (dense + BM25)
   - Conversation chain letters
   - Related letters by topic

3. **Context Documents** (Curated):
   - User-selected documents
   - Summaries and keywords

**Output**:

```json
{
  "sources": [
    {
      "id": "doc_id::clause::4.2",
      "source_type": "contract_clause",
      "label": "Clause 4.2 Extension of Time",
      "snippet": "The Contractor shall be entitled...",
      "clause_number": "4.2",
      "page_numbers": [45],
      "score": 0.89
    },
    {
      "id": "letter_id::chunk::123",
      "source_type": "letter",
      "label": "Previous EOT Request (GCS-LET-341)",
      "snippet": "We requested extension...",
      "letter_id": "letter_id"
    }
  ]
}
```

#### 3.3 Draft Generation

```python
# Node: draft_letter
```

**LLM Prompt**:

```
Subject: {subject}
Recipient: {recipient}
Plan: {strategic_plan}
Requirements: {context + Q&A answers}
Sources: {formatted_sources}

Generate a formal letter body that:
- Follows the strategic plan structure
- Cites sources appropriately
- Maintains professional tone
- Addresses all key points
```

**AI Model**: Configurable (default: GPT-4o)

**Output**:

```json
{
  "subject": "Request for EOT Clarification",
  "body": "Dear Sir,\n\nWe refer to...",
  "key_points": ["Point 1", "Point 2"]
}
```

#### 3.4 Draft Review (AI Reviewer)

```python
# Node: review_draft
```

**Reviewer Checks**:

1. **Citation Validation**: Verify mentioned clauses exist in sources
2. **Source Grounding**: Ensure factual claims have evidence
3. **Placeholder Detection**: Flag TBD or unconfirmed markers
4. **Tone Analysis**: Check alignment with strategic plan

**Reviewer Prompt**:

```
Draft: {draft_body}
Sources: {sources_list}

Flag issues in format: LEVEL|message|evidence
Focus on: factual grounding, missing citations, risky commitments
```

**Output**:

```json
{
  "reviewer_findings": [
    {
      "level": "error",
      "message": "Draft references Clause 5.3 not in sources",
      "evidence": "Clause 5.3"
    },
    {
      "level": "warning",
      "message": "Contains placeholder [TBD]",
      "evidence": null
    }
  ]
}
```

**Blocking Logic**: If any `level="error"` findings, status = "needs_attention"

#### 3.5 Data Persistence

```json
{
  "draft_output": "Letter body text...",
  "draft_plan": "Strategic plan used",
  "draft_sources": [
    /* source objects */
  ],
  "reviewer_findings": [
    /* findings */
  ],
  "graph_status": "ready_for_review",
  "graph_started_at": "2025-02-03T12:00:00Z",
  "graph_completed_at": "2025-02-03T12:10:00Z",
  "duration_draft": 0.17
}
```

**Duration Tracking**: `strategy_completed_at` → `review_started_at`

---

### Step 4: Review Stage

**User Actions**:

1. Reviewer opens letter
2. Reviews draft content
3. Checks sources and citations
4. Adds comments
5. Approves or sends back for revision

**System Actions**:

- Updates status to "Review"
- Records `review_started_at`
- Stores reviewer comments
- Tracks reviewer_id

**Duration Tracking**: `review_started_at` → `approved_at`

---

### Step 5: Approval Stage

**User Actions**:

1. Approver performs final review
2. Approves letter
3. Triggers finalization

**System Actions**:

- Updates status to "Approval"
- Records `approved_at`
- Prepares for document generation

**Duration Tracking**: `approved_at` → `finalized_at`

---

### Step 6: Finalization & Document Generation

**User Actions**:

1. Select project-specific template (DOCX)
2. Confirm signatory details
3. Trigger finalization

**System Actions**:

#### 6.1 Template Selection

```python
# GET /api/settings/{project_id}
# Returns template_key for project
```

**Template Structure**:

```
/templates/letters/
  ├── KNPAGGC-01.docx
  ├── KNPCC-05.docx
  └── default.docx
```

#### 6.2 Document Generation

```python
# POST /api/letters/finalize/{letter_id}
```

**Process**:

1. Load project template (python-docx)
2. Fill placeholders:
   - `{Letter_No}` → letter_no
   - `{Date}` → current date
   - `{Subject}` → subject
   - `{To_Name}` → recipient name
   - `{To_Designation}` → recipient designation
   - `{To_Address}` → recipient address
   - `{Contract_No}` → contract reference
   - `{References}` → formatted reference list
   - `{Body}` → AI-generated draft body
   - `{From_Block}` → signatory details

3. Save DOCX to temp file
4. Convert to PDF (LibreOffice headless)
5. Upload both to S3:
   - Key: `letters/{project_id}/{year}/{month}/{letter_no}.{ext}`
6. Generate presigned URLs (1 hour expiry)
7. Update MongoDB with S3 keys and URLs

**Output**:

```json
{
  "letter_no": "GCS-LET-JVTI-GEN-00352-E01",
  "s3_key_docx": "letters/KNPAGGC-01/2025/02/GCS-LET-JVTI-GEN-00352-E01.docx",
  "s3_key_pdf": "letters/KNPAGGC-01/2025/02/GCS-LET-JVTI-GEN-00352-E01.pdf",
  "presigned_docx_url": "https://s3.amazonaws.com/...",
  "presigned_pdf_url": "https://s3.amazonaws.com/...",
  "finalized_at": "2025-02-03T14:00:00Z",
  "duration_total": 4.0
}
```

---

### Step 7: Completion

**System Actions**:

- Updates status to "Completed"
- Archives letter
- Makes documents available for download
- Syncs to FalkorDB graph

**FalkorDB Sync**:

```python
# Creates/updates nodes and edges
base_letter = {
  "code": "GCS-LET-JVTI-GEN-00352-E01",
  "direction": "outgoing",
  "subject": "Request for EOT",
  "date": "2025-02-03"
}
references = [
  {"code": "GCS-LET-341", "type": "CITES"}
]
falkor_service.upsert_letter_with_refs(base_letter, references)
```

---

## 3. Current Architecture Analysis

### 3.1 Backend Architecture

**Technology Stack**:

- **Framework**: FastAPI (Python 3.9+)
- **Database**: MongoDB (document store)
- **Vector Store**: Qdrant (embeddings)
- **Graph DB**: FalkorDB (relationships)
- **Object Storage**: AWS S3
- **AI Models**: OpenAI GPT-4o, GPT-4o-mini
- **Document Processing**: python-docx, LibreOffice

**Service Layer**:

```
┌─────────────────────────────────────────────────────────┐
│                    FastAPI Routers                       │
│  /letters  /ai-assistant  /documents  /contracts        │
└────────────────────┬────────────────────────────────────┘
                     │
┌────────────────────┴────────────────────────────────────┐
│                   Service Layer                          │
│  LetterService  AIService  DocumentService              │
│  ContractService  RetrievalService  FalkorGraphService  │
└────────────────────┬────────────────────────────────────┘
                     │
┌────────────────────┴────────────────────────────────────┐
│              AI Workflows (LangGraph-inspired)           │
│  LetterDraftGraph  StrategyPlanGraph                    │
└────────────────────┬────────────────────────────────────┘
                     │
┌────────────────────┴────────────────────────────────────┐
│                  Data Layer                              │
│  MongoDB  Qdrant  FalkorDB  S3                          │
└─────────────────────────────────────────────────────────┘
```

### 3.2 Frontend Architecture

**Technology Stack**:

- **Framework**: React 18 + TypeScript
- **Routing**: React Router v6
- **State Management**: React Query (TanStack Query)
- **UI Components**: ShadCN UI + Tailwind CSS
- **HTTP Client**: Axios

**Component Hierarchy**:

```
App
├── LetterWorkflowTabs (7 tabs)
│   ├── All Letters
│   ├── Input
│   ├── Strategy → LetterStrategicPlanPage
│   ├── Draft → LetterDraftPage
│   ├── Review → LetterReviewPage
│   ├── Approval → LetterApprovalPage
│   └── Completed
│
├── LetterStrategicPlanPage
│   ├── RoleSelector
│   ├── ContextPreviewTabs (3-way context)
│   ├── LinkedDocumentSelector
│   ├── BackgroundSummary
│   └── StrategyPlanDisplay
│
└── LetterDraftPage
    ├── PlanViewer
    ├── LetterDraftEditor
    ├── BackgroundSummary
    └── GraphStatusBadge
```

### 3.3 Data Flow

**Letter Creation → Finalization**:

```
User Input
    ↓
MongoDB (letter record)
    ↓
Strategy Generation (AI)
    ↓
Context Retrieval (Qdrant + FalkorDB)
    ↓
Draft Generation (AI + Sources)
    ↓
Review (AI Reviewer + Human)
    ↓
Approval (Human)
    ↓
Template Fill (python-docx)
    ↓
PDF Conversion (LibreOffice)
    ↓
S3 Upload (DOCX + PDF)
    ↓
MongoDB Update (URLs)
    ↓
FalkorDB Sync (Graph)
    ↓
Completed
```

---

## 4. Identified Gaps & Issues

### 4.1 Current Implementation Issues

#### **Issue 1: Template-Based Drafting (Limited AI)**

**Problem**: Current draft generation uses basic template filling rather than true RAG-powered LLM generation.

**Evidence**:

```python
# From ai_service.py (simplified)
def generate_draft(request):
    template = "Dear {recipient},\n\n{context}\n\nRegards"
    return template.format(recipient=request.recipient, context=request.context)
```

**Impact**:

- Low-quality drafts
- No clause-level citation
- Poor tone adaptation
- Limited context utilization

#### **Issue 2: Weak Source Grounding**

**Problem**: Sources are retrieved but not deeply integrated into draft generation.

**Evidence**: Draft body doesn't include inline citations like `[S1]` or `[Clause 4.2]`

**Impact**:

- Reviewers can't verify claims
- Risk of factual errors
- Manual citation work required

#### **Issue 3: No Clause-Level Extraction**

**Problem**: Contract PDFs are stored as whole documents without clause segmentation.

**Evidence**:

- No `clause_id` field in document metadata
- No clause-level vector embeddings
- Search returns full documents, not specific clauses

**Impact**:

- Imprecise retrieval
- Users must manually find relevant clauses
- Can't cite specific contract sections

#### **Issue 4: Missing Reviewer Agent Validation**

**Problem**: AI reviewer exists but findings don't block progression.

**Evidence**:

```python
# From letter_pipeline.py
reviewer_blocking = any(f.level == "error" for f in reviewer_findings)
# But status still set to "ready_for_review" even if blocking=True
```

**Impact**:

- Errors slip through to human review
- Wasted reviewer time
- Quality inconsistency

#### **Issue 5: No Version Control**

**Problem**: Editing a draft overwrites previous version; no history.

**Impact**:

- Can't track changes
- Can't revert to previous drafts
- Audit trail incomplete

#### **Issue 6: Limited Template Management**

**Problem**: Templates are file-based; no UI for upload/preview.

**Current**: Templates stored in `/templates/letters/{project_id}.docx`

**Missing**:

- Template upload UI
- Template preview
- Template versioning
- Placeholder validation

#### **Issue 7: Incomplete Time Tracking**

**Problem**: Duration calculations exist but not exposed in analytics.

**Missing**:

- Average time per stage
- Bottleneck identification
- SLA monitoring

#### **Issue 8: No Multi-Agent Parallelism**

**Problem**: All AI tasks run sequentially in single pipeline.

**Impact**:

- Slow generation (3-5 minutes total)
- Can't parallelize context generation + retrieval
- Poor user experience

---

### 4.2 Architecture Limitations

#### **Limitation 1: Monolithic Pipeline**

**Problem**: `LetterDraftGraph` is a single 1000+ line class handling all stages.

**Issues**:

- Hard to test individual nodes
- Difficult to extend
- Tight coupling

#### **Limitation 2: No Event-Driven Architecture**

**Problem**: All operations are synchronous HTTP requests.

**Issues**:

- Long request timeouts
- No background processing
- Can't handle concurrent users well

#### **Limitation 3: Inconsistent Error Handling**

**Problem**: Errors are appended to `warnings` array but not properly surfaced.

**Example**:

```python
try:
    result = await some_operation()
except Exception as exc:
    warnings.append(f"operation_failed: {exc}")
    # But execution continues!
```

#### **Limitation 4: No Caching**

**Problem**: Every request re-fetches documents, re-runs embeddings.

**Impact**:

- Slow performance
- High API costs (OpenAI embeddings)
- Poor scalability

---

## 5. Improvement Recommendations

### 5.1 Immediate Improvements (Keep Current Backend)

#### **Improvement 1: Enhance Draft Generation with True RAG**

**Current**:

```python
# Template-based
draft = template.format(context=context)
```

**Improved**:

```python
# RAG-powered with inline citations
prompt = f"""
Subject: {subject}
Strategic Plan: {plan}
Sources:
{format_sources_with_ids(sources)}

Generate a formal letter that:
1. Follows the strategic plan structure
2. Cites sources using [S1], [S2] format
3. Quotes clauses verbatim where needed
4. Maintains professional tone
"""
draft = await llm.generate(prompt, model="gpt-4o")
```

**Benefits**:

- Higher quality drafts
- Automatic citations
- Better source utilization

---

#### **Improvement 2: Implement Clause Extraction Pipeline**

**New Service**: `ClauseExtractionService`

**Process**:

1. Upload contract PDF → Marker conversion → Markdown
2. Extract Table of Contents structure
3. Use GPT-4o-mini to segment clauses:
   ```python
   prompt = """
   Extract all clauses from this contract markdown.
   Return JSON array:
   [
     {
       "clause_id": "4.2",
       "heading": "Extension of Time",
       "path": ["Part A", "Section 4", "Clause 4.2"],
       "text": "Full clause text...",
       "start_offset": 1234,
       "end_offset": 2345
     }
   ]
   """
   ```
4. Store clauses in MongoDB with metadata
5. Create clause-level embeddings in Qdrant
6. Link clauses to parent document

**Benefits**:

- Precise clause retrieval
- Better citations
- Faster search

---

#### **Improvement 3: Add Reviewer Agent Blocking**

**Current**:

```python
if reviewer_blocking:
    status = "needs_attention"
# But still saves draft
```

**Improved**:

```python
if reviewer_blocking:
    raise ValidationError(
        "Draft has critical issues that must be resolved",
        findings=reviewer_findings
    )
# Don't save draft; return errors to user
```

**UI Update**:

- Show reviewer findings prominently
- Block "Submit for Review" button
- Require fixes before proceeding

---

#### **Improvement 4: Add Draft Versioning**

**Schema Update**:

```json
{
  "draft_versions": [
    {
      "version": 1,
      "body": "Draft v1 text...",
      "created_at": "2025-02-03T12:00:00Z",
      "created_by": "user_id",
      "sources": [
        /* sources */
      ],
      "reviewer_findings": [
        /* findings */
      ]
    },
    {
      "version": 2,
      "body": "Draft v2 text...",
      "created_at": "2025-02-03T13:00:00Z",
      "created_by": "user_id"
    }
  ],
  "current_version": 2
}
```

**UI**:

- Version dropdown in draft editor
- Diff view between versions
- Restore previous version option

---

#### **Improvement 5: Template Management UI**

**New Page**: `LetterSettingsPage`

**Features**:

1. Upload template (DOCX)
2. Preview template with sample data
3. Validate placeholders
4. Set default signatory info per project
5. Version templates

**Backend**:

```python
# POST /api/settings/template-upload/{project_id}
# Saves to /templates/letters/{project_id}_v{version}.docx
```

---

#### **Improvement 6: Enhanced Analytics Dashboard**

**New Endpoint**: `GET /api/analytics/letter-workflow`

**Metrics**:

- Average time per stage
- Bottleneck identification (longest stage)
- Success rate (% reaching Completed)
- Rejection rate by stage
- AI generation success rate
- Source retrieval quality (avg sources per draft)

**UI**: Charts showing:

- Time distribution by stage (bar chart)
- Workflow funnel (conversion rates)
- Trend over time (line chart)

---

### 5.2 Advanced Improvements (Require Backend Changes)

#### **Improvement 7: Event-Driven Architecture**

**Current**: Synchronous HTTP requests

**Proposed**: Event-driven with message queue

**Architecture**:

```
User Request
    ↓
FastAPI (creates job)
    ↓
Redis Queue
    ↓
Background Worker (Celery)
    ↓
Process stages asynchronously
    ↓
WebSocket notification to UI
```

**Benefits**:

- Non-blocking requests
- Better scalability
- Progress updates in real-time

---

#### **Improvement 8: Multi-Agent Parallelism**

**Current**: Sequential pipeline

**Proposed**: Parallel agent execution

**Example**:

```python
# Run in parallel
async with asyncio.TaskGroup() as tg:
    context_task = tg.create_task(generate_contexts())
    retrieval_task = tg.create_task(retrieve_sources())
    plan_task = tg.create_task(generate_plan())

# Combine results
results = await asyncio.gather(context_task, retrieval_task, plan_task)
```

**Benefits**:

- 3x faster generation
- Better resource utilization

---

#### **Improvement 9: Caching Layer**

**Add Redis Cache**:

```python
@cache(ttl=3600)
async def get_document_embeddings(doc_id):
    # Cache embeddings for 1 hour
    return await embedding_service.embed(doc_id)
```

**Cache Strategy**:

- Document embeddings (1 hour)
- Conversation chains (5 minutes)
- Strategic plans (until letter updated)
- Source retrievals (10 minutes)

---

## 6. New Backend Architecture Plan

### 6.1 Architecture Overview

**Proposed Stack**:

- **Framework**: FastAPI (keep)
- **Task Queue**: Celery + Redis
- **Database**: PostgreSQL (replace MongoDB for better transactions)
- **Vector Store**: Qdrant (keep)
- **Graph DB**: Neo4j (replace FalkorDB for better query performance)
- **Object Storage**: S3 (keep)
- **Cache**: Redis
- **AI Orchestration**: LangGraph (actual library, not facsimile)
- **Document Processing**: Marker + Docling
- **Monitoring**: Prometheus + Grafana

---

### 6.2 Service Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                      API Gateway (FastAPI)                   │
│  Authentication  Rate Limiting  Request Validation          │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────┴────────────────────────────────────┐
│                   Orchestration Layer                        │
│  LangGraph Workflows  State Management  Event Bus           │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────┴────────────────────────────────────┐
│                    Domain Services                           │
│  LetterService  StrategyService  DraftService               │
│  ReviewService  TemplateService  AnalyticsService           │
└────────────────────────┬────────────────────────────────────┘
                         │
┌────────────────────────┴────────────────────────────────────┐
│                   Infrastructure Services                    │
│  DocumentStore  VectorStore  GraphStore  ObjectStore        │
│  CacheService  QueueService  NotificationService            │
└─────────────────────────────────────────────────────────────┘
```

---

### 6.3 Database Schema (PostgreSQL)

#### **Letters Table**

```sql
CREATE TABLE letters (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    letter_no VARCHAR(100) UNIQUE NOT NULL,
    organization_id UUID NOT NULL,
    project_id UUID NOT NULL,

    -- Metadata
    subject TEXT NOT NULL,
    recipient VARCHAR(255),
    status VARCHAR(50) NOT NULL,

    -- Content
    content TEXT,
    current_draft_version INT DEFAULT 0,

    -- Strategy
    strategy_role VARCHAR(50),
    strategy_recipient VARCHAR
```
