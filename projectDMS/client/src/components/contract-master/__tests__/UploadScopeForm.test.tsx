/**
 * T26 - Upload scope capture UI.
 *
 * F13-01 - the scope choice is explicit and non-nullable.
 * F13-02 - a cleared project cannot become organisation scope.
 * F13-14 - a selected contract creates no implicit applicability.
 *
 * F13-02 needs a mutation proof for a reason worth naming: a test that never
 * clears the project passes against a form that silently falls back to
 * organisation scope the moment the selector empties. So the test clears it
 * explicitly, and the mutation reinstates the fallback.
 *
 * DEBT-13 is the underlying defect: project selection is sticky client state
 * whose setter is guarded, so clearing it never cleared the stored value. The
 * rule here is that restored client state may repopulate a *selector value* and
 * may never supply a *discriminator*.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { UploadScopeForm } from "../UploadScopeForm";
import type { ContractMasterCapabilities } from "@/services/contract-master-v1-api";

const FULL: ContractMasterCapabilities = {
  can_browse_catalogue: true,
  can_view_instruments: true,
  can_manage_classification: true,
  can_manage_applicability: true,
  can_upload_organization_scope: true,
  can_upload_project_scope: true,
  can_review_migration: true,
  can_promote: true,
};

const PROJECT_TIER: ContractMasterCapabilities = {
  ...FULL,
  can_browse_catalogue: false,
  can_upload_organization_scope: false,
};

const PROJECTS = [
  { id: "project-7", name: "Metro Rail Package 01" },
  { id: "project-9", name: "Coastal Highway" },
];

function renderForm(props: Partial<React.ComponentProps<typeof UploadScopeForm>> = {}) {
  const onSubmit = vi.fn();
  const utils = render(
    <UploadScopeForm
      capabilities={FULL}
      projects={PROJECTS}
      onSubmit={onSubmit}
      {...props}
    />,
  );
  return { ...utils, onSubmit };
}

const submitButton = () => screen.getByRole("button", { name: /upload/i }) as HTMLButtonElement;
const organisationRadio = () => screen.getByLabelText(/organisation-owned/i);
const projectRadio = () => screen.getByLabelText(/belongs to one project/i);
const projectSelect = () => screen.getByLabelText(/^project$/i) as HTMLSelectElement;

// --------------------------------------------------------------------------- //
// F13-01 : explicit and non-nullable
// --------------------------------------------------------------------------- //

describe("F13-01 the scope choice is explicit", () => {
  it("preselects neither scope", () => {
    renderForm();
    expect((organisationRadio() as HTMLInputElement).checked).toBe(false);
    expect((projectRadio() as HTMLInputElement).checked).toBe(false);
  });

  it("cannot submit with neither chosen", () => {
    renderForm();
    expect(submitButton().disabled).toBe(true);
  });

  it("explains why submission is blocked rather than failing silently", () => {
    renderForm();
    expect(screen.getByText(/choose a scope/i)).toBeTruthy();
  });

  it("emits an explicit organisation discriminator", () => {
    const { onSubmit } = renderForm();
    fireEvent.click(organisationRadio());
    fireEvent.click(submitButton());

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0][0]).toEqual({ scope_level: "organization" });
  });

  it("omits project_id entirely under organisation scope", () => {
    const { onSubmit } = renderForm();
    fireEvent.click(organisationRadio());
    fireEvent.click(submitButton());
    expect("project_id" in onSubmit.mock.calls[0][0]).toBe(false);
  });

  it("does not render a project selector under organisation scope", () => {
    renderForm();
    fireEvent.click(organisationRadio());
    expect(screen.queryByLabelText(/^project$/i)).toBeNull();
  });

  it("emits an explicit project discriminator with its anchor", () => {
    const { onSubmit } = renderForm();
    fireEvent.click(projectRadio());
    fireEvent.change(projectSelect(), { target: { value: "project-7" } });
    fireEvent.click(submitButton());

    expect(onSubmit.mock.calls[0][0]).toEqual({
      scope_level: "project",
      project_id: "project-7",
    });
  });
});

// --------------------------------------------------------------------------- //
// F13-02 : a cleared project cannot become organisation scope
// --------------------------------------------------------------------------- //

describe("F13-02 a cleared project cannot become organisation scope", () => {
  it("blocks submission when the project selector is emptied", () => {
    renderForm();
    fireEvent.click(projectRadio());
    fireEvent.change(projectSelect(), { target: { value: "project-7" } });
    expect(submitButton().disabled).toBe(false);

    fireEvent.change(projectSelect(), { target: { value: "" } });

    expect(submitButton().disabled).toBe(true);
  });

  it("keeps project scope selected when the project is cleared", () => {
    renderForm();
    fireEvent.click(projectRadio());
    fireEvent.change(projectSelect(), { target: { value: "project-7" } });
    fireEvent.change(projectSelect(), { target: { value: "" } });

    // Clearing invalidates the form. It does not switch the discriminator.
    expect((projectRadio() as HTMLInputElement).checked).toBe(true);
    expect((organisationRadio() as HTMLInputElement).checked).toBe(false);
  });

  it("never emits organisation scope after a project was cleared", () => {
    const { onSubmit } = renderForm();
    fireEvent.click(projectRadio());
    fireEvent.change(projectSelect(), { target: { value: "project-7" } });
    fireEvent.change(projectSelect(), { target: { value: "" } });
    fireEvent.click(submitButton());

    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("says why, rather than quietly widening the scope", () => {
    renderForm();
    fireEvent.click(projectRadio());
    fireEvent.change(projectSelect(), { target: { value: "" } });
    expect(screen.getByText(/project scope needs a project/i)).toBeTruthy();
  });
});

// --------------------------------------------------------------------------- //
// DEBT-13 : restored client state may fill a selector, never a discriminator
// --------------------------------------------------------------------------- //

describe("DEBT-13 sticky client state", () => {
  it("may pre-fill the project selector from remembered state", () => {
    renderForm({ rememberedProjectId: "project-9" });
    fireEvent.click(projectRadio());
    expect(projectSelect().value).toBe("project-9");
  });

  it("never lets remembered state preselect the discriminator", () => {
    renderForm({ rememberedProjectId: "project-9" });
    expect((projectRadio() as HTMLInputElement).checked).toBe(false);
    expect((organisationRadio() as HTMLInputElement).checked).toBe(false);
    expect(submitButton().disabled).toBe(true);
  });

  it("does not restore a cleared project behind the user's back", () => {
    renderForm({ rememberedProjectId: "project-9" });
    fireEvent.click(projectRadio());
    fireEvent.change(projectSelect(), { target: { value: "" } });
    expect(projectSelect().value).toBe("");
    expect(submitButton().disabled).toBe(true);
  });
});

// --------------------------------------------------------------------------- //
// F13-14 : upload is not applicability
// --------------------------------------------------------------------------- //

describe("F13-14 upload creates no applicability", () => {
  it("states that uploading does not apply the document to a contract", () => {
    renderForm();
    expect(screen.getByText(/does not apply/i)).toBeTruthy();
  });

  it("sends no contract or applicability field with the scope", () => {
    const { onSubmit } = renderForm({ selectedContractId: "contract-3" });
    fireEvent.click(organisationRadio());
    fireEvent.click(submitButton());

    const payload = onSubmit.mock.calls[0][0];
    expect(Object.keys(payload)).toEqual(["scope_level"]);
    expect(JSON.stringify(payload)).not.toContain("contract-3");
  });

  it("offers no apply-to-current-contract control", () => {
    renderForm({ selectedContractId: "contract-3" });
    expect(screen.queryByText(/apply to/i)).toBeNull();
  });
});

// --------------------------------------------------------------------------- //
// capability-driven visibility (display only; the server still enforces)
// --------------------------------------------------------------------------- //

describe("capability-driven visibility", () => {
  it("hides organisation scope from an actor the server would refuse", () => {
    renderForm({ capabilities: PROJECT_TIER });
    expect(screen.queryByLabelText(/organisation-owned/i)).toBeNull();
  });

  it("still offers project scope to that actor", () => {
    renderForm({ capabilities: PROJECT_TIER });
    expect(screen.getByLabelText(/belongs to one project/i)).toBeTruthy();
  });

  it("surfaces a server refusal rather than pretending it succeeded", () => {
    renderForm({ serverError: "actor lacks dms.contract.master.manage at the requested scope" });
    expect(screen.getByText(/dms\.contract\.master\.manage/i)).toBeTruthy();
  });

  it("surfaces a retry scope conflict from the server", () => {
    renderForm({
      serverError:
        "upload doc-1 was created with scope 'project'; a retry claiming 'organization' is rejected",
    });
    expect(screen.getByText(/retry claiming/i)).toBeTruthy();
  });
});
