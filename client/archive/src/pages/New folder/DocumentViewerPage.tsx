// DocumentViewerPage.tsx
import React, { useState, useEffect, useCallback } from "react";
import { useParams } from "react-router-dom";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { toast } from "sonner";
import { enhancedApi, Document } from "@/services/enhanced-api";

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
  status?: string;
  createdAt?: string; // Assuming these are part of Document
  updatedAt?: string;
  createdBy?: string;
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
  const [activeTab, setActiveTab] = useState("metadata");
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
  const [isLoadingReferences, setIsLoadingReferences] = useState(false);
  const [isLoadingAvailableDocuments, setIsLoadingAvailableDocuments] =
    useState(false);
  const [uploadType, setUploadType] = useState<"Incoming" | "Outgoing">(
    "Incoming"
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
        from_: (data as any).from_ || "", // Assuming from_ might be a dynamic property
        uploadType:
          data.uploadType?.toLowerCase() === "incoming"
            ? "Incoming"
            : "Outgoing",
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
        `/api/documents/${documentId}/references`,
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
        uploadType === "Incoming" ? "outgoing" : "incoming";
      const availableResponse = await fetch(
        `/api/documents?uploadType=${oppositeDirection}&excludeIds=${linkedDocumentIds.join(
          ","
        )}`,
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

      const formattedAvailableData = availableData.map((doc: any) => ({
        id: doc._id,
        name: doc.filename,
        date: doc.date,
        uploadType:
          doc.uploadType?.toLowerCase() === "incoming"
            ? "Incoming"
            : "Outgoing", // Normalize for frontend
        letterNo: doc.letterNo,
        subject: doc.subject,
      }));
      setAvailableDocuments(formattedAvailableData);

      const linkedDocumentsDetailsPromises = linkedData.map((ref: any) =>
        fetch(`/api/documents/${ref.documentId}`, {
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
  }, [documentId, uploadType]);

  const fetchSubtags = useCallback(async (tagId: string) => {
    if (!tagId) {
      setAvailableSubtags([]); // Clear subtags if no tagId
      return;
    }

    setIsLoadingSubtags(true);
    try {
      const response = await fetch(`/api/tags/${tagId}/subtags`, {
        headers: {
          Authorization: `Bearer ${localStorage.getItem("accessToken")}`,
        },
      });
      if (!response.ok) {
        throw new Error("Failed to fetch subtags");
      }
      const subtagData = await response.json();

      setAvailableSubtags((prevSubtags) => {
        // Filter out subtags belonging to this tagId before adding new ones
        const otherSubtags = prevSubtags.filter(
          (subtag) => subtag.tagId !== tagId
        );
        const newSubtags = subtagData.map((subtag: any) => ({
          value: subtag._id,
          label: subtag.name,
          tagId: tagId,
        }));
        return [...otherSubtags, ...newSubtags];
      });
    } catch (error) {
      console.error("Error fetching subtags:", error);
      toast.error("Failed to fetch subtags", {
        description: "Please try again later",
      });
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

  useEffect(() => {
    fetchAvailableAndLinkedDocuments();
  }, [fetchAvailableAndLinkedDocuments]);

  // Fetch tags on component mount
  useEffect(() => {
    const fetchTags = async () => {
      try {
        const tagsResponse = await fetch("/api/tags", {
          headers: {
            Authorization: `Bearer ${localStorage.getItem("accessToken")}`,
          },
        });
        if (!tagsResponse.ok) {
          throw new Error("Failed to fetch tags");
        }
        const tagsData = await tagsResponse.json();
        const formattedTags = tagsData.map((tag: any) => ({
          value: tag._id,
          label: tag.name,
        }));
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
      const currentTag = document.tags?.[0] || "";
      // Ensure currentSelectedTag is set here as well for initial load
      setCurrentSelectedTag(currentTag);

      // Fetch subtags for the initial tag if available
      if (currentTag && availableTags.length > 0) {
        const tag = availableTags.find((t) => t.label === currentTag);
        if (tag) {
          fetchSubtags(tag.value);
        }
      }

      setMetadataFields([
        {
          id: "uploadType",
          label: "Direction",
          value: document.uploadType || "",
          type: "radio",
          options: ["Incoming", "Outgoing"],
        },
        {
          id: "date",
          label: "Date",
          value: document.date || "",
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
        { id: "to", label: "To", value: document.to || "", type: "text" },
        {
          id: "tag",
          label: "Tag",
          value: currentTag,
          type: "select",
          options: availableTags.map((tag) => tag.label),
        },
        {
          id: "subTag",
          label: "Sub-Tag",
          value: document.subTags?.[0] || "",
          type: "select",
          options: getSubtagOptions(currentTag), // Use the memoized getter
        },
        {
          id: "status",
          label: "Status",
          value: document.status || "",
          type: "select",
          options: ["Received", "Input Required", "Completed", "Under Process"],
        },
      ]);
    }
  }, [document, availableTags, fetchSubtags]); // Only depend on document and availableTags for initial setup

  // Effect to update subtag options when `availableSubtags` or `currentSelectedTag` changes
  // This ensures the dropdown options are always up-to-date
  useEffect(() => {
    setMetadataFields((prevFields) =>
      prevFields.map((field) =>
        field.id === "subTag"
          ? { ...field, options: getSubtagOptions(currentSelectedTag) }
          : field
      )
    );
  }, [availableSubtags, currentSelectedTag, getSubtagOptions]);

  const handleMetadataChange = useCallback(
    (id: MetadataFieldName, value: string) => {
      console.log(`Updating metadata field ${id} to ${value}`);

      setDocument((prevDoc) => {
        if (!prevDoc) return null;

        const updatedDoc = { ...prevDoc };

        switch (id) {
          case "uploadType":
            updatedDoc.uploadType = value as "Incoming" | "Outgoing";
            setUploadType(value as "Incoming" | "Outgoing");
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
          case "tag":
            updatedDoc.tags = [value];
            // Clear subtag when tag changes
            updatedDoc.subTags = [];
            setCurrentSelectedTag(value); // Update current selected tag state
            const selectedTag = availableTags.find(
              (tag) => tag.label === value
            );
            if (selectedTag) {
              fetchSubtags(selectedTag.value);
            } else {
              setAvailableSubtags([]); // Clear subtags if no tag is selected
            }
            break;
          case "subTag":
            updatedDoc.subTags = [value];
            break;
          case "status":
            updatedDoc.status = value;
            break;
          default:
            break;
        }
        return updatedDoc;
      });
    },
    [availableTags, fetchSubtags]
  ); // Dependencies for useCallback

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
              <div className="h-full flex flex-col">
                <div className="bg-white border-b p-3">
                  <Tabs
                    value={activeTab}
                    onValueChange={setActiveTab}
                    className="w-full"
                  >
                    <TabsList className="grid w-full grid-cols-4">
                      <TabsTrigger value="metadata">Metadata</TabsTrigger>
                      <TabsTrigger value="enclosure">Enclosures</TabsTrigger>
                      <TabsTrigger value="references">References</TabsTrigger>
                      <TabsTrigger value="details">Details</TabsTrigger>
                    </TabsList>

                    <TabsContent value="metadata" className="mt-0 space-y-5">
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

                    <TabsContent value="references" className="mt-0 space-y-5">
                      <ReferencesPanel
                        documentId={documentId}
                        uploadType={uploadType}
                        linkedReferences={linkedReferences}
                        setLinkedReferences={setLinkedReferences}
                        availableDocuments={availableDocuments}
                        isLinkReferenceDialogOpen={isLinkReferenceDialogOpen}
                        setIsLinkReferenceDialogOpen={
                          setIsLinkReferenceDialogOpen
                        }
                      />
                    </TabsContent>

                    <TabsContent value="details" className="mt-0 space-y-5">
                      <DocumentDetailsPanel
                        document={{
                          createdAt: document.createdAt,
                          modifiedAt: document.updatedAt || document.createdAt,
                          createdBy: document.createdBy,
                          version: document.version || "1.0", // Use document.version if available
                          tags: document.tags || [],
                        }}
                      />
                    </TabsContent>

                    <TabsContent value="enclosure" className="mt-0 space-y-5">
                      <EnclosuresPanel documentId={documentId} />
                    </TabsContent>
                  </Tabs>
                </div>

                <ScrollArea className="flex-1">
                  <div className="p-4">
                    {/* Content will be displayed via TabsContent above */}
                  </div>
                </ScrollArea>
              </div>
            </ResizablePanel>
          )}
        </ResizablePanelGroup>
      </div>
    </div>
  );
};

export default DocumentViewerPage;
