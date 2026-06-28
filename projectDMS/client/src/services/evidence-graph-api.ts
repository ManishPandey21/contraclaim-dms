import { api } from "./api";

export type EvidenceEntityType =
  | "letter"
  | "document"
  | "clause"
  | "drawing"
  | "payment_event"
  | "programme_milestone"
  | "key_date"
  | "delay_event"
  | "claim"
  | "variation"
  | "bank_guarantee"
  | "project_event"
  | "unresolved_reference";

export type EventLinkStatus = "ai_suggested" | "user_verified" | "rejected" | "approved";

export interface EventLinkDTO {
  id: string;
  link_group_id: string;
  revision: number;
  organization_id?: string | null;
  project_id?: string | null;
  source_type: EvidenceEntityType;
  source_id: string;
  target_type: EvidenceEntityType;
  target_id: string;
  relation_type: string;
  status: EventLinkStatus;
  confidence?: number | null;
  evidence_text?: string | null;
  metadata?: Record<string, unknown>;
  created_at?: string | null;
  created_by?: string | null;
}

export interface ProjectEventDTO {
  id: string;
  organization_id?: string | null;
  project_id?: string | null;
  event_type: string;
  event_date: string;
  event_end_date?: string | null;
  title: string;
  description?: string | null;
  party?: string | null;
  package?: string | null;
  impact_area?: string | null;
  source_entity_type?: EvidenceEntityType | null;
  source_entity_id?: string | null;
  status: string;
  confidence?: number | null;
  ai_extraction_id?: string | null;
  metadata?: Record<string, unknown>;
  links: EventLinkDTO[];
}

export interface TimelineSummaryDTO {
  total_events: number;
  graph_links: number;
  ai_suggested: number;
  user_verified: number;
  approved: number;
  rejected: number;
  awaiting_review: number;
}

export interface TimelineResponseDTO {
  events: ProjectEventDTO[];
  summary: TimelineSummaryDTO;
  skip: number;
  limit: number;
  has_more: boolean;
}

function normalizeLink(raw: any): EventLinkDTO {
  return {
    ...raw,
    id: raw?._id ?? raw?.id,
  } as EventLinkDTO;
}

function normalizeEvent(raw: any): ProjectEventDTO {
  return {
    ...raw,
    id: raw?._id ?? raw?.id,
    links: Array.isArray(raw?.links) ? raw.links.map(normalizeLink) : [],
  } as ProjectEventDTO;
}

export async function getContractTimeline(params?: {
  organization_id?: string;
  project_id?: string;
  event_type?: string;
  link_status?: string;
  date_from?: string;
  date_to?: string;
  package?: string;
  party?: string;
  claim_type?: string;
  clause?: string;
  drawing?: string;
  key_date?: string;
  delay_responsibility?: string;
  payment_status?: string;
  location?: string;
  skip?: number;
  limit?: number;
}): Promise<TimelineResponseDTO> {
  const { data } = await api.get("/contracts/timeline", { params });
  return {
    summary: data?.summary ?? {
      total_events: 0,
      graph_links: 0,
      ai_suggested: 0,
      user_verified: 0,
      approved: 0,
      rejected: 0,
      awaiting_review: 0,
    },
    events: Array.isArray(data?.events) ? data.events.map(normalizeEvent) : [],
    skip: data?.skip ?? 0,
    limit: data?.limit ?? 100,
    has_more: Boolean(data?.has_more),
  };
}

export async function verifyEventLink(linkGroupId: string, note?: string): Promise<EventLinkDTO> {
  const { data } = await api.post(`/event-links/${linkGroupId}/verify`, { note });
  return normalizeLink(data);
}

export async function rejectEventLink(linkGroupId: string, note?: string): Promise<EventLinkDTO> {
  const { data } = await api.post(`/event-links/${linkGroupId}/reject`, { note });
  return normalizeLink(data);
}

export async function approveEventLink(linkGroupId: string, note?: string): Promise<EventLinkDTO> {
  const { data } = await api.post(`/event-links/${linkGroupId}/approve`, { note });
  return normalizeLink(data);
}

export async function getEventLinkHistory(linkGroupId: string): Promise<EventLinkDTO[]> {
  const { data } = await api.get(`/event-links/${linkGroupId}/history`);
  return Array.isArray(data) ? data.map(normalizeLink) : [];
}
