"""
Comprehensive test script for Qdrant and FalkorDB using real PDF test data.

This script:
1. Loads PDF files from the test data folder
2. Extracts text and metadata
3. Creates embeddings and stores in Qdrant
4. Creates letter nodes and relationships in FalkorDB
5. Verifies storage and generates detailed reports

Usage:
    python backend/scripts/test_qdrant_falkor_with_data.py
    python backend/scripts/test_qdrant_falkor_with_data.py --cleanup  # Clean before testing
    python backend/scripts/test_qdrant_falkor_with_data.py --verify-only  # Only verify existing data
"""

import asyncio
import logging
import os
import re
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
load_dotenv()

try:
    import PyPDF2
except ImportError:
    PyPDF2 = None

try:
    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qmodels
except ImportError:
    QdrantClient = None
    qmodels = None

from rbac_backend.config.document_processing_config import DocumentProcessingConfig
from rbac_backend.services.falkor_graph_service import FalkorGraphService, normalize_letter_code
from rbac_backend.services.langchain_vector_service import LangChainVectorService
from rbac_backend.services.ocr_service import OCRService, DocumentProcessingError

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('test_qdrant_falkor.log')
    ]
)
logger = logging.getLogger(__name__)

# Test data folder path
TEST_DATA_FOLDER = r"C:\Users\santo\Downloads\KNPCC-06 Borewell (1)\KNPCC-06 Borewell\New folder\test data"


class TestResult:
    """Container for test results"""
    def __init__(self):
        self.total_files = 0
        self.processed_files = 0
        self.failed_files = 0
        self.qdrant_vectors_stored = 0
        self.falkor_nodes_created = 0
        self.falkor_relationships_created = 0
        self.errors: List[str] = []
        self.file_results: List[Dict[str, Any]] = []
        self.start_time = time.time()

    def add_file_result(self, filename: str, success: bool, details: Dict[str, Any]):
        """Add result for a processed file"""
        self.file_results.append({
            'filename': filename,
            'success': success,
            'details': details
        })
        if success:
            self.processed_files += 1
        else:
            self.failed_files += 1

    def add_error(self, error: str):
        """Add an error message"""
        self.errors.append(error)

    def get_duration(self) -> float:
        """Get test duration in seconds"""
        return time.time() - self.start_time

    def print_summary(self):
        """Print test summary"""
        duration = self.get_duration()

        print("\n" + "=" * 80)
        print("TEST SUMMARY")
        print("=" * 80)
        print(f"Duration: {duration:.2f} seconds")
        print(f"Total Files: {self.total_files}")
        print(f"Successfully Processed: {self.processed_files}")
        print(f"Failed: {self.failed_files}")
        print(f"\nQdrant Vectors Stored: {self.qdrant_vectors_stored}")
        print(f"FalkorDB Nodes Created: {self.falkor_nodes_created}")
        print(f"FalkorDB Relationships: {self.falkor_relationships_created}")

        if self.errors:
            print(f"\nErrors ({len(self.errors)}):")
            for i, error in enumerate(self.errors, 1):
                print(f"  {i}. {error}")

        print("\nFile Results:")
        for result in self.file_results:
            status = "[OK]" if result["success"] else "[FAIL]"
            print(f"  {status} {result['filename']}")
            if result["details"]:
                for key, value in result["details"].items():
                    print(f"      {key}: {value}")
        print("=" * 80)


class PDFProcessor:
    """Process PDF files and extract text and metadata"""

    @staticmethod
    def extract_text_from_pdf(pdf_path: str) -> Optional[str]:
        """Extract text from PDF file"""
        if PyPDF2 is None:
            logger.error("PyPDF2 not installed. Install with: pip install PyPDF2")
            return None

        try:
            with open(pdf_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                text = ""
                for page in pdf_reader.pages:
                    page_text = page.extract_text() or ""
                    text += page_text + "\n"

                text = text.strip()
                if not text:
                    logger.warning("No text extracted from %s; file may be scanned or image-only", pdf_path)
                    return None

                return text
        except Exception as e:
            logger.error(f"Failed to extract text from {pdf_path}: {e}")
            return None

    @staticmethod
    def extract_metadata(text: str, filename: str) -> Dict[str, Any]:
        """Extract metadata from text content"""
        metadata = {
            'letter_no': None,
            'date': None,
            'subject': None,
            'from_company': None,
            'to_company': None,
            'references': [],
            'direction': 'incoming'
        }

        # Extract letter number from filename or content
        # Pattern 1: Kanpur-LET-JVTI-CPM-01634-E01
        # Pattern 2: AFC-PM-KNPCC-06-4930
        letter_patterns = [
            r'([A-Z]+-[A-Z]+-[A-Z]+-[A-Z]+-\d+-[A-Z]\d+)',  # Kanpur pattern
            r'([A-Z]+-[A-Z]+-[A-Z]+-\d+-\d+)',  # AFC pattern
            r'Ref\s*[:.]\s*([A-Z0-9/-]+)',  # Reference pattern
            r'Letter\s*No\s*[:.]\s*([A-Z0-9/-]+)',  # Letter No pattern
        ]

        # Try filename first
        for pattern in letter_patterns:
            match = re.search(pattern, filename)
            if match:
                metadata['letter_no'] = match.group(1)
                break

        # If not in filename, try content
        if not metadata['letter_no'] and text:
            for pattern in letter_patterns:
                match = re.search(pattern, text[:1000])  # Search first 1000 chars
                if match:
                    metadata['letter_no'] = match.group(1)
                    break

        # Extract date
        date_patterns = [
            r'Date\s*[:.]\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
            r'Dated\s*[:.]\s*(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
            r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
        ]

        if text:
            for pattern in date_patterns:
                match = re.search(pattern, text[:1000])
                if match:
                    metadata['date'] = match.group(1)
                    break

        # Extract subject
        subject_patterns = [
            r'Subject\s*[:.]\s*(.+?)(?:\n|$)',
            r'Sub\s*[:.]\s*(.+?)(?:\n|$)',
            r'Re\s*[:.]\s*(.+?)(?:\n|$)',
        ]

        if text:
            for pattern in subject_patterns:
                match = re.search(pattern, text[:2000], re.IGNORECASE)
                if match:
                    metadata['subject'] = match.group(1).strip()[:200]  # Limit length
                    break

        # Extract references (other letter numbers mentioned)
        if text:
            ref_patterns = [
                r'Ref\s*[:.]\s*([A-Z0-9/-]+)',
                r'Reference\s*[:.]\s*([A-Z0-9/-]+)',
                r'In\s+reply\s+to\s+([A-Z0-9/-]+)',
            ]

            for pattern in ref_patterns:
                matches = re.findall(pattern, text[:2000])
                metadata['references'].extend(matches)

        # Determine direction from filename or content
        if 'AFC' in filename.upper() or (text and 'AFC' in text[:500]):
            metadata['direction'] = 'outgoing'

        return metadata

    @staticmethod
    def chunk_text(text: str, chunk_size: int = 1000, overlap: int = 200) -> List[str]:
        """Split text into overlapping chunks"""
        if not text:
            return []

        chunks = []
        start = 0
        text_length = len(text)

        while start < text_length:
            end = start + chunk_size
            chunk = text[start:end]

            # Try to break at sentence boundary
            if end < text_length:
                last_period = chunk.rfind('.')
                last_newline = chunk.rfind('\n')
                break_point = max(last_period, last_newline)

                if break_point > chunk_size * 0.5:  # Only break if we're past halfway
                    chunk = chunk[:break_point + 1]
                    end = start + break_point + 1

            chunks.append(chunk.strip())
            start = end - overlap

        return [c for c in chunks if c]  # Remove empty chunks


class QdrantTester:
    """Test Qdrant vector storage"""

    def __init__(self, config: DocumentProcessingConfig):
        self.config = config
        self.service = LangChainVectorService(config)
        self.client: Optional[QdrantClient] = None

        if QdrantClient and config.qdrant_enabled:
            try:
                self.client = QdrantClient(
                    url=config.qdrant_url,
                    api_key=config.qdrant_api_key,
                    timeout=10.0
                )
            except Exception as e:
                logger.error(f"Failed to initialize Qdrant client: {e}")

    async def store_document_vectors(
        self,
        document_id: str,
        chunks: List[str],
        metadata: Dict[str, Any]
    ) -> int:
        """Store document chunks as vectors in Qdrant"""
        if not self.service.enabled:
            logger.warning("Qdrant service not enabled")
            return 0

        # Prepare payloads
        payloads = []
        for i, chunk in enumerate(chunks):
            chunk_metadata = metadata.copy()
            chunk_metadata['document_id'] = document_id
            chunk_metadata['chunk_index'] = i
            chunk_metadata['chunk_id'] = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{document_id}:{i}"))

            payloads.append({
                'text': chunk,
                'metadata': chunk_metadata,
                'checksum': str(hash(chunk))
            })

        # Store in Qdrant
        try:
            count = await self.service.replace_document(payloads)
            logger.info(f"Stored {count} vectors for document {document_id}")
            return count
        except Exception as e:
            logger.error(f"Failed to store vectors for {document_id}: {e}")
            return 0

    def verify_storage(self, document_id: str) -> Optional[int]:
        """Verify vectors are stored in Qdrant"""
        if not self.client or not self.config.qdrant_enabled:
            return None

        try:
            result = self.client.count(
                collection_name=self.config.qdrant_collection,
                count_filter=qmodels.Filter(
                    must=[
                        qmodels.FieldCondition(
                            key="metadata.document_id",
                            match=qmodels.MatchValue(value=document_id)
                        )
                    ]
                )
            )
            return result.count
        except Exception as e:
            logger.error(f"Failed to verify Qdrant storage for {document_id}: {e}")
            return None

    def cleanup_collection(self):
        """Clean up test collection"""
        if not self.client or not self.config.qdrant_enabled:
            return

        try:
            self.client.delete_collection(self.config.qdrant_collection)
            logger.info(f"Deleted Qdrant collection: {self.config.qdrant_collection}")
        except Exception as e:
            logger.warning(f"Failed to delete collection: {e}")


class FalkorTester:
    """Test FalkorDB graph storage"""

    def __init__(self):
        self.service = FalkorGraphService()

    def create_letter_node(
        self,
        letter_no: str,
        metadata: Dict[str, Any]
    ) -> bool:
        """Create a letter node in FalkorDB"""
        if not self.service.enabled:
            logger.warning("FalkorDB service not enabled")
            return False

        try:
            letter_data = {
                'code': letter_no,
                'normCode': normalize_letter_code(letter_no),
                'direction': metadata.get('direction', 'incoming'),
                'subject': metadata.get('subject', ''),
                'date': metadata.get('date'),
                'project': 'KNPCC-06'
            }

            # Extract references for relationships
            references = []
            for ref in metadata.get('references', []):
                if ref and ref != letter_no:
                    references.append({
                        'code': ref,
                        'normCode': normalize_letter_code(ref),
                        'type': 'CITES',
                        'source': 'test_script'
                    })

            self.service.upsert_letter_with_refs(
                letter=letter_data,
                references=references,
                cleanup=True
            )

            logger.info(f"Created FalkorDB node for {letter_no} with {len(references)} references")
            return True

        except Exception as e:
            logger.error(f"Failed to create FalkorDB node for {letter_no}: {e}")
            return False

    def verify_node(self, letter_no: str) -> Optional[Dict[str, Any]]:
        """Verify letter node exists in FalkorDB"""
        if not self.service.enabled:
            return None

        try:
            norm_code = normalize_letter_code(letter_no)
            node = self.service.get_letter(norm_code)
            return node
        except Exception as e:
            logger.error(f"Failed to verify FalkorDB node for {letter_no}: {e}")
            return None

    def get_statistics(self) -> Dict[str, int]:
        """Get FalkorDB statistics"""
        if not self.service.enabled:
            return {}

        try:
            total_letters = self.service.debug_count_letters()
            return {
                'total_letters': total_letters
            }
        except Exception as e:
            logger.error(f"Failed to get FalkorDB statistics: {e}")
            return {}


async def process_test_files(
    test_folder: str,
    cleanup: bool = False,
    verify_only: bool = False
) -> TestResult:
    """Process all test files and store in Qdrant and FalkorDB"""

    result = TestResult()

    # Initialize services
    config = DocumentProcessingConfig()
    qdrant_tester = QdrantTester(config)
    falkor_tester = FalkorTester()
    pdf_processor = PDFProcessor()
    ocr_service = OCRService(config)

    # Check if services are enabled
    print("\n" + "="*80)
    print("SERVICE STATUS")
    print("="*80)
    print(f"Qdrant Enabled: {qdrant_tester.service.enabled}")
    if qdrant_tester.service.enabled:
        print(f"  URL: {config.qdrant_url}")
        print(f"  Collection: {config.qdrant_collection}")
    else:
        print(f"  Error: {qdrant_tester.service._init_error}")

    print(f"\nFalkorDB Enabled: {falkor_tester.service.enabled}")
    if falkor_tester.service.enabled:
        print(f"  Host: {falkor_tester.service.config.host}")
        print(f"  Port: {falkor_tester.service.config.port}")
        print(f"  Graph: {falkor_tester.service.config.graph_name}")
    print("="*80)

    # Cleanup if requested
    if cleanup and not verify_only:
        print("\nCleaning up existing data...")
        qdrant_tester.cleanup_collection()
        # Re-initialize services after cleanup so the collection is recreated
        qdrant_tester = QdrantTester(config)
        falkor_tester = FalkorTester()

    # Get list of PDF files
    test_path = Path(test_folder)
    if not test_path.exists():
        result.add_error(f"Test folder not found: {test_folder}")
        return result

    candidates = list(test_path.glob("*.pdf")) + list(test_path.glob("*.PDF"))
    # Deduplicate paths (Windows glob is case-insensitive, so combining patterns can double count)
    pdf_files = sorted({p.resolve() for p in candidates})
    result.total_files = len(pdf_files)

    print(f"\nFound {len(pdf_files)} PDF files to process")

    # Process each file
    for pdf_file in pdf_files:
        filename = pdf_file.name
        print(f"\n{'='*80}")
        print(f"Processing: {filename}")
        print(f"{'='*80}")

        file_details = {}

        try:
            if verify_only:
                # Only verify existing data
                document_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, filename))

                # Verify Qdrant
                qdrant_count = qdrant_tester.verify_storage(document_id)
                file_details['qdrant_vectors'] = qdrant_count if qdrant_count is not None else 'N/A'

                # Verify FalkorDB
                metadata = pdf_processor.extract_metadata("", filename)
                if metadata['letter_no']:
                    falkor_node = falkor_tester.verify_node(metadata['letter_no'])
                    file_details['falkor_node'] = 'Found' if falkor_node else 'Not Found'

                result.add_file_result(filename, True, file_details)
                continue

            # Extract text
            print("Extracting text...")
            text = pdf_processor.extract_text_from_pdf(str(pdf_file))
            if not text:
                print("  No text found; attempting OCR fallback...")
                try:
                    processed_path, raw_text = await ocr_service.process_pdf(pdf_file)
                    text = (raw_text or "").strip()
                    if not text and processed_path:
                        text = pdf_processor.extract_text_from_pdf(str(processed_path))
                    if text:
                        print(f"  OCR extracted {len(text)} characters")
                    else:
                        result.add_error(f"Failed to extract text from {filename}")
                        result.add_file_result(filename, False, {'error': 'Text extraction failed'})
                        continue
                except DocumentProcessingError as e:
                    result.add_error(f"OCR failed for {filename}: {e}")
                    result.add_file_result(filename, False, {'error': f'OCR failed: {e}'})
                    continue
                except Exception as e:
                    result.add_error(f"OCR unexpected error for {filename}: {e}")
                    result.add_file_result(filename, False, {'error': f'OCR error: {e}'})
                    continue

            file_details['text_length'] = len(text)
            print(f"  Extracted {len(text)} characters")

            # Extract metadata
            print("Extracting metadata...")
            metadata = pdf_processor.extract_metadata(text, filename)
            file_details['letter_no'] = metadata['letter_no'] or 'Not found'
            file_details['date'] = metadata['date'] or 'Not found'
            file_details['references'] = len(metadata['references'])

            print(f"  Letter No: {metadata['letter_no']}")
            print(f"  Date: {metadata['date']}")
            print(f"  Subject: {metadata['subject'][:50] if metadata['subject'] else 'Not found'}...")
            print(f"  References: {len(metadata['references'])}")

            # Create chunks
            print("Creating text chunks...")
            chunks = pdf_processor.chunk_text(text, chunk_size=1000, overlap=200)
            file_details['chunks'] = len(chunks)
            print(f"  Created {len(chunks)} chunks")

            # Store in Qdrant
            if qdrant_tester.service.enabled:
                print("Storing vectors in Qdrant...")
                document_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, filename))
                vector_count = await qdrant_tester.store_document_vectors(
                    document_id,
                    chunks,
                    metadata
                )
                file_details['qdrant_vectors'] = vector_count
                result.qdrant_vectors_stored += vector_count

                # Verify storage
                verified_count = qdrant_tester.verify_storage(document_id)
                file_details['qdrant_verified'] = verified_count
                print(f"  Stored and verified {verified_count} vectors")

            # Store in FalkorDB
            if falkor_tester.service.enabled and metadata['letter_no']:
                print("Creating FalkorDB node...")
                success = falkor_tester.create_letter_node(metadata['letter_no'], metadata)
                if success:
                    result.falkor_nodes_created += 1
                    result.falkor_relationships_created += len(metadata['references'])
                    file_details['falkor_node'] = 'Created'

                    # Verify node
                    node = falkor_tester.verify_node(metadata['letter_no'])
                    file_details['falkor_verified'] = 'Yes' if node else 'No'
                    print(f"  Node created and verified")
                else:
                    file_details['falkor_node'] = 'Failed'

            result.add_file_result(filename, True, file_details)
            print(f"[OK] Successfully processed {filename}")

        except Exception as e:
            error_msg = f"Error processing {filename}: {str(e)}"
            logger.error(error_msg)
            result.add_error(error_msg)
            result.add_file_result(filename, False, {'error': str(e)})

    # Get final statistics
    print("\n" + "="*80)
    print("FINAL VERIFICATION")
    print("="*80)

    if falkor_tester.service.enabled:
        stats = falkor_tester.get_statistics()
        print(f"FalkorDB Total Letters: {stats.get('total_letters', 'N/A')}")

    return result


def main():
    """Main entry point"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Test Qdrant and FalkorDB with real PDF data"
    )
    parser.add_argument(
        '--cleanup',
        action='store_true',
        help='Clean up existing data before testing'
    )
    parser.add_argument(
        '--verify-only',
        action='store_true',
        help='Only verify existing data without processing'
    )
    parser.add_argument(
        '--test-folder',
        default=TEST_DATA_FOLDER,
        help='Path to test data folder'
    )

    args = parser.parse_args()

    print("\n" + "="*80)
    print("QDRANT AND FALKORDB TEST SUITE")
    print("="*80)
    print(f"Test Folder: {args.test_folder}")
    print(f"Cleanup: {args.cleanup}")
    print(f"Verify Only: {args.verify_only}")
    print("="*80)

    # Run tests
    result = asyncio.run(process_test_files(
        args.test_folder,
        cleanup=args.cleanup,
        verify_only=args.verify_only
    ))

    # Print summary
    result.print_summary()

    # Exit with appropriate code
    sys.exit(0 if result.failed_files == 0 else 1)


if __name__ == "__main__":
    main()
