/**
 * T28 - Contract viewer: five states and derived evidence readiness.
 *
 * F13-04  - suggested and resolved classification differ visibly.
 * F13-05/06 - projection pending and failed both disable evidence use.
 * F13-07  - promotion does not imply evidence-ready.
 * F13-13  - post-promotion scope has no generic edit control.
 * F13-15  - content-blocked and applicable render as two dimensions.
 * F13-16  - the same contract_id across projects stays isolated.
 * F13-17  - evidence readiness derives from current backend state.
 *
 * F13-07 and F13-17 carry mutation proofs, and they are the same hazard seen
 * from two sides: a cached readiness flag passes every static test in this file
 * because nothing in a single render disagrees with it. The mutations introduce
 * the cache, and the tests that change authority mid-session are the ones that
 * must catch it.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { InstrumentStatePanel } from "../InstrumentStatePanel";
import type { InstrumentDetail } from "@/services/contract-master-v1-api";

const BASE: InstrumentDetail = {
  contract_document_id: "cd-1",
  document_id: "doc-1",
  document_version_id: "doc-1-v2",
  contract_document_type: "general_conditions",
  scope_level: "project",
  project_id: "project-7",
  classification_revision: 2,
  projection_status: "CURRENT",
  projection_revision: 2,
  applicability_count: 1,
  content_consumable: true,
  evidence_ready: true,
  viewer_state: "APPLICABLE - EVIDENCE READY",
};

function panel(overrides: Partial<InstrumentDetail> = {}, props = {}) {
  const fetchInstrument = vi.fn().mockResolvedValue({ ...BASE, ...overrides });
  const utils = render(
    <InstrumentStatePanel
      contractDocumentId="cd-1"
      projectId="project-7"
      fetchInstrument={fetchInstrument}
      {...props}
    />,
  );
  return { ...utils, fetchInstrument };
}

// --------------------------------------------------------------------------- //
// F13-15 : content-blocked and applicable are two dimensions
// --------------------------------------------------------------------------- //

describe("F13-15 content-blocked and applicable are separate facts", () => {
  it("shows both at once", async () => {
    panel({
      content_consumable: false,
      evidence_ready: false,
      viewer_state: "APPLICABLE - CONTENT BLOCKED",
    });

    await waitFor(() => expect(screen.getByTestId("dimension-applicability")).toBeTruthy());
    expect(screen.getByTestId("dimension-applicability").textContent).toMatch(/1 contract/);
    expect(screen.getByTestId("dimension-content").textContent).toMatch(/unavailable/i);
  });

  it("does not drop the instrument from view because its content is blocked", async () => {
    panel({ content_consumable: false, viewer_state: "APPLICABLE - CONTENT BLOCKED" });
    await waitFor(() => expect(screen.getByText("APPLICABLE - CONTENT BLOCKED")).toBeTruthy());
  });

  it("renders six dimensions separately, never one status", async () => {
    panel();
    await waitFor(() => expect(screen.getByTestId("dimension-scope")).toBeTruthy());
    for (const dimension of [
      "scope",
      "classification",
      "applicability",
      "projection",
      "content",
      "readiness",
    ]) {
      expect(screen.getByTestId(`dimension-${dimension}`)).toBeTruthy();
    }
  });

  it("never renders the word Active", async () => {
    const { container } = panel();
    await waitFor(() => expect(screen.getByTestId("dimension-scope")).toBeTruthy());
    expect(container.textContent).not.toMatch(/\bActive\b/);
  });
});

// --------------------------------------------------------------------------- //
// F13-05 / F13-06 : pending and failed both disable evidence use
// --------------------------------------------------------------------------- //

describe("F13-05/06 projection state disables evidence use", () => {
  it("disables evidence use while the projection is pending", async () => {
    panel({
      projection_status: "PENDING",
      projection_revision: 1,
      evidence_ready: false,
      viewer_state: "APPLICABLE - PROJECTION PENDING",
    });
    await waitFor(() =>
      expect((screen.getByTestId("use-as-evidence") as HTMLButtonElement).disabled).toBe(true),
    );
  });

  it("disables evidence use when the projection failed", async () => {
    panel({
      projection_status: "FAILED",
      evidence_ready: false,
      viewer_state: "APPLICABLE - PROJECTION PENDING",
    });
    await waitFor(() =>
      expect((screen.getByTestId("use-as-evidence") as HTMLButtonElement).disabled).toBe(true),
    );
  });

  it("offers retry on failure and never a fallback to the previous generation", async () => {
    panel({ projection_status: "FAILED", evidence_ready: false });
    await waitFor(() => expect(screen.getByRole("button", { name: /retry/i })).toBeTruthy());
    expect(screen.queryByText(/previous generation/i)).toBeNull();
    expect(screen.queryByRole("button", { name: /use previous/i })).toBeNull();
  });

  it("enables evidence use only when the server says ready", async () => {
    panel();
    await waitFor(() =>
      expect((screen.getByTestId("use-as-evidence") as HTMLButtonElement).disabled).toBe(false),
    );
  });
});

// --------------------------------------------------------------------------- //
// F13-07 / F13-17 : readiness is derived, never remembered
// --------------------------------------------------------------------------- //

describe("F13-07 promotion does not imply evidence-ready", () => {
  it("shows a freshly promoted instrument as not ready", async () => {
    panel({
      classification_revision: 1,
      projection_status: "PENDING",
      projection_revision: null,
      evidence_ready: false,
      viewer_state: "APPLICABLE - PROJECTION PENDING",
    });
    await waitFor(() =>
      expect(screen.getByTestId("dimension-readiness").textContent).toMatch(/not ready/i),
    );
  });
});

describe("F13-17 readiness comes from the current backend state", () => {
  it("changes when the server's answer changes mid-session", async () => {
    const fetchInstrument = vi
      .fn()
      .mockResolvedValueOnce(BASE)
      .mockResolvedValueOnce({
        ...BASE,
        classification_revision: 3,
        projection_status: "PENDING",
        projection_revision: 2,
        evidence_ready: false,
        viewer_state: "APPLICABLE - PROJECTION PENDING",
      });

    render(
      <InstrumentStatePanel
        contractDocumentId="cd-1"
        projectId="project-7"
        fetchInstrument={fetchInstrument}
      />,
    );

    await waitFor(() =>
      expect(screen.getByTestId("dimension-readiness").textContent).toMatch(/ready/i),
    );

    screen.getByTestId("refresh").click();

    await waitFor(() =>
      expect(screen.getByTestId("dimension-readiness").textContent).toMatch(/not ready/i),
    );
  });

  it("re-fetches rather than answering from what it already had", async () => {
    const { fetchInstrument } = panel();
    await waitFor(() => expect(fetchInstrument).toHaveBeenCalledTimes(1));
    screen.getByTestId("refresh").click();
    await waitFor(() => expect(fetchInstrument).toHaveBeenCalledTimes(2));
  });

  it("stores no readiness flag of its own", async () => {
    panel();
    await waitFor(() => expect(screen.getByTestId("dimension-readiness")).toBeTruthy());
    // Nothing about this instrument may survive in client storage. Asserted
    // over the whole store rather than one key, so a differently-named cache
    // cannot slip through.
    for (const store of [window.localStorage, window.sessionStorage]) {
      const dump = JSON.stringify({ ...store });
      expect(dump).not.toMatch(/evidence_ready/);
      expect(dump).not.toMatch(/contract-master/);
    }
  });
});

// --------------------------------------------------------------------------- //
// F13-04 : suggestion vs resolution
// --------------------------------------------------------------------------- //

describe("F13-04 suggested and resolved classification differ visibly", () => {
  it("labels a suggestion as unconfirmed", async () => {
    panel({}, { classificationSuggestion: { contract_document_type: "amendment", basis: "filename" } });
    await waitFor(() => expect(screen.getByTestId("classification-suggestion")).toBeTruthy());
    expect(screen.getByTestId("classification-suggestion").textContent).toMatch(/not confirmed/i);
  });

  it("names the basis so the operator can weigh it", async () => {
    panel({}, { classificationSuggestion: { contract_document_type: "amendment", basis: "filename" } });
    await waitFor(() =>
      expect(screen.getByTestId("classification-suggestion").textContent).toMatch(/filename/i),
    );
  });

  it("keeps the resolved classification visually distinct", async () => {
    panel({}, { classificationSuggestion: { contract_document_type: "amendment", basis: "filename" } });
    await waitFor(() => expect(screen.getByTestId("dimension-classification")).toBeTruthy());
    expect(screen.getByTestId("dimension-classification").textContent).toMatch(/general_conditions/);
    expect(screen.getByTestId("dimension-classification").textContent).not.toMatch(/amendment/);
  });

  it("discloses the reprojection consequence before a correction", async () => {
    panel();
    await waitFor(() => expect(screen.getByTestId("correction-consequence")).toBeTruthy());
    const text = screen.getByTestId("correction-consequence").textContent ?? "";
    expect(text).toMatch(/revision/i);
    expect(text).toMatch(/pending|unavailable/i);
  });
});

// --------------------------------------------------------------------------- //
// F13-13 : authoritative scope is not editable
// --------------------------------------------------------------------------- //

describe("F13-13 post-promotion scope has no generic edit control", () => {
  it("renders scope as a fact, not a field", async () => {
    panel();
    await waitFor(() => expect(screen.getByTestId("dimension-scope")).toBeTruthy());
    const scope = screen.getByTestId("dimension-scope");
    expect(scope.querySelector("input")).toBeNull();
    expect(scope.querySelector("select")).toBeNull();
  });

  it("offers no edit control for scope anywhere in the panel", async () => {
    panel();
    await waitFor(() => expect(screen.getByTestId("dimension-scope")).toBeTruthy());
    expect(screen.queryByRole("button", { name: /edit scope/i })).toBeNull();
    expect(screen.queryByRole("button", { name: /change project/i })).toBeNull();
  });
});

// --------------------------------------------------------------------------- //
// F13-16 : contract identity isolation
// --------------------------------------------------------------------------- //

describe("F13-16 the same contract_id in two projects stays isolated", () => {
  it("scopes its fetch by project", async () => {
    const { fetchInstrument } = panel();
    await waitFor(() => expect(fetchInstrument).toHaveBeenCalled());
    expect(fetchInstrument).toHaveBeenCalledWith("cd-1", "project-7");
  });

  it("re-fetches when the project changes rather than reusing the view", async () => {
    const fetchInstrument = vi.fn().mockResolvedValue(BASE);
    const { rerender } = render(
      <InstrumentStatePanel
        contractDocumentId="cd-1"
        projectId="project-7"
        fetchInstrument={fetchInstrument}
      />,
    );
    await waitFor(() => expect(fetchInstrument).toHaveBeenCalledTimes(1));

    rerender(
      <InstrumentStatePanel
        contractDocumentId="cd-1"
        projectId="project-9"
        fetchInstrument={fetchInstrument}
      />,
    );

    await waitFor(() => expect(fetchInstrument).toHaveBeenCalledTimes(2));
    expect(fetchInstrument).toHaveBeenLastCalledWith("cd-1", "project-9");
  });

  it("keys its cache by project as well as instrument", async () => {
    panel();
    await waitFor(() => expect(screen.getByTestId("cache-key")).toBeTruthy());
    expect(screen.getByTestId("cache-key").textContent).toBe("contract-master:project-7:cd-1");
  });
});
