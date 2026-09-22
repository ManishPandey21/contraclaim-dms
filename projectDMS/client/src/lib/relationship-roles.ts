/**
 * Roles that assert the linked Document IS correspondence (server:
 * `CORRESPONDENCE_ROLES` in entity_adapter_registry.py). The server refuses any
 * other Document under them with 422, so the link selector offers only
 * incoming/outgoing Documents for these roles. Every other role
 * (supporting_document, notice, variation_submission, ...) may legitimately
 * point at contract or supporting files and is not filtered.
 */
export const CORRESPONDENCE_ROLES: ReadonlySet<string> = new Set([
  "correspondence",
  "payment_correspondence",
]);

export function isCorrespondenceRole(role: string): boolean {
  return CORRESPONDENCE_ROLES.has(role);
}

/**
 * Register types the Document side may link to ("Link to Record"). Mirrors the
 * server's LINK_TO_RECORD_TARGET_TYPES: only targets whose adapter, deep link
 * and register page are verified. The server refuses anything else with 404.
 */
export const LINK_TO_RECORD_TARGETS: ReadonlyArray<{ value: string; label: string }> = [
  { value: "variation", label: "Variation" },
  { value: "delay_event", label: "Hindrance / Constraint" },
  { value: "claim", label: "Claim" },
  { value: "ipc_bill", label: "IPC / Bill" },
  { value: "insurance", label: "Insurance" },
  { value: "bank_guarantee_event", label: "Bank Guarantee event" },
  { value: "key_date_achievement", label: "Key Date achievement" },
  { value: "eot_submission", label: "EOT submission" },
  { value: "eot_determination", label: "EOT determination" },
];

const TARGET_TYPE_LABELS: Record<string, string> = {
  ...Object.fromEntries(LINK_TO_RECORD_TARGETS.map((item) => [item.value, item.label])),
  bank_guarantee: "Bank Guarantee",
  contract_document: "Contract Document",
};

export function targetTypeLabel(targetType?: string | null): string {
  const key = String(targetType || "");
  return TARGET_TYPE_LABELS[key] || key.replace(/_/g, " ");
}

const ROLE_LABELS: Record<string, string> = {
  notice: "Notice",
  claim_submission: "Claim submission",
  supporting_document: "Supporting document",
  engineer_response: "Engineer response",
  employer_response: "Employer response",
  determination: "Determination",
  correspondence: "Correspondence",
  ipc_submission: "IPC submission",
  certified_ipc: "Certified IPC",
  invoice: "Invoice",
  payment_certificate: "Payment certificate",
  payment_correspondence: "Payment correspondence",
  original_bg: "Original BG",
  submission: "Submission",
  extension: "Extension",
  release: "Release",
  contractor_notification: "Contractor notification",
  engineer_acknowledgement: "Engineer acknowledgement",
  completion_certificate: "Completion certificate",
  inspection_record: "Inspection record",
  eot_submission: "EOT submission",
  eot_supporting_document: "EOT supporting document",
  eot_determination: "EOT determination",
  engineer_determination: "Engineer determination",
  policy: "Policy",
  certificate: "Certificate",
  variation_submission: "Variation submission",
  variation_approval: "Variation approval",
  instruction: "Instruction",
  site_record: "Site record",
  photograph: "Photograph",
  programme_record: "Programme record",
  manual_review: "Legacy link (review)",
};

export function relationshipRoleLabel(role?: string | null): string {
  const key = String(role || "");
  return ROLE_LABELS[key] || key.replace(/_/g, " ");
}

/** The role a correspondence Document most plausibly plays, among those allowed. */
export function defaultRoleFor(allowedRoles: ReadonlyArray<string>): string {
  for (const preferred of ["correspondence", "payment_correspondence", "supporting_document"]) {
    if (allowedRoles.includes(preferred)) return preferred;
  }
  return allowedRoles[0] || "";
}
