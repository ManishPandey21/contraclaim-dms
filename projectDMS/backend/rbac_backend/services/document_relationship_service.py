"""Authority-safe canonical Document relationships for application entities."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Awaitable, Callable, Iterable, Optional, TypeVar

from fastapi import HTTPException
from ..core.permissions import Permissions
from ..models.document_relationship import (
    DocumentRelationship,
    DocumentRelationshipInput,
    DocumentRelationshipView,
)
from .audit_event_service import AuditEventService
from .entity_adapter_registry import EntityAdapterRegistry, EntityContext
from .policy_service import PolicyService
from .publication_policy import is_consumable, resolve_canonical_document
from ..utils.error_handler import BaseDomainError


_TransactionResult = TypeVar("_TransactionResult")


class DocumentRelationshipError(BaseDomainError):
    def __init__(self, detail: str, status_code: int = 400) -> None:
        super().__init__(detail, status_code, error="DocumentRelationshipError")
        self.detail = detail
        self.status_code = status_code


class DocumentRelationshipService:
    """Owns validation, persistence, authority filtering, and audit for links."""

    def __init__(
        self,
        db: Any,
        *,
        policy: Optional[PolicyService] = None,
        audit: Optional[AuditEventService] = None,
        registry: Optional[EntityAdapterRegistry] = None,
    ) -> None:
        self.db = db
        self.policy = policy or PolicyService(db=db)
        self.audit = audit or AuditEventService(db)
        self.registry = registry or EntityAdapterRegistry()

    @asynccontextmanager
    async def _transaction(self):
        client = getattr(self.db, "client", None)
        start_session = getattr(client, "start_session", None)
        if not callable(start_session):
            yield None
            return
        session = await start_session()
        async with session:
            async with session.start_transaction():
                yield session

    async def _run_transaction(
        self,
        callback: Callable[[Any], Awaitable[_TransactionResult]],
    ) -> _TransactionResult:
        client = getattr(self.db, "client", None)
        start_session = getattr(client, "start_session", None)
        if not callable(start_session):
            return await callback(None)
        session = await start_session()
        async with session:
            with_transaction = getattr(session, "with_transaction", None)
            if callable(with_transaction):
                return await with_transaction(callback)
            async with session.start_transaction():
                return await callback(session)

    async def _target(
        self,
        actor: Any,
        target_type: str,
        target_id: str,
        *,
        manage: bool,
        freeze: bool = False,
    ) -> EntityContext:
        try:
            adapter = self.registry.get(target_type)
        except KeyError as exc:
            raise DocumentRelationshipError("Unsupported relationship target", 404) from exc
        context = await adapter.load(self.db, target_id)
        if context is None:
            raise DocumentRelationshipError("Relationship target not found", 404)
        if not context.organization_id or not context.project_id:
            raise DocumentRelationshipError(
                "Relationship target requires explicit organization and project scope", 409
            )
        permission = (
            context.freeze_permission or context.manage_permission
            if freeze
            else context.manage_permission if manage else context.view_permission
        )
        await self.policy.authorize_document(actor, permission, context.entity, resource_type=context.target_type)
        return context

    async def _document(
        self,
        actor: Any,
        context: EntityContext,
        document_id: str,
        *,
        fail_closed: bool = True,
        require_consumable: bool = True,
        session: Any = None,
    ) -> Optional[dict[str, Any]]:
        try:
            document = await resolve_canonical_document(
                self.db,
                document_id,
                session=session,
            )
            if not document:
                raise DocumentRelationshipError("Document not found", 404)
            if require_consumable and not is_consumable(document):
                raise DocumentRelationshipError("Document is not currently consumable", 409)
            organization_id = str(document.get("organization_id") or document.get("organizationId") or "")
            project_id = str(document.get("project_id") or document.get("projectId") or "")
            if not project_id:
                raise DocumentRelationshipError("Project-level relationships cannot use project-null Documents", 409)
            if organization_id != context.organization_id or project_id != context.project_id:
                raise DocumentRelationshipError("Document scope does not match relationship target", 403)
            await self.policy.authorize_document(
                actor,
                Permissions.DOCUMENT_VIEW,
                document,
                resource_type="document",
            )
            return document
        except (DocumentRelationshipError, HTTPException):
            if fail_closed:
                raise
            return None

    async def _guard_document_relationship_write(
        self,
        actor: Any,
        context: EntityContext,
        document_id: str,
        *,
        session: Any,
    ) -> dict[str, Any]:
        document = await self._document(
            actor,
            context,
            document_id,
            session=session,
        )
        result = await self.db.documents.update_one(
            {"_id": document.get("_id")},
            {"$inc": {"document_relationship_revision": 1}},
            session=session,
        )
        if not getattr(result, "matched_count", 0):
            raise DocumentRelationshipError(
                "Document changed during relationship creation", 409
            )
        return document

    async def _resolved_document_for_link(
        self,
        actor: Any,
        context: EntityContext,
        stored: dict[str, Any],
        *,
        fail_closed: bool = False,
        require_consumable: bool = True,
    ) -> Optional[dict[str, Any]]:
        document = await self._document(
            actor,
            context,
            str(stored.get("document_id") or ""),
            fail_closed=fail_closed,
            require_consumable=require_consumable,
        )
        if document is None:
            return None
        version_id = str(stored.get("document_version_id") or "")
        if not version_id:
            return document
        version = await self.db.document_versions.find_one(
            {"_id": version_id, "document_id": str(stored.get("document_id") or "")}
        )
        if not version or not version.get("file_object_id"):
            if fail_closed:
                raise DocumentRelationshipError("Frozen Document version is unavailable", 409)
            return None
        return {
            **document,
            "file_object_id": version.get("file_object_id"),
            "resolved_version_id": version_id,
            "resolved_version_number": version.get("version_number"),
        }

    @staticmethod
    def _active_identity(context: EntityContext, link: DocumentRelationshipInput) -> dict[str, Any]:
        return {
            "organization_id": context.organization_id,
            "project_id": context.project_id,
            "target_type": context.target_type,
            "target_id": context.target_id,
            "document_id": str(link.document_id),
            "relationship_role": link.relationship_role,
            "removed_at": None,
        }

    async def link_batch(
        self,
        actor: Any,
        target_type: str,
        target_id: str,
        links: Iterable[DocumentRelationshipInput],
        *,
        idempotency_key: Optional[str] = None,
        source: str = "user",
        source_metadata: Optional[dict[str, Any]] = None,
    ) -> list[DocumentRelationshipView]:
        context = await self._target(actor, target_type, target_id, manage=True)
        if context.frozen:
            raise DocumentRelationshipError("Evidence relationships are frozen", 409)
        prepared: list[tuple[DocumentRelationshipInput, dict[str, Any], DocumentRelationship, dict[str, Any]]] = []
        for link in links:
            if link.relationship_role not in context.allowed_roles:
                raise DocumentRelationshipError("Relationship role is not valid for this target", 422)
            document = await self._document(actor, context, link.document_id)
            version_id = link.document_version_id
            if version_id:
                version = await self.db.document_versions.find_one(
                    {"_id": version_id, "document_id": str(link.document_id)}
                )
                if not version or not version.get("file_object_id"):
                    raise DocumentRelationshipError("Document version does not belong to Document", 409)
            relationship = DocumentRelationship(
                **self._active_identity(context, link),
                parent_type=context.parent_type,
                parent_id=context.parent_id,
                document_version_id=version_id,
                description=link.description,
                source=source,
                created_by=getattr(actor, "id", None),
                authority_snapshot={
                    "document_processing_status": document.get("processing_status") if document else None,
                    "document_lifecycle_state": document.get("lifecycle_state") if document else None,
                },
                metadata={
                    **dict(source_metadata or {}),
                    **({"idempotency_key": idempotency_key} if idempotency_key else {}),
                },
            )
            prepared.append((link, document, relationship, relationship.model_dump(by_alias=True)))

        async def persist(session: Any) -> list[DocumentRelationshipView]:
            adapter = self.registry.get(context.target_type)
            if not await adapter.guard_relationship_write(
                self.db,
                context,
                session=session,
            ):
                raise DocumentRelationshipError(
                    "Relationship target changed or evidence is frozen", 409
                )
            results: list[DocumentRelationshipView] = []
            for link, _, relationship, payload in prepared:
                document = await self._guard_document_relationship_write(
                    actor,
                    context,
                    link.document_id,
                    session=session,
                )
                identity = self._active_identity(context, link)
                result = await self.db.entity_document_links.update_one(
                    identity,
                    {"$setOnInsert": payload},
                    upsert=True,
                    session=session,
                )
                created = getattr(result, "upserted_id", None) is not None
                if created:
                    stored = payload
                    await self.audit.emit(
                        action="document_relationship.linked",
                        actor_id=getattr(actor, "id", None),
                        resource_type="entity_document_link",
                        resource_id=relationship.id,
                        organization_id=context.organization_id,
                        project_id=context.project_id,
                        after=payload,
                        correlation_id=idempotency_key,
                        session=session,
                    )
                else:
                    stored = await self.db.entity_document_links.find_one(
                        identity, session=session
                    )
                    if stored is None:
                        raise DocumentRelationshipError(
                            "Document relationship changed during link", 409
                        )
                results.append(
                    DocumentRelationshipView(
                        **stored,
                        document=document,
                        target_label=context.label,
                        target_route=context.route,
                    )
                )
            return results

        return await self._run_transaction(persist)

    async def link_target(self, link_id: str) -> Optional[tuple[str, str]]:
        """``(target_type, target_id)`` of a stored link, or ``None``. Read-only."""
        stored = await self.db.entity_document_links.find_one({"_id": link_id})
        if stored is None:
            return None
        return str(stored.get("target_type") or ""), str(stored.get("target_id") or "")

    async def _stored_link(self, link_id: str) -> dict[str, Any]:
        stored = await self.db.entity_document_links.find_one({"_id": link_id})
        if stored is None:
            raise DocumentRelationshipError("Document relationship not found", 404)
        return stored

    async def remove(
        self,
        actor: Any,
        link_id: str,
        *,
        reason: str,
        expected_revision: int,
    ) -> DocumentRelationshipView:
        stored = await self._stored_link(link_id)
        context = await self._target(
            actor,
            str(stored.get("target_type") or ""),
            str(stored.get("target_id") or ""),
            manage=True,
        )
        if context.frozen:
            raise DocumentRelationshipError("Evidence relationships are frozen", 409)
        if stored.get("removed_at") is not None:
            raise DocumentRelationshipError("Document relationship is already removed", 409)
        if int(stored.get("_revision") or 1) != int(expected_revision):
            raise DocumentRelationshipError("Document relationship was modified", 409)
        document = await self._document(
            actor,
            context,
            str(stored.get("document_id") or ""),
            require_consumable=False,
        )
        now = datetime.utcnow()
        update = {
            "removed_at": now,
            "removed_by": getattr(actor, "id", None),
            "removal_reason": reason,
        }
        updated = {
            **stored,
            **update,
            "_revision": int(stored.get("_revision") or 1) + 1,
        }
        async with self._transaction() as session:
            result = await self.db.entity_document_links.update_one(
                {"_id": link_id, "removed_at": None, "_revision": expected_revision},
                {"$set": update, "$inc": {"_revision": 1}},
                session=session,
            )
            if not getattr(result, "matched_count", 0):
                raise DocumentRelationshipError("Document relationship was modified", 409)
            await self.audit.emit(
                action="document_relationship.unlinked",
                actor_id=getattr(actor, "id", None),
                resource_type="entity_document_link",
                resource_id=link_id,
                organization_id=context.organization_id,
                project_id=context.project_id,
                before=stored,
                after=updated,
                reason=reason,
                session=session,
            )
        return DocumentRelationshipView(
            **updated,
            document=document,
            target_label=context.label,
            target_route=context.route,
        )

    async def freeze(
        self,
        actor: Any,
        target_type: str,
        target_id: str,
        *,
        reason: str,
        lifecycle_orchestrated: bool = False,
        allow_already_frozen: bool = False,
    ) -> list[DocumentRelationshipView]:
        context = await self._target(
            actor, target_type, target_id, manage=True, freeze=True
        )
        adapter = self.registry.get(context.target_type)
        if (
            adapter.freeze_requires_lifecycle_orchestration
            and not lifecycle_orchestrated
        ):
            raise DocumentRelationshipError(
                f"{context.target_type} evidence must be frozen through its lifecycle action",
                409,
            )
        if context.frozen:
            if allow_already_frozen:
                return await self.list_for_target(actor, target_type, target_id)
            raise DocumentRelationshipError("Evidence relationships are already frozen", 409)
        if not adapter.supports_freeze:
            raise DocumentRelationshipError(
                f"{context.target_type} evidence freeze is not supported",
                409,
            )

        legacy_ids = list(context.entity.get("linked_document_ids") or [])
        if legacy_ids:
            if context.target_type != "claim":
                raise DocumentRelationshipError(
                    "Legacy relationship intent is ambiguous and requires manual review",
                    409,
                )
            await self.link_batch(
                actor,
                context.target_type,
                context.target_id,
                [
                    DocumentRelationshipInput(
                        document_id=str(document_id),
                        relationship_role="supporting_document",
                    )
                    for document_id in legacy_ids
                ],
                source="legacy_compatibility",
            )
            context = await self._target(actor, target_type, target_id, manage=True)

        async def prepare_links(
            stored_links: Iterable[dict[str, Any]],
            *,
            session: Any = None,
        ) -> list[tuple[dict[str, Any], dict[str, Any], str]]:
            prepared: list[tuple[dict[str, Any], dict[str, Any], str]] = []
            for stored in stored_links:
                document = await self._document(
                    actor,
                    context,
                    str(stored.get("document_id") or ""),
                    session=session,
                )
                version_id = str((document or {}).get("current_version_id") or "")
                if not version_id:
                    raise DocumentRelationshipError(
                        "Every frozen relationship requires a current Document version",
                        409,
                    )
                version = await self.db.document_versions.find_one(
                    {
                        "_id": version_id,
                        "document_id": str(stored.get("document_id") or ""),
                    },
                    session=session,
                )
                if not version:
                    raise DocumentRelationshipError(
                        "Current Document version is missing or does not belong to Document",
                        409,
                    )
                if not version.get("file_object_id"):
                    raise DocumentRelationshipError(
                        "Current Document version has no immutable file artifact",
                        409,
                    )
                prepared.append((stored, document or {}, version_id))
            return prepared

        preflight_links = await self.db.entity_document_links.find(
            {
                "organization_id": context.organization_id,
                "project_id": context.project_id,
                "target_type": context.target_type,
                "target_id": context.target_id,
                "removed_at": None,
            }
        ).to_list(length=None)
        await prepare_links(preflight_links)

        now = datetime.utcnow()
        actor_id = getattr(actor, "id", None)

        async def persist(session: Any) -> list[DocumentRelationshipView]:
            await adapter.freeze(
                self.db,
                context,
                actor_id=actor_id,
                frozen_at=now,
                reason=reason,
                session=session,
            )
            stored_links = await self.db.entity_document_links.find(
                {
                    "organization_id": context.organization_id,
                    "project_id": context.project_id,
                    "target_type": context.target_type,
                    "target_id": context.target_id,
                    "removed_at": None,
                },
                session=session,
            ).sort("created_at", 1).to_list(length=None)

            prepared = await prepare_links(stored_links, session=session)

            for stored, _, version_id in prepared:
                result = await self.db.entity_document_links.update_one(
                    {
                        "_id": stored.get("_id"),
                        "removed_at": None,
                        "_revision": int(stored.get("_revision") or 1),
                    },
                    {
                        "$set": {
                            "document_version_id": version_id,
                            "frozen_at": now,
                            "frozen_by": actor_id,
                        },
                        "$inc": {"_revision": 1},
                    },
                    session=session,
                )
                if not getattr(result, "matched_count", 0):
                    raise DocumentRelationshipError(
                        "Document relationship changed during freeze", 409
                    )
            await self.audit.emit(
                action="document_relationships.frozen",
                actor_id=actor_id,
                resource_type=context.target_type,
                resource_id=context.target_id,
                organization_id=context.organization_id,
                project_id=context.project_id,
                before={"evidence_frozen_at": None},
                after={
                    "evidence_frozen_at": now,
                    "document_version_ids": [
                        version_id for _, _, version_id in prepared
                    ],
                },
                reason=reason,
                session=session,
            )
            return [
                DocumentRelationshipView(
                    **{
                        **stored,
                        "document_version_id": version_id,
                        "frozen_at": now,
                        "frozen_by": actor_id,
                        "_revision": int(stored.get("_revision") or 1) + 1,
                    },
                    document=document,
                    target_label=context.label,
                    target_route=context.route,
                )
                for stored, document, version_id in prepared
            ]

        try:
            return await self._run_transaction(persist)
        except RuntimeError as exc:
            raise DocumentRelationshipError(str(exc), 409) from exc

    async def history(self, actor: Any, link_id: str) -> list[DocumentRelationshipView]:
        stored = await self._stored_link(link_id)
        context = await self._target(
            actor,
            str(stored.get("target_type") or ""),
            str(stored.get("target_id") or ""),
            manage=False,
        )
        document = await self._resolved_document_for_link(
            actor,
            context,
            stored,
            fail_closed=True,
        )
        return [
            DocumentRelationshipView(
                **stored,
                document=document,
                target_label=context.label,
                target_route=context.route,
            )
        ]

    async def list_for_document(
        self,
        actor: Any,
        document_id: str,
    ) -> list[DocumentRelationshipView]:
        document = await resolve_canonical_document(self.db, document_id)
        if not document:
            raise DocumentRelationshipError("Document not found", 404)
        await self.policy.authorize_document(
            actor,
            Permissions.DOCUMENT_VIEW,
            document,
            resource_type="document",
        )
        if not is_consumable(document):
            return []
        organization_id = str(document.get("organization_id") or document.get("organizationId") or "")
        project_id = str(document.get("project_id") or document.get("projectId") or "")
        if not project_id:
            return []
        cursor = self.db.entity_document_links.find(
            {
                "organization_id": organization_id,
                "project_id": project_id,
                "document_id": str(document.get("_id") or document_id),
                "removed_at": None,
            }
        ).sort("created_at", 1)
        results: list[DocumentRelationshipView] = []
        async for stored in cursor:
            try:
                context = await self._target(
                    actor,
                    str(stored.get("target_type") or ""),
                    str(stored.get("target_id") or ""),
                    manage=False,
                )
            except (DocumentRelationshipError, HTTPException):
                continue
            if context.organization_id != organization_id or context.project_id != project_id:
                continue
            results.append(
                DocumentRelationshipView(
                    **stored,
                    document=document,
                    target_label=context.label,
                    target_route=context.route,
                )
            )
        canonical_targets = {
            (str(link.target_type), str(link.target_id)) for link in results
        }
        for adapter in self.registry.adapters():
            legacy_target_ids = await adapter.legacy_targets_for_document(
                self.db,
                document_id=str(document.get("_id") or document_id),
                organization_id=organization_id,
                project_id=project_id,
            )
            for legacy_target_id in legacy_target_ids:
                if (adapter.target_type, legacy_target_id) in canonical_targets:
                    continue
                try:
                    context = await self._target(
                        actor, adapter.target_type, legacy_target_id, manage=False
                    )
                except (DocumentRelationshipError, HTTPException):
                    continue
                results.append(
                    DocumentRelationshipView(
                        _id=f"legacy:{adapter.target_type}:{legacy_target_id}:{document_id}",
                        organization_id=organization_id,
                        project_id=project_id,
                        target_type=adapter.target_type,
                        target_id=legacy_target_id,
                        document_id=str(document.get("_id") or document_id),
                        relationship_role=adapter.legacy_relationship_role,
                        source="legacy_read_through",
                        document=document,
                        target_label=context.label,
                        target_route=context.route,
                    )
                )
        return results

    async def list_for_target(
        self,
        actor: Any,
        target_type: str,
        target_id: str,
    ) -> list[DocumentRelationshipView]:
        context = await self._target(actor, target_type, target_id, manage=False)
        cursor = self.db.entity_document_links.find(
            {
                "organization_id": context.organization_id,
                "project_id": context.project_id,
                "target_type": context.target_type,
                "target_id": context.target_id,
                "removed_at": None,
            }
        ).sort("created_at", 1)
        results: list[DocumentRelationshipView] = []
        async for stored in cursor:
            document = await self._resolved_document_for_link(
                actor, context, stored, fail_closed=False
            )
            if document is None:
                continue
            results.append(
                DocumentRelationshipView(
                    **stored,
                    document=document,
                    target_label=context.label,
                    target_route=context.route,
                )
            )
        canonical_document_ids = {str(link.document_id) for link in results}
        adapter = self.registry.get(context.target_type)
        for legacy_id in context.entity.get("linked_document_ids") or []:
            candidate = str(legacy_id or "")
            if not candidate or candidate in canonical_document_ids:
                continue
            document = await self._document(
                actor,
                context,
                candidate,
                fail_closed=False,
            )
            if document is None:
                continue
            canonical_id = str(document.get("_id") or candidate)
            if canonical_id in canonical_document_ids:
                continue
            results.append(
                DocumentRelationshipView(
                    _id=f"legacy:{context.target_type}:{context.target_id}:{canonical_id}",
                    organization_id=context.organization_id,
                    project_id=context.project_id,
                    target_type=context.target_type,
                    target_id=context.target_id,
                    document_id=canonical_id,
                    relationship_role=adapter.legacy_relationship_role,
                    source="legacy_read_through",
                    document=document,
                    target_label=context.label,
                    target_route=context.route,
                )
            )
            canonical_document_ids.add(canonical_id)
        return results

    async def delete_target(
        self,
        actor: Any,
        target_type: str,
        target_id: str,
        *,
        reason: str,
    ) -> None:
        context = await self._target(actor, target_type, target_id, manage=False)
        await self.policy.authorize_document(
            actor,
            context.delete_permission,
            context.entity,
            resource_type=context.target_type,
        )
        if context.frozen:
            raise DocumentRelationshipError("Frozen evidence target cannot be deleted", 409)
        adapter = self.registry.get(context.target_type)
        now = datetime.utcnow()
        actor_id = getattr(actor, "id", None)

        async def persist(session: Any) -> None:
            if not await adapter.delete(self.db, context, session=session):
                raise DocumentRelationshipError(
                    "Relationship target changed or evidence is frozen", 409
                )
            links = await self.db.entity_document_links.find(
                {
                    "organization_id": context.organization_id,
                    "project_id": context.project_id,
                    "target_type": context.target_type,
                    "target_id": context.target_id,
                    "removed_at": None,
                },
                session=session,
            ).to_list(length=None)
            for stored in links:
                updated = {
                    **stored,
                    "removed_at": now,
                    "removed_by": actor_id,
                    "removal_reason": reason,
                    "_revision": int(stored.get("_revision") or 1) + 1,
                }
                result = await self.db.entity_document_links.update_one(
                    {
                        "_id": stored.get("_id"),
                        "removed_at": None,
                        "_revision": int(stored.get("_revision") or 1),
                    },
                    {
                        "$set": {
                            "removed_at": now,
                            "removed_by": actor_id,
                            "removal_reason": reason,
                        },
                        "$inc": {"_revision": 1},
                    },
                    session=session,
                )
                if not getattr(result, "matched_count", 0):
                    raise DocumentRelationshipError(
                        "Document relationship changed during target deletion", 409
                    )
                await self.audit.emit(
                    action="document_relationship.unlinked",
                    actor_id=actor_id,
                    resource_type="entity_document_link",
                    resource_id=str(stored.get("_id") or ""),
                    organization_id=context.organization_id,
                    project_id=context.project_id,
                    before=stored,
                    after=updated,
                    reason=reason,
                    session=session,
                )
            await self.audit.emit(
                action=f"{context.target_type}.deleted",
                actor_id=actor_id,
                resource_type=context.target_type,
                resource_id=context.target_id,
                organization_id=context.organization_id,
                project_id=context.project_id,
                before=context.entity,
                after=None,
                reason=reason,
                session=session,
            )

        await self._run_transaction(persist)

    async def authorized_document_ids(
        self,
        actor: Any,
        target_type: str,
        target_id: str,
        *,
        legacy_document_ids: Iterable[str] = (),
    ) -> list[str]:
        context = await self._target(actor, target_type, target_id, manage=False)
        active = await self.list_for_target(actor, target_type, target_id)
        result = [str(link.document_id) for link in active]
        seen = set(result)
        for legacy_id in legacy_document_ids:
            candidate = str(legacy_id or "")
            if not candidate or candidate in seen:
                continue
            document = await self._document(
                actor, context, candidate, fail_closed=False
            )
            if document is None:
                continue
            canonical_id = str(document.get("_id") or candidate)
            if canonical_id not in seen:
                result.append(canonical_id)
                seen.add(canonical_id)
        return result

    async def authorized_document_sources(
        self,
        actor: Any,
        target_type: str,
        target_id: str,
        *,
        legacy_document_ids: Iterable[str] = (),
    ) -> list[dict[str, Any]]:
        context = await self._target(actor, target_type, target_id, manage=False)
        links = await self.list_for_target(actor, target_type, target_id)
        sources: list[dict[str, Any]] = []
        seen: set[str] = set()
        for link in links:
            document_id = str(link.document_id)
            document = link.document or {}
            sources.append(
                {
                    "document_id": document_id,
                    "document_version_id": link.document_version_id,
                    "file_object_id": document.get("file_object_id"),
                }
            )
            seen.add(document_id)
        for legacy_id in legacy_document_ids:
            candidate = str(legacy_id or "")
            if not candidate or candidate in seen:
                continue
            document = await self._document(actor, context, candidate, fail_closed=False)
            if document is None:
                continue
            canonical_id = str(document.get("_id") or candidate)
            sources.append(
                {
                    "document_id": canonical_id,
                    "document_version_id": document.get("current_version_id"),
                    "file_object_id": document.get("file_object_id"),
                }
            )
            seen.add(canonical_id)
        return sources

    async def authorized_document_ids_for_claims(
        self,
        actor: Any,
        claims: Iterable[dict[str, Any]],
    ) -> dict[str, list[str]]:
        return await self.authorized_document_ids_for_targets(actor, "claim", claims)

    async def authorized_document_ids_for_targets(
        self,
        actor: Any,
        target_type: str,
        targets: Iterable[dict[str, Any]],
    ) -> dict[str, list[str]]:
        target_rows = list(targets)
        adapter = self.registry.get(target_type)
        contexts = {
            str(target.get("_id") or ""): adapter.context_from_entity(target)
            for target in target_rows
            if target.get("_id")
        }
        if not contexts:
            return {}
        cursor = self.db.entity_document_links.find(
            {
                "target_type": adapter.target_type,
                "target_id": {"$in": list(contexts)},
                "removed_at": None,
            }
        )
        stored_by_target: dict[str, list[dict[str, Any]]] = {
            target_id: [] for target_id in contexts
        }
        async for stored in cursor:
            target_id = str(stored.get("target_id") or "")
            context = contexts.get(target_id)
            if (
                context is not None
                and stored.get("organization_id") == context.organization_id
                and stored.get("project_id") == context.project_id
            ):
                stored_by_target[target_id].append(stored)

        results: dict[str, list[str]] = {}
        for target in target_rows:
            target_id = str(target.get("_id") or "")
            context = contexts.get(target_id)
            if context is None:
                continue
            try:
                await self.policy.authorize_document(
                    actor,
                    context.view_permission,
                    context.entity,
                    resource_type=context.target_type,
                )
            except HTTPException:
                results[target_id] = []
                continue
            seen: set[str] = set()
            authorized: list[str] = []
            for stored in stored_by_target[target_id]:
                document = await self._resolved_document_for_link(
                    actor, context, stored, fail_closed=False
                )
                if document is None:
                    continue
                document_id = str(document.get("_id") or stored.get("document_id") or "")
                if document_id and document_id not in seen:
                    authorized.append(document_id)
                    seen.add(document_id)
            for legacy_id in target.get("linked_document_ids") or []:
                candidate = str(legacy_id or "")
                if not candidate or candidate in seen:
                    continue
                document = await self._document(
                    actor, context, candidate, fail_closed=False
                )
                if document is None:
                    continue
                document_id = str(document.get("_id") or candidate)
                authorized.append(document_id)
                seen.add(document_id)
            results[target_id] = authorized
        return results

    async def reject_ambiguous_legacy_write(
        self,
        actor: Any,
        target_type: str,
        *,
        organization_id: Optional[str],
        project_id: Optional[str],
    ) -> None:
        """Fail closed before a legacy create can invent role/event meaning.

        The caller must complete the native create authorization first.  No
        Document is resolved because an IPC legacy array contains neither a
        relationship role nor a stable child-event identity to validate.
        """
        if str(target_type or "").strip().lower() != "claim":
            raise DocumentRelationshipError(
                "Legacy relationship intent is ambiguous and requires manual review",
                409,
            )

    async def replace_legacy_document_ids(
        self,
        actor: Any,
        target_type: str,
        target_id: str,
        document_ids: Iterable[str],
    ) -> list[DocumentRelationshipView]:
        context = await self._target(actor, target_type, target_id, manage=True)
        if context.frozen:
            raise DocumentRelationshipError("Evidence relationships are frozen", 409)
        if context.target_type != "claim":
            raise DocumentRelationshipError(
                "Legacy relationship intent is ambiguous and requires manual review", 409
            )
        desired = list(dict.fromkeys(str(item) for item in document_ids if str(item)))
        if desired:
            await self.link_batch(
                actor,
                context.target_type,
                context.target_id,
                [
                    DocumentRelationshipInput(
                        document_id=document_id,
                        relationship_role="supporting_document",
                    )
                    for document_id in desired
                ],
                source="legacy_compatibility",
            )
        cursor = self.db.entity_document_links.find(
            {
                "organization_id": context.organization_id,
                "project_id": context.project_id,
                "target_type": context.target_type,
                "target_id": context.target_id,
                "relationship_role": "supporting_document",
                "source": "legacy_compatibility",
                "removed_at": None,
            }
        )
        async for stored in cursor:
            if str(stored.get("document_id") or "") not in desired:
                await self.remove(
                    actor,
                    str(stored.get("_id") or ""),
                    reason="Legacy linked_document_ids replacement",
                    expected_revision=int(stored.get("_revision") or 1),
                )
        return await self.list_for_target(actor, target_type, target_id)
