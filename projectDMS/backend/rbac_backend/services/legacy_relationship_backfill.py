"""Unified legacy document-relationship backfill control plane.

Every register stored evidence as a flat `linked_document_ids` array before the
canonical `entity_document_links` model existed. This service converts those
rows into canonical relationships — but only the unambiguous ones.

The rule this module exists to enforce:

    Legacy relationship membership is DISCOVERY INPUT, never AUTHORITY.

A candidate is only written after the module's own classifier has re-resolved it
against the current canonical target, the current canonical Document, and the
current authority rules. This module owns orchestration only — dry-run, scope,
selection, idempotency, provenance, audit, and the call into
``DocumentRelationshipService``. It owns no semantics: which target an id
belongs to, and which roles are legal there, stay with the module classifier and
the entity adapter. There is deliberately no `if module == ...` branch here.
"""

from __future__ import annotations

import logging

from dataclasses import dataclass
from enum import Enum
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Optional
from uuid import uuid4

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

from ..models.document_relationship import DocumentRelationshipInput
from .audit_event_service import AuditEventService
from .document_relationship_service import (
    DocumentRelationshipError,
    DocumentRelationshipService,
)
from .entity_adapter_registry import EntityAdapterRegistry


# A candidate may carry these findings and still be backfillable: the authority
# chain is clean and only the ROLE is unknown, which the operator supplies.
# Anything else — ambiguous events, cross-scope, blocked or missing Documents,
# frozen or orphaned targets — is refused. Fail closed by allow-list, never by
# deny-list: a classifier that grows a new adverse finding must not silently
# become auto-migratable.
BACKFILLABLE_FINDINGS = frozenset({"valid", "ambiguous_role", "manual_review"})

MAX_BACKFILL_BATCH = 50

logger = logging.getLogger(__name__)

#: One legacy candidate is ONE migration decision. The canonical relationship
#: identity includes `relationship_role`, so two operators adjudicating the same
#: candidate differently would each write a row the unique index considers
#: distinct — two migrations from one flat legacy membership. Ownership of the
#: decision is therefore claimed atomically under its own identity, which is
#: deliberately role-free.
BACKFILL_CLAIMS_COLLECTION = "legacy_backfill_claims"

#: A crashed adjudication must not lock a candidate forever. Ownership is a
#: lease, reclaimable once it expires — the same shape the Insurance
#: canonicalization claims already use.
BACKFILL_CLAIM_LEASE = timedelta(minutes=15)


class BackfillCapability(Enum):
    """What an operator may DO with a registered module.

    WRITE_BACKFILL: representative repository-defined legacy data can produce a
    genuinely migratable candidate, so `apply` may write canonical relationships.

    INVENTORY_ONLY: legacy evidence is discoverable/classifiable/reconcilable,
    but ordinary automatic backfill execution is refused. Used where exact
    ownership cannot be proven from repository-defined data (e.g. Bank
    Guarantee parent evidence carries no event provenance). It must be
    impossible to mistake "inventory exists" for "backfill is available".
    """

    WRITE_BACKFILL = "write_backfill"
    INVENTORY_ONLY = "inventory_only"


@dataclass(frozen=True)
class LegacyBackfillModule:
    """How one register exposes its legacy relationship candidates."""

    module: str
    classify: Callable[..., Any]
    source_kinds: frozenset[str]
    classifier_kwargs: Callable[[], dict[str, Any]] = dict
    capability: BackfillCapability = BackfillCapability.WRITE_BACKFILL


class LegacyBackfillError(Exception):
    """Raised when a backfill request is structurally unusable."""

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.detail = message
        self.status_code = status_code


class LegacyRelationshipBackfillService:
    def __init__(
        self,
        db: Any,
        *,
        policy: Any,
        registry: Optional[EntityAdapterRegistry] = None,
    ) -> None:
        self.db = db
        self.policy = policy
        self.registry = registry or EntityAdapterRegistry()
        self.audit = AuditEventService(db)

    # -- discovery ---------------------------------------------------------

    async def inventory(
        self,
        module: LegacyBackfillModule,
        *,
        org_id: Optional[str],
        project_id: Optional[str],
    ) -> dict[str, Any]:
        """Read-only. Classify legacy candidates within the operator's scope."""
        report = await module.classify(self.db, **module.classifier_kwargs())
        candidates: list[dict[str, Any]] = []
        for row in report.get("candidates", []):
            if str(row.get("source_kind") or "") not in module.source_kinds:
                continue
            # Scope comes from the canonical target, never from the legacy row.
            context = await self._target_context(row)
            if context is None:
                candidates.append({**row, "scope_status": "missing_target"})
                continue
            if not self._context_in_scope(context, org_id, project_id):
                continue
            candidates.append(
                {
                    **row,
                    "organization_id": context.organization_id,
                    "project_id": context.project_id,
                    "target_frozen": bool(getattr(context, "frozen", False)),
                }
            )
        counts: dict[str, int] = {}
        for row in candidates:
            key = str(row.get("classification") or "unknown")
            counts[key] = counts.get(key, 0) + 1
        return {
            "module": module.module,
            "org_id": org_id,
            "project_id": project_id,
            "candidate_count": len(candidates),
            "counts": counts,
            "candidates": candidates,
            "backfillable_findings": sorted(BACKFILLABLE_FINDINGS),
        }

    # -- execution ---------------------------------------------------------

    async def apply(
        self,
        actor: Any,
        module: LegacyBackfillModule,
        selections: Iterable[dict[str, Any]],
        *,
        org_id: Optional[str],
        project_id: Optional[str],
        dry_run: bool,
        run_id: str,
    ) -> list[dict[str, Any]]:
        if module.capability is not BackfillCapability.WRITE_BACKFILL:
            # An inventory-only module has no safe automatic migration path;
            # writing through it would fabricate the ownership its data lacks.
            # Refuse before any selection/claim/audit so the seam cannot be
            # mistaken for a writable one. Use the reconciliation inventory.
            raise LegacyBackfillError(
                f"Module '{module.module}' is inventory-only: legacy evidence "
                "must be reconciled manually, not auto-backfilled.",
                status_code=409,
            )
        chosen = list(selections or [])
        if not chosen:
            raise LegacyBackfillError(
                "Select at least one legacy relationship candidate to backfill."
            )
        if len(chosen) > MAX_BACKFILL_BATCH:
            raise LegacyBackfillError(
                f"Select at most {MAX_BACKFILL_BATCH} candidates per run."
            )

        report = await module.classify(self.db, **module.classifier_kwargs())
        # A legacy array may list the same document twice: the classifier emits
        # the first occurrence as `valid` and the rest as `duplicate`. Last-wins
        # would shadow the usable row with its own duplicate and make the link
        # permanently unbackfillable, so prefer the backfillable occurrence.
        by_key: dict[tuple[str, str], dict[str, Any]] = {}
        for row in report.get("candidates", []):
            if str(row.get("source_kind") or "") not in module.source_kinds:
                continue
            key = (str(row.get("target_id") or ""), str(row.get("document_id") or ""))
            existing = by_key.get(key)
            if existing is None:
                by_key[key] = row
                continue
            if "valid" in (row.get("findings") or []) and "valid" not in (
                existing.get("findings") or []
            ):
                by_key[key] = row

        results: list[dict[str, Any]] = []
        for selection in chosen:
            results.append(
                await self._apply_one(
                    actor,
                    module,
                    selection,
                    by_key,
                    org_id=org_id,
                    project_id=project_id,
                    dry_run=dry_run,
                    run_id=run_id,
                )
            )
        return results

    async def _apply_one(
        self,
        actor: Any,
        module: LegacyBackfillModule,
        selection: dict[str, Any],
        by_key: dict[tuple[str, str], dict[str, Any]],
        *,
        org_id: Optional[str],
        project_id: Optional[str],
        dry_run: bool,
        run_id: str,
    ) -> dict[str, Any]:
        target_id = str(selection.get("target_id") or "")
        document_id = str(selection.get("document_id") or "")
        role = str(selection.get("relationship_role") or "").strip()

        def outcome(status: str, **extra: Any) -> dict[str, Any]:
            return {
                "module": module.module,
                "target_id": target_id,
                "document_id": document_id,
                "status": status,
                **extra,
            }

        candidate = by_key.get((target_id, document_id))
        if candidate is None:
            return outcome("not_found", reason="No legacy candidate matches this selection")

        target_type = str(candidate.get("target_type") or "")
        classification = str(candidate.get("classification") or "unknown")
        findings = [str(value) for value in candidate.get("findings") or []]

        # Re-load the canonical target. A deleted, missing or foreign target
        # must never receive a new canonical relationship, whatever the legacy
        # row claimed about its own scope.
        context = await self._target_context(candidate)
        if context is None:
            return outcome(
                "missing_target",
                target_type=target_type,
                reason="Canonical target no longer resolves",
            )
        if not self._context_in_scope(context, org_id, project_id):
            return outcome(
                "out_of_scope",
                target_type=target_type,
                reason="Target is outside the requested scope",
            )
        if bool(getattr(context, "frozen", False)):
            return outcome(
                "frozen_target",
                target_type=target_type,
                reason="Evidence relationships are frozen for this target",
            )

        unusable = sorted(set(findings) - BACKFILLABLE_FINDINGS)
        if "valid" not in findings or unusable:
            return outcome(
                "requires_manual_review",
                target_type=target_type,
                classification=classification,
                findings=findings,
                blocking_findings=unusable,
            )

        # Role precedence: the operator's explicit choice, else a role the module
        # resolved deterministically from its own accepted semantics. Never a
        # guess made here — a module that cannot resolve one leaves it unset and
        # the candidate is refused. The entity adapter still decides whether the
        # resulting role is legal for this target.
        role_source = "operator" if role else "classifier"
        if not role:
            role = str(candidate.get("relationship_role") or "").strip()
        if not role:
            return outcome(
                "role_required",
                target_type=target_type,
                classification=classification,
                reason="A relationship role must be chosen for this candidate",
            )

        existing = await self._existing_canonical(target_type, target_id, document_id)
        if existing is not None:
            existing_role = str(existing.get("relationship_role") or "")
            if existing_role and existing_role != role:
                # One legacy candidate is one migration decision. A different
                # role now is a correction, which needs its own explicit
                # operation — not a second migration-derived relationship.
                return outcome(
                    "role_conflict",
                    target_type=target_type,
                    classification=classification,
                    relationship_role=role,
                    existing_role=existing_role,
                    reason=(
                        "This legacy candidate was already adjudicated as "
                        f"'{existing_role}'; changing it needs an explicit correction"
                    ),
                )
            return outcome(
                "already_canonical",
                target_type=target_type,
                classification=classification,
            )

        # A relationship someone deliberately removed is a canonical human
        # decision. Legacy membership must never silently overturn it.
        removed = await self._previously_removed(target_type, target_id, document_id)
        if removed is not None:
            return outcome(
                "previously_removed",
                target_type=target_type,
                classification=classification,
                reason=(
                    "This relationship was removed on purpose; re-linking needs a "
                    "human decision"
                ),
                removal_reason=removed.get("removal_reason"),
            )

        if dry_run:
            # A preview must never take durable ownership of a candidate, or a
            # later real run would find itself locked out.
            return outcome(
                "eligible",
                target_type=target_type,
                classification=classification,
                relationship_role=role,
            )

        claim_id = self._candidate_claim_id(module, candidate, target_type, target_id, document_id)
        owner_token = uuid4().hex
        owner = await self._claim_candidate(
            claim_id,
            actor=actor,
            role=role,
            run_id=run_id,
            owner_token=owner_token,
            target_type=target_type,
            target_id=target_id,
            document_id=document_id,
        )
        if owner is not None and str(owner.get("status") or "") != "complete":
            # A live lease held by another run: the outcome is not yet known, so
            # report something retryable rather than a success-shaped terminal
            # status for a relationship that may never exist.
            return outcome(
                "in_progress",
                target_type=target_type,
                classification=classification,
                relationship_role=role,
                reason="Another operator is adjudicating this candidate; retry shortly",
            )
        if owner is not None:
            # Someone else owns this migration decision.
            existing_role = str(owner.get("relationship_role") or "")
            if existing_role and existing_role != role:
                return outcome(
                    "role_conflict",
                    target_type=target_type,
                    classification=classification,
                    relationship_role=role,
                    existing_role=existing_role,
                    reason=(
                        "This legacy candidate was already adjudicated as "
                        f"'{existing_role}'; changing it needs an explicit correction"
                    ),
                )
            return outcome(
                "already_canonical",
                target_type=target_type,
                classification=classification,
            )

        confirmed = False
        # Any escape — a driver fault, a transaction abort, a cancellation —
        # must release the claim, or the candidate is locked out and a retry
        # would read the orphaned claim as a completed migration.
        try:
            try:
                links = await DocumentRelationshipService(
                    self.db, policy=self.policy
                ).link_batch(
                    actor,
                    target_type,
                    target_id,
                    [
                        DocumentRelationshipInput(
                            document_id=document_id,
                            relationship_role=role,
                        )
                    ],
                    source="migration",
                    idempotency_key=(
                        f"legacy-backfill:{module.module}:{target_type}"
                        f":{target_id}:{document_id}"
                    ),
                    source_metadata={
                        # The module names its own legacy field; a register may have
                        # more than one, with different semantics.
                        "legacy_field": str(
                            candidate.get("legacy_field") or "linked_document_ids"
                        ),
                        "legacy_source_kind": str(candidate.get("source_kind") or ""),
                        "legacy_target_id": target_id,
                        "legacy_document_id": document_id,
                        "classifier_result": classification,
                    # Event-bearing modules record HOW the event was resolved,
                    # so a later reader can tell a proven event from a guess.
                    **{
                        key: candidate[key]
                        for key in (
                            "event_resolution_source",
                            "legacy_history_id",
                            "legacy_revision_number",
                        )
                        if candidate.get(key) is not None
                    },
                    **(
                        {"legacy_parent_id": str(candidate.get("parent_id"))}
                        if candidate.get("parent_id")
                        else {}
                    ),
                        # Whether the role was adjudicated by a human or derived by
                        # the module. Provenance must never imply the classifier
                        # knew a role it could not determine.
                        "role_source": role_source,
                        "operator_id": getattr(actor, "id", None),
                        "migration_run_id": run_id,
                        "backfilled_at": datetime.now(timezone.utc).isoformat(),
                    },
                )
            except (DocumentRelationshipError, HTTPException) as exc:
                # `finally` releases the claim: no explicit release here, or the
                # same claim is deleted twice on every refusal.
                # One bad selection must not abort a batch that has already written
                # earlier items, nor escape as a 500. Refusals stay per-item —
                # including an authorization refusal raised as HTTPException, which
                # would otherwise abandon the batch with no results body.
                return outcome(
                    "rejected",
                    target_type=target_type,
                    classification=classification,
                    relationship_role=role,
                    reason=getattr(exc, "detail", str(exc)),
                )

            # M1: under concurrency both operators can pass the already-canonical
            # check. The link upsert is idempotent, so the loser gets the winner's
            # row back — recording "backfilled" and auditing it would report a write
            # that never happened.
            if not links:
                return outcome(
                    "rejected",
                    target_type=target_type,
                    classification=classification,
                    reason="The relationship service returned no link",
                )
            stored_run_id = str(
                (getattr(links[0], "metadata", None) or {}).get("migration_run_id") or ""
            )
            if stored_run_id != run_id:
                # Fail closed: only a link stamped with THIS run is our write. An
                # absent or foreign stamp means someone else created it, so claiming
                # "backfilled" here would audit a write that never happened.
                return outcome(
                    "already_canonical",
                    target_type=target_type,
                    classification=classification,
                )

            result = outcome(
                "backfilled",
                target_type=target_type,
                classification=classification,
                relationship_role=role,
                link_id=str(getattr(links[0], "id", "") or ""),
            )
            confirmed = True
            await self._confirm_candidate(claim_id, owner_token)
        finally:
            if not confirmed:
                await self._release_candidate(claim_id, owner_token)
        # The audit is emitted after the claim is confirmed, so a failure here
        # leaves a durable link with no audit row rather than an audited write
        # that never happened. Releasing instead would make the retry re-audit a
        # link it did not create — the worse of the two. Only an outbox removes
        # the gap entirely.
        await self.audit.emit(
            action="legacy_relationship.backfilled",
            actor_id=getattr(actor, "id", None),
            resource_type=target_type,
            resource_id=target_id,
            organization_id=str(candidate.get("organization_id") or org_id or ""),
            project_id=str(candidate.get("project_id") or project_id or ""),
            after={**result, "migration_run_id": run_id},
            reason="Operator legacy relationship backfill",
        )
        return result

    # -- helpers -----------------------------------------------------------

    # -- candidate ownership ------------------------------------------------

    @staticmethod
    def _candidate_claim_id(
        module: LegacyBackfillModule,
        candidate: dict[str, Any],
        target_type: str,
        target_id: str,
        document_id: str,
    ) -> str:
        """Identity of ONE migration decision — deliberately role-free.

        Includes the module and the legacy field so two registers, or two legacy
        arrays on one register, can never collide on the same key.
        """
        legacy_field = str(candidate.get("legacy_field") or "linked_document_ids")
        return (
            f"legacy-backfill:{module.module}:{legacy_field}"
            f":{target_type}:{target_id}:{document_id}"
        )

    def _claims_collection(self) -> Any:
        """The claims collection, however this handle exposes collections."""
        collection = getattr(self.db, BACKFILL_CLAIMS_COLLECTION, None)
        if collection is not None:
            return collection
        return self.db[BACKFILL_CLAIMS_COLLECTION]

    async def _claim_candidate(
        self,
        claim_id: str,
        *,
        actor: Any,
        role: str,
        run_id: str,
        owner_token: str,
        target_type: str,
        target_id: str,
        document_id: str,
    ) -> Optional[dict[str, Any]]:
        """Atomically take ownership. Returns None when we won, else the owner."""
        collection = self._claims_collection()
        now = datetime.now(timezone.utc)
        record = {
            "_id": claim_id,
            "relationship_role": role,
            "migration_run_id": run_id,
            "owner_token": owner_token,
            "operator_id": getattr(actor, "id", None),
            "target_type": target_type,
            "target_id": target_id,
            "document_id": document_id,
            "status": "processing",
            "claimed_at": now,
            "lease_expires_at": now + BACKFILL_CLAIM_LEASE,
        }
        try:
            await collection.insert_one(record)
            return None
        except DuplicateKeyError:
            pass

        existing = await collection.find_one({"_id": claim_id}) or {}
        if str(existing.get("status") or "") == "complete":
            return existing
        # An abandoned lease is reclaimable; a live one is not.
        reclaimed = await collection.find_one_and_update(
            {
                "_id": claim_id,
                "status": {"$ne": "complete"},
                "lease_expires_at": {"$lte": now},
            },
            {"$set": {key: value for key, value in record.items() if key != "_id"}},
        )
        if reclaimed is not None:
            return None
        return existing

    async def _confirm_candidate(self, claim_id: str, owner_token: str) -> None:
        """Mark our adjudication durable, so a later attempt is terminal."""
        await self._claims_collection().update_one(
            {"_id": claim_id, "owner_token": owner_token},
            {"$set": {"status": "complete", "completed_at": datetime.now(timezone.utc)}},
        )

    async def _release_candidate(self, claim_id: str, owner_token: str) -> None:
        """Release ownership so a failed adjudication can be retried.

        Scoped to our own token so a reclaim by a later run is never undone.
        """
        try:
            await self._claims_collection().delete_one(
                {"_id": claim_id, "owner_token": owner_token, "status": "processing"}
            )
        except Exception:  # noqa: BLE001 - release is best effort, never fatal
            logger.warning(
                "Failed to release legacy backfill claim %s; it will be "
                "reclaimable once its lease expires",
                claim_id,
                exc_info=True,
            )

    async def _target_context(self, row: dict[str, Any]) -> Any:
        """Canonical, server-derived target state for a legacy candidate."""
        target_type = str(row.get("target_type") or "")
        target_id = str(row.get("target_id") or "")
        if not target_type or not target_id:
            return None
        try:
            adapter = self.registry.get(target_type)
        except Exception:
            return None
        return await adapter.load(self.db, target_id)

    @staticmethod
    def _context_in_scope(
        context: Any, org_id: Optional[str], project_id: Optional[str]
    ) -> bool:
        if org_id and str(getattr(context, "organization_id", "") or "") != str(org_id):
            return False
        if project_id and str(getattr(context, "project_id", "") or "") != str(project_id):
            return False
        return True

    async def _existing_canonical(
        self, target_type: str, target_id: str, document_id: str
    ) -> Optional[dict[str, Any]]:
        """Any active relationship for this candidate, whatever its role.

        Role-free on purpose: the migration decision is per candidate, so an
        existing link under a different role is a conflict to report, not an
        invitation to add another.
        """
        return await self.db.entity_document_links.find_one(
            {
                "target_type": target_type,
                "target_id": target_id,
                "document_id": document_id,
                "removed_at": None,
            }
        )

    async def _previously_removed(
        self, target_type: str, target_id: str, document_id: str
    ) -> Optional[dict[str, Any]]:
        return await self.db.entity_document_links.find_one(
            {
                "target_type": target_type,
                "target_id": target_id,
                "document_id": document_id,
                "removed_at": {"$ne": None},
            }
        )
