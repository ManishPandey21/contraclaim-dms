# Role-Based Strategy Implementation Plan

_Sources referenced:_ `IMPLEMENTATION-SUMMARY.md`, `integrated-workflow-v2.md`, `role-based-strategy-system.md`, live repo inspection (Nov 2025 build).

## Current State

- Workflow already exposes a **Strategy** stage between Input and Draft, and the LangGraph pipeline can return a 5-section plan (tone, structure, responses, risks, outcomes).
- The codebase **lacks role awareness**: no consolidated Contractor/Engineer/Employer contexts, no role selection UI, and the strategy API only consumes a single generic context.
- There are **no endpoints** for generating/refreshing three-way contexts or persisting role metadata on the letter record.

## Target Capabilities (from docs)

1. Users select a role (Contractor / Engineer / Employer) before generating the plan.
2. System builds **three consolidated contexts** by traversing the letter thread, using full letter bodies.
3. Strategy generation uses role-specific prompts and stores a structured outline so Draft stage can reference it.
4. Letter schema tracks contexts, selected role, and plan approvals for analytics.

## Implementation Plan

### 1. Data & Models

- **Letter schema**
  - Add fields for `contractor_context`, `engineer_context`, `employer_context`, `strategy_role`, `strategy_recipient`,
    `thread_id`, `thread_letters`, `correspondence_type`, `parties_involved`, `strategy_started_at`, `strategy_completed_at`,
    `duration_strategy`.
  - Update `LetterUpdate`/`LetterCreate` plus client-side mapping (`UILetter`) and hook types.
- **Strategy payload models**
  - Extend `StrategyPlanRequest/Response` to carry role name, audience, consolidated contexts, and summary points.
  - Add `StrategyContextRequest/Response` models for the new context endpoint.

### 2. Backend Services & Endpoints

1. Create `StrategyContextService`
   - Uses `ConversationService` + `PartyService` to pull the full thread.
   - Applies keyword + party-name heuristics (from role-based doc) to bucket each letter into Contractor / Engineer / Employer buckets.
   - Produces timeline metadata plus concatenated context strings.
2. Add REST endpoints:
   - `POST /letters/{id}/strategy/context` → generate + persist contexts & timeline fields on the letter document; returns the contexts.
   - `PATCH /letters/{id}/strategy-role` (or reuse update endpoint) to persist selected role / recipient.
3. Update `AIService.generate_strategy_plan` to:
   - Accept the role-aware request.
   - Compose a role-prefixed context string (embedding the three contexts + selected role) before calling LangGraph in `analysis_only` mode.
   - Persist the structured outline (`strategic_outline`) + plan metadata using enhanced `LetterService.record_langgraph_result`.

### 3. LangGraph / Persistence

- Ensure `LetterGraphResult` and `LetterService.record_langgraph_result` retain structured fields (`tone_approach`, `content_structure`, etc.) inside `strategic_outline`.
- When running in analysis-only mode, map those fields to the new `StrategyPlanResponse`.
- Track `strategy_run_id`, `strategy_graph_status`, `strategy_graph_trace`, and timestamps separately from drafting runs.

### 4. Frontend Enhancements

1. **API layer**
   - Add client service functions (`generateStrategyContexts`, `saveStrategyRole`, updated `useLanggraphStrategyPlan` payload).
2. **State & types**
   - Extend `useLetterWorkflow` and `UILetter` to hold contexts, role metadata, and new graph fields.
3. **UI components**
   - `RoleSelector` card (radio/select) + optional audience select when Engineer is chosen.
   - `ContextPreviewTabs` component that exposes Contractor/Engineer/Employer context strings with copy actions.
   - Update `StrategyPlanDisplay` to accept role metadata (still five sections for now, matching current LangGraph output).
4. **LetterStrategicPlanPage**
   - Load previously saved contexts/role.
   - Provide “Generate Contexts” CTA tied to the new endpoint.
   - Disable plan generation until contexts exist + role chosen.
   - When generating the plan, send role, recipient, and contexts to the updated hook.
   - Persist approvals (strategy → draft transition) and new metadata.

### 5. Validation & UX

- Handle empty contexts with helpful messaging (prompt to generate contexts).
- Surface API errors (context generation, plan generation) via toasts.
- Ensure Draft page redirects to Strategy when `strategy_plan` missing or role not approved.
- Add default heuristics + fallback text for contexts to avoid blank results.

## Deliverables

- Updated backend schema/models/services/endpoints.
- New frontend components/services enabling role selection & context previews.
- End-to-end strategy experience aligning with role-based requirements.
- Documentation (this file) describing the implementation path.
