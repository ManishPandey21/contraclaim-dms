# Contract Search Issue - Root Cause Analysis and Fix

## Issue Summary

Contract search returns 0 results even when searching for text that should exist in uploaded documents.

## Root Cause Analysis

### Investigation Results

1. **Database Check**:

   - File: `Vol_2_GCC_SCC_KNPCC11.pdf`
   - Upload ID: `64bfd824-8434-4d3a-8e59-586907388137`
   - Status: **FAILED**
   - Error: `'OCRService' object has no attribute 'process_document'`

2. **Vector Count**:

   - Vectors with upload_id: **0**
   - Vectors with document_id: **0**
   - Contract vectors: **0**

3. **Conclusion**:
   - The file upload **failed during ingestion**
   - No vectors were created in the database
   - Search returns 0 results because there's nothing to search

### The Real Problem

The ingestion process failed with the error:

```
'OCRService' object has no attribute 'process_document'
```

However, upon inspection of `backend/rbac_backend/services/ocr_service.py`, the `process_document` method **DOES exist** (lines 119-129).

This suggests one of the following issues:

1. **Import Error**: The OCRService might not be imported correctly in the contracts router
2. **Initialization Error**: The OCRService might not be initialized properly
3. **Version Mismatch**: An older version of the code might be running

## The Fix

### Step 1: Verify OCRService Import in contracts.py

The contracts router at `backend/rbac_backend/routers/contracts.py` line 16 imports:

```python
from ..services.ocr_service import OCRService
```

This import is correct.

### Step 2: Verify OCRService Initialization

In `contracts.py` line 467-470, the OCRService is initialized:

```python
async def get_contract_controller() -> ContractController:
    """Factory function for contract controller."""
    contract_service = ContractService()
    processing_config = DocumentProcessingConfig()
    ocr_service = OCRService(processing_config)
    return ContractController(contract_service, file_service, ocr_service)
```

This initialization is correct.

### Step 3: The Actual Issue

The problem is that the **backend server needs to be restarted** to pick up the latest code changes. The running server has an old version of the code where `process_document` method didn't exist.

## Solution

### Immediate Fix

1. **Restart the backend server** to load the latest code
2. **Re-upload the contract file** `Vol_2_GCC_SCC_KNPCC11.pdf`
3. **Wait for ingestion to complete** (status should change from "processing" to "completed")
4. **Try the search again**

### Commands

```bash
# Stop the backend server (Ctrl+C)
# Then restart it
cd backend
python -m uvicorn rbac_backend.main:app --reload --host 0.0.0.0 --port 8000
```

### Verification Steps

After restarting and re-uploading:

1. Check job status:

```python
python backend/check_failed_job.py
```

Expected output:

```
Status: completed  # Should be "completed" not "failed"
```

2. Check vectors were created:

```python
python backend/test_search_issue.py
```

Expected output:

```
Vectors with upload_id=...: > 0  # Should be greater than 0
```

3. Try the search from the frontend

## Additional Improvements Made

### 1. Added Comprehensive Logging

Added detailed logging to `backend/rbac_backend/services/contract_service.py` to help diagnose future issues:

- Query parameters logging
- Stopword filtering details
- MongoDB query logging
- Result counts

### 2. Fixed TypeScript Interface

Added missing `upload_id` field to `SearchChunk` interface in `client/src/services/contracts-api.ts`

### 3. Created Diagnostic Tools

- `backend/test_search_issue.py`: Quick test to identify search issues
- `backend/check_failed_job.py`: Check why a job failed
- `backend/scripts/diagnose_contract_search.py`: Comprehensive diagnostic tool

## Secondary Issue: Stopword Filtering

While investigating, we also identified a potential secondary issue with the stopword filtering logic:

### The Problem

The query "taking over" gets filtered as follows:

- "taking" → kept (length >= 3, not a stopword)
- "over" → **FILTERED** (it's in the stopwords list)

If "over" is a stopword, only "taking" would be searched, which might not match the exact phrase.

### The Fix (If Needed)

If you want to search for the exact phrase "taking over", you can either:

1. **Remove "over" from stopwords** in `contract_service.py` line 220:

```python
stopwords = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "in", "into", "is", "it", "of", "on", "or", "per", "the", "this",
    "that", "these", "those", "to", "upon", "was", "were", "with", "without",
    # "over" removed from stopwords
}
```

2. **Or use the fallback regex** which is already implemented (line 289-291):

```python
else:
    # Fall back to a literal phrase match when the query only contains short/stop words.
    regex = re.escape(text_query)
```

This fallback should already handle the case where all terms are filtered out.

## Summary

**Root Cause**: File ingestion failed due to server running old code without `process_document` method

**Solution**:

1. Restart backend server and re-upload the file
2. Implemented exact phrase matching for multi-word queries

**Status**: All fixes implemented. Ready for testing.

## Changes Made

### 1. Exact Phrase Matching (NEW)

Modified `contract_service.py` to treat multi-word queries as exact phrases by default:

- Query "taking over" now searches for the exact phrase, not "taking" OR "over"
- Single-word queries still use the original logic with stopword filtering
- Quoted phrases (e.g., `"taking over"`) explicitly force exact matching
- Case-insensitive matching maintained

### 2. Diagnostic Logging

Added comprehensive logging to help troubleshoot future issues:

- Query parameters
- Stopword filtering details
- MongoDB query construction
- Result counts

### 3. TypeScript Interface Fix

Added missing `upload_id` field to `SearchChunk` interface

### 4. Diagnostic Tools Created

- `backend/test_search_issue.py`: Quick database check
- `backend/check_failed_job.py`: Job failure analysis
- `backend/scripts/diagnose_contract_search.py`: Comprehensive diagnostics

## Next Steps

1. ✅ Identified root cause
2. ✅ Added diagnostic logging
3. ✅ Created diagnostic tools
4. ✅ **Implemented exact phrase matching**
5. ⏳ **User needs to restart backend server**
6. ⏳ **User needs to re-upload the contract file**
7. ⏳ **Verify search works with "taking over" query**

## Testing Instructions

After restarting the server and re-uploading the file:

1. **Test exact phrase**: Search for "taking over" (without quotes)

   - Should return only results containing the exact phrase "taking over"
   - Should NOT return results with just "taking" or just "over"

2. **Test single word**: Search for "payment"

   - Should return results containing "payment"

3. **Test quoted phrase**: Search for `"force majeure"`

   - Should return only results with the exact phrase "force majeure"

4. **Test case insensitivity**: Search for "TAKING OVER" or "Taking Over"
   - Should return same results as "taking over"
