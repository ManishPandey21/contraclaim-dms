import { api } from "./api";

// ---------------------------------------------------------------------------
// Claim register API (Phase 4 / Module 1). Mirrors backend routers/claims.py.
// Backend serialises id as `_id`; normalised to `id`.
// ---------------------------------------------------------------------------

export type ClaimType =
  | "eot"
  | "variation"
  | "payment_ipc"
  | "loss_expense"
  | "acceleration"
  | "defect"
  | "other";

export type ClaimStatus =
  | "draft"
  | "notified"
  | "submitted"
  | "under_review"
  | "agreed"
  | "rejected"
  | "disputed"
  | "closed";

export interface ClaimDTO {
  id: string;
  claim_ref?: string | null;
  type: ClaimType;
  title: string;
  description?: string | null;
  status: ClaimStatus;
  event_date?: string | null;
  notice_date?: string | null;
  submission_date?: string | null;
  response_due_date?: string | null;
  amount_claimed?: number | null;
  amount_agreed?: number | null;
  currency?: string | null;
  eot_days_claimed?: number | null;
  eot_days_granted?: number | null;
  responsible_party_id?: string | null;
  contract_clauses: string[];
  linked_document_ids: string[];
  linked_letter_ids: string[];
  organization_id?: string | null;
  project_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ClaimPayload {
  title: string;
  type?: ClaimType;
  claim_ref?: string;
  description?: string;
  status?: ClaimStatus;
  amount_claimed?: number;
  currency?: string;
  response_due_date?: string;
  project_id?: string;
  responsible_party_id?: string;
}

function normalize(raw: any): ClaimDTO {
  return {
    ...raw,
    id: raw?._id ?? raw?.id,
    contract_clauses: raw?.contract_clauses ?? [],
    linked_document_ids: raw?.linked_document_ids ?? [],
    linked_letter_ids: raw?.linked_letter_ids ?? [],
  } as ClaimDTO;
}

export async function getClaims(params?: {
  type?: string;
  status?: string;
  project_id?: string;
}): Promise<ClaimDTO[]> {
  const { data } = await api.get("/claims", { params });
  return Array.isArray(data) ? data.map(normalize) : [];
}

export async function createClaim(payload: ClaimPayload): Promise<ClaimDTO> {
  const { data } = await api.post("/claims", payload);
  return normalize(data);
}

export async function updateClaim(id: string, payload: Partial<ClaimPayload>): Promise<ClaimDTO> {
  const { data } = await api.put(`/claims/${id}`, payload);
  return normalize(data);
}

export async function setClaimStatus(id: string, status: ClaimStatus): Promise<ClaimDTO> {
  const { data } = await api.post(`/claims/${id}/status`, { status });
  return normalize(data);
}

export async function deleteClaim(id: string): Promise<void> {
  await api.delete(`/claims/${id}`);
}
