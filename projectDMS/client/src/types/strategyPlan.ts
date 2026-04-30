import type {
  LanggraphBackgroundItem,
  LanggraphContextDocument,
  LanggraphNodeTrace,
} from "@/types/langgraph";

export interface StrategyToneApproach {
  overall_tone?: string;
  key_messaging_strategy?: string;
  relationship_management_approach?: string;
}

export interface StrategyContentStructure {
  opening_strategy?: string;
  key_points_order?: string[];
  contractual_references?: string[];
  closing_approach?: string;
}

export interface StrategyPointResponse {
  contractor_point?: string;
  response_strategy?: string;
  evidence_references?: string[];
  contractual_basis?: string;
}

export interface StrategyRiskMitigation {
  legal_risks?: string[];
  relationship_risks?: string[];
  project_impact_considerations?: string[];
}

export interface StrategyDesiredOutcome {
  immediate_action?: string;
  next_steps?: string[];
  fallback_positions?: string[];
}

export type StrategyRole = "contractor" | "engineer" | "employer";

export interface StrategyPlanResponse {
  letter_id: string;
  run_id: string;
  status: string;
  generated_at: string;
  plan: string;
  tone_approach: StrategyToneApproach;
  content_structure: StrategyContentStructure;
  specific_responses: StrategyPointResponse[];
  risk_mitigation: StrategyRiskMitigation;
  desired_outcome: StrategyDesiredOutcome;
  summary_points: string[];
  background_summary: LanggraphBackgroundItem[];
  context_document_ids: string[];
  context_documents: LanggraphContextDocument[];
  trace: LanggraphNodeTrace[];
  warnings: string[];
}

export interface StrategyContextTimelineEntry {
  letter_id: string;
  role: StrategyRole | string;
  letter_no?: string;
  subject?: string;
  date?: string;
}

export interface StrategyContextResponse {
  letter_id: string;
  contractor_context?: string;
  engineer_context?: string;
  employer_context?: string;
  thread_letters: string[];
  timeline: StrategyContextTimelineEntry[];
}
