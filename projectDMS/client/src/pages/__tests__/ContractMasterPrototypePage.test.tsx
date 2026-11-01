import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import ContractMasterPrototypePage from "@/pages/ContractMasterPrototypePage";

describe("Contract Master prototype renders all five surfaces", () => {
  it("renders every surface heading", () => {
    render(<ContractMasterPrototypePage />);
    for (const heading of [
      /1 · Upload scope/,
      /2 · Organisation catalogue/,
      /3 · Instrument viewer/,
      /4 · Contract Q&A/,
      /5 · Migration review queue/,
    ]) {
      expect(screen.getByText(heading)).toBeTruthy();
    }
  });

  it("uses the frozen state vocabulary and never the word Active", () => {
    const { container } = render(<ContractMasterPrototypePage />);
    expect(screen.getAllByText("CATALOGUED / UNASSIGNED").length).toBeGreaterThan(0);
    expect(screen.getAllByText("APPLICABLE - EVIDENCE READY").length).toBeGreaterThan(0);
    expect(screen.getAllByText("APPLICABLE - CONTENT BLOCKED").length).toBeGreaterThan(0);
    expect(screen.getAllByText("SUPERSEDED / WITHDRAWN").length).toBeGreaterThan(0);
    expect(container.textContent).not.toMatch(/\bActive\b/);
  });

  it("blocks upload until a scope is chosen", () => {
    render(<ContractMasterPrototypePage />);
    const upload = screen.getByRole("button", { name: "Upload" }) as HTMLButtonElement;
    expect(upload.disabled).toBe(true);
  });

  it("disables save, export and cite when provenance is unrecorded", () => {
    render(<ContractMasterPrototypePage />);
    for (const action of ["Save", "Export", "Cite"]) {
      expect((screen.getByRole("button", { name: action }) as HTMLButtonElement).disabled).toBe(true);
    }
  });

  it("blocks promotion for a candidate with open axes", () => {
    render(<ContractMasterPrototypePage />);
    const buttons = screen.getAllByRole("button", { name: "Promote this candidate" });
    expect(buttons.filter((b) => (b as HTMLButtonElement).disabled).length).toBe(3);
    expect(buttons.filter((b) => !(b as HTMLButtonElement).disabled).length).toBe(1);
  });
});
