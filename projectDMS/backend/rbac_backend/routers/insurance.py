"""Insurance Register API. PolicyService-gated, tenant-scoped, audited.

Mirrors the Bank Guarantee router: list / summary / alerts / export, full CRUD,
file replace, and the admin-managed Insurance Type master.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import List, Optional
from uuid import uuid4

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Body,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse

from ..core.config import settings
from ..core.database import get_db
from ..core.permissions import Permissions
from ..core.security import CurrentUser, build_scope_query, get_current_user
from ..models.insurance import (
    Insurance,
    InsuranceCreate,
    InsuranceSummary,
    InsuranceType,
    InsuranceTypeCreate,
    InsuranceTypeUpdate,
    InsuranceUpdate,
)
from ..models.document_relationship import DocumentRelationshipInput
from ..services.document_relationship_service import (
    DocumentRelationshipError,
    DocumentRelationshipService,
)
from ..services.audit_event_service import AuditEventService
from ..services.entity_adapter_registry import INSURANCE_DOCUMENT_ROLES
from ..services.insurance_document_link_migration import (
    canonicalize_legacy_insurance_file,
    classify_legacy_insurance_evidence,
)
from ..services.insurance_service import InsuranceService, InsuranceTypeService
from ..services.policy_service import PolicyService
from ..services.step_up_service import require_step_up
from ..utils.error_handler import BaseDomainError
from .documents import DocumentController, get_document_controller

router = APIRouter()
logger = logging.getLogger(__name__)

# Preserved legacy policy files remain readable only through canonical
# Document authority after migration.
_CONTENT_TYPES = {".pdf": "application/pdf", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}


def _insurance_dir() -> Path:
    directory = Path(settings.UPLOADS_DIR) / "insurance"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _insurance_file_path(token: Optional[str]) -> Path:
    """Resolve a stored token to a path, guarding against path traversal."""
    if not token or "/" in token or "\\" in token or ".." in token:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid file reference")
    base = _insurance_dir().resolve()
    path = (base / token).resolve()
    if path.parent != base:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid file reference")
    return path


async def get_policy(db=Depends(get_db)) -> PolicyService:
    return PolicyService(db=db)


async def _load(insurance_id: str, permission: str, db, current_user, policy) -> dict:
    ins = await InsuranceService(db).get(insurance_id)
    if not ins:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Insurance policy not found")
    await policy.authorize_document(current_user, permission, ins, resource_type="insurance")
    return (await _project_canonical_links(db, current_user, policy, [ins]))[0]


async def _project_canonical_links(
    db,
    current_user: CurrentUser,
    policy: PolicyService,
    rows: list[dict],
) -> list[dict]:
    """Expose authorized canonical links while retaining legacy metadata."""
    if not rows:
        return rows
    ids_by_policy = await DocumentRelationshipService(
        db, policy=policy
    ).authorized_document_ids_for_targets(current_user, "insurance", rows)
    projected: list[dict] = []
    for row in rows:
        item = dict(row)
        item["linked_document_ids"] = ids_by_policy.get(str(row.get("_id") or ""), [])
        projected.append(item)
    return projected


# --- register --------------------------------------------------------------


@router.get("/insurance", response_model=List[Insurance])
async def list_insurance(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    contract_id: Optional[str] = Query(None),
    insurance_type: Optional[str] = Query(None, alias="type"),
    insurance_company: Optional[str] = Query(None, alias="company"),
    status_filter: Optional[str] = Query(None, alias="status"),
    uploaded_by: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=2000),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user, Permissions.INSURANCE_VIEW, resource_type="insurance",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id, audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    items = await InsuranceService(db).list(
        scope, project_id=project_id, contract_id=contract_id, insurance_type=insurance_type,
        insurance_company=insurance_company, status=status_filter, uploaded_by=uploaded_by,
        q=q, skip=skip, limit=limit,
    )
    items = await _project_canonical_links(db, current_user, policy, items)
    return [Insurance(**i) for i in items]


@router.get("/insurance/summary", response_model=InsuranceSummary)
async def insurance_summary(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user, Permissions.INSURANCE_VIEW, resource_type="insurance",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id, audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    return InsuranceSummary(**await InsuranceService(db).summary(scope, project_id=project_id))


@router.get("/insurance/alerts", response_model=List[Insurance])
async def insurance_alerts(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user, Permissions.INSURANCE_VIEW, resource_type="insurance",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id, audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    items = await InsuranceService(db).alerts(scope)
    items = await _project_canonical_links(db, current_user, policy, items)
    return [Insurance(**i) for i in items]


@router.get("/insurance/export")
async def export_insurance(
    format: str = Query("csv", pattern="^(csv|xlsx|pdf)$"),
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user, Permissions.INSURANCE_EXPORT, resource_type="insurance",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id, audit=False,
    )
    from ..services import contract_controls_export as cx

    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    rows = await InsuranceService(db).list(scope, project_id=project_id, limit=5000)
    rows = await _project_canonical_links(db, current_user, policy, rows)
    return cx.export_response("insurance-register", cx.INSURANCE_COLUMNS, rows, format)


# --- insurance type master (admin-managed) ---------------------------------


@router.get("/insurance/types", response_model=List[InsuranceType])
async def list_insurance_types(
    organization_id: Optional[str] = Query(None),
    include_inactive: bool = Query(False),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user, Permissions.INSURANCE_VIEW, resource_type="insurance",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        audit=False,
    )
    org = organization_id or getattr(current_user, "organization_id", None)
    return [InsuranceType(**t) for t in await InsuranceTypeService(db).list(org, include_inactive=include_inactive)]


@router.post("/insurance/types", response_model=InsuranceType, status_code=status.HTTP_201_CREATED)
async def create_insurance_type(
    payload: InsuranceTypeCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    org = payload.organization_id or getattr(current_user, "organization_id", None)
    await policy.authorize(
        current_user, Permissions.INSURANCE_MANAGE_TYPES, resource_type="insurance",
        organization_id=org,
    )
    return InsuranceType(**await InsuranceTypeService(db).create(payload, current_user))


@router.put("/insurance/types/{type_id}", response_model=InsuranceType)
async def update_insurance_type(
    type_id: str,
    payload: InsuranceTypeUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    service = InsuranceTypeService(db)
    existing = await service.get(type_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Insurance type not found")
    await policy.authorize(
        current_user, Permissions.INSURANCE_MANAGE_TYPES, resource_type="insurance",
        organization_id=existing.get("organization_id"),
    )
    return InsuranceType(**(await service.update(existing, payload.model_dump(exclude_unset=True), current_user) or existing))


@router.delete("/insurance/types/{type_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_insurance_type(
    type_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    service = InsuranceTypeService(db)
    existing = await service.get(type_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Insurance type not found")
    await policy.authorize(
        current_user, Permissions.INSURANCE_MANAGE_TYPES, resource_type="insurance",
        organization_id=existing.get("organization_id"),
    )
    await service.delete(existing, current_user)
    return None


# --- CRUD ------------------------------------------------------------------


@router.post("/insurance", response_model=Insurance, status_code=status.HTTP_201_CREATED)
async def create_insurance(
    payload: InsuranceCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    org = payload.organization_id or getattr(current_user, "organization_id", None)
    await policy.authorize(
        current_user, Permissions.INSURANCE_CREATE, resource_type="insurance",
        organization_id=org, project_id=payload.project_id,
    )
    service = InsuranceService(db)
    created = await service.create(payload, current_user)

    # Path B: attach Documents that already exist instead of re-uploading them.
    # link_batch owns its own transaction and derives scope from the created
    # policy, so the caller cannot widen authority here. If it refuses the link,
    # the policy is rolled back rather than left without its evidence.
    existing_links = list(payload.existing_document_links or [])
    if existing_links:
        insurance_id = str(created.get("_id") or "")
        try:
            await DocumentRelationshipService(db, policy=policy).link_batch(
                current_user,
                "insurance",
                insurance_id,
                existing_links,
                source="user",
                idempotency_key=f"insurance-create:{insurance_id}",
            )
        except Exception as exc:
            # Compensation must cover infrastructure faults too, not just domain
            # refusals: a transaction abort would otherwise leave a committed
            # policy without the evidence the caller made a precondition.
            await service.rollback_create(
                created,
                current_user,
                reason="existing_document_link_failed",
            )
            if isinstance(exc, DocumentRelationshipError):
                # Preserve the refusal's own status: a scope 403 or role 422
                # must not degrade into a 500.
                raise HTTPException(
                    status_code=exc.status_code, detail=exc.detail
                ) from exc
            raise
    return Insurance(**created)


@router.post("/insurance/upload")
async def upload_insurance_file(
    file: UploadFile = File(...),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    """Reject the retired parallel-storage upload seam.

    Existing tokens remain readable. New evidence must use the policy-scoped
    canonical Document upload endpoint below.
    """
    await policy.authorize(
        current_user, Permissions.INSURANCE_CREATE, resource_type="insurance",
        organization_id=getattr(current_user, "organization_id", None),
    )
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=(
            "Legacy Insurance token uploads are retired; create the policy and "
            "use /insurance/{insurance_id}/documents/upload"
        ),
    )


@router.post(
    "/insurance/{insurance_id}/documents/upload",
    status_code=status.HTTP_201_CREATED,
)
async def upload_canonical_insurance_document(
    insurance_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    relationship_role: str = Form("policy"),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    controller: DocumentController = Depends(get_document_controller),
):
    """Upload through the canonical DMS pipeline, then link to the policy."""
    ins = await _load(
        insurance_id,
        Permissions.INSURANCE_EDIT,
        db,
        current_user,
        policy,
    )
    if relationship_role not in INSURANCE_DOCUMENT_ROLES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unsupported Insurance document relationship role",
        )
    issue_date = ins.get("date_of_issue")
    if not issue_date:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Insurance policy issue date is required before uploading a Document",
        )
    date_str = (
        issue_date.isoformat()
        if hasattr(issue_date, "isoformat")
        else str(issue_date or "")
    )
    document = await controller.create_document(
        background_tasks=background_tasks,
        file=file,
        organization_id=str(ins.get("organization_id") or ""),
        project_id=str(ins.get("project_id") or ""),
        upload_type="incoming",
        letter_no=str(ins.get("policy_number") or insurance_id),
        date_str=date_str,
        current_user=current_user,
        subject=f"Insurance policy {ins.get('policy_number') or insurance_id}",
        tags=["insurance"],
        status="draft",
        ocr_enabled=True,
        compression_enabled=False,
    )
    try:
        links = await DocumentRelationshipService(db, policy=policy).link_batch(
            current_user,
            "insurance",
            insurance_id,
            [
                DocumentRelationshipInput(
                    document_id=str(document.id),
                    relationship_role=relationship_role,
                )
            ],
            source="user",
        )
    except Exception as exc:  # noqa: BLE001 - compensated and re-raised below
        link_error = exc
    else:
        link_error = None

    if link_error is not None:
        # Compensation runs outside the handler so that escalating a failed
        # cleanup as 500 cannot also swallow the original refusal's status.
        try:
            await controller.compensate_failed_creation(
                str(document.id),
                current_user=current_user,
                reason="insurance_document_relationship_failed",
            )
        except (BaseDomainError, HTTPException):
            # Compensation's own refusal keeps its status; masking it as 500
            # would turn a 403/404 into a fake outage.
            raise
        except Exception as cleanup_exc:
            # The original link failure is no longer this frame's __context__,
            # so log it explicitly or the root cause is lost.
            logger.error(
                "Insurance Document link failed for %s; compensation also failed",
                document.id,
                exc_info=link_error,
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "Insurance Document link failed and compensation requires "
                    f"manual review for Document {document.id}"
                ),
            ) from cleanup_exc
        if isinstance(link_error, DocumentRelationshipError):
            raise HTTPException(
                status_code=link_error.status_code,
                detail=link_error.detail,
            ) from link_error
        raise link_error
    return {
        "document": document.model_dump(by_alias=True),
        "link": links[0].model_dump(by_alias=True),
    }


@router.get("/insurance/{insurance_id}", response_model=Insurance)
async def get_insurance(
    insurance_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    return Insurance(**await _load(insurance_id, Permissions.INSURANCE_VIEW, db, current_user, policy))


@router.get("/insurance/{insurance_id}/file")
async def get_insurance_file(
    insurance_id: str,
    download: bool = Query(False),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    """Serve preserved legacy bytes only through current canonical authority."""
    ins = await _load(insurance_id, Permissions.INSURANCE_VIEW, db, current_user, policy)
    token = ins.get("document_id")
    path = _insurance_file_path(token)
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No file attached")
    visible_links = await DocumentRelationshipService(db, policy=policy).list_for_target(
        current_user, "insurance", insurance_id
    )
    migration_link = next(
        (
            link
            for link in visible_links
            if link.source == "migration"
            and link.relationship_role == "policy"
            and str(link.metadata.get("legacy_storage_key") or "") == str(token or "")
            and link.metadata.get("legacy_sha256")
        ),
        None,
    )
    if migration_link is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Legacy Insurance file requires canonicalization or manual review",
        )
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != str(migration_link.metadata["legacy_sha256"]):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Legacy Insurance file changed after canonicalization",
        )
    media_type = ins.get("document_content_type") or _CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
    filename = ins.get("document_name") or token
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        content_disposition_type="attachment" if download else "inline",
    )


@router.put("/insurance/{insurance_id}", response_model=Insurance)
async def update_insurance(
    insurance_id: str,
    payload: InsuranceUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    ins = await _load(insurance_id, Permissions.INSURANCE_EDIT, db, current_user, policy)
    updated = await InsuranceService(db).update(ins, payload.model_dump(exclude_unset=True), current_user)
    return Insurance(
        **(
            await _project_canonical_links(
                db,
                current_user,
                policy,
                [updated or ins],
            )
        )[0]
    )


@router.post("/insurance/{insurance_id}/replace-file", response_model=Insurance)
async def replace_insurance_file(
    insurance_id: str,
    document_id: str = Body(..., embed=True),
    document_name: Optional[str] = Body(None, embed=True),
    document_content_type: Optional[str] = Body(None, embed=True),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load(insurance_id, Permissions.INSURANCE_EDIT, db, current_user, policy)
    raise HTTPException(
        status_code=status.HTTP_410_GONE,
        detail=(
            "Legacy Insurance token replacement is retired; use the canonical "
            "Document relationship API or policy-scoped canonical upload"
        ),
    )


@router.delete("/insurance/{insurance_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_insurance(
    insurance_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load(insurance_id, Permissions.INSURANCE_DELETE, db, current_user, policy)
    # delete_target emits the `insurance.deleted` audit inside its own transaction;
    # emitting a second one here would duplicate the record and lose the reason.
    await DocumentRelationshipService(db, policy=policy).delete_target(
        current_user,
        "insurance",
        insurance_id,
        reason="Insurance policy deleted",
    )
    return None


# --- Legacy evidence canonicalization (operator-only) ----------------------
#
# Legacy Insurance policies stored their file as a bare token under the uploads
# volume. `GET /insurance/{id}/file` now serves those bytes only through canonical
# Document authority, so an authorised operator needs a way to inventory and
# canonicalize them. The eligibility rules live in the canonicalization service —
# this seam only authorises, scopes, and reports. It deliberately cannot approve
# semantic document reuse: that decision stays a manual-review outcome.

_LEGACY_CANONICALIZATION_ACTION = "insurance.legacy_canonicalization"
_MAX_CANONICALIZATION_BATCH = 50
# Only these two classifications may canonicalize without a human decision.
# Everything else — ambiguity, cross-tenant, blocked, deleted, missing, hash
# mismatch, storage errors — stays manual review.
_AUTO_ELIGIBLE_CLASSIFICATIONS = {"requires_canonicalization", "orphan_file"}


def _legacy_insurance_root() -> Path:
    """The on-disk root holding preserved legacy Insurance policy files."""
    return _insurance_dir()


async def _authorize_legacy_canonicalization(
    request: Request,
    current_user: CurrentUser,
    policy: PolicyService,
    org_id: Optional[str],
    project_id: Optional[str],
) -> None:
    """Operator gate: explicit admin permission, scoped, with step-up."""
    if not org_id and not project_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide org_id or project_id to scope legacy canonicalization.",
        )
    await policy.authorize(
        current_user,
        Permissions.DMS_ADMIN,
        resource_type="insurance_legacy_canonicalization",
        organization_id=org_id,
        project_id=project_id,
    )
    await require_step_up(
        request, current_user, action=_LEGACY_CANONICALIZATION_ACTION
    )


def _in_operator_scope(
    insurance: dict, org_id: Optional[str], project_id: Optional[str]
) -> bool:
    if org_id and str(insurance.get("organization_id") or "") != str(org_id):
        return False
    if project_id and str(insurance.get("project_id") or "") != str(project_id):
        return False
    return True


@router.post("/insurance/legacy-evidence/inventory")
async def inventory_legacy_insurance_evidence(
    request: Request,
    org_id: Optional[str] = Body(None),
    project_id: Optional[str] = Body(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
) -> dict:
    """Read-only classification of preserved legacy Insurance evidence."""
    await _authorize_legacy_canonicalization(
        request, current_user, policy, org_id, project_id
    )
    report = await classify_legacy_insurance_evidence(
        db, legacy_root=_legacy_insurance_root()
    )
    candidates = [
        row
        for row in report.get("candidates", [])
        if _in_operator_scope(row, org_id, project_id)
    ]
    counts: dict = {}
    for row in candidates:
        classification = str(row.get("classification") or "unknown")
        counts[classification] = counts.get(classification, 0) + 1
    return {
        "org_id": org_id,
        "project_id": project_id,
        "candidates": candidates,
        "counts": counts,
        "eligible_classifications": sorted(_AUTO_ELIGIBLE_CLASSIFICATIONS),
    }


@router.post("/insurance/legacy-evidence/canonicalize")
async def canonicalize_legacy_insurance_evidence(
    request: Request,
    insurance_ids: List[str] = Body(...),
    org_id: Optional[str] = Body(None),
    project_id: Optional[str] = Body(None),
    dry_run: bool = Body(True),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
    controller: DocumentController = Depends(get_document_controller),
) -> dict:
    """Canonicalize explicitly selected, eligible legacy Insurance policy files."""
    await _authorize_legacy_canonicalization(
        request, current_user, policy, org_id, project_id
    )
    selected = [str(value).strip() for value in (insurance_ids or []) if str(value).strip()]
    if not selected:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Select at least one Insurance policy to canonicalize.",
        )
    if len(selected) > _MAX_CANONICALIZATION_BATCH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Select at most "
                f"{_MAX_CANONICALIZATION_BATCH} Insurance policies per run."
            ),
        )

    legacy_root = _legacy_insurance_root()
    audit = AuditEventService(db)
    migration_run_id = f"insurance-operator:{getattr(current_user, 'id', '')}:{uuid4().hex}"
    report = await classify_legacy_insurance_evidence(db, legacy_root=legacy_root)
    by_target = {
        str(row.get("target_id") or ""): row
        for row in report.get("candidates", [])
        if row.get("source_kind") == "legacy_file"
    }

    results: List[dict] = []
    for insurance_id in selected:
        insurance = await db.insurance_policies.find_one({"_id": insurance_id})
        if insurance is None:
            results.append(
                {
                    "insurance_id": insurance_id,
                    "status": "not_found",
                    "reason": "Insurance policy not found",
                }
            )
            continue
        if not _in_operator_scope(insurance, org_id, project_id):
            results.append(
                {
                    "insurance_id": insurance_id,
                    "status": "out_of_scope",
                    "reason": "Insurance policy is outside the requested scope",
                }
            )
            continue

        candidate = by_target.get(insurance_id)
        classification = str((candidate or {}).get("classification") or "unknown")
        if candidate is None or classification not in _AUTO_ELIGIBLE_CLASSIFICATIONS:
            results.append(
                {
                    "insurance_id": insurance_id,
                    "status": "requires_manual_review",
                    "classification": classification,
                    "findings": list((candidate or {}).get("findings") or []),
                }
            )
            continue
        if dry_run:
            results.append(
                {
                    "insurance_id": insurance_id,
                    "status": "eligible",
                    "classification": classification,
                }
            )
            continue

        try:
            migrated = await canonicalize_legacy_insurance_file(
                db,
                insurance_id=insurance_id,
                legacy_root=legacy_root,
                current_user=current_user,
                controller=controller,
                policy=policy,
                migration_run_id=migration_run_id,
            )
        except (BaseDomainError, HTTPException):
            raise
        except ValueError as exc:
            results.append(
                {
                    "insurance_id": insurance_id,
                    "status": "rejected",
                    "classification": classification,
                    "reason": str(exc),
                }
            )
            continue
        results.append(
            {
                "insurance_id": insurance_id,
                "status": "canonicalized",
                "classification": classification,
                "document_id": migrated.get("document_id"),
                "document_version_id": migrated.get("document_version_id"),
            }
        )

    for outcome in results:
        await audit.emit(
            action="insurance.legacy_canonicalization",
            actor_id=getattr(current_user, "id", None),
            resource_type="insurance",
            resource_id=outcome["insurance_id"],
            organization_id=org_id,
            project_id=project_id,
            after={**outcome, "dry_run": dry_run, "migration_run_id": migration_run_id},
            reason="Operator legacy Insurance canonicalization",
        )

    return {
        "dry_run": dry_run,
        "migration_run_id": migration_run_id,
        "org_id": org_id,
        "project_id": project_id,
        "results": results,
    }
