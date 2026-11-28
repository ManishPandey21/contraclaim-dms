import React from "react";
import { MemoryRouter } from "react-router-dom";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { useLetterBenchStore } from "@/stores/useLetterBenchStore";
import type { GroundingStatement } from "@/types/letterBench";
import { GroundingPanel } from "../GroundingPanel";
import { unresolvedStatements } from "../benchLogic";
import { ReplyComposer } from "../ReplyComposer";
import { assertion, paragraph } from "./fixtures";

beforeEach(() => useLetterBenchStore.getState().reset());

const INVENTED = "The contract documents and site records do not substantiate the delay as claimed.";

function statement(overrides: Partial<GroundingStatement> = {}): GroundingStatement {
  return {
    text: INVENTED,
    classification: "verified_fact",
    status: "unsupported",
    material: true,
    requires_confirmation: true,
    ...overrides,
  };
}

describe("GroundingPanel", () => {
  it("says approval is held while a material statement is unsupported", () => {
    render(
      <MemoryRouter>
        <GroundingPanel grounding={[statement(), statement({ text: "Sub-Clause 20.2 requires notice within 28 days.", status: "supported", document_id: "doc-1", source_label: "Sub-Clause 20.2", passage: "not later than 28 days" })]} />
      </MemoryRouter>
    );
    expect(screen.getByText(/cannot be approved until they are resolved/)).toBeInTheDocument();
    expect(screen.getAllByTestId("grounding-statement")[0]).toHaveTextContent("Unsupported");
    expect(screen.getByRole("link", { name: "Sub-Clause 20.2" })).toHaveAttribute("href", "/documentviewer/doc-1");
  });

  it("does not count placeholders or non-material wording as unresolved facts", () => {
    expect(
      unresolvedStatements([
        statement({ classification: "requires_confirmation" }),
        statement({ material: false, status: "not_applicable" }),
        statement({ status: "user_confirmed", confirmation_basis: "IR-22" }),
      ])
    ).toHaveLength(0);
  });
});

describe("ReplyComposer confirmation", () => {
  const assertions = [assertion({ verdict: "none" })];
  const paragraphs = [paragraph({ text: `Not accepted. ${INVENTED}` })];

  it("flags the paragraph that holds an unsupported statement and offers a re-run", () => {
    render(<ReplyComposer paragraphs={paragraphs} assertions={assertions} grounding={[statement()]} readOnly={false} composing={false} onSave={vi.fn()} onRerun={vi.fn()} />);
    expect(screen.getByTestId("paragraph-unsupported-P-A1")).toHaveTextContent("1 unsupported statement");
    expect(screen.getByRole("button", { name: "Re-run" })).toBeInTheDocument();
  });

  it("saves the paragraph with the basis the reviewer records", () => {
    const onSave = vi.fn();
    render(<ReplyComposer paragraphs={paragraphs} assertions={assertions} grounding={[statement()]} readOnly={false} composing={false} onSave={onSave} onRerun={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: /Confirm the facts in this paragraph/ }));
    const confirm = screen.getByRole("button", { name: "Confirm" });
    expect(confirm).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Basis of confirmation"), { target: { value: "Site diary Oct 2026" } });
    fireEvent.click(confirm);
    expect(onSave).toHaveBeenCalledWith("P-A1", `Not accepted. ${INVENTED}`, "Site diary Oct 2026");
  });

  it("offers no confirmation when the reply is read-only", () => {
    render(<ReplyComposer paragraphs={paragraphs} assertions={assertions} grounding={[statement()]} readOnly composing={false} onSave={vi.fn()} onRerun={vi.fn()} />);
    expect(screen.queryByRole("button", { name: /Confirm the facts/ })).toBeNull();
  });
});
