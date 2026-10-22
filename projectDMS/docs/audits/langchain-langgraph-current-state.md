# LangChain / LangGraph — current state (read-only audit)

- **Date:** 2026-09-27
- **Tree audited:** `C:\SaaS\projectDMS`, HEAD `a179ca9` (branch `integrate/key-date-eot`). The working tree is dirty, and files were read as they are on disk.
- **Mode:** read-only. No source, configuration, commits, pushes or deploys. The only writes are this `docs/audits/langchain-langgraph-*` set and `docs/audits/langchain-langgraph-graphify/`.
- **Production environment values were not read.** Every statement about "default configuration" refers to `core/config.py`, `.env.example` and `docker-compose.prod.yml` defaults. Whether the production `.env` flips any flag is **UNVERIFIED**.

Companion documents:

- [blockers](langchain-langgraph-blockers.md)
- [target architecture](langchain-langgraph-target-architecture.md)
- [graphs](langchain-langgraph-graphs.md)

## How this was established

| Method | What it produced |
|---|---|
| Repo-wide grep for the Phase-1 term list (`langgraph`, `langchain`, `StateGraph`, `MemorySaver`, `MongoDBSaver`, `interrupt`, `Command`, `Send`, `bind_tools`, `with_structured_output`, `ChatPromptTemplate`, …) | 199 files hit. Only 10 Python files import `langgraph`/`langchain` directly. |
| `graft` call-graph queries (callers, blast radius) | Every "who calls this" claim below |
| Four parallel read-only investigations: arbitration graph, letter-drafting graph, RBAC/tenancy + test inventory, LangChain/LLM layer | The LangChain/LLM investigation stalled; I finished that scope directly |
| Targeted test runs with `backend/.venv` and `FALKOR_TEST_HOST=falkor.invalid` | Arbitration: 6 files, **147 passed / 13 skipped**. Letter LangGraph: 3 files, **14 passed / 0 skipped** |
| One executed probe of the arbitration graph (a real `StateGraph` + `InMemorySaver`) | Reproduced the gate-skip defect (B4) |
| Graphify AST graph of the 73 AI-surface files | 1,854 nodes, 4,996 edges, 54 communities; see [graphs](langchain-langgraph-graphs.md) |

Evidence labels used throughout:

- **Verified**: I read the code or executed it.
- **Code-read**: the mechanism was read but not executed.
- **UNVERIFIED**: stated but not checked.

## Headline

ContraClaim contains **three real LangGraph `StateGraph`s**:

1. The arbitration pleadings workflow.
2. The letter-drafting "v3" engine.
3. An undeployed standalone microservice.

**None of them is reachable with the shipped default configuration.** All live AI drafting today runs through ordinary custom Python services. Those services call the OpenAI SDK directly, apart from a little LangChain in ingestion.

LangChain itself is present and **live**, but only in three narrow places:

- a text splitter;
- a Qdrant/OpenAI-embeddings dual-write vector store;
- one `ChatOpenAI` classifier in contract ingestion.

The name "LangGraph" is also attached to a fourth, **fake** graph: `ai_workflows/langgraph/LetterDraftGraph`, a hand-rolled sequential pipeline whose own docstring says it emulates LangGraph "without introducing the external dependency". Its server endpoints are live, but the current UI does not use them to generate drafts.

## Dependencies (what actually ships)

| Manifest | Used by | LangChain/LangGraph pins |
|---|---|---|
| `backend/rbac_backend/requirements.txt` | Backend Docker image (`backend/Dockerfile:26-27`) and CI (`C:\SaaS\.github\workflows\ci.yml:67`) | `langchain-core==1.5.0`, `langchain-text-splitters==1.1.2`, `langgraph==1.2.9`, `langgraph-checkpoint-mongodb==0.4.0`, `langchain-openai==1.1.14`, `langchain-qdrant==1.0.0` (L101-110) |
| `requirements.txt` (repo root) | Nothing found in Docker or CI (**orphan**) | Drifted: `langchain-openai==1.0.1` and `langchain-qdrant==1.1.0`, plus `langchain`, `langchain-classic`, `langchain-community`, `langgraph-prebuilt`, `langgraph-sdk` (L103-115) |
| `services/langgraph/requirements.txt` | CI builds and Trivy-scans this image (`projectDMS/.github/workflows/ci.yml:173-203`), but no compose file runs it | `langgraph==1.2.9`, `langgraph-checkpoint-mongodb==0.4.0` |

The same backend image also carries **LlamaIndex** (`services/llamaindex_service.py`), **PydanticAI** (`services/pydantic_ai_service.py`) and the raw **OpenAI SDK** (`retrieval/generator.py`). LangGraph is therefore one of five overlapping AI frameworks in a single process (finding M14).

## Inventory

**Status key**

- ACTIVE: reachable and executed with default configuration.
- PARTIALLY ACTIVE: reachable, but only part of it runs or matters.
- UNREACHABLE: wired, but gated off by default.
- DEAD CODE: no runtime caller.
- EXPERIMENTAL: not deployed.
- TEST ONLY.

| # | Component | File / module | LC/LG dependency | Purpose | Entry point | Runtime owner | Persistence | Reachable? | Tested? | Feature flag (default) | Prod configured? | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Arbitration pleadings graph | `services/arbitration_drafting/langgraph_engine.py:227-409` (`build_arbitration_graph`) plus `graph_commands.py` executor | `langgraph` `StateGraph`, `interrupt`, `Command`; `langgraph-checkpoint-mongodb` `MongoDBSaver` | 8 human gates, 5-way analysis fan-out, 8-way validation fan-out, remediation loop, approval, filing export | `POST /api/arbitration/cases/{id}/workflows` (+ resume, approvals, cancel) from `ArbitrationCaseWorkspacePage.tsx` | The HTTP request task. `asyncio.to_thread(graph.invoke)` runs synchronously in the request; there is no worker | `MongoDBSaver` → `arbitration_langgraph_checkpoints`/`_writes` (TTL 30 d), plus a Mongo run ledger and effect leases | **No by default.** Selected only when `ARBITRATION_ENGINE_ROLLOUT_MODE=canary`/`primary` and `ARBITRATION_ENGINE_DEFAULT=langgraph_v1` (`engines/policy.py:107-167`) | Yes: 136 unit tests (fakes + `InMemorySaver`). Real-Mongo kill/TTL/load drills are env-gated and skipped in CI | `ARBITRATION_ENGINE_DEFAULT=arbitration_v2`, `ROLLOUT_MODE=off`, `PRODUCTION_ACCEPTED=false` (`config.py:282-292`) | Defaults in `docker-compose.prod.yml:81-112`; actual prod `.env` UNVERIFIED | **UNREACHABLE** |
| 1a | Arbitration shadow projection | `langgraph_engine.py:450-475` | Same graph, `InMemorySaver`, no executor | Parity projection alongside v2 | v2 create/approve when `ROLLOUT_MODE=shadow` | Request task; failures swallowed (`workflow_service.py:199-300`) | Memory only | Only in shadow mode | Yes (unit) | `ROLLOUT_MODE=shadow` | No | **UNREACHABLE** |
| 2 | Arbitration v2 engine | `engines/v2.py:29-180`, `workflow_service.py:908-1272` | None (custom). Its `thread_id` naming mirrors LangGraph | The live arbitration workflow | Same routes | Request task | Mongo run ledger | Yes | Yes | default | Yes | **ACTIVE** (not LangGraph) |
| 3 | Letter drafting v3 graph | `services/letter_drafting/langgraph_engine.py:73-161` (`build_drafting_graph`) | `StateGraph`, `interrupt`, `Command`, `MongoDBSaver` | Linear 10-node shell. **Every node is a deterministic marker.** Real work runs in `process_drafting_job` (`:818-900`) by calling the v2 service, and results are injected with `Command(resume, update)` | `POST /api/letters/{id}/drafting/runs` returns 202, then Redis queue `letter_drafting_queue`, then `contract-worker` (`python -m rbac_backend.worker`) | Worker process `_worker_loop` → `process_drafting_job` | `MongoDBSaver` → `letter_draft_langgraph_checkpoints`/`_writes` (TTL 30 d); Redis job hash; Mongo `letter_draft_runs` | **No by default.** Needs `DRAFT_ENGINE_DEFAULT=langgraph_v3` + canary/primary + `DRAFTING_QUEUE_ENABLED=true` (backend and worker) + `START_DRAFTING_QUEUE_WORKERS=true` | 7 unit tests (`InMemorySaver`) + authority red tests. No queue, kill, cancel or real-Mongo test | `DRAFT_ENGINE_DEFAULT=v2`, `DRAFT_ENGINE_ROLLOUT_MODE=off`, `DRAFTING_QUEUE_ENABLED=false`, `START_DRAFTING_QUEUE_WORKERS=false` (`config.py:272-275, 450-451`) | Defaults in `docker-compose.prod.yml:69-78, 113-121`; actual prod UNVERIFIED | **UNREACHABLE** |
| 4 | Letter drafting v2 | `services/letter_drafting/service.py:136-603` (`DraftRunService.create_run`) | None. LLM via `retrieval/generator.py` (raw `AsyncOpenAI`) | Live contractual-letter drafting: context, strategy plan, draft, cyclic validation, legal risk, approvals | Same POST, answered synchronously with 200 | Request task | Mongo `letter_draft_runs`, `draft_context_packs`, `letter_draft_events` | Yes | ~120 mock tests | default | Yes | **ACTIVE** (not LangGraph) |
| 5 | "LangGraph" letter pipeline | `ai_workflows/langgraph/letter_pipeline.py:138` (`LetterDraftGraph`) | **None.** Custom sequential `_exec_node` runner (`:1777`); `ai_workflows/__init__.py:1-6` says so | Load letter → context → retrieval → plan (LLM) → draft (LLM) → review (LLM) → route → FalkorDB write | `POST /api/ai-assistant/langgraph/{draft,background,strategy-plan}` (`routers/ai_assistant.py:411-442`) | Request task | Writes into the `letters` document (`letter_service.py:1258-1366`) | Server routes live; client `useLanggraphDraft.runDraft` never called, `useLanggraphStrategyPlan` has no callers. `GET …/langgraph/runs/{id}` is used for display (`LetterDraftPage.tsx:95`) | Authority red tests (mock) | None. `LANGGRAPH_ENABLED` gates nothing (`config.py:252, 793`) | Live | **PARTIALLY ACTIVE** (API live, UI generation path unused) |
| 6 | Standalone orchestrator service | `services/langgraph/orchestrator.py` | `StateGraph` + `MemorySaver` (`:39-40`) | Prototype ingest→analyze→compliance→risk→report | Its own FastAPI app | None; no compose file runs it (header `:3-18`) | In-memory | No | Image build + scan in CI only | — | No | **EXPERIMENTAL** |
| 7 | `LangChainVectorService` | `services/langchain_vector_service.py:14-362` | `langchain_openai.OpenAIEmbeddings`, `langchain_qdrant.QdrantVectorStore` | Qdrant dual-write of document chunks; document vector search | `DatabaseService._create_and_store_embeddings` (`database_service.py:475-499`), `GET /documents/vector-search` (`routers/documents.py:511-639`), `storage_sync._resync_document_vectors`, `document_service._delete_qdrant_vectors` | Ingestion worker/request | Qdrant collection (payload nested under `metadata.`) | Yes | 3 mock tests + env-gated live test | `qdrant_enabled` / `vector_dual_write_enabled` | Yes | **ACTIVE** |
| 8 | Contract categorizer LLM | `services/contract_categorizer.py:130-230` (`LLMService`) | `langchain_openai.ChatOpenAI`, `ChatPromptTemplate`, `prompt \| llm` chain | Classify contract excerpts into category keys | `ContractIngestor._categorize_text` (`contracts_ingest.py:1883-1894`) | Contract ingest (worker) | Contract document fields | Yes, when `OPENAI_API_KEY` is set | No direct test | none | Yes | **ACTIVE** |
| 9 | Text splitter | `services/text_processing_service.py:34-52` | `langchain_text_splitters.RecursiveCharacterTextSplitter` | Chunking for the embeddings pipeline; custom fallback on ImportError | `DatabaseService._create_and_store_embeddings` (`database_service.py:394-397`) | Ingestion | — | Yes | Yes (metadata tests) | none | Yes | **ACTIVE** |
| 10 | `LangGraphRun` model | `models/langgraph_run.py` | — | — | none | — | — | No references | — | — | — | **DEAD CODE** |
| 11 | `ai_workflows/langgraph.py` shim | `ai_workflows/langgraph.py` | — | `__path__` shim shadowed by the `ai_workflows/langgraph/` package (Python imports the package; verified in venv) | — | — | — | Never imported | — | — | — | **DEAD CODE** |
| 12 | `LLMConfigService` / `LangGraphLLMConfig` | `services/llm_config_service.py:247-312`, `models/ai_models.py:143` | name only | DB-stored model/prompt config for the "langgraph" pipeline | `/ai-assistant` config routes (superadmin) | Request | Mongo | Yes | partial | — | — | **PARTIALLY ACTIVE** |
| 13 | Letter-drafting outbox / reconciler | `service.py:2469, 2556` (writes), `repository.py:246` (`reconciliation_candidates`) | — | Durable follow-up effects | none consume them | — | Mongo `letter_draft_outbox` | Write-only | — | — | — | **DEAD CODE** (write-only) |
| 14 | Arbitration acceptance evidence | `acceptance.py:17-128`; migration `v20260723_0001:36,51` | — | Receipt chain for primary cutover | `POST …/acceptance-receipts` | Request | `arbitration_acceptance_evidence`/`_signoffs` | Route live, but **nothing writes the evidence collections** | Unit (forgery) | — | No | **UNREACHABLE** (cannot be satisfied) |

## Runtime reachability, per workflow

### Arbitration LangGraph (item 1): unreachable by default

**Trigger chain (code-read):**

1. `ArbitrationCaseWorkspacePage.tsx` (create `:645`, resume `:669/675/681/714`, approve `:697`, cancel `:1269`)
2. → `client/src/services/arbitration-drafting-api.ts:317-381`
3. → `routers/arbitration_drafting.py:197-338`. Every route runs `_load_case_and_authorize`: a `build_scope_query` lookup, then `authorize_document` with `dms.arbitration.{generate,view,edit,approve,export,admin}`.
4. → `ArbitrationWorkflowService`
5. → `DraftEngineSelector` (`engines/policy.py:107-167`)
6. → `LangGraphArbitrationEngine._checkpoint`
7. → `asyncio.to_thread(graph.invoke, …)` (`langgraph_engine.py:746, 761, 764`)
8. → node commands, which call back onto the event loop with `run_coroutine_threadsafe(...).result()` and **no timeout** (`graph_commands.py:100-104`)
9. → Mongo records, then `MongoDBSaver`.

| Question | Answer |
|---|---|
| Who calls it? | The HTTP request task. There is no queue or worker. The 202 on create is nominal: analysis, generation and validation all run inside the request. |
| Real user reachable? | Only when an operator sets canary or primary. |
| Role | Case scope plus `dms.arbitration.generate` to create; `…approve`/`…export` per gate; `…admin` + step-up for force-v2, acceptance and checkpoints. |
| After process restart | The checkpoint survives in Mongo. There is **no startup recovery loop or sweeper** (nothing in `main.py`). A run that died mid-node is recovered only when the same node is re-executed after its 120 s effect lease expires, and nothing re-drives it (D3/H3). |
| After worker crash | There is no worker; see above. |
| After LLM failure | Drafting is deterministic unless `ARBITRATION_DRAFT_MODE=llm`. In LLM mode a failure falls back to the deterministic draft with a warning (`llm_generator.py:82-89`). |
| Can it resume? | Yes, at the eight gates. It cannot resume from a failed node (H3). |
| Duplicate execution? | Guarded: unique `effect_key` with leases (`workflow_repository.claim_effect:130-233`), a run execution lease, compare-and-set on `state_version`, and a uuid5 run id from the Idempotency-Key. |
| Two workers on one graph? | Prevented by the effect key + run lease; the loser gets 409. |
| Durably stored? | Yes (Mongo). A checkpoint-sync failure is swallowed into `checkpoint_sync_status=pending` with no re-sync (H3). |

### Letter drafting v3 (item 3): unreachable by default

**Trigger chain (code-read):**

1. `LetterDraftPage.tsx:383` / `LetterStrategicPlanPage.tsx:470`
2. → `useLetterDrafting.run` (`hooks/useLetterDrafting.ts:75`, with a **fresh random Idempotency-Key per click**)
3. → `POST /api/letters/{id}/drafting/runs` (`routers/letter_drafting.py:161`)
4. → `DraftEngineSelector`
5. → `LangGraphDraftingEngine.create` (`langgraph_engine.py:232`: authorize, insert a queued run, `DraftingQueue.enqueue`)
6. → Redis db 1 `letter_drafting_queue` → `BRPOPLPUSH` → `contract-worker`
7. → `process_drafting_job` (`:818-900`)
8. → v2 `DraftRunService.create_run(stop_after_generation=True)` (LLM)
9. → `Command(resume=…, update=…)` into the graph
10. → Mongo.

The client polls `/state` once a second for 30 attempts, then stops.

| Question | Answer |
|---|---|
| After worker crash | The Redis heartbeat/visibility timeout re-queues the job, up to 3 retries, then dead-letters it. **Mongo is never told.** `process_drafting_job` has no `except`, so the run stays `running` with a lease owner forever (B6). |
| After LLM failure | `LLMGenerator.generate` swallows it and returns fallback prose, which is stored as a completed draft (B3). |
| Can it resume? | Only at `user_direction_gate` / `strategy_gate`. A retry after evidence collection writes `running/poll` while the graph is paused, so resume returns 409 (H5). |
| Duplicate execution? | Idempotency index on (letter_id, idempotency_key). **But** the worker claim compare-and-set has no status predicate, so a redelivered job re-claims a terminal run (`repository.py:97-103`). A double-click creates two runs because each click sends a new key. |
| Cancel | Sets `cancel_requested`, which is checked only before claim. The graph's `cancellation_requested` is hard-coded False (`:878`), so a cancel during a run is lost. |

### Legacy `LetterDraftGraph` (item 5): API live

`POST /api/ai-assistant/langgraph/*` → `_authorize_ai_action` (authorizes the **payload's claimed** org/project) → `AIService.generate_draft_with_langgraph` (`ai_service.py:267`) → `LetterDraftGraph.run` → `letter_service.get_letter(request.letter_id)` **unscoped** (`letter_pipeline.py:239`) → retrieval, then LLM plan/draft/review → `record_langgraph_result` → `db.letters.update_one({"_id": …})` **unscoped** (`letter_service.py:1265-1366`). This is the cross-tenant write in B1.

### LangChain pieces (items 7-9): active

- **Document ingestion.** `DatabaseService._create_and_store_embeddings` (a) embeds each chunk through **LlamaIndex** for Mongo `document_vectors` (`index_chunks(persist=False)`, `llamaindex_service.py:148-232`) and (b) embeds the **same chunks again** through LangChain `OpenAIEmbeddings` for Qdrant (`replace_document`). That is two embedding calls per chunk.
- **Qdrant failure handling.** A Qdrant failure returns 0 and yields `mismatch` status. An init failure yields `mongo_only`, is memoized for the process lifetime, and raises no alarm (M2).
- **Contract ingestion** calls `ChatOpenAI` through a LangChain chain. Raw contract text is inserted into a `ChatPromptTemplate`, so any `{`/`}` in the contract makes template formatting fail. The error is swallowed and returns `[]` (M3, code-read).

## LangGraph state models (summary; full graphs in the graphs doc)

| | Arbitration | Letter v3 | Orchestrator |
|---|---|---|---|
| State | `ArbitrationGraphState` `TypedDict(total=False)` (`:46-140`): ids (`run_id`, `thread_id`, `case_id`, `draft_id`, `organization_id`, `project_id`), ~60 artifact id/hash pairs, ~25 flags, `state_version`, `remediation_cycle`, `retry_budget_remaining` (never decremented) | `DraftGraphState` (`:49-70`): ids, flags, statuses only | dict, in memory |
| Checkpoint validation | `validate_checkpoint_state` (`:180-212`): no raw content, 1 KB per string, 256 KB total | none (`DRAFT_ENGINE_MAX_CHECKPOINT_BYTES` not enforced) | — |
| Nodes | 2 intake + 5 analysis (fan-out) + merge + plan + generate + 8 validate (fan-out) + merge + remediate + approve + export + 8 gates + complete/cancelled | 10 linear nodes, all deterministic markers | ingest/analyze/compliance/risk/report |
| Conditional edges | `continue_or_cancel`, `fanout_or_cancel`, `validation_route` (remediate loop bounded by `MAX_REMEDIATION_CYCLES=1`) | none | UNVERIFIED |
| Human gates | 8 `interrupt()` gates (`_gate`, `:215-224`) | 2 (`user_direction_gate` conditional, `strategy_gate` always) | none |
| LLM calls inside graph | Only `generate_draft` in `ARBITRATION_DRAFT_MODE=llm` | **none**; LLM runs outside the graph in v2 | UNVERIFIED |
| Tool calls | none (no `ToolNode`, `bind_tools`, `Send`) | none | none |
| Checkpointer | `MongoDBSaver`, sync pymongo, **new MongoClient per checkpoint op** | `MongoDBSaver`, new client per job; DB name taken from the `DATABASE_URL` path, not `MONGODB_DATABASE` (M5) | `MemorySaver` |
| thread_id | `arbitration:{run_id}` (no tenant; bound through run→case) | `uuid4()` at create | — |
| Cancel | fenced `cancel_if_idle` + `cancellation_requested` routed to `cancelled` | lost during a run (H5) | — |
| Timeout | none on `run_coroutine_threadsafe().result()` | none | — |
| Recovery | lazy lease reclaim; TTL-expired checkpoint rebuilt from ledger (`_recovery_state`, `:657-689`) | none (no reconciler) | none |

Arbitration's conceptual phases, as implemented:

| Phase | Implemented as |
|---|---|
| analysis | ✅ five deterministic analysis branches |
| evidence | ✅ matrix rows + snapshots |
| clause interpretation | ✅ `analyze_clause_jurisdiction_notice` |
| chronology | ✅ `analyze_chronology` |
| drafting | ✅ `generate_draft`, deterministic by default |
| legal consistency | ✅ eight deterministic validators + remediation |
| human review | ✅ eight gates, **but skippable (B4)** |
| export | ✅ node exists, **but always 403s (B5, code-read)** |

## Agent reality

"Agents" in this repo are **ordinary service classes or deterministic functions**. There is no autonomous tool-using agent anywhere: zero hits for `create_react_agent`, `create_tool_calling_agent`, `AgentExecutor`, `ToolNode`, `tools_condition` or `bind_tools` in `backend/` or `services/`.

| Agent | Implemented? | Framework | Reachable? | Inputs → outputs | Tools | State | Human approval | Failure handling | Prod-ready? |
|---|---|---|---|---|---|---|---|---|---|
| Evidence mapping (arbitration) | Yes | LangGraph node over deterministic domain code | Via v2 (live) or LG (gated) | case docs → matrix rows | none | Mongo | matrix_review gate | effect lease | v2 yes; LG no (B4/B5) |
| Clause interpretation / jurisdiction / notice | Yes | deterministic branch | same | clauses → matrix | none | Mongo | matrix gate | lease | same |
| Chronology | Yes | deterministic branch | same | events → chronology rows | none | Mongo | matrix gate | lease | same |
| Drafting (arbitration) | Yes | `ArbitrationDraftingService` + optional `LLMArbitrationAgent` (`agents/llm.py:138`, subclass of the deterministic agent) | same | plan → draft | none | Mongo | plan/draft gates | LLM → deterministic fallback with warning | deterministic yes |
| Legal consistency | Yes | eight deterministic validators | same | draft → findings | none | Mongo | legal_review gate | remediation (1 cycle) | yes |
| Human review | Yes | `interrupt` gates / v2 status gates | same | — | — | Mongo | — | — | LG path defective (B4) |
| Export | Yes | `queue_filing_bundle_export` | v2 live; LG node fails (B5) | approved draft → bundle | none | Mongo/S3 | export gate | permanent-fail on 403 | LG no |
| Letter strategy / draft / review | Yes | prompt wrappers over `LLMGenerator` (raw OpenAI) | v2 live | letter + evidence → plan/draft | none | Mongo | 3-step approval chain | **silent fallback prose (B3)** | no (B3) |
| Letter section editor | Yes | prompt wrapper | live route `revise-sections` | section → section | none | Mongo | — | **always no-ops (H2)** | no |
| Contract clause "agent" (`services/contract_clause/agent.py`) | Yes | plain service (clause extraction + Qdrant `contract_clauses`) | ingestion | contract → clause records | none | Mongo + Qdrant | — | — | out of scope of LG |
| Contract appraisal | Yes | `AppraisalGenerator` prompt wrapper | report routes | contract → report | none | Mongo | — | UNVERIFIED | not assessed |
| Variation / EOT analysis | Not as agents | Services exist (hindrance/key-dates), not LLM agents | — | — | — | — | — | — | n/a |
| Categorizer | Yes | LangChain `prompt \| ChatOpenAI` | ingestion | excerpt → keys | none | — | — | swallow → `[]` | fragile (M3) |

## LLM / tool architecture

- **Providers.** OpenAI only in the live paths:
  - `AsyncOpenAI` in `retrieval/generator.py`
  - `ChatOpenAI` in the categorizer
  - `OpenAIEmbeddings` in the LangChain Qdrant store
  - LlamaIndex OpenAI embeddings
  - PydanticAI, which sets `os.environ["OPENAI_API_KEY"]` (`pydantic_ai_service.py:58`)
- **`LANGGRAPH_PLAN_MODEL` defaults to `grok-4-1-fast`** (`config.py:257`), a non-OpenAI model name, in config that feeds the legacy pipeline. How it is routed is UNVERIFIED.
- **Model selection.**
  - Environment: `OPENAI_MODEL`, `LANGGRAPH_*_MODEL`.
  - Hard-coded class defaults: `DraftGenerator`/`ScopedSectionEditor` `"gpt-4o"`.
  - DB-stored for the legacy pipeline: `LLMConfigService`.
  - Nothing is tenant- or project-controlled.
- **Temperature** is hard-coded at 0.2 in `LLMGenerator`.
- **Structured output.** There is no `with_structured_output` or tool calling anywhere. Output is parsed with `json.loads` / `parse_draft_artifact`, arbitration uses an `LLMOutputError` plus fabricated-citation rejection, and there is no JSON repair.
- **Silent fallbacks.** `LLMGenerator.generate` returns canned prose on any exception or when no API key is set (`generator.py:37-60`). `strict=True`, which CLAUDE.md mandates, **does not exist** in the signature (B3).
- **Prompt-injection defences.**
  - The section-edit prompt marks the section as untrusted.
  - Arbitration has an injection guard (per memory; not re-verified here).
  - The categorizer interpolates raw contract text straight into the template.
- **Token budgets.** Fixed `max_tokens` per call and character truncation in the categorizer. No tokenizer-based budgeting was found (UNVERIFIED across all paths).
- **Cost/token tracking.** No usage capture from LLM responses was found. Metering records business events (`UsageEventType`), not tokens.

## RBAC / tenancy (summary; details in blockers)

| Area | Assessment |
|---|---|
| Arbitration routes | **Canonical and sound.** Case lookup via `build_scope_query`, `authorize_document`, runs bound to their case, checkpoint route bound to the case with ADMIN + step-up. Covered by `test_arbitration_http_isolation.py` (10 tests), which is skipped when run after other files (order-dependent). |
| Letter-drafting v2/v3 run routes | Authorize the letter, fetch runs by (letter_id, run_id). The v3 worker re-resolves the stored principal. |
| Letter-drafting ops checkpoints route | **Not scoped by run** (M1). |
| `/ai-assistant/langgraph/*` | **Cross-tenant read and write** (B1). |
| `/v1/retrieval/*` | **`filters.metadata` overrides `org_id`/`project_id`** (B2). |
| `/deep-planning/generate-draft` | **Loads a foreign letter into the prompt** (H1). |
| FalkorDB letter nodes | Keyed by `normCode` only; accepted residual, pinned by tests. |

## Test coverage (summary)

- **Arbitration LangGraph** is the best-tested component: compile, every gate, fan-out replay, idempotent create, effect leases, cancel fencing, TTL reconstruction and policy selection, all on fakes plus `InMemorySaver`.
- **The real-Mongo drills** (process kill, TTL, sustained load) are env-gated and **never run in CI**. CI runs a standalone `mongo:8.0` with no replica set, Qdrant, Falkor or Redis, and none of the `RUN_*` flags.
- **Letter v3** has no queue, kill, cancel, retry, LLM-failure, malformed-output or concurrency tests.
- **No drafting workflow** runs against a real LLM or a real checkpointer in CI.
- **The tests that should catch B1, B2, H1, H2 and M1 do not exist.** H2 is hidden because the test replaces the editor with a fake.

The full coverage matrix is in [blockers §Test gaps](langchain-langgraph-blockers.md#test-coverage-matrix).

## What is duplicated

| Workflow | Implementations | Live |
|---|---|---|
| Letter drafting | v2 `DraftRunService`; v3 LangGraph (a shell that calls v2); legacy `LetterDraftGraph` (its own plan/draft/review LLM calls) | v2 |
| Arbitration | v2 custom engine (`workflow_service.approve_gate/resume`); LangGraph node commands (`graph_commands._build_plan/_generate_draft/_merge_validation/_remediate`), which mirror `workflow_service.py:910-1041, 318-568` | v2 |
| Qdrant writes | LangChain `QdrantVectorStore` (nested `metadata.` payload) and the native `retrieval/vector_client` (flat payload) | both |
| Embeddings | LlamaIndex (Mongo) and LangChain `OpenAIEmbeddings` (Qdrant) on the same chunks | both |
| LLM clients | raw `AsyncOpenAI`, LangChain `ChatOpenAI`, PydanticAI, LlamaIndex | all |

## Fit with the intended architecture

- **Consistent with it:**
  - Qdrant as the only vector store.
  - Clause-first retrieval.
  - Arbitration LangGraph kept non-primary behind an acceptance chain.
  - Mongo as the durable ledger.
- **Inconsistent with it:**
  - Letter v3 adds LangGraph without moving any logic into the graph. It inherits v2's failure modes and adds queue/checkpoint failure modes of its own.
  - The acceptance chain CLAUDE.md requires for arbitration **cannot be satisfied**: nothing writes the evidence collections, and the export node fails (B5).
  - The `strict=True` rule for drafting LLM calls cannot be followed with the current `LLMGenerator`.
