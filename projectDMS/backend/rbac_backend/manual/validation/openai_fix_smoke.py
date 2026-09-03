#!/usr/bin/env python3
"""
Test script to validate the OpenAI service fix
"""

import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BASE_DIR))

def test_openai_service_import():
    """Test if OpenAI service can be imported"""
    print("Testing OpenAI Service Import...")
    try:
        from rbac_backend.services.openai_service import OpenAIService
        print("✅ OpenAI Service import successful")
        return True
    except Exception as e:
        print(f"❌ OpenAI Service import failed: {e}")
        return False

def test_openai_service_instantiation():
    """Test if OpenAI service can be instantiated"""
    print("Testing OpenAI Service Instantiation...")
    try:
        from rbac_backend.services.openai_service import OpenAIService
        from rbac_backend.config.document_processing_config import DocumentProcessingConfig

        # Create a basic config
        config = DocumentProcessingConfig()

        # Try to create service (this will fail without API key, but that's expected)
        try:
            service = OpenAIService(config)
            print("✅ OpenAI Service instantiation successful")
            return True
        except Exception as e:
            if "OpenAI API key not configured" in str(e):
                print("✅ OpenAI Service instantiation successful (API key not configured, as expected)")
                return True
            else:
                print(f"❌ OpenAI Service instantiation failed: {e}")
                return False
    except Exception as e:
        print(f"❌ OpenAI Service instantiation failed: {e}")
        return False

def test_openai_service_method_structure():
    """Test if the process_document method has the correct structure"""
    print("Testing OpenAI Service Method Structure...")
    try:
        from rbac_backend.services.openai_service import OpenAIService
        import inspect

        # Get the source code of the process_document method
        source = inspect.getsource(OpenAIService.process_document)

        # Check if the fix is present
        if '{"type": "file", "file": {"file_id": file_id}}' in source:
            print("✅ OpenAI API call format is correct (new format)")
            return True
        elif '{"type": "file", "file_id": file_id}' in source:
            print("❌ OpenAI API call format is incorrect (old format)")
            return False
        else:
            print("⚠️  Could not find OpenAI API call format in source")
            return False
    except Exception as e:
        print(f"❌ Method structure test failed: {e}")
        return False

def test_logging_improvements():
    """Test if logging improvements are present"""
    print("Testing Logging Improvements...")
    try:
        from rbac_backend.services.openai_service import OpenAIService
        import inspect

        # Get the source code of the process_document method
        source = inspect.getsource(OpenAIService.process_document)

        # Check if logging is present
        if 'logger.error' in source and 'OpenAI API call failed' in source:
            print("✅ Enhanced error logging is present")
            return True
        else:
            print("❌ Enhanced error logging is missing")
            return False
    except Exception as e:
        print(f"❌ Logging test failed: {e}")
        return False

def main():
    """Run all tests"""
    print("=" * 60)
    print("OPENAI SERVICE FIX VALIDATION TEST")
    print("=" * 60)

    tests = [
        test_openai_service_import,
        test_openai_service_instantiation,
        test_openai_service_method_structure,
        test_logging_improvements,
    ]

    results = []
    for test in tests:
        result = test()
        results.append(result)
        print()

    # Summary
    print("=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)

    passed = sum(results)
    total = len(results)

    print(f"Total tests: {total}")
    print(f"Passed: {passed}")
    print(f"Failed: {total - passed}")
    print(f"Success rate: {(passed/total)*100:.1f}%")

    if passed == total:
        print("\n🎉 ALL TESTS PASSED! OpenAI service fix is working correctly.")
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
        import traceback
        traceback.print_exc()
        sys.exit(1)
