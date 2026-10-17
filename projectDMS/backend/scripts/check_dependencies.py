#!/usr/bin/env python
"""
Check document processing dependencies and configuration.
"""

import importlib
import sys

def check_dependencies():
    print("=== Document Processing Dependencies Check ===")

    dependencies = [
        "langchain",
        "langchain_openai",
        "langchain_community",
        "qdrant_client",
        "pymongo",
        "redis",
        "openai",
        "tiktoken"
    ]

    missing = []
    for dep in dependencies:
        try:
            importlib.import_module(dep)
            print(f"✅ {dep}")
        except ImportError as e:
            print(f"❌ {dep}: {e}")
            missing.append(dep)

    return missing

def check_config():
    print("\n=== Configuration Check ===")
    try:
        from rbac_backend.config.document_processing_config import DocumentProcessingConfig
        config = DocumentProcessingConfig()

        print("✅ DocumentProcessingConfig loaded")
        print(f"   Qdrant enabled: {config.qdrant_enabled}")
        print(f"   FalkorDB enabled: {config.falkordb_enabled}")
        print(f"   OpenAI API key: {'Set' if config.openai_api_key else 'Not set'}")

    except Exception as e:
        print(f"❌ Configuration error: {e}")
        return False

    return True

def check_services():
    print("\n=== Service Initialization Check ===")

    try:
        from rbac_backend.services.langchain_vector_service import LangChainVectorService
        from rbac_backend.config.document_processing_config import DocumentProcessingConfig

        config = DocumentProcessingConfig()
        service = LangChainVectorService(config)
        print("✅ LangChainVectorService initialized")

    except Exception as e:
        print(f"❌ LangChainVectorService failed: {e}")
        return False

    try:
        from rbac_backend.services.falkor_graph_service import FalkorGraphService
        service = FalkorGraphService()
        print("✅ FalkorGraphService initialized")

    except Exception as e:
        print(f"❌ FalkorGraphService failed: {e}")
        return False

    return True

def main():
    print("Document Processing Setup Diagnosis")
    print("=" * 50)

    # Check dependencies
    missing_deps = check_dependencies()

    # Check configuration
    config_ok = check_config()

    # Check services
    services_ok = check_services()

    # Recommendations
    print("\n=== RECOMMENDATIONS ===")
    if missing_deps:
        print("1. Install missing dependencies:")
        for dep in missing_deps:
            print(f"   pip install {dep}")

    if not config_ok or not services_ok:
        print("2. Check your environment variables:")
        print("   - OPENAI_API_KEY")
        print("   - QDRANT_URL, QDRANT_API_KEY")
        print("   - FALKORDB_URL, FALKORDB_PASSWORD")
        print("   - MONGO_URL, MONGO_DB_NAME")

    if missing_deps:
        print("\n❌ Missing dependencies - fix above issues first")
        return 1
    elif not config_ok or not services_ok:
        print("\n⚠️  Configuration issues - check environment variables")
        return 1
    else:
        print("\n✅ All checks passed! You can process documents.")
        return 0

if __name__ == "__main__":
    sys.exit(main())
