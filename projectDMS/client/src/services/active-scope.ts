/**
 * The navbar selection, as the backend sees it.
 *
 * `TenantContext` owns the selection and is the only writer here; the API
 * client's request interceptor is the only reader. Every API request therefore
 * carries `X-Org-Id` / `X-Proj-Id` for the CURRENT selection, and the backend
 * (`core/tenant_context.py`) holds the Hindrance register to it: a record in
 * another project is refused 403 `context_forbidden`, and a record-level request
 * with no project selected is 400 `selection_required`.
 *
 * It is a module-level value rather than React state on purpose: `selectProject`
 * updates it synchronously, before the routed page remounts, so the first
 * request a remounted page makes already carries the new project.
 */
export type ActiveScope = { organizationId: string; projectId: string };

let current: ActiveScope = { organizationId: "", projectId: "" };

export function setActiveScope(next: Partial<ActiveScope>): void {
  current = {
    organizationId: String(next.organizationId ?? "").trim(),
    projectId: String(next.projectId ?? "").trim(),
  };
}

export function getActiveScope(): ActiveScope {
  return current;
}

/** The request headers for the current selection; empty values are omitted. */
export function activeScopeHeaders(): Record<string, string> {
  return {
    ...(current.organizationId ? { "X-Org-Id": current.organizationId } : {}),
    ...(current.projectId ? { "X-Proj-Id": current.projectId } : {}),
  };
}

/** The backend's machine-readable scope refusal code, if the error carries one. */
export function scopeErrorCode(error: unknown): "selection_required" | "context_forbidden" | null {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  const code = detail && typeof detail === "object" ? (detail as { code?: unknown }).code : undefined;
  return code === "selection_required" || code === "context_forbidden" ? code : null;
}
