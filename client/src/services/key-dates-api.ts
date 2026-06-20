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
  eot_status?: string | null;
  current_revision: number;
  actual_achievement_date?: string | null;
  achieved_by?: string | null;
  achieved_on_time?: boolean | null;
  delay_days?: number | null;
  early_completion_days?: number | null;
  achievement_remarks?: string | null;
  final_status?: string | null;
  status?: MilestoneStatus | null;
  days_remaining?: number | null;
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
