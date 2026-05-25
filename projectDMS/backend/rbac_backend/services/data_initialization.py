import logging
from typing import List, Dict, Any, Optional
from contextlib import asynccontextmanager

from ..models.permission import Permission
from ..models.role import Role  
from ..models.user import User
from ..models.organization import Organization
from ..models.project import Project
from ..services.permission_service import PermissionService

logger = logging.getLogger(__name__)

class DataInitializationError(Exception):
    """Custom exception for data initialization errors"""
    pass

class DataInitializer:
    """Service for initializing default data with proper error handling"""
    
    def __init__(self, database):
        self.database = database
    
    async def initialize_all_data(self) -> Dict[str, int]:
        """
        Initialize all data types and return counts of created records.
        
        Returns:
            Dictionary with counts of created records for each data type
        """
        results = {
            'permissions': 0,
            'roles': 0, 
            'users': 0,
            'organizations': 0,
            'projects': 0
        }
        
        logger.info("Starting data initialization")
        
        try:
            # Initialize in dependency order
            results['permissions'] = await self.initialize_permissions()
            results['roles'] = await self.initialize_roles()
            results['users'] = await self.initialize_users()
            results['organizations'] = await self.initialize_organizations()
            results['projects'] = await self.initialize_projects()
            
            total_created = sum(results.values())
            logger.info(f"Data initialization completed. Created {total_created} total records")
            return results
            
        except Exception as e:
            logger.error(f"Data initialization failed: {e}")
            raise DataInitializationError(f"Failed to initialize data: {str(e)}")
    
    async def initialize_permissions(self) -> int:
        """Initialize default permissions"""
        try:
            from ..initial_data import default_permissions
            
            permissions_data = getattr(default_permissions, 'DEFAULT_PERMISSIONS', [])
            if not permissions_data:
                logger.warning("No default permissions data found")
                return 0
            
            created_count = 0
            perm_service = PermissionService()
            
            for perm_data in permissions_data:
                if not self._validate_permission_data(perm_data):
                    logger.warning(f"Invalid permission data: {perm_data}")
                    continue
                
                try:
                    existing = await self.database.permissions.find_one({"_id": perm_data["_id"]})
                    if not existing:
                        # Normalize legacy/partial documents through PermissionService to ensure required fields
                        permission = perm_service._build_permission_from_doc(perm_data)  # type: ignore[attr-defined]
                        await self.database.permissions.insert_one(permission.model_dump(by_alias=True))
                        created_count += 1
                        logger.debug(f"Created permission: {perm_data['_id']}")
                    else:
                        permission = perm_service._build_permission_from_doc(perm_data)  # type: ignore[attr-defined]
                        desired = permission.model_dump(by_alias=True, exclude_none=True)
                        desired.pop("_id", None)
                        await self.database.permissions.update_one(
                            {"_id": perm_data["_id"]},
                            {"$set": desired},
                        )
                        logger.debug(f"Permission already exists: {perm_data['_id']}")
                        
                except Exception as e:
                    logger.error(f"Failed to create permission {perm_data.get('_id', 'unknown')}: {e}")
                    continue
            
            logger.info(f"Initialized {created_count} permissions")
            return created_count
            
        except ImportError as e:
            logger.error(f"Failed to import default permissions: {e}")
            raise DataInitializationError("Default permissions module not found")
        except Exception as e:
            logger.error(f"Permission initialization failed: {e}")
            raise DataInitializationError(f"Permission initialization failed: {str(e)}")
    
    async def initialize_roles(self) -> int:
        """Initialize default roles"""
        try:
            from ..initial_data import default_roles
            
            roles_data = getattr(default_roles, 'DEFAULT_ROLES', [])
            if not roles_data:
                logger.warning("No default roles data found")
                return 0
            
            created_count = 0
            
            for role_data in roles_data:
                if not self._validate_role_data(role_data):
                    logger.warning(f"Invalid role data: {role_data}")
                    continue
                
                try:
                    existing = await self.database.roles.find_one({"_id": role_data["_id"]})
                    if not existing:
                        role = Role(**role_data)
                        await self.database.roles.insert_one(role.model_dump(by_alias=True))
                        created_count += 1
                        logger.debug(f"Created role: {role_data['_id']}")
                    else:
                        default_permissions = [
                            str(permission)
                            for permission in (role_data.get("permissions") or [])
                            if permission
                        ]
                        if default_permissions:
                            await self.database.roles.update_one(
                                {"_id": role_data["_id"]},
                                {
                                    "$set": {
                                        "name": role_data.get("name", existing.get("name")),
                                        "is_system": role_data.get("is_system", True),
                                        "scope": role_data.get("scope", existing.get("scope", "system")),
                                        "is_active": role_data.get("is_active", existing.get("is_active", True)),
                                        "updated_at": role_data.get("updated_at"),
                                    },
                                    "$addToSet": {
                                        "permissions": {"$each": default_permissions}
                                    },
                                },
                            )
                        logger.debug(f"Role already exists: {role_data['_id']}")
                        
                except Exception as e:
                    logger.error(f"Failed to create role {role_data.get('_id', 'unknown')}: {e}")
                    continue
            
            logger.info(f"Initialized {created_count} roles")
            return created_count
            
        except ImportError as e:
            logger.error(f"Failed to import default roles: {e}")
            raise DataInitializationError("Default roles module not found")
        except Exception as e:
            logger.error(f"Role initialization failed: {e}")
            raise DataInitializationError(f"Role initialization failed: {str(e)}")
    
    async def initialize_users(self) -> int:
        """Initialize default users"""
        try:
            from ..initial_data import default_users
            
            users_data = getattr(default_users, 'DEFAULT_USERS', [])
            if not users_data:
                logger.warning("No default users data found")
                return 0
            
            created_count = 0
            
            for user_data in users_data:
                if not self._validate_user_data(user_data):
                    logger.warning(f"Invalid user data for user: {user_data.get('username', 'unknown')}")
                    continue
                
                try:
                    existing = await self.database.users.find_one({"username": user_data["username"]})
                    if not existing:
                        await self.database.users.insert_one(user_data)
                        created_count += 1
                        logger.debug(f"Created user: {user_data['username']}")
                    else:
                        logger.debug(f"User already exists: {user_data['username']}")
                        
                except Exception as e:
                    logger.error(f"Failed to create user {user_data.get('username', 'unknown')}: {e}")
                    continue
            
            logger.info(f"Initialized {created_count} users")
            return created_count
            
        except ImportError as e:
            logger.error(f"Failed to import default users: {e}")
            raise DataInitializationError("Default users module not found")
        except Exception as e:
            logger.error(f"User initialization failed: {e}")
            raise DataInitializationError(f"User initialization failed: {str(e)}")
    
    async def initialize_organizations(self) -> int:
        """Initialize default organizations"""
        try:
            from ..initial_data import default_organizations
            
            orgs_data = getattr(default_organizations, 'DEFAULT_ORGANIZATIONS', [])
            if not orgs_data:
                logger.warning("No default organizations data found")
                return 0
            
            created_count = 0
            
            for org_data in orgs_data:
                if not self._validate_organization_data(org_data):
                    logger.warning(f"Invalid organization data: {org_data}")
                    continue
                
                try:
                    existing = await self.database.organizations.find_one({"_id": org_data["_id"]})
                    if not existing:
                        organization = Organization(**org_data)
                        await self.database.organizations.insert_one(organization.model_dump(by_alias=True))
                        created_count += 1
                        logger.debug(f"Created organization: {org_data['_id']}")
                    else:
                        logger.debug(f"Organization already exists: {org_data['_id']}")
                        
                except Exception as e:
                    logger.error(f"Failed to create organization {org_data.get('_id', 'unknown')}: {e}")
                    continue
            
            logger.info(f"Initialized {created_count} organizations")
            return created_count
            
        except ImportError as e:
            logger.error(f"Failed to import default organizations: {e}")
            raise DataInitializationError("Default organizations module not found")
        except Exception as e:
            logger.error(f"Organization initialization failed: {e}")
            raise DataInitializationError(f"Organization initialization failed: {str(e)}")
    
    async def initialize_projects(self) -> int:
        """Initialize default projects"""
        try:
            from ..initial_data import default_projects
            
            projects_data = getattr(default_projects, 'DEFAULT_PROJECTS', [])
            if not projects_data:
                logger.warning("No default projects data found")
                return 0
            
            created_count = 0
            
            for proj_data in projects_data:
                if not self._validate_project_data(proj_data):
                    logger.warning(f"Invalid project data: {proj_data}")
                    continue
                
                try:
                    existing = await self.database.projects.find_one({"_id": proj_data["_id"]})
                    if not existing:
                        project = Project(**proj_data)
                        await self.database.projects.insert_one(project.model_dump(by_alias=True))
                        created_count += 1
                        logger.debug(f"Created project: {proj_data['_id']}")
                    else:
                        logger.debug(f"Project already exists: {proj_data['_id']}")
                        
                except Exception as e:
                    logger.error(f"Failed to create project {proj_data.get('_id', 'unknown')}: {e}")
                    continue
            
            logger.info(f"Initialized {created_count} projects")
            return created_count
            
        except ImportError as e:
            logger.error(f"Failed to import default projects: {e}")
            raise DataInitializationError("Default projects module not found")
        except Exception as e:
            logger.error(f"Project initialization failed: {e}")
            raise DataInitializationError(f"Project initialization failed: {str(e)}")
    
    # Validation methods
    def _validate_permission_data(self, data: Dict[str, Any]) -> bool:
        """Validate permission data structure"""
        required_fields = ['_id']
        return all(field in data for field in required_fields)
    
    def _validate_role_data(self, data: Dict[str, Any]) -> bool:
        """Validate role data structure"""
        required_fields = ['_id']
        return all(field in data for field in required_fields)
    
    def _validate_user_data(self, data: Dict[str, Any]) -> bool:
        """Validate user data structure"""
        required_fields = ['username']
        return all(field in data for field in required_fields)
    
    def _validate_organization_data(self, data: Dict[str, Any]) -> bool:
        """Validate organization data structure"""
        required_fields = ['_id']
        return all(field in data for field in required_fields)
    
    def _validate_project_data(self, data: Dict[str, Any]) -> bool:
        """Validate project data structure"""
        required_fields = ['_id']
        return all(field in data for field in required_fields)

# Factory function
def create_data_initializer(database) -> DataInitializer:
    """Create data initializer instance"""
    return DataInitializer(database)

# Convenience functions for backward compatibility
async def initialize_permissions(database=None):
    """Initialize permissions with dependency injection"""
    if database is None:
        from ..core.database import database
    
    initializer = create_data_initializer(database)
    return await initializer.initialize_permissions()

async def initialize_roles(database=None):
    """Initialize roles with dependency injection"""
    if database is None:
        from ..core.database import database
    
    initializer = create_data_initializer(database)
    return await initializer.initialize_roles()

async def initialize_users(database=None):
    """Initialize users with dependency injection"""
    if database is None:
        from ..core.database import database
    
    initializer = create_data_initializer(database)
    return await initializer.initialize_users()

async def initialize_organizations(database=None):
    """Initialize organizations with dependency injection"""
    if database is None:
        from ..core.database import database
    
    initializer = create_data_initializer(database)
    return await initializer.initialize_organizations()

async def initialize_projects(database=None):
    """Initialize projects with dependency injection"""
    if database is None:
        from ..core.database import database
    
    initializer = create_data_initializer(database)
    return await initializer.initialize_projects()

async def initialize_all_data(database=None) -> Dict[str, int]:
    """Initialize all data types"""
    if database is None:
        from ..core.database import database
    
    initializer = create_data_initializer(database)
    return await initializer.initialize_all_data()
