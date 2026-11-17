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
  /** Evidence not confirmed against the Contract Master, or naming no contract. Warnings only. */
  evidence_notes?: string[];
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
  /** One key per "Escalate" action: a repeated click extends the case the first one made (D-18). */
  idempotency_key?: string;
  /** Claims whose unlocked board is weak, acknowledged by the user (D-18). */
  acknowledged_weak?: string[];
}

export interface UndoResult {
  claim_id: string;
  case_id: string;
  status: string;
  rows_archived: number;
  freeze_lifted: boolean;
  case_discarded: boolean;
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

/** Undo one claim's escalation, until the case's first pleading version (4B-37). */
export async function undoEscalation(claimId: string, caseId: string, reason: string): Promise<UndoResult> {
  const { data } = await api.post<UndoResult>("/claims/escalation/undo", { claim_id: claimId, case_id: caseId, reason });
  return data;
}
