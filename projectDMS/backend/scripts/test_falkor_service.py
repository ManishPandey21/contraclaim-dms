#!/usr/bin/env python3
"""
Test script for FalkorGraphService
"""

import sys
import os
import asyncio
import logging
from datetime import datetime, date
from typing import Dict, Any, List

# Add the backend directory to Python path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from backend.rbac_backend.services.falkor_graph_service import (
    FalkorGraphService, 
    FalkorGraphConfig, 
    normalize_letter_code,
    FalkorGraphError
)

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('falkor_test.log')
    ]
)

logger = logging.getLogger(__name__)

class FalkorGraphTester:
    def __init__(self):
        self.service = FalkorGraphService()
        self.test_letters = []
        
    def print_separator(self, title: str):
        print(f"\n{'='*60}")
        print(f" {title}")
        print(f"{'='*60}")
    
    def test_connection(self) -> bool:
        """Test basic connection to FalkorDB"""
        self.print_separator("Testing Connection")
        try:
            if not self.service.enabled:
                print("❌ FalkorDB is disabled in configuration")
                return False
            
            # Test connection by counting letters
            count = self.service.debug_count_letters()
            print(f"✅ Connection successful - Found {count} letters in database")
            return True
            
        except Exception as e:
            print(f"❌ Connection failed: {e}")
            return False
    
    def test_normalize_function(self):
        """Test letter code normalization"""
        self.print_separator("Testing Code Normalization")
        
        test_cases = [
            ("ABC-123", "abc-123"),
            ("Test Letter 456", "test-letter-456"),
            ("SPECIAL@CHAR#CODE$", "special-char-code"),
            ("  spaces  around  ", "spaces-around"),
            ("", "unknown"),
            (None, "unknown"),
        ]
        
        for input_code, expected in test_cases:
            result = normalize_letter_code(input_code)
            status = "✅" if result == expected else "❌"
            print(f"{status} '{input_code}' -> '{result}' (expected: '{expected}')")
    
    def test_parameter_serialization(self):
        """Test parameter serialization for FalkorDB"""
        self.print_separator("Testing Parameter Serialization")
        
        test_params = {
            "normCode": "test-001",
            "code": "TEST-001",
            "direction": "incoming",
            "subject": "Test Subject",
            "date": "2024-01-15",
            "project": "TEST-PROJECT",
            "createdAt": datetime.now().isoformat(),
            "lastUpdated": datetime.now().isoformat(),
            "nullField": None,
            "emptyField": "",
        }
        
        try:
            serialized = self.service._serialize_params(test_params)
            print("✅ Parameter serialization successful")
            print("Serialized parameters:")
            for key, value in serialized.items():
                print(f"  {key}: {value} ({type(value).__name__})")
            return True
        except Exception as e:
            print(f"❌ Parameter serialization failed: {e}")
            return False
    
    def test_simple_query(self) -> bool:
        """Test executing a simple query"""
        self.print_separator("Testing Simple Query")
        
        try:
            result = self.service._execute("RETURN 'hello' AS message")
            parsed = self.service._parse_rows(result)
            print(f"✅ Simple query successful: {parsed}")
            return True
        except Exception as e:
            print(f"❌ Simple query failed: {e}")
            return False
    
    def create_test_letter(self, code: str, refs: List[str] = None) -> Dict[str, Any]:
        """Create a test letter payload"""
        base_letter = {
            "code": code,
            "normCode": normalize_letter_code(code),
            "direction": "incoming",
            "subject": f"Test Letter {code}",
            "date": date.today().isoformat(),
            "project": "TEST-PROJECT"
        }
        
        references = []
        if refs:
            for ref_code in refs:
                references.append({
                    "code": ref_code,
                    "normCode": normalize_letter_code(ref_code),
                    "type": "CITES",
                    "source": "test"
                })
        
        return base_letter, references
    
    def test_upsert_single_letter(self) -> bool:
        """Test upserting a single letter without references"""
        self.print_separator("Testing Single Letter Upsert")
        
        test_code = "TEST-SINGLE-001"
        letter, refs = self.create_test_letter(test_code)
        
        try:
            self.service.upsert_letter_with_refs(letter, refs, cleanup=False)
            print(f"✅ Single letter upsert successful: {test_code}")
            self.test_letters.append(test_code)
            return True
        except Exception as e:
            print(f"❌ Single letter upsert failed: {e}")
            return False
    
    def test_upsert_letter_with_references(self) -> bool:
        """Test upserting a letter with references"""
        self.print_separator("Testing Letter with References")
        
        # Create main letter
        main_code = "TEST-MAIN-002"
        ref_codes = ["TEST-REF-001", "TEST-REF-002", "TEST-REF-003"]
        
        letter, refs = self.create_test_letter(main_code, ref_codes)
        
        try:
            self.service.upsert_letter_with_refs(letter, refs, cleanup=False)
            print(f"✅ Letter with references upsert successful: {main_code}")
            print(f"   References: {ref_codes}")
            self.test_letters.append(main_code)
            self.test_letters.extend(ref_codes)
            return True
        except Exception as e:
            print(f"❌ Letter with references upsert failed: {e}")
            return False
    
    def test_upsert_complex_references(self) -> bool:
        """Test upserting letters with different reference types"""
        self.print_separator("Testing Complex References")
        
        main_code = "TEST-COMPLEX-003"
        
        references = [
            {"code": "TEST-CITE-001", "type": "CITES", "source": "test"},
            {"code": "TEST-REPLY-001", "type": "REPLIES_TO", "source": "test"},
            {"code": "TEST-CITE-002", "type": "CITES", "source": "test"},
        ]
        
        letter, _ = self.create_test_letter(main_code)
        
        try:
            self.service.upsert_letter_with_refs(letter, references, cleanup=False)
            print(f"✅ Complex references upsert successful: {main_code}")
            self.test_letters.append(main_code)
            for ref in references:
                self.test_letters.append(ref["code"])
            return True
        except Exception as e:
            print(f"❌ Complex references upsert failed: {e}")
            return False
    
    def test_get_letter(self) -> bool:
        """Test retrieving a letter"""
        self.print_separator("Testing Letter Retrieval")
        
        if not self.test_letters:
            print("⚠️  No test letters available for retrieval test")
            return False
        
        test_code = self.test_letters[0]
        norm_code = normalize_letter_code(test_code)
        
        try:
            letter = self.service.get_letter(norm_code)
            if letter:
                print(f"✅ Letter retrieval successful: {test_code}")
                print(f"   Retrieved: {letter.get('code')} - {letter.get('subject')}")
                return True
            else:
                print(f"❌ Letter not found: {test_code}")
                return False
        except Exception as e:
            print(f"❌ Letter retrieval failed: {e}")
            return False
    
    def test_get_thread(self) -> bool:
        """Test retrieving a thread"""
        self.print_separator("Testing Thread Retrieval")
        
        if not self.test_letters:
            print("⚠️  No test letters available for thread test")
            return False
        
        test_code = self.test_letters[0]
        norm_code = normalize_letter_code(test_code)
        
        try:
            # Test depth=0 (just the letter itself)
            thread_zero = self.service.get_thread(norm_code, depth=0)
            print(f"✅ Thread depth=0: Found {len(thread_zero)} letters")
            
            # Test depth=1
            thread_one = self.service.get_thread(norm_code, depth=1)
            print(f"✅ Thread depth=1: Found {len(thread_one)} letters")
            
            return True
        except Exception as e:
            print(f"❌ Thread retrieval failed: {e}")
            return False
    
    def test_get_neighbors(self) -> bool:
        """Test getting neighbors"""
        self.print_separator("Testing Neighbor Retrieval")
        
        if not self.test_letters:
            print("⚠️  No test letters available for neighbor test")
            return False
        
        test_code = self.test_letters[0]
        norm_code = normalize_letter_code(test_code)
        
        try:
            outgoing = self.service.get_neighbors(norm_code, "outgoing")
            incoming = self.service.get_neighbors(norm_code, "incoming")
            
            print(f"✅ Outgoing neighbors: {len(outgoing)}")
            print(f"✅ Incoming neighbors: {len(incoming)}")
            
            return True
        except Exception as e:
            print(f"❌ Neighbor retrieval failed: {e}")
            return False
    
    def test_debug_functions(self) -> bool:
        """Test debug functions"""
        self.print_separator("Testing Debug Functions")
        
        try:
            # Count all letters
            count = self.service.debug_count_letters()
            print(f"✅ Total letters in database: {count}")
            
            # List some letters
            letters = self.service.debug_list_all_letters(limit=5)
            print(f"✅ Sample letters ({len(letters)}):")
            for letter in letters:
                print(f"   - {letter.get('normCode')}: {letter.get('subject')}")
            
            return True
        except Exception as e:
            print(f"❌ Debug functions failed: {e}")
            return False
    
    def test_cleanup_functionality(self) -> bool:
        """Test reference cleanup functionality"""
        self.print_separator("Testing Cleanup Functionality")
        
        # Create a test letter with references
        test_code = "TEST-CLEANUP-004"
        ref_codes = ["TEST-CLEANUP-REF-001", "TEST-CLEANUP-REF-002"]
        
        letter, refs = self.create_test_letter(test_code, ref_codes)
        
        try:
            # First upsert with references
            self.service.upsert_letter_with_refs(letter, refs, cleanup=False)
            print("✅ Initial upsert with references successful")
            
            # Get outgoing neighbors to verify references exist
            neighbors_before = self.service.get_neighbors(
                normalize_letter_code(test_code), "outgoing"
            )
            print(f"✅ References before cleanup: {len(neighbors_before)}")
            
            # Now upsert with different references and cleanup enabled
            new_refs = [{"code": "TEST-CLEANUP-NEW-001", "type": "CITES", "source": "test"}]
            self.service.upsert_letter_with_refs(letter, new_refs, cleanup=True)
            print("✅ Upsert with cleanup successful")
            
            # Get outgoing neighbors after cleanup
            neighbors_after = self.service.get_neighbors(
                normalize_letter_code(test_code), "outgoing"
            )
            print(f"✅ References after cleanup: {len(neighbors_after)}")
            
            self.test_letters.append(test_code)
            return True
            
        except Exception as e:
            print(f"❌ Cleanup functionality test failed: {e}")
            return False
    
    def test_error_handling(self) -> bool:
        """Test error handling for invalid operations"""
        self.print_separator("Testing Error Handling")
        
        try:
            # Test with empty letter code
            empty_letter = {"code": ""}
            self.service.upsert_letter_with_refs(empty_letter, [])
            print("✅ Empty code handling - skipped as expected")
            
            # Test with invalid parameters
            try:
                self.service._execute("MATCH (n:Letter {invalidParam: $missingParam}) RETURN n", {})
                print("❌ Should have failed with missing parameters")
                return False
            except FalkorGraphError:
                print("✅ Missing parameter error handled correctly")
            
            return True
        except Exception as e:
            print(f"❌ Error handling test failed: {e}")
            return False
    
    def run_all_tests(self) -> Dict[str, bool]:
        """Run all tests and return results"""
        results = {}
        
        # Basic functionality tests
        results["connection"] = self.test_connection()
        results["normalization"] = self.test_normalize_function()
        results["parameter_serialization"] = self.test_parameter_serialization()
        
        # Only continue if connection is successful
        if results["connection"]:
            results["simple_query"] = self.test_simple_query()
            results["single_letter"] = self.test_upsert_single_letter()
            results["letter_with_refs"] = self.test_upsert_letter_with_references()
            results["complex_refs"] = self.test_upsert_complex_references()
            results["get_letter"] = self.test_get_letter()
            results["get_thread"] = self.test_get_thread()
            results["get_neighbors"] = self.test_get_neighbors()
            results["debug_functions"] = self.test_debug_functions()
            results["cleanup"] = self.test_cleanup_functionality()
            results["error_handling"] = self.test_error_handling()
        
        return results
    
    def print_summary(self, results: Dict[str, bool]):
        """Print test summary"""
        self.print_separator("TEST SUMMARY")
        
        total_tests = len(results)
        passed_tests = sum(1 for result in results.values() if result)
        failed_tests = total_tests - passed_tests
        
        print(f"Total Tests: {total_tests}")
        print(f"✅ Passed: {passed_tests}")
        print(f"❌ Failed: {failed_tests}")
        
        if failed_tests > 0:
            print("\nFailed tests:")
            for test_name, result in results.items():
                if not result:
                    print(f"  ❌ {test_name}")
        
        success_rate = (passed_tests / total_tests) * 100 if total_tests > 0 else 0
        print(f"\nSuccess Rate: {success_rate:.1f}%")
        
        if success_rate == 100:
            print("\n🎉 All tests passed!")
        elif success_rate >= 80:
            print("\n👍 Most tests passed!")
        else:
            print("\n⚠️  Significant test failures detected")


def main():
    """Main test function"""
    print("FalkorGraphService Test Suite")
    print("=" * 50)
    
    tester = FalkorGraphTester()
    
    try:
        results = tester.run_all_tests()
        tester.print_summary(results)
        
        # Exit with appropriate code
        failed_count = sum(1 for result in results.values() if not result)
        sys.exit(failed_count)
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n💥 Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()