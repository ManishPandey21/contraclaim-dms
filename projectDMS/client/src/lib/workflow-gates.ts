/**
 * The eight human gates of a governed pleading workflow, in order, read from the
 * workflow state the server returns (plan 4B-40, B4).
 *
 * The server owns every decision; this only names, in plain words, which gate the
 * run is at and who acts there. A gate is done when the server holds its approval
 * receipt (or, for the question gate, when the run has moved past it).
 */
import type { ArbitrationWorkflowState } from "@/services/arbitration-drafting-api";

export type GateKey =
  | "document_selection"
  | "material_question"
  | "matrix_review"
  | "readiness"
  | "plan"
  | "legal_review"
  | "draft"
  | "export";

export interface GateDefinition {
  gate: GateKey;
  label: string;
  /** The workflow node at which this gate waits for a person. */
  node: string;
  /** Who may act there by default (the server's DEFAULT_GATE_ROLES). */
  roles: string[];
}

export const WORKFLOW_GATES: GateDefinition[] = [
  { gate: "document_selection", label: "Choose the documents", node: "document_selection_gate", roles: ["legal_reviewer", "contract_reviewer"] },
  { gate: "material_question", label: "Answer the open questions", node: "material_question_gate", roles: [] },
  { gate: "matrix_review", label: "Review the matrices", node: "matrix_review_gate", roles: ["legal_reviewer", "contract_reviewer", "delay_reviewer", "quantum_reviewer"] },
  { gate: "readiness", label: "Approve the case for drafting", node: "readiness_approval_gate", roles: ["senior_legal_approver"] },
  { gate: "plan", label: "Approve the pleading plan", node: "plan_approval_gate", roles: ["senior_legal_approver"] },
  { gate: "legal_review", label: "Legal review of the draft", node: "legal_review_gate", roles: ["legal_reviewer", "senior_legal_approver"] },
  { gate: "draft", label: "Approve the draft", node: "draft_approval_gate", roles: ["senior_legal_approver"] },
  { gate: "export", label: "Authorise export for filing", node: "export_authorization_gate", roles: ["export_authorizer"] },
];

export const REVIEWER_ROLE_LABELS: Record<string, string> = {
  legal_reviewer: "a legal reviewer",
  contract_reviewer: "a contracts reviewer",
  delay_reviewer: "a delay reviewer",
  quantum_reviewer: "a quantum reviewer",
  senior_legal_approver: "a senior legal approver",
  export_authorizer: "an export authoriser",
};

export type GateState = "done" | "current" | "working" | "upcoming" | "stopped";

export interface GateStep extends GateDefinition {
  state: GateState;
  /** For the current gate: who acts, in plain words. */
  who?: string;
}

function rolePhrase(roles: string[]): string {
  const named = roles.map((role) => REVIEWER_ROLE_LABELS[role] ?? "the case team");
  const unique = Array.from(new Set(named));
  if (!unique.length) return "the case team";
  return unique.length === 1 ? unique[0] : `${unique.slice(0, -1).join(", ")} or ${unique[unique.length - 1]}`;
}

/** Every gate with its state; exactly one is current, working or stopped unless the run is complete. */
export function gateSteps(workflow: Pick<ArbitrationWorkflowState, "status" | "current_node" | "required_human_role" | "approval_receipt_ids">): GateStep[] {
  const receipts = workflow.approval_receipt_ids ?? {};
  const complete = workflow.status === "completed" || workflow.current_node === "complete";
  const stopped = workflow.status === "cancelled" || workflow.status === "failed";
  const atIndex = WORKFLOW_GATES.findIndex((gate) => gate.node === workflow.current_node);
  // Between gates the system is working; the first gate without an approval is next.
  const nextIndex = atIndex >= 0 ? atIndex : WORKFLOW_GATES.findIndex((gate) => gate.gate !== "material_question" && !receipts[gate.gate]);
  return WORKFLOW_GATES.map((gate, index) => {
    if (complete || receipts[gate.gate] || (nextIndex >= 0 && index < nextIndex)) return { ...gate, state: "done" };
    if (index !== nextIndex) return { ...gate, state: "upcoming" };
    if (stopped) return { ...gate, state: "stopped" };
    if (atIndex < 0) return { ...gate, state: "working", who: "the system, preparing this step" };
    const roles = workflow.required_human_role ? [workflow.required_human_role] : gate.roles;
    return { ...gate, state: "current", who: rolePhrase(roles) };
  });
}
