import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { normalizeRoleId } from "@/config/rolePermissions";
import {
  listOrganizations,
  type Organization,
} from "@/services/organizations-api";
import { listProjects, type Project } from "@/services/projects-api";
import { getCurrentUserProfile } from "@/services/session-api";

const ORGANIZATION_STORAGE_KEY = "org_id";
const PROJECT_STORAGE_KEY = "proj_id";

/** Roles that may choose an organisation. */
const GLOBAL_ROLES = ["superadmin", "superuser"];
/** Roles pinned to one organisation but free to choose a project within it. */
const ORG_ROLES = ["orgadmin", "orguser"];
/** Roles pinned to both an organisation and their assigned project(s). */
const PROJECT_ROLES = ["projectadmin", "projectuser"];

export type RoleTier = "global" | "org" | "project" | "restricted";

type TenantContextValue = {
  organizations: Organization[];
  projects: Project[];
  selectedOrganization: Organization | null;
  selectedProject: Project | null;
  selectedOrganizationId: string;
  selectedProjectId: string;
  roleTier: RoleTier;
  /** True when the selector should render as an editable dropdown. */
  canSwitchOrganization: boolean;
  canSwitchProject: boolean;
  /** True when the value is fixed by the user's role and shown read-only. */
  organizationLocked: boolean;
  projectLocked: boolean;
  /**
   * True only when the account has no reachable scope at all (a project-role
   * account with no active assigned project, or a non-global account with no
   * organisation). A *partial* selection is not a blocked state: it means
   * consolidated data, not missing data.
   */
  hasNoAccessibleScope: boolean;
  /** True once a scope can be resolved. Gate data loads on this. */
  contextReady: boolean;
  loading: boolean;
  error: string | null;
  selectOrganization: (organizationId: string) => void;
  selectProject: (projectId: string) => void;
  refresh: () => Promise<void>;
};

const TenantContext = createContext<TenantContextValue | null>(null);

function roleList(value: string[] | string | undefined): string[] {
  const values = Array.isArray(value) ? value : String(value || "").split(",");
  return values.map((role) => normalizeRoleId(String(role))).filter(Boolean);
}

export function resolveRoleTier(roles: string[]): RoleTier {
  if (roles.some((role) => GLOBAL_ROLES.includes(role))) return "global";
  if (roles.some((role) => ORG_ROLES.includes(role))) return "org";
  if (roles.some((role) => PROJECT_ROLES.includes(role))) return "project";
  return "restricted";
}

function storedValue(key: string): string {
  return typeof window === "undefined" ? "" : window.localStorage.getItem(key) || "";
}

function writeStorage(organizationId: string, projectId: string) {
  if (typeof window === "undefined") return;
  if (organizationId) {
    window.localStorage.setItem(ORGANIZATION_STORAGE_KEY, organizationId);
  } else {
    window.localStorage.removeItem(ORGANIZATION_STORAGE_KEY);
  }
  // Legacy pages read proj_id straight from storage. Removing it here is what
  // stops them rendering the previous project's rows after an organisation
  // change, so it must happen before the change is announced.
  if (projectId) {
    window.localStorage.setItem(PROJECT_STORAGE_KEY, projectId);
  } else {
    window.localStorage.removeItem(PROJECT_STORAGE_KEY);
  }
}

function announce(organizationId: string, projectId: string) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent("tenant-context-changed", {
      detail: { organizationId, projectId },
    }),
  );
}

function isActive(record: { is_active?: boolean; status?: string } | undefined): boolean {
  if (!record) return false;
  if (record.is_active === false) return false;
  // Projects historically carried a display status instead of a flag.
  if (record.status && ["Completed", "On Hold", "Inactive"].includes(record.status)) return false;
  return true;
}

export function TenantProvider({ children }: React.PropsWithChildren) {
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [roles, setRoles] = useState<string[]>([]);
  const [assignedProjectIds, setAssignedProjectIds] = useState<Set<string>>(new Set());
  const [selectedOrganizationId, setSelectedOrganizationId] = useState("");
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const activeRef = useRef(true);

  const loadTenantScope = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [profile, availableOrganizations, availableProjects] = await Promise.all([
        getCurrentUserProfile({ force: true }),
        listOrganizations(),
        listProjects(),
      ]);
      if (!activeRef.current) return;

      const nextRoles = roleList(profile.roles);
      const tier = resolveRoleTier(nextRoles);
      const profileOrganizationId = String(profile.organization_id || "");
      const assigned = new Set((profile.projects || []).map((id) => String(id)));

      const activeOrganizations = availableOrganizations.filter((organization) =>
        isActive(organization as never),
      );
      const activeProjects = availableProjects.filter((project) => isActive(project));
      const organizationIds = new Set(
        activeOrganizations.map((organization) => String(organization._id)),
      );

      // Revalidate anything persisted from a previous session against what the
      // user can reach *now* -- roles, memberships and project status all move.
      const persistedOrganizationId = storedValue(ORGANIZATION_STORAGE_KEY);
      const persistedProjectId = storedValue(PROJECT_STORAGE_KEY);

      let organizationId = "";
      if (tier === "org" || tier === "project") {
        // Server pins these tiers; the browser gets no say.
        organizationId = organizationIds.has(profileOrganizationId) ? profileOrganizationId : "";
      } else if (tier === "global") {
        if (organizationIds.has(persistedOrganizationId)) {
          organizationId = persistedOrganizationId;
        } else if (activeOrganizations.length === 1) {
          // Only one reachable organisation is not a choice.
          organizationId = String(activeOrganizations[0]._id);
        }
      }

      const selectable = activeProjects.filter((project) => {
        if (String(project.organization_id) !== organizationId) return false;
        if (tier === "project") return assigned.has(String(project._id));
        return true;
      });

      let projectId = "";
      if (organizationId) {
        const persistedIsSelectable = selectable.some(
          (project) => String(project._id) === persistedProjectId,
        );
        if (persistedIsSelectable) {
          projectId = persistedProjectId;
        } else if (selectable.length === 1) {
          // Exactly one option is not a choice either -- auto-select it.
          projectId = String(selectable[0]._id);
        }
        // More than one option and nothing valid persisted: the user must choose.
      }

      writeStorage(organizationId, projectId);
      setRoles(nextRoles);
      setAssignedProjectIds(assigned);
      setOrganizations(activeOrganizations);
      setProjects(activeProjects);
      setSelectedOrganizationId(organizationId);
      setSelectedProjectId(projectId);
      announce(organizationId, projectId);
    } catch {
      if (activeRef.current) {
        setError("Organisation and project details could not be loaded.");
      }
    } finally {
      if (activeRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    activeRef.current = true;
    void loadTenantScope();
    return () => {
      activeRef.current = false;
    };
  }, [loadTenantScope]);

  const roleTier = useMemo(() => resolveRoleTier(roles), [roles]);

  const selectableProjects = useMemo(
    () =>
      projects.filter((project) => {
        if (String(project.organization_id) !== String(selectedOrganizationId)) return false;
        if (roleTier === "project") return assignedProjectIds.has(String(project._id));
        return true;
      }),
    [assignedProjectIds, projects, roleTier, selectedOrganizationId],
  );

  // A single option is never a choice, so it renders read-only. A project-tier
  // user with several assignments still needs to pick between them -- the list
  // is already narrowed to their own assignments, so this cannot reach another
  // project, and locking them with nothing selected would dead-end the account.
  const canSwitchOrganization = roleTier === "global" && organizations.length > 1;
  const canSwitchProject = selectableProjects.length > 1;
  const organizationLocked = !canSwitchOrganization;
  const projectLocked = !canSwitchProject;

  const selectOrganization = useCallback(
    (organizationId: string) => {
      if (!canSwitchOrganization || organizationId === selectedOrganizationId) return;
      // Changing *or clearing* the organisation always drops the project, so the
      // scope widens back to "all organisations" rather than stranding a project
      // from the organisation just left. Storage is written first because legacy
      // pages read proj_id directly, and must not see the outgoing project.
      writeStorage(organizationId, "");
      setSelectedOrganizationId(organizationId);
      setSelectedProjectId("");
      announce(organizationId, "");
    },
    [canSwitchOrganization, selectedOrganizationId],
  );

  const selectProject = useCallback(
    (projectId: string) => {
      if (projectId === selectedProjectId) return;
      // An empty id clears the project and returns to consolidated data for the
      // current organisation. Anything else must be a project the user can
      // actually reach.
      if (projectId) {
        const project = selectableProjects.find(
          (candidate) => String(candidate._id) === projectId,
        );
        if (!project) return;
      }
      writeStorage(selectedOrganizationId, projectId);
      setSelectedProjectId(projectId);
      announce(selectedOrganizationId, projectId);
    },
    [selectableProjects, selectedOrganizationId, selectedProjectId],
  );

  // A partial selection is a legitimate, broader scope -- not a blocked state.
  // Only an account with nowhere to look at all is blocked: a project-role
  // account with no active assigned project, or a non-global account with no
  // organisation.
  const hasNoAccessibleScope =
    !loading &&
    !error &&
    ((roleTier === "project" && selectableProjects.length === 0) ||
      (roleTier !== "global" && !selectedOrganizationId));
  const contextReady = !loading && !error && !hasNoAccessibleScope;

  const value = useMemo<TenantContextValue>(
    () => ({
      organizations,
      projects: selectableProjects,
      selectedOrganization:
        organizations.find(
          (organization) => String(organization._id) === selectedOrganizationId,
        ) || null,
      selectedProject:
        selectableProjects.find((project) => String(project._id) === selectedProjectId) || null,
      selectedOrganizationId,
      selectedProjectId,
      roleTier,
      canSwitchOrganization,
      canSwitchProject,
      organizationLocked,
      projectLocked,
      hasNoAccessibleScope,
      contextReady,
      loading,
      error,
      selectOrganization,
      selectProject,
      refresh: loadTenantScope,
    }),
    [
      canSwitchOrganization,
      canSwitchProject,
      contextReady,
      error,
      loadTenantScope,
      loading,
      organizationLocked,
      organizations,
      projectLocked,
      hasNoAccessibleScope,
      roleTier,
      selectOrganization,
      selectProject,
      selectableProjects,
      selectedOrganizationId,
      selectedProjectId,
    ],
  );

  return <TenantContext.Provider value={value}>{children}</TenantContext.Provider>;
}

export function useTenant(): TenantContextValue {
  const context = useContext(TenantContext);
  if (!context) throw new Error("useTenant must be used within TenantProvider");
  return context;
}
