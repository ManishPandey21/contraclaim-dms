import { useState, useEffect, useCallback } from "react";
import { api } from "@/services/api";
import { joinApiUrl } from "@/config/api";
import { authenticatedFetch } from "@/services/http";

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
      try {
        setIsLoading(true);
        const [usersRes, orgsRes, projectsRes] = await Promise.allSettled([
          api.get<User[]>("/users"),
          api.get<any[]>("/organizations"),
          api.get<any[]>("/projects"),
        ]);

        if (usersRes.status === "fulfilled") {
          // Normalize users and filter to those with drafting capability (role-based)
          const rawUsers = (usersRes.value.data as any[]) ?? [];
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
          const filtered = rawUsers.filter((u: any) => {
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

        let normalizedOrgs: Organization[] = [];
        if (orgsRes.status === "fulfilled") {
          // Normalize organizations payloads that may arrive as a paginated response
          const raw = orgsRes.value?.data as any;
          const collection = Array.isArray(raw)
            ? raw
            : Array.isArray(raw?.organizations)
            ? raw.organizations
            : [];

          if (!Array.isArray(raw) && !Array.isArray(raw?.organizations)) {
            console.warn(
              "Unexpected organizations payload shape; attempting fallback fetch",
              raw
            );
          }

          normalizedOrgs = collection.map((o: any) => ({
            id: o.id ?? o._id ?? String(o._id ?? ""),
            name: o.name,
            description: o.description,
          }));
        } else {
          console.warn("Failed to load organizations:", orgsRes.reason);
        }

        // Fallback fetch when the primary axios call fails or returns no records.
        if (normalizedOrgs.length === 0 && typeof window !== "undefined") {
          try {
            const resp = await authenticatedFetch(joinApiUrl("/organizations"), {
              headers: { Accept: "application/json" },
            });
            if (resp.ok) {
              const json = await resp.json();
              const fallback = Array.isArray(json)
                ? json
                : Array.isArray(json?.organizations)
                ? json.organizations
                : [];
              normalizedOrgs = fallback.map((o: any) => ({
                id: o.id ?? o._id ?? String(o._id ?? ""),
                name: o.name,
                description: o.description,
              }));
            } else {
              console.warn(
                "Fallback organization fetch failed",
                resp.status,
                await resp.text().catch(() => "")
              );
            }
          } catch (fallbackError) {
            console.warn("Fallback organization fetch threw", fallbackError);
          }
        }

        setOrganizations(normalizedOrgs);

        if (projectsRes.status === "fulfilled") {
          // Normalize projects and map organization_id -> organizationId for filtering
          const pdata = projectsRes.value.data as any[];
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

  // Fetch letters when tab changes
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
    async (id: string) => {
      await api.post(`/letters/${id}/submit`, {}, headerFor(id));
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
