// Types for the Letter Bench (skill-based drafting). Mirrors
// backend/rbac_backend/models/letter_bench.py and models/skill.py.

export type DraftRole = "contractor" | "engineer" | "employer";
export type Verdict = "entitlement" | "partial" | "none" | "need_information";
export type AssertionState = "open" | "decided" | "locked";
export type ClauseCheckState = "found" | "not_found" | "not_checked";
export type StepStatus = "queued" | "running" | "completed" | "failed";
export type ChainLinkStatus =
  | "pending"
  | "queued"
  | "running"
  | "done"
  | "stale"
  | "failed"
  | "unavailable";
export type BoardStage = "dissecting" | "positions" | "locked" | "composed";
export type SkillKind = "analysis" | "evaluation" | "drafting";

export interface TextSpan {
  start: number;
  end: number;
  page?: number | null;
}

export interface ClauseCheck {
  clause_number: string;
  state: ClauseCheckState;
  title?: string | null;
  note?: string | null;
}

export interface Assertion {
  assertion_id: string;
  order: number;
  quoted_text: string;
  span?: TextSpan | null;
  clause_refs: string[];
  clause_checks: ClauseCheck[];
  proposed_verdict?: Verdict | null;
  proposed_reason?: string | null;
  verdict?: Verdict | null;
  verdict_source?: "ai" | "user" | null;
  user_direction?: string | null;
  source_ids: string[];
  routed_skill_key?: string | null;
  step_ids: string[];
  needs: string[];
  reply_paragraph_id?: string | null;
  state: AssertionState;
  origin: "ai" | "user";
}

export interface ChainLink {
  link_id: string;
  skill_key: string;
  label: string;
  kind: SkillKind;
  target_assertion_ids: string[];
  skill_version?: string | null;
  status: ChainLinkStatus;
  step_id?: string | null;
  reason?: string | null;
  added_by: "router" | "user";
}

export type ParagraphPart =
  | "header"
  | "opening"
  | "point"
  | "request"
  | "reservations"
  | "attachments"
  | "closing";

export interface ReplyParagraph {
  paragraph_id: string;
  assertion_id?: string | null;
  part: ParagraphPart;
  text: string;
  step_id?: string | null;
  stale: boolean;
  edited_by_user: boolean;
  degraded: boolean;
  clause_refs: string[];
  source_ids: string[];
  confirmation_basis?: string | null;
  confirmed_by?: string | null;
  confirmed_at?: string | null;
}

export type GroundingStatus =
  | "supported"
  | "partially_supported"
  | "contradicted"
  | "unsupported"
  | "user_confirmed"
  | "not_applicable";

/** One sentence of the composed letter and what, if anything, backs it. */
export interface GroundingStatement {
  text: string;
  classification: string;
  status: GroundingStatus;
  material: boolean;
  source_id?: string | null;
  source_label?: string | null;
  document_id?: string | null;
  passage?: string | null;
  location?: string | null;
  contradiction?: string | null;
  requires_confirmation: boolean;
  confirmation_basis?: string | null;
  confirmed_by?: string | null;
  confirmed_at?: string | null;
}

export interface BoardQuestion {
  question: {
    question_id: string;
    question: string;
    category: string;
    why?: string | null;
    required: boolean;
  };
  answer?: string | null;
}

export interface DeadlineInfo {
  raw?: string | null;
  due_date?: string | null;
  days_remaining?: number | null;
  basis?: string | null;
  source: "extracted" | "manual" | "none";
  risk?: "low" | "medium" | "high" | null;
}

export interface PositionBoard {
  letter_id: string;
  organization_id: string;
  project_id?: string | null;
  board_version: number;
  stage: BoardStage;
  role: DraftRole;
  incoming_document_id?: string | null;
  trigger?: { type: string; id: string; label: string; status?: string } | null;
  incoming_text: string;
  incoming_meta: {
    letter_no?: string | null;
    letter_date?: string | null;
    sender?: string | null;
    recipient?: string | null;
    subject?: string | null;
    summary?: string[];
    immediate_action?: string | null;
    [key: string]: unknown;
  };
  issue_type?: string | null;
  deadline: DeadlineInfo;
  assertions: Assertion[];
  chain: ChainLink[];
  questions: BoardQuestion[];
  reply_paragraphs: ReplyParagraph[];
  internal_note?: string | null;
  analysis_step_id?: string | null;
  locked_strategy_version?: number | null;
  draft_run_id?: string | null;
  grounding?: GroundingStatement[];
  composing: boolean;
  last_error?: string | null;
  /** Set when the letter tripped the injection scan: no step on this board calls the model. */
  model_blocked_reason?: string | null;
}

export interface SkillStepSummary {
  step_id: string;
  skill_key: string;
  skill_version: string;
  skill_hash: string;
  skill_name?: string | null;
  target_assertion_ids: string[];
  output_schema_key: string;
  status: StepStatus;
  stale: boolean;
  degraded: boolean;
  error?: string | null;
  created_at?: string | null;
  completed_at?: string | null;
}

export interface SkillStep extends SkillStepSummary {
  letter_id: string;
  role?: string | null;
  overlay_key?: string | null;
  output: Record<string, unknown>;
  model?: string | null;
}

export interface BenchGate {
  undecided: number;
  unanswered_questions: number;
  stale_steps: number;
  running_steps: number;
  can_lock: boolean;
  can_compose: boolean;
  blockers: string[];
}

export interface BenchResponse {
  letter_id: string;
  letter_title?: string | null;
  letter_status?: string | null;
  board: PositionBoard;
  steps: SkillStepSummary[];
  gate: BenchGate;
  rollout_mode: string;
  read_only: boolean;
}

export interface BenchAvailability {
  enabled: boolean;
  rollout_mode: string;
}

export interface BenchOpenResponse {
  letter_id: string;
  created: boolean;
  board_version: number;
}

export interface AssertionPatch {
  verdict?: Verdict;
  clear_verdict?: boolean;
  user_direction?: string;
  routed_skill_key?: string;
  clear_routed_skill?: boolean;
  split_at?: number;
  merge_with?: string;
  remove?: boolean;
}

export interface ChainLinkInput {
  link_id?: string;
  skill_key: string;
  target_assertion_ids: string[];
}

export interface SkillChainRule {
  skill_key: string;
  label?: string | null;
  issue_types: string[];
  keywords: string[];
  always: boolean;
}

export interface EffectiveSkill {
  skill_key: string;
  name: string;
  description: string;
  kind: SkillKind;
  use_when?: string | null;
  version?: string | null;
  content_hash?: string | null;
  roles_supported: DraftRole[];
  native_role?: DraftRole | null;
  contract_forms: string[];
  chains_to: SkillChainRule[];
  required_inputs: string[];
  enabled: boolean;
  pinned: boolean;
  available: boolean;
  unavailable_reason?: string | null;
}

export interface SkillEvalReport {
  suite: string;
  score: number;
  threshold: number;
  case_count: number;
  signed_off_by?: string | null;
  notes?: string | null;
}

export interface SkillVersionSummary {
  skill_key: string;
  version: string;
  name: string;
  kind: SkillKind;
  status: "draft" | "published" | "deprecated";
  content_hash: string;
  owner_scope: string;
  eval_report?: SkillEvalReport | null;
  created_at?: string | null;
  published_at?: string | null;
}

export interface SkillBinding {
  organization_id: string;
  project_id?: string | null;
  skill_key: string;
  pinned_version?: string | null;
  enabled: boolean;
}

export interface SkillAdminList {
  skills: Array<{ skill_key: string; name: string; kind: SkillKind; latest_published_version?: string | null }>;
  versions: SkillVersionSummary[];
  bindings: SkillBinding[];
}

export const VERDICT_LABELS: Record<Verdict, string> = {
  entitlement: "Entitlement",
  partial: "Partial",
  none: "None",
  need_information: "Need information",
};

export const VERDICT_DESCRIPTIONS: Record<Verdict, string> = {
  entitlement: "Valid contractual entitlement",
  partial: "Entitlement in part only",
  none: "No entitlement",
  need_information: "Cannot be decided on the information given",
};

export const VERDICT_ORDER: Verdict[] = ["entitlement", "partial", "none", "need_information"];
