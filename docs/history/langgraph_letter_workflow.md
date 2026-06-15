# LangGraph Letter Workflow Integration

## Runtime Entry Points
- FastAPI exposes three LangGraph-specific routes in `backend/rbac_backend/routers/ai_assistant.py:225`-`backend/rbac_backend/routers/ai_assistant.py:287`. The `/ai-assistant/langgraph/draft` and `/ai-assistant/langgraph/background` POST handlers sanitise the request, enforce `letter:write` scope, and forward to the controller, while `/ai-assistant/langgraph/runs/{letter_id}` returns the most recent snapshot for the UI.
- The controller delegates to `AIService.generate_draft_with_langgraph` (`backend/rbac_backend/services/ai_service.py:193`), which instantiates `LetterDraftGraph`, runs the pipeline, and persists the result via `LetterService.record_langgraph_result` (`backend/rbac_backend/services/letter_service.py:948`). Fetch requests call `LetterService.get_langgraph_snapshot` (`backend/rbac_backend/services/letter_service.py:984`).

## Pipeline Structure (LangGraph Facsimile)
`LetterDraftGraph.run` (`backend/rbac_backend/ai_workflows/langgraph/letter_pipeline.py:98`) mimics a LangGraph DAG with deterministic async “nodes”. Each node is wrapped by `_exec_node`, capturing timing and payloads for trace replay.

- **load_state** (`letter_pipeline.py:126`-`letter_pipeline.py:140`): loads the letter record and surfaces status/metadata used later for routing.
- **collect_context** (`letter_pipeline.py:142`-`letter_pipeline.py:303`): merges explicit `document_ids`, stored context, and fallback lookups by letter number. It filters by organisation/project, pulls document metadata & comments, loads the conversation chain via `ConversationService.get_conversation_chain` (`backend/rbac_backend/services/conversation_service.py:32`), and, when Falkor is enabled, hydrates the surrounding graph thread with `FalkorGraphService.get_thread`.
- **plan_response** (`letter_pipeline.py:305`-`letter_pipeline.py:410`): distils key points from the letter subject, drafter inputs, document summaries, and recent comments. It emits a plan, `summary_points`, and a combined `background_summary` that the UI presents.
- **analysis_only branch** (`letter_pipeline.py:413`-`letter_pipeline.py:435`): short-circuits drafting when the background endpoint is used, returning metadata plus empty draft content.
- **draft_letter** (`letter_pipeline.py:438`-`letter_pipeline.py:470`): augments the drafter’s context with the generated plan and calls the injected `draft_callback` (AIService’s text generator) to produce body text and bullet points.
- **validate_and_route** (`letter_pipeline.py:472`-`letter_pipeline.py:489`): checks workflow status using `workflow_engine` to warn if the letter is terminal or to propose next transitions.
- **Falkor synchronisation** (`letter_pipeline.py:491`-`letter_pipeline.py:536`): normalises selected documents into `agent`-sourced references and calls `FalkorGraphService.upsert_letter_with_refs`, refreshing the thread for downstream viewers.
- The final assembly (`letter_pipeline.py:537`-`letter_pipeline.py:554`) packages plan, draft, context artefacts, warnings, and trace entries into a `LetterGraphResult` for persistence.

## Persistence & Retrieval
- `LetterService.record_langgraph_result` stores timing, warnings, plan text, draft body, trace logs, background items, context document IDs, and the Falkor thread on the letter record (`letter_service.py:948`-`letter_service.py:982`).
- `LetterService.get_langgraph_snapshot` rehydrates the stored payload into a `LangGraphDraftResponse`, including `LetterDraftResponse` for the body and reconstructed trace entries (`letter_service.py:984`-`letter_service.py:1024`). This is what the `/runs/{letter_id}` endpoint returns to the client.

## Frontend Consumption
- The drafting UI uses `useLanggraphDraft` (`client/src/hooks/useLanggraphDraft.ts:16`) to POST either the draft or background endpoints, auto-selecting `/background` when `analysisOnly` is set.
- `useLetterGraphRuns` (`client/src/hooks/useLetterGraphRuns.ts:5`) fetches and caches the latest run snapshot for a letter using the GET endpoint.
- `LetterDraftPage` (`client/src/pages/LetterDraftPage.tsx:31`) combines both hooks: it hydrates state with stored context document IDs, renders LangGraph summaries (`PlanViewer`, `BackgroundSummary`, `GraphStatusBadge`), and presents the refreshed draft. Trigger buttons call `runDraft` for regeneration and `fetchRun` for refresh, wiring the LangGraph outputs into the broader letter workflow.
- Types such as `LanggraphDraftResponse` and supporting structures live in `client/src/types/langgraph.ts`, ensuring the UI maps context documents, background entries, and trace data exposed by the backend.

## Supporting Services
- Document metadata, summaries, and comment extracts originate from `DocumentService` calls inside `collect_context`, while `ConversationService` and `FalkorGraphService` provide threaded context. The workflow engine (`backend/rbac_backend/services/workflow.py`) drives status-based warnings, allowing LangGraph runs to signal whether additional manual transitions are required.

This arrangement keeps LangGraph orchestration server-side, exposes deterministic traces for auditing, and feeds the drafting workspace with plan, background, and citation context without needing the external LangGraph runtime.
