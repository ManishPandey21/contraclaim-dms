import { Building2, FolderKanban } from "lucide-react";
import type { PropsWithChildren } from "react";
import { useTenant } from "@/contexts/TenantContext";

/**
 * Blocks project-scoped pages until a complete Organisation + Project context
 * exists.
 *
 * This is not cosmetic. Rendering children before `contextReady` is what lets
 * a page fire a request with the previous project's id, or briefly paint the
 * previous project's rows after an organisation change. The gate holds the
 * page until the context is both complete and revalidated.
 */
const TenantContextGate = ({ children }: PropsWithChildren) => {
  const {
    contextReady,
    requiresSelection,
    loading,
    error,
    roleTier,
    selectedOrganizationId,
    projects,
  } = useTenant();

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
      <div role="alert" className="mx-auto max-w-lg rounded-lg border border-red-200 bg-red-50 p-6 text-center">
        <p className="text-sm font-semibold text-red-800">{error}</p>
      </div>
    );
  }

  if (contextReady) return <>{children}</>;

  // Reachable only when a selection is genuinely outstanding.
  if (!requiresSelection) return <>{children}</>;

  const needsOrganisation = !selectedOrganizationId;
  const noProjects = Boolean(selectedOrganizationId) && projects.length === 0;

  if (noProjects) {
    return (
      <div
        role="status"
        className="mx-auto max-w-lg rounded-lg border border-amber-200 bg-amber-50 p-8 text-center"
      >
        <FolderKanban aria-hidden="true" className="mx-auto h-8 w-8 text-amber-600" />
        <h2 className="mt-3 text-base font-semibold text-amber-900">No project available</h2>
        <p className="mt-2 text-sm text-amber-800">
          {roleTier === "project"
            ? "No active project is assigned to your account. Please contact your administrator."
            : "This Organisation has no active projects you can access."}
        </p>
      </div>
    );
  }

  return (
    <div
      role="status"
      aria-live="polite"
      className="mx-auto max-w-lg rounded-lg border border-blue-200 bg-blue-50 p-8 text-center"
    >
      <Building2 aria-hidden="true" className="mx-auto h-8 w-8 text-blue-600" />
      <h2 className="mt-3 text-base font-semibold text-slate-900">
        Please select an Organisation and Project to continue.
      </h2>
      <p className="mt-2 text-sm text-slate-600">
        {needsOrganisation
          ? "Choose an Organisation in the header, then choose a Project."
          : "Choose a Project in the header to load this page."}
      </p>
      <button
        type="button"
        className="mt-4 rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-blue-300"
        onClick={() => {
          const label = needsOrganisation ? "Select organisation" : "Select project";
          const selector = document.querySelector<HTMLSelectElement>(
            `select[aria-label="${label}"]`,
          );
          selector?.focus();
        }}
      >
        {needsOrganisation ? "Select Organisation" : "Select Project"}
      </button>
    </div>
  );
};

export default TenantContextGate;
