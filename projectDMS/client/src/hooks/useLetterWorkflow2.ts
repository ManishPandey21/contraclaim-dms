import { useState, useEffect, useCallback } from "react";
import { api } from "@/services/api";
import { joinApiUrl } from "@/config/api";

const parseOrganizationsResponse = (payload: any): Organization[] => {
  // Handle nested response structure from backend: { organizations: [...], total, page, limit }
  let collection: any[] = [];

  if (Array.isArray(payload)) {
    collection = payload;
  } else if (payload && typeof payload === "object") {
    // Check for nested organizations array (from OrganizationListResponse)
    if (Array.isArray(payload.organizations)) {
      collection = payload.organizations;
    } else if (Array.isArray(payload.data)) {
      collection = payload.data;
    }
  }

  console.log("[parseOrganizationsResponse] Parsing organizations:", {
    payloadType: typeof payload,
    isArray: Array.isArray(payload),
    hasOrganizations: payload?.organizations ? "yes" : "no",
    collectionLength: collection.length,
  });

  return collection
    .map((org: any) => {
      // Handle both _id and id fields from backend
      const id =
        org?.id ??
        org?._id ??
        (typeof org?._id !== "undefined" ? String(org._id) : "");
      const name = org?.name ?? org?.title ?? "";
      const description =
        org?.description ?? org?.shortName ?? org?.short_name ?? undefined;

      if (!id || !name) {
        console.warn(
          "[parseOrganizationsResponse] Skipping org with missing id or name:",
          org
        );
        return null;
      }

      return { id: String(id), name: String(name), description };
    })
    .filter(Boolean) as Organization[];
};

const buildFallbackHeaders = (): Record<string, string> => {
  const headers: Record<string, string> = {
    Accept: "application/json",
  };
  if (typeof window === "undefined") {
    return headers;
  }

  const token = window.localStorage.getItem("accessToken");
  if (token && token.trim() !== "") {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const storedUserId = window.localStorage.getItem("user_id") ?? "";
  headers["X-User-Id"] = storedUserId.trim() !== "" ? storedUserId : "demo";

  const storedRoles = window.localStorage.getItem("user_roles") ?? "";
  let roleHeader = storedRoles.trim();
  if (!roleHeader) {
    roleHeader = "superadmin";
  } else {
    try {
      const parsed = JSON.parse(roleHeader);
      if (Array.isArray(parsed)) {
        roleHeader = parsed.map((role: any) => String(role)).join(",");
      }
    } catch {
      // keep original string
    }
  }
  headers["X-User-Role"] = roleHeader;
  headers["X-User-Roles"] = roleHeader;

  const orgId = window.localStorage.getItem("org_id");
  if (orgId && orgId.trim() !== "") {
    headers["X-Org-Id"] = orgId;
  }

  const projId = window.localStorage.getItem("proj_id");
  if (projId && projId.trim() !== "") {
    headers["X-Proj-Id"] = projId;
  }
  return headers;
};

const fetchOrganizationsResilient = async (): Promise<Organization[]> => {
  console.log("[fetchOrganizationsResilient] Starting organization fetch...");

  try {
    const { data } = await api.get(joinApiUrl("/organizations"), {
      params: { limit: 50 },
    });
    console.log("[fetchOrganizationsResilient] Primary fetch response:", data);

    const parsed = parseOrganizationsResponse(data);
    console.log(
      "[fetchOrganizationsResilient] Parsed organizations:",
      parsed.length
    );

    if (parsed.length > 0) {
      return parsed;
    }
  } catch (err) {
    console.warn(
      "[fetchOrganizationsResilient] Primary organization fetch failed:",
      err
    );
  }

  if (typeof window === "undefined") {
    console.log(
      "[fetchOrganizationsResilient] Window undefined, returning empty array"
    );
    return [];
  }

  try {
    console.log("[fetchOrganizationsResilient] Attempting fallback fetch...");
    const headers = buildFallbackHeaders();
    const resp = await fetch(joinApiUrl("/organizations?limit=50"), {
      headers,
    });

    if (!resp.ok) {
      const detail = await resp.text().catch(() => "");
      console.warn(
        "[fetchOrganizationsResilient] Fallback organization fetch failed:",
        resp.status,
        resp.statusText,
        detail
      );
      return [];
    }

    const json = await resp.json();
    console.log("[fetchOrganizationsResilient] Fallback fetch response:", json);

    const parsed = parseOrganizationsResponse(json);
    console.log(
      "[fetchOrganizationsResilient] Fallback parsed organizations:",
      parsed.length
    );

    if (parsed.length > 0) {
      return parsed;
    }
  } catch (fallbackError) {
    console.warn(
      "[fetchOrganizationsResilient] Fallback organization fetch threw:",
      fallbackError
    );
  }

  console.log(
    "[fetchOrganizationsResilient] No organizations found, returning empty array"
  );
  return [];
};

export type LetterStatus =
  | "Draft"
  | "Input"
  | "Review"
  | "Approval"
  | "Completed"
  | "Rejected";

export interface User {
  id: string;
  name: string;
  email: string;
  avatar?: string;
  // Optional fields used for permission/org scoping
  roles?: string[];
  organizationId?: string | null;
  projects?: string[];
}

export interface Organization {
  id: string;
  name: string;
  description?: string;
}

export interface Project {
  id: string;
  name: string;
  description?: string;
  organizationId: string;
}

export interface LetterRef {
  id: string;
  title: string;
  subject: string;
  date: string;
  reference_number: string;
}

export interface Letter {
  id: string;
  title: string;
  recipient: string;
  subject: string;
  content: string;
  status: LetterStatus;
  created_by: string;
  assigned_to: string;
  organization_id: string;
  project_id?: string;
  created_at: string;
  updated_at: string;
  due_date?: string;
  comments: string[];
  reference?: LetterRef | null;
  status_start_date?: string;
  statusStartDate?: string;
  pendency_days?: number;
  pendencyDays?: number;
}

export interface CreateLetterInput {
  title: string;
  recipient: string;
  subject: string;
  content?: string;
  assigned_to: string;
  organization_id?: string;
  project_id?: string;
  reference?: LetterRef | null;
  due_date?: string | null;
  useAI?: boolean;
  context?: string;
}

export interface InputRequest {
  id: string;
  letter_id: string;
  requested_from: string;
  details: string;
  due_date?: string;
  status: string;
  response?: string;
  created_at: string;
  updated_at: string;
}

export const useLetterWorkflow = () => {
  const [activeTab, setActiveTab] = useState<LetterStatus | "All">("All");
  const [isInitiateDialogOpen, setIsInitiateDialogOpen] = useState(false);
  const [isRequestInputDialogOpen, setIsRequestInputDialogOpen] =
    useState(false);
  const [selectedLetter, setSelectedLetter] = useState<Letter | null>(null);

  const [users, setUsers] = useState<User[]>([]);
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [letters, setLetters] = useState<Letter[]>([]);

  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Build authorization headers for org/project-scoped endpoints
  const headerFor = useCallback(
    (letterId?: string, orgIdOverride?: string, projIdOverride?: string) => {
      const target = letterId
        ? letters.find((l) => l.id === letterId)
        : selectedLetter;

      const orgId =
        orgIdOverride ??
        target?.organization_id ??
        (typeof window !== "undefined"
          ? window.localStorage.getItem("org_id") ?? undefined
          : undefined);
      const projId =
        projIdOverride ??
        target?.project_id ??
        (typeof window !== "undefined"
          ? window.localStorage.getItem("proj_id") ?? undefined
          : undefined);

      return {
        headers: {
          "X-Org-Id": orgId ?? "",
          "X-Proj-Id": projId ?? "",
        },
      };
    },
    [letters, selectedLetter]
  );

  // Normalize backend letter payload to hook shape (ensures .id exists)
  const normalizeLetter = useCallback((l: any) => {
    const id = l?.id ?? (l as any)?._id ?? "";

    const statusRaw =
      l?.statusStartDate ??
      l?.status_start_date ??
      l?.status_startDate ??
      l?.statusstartdate ??
      l?.updatedAt ??
      l?.updated_at ??
      l?.createdAt ??
      l?.created_at;

    let statusStartDate: string | undefined;
    if (statusRaw instanceof Date) {
      statusStartDate = statusRaw.toISOString();
    } else if (typeof statusRaw === "string") {
      statusStartDate = statusRaw;
    }

    let pendency =
      typeof l?.pendencyDays === "number"
        ? l.pendencyDays
        : typeof l?.pendency_days === "number"
        ? l.pendency_days
        : undefined;

    if ((pendency === undefined || Number.isNaN(pendency)) && statusStartDate) {
      const parsed = new Date(statusStartDate);
      if (!Number.isNaN(parsed.getTime())) {
        const diffMs = Date.now() - parsed.getTime();
        pendency = Math.max(0, Math.floor(diffMs / (1000 * 60 * 60 * 24)));
      }
    }

    return {
      ...l,
      id: String(id),
      created_by: l?.created_by ?? l?.createdBy ?? l?.created_by_id ?? "",
      assigned_to: l?.assigned_to ?? l?.assignedTo?.id ?? "",
      organization_id: l?.organization_id ?? l?.organizationId ?? "",
      project_id:
        l?.project_id ??
        l?.projectId ??
        (Array.isArray(l?.projects) ? l.projects[0] : undefined),
      statusStartDate,
      status_start_date: statusStartDate,
      pendencyDays: pendency,
      pendency_days: pendency,
    };
  }, []);

  // Fetch initial data
  useEffect(() => {
    const fetchInitialData = async () => {
      setIsLoading(true);
      try {
        const [usersRes, projectsRes] = await Promise.allSettled([
          api.get<User[]>("/users"),
          api.get<any[]>("/projects"),
        ]);

        let hasData = false;

        if (usersRes.status === "fulfilled") {
          const rawUsers = (usersRes.value?.data as any[]) ?? [];
          const draftingRoles = new Set([
            "superadmin",
            "orgadmin",
            "orguser",
            "projectadmin",
            "projectuser",
            "projadmin",
            "projuser",
            "contractmgr_org",
            "contractmgr_proj",
            "headcontract",
          ]);
          const filtered = rawUsers.filter((u: any) => {
            const roles: string[] = Array.isArray(u?.roles) ? u.roles : [];
            if (roles.length === 0) return true;
            return roles.some((r) => draftingRoles.has(String(r)));
          });
          const normalized = filtered.map((u: any) => ({
            id: u.id ?? u._id ?? String(u._id ?? u.email ?? u.username ?? ""),
            name: u.username ?? u.name ?? u.full_name ?? u.email ?? "User",
            email: u.email ?? "",
            avatar: u.avatar ?? undefined,
            roles: Array.isArray(u?.roles) ? u.roles : [],
            organizationId:
              (typeof u.organization_id === "string"
                ? u.organization_id
                : u.organization_id
                ? String(u.organization_id)
                : undefined) ??
              (typeof u.organizationId === "string"
                ? u.organizationId
                : undefined) ??
              null,
            projects: Array.isArray(u?.projects)
              ? u.projects.map((p: any) => String(p))
              : [],
          }));
          setUsers(normalized);
          if (normalized.length > 0) {
            hasData = true;
          }
        } else {
          console.warn("Failed to load users:", usersRes.reason);
          setUsers([]);
        }

        const organizationsList = await fetchOrganizationsResilient();
        setOrganizations(organizationsList);
        if (organizationsList.length > 0) {
          hasData = true;
        }

        if (projectsRes.status === "fulfilled") {
          const rawProjects = projectsRes.value?.data;
          const pdata = Array.isArray(rawProjects)
            ? rawProjects
            : Array.isArray(rawProjects?.projects)
            ? rawProjects.projects
            : [];
          setProjects(
            pdata.map((p: any) => ({
              id: p.id ?? p._id ?? String(p._id ?? ""),
              name: p.name,
              description: p.description,
              organizationId:
                p.organizationId ??
                p.organization_id ??
                p.organization?.id ??
                "",
            }))
          );
          if (pdata.length > 0) {
            hasData = true;
          }
        } else {
          console.warn("Failed to load projects:", projectsRes.reason);
          setProjects([]);
        }

        if (hasData) {
          setError(null);
        } else {
          setError((prev) => prev ?? "Failed to load initial data");
        }
      } catch (e: any) {
        setError(e?.response?.data?.detail || "Failed to load initial data");
      } finally {
        setIsLoading(false);
      }
    };

    fetchInitialData();
  }, []);

  const fetchLetters = useCallback(async () => {
    try {
      setIsLoading(true);
      setError(null);
      const params: any = {};
      if (activeTab !== "All") params.tab = activeTab;

      const { data } = await api.get<Letter[]>("/letters", { params });
      const normalized = (Array.isArray(data) ? data : []).map((l: any) =>
        normalizeLetter(l)
      );
      setLetters(normalized as any);
    } catch (e: any) {
      setError(e?.response?.data?.detail || "Failed to load letters");
    } finally {
      setIsLoading(false);
    }
  }, [activeTab, normalizeLetter]);

  // Fetch letters when tab changes
  useEffect(() => {
    fetchLetters();
  }, [fetchLetters]);

  // Ensure scoping headers (org_id/proj_id) based on roles and available data,
  // so backend returns scoped letters for non-superadmin users.
  useEffect(() => {
    try {
      if (typeof window === "undefined") return;
      const rawRoles = window.localStorage.getItem("user_roles");
      const parseRoles = (raw: string | null): string[] => {
        if (!raw) return [];
        try {
          const parsed = JSON.parse(raw);
          if (Array.isArray(parsed))
            return parsed.map((r) => String(r).toLowerCase());
        } catch {
          // not JSON, treat as comma-separated
        }
        return String(raw)
          .split(",")
          .map((r) => r.trim().toLowerCase())
          .filter(Boolean);
      };
      const roles = parseRoles(rawRoles);

      // If superadmin, allow full visibility without forcing org/project scope
      if (roles.includes("superadmin")) return;

      const hasOrgRole = roles.some((r) => r === "orgadmin" || r === "orguser");
      const hasProjRole = roles.some(
        (r) =>
          r === "projectadmin" ||
          r === "projectuser" ||
          r === "projadmin" ||
          r === "projuser"
      );

      let updated = false;
      let orgId = window.localStorage.getItem("org_id") || undefined;
      let projId = window.localStorage.getItem("proj_id") || undefined;

      if (hasOrgRole && !orgId && organizations.length > 0) {
        orgId = String(organizations[0].id);
        window.localStorage.setItem("org_id", orgId);
        updated = true;
      }

      if (hasProjRole && !projId && projects.length > 0) {
        // Prefer a project under the selected org if available
        const preferredOrgId = orgId;
        let chosen = projects[0];
        if (preferredOrgId) {
          const underOrg = projects.find(
            (p) => String(p.organizationId) === String(preferredOrgId)
          );
          if (underOrg) chosen = underOrg;
        }
        projId = String(chosen.id);
        window.localStorage.setItem("proj_id", projId);
        // Ensure org_id aligns with the chosen project if still missing
        if (!orgId && chosen.organizationId) {
          orgId = String(chosen.organizationId);
          window.localStorage.setItem("org_id", orgId);
        }
        updated = true;
      }

      if (updated) {
        // Re-fetch letters with new scoping headers applied by Axios interceptor
        fetchLetters();
      }
    } catch {
      // Non-fatal
    }
  }, [organizations, projects, fetchLetters]);

  const handleLetterInitiation = useCallback(
    async (payload: CreateLetterInput): Promise<Letter> => {
      try {
        setIsLoading(true);
        setError(null);
        const { data } = await api.post<Letter>(
          "/letters",
          payload,
          headerFor(undefined, payload.organization_id, payload.project_id)
        );

        const created = normalizeLetter(data as any);
        setLetters((prev) => [created as any, ...prev]);
        setIsInitiateDialogOpen(false);

        return created as any;
      } catch (e: any) {
        setError(e?.response?.data?.detail || "Failed to create letter");
        throw e;
      } finally {
        setIsLoading(false);
      }
    },
    [headerFor, normalizeLetter]
  );

  const handleLetterUpdate = useCallback(
    async (id: string, patch: Partial<Letter>): Promise<Letter> => {
      try {
        setIsLoading(true);
        setError(null);
        const { data } = await api.put<Letter>(
          `/letters/${id}`,
          patch,
          headerFor(id)
        );

        const updated = normalizeLetter(data as any);
        setLetters((prev) =>
          prev.map((letter) => (letter.id === id ? (updated as any) : letter))
        );

        if (selectedLetter?.id === id) {
          setSelectedLetter(updated as any);
        }

        return updated as any;
      } catch (e: any) {
        setError(e?.response?.data?.detail || "Failed to update letter");
        throw e;
      } finally {
        setIsLoading(false);
      }
    },
    [selectedLetter, headerFor, normalizeLetter]
  );

  const handleInputRequest = useCallback(
    async (
      letterId: string,
      payload: {
        requested_from: string;
        details: string;
        due_date?: string;
        key_points?: string;
        reference_letter_id?: string;
      }
    ) => {
      try {
        setIsLoading(true);
        setError(null);
        const { data } = await api.post(
          `/input-requests/letter/${letterId}`,
          payload,
          headerFor(letterId)
        );

        setIsRequestInputDialogOpen(false);
        return data;
      } catch (e: any) {
        setError(e?.response?.data?.detail || "Failed to create input request");
        throw e;
      } finally {
        setIsLoading(false);
      }
    },
    [headerFor]
  );

  // Letter workflow actions
  const submitForReview = useCallback(
    async (id: string) => {
      await api.post(`/letters/${id}/submit`, {}, headerFor(id));
      await fetchLetters();
    },
    [headerFor, fetchLetters]
  );

  const approveLetter = useCallback(
    async (id: string) => {
      await api.post(`/letters/${id}/approve`, {}, headerFor(id));
      await fetchLetters();
    },
    [headerFor, fetchLetters]
  );

  const completeLetter = useCallback(
    async (id: string) => {
      await api.post(`/letters/${id}/complete`, {}, headerFor(id));
      await fetchLetters();
    },
    [headerFor, fetchLetters]
  );

  const addComment = useCallback(
    async (id: string, comment: string) => {
      await api.post(`/letters/${id}/comment`, { comment }, headerFor(id));
      await fetchLetters();
    },
    [headerFor, fetchLetters]
  );

  const reassign = useCallback(
    async (id: string, userId: string) => {
      await api.post(`/letters/${id}/assign/${userId}`, {}, headerFor(id));
      await fetchLetters();
    },
    [headerFor, fetchLetters]
  );

  // Input request actions
  const listInputRequests = useCallback(
    async (letterId: string) => {
      const { data } = await api.get(
        `/input-requests/letter/${letterId}`,
        headerFor(letterId)
      );
      return data;
    },
    [headerFor]
  );

  const respondToInputRequest = useCallback(
    async (requestId: string, message: string) => {
      const { data } = await api.post(`/input-requests/${requestId}/respond`, {
        message,
      });
      return data;
    },
    []
  );

  const closeInputRequest = useCallback(async (requestId: string) => {
    const { data } = await api.post(`/input-requests/${requestId}/close`);
    return data;
  }, []);

  // Utility functions
  const formatDate = useCallback((dateString: string) => {
    return new Date(dateString).toLocaleDateString();
  }, []);

  const getFilteredLetters = useCallback(() => {
    return letters;
  }, [letters]);

  return {
    // State
    activeTab,
    setActiveTab,
    isInitiateDialogOpen,
    setIsInitiateDialogOpen,
    isRequestInputDialogOpen,
    setIsRequestInputDialogOpen,
    selectedLetter,
    setSelectedLetter,

    // Data
    users,
    organizations,
    projects,
    letters,
    isLoading,
    error,

    // Actions
    handleLetterInitiation,
    handleLetterUpdate,
    handleInputRequest,
    submitForReview,
    approveLetter,
    completeLetter,
    addComment,
    reassign,

    // Input requests
    listInputRequests,
    respondToInputRequest,
    closeInputRequest,

    // Utilities
    formatDate,
    getFilteredLetters,
    fetchLetters,
  };
};
