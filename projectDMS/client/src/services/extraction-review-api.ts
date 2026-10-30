import { api } from "./api";

/**
 * Release 1.1.1 extraction review. The server decides everything (selection,
 * permission, stale evidence); the client only sends what the reviewer saw.
 */

export interface ExtractionReviewQueueItem {
  document_id: string;
  filename?: string | null;
  subject?: string | null;
  letter_no?: string | null;
  organization_id?: string | null;
  project_id?: string | null;
  processing_status: string;
  review_pages: number[];
  first_review_page: number | null;
  review_reason?: string | null;
  uploaded_at?: string | null;
  updated_at?: string | null;
}

export interface ExtractionReviewQueue {
  items: ExtractionReviewQueueItem[];
  total: number;
  skip: number;
  limit: number;
}

export interface ExtractionReviewDecision {
  id: string;
  page_number: number;
  action: "approved_as_is" | "rejected";
  decision_version: number;
  canonical_revision: number;
  page_checksum: string;
  comment?: string | null;
  reviewed_by?: string | null;
  reviewed_by_label?: string | null;
  reviewed_at?: string | null;
  machine_verdict?: string | null;
  machine_needs_review?: boolean | null;
}

export interface ExtractionReviewPage {
  page_number: number;
  in_evidence: boolean;
  source?: string | null;
  status?: string | null;
  page_class?: string | null;
  machine_quality_verdict?: string | null;
  machine_needs_review?: boolean | null;
  machine_failed_checks: { name?: string | null; verdict?: string | null; detail?: string | null }[];
  unresolved: boolean;
  text_withheld: boolean;
  error?: string | null;
  table_count: number;
  tables: string[][][];
  page_checksum?: string | null;
  char_count: number;
  text: string;
  approvable: boolean;
  decision: ExtractionReviewDecision | null;
}

export type ExtractionReviewState =
  | "required"
  | "publication_pending"
  | "resolved"
  | "not_required";

export interface ExtractionReview {
  document_id: string;
  filename?: string | null;
  processing_status?: string | null;
  evidence_status: string;
  can_review: boolean;
  review_state: ExtractionReviewState;
  reviewable: boolean;
  extraction_run_id?: string | null;
  canonical_revision?: number | null;
  canonical_sha256?: string | null;
  blocking_pages: number[];
  unresolved_pages?: number[];
  pages: ExtractionReviewPage[];
}

export interface PageDecisionResult {
  decision: ExtractionReviewDecision;
  idempotent: boolean;
  resolution: { resolved: boolean; state: string; unresolved_pages?: number[] };
}

export async function listExtractionReviewQueue(params?: { skip?: number; limit?: number }) {
  const { data } = await api.get("/document-extraction-reviews", { params });
  return data as ExtractionReviewQueue;
}

export async function getExtractionReview(documentId: string) {
  const { data } = await api.get(`/documents/${documentId}/extraction-review`);
  return data as ExtractionReview;
}

export async function decideExtractionPage(
  documentId: string,
  page: ExtractionReviewPage,
  action: "approve" | "reject",
  review: Pick<ExtractionReview, "canonical_revision">,
  comment: string
) {
  const { data } = await api.post(
    `/documents/${documentId}/extraction-review/pages/${page.page_number}/${action}`,
    {
      canonical_revision: review.canonical_revision,
      page_checksum: page.page_checksum,
      comment: comment.trim() || null,
      expected_decision_version: page.decision?.decision_version ?? 0,
    }
  );
  return data as PageDecisionResult;
}
