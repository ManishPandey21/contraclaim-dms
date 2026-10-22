# Document Ingestion — Current Release Gap Register

Baseline: `origin/release/contraclaim-rc1` @ `5a8cca2`, reconciled 2026-09-27 against
`reconcile/cl4a-onto-release-rc1` @ `4fcd012` and `fix/contract-master-reprojection-runtime` @ `3ca0149` (PR #34).
The evidence for each row is in [document-ingestion-reconciled-findings.md](document-ingestion-reconciled-findings.md).
Production was not read; production has historically run behind this release candidate.

## Counts

| | BLOCKER | HIGH | MEDIUM | LOW | Total |
|---|---|---|---|---|---|
| Original audit (`a179ca9` + dirty tree) | 4 | 22 | 38 | 16 | 80 |
| **Open with no fix on any lineage (CURRENT + PARTIAL residue), recomputed severity** | **2** | **19** | **26** | **23** | **70** |
| + fixed on RECON/PR #34 but not in release (still live on REL) | 1 | 2 | 1 | 2 | 6 |
| = live on REL today, original findings | 3 | 21 | 27 | 25 | 76 |
| New findings from this reconciliation (DI-N*) | 0 | 2 | 6 | 0 | 8 |

Outside the counts above:

| Category | Count | IDs |
|---|---|---|
| FIXED IN RELEASE | 3 | DI-H2, DI-M18, DI-L4 |
| FIXED BUT WAITING FOR INTEGRATION | 6 | DI-B2, DI-H14, DI-H17, DI-M37, DI-L11, DI-L16 |
| STALE / NOT APPLICABLE | 0 | |
| UNVERIFIED | 1 | DI-M19, a LOW finding whose impact depends on the production storage mode |

Why the severities moved:

| Finding | Change | Reason |
|---|---|---|
| DI-B4 | BLOCKER → HIGH | The page-engine mechanism is fixed. |
| DI-M11 | MEDIUM → HIGH | Same root and user effect as DI-B3. |
| DI-H21, DI-H22 | HIGH → MEDIUM | DI-H21: a working upload page exists. DI-H22: it costs review time; no data is lost. |
| DI-M7, DI-M8, DI-M13, DI-M20, DI-M21 | MEDIUM → LOW | Most of each is fixed in REL. |
| DI-M22, DI-M27, DI-M28, DI-M29, DI-M34 | MEDIUM → LOW | Guarded, dormant, or off by default in production. |

---

## BLOCKER (2)

| ID | Pipelines | Defect | Failure scenario | Classification |
|---|---|---|---|---|
| DI-B3 | legacy_v0, unified_v1 | One reference-shaped item in any `List[str]` metadata field makes the parser return an all-empty result, which is reported as success. | An LLM report lists `letter no. XYZ/12 dated 01.08.2024` under Key Words. Every field is emptied, the document goes `completed` with no degraded marker, and `sync_bidirectional(references=[], clear_existing=True)` deletes existing reference links. | CURRENT, reproduced on all lineages |
| DI-B1 | legacy_v0, unified_v1 | The correspondence vector writer nests tenant keys under `metadata.organization_id`; every scoped reader filters on flat `org_id`. | An uploaded letter is embedded successfully (`synced`), but RAG, drafting retrieval and `/documents` vector search return 0 results. The call does not raise, so no failover happens. | CURRENT, confirmed by executing the real code in Qdrant local mode (§3.1 of the findings doc). A production scroll would settle the live payload. |

## HIGH (19 carried + 2 new)

| ID | Pipelines | Defect (one line) | Status |
|---|---|---|---|
| DI-M11 | v0, v1 | Invalid or empty LLM output is still `completed`; no metadata completeness check. | CURRENT (raised) |
| DI-N6 | v0, v1 → drafting | `key_reply_points` (LLM advice) is shown as incoming-letter content in the reply matrix with unrelated `source_ids`, indexed in Qdrant payloads, and displayed unlabelled. | NEW |
| DI-N1 | manual, reconcile repair | Chunk ids that are not UUIDs are used as Qdrant point ids and rejected, so manual and repair vectors never land. | NEW, executed |
| DI-H3 | v0, v1 | Embedding and vector errors are swallowed; completion ignores `vector_sync_status`. | CURRENT |
| DI-H16 | contracts | The clause point id (a SHA-1 hash) is not a valid UUID. The upsert is rejected and the error is only logged, so the clause-first index is empty. | CURRENT, executed |
| DI-H4 | v0, v1 | The LLM "Full Content" is the authoritative text used for drafting and evidence. | CURRENT |
| DI-H5 | v0, v1 | Reprocess overwrites human-corrected metadata. | CURRENT |
| DI-H6 | v0, v1 | The OCR-fallback path writes the filename as letterNo and subject, which creates false reference links. | CURRENT |
| DI-H7 | v0, v1 | Falkor Letter graph: one tenant's delete removes another tenant's edges, and thread/denial reads return foreign letter codes into drafting. | CURRENT (security) |
| DI-H20 | v0, v1 | `_find_existing_document` falls back to a cross-tenant filename lookup. | CURRENT (latent cross-tenant write) |
| DI-B4 residue | v0; contracts on REL | Legacy retry overwrites `ocrText`/`full_text` blindly (conditional). Contract OCR retry rebuilds pages and vectors from degraded text. The contract fix is on RECON only. | PARTIAL |
| DI-H1 | v0 (default) | The legacy OCR decision is made for the whole document from the first 5 pages, so scanned annexures are never OCR'd. | CURRENT |
| DI-H8 | v0, v1 | `GET /documents/{id}/processing-status` has no route, so the viewer hides the banner and Retry. | CURRENT |
| DI-H9 | v0, v1 | The UI does not recognise `human_review_required`, `partially_processed`, `stored_only`, `skipped` or `cancelled`. | CURRENT |
| DI-H10 | v0, v1 | `/documents/{id}/process` runs inline in the API request, always reports success, and can leave the document stuck `processing`. | PARTIAL (only contracts are guarded, on RECON/CM) |
| DI-H11 | v0, v1 | The job claim has no attempts cap, so poison jobs cycle forever. | PARTIAL (OCR timeout fixed in REL) |
| DI-H12 | v0, v1 | Bulk upload runs in `BackgroundTasks` with temp files; a restart strands the batch; no cancel or retry route. | CURRENT |
| DI-H13 | v0, v1 | Duplicate precheck is not atomic: no unique sha256 index, and the `$set` of sha256 and `duplicate_status` is swallowed on failure. | CURRENT |
| DI-H15 | contracts | DOCX writes no page rows, and org-scoped uploads fail `validate_scope`, producing 0 clauses. The error is swallowed and the contract is `completed`. | PARTIAL (PARTIAL-extraction hold on RECON only) |
| DI-H18 | folder upload | No antivirus and no content dedupe on `/upload-file`. | PARTIAL (size cap fixed) |
| DI-H19 | all | No fact-level provenance (page, span, model, prompt, schema, confidence, review). | CURRENT (design) |

## MEDIUM (26 carried + 6 new)

| Area | IDs |
|---|---|
| Worker / job durability | DI-M1, DI-M2, DI-M3, DI-M4, DI-M9, DI-M32 (partial) |
| Indexing / vectors | DI-M6, DI-M31, DI-N2, DI-N3 |
| Canonical text | DI-M10, DI-M33, DI-N7 |
| Async / I/O | DI-M5 |
| Bulk | DI-M15 (cross-tenant status read), DI-M16 |
| Metadata / registers | DI-M17 |
| References / graph | DI-M23, DI-M24, DI-M25, DI-M26 |
| Contracts | DI-H21, DI-M12, DI-M14, DI-M30, DI-M35, DI-M36 (partial), DI-M38, DI-N4, DI-N8 |
| Unified engine | DI-H22, DI-N5 |

## LOW (23)

- **Partially fixed, residue only:** DI-M7, DI-M8, DI-M13, DI-M20, DI-M21.
- **Guarded or dormant:** DI-M22, DI-M27, DI-M28, DI-M29, DI-M34.
- **Original LOW findings:** DI-L1, DI-L2, DI-L3, DI-L5, DI-L6, DI-L7, DI-L8, DI-L9, DI-L10, DI-L12, DI-L13, DI-L14, DI-L15.

---

## Fixed but waiting for integration (live on REL until merged)

| ID | Sev on REL | Fixed on | What integration brings |
|---|---|---|---|
| DI-B2 | BLOCKER | CM `3ca0149` (PR #34) | A runtime-owned, durable, fenced reprojection loop in `contract-worker`, and a 409 `projection_not_current` in place of a silent `valid_empty`. |
| DI-H14 | HIGH | CM | Contract queue `waiting` state; the processing entry and heartbeat are kept during backoff. |
| DI-H17 | HIGH | CM | Governed documents: contract ingest invalidates the projection, and generic pipelines refuse (fail closed). |
| DI-M37 | MEDIUM | RECON (PR #31, `authorize_contract_scope`) + CM (lease reclaim) | Real contract-scope authorization in the evidence route; expired claims are reclaimed. |
| DI-L11 | LOW | CM | `_memory_index` is no longer appended when Qdrant is enabled. |
| DI-L16 | LOW | CM | Revision fence on projection failure. |

Integration preconditions, carried from the reconciliation:

1. **Replica-set proof.** The five runtime integration suites at CM were skipped (no replica-set Mongo). Run them against a disposable replica set before merge.
2. **Topology.** RECON itself is not in REL. Integrating PR #34 means integrating RECON (57 commits, CL-1…CL-4A).
3. **Tracked gaps on CM.** Carry these forward with the merge:
   - no Qdrant read-back before CURRENT;
   - no query-time revision filter;
   - the public `mark_projection_current` has no caller;
   - DI-N4 (revision race on confirm).
4. **Contract page residue.** DI-N8 needs a one-off reindex of contracts that a REL-era retry degraded.

## Fixed in release

| ID | Fix location at REL | Residual |
|---|---|---|
| DI-H2 | `services/extraction/text_quality.py`, used by legacy `ocr_service.py` and by `engine.py` | none |
| DI-M18 | `ocr_service.py:615-627`: encrypted PDFs fail visibly | still retried as if transient; no intake `encrypted` state |
| DI-L4 | `routers/profiles.py:152-161` sniffs bytes | none |

## Unverified

| ID | What would settle it |
|---|---|
| DI-M19 | Read the production storage mode. If uploads are S3-only, a unified job re-sniffs octet-stream and fails visibly. |
| DI-B1 (live payload only) | One production scroll of `document_vectors`. The code-level defect is mechanically confirmed regardless. |
| DI-B2 (runtime) | The CM replica-set integration suites, or a staging promotion reaching CURRENT with a heartbeat row. |
