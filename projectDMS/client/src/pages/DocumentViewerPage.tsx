// DocumentViewerPage.tsx
import React, { useState, useEffect, useCallback, useMemo } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { toast } from "sonner";
import { enhancedApi, Document } from "@/services/enhanced-api";
import { joinApiUrl } from "@/config/api";

// Import our new components
import DocumentHeader from "@/components/document-viewer/DocumentHeader";
import DocumentViewer from "@/components/document-viewer/DocumentViewer";
import MetadataEditor from "@/components/document-viewer/MetadataEditor";
import EnclosuresPanel from "@/components/document-viewer/EnclosuresPanel";
import ReferencesPanel from "@/components/document-viewer/ReferencesPanel";
import DocumentDetailsPanel from "@/components/document-viewer/DocumentDetailsPanel";

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

  const fetchDocument = useCallback(async () => {
    if (!documentId) {
      setIsLoading(false);
      return;
    }
    setIsLoading(true);
    setIsError(false);
    try {
      const data: Document = await enhancedApi.getDocument(documentId);
      // Map to LocalDocument and normalize uploadType casing
      const localDoc: LocalDocument = {
        ...data,
        // Support both keys from backend responses
        from_: (data as any).from ?? (data as any).from_ ?? "",
        uploadType:
          data.uploadType?.toLowerCase() === "incoming"
            ? "incoming"
            : "outgoing",
        tags: (data as any).tags || [], // Ensure tags are an array
        subTags: (data as any).subTags || [], // Ensure subTags are an array
      };
      setDocument(localDoc);
      setUploadType(localDoc.uploadType);
      setCurrentSelectedTag(localDoc.tags?.[0] || "");
    } catch (error) {
      console.error("Error fetching document:", error);
      setIsError(true);
      toast.error("Failed to fetch document", {
        description: "Please try again later",
      });
    } finally {
      setIsLoading(false);
    }
  }, [documentId]);

  const fetchAvailableAndLinkedDocuments = useCallback(async () => {
    if (!documentId) return;
    setIsLoadingAvailableDocuments(true);
    setIsLoadingReferences(true);
    try {
      const linkedResponse = await fetch(
        joinApiUrl(`/documents/${documentId}/references`),
        {
          headers: {
            Authorization: `Bearer ${localStorage.getItem("accessToken")}`,
          },
        }
      );
      if (!linkedResponse.ok) {
        const errorData = await linkedResponse.json();
        throw new Error(errorData.detail || "Failed to fetch linked documents");
      }
      const linkedData = await linkedResponse.json();
      const linkedDocumentIds = linkedData.map((ref: any) => ref.documentId);

      // Use lowercase for API calls
      const oppositeDirection =
        uploadType === "incoming" ? "outgoing" : "incoming";
      const availableResponse = await fetch(
        joinApiUrl(
          `/documents?uploadType=${oppositeDirection}&excludeIds=${linkedDocumentIds.join(
            ","
          )}&project_id=${document?.project_id || ""}`
        ),
        {
          headers: {
            Authorization: `Bearer ${localStorage.getItem("accessToken")}`,
          },
        }
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
        fetch(joinApiUrl(`/documents/${ref.documentId}`), {
          headers: {
            Authorization: `Bearer ${localStorage.getItem("accessToken")}`,
          },
        }).then(async (docResponse) => {
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
      const response = await fetch(joinApiUrl(`/tags/${tagId}/subtags`), {
        headers: {
          Authorization: `Bearer ${localStorage.getItem("accessToken")}`,
        },
      });
      if (!response.ok) {
        throw new Error("Failed to fetch subtags");
      }
      const subtagData = await response.json();

      // Handle different response formats from the backend
      let subtagArray: any[] = [];
      if (Array.isArray(subtagData)) {
        subtagArray = subtagData;
      } else if (subtagData && Array.isArray(subtagData.subtags)) {
        subtagArray = subtagData.subtags;
      } else if (subtagData && typeof subtagData === "object") {
        // If it's an object but not an array, try to extract subtags
        subtagArray = Object.values(subtagData).filter(Array.isArray).flat();
      } else {
        console.warn("Unexpected subtag data format:", subtagData);
        subtagArray = [];
      }

      setAvailableSubtags((prevSubtags) => {
        // Filter out subtags belonging to this tagId before adding new ones
        const otherSubtags = prevSubtags.filter(
          (subtag) => subtag.tagId !== tagId
        );
        const newSubtags = subtagArray.map((subtag: any) => ({
          value: subtag._id || subtag.id || "",
          label: subtag.name || "",
          tagId: tagId,
        }));
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
    (selectedTagLabel: string | undefined): string[] => {
      if (!selectedTagLabel) return [];
      const tagId = availableTags.find(
        (tag) => tag.label === selectedTagLabel
      )?.value;
      if (!tagId) return [];
      const options = availableSubtags
        .filter((subtag) => subtag.tagId === tagId)
        .map((subtag) => subtag.label);
      return options;
    },
    [availableTags, availableSubtags]
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
        const token = localStorage.getItem("accessToken") || "";
        const headers: Record<string, string> = token
          ? { Authorization: `Bearer ${token}` }
          : {};
        const tagsResponse = await fetch(joinApiUrl("/tags"), {
          headers,
        });
        if (!tagsResponse.ok) {
          throw new Error("Failed to fetch tags");
        }
        const tagsData = await tagsResponse.json();
        const formattedTags = Array.isArray(tagsData)
          ? tagsData.map((tag: any) => ({
              value: tag._id,
              label: tag.name,
            }))
          : Array.isArray(tagsData.tags)
          ? tagsData.tags.map((tag: any) => ({
              value: tag._id,
              label: tag.name,
            }))
          : [];
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
      // Convert tag ID to label
      const currentTagId = document.tags?.[0] || "";
      const currentTagLabel = currentTagId
        ? availableTags.find((tag) => tag.value === currentTagId)?.label || ""
        : "";

      // Convert subtag ID to label
      const currentSubTagId = document.subTags?.[0] || "";
      const currentSubTagLabel = currentSubTagId
        ? availableSubtags.find((subtag) => subtag.value === currentSubTagId)
            ?.label || ""
        : "";

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
          value: currentTagLabel,
          type: "select",
          options: availableTags.map((tag) => tag.label),
        },
        {
          id: "subTag",
          label: "Sub-Tag",
          value: currentSubTagLabel,
          type: "select",
          options: getSubtagOptions(currentTagLabel), // Use the memoized getter
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
  }, [document, availableTags, availableSubtags, getSubtagOptions]);

  // Effect to fetch subtags when the selected tag changes
  useEffect(() => {
    if (currentSelectedTag) {
      const tagId = availableTags.find(
        (tag) => tag.label === currentSelectedTag
      )?.value;
      if (tagId) {
        fetchSubtags(tagId);
      }
    }
  }, [currentSelectedTag, availableTags, fetchSubtags]);

  // Effect to update subtag options in metadataFields when availableSubtags changes
  useEffect(() => {
    setMetadataFields((prevFields) => {
      const newOptions = getSubtagOptions(currentSelectedTag);
      return prevFields.map((field) =>
        field.id === "subTag" ? { ...field, options: newOptions } : field
      );
    });
  }, [availableSubtags, getSubtagOptions, currentSelectedTag]);

  const updateMetadata = async (updatedFields: Partial<LocalDocument>) => {
    if (!documentId) return;
    try {
      await enhancedApi.updateDocument(documentId, updatedFields);
      toast.success("Metadata updated successfully", {
        description: "All changes have been saved.",
      });
    } catch (error) {
      console.error("Error updating metadata:", error);
      toast.error("Failed to update metadata");
    }
  };

  const handleMetadataChange = useCallback(
    (id: MetadataFieldName, value: string) => {
      setDocument((prevDoc) => {
        if (!prevDoc) return null;

        const updatedDoc = { ...prevDoc };
        let backendUpdate: Partial<LocalDocument> = {};

        switch (id) {
          case "uploadType":
            updatedDoc.uploadType = value.toLowerCase() as
              | "incoming"
              | "outgoing";
            backendUpdate.uploadType = value.toLowerCase() as
              | "incoming"
              | "outgoing";
            setUploadType(value.toLowerCase() as "incoming" | "outgoing");
            break;
          case "date":
            updatedDoc.date = value;
            backendUpdate.date = value;
            break;
          case "letterNo":
            updatedDoc.letterNo = value;
            backendUpdate.letterNo = value;
            break;
          case "subject":
            updatedDoc.subject = value;
            backendUpdate.subject = value;
            break;
          case "from_":
            updatedDoc.from_ = value;
            // Backend expects the alias key "from" (Pydantic Field(alias="from"))
            // so send "from" instead of "from_"
            (backendUpdate as any).from = value;
            break;
          case "to":
            updatedDoc.to = value;
            backendUpdate.to = value;
            break;
          case "tag":
            const selectedTag = availableTags.find(
              (tag) => tag.label === value
            );
            if (selectedTag) {
              updatedDoc.tags = [selectedTag.value];
              backendUpdate.tags = [selectedTag.value];
              // Clear subtag when tag changes
              updatedDoc.subTags = [];
              backendUpdate.subTags = [];
              setCurrentSelectedTag(value);
            } else {
              setAvailableSubtags([]);
            }
            break;
          case "subTag":
            const selectedSubTag = availableSubtags.find(
              (subtag) => subtag.label === value
            );
            if (selectedSubTag) {
              updatedDoc.subTags = [selectedSubTag.value];
              backendUpdate.subTags = [selectedSubTag.value];
            }
            break;
          case "status":
            updatedDoc.status = value;
            backendUpdate.status = value;
            break;
          default:
            break;
        }

        if (Object.keys(backendUpdate).length > 0) {
          updateMetadata(backendUpdate);
        }

        return updatedDoc;
      });
    },
    [availableTags, availableSubtags, documentId]
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
    return <div>Loading...</div>;
  }

  if (isError || !document) {
    return <div>Error loading document.</div>;
  }

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
      <div className="flex-1 overflow-hidden">
        <ResizablePanelGroup direction="horizontal">
          <ResizablePanel defaultSize={75} minSize={50}>
            <DocumentViewer document={document} />
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
