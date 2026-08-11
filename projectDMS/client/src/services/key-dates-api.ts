import { api } from "./api";

// ---------------------------------------------------------------------------
// Key Date / Milestone Tracker API. Mirrors backend/rbac_backend/routers/
// key_dates.py. Backend serialises id as `_id`; normalised to `id`.
// ---------------------------------------------------------------------------

export type MilestoneStatus =
  | "not_started"
  | "upcoming"
  | "due_soon"
  | "due_today"
  | "overdue"
  | "achieved"
  | "eot_submitted"
  | "eot_under_review"
  | "extension_approved"
  | "extension_rejected";

export type EOTStatus =
  | "draft"
  | "submitted"
  | "under_review"
  | "approved"
  | "rejected"
  | "withdrawn";

export interface MilestoneRevision {
  revision_number: number;
  approved_revised_key_date?: string | null;
  eot_letter_reference?: string | null;
  approval_letter_reference?: string | null;
  approval_date?: string | null;
  status: string;
}

export interface MilestoneDTO {
  id: string;
  milestone_ref?: string | null;
  title: string;
  description?: string | null;
  contractual_week_number: number;
  original_planned_key_date?: string | null;
  calculated_key_date?: string | null;
  current_approved_key_date?: string | null;
  responsible_party_id?: string | null;
  remarks?: string | null;
  linked_document_ids: string[];
  linked_letter_ids: string[];
  organization_id?: string | null;
  project_id?: string | null;
  contract_id?: string | null;
  eot_status?: string | null;
  current_revision: number;
  latest_eot_submission_label?: string | null;
  latest_eot_submitted_date?: string | null;
  latest_eot_status?: string | null;
  pending_eot_count?: number;
  actual_achievement_date?: string | null;
  achieved_by?: string | null;
  achieved_on_time?: boolean | null;
  delay_days?: number | null;
  early_completion_days?: number | null;
  achievement_remarks?: string | null;
  final_status?: string | null;
  status?: MilestoneStatus | null;
  days_remaining?: number | null;
  revisions?: MilestoneRevision[];
  created_at?: string | null;
}

export interface MilestonePayload {
  title: string;
  project_id: string;
  contractual_week_number: number;
  project_start_date?: string;
  description?: string;
  milestone_ref?: string;
  responsible_party_id?: string;
  remarks?: string;
}

export interface EOTDTO {
  id: string;
  milestone_id: string;
  application_date?: string | null;
  eot_letter_reference?: string | null;
  requested_extension_days?: number | null;
  requested_revised_key_date?: string | null;
  reason?: string | null;
  status: EOTStatus;
  submitted_by?: string | null;
  submitted_date?: string | null;
  reviewed_by?: string | null;
  reviewed_date?: string | null;
  approved_extension_days?: number | null;
  approved_revised_key_date?: string | null;
  approval_letter_reference?: string | null;
  approval_date?: string | null;
  approving_authority?: string | null;
  remarks?: string | null;
}

export interface ExtensionHistoryDTO {
  id: string;
  milestone_id: string;
  revision_number: number;
  original_key_date?: string | null;
  previous_key_date?: string | null;
  requested_revised_key_date?: string | null;
  approved_revised_key_date?: string | null;
  requested_extension_days?: number | null;
  approved_extension_days?: number | null;
  eot_letter_reference?: string | null;
  approval_letter_reference?: string | null;
  approval_date?: string | null;
  status: string;
  remarks?: string | null;
  created_at?: string | null;
}

export interface KeyDateDashboardDTO {
  total: number;
  achieved: number;
  pending: number;
  overdue: number;
  due_30: number;
  due_15: number;
  due_10: number;
  due_1: number;
  eot_submitted: number;
  eot_under_review: number;
  eot_approved: number;
  eot_rejected: number;
  achieved_late: number;
  achieved_early: number;
}

export type BaselineStatus = "draft" | "frozen";
export type EOTSubmissionStatus = "draft" | "submitted" | "locked" | "withdrawn" | "superseded";
export type EOTDeterminationStatus =
  | "not_started" | "under_review" | "pending" | "granted"
  | "partially_granted" | "rejected" | "no_extension" | "superseded";
export type EOTDeterminationResult = "granted" | "partially_granted" | "rejected" | "no_change" | "pending";

export interface EOTSubmissionItemDTO {
  id?: string;
  eot_submission_id: string;
  key_date_id: string;
  milestone_ref: string;
  description?: string | null;
  original_contractual_date?: string | null;
  contractual_date_at_submission?: string | null;
  eot_submitted_date: string;
  claimed_extension_days?: number | null;
  remarks?: string | null;
}

export interface EOTSubmissionRevisionDTO {
  id: string;
  organization_id?: string | null;
  project_id: string;
  contract_id: string;
  revision_number: number;
  revision_label: string;
  eot_reference?: string | null;
  contractor_submission_date?: string | null;
  contractor_letter_reference?: string | null;
  claim_cutoff_date?: string | null;
  status: EOTSubmissionStatus;
  remarks?: string | null;
  created_at: string;
  created_by?: string | null;
  locked_at?: string | null;
  locked_by?: string | null;
  items: EOTSubmissionItemDTO[];
}

export interface EOTDeterminationItemDTO {
  id?: string;
  determination_id: string;
  key_date_id: string;
  milestone_ref: string;
  description?: string | null;
  contractual_date_before_determination?: string | null;
  submitted_date?: string | null;
  claimed_extension_days?: number | null;
  eot_granted_date?: string | null;
  granted_extension_days?: number | null;
  determination_result: EOTDeterminationResult;
  remarks?: string | null;
}

export interface EOTDeterminationDTO {
  id: string;
  organization_id?: string | null;
  project_id: string;
  contract_id: string;
  eot_submission_ids: string[];
  covered_revision_labels: string[];
  determination_reference?: string | null;
  determination_date?: string | null;
  approval_grant_reference?: string | null;
  approved_by?: string | null;
  status: EOTDeterminationStatus;
  remarks?: string | null;
  supersedes_determination_ids: string[];
  created_at: string;
  created_by?: string | null;
  frozen_at?: string | null;
  frozen_by?: string | null;
  items: EOTDeterminationItemDTO[];
}

export interface KeyDateWorkflowSummaryDTO {
  project_id: string;
  contract_id: string;
  baseline_status: BaselineStatus;
  baseline_frozen_at?: string | null;
  baseline_frozen_by?: string | null;
  current_contractual_baseline: string;
  latest_eot_submission?: string | null;
  pending_determinations: number;
  open_eot_submissions: number;
  oldest_pending_submission?: string | null;
  submissions: EOTSubmissionRevisionDTO[];
  determinations: EOTDeterminationDTO[];
}

export interface EOTSubmissionItemPayload {
  milestone_ref: string;
  eot_submitted_date: string;
  claimed_extension_days?: number;
  remarks?: string;
}

export interface EOTDeterminationItemPayload {
  milestone_ref: string;
  eot_granted_date?: string;
  granted_extension_days?: number;
  determination_result: EOTDeterminationResult;
  remarks?: string;
}

export interface CSVImportRowDTO {
  row_number: number;
  data: Record<string, any>;
  errors: string[];
  warnings: string[];
  duplicate: boolean;
}

export interface CSVImportPreviewDTO {
  module: string;
  total_rows: number;
  valid_rows: number;
  invalid_rows: number;
  can_import: boolean;
  rows: CSVImportRowDTO[];
  required_headers: string[];
  template_headers: string[];
}

export interface CSVImportResultDTO extends CSVImportPreviewDTO {
  imported_count: number;
  created_ids: string[];
}

function normalize(raw: any): MilestoneDTO {
  return {
    ...raw,
    id: raw?._id ?? raw?.id,
    linked_document_ids: raw?.linked_document_ids ?? [],
    linked_letter_ids: raw?.linked_letter_ids ?? [],
  } as MilestoneDTO;
}

const normEot = (raw: any): EOTDTO => ({ ...raw, id: raw?._id ?? raw?.id });
const normHist = (raw: any): ExtensionHistoryDTO => ({ ...raw, id: raw?._id ?? raw?.id });
const normSubmission = (raw: any): EOTSubmissionRevisionDTO => ({
  ...raw,
  id: raw?._id ?? raw?.id,
  items: (raw?.items ?? []).map((item: any) => ({ ...item, id: item?._id ?? item?.id })),
});
const normDetermination = (raw: any): EOTDeterminationDTO => ({
  ...raw,
  id: raw?._id ?? raw?.id,
  items: (raw?.items ?? []).map((item: any) => ({ ...item, id: item?._id ?? item?.id })),
});

export async function getMilestones(params?: {
  project_id?: string;
  status?: string;
  responsible_party_id?: string;
}): Promise<MilestoneDTO[]> {
  const { data } = await api.get("/key-dates", { params });
  return Array.isArray(data) ? data.map(normalize) : [];
}

export async function getMilestone(id: string): Promise<MilestoneDTO> {
  const { data } = await api.get(`/key-dates/${id}`);
  return normalize(data);
}

export async function getKeyDateDashboard(params?: {
  project_id?: string;
}): Promise<KeyDateDashboardDTO> {
  const { data } = await api.get("/key-dates/dashboard", { params });
  return data as KeyDateDashboardDTO;
}

export async function createMilestone(payload: MilestonePayload): Promise<MilestoneDTO> {
  const { data } = await api.post("/key-dates", payload);
  return normalize(data);
}

export async function updateMilestone(id: string, payload: Partial<MilestonePayload>): Promise<MilestoneDTO> {
  const { data } = await api.put(`/key-dates/${id}`, payload);
  return normalize(data);
}

export async function deleteMilestone(id: string): Promise<void> {
  await api.delete(`/key-dates/${id}`);
}

export interface RecalculateResult {
  project_id: string;
  start_date?: string | null;
  week_basis: string;
  updated: number;
  scanned: number;
}

/** Re-derive a project's milestone key dates from the current LOA + week basis.
 *  Baselines under an approved EOT revision are left untouched. */
export async function recalculateKeyDates(projectId: string): Promise<RecalculateResult> {
  const { data } = await api.post("/key-dates/recalculate", null, {
    params: { project_id: projectId },
  });
  return data as RecalculateResult;
}

export async function submitEOT(
  milestoneId: string,
  payload: {
    requested_extension_days: number;
    eot_letter_reference?: string;
    requested_revised_key_date?: string;
    reason?: string;
    application_date?: string;
    submit?: boolean;
  },
): Promise<EOTDTO> {
  const { data } = await api.post(`/key-dates/${milestoneId}/eot`, payload);
  return normEot(data);
}

export async function listEOTs(milestoneId: string): Promise<EOTDTO[]> {
  const { data } = await api.get(`/key-dates/${milestoneId}/eots`);
  return Array.isArray(data) ? data.map(normEot) : [];
}

export async function reviewEOT(
  milestoneId: string,
  eotId: string,
  payload: {
    decision: "approved" | "rejected" | "withdrawn" | "under_review";
    approved_extension_days?: number;
    approved_revised_key_date?: string;
    approval_letter_reference?: string;
    approval_date?: string;
    approving_authority?: string;
    approval_remarks?: string;
  },
): Promise<EOTDTO> {
  const { data } = await api.post(`/key-dates/${milestoneId}/eot/${eotId}/review`, payload);
  return normEot(data);
}

export async function getExtensionHistory(milestoneId: string): Promise<ExtensionHistoryDTO[]> {
  const { data } = await api.get(`/key-dates/${milestoneId}/history`);
  return Array.isArray(data) ? data.map(normHist) : [];
}

export async function exportKeyDates(
  format: "csv" | "xlsx" | "pdf",
  params?: { project_id?: string },
): Promise<Blob> {
  const { data } = await api.get("/key-dates/export", {
    params: { format, ...params },
    responseType: "blob",
  });
  return data instanceof Blob ? data : new Blob([data]);
}

export async function getKeyDateWorkflow(projectId: string, contractId = "primary"): Promise<KeyDateWorkflowSummaryDTO> {
  const { data } = await api.get("/key-dates/workflow", {
    params: { project_id: projectId, contract_id: contractId },
  });
  return {
    ...data,
    submissions: (data?.submissions ?? []).map(normSubmission),
    determinations: (data?.determinations ?? []).map(normDetermination),
  } as KeyDateWorkflowSummaryDTO;
}

export async function freezeOriginalKeyDates(projectId: string, contractId = "primary"): Promise<void> {
  await api.post("/key-dates/baseline/freeze", {
    project_id: projectId,
    contract_id: contractId,
    confirmation: true,
  });
}

export async function createEOTSubmissionRevision(payload: {
  project_id: string;
  contract_id?: string;
  eot_reference?: string;
  contractor_submission_date?: string;
  contractor_letter_reference?: string;
  claim_cutoff_date?: string;
  remarks?: string;
  status?: "draft" | "submitted";
  items: EOTSubmissionItemPayload[];
}): Promise<EOTSubmissionRevisionDTO> {
  const { data } = await api.post("/key-dates/eot-submissions", payload);
  return normSubmission(data);
}

export async function updateEOTSubmissionRevision(
  id: string,
  payload: Partial<{
    eot_reference: string;
    contractor_submission_date: string;
    contractor_letter_reference: string;
    claim_cutoff_date: string;
    remarks: string;
    status: "draft" | "submitted";
    items: EOTSubmissionItemPayload[];
  }>,
): Promise<EOTSubmissionRevisionDTO> {
  const { data } = await api.put(`/key-dates/eot-submissions/${id}`, payload);
  return normSubmission(data);
}

export async function lockEOTSubmissionRevision(id: string): Promise<EOTSubmissionRevisionDTO> {
  const { data } = await api.post(`/key-dates/eot-submissions/${id}/lock`);
  return normSubmission(data);
}

export async function createEOTDetermination(payload: {
  project_id: string;
  contract_id?: string;
  eot_submission_ids: string[];
  determination_reference?: string;
  determination_date?: string;
  approval_grant_reference?: string;
  approved_by?: string;
  status: EOTDeterminationStatus;
  remarks?: string;
  supersedes_determination_ids?: string[];
  items: EOTDeterminationItemPayload[];
}): Promise<EOTDeterminationDTO> {
  const { data } = await api.post("/key-dates/eot-determinations", payload);
  return normDetermination(data);
}

export async function updateEOTDetermination(
  id: string,
  payload: Partial<{
    determination_reference: string;
    determination_date: string;
    approval_grant_reference: string;
    approved_by: string;
    status: EOTDeterminationStatus;
    remarks: string;
    supersedes_determination_ids: string[];
    items: EOTDeterminationItemPayload[];
  }>,
): Promise<EOTDeterminationDTO> {
  const { data } = await api.put(`/key-dates/eot-determinations/${id}`, payload);
  return normDetermination(data);
}

export async function freezeEOTDetermination(id: string): Promise<EOTDeterminationDTO> {
  const { data } = await api.post(`/key-dates/eot-determinations/${id}/freeze`);
  return normDetermination(data);
}

export async function exportKeyDateWorkflow(
  kind: "baseline" | "history" | "submission" | "determination",
  format: "csv" | "xlsx" | "pdf",
  options: { projectId?: string; contractId?: string; resourceId?: string },
): Promise<Blob> {
  const path = kind === "baseline"
    ? "/key-dates/baseline/export"
    : kind === "history"
      ? "/key-dates/history/export"
      : kind === "submission"
        ? `/key-dates/eot-submissions/${options.resourceId}/export`
        : `/key-dates/eot-determinations/${options.resourceId}/export`;
  const params: Record<string, string> = { format };
  if (options.projectId) params.project_id = options.projectId;
  if (options.contractId) params.contract_id = options.contractId;
  const { data } = await api.get(path, { params, responseType: "blob" });
  return data instanceof Blob ? data : new Blob([data]);
}

async function revisionCsvRequest(
  kind: "submission" | "determination",
  id: string,
  action: "template" | "preview" | "import",
  file?: File,
): Promise<any> {
  const resource = kind === "submission" ? "eot-submissions" : "eot-determinations";
  const path = `/key-dates/${resource}/${id}/${action === "template" ? "template" : `import/${action}`}`;
  if (action === "template") {
    const { data } = await api.get(path, { responseType: "blob" });
    return data instanceof Blob ? data : new Blob([data], { type: "text/csv" });
  }
  const form = new FormData();
  if (file) form.append("file", file);
  const { data } = await api.post(path, form, {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data;
}

export const downloadEOTSubmissionTemplate = (id: string): Promise<Blob> =>
  revisionCsvRequest("submission", id, "template");
export const previewEOTSubmissionCsv = (id: string, file: File): Promise<CSVImportPreviewDTO> =>
  revisionCsvRequest("submission", id, "preview", file);
export const importEOTSubmissionCsv = (id: string, file: File): Promise<CSVImportResultDTO> =>
  revisionCsvRequest("submission", id, "import", file);
export const downloadEOTDeterminationTemplate = (id: string): Promise<Blob> =>
  revisionCsvRequest("determination", id, "template");
export const previewEOTDeterminationCsv = (id: string, file: File): Promise<CSVImportPreviewDTO> =>
  revisionCsvRequest("determination", id, "preview", file);
export const importEOTDeterminationCsv = (id: string, file: File): Promise<CSVImportResultDTO> =>
  revisionCsvRequest("determination", id, "import", file);

type CSVImportScope = { project_id: string; organization_id: string };

function csvFormData(file: File, scope: CSVImportScope): FormData {
  const form = new FormData();
  form.append("file", file);
  form.append("project_id", scope.project_id);
  form.append("organization_id", scope.organization_id);
  return form;
}

export async function downloadKeyDatesImportTemplate(): Promise<Blob> {
  const { data } = await api.get("/key-dates/import/template", { responseType: "blob" });
  return data instanceof Blob ? data : new Blob([data], { type: "text/csv" });
}

export async function previewKeyDatesCsv(
  file: File,
  scope: CSVImportScope,
): Promise<CSVImportPreviewDTO> {
  const { data } = await api.post("/key-dates/import/preview", csvFormData(file, scope), {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data as CSVImportPreviewDTO;
}

export async function importKeyDatesCsv(
  file: File,
  scope: CSVImportScope,
): Promise<CSVImportResultDTO> {
  const { data } = await api.post("/key-dates/import", csvFormData(file, scope), {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data as CSVImportResultDTO;
}

export async function recordAchievement(
  milestoneId: string,
  payload: {
    actual_achievement_date: string;
    achieved_by?: string;
    achievement_remarks?: string;
    client_notification_required?: boolean;
    client_notification_ref?: string;
    client_notification_date?: string;
    final_status?: string;
  },
): Promise<MilestoneDTO> {
  const { data } = await api.post(`/key-dates/${milestoneId}/achievement`, payload);
  return normalize(data);
}
