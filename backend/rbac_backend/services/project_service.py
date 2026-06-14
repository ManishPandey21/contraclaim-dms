# services/project_service.py

import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from bson import ObjectId

from ..core.config import settings
from ..core.database import get_database
from ..utils.error_handler import ProjectError, ValidationError
from ..utils.audit_logger import AuditLogger

logger = logging.getLogger(__name__)

class ProjectServiceError(Exception):
    """Custom exception for project service errors."""
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code

class ProjectService:
    """Service for managing projects."""
    
    def __init__(self):
        self.db = None
        self.audit_logger = AuditLogger()

    def _org_match_clause(self, org_id: Optional[str]) -> Dict[str, Any]:
        """
        Return a filter that matches both string and ObjectId representations
        of the given organization id to avoid type-mismatch misses.
        """
        if not org_id:
            return {}
        clauses = [{"organization_id": org_id}]
        try:
            clauses.append({"organization_id": ObjectId(org_id)})
        except Exception:
            pass
        return {"$or": clauses}
        
    async def _get_db(self):
        """Get database connection."""
        if self.db is None:
            self.db = await get_database()
        return self.db
    
    async def get_project_by_id(self, project_id: str) -> Optional[Dict[str, Any]]:
        """Get project by ID."""
        try:
            db = await self._get_db()
            
            # Handle ObjectId conversion
            try:
                query_id = ObjectId(project_id)
            except:
                query_id = project_id
            
            project_doc = await db.projects.find_one({
                "_id": query_id,
                "$or": [
                    {"is_active": True},
                    {"is_active": {"$exists": False}}
                ]
            })
            
            if not project_doc:
                return None
            
            # Keep _id as string for frontend compatibility
            if "_id" in project_doc:
                project_doc["_id"] = str(project_doc["_id"])
            
            return project_doc
            
        except Exception as e:
            logger.error(f"Failed to get project {project_id}: {str(e)}")
            return None
    
    async def get_projects_by_organization(self, organization_id: str) -> List[Dict[str, Any]]:
        """Get all projects for a specific organization."""
        try:
            db = await self._get_db()
            
            org_filter = self._org_match_clause(organization_id)
            base_filter: Dict[str, Any] = {
                "$or": [
                    {"is_active": True},
                    {"is_active": {"$exists": False}}
                ]
            }
            if org_filter:
                base_filter.update(org_filter)

            cursor = db.projects.find(base_filter)
            project_docs = await cursor.to_list(length=None)
            
            # Keep _id as string for frontend compatibility
            projects = []
            for doc in project_docs:
                if "_id" in doc:
                    doc["_id"] = str(doc["_id"])
                projects.append(doc)
            
            return projects
            
        except Exception as e:
            logger.error(f"Failed to get projects for organization: {str(e)}")
            return []
    
    async def get_projects_paginated(
        self,
        filters: Dict[str, Any],
        pagination: Dict[str, int]
    ) -> Tuple[List[Dict[str, Any]], int]:
        """Get projects with pagination and filtering."""
        try:
            db = await self._get_db()
            
            # Build query
            base_clauses: list[Dict[str, Any]] = [
                {
                    "$or": [
                        {"is_active": True},
                        {"is_active": {"$exists": False}}
                    ]
                }
            ]

            # Apply direct filters passed from router (e.g., scoped ids)
            for key, value in (filters or {}).items():
                if key in {"search", "organization_id"}:
                    continue
                if value is None:
                    continue
                base_clauses.append({key: value})
            
            # Search filter
            if filters.get("search"):
                base_clauses.append({
                    "$or": [
                        {"name": {"$regex": filters["search"], "$options": "i"}},
                        {"description": {"$regex": filters["search"], "$options": "i"}}
                    ]
                })
            
            # Organization filter
            if filters.get("organization_id"):
                org_filter = self._org_match_clause(filters["organization_id"])
                if org_filter:
                    base_clauses.append(org_filter)

            query = {"$and": base_clauses} if len(base_clauses) > 1 else base_clauses[0]
            
            # Get total count
            total_count = await db.projects.count_documents(query)
            
            # Get paginated results
            cursor = db.projects.find(query).skip(pagination["skip"]).limit(pagination["limit"])
            project_docs = await cursor.to_list(length=pagination["limit"])
            
            # Convert to project objects - keep _id for frontend compatibility
            projects = []
            for doc in project_docs:
                # Keep _id as string for frontend compatibility
                if "_id" in doc:
                    doc["_id"] = str(doc["_id"])
                projects.append(doc)
            
            logger.info(f"Retrieved {len(projects)} projects from database")
            return projects, total_count
            
        except Exception as e:
            logger.error(f"Failed to get projects: {str(e)}")
            return [], 0
    
    async def check_project_access(
        self,
        user_id: str,
        project_id: str,
        action: str
    ) -> bool:
        """Check if user has access to project for specific action."""
        try:
            db = await self._get_db()
            
            # Get project
            project = await self.get_project_by_id(project_id)
            if not project:
                return False
            
            # Get user
            try:
                user_query_id = ObjectId(user_id)
            except Exception:
                user_query_id = user_id
            user_doc = await db.users.find_one({"_id": user_query_id}) or await db.users.find_one({"_id": str(user_id)})
            if not user_doc:
                return False
            
            user_roles = user_doc.get("roles", [])
            user_org_id = user_doc.get("organization_id")
            user_projects = user_doc.get("projects", [])
            
            # Superadmin has access to all projects
            if 'superadmin' in user_roles:
                return True
            
            # Organization admin can access all projects in their organization
            if 'orgadmin' in user_roles and user_org_id == project.get("organization_id"):
                return True
            
            # Project admin can access their assigned projects
            if 'projectadmin' in user_roles and project_id in user_projects:
                return True
            
            # Regular users can only read projects they're assigned to
            if action == 'read' and 'user' in user_roles and project_id in user_projects:
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Failed to check project access: {str(e)}")
            return False
    
    async def create_project(
        self,
        project_data: Dict[str, Any],
        created_by: Any
    ) -> Dict[str, Any]:
        """Create a new project."""
        try:
            db = await self._get_db()
            
            # Create project document
            project_doc = {
                **project_data,
                "is_active": True,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
                "created_by": getattr(created_by, 'id', str(created_by)),
                "parties": project_data.get("parties", []),
                "documents": project_data.get("documents", [])
            }
            
            # Insert project
            result = await db.projects.insert_one(project_doc)
            project_id = str(result.inserted_id)
            
            # Return created project
            project_doc["id"] = project_id
            project_doc.pop("_id", None)
            
            return project_doc
            
        except Exception as e:
            logger.error(f"Failed to create project: {str(e)}")
            raise ProjectServiceError("Project creation failed")
    
    async def update_project(
        self,
        project_id: str,
        update_data: Dict[str, Any],
        updated_by: Any
    ) -> Dict[str, Any]:
        """Update project."""
        try:
            db = await self._get_db()
            
            # Handle ObjectId conversion
            try:
                query_id = ObjectId(project_id)
            except:
                query_id = project_id
            
            # Build update document
            update_doc = {
                **update_data,
                "updated_at": datetime.utcnow(),
                "updated_by": getattr(updated_by, 'id', str(updated_by))
            }
            
            # Update project
            result = await db.projects.update_one(
                {"_id": query_id},
                {"$set": update_doc}
            )
            
            if result.matched_count == 0:
                raise ProjectServiceError("Project not found", 404)
            
            # Return updated project
            updated_project = await self.get_project_by_id(project_id)
            return updated_project
            
        except ProjectServiceError:
            raise
        except Exception as e:
            logger.error(f"Failed to update project {project_id}: {str(e)}")
            raise ProjectServiceError("Project update failed")
    
    async def delete_project(self, project_id: str, deleted_by: Any) -> bool:
        """Soft delete project."""
        try:
            db = await self._get_db()
            
            # Handle ObjectId conversion
            try:
                query_id = ObjectId(project_id)
            except:
                query_id = project_id
            
            # Soft delete (mark as inactive)
            result = await db.projects.update_one(
                {"_id": query_id},
                {
                    "$set": {
                        "is_active": False,
                        "deleted_at": datetime.utcnow(),
                        "deleted_by": getattr(deleted_by, 'id', str(deleted_by))
                    }
                }
            )
            
            return result.modified_count > 0
            
        except Exception as e:
            logger.error(f"Failed to delete project {project_id}: {str(e)}")
            return False
