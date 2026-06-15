# LetterWorkflow Enhancement Plan

## Present Method

- **Document Selection Panel**: Letter workflow screens (`client/src/pages/LetterStrategicPlanPage.tsx`, `client/src/pages/LetterDraftPage.tsx`) do not surface any view of linked documents. The only UI capable of showing linked artefacts is the document viewer (`client/src/components/document-viewer/ReferencesPanel.tsx`) and it is disconnected from the drafting flow. LangGraph requests (`useLanggraphDraft.runDraft`) accept a `documentIds` array, but the callers never populate it, so the backend pipeline (`backend/rbac_backend/ai_workflows/langgraph/letter_pipeline.py`) falls back to auto-discovering up to five documents that share the same `letter_no`. `LetterDraftEditor.tsx` shows a mock reference selector backed by `samplePreviousLetters`, leaving drafters without real control over the evidence that feeds the AI run.

- **Background Generation Button**: Strategy and drafting pages only expose a generic “Generate with LangGraph”/“Regenerate Draft” button. The interaction is all-or-nothing: it kicks off the full pipeline (plan + draft) and writes the output back to the letter via `LetterService.record_langgraph_result`. There is no clearly labelled background/context generation step, no way to rehearse the background before drafting, and no visual priority given to the action.

- **Abstract/Summary Display**: `PlanViewer.tsx` renders the stored `draft_plan` and `summary_points` verbatim, always expanded, without emphasising that this is the AI-produced background. `LetterSummaryPage.tsx` can display document summaries, but it sits outside the workflow. There is no collapsible/annotated abstract section, no surface for human commentary, and no traceability back to the documents that informed each bullet.

- **Draft Strategy Planning Section**: The strategic plan page simply dumps the plan in `PlanViewer` and treats it as read-only unless the user flips into a raw `Textarea`. `LetterDraftEditor.tsx` is a simple textarea with optional mock AI helper (`AIAssistant.tsx`). The old deep-planning assistant (`client/src/components/letter-workflow_old/DeepPlanningAssistant.tsx`) is no longer wired in. As a result, there is limited structure to guide the reply, no coupling between selected background points and the draft outline, and no safeguard that the reply follows a logical progression.

## Proposed Changes

### Document Selection Panel

- **Frontend**:
  - Introduce a dedicated `LinkedDocumentSelector` component embedded at the top of `LetterStrategicPlanPage.tsx` (and surfaced in `LetterDraftPage.tsx` for late edits). It should list:
    - Direct document links from the originating document (via `/documents/{id}/linked`).
    - Any documents referenced in the conversation history (`letter.references`, `conversation_service.get_conversation_chain` output).
    - Search results for ad-hoc inclusion (reuse the documents search endpoint with debounce).
  - Render each candidate with checkboxes, quick metadata (direction, subject, letter number), and a secondary action to open the viewer.
  - Persist the selection in component state, defaulting to currently linked documents. 
  - Feed the selection into LangGraph requests by extending the hook call: `runDraft({ ..., documentIds: selectedIds })`. Store the set locally (context or Zustand) so the same selection is available when the user moves into the drafting screen.

- **Backend**:
  - Extend `Letter` persistence with a `context_document_ids` array (stored alongside `draft_plan` in `letters` collection) so the chosen evidence survives reloads and can be audited.
  - Add a helper in `DocumentService` to fetch documents by ID batch (to avoid hand-written aggregation in the pipeline).
  - Update `LetterDraftGraph.collect_context` to:
    - Merge the persisted selection with the auto-detected `letter_no` matches (selected docs take precedence).
    - Pull metadata (summary, keywords, latest comments) for each selected document.
  - Expose a read/write interface (`GET /letters/{id}/context-documents`, `PUT /letters/{id}/context-documents`) that the UI can call when toggles change, with validation that IDs belong to the same organisation/project.

### Background Generation Control

- Add a dedicated “Generate Background” button, visually primary, next to the page title. The action should call a new backend entry point (e.g. `POST /ai-assistant/langgraph/background`) that runs the pipeline up to the `plan_response` node and returns:
  - Normalised background summary (bulleted),
  - Document snippets/citations per bullet,
  - Diagnostics (warnings about missing documents, stale data).
- Introduce an optional `analysisOnly` flag in `LangGraphDraftRequest` so the backend can reuse the same code path and skip the drafting node when only context is needed.
- Surface progress / completion states in the UI (spinner, success toast) and cache the latest background payload in letter state so the drafter can revisit it without another API call.

### Abstract / Summary Presentation

- Replace the static `PlanViewer` summary block with an accordion-style `BackgroundSummary` component:
  - Section header clearly labelled “AI Background Summary”.
  - Each bullet expands to show supporting snippets and the documents that contributed (with quick links).
  - Allow inline human notes (e.g. text input or tags) that save back to the letter record (`background_annotations` field).
- Augment `PlanViewer` to accept the richer background payload (citations + annotations) and render them when expanded.
- Provide a quick toggle to hide/show the section so reviewers can focus on either plan or draft.

### Draft Strategy Planning Section

- Split the current planning card into structured sub-sections (Introduction, Factual Background, Analysis, Requested Actions, Closing). Seed each section from the background bullets (`summary_points`) using heuristics (e.g. first bullet -> intro, bullets mentioning “request” -> actions).
- Allow manual re-ordering and editing with autosave, storing the curated outline in new fields (`strategic_outline`, `outline_last_edited_by`).
- When the user clicks “Generate Draft”, pass both the curated outline and the selected document IDs to LangGraph so the final draft respects the agreed structure.
- Modernise `LetterDraftEditor` to display the outline alongside the editable draft (two-pane view), keeping the AI assistant optional but wired to real backend endpoints (`enhancedApi.deepPlanning` can serve as fallback for richer assistance).
- Add guardrails before submitting for review: surface validation messages if key sections are empty or background is outdated compared to the latest document edits (compare timestamps).

## Final Expected Process

1. Drafter opens a letter and immediately sees the linked-document selector populated with existing references. They tick/untick the documents that should inform the reply (and can pull in new evidence via search).
2. They press the prominently labelled “Generate Background” button. The system analyses the chosen documents, conversation chain, and existing instructions, returning an expandable background summary with citations and alerts (e.g. missing replies).
3. The drafter reviews the summary, optionally adds human notes, and locks in the outline sections presented in the strategy planning area. Any adjustment to document selection invalidates cached background, prompting regeneration.
4. Once satisfied, they trigger “Generate Strategy Plan”/“Generate Draft”, which now consumes the curated outline and document set. The AI returns a draft aligned with the agreed structure, displayed next to the outline for quick comparison.
5. The drafter refines the text, captures rationale in annotations if needed, and submits for review. The stored `context_document_ids`, background summary, outline, and draft are all persisted for auditors, giving reviewers clear traceability from evidence → background → strategy → final reply.

This end-state delivers tighter human control, auditable context selection, and a clearer separation between understanding the background, shaping the plan, and producing the final written response.
