import { useState, useEffect, useCallback } from "react";
import { api } from "@/services/api";

export type LetterStatus =
  | "Draft"
  | "Input"
  | "Strategy"
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

const ORGANIZATION_FETCH_LIMIT = 50;

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

      if (!id || !name) {
        console.warn(
          "[parseOrganizationsResponse] Skipping org with missing id or name:",
          org
        );
        return null;
      }

      return {
        id: String(id),
        name: String(name),
        description:
          org?.description ?? org?.shortName ?? org?.short_name ?? undefined,
      } as Organization;
    })
    .filter(Boolean) as Organization[];
};

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
  letter_no?: string;
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
  contractor_context?: string;
  engineer_context?: string;
  employer_context?: string;
  strategy_role?: string;
  strategy_recipient?: string;
  strategy_plan?: string;
  strategy_run_id?: string;
  strategy_graph_status?: string;
  strategy_graph_trace?: Record<string, unknown>[];
  strategy_graph_started_at?: string;
  strategy_graph_completed_at?: string;
  thread_letters?: string[];
  strategy_plan_approved_by?: string;
  strategy_plan_approved_at?: string;
  pendency_days?: number;
  draft_plan?: string;
  draft_output?: string;
  summary_points?: string[];
  graph_status?: string;
  graph_warnings?: string[];
  graph_run_id?: string;
  draft_trace?: Record<string, unknown>[];
  context_document_ids?: string[];
  context_documents?: Record<string, unknown>[];
  background_summary?: Record<string, unknown>[];
  background_annotations?: string;
  strategic_outline?: Record<string, unknown> | null;
  outline_last_edited_by?: string;
  outline_last_edited_at?: string;
  graph_thread?: Record<string, unknown>[];
  draft_sources?: Record<string, unknown>[];
  reviewer_findings?: Record<string, unknown>[];
  reviewer_blocking?: boolean;
  draft_versions?: Record<string, unknown>[];
  current_draft_version?: number;
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
    const rawOrgId = l?.organization_id ?? l?.organizationId ?? "";
    const rawProjId =
      l?.project_id ??
      l?.projectId ??
      (Array.isArray(l?.projects) ? l.projects[0] : undefined);

    return {
      ...l,
      id: String(id),
      created_by: l?.created_by ?? l?.createdBy ?? l?.created_by_id ?? "",
      assigned_to: l?.assigned_to ?? l?.assignedTo?.id ?? "",
      organization_id:
        rawOrgId !== undefined && rawOrgId !== null ? String(rawOrgId) : "",
      project_id:
        rawProjId !== undefined && rawProjId !== null
          ? String(rawProjId)
          : undefined,
      reviewer_blocking: l?.reviewer_blocking ?? false,
      draft_versions: l?.draft_versions ?? [],
      current_draft_version: l?.current_draft_version,
    };
  }, []);

  // Fetch initial data
  useEffect(() => {
    const fetchInitialData = async () => {
      try {
        setIsLoading(true);
        setError(null);
        const [usersRes, orgsRes, projectsRes] = await Promise.allSettled([
          api.get<User[]>("/users"),
          api.get("/organizations", {
            params: { limit: ORGANIZATION_FETCH_LIMIT },
          }),
          api.get<any[]>("/projects"),
        ]);

        if (usersRes.status === "fulfilled") {
          // Normalize users and filter to those with drafting capability (role-based)
          const rawUsersPayload = usersRes.value.data as any;
          const rawUsers = Array.isArray(rawUsersPayload)
            ? rawUsersPayload
            : Array.isArray(rawUsersPayload?.users)
            ? rawUsersPayload.users
            : Array.isArray(rawUsersPayload?.items)
            ? rawUsersPayload.items
            : Array.isArray(rawUsersPayload?.data)
            ? rawUsersPayload.data
            : [];
          const sourceUsers = Array.isArray(rawUsers) ? rawUsers : [];
          // Only include roles that have drafting capability (docs:create).
          // Based on seeded roles, exclude doccontroller (no docs:create).
          const draftingRoles = new Set([
            "superadmin",
            "orgadmin",
            "orguser",
            "projectadmin",
            "projectuser",
            // common aliases seen in seeds
            "projadmin",
            "projuser",
            "contractmgr_org",
            "contractmgr_proj",
            "headcontract",
          ]);
          const filtered = sourceUsers.filter((u: any) => {
            const roles: string[] = Array.isArray(u?.roles) ? u.roles : [];
            // If roles missing, include by default to avoid empty list in dev data
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
        } else {
          console.warn("Failed to load users:", usersRes.reason);
          setUsers([]);
        }

        if (orgsRes.status === "fulfilled") {
          console.log(
            "[useLetterWorkflow] Organizations response:",
            orgsRes.value.data
          );
          const parsed = parseOrganizationsResponse(orgsRes.value.data);
          console.log(
            "[useLetterWorkflow] Parsed organizations:",
            parsed.length
          );
          setOrganizations(parsed);
        } else {
          console.warn("Failed to load organizations:", orgsRes.reason);
          setOrganizations([]);
        }

        if (projectsRes.status === "fulfilled") {
          // Normalize projects and map organization_id -> organizationId for filtering
          const rawProjects = projectsRes.value.data;
          const pdata = Array.isArray(rawProjects)
            ? rawProjects
            : Array.isArray(rawProjects?.projects)
            ? rawProjects.projects
            : [];

          console.log(
            "[useLetterWorkflow] Projects response:",
            pdata.length,
            "projects"
          );

          setProjects(
            pdata.map((p: any) => ({
              id: String(p.id ?? p._id ?? ""),
              name: p.name,
              description: p.description,
              organizationId: (() => {
                const raw =
                  p.organizationId ??
                  p.organization_id ??
                  p.organization?.id ??
                  (Array.isArray(p.organization)
                    ? p.organization[0]?.id
                    : undefined);
                return raw !== undefined && raw !== null ? String(raw) : "";
              })(),
            }))
          );
        } else {
          console.warn("Failed to load projects:", projectsRes.reason);
          setProjects([]);
        }
      } catch (e: any) {
        setError(e?.response?.data?.detail || "Failed to load initial data");
      } finally {
        setIsLoading(false);
      }
    };

    fetchInitialData();
  }, []);

  // Fetch letters when tab changes
  useEffect(() => {
    fetchLetters();
  }, [activeTab]);

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
  }, [organizations, projects]);

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
  }, [activeTab]);

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
    []
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
    [selectedLetter]
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
    []
  );

  // Letter workflow actions
  const submitForReview = useCallback(
    async (
      id: string,
      payload?: {
        reviewer_summary?: string;
        reviewer_findings?: Record<string, unknown>[];
      }
    ) => {
      await api.post(`/letters/${id}/submit`, payload ?? {}, headerFor(id));
      await fetchLetters();
    },
    [fetchLetters]
  );

  const approveLetter = useCallback(
    async (id: string) => {
      await api.post(`/letters/${id}/approve`, {}, headerFor(id));
      await fetchLetters();
    },
    [fetchLetters]
  );

  const completeLetter = useCallback(
    async (id: string) => {
      await api.post(`/letters/${id}/complete`, {}, headerFor(id));
      await fetchLetters();
    },
    [fetchLetters]
  );

  const moveLetterToStrategy = useCallback(
    async (id: string) => {
      await api.post(`/letters/${id}/move-to-strategy`, {}, headerFor(id));
      await fetchLetters();
    },
    [fetchLetters]
  );

  const addComment = useCallback(
    async (id: string, comment: string) => {
      await api.post(`/letters/${id}/comment`, { comment }, headerFor(id));
      await fetchLetters();
    },
    [fetchLetters]
  );

  const reassign = useCallback(
    async (id: string, userId: string) => {
      await api.post(`/letters/${id}/assign/${userId}`, {}, headerFor(id));
      await fetchLetters();
    },
    [fetchLetters]
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
    moveLetterToStrategy,
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
