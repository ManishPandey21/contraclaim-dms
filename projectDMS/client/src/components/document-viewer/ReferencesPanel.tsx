import React, { useEffect, useState } from "react";
import { joinApiUrl } from "@/config/api";
import { authenticatedFetch } from "@/services/http";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
  DialogClose,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHeader,
  TableHead,
  TableRow,
} from "@/components/ui/table";
import { Link as RouterLink } from "react-router-dom";
import { toast } from "sonner";
import {
  FileText,
  LinkIcon,
  PlusCircle,
  Trash,
  Loader2,
  ExternalLink,
  Search,
} from "lucide-react";
import { formatDate } from "@/utils/datetime";

interface DocumentReference {
  id: string;
  name: string;
  date: string;
  uploadType: "Incoming" | "Outgoing";
  letterNo: string;
  subject: string;
  linkType?: "direct" | "indirect";
}

interface ReferencesPanelProps {
  documentId: string;
  projectId?: string;
  uploadType: "Incoming" | "Outgoing";
  linkedReferences: DocumentReference[];
  setLinkedReferences: React.Dispatch<
    React.SetStateAction<DocumentReference[]>
  >;
  availableDocuments: DocumentReference[];
  isLinkReferenceDialogOpen: boolean;
  setIsLinkReferenceDialogOpen: React.Dispatch<React.SetStateAction<boolean>>;
}

async function readJsonSafe(resp: Response): Promise<any | null> {
  try {
    return await resp.json();
  } catch {
    return null;
  }
}

const ReferencesPanel: React.FC<ReferencesPanelProps> = ({
  documentId,
  projectId,
  uploadType,
  linkedReferences,
  setLinkedReferences,
  availableDocuments,
  isLinkReferenceDialogOpen,
  setIsLinkReferenceDialogOpen,
}) => {
  const [isLinking, setIsLinking] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");
  const [searchResults, setSearchResults] = useState<DocumentReference[]>(
    availableDocuments.slice(0, 10)
  );
  const [isSearching, setIsSearching] = useState(false);

  // Fetch current linked documents
  useEffect(() => {
    const fetchLinkedDocuments = async () => {
      if (!documentId) return;
      try {
        const response = await authenticatedFetch(
          joinApiUrl(`/documents/${documentId}/linked`),
          {}
        );
        if (!response.ok) {
          const err = await readJsonSafe(response);
          throw new Error(err?.detail || "Failed to fetch linked documents");
        }
        const refs = await response.json();
        // Fetch document details for each linked reference
        const formatted: DocumentReference[] = await Promise.all(
          (refs || []).map(async (ref: any) => {
            try {
              const docResponse = await authenticatedFetch(
                joinApiUrl(`/documents/${ref.documentId}`),
                {}
              );
              if (!docResponse.ok) throw new Error("doc fetch failed");
              const docData = await docResponse.json();
              return {
                id: ref.documentId,
                name: docData.filename || "Unknown Document",
                date: docData.date ? formatDate(docData.date) : "",
                uploadType: (docData.uploadType || "Incoming") as
                  | "Incoming"
                  | "Outgoing",
                letterNo: docData.letterNo || "",
                subject: docData.subject || "",
                linkType: ref.linkType,
              } as DocumentReference;
            } catch {
              return {
                id: ref.documentId,
                name: "Unknown Document",
                date: "",
                uploadType: "Incoming",
                letterNo: "",
                subject: "",
                linkType: ref.linkType,
              } as DocumentReference;
            }
          })
        );
        setLinkedReferences(formatted);
      } catch (error) {
        console.error("Error fetching linked documents:", error);
        toast.error("Failed to fetch linked documents", {
          description: "Please try again later",
        });
      }
    };

    fetchLinkedDocuments();
  }, [documentId, setLinkedReferences]);

  // Search available documents (Stakeholders search API)
  const handleSearch = async () => {
    if (!searchQuery.trim()) {
      setSearchResults(availableDocuments.slice(0, 10));
      return;
    }
    setIsSearching(true);
    try {
      const projectFilter = projectId ? `&project_id=${projectId}` : "";
      const response = await authenticatedFetch(
        joinApiUrl(
          `/stakeholders/documents/search?search=${encodeURIComponent(
            searchQuery
          )}&limit=20${projectFilter}`
        ),
        {}
      );
      if (!response.ok) throw new Error("Failed to search documents");
      const data = await response.json();
      const formattedResults: DocumentReference[] = data.map((doc: any) => ({
        id: doc.id,
        name: doc.document,
        date: doc.created_at ? formatDate(doc.created_at) : "",
        uploadType: (doc.document_type || "Incoming") as
          | "Incoming"
          | "Outgoing",
        letterNo: doc.letter_no || "",
        subject: doc.subject || "",
      }));
      setSearchResults(formattedResults);
    } catch (error) {
      console.error("Error searching documents:", error);
      toast.error("Failed to search documents", {
        description: "Please try again later",
      });
      // Fallback to local search
      const q = searchQuery.toLowerCase();
      const results = availableDocuments.filter(
        (doc) =>
          doc.name.toLowerCase().includes(q) ||
          doc.letterNo.toLowerCase().includes(q) ||
          doc.subject.toLowerCase().includes(q)
      );
      setSearchResults(results.slice(0, 10));
    } finally {
      setIsSearching(false);
    }
  };

  // Fetch a quick list of available documents on open if not provided
  const fetchAvailableDocuments = async () => {
    if (availableDocuments.length > 0) {
      setSearchResults(
        availableDocuments
          .filter(
            (d) =>
              d.id !== documentId &&
              !linkedReferences.find((r) => r.id === d.id)
          )
          .slice(0, 10)
      );
      return;
    }
    setIsSearching(true);
    try {
      const projectFilter = projectId ? `&project_id=${projectId}` : "";
      const response = await authenticatedFetch(
        joinApiUrl(`/documents?limit=20${projectFilter}`),
        {}
      );
      if (!response.ok) throw new Error("Failed to fetch documents");
      const data = await response.json();
      const formattedResults: DocumentReference[] = data.documents.map(
        (doc: any) => ({
          id: doc._id || doc.id,
          name: doc.filename || "Unknown Document",
          date: doc.date ? formatDate(doc.date) : "",
          uploadType: (doc.uploadType || "Incoming") as "Incoming" | "Outgoing",
          letterNo: doc.letterNo || "",
          subject: doc.subject || "",
        })
      );
      const filtered = formattedResults.filter(
        (doc) =>
          doc.id !== documentId &&
          !linkedReferences.some((ref) => ref.id === doc.id)
      );
      setSearchResults(filtered.slice(0, 10));
    } catch (error) {
      console.error("Error fetching available documents:", error);
      toast.error("Failed to fetch available documents", {
        description: "Please try again later",
      });
      setSearchResults([]);
    } finally {
      setIsSearching(false);
    }
  };

  // Add a direct reference
  const handleAddReference = async (referencedDocumentId: string) => {
    if (linkedReferences.some((ref) => ref.id === referencedDocumentId)) {
      toast.info("This document is already linked");
      return;
    }
    try {
      const response = await authenticatedFetch(
        joinApiUrl(`/documents/${documentId}/references`),
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            referenced_document_id: referencedDocumentId,
            link_type: "direct",
          }),
        }
      );
      if (!response.ok) {
        const err = await readJsonSafe(response);
        throw new Error(err?.detail || "Failed to add reference");
      }

      // Fetch details for the newly linked doc
      const docResponse = await authenticatedFetch(
        joinApiUrl(`/documents/${referencedDocumentId}`),
        {}
      );
      if (!docResponse.ok) {
        const err = await readJsonSafe(docResponse);
        throw new Error(
          err?.detail ||
            `Failed to fetch details for document ${referencedDocumentId}`
        );
      }
      const docData = await docResponse.json();
      const newReference: DocumentReference = {
        id: referencedDocumentId,
        name: docData.filename || "Unknown Document",
        date: docData.date ? formatDate(docData.date) : "",
        uploadType: (docData.uploadType || "Incoming") as
          | "Incoming"
          | "Outgoing",
        letterNo: docData.letterNo || "",
        subject: docData.subject || "",
        linkType: "direct",
      };
      setLinkedReferences((prev) => [...prev, newReference]);
      toast.success("Reference added successfully", {
        description: `"${newReference.name}" added to linked references`,
      });
    } catch (error) {
      console.error("Error adding reference:", error);
      toast.error("Failed to add reference", {
        description: "Please try again later",
      });
    }
  };

  // Remove a reference
  const handleRemoveReference = async (referenceId: string) => {
    try {
      const response = await authenticatedFetch(
        joinApiUrl(`/documents/${documentId}/references/${referenceId}`),
        {
          method: "DELETE",
        }
      );
      if (!response.ok) {
        const err = await readJsonSafe(response);
        throw new Error(err?.detail || "Failed to remove reference");
      }
      setLinkedReferences((prev) =>
        prev.filter((ref) => ref.id !== referenceId)
      );
      toast.success("Reference removed", {
        description: "The document reference has been removed",
      });
    } catch (error) {
      console.error("Error removing reference:", error);
      toast.error("Failed to remove reference", {
        description: "Please try again later",
      });
    }
  };

  const getLinkTypeLabel = (linkType?: string) => {
    if (linkType === "direct") return "Direct Link";
    if (linkType === "indirect") return "Indirect Link";
    return "Link";
  };

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader className="py-3 px-4 flex flex-row items-center justify-between">
          <CardTitle className="text-sm font-medium flex items-center gap-2">
            <LinkIcon className="h-4 w-4 text-muted-foreground" />
            Document References ({linkedReferences.length})
          </CardTitle>
          <Dialog>
            <DialogTrigger asChild>
              <Button
                variant="outline"
                size="sm"
                className="h-8"
                onClick={fetchAvailableDocuments}
              >
                <PlusCircle className="h-6 w-6 mr-1" />
                Add Reference
              </Button>
            </DialogTrigger>
            <DialogContent className="sm:max-w-7xl z-[100]">
              <DialogHeader>
                <DialogTitle>Add Reference</DialogTitle>
                <DialogDescription>
                  Search and link documents to this {uploadType.toLowerCase()}{" "}
                  letter
                </DialogDescription>
              </DialogHeader>

              <div className="space-y-4 py-2">
                <div className="flex gap-2">
                  <div className="relative flex-1">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                    <Input
                      placeholder="Search by name, letter number, or subject..."
                      className="pl-9"
                      value={searchQuery}
                      onChange={(e) => setSearchQuery(e.target.value)}
                      onKeyDown={(e) => e.key === "Enter" && handleSearch()}
                    />
                  </div>
                  <Button
                    onClick={handleSearch}
                    variant="secondary"
                    disabled={isSearching}
                  >
                    {isSearching ? (
                      <Loader2 className="h-4 w-4 animate-spin" />
                    ) : (
                      "Search"
                    )}
                  </Button>
                </div>

                <div className="border rounded-md overflow-hidden">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Document</TableHead>
                        <TableHead className="hidden sm:table-cell">
                          Letter No.
                        </TableHead>
                        <TableHead className="w-20"></TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {searchResults.length === 0 ? (
                        <TableRow>
                          <TableCell
                            colSpan={3}
                            className="text-center py-4 text-muted-foreground"
                          >
                            {searchQuery
                              ? "No matching documents found"
                              : "No documents available"}
                          </TableCell>
                        </TableRow>
                      ) : (
                        searchResults.map((doc) => (
                          <TableRow key={doc.id}>
                            <TableCell>
                              <div className="flex flex-col">
                                <div className="flex items-center gap-1.5">
                                  <FileText className="h-4 w-4 text-slate-500 shrink-0" />
                                  <span className="font-medium text-sm truncate">
                                    {doc.name}
                                  </span>
                                </div>
                                <span className="text-xs text-muted-foreground pl-5">
                                  {doc.subject}
                                </span>
                              </div>
                            </TableCell>
                            <TableCell className="hidden sm:table-cell">
                              {doc.letterNo}
                            </TableCell>
                            <TableCell>
                              <Button
                                variant="ghost"
                                size="sm"
                                className="h-8 w-8 p-0"
                                onClick={() => handleAddReference(doc.id)}
                                disabled={linkedReferences.some(
                                  (ref) => ref.id === doc.id
                                )}
                              >
                                <PlusCircle className="h-4 w-4" />
                              </Button>
                            </TableCell>
                          </TableRow>
                        ))
                      )}
                    </TableBody>
                  </Table>
                </div>
              </div>

              <DialogClose asChild>
                <div className="flex justify-end mt-4">
                  <Button variant="outline">Done</Button>
                </div>
              </DialogClose>
            </DialogContent>
          </Dialog>
        </CardHeader>

        <CardContent className="p-0">
          {linkedReferences.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-8 px-4 text-center">
              <div className="h-12 w-12 rounded-full bg-slate-100 flex items-center justify-center mb-3">
                <LinkIcon className="h-6 w-6 text-slate-400" />
              </div>
              <h3 className="font-medium text-slate-900">
                No References Linked
              </h3>
              <p className="text-sm text-slate-500 mt-1 max-w-xs">
                This document is not linked to any{" "}
                {uploadType === "Incoming" ? "outgoing" : "incoming"} letters.
              </p>
            </div>
          ) : (
            <div className="divide-y">
              {linkedReferences.map((ref) => (
                <div key={ref.id} className="p-3 hover:bg-slate-50">
                  <div className="flex justify-between items-start">
                    <div>
                      <div className="flex items-center gap-1.5">
                        <FileText className="h-4 w-4 text-slate-500" />
                        <span className="font-medium text-sm">{ref.name}</span>
                      </div>
                      <p className="text-xs text-slate-500 mt-1">
                        {ref.subject}
                      </p>
                      <div className="flex items-center gap-2 mt-2">
                        <Badge
                          variant="outline"
                          className={
                            ref.uploadType === "Incoming"
                              ? "bg-blue-50 text-blue-700"
                              : "bg-green-50 text-green-700"
                          }
                        >
                          {ref.uploadType}
                        </Badge>
                        <Badge
                          variant="outline"
                          className={
                            ref.linkType === "direct"
                              ? "bg-indigo-50 text-indigo-700"
                              : "bg-purple-50 text-purple-700"
                          }
                        >
                          {getLinkTypeLabel(ref.linkType)}
                        </Badge>
                        <span className="text-xs text-slate-500">
                          {ref.date}
                        </span>
                      </div>
                    </div>
                    <div className="flex gap-1">
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7"
                        asChild
                      >
                        <RouterLink to={`/documentviewer/${ref.id}`}>
                          <ExternalLink className="h-3.5 w-3.5" />
                        </RouterLink>
                      </Button>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-7 w-7 text-red-500 hover:text-red-700 hover:bg-red-50"
                        onClick={() => handleRemoveReference(ref.id)}
                      >
                        <Trash className="h-3.5 w-3.5" />
                      </Button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
};

export default ReferencesPanel;
