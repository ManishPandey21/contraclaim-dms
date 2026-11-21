import { api } from "./api";

/** Reviewer role assignments (plan 4B-07, 4B-08): who holds which reviewer role, where. */
export type ReviewerScopeType = "platform" | "organization" | "project";

export interface ReviewerAssignment {
  id: string;
  user_id: string;
  user_name: string;
  role_key: string;
  role_label: string;
  scope_type: ReviewerScopeType;
  organization_id?: string | null;
  project_id?: string | null;
  granted_by: string;
  reason: string;
  granted_at: string;
  expires_at?: string | null;
  revoked_at?: string | null;
  revoke_reason?: string | null;
  state: "active" | "expired" | "revoked";
}

export interface MyReviewerRoles {
  platform: string[];
  assignments: ReviewerAssignment[];
}

export interface LegacyReviewerRoleReport {
  organization_id?: string | null;
  holders: Array<{ user_id: string; user_name: string; role_key: string; has_assignment: boolean }>;
  counts_by_role: Record<string, number>;
}

export interface ReviewerAssignmentInput {
  user_id: string;
  role_key: string;
  scope_type: ReviewerScopeType;
  organization_id?: string;
  project_id?: string;
  reason: string;
  expires_at?: string;
}

const BASE = "/reviewer-assignments";

export async function fetchMyReviewerRoles(): Promise<MyReviewerRoles> {
  const { data } = await api.get<MyReviewerRoles>(`${BASE}/mine`);
  return data;
}

export async function fetchReviewerAssignments(params: {
  scope_type: ReviewerScopeType;
  organization_id?: string;
  project_id?: string;
  include_inactive?: boolean;
}): Promise<ReviewerAssignment[]> {
  const { data } = await api.get<{ items: ReviewerAssignment[] }>(BASE, { params });
  return data.items;
}

export async function grantReviewerAssignment(input: ReviewerAssignmentInput): Promise<ReviewerAssignment> {
  const { data } = await api.post<ReviewerAssignment>(BASE, input);
  return data;
}

export async function revokeReviewerAssignment(id: string, reason: string): Promise<ReviewerAssignment> {
  const { data } = await api.post<ReviewerAssignment>(`${BASE}/${encodeURIComponent(id)}/revoke`, { reason });
  return data;
}

export async function fetchLegacyReviewerRoles(organizationId?: string): Promise<LegacyReviewerRoleReport> {
  const { data } = await api.get<LegacyReviewerRoleReport>(`${BASE}/legacy-report`, {
    params: { organization_id: organizationId },
  });
  return data;
}
