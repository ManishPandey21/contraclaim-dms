# Production-Grade Letter Drafting System
## Architecture & Process Design
### Inspired by Cyclic RAG Workflow

## 1. Objective

The system shall draft high-quality contractual letters and replies by using only verified project records, contract documents, previous correspondence, clauses, user instructions, and retrieved evidence. The drafting process shall not be a single-pass AI generation. Instead, it shall follow a **cyclic retrieval, drafting, critique, validation, and refinement workflow** until the answer is contractually sound, evidence-backed, and suitable for formal issue.

---

## 2. Core Concept

The system will work like a **contract-aware drafting engine**.

For every user request, the system shall:

1. Understand whether the user wants a **fresh letter** or **reply letter**.
2. Identify missing critical inputs.
3. Retrieve relevant contract clauses, previous letters, drawings, notices, instructions, EOT records, IPC/payment details, claims, approvals, and linked correspondence.
4. Generate a draft.
5. Critique the draft for contractual correctness, missing facts, unsupported statements, tone, and risk.
6. Retrieve again if gaps are found.
7. Refine the draft.
8. Produce a final letter with source integrity notes.

---

## 3. High-Level Workflow

```text
User Request
   ↓
Query Understanding
   ↓
Input Completeness Check
   ↓
Document / Clause / Letter Retrieval
   ↓
Context Assembly
   ↓
Draft Generation
   ↓
Contractual Critique & Risk Review
   ↓
Satisfactory?
   ├── No → Refine Query → Retrieve Again → Redraft
   └── Yes → Final Letter + Source Notes
```

---

## 4. Main System Modules

### 4.1 Query Understanding Module

This module identifies the nature of the user’s request.

It should classify the request as:

```text
1. Fresh Letter
2. Reply to Contractor
3. Reply to Engineer / Employer
4. Claim Rejection
5. EOT Recommendation
6. Notice / Warning
7. Explanation Required
8. Payment / IPC / Recovery Matter
9. Variation / VO Matter
10. Technical Observation
11. Contractual Interpretation
```

It should also extract:

```json
{
  "project": "KNPCC-11",
  "contract_package": "KNPCC-11",
  "letter_type": "Reply / Fresh / Notice / Rejection",
  "subject": "TBM brought to Casting Yard without approval",
  "party_from": "GC / Engineer",
  "party_to": "Contractor",
  "key_clause_refs": ["GCC 4.15.1"],
  "requested_action": "Ask explanation from Contractor",
  "urgency": "Immediate",
  "tone": "Formal, contractual, firm"
}
```

---

### 4.2 Input Completeness Check

Before drafting, the system shall check whether sufficient information is available.

#### Critical Inputs for a Reply Letter

```text
1. Contractor’s incoming letter reference
2. Date of contractor’s letter
3. Subject
4. Contract package
5. Relevant clause
6. Event / issue description
7. GC / Employer position
8. Required instruction / decision
```

#### Critical Inputs for a Fresh Letter

```text
1. Purpose of the letter
2. Contract package
3. Addressee
4. Relevant facts
5. Clause / contractual basis
6. Required action from recipient
7. Time limit, if any
```

#### Draft Blocking Rule

If more than three critical facts are missing, the system should not generate a final contractual draft. It should instead return:

```text
Draft Blocked: Insufficient source material.

Required information:
1. Contractor’s letter reference and date
2. Relevant contract clause
3. Specific event date / site location
4. Required action from Contractor
```

If only minor facts are missing, the system may proceed using placeholders such as:

```text
[CONFIRM: Letter Reference]
[CONFIRM: Date]
[CONFIRM: Exact Clause]
```

---

## 5. Retrieval Layer

### 5.1 Retrieval Sources

The system should retrieve from the following repositories:

```text
1. Contract Agreement
2. GCC / SCC
3. Employer’s Requirements
4. BOQ / Schedules
5. Tender Drawings
6. Addenda and Pre-bid Replies
7. Approved Drawings
8. Method Statements
9. Previous Incoming Letters
10. Previous Outgoing Letters
11. NCRs / Site Instructions
12. EOT Records
13. IPC / Payment Records
14. Variation / Claim Files
15. MOMs / Emails / Approvals
```

---

### 5.2 Hybrid Retrieval Design

Use a combination of:

```text
1. Keyword Search
2. Semantic Vector Search
3. Metadata Filtering
4. Letter Reference Search
5. Clause Search
6. Graph-Based Linked Correspondence Search
```

Example retrieval query:

```json
{
  "project_id": "KNPCC-11",
  "topic": "TBM brought to casting yard without approval",
  "clause_refs": ["GCC 4.15.1"],
  "document_types": ["Contract", "Letter", "Site Instruction"],
  "date_range": "latest relevant correspondence",
  "party": "Contractor"
}
```

---

## 6. Knowledge Store Design

### 6.1 Metadata Store

Use MongoDB / Firestore-style metadata for structured fields.

Example:

```json
{
  "document_id": "doc_001",
  "project_id": "KNPCC-11",
  "organization_id": "UPMRC",
  "document_type": "Incoming Letter",
  "letter_no": "KNPCC11/CONT/2026/045",
  "date": "2026-05-09",
  "from": "Contractor",
  "to": "GC",
  "subject": "TBM Delivery at Casting Yard",
  "status": "Received",
  "linked_documents": ["doc_002", "doc_003"],
  "clauses": ["GCC 4.15.1"],
  "tags": ["TBM", "Contractor Equipment", "Casting Yard"]
}
```

---

### 6.2 Vector Store

Use Qdrant / Weaviate / MongoDB Atlas Vector Search.

Each document should be chunked and embedded with metadata:

```json
{
  "chunk_id": "chunk_001",
  "document_id": "doc_001",
  "project_id": "KNPCC-11",
  "text": "GCC Sub-Clause 4.15.1 states that Contractor's Equipment brought onto Site shall be deemed to be exclusively intended for execution of the Works...",
  "metadata": {
    "clause": "GCC 4.15.1",
    "source_type": "Contract Clause",
    "page": 123,
    "confidence": 0.96
  }
}
```

---

### 6.3 Graph Store

Use Graphiti / Neo4j / FalkorDB-style relationships.

Important relationships:

```text
Letter A replies_to Letter B
Letter A refers_to Clause X
Letter A concerns Topic Y
Instruction A issued_to Contractor
Claim A rejected_by Letter B
Drawing A supersedes Drawing B
EOT Recommendation refers_to Programme Revision
```

Example graph model:

```text
(Contractor Letter)-[:REPLIED_BY]->(GC Letter)
(GC Letter)-[:CITES]->(GCC Clause 4.15.1)
(GC Letter)-[:CONCERNS]->(TBM at Casting Yard)
(TBM Issue)-[:LOCATED_AT]->(Casting Yard)
```

This is essential for contractual drafting because replies must be linked with previous letters, clauses, and records.

---

## 7. Cyclic RAG Drafting Workflow

### Step 1: Query Understanding

The system reads the user instruction:

```text
Draft a letter asking the Contractor to explain why a TBM has been brought to the Casting Yard when tunnelling works are completed.
```

The system identifies:

```json
{
  "intent": "Fresh letter / explanation required",
  "issue": "TBM brought to site without approval",
  "contractual_basis": "GCC 4.15.1",
  "required_action": "Contractor to explain purpose, authority and contractual basis",
  "tone": "Firm but formal"
}
```

---

### Step 2: Retrieval

The system retrieves:

```text
1. GCC Clause 4.15.1
2. Contract definition of Site
3. Contract definition of Contractor’s Equipment
4. Previous approvals for major equipment, if any
5. Project status showing tunnelling works completed
6. Any previous letters regarding TBM / casting yard use
```

---

### Step 3: Draft Generation

The LLM prepares the first draft.

Example output:

```text
Dear Sir,

It has come to the attention of GC / UPMRC that a major item of Contractor’s Equipment, namely a TBM, has been brought to the Site / Casting Yard although tunnelling works under the Contract have already been completed...
```

---

### Step 4: Critique & Evaluation

The system checks:

```text
1. Is the clause correctly cited?
2. Is the allegation supported by records?
3. Is the tone too harsh or legally risky?
4. Has the letter asked for explanation instead of directly alleging misuse?
5. Is the required action clear?
6. Is the time limit mentioned?
7. Are unsupported words like “presumably” avoided unless evidence exists?
8. Does the draft preserve Employer’s rights?
```

---

### Step 5: Refine Query / Retrieve Again

If the critique finds missing support, the system retrieves again.

Example:

```text
Retrieve previous correspondence regarding completion of tunnelling works.
Retrieve any approved equipment deployment schedule.
Retrieve site possession / casting yard usage conditions.
```

---

### Step 6: Final Answer

The final draft is generated only after the system confirms that it is:

```text
1. Contractually grounded
2. Factually supported
3. Clear in instruction
4. Free from unsupported allegations
5. Suitable for formal issue
```

---

## 8. Draft Validation Rules

Before finalizing any letter, the system should run the following checks.

### 8.1 Contractual Validity Check

```text
- Every clause cited must exist in the uploaded contract.
- Clause interpretation must not be invented.
- If clause text is unavailable, use placeholder:
  [CONFIRM: Exact clause wording]
```

### 8.2 Evidence Check

```text
- Every major factual assertion must be supported by a document, letter, record, drawing, programme, or user input.
- Unsupported allegations must be softened.
```

Unsafe:

```text
The Contractor is misusing UPMRC premises.
```

Safer:

```text
The circumstances require clarification from the Contractor regarding the purpose and contractual basis for bringing the said equipment to Site.
```

---

### 8.3 Tone Check

The system should maintain:

```text
Formal
Contractual
Firm
Non-emotional
Non-accusatory unless evidence is strong
Preserve rights of GC / UPMRC
```

---

### 8.4 Output Quality Check

The final draft should check:

```text
- Correct salutation
- Proper reference format
- Clear issue statement
- Contractual basis
- Required explanation / action
- Time limit
- Reservation of rights
- Closing sentence
```

---

## 9. Recommended System Architecture

```text
Frontend
  ↓
Letter Drafting UI
  ↓
API Gateway
  ↓
Drafting Orchestrator
  ↓
Query Understanding Agent
  ↓
Retrieval Agent
  ↓
Context Builder
  ↓
Drafting Agent
  ↓
Critique Agent
  ↓
Validation Agent
  ↓
Final Letter Generator
  ↓
Export to DOCX / PDF
```

---

## 10. Application Layer

### 10.1 Frontend

Main UI pages:

```text
1. Letter Drafting Page
2. Reply Drafting Page
3. Document Viewer
4. Clause Search Panel
5. Linked Correspondence Panel
6. Source Evidence Panel
7. Draft Review Panel
8. Approval Workflow Panel
```

The user should be able to:

```text
- Select project
- Select incoming letter
- Select letter type
- Add drafting instruction
- View retrieved sources
- Accept / reject AI suggestions
- Edit final draft
- Send for review
- Export as DOCX / PDF
```

---

### 10.2 Backend

Suggested backend services:

```text
1. document_service.py
2. ingestion_service.py
3. retrieval_service.py
4. graph_service.py
5. drafting_service.py
6. critique_service.py
7. validation_service.py
8. export_service.py
9. audit_log_service.py
10. notification_service.py
```

---

## 11. LangGraph-Based Orchestration

The cyclic workflow can be implemented using LangGraph.

Suggested nodes:

```text
1. understand_query
2. check_completeness
3. retrieve_context
4. build_context_pack
5. generate_draft
6. critique_draft
7. validate_sources
8. refine_query
9. regenerate_draft
10. finalize_letter
```

Suggested state object:

```json
{
  "user_request": "",
  "project_id": "",
  "document_id": "",
  "letter_type": "",
  "retrieved_sources": [],
  "context_pack": {},
  "draft": "",
  "critique": {},
  "validation_result": {},
  "iteration_count": 0,
  "final_answer": ""
}
```

Suggested stopping conditions:

```text
- Draft quality score >= 85%
- No unsupported critical statement
- Clause references validated
- Maximum 3 retrieval cycles completed
```

---

## 12. Context Pack Format

Before drafting, the system should create a structured context pack.

```json
{
  "project": {
    "name": "KNPCC-11",
    "employer": "UPMRC",
    "engineer": "GC",
    "contractor": "Contractor"
  },
  "draft_request": {
    "type": "Explanation Required",
    "subject": "TBM brought to Casting Yard without approval",
    "tone": "Formal and firm"
  },
  "facts": [
    "A TBM has been brought to the Site / Casting Yard.",
    "Tunnelling works under KNPCC-11 are already completed.",
    "No prior approval is available in the retrieved records."
  ],
  "contractual_basis": [
    {
      "clause": "GCC 4.15.1",
      "summary": "Contractor's Equipment brought onto Site is deemed exclusively intended for execution of the Works."
    }
  ],
  "required_action": [
    "Contractor to explain purpose of bringing the TBM.",
    "Contractor to explain authority / approval.",
    "Contractor to explain contractual basis."
  ],
  "source_notes": [
    "Clause text to be verified from uploaded contract.",
    "Completion status of tunnelling works to be verified from project records."
  ]
}
```

---

## 13. Final Letter Output Format

The system should generate:

```text
1. Draft Letter
2. Key Contractual Basis
3. Source Integrity Notes
4. Missing Confirmations, if any
5. Suggested Subject Line
6. Suggested References
```

Example:

```text
Subject: Requirement to Explain Bringing of TBM to Site / Casting Yard

Dear Sir,

It has come to the attention of GC / UPMRC that a major item of Contractor’s Equipment, namely a TBM, has been brought to the Site / Casting Yard, although the tunnelling works under the KNPCC-11 Contract have already been completed and no prior approval appears to have been obtained for bringing such equipment to the Site.

In terms of GCC Sub-Clause 4.15.1, any Contractor’s Equipment brought onto the Site shall be deemed to be exclusively intended for execution of the Works under the Contract. Accordingly, you are hereby required to explain the purpose, authority, and contractual basis for bringing the said TBM to the Site at this stage, particularly when the equipment does not appear to be required for the remaining Works under KNPCC-11. Your explanation shall be submitted immediately, failing which the matter may be dealt with in accordance with the applicable provisions of the Contract.

Yours faithfully,
For GC / Engineer
```

---

## 14. Security and Governance

The system should include:

```text
1. Role-Based Access Control
2. Project-Level Document Access
3. Organization-Level Isolation
4. Audit Logs for AI Draft Generation
5. Version History of Drafts
6. Source Traceability
7. Approval Workflow
8. No Hallucination Guardrails
9. Clause Citation Validation
10. Export Control for Final Letters
```

---

## 15. Audit Trail

Every AI drafting activity should be stored.

```json
{
  "draft_id": "draft_001",
  "project_id": "KNPCC-11",
  "created_by": "user_001",
  "input_document_id": "doc_001",
  "retrieved_sources": ["doc_002", "doc_003"],
  "clauses_used": ["GCC 4.15.1"],
  "draft_version": 1,
  "ai_model": "selected_model",
  "review_status": "Pending Review",
  "created_at": "2026-05-10T19:00:00"
}
```

---

## 16. Production-Grade Enhancements

### 16.1 Confidence Scoring

Each draft should include an internal confidence score:

```text
Clause confidence: 90%
Factual support confidence: 85%
Tone suitability: 95%
Risk level: Low / Medium / High
```

---

### 16.2 Human Review Gate

No contractual letter should be issued directly by AI.

Recommended statuses:

```text
Draft Generated
Under Review
Returned for Correction
Approved
Issued
Replied
Closed
```

---

### 16.3 Red-Flag Detection

The system should warn the user where the draft contains:

```text
1. Unsupported allegation
2. Missing clause reference
3. Ambiguous contractual basis
4. Possible waiver of rights
5. Admission of Employer / Engineer delay
6. Inconsistent previous correspondence
7. Time-bar / notice period issue
8. Payment implication
9. EOT / cost implication
10. Variation implication
```

---

## 17. Recommended Implementation Phases

### Phase 1: Basic Drafting Workflow

```text
- User input form
- Project selection
- Letter type selection
- Manual context input
- AI draft generation
- Save draft
- Export to DOCX
```

---

### Phase 2: RAG-Based Drafting

```text
- Contract document ingestion
- Letter ingestion
- Vector search
- Clause retrieval
- Previous correspondence retrieval
- Source-linked draft generation
```

---

### Phase 3: Cyclic RAG Workflow

```text
- Critique agent
- Source validation agent
- Query refinement
- Re-retrieval
- Draft improvement loop
- Draft quality scoring
```

---

### Phase 4: Graph-Based Correspondence Intelligence

```text
- Link incoming and outgoing letters
- Identify reply chains
- Track references
- Build issue-wise chronology
- Retrieve related correspondence automatically
```

---

### Phase 5: Production Controls

```text
- RBAC
- Audit logs
- Approval workflow
- Notifications
- Version history
- Final issue register
- Legal / contractual risk warnings
```

---

## 18. Best Practical Design for ContraClaim DMS

For ContraClaim DMS, the ideal design should be:

```text
MongoDB = Metadata, documents, users, projects, draft records
S3 / Local Storage = Original files and generated letters
Qdrant / Mongo Vector Search = Semantic retrieval
FalkorDB / Neo4j / Graphiti = Letter relationship graph
FastAPI = Backend APIs
React = Frontend UI
LangGraph = Cyclic drafting orchestration
OpenAI / Groq / Local LLM = Drafting and critique
DOCX Template Engine = Final letter export
```

---

## 19. Final Recommended Process

```text
1. User selects Project.
2. User selects Fresh Letter or Reply Letter.
3. User selects source document / incoming letter, if applicable.
4. System extracts metadata.
5. System asks for missing critical inputs.
6. System retrieves clauses and previous correspondence.
7. System creates context pack.
8. AI generates first draft.
9. Critique agent checks draft.
10. Validation agent checks source support.
11. If weak, system retrieves again.
12. AI refines draft.
13. User reviews draft.
14. User edits / approves.
15. System exports DOCX / PDF.
16. Final letter is stored and linked to the original correspondence.
17. Status is updated in DMS.
```

---

## 20. Recommended File Name

```text
Letter_Drafting_Cyclic_RAG_Architecture.md
```
