# Qdrant and FalkorDB Test Suite

This test suite provides comprehensive testing of Qdrant vector storage and FalkorDB graph database using real PDF documents.

## Overview

The test script (`test_qdrant_falkor_with_data.py`) performs the following operations:

1. **PDF Processing**: Extracts text and metadata from PDF files
2. **Vector Storage**: Creates embeddings and stores them in Qdrant
3. **Graph Storage**: Creates letter nodes and relationships in FalkorDB
4. **Verification**: Validates that data is correctly stored in both systems
5. **Reporting**: Generates detailed reports on test results

## Prerequisites

### Required Python Packages

```bash
pip install PyPDF2 qdrant-client langchain-openai langchain-qdrant langgraph python-dotenv redis openai
# OCR fallback (scanned PDFs)
pip install ocrmypdf pdfplumber pdf2image pytesseract
```

### Environment Variables

Ensure the following environment variables are set:

```bash
# OpenAI (for embeddings)
OPENAI_API_KEY=your_openai_api_key

# Qdrant
QDRANT_URL=http://localhost:6333
QDRANT_API_KEY=your_qdrant_api_key  # Optional
QDRANT_COLLECTION=document_vectors

# FalkorDB
FALKORDB_ENABLED=true
FALKORDB_HOST=127.0.0.1
FALKORDB_PORT=6380
FALKORDB_GRAPH_NAME=contraclaim
FALKORDB_PASSWORD=  # Optional

# Vector Storage
VECTOR_DUAL_WRITE_ENABLED=true
```

### Services Running

Make sure the following services are running:

1. **Qdrant**: Running on port 6333 (default)

   ```bash
   docker run -p 6333:6333 qdrant/qdrant
   ```

2. **FalkorDB**: Running on port 6380 (default)
   ```bash
   docker run -p 6380:6379 falkordb/falkordb
   ```

## Usage

### Basic Test Run

Process all PDF files in the test data folder:

```bash
python backend/scripts/test_qdrant_falkor_with_data.py
```

### Clean Up Before Testing

Delete existing data and start fresh:

```bash
python backend/scripts/test_qdrant_falkor_with_data.py --cleanup
```

### Verify Existing Data Only

Check if data is already stored without processing:

```bash
python backend/scripts/test_qdrant_falkor_with_data.py --verify-only
```

### Custom Test Folder

Use a different folder for test data:

```bash
python backend/scripts/test_qdrant_falkor_with_data.py --test-folder "C:\path\to\your\pdfs"
```

## Test Data

The script expects PDF files in the following location by default:

```
C:\Users\santo\Downloads\KNPCC-06 Borewell (1)\KNPCC-06 Borewell\New folder\test data
```

Current test files (9 PDFs):

- AFC-PM-KNPCC-06 4930.pdf
- AFC-PM-KNPCC-06-4096.pdf
- AFC-PM-KNPCC-06-4399.pdf
- AFC-PM-KNPCC-06-4670.pdf
- AFC-PM-KNPCC-06-4905.pdf
- Kanpur-LET-JVTI-CPM-01634-E01.pdf
- Kanpur-LET-JVTI-CPM-01647-E01.PDF
- Kanpur-LET-JVTI-TBM-00371-E01.pdf
- Kanpur-LET-JVTI-TBM-00395-E01.PDF

## What the Script Does

### 1. PDF Text Extraction

- Uses PyPDF2 to extract text from each PDF
- Handles multi-page documents
- Reports character count for each document

### 2. Metadata Extraction

Extracts the following metadata from each document:

- **Letter Number**: Identifies the document (e.g., "Kanpur-LET-JVTI-CPM-01634-E01")
- **Date**: Document date
- **Subject**: Document subject line
- **References**: Other letters referenced in the document
- **Direction**: Incoming or outgoing letter

### 3. Text Chunking

- Splits text into manageable chunks (default: 1000 characters)
- Uses overlapping chunks (default: 200 character overlap)
- Attempts to break at sentence boundaries

### 4. Qdrant Vector Storage

For each document:

- Creates embeddings using OpenAI's text-embedding-3-small model
- Stores vectors in Qdrant collection
- Associates metadata with each vector
- Verifies storage by counting stored vectors

### 5. FalkorDB Graph Storage

For each document:

- Creates a Letter node with properties:
  - `normCode`: Normalized letter code
  - `code`: Original letter code
  - `direction`: incoming/outgoing
  - `subject`: Letter subject
  - `date`: Letter date
  - `project`: Project identifier
- Creates relationships:
  - `CITES`: References to other letters
  - `REPLIES_TO`: Reply relationships
- Verifies node creation

### 6. Verification

- Counts vectors stored in Qdrant for each document
- Verifies letter nodes exist in FalkorDB
- Reports any discrepancies

## Output

### Console Output

The script provides real-time progress updates:

```
================================================================================
QDRANT AND FALKORDB TEST SUITE
================================================================================
Test Folder: C:\Users\santo\Downloads\KNPCC-06 Borewell (1)\...
Cleanup: False
Verify Only: False
================================================================================

================================================================================
SERVICE STATUS
================================================================================
Qdrant Enabled: True
  URL: http://localhost:6333
  Collection: document_vectors

FalkorDB Enabled: True
  Host: 127.0.0.1
  Port: 6380
  Graph: contraclaim
================================================================================

Found 9 PDF files to process

================================================================================
Processing: AFC-PM-KNPCC-06 4930.pdf
================================================================================
Extracting text...
  Extracted 5432 characters
Extracting metadata...
  Letter No: AFC-PM-KNPCC-06-4930
  Date: 15/01/2024
  Subject: Regarding borewell construction...
  References: 2
Creating text chunks...
  Created 6 chunks
Storing vectors in Qdrant...
  Stored and verified 6 vectors
Creating FalkorDB node...
  Node created and verified
[OK] Successfully processed AFC-PM-KNPCC-06 4930.pdf
```

### Test Summary

At the end, a comprehensive summary is displayed:

```
================================================================================
TEST SUMMARY
================================================================================
Duration: 45.23 seconds
Total Files: 9
Successfully Processed: 9
Failed: 0

Qdrant Vectors Stored: 54
FalkorDB Nodes Created: 9
FalkorDB Relationships: 15

File Results:
  [OK] AFC-PM-KNPCC-06 4930.pdf
      text_length: 5432
      letter_no: AFC-PM-KNPCC-06-4930
      chunks: 6
      qdrant_vectors: 6
      falkor_node: Created
  [OK] Kanpur-LET-JVTI-CPM-01634-E01.pdf
      text_length: 3821
      letter_no: Kanpur-LET-JVTI-CPM-01634-E01
      chunks: 4
      qdrant_vectors: 4
      falkor_node: Created
  ...
================================================================================
```

### Log File

Detailed logs are written to `test_qdrant_falkor.log` including:

- Timestamp for each operation
- Detailed error messages
- Service initialization details
- Query execution logs

## Troubleshooting

### Common Issues

#### 1. PyPDF2 Not Installed

```
Error: PyPDF2 not installed
Solution: pip install PyPDF2
```

#### 2. Qdrant Connection Failed

```
Error: Cannot connect to Qdrant
Solution:
- Check if Qdrant is running: docker ps
- Verify QDRANT_URL is correct
- Check firewall settings
```

#### 3. FalkorDB Connection Failed

```
Error: Cannot connect to FalkorDB
Solution:
- Check if FalkorDB is running: docker ps
- Verify FALKORDB_HOST and FALKORDB_PORT
- Check Redis connection
```

#### 4. OpenAI API Key Missing

```
Error: OpenAI API key not set
Solution: export OPENAI_API_KEY=your_key
```

#### 5. Test Folder Not Found

```
Error: Test folder not found
Solution: Use --test-folder to specify correct path
```

## Verification Commands

### Check Qdrant Storage

```python
from qdrant_client import QdrantClient

client = QdrantClient(url="http://localhost:6333")
collection_info = client.get_collection("document_vectors")
print(f"Total vectors: {collection_info.points_count}")
```

### Check FalkorDB Storage

```python
from rbac_backend.services.falkor_graph_service import FalkorGraphService

service = FalkorGraphService()
total = service.debug_count_letters()
print(f"Total letters: {total}")

# List all letters
letters = service.debug_list_all_letters(limit=20)
for letter in letters:
    print(f"  - {letter['code']}: {letter['subject']}")
```

## Performance Expectations

For 9 PDF files (typical test data):

- **Processing Time**: 30-60 seconds
- **Qdrant Vectors**: 40-60 vectors (depending on document length)
- **FalkorDB Nodes**: 9 nodes
- **Relationships**: 10-20 relationships (depending on references)

## Next Steps

After successful testing:

1. **Verify Data Quality**: Check if extracted metadata is accurate
2. **Test Search**: Try semantic search queries in Qdrant
3. **Test Graph Queries**: Query letter relationships in FalkorDB
4. **Integration Testing**: Test with the full application
5. **Performance Testing**: Test with larger document sets

## Additional Scripts

### Quick Verification Script

Create a simple verification script:

```python
# verify_storage.py
from rbac_backend.config.document_processing_config import DocumentProcessingConfig
from rbac_backend.services.falkor_graph_service import FalkorGraphService
from qdrant_client import QdrantClient

config = DocumentProcessingConfig()

# Check Qdrant
client = QdrantClient(url=config.qdrant_url)
info = client.get_collection(config.qdrant_collection)
print(f"Qdrant vectors: {info.points_count}")

# Check FalkorDB
falkor = FalkorGraphService()
count = falkor.debug_count_letters()
print(f"FalkorDB letters: {count}")
```

## Support

For issues or questions:

1. Check the log file: `test_qdrant_falkor.log`
2. Review error messages in console output
3. Verify all prerequisites are met
4. Check service status and connectivity

## License

This test suite is part of the ContraClaim DMS project.
