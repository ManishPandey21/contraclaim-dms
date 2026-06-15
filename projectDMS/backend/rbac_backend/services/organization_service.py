# services/organization_service.py

import logging
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from bson import ObjectId

from ..core.config import settings
from ..core.database import get_database
from ..models.organization import Organization
from ..models.organization import OrganizationCreate, OrganizationUpdate, OrganizationResponse
from ..utils.error_handler import OrganizationError, ValidationError
from ..utils.audit_logger import AuditLogger

logger = logging.getLogger(__name__)

class OrganizationServiceError(Exception):
    """Custom exception for organization service errors."""
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code

class OrganizationService:
    """Service for managing organizations."""
    
    def __init__(self):
        self.db = None
        self.audit_logger = AuditLogger()
        
    async def _get_db(self):
        """Get database connection."""
        if self.db is None:
            self.db = await get_database()
        return self.db
    
    async def create_organization(
        self,
        org_data: OrganizationCreate,
        created_by: Any
    ) -> Organization:
        """Create a new organization and provision its initial subscription."""
        try:
            db = await self._get_db()
            
            # Check for duplicate organization name
            existing_org = await db.organizations.find_one({"name": org_data.name})
            if existing_org:
                raise OrganizationServiceError(f"Organization '{org_data.name}' already exists", 409)
            
            # Create organization document
            org_doc = {
                "name": org_data.name,
                "shortName": org_data.shortName,
                "email": org_data.email,
                "phone": org_data.phone,
                "panNumber": org_data.panNumber,
                "gstNumber": org_data.gstNumber,
                "address": org_data.address,
                "city": org_data.city,
                "state": org_data.state,
                "pinCode": org_data.pinCode,
                "adminName": org_data.adminName,
                "adminEmail": org_data.adminEmail,
                "adminContact": org_data.adminContact,
                "billingEnabled": org_data.billingEnabled or False,
                "is_active": True,
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow(),
                "created_by": getattr(created_by, 'id', str(created_by))
            }
            
            # Insert organization
            result = await db.organizations.insert_one(org_doc)
            org_id = str(result.inserted_id)
            
            # --- Provision initial subscription ---
            await self._provision_initial_subscription(
                org_id=org_id,
                plan_code=getattr(org_data, "plan_code", None),
                subscription_status=getattr(org_data, "subscription_status", None),
                trial_days=getattr(org_data, "trial_days", None),
                created_by=created_by,
                billing_period=getattr(org_data, "billing_period", None),
                add_on_codes=getattr(org_data, "add_on_codes", None),
            )
            
            # Return created organization
            org_doc["id"] = org_id
            org_doc.pop("_id", None)
            
            return Organization(**org_doc)
            
        except OrganizationServiceError:
            raise
        except Exception as e:
            logger.error(f"Failed to create organization: {str(e)}")
            raise OrganizationServiceError("Organization creation failed")

    async def _provision_initial_subscription(
        self,
        org_id: str,
        plan_code: str | None,
        subscription_status: str | None,
        trial_days: int | None,
        created_by: Any,
        billing_period: str | None = None,
        add_on_codes: list | None = None,
    ) -> None:
        """Provision the initial subscription for a newly created organization."""
        from .monetization_service import MonetizationService
        from ..models.rbac_monetization import SubscriptionCreate

        effective_plan = plan_code or "no_service_override"
        effective_status = subscription_status or ("trial" if plan_code else "active")
        effective_billing_period = billing_period or "monthly"

        now = datetime.utcnow()
        starts_at = now
        ends_at = None
        trial_ends_at = None
        is_trial = effective_status == "trial"
        is_pilot = effective_status == "pilot"

        if is_trial and trial_days:
            from datetime import timedelta
            ends_at = now + timedelta(days=trial_days)
            trial_ends_at = ends_at
        elif is_pilot and trial_days:
            from datetime import timedelta
            ends_at = now + timedelta(days=trial_days)

        # Compute period boundaries
        from datetime import timedelta
        period_map = {"quarterly": 90, "semi_annual": 182, "annual": 365}
        period_days = period_map.get(effective_billing_period, 30)
        current_period_end = now + timedelta(days=period_days)

        payload = SubscriptionCreate(
            organization_id=org_id,
            plan_code=effective_plan,
            billing_period=effective_billing_period,
            status=effective_status,
            billing_status="active",
            starts_at=starts_at,
            ends_at=ends_at,
            current_period_start=now,
            current_period_end=current_period_end,
            trial=is_trial,
            trial_ends_at=trial_ends_at,
            pilot=is_pilot,
            auto_renew=not is_trial,
            active_add_ons=add_on_codes or [],
        )

        try:
            monetization = MonetizationService()
            await monetization.create_subscription(payload, created_by)
            logger.info(f"Provisioned subscription '{effective_plan}' ({effective_status}) for org {org_id}")
        except Exception as e:
            # Log but don't fail org creation — subscription can be assigned manually
            logger.error(f"Failed to provision subscription for org {org_id}: {e}")

    
    async def get_organization_by_id(self, org_id: str) -> Optional[Organization]:
        """Get organization by ID."""
        try:
            db = await self._get_db()
            
            # Handle ObjectId conversion
            try:
                query_id = ObjectId(org_id)
            except:
                query_id = org_id
            
            org_doc = await db.organizations.find_one({"_id": query_id})
            
            if not org_doc:
                return None
            
            # Convert ObjectId to string
            org_doc["id"] = str(org_doc["_id"])
            org_doc.pop("_id", None)
            
            return Organization(**org_doc)
            
        except Exception as e:
            logger.error(f"Failed to get organization {org_id}: {str(e)}")
            return None
    
    async def get_organization_by_name(self, name: str) -> Optional[Organization]:
        """Get organization by name."""
        try:
            db = await self._get_db()
            
            org_doc = await db.organizations.find_one({"name": name})
            
            if not org_doc:
                return None
            
            # Convert ObjectId to string
            org_doc["id"] = str(org_doc["_id"])
            org_doc.pop("_id", None)
            
            return Organization(**org_doc)
            
        except Exception as e:
            logger.error(f"Failed to get organization by name {name}: {str(e)}")
            return None
    

    async def get_organizations_paginated(
        self,
        filters: Dict[str, Any],
        pagination: Dict[str, int]
    ) -> Tuple[List[Organization], int]:
        """Get organizations with pagination and filtering."""
        try:
            db = await self._get_db()

            data_filters = dict(filters or {})
            allowed_ids = data_filters.pop('allowed_ids', None)

            query: Dict[str, Any] = {}

            search_term = data_filters.get('search')
            if search_term:
                query['$or'] = [
                    {'name': {'$regex': search_term, '$options': 'i'}},
                    {'shortName': {'$regex': search_term, '$options': 'i'}},
                    {'city': {'$regex': search_term, '$options': 'i'}}
                ]

            name_filter = data_filters.get('name')
            if name_filter:
                query['name'] = {'$regex': name_filter, '$options': 'i'}

            city_filter = data_filters.get('city')
            if city_filter:
                query['city'] = {'$regex': city_filter, '$options': 'i'}

            state_filter = data_filters.get('state')
            if state_filter:
                query['state'] = {'$regex': state_filter, '$options': 'i'}

            query['is_active'] = {'$ne': False}

            if allowed_ids:
                if not isinstance(allowed_ids, (list, tuple, set)):
                    allowed_ids = [allowed_ids]
                coerced_ids = []
                for value in allowed_ids:
                    if not value:
                        continue
                    try:
                        coerced_ids.append(ObjectId(str(value)))
                    except Exception:
                        coerced_ids.append(str(value))
                if coerced_ids:
                    if len(coerced_ids) == 1:
                        query['_id'] = coerced_ids[0]
                    else:
                        query['_id'] = {'$in': coerced_ids}
                else:
                    query['_id'] = {'$in': []}

            total_count = await db.organizations.count_documents(query)

            cursor = db.organizations.find(query).skip(pagination['skip']).limit(pagination['limit'])
            org_docs = await cursor.to_list(length=pagination['limit'])

            organizations = []
            for doc in org_docs:
                doc['id'] = str(doc['_id'])
                doc.pop('_id', None)
                organizations.append(Organization(**doc))

            return organizations, total_count

        except Exception as e:
            logger.error(f"Failed to get organizations: {str(e)}")
            return [], 0

    async def update_organization(
        self,
        org_id: str,
        update_data: OrganizationUpdate,
        updated_by: Any
    ) -> Organization:
        """Update organization."""
        try:
            db = await self._get_db()
            
            # Handle ObjectId conversion
            try:
                query_id = ObjectId(org_id)
            except:
                query_id = org_id
            
            # Build update document
            update_doc = {
                "updated_at": datetime.utcnow(),
                "updated_by": getattr(updated_by, 'id', str(updated_by))
            }
            
            # Add fields that are being updated
            for field in ['name', 'shortName', 'email', 'phone', 'panNumber', 'gstNumber',
                         'address', 'city', 'state', 'pinCode', 'adminName', 'adminEmail', 
                         'adminContact', 'billingEnabled']:
                value = getattr(update_data, field, None)
                if value is not None:
                    update_doc[field] = value
            
            # Update organization
            result = await db.organizations.update_one(
                {"_id": query_id},
                {"$set": update_doc}
            )
            
            if result.matched_count == 0:
                raise OrganizationServiceError("Organization not found", 404)
            
            # Return updated organization
            updated_org = await self.get_organization_by_id(org_id)
            return updated_org
            
        except OrganizationServiceError:
            raise
        except Exception as e:
            logger.error(f"Failed to update organization {org_id}: {str(e)}")
            raise OrganizationServiceError("Organization update failed")
    
    async def delete_organization(self, org_id: str, deleted_by: Any) -> bool:
        """Soft delete organization."""
        try:
            db = await self._get_db()
            
            # Handle ObjectId conversion
            try:
                query_id = ObjectId(org_id)
            except:
                query_id = org_id
            
            # Soft delete (mark as inactive)
            result = await db.organizations.update_one(
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
            logger.error(f"Failed to delete organization {org_id}: {str(e)}")
            return False
    
    async def organization_exists_by_name(self, name: str) -> bool:
        """Check if organization exists by name."""
        try:
            db = await self._get_db()
            count = await db.organizations.count_documents({"name": name, "is_active": True})
            return count > 0
        except Exception as e:
            logger.error(f"Failed to check organization existence by name: {str(e)}")
            return False
    
    async def organization_exists_by_pan(self, pan_number: str) -> bool:
        """Check if organization exists by PAN number."""
        try:
            db = await self._get_db()
            count = await db.organizations.count_documents({"panNumber": pan_number, "is_active": True})
            return count > 0
        except Exception as e:
            logger.error(f"Failed to check organization existence by PAN: {str(e)}")
            return False
    
    async def check_organization_dependencies(self, org_id: str) -> List[str]:
        """Check for organization dependencies before deletion."""
        try:
            db = await self._get_db()
            dependencies = []
            
            # Check for users
            user_count = await db.users.count_documents({"organization_id": org_id, "is_active": True})
            if user_count > 0:
                dependencies.append(f"{user_count} active users")
            
            # Check for projects
            project_count = await db.projects.count_documents({"organization_id": org_id, "is_active": True})
            if project_count > 0:
                dependencies.append(f"{project_count} active projects")
            
            # Check for documents
            document_count = await db.documents.count_documents({"organization_id": org_id, "is_active": True})
            if document_count > 0:
                dependencies.append(f"{document_count} active documents")
            
            return dependencies
            
        except Exception as e:
            logger.error(f"Failed to check organization dependencies: {str(e)}")
            return []
    
    async def get_organization_stats(self, org_id: str) -> Dict[str, Any]:
        """Get organization statistics."""
        try:
            db = await self._get_db()
            
            stats = {
                "users_count": await db.users.count_documents({"organization_id": org_id, "is_active": True}),
                "projects_count": await db.projects.count_documents({"organization_id": org_id, "is_active": True}),
                "documents_count": await db.documents.count_documents({"organization_id": org_id, "is_active": True}),
                "contracts_count": await db.contracts.count_documents({"organization_id": org_id, "is_active": True}),
            }
            
            return stats
            
        except Exception as e:
            logger.error(f"Failed to get organization stats: {str(e)}")
            return {}
    
    async def validate_organization_data(self, org_id: str) -> Dict[str, Any]:
        """Validate organization data integrity."""
        try:
            db = await self._get_db()
            validation_results = {"is_valid": True, "issues": []}
            
            # Get organization
            org = await self.get_organization_by_id(org_id)
            if not org:
                validation_results["is_valid"] = False
                validation_results["issues"].append("Organization not found")
                return validation_results
            
            # Check required fields
            if not org.name:
                validation_results["is_valid"] = False
                validation_results["issues"].append("Organization name is missing")
            
            # Check data integrity
            if org.panNumber:
                # Check PAN format
                import re
                if not re.match(r'^[A-Z]{5}[0-9]{4}[A-Z]$', org.panNumber):
                    validation_results["is_valid"] = False
                    validation_results["issues"].append("Invalid PAN number format")
            
            if org.gstNumber:
                # Check GST format
                if not re.match(r'^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$', org.gstNumber):
                    validation_results["is_valid"] = False
                    validation_results["issues"].append("Invalid GST number format")
            
            # Check email format
            if org.email:
                if not re.match(r'^[^@]+@[^@]+\.[^@]+$', org.email):
                    validation_results["is_valid"] = False
                    validation_results["issues"].append("Invalid email format")
            
            return validation_results
            
        except Exception as e:
            logger.error(f"Failed to validate organization data: {str(e)}")
            return {"is_valid": False, "issues": ["Validation failed"]}
    
    async def get_all_organizations(self) -> List[Organization]:
        """Get all active organizations."""
        try:
            organizations, _ = await self.get_organizations_paginated({}, {"skip": 0, "limit": 1000})
            return organizations
        except Exception as e:
            logger.error(f"Failed to get all organizations: {str(e)}")
            return []


