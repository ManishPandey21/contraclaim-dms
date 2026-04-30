from typing import List

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from ..core.security import CurrentUser, get_current_user
from ..models.report import ReportDefinition, ReportPreview, ReportRequest
from ..services.report_service import ReportService, ReportServiceError

router = APIRouter()


def get_report_service() -> ReportService:
    return ReportService()


@router.get("/reports", response_model=List[ReportDefinition])
async def list_reports(
    current_user: CurrentUser = Depends(get_current_user),
    report_service: ReportService = Depends(get_report_service),
) -> List[ReportDefinition]:
    """Return the catalog of available reports."""
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
