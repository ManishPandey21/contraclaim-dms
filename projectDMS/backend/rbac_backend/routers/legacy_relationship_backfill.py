"""Operator control plane for the unified legacy relationship backfill.

One surface for every register. The module key selects a classifier; the
orchestration — authorization, scope, dry-run, selection, idempotency, audit —
is identical for all of them, and no module semantics live here.

Deliberately absent: any startup hook, background job, scheduler, or
"migrate everything" mode. An operator must scope, dry-run, review, select and
execute, every time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, List, Optional
from uuid import uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status

from ..core.config import settings
from ..core.database import get_db
from ..core.permissions import Permissions
from ..core.security import CurrentUser, get_current_user
from ..services.bank_guarantee_document_link_migration import (
    classify_legacy_bank_guarantee_links,
)
from ..services.claim_document_link_migration import (
    classify_legacy_claim_letter_links,
    classify_legacy_claim_links,
)
from ..services.ipc_document_link_migration import classify_legacy_ipc_links
from ..services.key_date_document_link_migration import (
    EVENT_PROVEN_SOURCE_KINDS,
    INVENTORY_ONLY_SOURCE_KINDS,
    classify_legacy_key_date_links,
)
from ..services.insurance_document_link_migration import (
    classify_legacy_insurance_evidence,
)
from ..services.legacy_relationship_backfill import (
    LegacyBackfillError,
    BackfillCapability,
    LegacyBackfillModule,
    LegacyRelationshipBackfillService,
    MAX_BACKFILL_BATCH,
)
from ..services.policy_service import PolicyService
from ..services.step_up_service import require_step_up

router = APIRouter()

_BACKFILL_ACTION = "legacy_relationship.backfill"


def _legacy_insurance_root() -> Path:
    directory = Path(settings.UPLOADS_DIR) / "insurance"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


# Registry of legacy sources. Adding a register means adding one entry plus a
# classifier that tags its rows with a `source_kind` — never a branch inside the
# orchestrator or the relationship service.
#
# Every register is now represented. Capability is per registered entry, so a
# register whose legacy data cannot prove event ownership is exposed for
# reconciliation without ever appearing writable.
_MODULES: dict[str, LegacyBackfillModule] = {
    "claim": LegacyBackfillModule(
        module="claim",
        classify=classify_legacy_claim_links,
        source_kinds=frozenset({"legacy_linked_document_id"}),
    ),
    "claim_letter": LegacyBackfillModule(
        module="claim_letter",
        classify=classify_legacy_claim_letter_links,
        source_kinds=frozenset({"legacy_linked_letter_id"}),
    ),
    "ipc": LegacyBackfillModule(
        module="ipc",
        classify=classify_legacy_ipc_links,
        source_kinds=frozenset({"legacy_linked_document_id"}),
    ),
    "bank_guarantee": LegacyBackfillModule(
        module="bank_guarantee",
        classify=classify_legacy_bank_guarantee_links,
        # Both legacy sources are surfaced so an operator sees the whole
        # picture; the parent array carries `ambiguous_event` and can never
        # migrate, while extension history resolves to an exact event.
        source_kinds=frozenset(
            {"legacy_bg_parent_array", "legacy_bg_extension_history"}
        ),
        # Repository-defined BG legacy evidence is parent-level and carries no
        # exact event provenance, so it cannot be auto-mapped to a
        # bank_guarantee_event without inventing ownership. BG is therefore
        # reconciliation/inventory only; the classifier still surfaces every
        # candidate for manual review.
        capability=BackfillCapability.INVENTORY_ONLY,
    ),
    # Key Date splits by SOURCE, not by register: the three event collections
    # carry the document on the event row itself, so their event identity is
    # proven and they are writable. The milestone parent array and the
    # deprecated EOT application shape prove nothing about which event owns the
    # evidence, so they are reconciliation only.
    "key_date": LegacyBackfillModule(
        module="key_date",
        classify=classify_legacy_key_date_links,
        source_kinds=EVENT_PROVEN_SOURCE_KINDS,
    ),
    "key_date_legacy": LegacyBackfillModule(
        module="key_date_legacy",
        classify=classify_legacy_key_date_links,
        source_kinds=INVENTORY_ONLY_SOURCE_KINDS,
        capability=BackfillCapability.INVENTORY_ONLY,
    ),
    "insurance": LegacyBackfillModule(
        module="insurance",
        classify=classify_legacy_insurance_evidence,
        source_kinds=frozenset({"legacy_linked_document_id"}),
        classifier_kwargs=lambda: {"legacy_root": _legacy_insurance_root()},
    ),
}


async def get_policy(db=Depends(get_db)) -> PolicyService:
    return PolicyService(db=db)


def _module(module: str) -> LegacyBackfillModule:
    entry = _MODULES.get(str(module or "").strip().lower())
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown legacy backfill module: {module}",
        )
    return entry


async def _authorize_backfill(
    request: Request,
    current_user: CurrentUser,
    policy: PolicyService,
    org_id: Optional[str],
    project_id: Optional[str],
) -> None:
    if not org_id and not project_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide org_id or project_id to scope the backfill.",
        )
    await policy.authorize(
        current_user,
        Permissions.DMS_ADMIN,
        resource_type="legacy_relationship_backfill",
        organization_id=org_id,
        project_id=project_id,
    )
    await require_step_up(request, current_user, action=_BACKFILL_ACTION)


@router.post("/legacy-relationship-backfill/inventory")
async def inventory_legacy_relationship_backfill(
    request: Request,
    module: str = Body(...),
    org_id: Optional[str] = Body(None),
    project_id: Optional[str] = Body(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
) -> dict:
    """Read-only classification of one register's legacy relationship rows."""
    await _authorize_backfill(request, current_user, policy, org_id, project_id)
    return await LegacyRelationshipBackfillService(db, policy=policy).inventory(
        _module(module), org_id=org_id, project_id=project_id
    )


@router.post("/legacy-relationship-backfill/apply")
async def apply_legacy_relationship_backfill(
    request: Request,
    module: str = Body(...),
    selections: List[dict] = Body(...),
    org_id: Optional[str] = Body(None),
    project_id: Optional[str] = Body(None),
    dry_run: bool = Body(True),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
) -> dict:
    """Backfill explicitly selected, unambiguous legacy relationships."""
    await _authorize_backfill(request, current_user, policy, org_id, project_id)
    entry = _module(module)
    run_id = f"legacy-backfill:{getattr(current_user, 'id', '')}:{uuid4().hex}"
    service = LegacyRelationshipBackfillService(db, policy=policy)
    try:
        results = await service.apply(
            current_user,
            entry,
            selections,
            org_id=org_id,
            project_id=project_id,
            dry_run=dry_run,
            run_id=run_id,
        )
    except LegacyBackfillError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {
        "module": entry.module,
        "dry_run": dry_run,
        "migration_run_id": run_id,
        "org_id": org_id,
        "project_id": project_id,
        "max_batch": MAX_BACKFILL_BATCH,
        "results": results,
    }


__all__ = ["router"]
