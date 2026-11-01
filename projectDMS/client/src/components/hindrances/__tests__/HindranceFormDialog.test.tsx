import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ComponentProps } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ createHindrance: vi.fn(), updateHindrance: vi.fn() }));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }));
vi.mock("sonner", () => ({ toast }));
vi.mock("@/services/hindrance-api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/services/hindrance-api")>()),
  ...api,
}));

import HindranceFormDialog from "../HindranceFormDialog";
import type { HindranceDTO } from "@/services/hindrance-api";

const existing: HindranceDTO = {
  id: "h-1",
  organization_id: "org-A",
  project_id: "proj-A1",
  event_type: "delay_event",
  hindrance_ref: "DLY-0001",
  delay_ref: "D-7",
  title: "Late GFC drawings",
  description: "Drawings for P4 late",
  category: "drawing_approval",
  start_date: "2026-01-05T00:00:00",
  end_date: "2026-01-20T00:00:00",
  responsibility: "employer",
  responsible_party: "Engineer",
  affected_party: "Contractor",
  cause: "Design backlog",
  location: "Pier P4",
  critical_path_impact: true,
  claimed_days: 12,
  assessed_days: null,
  status: "claimed",
  claim_status: null,
  linked_document_ids: [],
  created_at: "2026-01-05T09:00:00",
};

function renderDialog(props: Partial<ComponentProps<typeof HindranceFormDialog>> = {}) {
  const onSaved = vi.fn();
  const onOpenChange = vi.fn();
  render(
    <HindranceFormDialog open onOpenChange={onOpenChange} onSaved={onSaved} projectId="proj-A1" organizationId="org-A" {...props} />,
  );
  return { onSaved, onOpenChange, dialog: within(screen.getByRole("dialog")) };
}

describe("HindranceFormDialog", () => {
  beforeEach(() => vi.clearAllMocks());

  it("validates required fields and date order before calling the API", async () => {
    const { dialog } = renderDialog();

    fireEvent.click(dialog.getByRole("button", { name: "Record entry" }));
    expect(await dialog.findByText("Title is required")).toBeInTheDocument();
    expect(dialog.getByText("Start date is required")).toBeInTheDocument();

    fireEvent.change(dialog.getByLabelText("Title *"), { target: { value: "Access" } });
    fireEvent.change(dialog.getByLabelText("Start / occurrence date *"), { target: { value: "2026-02-10" } });
    fireEvent.change(dialog.getByLabelText("End / resolution date"), { target: { value: "2026-02-01" } });
    fireEvent.click(dialog.getByRole("button", { name: "Record entry" }));

    expect(await dialog.findByText("End date cannot be before the start date")).toBeInTheDocument();
    expect(api.createHindrance).not.toHaveBeenCalled();
  });

  it("does not require or send a delay assessment for a simple constraint", async () => {
    api.createHindrance.mockResolvedValue({ ...existing, id: "h-9", hindrance_ref: "CNS-0001", timeline_sync_status: "synced" });
    const { dialog, onSaved } = renderDialog();

    expect(dialog.queryByLabelText("Claimed (days)")).not.toBeInTheDocument();
    fireEvent.change(dialog.getByLabelText("Type"), { target: { value: "constraint" } });
    fireEvent.change(dialog.getByLabelText("Title *"), { target: { value: "Night-work ban" } });
    fireEvent.change(dialog.getByLabelText("Start / occurrence date *"), { target: { value: "2026-02-10" } });
    fireEvent.click(dialog.getByRole("button", { name: "Record entry" }));

    await waitFor(() => expect(api.createHindrance).toHaveBeenCalledTimes(1));
    expect(api.createHindrance.mock.calls[0][0]).toMatchObject({
      event_type: "constraint",
      critical_path_impact: false,
      claimed_days: null,
      assessed_days: null,
      float_consumed_days: null,
      claim_status: null,
    });
    expect(api.createHindrance.mock.calls[0][0]).not.toHaveProperty("hindrance_ref");
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
    expect(toast.warning).not.toHaveBeenCalled();
  });

  it("warns when the entry saved but the timeline projection failed", async () => {
    api.createHindrance.mockResolvedValue({ ...existing, timeline_sync_status: "failed" });
    const { dialog } = renderDialog();
    fireEvent.change(dialog.getByLabelText("Title *"), { target: { value: "Access" } });
    fireEvent.change(dialog.getByLabelText("Start / occurrence date *"), { target: { value: "2026-02-10" } });
    fireEvent.click(dialog.getByRole("button", { name: "Record entry" }));

    await waitFor(() => expect(toast.warning).toHaveBeenCalledWith(expect.stringContaining("Contract Timeline could not be updated")));
  });

  it("reveals the delay assessment section on demand and sends its values", async () => {
    api.createHindrance.mockResolvedValue({ ...existing, timeline_sync_status: "synced" });
    const { dialog } = renderDialog();

    fireEvent.click(dialog.getByLabelText("Record assessment"));
    fireEvent.click(dialog.getByLabelText("Affects the critical path"));
    fireEvent.change(dialog.getByLabelText("Claimed (days)"), { target: { value: "5" } });
    fireEvent.change(dialog.getByLabelText("Claim status"), { target: { value: "notified" } });
    fireEvent.change(dialog.getByLabelText("Title *"), { target: { value: "Delay" } });
    fireEvent.change(dialog.getByLabelText("Start / occurrence date *"), { target: { value: "2026-02-10" } });
    fireEvent.click(dialog.getByRole("button", { name: "Record entry" }));

    await waitFor(() => expect(api.createHindrance).toHaveBeenCalledTimes(1));
    expect(api.createHindrance.mock.calls[0][0]).toMatchObject({
      critical_path_impact: true,
      claimed_days: 5,
      claim_status: "notified",
    });
  });

  it("edits by sending only changed fields, with a cleared field as null", async () => {
    api.updateHindrance.mockResolvedValue({ ...existing, location: null, title: "Late GFC drawings (P4)" });
    const { dialog, onSaved } = renderDialog({ item: existing });

    expect(dialog.getByLabelText("Title *")).toHaveValue("Late GFC drawings");
    expect(dialog.getByLabelText("Status")).toHaveValue("claimed");
    fireEvent.change(dialog.getByLabelText("Title *"), { target: { value: "Late GFC drawings (P4)" } });
    fireEvent.change(dialog.getByLabelText("Location"), { target: { value: "" } });
    fireEvent.click(dialog.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(api.updateHindrance).toHaveBeenCalledTimes(1));
    expect(api.updateHindrance).toHaveBeenCalledWith("h-1", { title: "Late GFC drawings (P4)", location: null });
    await waitFor(() => expect(onSaved).toHaveBeenCalled());
  });

  it("surfaces the server's validation message", async () => {
    api.createHindrance.mockRejectedValue({
      response: { data: { detail: [{ loc: ["body", "status"], msg: "Input should be 'open'" }] } },
    });
    const { dialog, onOpenChange } = renderDialog();
    fireEvent.change(dialog.getByLabelText("Title *"), { target: { value: "Access" } });
    fireEvent.change(dialog.getByLabelText("Start / occurrence date *"), { target: { value: "2026-02-10" } });
    fireEvent.click(dialog.getByRole("button", { name: "Record entry" }));

    await waitFor(() => expect(toast.error).toHaveBeenCalledWith("status: Input should be 'open'"));
    expect(onOpenChange).not.toHaveBeenCalledWith(false);
  });

  it("refuses to record without a navbar project", async () => {
    const { dialog } = renderDialog({ projectId: "" });
    fireEvent.change(dialog.getByLabelText("Title *"), { target: { value: "Access" } });
    fireEvent.change(dialog.getByLabelText("Start / occurrence date *"), { target: { value: "2026-02-10" } });
    fireEvent.click(dialog.getByRole("button", { name: "Record entry" }));

    expect(await dialog.findByRole("alert")).toHaveTextContent("Select a project in the navbar first");
    expect(api.createHindrance).not.toHaveBeenCalled();
  });
});
