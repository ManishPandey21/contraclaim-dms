#!/usr/bin/env python3
"""Manual verification script for the OpenAI service fix."""

from pathlib import Path

def verify_openai_fix():
    """Verify the OpenAI service fix by checking file content"""
    print("Verifying OpenAI Service Fix...")
    
    try:
        target = Path(__file__).resolve().parents[3] / "rbac_backend" / "services" / "openai_service.py"
        with target.open("r", encoding="utf-8") as f:
            content = f.read()
        
        # Check for the new format
        new_format = '"type": "file", "file": {"file_id": file_id}'
        old_format = '"type": "file", "file_id": file_id'
        
        if new_format in content:
            print("✅ NEW API FORMAT FOUND: Correct OpenAI API call format is present")
            format_check = True
        elif old_format in content:
            print("❌ OLD API FORMAT FOUND: Incorrect OpenAI API call format is still present")
            format_check = False
        else:
            print("⚠️  API FORMAT NOT FOUND: Could not locate OpenAI API call format")
            format_check = False
        
        # Check for logging improvements
        if 'logger.error(f"OpenAI API call failed with file_id' in content:
            print("✅ ENHANCED LOGGING FOUND: Improved error logging is present")
            logging_check = True
        else:
            print("❌ ENHANCED LOGGING MISSING: Improved error logging is not present")
            logging_check = False
        
        # Check for proper imports
        if 'import logging' in content and 'logger = logging.getLogger(__name__)' in content:
            print("✅ LOGGING IMPORTS FOUND: Proper logging imports are present")
            import_check = True
        else:
            print("❌ LOGGING IMPORTS MISSING: Proper logging imports are not present")
            import_check = False
        
        return format_check and logging_check and import_check
        
    except Exception as e:
        print(f"❌ ERROR: Could not verify fix - {e}")
        return False

def main():
    print("=" * 60)
    print("OPENAI SERVICE FIX VERIFICATION")
    print("=" * 60)
    
    success = verify_openai_fix()
    
    print("\n" + "=" * 60)
    print("VERIFICATION SUMMARY")
    print("=" * 60)
    
    if success:
        print("🎉 ALL CHECKS PASSED! The OpenAI service fix is correctly implemented.")
        print("\nThe fix addresses the error:")
        print("'Missing required parameter: messages[0].content[1].file'")
        print("\nBy updating the API call format from:")
        print('{"type": "file", "file_id": file_id}')
        print("To:")
        print('{"type": "file", "file": {"file_id": file_id}}')
        return 0
    else:
        print("⚠️  VERIFICATION FAILED! The fix may not be correctly implemented.")
        return 1

if __name__ == "__main__":
    exit_code = main()
    exit(exit_code)
