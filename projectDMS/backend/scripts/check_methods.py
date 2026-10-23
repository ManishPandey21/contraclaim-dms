#!/usr/bin/env python
"""
Check available methods in FalkorGraphService.
"""

import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from rbac_backend.services.falkor_graph_service import FalkorGraphService

def check_methods():
    print("=== FalkorGraphService Methods ===")
    service = FalkorGraphService()

    # List all public methods
    methods = [method for method in dir(service) if not method.startswith('_')]
    print("Public methods:", methods)

    # List all private methods
    private_methods = [method for method in dir(service) if method.startswith('_') and not method.startswith('__')]
    print("Private methods:", private_methods)

    # Check if get_letter exists
    if 'get_letter' in methods:
        print("\nget_letter method exists!")
    else:
        print("\nNo get_letter method found!")

if __name__ == "__main__":
    check_methods()
