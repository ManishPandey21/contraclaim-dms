# Graph Report (focused: document upload/ingestion code, AST-only, 95 files) - C:\SaaS\projectDMS  (2026-09-27)

## Corpus Check
- 95 files · ~0 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 2100 nodes · 4699 edges · 98 communities (87 shown, 11 thin omitted)
- Extraction: 100% EXTRACTED · 0% INFERRED · 0% AMBIGUOUS · INFERRED: 20 edges (avg confidence: 0.77)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Letter API router|Letter API router]]
- [[_COMMUNITY_Extraction quality gate checks|Extraction quality gate checks]]
- [[_COMMUNITY_Letter service|Letter service]]
- [[_COMMUNITY_Graph ingestion (Falkor adapter)|Graph ingestion (Falkor adapter)]]
- [[_COMMUNITY_Contract clause index + embeddings|Contract clause index + embeddings]]
- [[_COMMUNITY_Evidence ladder / quality verdict|Evidence ladder / quality verdict]]
- [[_COMMUNITY_Publication authority policy|Publication authority policy]]
- [[_COMMUNITY_Background job processor|Background job processor]]
- [[_COMMUNITY_FalkorDB letter graph|FalkorDB letter graph]]
- [[_COMMUNITY_Contract Master API|Contract Master API]]
- [[_COMMUNITY_Bulk upload service|Bulk upload service]]
- [[_COMMUNITY_Contract appraisal API|Contract appraisal API]]
- [[_COMMUNITY_Clause chunking agent|Clause chunking agent]]
- [[_COMMUNITY_Secure file storage|Secure file storage]]
- [[_COMMUNITY_Contract ingest queue worker|Contract ingest queue worker]]
- [[_COMMUNITY_Extraction engine + page classifier|Extraction engine + page classifier]]
- [[_COMMUNITY_Document service (authz/publication)|Document service (authz/publication)]]
- [[_COMMUNITY_Document references/enclosures API|Document references/enclosures API]]
- [[_COMMUNITY_Clause storage service|Clause storage service]]
- [[_COMMUNITY_Page stores (document/contract)|Page stores (document/contract)]]
- [[_COMMUNITY_Reference sync service|Reference sync service]]
- [[_COMMUNITY_Retrieval service|Retrieval service]]
- [[_COMMUNITY_OCR service (pagewise)|OCR service (pagewise)]]
- [[_COMMUNITY_Duplicate detection|Duplicate detection]]
- [[_COMMUNITY_Contract upload streaming|Contract upload streaming]]
- [[_COMMUNITY_Metadata processor|Metadata processor]]
- [[_COMMUNITY_Contract ingestor|Contract ingestor]]
- [[_COMMUNITY_Document processor + fallback ladder|Document processor + fallback ladder]]
- [[_COMMUNITY_Reference parsing + pipeline routing|Reference parsing + pipeline routing]]
- [[_COMMUNITY_BOQ/subitem detection|BOQ/subitem detection]]
- [[_COMMUNITY_Contract promotion|Contract promotion]]
- [[_COMMUNITY_Document delete/update cascade|Document delete/update cascade]]
- [[_COMMUNITY_Document processing jobs|Document processing jobs]]
- [[_COMMUNITY_Contract ingest LLM generator|Contract ingest LLM generator]]
- [[_COMMUNITY_Chunk enrichment + embeddings|Chunk enrichment + embeddings]]
- [[_COMMUNITY_Contract upload scope|Contract upload scope]]
- [[_COMMUNITY_Document service errors/refs|Document service errors/refs]]
- [[_COMMUNITY_Upload durability + reprojection|Upload durability + reprojection]]
- [[_COMMUNITY_Qdrant vector client|Qdrant vector client]]
- [[_COMMUNITY_Document list/summary API|Document list/summary API]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Community 50|Community 50]]
- [[_COMMUNITY_Community 51|Community 51]]
- [[_COMMUNITY_Community 52|Community 52]]
- [[_COMMUNITY_Community 53|Community 53]]
- [[_COMMUNITY_Community 54|Community 54]]
- [[_COMMUNITY_Community 55|Community 55]]
- [[_COMMUNITY_Community 56|Community 56]]
- [[_COMMUNITY_Community 57|Community 57]]
- [[_COMMUNITY_Community 58|Community 58]]
- [[_COMMUNITY_Community 59|Community 59]]
- [[_COMMUNITY_Community 60|Community 60]]
- [[_COMMUNITY_Community 61|Community 61]]
- [[_COMMUNITY_Community 62|Community 62]]
- [[_COMMUNITY_Community 63|Community 63]]
- [[_COMMUNITY_Community 64|Community 64]]
- [[_COMMUNITY_Community 65|Community 65]]
- [[_COMMUNITY_Community 66|Community 66]]
- [[_COMMUNITY_Community 67|Community 67]]
- [[_COMMUNITY_Community 68|Community 68]]
- [[_COMMUNITY_Community 69|Community 69]]
- [[_COMMUNITY_Community 70|Community 70]]
- [[_COMMUNITY_Community 71|Community 71]]
- [[_COMMUNITY_Community 72|Community 72]]
- [[_COMMUNITY_Community 73|Community 73]]
- [[_COMMUNITY_Community 74|Community 74]]
- [[_COMMUNITY_Community 75|Community 75]]
- [[_COMMUNITY_Community 76|Community 76]]
- [[_COMMUNITY_Community 77|Community 77]]
- [[_COMMUNITY_Community 78|Community 78]]
- [[_COMMUNITY_Community 79|Community 79]]
- [[_COMMUNITY_Community 80|Community 80]]
- [[_COMMUNITY_Community 81|Community 81]]
- [[_COMMUNITY_Community 82|Community 82]]
- [[_COMMUNITY_Community 83|Community 83]]
- [[_COMMUNITY_Community 84|Community 84]]
- [[_COMMUNITY_Community 85|Community 85]]
- [[_COMMUNITY_Community 86|Community 86]]
- [[_COMMUNITY_Community 87|Community 87]]
- [[_COMMUNITY_Community 88|Community 88]]
- [[_COMMUNITY_Community 89|Community 89]]
- [[_COMMUNITY_Community 90|Community 90]]
- [[_COMMUNITY_Community 91|Community 91]]
- [[_COMMUNITY_Community 92|Community 92]]
- [[_COMMUNITY_Community 93|Community 93]]
- [[_COMMUNITY_Community 94|Community 94]]
- [[_COMMUNITY_Community 95|Community 95]]
- [[_COMMUNITY_Community 96|Community 96]]
- [[_COMMUNITY_Community 97|Community 97]]

## God Nodes (most connected - your core abstractions)
1. `DocumentService` - 72 edges
2. `DocumentController` - 50 edges
3. `RetrievalService` - 44 edges
4. `LetterService` - 34 edges
5. `LetterController` - 31 edges
6. `FalkorGraphService` - 31 edges
7. `ContractIngestor` - 28 edges
8. `normalize_letter_code()` - 28 edges
9. `GraphIngestionService` - 27 edges
10. `Document` - 27 edges

## Surprising Connections (you probably didn't know these)
- `upload_contracts_multipart()` --references--> `UploadFile`  [EXTRACTED]
  backend/rbac_backend/routers/contracts.py → client/src/pages/UploadPage.tsx
- `upload_contract_chunk()` --references--> `UploadFile`  [EXTRACTED]
  backend/rbac_backend/routers/contracts.py → client/src/pages/UploadPage.tsx
- `controller_get_document()` --references--> `Document`  [EXTRACTED]
  backend/rbac_backend/routers/documents.py → client/src/pages/DocumentsPage.tsx
- `controller_update_document()` --references--> `Document`  [EXTRACTED]
  backend/rbac_backend/routers/documents.py → client/src/pages/DocumentsPage.tsx
- `controller_add_enclosure()` --references--> `UploadFile`  [EXTRACTED]
  backend/rbac_backend/routers/documents.py → client/src/pages/UploadPage.tsx

## Import Cycles
- None detected.

## Communities (98 total, 11 thin omitted)

### Letter API router - "Letter API router"
Cohesion: 0.05
Nodes (67): approve_letter(), assign_contract_drafter(), AssignDrafterRequest, complete_letter(), ContextDocumentUpdateRequest, create_letter(), _enforce_letter_separation_of_duties(), _enforce_letter_transition() (+59 more)

### Extraction quality gate checks - "Extraction quality gate checks"
Cohesion: 0.06
Nodes (61): ColumnRole, is_checkable(), map_column_roles(), _normalize(), Map table headers onto the roles a numeric check needs.  This is the prerequisit, Return one role per header cell, UNKNOWN where nothing matched., True only when a row-level qty x rate = amount check is meaningful.      Require, check_date_column() (+53 more)

### Letter service - "Letter service"
Cohesion: 0.07
Nodes (40): create_letter_service(), InvalidLetterIdError, InvalidLetterTransitionError, LetterNotFoundError, LetterService, LetterServiceError, Any, Database (+32 more)

### Graph ingestion (Falkor adapter) - "Graph ingestion (Falkor adapter)"
Cohesion: 0.08
Nodes (26): GraphAdapter, GraphAdapterError, GraphConfig, GraphEdge, GraphNode, GraphQueryResult, Any, Graph adapter abstraction for document relationship graph. (+18 more)

### Contract clause index + embeddings - "Contract clause index + embeddings"
Cohesion: 0.07
Nodes (44): ClauseMergeRequest, ClauseSplitRequest, ClauseUpdateRequest, get_clause_index_service(), get_contract_service(), list_document_clauses(), merge_clauses(), ContractService (+36 more)

### Evidence ladder / quality verdict - "Evidence ladder / quality verdict"
Cohesion: 0.08
Nodes (38): assemble_evidence(), Path, Assemble the minimum material that can answer the question.  Region-preferred: a, Build the evidence packet for one page or region., classify_corroboration(), _evidence_literals(), _evidence_tokens(), ExtractionFallbackLadder (+30 more)

### Publication authority policy - "Publication authority policy"
Cohesion: 0.08
Nodes (50): Repairs divergence between Mongo chunk records and Qdrant vectors using determin, VectorReconciler, _as_mapping(), authoritative_summary(), authoritative_text(), AuthorityDecision, blocked_document_ids(), _collection() (+42 more)

### Background job processor - "Background job processor"
Cohesion: 0.06
Nodes (37): BackgroundJobProcessor, cancel_background_job(), example_async_job(), example_sync_job(), get_background_processor(), get_background_services_info(), get_background_stats(), get_job_details() (+29 more)

### FalkorDB letter graph - "FalkorDB letter graph"
Cohesion: 0.09
Nodes (23): FalkorGraphError, FalkorGraphService, Any, Redis, Ensure required schema constraints and indexes exist., Create/Update a letter node and its reference edges.          `owner_document_id, Raised when FalkorDB operations fail., Delete a letter node and every edge attached to it (DETACH DELETE).          Mir (+15 more)

### Contract Master API - "Contract Master API"
Cohesion: 0.08
Nodes (39): adjudicate_candidate(), AdjudicationCommand, browse_catalogue(), CapabilityResponse, CatalogueItem, CatalogueResponse, claim_candidate(), ClassificationCommand (+31 more)

### Bulk upload service - "Bulk upload service"
Cohesion: 0.09
Nodes (22): BulkUploadService, Any, BulkUploadStatus, DocumentProcessingResult, Read CSV from FastAPI UploadFile with encoding detection, Clean the dataframe of problematic characters, Normalize CSV headers to the canonical field names used by single-upload., Persist UploadFile streams to temporary files so they remain readable after the (+14 more)

### Contract appraisal API - "Contract appraisal API"
Cohesion: 0.15
Nodes (37): add_review_comment(), approve_appraisal(), cancel_appraisal_job(), create_registers(), delete_appraisal(), edit_appraisal(), existing_appraisal(), export_appraisal_docx() (+29 more)

### Clause chunking agent - "Clause chunking agent"
Cohesion: 0.12
Nodes (20): ClauseChunkingAgent, ClauseProcessingSummary, DetectedClause, DetectedTable, DocumentScope, Any, Contract Clause Chunking Agent orchestrator (Phase 2).  Ties the pipeline togeth, Paragraph-aware split of a long clause into parts <= max_len. (+12 more)

### Secure file storage - "Secure file storage"
Cohesion: 0.10
Nodes (18): get_file_service(), FileService, DocumentProcessingConfig, Path, Persist uploaded document bytes to a secure location and return the absolute pat, Service for file system operations, Compress content based on file type., Persist an UploadFile to the secure uploads directory. (+10 more)

### Contract ingest queue worker - "Contract ingest queue worker"
Cohesion: 0.13
Nodes (11): ContractIngestQueue, get_contract_ingest_queue(), _parse_utc_timestamp(), Any, datetime, Redis, Recover only genuinely stale jobs from the processing list.          Older code, start_contract_ingest_queue() (+3 more)

### Extraction engine + page classifier - "Extraction engine + page classifier"
Cohesion: 0.14
Nodes (26): Page-level extraction: decide, batch, OCR, merge - once, for every caller.  This, Corroboration, FallbackOutcome, extract_image(), extract_text_file(), _image_size(), Path, Extractors for standalone images and plain-text uploads.  Images are OCR'd direc (+18 more)

### Document service (authz/publication) - "Document service (authz/publication)"
Cohesion: 0.10
Nodes (16): DocumentService, Any, DocumentUpdate, Async service for document CRUD operations with proper error handling, Move crashed/stuck Mongo-backed document jobs back to retrying.          The d, Merge extracted fields with the current canonical authority axes., Remove this document's graph node only if nothing else supports it.          T, Persist what this extraction attempt resolved and what it still owes. (+8 more)

### Document references/enclosures API - "Document references/enclosures API"
Cohesion: 0.10
Nodes (33): add_document_comment(), add_document_reference(), controller_add_reference(), controller_list_enclosures(), controller_list_linked_documents(), controller_list_references(), controller_remove_enclosure(), controller_remove_reference() (+25 more)

### Clause storage service - "Clause storage service"
Cohesion: 0.11
Nodes (16): ClauseStorageService, Any, ContractClause, Full guardrail for a clause-processing run: scope + all required perms (req 4)., Ancestor path from root to this clause.          - "8.4"        -> ["8", "8.4"], Deterministic per-clause-part identity for idempotent upserts.          Section, Validation rules (req 11 table): map inputs to quality/authorisation flags., Build one fully-scoped, hierarchy-aware clause record. (+8 more)

### Page stores (document/contract) - "Page stores (document/contract)"
Cohesion: 0.09
Nodes (14): ContractPageStore, Any, PageStore adapter for the contract ingestion path.  Everything contract-specific, Build the contract_ocr_pages record for one page.      cleaned_text is intention, to_contract_page_record(), DocumentPageStore, Any, PageStore adapter for general documents and enclosures.  Mirrors ContractPageSto (+6 more)

### Reference sync service - "Reference sync service"
Cohesion: 0.17
Nodes (15): Any, Database, ObjectId, Store unresolved references for later inspection or offline processing., Keeps document reference relationships consistent across MongoDB collections., Resolve deferred references whose targets have since been ingested.          Wit, Ensure forward (`references`) and backward (`referencedBy`) links are in sync., Return a stable identity for queue and per-run de-duplication. (+7 more)

### Retrieval service - "Retrieval service"
Cohesion: 0.14
Nodes (8): Any, Lightweight critique: ask LLM to identify gaps/contradictions and suggest refine, Ids whose document EXISTS and is currently non-consumable.          Deliberate, Ids whose canonical document actually exists in Mongo.          Used only wher, Iterative contract QA loop:         - build initial queries with clause/topic h, Search the structured clause vectors (contract_clauses namespace).          Fi, RetrievalService, EvidenceLedgerEntry

### OCR service (pagewise) - "OCR service (pagewise)"
Cohesion: 0.11
Nodes (17): PageExtractionPolicy, DocumentProcessingError, OCRService, Any, DocumentProcessingConfig, Path, Process PDF with OCR if needed and return processed file path and raw OCR text., Custom exception for document processing errors (+9 more)

### Duplicate detection - "Duplicate detection"
Cohesion: 0.16
Nodes (17): content_similarity(), _date_key(), DuplicateDetectionService, _normalize_text(), Any, Database, Detects duplicate uploads before and after OCR/metadata extraction., Classify an incoming upload before any record is created.          Both checks a (+9 more)

### Contract upload streaming - "Contract upload streaming"
Cohesion: 0.17
Nodes (22): _contract_limits(), _enqueue_or_start_contract_ingest(), _preprocess_pdf_with_ocr(), Any, Path, StorageSettingsService, Contract upload, listing, search, and download routes., _resolve_uploads_dir() (+14 more)

### Metadata processor - "Metadata processor"
Cohesion: 0.11
Nodes (15): DocumentProcessorError, Alias for document processor errors, DocumentProcessingError, MetadataProcessorService, Any, DocumentProcessingConfig, Process multiple documents concurrently with controlled concurrency., Extract metadata from PDF without full document processing.                  Arg (+7 more)

### Contract ingestor - "Contract ingestor"
Cohesion: 0.13
Nodes (13): ClauseSpan, ContractIngestor, IngestionError, Insert document vector records, Main service for contract document ingestion, Clause span extracted from Markdown with offsets., Ingest a contract file with proper error handling and logging.         Uses int, Custom exception for ingestion errors (+5 more)

### Document processor + fallback ladder - "Document processor + fallback ladder"
Cohesion: 0.13
Nodes (15): DocumentProcessor, LegacyDispatchResult, Any, Path, Decide whether this document gets a fallback ladder.          This is the prod, Adopt dual-confirmed repairs into the page text, then re-verify.          Thre, Assess every page, escalating only what the gate says is wrong.          Retur, The pre-Phase-3 extraction path, retained for canary rollback.          Delibe (+7 more)

### Reference parsing + pipeline routing - "Reference parsing + pipeline routing"
Cohesion: 0.12
Nodes (20): _is_read_only_cypher(), True when the Cypher contains no write clause (safe for GRAPH.RO_QUERY)., claim_filter_for(), pipeline_version_of(), Any, Which extraction pipeline a document job belongs to.  The decision is made once,, Decide the pipeline for a job being created., Read a job's pipeline from what was persisted, not from config. (+12 more)

### BOQ/subitem detection - "BOQ/subitem detection"
Cohesion: 0.14
Nodes (20): BOQRow, BOQTable, _columns_from_header(), detect_boq_tables(), is_boq_header(), parse_boq_row(), Detect and structure Bill of Quantities (BOQ) tables from cleaned page text.  Un, Find BOQ tables across pages.      A table is a header line followed by BOQ rows (+12 more)

### Contract promotion - "Contract promotion"
Cohesion: 0.16
Nodes (15): _contract_document_id(), ContractPromotionService, NotPromotable, _now(), PromotionReceipt, Any, datetime, Promotion: one adjudicated candidate becomes one authoritative instrument.  All- (+7 more)

### Document delete/update cascade - "Document delete/update cascade"
Cohesion: 0.09
Nodes (14): create_document_service(), DocumentConflictError, DocumentDeletionResult, DocumentDependencyError, Database, NotificationService, Update an existing document.                  Args:             document_id:, Delete a document by ID and cascade-clean everything linked to it.          Af (+6 more)

### Document processing jobs - "Document processing jobs"
Cohesion: 0.14
Nodes (11): Enclosure, EnclosureResponse, Run a durable document-processing job and update terminal state., Requeue a job that resolved some pages but still owes others., Purge artifacts an EARLIER successful run published for this document., Terminal, non-success state: automated recovery cannot finish this., Terminal, non-success: stored deliberately, never extracted., Background worker entrypoint for OCR/metadata processing. (+3 more)

### Contract ingest LLM generator - "Contract ingest LLM generator"
Cohesion: 0.11
Nodes (15): AIOutputGuardrailService, LLMGenerator, DocumentProcessingConfig, Small wrapper for answer drafting with an offline fallback., True when a live LLM client is configured; False in offline fallback mode., AsyncIOMotorDatabase, ObservabilityService, create_contract_ingestor() (+7 more)

### Chunk enrichment + embeddings - "Chunk enrichment + embeddings"
Cohesion: 0.12
Nodes (11): ChunkEnricher, Contextual enrichment using neighborhood and semantic proximity., AsyncIOMotorDatabase, ObservabilityService, EmbeddingClient, DocumentProcessingConfig, Wrapper around OpenAI embeddings with a deterministic fallback., AsyncIOMotorDatabase (+3 more)

### Contract upload scope - "Contract upload scope"
Cohesion: 0.13
Nodes (16): ContractUploadScopeService, Any, Upload scope: authorised by capability, recorded as a candidate.  Two things thi, Check capabilities at the resource scope named by the request., Record the scope decision against a canonical Document.          ``selected_cont, One scope decision, applied identically to every file in the batch., The actor may not create a document at the requested scope., The named project does not exist in this organisation.      Raised rather than d (+8 more)

### Document service errors/refs - "Document service errors/refs"
Cohesion: 0.16
Nodes (12): DocumentNotFoundError, DocumentServiceError, InvalidDocumentIdError, ReferenceCreate, Refresh the derived FalkorDB graph from the latest Mongo document., Return comments for the given document as a list of dicts:         [{ id, text,, Add a comment to a document. Stores in 'document_comments' collection., Normalise raw Mongo records into API response dictionaries. (+4 more)

### Upload durability + reprojection - "Upload durability + reprojection"
Cohesion: 0.12
Nodes (16): This worker's generation is no longer the current one.      Raised rather than r, StaleWorkerGeneration, Any, Upload scope durability, retry conflict, and post-promotion immutability.  Three, Apply a generic edit, refusing any attempt on frozen scope fields., A retry named a different scope than the original upload., A generic edit tried to reach an authoritative scope field., Durable scope intent, conflict-checked retries, frozen after promotion. (+8 more)

### Qdrant vector client - "Qdrant vector client"
Cohesion: 0.19
Nodes (7): Any, DocumentProcessingConfig, Qdrant is configured but an operation failed.      Callers must treat this as an, Small wrapper to write/read vectors from Qdrant with an in-memory fallback., Ensure a collection exists with the configured vector size/distance.         If, VectorClient, VectorStoreUnavailableError

### Document list/summary API - "Document list/summary API"
Cohesion: 0.14
Nodes (18): _apply_linkable_document_constraints(), _build_summary_metadata_update(), _clean_summary_scalar(), controller_link_documents(), controller_list_documents(), download_all_project_documents(), get_document_bulk_download_service(), link_documents_endpoint() (+10 more)

### Community 40 - "Community 40"
Cohesion: 0.14
Nodes (13): add_enclosure(), bulk_upload_documents(), create_document(), Process bulk upload in background., Process a single file from bulk upload., Create a new document., Add an enclosure to a document., Bulk upload documents with CSV metadata.          The CSV file should contain me (+5 more)

### Community 41 - "Community 41"
Cohesion: 0.16
Nodes (10): DocumentController, Any, Parse and validate CSV content., Validate individual CSV row., Parse comma-separated string into list., Parse boolean field from various formats., Document controller with clean separation of concerns and bulk upload support., Remove an unlinked failed Document identity while retaining its bytes. (+2 more)

### Community 42 - "Community 42"
Cohesion: 0.16
Nodes (11): ContractReprojectionWorker, ProjectionClaim, Any, Reprojection worker: claim a generation, and commit only if it is still current., Stable clause identity, deliberately free of any classification term.          R, One projected clause row.          Two deliberate absences:          * **no inst, Take ownership of one generation, or return ``None``.          Returns ``None``, Publish this generation, if it is still the current one.          Idempotent: re (+3 more)

### Community 43 - "Community 43"
Cohesion: 0.15
Nodes (11): FileHasher, MarkerResult, MarkerService, DocumentProcessingConfig, Path, Outputs from Marker PDF -> Markdown extraction., Optional Marker integration to extract Markdown from contract PDFs., Service for computing file hashes (+3 more)

### Community 44 - "Community 44"
Cohesion: 0.25
Nodes (7): FileObjectService, Any, Path, StorageSettingsService, Stores immutable file objects while preserving legacy path fields., StorageProviderConfig, StorageProviderLocation

### Community 45 - "Community 45"
Cohesion: 0.13
Nodes (10): ClauseExtractor, ClauseInfo, Return page numbers (1-based) that overlap this clause span., Pull out structured clause tags like GCC 8.3 or SCC 13.2 when present., Build embedding payloads from extracted clauses.         Per markdown guide: Ea, Enhanced clause extraction that preserves complete clauses, Extract complete clauses from contract text.         Returns list of ClauseInfo, Split a long clause intelligently at paragraph or sentence boundaries. (+2 more)

### Community 46 - "Community 46"
Cohesion: 0.14
Nodes (11): ImageOcrRunner, MeterCallback, OcrRunner, PageStore, Path, The seam between the extraction engine and caller-specific persistence.  The eng, Where a caller persists batch lifecycle and page records., Runs OCR over selected pages of a PDF.      PDF-only: implementations may count (+3 more)

### Community 47 - "Community 47"
Cohesion: 0.16
Nodes (18): controller_delete_document(), _current_user_policy_scope(), delete_document(), download_bulk_upload_template(), export_documents(), get_bulk_upload_status(), get_policy_service(), list_documents() (+10 more)

### Community 48 - "Community 48"
Cohesion: 0.20
Nodes (10): OcrMyPdfRunner, OcrRunnerError, Path, Run OCRmyPDF over a batch of pages and return their text by page number.  The pa, Raised when an OCR batch cannot be run or its output cannot be mapped., map_source_pages_to_output(), PageMappingError, Map source PDF page numbers to their index in an OCR batch's output.  Background (+2 more)

### Community 49 - "Community 49"
Cohesion: 0.24
Nodes (13): Citation, ContractQARequest, ContractQAResponse, IterationTrace, Any, RagRequest, RagResponse, SearchBackend (+5 more)

### Community 50 - "Community 50"
Cohesion: 0.26
Nodes (17): _authorize_contract_scope(), create_contract_upload_session(), download_contract(), get_contract_service(), get_contract_status(), get_policy_service(), list_contract_uploads(), ContractService (+9 more)

### Community 51 - "Community 51"
Cohesion: 0.21
Nodes (9): clause_node_id(), ClauseGraphService, GraphEdge, GraphModel, GraphNode, Any, ContractClause, Build the clause graph in FalkorDB (req 18).  Nodes: Contract, ContractDocument, (+1 more)

### Community 52 - "Community 52"
Cohesion: 0.18
Nodes (7): ParsedDocument, Any, Upsert job status in database, Full document text plus page-aware spans for PDF grounding., Delegate page-level extraction to the shared engine.          Behaviour is unc, Fallback path when vector service is unavailable., Create database records for document vectors

### Community 53 - "Community 53"
Cohesion: 0.15
Nodes (10): DocumentParser, DocumentParsingError, Custom exception for document parsing errors, Service for parsing different document formats, Initialize available parsers, Extract text from document with proper error handling and page spans, Extract text from PDF using pdfminer with page spans for grounding., Extract text from DOCX using python-docx (+2 more)

### Community 54 - "Community 54"
Cohesion: 0.15
Nodes (8): ObjectId, Persist manually edited summary metadata without reprocessing content., Search documents with a query.                  Args:             query: Mong, Retrieve multiple documents with pagination.                  Args:, Create a new document.         Accepts either a Document instance or keyword fi, Enrich document for read operations:          - Resolve project_name from proje, Document, NotificationContext

### Community 55 - "Community 55"
Cohesion: 0.17
Nodes (15): ApplicabilityLifecycleKind, ApplicabilityCommand, Apply, withdraw or supersede - always as an explicit, confirmed act.      Appl, record_applicability(), ApplicabilityOverlapError, ensure_contract_document_indexes(), _interval_is_open(), Any (+7 more)

### Community 56 - "Community 56"
Cohesion: 0.24
Nodes (5): IngestionJob, IngestionPipeline, Any, Orchestrated ingestion pipeline with idempotent stages., Update vector_sync_status bookkeeping to clear stale statuses.

### Community 57 - "Community 57"
Cohesion: 0.19
Nodes (11): IngestionService, AsyncIOMotorDatabase, ObservabilityService, Facade to create ingestion jobs and trigger the pipeline., get_embedding_client(), get_llm_generator(), get_retrieval_config(), get_vector_client() (+3 more)

### Community 58 - "Community 58"
Cohesion: 0.18
Nodes (9): Lazily wire real Qdrant + FalkorDB sinks for the live pipeline., ClauseEmbeddingService, Any, ContractClause, Embed authorised clauses (cleaned text only) and upsert to Qdrant.  Embeddings a, Context-prefixed cleaned text used for the embedding (spec section 8)., Qdrant payload for a clause vector (req 17)., Only authorised, validated clauses with cleaned text are embedded. (+1 more)

### Community 59 - "Community 59"
Cohesion: 0.15
Nodes (8): DatabaseService, Get organization name by ID, Get project name by ID, Service for database operations during ingestion, Ensure required database indexes exist, Create index for document_vectors collection, Create index for contract_ingest_jobs collection, Create indexes for page/batch OCR audit collections.

### Community 60 - "Community 60"
Cohesion: 0.19
Nodes (5): Provider-backed immutable file object storage service., Any, Download an object and return its bytes. Caller decides how to persist/stream., S3 abstraction. Uses environment-driven settings and falls back to defaults., S3Service

### Community 61 - "Community 61"
Cohesion: 0.29
Nodes (10): deterministic_chunk_id(), Build a deterministic chunk identifier that can be reused across Mongo + Qdrant., chunk_text(), Deterministically chunk text with overlap., Chunk, compute_content_hash(), IngestionJobCreate, IngestionOptions (+2 more)

### Community 62 - "Community 62"
Cohesion: 0.28
Nodes (14): bg_required_dates(), create_contract_master(), get_contract_master(), get_policy(), list_contract_master(), _load(), CurrentUser, PolicyService (+6 more)

### Community 63 - "Community 63"
Cohesion: 0.24
Nodes (7): PageRasterizer, Path, RasterizationError, Render PDF pages and regions to PNG for the vision fallback.  pdfplumber's `to_i, Raised when a page or region cannot be rendered., Return the page as PNG bytes., Return only the requested region as PNG bytes.          `bbox` is in PDF user-sp

### Community 64 - "Community 64"
Cohesion: 0.18
Nodes (6): LLMRerankerBackend, Re-order ``results`` (already heuristically ranked) by a blend of the         he, Return one relevance score in [0, 1] per passage., Scores query/passage relevance with the existing LLM generator.      Uses one ba, RerankerBackend, RerankerService

### Community 65 - "Community 65"
Cohesion: 0.27
Nodes (5): ContractTextPreprocessor, ParsedPage, Lightweight representation of a page worth of text., Synchronous pdfminer extraction, run through an executor by callers., Clean page text before contract chunking while preserving legal meaning.

### Community 66 - "Community 66"
Cohesion: 0.23
Nodes (6): SearchResult, Map lightweight labels (C1, C2...) to stable citation identifiers (clause/sectio, Lightweight reranker combining base score, clause-hint matches, and legal keywor, Keep only evidence from the explicitly selected documents.      The grounding, Hybrid retrieval: reuse search() (vector-backed) across multiple queries, then r, _restrict_to_grounding()

### Community 67 - "Community 67"
Cohesion: 0.23
Nodes (12): controller_get_document(), controller_update_document(), get_document(), DocumentUpdate, Get document with proper authorization and enrichment., Update document with validation and authorization., Get a specific document., Initialize a draft letter request for a document.     Returns document details t (+4 more)

### Community 68 - "Community 68"
Cohesion: 0.23
Nodes (8): ContractClause, Modification links from SCC/addendum clauses to base GCC clauses (req 19)., _classify(), ModificationDetector, ModificationSignal, Detect SCC / addendum / corrigendum modifications to base (GCC) clauses.  Per th, Phrase-based detector for clause modifications (req 19)., Structured modification link (spec section 10).

### Community 69 - "Community 69"
Cohesion: 0.24
Nodes (11): _metadata_to_legacy_dict(), _parse_date_safe_fixed(), parse_extraction_report_fixed(), Any, datetime, Legacy metadata compatibility helpers.  Older routers import ``rbac_backend.serv, Return field names used by historical metadata callers., Parse an LLM extraction report into legacy dictionary keys. (+3 more)

### Community 70 - "Community 70"
Cohesion: 0.20
Nodes (11): describeDocumentProgress(), DuplicateBlock, FileWithRelativePath, isDocumentProgressSettled(), Organization, PROCESSING_STAGE_LABELS, PROCESSING_STATUS_LABELS, PROCESSING_TONE_CLASSES (+3 more)

### Community 71 - "Community 71"
Cohesion: 0.18
Nodes (8): controller_add_enclosure(), EnclosureResponse, Add enclosure with security validation., AntivirusService, Path, Asynchronous ClamAV client service using standard clamd TCP streaming., Central service for scanning uploaded files via ClamAV clamd daemon., Scan a local file by streaming its chunks over network socket to ClamAV.

### Community 72 - "Community 72"
Cohesion: 0.25
Nodes (7): get_upload_policy(), CurrentUser, Serve the canonical upload policy to the client., Return the allowed MIME types, extensions, and size caps per surface.      Authe, Any, The canonical supported-file policy.  The backend is the single source of truth., UploadPolicyService

### Community 73 - "Community 73"
Cohesion: 0.31
Nodes (6): group_contiguous_pages(), PageExtractionEngine, Path, Group sorted, de-duplicated page numbers into contiguous runs.      Behaviour is, _NativePage, _PageState

### Community 74 - "Community 74"
Cohesion: 0.31
Nodes (4): SearchRequest, Keyword-score structured clause records (contract_clauses collection)., Mongo contract retrieval: merge clause records and document chunks., Phrase-aware lexical score for bounded Mongo contract fallback.          Contr

### Community 76 - "Community 76"
Cohesion: 0.22
Nodes (5): appendUploadScope(), ContractScopeLevel, ContractUploadCapabilities, InvalidUploadScopeError, serializeUploadScope()

### Community 77 - "Community 77"
Cohesion: 0.33
Nodes (6): Dependency-free BM25-lite relevance for the Mongo fallback path.          Comb, first_present(), normalize_source_payload(), Any, Return a common source metadata shape while preserving original fields., source_hash()

### Community 78 - "Community 78"
Cohesion: 0.28
Nodes (7): process_document_endpoint(), process_document_internal(), DocumentProcessingResult, Trigger document reprocessing through the LangChain pipeline., Trigger OCR/AI processing for an existing document., Internal endpoint for orchestrators to process documents without user context., Ensure a local file exists for processing. Prefer local path; otherwise download

### Community 79 - "Community 79"
Cohesion: 0.25
Nodes (8): process_document(), Content extraction, metadata parsing and persistence.          Shared by both, Convenience function for document processing, process_document(), process_document_async(), Async compatibility wrapper using the maintained processor., Synchronous compatibility wrapper used by legacy background tasks., ProcessingResult

### Community 80 - "Community 80"
Cohesion: 0.25
Nodes (6): ImageOcrRunnerError, Path, Direct OCR for standalone PNG/JPEG inputs; no PDF page mapping.  Deliberately se, Raised when a standalone image cannot be OCR'd., TesseractImageOcrRunner, RuntimeError

### Community 81 - "Community 81"
Cohesion: 0.36
Nodes (8): BackendDocument, directionFromUploadType(), DocumentsFilterUrlState, DocumentsPage(), filtersFromSearchParams(), parseDocumentsPageParam(), searchParamsFromFilters(), uploadTypeFromDirection()

### Community 82 - "Community 82"
Cohesion: 0.33
Nodes (7): _build_query_mode(), EvidenceResponse, EvidenceSearchCommand, Translate the wire mode. Every refusal here is a 422, never a default., Contract evidence. Distinct from `/api/contracts/search` on purpose.      The, search_contract_evidence_route(), ContractSearchRequest

### Community 85 - "Community 85"
Cohesion: 0.33
Nodes (4): create_document_processor(), DocumentProcessingConfig, Create document processor instance, Update processing configuration.

### Community 86 - "Community 86"
Cohesion: 0.33
Nodes (5): ParsedDocumentMetadata, Raised when an upload's detected MIME has no extractor.      Deliberately fail, Save processing results to file system and database, UnsupportedSourceKindError, DocumentProcessingError

### Community 87 - "Community 87"
Cohesion: 0.33
Nodes (3): Build the `_id` clause for a list of identifiers, or None if empty.          M, `get_documents_by_ids` bounded by actor scope and publication authority., Fetch documents for the provided identifiers preserving the requested order.

### Community 88 - "Community 88"
Cohesion: 0.50
Nodes (3): BulkUploadStatus, Get bulk upload job status., Attach live per-document pipeline state to bulk upload results.          The sto

### Community 89 - "Community 89"
Cohesion: 0.50
Nodes (4): get_document_controller(), AuthorizationService, Factory function for document controller., ExportService

## Knowledge Gaps
- **13 isolated node(s):** `BackendDocument`, `DocumentsFilterUrlState`, `DuplicateBlock`, `PROCESSING_STAGE_LABELS`, `PROCESSING_STATUS_LABELS` (+8 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **11 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `DocumentService` connect `Document service (authz/publication)` to `Document processing jobs`, `Document service errors/refs`, `Community 54`, `Community 87`, `Document delete/update cascade`?**
  _High betweenness centrality (0.009) - this node is a cross-community bridge._
- **Why does `create_document_processor()` connect `Community 85` to `Document processor + fallback ladder`?**
  _High betweenness centrality (0.008) - this node is a cross-community bridge._
- **Why does `DocumentProcessor` connect `Document processor + fallback ladder` to `Community 85`, `Community 86`, `Community 79`?**
  _High betweenness centrality (0.008) - this node is a cross-community bridge._
- **What connects `Graph adapter abstraction for document relationship graph.`, `Raised when graph operations fail.`, `Represents a node in the knowledge graph.` to the rest of the system?**
  _631 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Letter API router` be split into smaller, more focused modules?**
  _Cohesion score 0.052531645569620256 - nodes in this community are weakly interconnected._
- **Should `Extraction quality gate checks` be split into smaller, more focused modules?**
  _Cohesion score 0.057911392405063294 - nodes in this community are weakly interconnected._
- **Should `Letter service` be split into smaller, more focused modules?**
  _Cohesion score 0.06692242114236999 - nodes in this community are weakly interconnected._