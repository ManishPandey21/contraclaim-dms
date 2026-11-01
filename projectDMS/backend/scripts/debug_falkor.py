#!/usr/bin/env python
"""
Debug FalkorDB connection issues.
"""

import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from rbac_backend.services.falkor_graph_service import FalkorGraphService
from rbac_backend.config.document_processing_config import DocumentProcessingConfig

def debug_falkor():
    print("=== FalkorDB Connection Debug ===")

    # Check config
    config = DocumentProcessingConfig()
    print(f"FALKORDB_URL: {config.falkordb_url}")
    print(f"FALKORDB_PASSWORD: {'*' * len(config.falkordb_password) if config.falkordb_password else 'None'}")
    print(f"FALKORDB_ENABLED: {config.falkordb_enabled}")

    # Try to create service
    service = FalkorGraphService()
    print(f"Service enabled: {service.enabled}")
    print(f"Service _client attr: {hasattr(service, '_client')}")

    if hasattr(service, '_client'):
        client_val = service._client
        print(f"Service _client value: {client_val}")
        print(f"Service _client type: {type(client_val)}")

        # If it's a property/method, try to call it
        if callable(client_val):
            try:
                result = client_val()
                print(f"Service _client() call result: {result}")
                print(f"Result type: {type(result)}")
            except Exception as e:
                print(f"Error calling _client(): {e}")

    # Test direct connection
    if service.enabled:
        print("\n--- Testing direct query ---")
        try:
            # Use the public method instead of private _execute
            result = service.execute_query("RETURN 'Hello FalkorDB' AS message")
            print(f"Direct query result: {result}")
        except Exception as e:
            print(f"Direct query failed: {e}")

if __name__ == "__main__":
    debug_falkor()
