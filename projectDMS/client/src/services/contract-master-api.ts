import { api } from "./api";

// Contract Master API. Mirrors backend/rbac_backend/routers/contract_master.py.

export interface ContractMasterDTO {
  id: string;
  contract_id: string;
  contract_name?: string | null;
  contract_code?: string | null;
  client_name?: string | null;
  contractor_name?: string | null;
  engineer_name?: string | null;
  currency: string;
  original_contract_value?: number | null;
  current_contract_value?: number | null;
  contract_start_date?: string | null;
  original_completion_date?: string | null;
  revised_completion_date?: string | null;
  defect_liability_period_days?: number | null;
  reporting_period?: string | null;
  // Key-date calc basis: "loa_plus_weeks" (LOA + weeks*7) | "loa_plus_weeks_minus_1".
  week_basis?: string | null;
  bg_validity_rules: Record<string, { basis: string; offset_days: number }>;
  organization_id?: string | null;
  project_id?: string | null;
  effective_completion_date?: string | null;
  dlp_end_date?: string | null;
}

export interface ContractMasterPayload {
  project_id: string;
  contract_id?: string;
  contract_name?: string;
  contract_code?: string;
  client_name?: string;
  contractor_name?: string;
  engineer_name?: string;
  currency?: string;
  original_contract_value?: number;
  contract_start_date?: string;
  original_completion_date?: string;
  defect_liability_period_days?: number;
  reporting_period?: string;
  week_basis?: string;
  bg_validity_rules?: Record<string, { basis: string; offset_days: number }>;
}

export interface BGRequiredDate {
  bg_type: string;
  basis: string;
  offset_days: number;
  required_up_to?: string | null;
}

const norm = (raw: any): ContractMasterDTO => ({
  ...raw, id: raw?._id ?? raw?.id, bg_validity_rules: raw?.bg_validity_rules ?? {},
});

export async function listContractMaster(params?: { project_id?: string }): Promise<ContractMasterDTO[]> {
  const { data } = await api.get("/contracts/master", { params });
  return Array.isArray(data) ? data.map(norm) : [];
}

/** The "primary" contract master for a project, or null. */
export async function getContractMasterForProject(projectId: string): Promise<ContractMasterDTO | null> {
  const list = await listContractMaster({ project_id: projectId });
  return list.find((c) => c.contract_id === "primary") || list[0] || null;
}

export async function createContractMaster(payload: ContractMasterPayload): Promise<ContractMasterDTO> {
  const { data } = await api.post("/contracts/master", payload);
  return norm(data);
}

export async function updateContractMaster(id: string, payload: Partial<ContractMasterPayload>): Promise<ContractMasterDTO> {
  const { data } = await api.put(`/contracts/master/${id}`, payload);
  return norm(data);
}

export async function reviseCompletion(id: string, revised_completion_date: string, remarks?: string): Promise<ContractMasterDTO> {
  const { data } = await api.post(`/contracts/master/${id}/revise-completion`, { revised_completion_date, remarks });
  return norm(data);
}

export async function getBGRequiredDates(id: string): Promise<BGRequiredDate[]> {
  const { data } = await api.get(`/contracts/master/${id}/bg-required-dates`);
  return Array.isArray(data) ? (data as BGRequiredDate[]) : [];
}
