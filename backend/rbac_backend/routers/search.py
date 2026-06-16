from fastapi import APIRouter, Depends, Query, HTTPException, Body
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
from motor.motor_asyncio import AsyncIOMotorDatabase
from ..core.database import get_database
from ..core.security import get_current_user
from ..models.user import User
from ..services.policy_service import PolicyService
from ..services.scope_service import ScopeService
import re
from bson import ObjectId
import logging

logger = logging.getLogger(__name__)

router = APIRouter(tags=["search"])

@router.get("/search/documents")
async def search_documents(
    q: Optional[str] = Query(None, description="Search query"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    sort_by: str = Query("relevance", description="Sort field"),
    sort_order: str = Query("desc", description="Sort order"),
    date_from: Optional[str] = Query(None, description="Date range start (YYYY-MM-DD)"),
    date_to: Optional[str] = Query(None, description="Date range end (YYYY-MM-DD)"),
    file_types: List[str] = Query([], description="File type filters"),
    organizations: List[str] = Query([], description="Organization filters"),
    projects: List[str] = Query([], description="Project filters"),
    categories: List[str] = Query([], description="Category filters"),
    tags: List[str] = Query([], description="Tag filters"),
    upload_type: Optional[str] = Query(None, description="Upload direction (incoming/outgoing)"),
    include_facets: bool = Query(False, description="Include search facets"),
    include_content: bool = Query(False, description="Include document content"),
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Advanced document search with filtering, sorting, and faceting
    """
    try:
        start_time = datetime.now()
        requested_org = str(organizations[0]) if organizations else getattr(current_user, "organization_id", None)
        requested_project = str(projects[0]) if projects else None
        await PolicyService().authorize(
            current_user,
            "dms.document.view",
            resource_type="search",
            organization_id=requested_org,
            project_id=requested_project,
            audit=False,
        )
        
        # Build search pipeline
        pipeline = []
        match_conditions = {}

        # RBAC scoping
        roles = set(current_user.roles or [])
        if "superadmin" not in roles:
            scope = ScopeService()
            allowed_orgs = await scope.client_organization_ids(current_user)
            allowed_projects = await scope.client_project_ids(current_user)
            if "orgadmin" in roles or "orguser" in roles:
                if allowed_orgs:
                    match_conditions["organization_id"] = {"$in": sorted(allowed_orgs)}
                else:
                    # No org context => no results
                    return {"results": [], "total": 0, "page": page, "limit": limit, "time_ms": 0}
            elif "projectadmin" in roles or "projectuser" in roles:
                if allowed_projects:
                    match_conditions["project_id"] = {"$in": sorted(allowed_projects)}
                else:
                    return {"results": [], "total": 0, "page": page, "limit": limit, "time_ms": 0}
            else:
                return {"results": [], "total": 0, "page": page, "limit": limit, "time_ms": 0}
        
        # Text search. Uses the wildcard text index created once at startup in
        # core/database.py (ensure_indexes); per-request index creation was removed
        # (M5) — it added latency and silently failed against the existing index.
        if q and q.strip():
            match_conditions["$text"] = {"$search": q.strip()}
        
        # Date range filter
        if date_from or date_to:
            date_filter = {}
            if date_from:
                try:
                    date_filter["$gte"] = datetime.fromisoformat(date_from)
                except ValueError:
                    raise HTTPException(status_code=400, detail="Invalid date_from format")
            if date_to:
                try:
                    # Include the entire day
                    date_filter["$lte"] = datetime.fromisoformat(date_to) + timedelta(days=1)
                except ValueError:
                    raise HTTPException(status_code=400, detail="Invalid date_to format")
            match_conditions["createdAt"] = date_filter
        
        # File type filter
        if file_types:
            # Extract file extensions from filename
            file_type_regex = "|".join([f"\.{ft}$" for ft in file_types])
            match_conditions["filename"] = {"$regex": file_type_regex, "$options": "i"}
        
        # Organization filter
        if organizations:
            try:
                org_ids = [ObjectId(org_id) if ObjectId.is_valid(org_id) else org_id for org_id in organizations]
            except Exception:
                org_ids = organizations
            if "superadmin" not in roles:
                requested = {str(item) for item in organizations}
                permitted = requested & allowed_orgs
                if not permitted:
                    return {"results": [], "total": 0, "page": page, "limit": limit, "time_ms": 0}
                org_ids = sorted(permitted)
            match_conditions["organization_id"] = {"$in": org_ids}
        
        # Project filter
        if projects:
            try:
                project_ids = [ObjectId(proj_id) if ObjectId.is_valid(proj_id) else proj_id for proj_id in projects]
            except Exception:
                project_ids = projects
            if "superadmin" not in roles:
                requested = {str(item) for item in projects}
                permitted = requested & allowed_projects
                if not permitted:
                    return {"results": [], "total": 0, "page": page, "limit": limit, "time_ms": 0}
                project_ids = sorted(permitted)
            match_conditions["project_id"] = {"$in": project_ids}
        
        # Category filter
        if categories:
            match_conditions["categories"] = {"$in": categories}
        
        # Tags filter
        if tags:
            match_conditions["tags"] = {"$in": tags}
        
        # Upload type / Direction filter
        if upload_type:
            if upload_type not in ["incoming", "outgoing"]:
                raise HTTPException(status_code=400, detail="Invalid upload_type")
            match_conditions["uploadType"] = upload_type
        
        # Add match stage
        if match_conditions:
            pipeline.append({"$match": match_conditions})
        
        # Add score for text search
        if q and q.strip():
            pipeline.append({
                "$addFields": {
                    "score": {"$meta": "textScore"}
                }
            })
        
        # Sorting
        sort_stage = {}
        if sort_by == "relevance" and q and q.strip():
            sort_stage["score"] = {"$meta": "textScore"}
        elif sort_by == "date":
            sort_stage["createdAt"] = -1 if sort_order == "desc" else 1
        elif sort_by == "name":
            sort_stage["name"] = -1 if sort_order == "desc" else 1
        elif sort_by == "size":
            sort_stage["size"] = -1 if sort_order == "desc" else 1
        else:
            sort_stage["createdAt"] = -1  # Default sort
        
        pipeline.append({"$sort": sort_stage})
        
        # Get total count
        count_pipeline = pipeline.copy()
        count_pipeline.append({"$count": "total"})
        count_result = await db.documents.aggregate(count_pipeline).to_list(1)
        total = count_result[0]["total"] if count_result else 0
        
        # Add pagination
        skip = (page - 1) * limit
        pipeline.extend([
            {"$skip": skip},
            {"$limit": limit}
        ])
        
        # Project fields
        project_fields = {
            "_id": 1,
            "name": 1,
            "filename": 1,
            "organization_id": 1,
            "project_id": 1,
            "uploadType": 1,
            "categories": 1,
            "file_path": 1,
            "size": 1,
            "createdAt": 1,
            "updatedAt": 1
        }
        
        if q and q.strip():
            project_fields["score"] = {"$meta": "textScore"}
        
        if include_content:
            project_fields["content"] = 1
        
        # Add excerpt generation for text search
        if q and q.strip() and not include_content:
            project_fields["excerpt"] = {
                "$substr": ["$content", 0, 200]
            }
        
        pipeline.append({"$project": project_fields})
        
        # Execute search
        results = await db.documents.aggregate(pipeline).to_list(limit)
        
        # Convert ObjectIds to strings
        for result in results:
            if "_id" in result and isinstance(result["_id"], ObjectId):
                result["_id"] = str(result["_id"])
            if "organization_id" in result and isinstance(result["organization_id"], ObjectId):
                result["organization_id"] = str(result["organization_id"])
            if "project_id" in result and isinstance(result["project_id"], ObjectId):
                result["project_id"] = str(result["project_id"])
        
        # Calculate search time
        search_time = int((datetime.now() - start_time).total_seconds() * 1000)
        
        response = {
            "results": results,
            "total": total,
            "page": page,
            "limit": limit,
            "hasMore": skip + len(results) < total,
            "searchTime": search_time
        }
        
        # Add facets if requested
        if include_facets:
            facets = await _get_search_facets(db, match_conditions)
            response["facets"] = facets
        
        return response

    except HTTPException:
        # BUGFIX (H4): client validation errors (e.g. invalid date_from/date_to or
        # upload_type) raise HTTPException(400). Without re-raising here, the broad
        # `except Exception` below swallowed them and returned a misleading 500.
        raise
    except Exception as e:
        logger.error(f"Search error: {str(e)}")
        raise HTTPException(status_code=500, detail="Search failed")

@router.get("/search/suggestions")
async def get_search_suggestions(
    q: str = Query(..., min_length=2, description="Partial search query"),
    limit: int = Query(5, ge=1, le=20, description="Number of suggestions"),
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Get search suggestions based on partial query
    """
    try:
        # Search in document names and common terms
        suggestions = []
        
        # Get suggestions from document names
        name_pipeline = [
            {
                "$match": {
                    "name": {"$regex": re.escape(q), "$options": "i"}
                }
            },
            {
                "$project": {
                    "name": 1,
                    "_id": 0
                }
            },
            {"$limit": limit}
        ]
        
        name_results = await db.documents.aggregate(name_pipeline).to_list(limit)
        suggestions.extend([doc["name"] for doc in name_results])
        
        # Get suggestions from search history (if implemented)
        # This would require a search_history collection
        
        # Remove duplicates and limit
        unique_suggestions = list(dict.fromkeys(suggestions))[:limit]
        
        return {"suggestions": unique_suggestions}
        
    except Exception as e:
        logger.error(f"Suggestions error: {str(e)}")
        return {"suggestions": []}

@router.get("/search/popular")
async def get_popular_searches(
    limit: int = Query(10, ge=1, le=50, description="Number of popular searches"),
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Get popular search terms
    """
    try:
        # This would require a search_analytics collection
        # For now, return some common terms
        popular_terms = [
            {"term": "contract", "count": 150},
            {"term": "agreement", "count": 120},
            {"term": "invoice", "count": 100},
            {"term": "report", "count": 80},
            {"term": "proposal", "count": 60},
            {"term": "letter", "count": 50},
            {"term": "specification", "count": 40},
            {"term": "drawing", "count": 30},
            {"term": "certificate", "count": 25},
            {"term": "warranty", "count": 20}
        ]
        
        return {"searches": popular_terms[:limit]}
        
    except Exception as e:
        logger.error(f"Popular searches error: {str(e)}")
        return {"searches": []}

@router.post("/search/track")
async def track_search(
    search_data: Dict[str, Any] = Body(...),
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Track search query for analytics
    """
    try:
        # Store search analytics
        analytics_doc = {
            "user_id": current_user.id,
            "query": search_data.get("query", ""),
            "filters": search_data.get("filters", {}),
            "result_count": search_data.get("result_count", 0),
            "timestamp": datetime.utcnow(),
            "session_id": search_data.get("session_id"),
        }
        
        await db.search_analytics.insert_one(analytics_doc)
        return {"status": "tracked"}
        
    except Exception as e:
        logger.error(f"Search tracking error: {str(e)}")
        return {"status": "failed"}

@router.get("/search/analytics")
async def get_search_analytics(
    date_from: Optional[str] = Query(None, description="Analytics date range start"),
    date_to: Optional[str] = Query(None, description="Analytics date range end"),
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Get search analytics (admin only)
    """
    try:
        # Build date filter
        date_filter = {}
        if date_from:
            date_filter["$gte"] = datetime.fromisoformat(date_from)
        if date_to:
            date_filter["$lte"] = datetime.fromisoformat(date_to)
        
        match_condition = {}
        if date_filter:
            match_condition["timestamp"] = date_filter
        
        # Get top searches
        top_searches_pipeline = [
            {"$match": match_condition},
            {
                "$group": {
                    "_id": "$query",
                    "count": {"$sum": 1},
                    "avg_results": {"$avg": "$result_count"}
                }
            },
            {"$sort": {"count": -1}},
            {"$limit": 20}
        ]
        
        top_searches = await db.search_analytics.aggregate(top_searches_pipeline).to_list(20)
        
        # Get search volume over time
        volume_pipeline = [
            {"$match": match_condition},
            {
                "$group": {
                    "_id": {
                        "date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$timestamp"}}
                    },
                    "searches": {"$sum": 1},
                    "unique_users": {"$addToSet": "$user_id"}
                }
            },
            {
                "$project": {
                    "date": "$_id.date",
                    "searches": 1,
                    "unique_users": {"$size": "$unique_users"},
                    "_id": 0
                }
            },
            {"$sort": {"date": 1}}
        ]
        
        volume_data = await db.search_analytics.aggregate(volume_pipeline).to_list(100)
        
        return {
            "top_searches": top_searches,
            "volume_over_time": volume_data
        }
        
    except Exception as e:
        logger.error(f"Analytics error: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to get analytics")

@router.post("/search/semantic")
async def semantic_search(
    search_data: Dict[str, Any] = Body(...),
    db: AsyncIOMotorDatabase = Depends(get_database),
    current_user: User = Depends(get_current_user)
):
    """
    Perform semantic search using AI/vector similarity
    """
    try:
        query = search_data.get("query", "")
        limit = search_data.get("limit", 10)
        threshold = search_data.get("threshold", 0.7)
        
        # This would require vector embeddings and similarity search
        # For now, fall back to text search
        return await search_documents(
            q=query,
            limit=limit,
            db=db,
            current_user=current_user
        )
        
    except Exception as e:
        logger.error(f"Semantic search error: {str(e)}")
        raise HTTPException(status_code=500, detail="Semantic search failed")

async def _get_search_facets(db: AsyncIOMotorDatabase, base_match: Dict[str, Any]) -> Dict[str, Any]:
    """
    Get search facets for filtering
    """
    try:
        facets = {}
        
        # Organizations facet
        org_pipeline = [
            {"$match": base_match},
            {"$group": {"_id": "$organization_id", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 20}
        ]
        
        org_results = await db.documents.aggregate(org_pipeline).to_list(20)
        
        # Get organization names
        org_ids = [result["_id"] for result in org_results if result["_id"]]
        org_names = {}
        if org_ids:
            orgs = await db.organizations.find(
                {"_id": {"$in": org_ids}},
                {"name": 1}
            ).to_list(len(org_ids))
            org_names = {str(org["_id"]): org["name"] for org in orgs}
        
        facets["organizations"] = [
            {
                "_id": str(result["_id"]) if result["_id"] else "unknown",
                "name": org_names.get(str(result["_id"]), "Unknown"),
                "count": result["count"]
            }
            for result in org_results
        ]
        
        # Categories facet
        cat_pipeline = [
            {"$match": base_match},
            {"$unwind": "$categories"},
            {"$group": {"_id": "$categories", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 20}
        ]
        
        cat_results = await db.documents.aggregate(cat_pipeline).to_list(20)
        facets["categories"] = [
            {"name": result["_id"], "count": result["count"]}
            for result in cat_results
        ]
        
        # File types facet
        file_type_pipeline = [
            {"$match": base_match},
            {
                "$project": {
                    "file_extension": {
                        "$toLower": {
                            "$arrayElemAt": [
                                {"$split": ["$filename", "."]}, -1
                            ]
                        }
                    }
                }
            },
            {"$group": {"_id": "$file_extension", "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 10}
        ]
        
        file_type_results = await db.documents.aggregate(file_type_pipeline).to_list(10)
        facets["fileTypes"] = [
            {"type": result["_id"], "count": result["count"]}
            for result in file_type_results
        ]
        
        return facets
        
    except Exception as e:
        logger.error(f"Facets error: {str(e)}")
        return {}
