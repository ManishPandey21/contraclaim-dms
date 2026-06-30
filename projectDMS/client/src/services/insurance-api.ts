import { api } from "./api";

// Insurance Register API. Mirrors routers/insurance.py.

export type InsuranceStatus = "active" | "expiring_soon" | "expired";

export interface InsuranceDTO {
  id: string;
  contract_id?: string | null;
  contractor_name?: string | null;
  insurance_type?: string | null;
  insurance_company?: string | null;
  policy_number?: string | null;
  sum_insured?: number | null;
  currency: string;
  date_of_issue?: string | null;
  date_of_expiry?: string | null;
  document_id?: string | null;
  linked_document_ids: string[];
  remarks?: string | null;
  project_id?: string | null;
  organization_id?: string | null;
  status?: InsuranceStatus | null;
  days_remaining?: number | null;
  next_alert_date?: string | null;
  created_at?: string | null;
  created_by?: string | null;
  created_by_name?: string | null;
}

export interface InsurancePayload {
  project_id: string;
  insurance_type: string;
  policy_number: string;
  contract_id?: string;
  contractor_name?: string;
  insurance_company?: string;
  sum_insured?: number;
  currency?: string;
  date_of_issue?: string;
  date_of_expiry?: string;
  document_id?: string;
  linked_document_ids?: string[];
  remarks?: string;
  organization_id?: string;
}

export interface InsuranceSummaryDTO {
  total: number;
  active: number;
  expiring_soon: number;
  expired: number;
  missing: number;
  total_sum_insured: number;
}

export interface InsuranceTypeDTO {
  id: string;
  name: string;
  description?: string | null;
  is_active: boolean;
  is_default: boolean;
  organization_id?: string | null;
}

const norm = (raw: any): InsuranceDTO => ({
  ...raw,
  id: raw?._id ?? raw?.id,
  linked_document_ids: raw?.linked_document_ids ?? [],
});

const normType = (raw: any): InsuranceTypeDTO => ({ ...raw, id: raw?._id ?? raw?.id });

export async function getInsurance(params?: {
  project_id?: string;
  organization_id?: string;
  contract_id?: string;
  type?: string;
  company?: string;
  status?: string;
  uploaded_by?: string;
  q?: string;
}): Promise<InsuranceDTO[]> {
  const { data } = await api.get("/insurance", { params });
  return Array.isArray(data) ? data.map(norm) : [];
}

export async function getInsuranceSummary(params?: {
  project_id?: string;
  organization_id?: string;
}): Promise<InsuranceSummaryDTO> {
  const { data } = await api.get("/insurance/summary", { params });
  return data as InsuranceSummaryDTO;
}

export async function getInsuranceAlerts(params?: {
  project_id?: string;
  organization_id?: string;
}): Promise<InsuranceDTO[]> {
  const { data } = await api.get("/insurance/alerts", { params });
  return Array.isArray(data) ? data.map(norm) : [];
}

export async function createInsurance(payload: InsurancePayload): Promise<InsuranceDTO> {
  const { data } = await api.post("/insurance", payload);
  return norm(data);
}

export async function updateInsurance(id: string, payload: Partial<InsurancePayload>): Promise<InsuranceDTO> {
  const { data } = await api.put(`/insurance/${id}`, payload);
  return norm(data);
}

export async function replaceInsuranceFile(id: string, documentId: string): Promise<InsuranceDTO> {
  const { data } = await api.post(`/insurance/${id}/replace-file`, { document_id: documentId });
  return norm(data);
}

export async function deleteInsurance(id: string): Promise<void> {
  await api.delete(`/insurance/${id}`);
}

export async function exportInsurance(
  format: "csv" | "xlsx" | "pdf",
  params?: { project_id?: string; organization_id?: string },
): Promise<Blob> {
  const { data } = await api.get("/insurance/export", {
    params: { format, ...params },
    responseType: "blob",
  });
  return data instanceof Blob ? data : new Blob([data]);
}

// --- Insurance Type master ---

export async function getInsuranceTypes(params?: {
  organization_id?: string;
  include_inactive?: boolean;
}): Promise<InsuranceTypeDTO[]> {
  const { data } = await api.get("/insurance/types", { params });
  return Array.isArray(data) ? data.map(normType) : [];
}

export async function createInsuranceType(payload: {
  name: string;
  description?: string;
  organization_id?: string;
}): Promise<InsuranceTypeDTO> {
  const { data } = await api.post("/insurance/types", payload);
  return normType(data);
}

export async function updateInsuranceType(
  id: string,
  payload: { name?: string; description?: string; is_active?: boolean },
): Promise<InsuranceTypeDTO> {
  const { data } = await api.put(`/insurance/types/${id}`, payload);
  return normType(data);
}

export async function deactivateInsuranceType(id: string): Promise<void> {
  await api.delete(`/insurance/types/${id}`);
}
