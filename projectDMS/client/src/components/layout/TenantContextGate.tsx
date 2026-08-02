import { FolderKanban } from "lucide-react";
import type { PropsWithChildren } from "react";
import { useTenant } from "@/contexts/TenantContext";

/**
 * Guards project-scoped pages while the tenant scope is being resolved.
 *
 * Under the progressive-narrowing model a partial selection is a legitimate,
 * broader scope rather than a missing one: an Organisation User with no project
 * selected sees consolidated data across their organisation, and a Super Admin
 * with nothing selected sees consolidated data across all organisations. So
 * this gate deliberately does *not* block on an absent selection.
 *
 * It blocks only where there is genuinely nothing to show: while the scope is
 * still loading (rendering children early is what lets a page fire a request
 * with the previous context), when the scope failed to load, and when the
 * account has no reachable scope at all.
 */
const TenantContextGate = ({ children }: PropsWithChildren) => {
  const { contextReady, hasNoAccessibleScope, loading, error, roleTier } = useTenant();

  if (loading) {
    return (
      <div
        role="status"
        aria-label="Loading organisation and project"
        className="flex min-h-64 items-center justify-center"
      >
        <div className="h-8 w-64 animate-pulse rounded-md bg-slate-100" />
      </div>
    );
  }

  if (error) {
    return (
      <div
        role="alert"
        className="mx-auto max-w-lg rounded-lg border border-red-200 bg-red-50 p-6 text-center"
      >
        <p className="text-sm font-semibold text-red-800">{error}</p>
      </div>
    );
  }

  if (hasNoAccessibleScope) {
    return (
      <div
        role="status"
        className="mx-auto max-w-lg rounded-lg border border-amber-200 bg-amber-50 p-8 text-center"
      >
        <FolderKanban aria-hidden="true" className="mx-auto h-8 w-8 text-amber-600" />
        <h2 className="mt-3 text-base font-semibold text-amber-900">No accessible data</h2>
        <p className="mt-2 text-sm text-amber-800">
          {roleTier === "project"
            ? "No active project is assigned to your account. Please contact your administrator."
            : "No active organisation is assigned to your account. Please contact your administrator."}
        </p>
      </div>
    );
  }

  return contextReady ? <>{children}</> : null;
};

export default TenantContextGate;
