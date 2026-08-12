import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import CsvImportDialog, { type CSVImportPreview } from "../CsvImportDialog";

// This suite pins the dialog to the *minimum* TenantContext contract that every
// branch of this app ships. Production regressed because the dialog required
// `tenant.contextReady`, a field only some revisions of TenantContext provide:
// where it is absent the readiness expression is `undefined && ...`, so a
// correctly selected Organisation and Project were refused on every click with
// "Select an Organisation and Project first". The production bundle is built by
// Vite/esbuild, which does not type-check, so nothing caught it before release.
//
// The tenant value below therefore deliberately omits contextReady, roleTier,
// organizationLocked, projectLocked and hasNoAccessibleScope. If the dialog
// starts depending on any of them again, these tests fail.
const { useTenant } = vi.hoisted(() => ({ useTenant: vi.fn() }));
vi.mock("@/contexts/TenantContext", () => ({ useTenant }));

const ORG = "6512f0a1b2c3d4e5f6a7b8c9";
const PROJECT = "70a1b2c3d4e5f6a7b8c9d0e1";

function deployedTenant(overrides: Record<string, unknown> = {}) {
  return {
    organizations: [{ _id: ORG, name: "GULERMAK-SAM INDIA KANPUR METRO JOINT VENTURE" }],
    projects: [{ _id: PROJECT, name: "GUL-SAM JV KNPCC05", organization_id: ORG }],
    selectedOrganization: { _id: ORG, name: "GULERMAK-SAM INDIA KANPUR METRO JOINT VENTURE" },
    selectedProject: { _id: PROJECT, name: "GUL-SAM JV KNPCC05", organization_id: ORG },
    selectedOrganizationId: ORG,
    selectedProjectId: PROJECT,
    canSwitchOrganization: false,
    canSwitchProject: false,
    loading: false,
    error: null,
    selectOrganization: vi.fn(),
    selectProject: vi.fn(),
    ...overrides,
  };
}

const okPreview: CSVImportPreview = {
  total_rows: 1,
  valid_rows: 1,
  invalid_rows: 0,
  can_import: true,
  rows: [
    { row_number: 2, data: { title: "Foundation complete" }, errors: [], warnings: [], duplicate: false },
  ],
  required_headers: ["title", "contractual_week_number"],
  template_headers: ["title", "contractual_week_number"],
};

function renderDialog(props: Record<string, unknown> = {}) {
  const onPreview = vi.fn().mockResolvedValue(okPreview);
  const onImport = vi.fn().mockResolvedValue({ ...okPreview, imported_count: 1, created_ids: ["a"] });
  // A fresh element per render: React bails out of reconciliation when the
  // element reference is unchanged, which would silently skip the re-render.
  const build = () => (
    <CsvImportDialog
      open
      onOpenChange={vi.fn()}
      title="Upload Key Dates CSV"
      description="Preview imported milestones."
      sampleFileName="key-date-import-template.csv"
      onDownloadTemplate={vi.fn()}
      onPreview={onPreview}
      onImport={onImport}
      onImported={vi.fn()}
      rowLabel={(row) => String(row.data.title)}
      {...props}
    />
  );
  const { rerender } = render(build());
  return { onPreview, onImport, rerender: () => rerender(build()) };
}

const csvFile = () =>
  new File(["title,contractual_week_number\nFoundation complete,5"], "key-dates.csv", {
    type: "text/csv",
  });

describe("CsvImportDialog against the deployed TenantContext contract", () => {
  beforeEach(() => {
    window.ResizeObserver = class {
      observe() {}
      unobserve() {}
      disconnect() {}
    } as typeof window.ResizeObserver;
    vi.clearAllMocks();
    useTenant.mockReturnValue(deployedTenant());
  });

  it("previews with the navbar selection without asking the user to reselect", async () => {
    const user = userEvent.setup();
    const { onPreview } = renderDialog();

    await user.upload(screen.getByLabelText("CSV file"), csvFile());

    const preview = screen.getByRole("button", { name: "Preview" });
    expect(preview).toBeEnabled();

    await user.click(preview);

    // Canonical ids, never the display names.
    await waitFor(() =>
      expect(onPreview).toHaveBeenCalledWith(expect.any(File), {
        organization_id: ORG,
        project_id: PROJECT,
      }),
    );
    expect(screen.queryByText("Select an Organisation and Project first")).not.toBeInTheDocument();
  });

  it("names the missing condition instead of a generic scope message", async () => {
    useTenant.mockReturnValue(deployedTenant({ selectedProjectId: "", selectedProject: null }));
    renderDialog();

    expect(await screen.findByText("Please select a Project.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Preview" })).toBeDisabled();
  });

  it("refuses a project belonging to another organisation", async () => {
    useTenant.mockReturnValue(
      deployedTenant({
        projects: [{ _id: PROJECT, name: "Other Org Project", organization_id: "other-org" }],
      }),
    );
    renderDialog();

    expect(
      await screen.findByText("The selected Project does not belong to the selected Organisation."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Preview" })).toBeDisabled();
  });

  it("keeps Preview disabled until a CSV file is chosen", () => {
    renderDialog();
    expect(screen.getByRole("button", { name: "Preview" })).toBeDisabled();
  });

  it("imports against the scope the preview validated", async () => {
    const user = userEvent.setup();
    const { onPreview, onImport } = renderDialog();

    await user.upload(screen.getByLabelText("CSV file"), csvFile());
    await user.click(screen.getByRole("button", { name: "Preview" }));
    await waitFor(() => expect(onPreview).toHaveBeenCalled());

    await user.click(screen.getByRole("button", { name: "Import" }));

    await waitFor(() =>
      expect(onImport).toHaveBeenCalledWith(expect.any(File), {
        organization_id: ORG,
        project_id: PROJECT,
      }),
    );
  });

  it("invalidates the preview when the navbar project changes underneath it", async () => {
    const user = userEvent.setup();
    const { onPreview, onImport, rerender } = renderDialog();

    await user.upload(screen.getByLabelText("CSV file"), csvFile());
    await user.click(screen.getByRole("button", { name: "Preview" }));
    await waitFor(() => expect(onPreview).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: "Import" })).toBeEnabled();

    const movedProject = "80b2c3d4e5f6a7b8c9d0e1f2";
    useTenant.mockReturnValue(
      deployedTenant({
        projects: [{ _id: movedProject, name: "Another Project", organization_id: ORG }],
        selectedProjectId: movedProject,
        selectedProject: { _id: movedProject, name: "Another Project", organization_id: ORG },
      }),
    );
    rerender();

    // The stale preview must not remain importable against the new project.
    await waitFor(() => expect(screen.getByRole("button", { name: "Import" })).toBeDisabled());
    expect(onImport).not.toHaveBeenCalled();
  });
});
