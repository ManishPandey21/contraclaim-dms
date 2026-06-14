# Complete Data Storage Workflow Analysis & Improvement Guide

## Executive Summary

Your ContraClaim system has a **multi-tier vector storage architecture** spanning MongoDB, Qdrant, and FalkorDB. This document details:

1. **Complete workflow** from document upload → processing → storage
2. **Data flow** across all three storage systems
3. **Current gaps and issues** in the dual-write and relationship synchronization
4. **Detailed improvement recommendations** with implementation code
5. **Best practices** for keeping data consistent across stores

---

## 1. Complete Data Storage Workflow

### Phase 1: Document Upload & Initial Storage

```
User uploads PDF → DocumentService.create_document()
   ↓
1. Store base document record in MongoDB: documents collection
   Fields: filename, organization_id, project_id, status="pending", created_at
   
2. Return document_id (ObjectId) to frontend
   
3. Queue background job: DocumentService.queue_document_processing()
   → Background worker will process asynchronously
```

**Current Location:** `DocumentService.create_document()` (document_service.py:550-680)

**What's Stored:**
- MongoDB `documents` collection gets initial record with basic metadata
- No vectors stored yet (happens after OCR/extraction)
- No graph nodes created yet (happens in Phase 3)

---

### Phase 2: OCR, Metadata Extraction & Parsing

```
Background Worker (Celery) processes:

1. DocumentService.process_document_async()
   └─ Located in: document_service.py:1070+
   
2. DocumentProcessor.process_document()
   ├─ Step 1: OCRService.process_pdf() → raw_ocr_text
   ├─ Step 2: OpenAI upload & extraction → extracted_content
   ├─ Step 3: PydanticAIService.extract_metadata() → ParsedDocumentMetadata
   │   └─ FALLBACK: TextProcessingService.parse_extraction_report() (regex)
   └─ Step 4: DatabaseService.save_document_data() → chunks_created

3. Return ProcessingResult with:
   ├─ metadata: ParsedDocumentMetadata (structured fields)
   ├─ chunks_created: int (number of embeddings)
   ├─ metadata_source: "pydantic_ai" | "legacy_regex" | "openai"
   └─ success: bool
```

**Key Services Involved:**
- `OCRService` – PDF to text conversion
- `OpenAIService` – File upload & extraction prompt
- `PydanticAIService` – Structured metadata extraction (optional)
- `TextProcessingService` – Text chunking & regex parsing
- `DatabaseService` – Persistence to MongoDB + vectors

**Current Location:** `DocumentProcessor.process_document()` (document_processor.py:45-180)

---

### Phase 3: Metadata & References Update to MongoDB

```
DatabaseService._upsert_document_metadata():

1. Find or create document in MongoDB:
   documents collection → update fields:
   {
     ocrText: full_text,
     subject: metadata.subject,
     letterNo: metadata.letter_no,
     from: metadata.from_company,
     to: metadata.to_company,
     summary: metadata.summary,
     keywords: metadata.keywords,
     contractual_clauses: metadata.contractual_clauses,
     reference: [normalized_references],  ← IMPORTANT
     full_text: metadata.full_content,
     updatedAt: datetime.utcnow()
   }

2. _normalize_metadata_references() processes reference array:
   Input: [{letterNo: "X", date: "Y", ...}, ...]
   Output: [{letterNo, date, text}, ...]
   Stored in: documents.reference[]
```

**Critical Issue #1: Reference Storage Inconsistency**
- References extracted from OCR are stored in `documents.reference[]`
- But **NOT synchronized to linked documents' references arrays**
- When document A references B, only A is updated; B is not marked as "referenced by A"

**Current Location:** `DatabaseService._upsert_document_metadata()` (database_service.py:80-150)

---

### Phase 4: Vector Embedding Creation & Dual-Write

```
DatabaseService._create_and_store_embeddings():

1. TextProcessingService.chunk_text() → chunks[]
   Chunk size: config.chunk_size (usually 512)
   Overlap: config.chunk_overlap (usually 50)

2. For each chunk, build payload:
   {
     text: chunk_content,
     metadata: {
       document_id: str(doc._id),
       organization_id: org_id,
       project_id: proj_id,
       uploadType: upload_type,
       letterNo: doc.letterNo,
       filepath_local: doc.filepath_local,
       filepath_s3: doc.filepath_s3,
       chunk_index: i,
       source: "document_processing",
       checksum_sha256: sha256(chunk)
     },
     checksum: sha256(chunk)
   }

3. DUAL-WRITE EXECUTION:
   ┌─ Branch A: Qdrant (LangChain)
   │  └─ LangChainVectorService.replace_document(payloads)
   │     ├─ Delete old vectors by document_id filter
   │     ├─ Upsert new chunks with LangChain QdrantVectorStore
   │     └─ Log: "Upserted N chunks to Qdrant"
   │
   └─ Branch B: MongoDB (LlamaIndex)
      └─ LlamaIndexVectorService.index_chunks(payloads)
         ├─ Create OpenAI embeddings (batch)
         ├─ Add to MongoDBAtlasVectorSearch vector store
         ├─ Insert bookkeeping records in document_vectors collection
         └─ Return node_ids for future deletion tracking

4. MongoDB document_vectors collection receives:
   {
     _id: ObjectId,
     document_id: str,
     organization_id: str,
     project_id: str,
     uploadType: str,
     letterNo: str,
     filepath_local: str,
     filepath_s3: str,
     chunk_index: int,
     vector_ref: str (LlamaIndex node ID for deletion),
     embedding_id: uuid,
     embedding_model: "text-embedding-3-small",
     embedding_dims: 1536,
     embedding: [float, float, ...],  ← RAW VECTOR
     text: chunk_text,
     num_tokens: int,
     checksum_sha256: str,
     createdAt: datetime
   }

5. Qdrant collection (same name as config.qdrant_collection) receives:
   {
     id: computed_id,
     vector: [float, float, ...],
     payload: {
       document_id: str,
       organization_id: str,
       project_id: str,
       chunk_index: int,
       text: chunk_text,
       checksum: str,
       ... (all metadata fields)
     }
   }
```

**Current Location:** `DatabaseService._create_and_store_embeddings()` (database_service.py:150-240)

**Dual-Write Configuration:**
- `config.vector_store_enabled` = enables MongoDB LlamaIndex writing
- `config.qdrant_enabled` = enables Qdrant LangChain writing
- Both are typically **true**, so both branches execute (true dual-write)

---

### Phase 5: Graph Database Ingestion (FalkorDB)

```
GraphIngestionService.ingest_document():

Called from: DocumentService.process_document_async() (document_service.py:1080+)

1. Normalize letter code:
   "GLM-SAM-KNPCC-05-UPMRC-OL-2024-4450" → "glm-sam-knpcc-05-upmrc-ol-2024-4450"

2. Build letter node payload:
   {
     code: original_letter_no,
     normCode: normalized,
     direction: "incoming" | "outgoing",
     subject: metadata.subject,
     date: parsed_datetime,
     project: project_name or "KNPCC-11",
     createdAt, lastUpdated: timestamps
   }

3. Extract references from metadata.references:
   For each {letterNo: "X", date: "Y", ...}:
   ├─ Normalize: "x-y-z..."
   ├─ Build ref node:
   │  {
   │    code: X,
   │    normCode: normalized,
   │    type: "CITES" (default) | "REPLIES_TO",
   │    source: "parser" (default) | "manual"
   │  }
   └─ Skip if matches current letter normCode

4. Execute Cypher merge operations via Redis/FalkorDB:
   ├─ OPTIONAL: Clean up old edges (if cleanup_enabled)
   │  └─ Delete CITES/REPLIES_TO edges where source != "manual"
   │
   ├─ MERGE source letter node (src)
   │  ├─ CREATE: normCode, code, direction, subject, date, project, createdAt
   │  └─ MATCH: UPDATE code, direction, subject, date, project, lastUpdated
   │
   ├─ FOR EACH reference:
   │  ├─ MERGE destination letter node (dst)
   │  │  ├─ CREATE: normCode, code, direction, createdAt
   │  │  └─ MATCH: UPDATE code, lastUpdated
   │  │
   │  └─ MERGE edge (src)-[CITES|REPLIES_TO]->(dst)
   │     └─ SET: source, createdAt, updatedAt
   │
   └─ Execute: redis.execute_command("GRAPH.QUERY", graph_name, cypher, params)

5. Result: Letter nodes + relationship edges persisted in FalkorDB
   └─ Schema validated: UNIQUE constraint on normCode, indexes on date/direction
```

**Current Location:** Not fully implemented in provided code (would be in GraphIngestionService)
**Trigger Point:** `DocumentService.process_document_async()` (document_service.py:1080+)

---

## 2. Data Storage Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                      DOCUMENT UPLOAD ENDPOINT                    │
│            POST /documents/upload (DocumentController)           │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
                   ┌─────────────────────┐
                   │  DocumentService    │
                   │.create_document()   │
                   └────────┬────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        │                   │                   │
        ▼                   ▼                   ▼
    ┌───────────────┐  ┌──────────────┐  ┌──────────────┐
    │   MongoDB     │  │  SecureFile  │  │ Queue Job    │
    │   documents   │  │   Service    │  │  (Celery)    │
    │   collection  │  │  (S3 Upload) │  │ background   │
    └───────────────┘  └──────────────┘  └──────┬───────┘
                                                 │
                                       ┌─────────▼────────┐
                                       │ BACKGROUND WORKER│
                                       │process_document_ │
                                       │async()           │
                                       └────────┬─────────┘
                                                │
        ┌───────────────────────────┬──────────┼──────────┬─────────────────┐
        │                           │          │          │                 │
        ▼                           ▼          ▼          ▼                 ▼
    ┌─────────────┐        ┌──────────────┐ ┌──────────────┐    ┌───────────────────┐
    │ OCRService  │        │ OpenAIService│ │PydanticAI    │    │ Graph Ingestion   │
    │.process_pdf │        │.upload_file()│ │Service       │    │Service            │
    │             │        │.process_doc()│ │              │    │                   │
    └─────────────┘        └──────────────┘ └──────────────┘    └───────────────────┘
         │                        │                 │                       │
         │ raw_ocr_text           │ extracted_      │ ParsedDocument        │ Letter nodes +
         │                        │ content         │ Metadata              │ references
         │                        │                 │                       │
         └────────────────────────┼─────────────────┴───────────┬───────────┘
                                  │                             │
                                  ▼                             ▼
                        ┌──────────────────────┐    ┌─────────────────────────┐
                        │ TextProcessing       │    │ DatabaseService        │
                        │ .parse_extraction()  │    │ ._upsert_document_meta()│
                        │ .chunk_text()        │    │                         │
                        └──────────┬───────────┘    └────────┬────────────────┘
                                   │                        │
                                   └────────────┬───────────┘
                                                │
                    ┌───────────────────────────┼────────────────────────┐
                    │                           │                        │
                    ▼                           ▼                        ▼
            ┌──────────────────┐      ┌──────────────────────┐  ┌────────────────┐
            │ MongoDB          │      │ Dual-Write: Vectors │  │ FalkorDB Graph │
            │ documents        │      │                      │  │ (via Graphiti) │
            │ collection       │      └──────────┬───────────┘  └────────────────┘
            │                  │                 │
            ├─ subject         │      ┌──────────┴──────────┐
            ├─ letterNo        │      │                     │
            ├─ from/to         │      ▼                     ▼
            ├─ summary         │  ┌─────────────────┐  ┌─────────────┐
            ├─ keywords        │  │ MongoDB         │  │ Qdrant      │
            ├─ clauses         │  │ (LlamaIndex)    │  │ (LangChain) │
            ├─ reference[]     │  │                 │  │             │
            └─ full_text       │  ├─document_vectors│  ├─embeddings  │
                               │  │ collection      │  │ collection  │
                               │  ├─ chunk_text    │  ├─ metadata   │
                               │  ├─ embedding[]   │  └─ vectors    │
                               │  ├─ metadata      │
                               │  └─ vector_ref    │
                               │                   │
                               └─────────────────┘
                                       │ ← ISSUE: Not synced
                                       │
                         ┌─────────────▼──────────────┐
                         │  MISSING SYNCHRONIZATION  │
                         │  References updated in:   │
                         │  ✓ documents.reference    │
                         │  ✓ FalkorDB edges         │
                         │  ✗ Linked document refs   │
                         │  ✗ Qdrant metadata        │
                         └───────────────────────────┘
```

---

## 3. Current Issues & Root Causes

### Issue #1: Reference Linkage Not Bidirectional

**Problem:** When document A is extracted with references to B & C, only A's `documents.reference[]` is updated. B and C never know they are "referenced by" A.

**Current Behavior:**
```python
# In document_processor.py → database_service.py → DocumentService.process_document_async()
if metadata.references:
    normalized_refs = self._normalize_metadata_references(metadata.references)
    update_fields["reference"] = normalized_refs  # ← Only updates THIS document
    # ✗ Does NOT update related documents' referencedBy field
```

**Desired Behavior:**
```python
# For each reference in metadata.references:
# 1. Update THIS document: document.references = [...]
# 2. Update LINKED document: linkedDoc.referencedBy = [...]
# 3. Update MongoDB BOTH ways
# 4. Update Qdrant metadata for BOTH documents
# 5. Update FalkorDB edges (already correct via Cypher)
```

**Impact:** Conversation chains break; reverse lookups fail; linkage incomplete

---

### Issue #2: Qdrant Vectors Not Updated When References Change

**Problem:** When document metadata is updated (references, keywords, etc.), Qdrant payloads are stale. Only initial write happens; updates don't sync.

**Current Behavior:**
```python
# LangChainVectorService.replace_document() only called ONCE during initial processing
# If document.reference is later updated manually via API:
# → MongoDB updates fine
# → Qdrant vectors still have old metadata in payload
```

**Why It Matters:** Semantic search using reference metadata returns stale results

---

### Issue #3: No Mechanism to Cascade Updates

**Problem:** If document A is updated after creation, and now has different references, there's no way to:
1. Detect this change
2. Recalculate relationships
3. Re-sync to Qdrant and FalkorDB

**Current State:** One-time write only; no update hooks

---

### Issue #4: FalkorDB Edges Cleanup Risky

**Problem:** When reference cleanup is enabled, old edges are deleted BUT if a reference node doesn't exist, the query silently fails.

```cypher
# Current cleanup query:
MATCH (src:Letter {normCode:$norm})-[e:CITES|REPLIES_TO]->(dst:Letter) 
WHERE coalesce(e.source,'') <> 'manual' 
AND NOT (dst.normCode + '|' + type(e)) IN $keep 
DELETE e

# Issue: If referenced letter never got its own node, 
# the edge deletion might not work as expected
```

---

## 4. Detailed Improvement Recommendations

### Improvement #1: Implement Bidirectional Reference Sync

**File:** Create `services/reference_sync_service.py`

```python
from typing import List, Dict, Any, Optional
from datetime import datetime
from pymongo.database import Database
from bson.objectid import ObjectId

class ReferenceSyncService:
    """Ensures references are synchronized bidirectionally across all stores."""
    
    def __init__(self, db: Database):
        self.db = db
    
    async def sync_bidirectional_references(
        self,
        source_doc_id: str,
        references: List[Dict[str, Any]],
        organization_id: str,
        project_id: str,
    ) -> Dict[str, Any]:
        """
        Sync references bidirectionally for a document.
        
        When document A extracts references to B & C:
        1. Set A.references = [B, C]
        2. Set B.referencedBy += [A]
        3. Set C.referencedBy += [A]
        4. Update Qdrant payloads for A, B, C
        5. Update FalkorDB edges
        
        Args:
            source_doc_id: MongoDB ObjectId of source document
            references: List of {letterNo, date, text, ...}
            organization_id: Org scoping
            project_id: Project scoping
        
        Returns:
            {"synced": N, "updated_documents": [...]}
        """
        
        source_oid = ObjectId(source_doc_id)
        source_doc = await self.db.documents.find_one({"_id": source_oid})
        
        if not source_doc:
            raise ValueError(f"Source document {source_doc_id} not found")
        
        # 1. Update source document references
        await self.db.documents.update_one(
            {"_id": source_oid},
            {"$set": {
                "reference": references or [],
                "references_synced_at": datetime.utcnow(),
                "references_count": len(references or [])
            }}
        )
        
        # 2. Find and update all referenced documents
        updated_count = 0
        updated_doc_ids = []
        
        for ref in (references or []):
            ref_letter_no = ref.get("letterNo") or ref.get("letter_no")
            if not ref_letter_no:
                continue
            
            # Find target document by letterNo
            target_docs = await self.db.documents.find(
                {
                    "letterNo": ref_letter_no,
                    "organization_id": organization_id,
                    "project_id": project_id
                }
            ).to_list(None)
            
            for target_doc in target_docs:
                target_oid = target_doc["_id"]
                updated_doc_ids.append(str(target_oid))
                
                # Add source to target's referencedBy array (idempotent)
                await self.db.documents.update_one(
                    {"_id": target_oid},
                    {"$addToSet": {
                        "referencedBy": str(source_oid),
                        "referenced_by_ids": str(source_oid)
                    }},
                    upsert=False
                )
                
                updated_count += 1
        
        # 3. Invalidate cached vector payloads for all affected documents
        affected_ids = [str(source_oid)] + updated_doc_ids
        await self.db.sync_status.insert_one({
            "type": "reference_sync",
            "source_document_id": str(source_oid),
            "affected_document_ids": affected_ids,
            "vector_sync_needed": True,
            "timestamp": datetime.utcnow()
        })
        
        return {
            "synced": True,
            "source_document_id": str(source_oid),
            "updated_target_count": updated_count,
            "updated_document_ids": updated_doc_ids
        }
    
    async def update_linked_references_in_vectors(
        self,
        document_id: str,
    ) -> int:
        """
        Update Qdrant vector payloads for a document after reference changes.
        
        This MUST be called after sync_bidirectional_references() 
        to update Qdrant's stale metadata.
        """
        source_oid = ObjectId(document_id)
        
        # Fetch updated document
        doc = await self.db.documents.find_one({"_id": source_oid})
        if not doc:
            return 0
        
        # Fetch all vectors for this document
        vectors = await self.db.document_vectors.find(
            {"document_id": str(source_oid)}
        ).to_list(None)
        
        if not vectors:
            return 0
        
        # Update payloads in Qdrant (via LangChainVectorService)
        from services.langchain_vector_service import LangChainVectorService
        from config.document_processing_config import DocumentProcessingConfig
        
        config = DocumentProcessingConfig()  # Load from env
        qdrant_service = LangChainVectorService(config)
        
        if not qdrant_service.enabled:
            return 0
        
        # Rebuild payloads with fresh metadata
        payloads = []
        for vector in vectors:
            metadata = dict(vector.get("metadata") or {})
            
            # Update metadata fields that may have changed
            if doc.get("reference"):
                metadata["references"] = [
                    r.get("letterNo") for r in doc.get("reference", [])
                ]
            if doc.get("keywords"):
                metadata["keywords"] = doc.get("keywords", [])
            
            payloads.append({
                "text": vector.get("text"),
                "metadata": metadata,
                "checksum": vector.get("checksum_sha256")
            })
        
        # Replace in Qdrant
        updated_count = await qdrant_service.replace_document(payloads)
        
        return updated_count
```

**Integration Point:**
```python
# In DocumentService.process_document_async():

# After _upsert_document_metadata() and before graph ingestion:
reference_sync = ReferenceSyncService(db)
sync_result = await reference_sync.sync_bidirectional_references(
    source_doc_id=document_id,
    references=metadata.references or [],
    organization_id=org_id,
    project_id=proj_id
)

# Update Qdrant payloads
vector_update_count = await reference_sync.update_linked_references_in_vectors(
    document_id=document_id
)
```

---

### Improvement #2: Create Unified Vector Sync Service

**File:** Create `services/vector_sync_service.py`

```python
from typing import List, Dict, Any, Optional
import asyncio

class VectorSyncService:
    """Handles synchronization of vectors across MongoDB and Qdrant."""
    
    def __init__(
        self,
        db: Database,
        llamaindex_service: LlamaIndexVectorService,
        langchain_service: LangChainVectorService
    ):
        self.db = db
        self.llamaindex_service = llamaindex_service
        self.langchain_service = langchain_service
    
    async def sync_document_vectors(
        self,
        document_id: str,
        full_resync: bool = False
    ) -> Dict[str, int]:
        """
        Synchronize vectors across MongoDB and Qdrant.
        
        Args:
            document_id: Document to sync
            full_resync: If True, delete and recreate; if False, update payloads
        
        Returns:
            {"mongodb": N, "qdrant": N, "falkordb_refs": N}
        """
        
        mongo_updated = await self._sync_mongodb_vectors(document_id)
        qdrant_updated = await self._sync_qdrant_vectors(document_id, full_resync)
        
        return {
            "mongodb": mongo_updated,
            "qdrant": qdrant_updated
        }
    
    async def _sync_mongodb_vectors(self, document_id: str) -> int:
        """Ensure all vectors in MongoDB have fresh metadata."""
        # Implementation: Re-fetch document, update all its vectors' metadata
        pass
    
    async def _sync_qdrant_vectors(self, document_id: str, full_resync: bool) -> int:
        """Ensure all vectors in Qdrant have fresh metadata."""
        # Implementation: Call LangChainVectorService.replace_document()
        pass
```

---

### Improvement #3: Add Document Sync Endpoint

**File:** `routers/documents.py` - Add new endpoint

```python
@router.post("/documents/{document_id}/sync-references")
async def sync_document_references(
    document_id: str,
    current_user = Depends(get_current_user),
    db = Depends(get_database)
):
    """
    Manually trigger bidirectional reference sync and vector updates.
    
    Use this after updating document metadata via API.
    """
    try:
        doc = await db.documents.find_one(
            {"_id": ObjectId(document_id)}
        )
        
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        
        # Verify access
        if doc["organization_id"] != current_user.organization_id:
            raise HTTPException(status_code=403, detail="Access denied")
        
        reference_sync = ReferenceSyncService(db)
        
        # Sync references bidirectionally
        result = await reference_sync.sync_bidirectional_references(
            source_doc_id=document_id,
            references=doc.get("reference", []),
            organization_id=doc["organization_id"],
            project_id=doc["project_id"]
        )
        
        # Update Qdrant payloads
        vector_update = await reference_sync.update_linked_references_in_vectors(
            document_id=document_id
        )
        
        return {
            "status": "synced",
            "references_updated": result["updated_target_count"],
            "vectors_updated": vector_update,
            "affected_documents": result["updated_document_ids"]
        }
    
    except Exception as e:
        logger.error(f"Reference sync failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
```

---

### Improvement #4: Strengthen Qdrant Collection Initialization

**File:** `langchain_vector_service.py` - Update `_ensure_collection()`

```python
def _ensure_collection(self, client, qmodels, distance) -> None:
    """Safely ensure Qdrant collection exists with proper error handling."""
    
    try:
        # Check if collection exists
        try:
            existing = client.get_collection(self.config.qdrant_collection)
            logger.info(
                "Qdrant collection already exists: %s (vectors: %s)",
                self.config.qdrant_collection,
                existing.points_count
            )
            return
        except Exception as e:
            if "not found" not in str(e).lower():
                # Not a "not found" error; something else is wrong
                logger.error(f"Unexpected Qdrant error: {e}")
                raise
        
        # Collection doesn't exist; create it safely
        logger.info(
            "Creating new Qdrant collection: %s",
            self.config.qdrant_collection
        )
        
        vectors_config = qmodels.VectorParams(
            size=self.config.qdrant_vector_size,
            distance=distance,
        )
        
        try:
            # Create with timeout to prevent hanging
            collection = client.create_collection(
                collection_name=self.config.qdrant_collection,
                vectors_config=vectors_config,
                timeout=30
            )
            
            logger.info(
                "Successfully created Qdrant collection: %s",
                self.config.qdrant_collection
            )
            
        except Exception as e:
            # If creation fails, verify collection still doesn't exist
            # before raising (in case concurrent request just created it)
            try:
                client.get_collection(self.config.qdrant_collection)
                logger.info("Collection was created by concurrent process")
            except:
                logger.error(f"Failed to create Qdrant collection: {e}")
                raise
    
    except Exception as e:
        raise DocumentProcessingError(
            f"Qdrant collection initialization failed: {e}"
        ) from e
```

---

## 5. Implementation Checklist

### Phase 1: Critical Fixes (Do First - 2 Days)

- [ ] Fix Qdrant collection initialization (Improvement #4)
- [ ] Create ReferenceSyncService (Improvement #1)
- [ ] Add `/documents/{id}/sync-references` endpoint (Improvement #3)
- [ ] Test bidirectional reference syncing with sample data
- [ ] Verify Qdrant payloads update correctly

### Phase 2: Integration (Days 3-4)

- [ ] Integrate ReferenceSyncService into DocumentService.process_document_async()
- [ ] Add reference syncing to document update workflows
- [ ] Create VectorSyncService (Improvement #2)
- [ ] Add monitoring/logging for sync operations

### Phase 3: Testing & Validation (Days 5-7)

- [ ] Unit tests for ReferenceSyncService
- [ ] Integration tests: upload doc with refs → verify MongoDB + Qdrant + FalkorDB
- [ ] Test manual sync endpoint
- [ ] Load test: 100+ documents with cross-references
- [ ] Verify no race conditions in concurrent uploads

### Phase 4: Documentation & Deployment (Week 2)

- [ ] Document sync process
- [ ] Add to deployment checklist
- [ ] Create rollback plan
- [ ] Deploy to staging
- [ ] Monitor for 1 week before production

---

## 6. Monitoring & Health Checks

- `GET /api/storage-sync/status` now surfaces MongoDB and Qdrant chunk counts, the state of `vector_sync_status`, and documents missing backlinks. A non-empty `issues` array indicates the system is degraded.
- A background monitor (runs every 5 minutes) logs through `storage.sync` whenever mismatches, stale rows, or sync errors are detected, making automated alerting straightforward.
- Use these signals to drive dashboards and alerts alongside existing health probes.

---

## 7. Configuration Settings

**Add to your `.env`:**

```bash
# Vector Storage
VECTOR_STORE_ENABLED=true
QDRANT_ENABLED=true
DUAL_VECTOR_WRITE=true  # Both MongoDB and Qdrant
VECTOR_DUAL_WRITE_ENABLED=true  # Runtime toggle for Qdrant writes
VECTOR_VERIFY_AFTER_WRITE=false  # Enable strict post-write verification
AUTO_REFERENCE_SYNC=true  # Automatically sync references on upload
REFERENCE_SYNC_TIMEOUT=60  # seconds

# Storage Consistency
VERIFY_DUAL_WRITE=true  # Log warnings if MongoDB and Qdrant diverge
SYNC_CHECK_INTERVAL=3600  # seconds (background check)
MAX_PENDING_SYNCS=100  # Alert if more than this

# FalkorDB
FALKORDB_ENABLED=true
FALKORDB_CLEANUP_REFERENCES=true  # Delete old edges when updating
FALKORDB_EDGE_SOURCE_FILTER=true  # Only delete edges with source="parser"
```
## Implementation Updates (April 2025)

- Introduced `ReferenceSyncService`, the `/api/documents/{id}/sync-references` endpoint, and bidirectional schema updates so Mongo backlinks stay in sync with parser output.
- Added a vector sync tracker (`vector_sync_status`), new toggles (`VECTOR_DUAL_WRITE_ENABLED`, `VECTOR_VERIFY_AFTER_WRITE`), and `scripts/reconcile_vectors.py` for nightly reconciliation.
- Hardened FalkorDB ingestion by skipping non-parser edges during cleanup, batching the backfill script (`scripts/backfill_falkordb.py`), and stamping LangGraph-generated edges with `source="agent"`.
- Shipped `/api/storage-sync/status` plus a 5-minute background monitor that logs issues via the `storage.sync` logger.
- Captured the changes with new unit tests and documentation updates to anchor future rollout work.


---

## Summary: What Gets Stored Where

| Data | MongoDB | Qdrant | FalkorDB |
|------|---------|--------|----------|
| **Metadata (subject, letterNo, from/to, date)** | ✓ `documents` | ✓ payload | ✓ Letter node properties |
| **References parsed** | ✓ `documents.reference[]` | ✓ metadata field | ✓ CITES/REPLIES_TO edges |
| **ReferencedBy (backlinks)** | ✓ `documents.referencedBy[]` | ✗ **Not synced** | ✓ Implicit (reverse edges) |
| **Vector embeddings** | ✓ `document_vectors.embedding` | ✓ vector field | ✗ Not stored |
| **Chunk text** | ✓ `document_vectors.text` | ✓ payload | ✗ Not stored |
| **OCR text** | ✓ `documents.ocrText` | ✗ Not stored | ✗ Not stored |
| **Keywords/Clauses** | ✓ `documents.keywords/clauses` | ✓ metadata | ✗ Not stored |
| **Full text** | ✓ `documents.full_text` | ✓ Chunked in text | ✗ Not stored |

---

## Final Recommendations

1. **Implement Improvement #1-4 immediately** – They fix critical consistency gaps
2. **Monitor Qdrant carefully** – It's the most fragile store (no persistence between restarts)
3. **Use FalkorDB only for querying relationships**, not as primary storage
4. **Always double-write to MongoDB first**, then to Qdrant/FalkorDB
5. **Create a nightly reconciliation job** to detect and fix inconsistencies

Good luck with your implementation!



