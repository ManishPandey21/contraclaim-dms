import { api } from "./api";

// Escalation of claims into an arbitration case. Mirrors backend
// routers/claim_escalation.py. The preview changes nothing.

export interface ClaimCarry {
  claim_id: string;
  claim_ref?: string | null;
  title: string;
  status: string;
  board_locked: boolean;
  has_board: boolean;
  already_in_case_id?: string | null;
  rows: Record<string, number>;
  weak: string[];
  not_carried: string[];
  status_after: string;
}

export interface EscalationPreview {
  project_id?: string | null;
  claims: ClaimCarry[];
  totals: Record<string, number>;
  blockers: string[];
  can_escalate: boolean;
}

export interface EscalationResult {
  case_id: string;
  case_title: string;
  created_case: boolean;
  rows_created: Record<string, number>;
  rows_already_present: number;
  claims: ClaimCarry[];
}

export interface EscalationTarget {
  case_id?: string;
  new_case?: { title: string; case_reference?: string; party_perspective: "claimant" | "respondent" };
}

export async function previewEscalation(claimIds: string[], caseId?: string): Promise<EscalationPreview> {
  const { data } = await api.post<EscalationPreview>("/claims/escalation/preview", {
    claim_ids: claimIds,
    case_id: caseId,
  });
  return data;
}

export async function runEscalation(claimIds: string[], target: EscalationTarget): Promise<EscalationResult> {
  const { data } = await api.post<EscalationResult>("/claims/escalation/run", { claim_ids: claimIds, ...target });
  return data;
}
