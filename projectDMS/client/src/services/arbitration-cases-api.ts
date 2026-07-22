import { api } from "./api";

export type ArbitrationCaseStatus =
  | "intake"
  | "matrix_preparation"
  | "ready_for_drafting"
  | "drafting"
  | "under_review"
  | "approved"
  | "filed"
  | "archived";

export type PartyPerspective = "claimant" | "respondent" | "both" | "neutral";

export interface ArbitrationCase {
  _id: string;
  organization_id?: string | null;
  project_id: string;
  contract_id?: string | null;
  title: string;
  case_reference?: string | null;
  party_perspective: PartyPerspective;
  tribunal_details?: string | null;
  institutional_rules?: string | null;
  seat?: string | null;
  venue?: string | null;
  language?: string | null;
  governing_law?: string | null;
  arbitration_clause_source_id?: string | null;
  arbitration_clause?: string | null;
  case_summary?: string | null;
  status: ArbitrationCaseStatus;
  readiness_score: number;
  readiness_blockers?: ReadinessCheck[];
  created_at?: string;
  updated_at?: string;
}

export interface ArbitrationCaseCreatePayload {
  organization_id?: string | null;
  project_id: string;
  contract_id?: string | null;
  title: string;
  case_reference?: string | null;
  party_perspective?: PartyPerspective;
  tribunal_details?: string | null;
  institutional_rules?: string | null;
  seat?: string | null;
  venue?: string | null;
  language?: string | null;
  governing_law?: string | null;
  arbitration_clause_source_id?: string | null;
  arbitration_clause?: string | null;
  case_summary?: string | null;
}

export type MatrixSlug =
  | "document-index"
  | "chronology-matrix"
  | "clause-matrix"
  | "issue-matrix"
  | "claim-matrix"
  | "defence-matrix"
  | "counterclaim-matrix"
  | "rejoinder-matrix"
  | "quantum-annexures"
  | "notice-compliance"
  | "jurisdiction-matrix"
  | "expert-alignment";

export interface MatrixRow {
  _id: string;
  case_id: string;
  draft_id?: string | null;
  organization_id?: string | null;
  project_id?: string | null;
  contract_id?: string | null;
  approval_status?: string | null;
  verification_status?: string | null;
  readiness_status?: string | null;
  created_at?: string;
  updated_at?: string;
  [key: string]: unknown;
}

export type MatrixReviewAction = "assign" | "comment" | "request_changes" | "approve" | "reject";

export interface MatrixReviewPayload {
  action: MatrixReviewAction;
  reviewer_role?: "legal" | "contracts" | "technical" | "delay" | "quantum" | "commercial" | "reviewer";
  reviewer_user_id?: string | null;
  required_roles?: string[];
  comment?: string | null;
  due_at?: string | null;
}

export interface ReadinessCheck {
  _id: string;
  case_id: string;
  draft_id?: string | null;
  check_key: string;
  check_group: string;
  status: string;
  message: string;
  linked_matrix_row_id?: string | null;
}

export interface ReadinessResponse {
  case_id: string;
  draft_id?: string | null;
  readiness_score: number;
  status: "ready" | "blocked";
  blockers: ReadinessCheck[];
  checks: ReadinessCheck[];
}

export interface AgentRun {
  _id: string;
  case_id: string;
  draft_id?: string | null;
  agent_type: string;
  status: string;
  background_job_id?: string | null;
  source_ids?: string[];
  output_summary?: string | null;
  created_records?: Array<Record<string, unknown>>;
  warnings?: string[];
  errors?: string[];
  created_at?: string;
  completed_at?: string;
}

export interface CaseDashboard {
  case: ArbitrationCase;
  matrix_counts: Record<MatrixSlug, number>;
  approved_counts: Record<MatrixSlug, number>;
  drafts: Array<Record<string, unknown>>;
  readiness: ReadinessResponse;
  latest_agent_runs: AgentRun[];
}

export interface CitationAudit {
  case_id: string;
  ok: boolean;
  exhibit_count: number;
  cited_exhibits: string[];
  missing_exhibits: string[];
  unused_exhibits: string[];
  blocking_issue_count?: number;
  warning_issue_count?: number;
  issues?: Array<{
    severity?: string;
    issue_type?: string;
    draft_id?: string;
    matrix?: string;
    matrix_row_id?: string;
    message?: string;
  }>;
  drafts: Array<Record<string, unknown>>;
}

export interface FilingBundleManifest {
  case: ArbitrationCase;
  exhibit_list: MatrixRow[];
  citation_audit: CitationAudit;
  matrix_counts: Record<string, number>;
  readiness: ReadinessResponse;
}

export type BundleExportFormat = "zip" | "docx" | "pdf";

export interface BundleExport {
  _id: string;
  case_id: string;
  format: BundleExportFormat;
  status: "queued" | "running" | "completed" | "failed" | string;
  background_job_id?: string | null;
  effect_key?: string | null;
  attempts?: number;
  content_type?: string | null;
  filename?: string | null;
  content_length?: number;
  error?: string | null;
  created_at?: string;
  started_at?: string | null;
  completed_at?: string | null;
  expires_at?: string | null;
}

export const MATRIX_DEFINITIONS: Array<{ slug: MatrixSlug; label: string }> = [
  { slug: "document-index", label: "Document Index" },
  { slug: "clause-matrix", label: "Clause Matrix" },
  { slug: "issue-matrix", label: "Issue Matrix" },
  { slug: "claim-matrix", label: "Claim Matrix" },
  { slug: "defence-matrix", label: "Defence Matrix" },
  { slug: "counterclaim-matrix", label: "Counterclaim Matrix" },
  { slug: "rejoinder-matrix", label: "Rejoinder Matrix" },
  { slug: "quantum-annexures", label: "Quantum Annexures" },
  { slug: "notice-compliance", label: "Notice Compliance" },
  { slug: "jurisdiction-matrix", label: "Jurisdiction & Limitation" },
  { slug: "expert-alignment", label: "Expert Alignment" },
  { slug: "chronology-matrix", label: "Chronology Matrix" },
];

export async function listArbitrationCases(params: Record<string, unknown> = {}) {
  const { data } = await api.get<ArbitrationCase[]>("/arbitration/cases", { params });
  return data;
}

export async function createArbitrationCase(payload: ArbitrationCaseCreatePayload) {
  const { data } = await api.post<ArbitrationCase>("/arbitration/cases", payload);
  return data;
}

export async function getArbitrationCase(caseId: string) {
  const { data } = await api.get<ArbitrationCase>(`/arbitration/cases/${caseId}`);
  return data;
}

export async function updateArbitrationCase(caseId: string, payload: Partial<ArbitrationCaseCreatePayload>) {
  const { data } = await api.patch<ArbitrationCase>(`/arbitration/cases/${caseId}`, payload);
  return data;
}

export async function getArbitrationCaseDashboard(caseId: string) {
  const { data } = await api.get<CaseDashboard>(`/arbitration/cases/${caseId}/dashboard`);
  return data;
}

export async function getArbitrationReadiness(caseId: string, params: Record<string, unknown> = {}) {
  const { data } = await api.get<ReadinessResponse>(`/arbitration/cases/${caseId}/readiness`, { params });
  return data;
}

export async function approveArbitrationReadiness(caseId: string) {
  const { data } = await api.post(`/arbitration/cases/${caseId}/approve-readiness`);
  return data;
}

export async function listMatrixRows(caseId: string, matrix: MatrixSlug) {
  const { data } = await api.get<MatrixRow[]>(`/arbitration/cases/${caseId}/${matrix}`);
  return data;
}

export async function createMatrixRow(caseId: string, matrix: MatrixSlug, payload: Record<string, unknown>) {
  const { data } = await api.post<MatrixRow>(`/arbitration/cases/${caseId}/${matrix}`, payload);
  return data;
}

export async function updateMatrixRow(caseId: string, matrix: MatrixSlug, rowId: string, payload: Record<string, unknown>) {
  const { data } = await api.patch<MatrixRow>(`/arbitration/cases/${caseId}/${matrix}/${rowId}`, payload);
  return data;
}

export async function reviewMatrixRow(caseId: string, matrix: MatrixSlug, rowId: string, payload: MatrixReviewPayload) {
  const { data } = await api.post<MatrixRow>(`/arbitration/cases/${caseId}/${matrix}/${rowId}/review`, payload);
  return data;
}

export async function runArbitrationAgent(caseId: string, agentType: string, payload: Record<string, unknown> = {}) {
  const { data } = await api.post<AgentRun>(`/arbitration/cases/${caseId}/agents/${agentType}/run`, payload);
  return data;
}

export async function queueArbitrationAgent(caseId: string, agentType: string, payload: Record<string, unknown> = {}) {
  const { data } = await api.post<AgentRun>(`/arbitration/cases/${caseId}/agents/${agentType}/queue`, payload);
  return data;
}

export async function listArbitrationAgentRuns(caseId: string) {
  const { data } = await api.get<AgentRun[]>(`/arbitration/cases/${caseId}/agent-runs`);
  return data;
}

export async function getExhibitList(caseId: string) {
  const { data } = await api.get<MatrixRow[]>(`/arbitration/cases/${caseId}/exhibit-list`);
  return data;
}

export async function getCitationAudit(caseId: string) {
  const { data } = await api.get<CitationAudit>(`/arbitration/cases/${caseId}/citation-audit`);
  return data;
}

export async function getFilingBundleManifest(caseId: string) {
  const { data } = await api.get<FilingBundleManifest>(`/arbitration/cases/${caseId}/filing-bundle/manifest`);
  return data;
}

export async function downloadCaseBundleZip(caseId: string) {
  const { data } = await api.get(`/arbitration/cases/${caseId}/filing-bundle/zip`, {
    responseType: "blob",
  });
  return data as Blob;
}

export async function downloadCaseBundleDocx(caseId: string) {
  const { data } = await api.get(`/arbitration/cases/${caseId}/filing-bundle/docx`, {
    responseType: "blob",
  });
  return data as Blob;
}

export async function downloadCaseBundlePdf(caseId: string) {
  const { data } = await api.get(`/arbitration/cases/${caseId}/filing-bundle/pdf`, {
    responseType: "blob",
  });
  return data as Blob;
}

export async function queueCaseBundleExport(caseId: string, format: BundleExportFormat) {
  const { data } = await api.post<BundleExport>(`/arbitration/cases/${caseId}/filing-bundle/exports`, { format });
  return data;
}

export async function getCaseBundleExport(caseId: string, exportId: string) {
  const { data } = await api.get<BundleExport>(`/arbitration/cases/${caseId}/filing-bundle/exports/${exportId}`);
  return data;
}

export async function downloadQueuedCaseBundleExport(caseId: string, exportId: string) {
  const { data } = await api.get(`/arbitration/cases/${caseId}/filing-bundle/exports/${exportId}/download`, {
    responseType: "blob",
  });
  return data as Blob;
}
