## FalkorDB Metadata Upload Investigation

### 1. End-to-End Flow

1. **Upload & stub creation**  
   `backend/rbac_backend/routers/documents.py` → `DocumentController.create_document` stores the file and calls `DocumentService.create_document`.
2. **Background processing**  
   `DocumentService.queue_document_processing` enqueues OCR/metadata work.  
   `DocumentService.process_document_async` (same file, ~line 600) runs OCR, metadata extraction, and builds `update_fields` for Mongo.
3. **Mongo update**  
   The processed metadata is written back to the `documents` collection (`update_fields`).
4. **Graph ingestion**  
   Immediately after the Mongo update, `DocumentService.process_document_async` calls  
   `self.graph_ingestion.ingest_document(...)`.
5. **GraphIngestionService**  
   `backend/rbac_backend/graph/graph_ingestion_service.py` orchestrates:
   - Upserting document nodes/edges via `GraphAdapter` (Graphiti/Neo4j integration).
   - Upserting Falkor graph nodes and references with `sync_document_to_falkor`.
6. **Falkor upsert**  
   `backend/rbac_backend/services/falkor_graph_service.py` (`FalkorGraphService.upsert_letter_with_refs`) executes RedisGraph commands to create/update the `Letter` node and `CITES` / `REPLIES_TO` relationships.

### 2. Relevant Files

| File | Responsibility |
|------|----------------|
| `backend/rbac_backend/routers/documents.py` | API endpoints for document upload/process. |
| `backend/rbac_backend/services/document_service.py` | Metadata extraction, Mongo persistence, and invocation of graph ingestion. |
| `backend/rbac_backend/graph/graph_ingestion_service.py` | Converts document metadata into graph nodes, relationships, and triggers Falkor sync. |
| `backend/rbac_backend/graph/graph_adapter.py` | Graphiti client wrapper. Determines `GraphIngestionService.enabled`. |
| `backend/rbac_backend/services/falkor_graph_service.py` | Direct RedisGraph/FalkorDB integration. |
| `backend/rbac_backend/config/document_processing_config.py` | Aggregates env/settings (`FALKORDB_*`, `QDRANT_*`) used by services. |

### 3. Root Cause of Missing Metadata

`GraphIngestionService.ingest_document` short-circuits when `GraphAdapter.config.enabled` is `False`:

```python
if not self.enabled:
    logger.debug("Graph ingestion disabled; skipping document %s", document_id)
    return
```

`self.enabled` defers to `GraphAdapter.config.enabled`, which is only `True` when both `GRAPHITI_BASE_URL` and `GRAPHITI_API_KEY` are configured (see `GraphConfig.from_settings` in `graph_adapter.py`). In the current deployment these Graphiti settings are unset, so `enabled` is `False`.

**Impact:** When the Graphiti adapter is disabled, `ingest_document` returns before reaching `sync_document_to_falkor`, so no metadata is written to FalkorDB despite successful Mongo updates.

This explains why:

- `scripts/check_qdrant_falkor.py --document-id ...` reports metadata in Mongo but finds 0 letters in FalkorDB.
- Falkor connection tests run successfully, yet the graph remains empty.

### 4. Suggested Remediation

1. **Decouple Falkor ingestion from GraphAdapter state**  
   Remove or refactor the early return so Falkor sync still runs when Graphiti is disabled, e.g.:
   ```python
   if not self.adapter.config.enabled:
       logger.debug("GraphAdapter disabled; skipping Graphiti upsert but continuing Falkor sync for %s", document_id)
   else:
       # perform GraphAdapter upserts
   # Always execute sync_document_to_falkor(...)
   ```
2. **Alternatively configure Graphiti**  
   Provide `GRAPHITI_BASE_URL` and `GRAPHITI_API_KEY` so `GraphAdapter` remains enabled. This may be overkill if Falkor is the only required graph target.
3. **Add observability**  
   Log a warning when Falkor sync is skipped because the adapter is disabled; currently only a debug message is emitted.

### 5. Validation Steps

1. Apply the code change above (or configure Graphiti).
2. Restart the backend and reprocess a document:  
   `POST /documents/{id}/process`.
3. Run  
   `python backend/scripts/check_qdrant_falkor.py --document-id <id>`  
   and confirm:
   - `Qdrant chunks` > 0 (if embeddings enabled).
   - `Falkor letter` exists for the normalized code.
4. Optional: execute `python backend/scripts/test_falkordb_connection.py` to ensure the Falkor integration tests no longer skip.

### 6. Current Status

Without the refactor, FalkorDB receives no metadata even though uploads succeed and Mongo is populated. Implementing the remediation will allow metadata to flow into FalkorDB independent of Graphiti configuration.

