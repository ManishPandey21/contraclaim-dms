/**
 * Reviewer roles and the role list (plan 4B-07, 4B-08).
 *
 * A reviewer role is granted by a reviewer assignment. The Users form edits one
 * access role; everything else on a user's role list must survive an edit.
 */
export const SKILL_REVIEWER_ROLES = [
  "chief_contract_expert",
  "delay_planning_expert",
  "arbitration_case_preparation_expert",
  "senior_legal_approver",
] as const;

export const ARBITRATION_REVIEWER_ROLES = [
  "legal_reviewer",
  "contract_reviewer",
  "delay_reviewer",
  "quantum_reviewer",
  "export_authorizer",
] as const;

export const REVIEWER_ROLE_LABELS: Record<string, string> = {
  chief_contract_expert: "Chief contract expert",
  delay_planning_expert: "Delay and planning expert",
  arbitration_case_preparation_expert: "Arbitration case preparation expert",
  senior_legal_approver: "Senior legal approver",
  legal_reviewer: "Legal reviewer",
  contract_reviewer: "Contract reviewer",
  delay_reviewer: "Delay reviewer",
  quantum_reviewer: "Quantum reviewer",
  export_authorizer: "Export authoriser",
};

/** Roles a person may be given at a scope. Platform scope is for skill packs only. */
export function reviewerRolesForScope(scope: "platform" | "organization" | "project"): string[] {
  return scope === "platform"
    ? [...SKILL_REVIEWER_ROLES]
    : [...SKILL_REVIEWER_ROLES, ...ARBITRATION_REVIEWER_ROLES];
}

/**
 * The role list to save when the Users form changes a user's access role.
 *
 * The form shows and edits the first role only. It used to save a one-item
 * list, which silently removed every other role the user held. The edited role
 * replaces the first; the rest are kept in order, without duplicates.
 */
export function rolesAfterEdit(existing: readonly string[] | undefined | null, accessRole: string): string[] {
  const kept = (existing ?? []).slice(1).filter((role) => role && role !== accessRole);
  return [accessRole, ...kept.filter((role, index) => kept.indexOf(role) === index)];
}

/** Twelve months from a date, as the date input's value; the default end of an organisation or project grant. */
export function defaultExpiry(from: Date = new Date()): string {
  const end = new Date(Date.UTC(from.getUTCFullYear() + 1, from.getUTCMonth(), from.getUTCDate()));
  end.setUTCDate(end.getUTCDate() - 1);
  return end.toISOString().slice(0, 10);
}
