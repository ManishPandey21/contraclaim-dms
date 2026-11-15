import React, {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Link } from "react-router-dom";
import {
  createContractUploadSession,
  downloadContractDocument,
  uploadContractsMultipart,
  uploadContractInChunks,
  getContractStatus,
  UploadResult,
  StatusResponse,
  ContractJobStatus,
  isTerminalContractStatus,
} from "@/services/contracts-api";
import { extractErrorMessage } from "@/lib/error-logger";
import { api } from "@/services/api";
import PageHeader from "@/components/ui/PageHeader";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
} from "@/components/ui/card";
import Badge from "@/components/ui/badge";
import { toast } from "sonner";
import {
  UploadCloud,
  Loader2,
  FileText,
  Building2,
  FolderOpen,
  X,
  CheckCircle,
  AlertCircle,
  Clock,
  Download,
  RotateCcw,
  Upload as UploadIcon,
} from "lucide-react";
import { usePinnedPageScope } from "@/hooks/useRegisterProjectScope";

type UploadProgress = {
  file: File;
  mode: "multipart" | "chunked";
  progress: number; // 0-100
  upload_id?: string;
  document_id?: string;
  status?: ContractJobStatus;
  categories?: string[] | null;
  error?: string | null;
  processing_stage?: string | null;
  stage_label?: string | null;
};

type Organization = { id: string; name: string; shortName?: string | null };
type Project = { _id: string; name: string; organization_id: string };

type PrevUpload = {
  document_id: string;
  upload_id?: string | null;
  filename: string;
  status?: string;
  categories?: string[] | null;
  createdAt?: string | null;
  size?: number | null;
};

const bytesToHuman = (n: number) => {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(1)} GB`;
};

const statusVariant = (
  s?: UploadProgress["status"]
): "primary" | "success" | "danger" | "neutral" => {
  if (s === "completed") return "success";
  if (s === "failed" || s === "human_review_required") return "danger";
  if (s === "processing" || s === "queued") return "primary";
  return "neutral";
};

const getStatusIcon = (status?: UploadProgress["status"]) => {
  switch (status) {
    case "completed":
      return <CheckCircle className="h-4 w-4" />;
    case "failed":
    case "human_review_required":
      return <AlertCircle className="h-4 w-4" />;
    case "processing":
    case "queued":
      return <Clock className="h-4 w-4" />;
    default:
      return <FileText className="h-4 w-4" />;
  }
};

const ContractsUploadPage: React.FC = () => {
  // Organization/Project name fetching
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [orgLoading, setOrgLoading] = useState<boolean>(false);
  const [projLoading, setProjLoading] = useState<boolean>(false);

  const [orgId, setOrgId] = useState<string>(
    () => window.localStorage.getItem("org_id") || ""
  );
const [projId, setProjId] = useState<string>(
  () => window.localStorage.getItem("proj_id") || ""
);
  // CL-4A: while the navbar selects a project, this page's picker follows it.
  usePinnedPageScope(orgId, setOrgId, projId, setProjId);

  const parseOrganizationsResponse = useCallback((payload: any): Organization[] => {
    const collection = Array.isArray(payload)
      ? payload
      : Array.isArray(payload?.organizations)
      ? payload.organizations
      : [];

    return collection
      .map((org: any) => {
        const id =
          org?.id ??
          org?._id ??
          (typeof org?._id !== "undefined" ? String(org._id) : "");
        const name = org?.name ?? org?.title ?? "";
        if (!id || !name) {
          return null;
        }
        return {
          id: String(id),
          name: String(name),
          shortName: org?.shortName ?? org?.short_name ?? null,
        } as Organization;
      })
      .filter(Boolean) as Organization[];
  }, []);

  // Files and uploads
  const [files, setFiles] = useState<File[]>([]);
  const [uploads, setUploads] = useState<UploadProgress[]>([]);
  const [prevUploads, setPrevUploads] = useState<PrevUpload[]>([]);
  const [prevLoading, setPrevLoading] = useState<boolean>(false);
  const [hasMore, setHasMore] = useState<boolean>(false);
  const PAGE_SIZE = 50;
  const pollTimers = useRef<Record<string, any>>({});
  const uploadAbortRef = useRef<AbortController | null>(null);
  const [uploadingBatch, setUploadingBatch] = useState(false);

  // Drag & Drop
  const [dragging, setDragging] = useState(false);
  const dropRef = useRef<HTMLDivElement | null>(null);

  // Fetch organizations on mount
  useEffect(() => {
    const fetchOrgs = async () => {
      setOrgLoading(true);
      try {
        const { data } = await api.get("/organizations", {
          params: { limit: 50 },
        });
        const list = parseOrganizationsResponse(data);
        setOrganizations(list);
      } catch (e) {
        console.error("Failed to fetch organizations", e);
        setOrganizations([]);
      } finally {
        setOrgLoading(false);
      }
    };
    fetchOrgs();
  }, [parseOrganizationsResponse]);

  // Fetch projects when orgId changes
  useEffect(() => {
    const fetchProjects = async () => {
      if (!orgId) {
        setProjects([]);
        return;
      }
      setProjLoading(true);
      try {
        const { data } = await api.get("/projects", {
          params: { organization_id: orgId },
        });
        const list: Project[] = (data || []).map((p: any) => ({
          _id: p._id ?? p.id ?? "",
          name: p.name ?? "",
          organization_id: p.organization_id ?? "",
        }));
        setProjects(list);
      } catch (e) {
        console.error("Failed to fetch projects", e);
      } finally {
        setProjLoading(false);
      }
    };
    fetchProjects();
  }, [orgId]);

  // Persist selections
  useEffect(() => {
    if (orgId) window.localStorage.setItem("org_id", orgId);
    if (projId) window.localStorage.setItem("proj_id", projId);
  }, [orgId, projId]);

  // Load previously uploaded contracts for selected org/project
  const refreshPrevUploads = useCallback(
    async (append: boolean = false) => {
      if (!orgId) {
        setPrevUploads([]);
        setHasMore(false);
        return;
      }
      setPrevLoading(true);
      try {
        const skip = append ? prevUploads.length : 0;
        const { data } = await api.get("/contracts/list", {
          params: {
            organization_id: orgId,
            project_id: projId || undefined,
            limit: PAGE_SIZE,
            skip,
          },
        });
        const uploads: PrevUpload[] = (data?.uploads || []).map((r: any) => ({
          document_id: String(r.document_id ?? ""),
          upload_id: r.upload_id ?? null,
          filename: r.filename ?? "file",
          status: r.status ?? "unknown",
          categories: r.categories ?? null,
          createdAt: r.createdAt ?? null,
          size: r.size ?? null,
        }));
        setHasMore(uploads.length === PAGE_SIZE);
        setPrevUploads((prev) => (append ? [...prev, ...uploads] : uploads));
      } catch (e) {
        console.error("Failed to load previous uploads", e);
        if (!append) setPrevUploads([]);
        setHasMore(false);
      } finally {
        setPrevLoading(false);
      }
    },
    [orgId, projId, prevUploads.length]
  );

  // Initial and org/project change refresh
  useEffect(() => {
    refreshPrevUploads();
  }, [refreshPrevUploads]);

  const onSelectFiles = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const list = e.target.files ? Array.from(e.target.files) : [];
      if (list.length === 0) return;
      setFiles((prev) => {
        const key = (f: File) =>
          `${f.name}__${f.size}__${(f as any).lastModified ?? ""}`;
        const existing = new Set(prev.map(key));
        const merged: File[] = [...prev];
        for (const f of list) {
          const k = key(f);
          if (!existing.has(k)) {
            merged.push(f);
            existing.add(k);
          }
        }
        // Allow re-selecting the same files by clearing the input's value.
        if (e.target) (e.target as HTMLInputElement).value = "";
        return merged;
      });
    },
    []
  );

  const removeFile = useCallback((idx: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== idx));
  }, []);

  const clearFiles = useCallback(() => {
    setFiles([]);
  }, []);

  const canUpload = useMemo(
    () => orgId && projId && files.length > 0,
    [orgId, projId, files.length]
  );

  const stopPolling = useCallback((uploadId: string) => {
    const timers = pollTimers.current;
    if (timers[uploadId]) {
      clearInterval(timers[uploadId]);
      delete timers[uploadId];
    }
  }, []);

  const pollStatus = useCallback(
    (uploadId: string) => {
      stopPolling(uploadId);
      const startedAt = Date.now();
      const timer = setInterval(async () => {
        try {
          const st: StatusResponse = await getContractStatus(uploadId);
          setUploads((prev) =>
            prev.map((u) =>
              u.upload_id === uploadId
                ? {
                    ...u,
                    document_id: st.document_id || u.document_id,
                    status: st.status,
                    progress:
                      typeof st.progress === "number"
                        ? st.progress
                        : st.status === "completed"
                        ? 100
                        : u.progress,
                    categories: st.categories || null,
                    error: st.error || null,
                    processing_stage: st.processing_stage || null,
                    stage_label: st.stage_label || null,
                  }
                : u
            )
          );
          if (isTerminalContractStatus(st.status)) {
            stopPolling(uploadId);
            // Refresh the "Previously Uploaded" list when an upload completes
            if (st.status === "completed") {
              refreshPrevUploads();
            }
          }
          if (Date.now() - startedAt > 10 * 60 * 1000) {
            stopPolling(uploadId);
            setUploads((prev) =>
              prev.map((u) =>
                u.upload_id === uploadId
                  ? {
                      ...u,
                      status: u.status === "completed" ? "completed" : "failed",
                      error: u.status === "completed" ? u.error || null : "Upload processing timed out. Refresh the page to verify final status.",
                    }
                  : u
              )
            );
          }
        } catch {
          if (Date.now() - startedAt > 10 * 60 * 1000) {
            stopPolling(uploadId);
          }
        }
      }, 1500);
      pollTimers.current[uploadId] = timer;
    },
    [stopPolling, refreshPrevUploads]
  );

  useEffect(() => {
    const activePollTimers = pollTimers.current;
    return () => {
      uploadAbortRef.current?.abort();
      Object.keys(activePollTimers).forEach((uploadId) => stopPolling(uploadId));
    };
  }, [stopPolling]);

  // When all uploads in the current batch finish (completed or failed),
  // refresh the Previously Uploaded list so the new files appear after page reloads too.
  useEffect(() => {
    if (uploads.length === 0) return;
    const allDone = uploads.every((u) => isTerminalContractStatus(u.status));
    if (allDone) {
      refreshPrevUploads();
    }
  }, [uploads, refreshPrevUploads]);

  // Generic download helper
  const downloadBy = useCallback(
    async (params: { document_id?: string; filename?: string }) => {
      try {
        if (!params.document_id) {
          toast.warning("File is not ready to download yet.");
          return;
        }
        const fname = params.filename || "contract";
        const blob = await downloadContractDocument(params.document_id);
        const url = window.URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = fname;
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
        window.URL.revokeObjectURL(url);
      } catch (e) {
        console.error("Download failed", e);
        toast.error(extractErrorMessage(e, "Failed to download file."));
      }
    },
    []
  );

  // Download a just-uploaded item (progress list)
  const handleDownloadUpload = useCallback(
    (u: UploadProgress) => {
      const filename = (u.file && u.file.name) || "contract.pdf";
      return downloadBy({ document_id: u.document_id, filename });
    },
    [downloadBy]
  );

  // Download an existing (previously uploaded) item
  const handleDownloadExisting = useCallback(
    (pu: PrevUpload) => {
      const filename = pu.filename || "contract.pdf";
      return downloadBy({ document_id: pu.document_id, filename });
    },
    [downloadBy]
  );

  // Drag & Drop handlers
  const onDragOver = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      e.stopPropagation();
      if (!dragging) setDragging(true);
    },
    [dragging]
  );

  const onDragLeave = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      e.stopPropagation();
      if (dragging) setDragging(false);
    },
    [dragging]
  );

  const onDrop = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setDragging(false);
    const dt = e.dataTransfer;
    if (dt && dt.files && dt.files.length > 0) {
      const newFiles = Array.from(dt.files);
      setFiles((prev) => {
        const key = (f: File) =>
          `${f.name}__${f.size}__${(f as any).lastModified ?? ""}`;
        const existing = new Set(prev.map(key));
        const merged = [...prev];
        for (const file of newFiles) {
          const fileKey = key(file);
          if (!existing.has(fileKey)) {
            existing.add(fileKey);
            merged.push(file);
          }
        }
        return merged;
      });
      dt.clearData();
    }
  }, []);

  const uploadFile = useCallback(
    async (f: File, controller: AbortController) => {
      const CHUNK_THRESHOLD = 5 * 1024 * 1024;
      const isChunked = f.size > CHUNK_THRESHOLD;

      try {
        const session = await createContractUploadSession(
          {
            filename: f.name,
            organization_id: orgId,
            project_id: projId,
          },
          controller.signal
        );
        setUploads((prev) =>
          prev.map((u) =>
            u.file === f
              ? {
                  ...u,
                  upload_id: session.upload_id,
                  error: null,
                }
              : u
          )
        );

        if (!isChunked) {
          const resp = await uploadContractsMultipart(
            [f],
            orgId,
            projId || undefined,
            undefined,
            [session.upload_id],
            controller.signal
          );
          const result: UploadResult | undefined = resp.results?.[0];
          if (!result) {
            throw new Error("Upload did not return a result.");
          }
          setUploads((prev) =>
            prev.map((u) =>
              u.file === f
                ? {
                    ...u,
                    progress: 100,
                    upload_id: result.upload_id,
                    document_id: result.document_id,
                    status: "queued",
                    error: null,
                  }
                : u
            )
          );
          pollStatus(result.upload_id);
          return;
        }

        const result = await uploadContractInChunks(
          f,
          orgId,
          projId || undefined,
          {
            chunkSize: session.max_chunk_size_bytes,
            uploadId: session.upload_id,
            signal: controller.signal,
            onProgress: (p) => {
              setUploads((prev) =>
                prev.map((u) => (u.file === f ? { ...u, progress: p } : u))
              );
            },
          }
        );
        setUploads((prev) =>
          prev.map((u) =>
            u.file === f
              ? {
                  ...u,
                  progress: 100,
                  upload_id: result.upload_id,
                  document_id: result.document_id || undefined,
                  status: "queued",
                  error: null,
                }
              : u
          )
        );
        if (result.upload_id) {
          pollStatus(result.upload_id);
        }
      } catch (e: any) {
        const isCanceled =
          e?.name === "CanceledError" ||
          e?.code === "ERR_CANCELED" ||
          e?.message === "Upload canceled";
        const message = isCanceled
          ? "Upload canceled by user."
          : extractErrorMessage(e, "Upload failed.");
        setUploads((prev) =>
          prev.map((u) =>
            u.file === f ? { ...u, status: "failed", error: message } : u
          )
        );
      }
    },
    [orgId, projId, pollStatus]
  );

  const handleUpload = useCallback(async () => {
    if (!orgId) {
      toast.warning("Please select an organization.");
      return;
    }
    if (!projId) {
      toast.warning("Please select a project.");
      return;
    }
    if (files.length === 0) {
      toast.warning("Select one or more files.");
      return;
    }

    const controller = new AbortController();
    uploadAbortRef.current = controller;
    setUploadingBatch(true);
    const CHUNK_THRESHOLD = 5 * 1024 * 1024;

    const smallFiles = files.filter((f) => f.size <= CHUNK_THRESHOLD);
    const largeFiles = files.filter((f) => f.size > CHUNK_THRESHOLD);

    setUploads([
      ...smallFiles.map<UploadProgress>((f) => ({
        file: f,
        mode: "multipart",
        progress: 0,
      })),
      ...largeFiles.map<UploadProgress>((f) => ({
        file: f,
        mode: "chunked",
        progress: 0,
      })),
    ]);

    try {
      for (const f of smallFiles) {
        if (controller.signal.aborted) break;
        await uploadFile(f, controller);
      }
      for (const f of largeFiles) {
        if (controller.signal.aborted) break;
        await uploadFile(f, controller);
      }
    } finally {
      if (uploadAbortRef.current === controller) {
        uploadAbortRef.current = null;
      }
      setUploadingBatch(false);
    }
  }, [files, orgId, projId, uploadFile]);

  const handleCancelUploads = useCallback(() => {
    uploadAbortRef.current?.abort();
    setUploadingBatch(false);
    setUploads((prev) =>
      prev.map((upload) =>
        isTerminalContractStatus(upload.status)
          ? upload
          : { ...upload, status: "failed", error: upload.error || "Upload canceled by user." }
      )
    );
  }, []);

  const retryUpload = useCallback(
    async (upload: UploadProgress) => {
      if (!orgId || !projId || uploadingBatch) {
        return;
      }
      if (upload.upload_id) {
        stopPolling(upload.upload_id);
      }
      const controller = new AbortController();
      uploadAbortRef.current = controller;
      setUploadingBatch(true);
      setUploads((prev) =>
        prev.map((item) =>
          item.file === upload.file
            ? {
                ...item,
                progress: 0,
                upload_id: undefined,
                document_id: undefined,
                status: undefined,
                categories: null,
                error: null,
              }
            : item
        )
      );
      try {
        await uploadFile(upload.file, controller);
      } finally {
        if (uploadAbortRef.current === controller) {
          uploadAbortRef.current = null;
        }
        setUploadingBatch(false);
      }
    },
    [orgId, projId, stopPolling, uploadFile, uploadingBatch]
  );

  return (
    <div className="max-w-7xl mx-auto p-6 space-y-6">
      <PageHeader
        icon={<UploadCloud className="h-6 w-6" />}
        title="Upload Contracts"
        description="Upload and organize contract documents by organization and project"
      />

      {/* Organization & Project Selection */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Building2 className="h-5 w-5" />
            Contract Details
          </CardTitle>
          <CardDescription>
            Please fill in the contract information and select the associated
            organization and project
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <label className="text-sm font-medium text-gray-700">
                Organization *
              </label>
              <select
                data-testid="contract-upload-org-select"
                className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                value={orgId}
                onChange={(e) => {
                  setOrgId(e.target.value);
                  setProjId("");
                }}
                disabled={orgLoading}
              >
                <option value="">Select organization</option>
                {organizations.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.name}
                  </option>
                ))}
              </select>
              {orgLoading && (
                <p className="text-xs text-gray-500 flex items-center gap-1">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Loading organizations...
                </p>
              )}
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium text-gray-700">
                Project *
              </label>
              <select
                data-testid="contract-upload-project-select"
                className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 disabled:bg-gray-50 disabled:text-gray-500"
                value={projId}
                onChange={(e) => setProjId(e.target.value)}
                disabled={!orgId || projLoading}
              >
                <option value="">
                  {!orgId
                    ? "Please select an organization first"
                    : "Select project"}
                </option>
                {projects.map((p) => (
                  <option key={p._id} value={p._id}>
                    {p.name}
                  </option>
                ))}
              </select>
              {projLoading && (
                <p className="text-xs text-gray-500 flex items-center gap-1">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Loading projects...
                </p>
              )}
            </div>
          </div>
        </CardContent>
        <CardFooter>
          <button
            onClick={() => refreshPrevUploads(true)}
            disabled={prevLoading || !hasMore}
            className={`px-4 py-2 rounded-md text-sm font-medium ${
              prevLoading || !hasMore
                ? "bg-gray-200 text-gray-500 cursor-not-allowed"
                : "bg-white border border-gray-300 text-gray-800 hover:bg-gray-50"
            }`}
            title={hasMore ? "Load more uploads" : "No more items"}
          >
            {prevLoading
              ? "Loading..."
              : hasMore
              ? "Load more"
              : "No more items"}
          </button>
        </CardFooter>
      </Card>

      {/* File Upload Area */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <FolderOpen className="h-5 w-5" />
            Contract Files *
          </CardTitle>
          <CardDescription>
            Supported formats: PDF and DOCX. Server-side validation enforces the
            upload limits and file security checks.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div
            ref={dropRef}
            onDragOver={onDragOver}
            onDragLeave={onDragLeave}
            onDrop={onDrop}
            className={`
              relative border-2 border-dashed rounded-lg p-8 text-center transition-colors
              ${
                dragging
                  ? "border-blue-500 bg-blue-50"
                  : "border-gray-300 hover:border-gray-400"
              }
            `}
          >
            <input
              data-testid="contract-upload-file-input"
              type="file"
              multiple
              accept=".pdf,.docx"
              onChange={onSelectFiles}
              className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
            />
            <div className="space-y-4">
              <div className="mx-auto w-12 h-12 bg-gray-100 rounded-full flex items-center justify-center">
                <UploadCloud className="h-6 w-6 text-gray-600" />
              </div>
              <div>
                <p className="text-lg font-medium text-gray-900">
                  {dragging
                    ? "Drop files here"
                    : "Choose files or drag and drop"}
                </p>
                <p className="text-sm text-gray-500 mt-1">
                  PDF and DOCX only. Large files upload in secure chunks.
                </p>
              </div>
            </div>
          </div>

          {/* Selected Files List */}
          {files.length > 0 && (
            <div className="mt-6 space-y-3">
              <div className="flex items-center justify-between">
                <h4 className="text-sm font-medium text-gray-900">
                  Selected Files ({files.length})
                </h4>
                <button
                  onClick={clearFiles}
                  className="text-sm text-red-600 hover:text-red-700"
                >
                  Clear all
                </button>
              </div>
              <div className="space-y-2">
                {files.map((file, idx) => (
                  <div
                    key={`${file.name}-${idx}`}
                    className="flex items-center justify-between p-3 bg-gray-50 rounded-lg"
                  >
                    <div className="flex items-center gap-3">
                      <FileText className="h-5 w-5 text-gray-400" />
                      <div>
                        <p className="text-sm font-medium text-gray-900">
                          {file.name}
                        </p>
                        <p className="text-xs text-gray-500">
                          {bytesToHuman(file.size)}
                        </p>
                      </div>
                    </div>
                    <button
                      onClick={() => removeFile(idx)}
                      className="p-1 text-gray-400 hover:text-red-500 transition-colors"
                    >
                      <X className="h-4 w-4" />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}
        </CardContent>
        <CardFooter className="flex gap-3">
          <button
            data-testid="contract-upload-submit"
            onClick={handleUpload}
            disabled={!canUpload || uploadingBatch}
            className={`
              flex-1 flex items-center justify-center gap-2 px-4 py-2 rounded-md font-medium transition-colors
              ${
                canUpload && !uploadingBatch
                  ? "bg-blue-600 hover:bg-blue-700 text-white"
                  : "bg-gray-300 text-gray-500 cursor-not-allowed"
              }
            `}
          >
            {uploadingBatch ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <UploadIcon className="h-4 w-4" />
            )}
            {uploadingBatch ? "Uploading..." : `Upload Contract${files.length > 1 ? "s" : ""}`}
          </button>
          {uploadingBatch && (
            <button
              onClick={handleCancelUploads}
              className="px-4 py-2 rounded-md font-medium border border-gray-300 text-gray-700 hover:bg-gray-50"
            >
              Cancel Uploads
            </button>
          )}
        </CardFooter>
      </Card>

      {/* Previously Uploaded */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between w-full">
            <div>
              <CardTitle>Previously Uploaded</CardTitle>
              <CardDescription>
                Recent uploads for the selected organization and project
              </CardDescription>
            </div>
            <div className="ml-auto">
              <Link
                to="/contracts/search"
                className="inline-flex items-center px-3 py-2 text-sm font-medium rounded-md bg-blue-600 text-white hover:bg-blue-700 border border-blue-600 transition-colors"
                title="Go to Search Clauses"
              >
                Search Clauses
              </Link>
            </div>
          </div>
        </CardHeader>
        <CardContent>
          {prevLoading ? (
            <p className="text-sm text-gray-500 flex items-center gap-1">
              <Loader2 className="h-4 w-4 animate-spin" /> Loading...
            </p>
          ) : prevUploads.length === 0 ? (
            <p className="text-sm text-gray-500">No previous uploads found.</p>
          ) : (
            <ul className="divide-y divide-gray-200">
              {prevUploads.map((pu, idx) => (
                <li
                  key={(pu.document_id || pu.upload_id || "") + String(idx)}
                  className="py-3 flex items-center justify-between gap-4"
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <FileText className="h-5 w-5 text-gray-400" />
                    <div className="min-w-0">
                      <div className="text-sm font-medium text-gray-900 truncate">
                        {pu.filename}
                      </div>
                      <div className="text-xs text-gray-500">
                        {pu.size ? `${bytesToHuman(pu.size)} • ` : ""}
                        {pu.createdAt
                          ? new Date(pu.createdAt).toLocaleString()
                          : ""}
                      </div>
                    </div>
                  </div>
                  <div className="flex items-center gap-2">
                    <Badge
                      variant={statusVariant(
                        (pu.status as any) || ("unknown" as any)
                      )}
                    >
                      {pu.status || "unknown"}
                    </Badge>
                    <button
                      onClick={() => handleDownloadExisting(pu)}
                      title="Download file"
                      className="p-1 rounded border border-blue-200 text-blue-600 hover:bg-blue-50"
                    >
                      <Download className="h-4 w-4" />
                    </button>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      {/* Upload Progress */}
      {uploads.length > 0 && (
        <Card data-testid="contract-upload-progress">
          <CardHeader>
            <CardTitle>Upload Progress</CardTitle>
            <CardDescription>
              Track the progress of your contract uploads
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="space-y-4">
              {uploads.map((upload, idx) => (
                <div key={`${upload.file.name}-${idx}`} className="space-y-2">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3">
                      {getStatusIcon(upload.status)}
                      <div>
                        <p className="text-sm font-medium text-gray-900">
                          {upload.file.name}
                        </p>
                        <p className="text-xs text-gray-500">
                          {bytesToHuman(upload.file.size)} • {upload.mode}
                        </p>
                        {upload.stage_label && (
                          <p className="text-xs text-blue-600">
                            {upload.stage_label}
                          </p>
                        )}
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      {upload.status === "failed" && (
                        <button
                          onClick={() => retryUpload(upload)}
                          disabled={uploadingBatch}
                          title="Retry file upload"
                          className={`p-1 rounded border text-sm inline-flex items-center gap-1 ${
                            uploadingBatch
                              ? "border-gray-200 text-gray-400 cursor-not-allowed"
                              : "border-amber-200 text-amber-700 hover:bg-amber-50"
                          }`}
                        >
                          <RotateCcw className="h-4 w-4" />
                        </button>
                      )}
                      {upload.status && (
                        <Badge variant={statusVariant(upload.status)}>
                          {upload.status}
                        </Badge>
                      )}
                      <span className="text-sm text-gray-500">
                        {upload.progress}%
                      </span>
                      <button
                        onClick={() => handleDownloadUpload(upload)}
                        disabled={
                          !(upload.status === "completed" || upload.document_id)
                        }
                        title="Download file"
                        className={`p-1 rounded border text-sm inline-flex items-center gap-1 ${
                          upload.status === "completed" || upload.document_id
                            ? "border-blue-200 text-blue-600 hover:bg-blue-50"
                            : "border-gray-200 text-gray-400 cursor-not-allowed"
                        }`}
                      >
                        <Download className="h-4 w-4" />
                      </button>
                    </div>
                  </div>

                  {/* Progress Bar */}
                  <div className="w-full bg-gray-200 rounded-full h-2">
                    <div
                      className={`h-2 rounded-full transition-all duration-300 ${
                        upload.status === "completed"
                          ? "bg-green-500"
                          : upload.status === "failed" || upload.status === "human_review_required"
                          ? "bg-red-500"
                          : "bg-blue-500"
                      }`}
                      style={{ width: `${upload.progress}%` }}
                    />
                  </div>

                  {/* Error Message */}
                  {upload.error && (
                    <p className="text-sm text-red-600 bg-red-50 p-2 rounded">
                      {upload.error}
                    </p>
                  )}

                  {/* Categories */}
                  {upload.categories && upload.categories.length > 0 && (
                    <div className="flex flex-wrap gap-1">
                      {upload.categories.map((cat) => (
                        <Badge key={cat} variant="outline" className="text-xs">
                          {cat}
                        </Badge>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
};

export default ContractsUploadPage;
