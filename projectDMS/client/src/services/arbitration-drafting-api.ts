import { api } from "./api";

export type ArbitrationDraftType =
  | "statement_of_claim"
  | "statement_of_defence"
  | "rejoinder"
  | "counterclaim";

export type ArbitrationPartyRole = "claimant" | "respondent";

export interface ArbitrationReferenceInput {
  source_type: string;
  source_id: string;
  label: string;
  citation?: string | null;
  snippet?: string | null;
  page_numbers?: number[];
  clause_number?: string | null;
  letter_no?: string | null;
  allowed_use?: string;
  metadata?: Record<string, unknown>;
}

export interface ArbitrationSelectedReference extends ArbitrationReferenceInput {
  _id?: string;
  selected_by?: string | null;
}

export interface ArbitrationClaimHeadInput {
  head_type?: string;
  description: string;
  amount?: number | null;
  currency?: string | null;
  calculation_basis?: string | null;
  supporting_source_ids?: string[];
  status?: string;
}

export interface ArbitrationDraftCreatePayload {
  case_id?: string | null;
  organization_id?: string | null;
  project_id: string;
  contract_id?: string | null;
  draft_type: ArbitrationDraftType;
  party_role: ArbitrationPartyRole;
  dispute_type?: string;
  title: string;
  case_details?: Record<string, unknown>;
  tribunal_details?: string | null;
  arbitration_clause?: string | null;
  governing_law?: string | null;
  relief_sought?: string | null;
  manual_facts?: string | null;
  claim_amount?: number | null;
  currency?: string | null;
  interest_rate?: number | null;
  include_register_sources?: boolean;
  excluded_register_ids?: string[];
  selected_references?: ArbitrationReferenceInput[];
  claim_heads?: ArbitrationClaimHeadInput[];
}

export interface ArbitrationDraft {
  _id: string;
  case_id?: string | null;
  organization_id?: string | null;
  project_id: string;
  contract_id?: string | null;
  draft_type: ArbitrationDraftType;
  party_role: ArbitrationPartyRole;
  dispute_type: string;
  title: string;
  status: string;
  is_locked: boolean;
  current_version: number;
  include_register_sources?: boolean;
  excluded_register_ids?: string[];
  latest_version?: ArbitrationDraftVersion | null;
  selected_references?: ArbitrationSelectedReference[];
  claim_heads?: ArbitrationClaimHeadInput[];
  paragraph_responses?: Array<Record<string, unknown>>;
  created_at?: string;
  updated_at?: string;
}

export interface ArbitrationDraftVersion {
  _id: string;
  draft_id: string;
  version: number;
  full_markdown: string;
  sections?: Array<{ key: string; heading: string; body: string }>;
  structured_output?: {
    validation_warnings?: string[];
    approval_blockers?: string[];
    legal_review_required?: boolean;
    source_policy?: string;
    source_count?: number;
    input_hash?: string;
  } | null;
  source_ledger?: Array<Record<string, unknown>>;
  missing_evidence?: string[];
  annexures?: Array<Record<string, unknown>>;
  warnings?: string[];
  validation_status?: string;
  created_at?: string;
}

export async function listArbitrationDrafts(params: Record<string, unknown> = {}) {
  const { data } = await api.get<ArbitrationDraft[]>("/arbitration/drafts", { params });
  return data;
}

export async function createArbitrationDraft(payload: ArbitrationDraftCreatePayload) {
  const { data } = await api.post<ArbitrationDraft>("/arbitration/drafts", payload);
  return data;
}

export async function getArbitrationDraft(draftId: string) {
  const { data } = await api.get<ArbitrationDraft>(`/arbitration/drafts/${draftId}`);
  return data;
}

export async function updateArbitrationDraft(draftId: string, payload: Record<string, unknown>) {
  const { data } = await api.patch<ArbitrationDraft>(`/arbitration/drafts/${draftId}`, payload);
  return data;
}

export async function generateArbitrationDraft(draftId: string, payload: Record<string, unknown> = {}) {
  const { data } = await api.post<ArbitrationDraft>(`/arbitration/drafts/${draftId}/generate`, payload);
  return data;
}

export async function prepareArbitrationDraftFromCase(draftId: string) {
  const { data } = await api.post(`/arbitration/drafts/${draftId}/prepare-from-case`);
  return data;
}

export async function regenerateArbitrationSection(
  draftId: string,
  sectionKey: string,
  payload: Record<string, unknown> = {},
) {
  const { data } = await api.post<ArbitrationDraft>(
    `/arbitration/drafts/${draftId}/sections/${sectionKey}/regenerate`,
    payload,
  );
  return data;
}

export async function importDefenceParagraphs(draftId: string, text: string) {
  const { data } = await api.post(`/arbitration/drafts/${draftId}/paragraph-responses/import-defence`, {
    source_pleading_type: "statement_of_defence",
    text,
  });
  return data;
}

export async function importSocParagraphs(draftId: string, text: string) {
  const { data } = await api.post(`/arbitration/drafts/${draftId}/paragraph-responses/import-soc`, {
    source_pleading_type: "statement_of_claim",
    text,
  });
  return data;
}

export async function exportArbitrationDraft(draftId: string, format: "docx" | "pdf") {
  const { data } = await api.get(`/arbitration/drafts/${draftId}/export/${format}`, {
    responseType: "blob",
  });
  return data as Blob;
}

export async function approveArbitrationDraft(draftId: string) {
  const { data } = await api.post<ArbitrationDraft>(`/arbitration/drafts/${draftId}/approve`);
  return data;
}

export async function returnArbitrationDraftForRevision(draftId: string, reason: string) {
  const { data } = await api.post<ArbitrationDraft>(`/arbitration/drafts/${draftId}/return-for-revision`, {
    reason,
  });
  return data;
}

export async function listArbitrationDraftVersions(draftId: string) {
  const { data } = await api.get<ArbitrationDraftVersion[]>(`/arbitration/drafts/${draftId}/versions`);
  return data;
}

export async function getArbitrationDraftVersion(draftId: string, version: number) {
  const { data } = await api.get<ArbitrationDraftVersion>(`/arbitration/drafts/${draftId}/versions/${version}`);
  return data;
}

export async function saveArbitrationDraftVersion(draftId: string, fullMarkdown: string) {
  const { data } = await api.post<ArbitrationDraftVersion>(`/arbitration/drafts/${draftId}/versions`, {
    full_markdown: fullMarkdown,
  });
  return data;
}

export async function searchArbitrationEvidence(draftId: string, query: string, limit = 20) {
  const { data } = await api.post<{ results: ArbitrationReferenceInput[] }>(
    `/arbitration/drafts/${draftId}/evidence/search`,
    { query, limit },
  );
  return data.results || [];
}

export async function addArbitrationDraftReferences(draftId: string, references: ArbitrationReferenceInput[]) {
  const { data } = await api.post<ArbitrationDraft>(`/arbitration/drafts/${draftId}/references`, {
    references,
  });
  return data;
}

export async function removeArbitrationDraftReference(draftId: string, referenceId: string) {
  const { data } = await api.delete<ArbitrationDraft>(`/arbitration/drafts/${draftId}/references/${referenceId}`);
  return data;
}
