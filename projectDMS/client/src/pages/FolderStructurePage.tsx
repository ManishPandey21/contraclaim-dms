import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  ChevronRight,
  Download,
  File,
  FolderOpen,
  Loader2,
  RefreshCw,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useToast } from "@/hooks/use-toast";
import useRBAC from "@/hooks/useRBAC";
import { api } from "@/services/api";
import { listOrganizations } from "@/services/organizations-api";
import { listProjects } from "@/services/projects-api";
import type { DocumentBulkDownloadType } from "@/types/api";

type UploadType = "incoming" | "outgoing" | "contract";

interface Organization {
  _id: string;
  name: string;
  shortName?: string;
}

interface Project {
  _id: string;
  name: string;
  organization_id: string;
  shortName?: string;
}

interface DocumentItem {
  _id?: string;
  id?: string;
  filename: string;
  uploadType: UploadType | string;
  date?: string;
  createdAt?: string;
  filesize?: number;
  status?: string;
}

interface DownloadScope {
  downloadType: DocumentBulkDownloadType;
  uploadType?: UploadType;
  year?: number;
  month?: number;
}

interface TreeNode {
  id: string;
  name: string;
  type: "folder" | "file";
  children: TreeNode[];
  scope?: DownloadScope;
  document?: DocumentItem;
  size?: number;
}

const MONTH_NAMES = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

const UPLOAD_TYPE_LABELS: Record<UploadType, string> = {
  incoming: "Incoming",
  outgoing: "Outgoing",
  contract: "Contracts",
};

function normalizeUploadType(value?: string): UploadType {
  const normalized = String(value || "").toLowerCase();
  if (normalized === "outgoing") return "outgoing";
  if (normalized === "contract") return "contract";
  return "incoming";
}

function documentId(document: DocumentItem): string {
  return String(document._id || document.id || "");
}

function documentDate(document: DocumentItem): Date {
  const value = document.date || document.createdAt;
  const parsed = value ? new Date(value) : new Date();
  return Number.isNaN(parsed.getTime()) ? new Date() : parsed;
}

function compactName(value: string): string {
  return String(value || "Project")
    .replace(/[^A-Za-z0-9._-]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 80) || "Project";
}

function extractFilenameFromDisposition(contentDisposition?: string): string | null {
  if (!contentDisposition) return null;
  const utf8Match = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match?.[1]) {
    return decodeURIComponent(utf8Match[1].replace(/"/g, ""));
  }
  const filenameMatch = contentDisposition.match(/filename="?([^";]+)"?/i);
  return filenameMatch?.[1] ? filenameMatch[1] : null;
}

function triggerBlobDownload(blob: Blob, filename: string) {
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  window.URL.revokeObjectURL(url);
}

function buildDocumentTree(documents: DocumentItem[]): TreeNode {
  const root: TreeNode = {
    id: "root",
    name: "Project Documents",
    type: "folder",
    children: [],
    scope: { downloadType: "complete" },
  };

  const directionMap = new Map<UploadType, TreeNode>();
  const directionOrder: UploadType[] = ["incoming", "outgoing", "contract"];

  for (const type of directionOrder) {
    const downloadType: DocumentBulkDownloadType =
      type === "contract" ? "contracts" : "letters";
    const node: TreeNode = {
      id: type,
      name: UPLOAD_TYPE_LABELS[type],
      type: "folder",
      children: [],
      scope: { downloadType, uploadType: type },
    };
    directionMap.set(type, node);
  }

  for (const doc of documents) {
    const id = documentId(doc);
    if (!id) continue;

    const uploadType = normalizeUploadType(doc.uploadType);
    const direction = directionMap.get(uploadType);
    if (!direction) continue;

    const date = documentDate(doc);
    const year = date.getFullYear();
    const month = date.getMonth() + 1;
    const yearId = `${uploadType}/${year}`;
    const monthId = `${yearId}/${String(month).padStart(2, "0")}`;

    let yearNode = direction.children.find((item) => item.id === yearId);
    if (!yearNode) {
      yearNode = {
        id: yearId,
        name: String(year),
        type: "folder",
        children: [],
        scope: {
          ...direction.scope!,
          year,
        },
      };
      direction.children.push(yearNode);
    }

    let monthNode = yearNode.children.find((item) => item.id === monthId);
    if (!monthNode) {
      monthNode = {
        id: monthId,
        name: MONTH_NAMES[month - 1],
        type: "folder",
        children: [],
        scope: {
          ...direction.scope!,
          year,
          month,
        },
      };
      yearNode.children.push(monthNode);
    }

    monthNode.children.push({
      id: `document/${id}`,
      name: doc.filename || id,
      type: "file",
      children: [],
      document: doc,
      size: doc.filesize,
    });
  }

  for (const direction of directionMap.values()) {
    direction.children.sort((a, b) => Number(b.name) - Number(a.name));
    for (const year of direction.children) {
      year.children.sort((a, b) => {
        const aMonth = a.scope?.month || 0;
        const bMonth = b.scope?.month || 0;
        return bMonth - aMonth;
      });
      for (const month of year.children) {
        month.children.sort((a, b) => a.name.localeCompare(b.name));
      }
    }
  }

  root.children = directionOrder
    .map((type) => directionMap.get(type)!)
    .filter((node) => node.children.length > 0);

  return root;
}

function findNode(root: TreeNode, path: string[]): TreeNode {
  let current = root;
  for (const id of path) {
    const next = current.children.find((child) => child.id === id);
    if (!next) return root;
    current = next;
  }
  return current;
}

function pathFromNodeId(id: string): string[] {
  if (id === "root") return [];
  const parts = id.split("/");
  return parts.map((_, index) => parts.slice(0, index + 1).join("/"));
}

const FolderStructurePage: React.FC = () => {
  const { toast } = useToast();
  const { roles, can } = useRBAC();

  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [documents, setDocuments] = useState<DocumentItem[]>([]);
  const [selectedOrgId, setSelectedOrgId] = useState<string>("");
  const [selectedProjId, setSelectedProjId] = useState<string>("");
  const [path, setPath] = useState<string[]>([]);
  const [selectedNodeId, setSelectedNodeId] = useState<string>("root");
  const [loadingOrganizations, setLoadingOrganizations] = useState(false);
  const [loadingProjects, setLoadingProjects] = useState(false);
  const [loadingDocuments, setLoadingDocuments] = useState(false);
  const [downloadingId, setDownloadingId] = useState<string | null>(null);

  const canDownloadAllDocuments =
    roles.some((role) =>
      ["superadmin", "orgadmin", "projectadmin"].includes(
        String(role).toLowerCase()
      )
    ) || can("dms.document.bulk_download");

  const selectedProject = projects.find((project) => project._id === selectedProjId);
  const tree = useMemo(() => buildDocumentTree(documents), [documents]);
  const currentNode = useMemo(() => findNode(tree, path), [tree, path]);
  const selectedNode = useMemo(() => {
    if (selectedNodeId === "root") return tree;
    const stack = [...tree.children];
    while (stack.length > 0) {
      const node = stack.shift()!;
      if (node.id === selectedNodeId) return node;
      stack.push(...node.children);
    }
    return currentNode;
  }, [currentNode, selectedNodeId, tree]);

  const breadcrumbNodes = useMemo(() => {
    const result: TreeNode[] = [tree];
    let cursor = tree;
    for (const id of path) {
      const next = cursor.children.find((child) => child.id === id);
      if (!next) break;
      result.push(next);
      cursor = next;
    }
    return result;
  }, [path, tree]);

  useEffect(() => {
    const fetchOrganizations = async () => {
      setLoadingOrganizations(true);
      try {
        const data = await listOrganizations();
        setOrganizations(data as unknown as Organization[]);
      } catch (error) {
        console.error("Error fetching organizations:", error);
        toast({
          title: "Error",
          description: "Failed to load organizations.",
          variant: "destructive",
        });
      } finally {
        setLoadingOrganizations(false);
      }
    };
    fetchOrganizations();
  }, [toast]);

  useEffect(() => {
    const fetchProjects = async () => {
      if (!selectedOrgId) {
        setProjects([]);
        setSelectedProjId("");
        setDocuments([]);
        setPath([]);
        setSelectedNodeId("root");
        return;
      }

      setLoadingProjects(true);
      try {
        const data = await listProjects({ organization_id: selectedOrgId });
        setProjects(data as unknown as Project[]);
      } catch (error) {
        console.error("Error fetching projects:", error);
        toast({
          title: "Error",
          description: "Failed to load projects.",
          variant: "destructive",
        });
      } finally {
        setLoadingProjects(false);
      }
    };
    fetchProjects();
  }, [selectedOrgId, toast]);

  const fetchDocuments = useCallback(async () => {
    if (!selectedProjId) {
      setDocuments([]);
      return;
    }

    setLoadingDocuments(true);
    try {
      const all: DocumentItem[] = [];
      const limit = 1000;
      let skip = 0;
      let total = 0;

      do {
        const { data } = await api.get("/documents", {
          params: {
            organization_id: selectedOrgId || undefined,
            project_id: selectedProjId,
            skip,
            limit,
          },
        });
        const batch = (data?.documents || []) as DocumentItem[];
        total = Number(data?.total || batch.length);
        all.push(...batch);
        skip += limit;
        if (batch.length === 0) break;
      } while (all.length < total);

      setDocuments(all);
      setPath([]);
      setSelectedNodeId("root");
    } catch (error: any) {
      console.error("Error fetching documents:", error);
      toast({
        title: "Error",
        description:
          error?.response?.data?.detail?.message ||
          error?.response?.data?.detail ||
          "Failed to load project documents.",
        variant: "destructive",
      });
    } finally {
      setLoadingDocuments(false);
    }
  }, [selectedOrgId, selectedProjId, toast]);

  useEffect(() => {
    fetchDocuments();
  }, [fetchDocuments]);

  const downloadFile = async (node: TreeNode) => {
    const doc = node.document;
    const id = doc ? documentId(doc) : "";
    if (!id || !doc) return;

    setDownloadingId(node.id);
    try {
      const response = await api.get(`/documents/${id}/download`, {
        responseType: "blob",
      });
      const filename =
        extractFilenameFromDisposition(response.headers["content-disposition"]) ||
        doc.filename ||
        "document";
      triggerBlobDownload(response.data as Blob, filename);
      toast({
        title: "Download ready",
        description: `${filename} downloaded successfully.`,
      });
    } catch (error: any) {
      console.error("Error downloading document:", error);
      toast({
        title: "Download failed",
        description:
          error?.response?.data?.detail?.message ||
          error?.message ||
          "Failed to download document.",
        variant: "destructive",
      });
    } finally {
      setDownloadingId(null);
    }
  };

  const downloadFolder = async (node: TreeNode) => {
    if (!selectedProjId || !node.scope) return;

    setDownloadingId(node.id);
    try {
      const response = await api.get("/documents/download-all", {
        params: {
          project_id: selectedProjId,
          type: node.scope.downloadType,
          upload_type: node.scope.uploadType,
          year: node.scope.year,
          month: node.scope.month,
        },
        responseType: "blob",
      });

      const fallbackProject = compactName(
        selectedProject?.shortName || selectedProject?.name || "Project"
      );
      const fallbackScope = compactName(node.name);
      const filename =
        extractFilenameFromDisposition(response.headers["content-disposition"]) ||
        `${fallbackProject}_${fallbackScope}_${new Date()
          .toISOString()
          .slice(0, 10)}.zip`;

      triggerBlobDownload(response.data as Blob, filename);
      toast({
        title: "Download ready",
        description: `${node.name} package downloaded successfully.`,
      });
    } catch (error: any) {
      console.error("Error downloading folder:", error);
      const data = error?.response?.data;
      let message = error?.message || "Failed to download selected folder.";
      if (data instanceof Blob) {
        try {
          const parsed = JSON.parse(await data.text());
          message = parsed?.detail?.message || parsed?.detail || parsed?.message || message;
        } catch {
          // keep fallback
        }
      } else {
        message = data?.detail?.message || data?.detail || data?.message || message;
      }
      toast({
        title: "Download failed",
        description: message,
        variant: "destructive",
      });
    } finally {
      setDownloadingId(null);
    }
  };

  const downloadNode = async (node: TreeNode) => {
    if (node.type === "file") {
      await downloadFile(node);
      return;
    }
    await downloadFolder(node);
  };

  const navigateTo = (node: TreeNode) => {
    if (node.type !== "folder") return;
    if (node.id === "root") {
      setPath([]);
      setSelectedNodeId("root");
      return;
    }
    setPath(pathFromNodeId(node.id));
    setSelectedNodeId(node.id);
  };

  const formatSize = (size?: number) => {
    if (!size) return "";
    if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
    return `${(size / (1024 * 1024)).toFixed(1)} MB`;
  };

  return (
    <div className="flex min-h-screen bg-gray-50">
      <aside className="w-80 border-r bg-white p-6">
        <Card>
          <CardHeader>
            <CardTitle>Document Scope</CardTitle>
            <CardDescription>
              Select a project, then download the project, a folder, or a file.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="space-y-2">
              <Label>Organization</Label>
              <Select
                value={selectedOrgId}
                onValueChange={(value) => {
                  setSelectedOrgId(value);
                  setSelectedProjId("");
                }}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select organization" />
                </SelectTrigger>
                <SelectContent>
                  {loadingOrganizations ? (
                    <SelectItem value="loading" disabled>
                      Loading organizations...
                    </SelectItem>
                  ) : (
                    organizations.map((org) => (
                      <SelectItem key={org._id} value={org._id}>
                        {org.name}
                      </SelectItem>
                    ))
                  )}
                </SelectContent>
              </Select>
            </div>

            <div className="space-y-2">
              <Label>Project</Label>
              <Select
                value={selectedProjId}
                onValueChange={setSelectedProjId}
                disabled={!selectedOrgId || loadingProjects}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select project" />
                </SelectTrigger>
                <SelectContent>
                  {loadingProjects ? (
                    <SelectItem value="loading" disabled>
                      Loading projects...
                    </SelectItem>
                  ) : (
                    projects.map((project) => (
                      <SelectItem key={project._id} value={project._id}>
                        {project.name}
                      </SelectItem>
                    ))
                  )}
                </SelectContent>
              </Select>
            </div>

            <Separator />

            <div className="space-y-2 text-sm text-gray-600">
              <div className="flex justify-between">
                <span>Total files</span>
                <span className="font-medium text-gray-900">{documents.length}</span>
              </div>
              <div className="flex justify-between">
                <span>Selected</span>
                <span className="max-w-40 truncate font-medium text-gray-900">
                  {selectedNode.name}
                </span>
              </div>
            </div>

            <div className="grid gap-2">
              <Button
                onClick={() => downloadNode(selectedNode)}
                disabled={
                  !selectedProjId ||
                  downloadingId !== null ||
                  (selectedNode.type === "folder" && !canDownloadAllDocuments)
                }
              >
                {downloadingId ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <Download className="mr-2 h-4 w-4" />
                )}
                Download Selected
              </Button>
              <Button
                variant="outline"
                onClick={fetchDocuments}
                disabled={!selectedProjId || loadingDocuments}
              >
                <RefreshCw className="mr-2 h-4 w-4" />
                Refresh
              </Button>
            </div>

            {!canDownloadAllDocuments && (
              <p className="text-xs text-gray-500">
                Folder ZIP downloads require project bulk-download permission.
                Individual files remain available when document read access is granted.
              </p>
            )}
          </CardContent>
        </Card>
      </aside>

      <main className="flex-1 p-6">
        <h1 className="mb-6 text-2xl font-bold">Folder Structure</h1>
        <Card>
          <CardHeader>
            <CardTitle>Folder Structure</CardTitle>
            <CardDescription>
              Browse real uploaded documents grouped by direction, year, and month.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="mb-4 flex flex-wrap items-center gap-2 text-sm">
              {breadcrumbNodes.map((node, index) => (
                <React.Fragment key={node.id}>
                  {index > 0 && <ChevronRight className="h-4 w-4 text-gray-400" />}
                  <button
                    type="button"
                    className="rounded px-1.5 py-1 text-gray-700 hover:bg-gray-100"
                    onClick={() => navigateTo(node)}
                  >
                    {index === 0 ? "Root" : node.name}
                  </button>
                </React.Fragment>
              ))}
            </div>

            <Separator className="mb-4" />

            {!selectedProjId ? (
              <div className="flex h-56 items-center justify-center text-gray-500">
                Select an organization and project to view documents.
              </div>
            ) : loadingDocuments ? (
              <div className="flex h-56 items-center justify-center text-gray-500">
                <Loader2 className="mr-2 h-6 w-6 animate-spin" />
                Loading documents...
              </div>
            ) : currentNode.children.length === 0 ? (
              <div className="flex h-56 items-center justify-center text-gray-500">
                No documents found for this selection.
              </div>
            ) : (
              <ScrollArea className="h-[calc(100vh-260px)] pr-4">
                <div className="grid gap-2">
                  {currentNode.children.map((node) => {
                    const isSelected = selectedNode.id === node.id;
                    const isDownloading = downloadingId === node.id;
                    const folderDownloadDisabled =
                      node.type === "folder" && !canDownloadAllDocuments;

                    return (
                      <div
                        key={node.id}
                        className={`flex items-center justify-between rounded-md border p-3 transition-colors ${
                          isSelected
                            ? "border-blue-300 bg-blue-50"
                            : "border-gray-200 bg-white hover:bg-gray-50"
                        }`}
                      >
                        <button
                          type="button"
                          className="flex min-w-0 flex-1 items-center gap-3 text-left"
                          onClick={() => setSelectedNodeId(node.id)}
                          onDoubleClick={() => navigateTo(node)}
                        >
                          {node.type === "folder" ? (
                            <FolderOpen className="h-5 w-5 shrink-0 text-blue-500" />
                          ) : (
                            <File className="h-5 w-5 shrink-0 text-gray-500" />
                          )}
                          <span className="truncate font-medium">{node.name}</span>
                          {node.type === "folder" && (
                            <span className="text-xs text-gray-500">
                              {node.children.length} item{node.children.length === 1 ? "" : "s"}
                            </span>
                          )}
                          {node.type === "file" && node.size ? (
                            <span className="text-xs text-gray-500">
                              {formatSize(node.size)}
                            </span>
                          ) : null}
                        </button>

                        <div className="ml-3 flex items-center gap-2">
                          {node.type === "folder" && (
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => navigateTo(node)}
                            >
                              Open
                            </Button>
                          )}
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => downloadNode(node)}
                            disabled={downloadingId !== null || folderDownloadDisabled}
                            title={
                              folderDownloadDisabled
                                ? "Folder download requires bulk-download permission"
                                : "Download"
                            }
                          >
                            {isDownloading ? (
                              <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                              <Download className="h-4 w-4" />
                            )}
                          </Button>
                        </div>
                      </div>
                    );
                  })}
                </div>
              </ScrollArea>
            )}
          </CardContent>
        </Card>
      </main>
    </div>
  );
};

export default FolderStructurePage;
