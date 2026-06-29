import { api } from "./api";

export interface SecurityTermsVersionDTO {
  _id?: string;
  id?: string;
  version: string;
  title: string;
  body: string;
  effective_date: string;
  is_active: boolean;
  terms_hash: string;
  created_at?: string;
  created_by?: string | null;
  activated_at?: string | null;
  activated_by?: string | null;
}

export interface SecurityTermsAcceptanceDTO {
  _id?: string;
  id?: string;
  user_id: string;
  org_id?: string | null;
  terms_version: string;
  accepted_at: string;
  ip_address?: string | null;
  user_agent?: string | null;
  terms_hash: string;
  acceptance_method: string;
}

export interface SecurityTermsStatusDTO {
  requires_acceptance: boolean;
  active_version: SecurityTermsVersionDTO;
  accepted_acceptance?: SecurityTermsAcceptanceDTO | null;
}

export interface CreateSecurityTermsVersionPayload {
  version: string;
  title?: string;
  body: string;
  effective_date: string;
  is_active?: boolean;
}

export async function getSecurityTermsStatus(): Promise<SecurityTermsStatusDTO> {
  const { data } = await api.get("/security-terms/status");
  return data as SecurityTermsStatusDTO;
}

export async function acceptSecurityTerms(): Promise<SecurityTermsAcceptanceDTO> {
  const { data } = await api.post("/security-terms/accept", {
    accepted: true,
    acceptance_method: "checkbox_accept_continue",
  });
  return data as SecurityTermsAcceptanceDTO;
}

export async function getMySecurityTermsAcceptances(): Promise<SecurityTermsAcceptanceDTO[]> {
  const { data } = await api.get("/security-terms/acceptances");
  return Array.isArray(data?.acceptances) ? data.acceptances : [];
}

export async function listSecurityTermsVersions(): Promise<SecurityTermsVersionDTO[]> {
  const { data } = await api.get("/security-terms/versions");
  return Array.isArray(data) ? data : [];
}

export async function createSecurityTermsVersion(
  payload: CreateSecurityTermsVersionPayload,
): Promise<SecurityTermsVersionDTO> {
  const { data } = await api.post("/security-terms/versions", payload);
  return data as SecurityTermsVersionDTO;
}

export async function activateSecurityTermsVersion(versionId: string): Promise<SecurityTermsVersionDTO> {
  const { data } = await api.post(`/security-terms/versions/${versionId}/activate`);
  return data as SecurityTermsVersionDTO;
}
