import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import CsvImportDialog, {
  type CSVImportPreview,
} from "../CsvImportDialog";
import { TenantProvider } from "@/contexts/TenantContext";

const { getCurrentUserProfile, listOrganizations, listProjects } = vi.hoisted(
  () => ({
    getCurrentUserProfile: vi.fn(),
    listOrganizations: vi.fn(),
    listProjects: vi.fn(),
  }),
);

vi.mock("@/services/session-api", () => ({ getCurrentUserProfile }));
vi.mock("@/services/organizations-api", () => ({ listOrganizations }));
vi.mock("@/services/projects-api", () => ({ listProjects }));

const emptyPreview: CSVImportPreview = {
  total_rows: 1,
  valid_rows: 1,
  invalid_rows: 0,
  can_import: true,
  rows: [
    {
      row_number: 2,
      data: { title: "Foundation complete" },
      errors: [],
      warnings: [],
      duplicate: false,
    },
  ],
  required_headers: ["title", "contractual_week_number"],
  template_headers: ["title", "contractual_week_number"],
};

describe("CsvImportDialog tenant scope", () => {
  beforeEach(() => {
    window.ResizeObserver = class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    } as typeof window.ResizeObserver;
    Object.defineProperties(Element.prototype, {
      hasPointerCapture: { value: vi.fn(() => false), configurable: true },
      setPointerCapture: { value: vi.fn(), configurable: true },
      releasePointerCapture: { value: vi.fn(), configurable: true },
      scrollIntoView: { value: vi.fn(), configurable: true },
    });
    const storage = new Map<string, string>();
    vi.mocked(window.localStorage.getItem).mockImplementation(
      (key) => storage.get(key) ?? null,
    );
    vi.mocked(window.localStorage.setItem).mockImplementation((key, value) => {
      storage.set(key, String(value));
    });
    vi.mocked(window.localStorage.removeItem).mockImplementation((key) => {
      storage.delete(key);
    });
    vi.clearAllMocks();
    getCurrentUserProfile.mockResolvedValue({
      roles: ["orgadmin"],
      organization_id: "org-1",
      projects: [],
    });
    listOrganizations.mockResolvedValue([{ _id: "org-1", name: "Acme Infrastructure" }]);
    listProjects.mockResolvedValue([
      { _id: "project-1", name: "North Corridor", organization_id: "org-1" },
      { _id: "project-2", name: "South Corridor", organization_id: "org-1" },
      { _id: "project-3", name: "Other Org", organization_id: "org-2" },
    ]);
  });

  it("requires a permitted project and sends the canonical scope with preview", async () => {
    const user = userEvent.setup();
    const onPreview = vi.fn().mockResolvedValue(emptyPreview);

    render(
      <TenantProvider>
        <CsvImportDialog
          open
          onOpenChange={vi.fn()}
          title="Upload Key Dates CSV"
          description="Preview imported milestones."
          sampleFileName="key-date-import-template.csv"
          onDownloadTemplate={vi.fn()}
          onPreview={onPreview}
          onImport={vi.fn()}
          onImported={vi.fn()}
          rowLabel={(row) => String(row.data.title)}
        />
      </TenantProvider>,
    );

    expect(await screen.findByText("Acme Infrastructure")).toBeInTheDocument();
    const previewButton = screen.getByRole("button", { name: "Preview" });
    expect(previewButton).toBeDisabled();

    await user.click(screen.getByRole("combobox", { name: "Project" }));
    await user.click(await screen.findByRole("option", { name: "South Corridor" }));
    expect(screen.queryByRole("option", { name: "Other Org" })).not.toBeInTheDocument();

    const file = new File(
      ["title,contractual_week_number\nFoundation complete,5"],
      "key-dates.csv",
      { type: "text/csv" },
    );
    await user.upload(screen.getByLabelText("CSV file"), file);
    await user.click(previewButton);

    await waitFor(() =>
      expect(onPreview).toHaveBeenCalledWith(file, {
        organization_id: "org-1",
        project_id: "project-2",
      }),
    );
  });

  it("locks a project-restricted user to the assigned organisation and project", async () => {
    const user = userEvent.setup();
    const onPreview = vi.fn().mockResolvedValue(emptyPreview);
    getCurrentUserProfile.mockResolvedValue({
      roles: ["projectuser"],
      organization_id: "org-1",
      projects: ["project-2"],
    });

    render(
      <TenantProvider>
        <CsvImportDialog
          open
          onOpenChange={vi.fn()}
          title="Upload Bank Guarantees CSV"
          description="Preview imported guarantees."
          sampleFileName="bank-guarantee-import-template.csv"
          onDownloadTemplate={vi.fn()}
          onPreview={onPreview}
          onImport={vi.fn()}
          onImported={vi.fn()}
          rowLabel={(row) => String(row.data.bg_number)}
        />
      </TenantProvider>,
    );

    const organization = await screen.findByRole("combobox", {
      name: "Organisation",
    });
    const project = screen.getByRole("combobox", { name: "Project" });
    expect(organization).toBeDisabled();
    expect(project).toBeDisabled();
    expect(screen.getByText("Acme Infrastructure")).toBeInTheDocument();
    expect(screen.getByText("South Corridor")).toBeInTheDocument();

    const file = new File(
      ["contract_id,bg_number,bg_type\nprimary,BG-2026-001,performance"],
      "bank-guarantees.csv",
      { type: "text/csv" },
    );
    await user.upload(screen.getByLabelText("CSV file"), file);
    await user.click(screen.getByRole("button", { name: "Preview" }));

    await waitFor(() =>
      expect(onPreview).toHaveBeenCalledWith(file, {
        organization_id: "org-1",
        project_id: "project-2",
      }),
    );
  });

  it("renders every preview row inside a bounded vertical scroll region", async () => {
    const user = userEvent.setup();
    const rows = Array.from({ length: 15 }, (_, index) => ({
      row_number: index + 2,
      data: { title: `Milestone ${index + 1}` },
      errors: [],
      warnings: [],
      duplicate: false,
    }));
    const onPreview = vi.fn().mockResolvedValue({
      ...emptyPreview,
      total_rows: rows.length,
      valid_rows: rows.length,
      rows,
    });

    render(
      <TenantProvider>
        <CsvImportDialog
          open
          onOpenChange={vi.fn()}
          title="Upload Key Dates CSV"
          description="Preview imported milestones."
          sampleFileName="key-date-import-template.csv"
          onDownloadTemplate={vi.fn()}
          onPreview={onPreview}
          onImport={vi.fn()}
          onImported={vi.fn()}
          rowLabel={(row) => String(row.data.title)}
        />
      </TenantProvider>,
    );

    await screen.findByText("Acme Infrastructure");
    await user.click(screen.getByRole("combobox", { name: "Project" }));
    await user.click(await screen.findByRole("option", { name: "North Corridor" }));

    await user.upload(
      screen.getByLabelText("CSV file"),
      new File(["title,contractual_week_number\nMilestone 1,5"], "key-dates.csv", {
        type: "text/csv",
      }),
    );
    await user.click(screen.getByRole("button", { name: "Preview" }));

    const previewRows = await screen.findByRole("region", {
      name: "Uploaded CSV rows",
    });
    expect(previewRows).toHaveClass("max-h-[40vh]", "overflow-y-auto");
    expect(within(previewRows).getByText("Milestone 1")).toBeInTheDocument();
    expect(within(previewRows).getByText("Milestone 15")).toBeInTheDocument();
  });
});
