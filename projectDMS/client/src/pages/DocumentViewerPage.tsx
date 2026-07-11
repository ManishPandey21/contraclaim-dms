// DocumentViewerPage.tsx
import React, { useState, useEffect, useCallback, useMemo, Suspense } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Button } from "@/components/ui/button";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { toast } from "sonner";
import { AlertTriangle, FileText, RefreshCw } from "lucide-react";
import {
  enhancedApi,
  Document,
  type DocumentProcessingJobStatus,
} from "@/services/enhanced-api";
import { joinApiUrl } from "@/config/api";
import { authenticatedFetch } from "@/services/http";
import { listSubTags, listTags } from "@/services/tags-api";
import RouteSkeleton from "@/components/layout/RouteSkeleton";

// Import our new components
import DocumentHeader from "@/components/document-viewer/DocumentHeader";
import MetadataEditor from "@/components/document-viewer/MetadataEditor";
import EnclosuresPanel from "@/components/document-viewer/EnclosuresPanel";
import ReferencesPanel from "@/components/document-viewer/ReferencesPanel";
import DocumentDetailsPanel from "@/components/document-viewer/DocumentDetailsPanel";

const DocumentViewer = React.lazy(
  () => import("@/components/document-viewer/DocumentViewer")
);

// Define interfaces and types
interface DocumentReference {
  id: string;
  name: string;
  date: string;
  uploadType: "Incoming" | "Outgoing";
  letterNo: string;
  subject: string;
  linkType?: "direct" | "indirect";
}

// Use the Document type from enhanced API, but extend it for local use
export interface LocalDocument extends Document {
  id?: string; // For backward compatibility
  pages?: number;
  modifiedAt?: string;
  size?: string;
  version?: string;
  from_?: string; // Add this field if it's not in the base Document
  filetype?: string;   // or required if always present
  filename?: string;
  tags?: string[]; // Assuming tags are an array of strings
  subTags?: string[]; // Assuming subTags are an array of strings
  status: string; // Make required to match base Document interface
  createdAt: string; // Make required to match base Document interface
  updatedAt?: string;
  createdBy: string; // Make required to match base Document interface
}

type MetadataFieldName =
  | "uploadType"
  | "date"
  | "letterNo"
  | "subject"
  | "from_"
  | "to"
  | "tag"
  | "subTag"
  | "status";

interface MetadataField {
  id: MetadataFieldName;
  label: string;
  value: string;
  type: "select" | "text" | "textarea" | "date" | "radio";
  options?: string[];
}

// Document Viewer Page Component
const DocumentViewerPage: React.FC = () => {
  const { id: documentID } = useParams<{ id: string }>(); // Specify type for useParams
  const [showMetadata, setShowMetadata] = useState(true);
  const [searchParams] = useSearchParams();
  const allowedTabs = new Set([
    "metadata",
    "enclosure",
    "references",
    "details",
  ]);
  const initialTabParam = (searchParams.get("tab") || "metadata").toLowerCase();
  const initialActiveTab = allowedTabs.has(initialTabParam)
    ? initialTabParam
    : "metadata";
  const [activeTab, setActiveTab] = useState(initialActiveTab);
  useEffect(() => {
    const t = (searchParams.get("tab") || "").toLowerCase();
    if (allowedTabs.has(t) && t !== activeTab) {
      setActiveTab(t);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams]);
  const [isShareDialogOpen, setIsShareDialogOpen] = useState(false);
  const [metadataFields, setMetadataFields] = useState<MetadataField[]>([]);
  const [isLinkReferenceDialogOpen, setIsLinkReferenceDialogOpen] =
    useState(false);
  const [linkedReferences, setLinkedReferences] = useState<DocumentReference[]>(
    []
  );
  const [availableDocuments, setAvailableDocuments] = useState<
    DocumentReference[]
  >([]);
  const [document, setDocument] = useState<LocalDocument | null>(null); // Use LocalDocument
  const [isLoading, setIsLoading] = useState(true);
  const [isError, setIsError] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [processingStatus, setProcessingStatus] =
    useState<DocumentProcessingJobStatus | null>(null);
  const [isRetryingProcessing, setIsRetryingProcessing] = useState(false);
  const [createdByName, setCreatedByName] = useState<string>("");
  const [usersMap, setUsersMap] = useState<Record<string, string>>({});
  const [isLoadingReferences, setIsLoadingReferences] = useState(false);
  const [isLoadingAvailableDocuments, setIsLoadingAvailableDocuments] =
    useState(false);
  const [uploadType, setUploadType] = useState<"incoming" | "outgoing">(
    "incoming"
  );

  const documentId = documentID;

  const [availableTags, setAvailableTags] = useState<
    { value: string; label: string }[]
  >([]);
  const [availableSubtags, setAvailableSubtags] = useState<
    { value: string; label: string; tagId: string }[]
  >([]);
  const [isLoadingSubtags, setIsLoadingSubtags] = useState(false);

  // Track the currently selected tag to update subtag options dynamically
  const [currentSelectedTag, setCurrentSelectedTag] = useState<string>("");

  const resolveOptionValue = useCallback(
    (
      value: string | undefined,
      options: { value: string; label: string }[]
    ): string => {
      if (!value) return "";
      return (
        options.find((option) => option.value === value || option.label === value)
          ?.value || value
      );
    },
    []
  );

  const fetchDocument = useCallback(async () => {
    if (!documentId) {
      setIsLoading(false);
      return;
    }
    setIsLoading(true);
    setIsError(false);
    setErrorMessage(null);
    try {
      const data: Document = await enhancedApi.getDocument(documentId);
      // Map to LocalDocument and normalize uploadType casing
      const localDoc: LocalDocument = {
        ...data,
        // Support both keys from backend responses
        from_: (data as any).from ?? (data as any).from_ ?? "",

          uploadType: data.uploadType?.toLowerCase() === "incoming" ? "incoming" : "outgoing",
  tags: Array.isArray((data as any).tags) ? (data as any).tags : [],
  subTags: Array.isArray((data as any).subTags) ? (data as any).subTags : [],
      };
      setDocument(localDoc);
      setUploadType(localDoc.uploadType);
      setCurrentSelectedTag(localDoc.tags?.[0] || "");
    } catch (error) {
      console.error("Error fetching document:", error);
      setIsError(true);
      setErrorMessage(
        error instanceof Error ? error.message : "Failed to fetch document"
      );
      toast.error("Failed to fetch document", {
        description: "Please try again later",
      });
    } finally {
      setIsLoading(false);
    }
  }, [documentId]);

  const fetchProcessingStatus = useCallback(async () => {
    if (!documentId) return null;
    try {
      const status = await enhancedApi.getDocumentProcessingStatus(documentId);
      setProcessingStatus(status);
      return status;
    } catch (error) {
      console.warn("Unable to fetch processing status", error);
      const fallbackStatus = {
        _id: "",
        document_id: documentId,
        status: "not_queued",
        attempts: 0,
        max_attempts: 3,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      };
      setProcessingStatus(fallbackStatus);
      return fallbackStatus;
    }
  }, [documentId]);

  const retryProcessing = useCallback(async () => {
    if (!documentId) return;
    setIsRetryingProcessing(true);
    try {
      const result = await enhancedApi.processDocument(documentId);
      setProcessingStatus((prev) =>
        result.job_id
          ? {
              _id: result.job_id,
              document_id: documentId,
              status: result.status || "queued",
              stage: "queued",
              attempts: 0,
              max_attempts: 3,
              error: null,
              metadata: null,
              created_at: new Date().toISOString(),
              updated_at: new Date().toISOString(),
            }
          : prev
      );
      toast.success("Document processing queued");
    } catch (error) {
      toast.error("Failed to queue processing", {
        description:
          error instanceof Error ? error.message : "Unknown error occurred",
      });
    } finally {
      setIsRetryingProcessing(false);
    }
  }, [documentId]);

  useEffect(() => {
    if (!documentId) return;
    let cancelled = false;
    let completedRefreshDone = false;
    const terminal = new Set(["completed", "failed", "dead_lettered", "not_queued"]);

    const currentStatus = processingStatus?.status;
    if (currentStatus && terminal.has(currentStatus)) {
      return;
    }

    const poll = async () => {
      const status = await fetchProcessingStatus();
      if (cancelled || !status) return;
      if (status.status === "completed" && !completedRefreshDone) {
        completedRefreshDone = true;
        await fetchDocument();
      }
    };

    void poll();
    const interval = window.setInterval(() => {
      if (cancelled) return;
      const current = processingStatus?.status;
      if (current && terminal.has(current)) {
        window.clearInterval(interval);
        return;
      }
      void poll();
    }, 4000);

    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
  }, [documentId, fetchDocument, fetchProcessingStatus, processingStatus?.status]);

  const fetchAvailableAndLinkedDocuments = useCallback(async () => {
    if (!documentId) return;
    setIsLoadingAvailableDocuments(true);
    setIsLoadingReferences(true);
    try {
      const linkedResponse = await authenticatedFetch(
        joinApiUrl(`/documents/${documentId}/references`),
        {}
      );
      if (!linkedResponse.ok) {
        const errorData = await linkedResponse.json();
        throw new Error(errorData.detail || "Failed to fetch linked documents");
      }
      const rawLinkedData = await linkedResponse.json();
      const linkedData = Array.isArray(rawLinkedData) ? rawLinkedData : (rawLinkedData.linked || []);
      const linkedDocumentIds = linkedData.map((ref: any) => ref.documentId);

      // Use lowercase for API calls
      const oppositeDirection =
        uploadType === "incoming" ? "outgoing" : "incoming";
      const availableResponse = await authenticatedFetch(
        joinApiUrl(
          `/documents?uploadType=${oppositeDirection}&excludeIds=${linkedDocumentIds.join(
            ","
          )}&project_id=${document?.project_id || ""}`
        ),
        {}
      );
      if (!availableResponse.ok) {
        const errorData = await availableResponse.json();
        throw new Error(
          errorData.detail || "Failed to fetch available documents"
        );
      }
      const availableData = await availableResponse.json();

      const formattedAvailableData = availableData.documents.map(
        (doc: any) => ({
          id: doc._id,
          name: doc.filename,
          date: doc.date,
          uploadType:
            doc.uploadType?.toLowerCase() === "incoming"
              ? "Incoming"
              : "Outgoing", // Normalize for frontend
          letterNo: doc.letterNo,
          subject: doc.subject,
        })
      );
      setAvailableDocuments(formattedAvailableData);

      const linkedDocumentsDetailsPromises = linkedData.map((ref: any) =>
        authenticatedFetch(joinApiUrl(`/documents/${ref.documentId}`)).then(async (docResponse) => {
          if (!docResponse.ok) {
            const errorData = await docResponse.json();
            throw new Error(
              errorData.detail ||
                `Failed to fetch details for document ${ref.documentId}`
            );
          }
          const docData = await docResponse.json();
          return {
            id: docData.id,
            name: docData.filename,
            date: docData.date,
            uploadType:
              docData.uploadType?.toLowerCase() === "incoming"
                ? "Incoming"
                : "Outgoing", // Normalize for frontend
            letterNo: docData.letterNo,
            subject: docData.subject,
            linkType: ref.linkType,
          };
        })
      );
      const formattedLinkedData = await Promise.all(
        linkedDocumentsDetailsPromises
      );
      setLinkedReferences(formattedLinkedData);
    } catch (error) {
      console.error("Error fetching documents:", error);
      toast.error("Failed to fetch documents", {
        description: "Please try again later",
      });
    } finally {
      setIsLoadingAvailableDocuments(false);
      setIsLoadingReferences(false);
    }
  }, [documentId, uploadType, document?.project_id]);

  const fetchSubtags = useCallback(async (tagId: string) => {
    if (!tagId) {
      setAvailableSubtags([]); // Clear subtags if no tagId
      return;
    }

    setIsLoadingSubtags(true);
    try {
      const subtagArray = await listSubTags(tagId, { limit: 200 });

      setAvailableSubtags((prevSubtags) => {
        // Filter out subtags belonging to this tagId before adding new ones
        const otherSubtags = prevSubtags.filter(
          (subtag) => subtag.tagId !== tagId
        );
        const newSubtags = subtagArray
          .map((subtag) => ({
            value: subtag._id,
            label: subtag.name,
            tagId: tagId,
          }))
          .filter((subtag) => subtag.value && subtag.label);
        return [...otherSubtags, ...newSubtags];
      });
    } catch (error) {
      console.error("Error fetching subtags:", error);
      toast.error("Failed to fetch subtags", {
        description: "Please try again later",
      });
      // Set empty array on error to prevent UI issues
      setAvailableSubtags((prevSubtags) =>
        prevSubtags.filter((subtag) => subtag.tagId !== tagId)
      );
    } finally {
      setIsLoadingSubtags(false);
    }
  }, []);

  const getSubtagOptions = useCallback(
    (selectedTagValue: string | undefined): string[] => {
      const tagId = resolveOptionValue(selectedTagValue, availableTags);
      if (!tagId) return [];
      const options = availableSubtags
        .filter((subtag) => subtag.tagId === tagId)
        .map((subtag) => subtag.label);
      return options;
    },
    [availableTags, availableSubtags, resolveOptionValue]
  );

  useEffect(() => {
    fetchDocument();
  }, [fetchDocument]);

  // Prefetch users to resolve names in Details panel
  useEffect(() => {
    const loadUsers = async () => {
      try {
        const users = await enhancedApi.getUsers();
        const map: Record<string, string> = {};
        users.forEach((u: any) => {
          const key = u.id || u._id;
          if (key) {
            map[key] = u.username || u.email || key;
          }
        });
        setUsersMap(map);
      } catch {
        // ignore; we will fallback to per-user fetch if needed
      }
    };
    loadUsers();
  }, []);

  // Resolve creator name for Details panel (show name not ID)
  useEffect(() => {
    const loadCreator = async () => {
      const id = document?.createdBy;
      if (!id) {
        setCreatedByName("");
        return;
      }
      // Try preloaded map first
      if (usersMap[id]) {
        setCreatedByName(usersMap[id]);
        return;
      }
      try {
        const user = await enhancedApi.getUser(id);
        setCreatedByName(user.username || (user as any).email || id);
      } catch (e) {
        setCreatedByName(id);
      }
    };
    loadCreator();
  }, [document?.createdBy, usersMap]);

  useEffect(() => {
    fetchAvailableAndLinkedDocuments();
  }, [fetchAvailableAndLinkedDocuments]);

  // Fetch tags on component mount
  useEffect(() => {
    const fetchTags = async () => {
      try {
        const tagsData = await listTags({ limit: 200 });
        const formattedTags = tagsData.tags
          .map((tag) => ({
            value: tag._id,
            label: tag.name,
          }))
          .filter((tag) => tag.value && tag.label);
        setAvailableTags(formattedTags);
      } catch (error) {
        console.error("Error fetching tags:", error);
        toast.error("Failed to fetch tags", {
          description: "Please try again later",
        });
      }
    };
    fetchTags();
  }, []);

  // Effect to initialize metadataFields when document or tags/subtags change
  useEffect(() => {
    if (document) {
      const currentTagValue = resolveOptionValue(
        document.tags?.[0],
        availableTags
      );

      const currentSubTagValue = resolveOptionValue(
        document.subTags?.[0],
        availableSubtags
      );

      // Format date for HTML date input (YYYY-MM-DD)
      const formatDateForInput = (dateString: string) => {
        if (!dateString) return "";
        try {
          const date = new Date(dateString);
          return date.toISOString().split("T")[0]; // Get YYYY-MM-DD format
        } catch (error) {
          console.error("Error formatting date:", error);
          return "";
        }
      };

      setMetadataFields([
        {
          id: "uploadType",
          label: "Direction",
          value: document.uploadType === "incoming" ? "Incoming" : "Outgoing",
          type: "radio",
          options: ["Incoming", "Outgoing"],
        },
        {
          id: "date",
          label: "Date",
          value: formatDateForInput(document.date),
          type: "date",
        },
        {
          id: "letterNo",
          label: "Letter No.",
          value: document.letterNo || "",
          type: "text",
        },
        {
          id: "subject",
          label: "Subject",
          value: document.subject || "",
          type: "text",
        },
        {
          id: "from_",
          label: "From",
          value: document.from_ || "",
          type: "text",
        },
        {
          id: "to",
          label: "To",
          value: document.to || "",
          type: "text",
        },
        {
          id: "tag",
          label: "Tag",
          value: currentTagValue,
          type: "select",
          options: availableTags.map((tag) => tag.label),
        },
        {
          id: "subTag",
          label: "Sub-Tag",
          value: currentSubTagValue,
          type: "select",
          options: getSubtagOptions(currentTagValue), // Use the memoized getter
        },
        {
          id: "status",
          label: "Status",
          value: document.status || "",
          type: "select",
          options: [
            "--- Incoming ---",
            "Received",
            "Input Required",
            "On Hold",
            "Under Review",
            "Under Process",
            "Replied",
            "Forwarded",
            "Completed",
            "--- Outgoing ---",
            "Closed",
            "Reply Received",
            "No Reply Received",
            "Reply Overdue",
            "--- Legacy ---",
            "Draft",
            "Pending Review",
            "Pending Reply",
            "Reply Not Required",
          ],
        },
      ]);
    }
  }, [
    document,
    availableTags,
    availableSubtags,
    getSubtagOptions,
    resolveOptionValue,
  ]);

  useEffect(() => {
    if (!document?.tags?.[0]) return;
    const resolvedTag = resolveOptionValue(document.tags[0], availableTags);
    if (resolvedTag && resolvedTag !== currentSelectedTag) {
      setCurrentSelectedTag(resolvedTag);
    }
  }, [availableTags, currentSelectedTag, document?.tags, resolveOptionValue]);

  // Effect to fetch subtags when the selected tag changes
  useEffect(() => {
    if (currentSelectedTag) {
      fetchSubtags(currentSelectedTag);
    }
  }, [currentSelectedTag, fetchSubtags]);

  // Effect to update subtag options in metadataFields when availableSubtags changes
  useEffect(() => {
    setMetadataFields((prevFields) => {
      const newOptions = getSubtagOptions(currentSelectedTag);
      return prevFields.map((field) =>
        field.id === "subTag" ? { ...field, options: newOptions } : field
      );
    });
  }, [availableSubtags, getSubtagOptions, currentSelectedTag]);

  const handleMetadataChange = useCallback(
    (id: MetadataFieldName, value: string) => {
      setDocument((prevDoc) => {
        if (!prevDoc) return null;

        const updatedDoc = { ...prevDoc };

        switch (id) {
          case "uploadType":
            updatedDoc.uploadType = value.toLowerCase() as
              | "incoming"
              | "outgoing";
            setUploadType(value.toLowerCase() as "incoming" | "outgoing");
            break;
          case "date":
            updatedDoc.date = value;
            break;
          case "letterNo":
            updatedDoc.letterNo = value;
            break;
          case "subject":
            updatedDoc.subject = value;
            break;
          case "from_":
            updatedDoc.from_ = value;
            break;
          case "to":
            updatedDoc.to = value;
            break;
          case "tag": {
            const selectedTag = availableTags.find(
              (tag) => tag.value === value || tag.label === value
            );
            if (selectedTag) {
              updatedDoc.tags = [selectedTag.value];
              // Clear subtag when tag changes
              updatedDoc.subTags = [];
              setCurrentSelectedTag(selectedTag.value);
            } else {
              setAvailableSubtags([]);
            }
            break;
          }
          case "subTag": {
            const selectedSubTag = availableSubtags.find(
              (subtag) => subtag.value === value || subtag.label === value
            );
            if (selectedSubTag) {
              updatedDoc.subTags = [selectedSubTag.value];
            }
            break;
          }
          case "status":
            updatedDoc.status = value;
            break;
          default:
            break;
        }

        return updatedDoc;
      });
    },
    [availableTags, availableSubtags]
  );

  // Build lightweight activity timeline for Details tab
  type ActivityItem = {
    id: string;
    action: string;
    timestamp: string;
    user?: string;
  };
  const activityItems = useMemo<ActivityItem[]>(() => {
    if (!document) return [];
    const items: ActivityItem[] = [];
    // Created
    if (document.createdAt) {
      items.push({
        id: "created",
        action: "created the document",
        timestamp: document.createdAt,
        user: createdByName || document.createdBy,
      });
    }
    // Metadata updated
    const modifiedAt = document.updatedAt || document.createdAt;
    if (modifiedAt && modifiedAt !== document.createdAt) {
      items.push({
        id: "metadata_updated",
        action: "updated metadata",
        timestamp: modifiedAt,
        user: createdByName || document.createdBy,
      });
    }
    // Status snapshot
    if (document.status) {
      items.push({
        id: "status",
        action: `status changed to ${document.status}`,
        timestamp: modifiedAt || document.createdAt,
      });
    }
    // Sort newest first
    return items.sort(
      (a, b) =>
        new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
    );
  }, [document, createdByName]);

  const relatedDocuments = [
    {
      id: "001",
      name: "Q3 Financial Report.pdf",
      date: "2023-09-15",
      type: "PDF",
    },
    {
      id: "002",
      name: "Annual Budget 2023.xlsx",
      date: "2023-01-10",
      type: "XLSX",
    },
    {
      id: "003",
      name: "Financial Projections 2024.pdf",
      date: "2023-12-22",
      type: "PDF",
    },
  ];

  if (isLoading) {
    return <RouteSkeleton />;
  }

  if (isError || !document) {
    return (
      <div className="min-h-screen bg-gray-50 p-6">
        <div className="mx-auto flex max-w-xl flex-col items-center justify-center rounded-lg border bg-white p-8 text-center shadow-sm">
          <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-red-50">
            <AlertTriangle className="h-6 w-6 text-red-600" />
          </div>
          <h1 className="text-lg font-semibold">Unable to load document</h1>
          <p className="mt-2 text-sm text-muted-foreground">
            {errorMessage || "The document could not be loaded. Check your access or retry."}
          </p>
          <div className="mt-6 flex gap-3">
            <Button variant="outline" onClick={() => window.history.back()}>
              Back
            </Button>
            <Button onClick={() => void fetchDocument()}>
              <RefreshCw className="mr-2 h-4 w-4" />
              Retry
            </Button>
          </div>
        </div>
      </div>
    );
  }

  const effectiveProcessingStatus =
    processingStatus?.status || document.processing_status || "not_queued";
  const processingError =
    processingStatus?.error || document.processing_error || null;
  const showProcessingBanner = [
    "queued",
    "processing",
    "retrying",
    "failed",
    "dead_lettered",
    "metadata_extracted",
    "completed",
  ].includes(effectiveProcessingStatus);
  const processingLabel =
    effectiveProcessingStatus === "completed"
      ? "Metadata extracted"
      : effectiveProcessingStatus === "metadata_extracted"
        ? "Metadata extracted; indexing is finishing"
        : effectiveProcessingStatus === "failed" ||
            effectiveProcessingStatus === "dead_lettered"
          ? "Metadata extraction failed"
          : effectiveProcessingStatus === "retrying"
            ? "Metadata extraction retrying"
            : effectiveProcessingStatus === "queued"
              ? "Metadata extraction queued"
              : "Metadata extraction in progress";

  return (
    <div className="h-screen flex flex-col bg-gray-100">
      <DocumentHeader
        document={document}
        showMetadata={showMetadata}
        setShowMetadata={setShowMetadata}
        isShareDialogOpen={isShareDialogOpen}
        setIsShareDialogOpen={setIsShareDialogOpen}
        isLinkReferenceDialogOpen={isLinkReferenceDialogOpen}
        setIsLinkReferenceDialogOpen={setIsLinkReferenceDialogOpen}
        relatedDocuments={relatedDocuments}
      />
      {showProcessingBanner && (
        <div
          className={`border-b px-4 py-2 text-sm ${
            effectiveProcessingStatus === "failed" ||
            effectiveProcessingStatus === "dead_lettered"
              ? "border-red-200 bg-red-50 text-red-800"
              : effectiveProcessingStatus === "completed"
                ? "border-green-200 bg-green-50 text-green-800"
                : "border-amber-200 bg-amber-50 text-amber-900"
          }`}
        >
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <span className="font-medium">{processingLabel}</span>
              {processingStatus?.stage && (
                <span className="ml-2 text-xs opacity-80">
                  Stage: {processingStatus.stage}
                </span>
              )}
              {processingError?.message && (
                <span className="ml-2 text-xs opacity-90">
                  {String(processingError.message)}
                </span>
              )}
            </div>
            <div className="flex items-center gap-2">
              {processingError && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => window.history.back()}
                >
                  Back
                </Button>
              )}
              {(effectiveProcessingStatus === "failed" ||
                effectiveProcessingStatus === "dead_lettered") && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => void retryProcessing()}
                  disabled={isRetryingProcessing}
                >
                  <RefreshCw className="mr-2 h-4 w-4" />
                  {isRetryingProcessing ? "Retrying..." : "Retry Processing"}
                </Button>
              )}
            </div>
          </div>
        </div>
      )}
      <div className="flex-1 overflow-hidden">
        <ResizablePanelGroup direction="horizontal">
          <ResizablePanel defaultSize={75} minSize={50}>
            <Suspense
              fallback={
                <div className="flex h-full min-h-[480px] flex-col items-center justify-center bg-white text-muted-foreground">
                  <FileText className="mb-3 h-8 w-8" />
                  <p className="text-sm">Loading document preview...</p>
                </div>
              }
            >
              <DocumentViewer document={document} />
            </Suspense>
          </ResizablePanel>

          {showMetadata && <ResizableHandle withHandle />}

          {showMetadata && (
            <ResizablePanel defaultSize={25} minSize={20}>
              <div className="h-full min-h-0 flex flex-col">
                <Tabs
                  value={activeTab}
                  onValueChange={setActiveTab}
                  className="w-full h-full min-h-0 flex flex-col"
                >
                  <div className="bg-white border-b p-3">
                    <TabsList className="grid w-full grid-cols-4">
                      <TabsTrigger value="metadata">Metadata</TabsTrigger>
                      <TabsTrigger value="enclosure">Enclosures</TabsTrigger>
                      <TabsTrigger value="references">References</TabsTrigger>
                      <TabsTrigger value="details">Details</TabsTrigger>
                    </TabsList>
                  </div>

                  <ScrollArea className="flex-1 min-h-0">
                    <div className="p-4 space-y-5">
                      <TabsContent value="metadata" className="m-0">
                        <MetadataEditor
                          metadataFields={metadataFields}
                          onMetadataChange={handleMetadataChange}
                          documentId={documentId!}
                          setMetadataFields={setMetadataFields}
                          availableTags={availableTags}
                          availableSubtags={availableSubtags}
                          isLoadingSubtags={isLoadingSubtags}
                        />
                      </TabsContent>

                      <TabsContent value="references" className="m-0">
                        <ReferencesPanel
                          documentId={documentId}
                          projectId={document?.project_id}
                          uploadType={
                            uploadType === "incoming" ? "Incoming" : "Outgoing"
                          }
                          linkedReferences={linkedReferences}
                          setLinkedReferences={setLinkedReferences}
                          availableDocuments={availableDocuments}
                          isLinkReferenceDialogOpen={isLinkReferenceDialogOpen}
                          setIsLinkReferenceDialogOpen={
                            setIsLinkReferenceDialogOpen
                          }
                        />
                      </TabsContent>

                      <TabsContent value="details" className="m-0">
                        <DocumentDetailsPanel
                          document={{
                            createdAt: document.createdAt,
                            modifiedAt:
                              document.updatedAt || document.createdAt,
                            createdBy: createdByName || document.createdBy,
                            version: document.version || "1.0",
                            tags: (document.tags || []).map((id) => {
                              const match = availableTags.find(
                                (t) => t.value === id
                              );
                              return match ? match.label : id;
                            }),
                          }}
                          activity={activityItems}
                        />
                      </TabsContent>

                      <TabsContent value="enclosure" className="m-0">
                        <EnclosuresPanel documentId={documentId} />
                      </TabsContent>
                    </div>
                  </ScrollArea>
                </Tabs>
              </div>
            </ResizablePanel>
          )}
        </ResizablePanelGroup>
      </div>
    </div>
  );
};

export default DocumentViewerPage;
