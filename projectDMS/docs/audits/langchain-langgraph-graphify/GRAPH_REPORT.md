# Graph Report - C:\Users\santo\AppData\Local\Temp\claude\C--SaaS-projectDMS\83fa85f7-4172-438c-b437-c368366780d4\scratchpad\gfy\corpus  (2026-09-27)

## Corpus Check
- 73 files · ~0 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 1854 nodes · 4996 edges · 54 communities
- Extraction: 97% EXTRACTED · 3% INFERRED · 0% AMBIGUOUS · INFERRED: 158 edges (avg confidence: 0.63)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 12|Community 12]]
- [[_COMMUNITY_Community 13|Community 13]]
- [[_COMMUNITY_Community 14|Community 14]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Community 50|Community 50]]
- [[_COMMUNITY_Community 51|Community 51]]
- [[_COMMUNITY_Community 52|Community 52]]
- [[_COMMUNITY_Community 53|Community 53]]

## God Nodes (most connected - your core abstractions)
1. `DraftRunService` - 139 edges
2. `ArbitrationCaseWorkspaceService` - 129 edges
3. `ArbitrationDraftingService` - 79 edges
4. `_jsonable()` - 47 edges
5. `ArbitrationWorkflowRepository` - 44 edges
6. `ArbitrationWorkflowService` - 44 edges
7. `DeterministicArbitrationAgent` - 43 edges
8. `DraftRunRepository` - 41 edges
9. `LetterService` - 41 edges
10. `_load_case_and_authorize()` - 39 edges

## Surprising Connections (you probably didn't know these)
- `ArbitrationCaseWorkspaceService` --uses--> `ArbitrationDraftExporter`  [INFERRED]
  backend/rbac_backend/services/arbitration_drafting/case_workspace.py → backend/rbac_backend/services/arbitration_drafting/exporter.py
- `ArbitrationCaseWorkspaceService` --uses--> `ArbitrationDraftingRepository`  [INFERRED]
  backend/rbac_backend/services/arbitration_drafting/case_workspace.py → backend/rbac_backend/services/arbitration_drafting/repository.py
- `ArbitrationCaseWorkspaceService` --uses--> `ArbitrationWorkflowRepository`  [INFERRED]
  backend/rbac_backend/services/arbitration_drafting/case_workspace.py → backend/rbac_backend/services/arbitration_drafting/workflow_repository.py
- `FilingExportQueue` --uses--> `ArbitrationCaseWorkspaceService`  [INFERRED]
  backend/rbac_backend/services/arbitration_drafting/filing_export_queue.py → backend/rbac_backend/services/arbitration_drafting/case_workspace.py
- `GraphNodeOutput` --uses--> `ArbitrationCaseWorkspaceService`  [INFERRED]
  backend/rbac_backend/services/arbitration_drafting/graph_commands.py → backend/rbac_backend/services/arbitration_drafting/case_workspace.py

## Import Cycles
- 2-file cycle: `backend/rbac_backend/ai_workflows/langgraph/letter_pipeline.py -> backend/rbac_backend/services/letter_service.py -> backend/rbac_backend/ai_workflows/langgraph/letter_pipeline.py`

## Communities (54 total, 0 thin omitted)

### Community 0 - "Community 0"
Cohesion: 0.06
Nodes (60): _actor_id(), _add_years(), _collection(), compute_simple_interest(), _condense(), _cost_head(), _dedupe_by_id(), DeterministicArbitrationAgent (+52 more)

### Community 1 - "Community 1"
Cohesion: 0.06
Nodes (97): ArbitrationBundleExportRequest, ArbitrationWorkflowCancelRequest, ArbitrationWorkflowFallbackRequest, ArbitrationWorkflowResumeRequest, add_arbitration_references(), AddReferencesRequest, approve_arbitration_case_readiness(), approve_arbitration_draft() (+89 more)

### Community 2 - "Community 2"
Cohesion: 0.03
Nodes (96): accept_draft(), accept_draft_plan(), add_draft_review_comment(), analyze_incoming_letter(), approve_draft_run(), approve_draft_stage(), assign_draft_reviewer(), cancel_draft_run() (+88 more)

### Community 3 - "Community 3"
Cohesion: 0.07
Nodes (38): ArbitrationSelectedReferenceCreate, _actor_id(), actor_roles(), _configured_roles(), enforce_author_approver_separation(), enforce_gate_role(), enforce_matrix_reviewer_role(), Any (+30 more)

### Community 4 - "Community 4"
Cohesion: 0.08
Nodes (37): _allowed_use(), ArbitrationContextBuilder, _as_list(), _collect(), condense(), _first_present(), _is_rejected(), _is_verified_source() (+29 more)

### Community 5 - "Community 5"
Cohesion: 0.08
Nodes (22): _actor_snapshot(), ArbitrationCaseWorkspaceService, _clean_payload(), _date_sort_value(), _is_positive(), _is_ready_row(), _matrix_review_defaults(), Any (+14 more)

### Community 6 - "Community 6"
Cohesion: 0.07
Nodes (32): condense_text(), DraftContextBuilder, Any, DraftContextBundle, DraftRunCreateRequest, Letter, ProjectEvidenceUniverse, SourceEvidence (+24 more)

### Community 7 - "Community 7"
Cohesion: 0.08
Nodes (27): Any, AssignReviewerRequest, ConfirmAnalysisRequest, ConfirmPlanRequest, DraftCommentRequest, DraftContextPack, DraftMode, DraftRun (+19 more)

### Community 8 - "Community 8"
Cohesion: 0.07
Nodes (25): AbstractEventLoop, ArbitrationGraphCommandExecutor, classify_retry(), Any, ArbitrationWorkflowCreateRequest, Exception, Bind a matrix refresh to a new immutable evidence/revision snapshot., Classify node failures without persisting provider or legal-content details. (+17 more)

### Community 9 - "Community 9"
Cohesion: 0.08
Nodes (30): InvalidLetterIdError, LetterService, LetterServiceError, Any, Letter, NotificationType, Check if a letter exists.                  Args:             letter_id: Letter I, Compatibility alias used by routers. (+22 more)

### Community 10 - "Community 10"
Cohesion: 0.13
Nodes (16): ArbitrationDraftGenerator, _evidence_note(), _facts(), _matrix_counts(), _matrix_rows(), _numbered_sources(), Any, Deterministic source-grounded pleading generator.      This gives the workflow a (+8 more)

### Community 11 - "Community 11"
Cohesion: 0.08
Nodes (18): DraftRunRepository, Any, datetime, DraftContextPack, DraftMode, DraftRun, Persist immutable inputs and a text-free evidence ledger once per run., Find graph runs whose snapshots/checkpoints need safe operator repair. (+10 more)

### Community 12 - "Community 12"
Cohesion: 0.08
Nodes (25): ArbitrationEngineDecision, ArbitrationWorkflowEngine, Any, ArbitrationEngineSelector, canonical_workflow_request_hash(), _items(), Any, DraftEngineDecision (+17 more)

### Community 13 - "Community 13"
Cohesion: 0.07
Nodes (33): LangGraph-oriented workflow utilities for the RBAC backend.  This package contai, Letter drafting LangGraph facsimile., _authorized_contract_evidence_universe(), _eligible_contract_clause_records(), _filter_consumable_graph_entries(), _identity_only_graph_entry(), LetterDraftGraph, LetterGraphNodeTrace (+25 more)

### Community 14 - "Community 14"
Cohesion: 0.07
Nodes (30): categorize_contract(), CategoryInfo, CategoryKey, ContractCategorizer, ContractCategorizerConfig, ContractCategorizerError, create_contract_categorizer(), KeywordMatcher (+22 more)

### Community 15 - "Community 15"
Cohesion: 0.10
Nodes (22): ClauseChunkingAgent, ClauseProcessingSummary, DetectedClause, DetectedTable, DocumentScope, Any, Contract Clause Chunking Agent orchestrator (Phase 2).  Ties the pipeline togeth, Paragraph-aware split of a long clause into parts <= max_len. (+14 more)

### Community 16 - "Community 16"
Cohesion: 0.11
Nodes (23): build_drafting_graph(), LangGraphDraftingEngine, process_drafting_job(), Any, BaseCheckpointSaver, CurrentUser, DraftRun, DraftRunCancelRequest (+15 more)

### Community 17 - "Community 17"
Cohesion: 0.09
Nodes (18): AIAssistantStats, Compatibility shim: route ai_workflows.langgraph imports to the real langgraph p, LangGraphNodeTrace, LangGraphRun, Represents a single node execution trace., Stored LangGraph run metadata for a letter., AIService, Any (+10 more)

### Community 18 - "Community 18"
Cohesion: 0.14
Nodes (14): _epoch(), FilingExportQueue, get_filing_export_queue(), heartbeat_filing_export_job(), _now(), process_filing_export_job(), Any, Exception (+6 more)

### Community 19 - "Community 19"
Cohesion: 0.11
Nodes (14): DraftArtifact, DraftContextBundle, ExactClauseSearchRequest, ExactReferenceSearchRequest, SourceEvidence, ValidationReport, Unified provenance records; labels match the prompt's [S#] tokens., The second door into the same persisted DraftRun.          Whatever this retur (+6 more)

### Community 20 - "Community 20"
Cohesion: 0.16
Nodes (17): AsyncClient, BaseModel, BaseSettings, broadcast_state(), collections(), ContractGraph, ContractState, get_workflow() (+9 more)

### Community 21 - "Community 21"
Cohesion: 0.12
Nodes (8): ArbitrationWorkflowRepository, Any, Claim a replay-safe effect with a recoverable process-death lease., Fence cancellation while one authoritative graph effect is active., Atomically cancel only when no live authoritative effect owns the run., Invalidate downstream gate receipts and rewind active runs after drift., Finalize only receipts already referenced by a successful run CAS.          This, Persist the bounded Phase-6 rollout decision without tenant identifiers.

### Community 22 - "Community 22"
Cohesion: 0.14
Nodes (23): apply_section_edits(), content_hash(), DraftSection, freeze_sections(), preserved_sections(), protected_anchor_changes(), protected_anchors(), Exact-preservation primitives for scoped letter revisions.  The model is never a (+15 more)

### Community 23 - "Community 23"
Cohesion: 0.24
Nodes (6): _actor_id(), ArbitrationWorkflowService, Any, ArbitrationProductionAcceptanceRequest, ArbitrationWorkflowApprovalRequest, ArbitrationWorkflowCreateRequest

### Community 24 - "Community 24"
Cohesion: 0.10
Nodes (24): InvalidLetterTransitionError, LetterNotFoundError, # TODO: In a real application, you might want to:, Exception raised when letter is not found, Exception raised when a workflow status transition is not allowed., can_transition(), compute_pendency_days(), InvalidStatusError (+16 more)

### Community 25 - "Community 25"
Cohesion: 0.14
Nodes (11): _canonical_hash(), Remove response-only identifiers/timestamps from recomputed audit data., _stable_runtime_projection(), ArbitrationDraftExporter, _contents(), _is_separator_row(), _parse_blocks(), Any (+3 more)

### Community 26 - "Community 26"
Cohesion: 0.16
Nodes (5): ArbitrationCaseCreate, ArbitrationDraftingRepository, _collect(), _jsonable(), Any

### Community 27 - "Community 27"
Cohesion: 0.11
Nodes (13): Any, datetime, Service for text processing and metadata parsing, Parse structured extraction report into metadata object.                  Args, Split text into chunks with overlap.                  Args:             text:, Extract single field using regex patterns, Extract list field using regex patterns         Note: Search against (text + "\, Parse block of text into list items (+5 more)

### Community 28 - "Community 28"
Cohesion: 0.16
Nodes (10): DraftingQueue, get_drafting_queue(), _now(), _parse(), Any, datetime, Redis, Dedicated durable Redis queue for official LangGraph drafting runs.  This delibe (+2 more)

### Community 29 - "Community 29"
Cohesion: 0.13
Nodes (24): clear_cache(), get_ai_assistant_stats(), get_langgraph_config(), get_langgraph_run(), get_prompt_by_key(), get_prompts(), health_check(), PromptTemplateUpdateRequest (+16 more)

### Community 30 - "Community 30"
Cohesion: 0.17
Nodes (15): ArbitrationWorkflowDomain, deterministic_merge(), _gated_fact(), _opponent_paragraphs(), Any, Whether a matrix row counts toward the required-matrix presence gate.      App, Distributed bounded-concurrency lease scoped to tenant and project., Execute one read-only evidence branch and persist only its immutable artifact. (+7 more)

### Community 31 - "Community 31"
Cohesion: 0.12
Nodes (16): DraftInputValidator, DraftContextBundle, DraftRunCreateRequest, Letter, SourceEvidence, ValidationReport, Validates the workflow-specific inputs before drafting., DraftValidator (+8 more)

### Community 32 - "Community 32"
Cohesion: 0.10
Nodes (17): DraftGraphState, MongoDraftCheckpointStore, Official LangGraph v3 drafting runtime.  The runtime owns graph orchestration an, Synchronous driver adapter required by langgraph-checkpoint-mongodb 0.4., Versioned, deliberately minimal persisted graph state., _excerpt(), LegalRiskReviewer, Legal / Contractual Risk Review Agent (multi-agent workflow Phase 3).  Determini (+9 more)

### Community 33 - "Community 33"
Cohesion: 0.13
Nodes (17): AIAssistantController, generate_langgraph_strategy_plan(), generate_letter_draft(), Any, LangGraphDraftResponse, LetterDraftRequest, LetterDraftResponse, StrategyPlanRequest (+9 more)

### Community 34 - "Community 34"
Cohesion: 0.12
Nodes (11): ReviseSectionsRequest, Apply an allowed AI edit only to selected, non-frozen draft sections., revise_selected_draft_sections(), DraftRunService, DraftingStartRequest, ReviseDraftRequest, Coordinates v2 letter drafting runs., DraftAuditResponse (+3 more)

### Community 35 - "Community 35"
Cohesion: 0.21
Nodes (20): audit_historical_paragraph_positions(), _candidate_number(), classify_historical_row(), _normalized_paragraph_number(), Any, Conservative review of historical defence/rejoinder paragraph rows.  Legacy draf, resolve_historical_paragraph_position(), _admission_denial() (+12 more)

### Community 36 - "Community 36"
Cohesion: 0.21
Nodes (16): build_prompt_payload(), DraftGenerator, fallback_draft(), fallback_strategy_plan(), format_sources_for_prompt(), parse_draft_artifact(), Any, DraftArtifact (+8 more)

### Community 37 - "Community 37"
Cohesion: 0.19
Nodes (17): acceptance_hash(), acceptance_payload(), Any, datetime, Resolve externally asserted acceptance data to immutable server records.      Re, resolve_server_backed_acceptance(), sign_acceptance(), verify_acceptance_receipt() (+9 more)

### Community 38 - "Community 38"
Cohesion: 0.13
Nodes (11): create_workflow_engine(), Get list of valid transition statuses from current status.                  Args, Get transition history for an item.                  Args:             item_id:, Check if status is a terminal status (no further transitions possible)., Backward compatible alias for is_terminal_status used by LangGraph pipelines., Get list of all valid status strings, Create workflow engine instance, Engine for managing workflow transitions and validation (+3 more)

### Community 39 - "Community 39"
Cohesion: 0.25
Nodes (8): ArbitrationShadowCandidate, Any, Independent, read-only candidate projection for LangGraph shadow parity., Recompute normalized graph inputs without authoritative artifact writes.      Th, _branch_matrices(), _canonical_row(), _evidence_status(), _source_revision_ids()

### Community 40 - "Community 40"
Cohesion: 0.16
Nodes (12): get_ai_controller(), PolicyService, Factory function for AI controller with proper dependencies., get_llm_config_service(), LLMConfigService, LangGraphLLMConfig, Persist and resolve LangGraph LLM configuration for drafter/reviewer., Fetch current configuration with defaults applied. (+4 more)

### Community 41 - "Community 41"
Cohesion: 0.18
Nodes (6): LangChainVectorService, Any, Dual-write vector service backed by LangChain + Qdrant., Delete every vector point belonging to a document from Qdrant.          Returns, Replace document vectors in Qdrant using LangChain with UUID-based point IDs., Perform semantic search against Qdrant and return scored chunks.

### Community 42 - "Community 42"
Cohesion: 0.23
Nodes (8): ensure_strategy_roadmap(), ensure_untrusted_guard(), extract_template_variables(), Any, Ensure DB-overridden strategy prompts still include the required roadmap., Ensure any drafting/strategy prompt carries the injection guard (H1).      Appli, render_prompt(), PromptTemplateRecord

### Community 43 - "Community 43"
Cohesion: 0.22
Nodes (10): GraphNodeOutput, ArbitrationValidationOrchestrator, _claim_source_spans(), _dedupe_issues(), _issue(), Any, Parallel, immutable validation with narrowly bounded safe remediation.      Vali, Return a text candidate only when the no-new-facts invariant holds. (+2 more)

### Community 44 - "Community 44"
Cohesion: 0.22
Nodes (8): ClauseCheckingAgent, normalize_cited_clause(), Any, IncomingLetterAnalysis, Relevant Clause Checking Agent (multi-agent workflow Phase 2).  Verifies the cla, Enrich cited-clause evaluations from structured clause records., _terms(), CitedClauseEvaluation

### Community 45 - "Community 45"
Cohesion: 0.27
Nodes (6): ApprovalStage, ApprovalStep, ApproveStageRequest, One step of the drafter -> reviewer -> final approval chain.          Order is, Legacy single-approve endpoint: advances the next pending stage of         the, Bind a legacy status transition to the exact governed run artifact.

### Community 46 - "Community 46"
Cohesion: 0.27
Nodes (5): _exhibit_volume(), Cheap resolvability check for the citation audit (no byte fetch)., Resolve exhibit bytes: local path first, then S3 (mirrors document download)., Map a document-index row to its guide §19 bundle volume., Path

### Community 47 - "Community 47"
Cohesion: 0.36
Nodes (3): ArbitrationV2WorkflowEngine, Any, ArbitrationWorkflowCreateRequest

### Community 48 - "Community 48"
Cohesion: 0.33
Nodes (6): locked_instruction(), _normalize(), Locked-paragraph support for the Redraft Agent (Phase 3).  Human-approved paragr, Instruction block appended to a revision request., Return the locked paragraphs that do NOT survive in the draft.      Comparison i, verify_locked_paragraphs()

### Community 49 - "Community 49"
Cohesion: 0.40
Nodes (3): Request, request_context_middleware(), _slow_request_threshold_ms()

### Community 50 - "Community 50"
Cohesion: 0.40
Nodes (5): VectorSearchResponse, Search for semantically similar letters with proper security and caching., Search for semantically similar letters., search_similar_letters(), LetterSearchRequest

### Community 51 - "Community 51"
Cohesion: 0.50
Nodes (5): generate_langgraph_background(), generate_langgraph_draft(), LangGraphDraftRequest, Run the LangGraph letter drafting pipeline., Generate LangGraph background without drafting the reply.

### Community 52 - "Community 52"
Cohesion: 0.40
Nodes (4): create_letter_service(), Create letter service instance, Database, NotificationService

### Community 53 - "Community 53"
Cohesion: 0.67
Nodes (3): lock_paragraphs(), LockParagraphsRequest, Lock human-approved paragraphs so AI redrafts cannot change them.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `ArbitrationCaseWorkspaceService` connect `Community 5` to `Community 25`, `Community 26`, `Community 21`, `Community 46`?**
  _High betweenness centrality (0.014) - this node is a cross-community bridge._
- **Why does `DraftRunService` connect `Community 34` to `Community 32`, `Community 36`, `Community 6`, `Community 7`, `Community 11`, `Community 44`, `Community 45`, `Community 19`, `Community 22`, `Community 31`?**
  _High betweenness centrality (0.013) - this node is a cross-community bridge._
- **Why does `ArbitrationDraftingService` connect `Community 3` to `Community 4`, `Community 5`, `Community 10`, `Community 25`, `Community 26`?**
  _High betweenness centrality (0.010) - this node is a cross-community bridge._
- **Are the 13 inferred relationships involving `DraftRunService` (e.g. with `ClauseCheckingAgent` and `DraftContextBuilder`) actually correct?**
  _`DraftRunService` has 13 INFERRED edges - model-reasoned connections that need verification._
- **Are the 3 inferred relationships involving `ArbitrationCaseWorkspaceService` (e.g. with `ArbitrationDraftExporter` and `ArbitrationDraftingRepository`) actually correct?**
  _`ArbitrationCaseWorkspaceService` has 3 INFERRED edges - model-reasoned connections that need verification._
- **Are the 7 inferred relationships involving `ArbitrationDraftingService` (e.g. with `ArbitrationCaseWorkspaceService` and `ArbitrationContextBuilder`) actually correct?**
  _`ArbitrationDraftingService` has 7 INFERRED edges - model-reasoned connections that need verification._
- **What connects `Resolve externally asserted acceptance data to immutable server records.      Re`, `Resolve a matrix reviewer role from server claims or an assignment.      A gener`, `Derive a privileged gate role exclusively from signed identity claims.` to the rest of the system?**
  _340 weakly-connected nodes found - possible documentation gaps or missing edges._