# ContraClaim DMS Production Environment Setup

This document defines the production environment variables for the ContraClaim DMS backend and frontend. It is based on `backend/.env.development`, the root/backend example env files, and environment variables referenced by the backend and frontend source code.

Do not commit real production `.env` files or real secret values to Git.

## Backend Production Environment Variables

Create the backend production environment file on the server or deployment platform, usually as `backend/.env` for the current Docker Compose setup.

### Core Application and Server

| Key | Required | Production example | Must change before deployment | Notes |
| --- | --- | --- | --- | --- |
| `ENVIRONMENT` | Yes | `production` | Yes | Enables production validation and disables API docs in the active app. |
| `COMPOSE_PROJECT_NAME` | No | `contraclaim` | No | Docker Compose project name. |
| `APP_NAME` | No | `ContraClaim API` | No | Referenced by legacy config management module. |
| `APP_VERSION` | No | `2.0.0` | No | Referenced by legacy config management module. |
| `APP_DESCRIPTION` | No | `Contract Management API` | No | Referenced by legacy config management module. |
| `HOST` | No | `0.0.0.0` | No | Uvicorn/container bind host if used by process manager. |
| `PORT` | Yes | `8000` | No | Backend application port. |
| `BACKEND_PORT` | No | `8000` | No | Compose/runtime convenience alias. |
| `BACKEND_WORKERS` | No | `4` | Tune | Worker count for production process manager. |
| `WORKER_PROCESSES` | No | `2` | Tune | Referenced by config management module. |
| `WORKER_TIMEOUT` | No | `120` | Tune | Worker timeout in seconds. |
| `KEEPALIVE_TIMEOUT` | No | `2` | Tune | Keep-alive timeout in seconds. |
| `DEBUG` | No | `false` | Yes | Must be `false` in production. |
| `ENABLE_API_DOCS` | No | `false` | Recommended | Active app disables docs automatically when `ENVIRONMENT=production`; keep false. |
| `API_PREFIX` | No | `/api` | No | Referenced by config management module. |
| `DOCS_URL` | No |  | No | Leave empty or omit in production. |
| `REDOC_URL` | No |  | No | Leave empty or omit in production. |
| `OPENAPI_URL` | No |  | No | Leave empty or omit in production. |
| `PUBLIC_BASE_URL` | Yes | `https://web.contraclaim.com` | Yes | Public frontend base URL. |
| `PUBLIC_API_URL` | Yes | `https://app.contraclaim.com/api` | Yes | Public backend API URL used in email/share flows. |
| `APP_URL` | Yes | `https://web.contraclaim.com` | Yes | Frontend URL used in email links. |
| `BACKEND_PUBLIC_URL` | No | `https://app.contraclaim.com` | If used | Fallback public backend URL for email links. |
| `API_BASE_URL` | No | `https://app.contraclaim.com/api` | If used | Fallback public API URL for email links. |
| `DOCUMENT_SHARE_TOKEN_TTL_DAYS` | Yes | `30` | Policy decision | Share link token lifetime. |
| `SHARE_EMAIL_ATTACHMENT_MAX_MB` | Yes | `15` | Policy decision | Maximum attachment size for share emails. |

### Database and Runtime State

| Key | Required | Production example | Must change before deployment | Notes |
| --- | --- | --- | --- | --- |
| `DATABASE_URL` | Yes | `mongodb://mongo:27017/contraclaim?replicaSet=rs0` | Yes | Main MongoDB connection string. Do not use localhost in production unless running in the same container namespace intentionally. |
| `MONGODB_URI` | No | `mongodb://mongo:27017/contraclaim?replicaSet=rs0` | Yes | Alias used by scripts/document processing. Keep aligned with `DATABASE_URL`. |
| `MONGODB_URL` | Conditional | `mongodb://mongo:27017/contraclaim?replicaSet=rs0` | Yes | Required only if `routers/config_management.py` is enabled/imported. |
| `DATABASE_NAME` | Yes | `contraclaim` | No | Legacy database name alias used by some services/scripts. |
| `MONGODB_DATABASE` | Yes | `contraclaim` | No | Main database name used by `core/config.py`. |
| `MONGODB_APP_NAME` | No | `ContractDMS` | No | MongoDB app name. |
| `MONGODB_MAX_POOL_SIZE` | No | `100` | Tune | MongoDB max connection pool size. |
| `MONGODB_MIN_POOL_SIZE` | No | `1` | Tune | MongoDB min connection pool size. |
| `MONGODB_SERVER_SELECTION_TIMEOUT_MS` | No | `5000` | Tune | Server selection timeout. |
| `MONGODB_CONNECT_TIMEOUT_MS` | No | `5000` | Tune | Connect timeout. |
| `MONGODB_SOCKET_TIMEOUT_MS` | No | `20000` | Tune | Socket timeout. |
| `MONGODB_REPLICA_SET` | Yes | `rs0` | Yes | Production validation expects a replica set unless standalone production is explicitly allowed. |
| `MONGODB_RETRY_WRITES` | No | `true` | No | MongoDB retryable writes. |
| `MONGODB_ALLOW_STANDALONE_PRODUCTION` | Conditional | `false` | Yes | Keep `false`; only set `true` for an explicit, accepted standalone MongoDB production risk. |
| `LOCAL_MONGODB_URI` | No |  | No | Development/test override; normally omit in production. |
| `APP_REDIS_URL` | Yes | `redis://:<REDIS_PASSWORD>@redis:6379/1` | Yes | Required in production unless `RUNTIME_STATE_REDIS_URL` is set. |
| `RUNTIME_STATE_REDIS_URL` | Yes | `redis://:<REDIS_PASSWORD>@redis:6379/1` | Yes | Runtime state/session/cache Redis URL. At least this or `APP_REDIS_URL` is required. |
| `REDIS_URL` | No | `redis://:<REDIS_PASSWORD>@redis:6379/0` | Yes | General Redis URL if used outside runtime state. |
| `REDIS_PASSWORD` | Yes | `<strong-redis-password>` | Yes | Secret. Also consumed by Compose services. |

### Authentication, JWT, CORS, and Security

| Key | Required | Production example | Must change before deployment | Notes |
| --- | --- | --- | --- | --- |
| `SECRET_KEY` | Yes | `<64-plus-character-random-secret>` | Yes | Critical backend secret used for JWT/session crypto. |
| `JWT_SECRET` | Conditional | `<64-plus-character-random-secret>` | Yes | Legacy alias. Keep aligned with `SECRET_KEY` if legacy code is used. |
| `JWT_REFRESH_SECRET` | Conditional | `<64-plus-character-random-refresh-secret>` | Yes | Legacy refresh token secret. |
| `JWT_SECRET_KEY` | Conditional | `<64-plus-character-random-secret>` | Yes | Required only if `routers/config_management.py` is enabled/imported. |
| `ALGORITHM` | Yes | `HS256` | No | Active JWT algorithm setting. |
| `JWT_ALGORITHM` | Conditional | `HS256` | No | Legacy/config management JWT algorithm setting. |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Yes | `30` | Policy decision | Access token lifetime. |
| `JWT_EXPIRATION` | Conditional | `30` | Policy decision | Legacy expiration value in minutes. |
| `JWT_EXPIRATION_HOURS` | Conditional | `24` | Policy decision | Required only if config management module is enabled. |
| `CORS_ORIGINS` | Yes | `["https://web.contraclaim.com","https://app.contraclaim.com"]` | Yes | Do not include localhost or `127.0.0.1` in production. |
| `CORS_ALLOW_CREDENTIALS` | No | `true` | Review | Referenced by config management module. |
| `SECURITY_HEADERS_ENABLED` | No | `true` | No | Referenced by config management module. |
| `ALLOW_DEV_HEADERS` | No | `false` | Yes | Must be `false` in production. |
| `GLOBAL_RATE_LIMIT_REQUESTS` | No | `1000` | Tune | Referenced by config management module. |
| `GLOBAL_RATE_LIMIT_WINDOW` | No | `3600` | Tune | Seconds. |
| `USER_RATE_LIMIT_REQUESTS` | No | `100` | Tune | User-level request limit. |
| `USER_RATE_LIMIT_WINDOW` | No | `3600` | Tune | Seconds. |
| `MAX_FILE_SIZE` | No | `52428800` | Tune | Bytes; config management upload limit. |
| `MAX_FILES_PER_UPLOAD` | No | `10` | Tune | Config management upload limit. |
| `QUARANTINE_SUSPICIOUS_FILES` | No | `true` | No | Config management file safety option. |

### File Storage and Uploads

| Key | Required | Production example | Must change before deployment | Notes |
| --- | --- | --- | --- | --- |
| `UPLOADS_DIR` | Yes | `/var/www/app.contraclaim.com/backend/uploads` | Yes | Must be writable by the backend process. |
| `SECURE_UPLOADS_DIR` | Yes | `/var/www/app.contraclaim.com/backend/secure_uploads` | Yes | Preferred secure file storage path. |
| `TEMP_DIR` | No | `/var/www/app.contraclaim.com/backend/tmp` | Yes | Referenced by config management module. |
| `UPLOAD_STREAM_CHUNK_SIZE_MB` | No | `1` | Tune | Streaming upload chunk size. |
| `UPLOAD_VALIDATION_SAMPLE_BYTES` | No | `8192` | Tune | Bytes sampled during validation. |
| `GENERAL_UPLOAD_MAX_FILE_SIZE_MB` | No | `100` | Policy decision | General upload size limit. |
| `UPLOAD_MAX_CONCURRENT_PER_USER` | No | `3` | Tune | Upload concurrency limit. |
| `UPLOAD_MAX_CONCURRENT_PER_ORG` | No | `20` | Tune | Upload concurrency limit. |
| `BULK_UPLOAD_MAX_FILES` | No | `100` | Tune | Bulk upload limit from `core/config.py`. |
| `BULK_UPLOAD_MAX_SIZE_MB` | No | `500` | Tune | Bulk upload size limit. |
| `CONTRACT_UPLOAD_MAX_FILE_SIZE_MB` | No | `50` | Tune | Contract upload hard limit. |
| `CONTRACT_UPLOAD_MAX_CHUNK_SIZE_MB` | No | `5` | Tune | Contract upload chunk limit. |
| `CONTRACT_UPLOAD_SESSION_TTL_SECONDS` | No | `3600` | Tune | Contract upload session TTL. |
| `CONTRACT_UPLOAD_MAX_CONCURRENT_SESSIONS` | No | `10` | Tune | Contract upload concurrency. |
| `ALLOWED_DOCUMENT_MIMES` | Yes | `["application/pdf","image/png","image/jpeg","text/plain"]` | Policy decision | JSON array string. |
| `ALLOWED_ENCLOSURE_MIMES` | Yes | `["application/pdf","image/png","image/jpeg","text/plain"]` | Policy decision | JSON array string. |
| `ALLOWED_CONTRACT_MIMES` | No | `["application/pdf","application/vnd.openxmlformats-officedocument.wordprocessingml.document"]` | Policy decision | Referenced by `core/config.py` default. |
| `ENABLE_VIRUS_SCANNING` | No | `false` | Policy decision | Set true only after scanner integration is available. |
| `ENABLE_FILE_DEDUPLICATION` | No | `true` | No | Config management setting. |
| `AUTO_CLEANUP_TEMP_FILES` | No | `true` | No | Config management setting. |
| `TEMP_FILE_RETENTION_HOURS` | No | `24` | Tune | Temp cleanup retention. |

### AWS S3

| Key | Required | Production example | Must change before deployment | Notes |
| --- | --- | --- | --- | --- |
| `AWS_ACCESS_KEY_ID` | Yes | `<aws-access-key-id>` | Yes | Secret/credential. Prefer IAM role where possible. |
| `AWS_SECRET_ACCESS_KEY` | Yes | `<aws-secret-access-key>` | Yes | Secret. |
| `AWS_REGION` | Yes | `ap-south-1` | Confirm | Production bucket region. |
| `AWS_BUCKET_NAME` | Yes | `contraclaim-prod` | Yes | Production S3 bucket. |
| `S3_ENABLED` | No | `true` | Policy decision | Used by config management module. |
| `S3_PRESIGNED_URL_EXPIRY` | No | `1800` | Tune | Seconds. |

### AI, LLM, and Assistant Configuration

| Key | Required | Production example | Must change before deployment | Notes |
| --- | --- | --- | --- | --- |
| `OPENAI_API_KEY` | Yes | `<openai-api-key>` | Yes | Secret. Required by active config validation and AI services. |
| `OPENAI_MODEL` | No | `gpt-4o` | Review | General model setting from env reference. |
| `OPENAI_RESPONSES_MODEL` | No | `gpt-4.1` | Review | Responses/summaries model. |
| `OPENAI_MODEL_CHAT` | No | `gpt-4.1` | Review | Config management chat model. |
| `OPENAI_MODEL_EMBEDDING` | No | `text-embedding-3-small` | Review | Config management embedding model. |
| `OPENAI_MODEL_RESPONSES` | No | `gpt-4.1` | Review | Config management responses model. |
| `OPENAI_CALLS_PER_MINUTE` | No | `30` | Tune | Config management rate limit. |
| `OPENAI_CALLS_PER_DAY` | No | `1000` | Tune | Config management rate limit. |
| `OPENAI_TIMEOUT` | No | `60` | Tune | Seconds. |
| `OPENAI_EMBEDDING_MODEL` | No | `text-embedding-3-small` | Review | Read indirectly by document processing via settings. |
| `EMBED_MODEL` | No | `text-embedding-3-small` | Review | Used by `routers/rag_utils.py`. |
| `ASSISTANT_ID` | Conditional | `<openai-assistant-id>` | Yes if assistants are used | Secret-like provider identifier. |
| `SECONDARY_ASSISTANT_ID` | Conditional | `<openai-secondary-assistant-id>` | Yes if assistants are used | `core/config.py` aliases this into `ASSISTANT_ID1`. |
| `ASSISTANT_ID1` | No | `<openai-secondary-assistant-id>` | If legacy code uses it | Legacy alias; prefer `SECONDARY_ASSISTANT_ID`. |
| `XAI_API_KEY` | Conditional | `<xai-api-key>` | Yes if Grok/XAI is used | Secret. |
| `model_1` | No | `grok-4` | Review | Lowercase legacy key kept for compatibility. |
| `model` | No | `grok-4-1-fast` | Review | Lowercase legacy key kept for compatibility. |
| `PYDANTIC_AI_ENABLED` | No | `true` | Review | Enables PydanticAI integration. |
| `PYDANTIC_AI_MODEL` | No | `gpt-4o-mini` | Review | PydanticAI model. |
| `PYDANTIC_AI_PROVIDER` | No | `openai` | Review | PydanticAI provider. |
| `PYDANTIC_AI_BASE_URL` | No |  | If custom provider | Optional provider base URL. |
| `PYDANTIC_AI_TEMPERATURE` | No | `0.1` | Tune | Generation temperature. |
| `PYDANTIC_AI_MAX_RETRIES` | No | `2` | Tune | Retry count. |
| `ENABLE_CONTENT_FILTERING` | No | `true` | No | Config management AI setting. |
| `MAX_PROMPT_LENGTH` | No | `8000` | Tune | Config management AI setting. |
| `MAX_CONTEXT_LENGTH` | No | `5000` | Tune | Config management AI setting. |
| `SIMILARITY_THRESHOLD` | No | `0.3` | Tune | Config management retrieval threshold. |
| `MAX_SIMILAR_LETTERS` | No | `5` | Tune | Config management retrieval limit. |
| `EMBEDDING_CACHE_SIZE` | No | `1000` | Tune | Config management cache setting. |
| `EMBEDDING_CACHE_TTL` | No | `3600` | Tune | Seconds. |
| `SEARCH_CACHE_SIZE` | No | `500` | Tune | Config management cache setting. |
| `SEARCH_CACHE_TTL` | No | `1800` | Tune | Seconds. |

### LangGraph and Orchestration

| Key | Required | Production example | Must change before deployment | Notes |
| --- | --- | --- | --- | --- |
| `LANGGRAPH_ENABLED` | Yes | `true` | Review | If `true` in production, `LANGGRAPH_API_TOKEN` is required by validation. |
| `LANGGRAPH_API_TOKEN` | Conditional | `<strong-langgraph-api-token>` | Yes | Required when `LANGGRAPH_ENABLED=true` and `ENVIRONMENT=production`. |
| `LANGGRAPH_DEFAULT_AGENT` | No | `contract_drafter` | Review | Default agent name. |
| `LANGGRAPH_MODEL` | No | `gpt-4o` | Review | General LangGraph model. |
| `LANGGRAPH_DRAFTER_MODEL` | No | `gpt-4o` | Review | Drafting model from active config defaults. |
| `LANGGRAPH_REVIEWER_MODEL` | No | `gpt-4o-mini` | Review | Reviewer model. |
| `LANGGRAPH_PLAN_MODEL` | No | `grok-4-1-fast` | Review | Planning model. |
| `LANGGRAPH_TIMEOUT` | No | `90` | Tune | Seconds. |
| `LANGGRAPH_TRACE_STORE` | No |  | Optional | Trace store backend/path if enabled. |
| `LANGGRAPH_DRAFT_PROMPT_TEMPLATE` | No | `<prompt-template-text>` | Review | Optional prompt override. |
| `LANGGRAPH_PLAN_PROMPT_TEMPLATE` | No | `<prompt-template-text>` | Review | Optional prompt override. |

### Graphiti, Inngest, and LlamaIndex

| Key | Required | Production example | Must change before deployment | Notes |
| --- | --- | --- | --- | --- |
| `GRAPHITI_API_URL` | Conditional | `http://graphiti:8080` | Review | Required if Graphiti API integration is enabled. |
| `GRAPHITI_API_KEY` | Conditional | `<graphiti-api-key>` | Yes if used | Secret. |
| `GRAPHITI_WORKSPACE` | No | `ContraClaim` | Review | Workspace name. |
| `INNGEST_APP_ID` | No | `contractdms` | Review | Inngest app ID. |
| `INNGEST_EVENT_KEY` | Conditional | `<inngest-event-key>` | Yes if used | Secret. |
| `INNGEST_API_URL` | No | `https://api.inngest.com` | No | Inngest API URL. |
| `LLAMA_INDEX_ENABLED` | No | `true` | Review | Enables LlamaIndex integration. |
| `LLAMA_INDEX_STORAGE_DIR` | No | `/var/www/app.contraclaim.com/backend/storage/llama_index` | Yes | Writable storage path. |

### Qdrant and Vector Storage

| Key | Required | Production example | Must change before deployment | Notes |
| --- | --- | --- | --- | --- |
| `QDRANT_ENABLED` | No | `true` | Review | Enables Qdrant integration. |
| `QDRANT_URL` | Conditional | `http://qdrant:6333` | Yes | Required for vector search/Qdrant-backed retrieval. |
| `QDRANT_API_KEY` | Conditional | `<qdrant-api-key>` | Yes | Secret when Qdrant API key is enabled. |
| `QDRANT_COLLECTION` | No | `contracts` | Review | Qdrant collection. |
| `QDRANT_VECTOR_SIZE` | No | `1536` | Must match embedding model | Vector dimension. |
| `QDRANT_DISTANCE` | No | `Cosine` | Must match collection | Distance metric. |
| `QDRANT_TIMEOUT` | No | `15` | Tune | Seconds. |
| `QDRANT_VECTOR_NAME` | No |  | If named vectors are used | Optional named vector. |
| `VECTOR_STORE_ENABLED` | No | `true` | Review | Enables vector store behavior. |
| `VECTORDB_URL` | No | `http://qdrant:6333` | Yes if used | Older vector DB alias. |
| `VECTORDB_API_KEY` | No | `<qdrant-api-key>` | Yes if used | Older vector DB alias. |
| `VECTORDB_COLLECTION` | No | `documents` | Review | Older vector DB alias. |
| `VECTORDB_BATCH_SIZE` | No | `64` | Tune | Batch size. |
| `EMBEDDING_MODEL` | No | `sentence-transformers/all-MiniLM-L6-v2` | Review | Legacy embedding model key. |
| `DUAL_VECTOR_WRITE` | No | `true` | Review | Enables dual vector writes. |
| `VECTOR_DUAL_WRITE_ENABLED` | No | `true` | Review | Active config dual-write toggle. |
| `VECTOR_VERIFY_AFTER_WRITE` | No | `false` | Review | Active config verification toggle. |
| `AUTO_REFERENCE_SYNC` | No | `true` | Review | Reference sync behavior from dev env. |
| `REFERENCE_SYNC_TIMEOUT` | No | `60` | Tune | Seconds. |
| `VERIFY_DUAL_WRITE` | No | `true` | Review | Legacy consistency check toggle. |
| `SYNC_CHECK_INTERVAL` | No | `3600` | Tune | Seconds. |
| `MAX_PENDING_SYNCS` | No | `100` | Tune | Alert threshold. |

### FalkorDB and Contract Queue

| Key | Required | Production example | Must change before deployment | Notes |
| --- | --- | --- | --- | --- |
| `FALKORDB_ENABLED` | No | `true` | Review | Enables FalkorDB graph integration. |
| `FALKORDB_URL` | Conditional | `redis://:<FALKORDB_PASSWORD>@falkordb:6379` | Yes | Required if FalkorDB is enabled. |
| `FALKORDB_HOST` | Conditional | `falkordb` | Yes | Component form used by graph service. |
| `FALKORDB_PORT` | Conditional | `6379` | Yes | Container-internal port for Compose. |
| `FALKORDB_GRAPH_NAME` | No | `contraclaim` | Review | Graph name. |
| `FALKORDB_PASSWORD` | Conditional | `<falkordb-password>` | Yes if auth enabled | Secret. |
| `FALKORDB_CLEANUP_REFERENCES` | No | `true` | Review | Cleans old graph edges on update. |
| `FALKORDB_EDGE_SOURCE_FILTER` | No | `true` | Review | Legacy edge cleanup safety toggle. |
| `FALKORDB_INDEX_NAME` | No | `document_vectors` | Review | Graph vector index name. |
| `FALKORDB_VECTOR_DIM` | No | `1536` | Must match embedding model | Graph vector dimension. |
| `CONTRACT_QUEUE_ENABLED` | No | `true` | Review | Enables durable contract ingestion queue. |
| `CONTRACT_QUEUE_REDIS_URL` | Yes | `redis://:<REDIS_PASSWORD>@redis:6379/0` | Yes | Required for production queue workers. |
| `CONTRACT_QUEUE_NAME` | No | `contract_ingest_queue` | No | Queue name. |
| `CONTRACT_QUEUE_PROCESSING_NAME` | No | `contract_ingest_processing` | No | Processing queue name. |
| `CONTRACT_QUEUE_DEADLETTER_NAME` | No | `contract_ingest_deadletter` | No | Dead-letter queue name. |
| `CONTRACT_QUEUE_MAX_RETRIES` | No | `3` | Tune | Retry count. |
| `CONTRACT_QUEUE_WORKERS` | No | `1` | Tune | Worker count. |
| `START_BACKGROUND_SERVICES` | Yes | `true` | Deployment-specific | Backend should usually start background services; dedicated worker sets this false. |
| `START_CONTRACT_QUEUE_WORKERS` | Yes | `false` | Deployment-specific | Backend container should usually be false if a separate `contract-worker` runs. |

### Document Processing, OCR, Docling, and Marker

| Key | Required | Production example | Must change before deployment | Notes |
| --- | --- | --- | --- | --- |
| `DOCLING_ALLOWED_EXTENSIONS` | No | `.pdf,.png,.jpg,.jpeg,.tiff,.bmp,.docx,.pptx` | Policy decision | Allowed Docling file extensions. |
| `DOCLING_MAX_FILE_SIZE_MB` | No | `150` | Policy decision | Docling file size limit. |
| `DOCLING_LOG_LEVEL` | No | `INFO` | No | Docling log level. |
| `MARKER_ENABLED` | No | `true` | Review | Requires Marker CLI in runtime image/PATH. |
| `MARKER_CMD` | No | `marker` | Review | Marker command name/path. |
| `MARKER_OUTPUT_DIR` | No | `/var/www/app.contraclaim.com/backend/uploads/marker` | Yes | Writable output directory. |
| `CLAUSE_EXTRACTION_ENABLED` | No | `true` | Review | Used by document processing config. |
| `CLAUSE_EXTRACTION_MODEL` | No | `gpt-4o-mini` | Review | Clause extraction model. |
| `CLAUSE_EXTRACTION_MAX_CHARS` | No | `12000` | Tune | Max chars per extraction request. |
| `OCR_ENABLED` | No | `true` | Review | Config management OCR setting. |
| `OCR_LANGUAGE` | No | `eng` | Review | OCR language. |
| `OCR_TIMEOUT` | No | `300` | Tune | Seconds. |
| `OCR_DPI` | No | `300` | Tune | OCR DPI. |
| `OCR_PREPROCESS` | No | `true` | Review | OCR preprocessing. |
| `OCR_DESKEW` | No | `true` | Review | OCR deskew. |
| `OCR_DENOISE` | No | `true` | Review | OCR denoise. |

### SMTP and Email

| Key | Required | Production example | Must change before deployment | Notes |
| --- | --- | --- | --- | --- |
| `SMTP_HOST` | Yes | `smtp.gmail.com` | Confirm | SMTP server. |
| `SMTP_PORT` | Yes | `587` | Confirm | SMTP port. |
| `SMTP_USER` | Conditional | `<smtp-username>` | Yes | Email service checks `SMTP_USER` first. |
| `SMTP_USERNAME` | Yes | `<smtp-username>` | Yes | Critical active config field. |
| `SMTP_PASSWORD` | Yes | `<smtp-app-password>` | Yes | Secret. |
| `SMTP_FROM_EMAIL` | Yes | `noreply@contraclaim.com` | Yes | Sender email. |
| `SMTP_FROM_NAME` | No | `ContraClaim DMS` | Review | Sender display name. |
| `SMTP_ENCRYPTION` | No | `starttls` | Confirm | Used by SMTP settings service. |
| `SMTP_ENCRYPTION_TYPE` | No | `starttls` | Confirm | Legacy alias checked by SMTP settings service. |
| `SMTP_SETTINGS_ENCRYPTION_KEY` | Yes | `<32-byte-url-safe-encryption-key>` | Yes | Required to securely encrypt stored SMTP settings. |
| `FROM_EMAIL` | No | `noreply@contraclaim.com` | If legacy code uses it | Fallback alias. |
| `FROM_NAME` | No | `ContraClaim DMS` | If legacy code uses it | Fallback alias. |
| `EMAIL_HOST` | No | `smtp.gmail.com` | Confirm | Legacy email alias from env reference. |
| `EMAIL_PORT` | No | `587` | Confirm | Legacy email alias. |
| `EMAIL_USERNAME` | No | `<smtp-username>` | Yes if used | Legacy email alias. |
| `EMAIL_PASSWORD` | No | `<smtp-app-password>` | Yes if used | Secret legacy email alias. |
| `EMAIL_FROM` | No | `noreply@contraclaim.com` | Confirm | Legacy email alias. |

### Logging, Monitoring, and Observability

| Key | Required | Production example | Must change before deployment | Notes |
| --- | --- | --- | --- | --- |
| `LOG_LEVEL` | No | `INFO` | No | Use `DEBUG` only for short incident windows. |
| `LOG_FORMAT` | No | `structured` | No | Config management log format. |
| `LOG_TO_FILE` | No | `false` | Deployment-specific | Prefer container stdout/stderr unless file logging is required. |
| `LOG_FILE_PATH` | No | `/var/log/contraclaim/app.log` | If file logging | Writable path if `LOG_TO_FILE=true`. |
| `LOG_ROTATION_SIZE` | No | `100MB` | Tune | File logging rotation size. |
| `LOG_RETENTION_DAYS` | No | `30` | Tune | File logging retention. |
| `LOG_SENSITIVE_DATA` | No | `false` | Yes | Must remain `false` in production. |
| `MASK_SENSITIVE_FIELDS` | No | `true` | Yes | Must remain `true` in production. |
| `SENTRY_DSN` | No | `<sentry-dsn>` | If Sentry is used | Secret-like DSN. |
| `ENABLE_METRICS` | No | `true` | Review | Config management setting. |
| `METRICS_ENABLED` | Yes | `true` | Review | If true in production, `METRICS_TOKEN` is required by active validation. |
| `METRICS_TOKEN` | Conditional | `<strong-metrics-token>` | Yes | Required when `METRICS_ENABLED=true`. |
| `ENABLE_HEALTH_CHECKS` | No | `true` | No | Config management setting. |
| `OBSERVABILITY_STORE_RAW_QUERIES` | No | `false` | Yes | Keep false unless reviewed for privacy/compliance. |
| `SLOW_REQUEST_THRESHOLD_MS` | No | `2000` | Tune | Slow request logging threshold. |

## Frontend Production Environment Variables

Create the frontend production environment file as `client/.env.production` for local builds, or configure equivalent build-time variables in the deployment platform. Vite exposes only `VITE_` variables to browser code.

| Key | Required | Production example | Must change before deployment | Notes |
| --- | --- | --- | --- | --- |
| `VITE_API_BASE_URL` | Yes | `https://app.contraclaim.com/api` | Yes | Main FastAPI backend base URL. No trailing slash. Current code falls back to `/api`, but production should set this explicitly. |
| `VITE_LANGGRAPH_ENABLED` | No | `true` | Review | Feature flag used by `client/src/config/features.ts`. Missing from current frontend env examples. |
| `VITE_ENABLE_DEMO_HEADERS` | No | `false` | Yes | Documented in `client/.env.example`, but no active source usage found. Keep false/omit in production. |
| `VITE_DEV_SERVER_PORT` | No | `5173` | No | Informational only; not required for production builds. |

Runtime alternative: `window.__API_BASE_URL__` can override the API base URL at runtime if injected by hosting infrastructure, but it is not a `.env` key.

## Missing Values Found

### Missing or Blank in `backend/.env.development`

These keys are either blank in `backend/.env.development` or referenced by production validation/code but not present there. Add production values only where the feature is enabled.

| Key | Status in `backend/.env.development` | Production action |
| --- | --- | --- |
| `APP_REDIS_URL` | Blank | Set to production Redis runtime-state URL, or set `RUNTIME_STATE_REDIS_URL`. |
| `RUNTIME_STATE_REDIS_URL` | Blank | Set to production Redis runtime-state URL, or set `APP_REDIS_URL`. |
| `CONTRACT_QUEUE_REDIS_URL` | Blank | Set to production Redis queue URL if queue workers are enabled. |
| `REDIS_PASSWORD` | Blank | Set a strong Redis password if Redis auth is enabled. |
| `FALKORDB_PASSWORD` | Blank | Set if FalkorDB auth is enabled. |
| `LANGGRAPH_API_TOKEN` | Blank | Required when `LANGGRAPH_ENABLED=true` in production. |
| `METRICS_TOKEN` | Blank | Required when `METRICS_ENABLED=true` in production. |
| `MONGODB_REPLICA_SET` | Blank | Set replica set name, or include `replicaSet=` in `DATABASE_URL`. |
| `SMTP_SETTINGS_ENCRYPTION_KEY` | Blank | Set a strong encryption key before storing SMTP settings. |
| `SENTRY_DSN` | Blank | Set only if Sentry is used. |
| `ASSISTANT_ID1` | Blank | Prefer `SECONDARY_ASSISTANT_ID`; set only if legacy code expects `ASSISTANT_ID1`. |
| `ALLOW_DEV_HEADERS` | Missing | Add as `false` for production. |
| `ENABLE_API_DOCS` | Missing | Add as `false` for explicit production hardening. |
| `LOCAL_MONGODB_URI` | Missing | Usually omit in production. |
| `BULK_UPLOAD_MAX_FILES` | Missing | Add if overriding active default. |
| `BULK_UPLOAD_MAX_SIZE_MB` | Missing | Add if overriding active default. |
| `CONTRACT_QUEUE_ENABLED` | Missing | Add if controlling queue enablement explicitly. |
| `CONTRACT_QUEUE_NAME` | Missing | Add if overriding queue name. |
| `CONTRACT_QUEUE_PROCESSING_NAME` | Missing | Add if overriding processing queue name. |
| `CONTRACT_QUEUE_DEADLETTER_NAME` | Missing | Add if overriding dead-letter queue name. |
| `CONTRACT_QUEUE_MAX_RETRIES` | Missing | Add if overriding retry count. |
| `CONTRACT_QUEUE_WORKERS` | Missing | Add if overriding worker count. |
| `CONTRACT_UPLOAD_MAX_FILE_SIZE_MB` | Missing | Add if overriding active default. |
| `CONTRACT_UPLOAD_MAX_CHUNK_SIZE_MB` | Missing | Add if overriding active default. |
| `CONTRACT_UPLOAD_SESSION_TTL_SECONDS` | Missing | Add if overriding active default. |
| `CONTRACT_UPLOAD_MAX_CONCURRENT_SESSIONS` | Missing | Add if overriding active default. |
| `VECTOR_VERIFY_AFTER_WRITE` | Missing | Add if write verification is required. |
| `FALKORDB_INDEX_NAME` | Missing | Add if overriding graph vector index name. |
| `FALKORDB_VECTOR_DIM` | Missing | Add if overriding graph vector dimension. |
| `QDRANT_VECTOR_NAME` | Missing | Add only if using named Qdrant vectors. |
| `CLAUSE_EXTRACTION_ENABLED` | Missing | Add if controlling clause extraction explicitly. |
| `CLAUSE_EXTRACTION_MODEL` | Missing | Add if clause extraction is enabled. |
| `CLAUSE_EXTRACTION_MAX_CHARS` | Missing | Add if overriding clause extraction size. |
| `FROM_EMAIL` | Missing | Optional alias; add if legacy email code needs it. |
| `FROM_NAME` | Missing | Optional alias; add if legacy email code needs it. |
| `BACKEND_PUBLIC_URL` | Missing | Optional email fallback; add if not using `PUBLIC_API_URL`. |
| `API_BASE_URL` | Missing | Optional email fallback; add if not using `PUBLIC_API_URL`. |
| `EMBED_MODEL` | Missing | Add if `routers/rag_utils.py` should use a non-default embedding model. |
| `OPENAI_EMBEDDING_MODEL` | Missing | Add if document processing should use a non-default embedding model. |

The following keys are referenced by `routers/config_management.py` but that router is not included in the active FastAPI app in `backend/rbac_backend/main.py`. Add them only if that module is enabled/imported in production: `JWT_SECRET_KEY`, `JWT_EXPIRATION_HOURS`, `MONGODB_URL`, `DB_MIN_CONNECTIONS`, `DB_MAX_CONNECTIONS`, `DB_CONNECTION_TIMEOUT`, `DB_DEFAULT_PAGE_SIZE`, `DB_MAX_PAGE_SIZE`, `DB_QUERY_TIMEOUT`, `TEMP_DIR`, `S3_ENABLED`, `S3_PRESIGNED_URL_EXPIRY`, `ENABLE_VIRUS_SCANNING`, `ENABLE_FILE_DEDUPLICATION`, `AUTO_CLEANUP_TEMP_FILES`, `TEMP_FILE_RETENTION_HOURS`, `OPENAI_MODEL_CHAT`, `OPENAI_MODEL_EMBEDDING`, `OPENAI_MODEL_RESPONSES`, `OPENAI_CALLS_PER_MINUTE`, `OPENAI_CALLS_PER_DAY`, `OPENAI_TIMEOUT`, `SIMILARITY_THRESHOLD`, `MAX_SIMILAR_LETTERS`, `EMBEDDING_CACHE_SIZE`, `EMBEDDING_CACHE_TTL`, `SEARCH_CACHE_SIZE`, `SEARCH_CACHE_TTL`, `ENABLE_CONTENT_FILTERING`, `MAX_PROMPT_LENGTH`, `MAX_CONTEXT_LENGTH`, `OCR_ENABLED`, `OCR_LANGUAGE`, `OCR_TIMEOUT`, `OCR_DPI`, `OCR_PREPROCESS`, `OCR_DESKEW`, `OCR_DENOISE`, `LOG_FORMAT`, `LOG_TO_FILE`, `LOG_FILE_PATH`, `LOG_ROTATION_SIZE`, `LOG_RETENTION_DAYS`, `LOG_SENSITIVE_DATA`, `MASK_SENSITIVE_FIELDS`, `APP_NAME`, `APP_VERSION`, `APP_DESCRIPTION`, `HOST`, `DEBUG`, `API_PREFIX`, `DOCS_URL`, `REDOC_URL`, `OPENAPI_URL`, `ENABLE_METRICS`, `ENABLE_HEALTH_CHECKS`, `WORKER_PROCESSES`, `WORKER_TIMEOUT`, and `KEEPALIVE_TIMEOUT`.

### Frontend Variables Not Fully Documented Yet

| Key | Finding | Production action |
| --- | --- | --- |
| `VITE_LANGGRAPH_ENABLED` | Used in `client/src/config/features.ts` but missing from `client/.env.example` and `client/.env.production`. | Add explicitly as `true` or `false`. |
| `VITE_ENABLE_DEMO_HEADERS` | Present as a commented example, but no active code usage was found. | Keep false/omit in production unless code is added for it. |

## Security Notes

- Real production `.env` files must not be committed to Git.
- Store secrets in the production server environment, Docker/Compose secrets, CI/CD secret manager, or the deployment platform's encrypted environment variable store.
- Rotate any secret that has ever been committed, shared in chat, included in logs, or exposed on a developer machine.
- Use separate development, staging, and production credentials for MongoDB, Redis, Qdrant, S3, SMTP, OpenAI, Graphiti, Inngest, LangGraph, and monitoring.
- Keep `DEBUG=false`, `ALLOW_DEV_HEADERS=false`, `LOG_SENSITIVE_DATA=false`, and `MASK_SENSITIVE_FIELDS=true` in production.
- Do not include localhost origins in production `CORS_ORIGINS`.
- Prefer private container network hostnames such as `mongo`, `redis`, `qdrant`, and `falkordb` for service-to-service URLs in Docker Compose production deployments.
- Keep uploaded files, temporary files, logs, vector data, and database volumes outside Git-tracked paths.

Recommended `.gitignore` entries:

```gitignore
# Environment and secrets
.env
.env.*
!.env.example
!**/.env.example
secrets/
**/secrets/
config/secrets/
*.pem
*.key
*.crt
*.p12
*.pfx
*.token

# Local/generated runtime data
uploads/
**/uploads/
storage/
**/storage/
data/
qdrant_data/
logs/
**/logs/
tmp/
temp/
**/tmp/
**/temp/
```

The repository root `.gitignore` already contains the most important environment and secret exclusions. Keep those rules in place.
