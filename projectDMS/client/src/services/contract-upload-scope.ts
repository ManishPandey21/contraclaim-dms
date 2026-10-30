/**
 * Contract upload scope, expressed so invalid states cannot be written down.
 *
 * The client had two upload paths that disagreed on the wire form of "no
 * project": one sent an empty string, the other omitted the field. The server
 * could not distinguish either from "the user did not answer", so an absent
 * project became an organisation-scope decision nobody made.
 *
 * A discriminated union fixes that at the type level. There is no shape here
 * that says "project scope, no project" — you cannot construct one, so both
 * upload paths converge on the same serialisation because it is the only one
 * the types permit.
 *
 * Action visibility comes from a **server capability response**, never from a
 * role name held by the client. A client-side role test is a second copy of the
 * permission model that drifts silently from the real one, and it can only ever
 * hide a button — the server still has to decide, so the client's copy adds
 * risk without adding safety.
 */

/** The wire discriminator. Sent affirmatively, never as a nullability. */
export type ContractScopeLevel = "organization" | "project";

export interface OrganizationScopeUpload {
  scope_level: "organization";
  /** Deliberately absent: there is no field here to leave blank. */
}

export interface ProjectScopeUpload {
  scope_level: "project";
  /** Required by the type, so it cannot be omitted or left empty. */
  project_id: string;
}

export type ContractUploadScope = OrganizationScopeUpload | ProjectScopeUpload;

/**
 * What the server says this actor may do. The client renders from this and
 * makes no authority decision of its own.
 */
export interface ContractUploadCapabilities {
  can_upload_organization_scope: boolean;
  can_upload_project_scope: boolean;
}

export class InvalidUploadScopeError extends Error {}

/**
 * Build an organisation-scope decision.
 *
 * Takes no argument at all, so there is nothing to accidentally pass.
 */
export function organizationScope(): OrganizationScopeUpload {
  return { scope_level: "organization" };
}

/**
 * Build a project-scope decision, or throw.
 *
 * A blank anchor is rejected here rather than sent: a whitespace string
 * survives `required` checks and arrives at the server as a value, which is the
 * failure mode this whole union exists to remove.
 */
export function projectScope(projectId: string): ProjectScopeUpload {
  const anchor = (projectId ?? "").trim();
  if (!anchor) {
    throw new InvalidUploadScopeError(
      "project scope requires a project id; a blank anchor is an unanswered question, not a project",
    );
  }
  return { scope_level: "project", project_id: anchor };
}

/**
 * The single serialisation both upload paths use.
 *
 * One function rather than two inline object literals, so the two paths cannot
 * drift apart again — the previous defect was exactly that drift.
 */
export function serializeUploadScope(scope: ContractUploadScope): Record<string, string> {
  if (scope.scope_level === "project") {
    return { scope_level: "project", project_id: scope.project_id };
  }
  // No project_id key at all. Sending an empty one would recreate the
  // ambiguity the server now rejects.
  return { scope_level: "organization" };
}

/** Append the scope to a multipart body, identically for every upload path. */
export function appendUploadScope(form: FormData, scope: ContractUploadScope): FormData {
  const serialised = serializeUploadScope(scope);
  Object.entries(serialised).forEach(([key, value]) => form.append(key, value));
  return form;
}

/**
 * Which scopes this actor may choose, according to the server.
 *
 * Takes capabilities, not a user or a role: the parameter list makes a
 * role-name test impossible to write here.
 */
export function availableScopeLevels(
  capabilities: ContractUploadCapabilities,
): ContractScopeLevel[] {
  const levels: ContractScopeLevel[] = [];
  if (capabilities.can_upload_organization_scope) levels.push("organization");
  if (capabilities.can_upload_project_scope) levels.push("project");
  return levels;
}

/** Whether the organisation-scope action should be offered at all. */
export function canOfferOrganizationScope(
  capabilities: ContractUploadCapabilities,
): boolean {
  return capabilities.can_upload_organization_scope;
}
