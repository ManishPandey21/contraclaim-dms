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

const ORGANIZATION_STORAGE_KEY = "org_id";
const PROJECT_STORAGE_KEY = "proj_id";

type TenantContextValue = {
  organizations: Organization[];
  projects: Project[];
  selectedOrganization: Organization | null;
  selectedProject: Project | null;
  selectedOrganizationId: string;
  selectedProjectId: string;
  canSwitchOrganization: boolean;
  canSwitchProject: boolean;
  loading: boolean;
  error: string | null;
  selectOrganization: (organizationId: string) => void;
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

export function TenantProvider({ children }: React.PropsWithChildren) {
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [roles, setRoles] = useState<string[]>([]);
  const [selectedOrganizationId, setSelectedOrganizationId] = useState("");
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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
        const isSystemAdmin = nextRoles.includes("superadmin");

        const organizationIds = new Set(
          availableOrganizations.map((organization) => String(organization._id)),
        );
        const projectById = new Map(
          availableProjects.map((project) => [String(project._id), project]),
        );

        const organizationId =
          (isSystemAdmin && organizationIds.has(persistedOrganizationId)
            ? persistedOrganizationId
            : "") ||
          (organizationIds.has(profileOrganizationId) ? profileOrganizationId : "") ||
          (organizationIds.has(persistedOrganizationId) ? persistedOrganizationId : "") ||
          String(availableOrganizations[0]?._id || "");

        const projectsInOrganization = availableProjects.filter(
          (project) => String(project.organization_id) === organizationId,
        );
        const assignedProjectIds = new Set(
          (profile.projects || []).map((projectId) => String(projectId)),
        );
        const persistedProject = projectById.get(persistedProjectId);
        const persistedProjectIsValid =
          persistedProject && String(persistedProject.organization_id) === organizationId;
        const assignedProject = projectsInOrganization.find((project) =>
          assignedProjectIds.has(String(project._id)),
        );
        const projectId = persistedProjectIsValid
          ? persistedProjectId
          : String(assignedProject?._id || projectsInOrganization[0]?._id || "");

        setRoles(nextRoles);
        setOrganizations(availableOrganizations);
        setProjects(availableProjects);
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
    window.localStorage.setItem(ORGANIZATION_STORAGE_KEY, selectedOrganizationId);
    if (selectedProjectId) {
      window.localStorage.setItem(PROJECT_STORAGE_KEY, selectedProjectId);
    } else {
      window.localStorage.removeItem(PROJECT_STORAGE_KEY);
    }
  }, [selectedOrganizationId, selectedProjectId]);

  const projectsForSelectedOrganization = useMemo(
    () =>
      projects.filter(
        (project) =>
          String(project.organization_id) === String(selectedOrganizationId),
      ),
    [projects, selectedOrganizationId],
  );

  const canSwitchOrganization = roles.includes("superadmin") && organizations.length > 1;
  const canSwitchProject =
    roles.some((role) => ["superadmin", "orgadmin", "orguser"].includes(role)) &&
    projectsForSelectedOrganization.length > 1;

  const selectOrganization = useCallback(
    (organizationId: string) => {
      if (!canSwitchOrganization || organizationId === selectedOrganizationId) return;
      const firstProject = projects.find(
        (project) => String(project.organization_id) === organizationId,
      );
      const firstProjectId = String(firstProject?._id || "");
      window.localStorage.setItem(ORGANIZATION_STORAGE_KEY, organizationId);
      if (firstProjectId) {
        window.localStorage.setItem(PROJECT_STORAGE_KEY, firstProjectId);
      } else {
        window.localStorage.removeItem(PROJECT_STORAGE_KEY);
      }
      setSelectedOrganizationId(organizationId);
      setSelectedProjectId(firstProjectId);
    },
    [canSwitchOrganization, projects, selectedOrganizationId],
  );

  const selectProject = useCallback(
    (projectId: string) => {
      if (!canSwitchProject || projectId === selectedProjectId) return;
      const project = projectsForSelectedOrganization.find(
        (candidate) => String(candidate._id) === projectId,
      );
      if (!project) return;
      // Persist before changing React state. MainLayout remounts the routed page
      // on the state change, and many existing pages initialize their filters
      // synchronously from these storage keys.
      window.localStorage.setItem(ORGANIZATION_STORAGE_KEY, selectedOrganizationId);
      window.localStorage.setItem(PROJECT_STORAGE_KEY, projectId);
      setSelectedProjectId(projectId);
      window.dispatchEvent(
        new CustomEvent("tenant-context-changed", {
          detail: { organizationId: selectedOrganizationId, projectId },
        }),
      );
    },
    [
      canSwitchProject,
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
      selectedOrganizationId,
      selectedProjectId,
      canSwitchOrganization,
      canSwitchProject,
      loading,
      error,
      selectOrganization,
      selectProject,
    }),
    [
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
    ],
  );

  return <TenantContext.Provider value={value}>{children}</TenantContext.Provider>;
}

export function useTenant(): TenantContextValue {
  const context = useContext(TenantContext);
  if (!context) throw new Error("useTenant must be used within TenantProvider");
  return context;
}
