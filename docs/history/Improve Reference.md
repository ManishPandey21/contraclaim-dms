# Improve Reference

## Reference Tracking Improvement Plan

### Overview

This document outlines the implementation plan for an automatic reference tracking system that creates a chain of correspondence between letters. The system will automatically link new letters to existing correspondence chains based on subject matter and organizational context.

### Core Architecture

1. **Data Model Enhancements**

   - Add the following fields to the Document model:

   ```python
   class Document(BaseModel):
       # Existing fields...
       previous_letter_id: Optional[str] = None  # Immediate predecessor in chain
       chain_head_id: Optional[str] = None       # First letter in the chain
       chain_position: int = 1                   # Position in the chain (1-based)
   ```

2. **Automatic Chain Detection Logic**
   a. **Previous Letter Identification**

   ```python
   async def find_previous_letter(new_doc: Document, db: MongoClient) -> Optional[Document]:
       """
       Find the most relevant previous letter in the same correspondence chain
       """
       # Query for letters with same subject and opposite type
       query = {
           "project_id": new_doc.project_id,
           "organization_id": new_doc.organization_id,
           "subject": new_doc.subject,
           "uploadType": "outgoing" if new_doc.uploadType == "incoming" else "incoming"
       }
       return await db.documents.find_one(query, sort=[("date", pymongo.DESCENDING)])
   ```

   b. **Chain Inheritance Logic**

   ```python
   async def inherit_chain_references(new_doc: Document, previous_letter: Document, db: MongoClient):
       """
       Inherit references from previous letter and establish chain relationships
       """
       # Determine chain head (use previous letter's head or itself if starting a chain)
       chain_head_id = previous_letter.chain_head_id or str(previous_letter.id)

       # Update the new document with chain information
       update_data = {
           "previous_letter_id": str(previous_letter.id),
           "chain_head_id": chain_head_id,
           "chain_position": previous_letter.chain_position + 1,
           "references": [
               *previous_letter.references,
               DocumentReference(
                   documentId=str(previous_letter.id),
                   linkType="direct"
               )
           ]
       }
       await db.documents.update_one({"_id": new_doc.id}, {"$set": update_data})
   ```

3. **Integration with Existing API**

   - Modify Document Creation Endpoint

   ```python
   @router.post("/documents", response_model=Document)
   async def create_document(background_tasks: BackgroundTasks, ...):
       # ... existing document creation logic ...

       # After document creation, find and link to previous letter
       previous_letter = await find_previous_letter(created_document, db)

       if previous_letter:
           await inherit_chain_references(created_document, previous_letter, db)
           # Refetch the updated document
           created_document = await db.documents.find_one({"_id": created_document.id})

       return created_document
   ```

4. **New API Endpoints**
   a. **Get Full Correspondence Chain**

   ```python
   @router.get("/documents/{id}/chain", response_model=List[Document])
   async def get_correspondence_chain(id: str, db = Depends(get_db), current_user: CurrentUser = Depends(get_current_user)):
       """
       Retrieve the complete chain of correspondence for a document
       """
       # Authorization check (same as get_document)
       document = await db.documents.find_one({"_id": id})
       if not document:
           raise HTTPException(status_code=404, detail="Document not found")

       # Get all documents in the chain
       chain = []
       current = document

       while current:
           chain.append(current)
           if not current.get("previous_letter_id"):
               break
           current = await db.documents.find_one({"_id": current["previous_letter_id"]})

       # Return in chronological order (oldest first)
       return sorted(chain, key=lambda x: x.chain_position)
   ```

   b. **Visualize Correspondence Chain**

   ```python
   @router.get("/documents/{id}/chain-visualization")
   async def visualize_correspondence_chain(id: str, db = Depends(get_db), current_user: CurrentUser = Depends(get_current_user)):
       """
       Return data formatted for visualization of the correspondence chain
       """
       chain = await get_correspondence_chain(id, db, current_user)

       visualization_data = {
           "nodes": [],
           "edges": []
       }

       for doc in chain:
           visualization_data["nodes"].append({
               "id": str(doc.id),
               "label": f"{doc.uploadType}: {doc.letterNo}",
               "type": doc.uploadType,
               "date": doc.date.isoformat() if isinstance(doc.date, datetime) else doc.date
           })

           if doc.previous_letter_id:
               visualization_data["edges"].append({
                   "from": str(doc.previous_letter_id),
                   "to": str(doc.id),
                   "label": "references"
               })

       return visualization_data
   ```

5. **Enhanced Reference Matching**

   - **Fuzzy Subject Matching**

   ```python
   from fuzzywuzzy import fuzz

   async def find_previous_letter_enhanced(new_doc: Document, db: MongoClient, threshold=85):
       """
       Find previous letter using fuzzy matching on subject
       """
       # Get potential candidates
       candidates = await db.documents.find({
           "project_id": new_doc.project_id,
           "organization_id": new_doc.organization_id,
           "uploadType": "outgoing" if new_doc.uploadType == "incoming" else "incoming",
           "date": {"$lt": new_doc.date}
       }).sort("date", pymongo.DESCENDING).to_list(length=20)

       # Apply fuzzy matching
       scored_candidates = []
       for candidate in candidates:
           score = fuzz.token_sort_ratio(new_doc.subject, candidate.subject)
           if score >= threshold:
               scored_candidates.append((candidate, score))

       # Return best match
       if scored_candidates:
           return max(scored_candidates, key=lambda x: x[1])[0]

       return None
   ```

6. **Database Indexing**
   - Create indexes to support efficient chain queries:
   ```python
   # During application initialization
   async def create_reference_indexes(db: MongoClient):
       await db.documents.create_index([
           ("project_id", 1),
           ("organization_id", 1),
           ("subject", 1),
           ("uploadType", 1),
           ("date", -1)
       ])

       await db.documents.create_index([("chain_head_id", 1)])
       await db.documents.create_index([("previous_letter_id", 1)])
   ```

### Implementation Phases

- **Phase 1**: Basic Chain Implementation (2 weeks)
- **Phase 2**: Enhanced Matching (1 week)
- **Phase 3**: Visualization & UI Integration (1 week)
- **Phase 4**: Advanced Features (2 weeks)

### Expected Benefits

- Automatic Reference Tracking: Eliminates manual reference entry
- Complete Correspondence History: Provides full context for each letter
- Improved Searchability: Enables tracking entire conversation threads
- Visual Context: Helps users understand correspondence relationships
- Time Savings: Reduces manual administrative work
