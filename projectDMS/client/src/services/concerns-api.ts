import { api } from "./api";

// Concerns register API. Mirrors routers/concerns.py (mounted at /api/concerns).

export type ConcernStatus = "open" | "in_progress" | "resolved" | "closed";
export type ConcernPriority = "low" | "medium" | "high" | "critical";

export interface ConcernDTO {
  id: string;
  name?: string | null;
  email?: string | null;
  description?: string | null;
  party_id?: string | null;
  organization_id?: string | null;
  project_id?: string | null;
  status: ConcernStatus;
  priority: ConcernPriority;
  is_active: boolean;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ConcernPayload {
  name?: string;
  email?: string;
  description?: string;
  party_id?: string;
  organization_id?: string;
  project_id?: string;
  status?: ConcernStatus;
  priority?: ConcernPriority;
}

export interface ConcernListResponse {
  concerns: ConcernDTO[];
  total: number;
  page: number;
  limit: number;
  has_next: boolean;
  has_prev: boolean;
}

const norm = (raw: any): ConcernDTO => ({ ...raw, id: raw?._id ?? raw?.id });

export async function getConcerns(params?: {
  status?: string;
  priority?: string;
  search?: string;
  partyId?: string;
  skip?: number;
  limit?: number;
}): Promise<ConcernListResponse> {
  const { data } = await api.get("/concerns/", { params });
  return {
    total: data?.total ?? 0,
    page: data?.page ?? 1,
    limit: data?.limit ?? 50,
    has_next: Boolean(data?.has_next),
    has_prev: Boolean(data?.has_prev),
    concerns: Array.isArray(data?.concerns) ? data.concerns.map(norm) : [],
  };
}

export async function createConcern(payload: ConcernPayload): Promise<ConcernDTO> {
  const { data } = await api.post("/concerns/", payload);
  return norm(data);
}

export async function updateConcern(
  id: string,
  payload: Partial<ConcernPayload> & { is_active?: boolean },
): Promise<ConcernDTO> {
  const { data } = await api.put(`/concerns/${id}`, payload);
  return norm(data);
}

export async function deleteConcern(id: string): Promise<void> {
  await api.delete(`/concerns/${id}`);
}
