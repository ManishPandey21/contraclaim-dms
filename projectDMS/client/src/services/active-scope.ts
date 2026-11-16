/**
 * The navbar selection, as the backend sees it.
 *
 * `TenantContext` owns the selection and is the only writer here; the API
 * client's request interceptor is the only reader. Every API request therefore
 * carries `X-Org-Id` / `X-Proj-Id` for the CURRENT selection, and the backend
 * (`core/tenant_context.py`) holds every scoped register to it (Hindrance,
 * Variation, Programme, Chronology and, since CL-4A, the core DMS modules): a
 * record in another project is refused 403 `context_forbidden`, and a
 * record-level request with no project selected is 400 `selection_required`.
 *
 * It is a module-level value rather than React state on purpose: `selectProject`
 * updates it synchronously, before the routed page remounts, so the first
 * request a remounted page makes already carries the new project.
 */
export type ActiveScope = { organizationId: string; projectId: string };

/**
 * The explicit "All Organisations" / "All Projects" value (Super Admin only,
 * owner policy 2026-10-05). It is sent as-is in `X-Org-Id` / `X-Proj-Id`, so the
 * backend never has to infer ALL from a missing header; any other role sending
 * it is refused 403. All Organisations always means All Projects too.
 */
export const ALL_SELECTION = "__all__";

function normalise(next: Partial<ActiveScope>): ActiveScope {
  const organizationId = String(next.organizationId ?? "").trim();
  const projectId = String(next.projectId ?? "").trim();
  return organizationId === ALL_SELECTION
    ? { organizationId, projectId: ALL_SELECTION }
    : { organizationId, projectId };
}

let current: ActiveScope = { organizationId: "", projectId: "" };

export function setActiveScope(next: Partial<ActiveScope>): void {
  current = normalise(next);
}

/** The persisted form of a selection, ALL included. Versioned for later changes. */
export function serializeSelection(scope: Partial<ActiveScope>): string {
  const { organizationId, projectId } = normalise(scope);
  return JSON.stringify({ v: 1, organizationId, projectId });
}

/** The inverse of `serializeSelection`; `null` for anything it did not write. */
export function parseSelection(raw: string | null | undefined): ActiveScope | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as { v?: unknown; organizationId?: unknown; projectId?: unknown };
    if (parsed?.v !== 1 || typeof parsed.organizationId !== "string" || typeof parsed.projectId !== "string") {
      return null;
    }
    return normalise({ organizationId: parsed.organizationId, projectId: parsed.projectId });
  } catch {
    return null;
  }
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

/** A user-facing sentence for a scope refusal, or `null` for any other error. */
export function scopeRefusalMessage(error: unknown, noun = "record"): string | null {
  const code = scopeErrorCode(error);
  if (code === "selection_required") return `Select a project in the navbar to open or change this ${noun}.`;
  if (code === "context_forbidden") return `This ${noun} is not in the project selected in the navbar.`;
  return null;
}
