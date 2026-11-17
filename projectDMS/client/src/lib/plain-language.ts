/**
 * One vocabulary for what the user reads (plan 4B-11): statuses, engine terms
 * and error codes in plain English, and API refusals rendered from their
 * `user_message`. Nothing here may show a database id, an ObjectId, an
 * internal key or stack text.
 */

export const CLAIM_STATUS_LABELS = {
  potential: "Potential claim",
  draft: "Draft",
  notified: "Notified",
  submitted: "Submitted",
  under_review: "Under review",
  agreed: "Agreed",
  rejected: "Rejected",
  disputed: "Disputed",
  closed: "Closed",
} as const;

export const ARBITRATION_CASE_STATUS_LABELS = {
  intake: "Prepare the case file",
  matrix_preparation: "Review the matrices",
  ready_for_drafting: "Approved for drafting",
  drafting: "Drafting",
  under_review: "Under review",
  approved: "Approved",
  filed: "Filed",
  archived: "Archived",
} as const;

export const ARBITRATION_DRAFT_STATUS_LABELS = {
  draft: "Draft",
  generating: "Being prepared",
  under_review: "In review",
  approved: "Approved",
  exported: "Exported for filing",
  superseded: "Replaced by a later version",
  failed: "Could not be prepared",
} as const;

export const ENGINE_TERM_LABELS = {
  source_ledger: "Evidence list",
  claim_matrix: "Heads of claim",
  readiness: "Approve for drafting",
  matrix_review: "Review the matrices",
  pleaded_head: "Head of claim",
  degraded: "Prepared without the assistant",
} as const;

/** Error codes the server sends in `detail.code`, in the words the user reads. */
export const ERROR_CODE_MESSAGES = {
  picker_permission_not_supported: "This list cannot be filtered that way.",
  project_not_found: "That project could not be found.",
  explicit_grant_required: "This needs a permission granted to you by name. Ask your administrator.",
  evidence_restricted: "Some evidence is restricted for you, so this cannot be exported.",
  no_active_subscription: "Your organisation's subscription does not cover this.",
  missing_permission: "You do not have permission to do this.",
  claim_origin_server_owned: "A claim's origin is recorded by the system; it cannot be set here.",
  claim_create_status: "A new claim starts as a draft or as notified.",
  claim_changed_concurrently: "Someone changed this claim at the same time. Reload it and try again.",
  claim_evidence_frozen: "This claim has been escalated to arbitration, so its record can no longer be edited.",
  claim_amount_from_board: "This claim's amount and days come from its heads on the Claim Bench. Change them there.",
  head_amount_from_lines: "This head's amount is the total of its quantum lines. Change the lines instead.",
  bench_not_locked: "The board is not locked.",
  bench_unlock_reason: "Say why the board is being reopened.",
  signal_not_eligible: "This record no longer indicates a new claim.",
  signal_organisation_required: "Select the organisation this record belongs to, then raise the claim.",
  notice_quote_period: "The quoted contract wording does not state that number of days.",
  notice_quote_not_in_clause: "The quoted wording is not in that clause of this project's contract.",
  notice_self_verification: "You recorded this notice period, so someone else must verify it.",
  escalation_key_required: "This request could not be matched to your escalation. Reload the page and try again.",
  escalation_claim_taken: "Someone changed or escalated this claim at the same time. Nothing was escalated; reload and try again.",
  escalation_key_released: "The earlier attempt did not finish. Try again.",
  escalation_key_reused: "This escalation was started for different claims. Reload the page and try again.",
  escalation_in_progress: "This escalation is still running. Try again in a moment.",
  escalation_weak_unacknowledged: "Acknowledge what is weak before escalating a claim whose board is not locked.",
  escalation_undo_reason: "Say why the escalation is being undone.",
  escalation_not_found: "This claim was not escalated into this case, so there is nothing to undo.",
  escalation_undo_after_pleading: "A pleading version exists in this case, so the escalation can no longer be undone.",
  escalation_claim_moved: "The claim has changed since it was escalated, so the escalation is not undone.",
  draft_action_refused: "This pleading cannot be approved or exported yet. The reasons are listed on the draft.",
  draft_case_scope_mismatch: "That arbitration case belongs to another project. Choose a case of this draft's project.",
  reviewer_platform_grant_forbidden: "Only a Super Admin can grant or withdraw a platform reviewer role.",
  reviewer_grant_needs_organisation_admin: "Only an administrator of that organisation can grant or withdraw this reviewer role.",
  reviewer_self_grant: "You cannot grant a reviewer role to yourself. Another administrator must do it.",
  reviewer_user_not_found: "That person could not be found.",
  reviewer_user_not_a_person: "A reviewer role is held by a person, not by a service account.",
  reviewer_user_not_a_member: "That person is not a member of that organisation or project.",
  reviewer_expiry_in_the_past: "The end date must be in the future.",
  reviewer_expiry_too_long: "An organisation or project reviewer role runs for at most twelve months. Grant it again after that.",
  reviewer_assignment_exists: "That person already holds this reviewer role at this scope.",
  reviewer_assignment_not_found: "That assignment could not be found.",
  reviewer_scope_mismatch: "That project does not belong to that organisation.",
  perspective_locked:
    "A determination, simulation or submission has already been made from this position. Only an administrator of the organisation can change it, with a reason.",
  perspective_reason_required: "Say why the party this project acts for is being changed (at least 10 characters).",
  perspective_changed_concurrently: "Someone changed this at the same time. Reload and try again.",
  perspective_default_needs_organisation_admin: "Only an administrator of the organisation can set its default.",
  claim_contract_required: "This project has more than one contract. Choose the contract this claim is made under.",
  claim_contract_not_valid: "That contract is not one of this project's contracts, or you cannot read it.",
  claim_contract_fixed:
    "A claim's contract cannot be changed once it is set: the records linked to it belong to that contract.",
  quantum_formula_amount_computed: "A formula line's amount is worked out from its inputs, so it cannot be typed.",
  quantum_formula_inputs_missing: "Enter every figure the formula needs, each as a positive number.",
  quantum_formula_period_required: "State the start and end of the delay period: the formula multiplies by its days.",
  quantum_amount_required: "Enter the amount of this line.",
  determination_reviewer_not_authorised:
    "That person cannot approve this determination. Choose someone on this project who holds determination authority.",
  determination_not_the_reviewer: "Only the person this determination was sent to can approve it or send it back.",
  determination_capacity_changed:
    "The party this project acts for has changed since this determination was started, so it cannot be changed or decided here.",
  determination_superseded: "This revision has already been revised. Only the latest revision can be revised.",
  determination_reviewer_no_longer_authorised:
    "You no longer hold determination authority on this project, so you cannot approve or return this determination. The person who sent it can send it to another approver.",
  determination_reassign_not_sender:
    "Only the person who made or sent this determination can send it to another approver.",
  determination_reviewer_still_authorised:
    "The approver this was sent to can still decide it. It can go to another approver only if they lose determination authority on this project.",
  claim_contract_mismatch:
    "A record of another contract cannot support this claim. Only records of the claim's own contract can be linked or relied on.",
} as const;

export type StatusKind = "claim" | "arbitration_case" | "arbitration_draft";

const STATUS_MAPS: Record<StatusKind, Record<string, string>> = {
  claim: CLAIM_STATUS_LABELS,
  arbitration_case: ARBITRATION_CASE_STATUS_LABELS,
  arbitration_draft: ARBITRATION_DRAFT_STATUS_LABELS,
};

/** A status in plain words. An unmapped value is shown humanised, never as a raw key. */
export function plainStatus(kind: StatusKind, value: string | null | undefined): string {
  if (!value) return "Not set";
  return STATUS_MAPS[kind][value] ?? humanise(value);
}

export function humanise(value: string): string {
  const words = value.replace(/[_.-]+/g, " ").trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1).toLowerCase() : "Not set";
}

const OBJECT_ID = /\b[0-9a-f]{24}\b/gi;
const UUID = /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi;
/** A content hash (SHA-1 or longer): a plan or version fingerprint is no more readable than an id. */
const HASH = /\b[0-9a-f]{40,}\b/gi;
const STACK = /(Traceback|\bat [\w.$<>]+ \(|File ".*", line \d+)/;

/** Whether text shows something a user must never see: a database id or stack text. */
export function containsRawId(text: string): boolean {
  OBJECT_ID.lastIndex = 0;
  UUID.lastIndex = 0;
  HASH.lastIndex = 0;
  return OBJECT_ID.test(text) || UUID.test(text) || HASH.test(text);
}

function scrub(text: string): string {
  return text.replace(HASH, "a record").replace(OBJECT_ID, "a record").replace(UUID, "a record");
}

const FALLBACK = "Something went wrong. Try again, or ask your administrator if it keeps happening.";

/**
 * The message to show for a failed API call.
 *
 * Order: a structured refusal's `user_message`; a known `code`; a plain-string
 * `detail` (ids scrubbed); the status family; a generic sentence. Stack text is
 * never shown.
 */
export function errorMessage(error: unknown): string {
  const response = (error as { response?: { status?: number; data?: unknown } } | null)?.response;
  const data = response?.data as { detail?: unknown; code?: string; user_message?: string } | undefined;
  const detail = data?.detail ?? data;
  if (detail && typeof detail === "object") {
    const structured = detail as { user_message?: unknown; code?: unknown };
    if (typeof structured.user_message === "string" && structured.user_message.trim()) {
      return scrub(structured.user_message.trim());
    }
    if (typeof structured.code === "string" && structured.code in ERROR_CODE_MESSAGES) {
      return ERROR_CODE_MESSAGES[structured.code as keyof typeof ERROR_CODE_MESSAGES];
    }
  }
  if (typeof detail === "string" && detail.trim() && !STACK.test(detail)) {
    const reason = detail.match(/^Not authorized: (\w+)$/)?.[1];
    if (reason && reason in ERROR_CODE_MESSAGES) return ERROR_CODE_MESSAGES[reason as keyof typeof ERROR_CODE_MESSAGES];
    if (detail.length <= 300) return scrub(detail.trim());
  }
  const status = response?.status;
  if (status === 403) return ERROR_CODE_MESSAGES.missing_permission;
  if (status === 404) return "That item could not be found. It may have been removed, or you may not have access.";
  if (status === 409) return "Someone changed this at the same time. Reload and try again.";
  return FALLBACK;
}
