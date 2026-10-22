# LangChain / LangGraph audit — findings and blockers

Read-only audit of HEAD `a179ca9`, working tree dirty, dated 2026-09-27. See [current state](langchain-langgraph-current-state.md) for context.

**Evidence labels**
- **Verified**: I read the code and confirmed the mechanism line by line, or it was executed.
- **Reproduced**: executed.
- **Code-read**: the mechanism was traced by an investigator and spot-checked, but not executed.
- **UNVERIFIED**: plausible, not confirmed.

No finding here was probed against production.

| Severity | Count |
|---|---|
| BLOCKER | 6 |
| HIGH | 8 |
| MEDIUM | 14 |
| LOW | 5 |

Three of the blockers (B1–B3) affect code that is **live today** under the default configuration. The other three (B4–B6) block a LangGraph cutover but sit behind flags that are off by default.

---

## BLOCKERS

### B1 — Cross-tenant letter read and write via `/ai-assistant/langgraph/*`
- **Component:** legacy `LetterDraftGraph` pipeline, `AIService`, `LetterService`.
- **Files:**
  - `routers/ai_assistant.py:73-110`
  - `routers/ai_assistant.py:221-290`
  - `ai_workflows/langgraph/letter_pipeline.py:237-263`
  - `services/ai_service.py:267-279` and `:346-371`
  - `services/letter_service.py:1258-1366`
- **Evidence (Verified by code read):**
  - `_authorize_ai_action` authorizes the org and project the caller *claims* in the payload. It falls back to the caller's own org and first project. `letter_id` is used for audit only.
  - `load_state` then calls `letter_service.get_letter(request.letter_id)` with no scope.
  - `collect_context` derives `letter_org` and `letter_project` from that foreign letter and uses them for retrieval.
  - `record_langgraph_result` writes `draft_output`, `draft_versions`, `strategy_plan` and related fields with `update_one({"_id": letter_oid})`, again with no scope.
- **Runtime consequence:** a user holding `DRAFTING_REQUEST_CREATE` in org A can send org B's letter ObjectId. The response returns that letter's subject and status in the trace, and the call overwrites B's draft and strategy fields. Those fields are the plan inputs v2 reads (see M7).
- **Reproduce:** seed two orgs, each with an active subscription row. Log in as an A user and `POST /api/ai-assistant/langgraph/strategy-plan {letter_id: <B letter>}`. Then read `letters.strategy_plan` for B.
- **Why it matters:** a tenant-isolation breach with a write on a live route. The mitigations only partly contain it: documents, conversation, related letters and contract evidence are re-authorized against the actor.
- **Remediation:**
  - Load the letter through `build_scope_query` for the actor, or `authorize_document` on the loaded letter, before any node runs.
  - Make `record_langgraph_result` take a scope filter.
  - Better still, retire the route (see target architecture).
  - Add a red test with a foreign `letter_id`.
- **Changes architecture?** No. **Complexity:** SMALL.

### B2 — Retrieval `filters.metadata` overrides the authorized `org_id` / `project_id`
- **Component:** `RetrievalService.search`, the Qdrant path used by `/v1/retrieval/search`, `/rag` and `/contract-qa`.
- **Files:**
  - `retrieval/service.py:129-137`
  - `retrieval/models.py:25-36`
  - `routers/retrieval_engine.py:95-121`
  - Mongo fallback: `retrieval/service.py:539-542`
- **Evidence (Verified by code read):**
  - `SearchFilters.metadata: Dict[str, Any]` is free-form, with `extra="allow"`.
  - The search filter is built as `{"org_id": filters.org_id, "project_id": …, **(filters.metadata or {})}`. The spread comes *after* the authorized keys, so it overwrites them.
  - `_authorize_scope` checks only `filters.org_id` and `filters.project_id`.
  - Results are post-filtered only for publication-blocked documents, never for org.
- **Runtime consequence:** a request with `filters={org_id:A, project_id:PA, metadata:{org_id:B, project_id:PB}}` passes authorization on A and searches B's vectors. The chunk text comes back, and in `/rag` it feeds an LLM answer.
- **Reproduce:** a unit test against `RetrievalService.search` with a recording vector client; assert the filter passed down has `org_id == B`.
- **Why it matters:** cross-tenant read of contract text. The vector client's own guard (`vector_client.py:288-296`) only requires *some* `org_id`, so it does not help.
- **Remediation:**
  - Build the filter as `{**metadata_without_reserved_keys, "org_id": …, "project_id": …}`. The authorized keys must be written last.
  - Reject any reserved key that appears in `metadata`.
  - Apply the same fix to `_search_contract_mongo`.
  - Treat an empty-list `project_id` as deny (`vector_client.py:469-472`).
- **Changes architecture?** No. **Complexity:** SMALL.

### B3 — LLM failure is stored as a completed draft (silent fallback), and the `strict=True` contract does not exist
- **Component:** `retrieval/generator.py::LLMGenerator`, used by every live letter-drafting LLM call.
- **Files:**
  - `retrieval/generator.py:37-60`
  - `services/letter_drafting/generator.py:54-60` and `:86-97`
  - `service.py:509` (the `llm_degraded` flag)
  - `ai_workflows/langgraph/letter_pipeline.py:1207, 1496, 1599`
- **Evidence (Verified):**
  - `generate(prompt, max_tokens, model)` catches every exception, and also covers the no-API-key case, by returning the fixed prose "Answer unavailable: the language model could not generate…".
  - `DraftGenerator.generate` only records `draft_llm:` when `generate` *raises*, which never happens. The fallback prose is therefore parsed by `parse_draft_artifact` into the draft, and `llm_degraded` stays false.
  - CLAUDE.md requires new drafting call sites to pass `strict=True`, but the signature has no such parameter (confirmed in the venv).
- **Runtime consequence:** during an OpenAI outage, a 401 or a rate limit, v2 runs complete with "Answer unavailable…" as the letter body and strategy plan. They look like successes and can go into the approval chain. v3 inherits this because it calls v2.
- **Reproduce:** unset `OPENAI_API_KEY` (or monkeypatch the client to raise), `POST /api/letters/{id}/drafting/runs`, then inspect `draft_artifact.draft_letter`.
- **Why it matters:** a silent failure on the core product output. This is the house anti-pattern CLAUDE.md warns about.
- **Remediation:**
  - Add `strict: bool = False` to `LLMGenerator.generate`. When strict, raise a typed `LLMUnavailableError` and never return prose.
  - Make every drafting call site strict, keeping its deterministic fallback and setting `llm_degraded` and a `degraded` run marker.
  - Add a metric for fallback activations.
  - Add tests covering outage, no key and an empty completion.
- **Changes architecture?** No, this is the LLM abstraction contract. **Complexity:** SMALL–MEDIUM.

### B4 — Arbitration LangGraph human gate can be skipped on resume
- **Component:** the `_gate` node factory. Blocks LangGraph cutover.
- **Files:**
  - `services/arbitration_drafting/langgraph_engine.py:215-224`
  - `workflow_service.py:820-855` (rejections)
  - `workflow_service.py:902` (re-approval resume)
  - `langgraph_engine.py:292-295, 706-734`
- **Evidence (Reproduced by an investigator's probe with a real `StateGraph` + `InMemorySaver`; mechanism Verified by code read):**
  - `_gate` calls `interrupt()` when its flag is false and then returns "passed". On resume, `interrupt()` returns the resume value and the node proceeds **without re-checking the flag**.
  - A draft rejection moves the run back to `legal_review_gate`, but the checkpoint stays paused at `draft_approval_gate`.
  - The next legal-review approval resumes the checkpoint. The graph walks past the draft gate with `draft_approved=False` and lands on `export_authorization_gate`.
  - `commit_draft_approval` runs as a marker only, because no node execution is needed there, so `drafting.approve` is never called.
  - The following approval then passes export with `export_authorized=False`.
  - Probe output: `('export_authorization_gate',)` after the re-approval, then `complete/completed`.
- **Runtime consequence:** a draft can reach the export node without its approval ever being committed.
- **Reproduce:** use the probe pattern. Build the graph, drive it to `draft_approval_gate`, reject, re-approve legal review, and inspect `graph.get_state(...).next`.
- **Why it matters:** human-in-the-loop is the core legal control in arbitration pleadings.
- **Remediation:**
  - After `interrupt()` returns, re-check `required_flag` and loop: interrupt again when it is still false.
  - Alternatively, make a rejection rewind the checkpoint (`update_state(..., as_node=<gate>)`) so graph position and run position cannot diverge.
  - Add tests for every reject → re-approve path.
- **Changes architecture?** No. **Complexity:** SMALL–MEDIUM.

### B5 — Arbitration LangGraph `create_filing_export` node always fails, so no LangGraph run can complete and the acceptance chain cannot be satisfied
- **Component:** `graph_commands._create_filing_export`, the acceptance evidence.
- **Files:**
  - `graph_commands.py:117-119` and `:1051-1061`
  - `case_workspace.py:2012`
  - `approval_policy.py:46-58`
  - `workflow_hardening.py:114-121`
  - `acceptance.py:17-128`
  - migration `v20260723_0001:36,51`
- **Evidence (Code-read, not executed):**
  - The node passes `self._actor(run)`, a `SimpleNamespace(id, email)` with no roles.
  - `_authorize_filing_bundle` calls `resolve_gate_role("export")`, and `actor_roles` finds no roles, so it raises 403. The author-separation check would also answer 409.
  - The 403 is classified as permanent, so the effect is failed as non-retryable.
  - `accepted_graph_runs` needs LangGraph runs with `status=="completed"`.
  - Separately, nothing in the application writes `arbitration_acceptance_evidence` or `arbitration_acceptance_signoffs`.
- **Runtime consequence:** primary cutover (`ARBITRATION_ENGINE_PRODUCTION_ACCEPTED`) is unreachable by construction.
- **Reproduce:** a unit test that runs `_create_filing_export` with the real `_authorize_filing_bundle` against a fake DB.
- **Why it matters:** the documented release path for arbitration LangGraph cannot be completed.
- **Remediation:**
  - Resolve the export approver's real principal from the committed export approval receipt and authorize as that user.
  - Verify `export_approval_receipt_id` rather than only hashing it.
  - Add an evidence writer, or document an operator procedure, for acceptance rows.
- **Changes architecture?** No. **Complexity:** MEDIUM.

### B6 — Letter-drafting v3 runs can get stuck `running` forever
- **Component:** `LangGraphDraftingEngine.process_drafting_job` and the drafting queue. Blocks the v3 cutover.
- **Files:**
  - `services/letter_drafting/langgraph_engine.py:818-900`
  - `drafting_queue.py:106, 136-161, 213-233`
  - `repository.py:97-103, 246`
- **Evidence (Code-read):**
  - `process_drafting_job` has no `except`. After three Redis retries the job is dead-lettered, but Mongo `letter_draft_runs` stays `running` with `lease_owner` set.
  - Resume returns 409.
  - Cancel only sets `cancel_requested`, which no worker acts on.
  - force-v2 returns 409 because a completed effect exists.
  - `reconciliation_candidates` has no callers, and the outbox is never consumed.
- **Runtime consequence:** a permanently wedged letter draft, with no user or operator exit short of a manual database edit.
- **Reproduce:** make `commit_validation_stage` raise (for example, a v2 child run that stopped at the question gate and has no `draft_artifact`), let the worker exhaust its retries, then call resume and cancel.
- **Why it matters:** it breaks the durability promise that is the only reason to use LangGraph here.
- **Remediation:**
  - Write back a terminal `failed` status (with the error class) on the final attempt.
  - Add a reconciler for `running` runs whose lease has expired.
  - Let cancel finalize idle or failed runs.
  - Or retire v3 (see target architecture).
- **Changes architecture?** Only if v3 is retired. **Complexity:** MEDIUM.

---

## HIGH

| ID | Component | Files | Evidence | Consequence | Remediation | Arch? | Size |
|---|---|---|---|---|---|---|---|
| H1 | Deep-planning draft | `routers/deep_planning.py:843-890, 938-950, 358-384` | Authorizes the claimed scope, then `db.letters.find_one({"_id": target_letter_id})` with no scope. The foreign letter's summary and keywords go into the LLM prompt. `validate_document_ids` compares org only and ignores the project tier. (Code-read) | Cross-tenant content in the prompt and output; project-tier users can use any document in their org | Scoped load with `build_scope_query`; use `authorize_document` for documents; red tests | No | SMALL |
| H2 | Section editor | `services/letter_drafting/section_editor.py:60-72`, `retrieval/generator.py:37`, `routers/letter_drafting.py:411` | Passes `strict=True` to a `generate` that has no such parameter. The resulting `TypeError` is caught and the original text is kept. The test swaps in `_FakeEditor`, which hides this (`test_letter_drafting_frozen_sections.py:145`). (**Verified**) | Every `revise-sections` call returns unchanged text plus a warning | Fix together with B3 (adds `strict`). Add a test that uses the real editor with a stub client | No | SMALL |
| H3 | Arbitration run wedge and pending sync | `workflow_service.py:582-639, 867-902, 1097-1100, 1351`; `graph_commands.py:203-209` | `approve_gate` transitions first and calls `checkpoint_transition` without a guard. A node failure leaves the run `running` at a node that neither resume nor approve accepts, and fallback is forbidden because authoritative effects exist. A permanently failed effect answers "already active" 409 forever. `checkpoint_sync_status=pending` is never re-synced. (Code-read) | Stuck arbitration runs; the UI shows `running` although no analysis ran | Wrap and compensate the transition; add a reconciler and sweeper (for example on `contract-worker`); allow operator retry of failed effects; add a re-sync job | No | MEDIUM |
| H4 | Arbitration canary gate | `core/config.py:704-760`, `engines/policy.py:161-166` | The production config gate validates only `primary`. `DEFAULT=langgraph_v1` + `ROLLOUT_MODE=canary` + a percentage or allowlist makes LangGraph authoritative with no acceptance. (Code-read) | The B4/B5 defects can reach production users through a canary flip | Require acceptance, or an explicit `ALLOW_UNACCEPTED_CANARY` with an allowlist-only scope, in production | No | SMALL |
| H5 | Letter v3 state machine | `langgraph_engine.py:530-537, 825, 832, 866-898`; `repository.py:97-103`; `drafting_queue.py:106` | The claim compare-and-set has no status predicate, so terminal runs get re-claimed. A retry after `collect_evidence` persists `running/poll` while the graph is paused at the strategy gate, so resume answers 409. Cancel during a run is lost (`cancellation_requested` hard-coded False). A narrow resume-drop window exists. (Code-read) | Wrong status, lost cancels, unresumable runs | Add a status predicate to the claim; derive persisted status from `graph.get_state`; check cancel between stages and in the final compare-and-set | No | MEDIUM |
| H6 | Letter v3 → v2 child run | `langgraph_engine.py:583-668, 642`; `service.py:2164-2190, 739`; `repository.py:109` | The domain adapter creates a v2 child run marked `completed` with no cyclic validation or legal-risk review. `approve_stage` does not check `parent_run_id`. `runs/latest` returns the child. (Code-read) | An unreviewed draft can enter the approval chain; dashboard counts double | Mark child runs `internal`, refuse approval on them, and exclude them from `latest` and metrics | No | SMALL |
| H7 | In-request, blocking graph execution | `langgraph_engine.py:412-429, 703, 746-764, 800`; `graph_commands.py:100-104`; letter `langgraph_engine.py:164-180, 843, 866` | Arbitration runs the whole graph inside the HTTP request (the 202 is nominal). Callbacks use `run_coroutine_threadsafe().result()` with no timeout. `MongoDBSaver` performs synchronous index creation, with a new `MongoClient` per operation, inside async code. (Code-read) | Gateway timeouts kill runs mid-node (see H3); event-loop stalls; connection churn | Execute on a worker (the Redis/`contract-worker` queue already exists); share one saver and client per process; put timeouts on every bridge call | **Yes** (moves execution ownership) | MEDIUM |
| H8 | Verification gap | `C:\SaaS\.github\workflows\ci.yml:46-73`; `test_arbitration_http_isolation.py:44`; `test_arbitration_process_kill_integration.py:~95` | CI runs standalone Mongo only, with no `RUN_*` flags. The kill, TTL and load drills and every real checkpointer test are skipped. The isolation test skips depending on file order ("TestClient/app unavailable") and passes when run alone. The kill test stubs its node operation. B1, B2, H1, H2 and M1 have no tests. (Verified: the skip was observed) | Green CI proves nothing about durability or isolation | Add a replica-set Mongo service to CI and run the arbitration drills; fix the import-order skip so it fails instead of skipping; add red tests for each finding | No | MEDIUM |

## MEDIUM

| ID | Component | Files | Evidence and consequence | Remediation | Arch? | Size |
|---|---|---|---|---|---|---|
| M1 | Letter-drafting ops checkpoints | `routers/letter_drafting.py:270-283`; `langgraph_engine.py:498-522`; `scope_service.py:140` | Authorizes `drafting.workflow.checkpoints` with no org or project, then fetches by `run_id` alone. A drafting manager, or any expert allocation in *any* org, can read another org's redacted checkpoint state with a known UUID. Step-up is still required. | Bind the run to its letter and scope first, as the arbitration equivalent does | No | SMALL |
| M2 | `LangChainVectorService` | `langchain_vector_service.py:240-347`; `database_service.py:491-630`; `routers/documents.py:571-592` | (a) An upsert failure returns 0 with a warning. (b) Delete-then-add is not atomic, so a failed add loses the vectors. (c) An init failure is memoized for the whole process and the document is marked `mongo_only` without any alarm. (d) `similarity_search` swallows errors and returns `[]`. (e) Vector-search filters use flat `organization_id` keys while LangChain nests payload under `metadata.`, so the search probably matches nothing (**UNVERIFIED** against live Qdrant). Failure is closed but silent. | Replace with the native `vector_client` (see target), or raise on configured-but-failing; filter on `metadata.*`; add tests | Yes, if replaced | MEDIUM |
| M3 | Contract categorizer | `contract_categorizer.py:166-230` | Raw contract text goes into `ChatPromptTemplate.from_messages`, so any `{`/`}` breaks templating. The error is swallowed and returns `[]`. The synchronous `chain.invoke` inside `async` blocks the loop. The excerpt is not delimited as untrusted. (Code-read) | Pass the text as a template variable (or a plain message); make the call async; use JSON-schema output | No | SMALL |
| M4 | `LANGGRAPH_ENABLED` / `LANGGRAPH_API_TOKEN` | `config.py:252-267, 793-796`; `docker-compose.prod.yml:64-65`; `.env.example:131-135` | The flag gates nothing, although `.env.example` says it "activates" the pipeline. Production compose *requires* `LANGGRAPH_API_TOKEN` for a service that is not deployed. | Remove, or rename to what it actually controls | No | SMALL |
| M5 | Letter checkpoint database | `letter_drafting/langgraph_engine.py:164-180`; `core/database.py:40`; `docker-compose.prod.yml:13` | The saver takes the database name from the `DATABASE_URL` path and ignores `MONGODB_DATABASE`, so checkpoints may land in another database. **UNVERIFIED** in production. | Use the app's configured database | No | SMALL |
| M6 | Checkpoint TTL semantics | `v20260722_0001:11`; `langgraph_engine.py` (arbitration) `:657-689, 757-763` | The migration hard-codes a 30-day TTL, but the runtime `create_index` uses the configured value, so any change conflicts (**UNVERIFIED**). Awaiting runs outlive their checkpoints. TTL reconstruction drops the pending resume update. | One TTL source; reconstruct *and then* apply the resume; alert on reconstruction | No | SMALL |
| M7 | Legacy pipeline vs v2 plan | `letter_service.py:1270-1366`; `service.py:3623` | `/ai-assistant/langgraph/strategy-plan` writes `letters.strategy_plan`, which v2 reads. Approval is a timestamp rather than a content hash. Versioning is read-modify-write, so versions can race. | Retire the legacy routes; hash approved plans | Yes (retire) | SMALL |
| M8 | Dead or write-only code | `models/langgraph_run.py`; `ai_workflows/langgraph.py`; `client/src/hooks/useLanggraphStrategyPlan.ts`; the `useLanggraphDraft.runDraft` path; `letter_draft_outbox`; `reconciliation_candidates`; `retry_budget_remaining`; `services/langgraph/` (CI builds and scans an undeployed image) | Zero-reference or never-consumed code that suggests features which do not exist | Delete after a zero-reference proof | No | SMALL |
| M9 | Arbitration duplicate domain transitions | `graph_commands.py` `_build_plan`, `_generate_draft`, `_merge_validation`, `_remediate` vs `workflow_service.py:318-568, 910-1041` | Two implementations of the same transitions, so behaviour will drift between v2 and LangGraph | Extract shared domain commands that both engines call | Yes (small) | MEDIUM |
| M10 | Client drafting UX | `client/src/hooks/useLetterDrafting.ts:57-68, 85` | Polling stops after 30 s and leaves a `running` run displayed. A fresh `Idempotency-Key` per click means a double-click creates two runs. | Key per intent (letter + mode + payload hash); back-off polling with no cap, or server-sent events | No | SMALL |
| M11 | Import cycle | `ai_workflows/langgraph/letter_pipeline.py` ↔ `services/letter_service.py` (Graphify) | Circular import between the pipeline and the letter service | Resolved by retiring or moving the pipeline | No | SMALL |
| M12 | v3 evidence drift | `langgraph_engine.py:551-574, 649`; `service.py:228` | The sealed evidence snapshot is not the evidence actually used for generation, and the incoming-letter analyzer runs twice | Generate from the snapshot | No | MEDIUM |
| M13 | Letter strategy context write | `routers/letters.py:2438` | The route gates on letter *read*, then calls `update_letter` | Require the edit permission | No | SMALL |
| M14 | Framework sprawl and double embedding | `database_service.py:475-554`; `llamaindex_service.py:148-232`; `pydantic_ai_service.py:41-165` | LangChain, LangGraph, LlamaIndex, PydanticAI and the raw OpenAI SDK all ship in one image. Every document chunk is embedded twice: LlamaIndex for Mongo, then LangChain for Qdrant. PydanticAI mutates `os.environ`. | Consolidate (see target architecture) | **Yes** | MEDIUM |

## LOW

| ID | Finding | Files | Remediation |
|---|---|---|---|
| L1 | Root `requirements.txt` is orphaned and has drifted from the backend pins (`langchain-openai` 1.0.1 vs 1.1.14, `langchain-qdrant` 1.1.0 vs 1.0.0, plus five extra LangChain packages) | `requirements.txt:103-115` | Delete it or generate it from the backend manifest |
| L2 | Docs over-claim: the outbox and reconciliation are marked "Complete" | `docs/langgraph-migration/implementation-status.md:10-11` | Correct the doc |
| L3 | Hand-rolled `"superadmin" in roles` checks on the ai-assistant config routes and the retrieval ops routes (they fail closed) | `routers/ai_assistant.py:45-52`; `routers/retrieval_engine.py:227, 248, 262` | Use `require_permission` |
| L4 | Arbitration create after a crash mid-graph: a partial checkpoint makes invoke get skipped, so the run never continues from create (**UNVERIFIED** end to end) | `langgraph_engine.py:508-511, 739-745` | Add a test; continue from the checkpoint |
| L5 | `ai_assistant` catches only `HTTPException` before `except Exception`, so domain 403s may surface as 500s (**UNVERIFIED**) | `routers/ai_assistant.py:291-295` | `except (BaseDomainError, HTTPException): raise` |

---

## Test coverage matrix

Legend:
- **Y**: unit or mock coverage exists.
- **I**: only in an env-gated real integration test, which does not run in CI.
- **P**: partial.
- **—**: no coverage.

| Dimension | Arbitration LG | Letter v3 LG | Letter v2 | ai_assistant pipeline | Retrieval | LangChain vector | Deep planning |
|---|---|---|---|---|---|---|---|
| Graph compilation | Y | Y | n/a | — | n/a | n/a | n/a |
| Individual nodes | Y | Y | Y | Y | n/a | n/a | — |
| State transitions | Y | P | Y | — | n/a | n/a | — |
| Conditional transitions | Y | Y | P | — | n/a | n/a | — |
| Checkpoint/resume | Y + I | Y | Y | n/a | n/a | n/a | n/a |
| Failure/retry | Y | — | — | — | Y | — | — |
| Duplicate execution | Y | P | Y | — | — | — | — |
| Worker restart/kill | Y (mock) + I (stubbed node) | — | n/a | n/a | n/a | n/a | n/a |
| Cancellation | Y + I | — | — | n/a | n/a | n/a | n/a |
| Human interrupt/resume | Y (not reject→re-approve, B4) | Y | Y | n/a | n/a | n/a | n/a |
| RBAC | Y (HTTP; order-dependent skip) | Y | Y | P | Y | — | — |
| Tenant isolation | Y | Y | Y | P (foreign `letter_id`: —) | P (metadata override: —) | — | — |
| LLM failure | Y | — | P (B3 not caught) | P | Y | n/a | Y |
| Tool failure | P | — | — | — | Y | — | P |
| Malformed structured output | Y | — | Y | — | Y | n/a | — |
| Timeouts | — | — | — | — | Y (reranker) | — | — |
| Large documents | — | — | — | — | — | — | Y |
| Parallel workflows | Y + I | — | — | — | — | — | — |

Real LLM coverage: only `integration/test_external_services_integration.py`, gated on `RUN_EXTERNAL_INTEGRATION_TESTS`. It exercises plumbing, not any workflow.

Test runs made during this audit, with `backend/.venv`:
- Arbitration: 6 files, **147 passed / 13 skipped**.
- Letter LangGraph: 3 files, **14 passed / 0 skipped**.
- The full suite was **not** run.
