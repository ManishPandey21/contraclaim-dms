import { api } from "./api";

export type ChronologyStatus =
  | "draft"
  | "extracting"
  | "review"
  | "verified"
  | "exported"
  | "archived"
  | "failed";

export type ChronologyVerificationStatus =
  | "ai_suggested"
  | "verified"
  | "edited_verified"
  | "rejected"
  | "duplicate"
  | "needs_review";

export interface MatterChronologyDTO {
  id: string;
  organization_id?: string | null;
  project_id: string;
  contract_id?: string | null;
  matter_id?: string | null;
  claim_id?: string | null;
  title: string;
  chronology_type: string;
  party_perspective: string;
  status: ChronologyStatus;
  selected_source_ids: string[];
  selected_source_types: string[];
  summary_counts?: Record<string, number>;
  updated_at?: string | null;
  created_at?: string | null;
}

export interface ChronologyEventDTO {
  id: string;
  chronology_id: string;
  organization_id?: string | null;
  project_id?: string | null;
  event_date?: string | null;
  date_text?: string | null;
  date_type: string;
  title: string;
  description?: string | null;
  source_document_id?: string | null;
  source_document_name?: string | null;
  source_page?: number | null;
  letter_no?: string | null;
  from_party?: string | null;
  to_party?: string | null;
  contract_clauses: string[];
  issue_tags: string[];
  claim_heads: string[];
  responsible_party?: string | null;
  supports_party: string;
  event_classification: string;
  impact_type: string;
  confidence_score?: number | null;
  verification_status: ChronologyVerificationStatus;
  manual_notes?: string | null;
  legal_relevance?: string | null;
  pleading_use: string;
  project_event_id?: string | null;
  event_link_ids: string[];
}

export interface ChronologyCreatePayload {
  organization_id?: string;
  project_id: string;
  contract_id?: string;
  matter_id?: string;
  claim_id?: string;
  title: string;
  chronology_type: string;
  party_perspective: string;
  selected_source_ids?: string[];
  selected_source_types?: string[];
}

export interface ChronologyPleadingContextDTO {
  chronology_id: string;
  source_ledger: Array<Record<string, unknown>>;
  missing_evidence: string[];
  summary: Record<string, number>;
}

function normalizeChronology(raw: any): MatterChronologyDTO {
  return {
    ...raw,
    id: raw?._id ?? raw?.id,
    selected_source_ids: raw?.selected_source_ids ?? [],
    selected_source_types: raw?.selected_source_types ?? [],
    summary_counts: raw?.summary_counts ?? {},
  } as MatterChronologyDTO;
}

function normalizeEvent(raw: any): ChronologyEventDTO {
  return {
    ...raw,
    id: raw?._id ?? raw?.id,
    contract_clauses: raw?.contract_clauses ?? [],
    issue_tags: raw?.issue_tags ?? [],
    claim_heads: raw?.claim_heads ?? [],
    event_link_ids: raw?.event_link_ids ?? [],
  } as ChronologyEventDTO;
}

export async function listChronologies(params?: Record<string, string | number | undefined>): Promise<MatterChronologyDTO[]> {
  const { data } = await api.get("/chronologies", { params });
  return Array.isArray(data) ? data.map(normalizeChronology) : [];
}

export async function createChronology(payload: ChronologyCreatePayload): Promise<MatterChronologyDTO> {
  const { data } = await api.post("/chronologies", payload);
  return normalizeChronology(data);
}

export async function getChronology(chronologyId: string): Promise<MatterChronologyDTO> {
  const { data } = await api.get(`/chronologies/${chronologyId}`);
  return normalizeChronology(data);
}

export async function extractChronologyEvents(chronologyId: string, sourceDocumentIds: string[] = []) {
  const { data } = await api.post(`/chronologies/${chronologyId}/extract`, {
    source_document_ids: sourceDocumentIds,
  });
  return data;
}

export async function listChronologyEvents(
  chronologyId: string,
  params?: Record<string, string | number | undefined>,
): Promise<ChronologyEventDTO[]> {
  const { data } = await api.get(`/chronologies/${chronologyId}/events`, { params });
  return Array.isArray(data) ? data.map(normalizeEvent) : [];
}

export async function verifyChronologyEvent(chronologyId: string, eventId: string, note?: string): Promise<ChronologyEventDTO> {
  const { data } = await api.post(`/chronologies/${chronologyId}/events/${eventId}/verify`, { note });
  return normalizeEvent(data);
}

export async function rejectChronologyEvent(chronologyId: string, eventId: string, note?: string): Promise<ChronologyEventDTO> {
  const { data } = await api.post(`/chronologies/${chronologyId}/events/${eventId}/reject`, { note });
  return normalizeEvent(data);
}

export async function getChronologyPleadingContext(
  chronologyId: string,
  includeUnverified = false,
): Promise<ChronologyPleadingContextDTO> {
  const { data } = await api.get(`/chronologies/${chronologyId}/pleading-context`, {
    params: { include_unverified: includeUnverified },
  });
  return {
    chronology_id: data?.chronology_id ?? chronologyId,
    source_ledger: data?.source_ledger ?? [],
    missing_evidence: data?.missing_evidence ?? [],
    summary: data?.summary ?? {},
  };
}

export async function attachChronologyToDraft(draftId: string, chronologyId: string, includeUnverified = false) {
  const { data } = await api.post(`/arbitration/drafts/${draftId}/attach-chronology`, {
    chronology_id: chronologyId,
    include_unverified: includeUnverified,
  });
  return data;
}

export function chronologyExportUrl(chronologyId: string, format: "docx" | "xlsx" | "pdf" | "evidence-index"): string {
  return `/api/chronologies/${chronologyId}/export/${format}`;
}

