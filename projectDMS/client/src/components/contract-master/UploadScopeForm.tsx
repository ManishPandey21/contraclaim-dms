/**
 * Upload scope capture — an explicit organisation-or-project choice.
 *
 * The defect this replaces is subtle enough to be worth stating. Project
 * selection was sticky client state whose setter was guarded, so clearing it
 * never cleared the stored value; combined with a server that read an absent
 * project as "organisation", a user who cleared the selector could upload an
 * organisation-wide document without ever choosing to.
 *
 * Two rules follow, and the component is built around them:
 *
 * - **Remembered state may fill a selector, never a discriminator.**
 *   `rememberedProjectId` pre-fills the project dropdown and cannot preselect
 *   the scope. Scope starts `null`, which is "not answered" and is not
 *   submittable.
 * - **Clearing the project invalidates the form.** It does not fall back to
 *   organisation scope. Falling back is the behaviour that produced the defect,
 *   and it is precisely what looks like a helpful default.
 *
 * Uploading also does not apply the document to a contract. A contract selected
 * elsewhere in the app is where the user happens to be working — treating it as
 * legal applicability would manufacture the one fact the corpus never contains.
 * `selectedContractId` is accepted so the caller need not strip it, and is
 * deliberately never sent.
 *
 * Capabilities hide controls the server would refuse. That is presentation, not
 * authorisation: the server decides, and a hidden control is a courtesy, not a
 * gate.
 */

import React, { useState } from "react";

import {
  organizationScope,
  projectScope,
  serializeUploadScope,
} from "@/services/contract-upload-scope";
import type { ContractMasterCapabilities } from "@/services/contract-master-v1-api";

export interface ProjectOption {
  id: string;
  name: string;
}

export interface UploadScopeFormProps {
  capabilities: ContractMasterCapabilities;
  projects: ProjectOption[];
  onSubmit: (payload: Record<string, string>) => void;
  /** May fill the selector. Never selects a scope. */
  rememberedProjectId?: string;
  /** Operational context only. Never sent, never turned into applicability. */
  selectedContractId?: string;
  /** A refusal the server actually returned, shown verbatim. */
  serverError?: string;
}

type ScopeLevel = "organization" | "project";

export function UploadScopeForm({
  capabilities,
  projects,
  onSubmit,
  rememberedProjectId,
  selectedContractId,
  serverError,
}: UploadScopeFormProps) {
  // `null` is "the user has not answered". It is not a scope, and it is the
  // only initial value that cannot be mistaken for one.
  const [level, setLevel] = useState<ScopeLevel | null>(null);
  const [projectId, setProjectId] = useState<string>(rememberedProjectId ?? "");

  const trimmedProject = projectId.trim();
  let blocker: string | null = null;
  if (level === null) {
    blocker = "Choose a scope. Nothing is preselected, and there is no default.";
  } else if (level === "project" && !trimmedProject) {
    blocker =
      "Project scope needs a project. Clearing it invalidates the form — it does not fall back to organisation scope.";
  }

  const submit = () => {
    if (blocker !== null || level === null) return;
    const scope =
      level === "organization" ? organizationScope() : projectScope(trimmedProject);
    onSubmit(serializeUploadScope(scope));
  };

  return (
    <div className="space-y-4">
      <fieldset>
        <legend className="text-sm font-medium">Who owns this document?</legend>
        <div className="mt-2 flex flex-col gap-2">
          {capabilities.can_upload_organization_scope ? (
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name="contract-upload-scope"
                checked={level === "organization"}
                onChange={() => setLevel("organization")}
              />
              <span>Organisation-owned</span>
            </label>
          ) : null}

          {capabilities.can_upload_project_scope ? (
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name="contract-upload-scope"
                checked={level === "project"}
                onChange={() => setLevel("project")}
              />
              <span>Belongs to one project</span>
            </label>
          ) : null}
        </div>
      </fieldset>

      {level === "project" ? (
        <div>
          <label className="block text-sm font-medium" htmlFor="contract-upload-project">
            Project
          </label>
          <select
            id="contract-upload-project"
            className="mt-1 w-72 rounded border border-slate-300 px-2 py-1"
            value={projectId}
            onChange={(event) => setProjectId(event.target.value)}
          >
            <option value="">— select a project —</option>
            {projects.map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </select>
        </div>
      ) : null}

      <p className="rounded bg-slate-50 p-3 text-sm text-slate-700">
        Uploading <strong>does not apply</strong> this document to a contract. Applicability is a
        separate, confirmed action.
      </p>

      <div>
        <button
          type="button"
          className="rounded bg-slate-900 px-3 py-1.5 text-sm text-white disabled:bg-slate-300"
          disabled={blocker !== null}
          onClick={submit}
        >
          Upload
        </button>
        {blocker ? <p className="mt-2 text-sm text-rose-700">{blocker}</p> : null}
        {serverError ? (
          <p className="mt-2 rounded bg-rose-50 p-2 text-sm text-rose-900" role="alert">
            {serverError}
          </p>
        ) : null}
      </div>
    </div>
  );
}

export default UploadScopeForm;
