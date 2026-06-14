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
  RefreshCw,
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
import { authenticatedFetch } from "@/services/http";
import enhancedApi from "@/services/enhanced-api";
import {
  searchInDocument,
  fullTextSearch,
  type SearchResponse,
} from "@/services/search-api";

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

const DocumentsPage = () => {
  const navigate = useNavigate();
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
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState("");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [tagFilter, setTagFilter] = useState<string>("all");
  const [directionFilter, setDirectionFilter] = useState<string>("all");
  const [sortConfig, setSortConfig] = useState<{
    key: keyof Document;
    direction: "ascending" | "descending";
  }>({
    key: "name",
    direction: "descending",
  });

  // Clause/Text Search states
  const [selectedProjectId, setSelectedProjectId] = useState<string>("");
  const [projectDocs, setProjectDocs] = useState<
    Array<{ id: string; name: string }>
  >([]);
  const [selectedDocId, setSelectedDocId] = useState<string>(""); // "all" or document id
  const [textQuery, setTextQuery] = useState<string>("");
  const [debouncedQuery, setDebouncedQuery] = useState<string>("");
  const [searchLoading, setSearchLoading] = useState<boolean>(false);
  const [searchError, setSearchError] = useState<string | null>(null);

  type DisplayMatch = {
    documentId: string;
    filename: string;
    page?: number;
    position?: number;
    snippet: string;
  };

  const [searchResults, setSearchResults] = useState<DisplayMatch[]>([]);
  const [searchTotal, setSearchTotal] = useState<number>(0);
  const [searchPage, setSearchPage] = useState<number>(1);
  const [searchPageSize, setSearchPageSize] = useState<number>(10);

  const [currentPage, setCurrentPage] = useState(1);
  const documentsPerPage = 25;

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
    if (tagFilter !== "all") {
      params.append("tags", tagFilter);
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
      const response = await authenticatedFetch(url);

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
            letterNo: doc.letterNo || "",
            direction: doc.uploadType === "incoming" ? "Incoming" : "Outgoing",
            from_: fromDisplay,
            to: doc.to || "",
            subject: doc.subject,
            tag: doc.tags.length > 0 ? doc.tags[0] : "",
            subTag: doc.subTags.length > 0 ? doc.subTags[0] : "",
            status: status as Document["status"],
            project: projectDisplay || "Unknown Project",
            uploadDate: doc.createdAt,
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
  }, [currentPage, searchTerm, statusFilter, tagFilter, directionFilter]);

  useEffect(() => {
    fetchDocuments();
  }, [fetchDocuments]);

  useEffect(() => {
    fetchDocuments();
  }, [fetchDocuments]);

  // Debounce textQuery for clause/text search
  useEffect(() => {
    const t = setTimeout(() => setDebouncedQuery(textQuery.trim()), 400);
    return () => clearTimeout(t);
  }, [textQuery]);

  // Fetch documents for the selected project to populate file dropdown
  const fetchProjectDocuments = useCallback(async (projId: string) => {
    if (!projId) {
      setProjectDocs([]);
      return;
    }
    try {
      const docs = await enhancedApi.getDocuments({
        project_id: projId,
        limit: 500,
      });
      const mapped =
        (Array.isArray(docs) ? docs : []).map((d: any) => ({
          id: String((d as any)._id ?? (d as any).id ?? ""),
          name: (d as any).filename ?? (d as any).name ?? "Untitled",
        })) ?? [];
      setProjectDocs(mapped);
    } catch {
      setProjectDocs([]);
    }
  }, []);

  // When project selection changes, persist scoping headers and load its files
  useEffect(() => {
    try {
      if (selectedProjectId) {
        const proj = projs.find(
          (p) => String(p.id) === String(selectedProjectId)
        );
        localStorage.setItem("proj_id", String(selectedProjectId));
        if (proj?.organizationId) {
          localStorage.setItem("org_id", String(proj.organizationId));
        }
        fetchProjectDocuments(selectedProjectId);
      } else {
        // Clear if no project selected
        localStorage.removeItem("proj_id");
        // Keep org_id as-is
        setProjectDocs([]);
      }
    } catch {
      // non-fatal
    }
  }, [selectedProjectId, projs, fetchProjectDocuments]);

  // Reset pagination when key inputs change (project / file / query)
  useEffect(() => {
    setSearchPage(1);
  }, [selectedProjectId, selectedDocId, debouncedQuery]);

  // Execute the clause/text search whenever inputs change (debounced)
  const runSearch = useCallback(async () => {
    // Require explicit file selection (either specific file or "all")
    if (
      !selectedProjectId ||
      !selectedDocId ||
      !debouncedQuery ||
      debouncedQuery.length < 2
    ) {
      setSearchResults([]);
      setSearchTotal(0);
      setSearchError(null);
      return;
    }

    // Validate selected file belongs to current project if not "all"
    if (
      selectedDocId !== "all" &&
      !projectDocs.some((d) => String(d.id) === String(selectedDocId))
    ) {
      setSearchResults([]);
      setSearchTotal(0);
      setSearchError(
        "Selected file not found in this project or access denied."
      );
      return;
    }

    setSearchLoading(true);
    setSearchError(null);

    try {
      // If a specific file is selected, search only within that file
      if (selectedDocId && selectedDocId !== "all") {
        const data = await searchInDocument(selectedDocId, debouncedQuery, {});
        // Map to display structure
        const docMeta = projectDocs.find((d) => d.id === selectedDocId);
        const mapped: DisplayMatch[] = (data.matches || []).map((m) => ({
          documentId: selectedDocId,
          filename: docMeta?.name ?? "Document",
          page: m.page,
          position: m.position,
          snippet: m.highlight || m.context || "",
        }));
        setSearchResults(mapped);
        setSearchTotal(data.total || mapped.length);
      } else {
        // All files within the selected project
        const resp: SearchResponse = await fullTextSearch(debouncedQuery, {
          projects: [selectedProjectId],
          limit: searchPageSize,
          offset: (searchPage - 1) * searchPageSize,
          fuzzy: false,
        });
        const mapped: DisplayMatch[] = (resp.results || []).map((r: any) => ({
          documentId: String(r._id || r.id || ""),
          filename: r.filename || r.name || "Document",
          page: undefined, // unknown in fulltext; backend may add later
          position: undefined,
          snippet:
            Array.isArray(r.highlights) && r.highlights.length > 0
              ? r.highlights[0]
              : r.excerpt || "",
        }));
        setSearchResults(mapped);
        setSearchTotal(resp.total || mapped.length);
      }
    } catch (e: any) {
      setSearchResults([]);
      setSearchTotal(0);
      if (e?.response?.status === 401 || e?.response?.status === 403) {
        setSearchError("You do not have access to this project or file.");
      } else {
        setSearchError(e?.message || "Search failed");
      }
    } finally {
      setSearchLoading(false);
    }
  }, [
    selectedProjectId,
    debouncedQuery,
    selectedDocId,
    projectDocs,
    searchPage,
    searchPageSize,
  ]);

  useEffect(() => {
    void runSearch();
  }, [runSearch]);

  // Fetch organizations and projects for LetterInitiationForm
  const fetchOrgsAndProjects = useCallback(async () => {
    try {
      // Organizations
      const orgRes = await authenticatedFetch(joinApiUrl("/organizations"));
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
      const projRes = await authenticatedFetch(joinApiUrl("/projects"));
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
  }, []);

  const fetchUsers = useCallback(async () => {
    try {
      const res = await authenticatedFetch(joinApiUrl("/users"));
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
  }, []);

  useEffect(() => {
    fetchOrgsAndProjects();
  }, [fetchOrgsAndProjects]);

  useEffect(() => {
    fetchUsers();
  }, [fetchUsers]);

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
    if (a[sortConfig.key] < b[sortConfig.key]) {
      return sortConfig.direction === "ascending" ? -1 : 1;
    }
    if (a[sortConfig.key] > b[sortConfig.key]) {
      return sortConfig.direction === "ascending" ? 1 : -1;
    }
    return 0;
  });

  const filteredDocuments = sortedDocuments.filter((doc) => {
    const matchesSearch =
      doc.name.toLowerCase().includes(searchTerm.toLowerCase()) ||
      doc.subject.toLowerCase().includes(searchTerm.toLowerCase()) ||
      doc.letterNo.toLowerCase().includes(searchTerm.toLowerCase());
    const matchesStatus = statusFilter === "all" || doc.status === statusFilter;
    const matchesTag = tagFilter === "all" || doc.tag === tagFilter;
    const matchesDirection =
      directionFilter === "all" || doc.direction === directionFilter;

    return matchesSearch && matchesStatus && matchesTag && matchesDirection;
  });

  const totalPages = Math.ceil(totalDocuments / documentsPerPage);
  const startIndex = (currentPage - 1) * documentsPerPage;

  // Since we're getting paginated data from the backend, we don't need to slice again
  // The documents array already contains the correct page of data
  const paginatedDocuments = documents;

  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [isPreviewOpen, setIsPreviewOpen] = useState(false);
  const [isPreviewLoading, setIsPreviewLoading] = useState(false);

  const handleViewDocument = (docId: string) => {
    // navigate(`api/documents/${docId}`);
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
        const response = await authenticatedFetch(joinApiUrl(`/documents/${docId}`), {
          method: "DELETE",
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
    [documents, setDocuments]
  );

  const handleDownloadDocument = useCallback(
    async (docId: string, docName: string) => {
      try {
        const response = await authenticatedFetch(joinApiUrl(`/documents/${docId}`));

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
    []
  );

  const handleRequestDraft = (docId: string) => {
    // Open Letter Initiation dialog directly from Documents page
    setIsInitiateDialogOpen(true);
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
    [navigate, setIsInitiateDialogOpen]
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
            onClick={() => navigate("/upload")}
            className="bg-docsumo-blue hover:bg-docsumo-blue/90 text-white"
          >
            <Upload size={18} className="mr-2" />
            Upload
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-6">
        {/* Clause/Text Search Panel */}
        <Card className="glass-card">
          <CardHeader className="pb-3">
            <CardTitle className="text-lg">Clause/Text Search</CardTitle>
            <CardDescription>
              Search within uploaded files for a selected project
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 md:grid-cols-4 gap-3 mb-4">
              {/* Project Select */}
              <div className="col-span-1">
                <div className="text-xs text-gray-600 mb-1">Project</div>
                <Select
                  value={selectedProjectId}
                  onValueChange={(val) => {
                    setSelectedProjectId(val);
                    setSelectedDocId(""); // reset file selection
                    setSearchResults([]);
                    setSearchTotal(0);
                  }}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue placeholder="Select project" />
                  </SelectTrigger>
                  <SelectContent>
                    {projs.map((p) => (
                      <SelectItem key={p.id} value={String(p.id)}>
                        {p.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {/* File Select */}
              <div className="col-span-1">
                <div className="text-xs text-gray-600 mb-1">File</div>
                <Select
                  value={selectedDocId}
                  onValueChange={(val) => {
                    setSelectedDocId(val);
                    setSearchResults([]);
                    setSearchTotal(0);
                  }}
                  disabled={!selectedProjectId}
                >
                  <SelectTrigger className="w-full">
                    <SelectValue placeholder="Select file" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Files</SelectItem>
                    {projectDocs.map((d) => (
                      <SelectItem key={d.id} value={d.id}>
                        {d.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              {/* Search Input */}
              <div className="col-span-2">
                <div className="text-xs text-gray-600 mb-1">Search Text</div>
                <Input
                  type="text"
                  placeholder="Enter clause or text to search..."
                  value={textQuery}
                  onChange={(e) => setTextQuery(e.target.value)}
                  disabled={!selectedProjectId || !selectedDocId}
                />
              </div>
            </div>

            {/* Validation/help */}
            {!selectedProjectId && (
              <div className="text-xs text-amber-700 bg-amber-50 ring-1 ring-amber-200 rounded px-3 py-2 mb-3">
                Select a project to enable file selection and search.
              </div>
            )}
            {selectedProjectId && !selectedDocId && (
              <div className="text-xs text-blue-700 bg-blue-50 ring-1 ring-blue-200 rounded px-3 py-2 mb-3">
                Choose a file or select “All Files” to enable searching.
              </div>
            )}

            {/* Results */}
            <div className="border rounded-md p-3 min-h-[120px]">
              {searchLoading ? (
                <div className="text-sm text-gray-600">Searching...</div>
              ) : searchError ? (
                <div className="text-sm text-red-600">Error: {searchError}</div>
              ) : !debouncedQuery || debouncedQuery.length < 2 ? (
                <div className="text-sm text-gray-500">
                  Enter at least 2 characters to search.
                </div>
              ) : searchResults.length === 0 ? (
                <div className="text-sm text-gray-500">No matches found.</div>
              ) : (
                <div className="space-y-3">
                  {searchResults.map((r, idx) => (
                    <div
                      key={`${r.documentId}-${idx}`}
                      className="rounded border p-2 hover:bg-slate-50"
                    >
                      <div className="text-sm font-medium">
                        {r.filename}
                        {(typeof r.page === "number" ||
                          typeof r.position === "number") && (
                          <span className="text-xs text-gray-500 ml-2">
                            {typeof r.page === "number" ? `Page ${r.page}` : ""}
                            {typeof r.page === "number" &&
                            typeof r.position === "number"
                              ? ", "
                              : ""}
                            {typeof r.position === "number"
                              ? `Pos ${r.position}`
                              : ""}
                          </span>
                        )}
                      </div>
                      <div className="text-xs text-gray-700 mt-1 whitespace-pre-wrap break-words">
                        {r.snippet || "(no preview available)"}
                      </div>
                    </div>
                  ))}

                  {/* Pagination controls (only for All Files / fulltext) */}
                  {selectedDocId === "all" && searchTotal > searchPageSize && (
                    <div className="flex items-center justify-between mt-2">
                      <div className="text-xs text-gray-500">
                        Showing {(searchPage - 1) * searchPageSize + 1} to{" "}
                        {Math.min(searchPage * searchPageSize, searchTotal)} of{" "}
                        {searchTotal} results
                      </div>
                      <div className="flex items-center space-x-2">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => {
                            const next = Math.max(1, searchPage - 1);
                            setSearchPage(next);
                          }}
                          disabled={searchPage === 1}
                        >
                          <ChevronLeft size={14} />
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => {
                            const maxPage = Math.ceil(
                              searchTotal / searchPageSize
                            );
                            const next = Math.min(maxPage, searchPage + 1);
                            setSearchPage(next);
                          }}
                          disabled={
                            searchPage >=
                            Math.ceil(searchTotal / searchPageSize)
                          }
                        >
                          <ChevronRight size={14} />
                        </Button>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          </CardContent>
        </Card>

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
                  onChange={(e) => setSearchTerm(e.target.value)}
                />
              </div>

              <div className="flex flex-wrap items-center gap-2 w-full md:w-auto">
                <Filter size={18} className="text-gray-500 hidden md:block" />

                <Select
                  value={directionFilter}
                  onValueChange={setDirectionFilter}
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

                <Select value={statusFilter} onValueChange={setStatusFilter}>
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

                <Select value={tagFilter} onValueChange={setTagFilter}>
                  <SelectTrigger className="w-full md:w-[150px]">
                    <SelectValue placeholder="Tag" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All Tags</SelectItem>
                    <SelectItem value="Claims">Claims</SelectItem>
                    <SelectItem value="Variations">Variations</SelectItem>
                    <SelectItem value="Hindrances">Hindrances</SelectItem>
                    <SelectItem value="Design & Drawings">
                      Design & Drawings
                    </SelectItem>
                    <SelectItem value="Payment">Payment</SelectItem>
                    <SelectItem value="Schedule">Schedule</SelectItem>
                    <SelectItem value="Safety">Safety</SelectItem>
                    <SelectItem value="Quality">Quality</SelectItem>
                  </SelectContent>
                </Select>
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
                <Button
                  variant="outline"
                  className="mt-4"
                  onClick={() => void fetchDocuments()}
                >
                  <RefreshCw size={14} className="mr-2" />
                  Retry
                </Button>
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
                  {searchTerm ||
                  statusFilter !== "all" ||
                  tagFilter !== "all" ||
                  directionFilter !== "all"
                    ? "No documents match your current filters. Try adjusting your search criteria."
                    : "You haven't uploaded any documents yet. Start by uploading your first document."}
                </p>
                {!searchTerm &&
                  statusFilter === "all" &&
                  tagFilter === "all" &&
                  directionFilter === "all" && (
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
                            onClick={() => handleSort("name")}
                          >
                            Name
                            {sortConfig.key === "name" && (
                              <ArrowUpDown
                                size={14}
                                className="ml-1.5 inline"
                              />
                            )}
                          </Button>
                        </TableHead>
                        <TableHead>
                          <Button
                            variant="ghost"
                            className="p-0 hover:bg-transparent font-medium"
                            onClick={() => handleSort("date")}
                          >
                            Date
                            {sortConfig.key === "date" && (
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
                        <TableHead>Tag</TableHead>
                        <TableHead>Sub-Tag</TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead>Project</TableHead>
                        <TableHead>
                          <Button
                            variant="ghost"
                            className="p-0 hover:bg-transparent font-medium"
                            onClick={() => handleSort("uploadDate")}
                          >
                            Upload Date
                            {sortConfig.key === "uploadDate" && (
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
                      {paginatedDocuments.map((doc) => (
                        <TableRow key={doc.id} className="text-sm">
                          <TableCell>
                            <div className="w-8 h-8 rounded-md bg-gray-100 flex items-center justify-center">
                              <FileText
                                size={14}
                                className="text-docsumo-blue"
                              />
                            </div>
                          </TableCell>
                          <TableCell className="font-medium max-w-[200px] truncate">
                            {doc.name}
                          </TableCell>
                          <TableCell>
                            {new Date(doc.date).toLocaleDateString()}
                          </TableCell>
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
                          <TableCell className="max-w-[200px] truncate">
                            {doc.subject}
                          </TableCell>
                          <TableCell>{doc.tag}</TableCell>
                          <TableCell className="max-w-[150px] truncate">
                            {doc.subTag}
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
                            {new Date(doc.uploadDate).toLocaleDateString()}
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
                                  <DropdownMenuItem
                                    onClick={() => handleRequestDraft(doc.id)}
                                  >
                                    <PenSquare size={14} className="mr-2" />
                                    Request Draft
                                  </DropdownMenuItem>
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
                                          variant="neutral"
                                          className="ml-2 text-xs"
                                        >
                                          {doc.references.length}
                                        </Badge>
                                      )}
                                  </DropdownMenuItem>
                                  <DropdownMenuSeparator />
                                  <DropdownMenuItem
                                    className="text-red-500 focus:text-red-500"
                                    onClick={() => handleDeleteDocument(doc.id)}
                                  >
                                    <Trash2 size={14} className="mr-2" />
                                    Delete
                                  </DropdownMenuItem>
                                </DropdownMenuContent>
                              </DropdownMenu>
                            </div>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
                {totalPages > 0 && (
                  <div className="flex items-center justify-between space-x-2 py-4">
                    <div className="text-sm text-gray-500">
                      Showing {startIndex + 1} to{" "}
                      {Math.min(startIndex + documentsPerPage, totalDocuments)}{" "}
                      of {totalDocuments} documents
                    </div>
                    <div className="flex items-center space-x-2">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() =>
                          setCurrentPage((p) => Math.max(1, p - 1))
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
                            onClick={() => setCurrentPage(i + 1)}
                          >
                            {i + 1}
                          </Button>
                        ))}
                      </div>

                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() =>
                          setCurrentPage((p) => Math.min(totalPages, p + 1))
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
