# LangChain / LangGraph — graphs

## Graphify output

- **Where it is:** [`langchain-langgraph-graphify/graph.html`](langchain-langgraph-graphify/graph.html) (interactive) and [`GRAPH_REPORT.md`](langchain-langgraph-graphify/GRAPH_REPORT.md).
- **What it covers:** the 73 AI-surface files, 36,151 lines:
  - `services/arbitration_drafting/**`
  - `services/letter_drafting/**`
  - `ai_workflows/**`
  - `services/langgraph/*`
  - the three AI routers
  - the LangChain services
  - `ai_service`, `llm_config_service`, `letter_service`, `workflow`, `contract_clause/agent`, `models/langgraph_run`, `main`
- **Result:** 1,854 nodes, 4,996 edges, 54 communities. Extraction is 97% EXTRACTED and 3% INFERRED; the INFERRED edges are 158 "uses" edges with an average confidence of 0.63.
- **How it was built:** an AST-only build, with no LLM and no tokens spent. The corpus was copied to a scratch directory so the repo's existing `graphify-out/` was not overwritten. Communities are left numbered rather than hand-labelled.
- **What it found:**
  - God nodes: `DraftRunService` (139 edges), `ArbitrationCaseWorkspaceService` (129), `ArbitrationDraftingService` (79), `ArbitrationWorkflowRepository` / `ArbitrationWorkflowService` (44 each).
  - One import cycle: `ai_workflows/langgraph/letter_pipeline.py` ↔ `services/letter_service.py` (M11).

**Honesty note.** Graphify renders one whole-corpus graph. It cannot produce the eight task-specific views below. Those are **Mermaid diagrams I derived by hand** from the verified evidence (file:line in the [current state](langchain-langgraph-current-state.md) and [blockers](langchain-langgraph-blockers.md)).

In every diagram:
- A solid arrow (`-->`) is an edge verified in code.
- A dotted arrow (`-.->`) is conditional or flag-gated.
- An edge labelled `?` is **unproven**.
- A red node is a defect site.

---

## Graph 1 — Current LangChain / LangGraph architecture

```mermaid
flowchart TB
  subgraph Deps["Backend image deps (backend/rbac_backend/requirements.txt)"]
    LG[langgraph 1.2.9]
    LGM[langgraph-checkpoint-mongodb 0.4.0]
    LCC[langchain-core 1.5.0]
    LCO[langchain-openai 1.1.14]
    LCQ[langchain-qdrant 1.0.0]
    LCT[langchain-text-splitters 1.1.2]
  end
  subgraph Real["Real StateGraphs"]
    ARB[Arbitration graph<br/>langgraph_engine.py:227]
    V3[Letter v3 graph<br/>letter_drafting/langgraph_engine.py:73]
    ORC[Orchestrator graph<br/>services/langgraph - NOT DEPLOYED]
  end
  subgraph Fake["Named 'LangGraph', not LangGraph"]
    LDG[LetterDraftGraph<br/>ai_workflows/langgraph/letter_pipeline.py:138]
  end
  subgraph Live["Live, not LangGraph"]
    ARBV2[Arbitration v2 engine]
    LV2[Letter DraftRunService v2]
  end
  subgraph LC["LangChain usage (live)"]
    LCVS[LangChainVectorService]
    CAT[contract_categorizer LLMService]
    TPS[TextProcessingService splitter]
  end
  LG --> ARB & V3 & ORC
  LGM --> ARB & V3
  LCO --> LCVS & CAT
  LCQ --> LCVS
  LCC --> CAT
  LCT --> TPS
  V3 -->|calls domain stages| LV2
  ARB -. mirrors transitions .-> ARBV2
  LCVS --> QD[(Qdrant)]
  ARB --> MG[(Mongo)]
  V3 --> MG
  V3 --> RD[(Redis queue)]
  ORC -. MemorySaver .-> MEM[(process memory)]
```

Three real graphs: two are gated off by default and one is undeployed. The live paths (v2 engines, legacy pipeline, LangChain services) are not LangGraph.

---

## Graph 2 — Runtime call graph (UI → API → service → queue → worker → graph → LLM/tools → persistence)

```mermaid
flowchart LR
  subgraph UI
    AWP[ArbitrationCaseWorkspacePage]
    LDP[LetterDraftPage / LetterStrategicPlanPage]
    HK[useLetterDrafting]
    HG[useLanggraphDraft - loading flag only]
  end
  AWP --> AAPI[arbitration-drafting-api.ts] --> RARB[/api/arbitration/cases/:id/workflows/]
  LDP --> HK --> RLD[/api/letters/:id/drafting/runs/]
  LDP --> RGR[GET /api/ai-assistant/langgraph/runs/:id]
  HG -. never calls runDraft .-> RAI[/api/ai-assistant/langgraph/draft|background|strategy-plan/]
  RARB --> WFS[ArbitrationWorkflowService] --> SEL1{engine policy}
  SEL1 -->|default| AV2[v2 engine - request task]
  SEL1 -.->|canary/primary| LGE[LangGraphArbitrationEngine._checkpoint<br/>asyncio.to_thread graph.invoke IN REQUEST]
  LGE --> GC[graph_commands executor] --> DOM[domain services]
  RLD --> SEL2{DraftEngineSelector}
  SEL2 -->|default| DRS[DraftRunService.create_run - sync 200]
  SEL2 -.->|langgraph_v3 + queue flags| ENQ[DraftingQueue.enqueue] --> RQ[(Redis letter_drafting_queue)] --> CW[contract-worker _worker_loop] --> PDJ[process_drafting_job] --> V3G[v3 graph]
  PDJ --> DRS
  RAI --> AIS[AIService] --> LDG[LetterDraftGraph.run] --> LLMG
  DRS --> LLMG[LLMGenerator - AsyncOpenAI]
  DOM -.->|ARBITRATION_DRAFT_MODE=llm| LLMA[LLMArbitrationAgent]
  DRS --> RET[RetrievalService / Qdrant]
  LDG --> RET
  LGE --> CPA[(arbitration_langgraph_checkpoints)]
  V3G --> CPL[(letter_draft_langgraph_checkpoints)]
  DRS --> LRUNS[(letter_draft_runs)]
  AV2 --> ARUNS[(arbitration workflow runs/effects)]
  LDG --> LET[(letters doc)]
  classDef bad fill:#fdd,stroke:#c00
  class LLMG,LDG,LGE bad
```

- `LLMG` is marked as a defect site for B3 (silent fallback prose), `LDG` for B1 (unscoped load and write), and `LGE` for H7 (in-request execution).
- The exports that follow approval are omitted here.

---

## Graph 3 — Every LangGraph state machine

### 3a. Arbitration (`langgraph_engine.py:304-408`)

```mermaid
stateDiagram-v2
  [*] --> validate_intake
  validate_intake --> capture_input_snapshot
  capture_input_snapshot --> document_selection_gate
  state fan1 <<fork>>
  document_selection_gate --> fan1 : interrupt / resume
  fan1 --> analyze_documents
  fan1 --> analyze_chronology
  fan1 --> analyze_clause_jurisdiction_notice
  fan1 --> analyze_pleading_position
  fan1 --> analyze_quantum_expert
  state join1 <<join>>
  analyze_documents --> join1
  analyze_chronology --> join1
  analyze_clause_jurisdiction_notice --> join1
  analyze_pleading_position --> join1
  analyze_quantum_expert --> join1
  join1 --> merge_evidence_and_matrices
  merge_evidence_and_matrices --> material_question_gate
  material_question_gate --> matrix_review_gate
  matrix_review_gate --> readiness_approval_gate
  readiness_approval_gate --> build_pleading_plan
  build_pleading_plan --> plan_approval_gate
  plan_approval_gate --> generate_draft
  state fan2 <<fork>>
  generate_draft --> fan2
  fan2 --> validate_x8 : citations, assertions, legal_structure,<br/>new_matter, quantum, duplication, exhibits, source_drift
  validate_x8 --> merge_validation_artifacts
  merge_validation_artifacts --> remediate_draft : validation_route=remediate
  remediate_draft --> validate_x8 : loop (MAX_REMEDIATION_CYCLES=1)
  merge_validation_artifacts --> legal_review_gate : review
  legal_review_gate --> draft_approval_gate
  draft_approval_gate --> commit_draft_approval
  commit_draft_approval --> export_authorization_gate
  export_authorization_gate --> create_filing_export
  create_filing_export --> complete
  complete --> [*]
  note right of draft_approval_gate : B4 - resume passes gate<br/>without re-checking flag
  note right of create_filing_export : B5 - actor has no roles -> 403
```

- Every step also has a `continue_or_cancel` / `fanout_or_cancel` edge to `cancelled`, then `[*]`. They are omitted for legibility.
- Gates: `document_selection`, `material_question`, `matrix_review`, `readiness_approval`, `plan_approval`, `legal_review`, `draft_approval`, `export_authorization`. Each calls `interrupt({kind, run_id, state_version})`.

### 3b. Letter drafting v3 (`letter_drafting/langgraph_engine.py:151-160`)

```mermaid
stateDiagram-v2
  [*] --> collect_evidence : flag only
  collect_evidence --> review_correspondence : always True
  review_correspondence --> user_direction_gate
  user_direction_gate --> strategy_gate : interrupt if questions_required
  strategy_gate --> draft_generation : interrupt ALWAYS
  draft_generation --> validation_review : raise if marker false
  validation_review --> legal_risk_review
  legal_risk_review --> approval_gate
  approval_gate --> prepare_execution
  prepare_execution --> [*]
  note left of draft_generation : All real work runs OUTSIDE the graph in<br/>process_drafting_job -> v2 DraftRunService,<br/>injected via Command(resume, update)
```

This graph has no conditional edges and no loops. `cancellation_requested` is never set to True (`:878`).

### 3c. Standalone orchestrator (`services/langgraph/orchestrator.py:113-126`, not deployed)

```mermaid
stateDiagram-v2
  [*] --> ingest
  ingest --> analyze
  analyze --> compliance
  compliance --> risk
  risk --> report
  report --> [*]
  note right of ingest : MemorySaver - lost on restart
```

### Not a state machine: `LetterDraftGraph`

It runs its nodes in a fixed order through `_exec_node`:

```
load_state → collect_context → retrieve_sources → plan_response (LLM) → draft_letter (LLM) → review_draft (LLM) → validate_and_route → Falkor write
```

It returns early on `analysis_only`. There is no checkpoint and no interrupt.

---

## Graph 4 — Agent / tool relationships

```mermaid
flowchart TB
  subgraph Arbitration
    DAA[DeterministicArbitrationAgent] --> DS[ArbitrationDraftingService]
    LAA[LLMArbitrationAgent<br/>subclass] -. ARBITRATION_DRAFT_MODE=llm .-> LDGEN[LLMDraftGenerator]
    LDGEN -->|failure| DAA
    VAL[8 deterministic validators] --> DS
  end
  subgraph Letters
    SP[StrategyPlanner] --> LLMG[LLMGenerator]
    DG[DraftGenerator] --> LLMG
    SE[ScopedSectionEditor] -->|strict=True -> TypeError H2| LLMG
    LRR[LegalRiskReviewer - deterministic]
    LDG[LetterDraftGraph plan/draft/review] --> LLMG
  end
  subgraph Ingestion
    CAT[Categorizer LLMService] --> CO[ChatOpenAI]
    CCA[contract_clause agent - plain service] --> QD[(Qdrant contract_clauses)]
  end
  LLMG --> OAI[OpenAI chat.completions]
  CO --> OAI
  RET[RetrievalService - clause-first] --> QD
  DS --> RET
  DG --> RET
  NT[No ToolNode / bind_tools / create_react_agent anywhere]:::note
  classDef note fill:#eee,stroke:#999,stroke-dasharray:3
```

"Agents" are service classes or prompt wrappers. None of them selects tools.

---

## Graph 5 — Persistence, checkpoint and recovery

```mermaid
flowchart LR
  subgraph Arbitration
    AR[run row: state_version CAS] --- AE[effects: unique effect_key<br/>lease 120s, heartbeat 30s, max 5]
    AR --- AA[approval receipts pending->committed]
    AR --- ACP[(arbitration_langgraph_checkpoints<br/>MongoDBSaver, TTL 30d)]
    ACP -. TTL expired .-> REB[_recovery_state rebuild from ledger<br/>resume update DROPPED M6]
    AE -. lease expired + same node re-run .-> RECL[lazy reclaim]
    SYNC[checkpoint_sync_status=pending] -. no re-sync job ? .-> X1((nothing))
  end
  subgraph LetterV3
    LR[letter_draft_runs: state_version CAS<br/>NO status predicate H5] --- LE[letter_draft_effects unique effect_key]
    LR --- LCP[(letter_draft_langgraph_checkpoints<br/>DB from DATABASE_URL path M5)]
    RJ[(Redis job hash - no TTL)] --> RP[processing list + heartbeat] -->|3 retries| DL[(deadletter)]
    DL -. no write-back B6 .-> LR
    OUT[(letter_draft_outbox)] -. no consumer .-> X2((nothing))
    RC[reconciliation_candidates] -. no caller .-> X3((nothing))
  end
  subgraph Restart
    API[API restart] -->|arbitration in-request runs abort mid-node| AR
    W[worker restart] -->|visibility timeout requeue| RJ
    MG[Mongo restart] -->|checkpoint write raises; arbitration fails closed 503| ACP
    RS[Redis restart] -->|queued jobs lost ? no AOF evidence read| RJ
  end
```

- The Redis restart edge is **unproven**: I did not inspect the persistence settings of the production Redis.
- **No startup recovery loop exists in `main.py`.**

---

## Graph 6 — Failure, retry and cancellation paths

```mermaid
flowchart TB
  subgraph LLM
    F1[OpenAI error / no key] --> G1[LLMGenerator returns fallback prose] --> G2[stored as COMPLETED draft - B3]
  end
  subgraph ArbitrationNode
    N1[node raises] --> N2{retryable?}
    N2 -->|transient| N3[effect retry <= 5]
    N2 -->|permanent| N4[effect failed non-retryable]
    N4 --> N5[claim_effect 409 'already active' forever - H3]
    N1 --> N6[run left running at non-gate node<br/>resume/approve refuse; fallback forbidden - H3]
    C1[cancel] --> C2{idle + lease expired?}
    C2 -->|yes| C3[cancelled via CAS]
    C2 -->|no| C4[cancellation_requested -> routed to cancelled at next edge]
  end
  subgraph LetterV3Job
    J1[stage raises] --> J2[Redis retry x3] --> J3[deadletter] --> J4[Mongo still running - B6]
    K1[cancel during run] --> K2[cancel_requested] -.->|checked only before claim| K3[final write overwrites status - H5]
    R1[retry after collect_evidence] --> R2[persisted running/poll while graph paused - H5]
  end
  subgraph Vector
    Q1[Qdrant upsert error] --> Q2[return 0 + warning] --> Q3[status mismatch]
    Q4[LangChain init error] --> Q5[memoized disabled] --> Q6[status mongo_only, no alarm - M2]
  end
```

---

## Graph 7 — RBAC and tenant boundaries around AI workflows

```mermaid
flowchart LR
  U[CurrentUser<br/>EffectiveScope] --> P{PolicyService.authorize}
  subgraph Sound
    A1[/arbitration/*/] --> S1[_load_case_and_authorize<br/>build_scope_query + authorize_document] --> RUN1[run bound to case_id]
    L1[/letters/:id/drafting/runs/*/] --> S2[_load_and_authorize letter] --> RUN2[run by letter_id+run_id]
    RQA[/v1/contract-qa/] --> S3[re-authorize against document scope]
  end
  subgraph Broken
    AI[/ai-assistant/langgraph/*/] --> P2[authorize CLAIMED scope] --> LOAD[get_letter unscoped] --> W1[letters.update_one unscoped - B1]
    RS[/v1/retrieval/search|rag/] --> P3[authorize filters.org_id] --> MERGE[filters.metadata spread LAST overrides org_id - B2] --> QD[(Qdrant other tenant)]
    DP[/deep-planning/generate-draft/] --> P4[authorize claimed scope] --> FL[find_one target_letter_id unscoped - H1] --> PROMPT[LLM prompt]
    OPS[/ops/letter-drafting/runs/:run_id/checkpoints/] --> P5[authorize with NO org/project] --> CK[get_by_run_id unscoped - M1]
  end
  subgraph Graph
    FK[FalkorDB Letter nodes MERGE on normCode only] -. accepted residual .-> X[cross-org code existence]
  end
  U --> A1 & L1 & RQA & AI & RS & DP & OPS
  classDef bad fill:#fdd,stroke:#c00
  class W1,MERGE,FL,CK bad
```

---

## Graph 8 — Proposed target architecture

See [target architecture](langchain-langgraph-target-architecture.md#target-shape) for the rationale.

```mermaid
flowchart LR
  UI[SPA] -->|intent-keyed Idempotency-Key| API[Routers: scoped load BEFORE work<br/>return 202]
  API --> M[(Mongo run ledger / effects / receipts)]
  API --> Q[(Redis queue)]
  Q --> W[contract-worker executor]
  W -->|arbitration| LG[LangGraph arbitration<br/>shared MongoDBSaver, gates re-check flags]
  W -->|letters| V2[DraftRunService v2 - no LangGraph]
  LG --> DC[shared domain commands]
  V2 --> DC
  DC --> GW[LLM gateway: strict / Degraded result / usage capture / timeouts]
  DC --> RET[RetrievalService + native vector_client<br/>reserved scope keys written last]
  RET --> QD[(Qdrant single writer)]
  DC --> FK[(FalkorDB)]
  LG --> CP[(checkpoints, one TTL source)]
  REC[reconciler + sweeper] --> M & Q
  OBS[metrics: stuck runs, DLQ depth, degraded LLM, tokens/tenant] -.-> W
  RET_GONE[retired: letter v3, LetterDraftGraph, orchestrator,<br/>LangChainVectorService, langchain-qdrant/openai]:::gone
  classDef gone fill:#eee,stroke:#999,stroke-dasharray:4
```
