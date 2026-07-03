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

/**
 * Re-run ingestion for an existing contract to rebuild its vector index without
 * re-uploading. Mirrors POST /contracts/{document_id}/reindex.
 */
export async function reindexContract(documentId: string): Promise<StatusResponse> {
  const { data } = await api.post(`/contracts/${documentId}/reindex`, {});
  return data as StatusResponse;
}

// --- Clause Index (clause-wise contract records) --------------------------

export interface ClauseRow {
  clause_uid: string;
  clause_no?: string | null;
  clause_title?: string | null;
  parent_clause_no?: string | null;
  clause_path?: string[] | null;
  level?: number | null;
  document_type?: string | null;
  volume?: string | null;
  page_start?: number | null;
  page_end?: number | null;
  chunk_type?: string | null;
  chunk_part?: number | null;
  chunk_total?: number | null;
  confidence?: string | null;
  quality_status?: string | null;
  is_current?: boolean;
  is_superseded?: boolean;
  superseded_by_clause_id?: string | null;
  is_authorised_for_ai?: boolean;
  embedding_status?: string | null;
  human_review_required?: boolean;
  manually_edited?: boolean;
  verified_by?: string | null;
  linked_clause_no?: string | null;
  table_title?: string | null;
}

export async function listDocumentClauses(
  documentId: string,
): Promise<{ document_id: string; count: number; clauses: ClauseRow[] }> {
  const { data } = await api.get(`/contracts/${documentId}/clauses`);
  return data;
}

export async function updateClause(
  clauseUid: string,
  patch: {
    clause_title?: string;
    mark_verified?: boolean;
    is_superseded?: boolean;
    superseded_by_clause_id?: string;
  },
): Promise<ClauseRow> {
  const { data } = await api.patch(`/contracts/clauses/${clauseUid}`, patch);
  return data as ClauseRow;
}

export async function regenerateClauseEmbedding(clauseUid: string): Promise<ClauseRow> {
  const { data } = await api.post(`/contracts/clauses/${clauseUid}/regenerate-embedding`, {});
  return data as ClauseRow;
}

export async function splitClause(clauseUid: string, splitAt: number): Promise<{ parts: ClauseRow[] }> {
  const { data } = await api.post(`/contracts/clauses/${clauseUid}/split`, { split_at: splitAt });
  return data;
}

export async function mergeClauses(clauseUids: string[]): Promise<ClauseRow> {
  const { data } = await api.post(`/contracts/clauses/merge`, { clause_uids: clauseUids });
  return data as ClauseRow;
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

// ---------------------------------------------------------------------------
// Contract Document Appraisal (Contract Appraisal Report — v2 Phase 1).
// Mirrors backend routers/contract_appraisal.py.
// ---------------------------------------------------------------------------

export type AppraisalJobStatus =
  | "queued"
  | "running"
  | "generating"
  | "completed"
  | "failed"
  | "cancelled";

export type ReportStatus =
  | "draft"
  | "under_review"
  | "approved"
  | "superseded"
  | "rejected";

export interface AppraisalJob {
  _id: string;
  organization_id?: string | null;
  project_id?: string | null;
  document_ids: string[];
  status: AppraisalJobStatus;
  current_step?: string | null;
  progress: number;
  error_message?: string | null;
  report_id?: string | null;
}

export interface AppraisalSection {
  key: string;
  title: string;
  markdown: string;
  citations: ContractCitation[];
  supported: boolean;
  confidence: number;
}

export interface AppraisalReport {
  _id: string;
  organization_id?: string | null;
  project_id?: string | null;
  job_id?: string | null;
  document_ids: string[];
  report_version: number;
  status: ReportStatus;
  document_completeness_status: "complete" | "incomplete" | "requires_review";
  missing_documents: string[];
  executive_summary: string;
  full_report_markdown: string;
  sections: AppraisalSection[];
  citations: ContractCitation[];
  overall_risk_rating?: string | null;
  confidence_score: number;
  review_comments_count: number;
  approved_by?: string | null;
  is_locked: boolean;
  created_at?: string | null;
}

export interface AppraisalReviewComment {
  _id: string;
  report_id: string;
  commented_by?: string | null;
  comment_text: string;
  section_reference?: string | null;
  status: string;
  created_at?: string | null;
}

export async function generateAppraisal(payload: {
  organization_id?: string;
  project_id: string;
  document_ids?: string[];
}): Promise<AppraisalJob> {
  const { data } = await api.post("/contracts/appraisal/generate", payload);
  return data as AppraisalJob;
}

export async function getAppraisalJob(jobId: string): Promise<AppraisalJob> {
  const { data } = await api.get(`/contracts/appraisal/jobs/${jobId}`);
  return data as AppraisalJob;
}

export async function listAppraisals(params?: {
  organization_id?: string;
  project_id?: string;
}): Promise<AppraisalReport[]> {
  const { data } = await api.get("/contracts/appraisal", { params });
  return Array.isArray(data) ? (data as AppraisalReport[]) : [];
}

export async function getAppraisal(reportId: string): Promise<AppraisalReport> {
  const { data } = await api.get(`/contracts/appraisal/${reportId}`);
  return data as AppraisalReport;
}

/** The live report for an (org, project, document) selection, or null. */
export async function getExistingAppraisal(params: {
  organization_id?: string;
  project_id: string;
  document_id?: string;
}): Promise<AppraisalReport | null> {
  const { data } = await api.get("/contracts/appraisal/existing", { params });
  return data ? (data as AppraisalReport) : null;
}

export async function editAppraisal(
  reportId: string,
  fields: {
    full_report_markdown?: string;
    executive_summary?: string;
    overall_risk_rating?: string;
  },
): Promise<AppraisalReport> {
  const { data } = await api.put(`/contracts/appraisal/${reportId}`, fields);
  return data as AppraisalReport;
}

export async function deleteAppraisal(reportId: string): Promise<void> {
  await api.delete(`/contracts/appraisal/${reportId}`);
}

export async function approveAppraisal(reportId: string): Promise<AppraisalReport> {
  const { data } = await api.post(`/contracts/appraisal/${reportId}/approve`, {});
  return data as AppraisalReport;
}

export async function rejectAppraisal(reportId: string): Promise<AppraisalReport> {
  const { data } = await api.post(`/contracts/appraisal/${reportId}/reject`, {});
  return data as AppraisalReport;
}

export async function regenerateAppraisal(reportId: string): Promise<AppraisalJob> {
  const { data } = await api.post(`/contracts/appraisal/${reportId}/regenerate`, {});
  return data as AppraisalJob;
}

export async function addAppraisalComment(
  reportId: string,
  comment_text: string,
  section_reference?: string,
): Promise<AppraisalReviewComment> {
  const { data } = await api.post(`/contracts/appraisal/${reportId}/review-comments`, {
    comment_text,
    section_reference,
  });
  return data as AppraisalReviewComment;
}

export async function listAppraisalComments(reportId: string): Promise<AppraisalReviewComment[]> {
  const { data } = await api.get(`/contracts/appraisal/${reportId}/review-comments`);
  return Array.isArray(data) ? (data as AppraisalReviewComment[]) : [];
}

export async function exportAppraisalDocx(reportId: string): Promise<Blob> {
  const { data } = await api.get(`/contracts/appraisal/${reportId}/export/docx`, {
    responseType: "blob",
  });
  return data instanceof Blob ? data : new Blob([data]);
}

export async function exportAppraisalPdf(reportId: string): Promise<Blob> {
  const { data } = await api.get(`/contracts/appraisal/${reportId}/export/pdf`, {
    responseType: "blob",
  });
  return data instanceof Blob ? data : new Blob([data]);
}

// --- registers + clause library (Phase 2) ---------------------------------

export type RegisterKind = "obligations" | "risks" | "key-dates";

export interface RegisterItem {
  _id: string;
  report_id: string;
  clause_reference?: string | null;
  document_name?: string | null;
  page_number?: number | null;
  source_quote?: string | null;
  confidence_score: number;
  verification_status: string;
  status?: string;
  // obligation
  party?: string;
  obligation_title?: string;
  obligation_description?: string;
  // risk
  risk_title?: string;
  risk_description?: string;
  risk_category?: string;
  severity?: string | null;
  // key date
  date_title?: string;
  date_type?: string;
}

export interface ClauseEntry {
  document_id?: string | null;
  document_name?: string | null;
  clause_number?: string | null;
  clause_title?: string | null;
  page_numbers: number[];
  snippet: string;
}

export async function createAppraisalRegisters(
  reportId: string,
): Promise<{ report_id: string; created: Record<string, number> }> {
  const { data } = await api.post(`/contracts/appraisal/${reportId}/create-registers`, {});
  return data;
}

export async function listRegister(
  kind: RegisterKind,
  params: { organization_id?: string; project_id?: string; report_id?: string },
): Promise<RegisterItem[]> {
  const { data } = await api.get(`/contracts/${kind}`, { params });
  return Array.isArray(data) ? (data as RegisterItem[]) : [];
}

export async function updateRegisterItem(
  kind: RegisterKind,
  itemId: string,
  fields: Partial<Pick<RegisterItem, "verification_status" | "status" | "severity">>,
): Promise<RegisterItem> {
  const { data } = await api.put(`/contracts/${kind}/${itemId}`, fields);
  return data as RegisterItem;
}

export async function listClauses(params: {
  organization_id?: string;
  project_id?: string;
  q?: string;
  limit?: number;
}): Promise<ClauseEntry[]> {
  const { data } = await api.get("/contracts/clauses", { params });
  return Array.isArray(data) ? (data as ClauseEntry[]) : [];
}

// --- uploaded contract documents (for the appraisal source dropdown) ------

export interface ContractUpload {
  document_id: string;
  upload_id?: string | null;
  filename?: string | null;
  status: string;
  createdAt?: string | null;
}

export async function listContractUploads(params?: {
  organization_id?: string;
  project_id?: string;
  limit?: number;
}): Promise<ContractUpload[]> {
  const { data } = await api.get("/contracts/list", { params });
  const uploads = data?.uploads;
  return Array.isArray(uploads) ? (uploads as ContractUpload[]) : [];
}
