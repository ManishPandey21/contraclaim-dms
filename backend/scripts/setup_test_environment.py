"""
Setup script for Qdrant and FalkorDB testing environment.

This script:
1. Checks if required packages are installed
2. Verifies environment variables are set
3. Tests connectivity to Qdrant and FalkorDB
4. Provides setup instructions if needed

Usage:
    python backend/scripts/setup_test_environment.py
"""

import os
import sys
from typing import List, Tuple

from dotenv import load_dotenv

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

# Load environment variables from .env if present
load_dotenv()


class Colors:
    """ANSI color codes for terminal output"""
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    RESET = '\033[0m'
    BOLD = '\033[1m'


def print_header(text: str):
    """Print a formatted header"""
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*80}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}{text.center(80)}{Colors.RESET}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'='*80}{Colors.RESET}\n")


def print_success(text: str):
    """Print success message"""
    print(f"{Colors.GREEN}[OK] {text}{Colors.RESET}")


def print_error(text: str):
    """Print error message"""
    print(f"{Colors.RED}[ERROR] {text}{Colors.RESET}")


def print_warning(text: str):
    """Print warning message"""
    print(f"{Colors.YELLOW}[WARN] {text}{Colors.RESET}")


def print_info(text: str):
    """Print info message"""
    print(f"{Colors.BLUE}[INFO] {text}{Colors.RESET}")


def check_package(package_name: str, import_name: str = None) -> bool:
    """Check if a Python package is installed"""
    if import_name is None:
        import_name = package_name
    
    try:
        __import__(import_name)
        return True
    except ImportError:
        return False


def check_packages() -> Tuple[bool, List[str]]:
    """Check if all required packages are installed"""
    print_header("CHECKING PYTHON PACKAGES")
    
    packages = [
        ("PyPDF2", "PyPDF2"),
        ("qdrant-client", "qdrant_client"),
        ("langchain-openai", "langchain_openai"),
        ("langchain-qdrant", "langchain_qdrant"),
        ("langgraph", "langgraph"),
        ("python-dotenv", "dotenv"),
        ("redis", "redis"),
        ("openai", "openai"),
    ]
    
    missing = []
    all_installed = True
    
    for package_name, import_name in packages:
        if check_package(package_name, import_name):
            print_success(f"{package_name} is installed")
        else:
            print_error(f"{package_name} is NOT installed")
            missing.append(package_name)
            all_installed = False
    
    if missing:
        print_warning("\nMissing packages detected!")
        print_info("Install missing packages with:")
        print(f"  pip install {' '.join(missing)}")
    
    return all_installed, missing


def check_environment_variables() -> Tuple[bool, List[str]]:
    """Check if required environment variables are set"""
    print_header("CHECKING ENVIRONMENT VARIABLES")
    
    required_vars = [
        ("OPENAI_API_KEY", True),
        ("QDRANT_URL", False),
        ("QDRANT_COLLECTION", False),
        ("FALKORDB_ENABLED", False),
        ("FALKORDB_HOST", False),
        ("FALKORDB_PORT", False),
        ("VECTOR_DUAL_WRITE_ENABLED", False),
    ]
    
    missing = []
    all_set = True
    
    for var_name, is_critical in required_vars:
        value = os.getenv(var_name)
        if value:
            # Mask sensitive values
            if "KEY" in var_name or "PASSWORD" in var_name:
                display_value = f"{value[:8]}..." if len(value) > 8 else "***"
            else:
                display_value = value
            print_success(f"{var_name} = {display_value}")
        else:
            if is_critical:
                print_error(f"{var_name} is NOT set (CRITICAL)")
                missing.append(var_name)
                all_set = False
            else:
                print_warning(f"{var_name} is NOT set (will use default)")
    
    if missing:
        print_warning("\nCritical environment variables missing!")
        print_info("Set them in your .env file or export them:")
        for var in missing:
            print(f"  export {var}=your_value")
    
    return all_set, missing


def test_qdrant_connection() -> bool:
    """Test connection to Qdrant"""
    print_header("TESTING QDRANT CONNECTION")
    
    try:
        from qdrant_client import QdrantClient
        from rbac_backend.config.document_processing_config import DocumentProcessingConfig
        
        config = DocumentProcessingConfig()
        
        if not config.qdrant_enabled:
            print_warning("Qdrant is disabled in configuration")
            return False
        
        print_info(f"Connecting to Qdrant at {config.qdrant_url}...")
        
        client = QdrantClient(
            url=config.qdrant_url,
            api_key=config.qdrant_api_key,
            timeout=5.0
        )
        
        # Try to get collections
        collections = client.get_collections()
        print_success(f"Connected to Qdrant successfully!")
        print_info(f"Available collections: {len(collections.collections)}")
        
        # Check if our collection exists
        collection_names = [c.name for c in collections.collections]
        if config.qdrant_collection in collection_names:
            info = client.get_collection(config.qdrant_collection)
            print_success(f"Collection '{config.qdrant_collection}' exists with {info.points_count} vectors")
        else:
            print_warning(f"Collection '{config.qdrant_collection}' does not exist yet (will be created)")
        
        return True
        
    except ImportError as e:
        print_error(f"Required packages not installed: {e}")
        return False
    except Exception as e:
        print_error(f"Failed to connect to Qdrant: {e}")
        print_info("Make sure Qdrant is running:")
        print("  docker run -p 6333:6333 qdrant/qdrant")
        return False


def test_falkordb_connection() -> bool:
    """Test connection to FalkorDB"""
    print_header("TESTING FALKORDB CONNECTION")
    
    try:
        from rbac_backend.services.falkor_graph_service import FalkorGraphService
        
        service = FalkorGraphService()
        
        if not service.enabled:
            print_warning("FalkorDB is disabled in configuration")
            return False
        
        print_info(f"Connecting to FalkorDB at {service.config.host}:{service.config.port}...")
        
        # Try to ensure schema (this will test connection)
        service.ensure_schema()
        print_success("Connected to FalkorDB successfully!")
        
        # Get statistics
        total_letters = service.debug_count_letters()
        print_info(f"Current graph has {total_letters} letter nodes")
        
        if total_letters > 0:
            print_info("Sample letters:")
            letters = service.debug_list_all_letters(limit=5)
            for letter in letters:
                subject = letter.get('subject') or 'N/A'
                print(f"  - {letter.get('code', 'N/A')}: {subject[:50]}")
        
        return True
        
    except ImportError as e:
        print_error(f"Required packages not installed: {e}")
        return False
    except Exception as e:
        print_error(f"Failed to connect to FalkorDB: {e}")
        print_info("Make sure FalkorDB is running:")
        print("  docker run -p 6380:6379 falkordb/falkordb")
        return False


def check_test_data() -> bool:
    """Check if test data folder exists"""
    print_header("CHECKING TEST DATA")
    
    test_folder = r"C:\Users\santo\Downloads\KNPCC-06 Borewell (1)\KNPCC-06 Borewell\New folder\test data"
    
    if os.path.exists(test_folder):
        pdf_files = [f for f in os.listdir(test_folder) if f.lower().endswith('.pdf')]
        print_success(f"Test data folder exists: {test_folder}")
        print_info(f"Found {len(pdf_files)} PDF files")
        
        if pdf_files:
            print_info("Sample files:")
            for pdf in pdf_files[:5]:
                print(f"  - {pdf}")
            if len(pdf_files) > 5:
                print(f"  ... and {len(pdf_files) - 5} more")
        
        return True
    else:
        print_error(f"Test data folder not found: {test_folder}")
        print_info("You can specify a custom folder with --test-folder when running the test")
        return False


def print_next_steps(all_checks_passed: bool):
    """Print next steps based on check results"""
    print_header("NEXT STEPS")
    
    if all_checks_passed:
        print_success("All checks passed! You're ready to run the tests.")
        print_info("\nRun the test suite with:")
        print("  python backend/scripts/test_qdrant_falkor_with_data.py")
        print("\nOr with cleanup:")
        print("  python backend/scripts/test_qdrant_falkor_with_data.py --cleanup")
        print("\nFor more options:")
        print("  python backend/scripts/test_qdrant_falkor_with_data.py --help")
    else:
        print_warning("Some checks failed. Please fix the issues above before running tests.")
        print_info("\nCommon fixes:")
        print("1. Install missing packages:")
        print("   pip install PyPDF2 qdrant-client langchain-openai langchain-qdrant langgraph python-dotenv redis openai")
        print("\n2. Set environment variables in .env file:")
        print("   OPENAI_API_KEY=your_key")
        print("   QDRANT_URL=http://localhost:6333")
        print("   FALKORDB_ENABLED=true")
        print("\n3. Start required services:")
        print("   docker run -p 6333:6333 qdrant/qdrant")
        print("   docker run -p 6380:6379 falkordb/falkordb")


def main():
    """Main entry point"""
    print_header("QDRANT & FALKORDB TEST ENVIRONMENT SETUP")
    
    # Run all checks
    packages_ok, missing_packages = check_packages()
    env_ok, missing_env = check_environment_variables()
    qdrant_ok = test_qdrant_connection() if packages_ok else False
    falkor_ok = test_falkordb_connection() if packages_ok else False
    data_ok = check_test_data()
    
    # Summary
    print_header("SETUP SUMMARY")
    
    checks = [
        ("Python Packages", packages_ok),
        ("Environment Variables", env_ok),
        ("Qdrant Connection", qdrant_ok),
        ("FalkorDB Connection", falkor_ok),
        ("Test Data", data_ok),
    ]
    
    for check_name, passed in checks:
        if passed:
            print_success(f"{check_name}: OK")
        else:
            print_error(f"{check_name}: FAILED")
    
    all_passed = all(passed for _, passed in checks)
    
    # Print next steps
    print_next_steps(all_passed)
    
    # Exit with appropriate code
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
