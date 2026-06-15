# AI Dependency Installation &amp; Integration Audit

This audit documents how LlamaIndex, Qdrant, LangGraph, and Graphiti are installed, configured, initialized, and used across the repository. It highlights configuration gaps and provides production-readiness recommendations.

Repository scope inspected:

- docker-compose.yml, docker-compose.prod.yml
- backend/ (FastAPI API, LlamaIndex service)
- services/langgraph (LangGraph orchestrator)
- services/graphiti (Graphiti knowledge graph API)
- services/docling (OCR and vector upsert to Qdrant)
- client/ (React frontend)
- docs/ (design notes and plans, including prior LlamaIndex integration plan)

---

## 1. LlamaIndex

### Installation

- Current install sources checked:
  - backend/Dockerfile installs from backend/requirements.txt
  - backend/requirements.txt: file exists but returned empty content on read; likely empty or misconfigured
  - backend/rbac_backend/requirements.txt: exists but appears malformed/encoded; no `llama-index*` packages present
  - No occurrences of `llama-index` in any requirements\*.txt across the repo
- Code depends on LlamaIndex in:
  - backend/rbac_backend/services/llamaindex_service.py
    - Imports:
      - `from llama_index.core import Document, VectorStoreIndex`
      - `from llama_index.embeddings.openai import OpenAIEmbedding`
      - `from llama_index.vector_stores.mongodb import MongoDBAtlasVectorSearch`
- Conclusion: LlamaIndex is referenced in backend code but not installed by the backend image. This will fail at import time when vector store features are enabled.

Recommended packages to add to backend/requirements.txt:

- llama-index-core
- llama-index-embeddings-openai
- llama-index-vector-stores-mongodb
- pymongo (already present via rbac requirements, but ensure it is in the active backend requirements)
- motor (async Mongo, already used)

### Environment Variables

- Consumed by backend configuration in:
  - backend/rbac_backend/config/document_processing_config.py
  - backend/rbac_backend/core/config.py
- Relevant keys:
  - OPENAI_API_KEY
  - DATABASE_URL (Mongo URI for backend main DB)
  - LOCAL_MONGODB_URI (optional override for local dev)
  - VECTOR_STORE_ENABLED (bool, default True)
  - VECTOR_STORE_COLLECTION (default: document_vectors)
  - OPENAI_EMBEDDING_MODEL (default: text-embedding-3-small)
  - VECTOR_STORE_DB_NAME (database_name override)
- The backend Settings loads from .env.example (if present) and then .env:
  - `load_dotenv(".env.example", override=False)` then `load_dotenv()`

### Import &amp; Initialization

- Entry points:
  - backend/rbac_backend/services/database_service.py
    - `_create_and_store_embeddings` checks `config.vector_store_enabled`
    - Uses `LlamaIndexVectorService` to embed chunks and store to Mongo
  - backend/rbac_backend/services/llamaindex_service.py
    - Ensures MongoClient (Atlas vs local MongoDB 8+):
      - Uses `ServerApi('1')` only for SRV URIs (Atlas)
    - Embedding model via `OpenAIEmbedding(model=..., api_key=...)`
    - Vector store: `MongoDBAtlasVectorSearch(...)` with a provided collection
    - Builds a `VectorStoreIndex` and uses `add(nodes=docs)` to upsert
- Storage:
  - Uses MongoDB (Atlas or MongoDB 8 with Vector Search) rather than Qdrant
  - Maintains `document_vectors` collection with embedding vectors and metadata

### Connection Flow

- Backend ingestion:
  - Text chunking → batch embedding via OpenAI through LlamaIndex → upsert to MongoDB vector store (Atlas Vector Search or MongoDB 8 local)
  - Queries: `vector_store.similarity_search(...)` using computed query embedding

### Observations

- Installation missing: LlamaIndex packages are not in the active backend requirements used by Dockerfile; runtime failures are expected when invoking vector store features.
- Dual vector stack: Backend uses Mongo Vector Search; `docling` uses Qdrant. This divergence increases system complexity.
- Indexing: Vector Search index creation for MongoDB 8 is described in code comments but not automated.

---

## 2. Qdrant

### Installation

- Deployed as a service via docker-compose.yml:
  - Image: `qdrant/qdrant:latest`
  - Ports: 6333 (HTTP), 6334 (gRPC, set via env)
  - Secrets: API key provided via `secrets: [qdrant_api_key]`
  - Volumes: `qdrant_data` (storage), `qdrant_snapshots`
  - Healthcheck: `http://localhost:6333/healthz`
- Client library used in services/docling:
  - `qdrant-client[grpc]>=1.9.1` (services/docling/requirements.txt)

### Environment Variables

- Qdrant service:
  - Reads API key from secret: `QDRANT__SERVICE__API_KEY=/run/secrets/qdrant_api_key`
  - `QDRANT__SERVICE__GRPC_PORT=6334`
- Docling Qdrant client:
  - VECTORDB_URL (e.g., http://qdrant:6333)
  - VECTORDB_API_KEY
  - VECTORDB_COLLECTION (default "documents")
  - VECTORDB_BATCH_SIZE
  - EMBEDDING_MODEL (for SentenceTransformers)
- Graphiti service env includes GRAPHITI_QDRANT_URL and GRAPHITI_QDRANT_API_KEY, but code does not use them
- Note: VECTORDB_API_KEY must be present in the root .env; it is NOT automatically sourced from the Docker secret used by the Qdrant server.

### Import &amp; Initialization

- services/docling/ocr_service.py:
  - `QdrantClient(url=settings.vectordb_url, api_key=settings.vectordb_api_key)`
  - Ensures collection exists with appropriate vector size based on SentenceTransformer embedding dimension
  - Upserts vectors per processed document chunk:
    - `client.upsert(collection_name=..., points=[PointStruct(... vector=..., payload=metadata)])`

### Connection Flow

- Docling processing job:
  - OCR/parse → text → SentenceTransformer embeddings → upsert to Qdrant (collection from env) with metadata
- LangGraph/Graphiti:
  - Current code paths do not query Qdrant directly
- Backend:
  - Uses Mongo Vector Search for embeddings; no Qdrant usage

### Observations

- Qdrant cluster is healthy and volume-backed per docker-compose
- The docling service is correctly parameterized to push vectors to Qdrant
- Graphiti env includes Qdrant variables but Graphiti code does not use Qdrant (see section 4)

---

## 3. LangGraph

### Installation

- services/langgraph/requirements.txt includes:
  - langgraph>=0.0.52, fastapi, redis, httpx, loguru, tenacity, websockets, python-dotenv
- services/langgraph/Dockerfile installs and runs `uvicorn orchestrator:app --port 9000`

### Environment Variables

- Modeled in services/langgraph/orchestrator.py Settings:
  - LANGGRAPH_REDIS_URL (default redis://redis:6379/0)
  - LANGGRAPH_GRAPHITI_URL (default http://graphiti:8080)
  - LANGGRAPH_QDRANT_URL (default http://qdrant:6333) — defined but unused in current logic
  - LANGGRAPH_DOCLING_URL (default http://docling:8081)
  - LANGGRAPH_API_TOKEN (optional bearer for API protection)
  - LANGGRAPH_LOG_DIR (default /app/logs)
  - LANGGRAPH_INGEST_QUEUE (default workflow:ingest)
  - LANGGRAPH_WS_KEEPALIVE (default 15)
- docker-compose.yml wires these from .env and service hostnames

### Import &amp; Initialization

- Uses:
  - `from langgraph.graph import StateGraph, START, END`
  - `from langgraph.checkpoint.memory import MemorySaver`
- Graph nodes (ContractGraph):
  - ingest → analyze → compliance → risk → report → END
  - Interactions:
    - Docling: POST {docling}/jobs
    - Graphiti: POST {graphiti}/graph/query (for summary/compliance/risk)
  - State persisted in Redis (hash "workflow:state")
- API:
  - /workflows (start)
  - /workflows/{job_id} (status)
  - /agents/collections (proxies GET {graphiti}/vector/collections; note: Graphiti currently does not expose this route)
  - /ws/{job_id} WebSockets for state updates

### Connection Flow

- Client → LangGraph /workflows → orchestrator sequences:
  - Docling job triggers
  - Graphiti queries for analysis/compliance/risk
  - Reports written to LANGGRAPH_LOG_DIR
  - Redis used for state queryability

### Observations

- qdrant_url is configured but unused in orchestrator logic
- /agents/collections endpoint assumes Graphiti has a `/vector/collections` API which is not implemented in services/graphiti/app.py
- Production readiness improved via retries (tenacity), health endpoint, and structured logging

---

## 4. Graphiti

### Installation

- services/graphiti/requirements.txt includes:
  - graphiti-core[falkordb]>=0.4.0, redis, fastapi, openai, tenacity, loguru
- services/graphiti/Dockerfile installs and runs `uvicorn app:app --port 8080`

### Environment Variables

- services/graphiti/app.py Settings:
  - GRAPHITI_DB_URL (alias for falkordb_url) — e.g., redis://:${FALKORDB_PASSWORD}@falkordb:6379
  - GRAPHITI_OPENAI_API_KEY (optional; enables OpenAI summarization)
  - GRAPHITI_LOG_LEVEL
  - GRAPHITI_GRAPH_NAME (via Dockerfile default: contract_graph)
  - log_dir: /app/logs
  - default_limit: 25
- docker-compose.yml provides:
  - GRAPHITI_DB_URL, GRAPHITI_QDRANT_URL, GRAPHITI_QDRANT_API_KEY, GRAPHITI_OPENAI_API_KEY
  - NOTE: Only DB*URL and OPENAI_API_KEY are used in code. QDRANT*\* envs are currently unused.

### Import &amp; Initialization

- Uses RedisGraph (FalkorDB) as the graph database:
  - `self.redis = redis.from_url(url)`
  - `self.graph = Graph(graph_name, self.redis)`
- Optional Graphiti client (FalkorDB) via `graphiti_core.clients.falkordb` (best-effort)
- Provides endpoints:
  - /documents (upsert Document node)
  - /episodes (create Episode node and relate to Documents)
  - /search (body/title contains)
  - /relationships (create edge)
  - /temporal (time-window query)
  - /graph/query (raw Cypher)
- Optional OpenAI summarization for document upserts if GRAPHITI_OPENAI_API_KEY set

###
