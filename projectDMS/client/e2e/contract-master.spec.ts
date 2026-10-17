/**
 * Contract Master browser acceptance.
 *
 * F13-03 — a project admin cannot reach organisation-scope creation.
 * F13-09 — a browse result cannot launch Q&A.
 * F13-10 — historical mode requires a date.
 * F13-17 — evidence readiness derives from current backend state and cannot be
 *          a stale client flag.  (mutation-required)
 *
 * F13-17 is the one that cannot be proven any other way. Every static test
 * passes against a component that caches readiness once and never looks again;
 * only changing the server's answer *mid-session* and re-fetching distinguishes
 * a derived value from a remembered one. So this spec flips the backend response
 * between two reads of the same instrument and asserts the UI follows.
 *
 * Routes are mocked at the network boundary, in the shape the BRIDGE-01 router
 * actually returns — the repo's existing e2e convention.
 */

import { expect, Page, Route, test } from "@playwright/test";

const ORG = "org-A";
const PROJECT = "proj-A";
const PROTOTYPE_URL = "/contract-master/workspace";

type Capabilities = Record<string, boolean>;

const ORG_TIER: Capabilities = {
  can_browse_catalogue: true,
  can_view_instruments: true,
  can_manage_classification: true,
  can_manage_applicability: true,
  can_upload_organization_scope: true,
  can_upload_project_scope: true,
  can_review_migration: true,
  can_promote: true,
};

const PROJECT_TIER: Capabilities = {
  ...ORG_TIER,
  can_browse_catalogue: false,
  can_manage_classification: false,
  can_manage_applicability: false,
  can_upload_organization_scope: false,
  can_review_migration: false,
  can_promote: false,
};

function instrument(overrides: Record<string, unknown> = {}) {
  return {
    contract_document_id: "cd-1",
    document_id: "doc-1",
    document_version_id: "doc-1-v1",
    contract_document_type: "general_conditions",
    scope_level: "project",
    project_id: PROJECT,
    classification_revision: 1,
    projection_status: "CURRENT",
    projection_revision: 1,
    applicability_count: 1,
    content_consumable: true,
    evidence_ready: true,
    viewer_state: "APPLICABLE - EVIDENCE READY",
    ...overrides,
  };
}

async function json(route: Route, body: unknown) {
  await route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

/**
 * The workspace is behind ProtectedRoute, so the browser has to arrive with a
 * session and the Contract Master audience permission before any of the rules
 * below are reachable. Both are answered at the network boundary, in the shape
 * the real endpoints return.
 *
 * Capability flags still decide what is rendered — this only gets the actor
 * through the front door, which is exactly what a real authorised user does.
 */
async function mockSession(page: Page) {
  await page.route("**/api/me", (route) =>
    json(route, {
      id: "user-1",
      email: "contract.master@example.test",
      organization_id: ORG,
      project_id: PROJECT,
      roles: ["contractmgr_org"],
      permissions: ["dms.contract.master.view", "dms.document.view"],
    }),
  );
  await page.route("**/api/security-terms/status", (route) =>
    json(route, { requires_acceptance: false }),
  );
}

async function mockContractMaster(
  page: Page,
  options: {
    capabilities?: Capabilities;
    instrumentSequence?: Array<Record<string, unknown>>;
    evidence?: unknown;
    evidenceStatus?: number;
  } = {},
) {
  const capabilities = options.capabilities ?? ORG_TIER;
  const sequence = options.instrumentSequence ?? [instrument()];
  let readCount = 0;

  await mockSession(page);
  await page.route("**/api/contract-master/capabilities**", (route) => json(route, capabilities));
  await page.route("**/api/contract-master/catalogue**", (route) =>
    json(route, { items: [instrument()] }),
  );
  await page.route("**/api/contract-master/reconciliation/candidates**", (route) =>
    json(route, { candidates: [] }),
  );
  await page.route("**/api/contract-master/instruments/**", (route) => {
    // Each read returns the next server state. A cached client flag would not
    // notice the change; a derived one must.
    const body = sequence[Math.min(readCount, sequence.length - 1)];
    readCount += 1;
    return json(route, body);
  });
  await page.route("**/api/contract-master/evidence/search**", (route) => {
    if (options.evidenceStatus && options.evidenceStatus !== 200) {
      return route.fulfill({
        status: options.evidenceStatus,
        contentType: "application/json",
        body: JSON.stringify({ detail: "canonical eligibility could not be resolved" }),
      });
    }
    return json(
      route,
      options.evidence ?? {
        results: [],
        outcome: "valid_empty",
        degraded_sources: [],
        total_count: 0,
      },
    );
  });
}

// --------------------------------------------------------------------------- //
// F13-03 — a project admin cannot reach organisation-scope creation
// --------------------------------------------------------------------------- //

test.describe("F13-03 organisation-scope creation is out of reach for a project actor", () => {
  test("the organisation scope choice is not selectable", async ({ page }) => {
    await mockContractMaster(page, { capabilities: PROJECT_TIER });
    await page.goto(PROTOTYPE_URL);

    // The form omits the choice entirely rather than disabling it, which is the
    // stronger behaviour: there is no control to re-enable in devtools.
    await expect(page.getByRole("radio", { name: /organisation/i })).toHaveCount(0);
    await expect(page.getByRole("radio", { name: /project/i })).toHaveCount(1);
  });

  test("the catalogue tab is not offered at all", async ({ page }) => {
    await mockContractMaster(page, { capabilities: PROJECT_TIER });
    await page.goto(PROTOTYPE_URL);

    await expect(page.getByTestId("tab-catalogue")).toHaveCount(0);
    await expect(page.getByTestId("tab-migration")).toHaveCount(0);
  });

  test("an organisation actor does get both", async ({ page }) => {
    await mockContractMaster(page, { capabilities: ORG_TIER });
    await page.goto(PROTOTYPE_URL);

    await expect(page.getByTestId("tab-catalogue")).toBeVisible();
    await expect(page.getByRole("radio", { name: /organisation/i })).toBeEnabled();
  });
});

// --------------------------------------------------------------------------- //
// F13-10 — historical mode requires a date
// --------------------------------------------------------------------------- //

test.describe("F13-10 historical mode requires a date", () => {
  test("asking is blocked until a date is supplied", async ({ page }) => {
    await mockContractMaster(page);
    await page.goto(PROTOTYPE_URL);
    // Q&A launches only from an evidence-ready instrument, so open one first.
    await page.getByTestId("tab-catalogue").click();
    await page.getByRole("button", { name: "cd-1" }).click();
    await expect(page.getByTestId("state-badge")).toBeVisible();
    await page.getByTestId("tab-qa").click();

    await page.getByLabel("Question").fill("what notice is required");
    await page.getByLabel("Mode").selectOption("historical");

    await expect(page.getByRole("button", { name: "Ask" })).toBeDisabled();
    await expect(page.getByText(/does not default to today/i)).toContainText("does not default to today");

    await page.getByLabel("Event date").fill("2021-03-01");
    await expect(page.getByRole("button", { name: "Ask" })).toBeEnabled();
  });

  test("no mode is preselected", async ({ page }) => {
    await mockContractMaster(page);
    await page.goto(PROTOTYPE_URL);
    // Q&A launches only from an evidence-ready instrument, so open one first.
    await page.getByTestId("tab-catalogue").click();
    await page.getByRole("button", { name: "cd-1" }).click();
    await expect(page.getByTestId("state-badge")).toBeVisible();
    await page.getByTestId("tab-qa").click();

    await expect(page.getByLabel("Mode")).toHaveValue("");
    await expect(page.getByRole("button", { name: "Ask" })).toBeDisabled();
  });
});

// --------------------------------------------------------------------------- //
// F13-09 — a browse result cannot launch Q&A
// --------------------------------------------------------------------------- //

test.describe("F13-09 a non-evidence-ready instrument cannot launch Q&A", () => {
  test("asking is refused for a catalogued, unassigned instrument", async ({ page }) => {
    await mockContractMaster(page, {
      instrumentSequence: [
        instrument({
          applicability_count: 0,
          evidence_ready: false,
          viewer_state: "CATALOGUED / UNASSIGNED",
        }),
      ],
    });
    await page.goto(PROTOTYPE_URL);

    await page.getByTestId("tab-catalogue").click();
    await page.getByRole("button", { name: "cd-1" }).click();
    await expect(page.getByTestId("state-badge")).toBeVisible();

    await page.getByTestId("tab-qa").click();
    await expect(page.getByTestId("launch-blocked")).toBeVisible();
    await expect(page.getByRole("button", { name: "Ask" })).toBeDisabled();
  });
});

// --------------------------------------------------------------------------- //
// F13-17 — readiness is derived, never a stale client flag
// --------------------------------------------------------------------------- //

test.describe("F13-17 evidence readiness follows the server mid-session", () => {
  test("a classification correction flips readiness on the next read", async ({ page }) => {
    await mockContractMaster(page, {
      instrumentSequence: [
        // First read: ready.
        instrument({ evidence_ready: true, viewer_state: "APPLICABLE - EVIDENCE READY" }),
        // Second read: a correction landed, projection is pending again.
        instrument({
          classification_revision: 2,
          projection_status: "PENDING",
          projection_revision: 1,
          evidence_ready: false,
          viewer_state: "APPLICABLE - PROJECTION PENDING",
        }),
      ],
    });
    await page.goto(PROTOTYPE_URL);

    await page.getByTestId("tab-catalogue").click();
    await page.getByRole("button", { name: "cd-1" }).click();

    await expect(page.getByTestId("dimension-readiness")).toContainText("Ready");
    await expect(page.getByTestId("use-as-evidence")).toBeEnabled();

    // Nothing in the client changed. The server's answer did.
    await page.getByTestId("refresh").click();

    await expect(page.getByTestId("dimension-readiness")).toContainText("Not ready");
    await expect(page.getByTestId("use-as-evidence")).toBeDisabled();
    await expect(page.getByTestId("state-badge")).toContainText("APPLICABLE - PROJECTION PENDING");
  });

  test("a publication block flips readiness without touching the instrument", async ({ page }) => {
    await mockContractMaster(page, {
      instrumentSequence: [
        instrument({ evidence_ready: true }),
        instrument({
          content_consumable: false,
          evidence_ready: false,
          viewer_state: "APPLICABLE - CONTENT BLOCKED",
        }),
      ],
    });
    await page.goto(PROTOTYPE_URL);
    await page.getByTestId("tab-catalogue").click();
    await page.getByRole("button", { name: "cd-1" }).click();

    await expect(page.getByTestId("dimension-readiness")).toContainText("Ready");
    await page.getByTestId("refresh").click();

    await expect(page.getByTestId("dimension-content")).toContainText("unavailable");
    await expect(page.getByTestId("dimension-readiness")).toContainText("Not ready");
    // Applicable AND blocked, both still true.
    await expect(page.getByTestId("dimension-content")).toBeVisible();
  });
});

// --------------------------------------------------------------------------- //
// evidence outcomes reach the browser distinctly (T24 / UI-03)
// --------------------------------------------------------------------------- //

test.describe("evidence outcomes stay distinct in the browser", () => {
  test("a degraded answer shows a banner above the results", async ({ page }) => {
    await mockContractMaster(page, {
      evidence: {
        results: [{ clause_number: "10.1" }],
        outcome: "degraded",
        degraded_sources: ["vector"],
        total_count: 1,
      },
    });
    await page.goto(PROTOTYPE_URL);
    // Q&A launches only from an evidence-ready instrument, so open one first.
    await page.getByTestId("tab-catalogue").click();
    await page.getByRole("button", { name: "cd-1" }).click();
    await expect(page.getByTestId("state-badge")).toBeVisible();
    await page.getByTestId("tab-qa").click();
    await page.getByLabel("Question").fill("variation");
    await page.getByLabel("Mode").selectOption("current_state");
    await page.getByRole("button", { name: "Ask" }).click();

    await expect(page.getByTestId("degraded-banner")).toBeVisible();
    await expect(page.getByTestId("degraded-banner")).toContainText("vector");
    await expect(page.getByTestId("valid-empty")).toHaveCount(0);
  });

  test("a valid empty answer offers no widening", async ({ page }) => {
    await mockContractMaster(page);
    await page.goto(PROTOTYPE_URL);
    // Q&A launches only from an evidence-ready instrument, so open one first.
    await page.getByTestId("tab-catalogue").click();
    await page.getByRole("button", { name: "cd-1" }).click();
    await expect(page.getByTestId("state-badge")).toBeVisible();
    await page.getByTestId("tab-qa").click();
    await page.getByLabel("Question").fill("variation");
    await page.getByLabel("Mode").selectOption("current_state");
    await page.getByRole("button", { name: "Ask" }).click();

    await expect(page.getByTestId("valid-empty")).toBeVisible();
    await expect(page.getByTestId("degraded-banner")).toHaveCount(0);
    await expect(page.getByRole("button", { name: /search (everything|all|wider)/i })).toHaveCount(0);
  });

  test("an authority failure is not shown as an empty answer", async ({ page }) => {
    await mockContractMaster(page, { evidenceStatus: 409 });
    await page.goto(PROTOTYPE_URL);
    // Q&A launches only from an evidence-ready instrument, so open one first.
    await page.getByTestId("tab-catalogue").click();
    await page.getByRole("button", { name: "cd-1" }).click();
    await expect(page.getByTestId("state-badge")).toBeVisible();
    await page.getByTestId("tab-qa").click();
    await page.getByLabel("Question").fill("variation");
    await page.getByLabel("Mode").selectOption("current_state");
    await page.getByRole("button", { name: "Ask" }).click();

    await expect(page.getByTestId("authority-failure")).toBeVisible();
    await expect(page.getByTestId("valid-empty")).toHaveCount(0);
  });

  test("save, export and cite stay disabled without provenance", async ({ page }) => {
    await mockContractMaster(page, {
      evidence: {
        results: [{ clause_number: "10.1" }],
        outcome: "complete",
        degraded_sources: [],
        total_count: 1,
      },
    });
    await page.goto(PROTOTYPE_URL);
    // Q&A launches only from an evidence-ready instrument, so open one first.
    await page.getByTestId("tab-catalogue").click();
    await page.getByRole("button", { name: "cd-1" }).click();
    await expect(page.getByTestId("state-badge")).toBeVisible();
    await page.getByTestId("tab-qa").click();
    await page.getByLabel("Question").fill("variation");
    await page.getByLabel("Mode").selectOption("current_state");
    await page.getByRole("button", { name: "Ask" }).click();

    for (const action of ["Save", "Export", "Cite"]) {
      await expect(page.getByRole("button", { name: action })).toBeDisabled();
    }
  });
});

// --------------------------------------------------------------------------- //
// the legacy bypass
// --------------------------------------------------------------------------- //

test.describe("the UI never routes contract evidence through generic search", () => {
  test("no request reaches /api/contracts/search", async ({ page }) => {
    const generic: string[] = [];
    page.on("request", (request) => {
      if (request.url().includes("/api/contracts/search")) generic.push(request.url());
    });

    await mockContractMaster(page);
    await page.goto(PROTOTYPE_URL);
    // Q&A launches only from an evidence-ready instrument, so open one first.
    await page.getByTestId("tab-catalogue").click();
    await page.getByRole("button", { name: "cd-1" }).click();
    await expect(page.getByTestId("state-badge")).toBeVisible();
    await page.getByTestId("tab-qa").click();
    await page.getByLabel("Question").fill("variation");
    await page.getByLabel("Mode").selectOption("current_state");
    await page.getByRole("button", { name: "Ask" }).click();

    await expect(page.getByTestId("valid-empty")).toBeVisible();
    expect(generic).toEqual([]);
  });
});
