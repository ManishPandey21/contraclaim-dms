from __future__ import annotations

import logging
from typing import Optional, Tuple, List, Dict, Any

from pymongo.database import Database

from ..core.database import get_database
from ..models.storage_settings import (
    OrganizationStorageSettings,
    ProjectStorageSettings,
    ResolvedStorageSettings,
    StorageProviderConfig,
    StorageBasePaths,
)

logger = logging.getLogger(__name__)


class StorageSettingsService:
    """Persist and resolve storage settings for organizations and projects."""

    def __init__(self, db: Optional[Database] = None) -> None:
        self.db = db

    async def _get_db(self) -> Database:
        if self.db is not None:
            return self.db
        return await get_database()

    async def get_org_settings(self, org_id: str) -> Optional[OrganizationStorageSettings]:
        db = await self._get_db()
        doc = await db.storage_settings.find_one({"type": "org", "org_id": org_id})
        if not doc:
            return None
        # Convert ObjectId to string before passing to Pydantic
        if "_id" in doc:
            doc["_id"] = str(doc["_id"])
        return OrganizationStorageSettings(**doc)

    async def upsert_org_settings(self, org_id: str, payload: OrganizationStorageSettings) -> OrganizationStorageSettings:
        db = await self._get_db()
        data = payload.model_dump(by_alias=True, exclude_none=True)
        data.update({"type": "org", "org_id": org_id})
        await db.storage_settings.update_one(
            {"type": "org", "org_id": org_id},
            {"$set": data},
            upsert=True,
        )
        logger.info("Upserted org storage settings for %s", org_id)
        saved = await db.storage_settings.find_one({"type": "org", "org_id": org_id})
        # Convert ObjectId to string before passing to Pydantic
        if saved and "_id" in saved:
            saved["_id"] = str(saved["_id"])
        return OrganizationStorageSettings(**saved)

    async def get_project_settings(self, project_id: str) -> Optional[ProjectStorageSettings]:
        db = await self._get_db()
        doc = await db.storage_settings.find_one({"type": "project", "project_id": project_id})
        if not doc:
            return None
        # Convert ObjectId to string before passing to Pydantic
        if "_id" in doc:
            doc["_id"] = str(doc["_id"])
        return ProjectStorageSettings(**doc)

    async def upsert_project_settings(
        self,
        project_id: str,
        org_id: str,
        payload: ProjectStorageSettings,
    ) -> ProjectStorageSettings:
        db = await self._get_db()
        data = payload.model_dump(by_alias=True, exclude_none=True)
        data.update({"type": "project", "project_id": project_id, "org_id": org_id})
        await db.storage_settings.update_one(
            {"type": "project", "project_id": project_id},
            {"$set": data},
            upsert=True,
        )
        logger.info("Upserted project storage settings for %s", project_id)
        saved = await db.storage_settings.find_one({"type": "project", "project_id": project_id})
        # Convert ObjectId to string before passing to Pydantic
        if saved and "_id" in saved:
            saved["_id"] = str(saved["_id"])
        return ProjectStorageSettings(**saved)

    def _choose_primary(self, providers: List[StorageProviderConfig]) -> List[StorageProviderConfig]:
        """Ensure exactly one primary. If none flagged, pick the first enabled."""
        enabled = [p for p in providers if p.enabled]
        if not enabled:
            return providers
        if not any(p.primary for p in enabled):
            enabled[0].primary = True
        else:
            # ensure single primary
            found = False
            for p in enabled:
                if p.primary:
                    if found:
                        p.primary = False
                    found = True
        return providers

    def _normalize_paths(
        self,
        base_paths: StorageBasePaths,
        org_short: Optional[str],
        project_short: Optional[str],
    ) -> StorageBasePaths:
        org = (org_short or "ORG").strip() or "ORG"
        proj = (project_short or "PROJ").strip() or "PROJ"
        def _fill(template: str) -> str:
            return template.replace("ORG", org).replace("PROJ", proj)

        return StorageBasePaths(
            incoming=_fill(base_paths.incoming),
            outgoing=_fill(base_paths.outgoing),
            contracts=_fill(base_paths.contracts),
        )

    async def resolve_settings(
        self,
        org_id: str,
        project_id: Optional[str] = None,
    ) -> ResolvedStorageSettings:
        """
        Resolve effective settings with inheritance:
        - Start with org settings (if any)
        - Apply project overrides when inherit_from_org is False
        """
        org_settings = await self.get_org_settings(org_id)
        project_settings: Optional[ProjectStorageSettings] = None
        if project_id:
            project_settings = await self.get_project_settings(project_id)

        # Defaults
        org_short = org_settings.org_short_name if org_settings else None
        project_short = project_settings.project_short_name if project_settings and project_settings.project_short_name else None

        # Providers
        providers: List[StorageProviderConfig] = []
        if project_settings and project_settings.providers and project_settings.inherit_from_org is False:
            providers = [StorageProviderConfig(**p.model_dump()) if isinstance(p, StorageProviderConfig) else StorageProviderConfig(**p) for p in project_settings.providers]
        elif org_settings and org_settings.providers:
            providers = [StorageProviderConfig(**p.model_dump()) if isinstance(p, StorageProviderConfig) else StorageProviderConfig(**p) for p in org_settings.providers]
        # Fallback default local-only provider
        if not providers:
            providers = [StorageProviderConfig(id="local", enabled=True, primary=True)]
        providers = self._choose_primary(providers)

        # Base paths
        base_paths = StorageBasePaths()
        if project_settings and project_settings.base_paths and project_settings.inherit_from_org is False:
            base_paths = project_settings.base_paths
        elif org_settings and org_settings.base_paths:
            base_paths = org_settings.base_paths

        normalized_paths = self._normalize_paths(base_paths, org_short, project_short)

        return ResolvedStorageSettings(
            org_id=org_id,
            project_id=project_id,
            org_short_name=org_short,
            project_short_name=project_short,
            providers=providers,
            base_paths=normalized_paths,
        )
