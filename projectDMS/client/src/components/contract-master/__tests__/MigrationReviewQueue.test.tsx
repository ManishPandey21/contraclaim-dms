/**
 * T30 — migration review queue.
 *
 * The operator-flow gates for the M09 series, at the surface where an operator
 * actually acts: per-axis blocking reasons, a suggestion that says it is one,
 * and no control that promotes anything in bulk.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { MigrationReviewQueue } from "../MigrationReviewQueue";
import type { ReconciliationCandidate } from "@/services/contract-master-v1-api";

const CANDIDATES: ReconciliationCandidate[] = [
  {
    candidate_id: "c1",
    canonical_document_id: "doc-101",
    scope_state: "AMBIGUOUS",
    type_state: "TYPE_SUGGESTED",
    scope_hint:
      "the creation audit retained a null project, which records only that no project was in the session",
    promotion_blocked_reasons: [
      "scope: AMBIGUOUS - operator adjudication required",
      "type: TYPE_SUGGESTED - operator confirmation required",
    ],
  },
  {
    candidate_id: "c2",
    canonical_document_id: "doc-102",
    scope_state: "PROJECT_SCOPE_CONFIRMED",
    type_state: "TYPE_UNKNOWN",
    scope_hint: null,
    promotion_blocked_reasons: ["type: TYPE_UNKNOWN - operator confirmation required"],
  },
  {
    candidate_id: "c3",
    canonical_document_id: "doc-103",
    scope_state: "ORG_SCOPE_CONFIRMED",
    type_state: "TYPE_RESOLVED",
    scope_hint: null,
    adjudicated_by: "alice",
    promotion_blocked_reasons: [],
  },
  {
    candidate_id: "c4",
    canonical_document_id: "doc-104",
    scope_state: "INVALID",
    type_state: "TYPE_UNKNOWN",
    scope_hint: "the referenced project belongs to a different organisation",
    promotion_blocked_reasons: [
      "scope: INVALID (terminal)",
      "type: TYPE_UNKNOWN - operator confirmation required",
    ],
  },
];

describe("blocking reasons are shown per axis", () => {
  it("lists each axis separately rather than one needs-review chip", () => {
    render(<MigrationReviewQueue candidates={CANDIDATES} />);
    expect(screen.getByText(/scope: AMBIGUOUS - operator adjudication required/)).toBeTruthy();
    expect(screen.getByText(/type: TYPE_SUGGESTED - operator confirmation required/)).toBeTruthy();
  });

  it("shows scope and type as independent columns", () => {
    render(<MigrationReviewQueue candidates={CANDIDATES} />);
    const scopes = screen.getAllByTestId("scope-state").map((n) => n.textContent);
    const types = screen.getAllByTestId("type-state").map((n) => n.textContent ?? "");
    // A confirmed scope with an unknown type is a real, displayable combination.
    expect(scopes).toContain("PROJECT_SCOPE_CONFIRMED");
    expect(types.some((t) => t.startsWith("TYPE_UNKNOWN"))).toBe(true);
  });

  it("surfaces the scope hint in the server's words", () => {
    render(<MigrationReviewQueue candidates={CANDIDATES} />);
    expect(screen.getAllByTestId("scope-hint")[0].textContent).toMatch(
      /no project was in the session/i,
    );
  });

  it("marks a terminal INVALID candidate as terminal", () => {
    render(<MigrationReviewQueue candidates={CANDIDATES} />);
    expect(screen.getByText(/scope: INVALID \(terminal\)/)).toBeTruthy();
  });
});

describe("a suggestion never reads as a classification", () => {
  it("labels a suggested type as unconfirmed", () => {
    render(<MigrationReviewQueue candidates={CANDIDATES} />);
    expect(screen.getByTestId("suggestion-marker").textContent).toMatch(/not confirmed/i);
  });

  it("does not label a resolved type as a suggestion", () => {
    render(<MigrationReviewQueue candidates={[CANDIDATES[2]]} />);
    expect(screen.queryByTestId("suggestion-marker")).toBeNull();
  });
});

describe("promotion is by explicit selection only", () => {
  it("disables promotion for every blocked candidate", () => {
    render(<MigrationReviewQueue candidates={CANDIDATES} />);
    const buttons = screen.getAllByTestId("promote-candidate") as HTMLButtonElement[];
    expect(buttons.filter((b) => b.disabled)).toHaveLength(3);
    expect(buttons.filter((b) => !b.disabled)).toHaveLength(1);
  });

  it("promotes exactly the candidate whose button was pressed", () => {
    const onPromote = vi.fn();
    render(<MigrationReviewQueue candidates={CANDIDATES} onPromote={onPromote} />);
    const enabled = (screen.getAllByTestId("promote-candidate") as HTMLButtonElement[]).find(
      (b) => !b.disabled,
    )!;
    fireEvent.click(enabled);
    expect(onPromote).toHaveBeenCalledTimes(1);
    expect(onPromote).toHaveBeenCalledWith("c3");
  });

  it("offers no bulk promotion control of any kind", () => {
    render(<MigrationReviewQueue candidates={CANDIDATES} />);
    const labels = screen.getAllByRole("button").map((b) => (b.textContent ?? "").toLowerCase());
    for (const forbidden of ["promote all", "approve all", "auto", "resolve all", "migrate all"]) {
      expect(labels.some((label) => label.includes(forbidden))).toBe(false);
    }
  });

  it("never describes promotion as making something searchable", () => {
    const { container } = render(<MigrationReviewQueue candidates={CANDIDATES} />);
    expect(container.textContent).not.toMatch(/searchable/i);
  });
});

describe("UI-04 filtering rather than forced grouping", () => {
  it("defaults to a flat list of everything", () => {
    render(<MigrationReviewQueue candidates={CANDIDATES} />);
    expect(screen.getAllByTestId("migration-row")).toHaveLength(4);
  });

  it("filters to candidates blocked on a given axis", () => {
    render(<MigrationReviewQueue candidates={CANDIDATES} />);
    fireEvent.change(screen.getByLabelText("Filter"), { target: { value: "type" } });
    expect(screen.getAllByTestId("migration-row")).toHaveLength(3);
  });

  it("filters to promotable candidates", () => {
    render(<MigrationReviewQueue candidates={CANDIDATES} />);
    fireEvent.change(screen.getByLabelText("Filter"), { target: { value: "promotable" } });
    expect(screen.getAllByTestId("migration-row")).toHaveLength(1);
  });
});
