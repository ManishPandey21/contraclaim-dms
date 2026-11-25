import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DocumentPicker from "@/components/pickers/DocumentPicker";
import LetterPicker from "@/components/pickers/LetterPicker";
import { UNAVAILABLE } from "@/lib/picker-labels";
import { containsRawId } from "@/lib/plain-language";

const docs = vi.hoisted(() => ({ listDocuments: vi.fn(), getDocument: vi.fn() }));
vi.mock("@/services/documents-api", () => docs);

const LETTER = "65f0a1b2c3d4e5f6a7b8c9d1";
const DRAWING = "65f0a1b2c3d4e5f6a7b8c9d2";
const GONE = "65f0a1b2c3d4e5f6a7b8c9d3";

beforeEach(() => {
  vi.clearAllMocks();
  docs.listDocuments.mockResolvedValue({
    documents: [
      { _id: LETTER, uploadType: "incoming", letterNo: "NHAI/PD/112", subject: "Notice of delay", date: "2026-04-11T00:00:00" },
      { _id: DRAWING, uploadType: "contract", filename: "GA drawing.pdf" },
    ],
  });
  docs.getDocument.mockImplementation(async (id: string) => {
    if (id === LETTER) return { _id: LETTER, uploadType: "incoming", letterNo: "NHAI/PD/112", subject: "Notice of delay", date: "2026-04-11" };
    throw { response: { status: 404 } };
  });
});

describe("DocumentPicker and LetterPicker", () => {
  it("searches within the project and chooses by name", async () => {
    const onChange = vi.fn();
    render(<DocumentPicker projectId="proj-A1" value={[]} onChange={onChange} label="Evidence" />);
    await userEvent.type(screen.getByLabelText("Search: Evidence"), "delay{Enter}");
    expect(docs.listDocuments).toHaveBeenCalledWith({ project_id: "proj-A1", q: "delay", limit: 50 });
    const results = await screen.findByRole("list", { name: "Results: Evidence" });
    await userEvent.click(within(results).getAllByRole("button", { name: "Choose" })[1]);
    expect(onChange).toHaveBeenCalledWith([DRAWING]);
  });

  it("shows a chosen document that cannot be read as restricted, never as its id", async () => {
    const { container } = render(<DocumentPicker projectId="proj-A1" value={[GONE]} onChange={vi.fn()} label="Evidence" />);
    expect(await screen.findByText(UNAVAILABLE)).toBeInTheDocument();
    expect(containsRawId(container.textContent || "")).toBe(false);
  });

  it("offers letters only, labelled by reference, subject and date", async () => {
    const { container } = render(<LetterPicker projectId="proj-A1" value={[LETTER]} onChange={vi.fn()} label="Notice letter" />);
    expect(await screen.findByText("NHAI/PD/112 · Notice of delay · 2026-04-11")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Search: Notice letter"), "{Enter}");
    const results = await screen.findByRole("list", { name: "Results: Notice letter" });
    expect(within(results).getAllByRole("listitem")).toHaveLength(1);
    expect(within(results).queryByText("GA drawing.pdf")).not.toBeInTheDocument();
    await waitFor(() => expect(containsRawId(container.textContent || "")).toBe(false));
  });

  it("keeps a single choice when single is set", async () => {
    const onChange = vi.fn();
    render(<LetterPicker projectId="proj-A1" value={[]} onChange={onChange} label="Notice letter" single />);
    await userEvent.type(screen.getByLabelText("Search: Notice letter"), "{Enter}");
    const results = await screen.findByRole("list", { name: "Results: Notice letter" });
    await userEvent.click(within(results).getByRole("button", { name: "Choose" }));
    expect(onChange).toHaveBeenCalledWith([LETTER]);
  });
});
