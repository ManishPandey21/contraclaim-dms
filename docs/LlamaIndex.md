# LlamaIndex Integration Plan

## Goals
- Replace bespoke OpenAI embedding pipeline with LlamaIndex primitives for consistency across document ingestion flows.
- Centralize MongoDB vector storage via MongoDBAtlasVectorSearch and track vector metadata (embedding_id, ector_ref).
- Maintain existing document processing API surface while enabling richer retrieval pipelines.

## Current State Assessment
- DocumentProcessor delegates embedding creation to DatabaseService, which chunks text and calls AsyncOpenAI.embeddings.create, persisting raw float arrays in document_vectors.
- Contract ingestion (contracts_ingest.py) implements a parallel pathway that also writes raw embeddings directly to MongoDB.
- No shared abstraction manages vector store lifecycle, and Mongo collections do not retain external vector-store identifiers.
- Tests (	est_embedding_fix.py, 	est_openai_fix.py) patch OpenAI clients directly; no coverage for LlamaIndex interactions.

## Target Architecture
- Introduce a lightweight LlamaIndexVectorService that:
  - Accepts text chunks + metadata, builds llama_index.core.Document objects, and pushes them to MongoDBAtlasVectorSearch.
  - Returns both generated vector IDs and metadata required for Mongo bookkeeping.
  - Lazily instantiates an underlying synchronous pymongo.MongoClient from existing config for compatibility with LlamaIndex.
- Update document processing and contract ingestion flows to:
  - Reuse existing chunking logic.
  - Capture returned ids as embedding_id (local deterministic id) and ector_ref (Atlas vector id) per chunk.
  - Persist text snippets and metadata without storing full float arrays.
- Retain ability to short-circuit to legacy embedding pipeline via config toggle for rollback.

## Implementation Steps
1. **Dependencies & Config**
   - Add llama-index-core, llama-index-embeddings-openai, and llama-index-vector-stores-mongodb to equirements.txt.
   - Extend DocumentProcessingConfig with vector store options (collection name, toggle flag).
2. **Service Layer**
   - Create services/llamaindex_service.py encapsulating LlamaIndex setup, including:
     - Construction of OpenAIEmbedding(model=config.openai_embedding_model)
     - Lazy MongoDBAtlasVectorSearch initialization targeting configurable collection.
     - sync adapters that delegate blocking ector_store.add calls via syncio.to_thread.
   - Provide helper methods to serialize metadata (document id, chunk index, project/org context).
3. **DatabaseService Refactor**
   - Replace _create_embeddings and direct Mongo insert of float arrays with calls to LlamaIndexVectorService.
   - Persist new document_vectors documents containing:
     - embedding_id (stable UUID/sha for chunk)
     - ector_ref (Atlas vector identifier returned by LlamaIndex)
     - chunk metadata (chunk_index, 	ext, checksum, etc.)
     - remove stored embedding blobs to shrink records.
   - Handle removals by deleting via ector_ref when reprocessing documents.
4. **Contract Ingest Pipeline Alignment**
   - Update contracts_ingest.py to reuse the new vector service for embeddings.
   - Ensure ingestion job writes the same metadata shape.
5. **Models & Schemas**
   - Adjust Pydantic models (e.g., Document or vector DTOs) if they expose embedding fields to include embedding_id/ector_ref.
6. **Testing**
   - Introduce unit tests with patched LlamaIndex components verifying:
     - Correct Document metadata assembly.
     - Proper persistence of embedding_id/ector_ref.
   - Update existing tests to mock the new service instead of raw OpenAI calls.
7. **Migration Strategy**
   - Provide a helper script (future work) to backfill legacy document_vectors records, flagging this as a follow-up task.
   - Document config toggle to revert to legacy pipeline during rollout.

## Risks & Mitigations
- **LlamaIndex dependency weight**: keep import scoped to the new service to avoid startup penalties when disabled.
- **Async vs sync Mongo clients**: wrap synchronous vector operations with syncio.to_thread and centralize client lifecycle management.
- **Existing consumers expecting embedding array**: audit usages and update to rely on ector_ref; provide compatibility shims where immediate rewrite is impractical.

## Validation Plan
- Automated tests covering document processing + contract ingestion to confirm vector documents include both identifiers and omit raw embeddings.
- Manual smoke test processing a sample PDF to verify vector documents exist in Mongo with expected fields and that downstream retrieval code remains functional (or gated behind config).
- Document fallback and monitoring procedures in project README / ops notes.
