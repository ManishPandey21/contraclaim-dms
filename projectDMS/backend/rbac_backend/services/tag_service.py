from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Dict, List, Optional, Tuple

from bson import ObjectId

from ..core.database import get_database
from ..models.tag import (
    Subtag,
    SubtagCreate,
    SubtagUpdate,
    Tag,
    TagCreate,
    TagResponse,
    TagUpdate,
    SubtagLookupItem,
    SubtagResponse,
)
from ..utils.error_handler import TagError

#: Upper bound on subtags one batch lookup returns; the response says when it
#: was reached instead of reading an unbounded result set.
SUBTAG_BATCH_MAX_RESULTS = 5000


class TagService:
    """Service encapsulating tag and subtag persistence."""

    def __init__(self) -> None:
        self._tags = None
        self._subtags = None
        self._documents = None
        self._organizations = None
        self._projects = None

    async def _get_handles(self):
        if self._tags is None or self._subtags is None or self._documents is None:
            db = await get_database()
            self._tags = db.tags
            self._subtags = db.subtags
            self._documents = db.documents
            # Optional collections used for enrichment
            try:
                self._organizations = getattr(db, "organizations", None)
            except Exception:
                self._organizations = None
            try:
                self._projects = getattr(db, "projects", None)
            except Exception:
                self._projects = None
        return self._tags, self._subtags, self._documents

    async def get_tag_by_name_and_org(self, name: str, organization_id: Optional[str]) -> Optional[Tag]:
        tags, *_ = await self._get_handles()
        query: Dict[str, Any] = {"name": name, "is_active": True}
        if organization_id:
            query["organization_id"] = organization_id
        doc = await tags.find_one(query)
        return self._to_tag(doc) if doc else None

    async def create_tag(
        self, tag_data: TagCreate, organization_id: Optional[str], current_user: Any
    ) -> Tag:
        tags, *_ = await self._get_handles()
        payload = tag_data.model_dump(exclude_none=True)

        # Role names normalization
        role_names: set[str] = set()
        for role in getattr(current_user, "roles", []) or []:
            if isinstance(role, str):
                role_names.add(role.lower())
            elif isinstance(role, dict):
                name = role.get("name") or role.get("role") or role.get("slug")
                if isinstance(name, str):
                    role_names.add(name.lower())
            else:
                name = (
                    getattr(role, "name", None)
                    or getattr(role, "role", None)
                    or getattr(role, "slug", None)
                )
                if isinstance(name, str):
                    role_names.add(name.lower())

        # Compute visibility and scope
        if "superadmin" in role_names:
            # superadmin can create global or org-specific tags
            if organization_id is None or str(organization_id).lower() == "global":
                # Global tag
                payload["visibility"] = "global"
                payload.pop("organization_id", None)
                payload.pop("project_id", None)
            else:
                # Organization-scoped by explicit org
                payload["visibility"] = "organization"
                payload["organization_id"] = str(organization_id)
                payload.pop("project_id", None)
        elif {"orgadmin", "orguser"} & role_names:
            # Org users must create org-scoped tags
            org = organization_id or getattr(current_user, "organization_id", None)
            if not org:
                raise TagError("Organization context required for tag creation", 403)
            payload["visibility"] = "organization"
            payload["organization_id"] = str(org)
            payload.pop("project_id", None)
        elif {"projectadmin", "projectuser"} & role_names:
            # Project users create project-scoped tags under their org and one of their projects
            org = getattr(current_user, "organization_id", None)
            projects = getattr(current_user, "projects", []) or []
            if not org or not projects:
                raise TagError("Project scope required for project-level tag creation", 403)
            payload["visibility"] = "project"
            payload["organization_id"] = str(org)
            payload["project_id"] = str(projects[0])
        else:
            # Fallback: if we have org context, use organization visibility, otherwise global
            org = organization_id or getattr(current_user, "organization_id", None)
            if org:
                payload["visibility"] = "organization"
                payload["organization_id"] = str(org)
                payload.pop("project_id", None)
            else:
                payload["visibility"] = "global"
                payload.pop("organization_id", None)
                payload.pop("project_id", None)

        payload["is_active"] = True
        payload["created_at"] = datetime.utcnow()
        payload["updated_at"] = datetime.utcnow()
        payload["created_by"] = getattr(current_user, "id", None)
        payload["updated_by"] = getattr(current_user, "id", None)

        result = await tags.insert_one(payload)
        payload["_id"] = str(result.inserted_id)
        return Tag(**payload)

    async def get_tags_paginated(
        self, filters: Dict[str, Any], pagination: Dict[str, int]
    ) -> Tuple[List[TagResponse], int]:
        tags, subtags, documents = await self._get_handles()
        criteria: Dict[str, Any] = {"is_active": True}

        # Merge authorized filters, including compound $and/$or visibility/search clauses.
        for key, value in (filters or {}).items():
            if key == "search" or value is None:
                continue
            criteria[key] = value

        # Optional free-text search if provided (augment existing $or)
        if search := filters.get("search"):
            try:
                pattern = re.escape(str(search).strip())
                if pattern:
                    search_clause = {
                        "$or": [
                        {"name": {"$regex": pattern, "$options": "i"}},
                        {"description": {"$regex": pattern, "$options": "i"}},
                        ]
                    }
                    if "$and" in criteria:
                        criteria["$and"].append(search_clause)
                    elif "$or" in criteria:
                        criteria["$and"] = [{"$or": criteria.pop("$or")}, search_clause]
                    else:
                        criteria.update(search_clause)
            except Exception:
                pass

        skip = max(pagination.get("skip", 0), 0)
        limit = max(pagination.get("limit", 20), 1)

        total = await tags.count_documents(criteria)
        docs = (
            await tags.find(criteria)
            .sort("updated_at", -1)
            .skip(skip)
            .limit(limit)
            .to_list(length=limit)
        )

        # Convert raw docs to Tag models first to gather IDs for batch lookups
        tag_models: List[Tag] = [self._to_tag(doc) for doc in docs]

        # Batch resolve organization and project names (best-effort)
        org_name_map: Dict[str, str] = {}
        proj_name_map: Dict[str, str] = {}

        org_ids = {str(t.organization_id) for t in tag_models if getattr(t, "organization_id", None)}
        proj_ids = {str(t.project_id) for t in tag_models if getattr(t, "project_id", None)}

        try:
            if getattr(self, "_organizations", None) and org_ids:
                org_query_ids = [self._to_query_id(x) for x in org_ids]
                org_docs = await self._organizations.find({"_id": {"$in": org_query_ids}}).to_list(
                    length=max(len(org_query_ids), 1)
                )
                for od in org_docs:
                    try:
                        org_name_map[str(od.get("_id"))] = od.get("name") or od.get("organization_name") or ""
                    except Exception:
                        pass
        except Exception:
            # ignore lookup errors
            pass

        try:
            if getattr(self, "_projects", None) and proj_ids:
                proj_query_ids = [self._to_query_id(x) for x in proj_ids]
                proj_docs = await self._projects.find({"_id": {"$in": proj_query_ids}}).to_list(
                    length=max(len(proj_query_ids), 1)
                )
                for pd in proj_docs:
                    try:
                        proj_name_map[str(pd.get("_id"))] = pd.get("name") or pd.get("project_name") or ""
                    except Exception:
                        pass
        except Exception:
            # ignore lookup errors
            pass

        responses: List[TagResponse] = []
        for tag in tag_models:
            sub_count = await subtags.count_documents({"tag_id": tag.id, "is_active": True})
            usage = await documents.count_documents(
                self._document_tag_usage_query(tag.id or "", tag.name)
            )

            org_name = org_name_map.get(str(tag.organization_id or ""), None)
            proj_name = proj_name_map.get(str(tag.project_id or ""), None)

            # Created-by label based on visibility
            created_by_label: Optional[str] = None
            try:
                vis = getattr(tag, "visibility", None)
                if vis == "global":
                    created_by_label = "System Created"
                elif vis == "organization":
                    created_by_label = f"Created by {org_name or 'Organization'}"
                elif vis == "project":
                    created_by_label = f"Created by {proj_name or 'Project'}"
            except Exception:
                pass

            responses.append(
                TagResponse(
                    **tag.model_dump(by_alias=True),
                    subtag_count=sub_count,
                    usage_count=usage,
                    organization_name=org_name,
                    project_name=proj_name,
                    created_by_label=created_by_label,
                )
            )
        return responses, total

    async def get_tag_by_id(self, tag_id: str) -> Optional[Tag]:
        tags, *_ = await self._get_handles()
        query_id = self._to_query_id(tag_id)
        doc = await tags.find_one({"_id": query_id, "is_active": True})
        return self._to_tag(doc) if doc else None

    async def update_tag(
        self, tag_id: str, update: TagUpdate, current_user: Any
    ) -> Tag:
        tags, *_ = await self._get_handles()
        update_fields = update.model_dump(exclude_none=True)
        if not update_fields:
            existing = await self.get_tag_by_id(tag_id)
            if not existing:
                raise TagError("Tag not found", 404)
            return existing
        update_fields["updated_at"] = datetime.utcnow()
        update_fields["updated_by"] = getattr(current_user, "id", None)
        query_id = self._to_query_id(tag_id)
        result = await tags.update_one({"_id": query_id, "is_active": True}, {"$set": update_fields})
        if result.matched_count == 0:
            raise TagError("Tag not found", 404)
        updated = await tags.find_one({"_id": query_id})
        return self._to_tag(updated)

    async def count_tag_usage(self, tag_id: str) -> int:
        _, _, documents = await self._get_handles()
        tag = await self.get_tag_by_id(tag_id)
        return await documents.count_documents(
            self._document_tag_usage_query(tag_id, getattr(tag, "name", None))
        )

    async def delete_tag_with_subtags(self, tag_id: str, current_user: Any) -> int:
        tags, subtags, _ = await self._get_handles()
        query_id = self._to_query_id(tag_id)
        result = await tags.update_one(
            {"_id": query_id, "is_active": True},
            {
                "$set": {
                    "is_active": False,
                    "updated_at": datetime.utcnow(),
                    "updated_by": getattr(current_user, "id", None),
                }
            },
        )
        if result.matched_count == 0:
            raise TagError("Tag not found", 404)
        tag_id_values = self._document_lookup_values(tag_id)
        subtag_result = await subtags.update_many(
            {"tag_id": {"$in": tag_id_values}},
            {"$set": {"is_active": False, "updated_at": datetime.utcnow()}},
        )
        return subtag_result.modified_count

    async def get_subtag_by_name_and_tag(self, name: str, tag_id: str) -> Optional[Subtag]:
        _, subtags, _ = await self._get_handles()
        pattern = f"^{re.escape(name.strip())}$"
        doc = await subtags.find_one(
            {
                "tag_id": {"$in": self._document_lookup_values(tag_id)},
                "name": {"$regex": pattern, "$options": "i"},
                "is_active": True,
            }
        )
        return self._to_subtag(doc) if doc else None

    async def create_subtag(
        self, tag_id: str, data: SubtagCreate, current_user: Any
    ) -> Subtag:
        _, subtags, _ = await self._get_handles()
        payload = data.model_dump(exclude_none=True)
        payload["tag_id"] = tag_id
        payload["is_active"] = True
        payload["created_at"] = datetime.utcnow()
        payload["updated_at"] = datetime.utcnow()
        payload["created_by"] = getattr(current_user, "id", None)
        payload["updated_by"] = getattr(current_user, "id", None)
        result = await subtags.insert_one(payload)
        payload["_id"] = str(result.inserted_id)
        return Subtag(**payload)

    async def get_subtags_paginated(
        self, tag_id: str, pagination: Dict[str, int]
    ) -> Tuple[List[SubtagResponse], int]:
        tags, subtags, documents = await self._get_handles()
        criteria = {"tag_id": tag_id, "is_active": True}
        skip = max(pagination.get("skip", 0), 0)
        limit = max(pagination.get("limit", 20), 1)
        total = await subtags.count_documents(criteria)
        docs = (
            await subtags.find(criteria)
            .sort("updated_at", -1)
            .skip(skip)
            .limit(limit)
            .to_list(length=limit)
        )
        # Build parent tag map in batch for enrichment
        parent_ids = [str(doc.get("tag_id")) for doc in docs if doc.get("tag_id")]
        parent_query_ids = [self._to_query_id(x) for x in parent_ids] if parent_ids else []
        parent_map: Dict[str, Tag] = {}
        if parent_query_ids:
            parent_docs = await tags.find({"_id": {"$in": parent_query_ids}}).to_list(length=max(len(parent_query_ids), 1))
            for pdoc in parent_docs:
                try:
                    tag = self._to_tag(pdoc)
                    parent_map[str(tag.id)] = tag
                except Exception:
                    pass

        # Resolve names for organizations/projects referenced by parent tags
        org_name_map: Dict[str, str] = {}
        proj_name_map: Dict[str, str] = {}
        try:
            org_ids = {
                str(t.organization_id)
                for t in parent_map.values()
                if getattr(t, "organization_id", None)
            }
            if getattr(self, "_organizations", None) and org_ids:
                org_docs = await self._organizations.find(
                    {"_id": {"$in": [self._to_query_id(x) for x in org_ids]}}
                ).to_list(length=max(len(org_ids), 1))
                for od in org_docs:
                    org_name_map[str(od.get("_id"))] = od.get("name") or od.get("organization_name") or ""
        except Exception:
            pass

        try:
            proj_ids = {
                str(t.project_id)
                for t in parent_map.values()
                if getattr(t, "project_id", None)
            }
            if getattr(self, "_projects", None) and proj_ids:
                proj_docs = await self._projects.find(
                    {"_id": {"$in": [self._to_query_id(x) for x in proj_ids]}}
                ).to_list(length=max(len(proj_ids), 1))
                for pd in proj_docs:
                    proj_name_map[str(pd.get("_id"))] = pd.get("name") or pd.get("project_name") or ""
        except Exception:
            pass

        responses: List[SubtagResponse] = []
        for doc in docs:
            sub = self._to_subtag(doc)
            usage = await documents.count_documents(
                self._document_subtag_usage_query(sub.id or "", sub.name)
            )

            # Determine created_by_label based on parent tag visibility
            created_by_label: Optional[str] = None
            tag_name: Optional[str] = None
            parent = parent_map.get(str(sub.tag_id))
            if parent:
                tag_name = getattr(parent, "name", None)
                vis = getattr(parent, "visibility", None)
                if vis == "global":
                    created_by_label = "System Created"
                elif vis == "organization":
                    org_name = org_name_map.get(str(getattr(parent, "organization_id", ""))) or "Organization"
                    created_by_label = f"Created by {org_name}"
                elif vis == "project":
                    proj_name = proj_name_map.get(str(getattr(parent, "project_id", ""))) or "Project"
                    created_by_label = f"Created by {proj_name}"

            responses.append(
                SubtagResponse(
                    **sub.model_dump(by_alias=True),
                    usage_count=usage,
                    tag_name=tag_name,
                    created_by_label=created_by_label,
                )
            )
        return responses, total

    async def get_subtag_lookup_for_tags(
        self, authorized_tag_query: Dict[str, Any], tag_ids: List[str]
    ) -> Tuple[List[SubtagLookupItem], bool]:
        """Active subtags of those requested tags the caller is authorized to see.

        Two queries, whatever the number of tags: the requested ids are first
        intersected with ``authorized_tag_query`` (from
        ``AuthorizationService.build_tag_query``) inside the tag query itself,
        and only the surviving parents reach the subtag query. A requested tag
        outside that universe is indistinguishable from one that does not exist.
        No usage counts or name enrichment: this feeds display lookups only.

        Returns the items and whether ``SUBTAG_BATCH_MAX_RESULTS`` cut them off.
        """
        if not tag_ids:
            return [], False
        tags, subtags, _ = await self._get_handles()
        requested = self._document_lookup_values(*tag_ids)
        parent_docs = await tags.find(
            {"$and": [authorized_tag_query, {"is_active": True}, {"_id": {"$in": requested}}]},
            {"_id": 1},
        ).to_list(length=len(requested))
        authorized_ids = [str(doc["_id"]) for doc in parent_docs if doc.get("_id") is not None]
        if not authorized_ids:
            return [], False

        docs = (
            await subtags.find(
                {"is_active": True, "tag_id": {"$in": self._document_lookup_values(*authorized_ids)}},
                {"_id": 1, "name": 1, "tag_id": 1},
            )
            .sort("name", 1)
            # Bounds the server-side sort too (top-k), not only what is read back.
            .limit(SUBTAG_BATCH_MAX_RESULTS + 1)
            .to_list(length=SUBTAG_BATCH_MAX_RESULTS + 1)
        )
        truncated = len(docs) > SUBTAG_BATCH_MAX_RESULTS
        items: List[SubtagLookupItem] = []
        for doc in docs[:SUBTAG_BATCH_MAX_RESULTS]:
            name = str(doc.get("name") or "").strip()
            if doc.get("_id") is None or not name or doc.get("tag_id") is None:
                # A malformed row must not fail the lookup for every other row.
                continue
            items.append(SubtagLookupItem(_id=str(doc["_id"]), name=name, tag_id=str(doc["tag_id"])))
        return items, truncated

    async def get_subtag_by_id(self, subtag_id: str) -> Optional[Subtag]:
        _, subtags, _ = await self._get_handles()
        query_id = self._to_query_id(subtag_id)
        doc = await subtags.find_one({"_id": query_id, "is_active": True})
        return self._to_subtag(doc) if doc else None

    async def update_subtag(
        self, subtag_id: str, update: SubtagUpdate, current_user: Any
    ) -> Subtag:
        _, subtags, _ = await self._get_handles()
        update_fields = update.model_dump(exclude_none=True)
        if not update_fields:
            existing = await self.get_subtag_by_id(subtag_id)
            if not existing:
                raise TagError("Subtag not found", 404)
            return existing
        update_fields["updated_at"] = datetime.utcnow()
        update_fields["updated_by"] = getattr(current_user, "id", None)
        query_id = self._to_query_id(subtag_id)
        result = await subtags.update_one({"_id": query_id, "is_active": True}, {"$set": update_fields})
        if result.matched_count == 0:
            raise TagError("Subtag not found", 404)
        updated = await subtags.find_one({"_id": query_id})
        return self._to_subtag(updated)

    async def count_subtag_usage(self, subtag_id: str) -> int:
        _, _, documents = await self._get_handles()
        subtag = await self.get_subtag_by_id(subtag_id)
        return await documents.count_documents(
            self._document_subtag_usage_query(
                subtag_id, getattr(subtag, "name", None)
            )
        )

    async def delete_subtag(self, subtag_id: str, current_user: Any) -> None:
        _, subtags, _ = await self._get_handles()
        query_id = self._to_query_id(subtag_id)
        result = await subtags.update_one(
            {"_id": query_id, "is_active": True},
            {
                "$set": {
                    "is_active": False,
                    "updated_at": datetime.utcnow(),
                    "updated_by": getattr(current_user, "id", None),
                }
            },
        )
        if result.matched_count == 0:
            raise TagError("Subtag not found", 404)

    def _to_query_id(self, value: str):
        try:
            return ObjectId(value)
        except Exception:
            return value

    def _document_lookup_values(self, *values: Optional[str]) -> List[Any]:
        lookup_values: List[Any] = []
        seen: set[str] = set()
        for value in values:
            text = str(value or "").strip()
            if not text or text in seen:
                continue
            seen.add(text)
            lookup_values.append(text)
            try:
                object_id = ObjectId(text)
                lookup_values.append(object_id)
            except Exception:
                pass
        return lookup_values or ["__none__"]

    def _document_tag_usage_query(
        self, tag_id: str, tag_name: Optional[str] = None
    ) -> Dict[str, Any]:
        values = self._document_lookup_values(tag_id, tag_name)
        return {
            "$or": [
                {"tags": {"$in": values}},
                {"tag": {"$in": values}},
                {"tag": {"$in": [str(value) for value in values]}},
            ]
        }

    def _document_subtag_usage_query(
        self, subtag_id: str, subtag_name: Optional[str] = None
    ) -> Dict[str, Any]:
        values = self._document_lookup_values(subtag_id, subtag_name)
        string_values = [str(value) for value in values]
        return {
            "$or": [
                {"subTags": {"$in": values}},
                {"subtags": {"$in": values}},
                {"subTag": {"$in": values}},
                {"sub_tag": {"$in": values}},
                {"subTag": {"$in": string_values}},
                {"sub_tag": {"$in": string_values}},
            ]
        }

    def _to_tag(self, doc: Optional[Dict[str, Any]]) -> Tag:
        if not doc:
            raise TagError("Tag payload missing", 500)
        payload = dict(doc)

        # Normalize identifier fields to strings
        if payload.get("_id") is not None:
            payload["_id"] = str(payload.get("_id"))

        org_id = payload.get("organization_id", None)
        if org_id is not None:
            try:
                payload["organization_id"] = str(org_id)
            except Exception:
                payload["organization_id"] = org_id

        proj_id = payload.get("project_id", None)
        if proj_id is not None:
            try:
                payload["project_id"] = str(proj_id)
            except Exception:
                payload["project_id"] = proj_id

        visibility = payload.get("visibility")
        if not visibility:
            if payload.get("project_id"):
                payload["visibility"] = "project"
            elif payload.get("organization_id"):
                payload["visibility"] = "organization"
            else:
                payload["visibility"] = "global"

        for fld in ("created_by", "updated_by"):
            if fld in payload and payload[fld] is not None:
                try:
                    payload[fld] = str(payload[fld])
                except Exception:
                    # leave as-is if not convertible; pydantic may coerce
                    pass

        return Tag(**payload)

    def _to_subtag(self, doc: Optional[Dict[str, Any]]) -> Subtag:
        if not doc:
            raise TagError("Subtag payload missing", 500)
        payload = dict(doc)

        # Normalize identifier fields to strings
        if payload.get("_id") is not None:
            payload["_id"] = str(payload.get("_id"))

        if "tag_id" in payload and payload["tag_id"] is not None:
            try:
                payload["tag_id"] = str(payload["tag_id"])
            except Exception:
                pass

        for fld in ("created_by", "updated_by"):
            if fld in payload and payload[fld] is not None:
                try:
                    payload[fld] = str(payload[fld])
                except Exception:
                    pass

        return Subtag(**payload)
