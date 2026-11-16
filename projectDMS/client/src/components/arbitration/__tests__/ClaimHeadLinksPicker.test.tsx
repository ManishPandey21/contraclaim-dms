import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import ClaimHeadLinksPicker from "@/components/arbitration/ClaimHeadLinksPicker";
import { type ClaimHeadLinks, EMPTY_CLAIM_HEAD_LINKS } from "@/lib/claim-head-links";
import { containsRawId } from "@/lib/plain-language";

const cases = vi.hoisted(() => ({ listMatrixRows: vi.fn() }));
const docs = vi.hoisted(() => ({ listDocuments: vi.fn(), getDocument: vi.fn() }));
vi.mock("@/services/arbitration-cases-api", () => cases);
vi.mock("@/services/documents-api", () => docs);

const NOTICE = "65f0a1b2c3d4e5f6a7b8c9e1";
const INVOICE = "65f0a1b2c3d4e5f6a7b8c9e2";

beforeEach(() => {
  vi.clearAllMocks();
  cases.listMatrixRows.mockResolvedValue([{ _id: "r1", clause_number: "8.4" }, { _id: "r2", clause_number: "2.1" }]);
  docs.listDocuments.mockResolvedValue({
    documents: [
      { _id: NOTICE, uploadType: "outgoing", letterNo: "ABC/NHAI/41", subject: "Notice of claim", date: "2026-04-11" },
      { _id: INVOICE, uploadType: "contract", filename: "Plant hire invoice.pdf" },
    ],
  });
  docs.getDocument.mockRejectedValue({ response: { status: 404 } });
});

function Harness({ onValue }: { onValue: (value: ClaimHeadLinks) => void }) {
  const [value, setValue] = useState<ClaimHeadLinks>(EMPTY_CLAIM_HEAD_LINKS);
  return (
    <ClaimHeadLinksPicker
      caseId="case-1"
      projectId="proj-A1"
      value={value}
      onChange={(next) => {
        setValue(next);
        onValue(next);
      }}
    />
  );
}

describe("ClaimHeadLinksPicker", () => {
  it("chooses the clause, the notice letter and the evidence without typing an id", async () => {
    const onValue = vi.fn();
    const { container } = render(<Harness onValue={onValue} />);

    // Clauses come from the case's own clause matrix, in clause order.
    const clauseButtons = await screen.findAllByRole("button", { name: /^Clause / });
    expect(clauseButtons.map((button) => button.textContent)).toEqual(["Clause 2.1", "Clause 8.4"]);
    await userEvent.click(screen.getByRole("button", { name: "Clause 8.4" }));
    await userEvent.type(screen.getByLabelText("Add a clause by number"), "20.1{Enter}");

    await userEvent.type(screen.getByLabelText("Search: Notice given"), "{Enter}");
    const notices = await screen.findByRole("list", { name: "Results: Notice given" });
    // Only letters are offered as the notice.
    expect(within(notices).getAllByRole("listitem")).toHaveLength(1);
    await userEvent.click(within(notices).getByRole("button", { name: "Choose" }));

    await userEvent.type(screen.getByLabelText("Search: Records proving the amount or days"), "{Enter}");
    const records = await screen.findByRole("list", { name: "Results: Records proving the amount or days" });
    await userEvent.click(within(records).getAllByRole("button", { name: "Choose" })[1]);

    expect(onValue).toHaveBeenLastCalledWith({ clause_ids: ["8.4", "20.1"], notice_ids: [NOTICE], evidence_ids: [INVOICE] });
    expect(cases.listMatrixRows).toHaveBeenCalledWith("case-1", "clause-matrix");
    expect(containsRawId(container.textContent || "")).toBe(false);
  });
});
