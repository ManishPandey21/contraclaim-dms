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
    strategy_recipient VARCHAR(100),
    strategy_plan_id UUID REFERENCES strategy_plans(id),
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    draft_requested_at TIMESTAMP,
    input_received_at TIMESTAMP,
    strategy_started_at TIMESTAMP,
    strategy_completed_at TIMESTAMP,
    review_started_at TIMESTAMP,
    approved_at TIMESTAMP,
    finalized_at TIMESTAMP,
    
    -- Durations (in hours)
    duration_input DECIMAL(10,2),
    duration_strategy DECIMAL(10,2),
    duration_draft DECIMAL(10,2),
    duration_review DECIMAL(10,2),
    duration_approval DECIMAL(10,2),
    duration_total DECIMAL(10,2),
    
    -- Users
    created_by UUID NOT NULL,
    assigned_to UUID,
    reviewed_by UUID,
    approved_by UUID,
    
    -- Files
    template_key VARCHAR(255),
    s3_key_docx VARCHAR(500),
    s3_key_pdf VARCHAR(500),
    
    CONSTRAINT fk_organization FOREIGN KEY (organization_id) REFERENCES organizations(id),
    CONSTRAINT fk_project FOREIGN KEY (project_id) REFERENCES projects(id),
    CONSTRAINT fk_created_by FOREIGN KEY (created_by) REFERENCES users(id)
);

CREATE INDEX idx_letters_status ON letters(status);
CREATE INDEX idx_letters_org_project ON letters(organization_id, project_id);
CREATE INDEX idx_letters_created_at ON letters(created_at DESC);
```

#### **Draft Versions Table**
```sql
CREATE TABLE draft_versions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    letter_id UUID NOT NULL REFERENCES letters(id) ON DELETE CASCADE,
    version INT NOT NULL,
    
    -- Content
    body TEXT NOT NULL,
    key_points JSONB,
    
    -- Metadata
    created_at TIMESTAMP DEFAULT NOW(),
    created_by UUID NOT NULL REFERENCES users(id),
    
    -- AI Generation
    model_used VARCHAR(100),
    prompt_template TEXT,
    generation_time_ms INT,
    
    -- Sources
    sources JSONB,
    
    -- Review
    reviewer_findings JSONB,
    is_approved BOOLEAN DEFAULT FALSE,
    
    CONSTRAINT unique_letter_version UNIQUE (letter_id, version)
);

CREATE INDEX idx_draft_versions_letter ON draft_versions(letter_id, version DESC);
```

#### **Strategy Plans Table**
```sql
CREATE TABLE strategy_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    letter_id UUID NOT NULL REFERENCES letters(id) ON DELETE CASCADE,
    
    -- Plan Content
    plan_text TEXT NOT NULL,
    tone_approach JSONB,
    content_structure JSONB,
    specific_responses JSONB,
    risk_mitigation JSONB,
    desired_outcome JSONB,
    
    -- Context
    contractor_context TEXT,
    engineer_context TEXT,
    employer_context TEXT,
    summary_points JSONB,
    
    -- Metadata
    created_at TIMESTAMP DEFAULT NOW(),
    created_by UUID NOT NULL REFERENCES users(id),
    approved_at TIMESTAMP,
    approved_by UUID REFERENCES users(id),
    
    -- AI Generation
    model_used VARCHAR(100),
    run_id VARCHAR(100),
    trace JSONB,
    
    CONSTRAINT fk_letter FOREIGN KEY (letter_id) REFERENCES letters(id)
);

CREATE INDEX idx_strategy_plans_letter ON strategy_plans(letter_id);
```

#### **Context Documents Table**
```sql
CREATE TABLE letter_context_documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    letter_id UUID NOT NULL REFERENCES letters(id) ON DELETE CASCADE,
    document_id UUID NOT NULL,
    
    -- Selection
    selected_at TIMESTAMP DEFAULT NOW(),
    selected_by UUID NOT NULL REFERENCES users(id),
    selection_source VARCHAR(50), -- 'manual', 'ai_suggested', 'fallback'
    
    -- Metadata
    document_type VARCHAR(50), -- 'contract', 'letter', 'clause'
    relevance_score DECIMAL(5,4),
    
    CONSTRAINT unique_letter_document UNIQUE (letter_id, document_id)
);

CREATE INDEX idx_context_docs_letter ON letter_context_documents(letter_id);
```

#### **Clauses Table** (New)
```sql
CREATE TABLE contract_clauses (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_id UUID NOT NULL,
    
    -- Clause Identity
    clause_id VARCHAR(50) NOT NULL,
    clause_number VARCHAR(50),
    heading TEXT,
    
    -- Hierarchy
    path JSONB, -- ["Part A", "Section 4", "Clause 4.2"]
    parent_clause_id UUID REFERENCES contract_clauses(id),
    level INT,
    
    -- Content
    text TEXT NOT NULL,
    start_offset INT,
    end_offset INT,
    
    -- Metadata
    page_numbers INT[],
    extracted_at TIMESTAMP DEFAULT NOW(),
    extraction_method VARCHAR(50), -- 'ai', 'manual', 'ocr'
    
    -- Embeddings
    embedding_id VARCHAR(100), -- Qdrant point ID
    
    CONSTRAINT unique_doc_clause UNIQUE (document_id, clause_id)
);

CREATE INDEX idx_clauses_document ON contract_clauses(document_id);
CREATE INDEX idx_clauses_number ON contract_clauses(clause_number);
```

#### **Templates Table** (New)
```sql
CREATE TABLE letter_templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES projects(id),
    
    -- Template Info
    name VARCHAR(255) NOT NULL,
    description TEXT,
    version INT DEFAULT 1,
    
    -- File
    s3_key VARCHAR(500) NOT NULL,
    file_name VARCHAR(255),
    file_size_bytes BIGINT,
    
    -- Placeholders
    placeholders JSONB, -- ["Letter_No", "Date", "Subject", ...]
    
    -- Signatory Defaults
    default_from_name VARCHAR(255),
    default_from_designation VARCHAR(255),
    default_organization VARCHAR(255),
    
    -- Status
    is_active BOOLEAN DEFAULT TRUE,
    is_default BOOLEAN DEFAULT FALSE,
    
    -- Metadata
    created_at TIMESTAMP DEFAULT NOW(),
    created_by UUID NOT NULL REFERENCES users(id),
    updated_at TIMESTAMP DEFAULT NOW(),
    
    CONSTRAINT unique_project_template_version UNIQUE (project_id, name, version)
);

CREATE INDEX idx_templates_project ON letter_templates(project_id, is_active);
```

---

### 6.4 LangGraph Workflow Definition

**New Approach**: Use actual LangGraph library instead of facsimile

#### **Strategy Plan Workflow**
```python
from langgraph.graph import StateGraph, END
from typing import TypedDict, List, Dict, Any

class StrategyState(TypedDict):
    letter_id: str
    role: str
    subject: str
    recipient: str
    contractor_context: str
    engineer_context: str
    employer_context: str
    requirements: str
    
    # Outputs
    plan: str
    tone_approach: Dict[str, Any]
    content_structure: Dict[str, Any]
    specific_responses: List[Dict[str, Any]]
    risk_mitigation: Dict[str, Any]
    desired_outcome: Dict[str, Any]
    summary_points: List[str]
    warnings: List[str]

# Define nodes
async def load_letter(state: StrategyState) -> StrategyState:
    """Load letter and validate"""
    letter = await letter_service.get_letter(state["letter_id"])
    state["subject"] = letter.subject
    state["recipient"] = letter.recipient
    return state

async def generate_contexts(state: StrategyState) -> StrategyState:
    """Generate three-way contexts if missing"""
    if not state.get("contractor_context"):
        contexts = await context_service.generate_contexts(state["letter_id"])
        state.update(contexts)
    return state

async def analyze_tone(state: StrategyState) -> StrategyState:
    """Determine appropriate tone"""
    prompt = f"""
    Analyze the situation and recommend tone:
    Subject: {state['subject']}
    Role: {state['role']}
    Context: {state['contractor_context'][:500]}
    """
    tone = await llm.generate(prompt)
    state["tone_approach"] = parse_tone_response(tone)
    return state

async def structure_content(state: StrategyState) -> StrategyState:
    """Define content structure"""
    # ... implementation
    return state

async def identify_responses(state: StrategyState) -> StrategyState:
    """Identify specific responses needed"""
    # ... implementation
    return state

async def assess_risks(state: StrategyState) -> StrategyState:
    """Assess legal and relationship risks"""
    # ... implementation
    return state

async def define_outcome(state: StrategyState) -> StrategyState:
    """Define desired outcome"""
    # ... implementation
    return state

async def compile_plan(state: StrategyState) -> StrategyState:
    """Compile all sections into final plan"""
    plan_sections = [
        "1. TONE & APPROACH",
        format_section(state["tone_approach"]),
        "2. CONTENT STRUCTURE",
        format_section(state["content_structure"]),
        # ... etc
    ]
    state["plan"] = "\n\n".join(plan_sections)
    return state

# Build graph
workflow = StateGraph(StrategyState)

# Add nodes
workflow.add_node("load_letter", load_letter)
workflow.add_node("generate_contexts", generate_contexts)
workflow.add_node("analyze_tone", analyze_tone)
workflow.add_node("structure_content", structure_content)
workflow.add_node("identify_responses", identify_responses)
workflow.add_node("assess_risks", assess_risks)
workflow.add_node("define_outcome", define_outcome)
workflow.add_node("compile_plan", compile_plan)

# Define edges
workflow.set_entry_point("load_letter")
workflow.add_edge("load_letter", "generate_contexts")
workflow.add_edge("generate_contexts", "analyze_tone")

# Parallel execution
workflow.add_edge("analyze_tone", "structure_content")
workflow.add_edge("analyze_tone", "identify_responses")
workflow.add_edge("analyze_tone", "assess_risks")

# Converge
workflow.add_edge("structure_content", "define_outcome")
workflow.add_edge("identify_responses", "define_outcome")
workflow.add_edge("assess_risks", "define_outcome")

workflow.add_edge("define_outcome", "compile_plan")
workflow.add_edge("compile_plan", END)

# Compile
strategy_graph = workflow.compile()
```

#### **Draft Generation Workflow**
```python
class DraftState(TypedDict):
    letter_id: str
    strategy_plan: str
    subject: str
    recipient: str
    
    # Retrieval
    contract_sources: List[Dict]
    letter_sources: List[Dict]
    context_documents: List[Dict]
    
    # Generation
    draft_body: str
    key_points: List[str]
    
    # Review
    reviewer_findings: List[Dict]
    is_approved: bool

# Nodes
async def retrieve_contract_clauses(state: DraftState) -> DraftState:
    """Retrieve relevant contract clauses"""
    query = build_query(state["subject"], state["strategy_plan"])
    results = await contract_service.search_clauses(query)
    state["contract_sources"] = results
    return state

async def retrieve_correspondence(state: DraftState) -> DraftState:
    """Retrieve relevant previous letters"""
    results = await retrieval_service.search_letters(state["letter_id"])
    state["letter_sources"] = results
    return state

async def generate_draft(state: DraftState) -> DraftState:
    """Generate draft with citations"""
    sources = format_sources_with_ids(
        state["contract_sources"] + state["letter_sources"]
    )
    prompt = f"""
    Subject: {state['subject']}
    Recipient: {state['recipient']}
    Strategic Plan: {state['strategy_plan']}
    
    Sources:
    {sources}
    
    Generate a formal letter that:
    1. Follows the strategic plan
    2. Cites sources using [S1], [S2] format
    3. Quotes clauses verbatim
    4. Maintains professional tone
    """
    draft = await llm.generate(prompt, model="gpt-4o", max_tokens=1500)
    state["draft_body"] = draft
    return state

async def review_draft(state: DraftState) -> DraftState:
    """AI reviewer validation"""
    findings = await reviewer_agent.review(
        draft=state["draft_body"],
        sources=state["contract_sources"] + state["letter_sources"]
    )
    state["reviewer_findings"] = findings
    state["is_approved"] = not any(f["level"] == "error" for f in findings)
    return state

def should_regenerate(state: DraftState) -> str:
    """Conditional edge: regenerate if not approved"""
    if state["is_approved"]:
        return "save"
    else:
        return "regenerate"

# Build graph
draft_workflow = StateGraph(DraftState)

draft_workflow.add_node("retrieve_clauses", retrieve_contract_clauses)
draft_workflow.add_node("retrieve_letters", retrieve_correspondence)
draft_workflow.add_node("generate_draft", generate_draft)
draft_workflow.add_node("review_draft", review_draft)
draft_workflow.add_node("regenerate", generate_draft)  # Retry with feedback

draft_workflow.set_entry_point("retrieve_clauses")
draft_workflow.add_edge("retrieve_clauses", "retrieve_letters")
draft_workflow.add_edge("retrieve_letters", "generate_draft")
draft_workflow.add_edge("generate_draft", "review_draft")

# Conditional edge
draft_workflow.add_conditional_edges(
    "review_draft",
    should_regenerate,
    {
        "save": END,
        "regenerate": "regenerate"
    }
)
draft_workflow.add_edge("regenerate", "review_draft")

draft_graph = draft_workflow.compile()
```

---

### 6.5 API Endpoints (New Backend)

#### **Strategy Endpoints**
```python
# POST /api/v2/letters/{letter_id}/strategy/contexts
# Generate three-way contexts
@router.post("/{letter_id}/strategy/contexts")
async def generate_strategy_contexts(
    letter_id: UUID,
    refresh: bool = False,
    current_user: User = Depends(get_current_user)
):
    """Generate contractor, engineer, employer contexts"""
    result = await strategy_service.generate_contexts(
        letter_id=letter_id,
        refresh=refresh,
        user=current_user
    )
    return result

# POST /api/v2/letters/{letter_id}/strategy/plan
# Generate strategic plan
@router.post("/{letter_id}/strategy/plan")
async def generate_strategy_plan(
    letter_id: UUID,
    request: StrategyPlanRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user)
):
    """Generate strategic plan using LangGraph"""
    # Create async job
    job_id = await job_service.create_job(
        type="strategy_plan",
        letter_id=letter_id,
        user_id=current_user.id
    )
    
    # Queue background task
    background_tasks.add_task(
        strategy_graph.ainvoke,
        {
            "letter_id": str(letter_id),
            "role": request.role,
            **request.dict()
        },
        job_id=job_id
    )
    
    return {"job_id": job_id, "status": "queued"}

# GET /api/v2/jobs/{job_id}
# Check job status
@router.get("/jobs/{job_id}")
async def get_job_status(job_id: UUID):
    """Get job status and result"""
    job = await job_service.get_job(job_id)
    return {
        "job_id": job_id,
        "status": job.status,  # queued, running, completed, failed
        "progress": job.progress,
        "result": job.result if job.status == "completed" else None,
        "error": job.error if job.status == "failed" else None
    }
```

#### **Draft Endpoints**
```python
# POST /api/v2/letters/{letter_id}/drafts
# Generate new draft version
@router.post("/{letter_id}/drafts")
async def generate_draft(
    letter_id: UUID,
    request: DraftRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user)
):
    """Generate draft using LangGraph"""
    job_id = await job_service.create_job(
        type="draft_generation",
        letter_id=letter_id,
        user_id=current_user.id
    )
    
    background_tasks.add_task(
        draft_graph.ainvoke,
        {
            "letter_id": str(letter_id),
            **request.dict()
        },
        job_id=job_id
    )
    
    return {"job_id": job_id, "status": "queued"}

# GET /api/v2/letters/{letter_id}/drafts
# List all draft versions
@router.get("/{letter_id}/drafts")
async def list_drafts(
    letter_id: UUID,
    current_user: User = Depends(get_current_user)
):
    """Get all draft versions for a letter"""
    drafts = await draft_service.list_versions(letter_id)
    return drafts

# GET /api/v2/letters/{letter_id}/drafts/{version}
# Get specific draft version
@router.get("/{letter_id}/drafts/{version}")
async def get_draft_version(
    letter_id: UUID,
    version: int,
    current_user: User = Depends(get_current_user)
):
    """Get specific draft version"""
    draft = await draft_service.get_version(letter_id, version)
    return draft

# POST /api/v2/letters/{letter_id}/drafts/{version}/restore
# Restore previous version
@router.post("/{letter_id}/drafts/{version}/restore")
async def restore_draft_version(
    letter_id: UUID,
    version: int,
    current_user: User = Depends(get_current_user)
):
    """Restore a previous draft version as current"""
    new_version = await draft_service.restore_version(
        letter_id=letter_id,
        version=version,
        user=current_user
    )
    return new_version
```

#### **Template Endpoints**
```python
# POST /api/v2/templates/upload
# Upload new template
@router.post("/upload")
async def upload_template(
    project_id: UUID,
    file: UploadFile,
    name: str,
    description: str = None,
    current_user: User = Depends(get_current_user)
):
    """Upload letter template for project"""
    template = await template_service.upload(
        project_id=project_id,
        file=file,
        name=name,
        description=description,
        user=current_user
    )
    return template

# GET /api/v2/templates/{project_id}
# List templates for project
@router.get("/{project_id}")
async def list_templates(
    project_id: UUID,
    current_user: User = Depends(get_current_user)
):
    """List all templates for a project"""
    templates = await template_service.list_by_project(project_id)
    return templates

# POST /api/v2/templates/{template_id}/preview
# Preview template with sample data
@router.post("/{template_id}/preview")
async def preview_template(
    template_id: UUID,
    sample_data: Dict[str, Any],
    current_user: User = Depends(get_current_user)
):
    """Generate preview of template with sample data"""
    preview_url = await template_service.generate_preview(
        template_id=template_id,
        data=sample_data
    )
    return {"preview_url": preview_url}
```

#### **Clause Extraction Endpoints**
```python
# POST /api/v2/contracts/{document_id}/extract-clauses
# Extract clauses from contract
@router.post("/{document_id}/extract-clauses")
async def extract_clauses(
    document_id: UUID,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user)
):
    """Extract clauses from contract document"""
    job_id = await job_service.create_job(
        type="clause_extraction",
        document_id=document_id,
        user_id=current_user.id
    )
    
    background_tasks.add_task(
        clause_extraction_service.extract,
        document_id=document_id,
        job_id=job_id
    )
    
    return {"job_id": job_id, "status": "queued"}

# GET /api/v2/contracts/{document_id}/clauses
# List extracted clauses
@router.get("/{document_id}/clauses")
async def list_clauses(
    document_id: UUID,
    current_user: User = Depends(get_current_user)
):
    """Get all clauses for a contract"""
    clauses = await clause_service.list_by_document(document_id)
    return clauses

# GET /api/v2/clauses/search
# Search clauses
@router.get("/search")
async def search_clauses(
    query: str,
    organization_id: UUID,
    project_id: UUID = None,
    limit: int = 10,
    current_user: User = Depends(get_current_user)
):
    """Semantic search for contract clauses"""
    results = await clause_service.search(
        query=query,
        organization_id=organization_id,
        project_id=project_id,
        limit=limit
    )
    return results
```

---

### 6.6 Caching Strategy

```python
from redis import Redis
from functools import wraps
import json
import hashlib

redis_client = Redis(host='localhost', port=6379, db=0)

def cache_result(ttl: int = 3600, key_prefix: str = ""):
    """Decorator to cache function results"""
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Generate cache key
            key_data = f"{key_prefix}:{func.__name__}:{args}:{kwargs}"
            cache_key = hashlib.md5(key_data.encode()).hexdigest()
            
            # Check cache
            cached = redis_client.get(cache_key)
            if cached:
                return json.loads(cached)
            
            # Execute function
            result = await func(*args, **kwargs)
            
            # Store in cache
            redis_client.setex(
                cache_key,
                ttl,
                json.dumps(result, default=str)
            )
            
            return result
        return wrapper
    return decorator

# Usage
@cache_result(ttl=3600, key_prefix="embeddings")
async def get_document_embeddings(doc_id: UUID):
    """Get embeddings for document (cached 1 hour)"""
    return await embedding_service.embed(doc_id)

@cache_result(ttl=300, key_prefix="conversation")
async def get_conversation_chain(letter_id: UUID):
    """Get conversation chain (cached 5 minutes)"""
    return await conversation_service.get_chain(letter_id)
```

**Cache Invalidation**:
```python
async def invalidate_letter_cache(letter_id: UUID):
    """Invalidate all caches related to a letter"""
    patterns = [
        f"conversation:*:{letter_id}",
        f"strategy:*:{letter_id}",
        f"draft:*:{letter_id}"
    ]
    for pattern in patterns:
        keys = redis_client.keys(pattern)
        if keys:
            redis_client.delete(*keys)
```

---

### 6.7 Background Job Processing

```python
from celery import Celery
from celery.result import AsyncResult

celery_app = Celery(
    'letter_drafting',
    broker='redis://localhost:6379/0',
    backend='redis://localhost:6379/1'
)

@celery_app.task(bind=True)
def generate_strategy_plan_task(self, letter_id: str, params: dict):
    """Background task for strategy plan generation"""
    try:
        # Update job status
        self.update_state(state='PROGRESS', meta={'progress': 0})
        
        # Run LangGraph workflow
        result = asyncio.run(
            strategy_graph.ainvoke(
                {"letter_id": letter_id, **params}
            )
        )
        
        # Save result
        asyncio.run(
            strategy_service.save_plan(letter_id, result)
        )
        
        return {"status": "completed", "result": result}
        
    except Exception as exc:
        self.update_state(state='FAILURE', meta={'error': str(exc)})
        raise

@celery_app.task(bind=True)
def extract_clauses_task(self, document_id: str):
    """Background task for clause extraction"""
    try:
        self.update_state(state='PROGRESS', meta={'progress': 0})
        
        # Step 1: Convert PDF to Markdown (20%)
        markdown = asyncio.run(
            marker_service.convert(document_id)
        )
        self.update_state(state='PROGRESS', meta={'progress': 20})
        
        # Step 2: Extract clauses with AI (60%)
        clauses = asyncio.run(
            clause_extraction_service.extract_from_markdown(
                document_id, markdown
            )
        )
        self.update_state(state='PROGRESS', meta={'progress': 80})
        
        # Step 3: Create embeddings (20%)
        asyncio.run(
            embedding_service.embed_clauses(clauses)
        )
        self.update_state(state='PROGRESS', meta={'progress': 100})
        
        return {"status": "completed", "clause_count": len(clauses)}
        
    except Exception as exc:
        self.update_state(state='FAILURE', meta={'error': str(exc)})
        raise
```

---

### 6.8 WebSocket Notifications

```python
from fastapi import WebSocket, WebSocketDisconnect
from typing import Dict, Set

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}
    
    async def connect(self, user_id: str, websocket: WebSocket):
        await websocket.accept()
        if user_id not in self.active_connections:
            self.active_connections[user_id] = set()
        self.active_connections[user_id].add(websocket)
    
    def disconnect(self, user_id: str, websocket: WebSocket):
        if user_id in self.active_connections:
            self.active_connections[user_id].discard(websocket)
    
    async def send_to_user(self, user_id: str, message: dict):
        if user_id in self.active_connections:
            for connection in self.active_connections[user_id]:
                await connection.send_json(message)

manager = ConnectionManager()

@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    await manager.connect(user_id, websocket)
    try:
        while True:
            # Keep connection alive
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(user_id, websocket)

# Usage in background tasks
async def notify_user(user_id: str, event_type: str, data: dict):
    """Send notification to user via WebSocket"""
    await manager.send_to_user(
        user_id,
        {
            "type": event_type,
            "data": data,
            "timestamp": datetime.utcnow().isoformat()
        }
    )

# In Celery task
@celery_app.task
def generate_draft_task(letter_id: str, user_id: str):
    # ... generation logic
    
    # Notify user of completion
    asyncio.run(
        notify_user(
            user_id,
            "draft_completed",
            {"letter_id": letter_id, "version": 2}
        )
    )
```

---

## 7. Implementation Roadmap

### Phase 1: Foundation (Weeks 1-2)

**Goals**: Set up new infrastructure and migrate core data

**Tasks**:
1. Set up PostgreSQL database
2. Create database schema and migrations
3. Set up Redis for caching and Celery
4. Set up Celery workers
5. Migrate letter data from MongoDB to PostgreSQL
6. Set up WebSocket server

**Deliverables**:
- PostgreSQL running with schema
- Redis + Celery configured
- Data migration scripts
- WebSocket endpoint

---

### Phase 2: Clause Extraction (Weeks 3-4)

**Goals**: Implement clause-level extraction and indexing

**Tasks**:
1. Integrate Marker for PDF → Markdown conversion
2. Build clause extraction service with GPT-4o-mini
3. Create clause storage in PostgreSQL
4. Generate clause-level embeddings in Qdrant
5. Build clause search API
6. Create background job for bulk extraction

**Deliverables**:
- Clause extraction pipeline
- Clause search endpoint
- Qdrant clause embeddings
- Background job system

---

### Phase 3: LangGraph Integration (Weeks 5-6)

**Goals**: Replace facsimile with real LangGraph workflows

**Tasks**:
1. Install LangGraph library
2. Build strategy plan workflow
3. Build draft generation workflow
4. Integrate with Celery for async execution
5. Add progress tracking
6. Test workflows end-to-end

**Deliverables**:
- Strategy plan LangGraph workflow
- Draft generation LangGraph workflow
- Async job execution
- Progress notifications

---

### Phase 4: Enhanced Drafting (Weeks 7-8)

```
