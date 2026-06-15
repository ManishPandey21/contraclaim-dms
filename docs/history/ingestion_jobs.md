# Ingestion Jobs (v1)

Ingestion is orchestrated as a staged pipeline to keep uploads idempotent and observable. Routes live under `/v1/ingestion`.

## Endpoints

- `POST /v1/ingestion/jobs`
  - Body: `{"org_id", "project_id", "document_id", "options": {"chunk_size": 1200, "chunk_overlap": 120, "embedding_model"?, "enrichment_on": false, "enrichment_strategies": [], "use_enriched_text": false}}`
  - Response: `{job_id, status, stage, progress, created_at}`. The job is queued on the background processor immediately.

- `GET /v1/ingestion/jobs/{job_id}`
  - Response: `{status, stage, progress, stage_timings, error?, deduped}`.

## Pipeline stages

`queued → extracting → chunking → embedding → indexing → enriching? → done/failed`

- **extracting**: loads document text (`full_text`/`ocrText`) and computes a `content_hash`.
- **chunking**: deterministic chunk IDs built from document_id + index; stores page ranges when available.
- **embedding**: uses the configured OpenAI embedding model (falls back to a deterministic hash in offline/dev mode).
- **indexing**: upserts into Qdrant (or an in-memory fallback) with payload `{org_id, project_id, document_id, chunk_id, page, text, text_enriched?, tags}` and writes chunks to Mongo.
- **enriching (optional)**: rewrites chunks using neighborhood/semantic strategies and stores `text_enriched` plus `enrichment_metadata`.

## Idempotency

- `content_hash` is computed on the extracted text; if the same `document_id` + `content_hash` already reached `done`, the pipeline short-circuits with `deduped: true` and skips embedding/indexing.
- Chunk IDs are deterministic (`sha256(document_id:index:page_start:text)`) to avoid duplicating payloads across retries.

## Collections

- `ingestion_jobs`: `{job_id, org_id, project_id, document_id, content_hash, status, stage, progress, stage_timings[], deduped, error, created_at, updated_at, options}`
- `chunks`: `{chunk_id, document_id, org_id, project_id, page_start/end, text_original, text_enriched?, enrichment_metadata?, tags, content_hash, created_at}`

## Failure handling

- Failures set `stage=failed` with `error` attached. Jobs remain queryable for audits.
- Background workers retry transient errors (see `services/background_jobs.py`); persistent failures are logged to `rag_runs` via the observability service.

