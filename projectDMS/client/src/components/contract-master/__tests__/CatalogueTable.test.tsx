/**
 * T27 - Organisation catalogue surface.
 *
 * F13-08 - an unassigned organisation item displays as catalogued, not active
 *          evidence.
 *
 * The failure this guards against is a presentation one with legal
 * consequences: an organisation-owned instrument that applies to no contract is
 * a perfectly healthy catalogue entry, and rendering it with success styling
 * tells a user it is usable as evidence. It is not. Catalogue membership and
 * evidence eligibility are different questions, and the table has to keep them
 * visibly apart.
 *
 * UI-01: seven orthogonal dimensions stay visible on desktop. There is no
 * single collapsed status column, and the word "Active" appears nowhere.
 */

import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CatalogueTable } from "../CatalogueTable";
import type { CatalogueItem } from "@/services/contract-master-v1-api";

const UNASSIGNED: CatalogueItem = {
  contract_document_id: "cd-gcc",
  document_id: "doc-gcc",
  contract_document_type: "general_conditions",
  scope_level: "organization",
  classification_revision: 3,
  projection_status: "CURRENT",
  applicability_count: 0,
  content_consumable: true,
  evidence_ready: false,
  viewer_state: "CATALOGUED / UNASSIGNED",
};

const READY: CatalogueItem = {
  ...UNASSIGNED,
  contract_document_id: "cd-boq",
  document_id: "doc-boq",
  contract_document_type: "boq",
  scope_level: "project",
  applicability_count: 2,
  evidence_ready: true,
  viewer_state: "APPLICABLE - EVIDENCE READY",
};

const BLOCKED: CatalogueItem = {
  ...UNASSIGNED,
  contract_document_id: "cd-spec",
  document_id: "doc-spec",
  applicability_count: 1,
  content_consumable: false,
  evidence_ready: false,
  viewer_state: "APPLICABLE - CONTENT BLOCKED",
};

const PENDING: CatalogueItem = {
  ...UNASSIGNED,
  contract_document_id: "cd-pcc",
  document_id: "doc-pcc",
  applicability_count: 1,
  projection_status: "PENDING",
  evidence_ready: false,
  viewer_state: "APPLICABLE - PROJECTION PENDING",
};

const ALL = [UNASSIGNED, PENDING, READY, BLOCKED];

const rowFor = (id: string) => screen.getByTestId(`catalogue-row-${id}`);

// --------------------------------------------------------------------------- //
// F13-08
// --------------------------------------------------------------------------- //

describe("F13-08 an unassigned organisation item is catalogued, not evidence", () => {
  it("renders it with the catalogued state", () => {
    render(<CatalogueTable items={ALL} />);
    expect(within(rowFor("cd-gcc")).getByText("CATALOGUED / UNASSIGNED")).toBeTruthy();
  });

  it("does not give it success styling", () => {
    render(<CatalogueTable items={ALL} />);
    const badge = within(rowFor("cd-gcc")).getByTestId("state-badge");
    expect(badge.className).not.toMatch(/green/);
  });

  it("gives success styling only to evidence-ready", () => {
    render(<CatalogueTable items={ALL} />);
    expect(within(rowFor("cd-boq")).getByTestId("state-badge").className).toMatch(/green/);
    for (const id of ["cd-gcc", "cd-pcc", "cd-spec"]) {
      expect(within(rowFor(id)).getByTestId("state-badge").className).not.toMatch(/green/);
    }
  });

  it("shows that it applies to no contract at all", () => {
    render(<CatalogueTable items={ALL} />);
    expect(within(rowFor("cd-gcc")).getByText(/^0$/)).toBeTruthy();
  });

  it("marks it not evidence-ready", () => {
    render(<CatalogueTable items={ALL} />);
    expect(within(rowFor("cd-gcc")).getByTestId("evidence-ready").textContent).toMatch(/no/i);
  });
});

// --------------------------------------------------------------------------- //
// UI-01 : seven dimensions, no collapsed status
// --------------------------------------------------------------------------- //

describe("UI-01 the seven dimensions stay visible", () => {
  it("renders every dimension as its own column", () => {
    render(<CatalogueTable items={ALL} />);
    for (const header of [
      "Instrument",
      "Type",
      "Scope",
      "Applicability",
      "Projection",
      "Content",
      "State",
    ]) {
      expect(screen.getByRole("columnheader", { name: header })).toBeTruthy();
    }
  });

  it("never renders the word Active", () => {
    const { container } = render(<CatalogueTable items={ALL} />);
    expect(container.textContent).not.toMatch(/\bActive\b/);
  });

  it("has no single collapsed status column", () => {
    render(<CatalogueTable items={ALL} />);
    expect(screen.queryByRole("columnheader", { name: /^status$/i })).toBeNull();
  });

  it("keeps projection distinct from content authority", () => {
    render(<CatalogueTable items={ALL} />);
    const row = within(rowFor("cd-spec"));
    // Applicable, projection current, content blocked - three separate facts.
    expect(row.getByTestId("projection").textContent).toMatch(/CURRENT/);
    expect(row.getByTestId("content").textContent).toMatch(/blocked/i);
    expect(row.getByText("APPLICABLE - CONTENT BLOCKED")).toBeTruthy();
  });

  it("shows the classification revision alongside the type", () => {
    render(<CatalogueTable items={ALL} />);
    expect(within(rowFor("cd-gcc")).getByTestId("type").textContent).toMatch(/rev 3/);
  });
});

// --------------------------------------------------------------------------- //
// the surface itself
// --------------------------------------------------------------------------- //

describe("the catalogue surface", () => {
  it("renders an empty catalogue as empty rather than as an error", () => {
    render(<CatalogueTable items={[]} />);
    expect(screen.getByText(/no instruments/i)).toBeTruthy();
  });

  it("links each instrument to its own detail route", () => {
    render(<CatalogueTable items={ALL} onOpen={() => {}} />);
    expect(
      within(rowFor("cd-boq")).getByRole("button", { name: /cd-boq/ }),
    ).toBeTruthy();
  });
});
