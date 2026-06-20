import { api } from "./api";

// Variation Register API. Mirrors backend/rbac_backend/routers/variations.py.

export type VariationType = "positive" | "negative" | "neutral";
export type VariationStatus =
  | "draft" | "submitted" | "under_review" | "recommended"
  | "approved" | "rejected" | "superseded";

export interface VariationDTO {
  id: string;
  variation_number?: string | null;
  variation_type: VariationType;
  description?: string | null;
  letter_reference?: string | null;
  submitted_amount?: number | null;
  approved_amount?: number | null;
  difference_amount?: number | null;
  original_contract_value?: number | null;
  status: VariationStatus;
  approval_date?: string | null;
  remarks?: string | null;
  contract_id?: string | null;
  project_id?: string | null;
  linked_document_ids: string[];
  created_at?: string | null;
}

export interface VariationPayload {
  project_id: string;
  variation_number?: string;
  variation_type?: VariationType;
  description?: string;
  letter_reference?: string;
  submitted_amount?: number;
  approved_amount?: number;
  original_contract_value?: number;
  status?: VariationStatus;
  approval_date?: string;
  remarks?: string;
  contract_id?: string;
}

export interface VariationSummaryDTO {
  original_contract_value: number;
  total_submitted_amount: number;
  total_approved_amount: number;
  cumulative_approved_variation: number;
  revised_contract_value: number;
  percentage_variation: number;
  pending_variation_count: number;
  approved_variation_count: number;
  rejected_variation_count: number;
}

const norm = (raw: any): VariationDTO => ({
  ...raw, id: raw?._id ?? raw?.id, linked_document_ids: raw?.linked_document_ids ?? [],
});

export async function getVariations(params?: {
  project_id?: string; status?: string; type?: string; contract_id?: string;
}): Promise<VariationDTO[]> {
  const { data } = await api.get("/variations", { params });
  return Array.isArray(data) ? data.map(norm) : [];
}

export async function getVariationSummary(params?: {
  project_id?: string; contract_id?: string; original_contract_value?: number;
}): Promise<VariationSummaryDTO> {
  const { data } = await api.get("/variations/summary", { params });
  return data as VariationSummaryDTO;
}

export async function createVariation(payload: VariationPayload): Promise<VariationDTO> {
  const { data } = await api.post("/variations", payload);
  return norm(data);
}

export async function updateVariation(id: string, payload: Partial<VariationPayload>): Promise<VariationDTO> {
  const { data } = await api.put(`/variations/${id}`, payload);
  return norm(data);
}

export async function deleteVariation(id: string): Promise<void> {
  await api.delete(`/variations/${id}`);
}

export async function exportVariations(format: "csv" | "xlsx" | "pdf", params?: { project_id?: string }): Promise<Blob> {
  const { data } = await api.get("/variations/export", { params: { format, ...params }, responseType: "blob" });
  return data instanceof Blob ? data : new Blob([data]);
}
