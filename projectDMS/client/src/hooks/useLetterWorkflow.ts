import { useState, useEffect, useCallback } from "react";
import { api } from "@/services/api";
import { getCurrentUserProfile, SessionProfile } from "@/services/session-api";

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

const normalizeEntityId = (value: any): string => {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number") return String(value);
  if (typeof value === "object") {
    if (typeof value.$oid === "string") return value.$oid;
    if (typeof value.id === "string") return value.id;
    if (typeof value._id === "string") return value._id;
  }
  return String(value);
};

const cleanNamePart = (value: any): string => {
  const text = String(value ?? "").trim();
  return text && text !== "-" ? text : "";
};

const buildPersonName = (value: any): string => {
  const firstName = cleanNamePart(value?.first_name ?? value?.firstName);
  const lastName = cleanNamePart(value?.last_name ?? value?.lastName);
  const fullName = [firstName, lastName].filter(Boolean).join(" ").trim();
  if (fullName) return fullName;

  return (
    cleanNamePart(value?.full_name) ||
    cleanNamePart(value?.fullName) ||
    cleanNamePart(value?.name) ||
    "Unknown user"
  );
};

const normalizeWorkflowUser = (value: any): User => ({
  id: normalizeEntityId(value?.id ?? value?._id ?? value?.user_id ?? value?.email ?? value?.username),
  name: buildPersonName(value),
  email: value?.email ?? "",
  avatar: value?.avatar ?? undefined,
  roles: Array.isArray(value?.roles) ? value.roles : [],
  organizationId:
    normalizeEntityId(value?.organization_id ?? value?.organizationId) || null,
  projects: Array.isArray(value?.projects)
    ? value.projects.map((projectId: any) => normalizeEntityId(projectId)).filter(Boolean)
    : [],
});

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
  drafting_profile?: string;
  drafting_assigned_by?: string;
  drafting_assigned_at?: string;
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
  const [currentUser, setCurrentUser] = useState<SessionProfile | null>(null);
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [letters, setLetters] = useState<Letter[]>([]);

  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Backend derives user and tenant scope from the HttpOnly session cookie.
  const headerFor = useCallback(
    (_letterId?: string, _orgIdOverride?: string, _projIdOverride?: string) => {
      return {};
    },
    []
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
      drafting_profile: l?.drafting_profile,
      drafting_assigned_by: l?.drafting_assigned_by,
      drafting_assigned_at: l?.drafting_assigned_at,
    };
  }, []);

  // Fetch initial data
  useEffect(() => {
    const fetchInitialData = async () => {
      try {
        setIsLoading(true);
        setError(null);
        const [profileRes, usersRes, orgsRes, projectsRes] = await Promise.allSettled([
          getCurrentUserProfile(),
          api.get<User[]>("/users"),
          api.get("/organizations", {
            params: { limit: ORGANIZATION_FETCH_LIMIT },
          }),
          api.get<any[]>("/projects"),
        ]);

        if (profileRes.status === "fulfilled") {
          setCurrentUser(profileRes.value);
        } else {
          setCurrentUser(null);
        }

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
          // Only include roles that have drafting capability.
          // Based on seeded roles, exclude document controllers.
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
            "contraclaim_drafting_manager",
            "contraclaim_expert_drafter",
            "contract_letter_drafter",
            "contract_drafter",
          ]);
          const filtered = sourceUsers.filter((u: any) => {
            const roles: string[] = Array.isArray(u?.roles) ? u.roles : [];
            // If roles missing, include by default to avoid empty list in dev data
            if (roles.length === 0) return true;
            return roles.some((r) => draftingRoles.has(String(r)));
          });
          const normalized = filtered.map(normalizeWorkflowUser);
          setUsers(normalized);
        } else {
          console.warn("Failed to load users:", usersRes.reason);
          setUsers(
            profileRes.status === "fulfilled"
              ? [normalizeWorkflowUser(profileRes.value)]
              : []
          );
        }

        if (orgsRes.status === "fulfilled") {
          const parsed = parseOrganizationsResponse(orgsRes.value.data);
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

  // Fetch letters when tab changes. Backend derives tenant scope from /me/session.
  useEffect(() => {
    fetchLetters();
  }, [fetchLetters]);

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
    [headerFor, normalizeLetter, selectedLetter]
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
    async (
      id: string,
      payload?: {
        reviewer_summary?: string;
        reviewer_findings?: Record<string, unknown>[];
        draft_run_id?: string;
        expected_draft_hash?: string;
      }
    ) => {
      await api.post(`/letters/${id}/submit`, payload ?? {}, headerFor(id));
      await fetchLetters();
    },
    [fetchLetters, headerFor]
  );

  const approveLetter = useCallback(
    async (id: string) => {
      await api.post(`/letters/${id}/approve`, {}, headerFor(id));
      await fetchLetters();
    },
    [fetchLetters, headerFor]
  );

  const completeLetter = useCallback(
    async (id: string) => {
      await api.post(`/letters/${id}/complete`, {}, headerFor(id));
      await fetchLetters();
    },
    [fetchLetters, headerFor]
  );

  const moveLetterToStrategy = useCallback(
    async (id: string) => {
      await api.post(`/letters/${id}/move-to-strategy`, {}, headerFor(id));
      await fetchLetters();
    },
    [fetchLetters, headerFor]
  );

  const addComment = useCallback(
    async (id: string, comment: string) => {
      await api.post(`/letters/${id}/comment`, { comment }, headerFor(id));
      await fetchLetters();
    },
    [fetchLetters, headerFor]
  );

  const reassign = useCallback(
    async (id: string, userId: string) => {
      await api.post(`/letters/${id}/assign/${userId}`, {}, headerFor(id));
      await fetchLetters();
    },
    [fetchLetters, headerFor]
  );

  const assignContractDrafter = useCallback(
    async (
      id: string,
      payload: {
        user_id: string;
        drafting_profile:
          | "contractor"
          | "engineer_representation"
          | "employer_contract_review";
      }
    ) => {
      const { data } = await api.post<Letter>(
        `/letters/${id}/assign-drafter`,
        payload,
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
    },
    [headerFor, normalizeLetter, selectedLetter]
  );

  const assignReviewer = useCallback(
    async (
      letterId: string,
      runId: string,
      payload: {
        reviewer_user_id: string;
        due_at?: string;
        note?: string;
      }
    ) => {
      const { data } = await api.post(
        `/letters/${letterId}/drafting/runs/${runId}/assign-reviewer`,
        payload,
        headerFor(letterId)
      );
      await fetchLetters();
      return data;
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
      return Array.isArray(data) ? data : data?.requests ?? [];
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
    currentUser,
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
    assignContractDrafter,
    assignReviewer,

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
