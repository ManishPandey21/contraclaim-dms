import { api } from "./api";

export interface ContractUploadSessionResponse {
  upload_id: string;
  organization_id: string;
  project_id?: string | null;
  expires_at: string;
  max_file_size_bytes: number;
  max_chunk_size_bytes: number;
  max_chunks: number;
  allowed_extensions: string[];
  allowed_mime_types: string[];
}

export interface UploadResult {
  upload_id: string;
  document_id: string;
  filename: string;
  status: string;
}

export interface UploadMultipartResponse {
  organization_id: string;
  project_id?: string | null;
  results: UploadResult[];
}

export interface ChunkUploadResponse {
  upload_id: string;
  document_id?: string | null;
  filename: string;
  chunk_index: number;
  total_chunks: number;
  received: boolean;
  merged: boolean;
  scheduled: boolean;
  received_chunks?: number[];
  missing_chunks?: number[];
  upload_complete?: boolean;
}

export interface StatusResponse {
  upload_id: string;
  document_id?: string | null;
  status: "queued" | "processing" | "completed" | "failed" | "unknown";
  filename?: string | null;
  categories?: string[] | null;
  error?: string | null;
  organization_id?: string | null;
  project_id?: string | null;
  tags?: string[] | null;
  size?: number | null;
  queue_job_id?: string | null;
  updatedAt?: string | null;
  processing_stage?: string | null;
  stage_label?: string | null;
  progress?: number | null;
}

export interface HighlightOffset {
  start: number;
  end: number;
}

export interface ContractSource {
  document_id?: string | null;
  upload_id?: string | null;
  file_name?: string | null;
  clause_number?: string | null;
  clause_title?: string | null;
  section_heading?: string | null;
  clause_tags?: string[] | null;
  page_numbers?: number[] | null;
  page_number?: number | null;
  page?: number | null;
}

export interface SearchChunk {
  upload_id?: string | null;
  document_id?: string | null;
  file_name?: string | null;
  source_filename?: string | null;
  letterNo?: string | null;
  uploadType?: string | null;
  chunk_index: number;
  clause_number?: string | null;
  clause_title?: string | null;
  clause_type?: string | null;
  clause_level?: number | null;
  parent_clause_number?: string | null;
  is_complete_clause?: boolean;
  clause_start_position?: number | null;
  clause_end_position?: number | null;
  text: string;
  score: number;
  page_number?: number | null;
  page?: number | null;
  page_numbers?: number[] | null;
  section?: string | null;
  section_heading?: string | null;
  clause_tags?: string[] | null;
  offsets?: HighlightOffset[] | null;
}

export interface ContractSearchResponse {
  results: SearchChunk[];
  summary?: string | null;
  ai_summary_title?: string | null;
  sources?: ContractSource[];
  total_count?: number;
  has_more?: boolean;
  current_page?: number;
  page_size?: number;
  took_ms?: number;
  ai_summary?: string | null;
  aisummary?: string | null;
}

export async function createContractUploadSession(payload: {
  filename: string;
  organization_id?: string;
  project_id?: string;
}, signal?: AbortSignal): Promise<ContractUploadSessionResponse> {
  const { data } = await api.post("/contracts/upload-session", payload, { signal });
  return data;
}

export async function uploadContractsMultipart(
  files: File[],
  organization_id: string,
  project_id?: string,
  tags?: string[],
  uploadIds?: string[],
  signal?: AbortSignal
): Promise<UploadMultipartResponse> {
  const form = new FormData();
  files.forEach((f) => form.append("files", f, f.name));
  form.append("organization_id", organization_id);
  if (project_id) form.append("project_id", project_id);
  if (uploadIds?.length) {
    uploadIds.forEach((id) => form.append("upload_ids", id));
  }
  if (tags && tags.length) {
    tags.forEach((t) => form.append("tags", t));
  }
  const { data } = await api.post("/contracts/upload-multipart", form, {
    headers: { "Content-Type": "multipart/form-data" },
    signal,
  });
  return data;
}

export async function uploadContractInChunks(
  file: File,
  organization_id: string,
  project_id?: string,
  opts?: {
    chunkSize?: number;
    uploadId: string;
    onProgress?: (p: number) => void;
    tags?: string[];
    signal?: AbortSignal;
  }
): Promise<ChunkUploadResponse> {
  const chunkSize = opts?.chunkSize ?? 5 * 1024 * 1024;
  const uploadId = opts?.uploadId;
  if (!uploadId) {
    throw new Error("uploadId is required for chunked contract uploads");
  }
  const totalChunks = Math.ceil(file.size / chunkSize);

  let lastResp: ChunkUploadResponse = {
    upload_id: uploadId,
    filename: file.name,
    chunk_index: 0,
    total_chunks: totalChunks,
    received: false,
    merged: false,
    scheduled: false,
    received_chunks: [],
    missing_chunks: Array.from({ length: totalChunks }, (_, idx) => idx),
    upload_complete: false,
  };

  const uploaded = new Set<number>();
  for (let i = 0; i < totalChunks; i++) {
    if (opts?.signal?.aborted) {
      throw new Error("Upload canceled");
    }
    const start = i * chunkSize;
    const end = Math.min(file.size, start + chunkSize);
    const blob = file.slice(start, end);

    const form = new FormData();
    form.append("chunk", blob, `${file.name}.part${i}`);
    form.append("upload_id", uploadId);
    form.append("filename", file.name);
    form.append("chunkIndex", String(i));
    form.append("totalChunks", String(totalChunks));
    form.append("organization_id", organization_id);
    if (project_id) form.append("project_id", project_id);
    if (opts?.tags && opts.tags.length) {
      opts.tags.forEach((t) => form.append("tags", t));
    }

    const { data } = await api.post("/contracts/upload-chunk", form, {
      headers: { "Content-Type": "multipart/form-data" },
      signal: opts?.signal,
    });
    lastResp = data;
    (data.received_chunks || []).forEach((idx: number) => uploaded.add(idx));

    if (opts?.onProgress) {
      const progress = Math.round(((uploaded.size || i + 1) / totalChunks) * 100);
      opts.onProgress(progress);
    }
    if (data.upload_complete || data.merged) {
      break;
    }
  }

  return lastResp;
}

export async function getContractStatus(
  upload_id: string
): Promise<StatusResponse> {
  const { data } = await api.get("/contracts/status", {
    params: { upload_id },
  });
  return data;
}

export async function searchContracts(payload: {
  query: string;
  organization_id?: string;
  project_id?: string;
  document_id?: string;
  skip?: number; // pagination offset
  limit?: number;
  top_docs?: number;
  chunks_per_doc?: number;
  summarize?: boolean;
  tags?: string[];
  exact_phrase?: boolean;
  clause_number?: string;
  clause_title?: string;
  section_heading?: string;
  clause_tags?: string[];
  category_terms?: string[];
  page_from?: number;
  page_to?: number;
  signal?: AbortSignal;
}): Promise<ContractSearchResponse> {
  const { signal, ...body } = payload;
  const { data } = await api.post("/contracts/search", body, { signal });
  return data;
}

export async function downloadContractDocument(documentId: string): Promise<Blob> {
  const { data } = await api.get(`/contracts/${documentId}/download`, {
    responseType: "blob",
  });
  return data instanceof Blob ? data : new Blob([data]);
}

export type ContractRagRequest = {
  query: string;
  filters: {
    org_id: string;
    project_id: string;
    document_id?: string | null;
    tags?: string[];
  };
  limit?: number;
  strategy?: "vanilla" | "hyde" | "rag_fusion";
  backend?: "auto" | "qdrant" | "mongo";
  use_enriched_text?: boolean;
  answer_style?: string;
  max_tokens?: number;
};

export type ContractCitation = {
  document_id: string;
  chunk_id: string;
  page?: number | null;
  score?: number | null;
  snippet: string;
  document_title?: string | null;
  letter_no?: string | null;
  file_name?: string | null;
  clause_number?: string | null;
  clause_title?: string | null;
  section_heading?: string | null;
  page_numbers?: number[] | null;
};

export type ContractRagResponse = {
  answer: string;
  citations: ContractCitation[];
  strategy_used: string;
  timings: Record<string, number>;
};

export async function askContractQuestion(payload: ContractRagRequest): Promise<ContractRagResponse> {
  const { data } = await api.post("/v1/retrieval/rag", payload);
  return data;
}

export type IterationTrace = {
  iteration: number;
  queries: string[];
  retrieved_ids: string[];
  critique?: string | null;
  refinements?: string[];
  notes?: string | null;
};

export type ContractIterativeQARequest = ContractRagRequest & {
  require_citations?: boolean;
  max_iterations?: number;
  metadata_filters?: Record<string, any>;
};

export type ContractIterativeQAResponse = {
  answer: string;
  citations: ContractCitation[];
  strategy_used: string;
  timings: Record<string, number>;
  trace?: IterationTrace[];
};

export async function askIterativeContractQuestion(
  payload: ContractIterativeQARequest,
  signal?: AbortSignal
): Promise<ContractIterativeQAResponse> {
  const { data } = await api.post("/v1/retrieval/contract-qa", payload, { signal });
  return data;
}
