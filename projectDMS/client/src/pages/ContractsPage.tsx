import React, { useCallback, useMemo, useRef, useState } from "react";
import { toast } from "sonner";
import {
  uploadContractsMultipart,
  uploadContractInChunks,
  getContractStatus,
  searchContracts,
  UploadResult,
  StatusResponse,
  ContractSearchResponse,
} from "@/services/contracts-api";

type UploadProgress = {
  file: File;
  mode: "multipart" | "chunked";
  progress: number; // 0-100
  upload_id?: string;
  status?: "queued" | "processing" | "completed" | "failed" | "unknown";
  error?: string | null;
};

const bytesToHuman = (n: number) => {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  return `${(n / (1024 * 1024 * 1024)).toFixed(1)} GB`;
};

const ContractsPage: React.FC = () => {
  const [orgId, setOrgId] = useState<string>(
    () => window.localStorage.getItem("org_id") || ""
  );
  const [projId, setProjId] = useState<string>(
    () => window.localStorage.getItem("proj_id") || ""
  );
  const [tags, setTags] = useState<string>("");
  const [files, setFiles] = useState<File[]>([]);
  const [uploads, setUploads] = useState<UploadProgress[]>([]);
  const [query, setQuery] = useState<string>("");
  const [summarize, setSummarize] = useState<boolean>(true);
  const [searching, setSearching] = useState<boolean>(false);
  const [searchResult, setSearchResult] =
    useState<ContractSearchResponse | null>(null);
  const pollTimers = useRef<Record<string, any>>({});

  const parsedTags = useMemo(() => {
    return tags
      .split(",")
      .map((t) => t.trim())
      .filter((t) => !!t);
  }, [tags]);

  const onSelectFiles = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const list = e.target.files ? Array.from(e.target.files) : [];
      setFiles(list);
    },
    []
  );

  const canUpload = useMemo(() => {
    return orgId && files.length > 0;
  }, [orgId, files.length]);

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
      // Start polling every 1.5s
      const timer = setInterval(async () => {
        try {
          const st: StatusResponse = await getContractStatus(uploadId);
          setUploads((prev) =>
            prev.map((u) =>
              u.upload_id === uploadId
                ? { ...u, status: st.status, error: st.error || null }
                : u
            )
          );
          if (st.status === "completed" || st.status === "failed") {
            stopPolling(uploadId);
          }
        } catch (e) {
          // Ignore transient errors; keep polling for some cycles
        }
      }, 1500);
      pollTimers.current[uploadId] = timer;
    },
    [stopPolling]
  );

  const handleUpload = useCallback(async () => {
    if (!orgId) {
      toast.warning("Please provide Organization ID");
      return;
    }
    if (files.length === 0) {
      toast.warning("Select one or more files");
      return;
    }

    // Strategy:
    // - For files <= 15MB, use single multipart (batched) upload.
    // - For files > 15MB, use chunked upload with progress.
    const SMALL_LIMIT = 15 * 1024 * 1024;

    const smallFiles = files.filter((f) => f.size <= SMALL_LIMIT);
    const largeFiles = files.filter((f) => f.size > SMALL_LIMIT);

    // Initialize upload progress list
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

    // First, send small files via single multipart request if any
    if (smallFiles.length > 0) {
      try {
        const resp = await uploadContractsMultipart(
          smallFiles,
          orgId,
          projId || undefined,
          parsedTags
        );
        // We only get queued results with unique upload_ids. Start polling each.
        const results: UploadResult[] = resp.results || [];
        setUploads((prev) =>
          prev.map((u) => {
            const match = results.find((r) => r.filename === u.file.name);
            if (u.mode === "multipart" && match) {
              return {
                ...u,
                progress: 100,
                upload_id: match.upload_id,
                status: "queued",
              };
            }
            return u;
          })
        );
        // Poll statuses
        results.forEach((r) => {
          if (r.upload_id) pollStatus(r.upload_id);
        });
      } catch (e: any) {
        console.error(e);
        setUploads((prev) =>
          prev.map((u) =>
            u.mode === "multipart"
              ? { ...u, status: "failed", error: e?.message || "Upload failed" }
              : u
          )
        );
      }
    }

    // Next, send large files via chunked upload
    for (const f of largeFiles) {
      try {
        const result = await uploadContractInChunks(
          f,
          orgId,
          projId || undefined,
          {
            chunkSize: 5 * 1024 * 1024,
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
                  upload_id: result.upload_id,
                  status: result.merged ? "queued" : "processing",
                }
              : u
          )
        );
        if (result.upload_id) pollStatus(result.upload_id);
      } catch (e: any) {
        console.error(e);
        setUploads((prev) =>
          prev.map((u) =>
            u.file === f
              ? { ...u, status: "failed", error: e?.message || "Upload failed" }
              : u
          )
        );
      }
    }
  }, [files, orgId, projId, parsedTags, pollStatus]);

  const handleSearch = useCallback(async () => {
    if (!query.trim()) {
      toast.warning("Enter a search query");
      return;
    }
    setSearching(true);
    setSearchResult(null);
    try {
      const resp = await searchContracts({
        query,
        organization_id: orgId || undefined,
        project_id: projId || undefined,
        limit: 10,
        top_docs: 6,
        chunks_per_doc: 2,
        summarize,
      });
      setSearchResult(resp);
    } catch (e) {
      console.error(e);
      toast.error("Search failed");
    } finally {
      setSearching(false);
    }
  }, [query, orgId, projId, summarize]);

  return (
    <div className="p-4 max-w-6xl mx-auto">
      <h1 className="text-2xl font-semibold mb-4">
        Contracts: Upload &amp; Semantic Search
      </h1>

      <section className="mb-6 border rounded p-4">
        <h2 className="font-semibold mb-2">1) Metadata</h2>
        <div className="grid sm:grid-cols-2 gap-3">
          <div>
            <label className="block text-sm mb-1">Organization ID</label>
            <input
              className="w-full border rounded px-3 py-2"
              placeholder="organization_id"
              value={orgId}
              onChange={(e) => setOrgId(e.target.value)}
            />
            <small className="text-gray-500">
              Defaulted from your session (localStorage org_id)
            </small>
          </div>
          <div>
            <label className="block text-sm mb-1">Project ID (optional)</label>
            <input
              className="w-full border rounded px-3 py-2"
              placeholder="project_id"
              value={projId}
              onChange={(e) => setProjId(e.target.value)}
            />
          </div>
          <div className="sm:col-span-2">
            <label className="block text-sm mb-1">
              Tags (comma-separated, optional)
            </label>
            <input
              className="w-full border rounded px-3 py-2"
              placeholder="e.g. GCC,SCC,Variation,Payment"
              value={tags}
              onChange={(e) => setTags(e.target.value)}
            />
          </div>
        </div>
      </section>

      <section className="mb-6 border rounded p-4">
        <h2 className="font-semibold mb-2">2) Upload Documents</h2>
        <div className="flex items-center gap-3 mb-3">
          <input type="file" multiple onChange={onSelectFiles} />
          <button
            className={`px-4 py-2 rounded text-white ${
              canUpload
                ? "bg-blue-600 hover:bg-blue-700"
                : "bg-gray-400 cursor-not-allowed"
            }`}
            disabled={!canUpload}
            onClick={handleUpload}
          >
            Upload
          </button>
        </div>

        {files.length > 0 && (
          <div className="mb-3">
            <div className="text-sm text-gray-600">
              Selected {files.length} file(s):
            </div>
            <ul className="list-disc pl-5 text-sm">
              {files.map((f) => (
                <li key={f.name}>
                  {f.name} — {bytesToHuman(f.size)}
                </li>
              ))}
            </ul>
          </div>
        )}

        {uploads.length > 0 && (
          <div className="mt-4">
            <h3 className="font-medium mb-2">Progress</h3>
            <div className="space-y-2">
              {uploads.map((u) => (
                <div
                  key={`${u.file.name}-${u.mode}`}
                  className="border rounded p-2"
                >
                  <div className="flex items-center justify-between">
                    <div className="font-medium">{u.file.name}</div>
                    <div className="text-xs text-gray-500">
                      {u.mode.toUpperCase()}
                    </div>
                  </div>
                  <div className="mt-2 h-2 bg-gray-200 rounded">
                    <div
                      className="h-2 bg-green-600 rounded"
                      style={{ width: `${u.progress}%` }}
                    />
                  </div>
                  <div className="mt-1 text-sm">
                    {u.status ? (
                      <span
                        className={`px-2 py-0.5 rounded text-white ${
                          u.status === "failed"
                            ? "bg-red-600"
                            : u.status === "completed"
                            ? "bg-green-600"
                            : "bg-blue-600"
                        }`}
                      >
                        {u.status}
                      </span>
                    ) : (
                      <span className="text-gray-500">uploading...</span>
                    )}
                    {u.upload_id && (
                      <span className="ml-2 text-gray-500 text-xs">
                        id: {u.upload_id}
                      </span>
                    )}
                    {u.error && (
                      <div className="text-red-600 text-sm mt-1">
                        Error: {u.error}
                      </div>
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </section>

      <section className="mb-6 border rounded p-4">
        <h2 className="font-semibold mb-2">3) Search Contracts</h2>
        <div className="grid sm:grid-cols-1 gap-3">
          <div>
            <label className="block text-sm mb-1">Query</label>
            <input
              className="w-full border rounded px-3 py-2"
              placeholder={`e.g. "termination clause" or "confidentiality"`}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          <div className="flex items-center gap-3">
            <label className="inline-flex items-center gap-2">
              <input
                type="checkbox"
                checked={summarize}
                onChange={(e) => setSummarize(e.target.checked)}
              />
              <span>Summarize matches with OpenAI</span>
            </label>
            <button
              className={`px-4 py-2 rounded text-white ${
                searching ? "bg-gray-400" : "bg-indigo-600 hover:bg-indigo-700"
              }`}
              disabled={searching}
              onClick={handleSearch}
            >
              {searching ? "Searching..." : "Search"}
            </button>
          </div>
        </div>

        {searchResult && (
          <div className="mt-4">
            {!!searchResult.summary && (
              <div className="mb-4 p-3 rounded bg-yellow-50 border border-yellow-200">
                <div className="font-medium mb-1">AI Summary</div>
                <div className="whitespace-pre-wrap text-sm">
                  {searchResult.summary}
                </div>
              </div>
            )}

            <div>
              <div className="font-medium mb-2">Top Excerpts</div>
              <ul className="space-y-3">
                {searchResult.results.map((r, idx) => (
                  <li
                    key={`${idx}-${r.chunk_index}`}
                    className="border rounded p-3"
                  >
                    <div className="flex items-center justify-between">
                      <div className="text-sm text-gray-600">
                        {r.source_filename ? (
                          <span className="font-medium">
                            {r.source_filename}
                          </span>
                        ) : (
                          "Unknown file"
                        )}
                        {typeof r.score === "number" && (
                          <span className="ml-2 text-xs text-gray-500">
                            score: {r.score.toFixed(3)}
                          </span>
                        )}
                      </div>
                      {!!r.document_id && (
                        <span className="text-xs text-gray-500">
                          doc: {r.document_id}
                        </span>
                      )}
                    </div>
                    <div className="mt-2 text-sm whitespace-pre-wrap">
                      {r.text.length > 1000
                        ? r.text.slice(0, 1000) + " ... (truncated)"
                        : r.text}
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        )}
      </section>

      <div className="text-xs text-gray-400">
        Note: Contract chunks are stored in vector storage within Mongo
        (document_vectors, uploadType="contract"). Large files use chunked
        uploads automatically.
      </div>
    </div>
  );
};

export default ContractsPage;
