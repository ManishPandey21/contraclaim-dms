# services/party_service.py

import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from bson import ObjectId

from ..core.config import settings
from ..core.database import get_database

from ..models.party import Party, PartyType, PartyCreate, PartyUpdate, PartyResponse
from ..utils.error_handler import PartyError, ValidationError
from ..utils.audit_logger import AuditLogger

logger = logging.getLogger(__name__)

class PartyServiceError(Exception):
    """Custom exception for party service errors."""
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code

class PartyService:
    """Service for managing parties."""

    def __init__(self):
        self.db = None
        self.audit_logger = AuditLogger()

    async def _get_db(self):
        """Get database connection."""
        if self.db is None:
            self.db = await get_database()
        return self.db

    async def create_party(
        self,
        party_data: PartyCreate,
        created_by: Any
    ) -> Party:
        """Create a new party."""
        try:
            db = await self._get_db()

            # Check for duplicate party name within the same organization/context
            existing_party = await db.parties.find_one({
                "name": party_data.name,
                "organization_id": party_data.organization_id,
                "is_active": True
            })

            if existing_party:
                raise PartyServiceError(f"Party '{party_data.name}' already exists", 409)

            # Create party document
            party_doc = {
                "name": party_data.name,
                "type": party_data.type.value,
                "contact_email": party_data.contact_email,
                "contact_phone": party_data.contact_phone,
                "address": party_data.address,
                "city": party_data.city,
                "state": party_data.state,
                "pin_code": party_data.pin_code,
                "country": party_data.country,
                "organization_id": party_data.organization_id,
                "projects": party_data.projects or [],
                "representatives": party_data.representatives or [],
                "is_active": True,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
                "created_by": getattr(created_by, 'id', str(created_by))
            }

            # Insert party
            result = await db.parties.insert_one(party_doc)
            party_id = str(result.inserted_id)

            # Return created party
            party_doc["id"] = party_id
            party_doc.pop("_id", None)

            return Party(**party_doc)

        except PartyServiceError:
            raise
        except Exception as e:
            logger.error(f"Failed to create party: {str(e)}")
            raise PartyServiceError("Party creation failed")

    async def get_party_by_id(self, party_id: str) -> Optional[Party]:
        """Get party by ID."""
        try:
            db = await self._get_db()

            # Handle ObjectId conversion
            try:
                query_id = ObjectId(party_id)
            except:
                query_id = party_id

            party_doc = await db.parties.find_one({"_id": query_id, "is_active": True})

            if not party_doc:
                return None

            # Convert ObjectId to string
            party_doc["id"] = str(party_doc["_id"])
            party_doc.pop("_id", None)

            return Party(**party_doc)

        except Exception as e:
            logger.error(f"Failed to get party {party_id}: {str(e)}")
            return None

    async def get_parties_paginated(
        self,
        filters: Dict[str, Any],
        pagination: Dict[str, int]
    ) -> Tuple[List[Party], int]:
        """Get parties with pagination and filtering."""
        try:
            db = await self._get_db()

            # Build query
            query = {"is_active": True}

            # Search filter
            if filters.get("search"):
                query["$or"] = [
                    {"name": {"$regex": filters["search"], "$options": "i"}},
                    {"contact_email": {"$regex": filters["search"], "$options": "i"}},
                    {"city": {"$regex": filters["search"], "$options": "i"}}
                ]

            # Type filter
            if filters.get("type"):
                query["type"] = filters["type"].value if hasattr(filters["type"], 'value') else filters["type"]

            # Project filter
            if filters.get("project_id"):
                query["projects"] = filters["project_id"]

            # Organization filter
            if filters.get("organization_id"):
                query["organization_id"] = filters["organization_id"]

            # Get total count
            total_count = await db.parties.count_documents(query)

            # Get paginated results
            cursor = db.parties.find(query).skip(pagination["skip"]).limit(pagination["limit"])
            party_docs = await cursor.to_list(length=pagination["limit"])

            # Convert to Party objects
            parties = []
            for doc in party_docs:
                doc["id"] = str(doc["_id"])
                doc.pop("_id", None)
                parties.append(Party(**doc))

            return parties, total_count

        except Exception as e:
            logger.error(f"Failed to get parties: {str(e)}")
            return [], 0

    async def get_external_parties_paginated(
        self,
        filters: Dict[str, Any],
        pagination: Dict[str, int]
    ) -> Tuple[List[Party], int]:
        """Get external parties (not tied to specific organizations)."""
        try:
            db = await self._get_db()

            # Build query for external parties
            query = {
                "is_active": True,
                "$or": [
                    {"organization_id": None},
                    {"organization_id": {"$exists": False}}
                ]
            }

            # Search filter
            if filters.get("search"):
                query["$and"] = [
                    query,
                    {
                        "$or": [
                            {"name": {"$regex": filters["search"], "$options": "i"}},
                            {"contact_email": {"$regex": filters["search"], "$options": "i"}},
                            {"city": {"$regex": filters["search"], "$options": "i"}}
                        ]
                    }
                ]

            # Type filter
            if filters.get("party_type"):
                if "type" not in query:
                    query["type"] = filters["party_type"].value if hasattr(filters["party_type"], 'value') else filters["party_type"]

            # Get total count
            total_count = await db.parties.count_documents(query)

            # Get paginated results
            cursor = db.parties.find(query).skip(pagination["skip"]).limit(pagination["limit"])
            party_docs = await cursor.to_list(length=pagination["limit"])

            # Convert to Party objects
            parties = []
            for doc in party_docs:
                doc["id"] = str(doc["_id"])
                doc.pop("_id", None)
                parties.append(Party(**doc))

            return parties, total_count

        except Exception as e:
            logger.error(f"Failed to get external parties: {str(e)}")
            return [], 0

    async def update_party(
        self,
        party_id: str,
        update_data: PartyUpdate,
        updated_by: Any
    ) -> Party:
        """Update party."""
        try:
            db = await self._get_db()

            # Handle ObjectId conversion
            try:
                query_id = ObjectId(party_id)
            except:
                query_id = party_id

            # Build update document
            update_doc = {
                "updated_at": datetime.utcnow(),
                "updated_by": getattr(updated_by, 'id', str(updated_by))
            }

            # Add fields that are being updated
            for field in ['name', 'type', 'contact_email', 'contact_phone', 'address',
                         'city', 'state', 'pin_code', 'country', 'organization_id',
                         'projects', 'representatives']:
                value = getattr(update_data, field, None)
                if value is not None:
                    if field == 'type' and hasattr(value, 'value'):
                        update_doc[field] = value.value
                    else:
                        update_doc[field] = value

            # Update party
            result = await db.parties.update_one(
                {"_id": query_id},
                {"$set": update_doc}
            )

            if result.matched_count == 0:
                raise PartyServiceError("Party not found", 404)

            # Return updated party
            updated_party = await self.get_party_by_id(party_id)
            return updated_party

        except PartyServiceError:
            raise
        except Exception as e:
            logger.error(f"Failed to update party {party_id}: {str(e)}")
            raise PartyServiceError("Party update failed")

    async def delete_party_with_cascade(self, party_id: str, deleted_by: Any) -> bool:
        """Soft delete party with cascade operations."""
        try:
            db = await self._get_db()

            # Handle ObjectId conversion
            try:
                query_id = ObjectId(party_id)
            except:
                query_id = party_id

            # Soft delete (mark as inactive)
            result = await db.parties.update_one(
                {"_id": query_id},
                {
                    "$set": {
                        "is_active": False,
                        "deleted_at": datetime.utcnow(),
                        "deleted_by": getattr(deleted_by, 'id', str(deleted_by))
                    }
                }
            )

            if result.modified_count > 0:
                # Remove party from projects
                await db.projects.update_many(
                    {"parties": party_id},
                    {"$pull": {"parties": party_id}}
                )

                # Remove party from contracts
                await db.contracts.update_many(
                    {"parties": party_id},
                    {"$pull": {"parties": party_id}}
                )

            return result.modified_count > 0

        except Exception as e:
            logger.error(f"Failed to delete party {party_id}: {str(e)}")
            return False

    async def associate_with_project(
        self,
        party_id: str,
        project_id: str,
        associated_by: Any
    ) -> bool:
        """Associate party with project."""
        try:
            db = await self._get_db()

            # Add project to party's projects list
            result1 = await db.parties.update_one(
                {"_id": ObjectId(party_id)},
                {
                    "$addToSet": {"projects": project_id},
                    "$set": {
                        "updated_at": datetime.utcnow(),
                        "updated_by": getattr(associated_by, 'id', str(associated_by))
                    }
                }
            )

            # Add party to project's parties list
            result2 = await db.projects.update_one(
                {"_id": ObjectId(project_id)},
                {
                    "$addToSet": {"parties": party_id},
                    "$set": {
                        "updated_at": datetime.utcnow(),
                        "updated_by": getattr(associated_by, 'id', str(associated_by))
                    }
                }
            )

            return result1.modified_count > 0 and result2.modified_count > 0

        except Exception as e:
            logger.error(f"Failed to associate party with project: {str(e)}")
            return False

    async def check_party_dependencies(self, party_id: str) -> List[str]:
        """Check for party dependencies before deletion."""
        try:
            db = await self._get_db()
            dependencies = []

            # Check for contracts
            contract_count = await db.contracts.count_documents({
                "parties": party_id,
                "is_active": True
            })
            if contract_count > 0:
                dependencies.append(f"{contract_count} active contracts")

            # Check for projects
            project_count = await db.projects.count_documents({
                "parties": party_id,
                "is_active": True
            })
            if project_count > 0:
                dependencies.append(f"{project_count} active projects")

            # Check for documents
            document_count = await db.documents.count_documents({
                "party_id": party_id,
                "is_active": True
            })
            if document_count > 0:
                dependencies.append(f"{document_count} active documents")

            return dependencies

        except Exception as e:
            logger.error(f"Failed to check party dependencies: {str(e)}")
            return []

    async def get_parties_by_organization(self, organization_id: str) -> List[Party]:
        """Get all parties for a specific organization."""
        try:
            parties, _ = await self.get_parties_paginated(
                {"organization_id": organization_id},
                {"skip": 0, "limit": 1000}
            )
            return parties
        except Exception as e:
            logger.error(f"Failed to get parties for organization: {str(e)}")
            return []

    async def get_parties_by_project(self, project_id: str) -> List[Party]:
        """Get all parties for a specific project."""
        try:
            parties, _ = await self.get_parties_paginated(
                {"project_id": project_id},
                {"skip": 0, "limit": 1000}
            )
            return parties
        except Exception as e:
            logger.error(f"Failed to get parties for project: {str(e)}")
            return []
