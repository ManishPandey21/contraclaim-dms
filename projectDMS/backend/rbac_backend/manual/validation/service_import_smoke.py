#!/usr/bin/env python3
"""
Local service validation test for ContractDMS
Tests the core services defined in metadata_with_all_funtion.md
"""

import sys
import os
import traceback
from pathlib import Path

# Ensure 'rbac_backend' package is importable when running from repo root
BACKEND_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BACKEND_ROOT))

# Make stdout/stderr tolerate Unicode on Windows consoles
try:
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')  # type: ignore[attr-defined]
except Exception:
    pass

def test_service_imports():
    """Test if all core services can be imported"""
    print("=" * 60)
    print("TESTING SERVICE IMPORTS")
    print("=" * 60)

    services_to_test = [
        ("TextProcessingService", "rbac_backend.services.text_processing_service"),
        ("FileService", "rbac_backend.services.file_service"),
        ("OCRService", "rbac_backend.services.ocr_service"),
        ("OpenAIService", "rbac_backend.services.openai_service"),
        ("DatabaseService", "rbac_backend.services.database_service"),
        ("DocumentProcessor", "rbac_backend.services.document_processor"),
    ]

    results = {}

    for service_name, module_path in services_to_test:
        try:
            module = __import__(module_path, fromlist=[service_name])
            service_class = getattr(module, service_name)
            results[service_name] = {"status": "✅ PASS", "error": None}
            print(f"✅ {service_name}: Import successful")
        except Exception as e:
            results[service_name] = {"status": "❌ FAIL", "error": str(e)}
            print(f"❌ {service_name}: Import failed - {str(e)[:100]}")

    return results

def test_dataclass_imports():
    """Test if dataclasses and exceptions can be imported"""
    print("\n" + "=" * 60)
    print("TESTING DATACLASS AND EXCEPTION IMPORTS")
    print("=" * 60)

    results = {}

    items_to_test = [
        ("DocumentProcessingConfig", "rbac_backend.services.document_processor"),
        ("ParsedDocumentMetadata", "rbac_backend.services.document_processor"),
        ("ProcessingResult", "rbac_backend.services.document_processor"),
        ("DocumentProcessingError", "rbac_backend.services.document_processor"),
    ]

    for item_name, module_path in items_to_test:
        try:
            module = __import__(module_path, fromlist=[item_name])
            item_class = getattr(module, item_name)
            results[item_name] = {"status": "✅ PASS", "error": None}
            print(f"✅ {item_name}: Import successful")
        except Exception as e:
            results[item_name] = {"status": "❌ FAIL", "error": str(e)}
            print(f"❌ {item_name}: Import failed - {str(e)[:100]}")

    return results

def test_service_instantiation():
    """Test if services can be instantiated with basic config"""
    print("\n" + "=" * 60)
    print("TESTING SERVICE INSTANTIATION")
    print("=" * 60)

    results = {}

    try:
        # Test DocumentProcessingConfig
        from rbac_backend.config.document_processing_config import DocumentProcessingConfig
        config = DocumentProcessingConfig()
        results["DocumentProcessingConfig"] = "✅ PASS"
        print("✅ DocumentProcessingConfig: Instantiation successful")
    except Exception as e:
        results["DocumentProcessingConfig"] = f"❌ FAIL: {str(e)[:100]}"
        print(f"❌ DocumentProcessingConfig: Instantiation failed - {str(e)[:100]}")

    try:
        # Test TextProcessingService
        from rbac_backend.services.text_processing_service import TextProcessingService
        from rbac_backend.config.document_processing_config import DocumentProcessingConfig

        config = DocumentProcessingConfig()
        text_service = TextProcessingService(config)
        results["TextProcessingService"] = "✅ PASS"
        print("✅ TextProcessingService: Instantiation successful")
    except Exception as e:
        results["TextProcessingService"] = f"❌ FAIL: {str(e)[:100]}"
        print(f"❌ TextProcessingService: Instantiation failed - {str(e)[:100]}")

    try:
        # Test FileService
        from rbac_backend.services.file_service import FileService
        from rbac_backend.config.document_processing_config import DocumentProcessingConfig

        config = DocumentProcessingConfig()
        file_service = FileService(config)
        results["FileService"] = "✅ PASS"
        print("✅ FileService: Instantiation successful")
    except Exception as e:
        results["FileService"] = f"❌ FAIL: {str(e)[:100]}"
        print(f"❌ FileService: Instantiation failed - {str(e)[:100]}")

    return results

def test_text_processing_methods():
    """Test TextProcessingService methods"""
    print("\n" + "=" * 60)
    print("TESTING TEXT PROCESSING METHODS")
    print("=" * 60)

    results = {}

    try:
        from rbac_backend.services.text_processing_service import TextProcessingService
        from rbac_backend.config.document_processing_config import DocumentProcessingConfig

        config = DocumentProcessingConfig()
        text_service = TextProcessingService(config)

        # Test chunk_text method
        test_text = "This is a test document. " * 100
        chunks = text_service.chunk_text(test_text)

        if isinstance(chunks, list) and len(chunks) > 0:
            results["chunk_text"] = "✅ PASS"
            print(f"✅ chunk_text: Created {len(chunks)} chunks")
        else:
            results["chunk_text"] = "❌ FAIL: No chunks created"
            print("❌ chunk_text: No chunks created")

    except Exception as e:
        results["chunk_text"] = f"❌ FAIL: {str(e)[:100]}"
        print(f"❌ chunk_text: {str(e)[:100]}")

    try:
        # Test parse_extraction_report method
        from rbac_backend.services.text_processing_service import TextProcessingService
        from rbac_backend.config.document_processing_config import DocumentProcessingConfig

        config = DocumentProcessingConfig()
        text_service = TextProcessingService(config)

        # Test with sample report
        sample_report = """
        1) Date: 2024-01-15
        2) Letter No.: TEST-001
        3) From (Company): Test Company A
        4) To (Company): Test Company B
        5) Subject: Test Document
        6) References: REF-001, REF-002
        7) Summary: This is a test summary
        8) Key Words: test, document, sample
        9) Contractual Clauses: clause1, clause2
        10) Full content: This is the full content of the test document.
        """

        metadata = text_service.parse_extraction_report(sample_report)

        if hasattr(metadata, 'date') and metadata.date:
            results["parse_extraction_report"] = "✅ PASS"
            print("✅ parse_extraction_report: Parsing successful")
            print(f"   - Date: {metadata.date}")
            print(f"   - Subject: {metadata.subject}")
            print(f"   - Letter No: {metadata.letter_no}")
        else:
            results["parse_extraction_report"] = "❌ FAIL: No metadata parsed"
            print("❌ parse_extraction_report: No metadata parsed")

    except Exception as e:
        results["parse_extraction_report"] = f"❌ FAIL: {str(e)[:100]}"
        print(f"❌ parse_extraction_report: {str(e)[:100]}")

    return results

def test_factory_functions():
    """Test factory functions"""
    print("\n" + "=" * 60)
    print("TESTING FACTORY FUNCTIONS")
    print("=" * 60)

    results = {}

    try:
        from rbac_backend.services.document_processor import create_document_processor
        processor = create_document_processor()
        results["create_document_processor"] = "✅ PASS"
        print("✅ create_document_processor: Factory function works")
    except Exception as e:
        results["create_document_processor"] = f"❌ FAIL: {str(e)[:100]}"
        print(f"❌ create_document_processor: {str(e)[:100]}")

    return results

def main():
    """Run all tests"""
    print("ContractDMS Local Service Validation Test")
    print("Testing services defined in metadata_with_all_funtion.md")
    print(f"Current directory: {os.getcwd()}")

    all_results = {}

    # Test dataclass imports
    dataclass_results = test_dataclass_imports()
    all_results.update(dataclass_results)

    # Test service imports
    import_results = test_service_imports()
    all_results.update(import_results)

    # Test instantiation
    instantiation_results = test_service_instantiation()
    all_results.update(instantiation_results)

    # Test text processing methods
    method_results = test_text_processing_methods()
    all_results.update(method_results)

    # Test factory functions
    factory_results = test_factory_functions()
    all_results.update(factory_results)

    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    passed = sum(1 for result in all_results.values() if "✅ PASS" in str(result))
    total = len(all_results)

    print(f"Total tests: {total}")
    print(f"Passed: {passed}")
    print(f"Failed: {total - passed}")
    print(f"Success rate: {(passed/total)*100:.1f}%")

    if passed == total:
        print("\n🎉 ALL TESTS PASSED! Services are working correctly.")
        return 0
    else:
        print(f"\n⚠️  {total - passed} tests failed. Check the errors above.")
        return 1

if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except Exception as e:
        print(f"\n💥 Test runner crashed: {e}")
        traceback.print_exc()
        sys.exit(1)
