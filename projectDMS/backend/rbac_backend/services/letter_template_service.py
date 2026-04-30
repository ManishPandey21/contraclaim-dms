from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from bson import ObjectId

from ..core.database import get_database
from ..models.letter_template import (
    LetterTemplate,
    LetterTemplateCreate,
    LetterTemplateUpdate,
    TemplateSection,
    TemplateVersion,
)
from ..utils.error_handler import TemplateError


class LetterTemplateService:
    """Persistence layer for letter templates with scoped visibility."""

    def __init__(self) -> None:
        self._templates = None
        self._organizations = None
        self._projects = None

    async def _get_handles(self):
        if self._templates is None:
            db = await get_database()
            self._templates = db.letter_templates
            self._organizations = getattr(db, "organizations", None)
            self._projects = getattr(db, "projects", None)
        return self._templates

    def _extract_role_names(self, current_user: Any) -> set[str]:
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
        return role_names

    def _normalize_sections(self, sections: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for index, section in enumerate(sections):
            payload = dict(section or {})
            if not payload.get("id"):
                payload["id"] = str(uuid4())
            if payload.get("order") is None:
                payload["order"] = index
            normalized.append(payload)
        return normalized

    def _resolve_scope(
        self, payload: Dict[str, Any], current_user: Any
    ) -> Dict[str, Any]:
        role_names = self._extract_role_names(current_user)
        requested_visibility = (payload.get("visibility") or "").strip().lower()
        org_id = payload.get("organization_id")
        proj_id = payload.get("project_id")

        if "superadmin" in role_names:
            if requested_visibility == "global":
                return {"visibility": "global", "organization_id": None, "project_id": None}
            if requested_visibility == "organization":
                if not org_id:
                    raise TemplateError("organization_id is required for organization templates", 422)
                return {"visibility": "organization", "organization_id": str(org_id), "project_id": None}
            if requested_visibility == "project":
                if not proj_id:
                    raise TemplateError("project_id is required for project templates", 422)
                if not org_id:
                    org_id = getattr(current_user, "organization_id", None)
                if not org_id:
                    raise TemplateError("organization_id is required for project templates", 422)
                return {
                    "visibility": "project",
                    "organization_id": str(org_id),
                    "project_id": str(proj_id),
                }

            if proj_id:
                if not org_id:
                    org_id = getattr(current_user, "organization_id", None)
                if not org_id:
                    raise TemplateError("organization_id is required for project templates", 422)
                return {
                    "visibility": "project",
                    "organization_id": str(org_id),
                    "project_id": str(proj_id),
                }
            if org_id:
                return {
                    "visibility": "organization",
                    "organization_id": str(org_id),
                    "project_id": None,
                }
            return {"visibility": "global", "organization_id": None, "project_id": None}

        if "orgadmin" in role_names:
            org = org_id or getattr(current_user, "organization_id", None)
            if not org:
                raise TemplateError("Organization context required for templates", 403)
            if proj_id:
                return {
                    "visibility": "project",
                    "organization_id": str(org),
                    "project_id": str(proj_id),
                }
            return {"visibility": "organization", "organization_id": str(org), "project_id": None}

        if {"projectadmin", "projectuser"} & role_names:
            projects = getattr(current_user, "projects", []) or []
            if not projects and not proj_id:
                raise TemplateError("Project context required for templates", 403)
            if proj_id and projects and str(proj_id) not in {str(p) for p in projects if p}:
                raise TemplateError("Access denied to this project", 403)
            org = org_id or getattr(current_user, "organization_id", None)
            if not org:
                raise TemplateError("Organization context required for templates", 403)
            project_id = proj_id or (projects[0] if projects else None)
            return {
                "visibility": "project",
                "organization_id": str(org),
                "project_id": str(project_id),
            }

        raise TemplateError("Not authorized to create templates", 403)

    async def create_template(
        self, data: LetterTemplateCreate, current_user: Any
    ) -> LetterTemplate:
        templates = await self._get_handles()
        payload = data.model_dump(exclude_none=True)

        sections = payload.get("sections") or []
        normalized_sections = self._normalize_sections(
            [s.model_dump() if isinstance(s, TemplateSection) else dict(s) for s in sections]
        )

        scope = self._resolve_scope(payload, current_user)
        payload.update(scope)
        payload["sections"] = normalized_sections
        payload["version"] = 1
        payload["versions"] = [
            TemplateVersion(
                version=1,
                updated_at=datetime.utcnow(),
                updated_by=getattr(current_user, "id", None),
                sections=[TemplateSection(**s) for s in normalized_sections],
            ).model_dump()
        ]
        payload["usage_count"] = int(payload.get("usage_count") or 0)
        payload["is_active"] = True
        payload["created_at"] = datetime.utcnow()
        payload["updated_at"] = datetime.utcnow()
        payload["created_by"] = getattr(current_user, "id", None)
        payload["updated_by"] = getattr(current_user, "id", None)

        result = await templates.insert_one(payload)
        payload["_id"] = str(result.inserted_id)
        return LetterTemplate(**payload)

    async def get_templates_paginated(
        self, filters: Dict[str, Any], pagination: Dict[str, int]
    ) -> Tuple[List[LetterTemplate], int]:
        templates = await self._get_handles()
        criteria: Dict[str, Any] = {"is_active": True}

        for key in ("organization_id", "project_id", "visibility", "status", "category"):
            if key in filters and filters[key] is not None:
                criteria[key] = filters[key]

        scope_or = filters.get("$or")
        search = filters.get("search")

        and_clauses: List[Dict[str, Any]] = []
        if scope_or:
            and_clauses.append({"$or": scope_or})

        if search:
            try:
                pattern = str(search)
                and_clauses.append(
                    {
                        "$or": [
                            {"name": {"$regex": pattern, "$options": "i"}},
                            {"code": {"$regex": pattern, "$options": "i"}},
                            {"description": {"$regex": pattern, "$options": "i"}},
                        ]
                    }
                )
            except Exception:
                pass

        if and_clauses:
            criteria = {"$and": [criteria] + and_clauses}

        skip = max(pagination.get("skip", 0), 0)
        limit = max(pagination.get("limit", 20), 1)

        total = await templates.count_documents(criteria)
        docs = (
            await templates.find(criteria)
            .sort("updated_at", -1)
            .skip(skip)
            .limit(limit)
            .to_list(length=limit)
        )

        return [self._to_template(doc) for doc in docs], total

    async def get_template_by_id(self, template_id: str) -> Optional[LetterTemplate]:
        templates = await self._get_handles()
        query_id = self._to_query_id(template_id)
        doc = await templates.find_one({"_id": query_id, "is_active": True})
        return self._to_template(doc) if doc else None

    async def update_template(
        self, template_id: str, update: LetterTemplateUpdate, current_user: Any
    ) -> LetterTemplate:
        templates = await self._get_handles()
        existing = await self.get_template_by_id(template_id)
        if not existing:
            raise TemplateError("Template not found", 404)

        update_fields = update.model_dump(exclude_none=True)
        role_names = self._extract_role_names(current_user)
        if "superadmin" not in role_names:
            for restricted_field in ("visibility", "organization_id", "project_id"):
                if restricted_field in update_fields:
                    raise TemplateError(
                        "Only superadmin can change template scope", 403
                    )
        sections_payload = update_fields.pop("sections", None)

        update_set: Dict[str, Any] = {}
        update_set.update(update_fields)
        update_set["updated_at"] = datetime.utcnow()
        update_set["updated_by"] = getattr(current_user, "id", None)

        update_ops: Dict[str, Any] = {"$set": update_set}

        if sections_payload is not None:
            normalized_sections = self._normalize_sections(
                [
                    s.model_dump() if isinstance(s, TemplateSection) else dict(s)
                    for s in sections_payload
                ]
            )
            new_version = int(getattr(existing, "version", 1) or 1) + 1
            update_ops["$set"]["sections"] = normalized_sections
            update_ops["$set"]["version"] = new_version
            update_ops["$push"] = {
                "versions": TemplateVersion(
                    version=new_version,
                    updated_at=datetime.utcnow(),
                    updated_by=getattr(current_user, "id", None),
                    sections=[TemplateSection(**s) for s in normalized_sections],
                ).model_dump()
            }

        query_id = self._to_query_id(template_id)
        result = await templates.update_one({"_id": query_id, "is_active": True}, update_ops)
        if result.matched_count == 0:
            raise TemplateError("Template not found", 404)

        updated = await templates.find_one({"_id": query_id})
        return self._to_template(updated)

    async def delete_template(self, template_id: str, current_user: Any) -> None:
        templates = await self._get_handles()
        query_id = self._to_query_id(template_id)
        result = await templates.update_one(
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
            raise TemplateError("Template not found", 404)

    def _to_query_id(self, value: str):
        try:
            return ObjectId(value)
        except Exception:
            return value

    def _to_template(self, doc: Optional[Dict[str, Any]]) -> LetterTemplate:
        if not doc:
            raise TemplateError("Template payload missing", 500)
        payload = dict(doc)

        if payload.get("_id") is not None:
            payload["_id"] = str(payload.get("_id"))

        for fld in ("organization_id", "project_id", "created_by", "updated_by"):
            if payload.get(fld) is not None:
                try:
                    payload[fld] = str(payload[fld])
                except Exception:
                    pass

        sections = payload.get("sections") or []
        if isinstance(sections, list):
            normalized_sections = self._normalize_sections(
                [dict(s) if isinstance(s, dict) else s.model_dump() for s in sections]
            )
            payload["sections"] = normalized_sections

        visibility = payload.get("visibility")
        if not visibility:
            if payload.get("project_id"):
                payload["visibility"] = "project"
            elif payload.get("organization_id"):
                payload["visibility"] = "organization"
            else:
                payload["visibility"] = "global"

        payload["usage_count"] = int(payload.get("usage_count") or 0)

        return LetterTemplate(**payload)
