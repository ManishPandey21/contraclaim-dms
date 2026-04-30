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
import { useNavigate } from "react-router-dom";
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
import enhancedApi from "@/services/enhanced-api";
import NotificationCenter from "@/components/NotificationCenter";
import { create } from "zustand";

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
  uploadDate: string;
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
  createdAt: string;
  updatedAt: string;
}

// Wrapper component to handle prefilling
const LetterInitiationFormWithPrefill: React.FC<{
  onSubmit: (data: any) => void;
  onCancel: () => void;
  organizations: Array<{ id: string; name: string }>;
  projects: WorkflowProject[];
  users: User[];
  prefillData?: any;
}> = ({ onSubmit, onCancel, organizations, projects, users, prefillData }) => {
  const [formKey, setFormKey] = useState(0);

  useEffect(() => {
    // Force form re-render when prefillData changes
    setFormKey((prev) => prev + 1);
  }, [prefillData]);

  // Create modified form component that accepts initial values
  const FormWithDefaults = () => {
    const form = LetterInitiationForm({
      onSubmit,
      onCancel,
      organizations,
      projects,
      users,
    });

    // If we have prefill data, update the form defaults
    useEffect(() => {
      if (prefillData && form.props.children.props.form) {
        const formInstance = form.props.children.props.form;
        if (prefillData.subject) {
          formInstance.setValue("subject", prefillData.subject);
        }
        if (prefillData.recipient) {
          formInstance.setValue("recipient", prefillData.recipient);
        }
        if (prefillData.letter_no) {
          formInstance.setValue("title", `Re: ${prefillData.letter_no}`);
        }
        if (prefillData.organization_id) {
          formInstance.setValue("organizationId", prefillData.organization_id);
        }
        if (prefillData.project_id) {
          formInstance.setValue("projectId", prefillData.project_id);
        }
      }
    }, [prefillData, form]);

    return form;
  };

  return <FormWithDefaults key={formKey} />;
};

const DocumentsPage = () => {
  const navigate = useNavigate();
  const [isViewingReferences, setIsViewingReferences] = useState(false);
  const [selectedDocument, setSelectedDocument] = useState<Document | null>(
    null
  );
  const [isInitiateDialogOpen, setIsInitiateDialogOpen] = useState(false);
  const [draftDocumentData, setDraftDocumentData] = useState<any>(null);
  const [orgs, setOrgs] = useState<Array<{ id: string; name: string }>>([]);
  const [projs, setProjs] = useState<WorkflowProject[]>([]);
  const [users, setUsers] = useState<User[]>([]);
  const [totalDocuments, setTotalDocuments] = useState(0);
  const [documents, setDocuments] = useState<Document[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [tagFilter, setTagFilter] = useState<string>("all");
  const [directionFilter, setDirectionFilter] = useState<string>("all");
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

  const [currentPage, setCurrentPage] = useState(1);
  const documentsPerPage = 25;
  const [isExporting, setIsExporting] = useState(false);

  useEffect(() => {
    fetchDocuments();
  }, []);

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
    const ts = parseDateToTs(value);
    if (Number.isFinite(ts) && ts > 0) {
      const dt = new Date(ts);
      if (!Number.isNaN(dt.getTime())) {
        return dt.toLocaleDateString();
      }
    }
    if (value instanceof Date && !Number.isNaN(value.getTime())) {
      return value.toLocaleDateString();
    }
    const text = value ? String(value).trim() : "";
    return text || "—";
  };

  const buildAuthHeaders = useCallback((): Record<string, string> => {
    if (typeof window === "undefined") return {};

    const headers: Record<string, string> = {};
    const token = window.localStorage.getItem("accessToken");
    if (token && token.trim() !== "") {
      headers["Authorization"] = `Bearer ${token}`;
    }

    const userId = window.localStorage.getItem("user_id");
    headers["X-User-Id"] = userId && userId.trim() !== "" ? userId : "demo";

    const rawRoles = window.localStorage.getItem("user_roles");
    let rolesHeader = "superadmin";
    if (rawRoles && rawRoles.trim() !== "") {
      try {
        const parsed = JSON.parse(rawRoles);
        if (Array.isArray(parsed) && parsed.length > 0) {
          rolesHeader = parsed.map((r: any) => String(r)).join(",");
        } else {
          rolesHeader = rawRoles;
        }
      } catch {
        rolesHeader = rawRoles;
      }
    }
    headers["X-User-Role"] = rolesHeader;
    headers["X-User-Roles"] = rolesHeader;

    const orgId = window.localStorage.getItem("org_id");
    if (orgId && orgId.trim() !== "") {
      headers["X-Org-Id"] = orgId;
    }
    const projId = window.localStorage.getItem("proj_id");
    if (projId && projId.trim() !== "") {
      headers["X-Proj-Id"] = projId;
    }

    return headers;
  }, []);

  const fetchDocuments = useCallback(async () => {
    setLoading(true);
    setError(null);

    const params = new URLSearchParams();
    if (searchTerm) {
      params.append("search", searchTerm);
    }
    if (statusFilter !== "all") {
      params.append("status", statusFilter);
    }
    // Tag filter is applied client-side to avoid backend mismatch of IDs vs names
    // if (tagFilter !== "all") {
    //   params.append("tags", tagFilter);
    // }
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
      const response = await fetch(url, {
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

          return {
            id: doc._id,
            name: doc.filename,
            date: doc.date,
            dateTs: parseDateToTs(doc.date),
            letterNo: doc.letterNo || "",
            direction:
              doc.uploadType && doc.uploadType.toLowerCase() === "incoming"
                ? "Incoming"
                : "Outgoing",
            from_: fromDisplay,
            to: doc.to || "",
            subject: doc.subject,
            tag: doc.tags.length > 0 ? doc.tags[0] : "",
            subTag: doc.subTags.length > 0 ? doc.subTags[0] : "",
            status: status as Document["status"],
            project: projectDisplay || "Unknown Project",
            uploadDate: doc.createdAt,
            uploadDateTs: parseDateToTs(doc.createdAt),
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
      const orgRes = await fetch(joinApiUrl("/organizations"), { headers });
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
      const projRes = await fetch(joinApiUrl("/projects"), { headers });
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

      const res = await fetch(joinApiUrl("/users"), { headers });
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
        const res = await fetch(joinApiUrl("/tags"), { headers });
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
              const subtagRes = await fetch(
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

  const filteredDocuments = sortedDocuments.filter((doc) => {
    const search = searchTerm.toLowerCase();
    const matchesSearch =
      (doc.name || "").toLowerCase().includes(search) ||
      (doc.subject || "").toLowerCase().includes(search) ||
      (doc.letterNo || "").toLowerCase().includes(search);

    const matchesStatus = statusFilter === "all" || doc.status === statusFilter;

    // Client-side fallback for tag filtering:
    // - If backend returned names in doc.tag, compare by selected tag's name
    // - If backend expects IDs and returned ID-like tag in doc.tag, also compare by raw value
    const selectedTagName =
      tagFilter !== "all"
        ? availableTags.find((t) => t.id === tagFilter)?.name ?? tagFilter
        : null;
    const matchesTag =
      tagFilter === "all" ||
      (selectedTagName !== null &&
        (doc.tag || "").toString().trim().toLowerCase() ===
          selectedTagName.toString().trim().toLowerCase()) ||
      (doc.tag || "").toString().trim().toLowerCase() ===
        tagFilter.toString().trim().toLowerCase();

    const matchesDirection =
      directionFilter === "all" || doc.direction === directionFilter;

    return matchesSearch && matchesStatus && matchesTag && matchesDirection;
  });

  const totalPages = Math.ceil(totalDocuments / documentsPerPage);
  const startIndex = (currentPage - 1) * documentsPerPage;

  // Since we're getting paginated data from the backend, we don't need to slice again
  // But apply client-side sorting/filtering on the current page result
  const paginatedDocuments = filteredDocuments;

  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [isPreviewOpen, setIsPreviewOpen] = useState(false);
  const [isPreviewLoading, setIsPreviewLoading] = useState(false);

  const handleViewDocument = (docId: string) => {
    navigate(`/documentviewer/${docId}`);
  };

  const handleDeleteDocument = useCallback(
    async (docId: string) => {
      const confirmed = window.confirm(
        "Are you sure you want to delete this document?"
      );
      if (!confirmed) {
        return;
      }

      try {
        const headers = buildAuthHeaders();
        const response = await fetch(joinApiUrl(`/documents/${docId}`), {
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
    [documents, setDocuments, buildAuthHeaders]
  );

  const handleExportExcel = useCallback(async () => {
    try {
      setIsExporting(true);
      const params = new URLSearchParams();
      if (searchTerm) params.append("search", searchTerm);
      if (statusFilter !== "all") params.append("status", status
