export type DraftMode = "background" | "strategy" | "draft" | "review";
export type DraftType = "reply" | "fresh";
export type DraftRole = "contractor" | "engineer" | "employer";
export type LetterCategory =
  | "claim_reply"
  | "eot_reply"
  | "variation"
  | "payment_ipc"
  | "advance_recovery"
  | "completion"
  | "ncr_quality"
  | "delay_progress"
  | "records_request"
  | "dispute"
  | "general";

export type DraftTone =
  | "firm"
  | "neutral"
  | "advisory"
  | "conciliatory"
  | "firm_contractual";

export type RevisionAction =
  | "make_firmer"
  | "make_more_polite"
  | "add_contractual_reasoning"
  | "add_clause_reference"
  | "make_short"
  | "make_detailed"
  | "convert_to_employer_submission"
  | "convert_to_contractor_letter"
  | "regenerate"
  | "custom_instruction";

export type SectionEditAction =
  | "rewrite"
  | "grammar_spelling"
  | "clarity_structure"
  | "tone_formality"
  | "expand"
  | "polish";

export interface DraftRunCreateRequest {
  mode?: DraftMode;
  draft_type?: DraftType;
  letter_category?: LetterCategory;
  contract_package?: string;
  role?: DraftRole;
  recipient_focus?: string;
  subject?: string;
  recipient?: string;
  requirements?: string;
  points?: string;
  purpose?: string;
  desired_position?: string;
  required_action?: string;
  background_facts?: string;
  trigger_event?: string;
  tone?: DraftTone;
  timeline_days?: number;
  incoming_document_id?: string;
  incoming_letter_id?: string;
  clauses_to_consider?: string[];
  attachments?: string[];
  document_ids?: string[];
  include_letter_codes?: string[];
  exclude_letter_codes?: string[];
  plan_override?: string;
  max_iterations?: number;
  finalized?: boolean;
  user_direction_answers?: UserDirectionAnswer[];
  user_direction?: string;
}

export interface SourceEvidence {
  source_id: string;
  source_type:
    | "current_input"
    | "contract_clause"
    | "context_document"
    | "prior_correspondence"
    | "graph_thread"
    | "comment"
    | "other";
  allowed_use: "fact" | "style_continuity" | "history_only" | "clause" | "comment";
  organization_id?: string;
  project_id?: string;
  label: string;
  text?: string;
  snippet?: string;
  document_id?: string;
  letter_id?: string;
  clause_number?: string;
  clause_title?: string;
  page_numbers?: number[];
  score?: number;
  source_hash?: string;
  metadata?: Record<string, unknown>;
}

export interface ValidationFinding {
  level: "warning" | "error";
  code: string;
  message: string;
  evidence?: string;
}

export interface DraftArtifact {
  draft_letter: string;
  source_integrity_notes: string;
  learning_update?: string;
  raw_model_output?: string;
  model_name?: string;
  prompt_version?: number;
}

export interface ProbingQuestion {
  question_id: string;
  question: string;
  category?: "position" | "deadline" | "clause" | "amount" | "missing_input" | "scope";
  why?: string;
  question_version?: number;
  required?: boolean;
}

export interface UserDirectionAnswer {
  question_id?: string;
  answer: string;
  question_version?: number;
}

export interface UserDirectionRequest {
  answers?: UserDirectionAnswer[];
  directions?: string;
}

export interface LegalRiskFlag {
  flag_id: string;
  category: "admission" | "waiver" | "contradiction" | "entitlement";
  severity: "info" | "caution" | "high";
  excerpt: string;
  explanation: string;
}

export interface LegalRiskReport {
  flags: LegalRiskFlag[];
  human_review_required?: boolean;
  scanned_at?: string;
  human_reviewed_at?: string;
  human_reviewed_by?: string;
  human_review_comment?: string;
  reviewed_at?: string;
}

export type ApprovalStage = "drafter" | "reviewer" | "final";

export interface ApprovalStep {
  stage: ApprovalStage;
  approved_by?: string;
  approved_at?: string;
  comment?: string;
}

export interface ApproveStageRequest {
  stage: ApprovalStage;
  comment?: string;
}

export interface LockParagraphsRequest {
  locked_paragraphs: string[];
}

export interface FrozenDraftSection {
  section_index: number;
  content: string;
  content_hash: string;
  frozen_by?: string;
  frozen_at?: string;
}

export interface FreezeSectionsRequest {
  section_indices: number[];
  expected_draft_hash?: string;
}

export interface ReviseSectionsRequest {
  section_indices: number[];
  action: SectionEditAction;
  expected_draft_hash?: string;
}

export interface SectionRevisionRecord {
  source_run_id: string;
  action: SectionEditAction;
  editable_section_indices: number[];
  preserved_sections: FrozenDraftSection[];
  source_draft_hash: string;
  result_draft_hash: string;
  prompt_version: number;
}

export interface DraftRunResponse {
  run_id: string;
  letter_id: string;
  draft_type: DraftType;
  mode: DraftMode;
  letter_category: LetterCategory;
  contract_package?: string;
  status:
    | "queued"
    | "running"
    | "awaiting_user_direction"
    | "awaiting_strategy_confirmation"
    | "cancel_requested"
    | "cancelled"
    | "completed"
    | "blocked"
    | "needs_attention"
    | "failed"
    | "approved"
    | "exported"
    | "issued";
  role: DraftRole;
  recipient_focus?: string;
  inputs?: Record<string, unknown>;
  incoming_analysis?: Record<string, unknown>;
  planning_sheet?: Record<string, unknown>;
  reply_matrix?: Record<string, unknown>[];
  sources?: SourceEvidence[];
  context_bundle?: {
    active_workspace?: Record<string, unknown>;
    current_materials?: string[];
    selected_document_ids?: string[];
    prior_correspondence_ids?: string[];
    graph_thread_codes?: string[];
    comments?: string[];
    threshold_inputs?: Record<string, boolean>;
  };
  context_pack_id?: string;
  plan?: string;
  draft_artifact?: DraftArtifact;
  source_integrity_summary?: Record<string, unknown>;
  validation_report?: {
    blocking?: boolean;
    findings?: ValidationFinding[];
  };
  cyclic_trace?: {
    iteration: number;
    critique_blocking?: boolean;
    finding_codes?: string[];
    refinement_queries?: string[];
    retrieved_source_ids?: string[];
    regenerated?: boolean;
    notes?: string;
  }[];
  assertion_support?: {
    assertion_id: string;
    text: string;
    support_status: "supported" | "user_provided" | "unsupported" | "needs_confirmation";
    source_ids?: string[];
    risk_level?: "low" | "medium" | "high";
  }[];
  confidence_scores?: {
    clause_confidence: number;
    factual_support: number;
    tone_suitability: number;
    overall: number;
    risk_level: "low" | "medium" | "high";
  };
  iteration_count?: number;
  revision_of_run_id?: string;
  revision_action?: RevisionAction;
  probing_questions?: ProbingQuestion[];
  user_directions?: UserDirectionAnswer[];
  legal_risk_report?: LegalRiskReport;
  locked_paragraphs?: string[];
  frozen_sections?: FrozenDraftSection[];
  section_revision?: SectionRevisionRecord;
  approvals?: ApprovalStep[];
  approval_status?: string;
  assigned_reviewer_id?: string;
  returned_reason?: string;
  required_changes?: string[];
  issued_document_id?: string;
  exported_file_id?: string;
  exported_pdf_file_id?: string;
  exported_docx_file_id?: string;
  approved_by?: string;
  approved_at?: string;
  exported_by?: string;
  exported_at?: string;
  issued_by?: string;
  issued_at?: string;
  last_validated_at?: string;
  warnings?: string[];
  trace?: Record<string, unknown>[];
  started_at?: string;
  completed_at?: string;
  created_by?: string;
  engine?: "v2" | "langgraph_v3";
  engine_version?: string;
  thread_id?: string;
  execution_status?: "queued" | "running" | "awaiting_user_direction" | "awaiting_strategy_confirmation" | "completed" | "failed" | "cancel_requested" | "cancelled";
  next_action?: "none" | "poll" | "answer_questions" | "confirm_strategy" | "approve" | "cancelled";
  state_version?: number;
  last_checkpoint_id?: string;
}

export interface DraftRunAccepted {
  run_id: string;
  letter_id: string;
  engine: "langgraph_v3";
  execution_status: "queued" | "running" | "awaiting_user_direction" | "awaiting_strategy_confirmation" | "completed" | "failed" | "cancel_requested" | "cancelled";
  next_action: "none" | "poll" | "answer_questions" | "confirm_strategy" | "approve" | "cancelled";
  state_version: number;
  poll_url: string;
}

export interface DraftRunStateResponse extends Omit<DraftRunAccepted, "poll_url"> {
  last_checkpoint_id?: string;
  cancellation_requested_at?: string;
  updated_at?: string;
  probing_questions?: ProbingQuestion[];
}

export interface DraftRunResumeRequest {
  answers?: UserDirectionAnswer[];
  directions?: string;
  strategy_approved?: boolean;
  expected_state_version: number;
}

export interface DraftRunCancelRequest {
  reason?: string;
  expected_state_version: number;
}

export interface DraftLifecycleEvent {
  event_id: string;
  letter_id: string;
  run_id: string;
  event_type:
    | "created"
    | "reviewer_assigned"
    | "comment_added"
    | "analysis_confirmed"
    | "user_direction_provided"
    | "legal_risk_reviewed"
    | "paragraphs_locked"
    | "sections_frozen"
    | "sections_revised"
    | "drafter_approved"
    | "reviewer_approved"
    | "final_approved"
    | "plan_confirmed"
    | "plan_accepted"
    | "draft_accepted"
    | "validated"
    | "critiqued"
    | "revised"
    | "approved"
    | "returned_for_correction"
    | "exported"
    | "issued"
    | "failed";
  actor_user_id?: string;
  status?: string;
  detail?: string;
  payload?: Record<string, unknown>;
  created_at?: string;
}

export interface DraftAuditResponse {
  letter_id: string;
  run_id: string;
  events: DraftLifecycleEvent[];
}

export interface AssignReviewerRequest {
  reviewer_user_id: string;
  due_at?: string;
  note?: string;
}

export interface DraftCommentRequest {
  body: string;
  visibility?: "internal" | "reviewer" | "approver";
}

export interface ReturnForCorrectionRequest {
  reason: string;
  required_changes?: string[];
}

export interface DraftReviewAssignment {
  assignment_id: string;
  letter_id: string;
  run_id: string;
  reviewer_user_id: string;
  assigned_by?: string;
  due_at?: string;
  status: "assigned" | "completed" | "cancelled";
  note?: string;
  created_at?: string;
  updated_at?: string;
}

export interface DraftReviewComment {
  comment_id: string;
  letter_id: string;
  run_id: string;
  body: string;
  visibility: "internal" | "reviewer" | "approver";
  created_by?: string;
  created_at?: string;
}

export interface DraftGovernanceResponse {
  letter_id: string;
  run_id: string;
  assignments: DraftReviewAssignment[];
  comments: DraftReviewComment[];
  events: DraftLifecycleEvent[];
}

export interface DraftMetricKpi {
  key: string;
  label: string;
  value: number;
  formatted_value: string;
  target?: string;
  status: "good" | "watch" | "risk";
  detail?: string;
}

export interface DraftMetricBreakdownItem {
  label: string;
  count: number;
  percentage: number;
}

export interface DraftMetricTrendPoint {
  date: string;
  runs: number;
  approved: number;
  blocking: number;
  unsupported_rate: number;
  average_confidence: number;
}

export interface DraftMetricBottleneck {
  stage: string;
  count: number;
  average_age_hours: number;
}

export interface DraftQualityRiskItem {
  run_id: string;
  letter_id: string;
  status: string;
  risk: string;
  detail: string;
  created_at?: string;
}

export interface DraftQualityDashboardResponse {
  generated_at: string;
  window_days: number;
  total_runs: number;
  active_runs: number;
  approved_runs: number;
  exported_runs: number;
  issued_runs: number;
  average_cycle_hours: number;
  average_iterations: number;
  average_confidence: number;
  first_review_approval_rate: number;
  unsupported_claim_rate: number;
  blocking_validation_rate: number;
  source_integrity_rate: number;
  average_sources_per_run: number;
  review_return_rate: number;
  issue_artifact_compliance_rate: number;
  overdue_review_count: number;
  kpis: DraftMetricKpi[];
  status_breakdown: DraftMetricBreakdownItem[];
  quality_trends: DraftMetricTrendPoint[];
  bottlenecks: DraftMetricBottleneck[];
  recent_risks: DraftQualityRiskItem[];
}

export interface DraftContextPack {
  context_pack_id: string;
  letter_id: string;
  run_id: string;
  project?: Record<string, unknown>;
  draft_request?: Record<string, unknown>;
  facts?: string[];
  contractual_basis?: Record<string, unknown>[];
  prior_correspondence?: Record<string, unknown>[];
  required_actions?: string[];
  risk_flags?: string[];
  missing_confirmations?: string[];
  source_notes?: string[];
  source_ids?: string[];
  created_at?: string;
}

export interface SourceLedgerResponse {
  letter_id: string;
  run_id: string;
  source_count: number;
  sources: SourceEvidence[];
}

export interface ExactClauseSearchRequest {
  organization_id: string;
  project_id: string;
  clause_number: string;
  document_id?: string;
  limit?: number;
}

export interface ExactReferenceSearchRequest {
  organization_id: string;
  project_id: string;
  reference: string;
  limit?: number;
}

export interface ReviseDraftRequest {
  revision_action: RevisionAction;
  custom_instruction?: string;
  additional_requirements?: string;
  /** null/undefined = inherit the source run's locks; [] = clear all locks. */
  locked_paragraphs?: string[];
}
