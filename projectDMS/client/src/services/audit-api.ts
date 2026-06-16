import { api } from "./api";

// ---------------------------------------------------------------------------
// Audit export pack (Phase 3 / M7). Downloads a tenant-scoped CSV evidence pack
// from GET /api/audit/export. Requires the dms.audit.view permission.
// ---------------------------------------------------------------------------

export interface AuditExportParams {
  organization_id: string;
  project_id?: string;
  action?: string;
  from?: string; // ISO 8601
  to?: string; // ISO 8601
}

export async function downloadAuditExport(params: AuditExportParams): Promise<string> {
  const res = await api.get("/audit/export", { params, responseType: "blob" });

  const blob =
    res.data instanceof Blob ? res.data : new Blob([res.data], { type: "text/csv" });

  const disposition = (res.headers as Record<string, string> | undefined)?.[
    "content-disposition"
  ];
  let filename = "audit-export.csv";
  if (disposition) {
    const match = disposition.match(/filename="?([^";]+)"?/i);
    if (match && match[1]) filename = match[1];
  }

  const url = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  window.URL.revokeObjectURL(url);
  return filename;
}
