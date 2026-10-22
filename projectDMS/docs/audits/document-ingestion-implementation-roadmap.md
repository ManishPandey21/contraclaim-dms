# Document Ingestion — Implementation Roadmap (reconciled)

Planning only. No code has been written. This plan is derived from
[document-ingestion-current-release-gap-register.md](document-ingestion-current-release-gap-register.md)
(baseline `origin/release/contraclaim-rc1` @ `5a8cca2`). IDs refer to
[document-ingestion-reconciled-findings.md](document-ingestion-reconciled-findings.md).

## Ordering principles

1. **Data-destroying defects first; unreachable-but-intact data second.**
   - DI-B3 silently deletes reference links and empties metadata on documents that reach `completed`.
   - DI-B1 leaves vectors intact but unreachable. That fails closed and can be repaired by backfill.
2. **Integrate fixes that already exist before writing new ones.**
   - DI-B2, H14, H17, M37, L11, L16 and the contract half of DI-B4 are fixed on RECON and PR #34.
   - Re-implementing them against REL would fork the lineage a third time.
3. **Do not flip correspondence to the page engine (DI-H1) until retry and status durability are in place.**
   - The engine is what fixes DI-B4 for unified_v1.
   - The job layer around it still has no attempts cap, no lease token and no unique active job.
4. **Canonical text and spans come before structured facts with provenance.**
   - A fact's provenance must point at immutable page text.
   - Today it can only point at an LLM re-typing (DI-H4).
5. **Do not generalise across pipelines.** Each phase names the pipelines it touches, and its exit test runs per pipeline.

This order differs from the proposed A–G sequence in three places:

- **Qdrant schema is second, not third.** It is the remaining BLOCKER.
- **Tenancy gets its own early phase.** DI-H7 and DI-H20 are cross-tenant defects.
- **Canonical text moves after retry durability.** It depends on that phase (principle 3).

---

## Phase 0 — Integrate the waiting fixes (release engineering; runs in parallel with A)

**Scope.** Bring RECON `4fcd012` and PR #34 `3ca0149` into `release/contraclaim-rc1`.

**Closes.** DI-B2, DI-H14, DI-H17, DI-M37, DI-L11, DI-L16, DI-B4 (contracts), DI-H22 (contracts) and DI-H10 (contracts).

**Gates before merge:**
1. Run the five skipped CM runtime integration suites against a disposable **replica-set** Mongo. They are the only runtime proof of PENDING → CURRENT, and they were skipped in this reconciliation.
2. Get CI green. PR #34's CI is billing-blocked, so run the suite locally with the backend venv as the interim gate and record it as such.
3. Regenerate and diff the route inventory, because CM adds 409 paths on the manual ingestion and `/process` routes.
4. Push to both remotes. Production tracks whatever branch the server's own checkout shows, so read it; do not assume.

**Tracked follow-ups (do not block the merge):**
- Qdrant read-back count before CURRENT.
- Query-time revision filter.
- Delete or guard the uncalled `PostPromotionSequencer.mark_projection_current`.
- DI-N4: add `classification_revision` to the confirm update filter. This is a small change and could ride along.
- DI-N8: one-off `/contracts/{id}/reindex` for contracts whose `contract_ocr_pages` carry `text_layer` rows with empty text from a REL-era retry. Produce the list with a read-only query first.

**Exit criteria.**
- REL contains `3ca0149`, with the integration suites green on a replica set.
- A staging instrument promotes to CURRENT, and evidence search answers 409 while PENDING.

Complexity: M (merge + evidence), no new design.

---

## Phase A — Stop silent correspondence metadata corruption (S, first)

**Pipelines.** legacy_v0 and unified_v1, which share the parser and the writer. Contracts and manual are not affected.

**Closes.** DI-B3, DI-M11, DI-H6, DI-H5, DI-L7, and containment for DI-N6.

**Work:**
1. Parse `parse_legacy_reference_text` **only** for the references field. In every other list field, keep the raw string.
2. Validate per field. A field that fails validation is dropped and recorded; the valid fields are kept. Remove the whole-object `except → ParsedDocumentMetadata()`.
3. Add a metadata-completeness gate:
   - If core fields are empty, or any field failed, write `partial_failures.metadata_parse` and a visible degraded state (`metadata_degraded`, or `human_review_required`), never plain `completed`.
   - **Never call `sync_bidirectional(..., clear_existing=True)` with references from a degraded parse.** That call is what deletes existing links today.
4. The OCR-fallback path (DI-H6) leaves letterNo and subject empty and marks the document degraded. It never uses the filename.
5. Human edits survive reprocess (DI-H5). Keep a per-field `source=human` marker, following the existing pattern in `contract_clause/storage_service.py:336-347`, and honour `manual_summary_metadata_override`.
6. DI-N6 containment, with no data-model change yet:
   - Label `key_reply_points` as AI-generated advice in `LetterSummaryPage`.
   - In `letter_drafting/planning.py`, stop presenting it as `incoming_point` and stop stamping unrelated `source_ids`; present it as an advisory row.
   - Remove it from the Qdrant chunk payload in Phase B.
7. Fix the prompt numbering and the duplicate "Key Words" label (DI-L7). Changing the prompt text means bumping the prompt-version constant.

**Regression proof:**
- A parametrised test over every `List[str]` field in `ParsedDocumentMetadata`: a reference-shaped item keeps all other fields and marks the field degraded. This pins the six trigger fields found here.
- A caller-level test: a degraded parse does not clear existing reference links, and the document is not `completed`.
- A "still works" case: a clean report is unchanged, byte for byte.
- A static guard: `parse_extraction_report` has no broad `except` that returns an empty model.

**Data repair.** Run a read-only query for `completed` documents whose `metadata` snapshot is all-empty and whose `metadata_source` is a regex label. Reprocess them after the fix ships. Recover wiped reference links from `reference_sync` history if it is retained; otherwise re-derive them on reprocess.

**Exit criteria.** The DI-B3 reproduction script (in the findings doc, §3.3) yields the populated fields plus a degraded marker for `keywords` on REL + Phase A.

---

## Phase B — One Qdrant payload contract and indexing completion authority (M)

**Pipelines.** All four, in different ways. Correspondence (v0/v1) is the reader/writer mismatch. Contracts has the clause point ids. Manual has the chunk point ids and the P4 retry.

**Closes.** DI-B1, DI-H3, DI-H16, DI-N1, DI-N2, DI-N3, DI-M31, DI-M34, DI-L12, DI-M6, DI-L10, DI-L13, and the DI-N6 payload part.

**Work:**
1. Define one payload contract: flat `org_id`, `project_id`, `document_id`, `chunk_id`, `source_kind`, `schema_version`.
   - Prefer changing the single LangChain writer over teaching every reader two shapes.
   - Keep analysis fields (`key_reply_points`, `summary`, `issue_nature`, …) **out** of the payload.
2. Point ids for every writer are `uuid5(namespace, natural_id)`. The natural id (`clause_uid`, deterministic chunk id) stays in the payload. This covers DI-H16 and DI-N1.
3. Readers:
   - `/documents` passes a real `models.Filter` (DI-N2).
   - `list_chunk_ids` uses the contract key (DI-N3).
   - A Qdrant error **raises** to the caller, and the Mongo failsafe is used only when Qdrant is disabled by config. No swallowed `[]`.
4. Completion authority (DI-H3):
   - `completed` requires `vector_sync_status == synced` with count > 0 for non-empty text.
   - `error` or `mismatch` → a retriable `index_failed` state.
   - `replace_document` upserts new points first, then deletes stale ones.
5. Embed once per chunk (DI-M6). Create payload indexes on org, project and document (DI-L12). Add tenant fields to `vector_sync_status` (DI-M34). Treat a missing document as blocked in `publication_policy` (DI-M31).
6. **Backfill.** A migration that rewrites the payloads of existing correspondence points in place, or re-embeds them. Dry run first, with counts per org, and the migration dry-run gate before deploy.

**Regression proof.** A gate test that writes through the real upload writer and reads through `/v1/retrieval/search`, `/documents` vector search and `list_chunk_ids`, with a positive control and a cross-tenant negative. The DI-B1 local-mode proof is the template.

**Exit criteria.**
- The proof script shows filtered hits = 1 for the correspondence writer, and 0 for a foreign org.
- A production scroll (read-only) after the backfill shows no nested-only points.

---

## Phase C — Cross-tenant closures (S–M; can run in parallel with B)

**Pipelines.** v0 and v1 (graph and database service), plus bulk.

**Closes.** DI-H7, DI-H20, DI-M15, DI-M26, and DI-M27 (before Graphiti is ever enabled).

**Work:**
- Add an owner filter on every Falkor edge delete.
- Add an org filter on `get_thread` and `graph_codes_denied`.
- Migrate Letter node keys to tenant-scoped keys (`{org}:{normCode}`). Keep the existing "skip when another live owner exists" deletion rule until the migration completes.
- Delete the filename fallback in `_find_existing_document`.
- Store org and user on bulk jobs and check them on status reads.
- Stop using ObjectIds as Letter codes.

**Regression proof.** Tenant A/B boundary tests, one per query, including "A deletes → B's edges survive".

---

## Phase D — Correspondence retry and status durability (M)

**Pipelines.** v0 and v1 (the shared job layer). The legacy text-overwrite residue of DI-B4 is included.

**Closes.** DI-H11, DI-M1, DI-M2, DI-M3, DI-M4, DI-M9, DI-H8, DI-H9, DI-H10, DI-H13, DI-B4 (legacy_v0), DI-H22, DI-M8, DI-N5, DI-M32.

**Work:**
- **Job claims:**
  - Add `attempts < max_attempts` to the claim filter.
  - Add a unique partial index for one active job per document, plus a `(status, run_after, created_at)` index.
  - Put a lease token in every heartbeat and completion filter.
- **`/process`:** enqueue or force-requeue and return a `job_id`. Never run inline.
- **Routes and UI:**
  - Add an admin requeue route (DI-M9).
  - Add `GET /documents/{id}/processing-status` (DI-H8).
  - Generate the client status enum from `models/processing_state.py` (DI-H9).
- **Dedupe (DI-H13):**
  - Write sha256 and `duplicate_status` in the insert itself.
  - Add a unique partial index `(organization_id, project_id, sha256)` over active documents. Run a read-only duplicate census before creating it.
- **Text downgrades (DI-B4 legacy):** never downgrade stored `ocrText`/`full_text` to a shorter or LLM-sourced value on retry. Compare the source ranks, and keep the better text.
- **Unified engine:**
  - Treat blank pages as resolved-empty (DI-H22).
  - Refuse to create unified_v1 jobs when no canary worker can claim them, or let the main worker claim them (DI-N5).
- **Temp files (DI-M32):** use a unique temp dir per job and clean it up.

**Exit criteria.** Each of these has its own test:
- a poison job reaches dead-letter after N attempts;
- two concurrent enqueues produce one job;
- a retry never lowers text quality;
- the viewer shows review and partial states.

---

## Phase E — Canonical immutable extracted text, Markdown and provenance foundation (M–L)

**Pipelines.** v0 and v1. Contracts gets the Markdown/Marker cleanup.

**Closes.** DI-H4, DI-M10, DI-N7, DI-H1, DI-M7, DI-M33, DI-M30, and the DI-H19 foundation.

**Depends on.** D (durable retry) and B (re-embedding runs from canonical text).

**Work:**
1. Canonical text is the extracted page text only. It is immutable per extraction run and published through `document_extraction_heads` (wire `publish_run`, DI-M7).
2. LLM output is **never** written to `full_text`, `ocrText` or `body`, and never embedded as source (DI-H4, DI-M10). Remove `summary` from `publication_policy._TEXT_FIELDS` (DI-N7).
3. Make unified_v1 the correspondence default through the canary, org by org. This retires the legacy first-5-pages decision (DI-H1) and the legacy blind overwrite.
4. Replace the append-only `.md` summary with one canonical Markdown artefact per document per run, derived from page text, carrying page anchors (DI-M33). Either use Marker's output or remove Marker, and give it a timeout (DI-M30).
5. Record `extraction_run_id` and a text sha256 on every downstream artefact (chunks, metadata run, graph sync), so derived data can be traced to the text version it came from.

---

## Phase F — Structured metadata: separate source facts, analysis and reply advice (L)

**Pipelines.** v0 and v1, and the drafting consumers.

**Closes.** DI-H19, DI-N6 (fully), DI-M28, the remainder of DI-H5, and the data-model gap (§6 of the findings doc).

**Evaluation of the proposed target structure.** Adopt it, with four adjustments:

1. **Provenance is per fact, not per block.** Each item in `source_facts` is `{value, provenance}`, where `provenance = {document_id, extraction_run_id, page, source_span (offsets into canonical page text), extraction_method, model_version (the real model id), prompt_version (hash of the rendered prompt template), schema_version, confidence (from the model or validator, never a constant), human_review_status}`. A single `provenance` block beside the facts cannot say which page a date came from.
2. **Store extractions as an immutable versioned record** (`document_extractions`, one per run) and project the current values onto `documents`, with per-field `source ∈ {extracted, human}`. Reprocess writes a new extraction and never overwrites a `human` field.
3. **`reply_analysis` and `issues` are advisory by type.**
   - They are stored apart from `source_facts`.
   - They are never placed in Qdrant payloads, never returned by `authoritative_text`, and never used to ground a citation.
   - In drafting they appear as recommendations, each with its own evidence links. They are never an `incoming_point` unless backed by a `source_span`.
   - This is the structural fix for DI-N6. Phase A only contains it.
4. **Add three source facts the model lacks today:**
   - `amounts[]` (value, currency, span);
   - `deadlines[]` (date or duration, basis clause, span);
   - `attachments[]` (the letter's own "Encl:" list, distinct from uploaded enclosures).
   
   `references[]` becomes typed (letter, drawing, BOQ item, VO, IPC, EOT) with a span. That feeds Phase H.

**How far the current model is from the target.** Every source-fact field exists, but mixed and without provenance. Amounts, deadlines and attachments are missing. `issues` is almost entirely missing. `reply_analysis` exists only as `key_reply_points`, which is mis-typed as fact. Tags and subtags are already correctly separated. Provenance is nominal: constant confidence and a hard-coded prompt label.

**Migration shape.**
- Emit schema-validated JSON through the model's structured output. The regex report parser becomes a fallback that only ever degrades.
- Run a backfill that re-extracts from canonical text (Phase E).
- Change the prompt version with every prompt change; tests pin it.
- New LLM drafting-content calls use `strict=True` with a deterministic fallback, per house rule.

---

## Phase G — Bulk manifest, per-document jobs and bounded concurrency (M)

**Pipelines.** v0 and v1 (bulk, folder upload); contracts (upload hygiene).

**Closes.** DI-H12, DI-M16, DI-L5, DI-H18, DI-M12, DI-M13, DI-M14, DI-L1, DI-L2, DI-L3, DI-L6.

**Depends on.** D, for the job infrastructure.

**Work:**
- Persist a bulk manifest in Mongo, with one intake job per file driven by the worker. Add cancel and retry routes.
- Reject duplicate relative names in a manifest.
- Align the app upload limit with nginx's 200m outer bound. Lower the setting; do not raise nginx.
- Route folder uploads through `upload_streaming` + AV + precheck.
- On contract upload, call `duplicate_detection.precheck_upload` (house rule), take a merge lock or atomic claim, and return per-file results.

---

## Phase H — Reference resolution and graph quality (M)

**Pipelines.** v0 and v1.

**Closes.** DI-M23, DI-M24, DI-M25, DI-L15, and the remainder of DI-M26.

**Depends on.** F (typed references with spans) and C (tenant-scoped graph).

**Work:**
- Require a project for resolution, and filter by lifecycle, duplicates and deletion, with a date tiebreak.
- Resolve pending references when their target document is ingested, instead of relying only on the reaper.
- Build a typed extractor for the forms the reproduction showed failing: `Ref: X dtd`, `vide letter X dt`, `[n]` lists, and VO/IPC/drawing numbers.
- Label chronology fallbacks ESTIMATED or INFERRED.

---

## Phase I — Contract pipeline residue (M; after Phase 0)

**Closes.**
- DI-H15 (DOCX page records, org-scope clause indexing or a visible failure, completeness propagated)
- DI-H21
- DI-M35 (coalesce or 409; separate job ids per action)
- DI-M36 (a `retrying` state)
- DI-M38 (versioned vector swap, deletion of stale points and clauses)
- DI-M5 (async I/O)

---

## Summary of order

| Order | Phase | Severity removed from REL | Size | Blocked by |
|---|---|---|---|---|
| 0 | Integrate RECON + PR #34 | 1 BLOCKER, 2 HIGH, 1 MEDIUM, 2 LOW + partials | M (evidence) | replica-set run, CI |
| 1 | **A — metadata corruption** | 1 BLOCKER, 4 HIGH (B3, M11, H6, H5) + N6 containment | S | — |
| 2 | B — Qdrant contract + completion authority | 1 BLOCKER, 3 HIGH + N1 | M | — (backfill after A) |
| 2 | C — tenancy | 2 HIGH + 3 MEDIUM | S–M | — |
| 3 | D — retry/status durability | 6 HIGH + ~8 MEDIUM | M | — |
| 4 | E — canonical text + Markdown | 2 HIGH + 4 MEDIUM | M–L | B, D |
| 5 | F — structured facts / analysis split | 1 HIGH (+N6 structural) | L | E |
| 6 | G — bulk manifest | 2 HIGH + 3 MEDIUM | M | D |
| 7 | H — references / graph quality | 4 MEDIUM | M | C, F |
| 8 | I — contract residue | 1 HIGH + 4 MEDIUM | M | 0 |

**First implementation phase: Phase A.** It is small, has no dependency, and its failure is reproduced on every lineage. It stops ongoing silent data loss (emptied metadata, deleted reference links, filename-as-letter-number), with a per-field regression gate. Phase 0 runs beside it, as a release-engineering task with no new code.
