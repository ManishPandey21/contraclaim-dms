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
  | "potential"
  | "draft"
  | "notified"
  | "submitted"
  | "under_review"
  | "agreed"
  | "rejected"
  | "disputed"
  | "closed";

export type NoticeState = "open" | "due_soon" | "overdue" | "served_in_time" | "served_late";

/**
 * Where a claim stands against its notice period, computed by the server.
 * Only `contract_verified` is the contract's own period, confirmed by a person
 * against the quoted clause. Every other source is a figure to be checked.
 */
export interface NoticeAssessment {
  basis: "awareness" | "event";
  basis_date: string;
  period_days: number;
  period_source: "contract_verified" | "contract_unverified" | "organisation_rule" | "assumed_default";
  /** True only for a contract period a person has verified against its clause. */
  period_verified: boolean;
  clause_no?: string | null;
  requirement_id?: string | null;
  due_date: string;
  served_on?: string | null;
  days_remaining?: number | null;
  served_late_by_days?: number | null;
  state: NoticeState;
}

export interface ClaimDTO {
  id: string;
  claim_ref?: string | null;
  type: ClaimType;
  title: string;
  description?: string | null;
  status: ClaimStatus;
  event_date?: string | null;
  awareness_date?: string | null;
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
  notice_assessment?: NoticeAssessment | null;
  allowed_status_transitions: ClaimStatus[];
  /** Outline of the claim's board; the board itself is read through the Claim Bench. */
  head_count?: number;
  bench_stage?: "heads" | "locked" | null;
  origin?: { type: "hindrance" | "letter_bench"; id: string; label?: string | null } | null;
  organization_id?: string | null;
  project_id?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  evidence_frozen_at?: string | null;
  evidence_frozen_by?: string | null;
  evidence_freeze_reason?: string | null;
  /** The contract the claim is made under; null on a claim with none identified (4B-18). */
  contract_id?: string | null;
  contract_label?: string | null;
  /** What on this record needs attention, e.g. a legacy figure that needs re-entry. */
  record_notes?: string[];
}

export interface ClaimPayload {
  title: string;
  /** The contract the claim is made under. On an update it can only fill an empty one (4B-18). */
  contract_id?: string;
  type?: ClaimType;
  claim_ref?: string;
  /** null clears the field on an update (4B-12); undefined leaves it unchanged. */
  description?: string | null;
  status?: ClaimStatus;
  amount_claimed?: number;
  currency?: string;
  event_date?: string | null;
  awareness_date?: string | null;
  notice_date?: string | null;
  response_due_date?: string | null;
  eot_days_claimed?: number;
  contract_clauses?: string[];
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
    allowed_status_transitions: raw?.allowed_status_transitions ?? [],
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

export async function getClaim(id: string): Promise<ClaimDTO> {
  const { data } = await api.get(`/claims/${id}`);
  return normalize(data);
}

export interface ClaimContractOptions {
  options: Array<{ contract_id: string; label: string }>;
  /** The contract used when none is chosen: the project's only one. */
  selected: string | null;
  /** Several contracts exist, so one must be chosen. */
  must_choose: boolean;
}

/** The contracts a claim on this project can be made under, for the signed-in person. */
export async function fetchClaimContractOptions(projectId: string): Promise<ClaimContractOptions> {
  const { data } = await api.get<ClaimContractOptions>("/claims/contract-options", {
    params: { project_id: projectId },
  });
  return data;
}

/** The three questions a claim starts from (4B-20, D-24). */
export interface ClaimStartPayload {
  project_id: string;
  organization_id?: string;
  what_happened: string;
  /** yyyy-mm-dd */
  aware_on: string;
  contract_id?: string;
}

export interface ClaimStartResult {
  claim: ClaimDTO;
  head_id: string;
  board_version: number;
}

/** Start a claim: the claim (a draft) and its board with the first head. */
export async function startClaim(payload: ClaimStartPayload): Promise<ClaimStartResult> {
  const { data } = await api.post("/claims/start", payload);
  return { claim: normalize(data.claim), head_id: data.head_id, board_version: data.board_version };
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

// ---------------------------------------------------------------------------
// Approval workflow (Phase 4 / Module 3). Mirrors the claim approval endpoints.
// ---------------------------------------------------------------------------

export type ApprovalState =
  | "draft"
  | "assigned"
  | "in_review"
  | "approved"
  | "returned";

export interface ApprovalEvent {
  action: string;
  actor_id?: string | null;
  at?: string | null;
  from_state?: string | null;
  to_state?: string | null;
  comment?: string | null;
}

export interface ApprovalRecord {
  resource_type: string;
  resource_id: string;
  state: ApprovalState;
  drafter_id?: string | null;
  reviewer_id?: string | null;
  assigned_by?: string | null;
  assigned_at?: string | null;
  due_at?: string | null;
  submitted_by?: string | null;
  submitted_at?: string | null;
  decided_by?: string | null;
  decided_at?: string | null;
  decision_comment?: string | null;
  history: ApprovalEvent[];
}

export async function getClaimApproval(id: string): Promise<ApprovalRecord> {
  const { data } = await api.get(`/claims/${id}/approval`);
  return data as ApprovalRecord;
}

export async function assignClaimReviewer(
  id: string,
  reviewer_id: string,
  opts?: { due_at?: string; note?: string },
): Promise<ApprovalRecord> {
  const { data } = await api.post(`/claims/${id}/assign`, { reviewer_id, ...opts });
  return data as ApprovalRecord;
}

export async function submitClaimForReview(id: string): Promise<ApprovalRecord> {
  const { data } = await api.post(`/claims/${id}/submit-for-review`, {});
  return data as ApprovalRecord;
}

export async function approveClaim(id: string, comment?: string): Promise<ApprovalRecord> {
  const { data } = await api.post(`/claims/${id}/approve`, { comment });
  return data as ApprovalRecord;
}

export async function returnClaim(id: string, comment?: string): Promise<ApprovalRecord> {
  const { data } = await api.post(`/claims/${id}/return`, { comment });
  return data as ApprovalRecord;
}

// ---------------------------------------------------------------------------
// Clause-grounded AI assessment (Phase 4 / Module 5).
// ---------------------------------------------------------------------------

export interface AssessmentCitation {
  document_id?: string | null;
  chunk_id?: string | null;
  clause_number?: string | null;
  clause_title?: string | null;
  section_heading?: string | null;
  page?: number | null;
  snippet?: string | null;
  document_title?: string | null;
  file_name?: string | null;
}

export interface ClaimAssessment {
  id?: string;
  claim_id: string;
  claim_type?: string | null;
  query?: string | null;
  answer: string;
  citations: AssessmentCitation[];
  trace: Record<string, unknown>[];
  created_at?: string | null;
  created_by?: string | null;
}

export async function assessClaim(id: string): Promise<ClaimAssessment> {
  const { data } = await api.post(`/claims/${id}/assess`, {});
  return data as ClaimAssessment;
}

export async function getClaimAssessments(id: string): Promise<ClaimAssessment[]> {
  const { data } = await api.get(`/claims/${id}/assessments`);
  return Array.isArray(data) ? (data as ClaimAssessment[]) : [];
}

// ---------------------------------------------------------------------------
// Evidence bundle / data-room export (Phase 4 / Module 4). Downloads a ZIP.
// ---------------------------------------------------------------------------

export async function downloadEvidenceBundle(id: string): Promise<string> {
  const res = await api.get(`/claims/${id}/evidence-bundle`, { responseType: "blob" });
  const blob =
    res.data instanceof Blob ? res.data : new Blob([res.data], { type: "application/zip" });

  const disposition = (res.headers as Record<string, string> | undefined)?.[
    "content-disposition"
  ];
  let filename = `evidence-${id}.zip`;
  if (disposition) {
    const match = disposition.match(/filename="?([^";]+)"?/i);
    if (match && match[1]) filename = match[1];
  }

  const url = window.URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  window.URL.revokeObjectURL(url);
  return filename;
}
