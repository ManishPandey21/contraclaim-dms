import { Building2, FolderKanban, Lock } from "lucide-react";
import { useTenant } from "@/contexts/TenantContext";

const selectClassName =
  "min-w-0 max-w-56 truncate rounded-md border border-blue-200 bg-white px-2 py-1 text-sm font-semibold text-slate-900 shadow-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-200 disabled:cursor-not-allowed disabled:bg-slate-100 disabled:text-slate-500";

const lockedClassName =
  "flex max-w-56 items-center gap-1 truncate rounded-md bg-slate-100 px-2 py-1 text-sm font-semibold text-slate-700";

type LockedValueProps = { label: string; value: string | undefined; emptyText: string };

/** Read-only presentation for a value the user's role fixes. */
const LockedValue = ({ label, value, emptyText }: LockedValueProps) => (
  <span aria-label={label} className={lockedClassName} title={value || emptyText}>
    <Lock aria-hidden="true" className="h-3 w-3 shrink-0 text-slate-400" />
    <span className="truncate">{value || emptyText}</span>
  </span>
);

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
    roleTier,
    loading,
    error,
    selectOrganization,
    selectProject,
  } = useTenant();

  if (loading) {
    return (
      <div
        aria-label="Loading organisation and project"
        role="status"
        className="h-9 w-80 animate-pulse rounded-md bg-slate-100"
      />
    );
  }

  if (error) {
    return (
      <p role="alert" className="text-sm font-medium text-red-700">
        {error}
      </p>
    );
  }

  const projectDisabled = !canSwitchProject || !selectedOrganizationId;
  const noProjectsAvailable = Boolean(selectedOrganizationId) && projects.length === 0;

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
            <option value="">Select an Organisation</option>
            {organizations.map((organization) => (
              <option key={organization._id} value={organization._id}>
                {organization.name}
              </option>
            ))}
          </select>
        ) : (
          <LockedValue
            label="Organisation"
            value={selectedOrganization?.name}
            emptyText={roleTier === "global" ? "Select an Organisation" : "Not assigned"}
          />
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
            disabled={projectDisabled}
            value={selectedProjectId}
            onChange={(event) => selectProject(event.target.value)}
          >
            <option value="">Select a Project</option>
            {projects.map((project) => (
              <option key={project._id} value={project._id}>
                {project.name}
              </option>
            ))}
          </select>
        ) : !selectedOrganizationId ? (
          // Project stays disabled until an organisation is chosen.
          <select aria-label="Select project" className={selectClassName} disabled value="">
            <option value="">Select an Organisation first</option>
          </select>
        ) : noProjectsAvailable ? (
          <span className="text-sm font-medium text-amber-700">No project available</span>
        ) : (
          <LockedValue
            label="Project"
            value={selectedProject?.name}
            emptyText="Select a Project"
          />
        )}
      </div>
    </div>
  );
};

export default TenantScopeBar;
