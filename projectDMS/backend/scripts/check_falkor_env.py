#!/usr/bin/env python
"""
Check FalkorDB environment variables.
"""

import os

def check_falkor_env():
    print("=== FalkorDB Environment Check ===")

    falkor_url = os.getenv('FALKORDB_URL')
    falkor_password = os.getenv('FALKORDB_PASSWORD')

    print(f"FALKORDB_URL: '{falkor_url}'")
    print(f"FALKORDB_PASSWORD: '{falkor_password}'")

    # Check if URL is malformed
    if falkor_url and falkor_url.startswith('='):
        print("⚠️  URL starts with '=' - this is malformed!")
        corrected = falkor_url.lstrip('=')
        print(f"Corrected URL would be: '{corrected}'")

    # Test different connection options
    print("\n=== Testing Connection Options ===")

    # Option 1: Direct connection with default values
    print("Option 1 - Direct local connection:")
    print("  redis://localhost:6380")

    # Option 2: Using the port from malformed URL
    if falkor_url and '6380' in falkor_url:
        print("Option 2 - Using port 6380:")
        print("  redis://localhost:6380")

if __name__ == "__main__":
    check_falkor_env()
