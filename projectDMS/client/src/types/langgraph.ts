export interface LanggraphNodeTrace {
  name: string;
  status: string;
  started_at: string;
  completed_at: string;
  data: Record<string, unknown>;
}

export interface LanggraphDraft {
  subject: string;
  body: string;
  key_points: string[];
  generated_at: string;
}

export interface LanggraphContextDocument {
  id?: string;
  documentId?: string;
  letterNo?: string;
  subject?: string;
  uploadType?: string;
  date?: string;
  summary?: string;
  keywords?: string[];
  [key: string]: unknown;
}

export interface LanggraphBackgroundItem {
  id: string;
  text: string;
  type?: string;
  documents?: string[];
  generated_at?: string;
  [key: string]: unknown;
}

export interface LanggraphGraphThreadNode {
  normCode?: string;
  code?: string;
  direction?: string;
  subject?: string;
  date?: string;
  project?: string;
  createdAt?: string;
  [key: string]: unknown;
}

export interface LanggraphDraftSource {
  id: string;
  source_type:
    | "contract_clause"
    | "letter"
    | "context_document"
    | "comment"
    | "other";
  label: string;
  snippet?: string;
  document_id?: string;
  letter_id?: string;
  clause_number?: string;
  clause_title?: string;
  page_numbers?: number[];
  score?: number;
  metadata?: Record<string, unknown>;
}

export interface LanggraphDraftReviewFinding {
  level: "warning" | "error";
  message: string;
  evidence?: string;
}

export interface LanggraphToneApproach {
  overall_tone?: string;
  key_messaging_strategy?: string;
  relationship_management_approach?: string;
}

export interface LanggraphContentStructure {
  opening_strategy?: string;
  key_points_order?: string[];
  contractual_references?: string[];
  closing_approach?: string;
}

export interface LanggraphPointResponse {
  contractor_point?: string;
  response_strategy?: string;
  evidence_references?: string[];
  contractual_basis?: string;
}

export interface LanggraphRiskMitigation {
  legal_risks?: string[];
  relationship_risks?: string[];
  project_impact_considerations?: string[];
}

export interface LanggraphDesiredOutcome {
  immediate_action?: string;
  next_steps?: string[];
  fallback_positions?: string[];
}

export interface LanggraphDraftResponse {
  letter_id: string;
  run_id: string;
  status: string;
  plan: string;
  draft: LanggraphDraft;
  warnings: string[];
  trace: LanggraphNodeTrace[];
  summary_points: string[];
  started_at: string;
  completed_at: string;
  context_document_ids: string[];
  context_documents: LanggraphContextDocument[];
  background_summary: LanggraphBackgroundItem[];
  graph_thread: LanggraphGraphThreadNode[];
  sources: LanggraphDraftSource[];
  reviewer_findings: LanggraphDraftReviewFinding[];
  reviewer_blocking?: boolean;
  tone_approach?: LanggraphToneApproach;
  content_structure?: LanggraphContentStructure;
  specific_responses?: LanggraphPointResponse[];
  risk_mitigation?: LanggraphRiskMitigation;
  desired_outcome?: LanggraphDesiredOutcome;
  requirements_text?: string | null;
  context_text?: string | null;
}
