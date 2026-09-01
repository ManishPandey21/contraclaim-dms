/**
 * Client types for the Contract Master v1 HTTP surface (`/api/contract-master/*`).
 *
 * These mirror the router's response models exactly. They are deliberately not
 * a convenient reshaping of them: the moment the client invents a field the
 * server does not send — a merged "status", a cached readiness flag, a role —
 * the UI starts making decisions the server never authorised.
 *
 * Note what is absent. There is no `active` anywhere, because a single status
 * would fuse seven orthogonal facts. There is no `canEdit` derived from a role,
 * because visibility comes from the server capability response. And
 * `evidence_ready` is read from each response rather than stored, because every
 * one of its conjuncts can change without the instrument being touched.
 *
 * Distinct from `contract-master-api.ts`, which serves the *legacy* contract
 * master record (completion dates, bank-guarantee schedules) and shares nothing
 * with this domain but a name.
 */

import type { ContractUploadCapabilities } from "./contract-upload-scope";

/** The five frozen viewer states. "Active" is deliberately not among them. */
export type ContractViewerState =
  | "CATALOGUED / UNASSIGNED"
  | "APPLICABLE - PROJECTION PENDING"
  | "APPLICABLE - EVIDENCE READY"
  | "APPLICABLE - CONTENT BLOCKED"
  | "SUPERSEDED / WITHDRAWN";

export type ProjectionStatus = "PENDING" | "IN_PROGRESS" | "CURRENT" | "FAILED";

export interface CatalogueItem {
  contract_document_id: string;
  document_id: string;
  contract_document_type: string | null;
  scope_level: "organization" | "project" | null;
  classification_revision: number;
  projection_status: ProjectionStatus | null;
  applicability_count: number;
  content_consumable: boolean;
  /** Derived per response. Never cached, never persisted client-side. */
  evidence_ready: boolean;
  viewer_state: ContractViewerState;
}

export interface InstrumentDetail extends CatalogueItem {
  document_version_id: string | null;
  projection_revision: number | null;
  project_id: string | null;
}

/** Server capabilities. The only input to action visibility. */
export interface ContractMasterCapabilities extends ContractUploadCapabilities {
  can_browse_catalogue: boolean;
  can_view_instruments: boolean;
  can_manage_classification: boolean;
  can_manage_applicability: boolean;
  can_review_migration: boolean;
  can_promote: boolean;
}

export type QueryMode = "current_state" | "historical";

export interface EvidenceSearchCommand {
  project_id: string;
  contract_id: string;
  query: string;
  /** Required. There is no implicit mode, and no default-to-today. */
  query_mode: QueryMode;
  /** Required when `query_mode` is "historical". */
  event_date?: string;
  limit?: number;
  skip?: number;
}

/**
 * Four different answers, never one.
 *
 * `valid_empty` means the contract genuinely has nothing applicable.
 * `degraded` means a source was unavailable and this answer is incomplete.
 * An authority failure and a stale projection arrive as HTTP errors, not as an
 * empty list — flattening them into `200 []` is what let an outage read as
 * "the contract is silent on this".
 */
export type EvidenceOutcome = "complete" | "degraded" | "valid_empty";

export interface EvidenceResponse {
  results: Array<Record<string, unknown>>;
  outcome: EvidenceOutcome;
  degraded_sources: string[];
  total_count: number;
}

export type ScopeState =
  | "UNRESOLVED"
  | "AMBIGUOUS"
  | "PROJECT_SCOPE_CONFIRMED"
  | "ORG_SCOPE_CONFIRMED"
  | "INVALID";

export type TypeState = "TYPE_UNKNOWN" | "TYPE_SUGGESTED" | "TYPE_RESOLVED";

export interface ReconciliationCandidate {
  candidate_id: string;
  canonical_document_id: string;
  /** Scope and type progress independently and are displayed independently. */
  scope_state: ScopeState;
  type_state: TypeState;
  scope_hint: string | null;
  contract_document_type?: string | null;
  adjudicated_by?: string | null;
  promoted?: boolean;
  /** Per axis, in the server's words. Never a bare "needs review" chip. */
  promotion_blocked_reasons: string[];
}

const BASE = "/api/contract-master";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${BASE}${path}`, { credentials: "include" });
  if (!response.ok) throw new ContractMasterApiError(response.status, await safeText(response));
  return (await response.json()) as T;
}

async function postJson<T>(path: string, body: unknown): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new ContractMasterApiError(response.status, await safeText(response));
  return (await response.json()) as T;
}

async function safeText(response: Response): Promise<string> {
  try {
    return await response.text();
  } catch {
    return response.statusText;
  }
}

/**
 * Carries the status so a caller can tell the failures apart.
 *
 * 409 is an authority or state conflict — the answer is unknown, not empty.
 * 422 is a malformed request, such as a historical question with no date.
 * Collapsing either into "no results" is the specific defect this avoids.
 */
export class ContractMasterApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly detail: string,
  ) {
    super(`contract-master ${status}: ${detail}`);
  }

  get isAuthorityFailure(): boolean {
    return this.status === 409;
  }

  get isInvalidRequest(): boolean {
    return this.status === 422;
  }
}

export const contractMasterApi = {
  capabilities: (organizationId: string) =>
    getJson<ContractMasterCapabilities>(
      `/capabilities?organization_id=${encodeURIComponent(organizationId)}`,
    ),

  catalogue: (organizationId: string) =>
    getJson<{ items: CatalogueItem[] }>(
      `/catalogue?organization_id=${encodeURIComponent(organizationId)}`,
    ),

  instrument: (contractDocumentId: string) =>
    getJson<InstrumentDetail>(`/instruments/${encodeURIComponent(contractDocumentId)}`),

  searchEvidence: (command: EvidenceSearchCommand) =>
    postJson<EvidenceResponse>("/evidence/search", command),

  reconciliationCandidates: (organizationId: string) =>
    getJson<{ candidates: ReconciliationCandidate[] }>(
      `/reconciliation/candidates?organization_id=${encodeURIComponent(organizationId)}`,
    ),

  inventory: (organizationId: string) =>
    getJson<{ candidates: ReconciliationCandidate[] }>(
      `/reconciliation/inventory?organization_id=${encodeURIComponent(organizationId)}`,
    ),
};
