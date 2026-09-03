import logging
from typing import Dict, Any, Optional
from contextlib import asynccontextmanager

from .data_initialization import create_data_initializer

logger = logging.getLogger(__name__)

class SetupServiceError(Exception):
    """Custom exception for setup service errors"""
    pass

class SetupService:
    """Service for orchestrating application setup and data initialization"""

    def __init__(self, database):
        if database is None:
            raise ValueError("Database connection cannot be None")
        self.database = database
        self.data_initializer = create_data_initializer(database)

    async def initialize_application_data(self) -> Dict[str, Any]:
        """
        Orchestrate the initialization of all application data.

        Returns:
            Dictionary with initialization results and counts

        Raises:
            SetupServiceError: If initialization fails
        """
        logger.info("Starting application data initialization")

        try:
            # Use the data initializer to set up all data
            results = await self.data_initializer.initialize_all_data()

            # Log summary
            total_created = sum(results.values())
            logger.info(f"Application initialization completed successfully")
            logger.info(f"Total records created: {total_created}")
            for data_type, count in results.items():
                if count > 0:
                    logger.info(f"  - {data_type}: {count} records")

            return {
                "success": True,
                "results": results,
                "total_created": total_created,
                "message": "Application data initialized successfully"
            }

        except Exception as e:
            logger.error(f"Application initialization failed: {e}")
            raise SetupServiceError(f"Initialization failed: {str(e)}")

    async def initialize_specific_data(self, data_types: list) -> Dict[str, Any]:
        """
        Initialize only specific types of data.

        Args:
            data_types: List of data types to initialize
                       (e.g., ['permissions', 'roles', 'users'])

        Returns:
            Dictionary with initialization results

        Raises:
            SetupServiceError: If initialization fails
        """
        if not data_types or not isinstance(data_types, list):
            raise ValueError("data_types must be a non-empty list")

        logger.info(f"Initializing specific data types: {data_types}")

        results = {}
        total_created = 0

        try:
            for data_type in data_types:
                if data_type == 'permissions':
                    count = await self.data_initializer.initialize_permissions()
                elif data_type == 'roles':
                    count = await self.data_initializer.initialize_roles()
                elif data_type == 'users':
                    count = await self.data_initializer.initialize_users()
                elif data_type == 'organizations':
                    count = await self.data_initializer.initialize_organizations()
                elif data_type == 'projects':
                    count = await self.data_initializer.initialize_projects()
                else:
                    logger.warning(f"Unknown data type: {data_type}")
                    count = 0

                results[data_type] = count
                total_created += count

                if count > 0:
                    logger.info(f"Initialized {data_type}: {count} records created")

            logger.info(f"Specific data initialization completed: {total_created} total records")

            return {
                "success": True,
                "results": results,
                "total_created": total_created,
                "message": f"Successfully initialized: {', '.join(data_types)}"
            }

        except Exception as e:
            logger.error(f"Specific data initialization failed: {e}")
            raise SetupServiceError(f"Specific initialization failed: {str(e)}")

    async def verify_setup(self) -> Dict[str, Any]:
        """
        Verify that application setup is complete and valid.

        Returns:
            Dictionary with verification results
        """
        logger.info("Verifying application setup")

        verification_results = {
            "permissions": False,
            "roles": False,
            "users": False,
            "organizations": False,
            "projects": False,
            "database_connection": False
        }

        issues = []

        try:
            # Test database connection
            try:
                await self.database.command("ping")
                verification_results["database_connection"] = True
            except Exception as e:
                issues.append(f"Database connection failed: {e}")

            # Check each collection
            collections_to_check = [
                ("permissions", "permissions"),
                ("roles", "roles"),
                ("users", "users"),
                ("organizations", "organizations"),
                ("projects", "projects")
            ]

            for key, collection_name in collections_to_check:
                try:
                    count = await getattr(self.database, collection_name).count_documents({})
                    if count > 0:
                        verification_results[key] = True
                        logger.debug(f"Found {count} records in {collection_name}")
                    else:
                        issues.append(f"No records found in {collection_name} collection")
                except Exception as e:
                    issues.append(f"Error checking {collection_name}: {e}")

            # Determine overall status
            all_verified = all(verification_results.values())

            result = {
                "success": all_verified,
                "results": verification_results,
                "issues": issues,
                "message": "Setup verification completed" if all_verified else "Setup verification found issues"
            }

            if all_verified:
                logger.info("Application setup verification passed")
            else:
                logger.warning(f"Application setup verification failed: {len(issues)} issues found")
                for issue in issues:
                    logger.warning(f"  - {issue}")

            return result

        except Exception as e:
            logger.error(f"Setup verification failed: {e}")
            raise SetupServiceError(f"Verification failed: {str(e)}")

    async def reset_application_data(self, confirm: bool = False) -> Dict[str, Any]:
        """
        Reset (delete) all application data. Use with extreme caution!

        Args:
            confirm: Must be True to actually perform the reset

        Returns:
            Dictionary with reset results

        Raises:
            SetupServiceError: If reset fails
        """
        if not confirm:
            raise ValueError("confirm parameter must be True to perform reset")

        logger.warning("RESETTING ALL APPLICATION DATA - This action is irreversible!")

        collections_to_reset = ["permissions", "roles", "users", "organizations", "projects"]
        results = {}

        try:
            for collection_name in collections_to_reset:
                try:
                    collection = getattr(self.database, collection_name)
                    delete_result = await collection.delete_many({})
                    deleted_count = delete_result.deleted_count
                    results[collection_name] = deleted_count
                    logger.warning(f"Deleted {deleted_count} records from {collection_name}")
                except Exception as e:
                    logger.error(f"Failed to reset {collection_name}: {e}")
                    results[collection_name] = f"Error: {e}"

            total_deleted = sum(count for count in results.values() if isinstance(count, int))

            logger.warning(f"Application data reset completed: {total_deleted} total records deleted")

            return {
                "success": True,
                "results": results,
                "total_deleted": total_deleted,
                "message": "Application data reset completed"
            }

        except Exception as e:
            logger.error(f"Application data reset failed: {e}")
            raise SetupServiceError(f"Reset failed: {str(e)}")

# Factory function
def create_setup_service(database) -> SetupService:
    """Create setup service instance"""
    return SetupService(database)

# Convenience functions for backward compatibility
async def initialize_application_data(database=None):
    """Convenience function for application initialization"""
    if database is None:
        from ..core.database import database

    setup_service = create_setup_service(database)
    return await setup_service.initialize_application_data()
