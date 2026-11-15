import { api } from "./api";

/**
 * Hindrance & Constraint Register — typed client for `/api/hindrances`.
 *
 * The register is backed by the `delay_events` collection; `/api/delay-events`
 * remains as a compatibility API but new UI code talks to this one only.
 * Document evidence goes through the canonical relationship API
 * (`document-relationships-api.ts`, target type `delay_event`).
 */

export type HindranceEventType = "hindrance" | "constraint" | "delay_event";
export type HindranceStatus = "open" | "under_review" | "resolved" | "closed";
/** Stored statuses include three legacy claim states the register no longer writes. */
export type StoredHindranceStatus = HindranceStatus | "claimed" | "assessed" | "rejected";
export type HindranceResponsibility = "employer" | "contractor" | "concurrent" | "neutral" | "under_review";
export type HindranceClaimStatus =
  | "not_assessed"
  | "notified"
  | "claimed"
  | "assessed"
  | "rejected"
  | "withdrawn"
  | "not_applicable";
export type HindranceCategory =
  | "site_access"
  | "land_handover"
  | "design_information"
  | "drawing_approval"
  | "utility_diversion"
  | "traffic_diversion"
  | "tree_cutting"
  | "statutory_approval"
  | "local_restrictions"
  | "adverse_weather"
  | "force_majeure"
  | "variation_instruction"
  | "payment"
  | "employer_supplied_items"
  | "suspension"
  | "interface"
  | "other";
export type TimelineSyncStatus = "pending" | "synced" | "failed";
export type HindranceLinkTargetType = "programme_milestone" | "key_date" | "eot_submission";
export type HindranceSortField =
  | "start_date"
  | "end_date"
  | "hindrance_ref"
  | "title"
  | "status"
  | "event_type"
  | "category"
  | "responsibility"
  | "created_at"
  | "updated_at";

export interface HindranceDTO {
  id: string;
  organization_id?: string | null;
  project_id: string;
  event_type: HindranceEventType;
  hindrance_ref?: string | null;
  delay_ref?: string | null;
  title: string;
  description?: string | null;
  category?: HindranceCategory | null;
  start_date: string;
  end_date?: string | null;
  duration_days?: number | null;
  responsibility: HindranceResponsibility;
  responsible_party?: string | null;
  affected_party?: string | null;
  cause?: string | null;
  location?: string | null;
  programme_activity?: string | null;
  impact_description?: string | null;
  critical_path_impact: boolean;
  float_consumed_days?: number | null;
  claimed_days?: number | null;
  assessed_days?: number | null;
  entitlement?: string | null;
  status: StoredHindranceStatus;
  claim_status?: HindranceClaimStatus | null;
  linked_document_ids: string[];
  archived_at?: string | null;
  archived_by?: string | null;
  archive_reason?: string | null;
  timeline_sync_status?: TimelineSyncStatus | null;
  timeline_sync_error?: string | null;
  timeline_event_id?: string | null;
  timeline_synced_at?: string | null;
  created_at: string;
  created_by?: string | null;
  updated_at?: string | null;
  updated_by?: string | null;
}

/** Fields the register accepts on create. Reference and scope are server-owned. */
export interface HindranceCreatePayload {
  organization_id?: string;
  project_id: string;
  event_type: HindranceEventType;
  title: string;
  start_date: string;
  description?: string | null;
  category?: HindranceCategory | null;
  end_date?: string | null;
  responsibility?: HindranceResponsibility;
  responsible_party?: string | null;
  affected_party?: string | null;
  cause?: string | null;
  location?: string | null;
  programme_activity?: string | null;
  impact_description?: string | null;
  critical_path_impact?: boolean;
  float_consumed_days?: number | null;
  claimed_days?: number | null;
  assessed_days?: number | null;
  entitlement?: string | null;
  status?: HindranceStatus;
  claim_status?: HindranceClaimStatus | null;
}

/**
 * PATCH body. An omitted key is left untouched; a key sent as `null` is
 * cleared. Build it with `hindrancePatch` so only changed fields are sent.
 */
export type HindranceUpdatePayload = Partial<Omit<HindranceCreatePayload, "organization_id" | "project_id">>;

export interface HindranceListParams {
  organization_id?: string;
  project_id?: string;
  event_type?: HindranceEventType;
  status?: StoredHindranceStatus;
  claim_status?: HindranceClaimStatus;
  category?: HindranceCategory;
  responsibility?: HindranceResponsibility;
  critical_path_impact?: boolean;
  timeline_sync_status?: TimelineSyncStatus;
  start_from?: string;
  start_to?: string;
  q?: string;
  include_archived?: boolean;
  sort?: HindranceSortField;
  order?: "asc" | "desc";
  skip?: number;
  limit?: number;
}

export interface HindranceListResponse {
  items: HindranceDTO[];
  total: number;
  skip: number;
  limit: number;
}

export interface HindranceHistoryEntry {
  id?: string | null;
  action: string;
  actor_id?: string | null;
  resource_type?: string | null;
  resource_id?: string | null;
  result?: string | null;
  reason?: string | null;
  changed_fields: string[];
  created_at?: string | null;
}

export interface HindranceLinkTarget {
  label: string;
  title?: string | null;
  status?: string | null;
  date?: string | null;
  kind?: string | null;
  route?: string | null;
}

export interface HindranceLinkDTO {
  id: string;
  delay_event_id: string;
  organization_id: string;
  project_id: string;
  target_type: HindranceLinkTargetType;
  target_id: string;
  relationship_role: string;
  description?: string | null;
  created_at?: string | null;
  created_by?: string | null;
  removed_at?: string | null;
  revision: number;
  target?: HindranceLinkTarget | null;
  target_available: boolean;
  target_restricted: boolean;
}

export interface HindranceAffectingItem {
  link: HindranceLinkDTO;
  hindrance: HindranceDTO;
}

export interface ProgrammeMilestoneDTO {
  id: string;
  milestone_ref: string;
  title: string;
  milestone_type: string;
  status: string;
  planned_date?: string | null;
  project_id: string;
}

function withoutEmpty(params: HindranceListParams): Record<string, string | number | boolean> {
  const out: Record<string, string | number | boolean> = {};
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    out[key] = value as string | number | boolean;
  }
  return out;
}

export async function listHindrances(params: HindranceListParams = {}): Promise<HindranceListResponse> {
  const { data } = await api.get("/hindrances", { params: withoutEmpty(params) });
  return {
    items: Array.isArray(data?.items) ? data.items : [],
    total: Number(data?.total ?? 0),
    skip: Number(data?.skip ?? 0),
    limit: Number(data?.limit ?? 0),
  };
}

export async function getHindrance(id: string): Promise<HindranceDTO> {
  const { data } = await api.get(`/hindrances/${encodeURIComponent(id)}`);
  return data as HindranceDTO;
}

export async function createHindrance(payload: HindranceCreatePayload): Promise<HindranceDTO> {
  const { data } = await api.post("/hindrances", payload);
  return data as HindranceDTO;
}

export async function updateHindrance(id: string, payload: HindranceUpdatePayload): Promise<HindranceDTO> {
  const { data } = await api.patch(`/hindrances/${encodeURIComponent(id)}`, payload);
  return data as HindranceDTO;
}

export async function archiveHindrance(id: string, reason: string): Promise<HindranceDTO> {
  const { data } = await api.post(`/hindrances/${encodeURIComponent(id)}/archive`, { reason });
  return data as HindranceDTO;
}

export async function restoreHindrance(id: string, reason: string): Promise<HindranceDTO> {
  const { data } = await api.post(`/hindrances/${encodeURIComponent(id)}/restore`, { reason });
  return data as HindranceDTO;
}

export async function resyncHindranceTimeline(id: string): Promise<HindranceDTO> {
  const { data } = await api.post(`/hindrances/${encodeURIComponent(id)}/timeline-sync`);
  return data as HindranceDTO;
}

export async function getHindranceHistory(id: string): Promise<HindranceHistoryEntry[]> {
  const { data } = await api.get(`/hindrances/${encodeURIComponent(id)}/history`);
  return Array.isArray(data?.entries) ? data.entries : [];
}

export async function listHindranceLinks(id: string): Promise<HindranceLinkDTO[]> {
  const { data } = await api.get(`/hindrances/${encodeURIComponent(id)}/links`);
  return Array.isArray(data?.links) ? data.links : [];
}

export async function linkHindrance(
  id: string,
  payload: { target_type: HindranceLinkTargetType; target_id: string; description?: string },
): Promise<HindranceLinkDTO> {
  const { data } = await api.post(`/hindrances/${encodeURIComponent(id)}/links`, payload);
  return data as HindranceLinkDTO;
}

export async function unlinkHindrance(id: string, linkId: string, reason: string): Promise<HindranceLinkDTO> {
  const { data } = await api.post(
    `/hindrances/${encodeURIComponent(id)}/links/${encodeURIComponent(linkId)}/remove`,
    { reason },
  );
  return data as HindranceLinkDTO;
}

export async function listHindrancesAffecting(
  targetType: HindranceLinkTargetType,
  targetId: string,
): Promise<HindranceAffectingItem[]> {
  const { data } = await api.get(
    `/hindrances/affecting/${encodeURIComponent(targetType)}/${encodeURIComponent(targetId)}`,
  );
  return Array.isArray(data?.items) ? data.items : [];
}

/** Programme activities are the `programme_milestones` register (evidence-graph API). */
export async function listProgrammeMilestones(projectId: string): Promise<ProgrammeMilestoneDTO[]> {
  const { data } = await api.get("/programme-milestones", { params: { project_id: projectId, limit: 500 } });
  return (Array.isArray(data) ? data : []).map((row: any) => ({
    id: String(row._id ?? row.id ?? ""),
    milestone_ref: String(row.milestone_ref ?? ""),
    title: String(row.title ?? ""),
    milestone_type: String(row.milestone_type ?? ""),
    status: String(row.status ?? ""),
    planned_date: row.planned_date ?? null,
    project_id: String(row.project_id ?? ""),
  }));
}

/**
 * The PATCH body for an edit: only the keys whose value changed, with an
 * emptied optional field sent as `null` so the server clears it.
 */
export function hindrancePatch(
  before: HindranceDTO,
  after: HindranceCreatePayload,
): HindranceUpdatePayload {
  const patch: Record<string, unknown> = {};
  const keys = Object.keys(after) as Array<keyof HindranceCreatePayload>;
  for (const key of keys) {
    if (key === "organization_id" || key === "project_id") continue;
    const next = after[key] ?? null;
    const previous = (before as unknown as Record<string, unknown>)[key] ?? null;
    if (next !== previous) patch[key] = next;
  }
  return patch as HindranceUpdatePayload;
}
