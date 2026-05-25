import React, { useState, useEffect, useCallback } from "react";
import {
  FileText,
  Search,
  Filter,
  Download,
  Eye,
  MoreHorizontal,
  ArrowUpDown,
  Check,
  X,
  Clock,
  Upload,
  Trash2,
  FileSearch,
  Calendar,
  Info,
  ChevronLeft,
  ChevronRight,
  Tag,
  Link,
  PenSquare,
  BookOpen,
  Loader2,
} from "lucide-react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { format } from "date-fns";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { toast } from "sonner";
import useHasPermission from "@/hooks/useHasPermission";
import { AlertCircle } from "lucide-react";
// import { api, Project as ApiProject } from "@/services/api";
import LetterInitiationForm from "@/components/letter-workflow/LetterInitiationForm";
import {
  User,
  Project as WorkflowProject,
} from "@/components/letter-workflow/types";
import { joinApiUrl } from "@/config/api";
import {
  ensureValidToken,
  refreshToken,
  logoutAndRedirect,
} from "@/services/auth";
import { authenticatedFetch } from "@/services/http";
import enhancedApi from "@/services/enhanced-api";
import {
  getEffectivePlanServices,
  EffectivePlanState,
} from "@/services/plan-settings-api";
import { create } from "zustand";
import { LETTER_INITIATION_PREFILL_KEY } from "@/constants/storageKeys";
import { string } from "zod";

interface DocumentReference {
  id: string;
  name: string;
  date: string;
  type: string;
  direction: "Incoming" | "Outgoing";
  letterNo: string;
  subject: string;
  linkType?: "direct" | "indirect";
}

interface Document {
  id: string;
  name: string;
  date: string;
  dateTs: number;
  letterNo: string;
  direction: "Incoming" | "Outgoing";
  from_: string;
  to: string;
  subject: string;
  tag: string;
  subTag: string;
  status:
    | "Received"
    | "Input Required"
    | "On Hold"
    | "Under Review"
    | "Under Process"
    | "Replied"
    | "Forwarded"
    | "Completed"
    | "Closed"
    | "Reply Received"
    | "No Reply Received"
    | "Reply Overdue"
    | "Draft"
    | "Pending Review"
    | "Pending Reply"
    | "Reply Not Required";
  project: string; // This will now hold the project name
  organizationId?: string;
  projectId?: string;
  createdAt: string | null;
  uploadDate: string | null;
  uploadDateTs: number;
  size: string;
  references?: DocumentReference[];
}

interface BackendDocument {
  _id: string;
  filename: string;
  filepath_s3: string;
  filetype: string;
  filesize: number;
  uploadType: string;
  letterNo: string | null;
  date: string;
  subject: string;
  from_?: string | null; // backend may return from_ or from
  from?: string | null;
  to: string | null;
  tags: string[];
  subTags: string[];
  status: string;
  ocrEnabled: boolean;
  compressionEnabled: boolean;
  organization_id: string;
  project_id: string;
  project_name?: string; // backend-populated in list_documents
  createdAt?: string;
  created_at?: string;
  uploadedAt?: string;
  uploaded_at?: string;
  updatedAt?: string;
  updated_at?: string;
}

type DocumentsFilterUrlState = {
  searchTerm: string;
  statusFilter: string;
  tagFilter: string;
  directionFilter: string;
  projectFilter: string;
  dateFrom: string;
  dateTo: string;
  currentPage: number;
};

const parseDocumentsPageParam = (value: string | null): number => {
  const page = Number.parseInt(value || "", 10);
  return Number.isFinite(page) && page > 0 ? page : 1;
};

const directionFromUploadType = (value: string | null): string => {
  const normalized = (value || "").toLowerCase();
  if (normalized === "incoming") return "Incoming";
  if (normalized === "outgoing") return "Outgoing";
  return "all";
};

const uploadTypeFromDirection = (value: string): string | null => {
  if (value === "Incoming") return "incoming";
  if (value === "Outgoing") return "outgoing";
  return null;
};

const filtersFromSearchParams = (
  params: URLSearchParams
): DocumentsFilterUrlState => ({
  searchTerm: params.get("search") || "",
  statusFilter: params.get("status") || "all",
  tagFilter: params.get("tags") || "all",
  directionFilter: directionFromUploadType(params.get("uploadType")),
  projectFilter: params.get("project_id") || "all",
  dateFrom: params.get("date_from") || "",
  dateTo: params.get("date_to") || "",
  currentPage: parseDocumentsPageParam(params.get("page")),
});

const searchParamsFromFilters = (
  filters: DocumentsFilterUrlState
): URLSearchParams => {
  const params = new URLSearchParams();
  const search = filters.searchTerm.trim();
  if (search) params.set("search", search);
  if (filters.projectFilter !== "all") {
    params.set("project_id", filters.projectFilter);
  }
  if (filters.statusFilter !== "all") {
    params.set("status", filters.statusFilter);
  }
  if (filters.tagFilter !== "all") {
    params.set("tags", filters.tagFilter);
  }
  if (filters.dateFrom) {
    params.set("date_from", filters.dateFrom);
  }
  if (filters.dateTo) {
    params.set("date_to", filters.dateTo);
  }
  const uploadType = uploadTypeFromDirection(filters.directionFilter);
  if (uploadType) params.set("uploadType", uploadType);
  if (filters.currentPage > 1) {
    params.set("page", String(filters.currentPage));
  }
  return params;
};

const DocumentsPage = () => {
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const initialFilters = filtersFromSearchParams(searchParams);
  const [isViewingReferences, setIsViewingReferences] = useState(false);
  const [selectedDocument, setSelectedDocument] = useState<Document | null>(
    null
  );
  const [isInitiateDialogOpen, setIsInitiateDialogOpen] = useState(false);
  const [orgs, setOrgs] = useState<Array<{ id: string; name: string }>>([]);
  const [projs, setProjs] = useState<WorkflowProject[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [totalDocuments, setTotalDocuments] = useState(0);
  const [documents, setDocuments] = useState<Document[]>([]);
  const [effectiveServices, setEffectiveServices] = useState<{
    organizations: Record<string, EffectivePlanState>;
    projects: Record<string, EffectivePlanState>;
  }>({ organizations: {}, projects: {} });
  const [entitlementsLoading, setEntitlementsLoading] = useState(true);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState(initialFilters.searchTerm);
  const [statusFilter, setStatusFilter] = useState<string>(
    initialFilters.statusFilter
  );
  const [tagFilter, setTagFilter] = useState<string>(initialFilters.tagFilter);
  const [directionFilter, setDirectionFilter] = useState<string>(
    initialFilters.directionFilter
  );
  const [projectFilter, setProjectFilter] = useState<string>(
    initialFilters.projectFilter
  );
  const [dateFrom, setDateFrom] = useState(initialFilters.dateFrom);
  const [dateTo, setDateTo] = useState(initialFilters.dateTo);
  const [availableTags, setAvailableTags] = useState<
    Array<{ id: string; name: string }>
  >([]);
  const [availableSubtags, setAvailableSubtags] = useState<
    Array<{ id: string; name: string }>
  >([]);
  const [sortConfig, setSortConfig] = useState<{
    key: keyof Document;
    direction: "ascending" | "descending";
  }>({
    key: "dateTs",
    direction: "descending",
  });

  const [currentPage, setCurrentPage] = useState(initialFilters.currentPage);
  const documentsPerPage = 25;
  const [isExporting, setIsExporting] = useState(false);

  const parseDateToTs = (input: any): number => {
    if (!input) return 0;
    if (input instanceof Date) return input.getTime();
    const s = String(input);
    // Try native parse first (ISO and many common formats)
    const native = Date.parse(s);
    if (!Number.isNaN(native)) return native;
    // Try DD/MM/YYYY or DD-MM-YYYY with optional time
    const m = s.match(
      /^(\d{1,2})[\/\-](\d{1,2})[\/\-](\d{2,4})(?:\s+(\d{1,2}):(\d{2})(?::(\d{2}))?)?$/
    );
    if (m) {
      const day = parseInt(m[1], 10);
      const month = parseInt(m[2], 10) - 1;
      let year = parseInt(m[3], 10);
      if (year < 100) year += 2000;
      const hour = m[4] ? parseInt(m[4], 10) : 0;
      const minute = m[5] ? parseInt(m[5], 10) : 0;
      const second = m[6] ? parseInt(m[6], 10) : 0;
      return new Date(year, month, day, hour, minute, second).getTime();
    }
    return 0;
  };

  const formatDateDisplay = (value: any): string => {
    const formatResolved = (date: Date) => format(date, "dd-MM-yyyy");

    const ts = parseDateToTs(value);
    if (Number.isFinite(ts) && ts > 0) {
      const dt = new Date(ts);
      if (!Number.isNaN(dt.getTime())) {
        return formatResolved(dt);
      }
    }

    if (value instanceof Date && !Number.isNaN(value.getTime())) {
      return formatResolved(value);
    }

    const text = value ? String(value).trim() : "";
    if (text) {
      const fallbackTs = parseDateToTs(text);
      if (Number.isFinite(fallbackTs) && fallbackTs > 0) {
        const dt = new Date(fallbackTs);
        if (!Number.isNaN(dt.getTime())) {
          return formatResolved(dt);
        }
      }
      return text;
    }

    return "--";
  };

  const resolveTagDisplay = useCallback(
    (value?: string): string => {
      const rawValue = typeof value === "string" ? value.trim() : "";
      if (!rawValue) return "--";
      return (
        availableTags.find((tag) => String(tag.id) === rawValue)?.name ||
        rawValue
      );
    },
    [availableTags]
  );

  const resolveSubTagDisplay = useCallback(
    (value?: string): string => {
      const rawValue = typeof value === "string" ? value.trim() : "";
      if (!rawValue) return "--";
      return (
        availableSubtags.find((subtag) => String(subtag.id) === rawValue)
          ?.name || rawValue
      );
    },
    [availableSubtags]
  );

  const buildAuthHeaders = useCallback((): Record<string, string> => {
    return {};
  }, []);

  useEffect(() => {
    const filters = filtersFromSearchParams(searchParams);
    setSearchTerm((current) =>
      current === filters.searchTerm ? current : filters.searchTerm
    );
    setStatusFilter((current) =>
      current === filters.statusFilter ? current : filters.statusFilter
    );
    setTagFilter((current) =>
      current === filters.tagFilter ? current : filters.tagFilter
    );
    setDirectionFilter((current) =>
      current === filters.directionFilter ? current : filters.directionFilter
    );
    setProjectFilter((current) =>
      current === filters.projectFilter ? current : filters.projectFilter
    );
    setDateFrom((current) =>
      current === filters.dateFrom ? current : filters.dateFrom
    );
    setDateTo((current) =>
      current === filters.dateTo ? current : filters.dateTo
    );
    setCurrentPage((current) =>
      current === filters.currentPage ? current : filters.currentPage
    );
  }, [searchParams]);

  const updateDocumentFilters = useCallback(
    (
      updates: Partial<DocumentsFilterUrlState>,
      options: { resetPage?: boolean } = {}
    ) => {
      const nextFilters: DocumentsFilterUrlState = {
        searchTerm,
        statusFilter,
        tagFilter,
        directionFilter,
        projectFilter,
        dateFrom,
        dateTo,
        currentPage,
        ...updates,
      };

      if (options.resetPage) {
        nextFilters.currentPage = 1;
      }

      setSearchTerm(nextFilters.searchTerm);
      setStatusFilter(nextFilters.statusFilter);
      setTagFilter(nextFilters.tagFilter);
      setDirectionFilter(nextFilters.directionFilter);
      setProjectFilter(nextFilters.projectFilter);
      setDateFrom(nextFilters.dateFrom);
      setDateTo(nextFilters.dateTo);
      setCurrentPage(nextFilters.currentPage);
      setSearchParams(searchParamsFromFilters(nextFilters), { replace: true });
    },
    [
      searchTerm,
      statusFilter,
      tagFilter,
      directionFilter,
      projectFilter,
      dateFrom,
      dateTo,
      currentPage,
      setSearchParams,
    ]
  );

  const resetDocumentFilters = useCallback(() => {
    const resetFilters: DocumentsFilterUrlState = {
      searchTerm: "",
      statusFilter: "all",
      tagFilter: "all",
      directionFilter: "all",
      projectFilter: "all",
      dateFrom: "",
      dateTo: "",
      currentPage: 1,
    };

    setSearchTerm(resetFilters.searchTerm);
    setStatusFilter(resetFilters.statusFilter);
    setTagFilter(resetFilters.tagFilter);
    setDirectionFilter(resetFilters.directionFilter);
    setProjectFilter(resetFilters.projectFilter);
    setDateFrom(resetFilters.dateFrom);
    setDateTo(resetFilters.dateTo);
    setCurrentPage(resetFilters.currentPage);
    setSearchParams(searchParamsFromFilters(resetFilters), { replace: true });
  }, [setSearchParams]);

  const hasActiveFilters =
    searchTerm.trim() !== "" ||
    statusFilter !== "all" ||
    tagFilter !== "all" ||
    directionFilter !== "all" ||
    projectFilter !== "all" ||
    dateFrom !== "" ||
    dateTo !== "";

  useEffect(() => {
    let mounted = true;
    const loadEffectiveServices = async () => {
      try {
        setEntitlementsLoading(true);
        const response = await getEffectivePlanServices();
        if (mounted) {
          setEffectiveServices(response.effective || { organizations: {}, projects: {} });
        }
      } catch (error) {
        console.warn("[DocumentsPage] Failed to load plan service entitlements", error);
        if (mounted) {
          setEffectiveServices({ organizations: {}, projects: {} });
        }
      } finally {
        if (mounted) setEntitlementsLoading(false);
      }
    };

    loadEffectiveServices();
    return () => {
      mounted = false;
    };
  }, []);

  const isDraftingEnabledForDocument = useCallback(
    (doc: Document): boolean => {
      if (entitlementsLoading) return false;
      if (doc.projectId && effectiveServices.projects[doc.projectId]) {
        return Boolean(effectiveServices.projects[doc.projectId].drafting_enabled);
      }
      if (doc.organizationId && effectiveServices.organizations[doc.organizationId]) {
        return Boolean(
          effectiveServices.organizations[doc.organizationId].drafting_enabled
        );
      }
      return false;
    },
    [effectiveServices, entitlementsLoading]
  );

  const fetchDocuments = useCallback(async () => {
    setLoading(true);
    setError(null);

    const params = new URLSearchParams();
    if (searchTerm.trim()) {
      params.append("search", searchTerm.trim());
    }
    if (projectFilter && projectFilter !== "all") {
      params.append("project_id", projectFilter);
    }
    if (statusFilter !== "all") {
      params.append("status", statusFilter);
    }
    if (tagFilter !== "all") {
      params.append("tags", tagFilter);
    }
    if (dateFrom) {
      params.append("date_from", dateFrom);
    }
    if (dateTo) {
      params.append("date_to", dateTo);
    }
    if (directionFilter !== "all") {
      params.append(
        "uploadType",
        directionFilter === "Incoming" ? "incoming" : "outgoing"
      );
    }
    const skip = (currentPage - 1) * documentsPerPage;
    params.append("skip", String(skip));
    params.append("limit", String(documentsPerPage));
    const url = `${joinApiUrl("/documents")}?${params.toString()}`;

    try {
      const headers = buildAuthHeaders();
      const response = await authenticatedFetch(url, {
        headers,
      });

      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(
          `Failed to fetch documents: ${response.status} - ${errorText}`
        );
      }

      // Handle new response format with documents and total
      const responseData = await response.json();
      const data: BackendDocument[] = responseData.documents || responseData; // Fallback for backward compatibility
      const total: number = responseData.total || data.length; // Use total from response or fallback to data length

      setTotalDocuments(total);

      if (data) {
        const validStatuses: Document["status"][] = [
          "Received",
          "Input Required",
          "On Hold",
          "Under Review",
          "Under Process",
          "Replied",
          "Forwarded",
          "Completed",
          "Closed",
          "Reply Received",
          "No Reply Received",
          "Reply Overdue",
          "Draft",
          "Pending Review",
          "Pending Reply",
          "Reply Not Required",
        ];

        const transformedDocuments: Document[] = data.map((doc) => {
          let status = doc.status;
          if (!validStatuses.includes(status as Document["status"])) {
            console.warn(
              `Invalid status "${status}" for document ${doc._id}. Defaulting to "Received".`
            );
            status = "Received";
          }

          // Robustly resolve "From" name coming from backend
          const fromRaw =
            (typeof doc.from_ === "string" && doc.from_) ||
            (typeof (doc as any).from === "string" && (doc as any).from) ||
            "";
          const fromDisplay =
            typeof fromRaw === "string" && fromRaw.trim() !== ""
              ? fromRaw
              : "N/A";

          // Robustly resolve project name
          const projectDisplay =
            (doc.project_name as string | undefined) ||
            ((doc as any).project as string | undefined) ||
            "";
          // Type guard for Date objects
          const isDateObject = (value: unknown): value is Date => {
            return value instanceof Date;
          };

          const uploadDateSource =
            doc.createdAt ??
            doc.created_at ??
            doc.uploadedAt ??
            doc.uploaded_at ??
            doc.updatedAt ??
            doc.updated_at ??
            doc.date;

          const uploadDateString = isDateObject(uploadDateSource)
            ? uploadDateSource.toISOString()
            : typeof uploadDateSource === "string"
            ? uploadDateSource
            : "";

          return {
            id: doc._id,
            name: doc.filename,
            date: doc.date,
            dateTs: parseDateToTs(doc.date),
            letterNo: doc.letterNo || "",
            direction:
              doc.uploadType && doc.uploadType.toLowerCase() === "incoming"
                ? "Incoming"
                : "Outgoing", // direction: doc.uploadType === 'incoming' ? 'Incoming' : 'Outgoing',
            from_: fromDisplay,
            to: doc.to || "",
            subject: doc.subject,
            tag: doc.tags.length > 0 ? doc.tags[0] : "",
            subTag: doc.subTags.length > 0 ? doc.subTags[0] : "",
            status: status as Document["status"],
            project: projectDisplay || "Unknown Project",
            organizationId: doc.organization_id ? String(doc.organization_id) : undefined,
            projectId: doc.project_id ? String(doc.project_id) : undefined,
            createdAt: uploadDateString || null,
            uploadDate: uploadDateString || null,
            uploadDateTs: parseDateToTs(uploadDateSource),
            size: `${(doc.filesize / (1024 * 1024)).toFixed(2)} MB`,
            references: [],
          };
        });
        setDocuments(transformedDocuments);
      }
    } catch (error: any) {
      setError(error.message);
    } finally {
      setLoading(false);
    }
  }, [
    currentPage,
    searchTerm,
    statusFilter,
    tagFilter,
    directionFilter,
    projectFilter,
    dateFrom,
    dateTo,
    buildAuthHeaders,
  ]);

  useEffect(() => {
    fetchDocuments();
  }, [fetchDocuments]);

  // Fetch organizations and projects for LetterInitiationForm
  const fetchOrgsAndProjects = useCallback(async () => {
    try {
      const headers = buildAuthHeaders();

      // Organizations
      const orgRes = await authenticatedFetch(joinApiUrl("/organizations"), { headers });
      if (orgRes.ok) {
        const orgData = await orgRes.json();
        const mapped = Array.isArray(orgData)
          ? orgData
          : Array.isArray(orgData?.organizations)
          ? orgData.organizations
          : [];
        setOrgs(
          mapped.map((o: any) => ({
            id: o._id || o.id || "",
            name: o.name || o.title || "",
          }))
        );
      }

      // Projects
      const projRes = await authenticatedFetch(joinApiUrl("/projects"), { headers });
      if (projRes.ok) {
        const projData = await projRes.json();
        const mapped = Array.isArray(projData)
          ? projData
          : Array.isArray(projData?.projects)
          ? projData.projects
          : [];
        const mappedProjects: WorkflowProject[] = mapped.map(
          (p: any): WorkflowProject => ({
            id: p._id || p.id || "",
            name: p.name || p.title || "",
            organizationId: String(p.organization_id || p.organizationId || ""),
          })
        );
        setProjs(mappedProjects);
      }
    } catch {
      // non-fatal; form has safe fallbacks
    }
  }, [buildAuthHeaders]);

  const fetchUsers = useCallback(async () => {
    try {
      const headers = buildAuthHeaders();

      const res = await authenticatedFetch(joinApiUrl("/users"), { headers });
      if (res.ok) {
        const data = await res.json();
        const rawUsers: any[] = Array.isArray(data)
          ? data
          : Array.isArray((data as any)?.users)
          ? (data as any).users
          : [];

        // Keep parity with useLetterWorkflow filter to show drafting-capable users
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
          if (roles.length === 0) return true; // allow when roles are missing in dev data
          return roles.some((r) => draftingRoles.has(String(r)));
        });

        const normalized: User[] = filtered.map((u: any) => ({
          id: u.id ?? u._id ?? String(u._id ?? u.email ?? u.username ?? ""),
          name: u.username ?? u.name ?? u.full_name ?? u.email ?? "User",
          email: u.email ?? "",
          avatar: u.avatar ?? undefined,
        }));

        setUsers(normalized);
      } else {
        setUsers([]);
      }
    } catch {
      setUsers([]);
    }
  }, [buildAuthHeaders]);

  useEffect(() => {
    fetchOrgsAndProjects();
  }, [fetchOrgsAndProjects]);

  useEffect(() => {
    fetchUsers();
  }, [fetchUsers]);

  // Load tags for Tag dropdown (dynamic from backend)
  useEffect(() => {
    const loadTags = async () => {
      try {
        const headers = buildAuthHeaders();
        const res = await authenticatedFetch(joinApiUrl("/tags"), { headers });
        if (res.ok) {
          const data = await res.json();
          console.log("Tags API response:", data); // Debug log
          const mapped = Array.isArray(data)
            ? data
            : Array.isArray((data as any)?.tags)
            ? (data as any).tags
            : [];
          const tags = (mapped as any[]).map((t: any) => ({
            id: t._id || t.id || "",
            name: t.name || "",
          }));
          console.log("Processed tags:", tags); // Debug log
          setAvailableTags(tags.filter((t) => t.id && t.name));

          // Also load subtags for mapping
          const allSubtags: Array<{ id: string; name: string }> = [];
          for (const tag of tags) {
            try {
              const subtagRes = await authenticatedFetch(
                joinApiUrl(`/tags/${tag.id}/subtags`),
                { headers }
              );
              if (subtagRes.ok) {
                const subtagData = await subtagRes.json();
                console.log(`Subtags for ${tag.name}:`, subtagData); // Debug log

                // Handle different response formats
                let subtagArray: any[] = [];
                if (Array.isArray(subtagData)) {
                  subtagArray = subtagData;
                } else if (subtagData && Array.isArray(subtagData.subtags)) {
                  subtagArray = subtagData.subtags;
                } else if (subtagData && typeof subtagData === "object") {
                  subtagArray = Object.values(subtagData)
                    .filter(Array.isArray)
                    .flat();
                }

                const subtags = subtagArray.map((st: any) => ({
                  id: st._id || st.id || "",
                  name: st.name || "",
                }));
                allSubtags.push(...subtags);
              }
            } catch (error) {
              console.error(
                `Failed to fetch subtags for tag ${tag.id}:`,
                error
              );
            }
          }
          console.log("All subtags loaded:", allSubtags); // Debug log
          setAvailableSubtags(allSubtags);
        } else {
          console.error("Failed to fetch tags:", res.status, res.statusText);
          setAvailableTags([]);
        }
      } catch (error) {
        console.error("Error loading tags:", error);
        setAvailableTags([]);
      }
    };
    loadTags();
  }, [buildAuthHeaders]);

  const getStatusColor = (status: Document["status"]) => {
    switch (status) {
      case "Received":
        return "bg-blue-100 text-blue-800";
      case "Input Required":
        return "bg-red-100 text-red-800";
      case "On Hold":
        return "bg-gray-100 text-gray-800";
      case "Under Review":
        return "bg-yellow-100 text-yellow-800";
      case "Under Process":
        return "bg-indigo-100 text-indigo-800";
      case "Replied":
        return "bg-green-100 text-green-800";
      case "Forwarded":
        return "bg-purple-100 text-purple-800";
      case "Completed":
        return "bg-emerald-100 text-emerald-800";
      case "Closed":
        return "bg-slate-100 text-slate-800";
      case "Reply Received":
        return "bg-green-100 text-green-800";
      case "No Reply Received":
        return "bg-amber-100 text-amber-800";
      case "Reply Overdue":
        return "bg-red-100 text-red-800";
      case "Draft":
        return "bg-gray-100 text-gray-800";
      case "Pending Review":
        return "bg-yellow-100 text-yellow-800";
      case "Pending Reply":
        return "bg-blue-100 text-blue-800";
      case "Reply Not Required":
        return "bg-purple-100 text-purple-800";
      default:
        return "bg-gray-100 text-gray-800";
    }
  };

  const getStatusIcon = (status: Document["status"]) => {
    switch (status) {
      case "Received":
        return <FileText size={14} />;
      case "Input Required":
        return <PenSquare size={14} />;
      case "On Hold":
        return <X size={14} />;
      case "Under Review":
        return <FileSearch size={14} />;
      case "Under Process":
        return <PenSquare size={14} />;
      case "Replied":
        return <Check size={14} />;
      case "Forwarded":
        return <Link size={14} />;
      case "Completed":
        return <Check size={14} />;
      case "Closed":
        return <Check size={14} />;
      case "Reply Received":
        return <Check size={14} />;
      case "No Reply Received":
        return <Clock size={14} />;
      case "Reply Overdue":
        return <Clock size={14} />;
      case "Draft":
        return <PenSquare size={14} />;
      case "Pending Review":
        return <FileSearch size={14} />;
      case "Pending Reply":
        return <Clock size={14} />;
      case "Reply Not Required":
        return <X size={14} />;
      default:
        return <Info size={14} />;
    }
  };

  const getDirectionBadge = (direction: Document["direction"]) => {
    if (direction === "Incoming") {
      return (
        <Badge
          variant="outline"
          className="bg-blue-50 text-blue-700 border-blue-200"
        >
          Incoming
        </Badge>
      );
    } else {
      return (
        <Badge
          variant="outline"
          className="bg-green-50 text-green-700 border-green-200"
        >
          Outgoing
        </Badge>
      );
    }
  };

  const getLinkTypeBadge = (linkType: string | undefined) => {
    if (linkType === "direct") {
      return (
        <Badge
          variant="outline"
          className="bg-indigo-50 text-indigo-700 border-indigo-200"
        >
          Direct Link
        </Badge>
      );
    } else {
      return (
        <Badge
          variant="outline"
          className="bg-purple-50 text-purple-700 border-purple-200"
        >
          Indirect Link
        </Badge>
      );
    }
  };

  const handleSort = (key: keyof Document) => {
    const direction =
      sortConfig.key === key && sortConfig.direction === "ascending"
        ? "descending"
        : "ascending";

    setSortConfig({ key, direction });
  };

  const sortedDocuments = [...documents].sort((a, b) => {
    const key = sortConfig.key;
    const av: any = a[key];
    const bv: any = b[key];
    // Numeric date sorts for both raw strings and precomputed timestamps
    if (
      key === "date" ||
      key === "uploadDate" ||
      key === "dateTs" ||
      key === "uploadDateTs"
    ) {
      const at =
        key === "dateTs" || key === "uploadDateTs"
          ? Number(av || 0)
          : parseDateToTs(av);
      const bt =
        key === "dateTs" || key === "uploadDateTs"
          ? Number(bv || 0)
          : parseDateToTs(bv);
      if (at === bt) return 0;
      return sortConfig.direction === "ascending" ? at - bt : bt - at;
    }
    // Robust string comparison for fields like tag/status/subject/etc.
    const as = (av ?? "").toString().toLowerCase();
    const bs = (bv ?? "").toString().toLowerCase();
    const cmp = as.localeCompare(bs, undefined, {
      numeric: true,
      sensitivity: "base",
    });
    return sortConfig.direction === "ascending" ? cmp : -cmp;
  });

  const filteredDocuments = sortedDocuments;

  const totalPages = Math.ceil(totalDocuments / documentsPerPage);
  const startIndex = (currentPage - 1) * documentsPerPage;

  // Backend applies filters before pagination; the frontend only sorts the current page.
  const paginatedDocuments = filteredDocuments;

  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [isPreviewOpen, setIsPreviewOpen] = useState(false);
  const [isPreviewLoading, setIsPreviewLoading] = useState(false);
  const canDeleteDocuments = useHasPermission("documents:delete");

  const handleViewDocument = (docId: string) => {
    // navigate(`api/documents/${docId}`);
    navigate(`/documentviewer/${docId}`);
  };

  const handleDeleteDocument = useCallback(
    async (docId: string) => {
      if (!canDeleteDocuments) {
        toast.error("You do not have permission to delete documents.");
        return;
      }
      const confirmed = window.confirm(
        "Are you sure you want to delete this document?"
      );
      if (!confirmed) {
        return;
      }

      try {
        const headers = buildAuthHeaders();
        const response = await authenticatedFetch(joinApiUrl(`/documents/${docId}`), {
          method: "DELETE",
          headers,
        });

        if (!response.ok) {
          const errorText = await response.text();
          throw new Error(
            `Failed to delete document: ${response.status} - ${errorText}`
          );
        }
        setDocuments(documents.filter((doc) => doc.id !== docId));
        toast("Document Deleted", {
          description: "The document has been permanently removed.",
          icon: <Check className="text-docsumo-success" />,
        });
      } catch (error: any) {
        toast.error(error.message, {
          description: "Failed to delete document.",
        });
      }
    },
    [documents, setDocuments, buildAuthHeaders, canDeleteDocuments]
  );

  const handleExportExcel = useCallback(async () => {
    try {
      setIsExporting(true);
      const params = new URLSearchParams();
      if (searchTerm.trim()) params.append("search", searchTerm.trim());
      if (projectFilter !== "all") params.append("project_id", projectFilter);
      if (statusFilter !== "all") params.append("status", statusFilter);
      if (directionFilter !== "all") {
        params.append(
          "uploadType",
          directionFilter === "Incoming" ? "incoming" : "outgoing"
        );
      }
      if (tagFilter !== "all") {
        params.append("tags", tagFilter);
      }
      if (dateFrom) {
        params.append("date_from", dateFrom);
      }
      if (dateTo) {
        params.append("date_to", dateTo);
      }

      const url = `${joinApiUrl("/documents/export")}?${params.toString()}`;

      // Ensure token not near expiry; ignore errors (we retry on 401 below)
      try {
        await ensureValidToken(120);
      } catch {
        // no-op
      }

      const headers = buildAuthHeaders();

      let resp = await authenticatedFetch(url, {
        headers,
      });

      // If unauthorized, try a single refresh and retry once
      if (resp.status === 401) {
        try {
          await refreshToken();
          const retryHeaders = buildAuthHeaders();
          resp = await authenticatedFetch(url, {
            headers: retryHeaders,
          });
        } catch {
          toast.error("Export failed: session expired", {
            description: "Please log in again.",
          });
          logoutAndRedirect("/login");
          return;
        }
      }

      if (!resp.ok) {
        const text = await resp.text();
        throw new Error(`Export failed: ${resp.status} - ${text}`);
      }

      const blob = await resp.blob();
      const href = window.URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = href;
      a.download = "documents_export.xlsx";
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.URL.revokeObjectURL(href);
      toast("Export ready", {
        description: "Your Excel file has been downloaded.",
        icon: <Download className="text-docsumo-blue" />,
      });
    } catch (e: any) {
      toast.error(e?.message || "Export failed", {
        description: "Unable to export documents to Excel.",
      });
    } finally {
      setIsExporting(false);
    }
  }, [
    searchTerm,
    projectFilter,
    statusFilter,
    directionFilter,
    tagFilter,
    dateFrom,
    dateTo,
    buildAuthHeaders,
  ]);

  const handleDownloadDocument = useCallback(
    async (docId: string, docName: string) => {
      try {
        const headers = buildAuthHeaders();
        const response = await authenticatedFetch(joinApiUrl(`/documents/${docId}`), {
          headers,
        });

        if (!response.ok) {
          const errorText = await response.text();
          throw new Error(
            `Failed to fetch document details: ${response.status} - ${errorText}`
          );
        }

        const documentData = await response.json();

        if (documentData && documentData.presigned_url) {
          const link = document.createElement("a");
          link.href = documentData.presigned_url;
          link.download = docName;
          link.target = "_blank";
          document.body.appendChild(link);
          link.click();
          document.body.removeChild(link);

          toast("Download Started", {
            description: `${docName} is downloading...`,
            icon: <Download className="text-docsumo-blue" />,
          });
        } else {
          toast.error("Error downloading document", {
            description: "Presigned URL not available.",
          });
        }
      } catch (error: any) {
        toast.error(error.message, {
          description: "Failed to download document.",
        });
      }
    },
    [buildAuthHeaders]
  );

  const handleRequestDraft = async (docId: string) => {
    // Optimistic UI: temporarily mark as Under Process to disable UI immediately
    const original = documents.find((d) => d.id === docId);
    const originalStatus = original?.status;

    setDocuments((prev) =>
      prev.map((d) => (d.id === docId ? { ...d, status: "Under Process" } : d))
    );

    try {
      const draftContext = await enhancedApi.requestDraftForDocument(docId);

      if (typeof window !== "undefined") {
        try {
          const payloadToStore: Record<string, any> =
            draftContext && typeof draftContext === "object"
              ? {
                  ...draftContext,
                  document_id: (draftContext as any)?.document_id ?? docId,
                }
              : { document_id: docId };

          payloadToStore.draft_reserved = true;

          if (original?.project) {
            payloadToStore.project_name = original.project;
          }

          const organizationId =
            (payloadToStore as any)?.organization_id ??
            (payloadToStore as any)?.organizationId ??
            "";
          if (organizationId) {
            const matchingOrg = orgs.find((org) => org.id === organizationId);
            if (matchingOrg) {
              payloadToStore.organization_name =
                matchingOrg.name || payloadToStore.organization_name;
            }
          }

          if (
            !(payloadToStore as any)?.project_name &&
            (payloadToStore as any)?.project_id
          ) {
            const matchingProj = projs.find(
              (proj) => proj.id === (payloadToStore as any).project_id
            );
            if (matchingProj) {
              payloadToStore.project_name = matchingProj.name;
            }
          }

          window.sessionStorage.setItem(
            LETTER_INITIATION_PREFILL_KEY,
            JSON.stringify(payloadToStore)
          );
        } catch (storageError) {
          console.warn(
            "[DocumentsPage] Failed to cache draft prefill data",
            storageError
          );
        }
      }

      // Navigate to Letter Workflow with document context so initiation dialog opens there
      navigate(`/letters?requestDraftForDocumentId=${docId}`);
    } catch (e: any) {
      // Rollback optimistic update on error
      setDocuments((prev) =>
        prev.map((d) =>
          d.id === docId ? { ...d, status: originalStatus ?? d.status } : d
        )
      );

      // Show clear inline message via toast; EnhancedApi normalizes error messages
      const message =
        e?.message || "Draft already in progress for this document";
      toast.error("Draft already in progress for this document", {
        description: message,
      });
    }
  };

  const handleViewLinks = (docId: string) => {
    // Open the Document Viewer focused on the References tab
    navigate(`/documentviewer/${docId}?tab=references`);
  };

  const handleLinkAttachments = (docId: string) => {
    toast("Link Attachments", {
      description: "Opening attachment linking interface...",
      icon: <Link className="text-docsumo-blue" />,
    });
  };

  // Create a letter directly from the Documents page modal
  const handleInitiateLetterFromDocs = useCallback(
    async (formData: any) => {
      try {
        const payload: any = {
          title: formData.title,
          recipient: formData.recipient,
          subject: formData.subject,
          content: "",
          // Accept multiple shapes from the form
          assigned_to:
            formData.assigned_to ||
            formData.assignedUserId ||
            formData.assignedTo?.id ||
            "",
          organization_id:
            formData.organization_id || formData.organizationId || undefined,
          project_id: formData.project_id || formData.projectId || undefined,
        };

        if (!payload.assigned_to) {
          toast.error("Please select an Assigned Drafter", {
            description: "Assigned Drafter is required to initiate a letter.",
          });
          return;
        }

        const headers: Record<string, string> = {
          ...buildAuthHeaders(),
          "Content-Type": "application/json",
        };
        const res = await authenticatedFetch(joinApiUrl("/letters"), {
          method: "POST",
          headers,
          body: JSON.stringify(payload),
        });

        if (!res.ok) {
          const errText = await res.text();
          throw new Error(
            `Failed to create letter: ${res.status} - ${errText}`
          );
        }

        toast("Letter initiated", {
          description: "Draft letter has been created.",
          icon: <Check className="text-docsumo-success" />,
        });

        setIsInitiateDialogOpen(false);
        // Navigate to workflow page where the new draft will appear
        navigate("/letters");
      } catch (e: any) {
        toast.error(e?.message || "Failed to create letter");
      }
    },
    [navigate, setIsInitiateDialogOpen, buildAuthHeaders]
  );

  return (
    <div className="space-y-6 animate-fade-in">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-2xl font-semibold text-docsumo-text">
            Documents
          </h1>
          <p className="text-sm text-gray-500 mt-1">
            Manage and view all your uploaded documents
          </p>
        </div>

        <div className="flex space-x-2">
          <Button
            variant="outline"
            className="flex items-center"
            onClick={() => navigate("/tags")}
          >
            <Tag size={18} className="mr-2" />
            Manage Tags
          </Button>

          <Button
            variant="outline"
            className="flex items-center"
            onClick={handleExportExcel}
            disabled={isExporting}
            title="Export filtered documents to Excel"
          >
            {isExporting ? (
              <Loader2 size={18} className="mr-2 animate-spin" />
            ) : (
              <Download size={18} className="mr-2" />
            )}
            Export Excel
          </Button>

          <Button
            onClick={() => navigate("/upload")}
            className="bg-docsumo-blue hover:bg-docsumo-blue/90 text-white"
          >
            <Upload size={18} className="mr-2" />
            Upload
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6">
        <Card className="glass-card">
          <CardHeader className="pb-3">
            <CardTitle className="text-lg">Document Library</CardTitle>
            <CardDescription>
              Browse, search, and manage your documents
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex flex-col md:flex-row md:items-center space-y-3 md:space-y-0 md:space-x-4 mb-6">
              <div className="relative flex-1">
                <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                  <Search size={18} className="text-gray-400" />
                </div>
                <Input
                  type="text"
                  placeholder="Search by name, subject, letter number..."
                  className="pl-10"
                  value={searchTerm}
                  onChange={(e) =>
                    updateDocumentFilters(
                      { searchTerm: e.target.value },
                      { resetPage: true }
                    )
                  }
                />
              </div>

              <div className="flex flex-wrap items-center gap-2 w-full md:w-auto">
                <Filter size={18} className="text-gray-500 hidden md:block" />

                <Input
                  type="date"
                  value={dateFrom}
                  aria-label="Date from"
                  title="Date from"
                  onChange={(e) =>
                    updateDocumentFilters(
                      { dateFrom: e.target.value },
                      { resetPage: true }
                    )
                  }
                  className="w-full md:w-[150px]"
                />

                <Input
                  type="date"
                  value={dateTo}
                  min={dateFrom || undefined}
                  aria-label="Date to"
                  title="Date to"
                  onChange={(e) =>
                    updateDocumentFilters(
                      { dateTo: e.target.value },
                      { resetPage: true }
                    )
                  }
                  className="w-full md:w-[150px]"
                />

                <Select
                  value={directionFilter}
                  onValueChange={(value) =>
                    updateDocumentFilters(
                      { directionFilter: value },
                      { resetPage: true }
                    )
                  }
                >
                  <SelectTrigger className="w-full md:w-[150px]">
                    <SelectValue placeholder="Direction" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Directions</SelectItem>
                    <SelectItem value="Incoming">Incoming</SelectItem>
                    <SelectItem value="Outgoing">Outgoing</SelectItem>
                  </SelectContent>
                </Select>

                <Select
                  value={projectFilter}
                  onValueChange={(value) =>
                    updateDocumentFilters(
                      { projectFilter: value },
                      { resetPage: true }
                    )
                  }
                >
                  <SelectTrigger className="w-full md:w-[180px]">
                    <SelectValue placeholder="Project" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Projects</SelectItem>
                    {projs.map((p) => (
                      <SelectItem key={p.id} value={p.id}>
                        {p.name || p.id}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>

                <Select
                  value={statusFilter}
                  onValueChange={(value) =>
                    updateDocumentFilters(
                      { statusFilter: value },
                      { resetPage: true }
                    )
                  }
                >
                  <SelectTrigger className="w-full md:w-[150px]">
                    <SelectValue placeholder="Status" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Status</SelectItem>

                    <SelectItem
                      value="incomingGroup"
                      disabled
                      className="font-semibold"
                    >
                      --- Incoming ---
                    </SelectItem>
                    <SelectItem value="Received">Received</SelectItem>
                    <SelectItem value="Input Required">
                      Input Required
                    </SelectItem>
                    <SelectItem value="On Hold">On Hold</SelectItem>
                    <SelectItem value="Under Review">Under Review</SelectItem>
                    <SelectItem value="Under Process">Under Process</SelectItem>
                    <SelectItem value="Replied">Replied</SelectItem>
                    <SelectItem value="Forwarded">Forwarded</SelectItem>
                    <SelectItem value="Completed">Completed</SelectItem>

                    <SelectItem
                      value="outgoingGroup"
                      disabled
                      className="font-semibold"
                    >
                      --- Outgoing ---
                    </SelectItem>
                    <SelectItem value="Closed">Closed</SelectItem>
                    <SelectItem value="Reply Received">
                      Reply Received
                    </SelectItem>
                    <SelectItem value="No Reply Received">
                      No Reply Received
                    </SelectItem>
                    <SelectItem value="Reply Overdue">Reply Overdue</SelectItem>

                    <SelectItem
                      value="legacyGroup"
                      disabled
                      className="font-semibold"
                    >
                      --- Legacy ---
                    </SelectItem>
                    <SelectItem value="Draft">Draft</SelectItem>
                    <SelectItem value="Pending Review">
                      Pending Review
                    </SelectItem>
                    <SelectItem value="Pending Reply">Pending Reply</SelectItem>
                    <SelectItem value="Reply Not Required">
                      Reply Not Required
                    </SelectItem>
                  </SelectContent>
                </Select>

                <Select
                  value={tagFilter}
                  onValueChange={(value) =>
                    updateDocumentFilters(
                      { tagFilter: value },
                      { resetPage: true }
                    )
                  }
                >
                  <SelectTrigger className="w-full md:w-[150px]">
                    <SelectValue placeholder="Tag" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Tags</SelectItem>
                    {availableTags.map((t) => (
                      <SelectItem key={t.id} value={t.id}>
                        {t.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>

                {hasActiveFilters && (
                  <Button
                    type="button"
                    variant="outline"
                    className="w-full md:w-auto"
                    onClick={resetDocumentFilters}
                  >
                    <X size={14} className="mr-2" />
                    Reset
                  </Button>
                )}
              </div>
            </div>

            {loading ? (
              <div className="text-center py-12">
                <FileText
                  size={28}
                  className="text-gray-400 mx-auto mb-4 animate-spin"
                />
                <h3 className="text-lg font-medium">Loading Documents...</h3>
              </div>
            ) : error ? (
              <div className="text-center py-12">
                <AlertCircle size={28} className="text-red-500 mx-auto mb-4" />
                <h3 className="text-lg font-medium text-red-600">Error</h3>
                <p className="text-sm text-gray-500 mt-1 max-w-md mx-auto">
                  {error}
                </p>
              </div>
            ) : filteredDocuments.length === 0 ? (
              <div className="text-center py-12">
                <div className="flex justify-center">
                  <div className="w-16 h-16 rounded-full bg-gray-100 flex items-center justify-center mb-4">
                    <FileText size={28} className="text-gray-400" />
                  </div>
                </div>
                <h3 className="text-lg font-medium">No Documents Found</h3>
                <p className="text-sm text-gray-500 mt-1 max-w-md mx-auto">
                  {hasActiveFilters
                    ? "No documents match your current filters. Try adjusting your search criteria."
                    : "You haven't uploaded any documents yet. Start by uploading your first document."}
                </p>
                {hasActiveFilters ? (
                  <Button
                    onClick={resetDocumentFilters}
                    variant="outline"
                    className="mt-4"
                  >
                    <X size={14} className="mr-2" />
                    Reset Filters
                  </Button>
                ) : (
                  <Button
                    onClick={() => navigate("/upload")}
                    variant="outline"
                    className="mt-4"
                  >
                    <Upload size={14} className="mr-2" />
                    Upload Document
                  </Button>
                )}
              </div>
            ) : (
              <>
                <div className="rounded-md border overflow-auto">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead className="w-12">
                          <span className="sr-only">Icon</span>
                        </TableHead>
                        <TableHead>
                          <Button
                            variant="ghost"
                            className="p-0 hover:bg-transparent font-medium"
                            onClick={() => handleSort("dateTs")}
                          >
                            Date
                            {sortConfig.key === "dateTs" && (
                              <ArrowUpDown
                                size={14}
                                className="ml-1.5 inline"
                              />
                            )}
                          </Button>
                        </TableHead>
                        <TableHead>Letter No.</TableHead>
                        <TableHead>Direction</TableHead>
                        <TableHead>From</TableHead>
                        <TableHead>To</TableHead>
                        <TableHead>Subject</TableHead>
                        <TableHead>
                          <Button
                            variant="ghost"
                            className="p-0 hover:bg-transparent font-medium"
                            onClick={() => handleSort("tag")}
                          >
                            Tag
                            {sortConfig.key === "tag" && (
                              <ArrowUpDown
                                size={14}
                                className="ml-1.5 inline"
                              />
                            )}
                          </Button>
                        </TableHead>
                        <TableHead>Sub-Tag</TableHead>
                        <TableHead>
                          <Button
                            variant="ghost"
                            className="p-0 hover:bg-transparent font-medium"
                            onClick={() => handleSort("status")}
                          >
                            Status
                            {sortConfig.key === "status" && (
                              <ArrowUpDown
                                size={14}
                                className="ml-1.5 inline"
                              />
                            )}
                          </Button>
                        </TableHead>
                        <TableHead>Project</TableHead>
                        <TableHead>
                          <Button
                            variant="ghost"
                            className="p-0 hover:bg-transparent font-medium"
                            onClick={() => handleSort("uploadDateTs")}
                          >
                            Upload Date
                            {sortConfig.key === "uploadDateTs" && (
                              <ArrowUpDown
                                size={14}
                                className="ml-1.5 inline"
                              />
                            )}
                          </Button>
                        </TableHead>
                        <TableHead className="text-right">Actions</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {paginatedDocuments.map((doc) => {
                        const draftingEnabled = isDraftingEnabledForDocument(doc);
                        const draftDisabledReason = entitlementsLoading
                          ? "Checking drafting service availability..."
                          : "Drafting service is not active for this project or organization";

                        return (
                        <TableRow key={doc.id} className="text-sm">
                          <TableCell>
                            <div className="w-8 h-8 rounded-md bg-gray-100 flex items-center justify-center">
                              <FileText
                                size={14}
                                className="text-docsumo-blue"
                              />
                            </div>
                          </TableCell>
                          <TableCell>{formatDateDisplay(doc.date)}</TableCell>
                          <TableCell>{doc.letterNo}</TableCell>
                          <TableCell>
                            {getDirectionBadge(doc.direction)}
                          </TableCell>
                          <TableCell
                            className="max-w-[150px] truncate"
                            title={doc.from_ || "N/A"}
                          >
                            {doc.from_ && doc.from_.trim() !== ""
                              ? doc.from_
                              : "N/A"}
                          </TableCell>
                          <TableCell className="max-w-[150px] truncate">
                            {doc.to}
                          </TableCell>
                          <TableCell className="whitespace-pre-wrap break-words">
                            {doc.subject}
                          </TableCell>
                          <TableCell
                            className="max-w-[150px] truncate"
                            title={resolveTagDisplay(doc.tag)}
                          >
                            {resolveTagDisplay(doc.tag)}
                          </TableCell>
                          <TableCell
                            className="max-w-[150px] truncate"
                            title={resolveSubTagDisplay(doc.subTag)}
                          >
                            {resolveSubTagDisplay(doc.subTag)}
                          </TableCell>
                          <TableCell>
                            <Badge
                              className={`${getStatusColor(
                                doc.status
                              )} flex w-24 justify-center items-center space-x-1`}
                            >
                              {getStatusIcon(doc.status)}
                              <span>{doc.status}</span>
                            </Badge>
                          </TableCell>
                          <TableCell className="max-w-[150px] truncate">
                            {doc.project ? (
                              doc.project
                            ) : (
                              <span className="text-gray-400">—</span>
                            )}
                          </TableCell>
                          <TableCell>
                            {formatDateDisplay(doc.uploadDate || doc.date)}
                          </TableCell>
                          <TableCell className="text-right">
                            <div className="flex items-center justify-end space-x-1">
                              <Button
                                variant="ghost"
                                size="sm"
                                className="h-8 w-8 p-0"
                                onClick={() => handleViewDocument(doc.id)}
                                title="View"
                              >
                                <Eye size={14} className="text-gray-500" />
                                <span className="sr-only">View</span>
                              </Button>
                              <Button
                                variant="ghost"
                                size="sm"
                                className="h-8 w-8 p-0"
                                onClick={() =>
                                  handleDownloadDocument(doc.id, doc.name)
                                }
                                title="Download"
                              >
                                <Download size={14} className="text-gray-500" />
                                <span className="sr-only">Download</span>
                              </Button>
                              <DropdownMenu>
                                <DropdownMenuTrigger asChild>
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    className="h-8 w-8 p-0"
                                  >
                                    <MoreHorizontal
                                      size={14}
                                      className="text-gray-500"
                                    />
                                    <span className="sr-only">More</span>
                                  </Button>
                                </DropdownMenuTrigger>
                                <DropdownMenuContent align="end">
                                  <DropdownMenuItem
                                    onClick={() => handleViewDocument(doc.id)}
                                  >
                                    <Eye size={14} className="mr-2" />
                                    View
                                  </DropdownMenuItem>
                                  <DropdownMenuItem
                                    onClick={() =>
                                      handleDownloadDocument(doc.id, doc.name)
                                    }
                                  >
                                    <Download size={14} className="mr-2" />
                                    Download
                                  </DropdownMenuItem>
                                  <DropdownMenuItem
                                    onClick={() =>
                                      handleLinkAttachments(doc.id)
                                    }
                                  >
                                    <Link size={14} className="mr-2" />
                                    Link Attachments
                                  </DropdownMenuItem>
                                  {doc.status === "Under Process" ? (
                                    <DropdownMenuItem disabled>
                                      <PenSquare size={14} className="mr-2" />
                                      Request Draft
                                      <Badge
                                        variant="outline"
                                        className="ml-2 text-[10px]"
                                      >
                                        Draft already in progress for this
                                        document
                                      </Badge>
                                    </DropdownMenuItem>
                                  ) : !draftingEnabled ? (
                                    <DropdownMenuItem disabled>
                                      <PenSquare size={14} className="mr-2" />
                                      Request Draft
                                      <Badge
                                        variant="outline"
                                        className="ml-2 text-[10px]"
                                      >
                                        {draftDisabledReason}
                                      </Badge>
                                    </DropdownMenuItem>
                                  ) : (
                                    <DropdownMenuItem
                                      onClick={() => handleRequestDraft(doc.id)}
                                    >
                                      <PenSquare size={14} className="mr-2" />
                                      Request Draft
                                    </DropdownMenuItem>
                                  )}
                                  <DropdownMenuItem
                                    onClick={() => handleViewLinks(doc.id)}
                                    className={
                                      doc.references &&
                                      doc.references.length > 0
                                        ? "font-medium"
                                        : ""
                                    }
                                  >
                                    <FileSearch size={14} className="mr-2" />
                                    View References
                                    {doc.references &&
                                      doc.references.length > 0 && (
                                        <Badge
                                          variant="outline"
                                          className="ml-2 text-xs"
                                        >
                                          {doc.references.length}
                                        </Badge>
                                      )}
                                  </DropdownMenuItem>
                                  <DropdownMenuItem
                                    onClick={() =>
                                      navigate(`/documents/summary/${doc.id}`)
                                    }
                                  >
                                    <BookOpen size={14} className="mr-2" />
                                    Letter Summary
                                  </DropdownMenuItem>
                                  <DropdownMenuItem
                                    onClick={() =>
                                      navigate(`/reference/${doc.id}`)
                                    }
                                  >
                                    <Link size={14} className="mr-2" />
                                    Reference Chain
                                  </DropdownMenuItem>
                                  <DropdownMenuSeparator />
                                  {canDeleteDocuments && (
                                    <DropdownMenuItem
                                      className="text-red-500 focus:text-red-500"
                                      onClick={() => handleDeleteDocument(doc.id)}
                                    >
                                      <Trash2 size={14} className="mr-2" />
                                      Delete
                                    </DropdownMenuItem>
                                  )}
                                </DropdownMenuContent>
                              </DropdownMenu>
                            </div>
                          </TableCell>
                        </TableRow>
                        );
                      })}
                    </TableBody>
                  </Table>
                </div>
                {totalPages > 0 && (
                  <div className="flex items-center justify-between space-x-2 py-4">
                    <div className="text-sm text-gray-500">
                      Showing {startIndex + 1} to{" "}
                      {Math.min(
                        startIndex + paginatedDocuments.length,
                        totalDocuments
                      )}{" "}
                      of {totalDocuments} documents
                    </div>
                    <div className="flex items-center space-x-2">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() =>
                          updateDocumentFilters({
                            currentPage: Math.max(1, currentPage - 1),
                          })
                        }
                        disabled={currentPage === 1}
                      >
                        <ChevronLeft size={14} />
                        <span className="sr-only">Previous</span>
                      </Button>
                      <div className="flex items-center">
                        {Array.from({ length: totalPages }).map((_, i) => (
                          <Button
                            key={i}
                            variant={
                              currentPage === i + 1 ? "default" : "outline"
                            }
                            size="sm"
                            className="w-8 h-8"
                            onClick={() =>
                              updateDocumentFilters({ currentPage: i + 1 })
                            }
                          >
                            {i + 1}
                          </Button>
                        ))}
                      </div>

                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() =>
                          updateDocumentFilters({
                            currentPage: Math.min(totalPages, currentPage + 1),
                          })
                        }
                        disabled={currentPage === totalPages}
                      >
                        <ChevronRight size={14} />
                        <span className="sr-only">Next</span>
                      </Button>
                    </div>
                  </div>
                )}
              </>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Initiate Letter dialog wired to LetterInitiationForm (requested change) */}
      <Dialog
        open={isInitiateDialogOpen}
        onOpenChange={setIsInitiateDialogOpen}
      >
        <DialogContent className="sm:max-w-4xl w-[95vw] max-w-[1200px]">
          <DialogHeader>
            <DialogTitle>Initiate New Letter</DialogTitle>
          </DialogHeader>
          <LetterInitiationForm
            onSubmit={handleInitiateLetterFromDocs}
            onCancel={() => setIsInitiateDialogOpen(false)}
            organizations={orgs}
            projects={projs}
            users={users}
          />
        </DialogContent>
      </Dialog>

      <Dialog open={isViewingReferences} onOpenChange={setIsViewingReferences}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <Link size={18} className="text-blue-600" />
              Document References
            </DialogTitle>
            <DialogDescription>
              {selectedDocument
                ? `Linked references for ${selectedDocument.name}`
                : "Viewing document references"}
            </DialogDescription>
          </DialogHeader>

          {selectedDocument &&
          selectedDocument.references &&
          selectedDocument.references.length > 0 ? (
            <div className="mt-2">
              <div className="border rounded-md overflow-hidden">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Document</TableHead>
                      <TableHead>Letter No.</TableHead>
                      <TableHead>Direction</TableHead>
                      <TableHead>Link Type</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {selectedDocument.references.map((ref) => (
                      <TableRow
                        key={ref.id}
                        className="cursor-pointer hover:bg-slate-50"
                        onClick={() => {
                          setIsViewingReferences(false);
                          navigate(`/document/${ref.id}`);
                        }}
                      >
                        <TableCell className="py-2">
                          <div className="flex items-center gap-2">
                            <FileText size={14} className="text-slate-500" />
                            <span className="font-medium text-sm">
                              {ref.name}
                            </span>
                          </div>
                          <p className="text-xs text-slate-500 mt-0.5">
                            {ref.subject}
                          </p>
                        </TableCell>
                        <TableCell className="py-2 text-sm">
                          {ref.letterNo}
                        </TableCell>
                        <TableCell className="py-2">
                          {getDirectionBadge(ref.direction)}
                        </TableCell>
                        <TableCell className="py-2">
                          {ref.linkType && getLinkTypeBadge(ref.linkType)}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>

              <div className="mt-4 flex justify-between items-center">
                <p className="text-sm text-slate-500">
                  Click on a reference to view the document
                </p>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setIsViewingReferences(false)}
                >
                  Close
                </Button>
              </div>
            </div>
          ) : (
            <div className="py-8 text-center">
              <div className="mx-auto w-12 h-12 rounded-full bg-slate-100 flex items-center justify-center mb-4">
                <Link size={20} className="text-slate-400" />
              </div>
              <h3 className="text-lg font-medium">No References Found</h3>
              <p className="text-sm text-slate-500 mt-1 max-w-xs mx-auto">
                This document has no linked references.
              </p>
              <Button
                variant="outline"
                size="sm"
                className="mt-4"
                onClick={() => setIsViewingReferences(false)}
              >
                Close
              </Button>
            </div>
          )}
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default DocumentsPage;
