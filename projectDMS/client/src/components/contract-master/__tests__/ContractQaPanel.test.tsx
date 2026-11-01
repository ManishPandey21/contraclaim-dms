/**
 * T29 - Q&A UI: modes, empty, degraded and provenance states.
 *
 * F13-09 - a browse result cannot launch Q&A.
 * F13-10 - historical mode requires a date.
 * F13-11 - a degraded result cannot appear complete.
 * F13-12 - unrecorded provenance disables persist and export.
 * F13-14 - a selected contract creates no implicit applicability.
 *
 * F13-14 carries the mutation proof here as well as in T26, because the Q&A
 * surface is the other place a contract is "selected" and could quietly become
 * a legal statement.
 *
 * The four outcomes are the point of this suite. They arrive as different
 * things — a 200 with `valid_empty`, a 200 with `degraded`, a 409 — and the UI
 * has to keep them apart. Collapsing them into "No results" is what let an
 * outage read as "the contract is silent on this".
 */

import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ContractQaPanel } from "../ContractQaPanel";
import { ContractMasterApiError } from "@/services/contract-master-v1-api";

const CAPABILITIES = { can_save: true, can_export: true, can_cite: true };

function panel(props: Partial<React.ComponentProps<typeof ContractQaPanel>> = {}) {
  const search = vi.fn().mockResolvedValue({
    results: [{ document_id: "doc-1", text: "clause text" }],
    outcome: "complete",
    degraded_sources: [],
    total_count: 1,
  });
  const utils = render(
    <ContractQaPanel
      projectId="project-7"
      contractId="contract-3"
      search={search}
      provenanceCapabilities={CAPABILITIES}
      {...props}
    />,
  );
  return { ...utils, search };
}

const ask = () => screen.getByRole("button", { name: /ask/i }) as HTMLButtonElement;
const modeSelect = () => screen.getByLabelText(/mode/i) as HTMLSelectElement;

// --------------------------------------------------------------------------- //
// F13-10 : historical requires a date
// --------------------------------------------------------------------------- //

describe("F13-10 historical mode requires a date", () => {
  it("does not preselect a mode", () => {
    panel();
    expect(modeSelect().value).toBe("");
    expect(ask().disabled).toBe(true);
  });

  it("blocks a historical question with no date", () => {
    panel();
    fireEvent.change(modeSelect(), { target: { value: "historical" } });
    expect(ask().disabled).toBe(true);
    expect(screen.getByText(/needs a date/i)).toBeTruthy();
  });

  it("never defaults the date to today", () => {
    panel();
    fireEvent.change(modeSelect(), { target: { value: "historical" } });
    const date = screen.getByLabelText(/event date/i) as HTMLInputElement;
    expect(date.value).toBe("");
  });

  it("allows a historical question once dated", async () => {
    const { search } = panel();
    fireEvent.change(modeSelect(), { target: { value: "historical" } });
    fireEvent.change(screen.getByLabelText(/event date/i), {
      target: { value: "2021-03-01" },
    });
    fireEvent.click(ask());

    await waitFor(() => expect(search).toHaveBeenCalled());
    expect(search.mock.calls[0][0]).toMatchObject({
      query_mode: "historical",
      event_date: "2021-03-01",
    });
  });

  it("sends current_state explicitly rather than omitting the mode", async () => {
    const { search } = panel();
    fireEvent.change(modeSelect(), { target: { value: "current_state" } });
    fireEvent.click(ask());
    await waitFor(() => expect(search).toHaveBeenCalled());
    expect(search.mock.calls[0][0].query_mode).toBe("current_state");
  });
});

// --------------------------------------------------------------------------- //
// F13-09 : a browse result cannot launch Q&A
// --------------------------------------------------------------------------- //

describe("F13-09 browse results cannot launch Q&A", () => {
  it("offers no browse option among the modes", () => {
    panel();
    expect(screen.queryByRole("option", { name: /browse/i })).toBeNull();
  });

  it("refuses to ask about an instrument that is not evidence-ready", () => {
    panel({ launchedFrom: { source: "browse", evidenceReady: false } });
    expect(ask().disabled).toBe(true);
    expect(screen.getByText(/catalogue|browse/i)).toBeTruthy();
  });

  it("explains that a catalogue listing is not evidence", () => {
    panel({ launchedFrom: { source: "browse", evidenceReady: false } });
    expect(screen.getByTestId("launch-blocked").textContent).toMatch(/not evidence/i);
  });

  it("never issues a search from a browse launch", () => {
    const { search } = panel({ launchedFrom: { source: "browse", evidenceReady: false } });
    fireEvent.change(modeSelect(), { target: { value: "current_state" } });
    fireEvent.click(ask());
    expect(search).not.toHaveBeenCalled();
  });
});

// --------------------------------------------------------------------------- //
// F13-11 : degraded cannot look complete
// --------------------------------------------------------------------------- //

describe("F13-11 a degraded result cannot appear complete", () => {
  const degraded = vi.fn().mockResolvedValue({
    results: [{ document_id: "doc-1" }],
    outcome: "degraded",
    degraded_sources: ["vector"],
    total_count: 1,
  });

  it("shows a warning banner above the answer", async () => {
    panel({ search: degraded });
    fireEvent.change(modeSelect(), { target: { value: "current_state" } });
    fireEvent.click(ask());

    await waitFor(() => expect(screen.getByTestId("degraded-banner")).toBeTruthy());
    expect(screen.getByTestId("degraded-banner").textContent).toMatch(/reduced evidence coverage/i);
  });

  it("names the source that failed", async () => {
    panel({ search: degraded });
    fireEvent.change(modeSelect(), { target: { value: "current_state" } });
    fireEvent.click(ask());
    await waitFor(() => expect(screen.getByTestId("degraded-banner").textContent).toMatch(/vector/));
  });

  it("still shows the answer, since T24 permits it", async () => {
    panel({ search: degraded });
    fireEvent.change(modeSelect(), { target: { value: "current_state" } });
    fireEvent.click(ask());
    await waitFor(() => expect(screen.getByTestId("results")).toBeTruthy());
  });

  it("does not render degraded as a valid empty", async () => {
    panel({ search: degraded });
    fireEvent.change(modeSelect(), { target: { value: "current_state" } });
    fireEvent.click(ask());
    await waitFor(() => expect(screen.getByTestId("degraded-banner")).toBeTruthy());
    expect(screen.queryByTestId("valid-empty")).toBeNull();
  });
});

describe("the four outcomes stay distinct", () => {
  it("renders a valid empty as an explicit complete answer", async () => {
    const search = vi.fn().mockResolvedValue({
      results: [],
      outcome: "valid_empty",
      degraded_sources: [],
      total_count: 0,
    });
    panel({ search });
    fireEvent.change(modeSelect(), { target: { value: "current_state" } });
    fireEvent.click(ask());

    await waitFor(() => expect(screen.getByTestId("valid-empty")).toBeTruthy());
    expect(screen.queryByTestId("degraded-banner")).toBeNull();
    expect(screen.queryByTestId("authority-failure")).toBeNull();
  });

  it("offers no widening recovery on an empty answer", async () => {
    const search = vi.fn().mockResolvedValue({
      results: [],
      outcome: "valid_empty",
      degraded_sources: [],
      total_count: 0,
    });
    panel({ search });
    fireEvent.change(modeSelect(), { target: { value: "current_state" } });
    fireEvent.click(ask());

    await waitFor(() => expect(screen.getByTestId("valid-empty")).toBeTruthy());
    expect(screen.queryByRole("button", { name: /search all|widen|everything/i })).toBeNull();
  });

  it("renders an authority failure as a failure, not as an empty", async () => {
    const search = vi.fn().mockRejectedValue(
      new ContractMasterApiError(409, "canonical contract eligibility could not be resolved"),
    );
    panel({ search });
    fireEvent.change(modeSelect(), { target: { value: "current_state" } });
    fireEvent.click(ask());

    await waitFor(() => expect(screen.getByTestId("authority-failure")).toBeTruthy());
    expect(screen.queryByTestId("valid-empty")).toBeNull();
  });

  it("renders an invalid request distinctly from an authority failure", async () => {
    const search = vi.fn().mockRejectedValue(
      new ContractMasterApiError(422, "historical evidence requires event_date"),
    );
    panel({ search });
    fireEvent.change(modeSelect(), { target: { value: "current_state" } });
    fireEvent.click(ask());

    await waitFor(() => expect(screen.getByTestId("invalid-request")).toBeTruthy());
    expect(screen.queryByTestId("authority-failure")).toBeNull();
  });
});

// --------------------------------------------------------------------------- //
// F13-12 : provenance gates persistence
// --------------------------------------------------------------------------- //

describe("F13-12 unrecorded provenance disables persist and export", () => {
  it("disables save, export and cite when the server records none", async () => {
    panel({ provenanceCapabilities: { can_save: false, can_export: false, can_cite: false } });
    fireEvent.change(modeSelect(), { target: { value: "current_state" } });
    fireEvent.click(ask());

    await waitFor(() => expect(screen.getByTestId("results")).toBeTruthy());
    for (const action of ["Save", "Export", "Cite"]) {
      expect((screen.getByRole("button", { name: action }) as HTMLButtonElement).disabled).toBe(true);
    }
  });

  it("says why they are disabled", async () => {
    panel({ provenanceCapabilities: { can_save: false, can_export: false, can_cite: false } });
    fireEvent.change(modeSelect(), { target: { value: "current_state" } });
    fireEvent.click(ask());
    await waitFor(() => expect(screen.getByTestId("provenance-note")).toBeTruthy());
    expect(screen.getByTestId("provenance-note").textContent).toMatch(/provenance/i);
  });

  it("enables them when the server says provenance was recorded", async () => {
    panel();
    fireEvent.change(modeSelect(), { target: { value: "current_state" } });
    fireEvent.click(ask());
    await waitFor(() => expect(screen.getByTestId("results")).toBeTruthy());
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("decides from server capability, never from having results", async () => {
    panel({ provenanceCapabilities: { can_save: false, can_export: false, can_cite: false } });
    fireEvent.change(modeSelect(), { target: { value: "current_state" } });
    fireEvent.click(ask());
    await waitFor(() => expect(screen.getByTestId("results")).toBeTruthy());
    // Results exist and the actions are still disabled.
    expect((screen.getByRole("button", { name: "Save" }) as HTMLButtonElement).disabled).toBe(true);
  });
});

// --------------------------------------------------------------------------- //
// F13-14 : no implicit applicability
// --------------------------------------------------------------------------- //

describe("F13-14 asking about a contract creates no applicability", () => {
  it("sends only the evidence query, never an applicability command", async () => {
    const { search } = panel();
    fireEvent.change(modeSelect(), { target: { value: "current_state" } });
    fireEvent.click(ask());

    await waitFor(() => expect(search).toHaveBeenCalled());
    const payload = JSON.stringify(search.mock.calls[0][0]);
    expect(payload).not.toMatch(/APPLIED/);
    expect(payload).not.toMatch(/applicability/i);
  });

  it("offers no apply-this-instrument control", () => {
    panel();
    expect(screen.queryByRole("button", { name: /apply/i })).toBeNull();
  });
});
