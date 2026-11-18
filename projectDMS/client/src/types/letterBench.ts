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
  trigger?: BoardTrigger | null;
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
  /** User id of whoever ran the evaluation. Required when the version needs approvals. */
  recorded_by?: string | null;
}

export type SkillApprovalKind = "technical" | "legal";

export interface SkillApprovalRequirement {
  kind: SkillApprovalKind;
  reviewer_role: string;
}

/** A reviewer's decision, bound to the text and the evaluation result they saw. */
/** A server-side evaluation run (4B-41). Made by the server, never typed in; kept unchanged. */
export interface SkillEvalRun {
  _id: string;
  skill_key: string;
  version: string;
  content_hash: string;
  policy_hash: string;
  suite_key: string;
  suite_hash: string;
  model: string;
  prompt_key: string;
  prompt_version?: number | null;
  recorded_by: string;
  threshold: number;
  case_count: number;
  passed: boolean;
  failures: string[];
  scores: {
    element_agreement: number;
    element_agreement_by_type: Record<string, number>;
    classification_agreement?: number | null;
    degraded_rate: number;
    schema_validity: number;
  };
  matters: {
    matter_id: string;
    degraded: boolean;
    schema_valid: boolean;
    hard_failures: string[];
    elements: Record<string, [string | null, string | null]>;
    classification?: [string | null, string | null] | null;
  }[];
  cost: { model_calls: number; prompt_characters: number; output_characters: number };
  note?: string | null;
  started_at: string;
  completed_at?: string | null;
}

export interface SkillApproval {
  _id?: string;
  id?: string;
  skill_key: string;
  version: string;
  content_hash: string;
  /** Hash of the manifest the approval was given under; absent on approvals recorded before 4B-04. */
  policy_hash?: string | null;
  eval_suite: string;
  eval_report_hash: string;
  eval_score?: number | null;
  eval_threshold?: number | null;
  eval_case_count?: number | null;
  eval_recorded_by?: string | null;
  /** The server-side run the approval was given against (4B-41). */
  eval_run_id?: string | null;
  kind: SkillApprovalKind;
  reviewer_role: string;
  reviewer_user_id: string;
  comment?: string | null;
  decided_at: string;
  revoked_at?: string | null;
  revoked_by?: string | null;
  revoke_reason?: string | null;
}

export interface SkillApprovalList {
  skill_key: string;
  version: string;
  content_hash: string;
  /** The version's manifest hash; an approval must carry the same one. */
  policy_hash?: string | null;
  /** Reviews required, the server's floor included. */
  required: SkillApprovalRequirement[];
  approvals: SkillApproval[];
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
  required_approvals?: SkillApprovalRequirement[];
}

export interface SkillReviewQueueItem {
  skill_key: string;
  version: string;
  name: string;
  kind: SkillKind;
  content_hash: string;
  created_at?: string | null;
  reviews: Array<{
    kind: SkillApprovalKind;
    reviewer_role: string;
    mine: boolean;
    given: boolean;
    given_by_me: boolean;
  }>;
  waiting_on_me: boolean;
}

export interface SkillReviewQueue {
  my_roles: string[];
  items: SkillReviewQueueItem[];
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

/** Where a board came from. Determination letters (4B-31) also carry findings and information items. */
export interface BoardTrigger {
  type: string;
  id: string;
  label: string;
  status?: string;
  heads?: {
    head_id: string;
    head_title: string;
    result?: string | null;
    days?: number | null;
    amount?: number | null;
    findings?: string;
  }[];
  information_items?: {
    head_id: string;
    head_title: string;
    item: string;
    deadline?: string | null;
    consequence?: string | null;
  }[];
}
