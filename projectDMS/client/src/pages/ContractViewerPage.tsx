import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import PageHeader from "@/components/ui/PageHeader";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import Badge from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { toast } from "sonner";
import {
  Building2,
  Download,
  ExternalLink,
  FileText,
  ListTree,
  Loader2,
  MessageSquare,
  RefreshCw,
  Sparkles,
} from "lucide-react";
import { api } from "@/services/api";
import { joinApiUrl } from "@/config/api";
import { authenticatedFetch } from "@/services/http";
import { reindexContract } from "@/services/contracts-api";
import ClauseIndexTab from "@/components/contracts/ClauseIndexTab";
import { enhancedApi } from "@/services/enhanced-api";
import { extractErrorMessage } from "@/lib/error-logger";

type Organization = { id: string; name: string; shortName?: string | null };
type Project = { _id: string; name: string; organization_id: string };
type UploadOption = {
  document_id: string;
  upload_id?: string;
  filename: string;
  status?: string;
};

const ContractViewerPage: React.FC = () => {
  const navigate = useNavigate();
  const { id: routeDocId } = useParams<{ id: string }>();

  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [orgId, setOrgId] = useState<string>(() => window.localStorage.getItem("org_id") || "");
  const [projId, setProjId] = useState<string>(() => window.localStorage.getItem("proj_id") || "");
  const [uploads, setUploads] = useState<UploadOption[]>([]);
  const [selectedDocId, setSelectedDocId] = useState<string>(routeDocId || "");
  const [fetching, setFetching] = useState(false);

  // Selected document metadata + PDF preview state
  const [docMeta, setDocMeta] = useState<Record<string, any> | null>(null);
  const [pdfUrl, setPdfUrl] = useState<string | null>(null);
  const [pdfLoading, setPdfLoading] = useState(false);
  const [pdfError, setPdfError] = useState<string | null>(null);
  const [reindexing, setReindexing] = useState(false);
  const blobUrlRef = useRef<string | null>(null);

  const loadOrganizations = useCallback(async () => {
    setFetching(true);
    try {
      const { data } = await api.get("/organizations", { params: { limit: 100 } });
      const list: Organization[] = (data?.organizations || data || [])
        .map((o: any) => ({
          id: o.id ?? o._id ?? "",
          name: o.name ?? o.title ?? "",
          shortName: o.shortName ?? o.short_name ?? null,
        }))
        .filter((o: Organization) => o.id && o.name);
      setOrganizations(list);
    } catch {
      setOrganizations([]);
    } finally {
      setFetching(false);
    }
  }, []);

  const loadProjects = useCallback(async (organization_id: string) => {
    if (!organization_id) {
      setProjects([]);
      return;
    }
    setFetching(true);
    try {
      const { data } = await api.get("/projects", { params: { organization_id } });
      const list: Project[] = (data || [])
        .map((p: any) => ({
          _id: p._id ?? p.id ?? "",
          name: p.name ?? "",
          organization_id: p.organization_id ?? organization_id,
        }))
        .filter((p: Project) => p._id && p.name);
      setProjects(list);
    } catch {
      setProjects([]);
    } finally {
      setFetching(false);
    }
  }, []);

  const loadUploads = useCallback(async (organization_id: string, project_id?: string) => {
    if (!organization_id) {
      setUploads([]);
      return;
    }
    setFetching(true);
    try {
      const { data } = await api.get("/contracts/list", {
        params: { organization_id, project_id: project_id || undefined, limit: 200, skip: 0 },
      });
      const list: UploadOption[] = (data?.uploads || [])
        .map((u: any) => ({
          document_id: u.document_id ?? u._id ?? "",
          upload_id: u.upload_id ?? undefined,
          filename: u.filename ?? "contract",
          status: u.status ?? "unknown",
        }))
        .filter((u: UploadOption) => u.document_id);
      setUploads(list);
    } catch {
      setUploads([]);
    } finally {
      setFetching(false);
    }
  }, []);

  useEffect(() => {
    loadOrganizations().catch(() => {});
  }, [loadOrganizations]);

  useEffect(() => {
    if (orgId) {
      window.localStorage.setItem("org_id", orgId);
      loadProjects(orgId).catch(() => {});
    } else {
      window.localStorage.removeItem("org_id");
      setProjects([]);
      setUploads([]);
    }
  }, [orgId, loadProjects]);

  useEffect(() => {
    if (projId) {
      window.localStorage.setItem("proj_id", projId);
    } else {
      window.localStorage.removeItem("proj_id");
    }
    if (orgId) {
      loadUploads(orgId, projId).catch(() => {});
    }
  }, [projId, orgId, loadUploads]);

  // Deep-link: when arriving at /contracts/viewer/:id, resolve the document's
  // scope so the selectors reflect it (and the metadata panel populates).
  useEffect(() => {
    if (!routeDocId) return;
    setSelectedDocId(routeDocId);
    (async () => {
      try {
        const doc: any = await enhancedApi.getDocument(routeDocId);
        if (doc?.organization_id) setOrgId(String(doc.organization_id));
        if (doc?.project_id) setProjId(String(doc.project_id));
      } catch {
        // Metadata resolution is best-effort; the PDF still loads by id below.
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [routeDocId]);

  const revokeBlobUrl = useCallback(() => {
    if (blobUrlRef.current) {
      try {
        URL.revokeObjectURL(blobUrlRef.current);
      } catch {
        // ignore
      }
      blobUrlRef.current = null;
    }
  }, []);

  // Load the selected contract's metadata + PDF bytes whenever it changes.
  useEffect(() => {
    if (!selectedDocId) {
      setDocMeta(null);
      setPdfUrl(null);
      setPdfError(null);
      revokeBlobUrl();
      return;
    }

    let cancelled = false;
    setPdfLoading(true);
    setPdfError(null);
    setPdfUrl(null);
    revokeBlobUrl();

    (async () => {
      try {
        const meta = await enhancedApi.getDocument(selectedDocId).catch(() => null);
        if (!cancelled && meta) setDocMeta(meta as Record<string, any>);

        const resp = await authenticatedFetch(
          joinApiUrl(`/contracts/${selectedDocId}/download`),
        );
        if (!resp.ok) {
          const text = await resp.text().catch(() => "");
          throw new Error(text || `Failed to load contract (${resp.status})`);
        }
        const blob = await resp.blob();
        if (blob.size === 0) throw new Error("The contract file is empty.");
        if (cancelled) return;
        const objectUrl = URL.createObjectURL(blob);
        blobUrlRef.current = objectUrl;
        setPdfUrl(objectUrl);
      } catch (err) {
        if (!cancelled) {
          setPdfError(err instanceof Error ? err.message : "Failed to load contract");
        }
      } finally {
        if (!cancelled) setPdfLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [selectedDocId, revokeBlobUrl]);

  // Revoke any outstanding object URL on unmount.
  useEffect(() => () => revokeBlobUrl(), [revokeBlobUrl]);

  const [viewTab, setViewTab] = useState<"document" | "clauses">("document");
  const [pageAnchor, setPageAnchor] = useState<number | null>(null);

  const iframeUrl = useMemo(() => {
    if (!pdfUrl) return null;
    const pageHash = pageAnchor ? `page=${pageAnchor}&` : "";
    return `${pdfUrl}#${pageHash}toolbar=1&navpanes=0&view=FitH`;
  }, [pdfUrl, pageAnchor]);

  const openSourcePage = useCallback((page: number) => {
    setPageAnchor(page);
    setViewTab("document");
  }, []);

  const selectedName = useMemo(() => {
    if (docMeta?.filename) return String(docMeta.filename);
    return uploads.find((u) => u.document_id === selectedDocId)?.filename || "";
  }, [docMeta, uploads, selectedDocId]);

  const status = useMemo(() => {
    return (
      (docMeta?.status as string) ||
      uploads.find((u) => u.document_id === selectedDocId)?.status ||
      "unknown"
    );
  }, [docMeta, uploads, selectedDocId]);

  const categories: string[] = useMemo(
    () => (Array.isArray(docMeta?.contract_categories) ? docMeta!.contract_categories : []),
    [docMeta],
  );

  const handleOpenInNewTab = useCallback(() => {
    if (pdfUrl) window.open(pdfUrl, "_blank", "noopener,noreferrer");
  }, [pdfUrl]);

  const handleDownload = useCallback(() => {
    if (!pdfUrl) return;
    const a = window.document.createElement("a");
    a.href = pdfUrl;
    a.download = selectedName || "contract.pdf";
    window.document.body.appendChild(a);
    a.click();
    window.document.body.removeChild(a);
  }, [pdfUrl, selectedName]);

  const handleReindex = useCallback(async () => {
    if (!selectedDocId) return;
    setReindexing(true);
    try {
      await reindexContract(selectedDocId);
      toast.success("Reindex queued", {
        description: "The contract's search index is being rebuilt in the background.",
      });
    } catch (err) {
      toast.error("Failed to queue reindex", { description: extractErrorMessage(err) });
    } finally {
      setReindexing(false);
    }
  }, [selectedDocId]);

  const goToQA = useCallback(() => {
    if (orgId) window.localStorage.setItem("org_id", orgId);
    if (projId) window.localStorage.setItem("proj_id", projId);
    navigate("/contracts/qa");
  }, [navigate, orgId, projId]);

  return (
    <div className="p-6 space-y-6">
      <PageHeader
        icon={<FileText className="h-5 w-5" />}
        title="Contract Viewer"
        description="Browse and preview the original contract document for any project scope."
      />

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Building2 className="h-5 w-5" />
            Scope
          </CardTitle>
          <CardDescription>Select organization, project, and a contract to preview.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <label className="text-sm font-medium text-gray-700">Organization *</label>
              <select
                data-testid="contract-viewer-org-select"
                className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                value={orgId}
                onChange={(e) => {
                  setOrgId(e.target.value);
                  setProjId("");
                  setSelectedDocId("");
                }}
                disabled={fetching}
              >
                <option value="">Select organization</option>
                {organizations.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium text-gray-700">Project *</label>
              <select
                data-testid="contract-viewer-project-select"
                className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 disabled:bg-gray-50 disabled:text-gray-500"
                value={projId}
                onChange={(e) => {
                  setProjId(e.target.value);
                  setSelectedDocId("");
                }}
                disabled={!orgId || fetching}
              >
                <option value="">{orgId ? "Select project" : "Select organization first"}</option>
                {projects.map((p) => (
                  <option key={p._id} value={p._id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium text-gray-700">Contract File *</label>
            <select
              data-testid="contract-viewer-file-select"
              className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 disabled:bg-gray-50 disabled:text-gray-500"
              value={selectedDocId}
              onChange={(e) => setSelectedDocId(e.target.value)}
              disabled={!orgId || fetching || uploads.length === 0}
            >
              <option value="">{uploads.length ? "Select a contract" : "No contracts found"}</option>
              {uploads.map((u) => (
                <option key={u.document_id} value={u.document_id}>
                  {u.filename} {u.status ? `(${u.status})` : ""}
                </option>
              ))}
            </select>
          </div>
        </CardContent>
      </Card>

      {selectedDocId && (
        <Card>
          <CardHeader className="border-b">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="min-w-0">
                <CardTitle className="flex items-center gap-2 truncate">
                  <FileText className="h-5 w-5 shrink-0" />
                  <span className="truncate">{selectedName || "Contract"}</span>
                </CardTitle>
                <div className="mt-1 flex flex-wrap items-center gap-2">
                  <Badge variant={status.toLowerCase() === "completed" ? "success" : "neutral"}>
                    {status}
                  </Badge>
                  {categories.slice(0, 4).map((c) => (
                    <Badge key={c} variant="outline">
                      {c}
                    </Badge>
                  ))}
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <Button variant="outline" size="sm" onClick={goToQA} className="gap-2">
                  <MessageSquare className="h-4 w-4" />
                  Ask Q&amp;A
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => navigate("/contracts/appraisal")}
                  className="gap-2"
                >
                  <Sparkles className="h-4 w-4" />
                  Appraisal
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleReindex}
                  disabled={reindexing}
                  className="gap-2"
                  title="Rebuild the vector search index for this contract"
                >
                  {reindexing ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <RefreshCw className="h-4 w-4" />
                  )}
                  Rebuild index
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={handleOpenInNewTab}
                  disabled={!pdfUrl}
                  className="gap-2"
                >
                  <ExternalLink className="h-4 w-4" />
                  Open
                </Button>
                <Button size="sm" onClick={handleDownload} disabled={!pdfUrl} className="gap-2">
                  <Download className="h-4 w-4" />
                  Download
                </Button>
              </div>
            </div>
          </CardHeader>
          <CardContent className="p-0">
            <Tabs value={viewTab} onValueChange={(v) => setViewTab(v as "document" | "clauses")}>
              <div className="border-b px-3 pt-3">
                <TabsList>
                  <TabsTrigger value="document" className="gap-2">
                    <FileText className="h-4 w-4" /> Document
                  </TabsTrigger>
                  <TabsTrigger value="clauses" className="gap-2">
                    <ListTree className="h-4 w-4" /> Clause Index
                  </TabsTrigger>
                </TabsList>
              </div>

              <TabsContent value="document" className="m-0">
                <div className="h-[calc(100vh-360px)] min-h-[480px] w-full bg-gray-100">
                  {pdfLoading ? (
                    <div className="flex h-full flex-col items-center justify-center text-muted-foreground">
                      <Loader2 className="mb-3 h-8 w-8 animate-spin" />
                      <p className="text-sm">Loading contract preview…</p>
                    </div>
                  ) : pdfError ? (
                    <div className="flex h-full flex-col items-center justify-center px-6 text-center">
                      <FileText className="mb-3 h-8 w-8 text-gray-400" />
                      <p className="text-sm font-medium text-red-600">Unable to preview this contract</p>
                      <p className="mt-1 max-w-md text-xs text-muted-foreground">{pdfError}</p>
                    </div>
                  ) : iframeUrl ? (
                    <iframe
                      title={selectedName || "Contract document"}
                      src={iframeUrl}
                      className="h-full w-full border-0 bg-white"
                    />
                  ) : (
                    <div className="flex h-full flex-col items-center justify-center text-muted-foreground">
                      <FileText className="mb-3 h-8 w-8" />
                      <p className="text-sm">Preview unavailable.</p>
                    </div>
                  )}
                </div>
              </TabsContent>

              <TabsContent value="clauses" className="m-0 p-4">
                <ClauseIndexTab documentId={selectedDocId} onOpenPage={openSourcePage} />
              </TabsContent>
            </Tabs>
          </CardContent>
        </Card>
      )}
    </div>
  );
};

export default ContractViewerPage;
