from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from ..core.database import get_db
from ..core.permissions import Permissions
from ..core.security import CurrentUser, get_current_user
from ..models.report import ReportDefinition, ReportPreview, ReportRequest
from ..services.audit_event_service import AuditEventService
from ..services.audit_export import audit_events_to_csv
from ..services.policy_service import PolicyService
from ..services.report_service import ReportService, ReportServiceError

router = APIRouter()


def get_report_service() -> ReportService:
    return ReportService()


@router.get("/audit/export")
async def export_audit_events(
    organization_id: str = Query(...),
    project_id: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    from_ts: Optional[datetime] = Query(None, alias="from"),
    to_ts: Optional[datetime] = Query(None, alias="to"),
    limit: int = Query(5000, ge=1, le=50000),
    db=Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Export tenant-scoped audit events as a CSV evidence pack.

    Requires ``dms.audit.view`` and is constrained (deny-by-default) to the
    caller's organization/project scope by PolicyService.
    """
    await PolicyService().authorize(
        current_user,
        Permissions.AUDIT_VIEW,
        resource_type="audit",
        organization_id=organization_id,
        project_id=project_id,
    )
    events = await AuditEventService(db).query_events(
        organization_id=organization_id,
        project_id=project_id,
        start=from_ts,
        end=to_ts,
        action=action,
        limit=limit,
    )
    csv_text = audit_events_to_csv(events)
    filename = f"audit-export-{organization_id}-{datetime.utcnow().strftime('%Y%m%d')}.csv"
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/reports", response_model=List[ReportDefinition])
async def list_reports(
    current_user: CurrentUser = Depends(get_current_user),
    report_service: ReportService = Depends(get_report_service),
) -> List[ReportDefinition]:
    """Return the catalog of available reports."""
    await PolicyService().authorize(
        current_user,
        "dms.report.view",
        resource_type="report",
        organization_id=getattr(current_user, "organization_id", None),
        audit=False,
    )
    try:
        return await report_service.list_available_reports()
    except ReportServiceError as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/reports/preview", response_model=ReportPreview)
async def preview_report(
    request: ReportRequest,
    current_user: CurrentUser = Depends(get_current_user),
    report_service: ReportService = Depends(get_report_service),
) -> ReportPreview:
    """Generate a preview (JSON rows and metrics) for the selected report."""
    await PolicyService().authorize(
        current_user,
        "dms.report.view",
        resource_type="report",
        resource_id=request.report_id,
        organization_id=request.organization_id or getattr(current_user, "organization_id", None),
        project_id=request.project_id,
    )
    try:
        return await report_service.generate_preview(request, current_user)
    except ReportServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # pragma: no cover - unexpected failure
        raise HTTPException(status_code=500, detail=f"Failed to build report: {exc}")


@router.post("/reports/download")
async def download_report(
    request: ReportRequest,
    current_user: CurrentUser = Depends(get_current_user),
    report_service: ReportService = Depends(get_report_service),
) -> Response:
    """Download the report as a CSV file."""
    await PolicyService().authorize(
        current_user,
        "dms.report.view",
        resource_type="report",
        resource_id=request.report_id,
        organization_id=request.organization_id or getattr(current_user, "organization_id", None),
        project_id=request.project_id,
    )
    try:
        content, filename = await report_service.generate_download(request, current_user)
        return Response(
            content=content,
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except ReportServiceError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # pragma: no cover - unexpected failure
        raise HTTPException(status_code=500, detail=f"Failed to download report: {exc}")
