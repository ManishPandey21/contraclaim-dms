import { joinApiUrl } from "@/config/api";
import { authenticatedFetch } from "@/services/http";

/**
 * Release 1.2 grounded letter summary.
 *
 * A derived view of one canonical evidence revision. The server decides
 * whether the stored summary is current; when it is stale, held for review or
 * withheld, `short_summary` and `detailed_summary` are null and only the
 * status is returned. The client never shows a summary the server withheld.
 */

export type VerificationStatus =
  | "verified"
  | "source_stated"
  | "human_approved"
  | "uncertain"
  | "not_checkable";

export type SummaryStatus =
  | "not_generated"
  | "generating"
  | "current"
  | "stale"
  | "degraded"
  | "blocked_for_review"
  | "withheld"
  | "unavailable"
  | "failed";

export interface GroundedSummaryItem {
  text: string;
  role: string;
  party?: string | null;
  fact_ids: string[];
  page_numbers: number[];
  verification_status: VerificationStatus;
}

export interface GroundedFact {
  id: string;
  type: string;
  label: string;
  value: unknown;
  page_numbers: number[];
  verification_status: VerificationStatus;
  resolved_document_id?: string | null;
}

export const DETAILED_SECTIONS = [
  ["background", "Background"],
  ["material_events", "Material Events / Sequence"],
  ["issues_positions", "Issues / Positions Stated"],
  ["contractual_basis", "Contractual Basis Cited"],
  ["requests_relief", "Requests / Instructions / Relief Sought"],
  ["claims_reservations", "Claims / Reservations Asserted"],
  ["dates_periods", "Material Dates / Periods"],
  ["amounts_quantities", "Amounts / Quantities"],
  ["references", "Referenced Correspondence"],
  ["uncertainties", "Uncertainty / Review"],
] as const;

export type SectionKey = (typeof DETAILED_SECTIONS)[number][0];

export interface DetailedSummary {
  schema_version: string;
  executive_overview?: string | null;
  facts: GroundedFact[];
  markdown: string;
  generated_from: {
    canonical_revision: number;
    canonical_sha256: string;
    extraction_run_id?: string | null;
    review_resolution?: Record<string, unknown> | null;
  };
  generator: {
    mode: "deterministic" | "llm_assisted";
    prompt_version: string;
    generated_at: string;
    degraded: boolean;
  };
  [section: string]: unknown;
}

export interface GroundedSummaryView {
  document_id: string;
  summary_status: SummaryStatus;
  reason?: string | null;
  short_summary: string | null;
  detailed_summary: DetailedSummary | null;
  generated_from?: Record<string, unknown> | null;
  current_evidence?: { canonical_revision: number; canonical_sha256: string } | null;
}

export function sectionItems(summary: DetailedSummary, key: SectionKey): GroundedSummaryItem[] {
  const value = summary[key];
  return Array.isArray(value) ? (value as GroundedSummaryItem[]) : [];
}

async function readView(response: Response): Promise<GroundedSummaryView> {
  if (!response.ok) {
    let detail = `Request failed: ${response.status}`;
    try {
      const body = await response.json();
      detail = body?.detail || body?.message || detail;
    } catch {
      // keep the status line
    }
    const error = new Error(detail) as Error & { status?: number };
    error.status = response.status;
    throw error;
  }
  return (await response.json()) as GroundedSummaryView;
}

export async function fetchGroundedSummary(documentId: string): Promise<GroundedSummaryView> {
  return readView(await authenticatedFetch(joinApiUrl(`/documents/${documentId}/grounded-summary`)));
}

export async function generateGroundedSummary(documentId: string): Promise<GroundedSummaryView> {
  return readView(
    await authenticatedFetch(joinApiUrl(`/documents/${documentId}/grounded-summary/generate`), {
      method: "POST",
    })
  );
}
