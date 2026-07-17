import { Building2, FolderKanban } from "lucide-react";
import { useTenant } from "@/contexts/TenantContext";

const selectClassName =
  "min-w-0 max-w-56 truncate rounded-md border border-blue-200 bg-white px-2 py-1 text-sm font-semibold text-slate-900 shadow-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-200";

const TenantScopeBar = () => {
  const {
    organizations,
    projects,
    selectedOrganization,
    selectedProject,
    selectedOrganizationId,
    selectedProjectId,
    canSwitchOrganization,
    canSwitchProject,
    loading,
    error,
    selectOrganization,
    selectProject,
  } = useTenant();

  if (loading) {
    return (
      <div aria-label="Loading organisation and project" className="h-9 w-80 animate-pulse rounded-md bg-slate-100" />
    );
  }

  if (error) {
    return <p className="text-sm font-medium text-red-700">{error}</p>;
  }

  return (
    <div
      aria-label="Selected organisation and project"
      className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-2 rounded-lg border border-blue-100 bg-blue-50/70 px-3 py-2"
    >
      <div className="flex min-w-0 items-center gap-2">
        <Building2 aria-hidden="true" className="h-4 w-4 shrink-0 text-blue-700" />
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          Organisation
        </span>
        {canSwitchOrganization ? (
          <select
            aria-label="Select organisation"
            className={selectClassName}
            value={selectedOrganizationId}
            onChange={(event) => selectOrganization(event.target.value)}
          >
            {organizations.map((organization) => (
              <option key={organization._id} value={organization._id}>
                {organization.name}
              </option>
            ))}
          </select>
        ) : (
          <strong className="max-w-56 truncate text-sm text-slate-900" title={selectedOrganization?.name}>
            {selectedOrganization?.name || "Not selected"}
          </strong>
        )}
      </div>

      <div className="hidden h-6 w-px bg-blue-200 sm:block" aria-hidden="true" />

      <div className="flex min-w-0 items-center gap-2">
        <FolderKanban aria-hidden="true" className="h-4 w-4 shrink-0 text-blue-700" />
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          Project
        </span>
        {canSwitchProject ? (
          <select
            aria-label="Select project"
            className={selectClassName}
            value={selectedProjectId}
            onChange={(event) => selectProject(event.target.value)}
          >
            {projects.map((project) => (
              <option key={project._id} value={project._id}>
                {project.name}
              </option>
            ))}
          </select>
        ) : (
          <strong className="max-w-56 truncate text-sm text-slate-900" title={selectedProject?.name}>
            {selectedProject?.name || "Not selected"}
          </strong>
        )}
      </div>
    </div>
  );
};

export default TenantScopeBar;
