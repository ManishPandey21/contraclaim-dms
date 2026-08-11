import { api } from "./api";

// Bank Guarantee Register API. Mirrors routers/bank_guarantees.py.

export type BGType =
  | "performance" | "mobilisation_advance" | "plant_advance" | "retention"
  | "additional_performance" | "security_deposit" | "other";

export type BGStatus =
  | "draft" | "submitted" | "valid" | "extension_required" | "extended"
  | "expired" | "released" | "encashment_under_process" | "encashed";

export interface BGDTO {
  id: string;
  bg_type: BGType;
  bg_number?: string | null;
  issuing_bank?: string | null;
  branch?: string | null;
  bg_amount?: number | null;
  currency: string;
  conversion_rate?: number | null;
  bg_amount_base?: number | null;
  submission_date?: string | null;
  contractual_required_up_to?: string | null;
  bg_expiry_date?: string | null;
  claim_expiry_date?: string | null;
  bg_status: BGStatus;
  last_extension_date?: string | null;
  remarks?: string | null;
  contract_id?: string | null;
  project_id?: string | null;
  linked_document_ids: string[];
  current_revision: number;
  extension_required?: boolean | null;
  days_to_expiry?: number | null;
  next_alert_date?: string | null;
  created_at?: string | null;
}

export interface BGPayload {
  project_id: string;
  bg_type?: BGType;
  bg_number?: string;
  issuing_bank?: string;
  branch?: string;
  bg_amount?: number;
  currency?: string;
  conversion_rate?: number;
  submission_date?: string;
  contractual_required_up_to?: string;
  bg_expiry_date?: string;
  claim_expiry_date?: string;
  bg_status?: BGStatus;
  remarks?: string;
  contract_id?: string;
}

export interface BGSummaryDTO {
  total: number;
  total_bg_amount: number;
  valid: number;
  extension_required: number;
  expiring_45: number;
  expiring_30: number;
  expired: number;
  released: number;
}

export interface BGHistoryDTO {
  id: string;
  bg_id: string;
  revision_number: number;
  previous_expiry_date?: string | null;
  new_expiry_date?: string | null;
  previous_required_up_to?: string | null;
  new_required_up_to?: string | null;
  claim_expiry_date?: string | null;
  extension_letter_reference?: string | null;
  extension_date?: string | null;
  remarks?: string | null;
  created_at?: string | null;
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

const norm = (raw: any): BGDTO => ({
  ...raw, id: raw?._id ?? raw?.id, linked_document_ids: raw?.linked_document_ids ?? [],
});

export async function getBGs(params?: {
  project_id?: string; status?: string; type?: string; contract_id?: string;
}): Promise<BGDTO[]> {
  const { data } = await api.get("/bank-guarantees", { params });
  return Array.isArray(data) ? data.map(norm) : [];
}

export async function getBGSummary(params?: { project_id?: string }): Promise<BGSummaryDTO> {
  const { data } = await api.get("/bank-guarantees/summary", { params });
  return data as BGSummaryDTO;
}

// BGs needing attention (approaching expiry / claim deadline), scoped server-side.
export async function getBGAlerts(params?: {
  project_id?: string; organization_id?: string;
}): Promise<BGDTO[]> {
  const { data } = await api.get("/bank-guarantees/alerts", { params });
  return Array.isArray(data) ? data.map(norm) : [];
}

export async function createBG(payload: BGPayload): Promise<BGDTO> {
  const { data } = await api.post("/bank-guarantees", payload);
  return norm(data);
}

export async function updateBG(id: string, payload: Partial<BGPayload>): Promise<BGDTO> {
  const { data } = await api.put(`/bank-guarantees/${id}`, payload);
  return norm(data);
}

export async function deleteBG(id: string): Promise<void> {
  await api.delete(`/bank-guarantees/${id}`);
}

export async function extendBG(
  id: string,
  payload: {
    revised_expiry_date: string;
    revised_claim_expiry_date?: string;
    revised_required_up_to?: string;
    extension_letter_reference?: string;
    extension_date?: string;
    remarks?: string;
  },
): Promise<BGDTO> {
  const { data } = await api.post(`/bank-guarantees/${id}/extend`, payload);
  return norm(data);
}

export async function releaseBG(id: string, remarks?: string): Promise<BGDTO> {
  const { data } = await api.post(`/bank-guarantees/${id}/release`, { remarks });
  return norm(data);
}

export async function getBGHistory(id: string): Promise<BGHistoryDTO[]> {
  const { data } = await api.get(`/bank-guarantees/${id}/history`);
  return Array.isArray(data) ? (data as BGHistoryDTO[]) : [];
}

export async function exportBGs(format: "csv" | "xlsx" | "pdf", params?: { project_id?: string }): Promise<Blob> {
  const { data } = await api.get("/bank-guarantees/export", { params: { format, ...params }, responseType: "blob" });
  return data instanceof Blob ? data : new Blob([data]);
}

type CSVImportScope = { project_id: string; organization_id: string; contract_id?: string };

function csvFormData(file: File, scope: CSVImportScope): FormData {
  const form = new FormData();
  form.append("file", file);
  form.append("project_id", scope.project_id);
  form.append("organization_id", scope.organization_id);
  if (scope.contract_id) form.append("contract_id", scope.contract_id);
  return form;
}

export async function downloadBGImportTemplate(): Promise<Blob> {
  const { data } = await api.get("/bank-guarantees/import/template", { responseType: "blob" });
  return data instanceof Blob ? data : new Blob([data], { type: "text/csv" });
}

export async function previewBGsCsv(
  file: File,
  scope: CSVImportScope,
): Promise<CSVImportPreviewDTO> {
  const { data } = await api.post("/bank-guarantees/import/preview", csvFormData(file, scope), {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data as CSVImportPreviewDTO;
}

export async function importBGsCsv(
  file: File,
  scope: CSVImportScope,
): Promise<CSVImportResultDTO> {
  const { data } = await api.post("/bank-guarantees/import", csvFormData(file, scope), {
    headers: { "Content-Type": "multipart/form-data" },
  });
  return data as CSVImportResultDTO;
}
