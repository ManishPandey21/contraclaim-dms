// Two Chairs and the Submission Pack. Mirrors backend models/claim_findings.py
// and the response models of routers/claim_findings.py.

import type { Perspective } from "@/types/claimBench";

export type FindingResult = "full" | "partial" | "none" | "insufficient_information";
export type DeterminationStatus = "draft" | "in_review" | "returned" | "approved" | "recorded";
export type DeterminationOrigin = "authored" | "received";

export interface InformationRequest {
  item: string;
  deadline?: string | null;
  consequence?: string | null;
}

export interface FindingProposal {
  result: FindingResult;
  findings: string;
  residual_risks: string[];
  information_required: string[];
  proposed_by: "skill" | "record_check";
}

export interface HeadFinding {
  head_id: string;
  head_title: string;
  result?: FindingResult | null;
  days?: number | null;
  amount?: number | null;
  findings: string;
  residual_risks: string[];
  information_required: InformationRequest[];
  proposal?: FindingProposal | null;
  decided_by?: string | null;
  decided_at?: string | null;
}

export interface ClaimDetermination {
  _id: string;
  claim_id: string;
  origin: DeterminationOrigin;
  revision: number;
  supersedes_id?: string | null;
  issued_by: "engineer" | "employer";
  status: DeterminationStatus;
  heads: HeadFinding[];
  source_document_id?: string | null;
  reference?: string | null;
  determination_date?: string | null;
  reviewer_id?: string | null;
  created_by?: string | null;
  created_at: string;
  submitted_by?: string | null;
  decided_by?: string | null;
  decided_at?: string | null;
  history: { action: string; actor_id?: string | null; at: string; comment?: string | null }[];
  version: number;
}

export interface HeadFigures {
  head_id: string;
  days_claimed?: number | null;
  days_assessed?: number | null;
  days_assessed_low?: number | null;
  days_assessed_high?: number | null;
  amount_claimed?: number | null;
  amount_supported?: number | null;
  amount_locked?: number | null;
}

export interface DeterminationView {
  determination: ClaimDetermination;
  figures: HeadFigures[];
  board_changed: boolean;
  editable: boolean;
  blockers: string[];
  /** The viewer is the named reviewer of a determination in review (4B-26). */
  may_decide?: boolean;
  /** In review: whether the named reviewer still holds the authority on this project, read now. */
  reviewer_eligible?: boolean | null;
  /** The viewer made or sent it and its reviewer has lost the authority: it can go to another approver. */
  may_reassign?: boolean;
}

export interface DeterminationsResponse {
  claim_id: string;
  /** The claim's project: the approver is chosen from people holding authority there. */
  project_id?: string | null;
  perspective?: Perspective | null;
  can_author: boolean;
  cannot_author_reason?: string | null;
  items: DeterminationView[];
}

export interface HeadFindingInput {
  result: FindingResult | null;
  days: number | null;
  amount: number | null;
  findings: string;
  residual_risks: string[];
  information_required: InformationRequest[];
}

export interface SimulatedHead {
  head_id: string;
  head_title: string;
  likely_result: FindingResult;
  reasoning: string;
  likely_challenges: string[];
  information_likely_requested: string[];
  produced_by: "skill" | "record_check";
}

export interface ClaimSimulation {
  _id: string;
  label: string;
  privileged: boolean;
  simulated_party: Perspective;
  prepared_for: Perspective;
  heads: SimulatedHead[];
  board_version?: number | null;
  degraded: boolean;
  degraded_reason?: string | null;
  created_at: string;
}

export interface SimulationsResponse {
  label: string;
  privileged: boolean;
  claim_id: string;
  prepared_for?: Perspective | null;
  can_simulate: Perspective[];
  current_board_version?: number | null;
  items: ClaimSimulation[];
}

export interface StrategyNote {
  _id: string;
  label: string;
  strengths: string[];
  weaknesses: string[];
  residual_risks: string[];
  notes: string;
  board_version?: number | null;
  produced_by: "skill" | "record_check" | "person";
  degraded: boolean;
  degraded_reason?: string | null;
  version: number;
  updated_at: string;
}

export interface StrategyNoteResponse {
  label: string;
  privileged: boolean;
  claim_id: string;
  note?: StrategyNote | null;
  current_board_version?: number | null;
}

export interface StrategyNoteInput {
  strengths: string[];
  weaknesses: string[];
  residual_risks: string[];
  notes: string;
}

export interface ParagraphSource {
  kind: "claim" | "head" | "clause" | "notice" | "delay" | "quantum" | "document" | "standard_wording";
  ref: string;
  label: string;
}

export interface SubmissionSection {
  key: string;
  title: string;
  paragraphs: { text: string; sources: ParagraphSource[] }[];
  internal_notes: string[];
  produced_by: "skill" | "record_composition";
  degraded: boolean;
  degraded_reason?: string | null;
  composed_at: string;
}

export interface EvidenceItem {
  number: string;
  document_id: string;
  title: string;
  reference?: string | null;
  date?: string | null;
}

export interface SubmissionView {
  claim_id: string;
  exists: boolean;
  sections: { section: SubmissionSection; stale: boolean }[];
  evidence: EvidenceItem[];
  composed_at?: string | null;
  can_compose: boolean;
  cannot_compose_reason?: string | null;
  stale_sections: number;
  approved_for_issue: boolean;
  can_export: boolean;
  cannot_export_reason?: string | null;
  /** Evidence not confirmed against the Contract Master, or naming no contract. Warnings only. */
  evidence_notes?: string[];
}
