# Correspondence Qdrant backfill plan (DI-B1)

Status: **plan**. Nothing in this document has been run against production. The
census script it relies on is read-only and has been run only against disposable
test collections.

## 1. Why a backfill is needed

Before the DI-B1 fix, every correspondence vector was written by
`LangChainVectorService.replace_document` through LangChain `add_texts`. That call
stores the payload as `{page_content, metadata: {organization_id, project_id, ...}}`.

Every tenant-scoped reader filters the flat `org_id` / `project_id` fields:

- `VectorClient.search`, and through it `RetrievalService.search`;
- the `/api/v1/retrieval/*` search, RAG and contract-QA endpoints;
- the drafting agent;
- the LangGraph drafting pipeline.

So the legacy points exist and an unfiltered search finds them, but no scoped search
ever returns them. This was reproduced on the combined Phase B base (`9a071c5`), in
qdrant-client local mode and on a real Qdrant 1.12.5: 1 point written, 1 unfiltered
hit, 0 scoped hits, and a flat control point found by the same scoped search.

The fix changes the writer. Points already stored keep the legacy shape and stay
invisible until they are rewritten.

Legacy **read** compatibility was considered and rejected, for two reasons:

- Legacy payloads carry `metadata.key_reply_points` (AI reply advice, DI-N6).
  Making them readable would surface that advice as source evidence.
- The points were never reachable by a scoped reader, so leaving them unreadable
  removes nothing users had.

## 2. Payload schemas

### 2.1 Legacy (pre-fix) — `legacy_langchain_envelope`

```json
{
  "page_content": "<chunk text>",
  "metadata": {
    "document_id": "<str>", "organization_id": "<str or ''>", "project_id": "<str or ''>",
    "uploadType": "incoming|outgoing", "letterNo": "...", "filepath_local": "...",
    "filepath_s3": "...", "chunk_index": 0, "source": "document_processing",
    "chunk_id": "<document_id>-<24 hex>", "embedding_model": "...", "embedding_provider": "openai",
    "embedding_version": "v1", "chunking_version": "v1", "subject": "...", "summary": "...",
    "keywords": [], "...": "descriptive fields",
    "key_reply_points": ["<AI reply advice - present on points written before PR #36>"],
    "checksum_sha256": "...", "checksum": "...", "qdrant_point_id": "<uuid5>"
  }
}
```

- There is no flat `org_id`, and no schema marker.
- An organisation-less document was written with `organization_id: ""`, which makes
  an unscoped point.
- The storage-sync legacy repair path wrote the same envelope, with an extra
  `metadata.org_id`.

### 2.2 Native flat, unversioned — `native_flat_unversioned`

These are points written by `VectorClient.upsert`:

- contract token chunks (`contracts_ingest`);
- the manual ingestion pipeline;
- `retrieval/reconcile.py`;
- the storage-sync `chunks` branch.

They carry flat `org_id` / `organization_id` / `project_id`, so they are readable.
They are **not** correspondence backfill targets. The `contract_clauses` namespace is
a separate collection and is out of scope.

### 2.3 Canonical — `canonical_correspondence` (`payload_schema_version = "correspondence.v1"`)

This payload is built only by
`retrieval/correspondence_payload.py::build_correspondence_chunks`.

| Group | Fields |
|---|---|
| authority | `org_id`, `organization_id` (same value), `project_id` (string, or `null` for an organisation-level letter) |
| identity | `document_id`, `chunk_id`, `chunk_index`, `qdrant_point_id`, `doc_type` (`letter` for incoming/outgoing), `uploadType`, `source_type`, `source`, `page` (null), `page_numbers` |
| content | `text` |
| descriptive | `letterNo`/`letter_no`, `subject`, `summary`, `keywords`, `tags`, the extracted classification fields |
| provenance | `checksum_sha256`, `embedding_*`, `chunking_version`, `source_hash`, `payload_schema_version` |

Rules the builder and the writer both enforce:

- **Authority comes from the stored document row only.** It is written after every
  other field, so nothing else can shadow it.
- **One string form for ids.** An `ObjectId` and its string produce the same value.
  A non-id value (a dict, a list, a bool) is refused.
- **No organisation, no point.** A document without an organisation raises
  `CorrespondencePayloadError`. The writer marks `vector_sync_status = error` and
  records a partial failure; nothing is published.
- **No reply advice.** `key_reply_points`, `points_to_address`,
  `suggested_response_considerations` and `reply_recommendation(s)` never appear.
  The writer refuses a payload that contains them. The same applies to the
  **text**. Text that carries the report's "Key Reply Points" item is the LLM
  extraction report being used as the letter body, so it is refused before
  chunking: no point is written, and the sync is marked `error`. The builder also
  refuses any chunk that carries the heading. Cleaning the text was tried and
  rejected, because every stripping rule either leaked advice or dropped the body.
- **No interpretation.** `alleged_responsibility` and `linked_event_suggested`
  stay on the Mongo row but never enter a vector payload.
- **Invisible to the native reconcilers.** `VectorClient.list_chunk_ids` excludes
  canonical correspondence points. Its callers (ingestion prune and sync,
  `VectorReconciler`, and the storage-sync `chunks` branch) delete whatever is
  missing from `db.chunks`, and letters never have `chunks` rows.
- **Stable point id.** The id is `uuid5(NAMESPACE_URL, "contraclaim:qdrant:{document_id}:{chunk_id}")`,
  the same formula the legacy writer used. A rewrite therefore lands on the same point
  ids.
- **Validation before deletion.** The writer validates before it deletes anything.

`project_id: null` follows `build_scope_query`: a project-scoped search matches by
equality, so an organisation-level letter is not returned to a project search.
An organisation-level letter (Mongo `project_id: ""`) is reached by a retrieval
request with an explicit `"project_id": null`. That means organisation-level
documents only (`project_id IS NULL`), never all projects. It is answered only
for an actor with organisation-wide scope in that organisation
(`ScopeService.has_organization_wide_scope`); anyone else gets no rows.

## 3. Detecting legacy points

`classify_vector_payload` in `retrieval/correspondence_payload.py` is the only
definition of each class:

| Class | Rule |
|---|---|
| canonical | `payload_schema_version == "correspondence.v1"` |
| legacy envelope | no version, no flat org, `metadata.organization_id` / `metadata.org_id` present |
| native flat | no version, flat `org_id` / `organization_id` present |
| unscoped | no organisation anywhere |

### Census (read-only)

Run inside the backend container; the host interpreter lacks the dependencies.

```bash
docker compose -f docker-compose.prod.yml -f docker-compose.mongo-replicaset.yml exec -T backend \
  python scripts/correspondence_vector_census.py --collection document_vectors [--list-document-ids]
```

The census reports:

- totals per class;
- per-organisation counts;
- distinct legacy documents per organisation;
- the number of legacy points carrying reply advice.

It only scrolls. There is deliberately no resume option: a resumed scroll would report
part of the collection as if it were all of it. Exit codes: `0` when no legacy or
unscoped point remains, `3` when any does, `2` on a connection or configuration error.

### Qdrant REST equivalents, for spot checks

```text
POST /collections/document_vectors/points/count
{"exact": true, "filter": {"must": [{"is_empty": {"key": "payload_schema_version"}},
                                     {"is_empty": {"key": "org_id"}}]}}
   -> legacy-envelope + unscoped points

POST /collections/document_vectors/points/count
{"exact": true, "filter": {"must": [{"key": "payload_schema_version",
                                      "match": {"value": "correspondence.v1"}}]}}
   -> canonical points
```

### Per-document drift is now visible

`routers/storage_sync.py::_qdrant_document_filter` counts only canonical points for a
correspondence document. A document whose only points are legacy now reads as
`mismatch` (0 canonical against N Mongo rows) instead of `synced`. So
`POST /storage-sync/reconcile` with `dry_run: true` also lists legacy documents as
`would_repair`.

## 4. Migration method: **reindex** (recommended), not payload patch

The recommended path reindexes each affected document from its authoritative source,
using the existing repair writer: `storage_sync._resync_document_vectors`, legacy
(`document_vectors`) branch. Per document it:

1. Resolves the canonical `documents` row. It skips the document unless
   `is_consumable` (the publication barrier applies).
2. Reads that document's Mongo `document_vectors` rows, ordered by `chunk_index`.
   These are the chunk texts the original writer embedded.
3. Builds canonical points with `build_correspondence_chunks`, taking authority from
   the document row and never from the stored rows. A row naming another tenant is
   ignored; this is covered by a test.
4. Calls `LangChainVectorService.replace_document`. That deletes the document's
   points in both the flat and the `metadata.*` layout, then upserts the canonical
   points.
5. Recomputes the canonical count and sets `vector_sync_status`.

Why reindex rather than payload patch:

- **Authority.** Reindex takes authority from the current document row. It does not
  trust the legacy envelope, which may hold `""` or a stale value.
- **Publication policy.** Blocked, quarantined, deleted and duplicate documents are
  not republished.
- **Reply advice.** Legacy `key_reply_points` are dropped by construction.

Why a payload patch was not chosen: `overwrite_payload` by point id would avoid
embedding cost, since the legacy vectors were embedded from the same text. But it
would need its own copy of the authority, consumability and advice rules. It remains
a fallback only if embedding cost proves prohibitive, and it would need its own
review.

### Runner — to be built before the backfill, not built in Phase B

`POST /storage-sync/reconcile` cannot drive a full backfill, for three reasons:

- It scans only `limit ≤ 200` documents per call, ordered by `updatedAt desc`.
- A repaired document does not move out of that window, so repeated calls rescan the
  same documents and never reach document 201.
- It needs superadmin step-up for each call.

A small batch runner is required. It should reuse `_resync_document_vectors` and
nothing else, and have these properties:

- **Scope:** one organisation per run (mandatory `--org-id`), optionally one project.
  Documents are `uploadType ∈ {incoming, outgoing}`, not deleted, and `is_consumable`.
- **Order and resume:** iterate `documents` by `_id` ascending. After each batch,
  persist the last `_id` in a `vector_backfill_runs` checkpoint document keyed by run
  id. A restarted run continues from the checkpoint.
- **Batch size:** 50 documents per batch (default). Pause between batches to stay
  under the embedding provider's rate limit. Wall time is roughly
  `total_chunks / embeddings_per_second`.
- **Dry run (default):** per document, report the Mongo row count, the canonical
  point count and the legacy point count, plus the action it would take. It writes
  nothing.
- **Apply:** the `--apply` flag is required. Documents that are already fully
  canonical (canonical count = row count and legacy count = 0) are skipped, so a
  rerun is idempotent.
- **Failure and retry:**
  - A per-document failure is recorded with its error in the run document, and the
    runner moves on. It does not abort the batch.
  - `--retry-failed` reprocesses only the recorded failures.
  - A Qdrant outage aborts the run with its checkpoint intact. The writer raises and
    returns 0, and the runner treats `qdrant_chunks = 0` with `rows > 0` as a failure.
- **Documents with no rows:** a document with no `document_vectors` rows cannot be
  reindexed this way. It is listed for a full reprocess
  (`POST /documents/{id}/process`), which re-runs extraction.
- **Documents whose rows carry reply advice:** when a letter had no OCR text and no
  parsed full content, its rows were chunked from the LLM extraction report,
  including the "Key Reply Points" item. A row boundary can split that item, so it
  cannot be removed row by row. `_resync_document_vectors` answers `409` for such a
  document, and `scripts/reconcile_vectors.py` skips it with a warning. The runner
  lists these documents for a full reprocess. A reprocess that again yields only
  the report is refused visibly (sync `error`); such a letter needs real text,
  from OCR or supplied content, before it can be indexed. Rows written by the
  canonical writer carry `payload_schema_version` and are trusted by the repair
  paths.

## 5. Procedure

Run it per organisation, starting with the smallest.

1. **Snapshot.** Take a Qdrant snapshot of `document_vectors`
   (`POST /collections/document_vectors/snapshots`) and record its name. Confirm that
   the Mongo backup for the day exists.
2. **Census before.** Run it and keep the JSON; it is the baseline for step 6.
3. **Deploy the Phase B code.** New uploads are canonical from this point. Legacy
   documents stay unsearchable until reindexed, which is the status quo, not a
   regression.
4. **Dry run.** Run the runner in dry-run mode for the organisation. Review the
   would-repair count against the census legacy-document count for that
   organisation; the two must agree.
5. **Apply.** Run it with `--apply` in batches. Watch `vector_sync_status` values of
   `error` and `mismatch`, and the runner's failure list.
6. **Verify.** See §6.
7. **Repeat** for the next organisation.
8. **Cleanup.** See §8.

## 6. Verification

1. **Census.** The organisation's `legacy_document_counts` entry is 0, and canonical
   points ≈ the baseline legacy points for the reindexed documents.
2. **Sync status.** Per document, `vector_sync_status.sync_status == "synced"` and
   `qdrant_chunks == mongo_chunks`.
3. **Retrieval probe.** For a sample of reindexed documents (at least 5 per
   organisation, across projects), call `POST /api/v1/retrieval/search` as a user of
   that organisation and project, using a phrase from the letter. The document must
   be returned.
4. **Negative probe.** Search the same phrase as a user of another organisation, and
   as the same organisation with a different project. The document must be absent.
5. **No advice.** Count canonical points with a non-empty `key_reply_points`; it
   must be 0:

   ```text
   {"must": [{"key": "payload_schema_version", "match": {"value": "correspondence.v1"}}],
    "must_not": [{"is_empty": {"key": "key_reply_points"}}]}
   ```

   The writer also refuses such a payload.

## 7. Cross-tenant safety checks

- Authority on every rewritten point comes from `documents.organization_id` /
  `project_id` at reindex time. It never comes from `document_vectors` rows or the
  legacy envelope.
- The runner requires `--org-id`. It must refuse to run without one, and must refuse
  a document whose `organization_id` differs from the run's organisation (a
  defence-in-depth check before calling the writer).
- A document with no organisation is refused by the builder, so it is never
  republished. Its legacy points appear as `unscoped` in the census; handle them
  under §8.
- Reindex deletes both layouts for exactly one `document_id` within that document's
  organisation. The delete filter carries the organisation, so a shared or colliding
  `document_id` in another organisation is not touched.

## 8. Rollback and cleanup

**Rollback.**

- Code: redeploying the previous image restores the old writer, and new uploads again
  become invisible to scoped search. Rollback is only a recovery step for an
  unrelated defect.
- Data: restore the step 1 snapshot. Legacy points were never readable by scoped
  search, so restoring them loses nothing a user had. Canonical points written by the
  backfill are then gone, and documents return to the pre-backfill state.

**Cleanup criteria.** All of the following must hold before the backfill is declared
complete:

1. The census exits `0` for the collection, or every remaining legacy or unscoped
   point is on the §4 "no rows" list with a reprocess ticket.
2. Unscoped points (organisation-less legacy points) are deleted by point id after
   the owner reviews their `document_id` list. They can never be served and must not
   be republished without an organisation.
3. The §6 verification passes for every organisation.
4. No canonical point contains an advisory field (§6 step 5).

After cleanup, the `metadata.*` branches of `LangChainVectorService.delete_document`
and `VectorClient.list_chunk_ids` exist only for legacy points, and can be removed in
a follow-up.
