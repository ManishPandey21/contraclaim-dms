# Contraclaim DMS Letter Drafting – Current State and Upgrade Plan

## How Drafting Works Today (end-to-end)

- **Page entry**: `client/src/pages/LetterDraftPage.tsx` loads the letter via `useLetterWorkflow` (GET `/letters`) and the latest LangGraph run via `useLetterGraphRuns` (GET `/ai-assistant/langgraph/runs/{letterId}`). If the letter is still in Strategy or has no strategic plan it redirects to the strategy page.
- **Context selection**: `LinkedDocumentSelector` (GET/PUT `/letters/{id}/context-documents`) pulls curated docs + suggestions (by letter number) and allows search across `/documents`. Selected IDs are persisted to the letter and reused in LangGraph runs. Conversation chain and Falkor thread are shown for awareness but not yet queried for content.
- **Background generation**: “Generate Background” triggers `useLanggraphDraft` with `analysis_only=true` (POST `/ai-assistant/langgraph/background`). Backend `LetterDraftGraph`:
  - `load_state` verifies the letter.
  - `collect_context` compiles preferred doc IDs (request → stored context → fallback by letter_no), validates org/project scope, pulls document comments, fetches conversation_chain, and optionally a Falkor thread.
  - `plan_response` builds a heuristic plan and `background_summary` (documents + recent comments + summary points) without calling an external LLM.
  - Returns `status=analysis_only`; `LetterService.record_langgraph_result` stores `draft_plan`, `summary_points`, `background_summary`, `context_documents`, `graph_thread`, etc. on the letter.
- **Draft generation**: “Regenerate Draft” calls POST `/ai-assistant/langgraph/draft` with subject/recipient/editor content/selected docs. The same graph runs; after planning it calls `AIService.generate_draft` (template-based text using provided context, points, and document summaries/keywords). `validate_and_route` sets `graph_status=ready_for_review` unless the letter is terminal. Results are saved back to the letter (`draft_output`, `draft_plan`, traces, warnings).
- **Editing and submission**: `LetterDraftEditor` lets the user edit content and link a reference letter (current UI uses a stub list). `onSave` issues PUT `/letters/{id}`; “Submit for Review” adds a status change via POST `/letters/{id}/submit` and navigates to the review page. Graph status is shown with `GraphStatusBadge`.
- **Data persisted on the letter**: `letter_service.record_langgraph_result` writes plan/draft/output/status, `context_document_ids`, serialized `context_documents`, `background_summary`, and `graph_thread` so future loads and `/ai-assistant/langgraph/runs/{id}` can replay the last run.

### Notable gaps/constraints in the current flow

- Drafting is template-driven; no retrieval-augmented LLM is used yet, so tone and clause fidelity depend solely on user input and stored summaries.
- Context retrieval is limited to curated IDs or simple “same letter_no” fallback; no vector search across contracts/correspondence.
- Clause-level structure is not extracted; contract PDFs are unparsed beyond stored summaries/keywords.
- UI does not surface the exact sources used in the draft (only background summaries and plan annotations).
- Reviewer/QA loop is manual; no automated verification against constraints.

## Proposed Flowchart (target workflow)

```
Input Prompt
  ↓
Query Expansion (LLM identifies needed evidence: clauses + prior notices)
  ↓
Retrieval A – Contract clauses (PDF → Markdown chunks → vector search)
  ↓
Retrieval B – Correspondence history (recent letters on topic)
  ↓
Context Assembly (Pydantic AI builds Context Packet: legal constraint + factual history)
  ↓
Drafting (LLM mirrors prior tone/format; cites sources)
  ↓
Reviewer Agent Check (cross-check draft vs clause to avoid contradictions)
  ↓
User Review in UI (draft + linked sources side-by-side)
```

Prompt: Legal Document RAG Strategy – Contract-Focused Data Ingestion & Retrieval Plan

Since legal/contractual documents are high-stakes, "standard" RAG approaches are often insufficient. Use the following strategy to ensure fidelity, accuracy, and context-aware retrieval.

🔹 Marker Usage Scope

1. Use Marker only for contract documents to ensure high-fidelity Markdown extraction.
2. Other document types (e.g., letters, notes) should bypass Marker and follow a simpler pipeline (e.g., plain OCR or text extraction).

🔹 Lossless Markdown Conversion

When using Marker on contract PDFs (uploaded via ContractsUploadPage.tsx), capture:

1. Hierarchical structure: Part → Section → Clause → Sub-clause.
2. Table of Contents (ToC): Store this structure as metadata.

Persist outputs:

1. Markdown file → storage/
2. Clause header offsets for later segmentation and metadata tagging.

🔹 Clause Extraction

Use a lightweight OpenAI GPT-4o worker to extract structured clauses:

1. Fields: clause_id, heading, path, start_offset, end_offset
2. Link extracted clause data to document IDs for cross-referencing and traceability.

🔹 Vectorization & Storage (Qdrant)

Implement Gold Standard Chunking:

1. Use clauses as the primary chunk unit.
2. Avoid arbitrary character/token-based splitting.
3. Use Contextual Chunking: Each chunk includes document_title and clause_heading.

Store in Qdrant (qdrant_data/) with metadata:

{
"document_type": "contract",
"date": "...",
"clause_id": "...",
"project_id": "...",
"org_id": "...",
"upload_id": "...",
"toc_path": ["Part A", "Section 3", "Clause 4.2"]
}

🔹 Hybrid Search Strategy

Enable Hybrid Search (Dense Vector + BM25/Keyword):

1. Ensure precise retrieval of clause numbers like "Clause 4.2.b".
2. Use keyword scoring to supplement semantic embeddings where legal precision is critical.

Exact Phrase Matching:

1. Search queries like "taking over certificate" must return clauses where the full phrase appears exactly.
2. Avoid splitting queries into individual tokens (taking, over, certificate) unless fallback search is explicitly triggered.

🔹 Retrieval Logic

Support Parent-Child Retrieval:

1. When retrieving a sub-clause, surface its parent clause or section for context.
2. Use toc_path and clause_id to fetch full context across levels.

## Implementation Plan (phased)

### Phase 1 (Weeks 1–2): Data Ingestion

- **OCR & layout**: Add a Marker-based ingest job to convert contract documents PDFs (uploaded using ContractsUploadPage.tsx) → Markdown while preserving headings/clauses; store Markdown and header offsets in `storage/` and Mongo.

- **Clause extraction**: Add a lightweight OpenAI 4o mini worker to segment clauses (start/end, clause_id, heading, path). Persist structured clauses with cross-links to document IDs.
- **Vectorization**: Chunk Markdown and push to Qdrant (folder `qdrant_data`) with metadata: document_type (`contract`/`letter`), date, clause_id, letter_no, project/org, upload_id. Extend `DocumentService` to expose chunk metadata and Qdrant IDs for retrieval.

### Phase 2 (Weeks 3–4): Knowledge Graph Construction

- **Graph model**: Use FalkorDB (aligns with existing `FalkorGraphService`) to create nodes: Contract, Clause, Letter. Add edges: `(Letter)-[:REFERENCES]->(Clause)`, `(Letter)-[:FOLLOWS]->(Letter)` for chronology, `(Clause)-[:LOCATED_IN]->(Contract)`.
- **Sync pipeline**: On ingest or LangGraph run, upsert nodes/edges. Extend `record_langgraph_result` to push citations into Falkor (rather than only storing in Mongo).
- **API surface**: Add read endpoints to fetch graph threads enriched with clause titles and letter subjects for UI display and tool use.

### Phase 3 (Weeks 5–6): Drafting Agent

- **Agent prompt**: Implement a Pydantic AI agent with a legal persona; seed with style from prior approved letters (sample from `letters` collection).
- **Tooling**: Expose `search_contract_clauses()` (Qdrant + clause store) and `search_letter_history()` (Qdrant over correspondence, filtered by project/org and topic).
- **Draft + review loop**: `LangGraphLetterPipeline` calls agent -> builds draft with inline citations; a second reviewer agent validates against retrieved clauses and flags contradictions/gaps. Persist reviewer notes into `graph_warnings` and `background_summary`.
- **Backward compatibility**: Keep current `/ai-assistant/langgraph/draft` shape but extend `LangGraphDraftResponse` with `sources` (clause ids + letter ids) and reviewer findings.

### Phase 4 (Weeks 7–8): UI Integration

- **Sources panel**: Update `LetterDraftPage` to show the drafted letter beside the “Sources” list: clause excerpts and prior letters returned by the agent. Each citation links to `/documentviewer/{id}`.
- **Context UX**: Pre-fill `LinkedDocumentSelector` with agent-suggested clauses/letters (from retrieval) and allow pinning/unpinning. Warn when selected docs differ from those used in the last run.
- **Status/trace**: Extend `PlanViewer` to display reviewer-agent checks and the exact tools called. Surface `graph_warnings` inline in the draft editor.
- **Endpoints**: Add FastAPI routes for `/upload`, `/query` (retrieval), and `/generate-draft` that wrap the new agent and return structured source metadata.

## Suggested Changes and Final Target Process

- Replace the template-based `AIService.generate_draft` with the agentic workflow above, but keep the LangGraph node structure (load_state → collect_context → plan → draft → validate) so existing UI plumbing remains intact.
- Enrich `collect_context` to call Qdrant for semantic retrieval before falling back to curated IDs; thread results into `context_documents` and `background_summary`.
- Persist citations (`context_document_ids` + clause_ids) and reviewer notes so the UI can render source-side-by-side trust indicators.
- Add a guarded reviewer agent step that must succeed (or raise `needs_attention`) before `graph_status` becomes `ready_for_review`.
- Update `LetterDraftEditor` to pull real reference letters (via `/letters` query) and show which sources were used; when submitting for review, include the agent’s validation report.

**Final desired flow**: User provides a prompt → system expands needs → dual retrieval (contract clauses + recent correspondence) → Pydantic AI builds a context packet → drafting agent writes a citation-backed letter → reviewer agent checks against clauses → user sees the draft with sources and can accept/submit. This preserves the current workflow scaffolding while adding reliable, source-grounded drafting.
