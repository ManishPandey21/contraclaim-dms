import type { ClaimDTO, ClaimType, NoticeAssessment } from "@/services/claims-api";

export const ELEMENT_KEYS = ["event", "notice", "contractual_basis", "causation", "quantum"] as const;
export type ElementKey = (typeof ELEMENT_KEYS)[number];
export type ElementState = "supported" | "weak" | "missing" | "not_applicable";
export type HeadKind = "time" | "cost" | "time_and_cost";
export type HeadClassification = "excusable" | "compensable" | "contractor_risk" | "undetermined";
export type Perspective = "contractor" | "engineer" | "employer";

export interface HeadElement {
  proposed_state?: ElementState | null;
  proposed_reason?: string | null;
  /** Who made the proposal: the published skill, or the record checks alone. */
  proposed_by?: "skill" | "record_check" | null;
  source_ids: string[];
  /** The call a person made. Null until someone decides. */
  state?: ElementState | null;
  note?: string | null;
  decided_by?: string | null;
  decided_at?: string | null;
}

export interface ClaimHead {
  head_id: string;
  title: string;
  kind: HeadKind;
  event_summary?: string | null;
  clause_refs: string[];
  amount_claimed?: number | null;
  eot_days_claimed?: number | null;
  elements: Record<ElementKey, HeadElement>;
  proposed_classification?: HeadClassification | null;
  classification?: HeadClassification | null;
  residual_risks: string[];
  missing_information: string[];
  step_id?: string | null;
  test_stale: boolean;
  tested_at?: string | null;
}

export interface ClaimBenchBoard {
  board_version: number;
  stage: "heads" | "locked";
  heads: ClaimHead[];
  perspective?: Perspective | null;
  locked_at?: string | null;
  locked_by?: string | null;
}

export interface BenchGate {
  undecided_elements: number;
  unclassified_heads: number;
  stale_tests: number;
  blockers: string[];
  can_lock: boolean;
}

export interface BenchStepSummary {
  step_id: string;
  subject_id: string;
  skill_key: string;
  skill_version: string;
  skill_name?: string | null;
  role?: string | null;
  degraded: boolean;
  stale: boolean;
  error?: string | null;
  model?: string | null;
  completed_at?: string | null;
}

export interface NoticeRequirement {
  _id?: string;
  id?: string;
  organization_id: string;
  project_id: string;
  claim_type: string;
  period_days: number;
  trigger: "awareness" | "event";
  clause_no: string;
  clause_title?: string | null;
  quote: string;
  document_type?: string | null;
  origin: "clause_index" | "entered";
  verification: "extracted_unverified" | "verified";
  status: "current" | "superseded";
  created_by?: string | null;
  created_at?: string | null;
  verified_by?: string | null;
  verified_at?: string | null;
}

export interface NoticePeriodCandidate {
  period_days: number;
  trigger: "awareness" | "event";
  clause_no: string;
  clause_title?: string | null;
  quote: string;
  document_id?: string | null;
  document_type?: string | null;
  clause_uid?: string | null;
  modifies_general_conditions: boolean;
  cited_by_claim: boolean;
}

export interface NoticeCandidates {
  project_id: string;
  claim_type: string;
  clause_index_available: boolean;
  candidates: NoticePeriodCandidate[];
  current?: NoticeRequirement | null;
}

export interface NoticeRequirementInput {
  project_id: string;
  claim_type: string;
  period_days: number;
  trigger: "awareness" | "event";
  clause_no: string;
  clause_title?: string | null;
  quote: string;
  document_id?: string | null;
  document_type?: string | null;
  clause_uid?: string | null;
  origin: "clause_index" | "entered";
}

export interface ClaimBenchResponse {
  claim_id: string;
  claim_ref?: string | null;
  title: string;
  claim_type: ClaimType;
  status: ClaimDTO["status"];
  organization_id?: string | null;
  project_id?: string | null;
  exists: boolean;
  bench: ClaimBenchBoard;
  gate: BenchGate;
  steps: BenchStepSummary[];
  notice_assessment?: NoticeAssessment | null;
  notice_requirement?: NoticeRequirement | null;
  /** The party this project acts for. Null means nobody has said. */
  perspective?: Perspective | null;
  skill: { skill_key: string; available: boolean; version?: string | null; reason?: string | null };
  read_only: boolean;
  read_only_reason?: string | null;
  arbitration_case_id?: string | null;
  rollout_mode: string;
}

export interface ClaimSignal {
  source_type: "hindrance" | "letter_bench";
  source_id: string;
  label: string;
  title: string;
  reason: string;
  claim_type: ClaimType;
  event_date?: string | null;
  notice_assessment?: NoticeAssessment | null;
}

export interface HeadInput {
  title: string;
  kind: HeadKind;
  event_summary?: string | null;
  clause_refs: string[];
  amount_claimed?: number | null;
  eot_days_claimed?: number | null;
}

export interface HeadPatch {
  title?: string;
  kind?: HeadKind;
  event_summary?: string;
  clause_refs?: string[];
  amount_claimed?: number;
  clear_amount?: boolean;
  eot_days_claimed?: number;
  clear_eot_days?: boolean;
  classification?: HeadClassification;
  clear_classification?: boolean;
}

// --------------------------------------------------------------------------- //
// Delay Lab and Quantum Builder (Phase 2)
// --------------------------------------------------------------------------- //
export type RiskOwner = "employer" | "contractor" | "neutral";
export type Criticality = "critical" | "not_critical" | "unproven";
export type DelayMethod = "as_planned_vs_as_built" | "windows";
export type OverlapTreatment =
  | "eot_granted_no_cost"
  | "eot_granted_with_cost"
  | "apportioned"
  | "no_eot"
  | "not_true_concurrency";
export type LinkTargetType = "hindrance" | "eot_submission" | "eot_determination";

export interface EventDecision {
  proposed_risk_owner?: RiskOwner | null;
  proposed_criticality?: Criticality | null;
  proposed_reason?: string | null;
  proposed_by?: "skill" | "register" | null;
  risk_owner?: RiskOwner | null;
  criticality?: Criticality | null;
  note?: string | null;
  decided_by?: string | null;
}

export interface DelayEventView {
  event_id: string;
  link_id: string;
  label: string;
  title: string;
  start_date: string;
  end_date?: string | null;
  open_ended: boolean;
  register_responsibility?: string | null;
  register_critical?: boolean | null;
  /** False when the hindrance has changed since it was linked. */
  revision_current: boolean;
  counted: boolean;
  days?: number | null;
  decision: EventDecision;
}

export interface ConcurrencyFinding {
  treatment: OverlapTreatment;
  days_to_eot?: number | null;
  basis: string;
  decided_by?: string | null;
  decided_at?: string | null;
}

export interface OverlapView {
  overlap_id: string;
  start_date: string;
  end_date: string;
  days: number;
  employer_event_ids: string[];
  contractor_event_ids: string[];
  finding?: ConcurrencyFinding | null;
  days_to_eot?: number | null;
}

export interface LinkedRecordView {
  link_id: string;
  target_type: LinkTargetType;
  target_id: string;
  label: string;
  status?: string | null;
  date?: string | null;
  milestone_ref?: string | null;
  granted_days?: number | null;
  counted: boolean;
  not_counted_reason?: string | null;
  revision_current: boolean;
}

export interface DelayView {
  head_id: string;
  method?: DelayMethod | null;
  as_of_date?: string | null;
  unavailable_methods: Record<string, string>;
  include_neutral: boolean;
  neutral_basis?: string | null;
  events: DelayEventView[];
  overlaps: OverlapView[];
  records: LinkedRecordView[];
  employer_critical_days: number;
  contractor_critical_days: number;
  neutral_critical_days: number;
  contractor_only_days: number;
  net: {
    low: number;
    high: number;
    /** Set only when every overlap has a decided treatment. */
    days?: number | null;
    settled: boolean;
    previously_granted: number;
    employer_only_days: number;
    overlap_days_to_eot: number;
    neutral_days_included: number;
  };
  compensable_periods: { start_date: string; end_date: string; days: number }[];
  blockers: string[];
  stale_links: number;
}

export interface LinkCandidate {
  target_type: LinkTargetType;
  target_id: string;
  label: string;
  title?: string | null;
  date?: string | null;
  status?: string | null;
  milestones: string[];
}

export type QuantumCategory =
  | "direct"
  | "prolongation"
  | "disruption"
  | "overhead_formula"
  | "loss_of_profit"
  | "interest";
export type QuantumFormula = "none" | "hudson" | "emden" | "eichleay";
export type RecordRole = "support" | "actual_loss" | "work_declined";

export interface QuantumLine {
  line_id: string;
  category: QuantumCategory;
  description: string;
  amount: number;
  period_start?: string | null;
  period_end?: string | null;
  formula: QuantumFormula;
  basis?: string | null;
  records: { document_id: string; role: RecordRole }[];
}

export type QuantumLineInput = Omit<QuantumLine, "line_id">;

export interface QuantumView {
  head_id: string;
  currency: string;
  lines: { line: QuantumLine; locked: boolean; lock_reasons: string[] }[];
  claimed_total: number;
  supported_total: number;
  locked_total: number;
  locked_count: number;
}
