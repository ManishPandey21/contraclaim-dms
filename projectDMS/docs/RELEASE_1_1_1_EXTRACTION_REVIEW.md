# Release 1.1.1: human review of extraction pages

Base: `release/contraclaim-rc1` = `d7eef845` (tree `fbb00621`), which is Release 1.1.

Release 1.1.1 closes the `human_review_required` dead end. A reviewer can now inspect
each flagged page, approve it as-is or reject it. Once every blocking page of the
current evidence is approved, the document is published.

## 1. Audit of the path this release starts from

| Question | Answer (code at `d7eef845`) |
|---|---|
| Page-level `needs_review` | `document_ocr_pages.needs_review`, `quality_verdict` and `quality_checks`. These are written by `DocumentPageStore.finalize_pages` (`services/extraction_adapters/document_page_store.py`) after `_apply_quality_gate` (`services/document_processor.py`). |
| Checkpoint `review_page_numbers` | On `document_processing_jobs`, written by `build_attempt_outcome` → `_checkpoint_extraction_attempt` (`models/processing_state.py`, `services/document_service.py`). |
| Document `human_review_required` | Written by `DocumentService._mark_human_review`, which sets `processing_error.pages` / `quality_review_pages` / `unresolved_pages`. The no-job path in `process_document_async` also writes it. |
| Stale vector / graph removal | `_invalidate_stale_publication` → `_delete_qdrant_vectors`, `document_vectors.delete_many` and `_invalidate_graph_contribution`. Graph contribution is deleted only when there is no consumable supporter left. |
| Canonical head review state | `document_extraction_heads` manifest: `review_pages`, `unresolved_pages`, `page_map[].needs_review` / `content_sha256`, `canonical_revision`, `canonical_sha256` (`publish_canonical`). |
| Why `create_embeddings_for_document()` refuses | `is_publication_blocked(doc)` is the exact complement of `publication_policy.is_consumable`, and `human_review_required` is the only adverse state. |
| Reusable graph/evidence publication | `DocumentService._publish_graph_and_evidence_from_current` (re-checks consumability before each store), then `reference_sync_service.sync_bidirectional` and `_sync_current_document_to_falkor`. This is the same sequence the duplicate-release path uses. |
| Existing review API/UI | None. `get_document_canonical_evidence` existed internally with no route. The client had only the "Needs human review" banner. |
| Viewer at a page | No. The iframe fragment was hard-coded (`#toolbar=1&navpanes=0&view=FitH`). |
| Audit infrastructure | `DocumentAuditService.emit` writes to `document_audit_events`. It is best effort: a failed emit is swallowed. |

```
PAGE REVIEW SOURCE OF TRUTH = document_ocr_pages (machine) + canonical head manifest; human decisions in document_page_reviews
DOCUMENT REVIEW STATUS      = documents.processing_status / processing_error (+ job status)
CANONICAL REVIEW STATE      = head.review_pages / unresolved_pages / page_map; new head.review_resolution
VECTOR BLOCK                = publication_policy.is_publication_blocked (status human_review_required)
GRAPH BLOCK                 = same predicate in _current_publication_payload, plus the purge at _mark_human_review
EXISTING REVIEW API         = none
EXISTING REVIEW UI          = banner only
AUDIT INFRASTRUCTURE        = DocumentAuditService -> document_audit_events
```

## 2. Storage model

We compared three options:

- **A. Nested review fields on `document_ocr_pages`.** This rewrites machine evidence rows. It also has no history for several attempts, and a concurrent reviewer could overwrite a decision.
- **B. A separate append-only `document_page_reviews` collection.** It keeps full history, gives a natural optimistic-concurrency key, and costs no migration.
- **C. Audit events plus page fields.** The audit stream is best effort, so it cannot be the source of truth.

**We chose B.** Each decision is one row. The row is bound to `extraction_run_id`, `canonical_revision`, `canonical_sha256` and the page's `content_sha256`, and it carries a copy of the machine verdict. Page text is never copied. A unique index on `(document_id, canonical_revision, page_number, decision_version)` is the compare-and-set: two reviewers deciding the same page from the same state cannot both win.

That index is created in startup `ensure_indexes`, not through a catalogued migration. A new catalogue entry would expire the Gate 6 fresh-install evidence (`test_gate6_fresh_install_evidence.py`).

## 3. Machine verdict preservation

Approval never writes `document_ocr_pages` and never writes the head manifest fields. The integration test compares the page row byte for byte before and after approval. On resolution, the document's adverse `processing_error` is kept in `extraction_review.machine_verdict`, and the audit events carry the machine verdict.

## 4. Canonical revision behaviour

`canonical_revision` is **not** incremented on approval:

- The evidence text did not change.
- `canonical_revision` + `canonical_sha256` are what citations pin.
- A bump would invent a revision that has no page rows behind it.

Instead, the approval is bound immutably to the reviewed revision:

- `head.review_resolution` is written under a guard on the exact revision and checksum.
- `publish_canonical` (any new run) now `$unset`s `review_resolution`.
- Decision rows for the old revision stay as history and never apply to the new one.

## 5. Status sequence

```
human_review_required
  --(last blocking page approved; CAS on status + processing_job_id)-->
partially_processed / stage review_resolved_publication_pending   (consumable: content now authoritative)
  --(create_embeddings_for_document + vector check + graph/evidence/reference publication)-->
completed                                     if vectors synced (Mongo rows == mongo_chunks == qdrant_chunks)
partially_processed / stage indexing_incomplete  otherwise (never back to human review)
```

Rejected pages stay blocking. A page that is unresolved or has withheld text cannot be approved as-is (409); it needs re-extraction. Quarantined, duplicate-pending, deleted, contract-held and contract-source documents are refused before any decision is written.

Publication runs after the HTTP response (FastAPI background task). `POST /api/documents/{id}/extraction-review/finalize` resumes a crash or retries an indexing failure. It is idempotent: embeddings replace a document's points, graph writes MERGE, and status writes are guarded.

## 6. API

| Route | Gate |
|---|---|
| `GET /api/document-extraction-reviews` | selection list filters + `PolicyService` `dms.document.view` + `build_document_query` |
| `GET /api/documents/{id}/extraction-review` | selection + `authorize_document(dms.document.view)` |
| `POST /api/documents/{id}/extraction-review/pages/{page}/approve` | selection + `authorize_document(dms.document.review_extraction)` |
| `POST /api/documents/{id}/extraction-review/pages/{page}/reject` | same; a comment is required |
| `POST /api/documents/{id}/extraction-review/finalize` | same |

The request body is `canonical_revision`, `page_checksum`, `comment` and an optional `expected_decision_version`. The server supplies the reviewer and the time. Stale evidence returns 409.

## 7. Permission

`dms.document.review_extraction` is new, and narrower than `dms.document.edit_metadata`, which `orguser` / `projectuser` hold. It is in `CLIENT_DMS_PERMISSIONS`, so the release role definitions give it to `superadmin`, `orgadmin`, `projectadmin` and `contractmgr_org`.

**Production rollout:**

- The permission catalogue entry is seeded at startup.
- `orgadmin` / `projectadmin` role documents receive it only through the reviewed `align_role_contract` operation (an addition of class A).
- `contractmgr_org` would need an `OWNER_APPROVED_GRANTS` entry, which is an owner decision.

Super Admin is admitted by name but is still held to the active selection; the RBAC test covers this.

## 8. Not in this release

- manual text correction
- re-extraction of a single page
- G15, G16, R4
- metadata parser changes
- chronology
- global unified rollout
