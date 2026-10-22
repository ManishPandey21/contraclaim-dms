# Document Ingestion — Graphs

Read-only audit, 2026-09-27, working tree on `integrate/key-date-eot` @ `a179ca9` (dirty).

**Graphify output:** `docs/audits/document-ingestion-graphify/`. It is a focused AST graph (no LLM, 0 tokens) over 95 ingestion files: routers for documents, letters and contracts; `ingestion/`, `services/extraction*`, upload, bulk, OCR, metadata, references, duplicate detection, the contract ingest/queue/clause/promotion/reprojection modules, publication policy, graph and retrieval; plus `UploadPage.tsx`, `DocumentsPage.tsx` and the upload client services.
- `graph.html` is the interactive view, coloured by community with labels.
- `graph.json` is node-link JSON.
- `GRAPH_REPORT.md` covers god nodes, communities and cohesion.
- `labels.json` holds the community names.
- Size: 2100 nodes, about 4.7k edges, 98 communities.
- The main communities include: Letter API router, Extraction quality gate checks, Letter service, Graph ingestion, Contract clause index, Evidence ladder, Publication authority policy, Background job processor, FalkorDB letter graph, Contract Master API, Bulk upload service, Contract ingest queue worker, Extraction engine + page classifier, Page stores, Reference sync, Retrieval service, OCR service, Duplicate detection, Contract upload streaming, Metadata processor, Contract ingestor, Document processor + fallback ladder, Contract promotion, Qdrant vector client.

**Graphify limitation, which is itself a finding:** static edges stop at dynamic handoffs. There is no static path `upload_contracts_multipart → ContractIngestor`, `ContractIngestQueue → ContractIngestor` or `ContractIngestor → ClauseChunkingAgent`, because these go through Redis payloads and callbacks. The Graphify "surprising connections" (Python `UploadFile`/`Document` ↔ TSX pages) are name collisions, not real edges.

The Mermaid diagrams below were drawn by hand from the file:line evidence in the companion docs. Only real status constants are used.

---

## GRAPH 1 — Overall document ingestion architecture (AS-IS)

```mermaid
flowchart LR
  subgraph Client["React SPA"]
    UP[UploadPage single tab<br/>sequential loop]
    BU[UploadPage bulk tab<br/>one multipart]
    EN[EnclosuresPanel]
    CU[ContractsUploadPage]
    IN[InsuranceRegisterPage]
    DV[DocumentViewerPage<br/>polls missing route]
  end
  subgraph API["backend container (FastAPI)"]
    D1[POST /documents]
    D2[POST /documents/bulk-upload]
    D3[POST /documents/:id/enclosures]
    D4[POST /documents/:id/process<br/>INLINE pipeline]
    C1[POST /contracts/upload-multipart<br/>/upload-session + /upload-chunk]
    I1[POST /insurance/:id/documents/upload]
    BT[[BackgroundTasks<br/>_process_bulk_upload]]
    CD[DocumentController.create_document<br/>stream+sha256+AV+precheck]
  end
  subgraph Stores
    S3[(S3 / local uploads<br/>original, immutable key)]
    M[(Mongo)]
    R[(Redis)]
    Q[(Qdrant)]
    F[(FalkorDB)]
  end
  subgraph Workers
    DW[document-worker<br/>legacy_v0 default]
    UW[document-worker canary<br/>unified_v1]
    CW[contract-worker]
  end
  UP-->D1; BU-->D2; EN-->D3; CU-->C1; IN-->I1; DV-.->D4
  D1-->CD; I1-->CD; D2-->BT-->CD
  CD-->S3; CD-->M
  CD-- document_processing_jobs -->M
  M-- poll/claim -->DW; M-- poll/claim -->UW
  D3-->S3
  C1-->S3; C1-- contract_ingest_queue -->R-->CW
  DW-->M; DW-->Q; DW-->F
  UW-->M; UW-->Q; UW-->F
  CW-->M; CW-->Q; CW-->F
  D4-->M; D4-->Q; D4-->F
  CM[Contract Master<br/>contract_documents<br/>projection never CURRENT]:::gap
  M-.-CM
  classDef gap fill:#fdd,stroke:#c00
```

## GRAPH 2 — Single correspondence upload sequence (production default legacy_v0)

```mermaid
sequenceDiagram
  autonumber
  participant B as Browser (UploadPage)
  participant A as API /documents
  participant V as Validation+AV+precheck
  participant S as S3/local
  participant M as Mongo
  participant W as document-worker
  participant O as OCRService (legacy)
  participant L as OpenAI
  participant Q as Qdrant
  participant G as FalkorDB
  B->>A: POST multipart (one file; loop per file)
  A->>A: PolicyService.authorize(dms.document.upload) ×3
  A->>V: spool, sha256, magic bytes, AV (fail-open default), precheck_upload
  V-->>A: ok | 409 duplicate | letterNo match -> duplicate_status=pending
  A->>S: store original {org}/{proj}/{dir}/{Y}/{M}/{uploadId}_...
  A->>M: insert documents; $set sha256 (best effort)
  A->>M: insert document_processing_jobs {status: queued}
  A-->>B: 201 -> navigate to viewer
  B->>A: GET /documents/{id}/processing-status
  A-->>B: 404 (route missing) -> UI shows nothing
  W->>M: find_one_and_update queued|retrying -> processing (no attempts cap)
  W->>S: download whole file -> tmp/document_processing/<filename>
  W->>O: is_pdf_textual(first 5 pages)?
  alt any text
    O-->>W: pdfplumber text (no OCR for any page)
  else none
    O-->>W: ocrmypdf in-process (no timeout) | failure -> None
  end
  alt have text
    W->>L: process_text (27-field numbered report)
  else no text
    W->>L: upload PDF, Responses API
  end
  L-->>W: report | error -> regex fallback (filename as letterNo)
  W->>W: parse_extraction_report (exception -> EMPTY metadata, success)
  W->>M: documents.{ocrText, full_text=LLM Full Content, subject, ...}
  W->>Q: replace_document (delete then add; error -> 0, swallowed)
  W->>M: document_vectors (second embedding pass)
  W->>G: graph + evidence (swallowed); reference sync; sync_document_to_falkor (raises -> retry all)
  W->>M: job completed; documents.processing_status=completed (no vector check)
```

## GRAPH 3 — Bulk correspondence upload sequence

```mermaid
sequenceDiagram
  autonumber
  participant B as Browser (bulk tab)
  participant N as nginx (200m cap)
  participant A as API /documents/bulk-upload
  participant T as BackgroundTask (in API process)
  participant C as create_document
  participant M as Mongo
  participant W as document-worker
  B->>N: ONE multipart: CSV + up to 100 files
  N->>A: (413 if >200 MB although config allows 500 MB)
  A->>A: authorize; count/size checks; temp-copy files
  A->>M: bulk_upload_jobs {status: processing} (no org/user)
  A-->>B: job_id
  A->>T: schedule
  loop each CSV row, sequential
    T->>C: create_document(row) -> AV, precheck, store, insert, enqueue job
    C-->>T: ok | error (row failure recorded, loop continues)
    T->>M: rewrite full results array
  end
  T->>M: completed | completed_with_errors | failed
  Note over T: process restart = job stuck "processing", tail rows lost, temp files gone
  loop every 3 s, max 200 polls
    B->>A: GET /bulk-upload/{job_id}/status (no owner check)
    A-->>B: rows + processing_status/stage/duplicate_status
  end
  W->>M: each row's job processed independently (see Graph 2)
  Note over B: no per-file retry, no server cancel, no resume after refresh
```

## GRAPH 4 — Contract-document upload sequence

```mermaid
sequenceDiagram
  autonumber
  participant B as ContractsUploadPage
  participant A as /contracts/upload-*
  participant S as S3/local
  participant M as Mongo
  participant R as Redis
  participant W as contract-worker
  participant E as PageExtractionEngine
  participant Q as Qdrant
  participant G as FalkorDB
  participant K as ClauseChunkingAgent
  B->>A: multipart (<=5MB) or session + chunks
  A->>A: authorize per file; MIME pdf/docx; AV; NO duplicate precheck
  A->>S: {org}/{proj}/contracts/{uploadId}_{file}
  A->>M: documents(uploadType=contract,status=queued), contracts, contract_versions, sessions, contract_ingest_jobs
  A->>R: enqueue contract_ingest_job:{upload_id} (no-op if queued/running)
  W->>R: BRPOPLPUSH -> running (attempts+1, heartbeat)
  W->>E: pages (OCR <40 chars, batches of 25) -> contract_ocr_pages
  Note over E: completeness=PARTIAL ignored; DOCX bypasses engine
  W->>W: clean, categorize, Marker (unused), regex ClauseExtractor
  W->>M: document_vectors delete+insert
  W->>Q: clause payloads (uuid5 ids, rich page lineage)
  W->>G: upsert_contract_graph (sync, MERGE, no delete)
  W->>K: _run_clause_indexing_safe (errors swallowed)
  K->>M: contract_clauses upsert by clause_uid
  K->>Q: namespace contract_clauses, point id = SHA-1 hex (likely rejected)
  W->>M: documents.status=completed
  Note over M: contract_documents / projection untouched (PENDING forever)
```

## GRAPH 5 — OCR / extraction decision graph

```mermaid
flowchart TD
  S[Job claimed] --> PV{pipeline_version}
  PV -- legacy_v0 (prod default) --> L1{"any text on pages 1-5?<br/>PyPDF2 then pdfplumber"}
  L1 -- yes --> L2[pdfplumber text for whole doc<br/>NO OCR on any page]
  L1 -- no --> L3[ocrmypdf.ocr in-process<br/>force_ocr=False, no timeout]
  L3 -- ok --> L4[OCR text]
  L3 -- PriorOcrFound / Encrypted / other error --> L5[copy original, text=None]
  L5 --> L6[upload PDF to LLM<br/>LLM Full Content becomes text]
  PV -- unified_v1 (canary) --> U0[SourceKindRouter]
  U0 -- ARCHIVE --> U9[stored_only]
  U0 -- PDF --> U1[per page: native text + PageClassifier]
  U1 --> U2{UNRENDERABLE?}
  U2 -- yes --> U3[unrenderable]
  U2 -- no --> U4{"native chars &lt; 40?"}
  U4 -- no --> U5[text_layer]
  U4 -- yes --> U6{"OCR enabled and within attempt boundary?"}
  U6 -- disabled --> U7[ocr_disabled]
  U6 -- beyond boundary --> U8[ocr_deferred]
  U6 -- yes --> UB[OCRmyPDF CLI batch of 25, 900 s]
  UB -- text --> UC[ocr_completed]
  UB -- empty --> UD[ocr_empty -> unresolved]
  UB -- error/timeout --> UE[ocr_failed]
  UC & U5 --> QG[quality gate: needs_review -> completeness PARTIAL]
  RET[retry_pages set] -.->|OCR only retry set;<br/>others rebuilt from native text| U5
  classDef bad fill:#fdd,stroke:#c00
  class L2,L6,UD,RET bad
```

## GRAPH 6 — Metadata extraction graph

```mermaid
flowchart TD
  T["text available?"] -- yes --> P1[OpenAI chat: 27-field numbered TEXT report<br/>untruncated input, 60 s, 4096 out]
  T -- no --> P2[OpenAI Responses over uploaded PDF]
  P1 -- error --> FB[regex fallback report<br/>letterNo/subject = FILENAME<br/>date = first date in text]
  P2 -- error --> FAIL[attempt fails -> retry -> dead_lettered]
  P1 --> PR[parse_extraction_report regex]
  FB --> PR
  PR -- list item 'letter no X dated d.m.y'<br/>in keywords/clauses/reply points --> EMPTY[ValidationError caught<br/>ALL fields empty, success=True]:::bad
  PR -- ok --> MD[ParsedDocumentMetadata]
  PAI{"use_pydantic_ai?<br/>default False"} -- yes --> P3[PydanticAI agent, 12k chars] --> MD
  MD --> W1[documents: subject, letterNo, from, to, date, summary,<br/>key_reply_points, contractual_clauses, keywords, extracted_tags...]
  MD --> W2[documents.full_text = LLM Full Content]:::bad
  MD --> W3[append to uploads/org/proj/incoming.md]
  MD --> W4[ai_extractions: schema/prompt version, confidence=0.75 fixed]
  W1 --> OV[reprocess overwrites human edits]:::bad
  classDef bad fill:#fdd,stroke:#c00
```

## GRAPH 7 — Markdown generation / artifact lineage (AS-IS vs gaps)

```mermaid
flowchart LR
  O[(Original file<br/>immutable S3 key)] --> PT{"page records?"}
  PT -- legacy: none --> RT[raw_ocr_text]
  PT -- unified: document_ocr_pages<br/>write-only, heads never published --> RT
  RT --> LLM[LLM report]
  LLM --> FT[documents.full_text<br/>= authoritative_text]:::bad
  LLM --> MDLOG[incoming.md / outgoing.md<br/>append-only, per project, local disk]:::bad
  RT --> CH[chunks 3000/200<br/>no page, no span]
  CH --> QD[(Qdrant, nested payload)]
  CH --> DV[(Mongo document_vectors)]
  O --> MK[Marker .md, contracts only<br/>local disk, unused for chunking]:::bad
  X[Canonical Markdown<br/>with page anchors + hash + DB pointer]:::missing
  classDef bad fill:#fdd,stroke:#c00
  classDef missing fill:#eee,stroke:#999,stroke-dasharray: 5 5
```

## GRAPH 8 — Reference-linking graph

```mermaid
flowchart TD
  L[LLM prompt #6 references list] --> RP[reference_parser<br/>needs 'dated|dtd|dt' and 'no|number|#']
  RP --> N[normalize_letter_code: lower, non-alnum -> '-']
  N --> R1{"explicit documentId?"}
  R1 -- no --> R2["find letterNoNormalized == code<br/>scope: org + project<br/>org-only if source has no project"]
  R2 -- miss --> R3[case-insensitive exact letterNo]
  R2 & R3 -- hit --> LK[references / referencedBy<br/>bucket source=parser]
  R3 -- miss --> UQ[reference_sync_queue<br/>daily reaper 08:45, 200/run<br/>expires after 30 days]
  LK --> FG[("Falkor :Letter normCode<br/>SHARED across tenants")]
  FG --> TH[get_thread 6 hops -> drafting context<br/>graph_codes_denied has no org filter]:::bad
  DEL[delete_letter preserve branch<br/>deletes ALL outgoing edges] --> FG
  MAN[manual links bucket source=manual] --> LK
  NR["Not recognised: Ref. #91;1#93; lists, drawings, BOQ, VO, IPC/RA, EOT, attachments"]:::bad
  classDef bad fill:#fdd,stroke:#c00
  class DEL bad
```

## GRAPH 9 — Persistence map

```mermaid
flowchart LR
  subgraph Object["Object storage / disk"]
    S3A[S3 /org/proj/incoming|outgoing|contracts|enclosures<br/>ORIGINALS - authoritative]
    LD[local uploads/ + incoming.md + marker/ + ocr_batches/ + tmp/document_processing]
  end
  subgraph Mongo
    DOC[documents<br/>record + derived text/metadata, no revisions]
    FO[file_objects / document_versions]
    JOB[document_processing_jobs<br/>no indexes]
    PG[document_ocr_pages/_batches<br/>unified only]
    HD[document_extraction_heads<br/>never written]
    DVX[document_vectors]
    VSS[vector_sync_status<br/>no tenant fields]
    BJ[bulk_upload_jobs<br/>no org/user]
    RSQ[reference_sync_queue]
    CJ[contract_ingest_jobs / upload_sessions]
    CP[contract_ocr_pages]
    CC[contract_clauses]
    CDX[contract_documents + facts/applicability<br/>AUTHORITATIVE governance]
    CH2[chunks / ingestion_jobs<br/>manual P4 only]
    CHR[matter_chronology_events, ai_extractions, evidence graph]
  end
  subgraph Redis
    RQ[contract_ingest_queue/_processing/_deadletter]
    RL[rate:*, upload:concurrency:*, cache:*, sessions]
  end
  subgraph Qdrant
    QV[document_vectors collection<br/>two payload shapes]
    QC[contract_clauses namespace]
  end
  subgraph Falkor["FalkorDB graph 'contraclaim'"]
    FL[Letter / Document / Section / Clause<br/>CITES, REPLIES_TO, HAS_*]
  end
  S3A --> DOC --> DVX --> QV
  DOC --> FL
  CP --> CC --> QC
  CDX -. projection PENDING .- QC
```

## GRAPH 10 — Failure / retry state machines (real constants)

Document `processing_status` + job (correspondence):

```mermaid
stateDiagram-v2
  [*] --> queued : queue_document_processing
  queued --> processing : claim
  retrying --> processing : claim (run_after <= now; no attempts cap)
  processing --> skipped : lifecycle_state = deleted
  processing --> metadata_extracted : success and publishable
  processing --> human_review_required : success and not publishable
  processing --> retrying : failure, attempts < max (backoff min(300, 30*n))
  processing --> failed : failure, attempts >= max (job dead_lettered)
  processing --> retrying : stale recovery (heartbeat > 3600 s)
  metadata_extracted --> completed : checkpoint clean (vectors NOT checked)
  metadata_extracted --> partially_processed : checkpoint partial (unified)
  partially_processed --> processing : resume claim (retry_pages)
  human_review_required --> completed : later checkpoint overwrites
  processing --> stored_only : archive (unified)
  processing --> processing : inline /process failure leaves status stuck
```

Page status (unified + contracts):

```mermaid
stateDiagram-v2
  [*] --> text_layer : native chars >= 40
  [*] --> unrenderable
  [*] --> ocr_disabled
  [*] --> ocr_deferred
  [*] --> ocr_completed : batch ok with text
  [*] --> ocr_empty : batch ok, no text
  [*] --> ocr_failed : batch error/timeout
  ocr_failed --> ocr_completed : retry_pages
  ocr_deferred --> ocr_completed : retry_pages
  ocr_empty --> ocr_empty : retry burns an attempt
  ocr_completed --> text_layer : next resume rebuilds from native text (DI-B4)
```

Contract document + Redis job + Contract Master projection:

```mermaid
stateDiagram-v2
  state "documents.status (contract)" as D {
    [*] --> queued
    queued --> failed : enqueue failed (queue_failed)
    queued --> processing
    processing --> completed : even if pages PARTIAL / 0 clauses
    processing --> failed : shown while Redis still retries
    failed --> processing : redis retry / reindex
    completed --> processing : reindex / ocr retry
  }
  state "Redis job hash" as R {
    [*] --> q
    q --> running : BRPOPLPUSH
    running --> done : completed
    running --> q : attempts < 3 (lost if killed during backoff)
    running --> dead : attempts >= 3 -> contract_ingest_deadletter
  }
  state "projection_status" as P {
    [*] --> PENDING : promote / reclassify
    PENDING --> IN_PROGRESS : claim (no prod caller)
    IN_PROGRESS --> CURRENT : complete (no prod caller)
    IN_PROGRESS --> FAILED
    CURRENT --> PENDING : reclassify
    FAILED --> PENDING : reclassify
  }
```
(In the Redis diagram `q`, `done` and `dead` stand for the hash values `queued`, `completed` and `failed`.)

## GRAPH 11 — Current AS-IS workflow (condensed)

```mermaid
flowchart TD
  U[Upload] --> V[Validate: magic bytes, AV fail-open default,<br/>non-atomic sha256 precheck]
  V --> O[Persist original - OK, immutable]
  O --> J[documents + job - racy]
  J --> K{pipeline}
  K -- legacy default --> X1[doc-level OCR decision]
  K -- unified canary --> X2[page-level OCR, resume corrupts pages]
  X1 & X2 --> M[LLM text report -> regex<br/>silent empty on parse error]
  M --> T[full_text = LLM paraphrase]
  M --> E[embed twice; Qdrant errors swallowed]
  E --> G[graph: shared tenant nodes]
  G --> C[completed - no vector/metadata/page check]
  C --> S1["/v1/retrieval/search: flat filter misses nested payload"]
  C --> UI[Viewer polls missing route; review states unknown]
  classDef bad fill:#fdd,stroke:#c00
  class X2,M,T,E,G,C,S1,UI bad
```

## GRAPH 12 — Recommended TO-BE workflow

```mermaid
flowchart TD
  U[Upload / bulk manifest] --> V[Validate everywhere:<br/>magic+ext, AV fail-closed, encryption probe, scope]
  V --> O[Persist immutable original<br/>If-None-Match]
  O --> J[TXN: documents + unique job<br/>unique sha256 index]
  J --> W[Worker: attempts cap, lease token, timeout,<br/>fair per-tenant claim]
  W --> DT[Detect type from stored intake MIME]
  DT --> PI[Page inventory: class, chars, CID ratio, blank]
  PI --> OCR[OCR candidate pages only<br/>carry forward resolved pages]
  OCR --> CT[Canonical page text: versioned, hashed<br/>publish_run heads pointer]
  CT --> MD[Canonical Markdown with page anchors<br/>S3 derived/doc/run + DB pointer + hash]
  MD --> BR{family}
  BR -- correspondence --> MX[JSON metadata facts:<br/>page/span/confidence/model/prompt/schema]
  MX --> RF[Typed references, project-scoped,<br/>reverse resolution on arrival]
  RF --> RP[Reply points: facts / issues / considerations]
  BR -- contract --> CL[Clauses, definitions, notices, time-bars]
  CL --> PJ[Invalidate + re-project governed instrument]
  RP & PJ --> HR[Review gate per field policy<br/>does not block text indexing]
  MD --> CK[Chunk by Markdown block<br/>page_start/end + span]
  CK --> EM[Embed once, versioned]
  EM --> QD[Qdrant: flat tenant payload, uuid5 ids,<br/>upsert then delete stale]
  RF --> GR[Falkor: tenant-scoped keys, owner-filtered deletes]
  QD & GR & HR --> DONE{"all required stages green?"}
  DONE -- yes --> C[COMPLETE]
  DONE -- no --> DG[PARTIALLY_PROCESSED / INDEX_FAILED /<br/>METADATA_DEGRADED / HUMAN_REVIEW_REQUIRED<br/>stage-level retry]
  PJ --> CUR[projection CURRENT only after points verified]
```
