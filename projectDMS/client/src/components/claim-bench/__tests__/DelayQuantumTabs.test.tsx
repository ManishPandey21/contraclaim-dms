import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import DelayTab from "@/components/claim-bench/DelayTab";
import QuantumTab from "@/components/claim-bench/QuantumTab";
import type { DelayEventView, DelayView, QuantumView } from "@/types/claimBench";

const api = vi.hoisted(() => ({
  fetchHeadDelay: vi.fn(),
  fetchHeadQuantum: vi.fn(),
  fetchDelayCandidates: vi.fn(),
  addHeadLink: vi.fn(),
  removeHeadLink: vi.fn(),
  setDelaySettings: vi.fn(),
  decideDelayEvent: vi.fn(),
  decideDelayOverlap: vi.fn(),
  saveQuantumLine: vi.fn(),
  removeQuantumLine: vi.fn(),
}));
vi.mock("@/services/claim-bench-api", () => api);
vi.mock("@/services/document-relationships-api", () => ({
  listEntityDocumentLinks: vi.fn().mockResolvedValue([
    { _id: "l1", document_id: "doc-1", document: { _id: "doc-1", subject: "Plant log March 2026" } },
  ]),
}));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const event = (over: Partial<DelayEventView>): DelayEventView => ({
  event_id: "E1",
  link_id: "link-E1",
  label: "Hindrance HR-E1",
  title: "Site not handed over",
  start_date: "2026-03-01T00:00:00",
  end_date: "2026-03-31T00:00:00",
  open_ended: false,
  register_responsibility: "employer",
  register_critical: true,
  revision_current: true,
  counted: true,
  days: 31,
  decision: {
    risk_owner: "employer",
    criticality: "critical",
    proposed_by: "register",
    proposed_reason: "The hindrance register records responsibility as employer.",
  },
  ...over,
});

const delay = (over: Partial<DelayView> = {}): DelayView => ({
  head_id: "H1",
  method: null,
  as_of_date: null,
  unavailable_methods: {
    time_impact_analysis: "Needs a programme network (activities and logic). Only milestones are held.",
  },
  include_neutral: false,
  events: [
    event({}),
    event({
      event_id: "C1",
      link_id: "link-C1",
      label: "Hindrance HR-C1",
      title: "Late mobilisation",
      start_date: "2026-03-20T00:00:00",
      end_date: "2026-04-10T00:00:00",
      days: 22,
      decision: { risk_owner: "contractor", criticality: "critical" },
    }),
  ],
  overlaps: [
    {
      overlap_id: "2026-03-20_2026-03-31",
      start_date: "2026-03-20T00:00:00",
      end_date: "2026-03-31T00:00:00",
      days: 12,
      employer_event_ids: ["E1"],
      contractor_event_ids: ["C1"],
    },
  ],
  records: [],
  employer_critical_days: 31,
  contractor_critical_days: 22,
  neutral_critical_days: 0,
  contractor_only_days: 10,
  net: {
    low: 19,
    high: 31,
    days: null,
    settled: false,
    previously_granted: 0,
    employer_only_days: 19,
    overlap_days_to_eot: 0,
    neutral_days_included: 0,
  },
  compensable_periods: [{ start_date: "2026-03-01T00:00:00", end_date: "2026-03-19T00:00:00", days: 19 }],
  blockers: [
    "H1: choose the delay analysis method",
    "H1: 1 overlap between Employer and Contractor delay need a decided treatment",
  ],
  stale_links: 0,
  ...over,
});

const quantum = (over: Partial<QuantumView> = {}): QuantumView => ({
  head_id: "H1",
  currency: "INR",
  lines: [
    {
      line: {
        line_id: "Q1",
        category: "prolongation",
        description: "Site overheads",
        amount: 4500000,
        period_start: "2026-03-15T00:00:00",
        period_end: "2026-03-25T00:00:00",
        formula: "none",
        records: [{ document_id: "doc-1", role: "support" }],
      },
      locked: true,
      lock_reasons: [
        "6 of the 11 days in this period are not in a period decided as compensable in the delay assessment (Employer critical delay, not concurrent or decided as carrying cost).",
      ],
    },
    {
      line: {
        line_id: "Q2",
        category: "direct",
        description: "Idle plant",
        amount: 500000,
        formula: "none",
        records: [{ document_id: "doc-1", role: "support" }],
      },
      locked: false,
      lock_reasons: [],
    },
  ],
  claimed_total: 5000000,
  supported_total: 500000,
  locked_total: 4500000,
  locked_count: 1,
  ...over,
});

function mount(node: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

describe("Delay tab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.fetchHeadDelay.mockResolvedValue(delay());
    api.fetchHeadQuantum.mockResolvedValue(quantum());
  });

  it("shows the extension as a range, and says why, while an overlap is undecided", async () => {
    mount(<DelayTab claimId="c1" headId="H1" boardVersion={4} readOnly={false} />);
    const net = (
      await screen.findByRole("heading", { name: "Extension of time on the decisions so far" })
    ).closest("section")!;
    expect(within(net).getByText("19 to 31 days")).toBeInTheDocument();
    expect(within(net).getByText(/Shown as a range because an overlap has no decided treatment/)).toBeInTheDocument();
    expect(within(net).getByText("choose the delay analysis method")).toBeInTheDocument();
    expect(screen.getByText("Treatment not decided")).toBeInTheDocument();
  });

  it("will not record a treatment without a basis, then sends it with the board version", async () => {
    api.decideDelayOverlap.mockResolvedValue(delay());
    mount(<DelayTab claimId="c1" headId="H1" boardVersion={4} readOnly={false} />);
    const record = await screen.findByRole("button", { name: "Record treatment" });
    await userEvent.selectOptions(screen.getByLabelText("Treatment"), "eot_granted_no_cost");
    expect(record).toBeDisabled();
    await userEvent.type(screen.getByLabelText(/^Basis/), "Sub-Clause 8.4, time without cost.");
    expect(record).toBeEnabled();
    await userEvent.click(record);
    await waitFor(() =>
      expect(api.decideDelayOverlap).toHaveBeenCalledWith("c1", "H1", "2026-03-20_2026-03-31", 4, {
        treatment: "eot_granted_no_cost",
        basis: "Sub-Clause 8.4, time without cost.",
        days_to_eot: undefined,
      }),
    );
  });

  it("asks for the days of an apportioned overlap and bounds them", async () => {
    mount(<DelayTab claimId="c1" headId="H1" boardVersion={4} readOnly={false} />);
    const record = await screen.findByRole("button", { name: "Record treatment" });
    await userEvent.selectOptions(screen.getByLabelText("Treatment"), "apportioned");
    await userEvent.type(screen.getByLabelText(/^Basis/), "Apportioned on dominant cause.");
    expect(record).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Days counted (0 to 12)"), "13");
    expect(record).toBeDisabled();
    await userEvent.clear(screen.getByLabelText("Days counted (0 to 12)"));
    await userEvent.type(screen.getByLabelText("Days counted (0 to 12)"), "5");
    expect(record).toBeEnabled();
  });

  it("flags a hindrance that changed after linking and offers the latest version", async () => {
    api.fetchHeadDelay.mockResolvedValue(
      delay({ events: [event({ revision_current: false })], overlaps: [], stale_links: 1 }),
    );
    api.removeHeadLink.mockResolvedValue(delay());
    mount(<DelayTab claimId="c1" headId="H1" boardVersion={4} readOnly={false} />);
    expect(await screen.findByText(/has changed since it was linked/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Use latest/ }));
    await waitFor(() => expect(api.removeHeadLink).toHaveBeenCalledWith("c1", "H1", "link-E1", 4, true));
  });

  it("names the methods it cannot offer and sends a risk-owner decision", async () => {
    api.decideDelayEvent.mockResolvedValue(delay());
    mount(<DelayTab claimId="c1" headId="H1" boardVersion={4} readOnly={false} />);
    expect(await screen.findByText(/Time impact analysis \(Needs a programme network/)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getAllByLabelText("Whose risk")[0], "neutral");
    await waitFor(() =>
      expect(api.decideDelayEvent).toHaveBeenCalledWith("c1", "H1", "E1", 4, { risk_owner: "neutral" }),
    );
  });

  it("disables every control when the board is read-only", async () => {
    mount(<DelayTab claimId="c1" headId="H1" boardVersion={4} readOnly />);
    expect(await screen.findByLabelText("Delay analysis method")).toBeDisabled();
    expect(screen.getAllByLabelText("Whose risk")[0]).toBeDisabled();
    expect(screen.getByRole("button", { name: "Link a record" })).toBeDisabled();
  });
});

describe("Quantum tab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.fetchHeadDelay.mockResolvedValue(delay());
    api.fetchHeadQuantum.mockResolvedValue(quantum());
  });

  it("separates claimed, supported and locked, and shows what would unlock a line", async () => {
    mount(<QuantumTab claimId="c1" headId="H1" boardVersion={4} readOnly={false} />);
    const totals = (await screen.findByRole("heading", { name: "Build-up of this head" })).closest("section")!;
    expect(within(totals).getByText("₹50,00,000")).toBeInTheDocument();
    expect(within(totals).getByText("₹5,00,000")).toBeInTheDocument();
    expect(within(totals).getByText("₹45,00,000")).toBeInTheDocument();
    expect(
      screen.getByText(/6 of the 11 days in this period are not in a period decided as compensable/),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Locked")).toBeInTheDocument();
    expect(screen.getByLabelText("Supported")).toBeInTheDocument();
  });

  it("shows amounts from paise and marks a formula line as a calculation, not a finding (4B-24)", async () => {
    const note = "Calculated from the figures entered. A calculation is not a finding that this amount is recoverable.";
    api.fetchHeadQuantum.mockResolvedValue(
      quantum({
        lines: [
          {
            line: {
              line_id: "Q1", category: "overhead_formula", description: "Head-office overhead", amount: 41095.89,
              amount_paise: 4109589, formula: "hudson", records: [{ document_id: "doc-1", role: "actual_loss" }],
            },
            locked: false, lock_reasons: [], calculated: true, calculation_note: note,
          },
        ],
        // The float a server would derive is deliberately off by a paisa: the paise figure is shown.
        claimed_total: 41095.88, supported_total: 41095.88, locked_total: 0, locked_count: 0,
        claimed_total_paise: 4109589, supported_total_paise: 4109589, locked_total_paise: 0, calculated_total_paise: 4109589,
      })
    );
    mount(<QuantumTab claimId="c1" headId="H1" boardVersion={4} readOnly={false} />);
    const totals = (await screen.findByRole("heading", { name: "Build-up of this head" })).closest("section")!;
    expect(within(totals).getAllByText("₹41,095.89")).toHaveLength(4); // claimed and supported, each with its short form
    expect(within(totals).queryByText("₹41,095.88")).not.toBeInTheDocument();
    expect(within(totals).getByText(/is calculated by a formula. A calculation is not a finding/)).toBeInTheDocument();
    expect(screen.getByText(note)).toBeInTheDocument();
  });

  it("adds a line citing a document of the claim, against the board version", async () => {
    api.saveQuantumLine.mockResolvedValue(quantum());
    mount(<QuantumTab claimId="c1" headId="H1" boardVersion={4} readOnly={false} />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a line" }));
    const add = screen.getByRole("button", { name: "Add line" });
    expect(add).toBeDisabled();
    await userEvent.type(screen.getByLabelText("Description"), "Standby labour");
    await userEvent.type(screen.getByLabelText("Amount"), "1,20,000");
    await userEvent.selectOptions(await screen.findByLabelText("How Plant log March 2026 is used"), "support");
    await userEvent.click(add);
    await waitFor(() =>
      expect(api.saveQuantumLine).toHaveBeenCalledWith(
        "c1",
        "H1",
        4,
        {
          category: "direct",
          description: "Standby labour",
          amount: 120000,
          period_start: null,
          period_end: null,
          formula: "none",
          formula_inputs: {},
          basis: null,
          records: [{ document_id: "doc-1", role: "support" }],
        },
        undefined,
      ),
    );
  });

  it("never asks for a formula line's amount: it sends the figures, each with its record (4B-24)", async () => {
    api.saveQuantumLine.mockResolvedValue(quantum());
    mount(<QuantumTab claimId="c1" headId="H1" boardVersion={4} readOnly={false} />);
    await userEvent.click(await screen.findByRole("button", { name: "Add a line" }));
    await userEvent.selectOptions(screen.getByLabelText("Kind of cost"), "overhead_formula");
    await userEvent.selectOptions(screen.getByLabelText("Formula"), "hudson");
    await userEvent.type(screen.getByLabelText("Description"), "Head-office overhead");
    expect(screen.queryByRole("textbox", { name: "Amount" })).not.toBeInTheDocument();
    expect(screen.getByText("Worked out from the formula's figures when the line is saved.")).toBeInTheDocument();

    const add = screen.getByRole("button", { name: "Add line" });
    await userEvent.type(screen.getByLabelText("Head-office overhead and profit in the tender (%)"), "5");
    await userEvent.type(screen.getByLabelText("Contract sum"), "1,00,00,000");
    expect(add).toBeDisabled();
    expect(screen.getByText("Still needed: Contract period (days).")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Contract period (days)"), "365");
    const [firstRecord] = await screen.findAllByLabelText("Taken from");
    await userEvent.selectOptions(firstRecord, "doc-1");
    await userEvent.click(add);
    await waitFor(() => expect(api.saveQuantumLine).toHaveBeenCalled());
    const sent = api.saveQuantumLine.mock.calls[0][3];
    expect(sent).not.toHaveProperty("amount");
    expect(sent.formula).toBe("hudson");
    expect(sent.formula_inputs).toEqual({
      tender_percentage: { value: "5", document_id: "doc-1" },
      contract_sum: { value: "10000000", document_id: null },
      contract_period_days: { value: "365", document_id: null },
    });
  });
});
