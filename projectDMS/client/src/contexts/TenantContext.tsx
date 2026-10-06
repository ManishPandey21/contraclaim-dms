import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import { normalizeRoleId } from "@/config/rolePermissions";
import {
  listOrganizations,
  type Organization,
} from "@/services/organizations-api";
import { listProjects, type Project } from "@/services/projects-api";
import { getCurrentUserProfile } from "@/services/session-api";
import {
  ALL_SELECTION,
  parseSelection,
  serializeSelection,
  setActiveScope,
} from "@/services/active-scope";

const ORGANIZATION_STORAGE_KEY = "org_id";
const PROJECT_STORAGE_KEY = "proj_id";
/** The selection of a role on the ALL model, ALL included (`serializeSelection`). */
export const SELECTION_STORAGE_KEY = "tenant_selection";

type TenantContextValue = {
  organizations: Organization[];
  projects: Project[];
  selectedOrganization: Organization | null;
  selectedProject: Project | null;
  /** A real organisation id, or "" when none is selected or ALL is. */
  selectedOrganizationId: string;
  /** A real project id, or "" when none is selected or ALL is. */
  selectedProjectId: string;
  /** Super Admin's explicit "All Organisations" (implies all projects). */
  allOrganizations: boolean;
  /**
   * Explicit "All Projects" - contextual: every project of the selected
   * organisation the user may see (Super Admin, organisation admin/user).
   */
  allProjects: boolean;
  /** Whether "All Projects" is offered: Super Admin and organisation-tier roles. */
  canSelectAllProjects: boolean;
  canSwitchOrganization: boolean;
  canSwitchProject: boolean;
  loading: boolean;
  error: string | null;
  /** An organisation id, or `ALL_SELECTION` (Super Admin only). */
  selectOrganization: (organizationId: string) => void;
  /** A project id, or `ALL_SELECTION` (Super Admin and organisation-tier roles). */
  selectProject: (projectId: string) => void;
};

const TenantContext = createContext<TenantContextValue | null>(null);

function roleList(value: string[] | string | undefined): string[] {
  const values = Array.isArray(value) ? value : String(value || "").split(",");
  return values.map((role) => normalizeRoleId(String(role))).filter(Boolean);
}

function storedValue(key: string): string {
  return typeof window === "undefined" ? "" : window.localStorage.getItem(key) || "";
}

function realId(value: string): string {
  return value === ALL_SELECTION ? "" : value;
}

const ORGANIZATION_TIER_ROLES = ["orgadmin", "orguser"];

/**
 * Persist a selection. A role on the ALL model writes `tenant_selection` with
 * ALL kept explicit; the legacy `org_id` / `proj_id` keys, which many pages
 * read as filters, only ever hold real ids, so ALL removes them rather than
 * leaking the sentinel into a query string.
 */
function persistSelection(usesAllModel: boolean, organizationId: string, projectId: string): void {
  if (usesAllModel) {
    window.localStorage.setItem(
      SELECTION_STORAGE_KEY,
      serializeSelection({ organizationId, projectId }),
    );
  }
  const organization = realId(organizationId);
  const project = realId(projectId);
  if (organization) {
    window.localStorage.setItem(ORGANIZATION_STORAGE_KEY, organization);
  } else {
    window.localStorage.removeItem(ORGANIZATION_STORAGE_KEY);
  }
  if (project) {
    window.localStorage.setItem(PROJECT_STORAGE_KEY, project);
  } else {
    window.localStorage.removeItem(PROJECT_STORAGE_KEY);
  }
}

// Until the tenant scope loads, requests carry the persisted selection; the
// backend validates it. TenantContext is the only writer of the active scope.
setActiveScope(
  parseSelection(storedValue(SELECTION_STORAGE_KEY)) ?? {
    organizationId: storedValue(ORGANIZATION_STORAGE_KEY),
    projectId: storedValue(PROJECT_STORAGE_KEY),
  },
);

export function TenantProvider({ children }: React.PropsWithChildren) {
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [roles, setRoles] = useState<string[]>([]);
  // Raw selection: a real id, "" (nothing), or ALL_SELECTION (Super Admin).
  const [selectedOrganizationId, setSelectedOrganizationId] = useState("");
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const isSystemAdmin = roles.includes("superadmin");
  // Organisation-tier roles default to their organisation + All Projects
  // (owner policy 2026-10-06). The organisation comes from the profile, never
  // from stored state, and they never get All Organisations.
  const isOrganizationTier =
    !isSystemAdmin && roles.some((role) => ORGANIZATION_TIER_ROLES.includes(role));
  const usesAllModel = isSystemAdmin || isOrganizationTier;

  useEffect(() => {
    let active = true;

    async function loadTenantScope() {
      setLoading(true);
      setError(null);
      try {
        const [profile, availableOrganizations, availableProjects] = await Promise.all([
          getCurrentUserProfile(),
          listOrganizations(),
          listProjects(),
        ]);
        if (!active) return;

        const nextRoles = roleList(profile.roles);
        const profileOrganizationId = String(profile.organization_id || "");
        const persistedOrganizationId = storedValue(ORGANIZATION_STORAGE_KEY);
        const persistedProjectId = storedValue(PROJECT_STORAGE_KEY);

        const organizationIds = new Set(
          availableOrganizations.map((organization) => String(organization._id)),
        );
        const projectById = new Map(
          availableProjects.map((project) => [String(project._id), project]),
        );

        let organizationId: string;
        let projectId: string;

        if (nextRoles.includes("superadmin")) {
          // Default All Organisations + All Projects. A persisted selection is
          // restored only while it is still valid: an organisation that no
          // longer exists falls back to ALL, and a project that is not in the
          // restored organisation (stale state) falls back to All Projects.
          const stored = parseSelection(storedValue(SELECTION_STORAGE_KEY));
          organizationId =
            stored && organizationIds.has(stored.organizationId)
              ? stored.organizationId
              : ALL_SELECTION;
          const storedProject = stored ? projectById.get(stored.projectId) : undefined;
          projectId =
            organizationId !== ALL_SELECTION &&
            storedProject &&
            String(storedProject.organization_id) === organizationId
              ? String(storedProject._id)
              : ALL_SELECTION;
        } else if (nextRoles.some((role) => ORGANIZATION_TIER_ROLES.includes(role))) {
          // The authenticated organisation is the only one: a stored selection
          // can choose a project inside it, or All Projects, and nothing else.
          // Anything else stored (another organisation's project, a Super Admin
          // ALL left in this browser, the legacy keys) falls back to All Projects.
          organizationId =
            (organizationIds.has(profileOrganizationId) ? profileOrganizationId : "") ||
            String(availableOrganizations[0]?._id || "");
          const stored = parseSelection(storedValue(SELECTION_STORAGE_KEY));
          const storedProject = stored ? projectById.get(stored.projectId) : undefined;
          projectId =
            organizationId &&
            stored?.organizationId === organizationId &&
            storedProject &&
            String(storedProject.organization_id) === organizationId
              ? String(storedProject._id)
              : organizationId
                ? ALL_SELECTION
                : "";
        } else {
          organizationId =
            (organizationIds.has(profileOrganizationId) ? profileOrganizationId : "") ||
            (organizationIds.has(persistedOrganizationId) ? persistedOrganizationId : "") ||
            String(availableOrganizations[0]?._id || "");

          const projectsInOrganization = availableProjects.filter(
            (project) => String(project.organization_id) === organizationId,
          );
          const assignedProjectIds = new Set(
            (profile.projects || []).map((assigned) => String(assigned)),
          );
          const persistedProject = projectById.get(persistedProjectId);
          const persistedProjectIsValid =
            persistedProject && String(persistedProject.organization_id) === organizationId;
          const assignedProject = projectsInOrganization.find((project) =>
            assignedProjectIds.has(String(project._id)),
          );
          projectId = persistedProjectIsValid
            ? persistedProjectId
            : String(assignedProject?._id || projectsInOrganization[0]?._id || "");
        }

        setRoles(nextRoles);
        setOrganizations(availableOrganizations);
        setProjects(availableProjects);
        setActiveScope({ organizationId, projectId });
        setSelectedOrganizationId(organizationId);
        setSelectedProjectId(projectId);
      } catch {
        if (active) {
          setError("Organisation and project details could not be loaded.");
        }
      } finally {
        if (active) setLoading(false);
      }
    }

    void loadTenantScope();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!selectedOrganizationId) return;
    persistSelection(usesAllModel, selectedOrganizationId, selectedProjectId);
  }, [usesAllModel, selectedOrganizationId, selectedProjectId]);

  const projectsForSelectedOrganization = useMemo(
    () =>
      projects.filter(
        (project) =>
          String(project.organization_id) === String(selectedOrganizationId),
      ),
    [projects, selectedOrganizationId],
  );

  const allOrganizations = isSystemAdmin && selectedOrganizationId === ALL_SELECTION;
  const allProjects = usesAllModel && selectedProjectId === ALL_SELECTION;

  // On the ALL model there is always ALL to choose, so a single organisation or
  // project is still a choice; a project choice needs an organisation first.
  // Organisation-tier roles never switch organisation.
  const canSwitchOrganization = isSystemAdmin && organizations.length > 0;
  const canSwitchProject = usesAllModel
    ? !allOrganizations && projectsForSelectedOrganization.length > 0
    : false;

  const selectOrganization = useCallback(
    (organizationId: string) => {
      if (!canSwitchOrganization || organizationId === selectedOrganizationId) return;
      if (
        organizationId !== ALL_SELECTION &&
        !organizations.some((organization) => String(organization._id) === organizationId)
      ) {
        return;
      }
      // A new organisation always starts at All Projects: a project from the
      // previous organisation must never stay active.
      const projectId = ALL_SELECTION;
      persistSelection(true, organizationId, projectId);
      // Synchronously, before the routed page remounts and fetches.
      setActiveScope({ organizationId, projectId });
      setSelectedOrganizationId(organizationId);
      setSelectedProjectId(projectId);
    },
    [canSwitchOrganization, organizations, selectedOrganizationId],
  );

  const selectProject = useCallback(
    (projectId: string) => {
      if (!canSwitchProject || projectId === selectedProjectId) return;
      const isAll = projectId === ALL_SELECTION;
      if (isAll && !usesAllModel) return;
      if (
        !isAll &&
        !projectsForSelectedOrganization.some(
          (candidate) => String(candidate._id) === projectId,
        )
      ) {
        return;
      }
      // Persist before changing React state. MainLayout remounts the routed page
      // on the state change, and many existing pages initialize their filters
      // synchronously from these storage keys.
      persistSelection(usesAllModel, selectedOrganizationId, projectId);
      // Synchronously, before the routed page remounts and fetches.
      setActiveScope({ organizationId: selectedOrganizationId, projectId });
      setSelectedProjectId(projectId);
      window.dispatchEvent(
        new CustomEvent("tenant-context-changed", {
          detail: {
            organizationId: realId(selectedOrganizationId),
            projectId: realId(projectId),
          },
        }),
      );
    },
    [
      canSwitchProject,
      usesAllModel,
      projectsForSelectedOrganization,
      selectedOrganizationId,
      selectedProjectId,
    ],
  );

  const value = useMemo<TenantContextValue>(
    () => ({
      organizations,
      projects: projectsForSelectedOrganization,
      selectedOrganization:
        organizations.find(
          (organization) => String(organization._id) === selectedOrganizationId,
        ) || null,
      selectedProject:
        projectsForSelectedOrganization.find(
          (project) => String(project._id) === selectedProjectId,
        ) || null,
      selectedOrganizationId: realId(selectedOrganizationId),
      selectedProjectId: realId(selectedProjectId),
      allOrganizations,
      allProjects,
      canSelectAllProjects: usesAllModel,
      canSwitchOrganization,
      canSwitchProject,
      loading,
      error,
      selectOrganization,
      selectProject,
    }),
    [
      allOrganizations,
      allProjects,
      canSwitchOrganization,
      canSwitchProject,
      error,
      loading,
      organizations,
      projectsForSelectedOrganization,
      selectOrganization,
      selectProject,
      selectedOrganizationId,
      selectedProjectId,
      usesAllModel,
    ],
  );

  return <TenantContext.Provider value={value}>{children}</TenantContext.Provider>;
}

/** The navbar selection, or `null` outside `TenantProvider` (isolated page tests). */
export function useOptionalTenant(): TenantContextValue | null {
  return useContext(TenantContext);
}

export function useTenant(): TenantContextValue {
  const context = useContext(TenantContext);
  if (!context) throw new Error("useTenant must be used within TenantProvider");
  return context;
}
