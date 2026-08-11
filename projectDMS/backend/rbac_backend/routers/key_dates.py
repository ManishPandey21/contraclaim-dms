"""Key Date / Milestone Tracker API.

Tenant-scoped CRUD for milestones plus the EOT lifecycle, extension history and
achievement recording. Every endpoint is gated by PolicyService and lists are
filtered with build_scope_query, so Org A never sees Org B's key dates.
"""

from __future__ import annotations

import csv
import io
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Response, UploadFile, status

from ..core.database import get_db
from ..core.permissions import Permissions
from ..core.security import CurrentUser, build_scope_query, get_current_user
from ..models.key_date import (
    AchievementRecord,
    BaselineFreezeRequest,
    EOTDetermination,
    EOTDeterminationCreate,
    EOTDeterminationUpdate,
    EOTApplication,
    EOTApplicationCreate,
    EOTReview,
    EOTSubmission,
    EOTSubmissionCreate,
    EOTSubmissionUpdate,
    ExtensionHistory,
    KeyDateDashboard,
    KeyDateBaseline,
    KeyDateMilestone,
    KeyDateMilestoneCreate,
    KeyDateMilestoneUpdate,
    KeyDateWorkflowSummary,
)
from ..models.csv_import import CSVImportPreview, CSVImportResult
from ..services.key_date_service import KeyDateError, KeyDateService
from ..services.key_date_revision_service import (
    DETERMINATION_TEMPLATE_HEADERS,
    SUBMISSION_TEMPLATE_HEADERS,
    KeyDateRevisionService,
)
from ..services.policy_service import PolicyService
from ..services.register_csv_import import (
    KEY_DATE_SAMPLE_ROW,
    KEY_DATE_TEMPLATE_HEADERS,
    validate_csv_import_scope,
    import_key_dates_csv,
    preview_key_dates_csv,
    template_csv,
)

router = APIRouter()


async def get_policy(db=Depends(get_db)) -> PolicyService:
    return PolicyService(db=db)


def _bad_request(exc: KeyDateError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


async def _load(milestone_id: str, permission: str, db, current_user, policy) -> dict:
    svc = KeyDateService(db)
    m = await svc.get(milestone_id)
    if not m:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Milestone not found")
    await policy.authorize_document(current_user, permission, m, resource_type="key_date_milestone")
    return m


async def _read_csv(file: UploadFile) -> bytes:
    filename = (file.filename or "").lower()
    if filename and not filename.endswith(".csv"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Upload a .csv file")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CSV file is empty")
    return content


async def _load_submission(submission_id: str, permission: str, db, current_user, policy) -> dict:
    submission = await KeyDateRevisionService(db).get_submission(submission_id)
    if not submission:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="EOT submission not found")
    await policy.authorize_document(
        current_user, permission, submission, resource_type="key_date_eot_submission"
    )
    return submission


async def _load_determination(determination_id: str, permission: str, db, current_user, policy) -> dict:
    determination = await KeyDateRevisionService(db).get_determination(determination_id)
    if not determination:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="EOT determination not found")
    await policy.authorize_document(
        current_user, permission, determination, resource_type="key_date_eot_determination"
    )
    return determination


def _organization_id(current_user: CurrentUser, supplied: Optional[str] = None) -> Optional[str]:
    return supplied or getattr(current_user, "organization_id", None)


async def _authorize_workflow_scope(
    *, permission: str, project_id: str, organization_id: Optional[str],
    current_user: CurrentUser, policy: PolicyService,
) -> Optional[str]:
    org = _organization_id(current_user, organization_id)
    await policy.authorize(
        current_user, permission, resource_type="key_date_revision",
        organization_id=org, project_id=project_id,
    )
    return org


async def _export_metadata(db: Any, resource: Dict[str, Any], title: str) -> Dict[str, Any]:
    org_id = resource.get("organization_id")
    project_id = resource.get("project_id")
    organization = await db.organizations.find_one({"_id": org_id}) if org_id else None
    project = await db.projects.find_one({"_id": project_id}) if project_id else None
    return {
        "Organisation": (organization or {}).get("name") or org_id or "",
        "Project": (project or {}).get("name") or project_id or "",
        "Contract": resource.get("contract_id") or "primary",
        "Record": title,
    }


def _download_response(content: str | bytes, format: str, filename: str) -> Response:
    media = {
        "csv": "text/csv",
        "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "pdf": "application/pdf",
    }[format]
    return Response(
        content=content, media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{filename}.{format}"'},
    )


@router.get("/key-dates", response_model=List[KeyDateMilestone])
async def list_milestones(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    responsible_party_id: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user, Permissions.KEYDATE_VIEW, resource_type="key_dates",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id, audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    items = await KeyDateService(db).list(
        scope, project_id=project_id, status=status_filter,
        responsible_party_id=responsible_party_id, skip=skip, limit=limit,
    )
    return [KeyDateMilestone(**m) for m in items]


@router.get("/key-dates/dashboard", response_model=KeyDateDashboard)
async def key_date_dashboard(
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user, Permissions.KEYDATE_VIEW, resource_type="key_dates",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id, audit=False,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    return KeyDateDashboard(**await KeyDateService(db).dashboard(scope, project_id=project_id))


@router.get("/key-dates/export")
async def export_key_dates(
    format: str = Query("csv", pattern="^(csv|xlsx|pdf)$"),
    organization_id: Optional[str] = Query(None),
    project_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user, Permissions.KEYDATE_EXPORT, resource_type="key_dates",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id, audit=False,
    )
    from ..services import key_date_export as kx

    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    rows = await KeyDateService(db).list(scope, project_id=project_id, limit=5000)
    name = "key-date-register"
    if format == "csv":
        return Response(
            content=kx.milestones_to_csv(rows), media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{name}.csv"'},
        )
    if format == "xlsx":
        try:
            content = kx.milestones_to_xlsx(rows)
        except ImportError:
            raise HTTPException(status_code=501, detail="XLSX export is not available on this server")
        return Response(
            content=content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{name}.xlsx"'},
        )
    try:
        content = kx.milestones_to_pdf(rows)
    except ImportError:
        raise HTTPException(status_code=501, detail="PDF export is not available on this server")
    return Response(
        content=content, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{name}.pdf"'},
    )


@router.get("/key-dates/import/template")
async def key_dates_import_template(
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await policy.authorize(
        current_user,
        Permissions.KEYDATE_CREATE,
        resource_type="key_dates",
        organization_id=getattr(current_user, "organization_id", None),
        audit=False,
    )
    return Response(
        content=template_csv(KEY_DATE_TEMPLATE_HEADERS, KEY_DATE_SAMPLE_ROW),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="key-date-import-template.csv"'},
    )


@router.post("/key-dates/import/preview", response_model=CSVImportPreview)
async def preview_key_dates_import(
    file: UploadFile = File(...),
    organization_id: str = Form(..., min_length=1),
    project_id: str = Form(..., min_length=1),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    content = await _read_csv(file)
    try:
        organization_id, project_id = await validate_csv_import_scope(
            db,
            organization_id=organization_id,
            project_id=project_id,
        )
        await policy.authorize(
            current_user,
            Permissions.KEYDATE_CREATE,
            resource_type="key_date_milestone",
            organization_id=organization_id,
            project_id=project_id,
            audit=False,
        )
        preview = await preview_key_dates_csv(
            db,
            content,
            current_user,
            project_id=project_id,
            organization_id=organization_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return preview.response


@router.post("/key-dates/import", response_model=CSVImportResult)
async def import_key_dates(
    file: UploadFile = File(...),
    organization_id: str = Form(..., min_length=1),
    project_id: str = Form(..., min_length=1),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    content = await _read_csv(file)
    try:
        organization_id, project_id = await validate_csv_import_scope(
            db,
            organization_id=organization_id,
            project_id=project_id,
        )
        await policy.authorize(
            current_user,
            Permissions.KEYDATE_CREATE,
            resource_type="key_date_milestone",
            organization_id=organization_id,
            project_id=project_id,
            audit=False,
        )
        return await import_key_dates_csv(
            db,
            content,
            current_user,
            project_id=project_id,
            organization_id=organization_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/key-dates", response_model=KeyDateMilestone, status_code=status.HTTP_201_CREATED)
async def create_milestone(
    payload: KeyDateMilestoneCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    org = payload.organization_id or getattr(current_user, "organization_id", None)
    await policy.authorize(
        current_user, Permissions.KEYDATE_CREATE, resource_type="key_date_milestone",
        organization_id=org, project_id=payload.project_id,
    )
    try:
        created = await KeyDateService(db).create_milestone(payload, current_user)
    except KeyDateError as exc:
        raise _bad_request(exc)
    return KeyDateMilestone(**created)


@router.post("/key-dates/recalculate")
async def recalculate_key_dates(
    project_id: str = Query(..., min_length=1),
    organization_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    """Re-derive a project's milestone key dates from the current LOA + week basis.

    Opt-in admin action used after correcting the contract start date / week basis.
    Baselines under an approved EOT revision are left untouched.
    """
    await policy.authorize(
        current_user, Permissions.KEYDATE_EDIT, resource_type="key_dates",
        organization_id=organization_id or getattr(current_user, "organization_id", None),
        project_id=project_id,
    )
    scope = build_scope_query(current_user, organization_id=organization_id, project_id=project_id)
    try:
        return await KeyDateService(db).recalculate_project(scope, project_id, current_user)
    except KeyDateError as exc:
        raise _bad_request(exc)


# --- project/contract baseline + successive EOT revisions ----------------


@router.get("/key-dates/workflow", response_model=KeyDateWorkflowSummary)
@router.get("/key-dates/revisions", response_model=KeyDateWorkflowSummary)
async def key_date_workflow(
    project_id: str = Query(..., min_length=1),
    contract_id: str = Query("primary", min_length=1),
    organization_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    org = await _authorize_workflow_scope(
        permission=Permissions.KEYDATE_VIEW, project_id=project_id,
        organization_id=organization_id, current_user=current_user, policy=policy,
    )
    return KeyDateWorkflowSummary(**await KeyDateRevisionService(db).workflow_summary(
        org, project_id, contract_id
    ))


@router.post("/key-dates/baseline/freeze", response_model=KeyDateBaseline)
async def freeze_original_key_dates(
    payload: BaselineFreezeRequest,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    if not payload.confirmation:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Baseline freeze was not confirmed")
    org = await _authorize_workflow_scope(
        permission=Permissions.KEYDATE_BASELINE_FREEZE, project_id=payload.project_id,
        organization_id=payload.organization_id, current_user=current_user, policy=policy,
    )
    try:
        return KeyDateBaseline(**await KeyDateRevisionService(db).freeze_baseline(
            org, payload.project_id, payload.contract_id, current_user
        ))
    except KeyDateError as exc:
        raise _bad_request(exc)


@router.post("/key-dates/eot-submissions", response_model=EOTSubmission, status_code=status.HTTP_201_CREATED)
async def create_eot_submission_revision(
    payload: EOTSubmissionCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    org = await _authorize_workflow_scope(
        permission=Permissions.KEYDATE_EOT_SUBMIT, project_id=payload.project_id,
        organization_id=payload.organization_id, current_user=current_user, policy=policy,
    )
    payload.organization_id = org
    try:
        return EOTSubmission(**await KeyDateRevisionService(db).create_submission(payload, current_user))
    except KeyDateError as exc:
        raise _bad_request(exc)


@router.put("/key-dates/eot-submissions/{submission_id}", response_model=EOTSubmission)
async def update_eot_submission_revision(
    submission_id: str,
    payload: EOTSubmissionUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    submission = await _load_submission(
        submission_id, Permissions.KEYDATE_EOT_SUBMIT, db, current_user, policy
    )
    try:
        return EOTSubmission(**await KeyDateRevisionService(db).update_submission(
            submission, payload, current_user
        ))
    except KeyDateError as exc:
        raise _bad_request(exc)


@router.post("/key-dates/eot-submissions/{submission_id}/lock", response_model=EOTSubmission)
async def lock_eot_submission_revision(
    submission_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    submission = await _load_submission(
        submission_id, Permissions.KEYDATE_EOT_LOCK_SUBMISSION, db, current_user, policy
    )
    try:
        return EOTSubmission(**await KeyDateRevisionService(db).lock_submission(
            submission, current_user
        ))
    except KeyDateError as exc:
        raise _bad_request(exc)


@router.get("/key-dates/eot-submissions/{submission_id}/template")
async def eot_submission_template(
    submission_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    submission = await _load_submission(
        submission_id, Permissions.KEYDATE_EOT_SUBMIT, db, current_user, policy
    )
    milestones = await KeyDateService(db).list(
        build_scope_query(
            current_user,
            organization_id=submission.get("organization_id"),
            project_id=submission.get("project_id"),
        ),
        project_id=submission.get("project_id"), limit=5000,
    )
    milestones = [
        row for row in milestones
        if str(row.get("contract_id") or "primary") == str(submission.get("contract_id") or "primary")
    ]
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(SUBMISSION_TEMPLATE_HEADERS)
    existing = {
        str(item.get("milestone_ref") or "").casefold(): item
        for item in submission.get("items") or []
    }
    for milestone in milestones:
        item = existing.get(str(milestone.get("milestone_ref") or "").casefold(), {})
        writer.writerow([
            milestone.get("milestone_ref"),
            milestone.get("description") or milestone.get("title"),
            milestone.get("original_planned_key_date"),
            milestone.get("current_approved_key_date"),
            item.get("eot_submitted_date"),
            item.get("claimed_extension_days"),
            item.get("remarks"),
        ])
    return Response(
        content=buffer.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{submission.get("revision_label")}-submission-template.csv"'},
    )


@router.post("/key-dates/eot-submissions/{submission_id}/import/preview", response_model=CSVImportPreview)
async def preview_eot_submission_csv(
    submission_id: str,
    file: UploadFile = File(...),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    submission = await _load_submission(
        submission_id, Permissions.KEYDATE_EOT_SUBMIT, db, current_user, policy
    )
    try:
        preview, _items = await KeyDateRevisionService(db).submission_csv_preview(
            submission, await _read_csv(file)
        )
        return preview
    except KeyDateError as exc:
        raise _bad_request(exc)


@router.post("/key-dates/eot-submissions/{submission_id}/import", response_model=CSVImportResult)
async def import_eot_submission_csv(
    submission_id: str,
    file: UploadFile = File(...),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    submission = await _load_submission(
        submission_id, Permissions.KEYDATE_EOT_SUBMIT, db, current_user, policy
    )
    try:
        return await KeyDateRevisionService(db).import_submission_csv(
            submission, await _read_csv(file), current_user
        )
    except KeyDateError as exc:
        raise _bad_request(exc)


@router.post("/key-dates/eot-determinations", response_model=EOTDetermination, status_code=status.HTTP_201_CREATED)
async def create_eot_determination(
    payload: EOTDeterminationCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    org = await _authorize_workflow_scope(
        permission=Permissions.KEYDATE_EOT_DETERMINE, project_id=payload.project_id,
        organization_id=payload.organization_id, current_user=current_user, policy=policy,
    )
    payload.organization_id = org
    try:
        return EOTDetermination(**await KeyDateRevisionService(db).create_determination(
            payload, current_user
        ))
    except KeyDateError as exc:
        raise _bad_request(exc)


@router.put("/key-dates/eot-determinations/{determination_id}", response_model=EOTDetermination)
async def update_eot_determination(
    determination_id: str,
    payload: EOTDeterminationUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    determination = await _load_determination(
        determination_id, Permissions.KEYDATE_EOT_DETERMINE, db, current_user, policy
    )
    try:
        return EOTDetermination(**await KeyDateRevisionService(db).update_determination(
            determination, payload, current_user
        ))
    except KeyDateError as exc:
        raise _bad_request(exc)


@router.post("/key-dates/eot-determinations/{determination_id}/freeze", response_model=EOTDetermination)
async def freeze_eot_determination(
    determination_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    determination = await _load_determination(
        determination_id, Permissions.KEYDATE_EOT_FREEZE_DETERMINATION,
        db, current_user, policy,
    )
    try:
        return EOTDetermination(**await KeyDateRevisionService(db).freeze_determination(
            determination, current_user
        ))
    except KeyDateError as exc:
        raise _bad_request(exc)


@router.get("/key-dates/eot-determinations/{determination_id}/template")
async def eot_determination_template(
    determination_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    determination = await _load_determination(
        determination_id, Permissions.KEYDATE_EOT_DETERMINE, db, current_user, policy
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(DETERMINATION_TEMPLATE_HEADERS)
    existing = {
        str(item.get("milestone_ref") or "").casefold(): item
        for item in determination.get("items") or []
    }
    covered: Dict[str, Dict[str, Any]] = {}
    svc = KeyDateRevisionService(db)
    for submission_id in determination.get("eot_submission_ids") or []:
        submission = await svc.get_submission(str(submission_id))
        for item in (submission or {}).get("items") or []:
            covered[str(item.get("milestone_ref") or "").casefold()] = item
    for key, submitted in sorted(covered.items(), key=lambda entry: entry[0]):
        item = existing.get(key, {})
        milestone = await db.key_date_milestones.find_one({"_id": str(submitted.get("key_date_id"))}) or {}
        writer.writerow([
            submitted.get("milestone_ref"), submitted.get("description"),
            submitted.get("eot_submitted_date"), milestone.get("current_approved_key_date"),
            item.get("eot_granted_date"), item.get("granted_extension_days"),
            item.get("determination_result") or "pending", item.get("remarks"),
        ])
    return Response(
        content=buffer.getvalue(), media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="eot-determination-template.csv"'},
    )


@router.post("/key-dates/eot-determinations/{determination_id}/import/preview", response_model=CSVImportPreview)
async def preview_eot_determination_csv(
    determination_id: str,
    file: UploadFile = File(...),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    determination = await _load_determination(
        determination_id, Permissions.KEYDATE_EOT_DETERMINE, db, current_user, policy
    )
    try:
        preview, _items = await KeyDateRevisionService(db).determination_csv_preview(
            determination, await _read_csv(file)
        )
        return preview
    except KeyDateError as exc:
        raise _bad_request(exc)


@router.post("/key-dates/eot-determinations/{determination_id}/import", response_model=CSVImportResult)
async def import_eot_determination_csv(
    determination_id: str,
    file: UploadFile = File(...),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    determination = await _load_determination(
        determination_id, Permissions.KEYDATE_EOT_DETERMINE, db, current_user, policy
    )
    try:
        return await KeyDateRevisionService(db).import_determination_csv(
            determination, await _read_csv(file), current_user
        )
    except KeyDateError as exc:
        raise _bad_request(exc)


@router.get("/key-dates/baseline/export")
async def export_frozen_baseline(
    format: str = Query("csv", pattern="^(csv|xlsx|pdf)$"),
    project_id: str = Query(..., min_length=1),
    contract_id: str = Query("primary", min_length=1),
    organization_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    org = await _authorize_workflow_scope(
        permission=Permissions.KEYDATE_EXPORT, project_id=project_id,
        organization_id=organization_id, current_user=current_user, policy=policy,
    )
    baseline = await KeyDateRevisionService(db).baseline(org, project_id, contract_id)
    if not baseline or baseline.get("status") != "frozen":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Frozen baseline not found")
    from ..services import key_date_revision_export as kx
    metadata = await _export_metadata(db, baseline, "Original Contractual Key Dates")
    metadata.update({
        "Revision": 0, "Status": "Frozen", "Frozen By": baseline.get("frozen_by"),
        "Frozen On": baseline.get("frozen_at"),
    })
    headers, rows = kx.baseline_table(baseline)
    return _download_response(
        kx.render(format, metadata, headers, rows, "Frozen Original Key Dates"),
        format, "key-dates-original-frozen",
    )


@router.get("/key-dates/eot-submissions/{submission_id}/export")
async def export_eot_submission(
    submission_id: str,
    format: str = Query("csv", pattern="^(csv|xlsx|pdf)$"),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    submission = await _load_submission(
        submission_id, Permissions.KEYDATE_EXPORT, db, current_user, policy
    )
    from ..services import key_date_revision_export as kx
    metadata = await _export_metadata(db, submission, f"{submission.get('revision_label')} Submission")
    metadata.update({
        "Revision": submission.get("revision_number"), "Status": submission.get("status"),
        "Contractor Submission Date": submission.get("contractor_submission_date"),
        "Contractor Letter Reference": submission.get("contractor_letter_reference"),
        "Locked By": submission.get("locked_by"), "Locked On": submission.get("locked_at"),
    })
    headers, rows = kx.submission_table(submission)
    filename = f"{str(submission.get('revision_label') or 'eot').lower()}-submission"
    return _download_response(kx.render(format, metadata, headers, rows, "EOT Submission"), format, filename)


@router.get("/key-dates/eot-determinations/{determination_id}/export")
async def export_eot_determination(
    determination_id: str,
    format: str = Query("csv", pattern="^(csv|xlsx|pdf)$"),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    determination = await _load_determination(
        determination_id, Permissions.KEYDATE_EXPORT, db, current_user, policy
    )
    from ..services import key_date_revision_export as kx
    covered = ", ".join(determination.get("covered_revision_labels") or [])
    metadata = await _export_metadata(db, determination, f"Determination covering {covered}")
    metadata.update({
        "Determination Status": determination.get("status"),
        "Determination Reference": determination.get("determination_reference"),
        "Determination Date": determination.get("determination_date"),
        "Approval / Grant Reference": determination.get("approval_grant_reference"),
        "Frozen By": determination.get("frozen_by"), "Frozen On": determination.get("frozen_at"),
    })
    headers, rows = kx.determination_table(determination)
    return _download_response(
        kx.render(format, metadata, headers, rows, "EOT Determination"),
        format, "eot-determination",
    )


@router.get("/key-dates/history/export")
async def export_complete_key_date_history(
    format: str = Query("csv", pattern="^(csv|xlsx|pdf)$"),
    project_id: str = Query(..., min_length=1),
    contract_id: str = Query("primary", min_length=1),
    organization_id: Optional[str] = Query(None),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    org = await _authorize_workflow_scope(
        permission=Permissions.KEYDATE_EXPORT, project_id=project_id,
        organization_id=organization_id, current_user=current_user, policy=policy,
    )
    scope = build_scope_query(current_user, organization_id=org, project_id=project_id)
    milestones = await KeyDateService(db).list(scope, project_id=project_id, limit=5000)
    milestones = [
        row for row in milestones
        if str(row.get("contract_id") or "primary") == str(contract_id or "primary")
    ]
    svc = KeyDateRevisionService(db)
    submissions = await svc.list_submissions(org, project_id, contract_id)
    determinations = await svc.list_determinations(org, project_id, contract_id)
    from ..services import key_date_revision_export as kx
    resource = {"organization_id": org, "project_id": project_id, "contract_id": contract_id}
    metadata = await _export_metadata(db, resource, "Complete Key Date / EOT History")
    headers, rows = kx.history_table(milestones, submissions, determinations)
    return _download_response(
        kx.render(format, metadata, headers, rows, "Complete Key Date EOT History"),
        format, "key-date-eot-history",
    )


@router.get("/key-dates/{milestone_id}", response_model=KeyDateMilestone)
async def get_milestone(
    milestone_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    return KeyDateMilestone(**await _load(milestone_id, Permissions.KEYDATE_VIEW, db, current_user, policy))


@router.put("/key-dates/{milestone_id}", response_model=KeyDateMilestone)
async def update_milestone(
    milestone_id: str,
    payload: KeyDateMilestoneUpdate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    m = await _load(milestone_id, Permissions.KEYDATE_EDIT, db, current_user, policy)
    try:
        updated = await KeyDateService(db).update(m, payload.model_dump(exclude_unset=True), current_user)
    except KeyDateError as exc:
        raise _bad_request(exc)
    return KeyDateMilestone(**(updated or m))


@router.delete("/key-dates/{milestone_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_milestone(
    milestone_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    m = await _load(milestone_id, Permissions.KEYDATE_DELETE, db, current_user, policy)
    await KeyDateService(db).delete(m, current_user)
    return None


# --- EOT ------------------------------------------------------------------


@router.post("/key-dates/{milestone_id}/eot", response_model=EOTApplication, status_code=status.HTTP_201_CREATED)
async def submit_eot(
    milestone_id: str,
    payload: EOTApplicationCreate,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    m = await _load(milestone_id, Permissions.KEYDATE_EOT_SUBMIT, db, current_user, policy)
    try:
        eot = await KeyDateService(db).submit_eot(m, payload, current_user)
    except KeyDateError as exc:
        raise _bad_request(exc)
    return EOTApplication(**eot)


@router.get("/key-dates/{milestone_id}/eots", response_model=List[EOTApplication])
async def list_eots(
    milestone_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load(milestone_id, Permissions.KEYDATE_VIEW, db, current_user, policy)
    return [EOTApplication(**e) for e in await KeyDateService(db).list_eots(milestone_id)]


@router.post("/key-dates/{milestone_id}/eot/{eot_id}/review", response_model=EOTApplication)
async def review_eot(
    milestone_id: str,
    eot_id: str,
    review: EOTReview,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    m = await _load(milestone_id, Permissions.KEYDATE_EOT_APPROVE, db, current_user, policy)
    svc = KeyDateService(db)
    eot = await db.key_date_eot_applications.find_one({"_id": eot_id})
    if not eot or str(eot.get("milestone_id")) != str(milestone_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="EOT application not found")
    try:
        updated = await svc.review_eot(m, eot, review, current_user)
    except KeyDateError as exc:
        raise _bad_request(exc)
    return EOTApplication(**(updated or eot))


@router.get("/key-dates/{milestone_id}/history", response_model=List[ExtensionHistory])
async def extension_history(
    milestone_id: str,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    await _load(milestone_id, Permissions.KEYDATE_VIEW, db, current_user, policy)
    return [ExtensionHistory(**h) for h in await KeyDateService(db).list_extension_history(milestone_id)]


# --- achievement ----------------------------------------------------------


@router.post("/key-dates/{milestone_id}/achievement", response_model=KeyDateMilestone)
async def record_achievement(
    milestone_id: str,
    rec: AchievementRecord,
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
    policy: PolicyService = Depends(get_policy),
):
    m = await _load(milestone_id, Permissions.KEYDATE_ACHIEVEMENT, db, current_user, policy)
    try:
        updated = await KeyDateService(db).record_achievement(m, rec, current_user)
    except KeyDateError as exc:
        raise _bad_request(exc)
    return KeyDateMilestone(**updated)
